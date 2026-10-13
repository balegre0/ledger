from dataclasses import replace
from decimal import Decimal

import pytest
import xmlschema
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from ledger_country_do.channels.ecf_v1.adapters.xml import ecf, rfce
from ledger_country_do.channels.ecf_v1.adapters.xml.parsing import texts
from ledger_country_do.channels.ecf_v1.adapters.xml.schemas import corrected, violations
from ledger_country_do.channels.ecf_v1.adapters.xml.writer import Node
from ledger_country_do.channels.ecf_v1.adapters.xsd.published import Format
from ledger_country_do.channels.ecf_v1.domain.drafts import BillingIndicator, Buyer, Line
from ledger_country_do.channels.ecf_v1.domain.totals import compute_totals
from ledger_country_do_testing.samples import SIGNED_AT, consumer_invoice, credit_invoice

D = Decimal


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
    assert violations(Format.ECF_32, document, unsigned=True) == ()


def test_the_rfce_summarizes_a_consumer_invoice_with_its_security_code() -> None:
    draft = consumer_invoice()
    summary = rfce.render(draft, compute_totals(draft), security_code="dcp79q")
    found = texts(
        summary, "Encabezado/IdDoc/eNCF", "Encabezado/Totales/MontoTotal", "Encabezado/CodigoSeguridadeCF"
    )
    assert found == ("E320000000001", "1180.00", "dcp79q")
    assert texts(summary, "Encabezado/Totales/ITBIS1", "Encabezado/Totales/TotalITBIS1") == (None, "180.00")
    assert violations(Format.RFCE, summary, unsigned=True) == ()


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
XSD_32 = xmlschema.XMLSchema10(corrected(Format.ECF_32).decode("utf-8"))


@settings(max_examples=50, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(lines=st.lists(_LINE, min_size=1, max_size=8), named=st.booleans())
def test_every_drafted_consumer_invoice_is_valid_for_both_validators(
    *, lines: list[Line], named: bool
) -> None:
    buyer = Buyer(rnc="131098193", name="CLIENTE") if named else Buyer()
    draft = replace(consumer_invoice(large=named, buyer=buyer), lines=tuple(lines))
    document = ecf.render(draft, compute_totals(draft), signed_at=SIGNED_AT)
    assert violations(Format.ECF_32, document, unsigned=True) == ()
    signed_shape = document.replace(
        b"</ECF>", b'<Signature xmlns="http://www.w3.org/2000/09/xmldsig#"/></ECF>'
    )
    assert XSD_32.is_valid(signed_shape.decode("utf-8"))
