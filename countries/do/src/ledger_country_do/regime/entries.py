"""A regime's entries by key: its tax categories, document types and dated parameters.

The kernel's ``RegimeDef`` holds them as data; the pack reads them here. An entry the pack's own code names
and the regime does not declare, or declares with a value of another type, is a defect of the pack
(``RegimeEntryError``, an internal error): no client ever names one.
"""

from decimal import Decimal
from enum import StrEnum
from typing import ClassVar
lazy from datetime import date

from ledger_kernel.errors import LedgerError, excerpt
lazy from ledger_kernel.regimes import DocumentTypeDef, RegimeDef, TaxCategoryDef


class EntryKind(StrEnum):
    """The kinds of entry a regime declares, as an error names them."""

    TAX_CATEGORY = "tax category"
    DOCUMENT_TYPE = "document type"
    PARAMETER = "parameter"


class EntryProblem(StrEnum):
    """What is wrong with an entry the pack's code asked for."""

    UNDECLARED = "is not declared"
    NOT_AN_AMOUNT = "is not a decimal amount"


class RegimeEntryError(LedgerError):
    """The pack's code named an entry its regime does not hold as it needs: a defect of the pack."""

    code: ClassVar[str] = "do_regime_entry_invalid"

    def __init__(self, regime: str, kind: EntryKind, key: str, problem: EntryProblem) -> None:
        super().__init__(f"regime {regime!r} {kind} {excerpt(key)} {problem}")


def category(regime: RegimeDef, key: str) -> TaxCategoryDef:
    """The tax category ``key`` of ``regime``."""
    found = next((each for each in regime.tax_categories if each.key == key), None)
    if found is None:
        raise RegimeEntryError(regime.key, EntryKind.TAX_CATEGORY, key, EntryProblem.UNDECLARED)
    return found


def rate(regime: RegimeDef, key: str, day: date) -> Decimal:
    """The percent of tax category ``key`` in effect on ``day`` (``NotInEffectError`` when there is none)."""
    return category(regime, key).rates.at(day).percent


def document_type(regime: RegimeDef, code: str) -> DocumentTypeDef:
    """The document type ``code`` of ``regime``."""
    found = next((each for each in regime.document_types if each.code == code), None)
    if found is None:
        raise RegimeEntryError(regime.key, EntryKind.DOCUMENT_TYPE, code, EntryProblem.UNDECLARED)
    return found


def parameter(regime: RegimeDef, name: str, day: date) -> Decimal | int | str:
    """The parameter ``name`` of ``regime`` in effect on ``day`` (``NotInEffectError`` when there is none)."""
    timeline = regime.parameters.get(name)
    if timeline is None:
        raise RegimeEntryError(regime.key, EntryKind.PARAMETER, name, EntryProblem.UNDECLARED)
    return timeline.at(day)


def amount(regime: RegimeDef, name: str, day: date) -> Decimal:
    """The parameter ``name`` in effect on ``day``, which the pack declares as an amount."""
    value = parameter(regime, name, day)
    if type(value) is not Decimal:
        raise RegimeEntryError(regime.key, EntryKind.PARAMETER, name, EntryProblem.NOT_AN_AMOUNT)
    return value
