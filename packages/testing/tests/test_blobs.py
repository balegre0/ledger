"""The immutable stores' one contract, kept by the memory store tests use and by the platform's file store."""

import hashlib
from datetime import date
from typing import Final
lazy from collections.abc import Callable
lazy from pathlib import Path

import pytest

from ledger_kernel.errors import InvalidValueError
from ledger_platform.blobs.archive import ArchivedObject, ArchiveObjectMissingError
from ledger_platform.blobs.files import FileImmutableStore
from ledger_platform.blobs.store import (
    BlobDigestMismatchError,
    BlobNotFoundError,
    ObjectConflictError,
    StoredObject,
)
from ledger_testing.blobs import MemoryImmutableStore
lazy from ledger_platform.blobs.store import ImmutableStore
lazy from ledger_platform.runtime.archiving import StoreArchive

KEY: Final = "do-ecf-v1/test/org/company/ecf/0a1b.xml"
CONTENT: Final = b"<ECF>\r\n</ECF>"
DIGEST: Final = hashlib.sha256(CONTENT).digest()
RETAIN: Final = date(2036, 10, 19)
MEDIA: Final = "application/xml"


def in_memory(root: Path) -> ImmutableStore:
    del root
    return MemoryImmutableStore()


def in_files(root: Path) -> ImmutableStore:
    return FileImmutableStore(root)


STORES: Final = pytest.mark.parametrize("make", [in_memory, in_files], ids=["memory", "files"])


@STORES
async def test_an_object_is_stored_once_and_read_against_its_digest(
    make: Callable[[Path], ImmutableStore], tmp_path: Path
) -> None:
    store = make(tmp_path)
    stored = await store.put_once(KEY, CONTENT, retain_until=RETAIN, media_type=MEDIA)
    assert stored == StoredObject(key=KEY, sha256=DIGEST, size=len(CONTENT))
    assert await store.get(KEY, sha256=DIGEST) == CONTENT
    assert await store.put_once(KEY, CONTENT, retain_until=date(2050, 1, 1), media_type=MEDIA) == stored
    assert await store.retained_until(KEY) == RETAIN


@STORES
async def test_nothing_is_overwritten_nor_read_against_another_digest(
    make: Callable[[Path], ImmutableStore], tmp_path: Path
) -> None:
    store = make(tmp_path)
    await store.put_once(KEY, CONTENT, retain_until=RETAIN, media_type=MEDIA)
    with pytest.raises(ObjectConflictError):
        await store.put_once(KEY, b"<ECF/>", retain_until=RETAIN, media_type=MEDIA)
    assert await store.get(KEY, sha256=DIGEST) == CONTENT
    with pytest.raises(BlobDigestMismatchError):
        await store.get(KEY, sha256=bytes(32))


@STORES
async def test_a_missing_object_and_a_bad_key_or_digest_are_said_so(
    make: Callable[[Path], ImmutableStore], tmp_path: Path
) -> None:
    store = make(tmp_path)
    with pytest.raises(BlobNotFoundError):
        await store.get(KEY, sha256=DIGEST)
    with pytest.raises(BlobNotFoundError):
        await store.retained_until(KEY)
    with pytest.raises(InvalidValueError, match=r"blob\.key"):
        await store.put_once("../x", CONTENT, retain_until=RETAIN, media_type=MEDIA)
    with pytest.raises(InvalidValueError, match=r"blob\.key"):
        await store.retained_until("../x")
    with pytest.raises(InvalidValueError, match=r"blob\.sha256"):
        await store.get(KEY, sha256=DIGEST[:8])


async def test_the_archive_fixture_is_an_archive_over_the_memory_store(
    archive: StoreArchive, archive_store: MemoryImmutableStore
) -> None:
    stored = await archive.put_once(KEY, CONTENT, retain_until=date(2027, 1, 1), media_type=MEDIA)
    assert stored == ArchivedObject(key=KEY, sha256=DIGEST, size=len(CONTENT))
    assert await archive.get(KEY, sha256=DIGEST) == CONTENT
    assert await archive_store.retained_until(KEY) == date(2036, 11, 2)  # ten years from the test's clock
    with pytest.raises(ArchiveObjectMissingError):
        await archive.get("do-ecf-v1/none.xml", sha256=DIGEST)
