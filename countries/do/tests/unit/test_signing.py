import base64
import hashlib
import re
from typing import Final
lazy from collections.abc import Callable

import pytest
from cryptography.hazmat.primitives.serialization import Encoding
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from lxml import etree

from ledger_country_do.channels.ecf_v1.adapters.signing.profile import (
    C14N,
    ENVELOPED,
    PROFILES,
    RSA_SHA256,
    SHA256,
)
from ledger_country_do.channels.ecf_v1.adapters.signing.signer import (
    DocumentSignedError,
    NotAnRsaKeyError,
    SeedMalformedError,
    sign_enveloped,
)
from ledger_country_do.channels.ecf_v1.adapters.signing.verifier import (
    SignatureInvalidError,
    verify_enveloped,
)
from ledger_country_do.channels.ecf_v1.adapters.xml.parsing import DSIG, UnreadableDocumentError
from ledger_country_do_testing.pki import Authority, ec_key, organization, rsa_key
from ledger_country_do_testing.signatures import (
    OUTSIDE_ROOT,
    SHAPES,
    XMLSEC1_ONLY,
    sign_with_signxml,
    verify_with_signxml,
)
lazy from ledger_country_do_testing.pki import Issued

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
ECF: Final = SHAPES["ecf"]
RNC: Final = b"101850043"


def sign(issued: Issued, document: bytes, *, seed: bool = False) -> bytes:
    return sign_enveloped(document, issued.key, issued.certificate, seed=seed)


def canonical(document: bytes) -> bytes:
    return etree.tostring(
        etree.fromstring(document).getroottree(), method="c14n", exclusive=False, with_comments=False
    )


def test_the_profile_is_dgii_s_and_its_c14n_variant() -> None:
    head = (("CanonicalizationMethod", C14N), ("SignatureMethod", RSA_SHA256), ("Transform", ENVELOPED))
    assert {
        (*head, ("DigestMethod", SHA256)),
        (*head, ("Transform", C14N), ("DigestMethod", SHA256)),
    } == PROFILES


def test_the_signature_has_exactly_dgii_s_shape(issued: Issued) -> None:
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


def test_the_canonical_form_is_c14n_1_0_inclusive_without_comments() -> None:
    assert canonical(GOLDEN_C14N_INPUT) == GOLDEN_C14N


@pytest.mark.parametrize("shape", list(SHAPES))
def test_our_verifier_accepts_our_signatures(issued: Issued, shape: str) -> None:
    signed = sign(issued, SHAPES[shape], seed=shape == "seed")
    verified = verify_enveloped(signed)
    assert verified.certificate == issued.certificate
    assert len(verified.signature_value) > 300
    assert verified.signature_value in signed.decode()


@pytest.mark.parametrize("shape", [shape for shape in SHAPES if shape not in XMLSEC1_ONLY])
def test_signxml_verifies_our_signatures(issued: Issued, shape: str) -> None:
    assert verify_with_signxml(
        sign(issued, SHAPES[shape], seed=shape == "seed"), trusted=[issued.certificate]
    )


@pytest.mark.parametrize("prefixed", [False, True], ids=["default-namespace", "ds-prefix"])
@pytest.mark.parametrize("shape", ["ecf", "default-namespace", "nested-defaults", "references"])
def test_our_verifier_accepts_signxml_s_signatures(issued: Issued, shape: str, *, prefixed: bool) -> None:
    signed = sign_with_signxml(SHAPES[shape], issued, prefixed=prefixed)
    assert verify_enveloped(signed).certificate == issued.certificate


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


def test_what_lies_outside_the_root_is_covered_too(issued: Issued) -> None:
    signed = sign(issued, SHAPES[OUTSIDE_ROOT])
    with pytest.raises(SignatureInvalidError, match="changed after it was signed"):
        verify_enveloped(signed.replace(b"<?pi before?>", b"<?pi after?>"))


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
_SINGLE: Final = "single signature"
MUTATIONS: Final[dict[str, tuple[Callable[[bytes], bytes], str]]] = {
    "sha1": (_swap(rb"xmldsig-more#rsa-sha256", b"xmldsig#rsa-sha1"), _PROFILE),
    "sha1-digest": (_swap(rb"xmlenc#sha256", b"xmldsig#sha1"), _PROFILE),
    "exclusive-c14n": (_swap(rb"TR/2001/REC-xml-c14n-20010315", b"2001/10/xml-exc-c14n#"), _PROFILE),
    "c14n-with-comments": (_swap(rb"REC-xml-c14n-20010315", b"REC-xml-c14n-20010315#WithComments"), _PROFILE),
    "reference": (_swap(rb'URI=""', b'URI="#x"'), _PROFILE),
    "reference-type": (_swap(rb'URI=""', b'URI="" Type="urn:x"'), _PROFILE),
    "two-references": (_swap(rb"(<Reference .*</Reference>)", rb"\1\1"), _PROFILE),
    "no-transforms": (_swap(rb"<Transforms>.*</Transforms>", b""), _PROFILE),
    "xpath-transform": (
        _swap(rb"</Transforms>", _transform(b"http://www.w3.org/TR/1999/REC-xpath-19991116")),
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
    "object": (_swap(rb"</KeyInfo>", b"</KeyInfo><Object>x</Object>"), _PROFILE),
    "key-value": (_swap(rb"</X509Data>", b"</X509Data><KeyValue/>"), _PROFILE),
    "two-certificates": (_swap(rb"(<X509Certificate>[^<]*</X509Certificate>)", rb"\1\1"), _PROFILE),
    "foreign-element": (_swap(rb"</SignedInfo>", b'<x:y xmlns:x="urn:x"/></SignedInfo>'), _PROFILE),
    "comment-inside": (_swap(rb"</SignedInfo>", b"<!-- c --></SignedInfo>"), _PROFILE),
    "text-inside": (_swap(rb"</SignedInfo>", b"x</SignedInfo>"), _PROFILE),
    "signature-id": (_swap(rb'<Signature xmlns="', b'<Signature Id="s" xmlns="'), _PROFILE),
    "no-value": (_swap(rb"<SignatureValue>.*</SignatureValue>", b""), _PROFILE),
    "no-signed-info": (_swap(rb"<SignedInfo>.*</SignedInfo>", b""), _PROFILE),
    "two-signatures": (_swap(rb"(<Signature .*</Signature>)", rb"\1\1"), _SINGLE),
    "not-last": (_swap(rb"</Signature>", b"</Signature><After/>"), _SINGLE),
    "comment-after": (_swap(rb"</Signature>", b"</Signature><!-- after -->"), _SINGLE),
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


@pytest.mark.parametrize("mutation", list(MUTATIONS))
def test_what_breaks_dgii_s_profile_is_refused(issued: Issued, mutation: str) -> None:
    mutate, reason = MUTATIONS[mutation]
    with pytest.raises(SignatureInvalidError, match=reason) as caught:
        verify_enveloped(mutate(sign(issued, ECF)))
    assert caught.value.code == "do_ecf_signature_invalid"
    assert RNC.decode() not in str(caught.value)


def test_a_signature_as_the_root_is_refused(issued: Issued) -> None:
    signed = sign(issued, ECF)
    signature = signed[signed.index(b"<Signature ") : signed.index(b"</ECF>")]
    with pytest.raises(SignatureInvalidError, match=_SINGLE):
        verify_enveloped(signature.replace(b"</Signature>", signature + b"</Signature>", 1))


def test_a_key_shorter_than_2048_bits_is_refused(intermediate: Authority) -> None:
    short = intermediate.issue(organization(), key=rsa_key(1024))
    with pytest.raises(SignatureInvalidError, match="shorter than 2048 bits"):
        verify_enveloped(sign(short, ECF))


@pytest.mark.parametrize(
    ("document", "seed", "error"),
    [
        (b"<a>", False, UnreadableDocumentError),
        (b'<!DOCTYPE a [<!ENTITY b "c">]><a>&b;</a>', False, UnreadableDocumentError),
        (
            b'<!DOCTYPE a SYSTEM "http://169.254.169.254/latest/meta-data/"><a/>',
            False,
            UnreadableDocumentError,
        ),
        (b'<?xml version="1.0" encoding="latin-1"?><a/>', False, UnreadableDocumentError),
        (b"<SemillaModel><fecha/><valor/></SemillaModel>", True, SeedMalformedError),
        (b"<Semilla><valor/><fecha/></Semilla>", True, SeedMalformedError),
        (ECF, True, SeedMalformedError),
    ],
    ids=["unclosed", "entity", "external-dtd", "latin-1", "seed-order", "seed-root", "ecf-as-seed"],
)
def test_what_cannot_be_signed_is_refused_unread(
    issued: Issued, document: bytes, *, seed: bool, error: type[Exception]
) -> None:
    with pytest.raises(error):
        sign(issued, document, seed=seed)


@pytest.mark.parametrize(
    "document",
    [b"<a>", b'<!DOCTYPE a [<!ENTITY b "c">]><a>&b;</a>', b'<?xml version="1.0" encoding="latin-1"?><a/>'],
    ids=["unclosed", "entity", "latin-1"],
)
def test_what_cannot_be_read_is_not_verified(document: bytes) -> None:
    with pytest.raises(UnreadableDocumentError):
        verify_enveloped(document)


def test_a_signed_document_is_not_signed_again(issued: Issued) -> None:
    with pytest.raises(DocumentSignedError):
        sign(issued, sign(issued, ECF))
    with pytest.raises(DocumentSignedError):
        sign(issued, f'<Signature xmlns="{DSIG}"/>'.encode())


def test_only_an_rsa_key_signs(issued: Issued) -> None:
    with pytest.raises(NotAnRsaKeyError):
        sign_enveloped(ECF, ec_key(), issued.certificate)


_NAMES: Final = st.sampled_from(["a", "b", "Item", "eNCF", "x-y", "z.1"])
_TEXT: Final = st.text(
    st.characters(codec="utf-8", exclude_categories=("Cs", "Cc", "Co"), include_characters="\t\n\r"),
    max_size=12,
).filter(lambda text: "￾" not in text and "￿" not in text)
NAMESPACES: Final[tuple[dict[str | None, str], ...]] = ({}, {"xsi": "urn:xsi"}, {None: "urn:d"})


@st.composite
def documents(draw: st.DrawFn) -> bytes:
    """Well-formed documents: nested elements, attributes, any text XML allows, comments, namespaces."""
    root = etree.Element(draw(_NAMES), nsmap=draw(st.sampled_from(NAMESPACES)))
    elements = [root]
    for _ in range(draw(st.integers(0, 8))):
        parent = draw(st.sampled_from(elements))
        namespace = draw(st.sampled_from([None, "urn:n"]))
        tag = draw(_NAMES) if namespace is None else f"{{{namespace}}}{draw(_NAMES)}"
        child = etree.SubElement(parent, tag, {draw(_NAMES): draw(_TEXT)} if draw(st.booleans()) else {})
        child.text = draw(_TEXT)
        if draw(st.booleans()):
            parent.append(etree.Comment(draw(st.sampled_from(["c", " a note "]))))
        elements.append(child)
    root.text = draw(_TEXT)
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8")


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(documents())
def test_any_well_formed_document_round_trips_through_both_verifiers(issued: Issued, document: bytes) -> None:
    signed = sign(issued, document)
    assert verify_enveloped(signed).certificate == issued.certificate
    assert verify_with_signxml(signed, trusted=[issued.certificate])
