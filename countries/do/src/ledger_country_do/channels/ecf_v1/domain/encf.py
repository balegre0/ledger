"""The e-NCF, an e-CF's number: ``E``, the two-digit e-CF type, and a sequence of ten digits (FMT, IdDoc)."""

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar, Final, final, override

from ledger_country_do.channels.ecf_v1.domain.errors import EcfInvalidError

SEQUENCE_DIGITS: Final = 10
MAX_SEQUENCE: Final = 10**SEQUENCE_DIGITS - 1
_PATTERN: Final = re.compile(r"E([0-9]{2})([0-9]{10})")


class EcfType(StrEnum):
    """The ten e-CF types (FMT; regime ``do``'s document types)."""

    CREDIT_INVOICE = "31"
    CONSUMER_INVOICE = "32"
    DEBIT_NOTE = "33"
    CREDIT_NOTE = "34"
    PURCHASES = "41"
    MINOR_EXPENSES = "43"
    SPECIAL_REGIMES = "44"
    GOVERNMENT = "45"
    EXPORTS = "46"
    FOREIGN_PAYMENTS = "47"


_CODES: Final = frozenset(ecf_type.value for ecf_type in EcfType)


class InvalidEncfError(EcfInvalidError):
    """Not an e-NCF; the text is not repeated."""

    code: ClassVar[str] = "do_ecf_encf_invalid"

    def __init__(self) -> None:
        super().__init__(
            "an e-NCF is E, a two-digit e-CF type and a sequence from 1 to 9999999999 in ten digits"
        )


@final
@dataclass(frozen=True, slots=True)
class Encf:
    """An e-NCF."""

    ecf_type: EcfType
    sequence: int

    def __post_init__(self) -> None:
        if not 1 <= self.sequence <= MAX_SEQUENCE:
            raise InvalidEncfError

    @classmethod
    def parse(cls, text: str) -> Encf:
        """The e-NCF ``text`` writes; ``InvalidEncfError`` if it writes none."""
        found = _PATTERN.fullmatch(text)
        if found is None or found.group(1) not in _CODES:
            raise InvalidEncfError
        return cls(EcfType(found.group(1)), int(found.group(2)))

    @override
    def __str__(self) -> str:
        return f"E{self.ecf_type.value}{self.sequence:0{SEQUENCE_DIGITS}d}"
