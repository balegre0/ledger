"""The platform's client of the identity service: permissions for the authorization context, kept fresh.

``RemotePermissions`` asks ``IdentityQueryService.GetPermissions``, with a deadline on every call. A service
puts it behind a ``CachedPermissions``, and ``MembershipChanges`` drops what a membership event changed.
Every replica follows the events itself, on a core NATS subscription: a durable consumer would hand each event
to one replica only. Following is best effort; the cache's TTL bounds what a lost event costs.

A service opens its channel to identity as one of its resources (``Interfaces.resources``), so that the
channel is closed in order when the service stops, and follows the membership events from the same hook:
NATS is connected by then, and the subscription ends when NATS drains at the stop. The first such service
comes in milestone 3.
"""

from abc import ABCMeta, abstractmethod
from typing import Final, Protocol, final, override
lazy from collections.abc import Awaitable, Callable, Iterable, Mapping

import structlog
lazy import grpc
lazy from nats.aio.msg import Msg
lazy from nats.aio.subscription import Subscription

from ledger.events.identity.v1.events_pb2 import MembershipGranted, MembershipRevoked
from ledger.identity.v1.identity_query_pb2 import GetPermissionsRequest
from ledger.identity.v1.identity_query_pb2_grpc import IdentityQueryServiceStub
from ledger_contracts.envelope import Envelope
from ledger_kernel.access import Permission, parse_subject
from ledger_kernel.errors import InvalidInputError
from ledger_kernel.ids import CompanyId, OrganizationId, parse_id
from ledger_platform.authorization import PermissionSource
from ledger_platform.grpc_client import DEFAULT_DEADLINE, call_unary, require_deadline
lazy from ledger_kernel.access import Principal, SubjectId

GET_PERMISSIONS: Final = "/ledger.identity.v1.IdentityQueryService/GetPermissions"
MEMBERSHIP_SUBJECTS: Final = "identity.membership.>"
_GRANTED: Final = MembershipGranted.DESCRIPTOR.full_name
_REVOKED: Final = MembershipRevoked.DESCRIPTOR.full_name
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
    """What ``MembershipChanges`` tells: a ``CachedPermissions`` is one."""

    @abstractmethod
    def forget(self, organization: OrganizationId, company: CompanyId, subject: SubjectId) -> None:
        """Drop what is kept about ``subject`` in ``company``."""


class Subscriber(Protocol, metaclass=ABCMeta):
    """What ``MembershipChanges`` asks of a NATS connection: a ``nats.aio.client.Client`` is one.

    nats-py leaves the type of ``subscribe``'s ``future`` open; seen through this protocol, only the ones
    used here remain.
    """

    @abstractmethod
    async def subscribe(self, subject: str, *, cb: Callable[[Msg], Awaitable[None]]) -> Subscription:
        """Deliver every message on ``subject`` to ``cb``."""


def _member(organization: str, company: str, subject: str) -> tuple[OrganizationId, CompanyId, SubjectId]:
    return OrganizationId(parse_id(organization)), CompanyId(parse_id(company)), parse_subject(subject)


def _changed(envelope: Envelope) -> tuple[OrganizationId, CompanyId, SubjectId] | None:
    """Whose permissions a membership event changed; ``None`` for any other event."""
    if envelope.type == _GRANTED:
        grant = envelope.unwrap(MembershipGranted)
        return _member(grant.organization_id, grant.company_id, grant.subject)
    if envelope.type == _REVOKED:
        revocation = envelope.unwrap(MembershipRevoked)
        return _member(revocation.organization_id, revocation.company_id, revocation.subject)
    return None


@final
class MembershipChanges:
    """Keeps a permission cache honest: drops what identity announces has changed."""

    __slots__ = ("_cache",)

    def __init__(self, cache: PermissionCache) -> None:
        self._cache = cache

    async def follow(self, nats: Subscriber) -> Subscription:
        """Follow every membership event on this replica; unsubscribe, or drain the connection, to stop."""
        return await nats.subscribe(MEMBERSHIP_SUBJECTS, cb=self.handle)

    async def handle(self, message: Msg) -> None:
        """Forget the permissions an event changed; one that cannot be read is logged and skipped."""
        try:
            changed = _changed(Envelope.from_headers(message.headers or {}, message.data))
        except InvalidInputError as error:
            _logger.warning(
                "authorization.membership_event_unreadable", subject=message.subject, code=error.code
            )
            return
        if changed is not None:
            self._cache.forget(*changed)
