"""``/v1/companies``: register, list, read, change and deactivate companies.

A route checks, in this order: the caller (401), its access to the company (404) and its permission there
(403), as dependencies; then what the request must carry (``Idempotency-Key``, ``If-Match``) and its body
(422, 428). A ``POST`` claims its ``Idempotency-Key`` (``Idempotency.once``) before any statement of its own,
in the transaction of its work: a retry replays, a twin waits for the first and replays, a failure frees the
key. A change names the versions it may apply to in ``If-Match``: the use case locks the company, judges the
caller on the role it holds then, and compares and sets; a version that moved on meanwhile answers 412.
"""

from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, Depends
lazy from fastapi import Request, Response

from ledger_identity.api.context import precondition_checked, problems, success
from ledger_identity.api.models import CompanyPage, CompanyView
from ledger_identity.application import commands, queries
from ledger_identity.domain.organization import ORGANIZATION_ADMIN
from ledger_identity.domain.text import parse_legal_name
from ledger_kernel.access import Permission
from ledger_kernel.countries.do import TaxId
from ledger_kernel.tenancy import OrganizationScope
from ledger_platform.http.deps import (
    REPLAYED_HEADER,
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
    page_request,
)
lazy from ledger_identity.api.context import ApiContext
lazy from ledger_identity.api.models import CompanyChanges, NewCompany
lazy from ledger_identity.domain.company import Company
lazy from ledger_identity.settings import IdentitySettings
lazy from ledger_kernel.access import Principal
lazy from ledger_kernel.pagination import PageRequest
lazy from ledger_platform.auth.access import CompanyAccess
lazy from ledger_platform.http.deps import IfMatch
lazy from ledger_platform.idempotency import StoredResponse
lazy from ledger_platform.service import Runtime


def stored(status: HTTPStatus, company: Company) -> StoredResponse:
    """A company's answer, as kept for replays: its view and version, and where it lives once created."""
    headers = {"ETag": etag(company.version)}
    if status is HTTPStatus.CREATED:
        headers["Location"] = f"/v1/companies/{company.id}"
    return json_response(status, CompanyView.of(company), headers)


def router(context: ApiContext) -> APIRouter:
    """The companies' routes."""
    companies = APIRouter(prefix="/v1/companies", tags=["companies"])
    caller, runtime = context.caller, context.runtime
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
        responses=problems(401, 403, 409, 422)
        | success(HTTPStatus.CREATED, "Location", "ETag", REPLAYED_HEADER),
    )
    async def create_company(
        principal: Annotated[Principal, Depends(registrar)],
        body: NewCompany,
        request: Request,
        key: Annotated[str, Depends(idempotency_key)],
        current: Annotated[Runtime[IdentitySettings], Depends(runtime)],
    ) -> Response:
        """Register a company of the caller's organization; the caller becomes its owner.

        The caller must hold the identity provider's project role ``ledger.organization.admin``.
        """
        tax_id, legal_name = TaxId.parse(body.tax_id), parse_legal_name(body.legal_name)
        attempt = attempt_of(request, principal, key, body)
        async with current.database.transaction(
            OrganizationScope(principal.organization, None)
        ) as transaction:

            async def register() -> StoredResponse:
                unit = context.bind(transaction, current.outbox)
                created = await commands.create_company(unit, principal, tax_id=tax_id, legal_name=legal_name)
                return stored(HTTPStatus.CREATED, created)

            outcome = await context.idempotency.once(transaction, attempt, register)
        return answer(outcome)

    @companies.get("", response_model=CompanyPage, responses=problems(401, 422))
    async def list_companies(
        principal: Annotated[Principal, Depends(member)],
        page: Annotated[PageRequest, Depends(page_request)],
        current: Annotated[Runtime[IdentitySettings], Depends(runtime)],
    ) -> CompanyPage:
        """The companies of the caller's organization in which it holds a role."""
        async with current.database.transaction(
            OrganizationScope(principal.organization, None)
        ) as transaction:
            unit = context.bind(transaction, current.outbox)
            found = await queries.companies_of(unit, principal.subject, page)
        return CompanyPage(
            items=[CompanyView.of(company) for company in found.items], next_cursor=found.next_cursor
        )

    @companies.get(
        "/{company_id}",
        response_model=CompanyView,
        responses=problems(401, 403, 404, 422) | success(HTTPStatus.OK, "ETag"),
    )
    async def read_company(
        access: Annotated[CompanyAccess, Depends(reader)],
        response: Response,
        current: Annotated[Runtime[IdentitySettings], Depends(runtime)],
    ) -> CompanyView:
        """A company, with its version as ``ETag``."""
        async with current.database.transaction(access.tenant) as transaction:
            found = await queries.company(context.bind(transaction, current.outbox), access.tenant.company)
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
        current: Annotated[Runtime[IdentitySettings], Depends(runtime)],
    ) -> CompanyView:
        """Change a company, if it is at a version ``If-Match`` names."""
        legal_name = parse_legal_name(body.legal_name)
        with precondition_checked():
            async with current.database.transaction(access.tenant) as transaction:
                changed = await commands.rename_company(
                    context.bind(transaction, current.outbox),
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
        responses=problems(401, 403, 404, 409, 412, 422, 428)
        | success(HTTPStatus.OK, "ETag", REPLAYED_HEADER),
        openapi_extra=if_match_required(),
    )
    async def deactivate_company(
        access: Annotated[CompanyAccess, Depends(manager)],
        key: Annotated[str, Depends(idempotency_key)],
        condition: Annotated[IfMatch, Depends(if_match)],
        request: Request,
        current: Annotated[Runtime[IdentitySettings], Depends(runtime)],
    ) -> Response:
        """Deactivate a company, if it is at a version ``If-Match`` names: it changes no more."""
        attempt = attempt_of(request, access.principal, key)
        with precondition_checked():
            async with current.database.transaction(access.tenant) as transaction:

                async def deactivate() -> StoredResponse:
                    unit = context.bind(transaction, current.outbox)
                    inactive = await commands.deactivate_company(
                        unit, access.tenant.company, admits=condition.admits, actor=access.principal.subject
                    )
                    return stored(HTTPStatus.OK, inactive)

                outcome = await context.idempotency.once(transaction, attempt, deactivate)
        return answer(outcome)

    return companies
