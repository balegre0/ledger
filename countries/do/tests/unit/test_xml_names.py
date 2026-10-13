import pytest

from ledger_country_do.channels.ecf_v1.adapters.xml.names import UndeclaredElementError, declared, element
from ledger_country_do.channels.ecf_v1.adapters.xsd.published import Format


def test_an_element_is_named_as_its_schema_declares_it() -> None:
    assert element(Format.ECF_31, "rncemisor") == "RNCEmisor"
    assert element(Format.ECF_31, "encf") == "eNCF"
    assert element(Format.ANECF, "rncemisor") == "RncEmisor"
    assert element(Format.RFCE, "codigoseguridadecf") == "CodigoSeguridadeCF"


def test_an_element_a_schema_does_not_declare_is_refused() -> None:
    with pytest.raises(UndeclaredElementError, match=r"ecf-43.xsd"):
        element(Format.ECF_43, "comprador")


def test_names_that_differ_only_in_case_are_left_out() -> None:
    assert "subtotalimpuestoadicional" not in declared(Format.ECF_31)
    assert all(key == key.lower() for key in declared(Format.ECF_31))
