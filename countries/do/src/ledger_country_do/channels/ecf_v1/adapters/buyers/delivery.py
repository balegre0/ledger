"""``Buyers`` over the buyer client: what the delivery job's use case sees of a delivery (spec §8.6.5).

The client's outcomes become the port's values, the seeds signed carried by each: a receipt that holds
(``BuyerReceipt``), one refused (``BuyerReceiptRefused``), no receiver (``NoReceiver``). Every failure is the
buyer out of reach for now (``BuyerUnreached``, its code): the buyer's receiver unavailable (or requiring the
authentication the company keeps off: ``needs_authentication``), an endpoint in DGII's directory that may
not be reached (the buyer may correct its entry), and DGII's directory itself out of reach or its token not
signable (the company's certificate).
"""

from typing import final, override

from ledger_country_do.channels.ecf_v1.adapters.buyers.client import (
    BuyerRequiresAuthenticationError,
    BuyerUnavailableError,
    Delivered,
    NotApplicable,
)
from ledger_country_do.channels.ecf_v1.adapters.buyers.endpoints import BuyerEndpointRefusedError
from ledger_country_do.channels.ecf_v1.application.ports import (
    BuyerReceipt,
    BuyerReceiptRefused,
    Buyers,
    BuyerUnreached,
    NoReceiver,
)
from ledger_country_do.channels.ecf_v1.domain.errors import EcfConflictError, EcfUnavailableError
lazy from ledger_country_do.channels.ecf_v1.adapters.buyers.client import BuyerClient
lazy from ledger_country_do.channels.ecf_v1.application.ports import DeliveryDocument, Handed, SigningAs
lazy from ledger_country_do.channels.ecf_v1.domain.environments import Environment


@final
class BuyerDeliveries(Buyers):
    """``Buyers`` over an open ``BuyerClient`` (see the module)."""

    __slots__ = ("_client",)

    def __init__(self, client: BuyerClient) -> None:
        self._client = client

    @override
    async def deliver(
        self,
        who: SigningAs,
        environment: Environment,
        document: DeliveryDocument,
        signed: bytes,
        *,
        authenticate: bool,
    ) -> Handed:
        try:
            delivery = await self._client.deliver(
                who, environment, document, signed, authenticate=authenticate
            )
        except (BuyerUnavailableError, BuyerEndpointRefusedError) as error:
            needs = isinstance(error, BuyerRequiresAuthenticationError)
            return BuyerUnreached(code=error.code, needs_authentication=needs, seeds=error.seeds)
        except (EcfUnavailableError, EcfConflictError) as error:  # DGII's directory, or its token
            return BuyerUnreached(code=error.code, needs_authentication=False, seeds=())
        if isinstance(delivery, Delivered):
            return BuyerReceipt(
                arecf=delivery.arecf, received=delivery.received, reason=delivery.reason, seeds=delivery.seeds
            )
        if isinstance(delivery, NotApplicable):
            return NoReceiver(delivery.reason)
        return BuyerReceiptRefused(arecf=delivery.arecf, reason=delivery.reason, seeds=delivery.seeds)
