"""What an enrollment requires of a company (Decision 3): a certificate, a range of 31 or 32, a receiver."""

from datetime import UTC, date, datetime
from typing import Final
from uuid import uuid4

import pytest

from ledger_country_do.channels.ecf_v1.domain.encf import EcfType
from ledger_country_do.channels.ecf_v1.domain.numbering import RangeState, RangeStatus
from ledger_country_do.channels.ecf_v1.domain.requirements import NUMBERING_TYPES, Requirement, due

NOW: Final = datetime(2026, 10, 20, 3, 30, tzinfo=UTC)
"""23:30 on 19 October in Santo Domingo: DGII dates ranges there."""
LAST_DAY: Final = date(2028, 12, 31)


def a_range(
    ecf_type: EcfType = EcfType.CREDIT_INVOICE,
    *,
    expires_on: date | None = LAST_DAY,
    spent: bool = False,
    status: RangeStatus = RangeStatus.ACTIVE,
) -> RangeState:
    return RangeState(
        id=uuid4(),
        ecf_type=ecf_type,
        first=1,
        last=10,
        next=11 if spent else 1,
        expires_on=expires_on,
        status=status,
    )


def test_a_company_with_nothing_yet_needs_a_certificate_a_range_and_the_receiver() -> None:
    assert due(certificate=False, ranges=(), now=NOW, receiver=False) == (
        Requirement.CERTIFICATE,
        Requirement.NUMBERING_RANGE,
        Requirement.RECEIVER_URL,
    )
    assert [str(requirement) for requirement in Requirement] == [
        "certificate",
        "numbering_range",
        "receiver_url",
    ]


def test_a_usable_range_of_an_invoice_type_and_a_certificate_leave_nothing_due() -> None:
    assert frozenset({EcfType.CREDIT_INVOICE, EcfType.CONSUMER_INVOICE}) == NUMBERING_TYPES
    for ecf_type in NUMBERING_TYPES:
        assert due(certificate=True, ranges=(a_range(ecf_type),), now=NOW, receiver=True) == ()


@pytest.mark.parametrize(
    "unusable",
    [
        a_range(EcfType.CREDIT_NOTE),  # numbers notes, not invoices
        a_range(spent=True),
        a_range(status=RangeStatus.VOIDED),
        a_range(expires_on=date(2026, 10, 18)),
    ],
)
def test_a_range_that_cannot_number_an_invoice_today_leaves_one_due(unusable: RangeState) -> None:
    assert due(certificate=True, ranges=(unusable,), now=NOW, receiver=True) == (Requirement.NUMBERING_RANGE,)


def test_a_range_is_usable_through_its_last_day_in_santo_domingo() -> None:
    """At 03:30 UTC on 20 October it is still 19 October in Santo Domingo: the range has not expired."""
    last_day = a_range(expires_on=date(2026, 10, 19))
    assert due(certificate=True, ranges=(last_day,), now=NOW, receiver=True) == ()
