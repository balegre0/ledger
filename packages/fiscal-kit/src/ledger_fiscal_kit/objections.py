"""Why a channel would refuse a draft (spec §8.3 ``check``): a value of the kernel and the standard library.

Kept apart from ``channels`` (which names the platform's calls and assembled channels) so that a channel's
pure layers build objections without loading the platform; ``channels`` re-exports it.

``NumberUnavailableError`` is what every channel's ``issue`` raises (a subclass of its own, with its own code)
when it has no number to give a draft now that ``check`` passed (DO: a range ran out meanwhile): nothing is
kept, and ``fiscal`` routes the document again once the channel's requirements change (a range registered,
``fiscal_channel.requirements.changed``), never on a guess.
"""

import re
from dataclasses import dataclass
from typing import ClassVar, Final, final

from ledger_kernel.errors import ConflictError, InvalidValueError

_CODE: Final = re.compile(r"[a-z][a-z0-9_]{0,63}")


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Objection:
    """Why a channel would refuse a draft: a rule's code, the field it is about, a message naming the rule.

    The message names the rule, never the value: it is shown to the client and may be logged.
    """

    code: str
    field: str | None
    message: str

    def __post_init__(self) -> None:
        if _CODE.fullmatch(self.code) is None:
            raise InvalidValueError(field="objection.code", requirement="a snake_case code")


class NumberUnavailableError(ConflictError):
    """A channel has no number to give a document now (see the module): nothing of it was kept."""

    code: ClassVar[str] = "fiscal_number_unavailable"
