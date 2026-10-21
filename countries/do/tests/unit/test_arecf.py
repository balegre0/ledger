from dataclasses import replace
from datetime import UTC, datetime
from typing import Final

import pytest

from ledger_country_do.channels.ecf_v1.adapters.xml.arecf import render_arecf
from ledger_country_do.channels.ecf_v1.adapters.xml.schema_set import DO_SCHEMAS
from ledger_country_do.channels.ecf_v1.adapters.xsd.published import Format
from ledger_country_do.channels.ecf_v1.domain.encf import EcfType, Encf
from ledger_country_do.channels.ecf_v1.domain.exchanges import (
    ExchangeInvalidError,
    NotReceivedReason,
    Receipt,
)
from ledger_country_do.channels.ecf_v1.domain.notation import NaiveMomentError
from ledger_fiscal_kit.xml.hardened import texts

AT: Final = datetime(2026, 10, 13, 13, 31, 2, tzinfo=UTC)
DETAIL: Final = "DetalleAcusedeRecibo"
RECEIVED: Final = Receipt(
    issuer_rnc="101850043",
    buyer_rnc="131098193",
    encf=Encf(EcfType.CREDIT_INVOICE, 1),
    received=True,
    reason=None,
    at=AT,
)
PATHS: Final = tuple(
    f"{DETAIL}/{name}"
    for name in (
        "Version",
        "RNCEmisor",
        "RNCComprador",
        "eNCF",
        "Estado",
        "CodigoMotivoNoRecibido",
        "FechaHoraAcuseRecibo",
    )
)


def test_a_receipt_of_a_received_e_cf() -> None:
    assert RECEIVED.received
    document = render_arecf(RECEIVED)
    assert DO_SCHEMAS.violations(Format.ARECF, document, unsigned=True) == ()
    assert texts(document, *PATHS) == (
        "1.0",
        "101850043",
        "131098193",
        "E310000000001",
        "0",
        None,
        "13-10-2026 09:31:02",
    )


@pytest.mark.parametrize("reason", list(NotReceivedReason))
def test_a_receipt_of_an_e_cf_not_received_says_why(reason: NotReceivedReason) -> None:
    receipt = replace(RECEIVED, buyer_rnc="00113918205", received=False, reason=reason)
    assert not receipt.received
    document = render_arecf(receipt)
    assert DO_SCHEMAS.violations(Format.ARECF, document, unsigned=True) == ()
    found = texts(document, f"{DETAIL}/RNCComprador", f"{DETAIL}/Estado", f"{DETAIL}/CodigoMotivoNoRecibido")
    assert found == ("00113918205", "1", str(reason.value))


@pytest.mark.parametrize(
    ("issuer", "buyer", "field"),
    [("1018500", "131098193", "issuer_rnc"), ("101850043", "131-09819-3", "buyer_rnc")],
)
def test_both_parties_are_named_by_an_rnc_or_cedula(issuer: str, buyer: str, field: str) -> None:
    with pytest.raises(ExchangeInvalidError) as raised:
        replace(RECEIVED, issuer_rnc=issuer, buyer_rnc=buyer)
    assert raised.value.field == field


def test_a_receipt_is_dated_with_a_moment_that_carries_its_offset() -> None:
    with pytest.raises(NaiveMomentError):
        replace(RECEIVED, at=AT.replace(tzinfo=None))


def test_a_receipt_of_an_e_cf_received_gives_no_reason() -> None:
    with pytest.raises(ExchangeInvalidError, match="is given only when not received") as raised:
        replace(RECEIVED, reason=NotReceivedReason.DUPLICATE)
    assert raised.value.field == "reason"


def test_a_receipt_we_send_of_an_e_cf_not_received_says_why() -> None:
    not_received = replace(RECEIVED, received=False)
    assert not not_received.received
    with pytest.raises(ExchangeInvalidError, match="is given when not received") as raised:
        render_arecf(not_received)
    assert raised.value.field == "reason"
