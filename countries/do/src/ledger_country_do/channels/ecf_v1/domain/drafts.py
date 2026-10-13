"""What an e-CF of the spike says before its XML is written: issuer, buyer, lines, payment (FMT §A-§C).

The spike drafts types 31 (crédito fiscal) and 32 (consumo); the other eight types are v2-M3's. Every limit
here is one of DGII's e-CF schemas (lengths, decimals, line counts) or of DGII's rules the schemas do not say
(a type 31 names its buyer; dated ranges). An error names the field and the rule, never the value.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from enum import IntEnum, StrEnum
from types import MappingProxyType
from typing import ClassVar, Final, final
lazy from collections.abc import Mapping
lazy from datetime import date

from ledger_country_do.channels.ecf_v1.domain.encf import EcfType
from ledger_country_do.channels.ecf_v1.domain.errors import EcfInvalidError
from ledger_country_do.regime.rounding import round_amount
from ledger_country_do.regime.tax_ids import normalize, scheme_of
lazy from ledger_country_do.channels.ecf_v1.domain.encf import Encf

MAX_LINES: Final = 1000
"""The e-CF 31 and 32 schemas: ``Item [1..1000]`` (F7)."""
_LEGAL_NAME: Final = 150
_ADDRESS: Final = 100
_ITEM_NAME: Final = 80
_QUANTITY_PLACES: Final = 2
_UNIT_PRICE_PLACES: Final = 4
INTEGER_DIGITS: Final = 16
"""DGII's schemas give quantities, unit prices and amounts at most 16 integer digits (``[0-9]{1,16}``)."""
AMOUNT_LIMIT: Final = Decimal(10) ** INTEGER_DIGITS
"""The least value of 17 integer digits: every quantity, unit price and amount is below it."""


class BillingIndicator(IntEnum):
    """FMT's ``IndicadorFacturacion``: how a line is taxed."""

    NOT_BILLABLE = 0
    ITBIS_1 = 1
    """ITBIS at the standard rate (18 %)."""
    ITBIS_2 = 2
    """ITBIS at the reduced rate (16 %)."""
    ITBIS_3 = 3
    """ITBIS at 0 % (exports)."""
    EXEMPT = 4


TAXED: Final = (BillingIndicator.ITBIS_1, BillingIndicator.ITBIS_2, BillingIndicator.ITBIS_3)
CATEGORY_OF: Final[Mapping[BillingIndicator, str]] = MappingProxyType(
    {
        BillingIndicator.ITBIS_1: "do.itbis.standard",
        BillingIndicator.ITBIS_2: "do.itbis.reduced",
        BillingIndicator.ITBIS_3: "do.itbis.zero",
        BillingIndicator.EXEMPT: "do.itbis.exempt",
    }
)
"""Each billing indicator's tax category in regime ``do``."""


class GoodsOrService(IntEnum):
    """FMT's ``IndicadorBienoServicio``."""

    GOODS = 1
    SERVICE = 2


class IncomeType(StrEnum):
    """FMT's ``TipoIngresos``."""

    OPERATIONS = "01"
    FINANCIAL = "02"
    EXTRAORDINARY = "03"
    LEASES = "04"
    DEPRECIABLE_ASSETS = "05"
    OTHER = "06"


class PaymentType(IntEnum):
    """FMT's ``TipoPago``: 1 contado, 2 crédito, 3 gratuito."""

    CASH = 1
    CREDIT = 2
    FREE = 3


SPIKE_TYPES: Final = frozenset({EcfType.CREDIT_INVOICE, EcfType.CONSUMER_INVOICE})
UNDATED_TYPES: Final = frozenset({EcfType.CONSUMER_INVOICE, EcfType.CREDIT_NOTE})
"""DGII dates every range it authorizes except those of types 32 and 34 (v1 M4 Decision 13)."""


class DraftInvalidError(EcfInvalidError):
    """A draft breaks a rule of DGII's; ``field`` names where, the message the rule."""

    code: ClassVar[str] = "do_ecf_draft_invalid"

    def __init__(self, field_name: str, requirement: str) -> None:
        self.field: str = field_name
        super().__init__(f"{field_name} {requirement}")


class UnsupportedTypeError(EcfInvalidError):
    """A type the spike does not draft."""

    code: ClassVar[str] = "do_ecf_type_unsupported"

    def __init__(self, ecf_type: EcfType) -> None:
        super().__init__(f"the spike drafts e-CF types 31 and 32, not e-CF type {ecf_type.value}")


class _NotBillableLineError(DraftInvalidError):
    def __init__(self) -> None:
        super().__init__("line.billing", "is taxed or exempt (the spike writes no non-billable lines)")


class _LineCountError(DraftInvalidError):
    def __init__(self) -> None:
        super().__init__("lines", f"holds 1 to {MAX_LINES} lines")


class _SequenceExpiryError(DraftInvalidError):
    def __init__(self) -> None:
        super().__init__(
            "sequence_expires_on", "is given exactly for types whose ranges DGII dates (all but 32 and 34)"
        )


class _SequenceExpiredError(DraftInvalidError):
    def __init__(self) -> None:
        super().__init__(
            "sequence_expires_on", "is on or after the issue date (a range is used up to that day)"
        )


class _LineAmountError(DraftInvalidError):
    def __init__(self) -> None:
        super().__init__(
            "line.amount", f"(quantity x unit price) has at most {INTEGER_DIGITS} integer digits"
        )


class _UnnamedCreditBuyerError(DraftInvalidError):
    def __init__(self) -> None:
        super().__init__("buyer", "names its RNC or cédula and its legal name on a type 31")


def _text(field_name: str, value: str, limit: int) -> None:
    if not value.strip() or len(value) > limit:
        raise DraftInvalidError(field_name, f"holds 1 to {limit} characters, not only spaces")


def _tax_id(field_name: str, value: str) -> None:
    if scheme_of(value) is None or normalize(value) != value:
        raise DraftInvalidError(field_name, "is an RNC (9 digits) or a cédula (11 digits), digits only")


def _places(value: Decimal) -> int:
    exponent = value.as_tuple().exponent
    return -exponent if isinstance(exponent, int) and exponent < 0 else 0


def _decimal(field_name: str, value: Decimal, *, places: int, positive: bool) -> None:
    if (
        not value.is_finite()
        or value < 0
        or (positive and value == 0)
        or value >= AMOUNT_LIMIT
        or _places(value) > places
    ):
        sign = "greater than zero" if positive else "zero or more"
        raise DraftInvalidError(
            field_name,
            f"is a number {sign} with at most {INTEGER_DIGITS} integer digits and {places} decimals",
        )


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Issuer:
    """The company issuing the e-CF."""

    rnc: str
    legal_name: str
    address: str

    def __post_init__(self) -> None:
        _tax_id("issuer.rnc", self.rnc)
        _text("issuer.legal_name", self.legal_name, _LEGAL_NAME)
        _text("issuer.address", self.address, _ADDRESS)


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Buyer:
    """The buyer: an RNC or cédula and a name, either, or neither (an anonymous consumer)."""

    rnc: str | None = None
    name: str | None = None

    def __post_init__(self) -> None:
        if self.rnc is not None:
            _tax_id("buyer.rnc", self.rnc)
        if self.name is not None:
            _text("buyer.name", self.name, _LEGAL_NAME)

    @property
    def anonymous(self) -> bool:
        """Whether the buyer is neither identified nor named."""
        return self.rnc is None and self.name is None


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Line:
    """A detail line: quantity x unit price, billed under one indicator."""

    name: str
    quantity: Decimal
    unit_price: Decimal
    billing: BillingIndicator
    kind: GoodsOrService = GoodsOrService.GOODS

    def __post_init__(self) -> None:
        _text("line.name", self.name, _ITEM_NAME)
        _decimal("line.quantity", self.quantity, places=_QUANTITY_PLACES, positive=True)
        _decimal("line.unit_price", self.unit_price, places=_UNIT_PRICE_PLACES, positive=False)
        product = self.quantity * self.unit_price
        if product >= AMOUNT_LIMIT or round_amount(product) >= AMOUNT_LIMIT:
            raise _LineAmountError
        if self.billing is BillingIndicator.NOT_BILLABLE:
            raise _NotBillableLineError


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class EcfDraft:
    """An e-CF of type 31 or 32, numbered, before its XML is written."""

    encf: Encf
    issued_on: date
    issuer: Issuer
    lines: tuple[Line, ...]
    buyer: Buyer = field(default_factory=Buyer)
    sequence_expires_on: date | None = None
    income: IncomeType = IncomeType.OPERATIONS
    payment: PaymentType = PaymentType.CASH

    def __post_init__(self) -> None:
        if self.ecf_type not in SPIKE_TYPES:
            raise UnsupportedTypeError(self.ecf_type)
        if not 1 <= len(self.lines) <= MAX_LINES:
            raise _LineCountError
        if (self.ecf_type not in UNDATED_TYPES) != (self.sequence_expires_on is not None):
            raise _SequenceExpiryError
        if self.sequence_expires_on is not None and self.issued_on > self.sequence_expires_on:
            raise _SequenceExpiredError
        if self.ecf_type is EcfType.CREDIT_INVOICE and (self.buyer.rnc is None or self.buyer.name is None):
            raise _UnnamedCreditBuyerError

    @property
    def ecf_type(self) -> EcfType:
        """The draft's e-CF type, the e-NCF's."""
        return self.encf.ecf_type
