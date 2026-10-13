from datetime import UTC, datetime, timedelta

from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePrivateKey
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID

from ledger_country_do_testing.pki import (
    OPENING_PHRASE,
    Authority,
    bundle,
    ec_key,
    organization,
    pem,
    person,
)
from ledger_country_do_testing.samples import SIGNER_VALID_FROM
lazy from ledger_country_do_testing.pki import Issued


def test_subjects_follow_the_dominican_conventions() -> None:
    serial = person("40212345678").get_attributes_for_oid(NameOID.SERIAL_NUMBER)[0].value
    vat = organization("130000001").get_attributes_for_oid(NameOID.ORGANIZATION_IDENTIFIER)[0].value
    assert (serial, vat) == ("IDCDO-40212345678", "VATDO-130000001")


def test_a_hierarchy_issues_certificates_that_bundle_and_open_again() -> None:
    root = Authority.root()
    intermediate = root.intermediate()
    issued = intermediate.issue(person())
    loaded = pkcs12.load_pkcs12(bundle(issued, chain=[intermediate.certificate]), OPENING_PHRASE.encode())
    assert loaded.cert is not None
    assert loaded.cert.certificate == issued.certificate
    assert [extra.certificate for extra in loaded.additional_certs] == [intermediate.certificate]
    assert issued.certificate.issuer == intermediate.certificate.subject
    assert pem(root.certificate).startswith(b"-----BEGIN CERTIFICATE-----")
    assert issued.pem() == pem(issued.certificate)
    assert pkcs12.load_pkcs12(bundle(issued), OPENING_PHRASE.encode()).additional_certs == []


def test_a_test_breaks_what_it_needs() -> None:
    root = Authority.root(now=datetime.now(UTC))
    past = datetime.now(UTC) - timedelta(days=10)
    issued = root.issue(organization(), key=ec_key(), not_before=past - timedelta(days=1), not_after=past)
    assert isinstance(issued.key, EllipticCurvePrivateKey)
    assert issued.certificate.not_valid_after_utc == past.replace(microsecond=0)


def test_the_fixture_hierarchy_is_dated_from_a_fixed_moment_not_from_today(
    authority: Authority, issued: Issued, receiver_identity: Issued
) -> None:
    for certificate in (authority.certificate, issued.certificate, receiver_identity.certificate):
        assert certificate.not_valid_before_utc <= SIGNER_VALID_FROM
