"""What changes invoicing's state: creating a document (a draft, or issued at once), issuing a draft, voiding.

Each use case asks identity what it needs first (the company, its establishment), outside any unit (rule 13),
prices the document with the regime of the company's jurisdiction, and then changes the state in one unit of
the call's tenant, which commits the change, its event and its audit record together or none of them (rule 3).
A refusal leaves nothing stored, announced or recorded.

Each use case is also two steps: ``prepare_*`` does what happens outside any unit and gives back a ``Change``,
which ``apply`` makes in a unit its caller opens. The HTTP edge needs that: it claims the request's
``Idempotency-Key`` in the very unit of the change (§9.5), which no unit opened by the use case could share.
``admits`` is what ``If-Match`` names (``IfMatch.admits``): a document at a version it does not admit is
refused (``InvoiceVersionStaleError``) before anything is decided on it.
"""

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Final, final
lazy from collections.abc import Awaitable, Callable
lazy from datetime import date
lazy from decimal import Decimal
lazy from uuid import UUID

from ledger_identity.api import CompanyInactiveError
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
from ledger_invoicing.domain.pricing import Terms, local_date, price
from ledger_kernel.errors import InvalidValueError
from ledger_kernel.tenancy import Tenant
lazy from ledger_identity.api import CompanyInfo
lazy from ledger_invoicing.application.ports import CompanyLookup, InvoicingUnit, InvoicingUnits, RegimeOf
lazy from ledger_invoicing.domain.lines import LineTerms
lazy from ledger_invoicing.domain.parties import Party
lazy from ledger_invoicing.domain.pricing import CountryFields, EstablishmentRef, Pricing
lazy from ledger_invoicing.domain.text import ExternalReference, Reason
lazy from ledger_kernel.clock import Clock
lazy from ledger_kernel.money import Currency
lazy from ledger_kernel.regimes import Direction, DocumentKind, RegimeDef
lazy from ledger_platform.runtime.calls import Call, SystemCall

_NO_FIELDS: Final[CountryFields] = MappingProxyType({})


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
    fiscal: CountryFields = field(default=_NO_FIELDS, hash=False)
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


def tenant_of(call: Call | SystemCall) -> Tenant:
    """The company and mode ``call`` acts in: a document exists in one mode of one company."""
    scope = call.scope
    if not isinstance(scope, Tenant) or scope.mode is None:
        raise InvalidValueError(field="call.scope", requirement="a company in a mode")
    return scope


def seller_of(company: CompanyInfo) -> Seller:
    """The company as a document's seller: its legal name and its primary tax id."""
    return Seller(legal_name=company.legal_name, tax_id=company.tax_ids[0])


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class InvoicingCommands:
    """The use cases that change documents, over the ports they need."""

    units: InvoicingUnits
    lookup: CompanyLookup
    regime_of: RegimeOf
    clock: Clock
    """The wall clock: what fixes "today" in an establishment's time zone. Times recorded are the unit's."""
    new_id: Callable[[], UUID]

    async def create(self, call: Call, creation: Creation) -> Invoice:
        """Create the document ``creation`` asks for in the call's tenant (``prepare_create``), in a unit."""
        return await self._made(await self.prepare_create(call, creation))

    async def issue(self, call: Call, invoice: InvoiceId) -> Invoice:
        """Issue the draft ``invoice`` (``prepare_issue``), in a unit."""
        return await self._made(await self.prepare_issue(call, invoice))

    async def void(self, call: Call, invoice: InvoiceId, reason: Reason | None) -> Invoice:
        """Void the issued document ``invoice``, for ``reason`` if given (``prepare_void``), in a unit."""
        return await self._made(self.prepare_void(call, invoice, reason))

    async def prepare_create(self, call: Call, creation: Creation) -> Change:
        """Create the document ``creation`` asks for in the call's tenant: a draft, or issued at once.

        Refused without a trace: for an inactive company (``CompanyInactiveError``), an establishment it lacks
        (``EstablishmentUnknownError``), terms its regime refuses, a note whose invoice it cannot correct
        (``CorrectionInvalidError``), or a business key a document of its type holds
        (``ExternalReferenceTakenError``, when it is applied).
        """
        tenant = tenant_of(call)
        require_correction_named(creation.kind, named=creation.corrects is not None)
        company = await self._company(call, tenant)
        terms = creation.terms(company.functional_currency)
        pricing = await self._priced(call, company, terms)
        opening = Opening(
            id=InvoiceId(self.new_id()),
            tenant=tenant,
            kind=creation.kind,
            direction=creation.direction,
            external_reference=creation.external_reference,
            terms=terms,
        )
        seller = None if creation.draft else seller_of(company)
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

        return Change(tenant, apply)

    async def prepare_issue(self, call: Call, invoice: InvoiceId, *, admits: Admits = any_version) -> Change:
        """Issue the draft ``invoice``, priced again on the day it is issued, sold by the company as it is.

        ``InvoiceNotFoundError`` for no document of the tenant by that id; ``InvoiceVersionStaleError`` for
        one at a version ``admits`` refuses; ``InvoiceNotIssuableError`` for one that is not a draft, issued
        by another call meanwhile too; ``InvoiceChangedError`` when it changed otherwise while it was priced.
        A note locks itself, then the invoice it corrects (the lock order).
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
        company = await self._company(call, tenant)
        pricing = await self._priced(call, company, draft.terms)
        seller = seller_of(company)

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

        return Change(tenant, apply)

    def prepare_void(
        self, call: Call, invoice: InvoiceId, reason: Reason | None, *, admits: Admits = any_version
    ) -> Change:
        """Void the issued document ``invoice``, for ``reason`` if given: nothing to ask outside a unit.

        ``InvoiceNotFoundError`` for no document of the tenant by that id; ``InvoiceVersionStaleError`` for
        one at a version ``admits`` refuses, once locked; ``InvoiceNotVoidableError`` for one not issued, one
        a channel accepted, or one a draft or issued note corrects (read once it is locked, so a note made
        meanwhile is seen).
        """

        async def apply(unit: InvoicingUnit) -> Invoice:
            current = await unit.invoices.locked(invoice)
            if current is None:
                raise InvoiceNotFoundError
            if not admits(current.version):
                raise InvoiceVersionStaleError(invoice)
            noted = await unit.invoices.corrected_by(current.id)
            voided, event = current.void(reason=reason, corrected_by_a_note=noted, now=unit.now)
            await unit.invoices.update(voided)
            unit.events.publish(event)
            detail = {"kind": voided.kind.value}
            unit.audit.record("invoice.void", voided.id, before=current, after=voided, detail=detail)
            return voided

        return Change(tenant_of(call), apply)

    async def _made(self, change: Change) -> Invoice:
        """``change`` made in a unit of its own tenant."""
        async with self.units.unit(change.tenant) as unit:
            return await change.apply(unit)

    async def _company(self, call: Call, tenant: Tenant) -> CompanyInfo:
        """The tenant's company, which must be active to make or issue a document."""
        company = await self.lookup.company(call, tenant.company)
        if not company.active:
            raise CompanyInactiveError(company.id)
        return company

    async def _priced(self, call: Call, company: CompanyInfo, terms: Terms) -> Pricing:
        """``terms`` priced with the company's regime, today in its establishment's time zone."""
        establishment = await self._establishment(call, company, terms.establishment)
        return price(
            terms,
            regime=self._regime(company),
            functional_currency=company.functional_currency,
            establishment=establishment,
            today=local_date(self.clock(), establishment.time_zone),
        )

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
