"""Verifying a signature in DGII's profile: our own documents, and what DGII's counterparts sign (ARECF).

Built against signature wrapping and comment splitting: what is verified is the whole document as it will be
read, and nothing but DGII's profile is accepted. Two profiles share these rules:

- The document is read hardened (fiscal-kit's ``xml.hardened``) and holds no comment anywhere (a comment
  inside a value, ``1<!---->0``, would leave C14N without comments, so the signature, unchanged) and no
  processing instruction inside its root.
- It holds exactly one ``Signature``, its root's last child; the root itself is not one.
- ``SignedInfo``: C14N 1.0 (without comments), RSA-SHA256, one ``Reference`` with ``URI=""`` and no other
  attribute, its transforms the enveloped-signature one alone or followed by C14N 1.0 (``dsig.PROFILES``),
  SHA-256. No ``Object`` (so no XAdES), ``Manifest``, ``HMACOutputLength``, XPath or other element, no
  foreign element, no text but whitespace outside values.
- The digest is checked over the canonical form of the whole document (``URI=""``) without the ``Signature``
  element; the enveloped-signature transform removes the element, never the text around it.
- One signing certificate, an end entity that may sign (fiscal-kit's ``dsig.signing_problem``), RSA of
  ``MIN_KEY_BITS`` at least; ``SignatureValue`` is checked with its key and no other (a ``KeyValue`` is never
  used to verify).

``STRICT`` (what we sign, what DGII receives from us) is DGII's shape exactly: ``KeyInfo`` holds one
``X509Data`` holding one ``X509Certificate``, no ``Id``, and no ``xml:`` attribute on the root.

``INBOUND`` (what a counterparty signs: an ARECF) tolerates what common signers add, ignored or checked:

- ``Id`` on ``Signature``, ``SignedInfo``, ``Reference``, ``SignatureValue``, ``KeyInfo``, ``X509Data``;
- in ``KeyInfo``: one ``X509Data``, at most one ``KeyValue`` (an ``RSAKeyValue`` that must be the signing
  certificate's key) and at most one ``KeyName`` (ignored); no ``RetrievalMethod`` or other element;
- in ``X509Data``: one or more ``X509Certificate`` and at most one each of ``X509IssuerSerial``,
  ``X509SubjectName`` and ``X509SKI``, which must name the signing certificate (names as ``names`` reads them,
  skipped when unreadable; the serial number always). The signing certificate is the only one that issued no
  other in the set; the others are returned as untrusted intermediates;
- ``xml:`` attributes on the root, which C14N 1.0 carries onto ``SignedInfo``.

``SignedInfo`` is canonicalized as a deep copy under an element declaring every namespace in scope where it
sits (lxml's copy keeps only the namespaces it uses; inclusive C14N writes all in scope), carrying the root's
``xml:`` attributes (``profile.signed_info``'s reason for a standalone copy). Whether the certificate is one
to trust (its chain to an authorized authority, its validity, its subject's RNC) is the caller's policy:
``VerifiedSignature`` answers with the certificate and the intermediates the signature carries. A caller reads
what was signed from the same bytes it passed here, never from another copy.
"""

import base64
import copy
import hashlib
import hmac
from collections import Counter
from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar, Final, final
lazy from collections.abc import Iterator, Mapping, Sequence

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from lxml import etree

from ledger_country_do.channels.ecf_v1.adapters.signing.names import names_agree
from ledger_country_do.channels.ecf_v1.domain.errors import EcfInvalidError
from ledger_fiscal_kit.xml.dsig import MIN_KEY_BITS, PROFILES, XML_NAMESPACE, signing_problem
from ledger_fiscal_kit.xml.hardened import (
    COMMENT,
    DSIG,
    SIGNATURE,
    MalformedDocumentError,
    parser,
    require_plain,
    require_readable,
)


class VerificationProfile(StrEnum):
    """How much of what signers add beyond DGII's shape a verification tolerates (see the module)."""

    STRICT = "strict"
    INBOUND = "inbound"


STRICT: Final = VerificationProfile.STRICT
INBOUND: Final = VerificationProfile.INBOUND

_DS: Final = f"{{{DSIG}}}"
_XML: Final = f"{{{XML_NAMESPACE}}}"
_UNBOUNDED: Final = 1 << 16
_LEAVES: Final = (
    "CanonicalizationMethod",
    "SignatureMethod",
    "Transform",
    "DigestMethod",
    "DigestValue",
    "SignatureValue",
    "X509Certificate",
)
_SHARED_CHILDREN: Final[Mapping[str, frozenset[tuple[str, ...]]]] = {
    "Signature": frozenset({("SignedInfo", "SignatureValue", "KeyInfo")}),
    "SignedInfo": frozenset({("CanonicalizationMethod", "SignatureMethod", "Reference")}),
    "Reference": frozenset({("Transforms", "DigestMethod", "DigestValue")}),
    "Transforms": frozenset({("Transform",), ("Transform", "Transform")}),
    **dict.fromkeys(_LEAVES, frozenset({()})),
}
_CHILDREN: Final[Mapping[VerificationProfile, Mapping[str, frozenset[tuple[str, ...]]]]] = {
    STRICT: {
        **_SHARED_CHILDREN,
        "KeyInfo": frozenset({("X509Data",)}),
        "X509Data": frozenset({("X509Certificate",)}),
    },
    INBOUND: {
        **_SHARED_CHILDREN,
        "X509IssuerSerial": frozenset({("X509IssuerName", "X509SerialNumber")}),
        "KeyValue": frozenset({("RSAKeyValue",)}),
        "RSAKeyValue": frozenset({("Modulus", "Exponent")}),
        **dict.fromkeys(
            (
                "KeyName",
                "X509SubjectName",
                "X509SKI",
                "X509IssuerName",
                "X509SerialNumber",
                "Modulus",
                "Exponent",
            ),
            frozenset({()}),
        ),
    },
}
"""The children each element may hold, in order, by profile; an element missing here is refused."""
_COUNTED: Final[Mapping[str, Mapping[str, tuple[int, int]]]] = {
    "KeyInfo": {"X509Data": (1, 1), "KeyValue": (0, 1), "KeyName": (0, 1)},
    "X509Data": {
        "X509Certificate": (1, _UNBOUNDED),
        "X509IssuerSerial": (0, 1),
        "X509SubjectName": (0, 1),
        "X509SKI": (0, 1),
    },
}
"""``INBOUND``'s elements whose children may come in any order: how many of each, at least and at most."""
_ATTRIBUTES: Final[Mapping[str, frozenset[str]]] = {
    "CanonicalizationMethod": frozenset({"Algorithm"}),
    "SignatureMethod": frozenset({"Algorithm"}),
    "Transform": frozenset({"Algorithm"}),
    "DigestMethod": frozenset({"Algorithm"}),
    "Reference": frozenset({"URI"}),
}
_IDENTIFIED: Final = frozenset(
    {"Signature", "SignedInfo", "Reference", "SignatureValue", "KeyInfo", "X509Data"}
)
"""Where ``INBOUND`` tolerates (and ignores) an ``Id``."""
_VALUES: Final = frozenset(
    {
        "DigestValue",
        "SignatureValue",
        "X509Certificate",
        "KeyName",
        "X509SubjectName",
        "X509IssuerName",
        "X509SerialNumber",
        "X509SKI",
        "Modulus",
        "Exponent",
    }
)
"""The only elements that hold text other than whitespace."""
_COMMENTS: Final = "the document holds a comment or a processing instruction inside its root"
_SINGLE: Final = "the document holds no single signature as its root's last child"
_XML_ATTRIBUTES: Final = "the document carries an xml: attribute on its root"
_PROFILE: Final = "the signature is not DGII's profile"
_NOT_BASE64: Final = "a value is not base64, or a certificate is not DER"
_CHANGED: Final = "the document changed after it was signed"
_NO_LEAF: Final = "the signature carries no single signing certificate"
_NOT_RSA: Final = "the certificate's key is not RSA"
_SHORT: Final = f"the certificate's key is shorter than {MIN_KEY_BITS} bits"
_CLAIMS: Final = "the key information names another certificate or key"
_MISMATCH: Final = "the signature value does not match the certificate's key"


class SignatureInvalidError(EcfInvalidError):
    """A document whose XML signature does not hold; the reason never quotes the document."""

    code: ClassVar[str] = "do_ecf_signature_invalid"

    def __init__(self, reason: str) -> None:
        super().__init__(f"the XML signature does not hold: {reason}")


@final
@dataclass(frozen=True, slots=True)
class VerifiedSignature:
    """A signature that holds: who made it, what else it carries, and its value.

    ``intermediates`` are the other certificates the signature carries: untrusted, to build a chain from,
    never trust anchors. ``signature_value`` is as written, whitespace removed.
    """

    certificate: x509.Certificate
    intermediates: tuple[x509.Certificate, ...]
    signature_value: str


@final
@dataclass(frozen=True, slots=True)
class _Node:
    """An element of the ``Signature``, read: its local name in the XMLDSig namespace, attributes, text."""

    name: str
    attributes: Mapping[str, str]
    text: str
    children: tuple[_Node, ...]

    def walk(self) -> Iterator[_Node]:
        """This element and every one under it, in document order."""
        yield self
        for child in self.children:
            yield from child.walk()

    def find(self, *path: str) -> _Node | None:
        """The first element down ``path``, by local names."""
        node: _Node | None = self
        for name in path:
            node = (
                None if node is None else next((child for child in node.children if child.name == name), None)
            )
        return node

    def value(self, *path: str) -> str | None:
        """The text of the element down ``path``, whitespace removed."""
        found = self.find(*path)
        return None if found is None else "".join(found.text.split())


@final
@dataclass(frozen=True, slots=True)
class _Parts:
    """What a signature's checks need: canonical forms, and the ``Signature`` element read."""

    canonical_document: bytes
    canonical_signed_info: bytes
    signature: _Node


def _blank(text: str | None) -> bool:
    return text is None or not text.strip()


def _children_hold(node: _Node, profile: VerificationProfile) -> bool:
    names = tuple(child.name for child in node.children)
    if profile is INBOUND and node.name in _COUNTED:
        rule = _COUNTED[node.name]
        counts = Counter(names)
        return set(counts) <= set(rule) and all(
            low <= counts[name] <= high for name, (low, high) in rule.items()
        )
    return names in _CHILDREN[profile].get(node.name, frozenset())


def _node_holds(node: _Node, profile: VerificationProfile) -> bool:
    allowed = _ATTRIBUTES.get(node.name, frozenset())
    if profile is INBOUND and node.name in _IDENTIFIED:
        allowed |= {"Id"}
    return (
        _children_hold(node, profile)
        and set(node.attributes) <= allowed
        and (node.name in _VALUES or _blank(node.text))
    )


def _shape_holds(signature: _Node, profile: VerificationProfile) -> bool:
    if not all(_node_holds(node, profile) for node in signature.walk()):
        return False
    signed_info = signature.children[0]
    algorithms = tuple(
        (node.name, node.attributes.get("Algorithm"))
        for node in signed_info.walk()
        if node.name in _ATTRIBUTES and node.name != "Reference"
    )
    reference = signed_info.children[2]
    return algorithms in PROFILES and reference.attributes.get("URI") == ""


def _parts(signed: bytes, profile: VerificationProfile) -> _Parts:
    """The parts of ``signed``'s signature, once its place and shape are the profile's (see the module)."""
    require_readable(signed)
    if COMMENT in signed:
        raise SignatureInvalidError(_COMMENTS)
    try:
        root = etree.fromstring(signed, parser())
    except etree.XMLSyntaxError as error:
        raise MalformedDocumentError from error
    require_plain(root.getroottree().docinfo)
    if any(True for _ in root.iter(etree.ProcessingInstruction)):
        raise SignatureInvalidError(_COMMENTS)
    if root.tag == SIGNATURE or len(root.findall(f".//{SIGNATURE}")) != 1 or root[-1].tag != SIGNATURE:
        raise SignatureInvalidError(_SINGLE)
    inherited = {name: value for name, value in root.attrib.items() if name.startswith(_XML)}
    if profile is STRICT and inherited:
        raise SignatureInvalidError(_XML_ATTRIBUTES)
    signature = root[-1]
    elements = list(signature.iter())
    read: dict[int, _Node] = {}
    # In reverse document order every element comes after its children: each is read from theirs.
    for element in reversed(elements):
        tag = element.tag
        if (
            not isinstance(tag, str)
            or not tag.startswith(_DS)
            or not all(_blank(child.tail) for child in element)
        ):
            raise SignatureInvalidError(_PROFILE)
        children = tuple(read.pop(id(child)) for child in element)
        read[id(element)] = _Node(tag.removeprefix(_DS), dict(element.attrib), element.text or "", children)
    node = read[id(signature)]
    if not _shape_holds(node, profile):
        raise SignatureInvalidError(_PROFILE)
    signed_info = signature[0]
    standalone = etree.Element(signed_info.tag, nsmap=signed_info.nsmap)
    copied = copy.deepcopy(signed_info)
    standalone.attrib.update(copied.attrib)
    standalone.attrib.update(inherited)
    standalone.text = copied.text
    standalone.extend(list(copied))
    canonical_signed_info = etree.tostring(standalone, method="c14n", exclusive=False, with_comments=False)
    tail, previous = signature.tail, signature.getprevious()
    root.remove(signature)
    if previous is None:
        root.text = (root.text or "") + (tail or "")
    else:
        previous.tail = (previous.tail or "") + (tail or "")
    canonical = etree.tostring(root.getroottree(), method="c14n", exclusive=False, with_comments=False)
    return _Parts(canonical, canonical_signed_info, node)


def _decoded(text: str) -> bytes:
    return base64.b64decode("".join(text.split()), validate=True)


def _signing_certificate(
    certificates: Sequence[x509.Certificate],
) -> tuple[x509.Certificate, tuple[x509.Certificate, ...]] | None:
    """The only certificate that issued no other in ``certificates``, and the others; ``None`` if not one."""
    leaves = [
        certificate
        for certificate in certificates
        if not any(other is not certificate and other.issuer == certificate.subject for other in certificates)
    ]
    if len(leaves) != 1:
        return None
    (leaf,) = leaves
    return leaf, tuple(certificate for certificate in certificates if certificate is not leaf)


def _subject_key_identifier(certificate: x509.Certificate) -> bytes | None:
    try:
        return certificate.extensions.get_extension_for_class(x509.SubjectKeyIdentifier).value.digest
    except x509.ExtensionNotFound:
        return None


def _claims_hold(key_info: _Node, certificate: x509.Certificate, key: RSAPublicKey) -> bool:
    """Whether what ``KeyInfo`` says beside the certificates names ``certificate`` and its ``key``.

    The serial number must be the certificate's; a name that cannot be read is skipped (``names``).
    """
    issuer_serial = key_info.find("X509Data", "X509IssuerSerial")
    subject = key_info.find("X509Data", "X509SubjectName")
    identifier = key_info.value("X509Data", "X509SKI")
    key_value = key_info.find("KeyValue", "RSAKeyValue")
    numbers = key.public_numbers()
    try:
        return (
            (
                issuer_serial is None
                or (
                    int(issuer_serial.children[1].text.strip()) == certificate.serial_number
                    and names_agree(issuer_serial.children[0].text, certificate.issuer)
                )
            )
            and (subject is None or names_agree(subject.text, certificate.subject))
            and (identifier is None or _decoded(identifier) == _subject_key_identifier(certificate))
            and (
                key_value is None
                or (
                    int.from_bytes(_decoded(key_value.children[0].text)) == numbers.n
                    and int.from_bytes(_decoded(key_value.children[1].text)) == numbers.e
                )
            )
        )
    except ValueError:
        return False


def verify_enveloped(signed: bytes, profile: VerificationProfile = STRICT) -> VerifiedSignature:
    """The signature of ``signed``, once it holds under ``profile`` (see the module).

    ``SignatureInvalidError`` otherwise, its reason never quoting the document.
    """
    parts = _parts(signed, profile)
    signature = parts.signature
    key_info = signature.children[2]
    try:
        digest = _decoded(signature.value("SignedInfo", "Reference", "DigestValue") or "")
        value = _decoded(signature.value("SignatureValue") or "")
        certificates = [
            x509.load_der_x509_certificate(_decoded(node.text))
            for node in key_info.walk()
            if node.name == "X509Certificate"
        ]
    except ValueError as error:
        raise SignatureInvalidError(_NOT_BASE64) from error
    if not hmac.compare_digest(hashlib.sha256(parts.canonical_document).digest(), digest):
        raise SignatureInvalidError(_CHANGED)
    chosen = _signing_certificate(certificates)
    if chosen is None:
        raise SignatureInvalidError(_NO_LEAF)
    certificate, intermediates = chosen
    if (problem := signing_problem(certificate)) is not None:
        raise SignatureInvalidError(problem)
    public_key = certificate.public_key()
    if not isinstance(public_key, RSAPublicKey):
        raise SignatureInvalidError(_NOT_RSA)
    if public_key.key_size < MIN_KEY_BITS:
        raise SignatureInvalidError(_SHORT)
    if not _claims_hold(key_info, certificate, public_key):
        raise SignatureInvalidError(_CLAIMS)
    try:
        public_key.verify(value, parts.canonical_signed_info, padding.PKCS1v15(), hashes.SHA256())
    except InvalidSignature as error:
        raise SignatureInvalidError(_MISMATCH) from error
    return VerifiedSignature(certificate, intermediates, signature.value("SignatureValue") or "")
