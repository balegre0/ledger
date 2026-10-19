"""invoicing's errors: each with its kind and a stable code clients match on (§9.6).

Messages name fields by their path (``lines.3.quantity``), documents by their public id and limits by their
value, never what a client sent: a description, a buyer's name or tax id, a category key or an amount is the
client's data. ``InvalidValueError`` (``ledger_kernel.errors``) is for a value our own code built wrong.
"""

from enum import StrEnum
from typing import ClassVar
lazy from collections.abc import Mapping
lazy from uuid import UUID

from ledger_invoicing.domain.ids import INVOICE_PREFIX
from ledger_kernel.errors import ConflictError, InvalidInputError, LedgerError, NotFoundError
from ledger_kernel.ids import format_id


def _public(invoice: UUID) -> str:
    return format_id(INVOICE_PREFIX, invoice)


class InvalidTextError(InvalidInputError):
    """Text that cannot be kept as it is: empty, too long, or with an invisible character."""

    code: ClassVar[str] = "invalid_text"

    def __init__(self, field: str, problem: str) -> None:
        super().__init__(f"{field} {problem}")


class InvalidEmailError(InvalidInputError):
    """Text that is not an email address."""

    code: ClassVar[str] = "invalid_email"

    def __init__(self, field: str) -> None:
        super().__init__(f"{field} is not an email address of at most 254 characters")


class InvalidLineError(InvalidInputError):
    """A line field that breaks its rule: named by its path (``lines.3.taxes.1``) and what it must be."""

    code: ClassVar[str] = "invalid_line"

    def __init__(self, position: int, name: str, requirement: str, *, index: int | None = None) -> None:
        path = f"lines.{position}.{name}" if index is None else f"lines.{position}.{name}.{index}"
        super().__init__(f"{path} must be {requirement}")


class InvalidLinesError(InvalidInputError):
    """A document without lines, or with more than a document may have."""

    code: ClassVar[str] = "invalid_lines"

    def __init__(self, limit: int) -> None:
        super().__init__(f"a document has 1 to {limit} lines")


class TaxRateNotInEffectError(InvalidInputError):
    """A line names a category that has no rate in effect on the document's tax point."""

    code: ClassVar[str] = "tax_rate_not_in_effect"

    def __init__(self, position: int, index: int) -> None:
        super().__init__(f"lines.{position}.taxes.{index} has no rate in effect on the tax point")


class AmountOutOfRangeError(InvalidInputError):
    """An amount the document computes leaves the range money holds (below 10^18)."""

    code: ClassVar[str] = "amount_out_of_range"

    def __init__(self) -> None:
        super().__init__("an amount of the document is not below 10^18")


class InvalidExchangeRateError(InvalidInputError):
    """An exchange rate that is not a positive number with at most 12 decimal places."""

    code: ClassVar[str] = "invalid_exchange_rate"

    def __init__(self, places: int) -> None:
        requirement = f"a finite number above 0 and below 10^18, with at most {places} decimal places"
        super().__init__(f"exchange_rate must be {requirement}")


class CurrencyNotSupportedByRegimeError(InvalidInputError):
    """A currency whose minor unit has other places than the regime rounds to (Decision 8).

    The kernel's calculator rounds to the regime's places whatever the currency; until a regime says how it
    rounds a currency with other minor units (JPY's none, KWD's three), such a document is refused.
    """

    code: ClassVar[str] = "currency_not_supported_by_regime"

    def __init__(self, currency: str, places: int) -> None:
        super().__init__(
            f"{currency} has other minor units than the {places} places the company's regime rounds to"
        )


class InvalidIssueDateError(InvalidInputError):
    """An issue date after today where the document is issued: a document is not dated in the future."""

    code: ClassVar[str] = "invalid_issue_date"

    def __init__(self) -> None:
        super().__init__("issue_date must not be after today in the establishment's time zone")


class InvalidTaxPointError(InvalidInputError):
    """A tax point after the issue date, or further before it than a document may reach back."""

    code: ClassVar[str] = "invalid_tax_point"

    def __init__(self, days: int) -> None:
        super().__init__(f"tax_point must be on the issue date or at most {days} days before it")


class ExchangeRateRequiredError(InvalidInputError):
    """A document in a currency other than the company's functional one, without the rate between them."""

    code: ClassVar[str] = "exchange_rate_required"

    def __init__(self) -> None:
        super().__init__(
            "a document in another currency than the company's functional one needs exchange_rate"
        )


class ExchangeRateUnusedError(InvalidInputError):
    """An exchange rate sent with a document in the functional currency, where it means nothing."""

    code: ClassVar[str] = "exchange_rate_unused"

    def __init__(self) -> None:
        super().__init__("exchange_rate goes with a document in another currency than the functional one")


class FieldUnknownError(InvalidInputError):
    """A country field (``fiscal.<regime>.<field>``) the company's regime does not declare (§9.8)."""

    code: ClassVar[str] = "field_unknown"

    def __init__(self, regime: str, *, namespace: bool) -> None:
        super().__init__(
            f"fiscal holds a namespace other than the regime's, {regime}"
            if namespace
            else f"fiscal.{regime} holds a field the regime does not declare"
        )


class FieldRequiredError(InvalidInputError):
    """A country field the company's regime requires is missing."""

    code: ClassVar[str] = "field_required"

    def __init__(self, regime: str, name: str) -> None:
        super().__init__(f"fiscal.{regime}.{name} is required")


class FieldInvalidError(InvalidInputError):
    """A country field whose value is not of the kind its regime declares."""

    code: ClassVar[str] = "field_invalid"

    def __init__(self, regime: str, name: str, requirement: str) -> None:
        super().__init__(f"fiscal.{regime}.{name} must be {requirement}")


class CorrectionRequiredError(InvalidInputError):
    """A credit or debit note that does not name the invoice it corrects."""

    code: ClassVar[str] = "correction_required"

    def __init__(self) -> None:
        super().__init__("a credit or debit note names the invoice it corrects (corrects)")


class CorrectionUnexpectedError(InvalidInputError):
    """An invoice that names a document to correct: only a note corrects."""

    code: ClassVar[str] = "correction_unexpected"

    def __init__(self) -> None:
        super().__init__("an invoice corrects nothing: corrects goes with a credit or debit note")


class CorrectionProblem(StrEnum):
    """Why the document a note names cannot be corrected by it."""

    NOT_FOUND = "names no invoice of the company in this mode"
    """Another company's or another mode's document is not told apart from a missing one."""
    NOT_ISSUED = "is not an issued document"
    NOT_AN_INVOICE = "is a note, not an invoice"
    OTHER_DIRECTION = "is of the other direction"
    OTHER_CURRENCY = "is in another currency"


class CorrectionInvalidError(InvalidInputError):
    """A note names a document it cannot correct."""

    code: ClassVar[str] = "correction_invalid"

    def __init__(self, problem: CorrectionProblem) -> None:
        super().__init__(f"corrects {problem}")


class ExternalReferenceTakenError(ConflictError):
    """A document of the same company, mode and type holds the business key already (§9.5).

    ``existing_id`` is that document, which the problem names (``existing_id``) so a client can fetch it.
    """

    code: ClassVar[str] = "external_reference_taken"
    existing_id: UUID

    def __init__(self, existing: UUID) -> None:
        super().__init__(
            f"an invoice of this type holds this external_reference already: {_public(existing)}"
        )
        self.existing_id = existing

    def problem_members(self) -> Mapping[str, str]:
        """``existing_id``, the holder's public id: what the problem adds (§9.6)."""
        return {"existing_id": _public(self.existing_id)}


class InvoiceNotFoundError(NotFoundError):
    """No document by this id in the company and mode.

    What was asked for is never named: the id is the caller's, and may be another tenant's document.
    """

    code: ClassVar[str] = "invoice_not_found"

    def __init__(self) -> None:
        super().__init__("invoice was not found")


class InvoiceNotIssuableError(ConflictError):
    """Only a draft is issued."""

    code: ClassVar[str] = "invoice_not_issuable"

    def __init__(self, invoice: UUID) -> None:
        super().__init__(f"invoice {_public(invoice)} is not a draft")


class InvoiceNotVoidableError(ConflictError):
    """Only an issued document that no channel accepted and no standing note corrects is voided.

    An accepted one is corrected by a note; one a draft or issued note corrects keeps standing for it.
    """

    code: ClassVar[str] = "invoice_not_voidable"

    def __init__(self, invoice: UUID) -> None:
        super().__init__(
            f"invoice {_public(invoice)} is not issued, a channel accepted it, or a note corrects it"
        )


class InvoiceVersionStaleError(ConflictError):
    """The document is not at a version the request admits (``If-Match``): answered 412 by the HTTP edge."""

    code: ClassVar[str] = "invoice_version_stale"

    def __init__(self, invoice: UUID) -> None:
        super().__init__(f"invoice {_public(invoice)} is not at a version the request names")


class InvoiceChangedError(ConflictError):
    """The document changed between the read a use case decided on and its write."""

    code: ClassVar[str] = "invoice_changed"

    def __init__(self, invoice: UUID) -> None:
        super().__init__(f"invoice {_public(invoice)} changed meanwhile: read it again")


class EstablishmentUnknownError(InvalidInputError):
    """The document names an establishment its company does not have."""

    code: ClassVar[str] = "establishment_unknown"

    def __init__(self) -> None:
        super().__init__("establishment names no establishment of the company")


class RegimeMissingError(LedgerError):
    """No installed regime governs the company's jurisdiction: a defect of the installation, not the request.

    identity registers a company only in a jurisdiction an installed regime governs, so a missing one means a
    country pack was removed while its companies remained.
    """

    code: ClassVar[str] = "regime_missing"

    def __init__(self) -> None:
        super().__init__("no installed regime governs the company's jurisdiction")
