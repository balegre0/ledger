"""A company's fiscal address (§7.1): where its tax authority has it on record, as its regime describes it.

An address is its line (street, number, sector: what a person reads) and, where the regime keeps a catalogue
of its territory's subdivisions, the codes of its municipality and of its province, each optional. What a
regime takes is its ``AddressDef``: how long the line may be and which codes each part may take; a regime
that declares none takes the generic one (``GENERIC_ADDRESS``: a line of up to ``MAX_ADDRESS_LINE``
characters and no code). A ``FiscalAddress`` checks its own shape only (a defect of the code that built it
otherwise): the line cleaned (trimmed, not empty, within the generic limit), each code 1 to 16 upper-case
letters or digits; ``AddressDef.violation`` says which part a regime refuses.
"""

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, final, override

from ledger_kernel.errors import InvalidValueError

MAX_ADDRESS_LINE: Final = 300
"""Generic: a regime may keep a shorter limit (DGII's ``DireccionEmisor`` holds 100 characters)."""
_CODE: Final = re.compile(r"[A-Z0-9]{1,16}", re.ASCII)
"""A code of a subdivision's catalogue (DGII's are six digits: ``010100``)."""
_CODE_SHAPE: Final = "1 to 16 upper-case letters or digits"


def is_subdivision_code(code: str) -> bool:
    """Whether ``code`` is shaped as a code of a subdivisions' catalogue (whatever the regime's catalogue)."""
    return _CODE.fullmatch(code) is not None


def _is_code(code: str | None) -> bool:
    return code is None or is_subdivision_code(code)


class AddressPart(StrEnum):
    """A part of an address a regime may refuse."""

    LINE = "line"
    MUNICIPALITY = "municipality"
    PROVINCE = "province"


@final
@dataclass(frozen=True, slots=True, kw_only=True, repr=False)
class FiscalAddress:
    """A fiscal address (see the module); its ``repr`` names its codes, never its line."""

    line: str
    municipality: str | None = None
    """The code of its municipality in the regime's catalogue, if the regime keeps one."""
    province: str | None = None
    """The code of its province in the regime's catalogue, if the regime keeps one."""

    def __post_init__(self) -> None:
        line = self.line
        if not line or line != line.strip() or len(line) > MAX_ADDRESS_LINE:
            raise InvalidValueError(
                field="address.line", requirement=f"trimmed text of 1 to {MAX_ADDRESS_LINE} characters"
            )
        if not _is_code(self.municipality):
            raise InvalidValueError(field="address.municipality", requirement=_CODE_SHAPE)
        if not _is_code(self.province):
            raise InvalidValueError(field="address.province", requirement=_CODE_SHAPE)

    @override
    def __repr__(self) -> str:
        return f"FiscalAddress(municipality={self.municipality!r}, province={self.province!r})"


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class AddressDef:
    """What a regime takes as a company's fiscal address: its line's limit, the codes of each subdivision.

    The line is what any address holds; a municipality or a province is optional, and takes only a code of its
    catalogue (none when the regime keeps none).
    """

    max_line: int = MAX_ADDRESS_LINE
    municipalities: frozenset[str] = frozenset()
    provinces: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not 1 <= self.max_line <= MAX_ADDRESS_LINE:
            raise InvalidValueError(field="address.max_line", requirement=f"from 1 to {MAX_ADDRESS_LINE}")
        if not all(_is_code(code) for code in self.municipalities):
            raise InvalidValueError(field="address.municipalities", requirement=f"codes of {_CODE_SHAPE}")
        if not all(_is_code(code) for code in self.provinces):
            raise InvalidValueError(field="address.provinces", requirement=f"codes of {_CODE_SHAPE}")

    def violation(self, address: FiscalAddress) -> AddressPart | None:
        """The first part of ``address`` the regime refuses, line first; ``None`` when it takes it whole."""
        if len(address.line) > self.max_line:
            return AddressPart.LINE
        if address.municipality is not None and address.municipality not in self.municipalities:
            return AddressPart.MUNICIPALITY
        if address.province is not None and address.province not in self.provinces:
            return AddressPart.PROVINCE
        return None


GENERIC_ADDRESS: Final = AddressDef()
"""What a regime that declares no address takes: a line of up to ``MAX_ADDRESS_LINE`` characters, no code."""
