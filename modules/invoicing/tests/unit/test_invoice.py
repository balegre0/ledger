from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Final
from uuid import UUID
lazy from collections.abc import Callable

import pytest

from ledger_invoicing._manifest import MANIFEST
from ledger_invoicing.domain.errors import (
    CorrectionInvalidError,
    CorrectionProblem,
    CorrectionRequiredError,
    CorrectionUnexpectedError,
    ExternalReferenceTakenError,
    InvalidEmailError,
    InvalidTextError,
    InvoiceChangedError,
    InvoiceNotFoundError,
    InvoiceNotIssuableError,
    InvoiceNotVoidableError,
)
from ledger_invoicing.domain.ids import INVOICE_PREFIX, InvoiceId
from ledger_invoicing.domain.invoice import Invoice, InvoiceStatus, Opening, require_correction_named
from ledger_invoicing.domain.lines import LineTerms
from ledger_invoicing.domain.parties import Seller, party
from ledger_invoicing.domain.pricing import Dating, EstablishmentRef, Terms, price
from ledger_invoicing.domain.text import parse_external_reference, parse_reason
from ledger_invoicing.events import InvoiceFiscalUpdated, InvoiceIssued, InvoiceVoided
from ledger_kernel.errors import ErrorKind, InvalidValueError
from ledger_kernel.ids import CompanyId, OrganizationId, format_id, new_id
from ledger_kernel.money import Currency
from ledger_kernel.regimes import Direction, DocumentKind
from ledger_kernel.tax_ids import TaxId, TaxIdScheme
from ledger_kernel.tenancy import Mode, Tenant
from ledger_platform.data.codec import encode, payload_of
lazy from ledger_invoicing.domain.pricing import Pricing
lazy from ledger_kernel.regimes import RegimeDef

NOW: Final = datetime(2026, 10, 12, 13, 0, tzinfo=UTC)
LATER: Final = NOW + timedelta(hours=1)
ORGANIZATION: Final = OrganizationId(new_id())
TENANT: Final = Tenant(ORGANIZATION, CompanyId(new_id()), Mode.TEST)
SELLER: Final = Seller(legal_name="Acme SRL", tax_id=TaxId(TaxIdScheme("do.rnc"), "101850043"))
DOP: Final = Currency.of("DOP")
TERMS: Final = Terms(
    currency=DOP,
    lines=(
        LineTerms(
            description="Widget", quantity=Decimal(2), unit_price=Decimal(50), taxes=("do.itbis.standard",)
        ),
    ),
)


@pytest.fixture
def pricing(regime: RegimeDef) -> Pricing:
    return price(
        TERMS,
        regime=regime,
        functional_currency=DOP,
        establishment=EstablishmentRef(code="MAIN", time_zone="America/Santo_Domingo"),
        dating=Dating(today=date(2026, 10, 12)),
    )


def new(
    pricing: Pricing,
    *,
    kind: DocumentKind = DocumentKind.INVOICE,
    corrects: Invoice | None = None,
    seller: Seller | None = None,
    reference: str | None = None,
) -> tuple[Invoice, InvoiceIssued | None]:
    opening = Opening(
        id=InvoiceId(new_id()),
        tenant=TENANT,
        kind=kind,
        direction=Direction.SALES,
        external_reference=None if reference is None else parse_external_reference(reference),
        terms=TERMS,
    )
    return Invoice.new(opening, corrects=corrects, pricing=pricing, seller=seller, now=NOW)


def draft(pricing: Pricing) -> Invoice:
    invoice, issued = new(pricing)
    assert issued is None
    return invoice


def issued_invoice(pricing: Pricing) -> Invoice:
    invoice, _ = new(pricing, seller=SELLER)
    return invoice


# ── Creating ────────────────────────────────────────────────────────────────────


def test_a_document_has_the_json_form_its_audit_hashes(pricing: Pricing) -> None:
    """A unit's audit hashes a changed document as the platform's codec writes it: every part has a form."""
    encoded = encode(issued_invoice(pricing))
    assert isinstance(encoded, dict)
    assert (encoded["status"], encoded["kind"]) == ("issued", "invoice")
    assert "accepted_by" not in encoded  # what channels made of it is the fiscal mirror's, not the document's


def test_a_draft_is_kept_without_its_seller_and_announces_nothing(pricing: Pricing) -> None:
    invoice, issued = new(pricing, reference="ORD-1")
    assert issued is None
    assert (invoice.status, invoice.version, invoice.seller, invoice.issued_at) == (
        InvoiceStatus.DRAFT,
        1,
        None,
        None,
    )
    assert (invoice.tenant, invoice.kind, invoice.direction, invoice.external_reference) == (
        TENANT,
        DocumentKind.INVOICE,
        Direction.SALES,
        "ORD-1",
    )
    assert (invoice.terms, invoice.pricing, invoice.created_at, invoice.updated_at) == (
        TERMS,
        pricing,
        NOW,
        NOW,
    )
    assert (invoice.corrects, invoice.voided_at, invoice.void_reason) == (None, None, None)


def test_a_document_issued_at_once_is_at_its_first_version(pricing: Pricing) -> None:
    invoice, issued = new(pricing, seller=SELLER)
    assert (invoice.status, invoice.version, invoice.seller, invoice.issued_at) == (
        InvoiceStatus.ISSUED,
        1,
        SELLER,
        NOW,
    )
    assert issued == InvoiceIssued(
        scope=TENANT,
        subject=invoice.id,
        aggregate_version=1,
        kind=DocumentKind.INVOICE,
        direction=Direction.SALES,
        corrects=None,
    )


# ── Issuing ─────────────────────────────────────────────────────────────────────


def test_issuing_a_draft_snapshots_the_seller_and_announces_it(pricing: Pricing, regime: RegimeDef) -> None:
    created = draft(pricing)
    repriced = price(
        TERMS,
        regime=regime,
        functional_currency=DOP,
        establishment=pricing.establishment,
        dating=Dating(today=date(2026, 10, 20)),
    )
    invoice, issued = created.issue(seller=SELLER, pricing=repriced, corrected=None, now=LATER)
    assert (invoice.status, invoice.version, invoice.seller, invoice.pricing.issue_date) == (
        InvoiceStatus.ISSUED,
        2,
        SELLER,
        date(2026, 10, 20),
    )
    assert (invoice.issued_at, invoice.updated_at, invoice.created_at) == (LATER, LATER, NOW)
    assert (issued.subject, issued.aggregate_version) == (invoice.id, 2)


def test_only_a_draft_is_issued(pricing: Pricing) -> None:
    issued = issued_invoice(pricing)
    voided, _ = issued.void(reason=None, corrected_by_a_note=False, accepted=False, now=LATER)
    for invoice in (issued, voided):
        with pytest.raises(InvoiceNotIssuableError) as caught:
            invoice.issue(seller=SELLER, pricing=pricing, corrected=None, now=LATER)
        assert str(caught.value) == f"invoice {format_id(INVOICE_PREFIX, invoice.id)} is not a draft"
        assert (caught.value.kind, caught.value.code) == (ErrorKind.CONFLICT, "invoice_not_issuable")


# ── Voiding ─────────────────────────────────────────────────────────────────────


def test_voiding_an_issued_document_keeps_its_reason_and_announces_it(pricing: Pricing) -> None:
    issued = issued_invoice(pricing)
    voided, event = issued.void(
        reason=parse_reason("Wrong buyer"), corrected_by_a_note=False, accepted=False, now=LATER
    )
    assert (voided.status, voided.version, voided.voided_at, voided.void_reason, voided.updated_at) == (
        InvoiceStatus.VOIDED,
        2,
        LATER,
        "Wrong buyer",
        LATER,
    )
    assert event == InvoiceVoided(
        scope=TENANT,
        subject=issued.id,
        aggregate_version=2,
        kind=DocumentKind.INVOICE,
        direction=Direction.SALES,
    )


def test_a_draft_a_voided_document_and_one_a_channel_accepted_are_not_voided(pricing: Pricing) -> None:
    issued = issued_invoice(pricing)
    voided, _ = issued.void(reason=None, corrected_by_a_note=False, accepted=False, now=LATER)
    refused = (
        (draft(pricing), False, False),
        (voided, False, False),
        (issued, False, True),  # a channel accepted it: a credit note corrects it
        (issued, True, False),
    )
    for invoice, corrected, accepted in refused:
        with pytest.raises(InvoiceNotVoidableError) as caught:
            invoice.void(reason=None, corrected_by_a_note=corrected, accepted=accepted, now=LATER)
        assert str(caught.value) == (
            f"invoice {format_id(INVOICE_PREFIX, invoice.id)} is not issued, a channel accepted it, "
            "or a note corrects it"
        )
        assert (caught.value.kind, caught.value.code) == (ErrorKind.CONFLICT, "invoice_not_voidable")


# ── Corrections ─────────────────────────────────────────────────────────────────


def test_an_invoice_corrects_nothing_and_a_note_corrects_an_invoice(pricing: Pricing) -> None:
    original = issued_invoice(pricing)
    with pytest.raises(CorrectionUnexpectedError) as unexpected:
        new(pricing, corrects=original)
    assert str(unexpected.value) == "an invoice corrects nothing: corrects goes with a credit or debit note"
    assert (unexpected.value.kind, unexpected.value.code) == (ErrorKind.INVALID, "correction_unexpected")
    for kind in (DocumentKind.CREDIT_NOTE, DocumentKind.DEBIT_NOTE):
        with pytest.raises(CorrectionRequiredError) as required:
            new(pricing, kind=kind)
        assert str(required.value) == "a credit or debit note names the invoice it corrects (corrects)"
        assert (required.value.kind, required.value.code) == (ErrorKind.INVALID, "correction_required")
        note, issued = new(pricing, kind=kind, corrects=original, seller=SELLER)
        assert note.corrects == original.id
        assert issued is not None
        assert issued.corrects == original.id


def test_a_note_corrects_an_issued_invoice_of_its_company_mode_and_direction(pricing: Pricing) -> None:
    original = issued_invoice(pricing)
    voided, _ = original.void(reason=None, corrected_by_a_note=False, accepted=False, now=LATER)
    note, _ = new(pricing, kind=DocumentKind.CREDIT_NOTE, corrects=original, seller=SELLER)
    for corrected, problem in (
        (draft(pricing), CorrectionProblem.NOT_ISSUED),
        (voided, CorrectionProblem.NOT_ISSUED),
        (note, CorrectionProblem.NOT_AN_INVOICE),
        (replace(original, direction=Direction.PURCHASE), CorrectionProblem.OTHER_DIRECTION),
        (
            replace(original, terms=replace(TERMS, currency=Currency.of("USD"), exchange_rate=Decimal(60))),
            CorrectionProblem.OTHER_CURRENCY,
        ),
        (
            replace(original, tenant=Tenant(ORGANIZATION, CompanyId(new_id()), Mode.TEST)),
            CorrectionProblem.NOT_FOUND,
        ),
        (
            replace(original, tenant=Tenant(ORGANIZATION, TENANT.company, Mode.LIVE)),
            CorrectionProblem.NOT_FOUND,
        ),
    ):
        with pytest.raises(CorrectionInvalidError) as caught:
            new(pricing, kind=DocumentKind.DEBIT_NOTE, corrects=corrected)
        assert str(caught.value) == f"corrects {problem}"
        assert (caught.value.kind, caught.value.code) == (ErrorKind.INVALID, "correction_invalid")


def test_a_note_is_never_dated_before_the_invoice_it_corrects(pricing: Pricing) -> None:
    """Checked when a note is made and again when a draft note is issued, on the invoice as it is locked."""
    later = replace(pricing, issue_date=pricing.issue_date + timedelta(days=1))
    original = issued_invoice(later)
    with pytest.raises(CorrectionInvalidError) as caught:
        new(pricing, kind=DocumentKind.CREDIT_NOTE, corrects=original, seller=SELLER)
    assert str(caught.value) == "corrects is dated after the note"
    assert caught.value.code == "correction_invalid"
    same_day, _ = new(later, kind=DocumentKind.CREDIT_NOTE, corrects=original, seller=SELLER)
    assert same_day.corrects == original.id
    note, _ = new(later, kind=DocumentKind.CREDIT_NOTE, corrects=original)
    with pytest.raises(CorrectionInvalidError, match=r"^corrects is dated after the note$"):
        note.issue(seller=SELLER, pricing=pricing, corrected=original, now=LATER)


def test_whether_a_document_names_an_invoice_is_checked_before_anything_else() -> None:
    require_correction_named(DocumentKind.INVOICE, named=False)
    require_correction_named(DocumentKind.CREDIT_NOTE, named=True)
    with pytest.raises(CorrectionUnexpectedError):
        require_correction_named(DocumentKind.INVOICE, named=True)
    with pytest.raises(CorrectionRequiredError):
        require_correction_named(DocumentKind.DEBIT_NOTE, named=False)


def test_issuing_a_note_checks_its_invoice_again(pricing: Pricing) -> None:
    original = issued_invoice(pricing)
    note, _ = new(pricing, kind=DocumentKind.CREDIT_NOTE, corrects=original)
    voided, _ = original.void(reason=None, corrected_by_a_note=False, accepted=False, now=LATER)
    with pytest.raises(CorrectionInvalidError, match=r"^corrects is not an issued document$"):
        note.issue(seller=SELLER, pricing=pricing, corrected=voided, now=LATER)
    issued, event = note.issue(seller=SELLER, pricing=pricing, corrected=original, now=LATER)
    assert (issued.status, event.corrects) == (InvoiceStatus.ISSUED, original.id)


def test_issuing_a_note_without_the_invoice_it_names_is_a_defect(pricing: Pricing) -> None:
    original = issued_invoice(pricing)
    note, _ = new(pricing, kind=DocumentKind.CREDIT_NOTE, corrects=original)
    other = issued_invoice(pricing)
    for corrected in (None, other):
        with pytest.raises(InvalidValueError, match=r"^corrected must be the invoice the note names$"):
            note.issue(seller=SELLER, pricing=pricing, corrected=corrected, now=LATER)
    invoice = draft(pricing)
    with pytest.raises(InvalidValueError, match=r"^corrected must be the invoice the note names$"):
        invoice.issue(seller=SELLER, pricing=pricing, corrected=original, now=LATER)


# ── What an invoice always is ─────────────────────────────────────────────────

CONTRADICTIONS: Final[list[tuple[Callable[[Invoice], Invoice], str]]] = [
    (lambda invoice: replace(invoice, version=0), "invoice.version"),
    (
        lambda invoice: replace(invoice, tenant=Tenant(ORGANIZATION, TENANT.company, None)),
        "invoice.tenant",
    ),
    (lambda invoice: replace(invoice, seller=SELLER), "invoice.seller"),
    (lambda invoice: replace(invoice, issued_at=NOW), "invoice.seller"),
    (lambda invoice: replace(invoice, voided_at=NOW), "invoice.voided_at"),
    (lambda invoice: replace(invoice, void_reason=parse_reason("x")), "invoice.voided_at"),
    (lambda invoice: replace(invoice, corrects=InvoiceId(UUID(int=1))), "invoice.corrects"),
    (lambda invoice: replace(invoice, kind=DocumentKind.CREDIT_NOTE), "invoice.corrects"),
]


@pytest.mark.parametrize(("change", "field"), CONTRADICTIONS)
def test_an_invoice_that_contradicts_itself_is_a_defect(
    pricing: Pricing, change: Callable[[Invoice], Invoice], field: str
) -> None:
    with pytest.raises(InvalidValueError, match=rf"^{field} must be "):
        change(draft(pricing))


def test_an_issued_invoice_has_its_seller_and_a_voided_one_its_time(pricing: Pricing) -> None:
    issued = issued_invoice(pricing)
    with pytest.raises(InvalidValueError, match=r"^invoice\.seller must be "):
        replace(issued, seller=None)
    with pytest.raises(InvalidValueError, match=r"^invoice\.voided_at must be "):
        replace(issued, status=InvoiceStatus.VOIDED)


# ── Parties, events, errors ─────────────────────────────────────────────────────


def test_a_seller_and_an_invoice_show_nothing_a_client_wrote_in_their_repr(pricing: Pricing) -> None:
    assert repr(SELLER) == "Seller(tax_id=TaxId(scheme='do.rnc', value='******043'))"
    invoice = issued_invoice(pricing)
    voided, _ = invoice.void(
        reason=parse_reason("Juana's order"), corrected_by_a_note=False, accepted=False, now=LATER
    )
    shown = repr(voided)
    assert shown == f"Invoice(id={voided.id!r}, kind='invoice', status='voided', version=2)"
    assert "Acme" not in shown
    assert "Widget" not in shown
    assert "Juana" not in shown


def test_a_party_shows_nothing_personal_in_its_repr() -> None:
    buyer = party(
        legal_name="Juana Pérez",
        tax_id=TaxId(TaxIdScheme("do.cedula"), "00113918205"),
        address="Calle 1",
        email="juana@example.com",
    )
    assert (buyer.legal_name, buyer.address, buyer.email) == ("Juana Pérez", "Calle 1", "juana@example.com")
    assert repr(buyer) == "Party(tax_id=TaxId(scheme='do.cedula', value='********205'))"
    anonymous = party(legal_name="Consumidor final")
    assert (anonymous.tax_id, anonymous.address, anonymous.email) == (None, None, None)
    assert repr(anonymous) == "Party(tax_id=None)"


def test_a_party_s_fields_are_named_under_the_party() -> None:
    with pytest.raises(InvalidEmailError, match=r"^supplier\.email is not an email address"):
        party(field="supplier", legal_name="Acme", email="nope")
    with pytest.raises(InvalidTextError, match=r"^buyer\.legal_name is empty$"):
        party(legal_name="  ")
    with pytest.raises(InvalidTextError, match=r"^buyer\.address is empty$"):
        party(legal_name="Acme", address=" ")


def test_events_are_the_ones_the_manifest_declares_and_encode(pricing: Pricing) -> None:
    declared = {(event.type, event.version) for event in MANIFEST.events.publishes}
    assert declared == {
        (InvoiceIssued.TYPE, InvoiceIssued.VERSION),
        (InvoiceVoided.TYPE, InvoiceVoided.VERSION),
        (InvoiceFiscalUpdated.TYPE, InvoiceFiscalUpdated.VERSION),
    }
    public = {event.type: event.public for event in MANIFEST.events.publishes}
    assert public[InvoiceFiscalUpdated.TYPE] == "invoice.fiscal_updated"
    invoice, issued = new(pricing, seller=SELLER)
    _, voided = invoice.void(reason=None, corrected_by_a_note=False, accepted=False, now=LATER)
    assert issued is not None
    assert payload_of(issued) == {"kind": "invoice", "direction": "sales", "corrects": None}
    assert payload_of(voided) == {"kind": "invoice", "direction": "sales"}
    updated = InvoiceFiscalUpdated(
        scope=TENANT,
        subject=invoice.id,
        aggregate_version=3,
        channel="do-ecf-v1",
        status="accepted",
        version=3,
    )
    assert payload_of(updated) == {"channel": "do-ecf-v1", "status": "accepted", "version": 3}
    with pytest.raises(
        InvalidValueError, match=r"^invoice_fiscal_updated\.aggregate_version must be the version$"
    ):
        InvoiceFiscalUpdated(
            scope=TENANT,
            subject=invoice.id,
            aggregate_version=2,
            channel="do-ecf-v1",
            status="pending",
            version=3,
        )


def test_errors_name_documents_by_their_public_id_and_repeat_nothing_sent() -> None:
    invoice = UUID(int=7)
    public = format_id(INVOICE_PREFIX, invoice)
    taken = ExternalReferenceTakenError(invoice)
    assert str(taken) == f"an invoice of this type holds this external_reference already: {public}"
    assert (taken.kind, taken.code, taken.existing_id) == (
        ErrorKind.CONFLICT,
        "external_reference_taken",
        invoice,
    )
    assert str(InvoiceNotFoundError()) == "invoice was not found"
    assert (InvoiceNotFoundError.kind, InvoiceNotFoundError.code) == (
        ErrorKind.NOT_FOUND,
        "invoice_not_found",
    )
    changed = InvoiceChangedError(invoice)
    assert str(changed) == f"invoice {public} changed meanwhile: read it again"
    assert (changed.kind, changed.code) == (ErrorKind.CONFLICT, "invoice_changed")
