"""A company's commercial approval of an e-CF it received, sent out (spec §8.6.5 step 6, §8.6.8; Task 23).

**The client decides** (``request``, ``POST /v1/do/received_documents/{id}/approval``): approved, or rejected
with a reason a DGII text can hold (``ApprovalReasonError``), once (``ApprovalExistsError``: a second
decision is a 409). Only the document's buyer decides: a received document is the receiving company's alone
(the unit's tenant, row-level security), its ``RNCComprador`` the company's (reception answered reason 4 to
any other), and the ACECF names it as the buyer (a document naming none, ``NotTheBuyerError``). In one unit
the decision is kept (version + 1), audited (``received_document.approve``) and the ``do_ecf.approve`` job
enqueued.

**The job** (``approve``, the worker's) takes it out:

1. the ACECF (``CommercialApproval`` written by ``render_acecf``, valid for DGII's schema) signed as the
   company (``Signatures``, reason ``do_ecf.approve``, its moment ``FechaHoraAprobacionComercial``), checked
   once signed (schema and signature), archived (``acecf``) and recorded, **once**: later runs read it back
   from the archive (its SHA-256 checked);
2. DGII (``Authority.commercial_approval``, ``aprobacioncomercial``) unless settled: the submission's mark
   (``dgii_started_at``) committed first, then sent **once**: DGII's answer kept with its messages
   (``Acknowledged``: ``sent``; ``Refused``: ``refused``, never sent again); its outcome unknown
   (``OutcomeUnknownError``, or a mark found by a later run): ``outcome_unknown``, **never sent again**
   (Review Focus 1), a person settles it; DGII certainly did not take it (out of reach, no token, the
   company's certificate): the mark cleared, tried again. Only the run that set a mark clears it, and only it
   may replace an unknown outcome another run inferred from it (its answer is DGII's); DGII's client not
   ready leaves DGII to try again, nothing marked, and the run goes on;
3. the emitter (the ``urlAceptacion`` of DGII's directory, ``Emitters``; the company's buyer-authentication
   switch, Decision 19), unless settled: taken (``sent``), refused (``refused``, never sent again), no
   receiver listed or no directory in the environment (``not_applicable``), or out of reach: tried again.

Each party's outcome is recorded in a unit of its own as soon as it is known, and audited
(``received_document.approval_sent``, the party and the outcome; every seed signed for the emitter as
``do_ecf.buyer_seed.signed``, the emitter masked); no transaction spans a call. A party left to try again is
tried on Decision 21's schedule (``domain.delivery``) for seven days from the decision, then ``failed``.
Whatever leaves a party unsent for good (refused, failed, outcome unknown) is reported (``ApprovalReport``);
nothing is dropped silently. The response shapes of both parties and the file name ``ACECF.xml`` are not
confirmed by DGII: they live behind the clients (``Authority``, ``Emitters``) and are checked on TesteCF.
"""

from abc import ABCMeta, abstractmethod
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import ClassVar, Final, Literal, Protocol, assert_type, final
lazy from collections.abc import Callable
lazy from datetime import datetime
lazy from uuid import UUID

from ledger_country_do.channels.ecf_v1.application.ports import (
    Acknowledged,
    CertificateMissingError,
    CertificateNotValidError,
    OutcomeUnknownError,
    SignerClockSkewError,
)
from ledger_country_do.channels.ecf_v1.application.received import (
    OutgoingApproval,
    PartySending,
    SignedApproval,
)
from ledger_country_do.channels.ecf_v1.application.steps import Again, Finished
from ledger_country_do.channels.ecf_v1.domain.approval import MAX_REASON, Decision, Sending
from ledger_country_do.channels.ecf_v1.domain.delivery import delivery_open, next_delivery
from ledger_country_do.channels.ecf_v1.domain.drafts import text_violation
from ledger_country_do.channels.ecf_v1.domain.errors import (
    EcfConflictError,
    EcfError,
    EcfInvalidError,
    EcfUnavailableError,
)
from ledger_country_do.channels.ecf_v1.domain.exchanges import CommercialApproval
from ledger_fiscal_kit.status import kept
from ledger_kernel.errors import NotFoundError
from ledger_kernel.tax_ids import masked
lazy from ledger_country_do.channels.ecf_v1.application.ports import (
    Archived,
    Authority,
    Refused,
    SeedSigned,
    Signatures,
    SigningAs,
)
lazy from ledger_country_do.channels.ecf_v1.application.ranges import NumberingUnit, NumberingUnits
lazy from ledger_country_do.channels.ecf_v1.application.received import ReceivedDocument
lazy from ledger_country_do.channels.ecf_v1.application.steps import Next
lazy from ledger_country_do.channels.ecf_v1.domain.approval import Decided
lazy from ledger_country_do.channels.ecf_v1.domain.environments import Environment
lazy from ledger_fiscal_kit.status import AuthorityMessage
lazy from ledger_kernel.pagination import Page, PageRequest
lazy from ledger_kernel.tenancy import Tenant

type Party = Literal["emitter", "dgii"]
"""Whom the company's ACECF goes to."""
_RETRIED: Final[tuple[type[EcfError], ...]] = (
    EcfUnavailableError,
    CertificateMissingError,
    CertificateNotValidError,
    SignerClockSkewError,
)
"""What leaves a party to try again: something out of reach, or the company's certificate (an operator's).
Anything else (a document naming no buyer, a decision or a reception that raced, a defect) fails the run."""
NO_DIRECTORY: Final = "no_directory_in_environment"
"""Why the emitter is ``not_applicable`` where DGII publishes no directory of receivers (CerteCF)."""
REFUSED: Final = "refused"
"""Why a party is ``refused``: its own words (DGII's messages) are kept beside it."""
SENT: Final = "received_document.approval_sent"
SEED_SIGNED: Final = "do_ecf.buyer_seed.signed"


class ReceivedNotFoundError(EcfError, NotFoundError):
    """No document the company received in the mode has the id asked for."""

    code: ClassVar[str] = "do_ecf_received_document_not_found"

    def __init__(self) -> None:
        super().__init__("no such received document")


class ApprovalExistsError(EcfConflictError):
    """The company decided of the document already: its decision is sent once."""

    code: ClassVar[str] = "do_ecf_approval_exists"

    def __init__(self) -> None:
        super().__init__("the received document was approved or rejected already")


class NotTheBuyerError(EcfConflictError):
    """The document names no buyer: the company is not its buyer, and approves nothing of it."""

    code: ClassVar[str] = "do_ecf_approval_not_buyer"

    def __init__(self) -> None:
        super().__init__("the received document names no buyer: only its buyer approves it")


class ReasonRule(StrEnum):
    """What a decision's reason must be, besides a text DGII's can hold."""

    MISSING = "is given when rejecting"
    ON_APPROVAL = "is given only when rejecting"


class ApprovalReasonError(EcfInvalidError):
    """A rejection without a reason DGII's text can hold, or an approval that gives one."""

    code: ClassVar[str] = "do_ecf_approval_reason_invalid"

    def __init__(self, requirement: str) -> None:
        super().__init__(f"reason {requirement}")


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class ApprovalRequest:
    """What the client decided: approved, or rejected with a reason (``ApprovalReasonError`` otherwise)."""

    decision: Decision
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.decision is Decision.APPROVED:
            if self.reason is not None:
                raise ApprovalReasonError(ReasonRule.ON_APPROVAL)
            return
        if self.reason is None:
            raise ApprovalReasonError(ReasonRule.MISSING)
        violation = text_violation(self.reason, MAX_REASON)
        if violation is not None:
            raise ApprovalReasonError(violation.requirement)


class ApprovalFormat(Protocol, metaclass=ABCMeta):
    """The ACECF, written and checked against DGII's schema (``adapters.xml``)."""

    @abstractmethod
    def acecf(self, approval: CommercialApproval) -> bytes:
        """``approval``'s ACECF, unsigned, checked as it will be once signed."""

    @abstractmethod
    def signed_acecf(self, signed: bytes) -> None:
        """Raise unless ``signed`` is valid for DGII's schema and its signature holds."""


class ApprovalArchive(Protocol, metaclass=ABCMeta):
    """The legal archive (``Archive``'s rules) for the ACECF the company signs.

    Each method raises ``ArchiveUnavailableError`` when the archive cannot be reached.
    """

    @abstractmethod
    async def approval(self, tenant: Tenant, document: bytes) -> Archived:
        """``document``, an ACECF of ``tenant``'s, kept."""

    @abstractmethod
    async def read(self, archived: Archived) -> bytes:
        """The bytes kept as ``archived``, checked against its SHA-256 (``Archive.read``)."""


@final
@dataclass(frozen=True, slots=True)
class EmitterTook:
    """The emitter took the ACECF."""

    seeds: tuple[SeedSigned, ...]


@final
@dataclass(frozen=True, slots=True)
class EmitterRefused:
    """The emitter refused the ACECF: sending it again would be refused again."""

    seeds: tuple[SeedSigned, ...]


@final
@dataclass(frozen=True, slots=True)
class NoEmitter:
    """DGII lists no receiver for the emitter (``reason``): it gets no ACECF."""

    reason: str


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class EmitterUnreached:
    """The emitter's approval service could not be used now (``code``): tried again later.

    ``needs_authentication``: it answered 401 with authentication switched off (Decision 19).
    """

    code: str
    needs_authentication: bool
    seeds: tuple[SeedSigned, ...]


type EmitterAnswer = EmitterTook | EmitterRefused | NoEmitter | EmitterUnreached


class Emitters(Protocol, metaclass=ABCMeta):
    """The approval services of DGII's directory, as a buyer sends its ACECF to an e-CF's emitter."""

    @abstractmethod
    async def send(
        self, who: SigningAs, environment: Environment, emitter_rnc: str, signed: bytes, *, authenticate: bool
    ) -> EmitterAnswer:
        """``signed``, ``who``'s ACECF, to ``emitter_rnc``'s approval service; every seed signed answered."""


class ApprovalReport(Protocol, metaclass=ABCMeta):
    """Where the job says what a person has to look at."""

    @abstractmethod
    def needs_authentication(self) -> None:
        """An emitter requires authentication, switched off for the company (Decision 19)."""

    @abstractmethod
    def unsent(self, party: Party, outcome: Sending) -> None:
        """The ACECF will not reach ``party``: refused, failed, or its outcome unknown."""


class ApprovalMissingError(EcfError):
    """The job ran for a document the company never decided of: a defect of whoever enqueued it."""

    code: ClassVar[str] = "do_ecf_approval_missing"

    def __init__(self) -> None:
        super().__init__("the received document has no commercial decision to send")


_UNSENT: Final = frozenset({Sending.REFUSED, Sending.OUTCOME_UNKNOWN, Sending.FAILED})
"""What leaves a party without the ACECF for good: reported."""


def _buyer(document: ReceivedDocument) -> str:
    """The document's buyer: the company, which the ACECF names (``NotTheBuyerError`` if it names none)."""
    if document.ecf.buyer_rnc is None:
        raise NotTheBuyerError
    return document.ecf.buyer_rnc


def _approval(document: ReceivedDocument) -> OutgoingApproval:
    """The document's approval: the job runs for a decided document (``ApprovalMissingError`` otherwise)."""
    if document.approval is None:
        raise ApprovalMissingError
    return document.approval


async def _found(unit: NumberingUnit, document: UUID, *, lock: bool) -> ReceivedDocument:
    """The unit's received document ``document`` (``ReceivedNotFoundError`` if it is not the company's)."""
    found = await unit.received.get(document, lock=lock)
    if found is None:
        raise ReceivedNotFoundError
    return found


async def _changed(
    unit: NumberingUnit, current: ReceivedDocument, approval: OutgoingApproval
) -> ReceivedDocument:
    """``current`` with ``approval``, kept at its next version."""
    changed = replace(current, approval=approval, version=current.version + 1)
    await unit.received.changed(changed)
    return changed


def _again(sending: PartySending, code: str | None) -> PartySending:
    """``sending``, its reason the run's failure (``code``) when it is still to be sent."""
    if sending.state is not Sending.PENDING or code is None:
        return sending
    return replace(sending, reason=code)


def _given_up(sending: PartySending) -> PartySending:
    """``sending`` ``failed`` with its last failure's code once the seven days are over, if still pending."""
    if sending.state is not Sending.PENDING:
        return sending
    return replace(sending, state=Sending.FAILED)


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class ApprovalSigning:
    """How the ACECF is made: written and checked (``xml``), signed (``signatures``) at ``clock``'s moment."""

    signatures: Signatures
    xml: ApprovalFormat
    clock: Callable[[], datetime]


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class ApprovalSending:
    """Where the ACECF goes: DGII (``authority``) and the emitters, their clients open; what is reported.

    The clients are the worker's (reaching them in any other role is a defect).
    """

    authority: Callable[[], Authority]
    emitters: Callable[[], Emitters]
    report: ApprovalReport


@final
class CommercialApprovals:
    """The commercial approvals the company sends (see the module)."""

    __slots__ = ("_archive", "_authority", "_clock", "_emitters", "_report", "_signatures", "_units", "_xml")

    def __init__(
        self,
        units: NumberingUnits,
        archive: ApprovalArchive,
        signing: ApprovalSigning,
        sending: ApprovalSending,
    ) -> None:
        self._units = units
        self._archive = archive
        self._signatures = signing.signatures
        self._xml = signing.xml
        self._clock = signing.clock
        self._authority = sending.authority
        self._emitters = sending.emitters
        self._report = sending.report

    # ── the client's ────────────────────────────────────────────────────────────────

    async def page(
        self,
        tenant: Tenant,
        request: PageRequest,
        *,
        environment: Environment | None = None,
        issuer: str | None = None,
        decided: Decided | None = None,
    ) -> Page[ReceivedDocument]:
        """The documents the company received in the tenant's mode, oldest first."""
        async with self._units.unit(tenant) as unit:
            return await unit.received.page(request, environment=environment, issuer=issuer, decided=decided)

    async def get(self, tenant: Tenant, document: UUID) -> ReceivedDocument:
        """A document the company received in the tenant's mode (``ReceivedNotFoundError`` otherwise)."""
        async with self._units.unit(tenant) as unit:
            return await _found(unit, document, lock=False)

    @staticmethod
    async def request(unit: NumberingUnit, document: UUID, asked: ApprovalRequest) -> ReceivedDocument:
        """The company's decision ``asked`` of ``document``, kept, audited and enqueued in ``unit``."""
        current = await _found(unit, document, lock=True)
        if current.approval is not None:
            raise ApprovalExistsError
        _ = _buyer(current)
        approval = OutgoingApproval(
            decision=asked.decision,
            reason=asked.reason,
            requested_at=unit.now,
            emitter=PartySending(state=Sending.PENDING),
            dgii=PartySending(state=Sending.PENDING),
        )
        decided = await _changed(unit, current, approval)
        after = {"decision": asked.decision.value, "reason": asked.reason}
        unit.audit.record("received_document.approve", current.id, before=None, after=after)
        unit.approvals.enqueue(current.id)
        return decided

    # ── the job's ───────────────────────────────────────────────────────────────────

    async def approve(self, who: SigningAs, document_id: UUID) -> Next:
        """One run of ``do_ecf.approve`` for ``document_id`` (see the module)."""
        async with self._units.unit(who.tenant) as unit:
            document = await _found(unit, document_id, lock=False)
            authenticate = (await unit.options.get()).buyer_authentication
        approval = _approval(document)
        if approval.settled:
            return Finished()
        try:
            signed = await self._signed(who, document)
            acecf = await self._archive.read(signed.acecf)
        except _RETRIED as error:
            return await self._later(who.tenant, document_id, error.code)
        if approval.dgii.state is Sending.PENDING:
            await self._to_dgii(who, document, acecf)
        if approval.emitter.state is Sending.PENDING:
            await self._to_emitter(who, document, acecf, authenticate=authenticate)
        return await self._later(who.tenant, document_id, None)

    async def _signed(self, who: SigningAs, document: ReceivedDocument) -> SignedApproval:
        """The document's ACECF: as recorded, or signed, checked, archived and recorded now (see the module).

        Two runs that raced each sign one; the first recorded is the document's, and both send it.
        """
        approval = _approval(document)
        if approval.signed is not None:
            return approval.signed
        at = self._clock()
        ecf = document.ecf
        unsigned = self._xml.acecf(
            CommercialApproval(
                issuer_rnc=ecf.issuer_rnc,
                buyer_rnc=_buyer(document),
                encf=ecf.encf,
                issued_on=ecf.issued_on,
                total=ecf.total,
                approved=approval.decision is Decision.APPROVED,
                reason=approval.reason,
                at=at,
            )
        )
        signed = await self._signatures.document(who, unsigned, signed_at=at, reference=str(ecf.encf))
        self._xml.signed_acecf(signed.document)
        kept_now = SignedApproval(at=at, acecf=await self._archive.approval(who.tenant, signed.document))
        async with self._units.unit(who.tenant) as unit:
            current = await _found(unit, document.id, lock=True)
            recorded = _approval(current)
            chosen = recorded.signed or kept_now
            _ = await _changed(unit, current, replace(recorded, signed=chosen))
        return chosen

    async def _to_dgii(self, who: SigningAs, document: ReceivedDocument, acecf: bytes) -> None:
        """The ACECF to DGII, once: marked first, sent, its answer recorded (see the module).

        Nothing is marked before DGII's client is ready for the environment: a mark is set only to send. A
        client not ready (out of reach, not open) leaves DGII to try again, unmarked, and the run goes on to
        the emitter.
        """
        tenant = who.tenant
        try:
            authority = self._authority()
            authority.require_ready(document.environment)
        except _RETRIED as error:
            await self._dgii(
                tenant, document.id, None, PartySending(state=Sending.PENDING, reason=error.code)
            )
            return
        mark = await self._marked(tenant, document.id)
        if mark is None:  # marked by an earlier run that recorded no answer: never sent again
            await self._dgii(tenant, document.id, None, PartySending(state=Sending.OUTCOME_UNKNOWN))
            return
        answer: Acknowledged | Refused
        try:
            answer = await authority.commercial_approval(who, document.environment, acecf)
        except OutcomeUnknownError as error:
            unknown = PartySending(state=Sending.OUTCOME_UNKNOWN, reason=error.reason.value)
            await self._dgii(tenant, document.id, mark, unknown)
            return
        except _RETRIED as error:  # DGII certainly did not take it: the mark cleared, tried again
            await self._dgii(
                tenant, document.id, mark, PartySending(state=Sending.PENDING, reason=error.code)
            )
            return
        messages = kept(tuple(message.authority() for message in answer.messages))
        if isinstance(answer, Acknowledged):
            took = PartySending(state=Sending.SENT, sent_at=self._clock())
            await self._dgii(tenant, document.id, mark, took, messages=messages)
            return
        refused = PartySending(state=Sending.REFUSED, reason=REFUSED)
        await self._dgii(tenant, document.id, mark, refused, messages=messages)

    async def _marked(self, tenant: Tenant, document_id: UUID) -> datetime | None:
        """The mark this run set and committed; ``None`` when one was set already (see the module)."""
        async with self._units.unit(tenant) as unit:
            current = await _found(unit, document_id, lock=True)
            approval = _approval(current)
            if approval.dgii_started_at is not None:
                return None
            _ = await _changed(unit, current, replace(approval, dgii_started_at=unit.now))
            return unit.now

    async def _dgii(
        self,
        tenant: Tenant,
        document_id: UUID,
        mark: datetime | None,
        outcome: PartySending,
        *,
        messages: tuple[AuthorityMessage, ...] | None = None,
    ) -> None:
        """DGII's ``outcome`` recorded, audited, reported if unsent.

        ``mark``: the one this run set and sent under (``None``: it set none). Only that run clears its mark
        (DGII certainly did not take it), and only it may replace an unknown outcome another run inferred
        from the mark meanwhile: its answer is DGII's own. Anything else another run recorded stays.
        """
        async with self._units.unit(tenant) as unit:
            current = await _found(unit, document_id, lock=True)
            approval = _approval(current)
            holds = mark is not None and approval.dgii_started_at == mark
            inferred = holds and approval.dgii.state is Sending.OUTCOME_UNKNOWN
            if approval.dgii.state.settled and not inferred:  # recorded by a run that raced this one
                return
            cleared = holds and outcome.state is Sending.PENDING
            started = None if cleared else approval.dgii_started_at
            recorded = replace(
                approval,
                dgii=outcome,
                dgii_started_at=started,
                dgii_messages=approval.dgii_messages if messages is None else messages,
            )
            _ = await _changed(unit, current, recorded)
            self._recorded(unit, current.id, "dgii", outcome)

    async def _to_emitter(
        self, who: SigningAs, document: ReceivedDocument, acecf: bytes, *, authenticate: bool
    ) -> None:
        """The ACECF to the emitter's approval service, its answer recorded (see the module)."""
        outcome = PartySending(state=Sending.NOT_APPLICABLE, reason=NO_DIRECTORY)
        seeds: tuple[SeedSigned, ...] = ()
        if document.environment.has_directory:
            answer = await self._emitters().send(
                who, document.environment, document.ecf.issuer_rnc, acecf, authenticate=authenticate
            )
            outcome, seeds = self._answered(answer)
        async with self._units.unit(who.tenant) as unit:
            for seed in seeds:  # audited whatever came of the exchange (Decision 20)
                detail = {"endpoint_host": seed.endpoint_host, "emitter": masked(seed.buyer)}
                unit.audit.record(SEED_SIGNED, document.id, before=None, after=None, detail=detail)
            current = await _found(unit, document.id, lock=True)
            approval = _approval(current)
            if approval.emitter.state.settled:  # recorded by a run that raced this one
                return
            _ = await _changed(unit, current, replace(approval, emitter=outcome))
            self._recorded(unit, current.id, "emitter", outcome)

    def _answered(self, answer: EmitterAnswer) -> tuple[PartySending, tuple[SeedSigned, ...]]:
        """The emitter's ``answer`` as the party's outcome, and the seeds signed for it."""
        match answer:
            case EmitterTook(seeds=seeds):
                return PartySending(state=Sending.SENT, sent_at=self._clock()), seeds
            case EmitterRefused(seeds=seeds):
                return PartySending(state=Sending.REFUSED, reason=REFUSED), seeds
            case NoEmitter(reason=reason):
                return PartySending(state=Sending.NOT_APPLICABLE, reason=reason), ()
            case _:
                assert_type(answer, EmitterUnreached)
                if answer.needs_authentication:
                    self._report.needs_authentication()
                return PartySending(state=Sending.PENDING, reason=answer.code), answer.seeds

    def _recorded(self, unit: NumberingUnit, document_id: UUID, party: Party, outcome: PartySending) -> None:
        """``party``'s ``outcome`` audited (the party, the outcome, why), and reported if it is unsent."""
        detail = {"party": party, "outcome": outcome.state.value, "reason": outcome.reason or ""}
        unit.audit.record(SENT, document_id, before=None, after=None, detail=detail)
        if outcome.state in _UNSENT:
            self._report.unsent(party, outcome.state)

    async def _later(self, tenant: Tenant, document_id: UUID, code: str | None) -> Next:
        """What is next once the run's calls are recorded: nothing, the same later, or ``failed`` for good.

        ``code``: why nothing could be sent this run (the ACECF could not be signed or read back), if so.
        """
        async with self._units.unit(tenant) as unit:
            current = await _found(unit, document_id, lock=True)
            approval = _approval(current)
            if approval.settled:
                return Finished()
            dgii, emitter = _again(approval.dgii, code), _again(approval.emitter, code)
            if delivery_open(approval.requested_at, unit.now):
                attempts = approval.attempts + 1
                _ = await _changed(
                    unit, current, replace(approval, attempts=attempts, dgii=dgii, emitter=emitter)
                )
                return Again(next_delivery(approval.requested_at, unit.now, attempts))
            parties: tuple[tuple[Party, PartySending], ...] = (("dgii", dgii), ("emitter", emitter))
            _ = await _changed(
                unit, current, replace(approval, dgii=_given_up(dgii), emitter=_given_up(emitter))
            )
            for party, sending in parties:
                if sending.state is Sending.PENDING:
                    self._recorded(unit, current.id, party, _given_up(sending))
            return Finished()
