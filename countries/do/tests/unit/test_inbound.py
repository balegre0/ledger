"""Reading what counterparties send: received e-CF and commercial approvals, never trusted.

Every refusal is ``InboundDocumentInvalidError`` (``do_ecf_inbound_invalid``) and its message quotes nothing
of the document: each refused document here carries ``MARK`` in a value, and no message holds it.
"""

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType
from typing import Final
lazy from collections.abc import Callable, Mapping

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ledger_country_do.channels.ecf_v1.adapters.signing.stamp import signature_value
from ledger_country_do.channels.ecf_v1.adapters.xml.acecf import render_acecf
from ledger_country_do.channels.ecf_v1.adapters.xml.inbound import (
    MAX_INBOUND,
    InboundDocumentInvalidError,
    read_inbound_acecf,
    read_inbound_ecf,
)
from ledger_country_do.channels.ecf_v1.domain.drafts import BUYER_PARTS, BuyerPart
from ledger_country_do.channels.ecf_v1.domain.encf import EcfType, Encf
from ledger_country_do.channels.ecf_v1.domain.exchanges import CommercialApproval, InboundEcf
from ledger_country_do.channels.ecf_v1.domain.notation import SANTO_DOMINGO
from ledger_country_do.channels.ecf_v1.domain.totals import compute_totals
from ledger_country_do_testing.samples import (
    SIGNED_AT,
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
from ledger_fiscal_kit.xml.dsig import sign_enveloped
lazy from ledger_country_do.channels.ecf_v1.domain.drafts import EcfDraft
lazy from ledger_testing.pki import Issued

D = Decimal
GOLDEN: Final = Path(__file__).parents[1] / "contract" / "golden"
MARK: Final = "MARCA-SECRETA"
DRAFTS: Final[Mapping[str, Callable[[], EcfDraft]]] = MappingProxyType(
    {
        "ecf-31": credit_invoice,
        "ecf-32": lambda: consumer_invoice(2, large=True),
        "ecf-32-anonymous": consumer_invoice,
        "ecf-33": debit_note,
        "ecf-33-discounts": lambda: debit_note(discounts=True),
        "ecf-34": credit_note,
        "ecf-34-foreign-currency": lambda: credit_note(foreign=True),
        "ecf-41": purchase,
        "ecf-41-retentions": lambda: purchase(withholdings=True),
        "ecf-43": minor_expense,
        "ecf-44": special_regime_sale,
        "ecf-45": government_sale,
        "ecf-46": export_sale,
        "ecf-47": payment_abroad,
    }
)
"""The draft each e-CF golden is written from (the golden test's own list holds the zero-rate variant too)."""
APPROVED_AT: Final = datetime(2026, 10, 14, 8, 15, tzinfo=SANTO_DOMINGO)


def _golden(name: str) -> bytes:
    return (GOLDEN / f"{name}.xml").read_bytes()


def _sign(issued: Issued, document: bytes) -> bytes:
    return sign_enveloped(document, issued.key, issued.certificate)


@pytest.fixture(scope="module")
def signed_31(issued: Issued) -> bytes:
    marked = _golden("ecf-31").replace(b"FERRETERIA DUARTE SRL", MARK.encode())
    return _sign(issued, marked)


def _expected(draft: EcfDraft, signed: bytes) -> InboundEcf:
    totals = compute_totals(draft)
    rnc = draft.buyer.rnc if BuyerPart.RNC in BUYER_PARTS[draft.ecf_type] else None
    return InboundEcf(
        ecf_type=draft.ecf_type,
        encf=draft.encf,
        issuer_rnc=draft.issuer.rnc,
        issuer_name=draft.issuer.legal_name,
        buyer_rnc=rnc,
        issued_on=draft.issued_on,
        total=totals.total,
        itbis=totals.itbis_total,
        signed_at=SIGNED_AT,
        security_code=signature_value(signed)[:6],
    )


def test_every_type_is_read() -> None:
    assert {draft().ecf_type for draft in DRAFTS.values()} == set(EcfType)
    goldens = {path.stem for path in GOLDEN.glob("ecf-*.xml")}
    assert set(DRAFTS) <= goldens
    assert goldens - set(DRAFTS) == {"ecf-31-zero-and-exempt"}


@pytest.mark.parametrize("name", list(DRAFTS))
def test_a_signed_golden_e_cf_reads_as_its_draft(name: str, issued: Issued) -> None:
    signed = _sign(issued, _golden(name))
    assert read_inbound_ecf(signed) == _expected(DRAFTS[name](), signed)


def test_the_zero_rate_golden_reads_too(issued: Issued) -> None:
    read = read_inbound_ecf(_sign(issued, _golden("ecf-31-zero-and-exempt")))
    assert (read.encf, read.total, read.itbis) == (Encf(EcfType.CREDIT_INVOICE, 2), D("6265.67"), D("225.00"))


def test_an_e_cf_without_itbis_or_buyer_reads_none_for_them(issued: Issued) -> None:
    read = read_inbound_ecf(_sign(issued, _golden("ecf-43")))
    assert (read.buyer_rnc, read.itbis) == (None, None)


def _refused(document: bytes, reader: Callable[[bytes], object] = read_inbound_ecf) -> str:
    with pytest.raises(InboundDocumentInvalidError) as raised:
        reader(document)
    message = str(raised.value)
    assert raised.value.code == "do_ecf_inbound_invalid"
    assert MARK not in message
    assert MARK not in repr(raised.value)
    return message


def test_an_unsigned_e_cf_breaks_its_schema(signed_31: bytes) -> None:
    unsigned = _golden("ecf-31").replace(b"FERRETERIA DUARTE SRL", MARK.encode())
    assert "schema" in _refused(unsigned)
    assert read_inbound_ecf(signed_31).issuer_name == MARK


def test_a_document_with_a_doctype_is_refused_unread(signed_31: bytes) -> None:
    doctype = signed_31.replace(b"<ECF>", b'<!DOCTYPE ECF [<!ENTITY e "x">]><ECF>', 1)
    assert "DOCTYPE" in _refused(doctype)


def test_a_document_over_the_limit_is_refused_unread(signed_31: bytes) -> None:
    padding = b" " * (3 * 1024 * 1024)
    large = signed_31.replace(b"</ECF>", padding + b"</ECF>")
    assert len(large) > MAX_INBOUND
    assert str(MAX_INBOUND) in _refused(large)
    assert MAX_INBOUND == 2 * 1024 * 1024


@pytest.mark.parametrize(
    "document",
    [
        b"<ECF><Encabezado>",
        "<?xml version='1.0' encoding='UTF-16'?><ECF/>".encode("utf-16"),
        b"",
    ],
)
def test_what_is_not_well_formed_utf_8_xml_is_refused(document: bytes) -> None:
    _refused(document)


def test_another_root_is_refused(signed_31: bytes) -> None:
    assert "not an e-CF" in _refused(_golden("arecf-received"))
    namespaced = signed_31.replace(b"<ECF>", b'<ECF xmlns="urn:other">', 1)
    assert "not an e-CF" in _refused(namespaced)
    assert "not a commercial approval" in _refused(signed_31, read_inbound_acecf)


@pytest.mark.parametrize(
    ("wrong", "right"),
    [
        (b"<RNCEmisor>101850043</RNCEmisor>", b"<RNCEmisor>10185004</RNCEmisor>"),
        (b"<RNCEmisor>101850043</RNCEmisor>", b"<RNCEmisor> 101850043</RNCEmisor>"),
        (b"<TipoeCF>31</TipoeCF>", b"<TipoeCF>99</TipoeCF>"),
        (b"<TipoeCF>31</TipoeCF>", b"<TipoeCF>x</TipoeCF>"),
        (b"<TipoeCF>31</TipoeCF>", b""),
        (b"<MontoTotal>4887.58</MontoTotal>", b"<MontoTotal>-1</MontoTotal>"),
    ],
)
def test_a_value_the_schema_refuses_is_refused(signed_31: bytes, wrong: bytes, right: bytes) -> None:
    assert wrong in signed_31
    _refused(signed_31.replace(wrong, right, 1))


@pytest.mark.parametrize(
    ("wrong", "right", "problem"),
    [
        (b"<eNCF>E310000000001</eNCF>", b"<eNCF>E320000000001</eNCF>", "of another type"),
        (b"<eNCF>E310000000001</eNCF>", b"<eNCF>X310000000001</eNCF>", "e-NCF"),
        (b"<eNCF>E310000000001</eNCF>", b"<eNCF>E310000000000</eNCF>", "e-NCF"),
        (b"<FechaEmision>13-10-2026</FechaEmision>", b"<FechaEmision>31-02-2026</FechaEmision>", "date"),
        (b"<FechaHoraFirma>13-10-2026 09:30:00", b"<FechaHoraFirma>31-02-2026 09:30:00", "moment"),
    ],
)
def test_a_value_the_schema_lets_through_is_still_checked(
    signed_31: bytes, wrong: bytes, right: bytes, problem: str
) -> None:
    assert wrong in signed_31
    assert problem in _refused(signed_31.replace(wrong, right, 1))


def test_an_e_cf_whose_last_element_is_no_signature_is_refused(signed_31: bytes) -> None:
    start = signed_31.index(b"<Signature")
    end = signed_31.index(b"</Signature>") + len(b"</Signature>")
    assert "signature" in _refused(signed_31[:start] + b"<Firma/>" + signed_31[end:])


def test_a_signature_value_too_short_for_a_security_code_is_refused(signed_31: bytes) -> None:
    start = signed_31.index(b"<SignatureValue>") + len(b"<SignatureValue>")
    end = signed_31.index(b"</SignatureValue>")
    assert "signature" in _refused(signed_31[:start] + b"abc" + signed_31[end:])


@settings(max_examples=200, deadline=None)
@given(data=st.data())
def test_whatever_bytes_arrive_the_reader_returns_or_refuses(data: st.DataObject, signed_31: bytes) -> None:
    start = data.draw(st.integers(0, len(signed_31)))
    end = data.draw(st.integers(start, min(len(signed_31), start + 16)))
    patch = data.draw(st.binary(max_size=16) | st.sampled_from([b"<", b">", b"&", b"<!--", b"]]>", b"\x00"]))
    mutated = signed_31[:start] + patch + signed_31[end:]
    try:
        read: InboundEcf | None = read_inbound_ecf(mutated)
    except InboundDocumentInvalidError as error:
        read, message = None, str(error)
    else:
        message = ""
    assert MARK not in message
    assert read is None or read.encf.ecf_type is read.ecf_type


APPROVAL: Final = CommercialApproval(
    issuer_rnc="101850043",
    buyer_rnc="131098193",
    encf=Encf(EcfType.CREDIT_INVOICE, 1),
    issued_on=date(2026, 10, 13),
    total=D("4887.58"),
    approved=True,
    reason=None,
    at=APPROVED_AT,
)


def _approval(*, approved: bool = True, reason: str | None = None) -> bytes:
    return render_acecf(replace(APPROVAL, approved=approved, reason=reason))


def test_an_approval_reads_as_it_was_written_signed_or_not(issued: Issued) -> None:
    assert read_inbound_acecf(_approval()) == APPROVAL
    assert read_inbound_acecf(_sign(issued, _approval())) == APPROVAL
    rejected = read_inbound_acecf(_approval(approved=False, reason=MARK))
    assert rejected == replace(APPROVAL, approved=False, reason=MARK)


def test_the_approval_goldens_read(issued: Issued) -> None:
    assert read_inbound_acecf(_sign(issued, _golden("acecf-approved"))).approved
    assert read_inbound_acecf(_golden("acecf-rejected")).reason == "MERCANCIA RECIBIDA INCOMPLETA"


def test_a_moment_is_read_in_the_dominican_republic_s_time() -> None:
    assert read_inbound_acecf(_approval()).at == datetime(2026, 10, 14, 12, 15, tzinfo=UTC)


@pytest.mark.parametrize(
    "element", [b"<DetalleMotivoRechazo />", b"<DetalleMotivoRechazo>  </DetalleMotivoRechazo>"]
)
def test_an_empty_rejection_reason_is_none(element: bytes) -> None:
    written = _approval(approved=False, reason="X")
    rejected = written.replace(b"<DetalleMotivoRechazo>X</DetalleMotivoRechazo>", element)
    assert rejected != written
    assert read_inbound_acecf(rejected).reason is None


def test_tax_ids_are_read_as_the_schema_reads_integers() -> None:
    spaced = _approval().replace(b"<RNCEmisor>101850043</RNCEmisor>", b"<RNCEmisor> 101850043\n</RNCEmisor>")
    assert read_inbound_acecf(spaced).issuer_rnc == "101850043"


def test_an_approval_with_a_reason_contradicts_itself() -> None:
    contradictory = _approval(approved=False, reason=MARK).replace(
        b"<Estado>2</Estado>", b"<Estado>1</Estado>"
    )
    assert "reason" in _refused(contradictory, read_inbound_acecf)


@pytest.mark.parametrize(
    ("wrong", "right", "problem"),
    [
        (b"<eNCF>E310000000001</eNCF>", b"<eNCF>E3100000000</eNCF>", "e-NCF"),
        (b"<FechaEmision>13-10-2026</FechaEmision>", b"<FechaEmision>31-02-2026</FechaEmision>", "date"),
        (b"<FechaHoraAprobacionComercial>14-10-2026", b"<FechaHoraAprobacionComercial>31-02-2026", "moment"),
        (b"<Estado>1</Estado>", b"<Estado>3</Estado>", "schema"),
        (b"<RNCComprador>131098193</RNCComprador>", b"<RNCComprador>1310981</RNCComprador>", "schema"),
    ],
)
def test_an_approval_s_values_are_checked(wrong: bytes, right: bytes, problem: str) -> None:
    document = _approval()
    assert wrong in document
    marked = document.replace(wrong, right, 1).replace(b"</ACECF>", f"<{MARK}/></ACECF>".encode())
    assert problem in _refused(marked, read_inbound_acecf)


def test_an_approval_with_a_doctype_or_too_large_is_refused_unread() -> None:
    document = _approval()
    assert "DOCTYPE" in _refused(document.replace(b"<ACECF>", b"<!DOCTYPE ACECF><ACECF>"), read_inbound_acecf)
    large = document.replace(b"</ACECF>", b" " * MAX_INBOUND + b"</ACECF>")
    assert str(MAX_INBOUND) in _refused(large, read_inbound_acecf)
