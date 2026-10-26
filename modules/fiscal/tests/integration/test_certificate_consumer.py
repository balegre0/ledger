"""``fiscal.certificates`` (§3.2, §8.6.4): a certificate installed moves what its company's channels parked.

The handler is the ``api`` app's own (``App.members``), called with the signer's ``CertificateInstalled`` as
the outbox holds it, as the worker's runner would call it. For each mode the event names it marks the
company's enrollments in that mode for review (their requirements include a certificate) and asks every
channel the company is enrolled on in it, disabled enrollments' too (their routed documents still advance),
to resume what waited for a certificate; a channel no longer installed is skipped. It is idempotent: a
redelivery marks again and wakes nothing more. Another company's enrollments are never touched (tenancy).
The real channel's parked job woken and finished is DO's suite's
(``test_a_certificate_installed_wakes_through_fiscal_the_document_waiting_for_it_and_it_finishes``).
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final, Protocol
from uuid import UUID

import pytest
from pydantic import TypeAdapter
lazy import httpx
lazy import psycopg

from ledger_fiscal_kit.channels import Requirements, ResumeReason
from ledger_kernel.errors import InvalidValueError
from ledger_kernel.ids import COMPANY_PREFIX, CompanyId, OrganizationId, new_id, parse_id
from ledger_kernel.tenancy import Mode, Tenant
from ledger_platform.data.codec import payload_of
from ledger_platform.events.consumers import Outside, RawEvent
from ledger_signer.events import CertificateInstalled
from ledger_testing.api import created, document
lazy from ledger_platform.data.codec import JsonValue
lazy from ledger_platform.events.consumers import OutsideHandler
lazy from ledger_platform.runtime.app import App
lazy from ledger_platform.runtime.calls import SystemCall
lazy from ledger_testing.api import TestPrincipal

FAKE: Final = "do-fake-v1"
PATH: Final = "/v1/fiscal/enrollments"
CERTIFICATES: Final = "fiscal.certificates"
NOTHING_DUE: Final = Requirements(currently_due=())
UUIDS: Final = TypeAdapter(UUID)
MARKS: Final = TypeAdapter(list[tuple[str, str, bool]])


class FakeChannel(Protocol):
    """The conftest's channel, as these tests use it (see the conftest)."""

    due: Requirements
    resumed: list[tuple[SystemCall, CompanyId, ResumeReason]]


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


def posting(owner: TestPrincipal, company: str, key: str, mode: str = "test") -> dict[str, str]:
    return owner.posting(key, mode) | {"Ledger-Company": company}


async def enrolled(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str, environment: str = "testecf"
) -> str:
    """``company`` enrolled on the fake channel in ``environment``; the enrollment's public id."""
    mode = "live" if environment == "ecf" else "test"
    body = {"channel": FAKE, "environment": environment}
    response = await client.post(PATH, json=body, headers=posting(owner, company, f"e-{environment}", mode))
    assert response.status_code == 201, response.text
    return str(document(response)["id"])


async def company_of(admin: psycopg.AsyncConnection, public: str) -> Tenant:
    """The company of ``public`` (its id through identity's API), without a mode: the signer's scope."""
    company = parse_id(COMPANY_PREFIX, public)
    found = await admin.execute("SELECT organization_id FROM identity.companies WHERE id = %s", (company,))
    row = await found.fetchone()
    assert row is not None
    return Tenant(OrganizationId(UUIDS.validate_python(row[0])), CompanyId(company), None)


def installed(scope: Tenant, *modes: Mode) -> RawEvent:
    """``signer.certificate.installed`` for ``modes``, as the signer's outbox would hold it."""
    certificate = new_id()
    event = CertificateInstalled(
        scope=scope,
        subject=certificate,
        aggregate_version=1,
        certificate=certificate,
        modes=modes,
        backend="local",
        not_after=datetime(2028, 1, 1, tzinfo=UTC),
        replaces=(),
    )
    return raw(event.TYPE, scope, payload_of(event))


def raw(kind: str, scope: Tenant | None, payload: dict[str, JsonValue], version: int = 1) -> RawEvent:
    return RawEvent(
        id=new_id(),
        type=kind,
        version=version,
        source="signer",
        scope=scope,
        subject=new_id(),
        aggregate_version=1,
        occurred_at=datetime.now(UTC),
        traceparent=None,
        payload=payload,
    )


def consumer(api: App) -> OutsideHandler:
    (member,) = [member for member in api.members if member.name == "fiscal"]
    (spec,) = [each for each in member.consumers if each.name == CERTIFICATES]
    handler = spec.handler
    assert isinstance(handler, Outside)
    return handler.handler


async def marks(admin: psycopg.AsyncConnection) -> list[tuple[str, str, bool]]:
    """``(environment, status, review_due)`` of every enrollment, by environment then status."""
    found = await admin.execute(
        "SELECT environment, status, review_due FROM fiscal.enrollments ORDER BY environment, status"
    )
    return MARKS.validate_python(await found.fetchall())


def resumed(channel: FakeChannel) -> list[tuple[str, Tenant | None, str, UUID, ResumeReason]]:
    """``(reason, scope, call id, company, why)`` of every ``resume`` the channel was asked."""
    found: list[tuple[str, Tenant | None, str, UUID, ResumeReason]] = []
    for call, company, why in channel.resumed:
        scope = call.scope
        assert scope is None or isinstance(scope, Tenant)
        found.append((call.reason, scope, call.call_id, company, why))
    return found


def in_mode(company: Tenant, mode: Mode) -> Tenant:
    return Tenant(company.organization, company.company, mode)


async def test_a_certificate_installed_resumes_the_company_s_channels_and_marks_its_enrollments(
    bench: Bench,
) -> None:
    """Each mode the event names: its enrollments marked ``review_due``, each channel resumed once in it."""
    api, client, owner, company, fake_channel, admin = bench.parts()
    await enrolled(client, owner, company)
    await enrolled(client, owner, company, "ecf")
    scope = await company_of(admin, company)
    event = installed(scope, Mode.TEST, Mode.LIVE)
    await consumer(api)(event)
    assert await marks(admin) == [("ecf", "pending", True), ("testecf", "pending", True)]
    why = ResumeReason.CERTIFICATE_INSTALLED
    assert resumed(fake_channel) == [
        (CERTIFICATES, in_mode(scope, Mode.TEST), str(event.id), scope.company, why),
        (CERTIFICATES, in_mode(scope, Mode.LIVE), str(event.id), scope.company, why),
    ]
    await consumer(api)(event)  # redelivered: the same marks, the same wake (nothing more is parked)
    assert await marks(admin) == [("ecf", "pending", True), ("testecf", "pending", True)]
    assert len(fake_channel.resumed) == 4


async def test_only_the_modes_the_certificate_serves_are_resumed_and_the_next_read_reviews_the_mark(
    bench: Bench,
) -> None:
    api, client, owner, company, fake_channel, admin = bench.parts()
    enrollment = await enrolled(client, owner, company)
    await enrolled(client, owner, company, "ecf")
    scope = await company_of(admin, company)
    await consumer(api)(installed(scope, Mode.TEST))
    assert await marks(admin) == [("ecf", "pending", False), ("testecf", "pending", True)]
    assert [each[1] for each in resumed(fake_channel)] == [in_mode(scope, Mode.TEST)]
    fake_channel.due = NOTHING_DUE  # the certificate was what it lacked
    headers = owner.bearer() | {"Ledger-Company": company, "Ledger-Mode": "test"}
    reviewed = await client.get(f"{PATH}/{enrollment}", headers=headers)
    assert document(reviewed)["status"] == "active"
    assert await marks(admin) == [("ecf", "pending", False), ("testecf", "active", False)]


async def test_a_disabled_enrollment_s_channel_is_resumed_unmarked_and_one_not_installed_is_skipped(
    bench: Bench,
) -> None:
    """A routing kept is honoured once its enrollment left active (Task 20's I2): its documents advance."""
    api, client, owner, company, fake_channel, admin = bench.parts()
    enrollment = await enrolled(client, owner, company)
    disabled = await client.post(
        f"{PATH}/{enrollment}/disable", json={"reason": "Moved"}, headers=posting(owner, company, "d-1")
    )
    assert disabled.status_code == 200, disabled.text
    scope = await company_of(admin, company)
    await consumer(api)(installed(scope, Mode.TEST))
    assert (await marks(admin), len(fake_channel.resumed)) == ([("testecf", "disabled", False)], 1)
    await enrolled(client, owner, company, "certecf")
    await consumer(api)(installed(scope, Mode.TEST))
    assert await marks(admin) == [("certecf", "pending", True), ("testecf", "disabled", False)]
    assert len(fake_channel.resumed) == 2  # one channel, however many enrollments on it
    await admin.execute("UPDATE fiscal.enrollments SET channel = 'do-gone-v1'")
    await consumer(api)(installed(scope, Mode.TEST))
    assert len(fake_channel.resumed) == 2


async def test_another_company_s_enrollments_are_never_touched(bench: Bench) -> None:
    api, client, owner, company, fake_channel, admin = bench.parts()
    await enrolled(client, owner, company)
    registration = {
        "jurisdiction": {"country": "DO"},
        "functional_currency": "DOP",
        "tax_ids": [{"scheme": "do.rnc", "value": "131098193"}],
        "legal_name": "Otra SRL",
    }
    other = await created(client, "/v1/companies", registration, owner.posting("company-2"))
    await consumer(api)(installed(await company_of(admin, other), Mode.TEST, Mode.LIVE))
    assert (await marks(admin), fake_channel.resumed) == ([("testecf", "pending", False)], [])


async def test_an_event_that_is_not_a_certificate_installed_for_a_company_is_a_defect(
    api: App, company: str, admin: psycopg.AsyncConnection
) -> None:
    scope = await company_of(admin, company)
    good = installed(scope, Mode.TEST)
    handle = consumer(api)
    defects: list[tuple[RawEvent, str]] = [
        (raw(good.type, in_mode(scope, Mode.TEST), dict(good.payload)), r"raw_event\.scope"),
        (raw(good.type, None, dict(good.payload)), r"raw_event\.scope"),
        (raw("signer.certificate.retired", scope, dict(good.payload)), r"raw_event\.type"),
        (raw(good.type, scope, dict(good.payload), version=2), r"raw_event\.type"),
        (raw(good.type, scope, {**good.payload, "modes": []}), r"raw_event\.payload"),
        (raw(good.type, scope, {**good.payload, "modes": ["both"]}), r"raw_event\.payload"),
        (raw(good.type, scope, {}), r"raw_event\.payload"),
    ]
    for event, field in defects:
        with pytest.raises(InvalidValueError, match=field):
            await handle(event)
