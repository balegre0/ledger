"""The file store: an ``ImmutableStore`` in a directory, for local runs, tests and certification work.

An object is the file ``<root>/<key>``; the day it is kept until is a sidecar beside it,
``.<name>.retain`` (ISO date), written before the object and never rewritten, so a repeat ``put_once``
neither extends nor shortens it. Each is written once: to a temporary file of its directory
(``.<name>.<random>.tmp``), flushed to disk, then linked under its name, which fails if the name exists
(``os.link``, atomic on every file system the store runs on); the temporary name is then removed and the
object made read-only. A reader never sees half an object, and of writers racing for a key one links, the
others find its bytes. Names led by a dot are the store's own: no key has a segment led by one
(``store.require_key``).

Nothing deletes or overwrites a file: the retention date is recorded, not enforced (production keeps its
archive in S3 with object lock, Task 16; ``Settings`` refuses this store there). Every file operation runs in
a worker thread. Limit: the directory entries are not flushed to disk (no portable way), so a host that
crashes right after a write may lose the object's name; ``put_once`` then stores it again.
"""

import asyncio
import os
import secrets
import stat
from datetime import date
from typing import Final, final, override
lazy from pathlib import Path

from ledger_platform.blobs.store import (
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

READ_ONLY: Final = stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH
"""What an object's file allows once written: reading, by anyone; writing, by no one."""


def _sidecar(path: Path) -> Path:
    return path.with_name(f".{path.name}.retain")


def _create_once(path: Path, content: bytes) -> bool:
    """Write ``content`` as ``path`` unless the name exists (see the module): whether this call wrote it."""
    staged = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    try:
        with staged.open("xb") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        try:
            os.link(staged, path)
        except FileExistsError:
            return False
    finally:
        staged.unlink(missing_ok=True)
    path.chmod(READ_ONLY)
    return True


@final
class FileImmutableStore(ImmutableStore):
    """Objects as read-only files under ``root`` (see the module)."""

    __slots__ = ("_root",)

    def __init__(self, root: Path) -> None:
        self._root = root

    @property
    def root(self) -> Path:
        """The directory the objects are under."""
        return self._root

    @override
    async def put_once(
        self, key: str, content: bytes, *, retain_until: date, media_type: str
    ) -> StoredObject:
        """Store ``content`` as ``<root>/<key>`` once; a file has no media type to keep."""
        del media_type
        require_key(key)
        digest = digest_of(content)
        await asyncio.to_thread(self._put, self._path(key), content, digest, retain_until)
        return StoredObject(key=key, sha256=digest, size=len(content))

    @override
    async def get(self, key: str, *, sha256: bytes) -> bytes:
        require_key(key)
        require_digest("blob", sha256)
        content = await asyncio.to_thread(self._read, self._path(key))
        if digest_of(content) != sha256:
            raise BlobDigestMismatchError
        return content

    @override
    async def retained_until(self, key: str) -> date:
        require_key(key)
        recorded = await asyncio.to_thread(self._read, _sidecar(self._path(key)))
        return date.fromisoformat(recorded.decode("ascii"))

    def _path(self, key: str) -> Path:
        return self._root.joinpath(*key.split("/"))

    @staticmethod
    def _put(path: Path, content: bytes, digest: bytes, retain_until: date) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            _create_once(_sidecar(path), retain_until.isoformat().encode("ascii"))  # the first one stays
            if not _create_once(path, content) and digest_of(path.read_bytes()) != digest:
                raise ObjectConflictError
        except OSError as error:
            raise BlobStoreUnavailableError from error

    @staticmethod
    def _read(path: Path) -> bytes:
        try:
            return path.read_bytes()
        except FileNotFoundError as error:
            raise BlobNotFoundError from error
        except OSError as error:
            raise BlobStoreUnavailableError from error
