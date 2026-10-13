"""What reads identity's state: each query runs in a unit scoped to the caller's organization or company."""

from ledger_identity.domain.errors import (
    BranchNotFoundError,
    CompanyNotFoundError,
    EmissionPointNotFoundError,
    OrganizationNotFoundError,
)
from ledger_identity.domain.membership import permissions_in
lazy from ledger_identity.application.ports import IdentityUnit
lazy from ledger_identity.domain.branches import Branch, EmissionPoint
lazy from ledger_identity.domain.company import Company
lazy from ledger_identity.domain.ids import BranchId, EmissionPointId
lazy from ledger_identity.domain.membership import Membership
lazy from ledger_identity.domain.organization import Organization
lazy from ledger_kernel.access import Permission, SubjectId
lazy from ledger_kernel.ids import CompanyId, OrganizationId
lazy from ledger_kernel.pagination import Page, PageRequest


async def organization(unit: IdentityUnit, organization_id: OrganizationId) -> Organization:
    """The organization; ``OrganizationNotFoundError`` before its first company."""
    found = await unit.organizations.get(organization_id)
    if found is None:
        raise OrganizationNotFoundError(organization_id)
    return found


async def company(unit: IdentityUnit, company_id: CompanyId) -> Company:
    """The company; ``CompanyNotFoundError`` when the organization has none by that id."""
    found = await unit.companies.get(company_id)
    if found is None:
        raise CompanyNotFoundError(company_id)
    return found


async def companies_of(unit: IdentityUnit, subject: SubjectId, request: PageRequest) -> Page[Company]:
    """The companies of the unit's organization in which ``subject`` holds a role."""
    return await unit.companies.of_member(subject, request)


async def branch(unit: IdentityUnit, company_id: CompanyId, branch_id: BranchId) -> Branch:
    """The branch of the company; ``BranchNotFoundError`` otherwise."""
    found = await unit.branches.get(company_id, branch_id)
    if found is None:
        raise BranchNotFoundError(branch_id)
    return found


async def branches(unit: IdentityUnit, company_id: CompanyId, request: PageRequest) -> Page[Branch]:
    """The branches of the company."""
    return await unit.branches.page(company_id, request)


async def emission_point(
    unit: IdentityUnit, company_id: CompanyId, branch_id: BranchId, point_id: EmissionPointId
) -> EmissionPoint:
    """The emission point of the branch; ``EmissionPointNotFoundError`` otherwise."""
    found = await unit.emission_points.get(company_id, point_id)
    if found is None or found.branch != branch_id:
        raise EmissionPointNotFoundError(point_id)
    return found


async def emission_points(
    unit: IdentityUnit, company_id: CompanyId, branch_id: BranchId, request: PageRequest
) -> Page[EmissionPoint]:
    """The emission points of the branch, which must exist."""
    await branch(unit, company_id, branch_id)
    return await unit.emission_points.page(company_id, branch_id, request)


async def memberships(unit: IdentityUnit, company_id: CompanyId, request: PageRequest) -> Page[Membership]:
    """Who holds which role in the company."""
    return await unit.memberships.page(company_id, request)


async def permissions_of(
    unit: IdentityUnit, company_id: CompanyId, subject: SubjectId
) -> frozenset[Permission]:
    """What other services may let ``subject`` do in the company, which they cannot see deactivated.

    Its role's permissions; the reading ones only while the company is deactivated; none without an active
    membership, or in a company the organization does not have.
    """
    found = await unit.companies.get(company_id)
    membership = await unit.memberships.get(company_id, subject)
    if found is None or membership is None:
        return frozenset()
    return permissions_in(found, membership)
