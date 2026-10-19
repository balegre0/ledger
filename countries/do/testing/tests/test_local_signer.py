from datetime import UTC, datetime
from typing import Final

import pytest
from cryptography.hazmat.primitives.serialization import BestAvailableEncryption, pkcs12

from ledger_country_do.channels.ecf_v1.adapters.signing.seeds import SeedMalformedError
from ledger_country_do.channels.ecf_v1.adapters.signing.verifier import verify_enveloped
from ledger_country_do.channels.ecf_v1.application.ports import DocumentUnsignableError
from ledger_country_do_testing.local_signer import BundleIncompleteError, KeyPairSigner
from ledger_country_do_testing.pki import person
from ledger_country_do_testing.signatures import SHAPES
from ledger_fiscal_kit.xml.dsig import NotAnRsaKeyError, SigningPairError
from ledger_kernel.errors import InvalidValueError
from ledger_testing.pki import OPENING_PHRASE, Authority, bundle, ec_key, rsa_key

SIGNED_AT: Final = datetime(2026, 10, 13, 13, 30, tzinfo=UTC)
AUDIENCE: Final = "ecf.dgii.gov.do"


async def test_a_bundle_opens_into_a_signer_of_documents_and_seeds() -> None:
    issued = Authority.root().issue(person())
    signer = KeyPairSigner.from_pkcs12(bundle(issued), OPENING_PHRASE.encode())
    assert signer.certificate == issued.certificate
    document = await signer.sign_document(SHAPES["ecf"], signed_at=SIGNED_AT, reference="E310000000001")
    assert verify_enveloped(document).certificate == issued.certificate
    seed = await signer.sign_seed(SHAPES["seed"], audience=AUDIENCE)
    assert verify_enveloped(seed).certificate == issued.certificate


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


def test_a_pair_that_cannot_sign_makes_no_signer() -> None:
    # Refused when built, as the signer refuses it at import: signing never fails for the pair's sake.
    issued = Authority.root().issue(person())
    with pytest.raises(NotAnRsaKeyError):
        KeyPairSigner(ec_key(), issued.certificate)
    with pytest.raises(SigningPairError, match="not the signing key's"):
        KeyPairSigner(rsa_key(), issued.certificate)


@pytest.mark.parametrize(
    "seed", [SHAPES["ecf"], b"<a>", b"<SemillaModel/>"], ids=["an-e-cf", "unreadable", "empty-seed"]
)
async def test_what_is_not_dgii_s_seed_is_never_signed(seed: bytes) -> None:
    signer = KeyPairSigner.from_issued(Authority.root().issue(person()))
    with pytest.raises(SeedMalformedError):
        await signer.sign_seed(seed, audience=AUDIENCE)


@pytest.mark.parametrize(
    ("document", "reason"),
    [
        (b"<a>", "xml_document_malformed"),
        (b"<!-- c --><a/>", "xml_document_unsignable"),
        (
            SHAPES["ecf"].replace(
                b"</ECF>", b'<Signature xmlns="http://www.w3.org/2000/09/xmldsig#"/></ECF>'
            ),
            "xml_document_signed",
        ),
    ],
    ids=["unreadable", "comment", "signed"],
)
async def test_a_document_the_profile_refuses_is_refused_as_the_signer_refuses_it(
    document: bytes, reason: str
) -> None:
    signer = KeyPairSigner.from_issued(Authority.root().issue(person()))
    with pytest.raises(DocumentUnsignableError) as caught:
        await signer.sign_document(document, signed_at=SIGNED_AT, reference="E310000000001")
    assert caught.value.reason == reason


async def test_the_test_signer_holds_requests_to_the_port_s_limits() -> None:
    # It builds the port's requests, so what the signer would refuse the test signer refuses too.
    signer = KeyPairSigner.from_issued(Authority.root().issue(person()))
    with pytest.raises(InvalidValueError, match=r"xml_to_sign.signed_at must be an aware datetime"):
        await signer.sign_document(SHAPES["ecf"], signed_at=SIGNED_AT.replace(tzinfo=None), reference="E31")
    with pytest.raises(InvalidValueError, match=r"xml_to_sign.reference must be"):
        await signer.sign_document(SHAPES["ecf"], signed_at=SIGNED_AT, reference="juan@example.com")
    with pytest.raises(InvalidValueError, match=r"seed_to_sign.audience must be"):
        await signer.sign_seed(SHAPES["seed"], audience="https://ecf.dgii.gov.do/x")
