"""Every type, drafted at random, is written valid for its schema and adds up as DGII checks it.

DGII's checks (FMT; IT §12 and §13), read back from the written document: each line's ``MontoItem`` is its
quantity x ``PrecioUnitarioItem`` less ``DescuentoMonto`` within one unit; each rate's base is the sum of its
lines and its ITBIS that base at its rate; ``MontoTotal`` is the bases, the exempt amount and the ITBIS; the
withheld (or perceived) totals are the sums of the lines'. The same holds in the document's own currency
(``OtraMoneda``). A draft the domain refuses (a line whose pesos stray from its value) is not drawn.
"""

from dataclasses import replace
from datetime import date, timedelta
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from typing import Final

from hypothesis import HealthCheck, given, reject, settings
from hypothesis import strategies as st

from ledger_country_do.channels.ecf_v1.adapters.xml import ecf
from ledger_country_do.channels.ecf_v1.adapters.xml.schema_set import DO_SCHEMAS
from ledger_country_do.channels.ecf_v1.domain.drafts import (
    BILLINGS,
    BUYER_PARTS,
    ISR_REQUIRED_TYPES,
    ITBIS_WITHHOLDING_TYPES,
    NAMED_TYPES,
    NO_DISCOUNT_TYPES,
    NOTE_TYPES,
    PAYMENT_DUE_TYPES,
    PERCEPTION_TYPES,
    PESO,
    RNC_TYPES,
    UNDATED_TYPES,
    WITHHOLDING_TYPES,
    Buyer,
    BuyerPart,
    DraftInvalidError,
    EcfDraft,
    EcfReference,
    GoodsOrService,
    Line,
    ModificationCode,
    WithholdingRole,
)
from ledger_country_do.channels.ecf_v1.domain.encf import EcfType, Encf
from ledger_country_do.channels.ecf_v1.domain.totals import compute_totals
from ledger_country_do_testing.samples import ISSUED_ON, ISSUER, SEQUENCE_EXPIRES_ON, SIGNED_AT, USD
from ledger_fiscal_kit.xml.hardened import texts

D = Decimal
CENT: Final = D("0.01")
UNIT: Final = D(1)
_QUANTITIES = st.decimals(min_value=D("0.01"), max_value=D(9999), places=2)
_PRICES = st.decimals(min_value=D(0), max_value=D(99999), places=4)
_RATES = st.decimals(min_value=D("0.0001"), max_value=D("999.9999"), places=4)


def _cents(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


@st.composite
def _line(draw: st.DrawFn, ecf_type: EcfType) -> Line:
    quantity, price = draw(_QUANTITIES), draw(_PRICES)
    gross = (quantity * price).quantize(CENT, rounding=ROUND_DOWN)
    share = draw(st.sampled_from([D(0), D("0.1"), D("0.5"), D(1)]))
    discount = D(0) if ecf_type in NO_DISCOUNT_TYPES else (gross * share).quantize(CENT, rounding=ROUND_DOWN)
    return Line(
        name="ITEM",
        quantity=quantity,
        unit_price=price,
        discount=discount,
        billing=draw(st.sampled_from(sorted(BILLINGS[ecf_type]))),
        kind=draw(st.sampled_from(list(GoodsOrService))),
    )


@st.composite
def _buyer(draw: st.DrawFn, ecf_type: EcfType) -> Buyer:
    parts = BUYER_PARTS[ecf_type]
    rnc = BuyerPart.RNC in parts and (ecf_type in RNC_TYPES or draw(st.booleans()))
    foreign = not rnc and BuyerPart.FOREIGN_ID in parts and draw(st.booleans())
    named = BuyerPart.NAME in parts and (ecf_type in NAMED_TYPES or draw(st.booleans()))
    addressed = BuyerPart.ADDRESS in parts and draw(st.booleans())
    return Buyer(
        rnc="131098193" if rnc else None,
        foreign_id="P12345678" if foreign else None,
        name="ACME SRL" if named else None,
        address="CALLE 1" if addressed else None,
    )


def _withheld(draw: st.DrawFn, draft: EcfDraft) -> EcfDraft:
    """``draft`` with what its lines withhold, at most what each bears (its own currency's figures)."""
    if draft.ecf_type not in WITHHOLDING_TYPES:
        return draft
    own = compute_totals(draft)
    figures = own.foreign or own
    shares = st.sampled_from([None, D(0), D("0.3"), D(1)])
    lines: list[Line] = []
    for line, own_line in zip(draft.lines, figures.lines, strict=True):
        isr = draw(shares)
        if isr is None and draft.ecf_type in ISR_REQUIRED_TYPES:
            isr = D(0)
        itbis = draw(shares) if draft.ecf_type in ITBIS_WITHHOLDING_TYPES else None
        lines.append(
            replace(
                line,
                isr_withheld=None
                if isr is None
                else (own_line.amount * isr).quantize(CENT, rounding=ROUND_DOWN),
                itbis_withheld=None
                if itbis is None
                else (own_line.itbis * itbis).quantize(CENT, rounding=ROUND_DOWN),
            )
        )
    perceived = draft.ecf_type in PERCEPTION_TYPES and draw(st.booleans())
    return replace(
        draft,
        lines=tuple(lines),
        withholding_role=WithholdingRole.PERCEPTION if perceived else WithholdingRole.RETENTION,
    )


@st.composite
def _drafts(draw: st.DrawFn) -> EcfDraft:
    ecf_type = draw(st.sampled_from(list(EcfType)))
    foreign = draw(st.booleans())
    references = (
        (
            EcfReference(
                encf=Encf(EcfType.CREDIT_INVOICE, 1),
                issued_on=ISSUED_ON - timedelta(days=draw(st.integers(0, 90))),
                code=ModificationCode.CORRECT_AMOUNTS,
            ),
        )
        if ecf_type in NOTE_TYPES
        else ()
    )
    try:
        draft = EcfDraft(
            encf=Encf(ecf_type, 1),
            issued_on=ISSUED_ON,
            issuer=ISSUER,
            lines=tuple(draw(st.lists(_line(ecf_type), min_size=1, max_size=5))),
            buyer=draw(_buyer(ecf_type)),
            sequence_expires_on=None if ecf_type in UNDATED_TYPES else SEQUENCE_EXPIRES_ON,
            payment_due=date(2026, 12, 1) if ecf_type in PAYMENT_DUE_TYPES and draw(st.booleans()) else None,
            references=references,
            currency=USD if foreign else PESO,
            exchange_rate=draw(_RATES) if foreign else None,
        )
        return _withheld(draw, draft)
    except DraftInvalidError:  # a line whose pesos stray from its value (``totals``)
        reject()


def _number(document: bytes, path: str) -> Decimal | None:
    (found,) = texts(document, path)
    return None if found is None else D(found)


def _sum(values: list[Decimal | None]) -> Decimal:
    return sum((value for value in values if value is not None), D(0))


def _items(draft: EcfDraft) -> list[str]:
    return [f"DetallesItems/Item[{number}]" for number in range(1, len(draft.lines) + 1)]


def _adds_up(document: bytes, draft: EcfDraft, *, other: bool) -> None:
    """DGII's checks over ``Totales`` (or, ``other``, ``OtraMoneda``) and the items they sum."""
    amount = "OtraMonedaDetalle/MontoItemOtraMoneda" if other else "MontoItem"
    head = "Encabezado/OtraMoneda" if other else "Encabezado/Totales"
    suffix = "OtraMoneda" if other else ""
    billings = {item: texts(document, f"{item}/IndicadorFacturacion")[0] for item in _items(draft)}
    bases: list[Decimal] = []
    for rate in "123":
        base = _number(document, f"{head}/MontoGravado{'' if other else 'I'}{rate}{suffix}")
        billed = [
            _number(document, f"{item}/{amount}") for item, billing in billings.items() if billing == rate
        ]
        assert (base is None) is (not billed)
        if base is None:
            continue
        assert base == _sum(billed)
        percent = _number(document, f"Encabezado/Totales/ITBIS{rate}")
        assert percent is not None
        assert _number(document, f"{head}/TotalITBIS{rate}{suffix}") == _cents(base * percent / 100)
        bases.append(base)
    exempt = [_number(document, f"{item}/{amount}") for item, billing in billings.items() if billing == "4"]
    assert _number(document, f"{head}/MontoExento{suffix}") == (_sum(exempt) if exempt else None)
    itbis = _sum([_number(document, f"{head}/TotalITBIS{rate}{suffix}") for rate in "123"])
    assert _number(document, f"{head}/MontoTotal{suffix}") == sum(bases, D(0)) + _sum(exempt) + itbis


def _lines_add_up(document: bytes, draft: EcfDraft) -> None:
    for item in _items(draft):
        quantity = _number(document, f"{item}/CantidadItem") or D(0)
        price = _number(document, f"{item}/PrecioUnitarioItem") or D(0)
        discount = _number(document, f"{item}/DescuentoMonto") or D(0)
        assert abs(quantity * price - discount - (_number(document, f"{item}/MontoItem") or D(0))) <= UNIT
    for line, total in (("MontoITBISRetenido", "ITBIS"), ("MontoISRRetenido", "ISR")):
        withheld = [_number(document, f"{item}/Retencion/{line}") for item in _items(draft)]
        totals = [
            _number(document, f"Encabezado/Totales/{name}")
            for name in (f"Total{total}Retenido", f"Total{total}Retencion", f"Total{total}Percepcion")
        ]
        given = [each for each in totals if each is not None]
        assert given == ([_sum(withheld)] if any(each is not None for each in withheld) else [])


@settings(
    max_examples=400, deadline=None, suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much]
)
@given(draft=_drafts())
def test_every_drafted_e_cf_is_valid_and_adds_up_as_dgii_checks_it(draft: EcfDraft) -> None:
    document = ecf.render(draft, compute_totals(draft), signed_at=SIGNED_AT)
    assert DO_SCHEMAS.violations(ecf.FORMAT_OF[draft.ecf_type], document, unsigned=True) == ()
    _lines_add_up(document, draft)
    _adds_up(document, draft, other=False)
    if draft.exchange_rate is not None:
        _adds_up(document, draft, other=True)
