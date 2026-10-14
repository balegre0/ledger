"""Where a company is: a country (ISO 3166-1 alpha-2) and, where law differs inside it, a subdivision.

A subdivision is written as ISO 3166-2 writes it (``US-CA``). Only its shape is checked: the country's code, a
dash and one to three upper-case letters or digits. Whether ISO 3166-2 assigns it is not checked
here: a subdivision matters where a regime is keyed by it, and the regime pack names it.
"""

import re
from dataclasses import dataclass
from typing import ClassVar, Final, NewType, TypeIs, final

from ledger_kernel.errors import InvalidInputError, InvalidValueError, described

CountryCode = NewType("CountryCode", str)
"""An assigned ISO 3166-1 alpha-2 code, upper case (``DO``)."""

_ASSIGNED_CODES: Final = """
    AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW
    BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI
    FJ FK FM FO FR GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN
    IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME
    MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF
    PG PH PK PL PM PN PR PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV
    SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI VN VU WF WS YE
    YT ZA ZM ZW
"""
_ASSIGNED: Final[frozenset[str]] = frozenset(_ASSIGNED_CODES.split())
"""The 249 officially assigned codes (ISO 3166-1, 2026)."""
_SUBDIVISION: Final = re.compile(r"[A-Z]{2}-[A-Z0-9]{1,3}", re.ASCII)


class InvalidCountryError(InvalidInputError):
    """Text that is not an assigned ISO 3166-1 alpha-2 code."""

    code: ClassVar[str] = "invalid_country"

    def __init__(self, value: object) -> None:
        super().__init__(f"not an ISO 3166-1 alpha-2 country code: {described(value)}")


class InvalidSubdivisionError(InvalidInputError):
    """Text not shaped like an ISO 3166-2 code of the country it was given with."""

    code: ClassVar[str] = "invalid_subdivision"

    def __init__(self, value: object) -> None:
        super().__init__(f"not an ISO 3166-2 subdivision of the country: {described(value)}")


def is_country(text: object) -> TypeIs[CountryCode]:
    """Whether ``text`` is an assigned ISO 3166-1 alpha-2 code, upper case."""
    return type(text) is str and text in _ASSIGNED


def parse_country(text: object) -> CountryCode:
    """Read a country code; anything but an assigned upper-case alpha-2 code is an ``InvalidCountryError``."""
    if not is_country(text):
        raise InvalidCountryError(text)
    return CountryCode(text)


def _is_subdivision_of(country: str, subdivision: object) -> bool:
    return (
        type(subdivision) is str
        and _SUBDIVISION.fullmatch(subdivision) is not None
        and subdivision.startswith(f"{country}-")
    )


@final
@dataclass(frozen=True, slots=True)
class Jurisdiction:
    """A country and, where law differs inside it, one of its subdivisions (checked by shape only)."""

    country: CountryCode
    subdivision: str | None = None

    def __post_init__(self) -> None:
        if not is_country(self.country):
            raise InvalidValueError(
                field="jurisdiction.country", requirement="an assigned ISO 3166-1 alpha-2 code"
            )
        if self.subdivision is not None and not _is_subdivision_of(self.country, self.subdivision):
            raise InvalidValueError(
                field="jurisdiction.subdivision", requirement="shaped like an ISO 3166-2 code of its country"
            )


def parse_jurisdiction(country: object, subdivision: object = None) -> Jurisdiction:
    """Read a jurisdiction sent by a client: errors are the client's (``InvalidInputError``)."""
    code = parse_country(country)
    if subdivision is None:
        return Jurisdiction(code)
    if type(subdivision) is not str or not _is_subdivision_of(code, subdivision):
        raise InvalidSubdivisionError(subdivision)
    return Jurisdiction(code, subdivision)
