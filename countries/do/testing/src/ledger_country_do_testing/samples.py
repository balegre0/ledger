"""Sample drafts for tests and the fake DGII's dry runs.

The credit invoice's numbers are worked out in ``test_totals``: lines of 3,000.00 at 18 %, 850.50 at 16 % and
361.00 exempt, ITBIS 676.08, total 4,887.58. A small consumer invoice (1,180.00) goes as an RFCE; a large one
(295,000.00) goes in full and names its buyer. TesteCF's ranges expire on 2028-12-31 ([06] quick reference).

Every other type has a sample too, each the smallest document that shows what its type writes: a debit and a
credit note correcting the credit invoice (``E310000000001``), a sale under a special regime (exempt), a sale
to the government, an export (at 0 %, in dollars, to a buyer abroad), a purchase from an individual (with and
without withholdings), a minor expense and a payment abroad (in dollars, its ISR withheld).
"""

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Final

from ledger_country_do.channels.ecf_v1.domain.drafts import (
    PESO,
    BillingIndicator,
    Buyer,
    EcfDraft,
    EcfReference,
    GoodsOrService,
    Issuer,
    Line,
    ModificationCode,
    PaymentType,
)
from ledger_country_do.channels.ecf_v1.domain.encf import EcfType, Encf
from ledger_country_do.channels.ecf_v1.domain.notation import SANTO_DOMINGO
from ledger_kernel.money import Currency

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


CORRECTED: Final = Encf(EcfType.CREDIT_INVOICE, 1)
"""The e-CF the sample notes correct: the sample credit invoice."""
USD: Final = Currency.of("USD")
EXCHANGE_RATE: Final = Decimal("58.7512")
GOVERNMENT_BUYER: Final = Buyer(rnc="401007551", name="MINISTERIO DE OBRAS PUBLICAS")
FOREIGN_BUYER: Final = Buyer(foreign_id="P12345678", name="ACME TRADING LLC", address="1 MAIN ST, MIAMI FL")
SUPPLIER: Final = Buyer(rnc="00113918205", name="JUAN PEREZ")
"""An individual (a cédula) the company buys from and self-bills (type 41)."""
PAYEE: Final = Buyer(foreign_id="530123456", name="ACME CONSULTING LLC")
"""A provider abroad the company pays (type 47)."""


def _reference(corrected_on: date) -> tuple[EcfReference, ...]:
    return (EcfReference(encf=CORRECTED, issued_on=corrected_on, code=ModificationCode.CORRECT_AMOUNTS),)


def debit_note(*, discounts: bool = False) -> EcfDraft:
    """A type 33 adding to the credit invoice's amounts, on credit; ``discounts``: two lines discounted."""
    first, second, third = CREDIT_LINES
    lines = (
        (replace(first, discount=Decimal("150.00")), second, replace(third, discount=Decimal("0.99")))
        if discounts
        else CREDIT_LINES
    )
    return EcfDraft(
        encf=Encf(EcfType.DEBIT_NOTE, 2 if discounts else 1),
        issued_on=ISSUED_ON,
        issuer=ISSUER,
        lines=lines,
        buyer=replace(BUYER, address="CALLE 5 #12, SANTIAGO"),
        sequence_expires_on=SEQUENCE_EXPIRES_ON,
        payment=PaymentType.CREDIT,
        payment_due=date(2026, 11, 12),
        references=_reference(date(2026, 10, 1)),
    )


def credit_note(*, foreign: bool = False) -> EcfDraft:
    """A type 34 returning a bag of cement 43 days after the invoice; ``foreign``: in dollars, 12 after."""
    line = Line(
        name="CEMENTO GRIS 42.5 KG",
        quantity=Decimal(1),
        unit_price=Decimal("25.53") if foreign else Decimal("1500.00"),
        billing=BillingIndicator.ITBIS_1,
    )
    return EcfDraft(
        encf=Encf(EcfType.CREDIT_NOTE, 2 if foreign else 1),
        issued_on=ISSUED_ON,
        issuer=ISSUER,
        lines=(line,),
        buyer=BUYER,
        references=_reference(date(2026, 10, 1) if foreign else date(2026, 8, 31)),
        currency=USD if foreign else PESO,
        exchange_rate=EXCHANGE_RATE if foreign else None,
    )


def special_regime_sale() -> EcfDraft:
    """A type 44: the credit invoice's lines sold to a buyer under a special regime, every line exempt."""
    return EcfDraft(
        encf=Encf(EcfType.SPECIAL_REGIMES, 1),
        issued_on=ISSUED_ON,
        issuer=ISSUER,
        lines=tuple(replace(line, billing=BillingIndicator.EXEMPT) for line in CREDIT_LINES),
        buyer=BUYER,
        sequence_expires_on=SEQUENCE_EXPIRES_ON,
    )


def government_sale() -> EcfDraft:
    """A type 45: the credit invoice's lines sold to a ministry, named by its RNC, on credit."""
    return EcfDraft(
        encf=Encf(EcfType.GOVERNMENT, 1),
        issued_on=ISSUED_ON,
        issuer=ISSUER,
        lines=CREDIT_LINES,
        buyer=GOVERNMENT_BUYER,
        sequence_expires_on=SEQUENCE_EXPIRES_ON,
        payment=PaymentType.CREDIT,
        payment_due=date(2026, 12, 12),
    )


def export_sale() -> EcfDraft:
    """A type 46: cement sold abroad at 0 %, in dollars, to a buyer named by a foreign identifier."""
    line = Line(
        name="CEMENTO EXPORTACION",
        quantity=Decimal(100),
        unit_price=Decimal("7.75"),
        billing=BillingIndicator.ITBIS_3,
    )
    return EcfDraft(
        encf=Encf(EcfType.EXPORTS, 1),
        issued_on=ISSUED_ON,
        issuer=ISSUER,
        lines=(line,),
        buyer=FOREIGN_BUYER,
        sequence_expires_on=SEQUENCE_EXPIRES_ON,
        currency=USD,
        exchange_rate=EXCHANGE_RATE,
    )


def purchase(*, withholdings: bool = False) -> EcfDraft:
    """A type 41: a plumbing service and parts bought from an individual.

    ``withholdings``: the company withholds the service's whole ITBIS and 10 % of its amount as ISR.
    """
    service = Line(
        name="SERVICIO DE PLOMERIA",
        quantity=Decimal(1),
        unit_price=Decimal("5000.00"),
        billing=BillingIndicator.ITBIS_1,
        kind=GoodsOrService.SERVICE,
        itbis_withheld=Decimal("900.00") if withholdings else None,
        isr_withheld=Decimal("500.00") if withholdings else None,
    )
    parts = Line(
        name="TUBO PVC 1/2", quantity=Decimal(4), unit_price=Decimal("85.00"), billing=BillingIndicator.EXEMPT
    )
    return EcfDraft(
        encf=Encf(EcfType.PURCHASES, 2 if withholdings else 1),
        issued_on=ISSUED_ON,
        issuer=ISSUER,
        lines=(service, parts),
        buyer=SUPPLIER,
        sequence_expires_on=SEQUENCE_EXPIRES_ON,
    )


def minor_expense() -> EcfDraft:
    """A type 43: small expenses, exempt, with no supplier named."""
    line = Line(
        name="PASAJE AUTOBUS",
        quantity=Decimal(2),
        unit_price=Decimal("350.00"),
        billing=BillingIndicator.EXEMPT,
        kind=GoodsOrService.SERVICE,
    )
    return EcfDraft(
        encf=Encf(EcfType.MINOR_EXPENSES, 1),
        issued_on=ISSUED_ON,
        issuer=ISSUER,
        lines=(line,),
        sequence_expires_on=SEQUENCE_EXPIRES_ON,
    )


def payment_abroad() -> EcfDraft:
    """A type 47: a consultancy paid abroad in dollars, 27 % of it withheld as ISR."""
    line = Line(
        name="CONSULTORIA TECNICA",
        quantity=Decimal(1),
        unit_price=Decimal("1000.00"),
        billing=BillingIndicator.EXEMPT,
        kind=GoodsOrService.SERVICE,
        isr_withheld=Decimal("270.00"),
    )
    return EcfDraft(
        encf=Encf(EcfType.FOREIGN_PAYMENTS, 1),
        issued_on=ISSUED_ON,
        issuer=ISSUER,
        lines=(line,),
        buyer=PAYEE,
        sequence_expires_on=SEQUENCE_EXPIRES_ON,
        currency=USD,
        exchange_rate=EXCHANGE_RATE,
    )
