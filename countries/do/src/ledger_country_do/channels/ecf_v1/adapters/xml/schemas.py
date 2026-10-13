"""DGII's schemas, loaded with their corrections, and the validation of documents against them.

Validation runs on libxml2 (lxml's ``XMLSchema``, about half a millisecond for a 60-line e-CF); the tests
and the fake DGII validate with ``xmlschema`` too, an implementation that shares no code with it. A compiled
schema is not shared between threads (lxml keeps per-object state).

Every DGII document ends with its XML signature, which each schema demands (``xs:any``, at least once). A
document is checked before it is signed as it will be once signed: ``unsigned=True`` validates it with an
empty ``Signature`` element in the place the signature will take.
"""

import hashlib
import threading
from dataclasses import dataclass
from importlib.resources import files
from typing import ClassVar, Final, final

from lxml import etree

from ledger_country_do.channels.ecf_v1.adapters.xml.parsing import (
    SIGNATURE,
    MalformedDocumentError,
    parser,
    require_readable,
)
from ledger_country_do.channels.ecf_v1.adapters.xsd.published import CORRECTIONS, PUBLISHED
from ledger_country_do.channels.ecf_v1.domain.errors import EcfError, EcfInvalidError, truncate
lazy from ledger_country_do.channels.ecf_v1.adapters.xsd.published import Format

MAX_VIOLATIONS: Final = 20
"""How many problems a refusal lists."""
_MESSAGE: Final = 160
"""How much of libxml2's message a violation keeps: it names the element and facet, and may quote a value."""
_XSD_PACKAGE: Final = "ledger_country_do.channels.ecf_v1.adapters.xsd"


class SchemaDriftError(EcfError):
    """A vendored schema is not as pinned: a defect to fix, never a caller's mistake."""

    code: ClassVar[str] = "do_ecf_schema_drift"


class SchemaChangedError(SchemaDriftError):
    """A vendored file is not the one whose SHA-256 ``published`` pins."""

    def __init__(self, format_: Format) -> None:
        super().__init__(f"DGII's schema {format_.value} is not the file pinned in adapters.xsd.published")


class CorrectionDriftError(SchemaDriftError):
    """A correction no longer applies as many times as pinned."""

    def __init__(self, format_: Format, *, found: int, pinned: int) -> None:
        super().__init__(f"DGII's schema {format_.value} holds {found} of a correction pinned at {pinned}")


@final
@dataclass(frozen=True, slots=True)
class Violation:
    """One way a document breaks its schema: where (line, element path) and what libxml2 says, cut short."""

    line: int
    path: str
    message: str


class SchemaViolationError(EcfInvalidError):
    """A document DGII's schema refuses, with where and why (at most ``MAX_VIOLATIONS``, each cut short)."""

    code: ClassVar[str] = "do_ecf_schema_invalid"

    def __init__(self, format_: Format, found: tuple[Violation, ...]) -> None:
        self.violations: tuple[Violation, ...] = found
        listed = "; ".join(f"{violation.path}: {violation.message}" for violation in found)
        super().__init__(f"DGII's schema {format_.value} refuses the document: {listed}")


def published_bytes(format_: Format) -> bytes:
    """The schema of ``format_`` exactly as DGII published it."""
    return files(_XSD_PACKAGE).joinpath(format_.value).read_bytes()


def corrected(format_: Format) -> bytes:
    """The schema of ``format_`` with its corrections applied; ``SchemaDriftError`` if it is not as pinned."""
    data = published_bytes(format_)
    if hashlib.sha256(data).hexdigest() != PUBLISHED[format_].sha256:
        raise SchemaChangedError(format_)
    text = data.decode("utf-8-sig")
    for correction in CORRECTIONS:
        if correction.format is not format_:
            continue
        found = text.count(correction.wrong)
        if found != correction.count:
            raise CorrectionDriftError(format_, found=found, pinned=correction.count)
        text = text.replace(correction.wrong, correction.right)
    return text.encode("utf-8")


@final
class _PerThread(threading.local):
    """The schemas this thread compiled."""

    def __init__(self) -> None:
        self.schemas: dict[Format, etree.XMLSchema] = {}


_THREAD: Final = _PerThread()


def schema(format_: Format) -> etree.XMLSchema:
    """The compiled schema of ``format_`` for the calling thread."""
    found = _THREAD.schemas.get(format_)
    if found is None:
        found = _THREAD.schemas[format_] = etree.XMLSchema(etree.fromstring(corrected(format_), parser()))
    return found


def violations(format_: Format, document: bytes, *, unsigned: bool = False) -> tuple[Violation, ...]:
    """How ``document`` breaks the schema of ``format_``, empty if it does not (``unsigned``: the module)."""
    require_readable(document)
    try:
        root = etree.fromstring(document, parser())
    except etree.XMLSyntaxError as error:
        raise MalformedDocumentError from error
    if unsigned:
        etree.SubElement(root, SIGNATURE)
    checking = schema(format_)
    if checking.validate(root):
        return ()
    return tuple(
        Violation(line=entry.line, path=entry.path or "", message=truncate(entry.message, _MESSAGE))
        for entry in list(checking.error_log)[:MAX_VIOLATIONS]
    )


def checked(format_: Format, document: bytes, *, unsigned: bool) -> bytes:
    """``document``, once ``format_``'s schema accepts it; ``SchemaViolationError`` otherwise."""
    found = violations(format_, document, unsigned=unsigned)
    if found:
        raise SchemaViolationError(format_, found)
    return document
