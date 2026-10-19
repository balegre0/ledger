"""Identifiers of invoicing's own entities; organizations and companies use the kernel's."""

from typing import Final, NewType
from uuid import UUID

InvoiceId = NewType("InvoiceId", UUID)
"""A commercial document: an invoice, a credit note or a debit note."""

INVOICE_PREFIX: Final = "inv"
"""The public id prefix of a commercial document: ``inv_01h455vb4pex5vsknk084sn02q``."""
