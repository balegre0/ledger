"""What invoicing announces (§3.5): the facts other modules build on. Imported by the domain to emit them.

Each is scoped to its document's tenant (organization, company, mode: a document exists in one mode, §7.1);
its subject is the document and its ``aggregate_version`` the version the change left. Payloads are thin: a
consumer reads the document through ``InvoicingApi`` and never learns a buyer, a description or an amount from
an event. Kinds and directions travel as their words.
"""

from dataclasses import dataclass
from typing import ClassVar, final
lazy from uuid import UUID

from ledger_kernel.events import Event
lazy from ledger_kernel.regimes import Direction, DocumentKind


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class InvoiceIssued(Event):
    """A document was issued: from now on it is a fact to route to the fiscal channels (§8.5)."""

    TYPE: ClassVar[str] = "invoicing.invoice.issued"
    VERSION: ClassVar[int] = 1
    kind: DocumentKind
    direction: Direction
    corrects: UUID | None
    """The invoice a credit or debit note corrects; ``None`` for an invoice."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class InvoiceVoided(Event):
    """An issued document was voided before any channel accepted it."""

    TYPE: ClassVar[str] = "invoicing.invoice.voided"
    VERSION: ClassVar[int] = 1
    kind: DocumentKind
    direction: Direction
