"""The documents exchanged besides the e-CF (DT-EMI; [06] §4): range voids, approvals and receipts.

- An **ANECF** voids unused numbers of DGII-authorized ranges before DGII (``anularrango``, Task 29):
  ``annulments`` groups the ranges by type, as its format wants them (one ``Anulacion`` per type, at most
  ``ANNULMENT_RANGES`` ranges each), refusing ranges of one type that share a number, which DGII would
  count twice.
- An **ACECF** is a buyer's commercial approval or rejection of an e-CF it received (a rejection says why,
  in at most ``REJECTION_REASON`` characters), sent to the issuer and to DGII.
- An **ARECF** is a buyer's receipt of an e-CF: received, or not for one of DGII's ``NotReceivedReason``.

An approval (``CommercialApproval``) and a receipt (``Receipt``) are the values their builders write
(``adapters.xml``); an approval is also what reception reads of one of ours, as ``InboundEcf`` is what it
reads of a received e-CF. The reading is the adapters' (``adapters.xml.inbound``), never trusting what
arrives. Errors name the argument at fault and never quote its value.
"""

from dataclasses import dataclass
from enum import IntEnum
from itertools import pairwise
from typing import ClassVar, Final, final
lazy from collections.abc import Sequence
lazy from datetime import date, datetime
lazy from decimal import Decimal

from ledger_country_do.channels.ecf_v1.domain.encf import MAX_SEQUENCE, EcfType
from ledger_country_do.channels.ecf_v1.domain.errors import EcfInvalidError
from ledger_country_do.channels.ecf_v1.domain.notation import DGII_YEARS, require_aware
from ledger_country_do.regime.tax_ids import normalize, scheme_of
lazy from ledger_country_do.channels.ecf_v1.domain.encf import Encf

ANNULMENT_TYPES: Final = 10
"""An ANECF's ``Anulacion`` blocks: one per e-CF type, so every type fits in one."""
ANNULMENT_RANGES: Final = 10_000
"""The ranges one ``Anulacion`` lists (``Secuencias``)."""
ANNULMENT_COUNT: Final = 10**10 - 1
"""The most numbers an ANECF counts (``CantidadeNCFAnulados``, ten digits)."""
REJECTION_REASON: Final = 250
"""The longest reason a rejection gives (``DetalleMotivoRechazo``)."""
APPROVAL_TOTAL_DIGITS: Final = 16
"""An approval's total is an e-CF's ``MontoTotal``: at most 16 integer digits (the ACECF's own type, of 18
digits in all, would take 17 or 18 when the cents are zero)..."""
APPROVAL_TOTAL_PLACES: Final = 2
"""...and two decimals."""
_RANGES: Final = "ranges"
_TAX_ID_REQUIREMENT: Final = "is an RNC (9 digits) or a cédula (11 digits), digits only"
_ISSUED_ON: Final = "issued_on"
_ISSUED_ON_REQUIREMENT: Final = f"falls in the years {DGII_YEARS.start} to {DGII_YEARS.stop - 1}"
_TOTAL: Final = "total"
_TOTAL_REQUIREMENT: Final = (
    f"is an amount from 0 of at most {APPROVAL_TOTAL_DIGITS} integer digits "
    f"and {APPROVAL_TOTAL_PLACES} decimals"
)
REASON: Final = "reason"
"""The field an approval's reason is refused at."""
REASON_ON_APPROVAL: Final = "is given only when rejecting"
REASON_MISSING: Final = "is given when rejecting"
REASON_ON_RECEIPT: Final = "is given only when not received"
REASON_NOT_RECEIVED: Final = "is given when not received"


class ExchangeInvalidError(EcfInvalidError):
    """An ANECF, ACECF or ARECF that would not be DGII's; ``field`` names the argument, never its value."""

    code: ClassVar[str] = "do_ecf_exchange_invalid"

    def __init__(self, field_name: str, requirement: str) -> None:
        self.field: str = field_name
        super().__init__(f"{field_name} {requirement}")


def is_tax_id(text: str) -> bool:
    """Whether ``text`` is an RNC (9 digits) or a cédula (11 digits) written as ASCII digits alone."""
    return scheme_of(text) is not None and normalize(text) == text


def require_tax_id(field_name: str, text: str) -> None:
    """Refuse ``text`` unless it is an RNC or a cédula (``is_tax_id``); the error names ``field_name``."""
    if not is_tax_id(text):
        raise ExchangeInvalidError(field_name, _TAX_ID_REQUIREMENT)


@final
@dataclass(frozen=True, slots=True)
class VoidedRange:
    """Numbers of one type voided together: ``first`` to ``last``, both included."""

    ecf_type: EcfType
    first: int
    last: int

    def __post_init__(self) -> None:
        if not 1 <= self.first <= self.last <= MAX_SEQUENCE:
            raise ExchangeInvalidError(_RANGES, f"run from first to last within 1 to {MAX_SEQUENCE}")

    @property
    def count(self) -> int:
        """How many numbers it voids."""
        return self.last - self.first + 1


@final
@dataclass(frozen=True, slots=True)
class Annulment:
    """One type's voided ranges, by their first number: an ANECF's ``Anulacion``."""

    ecf_type: EcfType
    ranges: tuple[VoidedRange, ...]

    @property
    def count(self) -> int:
        """How many numbers it voids."""
        return sum(each.count for each in self.ranges)


def annulments(ranges: Sequence[VoidedRange]) -> tuple[Annulment, ...]:
    """``ranges`` grouped by type in DGII's order of types, each type's by first number (see the module).

    ``ExchangeInvalidError`` (``ranges``) for no range, ranges of one type that share a number, more ranges of
    one type than an ``Anulacion`` lists, or more numbers than an ANECF counts.
    """
    if not ranges:
        raise ExchangeInvalidError(_RANGES, "hold at least one range")
    grouped: list[Annulment] = []
    for ecf_type in EcfType:
        own = tuple(
            sorted((each for each in ranges if each.ecf_type is ecf_type), key=lambda each: each.first)
        )
        if not own:
            continue
        if any(earlier.last >= later.first for earlier, later in pairwise(own)):
            raise ExchangeInvalidError(_RANGES, "of one type share no number")
        if len(own) > ANNULMENT_RANGES:
            raise ExchangeInvalidError(_RANGES, f"hold at most {ANNULMENT_RANGES} ranges of one type")
        grouped.append(Annulment(ecf_type, own))
    if sum(each.count for each in grouped) > ANNULMENT_COUNT:
        raise ExchangeInvalidError(_RANGES, f"void at most {ANNULMENT_COUNT} numbers in all")
    return tuple(grouped)


class NotReceivedReason(IntEnum):
    """Why a buyer did not receive an e-CF (an ARECF's ``CodigoMotivoNoRecibido``)."""

    SPECIFICATION = 1
    """The document breaks DGII's specification: its schema, or it cannot be read."""
    SIGNATURE = 2
    """Its signature does not hold, or its certificate is not trusted."""
    DUPLICATE = 3
    """The same issuer sent the same e-NCF before, with other bytes."""
    NOT_THIS_RECEIVER = 4
    """Its buyer's RNC is not this receiver's."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class InboundEcf:
    """What reception reads of a received e-CF, its schema checked (``adapters.xml.inbound``).

    Its signature is checked apart (Task 22): reading it trusts none of it. ``buyer_rnc`` and ``itbis`` are
    ``None`` when the document has none (a 43 names no buyer; an exempt document states no ITBIS).
    ``ExchangeInvalidError`` for a party that is no RNC or cédula; ``NaiveMomentError`` for a moment without
    its UTC offset.
    """

    ecf_type: EcfType
    encf: Encf
    issuer_rnc: str
    issuer_name: str
    buyer_rnc: str | None
    issued_on: date
    total: Decimal
    """``MontoTotal``, in pesos."""
    itbis: Decimal | None
    """``TotalITBIS``, in pesos."""
    signed_at: datetime
    """``FechaHoraFirma``."""
    security_code: str
    """The first six characters of its signature value, as its printed form shows it."""

    def __post_init__(self) -> None:
        require_tax_id("issuer_rnc", self.issuer_rnc)
        if self.buyer_rnc is not None:
            require_tax_id("buyer_rnc", self.buyer_rnc)
        require_aware(self.signed_at)


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class InboundNames:
    """What a receipt of a received document names when the document cannot be read whole.

    Read leniently (``adapters.xml.inbound.name_inbound``): reception's receipt of a document refused for its
    specification names its issuer and e-NCF.
    """

    issuer_rnc: str
    buyer_rnc: str | None
    """``None`` when the document names no buyer (a 43): the receipt names the receiver itself."""
    encf: Encf


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class CommercialApproval:
    """A buyer's approval or rejection of an e-CF (an ACECF's fields): one we send, or one we receive.

    ``ExchangeInvalidError`` for a party that is no RNC or cédula, a total an e-CF cannot have (below 0, -0.00
    included, or of more than 16 integer digits or two decimals), a date an ACECF cannot write (a year
    outside ``DGII_YEARS``), or a reason on an approval; ``NaiveMomentError`` for a moment without its UTC
    offset. What only the approvals we send must do (a rejection says why, in a text DGII's can hold) is the
    builder's (``adapters.xml.acecf``): one received is read as its sender wrote it.
    """

    issuer_rnc: str
    """The e-CF's issuer."""
    buyer_rnc: str
    """Who approves or rejects it."""
    encf: Encf
    issued_on: date
    """The e-CF's date, as the buyer states it."""
    total: Decimal
    """The e-CF's total, as the buyer states it."""
    approved: bool
    reason: str | None
    """Why it was rejected, when the buyer says (never on an approval)."""
    at: datetime
    """``FechaHoraAprobacionComercial``."""

    def __post_init__(self) -> None:
        require_tax_id("issuer_rnc", self.issuer_rnc)
        require_tax_id("buyer_rnc", self.buyer_rnc)
        exponent = self.total.as_tuple().exponent
        if not (
            self.total.is_finite()
            and not self.total.is_signed()
            and isinstance(exponent, int)
            and exponent >= -APPROVAL_TOTAL_PLACES
            and self.total < 10**APPROVAL_TOTAL_DIGITS
        ):
            raise ExchangeInvalidError(_TOTAL, _TOTAL_REQUIREMENT)
        if self.issued_on.year not in DGII_YEARS:
            raise ExchangeInvalidError(_ISSUED_ON, _ISSUED_ON_REQUIREMENT)
        if self.approved and self.reason is not None:
            raise ExchangeInvalidError(REASON, REASON_ON_APPROVAL)
        require_aware(self.at)


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Receipt:
    """A buyer's receipt of an e-CF (an ARECF's fields): received, or not, for ``reason`` when it says.

    ``ExchangeInvalidError`` for a party that is no RNC or cédula, or a reason given with a receipt of an e-CF
    received; ``NaiveMomentError`` for a moment without its UTC offset. A receipt of an e-CF not received
    that gives no reason is DGII's (its schema leaves the reason optional) and is read so; one this channel
    sends always says why (``adapters.xml.arecf``).
    """

    issuer_rnc: str
    """The e-CF's issuer."""
    buyer_rnc: str
    """The receiver answering."""
    encf: Encf
    received: bool
    reason: NotReceivedReason | None
    """Why it was not received."""
    at: datetime
    """``FechaHoraAcuseRecibo``."""

    def __post_init__(self) -> None:
        require_tax_id("issuer_rnc", self.issuer_rnc)
        require_tax_id("buyer_rnc", self.buyer_rnc)
        if self.received and self.reason is not None:
            raise ExchangeInvalidError(REASON, REASON_ON_RECEIPT)
        require_aware(self.at)
