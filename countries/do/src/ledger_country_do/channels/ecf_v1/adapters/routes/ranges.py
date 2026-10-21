"""``/v1/do/numbering_ranges``: the company's DGII-authorized e-NCF ranges (§8.6.3).

The company is the ``Ledger-Company`` header's (§9.7) and the mode ``Ledger-Mode``'s (or the API client's): a
range numbers documents of one mode, in an environment of that mode. Every route requires, as its first
dependency, the caller (401) with a role in the company (404) that reads its ranges (``do_ecf.ranges.read``,
403), identity answering through fiscal's directory (Decision 4); then what the request must carry.

A ``POST`` is answered from its ``Idempotency-Key`` first: a retry of a request that succeeded replays its
answer before the permission the change needs is judged. Only a new request needs ``do_ecf.ranges.manage``
(403). Whether the company is enrolled in the environment is asked of fiscal outside any unit (rule 13); then
the range is registered in the unit that claims the key: the key, the range, its audit row and fiscal's
``fiscal_channel.requirements.changed`` commit together, and a failure keeps nothing. Every answer of a range
carries its version as ``ETag``.
"""

from dataclasses import dataclass
from http import HTTPStatus
from typing import Annotated, Final, final

from fastapi import APIRouter, Depends, Query
lazy from fastapi import Request, Response

from ledger_country_do.channels.ecf_v1.adapters.postgres.unit import bind
from ledger_country_do.channels.ecf_v1.adapters.routes.models import (
    NumberingRangeView,
    parse_range_ref,
    range_ref,
)
from ledger_kernel.access import parse_permission
from ledger_kernel.tenancy import Tenant
from ledger_platform.http import (
    REPLAYED_HEADER,
    Idempotency,
    Page,
    allowed,
    answer,
    attempt_of,
    company_in_header,
    etag,
    idempotency_key,
    json_response,
    ledger_mode_of,
    page_of_items,
    page_query,
    problems,
    success,
)
lazy from ledger_country_do.channels.ecf_v1.adapters.routes.models import NewRange
lazy from ledger_country_do.channels.ecf_v1.application.ranges import NumberingRange, Ranging
lazy from ledger_country_do.channels.ecf_v1.domain.encf import EcfType
lazy from ledger_country_do.channels.ecf_v1.domain.environments import Environment
lazy from ledger_kernel.access import Permission
lazy from ledger_kernel.pagination import PageRequest
lazy from ledger_kernel.tenancy import Mode
lazy from ledger_platform.auth import CompanyAccess, PermissionSource
lazy from ledger_platform.data import BoundDatabase
lazy from ledger_platform.http import Authentication, StoredResponse
lazy from ledger_platform.http.idempotency import Attempt

PREFIX: Final = "/v1/do/numbering_ranges"
RANGES_READ: Final = parse_permission("do_ecf.ranges.read")
"""See the company's DGII-authorized e-NCF ranges."""
RANGES_MANAGE: Final = parse_permission("do_ecf.ranges.manage")
"""Register DGII-authorized e-NCF ranges."""
READ: Final = problems(400, 401, 403, 404, 422) | success(HTTPStatus.OK, "ETag")
LIST: Final = problems(400, 401, 403, 404, 422)
CREATE: Final = problems(400, 401, 403, 404, 409, 422) | success(
    HTTPStatus.CREATED, "Location", "ETag", REPLAYED_HEADER
)


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class RangesApi:
    """What the routes share, built once by the assembly: the use cases, the units, the caller's access."""

    database: BoundDatabase
    """The channel's units, in its schema and role: where a POST claims its key with its range."""
    ranging: Ranging
    authentication: Authentication
    permissions: PermissionSource
    """What a principal may do in a company: identity's answer, through fiscal's directory (Decision 4)."""


@final
@dataclass(frozen=True, slots=True)
class Acting:
    """The caller, a reader of the request's company's ranges, and the tenant it acts in."""

    access: CompanyAccess
    tenant: Tenant
    """The request's company, in the request's mode."""

    def attempt(self, request: Request, key: str, body: NewRange) -> Attempt:
        """The request's use of ``key``: its route, its body and its company."""
        return attempt_of(request, self.access.principal, key, body, company=self.tenant.company)


def created(registered: NumberingRange) -> StoredResponse:
    """A new range's answer as kept for replays: its view, its version and where it lives."""
    headers = {"ETag": etag(registered.version), "Location": f"{PREFIX}/{range_ref(registered.id)}"}
    return json_response(HTTPStatus.CREATED, NumberingRangeView.of(registered), headers)


def router(api: RangesApi) -> APIRouter:
    """The routes of numbering ranges."""
    ranges = APIRouter(prefix=PREFIX, tags=["numbering ranges"])
    database, ranging, idempotency = api.database, api.ranging, Idempotency()

    async def permission_source() -> PermissionSource:
        return api.permissions

    caller = api.authentication.caller
    reads = allowed(company_in_header(caller, permission_source), RANGES_READ)
    mode_of = ledger_mode_of(caller)

    async def reader(
        access: Annotated[CompanyAccess, Depends(reads)], mode: Annotated[Mode, Depends(mode_of)]
    ) -> Acting:
        """The caller, a reader of the company's ranges, in the request's mode."""
        return Acting(access, Tenant(access.tenant.organization, access.tenant.company, mode))

    async def replayed(acting: Acting, attempt: Attempt, needed: Permission) -> Response | None:
        """The answer stored for the caller's use of the key; else ``None``, once ``needed`` is held."""
        async with database.unit(acting.tenant) as unit:
            earlier = await idempotency.replayed(unit, attempt)
        if earlier is not None:
            return answer(earlier)
        acting.access.require(needed)
        return None

    @ranges.post("", status_code=HTTPStatus.CREATED, response_model=NumberingRangeView, responses=CREATE)
    async def register(
        acting: Annotated[Acting, Depends(reader)],
        key: Annotated[str, Depends(idempotency_key)],
        body: NewRange,
        request: Request,
    ) -> Response:
        """Register a range DGII authorized, for an environment the company is enrolled in, in its mode.

        Needs ``do_ecf.ranges.manage``, unless it retries a request that succeeded. Refused: an environment
        of the other mode, 422 ``do_ecf_environment_not_allowed``; one the company is not enrolled in, 422
        ``do_ecf_environment_not_enrolled``; a type other than 32 and 34 without ``expires_on``, 422
        ``do_ecf_range_expiry_required``; ``first`` above ``last``, 422 ``do_ecf_range_invalid``; a number
        shared with a range of the same type and environment, 409 ``do_ecf_range_overlaps``.
        """
        attempt = acting.attempt(request, key, body)
        earlier = await replayed(acting, attempt, RANGES_MANAGE)
        if earlier is not None:
            return earlier
        prepared = await ranging.prepare(acting.tenant, body.request())
        async with database.unit(acting.tenant) as unit:

            async def keep() -> StoredResponse:
                return created(await ranging.register(bind(unit), prepared))

            outcome = await idempotency.once(unit, attempt, keep)
        return answer(outcome)

    @ranges.get("", response_model=Page[NumberingRangeView], responses=LIST)
    async def list_ranges(
        acting: Annotated[Acting, Depends(reader)],
        page: Annotated[PageRequest, Depends(page_query)],
        environment: Annotated[Environment | None, Query(description="Only this environment's.")] = None,
        ecf_type: Annotated[EcfType | None, Query(description="Only this e-CF type's.")] = None,
    ) -> Page[NumberingRangeView]:
        """The company's ranges in the request's mode, oldest first."""
        found = await ranging.page(acting.tenant, page, environment=environment, ecf_type=ecf_type)
        return page_of_items(found, NumberingRangeView.of)

    @ranges.get("/{range_id}", response_model=NumberingRangeView, responses=READ)
    async def read_range(
        acting: Annotated[Acting, Depends(reader)], range_id: str, response: Response
    ) -> NumberingRangeView:
        """A range, with how many numbers it has left (``remaining``)."""
        found = await ranging.get(acting.tenant, parse_range_ref(range_id))
        response.headers["ETag"] = etag(found.version)
        return NumberingRangeView.of(found)

    return ranges
