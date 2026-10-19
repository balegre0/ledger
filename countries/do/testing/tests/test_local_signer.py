from datetime import timedelta

import pytest
from cryptography.hazmat.primitives.serialization import BestAvailableEncryption, pkcs12

from ledger_country_do.channels.ecf_v1.adapters.signing.verifier import verify_enveloped
from ledger_country_do_testing.local_signer import BundleIncompleteError, KeyPairSigner
from ledger_country_do_testing.pki import person
from ledger_country_do_testing.signatures import SHAPES
from ledger_testing.pki import OPENING_PHRASE, Authority, bundle


async def test_a_bundle_opens_into_a_signer_of_documents_and_seeds() -> None:
    issued = Authority.root().issue(person())
    signer = KeyPairSigner.from_pkcs12(bundle(issued), OPENING_PHRASE.encode())
    assert signer.certificate == issued.certificate
    assert verify_enveloped(await signer.sign_document(SHAPES["ecf"])).certificate == issued.certificate
    assert verify_enveloped(await signer.sign_seed(SHAPES["seed"])).certificate == issued.certificate


def test_a_test_certificate_makes_a_signer() -> None:
    issued = Authority.root().issue(person())
    assert KeyPairSigner.from_issued(issued).certificate == issued.certificate


def test_a_bundle_without_its_key_signs_nothing() -> None:
    issued = Authority.root().issue(person())
    keyless = pkcs12.serialize_key_and_certificates(
        b"x", None, issued.certificate, None, BestAvailableEncryption(OPENING_PHRASE.encode())
    )
    with pytest.raises(BundleIncompleteError, match="holds no private key and certificate pair"):
        KeyPairSigner.from_pkcs12(keyless, OPENING_PHRASE.encode())


def test_a_bundle_without_its_certificate_signs_nothing() -> None:
    issued = Authority.root().issue(person())
    certless = pkcs12.serialize_key_and_certificates(
        b"x", issued.key, None, None, BestAvailableEncryption(OPENING_PHRASE.encode())
    )
    with pytest.raises(BundleIncompleteError):
        KeyPairSigner.from_pkcs12(certless, OPENING_PHRASE.encode())


def test_a_wrong_password_opens_nothing_and_is_not_echoed() -> None:
    issued = Authority.root().issue(person())
    with pytest.raises(ValueError, match="password") as caught:
        KeyPairSigner.from_pkcs12(bundle(issued), b"not the password")
    assert "not the password" not in str(caught.value)


def test_the_key_is_never_shown() -> None:
    issued = Authority.root().issue(person())
    signer = KeyPairSigner.from_issued(issued)
    assert "PRIVATE" not in repr(signer)


async def test_the_signer_s_validity_is_its_certificate_s() -> None:
    issued = Authority.root().issue(person())
    validity = await KeyPairSigner.from_issued(issued).certificate_validity()
    assert (validity.not_before, validity.not_after) == (
        issued.certificate.not_valid_before_utc,
        issued.certificate.not_valid_after_utc,
    )
    assert validity.covers(validity.not_before)
    assert validity.covers(validity.not_after)
    assert not validity.covers(validity.not_after + timedelta(microseconds=1))
