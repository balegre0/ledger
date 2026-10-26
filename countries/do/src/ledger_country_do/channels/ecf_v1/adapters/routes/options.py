"""``/v1/do/delivery_options``: how the company delivers to its buyers, in the request's mode (Decision 19).

``buyer_authentication`` (off by default): whether a buyer's authentication seed is signed with the
company's certificate for a token. Off, no seed is ever signed (F14: a hostile receiver could relay DGII's
own seed), and a buyer that requires a token fails its delivery with
``do_ecf_buyer_requires_authentication``; the client may switch it on and retry.

Both routes require the caller with a role in the company that manages its deliveries
(``do_ecf.deliveries.manage``), in the request's mode. The ``PUT`` sets a value: idempotent by itself, it
takes neither ``If-Match`` nor ``Idempotency-Key`` (§9.7); a change is audited (``delivery_options.set``).
"""

from typing import Annotated, Final

from fastapi import APIRouter, Depends

from ledger_country_do.channels.ecf_v1.adapters.postgres.unit import bind
from ledger_country_do.channels.ecf_v1.adapters.routes.deliveries import manager_of
from ledger_country_do.channels.ecf_v1.adapters.routes.models import DeliveryOptionsView
from ledger_country_do.channels.ecf_v1.application.documents import DeliveryOptions
from ledger_kernel.tenancy import Mode
from ledger_platform.http import problems
lazy from ledger_country_do.channels.ecf_v1.adapters.routes.deliveries import DeliveriesApi, Managing
lazy from ledger_country_do.channels.ecf_v1.adapters.routes.models import DeliveryOptionsBody

PATH: Final = "/v1/do/delivery_options"


def router(api: DeliveriesApi) -> APIRouter:
    """The routes of the company's delivery options."""
    options = APIRouter(prefix=PATH, tags=["deliveries"])
    database, delivering = api.database, api.delivering
    manager = manager_of(api)

    @options.get("", response_model=DeliveryOptionsView, responses=problems(400, 401, 403, 404, 422))
    async def read_options(managing: Annotated[Managing, Depends(manager)]) -> DeliveryOptionsView:
        """The company's delivery options in the request's mode."""
        found = await delivering.options(managing.tenant)
        return DeliveryOptionsView.of(found, livemode=managing.tenant.mode is Mode.LIVE)

    @options.put("", response_model=DeliveryOptionsView, responses=problems(400, 401, 403, 404, 422))
    async def set_options(
        managing: Annotated[Managing, Depends(manager)], body: DeliveryOptionsBody
    ) -> DeliveryOptionsView:
        """Set the company's delivery options in the request's mode (see the module)."""
        wanted = DeliveryOptions(buyer_authentication=body.buyer_authentication)
        async with database.unit(managing.tenant) as unit:
            kept = await delivering.set_options(bind(unit), wanted, managing.tenant.company)
        return DeliveryOptionsView.of(kept, livemode=managing.tenant.mode is Mode.LIVE)

    return options
