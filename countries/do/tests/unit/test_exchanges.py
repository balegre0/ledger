from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Final

import pytest

from ledger_country_do.channels.ecf_v1.domain.encf import MAX_SEQUENCE, EcfType, Encf
from ledger_country_do.channels.ecf_v1.domain.exchanges import (
    ANNULMENT_COUNT,
    ANNULMENT_RANGES,
    Annulment,
    CommercialApproval,
    ExchangeInvalidError,
    InboundEcf,
    NotReceivedReason,
    VoidedRange,
    annulments,
    is_tax_id,
)
from ledger_country_do.channels.ecf_v1.domain.notation import NaiveMomentError

CREDIT = EcfType.CREDIT_INVOICE
CONSUMER = EcfType.CONSUMER_INVOICE


@pytest.mark.parametrize(("first", "last"), [(0, 1), (2, 1), (1, MAX_SEQUENCE + 1)])
def test_a_voided_range_runs_from_first_to_last_within_dgii_s_sequences(first: int, last: int) -> None:
    with pytest.raises(ExchangeInvalidError, match="ranges run from first to last") as raised:
        VoidedRange(CREDIT, first, last)
    assert raised.value.field == "ranges"
    assert raised.value.code == "do_ecf_exchange_invalid"


def test_a_voided_range_counts_its_numbers() -> None:
    assert VoidedRange(CREDIT, 51, 100).count == 50
    assert VoidedRange(CREDIT, 7, 7).count == 1


def test_annulments_group_ranges_by_type_in_type_order_and_by_first_number() -> None:
    found = annulments(
        [VoidedRange(CONSUMER, 1001, 1500), VoidedRange(CREDIT, 200, 200), VoidedRange(CREDIT, 51, 100)]
    )
    assert found == (
        Annulment(CREDIT, (VoidedRange(CREDIT, 51, 100), VoidedRange(CREDIT, 200, 200))),
        Annulment(CONSUMER, (VoidedRange(CONSUMER, 1001, 1500),)),
    )
    assert [annulment.count for annulment in found] == [51, 500]


def test_ranges_of_two_types_may_share_numbers() -> None:
    assert len(annulments([VoidedRange(CREDIT, 1, 10), VoidedRange(CONSUMER, 1, 10)])) == 2


def test_nothing_to_void_is_refused() -> None:
    with pytest.raises(ExchangeInvalidError, match="at least one range"):
        annulments([])


@pytest.mark.parametrize(("first", "last"), [(10, 20), (1, 10), (20, 30), (12, 13)])
def test_overlapping_ranges_of_one_type_are_refused(first: int, last: int) -> None:
    with pytest.raises(ExchangeInvalidError, match="share no number"):
        annulments([VoidedRange(CREDIT, 10, 20), VoidedRange(CREDIT, first, last)])


def test_more_ranges_of_one_type_than_the_format_holds_are_refused() -> None:
    ranges = [VoidedRange(CREDIT, 2 * index + 1, 2 * index + 1) for index in range(ANNULMENT_RANGES + 1)]
    with pytest.raises(ExchangeInvalidError, match=f"at most {ANNULMENT_RANGES} ranges"):
        annulments(ranges)
    assert len(annulments(ranges[:-1])[0].ranges) == ANNULMENT_RANGES


def test_more_numbers_than_the_format_counts_are_refused() -> None:
    whole = [VoidedRange(CREDIT, 1, MAX_SEQUENCE)]
    assert annulments(whole)[0].count == ANNULMENT_COUNT
    with pytest.raises(ExchangeInvalidError, match=f"at most {ANNULMENT_COUNT} numbers"):
        annulments([*whole, VoidedRange(CONSUMER, 1, 1)])


FULLWIDTH: Final = "".join(chr(0xFF10 + int(digit)) for digit in "101850043")


def test_a_tax_id_is_nine_or_eleven_ascii_digits_as_written() -> None:
    assert is_tax_id("101850043")
    assert is_tax_id("00113918205")
    refused = ["10185004", "1018500431", "101-85004-3", " 101850043", FULLWIDTH]
    assert [text for text in refused if is_tax_id(text)] == []


def test_dgii_s_reasons_for_not_receiving() -> None:
    assert [(reason.name, reason.value) for reason in NotReceivedReason] == [
        ("SPECIFICATION", 1),
        ("SIGNATURE", 2),
        ("DUPLICATE", 3),
        ("NOT_THIS_RECEIVER", 4),
    ]


def test_what_is_read_carries_moments_with_their_offset() -> None:
    naive = datetime(2026, 10, 13, 9, 30, tzinfo=UTC).replace(tzinfo=None)
    with pytest.raises(NaiveMomentError):
        InboundEcf(
            ecf_type=CREDIT,
            encf=Encf(CREDIT, 1),
            issuer_rnc="101850043",
            issuer_name="FERRETERIA DUARTE SRL",
            buyer_rnc=None,
            issued_on=date(2026, 10, 13),
            total=Decimal("1.00"),
            itbis=None,
            signed_at=naive,
            security_code="abcdef",
        )
    with pytest.raises(NaiveMomentError):
        CommercialApproval(
            issuer_rnc="101850043",
            buyer_rnc="131098193",
            encf=Encf(CREDIT, 1),
            issued_on=date(2026, 10, 13),
            total=Decimal("1.00"),
            approved=True,
            reason=None,
            at=naive,
        )
    assert CommercialApproval(
        issuer_rnc="101850043",
        buyer_rnc="131098193",
        encf=Encf(CREDIT, 1),
        issued_on=date(2026, 10, 13),
        total=Decimal("1.00"),
        approved=True,
        reason=None,
        at=datetime(2026, 10, 13, tzinfo=UTC),
    ).approved


@pytest.mark.parametrize(
    ("issuer", "buyer", "field"), [("1018", None, "issuer_rnc"), ("101850043", "1310", "buyer_rnc")]
)
def test_a_received_e_cf_names_its_parties_by_rnc_or_cedula(
    issuer: str, buyer: str | None, field: str
) -> None:
    with pytest.raises(ExchangeInvalidError) as raised:
        InboundEcf(
            ecf_type=CREDIT,
            encf=Encf(CREDIT, 1),
            issuer_rnc=issuer,
            issuer_name="FERRETERIA DUARTE SRL",
            buyer_rnc=buyer,
            issued_on=date(2026, 10, 13),
            total=Decimal("1.00"),
            itbis=None,
            signed_at=datetime(2026, 10, 13, tzinfo=UTC),
            security_code="abcdef",
        )
    assert raised.value.field == field
