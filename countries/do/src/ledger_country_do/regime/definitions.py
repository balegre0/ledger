"""The schema of a regime: provisional, local copies of the kernel v2 regime types (spec §5.1, §8.2).

The kernel v2, which will hold these types (``ledger_kernel.regimes``), is built by the restructure in
parallel with this spike; the spike keeps copies under the spec's names and fields and imports nothing of the
workspace. The integration milestone replaces this module with imports from the kernel.

Legal rules are effective-dated data (spec rule 12): a ``Timeline`` lists a value's versions newest first and
answers the one in force on a day. Standard library only.
"""

import itertools
from dataclasses import dataclass
from enum import StrEnum
from typing import final
lazy from collections.abc import Callable, Mapping
lazy from datetime import date
lazy from decimal import Decimal


class EntryKind(StrEnum):
    """The kinds of entry a regime declares, as its errors name them."""

    TAX_ID_SCHEME = "tax-id scheme"
    TAX_CATEGORY = "tax category"
    DOCUMENT_TYPE = "document type"
    PARAMETER = "parameter"


class RegimeDataError(ValueError):
    """A regime's data breaks the regime schema: a defect of the pack, raised when the pack is built."""


class _EmptyTimelineError(RegimeDataError):
    def __init__(self, name: str) -> None:
        super().__init__(f"{name}: a timeline holds at least one value")


class _UnorderedTimelineError(RegimeDataError):
    def __init__(self, name: str) -> None:
        super().__init__(f"{name}: versions are listed newest first, each after the next")


class _DuplicateEntryError(RegimeDataError):
    def __init__(self, regime: str, kind: EntryKind) -> None:
        super().__init__(f"{regime}: a {kind} is declared twice")


class _UncitedSourceError(RegimeDataError):
    def __init__(self, regime: str) -> None:
        super().__init__(f"{regime}: a value cites a source the regime does not list")


class NotInForceError(LookupError):
    """A dated value was asked for a day before its oldest version."""

    def __init__(self, name: str, day: date) -> None:
        super().__init__(f"{name} has no value in force on {day.isoformat()}")


class UnknownEntryError(LookupError):
    """A regime was asked for an entry it does not declare."""

    def __init__(self, kind: EntryKind, key: str) -> None:
        super().__init__(f"regime declares no {kind} {key!r}")


class TaxIdCheck(StrEnum):
    """What a scheme's check says of a text (ADR 0006)."""

    VALID = "valid"
    CHECKSUM_MISMATCH = "checksum_mismatch"
    """Well formed, but not the check digit the algorithm predicts: reported; refused only where enforced."""
    INVALID_FORMAT = "invalid_format"


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Source:
    """Where a value of a regime comes from."""

    title: str
    url: str
    version: str
    retrieved_on: date


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class TaxIdSchemeDef:
    """A tax-identifier scheme (``<cc>.<name>``).

    Its length, its check, and whether its check digit is enforced.
    """

    key: str
    label: str
    digits: int
    check_digit_enforced: bool
    check: Callable[[str], TaxIdCheck]
    source: Source


@final
@dataclass(frozen=True, slots=True)
class Since[T]:
    """A value in force from ``since`` (inclusive) until the next newer version of its timeline."""

    since: date
    value: T


@final
@dataclass(frozen=True, slots=True)
class Timeline[T]:
    """A value's versions, newest first; ``at(day)`` is the one in force on ``day``."""

    name: str
    entries: tuple[Since[T], ...]

    def __post_init__(self) -> None:
        if not self.entries:
            raise _EmptyTimelineError(self.name)
        for newer, older in itertools.pairwise(self.entries):
            if newer.since <= older.since:
                raise _UnorderedTimelineError(self.name)

    def at(self, day: date) -> T:
        """The version in force on ``day``; ``NotInForceError`` before the oldest one."""
        for entry in self.entries:
            if entry.since <= day:
                return entry.value
        raise NotInForceError(self.name, day)


class GenericCategory(StrEnum):
    """The country-neutral kind of a tax category (UBL-like; v1 M5 decision 3)."""

    STANDARD = "standard"
    REDUCED = "reduced"
    ZERO = "zero"
    EXEMPT = "exempt"
    OUTSIDE_SCOPE = "outside_scope"


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class TaxCategoryDef:
    """A tax category and its dated percent; ``rates`` is ``None`` for a category without one (exempt)."""

    key: str
    label: str
    generic: GenericCategory
    rates: Timeline[Decimal] | None
    source: Source

    def rate_at(self, day: date) -> Decimal | None:
        """The percent in force on ``day``; ``None`` for a category without a rate."""
        return None if self.rates is None else self.rates.at(day)


class DocumentKind(StrEnum):
    """The commercial kind a fiscal document type covers (spec §7.6)."""

    INVOICE = "invoice"
    CREDIT_NOTE = "credit_note"
    DEBIT_NOTE = "debit_note"


class Direction(StrEnum):
    """Whether the company sells (issues to a buyer) or buys (self-bills a seller)."""

    SALES = "sales"
    PURCHASE = "purchase"


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class DocumentTypeDef:
    """A fiscal document type of the regime (an e-CF type in the Dominican Republic)."""

    code: str
    label: str
    kind: DocumentKind
    direction: Direction
    requires_buyer_tax_id: bool
    source: Source


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class RoundingRule:
    """How amounts round, and how far a declared amount may stray before the authority flags it."""

    mode: str
    """A ``decimal`` rounding constant, such as ``decimal.ROUND_HALF_UP``."""
    places: int
    unit_price_places: int
    line_tolerance: Decimal
    global_tolerance_per_line: Decimal
    source: Source


type ParameterValue = Decimal | int


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class RegimeDef:
    """A regime: a jurisdiction's tax law as typed, dated data (spec §8.2)."""

    key: str
    country: str
    currency: str
    time_zone: str
    labels: Mapping[str, str]
    tax_id_schemes: tuple[TaxIdSchemeDef, ...]
    tax_categories: tuple[TaxCategoryDef, ...]
    document_types: tuple[DocumentTypeDef, ...]
    rounding: RoundingRule
    parameters: Mapping[str, Timeline[ParameterValue]]
    sources: tuple[Source, ...]

    def __post_init__(self) -> None:
        declared = (
            (EntryKind.TAX_ID_SCHEME, [scheme.key for scheme in self.tax_id_schemes]),
            (EntryKind.TAX_CATEGORY, [category.key for category in self.tax_categories]),
            (EntryKind.DOCUMENT_TYPE, [document.code for document in self.document_types]),
        )
        for kind, keys in declared:
            if len(set(keys)) != len(keys):
                raise _DuplicateEntryError(self.key, kind)
        cited = {
            *(scheme.source for scheme in self.tax_id_schemes),
            *(category.source for category in self.tax_categories),
            *(document.source for document in self.document_types),
            self.rounding.source,
        }
        if not cited <= set(self.sources):
            raise _UncitedSourceError(self.key)

    def category(self, key: str) -> TaxCategoryDef:
        """The tax category ``key``; ``UnknownEntryError`` if the regime declares none."""
        found = next((category for category in self.tax_categories if category.key == key), None)
        if found is None:
            raise UnknownEntryError(EntryKind.TAX_CATEGORY, key)
        return found

    def document_type(self, code: str) -> DocumentTypeDef:
        """The document type ``code``; ``UnknownEntryError`` if the regime declares none."""
        found = next((document for document in self.document_types if document.code == code), None)
        if found is None:
            raise UnknownEntryError(EntryKind.DOCUMENT_TYPE, code)
        return found

    def parameter(self, name: str, day: date) -> ParameterValue:
        """The parameter ``name`` in force on ``day``."""
        timeline = self.parameters.get(name)
        if timeline is None:
            raise UnknownEntryError(EntryKind.PARAMETER, name)
        return timeline.at(day)
