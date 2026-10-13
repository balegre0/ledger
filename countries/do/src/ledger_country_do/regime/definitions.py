"""The schema of a regime: provisional, local copies of the kernel v2 regime types (spec §5.1, §8.2).

The kernel v2, which will hold these types (``ledger_kernel.regimes``), is built by the restructure in
parallel with this spike; the spike keeps copies under the spec's names and fields and imports nothing of the
workspace. The integration milestone replaces this module with imports from the kernel.

Standard library only.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import final
lazy from collections.abc import Callable
lazy from datetime import date


class TaxIdCheck(StrEnum):
    """What a scheme's check says of a text (ADR 0006)."""

    VALID = "valid"
    CHECKSUM_MISMATCH = "checksum_mismatch"
    """Well formed, but not the check digit the algorithm predicts: reported; refused only where enforced."""
    INVALID_FORMAT = "invalid_format"


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Source:
    """Where a value of a regime comes from."""

    title: str
    url: str
    version: str
    retrieved_on: date


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class TaxIdSchemeDef:
    """A tax-identifier scheme (``<cc>.<name>``).

    Its length, its check, and whether its check digit is enforced.
    """

    key: str
    label: str
    digits: int
    check_digit_enforced: bool
    check: Callable[[str], TaxIdCheck]
    source: Source
