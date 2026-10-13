"""``/v1/companies/{company_id}/branches/{branch_id}/emission-points``: the emission points of a branch."""

from dataclasses import dataclass
from http import HTTPStatus
from typing import Annotated, final
lazy from collections.abc import Callable, Coroutine

from fastapi import APIRouter, Depends
lazy from fastapi import Request, Response

from ledger_identity.adapters.http.branches import OPEN, READ, moded
from ledger_identity.adapters.http.models import EmissionPointView
from ledger_identity.adapters.postgres.unit import bind
from ledger_identity.application import commands, queries
from ledger_identity.domain.ids import BranchId, EmissionPointId
from ledger_identity.domain.text import parse_code, parse_name
from ledger_kernel.access import Permission
from ledger_kernel.ids import parse_uuid
from ledger_platform.http import (
    Page,
    allowed,
    answer,
    attempt_of,
    idempotency_key,
    json_response,
    ledger_mode,
    page_of_items,
    page_query,
)
lazy from ledger_identity.adapters.http.context import ApiContext
lazy from ledger_identity.adapters.http.models import NewEmissionPoint
lazy from ledger_kernel.pagination import PageRequest
lazy from ledger_kernel.tenancy import Mode
lazy from ledger_platform.auth import CompanyAccess
lazy from ledger_platform.http import StoredResponse


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
    database = context.database

    def in_branch(permission: Permission) -> BranchDependency:
        """The path's branch, once the caller is known to hold ``permission`` in its company."""
        guard = allowed(context.in_company, permission)

        async def place(branch_id: str, access: Annotated[CompanyAccess, Depends(guard)]) -> InBranch:
            return InBranch(access, BranchId(parse_uuid(branch_id)))

        return place

    reader, manager = in_branch(Permission.COMPANY_READ), in_branch(Permission.COMPANY_MANAGE)

    @points.post("", status_code=HTTPStatus.CREATED, response_model=EmissionPointView, responses=OPEN)
    async def open_emission_point(
        place: Annotated[InBranch, Depends(manager)],
        mode: Annotated[Mode, Depends(ledger_mode)],
        key: Annotated[str, Depends(idempotency_key)],
        body: NewEmissionPoint,
        request: Request,
    ) -> Response:
        """Open an emission point of the branch under a code the branch does not use yet.

        The request acts in ``Ledger-Mode``: its ``Idempotency-Key`` is kept per mode.
        """
        access = place.access
        opening = commands.Opening(parse_code(body.code), parse_name(body.name))
        attempt = attempt_of(request, access.principal, key, body)
        async with database.unit(moded(access, mode)) as unit:

            async def open_it() -> StoredResponse:
                opened = await commands.open_emission_point(
                    bind(unit),
                    access.tenant.company,
                    place.branch,
                    opening,
                    actor=access.principal.subject,
                )
                branch_path = f"/v1/companies/{opened.tenant.company}/branches/{opened.branch}"
                location = f"{branch_path}/emission-points/{opened.id}"
                return json_response(HTTPStatus.CREATED, EmissionPointView.of(opened), {"Location": location})

            outcome = await context.idempotency.once(unit, attempt, open_it)
        return answer(outcome)

    @points.get("", response_model=Page[EmissionPointView], responses=READ)
    async def list_emission_points(
        place: Annotated[InBranch, Depends(reader)],
        page: Annotated[PageRequest, Depends(page_query)],
    ) -> Page[EmissionPointView]:
        """The emission points of the branch."""
        access = place.access
        async with database.unit(access.tenant) as unit:
            found = await queries.emission_points(bind(unit), access.tenant.company, place.branch, page)
        return page_of_items(found, EmissionPointView.of)

    @points.get("/{emission_point_id}", response_model=EmissionPointView, responses=READ)
    async def read_emission_point(
        place: Annotated[InBranch, Depends(reader)], emission_point_id: str
    ) -> EmissionPointView:
        """An emission point of the branch."""
        access = place.access
        point = EmissionPointId(parse_uuid(emission_point_id))
        async with database.unit(access.tenant) as unit:
            found = await queries.emission_point(bind(unit), access.tenant.company, place.branch, point)
        return EmissionPointView.of(found)

    return points
