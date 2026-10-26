"""The printed representation of a signed e-CF (spec §8.6.7; Decision 26): what the PDF says, and its QR.

The PDF's text is read back with ``pypdf``; its QR is read back from the page's drawing (one square per dark
module) and decoded with ``zxing-cpp``, so what is checked is what a reader of the page scans.
"""

import threading
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Final

import pytest
import zxingcpp
from lxml import etree
from pypdf import PdfReader

from ledger_country_do.channels.ecf_v1.adapters.pdf import render
from ledger_country_do.channels.ecf_v1.adapters.pdf.layout import CONTINGENCY_LEGEND, draw, qr_symbol
from ledger_country_do.channels.ecf_v1.adapters.pdf.render import (
    PdfPrinting,
    SignedEcfUnreadableError,
    printed_of,
    render_pdf,
)
from ledger_country_do.channels.ecf_v1.application.documents import Signature
from ledger_country_do.channels.ecf_v1.application.ports import Archived
from ledger_country_do.channels.ecf_v1.domain.ecf import EmissionMode
from ledger_fiscal_kit.xml.hardened import parser

GOLDEN: Final = Path(__file__).parents[1] / "contract" / "golden"
SIGNED_AT: Final = datetime(2026, 10, 13, 13, 30, tzinfo=UTC)
ECF_QR: Final = (
    "https://ecf.dgii.gov.do/testecf/consultatimbre?RncEmisor=101850043&RncComprador=131098193"
    "&ENCF=E310000000001&FechaEmision=13-10-2026&MontoTotal=4887.58&FechaFirma=13-10-2026%2009:30:00"
    "&CodigoSeguridad=dcp79q"
)
RFCE_QR: Final = (
    "https://fc.dgii.gov.do/testecf/consultatimbrefc?RncEmisor=101850043&ENCF=E320000000001"
    "&MontoTotal=295000.00&CodigoSeguridad=ab%2BcD%2F"
)
SCALE: Final = 4
QUIET: Final = 4


def golden(name: str) -> bytes:
    return (GOLDEN / f"{name}.xml").read_bytes()


def signature(*, qr_url: str = ECF_QR, code: str = "dcp79q") -> Signature:
    return Signature(
        signed_at=SIGNED_AT,
        security_code=code,
        qr_url=qr_url,
        emission_mode=EmissionMode.NORMAL,
        ecf=Archived(key="do-ecf-v1/test/o/c/ecf/x.xml", sha256=bytes(32)),
        rfce=None,
    )


def pages_of(pdf: bytes) -> list[str]:
    return [page.extract_text() for page in PdfReader(BytesIO(pdf)).pages]


def text_of(pdf: bytes) -> str:
    return "\n".join(pages_of(pdf))


def squares(pdf: bytes) -> list[tuple[float, float, float]]:
    """Every square the pages draw (``re``): its lower-left corner and side, in points."""
    found: list[tuple[float, float, float]] = []
    for page in PdfReader(BytesIO(pdf)).pages:
        contents = page.get_contents()
        assert contents is not None
        tokens = contents.get_data().decode("latin-1").split()
        for at, token in enumerate(tokens):
            if token == "re":
                x, y, width, height = (float(each) for each in tokens[at - 4 : at])
                assert width == height
                found.append((x, y, width))
    return found


def modules_of(pdf: bytes) -> tuple[int, frozenset[tuple[int, int]]]:
    """The QR the page draws: its size in modules and its dark modules (column, row from the top)."""
    drawn = squares(pdf)
    side = drawn[0][2]
    left = min(x for x, _, _ in drawn)
    top = max(y for _, y, _ in drawn) + side
    cells = frozenset((round((x - left) / side), round((top - y - side) / side)) for x, y, _ in drawn)
    return max(column for column, _ in cells) + 1, cells


def decoded(pdf: bytes) -> list[tuple[str, str]]:
    """The text and error correction level of every QR ``zxing-cpp`` reads in the drawn modules."""
    size, cells = modules_of(pdf)
    side = (size + 2 * QUIET) * SCALE
    pixels = bytearray(b"\xff" * side * side)
    for column, row in cells:
        for line in range(SCALE):
            start = ((row + QUIET) * SCALE + line) * side + (column + QUIET) * SCALE
            pixels[start : start + SCALE] = b"\x00" * SCALE
    found = zxingcpp.read_barcodes(memoryview(bytes(pixels)).cast("B", (side, side)))
    return [(each.text, each.ec_level) for each in found]


def with_lines(document: bytes, count: int, name: str) -> bytes:
    """``document`` with its first line repeated ``count`` times, each named ``name`` and numbered."""
    root = etree.fromstring(document, parser())
    items = root.find("DetallesItems")
    assert items is not None
    first = items[0]
    for each in list(items):
        items.remove(each)
    for number in range(1, count + 1):
        item = deepcopy(first)
        named, numbered = item.find("NombreItem"), item.find("NumeroLinea")
        assert named is not None
        assert numbered is not None
        named.text, numbered.text = f"{name} {number}", str(number)
        items.append(item)
    return etree.tostring(root, encoding="utf-8", xml_declaration=True)


def test_the_pdf_names_the_e_cf_its_parties_its_lines_its_totals_and_its_security_code() -> None:
    text = text_of(render_pdf(golden("ecf-31"), signature()))
    for expected in (
        "Factura de Crédito Fiscal Electrónica",
        "e-NCF: E310000000001",
        "Fecha de emisión: 13-10-2026",
        "Válida hasta: 31-12-2028",
        "FERRETERIA DUARTE SRL",
        "RNC: 101850043",
        "AV. DUARTE 100, SANTO DOMINGO",
        "Comprador",
        "CONSTRUCTORA DEL CARIBE SRL",
        "RNC/Cédula: 131098193",
        "CEMENTO GRIS 42.5 KG",
        "CAFE MOLIDO 1 LB",
        "LIBRO DE TEXTO",
        "1500.00",
        "120.3333",
        "Exento",
        "Monto gravado ITBIS 18 %",
        "3000.00",
        "Monto gravado ITBIS 16 %",
        "850.50",
        "Monto exento",
        "361.00",
        "ITBIS 18 %",
        "540.00",
        "136.08",
        "Total ITBIS",
        "676.08",
        "Monto total",
        "4887.58",
        "Código de seguridad: dcp79q",
        "Fecha de firma digital: 13-10-2026 09:30:00",
    ):
        assert expected in text, expected
    assert CONTINGENCY_LEGEND not in text
    assert "Referencia" not in text
    assert "Moneda" not in text


def test_a_document_signed_in_contingency_carries_the_legend() -> None:
    """Every document is signed in the normal way until contingency is built (Decision 36, Task 28)."""
    printed = printed_of(golden("ecf-31"), signature())
    assert printed.contingency is False
    text = text_of(draw(replace(printed, contingency=True)))
    assert CONTINGENCY_LEGEND == "e-CF emitido en modalidad de Contingencia"
    assert CONTINGENCY_LEGEND in text


def test_the_qr_is_the_signature_s_url_with_error_correction_m_and_version_8_at_least() -> None:
    """An e-CF's URL needs more than version 8 holds at level M: the smallest version that holds it."""
    pdf = render_pdf(golden("ecf-31"), signature())
    assert decoded(pdf) == [(ECF_QR, "M")]
    size, _ = modules_of(pdf)
    assert (size - 17) // 4 == qr_symbol(ECF_QR).version == 10
    summary = render_pdf(golden("ecf-32"), signature(qr_url=RFCE_QR, code="ab+cD/"))
    assert decoded(summary) == [(RFCE_QR, "M")]
    assert modules_of(summary)[0] == 8 * 4 + 17
    assert qr_symbol("https://e.do/x").version == 8


def test_the_qr_s_modules_are_segno_s() -> None:
    symbol = qr_symbol(ECF_QR)
    _, cells = modules_of(render_pdf(golden("ecf-31"), signature()))
    assert cells == frozenset(
        (column, row)
        for row, modules in enumerate(symbol.matrix)
        for column, dark in enumerate(modules)
        if dark
    )


def test_two_renders_are_byte_identical_and_dated_at_the_signature() -> None:
    first = render_pdf(golden("ecf-31"), signature())
    assert first == render_pdf(golden("ecf-31"), signature())
    assert first.startswith(b"%PDF-")
    metadata = PdfReader(BytesIO(first)).metadata
    assert metadata is not None
    assert metadata.get("/CreationDate") == "D:20261013133000Z"
    assert metadata.get("/ModDate") == "D:20261013133000Z"
    assert metadata.title == "E310000000001"


def test_a_foreign_currency_note_states_its_currency_rate_and_the_e_cf_it_corrects() -> None:
    text = text_of(render_pdf(golden("ecf-34-foreign-currency"), signature()))
    for expected in (
        "Nota de Crédito Electrónica",
        "Referencia",
        "Modifica el e-NCF E310000000001 del",
        "Moneda: USD",
        "Tasa de cambio:",
        "Monto total en USD:",
    ):
        assert expected in text, expected


def test_a_line_with_a_discount_states_it() -> None:
    document = golden("ecf-33-discounts")
    root = etree.fromstring(document, parser())
    discounts = [found.text for found in root.iter("DescuentoMonto")]
    assert discounts
    text = text_of(render_pdf(document, signature()))
    for discount in discounts:
        assert discount is not None
        assert discount in text


def test_an_anonymous_consumer_invoice_names_no_buyer() -> None:
    text = text_of(render_pdf(golden("ecf-32-anonymous"), signature(qr_url=RFCE_QR)))
    assert "Factura de Consumo Electrónica" in text
    assert "Comprador" not in text


def test_a_purchase_names_its_supplier_and_what_it_withholds() -> None:
    text = text_of(render_pdf(golden("ecf-41-retentions"), signature()))
    assert "Comprobante Electrónico de Compras" in text
    assert "Proveedor" in text
    assert "Comprador" not in text
    assert "retenido" in text


def test_a_minor_expense_has_no_counterparty_block() -> None:
    text = text_of(render_pdf(golden("ecf-43"), signature()))
    assert "Comprobante Electrónico para Gastos Menores" in text
    assert "Proveedor" not in text


def test_many_lines_run_over_pages_each_numbered_with_the_e_ncf() -> None:
    long_name = "TUBO PVC SANITARIO 4 PULGADAS X 19 PIES CON CAMPANA Y ANILLO DE GOMA REFORZADO"
    document = with_lines(golden("ecf-31"), 120, long_name[:75])
    pages = pages_of(render_pdf(document, signature()))
    assert len(pages) > 2
    for number, page in enumerate(pages, start=1):
        assert f"E310000000001 - Página {number}" in page
    assert "\n".join(pages).count("TUBO PVC SANITARIO") == 120
    assert "Código de seguridad: dcp79q" in pages[-1]
    assert "Monto total" in pages[-1]
    assert len(decoded(render_pdf(document, signature()))) == 1


def test_a_name_beyond_the_page_s_font_still_renders() -> None:
    root = etree.fromstring(golden("ecf-31"), parser())
    name = root.find("Encabezado/Emisor/RazonSocialEmisor")
    assert name is not None
    name.text = "FERRETERÍA ŐRSÉG 中文 SRL"
    pdf = render_pdf(etree.tostring(root, encoding="utf-8", xml_declaration=True), signature())
    assert "FERRETERÍA" in text_of(pdf)


async def test_printing_renders_off_the_event_loop_what_render_pdf_renders(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    threads: list[int] = []

    def rendering(signed_ecf: bytes, recorded: Signature) -> bytes:
        threads.append(threading.get_ident())
        return render_pdf(signed_ecf, recorded)

    monkeypatch.setattr(render, "render_pdf", rendering)
    assert await PdfPrinting().pdf(golden("ecf-31"), signature()) == render_pdf(golden("ecf-31"), signature())
    assert len(threads) == 1
    assert threads[0] != threading.get_ident()  # not the event loop's thread


@pytest.mark.parametrize("name", ["ecf-44", "ecf-45", "ecf-46", "ecf-47", "ecf-33", "ecf-34", "ecf-41"])
def test_every_type_renders(name: str) -> None:
    text = text_of(render_pdf(golden(name), signature()))
    assert "Código de seguridad: dcp79q" in text
    assert "Monto total" in text


def test_the_totals_and_the_qr_stay_together_on_a_page_of_their_own_when_the_lines_leave_no_room() -> None:
    first, second = pages_of(render_pdf(with_lines(golden("ecf-31"), 45, "TUBO"), signature()))
    assert "TUBO 45" in first
    assert "Monto total" not in first
    assert "TUBO" not in second
    assert "Monto total" in second
    assert "Código de seguridad: dcp79q" in second


def test_a_word_wider_than_its_column_is_broken_within_it() -> None:
    word = "X" * 80
    text = text_of(render_pdf(with_lines(golden("ecf-31"), 1, word), signature()))
    assert word not in text
    assert "".join(text.split()).count(word) == 1


@pytest.mark.parametrize(
    "document",
    [
        b"not xml",
        b"<ECF><Encabezado><IdDoc><TipoeCF>99</TipoeCF></IdDoc></Encabezado></ECF>",
        b"<ECF><Encabezado><IdDoc><TipoeCF>31</TipoeCF></IdDoc></Encabezado></ECF>",
    ],
)
def test_an_archived_e_cf_that_cannot_be_read_is_a_defect(document: bytes) -> None:
    with pytest.raises(SignedEcfUnreadableError, match="cannot be read") as caught:
        render_pdf(document, signature())
    assert (caught.value.code, caught.value.kind) == ("do_ecf_signed_ecf_unreadable", "internal")


def test_an_export_names_its_buyer_by_its_foreign_identifier() -> None:
    text = text_of(render_pdf(golden("ecf-46"), signature()))
    assert "Identificador extranjero: P12345678" in text
    assert "ACME TRADING LLC" in text
    assert "1 MAIN ST, MIAMI FL" in text
    assert "RNC/Cédula" not in text


def test_a_type_the_channel_does_not_know_is_unreadable_and_not_repeated() -> None:
    """A ``TipoeCF`` that is no ``EcfType``: the archive's defect, the value never repeated."""
    with pytest.raises(SignedEcfUnreadableError) as caught:
        render_pdf(b"<ECF><Encabezado><IdDoc><TipoeCF>E31</TipoeCF></IdDoc></Encabezado></ECF>", signature())
    assert "E31" not in str(caught.value)
    assert caught.value.__cause__ is None
