"""``/v1/companies/{company_id}/branches/{branch_id}/emission-points``: the emission points of a branch."""

from dataclasses import dataclass
from http import HTTPStatus
from typing import Annotated, final
lazy from collections.abc import Callable, Coroutine

from fastapi import APIRouter, Depends
lazy from fastapi import Request, Response

from ledger_identity.api.branches import OPEN, READ
from ledger_identity.api.models import EmissionPointPage, EmissionPointView
from ledger_identity.application import commands, queries
from ledger_identity.domain.ids import BranchId, EmissionPointId
from ledger_identity.domain.text import parse_code, parse_name
from ledger_kernel.access import Permission
from ledger_kernel.ids import parse_id
from ledger_platform.routes import allowed, answer, attempt_of, idempotency_key, json_response, page_request
lazy from ledger_identity.api.context import ApiContext
lazy from ledger_identity.api.models import NewEmissionPoint
lazy from ledger_identity.settings import IdentitySettings
lazy from ledger_kernel.pagination import PageRequest
lazy from ledger_platform.authorization import CompanyAccess
lazy from ledger_platform.idempotency import StoredResponse
lazy from ledger_platform.service import Runtime


@final
@dataclass(frozen=True, slots=True)
class InBranch:
    """The branch a request's path names, and what the caller may do in the branch's company."""

    access: CompanyAccess
    branch: BranchId


type BranchDependency = Callable[[str, CompanyAccess], Coroutine[object, object, InBranch]]
"""The dependency of a route under a branch: the path's branch, the caller allowed what the route does."""


def router(context: ApiContext) -> APIRouter:
    """The routes of emission points."""
    points = APIRouter(
        prefix="/v1/companies/{company_id}/branches/{branch_id}/emission-points", tags=["emission points"]
    )
    runtime = context.runtime

    def in_branch(permission: Permission) -> BranchDependency:
        """The path's branch, once the caller is known to hold ``permission`` in its company."""
        guard = allowed(context.in_company, permission)

        async def place(branch_id: str, access: Annotated[CompanyAccess, Depends(guard)]) -> InBranch:
            return InBranch(access, BranchId(parse_id(branch_id)))

        return place

    reader, manager = in_branch(Permission.COMPANY_READ), in_branch(Permission.COMPANY_MANAGE)

    @points.post("", status_code=HTTPStatus.CREATED, response_model=EmissionPointView, responses=OPEN)
    async def open_emission_point(
        place: Annotated[InBranch, Depends(manager)],
        key: Annotated[str, Depends(idempotency_key)],
        body: NewEmissionPoint,
        request: Request,
        current: Annotated[Runtime[IdentitySettings], Depends(runtime)],
    ) -> Response:
        """Open an emission point of the branch under a code the branch does not use yet."""
        access = place.access
        opening = commands.Opening(parse_code(body.code), parse_name(body.name))
        attempt = attempt_of(request, access.principal, key, body)
        async with current.database.transaction(access.tenant) as transaction:

            async def open_it() -> StoredResponse:
                unit = context.bind(transaction, current.outbox)
                opened = await commands.open_emission_point(
                    unit,
                    access.tenant.company,
                    place.branch,
                    opening,
                    actor=access.principal.subject,
                )
                branch_path = f"/v1/companies/{opened.tenant.company}/branches/{opened.branch}"
                location = f"{branch_path}/emission-points/{opened.id}"
                return json_response(HTTPStatus.CREATED, EmissionPointView.of(opened), {"Location": location})

            outcome = await context.idempotency.once(transaction, attempt, open_it)
        return answer(outcome)

    @points.get("", response_model=EmissionPointPage, responses=READ)
    async def list_emission_points(
        place: Annotated[InBranch, Depends(reader)],
        page: Annotated[PageRequest, Depends(page_request)],
        current: Annotated[Runtime[IdentitySettings], Depends(runtime)],
    ) -> EmissionPointPage:
        """The emission points of the branch."""
        access = place.access
        async with current.database.transaction(access.tenant) as transaction:
            unit = context.bind(transaction, current.outbox)
            found = await queries.emission_points(unit, access.tenant.company, place.branch, page)
        items = [EmissionPointView.of(point) for point in found.items]
        return EmissionPointPage(items=items, next_cursor=found.next_cursor)

    @points.get("/{emission_point_id}", response_model=EmissionPointView, responses=READ)
    async def read_emission_point(
        place: Annotated[InBranch, Depends(reader)],
        emission_point_id: str,
        current: Annotated[Runtime[IdentitySettings], Depends(runtime)],
    ) -> EmissionPointView:
        """An emission point of the branch."""
        access = place.access
        point = EmissionPointId(parse_id(emission_point_id))
        async with current.database.transaction(access.tenant) as transaction:
            unit = context.bind(transaction, current.outbox)
            found = await queries.emission_point(unit, access.tenant.company, place.branch, point)
        return EmissionPointView.of(found)

    return points
