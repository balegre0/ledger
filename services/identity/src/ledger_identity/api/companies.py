"""``/v1/companies``: register, list, read, change and deactivate companies.

A ``POST`` claims its ``Idempotency-Key`` (``Idempotency.once``) before any statement of its own, in the
transaction of its work: a retry replays, a twin waits for the first and replays, a failure frees the key. A
change names the versions it may apply to in ``If-Match``: the use case compares and sets, and a version that
moved on meanwhile answers 412.
"""

from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, Depends
lazy from fastapi import Request, Response

from ledger_identity.api.context import precondition_checked, problems
from ledger_identity.api.models import CompanyPage, CompanyView
from ledger_identity.application import commands, queries
from ledger_identity.domain.text import parse_legal_name
from ledger_kernel.access import Permission
from ledger_kernel.countries.do import TaxId
from ledger_platform.routes import (
    answer,
    attempt_of,
    etag,
    idempotency_key,
    if_match,
    json_response,
    page_request,
)
lazy from ledger_identity.api.context import ApiContext
lazy from ledger_identity.api.models import CompanyChanges, NewCompany
lazy from ledger_identity.domain.company import Company
lazy from ledger_identity.settings import IdentitySettings
lazy from ledger_kernel.access import Principal
lazy from ledger_kernel.pagination import PageRequest
lazy from ledger_platform.authorization import CompanyAccess
lazy from ledger_platform.idempotency import StoredResponse
lazy from ledger_platform.routes import IfMatch
lazy from ledger_platform.service import Runtime


def stored(status: HTTPStatus, company: Company) -> StoredResponse:
    """A company's answer, as kept for replays: its view, where it lives and its version."""
    headers = {"Location": f"/v1/companies/{company.id}", "ETag": etag(company.version)}
    return json_response(status, CompanyView.of(company), headers)


def router(context: ApiContext) -> APIRouter:
    """The companies' routes."""
    companies = APIRouter(prefix="/v1/companies", tags=["companies"])
    caller, in_company, runtime = context.caller, context.in_company, context.runtime

    @companies.post(
        "", status_code=HTTPStatus.CREATED, response_model=CompanyView, responses=problems(401, 409, 422)
    )
    async def create_company(
        body: NewCompany,
        request: Request,
        principal: Annotated[Principal, Depends(caller)],
        key: Annotated[str, Depends(idempotency_key)],
        current: Annotated[Runtime[IdentitySettings], Depends(runtime)],
    ) -> Response:
        """Register a company of the caller's organization; the caller becomes its owner."""
        tax_id, legal_name = TaxId.parse(body.tax_id), parse_legal_name(body.legal_name)
        attempt = attempt_of(request, principal, key, body)
        async with current.database.transaction(principal.organization) as transaction:

            async def register() -> StoredResponse:
                unit = context.bind(transaction, current.outbox)
                created = await commands.create_company(unit, principal, tax_id=tax_id, legal_name=legal_name)
                return stored(HTTPStatus.CREATED, created)

            outcome = await context.idempotency.once(transaction, attempt, register)
        return answer(outcome)

    @companies.get("", response_model=CompanyPage, responses=problems(401, 422))
    async def list_companies(
        principal: Annotated[Principal, Depends(caller)],
        page: Annotated[PageRequest, Depends(page_request)],
        current: Annotated[Runtime[IdentitySettings], Depends(runtime)],
    ) -> CompanyPage:
        """The companies of the caller's organization in which it holds a role."""
        async with current.database.transaction(principal.organization) as transaction:
            unit = context.bind(transaction, current.outbox)
            found = await queries.companies_of(unit, principal.subject, page)
        return CompanyPage(
            items=[CompanyView.of(company) for company in found.items], next_cursor=found.next_cursor
        )

    @companies.get("/{company_id}", response_model=CompanyView, responses=problems(401, 403, 404, 422))
    async def read_company(
        access: Annotated[CompanyAccess, Depends(in_company)],
        response: Response,
        current: Annotated[Runtime[IdentitySettings], Depends(runtime)],
    ) -> CompanyView:
        """A company, with its version as ``ETag``."""
        access.require(Permission.COMPANY_READ)
        async with current.database.transaction(access.tenant) as transaction:
            found = await queries.company(context.bind(transaction, current.outbox), access.tenant.company)
        response.headers["ETag"] = etag(found.version)
        return CompanyView.of(found)

    @companies.patch(
        "/{company_id}", response_model=CompanyView, responses=problems(401, 403, 404, 409, 412, 422, 428)
    )
    async def change_company(
        body: CompanyChanges,
        access: Annotated[CompanyAccess, Depends(in_company)],
        condition: Annotated[IfMatch, Depends(if_match)],
        response: Response,
        current: Annotated[Runtime[IdentitySettings], Depends(runtime)],
    ) -> CompanyView:
        """Change a company, if it is at a version ``If-Match`` names."""
        access.require(Permission.COMPANY_MANAGE)
        legal_name = parse_legal_name(body.legal_name)
        with precondition_checked():
            async with current.database.transaction(access.tenant) as transaction:
                changed = await commands.rename_company(
                    context.bind(transaction, current.outbox),
                    access.tenant.company,
                    legal_name=legal_name,
                    admits=condition.admits,
                )
        response.headers["ETag"] = etag(changed.version)
        return CompanyView.of(changed)

    @companies.post(
        "/{company_id}/deactivate",
        response_model=CompanyView,
        responses=problems(401, 403, 404, 409, 412, 422, 428),
    )
    async def deactivate_company(
        request: Request,
        access: Annotated[CompanyAccess, Depends(in_company)],
        key: Annotated[str, Depends(idempotency_key)],
        condition: Annotated[IfMatch, Depends(if_match)],
        current: Annotated[Runtime[IdentitySettings], Depends(runtime)],
    ) -> Response:
        """Deactivate a company, if it is at a version ``If-Match`` names: it changes no more."""
        access.require(Permission.COMPANY_MANAGE)
        attempt = attempt_of(request, access.principal, key)
        with precondition_checked():
            async with current.database.transaction(access.tenant) as transaction:

                async def deactivate() -> StoredResponse:
                    unit = context.bind(transaction, current.outbox)
                    inactive = await commands.deactivate_company(
                        unit, access.tenant.company, admits=condition.admits
                    )
                    return stored(HTTPStatus.OK, inactive)

                outcome = await context.idempotency.once(transaction, attempt, deactivate)
        return answer(outcome)

    return companies
