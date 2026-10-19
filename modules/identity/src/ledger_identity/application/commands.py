"""What changes identity's state: each use case runs in one unit, which commits its changes and events."""

from dataclasses import dataclass
from typing import final
lazy from collections.abc import Callable
lazy from datetime import date

from ledger_identity.application.queries import main_establishment
from ledger_identity.domain.api_clients import ApiClient
from ledger_identity.domain.company import Company, FiscalProfile, require_fits
from ledger_identity.domain.errors import (
    ApiClientExistsError,
    ApiClientHumanError,
    ApiClientNotFoundError,
    EstablishmentCodeTakenError,
    EstablishmentsExistError,
    StaleVersionError,
    TaxIdTakenError,
    TimeZoneRequiredError,
    TimeZoneUnusedError,
)
from ledger_identity.domain.establishments import Establishment
from ledger_identity.domain.membership import Actor, Roster, found, grant, revoke
from ledger_identity.domain.organization import Organization, require_organization_admin
from ledger_identity.domain.roles import COMPANIES_MANAGE
from ledger_kernel.access import CompanyNotFoundError, PrincipalKind
from ledger_kernel.pagination import PageRequest
lazy from ledger_identity.application.ports import AuditAction, IdentityUnit
lazy from ledger_identity.domain.api_clients import ApiClientSpec
lazy from ledger_identity.domain.ids import ApiClientId
lazy from ledger_identity.domain.membership import Membership
lazy from ledger_identity.domain.text import Code, LegalName, Name, TimeZone
lazy from ledger_kernel.access import Principal, Role, SubjectId
lazy from ledger_kernel.ids import CompanyId
lazy from ledger_kernel.jurisdiction import Jurisdiction
lazy from ledger_kernel.money import Currency
lazy from ledger_kernel.regimes import RegimeDef
lazy from ledger_kernel.tax_ids import TaxId
lazy from ledger_kernel.tenancy import Mode

type Admits = Callable[[int], bool]
"""Whether a change may apply to a company at a version: the client's ``If-Match``."""
type RegimeOf = Callable[[Jurisdiction], RegimeDef | None]
"""The installed regime that governs a jurisdiction, if any."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Changes:
    """What a change of a company sets; whatever is ``None`` stays as it is.

    The jurisdiction, the functional currency and the tax ids make its fiscal profile (the tax ids as a whole
    list, the first its primary one): they change only until the profile is locked.
    """

    regime_of: RegimeOf
    """What judges a new profile: the installed regime of its jurisdiction."""
    legal_name: LegalName | None = None
    jurisdiction: Jurisdiction | None = None
    functional_currency: Currency | None = None
    tax_ids: tuple[TaxId, ...] | None = None
    time_zone: TimeZone | None = None
    """The zone ``MAIN`` keeps once the jurisdiction changes: required then, refused otherwise."""

    def profile_of(self, company: Company) -> FiscalProfile | None:
        """``company``'s profile with these changes, if any part of it changes at all."""
        if (self.jurisdiction, self.functional_currency, self.tax_ids) == (None, None, None):
            return None
        current = company.profile
        return FiscalProfile(
            jurisdiction=current.jurisdiction if self.jurisdiction is None else self.jurisdiction,
            functional_currency=(
                current.functional_currency if self.functional_currency is None else self.functional_currency
            ),
            tax_ids=current.tax_ids if self.tax_ids is None else self.tax_ids,
        )


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
@dataclass(frozen=True, slots=True, kw_only=True)
class ClientRegistration:
    """An API client to register: what it is registered as, and the mode it is bound to."""

    spec: ApiClientSpec
    mode: Mode


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


async def change_company(
    unit: IdentityUnit,
    company_id: CompanyId,
    changes: Changes,
    *,
    admits: Admits,
    actor: SubjectId,
) -> Company:
    """Change the company as ``changes`` say, if its current version is one ``admits``, as ``actor`` may.

    A fiscal profile changes only until it is locked (``FiscalProfileLockedError``), and only to one the
    regime of its jurisdiction allows (``require_fits``); a tax id it lists anew that an active company
    holds refuses the change (``TaxIdTakenError``), and one it no longer lists is released. Nothing that is
    so already is changed, announced or recorded.
    """
    current = await _admitted(unit, company_id, admits, actor)
    profile = changes.profile_of(current)
    changed, updated = current.change(legal_name=changes.legal_name, profile=profile, now=unit.now)
    moved = changed.profile.jurisdiction != current.profile.jurisdiction
    if not moved and changes.time_zone is not None:
        raise TimeZoneUnusedError
    if updated is None:
        return current
    refiled = changed.profile != current.profile
    if refiled:
        _ = require_fits(changed.profile, changes.regime_of(changed.profile.jurisdiction))
    if moved:
        await _move_main(unit, current, changes.time_zone)
    await _replace(unit, current, changed)
    if changed.profile.tax_ids != current.profile.tax_ids and not await unit.companies.retax(changed):
        raise TaxIdTakenError
    unit.events.publish(updated)
    action: AuditAction = "company.change_profile" if refiled else "company.rename"
    unit.audit.record(action, current.id, before=current, after=changed)
    return changed


async def _move_main(unit: IdentityUnit, company: Company, time_zone: TimeZone | None) -> None:
    """The ``MAIN`` establishment into ``time_zone``, the company changing jurisdiction.

    The old regime's zone would be stale, so the change names the new one (``TimeZoneRequiredError``); a
    company with establishments besides ``MAIN`` keeps its jurisdiction (``EstablishmentsExistError``): their
    zones were chosen under the old one, and identity will not guess them anew.
    """
    if time_zone is None:
        raise TimeZoneRequiredError
    if len((await unit.establishments.page(company.id, PageRequest(size=2))).items) > 1:
        raise EstablishmentsExistError
    main = await main_establishment(unit, company.id)
    moved = main.keeping(time_zone)
    await unit.establishments.update(moved)
    detail = {"code": moved.code, "time_zone": moved.time_zone, "previous_time_zone": main.time_zone}
    unit.audit.record("establishment.change_time_zone", moved.id, before=main, after=moved, detail=detail)


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


async def register_api_client(
    unit: IdentityUnit, admin: Principal, registration: ClientRegistration
) -> ApiClient:
    """Register a machine user as an API client of the admin's organization, bound to the registration's mode.

    The admin must be an administrator of the organization (``require_organization_admin``). A machine user
    that is an active API client already, in either mode, is refused (``ApiClientExistsError``): one
    credential carries one mode. The unit's mode is the registration's. An organization identity has not seen
    yet is kept now, pinned to the client's version, as its first company would have kept it.

    An API client is a machine user: a subject identity knows to be a person, the registering person
    itself, is refused (``ApiClientHumanError``). identity learns no other subject's kind before it calls,
    so a person registered by mistake is not refused here: that person is then held to the client's mode
    and revocation until it is revoked (its standing is the client's, whatever the token's kind).
    """
    require_organization_admin(admin)
    if admin.kind is PrincipalKind.HUMAN and registration.spec.subject == admin.subject:
        raise ApiClientHumanError
    await unit.organizations.add(
        Organization.of(admin, now=unit.now, api_version=registration.spec.api_version)
    )
    client = ApiClient.register(
        registration.spec,
        organization=admin.organization,
        mode=registration.mode,
        actor=admin.subject,
        now=unit.now,
    )
    if not await unit.api_clients.add(client):
        raise ApiClientExistsError
    unit.audit.record(
        "api_client.register", client.id, before=None, after=client, detail=_client_detail(client)
    )
    return client


async def revoke_api_client(unit: IdentityUnit, admin: Principal, client_id: ApiClientId) -> ApiClient:
    """Revoke an API client of the unit's mode: its tokens stop counting. Idempotent.

    The admin must be an administrator of the organization. Revoking a client that is revoked already changes
    and records nothing; an id the organization has no client by, in this mode, is not found.
    """
    require_organization_admin(admin)
    current = await unit.api_clients.get(client_id)
    if current is None:
        raise ApiClientNotFoundError(client_id)
    revoked = current.revoke(now=unit.now)
    if revoked is not current and await unit.api_clients.revoked(revoked):
        unit.audit.record(
            "api_client.revoke", current.id, before=current, after=revoked, detail=_client_detail(revoked)
        )
    return revoked


def _client_detail(client: ApiClient) -> dict[str, str]:
    """What an API client's audit rows say of it besides its hashes: ids and words, never its name."""
    return {"subject": client.subject, "mode": client.mode.value}


async def lock_fiscal_profile(unit: IdentityUnit, company_id: CompanyId) -> Company:
    """Lock the company's fiscal profile: its jurisdiction, functional currency and tax ids.

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
