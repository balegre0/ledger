"""The fiscal mirror (§7.5, §8.5): ``InvoicingApi.apply_fiscal_status`` for real, and the mirror on the wire.

fiscal's consumer and its synchronous path apply what a channel reports; a newer version per document and
channel wins, an older or equal one is ``STALE`` and changes nothing, so redeliveries and races are harmless
(rule 4). Each status kept is announced (``invoicing.invoice.fiscal_updated``, its aggregate version the
status's) and shown in ``fiscal[]``, ordered by channel, with the channel's details under the regime's key.
The document's own version (its ``ETag``) is not the mirror's. One a channel accepted is never voided.
"""

from datetime import UTC, datetime
from typing import Final
from uuid import UUID

import pytest
from pydantic import TypeAdapter
lazy import httpx
lazy import psycopg

from ledger_invoicing.adapters.postgres.unit import PgUnits
from ledger_invoicing.api import (
    INVOICING_API,
    Applied,
    AuthorityMessageInfo,
    FiscalStatusInfo,
    FiscalStatusName,
    InvoiceNotFoundError,
)
from ledger_invoicing.application.facade import InvoicingFacade
from ledger_invoicing.application.mirror import FiscalMirroring
from ledger_invoicing.application.queries import InvoicingQueries
from ledger_invoicing.domain.ids import INVOICE_PREFIX, InvoiceId
from ledger_kernel.errors import InvalidValueError
from ledger_kernel.ids import COMPANY_PREFIX, CompanyId, format_id, new_id, parse_id
from ledger_kernel.tenancy import Mode, Tenant
from ledger_platform.auth.providers.zitadel import organization_of
from ledger_platform.runtime import SystemCall
from ledger_platform.runtime.facade import UnitOpenError
from ledger_testing.api import document, page
lazy from ledger_invoicing.api import InvoicingApi
lazy from ledger_platform.data.bound import BoundDatabase
lazy from ledger_testing.api import TestPrincipal

NOW: Final = datetime(2026, 10, 20, 12, tzinfo=UTC)
LINE: Final = {"description": "Widget", "quantity": "2", "unit_price": "50", "taxes": ["do.itbis.standard"]}
INVOICE: Final = {"type": "invoice", "lines": [LINE]}
UPDATES: Final = """
    SELECT subject, aggregate_version, payload FROM invoicing.outbox
    WHERE type = 'invoicing.invoice.fiscal_updated' ORDER BY id
"""
ROWS: Final = TypeAdapter(list[tuple[UUID, int, dict[str, object]]])
ENTRIES: Final = TypeAdapter(list[dict[str, object]])


@pytest.fixture
def invoicing(module_database: BoundDatabase) -> InvoicingApi:
    """invoicing's API as the assembly binds it, over its units: what fiscal calls."""
    units = PgUnits(module_database)
    return INVOICING_API.take(InvoicingFacade(InvoicingQueries(units=units), FiscalMirroring(units=units)))


def status_of(
    status: FiscalStatusName = FiscalStatusName.PENDING, *, channel: str = "do-ecf-v1"
) -> FiscalStatusInfo:
    return FiscalStatusInfo(
        channel=channel,
        environment="testecf",
        status=status,
        authority_number="E310000000001",
        messages=(AuthorityMessageInfo(code="1", message="Aceptado condicional", lang="es"),),
        regime="do",
        details={"encf": "E310000000001", "security_code": "A1b2C3", "signed": True, "track": None},
        updated_at=NOW,
    )


def posting(principal: TestPrincipal, company: str, key: str, version: int | None = None) -> dict[str, str]:
    headers = principal.posting(key, "test") | {"Ledger-Company": company}
    return headers if version is None else headers | {"If-Match": f'"{version}"'}


def reading(principal: TestPrincipal, company: str) -> dict[str, str]:
    return principal.bearer() | {"Ledger-Company": company, "Ledger-Mode": "test"}


async def issued(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str, key: str = "i-1"
) -> InvoiceId:
    response = await client.post("/v1/invoices", json=INVOICE, headers=posting(owner, company, key))
    assert response.status_code == 201, response.text
    return InvoiceId(parse_id(INVOICE_PREFIX, str(document(response)["id"])))


def system(company: str, mode: Mode = Mode.TEST) -> SystemCall:
    """fiscal's call about a document of ``company``: the scope of the event that caused it."""
    tenant = Tenant(organization_of("1001"), CompanyId(parse_id(COMPANY_PREFIX, company)), mode)
    return SystemCall(reason="fiscal.channel_documents", scope=tenant, call_id=str(new_id()))


async def updates(admin: psycopg.AsyncConnection) -> list[tuple[UUID, int, dict[str, object]]]:
    cursor = await admin.execute(UPDATES)
    return ROWS.validate_python([tuple(row) for row in await cursor.fetchall()])


async def test_an_older_status_after_a_newer_one_is_stale_and_announced_once(
    client: httpx.AsyncClient,
    owner: TestPrincipal,
    company: str,
    invoicing: InvoicingApi,
    admin: psycopg.AsyncConnection,
) -> None:
    invoice = await issued(client, owner, company)
    call = system(company)
    accepted = status_of(FiscalStatusName.ACCEPTED)
    assert await invoicing.apply_fiscal_status(call, invoice, accepted, 3) is Applied.APPLIED
    assert await invoicing.apply_fiscal_status(call, invoice, status_of(), 2) is Applied.STALE
    assert await invoicing.apply_fiscal_status(call, invoice, accepted, 3) is Applied.STALE  # redelivered
    payload = {"channel": "do-ecf-v1", "status": "accepted", "version": 3}
    assert await updates(admin) == [(invoice, 3, payload)]
    read = await client.get(f"/v1/invoices/{document_id(invoice)}", headers=reading(owner, company))
    assert [(entry["status"], entry["version"]) for entry in fiscal_of(read)] == [("accepted", 3)]
    assert read.headers["etag"] == '"1"'  # the mirror is not the document's state


async def test_statuses_applied_in_order_are_each_announced(
    client: httpx.AsyncClient,
    owner: TestPrincipal,
    company: str,
    invoicing: InvoicingApi,
    admin: psycopg.AsyncConnection,
) -> None:
    invoice = await issued(client, owner, company)
    for version in (2, 3):
        assert (
            await invoicing.apply_fiscal_status(system(company), invoice, status_of(), version)
            is Applied.APPLIED
        )
    assert [(subject, version) for subject, version, _ in await updates(admin)] == [
        (invoice, 2),
        (invoice, 3),
    ]


async def test_the_mirror_is_on_the_wire_by_channel_with_the_details_under_the_regime(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str, invoicing: InvoicingApi
) -> None:
    invoice = await issued(client, owner, company)
    await invoicing.apply_fiscal_status(system(company), invoice, status_of(channel="do-zeta-v1"), 1)
    await invoicing.apply_fiscal_status(system(company), invoice, status_of(), 5)
    entry = {
        "channel": "do-ecf-v1",
        "environment": "testecf",
        "status": "pending",
        "version": 5,
        "messages": [{"code": "1", "message": "Aceptado condicional", "lang": "es"}],
        "do": {"encf": "E310000000001", "security_code": "A1b2C3", "signed": True, "track": None},
    }
    read = await client.get(f"/v1/invoices/{document_id(invoice)}", headers=reading(owner, company))
    assert fiscal_of(read) == [entry, {**entry, "channel": "do-zeta-v1", "version": 1}]
    listed = page(await client.get("/v1/invoices", headers=reading(owner, company)))
    assert [item["fiscal"] for item in listed["items"]] == [fiscal_of(read)]


async def test_a_document_a_channel_accepted_is_not_voided(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str, invoicing: InvoicingApi
) -> None:
    accepted, rejected = await issued(client, owner, company), await issued(client, owner, company, "i-2")
    await invoicing.apply_fiscal_status(
        system(company), accepted, status_of(FiscalStatusName.ACCEPTED_WITH_OBSERVATIONS), 4
    )
    await invoicing.apply_fiscal_status(system(company), rejected, status_of(FiscalStatusName.REJECTED), 4)
    refused = await client.post(
        f"/v1/invoices/{document_id(accepted)}/void", json={}, headers=posting(owner, company, "v-1", 1)
    )
    assert (refused.status_code, document(refused)["code"]) == (409, "invoice_not_voidable")
    voided = await client.post(
        f"/v1/invoices/{document_id(rejected)}/void", json={}, headers=posting(owner, company, "v-2", 1)
    )
    assert (voided.status_code, document(voided)["status"]) == (200, "voided")
    assert [entry["status"] for entry in fiscal_of(voided)] == ["rejected"]


async def test_a_retried_post_answers_the_document_as_it_is_now(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str, invoicing: InvoicingApi
) -> None:
    first = await client.post("/v1/invoices", json=INVOICE, headers=posting(owner, company, "i-1"))
    invoice = InvoiceId(parse_id(INVOICE_PREFIX, str(document(first)["id"])))
    assert document(first)["fiscal"] == []
    await invoicing.apply_fiscal_status(system(company), invoice, status_of(), 2)
    again = await client.post("/v1/invoices", json=INVOICE, headers=posting(owner, company, "i-1"))
    assert (again.status_code, again.headers["idempotent-replayed"]) == (201, "true")
    assert (again.headers["location"], again.headers["etag"]) == (first.headers["location"], '"1"')
    assert [entry["version"] for entry in fiscal_of(again)] == [2]
    assert {**document(again), "fiscal": []} == document(first)


async def test_a_status_for_a_document_the_tenant_does_not_have_is_not_found(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str, invoicing: InvoicingApi
) -> None:
    invoice = await issued(client, owner, company)
    for call, document_ in (
        (system(company), InvoiceId(new_id())),
        (system(company, Mode.LIVE), invoice),
    ):
        with pytest.raises(InvoiceNotFoundError):
            await invoicing.apply_fiscal_status(call, document_, status_of(), 1)


async def test_a_version_below_one_and_a_call_inside_a_unit_are_defects(
    invoicing: InvoicingApi, module_database: BoundDatabase
) -> None:
    call = SystemCall(reason="test", scope=Tenant(organization_of("1001"), CompanyId(new_id()), Mode.TEST))
    with pytest.raises(InvalidValueError, match=r"^fiscal_status\.version must be 1 or more$"):
        await invoicing.apply_fiscal_status(call, InvoiceId(new_id()), status_of(), 0)
    async with module_database.unit(call.scope):
        with pytest.raises(UnitOpenError):
            await invoicing.apply_fiscal_status(call, InvoiceId(new_id()), status_of(), 1)


def document_id(invoice: InvoiceId) -> str:
    return format_id(INVOICE_PREFIX, invoice)


def fiscal_of(response: httpx.Response) -> list[dict[str, object]]:
    assert response.status_code in {200, 201}, response.text
    return ENTRIES.validate_python(document(response)["fiscal"])
