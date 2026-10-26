"""An accepted e-CF delivered to its buyer: the ``do_ecf.deliver`` job, its retry, the company's options.

Spec §8.6.5 steps 1-4, Decisions 18-21. DGII requires an issuer to send each accepted e-CF to an electronic
buyer. Delivery **applies** (``deliverable``) when the document is accepted (or accepted conditionally), went
to DGII's reception (never a summarized 32, RFCE), its buyer has an RNC or cédula, and DGII publishes its
directory in the environment (not CerteCF); otherwise it is ``not_applicable``, as when DGII's directory lists
no receiver for the buyer (``NoReceiver``: the client hands it the printed representation).

Each run of the job (one buyer exchange) reads the document and the company's options in one unit, which
starts the delivery (``not_started`` to ``delivering``, ``delivery_since`` saved and published) before any
call, so a run that fails later never restarts the seven days; it then reads the signed e-CF back from the
archive (its SHA-256 checked), delivers it (``Buyers``: the directory, cached, then the buyer's reception, a
seed signed for a token only when the company switched **buyer authentication** on, Decision 19), keeps
whatever receipt the buyer answered in the archive, then records in another unit:

* a receipt that holds (``BuyerReceipt``): received, or not received as a duplicate (the buyer has it from an
  earlier attempt whose answer was lost): ``delivered`` (the latter with ``not_received_duplicate``); not
  received for another reason: ``failed`` with it (``not_received_<reason>``), the buyer's signed word, never
  retried;
* a receipt that fails its checks (``BuyerReceiptRefused``): kept as ``arecf-refused``, ``failed`` with its
  code, **never retried** (the buyer would answer the same);
* no receiver: ``not_applicable``;
* the buyer out of reach (``BuyerUnreached``): ``delivering``, tried again on Decision 21's schedule
  (``domain.delivery``) until seven days from the start, then ``failed`` with the last failure's code. A
  buyer that requires authentication while it is switched off is alerted at every attempt (the client may
  switch it on and retry).

**Every seed signed is audited** in the recording unit, whatever came of the exchange
(``do_ecf.buyer_seed.signed``, the document its subject, ``detail`` the endpoint's host and the buyer's tax id
masked, Decision 20); a receipt the archive could not keep audits them before the run fails. Every change of
``delivery`` publishes ``fiscal_channel.document.updated`` (version + 1); an attempt that leaves it
``delivering`` publishes nothing. A delivery is started again by the client (``retry``: ``delivering``, the
seven days from the retry, the earlier receipt forgotten, the job enqueued again) once it failed, or while it
is stuck (``not_started`` or ``delivering``) with no job left to run it (done, dead or gone).
"""

from abc import ABCMeta, abstractmethod
from dataclasses import replace
from typing import ClassVar, Final, Protocol, assert_type, final
lazy from collections.abc import Callable
lazy from datetime import datetime
lazy from uuid import UUID

from ledger_country_do.channels.ecf_v1.application.issuing import DocumentMissingError
from ledger_country_do.channels.ecf_v1.application.ports import (
    ArchiveUnavailableError,
    BuyerReceipt,
    BuyerReceiptRefused,
    BuyerUnreached,
    DeliveryDocument,
    NoReceiver,
)
from ledger_country_do.channels.ecf_v1.application.steps import Again, Finished
from ledger_country_do.channels.ecf_v1.domain.delivery import delivery_open, next_delivery
from ledger_country_do.channels.ecf_v1.domain.ecf import DeliveryState, EcfDocumentState
from ledger_country_do.channels.ecf_v1.domain.errors import EcfConflictError, EcfError
from ledger_country_do.channels.ecf_v1.domain.exchanges import NotReceivedReason
from ledger_country_do.channels.ecf_v1.domain.mapping import buyer_rnc, issuer_rnc
from ledger_country_do.channels.ecf_v1.domain.routing import Route
from ledger_kernel.errors import NotFoundError
from ledger_kernel.tax_ids import masked
lazy from ledger_country_do.channels.ecf_v1.application.documents import DeliveryOptions, EcfDocument
lazy from ledger_country_do.channels.ecf_v1.application.ports import (
    Archive,
    Archived,
    Buyers,
    Handed,
    SeedSigned,
    SigningAs,
)
lazy from ledger_country_do.channels.ecf_v1.application.ranges import NumberingUnit, NumberingUnits
lazy from ledger_country_do.channels.ecf_v1.application.steps import Next
lazy from ledger_fiscal_kit.drafts import DocumentRef
lazy from ledger_kernel.tenancy import Tenant

_ACCEPTED: Final = frozenset({EcfDocumentState.ACCEPTED, EcfDocumentState.ACCEPTED_CONDITIONALLY})
_UNDER_WAY: Final = frozenset({DeliveryState.NOT_STARTED, DeliveryState.DELIVERING})
"""Where a run takes a delivery on from; anything else is settled (or the client's to retry)."""
NOT_DELIVERABLE: Final = "not_deliverable"
"""``delivery_reason`` of a document delivery does not apply to (step 1 of §8.6.5)."""
NO_DIRECTORY: Final = "no_directory_in_environment"
"""``delivery_reason`` in an environment where DGII publishes no directory of receivers (CerteCF)."""
SEED_SIGNED: Final = "do_ecf.buyer_seed.signed"


class DeliveryNotFoundError(EcfError, NotFoundError):
    """The invoice has no e-CF in the company and mode."""

    code: ClassVar[str] = "do_ecf_delivery_not_found"

    def __init__(self) -> None:
        super().__init__("the invoice has no e-CF in this company and mode")


class DeliveryNotRetryableError(EcfConflictError):
    """Only a delivery that failed is retried: one under way, done or not applicable is not."""

    code: ClassVar[str] = "do_ecf_delivery_not_retryable"

    def __init__(self) -> None:
        super().__init__("only a delivery that failed can be retried")


class DeliveryReport(Protocol, metaclass=ABCMeta):
    """Where the job says what a person (or the client) may have to look at."""

    @abstractmethod
    def needs_authentication(self) -> None:
        """A buyer requires authentication, switched off for the company (Decision 19): alerted."""

    @abstractmethod
    def failed(self, reason: str) -> None:
        """A delivery failed for good (``reason``, a code): alerted; the client may retry it."""


def deliverable(document: EcfDocument) -> bool:
    """Whether delivery applies to ``document`` at all (step 1 of §8.6.5; see the module)."""
    return (
        document.state in _ACCEPTED
        and document.route is Route.RECEPTION
        and buyer_rnc(document.draft) is not None
    )


async def _found(unit: NumberingUnit, document_id: UUID, *, lock: bool) -> EcfDocument:
    """The unit's document ``document_id`` (``DocumentMissingError``: not the company's, a defect)."""
    document = await unit.documents.get(document_id, lock=lock)
    if document is None:
        raise DocumentMissingError
    return document


def _not_applicable(document: EcfDocument, buyer: str | None) -> str | None:
    """Why delivery does not apply to ``document`` (``buyer``: its buyer's RNC), or ``None`` when it does."""
    if buyer is None or not deliverable(document):
        return NOT_DELIVERABLE
    return None if document.environment.has_directory else NO_DIRECTORY


def _not_received(reason: NotReceivedReason | None) -> str:
    return "not_received" if reason is None else f"not_received_{reason.name.lower()}"


@final
class Delivering:
    """The delivery's use cases (see the module)."""

    __slots__ = ("_archive", "_buyers", "_report", "_units")

    def __init__(
        self,
        units: NumberingUnits,
        archive: Archive,
        buyers: Callable[[], Buyers],
        report: DeliveryReport,
    ) -> None:
        """``buyers``: the buyers' client, open (the worker's; a defect anywhere else)."""
        self._units = units
        self._archive = archive
        self._buyers = buyers
        self._report = report

    async def deliver(self, who: SigningAs, document_id: UUID) -> Next:
        """One run of the job for ``document_id`` (see the module)."""
        async with self._units.unit(who.tenant) as unit:
            document = await _found(unit, document_id, lock=True)
            if document.delivery not in _UNDER_WAY:
                return Finished()
            buyer = buyer_rnc(document.draft)
            not_applicable = _not_applicable(document, buyer)
            if buyer is None or not_applicable is not None:
                await self._move(unit, document, DeliveryState.NOT_APPLICABLE, not_applicable)
                return Finished()
            options = await unit.options.get()
            if document.delivery is DeliveryState.NOT_STARTED:  # started before any call: never restarted
                started = replace(document, delivery_since=unit.now)
                document = await self._move(unit, started, DeliveryState.DELIVERING, None)
        since = document.delivery_since or document.updated_at
        signed = await self._archive.read(document.signed().ecf)
        parties = DeliveryDocument(issuer_rnc=issuer_rnc(document.draft), buyer_rnc=buyer, encf=document.encf)
        handed = await self._buyers().deliver(
            who, document.environment, parties, signed, authenticate=options.buyer_authentication
        )
        kept = await self._kept(who.tenant, document, handed)
        return await self._recorded(who.tenant, document, handed, kept, since)

    async def _kept(self, tenant: Tenant, document: EcfDocument, handed: Handed) -> Archived | None:
        """The buyer's receipt, kept in the archive (``None`` without one); the seeds audited if it fails."""
        if not isinstance(handed, BuyerReceipt | BuyerReceiptRefused):
            return None
        refused = isinstance(handed, BuyerReceiptRefused)
        try:
            return await self._archive.buyer_receipt(tenant, handed.arecf, refused=refused)
        except ArchiveUnavailableError:
            async with self._units.unit(tenant) as unit:
                self._audited(unit, document, handed.seeds)
            raise

    @staticmethod
    def _audited(unit: NumberingUnit, document: EcfDocument, seeds: tuple[SeedSigned, ...]) -> None:
        """Every seed signed for the exchange, audited with the buyer masked (Decision 20)."""
        for seed in seeds:
            detail = {"endpoint_host": seed.endpoint_host, "buyer": masked(seed.buyer)}
            unit.audit.record(SEED_SIGNED, document.id, before=None, after=None, detail=detail)

    async def _recorded(
        self, tenant: Tenant, document: EcfDocument, handed: Handed, kept: Archived | None, since: datetime
    ) -> Next:
        """What came of the exchange, recorded (see the module)."""
        async with self._units.unit(tenant) as unit:
            if not isinstance(handed, NoReceiver):
                self._audited(unit, document, handed.seeds)
            current = await _found(unit, document.id, lock=True)
            if current.delivery not in _UNDER_WAY:  # settled meanwhile (a person's doing): nothing to take
                return Finished()
            match handed:
                case BuyerReceipt(received=received, reason=reason):
                    if received or reason is NotReceivedReason.DUPLICATE:  # a duplicate: it has it already
                        why = None if received else _not_received(reason)
                        await self._move(unit, current, DeliveryState.DELIVERED, why, receipt=kept)
                        return Finished()
                    failed = _not_received(reason)
                    await self._move(unit, current, DeliveryState.FAILED, failed, receipt=kept)
                    self._report.failed(failed)
                    return Finished()
                case BuyerReceiptRefused(reason=reason):
                    await self._move(unit, current, DeliveryState.FAILED, reason, receipt=kept)
                    self._report.failed(reason)
                    return Finished()
                case NoReceiver(reason=reason):
                    await self._move(unit, current, DeliveryState.NOT_APPLICABLE, reason)
                    return Finished()
                case _:
                    assert_type(handed, BuyerUnreached)
                    return await self._unreached(unit, current, handed, since)

    async def _unreached(
        self, unit: NumberingUnit, current: EcfDocument, handed: BuyerUnreached, since: datetime
    ) -> Next:
        """The buyer out of reach: tried again on the schedule, or ``failed`` once the seven days are over."""
        if handed.needs_authentication:
            self._report.needs_authentication()
        attempts = current.delivery_attempts + 1
        tried = replace(current, delivery_attempts=attempts)
        if not delivery_open(since, unit.now):
            await self._move(unit, tried, DeliveryState.FAILED, handed.code)
            self._report.failed(handed.code)
            return Finished()
        await self._move(unit, tried, DeliveryState.DELIVERING, handed.code)
        return Again(next_delivery(since, unit.now, attempts))

    @staticmethod
    async def _move(
        unit: NumberingUnit,
        current: EcfDocument,
        to: DeliveryState,
        reason: str | None,
        *,
        receipt: Archived | None = None,
    ) -> EcfDocument:
        """``current``'s delivery moved ``to`` (with ``reason``, and ``receipt`` if any), kept, published.

        Its version is one more when ``delivery`` changes; an attempt that leaves it as it was publishes
        nothing.
        """
        moved = replace(current, delivery=to, delivery_reason=reason, receipt=receipt or current.receipt)
        if to is current.delivery:
            await unit.documents.changed(moved)
            return moved
        moved = replace(moved, version=current.version + 1, updated_at=unit.now)
        await unit.documents.changed(moved)
        unit.announcements.document_updated(moved)
        return moved

    # ── the client's ────────────────────────────────────────────────────────────────

    async def retry(self, unit: NumberingUnit, invoice: DocumentRef) -> EcfDocument:
        """``invoice``'s delivery started again in ``unit``: seven days from now, the job enqueued.

        A delivery that failed, or one stuck (``not_started`` or ``delivering``), whose job no longer runs:
        done, dead, or gone (a job that may still run is never left behind a retry it would ignore).
        ``DeliveryNotFoundError`` when the invoice has no e-CF; ``DeliveryNotRetryableError`` otherwise
        (done, not applicable, or a job still waiting or running, which may finish it). The receipt of an
        earlier attempt is forgotten (the archive keeps it).
        """
        found = await unit.documents.of_invoice(invoice)
        if found is None:
            raise DeliveryNotFoundError
        current = await _found(unit, found.id, lock=True)
        retryable = current.delivery is DeliveryState.FAILED or current.delivery in _UNDER_WAY
        if not retryable or await unit.deliveries.pending(current.id):  # a job that may still run: its own
            raise DeliveryNotRetryableError
        started = replace(current, delivery_since=unit.now, delivery_attempts=0, receipt=None)
        moved = await self._move(unit, started, DeliveryState.DELIVERING, None)
        unit.audit.record(
            "delivery.retry",
            moved.id,
            before={"delivery": current.delivery.value},
            after={"delivery": moved.delivery.value},
        )
        unit.deliveries.enqueue(moved.id)
        return moved

    async def options(self, tenant: Tenant) -> DeliveryOptions:
        """The company's options in the tenant's mode."""
        async with self._units.unit(tenant) as unit:
            return await unit.options.get()

    @staticmethod
    async def set_options(unit: NumberingUnit, options: DeliveryOptions, subject: UUID) -> DeliveryOptions:
        """The company's options set to ``options`` in ``unit``; audited if changed (``subject``: its id)."""
        current = await unit.options.get()
        if current != options:
            await unit.options.put(options)
            unit.audit.record("delivery_options.set", subject, before=current, after=options)
        return options
