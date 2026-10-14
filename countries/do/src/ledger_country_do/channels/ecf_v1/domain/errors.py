"""The errors do-ecf-v1 raises on purpose: the kernel's, one hierarchy with every other error of the platform.

``EcfError`` is a ``LedgerError`` (an internal defect unless a subclass says otherwise); the invalid, conflict
and unavailable bases are also the kernel's bases of those kinds, so a boundary that answers the kernel's
errors answers the channel's alike. Each has a stable ``code`` (``do_ecf_*``). A message is built inside its
class and never quotes a document, an amount, a token or a tax id.
"""

from typing import ClassVar
lazy from collections.abc import Callable

from ledger_country_do.regime.entries import RegimeEntryError
from ledger_kernel.dated import NotInEffectError
from ledger_kernel.errors import ConflictError, InvalidInputError, LedgerError, UnavailableError


class EcfError(LedgerError):
    """Base of every error do-ecf-v1 raises on purpose: a defect unless a subclass says otherwise."""

    code: ClassVar[str] = "do_ecf_internal"


class EcfInvalidError(EcfError, InvalidInputError):
    """The input breaks a rule: sending it again cannot work."""

    code: ClassVar[str] = "do_ecf_invalid"


class EcfConflictError(EcfError, ConflictError):
    """The current state forbids it."""

    code: ClassVar[str] = "do_ecf_conflict"


class EcfUnavailableError(EcfError, UnavailableError):
    """Something outside failed; the same call may succeed later."""

    code: ClassVar[str] = "do_ecf_unavailable"


class RegimeNotInForceError(EcfInvalidError):
    """The regime has no value in force on the document's date (a draft dated before the regime starts)."""

    code: ClassVar[str] = "do_ecf_regime_not_in_force"

    def __init__(self, cause: NotInEffectError) -> None:
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
    except NotInEffectError as error:
        raise RegimeNotInForceError(error) from error
    except RegimeEntryError as error:
        raise RegimeDefectError(str(error)) from error
