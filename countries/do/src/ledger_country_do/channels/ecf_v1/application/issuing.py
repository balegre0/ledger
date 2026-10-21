"""Issuing an e-CF (spec §8.3 ``check`` and ``issue``, §8.5 step 4, §8.6.4 ``numbered → signed``).

``check`` writes nothing: the draft's objections (``mapping.objections``, the corrected invoice's e-CF looked
up among the company's accepted ones in the draft's mode), and ``do_ecf_range_missing`` when no number of the
draft's type could be given now in its environment (a read, no lock; Decision 6).

``issue`` gives an invoice one document (Decision 35): a second call answers the first's. The first numbers
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

``advance`` is the job's step for a ``numbered`` document (Task 19 adds submission and polling): the same
signing, then the same recording, from the stored draft. A company that cannot sign (no certificate, one not
valid then, the signer's clock) parks it (``certificate``); a range that ended before the moment of signing
blocks it (``blocked``, severity error: it needs another number); the signer or the archive unavailable is
raised, for the job to run again later. A document past ``numbered`` waits for submission (``submission``).
"""

import asyncio
from abc import ABCMeta, abstractmethod
from dataclasses import dataclass, replace
from datetime import timedelta
from enum import StrEnum
from typing import ClassVar, Final, Protocol, final
lazy from datetime import date
lazy from uuid import UUID

from ledger_country_do.channels.ecf_v1.application.documents import EcfDocument, Signature
from ledger_country_do.channels.ecf_v1.application.ports import (
    CertificateMissingError,
    CertificateNotValidError,
    SignerClockSkewError,
)
from ledger_country_do.channels.ecf_v1.application.signing import SequenceExpiredError
from ledger_country_do.channels.ecf_v1.domain.ecf import (
    DeliveryState,
    EcfDocumentState,
    EmissionMode,
    Step,
    transition,
)
from ledger_country_do.channels.ecf_v1.domain.errors import EcfError
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
from ledger_kernel.ids import new_id
lazy from ledger_country_do.channels.ecf_v1.application.ports import Archive, SigningAs
lazy from ledger_country_do.channels.ecf_v1.application.ranges import NumberingUnit, NumberingUnits
lazy from ledger_country_do.channels.ecf_v1.application.signing import Signing
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


class ParkReason(StrEnum):
    """Why the job waits, and what wakes it."""

    CERTIFICATE = "certificate"
    """The company cannot sign: a certificate installed (``Channel.resume``) wakes it."""
    BLOCKED = "blocked"
    """It needs a person: an operator."""
    SUBMISSION = "submission"
    """Signed: submission (Task 19's step) takes it on."""


class DocumentMissingError(EcfError):
    """A document the job or a recording names is not the unit's company's: a defect of whoever named it."""

    code: ClassVar[str] = "do_ecf_document_missing"

    def __init__(self) -> None:
        super().__init__("no such e-CF document in the company and mode")


class SigningReport(Protocol, metaclass=ABCMeta):
    """Where a request that leaves signing to the job says why."""

    @abstractmethod
    def deferred(self, reason: str) -> None:
        """A document stays ``numbered``: ``reason`` is the error's code, or ``TIMED_OUT``."""


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


def _present(document: EcfDocument | None) -> EcfDocument:
    if document is None:
        raise DocumentMissingError
    return document


def _waiting(document: EcfDocument) -> ParkReason:
    """Why the job waits for ``document``, past ``numbered``."""
    return ParkReason.BLOCKED if document.state is EcfDocumentState.BLOCKED else ParkReason.SUBMISSION


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
                environment, ecf_type, now=unit.now
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
        to_sign: EcfDraft | None = None
        async with self._units.unit(who.tenant) as unit:
            document = await unit.documents.of_invoice(draft.document)
            if document is None:
                document, to_sign = await self._numbered(
                    unit, draft, environment, delayed=sign_within is not None
                )
            elif sign_within is not None and document.state is EcfDocumentState.NUMBERED:
                to_sign = await self._rebuilt(unit, document)
        if sign_within is None or to_sign is None:
            return IssuedDocument(document=document, signed=document.signature is not None)
        try:
            async with asyncio.timeout(sign_within.total_seconds()):
                signature = await self._signature(who, document, to_sign)
        except TimeoutError:
            self._report.deferred(TIMED_OUT)
            return IssuedDocument(document=document, signed=False)
        except EcfError as error:
            self._report.deferred(error.code)
            return IssuedDocument(document=document, signed=False)
        recorded = await self._recorded(who.tenant, document.id, Step.SIGN, signature)
        return IssuedDocument(document=recorded, signed=recorded.signature is not None)

    async def advance(self, who: SigningAs, document_id: UUID) -> ParkReason:
        """The job's step for ``document_id`` (see the module); why the job waits next."""
        async with self._units.unit(who.tenant) as unit:
            document = _present(await unit.documents.get(document_id, lock=False))
            if document.state is not EcfDocumentState.NUMBERED:
                return _waiting(document)
            to_sign = await self._rebuilt(unit, document)
        try:
            signature = await self._signature(who, document, to_sign)
        except CertificateMissingError, CertificateNotValidError, SignerClockSkewError:
            return ParkReason.CERTIFICATE
        except SequenceExpiredError:
            return _waiting(await self._recorded(who.tenant, document_id, Step.BLOCK, None))
        return _waiting(await self._recorded(who.tenant, document_id, Step.SIGN, signature))

    async def _numbered(
        self, unit: NumberingUnit, draft: FiscalDraft, environment: Environment, *, delayed: bool
    ) -> tuple[EcfDocument, EcfDraft]:
        """``draft``'s document numbered and kept in ``unit``, with the e-CF it signs (see the module)."""
        accepted = await unit.documents.accepted(_references(draft))
        found = objections(draft, accepted=accepted, regime=self._regime)
        ecf_type = ecf_type_of(draft)
        if found or ecf_type is None:
            raise DraftObjectedError(found)
        allocation = await unit.ranges.allocate(environment, ecf_type, now=unit.now)
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
        unit.advances.enqueue(document.id, at=unit.now + SIGNING_DELAY if delayed else unit.now)
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
            current = _present(await unit.documents.get(document_id, lock=True))
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
