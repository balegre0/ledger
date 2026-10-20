"""What changes invoicing's state: creating a document (a draft, or issued at once), issuing a draft, voiding.

Each use case asks identity what it needs first (the company, its establishment), outside any unit (rule 13),
prices the document with the regime of the company's jurisdiction, and then changes the state in one unit of
the call's tenant, which commits the change, its event and its audit record together or none of them (rule 3).
A refusal leaves nothing stored, announced or recorded.

Each use case is two steps: ``prepare_*`` does what happens outside any unit and gives back a ``Change``,
which ``apply`` makes in a unit its caller opens, of the change's ``tenant``. The HTTP edge needs that: it
claims the request's ``Idempotency-Key`` in the very unit of the change (§9.5), which no unit opened by the
use case could share.
``admits`` is what ``If-Match`` names (``IfMatch.admits``): a document at a version it does not admit is
refused (``InvoiceVersionStaleError``) before anything is decided on it.

**The ``IssuePolicy`` chain** (§8.5 step 2). A change that issues a document (a creation that is no draft,
issuing a draft) builds, outside any unit, the very document its unit will store: its id minted, priced, sold
by the company, a note checked against its invoice as read (``Invoice.new``/``issue``), its times the clock's
(the unit's replace them). The chain checks that document (``IssuePolicies.check``) before ``prepare_*``
returns, so an objection refuses it with nothing stored; the unit still checks the note's invoice again, once
it holds it locked. With a chain, the keys of ``fiscal.<regime>`` the regime does not declare are the chain's
to judge (Decision 24). What runs once it is issued (``after_issue``) is the HTTP edge's, after the commit.
"""

from dataclasses import dataclass, field
from typing import final
lazy from collections.abc import Awaitable, Callable
lazy from datetime import date
lazy from decimal import Decimal
lazy from uuid import UUID

from ledger_identity.api import CompanyInactiveError
from ledger_invoicing.application.infos import info_of
from ledger_invoicing.domain.errors import (
    CorrectionInvalidError,
    CorrectionProblem,
    EstablishmentUnknownError,
    ExternalReferenceTakenError,
    InvoiceChangedError,
    InvoiceNotFoundError,
    InvoiceNotIssuableError,
    InvoiceVersionStaleError,
    RegimeMissingError,
)
from ledger_invoicing.domain.ids import InvoiceId
from ledger_invoicing.domain.invoice import Invoice, InvoiceStatus, Opening, require_correction_named
from ledger_invoicing.domain.parties import Seller
from ledger_invoicing.domain.pricing import NO_FIELDS, Dating, Terms, local_date, price, refuse_undeclared
from ledger_kernel.errors import InvalidValueError
from ledger_kernel.tenancy import Tenant
lazy from ledger_identity.api import CompanyInfo
lazy from ledger_invoicing.application.issuing import IssuePolicies
lazy from ledger_invoicing.application.ports import IdentityLookup, InvoicingUnit, InvoicingUnits, RegimeOf
lazy from ledger_invoicing.domain.lines import LineTerms
lazy from ledger_invoicing.domain.parties import Party
lazy from ledger_invoicing.domain.pricing import CountryFields, EstablishmentRef, Pricing
lazy from ledger_invoicing.domain.text import ExternalReference, Reason
lazy from ledger_kernel.clock import Clock
lazy from ledger_kernel.money import Currency
lazy from ledger_kernel.regimes import Direction, DocumentKind, RegimeDef
lazy from ledger_platform.runtime.calls import Call, SystemCall


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Creation:
    """A document a client asks to create, as it wrote it (``POST /v1/invoices``).

    What is ``None`` defaults: the currency to the company's functional one (Decision 5), the establishment to
    ``MAIN``, the dates when the document is priced (Decision 6). It is issued at once unless ``draft``
    (Decision 3).
    """

    kind: DocumentKind
    direction: Direction
    lines: tuple[LineTerms, ...]
    currency: Currency | None = None
    exchange_rate: Decimal | None = None
    issue_date: date | None = None
    tax_point: date | None = None
    establishment: str | None = None
    buyer: Party | None = None
    fiscal: CountryFields = field(default=NO_FIELDS, hash=False)
    external_reference: ExternalReference | None = None
    corrects: InvoiceId | None = None
    """The invoice a credit or debit note corrects."""
    draft: bool = False

    def terms(self, functional_currency: Currency) -> Terms:
        """The document's terms, in its currency or else the company's functional one."""
        return Terms(
            currency=functional_currency if self.currency is None else self.currency,
            lines=self.lines,
            exchange_rate=self.exchange_rate,
            issue_date=self.issue_date,
            tax_point=self.tax_point,
            establishment=self.establishment,
            buyer=self.buyer,
            fiscal=self.fiscal,
        )


type Admits = Callable[[int], bool]
"""Whether a change may apply to a document at a version (``If-Match``)."""


def any_version(version: int) -> bool:
    """What a request without ``If-Match`` admits: any version."""
    del version
    return True


@final
@dataclass(frozen=True, slots=True)
class Change:
    """A change decided outside any unit, made by ``apply`` in a unit of ``tenant`` that its caller opened."""

    tenant: Tenant
    apply: Callable[[InvoicingUnit], Awaitable[Invoice]]
    issues: bool = False
    """Whether it issues a document: what the ``IssuePolicy`` chain follows once it is committed."""


def tenant_of(call: Call | SystemCall) -> Tenant:
    """The company and mode ``call`` acts in: a document exists in one mode of one company."""
    scope = call.scope
    if not isinstance(scope, Tenant) or scope.mode is None:
        raise InvalidValueError(field="call.scope", requirement="a company in a mode")
    return scope


def seller_from_company(company: CompanyInfo) -> Seller:
    """The company as a document's seller: its legal name and its primary tax id."""
    return Seller(legal_name=company.legal_name, tax_id=company.tax_ids[0])


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class InvoicingCommands:
    """The use cases that change documents, over the ports they need."""

    units: InvoicingUnits
    lookup: IdentityLookup
    regime_of: RegimeOf
    clock: Clock
    """The wall clock: what fixes "today" in an establishment's time zone. Times recorded are the unit's."""
    new_id: Callable[[], UUID]
    policies: IssuePolicies
    """The ``IssuePolicy`` chain: what a document about to be issued is checked by."""

    async def prepare_create(self, call: Call, creation: Creation) -> Change:
        """Create the document ``creation`` asks for in the call's tenant: a draft, or issued at once.

        Refused without a trace: for an inactive company (``CompanyInactiveError``), an establishment it lacks
        (``EstablishmentUnknownError``), terms its regime refuses, a note whose invoice it cannot correct
        (``CorrectionInvalidError``), a document the ``IssuePolicy`` chain objects to unless it is a draft
        (``DocumentInvalidError``), or a business key a document of its type holds
        (``ExternalReferenceTakenError``, when it is applied).
        """
        tenant = tenant_of(call)
        require_correction_named(creation.kind, named=creation.corrects is not None)
        read = await self._corrected(tenant, creation.corrects)
        company = await self._company(call, tenant)
        terms = creation.terms(company.functional_currency)
        pricing = await self._priced(call, company, terms, read)
        opening = Opening(
            id=InvoiceId(self.new_id()),
            tenant=tenant,
            kind=creation.kind,
            direction=creation.direction,
            external_reference=creation.external_reference,
            terms=terms,
        )
        seller = None if creation.draft else seller_from_company(company)
        if seller is not None:
            prospective, _ = Invoice.new(
                opening, corrects=read, pricing=pricing, seller=seller, now=self.clock()
            )
            await self.policies.check(call, info_of(prospective))
        corrects = creation.corrects

        async def apply(unit: InvoicingUnit) -> Invoice:
            # Locked: a void of the invoice waits for this unit, or this one sees it voided.
            corrected = None if corrects is None else await unit.invoices.locked(corrects)
            if corrects is not None and corrected is None:
                raise CorrectionInvalidError(CorrectionProblem.NOT_FOUND)
            invoice, issued = Invoice.new(
                opening, corrects=corrected, pricing=pricing, seller=seller, now=unit.now
            )
            holder = await unit.invoices.add(invoice)
            if holder is not None:
                raise ExternalReferenceTakenError(holder)
            if issued is not None:
                unit.events.publish(issued)
            detail = {"kind": invoice.kind.value, "status": invoice.status.value}
            unit.audit.record("invoice.create", invoice.id, before=None, after=invoice, detail=detail)
            return invoice

        return Change(tenant, apply, issues=seller is not None)

    async def prepare_issue(self, call: Call, invoice: InvoiceId, *, admits: Admits = any_version) -> Change:
        """Issue the draft ``invoice``, priced again on the day it is issued, sold by the company as it is.

        ``InvoiceNotFoundError`` for no document of the tenant by that id; ``InvoiceVersionStaleError`` for
        one at a version ``admits`` refuses; ``InvoiceNotIssuableError`` for one that is not a draft, issued
        by another call meanwhile too; ``InvoiceChangedError`` when it changed otherwise while it was priced;
        ``DocumentInvalidError`` for one the ``IssuePolicy`` chain objects to, issued as it would be. A note
        locks itself, then the invoice it corrects (the lock order).
        """
        tenant = tenant_of(call)
        async with self.units.unit(tenant) as unit:
            draft = await unit.invoices.get(invoice)
        if draft is None:
            raise InvoiceNotFoundError
        if not admits(draft.version):
            raise InvoiceVersionStaleError(invoice)
        if draft.status is not InvoiceStatus.DRAFT:
            raise InvoiceNotIssuableError(draft.id)
        read = await self._corrected(tenant, draft.corrects)
        company = await self._company(call, tenant)
        pricing = await self._priced(call, company, draft.terms, read)
        seller = seller_from_company(company)
        prospective, _ = draft.issue(seller=seller, pricing=pricing, corrected=read, now=self.clock())
        await self.policies.check(call, info_of(prospective))

        async def apply(unit: InvoicingUnit) -> Invoice:
            current = await unit.invoices.locked(invoice)
            if current is not None and current.status is not InvoiceStatus.DRAFT:
                raise InvoiceNotIssuableError(current.id)  # issued (or voided) by another call meanwhile
            if current is None or current.version != draft.version:
                raise InvoiceChangedError(invoice)
            corrected = None if current.corrects is None else await unit.invoices.locked(current.corrects)
            issued, event = current.issue(seller=seller, pricing=pricing, corrected=corrected, now=unit.now)
            await unit.invoices.update(issued)
            unit.events.publish(event)
            detail = {"kind": issued.kind.value}
            unit.audit.record("invoice.issue", issued.id, before=current, after=issued, detail=detail)
            return issued

        return Change(tenant, apply, issues=True)

    def prepare_void(
        self, call: Call, invoice: InvoiceId, reason: Reason | None, *, admits: Admits = any_version
    ) -> Change:
        """Void the issued document ``invoice``, for ``reason`` if given: nothing to ask outside a unit.

        ``InvoiceNotFoundError`` for no document of the tenant by that id; ``InvoiceVersionStaleError`` for
        one at a version ``admits`` refuses, once locked; ``InvoiceNotVoidableError`` for one not issued, one
        a channel holds (any status but rejected or voided), or one a draft or issued note corrects (both read
        once it is locked, so a note made or a status applied meanwhile is seen).
        """

        async def apply(unit: InvoicingUnit) -> Invoice:
            current = await unit.invoices.locked(invoice)
            if current is None:
                raise InvoiceNotFoundError
            if not admits(current.version):
                raise InvoiceVersionStaleError(invoice)
            noted = await unit.invoices.corrected_by(current.id)
            held = await unit.fiscal.held(current.id)
            voided, event = current.void(reason=reason, corrected_by_a_note=noted, held=held, now=unit.now)
            await unit.invoices.update(voided)
            unit.events.publish(event)
            detail = {"kind": voided.kind.value}
            unit.audit.record("invoice.void", voided.id, before=current, after=voided, detail=detail)
            return voided

        return Change(tenant_of(call), apply)

    async def _company(self, call: Call, tenant: Tenant) -> CompanyInfo:
        """The tenant's company, which must be active to make or issue a document."""
        company = await self.lookup.company(call, tenant.company)
        if not company.active:
            raise CompanyInactiveError(company.id)
        return company

    async def _corrected(self, tenant: Tenant, corrects: InvoiceId | None) -> Invoice | None:
        """The invoice a note corrects, as read now (``None`` for an invoice), which the note is priced by.

        Read before the note is priced, in a unit of its own. An issued invoice is never priced again, so what
        is read here holds when the note's unit locks it and checks it again (``CorrectionInvalidError`` for
        one missing or not issued, here already).
        """
        if corrects is None:
            return None
        async with self.units.unit(tenant) as unit:
            corrected = await unit.invoices.get(corrects)
        if corrected is None:
            raise CorrectionInvalidError(CorrectionProblem.NOT_FOUND)
        if corrected.status is not InvoiceStatus.ISSUED:
            raise CorrectionInvalidError(CorrectionProblem.NOT_ISSUED)
        return corrected

    async def _priced(
        self, call: Call, company: CompanyInfo, terms: Terms, corrected: Invoice | None
    ) -> Pricing:
        """``terms`` priced with the company's regime, today in its establishment's time zone.

        A note's tax point defaults to the one of the invoice it corrects (``corrected``). The regime's
        undeclared country fields are the ``IssuePolicy`` chain's to judge, or refused without one.
        """
        establishment = await self._establishment(call, company, terms.establishment)
        regime = self._regime(company)
        pricing = price(
            terms,
            regime=regime,
            functional_currency=company.functional_currency,
            establishment=establishment,
            dating=Dating(
                today=local_date(self.clock(), establishment.time_zone),
                tax_point=None if corrected is None else corrected.pricing.tax_point,
            ),
        )
        if not self.policies.judges_fields():
            refuse_undeclared(pricing, regime)
        return pricing

    async def _establishment(self, call: Call, company: CompanyInfo, code: str | None) -> EstablishmentRef:
        found = await self.lookup.establishment(call, company.id, code)
        if found is None:
            raise EstablishmentUnknownError
        return found

    def _regime(self, company: CompanyInfo) -> RegimeDef:
        regime = self.regime_of(company.jurisdiction)
        if regime is None:
            raise RegimeMissingError
        return regime
