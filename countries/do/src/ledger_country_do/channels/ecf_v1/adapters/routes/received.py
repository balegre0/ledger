"""``/v1/do/received_documents``: the e-CF other emitters sent the company, and its decision of each.

Spec §8.6.5 step 6, §8.6.8. The company is the ``Ledger-Company`` header's (§9.7) and the mode
``Ledger-Mode``'s (or the API client's): a document received in an environment is of the mode that
environment serves. Every route requires, as its first dependency, the caller (401) with a role in the
company (404) that sees what it received (``do_ecf.received_documents.read``, 403), identity answering
through fiscal's directory (Decision 4). A document is the company's alone: another company's (or another
mode's) is 404 ``do_ecf_received_document_not_found``, never told apart from one that does not exist.

A list is filtered by environment, by issuer (its RNC or cédula) and by decision (``none``, ``approved``,
``rejected``). A document answers what its receiver read of it and its approval (never its XML), with its
version as ``ETag``.

A decision (``POST .../{id}/approval``) is answered from its ``Idempotency-Key`` first: a retry of a request
that succeeded replays its answer before the permission the change needs is judged. Only a new request needs
``do_ecf.received_documents.manage`` (403). In the unit that claims the key the decision is kept, audited and
the ``do_ecf.approve`` job enqueued: all commit together, or nothing does. It is answered 202: the ACECF is
signed and sent to the emitter and to DGII by the job, and the document shows where each stands. A second
decision is 409 ``do_ecf_approval_exists``; a rejection without a reason (or an approval with one) 422
``do_ecf_approval_reason_invalid``.
"""

from dataclasses import dataclass
from http import HTTPStatus
from typing import Annotated, Final, final

from fastapi import APIRouter, Depends, Query
lazy from fastapi import Request, Response

from ledger_country_do.channels.ecf_v1.adapters.postgres.unit import bind
from ledger_country_do.channels.ecf_v1.adapters.routes.models import (
    ReceivedDocumentView,
    parse_received_ref,
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
lazy from ledger_country_do.channels.ecf_v1.adapters.routes.models import ApprovalBody
lazy from ledger_country_do.channels.ecf_v1.application.approving import CommercialApprovals
lazy from ledger_country_do.channels.ecf_v1.domain.approval import Decided
lazy from ledger_country_do.channels.ecf_v1.domain.environments import Environment
lazy from ledger_kernel.pagination import PageRequest
lazy from ledger_kernel.tenancy import Mode
lazy from ledger_platform.auth import CompanyAccess, PermissionSource
lazy from ledger_platform.data import BoundDatabase
lazy from ledger_platform.http import Authentication, StoredResponse

PREFIX: Final = "/v1/do/received_documents"
RECEIVED_READ: Final = parse_permission("do_ecf.received_documents.read")
"""See the e-CF other emitters sent the company."""
RECEIVED_MANAGE: Final = parse_permission("do_ecf.received_documents.manage")
"""Approve or reject received e-CF commercially."""
TAX_ID: Final = r"^[0-9]{9}([0-9]{2})?$"
"""An RNC (nine digits) or a cédula (eleven)."""
READ: Final = problems(400, 401, 403, 404, 422) | success(HTTPStatus.OK, "ETag")
LIST: Final = problems(400, 401, 403, 404, 422)
DECIDE: Final = problems(400, 401, 403, 404, 409, 422) | success(HTTPStatus.ACCEPTED, "ETag", REPLAYED_HEADER)


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class ReceivedApi:
    """What the routes share, built once by the assembly: the use cases, the units, the caller's access."""

    database: BoundDatabase
    """The channel's units, in its schema and role: where a decision claims its key with its change."""
    approvals: CommercialApprovals
    authentication: Authentication
    permissions: PermissionSource
    """What a principal may do in a company: identity's answer, through fiscal's directory (Decision 4)."""


@final
@dataclass(frozen=True, slots=True)
class Reading:
    """The caller, a reader of the request's company's received documents, and the tenant it acts in."""

    access: CompanyAccess
    tenant: Tenant
    """The request's company, in the request's mode."""


def decided(document: ReceivedDocumentView) -> StoredResponse:
    """A decision's answer as kept for replays: the document's view and its version."""
    return json_response(HTTPStatus.ACCEPTED, document, {"ETag": etag(document.version)})


def router(api: ReceivedApi) -> APIRouter:
    """The routes of received documents."""
    received = APIRouter(prefix=PREFIX, tags=["received documents"])
    database, approvals, idempotency = api.database, api.approvals, Idempotency()

    async def permission_source() -> PermissionSource:
        return api.permissions

    caller = api.authentication.caller
    reads = allowed(company_in_header(caller, permission_source), RECEIVED_READ)
    mode_of = ledger_mode_of(caller)

    async def reader(
        access: Annotated[CompanyAccess, Depends(reads)], mode: Annotated[Mode, Depends(mode_of)]
    ) -> Reading:
        """The caller, a reader of the company's received documents, in the request's mode."""
        return Reading(access, Tenant(access.tenant.organization, access.tenant.company, mode))

    @received.get("", response_model=Page[ReceivedDocumentView], responses=LIST)
    async def list_received(
        reading: Annotated[Reading, Depends(reader)],
        page: Annotated[PageRequest, Depends(page_query)],
        environment: Annotated[Environment | None, Query(description="Only this environment's.")] = None,
        issuer: Annotated[
            str | None, Query(pattern=TAX_ID, description="Only this emitter's (its RNC or cédula).")
        ] = None,
        approval: Annotated[
            Decided | None, Query(description="Only those decided so: none, approved or rejected.")
        ] = None,
    ) -> Page[ReceivedDocumentView]:
        """The documents the company received in the request's mode, oldest first."""
        found = await approvals.page(
            reading.tenant, page, environment=environment, issuer=issuer, decided=approval
        )
        return page_of_items(found, ReceivedDocumentView.of)

    @received.get("/{document_id}", response_model=ReceivedDocumentView, responses=READ)
    async def read_received(
        reading: Annotated[Reading, Depends(reader)], document_id: str, response: Response
    ) -> ReceivedDocumentView:
        """A document the company received: what its receiver read of it, and its approval."""
        found = await approvals.get(reading.tenant, parse_received_ref(document_id))
        response.headers["ETag"] = etag(found.version)
        return ReceivedDocumentView.of(found)

    @received.post(
        "/{document_id}/approval",
        status_code=HTTPStatus.ACCEPTED,
        response_model=ReceivedDocumentView,
        responses=DECIDE,
    )
    async def decide(
        reading: Annotated[Reading, Depends(reader)],
        key: Annotated[str, Depends(idempotency_key)],
        document_id: str,
        body: ApprovalBody,
        request: Request,
    ) -> Response:
        """Approve or reject a document the company received; the ACECF goes to its emitter and DGII.

        Needs ``do_ecf.received_documents.manage``, unless it retries a request that succeeded (see the
        module).
        """
        attempt = attempt_of(request, reading.access.principal, key, body, company=reading.tenant.company)
        async with database.unit(reading.tenant) as unit:
            earlier = await idempotency.replayed(unit, attempt)
        if earlier is not None:
            return answer(earlier)
        reading.access.require(RECEIVED_MANAGE)
        document = parse_received_ref(document_id)
        asked = body.request()
        async with database.unit(reading.tenant) as unit:

            async def keep() -> StoredResponse:
                kept = await approvals.request(bind(unit), document, asked)
                return decided(ReceivedDocumentView.of(kept))

            outcome = await idempotency.once(unit, attempt, keep)
        return answer(outcome)

    return received
