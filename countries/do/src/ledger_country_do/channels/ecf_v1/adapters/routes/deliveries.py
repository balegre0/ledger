"""``/v1/do/deliveries/{invoice}/retry``: a failed delivery to the buyer started again (spec §8.6.5, §8.6.8).

The company is the ``Ledger-Company`` header's (§9.7) and the mode ``Ledger-Mode``'s (or the API
client's). Every route requires, as its first dependency, the caller (401) with a role in the company (404)
that manages its deliveries (``do_ecf.deliveries.manage``, 403), identity answering through fiscal's
directory (Decision 4).

A retry is answered from its ``Idempotency-Key`` first; a new one, in the unit that claims the key, starts
the delivery again (``failed`` to ``delivering``, seven days from now), audits it, publishes the e-CF's new
version and enqueues the delivery job: all commit together, or nothing does. An invoice without an e-CF in
the company and mode is 404 ``do_ecf_delivery_not_found``. A delivery that failed is retried, and so is one
stuck (``not_started`` or ``delivering``), once its job is done, dead or gone; any other, or one whose job
may still run (whatever its state), is 409 ``do_ecf_delivery_not_retryable``.
"""

from dataclasses import dataclass
from http import HTTPStatus
from typing import Annotated, Final, final
lazy from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Depends
lazy from fastapi import Request, Response

from ledger_country_do.channels.ecf_v1.adapters.postgres.unit import bind
from ledger_country_do.channels.ecf_v1.adapters.routes.models import DeliveryView, parse_invoice_ref
from ledger_fiscal_kit.drafts import DocumentRef
from ledger_kernel.access import parse_permission
from ledger_kernel.tenancy import Tenant
from ledger_platform.http import (
    REPLAYED_HEADER,
    Idempotency,
    allowed,
    answer,
    attempt_of,
    company_in_header,
    idempotency_key,
    json_response,
    ledger_mode_of,
    problems,
    success,
)
lazy from ledger_country_do.channels.ecf_v1.application.delivering import Delivering
lazy from ledger_kernel.tenancy import Mode
lazy from ledger_platform.auth import CompanyAccess, PermissionSource
lazy from ledger_platform.data import BoundDatabase
lazy from ledger_platform.http import Authentication, StoredResponse

PREFIX: Final = "/v1/do/deliveries"
DELIVERIES_MANAGE: Final = parse_permission("do_ecf.deliveries.manage")
"""Retry a delivery to a buyer; choose whether to authenticate with buyers."""
RETRY: Final = problems(400, 401, 403, 404, 409, 422) | success(HTTPStatus.OK, REPLAYED_HEADER)


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class DeliveriesApi:
    """What the delivery routes share, built once by the assembly: the use cases, the units, the access."""

    database: BoundDatabase
    """The channel's units, in its schema and role: where a retry claims its key with its change."""
    delivering: Delivering
    authentication: Authentication
    permissions: PermissionSource
    """What a principal may do in a company: identity's answer, through fiscal's directory (Decision 4)."""


@final
@dataclass(frozen=True, slots=True)
class Managing:
    """The caller, a manager of the request's company's deliveries, and the tenant it acts in."""

    access: CompanyAccess
    tenant: Tenant
    """The request's company, in the request's mode."""


def manager_of(api: DeliveriesApi) -> Callable[[CompanyAccess, Mode], Awaitable[Managing]]:
    """The dependency giving the caller, a manager of the company's deliveries, in the request's mode."""

    async def permission_source() -> PermissionSource:
        return api.permissions

    caller = api.authentication.caller
    manages = allowed(company_in_header(caller, permission_source), DELIVERIES_MANAGE)
    mode_of = ledger_mode_of(caller)

    async def manager(
        access: Annotated[CompanyAccess, Depends(manages)], mode: Annotated[Mode, Depends(mode_of)]
    ) -> Managing:
        return Managing(access, Tenant(access.tenant.organization, access.tenant.company, mode))

    return manager


def router(api: DeliveriesApi) -> APIRouter:
    """The route that retries a delivery."""
    deliveries = APIRouter(prefix=PREFIX, tags=["deliveries"])
    database, delivering, idempotency = api.database, api.delivering, Idempotency()
    manager = manager_of(api)

    @deliveries.post("/{invoice}/retry", response_model=DeliveryView, responses=RETRY)
    async def retry(
        managing: Annotated[Managing, Depends(manager)],
        key: Annotated[str, Depends(idempotency_key)],
        invoice: str,
        request: Request,
    ) -> Response:
        """Deliver the invoice's e-CF to its buyer again, after its delivery failed (see the module)."""
        attempt = attempt_of(request, managing.access.principal, key, company=managing.tenant.company)
        reference = DocumentRef(parse_invoice_ref(invoice))
        async with database.unit(managing.tenant) as unit:

            async def keep() -> StoredResponse:
                started = await delivering.retry(bind(unit), reference)
                return json_response(HTTPStatus.OK, DeliveryView.of(started))

            outcome = await idempotency.once(unit, attempt, keep)
        return answer(outcome)

    return deliveries
