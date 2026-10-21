"""How DGII writes dates, moments and numbers (FMT): ``dd-MM-yyyy``, ``dd-MM-yyyy HH:mm:ss``, two decimals.

Moments are written in the Dominican Republic's time, UTC-4 all year (the tz database's America/Santo_Domingo
keeps -04:00 since 2000-12-03). A fixed offset keeps the domain free of a time-zone database, which Windows
lacks without ``tzdata``.
"""

from datetime import date, datetime, timedelta, timezone
from typing import ClassVar, Final
lazy from decimal import Decimal

from ledger_country_do.channels.ecf_v1.domain.errors import EcfError
from ledger_country_do.regime.rounding import round_amount

SANTO_DOMINGO: Final = timezone(timedelta(hours=-4), "AST")
_DATE: Final = "%d-%m-%Y"
DGII_MOMENT: Final = "%d-%m-%Y %H:%M:%S"
"""DGII's moments (``FechaHoraFirma``, ``FechaHoraAcuseRecibo``...): ``dd-MM-yyyy HH:mm:ss``."""
DGII_YEARS: Final = range(2000, 2100)
"""The years a date read from a DGII document may have: e-CF exist since 2019, and the ACECF's date pattern
writes 20xx only. A year 1 or 9999 would also leave a moment no room for an offset (``OverflowError``)."""


class NaiveMomentError(EcfError):
    """A moment without a UTC offset: a defect of the caller, never read in the machine's time zone."""

    code: ClassVar[str] = "do_ecf_naive_moment"

    def __init__(self) -> None:
        super().__init__("a moment carries its UTC offset (a timezone-aware datetime)")


def _in_santo_domingo(value: datetime) -> datetime:
    return require_aware(value).astimezone(SANTO_DOMINGO)


def require_aware(value: datetime) -> datetime:
    """``value``, once it carries its UTC offset; ``NaiveMomentError`` otherwise."""
    if value.utcoffset() is None:
        raise NaiveMomentError
    return value


def dgii_date(value: date) -> str:
    """``dd-MM-yyyy``."""
    return value.strftime(_DATE)


def dgii_moment(value: datetime) -> str:
    """``dd-MM-yyyy HH:mm:ss`` in the Dominican Republic's time; ``NaiveMomentError`` without a UTC offset."""
    return _in_santo_domingo(value).strftime(DGII_MOMENT)


def parse_dgii_date(text: str) -> date | None:
    """``text`` read as ``dd-MM-yyyy`` (days and months of one digit too), whitespace around ignored.

    ``None`` if it writes no such date: another format, digits that are not ASCII, no real day (a 31
    February), or a year outside ``DGII_YEARS``.
    """
    stripped = text.strip()
    if not stripped.isascii():
        return None
    try:
        found = date.strptime(stripped, _DATE)
    except ValueError:
        return None
    return found if found.year in DGII_YEARS else None


def parse_dgii_moment(text: str) -> datetime | None:
    """``text`` read as ``dd-MM-yyyy HH:mm:ss`` in the Dominican Republic's time, whitespace around ignored.

    ``None`` as ``parse_dgii_date`` says, for its date or for a time that is none.
    """
    stripped = text.strip()
    if not stripped.isascii():
        return None
    try:
        found = datetime.strptime(stripped, DGII_MOMENT).replace(tzinfo=SANTO_DOMINGO)
    except ValueError:
        return None
    return found if found.year in DGII_YEARS else None


def local_date(value: datetime) -> date:
    """The Dominican Republic's date at ``value``; ``NaiveMomentError`` without a UTC offset."""
    return _in_santo_domingo(value).date()


def dgii_amount(value: Decimal) -> str:
    """An amount, rounded half up, with exactly two decimals."""
    return format(round_amount(value), "f")


def dgii_number(value: Decimal) -> str:
    """A quantity or a unit price as it is, never in exponent form."""
    return format(value, "f")


def dgii_percent(value: Decimal) -> str:
    """An ITBIS rate as DGII's schemas want it: an integer (``18``, ``16``, ``0``)."""
    return format(value.normalize(), "f")
