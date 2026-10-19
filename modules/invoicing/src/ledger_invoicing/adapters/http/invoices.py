"""``/v1/invoices``: create, read, list, issue and void a company's commercial documents.

The company is the ``Ledger-Company`` header's (§9.7, Decision 1) and the mode ``Ledger-Mode``'s (or the API
client's): a document exists in one mode of one company. Every route requires, as its first dependency, the
caller (401) with a role in the company (404) that reads its documents (``invoicing.invoices.read``, 403);
then what the request must carry (``Ledger-Mode``, ``Idempotency-Key``, ``If-Match``: 400, 428).

A ``POST`` is answered from its ``Idempotency-Key`` first: a retry of a request that succeeded replays its
answer (``Idempotency.replayed``, the caller's own: the key is the caller's, on its route, in its mode, and
the request's company is part of what it fingerprints) before the permission the change needs is judged, so a
caller downgraded to reading, or a company deactivated, since the first answer still gets it. Only a new
request needs that permission (create, issue or void: 403) and has what its body says read (tax ids, text,
the document: 422). A body's shape is the framework's to check, before the route runs: a malformed body is
422 ``invalid_request`` even from a caller without the permission, which tells it nothing (the problem
repeats nothing sent). An ``Idempotency-Key`` is the caller's in its organization and mode, whatever the
company: the same key for another company's request is 422 ``idempotency_key_reused``, so a caller acting
for several companies keeps a key per company (§9.5). Creating and issuing then ask identity (the company,
its establishment), which no unit may be open for (rule 13), and the change is applied in the unit that
claims the key (``Idempotency.once``): the key, the document, its event and its audit commit together, and a
failure keeps nothing. Issuing and voiding name the versions they apply to in ``If-Match`` (412 when none is
current); every answer of a document carries its version as ``ETag``.
"""

from dataclasses import dataclass
from http import HTTPStatus
from typing import Annotated, Final, final

from fastapi import APIRouter, Depends, Query
lazy from fastapi import Request, Response

from ledger_invoicing.adapters.http.context import call_of
from ledger_invoicing.adapters.http.models import InvoiceView
from ledger_invoicing.adapters.http.public import INVOICE_REF_PATTERN, invoice_ref, parse_invoice_ref
from ledger_invoicing.adapters.postgres.unit import bind
from ledger_invoicing.application.ports import InvoiceFilters
from ledger_invoicing.domain.errors import InvoiceVersionStaleError
from ledger_invoicing.domain.permissions import INVOICES_CREATE, INVOICES_ISSUE, INVOICES_READ, INVOICES_VOID
from ledger_invoicing.domain.text import MAX_EXTERNAL_REFERENCE, parse_external_reference
from ledger_kernel.tenancy import Tenant
from ledger_platform.http import (
    REPLAYED_HEADER,
    Page,
    Problem,
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
from ledger_platform.http.problems import PROBLEM_JSON
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
                                    "pattern": INVOICE_REF_PATTERN,
                                    "description": (
                                        "The document holding the external_reference "
                                        "(external_reference_taken only)."
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
"""A creation's 409: a problem, with the holder of a taken business key in ``existing_id`` (§9.6)."""
READ: Final = problems(400, 401, 403, 404, 422) | success(HTTPStatus.OK, "ETag")
LIST: Final = problems(400, 401, 403, 404, 422)
CREATE: Final = (
    problems(400, 401, 403, 404, 422)
    | TAKEN
    | success(HTTPStatus.CREATED, "Location", "ETag", REPLAYED_HEADER)
)
CHANGE: Final = problems(400, 401, 403, 404, 409, 412, 422, 428) | success(
    HTTPStatus.OK, "ETag", REPLAYED_HEADER
)


@final
@dataclass(frozen=True, slots=True)
class Acting:
    """The caller, a reader of the request's company's documents, and the tenant it acts in."""

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


async def _replayed(
    context: ApiContext, caller: Acting, attempt: Attempt, needed: Permission
) -> Response | None:
    """The answer the caller's committed first use of the key stored; else ``None``, once ``needed`` is held.

    A retry is replayed whatever the caller may do now, as long as it reads the company's documents (the
    route's dependency): only a new request needs ``needed`` (``PermissionDeniedError``, 403, without it).
    """
    async with context.database.unit(caller.tenant) as unit:
        earlier = await context.idempotency.replayed(unit, attempt)
    if earlier is not None:
        return answer(earlier)
    caller.access.require(needed)
    return None


async def _changed(context: ApiContext, attempt: Attempt, change: Change, status: HTTPStatus) -> Response:
    """``change`` applied in a unit of its tenant that claims ``attempt``'s key: its answer or the stored one.

    The tenant is the change's (the call's), which is the request's: what is changed is never decided twice.
    """
    async with context.database.unit(change.tenant) as unit:

        async def apply() -> StoredResponse:
            return stored(status, await change.apply(bind(unit)))

        outcome = await context.idempotency.once(unit, attempt, apply)
    return answer(outcome)


def router(context: ApiContext) -> APIRouter:
    """The routes of documents."""
    invoices = APIRouter(prefix=PREFIX, tags=["invoices"])
    commands, queries, mode_of = context.commands, context.queries, context.mode
    reads = allowed(context.in_company, INVOICES_READ)

    async def reader(
        access: Annotated[CompanyAccess, Depends(reads)], mode: Annotated[Mode, Depends(mode_of)]
    ) -> Acting:
        """The caller, a reader of the company's documents, in the request's mode."""
        return Acting(access, Tenant(access.tenant.organization, access.tenant.company, mode))

    @invoices.post("", status_code=HTTPStatus.CREATED, response_model=InvoiceView, responses=CREATE)
    async def create_invoice(
        caller: Annotated[Acting, Depends(reader)],
        key: Annotated[str, Depends(idempotency_key)],
        body: NewInvoice,
        request: Request,
    ) -> Response:
        """Create a document of the company: issued at once, or a draft with ``draft: true``.

        Needs ``invoicing.invoices.create``, unless it retries a request that succeeded. A credit or debit
        note names the issued invoice it corrects (``corrects``). The business key
        (``external_reference``) is the company's once per mode and type: a duplicate is 409
        ``external_reference_taken``, its problem naming the holder (``existing_id``).
        """
        attempt = caller.attempt(request, key, body)
        earlier = await _replayed(context, caller, attempt, INVOICES_CREATE)
        if earlier is not None:
            return earlier
        change = await commands.prepare_create(caller.call, body.creation(context.tax_ids))
        return await _changed(context, attempt, change, HTTPStatus.CREATED)

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
        caller: Annotated[Acting, Depends(reader)],
        key: Annotated[str, Depends(idempotency_key)],
        aim: Annotated[Aim, Depends(aimed)],
        request: Request,
    ) -> Response:
        """Issue a draft at a version ``If-Match`` names, priced again today and sold by the company as it is.

        Needs ``invoicing.invoices.issue``, unless it retries a request that succeeded. One that is not a
        draft is 409 ``invoice_not_issuable``; one changed while it was priced, 409 ``invoice_changed``.
        """
        attempt = caller.attempt(request, key)
        earlier = await _replayed(context, caller, attempt, INVOICES_ISSUE)
        if earlier is not None:
            return earlier
        with precondition_checked(InvoiceVersionStaleError):
            change = await commands.prepare_issue(caller.call, aim.invoice, admits=aim.admits)
        return await _changed(context, attempt, change, HTTPStatus.OK)

    @invoices.post(
        "/{invoice_id}/void", response_model=InvoiceView, responses=CHANGE, openapi_extra=if_match_required()
    )
    async def void_invoice(
        caller: Annotated[Acting, Depends(reader)],
        key: Annotated[str, Depends(idempotency_key)],
        aim: Annotated[Aim, Depends(aimed)],
        request: Request,
        body: VoidBody | None = None,
    ) -> Response:
        """Void an issued document at a version ``If-Match`` names, for a ``reason`` if given.

        Needs ``invoicing.invoices.void``, unless it retries a request that succeeded. Refused (409
        ``invoice_not_voidable``) unless it is issued, while a draft or issued note corrects it, and once a
        fiscal channel accepted it: a credit note corrects it then.
        """
        attempt = caller.attempt(request, key, body)
        earlier = await _replayed(context, caller, attempt, INVOICES_VOID)
        if earlier is not None:
            return earlier
        reason = None if body is None else body.parsed()
        change = commands.prepare_void(caller.call, aim.invoice, reason, admits=aim.admits)
        with precondition_checked(InvoiceVersionStaleError):
            return await _changed(context, attempt, change, HTTPStatus.OK)

    return invoices
