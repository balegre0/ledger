"""Immutable stores for tests: one in memory, and the contract every store keeps, as tests any store can run.

``MemoryImmutableStore`` refuses what the platform's stores refuse (a bad key or digest, other bytes under a
key) and raises their errors, so code tested against it meets in production what it met here; it can be told
to be unavailable. Nothing ever leaves the process.

``ImmutableStoreContract`` is the platform's store contract (``ledger_platform.blobs.store``) as tests: a
store's suite subclasses it as ``Test<Store>Contract`` and says how to make the store; pytest runs every
test of the contract against it. The file store, the memory store and the S3 store (Task 16) run it.
"""

import asyncio
import hashlib
from abc import ABCMeta, abstractmethod
from datetime import date
from typing import Final, final, override
lazy from pathlib import Path

import pytest

from ledger_kernel.errors import InvalidValueError
from ledger_platform.blobs.store import (
    MAX_KEY,
    BlobDigestMismatchError,
    BlobNotFoundError,
    BlobStoreUnavailableError,
    ImmutableStore,
    ObjectConflictError,
    StoredObject,
    digest_of,
    require_digest,
    require_key,
)

REFUSED_KEYS: Final = (
    "",
    "/do-ecf-v1/x.xml",
    "do-ecf-v1/../x.xml",
    "do-ecf-v1/..x.xml",
    "do-ecf-v1/./x.xml",
    "do-ecf-v1//x.xml",
    "do-ecf-v1/.x.xml",
    "do-ecf-v1/x.xml/",
    "do-ecf-v1/x.",
    "do-ecf-v1/x-",
    "Do-ecf-v1/x.xml",
    "do-ecf-v1\\x.xml",
    "do-ecf-v1/x y.xml",
    "do-ecf-v1/nul",
    "do-ecf-v1/con.xml",
    "com1/x.xml",
    "do-ecf-v1/lpt9.pdf",
    "a" * (MAX_KEY + 1),
)
"""Keys every store refuses: they could leave the store's place, or name two things, or none."""
_KEY: Final = "do-ecf-v1/test/0199e3a0-0000-7000-8000-000000000001/ecf/0a1b.xml"
_CONTENT: Final = b"<ECF>\r\n<eNCF>E310000000001</eNCF>\n</ECF>"
"""Both line endings: a store that translated them would change the bytes."""
_DIGEST: Final = hashlib.sha256(_CONTENT).digest()
_RETAIN: Final = date(2036, 10, 19)
_MEDIA: Final = "application/xml"
_RACERS: Final = 8


def _expect(what: str, *, holds: bool) -> None:
    """Fail the test unless ``holds``: library code states its checks through pytest, not ``assert``."""
    if not holds:
        pytest.fail(f"the store breaks its contract: {what}")


class ImmutableStoreContract(metaclass=ABCMeta):
    """The store contract as tests (see the module): a subclass makes the store, under ``root`` if need be."""

    @abstractmethod
    def make(self, root: Path) -> ImmutableStore:
        """A new, empty store; ``root`` is a directory of the test's own."""

    @pytest.fixture
    def store(self, tmp_path: Path) -> ImmutableStore:
        """The store under test."""
        return self.make(tmp_path)

    async def test_an_object_is_stored_and_read_back_byte_for_byte(self, store: ImmutableStore) -> None:
        """An object stored is read back byte for byte, with its retention; an empty one too."""
        stored = await store.put_once(_KEY, _CONTENT, retain_until=_RETAIN, media_type=_MEDIA)
        _expect(
            "put_once's answer", holds=stored == StoredObject(key=_KEY, sha256=_DIGEST, size=len(_CONTENT))
        )
        _expect("get returns the bytes stored", holds=await store.get(_KEY, sha256=_DIGEST) == _CONTENT)
        _expect("the retention is the one stored", holds=await store.retained_until(_KEY) == _RETAIN)
        empty = await store.put_once("empty/x", b"", retain_until=_RETAIN, media_type=_MEDIA)
        _expect("an empty object is an object", holds=await store.get("empty/x", sha256=empty.sha256) == b"")

    async def test_the_same_bytes_again_change_nothing_not_even_the_retention(
        self, store: ImmutableStore
    ) -> None:
        """A repeat answers the stored object and neither shortens nor extends its retention."""
        first = await store.put_once(_KEY, _CONTENT, retain_until=_RETAIN, media_type=_MEDIA)
        for other in (date(2030, 1, 1), date(2050, 1, 1)):  # neither shortened nor extended
            again = await store.put_once(_KEY, _CONTENT, retain_until=other, media_type=_MEDIA)
            _expect("a repeat answers the stored object", holds=again == first)
        _expect("a repeat keeps the first retention", holds=await store.retained_until(_KEY) == _RETAIN)

    async def test_other_bytes_under_a_key_are_a_conflict_and_change_nothing(
        self, store: ImmutableStore
    ) -> None:
        """Other bytes under a key are refused, the stored ones kept."""
        await store.put_once(_KEY, _CONTENT, retain_until=_RETAIN, media_type=_MEDIA)
        with pytest.raises(ObjectConflictError):
            await store.put_once(_KEY, b"<ECF/>", retain_until=_RETAIN, media_type=_MEDIA)
        _expect("the stored bytes stay", holds=await store.get(_KEY, sha256=_DIGEST) == _CONTENT)

    async def test_bytes_are_read_only_against_their_digest(self, store: ImmutableStore) -> None:
        """Bytes are read back only against their SHA-256; a digest of another size is refused."""
        await store.put_once(_KEY, _CONTENT, retain_until=_RETAIN, media_type=_MEDIA)
        with pytest.raises(BlobDigestMismatchError):
            await store.get(_KEY, sha256=digest_of(b"other"))
        with pytest.raises(InvalidValueError, match=r"blob\.sha256 must be a SHA-256 digest \(32 bytes\)"):
            await store.get(_KEY, sha256=_DIGEST[:31])

    async def test_a_key_that_holds_nothing_is_not_found(self, store: ImmutableStore) -> None:
        """A key that holds nothing is not found, nor its retention."""
        with pytest.raises(BlobNotFoundError):
            await store.get(_KEY, sha256=_DIGEST)
        with pytest.raises(BlobNotFoundError):
            await store.retained_until(_KEY)

    async def test_writers_racing_for_a_key_store_it_once(self, store: ImmutableStore) -> None:
        """Writers racing for a key store one object, whole."""
        stored = await asyncio.gather(
            *(store.put_once(_KEY, _CONTENT, retain_until=_RETAIN, media_type=_MEDIA) for _ in range(_RACERS))
        )
        _expect(
            "one object", holds=set(stored) == {StoredObject(key=_KEY, sha256=_DIGEST, size=len(_CONTENT))}
        )
        _expect("its bytes, whole", holds=await store.get(_KEY, sha256=_DIGEST) == _CONTENT)

    @pytest.mark.parametrize("key", REFUSED_KEYS)
    async def test_a_key_that_breaks_the_rules_is_refused(self, store: ImmutableStore, key: str) -> None:
        """Every method refuses a key that breaks the rules, before the store is asked."""
        with pytest.raises(InvalidValueError, match=r"blob\.key must be"):
            await store.put_once(key, _CONTENT, retain_until=_RETAIN, media_type=_MEDIA)
        with pytest.raises(InvalidValueError, match=r"blob\.key must be"):
            await store.get(key, sha256=_DIGEST)
        with pytest.raises(InvalidValueError, match=r"blob\.key must be"):
            await store.retained_until(key)


@final
class MemoryImmutableStore(ImmutableStore):
    """Objects and the day each is kept until, by key, put once (see the module).

    While ``unavailable_for`` calls remain, each call is ``BlobStoreUnavailableError``.
    """

    __slots__ = ("_objects", "_unavailable")

    def __init__(self, *, unavailable_for: int = 0) -> None:
        self._objects: dict[str, tuple[bytes, date]] = {}
        self._unavailable = unavailable_for

    @override
    async def put_once(
        self, key: str, content: bytes, *, retain_until: date, media_type: str
    ) -> StoredObject:
        del media_type
        require_key(key)
        self._reach()
        stored, _ = self._objects.setdefault(key, (content, retain_until))
        if stored != content:
            raise ObjectConflictError
        return StoredObject(key=key, sha256=digest_of(content), size=len(content))

    @override
    async def get(self, key: str, *, sha256: bytes) -> bytes:
        require_key(key)
        require_digest("blob", sha256)
        content, _ = self._stored(key)
        if digest_of(content) != sha256:
            raise BlobDigestMismatchError
        return content

    @override
    async def retained_until(self, key: str) -> date:
        require_key(key)
        _, retain_until = self._stored(key)
        return retain_until

    def _stored(self, key: str) -> tuple[bytes, date]:
        self._reach()
        found = self._objects.get(key)
        if found is None:
            raise BlobNotFoundError
        return found

    def _reach(self) -> None:
        if self._unavailable > 0:
            self._unavailable -= 1
            raise BlobStoreUnavailableError
