"""What changes identity's state: each use case runs in one unit, which commits its changes and events."""

from dataclasses import dataclass
from typing import final
lazy from collections.abc import Callable

from ledger_identity.application.queries import branch, company
from ledger_identity.domain.branches import Branch, EmissionPoint
from ledger_identity.domain.company import Company
from ledger_identity.domain.errors import (
    BranchCodeTakenError,
    CompanyNotFoundError,
    EmissionPointCodeTakenError,
    StaleVersionError,
    TaxIdTakenError,
)
from ledger_identity.domain.membership import Actor, Roster, found, grant, revoke
from ledger_identity.domain.organization import Organization
lazy from ledger_identity.application.ports import IdentityUnit
lazy from ledger_identity.domain.ids import BranchId
lazy from ledger_identity.domain.membership import Membership
lazy from ledger_identity.domain.roles import Role
lazy from ledger_identity.domain.text import Code, LegalName, Name
lazy from ledger_kernel.access import Principal, SubjectId
lazy from ledger_kernel.countries.do import TaxId
lazy from ledger_kernel.ids import CompanyId

type Admits = Callable[[int], bool]
"""Whether a change may apply to a company at a version: the client's ``If-Match``."""


@final
@dataclass(frozen=True, slots=True)
class Granted:
    """The membership a grant left, and whether the subject held no active role before it."""

    membership: Membership
    created: bool


async def create_company(
    unit: IdentityUnit, founder: Principal, *, tax_id: TaxId, legal_name: LegalName
) -> Company:
    """Register a company of the founder's organization, with the founder as its first owner."""
    await unit.organizations.add(Organization.of(founder, now=unit.now))
    registered, created = Company.register(
        organization=founder.organization, tax_id=tax_id, legal_name=legal_name, now=unit.now
    )
    if not await unit.companies.add(registered):
        raise TaxIdTakenError
    owner, granted = found(registered, founder, now=unit.now)
    await unit.memberships.put(owner)
    await unit.events.publish(created)
    await unit.events.publish(granted)
    return registered


async def _admitted(unit: IdentityUnit, company_id: CompanyId, admits: Admits) -> Company:
    current = await company(unit, company_id)
    if not admits(current.version):
        raise StaleVersionError(current.id)
    return current


async def _replace(unit: IdentityUnit, current: Company, changed: Company) -> None:
    """Store ``changed`` in place of ``current``, unless another unit changed the company meanwhile."""
    if not await unit.companies.update(changed, version=current.version):
        raise StaleVersionError(current.id)


async def rename_company(
    unit: IdentityUnit, company_id: CompanyId, *, legal_name: LegalName, admits: Admits
) -> Company:
    """Give the company ``legal_name``, if its current version is one ``admits``."""
    current = await _admitted(unit, company_id, admits)
    renamed, updated = current.rename(legal_name, now=unit.now)
    if updated is not None:
        await _replace(unit, current, renamed)
        await unit.events.publish(updated)
    return renamed


async def deactivate_company(unit: IdentityUnit, company_id: CompanyId, *, admits: Admits) -> Company:
    """Deactivate the company, if its current version is one ``admits``."""
    current = await _admitted(unit, company_id, admits)
    inactive, deactivated = current.deactivate(now=unit.now)
    await _replace(unit, current, inactive)
    await unit.events.publish(deactivated)
    return inactive


async def open_branch(unit: IdentityUnit, company_id: CompanyId, *, code: Code, name: Name) -> Branch:
    """Open a branch of the company under a code it does not use yet."""
    opened = Branch.open(await _locked(unit, company_id), code=code, name=name, now=unit.now)
    if not await unit.branches.add(opened):
        raise BranchCodeTakenError
    return opened


async def open_emission_point(
    unit: IdentityUnit, company_id: CompanyId, branch_id: BranchId, *, code: Code, name: Name
) -> EmissionPoint:
    """Open an emission point of one of the company's branches, under a code the branch does not use yet."""
    owner = await _locked(unit, company_id)
    opened = EmissionPoint.open(
        owner, await branch(unit, company_id, branch_id), code=code, name=name, now=unit.now
    )
    if not await unit.emission_points.add(opened):
        raise EmissionPointCodeTakenError
    return opened


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
    if granted is not None:
        await unit.memberships.put(membership)
        await unit.events.publish(granted)
    current = roster.current
    return Granted(membership, created=current is None or not current.active)


async def revoke_role(
    unit: IdentityUnit, company_id: CompanyId, *, subject: SubjectId, actor: SubjectId
) -> None:
    """Take ``subject``'s role in the company away, as the membership of ``actor`` allows."""
    roster = await _roster(unit, company_id, subject)
    acting = await _actor(unit, company_id, actor)
    revoked, event = revoke(roster, actor=acting, now=unit.now)
    await unit.memberships.put(revoked)
    await unit.events.publish(event)
