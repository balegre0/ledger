"""What changes identity's state: each use case runs in one unit, which commits its changes and events."""

from dataclasses import dataclass
from typing import final
lazy from collections.abc import Callable

from ledger_identity.application.queries import branch
from ledger_identity.domain.branches import Branch, EmissionPoint
from ledger_identity.domain.company import Company
from ledger_identity.domain.errors import (
    BranchCodeTakenError,
    EmissionPointCodeTakenError,
    StaleVersionError,
    TaxIdTakenError,
)
from ledger_identity.domain.membership import Actor, Roster, found, grant, revoke
from ledger_identity.domain.organization import Organization, require_organization_admin
from ledger_identity.domain.roles import COMPANIES_MANAGE
from ledger_kernel.access import CompanyNotFoundError
lazy from ledger_identity.application.ports import AuditAction, IdentityUnit
lazy from ledger_identity.domain.ids import BranchId
lazy from ledger_identity.domain.membership import Membership
lazy from ledger_identity.domain.text import Code, LegalName, Name
lazy from ledger_kernel.access import Principal, Role, SubjectId
lazy from ledger_kernel.ids import CompanyId
lazy from ledger_kernel.tax_ids import TaxId

type Admits = Callable[[int], bool]
"""Whether a change may apply to a company at a version: the client's ``If-Match``."""


@final
@dataclass(frozen=True, slots=True)
class Opening:
    """What a branch or an emission point opens under: its code, unique where it opens, and its name."""

    code: Code
    name: Name


@final
@dataclass(frozen=True, slots=True)
class Granted:
    """The membership a grant left, and whether the subject held no active role before it."""

    membership: Membership
    created: bool


async def create_company(
    unit: IdentityUnit, founder: Principal, *, tax_id: TaxId, legal_name: LegalName
) -> Company:
    """Register a company of the founder's organization, with the founder as its first owner.

    The founder must be an administrator of the organization (``require_organization_admin``).
    """
    require_organization_admin(founder)
    await unit.organizations.add(Organization.of(founder, now=unit.now))
    registered, created = Company.register(
        organization=founder.organization, tax_id=tax_id, legal_name=legal_name, now=unit.now
    )
    if not await unit.companies.add(registered):
        raise TaxIdTakenError
    owner, granted = found(registered, founder, now=unit.now)
    await unit.memberships.put(owner)
    unit.events.publish(created)
    unit.events.publish(granted)
    unit.audit.record("company.register", registered.id, before=None, after=registered)
    _audit_membership(unit, "membership.grant", None, owner)
    return registered


def _audit_membership(
    unit: IdentityUnit, action: AuditAction, before: Membership | None, after: Membership
) -> None:
    """Record a change of a membership: its member, its role after it, and the role it replaced.

    The replaced role is in the record because the hashes cannot say it, and the event that carried it is gone
    once delivered: without it nobody can tell who demoted a member from owner, and from what.
    """
    detail = {"member": after.subject, "role": after.role.value}
    if before is not None and before.active and before.role is not after.role:
        detail["previous_role"] = before.role.value
    unit.audit.record(action, after.id, before=before, after=after, detail=detail)


async def _admitted(unit: IdentityUnit, company_id: CompanyId, admits: Admits, actor: SubjectId) -> Company:
    """The company, locked, if ``actor``'s membership allows managing it now and ``admits`` its version.

    The actor is judged as it stands once the company is locked: a role it lost meanwhile does not count.
    """
    current = await _managed(unit, company_id, actor)
    if not admits(current.version):
        raise StaleVersionError(current.id)
    return current


async def _replace(unit: IdentityUnit, current: Company, changed: Company) -> None:
    """Store ``changed`` in place of ``current``, unless another unit changed the company meanwhile."""
    if not await unit.companies.update(changed, version=current.version):
        raise StaleVersionError(current.id)


async def rename_company(
    unit: IdentityUnit, company_id: CompanyId, *, legal_name: LegalName, admits: Admits, actor: SubjectId
) -> Company:
    """Give the company ``legal_name``, if its current version is one ``admits``, as ``actor`` may."""
    current = await _admitted(unit, company_id, admits, actor)
    renamed, updated = current.rename(legal_name, now=unit.now)
    if updated is not None:
        await _replace(unit, current, renamed)
        unit.events.publish(updated)
        unit.audit.record("company.rename", current.id, before=current, after=renamed)
    return renamed


async def deactivate_company(
    unit: IdentityUnit, company_id: CompanyId, *, admits: Admits, actor: SubjectId
) -> Company:
    """Deactivate the company, if its current version is one ``admits``, as ``actor`` may."""
    current = await _admitted(unit, company_id, admits, actor)
    inactive, deactivated = current.deactivate(now=unit.now)
    await _replace(unit, current, inactive)
    unit.events.publish(deactivated)
    unit.audit.record("company.deactivate", current.id, before=current, after=inactive)
    return inactive


async def open_branch(
    unit: IdentityUnit, company_id: CompanyId, opening: Opening, *, actor: SubjectId
) -> Branch:
    """Open a branch of the company under a code it does not use yet, as ``actor`` may."""
    company = await _managed(unit, company_id, actor)
    opened = Branch.open(company, code=opening.code, name=opening.name, now=unit.now)
    if not await unit.branches.add(opened):
        raise BranchCodeTakenError
    unit.audit.record("branch.open", opened.id, before=None, after=opened, detail={"code": opened.code})
    return opened


async def open_emission_point(
    unit: IdentityUnit, company_id: CompanyId, branch_id: BranchId, opening: Opening, *, actor: SubjectId
) -> EmissionPoint:
    """Open an emission point of one of the company's branches, under a code the branch does not use yet."""
    company = await _managed(unit, company_id, actor)
    within = await branch(unit, company_id, branch_id)
    opened = EmissionPoint.open(company, within, code=opening.code, name=opening.name, now=unit.now)
    if not await unit.emission_points.add(opened):
        raise EmissionPointCodeTakenError
    detail = {"branch_id": str(within.id), "code": opened.code}
    unit.audit.record("emission_point.open", opened.id, before=None, after=opened, detail=detail)
    return opened


async def _managed(unit: IdentityUnit, company_id: CompanyId, actor: SubjectId) -> Company:
    """The company, locked, if ``actor``'s membership allows managing it as it stands once locked."""
    locked = await _locked(unit, company_id)
    (await _actor(unit, company_id, actor)).require(COMPANIES_MANAGE)
    return locked


async def _locked(unit: IdentityUnit, company_id: CompanyId) -> Company:
    """The company, locked until the unit ends: what it is checked active against cannot change meanwhile."""
    locked = await unit.companies.locked(company_id)
    if locked is None:
        raise CompanyNotFoundError(company_id)
    return locked


async def _roster(unit: IdentityUnit, company_id: CompanyId, subject: SubjectId) -> Roster:
    """The company locked, ``subject``'s membership and the owners: one membership change at a time."""
    locked = await _locked(unit, company_id)
    current = await unit.memberships.get(company_id, subject)
    return Roster(locked, subject, current, await unit.memberships.owners(company_id))


async def _actor(unit: IdentityUnit, company_id: CompanyId, subject: SubjectId) -> Actor:
    """The actor as its own membership stands now, read once the company is locked.

    The route checked the actor's permission before; a change that committed since (a demotion, a revocation)
    counts. Without an active membership the company does not exist for the actor.
    """
    held = await unit.memberships.get(company_id, subject)
    if held is None or not held.active:
        raise CompanyNotFoundError(company_id)
    return Actor(subject, held.permissions)


async def grant_role(
    unit: IdentityUnit, company_id: CompanyId, *, subject: SubjectId, role: Role, actor: SubjectId
) -> Granted:
    """Give ``subject`` the role ``role`` in the company, as the membership of ``actor`` allows."""
    roster = await _roster(unit, company_id, subject)
    acting = await _actor(unit, company_id, actor)
    membership, granted = grant(roster, role=role, actor=acting, now=unit.now)
    current = roster.current
    if granted is not None:
        await unit.memberships.put(membership)
        unit.events.publish(granted)
        _audit_membership(unit, "membership.grant", current, membership)
    return Granted(membership, created=current is None or not current.active)


async def revoke_role(
    unit: IdentityUnit, company_id: CompanyId, *, subject: SubjectId, actor: SubjectId
) -> None:
    """Take ``subject``'s role in the company away, as the membership of ``actor`` allows.

    A role revoked already stays so: nothing is stored or announced again.
    """
    roster = await _roster(unit, company_id, subject)
    acting = await _actor(unit, company_id, actor)
    revoked, event = revoke(roster, actor=acting, now=unit.now)
    if event is not None:
        await unit.memberships.put(revoked)
        unit.events.publish(event)
        _audit_membership(unit, "membership.revoke", roster.current, revoked)
