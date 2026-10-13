import pytest
from hypothesis import given
from hypothesis import strategies as st

from ledger_country_do.channels.ecf_v1.domain.encf import MAX_SEQUENCE, EcfType, Encf, InvalidEncfError


def test_an_encf_is_e_the_type_and_ten_digits() -> None:
    encf = Encf(EcfType.CREDIT_INVOICE, 1)
    assert str(encf) == "E310000000001"
    assert Encf.parse("E320000000064") == Encf(EcfType.CONSUMER_INVOICE, 64)


@pytest.mark.parametrize(
    "text",
    [
        "E3100000000001",
        "E31000000001",
        "E990000000001",
        "e310000000001",
        "E31000000000A",
        "E31\N{FULLWIDTH DIGIT ZERO}000000001",
        "",
    ],
    ids=["fourteen", "twelve", "unknown-type", "lowercase", "letter", "fullwidth-digit", "empty"],
)
def test_anything_else_is_not_an_encf(text: str) -> None:
    with pytest.raises(InvalidEncfError) as caught:
        Encf.parse(text)
    assert caught.value.code == "do_ecf_encf_invalid"


@pytest.mark.parametrize("sequence", [0, -1, MAX_SEQUENCE + 1])
def test_the_sequence_is_one_to_ten_digits(sequence: int) -> None:
    with pytest.raises(InvalidEncfError):
        Encf(EcfType.CONSUMER_INVOICE, sequence)


@given(st.sampled_from(list(EcfType)), st.integers(min_value=1, max_value=MAX_SEQUENCE))
def test_an_encf_reads_back_as_written(ecf_type: EcfType, sequence: int) -> None:
    encf = Encf(ecf_type, sequence)
    assert len(str(encf)) == 13
    assert Encf.parse(str(encf)) == encf
