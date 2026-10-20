"""A fiscal draft: what fiscal hands a channel, refused when it could not be a fiscal document's input."""

from dataclasses import replace
from datetime import date
from decimal import Decimal
from types import MappingProxyType
from typing import Final
from uuid import UUID

import pytest
from pydantic import TypeAdapter

from ledger_fiscal_kit.drafts import (
    DocumentRef,
    FiscalDraft,
    FiscalDraftInvalidError,
    FiscalLine,
    FiscalLineTax,
    FiscalParty,
    FiscalReference,
)
from ledger_kernel.errors import ErrorKind, InvalidValueError
from ledger_kernel.ids import CompanyId, OrganizationId
from ledger_kernel.money import Currency, Money
from ledger_kernel.regimes import (
    CategoryTotal,
    Direction,
    DocumentKind,
    RegimeKey,
    TaxCategoryKey,
    TaxCategoryKind,
    Totals,
)
from ledger_kernel.tax_ids import TaxId, TaxIdScheme
from ledger_kernel.tenancy import Mode, Tenant
from ledger_platform.data.codec import JsonValue, canonical_json, encode

ORGANIZATION: Final = OrganizationId(UUID("0199e3a0-0000-7000-8000-000000000001"))
COMPANY: Final = CompanyId(UUID("0199e3a0-0000-7000-8000-000000000002"))
DOCUMENT: Final = DocumentRef(UUID("0199e3a0-0000-7000-8000-000000000003"))
CORRECTED: Final = DocumentRef(UUID("0199e3a0-0000-7000-8000-000000000004"))
DOP: Final = Currency.of("DOP")
USD: Final = Currency.of("USD")
ITBIS: Final = TaxCategoryKey("do.itbis.standard")
ISSUER_RNC: Final = TaxId(TaxIdScheme("do.rnc"), "101850043")
COUNTERPARTY_RNC: Final = TaxId(TaxIdScheme("do.rnc"), "131880681")
JSON: Final = TypeAdapter(JsonValue)


def money(amount: str, currency: Currency = DOP) -> Money:
    return Money.of(amount, currency)


def totals(currency: Currency = DOP) -> Totals:
    return Totals(
        net=money("1000.00", currency),
        categories=(
            CategoryTotal(
                category=ITBIS,
                percent=Decimal(18),
                base=money("1000.00", currency),
                tax=money("180.00", currency),
            ),
        ),
        tax=money("180.00", currency),
        gross=money("1180.00", currency),
    )


def line(number: int = 1, *, currency: Currency = DOP, tax_currency: Currency | None = None) -> FiscalLine:
    return FiscalLine(
        number=number,
        description="Cemento gris 42.5 kg",
        quantity=Decimal(2),
        unit_price=Decimal(500),
        discount=Decimal(0),
        base=money("1000.00", currency),
        taxes=(
            FiscalLineTax(
                category=ITBIS,
                kind=TaxCategoryKind.STANDARD,
                percent=Decimal(18),
                since=date(2012, 11, 9),
                amount=money("180.00", tax_currency or currency),
            ),
        ),
    )


DRAFT: Final = FiscalDraft(
    document=DOCUMENT,
    version=1,
    organization=ORGANIZATION,
    company=COMPANY,
    mode=Mode.TEST,
    channel="do-ecf-v1",
    environment="testecf",
    regime=RegimeKey("do"),
    kind=DocumentKind.INVOICE,
    direction=Direction.SALES,
    issuer=FiscalParty(tax_id=ISSUER_RNC, legal_name="Ferretería Duarte SRL"),
    establishment_code="001",
    time_zone="America/Santo_Domingo",
    counterparty=FiscalParty(
        tax_id=COUNTERPARTY_RNC,
        legal_name="Constructora Peña SRL",
        address="Calle 5 #12, Santiago",
        email="compras@example.com",
    ),
    lines=(line(),),
    totals=totals(),
    currency=DOP,
    exchange_rate=None,
    functional_totals=None,
    fields={"income_type": "01", "payment": {"kind": 1, "amount": "5", "currency": "DOP"}},
    issue_date=date(2026, 10, 19),
    tax_point=date(2026, 10, 19),
)
NOTE: Final = replace(
    DRAFT,
    kind=DocumentKind.CREDIT_NOTE,
    references=(FiscalReference(document=CORRECTED, issue_date=date(2026, 10, 1)),),
    external_reference="NC-0001",
)
FOREIGN: Final = replace(
    DRAFT,
    currency=USD,
    lines=(line(currency=USD),),
    totals=totals(USD),
    exchange_rate=Decimal("58.75"),
    functional_totals=totals(),
)


def through_json(draft: FiscalDraft) -> JsonValue:
    """``draft`` as the platform's codec stores it (``jsonb``), parsed back."""
    return JSON.validate_json(canonical_json(encode(draft)))


def test_a_draft_s_scope_is_its_tenant() -> None:
    assert DRAFT.scope == Tenant(ORGANIZATION, COMPANY, Mode.TEST)


def test_a_draft_keeps_what_it_was_given() -> None:
    assert (DRAFT.references, DRAFT.external_reference, DRAFT.lines[0].goods) == ((), None, None)
    assert (DRAFT.lines[0].quantity, DRAFT.lines[0].discount) == (Decimal(2), Decimal(0))
    assert (DRAFT.establishment_code, DRAFT.time_zone) == ("001", "America/Santo_Domingo")
    assert DRAFT.lines[0].taxes[0].kind is TaxCategoryKind.STANDARD
    assert DRAFT.counterparty is not None
    assert (DRAFT.counterparty.address, DRAFT.counterparty.email) == (
        "Calle 5 #12, Santiago",
        "compras@example.com",
    )
    assert (NOTE.references[0].document, NOTE.references[0].issue_date) == (CORRECTED, date(2026, 10, 1))
    assert (FOREIGN.exchange_rate, FOREIGN.functional_totals) == (Decimal("58.75"), totals())


def test_a_draft_may_have_no_counterparty() -> None:
    assert replace(DRAFT, counterparty=None).counterparty is None


def test_a_draft_s_fields_are_its_own_copy() -> None:
    nested: dict[str, JsonValue] = {"kind": 1}
    fields: dict[str, JsonValue] = {"income_type": "01", "payment": nested}
    draft = replace(DRAFT, fields=fields)
    fields["income_type"] = "02"
    nested["kind"] = 2
    assert draft.fields == {"income_type": "01", "payment": {"kind": 1}}
    assert isinstance(draft.fields, MappingProxyType)


def test_an_issuer_without_a_tax_id_is_refused() -> None:
    with pytest.raises(FiscalDraftInvalidError) as caught:
        replace(DRAFT, issuer=FiscalParty(tax_id=None, legal_name="Ferretería Duarte SRL"))
    assert isinstance(caught.value, InvalidValueError)
    assert (caught.value.kind, caught.value.code) == (ErrorKind.INTERNAL, "fiscal_draft_invalid")
    assert str(caught.value) == "fiscal_draft.issuer.tax_id must be the issuing company's primary tax id"


def test_a_draft_without_lines_is_refused() -> None:
    with pytest.raises(FiscalDraftInvalidError, match=r"fiscal_draft.lines must be one line or more"):
        replace(DRAFT, lines=())


@pytest.mark.parametrize("numbers", [(2,), (1, 1), (1, 3), (0, 1)])
def test_lines_are_numbered_from_one_in_order(numbers: tuple[int, ...]) -> None:
    with pytest.raises(FiscalDraftInvalidError, match=r"fiscal_draft.lines must be numbered 1 to n in order"):
        replace(DRAFT, lines=tuple(line(number) for number in numbers))
    assert replace(DRAFT, lines=(line(1), line(2))).lines[1].number == 2


def test_a_draft_starts_at_version_one() -> None:
    with pytest.raises(FiscalDraftInvalidError, match=r"fiscal_draft.version must be 1 or more"):
        replace(DRAFT, version=0)


def test_every_amount_is_in_the_document_s_currency() -> None:
    rule = r"must be in the document's currency"
    with pytest.raises(FiscalDraftInvalidError, match=r"fiscal_draft.totals " + rule):
        replace(DRAFT, totals=totals(USD))
    with pytest.raises(FiscalDraftInvalidError, match=r"fiscal_draft.lines\[0\].base " + rule):
        replace(DRAFT, lines=(line(currency=USD, tax_currency=DOP),))
    with pytest.raises(FiscalDraftInvalidError, match=r"fiscal_draft.lines\[1\].taxes\[0\].amount " + rule):
        replace(DRAFT, lines=(line(1), line(2, tax_currency=USD)))


def test_a_rate_comes_with_the_functional_totals() -> None:
    rule = r"fiscal_draft.exchange_rate must be given with the functional totals, and only with them"
    with pytest.raises(FiscalDraftInvalidError, match=rule):
        replace(FOREIGN, functional_totals=None)
    with pytest.raises(FiscalDraftInvalidError, match=rule):
        replace(FOREIGN, exchange_rate=None)


@pytest.mark.parametrize("rate", [Decimal(0), Decimal("-58.75")])
def test_a_rate_is_positive(rate: Decimal) -> None:
    with pytest.raises(FiscalDraftInvalidError, match=r"fiscal_draft.exchange_rate must be more than 0"):
        replace(FOREIGN, exchange_rate=rate)


def test_functional_totals_are_in_another_currency() -> None:
    with pytest.raises(
        FiscalDraftInvalidError,
        match=r"fiscal_draft.functional_totals must be in a currency other than the document's",
    ):
        replace(FOREIGN, functional_totals=totals(USD))


@pytest.mark.parametrize("kind", [DocumentKind.CREDIT_NOTE, DocumentKind.DEBIT_NOTE])
def test_a_note_refers_to_what_it_corrects(kind: DocumentKind) -> None:
    with pytest.raises(
        FiscalDraftInvalidError, match=r"fiscal_draft.references must be one or more for a note"
    ):
        replace(NOTE, kind=kind, references=())
    assert replace(NOTE, kind=kind).kind is kind


def test_an_invoice_refers_to_nothing() -> None:
    with pytest.raises(FiscalDraftInvalidError, match=r"fiscal_draft.references must be none for an invoice"):
        replace(NOTE, kind=DocumentKind.INVOICE)


@pytest.mark.parametrize("zone", ["Mars/Olympus_Mons", "../etc/passwd", "", "America/"])
def test_the_time_zone_is_one_the_system_knows(zone: str) -> None:
    with pytest.raises(FiscalDraftInvalidError, match=r"fiscal_draft.time_zone must be an IANA time zone"):
        replace(DRAFT, time_zone=zone)


@pytest.mark.parametrize("draft", [DRAFT, NOTE, FOREIGN, replace(DRAFT, counterparty=None)])
def test_a_draft_reads_back_from_its_json_form(draft: FiscalDraft) -> None:
    assert FiscalDraft.read(through_json(draft)) == draft


def test_fields_read_back_as_they_were_written() -> None:
    read = FiscalDraft.read(through_json(DRAFT))
    assert read.fields == {"income_type": "01", "payment": {"kind": 1, "amount": "5", "currency": "DOP"}}


def json_with(**changes: JsonValue) -> JsonValue:
    """``DRAFT``'s JSON form with ``changes`` at its top level."""
    found = through_json(DRAFT)
    assert isinstance(found, dict)
    return found | changes


def json_with_line_currency(code: str) -> JsonValue:
    """``DRAFT``'s JSON form with its first line's base in currency ``code``."""
    found = through_json(DRAFT)
    assert isinstance(found, dict)
    lines = found["lines"]
    assert isinstance(lines, list)
    first = lines[0]
    assert isinstance(first, dict)
    first["base"] = {"amount": "1000.00", "currency": code}
    return found


@pytest.mark.parametrize(
    "found",
    [
        "a draft",
        {"document": "not a uuid"},
        json_with(currency="XXX"),
        json_with(currency=7),
        json_with_line_currency("XXX"),
        json_with(document="not a uuid"),
    ],
    ids=["text", "partial", "unknown-currency", "number-currency", "unknown-line-currency", "bad-uuid"],
)
def test_what_is_not_a_draft_s_json_form_is_refused(found: JsonValue) -> None:
    with pytest.raises(FiscalDraftInvalidError, match=r"fiscal_draft must be a fiscal draft's JSON form"):
        FiscalDraft.read(found)


def test_a_json_form_that_breaks_a_rule_is_refused_by_the_rule() -> None:
    broken = through_json(DRAFT)
    assert isinstance(broken, dict)
    broken["version"] = 0
    with pytest.raises(FiscalDraftInvalidError, match=r"fiscal_draft.version must be 1 or more"):
        FiscalDraft.read(broken)


def test_a_draft_shows_no_party_and_no_line() -> None:
    shown = repr(DRAFT)
    assert shown == f"FiscalDraft(document={DOCUMENT!r}, version=1, channel='do-ecf-v1', kind='invoice')"
    party = repr(DRAFT.counterparty)
    for secret in ("131880681", "Peña", "Santiago", "compras@example.com"):
        assert secret not in party
    assert "Cemento" not in repr(DRAFT.lines[0])
