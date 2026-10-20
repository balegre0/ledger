"""``/v1/fiscal/enrollments``: a company's enrollments in fiscal channels (§7.6).

The company is the ``Ledger-Company`` header's (§9.7) and the mode ``Ledger-Mode``'s (or the API client's):
an enrollment exists in one mode of one company. Every route requires, as its first dependency, the caller
(401) with a role in the company (404) that reads its enrollments (``fiscal.enrollments.read``, 403); then
what the request must carry (``Ledger-Mode``, ``Idempotency-Key``: 400, 422).

A ``POST`` is answered from its ``Idempotency-Key`` first: a retry of a request that succeeded replays its
answer before the permission the change needs is judged, so a caller downgraded to reading since gets it
still. Only a new request needs ``fiscal.enrollments.manage`` (403). Enrolling asks the channel what it
requires outside any unit (rule 13), then keeps the enrollment in the unit that claims the key: the key, the
enrollment, its event and its audit commit together, and a failure keeps nothing. Reading one enrollment
reviews it against what its channel requires now, and keeps the review if it changed anything (Decision
31); a list answers the enrollments as kept. Every answer of an enrollment carries its version as ``ETag``.
"""

from dataclasses import dataclass
from http import HTTPStatus
from typing import Annotated, Final, final

from fastapi import APIRouter, Depends
lazy from fastapi import Request, Response

from ledger_fiscal.adapters.http.models import (
    EnrollmentView,
    enrollment_ref,
    parse_enrollment_ref,
)
from ledger_fiscal.adapters.postgres.unit import bind
from ledger_fiscal.domain.enrollments import parse_reason
from ledger_fiscal.domain.permissions import ENROLLMENTS_MANAGE, ENROLLMENTS_READ
from ledger_kernel.ids import new_id
from ledger_kernel.tenancy import Tenant
from ledger_platform.http import (
    REPLAYED_HEADER,
    Page,
    allowed,
    answer,
    attempt_of,
    etag,
    idempotency_key,
    json_response,
    page_of_items,
    page_query,
    problems,
    success,
)
from ledger_platform.http.idempotency import Outcome
from ledger_platform.http.problems import PROBLEM_JSON, Problem, current_request_id
from ledger_platform.runtime.calls import Call
lazy from ledger_fiscal.adapters.http.context import ApiContext
lazy from ledger_fiscal.adapters.http.models import DisableBody, NewEnrollment
lazy from ledger_fiscal.application.enrolling import Enrolling
lazy from ledger_fiscal.domain.enrollments import Enrollment
lazy from ledger_kernel.access import Permission
lazy from ledger_kernel.pagination import PageRequest
lazy from ledger_kernel.tenancy import Mode
lazy from ledger_platform.auth import CompanyAccess
lazy from ledger_platform.http import StoredResponse
lazy from ledger_platform.http.idempotency import Attempt

PREFIX: Final = "/v1/fiscal/enrollments"
TAKEN: Final[dict[int | str, dict[str, object]]] = {
    HTTPStatus.CONFLICT.value: {
        "description": HTTPStatus.CONFLICT.phrase,
        "content": {
            PROBLEM_JSON: {
                "schema": {
                    "allOf": [
                        Problem.model_json_schema(),
                        {
                            "type": "object",
                            "properties": {
                                "existing_id": {
                                    "type": "string",
                                    "description": (
                                        "The enrollment holding the channel (fiscal_enrollment_exists only)."
                                    ),
                                }
                            },
                        },
                    ]
                }
            }
        },
    }
}
"""An enrollment's 409: a problem naming the enrollment that holds the channel in ``existing_id`` (§9.6)."""
READ: Final = problems(400, 401, 403, 404, 422) | success(HTTPStatus.OK, "ETag")
LIST: Final = problems(400, 401, 403, 404, 422)
CREATE: Final = (
    problems(400, 401, 403, 404, 422)
    | TAKEN
    | success(HTTPStatus.CREATED, "Location", "ETag", REPLAYED_HEADER)
)
CHANGE: Final = problems(400, 401, 403, 404, 422) | success(HTTPStatus.OK, "ETag", REPLAYED_HEADER)


@final
@dataclass(frozen=True, slots=True)
class Acting:
    """The caller, a reader of the request's company's enrollments, and the tenant it acts in."""

    access: CompanyAccess
    tenant: Tenant
    """The request's company, in the request's mode."""

    @property
    def call(self) -> Call:
        """The call the request makes of fiscal's use cases."""
        return Call(
            principal=self.access.principal,
            scope=self.tenant,
            request_id=current_request_id() or str(new_id()),
        )

    def attempt(self, request: Request, key: str, body: NewEnrollment | DisableBody) -> Attempt:
        """The request's use of ``key``: its route, its enrollment, its body and its company."""
        return attempt_of(request, self.access.principal, key, body, company=self.tenant.company)


def stored(status: HTTPStatus, enrollment: Enrollment) -> StoredResponse:
    """An enrollment's answer as kept for replays: its view and version, and where it lives once created."""
    headers = {"ETag": etag(enrollment.version)}
    if status is HTTPStatus.CREATED:
        headers["Location"] = f"{PREFIX}/{enrollment_ref(enrollment.id)}"
    return json_response(status, EnrollmentView.of(enrollment), headers)


async def _replayed(
    context: ApiContext, caller: Acting, attempt: Attempt, needed: Permission
) -> Response | None:
    """The answer stored for the caller's first use of the key; else ``None``, once ``needed`` is held."""
    async with context.database.unit(caller.tenant) as unit:
        earlier = await context.idempotency.stored(unit, attempt)
    if earlier is not None:
        return answer(Outcome(earlier, replayed=True))
    caller.access.require(needed)
    return None


def router(context: ApiContext) -> APIRouter:
    """The routes of enrollments."""
    enrollments = APIRouter(prefix=PREFIX, tags=["enrollments"])
    enrolling: Enrolling = context.enrolling
    reads, mode_of = allowed(context.in_company, ENROLLMENTS_READ), context.mode

    async def reader(
        access: Annotated[CompanyAccess, Depends(reads)], mode: Annotated[Mode, Depends(mode_of)]
    ) -> Acting:
        """The caller, a reader of the company's enrollments, in the request's mode."""
        return Acting(access, Tenant(access.tenant.organization, access.tenant.company, mode))

    @enrollments.post("", status_code=HTTPStatus.CREATED, response_model=EnrollmentView, responses=CREATE)
    async def enroll(
        caller: Annotated[Acting, Depends(reader)],
        key: Annotated[str, Depends(idempotency_key)],
        body: NewEnrollment,
        request: Request,
    ) -> Response:
        """Enroll the company, in the request's mode, on a channel and one of its environments.

        Needs ``fiscal.enrollments.manage``, unless it retries a request that succeeded. The answer says what
        the channel still requires (``requirements.currently_due``): the enrollment is ``pending`` until it
        requires nothing, then ``active`` (a live one also needs its authority authorization recorded). A
        channel that is not installed is 422 ``fiscal_channel_unknown``; an environment it does not have,
        ``fiscal_environment_unknown``; one of the other mode, ``fiscal_environment_not_allowed``. One
        enrollment that is not disabled per company, mode and channel: a second is 409
        ``fiscal_enrollment_exists``, its problem naming the first (``existing_id``).
        """
        attempt = caller.attempt(request, key, body)
        earlier = await _replayed(context, caller, attempt, ENROLLMENTS_MANAGE)
        if earlier is not None:
            return earlier
        prepared = await enrolling.prepare(caller.call, channel=body.channel, environment=body.environment)
        async with context.database.unit(caller.tenant) as unit:

            async def keep() -> StoredResponse:
                return stored(HTTPStatus.CREATED, await enrolling.enroll(bind(unit), prepared))

            outcome = await context.idempotency.once(unit, attempt, keep)
        return answer(outcome)

    @enrollments.get("", response_model=Page[EnrollmentView], responses=LIST)
    async def list_enrollments(
        caller: Annotated[Acting, Depends(reader)], page: Annotated[PageRequest, Depends(page_query)]
    ) -> Page[EnrollmentView]:
        """The company's enrollments in the request's mode, disabled ones too, oldest first, as kept."""
        return page_of_items(await enrolling.page(caller.call, page), EnrollmentView.of)

    @enrollments.get("/{enrollment_id}", response_model=EnrollmentView, responses=READ)
    async def read_enrollment(
        caller: Annotated[Acting, Depends(reader)], enrollment_id: str, response: Response
    ) -> EnrollmentView:
        """An enrollment, with what its channel requires now (kept, and the status it leads to)."""
        found = await enrolling.review(caller.call, parse_enrollment_ref(enrollment_id))
        response.headers["ETag"] = etag(found.version)
        return EnrollmentView.of(found)

    @enrollments.post("/{enrollment_id}/disable", response_model=EnrollmentView, responses=CHANGE)
    async def disable_enrollment(
        caller: Annotated[Acting, Depends(reader)],
        key: Annotated[str, Depends(idempotency_key)],
        enrollment_id: str,
        body: DisableBody,
        request: Request,
    ) -> Response:
        """Disable an enrollment for good, for a ``reason``; one disabled already is answered as it is.

        Needs ``fiscal.enrollments.manage``, unless it retries a request that succeeded. The company may then
        enroll on the channel again (in another environment, say).
        """
        attempt = caller.attempt(request, key, body)
        earlier = await _replayed(context, caller, attempt, ENROLLMENTS_MANAGE)
        if earlier is not None:
            return earlier
        enrollment, reason = parse_enrollment_ref(enrollment_id), parse_reason(body.reason)
        async with context.database.unit(caller.tenant) as unit:

            async def keep() -> StoredResponse:
                return stored(HTTPStatus.OK, await enrolling.disable(bind(unit), enrollment, reason))

            outcome = await context.idempotency.once(unit, attempt, keep)
        return answer(outcome)

    return enrollments
