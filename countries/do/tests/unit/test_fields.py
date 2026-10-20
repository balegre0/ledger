from datetime import date
from decimal import Decimal
from typing import Final

import pytest

from ledger_country_do.channels.ecf_v1.domain.drafts import (
    IncomeType,
    ModificationCode,
    PaymentType,
    WithholdingRole,
)
from ledger_country_do.channels.ecf_v1.domain.encf import EcfType
from ledger_country_do.channels.ecf_v1.domain.fields import (
    CHANNEL_FIELDS,
    INCOME_TYPE,
    MODIFICATION_CODE,
    PAYMENT_DUE,
    PAYMENT_TYPE,
    WITHHOLDING_ROLE,
    WITHHOLDINGS,
    EcfFields,
    Withholding,
    read_fields,
)
from ledger_country_do.regime import REGIME
from ledger_country_do.regime.fields import ECF_TYPE, REPLACES_NCF
from ledger_kernel.regimes import FieldKind, Locale
lazy from ledger_fiscal_kit.drafts import FieldValue

SECRET: Final = "SECRETO-1234"


def test_the_channel_declares_its_format_fields_in_the_regime_s_namespace() -> None:
    assert [(each.name, each.kind) for each in CHANNEL_FIELDS] == [
        ("income_type", FieldKind.CODE),
        ("payment_type", FieldKind.CODE),
        ("payment_due", FieldKind.DATE),
        ("modification_code", FieldKind.CODE),
        ("withholding_role", FieldKind.CODE),
        ("withholdings", FieldKind.OBJECT),
    ]
    assert CHANNEL_FIELDS == (
        INCOME_TYPE,
        PAYMENT_TYPE,
        PAYMENT_DUE,
        MODIFICATION_CODE,
        WITHHOLDING_ROLE,
        WITHHOLDINGS,
    )
    assert all(not each.required and Locale("es") in each.labels for each in CHANNEL_FIELDS)
    assert not {each.name for each in CHANNEL_FIELDS} & {each.name for each in REGIME.fields}
    assert (ECF_TYPE.kind, REPLACES_NCF.kind) == (FieldKind.CODE, FieldKind.OBJECT)


def test_no_field_reads_as_the_defaults() -> None:
    assert read_fields({}) == (EcfFields(), ())
    assert EcfFields() == EcfFields(
        ecf_type=None,
        income=IncomeType.OPERATIONS,
        payment=PaymentType.CASH,
        payment_due=None,
        modification=None,
        withholding_role=WithholdingRole.RETENTION,
        withholdings={},
    )


def test_every_field_reads_as_its_value() -> None:
    sent: dict[str, FieldValue] = {
        "ecf_type": "44",
        "income_type": "04",
        "payment_type": "2",
        "payment_due": "2026-11-12",
        "modification_code": "3",
    }
    assert read_fields(sent) == (
        EcfFields(
            ecf_type=EcfType.SPECIAL_REGIMES,
            income=IncomeType.LEASES,
            payment=PaymentType.CREDIT,
            payment_due=date(2026, 11, 12),
            modification=ModificationCode.CORRECT_AMOUNTS,
        ),
        (),
    )


@pytest.mark.parametrize(
    ("sent", "code"),
    [
        ("1", ModificationCode.VOID),
        ("2", ModificationCode.CORRECT_TEXT),
        ("5", ModificationCode.REFERENCE_CONSUMER),
    ],
)
def test_every_modification_code_but_a_contingency_replacement_is_read(
    sent: str, code: ModificationCode
) -> None:
    assert read_fields({"modification_code": sent}) == (EcfFields(modification=code), ())


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("ecf_type", "30"),
        ("ecf_type", 30),
        ("income_type", "07"),
        ("income_type", "1"),
        ("payment_type", 7),
        ("payment_type", "4"),
        ("payment_due", "2026-02-30"),
        ("payment_due", "13/10/2026"),
        ("payment_due", None),
        ("modification_code", "0"),
        ("modification_code", True),
        ("modification_code", "4"),
        ("replaces_ncf", {"ncf": "B0100000123", "date": "2026-11-20"}),
        ("withholding_role", "3"),
        ("withholding_role", 7),
        ("withholdings", "0:900.00"),
        ("withholdings", {"a": {"isr": "1.00"}}),
        ("withholdings", {"01": {"isr": "1.00"}}),
        ("withholdings", {"0": "1.00"}),
        ("withholdings", {"0": {}}),
        ("withholdings", {"0": {"vat": "1.00"}}),
        ("withholdings", {"0": {"isr": 1}}),
        ("withholdings", {"0": {"isr": "-1.00"}}),
        ("withholdings", {"0": {"itbis": "1E3"}}),
    ],
)
def test_a_field_of_a_value_it_does_not_take_is_invalid_and_never_quoted(
    name: str, value: FieldValue
) -> None:
    fields, found = read_fields({name: value})
    assert fields == EcfFields()
    (objection,) = found
    assert (objection.code, objection.field) == ("do_ecf_field_invalid", f"fiscal.do.{name}")
    assert repr(value) not in objection.message


def test_a_key_neither_the_regime_nor_the_channel_declares_is_unknown() -> None:
    _, found = read_fields({"colour": "red", f"{SECRET} {SECRET}": "x", "ecf_type": "31"})
    assert [(each.code, each.field) for each in found] == [
        ("do_ecf_field_unknown", "fiscal.do.colour"),
        ("do_ecf_field_unknown", "fiscal.do"),
    ]
    assert all(SECRET not in each.message and "red" not in each.message for each in found)


def test_withholdings_are_read_per_line_index_in_the_document_s_currency() -> None:
    sent: dict[str, FieldValue] = {
        "withholding_role": "2",
        "withholdings": {"0": {"itbis": "900.00", "isr": "500"}, "12": {"isr": "0.505"}},
    }
    assert read_fields(sent) == (
        EcfFields(
            withholding_role=WithholdingRole.PERCEPTION,
            withholdings={
                0: Withholding(itbis=Decimal("900.00"), isr=Decimal(500)),
                12: Withholding(itbis=None, isr=Decimal("0.505")),  # its precision is the mapping's
            },
        ),
        (),
    )
