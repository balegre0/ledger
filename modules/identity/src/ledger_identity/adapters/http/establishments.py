"""``/v1/companies/{company_id}/establishments``: a company's establishments, shared by both modes.

Opening one is a ``POST``: it acts in ``Ledger-Mode``, which keeps its ``Idempotency-Key``. An establishment
without a time zone of its own works in the one of the company's ``MAIN`` establishment (§7.1).
"""

from http import HTTPStatus
from typing import Annotated, Final

from fastapi import APIRouter, Depends
lazy from fastapi import Request, Response

from ledger_identity.adapters.http.models import EstablishmentView
from ledger_identity.adapters.http.public import (
    company_ref,
    establishment_ref,
    parse_establishment_ref,
)
from ledger_identity.adapters.postgres.unit import bind
from ledger_identity.application import commands, queries
from ledger_identity.domain.roles import COMPANIES_MANAGE, COMPANIES_READ
from ledger_identity.domain.text import parse_code, parse_name, parse_time_zone
from ledger_kernel.tenancy import Tenant
from ledger_platform.http import (
    REPLAYED_HEADER,
    Page,
    allowed,
    answer,
    attempt_of,
    idempotency_key,
    json_response,
    page_of_items,
    page_query,
    problems,
    success,
)
lazy from ledger_identity.adapters.http.context import ApiContext
lazy from ledger_identity.adapters.http.models import NewEstablishment
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
    """The routes of establishments."""
    establishments = APIRouter(prefix="/v1/companies/{company_id}/establishments", tags=["establishments"])
    database, mode_of = context.database, context.mode
    reader, manager = (
        allowed(context.in_company, COMPANIES_READ),
        allowed(context.in_company, COMPANIES_MANAGE),
    )

    @establishments.post("", status_code=HTTPStatus.CREATED, response_model=EstablishmentView, responses=OPEN)
    async def open_establishment(
        access: Annotated[CompanyAccess, Depends(manager)],
        mode: Annotated[Mode, Depends(mode_of)],
        key: Annotated[str, Depends(idempotency_key)],
        body: NewEstablishment,
        request: Request,
    ) -> Response:
        """Open an establishment of the company under a code it does not use yet."""
        time_zone = None if body.time_zone is None else parse_time_zone(body.time_zone)
        opening = commands.Opening(parse_code(body.code), parse_name(body.name), time_zone)
        attempt = attempt_of(request, access.principal, key, body, company=access.tenant.company)
        async with database.unit(moded(access, mode)) as unit:

            async def open_it() -> StoredResponse:
                opened = await commands.open_establishment(
                    bind(unit), access.tenant.company, opening, actor=access.principal.subject
                )
                company = company_ref(opened.tenant.company)
                location = f"/v1/companies/{company}/establishments/{establishment_ref(opened.id)}"
                return json_response(HTTPStatus.CREATED, EstablishmentView.of(opened), {"Location": location})

            outcome = await context.idempotency.once(unit, attempt, open_it)
        return answer(outcome)

    @establishments.get("", response_model=Page[EstablishmentView], responses=READ)
    async def list_establishments(
        access: Annotated[CompanyAccess, Depends(reader)],
        page: Annotated[PageRequest, Depends(page_query)],
    ) -> Page[EstablishmentView]:
        """The company's establishments."""
        async with database.unit(access.tenant) as unit:
            found = await queries.establishments(bind(unit), access.tenant.company, page)
        return page_of_items(found, EstablishmentView.of)

    @establishments.get("/{establishment_id}", response_model=EstablishmentView, responses=READ)
    async def read_establishment(
        access: Annotated[CompanyAccess, Depends(reader)], establishment_id: str
    ) -> EstablishmentView:
        """An establishment of the company."""
        wanted = parse_establishment_ref(establishment_id)
        async with database.unit(access.tenant) as unit:
            found = await queries.establishment(bind(unit), access.tenant.company, wanted)
        return EstablishmentView.of(found)

    return establishments
