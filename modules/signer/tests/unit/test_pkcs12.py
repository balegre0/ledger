"""Opening a PKCS#12 bundle: its RSA key and certificate, or one answer for every bundle that does not open.

A wrong password and bytes that are no bundle are refused alike, with no word of the library's (it may say
which); a bundle without an RSA key and its certificate is incomplete; one over 2 MiB is refused unread.
"""

from typing import Final

import pytest
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import NoEncryption, pkcs12
from cryptography.x509.oid import NameOID

from ledger_signer.adapters.custody.pkcs12 import MAX_BUNDLE_BYTES, open_bundle
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
