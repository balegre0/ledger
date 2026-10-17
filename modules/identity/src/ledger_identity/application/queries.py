"""What reads identity's state: each query runs in a unit scoped to the caller's organization or company."""

from ledger_identity.domain.errors import (
    ApiClientNotFoundError,
    EstablishmentNotFoundError,
    OrganizationNotFoundError,
)
from ledger_identity.domain.establishments import MAIN_CODE
from ledger_identity.domain.membership import permissions_in
from ledger_kernel.access import CompanyNotFoundError
lazy from ledger_identity.application.ports import IdentityUnit
lazy from ledger_identity.domain.api_clients import ApiClient
lazy from ledger_identity.domain.company import Company
lazy from ledger_identity.domain.establishments import Establishment
lazy from ledger_identity.domain.ids import ApiClientId, EstablishmentId
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


async def establishment(
    unit: IdentityUnit, company_id: CompanyId, establishment_id: EstablishmentId
) -> Establishment:
    """The establishment of the company; ``EstablishmentNotFoundError`` otherwise."""
    found = await unit.establishments.get(company_id, establishment_id)
    if found is None:
        raise EstablishmentNotFoundError(establishment_id)
    return found


async def establishments(
    unit: IdentityUnit, company_id: CompanyId, request: PageRequest
) -> Page[Establishment]:
    """The establishments of the company."""
    return await unit.establishments.page(company_id, request)


async def api_client(unit: IdentityUnit, client_id: ApiClientId) -> ApiClient:
    """The API client of the unit's mode; ``ApiClientNotFoundError`` otherwise."""
    found = await unit.api_clients.get(client_id)
    if found is None:
        raise ApiClientNotFoundError(client_id)
    return found


async def api_clients(unit: IdentityUnit, request: PageRequest) -> Page[ApiClient]:
    """The API clients of the unit's mode, revoked ones too."""
    return await unit.api_clients.page(request)


async def main_establishment(unit: IdentityUnit, company_id: CompanyId) -> Establishment:
    """The company's ``MAIN`` establishment, which every company is registered with."""
    found = await unit.establishments.by_code(company_id, MAIN_CODE)
    if found is None:
        raise EstablishmentNotFoundError(company_id)
    return found


async def memberships(unit: IdentityUnit, company_id: CompanyId, request: PageRequest) -> Page[Membership]:
    """Who holds which role in the company."""
    return await unit.memberships.page(company_id, request)


async def permissions_of(
    unit: IdentityUnit, company_id: CompanyId, subject: SubjectId
) -> frozenset[Permission]:
    """What other modules may let ``subject`` do in the company, which they cannot see deactivated.

    Its role's permissions; the reading ones only while the company is deactivated; none without an active
    membership, or in a company the organization does not have.
    """
    found = await unit.companies.get(company_id)
    membership = await unit.memberships.get(company_id, subject)
    if found is None or membership is None:
        return frozenset()
    return permissions_in(found, membership)
