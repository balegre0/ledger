"""What invoicing announces (§3.5): the facts other modules build on. Imported by the domain to emit them.

Each is published in the unit that makes the change it announces, so it commits with the document, its audit
record and the request's ``Idempotency-Key``, or not at all: a refused request, a draft and a replay announce
nothing. Its envelope is the outbox row's:

- ``scope``: the document's tenant, its organization, company and mode (a document exists in one mode, §7.1);
- ``subject``: the document's id (the spec's ``document`` field: the envelope already carries it);
- ``aggregate_version``: the version the change left (the document's ``ETag``), so a consumer applies events
  version-monotonically and replays nothing older.

Payloads are thin and stable (``VERSION`` 1: any change to a payload's shape is a new version; the tests pin
each payload until event schema snapshots check them in CI, §2 rule 9): a consumer reads the document through
``InvoicingApi.document`` and never learns a party, a tax id, a description or an amount from an event. Kinds
and directions travel as their words, ids as UUID strings (the platform's codec):

- ``invoicing.invoice.issued`` v1: ``{"kind": "invoice" | "credit_note" | "debit_note", "direction": "sales"
  | "purchase", "corrects": "<uuid>" | null}``;
- ``invoicing.invoice.voided`` v1: ``{"kind": ..., "direction": ...}``;
- ``invoicing.invoice.fiscal_updated`` v1 (public ``invoice.fiscal_updated``): ``{"channel": "do-ecf-v1",
  "status": "pending" | "accepted" | "accepted_with_observations" | "rejected" | "voided", "version": 3}``,
  announced by the unit that keeps a channel's newer status in the fiscal mirror. Its ``aggregate_version`` is
  that status's version in its channel (the mirror is not the document's state: the document's own version is
  unchanged), so a consumer keeps the newest per channel; the payload repeats it for webhooks, which show no
  envelope.
"""

from dataclasses import dataclass
from typing import ClassVar, final
lazy from uuid import UUID

from ledger_kernel.errors import InvalidValueError
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


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class InvoiceFiscalUpdated(Event):
    """A channel's newer status of a document was kept in the fiscal mirror (see the module)."""

    TYPE: ClassVar[str] = "invoicing.invoice.fiscal_updated"
    VERSION: ClassVar[int] = 1
    channel: str
    status: str
    """The platform's word for it (``FiscalStatusName``'s value)."""
    version: int
    """The status's version in its channel: the envelope's ``aggregate_version``, repeated."""

    def __post_init__(self) -> None:
        if self.aggregate_version != self.version:
            raise InvalidValueError(
                field="invoice_fiscal_updated.aggregate_version", requirement="the version"
            )


type InvoicingEvent = InvoiceIssued | InvoiceVoided | InvoiceFiscalUpdated
"""Every event invoicing publishes (its manifest's ``[[events.publishes]]``)."""
