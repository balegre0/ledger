"""What changes identity's state: each use case runs in one unit, which commits its changes and events."""

from dataclasses import dataclass
from typing import final
lazy from collections.abc import Callable
lazy from datetime import date

from ledger_identity.application.queries import main_establishment
from ledger_identity.domain.company import Company
from ledger_identity.domain.errors import EstablishmentCodeTakenError, StaleVersionError, TaxIdTakenError
from ledger_identity.domain.establishments import Establishment
from ledger_identity.domain.membership import Actor, Roster, found, grant, revoke
from ledger_identity.domain.organization import Organization, require_organization_admin
from ledger_identity.domain.roles import COMPANIES_MANAGE
from ledger_kernel.access import CompanyNotFoundError
lazy from ledger_identity.application.ports import AuditAction, IdentityUnit
lazy from ledger_identity.domain.company import FiscalProfile
lazy from ledger_identity.domain.membership import Membership
lazy from ledger_identity.domain.text import Code, LegalName, Name, TimeZone
lazy from ledger_kernel.access import Principal, Role, SubjectId
lazy from ledger_kernel.ids import CompanyId

type Admits = Callable[[int], bool]
"""Whether a change may apply to a company at a version: the client's ``If-Match``."""


@final
@dataclass(frozen=True, slots=True)
class Opening:
    """What an establishment opens under: its code, unique in the company, its name and its time zone.

    No time zone is the one of the company's ``MAIN`` establishment.
    """

    code: Code
    name: Name
    time_zone: TimeZone | None


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Registration:
    """What a company is registered with.

    Its fiscal profile, its name, the zone its main establishment keeps, and the API version its organization
    is pinned to if this is the organization's first company.
    """

    profile: FiscalProfile
    legal_name: LegalName
    time_zone: TimeZone
    api_version: date


@final
@dataclass(frozen=True, slots=True)
class Granted:
    """The membership a grant left, and whether the subject held no active role before it."""

    membership: Membership
    created: bool


async def create_company(unit: IdentityUnit, founder: Principal, registration: Registration) -> Company:
    """Register a company of the founder's organization, with the founder as its first owner.

    It is opened with its ``MAIN`` establishment, in the registration's time zone. The organization, kept with
    its first company, is pinned to the registration's API version; a later company leaves it as it was.

    The founder must be an administrator of the organization (``require_organization_admin``). Any one of the
    profile's tax ids that an active company holds refuses the registration (``TaxIdTakenError``).
    """
    require_organization_admin(founder)
    await unit.organizations.add(Organization.of(founder, now=unit.now, api_version=registration.api_version))
    registered, created = Company.register(
        organization=founder.organization,
        profile=registration.profile,
        legal_name=registration.legal_name,
        now=unit.now,
    )
    if not await unit.companies.add(registered):
        raise TaxIdTakenError
    owner, granted = found(registered, founder, now=unit.now)
    await unit.memberships.put(owner)
    main = Establishment.main(registered, time_zone=registration.time_zone, now=unit.now)
    _ = await unit.establishments.add(main)  # the first of a new company: its code cannot be taken
    unit.events.publish(created)
    unit.events.publish(granted)
    unit.audit.record("company.register", registered.id, before=None, after=registered)
    _audit_membership(unit, "membership.grant", None, owner)
    _audit_establishment(unit, main)
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


async def lock_fiscal_profile(unit: IdentityUnit, company_id: CompanyId) -> Company:
    """Lock the company's fiscal profile: its jurisdiction, functional currency and primary tax id.

    The platform's call, before a company's first numbered document; one already locked stays as it was, so a
    retry changes and announces nothing.
    """
    current = await _locked(unit, company_id)
    locked, updated = current.lock_fiscal_profile(now=unit.now)
    if updated is not None:
        await _replace(unit, current, locked)
        unit.events.publish(updated)
        unit.audit.record("company.lock_fiscal_profile", current.id, before=current, after=locked)
    return locked


async def open_establishment(
    unit: IdentityUnit, company_id: CompanyId, opening: Opening, *, actor: SubjectId
) -> Establishment:
    """Open an establishment of the company under a code it does not use yet, as ``actor`` may.

    Without a time zone of its own it works in the one of the company's ``MAIN`` establishment.
    """
    company = await _managed(unit, company_id, actor)
    time_zone = opening.time_zone or (await main_establishment(unit, company_id)).time_zone
    opened = Establishment.open(
        company, code=opening.code, name=opening.name, time_zone=time_zone, now=unit.now
    )
    if not await unit.establishments.add(opened):
        raise EstablishmentCodeTakenError
    _audit_establishment(unit, opened)
    return opened


def _audit_establishment(unit: IdentityUnit, opened: Establishment) -> None:
    """Record that ``opened`` was opened, naming its code and time zone."""
    detail = {"code": opened.code, "time_zone": opened.time_zone}
    unit.audit.record("establishment.open", opened.id, before=None, after=opened, detail=detail)


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
