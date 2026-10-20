"""An immutable store in memory, for tests: the platform's store contract, kept in a dictionary.

It refuses what the platform's stores refuse (a bad key or digest, other bytes under a key) and raises their
errors, so code tested against it meets in production what it met here. Nothing ever leaves the process.
"""

from typing import final, override
lazy from datetime import date

from ledger_platform.blobs.store import (
    BlobDigestMismatchError,
    BlobNotFoundError,
    ImmutableStore,
    ObjectConflictError,
    StoredObject,
    digest_of,
    require_digest,
    require_key,
)


@final
class MemoryImmutableStore(ImmutableStore):
    """Objects and the day each is kept until, by key, put once (see the module)."""

    __slots__ = ("_objects",)

    def __init__(self) -> None:
        self._objects: dict[str, tuple[bytes, date]] = {}

    @override
    async def put_once(
        self, key: str, content: bytes, *, retain_until: date, media_type: str
    ) -> StoredObject:
        del media_type
        require_key(key)
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
        found = self._objects.get(key)
        if found is None:
            raise BlobNotFoundError
        return found
