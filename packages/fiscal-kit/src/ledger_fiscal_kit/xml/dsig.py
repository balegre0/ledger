"""DGII's XML signature profile (*Firmado de e-CF*): enveloped XMLDSig, C14N 1.0, SHA-256, RSA-SHA256.

The ``Signature`` element, in the XMLDSig namespace as its default one, is the document root's last child::

    <Signature xmlns="http://www.w3.org/2000/09/xmldsig#"><SignedInfo>
      <CanonicalizationMethod Algorithm="http://www.w3.org/TR/2001/REC-xml-c14n-20010315"/>
      <SignatureMethod Algorithm="http://www.w3.org/2001/04/xmldsig-more#rsa-sha256"/>
      <Reference URI=""><Transforms>
        <Transform Algorithm="http://www.w3.org/2000/09/xmldsig#enveloped-signature"/>
      </Transforms><DigestMethod Algorithm="http://www.w3.org/2001/04/xmlenc#sha256"/>
      <DigestValue>...</DigestValue></Reference></SignedInfo>
      <SignatureValue>...</SignatureValue>
      <KeyInfo><X509Data><X509Certificate>...</X509Certificate></X509Data></KeyInfo></Signature>

written on one line, without the indentation. Not XAdES: DGII's documents and samples carry no XAdES
properties. The profile is generic (nothing in it is Dominican but its origin): the signer signs in it and a
channel's verifier checks it, so both share this module and the rule of what may sign.

**What may sign** (``signing_problem``): an end-entity certificate (not a certification authority) whose key
usage allows signing, ``digitalSignature`` or ``nonRepudiation`` (``content_commitment``), the latter what
qualified tax-procedure certificates assert. A certificate without a key-usage extension is refused: the
authorized authorities always write one. Whether the certificate is trusted (its chain, its validity, its
subject) is the caller's policy, not this rule.

**Signing, in two phases.** ``prepare`` checks the document and computes the reference's digest, SHA-256
over the document's inclusive canonical form (C14N 1.0, without comments) taken before the signature is added
(which is the reference over the whole document less the signature), and ``Prepared.to_sign``,
``SignedInfo``'s canonical form. Whoever holds the key makes RSASSA-PKCS1-v1_5 with SHA-256 over ``to_sign``
(a certification entity over its SHA-256 digest alone, so it never sees the document); ``Prepared.complete``
checks that value against the certificate and appends the signature. ``sign_enveloped`` is both phases with a
key in hand. A key is a value the caller passes in: nothing here loads one, writes one or names one in a
message (spec rule 7).

What is signed is exactly what a strict verifier accepts. The key is RSA of ``MIN_KEY_BITS`` at least and is
the certificate's own; the certificate may sign. The document is read hardened (``xml.hardened``) and holds no
comment (a comment inside a value would be signed as if it were not there), no processing instruction inside
its root and no ``xml:`` attribute on its root; a document already signed is refused (an enveloped signature
would leave it unsigned by this one). The signed document is the received one written again by lxml (UTF-8,
with its declaration): equal under canonicalization, which is all a signature covers.
"""

import base64
import hashlib
from typing import ClassVar, Final, final
from xml.sax.saxutils import quoteattr
lazy from collections.abc import Mapping

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey
from cryptography.hazmat.primitives.serialization import Encoding
from lxml import etree
lazy from cryptography.hazmat.primitives.asymmetric.types import PrivateKeyTypes

from ledger_fiscal_kit.xml.hardened import (
    COMMENT,
    DSIG,
    SIGNATURE,
    MalformedDocumentError,
    XmlDocumentError,
    parser,
    require_plain,
    require_readable,
)
from ledger_kernel.errors import InvalidInputError, InvalidValueError

C14N: Final = "http://www.w3.org/TR/2001/REC-xml-c14n-20010315"
RSA_SHA256: Final = "http://www.w3.org/2001/04/xmldsig-more#rsa-sha256"
SHA256: Final = "http://www.w3.org/2001/04/xmlenc#sha256"
ENVELOPED: Final = "http://www.w3.org/2000/09/xmldsig#enveloped-signature"
XML_NAMESPACE: Final = "http://www.w3.org/XML/1998/namespace"
"""The namespace of ``xml:lang``, ``xml:space``...: C14N 1.0 copies an ancestor's onto ``SignedInfo``."""
MIN_KEY_BITS: Final = 2048
"""The shortest RSA key signed or verified with."""
_HEAD: Final = (("CanonicalizationMethod", C14N), ("SignatureMethod", RSA_SHA256), ("Transform", ENVELOPED))
PROFILES: Final = frozenset(
    {
        (*_HEAD, ("DigestMethod", SHA256)),
        (*_HEAD, ("Transform", C14N), ("DigestMethod", SHA256)),
    }
)
"""The ``Algorithm`` attributes of ``SignedInfo`` in document order, as DGII's profile allows them: DGII's own
shape, and the same with the canonicalization written out as a second transform (signxml, .NET)."""
CERTIFICATION_AUTHORITY: Final = "the signing certificate is a certification authority's"
NOT_FOR_SIGNING: Final = "the signing certificate's key usage does not allow signing"
_XML: Final = f"{{{XML_NAMESPACE}}}"
_COMMENTS: Final = "a document to sign holds no comment and no processing instruction inside its root"
_XML_ATTRIBUTES: Final = "a document to sign carries no xml: attribute on its root"
_SHORT: Final = f"the signing key is shorter than {MIN_KEY_BITS} bits"
_NOT_ITS_KEY: Final = "the certificate is not the signing key's"
_MADE: Final = object()
"""``Prepared``'s token: only ``prepare`` holds it."""


class DocumentSignedError(XmlDocumentError):
    """A document that already holds a signature."""

    code: ClassVar[str] = "xml_document_signed"

    def __init__(self) -> None:
        super().__init__("the document already holds an XML signature")


class UnsignableDocumentError(XmlDocumentError):
    """A document the strict profile would not verify once signed; the reason never quotes it."""

    code: ClassVar[str] = "xml_document_unsignable"

    def __init__(self, reason: str) -> None:
        super().__init__(reason)


class NotAnRsaKeyError(InvalidInputError):
    """A key that is not RSA was handed in to sign: the profile signs with RSA only."""

    code: ClassVar[str] = "xml_signing_key_not_rsa"

    def __init__(self) -> None:
        super().__init__("the signature profile signs with RSA keys only")


class SigningPairError(InvalidInputError):
    """A key (or a signature value) and a certificate that cannot sign together; the reason names no key."""

    code: ClassVar[str] = "xml_signing_pair_refused"

    def __init__(self, reason: str) -> None:
        super().__init__(f"the signer cannot sign: {reason}")


def signed_info(digest: str, in_scope: Mapping[str, str] | None = None) -> str:
    """``SignedInfo`` as text: as it sits in the document (``in_scope`` is ``None``), or standalone.

    Standalone, for canonicalization, it declares what is in scope where it sits: the default namespace of
    ``Signature`` and ``in_scope``, the prefixed namespaces of the document's root. lxml's C14N of an element
    inside a document writes a spurious ``xmlns=""`` on grandchildren under a default namespace, which xmlsec1
    and .NET reject (v1 M3 Decision 13), hence the standalone form. The signer refuses ``xml:`` attributes on
    the root, which C14N 1.0 would carry onto ``SignedInfo``, so none is ever carried here.
    """
    if in_scope is None:
        declarations = ""
    else:
        prefixed = "".join(f" xmlns:{prefix}={quoteattr(uri)}" for prefix, uri in sorted(in_scope.items()))
        declarations = f' xmlns="{DSIG}"{prefixed}'
    return (
        f"<SignedInfo{declarations}>"
        f'<CanonicalizationMethod Algorithm="{C14N}"/>'
        f'<SignatureMethod Algorithm="{RSA_SHA256}"/>'
        f'<Reference URI=""><Transforms><Transform Algorithm="{ENVELOPED}"/></Transforms>'
        f'<DigestMethod Algorithm="{SHA256}"/><DigestValue>{digest}</DigestValue></Reference>'
        "</SignedInfo>"
    )


def signature(digest: str, value: str, certificate: str) -> str:
    """The ``Signature`` element as text, the XMLDSig namespace its default one."""
    return (
        f'<Signature xmlns="{DSIG}">{signed_info(digest)}'
        f"<SignatureValue>{value}</SignatureValue>"
        f"<KeyInfo><X509Data><X509Certificate>{certificate}</X509Certificate></X509Data></KeyInfo>"
        "</Signature>"
    )


def signing_problem(certificate: x509.Certificate) -> str | None:
    """Why ``certificate`` may not sign in this profile, or ``None`` when it may (see the module)."""
    try:
        if certificate.extensions.get_extension_for_class(x509.BasicConstraints).value.ca:
            return CERTIFICATION_AUTHORITY
    except x509.ExtensionNotFound:
        pass
    try:
        usage = certificate.extensions.get_extension_for_class(x509.KeyUsage).value
    except x509.ExtensionNotFound:
        return NOT_FOR_SIGNING
    return None if usage.digital_signature or usage.content_commitment else NOT_FOR_SIGNING


def require_signing_pair(public_key: RSAPublicKey, certificate: x509.Certificate) -> None:
    """Refuse a key that cannot sign with ``certificate``.

    It must be RSA of ``MIN_KEY_BITS`` at least and ``certificate``'s own, and ``certificate`` may sign.
    """
    if public_key.key_size < MIN_KEY_BITS:
        raise SigningPairError(_SHORT)
    own = certificate.public_key()
    if not isinstance(own, RSAPublicKey) or own.public_numbers() != public_key.public_numbers():
        raise SigningPairError(_NOT_ITS_KEY)
    if (problem := signing_problem(certificate)) is not None:
        raise SigningPairError(problem)


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _c14n(document: bytes) -> bytes:
    return etree.tostring(
        etree.fromstring(document, parser()), method="c14n", exclusive=False, with_comments=False
    )


@final
class Prepared:
    """A document ``prepare`` checked and digested: what it lacks is a signature value over ``to_sign``.

    Made by ``prepare`` alone (``made`` is its private token; anything else is refused, so no document reaches
    ``complete`` without ``prepare``'s checks). It holds the document, so it is never logged nor shown (its
    ``repr`` is the object's); completing it changes nothing in it, so it may be completed again.
    """

    __slots__ = ("_digest", "_document", "_to_sign")

    def __init__(self, made: object, document: bytes, digest: str, to_sign: bytes) -> None:
        if made is not _MADE:
            raise InvalidValueError(field="prepared", requirement="made by dsig.prepare")
        self._document = document
        self._digest = digest
        self._to_sign = to_sign

    @property
    def to_sign(self) -> bytes:
        """``SignedInfo``'s canonical form: what RSASSA-PKCS1-v1_5 with SHA-256 signs."""
        return self._to_sign

    def complete(self, signature_value: bytes, certificate: x509.Certificate) -> bytes:
        """The document signed, once ``signature_value`` over ``to_sign`` is the key of ``certificate``'s.

        ``NotAnRsaKeyError`` when ``certificate``'s key is not RSA; ``SigningPairError`` when the value is not
        its key's, or when ``certificate`` may not sign (see the module): a value made elsewhere (a
        certification entity's) is checked before it is written into a document.
        """
        public_key = certificate.public_key()
        if not isinstance(public_key, RSAPublicKey):
            raise NotAnRsaKeyError
        require_signing_pair(public_key, certificate)
        try:
            public_key.verify(signature_value, self._to_sign, padding.PKCS1v15(), hashes.SHA256())
        except InvalidSignature as error:
            raise SigningPairError(_NOT_ITS_KEY) from error
        root = etree.fromstring(self._document, parser())
        carried = signature(self._digest, _b64(signature_value), _b64(certificate.public_bytes(Encoding.DER)))
        root.append(etree.fromstring(carried, parser()))
        return etree.tostring(root.getroottree(), xml_declaration=True, encoding="UTF-8")


def prepare(document: bytes) -> Prepared:
    """Phase one: ``document`` checked as a strict verifier will read it once signed, and digested."""
    require_readable(document)
    if COMMENT in document:
        raise UnsignableDocumentError(_COMMENTS)
    try:
        root = etree.fromstring(document, parser())
    except etree.XMLSyntaxError as error:
        raise MalformedDocumentError from error
    tree = root.getroottree()
    require_plain(tree.docinfo)
    if any(True for _ in root.iter(etree.ProcessingInstruction)):
        raise UnsignableDocumentError(_COMMENTS)
    if any(name.startswith(_XML) for name in root.attrib):
        raise UnsignableDocumentError(_XML_ATTRIBUTES)
    if root.tag == SIGNATURE or root.find(f".//{SIGNATURE}") is not None:
        raise DocumentSignedError
    digest = _b64(
        hashlib.sha256(etree.tostring(tree, method="c14n", exclusive=False, with_comments=False)).digest()
    )
    in_scope = {prefix: uri for prefix, uri in root.nsmap.items() if prefix is not None}
    return Prepared(_MADE, document, digest, _c14n(signed_info(digest, in_scope).encode()))


def sign_enveloped(document: bytes, key: PrivateKeyTypes, certificate: x509.Certificate) -> bytes:
    """``document`` signed with ``key`` and ``certificate`` in the profile: both phases (see the module)."""
    if not isinstance(key, RSAPrivateKey):
        raise NotAnRsaKeyError
    require_signing_pair(key.public_key(), certificate)
    prepared = prepare(document)
    return prepared.complete(key.sign(prepared.to_sign, padding.PKCS1v15(), hashes.SHA256()), certificate)
