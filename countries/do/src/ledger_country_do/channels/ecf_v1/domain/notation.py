"""How DGII writes dates, moments and numbers (FMT): ``dd-MM-yyyy``, ``dd-MM-yyyy HH:mm:ss``, two decimals.

Moments are written in the Dominican Republic's time, UTC-4 all year (the tz database's America/Santo_Domingo
keeps -04:00 since 2000-12-03). A fixed offset keeps the domain free of a time-zone database, which Windows
lacks without ``tzdata``.
"""

from datetime import timedelta, timezone
from typing import Final
lazy from datetime import date, datetime
lazy from decimal import Decimal

from ledger_country_do.regime.rounding import round_amount

SANTO_DOMINGO: Final = timezone(timedelta(hours=-4), "AST")
_DATE: Final = "%d-%m-%Y"
_MOMENT: Final = "%d-%m-%Y %H:%M:%S"


def dgii_date(value: date) -> str:
    """``dd-MM-yyyy``."""
    return value.strftime(_DATE)


def dgii_moment(value: datetime) -> str:
    """``dd-MM-yyyy HH:mm:ss`` in the Dominican Republic's time (``value`` is timezone-aware)."""
    return value.astimezone(SANTO_DOMINGO).strftime(_MOMENT)


def local_date(value: datetime) -> date:
    """The Dominican Republic's date at ``value``."""
    return value.astimezone(SANTO_DOMINGO).date()


def dgii_amount(value: Decimal) -> str:
    """An amount, rounded half up, with exactly two decimals."""
    return format(round_amount(value), "f")


def dgii_number(value: Decimal) -> str:
    """A quantity or a unit price as it is, never in exponent form."""
    return format(value, "f")


def dgii_percent(value: Decimal) -> str:
    """An ITBIS rate as DGII's schemas want it: an integer (``18``, ``16``, ``0``)."""
    return format(value.normalize(), "f")
