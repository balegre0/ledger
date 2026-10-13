"""Reading XML that comes from outside, hardened (spec §8.4 ``xml``, §10).

A document is checked before it is parsed: at most ``MAX_DOCUMENT`` bytes, UTF-8 (DGII's documents are; any
other encoding, by byte order mark, by declaration or by its bytes, is refused, so no ``<!DOCTYPE`` can hide
in UTF-16), and no ``<!DOCTYPE`` anywhere in it (a DTD, internal or external, is never read: no entity
expansion, no external fetch). The parser then resolves no entity, loads no DTD, reaches no network, keeps
libxml2's own limits on depth and entity amplification (``huge_tree`` off) and drops comments: a comment
inside a value (``1<!---->0``) changes no signature (C14N without comments), so what is read must not see it
either. Once parsed, ``require_plain`` checks again what the parser saw: no DTD, and UTF-8. lxml parsers keep
per-object state, so each thread makes its own.

Nothing here returns an lxml element (their class is private to the type checkers): callers parse with
``parser()`` after ``require_readable`` and check the tree's ``docinfo`` with ``require_plain``, or ask
``texts`` for the values they need.
"""

import re
import threading
from typing import ClassVar, Final, final

from lxml import etree

from ledger_country_do.channels.ecf_v1.domain.errors import EcfInvalidError

DSIG: Final = "http://www.w3.org/2000/09/xmldsig#"
SIGNATURE: Final = f"{{{DSIG}}}Signature"
COMMENT: Final = b"<!--"
"""How every comment starts: character references cannot make markup, so these bytes find each one (and,
harmlessly, the same bytes inside a CDATA section or a processing instruction)."""
MAX_DOCUMENT: Final = 4 * 1024 * 1024
"""The largest document read: a type-32 e-CF of 1,000 lines is a few hundred kilobytes."""
_DOCTYPE: Final = re.compile(rb"<!DOCTYPE", re.IGNORECASE)
_UTF8_BOM: Final = b"\xef\xbb\xbf"
_DECLARED_ENCODING: Final = re.compile(rb"""<\?xml[^>]*?\sencoding\s*=\s*["']([^"']*)["']""")
_UTF8: Final = "utf-8"


class UnreadableDocumentError(EcfInvalidError):
    """Bytes that are not a document read here; each subclass says why, never quoting the document."""

    code: ClassVar[str] = "do_ecf_document_unreadable"


class DocumentTooLargeError(UnreadableDocumentError):
    """A document over the limit: refused unread."""

    code: ClassVar[str] = "do_ecf_document_too_large"

    def __init__(self, limit: int) -> None:
        super().__init__(f"a document is at most {limit} bytes")


class DoctypeRefusedError(UnreadableDocumentError):
    """A document that declares a DOCTYPE: no DTD, internal or external, is ever read."""

    code: ClassVar[str] = "do_ecf_document_doctype_refused"

    def __init__(self) -> None:
        super().__init__("a document that declares a DOCTYPE is refused")


class EncodingRefusedError(UnreadableDocumentError):
    """A document that is not UTF-8: by its byte order mark, its declaration or its bytes."""

    code: ClassVar[str] = "do_ecf_document_encoding_refused"

    def __init__(self) -> None:
        super().__init__("a document is read only in UTF-8")


class MalformedDocumentError(UnreadableDocumentError):
    """Not well-formed XML. The parser's message quotes the document, so it is not repeated."""

    code: ClassVar[str] = "do_ecf_document_malformed"

    def __init__(self) -> None:
        super().__init__("the document is not well-formed XML")


@final
class _PerThread(threading.local):
    """This thread's hardened parser."""

    def __init__(self) -> None:
        self.parser: etree.XMLParser = etree.XMLParser(
            resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False, remove_comments=True
        )


_THREAD: Final = _PerThread()


def parser() -> etree.XMLParser:
    """The calling thread's hardened parser (see the module)."""
    return _THREAD.parser


def require_readable(document: bytes, *, limit: int = MAX_DOCUMENT) -> None:
    """Refuse, unread, a document over ``limit`` bytes, not in UTF-8, or that declares a DOCTYPE."""
    if len(document) > limit:
        raise DocumentTooLargeError(limit)
    body = document.removeprefix(_UTF8_BOM)
    try:
        body.decode(_UTF8)
    except UnicodeDecodeError as error:
        raise EncodingRefusedError from error
    declared = _DECLARED_ENCODING.match(body)
    if b"\x00" in body or (declared is not None and declared[1].decode().lower() != _UTF8):
        raise EncodingRefusedError
    if _DOCTYPE.search(body) is not None:
        raise DoctypeRefusedError


def require_plain(info: etree.DocInfo) -> None:
    """Refuse, once parsed, a document the parser saw with a DTD or in an encoding other than UTF-8."""
    if info.doctype or info.internalDTD is not None or info.externalDTD is not None:
        raise DoctypeRefusedError
    if info.encoding.lower() != _UTF8:
        raise EncodingRefusedError


def texts(document: bytes, *paths: str) -> tuple[str | None, ...]:
    """The text at each of ``paths`` (ElementPath from the root, ``{namespace}name``); ``None`` if absent."""
    require_readable(document)
    try:
        root = etree.fromstring(document, parser())
    except etree.XMLSyntaxError as error:
        raise MalformedDocumentError from error
    require_plain(root.getroottree().docinfo)
    return tuple(root.findtext(path) for path in paths)
