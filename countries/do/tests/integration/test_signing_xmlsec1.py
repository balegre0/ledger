import base64
from datetime import UTC, datetime
lazy from collections.abc import Iterator

import pytest
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from cryptography.hazmat.primitives.serialization import Encoding

from ledger_country_do.channels.ecf_v1.adapters.signing.verifier import INBOUND, verify_enveloped
from ledger_country_do_testing.signatures import SHAPES, forge, sign_with_signxml
from ledger_country_do_testing.xmlsec import Xmlsec
from ledger_fiscal_kit.xml.dsig import sign_enveloped
from ledger_testing.pki import Authority, pem
lazy from ledger_testing.pki import Issued


@pytest.fixture(scope="module")
def xmlsec() -> Iterator[Xmlsec]:
    with Xmlsec.started() as started:
        yield started


@pytest.mark.parametrize("shape", list(SHAPES))
def test_every_shape_we_sign_verifies_in_xmlsec1_with_its_chain(
    xmlsec: Xmlsec, authority: Authority, intermediate: Authority, issued: Issued, shape: str
) -> None:
    signed = sign_enveloped(SHAPES[shape], issued.key, issued.certificate)
    verdict = xmlsec.verify(
        signed,
        trusted=pem(authority.certificate),
        untrusted=pem(intermediate.certificate),
        at=datetime.now(UTC),
    )
    assert verdict.verified, verdict.output
    assert "SignedInfo References (ok/all): 1/1" in verdict.output


def test_xmlsec1_verifies_what_signxml_signs(
    xmlsec: Xmlsec, authority: Authority, intermediate: Authority, issued: Issued
) -> None:
    signed = sign_with_signxml(SHAPES["ecf"], issued)
    verdict = xmlsec.verify(
        signed,
        trusted=pem(authority.certificate),
        untrusted=pem(intermediate.certificate),
        at=datetime.now(UTC),
    )
    assert verdict.verified, verdict.output
    assert "SignedInfo References (ok/all): 1/1" in verdict.output


def test_xmlsec1_refuses_a_tampered_one(
    xmlsec: Xmlsec, authority: Authority, intermediate: Authority, issued: Issued
) -> None:
    signed = sign_enveloped(SHAPES["ecf"], issued.key, issued.certificate).replace(b"101850043", b"101850044")
    verdict = xmlsec.verify(
        signed,
        trusted=pem(authority.certificate),
        untrusted=pem(intermediate.certificate),
        at=datetime.now(UTC),
    )
    assert not verdict.verified
    assert "data and digest do not match" in verdict.output


def test_xmlsec1_refuses_a_signer_outside_the_chain(
    xmlsec: Xmlsec, intermediate: Authority, issued: Issued
) -> None:
    signed = sign_enveloped(SHAPES["ecf"], issued.key, issued.certificate)
    stranger = Authority.root()
    verdict = xmlsec.verify(
        signed,
        trusted=pem(stranger.certificate),
        untrusted=pem(intermediate.certificate),
        at=datetime.now(UTC),
    )
    assert not verdict.verified
    assert "SignedInfo References (ok/all): 0/0" in verdict.output


def test_xmlsec1_carries_the_root_s_xml_attributes_as_the_inbound_profile_does(
    xmlsec: Xmlsec, authority: Authority, intermediate: Authority, issued: Issued
) -> None:
    assert isinstance(issued.key, RSAPrivateKey)
    document = b'<r xmlns:xsi="urn:xsi" xml:lang="es-DO" xml:space="preserve"><a xml:lang="en"> t </a></r>'
    signed = forge(document, issued.key, issued.certificate)
    verdict = xmlsec.verify(
        signed,
        trusted=pem(authority.certificate),
        untrusted=pem(intermediate.certificate),
        at=datetime.now(UTC),
    )
    assert verdict.verified, verdict.output
    assert verify_enveloped(signed, INBOUND).certificate == issued.certificate


def test_xmlsec1_verifies_what_the_inbound_profile_tolerates_with_the_chain_it_carries(
    xmlsec: Xmlsec, authority: Authority, intermediate: Authority, issued: Issued
) -> None:
    assert isinstance(issued.key, RSAPrivateKey)
    chain = base64.b64encode(intermediate.certificate.public_bytes(Encoding.DER)).decode()

    def edit(text: str) -> str:
        return (
            text.replace("<Signature ", '<Signature Id="s" ')
            .replace("<SignedInfo>", '<SignedInfo Id="i">')
            .replace('<Reference URI=""', '<Reference Id="r" URI=""')
            .replace("<KeyInfo>", '<KeyInfo Id="k"><KeyName>FERRETERIA</KeyName>')
            .replace("<X509Data>", f"<X509Data><X509Certificate>{chain}</X509Certificate>")
        )

    signed = forge(SHAPES["ecf"], issued.key, issued.certificate, edit=edit)
    verdict = xmlsec.verify(signed, trusted=pem(authority.certificate), untrusted=b"", at=datetime.now(UTC))
    assert verdict.verified, verdict.output
    assert verify_enveloped(signed, INBOUND).intermediates == (intermediate.certificate,)


def test_a_document_over_128_kib_reaches_xmlsec1_whole(
    xmlsec: Xmlsec, authority: Authority, intermediate: Authority, issued: Issued
) -> None:
    lines = "".join(f"<Item><NombreItem>Martillo {index}</NombreItem></Item>" for index in range(6000))
    document = f"<ECF><DetallesItems>{lines}</DetallesItems></ECF>".encode()
    assert len(document) > 256 * 1024
    signed = sign_enveloped(document, issued.key, issued.certificate)
    verdict = xmlsec.verify(
        signed,
        trusted=pem(authority.certificate),
        untrusted=pem(intermediate.certificate),
        at=datetime.now(UTC),
    )
    assert verdict.verified, verdict.output
    tampered = xmlsec.verify(
        signed.replace(b"Martillo 5999", b"Martillo 5998"),
        trusted=pem(authority.certificate),
        untrusted=pem(intermediate.certificate),
        at=datetime.now(UTC),
    )
    assert "data and digest do not match" in tampered.output
