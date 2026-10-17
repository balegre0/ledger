"""``Ledger-Mode`` (§9.7, ADR 0007): which mode a request acts in; required on moded routes.

The value is one of the exact words ``test`` and ``live``, sent once: no other case, no padding, no list. The
header's name is matched in any case, as HTTP says. A refusal never repeats what was sent.

A human's request names its mode in the header. An API client's credential carries its own (``MODE_KEY``,
left in the request by ``with_standing``): the header is then optional, and may only repeat it (403
``mode_mismatch`` otherwise).

FastAPI reads the header as optional, so that a missing one is our 400 (``mode_required``) and not its
422; the app's OpenAPI document says what is true of a moded route instead: the header is required, ``test``
or ``live`` (``require_mode_header``).
"""

from typing import Annotated, ClassVar, Final

from fastapi import Header
lazy from fastapi import Request

from ledger_kernel.errors import ForbiddenError
from ledger_kernel.tenancy import Mode
from ledger_platform.http.documents import operations_of, parameters_of
from ledger_platform.http.problems import BadRequestError

MODE_HEADER: Final = "Ledger-Mode"
MODE_KEY: Final = "ledger.mode"
"""Where the mode of an API client's credential waits in the request's ASGI scope."""
_MODES: Final = {mode.value: mode for mode in Mode}
_DESCRIPTION: Final = "test or live: the mode the request acts in."


class ModeRequiredError(BadRequestError):
    """A moded route was called without ``Ledger-Mode``."""

    code: ClassVar[str] = "mode_required"

    def __init__(self) -> None:
        super().__init__(f"this route acts in a mode: send {MODE_HEADER}: test or live")


class ModeInvalidError(BadRequestError):
    """``Ledger-Mode`` names no mode, or is sent more than once."""

    code: ClassVar[str] = "mode_invalid"

    def __init__(self) -> None:
        super().__init__(f"{MODE_HEADER} is sent once, as test or live")


class ModeMismatchError(ForbiddenError):
    """The request names a mode other than the one its API client's credential is bound to."""

    code: ClassVar[str] = "mode_mismatch"

    def __init__(self) -> None:
        super().__init__("this credential acts in the other mode only")


async def ledger_mode(
    request: Request,
    header: Annotated[str | None, Header(alias=MODE_HEADER, description=_DESCRIPTION)] = None,
) -> Mode:
    """The request's mode: its credential's, else the header's (``ModeRequiredError`` without one).

    ``ModeInvalidError`` for any value that is not a mode, whatever the credential; ``ModeMismatchError`` for
    a mode other than the credential's. ``header`` documents the parameter and says whether there is one;
    every line of it is read, so that two lines (which FastAPI would reduce to one) are refused rather than
    one of them chosen.
    """
    bound = request.scope.get(MODE_KEY)
    if header is None:
        if isinstance(bound, Mode):
            return bound
        raise ModeRequiredError
    sent = request.headers.getlist(MODE_HEADER)
    mode = _MODES.get(sent[0]) if len(sent) == 1 else None
    if mode is None:
        raise ModeInvalidError
    if isinstance(bound, Mode) and mode is not bound:
        raise ModeMismatchError
    return mode


def require_mode_header(document: dict[str, object]) -> None:
    """Document each ``Ledger-Mode`` header parameter of an OpenAPI ``document`` as it is: required.

    One of the words ``test`` and ``live``. In place; any other part of the document is left as it is.
    """
    for operation in operations_of(document):
        for parameter in parameters_of(operation):
            if (parameter.get("name"), parameter.get("in")) == (MODE_HEADER, "header"):
                parameter["required"] = True
                parameter["schema"] = {"type": "string", "enum": [mode.value for mode in Mode]}
                parameter["description"] = _DESCRIPTION
