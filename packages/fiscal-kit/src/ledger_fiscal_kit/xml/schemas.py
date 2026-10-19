"""An authority's schemas, pinned, loaded with their corrections, and documents validated against them.

A channel vendors the XSDs its authority publishes, byte for byte, and pins each to its SHA-256
(``PinnedSchema``); a file that does not compile as published carries ``Correction``s, each the least edit
that makes it compile, applied when the schema is loaded and never to the file. A ``SchemaSet`` checks a pin
at its first use: bytes that changed (``SchemaChangedError``) or a correction that no longer applies as many
times as pinned (``CorrectionDriftError``) are a defect to fix, never a caller's mistake.

Validation runs on libxml2 (lxml's ``XMLSchema``, about half a millisecond for a 60-line e-CF); a channel's
tests may validate with ``xmlschema`` too, an implementation that shares no code with it. A compiled schema is
not shared between threads (lxml keeps per-object state).

A signed document ends with its XML signature, which a schema may demand (``xs:any``, at least once). A
document is checked before it is signed as it will be once signed: ``unsigned=True`` validates it with an
empty ``Signature`` element in the place the signature will take.
"""

import hashlib
import threading
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import ClassVar, Final, final, override
lazy from collections.abc import Callable, Mapping

from lxml import etree

from ledger_fiscal_kit.xml.hardened import (
    SIGNATURE,
    MalformedDocumentError,
    parser,
    require_plain,
    require_readable,
)
from ledger_kernel.errors import InvalidInputError, InvalidValueError, LedgerError, truncate

MAX_VIOLATIONS: Final = 20
"""How many problems a refusal lists."""
_MESSAGE: Final = 160
"""How much of libxml2's message a violation keeps: it names the element and facet, and may quote a value."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Correction:
    """The least edit that makes a published schema compile: ``wrong``, ``count`` times, becomes ``right``."""

    wrong: str
    right: str
    count: int
    reason: str


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class PinnedSchema[F: StrEnum]:
    """The schema of ``format`` exactly as its authority published it, with its SHA-256 and corrections."""

    format: F
    published: Callable[[], bytes] = field(repr=False)
    """Reads the file as published: called at the schema's first use, never when a set is built."""
    sha256: str
    """Of the bytes ``published`` reads, lowercase hexadecimal."""
    corrections: tuple[Correction, ...] = ()


class SchemaDriftError(LedgerError):
    """A vendored schema is not as pinned: a defect to fix, never a caller's mistake."""

    code: ClassVar[str] = "xml_schema_drift"


class SchemaChangedError(SchemaDriftError):
    """A vendored file is not the one whose SHA-256 its pin holds."""

    def __init__(self, format_: StrEnum) -> None:
        super().__init__(f"the schema {format_.value} is not the file pinned")


class CorrectionDriftError(SchemaDriftError):
    """A correction no longer applies as many times as pinned."""

    def __init__(self, format_: StrEnum, *, found: int, pinned: int) -> None:
        super().__init__(f"the schema {format_.value} holds {found} of a correction pinned at {pinned}")


@final
@dataclass(frozen=True, slots=True)
class Violation:
    """One way a document breaks its schema, as data.

    ``line`` and ``path`` (the element path: names, never values) say where; ``kind`` is libxml2's error type
    (``SCHEMAV_CVC_PATTERN_VALID``...). ``message`` is libxml2's own text, cut short: it may quote a value of
    the document (an amount, a tax id), so it stays out of every ``repr`` and error message; read it only
    where the document itself may be shown.
    """

    line: int
    path: str
    kind: str
    message: str = field(repr=False)

    @override
    def __str__(self) -> str:
        return f"{self.path} ({self.kind}, line {self.line})"


class SchemaViolationError(InvalidInputError):
    """A document its schema refuses: ``violations`` say where and how (at most ``MAX_VIOLATIONS``).

    Its message names each violation's path, kind and line, never libxml2's text (which may quote values).
    """

    code: ClassVar[str] = "xml_schema_invalid"

    def __init__(self, format_: StrEnum, found: tuple[Violation, ...]) -> None:
        self.violations: tuple[Violation, ...] = found
        listed = "; ".join(str(violation) for violation in found)
        super().__init__(f"the schema {format_.value} refuses the document: {listed}")


@final
class _Compiled[F: StrEnum](threading.local):
    """The schemas this thread compiled."""

    def __init__(self) -> None:
        self.schemas: dict[F, etree.XMLSchema] = {}


@final
class SchemaSet[F: StrEnum]:
    """The schemas of formats ``F``, each pinned (see the module)."""

    __slots__ = ("_compiled", "_pins")

    def __init__(self, pins: Mapping[F, PinnedSchema[F]]) -> None:
        for format_, pin in pins.items():
            if pin.format is not format_:
                raise InvalidValueError(field=f"pins[{format_.value}].format", requirement=format_.value)
        self._pins: Final[Mapping[F, PinnedSchema[F]]] = MappingProxyType(dict(pins))
        self._compiled: Final = _Compiled[F]()

    def _pin(self, format_: F) -> PinnedSchema[F]:
        found = self._pins.get(format_)
        if found is None:
            raise InvalidValueError(field="format", requirement="one the schema set pins")
        return found

    def corrected(self, format_: F) -> bytes:
        """The schema of ``format_`` with its corrections applied; ``SchemaDriftError`` if not as pinned."""
        pin = self._pin(format_)
        published = pin.published()
        if hashlib.sha256(published).hexdigest() != pin.sha256:
            raise SchemaChangedError(format_)
        text = published.decode("utf-8-sig")
        for correction in pin.corrections:
            found = text.count(correction.wrong)
            if found != correction.count:
                raise CorrectionDriftError(format_, found=found, pinned=correction.count)
            text = text.replace(correction.wrong, correction.right)
        return text.encode("utf-8")

    def schema(self, format_: F) -> etree.XMLSchema:
        """The compiled schema of ``format_`` for the calling thread."""
        compiled = self._compiled.schemas
        found = compiled.get(format_)
        if found is None:
            found = compiled[format_] = etree.XMLSchema(etree.fromstring(self.corrected(format_), parser()))
        return found

    def violations(self, format_: F, document: bytes, *, unsigned: bool = False) -> tuple[Violation, ...]:
        """How ``document`` breaks ``format_``'s schema, empty if it does not (``unsigned``: the module)."""
        require_readable(document)
        try:
            root = etree.fromstring(document, parser())
        except etree.XMLSyntaxError as error:
            raise MalformedDocumentError from error
        require_plain(root.getroottree().docinfo)
        if unsigned:
            etree.SubElement(root, SIGNATURE)
        checking = self.schema(format_)
        if checking.validate(root):
            return ()
        return tuple(
            Violation(
                line=entry.line,
                path=entry.path or "",
                kind=entry.type_name,
                message=truncate(entry.message, _MESSAGE),
            )
            for entry in list(checking.error_log)[:MAX_VIOLATIONS]
        )

    def checked(self, format_: F, document: bytes, *, unsigned: bool) -> bytes:
        """``document``, once ``format_``'s schema accepts it; ``SchemaViolationError`` otherwise."""
        found = self.violations(format_, document, unsigned=unsigned)
        if found:
            raise SchemaViolationError(format_, found)
        return document
