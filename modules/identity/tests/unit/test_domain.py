import unicodedata
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Final
from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ledger_identity._manifest import MANIFEST
from ledger_identity.domain.branches import Branch, EmissionPoint
from ledger_identity.domain.company import Company, CompanyStatus, FiscalProfile
from ledger_identity.domain.errors import (
    BranchCodeTakenError,
    BranchNotFoundError,
    CompanyInactiveError,
    EmissionPointCodeTakenError,
    EmissionPointNotFoundError,
    InvalidCodeError,
    InvalidNameError,
    LastOwnerError,
    MembershipNotFoundError,
    OrganizationNotFoundError,
    OwnersOnlyError,
    StaleVersionError,
    TaxIdTakenError,
)
from ledger_identity.domain.membership import (
    Actor,
    MembershipStatus,
    Roster,
    found,
    grant,
    permissions_in,
    revoke,
)
from ledger_identity.domain.organization import Organization
from ledger_identity.domain.roles import (
    COMPANIES_MANAGE,
    COMPANIES_READ,
    EVERYTHING,
    MEMBERSHIPS_MANAGE,
    MEMBERSHIPS_READ,
    OWNERSHIP_MANAGE,
    PERMISSIONS,
    READ_ONLY,
    only_reads,
    permissions_of,
)
from ledger_identity.domain.text import (
    MAX_CODE,
    MAX_LEGAL_NAME,
    MAX_NAME,
    LegalName,
    parse_code,
    parse_legal_name,
    parse_name,
)
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
    Role,
    SubjectId,
    parse_permission,
)
from ledger_kernel.errors import ErrorKind, InvalidValueError
from ledger_kernel.ids import CompanyId, OrganizationId, new_id
from ledger_kernel.jurisdiction import parse_jurisdiction
from ledger_kernel.money import Currency
from ledger_kernel.tax_ids import TaxId, TaxIdScheme
from ledger_kernel.tenancy import Tenant
from ledger_platform.data.codec import payload_of
lazy from ledger_identity.domain.membership import Membership
lazy from ledger_kernel.errors import LedgerError

NOW: Final = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)
LATER: Final = NOW + timedelta(hours=1)
ORGANIZATION: Final = OrganizationId(new_id())
FOUNDER: Final = Principal(subject=SubjectId("2001"), organization=ORGANIZATION, provider_organization="1001")
RNC: Final = TaxId(TaxIdScheme("do.rnc"), "101850043")
CEDULA: Final = TaxId(TaxIdScheme("do.cedula"), "00113918205")
DOMINICAN: Final = parse_jurisdiction("DO")
PESO: Final = Currency.of("DOP")
OWNER: Final = Actor(SubjectId("2001"), permissions_of(Role.OWNER))
ADMIN: Final = Actor(SubjectId("2002"), permissions_of(Role.ADMIN))
VIEWER: Final = Actor(SubjectId("2003"), permissions_of(Role.VIEWER))


def profile(*tax_ids: TaxId) -> FiscalProfile:
    return FiscalProfile(jurisdiction=DOMINICAN, functional_currency=PESO, tax_ids=tax_ids)


def registered() -> Company:
    company, _ = Company.register(
        organization=ORGANIZATION,
        profile=profile(RNC),
        legal_name=parse_legal_name("Ferretería Duarte"),
        now=NOW,
    )
    return company


def deactivated() -> Company:
    company, _ = registered().deactivate(now=LATER)
    return company


def member(company: Company, subject: str, role: Role) -> Membership:
    membership, _ = grant(
        Roster(company, SubjectId(subject), None, owners=1), role=role, actor=OWNER, now=NOW
    )
    return membership


# ── Names and codes ─────────────────────────────────────────────────────────────


def test_a_name_is_trimmed_its_spaces_collapsed_and_composed() -> None:
    assert parse_legal_name("  Ferretería \t Duarte\n S.R.L. ") == "Ferretería Duarte S.R.L."
    assert parse_legal_name("Jos" + "e\u0301") == "José"  # decomposed accents are composed (NFC)


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("", "is empty"),
        (" \t\n ", "is empty"),
        ("x" * (MAX_LEGAL_NAME + 1), f"is longer than {MAX_LEGAL_NAME} characters"),
        ("Duarte\x00", "holds a control or format character"),
        ("Duar\u200dte", "holds a control or format character"),
    ],
    ids=["empty", "blank", "too-long", "nul", "zero-width-joiner"],
)
def test_a_name_that_cannot_be_shown_is_refused_without_being_echoed(text: str, problem: str) -> None:
    with pytest.raises(InvalidNameError) as caught:
        parse_legal_name(text)
    assert str(caught.value) == f"legal_name {problem}"
    assert (caught.value.kind, caught.value.code) == (ErrorKind.INVALID, "invalid_name")


def test_other_names_say_which_field_and_have_their_own_limit() -> None:
    assert parse_name("Sucursal Santiago") == "Sucursal Santiago"
    with pytest.raises(InvalidNameError, match=r"^branch_name is longer than 100 characters$"):
        parse_name("x" * (MAX_NAME + 1), field="branch_name")


@given(st.text(max_size=200))
def test_a_name_is_either_refused_or_read_the_same_way_twice(text: str) -> None:
    try:
        name = parse_legal_name(text)
    except InvalidNameError:
        return
    assert parse_legal_name(name) == name
    assert name == unicodedata.normalize("NFC", name)
    assert 0 < len(name) <= MAX_LEGAL_NAME


@pytest.mark.parametrize("text", ["001", "SUC1", "A", "9" * MAX_CODE])
def test_a_code_is_upper_case_letters_and_digits(text: str) -> None:
    assert parse_code(text) == text


@pytest.mark.parametrize("text", ["", "suc1", "SUC-1", "SUC 1", "1" * (MAX_CODE + 1), "Ñ"])
def test_anything_else_is_not_a_code(text: str) -> None:
    with pytest.raises(InvalidCodeError, match=f"a code is 1 to {MAX_CODE} upper-case letters and digits"):
        parse_code(text)


# ── Roles ───────────────────────────────────────────────────────────────────────


def test_what_each_role_may_do() -> None:
    assert {
        "identity.companies.read",
        "identity.companies.manage",
        "identity.memberships.read",
        "identity.memberships.manage",
        "identity.ownership.manage",
    } == EVERYTHING
    expected = {
        Role.OWNER: EVERYTHING,
        Role.ADMIN: EVERYTHING - {OWNERSHIP_MANAGE},
        Role.ACCOUNTANT: frozenset({COMPANIES_READ, MEMBERSHIPS_READ}),
        Role.ISSUER: frozenset({COMPANIES_READ}),
        Role.VIEWER: frozenset({COMPANIES_READ}),
    }
    assert expected == PERMISSIONS
    assert {COMPANIES_MANAGE, MEMBERSHIPS_MANAGE} <= PERMISSIONS[Role.ADMIN]


def test_the_roles_grant_exactly_what_the_manifest_declares() -> None:
    declared = {permission.code: frozenset(permission.roles) for permission in MANIFEST.permissions}
    granted = {
        permission: frozenset(role.value for role in Role if permission in permissions_of(role))
        for permission in EVERYTHING
    }
    assert granted == declared


def test_every_role_reads_its_company_and_only_owners_manage_ownership() -> None:
    assert all(COMPANIES_READ in permissions_of(role) for role in Role)
    assert [role for role in Role if OWNERSHIP_MANAGE in permissions_of(role)] == [Role.OWNER]


# ── Organizations and companies ─────────────────────────────────────────────────


def test_an_organization_is_the_one_its_principal_acts_in() -> None:
    assert Organization.of(FOUNDER, now=NOW) == Organization(
        id=ORGANIZATION, provider_id="1001", created_at=NOW
    )


def test_a_registered_company_is_active_at_its_first_version_and_says_so() -> None:
    company, created = Company.register(
        organization=ORGANIZATION,
        profile=profile(RNC, CEDULA),
        legal_name=parse_legal_name("Duarte"),
        now=NOW,
    )
    assert (company.status, company.version, company.created_at, company.updated_at) == (
        CompanyStatus.ACTIVE,
        1,
        NOW,
        NOW,
    )
    assert (company.profile.jurisdiction, company.profile.functional_currency) == (DOMINICAN, PESO)
    assert (company.profile.tax_ids, company.profile.primary_tax_id) == ((RNC, CEDULA), RNC)
    assert company.fiscal_profile_locked_at is None
    assert company.tenant == Tenant(ORGANIZATION, company.id, None)
    assert company.id.version == 7
    assert created == CompanyRegistered(
        scope=company.tenant,
        subject=company.id,
        aggregate_version=1,
        jurisdiction=DOMINICAN,
        functional_currency=PESO,
        tax_ids=(RNC, CEDULA),
        legal_name=LegalName("Duarte"),
    )
    assert (created.TYPE, created.VERSION, created.scope) == (
        "identity.company.registered",
        1,
        company.tenant,
    )


@pytest.mark.parametrize("tax_ids", [(), (RNC, RNC)], ids=["none", "the-same-twice"])
def test_a_fiscal_profile_holds_one_or_more_distinct_tax_ids(tax_ids: tuple[TaxId, ...]) -> None:
    with pytest.raises(InvalidValueError, match=r"^fiscal_profile\.tax_ids must be"):
        profile(*tax_ids)


def test_a_company_locks_its_fiscal_profile_once_at_a_new_version() -> None:
    company = registered()
    locked, updated = company.lock_fiscal_profile(now=LATER)
    assert (locked.fiscal_profile_locked_at, locked.version, locked.updated_at) == (LATER, 2, LATER)
    assert locked.profile == company.profile  # what the lock holds fixed
    assert updated == CompanyUpdated(
        scope=company.tenant,
        subject=company.id,
        aggregate_version=2,
        tax_ids=(RNC,),
        legal_name=company.legal_name,
        fiscal_profile_locked=True,
    )
    assert updated is not None
    assert updated.fiscal_profile_locked  # what a consumer reads to know the profile no longer moves
    assert locked.lock_fiscal_profile(now=LATER + timedelta(hours=1)) == (locked, None)  # locked once


def test_a_deactivated_company_locks_nothing() -> None:
    with pytest.raises(CompanyInactiveError):
        deactivated().lock_fiscal_profile(now=LATER)


def test_renaming_a_company_makes_a_new_version() -> None:
    company = registered()
    renamed, updated = company.rename(parse_legal_name("Duarte Hermanos"), now=LATER)
    assert (renamed.legal_name, renamed.version, renamed.updated_at, renamed.created_at) == (
        "Duarte Hermanos",
        2,
        LATER,
        NOW,
    )
    assert updated == CompanyUpdated(
        scope=company.tenant,
        subject=company.id,
        aggregate_version=2,
        tax_ids=(RNC,),
        legal_name=LegalName("Duarte Hermanos"),
        fiscal_profile_locked=False,
    )


def test_renaming_a_company_to_its_own_name_changes_nothing() -> None:
    company = registered()
    assert company.rename(company.legal_name, now=LATER) == (company, None)


def test_a_deactivated_company_says_so_and_changes_no_more() -> None:
    company = registered()
    inactive, event = company.deactivate(now=LATER)
    assert (inactive.status, inactive.version) == (CompanyStatus.INACTIVE, 2)
    assert event == CompanyDeactivated(scope=company.tenant, subject=company.id, aggregate_version=2)
    with pytest.raises(CompanyInactiveError) as caught:
        inactive.rename(parse_legal_name("Other"), now=LATER)
    assert (caught.value.kind, caught.value.code) == (ErrorKind.CONFLICT, "company_inactive")
    with pytest.raises(CompanyInactiveError):
        inactive.deactivate(now=LATER)


# ── Branches and emission points ───────────────────────────────────────────────


def test_a_branch_and_its_emission_points_belong_to_the_company() -> None:
    company = registered()
    branch = Branch.open(company, code=parse_code("SUC1"), name=parse_name("Santiago"), now=NOW)
    point = EmissionPoint.open(company, branch, code=parse_code("001"), name=parse_name("Caja 1"), now=NOW)
    assert (branch.tenant, branch.code, branch.name, branch.created_at) == (
        company.tenant,
        "SUC1",
        "Santiago",
        NOW,
    )
    assert (point.tenant, point.branch, point.code) == (company.tenant, branch.id, "001")


def test_nothing_opens_in_a_deactivated_company() -> None:
    company = registered()
    branch = Branch.open(company, code=parse_code("SUC1"), name=parse_name("Santiago"), now=NOW)
    inactive = company.deactivate(now=LATER)[0]
    with pytest.raises(CompanyInactiveError):
        Branch.open(inactive, code=parse_code("SUC2"), name=parse_name("Bonao"), now=LATER)
    with pytest.raises(CompanyInactiveError):
        EmissionPoint.open(inactive, branch, code=parse_code("002"), name=parse_name("Caja 2"), now=LATER)


def test_an_emission_point_cannot_open_in_a_branch_of_another_company() -> None:
    company, other = registered(), registered()
    branch = Branch.open(other, code=parse_code("SUC1"), name=parse_name("Santiago"), now=NOW)
    with pytest.raises(BranchNotFoundError):
        EmissionPoint.open(company, branch, code=parse_code("001"), name=parse_name("Caja 1"), now=NOW)


# ── Memberships ─────────────────────────────────────────────────────────────────


def test_the_founder_of_a_company_is_its_first_owner() -> None:
    company = registered()
    owner, granted = found(company, FOUNDER, now=NOW)
    assert (owner.subject, owner.role, owner.status, owner.version, owner.changed_by, owner.tenant) == (
        "2001",
        Role.OWNER,
        MembershipStatus.ACTIVE,
        1,
        "2001",
        company.tenant,
    )
    assert owner.active
    assert owner.permissions == EVERYTHING
    assert granted == MembershipGranted(
        scope=company.tenant,
        subject=owner.id,
        aggregate_version=1,
        member=SubjectId("2001"),
        role="owner",
        previous=None,
    )


def test_an_admin_grants_and_changes_roles_below_owner() -> None:
    company = registered()
    viewer, granted = grant(
        Roster(company, SubjectId("3001"), None, owners=1), role=Role.VIEWER, actor=ADMIN, now=NOW
    )
    assert (viewer.role, viewer.version, viewer.changed_by) == (Role.VIEWER, 1, "2002")
    assert granted is not None
    assert granted.previous is None
    issuer, changed = grant(
        Roster(company, viewer.subject, viewer, owners=1), role=Role.ISSUER, actor=ADMIN, now=LATER
    )
    assert (issuer.id, issuer.role, issuer.version, issuer.changed_at) == (viewer.id, Role.ISSUER, 2, LATER)
    assert changed == MembershipGranted(
        scope=company.tenant,
        subject=viewer.id,
        aggregate_version=2,
        member=SubjectId("3001"),
        role="issuer",
        previous="viewer",
    )


@pytest.mark.parametrize("role", [Role.VIEWER, Role.OWNER], ids=["viewer", "owner"])
def test_whoever_may_not_manage_memberships_changes_none(role: Role) -> None:
    company = registered()
    with pytest.raises(PermissionDeniedError) as caught:
        grant(Roster(company, SubjectId("3001"), None, owners=1), role=role, actor=VIEWER, now=NOW)
    assert (caught.value.kind, caught.value.code) == (ErrorKind.FORBIDDEN, "permission_denied")
    viewer = member(company, "3001", Role.VIEWER)
    with pytest.raises(PermissionDeniedError):
        revoke(Roster(company, viewer.subject, viewer, owners=1), actor=VIEWER, now=NOW)


def test_granting_the_role_a_principal_holds_changes_nothing() -> None:
    company = registered()
    viewer = member(company, "3001", Role.VIEWER)
    assert grant(
        Roster(company, viewer.subject, viewer, owners=1), role=Role.VIEWER, actor=ADMIN, now=LATER
    ) == (
        viewer,
        None,
    )


@pytest.mark.parametrize(
    ("current", "role"),
    [(None, Role.OWNER), (Role.VIEWER, Role.OWNER), (Role.OWNER, Role.ADMIN)],
    ids=["new-owner", "promote-to-owner", "demote-an-owner"],
)
def test_only_an_owner_grants_or_takes_the_owner_role(current: Role | None, role: Role) -> None:
    company = registered()
    held = None if current is None else member(company, "3001", current)
    with pytest.raises(OwnersOnlyError) as caught:
        grant(Roster(company, SubjectId("3001"), held, owners=2), role=role, actor=ADMIN, now=LATER)
    assert (caught.value.kind, caught.value.code) == (ErrorKind.FORBIDDEN, "owners_only")


def test_the_last_owner_cannot_be_demoted() -> None:
    company = registered()
    owner, _ = found(company, FOUNDER, now=NOW)
    with pytest.raises(LastOwnerError) as caught:
        grant(Roster(company, owner.subject, owner, owners=1), role=Role.ADMIN, actor=OWNER, now=LATER)
    assert (caught.value.kind, caught.value.code) == (ErrorKind.CONFLICT, "last_owner")


def test_one_of_two_owners_can_be_demoted_by_an_owner() -> None:
    company = registered()
    second = member(company, "3001", Role.OWNER)
    demoted, _ = grant(
        Roster(company, second.subject, second, owners=2), role=Role.ADMIN, actor=OWNER, now=LATER
    )
    assert demoted.role is Role.ADMIN


def test_no_role_is_granted_in_a_deactivated_company() -> None:
    with pytest.raises(CompanyInactiveError):
        grant(
            Roster(deactivated(), SubjectId("3001"), None, owners=1),
            role=Role.VIEWER,
            actor=OWNER,
            now=LATER,
        )


def test_a_revoked_membership_is_kept_at_a_new_version_and_says_which_role_it_held() -> None:
    company = registered()
    viewer = member(company, "3001", Role.VIEWER)
    revoked, event = revoke(Roster(company, viewer.subject, viewer, owners=1), actor=ADMIN, now=LATER)
    assert event == MembershipRevoked(
        scope=company.tenant, subject=viewer.id, aggregate_version=2, member=SubjectId("3001"), role="viewer"
    )
    assert (revoked.id, revoked.status, revoked.version, revoked.changed_at, revoked.changed_by) == (
        viewer.id,
        MembershipStatus.REVOKED,
        2,
        LATER,
        "2002",
    )
    assert not revoked.active
    assert revoked.permissions == frozenset()


def test_a_role_granted_again_continues_the_versions_of_the_revoked_membership() -> None:
    company = registered()
    viewer = member(company, "3001", Role.VIEWER)
    revoked, _ = revoke(Roster(company, viewer.subject, viewer, owners=1), actor=ADMIN, now=NOW)
    again, granted = grant(
        Roster(company, viewer.subject, revoked, owners=1), role=Role.VIEWER, actor=ADMIN, now=LATER
    )
    assert (again.id, again.status, again.version) == (viewer.id, MembershipStatus.ACTIVE, 3)
    assert granted == MembershipGranted(
        scope=company.tenant,
        subject=viewer.id,
        aggregate_version=3,
        member=SubjectId("3001"),
        role="viewer",
        previous=None,
    )


def test_a_role_can_be_revoked_even_in_a_deactivated_company() -> None:
    company = registered()
    viewer = member(company, "3001", Role.VIEWER)
    inactive = company.deactivate(now=LATER)[0]
    _, revoked = revoke(Roster(inactive, viewer.subject, viewer, owners=1), actor=ADMIN, now=LATER)
    assert revoked is not None
    assert revoked.role == Role.VIEWER


def test_only_an_owner_revokes_an_owner_and_never_the_last() -> None:
    company = registered()
    owner, _ = found(company, FOUNDER, now=NOW)
    with pytest.raises(OwnersOnlyError):
        revoke(Roster(company, owner.subject, owner, owners=2), actor=ADMIN, now=LATER)
    with pytest.raises(LastOwnerError):
        revoke(Roster(company, owner.subject, owner, owners=1), actor=OWNER, now=LATER)
    _, revoked = revoke(Roster(company, owner.subject, owner, owners=2), actor=OWNER, now=LATER)
    assert revoked is not None
    assert revoked.role == Role.OWNER


def test_revoking_a_role_nobody_holds_is_not_found() -> None:
    with pytest.raises(MembershipNotFoundError, match="'3001' holds no role in the company") as caught:
        revoke(Roster(registered(), SubjectId("3001"), None, owners=1), actor=OWNER, now=NOW)
    assert caught.value.code == "membership_not_found"


def test_revoking_a_role_already_revoked_changes_and_announces_nothing() -> None:
    """A retried revocation: the first one's outcome stands, and the answer is the same."""
    company = registered()
    viewer = member(company, "3001", Role.VIEWER)
    revoked, _ = revoke(Roster(company, viewer.subject, viewer, owners=1), actor=OWNER, now=NOW)
    assert revoke(Roster(company, viewer.subject, revoked, owners=1), actor=ADMIN, now=LATER) == (
        revoked,
        None,
    )
    with pytest.raises(PermissionDeniedError):
        revoke(Roster(company, viewer.subject, revoked, owners=1), actor=VIEWER, now=LATER)


def test_a_revoked_owner_is_revoked_again_by_an_owner_only() -> None:
    """The same answer the first revocation gave: an admin could never take the owner role."""
    company = registered()
    owner, _ = found(company, FOUNDER, now=NOW)
    revoked, _ = revoke(Roster(company, owner.subject, owner, owners=2), actor=OWNER, now=NOW)
    with pytest.raises(OwnersOnlyError):
        revoke(Roster(company, owner.subject, revoked, owners=1), actor=ADMIN, now=LATER)
    assert revoke(Roster(company, owner.subject, revoked, owners=1), actor=OWNER, now=LATER) == (
        revoked,
        None,
    )


def test_what_reads_is_decided_by_the_verb_so_every_module_s_permissions_are_covered() -> None:
    """A deactivated company keeps its data readable in every module, not only in identity."""
    held = frozenset(
        parse_permission(code)
        for code in (
            "identity.companies.read",
            "invoicing.invoices.read",
            "invoicing.invoices.issue",
            "do_ecf.ranges.manage",
            "fiscal.enrollments.read",
        )
    )
    assert only_reads(held) == frozenset(
        parse_permission(code)
        for code in ("identity.companies.read", "invoicing.invoices.read", "fiscal.enrollments.read")
    )
    assert only_reads(frozenset()) == frozenset()


def test_other_services_may_let_a_member_of_a_deactivated_company_read_only() -> None:
    company = registered()
    issuer = member(company, "3001", Role.ISSUER)
    assert permissions_in(company, issuer) == permissions_of(Role.ISSUER)
    assert permissions_in(company.deactivate(now=LATER)[0], issuer) == frozenset({COMPANIES_READ})
    owner, _ = found(company, FOUNDER, now=NOW)
    assert permissions_in(company.deactivate(now=LATER)[0], owner) == READ_ONLY
    revoked, _ = revoke(Roster(company, issuer.subject, issuer, owners=1), actor=OWNER, now=NOW)
    assert permissions_in(company, revoked) == frozenset()


# ── What a membership or a company is built from ──────────────────────────────

OTHER_ORGANIZATION: Final = OrganizationId(new_id())


def test_a_roster_holds_only_the_membership_of_its_own_subject_in_its_own_company() -> None:
    company = registered()
    viewer = member(company, "3001", Role.VIEWER)
    assert Roster(company, viewer.subject, viewer, owners=1).current == viewer
    with pytest.raises(InvalidValueError, match=r"^roster\.current must be the membership of its subject$"):
        Roster(company, SubjectId("3002"), viewer, owners=1)
    with pytest.raises(InvalidValueError, match=r"^roster\.current must be the membership of its company$"):
        Roster(registered(), viewer.subject, viewer, owners=1)


def test_the_founder_of_a_company_belongs_to_its_organization_and_founds_an_active_company() -> None:
    stranger = Principal(
        subject=SubjectId("2001"), organization=OTHER_ORGANIZATION, provider_organization="9"
    )
    with pytest.raises(
        InvalidValueError, match=r"^founder must be a principal of the company's organization$"
    ):
        found(registered(), stranger, now=NOW)
    with pytest.raises(CompanyInactiveError):
        found(deactivated(), FOUNDER, now=NOW)


def test_a_version_starts_at_one() -> None:
    company = registered()
    with pytest.raises(InvalidValueError, match=r"^company\.version must be at least 1$"):
        replace(company, version=0)
    viewer = member(company, "3001", Role.VIEWER)
    with pytest.raises(InvalidValueError, match=r"^membership\.version must be at least 1$"):
        replace(viewer, version=0)


# ── Errors ──────────────────────────────────────────────────────────────────────

SOME_ID: Final = UUID("01a134e5-8363-74c7-9f6c-aa195c5ba397")
"""A fixed id: it is part of the test ids below, which every xdist worker must collect alike."""


@pytest.mark.parametrize(
    ("error", "kind", "code", "message"),
    [
        (
            OrganizationNotFoundError(SOME_ID),
            ErrorKind.NOT_FOUND,
            "organization_not_found",
            f"organization {SOME_ID} has no company yet",
        ),
        (
            CompanyNotFoundError(CompanyId(SOME_ID)),
            ErrorKind.NOT_FOUND,
            "company_not_found",
            f"company {SOME_ID} was not found",
        ),
        (
            BranchNotFoundError(SOME_ID),
            ErrorKind.NOT_FOUND,
            "branch_not_found",
            f"branch {SOME_ID} was not found",
        ),
        (
            EmissionPointNotFoundError(SOME_ID),
            ErrorKind.NOT_FOUND,
            "emission_point_not_found",
            f"emission point {SOME_ID} was not found",
        ),
        (
            TaxIdTakenError(),
            ErrorKind.CONFLICT,
            "tax_id_taken",
            "an active company already holds this tax id",
        ),
        (
            BranchCodeTakenError(),
            ErrorKind.CONFLICT,
            "branch_code_taken",
            "the company already has a branch with this code",
        ),
        (
            EmissionPointCodeTakenError(),
            ErrorKind.CONFLICT,
            "emission_point_code_taken",
            "the branch already has an emission point with this code",
        ),
        (
            StaleVersionError(SOME_ID),
            ErrorKind.CONFLICT,
            "stale_version",
            f"company {SOME_ID} is not at the version the request names",
        ),
        (
            CompanyInactiveError(SOME_ID),
            ErrorKind.CONFLICT,
            "company_inactive",
            f"company {SOME_ID} is inactive",
        ),
        (
            LastOwnerError(),
            ErrorKind.CONFLICT,
            "last_owner",
            "a company keeps at least one owner",
        ),
        (
            OwnersOnlyError(),
            ErrorKind.FORBIDDEN,
            "owners_only",
            "only an owner grants or takes the owner role",
        ),
        (
            InvalidCodeError(),
            ErrorKind.INVALID,
            "invalid_code",
            "a code is 1 to 10 upper-case letters and digits",
        ),
        (
            MembershipNotFoundError(SubjectId("3001")),
            ErrorKind.NOT_FOUND,
            "membership_not_found",
            "'3001' holds no role in the company",
        ),
        (
            InvalidValueError(field="roster", requirement="whole"),
            ErrorKind.INTERNAL,
            "invalid_value",
            "roster must be whole",
        ),
        (
            PermissionDeniedError(MEMBERSHIPS_MANAGE),
            ErrorKind.FORBIDDEN,
            "permission_denied",
            "this needs the permission identity.memberships.manage",
        ),
    ],
)
def test_every_error_has_its_kind_its_code_and_a_message_without_client_text(
    error: LedgerError, kind: ErrorKind, code: str, message: str
) -> None:
    assert (error.kind, error.code, str(error)) == (kind, code, message)


# ── Laws ────────────────────────────────────────────────────────────────────────

ACTORS: Final = (OWNER, ADMIN, VIEWER)
SUBJECTS: Final = tuple(SubjectId(str(number)) for number in range(2001, 2005))


@st.composite
def changes(draw: st.DrawFn) -> tuple[bool, SubjectId, Role, Actor]:
    return (
        draw(st.booleans()),
        draw(st.sampled_from(SUBJECTS)),
        draw(st.sampled_from(list(Role))),
        draw(st.sampled_from(ACTORS)),
    )


@given(st.lists(changes(), max_size=40))
def test_whatever_is_granted_or_revoked_a_company_keeps_an_owner_and_versions_only_grow(
    steps: list[tuple[bool, SubjectId, Role, Actor]],
) -> None:
    company = registered()
    founder, _ = found(company, FOUNDER, now=NOW)
    held = {founder.subject: founder}
    for revoking, subject, role, actor in steps:
        before = held.get(subject)
        roster = Roster(
            company, subject, before, owners=sum(m.active and m.role is Role.OWNER for m in held.values())
        )
        changed: Membership
        event: MembershipGranted | MembershipRevoked | None
        try:
            if revoking:
                changed, event = revoke(roster, actor=actor, now=LATER)
            else:
                changed, event = grant(roster, role=role, actor=actor, now=LATER)
        except LedgerError:
            continue
        if event is None:
            assert changed == before
        else:
            assert changed.version == (1 if before is None else before.version + 1) == event.aggregate_version
            assert event.subject == changed.id
        held[subject] = changed
        assert any(m.active and m.role is Role.OWNER for m in held.values())


# ── Events ──────────────────────────────────────────────────────────────────────


def test_every_event_identity_announces_is_declared_in_its_manifest_and_nothing_else() -> None:
    every = (CompanyRegistered, CompanyUpdated, CompanyDeactivated, MembershipGranted, MembershipRevoked)
    announced = {(event.TYPE, event.VERSION) for event in every}
    declared = {(event.type, event.version) for event in MANIFEST.events.publishes}
    assert announced == declared


def test_an_event_s_payload_is_its_own_fields_as_json() -> None:
    company = registered()
    _, event = company.deactivate(now=LATER)
    _, granted = found(company, FOUNDER, now=NOW)
    assert payload_of(event) == {}
    assert payload_of(granted) == {"member": "2001", "role": "owner", "previous": None}
