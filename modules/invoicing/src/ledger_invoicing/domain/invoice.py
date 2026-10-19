"""The commercial document (§7.5): an invoice, a credit or a debit note, of a sale or a self-billed purchase.

Its state machine is ``draft --issue--> issued --void--> voided``. A draft is replaced, not edited (Decision
3): it keeps the client's terms and their pricing as of its creation, and issuing it prices the terms again on
the day it is issued, with the seller as the company is then. Every change makes a new version (its ``ETag``)
and returns the event that announces it, if any.

A credit or debit note names the invoice it corrects: an issued invoice of the same company, mode and
direction (Decision 10; credit-note balances are a later slice). An issued document is voided only while no
channel accepted it (Decision 11): an accepted one is corrected by a note. Which channels accepted it is the
fiscal mirror's, written by ``fiscal`` from M3b; until then ``accepted_by`` stays empty and the refusal is
stated, not reachable through the API.
"""

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import final
lazy from datetime import datetime

from ledger_invoicing.domain.errors import (
    CorrectionInvalidError,
    CorrectionProblem,
    CorrectionRequiredError,
    CorrectionUnexpectedError,
    InvoiceNotIssuableError,
    InvoiceNotVoidableError,
)
from ledger_invoicing.events import InvoiceIssued, InvoiceVoided
from ledger_kernel.errors import InvalidValueError
from ledger_kernel.regimes import DocumentKind
lazy from ledger_invoicing.domain.ids import InvoiceId
lazy from ledger_invoicing.domain.parties import Seller
lazy from ledger_invoicing.domain.pricing import Pricing, Terms
lazy from ledger_invoicing.domain.text import ExternalReference, Reason
lazy from ledger_kernel.regimes import Direction
lazy from ledger_kernel.tenancy import Tenant


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Opening:
    """What a new document is, whose, and on which terms: what the client asked to create."""

    id: InvoiceId
    tenant: Tenant
    kind: DocumentKind
    direction: Direction
    external_reference: ExternalReference | None
    terms: Terms


class InvoiceStatus(StrEnum):
    """Where a document is in its life."""

    DRAFT = "draft"
    ISSUED = "issued"
    VOIDED = "voided"


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Invoice:
    """A commercial document at a version: each change returns it changed and the event that says so."""

    id: InvoiceId
    tenant: Tenant
    """Its organization, company and mode: a document exists in one mode."""
    kind: DocumentKind
    direction: Direction
    status: InvoiceStatus
    external_reference: ExternalReference | None
    corrects: InvoiceId | None
    """The invoice a note corrects; ``None`` for an invoice."""
    terms: Terms
    pricing: Pricing
    seller: Seller | None
    """The company as it was when the document was issued; ``None`` while a draft."""
    accepted_by: frozenset[str]
    """The channels that accepted the document (the fiscal mirror, M3b): empty until then."""
    void_reason: Reason | None
    version: int
    created_at: datetime
    updated_at: datetime
    issued_at: datetime | None
    voided_at: datetime | None

    def __post_init__(self) -> None:
        if self.version < 1:
            raise InvalidValueError(field="invoice.version", requirement="at least 1")
        if self.tenant.mode is None:
            raise InvalidValueError(field="invoice.tenant", requirement="a tenant with a mode")
        drafted = self.status is InvoiceStatus.DRAFT
        if drafted != (self.seller is None) or drafted != (self.issued_at is None):
            raise InvalidValueError(
                field="invoice.seller", requirement="set, with issued_at, exactly when it is not a draft"
            )
        voided = self.status is InvoiceStatus.VOIDED
        if voided != (self.voided_at is not None) or (self.void_reason is not None and not voided):
            raise InvalidValueError(
                field="invoice.voided_at", requirement="set exactly when it is voided, as its reason may be"
            )
        if (self.kind is DocumentKind.INVOICE) != (self.corrects is None):
            raise InvalidValueError(field="invoice.corrects", requirement="set exactly when it is a note")

    @classmethod
    def new(
        cls,
        opening: Opening,
        *,
        corrects: Invoice | None,
        pricing: Pricing,
        seller: Seller | None,
        now: datetime,
    ) -> tuple[Invoice, InvoiceIssued | None]:
        """A new document at version 1, priced as ``pricing``: a draft without ``seller``, issued with one.

        ``corrects`` is the document a note names, as the company's unit read it (``None``: it names none).
        """
        kind = opening.kind
        if kind is DocumentKind.INVOICE and corrects is not None:
            raise CorrectionUnexpectedError
        if kind is not DocumentKind.INVOICE and corrects is None:
            raise CorrectionRequiredError
        if corrects is not None:
            _require_correctable(corrects, tenant=opening.tenant, direction=opening.direction)
        draft = cls(
            id=opening.id,
            tenant=opening.tenant,
            kind=kind,
            direction=opening.direction,
            status=InvoiceStatus.DRAFT,
            external_reference=opening.external_reference,
            corrects=None if corrects is None else corrects.id,
            terms=opening.terms,
            pricing=pricing,
            seller=None,
            accepted_by=frozenset(),
            void_reason=None,
            version=1,
            created_at=now,
            updated_at=now,
            issued_at=None,
            voided_at=None,
        )
        if seller is None:
            return draft, None
        issued = _issued(draft, seller=seller, pricing=pricing, now=now, version=1)
        return issued, _announced(issued)

    def issue(
        self, *, seller: Seller, pricing: Pricing, corrected: Invoice | None, now: datetime
    ) -> tuple[Invoice, InvoiceIssued]:
        """The draft issued, priced as ``pricing`` and sold by ``seller``, at its next version.

        A note checks its invoice again (``corrected``, as the unit reads it now): it may have been voided
        since the draft was made.
        """
        if self.status is not InvoiceStatus.DRAFT:
            raise InvoiceNotIssuableError(self.id)
        if (None if corrected is None else corrected.id) != self.corrects:
            raise InvalidValueError(field="corrected", requirement="the invoice the note names")
        if corrected is not None:
            _require_correctable(corrected, tenant=self.tenant, direction=self.direction)
        issued = _issued(self, seller=seller, pricing=pricing, now=now, version=self.version + 1)
        return issued, _announced(issued)

    def void(self, *, reason: Reason | None, now: datetime) -> tuple[Invoice, InvoiceVoided]:
        """The issued document voided, for ``reason`` if given, at its next version."""
        if self.status is not InvoiceStatus.ISSUED or self.accepted_by:
            raise InvoiceNotVoidableError(self.id)
        voided = replace(
            self,
            status=InvoiceStatus.VOIDED,
            void_reason=reason,
            version=self.version + 1,
            updated_at=now,
            voided_at=now,
        )
        return voided, InvoiceVoided(
            scope=self.tenant,
            subject=self.id,
            aggregate_version=voided.version,
            kind=self.kind,
            direction=self.direction,
        )


def _require_correctable(corrected: Invoice, *, tenant: Tenant, direction: Direction) -> None:
    """Refuse a note's invoice unless it is an issued invoice of the note's tenant and direction.

    Another company's or mode's document is refused as a missing one: nothing about it is told.
    """
    if corrected.tenant != tenant:
        raise CorrectionInvalidError(CorrectionProblem.NOT_FOUND)
    if corrected.status is not InvoiceStatus.ISSUED:
        raise CorrectionInvalidError(CorrectionProblem.NOT_ISSUED)
    if corrected.kind is not DocumentKind.INVOICE:
        raise CorrectionInvalidError(CorrectionProblem.NOT_AN_INVOICE)
    if corrected.direction is not direction:
        raise CorrectionInvalidError(CorrectionProblem.OTHER_DIRECTION)


def _issued(invoice: Invoice, *, seller: Seller, pricing: Pricing, now: datetime, version: int) -> Invoice:
    """``invoice`` issued at ``version``: the state change alone, its rules checked by the caller."""
    return replace(
        invoice,
        status=InvoiceStatus.ISSUED,
        pricing=pricing,
        seller=seller,
        version=version,
        updated_at=now,
        issued_at=now,
    )


def _announced(invoice: Invoice) -> InvoiceIssued:
    """The event that says ``invoice`` is issued, at its current version."""
    return InvoiceIssued(
        scope=invoice.tenant,
        subject=invoice.id,
        aggregate_version=invoice.version,
        kind=invoice.kind,
        direction=invoice.direction,
        corrects=invoice.corrects,
    )
