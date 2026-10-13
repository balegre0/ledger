import pytest
from cryptography import x509
from cryptography.x509.oid import NameOID

from ledger_country_do.channels.ecf_v1.adapters.signing.names import name_pairs, names_agree, parsed_name

NAME = x509.Name(
    [
        x509.NameAttribute(NameOID.COUNTRY_NAME, "DO"),
        x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "Santo Domingo"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "FERRETERIA DUARTE, SRL"),
        x509.NameAttribute(NameOID.COMMON_NAME, "JUAN  PEREZ"),
        x509.NameAttribute(NameOID.SERIAL_NUMBER, "IDCDO-40212345678"),
        x509.NameAttribute(NameOID.EMAIL_ADDRESS, "juan@example.do"),
        x509.NameAttribute(NameOID.ORGANIZATION_IDENTIFIER, "VATDO-101850043"),
    ]
)


_DOTNET_PARTS = (
    "OID.2.5.4.97=VATDO-101850043",
    "E=juan@example.do",
    "SERIALNUMBER=IDCDO-40212345678",
    "CN=JUAN PEREZ",
    'O="FERRETERIA DUARTE, SRL"',
    "S=Santo Domingo",
    "C=DO",
)
DOTNET = ", ".join(_DOTNET_PARTS)
""".NET: most specific first, ", " between parts, E= and SERIALNUMBER=, S= for the state, OID. prefix."""
_JAVA_PARTS = (
    "OID.2.5.4.97=VATDO-101850043",
    "EMAILADDRESS=juan@example.do",
    "OID.2.5.4.5=IDCDO-40212345678",
    "cn=juan perez",
    r"o=FERRETERIA DUARTE\, SRL",
    "st=Santo Domingo",
    "c=DO",
)
JAVA = ",".join(_JAVA_PARTS)
"""Java: OID.x.y.z for what it has no label for, EMAILADDRESS, ST, lower-case labels."""
_SPACED_PARTS = (
    "2.5.4.97=VATDO-101850043",
    "email=juan@example.do ; serialNumber=IDCDO-40212345678",
    "CN = JUAN PEREZ",
    r"O=FERRETERIA DUARTE\2C SRL",
    "ST=Santo Domingo",
    "C=DO",
)
SPACED = " , ".join(_SPACED_PARTS)
"""Spaces around separators and equals signs, a semicolon, a hexadecimal escape."""


@pytest.mark.parametrize(
    "text", [NAME.rfc4514_string(), DOTNET, JAVA, SPACED], ids=["rfc4514", "dotnet", "java", "spaced"]
)
def test_a_name_written_by_any_common_tool_agrees_with_the_certificate_s(text: str) -> None:
    assert parsed_name(text) == name_pairs(NAME)
    assert names_agree(text, NAME)


@pytest.mark.parametrize(
    "text",
    [
        "CN=JUAN PEREZ, C=DO",
        "CN=SOMEONE ELSE, O=FERRETERIA DUARTE\\, SRL, C=DO",
        NAME.rfc4514_string() + ",L=X",
    ],
)
def test_a_name_that_names_another_does_not_agree(text: str) -> None:
    assert not names_agree(text, NAME)


@pytest.mark.parametrize(
    "text",
    [
        "not a name",
        "",
        "CN=#0403414243",
        "FOO=bar",
        "CN=a\\",
        "CN=a\\2",
        'CN="open',
        "=x",
        "CN=a,,C=DO",
        r"CN=\C3",
    ],
)
def test_a_name_that_cannot_be_read_is_skipped_never_matched(text: str) -> None:
    assert parsed_name(text) is None
    assert names_agree(text, NAME)


def test_multi_valued_parts_and_hex_escapes_are_read() -> None:
    name = x509.Name(
        [
            x509.RelativeDistinguishedName(
                [x509.NameAttribute(NameOID.COMMON_NAME, "A+B"), x509.NameAttribute(NameOID.SURNAME, "Ñ")]
            )
        ]
    )
    assert parsed_name(r"CN=A\+B+SN=\C3\91") == name_pairs(name)
