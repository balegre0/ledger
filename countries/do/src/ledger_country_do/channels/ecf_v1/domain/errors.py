"""The errors do-ecf-v1 raises on purpose.

Each has a ``kind`` (how it crosses a boundary: invalid, conflict, unavailable, internal) and a stable
``code``, mirroring ``ledger_kernel.errors``; the integration milestone makes these bases subclasses of the
kernel's (the spike imports nothing of the workspace). A message is built inside its class and never quotes a
document, an amount, a token or a tax id.
"""

from typing import ClassVar, Final, Literal
lazy from collections.abc import Callable

from ledger_country_do.regime.definitions import NotInForceError, UnknownEntryError

type ErrorKind = Literal["invalid", "conflict", "unavailable", "internal"]
_ELLIPSIS: Final = "…"


class EcfError(Exception):
    """Base of every error do-ecf-v1 raises on purpose: a defect unless a subclass says otherwise."""

    kind: ClassVar[ErrorKind] = "internal"
    code: ClassVar[str] = "do_ecf_internal"


class EcfInvalidError(EcfError):
    """The input breaks a rule: sending it again cannot work."""

    kind: ClassVar[ErrorKind] = "invalid"
    code: ClassVar[str] = "do_ecf_invalid"


class EcfConflictError(EcfError):
    """The current state forbids it."""

    kind: ClassVar[ErrorKind] = "conflict"
    code: ClassVar[str] = "do_ecf_conflict"


class EcfUnavailableError(EcfError):
    """Something outside failed; the same call may succeed later."""

    kind: ClassVar[ErrorKind] = "unavailable"
    code: ClassVar[str] = "do_ecf_unavailable"


def truncate(text: str, limit: int) -> str:
    """``text`` cut to at most ``limit`` characters, an ellipsis marking the cut."""
    return text if len(text) <= limit else text[: limit - 1] + _ELLIPSIS


class RegimeNotInForceError(EcfInvalidError):
    """The regime has no value in force on the document's date (a draft dated before the regime starts)."""

    code: ClassVar[str] = "do_ecf_regime_not_in_force"

    def __init__(self, cause: NotInForceError) -> None:
        super().__init__(f"the regime cannot decide this document: {cause}")


class RegimeDefectError(EcfError):
    """The regime lacks what the channel needs of it: a defect of the pack, never a caller's mistake."""

    code: ClassVar[str] = "do_ecf_regime_defect"

    def __init__(self, detail: str) -> None:
        super().__init__(f"the regime is defective: {detail}")


def from_regime[T](lookup: Callable[[], T]) -> T:
    """``lookup``'s value; the regime's lookup errors raised as pack errors (``EcfError``)."""
    try:
        return lookup()
    except NotInForceError as error:
        raise RegimeNotInForceError(error) from error
    except UnknownEntryError as error:
        raise RegimeDefectError(str(error)) from error
