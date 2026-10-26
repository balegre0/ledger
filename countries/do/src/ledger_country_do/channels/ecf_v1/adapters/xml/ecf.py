"""The e-CF of every type, in the order of DGII's schemas (FMT, sections A to E, then the signing time).

One skeleton writes every type: identification, issuer, buyer, totals, the totals in another currency, the
lines and the reference to a corrected e-CF. What differs between types is which elements a type's schema
declares and requires, which the domain's per-type tables say (``drafts``: ``INCOME_TYPES``,
``BUYER_PARTS``, ``WITHHOLDING_TYPES``...) and, for what is only a matter of the format, the tables here;
``test_xsd_pins`` holds every table equal to the schemas. Elements are named through ``names.element``,
never by hand, and a field without a value is never written.

What is written:

- ``IdDoc``: type, e-NCF, the range's expiry (dated types), on a 34 whether it comes more than 30 days
  after the e-CF it corrects (``IndicadorNotaCredito``), that prices exclude ITBIS (types whose schema has
  ``IndicadorMontoGravado``, with taxed lines), the income type (sales), the payment type and due date.
- ``Emisor``; ``Comprador`` (written whenever the type requires it, empty for an anonymous buyer as F4
  says, otherwise only when the buyer gives something): RNC, foreign identifier, name, address.
- ``Totales`` in pesos, with what a purchase's lines withhold (as perceived when its company perceives:
  only a 41's schema has perception totals); ``OtraMoneda`` for another currency: the currency, the exchange
  rate and the document's totals in it.
- ``Item`` per line: its billing, its ``Retencion`` (purchases and payments abroad: the company's role and
  what it withholds, in pesos), name, kind, quantity, unit price and discount in pesos, the line in its own
  currency (``OtraMonedaDetalle``), its amount in pesos.
- ``InformacionReferencia`` of a note: the corrected e-NCF, its date, the modification code.
- ``FechaHoraFirma``. The signature is added by the signer, after it.
"""

from types import MappingProxyType
from typing import Final
lazy from collections.abc import Mapping
lazy from datetime import datetime

from ledger_country_do.channels.ecf_v1.adapters.xml.writer import Node
from ledger_country_do.channels.ecf_v1.adapters.xsd.published import Format
from ledger_country_do.channels.ecf_v1.domain.drafts import (
    INCOME_TYPES,
    TAXED,
    WITHHOLDING_TYPES,
    BillingIndicator,
    WithholdingRole,
)
from ledger_country_do.channels.ecf_v1.domain.encf import EcfType
from ledger_country_do.channels.ecf_v1.domain.notation import (
    dgii_date,
    dgii_moment,
    dgii_number,
    dgii_percent,
)
lazy from ledger_country_do.channels.ecf_v1.domain.drafts import Buyer, EcfDraft
lazy from ledger_country_do.channels.ecf_v1.domain.totals import LineFigures, Totals

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
PRICE_INDICATOR_TYPES: Final = frozenset(EcfType) - {
    EcfType.MINOR_EXPENSES,
    EcfType.SPECIAL_REGIMES,
    EcfType.EXPORTS,
    EcfType.FOREIGN_PAYMENTS,
}
"""The types whose schema has ``IndicadorMontoGravado``: an export (46) has none, though its lines are taxed
at 0 %, and 43, 44 and 47 bill exempt lines only."""
BUYER_REQUIRED_TYPES: Final = frozenset(
    {
        EcfType.CREDIT_INVOICE,
        EcfType.CONSUMER_INVOICE,
        EcfType.PURCHASES,
        EcfType.SPECIAL_REGIMES,
        EcfType.GOVERNMENT,
        EcfType.EXPORTS,
    }
)
"""The types whose schema requires ``Comprador``; on the others (33, 34, 47; a 43 has none) it is written
only when the buyer gives something."""
_RATE_TAGS: Final = {
    BillingIndicator.ITBIS_1: "1",
    BillingIndicator.ITBIS_2: "2",
    BillingIndicator.ITBIS_3: "3",
}
_PRICES_EXCLUDE_ITBIS: Final = "0"
_LATE: Final = {True: "1", False: "0"}
_WITHHELD_TOTALS: Final = ("totalitbisretenido", "totalisrretencion")


def _identification(header: Node, draft: EcfDraft) -> None:
    ecf_type = draft.ecf_type
    ident = header.child("iddoc")
    ident.text("tipoecf", ecf_type.value)
    ident.text("encf", str(draft.encf))
    expires = draft.sequence_expires_on
    ident.text("fechavencimientosecuencia", None if expires is None else dgii_date(expires))
    if ecf_type is EcfType.CREDIT_NOTE:
        ident.text("indicadornotacredito", _LATE[draft.credit_note_late])
    taxed = ecf_type in PRICE_INDICATOR_TYPES and any(line.billing in TAXED for line in draft.lines)
    ident.text("indicadormontogravado", _PRICES_EXCLUDE_ITBIS if taxed else None)
    ident.text("tipoingresos", draft.income.value if ecf_type in INCOME_TYPES else None)
    ident.number("tipopago", draft.payment.value)
    due = draft.payment_due
    ident.text("fechalimitepago", None if due is None else dgii_date(due))


def _issuer(header: Node, draft: EcfDraft) -> None:
    issuing = header.child("emisor")
    issuing.text("rncemisor", draft.issuer.rnc)
    issuing.text("razonsocialemisor", draft.issuer.legal_name)
    issuing.text("direccionemisor", draft.issuer.address)
    issuing.text("municipio", draft.issuer.municipality)
    issuing.text("provincia", draft.issuer.province)
    issuing.text("fechaemision", dgii_date(draft.issued_on))


def write_buyer(header: Node, buyer: Buyer, *, required: bool) -> None:
    """``Comprador``: when ``required`` (empty for an anonymous buyer, F4) or when the buyer gives any."""
    if not required and not buyer.parts:
        return
    buying = header.child("comprador")
    buying.text("rnccomprador", buyer.rnc)
    buying.text("identificadorextranjero", buyer.foreign_id)
    buying.text("razonsocialcomprador", buyer.name)
    buying.text("direccioncomprador", buyer.address)


def write_totals(header: Node, totals: Totals, *, rates: bool, perceived: bool = False) -> None:
    """``Totales``: bases, (with ``rates``) the rates, ITBIS per rate, exempt amount, total, withheld.

    What the lines withhold is written as withheld, or (``perceived``: a 41 whose company perceives) as
    perceived.
    """
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
    withheld = ("totalitbispercepcion", "totalisrpercepcion") if perceived else _WITHHELD_TOTALS
    summed.amount(withheld[0], totals.itbis_withheld)
    summed.amount(withheld[1], totals.isr_withheld)


def _other_currency(header: Node, draft: EcfDraft, totals: Totals) -> None:
    """``OtraMoneda``: the document's currency, the exchange rate and the totals in that currency."""
    own, exchange_rate = totals.foreign, draft.exchange_rate
    if own is None or exchange_rate is None:
        return
    other = header.child("otramoneda")
    other.text("tipomoneda", draft.currency.code)
    other.text("tipocambio", dgii_number(exchange_rate))
    other.amount("montogravadototalotramoneda", own.taxed_total)
    for indicator in TAXED:
        group = own.group(indicator)
        other.amount(f"montogravado{_RATE_TAGS[indicator]}otramoneda", None if group is None else group.base)
    other.amount("montoexentootramoneda", own.exempt)
    other.amount("totalitbisotramoneda", own.itbis_total)
    for indicator in TAXED:
        group = own.group(indicator)
        other.amount(f"totalitbis{_RATE_TAGS[indicator]}otramoneda", None if group is None else group.itbis)
    other.amount("montototalotramoneda", own.total)


def _withholding(item: Node, draft: EcfDraft, figures: LineFigures) -> None:
    """``Retencion``: required on every line of a purchase or a payment abroad, never written on others."""
    if draft.ecf_type not in WITHHOLDING_TYPES:
        return
    withholding = item.child("retencion")
    withholding.number("indicadoragenteretencionopercepcion", draft.withholding_role.value)
    withholding.amount("montoitbisretenido", figures.itbis_withheld)
    withholding.amount("montoisrretenido", figures.isr_withheld)


def _line(items: Node, draft: EcfDraft, totals: Totals, index: int) -> None:
    """The ``Item`` of ``draft``'s line ``index``: in pesos, and in its own currency when it has another."""
    line, figures = draft.lines[index], totals.lines[index]
    own = None if totals.foreign is None else totals.foreign.lines[index]
    item = items.child("item")
    item.number("numerolinea", index + 1)
    item.number("indicadorfacturacion", line.billing.value)
    _withholding(item, draft, figures)
    item.text("nombreitem", line.name)
    item.number("indicadorbienoservicio", line.kind.value)
    item.number("cantidaditem", line.quantity)
    item.number("preciounitarioitem", figures.unit_price)
    discounted = line.discount != 0
    item.amount("descuentomonto", figures.discount if discounted else None)
    if own is not None:
        detail = item.child("otramonedadetalle")
        detail.number("preciootramoneda", own.unit_price)
        detail.amount("descuentootramoneda", own.discount if discounted else None)
        detail.amount("montoitemotramoneda", own.amount)
    item.amount("montoitem", figures.amount)


def _reference(root: Node, draft: EcfDraft) -> None:
    """``InformacionReferencia``: the e-CF a note corrects (``EcfDraft`` holds one exactly on a note)."""
    for reference in draft.references:
        referring = root.child("informacionreferencia")
        referring.text("ncfmodificado", str(reference.encf))
        referring.text("fechancfmodificado", dgii_date(reference.issued_on))
        referring.number("codigomodificacion", reference.code.value)


def render(draft: EcfDraft, totals: Totals, *, signed_at: datetime) -> bytes:
    """``draft`` as its unsigned e-CF, ``FechaHoraFirma`` set to ``signed_at``."""
    ecf_type = draft.ecf_type
    root = Node.root(FORMAT_OF[ecf_type], "ecf")
    header = root.child("encabezado")
    header.text("version", VERSION)
    _identification(header, draft)
    _issuer(header, draft)
    write_buyer(header, draft.buyer, required=ecf_type in BUYER_REQUIRED_TYPES)
    write_totals(header, totals, rates=True, perceived=draft.withholding_role is WithholdingRole.PERCEPTION)
    _other_currency(header, draft, totals)
    items = root.child("detallesitems")
    for index in range(len(draft.lines)):
        _line(items, draft, totals, index)
    _reference(root, draft)
    root.text("fechahorafirma", dgii_moment(signed_at))
    return root.document()
