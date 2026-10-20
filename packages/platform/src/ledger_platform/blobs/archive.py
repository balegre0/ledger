"""The legal archive's vocabulary (§6.8), and the store the settings choose for it.

fiscal-kit's ``ArchivePort`` answers ``ArchivedObject`` and raises the errors below: they are defined here,
under the port's names and codes, so that the archive the app binds (``runtime.archiving.StoreArchive``,
over the store ``archive_store`` makes) is an ``ArchivePort`` without the platform importing the kit, which
builds on the platform and exports them as its own. A channel reads the archive under ``ARCHIVE_PORT_NAME``.
"""

from dataclasses import dataclass
from typing import ClassVar, Final, final

from ledger_kernel.errors import InvalidValueError, LedgerError, UnavailableError
from ledger_platform.blobs.files import FileImmutableStore
from ledger_platform.blobs.store import SHA256_BYTES, require_digest
lazy from ledger_platform.blobs.store import ImmutableStore
lazy from ledger_platform.config.settings import Settings

__all__ = [
    "ARCHIVE_PORT_NAME",
    "SHA256_BYTES",
    "ArchiveDigestMismatchError",
    "ArchiveObjectMissingError",
    "ArchiveUnavailableError",
    "ArchivedObject",
    "archive_store",
]

ARCHIVE_PORT_NAME: Final = "ArchivePort"
"""The name the app binds the archive under, fiscal-kit's ``ARCHIVE_PORT``'s."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class ArchivedObject:
    """An object the archive holds: its key, the SHA-256 of its bytes and their size."""

    key: str
    sha256: bytes
    size: int

    def __post_init__(self) -> None:
        require_digest("archived_object", self.sha256)
        if self.size < 0:
            raise InvalidValueError(field="archived_object.size", requirement="0 or more")


class ArchiveUnavailableError(UnavailableError):
    """The archive could not be reached: the same call may succeed later."""

    code: ClassVar[str] = "archive_unavailable"

    def __init__(self) -> None:
        super().__init__("the archive is unavailable")


class ArchiveDigestMismatchError(LedgerError):
    """An archived object's bytes are not those whose digest was recorded: a defect or a tampering."""

    code: ClassVar[str] = "archive_digest_mismatch"

    def __init__(self) -> None:
        super().__init__("an archived object's bytes do not match the digest recorded for them")


class ArchiveObjectMissingError(LedgerError):
    """An object the archive was asked for is not there: what PostgreSQL recorded was never stored."""

    code: ClassVar[str] = "archive_object_missing"

    def __init__(self) -> None:
        super().__init__("an archived object is missing")


def archive_store(settings: Settings) -> ImmutableStore | None:
    """The store ``LEDGER_ARCHIVE_BACKEND`` names; ``None`` when none is (no archive is bound then).

    ``file``: ``FileImmutableStore`` at ``LEDGER_ARCHIVE_PATH`` (``Settings`` requires it, and refuses this
    backend in production). The S3 object-lock store arrives with Task 16.
    """
    if settings.archive_backend is None:
        return None
    path = settings.archive_path
    if path is None:  # Settings refuses it; a model copied past its validation may still hold it
        raise InvalidValueError(field="settings.archive_path", requirement="set for the file backend")
    return FileImmutableStore(path)
