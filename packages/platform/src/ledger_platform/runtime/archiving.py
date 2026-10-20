"""The archive the app hands channels (§3.10 step 3, §6.8): an immutable store under fiscal-kit's contract.

``StoreArchive`` is fiscal-kit's ``ArchivePort`` (structurally: the platform imports no kit; its answers and
errors are ``blobs.archive``'s, which the kit exports as its own). It keeps the port's contract over any
``ImmutableStore``:

- the store's errors become the archive's, each chained to the store's: ``ObjectConflictError`` and
  ``BlobDigestMismatchError`` → ``ArchiveDigestMismatchError``, ``BlobNotFoundError`` →
  ``ArchiveObjectMissingError``, ``BlobStoreUnavailableError`` → ``ArchiveUnavailableError``;
- ``get`` refuses a ``sha256`` that is not a SHA-256 digest's size before the store is asked;
- no object is kept less than ``LEDGER_ARCHIVE_RETENTION_YEARS`` from the day it is stored (UTC): a shorter
  ``retain_until`` is raised to that, never refused, since the law's minimum (Código Tributario art. 50 h,
  ten years) is the operator's to set and a channel's own date may only be longer. A repeat ``put_once``
  changes no retention (the store's contract).

Its coroutines are facades: an archive is written and read outside any unit of work (Decision 11: archive,
then record), never while a transaction holds locks.
"""

from typing import Final, final
lazy from datetime import date

from ledger_kernel.errors import InvalidValueError
from ledger_platform.blobs.archive import (
    SHA256_BYTES,
    ArchiveDigestMismatchError,
    ArchivedObject,
    ArchiveObjectMissingError,
    ArchiveUnavailableError,
    archive_store,
)
from ledger_platform.blobs.store import (
    BlobDigestMismatchError,
    BlobNotFoundError,
    BlobStoreUnavailableError,
    ObjectConflictError,
)
from ledger_platform.runtime.facade import facade
lazy from ledger_kernel.clock import Clock
lazy from ledger_platform.blobs.store import ImmutableStore
lazy from ledger_platform.config.settings import Settings

_MARCH: Final = 3


def years_after(day: date, years: int) -> date:
    """``day``, ``years`` later; 29 February becomes 1 March in a year without one (later, never earlier)."""
    try:
        return day.replace(year=day.year + years)
    except ValueError:  # only 29 February has no such day
        return day.replace(year=day.year + years, month=_MARCH, day=1)


@final
class StoreArchive:
    """fiscal-kit's ``ArchivePort`` over ``store`` (see the module)."""

    __slots__ = ("_clock", "_store", "_years")

    def __init__(self, store: ImmutableStore, *, retention_years: int, clock: Clock) -> None:
        self._store = store
        self._years = retention_years
        self._clock = clock

    @facade
    async def put_once(
        self, key: str, content: bytes, *, retain_until: date, media_type: str
    ) -> ArchivedObject:
        """Store ``content`` under ``key`` until ``retain_until`` at the earliest (see the module)."""
        floor = years_after(self._clock().date(), self._years)
        try:
            stored = await self._store.put_once(
                key, content, retain_until=max(retain_until, floor), media_type=media_type
            )
        except ObjectConflictError as error:
            raise ArchiveDigestMismatchError from error
        except BlobStoreUnavailableError as error:
            raise ArchiveUnavailableError from error
        return ArchivedObject(key=stored.key, sha256=stored.sha256, size=stored.size)

    @facade
    async def get(self, key: str, *, sha256: bytes) -> bytes:
        """The bytes under ``key``, checked against ``sha256``."""
        if len(sha256) != SHA256_BYTES:
            raise InvalidValueError(
                field="archive.sha256", requirement=f"a SHA-256 digest ({SHA256_BYTES} bytes)"
            )
        try:
            return await self._store.get(key, sha256=sha256)
        except BlobNotFoundError as error:
            raise ArchiveObjectMissingError from error
        except BlobDigestMismatchError as error:
            raise ArchiveDigestMismatchError from error
        except BlobStoreUnavailableError as error:
            raise ArchiveUnavailableError from error


def archive_of(settings: Settings, *, clock: Clock) -> StoreArchive | None:
    """The archive the settings configure, which the app binds for channels; ``None`` without a backend."""
    store = archive_store(settings)
    if store is None:
        return None
    return StoreArchive(store, retention_years=settings.archive_retention_years, clock=clock)
