"""signxml as the second implementation of XML signatures; and documents signed as other signers sign them.

signxml shares no code with ``adapters.signing``; the fake DGII uses it to check what it receives and to sign
the ARECFs it answers. ``excise_empty_xmlns_declarations`` makes signxml undo lxml's spurious ``xmlns=""``
(its issue 193), without which it cannot verify a signature in the default namespace. signxml resolves
``URI=""`` to the root element, so a document with nodes outside its root is verified by xmlsec1 instead
(``XMLSEC1_ONLY``); nor does it carry the root's ``xml:`` attributes onto ``SignedInfo`` as C14N 1.0 says
(xmlsec1 does). The signing key is handed to signxml as an object: it is never serialized.

``forge`` signs a document with any variation of DGII's ``Signature`` a test asks for (an ``Id``, a
``KeyValue``, a chain, ``xml:`` attributes on the root), as counterparties' tools sign: the inbound profile's
tests verify what it makes, and xmlsec1 proves it right.
"""

import base64
import copy
import hashlib
from abc import ABCMeta, abstractmethod
from types import MappingProxyType
from typing import Final, Protocol, cast
lazy from collections.abc import Callable, Collection, Mapping

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.serialization import Encoding
from lxml import etree
from signxml.algorithms import (
    CanonicalizationMethod,
    DigestAlgorithm,
    SignatureConstructionMethod,
    SignatureMethod,
)
from signxml.exceptions import InvalidInput, InvalidSignature
from signxml.signer import XMLSigner
from signxml.verifier import SignatureConfiguration, XMLVerifier
lazy from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
lazy from cryptography.hazmat.primitives.asymmetric.types import PrivateKeyTypes
lazy from signxml.verifier import VerifyResult

from ledger_country_do.channels.ecf_v1.adapters.signing.profile import XML_NAMESPACE, signature
from ledger_country_do.channels.ecf_v1.adapters.xml.parsing import DSIG, parser, require_readable, texts
lazy from ledger_country_do_testing.pki import Issued

OUTSIDE_ROOT: Final = "outside-root"
XMLSEC1_ONLY: Final = frozenset({OUTSIDE_ROOT})
"""The shapes signxml gets wrong (see the module): xmlsec1 alone verifies them."""
SHAPES: Final[Mapping[str, bytes]] = MappingProxyType(
    {
        "ecf": (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<ECF xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema">'
            "<Encabezado><Version>1.0</Version><IdDoc><TipoeCF>31</TipoeCF><eNCF>E310000000001</eNCF></IdDoc>"
            "<Emisor><RNCEmisor>101850043</RNCEmisor>"
            "<RazonSocialEmisor>Ferretería Duarte &amp; Hijos</RazonSocialEmisor></Emisor></Encabezado>"
            "<DetallesItems><Item><NombreItem>Martillo &lt;12&quot;&gt;</NombreItem></Item>"
            "</DetallesItems><FechaHoraFirma>13-10-2026 09:30:00</FechaHoraFirma></ECF>"
        ).encode(),
        "seed": (
            b'<?xml version="1.0" encoding="utf-8"?><SemillaModel xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
            b' xmlns:xsd="http://www.w3.org/2001/XMLSchema"><valor>NmE1ZDg2YzQtMTc5OS00ZmM4</valor>'
            b"<fecha>2026-10-13T09:00:00.0000000-04:00</fecha></SemillaModel>"
        ),
        "plain": b"<a><b>x</b></a>",
        "default-namespace": b'<Doc xmlns="urn:d" a="1"><x:Part xmlns:x="urn:example:x">t</x:Part></Doc>',
        "nested-defaults": b'<r><x xmlns="urn:a"><y><z q="1"/></y></x><w xmlns:p="urn:p"><p:v/></w></r>',
        "references": b'<r>Pe\xc3\xb1a &#241; &#x1F600; &lt; &gt; &amp; "q"<e a="&#9;tab&#10;nl&#13;"/></r>',
        OUTSIDE_ROOT: (b'<?xml version="1.0"?><?pi before?>\n<r>t</r><?pi after?>'),
    }
)
"""Documents of every shape our signer signs and a signature must survive: DGII's, namespaces on the root,
default and nested namespaces, character references, CR and tabs in attributes, astral characters, processing
instructions outside the root. None holds a comment: the signer refuses them."""
_CONFIGURATION: Final = SignatureConfiguration(
    require_x509=True,
    location="./",
    expect_references=1,
    signature_methods=frozenset({SignatureMethod.RSA_SHA256}),
    digest_algorithms=frozenset({DigestAlgorithm.SHA256}),
)
_XML: Final = f"{{{XML_NAMESPACE}}}"
_CERTIFICATE: Final = f"{{{DSIG}}}Signature/{{{DSIG}}}KeyInfo/{{{DSIG}}}X509Data/{{{DSIG}}}X509Certificate"


class _Verifies(Protocol, metaclass=ABCMeta):
    """signxml's ``XMLVerifier`` as called here (signxml 5.1 leaves parts of ``verify`` unannotated)."""

    excise_empty_xmlns_declarations: bool

    @abstractmethod
    def verify(
        self, data: bytes, *, x509_cert: x509.Certificate, expect_config: SignatureConfiguration
    ) -> VerifyResult | list[VerifyResult]:
        """Verify ``data`` with ``x509_cert``."""


class _Signs(Protocol, metaclass=ABCMeta):
    """signxml's ``XMLSigner`` as called here (its ``namespaces`` takes ``None`` for the default one)."""

    namespaces: dict[str | None, str]

    @abstractmethod
    def sign(self, data: object, *, key: PrivateKeyTypes, cert: list[x509.Certificate]) -> etree.ElementBase:
        """``data``, an element, with an enveloped signature, as an element."""


def verify_with_signxml(signed: bytes, *, trusted: Collection[x509.Certificate] | None = None) -> bool:
    """Whether signxml finds ``signed``'s signature good, by the certificate it carries, if trusted.

    ``trusted`` ``None`` trusts the carried certificate (the local fake's mode: the owner's real certificate
    is unknown to it); the signature itself is always checked.
    """
    (carried,) = texts(signed, _CERTIFICATE)
    if carried is None:
        return False
    certificate = x509.load_der_x509_certificate(base64.b64decode("".join(carried.split())))
    if trusted is not None and certificate not in trusted:
        return False
    verifier = cast("_Verifies", XMLVerifier())
    verifier.excise_empty_xmlns_declarations = True
    try:
        verifier.verify(signed, x509_cert=certificate, expect_config=_CONFIGURATION)
    except InvalidSignature, InvalidInput:
        return False
    return True


def sign_with_signxml(document: bytes, issued: Issued, *, prefixed: bool = False) -> bytes:
    """``document`` signed by signxml (enveloped, C14N 1.0, RSA-SHA256), the namespace default or ``ds:``."""
    # signxml types ``namespaces`` ``dict[str, str]`` yet documents ``None`` as the default namespace's
    # key (https://github.com/XML-Security/signxml/issues/275): its signer is bound to ``_Signs`` through
    # ``object``.
    made = XMLSigner(
        method=SignatureConstructionMethod.enveloped,
        signature_algorithm=SignatureMethod.RSA_SHA256,
        digest_algorithm=DigestAlgorithm.SHA256,
        c14n_algorithm=CanonicalizationMethod.CANONICAL_XML_1_0,
    )
    signer = cast("_Signs", cast("object", made))
    if not prefixed:
        signer.namespaces = {None: DSIG}
    require_readable(document)
    signed = signer.sign(etree.fromstring(document, parser()), key=issued.key, cert=[issued.certificate])
    return etree.tostring(signed, xml_declaration=True, encoding="UTF-8")


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def forge(
    document: bytes,
    key: RSAPrivateKey,
    certificate: x509.Certificate,
    *,
    edit: Callable[[str], str] | None = None,
) -> bytes:
    """``document`` signed with ``key``, its ``Signature`` DGII's shape as ``edit`` changes it (as text).

    The digest and the signature value are computed after the edit, as a counterparty's tool would: inclusive
    C14N 1.0 of the document, then of ``SignedInfo`` with every namespace in scope and the root's ``xml:``
    attributes. For tests only: nothing checks what it is asked to sign.
    """
    require_readable(document)
    root = etree.fromstring(document, parser())
    tree = root.getroottree()
    digest = _b64(
        hashlib.sha256(etree.tostring(tree, method="c14n", exclusive=False, with_comments=False)).digest()
    )
    text = signature(digest, "", _b64(certificate.public_bytes(Encoding.DER)))
    root.append(etree.fromstring((text if edit is None else edit(text)).encode(), parser()))
    signed_info = root[-1][0]
    standalone = etree.Element(signed_info.tag, nsmap=signed_info.nsmap)
    standalone.attrib.update(signed_info.attrib)
    standalone.attrib.update({name: value for name, value in root.attrib.items() if name.startswith(_XML)})
    standalone.text = signed_info.text
    standalone.extend(copy.deepcopy(child) for child in signed_info)
    root[-1][1].text = _b64(
        key.sign(
            etree.tostring(standalone, method="c14n", exclusive=False, with_comments=False),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
    )
    return etree.tostring(tree, xml_declaration=True, encoding="UTF-8")
