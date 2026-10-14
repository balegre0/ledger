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
    assert parse_scheme("es.nif") == "es.nif"


@pytest.mark.parametrize(
    "text",
    ["rnc", "DO.rnc", "do.", "do.RNC", "dom.rnc", "do.rnc.x", "", "eu.vat", "xx.id", 7, None],
    ids=[
        "no-country",
        "upper-country",
        "no-name",
        "upper-name",
        "long-country",
        "three-parts",
        "empty",
        "group-of-countries",
        "unassigned-country",
        "number",
        "none",
    ],
)
def test_anything_else_is_not_a_scheme(text: object) -> None:
    with pytest.raises(InvalidTaxIdSchemeError) as caught:
        parse_scheme(text)  # the boundary hands over whatever it got
    assert caught.value.code == "invalid_tax_id_scheme"
    assert len(str(caught.value)) < 100


def test_a_value_that_is_not_text_is_named_by_its_type() -> None:
    with pytest.raises(InvalidTaxIdSchemeError, match="int: 7"):
        parse_scheme(7)


def test_repr_masks_all_but_the_last_three_characters() -> None:
    tax_id = TaxId(TaxIdScheme("do.rnc"), "101850043")
    assert repr(tax_id) == "TaxId(scheme='do.rnc', value='******043')"
    assert "101850043" not in str(tax_id)


def test_a_short_value_is_masked_whole() -> None:
    assert repr(TaxId(TaxIdScheme("mx.rfc"), "12")) == "TaxId(scheme='mx.rfc', value='***')"


@pytest.mark.parametrize("value", ["", "12 34", "x" * 41, "ñ123"])
def test_a_tax_id_built_from_a_bad_value_is_a_defect(value: str) -> None:
    with pytest.raises(InvalidValueError):
        TaxId(TaxIdScheme("do.rnc"), value)


@pytest.mark.parametrize("scheme", ["DO.rnc", "rnc", "", "eu.vat"])
def test_a_tax_id_built_with_a_bad_scheme_is_a_defect(scheme: str) -> None:
    with pytest.raises(InvalidValueError, match=r"^tax_id\.scheme must be a scheme key$"):
        TaxId(TaxIdScheme(scheme), "101850043")


def test_an_error_names_the_scheme_and_the_problem_never_the_value() -> None:
    error = InvalidTaxIdError(TaxIdScheme("do.rnc"), TaxIdCheck.INVALID_FORMAT)
    assert str(error) == "not a valid do.rnc identifier: invalid_format"
    assert error.code == "invalid_tax_id"
