"""Which e-NCF a document gets, from DGII-authorized ranges and released numbers (spec §8.6.3; Decision 9).

A series is a company's ranges of one e-CF type in one environment, in one mode. ``choose`` takes a **released
number** first (one DGII rejected with ``secuenciaUtilizada = false``), the lowest, once it is available:
``release_at`` its release, so that a track id of the rejected submission DGII lists late is never mistaken
for the new document's. Else the next number of the lowest range that is active, has numbers left and has not
expired on Santo Domingo's date (DGII dates ranges there). A released number is taken only from a range usable
that day: it expires with its range.

The repository applies the choice under the series' row lock, in the unit that records the document: it
deletes the released number taken, or moves its range's ``next`` on.

**Registration** (``authorized``): a range is DGII's authorization as the company enters it, numbered from its
first number on. DGII dates every type's ranges but may leave the consumer invoice's and the credit note's
(32, 34) undated (``UNDATED_TYPES``; TesteCF dates them all); no number may be shared with a range of its
series, whatever its state (a voided range's numbers stay voided).

**Accounting** (``accounting_holds``): every number of ``[first, next)`` has exactly one fate: used by a
document neither rejected nor voided, burned by a rejection, released, or voided by an accepted ANECF; and no
number of the range from ``next`` on has any.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar, Final, final
lazy from collections.abc import Sequence
lazy from collections.abc import Set as AbstractSet
lazy from datetime import date, datetime
lazy from uuid import UUID

from ledger_country_do.channels.ecf_v1.domain.encf import MAX_SEQUENCE, EcfType, Encf
from ledger_country_do.channels.ecf_v1.domain.errors import EcfConflictError, EcfInvalidError
from ledger_country_do.channels.ecf_v1.domain.notation import local_date, require_aware
from ledger_country_do.channels.ecf_v1.domain.polling import RECEIPT_TOLERANCE
from ledger_fiscal_kit.objections import NumberUnavailableError


class RangeStatus(StrEnum):
    """Whether a range may still number documents."""

    ACTIVE = "active"
    VOIDED = "voided"
    """Its unused numbers were voided before DGII: nothing more is taken from it."""


class RangeInvalidError(EcfInvalidError):
    """A range that is no DGII authorization: its values are not repeated."""

    code: ClassVar[str] = "do_ecf_range_invalid"

    def __init__(self) -> None:
        super().__init__(
            f"a range runs from first to last within 1 to {MAX_SEQUENCE}, its next from first to last + 1"
        )


class NoNumberAvailableError(EcfConflictError, NumberUnavailableError):
    """No range of the type can number a document now.

    The kit's ``NumberUnavailableError``, as fiscal reads it: it routes the document again once a range is
    registered.
    """

    code: ClassVar[str] = "do_ecf_range_missing"

    def __init__(self, ecf_type: EcfType) -> None:
        super().__init__(f"no e-NCF of type {ecf_type} is available: register a DGII-authorized range")


UNDATED_TYPES: Final = frozenset({EcfType.CONSUMER_INVOICE, EcfType.CREDIT_NOTE})
"""The types DGII may authorize without an expiry (32, 34): every other range states its last day."""


class RangeExpiryRequiredError(EcfInvalidError):
    """A range of a dated type registered without its last day."""

    code: ClassVar[str] = "do_ecf_range_expiry_required"

    def __init__(self, ecf_type: EcfType) -> None:
        super().__init__(f"a range of type {ecf_type.value} needs the day it expires, as DGII authorized it")


class RangeOverlapsError(EcfConflictError):
    """A range that shares a number with one its series holds: DGII authorizes a number once."""

    code: ClassVar[str] = "do_ecf_range_overlaps"

    def __init__(self) -> None:
        super().__init__(
            "the range shares numbers with a range already registered for its type and environment"
        )


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class RangeState:
    """A DGII-authorized range as it stands: ``next`` is the number it gives next (``last + 1``: spent)."""

    id: UUID
    ecf_type: EcfType
    first: int
    last: int
    next: int
    expires_on: date | None
    """Its last day, in Santo Domingo; ``None`` when DGII dates it not."""
    status: RangeStatus

    def __post_init__(self) -> None:
        if not (1 <= self.first <= self.last <= MAX_SEQUENCE and self.first <= self.next <= self.last + 1):
            raise RangeInvalidError

    @property
    def remaining(self) -> int:
        """How many numbers it has not given yet."""
        return self.last - self.next + 1

    def holds(self, sequence: int) -> bool:
        """Whether ``sequence`` is one of its numbers."""
        return self.first <= sequence <= self.last

    def usable_on(self, day: date) -> bool:
        """Whether it may number a document on ``day`` (Santo Domingo's date)."""
        return self.status is RangeStatus.ACTIVE and (self.expires_on is None or day <= self.expires_on)


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Released:
    """A number DGII rejected without using it, and from when it may number another document."""

    ecf_type: EcfType
    sequence: int
    available_at: datetime

    def __post_init__(self) -> None:
        require_aware(self.available_at)


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Allocation:
    """The number chosen, the range it belongs to, its expiry, and whether it was a released one."""

    encf: Encf
    range_id: UUID
    expires_on: date | None
    released: bool


def release_at(released_at: datetime) -> datetime:
    """When a number released at ``released_at`` may be taken again."""
    return released_at + RECEIPT_TOLERANCE


def choose(
    *, ecf_type: EcfType, ranges: Sequence[RangeState], released: Sequence[Released], now: datetime
) -> Allocation:
    """The number a new document of ``ecf_type`` gets at ``now`` (see the module).

    Ranges and released numbers of other types are ignored. ``NoNumberAvailableError`` when there is none;
    ``NaiveMomentError`` when ``now`` carries no UTC offset.
    """
    today = local_date(now)
    usable = sorted(
        (each for each in ranges if each.ecf_type is ecf_type and each.usable_on(today)),
        key=lambda each: each.first,
    )
    available = sorted(
        each.sequence for each in released if each.ecf_type is ecf_type and each.available_at <= now
    )
    for sequence in available:
        for range_ in usable:
            if range_.holds(sequence) and sequence < range_.next:
                return Allocation(
                    encf=Encf(ecf_type, sequence),
                    range_id=range_.id,
                    expires_on=range_.expires_on,
                    released=True,
                )
    for range_ in usable:
        if range_.remaining:
            return Allocation(
                encf=Encf(ecf_type, range_.next),
                range_id=range_.id,
                expires_on=range_.expires_on,
                released=False,
            )
    raise NoNumberAvailableError(ecf_type)


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Authorization:
    """A range as DGII authorized it: for one e-CF type, from ``first`` to ``last``, until ``expires_on``."""

    ecf_type: EcfType
    first: int
    last: int
    expires_on: date | None
    """Its last day, in Santo Domingo; ``None`` only for an undated type (``UNDATED_TYPES``)."""


def authorized(authorization: Authorization, *, range_id: UUID, series: Sequence[RangeState]) -> RangeState:
    """``authorization`` registered as ``range_id`` in a series holding ``series``: active, from its first on.

    ``RangeExpiryRequiredError`` without a day for a dated type; ``RangeInvalidError`` for numbers out of
    order or bounds; ``RangeOverlapsError`` when a number is one of a range of the same type in ``series``.
    """
    ecf_type, first, last = authorization.ecf_type, authorization.first, authorization.last
    if authorization.expires_on is None and ecf_type not in UNDATED_TYPES:
        raise RangeExpiryRequiredError(ecf_type)
    registered = RangeState(
        id=range_id,
        ecf_type=ecf_type,
        first=first,
        last=last,
        next=first,
        expires_on=authorization.expires_on,
        status=RangeStatus.ACTIVE,
    )
    if overlaps((first, last), [each for each in series if each.ecf_type is ecf_type]):
        raise RangeOverlapsError
    return registered


def overlaps(new: tuple[int, int], existing: Sequence[RangeState]) -> bool:
    """Whether ``new`` (first, last) shares a number with any of ``existing`` (the series' ranges)."""
    first, last = new
    return any(first <= range_.last and range_.first <= last for range_ in existing)


def accounting_holds(
    range_: RangeState,
    *,
    used: AbstractSet[int],
    burned: AbstractSet[int],
    released: AbstractSet[int],
    voided: AbstractSet[int],
) -> bool:
    """Whether every number of the range has exactly one fate, as the module says; others' numbers ignored.

    A number in two of the sets is caught; a number held twice within one set is not (sets hold it once):
    finding two documents with one number is the caller's check, before it builds ``used``.
    """
    fates = [
        sequence
        for numbers in (used, burned, released, voided)
        for sequence in numbers
        if range_.holds(sequence)
    ]
    distinct = set(fates)
    return len(fates) == len(distinct) == range_.next - range_.first and all(
        sequence < range_.next for sequence in distinct
    )
