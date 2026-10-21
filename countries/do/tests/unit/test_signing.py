import base64
import re
from datetime import UTC, datetime, timedelta
from typing import Final
lazy from collections.abc import Callable

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.x509.oid import NameOID
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from lxml import etree

from ledger_country_do.channels.ecf_v1.adapters.signing.seeds import SeedMalformedError, require_seed
from ledger_country_do.channels.ecf_v1.adapters.signing.verifier import (
    INBOUND,
    STRICT,
    SignatureInvalidError,
    verify_enveloped,
)
from ledger_country_do_testing.pki import organization
from ledger_country_do_testing.signatures import (
    OUTSIDE_ROOT,
    SHAPES,
    XMLSEC1_ONLY,
    forge,
    sign_with_signxml,
    verify_with_signxml,
)
from ledger_fiscal_kit.ports import MAX_SEED_BYTES
from ledger_fiscal_kit.xml.dsig import (
    C14N,
    CERTIFICATION_AUTHORITY,
    ENVELOPED,
    NOT_FOR_SIGNING,
    SigningPairError,
    sign_enveloped,
)
from ledger_fiscal_kit.xml.hardened import XmlDocumentError, texts
from ledger_testing.pki import SIGNING, Authority, ec_key, rsa_key
lazy from ledger_country_do.channels.ecf_v1.adapters.signing.verifier import VerificationProfile
lazy from ledger_testing.pki import Issued

ECF: Final = SHAPES["ecf"]
RNC: Final = b"101850043"
PROFILES_VERIFIED: Final = [STRICT, INBOUND]
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


def canonical(document: bytes) -> bytes:
    return etree.tostring(
        etree.fromstring(document).getroottree(), method="c14n", exclusive=False, with_comments=False
    )


def certificate(
    key: RSAPrivateKey, *, ca: bool | None = False, usage: x509.KeyUsage | None = SIGNING
) -> x509.Certificate:
    """A self-issued certificate of ``key`` with the extensions a test asks for (``None``: left out).

    It never has a subject key identifier.
    """
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


@pytest.mark.parametrize("profile", PROFILES_VERIFIED)
@pytest.mark.parametrize("shape", list(SHAPES))
def test_our_verifier_accepts_our_signatures(
    issued: Issued, shape: str, profile: VerificationProfile
) -> None:
    signed = sign(issued, SHAPES[shape])
    verified = verify_enveloped(signed, profile)
    assert verified.certificate == issued.certificate
    assert verified.intermediates == ()
    assert len(verified.signature_value) > 300
    assert verified.signature_value in signed.decode()


@pytest.mark.parametrize("shape", [shape for shape in SHAPES if shape not in XMLSEC1_ONLY])
def test_signxml_verifies_our_signatures(issued: Issued, shape: str) -> None:
    assert verify_with_signxml(sign(issued, SHAPES[shape]), trusted=[issued.certificate])


@pytest.mark.parametrize("profile", PROFILES_VERIFIED)
@pytest.mark.parametrize("prefixed", [False, True], ids=["default-namespace", "ds-prefix"])
@pytest.mark.parametrize("shape", ["ecf", "default-namespace", "nested-defaults", "references"])
def test_our_verifier_accepts_signxml_s_signatures(
    issued: Issued, shape: str, profile: VerificationProfile, *, prefixed: bool
) -> None:
    signed = sign_with_signxml(SHAPES[shape], issued, prefixed=prefixed)
    assert verify_enveloped(signed, profile).certificate == issued.certificate


def test_a_signature_value_wrapped_over_lines_is_read_as_written(issued: Issued) -> None:
    signed = sign(issued, ECF)
    value = verify_enveloped(signed).signature_value
    wrapped = signed.replace(value.encode(), b"\n".join(re.findall(rb".{1,76}", value.encode())) + b"\n")
    assert verify_enveloped(wrapped).signature_value == value


def test_a_tampered_document_fails_both_verifiers(issued: Issued) -> None:
    tampered = sign(issued, ECF).replace(RNC, b"101850044")
    with pytest.raises(SignatureInvalidError, match="changed after it was signed"):
        verify_enveloped(tampered)
    assert not verify_with_signxml(tampered, trusted=[issued.certificate])


@pytest.mark.parametrize("profile", PROFILES_VERIFIED)
def test_a_comment_cannot_split_a_signed_value(issued: Issued, profile: VerificationProfile) -> None:
    # C14N without comments reads 1<!---->0 as 10: the digest holds, so the comment itself must be refused.
    signed = sign(issued, ECF.replace(b"</FechaHoraFirma>", b"</FechaHoraFirma><Estado>10</Estado>"))
    split = signed.replace(b"<Estado>10<", b"<Estado>1<!---->0<").replace(RNC, b"1018<!-- -->50043")
    assert canonical(split) == canonical(signed)
    with pytest.raises(SignatureInvalidError, match="comment"):
        verify_enveloped(split, profile)
    assert texts(split, "Estado", "Encabezado/Emisor/RNCEmisor") == ("10", RNC.decode())


def test_what_lies_outside_the_root_is_covered_too(issued: Issued) -> None:
    signed = sign(issued, SHAPES[OUTSIDE_ROOT])
    with pytest.raises(SignatureInvalidError, match="changed after it was signed"):
        verify_enveloped(signed.replace(b"<?pi before?>", b"<?pi altered?>"))


def test_whitespace_around_the_signature_is_the_document_s_own(issued: Issued) -> None:
    # The enveloped-signature transform removes the Signature element, not the text around it.
    for document in (b"<a><b/>\n</a>", b"<a>\n</a>"):
        root = etree.fromstring(sign(issued, document))
        signature = root[-1]
        previous = signature.getprevious()
        if previous is None:
            signature.tail, root.text = root.text, None
        else:
            signature.tail, previous.tail = previous.tail, None
        verify_enveloped(etree.tostring(root))


def _swap(pattern: bytes, replacement: bytes) -> Callable[[bytes], bytes]:
    return lambda signed: re.sub(pattern, replacement, signed, count=1, flags=re.DOTALL)


def _certificate_of(issued: Issued) -> bytes:
    return base64.b64encode(issued.certificate.public_bytes(Encoding.DER))


def _ec_certificate(signed: bytes) -> bytes:
    issued = Authority.root().issue(organization(), key=ec_key())
    return _swap(rb"<X509Certificate>[^<]*<", b"<X509Certificate>" + _certificate_of(issued) + b"<")(signed)


def _other_certificate(signed: bytes) -> bytes:
    issued = Authority.root().issue(organization())
    return _swap(rb"<X509Certificate>[^<]*<", b"<X509Certificate>" + _certificate_of(issued) + b"<")(signed)


def _nested(signed: bytes) -> bytes:
    root = etree.fromstring(signed)
    root[0].append(root[-1])
    return etree.tostring(root)


def _transform(algorithm: bytes) -> bytes:
    return b'<Transform Algorithm="' + algorithm + b'"/></Transforms>'


_PROFILE: Final = "not DGII's profile"
XADES: Final = b'<xades:QualifyingProperties xmlns:xades="http://uri.etsi.org/01903/v1.3.2#"/>'
_SINGLE: Final = "single signature"
_COMMENT: Final = "comment or a processing instruction"
MUTATIONS: Final[dict[str, tuple[Callable[[bytes], bytes], str]]] = {
    "sha1": (_swap(rb"xmldsig-more#rsa-sha256", b"xmldsig#rsa-sha1"), _PROFILE),
    "sha1-digest": (_swap(rb"xmlenc#sha256", b"xmldsig#sha1"), _PROFILE),
    "exclusive-c14n": (_swap(rb"TR/2001/REC-xml-c14n-20010315", b"2001/10/xml-exc-c14n#"), _PROFILE),
    "c14n-1.1": (_swap(rb"TR/2001/REC-xml-c14n-20010315", b"2006/12/xml-c14n11"), _PROFILE),
    "c14n-with-comments": (_swap(rb"REC-xml-c14n-20010315", b"REC-xml-c14n-20010315#WithComments"), _PROFILE),
    "reference": (_swap(rb'URI=""', b'URI="#x"'), _PROFILE),
    "no-uri": (_swap(rb' URI=""', b""), _PROFILE),
    "reference-type": (_swap(rb'URI=""', b'URI="" Type="urn:x"'), _PROFILE),
    "two-references": (_swap(rb"(<Reference .*</Reference>)", rb"\1\1"), _PROFILE),
    "no-transforms": (_swap(rb"<Transforms>.*</Transforms>", b""), _PROFILE),
    "xpath-transform": (
        _swap(rb"</Transforms>", _transform(b"http://www.w3.org/TR/1999/REC-xpath-19991116")),
        _PROFILE,
    ),
    "xpath-inside": (
        _swap(rb'(enveloped-signature")/>', rb"\1><XPath>1</XPath></Transform>"),
        _PROFILE,
    ),
    "third-transform": (
        _swap(
            rb"</Transforms>", b'<Transform Algorithm="' + C14N.encode() + b'"/>' + _transform(C14N.encode())
        ),
        _PROFILE,
    ),
    "transforms-swapped": (
        _swap(
            rb"<Transform [^>]*/>",
            b'<Transform Algorithm="'
            + C14N.encode()
            + b'"/><Transform Algorithm="'
            + ENVELOPED.encode()
            + b'"/>',
        ),
        _PROFILE,
    ),
    "hmac-length": (
        _swap(rb'(rsa-sha256")/>', rb"\1><HMACOutputLength>8</HMACOutputLength></SignatureMethod>"),
        _PROFILE,
    ),
    "transform-id": (_swap(rb"<Transform ", b'<Transform Id="t" '), _PROFILE),
    "object": (_swap(rb"</KeyInfo>", b"</KeyInfo><Object>x</Object>"), _PROFILE),
    "xades": (_swap(rb"</KeyInfo>", b"</KeyInfo><Object>" + XADES + b"</Object>"), _PROFILE),
    "manifest": (_swap(rb"</KeyInfo>", b"</KeyInfo><Manifest/>"), _PROFILE),
    "retrieval-method": (_swap(rb"</X509Data>", b'</X509Data><RetrievalMethod URI="#k"/>'), _PROFILE),
    "x509-crl": (_swap(rb"</X509Certificate>", b"</X509Certificate><X509CRL>AAAA</X509CRL>"), _PROFILE),
    "dsa-key-value": (_swap(rb"</X509Data>", b"</X509Data><KeyValue><DSAKeyValue/></KeyValue>"), _PROFILE),
    "key-info-swapped": (
        _swap(rb"(<SignatureValue>[^<]*</SignatureValue>)(<KeyInfo>.*</KeyInfo>)", rb"\2\1"),
        _PROFILE,
    ),
    "foreign-element": (_swap(rb"</SignedInfo>", b'<x:y xmlns:x="urn:x"/></SignedInfo>'), _PROFILE),
    "foreign-attribute": (_swap(rb"<SignedInfo>", b'<SignedInfo xmlns:x="urn:x" x:a="1">'), _PROFILE),
    "text-inside": (_swap(rb"</SignedInfo>", b"x</SignedInfo>"), _PROFILE),
    "tail-inside": (_swap(rb"</SignedInfo>", b"</SignedInfo>x"), _PROFILE),
    "no-value": (_swap(rb"<SignatureValue>.*</SignatureValue>", b""), _PROFILE),
    "no-signed-info": (_swap(rb"<SignedInfo>.*</SignedInfo>", b""), _PROFILE),
    "no-key-info": (_swap(rb"<KeyInfo>.*</KeyInfo>", b""), _PROFILE),
    "comment-inside": (_swap(rb"</SignedInfo>", b"<!-- c --></SignedInfo>"), _COMMENT),
    "comment-after": (_swap(rb"</Signature>", b"</Signature><!-- after -->"), _COMMENT),
    "comment-in-value": (_swap(rb"101850043", b"1018<!---->50043"), _COMMENT),
    "pi-inside": (_swap(rb"<Encabezado>", b"<?pi in?><Encabezado>"), _COMMENT),
    "pi-in-signature": (_swap(rb"</KeyInfo>", b"</KeyInfo><?pi in?>"), _COMMENT),
    "two-signatures": (_swap(rb"(<Signature .*</Signature>)", rb"\1\1"), _SINGLE),
    "not-last": (_swap(rb"</Signature>", b"</Signature><After/>"), _SINGLE),
    "nested": (_nested, _SINGLE),
    "removed": (_swap(rb"<Signature .*</Signature>", b""), _SINGLE),
    "not-base64": (_swap(rb"<DigestValue>[^<]*<", b"<DigestValue>!!!<"), "not base64"),
    "not-der": (_swap(rb"<X509Certificate>[^<]*<", b"<X509Certificate>AAAA<"), "not base64"),
    "ec-certificate": (_ec_certificate, "not RSA"),
    "other-certificate": (_other_certificate, "does not match"),
    "wrong-value": (
        _swap(rb"<SignatureValue>[^<]*<", b"<SignatureValue>" + base64.b64encode(bytes(256)) + b"<"),
        "does not match",
    ),
}


@pytest.mark.parametrize("profile", PROFILES_VERIFIED)
@pytest.mark.parametrize("mutation", list(MUTATIONS))
def test_what_breaks_dgii_s_profile_is_refused(
    issued: Issued, mutation: str, profile: VerificationProfile
) -> None:
    mutate, reason = MUTATIONS[mutation]
    with pytest.raises(SignatureInvalidError, match=reason) as caught:
        verify_enveloped(mutate(sign(issued, ECF)), profile)
    assert caught.value.code == "do_ecf_signature_invalid"
    assert RNC.decode() not in str(caught.value)


@pytest.mark.parametrize("profile", PROFILES_VERIFIED)
def test_a_signature_as_the_root_is_refused(issued: Issued, profile: VerificationProfile) -> None:
    signed = sign(issued, ECF)
    signature = signed[signed.index(b"<Signature ") : signed.index(b"</ECF>")]
    with pytest.raises(SignatureInvalidError, match=_SINGLE):
        verify_enveloped(signature.replace(b"</Signature>", signature + b"</Signature>", 1), profile)


@pytest.mark.parametrize("profile", PROFILES_VERIFIED)
def test_a_key_shorter_than_2048_bits_is_refused(profile: VerificationProfile) -> None:
    key = rsa_key(1024)
    with pytest.raises(SignatureInvalidError, match="shorter than 2048 bits"):
        verify_enveloped(forge(ECF, key, certificate(key)), profile)


@pytest.mark.parametrize("profile", PROFILES_VERIFIED)
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
    profile: VerificationProfile, usage: x509.KeyUsage | None, reason: str, *, ca: bool
) -> None:
    key = rsa_key()
    made = certificate(key, ca=ca, usage=usage)
    with pytest.raises(SignatureInvalidError, match=reason):
        verify_enveloped(forge(ECF, key, made), profile)
    with pytest.raises(SigningPairError, match=reason) as caught:
        sign_enveloped(ECF, key, made)
    assert caught.value.code == "xml_signing_pair_refused"


@pytest.mark.parametrize("profile", PROFILES_VERIFIED)
def test_an_end_entity_without_basic_constraints_signs(profile: VerificationProfile) -> None:
    key = rsa_key()
    made = certificate(key, ca=None)
    assert verify_enveloped(sign_enveloped(ECF, key, made), profile).certificate == made


def test_a_subject_key_identifier_named_for_a_certificate_without_one_is_refused() -> None:
    key = rsa_key()
    made = certificate(key)
    identifier = base64.b64encode(bytes(20)).decode()
    signed = forge(
        ECF,
        key,
        made,
        edit=lambda text: text.replace("</X509Data>", f"<X509SKI>{identifier}</X509SKI></X509Data>"),
    )
    with pytest.raises(SignatureInvalidError, match="names another certificate or key"):
        verify_enveloped(signed, INBOUND)


def test_an_xml_attribute_on_the_root_is_strictly_refused(issued: Issued) -> None:
    assert isinstance(issued.key, RSAPrivateKey)
    signed = forge(b'<r xml:lang="es-DO"><a xml:space="preserve"> t </a></r>', issued.key, issued.certificate)
    with pytest.raises(SignatureInvalidError, match="xml: attribute"):
        verify_enveloped(signed, STRICT)
    assert verify_enveloped(signed, INBOUND).certificate == issued.certificate


def test_a_seed_is_dgii_s_semilla_model() -> None:
    require_seed(SHAPES["seed"])


@pytest.mark.parametrize(
    ("seed", "unreadable"),
    [
        (b"<a>", True),
        (b'<!DOCTYPE a [<!ENTITY b "c">]><SemillaModel>&b;</SemillaModel>', True),
        (b'<?xml version="1.0" encoding="latin-1"?><SemillaModel/>', True),
        (b"<SemillaModel><fecha/><valor/></SemillaModel>", False),
        (b"<Semilla><valor/><fecha/></Semilla>", False),
        (ECF, False),
        (b"<SemillaModel><valor>" + b"v" * MAX_SEED_BYTES + b"</valor><fecha>f</fecha></SemillaModel>", True),
    ],
    ids=["unclosed", "entity", "latin-1", "seed-order", "seed-root", "ecf-as-seed", "over-the-port-s-limit"],
)
def test_what_is_not_a_seed_is_refused_before_it_is_signed(seed: bytes, *, unreadable: bool) -> None:
    with pytest.raises(SeedMalformedError) as caught:
        require_seed(seed)
    assert isinstance(caught.value.__cause__, XmlDocumentError) is unreadable


@pytest.mark.parametrize(
    "document",
    [b"<a>", b'<!DOCTYPE a [<!ENTITY b "c">]><a>&b;</a>', b'<?xml version="1.0" encoding="latin-1"?><a/>'],
    ids=["unclosed", "entity", "latin-1"],
)
def test_what_cannot_be_read_is_not_verified(document: bytes) -> None:
    with pytest.raises(XmlDocumentError):
        verify_enveloped(document)


_NAMES: Final = st.sampled_from(["a", "b", "Item", "eNCF", "x-y", "z.1"])
_TEXT: Final = st.text(
    st.characters(codec="utf-8", exclude_categories=("Cs", "Cc", "Co"), include_characters="\t\n\r"),
    max_size=12,
).filter(lambda text: "￾" not in text and "￿" not in text)
NAMESPACES: Final[tuple[dict[str | None, str], ...]] = ({}, {"xsi": "urn:xsi"}, {None: "urn:d"})


@st.composite
def documents(draw: st.DrawFn) -> bytes:
    """Well-formed documents without comments: nested elements, attributes, any text XML allows."""
    root = etree.Element(draw(_NAMES), nsmap=draw(st.sampled_from(NAMESPACES)))
    elements = [root]
    for _ in range(draw(st.integers(0, 8))):
        parent = draw(st.sampled_from(elements))
        namespace = draw(st.sampled_from([None, "urn:n"]))
        tag = draw(_NAMES) if namespace is None else f"{{{namespace}}}{draw(_NAMES)}"
        child = etree.SubElement(parent, tag, {draw(_NAMES): draw(_TEXT)} if draw(st.booleans()) else {})
        child.text = draw(_TEXT)
        child.tail = draw(_TEXT)
        elements.append(child)
    root.text = draw(_TEXT)
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8")


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(documents())
def test_any_well_formed_document_round_trips_through_both_verifiers(issued: Issued, document: bytes) -> None:
    signed = sign(issued, document)
    assert verify_enveloped(signed).certificate == issued.certificate
    assert verify_with_signxml(signed, trusted=[issued.certificate])
