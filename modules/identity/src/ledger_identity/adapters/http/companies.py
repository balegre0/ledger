"""``/v1/companies``: register, list, read, change and deactivate companies.

A route checks, in this order: the caller (401), its access to the company (404) and its permission there
(403), as dependencies; then what the request must carry (``Ledger-Mode``, ``Idempotency-Key``, ``If-Match``)
and its body (400, 422, 428). A ``POST`` acts in the request's mode and claims its ``Idempotency-Key`` in that
mode (``Idempotency.once``) before any statement of its own, in the unit of its work: a retry replays, a twin
waits for the first and replays, a failure frees the key. A change names the versions it may apply to in
``If-Match``: the use case locks the company, judges the caller on the role it holds then, and compares and
sets; a version that moved on meanwhile answers 412.
"""

from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, Depends
lazy from fastapi import Request, Response

from ledger_identity.adapters.http.models import CompanyView
from ledger_identity.adapters.postgres.unit import bind
from ledger_identity.application import commands, queries
from ledger_identity.domain.errors import StaleVersionError
from ledger_identity.domain.organization import ORGANIZATION_ADMIN
from ledger_identity.domain.text import parse_legal_name
from ledger_kernel.access import Permission
from ledger_kernel.countries.do import TaxId
from ledger_kernel.tenancy import OrganizationScope, Tenant
from ledger_platform.http import (
    REPLAYED_HEADER,
    Page,
    allowed,
    answer,
    attempt_of,
    etag,
    holding,
    idempotency_key,
    if_match,
    if_match_required,
    in_organization,
    json_response,
    ledger_mode,
    page_of_items,
    page_query,
    precondition_checked,
    problems,
    success,
)
lazy from ledger_identity.adapters.http.context import ApiContext
lazy from ledger_identity.adapters.http.models import CompanyChanges, NewCompany
lazy from ledger_identity.domain.company import Company
lazy from ledger_kernel.access import Principal
lazy from ledger_kernel.pagination import PageRequest
lazy from ledger_kernel.tenancy import Mode
lazy from ledger_platform.auth import CompanyAccess
lazy from ledger_platform.http import IfMatch, StoredResponse


def stored(status: HTTPStatus, company: Company) -> StoredResponse:
    """A company's answer, as kept for replays: its view and version, and where it lives once created."""
    headers = {"ETag": etag(company.version)}
    if status is HTTPStatus.CREATED:
        headers["Location"] = f"/v1/companies/{company.id}"
    return json_response(status, CompanyView.of(company), headers)


def router(context: ApiContext) -> APIRouter:
    """The companies' routes."""
    companies = APIRouter(prefix="/v1/companies", tags=["companies"])
    caller, database = context.caller, context.database
    in_company = context.in_company
    reader, manager = (
        allowed(in_company, Permission.COMPANY_READ),
        allowed(in_company, Permission.COMPANY_MANAGE),
    )
    registrar, member = holding(caller, ORGANIZATION_ADMIN), in_organization(caller)

    @companies.post(
        "",
        status_code=HTTPStatus.CREATED,
        response_model=CompanyView,
        responses=problems(400, 401, 403, 409, 422)
        | success(HTTPStatus.CREATED, "Location", "ETag", REPLAYED_HEADER),
    )
    async def create_company(
        principal: Annotated[Principal, Depends(registrar)],
        mode: Annotated[Mode, Depends(ledger_mode)],
        body: NewCompany,
        request: Request,
        key: Annotated[str, Depends(idempotency_key)],
    ) -> Response:
        """Register a company of the caller's organization; the caller becomes its owner.

        The caller must hold the identity provider's project role ``ledger.organization.admin``. The request
        acts in ``Ledger-Mode``: its ``Idempotency-Key`` is kept per mode. The company is both modes'.
        """
        tax_id, legal_name = TaxId.parse(body.tax_id), parse_legal_name(body.legal_name)
        attempt = attempt_of(request, principal, key, body)
        async with database.unit(OrganizationScope(principal.organization, mode)) as unit:

            async def register() -> StoredResponse:
                created = await commands.create_company(
                    bind(unit), principal, tax_id=tax_id, legal_name=legal_name
                )
                return stored(HTTPStatus.CREATED, created)

            outcome = await context.idempotency.once(unit, attempt, register)
        return answer(outcome)

    @companies.get("", response_model=Page[CompanyView], responses=problems(401, 422))
    async def list_companies(
        principal: Annotated[Principal, Depends(member)],
        page: Annotated[PageRequest, Depends(page_query)],
    ) -> Page[CompanyView]:
        """The companies of the caller's organization in which it holds a role."""
        async with database.unit(OrganizationScope(principal.organization, None)) as unit:
            found = await queries.companies_of(bind(unit), principal.subject, page)
        return page_of_items(found, CompanyView.of)

    @companies.get(
        "/{company_id}",
        response_model=CompanyView,
        responses=problems(401, 403, 404, 422) | success(HTTPStatus.OK, "ETag"),
    )
    async def read_company(
        access: Annotated[CompanyAccess, Depends(reader)], response: Response
    ) -> CompanyView:
        """A company, with its version as ``ETag``."""
        async with database.unit(access.tenant) as unit:
            found = await queries.company(bind(unit), access.tenant.company)
        response.headers["ETag"] = etag(found.version)
        return CompanyView.of(found)

    @companies.patch(
        "/{company_id}",
        response_model=CompanyView,
        responses=problems(401, 403, 404, 409, 412, 422, 428) | success(HTTPStatus.OK, "ETag"),
        openapi_extra=if_match_required(),
    )
    async def change_company(
        access: Annotated[CompanyAccess, Depends(manager)],
        condition: Annotated[IfMatch, Depends(if_match)],
        body: CompanyChanges,
        response: Response,
    ) -> CompanyView:
        """Change a company, if it is at a version ``If-Match`` names."""
        legal_name = parse_legal_name(body.legal_name)
        with precondition_checked(StaleVersionError):
            async with database.unit(access.tenant) as unit:
                changed = await commands.rename_company(
                    bind(unit),
                    access.tenant.company,
                    legal_name=legal_name,
                    admits=condition.admits,
                    actor=access.principal.subject,
                )
        response.headers["ETag"] = etag(changed.version)
        return CompanyView.of(changed)

    @companies.post(
        "/{company_id}/deactivate",
        response_model=CompanyView,
        responses=problems(400, 401, 403, 404, 409, 412, 422, 428)
        | success(HTTPStatus.OK, "ETag", REPLAYED_HEADER),
        openapi_extra=if_match_required(),
    )
    async def deactivate_company(
        access: Annotated[CompanyAccess, Depends(manager)],
        mode: Annotated[Mode, Depends(ledger_mode)],
        key: Annotated[str, Depends(idempotency_key)],
        condition: Annotated[IfMatch, Depends(if_match)],
        request: Request,
    ) -> Response:
        """Deactivate a company, if it is at a version ``If-Match`` names: it changes no more.

        The request acts in ``Ledger-Mode``: its ``Idempotency-Key`` is kept per mode.
        """
        attempt = attempt_of(request, access.principal, key)
        tenant = Tenant(access.tenant.organization, access.tenant.company, mode)
        with precondition_checked(StaleVersionError):
            async with database.unit(tenant) as unit:

                async def deactivate() -> StoredResponse:
                    inactive = await commands.deactivate_company(
                        bind(unit),
                        access.tenant.company,
                        admits=condition.admits,
                        actor=access.principal.subject,
                    )
                    return stored(HTTPStatus.OK, inactive)

                outcome = await context.idempotency.once(unit, attempt, deactivate)
        return answer(outcome)

    return companies
