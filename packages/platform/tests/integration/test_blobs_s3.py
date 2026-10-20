"""``S3ImmutableStore`` against S3 itself, as LocalStack serves it: the store contract and object lock.

Each test has a bucket of its own, made with object lock enabled as production's is
(``deploy/local/localstack/init-s3.sh`` makes the local stack's the same way). The stores under test use the
client production makes (``s3_client``), its credentials found by AWS's chain in the environment the
``aws`` fixture sets. What LocalStack cannot be made to answer is ``tests/unit/test_blobs_s3_requests.py``'s.
"""

import base64
import hashlib
from datetime import UTC, date, datetime
from functools import partial
from typing import Final, final, override
lazy from collections.abc import AsyncGenerator, Callable
lazy from pathlib import Path

import pytest
from botocore.exceptions import ClientError
from pydantic import SecretStr

from ledger_platform.blobs.s3 import (
    RETENTION_METADATA,
    BlobStoreRefusedError,
    S3ImmutableStore,
    locked_until,
    s3_client,
)
from ledger_platform.blobs.store import BlobRecordCorruptError, ObjectConflictError
from ledger_platform.config.settings import Settings
from ledger_platform.runtime.archiving import archive_of
from ledger_testing.blobs import ImmutableStoreContract
lazy from ledger_platform.blobs.store import ImmutableStore
lazy from ledger_testing.localstack import LocalStack

KEY: Final = "do-ecf-v1/test/org/company/ecf/0a1b.xml"
CONTENT: Final = b"<ECF>\r\n<eNCF>E310000000001</eNCF>\n</ECF>"
DIGEST: Final = hashlib.sha256(CONTENT).digest()
CHECKSUM: Final = base64.b64encode(DIGEST).decode("ascii")
RETAIN: Final = date(2036, 10, 19)
MEDIA: Final = "application/xml"
URL: Final = "postgresql+psycopg://ledger_app:app@db.invalid:5432/ledger"

pytestmark = pytest.mark.xdist_group("localstack")
"""One worker runs every test that needs LocalStack, so that one container serves them."""


@pytest.fixture
def aws(localstack: LocalStack, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> LocalStack:
    """AWS's chain finds LocalStack's credentials in the environment, and nothing of the developer's."""
    for name in ("AWS_PROFILE", "AWS_SESSION_TOKEN", "AWS_ENDPOINT_URL", "AWS_ENDPOINT_URL_S3"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "config"))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "credentials"))
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", localstack.credential)
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", localstack.credential)
    return localstack


def production_store(localstack: LocalStack, bucket: str) -> S3ImmutableStore:
    """The store as ``archive_store`` makes it, on LocalStack's endpoint."""
    return S3ImmutableStore(
        bucket, partial(s3_client, region=localstack.region, endpoint_url=localstack.endpoint_url)
    )


@final
class TestS3StoreContract(ImmutableStoreContract):
    """The store contract, kept by the S3 store: a new bucket with object lock for each test."""

    localstack: LocalStack | None = None
    """The run's LocalStack, which ``on`` gives each test (its credentials set) before the store is made."""

    @pytest.fixture(autouse=True)
    def on(self, aws: LocalStack) -> None:
        self.localstack = aws

    @override
    def make(self, root: Path) -> ImmutableStore:
        del root
        if self.localstack is None:
            pytest.fail("the store is made before LocalStack was given")
        return production_store(self.localstack, self.localstack.bucket())


@pytest.fixture
def bucket(localstack: LocalStack) -> str:
    return localstack.bucket()


@pytest.fixture
async def store(aws: LocalStack, bucket: str) -> AsyncGenerator[S3ImmutableStore]:
    opened = production_store(aws, bucket)
    try:
        yield opened
    finally:
        await opened.aclose()


async def test_an_object_is_locked_in_compliance_mode_through_its_retention_day(
    store: S3ImmutableStore, localstack: LocalStack, bucket: str
) -> None:
    await store.put_once(KEY, CONTENT, retain_until=RETAIN, media_type=MEDIA)
    head = localstack.client().head_object(Bucket=bucket, Key=KEY, ChecksumMode="ENABLED")
    assert (head.get("ObjectLockMode"), head.get("ObjectLockRetainUntilDate")) == (
        "COMPLIANCE",
        locked_until(RETAIN),
    )
    assert (head.get("ContentType"), head.get("ServerSideEncryption")) == (MEDIA, "AES256")


async def test_nobody_deletes_or_shortens_a_locked_object(
    store: S3ImmutableStore, localstack: LocalStack, bucket: str
) -> None:
    """The store has no delete; the bucket refuses one, even to whoever may bypass governance."""
    assert not {name for name in dir(store) if not name.startswith("_")} - {
        "aclose",
        "bucket",
        "check",
        "get",
        "put_once",
        "retained_until",
    }
    await store.put_once(KEY, CONTENT, retain_until=RETAIN, media_type=MEDIA)
    client = localstack.client()
    version = client.head_object(Bucket=bucket, Key=KEY).get("VersionId", "")
    assert version
    removals: tuple[Callable[[], object], ...] = (
        lambda: client.delete_object(Bucket=bucket, Key=KEY, VersionId=version),
        lambda: client.delete_object(
            Bucket=bucket, Key=KEY, VersionId=version, BypassGovernanceRetention=True
        ),
        lambda: client.put_object_retention(
            Bucket=bucket,
            Key=KEY,
            VersionId=version,
            Retention={"Mode": "COMPLIANCE", "RetainUntilDate": datetime(2030, 1, 1, tzinfo=UTC)},
        ),
    )
    for removal in removals:
        with pytest.raises(ClientError, match="AccessDenied"):
            removal()
    assert await store.get(KEY, sha256=DIGEST) == CONTENT
    assert await store.retained_until(KEY) == RETAIN


async def test_an_object_hidden_behind_a_delete_marker_is_a_corrupt_record_and_never_replaced(
    store: S3ImmutableStore, localstack: LocalStack, bucket: str
) -> None:
    """A delete without a version id is allowed by object lock: it hides the locked version behind a marker.

    The archive reads that as tampering, and writes nothing under the key, other bytes least of all.
    """
    await store.put_once(KEY, CONTENT, retain_until=RETAIN, media_type=MEDIA)
    client = localstack.client()
    assert client.delete_object(Bucket=bucket, Key=KEY).get("DeleteMarker") is True
    with pytest.raises(BlobRecordCorruptError):
        await store.get(KEY, sha256=DIGEST)
    with pytest.raises(BlobRecordCorruptError):
        await store.retained_until(KEY)
    for content in (CONTENT, b"<ECF>forged</ECF>"):
        with pytest.raises(BlobRecordCorruptError):
            await store.put_once(KEY, content, retain_until=RETAIN, media_type=MEDIA)
    versions = client.list_object_versions(Bucket=bucket, Prefix=KEY).get("Versions", [])
    assert len(versions) == 1  # only the locked original


@pytest.mark.parametrize("mode", ["GOVERNANCE", None], ids=["governance", "unlocked"])
async def test_the_same_bytes_not_under_a_compliance_lock_are_never_answered_as_stored(
    store: S3ImmutableStore, localstack: LocalStack, bucket: str, mode: str | None
) -> None:
    """An object another writer left with the same bytes but a lock that can be lifted, or none."""
    client = localstack.client()
    metadata = {RETENTION_METADATA: RETAIN.isoformat()}
    if mode is None:
        client.put_object(
            Bucket=bucket,
            Key=KEY,
            Body=CONTENT,
            ChecksumAlgorithm="SHA256",
            ChecksumSHA256=CHECKSUM,
            Metadata=metadata,
        )
    else:
        client.put_object(
            Bucket=bucket,
            Key=KEY,
            Body=CONTENT,
            ChecksumAlgorithm="SHA256",
            ChecksumSHA256=CHECKSUM,
            Metadata=metadata,
            ObjectLockMode="GOVERNANCE",
            ObjectLockRetainUntilDate=locked_until(RETAIN),
        )
    with pytest.raises(BlobRecordCorruptError):
        await store.put_once(KEY, CONTENT, retain_until=RETAIN, media_type=MEDIA)
    with pytest.raises(BlobRecordCorruptError):
        await store.retained_until(KEY)


async def test_other_bytes_under_a_locked_key_are_a_conflict(store: S3ImmutableStore) -> None:
    await store.put_once(KEY, CONTENT, retain_until=RETAIN, media_type=MEDIA)
    with pytest.raises(ObjectConflictError):
        await store.put_once(KEY, b"<ECF/>", retain_until=RETAIN, media_type=MEDIA)


async def test_a_bucket_without_object_lock_refuses_the_archive(aws: LocalStack) -> None:
    """A bucket made without object lock cannot hold the legal archive: refused before anything is written.

    AWS would refuse the locked write itself; LocalStack 4.14 stores it, unlocked: the store asks first.
    """
    bucket = aws.bucket(object_lock=False)
    unlocked = production_store(aws, bucket)
    with pytest.raises(BlobStoreRefusedError, match=r"\(ObjectLockConfigurationNotFoundError\)"):
        await unlocked.put_once(KEY, CONTENT, retain_until=RETAIN, media_type=MEDIA)
    with pytest.raises(BlobStoreRefusedError, match=r"\(ObjectLockConfigurationNotFoundError\)"):
        await unlocked.check()
    assert aws.client().list_objects_v2(Bucket=bucket).get("KeyCount") == 0
    await unlocked.aclose()


async def test_a_missing_bucket_is_refused(aws: LocalStack) -> None:
    missing = production_store(aws, "ledger-no-such-bucket")
    with pytest.raises(BlobStoreRefusedError, match=r"\(NoSuchBucket\)"):
        await missing.check()
    await missing.aclose()


async def test_the_archive_the_settings_configure_writes_to_the_bucket(aws: LocalStack, bucket: str) -> None:
    """The settings name the bucket, the region and LocalStack; credentials come from AWS's own chain."""
    settings = Settings(
        environment="test",
        database_url=SecretStr(URL),
        archive_backend="s3",
        archive_bucket=bucket,
        archive_region=aws.region,
        archive_endpoint_url=aws.endpoint_url,
    )
    archive = archive_of(settings, clock=lambda: datetime(2026, 10, 20, tzinfo=UTC))
    assert archive is not None
    stored = await archive.put_once(KEY, CONTENT, retain_until=date(2027, 1, 1), media_type=MEDIA)
    assert stored.sha256 == DIGEST
    assert await archive.get(KEY, sha256=DIGEST) == CONTENT
    retained = await production_store(aws, bucket).retained_until(KEY)
    assert retained == date(2036, 10, 20)  # the app's ten years, never the channel's shorter date
