"""Signing in DGII's profile with a key object held by the caller (``profile``).

The spike's only signer (Coordination 8): v2-M2 moves this module to the signer module, where keys stay in the
signer process (spec rule 7). Nothing here loads a key from a file, writes one, or names one in a message.

The reference covers the whole document less the signature: its digest is SHA-256 over the document's
inclusive canonical form (C14N 1.0, without comments), taken before the signature is added, which is the same
thing. ``SignatureValue`` is RSASSA-PKCS1-v1_5 with SHA-256 over ``SignedInfo``'s canonical form. Documents
are read hardened (``xml.parsing``); a document already signed is refused (an enveloped signature would leave
it unsigned by this one); a seed must be DGII's ``SemillaModel`` (``valor``, ``fecha``). The signed document
is the received one written again by lxml (UTF-8, with its declaration): equal under canonicalization, which
is all a signature covers.
"""

import base64
import hashlib
from typing import ClassVar, Final

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from cryptography.hazmat.primitives.serialization import Encoding
from lxml import etree
lazy from cryptography import x509
lazy from cryptography.hazmat.primitives.asymmetric.types import PrivateKeyTypes

from ledger_country_do.channels.ecf_v1.adapters.signing.profile import XML_NAMESPACE, signature, signed_info
from ledger_country_do.channels.ecf_v1.adapters.xml.parsing import (
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


class DocumentSignedError(EcfInvalidError):
    """A document that already holds a signature."""

    code: ClassVar[str] = "do_ecf_document_signed"

    def __init__(self) -> None:
        super().__init__("the document already holds an XML signature")


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


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _c14n(document: bytes) -> bytes:
    return etree.tostring(
        etree.fromstring(document, parser()), method="c14n", exclusive=False, with_comments=False
    )


def sign_enveloped(
    document: bytes, key: PrivateKeyTypes, certificate: x509.Certificate, *, seed: bool = False
) -> bytes:
    """``document`` signed with ``key`` and ``certificate`` in DGII's profile (see the module)."""
    if not isinstance(key, RSAPrivateKey):
        raise NotAnRsaKeyError
    require_readable(document)
    try:
        root = etree.fromstring(document, parser())
    except etree.XMLSyntaxError as error:
        raise MalformedDocumentError from error
    tree = root.getroottree()
    require_plain(tree.docinfo)
    if root.tag == SIGNATURE or root.find(f".//{SIGNATURE}") is not None:
        raise DocumentSignedError
    children = [child.tag for child in root if isinstance(child.tag, str)]
    if seed and (root.tag != SEED_ROOT or children != list(SEED_CHILDREN)):
        raise SeedMalformedError
    digest = _b64(
        hashlib.sha256(etree.tostring(tree, method="c14n", exclusive=False, with_comments=False)).digest()
    )
    in_scope = {prefix: uri for prefix, uri in root.nsmap.items() if prefix is not None}
    inherited = {
        name.removeprefix(_XML): value for name, value in root.attrib.items() if name.startswith(_XML)
    }
    canonical_info = _c14n(signed_info(digest, in_scope, inherited=inherited).encode())
    value = _b64(key.sign(canonical_info, padding.PKCS1v15(), hashes.SHA256()))
    root.append(
        etree.fromstring(signature(digest, value, _b64(certificate.public_bytes(Encoding.DER))), parser())
    )
    return etree.tostring(tree, xml_declaration=True, encoding="UTF-8")
