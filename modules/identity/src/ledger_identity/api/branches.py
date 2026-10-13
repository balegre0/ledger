"""``/v1/companies/{company_id}/branches``: a company's branches."""

from http import HTTPStatus
from typing import Annotated, Final

from fastapi import APIRouter, Depends
lazy from fastapi import Request, Response

from ledger_identity.api.context import problems, success
from ledger_identity.api.models import BranchPage, BranchView
from ledger_identity.application import commands, queries
from ledger_identity.domain.ids import BranchId
from ledger_identity.domain.text import parse_code, parse_name
from ledger_kernel.access import Permission
from ledger_kernel.ids import parse_id
from ledger_platform.http.deps import (
    REPLAYED_HEADER,
    allowed,
    answer,
    attempt_of,
    idempotency_key,
    json_response,
    page_request,
)
lazy from ledger_identity.api.context import ApiContext
lazy from ledger_identity.api.models import NewBranch
lazy from ledger_identity.settings import IdentitySettings
lazy from ledger_kernel.pagination import PageRequest
lazy from ledger_platform.auth.access import CompanyAccess
lazy from ledger_platform.idempotency import StoredResponse
lazy from ledger_platform.service import Runtime

READ: Final = problems(401, 403, 404, 422)
"""The error answers of a read under a company."""
OPEN: Final = problems(401, 403, 404, 409, 422) | success(HTTPStatus.CREATED, "Location", REPLAYED_HEADER)
"""The answers of opening something under a company, under a code: where it lives, or why it was not."""


def router(context: ApiContext) -> APIRouter:
    """The routes of branches."""
    branches = APIRouter(prefix="/v1/companies/{company_id}/branches", tags=["branches"])
    runtime = context.runtime
    reader, manager = (
        allowed(context.in_company, Permission.COMPANY_READ),
        allowed(context.in_company, Permission.COMPANY_MANAGE),
    )

    @branches.post("", status_code=HTTPStatus.CREATED, response_model=BranchView, responses=OPEN)
    async def open_branch(
        access: Annotated[CompanyAccess, Depends(manager)],
        key: Annotated[str, Depends(idempotency_key)],
        body: NewBranch,
        request: Request,
        current: Annotated[Runtime[IdentitySettings], Depends(runtime)],
    ) -> Response:
        """Open a branch of the company under a code it does not use yet."""
        opening = commands.Opening(parse_code(body.code), parse_name(body.name))
        attempt = attempt_of(request, access.principal, key, body)
        async with current.database.transaction(access.tenant) as transaction:

            async def open_it() -> StoredResponse:
                unit = context.bind(transaction, current.outbox)
                opened = await commands.open_branch(
                    unit, access.tenant.company, opening, actor=access.principal.subject
                )
                location = f"/v1/companies/{opened.tenant.company}/branches/{opened.id}"
                return json_response(HTTPStatus.CREATED, BranchView.of(opened), {"Location": location})

            outcome = await context.idempotency.once(transaction, attempt, open_it)
        return answer(outcome)

    @branches.get("", response_model=BranchPage, responses=READ)
    async def list_branches(
        access: Annotated[CompanyAccess, Depends(reader)],
        page: Annotated[PageRequest, Depends(page_request)],
        current: Annotated[Runtime[IdentitySettings], Depends(runtime)],
    ) -> BranchPage:
        """The company's branches."""
        async with current.database.transaction(access.tenant) as transaction:
            found = await queries.branches(
                context.bind(transaction, current.outbox), access.tenant.company, page
            )
        return BranchPage(
            items=[BranchView.of(branch) for branch in found.items], next_cursor=found.next_cursor
        )

    @branches.get("/{branch_id}", response_model=BranchView, responses=READ)
    async def read_branch(
        access: Annotated[CompanyAccess, Depends(reader)],
        branch_id: str,
        current: Annotated[Runtime[IdentitySettings], Depends(runtime)],
    ) -> BranchView:
        """A branch of the company."""
        branch = BranchId(parse_id(branch_id))
        async with current.database.transaction(access.tenant) as transaction:
            found = await queries.branch(
                context.bind(transaction, current.outbox), access.tenant.company, branch
            )
        return BranchView.of(found)

    return branches
