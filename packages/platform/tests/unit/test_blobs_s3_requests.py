"""``S3ImmutableStore`` against botocore's stubbed client: what it asks S3 and how it reads the answers.

The store contract, object lock and a bucket's refusals are proved against LocalStack
(``tests/integration/test_blobs_s3.py``); here every request the store sends is checked parameter by
parameter, and every answer S3 may give, including those LocalStack cannot be made to give (a conditional
write contended, a 503, a connection lost), is given.
"""

import base64
import hashlib
import io
import socket
import threading
from datetime import UTC, date, datetime
from typing import Final
lazy from collections.abc import Callable, Iterator

import botocore.session
import pytest
from botocore.config import Config
from botocore.exceptions import EndpointConnectionError, NoCredentialsError
from botocore.response import StreamingBody
from botocore.stub import Stubber
lazy from types_boto3_s3.client import S3Client

from ledger_kernel.errors import ErrorKind, InvalidValueError
from ledger_platform.blobs.s3 import (
    LOCK_MODE,
    RETENTION_METADATA,
    BlobStoreRefusedError,
    S3Encryption,
    S3ImmutableStore,
    S3Timeouts,
    as_s3_client,
    locked_until,
    s3_client,
)
from ledger_platform.blobs.store import (
    BlobDigestMismatchError,
    BlobNotFoundError,
    BlobRecordCorruptError,
    BlobStoreUnavailableError,
    ObjectConflictError,
    StoredObject,
)
from ledger_platform.config.options import InvalidOptionError

BUCKET: Final = "ledger-archive-test"
KEY: Final = "do-ecf-v1/test/org/company/ecf/0a1b.xml"
CONTENT: Final = b"<ECF>\r\n<eNCF>E310000000001</eNCF>\n</ECF>"
DIGEST: Final = hashlib.sha256(CONTENT).digest()
CHECKSUM: Final = base64.b64encode(DIGEST).decode("ascii")
RETAIN: Final = date(2036, 10, 19)
LOCKED: Final = datetime(2036, 10, 20, tzinfo=UTC)
MEDIA: Final = "application/xml"
STORED: Final = StoredObject(key=KEY, sha256=DIGEST, size=len(CONTENT))
PUT: Final = {
    "Bucket": BUCKET,
    "Key": KEY,
    "Body": CONTENT,
    "IfNoneMatch": "*",
    "ChecksumAlgorithm": "SHA256",
    "ChecksumSHA256": CHECKSUM,
    "ContentType": MEDIA,
    "Metadata": {RETENTION_METADATA: "2036-10-19"},
    "ObjectLockMode": "COMPLIANCE",
    "ObjectLockRetainUntilDate": LOCKED,
    "ServerSideEncryption": "AES256",
}
HEAD: Final = {"Bucket": BUCKET, "Key": KEY, "ChecksumMode": "ENABLED"}
LOCK_ENABLED: Final = {"ObjectLockConfiguration": {"ObjectLockEnabled": "Enabled"}}


def checked(stubber: Stubber) -> None:
    """S3 says the bucket has object lock enabled, as the store asks before its first request."""
    stubber.add_response("get_object_lock_configuration", LOCK_ENABLED, {"Bucket": BUCKET})


def new_client() -> S3Client:
    """A client of its own that never reaches AWS: the tests stub or intercept every request."""
    client = botocore.session.Session().create_client(
        "s3",
        region_name="us-east-1",
        aws_access_key_id="test",
        aws_secret_access_key="test",
        config=Config(retries={"total_max_attempts": 1}),
    )
    return as_s3_client(client)


@pytest.fixture
def client() -> S3Client:
    return new_client()


@pytest.fixture
def stubber(client: S3Client) -> Iterator[Stubber]:
    """The client stubbed, the bucket's object lock already answered: a store's first request."""
    with Stubber(client) as stubbed:
        checked(stubbed)
        yield stubbed
        stubbed.assert_no_pending_responses()


@pytest.fixture
def store(client: S3Client) -> S3ImmutableStore:
    return S3ImmutableStore(BUCKET, lambda: client, backoff=0.0)


def body(content: bytes, *, length: int | None = None) -> StreamingBody:
    return StreamingBody(io.BytesIO(content), len(content) if length is None else length)


def head(
    *, checksum: str | None = CHECKSUM, retain: str | None = "2036-10-19", mode: str = LOCK_MODE
) -> dict[str, object]:
    answer: dict[str, object] = {
        "ContentLength": len(CONTENT),
        "Metadata": {} if retain is None else {RETENTION_METADATA: retain},
        "ObjectLockMode": mode,
        "ObjectLockRetainUntilDate": LOCKED,
    }
    if checksum is not None:
        answer["ChecksumSHA256"] = checksum
    return answer


# ── put_once ──────────────────────────────────────────────────────────────────────────────────────────────


async def test_an_object_is_written_once_under_compliance_lock_with_its_sha256(
    store: S3ImmutableStore, stubber: Stubber
) -> None:
    """A conditional write, its SHA-256 for S3 to check, a compliance lock through the retention day."""
    stubber.add_response("put_object", {"ChecksumSHA256": CHECKSUM}, PUT)
    assert await store.put_once(KEY, CONTENT, retain_until=RETAIN, media_type=MEDIA) == STORED


async def test_kms_encryption_names_its_key(client: S3Client, stubber: Stubber) -> None:
    kms = S3Encryption(algorithm="aws:kms", kms_key_id="alias/ledger-archive")
    store = S3ImmutableStore(BUCKET, lambda: client, encryption=kms)
    stubber.add_response(
        "put_object",
        {"ChecksumSHA256": CHECKSUM},
        {**PUT, "ServerSideEncryption": "aws:kms", "SSEKMSKeyId": "alias/ledger-archive"},
    )
    assert await store.put_once(KEY, CONTENT, retain_until=RETAIN, media_type=MEDIA) == STORED
    checked(stubber)
    stubber.add_response(
        "put_object", {"ChecksumSHA256": CHECKSUM}, {**PUT, "ServerSideEncryption": "aws:kms"}
    )
    default_key = S3ImmutableStore(BUCKET, lambda: client, encryption=S3Encryption(algorithm="aws:kms"))
    assert await default_key.put_once(KEY, CONTENT, retain_until=RETAIN, media_type=MEDIA) == STORED


async def test_the_same_bytes_again_answer_the_stored_object(
    store: S3ImmutableStore, stubber: Stubber
) -> None:
    """S3 refuses the write (the key holds an object); its checksum says it holds these bytes."""
    stubber.add_client_error("put_object", "PreconditionFailed", http_status_code=412, expected_params=PUT)
    stubber.add_response("head_object", head(), HEAD)
    assert await store.put_once(KEY, CONTENT, retain_until=RETAIN, media_type=MEDIA) == STORED


async def test_other_bytes_under_the_key_are_a_conflict(store: S3ImmutableStore, stubber: Stubber) -> None:
    stubber.add_client_error("put_object", "PreconditionFailed", http_status_code=412)
    other = base64.b64encode(hashlib.sha256(b"<ECF/>").digest()).decode("ascii")
    stubber.add_response("head_object", head(checksum=other), HEAD)
    with pytest.raises(ObjectConflictError):
        await store.put_once(KEY, CONTENT, retain_until=RETAIN, media_type=MEDIA)


async def test_an_object_without_its_checksum_is_a_corrupt_record(
    store: S3ImmutableStore, stubber: Stubber
) -> None:
    """Every object this store writes carries its SHA-256: one without was written otherwise."""
    stubber.add_client_error("put_object", "PreconditionFailed", http_status_code=412)
    stubber.add_response("head_object", head(checksum=None), HEAD)
    with pytest.raises(BlobRecordCorruptError):
        await store.put_once(KEY, CONTENT, retain_until=RETAIN, media_type=MEDIA)


async def test_a_write_refused_for_an_object_gone_since_is_tried_again(
    store: S3ImmutableStore, stubber: Stubber
) -> None:
    stubber.add_client_error("put_object", "PreconditionFailed", http_status_code=412)
    stubber.add_client_error("head_object", "404", http_status_code=404)
    stubber.add_response("put_object", {"ChecksumSHA256": CHECKSUM}, PUT)
    assert await store.put_once(KEY, CONTENT, retain_until=RETAIN, media_type=MEDIA) == STORED


async def test_a_contended_write_is_tried_again_a_bounded_number_of_times(
    client: S3Client, stubber: Stubber
) -> None:
    """S3 answers 409 to a conditional write while another one to the key is in flight."""
    store = S3ImmutableStore(BUCKET, lambda: client, conflict_attempts=2, backoff=0.001)
    for _ in range(2):
        stubber.add_client_error("put_object", "ConditionalRequestConflict", http_status_code=409)
    with pytest.raises(BlobStoreUnavailableError):
        await store.put_once(KEY, CONTENT, retain_until=RETAIN, media_type=MEDIA)
    stubber.add_client_error("put_object", "ConditionalRequestConflict", http_status_code=409)
    stubber.add_response("put_object", {"ChecksumSHA256": CHECKSUM}, PUT)
    assert await store.put_once(KEY, CONTENT, retain_until=RETAIN, media_type=MEDIA) == STORED


@pytest.mark.parametrize("answered", [{}, {"ChecksumSHA256": base64.b64encode(bytes(32)).decode("ascii")}])
async def test_a_write_s3_did_not_check_against_the_sha256_is_refused(
    store: S3ImmutableStore, stubber: Stubber, answered: dict[str, str]
) -> None:
    """A store that ignores checksums (an S3 look-alike) cannot hold the legal archive."""
    stubber.add_response("put_object", answered, PUT)
    with pytest.raises(BlobStoreRefusedError, match=r"\(checksum_unverified\)"):
        await store.put_once(KEY, CONTENT, retain_until=RETAIN, media_type=MEDIA)


# ── get ───────────────────────────────────────────────────────────────────────────────────────────────────


async def test_bytes_are_read_back_against_their_digest(store: S3ImmutableStore, stubber: Stubber) -> None:
    stubber.add_response("get_object", {"Body": body(CONTENT)}, HEAD)
    assert await store.get(KEY, sha256=DIGEST) == CONTENT
    stubber.add_response("get_object", {"Body": body(b"<ECF/>")}, HEAD)
    with pytest.raises(BlobDigestMismatchError):
        await store.get(KEY, sha256=DIGEST)


async def test_a_key_that_holds_nothing_is_not_found(store: S3ImmutableStore, stubber: Stubber) -> None:
    stubber.add_client_error("get_object", "NoSuchKey", http_status_code=404)
    with pytest.raises(BlobNotFoundError):
        await store.get(KEY, sha256=DIGEST)
    stubber.add_client_error("head_object", "404", http_status_code=404)
    with pytest.raises(BlobNotFoundError):
        await store.retained_until(KEY)


async def test_a_body_cut_short_is_unavailable(store: S3ImmutableStore, stubber: Stubber) -> None:
    stubber.add_response("get_object", {"Body": body(CONTENT, length=len(CONTENT) + 1)}, HEAD)
    with pytest.raises(BlobStoreUnavailableError):
        await store.get(KEY, sha256=DIGEST)


async def test_keys_and_digests_are_checked_before_s3_is_asked(
    store: S3ImmutableStore, stubber: Stubber
) -> None:
    """Only the bucket's check is stubbed, and a HEAD after the refusals: any request before would fail."""
    with pytest.raises(InvalidValueError, match=r"blob\.key must be"):
        await store.put_once("../x", CONTENT, retain_until=RETAIN, media_type=MEDIA)
    with pytest.raises(InvalidValueError, match=r"blob\.key must be"):
        await store.get("../x", sha256=DIGEST)
    with pytest.raises(InvalidValueError, match=r"blob\.sha256 must be"):
        await store.get(KEY, sha256=DIGEST[:31])
    with pytest.raises(InvalidValueError, match=r"blob\.key must be"):
        await store.retained_until("../x")
    stubber.add_response("head_object", head(), HEAD)
    assert await store.retained_until(KEY) == RETAIN


# ── retained_until ────────────────────────────────────────────────────────────────────────────────────────


async def test_the_retention_is_the_one_first_stored(store: S3ImmutableStore, stubber: Stubber) -> None:
    """The metadata keeps the day as first stored; a lock extended since (S3 allows it) changes nothing."""
    stubber.add_response("head_object", head(), HEAD)
    assert await store.retained_until(KEY) == RETAIN
    stubber.add_response(
        "head_object", {**head(), "ObjectLockRetainUntilDate": datetime(2050, 1, 1, tzinfo=UTC)}
    )
    assert await store.retained_until(KEY) == RETAIN


@pytest.mark.parametrize(
    "answer",
    [
        head(retain=None),
        head(retain="the nineteenth"),
        head(mode="GOVERNANCE"),
        {**head(), "ObjectLockRetainUntilDate": datetime(2036, 10, 19, 23, 59, tzinfo=UTC)},
        {key: value for key, value in head().items() if key != "ObjectLockMode"},
        {key: value for key, value in head().items() if key != "ObjectLockRetainUntilDate"},
    ],
    ids=["no-record", "unreadable", "governance", "lock-too-short", "no-lock", "no-lock-date"],
)
async def test_an_object_not_locked_as_recorded_is_a_corrupt_record(
    store: S3ImmutableStore, stubber: Stubber, answer: dict[str, object]
) -> None:
    stubber.add_response("head_object", answer, HEAD)
    with pytest.raises(BlobRecordCorruptError):
        await store.retained_until(KEY)


# ── what S3 answers that is no answer ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("code", "status"),
    [
        ("InternalError", 500),
        ("ServiceUnavailable", 503),
        ("SlowDown", 503),
        ("RequestTimeout", 400),
        ("BadDigest", 400),
        ("Throttling", 400),
    ],
)
async def test_what_may_pass_is_unavailable(
    store: S3ImmutableStore, stubber: Stubber, code: str, status: int
) -> None:
    for method in ("put_object", "get_object", "head_object"):
        stubber.add_client_error(method, code, http_status_code=status)
    with pytest.raises(BlobStoreUnavailableError):
        await store.put_once(KEY, CONTENT, retain_until=RETAIN, media_type=MEDIA)
    with pytest.raises(BlobStoreUnavailableError):
        await store.get(KEY, sha256=DIGEST)
    with pytest.raises(BlobStoreUnavailableError):
        await store.retained_until(KEY)


@pytest.mark.parametrize(
    ("code", "status"), [("AccessDenied", 403), ("NoSuchBucket", 404), ("InvalidRequest", 400), ("", 400)]
)
async def test_what_will_not_pass_is_refused_naming_s3_s_code_alone(
    store: S3ImmutableStore, stubber: Stubber, code: str, status: int
) -> None:
    """A bucket missing, without object lock, or closed to us: the configuration's to fix, not to retry.

    A HEAD answered 404 has no body to tell a missing bucket from a missing key by: not found, then.
    """
    heads = status != 404
    for method in ("put_object", "get_object", *(("head_object",) if heads else ())):
        stubber.add_client_error(method, code, service_message=f"{BUCKET} {KEY}", http_status_code=status)
    calls = [
        store.put_once(KEY, CONTENT, retain_until=RETAIN, media_type=MEDIA),
        store.get(KEY, sha256=DIGEST),
        *([store.retained_until(KEY)] if heads else []),
    ]
    for call in calls:
        with pytest.raises(BlobStoreRefusedError) as caught:
            await call
        message = str(caught.value)
        assert message == f"the object store refused the request ({code or 'unknown'})"
        assert BUCKET not in message
        assert KEY not in message
        assert caught.value.kind is ErrorKind.INTERNAL


def test_a_refusal_never_repeats_what_is_not_an_error_code() -> None:
    assert str(BlobStoreRefusedError("x-amz secret=1")) == "the object store refused the request (unknown)"
    assert BlobStoreRefusedError.code == "blob_store_refused"


def cut(**_: object) -> None:
    raise EndpointConnectionError(endpoint_url="https://s3.amazonaws.com")


async def test_a_lost_connection_is_unavailable_and_missing_credentials_refused() -> None:
    lost = new_client()
    lost.meta.events.register("before-send", cut)
    with pytest.raises(BlobStoreUnavailableError):  # the bucket's check
        await S3ImmutableStore(BUCKET, lambda: lost).retained_until(KEY)

    connected = new_client()
    store = S3ImmutableStore(BUCKET, lambda: connected)
    with Stubber(connected) as stubber:  # the bucket checked, then the connection lost
        checked(stubber)
        stubber.add_response("head_object", head(), HEAD)
        assert await store.retained_until(KEY) == RETAIN
    connected.meta.events.register("before-send", cut)
    with pytest.raises(BlobStoreUnavailableError):
        await store.put_once(KEY, CONTENT, retain_until=RETAIN, media_type=MEDIA)
    with pytest.raises(BlobStoreUnavailableError):
        await store.get(KEY, sha256=DIGEST)
    with pytest.raises(BlobStoreUnavailableError):
        await store.retained_until(KEY)

    anonymous = new_client()

    def unsigned(**_: object) -> None:
        raise NoCredentialsError

    anonymous.meta.events.register("before-sign", unsigned)
    with pytest.raises(BlobStoreRefusedError, match=r"\(NoCredentialsError\)"):
        await S3ImmutableStore(BUCKET, lambda: anonymous).get(KEY, sha256=DIGEST)


# ── the bucket ───────────────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "answer",
    [{}, {"ObjectLockConfiguration": {}}, {"ObjectLockConfiguration": {"ObjectLockEnabled": "Disabled"}}],
)
async def test_a_bucket_without_object_lock_is_refused_before_any_object_is_written(
    client: S3Client, answer: dict[str, object]
) -> None:
    with Stubber(client) as stubber:
        stubber.add_response("get_object_lock_configuration", answer, {"Bucket": BUCKET})
        store = S3ImmutableStore(BUCKET, lambda: client)
        with pytest.raises(BlobStoreRefusedError, match=r"\(object_lock_disabled\)"):
            await store.put_once(KEY, CONTENT, retain_until=RETAIN, media_type=MEDIA)
        stubber.add_client_error(
            "get_object_lock_configuration", "ObjectLockConfigurationNotFoundError", http_status_code=404
        )
        with pytest.raises(BlobStoreRefusedError, match=r"\(ObjectLockConfigurationNotFoundError\)"):
            await store.get(KEY, sha256=DIGEST)
        stubber.assert_no_pending_responses()


async def test_the_bucket_is_asked_again_until_it_answers(client: S3Client) -> None:
    with Stubber(client) as stubber:
        stubber.add_client_error("get_object_lock_configuration", "ServiceUnavailable", http_status_code=503)
        checked(stubber)
        stubber.add_response("head_object", head(), HEAD)
        stubber.add_response("head_object", head(), HEAD)
        store = S3ImmutableStore(BUCKET, lambda: client)
        with pytest.raises(BlobStoreUnavailableError):
            await store.retained_until(KEY)
        assert await store.retained_until(KEY) == RETAIN
        assert await store.retained_until(KEY) == RETAIN  # checked once, then kept
        stubber.assert_no_pending_responses()


# ── the store itself ──────────────────────────────────────────────────────────────────────────────────────


async def test_the_client_is_made_once_on_first_use(client: S3Client, stubber: Stubber) -> None:
    made: list[S3Client] = []

    def factory() -> S3Client:
        made.append(client)
        return client

    store = S3ImmutableStore(BUCKET, factory)
    assert made == []
    assert store.bucket == BUCKET
    stubber.add_response("head_object", head())
    stubber.add_response("head_object", head())
    assert await store.retained_until(KEY) == RETAIN
    assert await store.retained_until(KEY) == RETAIN
    assert made == [client]


type Make = Callable[[Callable[[], S3Client]], object]
REFUSED: Final[tuple[tuple[Make, str], ...]] = (
    (lambda factory: S3ImmutableStore("Ledger_Archive", factory), "bucket"),
    (lambda factory: S3ImmutableStore("ab", factory), "bucket"),
    (lambda factory: S3ImmutableStore("a" * 64, factory), "bucket"),
    (lambda factory: S3ImmutableStore("192.168.1.1", factory), "bucket"),
    (lambda factory: S3ImmutableStore("ledger-.archive", factory), "bucket"),
    (lambda _: S3Encryption(kms_key_id="alias/ledger-archive"), "kms_key_id"),
    (lambda _: S3Encryption(algorithm="aws:kms", kms_key_id=""), "kms_key_id"),
    (lambda factory: S3ImmutableStore(BUCKET, factory, conflict_attempts=0), "conflict_attempts"),
    (lambda factory: S3ImmutableStore(BUCKET, factory, backoff=-1.0), "backoff"),
)


@pytest.mark.parametrize(("make", "option"), REFUSED)
def test_a_store_that_cannot_work_is_refused(client: S3Client, make: Make, option: str) -> None:
    with pytest.raises(InvalidOptionError, match=f"archive option {option} must be") as caught:
        make(lambda: client)
    assert caught.value.component == "archive"


def test_the_lock_holds_through_the_whole_retention_day() -> None:
    assert locked_until(date(2036, 10, 19)) == datetime(2036, 10, 20, tzinfo=UTC)
    assert locked_until(date(2036, 12, 31)) == datetime(2037, 1, 1, tzinfo=UTC)


# ── the client the settings make ──────────────────────────────────────────────────────────────────────────


def test_the_client_is_aws_s_unless_an_endpoint_is_named() -> None:
    aws = s3_client(region="us-east-2")
    assert (aws.meta.region_name, aws.meta.endpoint_url) == (
        "us-east-2",
        "https://s3.us-east-2.amazonaws.com",
    )
    local = s3_client(region="us-east-1", endpoint_url="http://127.0.0.1:4566")
    assert local.meta.endpoint_url == "http://127.0.0.1:4566"


def test_a_client_of_another_service_is_no_s3_client() -> None:
    other = botocore.session.Session().create_client("sts", region_name="us-east-1")
    with pytest.raises(InvalidValueError, match=r"s3\.client must be a client of S3"):
        as_s3_client(other)


@pytest.mark.parametrize(
    ("make", "option"),
    [
        (lambda: S3Timeouts(connect=0.0), "connect_timeout"),
        (lambda: S3Timeouts(connect=61.0), "connect_timeout"),
        (lambda: S3Timeouts(read=float("inf")), "read_timeout"),
        (lambda: S3Timeouts(read=301.0), "read_timeout"),
        (lambda: S3Timeouts(attempts=0), "attempts"),
        (lambda: S3Timeouts(attempts=11), "attempts"),
    ],
)
def test_timeouts_and_attempts_are_bounded(make: Callable[[], S3Timeouts], option: str) -> None:
    with pytest.raises(InvalidOptionError, match=f"archive option {option} must be"):
        make()


@pytest.fixture
def silent() -> Iterator[tuple[str, list[bytes]]]:
    """An HTTP endpoint that reads each request and never answers: its URL, and every request's first line."""
    listener = socket.create_server(("127.0.0.1", 0))
    received: list[bytes] = []
    held: list[socket.socket] = []

    def serve() -> None:
        while True:
            try:
                connection = listener.accept()[0]
            except OSError:
                return
            held.append(connection)
            received.append(connection.recv(65536).split(b"\r\n", 1)[0])

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{listener.getsockname()[1]}", received
    finally:
        listener.close()
        thread.join(5)
        for connection in held:
            connection.close()


async def test_a_store_that_does_not_answer_is_unavailable_after_bounded_attempts(
    silent: tuple[str, list[bytes]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The read timeout ends each attempt, the attempts are counted; requests name the bucket in the path."""
    url, received = silent
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")

    def factory() -> S3Client:
        timeouts = S3Timeouts(connect=1.0, read=0.2, attempts=2)
        return s3_client(region="us-east-1", endpoint_url=url, timeouts=timeouts)

    store = S3ImmutableStore(BUCKET, factory)
    with pytest.raises(BlobStoreUnavailableError):
        await store.get(KEY, sha256=DIGEST)
    assert received == [f"GET /{BUCKET}?object-lock HTTP/1.1".encode()] * 2
