"""What the use cases need: invoicing's state in one unit of work, and identity through its API.

A unit is one transaction scoped to a company in a mode: row-level security limits what its repositories see
to that tenant, and every repository names it in every statement (tenancy is checked twice, §2). identity is
asked outside any unit (rule 13: no facade call while a unit is open), through ``IdentityLookup``. The methods
are abstract so that they need no body: a ``...`` is a statement no test could reach.

**Lock order.** A unit that locks two documents locks the note before the invoice it corrects (issuing a draft
note); every other unit locks one document at most. A note being created or issued and a void of its invoice
serialize on the invoice's lock, so whichever comes second sees the other's commit: no note stands on a voided
invoice, and no invoice is voided under a note. A fiscal status is applied under a share of the document's
lock (``FiscalMirror.apply``), so a void, which reads the mirror under the document's lock, never misses an
acceptance written meanwhile.
"""

from abc import ABCMeta, abstractmethod
from dataclasses import dataclass
from typing import ClassVar, Literal, Protocol, final
lazy from collections.abc import Callable, Mapping, Sequence
lazy from contextlib import AbstractAsyncContextManager
lazy from datetime import datetime
lazy from uuid import UUID

from ledger_kernel.errors import LedgerError
lazy from ledger_identity.api import CompanyInfo
lazy from ledger_invoicing.domain.fiscal import Applied, FiscalStatus
lazy from ledger_invoicing.domain.ids import InvoiceId
lazy from ledger_invoicing.domain.invoice import Invoice, InvoiceStatus
lazy from ledger_invoicing.domain.pricing import EstablishmentRef
lazy from ledger_invoicing.domain.text import ExternalReference
lazy from ledger_invoicing.events import InvoicingEvent
lazy from ledger_kernel.ids import CompanyId
lazy from ledger_kernel.jurisdiction import Jurisdiction
lazy from ledger_kernel.pagination import Page, PageRequest
lazy from ledger_kernel.regimes import Direction, DocumentKind, RegimeDef
lazy from ledger_kernel.tenancy import Tenant
lazy from ledger_platform.runtime.calls import Call


class ForeignTenantError(LedgerError):
    """A unit's documents were handed another tenant's document to keep: a defect of the caller."""

    code: ClassVar[str] = "invoice_foreign_tenant"

    def __init__(self) -> None:
        super().__init__("a unit keeps the documents of its own company and mode only")


class StaleWriteError(LedgerError):
    """A change of a document that is not the version after the one kept: the unit did not hold it locked."""

    code: ClassVar[str] = "invoice_write_stale"

    def __init__(self) -> None:
        super().__init__("a change keeps the version after the one the unit holds locked")


type RegimeOf = Callable[[Jurisdiction], RegimeDef | None]
"""The installed regime that governs a jurisdiction, if any (its subdivision's, else its country's)."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class InvoiceFilters:
    """Which of a tenant's documents a list holds: those of every condition given (``None``: any)."""

    kind: DocumentKind | None = None
    direction: Direction | None = None
    status: InvoiceStatus | None = None
    external_reference: ExternalReference | None = None


class Invoices(Protocol, metaclass=ABCMeta):
    """The documents of the unit's company, in the unit's mode."""

    @abstractmethod
    async def add(self, invoice: Invoice) -> InvoiceId | None:
        """Keep a new document: ``None`` once kept, else the document of its type holding its business key.

        Nothing is kept when the business key is taken: it is unique per company, mode and type for ever.
        Another tenant's document is refused (``ForeignTenantError``): a defect of the caller, before anything
        is kept.
        """

    @abstractmethod
    async def get(self, invoice: InvoiceId) -> Invoice | None:
        """The document, or ``None`` (another company's or mode's is ``None`` too)."""

    @abstractmethod
    async def locked(self, invoice: InvoiceId) -> Invoice | None:
        """The document, locked until the unit ends: whatever is decided on it waits for other units first."""

    @abstractmethod
    async def update(self, invoice: Invoice) -> None:
        """Keep ``invoice`` in place of the version kept of it, which the unit holds locked.

        ``StaleWriteError`` unless the version kept is the one before ``invoice``'s; refused as ``add``
        refuses, too.
        """

    @abstractmethod
    async def corrected_by(self, invoice: InvoiceId) -> bool:
        """Whether a draft or issued note of the tenant corrects ``invoice`` (a voided one does not)."""

    @abstractmethod
    async def page(self, request: PageRequest, filters: InvoiceFilters) -> Page[Invoice]:
        """The page ``request`` asks for of the documents ``filters`` names, in id (creation) order."""


class FiscalMirror(Protocol, metaclass=ABCMeta):
    """What the fiscal channels last reported of the unit's tenant's documents, one status per channel."""

    @abstractmethod
    async def of(self, invoices: Sequence[InvoiceId]) -> Mapping[InvoiceId, tuple[FiscalStatus, ...]]:
        """Each of ``invoices`` with its statuses, by channel key (none for one of another tenant)."""

    @abstractmethod
    async def accepted(self, invoice: InvoiceId) -> bool:
        """Whether a channel accepted ``invoice``: read under its lock by the unit that voids it."""

    @abstractmethod
    async def apply(self, invoice: InvoiceId, status: FiscalStatus) -> Applied | None:
        """Keep ``status`` if newer than the one kept for its channel; ``None`` for no such ``invoice``.

        Takes a share of the document's lock first: it waits for a unit that holds the document locked.
        """


class Events(Protocol, metaclass=ABCMeta):
    """What the unit announces once it commits."""

    @abstractmethod
    def publish(self, event: InvoicingEvent) -> None:
        """Announce ``event`` when, and only if, the unit commits (the outbox, written with the unit)."""


type AuditAction = Literal["invoice.create", "invoice.issue", "invoice.void"]
"""What a use case records it did (§10): every change of a document is audited."""


class Audit(Protocol, metaclass=ABCMeta):
    """Who changed what, recorded with the change (``audit_events``, §6.4): it commits with the unit, or not.

    The actor is the call the unit runs in, not the use case's to name.
    """

    @abstractmethod
    def record(
        self,
        action: AuditAction,
        subject: UUID,
        *,
        before: object,
        after: object,
        detail: Mapping[str, str] | None = None,
    ) -> None:
        """Record that ``action`` changed ``subject`` from ``before`` to ``after`` (``None``: not there).

        Only hashes of the two are kept; ``detail`` holds codes only, never what a client typed.
        """


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class InvoicingUnit:
    """One unit of work: invoicing's state as one transaction sees it, and what it announces."""

    invoices: Invoices
    fiscal: FiscalMirror
    events: Events
    audit: Audit
    now: datetime
    """The unit's time: everything it records and announces happened then, as one transaction does."""


class InvoicingUnits(Protocol, metaclass=ABCMeta):
    """Where the use cases get the units they work in, one for each thing they do."""

    @abstractmethod
    def unit(self, tenant: Tenant) -> AbstractAsyncContextManager[InvoicingUnit]:
        """A unit scoped to ``tenant`` (a company in a mode); it commits when its block ends cleanly."""


class IdentityLookup(Protocol, metaclass=ABCMeta):
    """What invoicing asks identity (``IdentityApi``), never while a unit is open (rule 13).

    invoicing's own port, over ``IdentityApi.company`` and ``IdentityApi.establishment`` (the spec's
    ``CompanyDirectory`` is fiscal-kit's, for channels).

    Answers are as identity has them when asked; they decide the seller, the regime and the establishment's
    day, and a document stores what it took from them.
    """

    @abstractmethod
    async def company(self, call: Call, company: CompanyId) -> CompanyInfo:
        """The company (``CompanyNotFoundError`` when the call's organization has none by that id)."""

    @abstractmethod
    async def establishment(
        self, call: Call, company: CompanyId, code: str | None
    ) -> EstablishmentRef | None:
        """The company's establishment of ``code`` (``MAIN`` when ``None``), or ``None`` when it has none."""


class Log(Protocol, metaclass=ABCMeta):
    """Where invoicing tells whoever runs it what went wrong outside a request's answer (the module's logger).

    Fields are codes and type names only: never a document, a party or what a client sent.
    """

    @abstractmethod
    def warning(self, event: str, /, **fields: str) -> None:
        """Log ``event`` at warning level with ``fields``."""

    @abstractmethod
    def exception(self, event: str, /, **fields: str) -> None:
        """Log ``event`` at error level with ``fields``, handling an exception (its type one of them)."""
