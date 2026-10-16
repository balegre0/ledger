"""``/v1/companies/{company_id}/branches``: a company's branches, shared by both modes.

Opening one is a ``POST``: it acts in ``Ledger-Mode``, which keeps its ``Idempotency-Key``.
"""

from http import HTTPStatus
from typing import Annotated, Final

from fastapi import APIRouter, Depends
lazy from fastapi import Request, Response

from ledger_identity.adapters.http.models import BranchView
from ledger_identity.adapters.postgres.unit import bind
from ledger_identity.application import commands, queries
from ledger_identity.domain.ids import BranchId
from ledger_identity.domain.roles import COMPANIES_MANAGE, COMPANIES_READ
from ledger_identity.domain.text import parse_code, parse_name
from ledger_kernel.ids import parse_uuid
from ledger_kernel.tenancy import Tenant
from ledger_platform.http import (
    REPLAYED_HEADER,
    Page,
    allowed,
    answer,
    attempt_of,
    idempotency_key,
    json_response,
    ledger_mode,
    page_of_items,
    page_query,
    problems,
    success,
)
lazy from ledger_identity.adapters.http.context import ApiContext
lazy from ledger_identity.adapters.http.models import NewBranch
lazy from ledger_kernel.pagination import PageRequest
lazy from ledger_kernel.tenancy import Mode
lazy from ledger_platform.auth import CompanyAccess
lazy from ledger_platform.http import StoredResponse

READ: Final = problems(401, 403, 404, 422)
"""The error answers of a read under a company."""
OPEN: Final = problems(400, 401, 403, 404, 409, 422) | success(
    HTTPStatus.CREATED, "Location", REPLAYED_HEADER
)
"""The answers of opening something under a company, under a code: where it lives, or why it was not."""


def moded(access: CompanyAccess, mode: Mode) -> Tenant:
    """The unit of a ``POST`` under the request's company: the company, in the request's mode."""
    return Tenant(access.tenant.organization, access.tenant.company, mode)


def router(context: ApiContext) -> APIRouter:
    """The routes of branches."""
    branches = APIRouter(prefix="/v1/companies/{company_id}/branches", tags=["branches"])
    database = context.database
    reader, manager = (
        allowed(context.in_company, COMPANIES_READ),
        allowed(context.in_company, COMPANIES_MANAGE),
    )

    @branches.post("", status_code=HTTPStatus.CREATED, response_model=BranchView, responses=OPEN)
    async def open_branch(
        access: Annotated[CompanyAccess, Depends(manager)],
        mode: Annotated[Mode, Depends(ledger_mode)],
        key: Annotated[str, Depends(idempotency_key)],
        body: NewBranch,
        request: Request,
    ) -> Response:
        """Open a branch of the company under a code it does not use yet."""
        opening = commands.Opening(parse_code(body.code), parse_name(body.name))
        attempt = attempt_of(request, access.principal, key, body, company=access.tenant.company)
        async with database.unit(moded(access, mode)) as unit:

            async def open_it() -> StoredResponse:
                opened = await commands.open_branch(
                    bind(unit), access.tenant.company, opening, actor=access.principal.subject
                )
                location = f"/v1/companies/{opened.tenant.company}/branches/{opened.id}"
                return json_response(HTTPStatus.CREATED, BranchView.of(opened), {"Location": location})

            outcome = await context.idempotency.once(unit, attempt, open_it)
        return answer(outcome)

    @branches.get("", response_model=Page[BranchView], responses=READ)
    async def list_branches(
        access: Annotated[CompanyAccess, Depends(reader)],
        page: Annotated[PageRequest, Depends(page_query)],
    ) -> Page[BranchView]:
        """The company's branches."""
        async with database.unit(access.tenant) as unit:
            found = await queries.branches(bind(unit), access.tenant.company, page)
        return page_of_items(found, BranchView.of)

    @branches.get("/{branch_id}", response_model=BranchView, responses=READ)
    async def read_branch(access: Annotated[CompanyAccess, Depends(reader)], branch_id: str) -> BranchView:
        """A branch of the company."""
        branch = BranchId(parse_uuid(branch_id))
        async with database.unit(access.tenant) as unit:
            found = await queries.branch(bind(unit), access.tenant.company, branch)
        return BranchView.of(found)

    return branches
