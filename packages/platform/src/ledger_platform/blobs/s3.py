"""The S3 object-lock store: an ``ImmutableStore`` in an S3 bucket, production's legal archive (§6.8).

What holds the contract is S3, not this code: the bucket has object lock enabled (and so versioning), and
every object is written under a lock in compliance mode, which nobody, the account's root included, can
delete, overwrite or shorten before its date. The store adds:

- **put once**: ``PutObject`` with ``If-None-Match: *``, so S3 writes only where the key holds nothing. When
  the key holds an object (412) the store reads its SHA-256 checksum (``HeadObject``): the same bytes answer
  the stored object, other bytes are ``ObjectConflictError``. A write that races another one to the key
  (409) is tried again, ``conflict_attempts`` times at most.
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

Errors: S3's answers that may pass (5xx, throttling, a timeout, a lost connection, a checksum S3 or botocore
found wrong on the way) are ``BlobStoreUnavailableError``; the others (no such bucket, a bucket without object
lock, access denied, no credentials) are ``BlobStoreRefusedError``, a fault of the configuration that no retry
mends, naming S3's error code and never a bucket, a key or a credential.

The bucket is checked once, before the first request: one without object lock enabled is refused
(``BlobStoreRefusedError``), whatever its writes would answer (AWS refuses a locked write to such a bucket;
an emulator may store it unlocked). The archive's role needs ``s3:GetBucketObjectLockConfiguration`` for it,
besides ``s3:PutObject``, ``s3:GetObject`` and ``s3:PutObjectRetention``'s implicit grant to set a lock on a
write (and ``kms:GenerateDataKey``/``kms:Decrypt`` on the key under ``aws:kms``).

Nothing here deletes: the store has no such method, and production's IAM policy should deny the archive's
role ``s3:DeleteObject`` and ``s3:PutObjectRetention`` besides (a delete without a version only hides an
object behind a delete marker, its locked version stays; the runbook's).

The client is botocore's, the one boto3 wraps (boto3's resources and transfers are of no use here; its
types, ``types-boto3-s3``, type botocore's client as ``S3Client``: ``as_s3_client``). botocore is
synchronous: every request runs in a worker thread (``asyncio.to_thread``), on one client made on first use,
its credentials found by AWS's own chain (the environment, a profile, a web identity, an instance role;
never ``LEDGER_*`` settings, never logged). A client is safe to share between threads; the session that
makes it is not, so each client gets a session of its own (``s3_client``).
"""

import asyncio
import base64
import re
import threading
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from enum import Enum, auto
from typing import ClassVar, Final, Literal, TypeIs, final, override
lazy from collections.abc import Callable, Mapping

import botocore.session
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError, PartialCredentialsError
lazy from botocore.client import BaseClient
lazy from types_boto3_s3.client import S3Client
lazy from types_boto3_s3.type_defs import HeadObjectOutputTypeDef, PutObjectRequestTypeDef

from ledger_kernel.errors import InvalidValueError, LedgerError
from ledger_platform.blobs.store import (
    BlobDigestMismatchError,
    BlobNotFoundError,
    BlobRecordCorruptError,
    BlobStoreUnavailableError,
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
_COMPONENT: Final = "archive"
_TRANSIENT: Final = frozenset(
    {
        "BadDigest",  # the bytes S3 received are not those sent: corrupted on the way
        "InternalError",
        "RequestTimeout",
        "RequestTimeTooSkewed",
        "ServiceUnavailable",
        "SlowDown",
        "Throttling",
        "ThrottlingException",
    }
)
"""S3's error codes below 500 that a later attempt may not meet."""
_SERVER_ERROR: Final = 500
_NOT_FOUND: Final = 404
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
    an alias or an ARN) or, without one, the account's key for S3.
    """

    algorithm: Literal["AES256", "aws:kms"] = "AES256"
    kms_key_id: str | None = None

    def __post_init__(self) -> None:
        if self.kms_key_id is not None and (self.algorithm != "aws:kms" or not self.kms_key_id):
            raise InvalidOptionError(
                component=_COMPONENT,
                option="kms_key_id",
                requirement="a key's id, alias or ARN, only with aws:kms encryption",
                value=self.kms_key_id,
            )


SSE_S3: Final = S3Encryption()
"""S3's own keys: what the archive is encrypted with unless the settings name KMS."""


def s3_client(
    *, region: str, endpoint_url: str | None = None, timeouts: S3Timeouts = DEFAULT_TIMEOUTS
) -> S3Client:
    """A client of S3 in ``region``, or of the S3 at ``endpoint_url`` (LocalStack: local runs and tests).

    Its credentials are AWS's chain's. Requests to an endpoint of our naming address the bucket in the path
    (LocalStack's host names are not the bucket's); AWS's own are addressed by host, as AWS requires.
    """
    config = Config(
        region_name=region,
        connect_timeout=timeouts.connect,
        read_timeout=timeouts.read,
        retries={"mode": "standard", "total_max_attempts": timeouts.attempts},
        request_checksum_calculation="when_required",  # the store sends its own SHA-256
        response_checksum_validation="when_supported",
        s3={"addressing_style": "virtual" if endpoint_url is None else "path"},
    )
    session = botocore.session.Session()  # one per client: a session is not safe to share between threads
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
    """What a failure before or after S3 answered means: no credentials to sign with, or no answer."""
    if isinstance(error, NoCredentialsError | PartialCredentialsError):
        return BlobStoreRefusedError(type(error).__name__)
    return BlobStoreUnavailableError()


def _code(error: ClientError) -> str:
    return error.response.get("Error", {}).get("Code", "")


def _status(error: ClientError) -> int:
    return error.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0)


@final
class S3ImmutableStore(ImmutableStore):
    """Objects in ``bucket`` under compliance lock, written once (see the module).

    ``client_factory`` makes the client on first use (``s3_client`` with the settings' region and endpoint;
    a test's own). A write that races another one to its key is tried ``conflict_attempts`` times at most,
    ``backoff`` seconds apart at first, twice as long each time after.
    """

    __slots__ = (
        "_backoff",
        "_bucket",
        "_client",
        "_conflict_attempts",
        "_encryption",
        "_factory",
        "_lock",
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
        self._client: S3Client | None = None
        self._lock = threading.Lock()

    @property
    def bucket(self) -> str:
        """The bucket the objects are in."""
        return self._bucket

    @override
    async def put_once(
        self, key: str, content: bytes, *, retain_until: date, media_type: str
    ) -> StoredObject:
        """Store ``content`` under ``key`` once, locked through ``retain_until`` (see the module)."""
        require_key(key)
        digest = digest_of(content)
        checksum = base64.b64encode(digest).decode("ascii")
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
        stored = StoredObject(key=key, sha256=digest, size=len(content))
        for attempt in range(self._conflict_attempts):
            if attempt > 0:
                await asyncio.sleep(self._backoff * 2.0 ** (attempt - 1))
            written = await asyncio.to_thread(self._write, request)
            if written is _Written.STORED:
                return stored
            if written is _Written.TAKEN:
                found = await asyncio.to_thread(self._head, key)
                if found is not None:  # else gone since (behind a delete marker): write again
                    if _checksum_of(found) != checksum:
                        raise ObjectConflictError
                    return stored
        raise BlobStoreUnavailableError

    @override
    async def get(self, key: str, *, sha256: bytes) -> bytes:
        require_key(key)
        require_digest("blob", sha256)
        content = await asyncio.to_thread(self._read, key)
        if digest_of(content) != sha256:
            raise BlobDigestMismatchError
        return content

    @override
    async def retained_until(self, key: str) -> date:
        require_key(key)
        found = await asyncio.to_thread(self._head, key)
        if found is None:
            raise BlobNotFoundError
        recorded = found.get("Metadata", {}).get(RETENTION_METADATA, "")
        try:
            day = date.fromisoformat(recorded)
        except ValueError as error:
            raise BlobRecordCorruptError from error
        lock = _present(found, "ObjectLockRetainUntilDate", datetime)
        if _present(found, "ObjectLockMode", str) != LOCK_MODE or lock is None or lock < locked_until(day):
            raise BlobRecordCorruptError
        return day

    def _connected(self) -> S3Client:
        """The client, made by the first caller (in a worker thread: making one reads files).

        Kept once the bucket proved it has object lock enabled; until then each caller tries again.
        """
        with self._lock:
            if self._client is None:
                client = self._factory()
                self._require_object_lock(client)
                self._client = client
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

    def _write(self, request: PutObjectRequestTypeDef) -> _Written:
        try:
            answer = self._connected().put_object(**request)
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

    def _head(self, key: str) -> HeadObjectOutputTypeDef | None:
        """The object's headers, ``None`` when the key holds nothing."""
        try:
            return self._connected().head_object(Bucket=self._bucket, Key=key, ChecksumMode="ENABLED")
        except ClientError as error:
            if _status(error) == _NOT_FOUND:  # a HEAD's answer has no body, so no code to tell more by
                return None
            raise _failure(error) from error
        except BotoCoreError as error:
            raise _unreached(error) from error

    def _read(self, key: str) -> bytes:
        try:
            answer = self._connected().get_object(Bucket=self._bucket, Key=key, ChecksumMode="ENABLED")
            body = answer["Body"]
            try:
                return body.read()
            finally:
                body.close()
        except ClientError as error:
            if _code(error) == "NoSuchKey":
                raise BlobNotFoundError from error
            raise _failure(error) from error
        except BotoCoreError as error:
            raise _unreached(error) from error


def _checksum_of(found: HeadObjectOutputTypeDef) -> str:
    """The SHA-256 S3 keeps for the object: every object this store writes has one (see the module)."""
    checksum = _present(found, "ChecksumSHA256", str)
    if checksum is None:
        raise BlobRecordCorruptError
    return checksum


def _present[T](answer: Mapping[str, object], field: str, kind: type[T]) -> T | None:
    """``answer``'s ``field`` if S3 sent it: its types say every field is always there, and some are not."""
    value = answer.get(field)
    return value if isinstance(value, kind) else None


class _Written(Enum):
    """What a conditional write came to."""

    STORED = auto()
    TAKEN = auto()
    """The key holds an object (412)."""
    CONTENDED = auto()
    """Another conditional write to the key was in flight (409)."""
