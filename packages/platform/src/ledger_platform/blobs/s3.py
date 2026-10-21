"""The S3 object-lock store: an ``ImmutableStore`` in an S3 bucket, production's legal archive (§6.8).

What holds the contract is S3, not this code: the bucket has object lock enabled (and so versioning), and
every object is written under a lock in compliance mode, which nobody, the account's root included, can
delete, overwrite or shorten before its date. The store adds:

- **put once**: a ``HeadObject`` first; where the key holds nothing, ``PutObject`` with ``If-None-Match: *``,
  so S3 writes only where the key still holds nothing. An object found (first, or after a 412 when another
  writer won the race) is the stored object only if it carries the same SHA-256 checksum under a compliance
  lock through the day its metadata records (``BlobRecordCorruptError`` for a lock that can be lifted, a
  shorter one or none; ``ObjectConflictError`` for other bytes). A write that races another one in flight to
  the key (409) is tried again, ``conflict_attempts`` times at most.
- **delete markers**: object lock lets anyone allowed ``s3:DeleteObject`` hide a locked object behind a
  delete marker (a delete without a version id). Every 404 S3 answers is read for its
  ``x-amz-delete-marker: true``: a key so hidden is ``BlobRecordCorruptError`` (tampering, an alert), never
  "not found" and never written again, so no other bytes can take the place of an archived document.
- **SHA-256 end to end**: the write carries the bytes' SHA-256 (``x-amz-checksum-sha256``), which S3 checks
  before it stores them and keeps with the object; a write it answers without that checksum was not checked
  (an S3 look-alike that ignores checksums) and is refused. A read is checked by botocore against the
  checksum S3 kept, then by the store against the digest the caller recorded.
- **the retention day**: the lock holds through the whole of ``retain_until``, until 00:00 UTC the day after
  (``locked_until``). The day itself is kept in the object's metadata (``x-amz-meta-retain-until``), which
  nothing changes once written: ``retained_until`` answers it, and refuses an object whose lock is not a
  compliance lock holding at least that long (``BlobRecordCorruptError``). S3 lets anyone allowed extend a
  lock, never shorten it; the recorded day stays the first one.
- **encryption at rest** by S3 (``SSE-S3``, ``AES256``) or by KMS (``aws:kms``, under a key of the
  operator's naming or the account's own): ``S3Encryption``.

Errors: S3's answers that may pass (5xx, throttling, ``OperationAborted``, a timeout, a lost connection, a
checksum S3 or botocore found wrong on the way) are ``BlobStoreUnavailableError``; the others (no such bucket,
a bucket without object lock, access denied, no credentials, a client misconfigured) are
``BlobStoreRefusedError``, a fault of the configuration that no retry mends, naming S3's error code and never
a bucket, a key or a credential. The logs show botocore's own errors by operation, code and status alone
(``observability.logs``): S3's messages name roles, accounts, buckets and keys.

The bucket is checked once, before the first request (``check``, which a start or a readiness probe may call
itself): one without object lock enabled is refused (``BlobStoreRefusedError``), whatever its writes would
answer (AWS refuses a locked write to such a bucket; an emulator may store it unlocked).

Permissions (AWS's "Required permissions for Amazon S3 API operations" and the ``HeadObject`` reference):
the archive's role needs ``s3:GetBucketObjectLockConfiguration`` (the check), ``s3:PutObject`` and
``s3:PutObjectRetention`` (a write that sets a lock needs both), ``s3:GetObject`` (reads and HEADs),
``s3:GetObjectRetention`` (without it a HEAD omits the lock, and ``retained_until`` reports every object
corrupt) and ``s3:ListBucket`` (without it S3 answers 403 for a missing key, which reads as a refusal, not
"not found"); under ``aws:kms`` also ``kms:GenerateDataKey`` and ``kms:Decrypt`` on the key. Nothing here
deletes: the store has no such method, and the bucket policy, not one role's IAM, must deny every principal
``s3:DeleteObject``, ``s3:DeleteObjectVersion`` and ``s3:PutLifecycleConfiguration`` (a lifecycle rule can
place delete markers), and deny ``s3:PutObject`` unless ``s3:object-lock-mode`` is ``COMPLIANCE`` and
``s3:object-lock-remaining-retention-days`` is at least 3650; an organization SCP keeps that policy from being
loosened (the plan's Owner item 4).

The client is botocore's, the one boto3 wraps (boto3's resources and transfers are of no use here; its
types, ``types-boto3-s3``, type botocore's client as ``S3Client``: ``as_s3_client``). It sends requests only
to AWS's endpoint for the region, or to the one ``s3_client`` is given: endpoints from ``AWS_ENDPOINT_URL*``
or an AWS configuration file are ignored, so the settings' refusal of an endpoint beyond a laptop holds.
botocore is synchronous: every request runs in the store's own threads (``WORKERS`` of them, named
``ledger-s3``), never the event loop's default executor, on one client made on first use, its credentials
found by AWS's own chain (the environment, a profile, a web identity, an instance role; never ``LEDGER_*``
settings, never logged). A client is safe to share between threads; the session that makes it is not, so
each client gets a session of its own. ``aclose`` stops the threads and closes the client.
"""

import asyncio
import base64
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from enum import Enum, auto
from functools import partial
from typing import ClassVar, Final, Literal, TypeIs, final, override
lazy from collections.abc import Callable, Mapping

import botocore.session
from botocore.config import Config
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    InvalidConfigError,
    NoCredentialsError,
    ParamValidationError,
    PartialCredentialsError,
    ProfileNotFound,
)
lazy from botocore.client import BaseClient
lazy from types_boto3_s3.client import S3Client
lazy from types_boto3_s3.type_defs import HeadObjectOutputTypeDef, PutObjectRequestTypeDef

from ledger_kernel.errors import InvalidValueError, LedgerError
from ledger_platform.blobs.store import (
    BlobDigestMismatchError,
    BlobNotFoundError,
    BlobRecordCorruptError,
    BlobStoreUnavailableError,
    ConnectedStore,
    ImmutableStore,
    ObjectConflictError,
    StoredObject,
    digest_of,
    require_digest,
    require_key,
)
from ledger_platform.config.options import InvalidOptionError, require_at_least, require_seconds
from ledger_platform.config.settings import is_bucket_name

LOCK_MODE: Final = "COMPLIANCE"
"""The only object-lock mode nobody can lift: governance mode yields to whoever may bypass it."""
RETENTION_METADATA: Final = "retain-until"
"""The user metadata (``x-amz-meta-retain-until``) that keeps the retention day as first stored."""
WORKERS: Final = 10
"""The store's threads by default: as many as botocore's connection pool holds."""
_COMPONENT: Final = "archive"
_TRANSIENT: Final = frozenset(
    {
        "BadDigest",  # the bytes S3 received are not those sent: corrupted on the way
        "InternalError",
        "OperationAborted",  # 409: a conflicting operation on the bucket in progress
        "RequestTimeout",
        "RequestTimeTooSkewed",
        "ServiceUnavailable",
        "SlowDown",
        "Throttling",
        "ThrottlingException",
    }
)
"""S3's error codes below 500 that a later attempt may not meet."""
_MISCONFIGURED: Final = (
    NoCredentialsError,
    PartialCredentialsError,
    ParamValidationError,
    ProfileNotFound,
    InvalidConfigError,
)
"""botocore's failures before a request leaves: the configuration's to mend, no retry's."""
_SERVER_ERROR: Final = 500
_NOT_FOUND: Final = 404
_DELETE_MARKER: Final = "x-amz-delete-marker"
_ERROR_CODE: Final = re.compile(r"[A-Za-z0-9._]{1,64}", re.ASCII)
"""What a refusal may repeat of a reason: an error code's characters, never a message's."""
_CONNECT_CEILING: Final = 60.0
_READ_CEILING: Final = 300.0
_ATTEMPTS_CEILING: Final = 10
_UNLOCKED: Final = "object_lock_disabled"
"""Why a bucket without object lock is refused (see the module)."""
_UNVERIFIED: Final = "checksum_unverified"
"""Why a write S3 answered without the checksum it was sent is refused (see the module)."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class S3Timeouts:
    """How long the client waits and how often it tries: each wait bounded, the attempts too.

    ``attempts`` counts the first: botocore's standard retry mode tries again, with jittered backoff, what
    may pass (throttling, 5xx, a timeout, a lost connection).
    """

    connect: float = 5.0
    read: float = 30.0
    attempts: int = 3

    def __post_init__(self) -> None:
        require_seconds(_COMPONENT, "connect_timeout", self.connect, ceiling=_CONNECT_CEILING)
        require_seconds(_COMPONENT, "read_timeout", self.read, ceiling=_READ_CEILING)
        require_at_least(_COMPONENT, "attempts", self.attempts)
        if self.attempts > _ATTEMPTS_CEILING:
            raise InvalidOptionError(
                component=_COMPONENT,
                option="attempts",
                requirement=f"at most {_ATTEMPTS_CEILING}",
                value=self.attempts,
            )


DEFAULT_TIMEOUTS: Final = S3Timeouts()
"""Five seconds to connect, thirty to read, three attempts."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class S3Encryption:
    """How S3 encrypts the objects at rest.

    With its own keys (``AES256``, SSE-S3), or with KMS (``aws:kms``, SSE-KMS) under ``kms_key_id`` (an id,
    an alias or an ARN) or, without one, the account's key for S3. A refusal never repeats the key's name.
    """

    algorithm: Literal["AES256", "aws:kms"] = "AES256"
    kms_key_id: str | None = None

    def __post_init__(self) -> None:
        if self.kms_key_id is not None and (self.algorithm != "aws:kms" or not self.kms_key_id):
            raise InvalidOptionError(
                component=_COMPONENT,
                option="kms_key_id",
                requirement="a key's id, alias or ARN, only with aws:kms encryption",
                value=None,
            )


SSE_S3: Final = S3Encryption()
"""S3's own keys: what the archive is encrypted with unless the settings name KMS."""


def s3_client(
    *, region: str, endpoint_url: str | None = None, timeouts: S3Timeouts = DEFAULT_TIMEOUTS
) -> S3Client:
    """A client of S3 in ``region``, or of the S3 at ``endpoint_url`` (LocalStack: local runs and tests).

    Its credentials are AWS's chain's; an endpoint configured anywhere else is ignored (see the module).
    Requests to an endpoint of our naming address the bucket in the path (LocalStack's host names are not
    the bucket's); AWS's own are addressed by host, as AWS requires.
    """
    config = Config(
        region_name=region,
        connect_timeout=timeouts.connect,
        read_timeout=timeouts.read,
        max_pool_connections=WORKERS,
        retries={"mode": "standard", "total_max_attempts": timeouts.attempts},
        request_checksum_calculation="when_required",  # the store sends its own SHA-256
        response_checksum_validation="when_supported",
        s3={"addressing_style": "virtual" if endpoint_url is None else "path"},
    )
    session = botocore.session.Session()  # one per client: a session is not safe to share between threads
    # Config's own switch is missing from botocore's types; the session's is the same one, read when the
    # client's configuration leaves it unset.
    session.set_config_variable("ignore_configured_endpoint_urls", value=True)
    return as_s3_client(session.create_client("s3", endpoint_url=endpoint_url, config=config))


def is_s3_client(client: BaseClient) -> TypeIs[S3Client]:
    """Whether ``client`` is a client of S3: botocore makes every client a ``BaseClient`` to its types."""
    return client.meta.service_model.service_name == "s3"


def as_s3_client(client: BaseClient) -> S3Client:
    """``client``, which must be a client of S3, as one."""
    if not is_s3_client(client):
        raise InvalidValueError(field="s3.client", requirement="a client of S3")
    return client


def locked_until(day: date) -> datetime:
    """The moment the lock of an object kept until ``day`` ends: the start of the next day, UTC."""
    return datetime.combine(day + timedelta(days=1), time(), UTC)


class BlobStoreRefusedError(LedgerError):
    """S3 refused a request no retry mends: the bucket or the credentials are not what the archive needs.

    A fault of the configuration (no such bucket, a bucket without object lock, access denied, no
    credentials): an internal error, which names S3's error code alone.
    """

    code: ClassVar[str] = "blob_store_refused"

    def __init__(self, reason: str) -> None:
        shown = reason if _ERROR_CODE.fullmatch(reason) else "unknown"
        super().__init__(f"the object store refused the request ({shown})")


def _failure(error: ClientError) -> LedgerError:
    """What an error S3 answered means to the store's caller (see the module)."""
    code = _code(error)
    if _status(error) >= _SERVER_ERROR or code in _TRANSIENT:
        return BlobStoreUnavailableError()
    return BlobStoreRefusedError(code)


def _unreached(error: BotoCoreError) -> LedgerError:
    """What a failure before or after S3 answered means: a client that cannot send, or no answer."""
    if isinstance(error, _MISCONFIGURED):
        return BlobStoreRefusedError(type(error).__name__)
    return BlobStoreUnavailableError()


def _code(error: ClientError) -> str:
    return error.response.get("Error", {}).get("Code", "")


def _status(error: ClientError) -> int:
    return error.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0)


def _absent(error: ClientError) -> bool:
    """Whether S3 said the key holds nothing (404); a key hidden behind a delete marker is corrupt."""
    if _status(error) != _NOT_FOUND:
        return False
    headers: Mapping[str, object] = error.response.get("ResponseMetadata", {}).get("HTTPHeaders", {})
    if headers.get(_DELETE_MARKER) == "true":
        raise BlobRecordCorruptError from error
    return True


def _present[T](answer: Mapping[str, object], field: str, kind: type[T]) -> T | None:
    """``answer``'s ``field`` if S3 sent it: its types say every field is always there, and some are not."""
    value = answer.get(field)
    return value if isinstance(value, kind) else None


def _recorded_retention(found: HeadObjectOutputTypeDef) -> date:
    """The day ``found`` is kept until as first stored, which its lock must hold through.

    ``BlobRecordCorruptError`` unless the metadata records a day and the object is locked in compliance mode
    until its end at least: an object another writer left with a lock that can be lifted, or none, is not
    one this store keeps.
    """
    recorded = found.get("Metadata", {}).get(RETENTION_METADATA, "")
    try:
        day = date.fromisoformat(recorded)
    except ValueError as error:
        raise BlobRecordCorruptError from error
    lock = _present(found, "ObjectLockRetainUntilDate", datetime)
    if _present(found, "ObjectLockMode", str) != LOCK_MODE or lock is None or lock < locked_until(day):
        raise BlobRecordCorruptError
    return day


def _require_stored(found: HeadObjectOutputTypeDef, checksum: str) -> None:
    """Refuse ``found`` as the object of the bytes whose SHA-256 is ``checksum`` (see the module)."""
    stored = _present(found, "ChecksumSHA256", str)
    if stored is None:  # every object this store writes carries its SHA-256
        raise BlobRecordCorruptError
    _recorded_retention(found)
    if stored != checksum:
        raise ObjectConflictError


@final
class S3ImmutableStore(ImmutableStore, ConnectedStore):
    """Objects in ``bucket`` under compliance lock, written once (see the module).

    ``client_factory`` makes the client on first use (``s3_client`` with the settings' region and endpoint;
    a test's own). A write that races another one to its key is tried ``conflict_attempts`` times at most,
    ``backoff`` seconds apart at first, twice as long each time after. Requests run on ``WORKERS`` threads of
    the store's own.
    """

    __slots__ = (
        "_backoff",
        "_bucket",
        "_client",
        "_closed",
        "_conflict_attempts",
        "_connecting",
        "_encryption",
        "_executor",
        "_factory",
    )

    def __init__(
        self,
        bucket: str,
        client_factory: Callable[[], S3Client],
        *,
        encryption: S3Encryption = SSE_S3,
        conflict_attempts: int = 5,
        backoff: float = 0.05,
    ) -> None:
        if not is_bucket_name(bucket):
            raise InvalidOptionError(
                component=_COMPONENT, option="bucket", requirement="an S3 bucket name", value=bucket
            )
        require_at_least(_COMPONENT, "conflict_attempts", conflict_attempts)
        if backoff < 0:
            raise InvalidOptionError(
                component=_COMPONENT, option="backoff", requirement="0 or more seconds", value=backoff
            )
        self._bucket = bucket
        self._factory = client_factory
        self._encryption = encryption
        self._conflict_attempts = conflict_attempts
        self._backoff = backoff
        self._executor = ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="ledger-s3")
        self._client: S3Client | None = None
        self._connecting = asyncio.Lock()
        self._closed = False

    @property
    def bucket(self) -> str:
        """The bucket the objects are in."""
        return self._bucket

    @override
    async def check(self) -> None:
        """Make the client and prove the bucket has object lock enabled, once: what a start may await.

        It raises what the first request would (``BlobStoreRefusedError``, ``BlobStoreUnavailableError``);
        once it succeeded, it asks nothing more.
        """
        await self._connected()

    @override
    async def aclose(self) -> None:
        """Stop the store's threads (once the requests in flight end) and close its client; idempotent.

        A request after it is ``BlobStoreUnavailableError``.
        """
        if self._closed:
            return
        self._closed = True
        await asyncio.to_thread(self._executor.shutdown, wait=True)
        if self._client is not None:
            self._client.close()

    @override
    async def put_once(
        self, key: str, content: bytes, *, retain_until: date, media_type: str
    ) -> StoredObject:
        """Store ``content`` under ``key`` once, locked through ``retain_until`` (see the module)."""
        require_key(key)
        digest = digest_of(content)
        checksum = base64.b64encode(digest).decode("ascii")
        stored = StoredObject(key=key, sha256=digest, size=len(content))
        client = await self._connected()
        found = await self._run(partial(self._head, client, key, checksum=True))
        if found is not None:
            _require_stored(found, checksum)
            return stored
        request: PutObjectRequestTypeDef = {
            "Bucket": self._bucket,
            "Key": key,
            "Body": content,
            "IfNoneMatch": "*",
            "ChecksumAlgorithm": "SHA256",
            "ChecksumSHA256": checksum,
            "ContentType": media_type,
            "Metadata": {RETENTION_METADATA: retain_until.isoformat()},
            "ObjectLockMode": LOCK_MODE,
            "ObjectLockRetainUntilDate": locked_until(retain_until),
            "ServerSideEncryption": self._encryption.algorithm,
        }
        if self._encryption.kms_key_id is not None:
            request["SSEKMSKeyId"] = self._encryption.kms_key_id
        for attempt in range(self._conflict_attempts):
            if attempt > 0:
                await asyncio.sleep(self._backoff * 2.0 ** (attempt - 1))
            written = await self._run(partial(self._write, client, request))
            if written is _Written.STORED:
                return stored
            if written is _Written.TAKEN:
                found = await self._run(partial(self._head, client, key, checksum=True))
                if found is not None:  # else gone since (no delete marker: _head says so): write again
                    _require_stored(found, checksum)
                    return stored
        raise BlobStoreUnavailableError

    @override
    async def get(self, key: str, *, sha256: bytes) -> bytes:
        require_key(key)
        require_digest("blob", sha256)
        client = await self._connected()
        content = await self._run(partial(self._read, client, key))
        if digest_of(content) != sha256:
            raise BlobDigestMismatchError
        return content

    @override
    async def retained_until(self, key: str) -> date:
        require_key(key)
        client = await self._connected()
        found = await self._run(partial(self._head, client, key, checksum=False))
        if found is None:
            raise BlobNotFoundError
        return _recorded_retention(found)

    async def _run[T](self, call: Callable[[], T]) -> T:
        """``call`` on one of the store's threads; ``BlobStoreUnavailableError`` once the store is closed."""
        if self._closed:
            raise BlobStoreUnavailableError
        return await asyncio.get_running_loop().run_in_executor(self._executor, call)

    async def _connected(self) -> S3Client:
        """The client, made and its bucket checked by the first caller while the others wait.

        Kept once the bucket proved it has object lock enabled; until then each caller tries again. The wait
        is bounded by the client's timeouts and attempts; no thread is held while it lasts.
        """
        client = self._client
        if client is not None:
            return client
        async with self._connecting:
            if self._client is None:
                made = await self._run(self._factory)
                await self._run(partial(self._require_object_lock, made))
                self._client = made
            return self._client

    def _require_object_lock(self, client: S3Client) -> None:
        """Refuse a bucket without object lock enabled: it cannot hold the legal archive."""
        try:
            answer = client.get_object_lock_configuration(Bucket=self._bucket)
        except ClientError as error:
            raise _failure(error) from error
        except BotoCoreError as error:
            raise _unreached(error) from error
        if answer.get("ObjectLockConfiguration", {}).get("ObjectLockEnabled") != "Enabled":
            raise BlobStoreRefusedError(_UNLOCKED)

    def _write(self, client: S3Client, request: PutObjectRequestTypeDef) -> _Written:
        try:
            answer = client.put_object(**request)
        except ClientError as error:
            match _code(error):
                case "PreconditionFailed":
                    return _Written.TAKEN
                case "ConditionalRequestConflict":
                    return _Written.CONTENDED
                case _:
                    raise _failure(error) from error
        except BotoCoreError as error:
            raise _unreached(error) from error
        if answer.get("ChecksumSHA256") != request.get("ChecksumSHA256"):
            raise BlobStoreRefusedError(_UNVERIFIED)
        return _Written.STORED

    def _head(self, client: S3Client, key: str, *, checksum: bool) -> HeadObjectOutputTypeDef | None:
        """The object's headers (its SHA-256 among them if ``checksum``), ``None`` when the key holds nothing.

        Only ``put_once`` asks for the checksum: under ``aws:kms`` reading it needs ``kms:Decrypt``.
        """
        try:
            if checksum:
                return client.head_object(Bucket=self._bucket, Key=key, ChecksumMode="ENABLED")
            return client.head_object(Bucket=self._bucket, Key=key)
        except ClientError as error:
            if _absent(error):  # a HEAD's answer has no body: a missing bucket reads as a missing key
                return None
            raise _failure(error) from error
        except BotoCoreError as error:
            raise _unreached(error) from error

    def _read(self, client: S3Client, key: str) -> bytes:
        try:
            answer = client.get_object(Bucket=self._bucket, Key=key, ChecksumMode="ENABLED")
            body = answer["Body"]
            try:
                return body.read()
            finally:
                body.close()
        except ClientError as error:
            if _code(error) == "NoSuchKey" and _absent(error):
                raise BlobNotFoundError from error
            raise _failure(error) from error
        except BotoCoreError as error:
            raise _unreached(error) from error


class _Written(Enum):
    """What a conditional write came to."""

    STORED = auto()
    TAKEN = auto()
    """The key holds an object (412)."""
    CONTENDED = auto()
    """Another conditional write to the key was in flight (409)."""
