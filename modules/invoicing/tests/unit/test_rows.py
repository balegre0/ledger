"""A document's row is read whole: columns that go together (a tax id's scheme and value, the seller's name
and tax id) are all there or all absent; a row holding part of them is a defect, named, never read as less."""

from typing import Final

import pytest

from ledger_invoicing.adapters.postgres.repositories import IncompleteRowError, seller_of, tax_id_of
from ledger_invoicing.domain.parties import Seller
from ledger_kernel.tax_ids import TaxId, TaxIdScheme

RNC: Final = TaxId(TaxIdScheme("do.rnc"), "101850043")


def test_a_tax_id_is_both_its_columns_or_none() -> None:
    assert tax_id_of(None, None, columns="buyer_tax") is None
    assert tax_id_of("do.rnc", "101850043", columns="buyer_tax") == RNC
    for scheme, value in (("do.rnc", None), (None, "101850043")):
        with pytest.raises(IncompleteRowError) as caught:
            tax_id_of(scheme, value, columns="buyer_tax")
        assert str(caught.value) == "invoices.buyer_tax_* are set in part: the row is not whole"
        assert caught.value.code == "invoice_row_incomplete"


def test_a_seller_is_its_name_and_its_tax_id_or_none() -> None:
    assert seller_of(None, None, None) is None
    assert seller_of("Acme SRL", "do.rnc", "101850043") == Seller(legal_name="Acme SRL", tax_id=RNC)
    for half in (("Acme SRL", None, None), (None, "do.rnc", "101850043"), ("Acme SRL", "do.rnc", None)):
        with pytest.raises(IncompleteRowError, match=r"^invoices\.seller_\* are set in part"):
            seller_of(*half)
