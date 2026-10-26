"""invoicing's API for other modules (§3.4), for real: the facade over PostgreSQL, called as they call it.

``document`` opens a unit of its own in the call's tenant (a company in a mode), so row-level security and the
repository both keep it there: another company's or mode's document is as missing as one never made. It may
not be called while a unit is open (rule 13), and a call that names no company in a mode is a defect.
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Final

import pytest

from ledger_invoicing.adapters.postgres.unit import PgUnits
from ledger_invoicing.api import INVOICING_API, InvoiceNotFoundError
from ledger_invoicing.application.facade import InvoicingFacade
from ledger_invoicing.application.infos import info_of
from ledger_invoicing.application.mirror import FiscalMirroring
from ledger_invoicing.application.queries import InvoicingQueries
from ledger_invoicing.domain.ids import InvoiceId
from ledger_invoicing.domain.invoice import Invoice, Opening
from ledger_invoicing.domain.lines import LineTerms
from ledger_invoicing.domain.parties import Seller, party
from ledger_invoicing.domain.pricing import Dating, EstablishmentRef, Terms, price
from ledger_kernel.access import Principal, PrincipalKind, SubjectId
from ledger_kernel.errors import InvalidValueError
from ledger_kernel.ids import CompanyId, OrganizationId, new_id
from ledger_kernel.money import Currency
from ledger_kernel.regimes import Direction, DocumentKind
from ledger_kernel.tax_ids import TaxId, TaxIdScheme
from ledger_kernel.tenancy import Mode, OrganizationScope, Tenant
from ledger_platform.runtime.calls import Call, SystemCall
from ledger_platform.runtime.facade import UnitOpenError
lazy from ledger_invoicing.api import InvoicingApi
lazy from ledger_kernel.regimes import RegimeDef
lazy from ledger_kernel.tenancy import Scope
lazy from ledger_platform.data.bound import BoundDatabase

NOW: Final = datetime(2026, 10, 12, 16, 0, tzinfo=UTC)
DOP: Final = Currency.of("DOP")
PRINCIPAL: Final = Principal(
    subject=SubjectId("2001"),
    organization=OrganizationId(new_id()),
    provider_organization="1001",
    kind=PrincipalKind.HUMAN,
)
TENANT: Final = Tenant(PRINCIPAL.organization, CompanyId(new_id()), Mode.TEST)
TERMS: Final = Terms(
    currency=DOP,
    lines=(
        LineTerms(
            description="Widget", quantity=Decimal(2), unit_price=Decimal(50), taxes=("do.itbis.standard",)
        ),
    ),
    buyer=party(legal_name="Beta SRL", tax_id=TaxId(TaxIdScheme("do.rnc"), "130000012")),
)


def called_in(scope: Scope) -> Call:
    """A request's call, as another module's route would pass it on."""
    return Call(principal=PRINCIPAL, scope=scope, request_id="request-1")


@pytest.fixture
def api(module_database: BoundDatabase) -> InvoicingApi:
    """The API as the assembly binds it, over invoicing's units in its own role."""
    units = PgUnits(module_database)
    return INVOICING_API.take(InvoicingFacade(InvoicingQueries(units=units), FiscalMirroring(units=units)))


async def kept(database: BoundDatabase, regime: RegimeDef, *, draft: bool = False) -> Invoice:
    """An invoice of ``TENANT``, issued unless ``draft``, kept by invoicing's repository."""
    opening = Opening(
        id=InvoiceId(new_id()),
        tenant=TENANT,
        kind=DocumentKind.INVOICE,
        direction=Direction.SALES,
        external_reference=None,
        terms=TERMS,
    )
    pricing = price(
        TERMS,
        regime=regime,
        functional_currency=DOP,
        establishment=EstablishmentRef(code="MAIN", time_zone="America/Santo_Domingo"),
        dating=Dating(today=date(2026, 10, 12)),
    )
    seller = (
        None
        if draft
        else Seller(legal_name="Acme SRL", tax_id=TaxId(TaxIdScheme("do.rnc"), "101850043"), address=None)
    )
    invoice, _ = Invoice.new(opening, corrects=None, pricing=pricing, seller=seller, now=NOW)
    async with PgUnits(database).unit(TENANT) as unit:
        assert await unit.invoices.add(invoice) is None
    return invoice


async def test_a_document_is_read_by_a_request_s_call_and_by_the_platform_s(
    api: InvoicingApi, module_database: BoundDatabase, regime: RegimeDef
) -> None:
    invoice = await kept(module_database, regime)
    draft = await kept(module_database, regime, draft=True)
    assert await api.document(called_in(TENANT), invoice.id) == info_of(invoice)
    by_a_consumer = SystemCall(reason="fiscal.commercial_documents", scope=TENANT)
    assert await api.document(by_a_consumer, draft.id) == info_of(draft)


async def test_another_tenant_s_document_is_as_missing_as_one_never_made(
    api: InvoicingApi, module_database: BoundDatabase, regime: RegimeDef
) -> None:
    invoice = await kept(module_database, regime)
    elsewhere = (
        Tenant(TENANT.organization, TENANT.company, Mode.LIVE),
        Tenant(TENANT.organization, CompanyId(new_id()), Mode.TEST),
        Tenant(OrganizationId(new_id()), TENANT.company, Mode.TEST),
    )
    for tenant in elsewhere:
        with pytest.raises(InvoiceNotFoundError) as caught:
            await api.document(called_in(tenant), invoice.id)
        assert str(caught.value) == "invoice was not found"  # never the id that was asked for
    with pytest.raises(InvoiceNotFoundError):
        await api.document(called_in(TENANT), InvoiceId(new_id()))


async def test_a_call_that_names_no_company_in_a_mode_is_a_defect(api: InvoicingApi) -> None:
    calls = (
        called_in(OrganizationScope(TENANT.organization, Mode.TEST)),
        called_in(Tenant(TENANT.organization, TENANT.company, None)),
        SystemCall(reason="test", scope=None),
    )
    for call in calls:
        with pytest.raises(InvalidValueError, match=r"^call\.scope must be a company in a mode$"):
            await api.document(call, InvoiceId(new_id()))


async def test_the_api_refuses_a_call_made_while_a_unit_is_open(
    api: InvoicingApi, module_database: BoundDatabase, regime: RegimeDef
) -> None:
    invoice = await kept(module_database, regime)
    async with module_database.unit(TENANT):
        with pytest.raises(UnitOpenError):
            await api.document(called_in(TENANT), invoice.id)
