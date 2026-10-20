"""``StoreArchive``: the archive the app hands channels, an immutable store under fiscal-kit's contract."""

import hashlib
from datetime import UTC, date, datetime
from typing import Final, final, override
lazy from pathlib import Path

import pytest
from pydantic import SecretStr

from ledger_kernel.errors import ErrorKind, InvalidValueError
from ledger_platform.blobs.archive import (
    SHA256_BYTES,
    ArchiveDigestMismatchError,
    ArchivedObject,
    ArchiveObjectMissingError,
    ArchiveUnavailableError,
    archive_store,
)
from ledger_platform.blobs.files import FileImmutableStore
from ledger_platform.blobs.store import (
    BlobDigestMismatchError,
    BlobNotFoundError,
    BlobStoreUnavailableError,
    ImmutableStore,
    ObjectConflictError,
)
from ledger_platform.config.settings import Settings
from ledger_platform.runtime.archiving import StoreArchive, archive_of, years_after
from ledger_platform.runtime.facade import require_facades
lazy from ledger_kernel.errors import LedgerError
lazy from ledger_platform.blobs.store import StoredObject

KEY: Final = "do-ecf-v1/test/org/company/ecf/0a1b.xml"
CONTENT: Final = b"<ECF/>"
DIGEST: Final = hashlib.sha256(CONTENT).digest()
NOW: Final = datetime(2026, 10, 19, 23, 59, tzinfo=UTC)
MEDIA: Final = "application/xml"
URL: Final = "postgresql+psycopg://ledger_app:app@db.invalid:5432/ledger"


def clock() -> datetime:
    return NOW


@final
class Failing(ImmutableStore):
    """A store that raises ``error`` on every call."""

    def __init__(self, error: LedgerError) -> None:
        self.error = error

    @override
    async def put_once(
        self, key: str, content: bytes, *, retain_until: date, media_type: str
    ) -> StoredObject:
        del key, content, retain_until, media_type
        raise self.error

    @override
    async def get(self, key: str, *, sha256: bytes) -> bytes:
        del key, sha256
        raise self.error

    @override
    async def retained_until(self, key: str) -> date:
        del key
        raise self.error


def archive(store: ImmutableStore, years: int = 10) -> StoreArchive:
    return StoreArchive(store, retention_years=years, clock=clock)


async def test_an_object_archived_is_read_back_against_its_digest(tmp_path: Path) -> None:
    store = FileImmutableStore(tmp_path)
    archived = await archive(store).put_once(KEY, CONTENT, retain_until=date(2040, 1, 1), media_type=MEDIA)
    assert archived == ArchivedObject(key=KEY, sha256=DIGEST, size=len(CONTENT))
    assert await archive(store).get(KEY, sha256=DIGEST) == CONTENT
    assert await store.retained_until(KEY) == date(2040, 1, 1)


async def test_no_object_is_kept_less_than_the_retention_the_app_is_configured_with(tmp_path: Path) -> None:
    store = FileImmutableStore(tmp_path)
    await archive(store, years=10).put_once(KEY, CONTENT, retain_until=date(2027, 1, 1), media_type=MEDIA)
    assert await store.retained_until(KEY) == date(2036, 10, 19)  # the day stored (UTC), ten years on


@pytest.mark.parametrize(
    ("day", "years", "expected"),
    [
        (date(2026, 10, 19), 10, date(2036, 10, 19)),
        (date(2028, 2, 29), 10, date(2038, 3, 1)),  # no 29 February then: the day after, never before
        (date(2028, 2, 29), 4, date(2032, 2, 29)),
    ],
)
def test_years_after_a_day_never_fall_short(day: date, years: int, expected: date) -> None:
    assert years_after(day, years) == expected


@pytest.mark.parametrize(
    ("raised", "expected"),
    [
        (ObjectConflictError(), ArchiveDigestMismatchError),
        (BlobStoreUnavailableError(), ArchiveUnavailableError),
    ],
)
async def test_the_store_s_errors_on_put_are_the_archive_s(
    raised: LedgerError, expected: type[LedgerError]
) -> None:
    with pytest.raises(expected) as caught:
        await archive(Failing(raised)).put_once(KEY, CONTENT, retain_until=date(2040, 1, 1), media_type=MEDIA)
    assert caught.value.__cause__ is raised


@pytest.mark.parametrize(
    ("raised", "expected"),
    [
        (BlobNotFoundError(), ArchiveObjectMissingError),
        (BlobDigestMismatchError(), ArchiveDigestMismatchError),
        (BlobStoreUnavailableError(), ArchiveUnavailableError),
    ],
)
async def test_the_store_s_errors_on_get_are_the_archive_s(
    raised: LedgerError, expected: type[LedgerError]
) -> None:
    with pytest.raises(expected) as caught:
        await archive(Failing(raised)).get(KEY, sha256=DIGEST)
    assert caught.value.__cause__ is raised


def test_the_archive_s_errors_say_what_happened_and_nothing_of_the_object() -> None:
    assert (ArchiveUnavailableError.code, ArchiveUnavailableError.kind) == (
        "archive_unavailable",
        ErrorKind.UNAVAILABLE,
    )
    assert (ArchiveDigestMismatchError.code, ArchiveDigestMismatchError.kind) == (
        "archive_digest_mismatch",
        ErrorKind.INTERNAL,
    )
    assert (ArchiveObjectMissingError.code, ArchiveObjectMissingError.kind) == (
        "archive_object_missing",
        ErrorKind.INTERNAL,
    )
    assert str(ArchiveObjectMissingError()) == "an archived object is missing"


async def test_get_refuses_a_digest_that_is_no_sha256_before_asking_the_store() -> None:
    untouchable = archive(Failing(BlobStoreUnavailableError()))
    for digest in (b"", DIGEST[: SHA256_BYTES - 1], DIGEST + b"\x00"):
        with pytest.raises(InvalidValueError, match=r"archive.sha256 must be a SHA-256 digest \(32 bytes\)"):
            await untouchable.get(KEY, sha256=digest)


def test_the_archive_is_a_facade_never_called_inside_a_unit_of_work(tmp_path: Path) -> None:
    require_facades(archive(FileImmutableStore(tmp_path)))  # UnguardedFacadeError otherwise


def test_an_archived_object_names_a_sha256_and_a_size() -> None:
    with pytest.raises(
        InvalidValueError, match=r"archived_object.sha256 must be a SHA-256 digest \(32 bytes\)"
    ):
        ArchivedObject(key="k", sha256=b"short", size=0)
    with pytest.raises(InvalidValueError, match=r"archived_object.size must be 0 or more"):
        ArchivedObject(key="k", sha256=DIGEST, size=-1)


def test_the_settings_choose_the_store(tmp_path: Path) -> None:
    bare = Settings(environment="test", database_url=SecretStr(URL))
    assert archive_store(bare) is None
    assert archive_of(bare, clock=clock) is None
    filed = bare.model_copy(update={"archive_backend": "file", "archive_path": tmp_path})
    store = archive_store(filed)
    assert isinstance(store, FileImmutableStore)
    assert store.root == tmp_path
    assert isinstance(archive_of(filed, clock=clock), StoreArchive)
    pathless = bare.model_copy(update={"archive_backend": "file"})  # past Settings' own validation
    with pytest.raises(InvalidValueError, match=r"settings\.archive_path must be set for the file backend"):
        archive_store(pathless)
