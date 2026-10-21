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
from ledger_country_do.channels.ecf_v1.adapters.xml.arecf import render_arecf
from ledger_country_do.channels.ecf_v1.adapters.xml.inbound import (
    MAX_INBOUND,
    InboundDocumentInvalidError,
    InboundNames,
    name_inbound,
    read_inbound_acecf,
    read_inbound_arecf,
    read_inbound_ecf,
)
from ledger_country_do.channels.ecf_v1.domain.drafts import BUYER_PARTS, BuyerPart
from ledger_country_do.channels.ecf_v1.domain.encf import EcfType, Encf
from ledger_country_do.channels.ecf_v1.domain.exchanges import (
    CommercialApproval,
    InboundEcf,
    NotReceivedReason,
    Receipt,
)
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


@pytest.mark.parametrize(
    ("spelling", "approved"),
    [
        (b"1", True),
        (b"01", True),
        (b"+1", True),
        (b"+01", True),
        (b" 1 ", True),
        (b"2", False),
        (b"+02", False),
    ],
)
def test_an_approval_s_state_is_read_as_the_integer_it_writes(spelling: bytes, *, approved: bool) -> None:
    written = _approval().replace(b"<Estado>1</Estado>", b"<Estado>" + spelling + b"</Estado>")
    assert read_inbound_acecf(written).approved is approved


@pytest.mark.parametrize(
    ("wrong", "right"),
    [
        (b"<MontoTotal>4887.58", b"<MontoTotal>4<?x?>887.58"),
        (b"<Estado>1</Estado>", b"<Estado><?x?>1</Estado>"),
        (b"<RNCComprador>131098193", b"<RNCComprador>131<?x?>098193"),
        (b"<eNCF>E31", b"<eNCF>E<?x?>31"),
        (b"</DetalleAprobacionComercial>", b"<?x?></DetalleAprobacionComercial>"),
    ],
)
def test_a_processing_instruction_inside_an_approval_is_refused(wrong: bytes, right: bytes) -> None:
    document = _approval()
    assert wrong in document
    assert "only elements" in _refused(document.replace(wrong, right, 1), read_inbound_acecf)


@pytest.mark.parametrize(
    ("wrong", "right"),
    [
        (b"<MontoTotal>4887.58", b"<MontoTotal>4<?x?>887.58"),
        (b"<RNCComprador>131098193", b"<RNCComprador>131<?x?>098193"),
        (b"<RazonSocialEmisor>", b"<RazonSocialEmisor>A<?x?>"),
        (b"<SignatureValue>", b"<SignatureValue><?x?>"),
    ],
)
def test_a_processing_instruction_inside_an_e_cf_is_refused(
    signed_31: bytes, wrong: bytes, right: bytes
) -> None:
    assert wrong in signed_31
    assert "only elements" in _refused(signed_31.replace(wrong, right, 1))


@pytest.mark.parametrize(
    "total", [b"99999999999999999.00", b"999999999999999999.00", b"10000000000000000.00"]
)
def test_an_approval_s_total_beyond_any_e_cf_s_is_refused(total: bytes) -> None:
    # MontoTotal's type (totalDigits 18, two decimals) takes 17 or 18 integer digits when the cents are zero;
    # the total of an e-CF, which an approval names, has 16 at most.
    written = _approval().replace(b"4887.58", total)
    assert "value" in _refused(written, read_inbound_acecf)


def test_the_largest_total_an_e_cf_writes_is_read() -> None:
    written = _approval().replace(b"4887.58", b"9999999999999999.99")
    assert read_inbound_acecf(written).total == D("9999999999999999.99")
    padded = _approval().replace(b"4887.58", b"0000000000000000000001.00")
    assert read_inbound_acecf(padded).total == D("1.00")


@pytest.mark.parametrize(
    ("wrong", "right", "problem"),
    [
        (b"14-10-2026 08:15:00", b"31-12-9999 23:59:59", "moment"),
        (b"14-10-2026 08:15:00", b"01-01-0001 00:00:00", "moment"),
        (b"<FechaEmision>13-10-2026", "<FechaEmision>13-10-202٦".encode(), "date"),
    ],
)
def test_an_approval_s_dates_are_this_century_s_in_ascii_digits(
    wrong: bytes, right: bytes, problem: str
) -> None:
    assert problem in _refused(_approval().replace(wrong, right, 1), read_inbound_acecf)


@pytest.mark.parametrize(
    ("wrong", "right", "problem"),
    [
        (b"<FechaHoraFirma>13-10-2026", b"<FechaHoraFirma>01-01-1900", "moment"),
        (b"<FechaEmision>13-10-2026", b"<FechaEmision>13-10-1999", "date"),
    ],
)
def test_an_e_cf_s_dates_are_this_century_s(
    signed_31: bytes, wrong: bytes, right: bytes, problem: str
) -> None:
    assert problem in _refused(signed_31.replace(wrong, right, 1))


def test_a_signature_value_that_is_no_base64_is_refused(signed_31: bytes) -> None:
    start = signed_31.index(b"<SignatureValue>") + len(b"<SignatureValue>")
    assert "signature" in _refused(signed_31[:start] + b"!@#$%^&amp;" + signed_31[start:])


def test_a_malformed_document_s_refusal_chains_nothing_of_libxml2_s() -> None:
    with pytest.raises(InboundDocumentInvalidError) as raised:
        read_inbound_ecf(f"<ECF>{MARK}</Encabezado>".encode())
    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__


@pytest.mark.parametrize("spelling", [b"031", b"+31"])
def test_a_type_written_otherwise_than_dgii_s_code_is_refused(signed_31: bytes, spelling: bytes) -> None:
    # xs:integer reads "031" and "+31" as 31; the type selects the schema, so only DGII's code is taken.
    written = signed_31.replace(b"<TipoeCF>31</TipoeCF>", b"<TipoeCF>" + spelling + b"</TipoeCF>")
    assert "schema" in _refused(written)


_INTEGERS = st.sampled_from([b"0", b"00", b"+0", b"1", b"01", b"+1", b"+01", b"2", b"-1", b" 1 ", b"1 2"])
_TOTALS = st.sampled_from(
    [
        b"1.00",
        b"1",
        b"1.5",
        b"0.00",
        b"9999999999999999.99",
        b"99999999999999999.00",
        b"999999999999999999.00",
        b" 1.00 ",
        b"1 .00",
        b"-1.00",
        b"+1.00",
        b"1E2",
    ]
)
_PIECES = st.sampled_from([b"<?x?>", b"<!--c-->", b"&#x31;", b" ", b"\n", b"<y/>", b"&amp;", b"\x00", b""])
_TAGS: Final = (b"RNCEmisor>", b"eNCF>", b"FechaEmision>", b"RNCComprador>", b"FechaHoraAprobacionComercial>")


@settings(max_examples=300, deadline=None)
@given(data=st.data())
def test_whatever_an_approval_holds_the_reader_returns_or_refuses(data: st.DataObject) -> None:
    document = _approval(approved=False, reason=MARK)
    document = document.replace(b"<Estado>2", b"<Estado>" + data.draw(_INTEGERS), 1)
    document = document.replace(b"4887.58", data.draw(_TOTALS), 1)
    tag = data.draw(st.sampled_from(_TAGS))
    at = document.index(b"<" + tag) + len(tag) + 1 + data.draw(st.integers(0, 4))
    document = document[:at] + data.draw(_PIECES) + document[at:]
    try:
        read: CommercialApproval | None = read_inbound_acecf(document)
    except InboundDocumentInvalidError as error:
        read, message = None, f"{error} {error!r}"
    else:
        message = ""
    assert MARK not in message
    assert read is None or read.total < 10**16


RECEIPT: Final = Receipt(
    issuer_rnc="101850043",
    buyer_rnc="131098193",
    encf=Encf(EcfType.CREDIT_INVOICE, 1),
    received=True,
    reason=None,
    at=APPROVED_AT,
)


def test_a_receipt_reads_as_it_was_written_signed_or_not(issued: Issued) -> None:
    assert read_inbound_arecf(render_arecf(RECEIPT)) == RECEIPT
    assert read_inbound_arecf(_sign(issued, render_arecf(RECEIPT))) == RECEIPT
    duplicate = replace(RECEIPT, received=False, reason=NotReceivedReason.DUPLICATE)
    assert read_inbound_arecf(render_arecf(duplicate)) == duplicate
    assert read_inbound_arecf(_golden("arecf-not-received-3")).reason is NotReceivedReason.DUPLICATE


@pytest.mark.parametrize(
    ("state", "reason", "received"),
    [
        (b"00", None, True),
        (b"+0", None, True),
        (b" 0", None, True),
        (b"+01", b"+04", False),
        (b"1", b"03", False),
    ],
)
def test_a_receipt_s_state_and_reason_are_read_as_integers(
    state: bytes, reason: bytes | None, *, received: bool
) -> None:
    written = render_arecf(RECEIPT).replace(b"<Estado>0</Estado>", b"<Estado>" + state + b"</Estado>")
    if reason is not None:
        coded = b"<CodigoMotivoNoRecibido>" + reason + b"</CodigoMotivoNoRecibido><FechaHoraAcuseRecibo>"
        written = written.replace(b"<FechaHoraAcuseRecibo>", coded)
    read = read_inbound_arecf(written)
    assert read.received is received
    assert read.reason == (None if reason is None else NotReceivedReason(int(reason)))


def test_a_receipt_not_received_may_give_no_reason() -> None:
    written = render_arecf(RECEIPT).replace(b"<Estado>0</Estado>", b"<Estado>1</Estado>")
    assert read_inbound_arecf(written) == replace(RECEIPT, received=False)


_CODED: Final = b"<Estado>0</Estado><CodigoMotivoNoRecibido>3</CodigoMotivoNoRecibido>"


@pytest.mark.parametrize(
    ("wrong", "right", "problem"),
    [
        (b"<Estado>0</Estado>", _CODED, "value"),
        (b"14-10-2026", b"31-02-2026", "moment"),
        (b"<eNCF>E310000000001", b"<eNCF>e310000000001", "e-NCF"),
        (b"<eNCF>E310000000001", b"<eNCF>E3100000000", "e-NCF"),
        (b"<RNCComprador>131098193", b"<RNCComprador>131<?x?>098193", "only elements"),
        (b"<Estado>0", b"<Estado>5", "schema"),
    ],
)
def test_a_receipt_s_values_are_checked(wrong: bytes, right: bytes, problem: str) -> None:
    written = render_arecf(RECEIPT)
    assert wrong in written
    marked = written.replace(wrong, right, 1).replace(b"</ARECF>", f"<{MARK}/></ARECF>".encode())
    assert problem in _refused(marked, read_inbound_arecf)


def test_another_root_is_no_receipt() -> None:
    assert "not a receipt" in _refused(_approval(), read_inbound_arecf)


def test_an_e_cf_is_named_for_its_receipt_when_it_can_be_read_at_all(signed_31: bytes) -> None:
    named = InboundNames(issuer_rnc="101850043", buyer_rnc="131098193", encf=Encf(EcfType.CREDIT_INVOICE, 1))
    assert name_inbound(signed_31) == named
    assert name_inbound(_golden("ecf-31").replace(b"<TipoeCF>31", b"<TipoeCF>99")) == named
    unnamed_buyer = replace(named, buyer_rnc=None, encf=Encf(EcfType.MINOR_EXPENSES, 1))
    assert name_inbound(_golden("ecf-43")) == unnamed_buyer


@pytest.mark.parametrize(
    "document",
    [
        b"<!DOCTYPE ECF><ECF/>",
        b"<ECF><Encabezado>",
        b"<ARECF/>",
        b"<ECF xmlns='urn:x'/>",
        b"<ECF><Encabezado><Emisor><RNCEmisor>1018</RNCEmisor></Emisor></Encabezado></ECF>",
        b" " * (MAX_INBOUND + 1),
    ],
    ids=["doctype", "malformed", "other-root", "namespaced", "short-rnc", "too-large"],
)
def test_what_cannot_be_named_is_answered_with_no_receipt(document: bytes) -> None:
    assert name_inbound(document) is None


@pytest.mark.parametrize(
    ("wrong", "right"),
    [
        (b"<eNCF>E310000000001", b"<eNCF>E3100000001"),
        (b"<RNCComprador>131098193", b"<RNCComprador>131<?x?>098193"),
        (b"<RNCEmisor>101850043", b"<RNCEmisor> 101850043"),
        (b"<RNCComprador>131098193", b"<RNCComprador>1310981"),
    ],
)
def test_a_name_that_is_no_e_ncf_or_tax_id_names_nothing(wrong: bytes, right: bytes) -> None:
    document = _golden("ecf-31")
    assert wrong in document
    assert name_inbound(document.replace(wrong, right, 1)) is None
