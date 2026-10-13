import asyncio
from typing import Final, final, override
lazy from collections.abc import AsyncGenerator, Awaitable, Callable

import grpc
import pytest
from nats.aio.client import Client
from nats.aio.msg import Msg
from nats.errors import ConnectionClosedError
from prometheus_client import CollectorRegistry
from structlog.testing import capture_logs
lazy from nats.aio.subscription import Subscription

from ledger.events.identity.v1.events_pb2 import (
    ROLE_ADMIN,
    ROLE_OWNER,
    CompanyCreated,
    CompanyDeactivated,
    MembershipGranted,
    MembershipRevoked,
)
from ledger.identity.v1.identity_query_pb2 import (
    GetCompanyRequest,
    GetCompanyResponse,
    GetPermissionsRequest,
    GetPermissionsResponse,
)
from ledger.identity.v1.identity_query_pb2_grpc import (
    IdentityQueryServiceServicer,
    add_IdentityQueryServiceServicer_to_server,
)
from ledger_contracts.envelope import Envelope
from ledger_kernel.access import Permission, Principal, SubjectId
from ledger_kernel.errors import NotFoundError
from ledger_kernel.ids import CompanyId, OrganizationId, new_id
from ledger_kernel.tenancy import Tenant
from ledger_platform.auth.access import CachedPermissions
from ledger_platform.grpc.client import MAX_DEADLINE, InvalidGrpcClientOptionError, RemoteUnavailableError
from ledger_platform.grpc.server import serve_grpc
from ledger_platform.identity_client import (
    GET_PERMISSIONS,
    PERMISSION_SUBJECTS,
    PermissionChanges,
    RemotePermissions,
    Subscriber,
    follow_permission_changes,
    following,
    known_permissions,
)
from ledger_platform.interruptions import Interruptions
from ledger_testing.caches import Forgotten
from ledger_testing.clock import Moment

ORGANIZATION: Final = OrganizationId(new_id())
COMPANY: Final = CompanyId(new_id())
ALICE: Final = Principal(subject=SubjectId("2001"), organization=ORGANIZATION, provider_organization="1001")
TENANT: Final = Tenant(ORGANIZATION, COMPANY, None)
ORGANIZATION_ID: Final = str(ORGANIZATION)

type Context = grpc.aio.ServicerContext[GetPermissionsRequest, GetPermissionsResponse]
type CompanyContext = grpc.aio.ServicerContext[GetCompanyRequest, GetCompanyResponse]


@final
class FakeIdentity(IdentityQueryServiceServicer):
    """The identity service as far as the platform's client can tell."""

    def __init__(self) -> None:
        self.asked: list[GetPermissionsRequest] = []
        self.codes = ["company.read", "invoice.issue", "spaceship.fly"]

    @override
    async def GetCompany(self, request: GetCompanyRequest, context: CompanyContext) -> GetCompanyResponse:
        del request, context
        raise NotFoundError

    @override
    async def GetPermissions(
        self, request: GetPermissionsRequest, context: Context
    ) -> GetPermissionsResponse:
        del context
        self.asked.append(request)
        return GetPermissionsResponse(permissions=self.codes)


@pytest.fixture
async def identity() -> AsyncGenerator[tuple[FakeIdentity, grpc.aio.Channel]]:
    fake = FakeIdentity()

    def register(server: grpc.aio.Server) -> None:
        add_IdentityQueryServiceServicer_to_server(fake, server)

    async with (
        serve_grpc(port=0, register=register, registry=CollectorRegistry()) as port,
        grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel,
    ):
        yield fake, channel


def message(envelope: Envelope, subject: str = "identity.membership.granted") -> Msg:
    return Msg(Client(), subject=subject, data=envelope.data, headers=envelope.headers())


def granted(organization: str = ORGANIZATION_ID, subject: str = "2001") -> Envelope:
    event = MembershipGranted(
        company_id=str(COMPANY), organization_id=organization, subject=subject, role=ROLE_ADMIN, version=2
    )
    return Envelope.wrap(event, source="identity", tenant=TENANT)


# ── Remote permissions ──────────────────────────────────────────────────────────


async def test_permissions_are_asked_of_identity_for_the_callers_organization(
    identity: tuple[FakeIdentity, grpc.aio.Channel],
) -> None:
    fake, channel = identity
    answer = await RemotePermissions(channel).permissions(ALICE, COMPANY)
    assert answer == frozenset({Permission.COMPANY_READ, Permission.INVOICE_ISSUE})
    (request,) = fake.asked
    assert (request.organization_id, request.company_id, request.subject) == (
        str(ORGANIZATION),
        str(COMPANY),
        "2001",
    )


def test_codes_this_version_does_not_know_are_ignored() -> None:
    assert known_permissions(["company.read", "spaceship.fly", ""]) == frozenset({Permission.COMPANY_READ})
    assert GET_PERMISSIONS == "/ledger.identity.v1.IdentityQueryService/GetPermissions"


@pytest.mark.parametrize("deadline", [0.0, MAX_DEADLINE + 1])
async def test_a_deadline_that_cannot_work_is_refused_when_the_client_is_made(deadline: float) -> None:
    async with grpc.aio.insecure_channel("127.0.0.1:1") as channel:
        with pytest.raises(InvalidGrpcClientOptionError, match="gRPC client option deadline must be"):
            RemotePermissions(channel, deadline=deadline)


async def test_an_identity_that_is_not_there_makes_permissions_unavailable() -> None:
    async with grpc.aio.insecure_channel("127.0.0.1:1") as channel:
        with pytest.raises(RemoteUnavailableError):
            await RemotePermissions(channel, deadline=1.0).permissions(ALICE, COMPANY)


async def test_a_cache_in_front_of_identity_asks_it_once_per_ttl(
    identity: tuple[FakeIdentity, grpc.aio.Channel],
) -> None:
    fake, channel = identity
    moment = Moment()
    cache = CachedPermissions(RemotePermissions(channel), ttl=30.0, clock=moment)
    for now in (0.0, 10.0, 29.9):
        moment.now = now
        await cache.permissions(ALICE, COMPANY)
    assert len(fake.asked) == 1
    moment.now = 30.0
    await cache.permissions(ALICE, COMPANY)
    assert len(fake.asked) == 2


# ── Membership changes ──────────────────────────────────────────────────────────


async def test_a_grant_makes_the_cache_forget_the_principal_in_the_company() -> None:
    cache = Forgotten()
    await PermissionChanges(cache).handle(message(granted()))
    assert cache.forgotten == [(ORGANIZATION, COMPANY, "2001")]


async def test_a_revocation_does_too() -> None:
    cache = Forgotten()
    event = MembershipRevoked(
        company_id=str(COMPANY), organization_id=str(ORGANIZATION), subject="2001", role=ROLE_OWNER, version=3
    )
    await PermissionChanges(cache).handle(
        message(Envelope.wrap(event, source="identity", tenant=TENANT), "identity.membership.revoked")
    )
    assert cache.forgotten == [(ORGANIZATION, COMPANY, "2001")]


async def test_another_event_on_the_subjects_changes_nothing() -> None:
    cache = Forgotten()
    other = Envelope.wrap(CompanyCreated(company_id=str(COMPANY)), source="identity", tenant=TENANT)
    await PermissionChanges(cache).handle(message(other))
    assert cache.forgotten == []


def deactivated(organization: str = ORGANIZATION_ID) -> Envelope:
    event = CompanyDeactivated(company_id=str(COMPANY), organization_id=organization, version=4)
    return Envelope.wrap(event, source="identity", tenant=TENANT)


async def test_a_deactivation_makes_the_cache_forget_the_company() -> None:
    cache = Forgotten()
    await PermissionChanges(cache).handle(message(deactivated(), "identity.company.deactivated"))
    assert (cache.forgotten, cache.companies) == ([], [(ORGANIZATION, COMPANY)])


def unreadable(kind: str) -> Msg:
    match kind:
        case "no-envelope":
            return Msg(Client(), subject="identity.membership.granted", data=b"", headers=None)
        case "bad-organization":
            return message(granted(organization="not-an-id"))
        case "bad-subject":
            return message(granted(subject="not a subject"))
        case "bad-company":
            return message(deactivated(organization="not-an-id"), "identity.company.deactivated")
        case _:
            return Msg(
                Client(), subject="identity.membership.granted", data=b"\xff\xff", headers=granted().headers()
            )


@pytest.mark.parametrize(
    "kind", ["no-envelope", "bad-organization", "bad-subject", "bad-company", "bad-payload"]
)
async def test_an_event_that_cannot_be_read_is_logged_and_skipped(kind: str) -> None:
    cache = Forgotten()
    with capture_logs() as logs:
        await PermissionChanges(cache).handle(unreadable(kind))
    assert (cache.forgotten, cache.companies) == ([], [])
    assert [log["event"] for log in logs] == ["authorization.identity_event_unreadable"]
    assert "not a subject" not in str(logs)


async def test_every_replica_following_the_subjects_hears_every_change(
    connect: Callable[[], Awaitable[Client]],
    eventually: Callable[[Callable[[], Awaitable[bool]]], Awaitable[None]],
) -> None:
    first, second = Forgotten(), Forgotten()
    one, other, publisher = await connect(), await connect(), await connect()
    await PermissionChanges(first).follow(one)
    await PermissionChanges(second).follow(other)
    await one.flush()
    await other.flush()
    envelope = granted()
    await publisher.publish("identity.membership.granted", envelope.data, headers=envelope.headers())

    async def both_heard() -> bool:
        return len(first.forgotten) == len(second.forgotten) == 1

    await eventually(both_heard)
    assert PERMISSION_SUBJECTS == ("identity.membership.>", "identity.company.deactivated")


async def publish(
    publisher: Client, envelope: Envelope, subject: str = "identity.membership.granted"
) -> None:
    await publisher.publish(subject, envelope.data, headers=envelope.headers())


async def test_an_unreadable_event_on_the_wire_does_not_stop_the_next_one(
    connect: Callable[[], Awaitable[Client]],
    eventually: Callable[[Callable[[], Awaitable[bool]]], Awaitable[None]],
) -> None:
    cache = Forgotten()
    follower, publisher = await connect(), await connect()
    with capture_logs() as logs:
        async with following(follower, cache, Interruptions()):
            await follower.flush()
            await publisher.publish("identity.membership.granted", b"\xff", headers={"ce-type": "garbage"})
            await publish(publisher, granted())

            async def heard() -> bool:
                return len(cache.forgotten) == 1

            await eventually(heard)
    assert cache.forgotten == [(ORGANIZATION, COMPANY, "2001")]
    assert [log["event"] for log in logs] == ["authorization.identity_event_unreadable"]


async def test_a_follower_hears_a_deactivation_on_the_wire(
    connect: Callable[[], Awaitable[Client]],
    eventually: Callable[[Callable[[], Awaitable[bool]]], Awaitable[None]],
) -> None:
    cache = Forgotten()
    follower, publisher = await connect(), await connect()
    event = deactivated()
    async with following(follower, cache, Interruptions()):
        await follower.flush()

        async def heard() -> bool:
            """Core NATS delivers at most once: the event goes out again until it is heard (forgetting twice
            is harmless), so the test checks what the follower does with it, not the transport's luck."""
            if not cache.companies:
                await publish(publisher, event, "identity.company.deactivated")
                await publisher.flush()
            return bool(cache.companies)

        await eventually(heard)
    assert set(cache.companies) == {(ORGANIZATION, COMPANY)}


@final
class SubscribesOnce(Subscriber):
    """A connection whose second subscription fails, as when NATS goes away between the two."""

    def __init__(self, connection: Subscriber) -> None:
        self.connection = connection
        self.subscribed = 0

    @override
    async def subscribe(self, subject: str, *, cb: Callable[[Msg], Awaitable[None]]) -> Subscription:
        if self.subscribed:
            raise ConnectionClosedError
        self.subscribed += 1
        return await self.connection.subscribe(subject, cb=cb)


async def test_a_follow_that_fails_halfway_leaves_no_subscription_behind(
    connect: Callable[[], Awaitable[Client]],
    eventually: Callable[[Callable[[], Awaitable[bool]]], Awaitable[None]],
) -> None:
    """The first subject's subscription is ended: a retried follow would otherwise hear each event twice."""
    cache, probe = Forgotten(), Forgotten()
    follower, prober, publisher = await connect(), await connect(), await connect()
    with pytest.raises(ConnectionClosedError):
        await PermissionChanges(cache).follow(SubscribesOnce(follower))
    await PermissionChanges(probe).follow(prober)
    await prober.flush()
    await publish(publisher, granted())

    async def probe_heard() -> bool:
        return len(probe.forgotten) == 1

    await eventually(probe_heard)
    await follower.flush()
    await asyncio.sleep(0.01)
    assert cache.forgotten == []


async def test_following_forgets_everything_kept_while_nobody_followed(
    connect: Callable[[], Awaitable[Client]],
) -> None:
    cache = Forgotten()
    async with following(await connect(), cache, Interruptions()):
        assert cache.cleared == 1


async def test_an_interruption_while_following_forgets_everything_and_none_after_it(
    connect: Callable[[], Awaitable[Client]],
) -> None:
    cache, interruptions = Forgotten(), Interruptions()
    async with following(await connect(), cache, interruptions):
        interruptions.interrupted()
        interruptions.interrupted()
        assert cache.cleared == 3
    interruptions.interrupted()
    assert cache.cleared == 3


async def test_following_ends_with_its_subscription(
    connect: Callable[[], Awaitable[Client]],
    eventually: Callable[[Callable[[], Awaitable[bool]]], Awaitable[None]],
) -> None:
    """After the follower left, it hears nothing of a grant that another follower hears."""
    cache, probe = Forgotten(), Forgotten()
    follower, prober, publisher = await connect(), await connect(), await connect()
    async with following(follower, cache, Interruptions()):
        pass
    _ = await PermissionChanges(probe).follow(prober)
    await prober.flush()
    await publish(publisher, granted())

    async def probe_heard() -> bool:
        return len(probe.forgotten) == 1

    await eventually(probe_heard)
    # The server queued whatever it sent the follower before the answer to this ping, and the client hands a
    # message to its subscription before it reads on.
    await follower.flush()
    await asyncio.sleep(0.01)
    assert cache.forgotten == []


async def test_leaving_after_the_connection_closed_is_quiet(connect: Callable[[], Awaitable[Client]]) -> None:
    """A closed connection ended its subscriptions: there is nothing left to unsubscribe."""
    connection = await connect()
    async with following(connection, Forgotten(), Interruptions()):
        await connection.close()


async def test_leaving_while_the_connection_drains_is_quiet(connect: Callable[[], Awaitable[Client]]) -> None:
    """A draining connection ends its subscriptions itself."""
    connection = await connect()
    async with following(connection, Forgotten(), Interruptions()):
        draining = asyncio.create_task(connection.drain())
        await asyncio.sleep(0)  # the drain starts draining the subscriptions,
        await asyncio.sleep(0)  # then says so
        assert connection.is_draining
    await draining


async def test_a_follower_worker_follows_until_it_is_stopped(
    connect: Callable[[], Awaitable[Client]],
    eventually: Callable[[Callable[[], Awaitable[bool]]], Awaitable[None]],
) -> None:
    cache, interruptions = Forgotten(), Interruptions()
    follower, publisher = await connect(), await connect()
    worker = asyncio.create_task(follow_permission_changes(follower, cache, interruptions))

    async def following_now() -> bool:
        return cache.cleared == 1

    await eventually(following_now)
    await follower.flush()
    await publish(publisher, granted())

    async def heard() -> bool:
        return len(cache.forgotten) == 1

    await eventually(heard)
    worker.cancel()
    with pytest.raises(asyncio.CancelledError):
        await worker
    interruptions.interrupted()
    assert cache.cleared == 1
