"""Subjects as the Dominican certification authorities name them, for certificates of ``ledger_testing.pki``.

A natural person's tax-procedure certificate names its cédula in ``serialNumber`` (``IDCDO-<cédula>``), an
organization's seal its RNC in ``organizationIdentifier`` (``VATDO-<rnc>``). DGII's TesteCF takes only
certificates of authorized entities: these serve tests and the fake DGII, never DGII.
"""

from typing import Final

from cryptography.x509.oid import NameOID
lazy from cryptography import x509

from ledger_testing.pki import distinguished_name

TAX_PROCEDURES: Final = "QUALIFIED CERTIFICATE FOR NATURAL PERSON - TAX PROCEDURES"


def person(cedula: str = "40212345678", *, name: str = "JUAN PEREZ") -> x509.Name:
    """A natural person's certificate for tax procedures: ``serialNumber`` ``IDCDO-<cedula>``."""
    given, _, family = name.partition(" ")
    return distinguished_name(
        (NameOID.COUNTRY_NAME, "DO"),
        (NameOID.DN_QUALIFIER, TAX_PROCEDURES),
        (NameOID.COMMON_NAME, name),
        (NameOID.SERIAL_NUMBER, f"IDCDO-{cedula}"),
        (NameOID.GIVEN_NAME, given),
        (NameOID.SURNAME, family or given),
    )


def organization(rnc: str = "101850043", *, name: str = "FERRETERIA DUARTE SRL") -> x509.Name:
    """An organization's electronic seal: ``organizationIdentifier`` ``VATDO-<rnc>``."""
    return distinguished_name(
        (NameOID.COUNTRY_NAME, "DO"),
        (NameOID.ORGANIZATION_NAME, name),
        (NameOID.COMMON_NAME, name),
        (NameOID.ORGANIZATION_IDENTIFIER, f"VATDO-{rnc}"),
    )
