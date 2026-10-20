"""A fiscal draft: what fiscal hands a channel, refused when it could not be a fiscal document's input."""

from dataclasses import replace
from datetime import date
from decimal import Decimal
from types import MappingProxyType
from typing import Final
from uuid import UUID

import pytest

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
lazy from ledger_platform.data.codec import JsonValue

ORGANIZATION: Final = OrganizationId(UUID("0199e3a0-0000-7000-8000-000000000001"))
COMPANY: Final = CompanyId(UUID("0199e3a0-0000-7000-8000-000000000002"))
DOCUMENT: Final = DocumentRef(UUID("0199e3a0-0000-7000-8000-000000000003"))
CORRECTED: Final = DocumentRef(UUID("0199e3a0-0000-7000-8000-000000000004"))
DOP: Final = Currency.of("DOP")
USD: Final = Currency.of("USD")
ITBIS: Final = TaxCategoryKey("do.itbis.standard")
SELLER_RNC: Final = TaxId(TaxIdScheme("do.rnc"), "101850043")
BUYER_RNC: Final = TaxId(TaxIdScheme("do.rnc"), "131880681")


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


def line(number: int = 1) -> FiscalLine:
    return FiscalLine(
        number=number,
        description="Cemento gris 42.5 kg",
        quantity=Decimal(2),
        unit_price=Decimal(500),
        discount=Decimal(0),
        base=money("1000.00"),
        taxes=(
            FiscalLineTax(
                category=ITBIS,
                kind=TaxCategoryKind.STANDARD,
                percent=Decimal(18),
                since=date(2012, 11, 9),
                amount=money("180.00"),
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
    seller=FiscalParty(tax_id=SELLER_RNC, legal_name="Ferretería Duarte SRL"),
    establishment_code="001",
    time_zone="America/Santo_Domingo",
    buyer=FiscalParty(
        tax_id=BUYER_RNC,
        legal_name="Constructora Peña SRL",
        address="Calle 5 #12, Santiago",
        email="compras@example.com",
    ),
    lines=(line(),),
    totals=totals(),
    currency=DOP,
    exchange_rate=None,
    functional_totals=None,
    fields={"income_type": "01", "payment": {"kind": 1}},
    issue_date=date(2026, 10, 19),
    tax_point=date(2026, 10, 19),
)


def test_a_draft_s_scope_is_its_tenant() -> None:
    assert DRAFT.scope == Tenant(ORGANIZATION, COMPANY, Mode.TEST)


def test_a_draft_keeps_what_it_was_given() -> None:
    assert DRAFT.references == ()
    assert DRAFT.external_reference is None
    assert DRAFT.lines[0].goods is None
    assert (DRAFT.lines[0].quantity, DRAFT.lines[0].discount) == (Decimal(2), Decimal(0))
    assert (DRAFT.establishment_code, DRAFT.time_zone) == ("001", "America/Santo_Domingo")
    assert DRAFT.lines[0].taxes[0].kind is TaxCategoryKind.STANDARD
    assert DRAFT.buyer.address == "Calle 5 #12, Santiago"
    assert DRAFT.buyer.email == "compras@example.com"
    note = replace(
        DRAFT,
        kind=DocumentKind.CREDIT_NOTE,
        references=(FiscalReference(document=CORRECTED, issue_date=date(2026, 10, 1)),),
        external_reference="NC-0001",
    )
    assert note.references[0].document == CORRECTED
    assert note.references[0].issue_date == date(2026, 10, 1)


def test_a_draft_s_fields_cannot_be_changed_by_whoever_built_it() -> None:
    fields: dict[str, JsonValue] = {"income_type": "01"}
    draft = replace(DRAFT, fields=fields)
    fields["income_type"] = "02"
    assert draft.fields == {"income_type": "01"}
    assert isinstance(draft.fields, MappingProxyType)


def test_a_seller_without_a_tax_id_is_refused() -> None:
    with pytest.raises(FiscalDraftInvalidError) as caught:
        replace(DRAFT, seller=FiscalParty(tax_id=None, legal_name="Ferretería Duarte SRL"))
    assert isinstance(caught.value, InvalidValueError)
    assert (caught.value.kind, caught.value.code) == (ErrorKind.INTERNAL, "fiscal_draft_invalid")
    assert str(caught.value) == "fiscal_draft.seller.tax_id must be the seller's primary tax id"


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


def test_the_totals_are_in_the_document_s_currency() -> None:
    with pytest.raises(
        FiscalDraftInvalidError, match=r"fiscal_draft.totals must be in the document's currency"
    ):
        replace(DRAFT, totals=totals(USD))


def test_a_rate_comes_with_the_functional_totals() -> None:
    foreign = replace(
        DRAFT, currency=USD, totals=totals(USD), exchange_rate=Decimal("58.75"), functional_totals=totals()
    )
    assert foreign.exchange_rate == Decimal("58.75")
    assert foreign.functional_totals == totals()
    rule = r"fiscal_draft.exchange_rate must be given with the functional totals, and only with them"
    with pytest.raises(FiscalDraftInvalidError, match=rule):
        replace(foreign, functional_totals=None)
    with pytest.raises(FiscalDraftInvalidError, match=rule):
        replace(foreign, exchange_rate=None)


def test_a_draft_shows_no_party_and_no_line() -> None:
    shown = repr(DRAFT)
    assert shown == f"FiscalDraft(document={DOCUMENT!r}, version=1, channel='do-ecf-v1', kind='invoice')"
    party = repr(DRAFT.buyer)
    for secret in ("131880681", "Peña", "Santiago", "compras@example.com"):
        assert secret not in party
    assert "Cemento" not in repr(DRAFT.lines[0])
