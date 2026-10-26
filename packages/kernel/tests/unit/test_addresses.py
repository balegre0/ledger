import pytest

from ledger_kernel.addresses import (
    GENERIC_ADDRESS,
    MAX_ADDRESS_LINE,
    AddressDef,
    AddressPart,
    FiscalAddress,
)
from ledger_kernel.errors import InvalidValueError

CATALOGUED = AddressDef(max_line=100, municipalities=frozenset({"010100"}), provinces=frozenset({"010000"}))


def test_an_address_is_its_line_and_the_codes_of_its_municipality_and_province() -> None:
    address = FiscalAddress(line="Av. Duarte 10, Villa Consuelo", municipality="010100", province="010000")
    assert (address.line, address.municipality, address.province) == (
        "Av. Duarte 10, Villa Consuelo",
        "010100",
        "010000",
    )
    assert FiscalAddress(line="Calle 1").municipality is None


def test_its_repr_names_its_codes_and_never_its_line() -> None:
    address = FiscalAddress(line="Av. Duarte 10", municipality="010100")
    assert repr(address) == "FiscalAddress(municipality='010100', province=None)"


@pytest.mark.parametrize("line", ["", " Calle 1", "Calle 1 ", "x" * (MAX_ADDRESS_LINE + 1)])
def test_a_line_our_code_did_not_clean_or_that_is_too_long_is_a_defect(line: str) -> None:
    with pytest.raises(InvalidValueError, match=r"address\.line"):
        FiscalAddress(line=line)


@pytest.mark.parametrize("code", ["", "01-01", "abc", "1" * 17])
def test_a_code_that_is_no_catalogue_code_s_shape_is_a_defect(code: str) -> None:
    with pytest.raises(InvalidValueError, match=r"address\.municipality"):
        FiscalAddress(line="Calle 1", municipality=code)
    with pytest.raises(InvalidValueError, match=r"address\.province"):
        FiscalAddress(line="Calle 1", province=code)


def test_a_regime_without_a_catalogue_takes_a_generic_line_and_no_code() -> None:
    assert GENERIC_ADDRESS.violation(FiscalAddress(line="x" * MAX_ADDRESS_LINE)) is None
    assert GENERIC_ADDRESS.violation(FiscalAddress(line="Calle 1", province="010000")) is AddressPart.PROVINCE


def test_a_regime_s_definition_bounds_the_line_and_names_the_codes_each_part_may_take() -> None:
    assert (
        CATALOGUED.violation(FiscalAddress(line="x" * 100, municipality="010100", province="010000")) is None
    )
    assert CATALOGUED.violation(FiscalAddress(line="x" * 101)) is AddressPart.LINE
    assert (
        CATALOGUED.violation(FiscalAddress(line="Calle 1", municipality="010000")) is AddressPart.MUNICIPALITY
    )
    assert CATALOGUED.violation(FiscalAddress(line="Calle 1", province="010100")) is AddressPart.PROVINCE


@pytest.mark.parametrize("limit", [0, MAX_ADDRESS_LINE + 1])
def test_a_definition_s_limit_is_within_the_generic_one(limit: int) -> None:
    with pytest.raises(InvalidValueError, match=r"address\.max_line"):
        AddressDef(max_line=limit)


def test_a_definition_s_codes_are_shaped_as_an_address_s() -> None:
    with pytest.raises(InvalidValueError, match=r"address\.municipalities"):
        AddressDef(municipalities=frozenset({"01-01"}))
    with pytest.raises(InvalidValueError, match=r"address\.provinces"):
        AddressDef(provinces=frozenset({""}))
