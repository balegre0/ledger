"""Issuing an e-CF (spec §8.3 ``check`` and ``issue``, §8.5 step 4, §8.6.4 ``numbered → signed``).

``check`` writes nothing: the draft's objections (``mapping.objections``, the corrected invoice's e-CF looked
up among the company's accepted ones in the draft's mode), and ``do_ecf_range_missing`` when no number of the
draft's type could be given now in its environment (a read, no lock; Decision 6).

``issue`` gives an invoice one document (Decision 35): a second call answers the first's, and gives a
document not yet decided its ``do_ecf.advance`` job again when that job died (a wake past its deadline, its
attempts spent) or is gone: a fresh one, with a new deadline, the job's own steps deciding what to do (never
a resend: the submission mark governs). A job ready, running or parked is left as it is. fiscal's recovery
path calls it again for every issued invoice (Task 19's review, Owner item 13). The first numbers
it in one unit: the series' row locked (``Ranges.allocate``, a released number first), the document kept
``numbered`` with the draft it was numbered for, the ``do_ecf.advance`` job enqueued (at once, or
``SIGNING_DELAY`` later when the request signs, so that the worker does not sign what the request is
signing; Decision 25), ``fiscal_channel.document.updated`` published (version 1); and it commits **before
anything is signed or archived**: a unit that rolls back gives its number back, and a signature or an
archive object made for it would name an e-NCF another document gets.

Then, when ``sign_within`` is given and the document is still ``numbered``, it is signed (``Signing``) and
archived (the e-CF, and the summary on the RFCE route) within that time; a unit then locks the document and
records the signature only if it is still ``numbered`` (version 2): a document signed by a concurrent request
or the job first keeps that signature, and this one stays an orphan in the archive, never referenced
(Decision 11). A signer slower than the budget, or any refusal of the channel's (no certificate, the signer
or the archive unavailable...), leaves the document ``numbered`` for the job (``SigningReport.deferred``).
What is recorded is never undone by the budget: the recording unit runs after it, never cancelled midway.

Two first requests for one invoice may both find no document: the series' lock orders them, and the
second's insert meets the first's (``DocumentExistsError``); its unit rolls back, giving its number back,
and it answers the first's document as a second call would, signing it if it may. A number is allocated at
the moment the channel's clock reads (``Signing.now``), the one its signature states. A refusal no retry
mends while the request signs (the signer's, the channel's checks of what came back) is reported
(``SigningReport.failed``); the signer or the archive out of reach, or the budget spent, only deferred.

``advance`` is the job's step for a ``numbered`` document (``advancing`` takes it on from there): the same
signing, then the same recording, from the stored draft, and the job runs again at once to submit it. A
company that cannot sign (no certificate, one not valid then, the signer's clock) waits (``certificate``); a
range that ended before the moment of signing blocks it (``blocked``, severity error: it needs another
number), and so does a refusal no retry mends, reported; the signer or the archive unavailable is raised, for
the job to run again later. A document another run moved past ``numbered`` meanwhile is taken from where it
is. A job naming no document is a defect: reported, and a conflict (the job is dead at once, never retried).
"""

import asyncio
from abc import ABCMeta, abstractmethod
from dataclasses import dataclass, replace
from datetime import timedelta
from typing import ClassVar, Final, Protocol, final
lazy from datetime import date, datetime
lazy from decimal import Decimal
lazy from uuid import UUID

from ledger_country_do.channels.ecf_v1.application.documents import (
    DocumentExistsError,
    EcfDocument,
    Signature,
)
from ledger_country_do.channels.ecf_v1.application.ports import (
    CertificateMissingError,
    CertificateNotValidError,
    SignerClockSkewError,
)
from ledger_country_do.channels.ecf_v1.application.signing import SequenceExpiredError
from ledger_country_do.channels.ecf_v1.application.steps import Again, ParkReason, Wait
from ledger_country_do.channels.ecf_v1.domain.ecf import (
    DeliveryState,
    EcfDocumentState,
    EmissionMode,
    Step,
    is_final,
    transition,
)
from ledger_country_do.channels.ecf_v1.domain.errors import (
    EcfConflictError,
    EcfError,
    EcfUnavailableError,
)
from ledger_country_do.channels.ecf_v1.domain.mapping import (
    DraftObjectedError,
    ecf_draft,
    ecf_type_of,
    objections,
    verify_totals,
)
from ledger_country_do.channels.ecf_v1.domain.numbering import UNDATED_TYPES, NoNumberAvailableError
from ledger_country_do.channels.ecf_v1.domain.routing import route
from ledger_country_do.channels.ecf_v1.domain.totals import compute_totals
from ledger_country_do.regime import REGIME
from ledger_fiscal_kit.objections import Objection
from ledger_kernel.errors import InvalidInputError
from ledger_kernel.ids import new_id
lazy from ledger_country_do.channels.ecf_v1.application.ports import Archive, SigningAs
lazy from ledger_country_do.channels.ecf_v1.application.ranges import NumberingUnit, NumberingUnits
lazy from ledger_country_do.channels.ecf_v1.application.signing import Signing
lazy from ledger_country_do.channels.ecf_v1.application.steps import Next
lazy from ledger_country_do.channels.ecf_v1.domain.drafts import EcfDraft
lazy from ledger_country_do.channels.ecf_v1.domain.encf import Encf
lazy from ledger_country_do.channels.ecf_v1.domain.environments import Environment
lazy from ledger_fiscal_kit.drafts import DocumentRef, FiscalDraft
lazy from ledger_kernel.regimes import RegimeDef
lazy from ledger_kernel.tenancy import Tenant

SIGNING_DELAY: Final = timedelta(seconds=5)
"""How long the job waits for a document the request signs (Decision 25)."""
RANGE_MISSING: Final = Objection(
    code=NoNumberAvailableError.code,
    field=None,
    message="no e-NCF of the document's type is available now: register a DGII-authorized range",
)
TIMED_OUT: Final = "timed_out"
"""``SigningReport.deferred``'s reason when the signer did not answer within the budget."""


class DocumentMissingError(EcfConflictError):
    """A document the job or a recording names is not the unit's company's: a defect of whoever named it."""

    code: ClassVar[str] = "do_ecf_document_missing"

    def __init__(self) -> None:
        super().__init__("no such e-CF document in the company and mode")


class SigningReport(Protocol, metaclass=ABCMeta):
    """Where a request that leaves signing to the job says why."""

    @abstractmethod
    def deferred(self, reason: str) -> None:
        """A document stays ``numbered`` for the job: ``reason`` is the error's code, or ``TIMED_OUT``."""

    @abstractmethod
    def failed(self, reason: str) -> None:
        """Signing a document failed in a way no retry mends, or a defect: ``reason`` is the error's code."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class IssuedDocument:
    """What ``issue`` answers: the document as it stands, and whether it carries a signature."""

    document: EcfDocument
    signed: bool


def _references(draft: FiscalDraft) -> tuple[DocumentRef, ...]:
    return tuple(reference.document for reference in draft.references)


def _stated(encf: Encf, expires_on: date | None) -> date | None:
    """The expiry an e-CF of ``encf`` states: its range's, for the types DGII dates (none for 32 and 34)."""
    return None if encf.ecf_type in UNDATED_TYPES else expires_on


def _job_time(now: datetime, *, delayed: bool) -> datetime:
    """When the advance job runs: ``SIGNING_DELAY`` after ``now`` while a request signs (Decision 25)."""
    return now + SIGNING_DELAY if delayed else now


def _next(document: EcfDocument) -> Next:
    """What the job does next for ``document``, past ``numbered``: a person if blocked, else its next step."""
    if document.state is EcfDocumentState.BLOCKED:
        return Wait(ParkReason.BLOCKED)
    return Again(document.updated_at)


@final
class Issuing:
    """The channel's issuing use cases (see the module)."""

    __slots__ = ("_archive", "_regime", "_report", "_signing", "_units")

    def __init__(
        self,
        units: NumberingUnits,
        signing: Signing,
        archive: Archive,
        report: SigningReport,
        *,
        regime: RegimeDef = REGIME,
    ) -> None:
        self._units = units
        self._signing = signing
        self._archive = archive
        self._report = report
        self._regime = regime

    async def check(self, draft: FiscalDraft, environment: Environment) -> tuple[Objection, ...]:
        """Why the channel would refuse ``draft`` in ``environment`` now; empty when it would issue it."""
        async with self._units.unit(draft.scope) as unit:
            accepted = await unit.documents.accepted(_references(draft))
            ecf_type = ecf_type_of(draft)
            missing = ecf_type is not None and not await unit.ranges.available(
                environment, ecf_type, now=self._signing.now()
            )
        found = objections(draft, accepted=accepted, regime=self._regime)
        return (*found, RANGE_MISSING) if missing else found

    async def issue(
        self, draft: FiscalDraft, environment: Environment, who: SigningAs, *, sign_within: timedelta | None
    ) -> IssuedDocument:
        """``draft``'s document, numbered once, signed within ``sign_within`` if given (see the module).

        ``DraftObjectedError`` for a draft with objections and ``NoNumberAvailableError`` when no number can
        be given: nothing is kept either way.
        """
        try:
            document, to_sign = await self._found(draft, environment, who, sign_within=sign_within)
        except DocumentExistsError:  # another request numbered it first: this unit rolled back
            document, to_sign = await self._found(draft, environment, who, sign_within=sign_within)
        if sign_within is None or to_sign is None:
            return IssuedDocument(document=document, signed=document.signature is not None)
        try:
            async with asyncio.timeout(sign_within.total_seconds()):
                signature = await self._signature(who, document, to_sign)
        except TimeoutError:
            self._report.deferred(TIMED_OUT)
            return IssuedDocument(document=document, signed=False)
        except (EcfConflictError, EcfUnavailableError) as error:
            self._report.deferred(error.code)
            return IssuedDocument(document=document, signed=False)
        except (EcfError, InvalidInputError) as error:  # the job blocks it, reporting it again
            self._report.failed(error.code)
            return IssuedDocument(document=document, signed=False)
        recorded = await self._recorded(who.tenant, document.id, Step.SIGN, signature)
        return IssuedDocument(document=recorded, signed=recorded.signature is not None)

    async def advance(self, who: SigningAs, document: EcfDocument) -> Next:
        """The job's step for ``document``, read ``numbered`` (see the module); what the job does next.

        A document another request or run moved on meanwhile is recorded as it is, and taken from there.
        """
        document_id = document.id
        async with self._units.unit(who.tenant) as unit:
            to_sign = await self._rebuilt(unit, document)
        try:
            signature = await self._signature(who, document, to_sign)
        except CertificateMissingError, CertificateNotValidError, SignerClockSkewError:
            return Wait(ParkReason.CERTIFICATE)
        except SequenceExpiredError:
            return _next(await self._recorded(who.tenant, document_id, Step.BLOCK, None))
        except InvalidInputError as error:  # the signer's refusal, or the channel's checks of what came back
            self._report.failed(error.code)
            return _next(await self._recorded(who.tenant, document_id, Step.BLOCK, None))
        return _next(await self._recorded(who.tenant, document_id, Step.SIGN, signature))

    async def document(self, tenant: Tenant, invoice: DocumentRef) -> EcfDocument | None:
        """``invoice``'s document in ``tenant``'s company and mode, as it stands; ``None`` if it has none."""
        async with self._units.unit(tenant) as unit:
            return await unit.documents.of_invoice(invoice)

    def now(self) -> datetime:
        """The channel's clock: what numbers are allocated, and released, by (``Signing.now``)."""
        return self._signing.now()

    async def total(self, unit: NumberingUnit, document: EcfDocument) -> Decimal:
        """``document``'s ``MontoTotal`` in pesos, as its e-CF states it (rebuilt from its draft)."""
        return compute_totals(await self._rebuilt(unit, document), self._regime).total

    def present(self, document: EcfDocument | None) -> EcfDocument:
        """``document``, or ``DocumentMissingError``, reported (see the module)."""
        if document is None:
            self._report.failed(DocumentMissingError.code)
            raise DocumentMissingError
        return document

    async def _found(
        self, draft: FiscalDraft, environment: Environment, who: SigningAs, *, sign_within: timedelta | None
    ) -> tuple[EcfDocument, EcfDraft | None]:
        """``draft``'s document, numbered now if it has none, and what to sign when the request signs it."""
        async with self._units.unit(who.tenant) as unit:
            document = await unit.documents.of_invoice(draft.document)
            if document is None:
                return await self._numbered(unit, draft, environment, delayed=sign_within is not None)
            if not is_final(document.state):  # a job that died or went is enqueued anew; a live one stays
                unit.advances.enqueue(document.id, at=_job_time(unit.now, delayed=sign_within is not None))
            if sign_within is not None and document.state is EcfDocumentState.NUMBERED:
                return document, await self._rebuilt(unit, document)
            return document, None

    async def _numbered(
        self, unit: NumberingUnit, draft: FiscalDraft, environment: Environment, *, delayed: bool
    ) -> tuple[EcfDocument, EcfDraft]:
        """``draft``'s document numbered and kept in ``unit``, with the e-CF it signs (see the module)."""
        accepted = await unit.documents.accepted(_references(draft))
        found = objections(draft, accepted=accepted, regime=self._regime)
        ecf_type = ecf_type_of(draft)
        if found or ecf_type is None:
            raise DraftObjectedError(found)
        allocation = await unit.ranges.allocate(environment, ecf_type, now=self._signing.now())
        ecf = ecf_draft(
            draft,
            number=allocation.encf,
            accepted=accepted,
            expires_on=_stated(allocation.encf, allocation.expires_on),
            regime=self._regime,
        )
        totals = compute_totals(ecf, self._regime)
        verify_totals(draft, totals)
        document = EcfDocument(
            id=new_id(),
            invoice=draft.document,
            environment=environment,
            encf=allocation.encf,
            range_id=allocation.range_id,
            range_expires_on=allocation.expires_on,
            state=EcfDocumentState.NUMBERED,
            route=route(ecf, totals, self._regime),
            delivery=DeliveryState.NOT_STARTED,
            issued_on=draft.issue_date,
            signature=None,
            draft=draft,
            version=1,
            created_at=unit.now,
            updated_at=unit.now,
        )
        await unit.documents.add(document)
        unit.advances.enqueue(document.id, at=_job_time(unit.now, delayed=delayed))
        unit.announcements.document_updated(document)
        return document, ecf

    async def _rebuilt(self, unit: NumberingUnit, document: EcfDocument) -> EcfDraft:
        """The e-CF ``document`` signs, from the draft it was numbered for."""
        accepted = await unit.documents.accepted(_references(document.draft))
        return ecf_draft(
            document.draft,
            number=document.encf,
            accepted=accepted,
            expires_on=_stated(document.encf, document.range_expires_on),
            regime=self._regime,
        )

    async def _signature(self, who: SigningAs, document: EcfDocument, to_sign: EcfDraft) -> Signature:
        """``to_sign`` signed as ``who`` and archived: what recording it as ``document``'s takes."""
        signed = await self._signing.sign(
            who, to_sign, environment=document.environment, expires_on=document.range_expires_on
        )
        ecf = await self._archive.ecf(who.tenant, signed.signed_ecf)
        rfce = (
            None if signed.signed_rfce is None else await self._archive.rfce(who.tenant, signed.signed_rfce)
        )
        return Signature(
            signed_at=signed.signed_at,
            security_code=signed.security_code,
            qr_url=signed.qr,
            emission_mode=EmissionMode.NORMAL,
            ecf=ecf,
            rfce=rfce,
        )

    async def _recorded(
        self, tenant: Tenant, document_id: UUID, step: Step, signature: Signature | None
    ) -> EcfDocument:
        """The document after ``step`` (signed with ``signature``, or blocked), if it is still ``numbered``.

        Its row locked first: a document another request or run moved on is kept as it is, and answered.
        """
        to = EcfDocumentState.SIGNED if step is Step.SIGN else EcfDocumentState.BLOCKED
        async with self._units.unit(tenant) as unit:
            current = self.present(await unit.documents.get(document_id, lock=True))
            if current.state is not EcfDocumentState.NUMBERED:
                return current
            moved = replace(
                current,
                state=transition(current.state, step, to),
                signature=signature,
                version=current.version + 1,
                updated_at=unit.now,
            )
            await unit.documents.changed(moved)
            unit.announcements.document_updated(moved)
        return moved
