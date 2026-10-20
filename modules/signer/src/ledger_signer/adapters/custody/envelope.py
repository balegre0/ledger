"""The envelope (spec §3.7): a key or a credential sealed under a data key of its own, which the KMS wraps.

``seal`` is AES-256-GCM with a fresh random 96-bit nonce: ``nonce ‖ ciphertext ‖ tag``. The associated data
is the encryption context the KMS binds the data key to (purpose, organization, company, certificate), each
name and value length-prefixed in the names' order: the sealed bytes open for their own certificate only, and
a row copied to another certificate or company does not open. Anything else (a flipped bit, another context,
another key) is ``CustodyTamperedError``, without the library's words.

``sealed`` and ``unsealed`` are the two halves custody uses: a fresh data key per secret, asked of the KMS
under the secret's context, and its plaintext dropped once it has sealed; to open, the KMS unwraps it again,
every time (no cache, plan Decision 18).
"""

import os
from typing import ClassVar, Final
lazy from collections.abc import Mapping

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ledger_kernel.errors import InvalidValueError, LedgerError
from ledger_signer.application.ports import SealedMaterial
lazy from ledger_signer.application.ports import KeyManagement, SealContext

DATA_KEY_BYTES: Final = 32
"""AES-256."""
NONCE_BYTES: Final = 12
"""GCM's 96-bit nonce, random for every seal (2^32 seals under one key stay far from a collision: one key
seals one secret here)."""
TAG_BYTES: Final = 16
"""GCM's tag: the bytes after the ciphertext."""
_LENGTH_BYTES: Final = 8


class CustodyTamperedError(LedgerError):
    """Sealed material that does not open: changed, or another certificate's (an internal error)."""

    code: ClassVar[str] = "custody_tampered"

    def __init__(self) -> None:
        super().__init__("sealed material in custody does not open for its certificate")


def associated_data(context: Mapping[str, str]) -> bytes:
    """``context`` as associated data: each name and its value, length-prefixed, in the names' order."""
    parts = (part.encode() for name in sorted(context) for part in (name, context[name]))
    return b"".join(len(part).to_bytes(_LENGTH_BYTES, "big") + part for part in parts)


def _cipher(data_key: bytes) -> AESGCM:
    if len(data_key) != DATA_KEY_BYTES:
        raise InvalidValueError(field="data_key", requirement=f"{DATA_KEY_BYTES} bytes (AES-256)")
    return AESGCM(data_key)


def seal(plaintext: bytes, data_key: bytes, associated: bytes) -> bytes:
    """``plaintext`` sealed under ``data_key``, bound to ``associated``: nonce, ciphertext and tag."""
    nonce = os.urandom(NONCE_BYTES)
    return nonce + _cipher(data_key).encrypt(nonce, plaintext, associated)


def unseal(sealed: bytes, data_key: bytes, associated: bytes) -> bytes:
    """What ``seal`` sealed; ``CustodyTamperedError`` for anything it did not seal with these."""
    cipher = _cipher(data_key)
    if len(sealed) < NONCE_BYTES + TAG_BYTES:
        raise CustodyTamperedError
    try:
        return cipher.decrypt(sealed[:NONCE_BYTES], sealed[NONCE_BYTES:], associated)
    except InvalidTag:
        raise CustodyTamperedError from None


async def sealed(kms: KeyManagement, plaintext: bytes, seal_for: SealContext) -> SealedMaterial:
    """``plaintext`` sealed under a new data key the KMS wrapped for ``seal_for``."""
    context = seal_for.context()
    data_key = await kms.new_data_key(context)
    return SealedMaterial(
        sealed=seal(plaintext, data_key.plaintext, associated_data(context)),
        wrapped_data_key=data_key.wrapped,
        kms_key_id=data_key.key_id,
    )


async def unsealed(kms: KeyManagement, material: SealedMaterial, seal_for: SealContext) -> bytes:
    """What ``material`` sealed for ``seal_for``, its data key unwrapped by the KMS for this call alone."""
    context = seal_for.context()
    data_key = await kms.unwrap(material.wrapped_data_key, key_id=material.kms_key_id, context=context)
    return unseal(material.sealed, data_key, associated_data(context))
