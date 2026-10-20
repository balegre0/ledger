"""Why something a document is issued through would refuse it (§8.5 step 2): an ``IssuePolicy``'s objection.

A policy (fiscal, for the channels of the company's enrollments) answers objections, never errors: invoicing
concatenates every policy's, in chain order, and refuses the document with all of them
(``DocumentInvalidError``, 422 ``invalid_document``). An objection is built by our own code: one that breaks
these bounds is a defect of its policy (``InvalidValueError``). Its message names the rule, never the value:
it is shown to the client and may be logged.
"""

import re
from dataclasses import dataclass
from typing import Final, final

from ledger_kernel.errors import InvalidValueError

MAX_FIELD: Final = 200
"""The longest path an objection names (``lines.3.description``, ``fiscal.do.withholdings.0.itbis``)."""
MAX_MESSAGE: Final = 500
_CODE: Final = re.compile(r"[a-z][a-z0-9_]{0,63}", re.ASCII)


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Objection:
    """A rule the document breaks: its stable code, the field it is about (if any), a message naming it."""

    code: str
    field: str | None
    message: str

    def __post_init__(self) -> None:
        if _CODE.fullmatch(self.code) is None:
            raise InvalidValueError(field="objection.code", requirement="a snake_case code")
        if self.field is not None and not 1 <= len(self.field) <= MAX_FIELD:
            raise InvalidValueError(
                field="objection.field", requirement=f"a path of 1 to {MAX_FIELD} characters"
            )
        if not 1 <= len(self.message) <= MAX_MESSAGE:
            raise InvalidValueError(field="objection.message", requirement=f"1 to {MAX_MESSAGE} characters")
