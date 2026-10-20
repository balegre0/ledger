from datetime import date
from typing import Final
from uuid import UUID

import pytest

from ledger_country_do.channels.ecf_v1.domain.drafts import EcfReference, ModificationCode
from ledger_country_do.channels.ecf_v1.domain.encf import EcfType, Encf
from ledger_country_do.channels.ecf_v1.domain.mapping import objections
from ledger_country_do.channels.ecf_v1.domain.references import AcceptedEcf, resolve
from ledger_country_do_testing.fiscal import fiscal_draft
from ledger_fiscal_kit.drafts import DocumentRef, FiscalReference
from ledger_kernel.regimes import DocumentKind

CORRECTED: Final = DocumentRef(UUID("0199e3a0-0000-7000-8000-0000000000f1"))
OTHER: Final = DocumentRef(UUID("0199e3a0-0000-7000-8000-0000000000f2"))
NOTE: Final = fiscal_draft(
    kind=DocumentKind.DEBIT_NOTE,
    references=(FiscalReference(document=CORRECTED, issue_date=date(2026, 10, 2)),),
    fields={"modification_code": "2"},
)


def test_a_note_s_reference_is_the_accepted_e_cf_of_the_invoice_it_corrects() -> None:
    accepted = {CORRECTED: AcceptedEcf(encf=Encf(EcfType.CONSUMER_INVOICE, 4), issued_on=date(2026, 10, 1))}
    assert resolve(NOTE, accepted, ecf_type=EcfType.DEBIT_NOTE, code=ModificationCode.CORRECT_TEXT) == (
        (
            EcfReference(
                encf=Encf(EcfType.CONSUMER_INVOICE, 4),
                issued_on=date(2026, 10, 1),
                code=ModificationCode.CORRECT_TEXT,
            ),
        ),
        (),
    )
    assert objections(NOTE, accepted=accepted) == ()


def test_an_invoice_with_no_accepted_e_cf_here_is_unresolved() -> None:
    accepted = {OTHER: AcceptedEcf(encf=Encf(EcfType.CREDIT_INVOICE, 4), issued_on=date(2026, 10, 1))}
    references, found = resolve(
        NOTE, accepted, ecf_type=EcfType.DEBIT_NOTE, code=ModificationCode.CORRECT_TEXT
    )
    assert references == ()
    assert [(each.code, each.field) for each in found] == [("do_ecf_reference_unresolved", "corrects")]
    assert [each.code for each in objections(NOTE, accepted={})] == ["do_ecf_reference_unresolved"]


def test_code_5_refers_to_a_consumer_invoice_only() -> None:
    # DGII's schemas gloss CodigoModificacion 5 as "Referencia Factura Consumo Electrónica": a type 32.
    consumer = {CORRECTED: AcceptedEcf(encf=Encf(EcfType.CONSUMER_INVOICE, 4), issued_on=date(2026, 10, 1))}
    references, _ = resolve(
        NOTE, consumer, ecf_type=EcfType.DEBIT_NOTE, code=ModificationCode.REFERENCE_CONSUMER
    )
    assert references[0].code is ModificationCode.REFERENCE_CONSUMER
    credit = {CORRECTED: AcceptedEcf(encf=Encf(EcfType.CREDIT_INVOICE, 4), issued_on=date(2026, 10, 1))}
    _, found = resolve(NOTE, credit, ecf_type=EcfType.DEBIT_NOTE, code=ModificationCode.REFERENCE_CONSUMER)
    assert [(each.code, each.field) for each in found] == [("do_ecf_reference_type_not_allowed", "corrects")]


@pytest.mark.parametrize("corrected", [EcfType.PURCHASES, EcfType.CREDIT_NOTE, EcfType.FOREIGN_PAYMENTS])
def test_a_note_corrects_a_sales_invoice_only(corrected: EcfType) -> None:
    accepted = {CORRECTED: AcceptedEcf(encf=Encf(corrected, 4), issued_on=date(2026, 10, 1))}
    references, found = resolve(
        NOTE, accepted, ecf_type=EcfType.DEBIT_NOTE, code=ModificationCode.CORRECT_TEXT
    )
    assert references == ()
    assert [(each.code, each.field) for each in found] == [("do_ecf_reference_type_not_allowed", "corrects")]
    assert "0000000004" not in found[0].message
