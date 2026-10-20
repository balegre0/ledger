"""A KMS of one master key in a file: for a laptop and the tests, never where keys are real (Decision 14).

It honours the ``KeyManagement`` contract as far as a file can: a data key is wrapped with AES-256-GCM under
the master key, the encryption context as associated data, so it unwraps under that context only; the key id
names the master key (``local:`` and the first 8 bytes of its SHA-256, in hexadecimal), and an unwrap for
another is refused. What a file cannot give (a workload identity, a log of every use, rotation) is why only
``local`` and ``test`` accept it; every other environment refuses it (``KmsLocalRefusedError``).

The key file holds the 32 bytes of the master key. In ``local`` it is created when it is missing, readable
and writable by its owner alone where the file system keeps POSIX modes (on Windows its directory's access
control list protects it); in ``test`` it must exist. An existing file is never replaced; one that cannot be
made or read is ``KmsKeyFileError``. Neither its content nor its path appears in an error, a log line or a
``repr``.
"""

import hashlib
import os
import secrets
import sys
from typing import ClassVar, Final, final, override
lazy from collections.abc import Mapping
lazy from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ledger_kernel.errors import LedgerError
from ledger_signer.adapters.custody.envelope import DATA_KEY_BYTES, NONCE_BYTES, TAG_BYTES, associated_data
from ledger_signer.application.ports import DataKey, KeyManagement, KmsRefusedError
lazy from ledger_platform.config.settings import Environment

MASTER_KEY_BYTES: Final = 32
"""AES-256."""
KEY_ID_PREFIX: Final = "local:"
_KEY_ID_BYTES: Final = 8
_OWNER_ONLY: Final = 0o600
_CREATE: Final = os.O_WRONLY | os.O_CREAT | os.O_EXCL | (os.O_BINARY if sys.platform == "win32" else 0)
"""A new file only (never one that exists), written as bytes (Windows translates line ends otherwise)."""
_ALLOWED: Final[frozenset[Environment]] = frozenset({"local", "test"})
"""Where a key in a file is acceptable: a laptop and the tests (the other environments hold real keys)."""
_TEMPORARY_BYTES: Final = 8


class KmsLocalRefusedError(LedgerError):
    """The local-file KMS in an environment that holds real keys: a defect of the deployment."""

    code: ClassVar[str] = "kms_local_refused"

    def __init__(self) -> None:
        super().__init__("the local-file KMS is for local development and tests only")


class KmsKeyFileError(LedgerError):
    """The local-file KMS's key file cannot be made or read, or holds no 256-bit key (never said where)."""

    code: ClassVar[str] = "kms_key_file_unusable"

    def __init__(self) -> None:
        super().__init__(f"the local-file KMS's key file must exist and hold {MASTER_KEY_BYTES} bytes")


def _create(path: Path) -> None:
    """A new key file at ``path``, its owner's alone; one that exists (made meanwhile, or before) is kept.

    Crash-safe: the key is written to a temporary file of its own in the same directory, made to disk, then
    linked to ``path``, which never names a file holding less than a whole key. Of starters racing, the first
    link wins and every other keeps it.
    """
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(_TEMPORARY_BYTES)}.tmp")
    try:
        descriptor = os.open(temporary, _CREATE, _OWNER_ONLY)
        with os.fdopen(descriptor, "wb") as file:
            file.write(os.urandom(MASTER_KEY_BYTES))
            file.flush()
            os.fsync(file.fileno())
        os.link(temporary, path)
    except FileExistsError:  # another starter's key is there first: it is the one kept
        pass
    except OSError:
        raise KmsKeyFileError from None
    finally:
        temporary.unlink(missing_ok=True)


def _master(path: Path, environment: Environment) -> bytes:
    if environment == "local":
        _create(path)
    try:
        master = path.read_bytes()
    except OSError:  # missing, a directory, unreadable: never said which, nor where
        raise KmsKeyFileError from None
    if len(master) != MASTER_KEY_BYTES:
        raise KmsKeyFileError
    return master


@final
class LocalFileKms(KeyManagement):
    """``KeyManagement`` over a master key in a file (see the module); it reads the file when it is made."""

    __slots__ = ("_master", "key_id")

    def __init__(self, path: Path, *, environment: Environment) -> None:
        if environment not in _ALLOWED:
            raise KmsLocalRefusedError
        master = _master(path, environment)
        self._master = AESGCM(master)
        self.key_id: Final = KEY_ID_PREFIX + hashlib.sha256(master).digest()[:_KEY_ID_BYTES].hex()
        """The master key's id, stored with every data key it wraps."""

    @override
    async def new_data_key(self, context: Mapping[str, str]) -> DataKey:
        plaintext = os.urandom(DATA_KEY_BYTES)
        nonce = os.urandom(NONCE_BYTES)
        wrapped = nonce + self._master.encrypt(nonce, plaintext, associated_data(context))
        return DataKey(plaintext=plaintext, wrapped=wrapped, key_id=self.key_id)

    @override
    async def unwrap(self, wrapped: bytes, *, key_id: str, context: Mapping[str, str]) -> bytes:
        if key_id != self.key_id or len(wrapped) < NONCE_BYTES + TAG_BYTES:
            raise KmsRefusedError
        try:
            return self._master.decrypt(
                wrapped[:NONCE_BYTES], wrapped[NONCE_BYTES:], associated_data(context)
            )
        except InvalidTag:
            raise KmsRefusedError from None

    @override
    async def ready(self) -> None:
        """Always ready: its key was read when it was made."""
