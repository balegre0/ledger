from typing import Final, final, override
lazy from collections.abc import AsyncGenerator, Awaitable, Callable

import grpc
import pytest
from nats.aio.client import Client
from nats.aio.msg import Msg
from prometheus_client import CollectorRegistry
from structlog.testing import capture_logs

from ledger.events.identity.v1.events_pb2 import (
    ROLE_ADMIN,
    ROLE_OWNER,
    CompanyCreated,
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
from ledger_kernel.ids import CompanyId, OrganizationId, Tenant, new_id
from ledger_platform.authorization import CachedPermissions
from ledger_platform.grpc_client import MAX_DEADLINE, InvalidGrpcClientOptionError, RemoteUnavailableError
from ledger_platform.grpc_server import serve_grpc
from ledger_platform.identity_client import (
    GET_PERMISSIONS,
    MEMBERSHIP_SUBJECTS,
    MembershipChanges,
    RemotePermissions,
    known_permissions,
)
from ledger_testing.caches import Forgotten

ORGANIZATION: Final = OrganizationId(new_id())
COMPANY: Final = CompanyId(new_id())
ALICE: Final = Principal(subject=SubjectId("2001"), organization=ORGANIZATION, provider_organization="1001")
TENANT: Final = Tenant(ORGANIZATION, COMPANY)
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


async def test_a_cache_in_front_of_identity_asks_once_per_ttl(
    identity: tuple[FakeIdentity, grpc.aio.Channel],
) -> None:
    fake, channel = identity
    cache = CachedPermissions(RemotePermissions(channel))
    for _ in range(3):
        await cache.permissions(ALICE, COMPANY)
    assert len(fake.asked) == 1


# ── Membership changes ──────────────────────────────────────────────────────────


async def test_a_grant_makes_the_cache_forget_the_principal_in_the_company() -> None:
    cache = Forgotten()
    await MembershipChanges(cache).handle(message(granted()))
    assert cache.forgotten == [(ORGANIZATION, COMPANY, "2001")]


async def test_a_revocation_does_too() -> None:
    cache = Forgotten()
    event = MembershipRevoked(
        company_id=str(COMPANY), organization_id=str(ORGANIZATION), subject="2001", role=ROLE_OWNER, version=3
    )
    await MembershipChanges(cache).handle(
        message(Envelope.wrap(event, source="identity", tenant=TENANT), "identity.membership.revoked")
    )
    assert cache.forgotten == [(ORGANIZATION, COMPANY, "2001")]


async def test_another_event_on_the_subjects_changes_nothing() -> None:
    cache = Forgotten()
    other = Envelope.wrap(CompanyCreated(company_id=str(COMPANY)), source="identity", tenant=TENANT)
    await MembershipChanges(cache).handle(message(other))
    assert cache.forgotten == []


def unreadable(kind: str) -> Msg:
    match kind:
        case "no-envelope":
            return Msg(Client(), subject="identity.membership.granted", data=b"", headers=None)
        case "bad-organization":
            return message(granted(organization="not-an-id"))
        case "bad-subject":
            return message(granted(subject="not a subject"))
        case _:
            return Msg(
                Client(), subject="identity.membership.granted", data=b"\xff\xff", headers=granted().headers()
            )


@pytest.mark.parametrize("kind", ["no-envelope", "bad-organization", "bad-subject", "bad-payload"])
async def test_an_event_that_cannot_be_read_is_logged_and_skipped(kind: str) -> None:
    cache = Forgotten()
    with capture_logs() as logs:
        await MembershipChanges(cache).handle(unreadable(kind))
    assert cache.forgotten == []
    assert [log["event"] for log in logs] == ["authorization.membership_event_unreadable"]
    assert "not a subject" not in str(logs)


async def test_every_replica_following_the_subjects_hears_every_change(
    connect: Callable[[], Awaitable[Client]],
    eventually: Callable[[Callable[[], Awaitable[bool]]], Awaitable[None]],
) -> None:
    first, second = Forgotten(), Forgotten()
    one, other, publisher = await connect(), await connect(), await connect()
    await MembershipChanges(first).follow(one)
    await MembershipChanges(second).follow(other)
    await one.flush()
    await other.flush()
    envelope = granted()
    await publisher.publish("identity.membership.granted", envelope.data, headers=envelope.headers())

    async def both_heard() -> bool:
        return len(first.forgotten) == len(second.forgotten) == 1

    await eventually(both_heard)
    assert MEMBERSHIP_SUBJECTS == "identity.membership.>"
