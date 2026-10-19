"""Public ids (§9.4): a document's UUID is shown as a TypeID (``inv_…``); anything else names no document.

Formatted and read only here, at the HTTP edge. A path that names an id of another type, an internal UUID or
text that is no TypeID of the prefix finds nothing (404) and is never repeated: no id is probed by type.
"""

from typing import Annotated, Final

from pydantic import StringConstraints

from ledger_invoicing.domain.errors import InvoiceNotFoundError
from ledger_invoicing.domain.ids import INVOICE_PREFIX, InvoiceId
from ledger_kernel.ids import InvalidPublicIdError, format_id, parse_id

_SUFFIX: Final = "[0-7][0-9a-hjkmnp-tv-z]{25}"
"""A TypeID's 26 base32 characters (Crockford's alphabet, lower case): 128 bits, so the first is at most 7."""

type InvoiceRef = Annotated[str, StringConstraints(pattern=rf"^{INVOICE_PREFIX}_{_SUFFIX}$")]
"""A document, by its public id (``inv_01h455vb4pex5vsknk084sn02q``)."""


def invoice_ref(invoice: InvoiceId) -> str:
    """The public id of ``invoice``."""
    return format_id(INVOICE_PREFIX, invoice)


def parse_invoice_ref(text: str) -> InvoiceId:
    """The document ``text`` names; ``InvoiceNotFoundError`` for anything but a public id of one."""
    try:
        return InvoiceId(parse_id(INVOICE_PREFIX, text))
    except InvalidPublicIdError:
        raise InvoiceNotFoundError from None
