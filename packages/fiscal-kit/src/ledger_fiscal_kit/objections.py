"""Why a channel would refuse a draft (spec §8.3 ``check``): a value of the kernel and the standard library.

Kept apart from ``channels`` (which names the platform's calls and assembled channels) so that a channel's
pure layers build objections without loading the platform; ``channels`` re-exports it.
"""

import re
from dataclasses import dataclass
from typing import Final, final

from ledger_kernel.errors import InvalidValueError

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
