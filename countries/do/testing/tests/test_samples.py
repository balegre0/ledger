from ledger_country_do.channels.ecf_v1.domain.drafts import Buyer
from ledger_country_do_testing.samples import BUYER, ISSUER, consumer_invoice, credit_invoice


def test_samples_take_what_a_test_changes() -> None:
    other = Buyer(rnc="131880738", name="OTRA EMPRESA SRL")
    assert credit_invoice(7, buyer=other).buyer == other
    assert str(credit_invoice(7).encf) == "E310000000007"
    assert consumer_invoice(large=True).buyer == BUYER
    assert consumer_invoice(buyer=other, issuer=ISSUER).buyer == other
