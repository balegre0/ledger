from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Final

import pytest
import xmlschema

from ledger_country_do.channels.ecf_v1.adapters.xml import ecf, rfce
from ledger_country_do.channels.ecf_v1.adapters.xml.schemas import corrected, violations
from ledger_country_do.channels.ecf_v1.adapters.xsd.published import Format
from ledger_country_do.channels.ecf_v1.domain.drafts import BillingIndicator, Line
from ledger_country_do.channels.ecf_v1.domain.totals import compute_totals
from ledger_country_do_testing.samples import SIGNED_AT, consumer_invoice, credit_invoice

GOLDEN: Final = Path(__file__).with_name("golden")
SIGNATURE: Final = b'<Signature xmlns="http://www.w3.org/2000/09/xmldsig#"/>'
ZERO_AND_EXEMPT: Final = (
    Line(
        name="VARILLA 3/8",
        quantity=Decimal(4),
        unit_price=Decimal("312.50"),
        billing=BillingIndicator.ITBIS_1,
    ),
    Line(
        name="CEMENTO EXPORTACION",
        quantity=Decimal(10),
        unit_price=Decimal("455.00"),
        billing=BillingIndicator.ITBIS_3,
    ),
    Line(
        name="LIBRO DE TEXTO",
        quantity=Decimal(2),
        unit_price=Decimal("120.3333"),
        billing=BillingIndicator.EXEMPT,
    ),
)
"""A type 31 mixing the standard rate, the zero rate (ITBIS 3) and an exempt line."""


def written(name: str) -> bytes:
    match name:
        case "ecf-31":
            draft = credit_invoice()
        case "ecf-31-zero-and-exempt":
            draft = replace(credit_invoice(2), lines=ZERO_AND_EXEMPT)
        case "ecf-32":
            draft = consumer_invoice(2, large=True)
        case "ecf-32-anonymous":
            draft = consumer_invoice(1)
        case _:
            draft = consumer_invoice(1)
            return rfce.render(draft, compute_totals(draft), security_code="dcp79q")
    return ecf.render(draft, compute_totals(draft), signed_at=SIGNED_AT)


CASES: Final = {
    "ecf-31": Format.ECF_31,
    "ecf-31-zero-and-exempt": Format.ECF_31,
    "ecf-32": Format.ECF_32,
    "ecf-32-anonymous": Format.ECF_32,
    "rfce-32": Format.RFCE,
}


@pytest.mark.parametrize("name", list(CASES))
def test_the_builders_write_the_golden_documents_byte_for_byte(name: str) -> None:
    assert written(name) == (GOLDEN / f"{name}.xml").read_bytes()


@pytest.mark.parametrize("name", list(CASES))
def test_every_golden_document_is_valid_for_both_validators(name: str) -> None:
    document = (GOLDEN / f"{name}.xml").read_bytes()
    root = b"</RFCE>" if CASES[name] is Format.RFCE else b"</ECF>"
    assert violations(CASES[name], document, unsigned=True) == ()
    signed_shape = document.replace(root, SIGNATURE + root)
    xmlschema.XMLSchema10(corrected(CASES[name]).decode("utf-8")).validate(signed_shape.decode("utf-8"))


def test_the_zero_rate_and_exempt_golden_s_totals() -> None:
    totals = compute_totals(replace(credit_invoice(2), lines=ZERO_AND_EXEMPT))
    assert [(group.indicator, group.base, group.percent, group.itbis) for group in totals.groups] == [
        (BillingIndicator.ITBIS_1, Decimal("1250.00"), Decimal(18), Decimal("225.00")),
        (BillingIndicator.ITBIS_3, Decimal("4550.00"), Decimal(0), Decimal("0.00")),
    ]
    assert (totals.exempt, totals.itbis_total, totals.total) == (
        Decimal("240.67"),
        Decimal("225.00"),
        Decimal("6265.67"),
    )
