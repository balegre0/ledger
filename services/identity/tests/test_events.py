import asyncio
from typing import Final, final
from uuid import UUID
lazy from collections.abc import AsyncGenerator, Callable

import pytest
from nats.aio.client import Client
from nats.js import JetStreamContext
from sqlalchemy import func, select
lazy import httpx
lazy from nats.aio.msg import Msg

import ledger.events.identity.v1.events_pb2 as wire
from ledger_contracts.envelope import Envelope
from ledger_identity.adapters.events import IDENTITY, SOURCE
from ledger_kernel.access import SubjectId
from ledger_kernel.ids import CompanyId
from ledger_platform.authentication import organization_of
from ledger_platform.identity_client import PermissionChanges
from ledger_platform.outbox import OutboxRecord
from ledger_testing.api import created
from ledger_testing.caches import Forgotten
from ledger_testing.waiting import eventually
lazy from ledger_identity.settings import IdentitySettings
lazy from ledger_platform.database import Database
lazy from ledger_platform.identity_client import Subscriber
lazy from ledger_platform.service import Service
lazy from ledger_testing.api import Caller

ORGANIZATION: Final = organization_of("1001")
TRACE_ID: Final = "4bf92f3577b34da6a3ce929d0e0e4736"
TRACEPARENT: Final = f"00-{TRACE_ID}-b7ad6b7169203331-01"
DUARTE: Final = {"tax_id": "101850043", "legal_name": "Duarte"}


@final
class Heard:
    """Every identity event a plain NATS subscriber receives, as envelopes."""

    def __init__(self) -> None:
        self.envelopes: list[Envelope] = []
        self.arrived = asyncio.Event()

    async def listen(self, subscriber: Subscriber) -> None:
        await subscriber.subscribe("identity.>", cb=self.keep)

    async def keep(self, message: Msg) -> None:
        self.envelopes.append(Envelope.from_headers(message.headers or {}, message.data))
        self.arrived.set()

    def about(self, company_id: str) -> list[Envelope]:
        return [
            envelope
            for envelope in self.envelopes
            if envelope.tenant is not None and str(envelope.tenant.company) == company_id
        ]


@pytest.fixture
async def nats(nats_url: str) -> AsyncGenerator[Client]:
    client = Client()
    await client.connect(nats_url)
    try:
        yield client
    finally:
        await client.close()


@pytest.fixture
async def heard(nats: Client) -> Heard:
    listener = Heard()
    await listener.listen(nats)
    await nats.flush()
    return listener


async def traced_company(client: httpx.AsyncClient, owner: Caller) -> str:
    """A company registered by a request on the trace ``TRACEPARENT`` names (the same key every time)."""
    headers = owner.posting("c-traced") | {"traceparent": TRACEPARENT}
    return await created(client, "/v1/companies", DUARTE, headers)


async def nothing_waits_in_the_outbox(database: Database) -> bool:
    async with database.transaction(None) as unit:
        pending = (await unit.connection.execute(select(func.count()).select_from(OutboxRecord))).scalar_one()
    return pending == 0


async def within(seconds: float, arrived: asyncio.Event, check: Callable[[], bool]) -> None:
    """Wait at most ``seconds`` for ``check`` to hold, looking again each time something ``arrived``."""
    async with asyncio.timeout(seconds):
        while True:
            arrived.clear()  # before looking: whatever arrives after the look wakes the wait
            if check():
                return
            await arrived.wait()


async def test_identity_declares_its_stream(service: Service[IdentitySettings], nats: Client) -> None:
    del service
    info = await JetStreamContext(nats).stream_info(IDENTITY.name)
    assert info.config.subjects == ["identity.>"]


async def test_a_new_company_and_its_owner_go_out_in_their_envelopes(
    client: httpx.AsyncClient, owner: Caller, heard: Heard
) -> None:
    company_id = await traced_company(client, owner)
    await within(10, heard.arrived, lambda: len(heard.about(company_id)) == 2)
    created_event, granted = heard.about(company_id)
    assert (created_event.source, created_event.type, granted.type) == (
        SOURCE,
        "ledger.events.identity.v1.CompanyCreated",
        "ledger.events.identity.v1.MembershipGranted",
    )
    assert created_event.tenant is not None
    assert created_event.tenant.organization == ORGANIZATION
    payload = created_event.unwrap(wire.CompanyCreated)
    assert (payload.company_id, payload.tax_id, payload.legal_name, payload.version) == (
        company_id,
        "101850043",
        "Duarte",
        1,
    )
    founder = granted.unwrap(wire.MembershipGranted)
    assert (founder.subject, founder.role, founder.previous_role, founder.version) == (
        "2001",
        wire.ROLE_OWNER,
        wire.ROLE_UNSPECIFIED,
        1,
    )


async def test_an_event_continues_the_trace_of_the_request_that_caused_it(
    client: httpx.AsyncClient, owner: Caller, heard: Heard
) -> None:
    company_id = await traced_company(client, owner)
    await within(10, heard.arrived, lambda: len(heard.about(company_id)) == 2)
    for envelope in heard.about(company_id):
        assert envelope.traceparent is not None
        assert envelope.traceparent.split("-")[1] == TRACE_ID


async def test_a_replayed_post_announces_nothing_more(
    client: httpx.AsyncClient, owner: Caller, heard: Heard, nats: Client, database: Database
) -> None:
    company_id = await traced_company(client, owner)
    await within(10, heard.arrived, lambda: len(heard.about(company_id)) == 2)
    stream = JetStreamContext(nats)
    before = (await stream.stream_info(IDENTITY.name)).state.last_seq
    assert (await traced_company(client, owner)) == company_id  # the same key and body: replayed
    # The replay's transaction committed before it answered: whatever it stored is in the outbox, and the
    # relay deletes a row only once JetStream has the event. An empty outbox leaves nothing more to come.
    await eventually(lambda: nothing_waits_in_the_outbox(database))
    assert (await stream.stream_info(IDENTITY.name)).state.last_seq == before
    assert len(heard.about(company_id)) == 2


async def test_every_replica_following_identity_hears_grants_and_revocations(
    client: httpx.AsyncClient, owner: Caller, company: str, nats: Client
) -> None:
    cache = Forgotten()
    await PermissionChanges(cache).follow(nats)
    await nats.flush()
    path = f"/v1/companies/{company}/memberships/3001"
    assert (await client.put(path, json={"role": "viewer"}, headers=owner.bearer())).status_code == 201
    assert (await client.delete(path, headers=owner.bearer())).status_code == 204
    expected = (ORGANIZATION, CompanyId(UUID(company)), SubjectId("3001"))
    await within(10, cache.arrived, lambda: cache.forgotten.count(expected) == 2)


async def test_every_replica_following_identity_hears_a_company_deactivated(
    client: httpx.AsyncClient, owner: Caller, company: str, nats: Client
) -> None:
    """Its members keep the reading permissions only: every answer kept about the company must go."""
    cache = Forgotten()
    await PermissionChanges(cache).follow(nats)
    await nats.flush()
    headers = owner.posting("off-1") | {"If-Match": "*"}
    assert (await client.post(f"/v1/companies/{company}/deactivate", headers=headers)).status_code == 200
    expected = (ORGANIZATION, CompanyId(UUID(company)))
    await within(10, cache.arrived, lambda: expected in cache.companies)
