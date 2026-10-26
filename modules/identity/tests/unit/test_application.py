"""The identity use cases over in-memory ports: what they change, announce and refuse."""

from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Final, final, override
lazy from collections.abc import Callable, Iterable, Mapping
lazy from uuid import UUID

import pytest

from ledger_identity.application import commands, queries
from ledger_identity.application.ports import (
    ApiClients,
    Audit,
    Companies,
    Establishments,
    Events,
    IdentityUnit,
    Memberships,
    Organizations,
)
from ledger_identity.domain.api_clients import ApiClientSpec
from ledger_identity.domain.company import CompanyStatus, FiscalProfile
from ledger_identity.domain.errors import (
    AddressCodeUnknownError,
    ApiClientExistsError,
    ApiClientNotFoundError,
    CompanyInactiveError,
    EstablishmentCodeTakenError,
    EstablishmentNotFoundError,
    InvalidNameError,
    JurisdictionUnsupportedError,
    LastOwnerError,
    MembershipNotFoundError,
    OrganizationAdminRequiredError,
    OrganizationNotFoundError,
    OwnersOnlyError,
    StaleVersionError,
    TaxIdTakenError,
)
from ledger_identity.domain.organization import ORGANIZATION_ADMIN, Organization
from ledger_identity.domain.roles import EVERYTHING, only_reads
from ledger_identity.domain.text import parse_code, parse_legal_name, parse_name, parse_time_zone
from ledger_identity.events import (
    CompanyDeactivated,
    CompanyRegistered,
    CompanyUpdated,
    MembershipGranted,
    MembershipRevoked,
)
from ledger_kernel.access import (
    CompanyNotFoundError,
    PermissionDeniedError,
    Principal,
    PrincipalKind,
    Role,
    SubjectId,
)
from ledger_kernel.addresses import AddressDef, FiscalAddress
from ledger_kernel.errors import ErrorKind
from ledger_kernel.ids import CompanyId, OrganizationId, new_id
from ledger_kernel.jurisdiction import parse_jurisdiction
from ledger_kernel.money import Currency, RoundingMode
from ledger_kernel.pagination import PageRequest, decode_cursor, page_of
from ledger_kernel.regimes import Locale, RegimeDef, RegimeKey, RoundingRule, Source
from ledger_kernel.tax_ids import TaxId, TaxIdScheme
from ledger_kernel.tenancy import Mode, Tenant
lazy from ledger_identity.application.ports import AuditAction
lazy from ledger_identity.domain.api_clients import ApiClient
lazy from ledger_identity.domain.company import Company
lazy from ledger_identity.domain.establishments import Establishment
lazy from ledger_identity.domain.ids import ApiClientId, EstablishmentId
lazy from ledger_identity.domain.membership import Membership
lazy from ledger_identity.domain.text import Code, LegalName
lazy from ledger_identity.events import IdentityEvent
lazy from ledger_kernel.jurisdiction import Jurisdiction
lazy from ledger_kernel.pagination import Page

NOW: Final = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)
LATER: Final = NOW + timedelta(minutes=5)
ORGANIZATION: Final = OrganizationId(new_id())
FOUNDER: Final = Principal(
    subject=SubjectId("2001"),
    organization=ORGANIZATION,
    provider_organization="1001",
    roles=frozenset({ORGANIZATION_ADMIN}),
    kind=PrincipalKind.HUMAN,
)
STRANGER: Final = Principal(
    subject=SubjectId("9001"),
    organization=OrganizationId(new_id()),
    provider_organization="9",
    roles=frozenset({ORGANIZATION_ADMIN}),
    kind=PrincipalKind.HUMAN,
)
NOT_ADMIN: Final = Principal(
    subject=SubjectId("2002"),
    organization=ORGANIZATION,
    provider_organization="1001",
    kind=PrincipalKind.HUMAN,
)
OWNER: Final = FOUNDER.subject
ADMIN: Final = SubjectId("2002")
VIEWER: Final = SubjectId("3001")
SANTO_DOMINGO: Final = parse_time_zone("America/Santo_Domingo")
VERSION: Final = date(2026, 10, 13)
"""The API version an organization is pinned to when it is first seen."""
RNC: Final = TaxId(TaxIdScheme("do.rnc"), "101850043")
CEDULA: Final = TaxId(TaxIdScheme("do.cedula"), "00113918205")
THEIRS: Final = TaxId(TaxIdScheme("do.rnc"), "401500022")
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
        self.establishments: dict[EstablishmentId, Establishment] = {}
        self.memberships: dict[tuple[CompanyId, SubjectId], Membership] = {}
        self.api_clients: dict[ApiClientId, ApiClient] = {}
        self.events: list[IdentityEvent] = []
        self.audited: list[Audited] = []
        self.while_waiting: Callable[[], None] = _nothing_happens
        """What other units commit while a unit waits for a company's lock."""
        self.races_lost = False
        """While set, every compare-and-set of a company finds it changed by someone else."""
        self.client_races_lost = False
        """While set, an API client is found revoked already by the unit that revokes it."""

    def unit(
        self, now: datetime = NOW, organization: OrganizationId = ORGANIZATION, mode: Mode | None = Mode.TEST
    ) -> IdentityUnit:
        return IdentityUnit(
            organizations=MemoryOrganizations(self),
            companies=MemoryCompanies(self, organization),
            establishments=MemoryEstablishments(self, organization),
            memberships=MemoryMemberships(self, organization),
            api_clients=MemoryApiClients(self, organization, mode),
            events=MemoryEvents(self),
            audit=MemoryAudit(self),
            now=now,
        )


@final
@dataclass(frozen=True, slots=True)
class Audited:
    """One change a unit recorded: what was done to which subject, from what to what, with which ids."""

    action: AuditAction
    subject: UUID
    before: object
    after: object
    detail: Mapping[str, str]


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
        taken = {
            tax_id
            for other in kept
            if other.status is CompanyStatus.ACTIVE
            for tax_id in other.profile.tax_ids
        }
        if taken & set(company.profile.tax_ids):
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
    async def retax(self, company: Company) -> bool:
        taken = {
            tax_id
            for other in self._state.companies.values()
            if other.id != company.id and other.status is CompanyStatus.ACTIVE
            for tax_id in other.profile.tax_ids
        }
        return not taken & set(company.profile.tax_ids)

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
class MemoryEstablishments(Establishments):
    def __init__(self, state: State, organization: OrganizationId) -> None:
        self._state = state
        self._organization = organization

    @override
    async def add(self, establishment: Establishment) -> bool:
        if any(
            other.tenant == establishment.tenant and other.code == establishment.code
            for other in self._state.establishments.values()
        ):
            return False
        self._state.establishments[establishment.id] = establishment
        return True

    @override
    async def get(self, company: CompanyId, establishment: EstablishmentId) -> Establishment | None:
        found = self._state.establishments.get(establishment)
        return found if found is not None and found.tenant == self._tenant(company) else None

    @override
    async def by_code(self, company: CompanyId, code: Code) -> Establishment | None:
        mine = (e for e in self._state.establishments.values() if e.tenant == self._tenant(company))
        return next((e for e in mine if e.code == code), None)

    @override
    async def page(self, company: CompanyId, request: PageRequest) -> Page[Establishment]:
        mine = [e for e in self._state.establishments.values() if e.tenant == self._tenant(company)]
        return paged(mine, request, key=lambda establishment: establishment.id)

    @override
    async def update(self, establishment: Establishment) -> None:
        self._state.establishments[establishment.id] = establishment

    def _tenant(self, company: CompanyId) -> Tenant:
        return Tenant(self._organization, company, None)


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
class MemoryApiClients(ApiClients):
    def __init__(self, state: State, organization: OrganizationId, mode: Mode | None) -> None:
        self._state = state
        self._organization = organization
        self._mode = mode

    @override
    async def add(self, client: ApiClient) -> bool:
        if any(
            other.organization == client.organization and other.subject == client.subject and other.active
            for other in self._state.api_clients.values()
        ):
            return False
        self._state.api_clients[client.id] = client
        return True

    @override
    async def get(self, client: ApiClientId) -> ApiClient | None:
        return next((found for found in self._mine() if found.id == client), None)

    @override
    async def revoked(self, client: ApiClient) -> bool:
        current = self._state.api_clients.get(client.id)
        if self._state.client_races_lost or current is None or not current.active:
            return False
        self._state.api_clients[client.id] = client
        return True

    @override
    async def page(self, request: PageRequest) -> Page[ApiClient]:
        return paged(self._mine(), request, key=lambda client: client.id)

    def _mine(self) -> list[ApiClient]:
        return [
            client
            for client in self._state.api_clients.values()
            if client.organization == self._organization and client.mode is self._mode
        ]


@final
class MemoryEvents(Events):
    def __init__(self, state: State) -> None:
        self._state = state

    @override
    def publish(self, event: IdentityEvent) -> None:
        self._state.events.append(event)


@final
class MemoryAudit(Audit):
    def __init__(self, state: State) -> None:
        self._state = state

    @override
    def record(
        self,
        action: AuditAction,
        subject: UUID,
        *,
        before: object,
        after: object,
        detail: Mapping[str, str] | None = None,
    ) -> None:
        self._state.audited.append(Audited(action, subject, before, after, dict(detail or {})))


@pytest.fixture
def state() -> State:
    return State()


def profile(*tax_ids: TaxId) -> FiscalProfile:
    return FiscalProfile(
        jurisdiction=parse_jurisdiction("DO"), functional_currency=Currency.of("DOP"), tax_ids=tax_ids
    )


def registration(*tax_ids: TaxId, legal_name: str = "Duarte") -> commands.Registration:
    return commands.Registration(
        profile=profile(*tax_ids),
        legal_name=parse_legal_name(legal_name),
        time_zone=SANTO_DOMINGO,
        api_version=VERSION,
    )


async def created(state: State, tax_id: TaxId = RNC, founder: Principal = FOUNDER) -> Company:
    return await commands.create_company(
        state.unit(organization=founder.organization), founder, registration(tax_id)
    )


async def with_an_admin(state: State, company: Company) -> None:
    """``ADMIN`` made an admin of ``company`` by its owner."""
    await commands.grant_role(state.unit(), company.id, subject=ADMIN, role=Role.ADMIN, actor=OWNER)


# ── Companies ───────────────────────────────────────────────────────────────────


async def test_a_new_company_has_its_founder_as_owner_and_both_are_announced(state: State) -> None:
    company = await created(state)
    assert state.organizations == {
        ORGANIZATION: Organization(id=ORGANIZATION, provider_id="1001", created_at=NOW, api_version=VERSION)
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
    await commands.create_company(state.unit(LATER), FOUNDER, registration(CEDULA, legal_name="Duarte II"))
    assert state.organizations[ORGANIZATION].created_at == NOW
    assert state.organizations[ORGANIZATION].api_version == VERSION
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


async def test_any_one_of_a_company_s_tax_ids_that_is_taken_refuses_the_company(state: State) -> None:
    await created(state)
    with pytest.raises(TaxIdTakenError):
        await commands.create_company(
            state.unit(LATER),
            STRANGER,
            registration(CEDULA, RNC, legal_name="Duarte II"),  # the cédula is free; the RNC is held
        )
    assert len(state.companies) == 1
    both = await commands.create_company(
        state.unit(LATER), STRANGER, registration(CEDULA, THEIRS, legal_name="Duarte II")
    )
    assert both.profile.tax_ids == (CEDULA, THEIRS)


async def test_a_deactivated_company_s_tax_id_may_be_registered_again(state: State) -> None:
    company = await created(state)
    await commands.deactivate_company(state.unit(LATER), company.id, admits=anything, actor=OWNER)
    other = await created(state, founder=STRANGER)
    assert (other.organization, other.profile) == (STRANGER.organization, company.profile)


def no_regime(jurisdiction: Jurisdiction) -> RegimeDef | None:
    """What judges a profile where no regime is installed: nothing."""
    del jurisdiction
    return None


def renaming(legal_name: LegalName) -> commands.Changes:
    """A change of the name alone: nothing judges a profile, none changes."""
    return commands.Changes(regime_of=no_regime, legal_name=legal_name)


async def test_a_profile_no_installed_regime_governs_is_refused_and_nothing_changes(state: State) -> None:
    company = await created(state)
    abroad = commands.Changes(regime_of=no_regime, jurisdiction=parse_jurisdiction("ES"))
    with pytest.raises(JurisdictionUnsupportedError):
        await commands.change_company(state.unit(LATER), company.id, abroad, admits=anything, actor=OWNER)
    assert (state.companies[company.id], len(state.events)) == (company, 2)


async def test_a_rename_is_a_new_version_announced(state: State) -> None:
    company = await created(state)
    renamed = await commands.change_company(
        state.unit(LATER),
        company.id,
        renaming(parse_legal_name("Duarte Hermanos")),
        admits=anything,
        actor=OWNER,
    )
    assert (renamed.version, state.companies[company.id]) == (2, renamed)
    assert isinstance(state.events[-1], CompanyUpdated)


async def test_a_rename_to_the_same_name_changes_and_announces_nothing(state: State) -> None:
    company = await created(state)
    same = await commands.change_company(
        state.unit(LATER), company.id, renaming(company.legal_name), admits=anything, actor=OWNER
    )
    assert (same, len(state.events)) == (company, 2)


async def test_a_change_the_precondition_does_not_admit_is_stale(state: State) -> None:
    company = await created(state)
    with pytest.raises(StaleVersionError):
        await commands.change_company(
            state.unit(LATER), company.id, renaming(parse_legal_name("X")), admits=nothing, actor=OWNER
        )
    with pytest.raises(StaleVersionError):
        await commands.deactivate_company(state.unit(LATER), company.id, admits=nothing, actor=OWNER)
    assert state.companies[company.id] == company


async def test_a_company_changed_by_someone_else_meanwhile_is_stale(state: State) -> None:
    company = await created(state)
    state.races_lost = True
    with pytest.raises(StaleVersionError):
        await commands.change_company(
            state.unit(LATER), company.id, renaming(parse_legal_name("X")), admits=anything, actor=OWNER
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
        await commands.open_establishment(
            state.unit(LATER),
            company.id,
            commands.Opening(parse_code("SUC1"), parse_name("S"), None),
            actor=OWNER,
        )


async def test_a_company_that_does_not_exist_is_not_found(state: State) -> None:
    missing = CompanyId(new_id())
    with pytest.raises(CompanyNotFoundError):
        await commands.change_company(
            state.unit(), missing, renaming(parse_legal_name("X")), admits=anything, actor=OWNER
        )
    with pytest.raises(CompanyNotFoundError):
        await queries.company(state.unit(), missing)


# ── The fiscal address (Task 35) ────────────────────────────────────────────────

CATALOGUE: Final = AddressDef(
    max_line=100, municipalities=frozenset({"010100"}), provinces=frozenset({"010000"})
)
ADDRESS: Final = FiscalAddress(line="Av. Duarte 10", municipality="010100", province="010000")


def catalogued(jurisdiction: Jurisdiction) -> RegimeDef:
    """A regime of ``jurisdiction`` that keeps a catalogue of codes (as DO's does), and nothing else."""
    return RegimeDef(
        key=RegimeKey(jurisdiction.country.lower()),
        jurisdiction=jurisdiction,
        currency=Currency.of("DOP"),
        time_zone="America/Santo_Domingo",
        labels={Locale("es"): jurisdiction.country},
        tax_id_schemes=(),
        tax_categories=(),
        document_types=(),
        rounding=RoundingRule(
            mode=RoundingMode.HALF_UP, points=frozenset(), places=2, line_tolerance=Decimal("0.01")
        ),
        fields=(),
        parameters={},
        sources=(Source(title="Law", url="https://law.invalid", version="1", retrieved_on=date(2026, 1, 1)),),
        address=CATALOGUE,
    )


def narrow(jurisdiction: Jurisdiction) -> RegimeDef:
    """A regime of ``jurisdiction`` that takes foreign tax ids and an address line of 5 characters at most."""
    return replace(catalogued(jurisdiction), address=AddressDef(max_line=5), foreign_tax_ids=True)


def moving(address: FiscalAddress | None, regime_of: commands.RegimeOf = catalogued) -> commands.Changes:
    """A change of the address alone, judged by ``regime_of``."""
    return commands.Changes(regime_of=regime_of, address=address)


async def test_a_company_is_registered_at_the_address_its_registration_states(state: State) -> None:
    located = replace(registration(RNC), address=ADDRESS)
    company = await commands.create_company(state.unit(), FOUNDER, located)
    assert state.companies[company.id].address == ADDRESS


async def test_a_new_address_is_a_new_version_announced_and_audited(state: State) -> None:
    company = await created(state)
    moved = await commands.change_company(
        state.unit(LATER), company.id, moving(ADDRESS), admits=anything, actor=OWNER
    )
    assert (moved.address, moved.version, state.companies[company.id]) == (ADDRESS, 2, moved)
    assert state.events[-1] == moved.updated()
    assert state.audited[-1] == Audited("company.change_address", company.id, company, moved, {})
    cleared = await commands.change_company(
        state.unit(LATER), company.id, moving(None), admits=anything, actor=OWNER
    )
    assert (cleared.address, cleared.version) == (None, 3)
    again = await commands.change_company(
        state.unit(LATER), company.id, moving(None), admits=anything, actor=OWNER
    )
    assert again == cleared  # so already: nothing changes, nothing is recorded
    assert [entry.action for entry in state.audited[3:]] == ["company.change_address"] * 2


async def test_a_rename_with_a_new_address_is_one_version_audited_for_each(state: State) -> None:
    company = await created(state)
    both = replace(moving(ADDRESS), legal_name=parse_legal_name("Duarte Hermanos"))
    changed = await commands.change_company(state.unit(LATER), company.id, both, admits=anything, actor=OWNER)
    assert (changed.legal_name, changed.address, changed.version) == ("Duarte Hermanos", ADDRESS, 2)
    assert [entry.action for entry in state.audited[3:]] == ["company.rename", "company.change_address"]


async def test_an_address_the_regime_does_not_take_is_refused_and_nothing_changes(state: State) -> None:
    company = await created(state)
    unknown = moving(replace(ADDRESS, province="999999"))
    with pytest.raises(AddressCodeUnknownError):
        await commands.change_company(state.unit(LATER), company.id, unknown, admits=anything, actor=OWNER)
    uninstalled = moving(ADDRESS, no_regime)  # no regime installed: a line, no code
    with pytest.raises(AddressCodeUnknownError):
        await commands.change_company(
            state.unit(LATER), company.id, uninstalled, admits=anything, actor=OWNER
        )
    assert (state.companies[company.id], len(state.events), len(state.audited)) == (company, 2, 3)


async def test_an_address_kept_is_judged_again_by_the_regime_of_a_new_jurisdiction(state: State) -> None:
    located = replace(registration(RNC), address=ADDRESS)
    company = await commands.create_company(state.unit(), FOUNDER, located)
    abroad = commands.Changes(
        regime_of=narrow, jurisdiction=parse_jurisdiction("US"), time_zone=parse_time_zone("America/New_York")
    )
    with pytest.raises(InvalidNameError, match=r"^address\.line is longer than 5 characters$"):
        await commands.change_company(state.unit(LATER), company.id, abroad, admits=anything, actor=OWNER)
    assert state.companies[company.id] == company


# ── Audit ───────────────────────────────────────────────────────────────────────


async def test_a_new_company_and_its_owner_are_audited_as_created(state: State) -> None:
    company = await created(state)
    owner = state.memberships[company.id, OWNER]
    (main,) = state.establishments.values()
    assert state.audited == [
        Audited("company.register", company.id, None, company, {}),
        Audited("membership.grant", owner.id, None, owner, {"member": OWNER, "role": "owner"}),
        Audited(
            "establishment.open",
            main.id,
            None,
            main,
            {"code": "MAIN", "time_zone": "America/Santo_Domingo"},
        ),
    ]


async def test_a_change_of_a_company_is_audited_from_what_it_was_to_what_it_is(state: State) -> None:
    company = await created(state)
    renamed = await commands.change_company(
        state.unit(LATER),
        company.id,
        renaming(parse_legal_name("Duarte Hermanos")),
        admits=anything,
        actor=OWNER,
    )
    inactive = await commands.deactivate_company(state.unit(LATER), company.id, admits=anything, actor=OWNER)
    assert state.audited[3:] == [
        Audited("company.rename", company.id, company, renamed, {}),
        Audited("company.deactivate", company.id, renamed, inactive, {}),
    ]


async def test_what_changes_nothing_or_is_refused_is_not_audited(state: State) -> None:
    company = await created(state)
    await commands.change_company(
        state.unit(LATER), company.id, renaming(company.legal_name), admits=anything, actor=OWNER
    )
    with pytest.raises(StaleVersionError):
        await commands.deactivate_company(state.unit(LATER), company.id, admits=nothing, actor=OWNER)
    await commands.grant_role(state.unit(), company.id, subject=VIEWER, role=Role.VIEWER, actor=OWNER)
    await commands.grant_role(state.unit(), company.id, subject=VIEWER, role=Role.VIEWER, actor=OWNER)
    await commands.revoke_role(state.unit(), company.id, subject=VIEWER, actor=OWNER)
    await commands.revoke_role(state.unit(), company.id, subject=VIEWER, actor=OWNER)
    opening = commands.Opening(parse_code("SUC1"), parse_name("A"), None)
    await commands.open_establishment(state.unit(), company.id, opening, actor=OWNER)
    with pytest.raises(EstablishmentCodeTakenError):
        await commands.open_establishment(state.unit(), company.id, opening, actor=OWNER)
    assert [entry.action for entry in state.audited] == [
        "company.register",
        "membership.grant",
        "establishment.open",
        "membership.grant",
        "membership.revoke",
        "establishment.open",
    ]


async def test_a_grant_and_a_revocation_are_audited_with_the_member_its_role_and_the_one_it_replaced(
    state: State,
) -> None:
    company = await created(state)
    first = await commands.grant_role(state.unit(), company.id, subject=VIEWER, role=Role.VIEWER, actor=OWNER)
    changed = await commands.grant_role(
        state.unit(LATER), company.id, subject=VIEWER, role=Role.ISSUER, actor=OWNER
    )
    await commands.revoke_role(state.unit(LATER), company.id, subject=VIEWER, actor=OWNER)
    revoked = state.memberships[company.id, VIEWER]
    member = first.membership.id
    assert state.audited[3:] == [
        Audited("membership.grant", member, None, first.membership, {"member": VIEWER, "role": "viewer"}),
        Audited(
            "membership.grant",
            member,
            first.membership,
            changed.membership,
            {"member": VIEWER, "role": "issuer", "previous_role": "viewer"},
        ),
        Audited(
            "membership.revoke", member, changed.membership, revoked, {"member": VIEWER, "role": "issuer"}
        ),
    ]


async def test_an_opened_establishment_is_audited_with_its_code_and_time_zone(state: State) -> None:
    company = await created(state)
    opened = await commands.open_establishment(
        state.unit(),
        company.id,
        commands.Opening(parse_code("SUC1"), parse_name("Santiago"), SANTO_DOMINGO),
        actor=OWNER,
    )
    detail = {"code": "SUC1", "time_zone": "America/Santo_Domingo"}
    assert state.audited[3:] == [Audited("establishment.open", opened.id, None, opened, detail)]


# ── Establishments ─────────────────────────────────────────────────────────────


async def test_a_new_company_has_a_main_establishment_in_the_time_zone_it_was_given(state: State) -> None:
    company = await created(state)
    (main,) = state.establishments.values()
    assert (main.tenant, main.code, main.name, main.time_zone) == (
        company.tenant,
        "MAIN",
        "Main",
        SANTO_DOMINGO,
    )
    detail = {"code": "MAIN", "time_zone": "America/Santo_Domingo"}
    assert state.audited[2] == Audited("establishment.open", main.id, None, main, detail)


async def test_establishments_are_opened_once_per_code_and_default_to_the_main_time_zone(
    state: State,
) -> None:
    company = await created(state)
    santiago = await commands.open_establishment(
        state.unit(),
        company.id,
        commands.Opening(parse_code("SUC1"), parse_name("Santiago"), None),
        actor=OWNER,
    )
    assert santiago.time_zone == SANTO_DOMINGO
    madrid = await commands.open_establishment(
        state.unit(),
        company.id,
        commands.Opening(parse_code("SUC2"), parse_name("Madrid"), parse_time_zone("Europe/Madrid")),
        actor=OWNER,
    )
    assert madrid.time_zone == "Europe/Madrid"
    for taken in ("SUC1", "MAIN"):
        with pytest.raises(EstablishmentCodeTakenError):
            await commands.open_establishment(
                state.unit(),
                company.id,
                commands.Opening(parse_code(taken), parse_name("Other"), None),
                actor=OWNER,
            )
    assert await queries.establishment(state.unit(), company.id, santiago.id) == santiago
    listed = await queries.establishments(state.unit(), company.id, PageRequest())
    assert {item.code for item in listed.items} == {"MAIN", "SUC1", "SUC2"}


async def test_a_company_deactivated_while_an_establishment_waits_for_its_lock_opens_nothing(
    state: State,
) -> None:
    company = await created(state)
    state.while_waiting = deactivate(state, company)
    with pytest.raises(CompanyInactiveError):
        await commands.open_establishment(
            state.unit(),
            company.id,
            commands.Opening(parse_code("SUC2"), parse_name("B"), None),
            actor=OWNER,
        )
    assert len(state.establishments) == 1


async def test_an_establishment_of_another_company_is_not_found(state: State) -> None:
    mine = await created(state)
    theirs = await created(state, THEIRS, founder=STRANGER)
    (theirs_main,) = (e for e in state.establishments.values() if e.tenant == theirs.tenant)
    with pytest.raises(EstablishmentNotFoundError):
        await queries.establishment(state.unit(), mine.id, theirs_main.id)


async def test_a_company_without_its_main_establishment_has_no_default_time_zone(state: State) -> None:
    company = await created(state)
    state.establishments.clear()
    with pytest.raises(EstablishmentNotFoundError):
        await commands.open_establishment(
            state.unit(),
            company.id,
            commands.Opening(parse_code("SUC1"), parse_name("Santiago"), None),
            actor=OWNER,
        )


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
    with pytest.raises(PermissionDeniedError):
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
    with pytest.raises(PermissionDeniedError):
        await commands.grant_role(state.unit(), company.id, subject=VIEWER, role=Role.VIEWER, actor=ADMIN)
    with pytest.raises(PermissionDeniedError):
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
    with pytest.raises(PermissionDeniedError):
        await commands.deactivate_company(state.unit(LATER), company.id, admits=anything, actor=ADMIN)
    with pytest.raises(PermissionDeniedError):
        await commands.change_company(
            state.unit(LATER), company.id, renaming(parse_legal_name("X")), admits=anything, actor=ADMIN
        )
    assert (state.companies[company.id], len(state.events)) == (company, 3)


async def test_a_company_is_changed_by_an_actor_with_an_active_role_there_only(state: State) -> None:
    company = await created(state)
    await with_an_admin(state, company)
    await commands.revoke_role(state.unit(), company.id, subject=ADMIN, actor=OWNER)
    with pytest.raises(CompanyNotFoundError):
        await commands.deactivate_company(state.unit(LATER), company.id, admits=anything, actor=ADMIN)
    with pytest.raises(CompanyNotFoundError):
        await commands.change_company(
            state.unit(LATER), company.id, renaming(parse_legal_name("X")), admits=anything, actor=VIEWER
        )
    assert state.companies[company.id] == company


async def test_a_refused_actor_is_refused_whatever_version_it_names(state: State) -> None:
    company = await created(state)
    await commands.grant_role(state.unit(), company.id, subject=VIEWER, role=Role.VIEWER, actor=OWNER)
    with pytest.raises(PermissionDeniedError):
        await commands.deactivate_company(state.unit(LATER), company.id, admits=nothing, actor=VIEWER)


async def test_an_admin_demoted_while_its_opening_waits_for_the_company_opens_nothing(state: State) -> None:
    company = await created(state)
    await with_an_admin(state, company)
    state.while_waiting = demote(state, company, ADMIN, Role.VIEWER)
    with pytest.raises(PermissionDeniedError):
        await commands.open_establishment(
            state.unit(),
            company.id,
            commands.Opening(parse_code("SUC2"), parse_name("B"), None),
            actor=ADMIN,
        )
    assert len(state.establishments) == 1


async def test_establishments_open_for_an_actor_with_an_active_role_only(state: State) -> None:
    company = await created(state)
    with pytest.raises(CompanyNotFoundError):
        await commands.open_establishment(
            state.unit(),
            company.id,
            commands.Opening(parse_code("SUC2"), parse_name("B"), None),
            actor=VIEWER,
        )
    assert len(state.establishments) == 1


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
    (main,) = state.establishments.values()
    outsider = state.unit(organization=STRANGER.organization)
    with pytest.raises(CompanyNotFoundError):
        await queries.company(outsider, company.id)
    with pytest.raises(CompanyNotFoundError):
        await commands.open_establishment(
            outsider,
            company.id,
            commands.Opening(parse_code("SUC2"), parse_name("B"), None),
            actor=OWNER,
        )
    with pytest.raises(CompanyNotFoundError):
        await commands.grant_role(outsider, company.id, subject=VIEWER, role=Role.VIEWER, actor=OWNER)
    with pytest.raises(CompanyNotFoundError):
        await commands.change_company(
            outsider, company.id, renaming(parse_legal_name("X")), admits=anything, actor=OWNER
        )
    with pytest.raises(EstablishmentNotFoundError):
        await queries.establishment(outsider, company.id, main.id)
    assert await queries.permissions_of(outsider, company.id, OWNER) == frozenset()
    assert (await queries.memberships(outsider, company.id, PageRequest())).items == ()
    assert (await queries.establishments(outsider, company.id, PageRequest())).items == ()


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
    mine = [
        await created(state, TaxId(TaxIdScheme("do.rnc"), rnc))
        for rnc in ("101850043", "130000012", "401007551")
    ]
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
    assert await queries.permissions_of(state.unit(), company.id, OWNER) == EVERYTHING
    assert await queries.permissions_of(state.unit(), company.id, VIEWER) == frozenset()


async def test_other_services_get_the_reading_permissions_only_of_a_deactivated_company(state: State) -> None:
    company = await created(state)
    await commands.deactivate_company(state.unit(LATER), company.id, admits=anything, actor=OWNER)
    assert await queries.permissions_of(state.unit(), company.id, OWNER) == only_reads(EVERYTHING)
    assert await queries.permissions_of(state.unit(), CompanyId(new_id()), OWNER) == frozenset()


async def test_the_memberships_of_a_company_are_listed(state: State) -> None:
    company = await created(state)
    await commands.grant_role(state.unit(), company.id, subject=VIEWER, role=Role.VIEWER, actor=OWNER)
    page = await queries.memberships(state.unit(), company.id, PageRequest())
    assert {membership.subject for membership in page.items} == {"2001", "3001"}


# ── API clients ─────────────────────────────────────────────────────────────────

MACHINE: Final = SubjectId("4001")


def client_registration(subject: SubjectId = MACHINE, mode: Mode = Mode.TEST) -> commands.ClientRegistration:
    spec = ApiClientSpec(subject=subject, name=parse_name("Odoo"), api_version=VERSION)
    return commands.ClientRegistration(spec=spec, mode=mode)


async def test_an_administrator_registers_a_machine_user_as_an_api_client_of_its_mode(state: State) -> None:
    client = await commands.register_api_client(
        state.unit(mode=Mode.LIVE), FOUNDER, client_registration(mode=Mode.LIVE)
    )
    assert state.api_clients == {client.id: client}
    assert (client.organization, client.mode, client.subject, client.created_by, client.api_version) == (
        ORGANIZATION,
        Mode.LIVE,
        MACHINE,
        FOUNDER.subject,
        VERSION,
    )
    (audit,) = state.audited
    assert (audit.action, audit.subject, audit.before, audit.after) == (
        "api_client.register",
        client.id,
        None,
        client,
    )
    assert audit.detail == {"subject": "4001", "mode": "live"}  # ids and words: never the client's name
    assert state.events == []


async def test_the_first_thing_an_organization_registers_may_be_an_api_client(state: State) -> None:
    await commands.register_api_client(state.unit(), FOUNDER, client_registration())
    assert state.organizations == {
        ORGANIZATION: Organization(id=ORGANIZATION, provider_id="1001", created_at=NOW, api_version=VERSION)
    }
    await commands.register_api_client(state.unit(LATER), FOUNDER, client_registration(SubjectId("4002")))
    assert state.organizations[ORGANIZATION].created_at == NOW  # kept as it was


async def test_only_an_organization_administrator_registers_or_revokes_api_clients(state: State) -> None:
    registered = await commands.register_api_client(state.unit(), FOUNDER, client_registration())
    with pytest.raises(OrganizationAdminRequiredError):
        await commands.register_api_client(state.unit(), NOT_ADMIN, client_registration(SubjectId("4002")))
    with pytest.raises(OrganizationAdminRequiredError):
        await commands.revoke_api_client(state.unit(), NOT_ADMIN, registered.id)
    assert state.api_clients == {registered.id: registered}


async def test_a_machine_user_is_one_api_client_whatever_the_mode_until_it_is_revoked(state: State) -> None:
    first = await commands.register_api_client(state.unit(), FOUNDER, client_registration())
    for mode in (Mode.TEST, Mode.LIVE):
        with pytest.raises(ApiClientExistsError):
            await commands.register_api_client(state.unit(mode=mode), FOUNDER, client_registration(mode=mode))
    await commands.revoke_api_client(state.unit(), FOUNDER, first.id)
    again = await commands.register_api_client(
        state.unit(mode=Mode.LIVE), FOUNDER, client_registration(mode=Mode.LIVE)
    )
    assert again.id != first.id
    assert [client.active for client in state.api_clients.values()] == [False, True]


async def test_revoking_an_api_client_is_audited_once_and_idempotent(state: State) -> None:
    client = await commands.register_api_client(state.unit(), FOUNDER, client_registration())
    revoked = await commands.revoke_api_client(state.unit(LATER), FOUNDER, client.id)
    again = await commands.revoke_api_client(state.unit(LATER + timedelta(hours=1)), FOUNDER, client.id)
    assert again == revoked
    assert (revoked.active, revoked.revoked_at) == (False, LATER)
    assert [(audit.action, audit.before, audit.after) for audit in state.audited] == [
        ("api_client.register", None, client),
        ("api_client.revoke", client, revoked),
    ]
    assert state.audited[1].detail == {"subject": "4001", "mode": "test"}


async def test_an_api_client_revoked_meanwhile_by_another_unit_is_not_audited_twice(state: State) -> None:
    client = await commands.register_api_client(state.unit(), FOUNDER, client_registration())
    state.client_races_lost = True  # another unit revoked it between this one's read and its update
    revoked = await commands.revoke_api_client(state.unit(LATER), FOUNDER, client.id)
    assert not revoked.active
    assert [audit.action for audit in state.audited] == ["api_client.register"]


async def test_an_api_client_is_found_in_its_own_mode_only(state: State) -> None:
    client = await commands.register_api_client(state.unit(), FOUNDER, client_registration())
    assert await queries.api_client(state.unit(), client.id) == client
    with pytest.raises(ApiClientNotFoundError):
        await queries.api_client(state.unit(mode=Mode.LIVE), client.id)
    with pytest.raises(ApiClientNotFoundError):
        await commands.revoke_api_client(state.unit(mode=Mode.LIVE), FOUNDER, client.id)
    with pytest.raises(ApiClientNotFoundError):
        await queries.api_client(state.unit(mode=None), client.id)  # a unit with no mode sees none
    with pytest.raises(ApiClientNotFoundError):
        await queries.api_client(state.unit(organization=STRANGER.organization), client.id)


async def test_the_api_clients_of_a_mode_are_listed_in_id_order(state: State) -> None:
    made = [
        await commands.register_api_client(state.unit(), FOUNDER, client_registration(SubjectId(f"400{n}")))
        for n in range(3)
    ]
    await commands.register_api_client(
        state.unit(mode=Mode.LIVE), FOUNDER, client_registration(SubjectId("4009"), Mode.LIVE)
    )
    first = await queries.api_clients(state.unit(), PageRequest(size=2))
    assert first.items == tuple(made[:2])
    assert first.next_cursor is not None
    rest = await queries.api_clients(
        state.unit(), PageRequest(size=2, after=decode_cursor(first.next_cursor))
    )
    assert (rest.items, rest.next_cursor) == (tuple(made[2:]), None)
