"""Sample drafts for tests, the fake DGII's dry runs and the spike CLI.

The credit invoice's numbers are worked out in ``test_totals``: lines of 3,000.00 at 18 %, 850.50 at 16 % and
361.00 exempt, ITBIS 676.08, total 4,887.58. A small consumer invoice (1,180.00) goes as an RFCE; a large one
(295,000.00) goes in full and names its buyer. TesteCF's ranges expire on 2028-12-31 ([06] quick reference).
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Final

from ledger_country_do.channels.ecf_v1.domain.drafts import (
    BillingIndicator,
    Buyer,
    EcfDraft,
    GoodsOrService,
    Issuer,
    Line,
)
from ledger_country_do.channels.ecf_v1.domain.encf import EcfType, Encf
from ledger_country_do.channels.ecf_v1.domain.notation import SANTO_DOMINGO

ISSUER: Final = Issuer(
    rnc="101850043", legal_name="FERRETERIA DUARTE SRL", address="AV. DUARTE 100, SANTO DOMINGO"
)
BUYER: Final = Buyer(rnc="131098193", name="CONSTRUCTORA DEL CARIBE SRL")
ISSUED_ON: Final = date(2026, 10, 13)
SIGNED_AT: Final = datetime(2026, 10, 13, 9, 30, tzinfo=SANTO_DOMINGO)
SEQUENCE_EXPIRES_ON: Final = date(2028, 12, 31)
SIGNER_VALID_FROM: Final = datetime(2026, 1, 1, tzinfo=UTC)
"""When the tests' certificates start being valid: before every moment the tests sign at (2026-10-13),
whatever day they run. A certificate dated from the real "now" would stop covering those moments a day
later."""
CREDIT_LINES: Final = (
    Line(
        name="CEMENTO GRIS 42.5 KG",
        quantity=Decimal(2),
        unit_price=Decimal("1500.00"),
        billing=BillingIndicator.ITBIS_1,
    ),
    Line(
        name="CAFE MOLIDO 1 LB",
        quantity=Decimal(1),
        unit_price=Decimal("850.50"),
        billing=BillingIndicator.ITBIS_2,
    ),
    Line(
        name="LIBRO DE TEXTO",
        quantity=Decimal(3),
        unit_price=Decimal("120.3333"),
        billing=BillingIndicator.EXEMPT,
        kind=GoodsOrService.GOODS,
    ),
)


def credit_invoice(sequence: int = 1, *, issuer: Issuer = ISSUER, buyer: Buyer = BUYER) -> EcfDraft:
    """A type 31 of three lines (18 %, 16 %, exempt), numbered ``sequence``."""
    return EcfDraft(
        encf=Encf(EcfType.CREDIT_INVOICE, sequence),
        issued_on=ISSUED_ON,
        issuer=issuer,
        lines=CREDIT_LINES,
        buyer=buyer,
        sequence_expires_on=SEQUENCE_EXPIRES_ON,
    )


def consumer_invoice(
    sequence: int = 1, *, large: bool = False, buyer: Buyer | None = None, issuer: Issuer = ISSUER
) -> EcfDraft:
    """A type 32 of one line at 18 %: 1,180.00 (an RFCE, anonymous by default) or, ``large``, 295,000.00."""
    price = Decimal("250000.00") if large else Decimal("1000.00")
    line = Line(name="VARILLA 3/8", quantity=Decimal(1), unit_price=price, billing=BillingIndicator.ITBIS_1)
    chosen = buyer if buyer is not None else (BUYER if large else Buyer())
    return EcfDraft(
        encf=Encf(EcfType.CONSUMER_INVOICE, sequence),
        issued_on=ISSUED_ON,
        issuer=issuer,
        lines=(line,),
        buyer=chosen,
    )
