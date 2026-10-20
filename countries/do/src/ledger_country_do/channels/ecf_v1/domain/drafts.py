"""What an e-CF says before its XML is written: issuer, buyer, lines, payment, references (FMT §A-§C).

Every type drafts (31 to 47). Every limit here is one of DGII's e-CF schemas (lengths, decimals, line counts,
which blocks a type holds) or one of DGII's rules the schemas do not say (a type 31 names its buyer; dated
ranges). ``test_xsd_pins`` holds each per-type table equal to the schemas. An error names the field and the
rule, never the value.

The line rules are also the channel's objections (``mapping``): ``line_violations`` and ``text_violation``
collect what a value breaks without raising, and a ``Line`` refuses the first.
"""

import unicodedata
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
from ledger_kernel.money import Currency
lazy from ledger_country_do.channels.ecf_v1.domain.encf import Encf

MAX_LINES: Final = 1000
"""The schemas of every type but the notes: ``Item [1..1000]`` (F7)."""
MAX_NOTE_LINES: Final = 10000
"""The schemas of types 33 and 34: ``Item [1..10000]``."""
LEGAL_NAME: Final = 150
ADDRESS: Final = 100
ITEM_NAME: Final = 80
FOREIGN_ID: Final = 20
"""``IdentificadorExtranjero``: a buyer's identifier that is no RNC or cédula."""
QUANTITY_PLACES: Final = 2
UNIT_PRICE_PLACES: Final = 4
DISCOUNT_PLACES: Final = 2
WITHHELD_PLACES: Final = 2
"""``MontoITBISRetenido`` and ``MontoISRRetenido``: amounts, two decimals."""
CREDIT_NOTE_DAYS: Final = 30
"""``IndicadorNotaCredito`` is 1 when a credit note is issued more than 30 calendar days after the e-CF it
corrects, 0 otherwise (the 34's schema glosses 0 as "<= a 30 días calendario", 1 as "> a 30 días")."""
INTEGER_DIGITS: Final = 16
"""DGII's schemas give quantities, unit prices and amounts at most 16 integer digits (``[0-9]{1,16}``)."""
AMOUNT_LIMIT: Final = Decimal(10) ** INTEGER_DIGITS
"""The least value of 17 integer digits: every quantity, unit price and amount is below it."""
EXCHANGE_RATE_LIMIT: Final = Decimal(1000)
"""``TipoCambio`` is ``[0-9]{1,3}(.[0-9]{1,4})?`` and more than zero."""
EXCHANGE_RATE_PLACES: Final = 4
PESO: Final = Currency.of("DOP")
_XML_CHARACTERS: Final = ((0x20, 0xD7FF), (0xE000, 0xFFFD), (0x10000, 0x10FFFF))
"""XML 1.0's ``Char`` production less tab, line feed and carriage return."""


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
INDICATOR_OF: Final[Mapping[str, BillingIndicator]] = MappingProxyType(
    {category: indicator for indicator, category in CATEGORY_OF.items()}
)
"""The billing indicator of each tax category DGII bills."""


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


class WithholdingRole(IntEnum):
    """FMT's ``IndicadorAgenteRetencionoPercepcion``: whether the company withholds or collects in advance."""

    RETENTION = 1
    PERCEPTION = 2


class ModificationCode(IntEnum):
    """FMT's ``CodigoModificacion``: why a note (or a replacement) refers to another document."""

    VOID = 1
    """Voids the modified e-CF."""
    CORRECT_TEXT = 2
    """Corrects the modified e-CF's text."""
    CORRECT_AMOUNTS = 3
    """Corrects the modified e-CF's amounts."""
    REPLACE_CONTINGENCY = 4
    """Replaces an NCF issued during a contingency (written once replacements are)."""
    REFERENCE_CONSUMER = 5
    """Refers to a consumer invoice (type 32)."""


UNDATED_TYPES: Final = frozenset({EcfType.CONSUMER_INVOICE, EcfType.CREDIT_NOTE})
"""DGII dates every range it authorizes except those of types 32 and 34 (v1 M4 Decision 13)."""
NOTE_TYPES: Final = frozenset({EcfType.DEBIT_NOTE, EcfType.CREDIT_NOTE})
"""The types that correct another e-CF: ``InformacionReferencia`` is required."""
RNC_TYPES: Final = frozenset({EcfType.CREDIT_INVOICE, EcfType.PURCHASES, EcfType.GOVERNMENT})
"""The types whose schema requires the buyer's RNC or cédula (``RNCComprador``)."""
NAMED_TYPES: Final = frozenset(
    {EcfType.CREDIT_INVOICE, EcfType.PURCHASES, EcfType.SPECIAL_REGIMES, EcfType.GOVERNMENT, EcfType.EXPORTS}
)
"""The types whose schema requires the buyer's name (``RazonSocialComprador``)."""
EXEMPT_ONLY_TYPES: Final = frozenset(
    {EcfType.MINOR_EXPENSES, EcfType.SPECIAL_REGIMES, EcfType.FOREIGN_PAYMENTS}
)
"""The types whose totals hold no taxed amount (no ``MontoGravadoI1`` to ``I3``)."""
_EVERY_BILLING: Final = frozenset({*TAXED, BillingIndicator.EXEMPT})
BILLINGS: Final[Mapping[EcfType, frozenset[BillingIndicator]]] = MappingProxyType(
    {
        ecf_type: frozenset({BillingIndicator.EXEMPT})
        if ecf_type in EXEMPT_ONLY_TYPES
        else frozenset({BillingIndicator.ITBIS_3})
        if ecf_type is EcfType.EXPORTS
        else _EVERY_BILLING
        for ecf_type in EcfType
    }
)
"""How each type's lines may be billed: as its totals can sum them (a 46 has ``MontoGravadoI3`` and no
``MontoExento``: its lines are at 0 %)."""


class BuyerPart(StrEnum):
    """What a ``Comprador`` may hold of a buyer."""

    RNC = "rnc"
    FOREIGN_ID = "foreign_id"
    NAME = "name"
    ADDRESS = "address"


_EVERY_PART: Final = frozenset(BuyerPart)
BUYER_PARTS: Final[Mapping[EcfType, frozenset[BuyerPart]]] = MappingProxyType(
    {
        ecf_type: frozenset()
        if ecf_type is EcfType.MINOR_EXPENSES
        else frozenset({BuyerPart.FOREIGN_ID, BuyerPart.NAME})
        if ecf_type is EcfType.FOREIGN_PAYMENTS
        else _EVERY_PART - {BuyerPart.FOREIGN_ID}
        if ecf_type in RNC_TYPES
        else _EVERY_PART
        for ecf_type in EcfType
    }
)
"""What each type's ``Comprador`` holds: a 43 has none, a 47's payee is foreign (no RNC, no address), and the
types that require an RNC take no foreign identifier."""
INCOME_TYPES: Final = frozenset(EcfType) - {
    EcfType.PURCHASES,
    EcfType.MINOR_EXPENSES,
    EcfType.FOREIGN_PAYMENTS,
}
"""The types that state their income type (``TipoIngresos``): not the purchases."""
PAYMENT_DUE_TYPES: Final = frozenset(EcfType) - {EcfType.MINOR_EXPENSES}
"""The types that may state when payment is due (``FechaLimitePago``)."""
NO_DISCOUNT_TYPES: Final = frozenset({EcfType.MINOR_EXPENSES, EcfType.FOREIGN_PAYMENTS})
"""The types whose lines hold no discount (no ``DescuentoMonto``)."""
WITHHOLDING_TYPES: Final = frozenset({EcfType.PURCHASES, EcfType.FOREIGN_PAYMENTS})
"""The types whose every line holds a ``Retencion`` block (their schemas require it): what the company
self-billing a purchase or a payment abroad withholds. The other types declare the block optional and are
never written with it: a seller does not state what its buyer withholds."""
ITBIS_WITHHOLDING_TYPES: Final = frozenset({EcfType.PURCHASES})
"""The withholding types that state ITBIS withheld (``MontoITBISRetenido``): a 47 has no ITBIS."""
ISR_REQUIRED_TYPES: Final = frozenset({EcfType.FOREIGN_PAYMENTS})
"""The types whose every line states the ISR withheld (``MontoISRRetenido``, which the 47 requires)."""
FOREIGN_CURRENCIES: Final = frozenset(
    {"BRL", "CAD", "CHF", "COP", "DKK", "EUR", "GBP", "HTG", "JPY", "MXN", "NOK", "SEK", "USD"}
)
"""``TipoMoneda``: the currencies DGII lists, in every type's ``OtraMoneda``, that are ISO 4217's. DGII's
``CHY`` (its Chinese yuan), ``SCP``, ``VEF`` and ``XDR`` are no ISO 4217 code money knows, so a document in
yuan (``CNY``) cannot be written: DGII's list names no code it could be written with."""


def max_lines(ecf_type: EcfType) -> int:
    """How many lines a document of ``ecf_type`` may hold."""
    return MAX_NOTE_LINES if ecf_type in NOTE_TYPES else MAX_LINES


class Rule(StrEnum):
    """A rule of DGII's a value breaks: once prefixed (``do_ecf_<rule>``), an objection's code."""

    TEXT_TOO_LONG = "text_too_long"
    TEXT_INVALID = "text_invalid"
    NUMBER_INVALID = "number_invalid"
    AMOUNT_TOO_LARGE = "amount_too_large"
    QUANTITY_PRECISION = "quantity_precision"
    UNIT_PRICE_PRECISION = "unit_price_precision"
    DISCOUNT_PRECISION = "discount_precision"
    DISCOUNT_TOO_LARGE = "discount_too_large"
    WITHHELD_TOO_LARGE = "withheld_too_large"


@final
@dataclass(frozen=True, slots=True)
class Violation:
    """A rule a value breaks, and what the value must be (never the value)."""

    rule: Rule
    requirement: str


class LinePart(StrEnum):
    """The parts of a line a rule speaks of."""

    NAME = "name"
    QUANTITY = "quantity"
    UNIT_PRICE = "unit_price"
    DISCOUNT = "discount"
    AMOUNT = "amount"
    """Quantity x unit price."""


_UNWRITABLE: Final = Violation(
    Rule.TEXT_INVALID,
    "holds something other than spaces, and no control character or anything XML 1.0 cannot hold",
)
_TOO_LARGE_AMOUNT: Final = Violation(
    Rule.AMOUNT_TOO_LARGE, f"(quantity x unit price) has at most {INTEGER_DIGITS} integer digits"
)
_DISCOUNT_ABOVE_GROSS: Final = Violation(Rule.DISCOUNT_TOO_LARGE, "is at most quantity x unit price")
ISR_ABOVE_AMOUNT: Final = Violation(
    Rule.WITHHELD_TOO_LARGE, "is at most the line's amount (quantity x unit price less its discount)"
)
ITBIS_ABOVE_TAX: Final = Violation(Rule.WITHHELD_TOO_LARGE, "is at most the line's ITBIS")


class DraftInvalidError(EcfInvalidError):
    """A draft breaks a rule of DGII's; ``field`` names where, the message the rule."""

    code: ClassVar[str] = "do_ecf_draft_invalid"

    def __init__(self, field_name: str, requirement: str) -> None:
        self.field: str = field_name
        super().__init__(f"{field_name} {requirement}")


class _NotBillableLineError(DraftInvalidError):
    def __init__(self) -> None:
        super().__init__("line.billing", "is taxed or exempt (non-billable lines are not written)")


class _LineCountError(DraftInvalidError):
    def __init__(self, most: int) -> None:
        super().__init__("lines", f"holds 1 to {most} lines")


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


class _UnidentifiedBuyerError(DraftInvalidError):
    def __init__(self) -> None:
        super().__init__("buyer.rnc", "is given on a type 31, 41 or 45")


class _UnnamedBuyerError(DraftInvalidError):
    def __init__(self) -> None:
        super().__init__("buyer.name", "is given on a type 31, 41, 44, 45 or 46")


class _BuyerPartError(DraftInvalidError):
    def __init__(self) -> None:
        super().__init__(
            "buyer", "holds only what the type's Comprador holds (none on 43; no RNC or address on 47)"
        )


class _PaymentDueError(DraftInvalidError):
    def __init__(self) -> None:
        super().__init__("payment_due", "is not given on a type 43")


class _ReferenceCountError(DraftInvalidError):
    def __init__(self) -> None:
        super().__init__(
            "references", "hold exactly the e-CF a type 33 or 34 corrects, and nothing otherwise"
        )


class _BillingNotAllowedError(DraftInvalidError):
    def __init__(self) -> None:
        super().__init__(
            "line.billing", "is one the type's totals sum (exempt only on 43, 44, 47; 0 % only on 46)"
        )


class _WithholdingNotAllowedError(DraftInvalidError):
    def __init__(self) -> None:
        super().__init__("line.withheld", "is given on a purchase (41) or a payment abroad (47) only")


class _ItbisWithheldNotAllowedError(DraftInvalidError):
    def __init__(self) -> None:
        super().__init__("line.itbis_withheld", "is not given on a payment abroad (47): it has no ITBIS")


class _IsrWithheldRequiredError(DraftInvalidError):
    def __init__(self) -> None:
        super().__init__("line.isr_withheld", "is given on every line of a payment abroad (47)")


class _IsrAboveAmountError(DraftInvalidError):
    def __init__(self) -> None:
        super().__init__("line.isr_withheld", ISR_ABOVE_AMOUNT.requirement)


class _DiscountNotAllowedError(DraftInvalidError):
    def __init__(self) -> None:
        super().__init__("line.discount", "is zero on a type 43 or 47")


class _CurrencyError(DraftInvalidError):
    def __init__(self) -> None:
        super().__init__("currency", "is the peso or a currency DGII lists (TipoMoneda)")


class _ExchangeRateError(DraftInvalidError):
    def __init__(self) -> None:
        super().__init__(
            "exchange_rate",
            "is given exactly for a foreign currency: more than 0, 3 integer digits and 4 decimals at most",
        )


def _xml_character(character: str) -> bool:
    code = ord(character)
    return unicodedata.category(character) != "Cc" and any(
        low <= code <= high for low, high in _XML_CHARACTERS
    )


def writable(text: str) -> bool:
    """Whether every character of ``text`` is one XML 1.0 can hold (``Char``) and none is a control character.

    Tabs and line breaks are control characters: DGII's texts are single lines.
    """
    return all(_xml_character(character) for character in text)


def text_violation(value: str, limit: int) -> Violation | None:
    """The rule ``value`` breaks as a text of DGII's of at most ``limit`` characters; ``None`` if none."""
    if len(value) > limit:
        return Violation(Rule.TEXT_TOO_LONG, f"holds at most {limit} characters")
    if not value.strip() or not writable(value):
        return _UNWRITABLE
    return None


def _places(value: Decimal) -> int:
    exponent = value.as_tuple().exponent
    return -exponent if isinstance(exponent, int) and exponent < 0 else 0


def number_violation(value: Decimal, *, places: int, precision: Rule, positive: bool) -> Violation | None:
    """The rule ``value`` breaks as a number of DGII's of at most ``places`` decimals; ``None`` if none."""
    if not value.is_finite() or value < 0 or (positive and value == 0):
        return Violation(
            Rule.NUMBER_INVALID, "is a number greater than zero" if positive else "is a number from 0"
        )
    if value >= AMOUNT_LIMIT:
        return Violation(Rule.AMOUNT_TOO_LARGE, f"has at most {INTEGER_DIGITS} integer digits")
    if _places(value) > places:
        return Violation(precision, f"has at most {places} decimals")
    return None


def line_violations(
    *, name: str, quantity: Decimal, unit_price: Decimal, discount: Decimal
) -> tuple[tuple[LinePart, Violation], ...]:
    """Every rule a line of these values breaks, by part, in the order of ``LinePart``."""
    parts = (
        (LinePart.NAME, text_violation(name, ITEM_NAME)),
        (
            LinePart.QUANTITY,
            number_violation(
                quantity, places=QUANTITY_PLACES, precision=Rule.QUANTITY_PRECISION, positive=True
            ),
        ),
        (
            LinePart.UNIT_PRICE,
            number_violation(
                unit_price, places=UNIT_PRICE_PLACES, precision=Rule.UNIT_PRICE_PRECISION, positive=False
            ),
        ),
        (
            LinePart.DISCOUNT,
            number_violation(
                discount, places=DISCOUNT_PLACES, precision=Rule.DISCOUNT_PRECISION, positive=False
            ),
        ),
    )
    found = tuple((part, violation) for part, violation in parts if violation is not None)
    if any(part is not LinePart.NAME for part, _ in found):
        return found
    gross = quantity * unit_price
    if gross >= AMOUNT_LIMIT or round_amount(gross) >= AMOUNT_LIMIT:
        return (*found, (LinePart.AMOUNT, _TOO_LARGE_AMOUNT))
    if discount > gross:
        return (*found, (LinePart.DISCOUNT, _DISCOUNT_ABOVE_GROSS))
    return found


def withheld_violation(value: Decimal) -> Violation | None:
    """The rule ``value`` breaks as an amount withheld; ``None`` if none."""
    return number_violation(value, places=WITHHELD_PLACES, precision=Rule.NUMBER_INVALID, positive=False)


def _text(field_name: str, value: str, limit: int) -> None:
    violation = text_violation(value, limit)
    if violation is not None:
        raise DraftInvalidError(field_name, violation.requirement)


def _tax_id(field_name: str, value: str) -> None:
    if scheme_of(value) is None or normalize(value) != value:
        raise DraftInvalidError(field_name, "is an RNC (9 digits) or a cédula (11 digits), digits only")


def exchange_rate_valid(rate: Decimal) -> bool:
    """Whether ``rate`` is one ``TipoCambio`` holds."""
    return rate.is_finite() and 0 < rate < EXCHANGE_RATE_LIMIT and _places(rate) <= EXCHANGE_RATE_PLACES


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Issuer:
    """The company issuing the e-CF."""

    rnc: str
    legal_name: str
    address: str

    def __post_init__(self) -> None:
        _tax_id("issuer.rnc", self.rnc)
        _text("issuer.legal_name", self.legal_name, LEGAL_NAME)
        _text("issuer.address", self.address, ADDRESS)


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Buyer:
    """The buyer (for a purchase, the supplier): an RNC or cédula, a foreign identifier, a name, an address.

    Each is optional; none of them is an anonymous consumer.
    """

    rnc: str | None = None
    foreign_id: str | None = None
    """``IdentificadorExtranjero``: an identifier of another country's."""
    name: str | None = None
    address: str | None = None

    def __post_init__(self) -> None:
        if self.rnc is not None:
            _tax_id("buyer.rnc", self.rnc)
        if self.foreign_id is not None:
            _text("buyer.foreign_id", self.foreign_id, FOREIGN_ID)
        if self.name is not None:
            _text("buyer.name", self.name, LEGAL_NAME)
        if self.address is not None:
            _text("buyer.address", self.address, ADDRESS)

    @property
    def anonymous(self) -> bool:
        """Whether the buyer is neither identified nor named."""
        return self.rnc is None and self.foreign_id is None and self.name is None

    @property
    def parts(self) -> frozenset[BuyerPart]:
        """What the buyer gives."""
        given = (
            (BuyerPart.RNC, self.rnc),
            (BuyerPart.FOREIGN_ID, self.foreign_id),
            (BuyerPart.NAME, self.name),
            (BuyerPart.ADDRESS, self.address),
        )
        return frozenset(part for part, value in given if value is not None)


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Line:
    """A detail line: quantity x unit price less its discount, billed under one indicator."""

    name: str
    quantity: Decimal
    unit_price: Decimal
    discount: Decimal = Decimal(0)
    """An amount (``DescuentoMonto``), at most quantity x unit price."""
    billing: BillingIndicator
    kind: GoodsOrService = GoodsOrService.GOODS
    itbis_withheld: Decimal | None = None
    """``MontoITBISRetenido``: at most the line's ITBIS (``totals.compute_totals`` holds it to that)."""
    isr_withheld: Decimal | None = None
    """``MontoISRRetenido``: at most the line's amount."""

    def __post_init__(self) -> None:
        found = line_violations(
            name=self.name, quantity=self.quantity, unit_price=self.unit_price, discount=self.discount
        )
        if found:
            part, violation = found[0]
            field_name = f"line.{part}"
            raise DraftInvalidError(field_name, violation.requirement)
        if self.billing is BillingIndicator.NOT_BILLABLE:
            raise _NotBillableLineError
        withheld = (("line.itbis_withheld", self.itbis_withheld), ("line.isr_withheld", self.isr_withheld))
        for field_name, amount in withheld:
            broken = None if amount is None else withheld_violation(amount)
            if broken is not None:
                raise DraftInvalidError(field_name, broken.requirement)
        if self.isr_withheld is not None and self.isr_withheld > self.amount:
            raise _IsrAboveAmountError

    @property
    def amount(self) -> Decimal:
        """Quantity x unit price less the discount, before rounding."""
        return self.quantity * self.unit_price - self.discount

    @property
    def withholds(self) -> bool:
        """Whether the line states an amount withheld."""
        return self.itbis_withheld is not None or self.isr_withheld is not None


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class EcfReference:
    """``InformacionReferencia``: the e-CF a note corrects, its date, and why it corrects it."""

    encf: Encf
    issued_on: date
    code: ModificationCode


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class EcfDraft:
    """An e-CF of any type, numbered, before its XML is written; amounts in ``currency``."""

    encf: Encf
    issued_on: date
    issuer: Issuer
    lines: tuple[Line, ...]
    buyer: Buyer = field(default_factory=Buyer)
    sequence_expires_on: date | None = None
    income: IncomeType = IncomeType.OPERATIONS
    payment: PaymentType = PaymentType.CASH
    payment_due: date | None = None
    """``FechaLimitePago``."""
    references: tuple[EcfReference, ...] = ()
    currency: Currency = PESO
    exchange_rate: Decimal | None = None
    """Pesos per unit of ``currency`` (``TipoCambio``), for a foreign currency only."""
    withholding_role: WithholdingRole = WithholdingRole.RETENTION
    """Written on the types that withhold (``WITHHOLDING_TYPES``) only."""

    def __post_init__(self) -> None:
        ecf_type = self.ecf_type
        most = max_lines(ecf_type)
        if not 1 <= len(self.lines) <= most:
            raise _LineCountError(most)
        if (ecf_type not in UNDATED_TYPES) != (self.sequence_expires_on is not None):
            raise _SequenceExpiryError
        if self.sequence_expires_on is not None and self.issued_on > self.sequence_expires_on:
            raise _SequenceExpiredError
        if ecf_type in RNC_TYPES and self.buyer.rnc is None:
            raise _UnidentifiedBuyerError
        if ecf_type in NAMED_TYPES and self.buyer.name is None:
            raise _UnnamedBuyerError
        if not self.buyer.parts <= BUYER_PARTS[ecf_type]:
            raise _BuyerPartError
        if self.payment_due is not None and ecf_type not in PAYMENT_DUE_TYPES:
            raise _PaymentDueError
        if len(self.references) != (1 if ecf_type in NOTE_TYPES else 0):
            raise _ReferenceCountError
        self._require_lines(ecf_type)
        self._require_currency()

    def _require_lines(self, ecf_type: EcfType) -> None:
        billings = BILLINGS[ecf_type]
        if any(line.billing not in billings for line in self.lines):
            raise _BillingNotAllowedError
        if ecf_type in NO_DISCOUNT_TYPES and any(line.discount != 0 for line in self.lines):
            raise _DiscountNotAllowedError
        if ecf_type not in WITHHOLDING_TYPES and any(line.withholds for line in self.lines):
            raise _WithholdingNotAllowedError
        if ecf_type in ISR_REQUIRED_TYPES and any(line.isr_withheld is None for line in self.lines):
            raise _IsrWithheldRequiredError
        if ecf_type not in ITBIS_WITHHOLDING_TYPES and any(
            line.itbis_withheld is not None for line in self.lines
        ):
            raise _ItbisWithheldNotAllowedError

    def _require_currency(self) -> None:
        rate = self.exchange_rate
        if self.currency == PESO:
            if rate is not None:
                raise _ExchangeRateError
            return
        if self.currency.code not in FOREIGN_CURRENCIES:
            raise _CurrencyError
        if rate is None or not exchange_rate_valid(rate):
            raise _ExchangeRateError

    @property
    def ecf_type(self) -> EcfType:
        """The draft's e-CF type, the e-NCF's."""
        return self.encf.ecf_type

    @property
    def credit_note_late(self) -> bool:
        """Whether the draft is a credit note issued more than ``CREDIT_NOTE_DAYS`` after what it corrects."""
        return self.ecf_type is EcfType.CREDIT_NOTE and any(
            (self.issued_on - reference.issued_on).days > CREDIT_NOTE_DAYS for reference in self.references
        )
