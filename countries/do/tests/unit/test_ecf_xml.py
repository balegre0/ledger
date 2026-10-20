import unicodedata
from dataclasses import replace
from datetime import date
from decimal import Decimal
lazy from collections.abc import Callable

import pytest
import xmlschema
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from ledger_country_do.channels.ecf_v1.adapters.xml import ecf, rfce
from ledger_country_do.channels.ecf_v1.adapters.xml.ecf import UnwrittenContentError
from ledger_country_do.channels.ecf_v1.adapters.xml.schema_set import DO_SCHEMAS
from ledger_country_do.channels.ecf_v1.adapters.xml.writer import EmptyTextError, Node, UnwritableTextError
from ledger_country_do.channels.ecf_v1.adapters.xsd.published import Format
from ledger_country_do.channels.ecf_v1.domain.drafts import (
    BillingIndicator,
    Buyer,
    DraftInvalidError,
    EcfReference,
    GoodsOrService,
    IncomeType,
    Line,
    ModificationCode,
    PaymentType,
)
from ledger_country_do.channels.ecf_v1.domain.encf import EcfType, Encf
from ledger_country_do.channels.ecf_v1.domain.totals import compute_totals
from ledger_country_do_testing.samples import SIGNED_AT, consumer_invoice, credit_invoice
from ledger_fiscal_kit.xml.hardened import texts
from ledger_kernel.money import Currency
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


_REFERENCE = EcfReference(
    encf=Encf(EcfType.CREDIT_INVOICE, 9), issued_on=date(2026, 10, 1), code=ModificationCode.VOID
)


def _discounted() -> EcfDraft:
    draft = credit_invoice()
    return replace(draft, lines=(replace(draft.lines[0], discount=D("1.00")), *draft.lines[1:]))


_UNWRITTEN: list[tuple[str, Callable[[], EcfDraft]]] = [
    ("lines", _discounted),
    ("currency", lambda: replace(credit_invoice(), currency=Currency.of("USD"), exchange_rate=D("58.75"))),
    (
        "references",
        lambda: replace(
            credit_invoice(),
            encf=Encf(EcfType.CREDIT_NOTE, 1),
            sequence_expires_on=None,
            references=(_REFERENCE,),
        ),
    ),
    ("payment_due", lambda: replace(credit_invoice(), payment_due=date(2026, 11, 12))),
    ("buyer", lambda: replace(credit_invoice(), buyer=replace(credit_invoice().buyer, address="CALLE 5"))),
    ("buyer", lambda: replace(consumer_invoice(), buyer=Buyer(foreign_id="P1234", name="JOHN DOE"))),
]


@pytest.mark.parametrize(("what", "build"), _UNWRITTEN)
def test_what_the_e_cf_does_not_write_yet_is_refused_never_dropped(
    what: str, build: Callable[[], EcfDraft]
) -> None:
    draft = build()
    with pytest.raises(UnwrittenContentError, match=what) as caught:
        ecf.render(draft, compute_totals(draft), signed_at=SIGNED_AT)
    assert (caught.value.kind, caught.value.code) == ("internal", "do_ecf_content_unwritten")


def test_the_summary_refuses_what_it_does_not_write_yet() -> None:
    draft = replace(consumer_invoice(), payment_due=date(2026, 11, 12))
    with pytest.raises(UnwrittenContentError, match="payment_due"):
        rfce.render(draft, compute_totals(draft), security_code="dcp79q")
