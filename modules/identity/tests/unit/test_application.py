"""The identity use cases over in-memory ports: what they change, announce and refuse."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Final, final, override
lazy from collections.abc import Callable, Iterable
lazy from uuid import UUID

import pytest

from ledger_identity.application import commands, queries
from ledger_identity.application.ports import (
    Branches,
    Companies,
    EmissionPoints,
    Events,
    IdentityUnit,
    Memberships,
    Organizations,
)
from ledger_identity.domain.company import CompanyStatus
from ledger_identity.domain.errors import (
    ActorNotAllowedError,
    BranchCodeTakenError,
    BranchNotFoundError,
    CompanyInactiveError,
    CompanyNotFoundError,
    EmissionPointCodeTakenError,
    EmissionPointNotFoundError,
    LastOwnerError,
    MembershipNotFoundError,
    OrganizationAdminRequiredError,
    OrganizationNotFoundError,
    OwnersOnlyError,
    StaleVersionError,
    TaxIdTakenError,
)
from ledger_identity.domain.ids import BranchId, EmissionPointId
from ledger_identity.domain.organization import ORGANIZATION_ADMIN, Organization
from ledger_identity.domain.roles import READ_ONLY, Role
from ledger_identity.domain.text import parse_code, parse_legal_name, parse_name
from ledger_identity.events import (
    CompanyDeactivated,
    CompanyRegistered,
    CompanyUpdated,
    MembershipGranted,
    MembershipRevoked,
)
from ledger_kernel.access import Permission, Principal, SubjectId
from ledger_kernel.countries.do import TaxId
from ledger_kernel.errors import ErrorKind
from ledger_kernel.ids import CompanyId, OrganizationId, new_id
from ledger_kernel.pagination import PageRequest, page_of
from ledger_kernel.tenancy import Tenant
lazy from ledger_identity.domain.branches import Branch, EmissionPoint
lazy from ledger_identity.domain.company import Company
lazy from ledger_identity.domain.membership import Membership
lazy from ledger_identity.events import IdentityEvent
lazy from ledger_kernel.pagination import Page

NOW: Final = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)
LATER: Final = NOW + timedelta(minutes=5)
ORGANIZATION: Final = OrganizationId(new_id())
FOUNDER: Final = Principal(
    subject=SubjectId("2001"),
    organization=ORGANIZATION,
    provider_organization="1001",
    roles=frozenset({ORGANIZATION_ADMIN}),
)
STRANGER: Final = Principal(
    subject=SubjectId("9001"),
    organization=OrganizationId(new_id()),
    provider_organization="9",
    roles=frozenset({ORGANIZATION_ADMIN}),
)
OWNER: Final = FOUNDER.subject
ADMIN: Final = SubjectId("2002")
VIEWER: Final = SubjectId("3001")
RNC: Final = TaxId.parse("101850043")
CEDULA: Final = TaxId.parse("00113918205")
THEIRS: Final = TaxId.parse("401500022")
"""The tax id of the other organization's company: one taxpayer is one active company."""


def anything(version: int) -> bool:
    del version
    return True


def nothing(version: int) -> bool:
    del version
    return False


def _nothing_happens() -> None:
    """The default of ``State.while_waiting``."""


def paged[T](items: Iterable[T], request: PageRequest, key: Callable[[T], UUID]) -> Page[T]:
    """A page as a repository reads it: in id order, after the cursor, one more than asked for."""
    after = [item for item in sorted(items, key=key) if request.after is None or key(item) > request.after]
    return page_of(after[: request.size + 1], request, key=key)


@final
class State:
    """identity's state in memory: what a unit reads and changes, and what it announced."""

    def __init__(self) -> None:
        self.organizations: dict[OrganizationId, Organization] = {}
        self.companies: dict[CompanyId, Company] = {}
        self.branches: dict[BranchId, Branch] = {}
        self.points: dict[EmissionPointId, EmissionPoint] = {}
        self.memberships: dict[tuple[CompanyId, SubjectId], Membership] = {}
        self.events: list[IdentityEvent] = []
        self.while_waiting: Callable[[], None] = _nothing_happens
        """What other units commit while a unit waits for a company's lock."""
        self.races_lost = False
        """While set, every compare-and-set of a company finds it changed by someone else."""

    def unit(self, now: datetime = NOW, organization: OrganizationId = ORGANIZATION) -> IdentityUnit:
        return IdentityUnit(
            organizations=MemoryOrganizations(self),
            companies=MemoryCompanies(self, organization),
            branches=MemoryBranches(self, organization),
            emission_points=MemoryEmissionPoints(self, organization),
            memberships=MemoryMemberships(self, organization),
            events=MemoryEvents(self),
            now=now,
        )


@final
class MemoryOrganizations(Organizations):
    def __init__(self, state: State) -> None:
        self._state = state

    @override
    async def add(self, organization: Organization) -> None:
        self._state.organizations.setdefault(organization.id, organization)

    @override
    async def get(self, organization: OrganizationId) -> Organization | None:
        return self._state.organizations.get(organization)


@final
class MemoryCompanies(Companies):
    def __init__(self, state: State, organization: OrganizationId) -> None:
        self._state = state
        self._organization = organization

    @override
    async def add(self, company: Company) -> bool:
        kept = self._state.companies.values()
        if any(other.status is CompanyStatus.ACTIVE and other.tax_id == company.tax_id for other in kept):
            return False
        self._state.companies[company.id] = company
        return True

    @override
    async def get(self, company: CompanyId) -> Company | None:
        return self._mine(company)

    @override
    async def locked(self, company: CompanyId) -> Company | None:
        self._state.while_waiting()
        return self._mine(company)

    @override
    async def update(self, company: Company, *, version: int) -> bool:
        current = self._mine(company.id)
        if self._state.races_lost or current is None or current.version != version:
            return False
        self._state.companies[company.id] = company
        return True

    @override
    async def of_member(self, subject: SubjectId, request: PageRequest) -> Page[Company]:
        held = {
            company
            for (company, member), membership in self._state.memberships.items()
            if member == subject and membership.active
        }
        mine = [
            company
            for company in self._state.companies.values()
            if company.organization == self._organization and company.id in held
        ]
        return paged(mine, request, key=lambda company: company.id)

    def _mine(self, company: CompanyId) -> Company | None:
        found = self._state.companies.get(company)
        return found if found is not None and found.organization == self._organization else None


@final
class MemoryBranches(Branches):
    def __init__(self, state: State, organization: OrganizationId) -> None:
        self._state = state
        self._organization = organization

    @override
    async def add(self, branch: Branch) -> bool:
        if any(
            other.tenant == branch.tenant and other.code == branch.code
            for other in self._state.branches.values()
        ):
            return False
        self._state.branches[branch.id] = branch
        return True

    @override
    async def get(self, company: CompanyId, branch: BranchId) -> Branch | None:
        found = self._state.branches.get(branch)
        return (
            found if found is not None and found.tenant == Tenant(self._organization, company, None) else None
        )

    @override
    async def page(self, company: CompanyId, request: PageRequest) -> Page[Branch]:
        mine = [
            branch
            for branch in self._state.branches.values()
            if branch.tenant == Tenant(self._organization, company, None)
        ]
        return paged(mine, request, key=lambda branch: branch.id)


@final
class MemoryEmissionPoints(EmissionPoints):
    def __init__(self, state: State, organization: OrganizationId) -> None:
        self._state = state
        self._organization = organization

    @override
    async def add(self, point: EmissionPoint) -> bool:
        if any(
            other.branch == point.branch and other.code == point.code for other in self._state.points.values()
        ):
            return False
        self._state.points[point.id] = point
        return True

    @override
    async def get(self, company: CompanyId, point: EmissionPointId) -> EmissionPoint | None:
        found = self._state.points.get(point)
        return (
            found if found is not None and found.tenant == Tenant(self._organization, company, None) else None
        )

    @override
    async def page(self, company: CompanyId, branch: BranchId, request: PageRequest) -> Page[EmissionPoint]:
        tenant = Tenant(self._organization, company, None)
        mine = [p for p in self._state.points.values() if p.tenant == tenant and p.branch == branch]
        return paged(mine, request, key=lambda point: point.id)


@final
class MemoryMemberships(Memberships):
    def __init__(self, state: State, organization: OrganizationId) -> None:
        self._state = state
        self._organization = organization

    @override
    async def get(self, company: CompanyId, subject: SubjectId) -> Membership | None:
        found = self._state.memberships.get((company, subject))
        return found if found is not None and found.tenant.organization == self._organization else None

    @override
    async def put(self, membership: Membership) -> None:
        self._state.memberships[membership.tenant.company, membership.subject] = membership

    @override
    async def owners(self, company: CompanyId) -> int:
        return sum(1 for member in self._active(company) if member.role is Role.OWNER)

    @override
    async def page(self, company: CompanyId, request: PageRequest) -> Page[Membership]:
        return paged(self._active(company), request, key=lambda membership: membership.id)

    def _active(self, company: CompanyId) -> list[Membership]:
        return [
            m
            for (held, _), m in self._state.memberships.items()
            if held == company and m.active and m.tenant.organization == self._organization
        ]


@final
class MemoryEvents(Events):
    def __init__(self, state: State) -> None:
        self._state = state

    @override
    def publish(self, event: IdentityEvent) -> None:
        self._state.events.append(event)


@pytest.fixture
def state() -> State:
    return State()


async def created(state: State, tax_id: TaxId = RNC, founder: Principal = FOUNDER) -> Company:
    return await commands.create_company(
        state.unit(organization=founder.organization),
        founder,
        tax_id=tax_id,
        legal_name=parse_legal_name("Duarte"),
    )


async def with_an_admin(state: State, company: Company) -> None:
    """``ADMIN`` made an admin of ``company`` by its owner."""
    await commands.grant_role(state.unit(), company.id, subject=ADMIN, role=Role.ADMIN, actor=OWNER)


# ── Companies ───────────────────────────────────────────────────────────────────


async def test_a_new_company_has_its_founder_as_owner_and_both_are_announced(state: State) -> None:
    company = await created(state)
    assert state.organizations == {
        ORGANIZATION: Organization(id=ORGANIZATION, provider_id="1001", created_at=NOW)
    }
    assert state.companies == {company.id: company}
    owner = state.memberships[company.id, OWNER]
    assert owner.role is Role.OWNER
    assert [type(event) for event in state.events] == [CompanyRegistered, MembershipGranted]


@pytest.mark.parametrize(
    "roles", [frozenset[str](), frozenset({"ledger.viewer", "LEDGER.ORGANIZATION.ADMIN"})]
)
async def test_registering_a_company_needs_the_organization_s_admin_role(
    state: State, roles: frozenset[str]
) -> None:
    """A member of the organization without it (a viewer, a machine user) registers nothing."""
    member = replace(FOUNDER, roles=roles)
    with pytest.raises(OrganizationAdminRequiredError) as caught:
        await created(state, founder=member)
    assert (caught.value.kind, caught.value.code) == (ErrorKind.FORBIDDEN, "forbidden")
    assert str(caught.value) == "registering a company needs the organization role ledger.organization.admin"
    assert (state.organizations, state.companies, state.events) == ({}, {}, [])


async def test_a_second_company_of_the_organization_keeps_the_organization_as_it_was(state: State) -> None:
    await created(state)
    await commands.create_company(
        state.unit(LATER), FOUNDER, tax_id=CEDULA, legal_name=parse_legal_name("Duarte II")
    )
    assert state.organizations[ORGANIZATION].created_at == NOW
    assert len(state.companies) == 2


async def test_a_tax_id_the_organization_has_is_taken(state: State) -> None:
    await created(state)
    with pytest.raises(TaxIdTakenError):
        await created(state)
    assert len(state.events) == 2


async def test_no_organization_may_register_a_tax_id_an_active_company_holds(state: State) -> None:
    await created(state)
    with pytest.raises(TaxIdTakenError):
        await created(state, founder=STRANGER)
    assert len(state.companies) == 1


async def test_a_deactivated_company_s_tax_id_may_be_registered_again(state: State) -> None:
    company = await created(state)
    await commands.deactivate_company(state.unit(LATER), company.id, admits=anything, actor=OWNER)
    other = await created(state, founder=STRANGER)
    assert (other.organization, other.tax_id) == (STRANGER.organization, company.tax_id)


async def test_a_rename_is_a_new_version_announced(state: State) -> None:
    company = await created(state)
    renamed = await commands.rename_company(
        state.unit(LATER),
        company.id,
        legal_name=parse_legal_name("Duarte Hermanos"),
        admits=anything,
        actor=OWNER,
    )
    assert (renamed.version, state.companies[company.id]) == (2, renamed)
    assert isinstance(state.events[-1], CompanyUpdated)


async def test_a_rename_to_the_same_name_changes_and_announces_nothing(state: State) -> None:
    company = await created(state)
    same = await commands.rename_company(
        state.unit(LATER), company.id, legal_name=company.legal_name, admits=anything, actor=OWNER
    )
    assert (same, len(state.events)) == (company, 2)


async def test_a_change_the_precondition_does_not_admit_is_stale(state: State) -> None:
    company = await created(state)
    with pytest.raises(StaleVersionError):
        await commands.rename_company(
            state.unit(LATER), company.id, legal_name=parse_legal_name("X"), admits=nothing, actor=OWNER
        )
    with pytest.raises(StaleVersionError):
        await commands.deactivate_company(state.unit(LATER), company.id, admits=nothing, actor=OWNER)
    assert state.companies[company.id] == company


async def test_a_company_changed_by_someone_else_meanwhile_is_stale(state: State) -> None:
    company = await created(state)
    state.races_lost = True
    with pytest.raises(StaleVersionError):
        await commands.rename_company(
            state.unit(LATER), company.id, legal_name=parse_legal_name("X"), admits=anything, actor=OWNER
        )
    assert len(state.events) == 2


async def test_a_deactivated_company_is_announced_and_opens_nothing_more(state: State) -> None:
    company = await created(state)
    inactive = await commands.deactivate_company(state.unit(LATER), company.id, admits=anything, actor=OWNER)
    assert (inactive.status, isinstance(state.events[-1], CompanyDeactivated)) == (
        CompanyStatus.INACTIVE,
        True,
    )
    with pytest.raises(CompanyInactiveError):
        await commands.open_branch(
            state.unit(LATER), company.id, commands.Opening(parse_code("SUC1"), parse_name("S")), actor=OWNER
        )


async def test_a_company_that_does_not_exist_is_not_found(state: State) -> None:
    missing = CompanyId(new_id())
    with pytest.raises(CompanyNotFoundError):
        await commands.rename_company(
            state.unit(), missing, legal_name=parse_legal_name("X"), admits=anything, actor=OWNER
        )
    with pytest.raises(CompanyNotFoundError):
        await queries.company(state.unit(), missing)


# ── Branches and emission points ───────────────────────────────────────────────


async def test_branches_and_emission_points_are_opened_once_per_code(state: State) -> None:
    company = await created(state)
    branch = await commands.open_branch(
        state.unit(), company.id, commands.Opening(parse_code("SUC1"), parse_name("Santiago")), actor=OWNER
    )
    with pytest.raises(BranchCodeTakenError):
        await commands.open_branch(
            state.unit(), company.id, commands.Opening(parse_code("SUC1"), parse_name("Other")), actor=OWNER
        )
    point = await commands.open_emission_point(
        state.unit(),
        company.id,
        branch.id,
        commands.Opening(parse_code("001"), parse_name("Caja")),
        actor=OWNER,
    )
    with pytest.raises(EmissionPointCodeTakenError):
        await commands.open_emission_point(
            state.unit(),
            company.id,
            branch.id,
            commands.Opening(parse_code("001"), parse_name("Caja")),
            actor=OWNER,
        )
    assert await queries.branch(state.unit(), company.id, branch.id) == branch
    assert await queries.emission_point(state.unit(), company.id, branch.id, point.id) == point
    assert (await queries.branches(state.unit(), company.id, PageRequest())).items == (branch,)
    assert (await queries.emission_points(state.unit(), company.id, branch.id, PageRequest())).items == (
        point,
    )


async def test_a_company_deactivated_while_a_branch_waits_for_its_lock_opens_nothing(state: State) -> None:
    company = await created(state)
    branch = await commands.open_branch(
        state.unit(), company.id, commands.Opening(parse_code("SUC1"), parse_name("A")), actor=OWNER
    )
    state.while_waiting = deactivate(state, company)
    with pytest.raises(CompanyInactiveError):
        await commands.open_branch(
            state.unit(), company.id, commands.Opening(parse_code("SUC2"), parse_name("B")), actor=OWNER
        )
    with pytest.raises(CompanyInactiveError):
        await commands.open_emission_point(
            state.unit(),
            company.id,
            branch.id,
            commands.Opening(parse_code("001"), parse_name("Caja")),
            actor=OWNER,
        )
    assert (len(state.branches), len(state.points)) == (1, 0)


async def test_an_emission_point_opens_in_a_branch_that_exists_only(state: State) -> None:
    company = await created(state)
    with pytest.raises(BranchNotFoundError):
        await commands.open_emission_point(
            state.unit(),
            company.id,
            BranchId(new_id()),
            commands.Opening(parse_code("001"), parse_name("Caja")),
            actor=OWNER,
        )
    with pytest.raises(BranchNotFoundError):
        await queries.emission_points(state.unit(), company.id, BranchId(new_id()), PageRequest())
    with pytest.raises(BranchNotFoundError):
        await queries.branch(state.unit(), company.id, BranchId(new_id()))


async def test_an_emission_point_is_found_under_its_own_branch_only(state: State) -> None:
    company = await created(state)
    first = await commands.open_branch(
        state.unit(), company.id, commands.Opening(parse_code("SUC1"), parse_name("A")), actor=OWNER
    )
    second = await commands.open_branch(
        state.unit(), company.id, commands.Opening(parse_code("SUC2"), parse_name("B")), actor=OWNER
    )
    point = await commands.open_emission_point(
        state.unit(),
        company.id,
        first.id,
        commands.Opening(parse_code("001"), parse_name("Caja")),
        actor=OWNER,
    )
    with pytest.raises(EmissionPointNotFoundError):
        await queries.emission_point(state.unit(), company.id, second.id, point.id)
    with pytest.raises(EmissionPointNotFoundError):
        await queries.emission_point(state.unit(), company.id, first.id, EmissionPointId(new_id()))


# ── Memberships ─────────────────────────────────────────────────────────────────


async def test_granting_a_role_creates_a_membership_or_changes_it(state: State) -> None:
    company = await created(state)
    await with_an_admin(state, company)
    first = await commands.grant_role(state.unit(), company.id, subject=VIEWER, role=Role.VIEWER, actor=ADMIN)
    again = await commands.grant_role(
        state.unit(LATER), company.id, subject=VIEWER, role=Role.VIEWER, actor=ADMIN
    )
    changed = await commands.grant_role(
        state.unit(LATER), company.id, subject=VIEWER, role=Role.ISSUER, actor=ADMIN
    )
    assert (first.created, again.created, changed.created) == (True, False, False)
    assert (again.membership, changed.membership.role) == (first.membership, Role.ISSUER)
    grants = [event for event in state.events if isinstance(event, MembershipGranted)]
    assert [(event.role, event.previous) for event in grants] == [
        ("owner", None),
        ("admin", None),
        ("viewer", None),
        ("issuer", "viewer"),
    ]


async def test_revoking_a_role_keeps_the_membership_revoked_and_announces_it(state: State) -> None:
    company = await created(state)
    await commands.grant_role(state.unit(), company.id, subject=VIEWER, role=Role.VIEWER, actor=OWNER)
    await commands.revoke_role(state.unit(LATER), company.id, subject=VIEWER, actor=OWNER)
    revoked = state.memberships[company.id, VIEWER]
    assert (revoked.active, revoked.version, revoked.changed_at, revoked.changed_by) == (
        False,
        2,
        LATER,
        OWNER,
    )
    assert isinstance(state.events[-1], MembershipRevoked)
    assert await queries.permissions_of(state.unit(), company.id, VIEWER) == frozenset()
    listed = await queries.memberships(state.unit(), company.id, PageRequest())
    assert [membership.subject for membership in listed.items] == [OWNER]
    assert (await queries.companies_of(state.unit(), VIEWER, PageRequest())).items == ()


async def test_revoking_a_revoked_role_again_changes_and_announces_nothing(state: State) -> None:
    company = await created(state)
    await commands.grant_role(state.unit(), company.id, subject=VIEWER, role=Role.VIEWER, actor=OWNER)
    await commands.revoke_role(state.unit(), company.id, subject=VIEWER, actor=OWNER)
    revoked, announced = state.memberships[company.id, VIEWER], len(state.events)
    await commands.revoke_role(state.unit(LATER), company.id, subject=VIEWER, actor=OWNER)
    assert (state.memberships[company.id, VIEWER], len(state.events)) == (revoked, announced)
    assert [type(event) for event in state.events].count(MembershipRevoked) == 1
    with pytest.raises(MembershipNotFoundError):
        await commands.revoke_role(state.unit(), company.id, subject=SubjectId("4004"), actor=OWNER)


async def test_a_role_granted_again_continues_the_membership_s_versions(state: State) -> None:
    company = await created(state)
    await commands.grant_role(state.unit(), company.id, subject=VIEWER, role=Role.VIEWER, actor=OWNER)
    await commands.revoke_role(state.unit(), company.id, subject=VIEWER, actor=OWNER)
    again = await commands.grant_role(
        state.unit(LATER), company.id, subject=VIEWER, role=Role.ISSUER, actor=OWNER
    )
    assert (again.created, again.membership.version) == (True, 3)
    versions = [
        event.aggregate_version
        for event in state.events
        if isinstance(event, MembershipGranted | MembershipRevoked) and event.member == VIEWER
    ]
    assert versions == [1, 2, 3]


async def test_the_founder_cannot_leave_the_company_ownerless(state: State) -> None:
    company = await created(state)
    with pytest.raises(LastOwnerError):
        await commands.revoke_role(state.unit(), company.id, subject=OWNER, actor=OWNER)
    with pytest.raises(LastOwnerError):
        await commands.grant_role(state.unit(LATER), company.id, subject=OWNER, role=Role.ADMIN, actor=OWNER)


async def test_the_actor_acts_with_the_role_it_holds_when_the_change_is_made(state: State) -> None:
    company = await created(state)
    await with_an_admin(state, company)
    await commands.grant_role(state.unit(), company.id, subject=ADMIN, role=Role.VIEWER, actor=OWNER)
    with pytest.raises(ActorNotAllowedError):
        await commands.grant_role(state.unit(), company.id, subject=VIEWER, role=Role.VIEWER, actor=ADMIN)
    await commands.grant_role(state.unit(), company.id, subject=ADMIN, role=Role.ADMIN, actor=OWNER)
    with pytest.raises(OwnersOnlyError):
        await commands.grant_role(state.unit(), company.id, subject=ADMIN, role=Role.OWNER, actor=ADMIN)
    await commands.revoke_role(state.unit(), company.id, subject=ADMIN, actor=OWNER)
    with pytest.raises(CompanyNotFoundError):
        await commands.revoke_role(state.unit(), company.id, subject=OWNER, actor=ADMIN)
    with pytest.raises(CompanyNotFoundError):
        await commands.grant_role(
            state.unit(), company.id, subject=VIEWER, role=Role.VIEWER, actor=SubjectId("9999")
        )


def deactivate(state: State, company: Company) -> Callable[[], None]:
    """The deactivation of ``company`` that commits while another unit waits for its lock."""

    def commit() -> None:
        current = state.companies[company.id]
        if current.status is CompanyStatus.ACTIVE:
            state.companies[company.id], _ = current.deactivate(now=LATER)

    return commit


def demote(state: State, company: Company, subject: SubjectId, role: Role) -> Callable[[], None]:
    """A change of ``subject``'s role that commits while another unit waits for the company's lock."""

    def commit() -> None:
        key = (company.id, subject)
        state.memberships[key] = replace(state.memberships[key], role=role)

    return commit


async def test_an_admin_demoted_while_its_grant_waits_for_the_company_is_refused(state: State) -> None:
    company = await created(state)
    await with_an_admin(state, company)
    state.while_waiting = demote(state, company, ADMIN, Role.VIEWER)
    with pytest.raises(ActorNotAllowedError):
        await commands.grant_role(state.unit(), company.id, subject=VIEWER, role=Role.VIEWER, actor=ADMIN)
    with pytest.raises(ActorNotAllowedError):
        await commands.revoke_role(state.unit(), company.id, subject=OWNER, actor=ADMIN)
    assert (company.id, VIEWER) not in state.memberships


async def test_an_owner_demoted_while_its_grant_of_ownership_waits_is_refused(state: State) -> None:
    company = await created(state)
    state.while_waiting = demote(state, company, OWNER, Role.ADMIN)
    with pytest.raises(OwnersOnlyError):
        await commands.grant_role(state.unit(), company.id, subject=ADMIN, role=Role.OWNER, actor=OWNER)
    assert (company.id, ADMIN) not in state.memberships


async def test_an_admin_demoted_while_its_change_of_the_company_waits_is_refused(state: State) -> None:
    company = await created(state)
    await with_an_admin(state, company)
    state.while_waiting = demote(state, company, ADMIN, Role.VIEWER)
    with pytest.raises(ActorNotAllowedError):
        await commands.deactivate_company(state.unit(LATER), company.id, admits=anything, actor=ADMIN)
    with pytest.raises(ActorNotAllowedError):
        await commands.rename_company(
            state.unit(LATER), company.id, legal_name=parse_legal_name("X"), admits=anything, actor=ADMIN
        )
    assert (state.companies[company.id], len(state.events)) == (company, 3)


async def test_a_company_is_changed_by_an_actor_with_an_active_role_there_only(state: State) -> None:
    company = await created(state)
    await with_an_admin(state, company)
    await commands.revoke_role(state.unit(), company.id, subject=ADMIN, actor=OWNER)
    with pytest.raises(CompanyNotFoundError):
        await commands.deactivate_company(state.unit(LATER), company.id, admits=anything, actor=ADMIN)
    with pytest.raises(CompanyNotFoundError):
        await commands.rename_company(
            state.unit(LATER), company.id, legal_name=parse_legal_name("X"), admits=anything, actor=VIEWER
        )
    assert state.companies[company.id] == company


async def test_a_refused_actor_is_refused_whatever_version_it_names(state: State) -> None:
    company = await created(state)
    await commands.grant_role(state.unit(), company.id, subject=VIEWER, role=Role.VIEWER, actor=OWNER)
    with pytest.raises(ActorNotAllowedError):
        await commands.deactivate_company(state.unit(LATER), company.id, admits=nothing, actor=VIEWER)


async def test_an_admin_demoted_while_its_opening_waits_for_the_company_opens_nothing(state: State) -> None:
    company = await created(state)
    branch = await commands.open_branch(
        state.unit(), company.id, commands.Opening(parse_code("SUC1"), parse_name("A")), actor=OWNER
    )
    await with_an_admin(state, company)
    state.while_waiting = demote(state, company, ADMIN, Role.VIEWER)
    with pytest.raises(ActorNotAllowedError):
        await commands.open_branch(
            state.unit(), company.id, commands.Opening(parse_code("SUC2"), parse_name("B")), actor=ADMIN
        )
    with pytest.raises(ActorNotAllowedError):
        await commands.open_emission_point(
            state.unit(),
            company.id,
            branch.id,
            commands.Opening(parse_code("001"), parse_name("Caja")),
            actor=ADMIN,
        )
    assert (len(state.branches), len(state.points)) == (1, 0)


async def test_establishments_open_for_an_actor_with_an_active_role_only(state: State) -> None:
    company = await created(state)
    branch = await commands.open_branch(
        state.unit(), company.id, commands.Opening(parse_code("SUC1"), parse_name("A")), actor=OWNER
    )
    with pytest.raises(CompanyNotFoundError):
        await commands.open_branch(
            state.unit(), company.id, commands.Opening(parse_code("SUC2"), parse_name("B")), actor=VIEWER
        )
    with pytest.raises(CompanyNotFoundError):
        await commands.open_emission_point(
            state.unit(),
            company.id,
            branch.id,
            commands.Opening(parse_code("001"), parse_name("Caja")),
            actor=VIEWER,
        )
    assert (len(state.branches), len(state.points)) == (1, 0)


async def test_a_deactivated_company_grants_no_role_but_still_revokes(state: State) -> None:
    company = await created(state)
    await commands.grant_role(state.unit(), company.id, subject=VIEWER, role=Role.VIEWER, actor=OWNER)
    await commands.deactivate_company(state.unit(LATER), company.id, admits=anything, actor=OWNER)
    with pytest.raises(CompanyInactiveError):
        await commands.grant_role(state.unit(LATER), company.id, subject=ADMIN, role=Role.ADMIN, actor=OWNER)
    await commands.revoke_role(state.unit(LATER), company.id, subject=VIEWER, actor=OWNER)
    assert not state.memberships[company.id, VIEWER].active


async def test_another_organizations_unit_sees_nothing_of_the_company(state: State) -> None:
    company = await created(state)
    branch = await commands.open_branch(
        state.unit(), company.id, commands.Opening(parse_code("SUC1"), parse_name("A")), actor=OWNER
    )
    outsider = state.unit(organization=STRANGER.organization)
    with pytest.raises(CompanyNotFoundError):
        await queries.company(outsider, company.id)
    with pytest.raises(CompanyNotFoundError):
        await commands.open_branch(
            outsider, company.id, commands.Opening(parse_code("SUC2"), parse_name("B")), actor=OWNER
        )
    with pytest.raises(CompanyNotFoundError):
        await commands.grant_role(outsider, company.id, subject=VIEWER, role=Role.VIEWER, actor=OWNER)
    with pytest.raises(CompanyNotFoundError):
        await commands.rename_company(
            outsider, company.id, legal_name=parse_legal_name("X"), admits=anything, actor=OWNER
        )
    with pytest.raises(BranchNotFoundError):
        await queries.branch(outsider, company.id, branch.id)
    assert await queries.permissions_of(outsider, company.id, OWNER) == frozenset()
    assert (await queries.memberships(outsider, company.id, PageRequest())).items == ()
    assert (await queries.branches(outsider, company.id, PageRequest())).items == ()


async def test_memberships_of_a_company_that_does_not_exist_are_not_found(state: State) -> None:
    with pytest.raises(CompanyNotFoundError):
        await commands.grant_role(
            state.unit(), CompanyId(new_id()), subject=VIEWER, role=Role.VIEWER, actor=OWNER
        )


# ── Queries ─────────────────────────────────────────────────────────────────────


async def test_the_organization_is_known_once_it_has_a_company(state: State) -> None:
    with pytest.raises(OrganizationNotFoundError):
        await queries.organization(state.unit(), ORGANIZATION)
    await created(state)
    assert (await queries.organization(state.unit(), ORGANIZATION)).provider_id == "1001"


async def test_a_principal_lists_the_companies_it_holds_a_role_in_page_by_page(state: State) -> None:
    mine = [await created(state, TaxId.parse(rnc)) for rnc in ("101850043", "130000012", "401007551")]
    theirs = await created(state, THEIRS, founder=STRANGER)
    first = await queries.companies_of(state.unit(), OWNER, PageRequest(size=2))
    assert first.items == tuple(sorted(mine, key=lambda company: company.id)[:2])
    assert first.next_cursor is not None
    rest = await queries.companies_of(state.unit(), OWNER, PageRequest(size=2, after=first.items[-1].id))
    assert (len(rest.items), rest.next_cursor) == (1, None)
    strangers = state.unit(organization=STRANGER.organization)
    assert (await queries.companies_of(strangers, STRANGER.subject, PageRequest())).items == (theirs,)


async def test_permissions_are_the_role_s_or_none(state: State) -> None:
    company = await created(state)
    assert await queries.permissions_of(state.unit(), company.id, OWNER) == frozenset(Permission)
    assert await queries.permissions_of(state.unit(), company.id, VIEWER) == frozenset()


async def test_other_services_get_the_reading_permissions_only_of_a_deactivated_company(state: State) -> None:
    company = await created(state)
    await commands.deactivate_company(state.unit(LATER), company.id, admits=anything, actor=OWNER)
    assert await queries.permissions_of(state.unit(), company.id, OWNER) == READ_ONLY
    assert await queries.permissions_of(state.unit(), CompanyId(new_id()), OWNER) == frozenset()


async def test_the_memberships_of_a_company_are_listed(state: State) -> None:
    company = await created(state)
    await commands.grant_role(state.unit(), company.id, subject=VIEWER, role=Role.VIEWER, actor=OWNER)
    page = await queries.memberships(state.unit(), company.id, PageRequest())
    assert {membership.subject for membership in page.items} == {"2001", "3001"}
