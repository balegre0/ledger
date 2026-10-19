"""invoicing's tables as migrated: row-level security hides and refuses what a unit's tenant does not own,
and a row can never point at another tenant's document, even where row-level security would let it in."""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Final
lazy from uuid import UUID

import pytest
from psycopg.errors import ForeignKeyViolation, InsufficientPrivilege
from sqlalchemy import func, insert, select, text
from sqlalchemy.exc import IntegrityError, ProgrammingError

from ledger_invoicing.adapters.postgres.tables import (
    InvoiceLineRecord,
    InvoiceLineTaxRecord,
    InvoiceRecord,
    InvoiceTaxRecord,
    InvoicingTable,
)
from ledger_kernel.ids import CompanyId, OrganizationId, new_id
from ledger_kernel.tenancy import Mode, OrganizationScope, Tenant
lazy from ledger_kernel.tenancy import Scope
lazy from ledger_platform.data.bound import BoundDatabase

NOW: Final = datetime(2026, 10, 12, 12, tzinfo=UTC)
TODAY: Final = date(2026, 10, 12)


def tenant_of(organization: OrganizationId | None = None, mode: Mode = Mode.TEST) -> Tenant:
    return Tenant(organization or OrganizationId(new_id()), CompanyId(new_id()), mode)


def keys(tenant: Tenant) -> dict[str, object]:
    mode = tenant.mode
    return {
        "organization_id": tenant.organization,
        "company_id": tenant.company,
        "mode": None if mode is None else mode.value,
    }


def invoice_of(
    tenant: Tenant, *, corrects: UUID | None = None, identifier: UUID | None = None
) -> dict[str, object]:
    """An issued invoice's row (a credit note's when it ``corrects`` one), nothing else of it."""
    return {
        "id": identifier or new_id(),
        **keys(tenant),
        "kind": "invoice" if corrects is None else "credit_note",
        "direction": "sales",
        "status": "issued",
        "external_reference": None,
        "corrects": corrects,
        "currency": "DOP",
        "fiscal": {},
        "regime": "do",
        "establishment_code": "MAIN",
        "establishment_time_zone": "America/Santo_Domingo",
        "issue_date": TODAY,
        "tax_point": TODAY,
        "net": Decimal(100),
        "tax": Decimal(18),
        "gross": Decimal(118),
        "seller_legal_name": "Acme SRL",
        "seller_tax_scheme": "do.rnc",
        "seller_tax_value": "101850043",
        "version": 1,
        "created_at": NOW,
        "updated_at": NOW,
        "issued_at": NOW,
    }


def line_of(tenant: Tenant, invoice: UUID) -> dict[str, object]:
    return {
        **keys(tenant),
        "invoice_id": invoice,
        "position": 0,
        "description": "Widget",
        "quantity": Decimal(1),
        "unit_price": Decimal(100),
        "discount": Decimal(0),
        "gross": Decimal(100),
        "base": Decimal(100),
    }


async def seeded(module_database: BoundDatabase, tenant: Tenant) -> UUID:
    """An invoice of ``tenant`` with a line, its tax and its totals' category: its id."""
    identifier = new_id()
    async with module_database.unit(tenant) as unit:
        await unit.connection.execute(insert(InvoiceRecord).values(invoice_of(tenant, identifier=identifier)))
        await unit.connection.execute(insert(InvoiceLineRecord).values(line_of(tenant, identifier)))
        await unit.connection.execute(
            insert(InvoiceLineTaxRecord).values(
                **keys(tenant),
                invoice_id=identifier,
                position=0,
                place=0,
                category="do.itbis.standard",
                kind="standard",
                percent=Decimal(18),
                since=date(2019, 1, 1),
                tax=Decimal(18),
            )
        )
        await unit.connection.execute(
            insert(InvoiceTaxRecord).values(
                **keys(tenant),
                invoice_id=identifier,
                place=0,
                category="do.itbis.standard",
                percent=Decimal(18),
                base=Decimal(100),
                tax=Decimal(18),
            )
        )
    return identifier


async def counts(module_database: BoundDatabase, scope: Scope | None) -> dict[str, int]:
    """How many rows of each of invoicing's tables ``scope`` sees."""
    async with module_database.unit(scope) as unit:
        return {
            table.name: (await unit.connection.execute(select(func.count()).select_from(table))).scalar_one()
            for table in InvoicingTable.metadata.tables.values()
        }


async def test_row_level_security_shows_a_tenant_its_own_rows_and_nothing_without_one(
    module_database: BoundDatabase,
) -> None:
    mine = tenant_of()
    await seeded(module_database, mine)
    await seeded(module_database, Tenant(mine.organization, mine.company, Mode.LIVE))
    await seeded(module_database, tenant_of(mine.organization))
    one = dict.fromkeys(("invoices", "invoice_lines", "invoice_line_taxes", "invoice_taxes"), 1)
    assert await counts(module_database, mine) == one
    for blind in (
        None,
        OrganizationScope(mine.organization, Mode.TEST),
        OrganizationScope(mine.organization, None),
        Tenant(mine.organization, mine.company, None),
    ):
        assert await counts(module_database, blind) == dict.fromkeys(one, 0)


async def test_a_unit_writes_no_row_of_another_tenant(module_database: BoundDatabase) -> None:
    mine = tenant_of()
    for row in (
        invoice_of(Tenant(mine.organization, mine.company, Mode.LIVE)),
        invoice_of(tenant_of(mine.organization)),
        invoice_of(tenant_of()),
    ):
        with pytest.raises(ProgrammingError) as caught:
            async with module_database.unit(mine) as unit:
                await unit.connection.execute(insert(InvoiceRecord).values(row))
        assert isinstance(caught.value.orig, InsufficientPrivilege)  # the policy refuses the row


@pytest.mark.parametrize("mode", [Mode.TEST, Mode.LIVE])
async def test_a_line_or_a_note_cannot_point_at_another_tenant_s_document(
    module_database: BoundDatabase, mode: Mode
) -> None:
    victim = tenant_of()
    theirs = await seeded(module_database, victim)
    attacker = Tenant(victim.organization, CompanyId(new_id()), mode)
    for row, table, key in (
        (line_of(attacker, theirs), InvoiceLineRecord, "fk_invoice_lines_invoice"),
        (invoice_of(attacker, corrects=theirs), InvoiceRecord, "fk_invoices_corrects"),
    ):
        with pytest.raises(IntegrityError) as caught:
            async with module_database.unit(attacker) as unit:  # the policy lets the row in
                await unit.connection.execute(insert(table).values(row))
        refusal = caught.value.orig
        assert isinstance(refusal, ForeignKeyViolation)
        assert refusal.diag.constraint_name == key


@pytest.mark.parametrize(
    "statement",
    ["SELECT count(*) FROM identity.companies", "SELECT version FROM platform.routing"],
)
async def test_invoicing_s_role_reaches_no_other_schema(
    module_database: BoundDatabase, statement: str
) -> None:
    with pytest.raises(ProgrammingError) as caught:
        async with module_database.unit(tenant_of()) as unit:
            await unit.connection.execute(text(statement))
    assert isinstance(caught.value.orig, InsufficientPrivilege)
