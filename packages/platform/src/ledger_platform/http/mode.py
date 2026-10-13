"""``Ledger-Mode`` (§9.7, ADR 0007): which mode a human token's request acts in; required on moded routes.

The value is one of the exact words ``test`` and ``live``, sent once: no other case, no padding, no list. The
header's name is matched in any case, as HTTP says. A refusal never repeats what was sent.

API-client credentials carry their own mode from v2-M2 (§9.7); until then the header is the only source.

FastAPI reads the header as optional, so that a missing one is our 400 (``mode_required``) and not its
422; the app's OpenAPI document says what is true of a moded route instead: the header is required, ``test``
or ``live`` (``require_mode_header``).
"""

from typing import Annotated, ClassVar, Final, TypeIs

from fastapi import Header
lazy from fastapi import Request

from ledger_kernel.tenancy import Mode
from ledger_platform.http.problems import BadRequestError

MODE_HEADER: Final = "Ledger-Mode"
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


async def ledger_mode(
    request: Request,
    header: Annotated[str | None, Header(alias=MODE_HEADER, description=_DESCRIPTION)] = None,
) -> Mode:
    """The request's mode: ``ModeRequiredError`` without the header, ``ModeInvalidError`` for any other value.

    ``header`` documents the parameter and says whether there is one; every line of it is read, so that two
    lines (which FastAPI would reduce to one) are refused rather than one of them chosen.
    """
    if header is None:
        raise ModeRequiredError
    sent = request.headers.getlist(MODE_HEADER)
    mode = _MODES.get(sent[0]) if len(sent) == 1 else None
    if mode is None:
        raise ModeInvalidError
    return mode


def _is_object(value: object) -> TypeIs[dict[str, object]]:
    return isinstance(value, dict)


def _is_array(value: object) -> TypeIs[list[object]]:
    return isinstance(value, list)


def require_mode_header(document: dict[str, object]) -> None:
    """Document each ``Ledger-Mode`` header parameter of an OpenAPI ``document`` as it is: required.

    One of the words ``test`` and ``live``. In place; any other part of the document is left as it is.
    """
    paths = document.get("paths")
    operations = [
        operation
        for item in (paths.values() if _is_object(paths) else ())
        if _is_object(item)
        for operation in item.values()
        if _is_object(operation)
    ]
    for operation in operations:
        parameters = operation.get("parameters")
        for parameter in parameters if _is_array(parameters) else ():
            if _is_object(parameter) and (parameter.get("name"), parameter.get("in")) == (
                MODE_HEADER,
                "header",
            ):
                parameter["required"] = True
                parameter["schema"] = {"type": "string", "enum": [mode.value for mode in Mode]}
                parameter["description"] = _DESCRIPTION
