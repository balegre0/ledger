"""``S3ImmutableStore`` against S3 itself, as LocalStack serves it: the store contract and object lock.

Each test has a bucket of its own, made with object lock enabled as production's is
(``deploy/local/localstack/init-s3.sh`` makes the local stack's the same way). What LocalStack cannot be
made to answer is ``tests/unit/test_blobs_s3_requests.py``'s.
"""

import hashlib
from datetime import UTC, date, datetime
from typing import Final, final, override
lazy from collections.abc import Callable
lazy from pathlib import Path

import pytest
from botocore.exceptions import ClientError
from pydantic import SecretStr

from ledger_platform.blobs.s3 import BlobStoreRefusedError, S3ImmutableStore, locked_until
from ledger_platform.config.settings import Settings
from ledger_platform.runtime.archiving import archive_of
from ledger_testing.blobs import ImmutableStoreContract
lazy from ledger_platform.blobs.store import ImmutableStore
lazy from ledger_testing.localstack import LocalStack

KEY: Final = "do-ecf-v1/test/org/company/ecf/0a1b.xml"
CONTENT: Final = b"<ECF>\r\n<eNCF>E310000000001</eNCF>\n</ECF>"
DIGEST: Final = hashlib.sha256(CONTENT).digest()
RETAIN: Final = date(2036, 10, 19)
MEDIA: Final = "application/xml"
URL: Final = "postgresql+psycopg://ledger_app:app@db.invalid:5432/ledger"

pytestmark = pytest.mark.xdist_group("localstack")
"""One worker runs every test that needs LocalStack, so that one container serves them."""


@final
class TestS3StoreContract(ImmutableStoreContract):
    """The store contract, kept by the S3 store: a new bucket with object lock for each test."""

    localstack: LocalStack | None = None
    """The run's LocalStack, which ``on`` gives each test before its store is made."""

    @pytest.fixture(autouse=True)
    def on(self, localstack: LocalStack) -> None:
        self.localstack = localstack

    @override
    def make(self, root: Path) -> ImmutableStore:
        del root
        if self.localstack is None:
            pytest.fail("the store is made before LocalStack was given")
        return S3ImmutableStore(self.localstack.bucket(), self.localstack.client)


@pytest.fixture
def bucket(localstack: LocalStack) -> str:
    return localstack.bucket()


@pytest.fixture
def store(localstack: LocalStack, bucket: str) -> S3ImmutableStore:
    return S3ImmutableStore(bucket, localstack.client)


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
        "bucket",
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


async def test_a_bucket_without_object_lock_refuses_the_archive(localstack: LocalStack) -> None:
    """A bucket made without object lock cannot hold the legal archive: refused before anything is written.

    AWS would refuse the locked write itself; LocalStack 4.14 stores it, unlocked: the store asks first.
    """
    bucket = localstack.bucket(object_lock=False)
    unlocked = S3ImmutableStore(bucket, localstack.client)
    with pytest.raises(BlobStoreRefusedError, match=r"\(ObjectLockConfigurationNotFoundError\)"):
        await unlocked.put_once(KEY, CONTENT, retain_until=RETAIN, media_type=MEDIA)
    assert localstack.client().list_objects_v2(Bucket=bucket).get("KeyCount") == 0


async def test_a_missing_bucket_is_refused(localstack: LocalStack) -> None:
    missing = S3ImmutableStore("ledger-no-such-bucket", localstack.client)
    with pytest.raises(BlobStoreRefusedError, match=r"\(NoSuchBucket\)"):
        await missing.get(KEY, sha256=DIGEST)


async def test_the_archive_the_settings_configure_writes_to_the_bucket(
    localstack: LocalStack, bucket: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The settings name the bucket, the region and LocalStack; credentials come from AWS's own chain."""
    for name in ("AWS_PROFILE", "AWS_SESSION_TOKEN", "AWS_ENDPOINT_URL", "AWS_ENDPOINT_URL_S3"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "config"))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "credentials"))
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", localstack.credential)
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", localstack.credential)
    settings = Settings(
        environment="test",
        database_url=SecretStr(URL),
        archive_backend="s3",
        archive_bucket=bucket,
        archive_region=localstack.region,
        archive_endpoint_url=localstack.endpoint_url,
    )
    archive = archive_of(settings, clock=lambda: datetime(2026, 10, 20, tzinfo=UTC))
    assert archive is not None
    stored = await archive.put_once(KEY, CONTENT, retain_until=date(2027, 1, 1), media_type=MEDIA)
    assert stored.sha256 == DIGEST
    assert await archive.get(KEY, sha256=DIGEST) == CONTENT
    retained = await S3ImmutableStore(bucket, localstack.client).retained_until(KEY)
    assert retained == date(2036, 10, 20)  # the app's ten years, never the channel's shorter date
