"""The envelope: a secret sealed under a data key, bound to its certificate and owners (spec §3.7).

Any change to the sealed bytes, the associated data or the key is refused as tampering; nonces are fresh for
every seal; the data key a seal needs comes from the KMS, wrapped under the same context it is unsealed with.
"""

from typing import Final

import pytest

from ledger_kernel.errors import ErrorKind, InvalidValueError
from ledger_kernel.ids import CompanyId, OrganizationId, new_id
from ledger_signer.adapters.custody.envelope import (
    DATA_KEY_BYTES,
    NONCE_BYTES,
    CustodyTamperedError,
    associated_data,
    seal,
    sealed,
    unseal,
    unsealed,
)
from ledger_signer.application.ports import SealContext
from ledger_signer.domain.certificates import CertificateId
lazy from ledger_signer.application.ports import KeyManagement

SECRET: Final = b"SECRET-SENTINEL: a PKCS#8 key would be here"
DATA_KEY: Final = bytes(range(DATA_KEY_BYTES))
OTHER_KEY: Final = bytes(reversed(range(DATA_KEY_BYTES)))
ASSOCIATED: Final = b"certificate one"
SEALS: Final = 10_000


def a_context() -> SealContext:
    return SealContext(
        organization=OrganizationId(new_id()),
        company=CompanyId(new_id()),
        certificate=CertificateId(new_id()),
    )


def test_a_sealed_secret_opens_with_its_key_and_associated_data() -> None:
    closed = seal(SECRET, DATA_KEY, ASSOCIATED)
    assert SECRET not in closed
    assert unseal(closed, DATA_KEY, ASSOCIATED) == SECRET


def test_any_flipped_bit_is_tampering() -> None:
    closed = seal(SECRET, DATA_KEY, ASSOCIATED)
    for bit in range(len(closed) * 8):
        flipped = bytearray(closed)
        flipped[bit // 8] ^= 1 << (bit % 8)
        with pytest.raises(CustodyTamperedError):
            unseal(bytes(flipped), DATA_KEY, ASSOCIATED)


@pytest.mark.parametrize(
    ("key", "associated"), [(DATA_KEY, b"certificate two"), (OTHER_KEY, ASSOCIATED)], ids=["data", "key"]
)
def test_another_associated_data_or_key_is_tampering(key: bytes, associated: bytes) -> None:
    closed = seal(SECRET, DATA_KEY, ASSOCIATED)
    with pytest.raises(CustodyTamperedError) as caught:
        unseal(closed, key, associated)
    assert caught.value.code == "custody_tampered"
    assert caught.value.kind is ErrorKind.INTERNAL
    assert caught.value.__cause__ is None  # the library's error stays behind
    assert caught.value.__suppress_context__


def test_bytes_shorter_than_a_nonce_and_a_tag_are_tampering() -> None:
    with pytest.raises(CustodyTamperedError):
        unseal(bytes(NONCE_BYTES), DATA_KEY, ASSOCIATED)


def test_nonces_never_repeat() -> None:
    nonces = {seal(SECRET, DATA_KEY, ASSOCIATED)[:NONCE_BYTES] for _ in range(SEALS)}
    assert len(nonces) == SEALS


@pytest.mark.parametrize("size", [16, 24, 31, 33])
def test_a_data_key_is_aes_256_or_nothing(size: int) -> None:
    with pytest.raises(InvalidValueError):
        seal(SECRET, bytes(size), ASSOCIATED)
    with pytest.raises(InvalidValueError):
        unseal(seal(SECRET, DATA_KEY, ASSOCIATED), bytes(size), ASSOCIATED)


def test_associated_data_is_the_sorted_length_prefixed_context() -> None:
    context = a_context().context()
    expected = b"".join(
        len(part).to_bytes(8, "big") + part
        for name in sorted(context)
        for part in (name.encode(), context[name].encode())
    )
    assert associated_data(context) == expected
    assert (
        associated_data(dict(reversed(list(context.items())))) == expected
    )  # the order given does not count
    assert associated_data({"ab": "c"}) != associated_data({"a": "bc"})  # no part runs into the next


async def test_a_secret_is_sealed_under_a_fresh_data_key_the_kms_wrapped(kms: KeyManagement) -> None:
    context = a_context()
    material = await sealed(kms, SECRET, context)
    assert SECRET not in material.sealed
    assert material.kms_key_id == "memory:1"
    assert await unsealed(kms, material, context) == SECRET
    again = await sealed(kms, SECRET, context)
    assert again.wrapped_data_key != material.wrapped_data_key  # one data key per seal
