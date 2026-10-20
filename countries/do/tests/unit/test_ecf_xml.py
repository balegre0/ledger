import unicodedata
from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest
import xmlschema
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from ledger_country_do.channels.ecf_v1.adapters.xml import ecf, rfce
from ledger_country_do.channels.ecf_v1.adapters.xml.schema_set import DO_SCHEMAS
from ledger_country_do.channels.ecf_v1.adapters.xml.writer import EmptyTextError, Node, UnwritableTextError
from ledger_country_do.channels.ecf_v1.adapters.xsd.published import Format
from ledger_country_do.channels.ecf_v1.domain.drafts import (
    BillingIndicator,
    Buyer,
    DraftInvalidError,
    GoodsOrService,
    IncomeType,
    Line,
    PaymentType,
)
from ledger_country_do.channels.ecf_v1.domain.totals import compute_totals
from ledger_country_do_testing.samples import (
    EXCHANGE_RATE,
    SIGNED_AT,
    USD,
    consumer_invoice,
    credit_invoice,
    credit_note,
    debit_note,
    export_sale,
    government_sale,
    minor_expense,
    payment_abroad,
    purchase,
    special_regime_sale,
)
from ledger_fiscal_kit.xml.hardened import texts
lazy from ledger_country_do.channels.ecf_v1.domain.drafts import EcfDraft

D = Decimal
_NAME_LIMIT = 80


def test_the_credit_invoice_carries_its_range_s_expiry_and_its_rates() -> None:
    draft = credit_invoice()
    document = ecf.render(draft, compute_totals(draft), signed_at=SIGNED_AT)
    found = texts(
        document,
        "Encabezado/IdDoc/eNCF",
        "Encabezado/IdDoc/FechaVencimientoSecuencia",
        "Encabezado/IdDoc/IndicadorMontoGravado",
        "Encabezado/Totales/ITBIS1",
        "Encabezado/Totales/ITBIS3",
        "Encabezado/Totales/MontoTotal",
        "FechaHoraFirma",
    )
    assert found == ("E310000000001", "31-12-2028", "0", "18", None, "4887.58", "13-10-2026 09:30:00")


def test_a_consumer_invoice_has_no_expiry_and_an_anonymous_buyer_an_empty_comprador() -> None:
    draft = consumer_invoice()
    document = ecf.render(draft, compute_totals(draft), signed_at=SIGNED_AT)
    assert b"FechaVencimientoSecuencia" not in document
    assert b"<Comprador />" in document  # F4: the schema requires the element, every child optional


def test_a_document_of_exempt_lines_only_writes_no_taxed_totals() -> None:
    exempt = Line(name="LIBRO", quantity=D(1), unit_price=D("10.00"), billing=BillingIndicator.EXEMPT)
    draft = replace(consumer_invoice(), lines=(exempt,))
    document = ecf.render(draft, compute_totals(draft), signed_at=SIGNED_AT)
    assert b"MontoGravadoTotal" not in document
    assert b"IndicadorMontoGravado" not in document
    assert DO_SCHEMAS.violations(Format.ECF_32, document, unsigned=True) == ()


def test_the_rfce_summarizes_a_consumer_invoice_with_its_security_code() -> None:
    draft = consumer_invoice()
    summary = rfce.render(draft, compute_totals(draft), security_code="dcp79q")
    found = texts(
        summary, "Encabezado/IdDoc/eNCF", "Encabezado/Totales/MontoTotal", "Encabezado/CodigoSeguridadeCF"
    )
    assert found == ("E320000000001", "1180.00", "dcp79q")
    assert texts(summary, "Encabezado/Totales/ITBIS1", "Encabezado/Totales/TotalITBIS1") == (None, "180.00")
    assert DO_SCHEMAS.violations(Format.RFCE, summary, unsigned=True) == ()


def test_the_writer_leaves_out_a_field_without_a_value_and_writes_numbers_as_they_are() -> None:
    root = Node.root(Format.ECF_32, "ecf")
    item = root.child("detallesitems").child("item")
    item.number("numerolinea", None)
    item.amount("montoitem", None)
    item.text("nombreitem", None)
    item.number("cantidaditem", D("1.50"))
    item.number("indicadorfacturacion", 4)
    found = texts(
        root.document(), "DetallesItems/Item/CantidadItem", "DetallesItems/Item/IndicadorFacturacion"
    )
    assert found == ("1.50", "4")
    assert b"<Item><CantidadItem>" in root.document()


def test_the_writer_never_writes_an_empty_element_by_accident() -> None:
    item = Node.root(Format.ECF_32, "ecf").child("detallesitems").child("item")
    with pytest.raises(EmptyTextError, match="NombreItem"):
        item.text("nombreitem", "")


@pytest.mark.parametrize("character", ["\x00", "\x0b", "\x1f", "\t", "\n", "\r", "\x7f", "\ud800", "￿"])
def test_the_writer_writes_no_text_xml_cannot_hold(character: str) -> None:
    item = Node.root(Format.ECF_32, "ecf").child("detallesitems").child("item")
    with pytest.raises(UnwritableTextError, match="NombreItem") as caught:
        item.text("nombreitem", f"CEMENTO{character}")
    assert "CEMENTO" not in str(caught.value)


def _xml_can_hold(text: str) -> bool:
    """XML 1.0's ``Char`` production less every control character: an oracle written apart from the code."""
    allowed = ((0x20, 0xD7FF), (0xE000, 0xFFFD), (0x10000, 0x10FFFF))
    return all(
        unicodedata.category(character) != "Cc"
        and any(low <= ord(character) <= high for low, high in allowed)
        for character in text
    )


@settings(max_examples=300)
@given(name=st.text(alphabet=st.characters(exclude_categories=()), max_size=90))
def test_every_accepted_name_renders_well_formed_and_reads_back_as_it_was(name: str) -> None:
    try:
        line = Line(name=name, quantity=D(1), unit_price=D("10.00"), billing=BillingIndicator.ITBIS_1)
    except DraftInvalidError:
        assert not name.strip() or len(name) > _NAME_LIMIT or not _xml_can_hold(name)
        return
    draft = replace(consumer_invoice(), lines=(line,))
    document = ecf.render(draft, compute_totals(draft), signed_at=SIGNED_AT)
    assert texts(document, "DetallesItems/Item/NombreItem") == (name,)


def test_only_a_consumer_invoice_is_summarized() -> None:
    draft = credit_invoice()
    with pytest.raises(rfce.RfceTypeError):
        rfce.render(draft, compute_totals(draft), security_code="dcp79q")


_NAMES = st.text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 ", min_size=1, max_size=80).filter(str.strip)
_LINE = st.builds(
    Line,
    name=_NAMES,
    quantity=st.decimals(min_value=D("0.01"), max_value=D(99999), places=2),
    unit_price=st.decimals(min_value=D(0), max_value=D(999999), places=4),
    billing=st.sampled_from(
        [
            BillingIndicator.ITBIS_1,
            BillingIndicator.ITBIS_2,
            BillingIndicator.ITBIS_3,
            BillingIndicator.EXEMPT,
        ]
    ),
)
XSD_32 = xmlschema.XMLSchema10(DO_SCHEMAS.corrected(Format.ECF_32).decode("utf-8"))


@settings(max_examples=50, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(lines=st.lists(_LINE, min_size=1, max_size=8), named=st.booleans())
def test_every_drafted_consumer_invoice_is_valid_for_both_validators(
    *, lines: list[Line], named: bool
) -> None:
    buyer = Buyer(rnc="131098193", name="CLIENTE") if named else Buyer()
    draft = replace(consumer_invoice(large=named, buyer=buyer), lines=tuple(lines))
    document = ecf.render(draft, compute_totals(draft), signed_at=SIGNED_AT)
    assert DO_SCHEMAS.violations(Format.ECF_32, document, unsigned=True) == ()
    signed_shape = document.replace(
        b"</ECF>", b'<Signature xmlns="http://www.w3.org/2000/09/xmldsig#"/></ECF>'
    )
    assert XSD_32.is_valid(signed_shape.decode("utf-8"))


@pytest.mark.parametrize(
    ("income", "payment", "kind", "codes"),
    [
        (IncomeType.OPERATIONS, PaymentType.CASH, GoodsOrService.GOODS, ("01", "1", "1")),
        (IncomeType.FINANCIAL, PaymentType.CREDIT, GoodsOrService.SERVICE, ("02", "2", "2")),
        (IncomeType.EXTRAORDINARY, PaymentType.FREE, GoodsOrService.GOODS, ("03", "3", "1")),
        (IncomeType.LEASES, PaymentType.CREDIT, GoodsOrService.SERVICE, ("04", "2", "2")),
        (IncomeType.DEPRECIABLE_ASSETS, PaymentType.CASH, GoodsOrService.GOODS, ("05", "1", "1")),
        (IncomeType.OTHER, PaymentType.CASH, GoodsOrService.SERVICE, ("06", "1", "2")),
    ],
)
def test_income_payment_and_item_kinds_are_written_in_dgii_s_codes(
    income: IncomeType, payment: PaymentType, kind: GoodsOrService, codes: tuple[str, str, str]
) -> None:
    sample = credit_invoice()
    lines = tuple(replace(line, kind=kind) for line in sample.lines)
    draft = replace(sample, income=income, payment=payment, lines=lines)
    document = ecf.render(draft, compute_totals(draft), signed_at=SIGNED_AT)
    found = texts(
        document,
        "Encabezado/IdDoc/TipoIngresos",
        "Encabezado/IdDoc/TipoPago",
        "DetallesItems/Item/IndicadorBienoServicio",
    )
    assert found == codes


def _written(draft: EcfDraft) -> bytes:
    """``draft`` rendered, and valid for its type's schema as written (unsigned)."""
    document = ecf.render(draft, compute_totals(draft), signed_at=SIGNED_AT)
    assert DO_SCHEMAS.violations(ecf.FORMAT_OF[draft.ecf_type], document, unsigned=True) == ()
    return document


_ITEM = "DetallesItems/Item"


def test_a_discounted_line_writes_its_discount_and_an_undiscounted_one_none() -> None:
    document = _written(debit_note(discounts=True))
    first, second, third = (f"{_ITEM}[{index}]/DescuentoMonto" for index in (1, 2, 3))
    assert texts(document, first, second, third) == ("150.00", None, "0.99")
    assert texts(document, f"{_ITEM}[1]/MontoItem", f"{_ITEM}[3]/MontoItem") == ("2850.00", "360.01")


def test_a_document_in_another_currency_writes_pesos_and_its_own_currency() -> None:
    draft = replace(credit_invoice(), currency=USD, exchange_rate=EXCHANGE_RATE)
    lines = (replace(draft.lines[0], discount=D("10.50")), *draft.lines[1:])
    document = _written(replace(draft, lines=lines))
    other = "Encabezado/OtraMoneda"
    assert texts(
        document,
        f"{other}/TipoMoneda",
        f"{other}/TipoCambio",
        f"{other}/MontoGravado1OtraMoneda",
        f"{other}/MontoExentoOtraMoneda",
        f"{other}/TotalITBISOtraMoneda",
        f"{other}/MontoTotalOtraMoneda",
    ) == ("USD", "58.7512", "2989.50", "361.00", "674.19", "4875.19")
    detail = f"{_ITEM}[1]/OtraMonedaDetalle"
    assert texts(
        document,
        f"{_ITEM}[1]/PrecioUnitarioItem",
        f"{_ITEM}[1]/DescuentoMonto",
        f"{_ITEM}[1]/MontoItem",
        f"{detail}/PrecioOtraMoneda",
        f"{detail}/DescuentoOtraMoneda",
        f"{detail}/MontoItemOtraMoneda",
        f"{_ITEM}[2]/OtraMonedaDetalle/DescuentoOtraMoneda",
    ) == ("88126.8000", "616.89", "175636.71", "1500.00", "10.50", "2989.50", None)
    assert b"OtraMoneda" not in _written(credit_invoice())


def test_a_note_names_the_e_cf_it_corrects_and_why() -> None:
    document = _written(debit_note())
    reference = "InformacionReferencia"
    assert texts(
        document,
        f"{reference}/NCFModificado",
        f"{reference}/FechaNCFModificado",
        f"{reference}/CodigoModificacion",
    ) == ("E310000000001", "01-10-2026", "3")
    assert b"InformacionReferencia" not in _written(credit_invoice())


@pytest.mark.parametrize(("draft", "indicator"), [(credit_note(), "1"), (credit_note(foreign=True), "0")])
def test_a_credit_note_says_whether_it_comes_more_than_thirty_days_after_its_e_cf(
    draft: EcfDraft, indicator: str
) -> None:
    document = _written(draft)
    assert texts(document, "Encabezado/IdDoc/IndicadorNotaCredito") == (indicator,)
    assert b"FechaVencimientoSecuencia" not in document
    assert b"IndicadorNotaCredito" not in _written(debit_note())


def test_a_payment_due_date_and_a_buyer_s_address_are_written() -> None:
    document = _written(debit_note())
    assert texts(document, "Encabezado/IdDoc/FechaLimitePago", "Encabezado/Comprador/DireccionComprador") == (
        "12-11-2026",
        "CALLE 5 #12, SANTIAGO",
    )


def test_a_consumer_s_foreign_identifier_is_written() -> None:
    draft = replace(consumer_invoice(), buyer=Buyer(foreign_id="P1234", name="JOHN DOE"))
    assert texts(
        _written(draft), "Encabezado/Comprador/IdentificadorExtranjero", "Encabezado/Comprador/RNCComprador"
    ) == ("P1234", None)


def test_a_note_to_an_anonymous_buyer_writes_no_comprador() -> None:
    assert b"Comprador" not in _written(replace(credit_note(), buyer=Buyer()))
    assert b"<Comprador />" in _written(consumer_invoice())  # F4: required on a 32, and empty


def test_a_special_regime_sale_writes_exempt_lines_only() -> None:
    document = _written(special_regime_sale())
    assert texts(document, "Encabezado/Totales/MontoExento", "Encabezado/Totales/MontoTotal") == (
        "4211.50",
        "4211.50",
    )
    assert b"MontoGravado" not in document
    assert b"ITBIS" not in document


def test_a_government_sale_names_its_buyer_s_rnc() -> None:
    assert texts(_written(government_sale()), "Encabezado/Comprador/RNCComprador") == ("401007551",)


def test_an_export_writes_only_the_zero_rate_and_no_price_indicator() -> None:
    document = _written(export_sale())
    assert b"IndicadorMontoGravado" not in document
    assert b"MontoGravadoI1" not in document
    assert b"ITBIS1" not in document
    assert texts(
        document,
        "Encabezado/Totales/MontoGravadoI3",
        "Encabezado/Totales/ITBIS3",
        "Encabezado/Comprador/IdentificadorExtranjero",
        "Encabezado/Comprador/DireccionComprador",
    ) == ("45532.18", "0", "P12345678", "1 MAIN ST, MIAMI FL")


def test_a_purchase_writes_its_retention_block_on_every_line() -> None:
    plain = _written(purchase())
    assert texts(
        plain,
        f"{_ITEM}[1]/Retencion/IndicadorAgenteRetencionoPercepcion",
        f"{_ITEM}[2]/Retencion/IndicadorAgenteRetencionoPercepcion",
        f"{_ITEM}[1]/Retencion/MontoITBISRetenido",
        "Encabezado/Totales/TotalITBISRetenido",
    ) == ("1", "1", None, None)
    assert b"TipoIngresos" not in plain
    withheld = _written(purchase(withholdings=True))
    assert texts(
        withheld,
        f"{_ITEM}[1]/Retencion/MontoITBISRetenido",
        f"{_ITEM}[1]/Retencion/MontoISRRetenido",
        f"{_ITEM}[2]/Retencion/MontoISRRetenido",
        "Encabezado/Totales/TotalITBISRetenido",
        "Encabezado/Totales/TotalISRRetencion",
    ) == ("900.00", "500.00", None, "900.00", "500.00")


def test_a_payment_abroad_withholds_isr_in_pesos_and_names_its_payee() -> None:
    document = _written(payment_abroad())
    assert texts(
        document,
        f"{_ITEM}/Retencion/IndicadorAgenteRetencionoPercepcion",
        f"{_ITEM}/Retencion/MontoISRRetenido",
        "Encabezado/Totales/TotalISRRetencion",
        "Encabezado/Comprador/IdentificadorExtranjero",
        "Encabezado/OtraMoneda/MontoTotalOtraMoneda",
    ) == ("1", "15862.82", "15862.82", "530123456", "1000.00")


def test_a_minor_expense_names_no_supplier_and_no_income_type() -> None:
    document = _written(minor_expense())
    assert b"Comprador" not in document
    assert b"TipoIngresos" not in document


def test_the_summary_writes_what_its_format_holds_of_a_richer_consumer_invoice() -> None:
    sample = consumer_invoice()
    draft = replace(
        sample,
        buyer=Buyer(foreign_id="P1234", name="JOHN DOE", address="1 MAIN ST"),
        payment_due=date(2026, 11, 12),
        currency=USD,
        exchange_rate=EXCHANGE_RATE,
        lines=(replace(sample.lines[0], discount=D("1.00")),),
    )
    summary = rfce.render(draft, compute_totals(draft), security_code="dcp79q")
    assert DO_SCHEMAS.violations(Format.RFCE, summary, unsigned=True) == ()
    assert texts(summary, "Encabezado/Comprador/IdentificadorExtranjero") == ("P1234",)
    assert b"1 MAIN ST" not in summary
