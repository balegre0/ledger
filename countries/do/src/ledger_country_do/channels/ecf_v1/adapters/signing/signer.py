"""Signing in DGII's profile with a key object held by the caller (``profile``).

The spike's only signer (Coordination 8): v2-M2 moves this module to the signer module, where keys stay in the
signer process (spec rule 7). Nothing here loads a key from a file, writes one, or names one in a message.

The reference covers the whole document less the signature: its digest is SHA-256 over the document's
inclusive canonical form (C14N 1.0, without comments), taken before the signature is added, which is the same
thing. ``SignatureValue`` is RSASSA-PKCS1-v1_5 with SHA-256 over ``SignedInfo``'s canonical form.

What is signed is exactly what the strict verifier accepts. The key is RSA of ``MIN_KEY_BITS`` at least and is
the certificate's own; the certificate signs documents (``certificates.signing_problem``). The document is
read hardened (``xml.parsing``) and holds no comment (a comment inside a value would be signed as if it were
not there), no processing instruction inside its root and no ``xml:`` attribute on its root; a document
already signed is refused (an enveloped signature would leave it unsigned by this one); a seed must be DGII's
``SemillaModel`` (``valor``, ``fecha``). The signed document is the received one written again by lxml (UTF-8,
with its declaration): equal under canonicalization, which is all a signature covers.
"""

import base64
import hashlib
from typing import ClassVar, Final

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey
from cryptography.hazmat.primitives.serialization import Encoding
from lxml import etree
lazy from cryptography import x509
lazy from cryptography.hazmat.primitives.asymmetric.types import PrivateKeyTypes

from ledger_country_do.channels.ecf_v1.adapters.signing.certificates import signing_problem
from ledger_country_do.channels.ecf_v1.adapters.signing.profile import (
    MIN_KEY_BITS,
    XML_NAMESPACE,
    signature,
    signed_info,
)
from ledger_country_do.channels.ecf_v1.adapters.xml.parsing import (
    COMMENT,
    SIGNATURE,
    MalformedDocumentError,
    parser,
    require_plain,
    require_readable,
)
from ledger_country_do.channels.ecf_v1.domain.errors import EcfError, EcfInvalidError

SEED_ROOT: Final = "SemillaModel"
SEED_CHILDREN: Final = ("valor", "fecha")
_XML: Final = f"{{{XML_NAMESPACE}}}"
_COMMENTS: Final = "a document to sign holds no comment and no processing instruction inside its root"
_XML_ATTRIBUTES: Final = "a document to sign carries no xml: attribute on its root"
_SHORT: Final = f"the signing key is shorter than {MIN_KEY_BITS} bits"
_NOT_ITS_KEY: Final = "the certificate is not the signing key's"


class DocumentSignedError(EcfInvalidError):
    """A document that already holds a signature."""

    code: ClassVar[str] = "do_ecf_document_signed"

    def __init__(self) -> None:
        super().__init__("the document already holds an XML signature")


class UnsignableDocumentError(EcfInvalidError):
    """A document DGII's strict profile would not verify once signed; the reason never quotes it."""

    code: ClassVar[str] = "do_ecf_document_unsignable"

    def __init__(self, reason: str) -> None:
        super().__init__(reason)


class SeedMalformedError(EcfInvalidError):
    """What was to be signed as a seed is not DGII's ``SemillaModel``."""

    code: ClassVar[str] = "do_ecf_seed_malformed"

    def __init__(self) -> None:
        super().__init__("a seed is a SemillaModel holding valor then fecha")


class NotAnRsaKeyError(EcfError):
    """A key that is not RSA reached the signer: DGII's profile signs with RSA only."""

    code: ClassVar[str] = "do_ecf_key_not_rsa"

    def __init__(self) -> None:
        super().__init__("DGII's profile signs with RSA keys only")


class SignerCertificateError(EcfError):
    """A key and certificate that cannot sign DGII's documents together: a defect of configuration."""

    code: ClassVar[str] = "do_ecf_signer_certificate"

    def __init__(self, reason: str) -> None:
        super().__init__(f"the signer cannot sign: {reason}")


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _c14n(document: bytes) -> bytes:
    return etree.tostring(
        etree.fromstring(document, parser()), method="c14n", exclusive=False, with_comments=False
    )


def _require_signing_pair(key: PrivateKeyTypes, certificate: x509.Certificate) -> RSAPrivateKey:
    if not isinstance(key, RSAPrivateKey):
        raise NotAnRsaKeyError
    if key.key_size < MIN_KEY_BITS:
        raise SignerCertificateError(_SHORT)
    public_key = certificate.public_key()
    if (
        not isinstance(public_key, RSAPublicKey)
        or public_key.public_numbers() != key.public_key().public_numbers()
    ):
        raise SignerCertificateError(_NOT_ITS_KEY)
    if (problem := signing_problem(certificate)) is not None:
        raise SignerCertificateError(problem)
    return key


def sign_enveloped(
    document: bytes, key: PrivateKeyTypes, certificate: x509.Certificate, *, seed: bool = False
) -> bytes:
    """``document`` signed with ``key`` and ``certificate`` in DGII's profile (see the module)."""
    signing_key = _require_signing_pair(key, certificate)
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
    children = [child.tag for child in root if isinstance(child.tag, str)]
    if seed and (root.tag != SEED_ROOT or children != list(SEED_CHILDREN)):
        raise SeedMalformedError
    digest = _b64(
        hashlib.sha256(etree.tostring(tree, method="c14n", exclusive=False, with_comments=False)).digest()
    )
    in_scope = {prefix: uri for prefix, uri in root.nsmap.items() if prefix is not None}
    canonical_info = _c14n(signed_info(digest, in_scope).encode())
    value = _b64(signing_key.sign(canonical_info, padding.PKCS1v15(), hashes.SHA256()))
    root.append(
        etree.fromstring(signature(digest, value, _b64(certificate.public_bytes(Encoding.DER))), parser())
    )
    return etree.tostring(tree, xml_declaration=True, encoding="UTF-8")
