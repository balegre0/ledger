"""signxml as the second implementation of XML signatures: it verifies ours and signs what ours must verify.

signxml shares no code with ``adapters.signing``; the fake DGII uses it to check what it receives and to sign
the ARECFs it answers. ``excise_empty_xmlns_declarations`` makes signxml undo lxml's spurious ``xmlns=""``
(its issue 193), without which it cannot verify a signature in the default namespace. signxml resolves
``URI=""`` to the root element, so a document with nodes outside its root is verified by xmlsec1 instead; and
it does not carry the root's ``xml:`` attributes onto ``SignedInfo`` as C14N 1.0 says (xmlsec1 does), so it
neither verifies nor makes a signature of such a document that xmlsec1 accepts (``XMLSEC1_ONLY``). The signing
key is handed to signxml as an object: it is never serialized.
"""

import base64
from abc import ABCMeta, abstractmethod
from types import MappingProxyType
from typing import Final, Protocol, cast
lazy from collections.abc import Collection, Mapping

from cryptography import x509
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
lazy from cryptography.hazmat.primitives.asymmetric.types import PrivateKeyTypes
lazy from signxml.verifier import VerifyResult

from ledger_country_do.channels.ecf_v1.adapters.xml.parsing import DSIG, parser, require_readable, texts
lazy from ledger_country_do_testing.pki import Issued

OUTSIDE_ROOT: Final = "outside-root"
XML_ATTRIBUTES: Final = "xml-attributes"
XMLSEC1_ONLY: Final = frozenset({OUTSIDE_ROOT, XML_ATTRIBUTES})
"""The shapes signxml gets wrong (see the module): xmlsec1 alone verifies them."""
SHAPES: Final[Mapping[str, bytes]] = MappingProxyType(
    {
        "ecf": (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<ECF xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema">'
            "<Encabezado><Version>1.0</Version><IdDoc><TipoeCF>31</TipoeCF><eNCF>E310000000001</eNCF></IdDoc>"
            "<Emisor><RNCEmisor>101850043</RNCEmisor>"
            "<RazonSocialEmisor>Ferretería Duarte &amp; Hijos</RazonSocialEmisor></Emisor></Encabezado>"
            "<!-- a comment --><DetallesItems><Item><NombreItem>Martillo &lt;12&quot;&gt;</NombreItem></Item>"
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
        XML_ATTRIBUTES: b'<r xml:lang="es-DO" xml:space="preserve"><a xml:lang="en"> t </a></r>',
        OUTSIDE_ROOT: (
            b'<?xml version="1.0"?><?pi before?><!-- c --><r><!-- in --><?pi x?>t</r><!-- after -->'
        ),
    }
)
"""Documents of every shape a signature must survive: DGII's, namespaces on the root, default and nested
namespaces, character references, CR and tabs in attributes, astral characters, ``xml:`` attributes on the
root (which C14N 1.0 carries onto ``SignedInfo``), nodes outside the root."""
_CONFIGURATION: Final = SignatureConfiguration(
    require_x509=True,
    location="./",
    expect_references=1,
    signature_methods=frozenset({SignatureMethod.RSA_SHA256}),
    digest_algorithms=frozenset({DigestAlgorithm.SHA256}),
)
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
    # signxml types ``namespaces`` ``dict[str, str]`` yet documents ``None`` as the default namespace's key:
    # its signer is bound to ``_Signs`` through ``object``.
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
