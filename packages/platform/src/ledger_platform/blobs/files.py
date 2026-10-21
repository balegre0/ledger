"""The file store: an ``ImmutableStore`` in a directory, for local runs, tests and certification work.

An object is the file ``<root>/<key>``; the day it is kept until is a sidecar beside it, ``.<name>.retain``
(ISO date), written before the object and never rewritten, so a repeat ``put_once`` neither extends nor
shortens it. Each is written once: to a temporary file of its directory (``.<name>.<random>.tmp``), flushed to
disk, then linked under its name with ``os.link``, which fails if the name exists; the temporary name is then
removed and the object made read-only (again by every later ``put_once`` of the same bytes, which completes a
write stopped before it). A reader never sees half an object, and of writers racing for a key one links, the
others find its bytes. Names led by a dot are the store's own: no key has a segment led by one
(``store.require_key``).

Durability. Every directory the store makes, and every name it links, is flushed with its directory
(``sync_directory``) before ``put_once`` returns. That is what Decision 11 needs: a channel records an object
in PostgreSQL only after ``put_once`` returned, and a recorded object is never stored again (its bytes are not
kept anywhere else), so a name lost after the record would be lost for good. Windows opens no directory to
flush; NTFS journals its directory entries.

Limits. ``os.link`` makes a name atomically on local POSIX file systems and on NTFS; a file system without
hard links (some network shares, some container mounts) refuses it, and ``put_once`` is then unavailable: the
store is meant for a local directory or a named volume. A process that stops between writing a temporary
file and removing it leaves it behind (``.<name>.<random>.tmp``): never read, never an object, safe to delete.
Nothing deletes or overwrites a file: the retention date is recorded, not enforced (production keeps its
archive in S3 with object lock, Task 16; ``Settings`` refuses this store there). A directory where an object
should be is a damaged store: reading it is ``BlobStoreUnavailableError``. Every file operation runs in a
worker thread.
"""

import asyncio
import os
import secrets
import stat
from contextlib import suppress
from datetime import date
from typing import Final, final, override
lazy from collections.abc import Callable
lazy from pathlib import Path

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

READ_ONLY: Final = stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH
"""What an object's file allows once written: reading, by anyone; writing, by no one."""

type DirectorySync = Callable[[Path], None]
"""Flushes a directory's entries to disk (``sync_directory``; a test records the calls instead)."""


def sync_directory(path: Path, *, opener: Callable[[Path, int], int] = os.open) -> None:
    """Flush ``path``'s entries to disk where the system opens a directory (not Windows: then a no-op)."""
    try:
        descriptor = opener(path, os.O_RDONLY)
    except OSError:
        return
    try:
        with suppress(OSError):  # a file system that cannot flush a directory has nothing more to give
            os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _sidecar(path: Path) -> Path:
    return path.with_name(f".{path.name}.retain")


@final
class FileImmutableStore(ImmutableStore):
    """Objects as read-only files under ``root`` (see the module)."""

    __slots__ = ("_root", "_sync")

    def __init__(self, root: Path, *, sync: DirectorySync = sync_directory) -> None:
        self._root = root
        self._sync = sync

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
        content = await asyncio.to_thread(_read, self._path(key))
        if digest_of(content) != sha256:
            raise BlobDigestMismatchError
        return content

    @override
    async def retained_until(self, key: str) -> date:
        require_key(key)
        return await asyncio.to_thread(_retention, self._path(key))

    def _path(self, key: str) -> Path:
        return self._root.joinpath(*key.split("/"))

    def _put(self, path: Path, content: bytes, digest: bytes, retain_until: date) -> None:
        try:
            self._make_directories(path.parent)
            self._create_once(_sidecar(path), retain_until.isoformat().encode("ascii"))  # the first one stays
            if not self._create_once(path, content):
                if digest_of(path.read_bytes()) != digest:
                    raise ObjectConflictError
                path.chmod(READ_ONLY)
        except OSError as error:
            raise BlobStoreUnavailableError from error

    def _make_directories(self, directory: Path) -> None:
        """``directory`` and its missing parents, each flushed with the directory that holds it."""
        missing: list[Path] = []
        while not directory.exists():
            missing.append(directory)
            directory = directory.parent
        for made in reversed(missing):
            made.mkdir(exist_ok=True)  # a writer racing for it may make it first
            self._sync(made.parent)

    def _create_once(self, path: Path, content: bytes) -> bool:
        """Write ``content`` as ``path`` unless the name exists (see the module): whether this wrote it."""
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
        self._sync(path.parent)
        return True


def _read(path: Path) -> bytes:
    """``path``'s bytes; a directory in its place is a damaged store (unavailable, see the module)."""
    try:
        return path.read_bytes()
    except FileNotFoundError as error:
        raise BlobNotFoundError from error
    except OSError as error:
        raise BlobStoreUnavailableError from error


def _retention(path: Path) -> date:
    """The retention recorded for the object at ``path``, which must exist (a record alone is no object)."""
    if not path.is_file():
        raise BlobNotFoundError
    recorded = _read(_sidecar(path))
    try:
        return date.fromisoformat(recorded.decode("ascii"))
    except ValueError as error:
        raise BlobRecordCorruptError from error
