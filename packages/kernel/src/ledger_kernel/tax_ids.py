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

from ledger_kernel.errors import InvalidInputError, InvalidValueError, excerpt

TaxIdScheme = NewType("TaxIdScheme", str)
"""``<cc>.<name>``: ``do.rnc``, ``do.cedula``, ``mx.rfc``, ``eu.vat``."""

MAX_TAX_ID_LENGTH: Final = 40
_SCHEME: Final = re.compile(r"[a-z]{2}\.[a-z][a-z0-9_]{0,30}", re.ASCII)
_SHOWN: Final = 3


class TaxIdCheck(StrEnum):
    """What a scheme's check says of a value."""

    VALID = "valid"
    CHECKSUM_MISMATCH = "checksum_mismatch"
    """Well formed, but the check digit is not what the algorithm predicts (refused only where enforced)."""
    INVALID_FORMAT = "invalid_format"


class InvalidTaxIdSchemeError(InvalidInputError):
    """Text that is not a scheme key."""

    code: ClassVar[str] = "invalid_tax_id_scheme"

    def __init__(self, text: str) -> None:
        super().__init__(f"not a tax id scheme (<country>.<name>): {excerpt(text)}")


class InvalidTaxIdError(InvalidInputError):
    """A value its scheme refuses; the message names the scheme and the problem, never the value."""

    code: ClassVar[str] = "invalid_tax_id"

    def __init__(self, scheme: TaxIdScheme, problem: TaxIdCheck) -> None:
        super().__init__(f"not a valid {scheme} identifier: {problem}")


def parse_scheme(text: str) -> TaxIdScheme:
    """Read a scheme key: two lower-case letters, a dot, a lower-case name."""
    if _SCHEME.fullmatch(text) is None:
        raise InvalidTaxIdSchemeError(text)
    return TaxIdScheme(text)


def _is_storable(value: object) -> bool:
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
        if type(self.scheme) is not str or _SCHEME.fullmatch(self.scheme) is None:
            raise InvalidValueError(field="tax_id.scheme", requirement="a scheme key")
        if not _is_storable(self.value):
            raise InvalidValueError(
                field="tax_id.value",
                requirement=f"1 to {MAX_TAX_ID_LENGTH} printable ASCII characters, no space",
            )

    @override
    def __repr__(self) -> str:
        value = self.value
        masked = "***" if len(value) <= _SHOWN else "*" * (len(value) - _SHOWN) + value[-_SHOWN:]
        return f"TaxId(scheme={self.scheme!r}, value={masked!r})"
