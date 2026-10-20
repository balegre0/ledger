"""Opening a PKCS#12 bundle: its RSA key and certificate, or one answer for every bundle that does not open.

A wrong password and bytes that are no bundle are refused alike, with no word of the library's (it may say
which); a bundle without an RSA key and its certificate is incomplete; one over 2 MiB is refused unread.
"""

from typing import Final

import pytest
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import NoEncryption, PrivateFormat, pkcs12
from cryptography.x509.oid import NameOID

from ledger_signer.adapters.custody.pkcs12 import MAX_ITERATIONS, iteration_counts, open_bundle
from ledger_signer.application.importing import MAX_BUNDLE_BYTES
from ledger_signer.application.ports import BundleIncompleteError, BundleUnreadableError, Secret
from ledger_testing.pki import OPENING_PHRASE, bundle, distinguished_name, ec_key
lazy from ledger_testing.pki import Authority, Issued

PASSWORD: Final = "PASSWORD-SENTINEL"


def test_a_bundle_opens_with_its_password(holder: Issued) -> None:
    key, certificate = open_bundle(bundle(holder, password=PASSWORD), Secret(PASSWORD))
    own = holder.key
    assert isinstance(own, rsa.RSAPrivateKey)
    assert key.private_numbers() == own.private_numbers()
    assert certificate == holder.certificate


def test_a_bundle_without_a_password_opens_with_an_empty_one(holder: Issued) -> None:
    plain = pkcs12.serialize_key_and_certificates(
        b"plain", holder.key, holder.certificate, None, NoEncryption()
    )
    _, certificate = open_bundle(plain, Secret(""))
    assert certificate == holder.certificate


def unreadable(kind: str, issued: Issued) -> tuple[bytes, str]:
    """A bundle that does not open, and the password it is given."""
    match kind:
        case "wrong password":
            return bundle(issued), "not its password"
        case "garbage":
            return b"PASSWORD-SENTINEL is not a bundle", OPENING_PHRASE
        case "nothing":
            return b"", OPENING_PHRASE
        case "cut short":
            return bundle(issued)[:-8], OPENING_PHRASE
        case _:
            assert kind == "over 2 MiB"
            return bytes(MAX_BUNDLE_BYTES + 1), OPENING_PHRASE


@pytest.mark.parametrize("kind", ["wrong password", "garbage", "nothing", "cut short", "over 2 MiB"])
def test_every_bundle_that_does_not_open_gets_one_answer(holder: Issued, kind: str) -> None:
    content, password = unreadable(kind, holder)
    with pytest.raises(BundleUnreadableError) as caught:
        open_bundle(content, Secret(password))
    error = caught.value
    assert error.code == "certificate_bundle_unreadable"
    assert error.__cause__ is None  # the library's message never travels
    assert error.__context__ is None or error.__suppress_context__
    assert "PASSWORD-SENTINEL" not in str(error)
    assert "PKCS12" not in str(error)


def test_a_bundle_of_another_kind_of_key_is_incomplete(authority: Authority) -> None:
    key = ec_key()
    issued = authority.issue(distinguished_name((NameOID.COMMON_NAME, "Elliptic")), key=key)
    with pytest.raises(BundleIncompleteError):
        open_bundle(bundle(issued), Secret(OPENING_PHRASE))


@pytest.mark.parametrize("missing", ["key", "certificate"])
def test_a_bundle_without_its_key_or_certificate_is_incomplete(holder: Issued, missing: str) -> None:
    key = None if missing == "key" else holder.key
    certificate = None if missing == "certificate" else holder.certificate
    content = pkcs12.serialize_key_and_certificates(b"partial", key, certificate, None, NoEncryption())
    with pytest.raises(BundleIncompleteError) as caught:
        open_bundle(content, Secret(""))
    assert caught.value.code == "certificate_bundle_incomplete"


def test_a_bundle_sealed_with_an_algorithm_the_library_lacks_gets_the_same_answer(
    holder: Issued, monkeypatch: pytest.MonkeyPatch
) -> None:
    message = f"{PASSWORD}: an algorithm the library lacks"

    def unsupported(data: bytes, password: bytes | None) -> pkcs12.PKCS12KeyAndCertificates:
        del data, password
        raise UnsupportedAlgorithm(message)

    monkeypatch.setattr(pkcs12, "load_pkcs12", unsupported)
    with pytest.raises(BundleUnreadableError) as caught:
        open_bundle(bundle(holder), Secret(PASSWORD))
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__


def sealed_with(issued: Issued, rounds: int, algorithm: pkcs12.PBES) -> bytes:
    """``issued`` as a bundle whose key and certificate are encrypted with ``rounds`` of ``algorithm``."""
    encryption = (
        PrivateFormat.PKCS12.encryption_builder()
        .kdf_rounds(rounds)
        .key_cert_algorithm(algorithm)
        .hmac_hash(hashes.SHA256())
        .build(PASSWORD.encode())
    )
    return pkcs12.serialize_key_and_certificates(b"rounds", issued.key, issued.certificate, None, encryption)


def tlv(tag: int, *parts: bytes) -> bytes:
    """A DER element of ``tag`` holding ``parts``."""
    content = b"".join(parts)
    size = len(content)
    if size < 0x80:
        return bytes((tag, size)) + content
    length = size.to_bytes((size.bit_length() + 7) // 8, "big")
    return bytes((tag, 0x80 | len(length))) + length + content


def as_ber(der: bytes) -> bytes:
    """``der`` as BER: each constructed element of indefinite length, each long octet string in two chunks."""
    out = bytearray()
    cursor = 0
    while cursor < len(der):
        tag, first = der[cursor], der[cursor + 1]
        if first < 0x80:
            size, start = first, cursor + 2
        else:
            count = first & 0x7F
            size, start = int.from_bytes(der[cursor + 2 : cursor + 2 + count], "big"), cursor + 2 + count
        content = der[start : start + size]
        if tag & 0x20:
            out += bytes((tag, 0x80)) + as_ber(content) + b"\x00\x00"
        elif tag == 0x04 and size > 64:
            half = size // 2
            out += b"\x24\x80" + tlv(0x04, content[:half]) + tlv(0x04, content[half:]) + b"\x00\x00"
        else:
            out += der[cursor : start + size]
        cursor = start + size
    return bytes(out)


@pytest.mark.parametrize(
    "algorithm",
    [pkcs12.PBES.PBESv2SHA256AndAES256CBC, pkcs12.PBES.PBESv1SHA1And3KeyTripleDESCBC],
    ids=["PBES2", "PKCS#12 PBE"],
)
def test_a_bundle_s_iteration_counts_are_read_before_it_is_opened(
    holder: Issued, algorithm: pkcs12.PBES
) -> None:
    sealed = sealed_with(holder, 3000, algorithm)
    assert sorted(iteration_counts(sealed)) == [2048, 3000, 3000]  # the MAC, the certificate, the key
    assert sorted(iteration_counts(as_ber(sealed))) == [2048, 3000, 3000]
    key, _ = open_bundle(sealed, Secret(PASSWORD))
    assert isinstance(key, rsa.RSAPrivateKey)


def test_a_bundle_in_the_clear_names_its_mac_s_count_alone(holder: Issued) -> None:
    plain = pkcs12.serialize_key_and_certificates(
        b"plain", holder.key, holder.certificate, None, NoEncryption()
    )
    assert iteration_counts(plain) == (2048,)


@pytest.fixture(scope="module")
def costly(holder: Issued) -> bytes:
    """A bundle whose key derivation takes one round more than the ceiling."""
    return sealed_with(holder, MAX_ITERATIONS + 1, pkcs12.PBES.PBESv2SHA256AndAES256CBC)


def test_a_bundle_over_the_iteration_ceiling_is_refused_unopened(
    costly: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[bytes] = []

    def spy(data: bytes, password: bytes | None) -> pkcs12.PKCS12KeyAndCertificates:
        del password
        opened.append(data)
        raise AssertionError

    monkeypatch.setattr(pkcs12, "load_pkcs12", spy)
    assert max(iteration_counts(costly)) == MAX_ITERATIONS + 1
    with pytest.raises(BundleUnreadableError) as caught:
        open_bundle(costly, Secret(PASSWORD))
    assert caught.value.__cause__ is None
    assert opened == []


INTEGER_7: Final = tlv(0x02, b"\x07")
SALT: Final = tlv(0x04, b"salt")
PBKDF2: Final = tlv(0x06, bytes.fromhex("2a864886f70d01050c"))
DATA: Final = tlv(0x06, bytes.fromhex("2a864886f70d010701"))
DIGEST: Final = tlv(0x30, tlv(0x30, tlv(0x06, bytes.fromhex("608648016503040201"))), tlv(0x04, bytes(32)))


def pfx(*safes: bytes, mac: bytes = b"") -> bytes:
    """A PFX of ``safes`` in a clear content, with ``mac`` (its MacData's content) when given."""
    content_info = tlv(0x30, DATA, tlv(0xA0, tlv(0x04, tlv(0x30, *safes))))
    return tlv(0x30, tlv(0x02, b"\x03"), content_info, tlv(0x30, mac) if mac else b"")


@pytest.mark.parametrize(
    ("structure", "counts"),
    [
        (pfx(tlv(0x30, PBKDF2, tlv(0x30, SALT, INTEGER_7))), (7,)),
        (pfx(tlv(0x30, PBKDF2)), ()),
        (pfx(tlv(0x30, DATA, tlv(0x30))), ()),
        (pfx(mac=DIGEST + SALT + INTEGER_7), (7,)),
        (pfx(mac=DIGEST + SALT), ()),
        (tlv(0x30, tlv(0x02, b"\x03")), ()),
    ],
    ids=["a scheme", "no parameters", "another content", "a mac", "a mac of one iteration", "no content"],
)
def test_what_names_a_count_and_what_does_not(structure: bytes, counts: tuple[int, ...]) -> None:
    assert iteration_counts(structure) == counts


@pytest.mark.parametrize(
    "malformed",
    [
        b"",
        tlv(0x02, b"\x01"),
        b"\x30",
        b"\x30\x05\x02\x01",
        b"\x30\x85\x00\x00\x00\x00\x01\x00",
        b"\x30\x84\xff\xff\xff\xff",
        b"\x30\x80\x02\x01\x01",
        b"\x04\x80\x00\x00",
        b"\x30\x80" * 40 + b"\x00\x00" * 40,
        pfx(tlv(0x30, PBKDF2, tlv(0x30, SALT))),
        pfx(tlv(0x30, PBKDF2, tlv(0x31, SALT, INTEGER_7))),
        pfx(tlv(0x30, PBKDF2, tlv(0x30, SALT, tlv(0x02, b"")))),
        pfx(tlv(0x30, PBKDF2, tlv(0x30, SALT, tlv(0x02, b"\x00")))),
        pfx(tlv(0x30, PBKDF2, tlv(0x30, SALT, SALT))),
        pfx(tlv(0x30, DATA, tlv(0xA0))),
        pfx(tlv(0x30, DATA, tlv(0xA0, INTEGER_7))),
        pfx(tlv(0x30, DATA, tlv(0xA0, SALT, SALT))),
        pfx(mac=DIGEST + SALT + tlv(0x02, b"\xff")),
    ],
    ids=[
        "nothing",
        "no sequence",
        "no length",
        "cut short",
        "a length of five bytes",
        "a length past the end",
        "no end of contents",
        "an indefinite primitive",
        "too deep",
        "parameters without a count",
        "parameters in a set",
        "an empty count",
        "a count of zero",
        "a count that is no integer",
        "a clear content of nothing",
        "a clear content that is no string",
        "a clear content of two strings",
        "a negative mac count",
    ],
)
def test_a_structure_that_is_not_a_bundle_s_is_unreadable(malformed: bytes) -> None:
    with pytest.raises(BundleUnreadableError):
        iteration_counts(malformed)
