"""Where a document goes: DGII's reception (track id, then polling) or the RFCE service (synchronous).

DT-DGII: a type-32 invoice whose total is below the dated ``rfce_threshold`` (DOP 250,000.00) is sent as a
summary (RFCE) to ``fc.dgii.gov.do``, and DGII's reception refuses it in full; from the threshold it goes in
full through reception and, as every type 32 of that size, names its buyer (FMT, Comprador).
"""

from enum import StrEnum
lazy from datetime import date
lazy from decimal import Decimal

from ledger_country_do.channels.ecf_v1.domain.drafts import DraftInvalidError
from ledger_country_do.channels.ecf_v1.domain.encf import EcfType
from ledger_country_do.channels.ecf_v1.domain.errors import from_regime
from ledger_country_do.regime import REGIME
from ledger_country_do.regime.entries import amount
from ledger_country_do.regime.parameters import RFCE_THRESHOLD
lazy from ledger_country_do.channels.ecf_v1.domain.drafts import EcfDraft
lazy from ledger_country_do.channels.ecf_v1.domain.totals import Totals
lazy from ledger_kernel.regimes import RegimeDef


class Route(StrEnum):
    """Which DGII service receives a document."""

    RECEPTION = "reception"
    RFCE = "rfce"


class _UnnamedLargeBuyerError(DraftInvalidError):
    def __init__(self) -> None:
        super().__init__("buyer", "names its RNC or cédula on a type 32 of DOP 250,000 or more")


def rfce_threshold(regime: RegimeDef, day: date) -> Decimal:
    """The total from which a type 32 of ``day`` goes in full, its buyer identified (``rfce_threshold``)."""
    return from_regime(lambda: amount(regime, RFCE_THRESHOLD, day))


def route(draft: EcfDraft, totals: Totals, regime: RegimeDef = REGIME) -> Route:
    """``draft``'s route (see the module).

    ``DraftInvalidError`` for a type 32 of the threshold or more without its buyer's RNC or cédula.
    """
    if draft.ecf_type is not EcfType.CONSUMER_INVOICE:
        return Route.RECEPTION
    if totals.total < rfce_threshold(regime, draft.issued_on):
        return Route.RFCE
    if draft.buyer.rnc is None:
        raise _UnnamedLargeBuyerError
    return Route.RECEPTION
