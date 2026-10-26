"""``POST /v1/invoices`` with fiscal's ``IssuePolicy`` (§8.5 steps 2 to 5, §7.6, Decisions 5, 24).

invoicing, identity and fiscal served by ``api``, with the conftest's ``FakeChannel`` as the one channel: an
active enrollment routes an issued invoice to it (its ``fiscal[]`` pending, numbered and, within the budget,
signed); a pending one refuses the invoice (``fiscal_enrollment_pending``, nothing stored); with none it
issues with ``fiscal: []``, and a field of the regime's namespace the regime does not declare is then
``field_unknown``. A channel with no number to give once the invoice is committed leaves it issued, never a
409. The mirror gets ``pending`` before anything is numbered, so a routed invoice can no longer be voided.
The representation is the channel's, for a document routed to it.
"""

from datetime import timedelta
from typing import Final, LiteralString, Protocol
lazy from collections.abc import Awaitable, Callable, Mapping
lazy from uuid import UUID

import pytest
from psycopg.rows import dict_row
from pydantic import TypeAdapter
from structlog.testing import capture_logs
lazy import httpx
lazy import psycopg

from ledger_fiscal.adapters.postgres.unit import PgUnits
from ledger_fiscal.application.enrolling import Enrolling
from ledger_fiscal.application.issuing import FiscalPolicy, Router
from ledger_fiscal_kit.channels import ChannelRegistry, Representation, Requirements
from ledger_fiscal_kit.objections import Objection
from ledger_identity.adapters.postgres.unit import PgUnits as IdentityUnits
from ledger_identity.application.facade import IdentityFacade
from ledger_invoicing.adapters.postgres.unit import PgUnits as InvoicingUnits
from ledger_invoicing.api import InvoiceId
from ledger_invoicing.application.facade import InvoicingFacade
from ledger_invoicing.application.mirror import FiscalMirroring
from ledger_invoicing.application.queries import InvoicingQueries
from ledger_kernel.access import Principal, PrincipalKind, SubjectId
from ledger_kernel.clock import utc_now
from ledger_kernel.ids import parse_id
from ledger_kernel.tenancy import Tenant
from ledger_platform.data.codec import JsonValue
from ledger_platform.runtime.calls import Call, SystemCall
from ledger_platform.runtime.contexts import AssembledChannels
from ledger_platform.runtime.registries import Regimes
from ledger_testing.api import document
lazy from ledger_fiscal_kit.channels import EnrollmentState, StatusUpdate
lazy from ledger_fiscal_kit.drafts import DocumentRef, FiscalDraft
lazy from ledger_platform.runtime.app import App
lazy from ledger_testing.api import TestPrincipal

PATH: Final = "/v1/invoices"
ENROLLMENTS: Final = "/v1/fiscal/enrollments"
FAKE: Final = "do-fake-v1"
NOTHING_DUE: Final = Requirements(currently_due=())
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
FISCAL: Final = TypeAdapter(list[dict[str, JsonValue]])
OBJECT: Final = TypeAdapter(dict[str, JsonValue])
ERRORS: Final = TypeAdapter(list[dict[str, str | None]])
ROWS: Final = TypeAdapter(list[tuple[str, int]])
EVENTS: Final = TypeAdapter(list[str])
PDF: Final = b"%PDF-1.7 fake"
FISCAL_UPDATES: Final = """
    SELECT payload FROM invoicing.outbox WHERE type = 'invoicing.invoice.fiscal_updated' AND subject = %s
    ORDER BY occurred_at, id
"""


class FakeChannel(Protocol):
    """The conftest's channel, as these tests use it (see the conftest)."""

    due: Requirements
    asked: list[tuple[SystemCall, EnrollmentState]]
    objections: list[Objection]
    checked: list[tuple[SystemCall, FiscalDraft]]
    issued: list[tuple[SystemCall, FiscalDraft, object]]
    documents: dict[DocumentRef, StatusUpdate]
    numbered: int
    slow: bool
    no_number: bool
    before_issue: Callable[[FiscalDraft], Awaitable[None]] | None
    printed: Representation | None


def headers(principal: TestPrincipal, company: str, key: str | None = None) -> dict[str, str]:
    """A request's headers in test mode for ``company``: a ``POST``'s when ``key`` is given."""
    if key is None:
        return principal.bearer() | {"Ledger-Company": company, "Ledger-Mode": "test"}
    return principal.posting(key) | {"Ledger-Company": company}


async def enroll(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str, fake_channel: FakeChannel, *, active: bool
) -> str:
    """Enroll ``company`` on the fake channel: active when it requires nothing, else pending."""
    fake_channel.due = NOTHING_DUE if active else Requirements(currently_due=("do_ecf.range", "certificate"))
    body = {"channel": FAKE, "environment": "testecf"}
    response = await client.post(ENROLLMENTS, json=body, headers=headers(owner, company, "enroll-1"))
    assert response.status_code == 201, response.text
    assert document(response)["status"] == ("active" if active else "pending")
    return str(document(response)["id"])


async def issued(
    client: httpx.AsyncClient,
    owner: TestPrincipal,
    company: str,
    body: Mapping[str, object] = INVOICE,
    key: str = "invoice-1",
) -> httpx.Response:
    return await client.post(PATH, json=dict(body), headers=headers(owner, company, key))


def fiscal_of(response: httpx.Response) -> list[dict[str, JsonValue]]:
    return FISCAL.validate_python(document(response)["fiscal"])


def details(entry: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    return OBJECT.validate_python(entry["do"])


def invoice_of(response: httpx.Response) -> UUID:
    """The id of the invoice ``response`` answers."""
    return parse_id("inv", str(document(response)["id"]))


async def fiscal_updates(admin: psycopg.AsyncConnection, invoice: UUID) -> list[tuple[str, int]]:
    """``(status, version)`` of every ``invoicing.invoice.fiscal_updated`` of ``invoice``, in order."""
    cursor = admin.cursor(row_factory=dict_row)
    await cursor.execute(
        FISCAL_UPDATES,
        (invoice,),
    )
    found = [OBJECT.validate_python(row["payload"]) for row in await cursor.fetchall()]
    return ROWS.validate_python([(each["status"], each["version"]) for each in found])


async def count(admin: psycopg.AsyncConnection, query: LiteralString) -> int:
    cursor = await admin.execute(query)
    row = await cursor.fetchone()
    assert row is not None
    return TypeAdapter(int).validate_python(row[0])


@pytest.fixture
def channel(fake_channel: FakeChannel) -> FakeChannel:
    return fake_channel


async def test_an_active_enrollment_routes_the_invoice_numbered_and_signed_within_the_budget(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str, channel: FakeChannel
) -> None:
    await enroll(client, owner, company, channel, active=True)
    response = await issued(client, owner, company)
    assert response.status_code == 201, response.text
    (entry,) = fiscal_of(response)
    assert (entry["channel"], entry["environment"], entry["status"], entry["version"]) == (
        FAKE,
        "testecf",
        "pending",
        3,  # fiscal's pending is 1; the channel's numbered and signed versions 1 and 2 are 2 and 3
    )
    assert details(entry) == {"encf": "E310000000001", "security_code": "Ab12Cd"}
    ((call, draft, budget),) = channel.issued
    assert (call.reason, call.scope, draft.channel, draft.environment) == (
        "fiscal.issue",
        draft.scope,
        FAKE,
        "testecf",
    )
    assert budget is not None
    ((checking, checked),) = channel.checked
    assert (checking.reason, checked.document) == ("fiscal.check", draft.document)


async def test_a_signer_slower_than_the_budget_leaves_the_invoice_numbered_without_a_security_code(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str, channel: FakeChannel
) -> None:
    await enroll(client, owner, company, channel, active=True)
    channel.slow = True
    response = await issued(client, owner, company)
    (entry,) = fiscal_of(response)
    assert (entry["version"], details(entry)) == (2, {"encf": "E310000000001", "security_code": None})


async def test_a_pending_enrollment_refuses_the_invoice_and_nothing_is_stored(
    client: httpx.AsyncClient,
    owner: TestPrincipal,
    company: str,
    channel: FakeChannel,
    admin: psycopg.AsyncConnection,
) -> None:
    await enroll(client, owner, company, channel, active=False)
    response = await issued(client, owner, company)
    assert (response.status_code, document(response)["code"]) == (422, "invalid_document")
    assert ERRORS.validate_python(document(response)["errors"]) == [
        {
            "code": "fiscal_enrollment_pending",
            "field": None,
            "message": (
                "the company's enrollment on do-fake-v1 is not active yet: "
                "it still requires do_ecf.range, certificate"
            ),
        }
    ]
    assert await count(admin, "SELECT count(*) FROM invoicing.invoices") == 0
    assert channel.checked == []


async def test_without_an_enrollment_the_invoice_issues_with_no_fiscal_status(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str, channel: FakeChannel
) -> None:
    response = await issued(client, owner, company)
    assert (response.status_code, fiscal_of(response)) == (201, [])
    assert (channel.checked, channel.issued) == ([], [])


async def test_a_field_the_regime_does_not_declare_is_unknown_when_no_channel_routes_the_invoice(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str
) -> None:
    """Decision 24, Task 12's note 4: with fiscal's policy in the chain invoicing keeps undeclared keys; with
    no channel routing the document, nothing may read them."""
    body = INVOICE | {"fiscal": {"do": {"ecf_type": "31", "income_type": "01", "Odd Name": "x"}}}
    response = await issued(client, owner, company, body)
    assert (response.status_code, document(response)["code"]) == (422, "invalid_document")
    assert [
        (error["code"], error["field"]) for error in ERRORS.validate_python(document(response)["errors"])
    ] == [
        ("field_unknown", "fiscal.do"),
        ("field_unknown", "fiscal.do.income_type"),
    ]


async def test_a_channel_reads_the_fields_of_the_regime_s_namespace_and_its_objections_refuse_the_invoice(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str, channel: FakeChannel
) -> None:
    await enroll(client, owner, company, channel, active=True)
    channel.objections = [
        Objection(code="do_ecf_range_missing", field=None, message="no e-NCF of the type is available now")
    ]
    body = INVOICE | {"fiscal": {"do": {"withholdings": {"0": {"itbis": "18.00"}}}}}
    response = await issued(client, owner, company, body)
    assert (response.status_code, document(response)["code"]) == (422, "invalid_document")
    assert ERRORS.validate_python(document(response)["errors"]) == [
        {"code": "do_ecf_range_missing", "field": None, "message": "no e-NCF of the type is available now"}
    ]
    ((_, draft),) = channel.checked
    assert dict(draft.fields) == {"withholdings": {"0": {"itbis": "18.00"}}}
    assert channel.issued == []


async def test_a_channel_with_no_number_left_once_the_invoice_is_committed_leaves_it_issued_and_pending(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str, channel: FakeChannel
) -> None:
    """Task 18's review: ``after_issue`` catches the channel's "no number now" (never a client's 409)."""
    await enroll(client, owner, company, channel, active=True)
    channel.no_number = True
    with capture_logs() as logged:
        response = await issued(client, owner, company)
    assert response.status_code == 201, response.text
    events = EVENTS.validate_python([entry.get("event") for entry in logged])
    assert [event for event in events if event.startswith("invoicing.after_issue")] == []
    (entry,) = fiscal_of(response)
    assert (entry["status"], entry["version"], details(entry)) == ("pending", 1, {})
    assert channel.numbered == 0


async def test_pending_is_mirrored_before_the_channel_numbers_and_a_routed_invoice_cannot_be_voided(
    client: httpx.AsyncClient,
    owner: TestPrincipal,
    company: str,
    channel: FakeChannel,
    admin: psycopg.AsyncConnection,
) -> None:
    """Task 12's note 12(a): a void is refused while a mirrored status is neither rejected nor voided."""
    await enroll(client, owner, company, channel, active=True)
    seen: list[tuple[str, int]] = []

    async def mirrored_first(draft: FiscalDraft) -> None:
        cursor = await admin.execute(
            "SELECT status, version FROM invoicing.fiscal_statuses WHERE invoice_id = %s", (draft.document,)
        )
        seen.extend(ROWS.validate_python(await cursor.fetchall()))

    channel.before_issue = mirrored_first
    response = await issued(client, owner, company)
    assert seen == [("pending", 1)]
    invoice = str(document(response)["id"])
    version = str(document(response)["version"])
    void = await client.post(
        f"{PATH}/{invoice}/void",
        json={},
        headers=headers(owner, company, "void-1") | {"If-Match": f'"{version}"'},
    )
    assert (void.status_code, document(void)["code"]) == (409, "invoice_not_voidable")
    assert await fiscal_updates(admin, invoice_of(response)) == [("pending", 1), ("pending", 3)]


async def test_the_representation_is_the_channel_s_for_a_document_routed_to_it(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str, channel: FakeChannel
) -> None:
    await enroll(client, owner, company, channel, active=True)
    response = await issued(client, owner, company)
    invoice = str(document(response)["id"])
    path = f"{PATH}/{invoice}/representation"
    unavailable = await client.get(path, params={"channel": FAKE}, headers=headers(owner, company))
    assert (unavailable.status_code, document(unavailable)["code"]) == (503, "fiscal_tests_not_printable")
    channel.printed = Representation(media_type="application/pdf", content=PDF, filename="E310000000001.pdf")
    printed = await client.get(path, params={"channel": FAKE}, headers=headers(owner, company))
    assert (printed.status_code, printed.headers["content-type"], printed.content) == (
        200,
        "application/pdf",
        PDF,
    )
    other = await client.get(path, params={"channel": "do-other-v1"}, headers=headers(owner, company))
    assert (other.status_code, document(other)["code"]) == (404, "representation_unavailable")


async def test_an_installed_channel_the_invoice_was_never_routed_to_renders_nothing(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str, channel: FakeChannel
) -> None:
    """Review M5: the routing names the channel that renders; an installed one is not enough."""
    channel.printed = Representation(media_type="application/pdf", content=PDF, filename="E310000000001.pdf")
    response = await issued(client, owner, company)  # no enrollment: routed nowhere
    printed = await client.get(
        f"{PATH}/{document(response)['id']}/representation",
        params={"channel": FAKE},
        headers=headers(owner, company),
    )
    assert (printed.status_code, document(printed)["code"]) == (404, "representation_unavailable")


def by_hand(api: App, channel: FakeChannel, ticks: tuple[float, ...]) -> tuple[FiscalPolicy, InvoicingFacade]:
    """fiscal's policy over the app's units and ``channel``, its clock reading ``ticks``; invoicing's API."""
    regimes = Regimes.load()
    fake_channels = AssembledChannels()
    fake_channels.add(FAKE, channel)
    fake_channels.seal()
    registry = ChannelRegistry(fake_channels)
    units = PgUnits(api.bound("fiscal"))
    invoicing_units = InvoicingUnits(api.bound("invoicing"))
    invoicing = InvoicingFacade(
        InvoicingQueries(units=invoicing_units), FiscalMirroring(units=invoicing_units)
    )
    identity = IdentityFacade(IdentityUnits(api.bound("identity")))
    router = Router(
        units=units,
        channels=registry,
        invoicing=invoicing,
        enrolling=Enrolling(units, registry, identity=identity, regimes=regimes, clock=utc_now),
        regimes=regimes,
        clock=utc_now,
    )
    readings = iter(ticks)
    return FiscalPolicy(router, regimes=regimes, monotonic=lambda: next(readings)), invoicing


def request_of(scope: Tenant) -> Call:
    """A request of the owner (subject 2001) in ``scope``."""
    principal = Principal(
        subject=SubjectId("2001"),
        organization=scope.organization,
        provider_organization="1001",
        kind=PrincipalKind.HUMAN,
    )
    return Call(principal=principal, scope=scope, request_id="r-1")


async def test_a_budget_already_spent_asks_the_channel_to_sign_within_nothing(
    api: App, client: httpx.AsyncClient, owner: TestPrincipal, company: str, channel: FakeChannel
) -> None:
    """Review M4: what is left of the budget is never negative: a spent one asks for no signature in time."""
    await enroll(client, owner, company, channel, active=True)
    _ = await issued(client, owner, company)
    policy, invoicing = by_hand(api, channel, (0.0, 10.0))
    (call, draft, _) = channel.issued[0]
    issued_invoice = await invoicing.document(call, InvoiceId(draft.document))
    await policy.after_issue(request_of(draft.scope), issued_invoice, budget=timedelta(seconds=1.5))
    assert channel.issued[-1][2] == timedelta(0)


async def test_after_issue_routes_through_no_enrollment_made_after_the_invoice_was_issued(
    api: App, client: httpx.AsyncClient, owner: TestPrincipal, company: str, channel: FakeChannel
) -> None:
    """Review minor 1: as the recovery path, ``after_issue`` routes only through enrollments made by the time
    the invoice was issued (one enrolled between the commit and ``after_issue`` does not take it)."""
    response = await issued(client, owner, company)  # no enrollment yet: fiscal: []
    await enroll(client, owner, company, channel, active=True)
    policy, invoicing = by_hand(api, channel, (0.0, 0.0))
    (_, state) = channel.asked[-1]  # the enrollment's company, as the channel was asked its requirements
    scope = Tenant(state.organization, state.company, state.mode)
    found = await invoicing.document(SystemCall(reason="test", scope=scope), InvoiceId(invoice_of(response)))
    await policy.after_issue(request_of(found.tenant), found, budget=timedelta(seconds=1.5))
    assert channel.issued == []
