"""An immutable store (§5.2 ``blobs``, §6.8): objects written once under a key, kept until a date.

A key names one object for ever: ``put_once`` stores bytes under a key that holds nothing, answers the stored
object when the key already holds the same bytes, and refuses other bytes (``ObjectConflictError``: keys
embed their bytes' digest, so other bytes under one are a defect or a tampering). Nothing is overwritten, and
nothing is deleted before its retention date: no method deletes. ``get`` reads bytes back only against the
SHA-256 the caller recorded for them.

Keys are plain relative paths: segments of lower-case letters, digits, ``.``, ``_`` and ``-``, each led and
ended by a letter or a digit, joined by ``/``, at most ``MAX_KEY`` characters, never ``..``, no segment named
as a device on Windows (``con``, ``prn``, ``aux``, ``nul``, ``com0``-``com9``, ``lpt0``-``lpt9``, whatever its
extension). A key can neither climb out of a store's root, nor name a backend's own files (a file store keeps
its sidecars under names led by a dot), nor be one name on a file system and another (Windows drops a trailing
dot, so ``a/b.`` would be ``a/b``).
A key or a digest that breaks the rules is a defect of the code that built it (``InvalidValueError``).
"""

import hashlib
import re
from abc import ABCMeta, abstractmethod
from dataclasses import dataclass
from typing import ClassVar, Final, Protocol, final, runtime_checkable
lazy from datetime import date

from ledger_kernel.errors import InvalidValueError, LedgerError, UnavailableError

MAX_KEY: Final = 512
SHA256_BYTES: Final = 32
"""The size of a SHA-256 digest: what a stored object's ``sha256`` and ``get``'s take."""
SEGMENT: Final = r"[a-z0-9](?:[a-z0-9._-]*[a-z0-9])?"
KEY_PATTERN: Final = re.compile(rf"{SEGMENT}(?:/{SEGMENT})*", re.ASCII)
DEVICE_NAMES: Final = frozenset(
    {
        "con",
        "prn",
        "aux",
        "nul",
        *(f"com{digit}" for digit in range(10)),
        *(f"lpt{digit}" for digit in range(10)),
    }
)
"""Names Windows reserves for devices, with any extension: a file so named cannot be made there."""
_KEY_RULE: Final = (
    f"at most {MAX_KEY} characters: segments of a-z 0-9 . _ -, each led and ended by a letter or a digit, "
    "joined by /, never .., none a device name"
)


def require_key(key: str) -> None:
    """Refuse a key that breaks the rules (see the module)."""
    if (
        len(key) > MAX_KEY
        or ".." in key
        or KEY_PATTERN.fullmatch(key) is None
        or any(segment.split(".", 1)[0] in DEVICE_NAMES for segment in key.split("/"))
    ):
        raise InvalidValueError(field="blob.key", requirement=_KEY_RULE)


def require_digest(owner: str, sha256: bytes) -> None:
    """Refuse a ``sha256`` that is not a SHA-256 digest's size."""
    if len(sha256) != SHA256_BYTES:
        raise InvalidValueError(
            field=f"{owner}.sha256", requirement=f"a SHA-256 digest ({SHA256_BYTES} bytes)"
        )


def digest_of(content: bytes) -> bytes:
    """The SHA-256 of ``content``."""
    return hashlib.sha256(content).digest()


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class StoredObject:
    """An object a store holds: its key, the SHA-256 of its bytes and their size."""

    key: str
    sha256: bytes
    size: int

    def __post_init__(self) -> None:
        require_digest("stored_object", self.sha256)
        if self.size < 0:
            raise InvalidValueError(field="stored_object.size", requirement="0 or more")


@runtime_checkable
class ImmutableStore(Protocol, metaclass=ABCMeta):
    """Write-once objects kept until a retention date (see the module).

    Every implementation keeps this contract (``ledger_testing``'s tests run it against each): the errors
    below, and nothing overwritten, shortened or deleted. The methods are abstract so that they need no body.
    """

    @abstractmethod
    async def put_once(
        self, key: str, content: bytes, *, retain_until: date, media_type: str
    ) -> StoredObject:
        """Store ``content`` under ``key`` until ``retain_until`` (see the module).

        The same bytes again answer the stored object and change nothing, its retention included;
        ``ObjectConflictError`` for other bytes; ``BlobStoreUnavailableError`` when the store cannot be
        reached.
        """

    @abstractmethod
    async def get(self, key: str, *, sha256: bytes) -> bytes:
        """The bytes under ``key``, whose SHA-256 must be ``sha256``.

        ``BlobNotFoundError`` when the key holds nothing; ``BlobDigestMismatchError`` when its bytes are not
        those; ``BlobStoreUnavailableError`` when the store cannot be reached.
        """

    @abstractmethod
    async def retained_until(self, key: str) -> date:
        """The day the object under ``key`` is kept until, as it was first stored.

        ``BlobNotFoundError`` when the key holds no object; ``BlobRecordCorruptError`` when the record
        cannot be read; ``BlobStoreUnavailableError`` when the store cannot be reached.
        """


class ObjectConflictError(LedgerError):
    """Other bytes under a key that holds an object: keys embed their digest, so a defect or a tampering."""

    code: ClassVar[str] = "blob_conflict"

    def __init__(self) -> None:
        super().__init__("the key already holds other bytes: an object is never overwritten")


class BlobNotFoundError(LedgerError):
    """A key that holds nothing: what was recorded as stored is not there."""

    code: ClassVar[str] = "blob_not_found"

    def __init__(self) -> None:
        super().__init__("no object is stored under the key")


class BlobDigestMismatchError(LedgerError):
    """An object whose bytes are not those whose digest was recorded."""

    code: ClassVar[str] = "blob_digest_mismatch"

    def __init__(self) -> None:
        super().__init__("the stored bytes do not match the digest recorded for them")


class BlobRecordCorruptError(LedgerError):
    """What a store records beside an object (its retention) cannot be read: a defect or a tampering."""

    code: ClassVar[str] = "blob_record_corrupt"

    def __init__(self) -> None:
        super().__init__("the store's record of an object cannot be read")


class BlobStoreUnavailableError(UnavailableError):
    """The store could not be reached, read or written: the same call may succeed later."""

    code: ClassVar[str] = "blob_store_unavailable"

    def __init__(self) -> None:
        super().__init__("the object store is unavailable")
