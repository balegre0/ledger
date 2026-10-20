"""Numbering (spec §8.3, §8.4 ``numbering``): the port a channel allocates its fiscal numbers through.

Two shapes are documented: *authorized ranges* (the authority grants ranges of sequences per series, each with
an expiry and possibly an opaque artifact, such as Chile's signed folio authorization) and *free series* (a
series per establishment or issuing point, such as Mexico's folios). Only the Dominican pack's authorized
ranges are implemented; allocation itself stays in the pack until a second channel needs it (rule of two).
"""

import re
from abc import ABCMeta, abstractmethod
from dataclasses import dataclass
from typing import Final, Protocol, final
lazy from datetime import date

from ledger_kernel.errors import InvalidValueError
lazy from ledger_fiscal_kit.drafts import FiscalDraft
lazy from ledger_platform.data.unit_of_work import UnitOfWork

_PLAIN: Final = re.compile(r"[!-~]{1,64}")
"""1 to 64 printable ASCII characters, no space."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class FiscalNumber:
    """A number allocated to one document: as printed (``text``), its series and sequence, its expiry."""

    text: str
    """As the document carries it (an e-NCF: ``E310000000001``)."""
    series: str
    sequence: int
    """From 1."""
    expires_on: date | None
    """The last day the number may be used; ``None`` when it never expires."""

    def __post_init__(self) -> None:
        for name, value in (("text", self.text), ("series", self.series)):
            if _PLAIN.fullmatch(value) is None:
                raise InvalidValueError(
                    field=f"fiscal_number.{name}", requirement="1 to 64 printable characters without spaces"
                )
        if self.sequence < 1:
            raise InvalidValueError(field="fiscal_number.sequence", requirement="1 or more")


class NumberingPolicy(Protocol, metaclass=ABCMeta):
    """How a channel allocates numbers.

    The method is abstract so that it needs no body: a ``...`` is a statement no test could reach.
    """

    @abstractmethod
    async def allocate(self, uow: UnitOfWork, draft: FiscalDraft, *, series: str) -> FiscalNumber:
        """The next number of ``series`` for ``draft``, allocated in ``uow``.

        It is the document's only if ``uow`` commits, and no other document holds it while ``uow`` is open.
        """
