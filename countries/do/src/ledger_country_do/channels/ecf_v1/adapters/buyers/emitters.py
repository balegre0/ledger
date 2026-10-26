"""``Emitters`` over the buyer client: what the approval job's use case sees of an ACECF sent (§8.6.5 step 6).

The client's outcomes become the port's values, the seeds signed carried by each: the emitter took it
(``EmitterTook``), refused it (``EmitterRefused``), or DGII's directory lists no receiver for it
(``NoEmitter``). Every failure is the emitter out of reach for now (``EmitterUnreached``, its code): its
approval service unavailable (or requiring the authentication the company keeps off:
``needs_authentication``), an endpoint in DGII's directory that may not be reached, and DGII's directory
itself out of reach or its token not signable (the company's certificate). The service's answers (200 taken,
400 refused) and the file name ``ACECF.xml`` are the client's (``buyers.approvals``), not confirmed by DGII.
"""

from typing import final, override

from ledger_country_do.channels.ecf_v1.adapters.buyers.approvals import ApprovalRefused, ApprovalSent
from ledger_country_do.channels.ecf_v1.adapters.buyers.client import (
    BuyerRequiresAuthenticationError,
    BuyerUnavailableError,
)
from ledger_country_do.channels.ecf_v1.adapters.buyers.endpoints import BuyerEndpointRefusedError
from ledger_country_do.channels.ecf_v1.application.approving import (
    EmitterRefused,
    Emitters,
    EmitterTook,
    EmitterUnreached,
    NoEmitter,
)
from ledger_country_do.channels.ecf_v1.domain.errors import EcfConflictError, EcfUnavailableError
lazy from ledger_country_do.channels.ecf_v1.adapters.buyers.client import BuyerClient
lazy from ledger_country_do.channels.ecf_v1.application.approving import EmitterAnswer
lazy from ledger_country_do.channels.ecf_v1.application.ports import SigningAs
lazy from ledger_country_do.channels.ecf_v1.domain.environments import Environment


@final
class EmitterApprovals(Emitters):
    """``Emitters`` over an open ``BuyerClient`` (see the module)."""

    __slots__ = ("_client",)

    def __init__(self, client: BuyerClient) -> None:
        self._client = client

    @override
    async def send(
        self, who: SigningAs, environment: Environment, emitter_rnc: str, signed: bytes, *, authenticate: bool
    ) -> EmitterAnswer:
        try:
            sent = await self._client.send_acecf(
                who, environment, emitter_rnc, signed, authenticate=authenticate
            )
        except (BuyerUnavailableError, BuyerEndpointRefusedError) as error:
            needs = isinstance(error, BuyerRequiresAuthenticationError)
            return EmitterUnreached(code=error.code, needs_authentication=needs, seeds=error.seeds)
        except (EcfUnavailableError, EcfConflictError) as error:  # DGII's directory, or its token
            return EmitterUnreached(code=error.code, needs_authentication=False, seeds=())
        if isinstance(sent, ApprovalSent):
            return EmitterTook(sent.seeds)
        if isinstance(sent, ApprovalRefused):
            return EmitterRefused(sent.seeds)
        return NoEmitter(sent.reason)
