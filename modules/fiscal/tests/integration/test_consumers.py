"""fiscal's consumers and its job (§3.5, §8.5): the recovery path, the mirror, the channels' requirements.

The handlers are the ``api`` app's own (``App.members``), called with the events invoicing published (read
from its outbox) or with an update of the conftest's ``FakeChannel``, as the worker's runners would call them:

- ``fiscal.commercial_documents`` routes an issued invoice the request did not, once per (invoice, channel):
  a second delivery numbers nothing; an invoice voided before it was routed is never numbered; a channel with
  no number to give gets a ``fiscal.route`` job (Task 18's review), which ``fiscal.requirements`` readies;
- ``fiscal.channel_documents`` keeps the newest status per channel (the mirror's version is the channel's + 1)
  and changes nothing for an older or repeated one, so it is consumed unordered;
- ``fiscal.requirements`` marks the company's enrollments on the channel for review: the next invoice reviews
  them (a pending one that now requires nothing routes it).
"""

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Final, Protocol
from uuid import UUID

import pytest
from psycopg.rows import dict_row
from pydantic import TypeAdapter
lazy import httpx
lazy import psycopg

from ledger_fiscal.adapters.postgres.repositories.enrollments import PgEnrollments
from ledger_fiscal.domain.enrollments import Asked, Enrollment, EnrollmentId
from ledger_fiscal_kit.channels import Requirements
from ledger_fiscal_kit.events import DocumentUpdated, RequirementsChanged
from ledger_fiscal_kit.status import FiscalStatus, Severity
from ledger_kernel.errors import InvalidValueError
from ledger_kernel.ids import CompanyId, OrganizationId, format_id, new_id, parse_id
from ledger_kernel.tenancy import Mode, Tenant
from ledger_platform.data.codec import JsonValue, encode
from ledger_platform.events.consumers import Outside, RawEvent, raw_event_of
from ledger_platform.jobs.outcomes import Done, Job, Park
from ledger_testing.api import document
lazy from ledger_fiscal_kit.channels import StatusUpdate
lazy from ledger_fiscal_kit.drafts import DocumentRef, FiscalDraft
lazy from ledger_platform.events.consumers import Handler, OutsideHandler
lazy from ledger_platform.jobs.outcomes import JobHandler
lazy from ledger_platform.runtime.app import App
lazy from ledger_platform.runtime.assembly import Member
lazy from ledger_platform.runtime.calls import SystemCall
lazy from ledger_testing.api import TestPrincipal

FAKE: Final = "do-fake-v1"
NOTHING_DUE: Final = Requirements(currently_due=())
LINE: Final = {
    "description": "CEMENTO GRIS 42.5 KG",
    "quantity": "2",
    "unit_price": "500.00",
    "taxes": ["do.itbis.standard"],
}
INVOICE: Final = {"type": "invoice", "lines": [LINE]}
OBJECT: Final = TypeAdapter(dict[str, JsonValue])
FISCAL: Final = TypeAdapter(list[dict[str, JsonValue]])
UPDATES: Final = TypeAdapter(list[tuple[str, int]])
JOBS: Final = TypeAdapter(list[tuple[str, str, str | None]])
FLAGS: Final = TypeAdapter(list[tuple[str, bool]])
UUIDS: Final = TypeAdapter(UUID)
BACKDATED: Final = "UPDATE fiscal.enrollments SET created_at = '2000-01-01T00:00:00Z'"
"""The company's enrollment made long before: its invoices are the recovery path's to route."""


class FakeChannel(Protocol):
    """The conftest's channel, as these tests use it (see the conftest)."""

    due: Requirements
    issued: list[tuple[SystemCall, FiscalDraft, object]]
    documents: dict[DocumentRef, StatusUpdate]
    numbered: int
    slow: bool
    no_number: bool
    before_issue: object

    def update(self, draft: FiscalDraft, *, version: int, signed: bool) -> StatusUpdate: ...


def headers(principal: TestPrincipal, company: str, key: str | None = None) -> dict[str, str]:
    if key is None:
        return principal.bearer() | {"Ledger-Company": company, "Ledger-Mode": "test"}
    return principal.posting(key) | {"Ledger-Company": company}


async def enroll(
    client: httpx.AsyncClient,
    owner: TestPrincipal,
    company: str,
    channel: FakeChannel,
    *,
    active: bool = True,
) -> None:
    channel.due = NOTHING_DUE if active else Requirements(currently_due=("do_ecf.range",))
    body = {"channel": FAKE, "environment": "testecf"}
    response = await client.post(
        "/v1/fiscal/enrollments", json=body, headers=headers(owner, company, "enroll")
    )
    assert response.status_code == 201, response.text


async def issued(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str, key: str = "invoice-1", body: object = None
) -> httpx.Response:
    response = await client.post(
        "/v1/invoices", json=INVOICE if body is None else body, headers=headers(owner, company, key)
    )
    assert response.status_code == 201, response.text
    return response


def invoice_of(response: httpx.Response) -> UUID:
    return parse_id("inv", str(document(response)["id"]))


async def read_back(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str, invoice: UUID
) -> list[dict[str, JsonValue]]:
    """The invoice's ``fiscal[]`` as ``GET`` answers it now."""
    response = await client.get(f"/v1/invoices/{format_id('inv', invoice)}", headers=headers(owner, company))
    return FISCAL.validate_python(document(response)["fiscal"])


async def enrolled_tenant(admin: psycopg.AsyncConnection) -> Tenant:
    """The company in test mode of the one enrollment kept."""
    row = await (await admin.execute("SELECT organization_id, company_id FROM fiscal.enrollments")).fetchone()
    assert row is not None
    return Tenant(
        OrganizationId(UUIDS.validate_python(row[0])), CompanyId(UUIDS.validate_python(row[1])), Mode.TEST
    )


def fiscal(api: App) -> Member:
    return next(member for member in api.members if member.name == "fiscal")


def outside(api: App, name: str) -> OutsideHandler:
    (consumer,) = [each for each in fiscal(api).consumers if each.name == name]
    handler = consumer.handler
    assert isinstance(handler, Outside)
    return handler.handler


def inside(api: App, name: str) -> Handler:
    (consumer,) = [each for each in fiscal(api).consumers if each.name == name]
    handler = consumer.handler
    assert not isinstance(handler, Outside)
    return handler


def route_job(api: App) -> JobHandler:
    return fiscal(api).kinds["fiscal.route"][1]


async def published(admin: psycopg.AsyncConnection, kind: str, subject: UUID) -> list[RawEvent]:
    """The events of ``kind`` invoicing published about ``subject``, as an inbox would hold them."""
    cursor = admin.cursor(row_factory=dict_row)
    await cursor.execute(
        "SELECT * FROM invoicing.outbox WHERE type = %s AND subject = %s ORDER BY occurred_at, id",
        (kind, subject),
    )
    return [raw_event_of({**row, "event_id": row["id"]}) for row in await cursor.fetchall()]


async def mirror_updates(admin: psycopg.AsyncConnection, invoice: UUID) -> list[tuple[str, int]]:
    """``(status, version)`` of every ``invoicing.invoice.fiscal_updated`` of ``invoice``, in order."""
    events = await published(admin, "invoicing.invoice.fiscal_updated", invoice)
    return UPDATES.validate_python([(event.payload["status"], event.payload["version"]) for event in events])


def channel_event(scope: Tenant, update: StatusUpdate) -> RawEvent:
    """``fiscal_channel.document.updated`` of ``update``, as the channel's outbox would hold it."""
    event = DocumentUpdated.of(scope, update)
    return RawEvent(
        id=new_id(),
        type=event.TYPE,
        version=event.VERSION,
        source=FAKE,
        scope=scope,
        subject=update.document,
        aggregate_version=update.version,
        occurred_at=update.occurred_at,
        traceparent=None,
        payload=OBJECT.validate_python(encode({"update": update})),
    )


def requirements_event(scope: Tenant) -> RawEvent:
    event = RequirementsChanged.of(scope, channel=FAKE)
    return RawEvent(
        id=new_id(),
        type=event.TYPE,
        version=event.VERSION,
        source=FAKE,
        scope=scope,
        subject=scope.company,
        aggregate_version=0,
        occurred_at=datetime.now(UTC),
        traceparent=None,
        payload={"channel": FAKE},
    )


async def jobs(admin: psycopg.AsyncConnection) -> list[tuple[str, str, str | None]]:
    cursor = await admin.execute(
        "SELECT key, status, parked_reason FROM fiscal.jobs WHERE kind = 'fiscal.route'"
    )
    return JOBS.validate_python(await cursor.fetchall())


def job(scope: Tenant | None, payload: dict[str, JsonValue]) -> Job:
    return Job(
        kind="fiscal.route",
        key="k",
        scope=scope,
        payload=payload,
        attempts=1,
        max_attempts=50,
        deadline_at=None,
        lease_token=new_id(),
        traceparent=None,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class Bench:
    """What a test here works with: the app, its client, the owner, its company, the channel, the database."""

    api: App
    client: httpx.AsyncClient
    owner: TestPrincipal
    company: str
    channel: FakeChannel
    admin: psycopg.AsyncConnection

    def parts(
        self,
    ) -> tuple[App, httpx.AsyncClient, TestPrincipal, str, FakeChannel, psycopg.AsyncConnection]:
        return self.api, self.client, self.owner, self.company, self.channel, self.admin


@pytest.fixture
def bench(
    api: App,
    client: httpx.AsyncClient,
    owned: tuple[TestPrincipal, str],
    fake_channel: FakeChannel,
    admin: psycopg.AsyncConnection,
) -> Bench:
    owner, company = owned
    return Bench(api=api, client=client, owner=owner, company=company, channel=fake_channel, admin=admin)


@pytest.fixture
def owned(owner: TestPrincipal, company: str) -> tuple[TestPrincipal, str]:
    return owner, company


async def test_the_recovery_path_routes_an_invoice_the_request_did_not_and_twice_numbers_it_once(
    bench: Bench,
) -> None:
    """The request's ``after_issue`` fails after the invoice committed (the channel crashes): the consumer of
    ``invoicing.invoice.issued`` numbers it, and a second delivery is a no-op (Task 12's note 12(b))."""
    api, client, owner, company, channel, admin = bench.parts()
    await enroll(client, owner, company, channel)
    crashes = [RuntimeError("crashed")]

    async def crash_once(draft: FiscalDraft) -> None:
        del draft
        if crashes:
            raise crashes.pop()

    channel.before_issue = crash_once
    invoice = invoice_of(await issued(client, owner, company))
    assert (channel.numbered, await mirror_updates(admin, invoice)) == (0, [("pending", 1)])
    (event,) = await published(admin, "invoicing.invoice.issued", invoice)
    recover = outside(api, "fiscal.commercial_documents")
    await recover(event)
    await recover(event)
    assert channel.numbered == 1
    assert await mirror_updates(admin, invoice) == [("pending", 1), ("pending", 2)]
    (_, (call, draft, budget), (again, _, _)) = channel.issued
    assert (call.reason, call.call_id, again.call_id, budget, draft.document) == (
        "fiscal.commercial_documents",
        str(event.id),
        str(event.id),
        None,
        invoice,
    )
    rows = await admin.execute("SELECT channel FROM fiscal.routings WHERE invoice_id = %s", (invoice,))
    assert await rows.fetchall() == [(FAKE,)]


async def test_an_invoice_voided_before_it_was_routed_is_never_numbered(
    bench: Bench,
) -> None:
    """Task 12's note 12(a): ``DOCUMENT_VOIDED`` from the pending status: number nothing, route nothing."""
    api, client, owner, company, channel, admin = bench.parts()
    response = await issued(client, owner, company)
    invoice = invoice_of(response)
    void = await client.post(
        f"/v1/invoices/{format_id('inv', invoice)}/void",
        json={},
        headers=headers(owner, company, "void-1") | {"If-Match": f'"{document(response)["version"]}"'},
    )
    assert void.status_code == 200, void.text
    await enroll(client, owner, company, channel)
    await admin.execute(BACKDATED)  # enrolled before the invoice was issued: the recovery may route it
    (event,) = await published(admin, "invoicing.invoice.issued", invoice)
    await outside(api, "fiscal.commercial_documents")(event)
    assert (channel.issued, await mirror_updates(admin, invoice)) == ([], [])
    rows = await admin.execute("SELECT count(*) FROM fiscal.routings")
    assert await rows.fetchall() == [(0,)]


async def test_the_mirror_keeps_the_newest_status_per_channel_whatever_order_updates_arrive_in(
    bench: Bench,
) -> None:
    """``fiscal.channel_documents`` is version-monotonic, so unordered: the synchronous path and the consumer
    both applying the channel's version 1 (the mirror's 2) apply it once; an older update changes nothing."""
    api, client, owner, company, channel, admin = bench.parts()
    await enroll(client, owner, company, channel)
    channel.slow = True
    invoice = invoice_of(await issued(client, owner, company))
    ((_, draft, _),) = channel.issued
    numbered = channel.documents[DocumentRef(invoice)]
    signed = channel.update(draft, version=2, signed=True)
    accepted = replace(
        signed, version=3, state="accepted", status=FiscalStatus.ACCEPTED, severity=Severity.INFO
    )
    mirror = outside(api, "fiscal.channel_documents")
    for update in (numbered, accepted, signed, accepted):
        await mirror(channel_event(draft.scope, update))
    assert await mirror_updates(admin, invoice) == [("pending", 1), ("pending", 2), ("accepted", 4)]
    (entry,) = await read_back(client, owner, company, invoice)
    assert (entry["status"], entry["version"], OBJECT.validate_python(entry["do"])) == (
        "accepted",
        4,
        {"encf": "E310000000001", "security_code": "Ab12Cd"},
    )


async def test_a_channel_with_no_number_parks_a_route_job_that_its_requirements_changing_readies(
    bench: Bench,
) -> None:
    """Task 18's review: the recovery path retries a channel's "no number now", woken when a range is
    registered (the channel's ``fiscal_channel.requirements.changed``), not by a backoff alone."""
    api, client, owner, company, channel, admin = bench.parts()
    await enroll(client, owner, company, channel)
    channel.no_number = True
    invoice = invoice_of(await issued(client, owner, company))
    (event,) = await published(admin, "invoicing.invoice.issued", invoice)
    await outside(api, "fiscal.commercial_documents")(event)
    key = f"{invoice}:{FAKE}"
    assert await jobs(admin) == [(key, "ready", None)]
    scope = channel.issued[0][1].scope
    payload: dict[str, JsonValue] = {"invoice": str(invoice), "channel": FAKE}
    route = route_job(api)
    assert await route(job(scope, payload)) == Park("range_missing")
    await admin.execute(
        "UPDATE fiscal.jobs SET status = 'parked', parked_reason = 'range_missing' WHERE key = %s", (key,)
    )
    async with api.bound("fiscal").unit(scope) as unit:
        await inside(api, "fiscal.requirements")(requirements_event(scope), unit)
    assert await jobs(admin) == [(key, "ready", None)]
    flags = await admin.execute("SELECT channel, review_due FROM fiscal.enrollments")
    assert FLAGS.validate_python(await flags.fetchall()) == [(FAKE, True)]
    channel.no_number = False
    assert await route(job(scope, payload)) == Done()
    assert channel.numbered == 1
    assert await mirror_updates(admin, invoice) == [("pending", 1), ("pending", 2)]


async def test_a_pending_enrollment_its_channel_marked_is_reviewed_by_the_next_invoice_and_routes_it(
    bench: Bench,
) -> None:
    """Decision 34: the mark, not a call inside the consumer's unit (rule 13); the next read reviews it."""
    api, client, owner, company, channel, admin = bench.parts()
    await enroll(client, owner, company, channel, active=False)
    scope = await enrolled_tenant(admin)
    async with api.bound("fiscal").unit(scope) as unit:
        await inside(api, "fiscal.requirements")(requirements_event(scope), unit)
    channel.due = NOTHING_DUE
    response = await issued(client, owner, company)
    (entry,) = FISCAL.validate_python(document(response)["fiscal"])
    assert (entry["status"], entry["version"]) == ("pending", 3)
    flags = await admin.execute("SELECT status, review_due FROM fiscal.enrollments")
    assert await flags.fetchall() == [("active", False)]


async def test_routing_again_skips_a_channel_no_longer_installed_and_a_credit_note_names_its_invoice(
    bench: Bench,
) -> None:
    api, client, owner, company, channel, admin = bench.parts()
    await enroll(client, owner, company, channel)
    response = await issued(client, owner, company)
    invoice = invoice_of(response)
    note = {"type": "credit_note", "corrects": document(response)["id"], "lines": [LINE]}
    credited = invoice_of(await issued(client, owner, company, "note-1", note))
    *_, (_, draft, _) = channel.issued
    ((_, invoice_draft, _), *_) = channel.issued
    assert [(reference.document, reference.issue_date) for reference in draft.references] == [
        (invoice, invoice_draft.issue_date)
    ]
    await admin.execute(
        "UPDATE fiscal.routings SET channel = 'do-gone-v1' WHERE invoice_id = %s", (credited,)
    )
    (event,) = await published(admin, "invoicing.invoice.issued", credited)
    calls = len(channel.issued)
    await outside(api, "fiscal.commercial_documents")(event)
    assert len(channel.issued) == calls + 1  # routed again to the installed channel only


async def test_a_purchase_or_an_enrollment_on_a_channel_no_longer_installed_routes_nothing(
    bench: Bench,
) -> None:
    """The fake channel covers sales only; an enrollment on a channel not installed here covers nothing."""
    api, client, owner, company, channel, admin = bench.parts()
    await enroll(client, owner, company, channel)
    supplier = {"legal_name": "SUPLIDOR SRL", "tax_id": {"scheme": "do.rnc", "value": "131098193"}}
    purchase = INVOICE | {"direction": "purchase", "buyer": supplier}
    response = await issued(client, owner, company, "purchase-1", purchase)
    assert FISCAL.validate_python(document(response)["fiscal"]) == []
    scope = await enrolled_tenant(admin)
    asked = Asked(
        enrollment=EnrollmentId(new_id()), tenant=scope, channel="do-gone-v1", environment="testecf"
    )
    gone = Enrollment.enroll(asked, requirements=NOTHING_DUE, now=datetime.now(UTC))
    async with api.bound("fiscal").unit(scope) as unit:
        assert await PgEnrollments(unit.connection, scope, Mode.TEST).add(gone) is None
    calls = len(channel.issued)
    response = await issued(client, owner, company, "invoice-2")
    assert len(FISCAL.validate_python(document(response)["fiscal"])) == 1
    assert len(channel.issued) == calls + 1


async def test_an_event_or_a_job_about_no_company_in_a_mode_is_a_defect_of_its_producer(api: App) -> None:
    """Dead at once (``INVALID``): retrying cannot mend an event or a job its producer got wrong."""
    scope = Tenant(OrganizationId(new_id()), CompanyId(new_id()), Mode.TEST)
    issued_event = RawEvent(
        id=new_id(),
        type="invoicing.invoice.issued",
        version=1,
        source="invoicing",
        scope=None,
        subject=new_id(),
        aggregate_version=1,
        occurred_at=datetime.now(UTC),
        traceparent=None,
        payload={},
    )
    recover = outside(api, "fiscal.commercial_documents")
    with pytest.raises(InvalidValueError, match=r"raw_event\.scope"):
        await recover(issued_event)
    with pytest.raises(InvalidValueError, match=r"raw_event\.subject"):
        await recover(replace(issued_event, scope=scope, subject=None))
    route = route_job(api)
    with pytest.raises(InvalidValueError, match=r"job\.scope"):
        await route(job(None, {"invoice": str(new_id()), "channel": FAKE}))
    with pytest.raises(InvalidValueError, match=r"job\.payload"):
        await route(job(scope, {"channel": FAKE}))


async def test_the_recovery_path_routes_no_invoice_issued_before_the_company_enrolled(
    bench: Bench,
) -> None:
    """Review I1: an invoice issued with ``fiscal: []`` stays commercial-only when the company enrolls later;
    the recovery path routes only through enrollments made by the time the invoice was issued."""
    api, client, owner, company, channel, admin = bench.parts()
    invoice = invoice_of(await issued(client, owner, company))  # no enrollment yet: fiscal: []
    await enroll(client, owner, company, channel)
    (event,) = await published(admin, "invoicing.invoice.issued", invoice)
    await outside(api, "fiscal.commercial_documents")(event)
    assert (channel.issued, await mirror_updates(admin, invoice)) == ([], [])


async def test_the_recovery_path_mirrors_pending_before_the_channel_numbers_what_the_request_never_routed(
    bench: Bench,
) -> None:
    """Task 12's note 12(a) on the recovery path: invoicing's mirror holds ``pending`` (version 1) before the
    channel is asked to number an invoice issued while the company was enrolled that the request never routed
    (here, an enrollment made before the invoice, whose request had nothing routed: the routing gone)."""
    api, client, owner, company, channel, admin = bench.parts()
    invoice = invoice_of(await issued(client, owner, company))
    await enroll(client, owner, company, channel)
    await admin.execute(BACKDATED)
    seen: list[tuple[str, int]] = []

    async def mirrored_first(draft: FiscalDraft) -> None:
        cursor = await admin.execute(
            "SELECT status, version FROM invoicing.fiscal_statuses WHERE invoice_id = %s", (draft.document,)
        )
        seen.extend(UPDATES.validate_python(await cursor.fetchall()))

    channel.before_issue = mirrored_first
    (event,) = await published(admin, "invoicing.invoice.issued", invoice)
    await outside(api, "fiscal.commercial_documents")(event)
    assert seen == [("pending", 1)]
    assert await mirror_updates(admin, invoice) == [("pending", 1), ("pending", 2)]


@pytest.mark.parametrize("left", ["pending", "disabled"])
@pytest.mark.parametrize("path", ["fiscal.route", "fiscal.commercial_documents"])
async def test_a_routing_kept_still_numbers_the_invoice_once_its_enrollment_left_active(
    bench: Bench, left: str, path: str
) -> None:
    """Review I2: the routing kept, not the enrollment's status now, names where a document goes: the
    parked job and the recovery path number it after its enrollment was disabled (or, by data, pending)."""
    api, client, owner, company, channel, admin = bench.parts()
    await enroll(client, owner, company, channel)
    channel.no_number = True
    invoice = invoice_of(await issued(client, owner, company))  # routed, pending, no number
    scope = channel.issued[0][1].scope
    if left == "disabled":
        enrollment = await (await admin.execute("SELECT id FROM fiscal.enrollments")).fetchone()
        assert enrollment is not None
        response = await client.post(
            f"/v1/fiscal/enrollments/{format_id('enr', UUIDS.validate_python(enrollment[0]))}/disable",
            json={"reason": "moving to another provider"},
            headers=headers(owner, company, "disable-1"),
        )
        assert response.status_code == 200, response.text
    else:
        await admin.execute("UPDATE fiscal.enrollments SET status = 'pending'")
    channel.no_number = False
    if path == "fiscal.route":
        payload: dict[str, JsonValue] = {"invoice": str(invoice), "channel": FAKE}
        assert await route_job(api)(job(scope, payload)) == Done()
    else:
        (event,) = await published(admin, "invoicing.invoice.issued", invoice)
        await outside(api, path)(event)
    assert channel.numbered == 1
    assert await mirror_updates(admin, invoice) == [("pending", 1), ("pending", 2)]


async def test_marking_a_channel_s_enrollments_for_review_leaves_a_disabled_one_alone(bench: Bench) -> None:
    """Review M6: a disabled enrollment is final; the mark is for those a review may change."""
    api, client, owner, company, channel, admin = bench.parts()
    await enroll(client, owner, company, channel)
    scope = await enrolled_tenant(admin)
    asked = Asked(enrollment=EnrollmentId(new_id()), tenant=scope, channel=FAKE, environment="certecf")
    gone = Enrollment.enroll(asked, requirements=NOTHING_DUE, now=datetime.now(UTC)).disabled(
        "moved", now=datetime.now(UTC)
    )
    async with api.bound("fiscal").unit(scope) as unit:
        assert await PgEnrollments(unit.connection, scope, Mode.TEST).add(gone) is None
    async with api.bound("fiscal").unit(scope) as unit:
        await inside(api, "fiscal.requirements")(requirements_event(scope), unit)
    flags = await admin.execute("SELECT status, review_due FROM fiscal.enrollments ORDER BY status")
    assert await flags.fetchall() == [("active", True), ("disabled", False)]


async def test_the_recovery_path_after_the_request_routed_and_numbered_the_invoice_changes_nothing(
    bench: Bench,
) -> None:
    """Task 12's note 12(b): keyed on (invoice, channel), the routing and the channel's own idempotency."""
    api, client, owner, company, channel, admin = bench.parts()
    await enroll(client, owner, company, channel)
    invoice = invoice_of(await issued(client, owner, company))
    before = await mirror_updates(admin, invoice)
    (event,) = await published(admin, "invoicing.invoice.issued", invoice)
    await outside(api, "fiscal.commercial_documents")(event)
    assert (channel.numbered, len(channel.issued)) == (1, 2)  # asked again, answered the same document
    assert await mirror_updates(admin, invoice) == before == [("pending", 1), ("pending", 3)]
    rows = await admin.execute("SELECT count(*) FROM fiscal.routings")
    assert await rows.fetchall() == [(1,)]
