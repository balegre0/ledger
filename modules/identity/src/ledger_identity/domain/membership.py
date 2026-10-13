"""Memberships: who holds which role in a company, and the rules for changing that (§8).

One role per principal and company. The founder of a company is its first owner. Roles are granted and
revoked by whoever may manage memberships (``MEMBERSHIP_MANAGE``), the owner role by owners only
(``OWNERSHIP_MANAGE``); the actor is judged on its membership as it is when the change is made, under the
company's lock. A company always keeps an owner: the last can be neither demoted nor revoked. Roles are
granted in active companies only, but can always be revoked: taking access away must never be blocked.

A revoked membership is kept, with its last role and status ``revoked``: its version only grows, so a role
granted again continues from it, and the version every event carries never goes back (§4.1).
"""

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import final
lazy from datetime import datetime

from ledger_identity.domain.company import CompanyStatus
from ledger_identity.domain.errors import (
    ActorNotAllowedError,
    InvalidStateError,
    LastOwnerError,
    MembershipNotFoundError,
    OwnersOnlyError,
)
from ledger_identity.domain.events import MembershipGranted, MembershipRevoked
from ledger_identity.domain.ids import MembershipId
from ledger_identity.domain.roles import READ_ONLY, Role, permissions_of
from ledger_kernel.access import Permission
from ledger_kernel.ids import new_id
lazy from ledger_identity.domain.company import Company
lazy from ledger_kernel.access import Principal, SubjectId
lazy from ledger_kernel.ids import Tenant


class MembershipStatus(StrEnum):
    """Whether a membership still grants its role."""

    ACTIVE = "active"
    REVOKED = "revoked"


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Membership:
    """A principal's role in a company (or the one held until revoked), its version and its last change."""

    id: MembershipId
    tenant: Tenant
    subject: SubjectId
    role: Role
    status: MembershipStatus
    version: int
    changed_at: datetime
    """When the membership last changed: the grant of its role, or its revocation."""
    changed_by: SubjectId
    """Who made that change."""

    def __post_init__(self) -> None:
        if self.version < 1:
            raise InvalidStateError(field="membership.version", requirement="at least 1")

    @property
    def active(self) -> bool:
        """Whether the membership still grants its role."""
        return self.status is MembershipStatus.ACTIVE

    @property
    def permissions(self) -> frozenset[Permission]:
        """What the role allows in the company; nothing once the membership is revoked."""
        return permissions_of(self.role) if self.active else frozenset()


@final
@dataclass(frozen=True, slots=True)
class Actor:
    """Who changes a membership, and what its own membership allows it in the company now."""

    subject: SubjectId
    permissions: frozenset[Permission]

    def require(self, permission: Permission) -> None:
        """Refuse with ``ActorNotAllowedError`` unless its membership allows ``permission`` now."""
        if permission not in self.permissions:
            raise ActorNotAllowedError(permission)

    @property
    def manages_ownership(self) -> bool:
        """Whether it may grant and take the owner role."""
        return Permission.OWNERSHIP_MANAGE in self.permissions


@final
@dataclass(frozen=True, slots=True)
class Roster:
    """What a change of one principal's role is decided on: company, principal, its membership, owners.

    Built from rows read for the same ``subject`` and ``company``: a ``current`` of anyone else, or of another
    company, would move one principal's role to another, so it is refused where the roster is made.
    """

    company: Company
    subject: SubjectId
    current: Membership | None
    """The principal's membership, active or revoked; ``None`` if it never held a role there."""
    owners: int
    """How many owners the company has now."""

    def __post_init__(self) -> None:
        current = self.current
        if current is not None and current.subject != self.subject:
            raise InvalidStateError(field="roster.current", requirement="the membership of its subject")
        if current is not None and current.tenant != self.company.tenant:
            raise InvalidStateError(field="roster.current", requirement="the membership of its company")


def found(company: Company, founder: Principal, *, now: datetime) -> tuple[Membership, MembershipGranted]:
    """The membership of a company's founder, a principal of its organization: its first owner."""
    company.require_active()
    if founder.organization != company.organization:
        raise InvalidStateError(field="founder", requirement="a principal of the company's organization")
    owner = Membership(
        id=MembershipId(new_id()),
        tenant=company.tenant,
        subject=founder.subject,
        role=Role.OWNER,
        status=MembershipStatus.ACTIVE,
        version=1,
        changed_at=now,
        changed_by=founder.subject,
    )
    granted = MembershipGranted(
        tenant=company.tenant, subject=founder.subject, role=Role.OWNER, previous=None, version=1
    )
    return owner, granted


def _require_managing(actor: Actor) -> None:
    actor.require(Permission.MEMBERSHIP_MANAGE)


def _require_ownership(actor: Actor) -> None:
    if not actor.manages_ownership:
        raise OwnersOnlyError


def grant(
    roster: Roster, *, role: Role, actor: Actor, now: datetime
) -> tuple[Membership, MembershipGranted | None]:
    """The roster subject's membership with ``role``; unchanged, and no event, when it holds it already."""
    roster.company.require_active()
    _require_managing(actor)
    held = roster.current
    current = held if held is not None and held.active else None
    if role is Role.OWNER or (current is not None and current.role is Role.OWNER):
        _require_ownership(actor)
    if current is not None and current.role is role:
        return current, None
    if current is not None and current.role is Role.OWNER and roster.owners <= 1:
        raise LastOwnerError
    membership = Membership(
        id=MembershipId(new_id()) if held is None else held.id,
        tenant=roster.company.tenant,
        subject=roster.subject,
        role=role,
        status=MembershipStatus.ACTIVE,
        version=1 if held is None else held.version + 1,
        changed_at=now,
        changed_by=actor.subject,
    )
    granted = MembershipGranted(
        tenant=membership.tenant,
        subject=roster.subject,
        role=role,
        previous=None if current is None else current.role,
        version=membership.version,
    )
    return membership, granted


def revoke(roster: Roster, *, actor: Actor, now: datetime) -> tuple[Membership, MembershipRevoked | None]:
    """Take the roster subject's role away: its membership, revoked, and the event that says so.

    A membership revoked already is left as it is, without an event: a retried revocation answers as the first
    did (the actor judged as it would have been then). A subject that never held a role has nothing to revoke.
    """
    _require_managing(actor)
    current = roster.current
    if current is None:
        raise MembershipNotFoundError(roster.subject)
    if current.role is Role.OWNER:
        _require_ownership(actor)
    if not current.active:
        return current, None
    if current.role is Role.OWNER and roster.owners <= 1:
        raise LastOwnerError
    revoked = replace(
        current,
        status=MembershipStatus.REVOKED,
        version=current.version + 1,
        changed_at=now,
        changed_by=actor.subject,
    )
    event = MembershipRevoked(
        tenant=current.tenant, subject=current.subject, role=current.role, version=revoked.version
    )
    return revoked, event


def permissions_in(company: Company, membership: Membership) -> frozenset[Permission]:
    """What other services may let ``membership``'s holder do for ``company``.

    Its role's permissions while it is active; of those, only the ones that read once the company is
    deactivated (other services cannot tell, and must not issue for it); nothing once it is revoked.
    """
    if company.status is CompanyStatus.INACTIVE:
        return membership.permissions & READ_ONLY
    return membership.permissions
