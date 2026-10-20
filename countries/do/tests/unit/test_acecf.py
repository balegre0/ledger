from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Final

import pytest

from ledger_country_do.channels.ecf_v1.adapters.xml.acecf import render_acecf
from ledger_country_do.channels.ecf_v1.adapters.xml.schema_set import DO_SCHEMAS
from ledger_country_do.channels.ecf_v1.adapters.xsd.published import Format
from ledger_country_do.channels.ecf_v1.domain.encf import EcfType, Encf
from ledger_country_do.channels.ecf_v1.domain.exchanges import (
    REJECTION_REASON,
    CommercialApproval,
    ExchangeInvalidError,
)
from ledger_country_do.channels.ecf_v1.domain.notation import NaiveMomentError
from ledger_fiscal_kit.xml.hardened import texts

D = Decimal
AT: Final = datetime(2026, 10, 14, 12, 15, tzinfo=UTC)
DETAIL: Final = "DetalleAprobacionComercial"
APPROVAL: Final = CommercialApproval(
    issuer_rnc="101850043",
    buyer_rnc="131098193",
    encf=Encf(EcfType.CREDIT_INVOICE, 1),
    issued_on=date(2026, 10, 13),
    total=D("4887.58"),
    approved=True,
    reason=None,
    at=AT,
)


def _rejection(reason: str | None) -> CommercialApproval:
    return replace(APPROVAL, approved=False, reason=reason)


def test_an_approval_names_the_e_cf_its_total_and_the_moment() -> None:
    document = render_acecf(APPROVAL)
    assert DO_SCHEMAS.violations(Format.ACECF, document, unsigned=True) == ()
    assert texts(
        document,
        f"{DETAIL}/Version",
        f"{DETAIL}/RNCEmisor",
        f"{DETAIL}/eNCF",
        f"{DETAIL}/FechaEmision",
        f"{DETAIL}/MontoTotal",
        f"{DETAIL}/RNCComprador",
        f"{DETAIL}/Estado",
        f"{DETAIL}/DetalleMotivoRechazo",
        f"{DETAIL}/FechaHoraAprobacionComercial",
    ) == (
        "1.0",
        "101850043",
        "E310000000001",
        "13-10-2026",
        "4887.58",
        "131098193",
        "1",
        None,
        "14-10-2026 08:15:00",
    )


def test_a_rejection_says_why() -> None:
    document = render_acecf(_rejection("MERCANCIA INCOMPLETA"))
    assert DO_SCHEMAS.violations(Format.ACECF, document, unsigned=True) == ()
    assert texts(document, f"{DETAIL}/Estado", f"{DETAIL}/DetalleMotivoRechazo") == (
        "2",
        "MERCANCIA INCOMPLETA",
    )


def test_a_rejection_s_reason_may_be_as_long_as_dgii_allows() -> None:
    reason = "X" * REJECTION_REASON
    assert texts(render_acecf(_rejection(reason)), f"{DETAIL}/DetalleMotivoRechazo") == (reason,)


def test_an_approval_gives_no_reason() -> None:
    with pytest.raises(ExchangeInvalidError, match="is given only when rejecting") as raised:
        replace(APPROVAL, reason="FALTA")
    assert raised.value.field == "reason"


@pytest.mark.parametrize(
    ("reason", "requirement"),
    [
        (None, "is given when rejecting"),
        ("X" * (REJECTION_REASON + 1), f"holds at most {REJECTION_REASON} characters"),
        ("   ", "holds"),
        ("LINEA 1\nLINEA 2", "holds"),
    ],
)
def test_a_rejection_we_send_says_why_in_a_text_dgii_can_hold(reason: str | None, requirement: str) -> None:
    with pytest.raises(ExchangeInvalidError, match=requirement) as raised:
        render_acecf(_rejection(reason))
    assert raised.value.field == "reason"


@pytest.mark.parametrize("field", ["issuer_rnc", "buyer_rnc"])
def test_both_parties_are_named_by_an_rnc_or_cedula(field: str) -> None:
    with pytest.raises(ExchangeInvalidError) as raised:
        replace(APPROVAL, issuer_rnc="13109819") if field == "issuer_rnc" else replace(
            APPROVAL, buyer_rnc="13109819"
        )
    assert raised.value.field == field
    assert "13109819" not in str(raised.value)


@pytest.mark.parametrize(
    "total",
    [D("-0.01"), D("-0.00"), D("1.005"), D("1E+16"), D("NaN"), D("Infinity"), D("9999999999999999.995")],
)
def test_the_total_is_one_an_acecf_holds_as_it_is(total: Decimal) -> None:
    with pytest.raises(ExchangeInvalidError) as raised:
        replace(APPROVAL, total=total)
    assert raised.value.field == "total"


def test_the_largest_total_dgii_writes() -> None:
    document = render_acecf(replace(APPROVAL, total=D("9999999999999999.99")))
    assert texts(document, f"{DETAIL}/MontoTotal") == ("9999999999999999.99",)
    assert texts(render_acecf(replace(APPROVAL, total=D(0))), f"{DETAIL}/MontoTotal") == ("0.00",)
    assert texts(render_acecf(replace(APPROVAL, total=D("1E+2"))), f"{DETAIL}/MontoTotal") == ("100.00",)


def test_an_approval_is_dated_with_a_moment_that_carries_its_offset() -> None:
    with pytest.raises(NaiveMomentError):
        replace(APPROVAL, at=AT.replace(tzinfo=None))


@pytest.mark.parametrize("issued_on", [date(1999, 12, 31), date(2100, 1, 1)])
def test_the_e_cf_s_date_is_one_an_acecf_writes(issued_on: date) -> None:
    with pytest.raises(ExchangeInvalidError) as raised:
        replace(APPROVAL, issued_on=issued_on)
    assert raised.value.field == "issued_on"
