from datetime import UTC, datetime
from decimal import Decimal

import pytest

from ledger_country_do.channels.ecf_v1.adapters.signing.signer import sign_enveloped
from ledger_country_do.channels.ecf_v1.adapters.signing.stamp import (
    SignatureValueMissingError,
    signature_value,
)
from ledger_country_do.channels.ecf_v1.adapters.signing.verifier import verify_enveloped
from ledger_country_do.channels.ecf_v1.domain.encf import EcfType, Encf
from ledger_country_do.channels.ecf_v1.domain.environments import Environment
from ledger_country_do.channels.ecf_v1.domain.printed import (
    EcfQrData,
    QrHosts,
    RfceQrData,
    SecurityCodeError,
    qr_url,
    rfce_qr_url,
    security_code,
)
from ledger_country_do_testing.samples import ISSUED_ON, SIGNED_AT
from ledger_country_do_testing.signatures import SHAPES
lazy from ledger_country_do_testing.pki import Issued


def test_the_security_code_is_the_signature_value_s_first_six_characters() -> None:
    assert security_code("dcp79qAbCdEf==") == "dcp79q"
    assert security_code(" dc\np7\t9q rest") == "dcp79q"
    with pytest.raises(SecurityCodeError):
        security_code("abc de")


def test_the_qr_of_an_e_cf_names_dgii_s_consultatimbre_with_it_s_seven_parameters() -> None:
    data = EcfQrData(
        issuer_rnc="101850043",
        buyer_rnc="131098193",
        encf=Encf(EcfType.CREDIT_INVOICE, 1),
        issued_on=ISSUED_ON,
        total=Decimal("4887.58"),
        signed_at=SIGNED_AT,
        security_code="dcp79q",
    )
    url = qr_url(data, environment=Environment.TESTECF)
    assert url == (
        "https://ecf.dgii.gov.do/testecf/consultatimbre?RncEmisor=101850043&RncComprador=131098193"
        "&ENCF=E310000000001&FechaEmision=13-10-2026&MontoTotal=4887.58&FechaFirma=13-10-2026%2009:30:00"
        "&CodigoSeguridad=dcp79q"
    )


def test_without_a_buyer_rnc_the_qr_leaves_it_out_and_base64_is_encoded() -> None:
    data = EcfQrData(
        issuer_rnc="101850043",
        buyer_rnc=None,
        encf=Encf(EcfType.CONSUMER_INVOICE, 2),
        issued_on=ISSUED_ON,
        total=Decimal(295000),
        signed_at=datetime(2026, 10, 13, 13, 30, tzinfo=UTC),
        security_code="ab+/c=",
    )
    hosts = QrHosts(ecf="http://127.0.0.1:8471", fc="http://127.0.0.1:8471")
    url = qr_url(data, environment=Environment.CERTECF, hosts=hosts)
    assert url.startswith(
        "http://127.0.0.1:8471/certecf/consultatimbre?RncEmisor=101850043&ENCF=E320000000002"
    )
    assert "RncComprador" not in url
    assert url.endswith("MontoTotal=295000.00&FechaFirma=13-10-2026%2009:30:00&CodigoSeguridad=ab%2B%2Fc%3D")


def test_the_qr_of_an_rfce_names_consultatimbrefc_on_dgii_s_fc_host() -> None:
    data = RfceQrData(
        issuer_rnc="101850043",
        encf=Encf(EcfType.CONSUMER_INVOICE, 1),
        total=Decimal("1180.005"),
        security_code="dcp79q",
    )
    url = rfce_qr_url(data, environment=Environment.TESTECF)
    assert url == (
        "https://fc.dgii.gov.do/testecf/consultatimbrefc?RncEmisor=101850043&ENCF=E320000000001"
        "&MontoTotal=1180.01&CodigoSeguridad=dcp79q"
    )


def test_the_signature_value_is_read_from_the_signed_document(issued: Issued) -> None:
    signed = sign_enveloped(SHAPES["ecf"], issued.key, issued.certificate)
    verified = verify_enveloped(signed).signature_value
    assert signature_value(signed) == verified
    assert security_code(signature_value(signed)) == verified[:6]
    with pytest.raises(SignatureValueMissingError):
        signature_value(SHAPES["ecf"])
    dsig = "http://www.w3.org/2000/09/xmldsig#"
    empty = f'<a><Signature xmlns="{dsig}"><SignatureValue> </SignatureValue></Signature></a>'.encode()
    with pytest.raises(SignatureValueMissingError):
        signature_value(empty)
    nested = f'<a><b><Signature xmlns="{dsig}"><SignatureValue>dcp79q</SignatureValue></Signature></b></a>'
    with pytest.raises(SignatureValueMissingError):
        signature_value(nested.encode())


def test_only_ecf_is_live() -> None:
    assert [environment.live for environment in Environment] == [False, False, True]
