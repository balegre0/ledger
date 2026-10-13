"""The platform's client of the identity service: permissions for the authorization context, kept fresh.

``RemotePermissions`` asks ``IdentityQueryService.GetPermissions``, with a deadline on every call. A service
puts it behind a ``CachedPermissions``, and ``PermissionChanges`` drops what identity announces has changed
what other services may do: a membership granted or revoked (``identity.membership.*``) drops the member's
answer, a company deactivated (``identity.company.deactivated``, which leaves its members the reading
permissions only) every answer about the company. The rule for identity's events: every event that changes
what other services may do must be followed here and drop what it changed. Every replica follows the events
itself, on core NATS subscriptions: a durable consumer would hand each event to one replica only. Following is
best effort; the cache's TTL bounds what a lost event costs.

Events published while the connection to NATS is down never reach a core subscription, so the cache forgets
everything when the connection is lost and again when it is back (``Interruptions``), and once when following
starts: what it kept before may have changed unannounced.

A service wires it so (the first such service comes in milestone 3)::

    @asynccontextmanager
    async def resources(settings: Settings) -> AsyncGenerator[Resources]:
        async with grpc.aio.insecure_channel(settings.identity_address) as channel:
            yield Resources(permissions=CachedPermissions(RemotePermissions(channel)))

    def workers(runtime: Runtime[Settings]) -> Mapping[str, Work]:
        cache = runtime.resources(Resources).permissions
        follow = partial(follow_permission_changes, runtime.nats, cache, runtime.interruptions)
        return {"permission changes": follow}

    Service(..., interfaces=Interfaces(resources=resources, ...), events=Events(workers=workers))

The channel is a resource, closed in order at the stop. The follower is a worker, not a resource: the
resources hook is handed the settings only, before there is a runtime with its NATS connection. As a worker
it is supervised (a failure to subscribe is tried again) and stopped before NATS drains, leaving with its
subscriptions (``following``).
"""

import asyncio
from abc import ABCMeta, abstractmethod
from contextlib import asynccontextmanager, suppress
from typing import Final, Never, Protocol, final, override
lazy from collections.abc import AsyncGenerator, Awaitable, Callable, Iterable, Mapping

import structlog
from nats.errors import ConnectionClosedError, ConnectionDrainingError
lazy import grpc
lazy from nats.aio.msg import Msg
lazy from nats.aio.subscription import Subscription

from ledger.events.identity.v1.events_pb2 import CompanyDeactivated, MembershipGranted, MembershipRevoked
from ledger.identity.v1.identity_query_pb2 import GetPermissionsRequest
from ledger.identity.v1.identity_query_pb2_grpc import IdentityQueryServiceStub
from ledger_contracts.envelope import Envelope
from ledger_kernel.access import Permission, parse_subject
from ledger_kernel.errors import InvalidInputError
from ledger_kernel.ids import CompanyId, OrganizationId, parse_id
from ledger_platform.authorization import PermissionSource
from ledger_platform.grpc_client import DEFAULT_DEADLINE, call_unary, require_deadline
lazy from ledger_kernel.access import Principal, SubjectId
lazy from ledger_platform.interruptions import Interruptions

GET_PERMISSIONS: Final = "/ledger.identity.v1.IdentityQueryService/GetPermissions"
PERMISSION_SUBJECTS: Final = ("identity.membership.>", "identity.company.deactivated")
"""The subjects of the events that change what other services may do (see the module)."""
_GRANTED: Final = MembershipGranted.DESCRIPTOR.full_name
_REVOKED: Final = MembershipRevoked.DESCRIPTOR.full_name
_DEACTIVATED: Final = CompanyDeactivated.DESCRIPTOR.full_name
_PERMISSIONS: Final[Mapping[str, Permission]] = {permission.value: permission for permission in Permission}
_logger = structlog.stdlib.get_logger(__name__)


def known_permissions(codes: Iterable[str]) -> frozenset[Permission]:
    """The permissions among ``codes`` that this version knows; a newer identity may grant others."""
    return frozenset(_PERMISSIONS[code] for code in codes if code in _PERMISSIONS)


@final
class RemotePermissions(PermissionSource):
    """Permissions as the identity service answers them, over gRPC, each call within ``deadline`` seconds.

    A ``deadline`` that cannot work is an ``InvalidGrpcClientOptionError`` at once, not on the first request.
    """

    __slots__ = ("_deadline", "_get_permissions")

    def __init__(self, channel: grpc.aio.Channel, *, deadline: float = DEFAULT_DEADLINE) -> None:
        require_deadline(deadline)
        self._get_permissions = IdentityQueryServiceStub(channel).GetPermissions
        self._deadline = deadline

    @override
    async def permissions(self, principal: Principal, company: CompanyId) -> frozenset[Permission]:
        request = GetPermissionsRequest(
            organization_id=str(principal.organization), company_id=str(company), subject=principal.subject
        )
        response = await call_unary(
            self._get_permissions, request, rpc=GET_PERMISSIONS, deadline=self._deadline
        )
        return known_permissions(response.permissions)


class PermissionCache(Protocol, metaclass=ABCMeta):
    """What ``PermissionChanges`` tells: a ``CachedPermissions`` is one."""

    @abstractmethod
    def forget(self, organization: OrganizationId, company: CompanyId, subject: SubjectId) -> None:
        """Drop what is kept about ``subject`` in ``company``."""

    @abstractmethod
    def forget_company(self, organization: OrganizationId, company: CompanyId) -> None:
        """Drop what is kept about anyone in ``company``."""

    @abstractmethod
    def forget_all(self) -> None:
        """Drop everything kept: changes may have gone unannounced."""


class Subscriber(Protocol, metaclass=ABCMeta):
    """What ``PermissionChanges`` asks of a NATS connection: a ``nats.aio.client.Client`` is one.

    nats-py leaves the type of ``subscribe``'s ``future`` open; seen through this protocol, only the ones
    used here remain.
    """

    @abstractmethod
    async def subscribe(self, subject: str, *, cb: Callable[[Msg], Awaitable[None]]) -> Subscription:
        """Deliver every message on ``subject`` to ``cb``."""


def _member(organization: str, company: str, subject: str) -> tuple[OrganizationId, CompanyId, SubjectId]:
    return OrganizationId(parse_id(organization)), CompanyId(parse_id(company)), parse_subject(subject)


def _company(organization: str, company: str) -> tuple[OrganizationId, CompanyId]:
    return OrganizationId(parse_id(organization)), CompanyId(parse_id(company))


@final
class PermissionChanges:
    """Keeps a permission cache honest: drops what identity announces has changed (see the module)."""

    __slots__ = ("_cache",)

    def __init__(self, cache: PermissionCache) -> None:
        self._cache = cache

    async def follow(self, nats: Subscriber) -> tuple[Subscription, ...]:
        """Follow every such event on this replica; unsubscribe each, or drain the connection, to stop.

        All of the subjects or none: when one subscription fails, those already made are ended before the
        error goes on, so that a follow tried again does not hear an event twice.
        """
        made: list[Subscription] = []
        try:
            for subject in PERMISSION_SUBJECTS:
                subscription = await nats.subscribe(subject, cb=self.handle)
                made.append(subscription)
        except BaseException:
            await _unsubscribe(made)
            raise
        return tuple(made)

    async def handle(self, message: Msg) -> None:
        """Forget what an event changed; one that cannot be read is logged and skipped, any other ignored."""
        try:
            self._forget(Envelope.from_headers(message.headers or {}, message.data))
        except InvalidInputError as error:
            _logger.warning(
                "authorization.identity_event_unreadable", subject=message.subject, code=error.code
            )

    def _forget(self, envelope: Envelope) -> None:
        if envelope.type == _GRANTED:
            grant = envelope.unwrap(MembershipGranted)
            self._cache.forget(*_member(grant.organization_id, grant.company_id, grant.subject))
        elif envelope.type == _REVOKED:
            revoked = envelope.unwrap(MembershipRevoked)
            self._cache.forget(*_member(revoked.organization_id, revoked.company_id, revoked.subject))
        elif envelope.type == _DEACTIVATED:
            deactivated = envelope.unwrap(CompanyDeactivated)
            self._cache.forget_company(*_company(deactivated.organization_id, deactivated.company_id))


async def _unsubscribe(subscriptions: Iterable[Subscription]) -> None:
    """End each of ``subscriptions``; a closed or draining connection ended them itself."""
    for subscription in subscriptions:
        with suppress(ConnectionClosedError, ConnectionDrainingError):
            await subscription.unsubscribe()


@asynccontextmanager
async def following(
    nats: Subscriber, cache: PermissionCache, interruptions: Interruptions
) -> AsyncGenerator[None]:
    """Keep ``cache`` honest while inside: follow identity's events, forget everything on interruptions.

    Entering subscribes and forgets everything kept so far; every interruption of the connection to NATS
    forgets everything again. Leaving unsubscribes and stops listening to the interruptions.
    """
    stop_listening = interruptions.listen(cache.forget_all)
    try:
        subscriptions = await PermissionChanges(cache).follow(nats)
        cache.forget_all()  # kept while nobody followed: changes may have gone unannounced
        try:
            yield
        finally:
            await _unsubscribe(subscriptions)
    finally:
        stop_listening()


async def follow_permission_changes(
    nats: Subscriber, cache: PermissionCache, interruptions: Interruptions
) -> Never:
    """Follow identity's events until cancelled (``following``): a service's worker (see the module)."""
    async with following(nats, cache, interruptions):
        while True:
            await asyncio.get_running_loop().create_future()
