"""What invoicing announces (§3.5), through the ``api`` app: each change writes its event to
``invoicing.outbox`` in the unit that makes it, so the event commits with the document, its audit and its
``Idempotency-Key`` or not at all; a refused request, a draft and a replay announce nothing. Read as the
superuser: the outbox is the module's and the relay's."""

from dataclasses import dataclass
from typing import Final, final
from uuid import UUID
lazy from collections.abc import Mapping

import pytest
from pydantic import TypeAdapter
lazy import httpx
lazy import psycopg

from ledger_invoicing.domain.ids import INVOICE_PREFIX
from ledger_kernel.ids import COMPANY_PREFIX, parse_id
from ledger_platform.auth.providers.zitadel import organization_of
from ledger_testing.api import document
lazy from ledger_testing.api import TestPrincipal

ORGANIZATION: Final = organization_of("1001")
"""The organization of ``owner`` (the provider's ``1001``), whose company the documents are."""
LINE: Final = {"description": "Widget", "quantity": "2", "unit_price": "50", "taxes": ["do.itbis.standard"]}
INVOICE: Final = {"type": "invoice", "lines": [LINE]}
ISSUED: Final = {"kind": "invoice", "direction": "sales", "corrects": None}
"""The payload of an invoice's ``invoicing.invoice.issued``, version 1."""
OUTBOX: Final = """
    SELECT type, version, source, organization_id, company_id, mode, subject, aggregate_version, payload
    FROM invoicing.outbox ORDER BY id
"""
type Row = tuple[str, int, str, UUID, UUID, str | None, UUID, int, dict[str, object]]
ROWS: Final = TypeAdapter(list[tuple[str, int, str, UUID, UUID, str | None, UUID, int, dict[str, object]]])


async def outbox(admin: psycopg.AsyncConnection) -> list[Row]:
    """Every event waiting in invoicing's outbox, oldest first."""
    cursor = await admin.execute(OUTBOX)
    return ROWS.validate_python([tuple(row) for row in await cursor.fetchall()])


@final
@dataclass(frozen=True, slots=True)
class Owner:
    """``owner``'s ``POST``s in its company, in ``mode``."""

    client: httpx.AsyncClient
    owner: TestPrincipal
    company: str
    mode: str = "test"

    async def post(
        self, path: str, key: str, body: Mapping[str, object] | None = None, version: int | None = None
    ) -> httpx.Response:
        """``body`` to ``path`` under ``key``, at the version ``If-Match`` names (if given)."""
        headers = self.owner.posting(key, self.mode) | {"Ledger-Company": self.company}
        if version is not None:
            headers |= {"If-Match": f'"{version}"'}
        return await self.client.post(path, json=body, headers=headers)


@pytest.fixture
def by(client: httpx.AsyncClient, owner: TestPrincipal, company: str) -> Owner:
    """``owner``, posting in its company in test mode."""
    return Owner(client, owner, company)


def id_of(response: httpx.Response) -> str:
    """The public id of the document ``response`` answers with, once it succeeded."""
    assert response.status_code in {200, 201}, response.text
    return str(document(response)["id"])


async def test_an_invoice_issued_at_once_is_announced_in_its_tenant_at_its_version(
    by: Owner, company: str, admin: psycopg.AsyncConnection
) -> None:
    live = Owner(by.client, by.owner, company, mode="live")
    invoice = id_of(await live.post("/v1/invoices", "i-1", INVOICE))
    assert await outbox(admin) == [
        (
            "invoicing.invoice.issued",
            1,
            "invoicing",
            ORGANIZATION,
            parse_id(COMPANY_PREFIX, company),
            "live",
            parse_id(INVOICE_PREFIX, invoice),
            1,
            ISSUED,
        )
    ]


async def test_a_draft_is_announced_when_it_is_issued_and_a_void_once(
    by: Owner, company: str, admin: psycopg.AsyncConnection
) -> None:
    draft = {**INVOICE, "draft": True}
    invoice = id_of(await by.post("/v1/invoices", "d-1", draft))
    assert await outbox(admin) == []  # a draft is no fact yet
    id_of(await by.post(f"/v1/invoices/{invoice}/issue", "issue-1", version=1))
    note = id_of(await by.post("/v1/invoices", "n-1", {**draft, "type": "credit_note", "corrects": invoice}))
    id_of(await by.post(f"/v1/invoices/{note}/issue", "issue-2", version=1))
    id_of(await by.post(f"/v1/invoices/{note}/void", "void-1", {}, version=2))
    company_id = parse_id(COMPANY_PREFIX, company)
    invoice_id, note_id = parse_id(INVOICE_PREFIX, invoice), parse_id(INVOICE_PREFIX, note)
    credited = {"kind": "credit_note", "direction": "sales"}
    assert await outbox(admin) == [
        ("invoicing.invoice.issued", 1, "invoicing", ORGANIZATION, company_id, "test", invoice_id, 2, ISSUED),
        (
            "invoicing.invoice.issued",
            1,
            "invoicing",
            ORGANIZATION,
            company_id,
            "test",
            note_id,
            2,
            credited | {"corrects": str(invoice_id)},
        ),
        ("invoicing.invoice.voided", 1, "invoicing", ORGANIZATION, company_id, "test", note_id, 3, credited),
    ]


async def test_a_refused_or_replayed_request_announces_nothing(
    by: Owner, admin: psycopg.AsyncConnection
) -> None:
    keyed = {**INVOICE, "external_reference": "ORD-1"}
    invoice = id_of(await by.post("/v1/invoices", "i-1", keyed))
    id_of(
        await by.post(
            "/v1/invoices", "n-1", {**INVOICE, "type": "credit_note", "corrects": invoice, "draft": True}
        )
    )
    announced = await outbox(admin)
    assert [(row[0], row[6]) for row in announced] == [
        ("invoicing.invoice.issued", parse_id(INVOICE_PREFIX, invoice))
    ]
    refused: tuple[tuple[str, str, Mapping[str, object] | None, int | None, int], ...] = (
        ("/v1/invoices", "i-2", {**INVOICE, "lines": [{**LINE, "quantity": "0"}]}, None, 422),
        ("/v1/invoices", "i-3", keyed, None, 409),  # its business key is taken
        (f"/v1/invoices/{invoice}/issue", "issue-1", None, 1, 409),  # issued already
        (f"/v1/invoices/{invoice}/void", "void-1", {}, 2, 412),  # not at the version If-Match names
        (f"/v1/invoices/{invoice}/void", "void-2", {}, 1, 409),  # a draft note corrects it
    )
    for path, key, body, version, status in refused:
        response = await by.post(path, key, body, version)
        assert response.status_code == status, (path, response.text)
    replayed = await by.post("/v1/invoices", "i-1", keyed)
    assert (replayed.status_code, replayed.headers["idempotent-replayed"]) == (201, "true")
    assert await outbox(admin) == announced
