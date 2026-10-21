"""The canonical form of a document (spec §8.4 ``xml``): C14N 1.0, without comments.

What a signature covers: two documents equal under it are the same document to a verifier, whatever their
attribute order, quotes, empty-element tags or declaration. The document is read hardened (``hardened``).
"""

from lxml import etree

from ledger_fiscal_kit.xml.hardened import MalformedDocumentError, parser, require_plain, require_readable


def canonical(document: bytes, *, exclusive: bool = False) -> bytes:
    """``document``'s inclusive canonical form (C14N 1.0), or its exclusive one; comments dropped."""
    require_readable(document)
    try:
        root = etree.fromstring(document, parser())
    except etree.XMLSyntaxError:
        raise MalformedDocumentError from None
    tree = root.getroottree()
    require_plain(tree.docinfo)
    return etree.tostring(tree, method="c14n", exclusive=exclusive, with_comments=False)
