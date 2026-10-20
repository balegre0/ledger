from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType
from typing import Final
lazy from collections.abc import Callable, Mapping

import pytest
import xmlschema

from ledger_country_do.channels.ecf_v1.adapters.xml import ecf, rfce
from ledger_country_do.channels.ecf_v1.adapters.xml.acecf import render_acecf
from ledger_country_do.channels.ecf_v1.adapters.xml.anecf import render_anecf
from ledger_country_do.channels.ecf_v1.adapters.xml.arecf import render_arecf
from ledger_country_do.channels.ecf_v1.adapters.xml.schema_set import DO_SCHEMAS
from ledger_country_do.channels.ecf_v1.adapters.xsd.published import PUBLISHED, Format
from ledger_country_do.channels.ecf_v1.domain.drafts import CATEGORY_OF, BillingIndicator, Line
from ledger_country_do.channels.ecf_v1.domain.encf import EcfType, Encf
from ledger_country_do.channels.ecf_v1.domain.exchanges import (
    CommercialApproval,
    NotReceivedReason,
    Receipt,
    VoidedRange,
)
from ledger_country_do.channels.ecf_v1.domain.notation import SANTO_DOMINGO
from ledger_country_do.channels.ecf_v1.domain.totals import compute_totals
from ledger_country_do.regime import REGIME
from ledger_country_do_testing.samples import (
    ISSUER,
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
from ledger_fiscal_kit.xml.c14n import canonical
from ledger_kernel.money import Money
from ledger_kernel.regimes import TaxCalculator, TaxCategoryKey, TaxLine
lazy from ledger_country_do.channels.ecf_v1.domain.drafts import EcfDraft

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


DRAFTS: Final[Mapping[str, Callable[[], EcfDraft]]] = MappingProxyType(
    {
        "ecf-31": credit_invoice,
        "ecf-31-zero-and-exempt": lambda: replace(credit_invoice(2), lines=ZERO_AND_EXEMPT),
        "ecf-32": lambda: consumer_invoice(2, large=True),
        "ecf-32-anonymous": consumer_invoice,
        "rfce-32": consumer_invoice,  # the summary of the anonymous sale
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
"""The draft each golden document is written from."""


VOIDED_AT: Final = datetime(2026, 10, 13, 10, 0, tzinfo=SANTO_DOMINGO)
VOIDED: Final = (
    VoidedRange(EcfType.CONSUMER_INVOICE, 1001, 1500),
    VoidedRange(EcfType.CREDIT_INVOICE, 51, 100),
    VoidedRange(EcfType.CREDIT_INVOICE, 200, 200),
)
"""The golden ANECF's ranges: two of type 31, one of type 32 (551 numbers)."""
RECEIVED_AT: Final = datetime(2026, 10, 13, 9, 31, 2, tzinfo=SANTO_DOMINGO)
APPROVED_AT: Final = datetime(2026, 10, 14, 8, 15, tzinfo=SANTO_DOMINGO)
BUYER_RNC: Final = "131098193"
"""The sample buyer's RNC (``samples.BUYER``)."""
INVOICED: Final = Encf(EcfType.CREDIT_INVOICE, 1)
"""The sample credit invoice (total 4,887.58), which the golden receipts and approvals answer."""


def _approval(*, approved: bool, reason: str | None) -> bytes:
    return render_acecf(
        CommercialApproval(
            issuer_rnc=ISSUER.rnc,
            buyer_rnc=BUYER_RNC,
            encf=INVOICED,
            issued_on=date(2026, 10, 13),
            total=Decimal("4887.58"),
            approved=approved,
            reason=reason,
            at=APPROVED_AT,
        )
    )


def _receipt(reason: NotReceivedReason | None) -> bytes:
    return render_arecf(
        Receipt(
            issuer_rnc=ISSUER.rnc,
            buyer_rnc=BUYER_RNC,
            encf=INVOICED,
            received=reason is None,
            reason=reason,
            at=RECEIVED_AT,
        )
    )


ANSWERS: Final[Mapping[str, tuple[Format, Callable[[], bytes]]]] = MappingProxyType(
    {
        "anecf": (Format.ANECF, lambda: render_anecf(issuer_rnc=ISSUER.rnc, ranges=VOIDED, at=VOIDED_AT)),
        "acecf-approved": (Format.ACECF, lambda: _approval(approved=True, reason=None)),
        "acecf-rejected": (
            Format.ACECF,
            lambda: _approval(approved=False, reason="MERCANCIA RECIBIDA INCOMPLETA"),
        ),
        "arecf-received": (Format.ARECF, lambda: _receipt(None)),
        "arecf-not-received-3": (Format.ARECF, lambda: _receipt(NotReceivedReason.DUPLICATE)),
    }
)
"""The other documents DGII defines, each golden written by its builder."""


def draft_of(name: str) -> EcfDraft:
    return DRAFTS[name]()


def written(name: str) -> bytes:
    if name in ANSWERS:
        return ANSWERS[name][1]()
    draft = draft_of(name)
    if name == "rfce-32":
        return rfce.render(draft, compute_totals(draft), security_code="dcp79q")
    return ecf.render(draft, compute_totals(draft), signed_at=SIGNED_AT)


CASES: Final = {
    name: Format.RFCE if name == "rfce-32" else ecf.FORMAT_OF[draft_of(name).ecf_type] for name in DRAFTS
} | {name: format_ for name, (format_, _) in ANSWERS.items()}


def test_every_e_cf_type_has_a_golden_document() -> None:
    assert {draft_of(name).ecf_type for name in DRAFTS} == set(EcfType)
    assert set(CASES.values()) == set(Format) - {Format.SEED}


@pytest.mark.parametrize("name", list(CASES))
def test_the_builders_write_the_golden_documents_byte_for_byte(name: str) -> None:
    golden = GOLDEN / f"{name}.xml"
    assert golden.is_file(), f"golden missing: {golden.name}"
    expected = golden.read_bytes()
    assert written(name) == expected
    assert canonical(written(name)) == canonical(expected)


@pytest.mark.parametrize("name", list(CASES))
def test_every_golden_document_is_valid_for_both_validators(name: str) -> None:
    document = (GOLDEN / f"{name}.xml").read_bytes()
    root = f"</{PUBLISHED[CASES[name]].root}>".encode()
    assert DO_SCHEMAS.violations(CASES[name], document, unsigned=True) == ()
    signed_shape = document.replace(root, SIGNATURE + root)
    xmlschema.XMLSchema10(DO_SCHEMAS.corrected(CASES[name]).decode("utf-8")).validate(
        signed_shape.decode("utf-8")
    )


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


@pytest.mark.parametrize("name", list(DRAFTS))
def test_the_kernel_totals_every_golden_document_as_the_channel_does(name: str) -> None:
    """Invoicing totals with the kernel's calculator over regime ``do``; the channel with DGII's rules.

    Both give each ITBIS rate the same base and tax, and the document the same total: else a document
    invoicing totals would be refused by the channel that verifies it. Taxes that compound (a selective
    consumption tax, ISC, charged first and ITBIS on a base that includes it) are not expressible in the
    kernel's ``TaxLine`` yet: v2-M3's, with the first ISC line; no golden holds one. A document in another
    currency is compared in that currency, where invoicing totals it; its pesos are each amount converted
    once, as invoicing converts its functional totals.
    """
    draft = draft_of(name)
    totals = compute_totals(draft)
    channel = totals.foreign or totals  # in the document's currency, as invoicing totals it
    lines = [
        TaxLine(base=Money(amount, REGIME.currency), categories=(TaxCategoryKey(CATEGORY_OF[line.billing]),))
        for line, amount in zip(draft.lines, channel.line_amounts, strict=True)
    ]
    kernel = TaxCalculator(REGIME).totals(lines, draft.issued_on)
    by_category = {total.category: total for total in kernel.categories}
    for group in channel.groups:
        found = by_category[TaxCategoryKey(CATEGORY_OF[group.indicator])]
        assert (found.base.amount, found.percent, found.tax.amount) == (
            group.base,
            group.percent,
            group.itbis,
        )
    assert (kernel.gross.amount, kernel.tax.amount) == (channel.total, channel.itbis_total or 0)
