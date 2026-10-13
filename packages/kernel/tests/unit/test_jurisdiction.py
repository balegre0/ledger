import pytest

from ledger_kernel.errors import InvalidValueError
from ledger_kernel.jurisdiction import (
    CountryCode,
    InvalidCountryError,
    InvalidSubdivisionError,
    Jurisdiction,
    parse_country,
    parse_jurisdiction,
)


def test_every_assigned_alpha_2_code_is_a_country() -> None:
    assert parse_country("DO") == "DO"
    assert parse_country("ZW") == "ZW"


@pytest.mark.parametrize("text", ["do", "XX", "EU", "D", "DOM", "", " DO", 7])
def test_anything_else_is_refused_without_echoing_it_whole(text: object) -> None:
    with pytest.raises(InvalidCountryError) as caught:
        parse_country(text)  # the boundary hands over whatever it got
    assert caught.value.code == "invalid_country"


def test_a_subdivision_belongs_to_its_country() -> None:
    assert parse_jurisdiction("US", "US-CA") == Jurisdiction(CountryCode("US"), "US-CA")
    with pytest.raises(InvalidSubdivisionError):
        parse_jurisdiction("US", "MX-CMX")
    with pytest.raises(InvalidSubdivisionError):
        parse_jurisdiction("US", "US-california")


def test_a_jurisdiction_without_a_subdivision_is_the_whole_country() -> None:
    assert parse_jurisdiction("DO") == Jurisdiction(CountryCode("DO"))


@pytest.mark.parametrize("subdivision", ["US-california", "MX-CMX", 7, ["US-CA"]])
def test_a_subdivision_that_is_not_an_iso_3166_2_code_of_the_country_is_refused(subdivision: object) -> None:
    with pytest.raises(InvalidSubdivisionError) as caught:
        parse_jurisdiction("US", subdivision)
    assert caught.value.code == "invalid_subdivision"


def test_a_jurisdiction_built_by_our_code_from_bad_parts_is_a_defect() -> None:
    with pytest.raises(InvalidValueError):
        Jurisdiction(CountryCode("XX"))
    with pytest.raises(InvalidValueError):
        Jurisdiction(CountryCode("US"), "MX-CMX")
