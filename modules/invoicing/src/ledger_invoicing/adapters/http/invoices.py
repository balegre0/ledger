"""``/v1/invoices``: create, read, list, issue and void a company's commercial documents.

The company is the ``Ledger-Company`` header's (§9.7, Decision 1) and the mode ``Ledger-Mode``'s (or the API
client's): a document exists in one mode of one company. A route checks, in this order: the caller (401), its
role in the company (404) and its permission there (403, Decision 2), as dependencies; then what the request
must carry (``Ledger-Mode``, ``Idempotency-Key``, ``If-Match``) and its body (400, 422, 428).

A ``POST`` claims its ``Idempotency-Key`` in the unit of its change (``Idempotency.once``), so the key, the
document, its event and its audit commit together, and a failure keeps nothing. Creating and issuing ask
identity first (the company, its establishment), which no unit may be open for (rule 13): a retry is answered
from the key before identity is asked (``Idempotency.replayed``), and the change is then applied in the unit
that claims the key. Issuing and voiding name the versions they apply to in ``If-Match`` (412 when none is
current, 428 without); every answer of a document carries its version as ``ETag``.
"""

from dataclasses import dataclass
from http import HTTPStatus
from typing import Annotated, Final, final
lazy from collections.abc import Callable, Coroutine

from fastapi import APIRouter, Depends, Query
lazy from fastapi import Request, Response

from ledger_invoicing.adapters.http.context import call_of
from ledger_invoicing.adapters.http.models import InvoiceView
from ledger_invoicing.adapters.http.public import invoice_ref, parse_invoice_ref
from ledger_invoicing.adapters.postgres.unit import bind
from ledger_invoicing.application.ports import InvoiceFilters
from ledger_invoicing.domain.errors import InvoiceVersionStaleError
from ledger_invoicing.domain.permissions import INVOICES_CREATE, INVOICES_ISSUE, INVOICES_READ, INVOICES_VOID
from ledger_invoicing.domain.text import MAX_EXTERNAL_REFERENCE, parse_external_reference
from ledger_kernel.tenancy import Tenant
from ledger_platform.http import (
    REPLAYED_HEADER,
    Page,
    allowed,
    answer,
    attempt_of,
    etag,
    idempotency_key,
    if_match,
    if_match_required,
    json_response,
    page_of_items,
    page_query,
    precondition_checked,
    problems,
    success,
)
lazy from ledger_invoicing.adapters.http.context import ApiContext
lazy from ledger_invoicing.adapters.http.models import NewInvoice, VoidBody
lazy from ledger_invoicing.application.commands import Admits, Change
lazy from ledger_invoicing.domain.ids import InvoiceId
lazy from ledger_invoicing.domain.invoice import Invoice, InvoiceStatus
lazy from ledger_kernel.access import Permission
lazy from ledger_kernel.pagination import PageRequest
lazy from ledger_kernel.regimes import Direction, DocumentKind
lazy from ledger_kernel.tenancy import Mode
lazy from ledger_platform.auth import CompanyAccess
lazy from ledger_platform.http import IfMatch, StoredResponse
lazy from ledger_platform.http.idempotency import Attempt
lazy from ledger_platform.runtime.calls import Call

PREFIX: Final = "/v1/invoices"
READ: Final = problems(400, 401, 403, 404, 422) | success(HTTPStatus.OK, "ETag")
LIST: Final = problems(400, 401, 403, 404, 422)
CREATE: Final = problems(400, 401, 403, 404, 409, 422) | success(
    HTTPStatus.CREATED, "Location", "ETag", REPLAYED_HEADER
)
CHANGE: Final = problems(400, 401, 403, 404, 409, 412, 422, 428) | success(
    HTTPStatus.OK, "ETag", REPLAYED_HEADER
)


@final
@dataclass(frozen=True, slots=True)
class Acting:
    """The caller, allowed what the route needs in the request's company, and the tenant it acts in."""

    access: CompanyAccess
    tenant: Tenant
    """The request's company, in the request's mode."""

    @property
    def call(self) -> Call:
        """The call the request makes of invoicing's use cases."""
        return call_of(self.access, self.tenant)

    def attempt(self, request: Request, key: str, body: NewInvoice | VoidBody | None = None) -> Attempt:
        """The request's use of ``key``: its route, its document, its body and its company."""
        return attempt_of(request, self.access.principal, key, body, company=self.access.tenant.company)


@final
@dataclass(frozen=True, slots=True)
class Aim:
    """The document a change names in its path, and the versions ``If-Match`` lets it apply to."""

    invoice: InvoiceId
    admits: Admits


async def aimed(invoice_id: str, condition: Annotated[IfMatch, Depends(if_match)]) -> Aim:
    """The document of the path (404 for anything but its public id) and what ``If-Match`` admits (428)."""
    return Aim(parse_invoice_ref(invoice_id), condition.admits)


async def filtered(
    kind: Annotated[DocumentKind | None, Query(alias="type", description="Only of this type.")] = None,
    direction: Annotated[Direction | None, Query(description="Only of this direction.")] = None,
    status: Annotated[InvoiceStatus | None, Query(description="Only in this status.")] = None,
    external_reference: Annotated[
        str | None,
        Query(min_length=1, max_length=MAX_EXTERNAL_REFERENCE, description="Only the one of this key."),
    ] = None,
) -> InvoiceFilters:
    """Which documents a list holds: those of every condition its query names."""
    return InvoiceFilters(
        kind=kind,
        direction=direction,
        status=status,
        external_reference=None
        if external_reference is None
        else parse_external_reference(external_reference),
    )


def stored(status: HTTPStatus, invoice: Invoice) -> StoredResponse:
    """A document's answer as kept for replays: its view and version, and where it lives once created."""
    headers = {"ETag": etag(invoice.version)}
    if status is HTTPStatus.CREATED:
        headers["Location"] = f"{PREFIX}/{invoice_ref(invoice.id)}"
    return json_response(status, InvoiceView.of(invoice), headers)


async def _changed(
    context: ApiContext, acting: Acting, attempt: Attempt, change: Change, status: HTTPStatus
) -> Response:
    """``change`` applied in the unit that claims ``attempt``'s key: its answer, or the one stored."""
    async with context.database.unit(acting.tenant) as unit:

        async def apply() -> StoredResponse:
            return stored(status, await change.apply(bind(unit)))

        outcome = await context.idempotency.once(unit, attempt, apply)
    return answer(outcome)


async def _replayed(context: ApiContext, acting: Acting, attempt: Attempt) -> Response | None:
    """The answer a committed first use of the key stored: what a retry gets before identity is asked."""
    async with context.database.unit(acting.tenant) as unit:
        earlier = await context.idempotency.replayed(unit, attempt)
    return None if earlier is None else answer(earlier)


def router(context: ApiContext) -> APIRouter:
    """The routes of documents."""
    invoices = APIRouter(prefix=PREFIX, tags=["invoices"])
    commands, queries, mode_of = context.commands, context.queries, context.mode

    def acting(permission: Permission) -> Callable[[CompanyAccess, Mode], Coroutine[object, object, Acting]]:
        """The dependency giving the caller allowed ``permission`` in the company, and its tenant."""
        guard = allowed(context.in_company, permission)

        async def in_mode(
            access: Annotated[CompanyAccess, Depends(guard)], mode: Annotated[Mode, Depends(mode_of)]
        ) -> Acting:
            return Acting(access, Tenant(access.tenant.organization, access.tenant.company, mode))

        return in_mode

    reader, creator, issuer, voider = (
        acting(INVOICES_READ),
        acting(INVOICES_CREATE),
        acting(INVOICES_ISSUE),
        acting(INVOICES_VOID),
    )

    @invoices.post("", status_code=HTTPStatus.CREATED, response_model=InvoiceView, responses=CREATE)
    async def create_invoice(
        caller: Annotated[Acting, Depends(creator)],
        key: Annotated[str, Depends(idempotency_key)],
        body: NewInvoice,
        request: Request,
    ) -> Response:
        """Create a document of the company: issued at once, or a draft with ``draft: true``.

        A credit or debit note names the issued invoice it corrects (``corrects``). The business key
        (``external_reference``) is the company's once per mode and type: a duplicate is 409
        ``external_reference_taken``, its problem naming the holder (``existing_id``).
        """
        creation = body.creation(context.tax_ids)
        attempt = caller.attempt(request, key, body)
        earlier = await _replayed(context, caller, attempt)
        if earlier is not None:
            return earlier
        change = await commands.prepare_create(caller.call, creation)
        return await _changed(context, caller, attempt, change, HTTPStatus.CREATED)

    @invoices.get("", response_model=Page[InvoiceView], responses=LIST)
    async def list_invoices(
        caller: Annotated[Acting, Depends(reader)],
        page: Annotated[PageRequest, Depends(page_query)],
        filters: Annotated[InvoiceFilters, Depends(filtered)],
    ) -> Page[InvoiceView]:
        """The company's documents in the request's mode, oldest first, as the filters name them."""
        return page_of_items(await queries.documents(caller.call, page, filters), InvoiceView.of)

    @invoices.get("/{invoice_id}", response_model=InvoiceView, responses=READ)
    async def read_invoice(
        caller: Annotated[Acting, Depends(reader)], invoice_id: str, response: Response
    ) -> InvoiceView:
        """A document of the company in the request's mode, with its version as ``ETag``."""
        found = await queries.document(caller.call, parse_invoice_ref(invoice_id))
        response.headers["ETag"] = etag(found.version)
        return InvoiceView.of(found)

    @invoices.post(
        "/{invoice_id}/issue", response_model=InvoiceView, responses=CHANGE, openapi_extra=if_match_required()
    )
    async def issue_invoice(
        caller: Annotated[Acting, Depends(issuer)],
        key: Annotated[str, Depends(idempotency_key)],
        aim: Annotated[Aim, Depends(aimed)],
        request: Request,
    ) -> Response:
        """Issue a draft at a version ``If-Match`` names, priced again today and sold by the company as it is.

        One that is not a draft is 409 ``invoice_not_issuable``; one changed while it was priced, 409
        ``invoice_changed``.
        """
        attempt = caller.attempt(request, key)
        earlier = await _replayed(context, caller, attempt)
        if earlier is not None:
            return earlier
        with precondition_checked(InvoiceVersionStaleError):
            change = await commands.prepare_issue(caller.call, aim.invoice, admits=aim.admits)
        return await _changed(context, caller, attempt, change, HTTPStatus.OK)

    @invoices.post(
        "/{invoice_id}/void", response_model=InvoiceView, responses=CHANGE, openapi_extra=if_match_required()
    )
    async def void_invoice(
        caller: Annotated[Acting, Depends(voider)],
        key: Annotated[str, Depends(idempotency_key)],
        aim: Annotated[Aim, Depends(aimed)],
        request: Request,
        body: VoidBody | None = None,
    ) -> Response:
        """Void an issued document at a version ``If-Match`` names, for a ``reason`` if given.

        Refused (409 ``invoice_not_voidable``) unless it is issued, while a draft or issued note corrects it,
        and once a fiscal channel accepted it: a credit note corrects it then.
        """
        attempt = caller.attempt(request, key, body)
        reason = None if body is None else body.parsed()
        change = commands.prepare_void(caller.call, aim.invoice, reason, admits=aim.admits)
        with precondition_checked(InvoiceVersionStaleError):
            return await _changed(context, caller, attempt, change, HTTPStatus.OK)

    return invoices
