"""The memory store keeps the stores' one contract; the archive fixture is an archive over it."""

import hashlib
from datetime import date
from typing import Final, final, override
lazy from pathlib import Path

import pytest

from ledger_platform.blobs.archive import ArchivedObject, ArchiveObjectMissingError
from ledger_platform.blobs.store import BlobStoreUnavailableError, ImmutableStore
from ledger_testing.blobs import ImmutableStoreContract, MemoryImmutableStore
lazy from ledger_platform.blobs.store import StoredObject
lazy from ledger_platform.runtime.archiving import StoreArchive

KEY: Final = "do-ecf-v1/test/org/company/ecf/0a1b.xml"
CONTENT: Final = b"<ECF>\r\n</ECF>"
DIGEST: Final = hashlib.sha256(CONTENT).digest()
RETAIN: Final = date(2036, 10, 19)
MEDIA: Final = "application/xml"


@final
class TestMemoryStoreContract(ImmutableStoreContract):
    """The store contract, kept by the memory store."""

    @override
    def make(self, root: Path) -> ImmutableStore:
        del root
        return MemoryImmutableStore()


async def test_the_memory_store_is_unavailable_for_as_many_calls_as_told() -> None:
    store = MemoryImmutableStore(unavailable_for=3)
    with pytest.raises(BlobStoreUnavailableError):
        await store.put_once(KEY, CONTENT, retain_until=RETAIN, media_type=MEDIA)
    with pytest.raises(BlobStoreUnavailableError):
        await store.get(KEY, sha256=DIGEST)
    with pytest.raises(BlobStoreUnavailableError):
        await store.retained_until(KEY)
    await store.put_once(KEY, CONTENT, retain_until=RETAIN, media_type=MEDIA)
    assert await store.get(KEY, sha256=DIGEST) == CONTENT


async def test_the_archive_fixture_is_an_archive_over_the_memory_store(
    archive: StoreArchive, archive_store: MemoryImmutableStore
) -> None:
    stored = await archive.put_once(KEY, CONTENT, retain_until=date(2027, 1, 1), media_type=MEDIA)
    assert stored == ArchivedObject(key=KEY, sha256=DIGEST, size=len(CONTENT))
    assert await archive.get(KEY, sha256=DIGEST) == CONTENT
    assert await archive_store.retained_until(KEY) == date(2036, 11, 2)  # ten years from the test's clock
    with pytest.raises(ArchiveObjectMissingError):
        await archive.get("do-ecf-v1/none.xml", sha256=DIGEST)


@final
class Forgetful(ImmutableStore):
    """A store that keeps its objects and forgets their retention: the contract must catch it."""

    def __init__(self) -> None:
        self.kept = MemoryImmutableStore()

    @override
    async def put_once(
        self, key: str, content: bytes, *, retain_until: date, media_type: str
    ) -> StoredObject:
        return await self.kept.put_once(key, content, retain_until=retain_until, media_type=media_type)

    @override
    async def get(self, key: str, *, sha256: bytes) -> bytes:
        return await self.kept.get(key, sha256=sha256)

    @override
    async def retained_until(self, key: str) -> date:
        del key
        return date(2000, 1, 1)


@final
class ForgetfulContract(ImmutableStoreContract):
    """The contract held against a store that breaks it (not collected: no ``Test`` prefix)."""

    @override
    def make(self, root: Path) -> ImmutableStore:
        del root
        return Forgetful()


async def test_the_contract_fails_a_store_that_breaks_it(tmp_path: Path) -> None:
    contract = ForgetfulContract()
    with pytest.raises(
        pytest.fail.Exception, match="the store breaks its contract: the retention is the one stored"
    ):
        await contract.test_an_object_is_stored_and_read_back_byte_for_byte(contract.make(tmp_path))
