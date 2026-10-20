"""The e-CF of types 31 and 32, in the order of DGII's schemas (FMT, sections A to C, then the signing time).

What the spike writes: identification (type, e-NCF, the range's expiry for dated types, whether prices
include ITBIS, income and payment type), issuer, buyer, totals (bases, rates and ITBIS per rate, exempt
amount, total), lines (number, billing indicator, name, goods or service, quantity, unit price, amount) and
``FechaHoraFirma``. The signature is added by the signer, after ``FechaHoraFirma``.
"""

from types import MappingProxyType
from typing import ClassVar, Final
lazy from collections.abc import Mapping
lazy from datetime import datetime
lazy from decimal import Decimal

from ledger_country_do.channels.ecf_v1.adapters.xml.writer import Node
from ledger_country_do.channels.ecf_v1.adapters.xsd.published import Format
from ledger_country_do.channels.ecf_v1.domain.drafts import TAXED, BillingIndicator
from ledger_country_do.channels.ecf_v1.domain.encf import EcfType
from ledger_country_do.channels.ecf_v1.domain.errors import EcfError
from ledger_country_do.channels.ecf_v1.domain.notation import dgii_date, dgii_moment, dgii_percent
lazy from ledger_country_do.channels.ecf_v1.domain.drafts import Buyer, EcfDraft, Line
lazy from ledger_country_do.channels.ecf_v1.domain.totals import Totals

VERSION: Final = "1.0"
FORMAT_OF: Final[Mapping[EcfType, Format]] = MappingProxyType(
    {
        EcfType.CREDIT_INVOICE: Format.ECF_31,
        EcfType.CONSUMER_INVOICE: Format.ECF_32,
        EcfType.DEBIT_NOTE: Format.ECF_33,
        EcfType.CREDIT_NOTE: Format.ECF_34,
        EcfType.PURCHASES: Format.ECF_41,
        EcfType.MINOR_EXPENSES: Format.ECF_43,
        EcfType.SPECIAL_REGIMES: Format.ECF_44,
        EcfType.GOVERNMENT: Format.ECF_45,
        EcfType.EXPORTS: Format.ECF_46,
        EcfType.FOREIGN_PAYMENTS: Format.ECF_47,
    }
)
"""The schema of each e-CF type."""
_RATE_TAGS: Final = {
    BillingIndicator.ITBIS_1: "1",
    BillingIndicator.ITBIS_2: "2",
    BillingIndicator.ITBIS_3: "3",
}
_PRICES_EXCLUDE_ITBIS: Final = "0"


class UnwrittenContentError(EcfError):
    """A draft holds what the writer does not write yet: refused, never dropped (a defect of the caller)."""

    code: ClassVar[str] = "do_ecf_content_unwritten"

    def __init__(self, what: str) -> None:
        super().__init__(f"the e-CF writer does not write {what} yet: the draft is refused rather than cut")


def require_written(draft: EcfDraft) -> None:
    """``UnwrittenContentError`` when ``draft`` holds what neither the e-CF nor its summary writes yet.

    Discounts (``DescuentoMonto``), another currency (``OtraMoneda``), references (``InformacionReferencia``),
    the payment's due date (``FechaLimitePago``) and the buyer's address or foreign identifier are drafted but
    not written until each type's writer is (v2-M3b Task 7): a document missing them would differ from its
    draft.
    """
    unwritten = (
        ("lines (a discount)", any(line.discount != 0 for line in draft.lines)),
        ("currency (another than the peso)", draft.exchange_rate is not None),
        ("references", bool(draft.references)),
        ("payment_due", draft.payment_due is not None),
        (
            "buyer (an address or a foreign identifier)",
            draft.buyer.address is not None or draft.buyer.foreign_id is not None,
        ),
    )
    found = next((what for what, held in unwritten if held), None)
    if found is not None:
        raise UnwrittenContentError(found)


def _identification(header: Node, draft: EcfDraft) -> None:
    ident = header.child("iddoc")
    ident.text("tipoecf", draft.ecf_type.value)
    ident.text("encf", str(draft.encf))
    expires = draft.sequence_expires_on
    ident.text("fechavencimientosecuencia", None if expires is None else dgii_date(expires))
    taxed = any(line.billing in TAXED for line in draft.lines)
    ident.text("indicadormontogravado", _PRICES_EXCLUDE_ITBIS if taxed else None)
    ident.text("tipoingresos", draft.income.value)
    ident.number("tipopago", draft.payment.value)


def _issuer(header: Node, draft: EcfDraft) -> None:
    issuing = header.child("emisor")
    issuing.text("rncemisor", draft.issuer.rnc)
    issuing.text("razonsocialemisor", draft.issuer.legal_name)
    issuing.text("direccionemisor", draft.issuer.address)
    issuing.text("fechaemision", dgii_date(draft.issued_on))


def write_buyer(header: Node, buyer: Buyer) -> None:
    """``Comprador``: written always (the schemas of 31, 32 and the RFCE require it), empty when anonymous."""
    buying = header.child("comprador")
    buying.text("rnccomprador", buyer.rnc)
    buying.text("razonsocialcomprador", buyer.name)


def write_totals(header: Node, totals: Totals, *, rates: bool) -> None:
    """``Totales``: bases, (with ``rates``) the rates, ITBIS per rate, exempt amount and total."""
    summed = header.child("totales")
    summed.amount("montogravadototal", totals.taxed_total)
    for indicator in TAXED:
        group = totals.group(indicator)
        summed.amount(f"montogravadoi{_RATE_TAGS[indicator]}", None if group is None else group.base)
    summed.amount("montoexento", totals.exempt)
    if rates:
        for indicator in TAXED:
            group = totals.group(indicator)
            summed.text(
                f"itbis{_RATE_TAGS[indicator]}", None if group is None else dgii_percent(group.percent)
            )
    summed.amount("totalitbis", totals.itbis_total)
    for indicator in TAXED:
        group = totals.group(indicator)
        summed.amount(f"totalitbis{_RATE_TAGS[indicator]}", None if group is None else group.itbis)
    summed.amount("montototal", totals.total)


def _line(items: Node, number: int, line: Line, amount: Decimal) -> None:
    item = items.child("item")
    item.number("numerolinea", number)
    item.number("indicadorfacturacion", line.billing.value)
    item.text("nombreitem", line.name)
    item.number("indicadorbienoservicio", line.kind.value)
    item.number("cantidaditem", line.quantity)
    item.number("preciounitarioitem", line.unit_price)
    item.amount("montoitem", amount)


def render(draft: EcfDraft, totals: Totals, *, signed_at: datetime) -> bytes:
    """``draft`` as its unsigned e-CF, ``FechaHoraFirma`` set to ``signed_at``."""
    require_written(draft)
    root = Node.root(FORMAT_OF[draft.ecf_type], "ecf")
    header = root.child("encabezado")
    header.text("version", VERSION)
    _identification(header, draft)
    _issuer(header, draft)
    write_buyer(header, draft.buyer)
    write_totals(header, totals, rates=True)
    items = root.child("detallesitems")
    for number, (line, amount) in enumerate(zip(draft.lines, totals.line_amounts, strict=True), start=1):
        _line(items, number, line, amount)
    root.text("fechahorafirma", dgii_moment(signed_at))
    return root.document()
