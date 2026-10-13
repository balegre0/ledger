"""Verifying a signature in DGII's profile: our own documents, and what DGII's counterparts sign (ARECF).

Built against signature wrapping: what is verified is the whole document, and only one shape is accepted.

- The document is read hardened (``xml.parsing``) and holds exactly one ``Signature``, its root's last child
  (no comment or element after it); the root itself is not one.
- The ``Signature`` is DGII's shape and nothing more: ``SignedInfo`` (``CanonicalizationMethod``,
  ``SignatureMethod``, one ``Reference`` with ``URI=""`` only, its ``Transforms``, ``DigestMethod``,
  ``DigestValue``), ``SignatureValue``, and ``KeyInfo`` holding one ``X509Data`` with one ``X509Certificate``.
  No ``Object``, ``KeyValue``, ``Id``, other attribute, foreign element, comment, processing instruction or
  text but whitespace (and base64 in the three values). Its ``Algorithm`` attributes are one of
  ``profile.PROFILES``: the enveloped-signature transform, alone or followed by C14N 1.0, and nothing else.
- The digest is checked over the canonical form of the whole document (``URI=""``: the root and the processing
  instructions outside it) without the ``Signature`` element; the enveloped-signature transform removes the
  element, never the text around it.
- ``SignatureValue`` is checked with the RSA key (2048 bits at least) of the certificate in ``KeyInfo``, the
  only key the signature names. ``SignedInfo`` is canonicalized as a standalone copy declaring every namespace
  in scope where it sits and carrying its ancestors' ``xml:`` attributes (``profile.signed_info``'s reason).

Whether that certificate is one to trust (its chain to a DR authority, its subject's RNC) is the caller's
question: ``VerifiedSignature.certificate`` answers it; chain validation is v2-M4's. A caller reads what was
signed from the same bytes it passed here, never from another copy.
"""

import base64
import copy
import hashlib
import hmac
from dataclasses import dataclass
from typing import ClassVar, Final, final

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from lxml import etree

from ledger_country_do.channels.ecf_v1.adapters.signing.profile import MIN_KEY_BITS, PROFILES, XML_NAMESPACE
from ledger_country_do.channels.ecf_v1.adapters.xml.parsing import (
    DSIG,
    SIGNATURE,
    MalformedDocumentError,
    parser,
    require_plain,
    require_readable,
)
from ledger_country_do.channels.ecf_v1.domain.errors import EcfInvalidError

_DS: Final = f"{{{DSIG}}}"
_XML: Final = f"{{{XML_NAMESPACE}}}"
_ALGORITHM: Final = ("Algorithm",)
_TRANSFORM: Final = (4, "Transform", _ALGORITHM)
_HEAD: Final = (
    (0, "Signature", ()),
    (1, "SignedInfo", ()),
    (2, "CanonicalizationMethod", _ALGORITHM),
    (2, "SignatureMethod", _ALGORITHM),
    (2, "Reference", ("URI",)),
    (3, "Transforms", ()),
    _TRANSFORM,
)
_TAIL: Final = (
    (3, "DigestMethod", _ALGORITHM),
    (3, "DigestValue", ()),
    (1, "SignatureValue", ()),
    (1, "KeyInfo", ()),
    (2, "X509Data", ()),
    (3, "X509Certificate", ()),
)
_SKELETONS: Final = frozenset({(*_HEAD, *_TAIL), (*_HEAD, _TRANSFORM, *_TAIL)})
"""The ``Signature`` elements in document order, as (depth, local name, attribute names): DGII's shape, with
one transform or two."""
_VALUES: Final = frozenset({"DigestValue", "SignatureValue", "X509Certificate"})
"""The only elements that hold text other than whitespace."""
_SINGLE: Final = "the document holds no single signature as its root's last child"
_PROFILE: Final = "the signature is not DGII's profile"
_NOT_BASE64: Final = "a value is not base64, or the certificate is not DER"
_CHANGED: Final = "the document changed after it was signed"
_NOT_RSA: Final = "the certificate's key is not RSA"
_SHORT: Final = f"the certificate's key is shorter than {MIN_KEY_BITS} bits"
_MISMATCH: Final = "the signature value does not match the certificate's key"


class SignatureInvalidError(EcfInvalidError):
    """A document whose XML signature does not hold; the reason never quotes the document."""

    code: ClassVar[str] = "do_ecf_signature_invalid"

    def __init__(self, reason: str) -> None:
        super().__init__(f"the XML signature does not hold: {reason}")


@final
@dataclass(frozen=True, slots=True)
class VerifiedSignature:
    """A signature that holds: the certificate that made it, and its value as written (whitespace removed)."""

    certificate: x509.Certificate
    signature_value: str


@final
@dataclass(frozen=True, slots=True)
class _Parts:
    """What a signature's checks need, read from the document: canonical forms and the three values."""

    canonical_document: bytes
    canonical_signed_info: bytes
    digest: str
    value: str
    certificate: str


def _blank(text: str | None) -> bool:
    return text is None or not text.strip()


def _parts(signed: bytes) -> _Parts:
    """The parts of ``signed``'s signature, once its place and shape are DGII's (see the module)."""
    require_readable(signed)
    try:
        root = etree.fromstring(signed, parser())
    except etree.XMLSyntaxError as error:
        raise MalformedDocumentError from error
    require_plain(root.getroottree().docinfo)
    if root.tag == SIGNATURE or len(root.findall(f".//{SIGNATURE}")) != 1 or root[-1].tag != SIGNATURE:
        raise SignatureInvalidError(_SINGLE)
    signature = root[-1]
    skeleton: list[tuple[int, str, tuple[str, ...]]] = []
    for node in signature.iter():
        name = node.tag.removeprefix(_DS) if isinstance(node.tag, str) and node.tag.startswith(_DS) else None
        if (
            name is None
            or (name not in _VALUES and not _blank(node.text))
            or (node is not signature and not _blank(node.tail))
        ):
            raise SignatureInvalidError(_PROFILE)
        skeleton.append((sum(1 for _ in node.iterancestors()) - 1, name, tuple(sorted(node.attrib))))
    signed_info = signature[0]
    algorithms = tuple(
        (etree.QName(node).localname, node.get("Algorithm"))
        for node in signed_info.iter()
        if "Algorithm" in node.attrib
    )
    uris = [reference.get("URI") for reference in signed_info.iter(f"{_DS}Reference")]
    if tuple(skeleton) not in _SKELETONS or algorithms not in PROFILES or uris != [""]:
        raise SignatureInvalidError(_PROFILE)
    standalone = etree.Element(signed_info.tag, dict(signed_info.attrib), nsmap=signed_info.nsmap)
    # The shape allows no attribute on Signature: the root is the only ancestor whose xml: attributes C14N 1.0
    # copies onto SignedInfo.
    for attribute, value in root.attrib.items():
        if attribute.startswith(_XML):
            standalone.set(attribute, value)
    standalone.text = signed_info.text
    standalone.extend(copy.deepcopy(child) for child in signed_info)
    canonical_signed_info = etree.tostring(standalone, method="c14n", exclusive=False, with_comments=False)
    digest = signed_info.findtext(f"{_DS}Reference/{_DS}DigestValue", default="")
    value = signature.findtext(f"{_DS}SignatureValue", default="")
    certificate = signature.findtext(f"{_DS}KeyInfo/{_DS}X509Data/{_DS}X509Certificate", default="")
    tail, previous = signature.tail, signature.getprevious()
    root.remove(signature)
    if tail is not None:
        if previous is None:
            root.text = (root.text or "") + tail
        else:
            previous.tail = (previous.tail or "") + tail
    canonical = etree.tostring(root.getroottree(), method="c14n", exclusive=False, with_comments=False)
    return _Parts(canonical, canonical_signed_info, digest, value, certificate)


def _decoded(text: str) -> bytes:
    return base64.b64decode("".join(text.split()), validate=True)


def verify_enveloped(signed: bytes) -> VerifiedSignature:
    """The signature of ``signed``, once it holds (see the module); ``SignatureInvalidError`` otherwise."""
    parts = _parts(signed)
    try:
        digest, value = _decoded(parts.digest), _decoded(parts.value)
        certificate = x509.load_der_x509_certificate(_decoded(parts.certificate))
    except ValueError as error:
        raise SignatureInvalidError(_NOT_BASE64) from error
    if not hmac.compare_digest(hashlib.sha256(parts.canonical_document).digest(), digest):
        raise SignatureInvalidError(_CHANGED)
    public_key = certificate.public_key()
    if not isinstance(public_key, RSAPublicKey):
        raise SignatureInvalidError(_NOT_RSA)
    if public_key.key_size < MIN_KEY_BITS:
        raise SignatureInvalidError(_SHORT)
    try:
        public_key.verify(value, parts.canonical_signed_info, padding.PKCS1v15(), hashes.SHA256())
    except InvalidSignature as error:
        raise SignatureInvalidError(_MISMATCH) from error
    return VerifiedSignature(certificate=certificate, signature_value="".join(parts.value.split()))
