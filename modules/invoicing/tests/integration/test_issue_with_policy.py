"""``POST /v1/invoices`` and ``/issue`` with an ``IssuePolicy`` chain (§8.5 steps 2-5, ADR 0011).

Two modules of the test's own implement ``IssuePolicy``, at orders 10 and 20 (``Scripted``): what they
object to refuses the document (422 ``invalid_document``, nothing stored); once it is issued their
``after_issue`` runs within the budget, and what they make of it (a fiscal status they apply, as fiscal will)
is in the answer, which is the document read again. A policy that fails or runs out of time costs the client
nothing: the document is issued.
"""

import asyncio
from datetime import timedelta
from typing import Final, final, override
lazy from collections.abc import Awaitable, Callable, Mapping, Sequence

import pytest
lazy import httpx

from ledger_invoicing.adapters.postgres.unit import PgUnits
from ledger_invoicing.api import (
    INVOICING_API,
    ISSUE_BUDGET,
    FiscalStatusInfo,
    FiscalStatusName,
    InvoiceInfo,
    IssuePolicy,
    Objection,
)
from ledger_invoicing.application.facade import InvoicingFacade
from ledger_invoicing.application.mirror import FiscalMirroring
from ledger_invoicing.application.queries import InvoicingQueries
from ledger_invoicing.domain.ids import INVOICE_PREFIX
from ledger_kernel.ids import parse_id
from ledger_platform.runtime import Call, SystemCall, facade
from ledger_testing.api import document, page
lazy from ledger_invoicing.api import InvoicingApi, Renderer
lazy from ledger_platform.data.bound import BoundDatabase
lazy from ledger_platform.runtime import App
lazy from ledger_testing.api import TestPrincipal

LINE: Final = {"description": "Widget", "quantity": "2", "unit_price": "50", "taxes": ["do.itbis.standard"]}
INVOICE: Final = {"type": "invoice", "lines": [LINE]}
FIELDS: Final = {"do": {"modification_code": "3", "withholdings": {"0": {"itbis": "18.00"}}}}
"""Fields the regime does not declare: a channel's, which only the chain may accept (Decision 24)."""


type AfterIssue = Callable[[Call, InvoiceInfo, timedelta], Awaitable[None]]
"""What a ``Scripted`` policy does once a document is issued, as a test tells it."""


@final
class Scripted(IssuePolicy):
    """An ``IssuePolicy`` a test scripts: what it objects to, and what it does once a document is issued."""

    def __init__(self) -> None:
        self.objections: list[Objection] = []
        self.after: AfterIssue | None = None
        self.checked: list[InvoiceInfo] = []
        self.budgets: list[timedelta] = []

    @override
    @facade
    async def check(self, call: Call, document: InvoiceInfo) -> Sequence[Objection]:
        self.checked.append(document)
        return tuple(self.objections)

    @override
    @facade
    async def after_issue(self, call: Call, document: InvoiceInfo, *, budget: timedelta) -> None:
        self.budgets.append(budget)
        if self.after is not None:
            await self.after(call, document, budget)


@pytest.fixture
def early() -> Scripted:
    """The policy first in the chain (order 10)."""
    return Scripted()


@pytest.fixture
def late() -> Scripted:
    """The policy second in the chain (order 20), whatever its module's name."""
    return Scripted()


@pytest.fixture
def implementations(
    early: Scripted, late: Scripted
) -> Sequence[tuple[str, int, IssuePolicy | None, Renderer | None]]:
    return (("probe_late", 20, late, None), ("probe_early", 10, early, None))


@pytest.fixture
def api(extended_api: App) -> App:
    """The ``api`` app with the test's two policy modules installed."""
    return extended_api


@pytest.fixture
def invoicing(module_database: BoundDatabase) -> InvoicingApi:
    """invoicing's API over its units, as fiscal's policy reaches it."""
    units = PgUnits(module_database)
    return INVOICING_API.take(InvoicingFacade(InvoicingQueries(units=units), FiscalMirroring(units=units)))


def posting(principal: TestPrincipal, company: str, key: str, version: int | None = None) -> dict[str, str]:
    headers = principal.posting(key, "test") | {"Ledger-Company": company}
    return headers if version is None else headers | {"If-Match": f'"{version}"'}


def reading(principal: TestPrincipal, company: str) -> dict[str, str]:
    return principal.bearer() | {"Ledger-Company": company, "Ledger-Mode": "test"}


def rule(code: str, path: str | None = None) -> Objection:
    return Objection(code=code, field=path, message=f"the rule {code}")


async def test_the_chain_s_objections_refuse_the_document_in_chain_order_and_nothing_is_stored(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str, early: Scripted, late: Scripted
) -> None:
    early.objections.append(rule("fiscal_enrollment_pending"))
    late.objections.extend([rule("do_ecf_range_missing", "type"), rule("do_ecf_isc_unsupported")])
    response = await client.post("/v1/invoices", json=INVOICE, headers=posting(owner, company, "i-1"))
    assert response.status_code == 422
    problem = document(response)
    assert (problem["code"], problem["omitted_errors"]) == ("invalid_document", 0)
    assert problem["errors"] == [
        {"code": "fiscal_enrollment_pending", "field": None, "message": "the rule fiscal_enrollment_pending"},
        {"code": "do_ecf_range_missing", "field": "type", "message": "the rule do_ecf_range_missing"},
        {"code": "do_ecf_isc_unsupported", "field": None, "message": "the rule do_ecf_isc_unsupported"},
    ]
    assert page(await client.get("/v1/invoices", headers=reading(owner, company)))["items"] == []
    assert (early.budgets, late.budgets) == ([], [])  # nothing was issued
    [checked] = early.checked
    assert late.checked == [checked]
    assert (checked.status.value, checked.seller is not None, checked.version) == ("issued", True, 1)


async def test_a_document_the_chain_accepts_is_issued_and_each_after_issue_runs_in_order(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str, early: Scripted, late: Scripted
) -> None:
    ran: list[str] = []

    def recording(name: str) -> AfterIssue:
        async def after(call: Call, issued: InvoiceInfo, budget: timedelta) -> None:
            assert isinstance(call, Call)
            assert budget <= ISSUE_BUDGET
            ran.append(f"{name}:{issued.status.value}:{issued.version}")

        return after

    early.after, late.after = recording("early"), recording("late")
    response = await client.post("/v1/invoices", json=INVOICE, headers=posting(owner, company, "i-1"))
    assert response.status_code == 201, response.text
    assert ran == ["early:issued:1", "late:issued:1"]
    made = document(response)
    assert (made["status"], made["fiscal"]) == ("issued", [])
    [checked] = early.checked
    assert parse_id(INVOICE_PREFIX, str(made["id"])) == checked.id  # the id minted before the check


async def test_what_after_issue_applies_is_in_the_answer_and_in_a_replay(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str, early: Scripted, invoicing: InvoicingApi
) -> None:
    async def numbered(call: Call, issued: InvoiceInfo, budget: timedelta) -> None:
        del call, budget
        status = FiscalStatusInfo(
            channel="do-ecf-v1",
            environment="testecf",
            status=FiscalStatusName.PENDING,
            authority_number="E310000000001",
            messages=(),
            regime="do",
            details={"encf": "E310000000001", "security_code": "A1b2C3"},
            updated_at=issued.updated_at,
        )
        system = SystemCall(reason="fiscal.issue", scope=issued.tenant)
        await invoicing.apply_fiscal_status(system, issued.id, status, 2)

    early.after = numbered
    response = await client.post("/v1/invoices", json=INVOICE, headers=posting(owner, company, "i-1"))
    assert response.status_code == 201, response.text
    entry: dict[str, object] = {
        "channel": "do-ecf-v1",
        "environment": "testecf",
        "status": "pending",
        "version": 2,
        "messages": [],
        "do": {"encf": "E310000000001", "security_code": "A1b2C3"},
    }
    made = document(response)
    assert (made["fiscal"], made["version"], response.headers["etag"]) == ([entry], 1, '"1"')
    assert response.headers["location"] == f"/v1/invoices/{made['id']}"
    again = await client.post("/v1/invoices", json=INVOICE, headers=posting(owner, company, "i-1"))
    assert (again.status_code, again.headers["idempotent-replayed"], document(again)) == (201, "true", made)
    assert len(early.budgets) == 1  # a replay issues nothing, so nothing runs after it


async def test_a_policy_that_blocks_or_fails_after_the_issue_leaves_the_document_issued(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str, early: Scripted, late: Scripted
) -> None:
    cancelled = asyncio.Event()

    async def blocks(call: Call, issued: InvoiceInfo, budget: timedelta) -> None:
        del call, issued
        assert budget <= ISSUE_BUDGET
        try:
            await asyncio.Event().wait()  # a signer that never answers
        except asyncio.CancelledError:
            cancelled.set()
            raise

    async def fails(call: Call, issued: InvoiceInfo, budget: timedelta) -> None:
        del call, issued, budget
        raise LookupError

    early.after, late.after = blocks, fails
    response = await client.post("/v1/invoices", json=INVOICE, headers=posting(owner, company, "i-1"))
    assert response.status_code == 201, response.text  # answered once the budget ran out
    assert cancelled.is_set()
    assert late.budgets == [timedelta(0)]  # what was left after the first used it all
    made = document(response)
    read = await client.get(f"/v1/invoices/{made['id']}", headers=reading(owner, company))
    assert (document(read)["status"], document(read)["fiscal"]) == ("issued", [])


async def test_a_draft_is_checked_and_followed_when_it_is_issued(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str, early: Scripted, late: Scripted
) -> None:
    draft = await client.post(
        "/v1/invoices", json={**INVOICE, "draft": True}, headers=posting(owner, company, "d-1")
    )
    assert draft.status_code == 201
    assert (early.checked, early.budgets) == ([], [])
    path = f"/v1/invoices/{document(draft)['id']}/issue"
    late.objections.append(rule("do_ecf_range_missing"))
    refused = await client.post(path, headers=posting(owner, company, "issue-1", version=1))
    assert (refused.status_code, document(refused)["code"]) == (422, "invalid_document")
    late.objections.clear()
    issued = await client.post(path, headers=posting(owner, company, "issue-2", version=1))
    assert (issued.status_code, document(issued)["status"]) == (200, "issued")
    assert [checked.version for checked in early.checked] == [2, 2]
    assert len(early.budgets) == len(late.budgets) == 1


async def test_fields_the_regime_does_not_declare_are_the_chain_s_to_judge(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str, early: Scripted
) -> None:
    body: Mapping[str, object] = {**INVOICE, "fiscal": FIELDS}
    response = await client.post("/v1/invoices", json=body, headers=posting(owner, company, "i-1"))
    assert response.status_code == 201, response.text
    [checked] = early.checked
    assert checked.fields == FIELDS
    listed = page(await client.get("/v1/invoices", headers=reading(owner, company)))
    assert [item["id"] for item in listed["items"]] == [document(response)["id"]]
    early.objections.append(rule("do_ecf_field_unknown", "fiscal.do.modification_code"))
    refused = await client.post("/v1/invoices", json=body, headers=posting(owner, company, "i-2"))
    assert (refused.status_code, document(refused)["code"]) == (422, "invalid_document")
