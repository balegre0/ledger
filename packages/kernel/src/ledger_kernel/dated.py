"""Values that change on dates (§8.2, rule 12): a rate, a threshold, a limit, as the law set it over time."""

from dataclasses import dataclass
from enum import StrEnum
from itertools import pairwise
from typing import ClassVar, final
lazy from datetime import date

from ledger_kernel.errors import InvalidInputError, LedgerError


class TimelineProblem(StrEnum):
    """Why dated values contradict themselves."""

    EMPTY = "holds no value"
    ENDS_BEFORE_START = "has a value that ends before it starts"
    NOT_NEWEST_FIRST = "is not newest first"
    OVERLAPPING = "has overlapping values"


class InvalidTimelineError(LedgerError):
    """Dated values that contradict themselves: a defect of the regime pack, found when it is built."""

    code: ClassVar[str] = "invalid_timeline"

    def __init__(self, problem: TimelineProblem) -> None:
        super().__init__(f"a timeline {problem}")


class NotInEffectError(InvalidInputError):
    """No value is in effect on the day asked for (before the first, or after one ended)."""

    code: ClassVar[str] = "not_in_effect"

    def __init__(self, day: date) -> None:
        super().__init__(f"no value is in effect on {day.isoformat()}")


@final
@dataclass(frozen=True, slots=True)
class Since[T]:
    """``value`` in effect from ``since`` (inclusive) until ``until`` (exclusive) or the next newer value."""

    since: date
    value: T
    until: date | None = None


@final
@dataclass(frozen=True, slots=True, init=False)
class Timeline[T]:
    """Dated values, newest first; ``at(day)`` is the one in effect on ``day`` (``entry_at``, with its date).

    A value: equal, hashed and shown by its entries.
    """

    entries: tuple[Since[T], ...]
    """Every value, newest first."""

    def __init__(self, *entries: Since[T]) -> None:
        if not entries:
            raise InvalidTimelineError(TimelineProblem.EMPTY)
        for entry in entries:
            if entry.until is not None and entry.until <= entry.since:
                raise InvalidTimelineError(TimelineProblem.ENDS_BEFORE_START)
        for newer, older in pairwise(entries):
            if not older.since < newer.since:
                raise InvalidTimelineError(TimelineProblem.NOT_NEWEST_FIRST)
            if older.until is not None and older.until > newer.since:
                raise InvalidTimelineError(TimelineProblem.OVERLAPPING)
        object.__setattr__(self, "entries", entries)  # frozen: set once, here

    def at(self, day: date) -> T:
        """The value in effect on ``day``; ``NotInEffectError`` when there is none."""
        return self.entry_at(day).value

    def entry_at(self, day: date) -> Since[T]:
        """The entry in effect on ``day``, which says since when its value is; ``NotInEffectError`` if none.

        The newest that started on or before ``day``, unless it ended by then.
        """
        for entry in self.entries:
            if entry.since <= day:
                if entry.until is not None and day >= entry.until:
                    raise NotInEffectError(day)
                return entry
        raise NotInEffectError(day)
