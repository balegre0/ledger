"""invoicing's use cases over in-memory ports: what they store, announce, audit and refuse.

A unit commits what it changed only when its block ends without an error, as a transaction does: a refusal
leaves nothing stored, announced or recorded.
"""

from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Final, final, override
from uuid import UUID
lazy from collections.abc import AsyncGenerator, Callable, Mapping

import pytest

from ledger_identity.api import CompanyInactiveError, CompanyInfo
from ledger_invoicing.application.commands import Creation, InvoicingCommands
from ledger_invoicing.application.ports import (
    Audit,
    CompanyLookup,
    Events,
    ForeignTenantError,
    InvoiceFilters,
    Invoices,
    InvoicingUnit,
    InvoicingUnits,
    StaleWriteError,
    UnmirroredAcceptanceError,
)
from ledger_invoicing.application.queries import InvoicingQueries
from ledger_invoicing.domain.errors import (
    CorrectionInvalidError,
    CorrectionRequiredError,
    CorrectionUnexpectedError,
    EstablishmentUnknownError,
    ExternalReferenceTakenError,
    InvoiceChangedError,
    InvoiceNotFoundError,
    InvoiceNotIssuableError,
    InvoiceNotVoidableError,
    InvoiceVersionStaleError,
    RegimeMissingError,
)
from ledger_invoicing.domain.ids import INVOICE_PREFIX, InvoiceId
from ledger_invoicing.domain.invoice import InvoiceStatus
from ledger_invoicing.domain.lines import LineTerms
from ledger_invoicing.domain.parties import Seller
from ledger_invoicing.domain.pricing import EstablishmentRef
from ledger_invoicing.domain.text import parse_external_reference, parse_reason
from ledger_invoicing.events import InvoiceIssued, InvoiceVoided
from ledger_kernel.access import CompanyNotFoundError, Principal, PrincipalKind, SubjectId
from ledger_kernel.errors import ErrorKind, InvalidValueError
from ledger_kernel.ids import CompanyId, OrganizationId, format_id, new_id
from ledger_kernel.jurisdiction import parse_jurisdiction
from ledger_kernel.money import Currency, Money
from ledger_kernel.pagination import PageRequest, page_of
from ledger_kernel.regimes import Direction, DocumentKind
from ledger_kernel.tax_ids import TaxId, TaxIdScheme
from ledger_kernel.tenancy import Mode, OrganizationScope, Tenant
from ledger_platform.runtime.calls import Call
lazy from ledger_invoicing.application.ports import AuditAction
lazy from ledger_invoicing.domain.invoice import Invoice
lazy from ledger_invoicing.events import InvoicingEvent
lazy from ledger_kernel.jurisdiction import Jurisdiction
lazy from ledger_kernel.pagination import Page
lazy from ledger_kernel.regimes import RegimeDef

NOON: Final = datetime(2026, 10, 12, 16, 0, tzinfo=UTC)
LATE_EVENING: Final = datetime(2026, 10, 13, 2, 0, tzinfo=UTC)
"""Already the 13th in UTC, still the 12th in Santo Domingo."""
ORGANIZATION: Final = OrganizationId(new_id())
COMPANY: Final = CompanyId(new_id())
TENANT: Final = Tenant(ORGANIZATION, COMPANY, Mode.TEST)
DOP: Final = Currency.of("DOP")
USD: Final = Currency.of("USD")
RNC: Final = TaxId(TaxIdScheme("do.rnc"), "101850043")
ACME: Final = CompanyInfo(
    id=COMPANY,
    organization=ORGANIZATION,
    jurisdiction=parse_jurisdiction("DO"),
    functional_currency=DOP,
    tax_ids=(RNC, TaxId(TaxIdScheme("do.cedula"), "00113918205")),
    legal_name="Acme SRL",
    active=True,
    fiscal_profile_locked=False,
    version=3,
)
MAIN: Final = EstablishmentRef(code="MAIN", time_zone="America/Santo_Domingo")
TOKYO: Final = EstablishmentRef(code="TOKYO", time_zone="Asia/Tokyo")
PRINCIPAL: Final = Principal(
    subject=SubjectId("2001"),
    organization=ORGANIZATION,
    provider_organization="1001",
    kind=PrincipalKind.HUMAN,
)
CALL: Final = Call(principal=PRINCIPAL, scope=TENANT, request_id="req-1")
WIDGET: Final = LineTerms(
    description="Widget", quantity=Decimal(2), unit_price=Decimal(50), taxes=("do.itbis.standard",)
)


INVOICE: Final = Creation(kind=DocumentKind.INVOICE, direction=Direction.SALES, lines=(WIDGET,))
"""An invoice issued at once, in the functional currency, from MAIN, at today's rates."""
DRAFT: Final = replace(INVOICE, draft=True)


# ── In-memory ports ────────────────────────────────────────────────────────────


@final
@dataclass(frozen=True, slots=True)
class Audited:
    """One change a unit recorded."""

    action: AuditAction
    subject: UUID
    before: object
    after: object
    detail: Mapping[str, str]


def _nothing_happens() -> None:
    """The default of ``State.while_waiting``."""


@final
class State:
    """invoicing's state in memory: what the units committed."""

    def __init__(self) -> None:
        self.invoices: dict[InvoiceId, Invoice] = {}
        self.events: list[InvoicingEvent] = []
        self.audited: list[Audited] = []
        self.while_waiting: Callable[[], None] = _nothing_happens
        """What another unit commits while one waits for a document's lock."""
        self.units = 0


@final
class Staged:
    """What one unit changed, committed to its ``State`` only if the unit ends without an error."""

    def __init__(self, state: State) -> None:
        self.state = state
        self.invoices: dict[InvoiceId, Invoice] = dict(state.invoices)
        self.events: list[InvoicingEvent] = []
        self.audited: list[Audited] = []

    def commit(self) -> None:
        self.state.invoices = self.invoices
        self.state.events.extend(self.events)
        self.state.audited.extend(self.audited)


def matches(invoice: Invoice, filters: InvoiceFilters) -> bool:
    """Whether ``invoice`` meets every condition ``filters`` gives."""
    return all(
        wanted is None or wanted == held
        for wanted, held in (
            (filters.kind, invoice.kind),
            (filters.direction, invoice.direction),
            (filters.status, invoice.status),
            (filters.external_reference, invoice.external_reference),
        )
    )


@final
class MemoryInvoices(Invoices):
    def __init__(self, staged: Staged, tenant: Tenant) -> None:
        self._staged = staged
        self._tenant = tenant

    def _require_own(self, invoice: Invoice) -> None:
        """What ``PgInvoices`` refuses before any statement: another tenant's document, an accepted one."""
        if invoice.tenant != self._tenant:
            raise ForeignTenantError
        if invoice.accepted_by:
            raise UnmirroredAcceptanceError

    @override
    async def add(self, invoice: Invoice) -> InvoiceId | None:
        self._require_own(invoice)
        reference = invoice.external_reference
        for other in self._staged.invoices.values():
            same_key = (other.tenant, other.kind, other.external_reference) == (
                invoice.tenant,
                invoice.kind,
                reference,
            )
            if reference is not None and same_key:
                return other.id
        self._staged.invoices[invoice.id] = invoice
        return None

    @override
    async def get(self, invoice: InvoiceId) -> Invoice | None:
        found = self._staged.invoices.get(invoice)
        return found if found is not None and found.tenant == self._tenant else None

    @override
    async def locked(self, invoice: InvoiceId) -> Invoice | None:
        self._staged.state.while_waiting()
        self._staged.invoices.update(self._staged.state.invoices)
        return await self.get(invoice)

    @override
    async def update(self, invoice: Invoice) -> None:
        self._require_own(invoice)
        kept = self._staged.invoices.get(invoice.id)
        if kept is None or kept.version != invoice.version - 1:  # as PgInvoices compares and sets
            raise StaleWriteError
        self._staged.invoices[invoice.id] = invoice

    @override
    async def corrected_by(self, invoice: InvoiceId) -> bool:
        return any(
            other.corrects == invoice
            and other.tenant == self._tenant
            and other.status is not InvoiceStatus.VOIDED
            for other in self._staged.invoices.values()
        )

    @override
    async def page(self, request: PageRequest, filters: InvoiceFilters) -> Page[Invoice]:
        mine = sorted(
            (
                invoice
                for invoice in self._staged.invoices.values()
                if invoice.tenant == self._tenant and matches(invoice, filters)
            ),
            key=lambda invoice: invoice.id,
        )
        before, after = request.before, request.after
        rows = (
            [invoice for invoice in reversed(mine) if invoice.id < before]
            if before is not None
            else [invoice for invoice in mine if after is None or invoice.id > after]
        )
        return page_of(rows[: request.size + 1], request, key=lambda invoice: invoice.id)


@final
class MemoryEvents(Events):
    def __init__(self, staged: Staged) -> None:
        self._staged = staged

    @override
    def publish(self, event: InvoicingEvent) -> None:
        self._staged.events.append(event)


@final
class MemoryAudit(Audit):
    def __init__(self, staged: Staged) -> None:
        self._staged = staged

    @override
    def record(
        self,
        action: AuditAction,
        subject: UUID,
        *,
        before: object,
        after: object,
        detail: Mapping[str, str] | None = None,
    ) -> None:
        self._staged.audited.append(Audited(action, subject, before, after, detail or {}))


@final
class MemoryUnits(InvoicingUnits):
    def __init__(self, state: State, clock: Callable[[], datetime]) -> None:
        self._state = state
        self._clock = clock

    @override
    @asynccontextmanager
    async def unit(self, tenant: Tenant) -> AsyncGenerator[InvoicingUnit]:
        staged = Staged(self._state)
        self._state.units += 1
        yield InvoicingUnit(
            invoices=MemoryInvoices(staged, tenant),
            events=MemoryEvents(staged),
            audit=MemoryAudit(staged),
            now=self._clock(),
        )
        staged.commit()


@final
class Lookup(CompanyLookup):
    """identity, as invoicing asks it: one company and its establishments."""

    def __init__(self) -> None:
        self.company_info: CompanyInfo | None = ACME
        self.establishments: dict[str, EstablishmentRef] = {"MAIN": MAIN, "TOKYO": TOKYO}
        self.calls: list[Call] = []

    @override
    async def company(self, call: Call, company: CompanyId) -> CompanyInfo:
        self.calls.append(call)
        info = self.company_info
        if info is None or info.id != company:
            raise CompanyNotFoundError(company)
        return info

    @override
    async def establishment(
        self, call: Call, company: CompanyId, code: str | None
    ) -> EstablishmentRef | None:
        self.calls.append(call)
        assert company == COMPANY
        return self.establishments.get("MAIN" if code is None else code)


@final
class World:
    """The use cases and the ports they run over, with a clock a test moves."""

    def __init__(self, regime: RegimeDef) -> None:
        self.state = State()
        self.directory = Lookup()
        self.now = NOON
        self.regimes: dict[str, RegimeDef] = {"DO": regime}
        self.ids: list[UUID] = []
        self.commands = InvoicingCommands(
            units=MemoryUnits(self.state, self.clock),
            lookup=self.directory,
            regime_of=self.regime_of,
            clock=self.clock,
            new_id=self.new_id,
        )
        self.queries = InvoicingQueries(units=self.commands.units)

    def clock(self) -> datetime:
        return self.now

    def regime_of(self, jurisdiction: Jurisdiction) -> RegimeDef | None:
        return self.regimes.get(jurisdiction.country)

    def new_id(self) -> UUID:
        made = new_id()
        self.ids.append(made)
        return made

    def stored(self) -> list[Invoice]:
        return list(self.state.invoices.values())


@pytest.fixture
def world(regime: RegimeDef) -> World:
    return World(regime)


async def issued_one(world: World, creation: Creation = INVOICE) -> Invoice:
    return await world.commands.create(CALL, creation)


# ── Creating ────────────────────────────────────────────────────────────────────


async def test_an_invoice_is_issued_at_once_sold_by_the_company_as_identity_knows_it(world: World) -> None:
    invoice = await world.commands.create(CALL, INVOICE)
    assert world.stored() == [invoice]
    assert (invoice.id, invoice.tenant, invoice.status, invoice.version) == (
        world.ids[0],
        TENANT,
        InvoiceStatus.ISSUED,
        1,
    )
    assert invoice.seller == Seller(legal_name="Acme SRL", tax_id=RNC)
    assert (invoice.created_at, invoice.issued_at) == (NOON, NOON)
    assert invoice.terms.currency == DOP
    assert invoice.pricing.totals.gross == Money.of("118.00", DOP)
    assert world.state.events == [
        InvoiceIssued(
            scope=TENANT,
            subject=invoice.id,
            aggregate_version=1,
            kind=DocumentKind.INVOICE,
            direction=Direction.SALES,
            corrects=None,
        )
    ]
    assert world.state.audited == [
        Audited("invoice.create", invoice.id, None, invoice, {"kind": "invoice", "status": "issued"})
    ]
    assert world.directory.calls == [CALL, CALL]


async def test_a_draft_is_stored_unannounced_without_its_seller(world: World) -> None:
    invoice = await world.commands.create(CALL, DRAFT)
    assert (invoice.status, invoice.seller, world.stored()) == (InvoiceStatus.DRAFT, None, [invoice])
    assert world.state.events == []
    assert world.state.audited == [
        Audited("invoice.create", invoice.id, None, invoice, {"kind": "invoice", "status": "draft"})
    ]


async def test_today_is_the_establishment_s_day_not_the_server_s(world: World) -> None:
    world.now = LATE_EVENING
    assert (await world.commands.create(CALL, INVOICE)).pricing.issue_date == date(2026, 10, 12)
    in_tokyo = await world.commands.create(CALL, replace(INVOICE, establishment="TOKYO"))
    assert (in_tokyo.pricing.issue_date, in_tokyo.pricing.establishment) == (date(2026, 10, 13), TOKYO)


async def test_an_establishment_the_company_lacks_is_refused_and_nothing_stored(world: World) -> None:
    with pytest.raises(EstablishmentUnknownError) as caught:
        await world.commands.create(CALL, replace(INVOICE, establishment="NOPE"))
    assert str(caught.value) == "establishment names no establishment of the company"
    assert (caught.value.kind, caught.value.code) == (ErrorKind.INVALID, "establishment_unknown")
    assert (world.stored(), world.state.events, world.state.audited, world.state.units) == ([], [], [], 0)


async def test_a_document_in_another_currency_carries_its_functional_totals(world: World) -> None:
    dollars = await world.commands.create(CALL, replace(INVOICE, currency=USD, exchange_rate=Decimal(60)))
    assert dollars.pricing.totals.gross == Money.of("118.00", USD)
    functional = dollars.pricing.functional
    assert functional is not None
    assert functional.gross == Money.of("7080.00", DOP)


async def test_an_inactive_or_unknown_company_creates_nothing(world: World) -> None:
    world.directory.company_info = replace(ACME, active=False)
    with pytest.raises(CompanyInactiveError):
        await world.commands.create(CALL, INVOICE)
    world.directory.company_info = None
    with pytest.raises(CompanyNotFoundError):
        await world.commands.create(CALL, INVOICE)
    assert (world.stored(), world.state.units) == ([], 0)


async def test_a_company_no_installed_regime_governs_is_a_defect_of_the_installation(world: World) -> None:
    world.regimes.clear()
    with pytest.raises(RegimeMissingError) as caught:
        await world.commands.create(CALL, INVOICE)
    assert str(caught.value) == "no installed regime governs the company's jurisdiction"
    assert (caught.value.kind, caught.value.code) == (ErrorKind.INTERNAL, "regime_missing")


async def test_a_call_that_names_no_company_in_a_mode_is_a_defect_of_its_caller(world: World) -> None:
    for scope in (Tenant(ORGANIZATION, COMPANY, None), OrganizationScope(ORGANIZATION, Mode.TEST)):
        with pytest.raises(InvalidValueError, match=r"^call\.scope must be a company in a mode$"):
            await world.commands.create(replace(CALL, scope=scope), INVOICE)
    assert world.directory.calls == []


async def test_an_external_reference_is_held_once_per_company_mode_and_type(world: World) -> None:
    keyed = replace(INVOICE, external_reference=parse_external_reference("ORD-1"))
    first = await world.commands.create(CALL, keyed)
    with pytest.raises(ExternalReferenceTakenError) as caught:
        await world.commands.create(CALL, replace(keyed, draft=True))
    assert caught.value.existing_id == first.id
    assert world.stored() == [first]
    assert len(world.state.events) == 1
    note = replace(keyed, kind=DocumentKind.CREDIT_NOTE, corrects=first.id)
    assert (await world.commands.create(CALL, note)).external_reference == "ORD-1"
    live = Tenant(ORGANIZATION, COMPANY, Mode.LIVE)
    assert (await world.commands.create(replace(CALL, scope=live), keyed)).tenant == live


async def test_whether_a_document_names_an_invoice_is_checked_before_identity_is_asked(world: World) -> None:
    with pytest.raises(CorrectionUnexpectedError):
        await world.commands.create(CALL, replace(INVOICE, corrects=InvoiceId(UUID(int=5))))
    with pytest.raises(CorrectionRequiredError):
        await world.commands.create(CALL, replace(INVOICE, kind=DocumentKind.CREDIT_NOTE))
    assert (world.directory.calls, world.state.units) == ([], 0)


async def test_a_note_is_in_its_invoice_s_currency(world: World) -> None:
    original = await issued_one(world)
    dollars = replace(INVOICE, kind=DocumentKind.CREDIT_NOTE, corrects=original.id, currency=USD)
    with pytest.raises(CorrectionInvalidError, match=r"^corrects is in another currency$"):
        await world.commands.create(CALL, replace(dollars, exchange_rate=Decimal(60)))


async def test_a_note_corrects_an_issued_invoice_of_its_company_and_mode(world: World) -> None:
    original = await issued_one(world)
    debit = replace(INVOICE, kind=DocumentKind.DEBIT_NOTE, corrects=original.id)
    note = await world.commands.create(CALL, debit)
    assert (note.corrects, world.state.events[-1]) == (
        original.id,
        InvoiceIssued(
            scope=TENANT,
            subject=note.id,
            aggregate_version=1,
            kind=DocumentKind.DEBIT_NOTE,
            direction=Direction.SALES,
            corrects=original.id,
        ),
    )
    live = replace(CALL, scope=Tenant(ORGANIZATION, COMPANY, Mode.LIVE))
    for call, corrects in ((CALL, InvoiceId(UUID(int=5))), (live, original.id)):
        with pytest.raises(
            CorrectionInvalidError, match=r"^corrects names no invoice of the company in this mode$"
        ):
            await world.commands.create(call, replace(debit, corrects=corrects))
    assert len(world.stored()) == 2


# ── Issuing a draft ─────────────────────────────────────────────────────────────


async def test_issuing_a_draft_prices_it_on_the_day_it_is_issued(world: World) -> None:
    draft = await world.commands.create(CALL, DRAFT)
    world.now = NOON + timedelta(days=3)
    issued = await world.commands.issue(CALL, draft.id)
    assert (issued.status, issued.version, issued.seller, issued.issued_at) == (
        InvoiceStatus.ISSUED,
        2,
        Seller(legal_name="Acme SRL", tax_id=RNC),
        NOON + timedelta(days=3),
    )
    assert (draft.pricing.issue_date, issued.pricing.issue_date) == (date(2026, 10, 12), date(2026, 10, 15))
    assert world.stored() == [issued]
    assert world.state.events == [
        InvoiceIssued(
            scope=TENANT,
            subject=draft.id,
            aggregate_version=2,
            kind=DocumentKind.INVOICE,
            direction=Direction.SALES,
            corrects=None,
        )
    ]
    assert world.state.audited[-1] == Audited("invoice.issue", draft.id, draft, issued, {"kind": "invoice"})


async def test_issuing_what_is_not_a_draft_or_not_there_is_refused(world: World) -> None:
    issued = await issued_one(world)
    with pytest.raises(InvoiceNotIssuableError):
        await world.commands.issue(CALL, issued.id)
    with pytest.raises(InvoiceNotFoundError) as caught:
        await world.commands.issue(CALL, InvoiceId(UUID(int=9)))
    assert (caught.value.kind, caught.value.code) == (ErrorKind.NOT_FOUND, "invoice_not_found")
    live = replace(CALL, scope=Tenant(ORGANIZATION, COMPANY, Mode.LIVE))
    with pytest.raises(InvoiceNotFoundError):
        await world.commands.issue(live, issued.id)
    assert len(world.state.events) == 1


async def test_a_draft_changed_while_it_was_priced_is_not_issued(world: World) -> None:
    draft = await world.commands.create(CALL, DRAFT)

    def someone_else_changes_it() -> None:
        world.state.invoices[draft.id] = replace(draft, version=2)

    world.state.while_waiting = someone_else_changes_it
    with pytest.raises(InvoiceChangedError):
        await world.commands.issue(CALL, draft.id)
    assert world.state.events == []


async def test_a_draft_issued_from_an_establishment_gone_since_is_refused(world: World) -> None:
    draft = await world.commands.create(CALL, replace(DRAFT, establishment="TOKYO"))
    del world.directory.establishments["TOKYO"]
    with pytest.raises(EstablishmentUnknownError):
        await world.commands.issue(CALL, draft.id)


async def test_issuing_a_draft_needs_its_company_active(world: World) -> None:
    draft = await world.commands.create(CALL, DRAFT)
    world.directory.company_info = replace(ACME, active=False)
    with pytest.raises(CompanyInactiveError):
        await world.commands.issue(CALL, draft.id)


async def test_a_note_whose_invoice_was_voided_while_it_was_priced_is_not_issued(world: World) -> None:
    original = await issued_one(world)
    note = await world.commands.create(
        CALL, replace(DRAFT, kind=DocumentKind.CREDIT_NOTE, corrects=original.id)
    )
    voided, _ = original.void(reason=None, corrected_by_a_note=False, now=NOON)

    def voided_meanwhile() -> None:
        world.state.invoices[original.id] = voided

    world.state.while_waiting = voided_meanwhile
    with pytest.raises(CorrectionInvalidError, match=r"^corrects is not an issued document$"):
        await world.commands.issue(CALL, note.id)


async def test_a_draft_issued_by_someone_else_meanwhile_is_not_issued_twice(world: World) -> None:
    draft = await world.commands.create(CALL, DRAFT)
    seller = Seller(legal_name="Acme SRL", tax_id=RNC)
    elsewhere, _ = draft.issue(seller=seller, pricing=draft.pricing, corrected=None, now=NOON)

    def issued_meanwhile() -> None:
        world.state.invoices[draft.id] = elsewhere

    world.state.while_waiting = issued_meanwhile
    with pytest.raises(InvoiceNotIssuableError):
        await world.commands.issue(CALL, draft.id)
    assert world.state.events == []


# ── Voiding ─────────────────────────────────────────────────────────────────────


async def test_voiding_an_issued_document_announces_and_records_it(world: World) -> None:
    issued = await issued_one(world)
    world.now = NOON + timedelta(hours=1)
    voided = await world.commands.void(CALL, issued.id, parse_reason("Wrong buyer"))
    assert (voided.status, voided.version, voided.void_reason, voided.voided_at) == (
        InvoiceStatus.VOIDED,
        2,
        "Wrong buyer",
        NOON + timedelta(hours=1),
    )
    assert world.stored() == [voided]
    assert world.state.events[-1] == InvoiceVoided(
        scope=TENANT,
        subject=issued.id,
        aggregate_version=2,
        kind=DocumentKind.INVOICE,
        direction=Direction.SALES,
    )
    assert world.state.audited[-1] == Audited("invoice.void", issued.id, issued, voided, {"kind": "invoice"})


async def test_an_invoice_a_draft_or_issued_note_corrects_is_not_voided(world: World) -> None:
    original = await issued_one(world)
    note = await world.commands.create(
        CALL, replace(DRAFT, kind=DocumentKind.CREDIT_NOTE, corrects=original.id)
    )
    with pytest.raises(InvoiceNotVoidableError):
        await world.commands.void(CALL, original.id, None)
    await world.commands.issue(CALL, note.id)
    with pytest.raises(InvoiceNotVoidableError):
        await world.commands.void(CALL, original.id, None)
    await world.commands.void(CALL, note.id, None)
    assert (await world.commands.void(CALL, original.id, None)).status is InvoiceStatus.VOIDED


async def test_a_note_made_while_its_invoice_is_voided_stops_the_void(world: World) -> None:
    # The fake serializes nothing: it shows what the void decides once the note is visible to it. That the
    # two serialize on the invoice's row lock (the note's unit locks the invoice it corrects, the void locks
    # it before it asks for notes) only PostgreSQL proves: Task 4's repository test.
    original = await issued_one(world)
    note = replace(original, id=InvoiceId(new_id()), kind=DocumentKind.CREDIT_NOTE, corrects=original.id)

    def a_note_is_made_meanwhile() -> None:
        world.state.invoices[note.id] = note

    world.state.while_waiting = a_note_is_made_meanwhile
    with pytest.raises(InvoiceNotVoidableError):
        await world.commands.void(CALL, original.id, None)


async def test_a_voided_document_is_not_voided_again(world: World) -> None:
    issued = await issued_one(world)
    await world.commands.void(CALL, issued.id, None)
    with pytest.raises(InvoiceNotVoidableError):
        await world.commands.void(CALL, issued.id, None)
    assert len(world.state.events) == 2


async def test_what_is_not_issued_or_not_there_is_not_voided(world: World) -> None:
    draft = await world.commands.create(CALL, DRAFT)
    with pytest.raises(InvoiceNotVoidableError):
        await world.commands.void(CALL, draft.id, None)
    with pytest.raises(InvoiceNotFoundError):
        await world.commands.void(CALL, InvoiceId(UUID(int=9)), None)
    assert (world.stored(), len(world.state.audited)) == ([draft], 1)


# ── If-Match: the versions a change may apply to ────────────────────────────────


def never(version: int) -> bool:
    del version
    return False


async def test_a_draft_is_issued_only_at_a_version_the_caller_admits(world: World) -> None:
    draft = await world.commands.create(CALL, DRAFT)
    with pytest.raises(InvoiceVersionStaleError) as caught:
        await world.commands.prepare_issue(CALL, draft.id, admits=never)
    assert (caught.value.kind, caught.value.code) == (ErrorKind.CONFLICT, "invoice_version_stale")
    assert world.directory.calls == [CALL, CALL]  # identity is not asked again for a stale request
    with pytest.raises(InvoiceNotFoundError):  # an unknown document is not found, whatever the request names
        await world.commands.prepare_issue(CALL, InvoiceId(UUID(int=9)), admits=never)
    change = await world.commands.prepare_issue(CALL, draft.id, admits=lambda version: version == 1)
    assert change.tenant == TENANT
    async with world.commands.units.unit(change.tenant) as unit:
        issued = await change.apply(unit)
    assert (issued.status, world.stored()) == (InvoiceStatus.ISSUED, [issued])


async def test_a_document_is_voided_only_at_a_version_the_caller_admits(world: World) -> None:
    issued = await issued_one(world)
    stale = world.commands.prepare_void(CALL, issued.id, None, admits=never)
    with pytest.raises(InvoiceVersionStaleError):
        async with world.commands.units.unit(stale.tenant) as unit:
            await stale.apply(unit)
    assert world.stored() == [issued]
    missing = world.commands.prepare_void(CALL, InvoiceId(UUID(int=9)), None, admits=never)
    with pytest.raises(InvoiceNotFoundError):
        async with world.commands.units.unit(missing.tenant) as unit:
            await missing.apply(unit)


async def test_a_creation_prepared_outside_any_unit_is_applied_in_one(world: World) -> None:
    change = await world.commands.prepare_create(CALL, INVOICE)
    assert (change.tenant, world.state.units, world.directory.calls) == (TENANT, 0, [CALL, CALL])
    async with world.commands.units.unit(change.tenant) as unit:
        created = await change.apply(unit)
    assert world.stored() == [created]


def test_a_business_key_s_conflict_names_its_holder_in_a_member_of_its_problem() -> None:
    holder = InvoiceId(new_id())
    members = ExternalReferenceTakenError(holder).problem_members()
    assert members == {"existing_id": format_id(INVOICE_PREFIX, holder)}


# ── Reading ─────────────────────────────────────────────────────────────────────


async def test_a_document_is_read_in_its_tenant_only(world: World) -> None:
    issued = await issued_one(world)
    assert await world.queries.document(CALL, issued.id) == issued
    live = replace(CALL, scope=Tenant(ORGANIZATION, COMPANY, Mode.LIVE))
    for call, invoice in ((live, issued.id), (CALL, InvoiceId(UUID(int=9)))):
        with pytest.raises(InvoiceNotFoundError):
            await world.queries.document(call, invoice)


async def test_documents_are_listed_as_the_filters_name_them_page_by_page(world: World) -> None:
    first, second = await issued_one(world), await issued_one(world)
    draft = await world.commands.create(CALL, DRAFT)
    every = await world.queries.documents(CALL, PageRequest(), InvoiceFilters())
    assert every.items == tuple(sorted((first, second, draft), key=lambda invoice: invoice.id))
    drafts = await world.queries.documents(CALL, PageRequest(), InvoiceFilters(status=InvoiceStatus.DRAFT))
    assert drafts.items == (draft,)
    before = await world.queries.documents(CALL, PageRequest(size=1, before=draft.id), InvoiceFilters())
    assert before.items == (second,)
