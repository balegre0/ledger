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
properties.
"""

from typing import Final
from xml.sax.saxutils import quoteattr
lazy from collections.abc import Mapping

from ledger_country_do.channels.ecf_v1.adapters.xml.parsing import DSIG

C14N: Final = "http://www.w3.org/TR/2001/REC-xml-c14n-20010315"
RSA_SHA256: Final = "http://www.w3.org/2001/04/xmldsig-more#rsa-sha256"
SHA256: Final = "http://www.w3.org/2001/04/xmlenc#sha256"
ENVELOPED: Final = "http://www.w3.org/2000/09/xmldsig#enveloped-signature"
XML_NAMESPACE: Final = "http://www.w3.org/XML/1998/namespace"
"""The namespace of ``xml:lang``, ``xml:space``...: C14N 1.0 copies an ancestor's onto ``SignedInfo``."""
MIN_KEY_BITS: Final = 2048
_HEAD: Final = (("CanonicalizationMethod", C14N), ("SignatureMethod", RSA_SHA256), ("Transform", ENVELOPED))
PROFILES: Final = frozenset(
    {
        (*_HEAD, ("DigestMethod", SHA256)),
        (*_HEAD, ("Transform", C14N), ("DigestMethod", SHA256)),
    }
)
"""The ``Algorithm`` attributes of ``SignedInfo`` in document order, as DGII's profile allows them: DGII's own
shape, and the same with the canonicalization written out as a second transform (signxml, .NET)."""


def signed_info(
    digest: str, in_scope: Mapping[str, str] | None = None, *, inherited: Mapping[str, str] | None = None
) -> str:
    """``SignedInfo`` as text: as it sits in the document (``in_scope`` is ``None``), or standalone.

    Standalone, for canonicalization, it declares what is in scope where it sits: the default namespace of
    ``Signature`` and ``in_scope``, the prefixed namespaces of the document's root, and carries ``inherited``,
    the root's ``xml:`` attributes by local name, which C14N 1.0 copies onto the apex of a document subset.
    lxml's C14N of an element inside a document writes a spurious ``xmlns=""`` on grandchildren under a
    default namespace, which xmlsec1 and .NET reject (v1 M3 Decision 13), hence the standalone form.
    """
    if in_scope is None:
        declarations = ""
    else:
        prefixed = "".join(f" xmlns:{prefix}={quoteattr(uri)}" for prefix, uri in sorted(in_scope.items()))
        carried = "".join(
            f" xml:{name}={quoteattr(value)}" for name, value in sorted((inherited or {}).items())
        )
        declarations = f' xmlns="{DSIG}"{prefixed}{carried}'
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
