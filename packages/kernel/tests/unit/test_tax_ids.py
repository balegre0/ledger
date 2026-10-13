import pytest

from ledger_kernel.errors import InvalidValueError
from ledger_kernel.tax_ids import (
    InvalidTaxIdError,
    InvalidTaxIdSchemeError,
    TaxId,
    TaxIdCheck,
    TaxIdScheme,
    parse_scheme,
)


def test_a_scheme_is_a_country_and_a_name() -> None:
    assert parse_scheme("do.rnc") == "do.rnc"
    assert parse_scheme("eu.vat") == "eu.vat"


@pytest.mark.parametrize("text", ["rnc", "DO.rnc", "do.", "do.RNC", "dom.rnc", "do.rnc.x", ""])
def test_anything_else_is_not_a_scheme(text: str) -> None:
    with pytest.raises(InvalidTaxIdSchemeError):
        parse_scheme(text)


def test_repr_masks_all_but_the_last_three_characters() -> None:
    tax_id = TaxId(TaxIdScheme("do.rnc"), "101850043")
    assert repr(tax_id) == "TaxId(scheme='do.rnc', value='******043')"
    assert "101850043" not in str(tax_id)


def test_a_short_value_is_masked_whole() -> None:
    assert repr(TaxId(TaxIdScheme("xx.id"), "12")) == "TaxId(scheme='xx.id', value='***')"


@pytest.mark.parametrize("value", ["", "12 34", "x" * 41, "ñ123"])
def test_a_tax_id_built_from_a_bad_value_is_a_defect(value: str) -> None:
    with pytest.raises(InvalidValueError):
        TaxId(TaxIdScheme("do.rnc"), value)


@pytest.mark.parametrize("scheme", ["DO.rnc", "rnc", ""])
def test_a_tax_id_built_with_a_bad_scheme_is_a_defect(scheme: str) -> None:
    with pytest.raises(InvalidValueError, match=r"^tax_id\.scheme must be a scheme key$"):
        TaxId(TaxIdScheme(scheme), "101850043")


def test_an_error_names_the_scheme_and_the_problem_never_the_value() -> None:
    error = InvalidTaxIdError(TaxIdScheme("do.rnc"), TaxIdCheck.INVALID_FORMAT)
    assert str(error) == "not a valid do.rnc identifier: invalid_format"
    assert error.code == "invalid_tax_id"
