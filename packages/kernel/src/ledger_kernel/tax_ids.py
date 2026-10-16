"""Qualified tax identifiers (§7.3, ADR 0006): a scheme ``<cc>.<name>`` and a normalized value.

Which schemes exist, how a value is normalized and whether its check digit is enforced is regime data
(``ledger_kernel.regimes.TaxIdSchemeDef``); this module holds the value type. A tax id is personal data:
``repr`` shows the scheme and the last three characters, an error the scheme and the problem, never the
value.
"""

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar, Final, NewType, final, override

from ledger_kernel.errors import InvalidInputError, InvalidValueError, described, excerpt
from ledger_kernel.jurisdiction import is_country

TaxIdScheme = NewType("TaxIdScheme", str)
"""``<cc>.<name>``: ``do.rnc``, ``do.cedula``, ``mx.rfc``, ``es.nif``.

``<cc>`` is one assigned ISO 3166-1 country, never a group of them: a VAT id is issued by one member state, so
the EU's are per country (``es.nif``, ``fr.tva``), not one ``eu.vat`` (ADR 0006).
"""

MAX_TAX_ID_LENGTH: Final = 40
_SCHEME: Final = re.compile(r"[a-z]{2}\.[a-z][a-z0-9_]{0,30}", re.ASCII)
_SHOWN: Final = 3


def masked(value: str) -> str:
    """``value`` with all but its last three characters hidden: the form a tax id takes in logs and ``repr``.

    A value of three characters or fewer is hidden whole, so a short id gives away nothing.
    """
    return "***" if len(value) <= _SHOWN else "*" * (len(value) - _SHOWN) + value[-_SHOWN:]


class TaxIdCheck(StrEnum):
    """What a scheme's check says of a value."""

    VALID = "valid"
    CHECKSUM_MISMATCH = "checksum_mismatch"
    """Well formed, but the check digit is not what the algorithm predicts (refused only where enforced)."""
    INVALID_FORMAT = "invalid_format"


class InvalidTaxIdSchemeError(InvalidInputError):
    """Not a scheme key: not text, not ``<cc>.<name>``, or ``<cc>`` not an assigned country's code."""

    code: ClassVar[str] = "invalid_tax_id_scheme"

    def __init__(self, value: object) -> None:
        shown = excerpt(value) if isinstance(value, str) else described(value)
        super().__init__(f"not a tax id scheme (<country>.<name>): {shown}")


class InvalidTaxIdError(InvalidInputError):
    """A value its scheme refuses; the message names the scheme and the problem, never the value."""

    code: ClassVar[str] = "invalid_tax_id"

    def __init__(self, scheme: TaxIdScheme, problem: TaxIdCheck) -> None:
        super().__init__(f"not a valid {scheme} identifier: {problem}")


def _is_scheme(text: object) -> bool:
    return type(text) is str and _SCHEME.fullmatch(text) is not None and is_country(text[:2].upper())


def parse_scheme(text: object) -> TaxIdScheme:
    """Read a scheme key: an assigned country's code in lower case, a dot, a lower-case name."""
    if type(text) is not str or not _is_scheme(text):
        raise InvalidTaxIdSchemeError(text)
    return TaxIdScheme(text)


def is_storable(value: object) -> bool:
    """Whether ``value`` can be a normalized tax id: 1 to 40 printable ASCII characters, no space."""
    return (
        type(value) is str
        and 1 <= len(value) <= MAX_TAX_ID_LENGTH
        and value.isascii()
        and value.isprintable()
        and " " not in value
    )


@final
@dataclass(frozen=True, slots=True, repr=False)
class TaxId:
    """A tax id as its scheme normalized it. Built through ``TaxIdSchemes.parse`` from a client's input."""

    scheme: TaxIdScheme
    value: str

    def __post_init__(self) -> None:
        if not _is_scheme(self.scheme):
            raise InvalidValueError(field="tax_id.scheme", requirement="a scheme key")
        if not is_storable(self.value):
            raise InvalidValueError(
                field="tax_id.value",
                requirement=f"1 to {MAX_TAX_ID_LENGTH} printable ASCII characters, no space",
            )

    @override
    def __repr__(self) -> str:
        return f"TaxId(scheme={self.scheme!r}, value={masked(self.value)!r})"
