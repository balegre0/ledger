"""A ``FiscalDraft`` read back from the platform codec's JSON form (a job's payload, a stored ``jsonb``).

Apart from ``drafts`` so that the draft stays pure: reading JSON takes pydantic and the platform's codec,
which a channel's pure layers never load.
"""

from typing import Final

from pydantic import TypeAdapter, ValidationError

from ledger_fiscal_kit.drafts import FiscalDraft, FiscalDraftInvalidError
from ledger_kernel.errors import LedgerError
from ledger_kernel.money import Currency
lazy from ledger_platform.data.codec import JsonValue

_DRAFT: Final = TypeAdapter(FiscalDraft)


def _with_currencies(found: JsonValue, *, top: bool = True) -> object:
    """``found``, a draft's JSON form, with each currency code read as its ``Currency`` (``fields`` aside)."""
    match found:
        case dict():
            return {
                key: Currency.of(value)
                if key == "currency" and isinstance(value, str)
                else value
                if top and key == "fields"
                else _with_currencies(value, top=False)
                for key, value in found.items()
            }
        case list():
            return [_with_currencies(item, top=False) for item in found]
        case _:
            return found


def read_draft(found: JsonValue) -> FiscalDraft:
    """The draft whose JSON form (the platform codec's) is ``found``.

    ``FiscalDraftInvalidError`` when ``found`` is no draft's JSON form, or names a draft that breaks one of
    the draft's rules.
    """
    try:
        return _DRAFT.validate_python(_with_currencies(found))
    except FiscalDraftInvalidError:
        raise
    except (ValidationError, LedgerError) as error:
        raise FiscalDraftInvalidError(field=None, requirement="a fiscal draft's JSON form") from error
