from cryptography.x509.oid import NameOID

from ledger_country_do_testing.pki import TAX_PROCEDURES, organization, person
from ledger_country_do_testing.samples import SIGNER_VALID_FROM
lazy from ledger_testing.pki import Authority, Issued


def test_subjects_follow_the_dominican_conventions() -> None:
    serial = person("40212345678").get_attributes_for_oid(NameOID.SERIAL_NUMBER)[0].value
    vat = organization("130000001").get_attributes_for_oid(NameOID.ORGANIZATION_IDENTIFIER)[0].value
    assert (serial, vat) == ("IDCDO-40212345678", "VATDO-130000001")
    assert person().get_attributes_for_oid(NameOID.DN_QUALIFIER)[0].value == TAX_PROCEDURES
    assert person(name="CHER").get_attributes_for_oid(NameOID.SURNAME)[0].value == "CHER"


def test_the_fixture_hierarchy_is_dated_from_a_fixed_moment_not_from_today(
    authority: Authority, issued: Issued, receiver_identity: Issued
) -> None:
    for certificate in (authority.certificate, issued.certificate, receiver_identity.certificate):
        assert certificate.not_valid_before_utc <= SIGNER_VALID_FROM
