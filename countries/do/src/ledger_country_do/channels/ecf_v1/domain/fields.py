"""do-ecf-v1's format-level country fields, and the reading of a draft's ``fiscal.do`` fields (§9.8).

The channel declares, in regime ``do``'s namespace, what DGII's format asks beyond the law: the income
type (``TipoIngresos``, ``"01"`` to ``"06"``), the payment type (``TipoPago``, ``"1"`` cash, ``"2"``
credit, ``"3"`` free), the payment's due date (``FechaLimitePago``) and why a note corrects
(``CodigoModificacion``, ``"1"`` to ``"5"``; ``"4"``, a contingency replacement, is not written yet),
and, on a purchase (41) or a payment abroad (47), what the company withholds: its role
(``IndicadorAgenteRetencionoPercepcion``, ``"1"`` retention, the default, or ``"2"`` perception) and the
amounts per line (``withholdings``). Codes are text, as every ``CODE`` field.

Where a line's withholdings live (v2-M3b Task 8's decision): in the document's ``fiscal.do.withholdings``, an
object keyed by the line's index (``"0"`` for ``lines.0``, as every path a client reads), each value
``{"itbis": "<amount>", "isr": "<amount>"}`` (either optional, amounts as decimal text of at most two places,
in the document's currency like every amount of it). Not a line-level field: the kit's ``FiscalLine`` and
invoicing's lines carry no country fields, and adding them would change two contracts for one channel's
need; a document-level object needs neither and is validated here like every channel field.

``read_fields`` reads them with the regime's own (``ecf_type``, ``replaces_ncf``): each value it cannot
take is ``do_ecf_field_invalid``, each key neither declares ``do_ecf_field_unknown``; an objection names
the field and the rule, never the value.
"""

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from types import MappingProxyType
from typing import Final, final
lazy from collections.abc import Callable, Mapping

from ledger_country_do.channels.ecf_v1.domain.drafts import (
    IncomeType,
    ModificationCode,
    PaymentType,
    WithholdingRole,
    withheld_violation,
)
from ledger_country_do.channels.ecf_v1.domain.encf import EcfType
from ledger_country_do.regime import REGIME
from ledger_country_do.regime.fields import ECF_TYPE, REPLACES_NCF
from ledger_fiscal_kit.objections import Objection
from ledger_kernel.regimes import FieldDef, FieldKind, Locale
lazy from ledger_fiscal_kit.drafts import FieldValue
lazy from ledger_kernel.regimes import RegimeDef

_SPANISH: Final = Locale("es")
_ENGLISH: Final = Locale("en")
_DATE: Final = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", re.ASCII)
_KEY: Final = re.compile(r"[a-z][a-z0-9_]{0,63}", re.ASCII)
_INDEX: Final = re.compile(r"0|[1-9][0-9]{0,4}", re.ASCII)
_AMOUNT: Final = re.compile(r"[0-9]{1,16}(\.[0-9]{1,2})?", re.ASCII)

INCOME_TYPE: Final = FieldDef(
    name="income_type",
    labels={_SPANISH: "Tipo de ingresos", _ENGLISH: "Income type"},
    kind=FieldKind.CODE,
)
PAYMENT_TYPE: Final = FieldDef(
    name="payment_type",
    labels={_SPANISH: "Tipo de pago", _ENGLISH: "Payment type"},
    kind=FieldKind.CODE,
)
PAYMENT_DUE: Final = FieldDef(
    name="payment_due",
    labels={_SPANISH: "Fecha límite de pago", _ENGLISH: "Payment due date"},
    kind=FieldKind.DATE,
)
MODIFICATION_CODE: Final = FieldDef(
    name="modification_code",
    labels={_SPANISH: "Código de modificación", _ENGLISH: "Modification code"},
    kind=FieldKind.CODE,
)
WITHHOLDING_ROLE: Final = FieldDef(
    name="withholding_role",
    labels={_SPANISH: "Indicador de agente de retención o percepción", _ENGLISH: "Withholding role"},
    kind=FieldKind.CODE,
)
WITHHOLDINGS: Final = FieldDef(
    name="withholdings",
    labels={_SPANISH: "Retenciones por línea", _ENGLISH: "Withholdings per line"},
    kind=FieldKind.OBJECT,
)
CHANNEL_FIELDS: Final = (
    INCOME_TYPE,
    PAYMENT_TYPE,
    PAYMENT_DUE,
    MODIFICATION_CODE,
    WITHHOLDING_ROLE,
    WITHHOLDINGS,
)
"""The format-level fields do-ecf-v1 reads under ``fiscal.do``, beyond the regime's."""
WRITTEN_MODIFICATIONS: Final = frozenset(ModificationCode) - {ModificationCode.REPLACE_CONTINGENCY}
"""The modification codes the channel writes: a contingency replacement (4) is not written yet."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Withholding:
    """What a line withholds, in the document's currency: ITBIS, ISR, either or both."""

    itbis: Decimal | None = None
    isr: Decimal | None = None


_NO_WITHHOLDINGS: Final[Mapping[int, Withholding]] = MappingProxyType({})


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class EcfFields:
    """What a draft's ``fiscal.do`` fields say; each one not sent is its default."""

    ecf_type: EcfType | None = None
    """``None``: the document's default type (``mapping.ecf_type_of``)."""
    income: IncomeType = IncomeType.OPERATIONS
    payment: PaymentType = PaymentType.CASH
    payment_due: date | None = None
    modification: ModificationCode | None = None
    withholding_role: WithholdingRole = WithholdingRole.RETENTION
    withholdings: Mapping[int, Withholding] = field(default=_NO_WITHHOLDINGS, hash=False)
    """By line index (``lines.<index>``)."""


def _code[T](value: object, choices: Mapping[str, T]) -> T | None:
    return choices.get(value) if isinstance(value, str) else None


def _amount(value: object) -> Decimal | None:
    if not isinstance(value, str) or _AMOUNT.fullmatch(value) is None:
        return None
    amount = Decimal(value)
    return None if withheld_violation(amount) is not None else amount


def _withholding(value: FieldValue) -> Withholding | None:
    if not isinstance(value, dict) or not value or not value.keys() <= {"itbis", "isr"}:
        return None
    amounts = {key: _amount(amount) for key, amount in value.items()}
    if any(amount is None for amount in amounts.values()):
        return None
    return Withholding(itbis=amounts.get("itbis"), isr=amounts.get("isr"))


def _withholdings(value: FieldValue) -> Mapping[int, Withholding] | None:
    """``value`` as withholdings by line index; ``None`` if any part of it is not one."""
    if not isinstance(value, dict):
        return None
    read: dict[int, Withholding] = {}
    for key, each in value.items():
        withholding = _withholding(each)
        if _INDEX.fullmatch(key) is None or withholding is None:
            return None
        read[int(key)] = withholding
    return MappingProxyType(read)


def _date(value: object) -> date | None:
    if not isinstance(value, str) or _DATE.fullmatch(value) is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


_ECF_TYPES: Final = {each.value: each for each in EcfType}
_INCOMES: Final = {each.value: each for each in IncomeType}
_PAYMENTS: Final = {str(each.value): each for each in PaymentType}
_MODIFICATIONS: Final = {str(each.value): each for each in WRITTEN_MODIFICATIONS}
_ROLES: Final = {str(each.value): each for each in WithholdingRole}
_REQUIREMENTS: Final[Mapping[str, str]] = {
    ECF_TYPE.name: "is an e-CF type, 31 to 47, as text",
    INCOME_TYPE.name: "is an income type, 01 to 06",
    PAYMENT_TYPE.name: "is a payment type, 1, 2 or 3, as text",
    PAYMENT_DUE.name: "is a date (YYYY-MM-DD)",
    MODIFICATION_CODE.name: (
        "is a modification code, 1, 2, 3 or 5, as text (4, a contingency replacement, is not written yet)"
    ),
    REPLACES_NCF.name: "is not taken yet: replacing a contingency NCF is not written",
    WITHHOLDING_ROLE.name: "is a withholding role, 1 (retention) or 2 (perception), as text",
    WITHHOLDINGS.name: (
        "is an object keyed by line index (0, 1, ...), each value an object of itbis and isr, "
        "each an amount as text of at most 16 integer digits and 2 decimals"
    ),
}
"""What each field the regime or the channel declares must be."""


@final
class _Reader:
    """Reads one draft's fields, keeping an objection for each value it cannot take."""

    __slots__ = ("_fields", "_namespace", "found")

    def __init__(self, fields: Mapping[str, FieldValue], namespace: str) -> None:
        self._fields = fields
        self._namespace = namespace
        self.found: list[Objection] = []

    def objection(self, code: str, name: str | None, requirement: str) -> Objection:
        path = self._namespace if name is None else f"{self._namespace}.{name}"
        return Objection(code=f"do_ecf_{code}", field=path, message=f"{path} {requirement}")

    def read[T](self, name: str, reading: Callable[[FieldValue], T | None]) -> T | None:
        """The field ``name`` as ``reading`` reads it; ``None`` when not sent or unreadable (objected)."""
        if name not in self._fields:
            return None
        value = reading(self._fields[name])
        if value is None:
            self.found.append(self.objection("field_invalid", name, _REQUIREMENTS[name]))
        return value


def read_fields(
    fields: Mapping[str, FieldValue], regime: RegimeDef = REGIME
) -> tuple[EcfFields, tuple[Objection, ...]]:
    """What ``fields`` (a draft's ``fiscal.do``) say, and the objections to what they cannot say."""
    reader = _Reader(fields, f"fiscal.{regime.key}")
    ecf_type = reader.read(ECF_TYPE.name, lambda value: _code(value, _ECF_TYPES))
    income = reader.read(INCOME_TYPE.name, lambda value: _code(value, _INCOMES))
    payment = reader.read(PAYMENT_TYPE.name, lambda value: _code(value, _PAYMENTS))
    due = reader.read(PAYMENT_DUE.name, _date)
    modification = reader.read(MODIFICATION_CODE.name, lambda value: _code(value, _MODIFICATIONS))
    role = reader.read(WITHHOLDING_ROLE.name, lambda value: _code(value, _ROLES))
    withholdings = reader.read(WITHHOLDINGS.name, _withholdings)
    if REPLACES_NCF.name in fields:
        reader.found.append(
            reader.objection("field_invalid", REPLACES_NCF.name, _REQUIREMENTS[REPLACES_NCF.name])
        )
    unknown = (
        reader.objection(
            "field_unknown",
            key if _KEY.fullmatch(key) else None,
            "names a field regime do or channel do-ecf-v1 declares",
        )
        for key in fields
        if key not in _REQUIREMENTS
    )
    read = EcfFields(
        ecf_type=ecf_type,
        income=IncomeType.OPERATIONS if income is None else income,
        payment=PaymentType.CASH if payment is None else payment,
        payment_due=due,
        modification=modification,
        withholding_role=WithholdingRole.RETENTION if role is None else role,
        withholdings=_NO_WITHHOLDINGS if withholdings is None else withholdings,
    )
    return read, (*reader.found, *unknown)
