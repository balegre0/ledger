"""``POST /v1/invoices`` through fiscal to do-ecf-v1 (§8.5): what the channel refuses; the synchronous path.

The ``api`` app serves identity, invoicing, fiscal (whose ``IssuePolicy`` asks the channel) and the signer
(its port the test's, signing with the session's key) beside the channel. A pending enrollment refuses the
invoice (fiscal's ``fiscal_enrollment_pending``); an active one has the channel check the draft: a type
without a range to number it is ``do_ecf_range_missing``, and a company that states no fiscal address (DGII's
``DireccionEmisor``, Task 35) is ``do_ecf_company_address_missing``, nothing stored either way.

A company at its fiscal address issues for real (Task 20's plan cases, possible since Task 35): the request
numbers and signs within its budget and answers the e-NCF, the security code and the QR's URL; with a signer
too slow for the budget it answers the e-NCF alone and leaves the signature to ``do_ecf.advance`` (the job's
own suite, ``test_advance.py``, drives it to DGII's answer; the whole path through the worker is Task 26's
end-to-end test); a request whose ``after_issue`` fails after the invoice committed is numbered by the
recovery consumer, once however often it runs; and the printed representation of a signed document answers
a PDF.
"""

import asyncio
from typing import Final, final, override
from urllib.parse import parse_qs, urlsplit
lazy from datetime import timedelta
lazy from uuid import UUID

import pytest
from psycopg.rows import dict_row
from pydantic import TypeAdapter
lazy import httpx
lazy import psycopg

from ledger_country_do.channels.ecf_v1.adapters.channel import DoEcfChannel
from ledger_fiscal_kit.ports import SignerPort
from ledger_kernel.ids import parse_id
from ledger_platform.data.codec import JsonValue
from ledger_platform.events.consumers import Outside, raw_event_of
from ledger_platform.runtime.app import App
from ledger_platform.runtime.facade import facade
from ledger_testing.api import document
from ledger_testing.signer import TestSigner, with_test_signer
lazy from ledger_country_do_testing.local_signer import KeyPairSigner
lazy from ledger_fiscal_kit.channels import Issued
lazy from ledger_fiscal_kit.drafts import FiscalDraft
lazy from ledger_fiscal_kit.ports import CertificateValidity, SeedToSign, Signed, XmlToSign
lazy from ledger_kernel.tenancy import Tenant
lazy from ledger_platform.config.settings import Settings
lazy from ledger_platform.events.consumers import OutsideHandler, RawEvent
lazy from ledger_platform.runtime.calls import SystemCall
lazy from ledger_testing.api import TestPrincipal
lazy from ledger_testing.modules import ModuleUnderTest

ENROLLMENTS: Final = "/v1/fiscal/enrollments"
RANGES: Final = "/v1/do/numbering_ranges"
A31: Final = {"environment": "testecf", "ecf_type": "31", "first": 1, "last": 100, "expires_on": "2028-12-31"}
LINE: Final = {
    "description": "CEMENTO GRIS 42.5 KG",
    "quantity": "2",
    "unit_price": "500.00",
    "taxes": ["do.itbis.standard"],
}
BUYER: Final = {
    "legal_name": "CONSTRUCTORA DEL CARIBE SRL",
    "tax_id": {"scheme": "do.rnc", "value": "131098193"},
}
INVOICE: Final = {"type": "invoice", "buyer": BUYER, "lines": [LINE]}
ADDRESS: Final = {"line": "AV. DUARTE 100, VILLA CONSUELO", "municipality": "010100", "province": "010000"}
"""The company's fiscal address as DGII has it: in Santo Domingo de Guzmán, Distrito Nacional."""
FIRST: Final = "E310000000001"
ERRORS: Final = TypeAdapter(list[dict[str, str | None]])
FISCAL: Final = TypeAdapter(list[dict[str, JsonValue]])
DETAILS: Final = TypeAdapter(dict[str, JsonValue])
ROWS: Final = TypeAdapter(list[tuple[str, int, str]])
JOBS: Final = TypeAdapter(list[tuple[str, str]])


@final
class GatedSigner(SignerPort):
    """The test's signer, which a test may hold: while ``held``, signing a document waits for its release."""

    __slots__ = ("_inner", "held")

    def __init__(self, inner: TestSigner) -> None:
        self._inner = inner
        self.held: asyncio.Event | None = None

    @facade
    @override
    async def sign_xml(self, call: SystemCall, request: XmlToSign) -> Signed:
        if self.held is not None:
            await self.held.wait()
        return await self._inner.sign_xml(call, request)

    @facade
    @override
    async def sign_seed(self, call: SystemCall, request: SeedToSign) -> Signed:
        return await self._inner.sign_seed(call, request)

    @facade
    @override
    async def certificate_validity(self, call: SystemCall, tenant: Tenant) -> CertificateValidity | None:
        return await self._inner.certificate_validity(call, tenant)


@pytest.fixture
def gated(signer: KeyPairSigner) -> GatedSigner:
    """Signs with the session's key (the channel verifies what it signs), unless a test holds it."""
    return GatedSigner(TestSigner(sign=signer.sign, sign_seed=signer.sign))


@pytest.fixture
def api(
    archived: Settings, module_under_test: ModuleUnderTest, gated: GatedSigner, channel_environment: None
) -> App:
    """The conftest's ``api`` app, its signer the test's ``gated`` one."""
    del channel_environment
    return App("api", settings=archived, discover=with_test_signer(module_under_test.discovering(), gated))


def posting(principal: TestPrincipal, company: str, key: str) -> dict[str, str]:
    return principal.posting(key) | {"Ledger-Company": company}


def reading(principal: TestPrincipal, company: str) -> dict[str, str]:
    return principal.bearer() | {"Ledger-Company": company, "Ledger-Mode": "test"}


async def enrolled(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str, *, ranged: bool = True
) -> None:
    """``company`` enrolled on do-ecf-v1 in ``testecf``: active once a range of 31 is registered."""
    body = {"channel": "do-ecf-v1", "environment": "testecf"}
    response = await client.post(ENROLLMENTS, json=body, headers=posting(owner, company, "enroll"))
    assert response.status_code == 201, response.text
    if not ranged:
        return
    registered = await client.post(RANGES, json=A31, headers=posting(owner, company, "range"))
    assert registered.status_code == 201, registered.text
    reviewed = await client.get(f"{ENROLLMENTS}/{document(response)['id']}", headers=reading(owner, company))
    assert document(reviewed)["status"] == "active"


async def located(client: httpx.AsyncClient, owner: TestPrincipal, company: str) -> None:
    """The company at its fiscal address, stated through identity's API (Task 35)."""
    response = await client.patch(
        f"/v1/companies/{company}", json={"address": ADDRESS}, headers=owner.bearer() | {"If-Match": "*"}
    )
    assert response.status_code == 200, response.text


async def refused(client: httpx.AsyncClient, owner: TestPrincipal, company: str, body: object) -> list[str]:
    """The codes of the objections ``body`` is refused with (422 ``invalid_document``)."""
    response = await client.post("/v1/invoices", json=body, headers=posting(owner, company, "invoice"))
    assert (response.status_code, document(response)["code"]) == (422, "invalid_document"), response.text
    return [str(error["code"]) for error in ERRORS.validate_python(document(response)["errors"])]


async def issued(client: httpx.AsyncClient, owner: TestPrincipal, company: str) -> dict[str, object]:
    """The invoice ``INVOICE`` as ``POST /v1/invoices`` answered it (201)."""
    response = await client.post("/v1/invoices", json=INVOICE, headers=posting(owner, company, "invoice"))
    assert response.status_code == 201, response.text
    return document(response)


def channel_status(invoice: dict[str, object]) -> tuple[dict[str, JsonValue], dict[str, JsonValue]]:
    """The invoice's one ``fiscal[]`` entry, and the channel's details in it (under ``do``)."""
    (entry,) = FISCAL.validate_python(invoice["fiscal"])
    return entry, DETAILS.validate_python(entry["do"])


async def documents(admin: psycopg.AsyncConnection, invoice: UUID) -> list[tuple[str, int, str]]:
    """``(ecf_type, sequence, state)`` of every do-ecf-v1 document of ``invoice``."""
    rows = await admin.execute(
        "SELECT ecf_type, sequence, state FROM do_ecf.documents WHERE invoice_id = %s", (invoice,)
    )
    return ROWS.validate_python(await rows.fetchall())


async def published(admin: psycopg.AsyncConnection, kind: str, subject: UUID) -> list[RawEvent]:
    """The events of ``kind`` invoicing published about ``subject``, as an inbox would hold them."""
    cursor = admin.cursor(row_factory=dict_row)
    await cursor.execute("SELECT * FROM invoicing.outbox WHERE type = %s AND subject = %s", (kind, subject))
    return [raw_event_of({**row, "event_id": row["id"]}) for row in await cursor.fetchall()]


def recovery(api: App) -> OutsideHandler:
    """fiscal's ``fiscal.commercial_documents`` handler, as the worker's runner calls it."""
    (member,) = [each for each in api.members if each.name == "fiscal"]
    (consumer,) = [each for each in member.consumers if each.name == "fiscal.commercial_documents"]
    handler = consumer.handler
    assert isinstance(handler, Outside)
    return handler.handler


# ── What the channel refuses ────────────────────────────────────────────────────


async def test_a_pending_enrollment_refuses_the_invoice(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str
) -> None:
    await enrolled(client, owner, company, ranged=False)
    assert await refused(client, owner, company, INVOICE) == ["fiscal_enrollment_pending"]


async def test_a_type_without_a_range_to_number_it_is_objected_and_nothing_is_stored(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str, admin: psycopg.AsyncConnection
) -> None:
    await enrolled(client, owner, company)
    await located(client, owner, company)
    consumer = {"type": "invoice", "lines": [LINE]}  # no buyer's RNC: a 32, whose range is not registered
    assert await refused(client, owner, company, consumer) == ["do_ecf_range_missing"]
    stored = await admin.execute("SELECT count(*) FROM invoicing.invoices")
    assert await stored.fetchall() == [(0,)]


async def test_a_company_that_states_no_fiscal_address_is_refused_with_the_code_that_says_so(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str, admin: psycopg.AsyncConnection
) -> None:
    """Task 35: every e-CF requires ``DireccionEmisor``; the company states it, not the invoice."""
    await enrolled(client, owner, company)
    assert await refused(client, owner, company, INVOICE) == ["do_ecf_company_address_missing"]
    stored = await admin.execute("SELECT count(*) FROM invoicing.invoices")
    assert await stored.fetchall() == [(0,)]


# ── The synchronous path (§8.5 step 4) ──────────────────────────────────────────


async def test_an_invoice_is_numbered_and_signed_within_the_budget_with_its_security_code_and_qr(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str, admin: psycopg.AsyncConnection
) -> None:
    await enrolled(client, owner, company)
    await located(client, owner, company)
    invoice = await issued(client, owner, company)
    assert invoice["seller"] == {
        "legal_name": "Ferreteria Duarte SRL",
        "tax_id": {"scheme": "do.rnc", "value": "101850043"},
        "address": ADDRESS,
    }
    entry, details = channel_status(invoice)
    assert (entry["channel"], entry["environment"], entry["status"]) == ("do-ecf-v1", "testecf", "pending")
    assert details["encf"] == FIRST
    security_code, qr_url = details["security_code"], details["qr_url"]
    assert isinstance(security_code, str)
    assert len(security_code) == 6
    assert isinstance(qr_url, str)
    assert qr_url.startswith("https://")
    assert parse_qs(urlsplit(qr_url).query)["CodigoSeguridad"] == [security_code]  # "+" is sent as %2B
    assert await documents(admin, parse_id("inv", str(invoice["id"]))) == [("31", 1, "signed")]


async def test_a_signer_too_slow_for_the_budget_leaves_the_number_and_the_signature_to_the_job(
    client: httpx.AsyncClient,
    owner: TestPrincipal,
    company: str,
    admin: psycopg.AsyncConnection,
    gated: GatedSigner,
) -> None:
    """The request answers within its budget with the e-NCF and no security code; ``do_ecf.advance`` signs."""
    await enrolled(client, owner, company)
    await located(client, owner, company)
    gated.held = asyncio.Event()
    # The signer is held until the end of the test, so a request that waited for it would never answer. No
    # tighter wall clock bound: under a loaded gate the 1.5 s budget plus the request's own work ran past 2 s.
    # The budget is version 2 below (an invoicing timeout leaves version 1 and logs after_issue_timed_out).
    async with asyncio.timeout(10):
        invoice = await issued(client, owner, company)
    entry, details = channel_status(invoice)
    assert entry["version"] == 2  # fiscal's pending 1, the channel's numbered 1 + 1
    assert (details["encf"], details.get("security_code"), details.get("qr_url")) == (FIRST, None, None)
    invoice_id = parse_id("inv", str(invoice["id"]))
    assert await documents(admin, invoice_id) == [("31", 1, "numbered")]
    jobs = await admin.execute("SELECT kind, status FROM do_ecf.jobs WHERE kind = 'do_ecf.advance'")
    assert JOBS.validate_python(await jobs.fetchall()) == [("do_ecf.advance", "ready")]
    gated.held.set()


@pytest.fixture
def crashing(monkeypatch: pytest.MonkeyPatch) -> None:
    """The channel's ``issue`` fails once, as a process dying after the invoice committed, then works."""
    issue = DoEcfChannel.issue
    crashes = [RuntimeError("the process died")]

    async def crash_once(
        channel: DoEcfChannel, call: SystemCall, draft: FiscalDraft, *, sign_within: timedelta | None
    ) -> Issued:
        if crashes:
            raise crashes.pop()
        return await issue(channel, call, draft, sign_within=sign_within)

    monkeypatch.setattr(DoEcfChannel, "issue", crash_once)


@pytest.mark.usefixtures("crashing")
async def test_an_invoice_whose_request_failed_after_it_was_issued_is_numbered_once_by_the_recovery_path(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str, admin: psycopg.AsyncConnection, api: App
) -> None:
    """The channel fails once after the invoice committed (``crashing``): the consumer of
    ``invoicing.invoice.issued`` numbers it, and a second delivery numbers nothing more (Task 12's note
    12(b))."""
    await enrolled(client, owner, company)
    await located(client, owner, company)
    invoice = await issued(client, owner, company)
    entry, details = channel_status(invoice)
    assert (entry["status"], entry["version"], details.get("encf")) == ("pending", 1, None)
    invoice_id = parse_id("inv", str(invoice["id"]))
    assert await documents(admin, invoice_id) == []
    (event,) = await published(admin, "invoicing.invoice.issued", invoice_id)
    recover = recovery(api)
    await recover(event)
    await recover(event)
    assert await documents(admin, invoice_id) == [("31", 1, "numbered")]
    read = await client.get(f"/v1/invoices/{invoice['id']}", headers=reading(owner, company))
    assert channel_status(document(read))[1]["encf"] == FIRST


async def test_a_signed_invoice_s_printed_representation_is_a_pdf(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str
) -> None:
    await enrolled(client, owner, company)
    await located(client, owner, company)
    invoice = await issued(client, owner, company)
    printed = await client.get(
        f"/v1/invoices/{invoice['id']}/representation",
        params={"channel": "do-ecf-v1"},
        headers=reading(owner, company),
    )
    assert printed.status_code == 200, printed.text
    assert printed.headers["content-type"] == "application/pdf"
    assert printed.content.startswith(b"%PDF-")
    assert FIRST in printed.headers["content-disposition"]
