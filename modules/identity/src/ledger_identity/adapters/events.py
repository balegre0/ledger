"""What identity announces on the wire (§4.1): protobuf messages in envelopes, through the outbox.

Stream ``IDENTITY`` holds every subject ``identity.<aggregate>.<event>``; each event is scoped to its
company and organization (``ce-subject``, ``ce-organizationid``) and carries its aggregate's version.
"""

from typing import Final, assert_type, final, override
lazy from collections.abc import Mapping

lazy from google.protobuf.message import Message

import ledger.events.identity.v1.events_pb2 as wire
from ledger_contracts.envelope import Envelope
from ledger_identity.application.ports import Events
from ledger_identity.domain.events import (
    CompanyCreated,
    CompanyDeactivated,
    CompanyUpdated,
    MembershipGranted,
    MembershipRevoked,
)
from ledger_identity.domain.roles import Role
from ledger_kernel.clock import utc_now
from ledger_platform.streams import StreamSpec
lazy from ledger_identity.domain.events import IdentityEvent
lazy from ledger_kernel.clock import Clock
lazy from ledger_platform.data.database import UnitOfWork
lazy from ledger_platform.outbox import Outbox

IDENTITY: Final = StreamSpec(name="IDENTITY", subjects=("identity.>",))
"""The stream of identity's events, declared by identity at start."""
SOURCE: Final = "identity"
_ROLES: Final[Mapping[Role, wire.Role.ValueType]] = {
    Role.OWNER: wire.ROLE_OWNER,
    Role.ADMIN: wire.ROLE_ADMIN,
    Role.ACCOUNTANT: wire.ROLE_ACCOUNTANT,
    Role.ISSUER: wire.ROLE_ISSUER,
    Role.VIEWER: wire.ROLE_VIEWER,
}


def _granted(event: MembershipGranted) -> wire.MembershipGranted:
    return wire.MembershipGranted(
        company_id=str(event.tenant.company),
        organization_id=str(event.tenant.organization),
        subject=event.subject,
        role=_ROLES[event.role],
        previous_role=wire.ROLE_UNSPECIFIED if event.previous is None else _ROLES[event.previous],
        version=event.version,
    )


def _revoked(event: MembershipRevoked) -> wire.MembershipRevoked:
    return wire.MembershipRevoked(
        company_id=str(event.tenant.company),
        organization_id=str(event.tenant.organization),
        subject=event.subject,
        role=_ROLES[event.role],
        version=event.version,
    )


def outgoing(event: IdentityEvent) -> tuple[Message, str]:
    """The payload of ``event`` and the subject it is published on.

    Exhaustive over ``IdentityEvent``: what reaches the last case must be a ``MembershipRevoked``
    (``assert_type``), so a new event fails type checking here instead of going out as a revocation.
    """
    company, organization = str(event.tenant.company), str(event.tenant.organization)
    match event:
        case CompanyCreated(tax_id=tax_id, legal_name=legal_name, version=version):
            created = wire.CompanyCreated(
                company_id=company,
                organization_id=organization,
                tax_id=str(tax_id),
                legal_name=legal_name,
                version=version,
            )
            return created, "identity.company.created"
        case CompanyUpdated(tax_id=tax_id, legal_name=legal_name, version=version):
            updated = wire.CompanyUpdated(
                company_id=company,
                organization_id=organization,
                tax_id=str(tax_id),
                legal_name=legal_name,
                version=version,
            )
            return updated, "identity.company.updated"
        case CompanyDeactivated(version=version):
            deactivated = wire.CompanyDeactivated(
                company_id=company, organization_id=organization, version=version
            )
            return deactivated, "identity.company.deactivated"
        case MembershipGranted():
            return _granted(event), "identity.membership.granted"
        case revoked:
            return _revoked(assert_type(revoked, MembershipRevoked)), "identity.membership.revoked"


@final
class OutboxEvents(Events):
    """Announces identity's events through the outbox of the unit's transaction."""

    __slots__ = ("_clock", "_outbox", "_unit")

    def __init__(self, unit: UnitOfWork, outbox: Outbox, *, clock: Clock = utc_now) -> None:
        self._unit = unit
        self._outbox = outbox
        self._clock = clock

    @override
    async def publish(self, event: IdentityEvent) -> None:
        message, subject = outgoing(event)
        envelope = Envelope.wrap(message, source=SOURCE, tenant=event.tenant, clock=self._clock)
        await self._outbox.add(self._unit, envelope, subject=subject)
