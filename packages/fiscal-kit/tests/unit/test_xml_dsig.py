import base64
import hashlib
import re
from datetime import UTC, datetime, timedelta
from typing import Final
lazy from collections.abc import Callable

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, utils
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.x509.oid import NameOID
from lxml import etree

from ledger_fiscal_kit.xml.dsig import (
    C14N,
    CERTIFICATION_AUTHORITY,
    ENVELOPED,
    NOT_FOR_SIGNING,
    PROFILES,
    RSA_SHA256,
    SHA256,
    DocumentSignedError,
    NotAnRsaKeyError,
    Prepared,
    SigningPairError,
    UnsignableDocumentError,
    prepare,
    sign_enveloped,
)
from ledger_fiscal_kit.xml.hardened import DSIG, XmlDocumentError
from ledger_kernel.errors import InvalidValueError
from ledger_testing.pki import SIGNING, Authority, distinguished_name, ec_key, rsa_key
lazy from ledger_testing.pki import Issued

type Verify = Callable[[bytes, x509.Certificate], None]

GOLDEN_C14N_INPUT: Final = (
    b'<?xml version="1.0" encoding="utf-8"?>\n<?pi before?>\n<ECF  b="2" a="1&#9;&#10;x"'
    b' xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
    b"<Empty/><!-- gone --><T>Pe&#241;a &lt;12&quot;&gt; \r\n&amp;</T>"
    b'<x:P xmlns:x="urn:x" xmlns="urn:d"><Q/></x:P></ECF>'
)
GOLDEN_C14N: Final = (
    b'<?pi before?>\n<ECF xmlns:xsd="http://www.w3.org/2001/XMLSchema"'
    b' xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" a="1&#x9;&#xA;x" b="2"><Empty></Empty>'
    b'<T>Pe\xc3\xb1a &lt;12"&gt; \n&amp;</T><x:P xmlns="urn:d" xmlns:x="urn:x"><Q></Q></x:P></ECF>'
)
"""Inclusive C14N 1.0 without comments of the input above: attributes sorted, their whitespace as references,
CR LF read as LF, empty elements written out, comments dropped, the PI before the root kept with its line
break, namespace declarations first and sorted."""
GOLDEN_SIGNATURE: Final = (
    '<Signature xmlns="http://www.w3.org/2000/09/xmldsig#"><SignedInfo>'
    '<CanonicalizationMethod Algorithm="http://www.w3.org/TR/2001/REC-xml-c14n-20010315"/>'
    '<SignatureMethod Algorithm="http://www.w3.org/2001/04/xmldsig-more#rsa-sha256"/>'
    '<Reference URI=""><Transforms><Transform Algorithm="http://www.w3.org/2000/09/xmldsig#enveloped-signature"/>'
    '</Transforms><DigestMethod Algorithm="http://www.w3.org/2001/04/xmlenc#sha256"/>'
    "<DigestValue>{digest}</DigestValue></Reference></SignedInfo><SignatureValue>{value}</SignatureValue>"
    "<KeyInfo><X509Data><X509Certificate>{certificate}</X509Certificate></X509Data></KeyInfo></Signature>"
)
"""DGII's shape (*Firmado de e-CF*), exactly, with its three values taken out."""
VALUES: Final = re.compile(r"<(DigestValue|SignatureValue|X509Certificate)>([^<]*)<")
ECF: Final = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    '<ECF xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema">'
    "<Encabezado><Version>1.0</Version><IdDoc><TipoeCF>31</TipoeCF><eNCF>E310000000001</eNCF></IdDoc>"
    "<Emisor><RNCEmisor>101850043</RNCEmisor>"
    "<RazonSocialEmisor>Ferretería Duarte &amp; Hijos</RazonSocialEmisor></Emisor></Encabezado>"
    "<DetallesItems><Item><NombreItem>Martillo &lt;12&quot;&gt;</NombreItem></Item>"
    "</DetallesItems><FechaHoraFirma>13-10-2026 09:30:00</FechaHoraFirma></ECF>"
).encode()
"""A document shaped as DGII's are: prefixed namespaces on the root, character references, non-ASCII text."""
SUBJECT: Final = distinguished_name((NameOID.COMMON_NAME, "LEDGER TEST SIGNER"))
ENCIPHERMENT_ONLY: Final = x509.KeyUsage(
    digital_signature=False,
    content_commitment=False,
    key_encipherment=True,
    data_encipherment=False,
    key_agreement=False,
    key_cert_sign=False,
    crl_sign=False,
    encipher_only=False,
    decipher_only=False,
)


def sign(issued: Issued, document: bytes) -> bytes:
    return sign_enveloped(document, issued.key, issued.certificate)


def rsa(issued: Issued) -> RSAPrivateKey:
    assert isinstance(issued.key, RSAPrivateKey)
    return issued.key


def canonical(document: bytes) -> bytes:
    return etree.tostring(
        etree.fromstring(document).getroottree(), method="c14n", exclusive=False, with_comments=False
    )


def certificate(
    key: RSAPrivateKey, *, ca: bool | None = False, usage: x509.KeyUsage | None = SIGNING
) -> x509.Certificate:
    """A self-issued certificate of ``key`` with the extensions a test asks for (``None``: left out)."""
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "ODD")])
    now = datetime.now(UTC)
    builder = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
    )
    if ca is not None:
        builder = builder.add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
    if usage is not None:
        builder = builder.add_extension(usage, critical=True)
    return builder.sign(key, hashes.SHA256())


def test_the_profile_is_dgii_s_and_its_c14n_variant() -> None:
    head = (("CanonicalizationMethod", C14N), ("SignatureMethod", RSA_SHA256), ("Transform", ENVELOPED))
    assert {
        (*head, ("DigestMethod", SHA256)),
        (*head, ("Transform", C14N), ("DigestMethod", SHA256)),
    } == PROFILES


def test_the_signature_has_exactly_dgii_s_shape(issued: Issued, verify: Verify) -> None:
    signed = sign(issued, ECF)
    found = re.search(rb"<Signature .*</Signature>", signed)
    assert found is not None
    element = found.group().decode()
    values = dict(
        zip(("digest", "value", "certificate"), [m.group(2) for m in VALUES.finditer(element)], strict=True)
    )
    assert element == GOLDEN_SIGNATURE.format(**values)
    assert base64.b64decode(values["digest"]) == hashlib.sha256(canonical(ECF)).digest()
    assert base64.b64decode(values["certificate"]) == issued.certificate.public_bytes(Encoding.DER)
    assert signed.endswith(GOLDEN_SIGNATURE.format(**values).encode() + b"</ECF>")
    verify(signed, issued.certificate)


def test_the_canonical_form_is_c14n_1_0_inclusive_without_comments() -> None:
    assert canonical(GOLDEN_C14N_INPUT) == GOLDEN_C14N


def test_prepare_then_complete_equals_sign_enveloped(issued: Issued) -> None:
    prepared = prepare(ECF)
    value = rsa(issued).sign(prepared.to_sign, padding.PKCS1v15(), hashes.SHA256())
    assert prepared.complete(value, issued.certificate) == sign(issued, ECF)
    assert prepared.complete(value, issued.certificate) == sign(issued, ECF)
    assert b"101850043" not in repr(prepared).encode()


def test_complete_refuses_a_certificate_that_is_not_the_signers(issued: Issued) -> None:
    prepared = prepare(ECF)
    value = rsa(issued).sign(prepared.to_sign, padding.PKCS1v15(), hashes.SHA256())
    stranger = Authority.root().issue(SUBJECT)
    with pytest.raises(SigningPairError, match="not the signing key's") as caught:
        prepared.complete(value, stranger.certificate)
    assert caught.value.code == "xml_signing_pair_refused"
    with pytest.raises(SigningPairError, match="not the signing key's"):
        prepared.complete(value[::-1], issued.certificate)
    with pytest.raises(SigningPairError, match=NOT_FOR_SIGNING):
        prepared.complete(value, certificate(rsa(issued), usage=ENCIPHERMENT_ONLY))


def test_complete_refuses_a_certificate_whose_key_is_not_rsa(issued: Issued) -> None:
    prepared = prepare(ECF)
    value = rsa(issued).sign(prepared.to_sign, padding.PKCS1v15(), hashes.SHA256())
    with pytest.raises(NotAnRsaKeyError) as caught:
        prepared.complete(value, Authority.root().issue(SUBJECT, key=ec_key()).certificate)
    assert caught.value.code == "xml_signing_key_not_rsa"


def test_a_prepared_document_is_made_by_prepare_alone() -> None:
    # Built otherwise, it would skip prepare's checks (comments, xml: attributes, a signature already there).
    unchecked = b"<!-- c --><a/>"
    with pytest.raises(InvalidValueError, match=r"prepared must be made by dsig.prepare"):
        Prepared(object(), unchecked, "digest", b"<SignedInfo/>")


def test_a_signature_made_from_the_digest_alone_verifies(issued: Issued, verify: Verify) -> None:
    # The remote backend's path: the certification entity sees SHA-256 of SignedInfo, never the document.
    prepared = prepare(ECF)
    digest = hashlib.sha256(prepared.to_sign).digest()
    value = rsa(issued).sign(digest, padding.PKCS1v15(), utils.Prehashed(hashes.SHA256()))
    signed = prepared.complete(value, issued.certificate)
    verify(signed, issued.certificate)
    assert signed == sign(issued, ECF)


def test_the_signer_signs_only_with_its_own_certificate_and_a_long_key(issued: Issued) -> None:
    with pytest.raises(SigningPairError, match="not the signing key's"):
        sign_enveloped(ECF, rsa_key(), issued.certificate)
    with pytest.raises(SigningPairError, match="not the signing key's"):
        sign_enveloped(ECF, rsa_key(), Authority.root().issue(SUBJECT, key=ec_key()).certificate)
    short = rsa_key(1024)
    with pytest.raises(SigningPairError, match="shorter than 2048 bits"):
        sign_enveloped(ECF, short, certificate(short))


@pytest.mark.parametrize(
    ("ca", "usage", "reason"),
    [
        (True, SIGNING, CERTIFICATION_AUTHORITY),
        (False, None, NOT_FOR_SIGNING),
        (False, ENCIPHERMENT_ONLY, NOT_FOR_SIGNING),
    ],
    ids=["authority", "no-key-usage", "encipherment-only"],
)
def test_a_certificate_that_may_not_sign_signs_nothing(
    usage: x509.KeyUsage | None, reason: str, *, ca: bool
) -> None:
    key = rsa_key()
    with pytest.raises(SigningPairError, match=reason) as caught:
        sign_enveloped(ECF, key, certificate(key, ca=ca, usage=usage))
    assert caught.value.code == "xml_signing_pair_refused"


def test_an_end_entity_without_basic_constraints_signs(verify: Verify) -> None:
    key = rsa_key()
    made = certificate(key, ca=None)
    verify(sign_enveloped(ECF, key, made), made)


@pytest.mark.parametrize(
    ("document", "reason"),
    [
        (ECF.replace(b"<Encabezado>", b"<!-- c --><Encabezado>"), "comment"),
        (ECF.replace(b"<Encabezado>", b"<?pi in?><Encabezado>"), "comment"),
        (b"<!-- before --><a/>", "comment"),
        (b'<a xml:lang="es"/>', "xml: attribute"),
    ],
    ids=["comment", "pi-inside", "comment-outside", "xml-attribute"],
)
def test_what_the_strict_profile_would_refuse_is_not_signed(
    issued: Issued, document: bytes, reason: str
) -> None:
    with pytest.raises(UnsignableDocumentError, match=reason) as caught:
        sign(issued, document)
    assert caught.value.code == "xml_document_unsignable"


@pytest.mark.parametrize(
    "document",
    [
        b"<a>",
        b'<!DOCTYPE a [<!ENTITY b "c">]><a>&b;</a>',
        b'<!DOCTYPE a SYSTEM "http://169.254.169.254/latest/meta-data/"><a/>',
        b'<?xml version="1.0" encoding="latin-1"?><a/>',
    ],
    ids=["unclosed", "entity", "external-dtd", "latin-1"],
)
def test_what_cannot_be_signed_is_refused_unread(issued: Issued, document: bytes) -> None:
    with pytest.raises(XmlDocumentError):
        sign(issued, document)


def test_a_signed_document_is_not_signed_again(issued: Issued) -> None:
    with pytest.raises(DocumentSignedError) as caught:
        sign(issued, sign(issued, ECF))
    assert (caught.value.code, caught.value.kind) == ("xml_document_signed", "invalid")
    with pytest.raises(DocumentSignedError):
        sign(issued, f'<Signature xmlns="{DSIG}"/>'.encode())


def test_only_an_rsa_key_signs(issued: Issued) -> None:
    with pytest.raises(NotAnRsaKeyError) as caught:
        sign_enveloped(ECF, ec_key(), issued.certificate)
    assert caught.value.code == "xml_signing_key_not_rsa"
