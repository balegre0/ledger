import pytest

from ledger_fiscal_kit import channels
from ledger_fiscal_kit.objections import Objection
from ledger_kernel.errors import InvalidValueError


def test_the_channels_contract_answers_the_objections_value_type() -> None:
    assert channels.Objection is Objection


def test_an_objection_s_code_is_snake_case() -> None:
    objection = Objection(
        code="do_ecf_text_too_long", field="lines.0.description", message="lines.0.description …"
    )
    assert objection.field == "lines.0.description"
    with pytest.raises(InvalidValueError, match=r"objection\.code"):
        Objection(code="Not A Code", field=None, message="x")
