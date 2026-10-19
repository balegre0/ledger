"""invoicing's state in PostgreSQL: the repository, the unit, and what PostgreSQL alone proves.

Units are invoicing's own (``module_database``: ``invoicing_rw``, its schema); what they announced and
recorded is read from ``invoicing.outbox`` and ``invoicing.audit_events`` past row-level security (``admin``).
The in-memory fake of the unit tests serializes nothing: that a void and a note correcting the same invoice
wait for each other on its row lock is proven here.
"""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Final, final, override
lazy from collections.abc import AsyncGenerator, Awaitable, Callable

import pytest
from sqlalchemy import func, select
lazy import psycopg

from ledger_identity.api import CompanyInfo
from ledger_invoicing.adapters.postgres.repositories import (
    ForeignTenantError,
    StaleWriteError,
    UnmirroredAcceptanceError,
)
from ledger_invoicing.adapters.postgres.tables import (
    InvoiceLineRecord,
    InvoiceLineTaxRecord,
    InvoiceRecord,
    InvoiceTaxRecord,
)
from ledger_invoicing.adapters.postgres.unit import PgUnits, UnscopedUnitError, bind
from ledger_invoicing.application.commands import Creation, InvoicingCommands
from ledger_invoicing.application.ports import CompanyLookup, InvoiceFilters, Invoices, InvoicingUnits
from ledger_invoicing.domain.errors import CorrectionInvalidError, InvoiceNotVoidableError
from ledger_invoicing.domain.ids import InvoiceId
from ledger_invoicing.domain.invoice import Invoice, InvoiceStatus, Opening
from ledger_invoicing.domain.lines import LineTerms
from ledger_invoicing.domain.parties import Seller, party
from ledger_invoicing.domain.pricing import EstablishmentRef, Terms, price
from ledger_invoicing.domain.text import ExternalReference, parse_external_reference, parse_reason
from ledger_kernel.access import Principal, PrincipalKind, SubjectId
from ledger_kernel.ids import CompanyId, OrganizationId, new_id
from ledger_kernel.jurisdiction import parse_jurisdiction
from ledger_kernel.money import Currency
from ledger_kernel.pagination import PageRequest, decode_cursor
from ledger_kernel.regimes import Direction, DocumentKind
from ledger_kernel.tax_ids import TaxId, TaxIdScheme
from ledger_kernel.tenancy import Mode, OrganizationScope, Tenant
from ledger_platform.runtime.calls import Call
from ledger_testing.database import app_url
from ledger_testing.waiting import eventually, waits_for_a_lock
lazy from ledger_invoicing.application.ports import InvoicingUnit
lazy from ledger_invoicing.domain.pricing import CountryFields
lazy from ledger_kernel.pagination import Page
lazy from ledger_kernel.regimes import RegimeDef
lazy from ledger_platform.data.bound import BoundDatabase
lazy from ledger_testing.postgres import WorkerDatabase

NOW: Final = datetime(2026, 10, 12, 16, 0, tzinfo=UTC)
TODAY: Final = date(2026, 10, 12)
DOP: Final = Currency.of("DOP")
USD: Final = Currency.of("USD")
MAIN: Final = EstablishmentRef(code="MAIN", time_zone="America/Santo_Domingo")
RNC: Final = TaxId(TaxIdScheme("do.rnc"), "101850043")
SELLER: Final = Seller(legal_name="Acme SRL", tax_id=RNC)
WIDGET: Final = LineTerms(
    description="Widget", quantity=Decimal("2.5"), unit_price=Decimal("40.10"), taxes=("do.itbis.standard",)
)
SERVICE: Final = LineTerms(
    description="Service",
    quantity=Decimal(1),
    unit_price=Decimal("1000.000000000001"),
    discount=Decimal("0.5"),
    taxes=("do.itbis.reduced", "do.isc.standard"),
)
TERMS: Final = Terms(currency=DOP, lines=(WIDGET, SERVICE))


def tenant_of(mode: Mode = Mode.TEST, organization: OrganizationId | None = None) -> Tenant:
    """A new company's tenant (of a new organization unless ``organization`` is given)."""
    return Tenant(organization or OrganizationId(new_id()), CompanyId(new_id()), mode)


def _document(regime: RegimeDef, opening: Opening, *, corrects: Invoice | None, draft: bool) -> Invoice:
    """The document ``opening`` asks for, priced with ``regime`` as of ``TODAY``: a draft, or issued."""
    pricing = price(opening.terms, regime=regime, functional_currency=DOP, establishment=MAIN, today=TODAY)
    seller = None if draft else SELLER
    invoice, _ = Invoice.new(opening, corrects=corrects, pricing=pricing, seller=seller, now=NOW)
    return invoice


def _opening(tenant: Tenant, kind: DocumentKind, terms: Terms, reference: str | None) -> Opening:
    return Opening(
        id=InvoiceId(new_id()),
        tenant=tenant,
        kind=kind,
        direction=Direction.SALES,
        external_reference=None if reference is None else parse_external_reference(reference),
        terms=terms,
    )


def made(
    regime: RegimeDef,
    tenant: Tenant,
    *,
    terms: Terms = TERMS,
    draft: bool = False,
    reference: str | None = None,
) -> Invoice:
    """A new invoice of ``tenant``."""
    opening = _opening(tenant, DocumentKind.INVOICE, terms, reference)
    return _document(regime, opening, corrects=None, draft=draft)


def note(
    regime: RegimeDef,
    corrects: Invoice,
    *,
    kind: DocumentKind = DocumentKind.CREDIT_NOTE,
    draft: bool = False,
    reference: str | None = None,
) -> Invoice:
    """A new note of ``corrects``'s tenant correcting it."""
    opening = _opening(corrects.tenant, kind, TERMS, reference)
    return _document(regime, opening, corrects=corrects, draft=draft)


async def in_unit[T](
    database: BoundDatabase, tenant: Tenant, work: Callable[[InvoicingUnit], Awaitable[T]]
) -> T:
    """``work`` in one unit of ``tenant``, committed when it ends cleanly."""
    async with PgUnits(database).unit(tenant) as unit:
        return await work(unit)


async def kept(database: BoundDatabase, invoice: Invoice) -> InvoiceId | None:
    """Keep ``invoice`` in a unit of its own tenant: ``None``, or the holder of its business key."""
    return await in_unit(database, invoice.tenant, lambda unit: unit.invoices.add(invoice))


async def read(database: BoundDatabase, tenant: Tenant, invoice: InvoiceId) -> Invoice | None:
    return await in_unit(database, tenant, lambda unit: unit.invoices.get(invoice))


async def rows(database: BoundDatabase, tenant: Tenant) -> dict[str, int]:
    """How many rows of each of a document's tables ``tenant`` sees."""
    tables = (InvoiceRecord, InvoiceLineRecord, InvoiceLineTaxRecord, InvoiceTaxRecord)
    async with database.unit(tenant) as unit:
        return {
            table.__tablename__: (
                await unit.connection.execute(select(func.count()).select_from(table))
            ).scalar_one()
            for table in tables
        }


# ── Keeping and reading ─────────────────────────────────────────────────────────


async def test_an_issued_document_is_kept_and_read_back_whole(
    module_database: BoundDatabase, regime_with_fields: RegimeDef
) -> None:
    tenant = tenant_of()
    buyer = party(
        legal_name="Beta SRL",
        tax_id=TaxId(TaxIdScheme("do.rnc"), "130000012"),
        address="Calle 1, Santo Domingo",
        email="compras@beta.example",
    )
    fields: CountryFields = {"do": {"ecf_type": "31", "note": "Pedido 7", "replaces": {"ncf": "B0100000123"}}}
    terms = replace(
        TERMS,
        currency=USD,
        exchange_rate=Decimal("60.123456"),
        issue_date=date(2026, 10, 10),
        tax_point=date(2026, 10, 1),
        establishment="MAIN",
        buyer=buyer,
        fiscal=fields,
    )
    invoice = made(regime_with_fields, tenant, terms=terms, reference="ORD-1")
    assert invoice.pricing.functional is not None
    assert await kept(module_database, invoice) is None
    assert await read(module_database, tenant, invoice.id) == invoice
    assert await rows(module_database, tenant) == {
        "invoices": 1,
        "invoice_lines": 2,
        "invoice_line_taxes": 3,
        "invoice_taxes": 3,
    }


async def test_a_draft_is_kept_without_its_seller_and_with_the_terms_it_defaults(
    module_database: BoundDatabase, regime: RegimeDef
) -> None:
    tenant = tenant_of()
    draft = made(regime, tenant, draft=True)
    assert await kept(module_database, draft) is None
    found = await read(module_database, tenant, draft.id)
    assert found == draft
    assert found is not None
    assert (found.seller, found.terms.issue_date, found.terms.buyer) == (None, None, None)


async def test_a_document_is_read_by_its_own_tenant_only(
    module_database: BoundDatabase, regime: RegimeDef
) -> None:
    tenant = tenant_of()
    invoice = made(regime, tenant)
    await kept(module_database, invoice)
    for other in (
        Tenant(tenant.organization, tenant.company, Mode.LIVE),
        Tenant(tenant.organization, CompanyId(new_id()), Mode.TEST),
        tenant_of(),
    ):
        assert await read(module_database, other, invoice.id) is None
        assert await rows(module_database, other) == dict.fromkeys(
            ("invoices", "invoice_lines", "invoice_line_taxes", "invoice_taxes"), 0
        )
    assert await read(module_database, tenant, InvoiceId(new_id())) is None


async def test_a_business_key_is_held_once_per_tenant_and_type_and_its_holder_named(
    module_database: BoundDatabase, regime: RegimeDef
) -> None:
    tenant = tenant_of()
    first = made(regime, tenant, reference="ORD-1")
    assert await kept(module_database, first) is None
    again = made(regime, tenant, reference="ORD-1", draft=True)
    assert await kept(module_database, again) == first.id
    assert await read(module_database, tenant, again.id) is None
    assert (await rows(module_database, tenant))["invoice_lines"] == 2  # nothing of the second was kept
    credit = note(regime, first, reference="ORD-1")
    live = made(regime, Tenant(tenant.organization, tenant.company, Mode.LIVE), reference="ORD-1")
    sibling = made(regime, tenant_of(organization=tenant.organization), reference="ORD-1")
    unkeyed = [made(regime, tenant), made(regime, tenant)]
    for other in (credit, live, sibling, *unkeyed):
        assert await kept(module_database, other) is None


async def test_a_unit_keeps_the_documents_of_its_own_tenant_only(
    module_database: BoundDatabase, regime: RegimeDef
) -> None:
    tenant = tenant_of()
    for foreign in (
        made(regime, Tenant(tenant.organization, tenant.company, Mode.LIVE)),
        made(regime, tenant_of(organization=tenant.organization)),
    ):

        async def add(unit: InvoicingUnit, document: Invoice = foreign) -> None:
            await unit.invoices.add(document)

        async def change(unit: InvoicingUnit, document: Invoice = foreign) -> None:
            await unit.invoices.update(document)

        with pytest.raises(ForeignTenantError) as caught:
            await in_unit(module_database, tenant, add)
        assert str(caught.value) == "a unit keeps the documents of its own company and mode only"
        with pytest.raises(ForeignTenantError):
            await in_unit(module_database, tenant, change)
    assert (await rows(module_database, tenant))["invoices"] == 0


async def test_a_document_a_channel_accepted_is_not_kept_before_the_fiscal_mirror_exists(
    module_database: BoundDatabase, regime: RegimeDef
) -> None:
    tenant = tenant_of()
    accepted = replace(made(regime, tenant), accepted_by=("do-ecf-v1",))
    with pytest.raises(UnmirroredAcceptanceError) as caught:
        await kept(module_database, accepted)
    assert caught.value.code == "invoice_acceptance_unmirrored"
    with pytest.raises(UnmirroredAcceptanceError):
        await in_unit(module_database, tenant, lambda unit: unit.invoices.update(accepted))


# ── Changing ────────────────────────────────────────────────────────────────────


async def test_an_issued_draft_is_kept_at_its_next_version_priced_anew(
    module_database: BoundDatabase, regime: RegimeDef
) -> None:
    tenant = tenant_of()
    draft = made(regime, tenant, draft=True, terms=replace(TERMS, lines=(WIDGET,)))
    await kept(module_database, draft)
    later = price(
        draft.terms,
        regime=regime,
        functional_currency=DOP,
        establishment=MAIN,
        today=TODAY + timedelta(days=2),
    )
    issued, _ = draft.issue(seller=SELLER, pricing=later, corrected=None, now=NOW + timedelta(days=2))

    async def issue(unit: InvoicingUnit) -> None:
        locked = await unit.invoices.locked(draft.id)
        assert locked == draft
        await unit.invoices.update(issued)

    await in_unit(module_database, tenant, issue)
    assert await read(module_database, tenant, draft.id) == issued
    assert (await rows(module_database, tenant))["invoice_lines"] == 1
    voided, _ = issued.void(reason=parse_reason("Wrong buyer"), corrected_by_a_note=False, now=NOW)
    await in_unit(module_database, tenant, lambda unit: unit.invoices.update(voided))
    assert await read(module_database, tenant, draft.id) == voided


async def test_a_change_that_skips_a_version_is_a_defect_and_changes_nothing(
    module_database: BoundDatabase, regime: RegimeDef
) -> None:
    tenant = tenant_of()
    invoice = made(regime, tenant)
    await kept(module_database, invoice)
    voided, _ = invoice.void(reason=None, corrected_by_a_note=False, now=NOW)
    for wrong in (replace(voided, version=3), replace(voided, id=InvoiceId(new_id()))):

        async def change(unit: InvoicingUnit, document: Invoice = wrong) -> None:
            await unit.invoices.update(document)

        with pytest.raises(StaleWriteError) as caught:
            await in_unit(module_database, tenant, change)
        assert caught.value.code == "invoice_write_stale"
    assert await read(module_database, tenant, invoice.id) == invoice


async def test_an_invoice_is_corrected_by_its_draft_and_issued_notes_not_by_a_voided_one(
    module_database: BoundDatabase, regime: RegimeDef
) -> None:
    tenant = tenant_of()
    original, other = made(regime, tenant), made(regime, tenant)
    await kept(module_database, original)
    await kept(module_database, other)

    async def corrected(invoice: Invoice) -> bool:
        return await in_unit(module_database, tenant, lambda unit: unit.invoices.corrected_by(invoice.id))

    assert not await corrected(original)
    credit = note(regime, original, draft=True)
    await kept(module_database, credit)
    assert (await corrected(original), await corrected(other)) == (True, False)
    elsewhere = Tenant(tenant.organization, tenant.company, Mode.LIVE)
    assert not await in_unit(module_database, elsewhere, lambda unit: unit.invoices.corrected_by(original.id))
    issued, _ = credit.issue(seller=SELLER, pricing=credit.pricing, corrected=original, now=NOW)
    await in_unit(module_database, tenant, lambda unit: unit.invoices.update(issued))
    assert await corrected(original)
    voided, _ = issued.void(reason=None, corrected_by_a_note=False, now=NOW)
    await in_unit(module_database, tenant, lambda unit: unit.invoices.update(voided))
    assert not await corrected(original)


# ── Listing ─────────────────────────────────────────────────────────────────────


async def test_documents_are_listed_page_by_page_as_the_filters_name_them(
    module_database: BoundDatabase, regime: RegimeDef
) -> None:
    tenant = tenant_of()
    invoices = [made(regime, tenant, reference=f"ORD-{number}") for number in range(3)]
    draft = made(regime, tenant, draft=True)
    debit = note(regime, invoices[0], kind=DocumentKind.DEBIT_NOTE)
    for invoice in (*invoices, draft, debit):
        await kept(module_database, invoice)
    await kept(module_database, made(regime, Tenant(tenant.organization, tenant.company, Mode.LIVE)))

    async def listed(request: PageRequest, filters: InvoiceFilters | None = None) -> list[Invoice]:
        found = await in_unit(
            module_database, tenant, lambda unit: unit.invoices.page(request, filters or InvoiceFilters())
        )
        return list(found.items)

    first = await in_unit(
        module_database, tenant, lambda unit: unit.invoices.page(PageRequest(size=2), InvoiceFilters())
    )
    assert (list(first.items), first.has_more) == (invoices[:2], True)
    assert first.next_cursor is not None
    rest = await listed(PageRequest(size=5, after=decode_cursor(first.next_cursor)))
    assert rest == [invoices[2], draft, debit]
    assert await listed(PageRequest(size=2, before=debit.id)) == [invoices[2], draft]
    assert await listed(PageRequest(), InvoiceFilters(kind=DocumentKind.DEBIT_NOTE)) == [debit]
    assert await listed(PageRequest(), InvoiceFilters(status=InvoiceStatus.DRAFT)) == [draft]
    assert await listed(PageRequest(), InvoiceFilters(direction=Direction.PURCHASE)) == []
    reference = ExternalReference("ORD-1")
    assert await listed(PageRequest(), InvoiceFilters(external_reference=reference)) == [invoices[1]]
    every = InvoiceFilters(
        kind=DocumentKind.INVOICE,
        direction=Direction.SALES,
        status=InvoiceStatus.ISSUED,
        external_reference=reference,
    )
    assert await listed(PageRequest(), every) == [invoices[1]]


# ── The unit ────────────────────────────────────────────────────────────────────


async def test_a_unit_scoped_to_no_company_in_a_mode_is_a_defect(module_database: BoundDatabase) -> None:
    organization = OrganizationId(new_id())
    for scope in (
        None,
        OrganizationScope(organization, Mode.TEST),
        Tenant(organization, CompanyId(new_id()), None),
    ):
        with pytest.raises(UnscopedUnitError) as caught:
            async with module_database.unit(scope) as unit:
                bind(unit)
        assert str(caught.value) == "invoicing's state is read and changed in a unit of a company in a mode"


async def test_what_a_use_case_announces_and_records_commits_with_the_document(
    module_database: BoundDatabase, admin: psycopg.AsyncConnection, regime: RegimeDef
) -> None:
    world = World(module_database, regime)
    invoice = await world.commands.create(world.call, INVOICE)
    events = await admin.execute("SELECT type, subject, aggregate_version FROM invoicing.outbox")
    assert await events.fetchall() == [("invoicing.invoice.issued", invoice.id, 1)]
    audited = await admin.execute("SELECT action, subject_id, actor FROM invoicing.audit_events")
    assert await audited.fetchall() == [("invoice.create", str(invoice.id), "system:test")]


# ── What PostgreSQL alone proves: a void and a note wait for each other ─────────


PRINCIPAL: Final = Principal(
    subject=SubjectId("2001"),
    organization=OrganizationId(new_id()),
    provider_organization="1001",
    kind=PrincipalKind.HUMAN,
)


@final
class Directory(CompanyLookup):
    """identity, as the use cases ask it: the call's company, active, with its MAIN establishment."""

    @override
    async def company(self, call: Call, company: CompanyId) -> CompanyInfo:
        return CompanyInfo(
            id=company,
            organization=call.principal.organization,
            jurisdiction=parse_jurisdiction("DO"),
            functional_currency=DOP,
            tax_ids=(RNC,),
            legal_name="Acme SRL",
            active=True,
            fiscal_profile_locked=False,
            version=1,
        )

    @override
    async def establishment(
        self, call: Call, company: CompanyId, code: str | None
    ) -> EstablishmentRef | None:
        del call, company, code
        return MAIN


@final
class HeldInvoices(Invoices):
    """A unit's documents that, once they lock ``target``, hold the lock until ``release`` is set."""

    def __init__(
        self, inner: Invoices, target: InvoiceId, holding: asyncio.Event, release: asyncio.Event
    ) -> None:
        self._inner = inner
        self._target = target
        self._holding = holding
        self._release = release

    @override
    async def add(self, invoice: Invoice) -> InvoiceId | None:
        return await self._inner.add(invoice)

    @override
    async def get(self, invoice: InvoiceId) -> Invoice | None:
        return await self._inner.get(invoice)

    @override
    async def locked(self, invoice: InvoiceId) -> Invoice | None:
        found = await self._inner.locked(invoice)
        if invoice == self._target:
            self._holding.set()
            await self._release.wait()
        return found

    @override
    async def update(self, invoice: Invoice) -> None:
        await self._inner.update(invoice)

    @override
    async def corrected_by(self, invoice: InvoiceId) -> bool:
        return await self._inner.corrected_by(invoice)

    @override
    async def page(self, request: PageRequest, filters: InvoiceFilters) -> Page[Invoice]:
        return await self._inner.page(request, filters)


@final
class HoldingUnits(InvoicingUnits):
    """invoicing's units, whose documents hold ``target``'s lock once they take it (``HeldInvoices``)."""

    def __init__(self, database: BoundDatabase, target: InvoiceId) -> None:
        self._units = PgUnits(database)
        self._target = target
        self.holding = asyncio.Event()
        self.release = asyncio.Event()

    @override
    @asynccontextmanager
    async def unit(self, tenant: Tenant) -> AsyncGenerator[InvoicingUnit]:
        async with self._units.unit(tenant) as unit:
            held = HeldInvoices(unit.invoices, self._target, self.holding, self.release)
            yield replace(unit, invoices=held)


@final
class World:
    """The use cases over invoicing's real units, identity answered by ``Directory``."""

    def __init__(
        self, database: BoundDatabase, regime: RegimeDef, units: InvoicingUnits | None = None
    ) -> None:
        self.tenant = Tenant(PRINCIPAL.organization, CompanyId(new_id()), Mode.TEST)
        self.call = Call(principal=PRINCIPAL, scope=self.tenant, request_id="request-1")
        self.commands = InvoicingCommands(
            units=units or PgUnits(database),
            lookup=Directory(),
            regime_of=lambda _: regime,
            clock=lambda: NOW,
            new_id=new_id,
        )

    def holding(
        self, database: BoundDatabase, regime: RegimeDef, target: InvoiceId
    ) -> tuple[World, HoldingUnits]:
        """The same company's use cases, over units that hold ``target``'s lock once they take it."""
        units = HoldingUnits(database, target)
        twin = World(database, regime, units)
        twin.tenant, twin.call = self.tenant, self.call
        return twin, units


INVOICE: Final = Creation(kind=DocumentKind.INVOICE, direction=Direction.SALES, lines=(WIDGET,))


def note_of(invoice: Invoice, *, draft: bool = False) -> Creation:
    return replace(INVOICE, kind=DocumentKind.CREDIT_NOTE, corrects=invoice.id, draft=draft)


async def test_a_void_that_locked_the_invoice_first_refuses_the_note_made_meanwhile(
    module_database: BoundDatabase, migrated: WorkerDatabase, regime: RegimeDef
) -> None:
    world = World(module_database, regime)
    invoice = await world.commands.create(world.call, INVOICE)
    voiding, units = world.holding(module_database, regime, invoice.id)
    void = asyncio.create_task(voiding.commands.void(world.call, invoice.id, None))
    await units.holding.wait()
    note = asyncio.create_task(world.commands.create(world.call, note_of(invoice)))
    await eventually(lambda: waits_for_a_lock(app_url(migrated)))  # the note waits for the invoice
    assert not note.done()
    units.release.set()
    assert (await void).status is InvoiceStatus.VOIDED
    with pytest.raises(CorrectionInvalidError, match=r"^corrects is not an issued document$"):
        await note


async def test_a_note_that_locked_the_invoice_first_refuses_the_void_asked_meanwhile(
    module_database: BoundDatabase, migrated: WorkerDatabase, regime: RegimeDef
) -> None:
    world = World(module_database, regime)
    invoice = await world.commands.create(world.call, INVOICE)
    noting, units = world.holding(module_database, regime, invoice.id)
    note = asyncio.create_task(noting.commands.create(world.call, note_of(invoice)))
    await units.holding.wait()
    void = asyncio.create_task(world.commands.void(world.call, invoice.id, None))
    await eventually(lambda: waits_for_a_lock(app_url(migrated)))  # the void waits for the invoice
    assert not void.done()
    units.release.set()
    assert (await note).corrects == invoice.id
    with pytest.raises(InvoiceNotVoidableError):
        await void
    assert (await read(module_database, world.tenant, invoice.id)) == invoice


async def test_a_note_being_issued_holds_its_invoice_against_a_void(
    module_database: BoundDatabase, migrated: WorkerDatabase, regime: RegimeDef
) -> None:
    world = World(module_database, regime)
    invoice = await world.commands.create(world.call, INVOICE)
    draft = await world.commands.create(world.call, note_of(invoice, draft=True))
    issuing, units = world.holding(module_database, regime, invoice.id)
    issue = asyncio.create_task(issuing.commands.issue(world.call, draft.id))
    await units.holding.wait()  # it holds the note, then the invoice: the lock order
    void = asyncio.create_task(world.commands.void(world.call, invoice.id, None))
    await eventually(lambda: waits_for_a_lock(app_url(migrated)))
    assert not void.done()
    units.release.set()
    assert (await issue).status is InvoiceStatus.ISSUED
    with pytest.raises(InvoiceNotVoidableError):
        await void
