"""The ``do_ecf.advance`` job's steps (spec §3.6, §8.6.3, §8.6.4; Decisions 7-10, 37): one DGII call per run.

Each run reads the document in one unit, makes **at most one call** to DGII (Decision 10), and records what
came of it in another unit that locks the row and checks it is still what the run read; no transaction spans
a call. It answers what the job does next (``steps``): run again at a moment, wait for a reason, or nothing
left. By the state found:

* ``numbered``: signed and archived (``Issuing.advance``), then again at once; or ``certificate``,
  ``blocked``.
* ``signed``, unmarked: the archived bytes read back (their SHA-256 checked), the mark committed, the
  document sent **once**, DGII's answer recorded (below).
* ``signed``, marked: **never sent**. The outcome is unknown; asked about after ``SETTLE_DELAY``.
* ``submitted``, ``in_process``: ``consultaresultado``: decided, or asked again on the poll schedule.
* ``outcome_unknown`` (reception): ``consultatrackids``, a fresh, unexcluded track id adopted; else asked
  again on the schedule, and after a day (at once in CerteCF) left to a person.
* ``outcome_unknown`` (RFCE): ``consultarfce``: decided; 0 (No encontrado) as above.
* ``blocked``: a person. Decided, ``voided``: nothing left.

**Never resend** (Review Focus 1). A ``signed`` document is sent only by the run that set its mark
(``submission_started_at``), a compare-and-set committed before the call (Decision 8): two runs at once
mark it once, and a run that finds it marked never sends it, whatever happened to the run that marked it
(its lease lost, its ``timeout`` hit, its process dead, a defect escaping it): the outcome is unknown, and
nothing but DGII's track ids (``consultarfce`` for an RFCE) or a person settles it. The state machine has no
way back to ``signed`` (Decision 7). Once sent, the call ends one of three ways (``Authority``): DGII
answered (recorded), the outcome is unknown (``OutcomeUnknownError``: recorded so, settled later), or DGII
certainly did not take it (out of reach, throttled, no token): the mark cleared by the run that set it while
the document is still ``signed``, tried again after ``after_unavailable``; DGII's seed not signable (the
company's certificate): the mark cleared, waiting for one. Any other error leaves the mark for the next run.

A run that marked and was overtaken (another run found its mark and made the outcome unknown) still records
DGII's answer while the outcome is unknown: the answer is the document's own.

**Track ids** (Decision 9): adopted only when DGII received it from ``RECEIPT_TOLERANCE`` before the moment
of signing on and no earlier document with the same e-NCF holds it. Asked on the poll schedule (``polls``,
reset only when a submission gets its track id) for ``TRACK_ID_WINDOW`` from the moment the outcome first
became unknown (``track_id_since``, never reset). Every step that leaves the outcome unknown (a lookup that
adopts nothing, ``consultarfce``'s 0, a poll's 0 for an adopted track id, DGII out of reach) waits for a
person once the window is closed, so a track id DGII lists but never finds cannot loop for ever.

**A wake** (``Channel.resume``: ``OPERATOR``, or any wake that lands while the run is under way) is the job
made ready from its park; nothing of it is kept on the document. Since no step runs the job again once the
window is closed, a run that finds the outcome unknown with the window closed follows a wake (or a run that
lost a race and is run again at once): it asks DGII once, may adopt a track id, and the poll that follows
waits for a person again on a 0. A wake buys one lookup and one poll; it never reopens the window.

**Decided**: DGII's state, messages, code and ``secuenciaUtilizada``; the number used, burned, or released
(a ``released_numbers`` row, allocatable from ``release_at`` on, by the channel's clock, which numbers are
allocated by). A summary (RFCE) and a rejected document are never delivered (``not_applicable``); an
accepted e-CF's delivery is the delivery job's (Task 21).

An answer about another issuer or e-NCF is never taken as the document's (``AnswerMismatchError``): it waits
for a person, its state as it was. Every state change is published (``fiscal_channel.document.updated``,
version + 1) in the unit that records it; an answer that changes no state publishes nothing.
"""

from abc import ABCMeta, abstractmethod
from dataclasses import replace
from typing import ClassVar, Final, Literal, Protocol, TypeIs, assert_type, final
lazy from collections.abc import Awaitable, Callable
lazy from datetime import datetime
lazy from uuid import UUID

from ledger_country_do.channels.ecf_v1.application.ports import (
    AuthorityUnavailableError,
    CertificateMissingError,
    CertificateNotValidError,
    OutcomeUnknownError,
    Received,
    Refused,
    SignerClockSkewError,
    SignerUnavailableError,
)
from ledger_country_do.channels.ecf_v1.application.steps import Again, Finished, ParkReason, Wait
from ledger_country_do.channels.ecf_v1.domain.ecf import (
    DeliveryState,
    EcfDocumentState,
    Step,
    answered,
    transition,
)
from ledger_country_do.channels.ecf_v1.domain.errors import EcfError, EcfUnavailableError
from ledger_country_do.channels.ecf_v1.domain.mapping import issuer_rnc
from ledger_country_do.channels.ecf_v1.domain.numbering import release_at
from ledger_country_do.channels.ecf_v1.domain.polling import (
    SETTLE_DELAY,
    adoptable,
    after_unavailable,
    next_poll,
    window_open,
)
from ledger_country_do.channels.ecf_v1.domain.routing import Route
from ledger_country_do.channels.ecf_v1.domain.status_map import (
    DgiiCode,
    DgiiService,
    NumberFate,
    Outcome,
    UnknownDgiiCodeError,
    decide,
)
lazy from ledger_country_do.channels.ecf_v1.application.documents import EcfDocument
lazy from ledger_country_do.channels.ecf_v1.application.issuing import Issuing
lazy from ledger_country_do.channels.ecf_v1.application.ports import (
    Answer,
    Archive,
    Authority,
    Reception,
    SigningAs,
)
lazy from ledger_country_do.channels.ecf_v1.application.ranges import NumberingUnit, NumberingUnits
lazy from ledger_country_do.channels.ecf_v1.application.steps import Next
lazy from ledger_kernel.tenancy import Tenant

_CANNOT_SIGN: Final[tuple[type[EcfError], ...]] = (
    CertificateMissingError,
    CertificateNotValidError,
    SignerClockSkewError,
)
"""Why DGII's seed could not be signed for a token: the company's to mend (``certificate``)."""
_UNREACHED: Final[tuple[type[EcfError], ...]] = (AuthorityUnavailableError, SignerUnavailableError)
"""DGII (or the signer, for DGII's seed) out of reach: certainly nothing was sent."""
_ANSWERABLE: Final = frozenset({EcfDocumentState.SIGNED, EcfDocumentState.OUTCOME_UNKNOWN})
"""Where a submission's answer is still taken: as sent, or since found marked by another run (its own)."""
MARKED: Final = "marked"
"""``AdvanceReport.unknown``'s reason when a run found a submission marked and never answered."""
UNDOCUMENTED: Final = "undocumented"
"""``AdvanceReport.unknown``'s reason when DGII took a summary and answered a code it does not document."""


class SettleTooShortError(EcfError):
    """A DGII client whose attempt may outlast the settle delay: a configuration defect.

    A request still in flight when DGII is asked for track ids could land after the answer, and be missed.
    """

    code: ClassVar[str] = "do_ecf_settle_too_short"

    def __init__(self) -> None:
        super().__init__("the settle delay is shorter than the longest attempt at sending a document")


class AnswerMismatchError(EcfUnavailableError):
    """DGII answered about another issuer or e-NCF than the document asked about."""

    code: ClassVar[str] = "do_ecf_dgii_answer_mismatch"

    def __init__(self) -> None:
        super().__init__("DGII's answer names another issuer or e-NCF than the document sent")


class AdvanceReport(Protocol, metaclass=ABCMeta):
    """Where the job says what a person may have to look at."""

    @abstractmethod
    def unknown(self, reason: str) -> None:
        """A submission's outcome became unknown: ``reason`` the failure, ``MARKED`` or ``UNDOCUMENTED``."""

    @abstractmethod
    def waiting(self, reason: ParkReason) -> None:
        """A document waits for a person (``outcome_unknown``, ``answer_mismatch``)."""


def mismatched(answer: Answer, document: EcfDocument) -> bool:
    """Whether ``answer`` names another issuer or e-NCF than ``document`` (a name it omits is not checked)."""
    return (answer.rnc is not None and answer.rnc != issuer_rnc(document.draft)) or (
        answer.encf is not None and answer.encf.upper() != str(document.encf)
    )


def _settled(value: object) -> TypeIs[Next]:
    """Whether ``value`` is already what the run answers (a step that could not go on)."""
    return isinstance(value, Wait | Again | Finished)


def _refused(refusal: Refused) -> Outcome:
    """A refusal at the door: rejected, its number burned (DGII may have recorded it, F13)."""
    return decide(
        DgiiCode.REJECTED, sequence_used=None, messages=refusal.messages, service=DgiiService.RESULT
    )


@final
class Advancing:
    """The job's steps (see the module)."""

    __slots__ = ("_archive", "_authority", "_issuing", "_report", "_units")

    def __init__(
        self,
        units: NumberingUnits,
        issuing: Issuing,
        archive: Archive,
        authority: Callable[[], Authority],
        report: AdvanceReport,
    ) -> None:
        """``authority``: DGII's client, open (the worker's; a defect anywhere else)."""
        self._units = units
        self._issuing = issuing
        self._archive = archive
        self._authority = authority
        self._report = report

    async def advance(self, who: SigningAs, document_id: UUID) -> Next:
        """One run of the job for ``document_id`` (see the module)."""
        async with self._units.unit(who.tenant) as unit:
            document = self._issuing.present(await unit.documents.get(document_id, lock=False))
            looked_up = (
                document.state is EcfDocumentState.OUTCOME_UNKNOWN and document.route is Route.RECEPTION
            )
            excluded = await unit.submissions.excluded(document) if looked_up else frozenset[str]()
        step: Awaitable[Next]
        match document.state:
            case EcfDocumentState.NUMBERED:
                step = self._issuing.advance(who, document)
            case EcfDocumentState.SIGNED if document.submission_started_at is None:
                step = self._submit(who, document)
            case EcfDocumentState.SIGNED:
                self._report.unknown(MARKED)
                step = self._became_unknown(who.tenant, document.id, document.submission_started_at)
            case EcfDocumentState.SUBMITTED | EcfDocumentState.IN_PROCESS:
                step = self._poll(who, document)
            case EcfDocumentState.OUTCOME_UNKNOWN if looked_up:
                step = self._look_up(who, document, excluded)
            case EcfDocumentState.OUTCOME_UNKNOWN:
                step = self._ask_rfce(who, document)
            case EcfDocumentState.BLOCKED:
                return Wait(ParkReason.BLOCKED)
            case _:
                assert_type(
                    document.state,
                    Literal[
                        EcfDocumentState.ACCEPTED,
                        EcfDocumentState.ACCEPTED_CONDITIONALLY,
                        EcfDocumentState.REJECTED,
                        EcfDocumentState.VOIDED,
                    ],
                )
                return Finished()
        return await step

    async def wake(self, tenant: Tenant, reasons: tuple[ParkReason, ...]) -> None:
        """Take on every document of ``tenant`` waiting for one of ``reasons``, once the unit commits."""
        async with self._units.unit(tenant) as unit:
            for reason in reasons:
                unit.advances.wake(reason)

    # ── signed: the one submission ──────────────────────────────────────────────────

    async def _submit(self, who: SigningAs, document: EcfDocument) -> Next:
        """Read back, mark, send once, record (see the module)."""
        authority = self._authority()
        authority.require_ready(document.environment)
        content = await self._archive.read(document.signed().sent(document.route))
        mark, now = await self._marked(who.tenant, document.id)
        if mark is None:  # another run marked it first: the next run takes it from where it is
            return Again(now)
        rnc, environment, encf = issuer_rnc(document.draft), document.environment, document.encf
        reception: Reception | Answer | Refused
        try:
            if document.route is Route.RFCE:
                reception = await authority.submit_rfce(who, environment, rnc, encf, content)
            else:
                reception = await authority.submit_ecf(who, environment, rnc, encf, content)
        except OutcomeUnknownError as error:
            self._report.unknown(error.reason.value)
            return await self._became_unknown(who.tenant, document.id, mark)
        except _UNREACHED:
            return await self._unmarked(who.tenant, document.id, mark, waiting=None)
        except _CANNOT_SIGN:
            return await self._unmarked(who.tenant, document.id, mark, waiting=ParkReason.CERTIFICATE)
        return await self._submitted(who.tenant, document, mark, reception)

    async def _marked(self, tenant: Tenant, document_id: UUID) -> tuple[datetime | None, datetime]:
        """The mark this run set and committed (``None``: marked already, or moved on); the unit's time."""
        async with self._units.unit(tenant) as unit:
            current = self._issuing.present(await unit.documents.get(document_id, lock=True))
            if current.state is not EcfDocumentState.SIGNED or current.submission_started_at is not None:
                return None, unit.now
            await unit.documents.changed(replace(current, submission_started_at=unit.now))
            return unit.now, unit.now

    async def _unmarked(
        self, tenant: Tenant, document_id: UUID, mark: datetime, *, waiting: ParkReason | None
    ) -> Next:
        """DGII certainly did not take it: the mark this run set cleared, DGII counted as asked."""
        async with self._units.unit(tenant) as unit:
            current = self._issuing.present(await unit.documents.get(document_id, lock=True))
            if current.state is not EcfDocumentState.SIGNED or current.submission_started_at != mark:
                return Again(unit.now)
            polls = current.polls + 1
            await unit.documents.changed(replace(current, submission_started_at=None, polls=polls))
            return Wait(waiting) if waiting is not None else Again(unit.now + after_unavailable(polls))

    async def _became_unknown(self, tenant: Tenant, document_id: UUID, mark: datetime | None) -> Next:
        """Marked with ``mark``, never answered: ``outcome_unknown``, asked about once settled."""
        async with self._units.unit(tenant) as unit:
            current = self._issuing.present(await unit.documents.get(document_id, lock=True))
            if current.state is EcfDocumentState.SIGNED and current.submission_started_at == mark:
                unknown = replace(current, track_id_since=current.track_id_since or unit.now)
                await self._move(unit, unknown, Step.SUBMIT, EcfDocumentState.OUTCOME_UNKNOWN)
            return Again(unit.now + SETTLE_DELAY)

    async def _submitted(
        self, tenant: Tenant, document: EcfDocument, mark: datetime, reception: Reception | Answer | Refused
    ) -> Next:
        """DGII's answer to the submission marked ``mark``, recorded while it is the document's to take."""
        answer: Received | Outcome | Next | UnknownDgiiCodeError
        if isinstance(reception, Received):
            answer = reception
        elif isinstance(reception, Refused):
            answer = _refused(reception)
        else:
            answer = self._mapped(reception, document, DgiiService.RFCE)
            if isinstance(answer, UnknownDgiiCodeError):  # DGII took it, answered nothing it documents
                self._report.unknown(UNDOCUMENTED)
                return await self._became_unknown(tenant, document.id, mark)
        if _settled(answer):
            return answer
        sequence_used = reception.sequence_used if isinstance(reception, Answer) else None
        async with self._units.unit(tenant) as unit:
            current = self._issuing.present(await unit.documents.get(document.id, lock=True))
            if current.submission_started_at != mark or current.state not in _ANSWERABLE:
                return Again(unit.now)  # recorded by another run already
            if isinstance(answer, Outcome):
                return await self._decided(unit, current, answer, sequence_used=sequence_used)
            if current.state is EcfDocumentState.SIGNED:  # the poll schedule starts with the track id
                await self._tracked(unit, replace(current, polls=0), Step.SUBMIT, answer.track_id, None)
                return Again(unit.now + next_poll(0))
            await self._tracked(unit, current, Step.ADOPT_TRACK_ID, answer.track_id, None)
            return Again(unit.now + next_poll(current.polls))

    # ── submitted, in process: DGII's result ─────────────────────────────────────────

    async def _poll(self, who: SigningAs, document: EcfDocument) -> Next:
        authority = self._authority()
        answer = await self._asked(
            who.tenant, document, authority.result(who, document.environment, document.tracked())
        )
        if _settled(answer):
            return answer
        outcome = await self._outcome(who.tenant, document, answer, DgiiService.RESULT)
        if _settled(outcome):
            return outcome
        async with self._units.unit(who.tenant) as unit:
            current = await self._unchanged(unit, document)
            if current is None:
                return Again(unit.now)
            asked = replace(current, polls=current.polls + 1, outcome_code=int(outcome.code))
            if outcome.final:
                return await self._decided(unit, asked, outcome, sequence_used=answer.sequence_used)
            to = answered(outcome.state)
            if to is current.state:  # still in process: nothing to publish
                await unit.documents.changed(asked)
            elif to is EcfDocumentState.OUTCOME_UNKNOWN:  # not found: unknown since the first time only
                since = current.track_id_since or unit.now
                moved = await self._move(unit, replace(asked, track_id_since=since), Step.POLL, to)
                return self._still_unknown(moved, unit.now)  # a window closed: a person (Decision 7)
            else:  # in process
                await self._move(unit, asked, Step.POLL, to)
            return Again(unit.now + next_poll(asked.polls))

    # ── outcome unknown: track ids, or consultarfce ──────────────────────────────────

    async def _look_up(self, who: SigningAs, document: EcfDocument, excluded: frozenset[str]) -> Next:
        if not document.environment.lists_track_ids:
            self._report.waiting(ParkReason.OUTCOME_UNKNOWN)
            return Wait(ParkReason.OUTCOME_UNKNOWN)
        authority = self._authority()
        rnc = issuer_rnc(document.draft)
        listed = await self._asked(
            who.tenant, document, authority.track_ids(who, document.environment, rnc, document.encf)
        )
        if _settled(listed):
            return listed
        adopted = adoptable(listed, signed_at=document.signed().signed_at, excluded=excluded)
        async with self._units.unit(who.tenant) as unit:
            current = await self._unchanged(unit, document)
            if current is None:
                return Again(unit.now)
            asked = replace(current, polls=current.polls + 1)
            if adopted is None:
                await unit.documents.changed(asked)
                return self._still_unknown(asked, unit.now)
            await self._tracked(unit, asked, Step.ADOPT_TRACK_ID, adopted.track_id, adopted.received_at)
            return Again(unit.now + next_poll(asked.polls))

    async def _ask_rfce(self, who: SigningAs, document: EcfDocument) -> Next:
        authority = self._authority()
        code = document.signed().security_code
        rnc = issuer_rnc(document.draft)
        answer = await self._asked(
            who.tenant, document, authority.rfce_status(who, document.environment, rnc, document.encf, code)
        )
        if _settled(answer):
            return answer
        outcome = await self._outcome(who.tenant, document, answer, DgiiService.RFCE_QUERY)
        if _settled(outcome):
            return outcome
        async with self._units.unit(who.tenant) as unit:
            current = await self._unchanged(unit, document)
            if current is None:
                return Again(unit.now)
            asked = replace(current, polls=current.polls + 1, outcome_code=int(outcome.code))
            if outcome.final:
                return await self._decided(unit, asked, outcome, sequence_used=answer.sequence_used)
            await unit.documents.changed(asked)
            return self._still_unknown(asked, unit.now)

    # ── what DGII answered a question about the document ─────────────────────────────

    async def _asked[T](self, tenant: Tenant, document: EcfDocument, question: Awaitable[T]) -> T | Next:
        """DGII's answer to ``question``; out of reach, or no token for DGII's seed, what the run answers."""
        try:
            return await question
        except _UNREACHED:
            return await self._unreached(tenant, document)
        except _CANNOT_SIGN:
            return Wait(ParkReason.CERTIFICATE)

    def _mapped(
        self, answer: Answer, document: EcfDocument, service: DgiiService
    ) -> Outcome | Next | UnknownDgiiCodeError:
        """``answer`` mapped as ``service``'s; about another e-NCF: a person; a code DGII never documented."""
        if mismatched(answer, document):
            self._report.waiting(ParkReason.ANSWER_MISMATCH)
            return Wait(ParkReason.ANSWER_MISMATCH)
        try:
            return decide(
                answer.code, sequence_used=answer.sequence_used, messages=answer.messages, service=service
            )
        except UnknownDgiiCodeError as error:
            return error

    async def _outcome(
        self, tenant: Tenant, document: EcfDocument, answer: Answer, service: DgiiService
    ) -> Outcome | Next:
        """``answer`` mapped; a code DGII never documented is read as DGII out of reach (asked again)."""
        mapped = self._mapped(answer, document, service)
        if isinstance(mapped, UnknownDgiiCodeError):
            return await self._unreached(tenant, document)
        return mapped

    def _still_unknown(self, document: EcfDocument, now: datetime) -> Next:
        """Asked again on the schedule while the window is open; then a person (Decision 7)."""
        if window_open(document.unknown_since(), now):
            return Again(now + next_poll(document.polls))
        self._report.waiting(ParkReason.OUTCOME_UNKNOWN)
        return Wait(ParkReason.OUTCOME_UNKNOWN)

    async def _unreached(self, tenant: Tenant, document: EcfDocument) -> Next:
        """DGII could not answer (or answered nothing it documents): counted, asked again after a wait."""
        async with self._units.unit(tenant) as unit:
            current = await self._unchanged(unit, document)
            if current is None:
                return Again(unit.now)
            asked = replace(current, polls=current.polls + 1)
            await unit.documents.changed(asked)
            unknown = asked.state is EcfDocumentState.OUTCOME_UNKNOWN
            if unknown and not window_open(asked.unknown_since(), unit.now):
                self._report.waiting(ParkReason.OUTCOME_UNKNOWN)
                return Wait(ParkReason.OUTCOME_UNKNOWN)
            return Again(unit.now + after_unavailable(asked.polls))

    # ── recording ───────────────────────────────────────────────────────────────────

    async def _unchanged(self, unit: NumberingUnit, document: EcfDocument) -> EcfDocument | None:
        """``document``'s row, locked, if no other run changed its state since it was read; else ``None``."""
        current = self._issuing.present(await unit.documents.get(document.id, lock=True))
        return current if current.version == document.version else None

    async def _tracked(
        self,
        unit: NumberingUnit,
        current: EcfDocument,
        step: Step,
        track_id: str,
        received_at: datetime | None,
    ) -> None:
        """``current`` given or adopting ``track_id``: ``submitted``, the track id kept under its number.

        ``received_at``: when DGII says it received the document, when it listed the track id.
        """
        await self._move(unit, replace(current, track_id=track_id), step, EcfDocumentState.SUBMITTED)
        await unit.submissions.add(current, track_id, at=unit.now, received_at=received_at)

    async def _decided(
        self, unit: NumberingUnit, current: EcfDocument, outcome: Outcome, *, sequence_used: bool | None
    ) -> Next:
        """DGII's final ``outcome`` recorded: the state, the number's fate, a released number given back."""
        to = answered(outcome.state)
        never_delivered = current.route is Route.RFCE or to is EcfDocumentState.REJECTED
        decided = replace(
            current,
            number_fate=outcome.number,
            outcome_code=int(outcome.code),
            sequence_used=sequence_used,
            messages=outcome.messages,
            delivery=DeliveryState.NOT_APPLICABLE if never_delivered else current.delivery,
        )
        moved = await self._move(unit, decided, Step.DECIDE, to)
        if outcome.number is NumberFate.RELEASED:
            released_at = self._issuing.now()
            await unit.ranges.release(
                moved.environment,
                moved.encf,
                document=moved.id,
                released_at=released_at,
                available_at=release_at(released_at),
            )
        return Finished()

    @staticmethod
    async def _move(
        unit: NumberingUnit, changed: EcfDocument, step: Step, to: EcfDocumentState
    ) -> EcfDocument:
        """``changed`` (as read, with what the step records) moved by ``step`` to ``to``, kept and published.

        Its version is one more: what ``fiscal_channel.document.updated`` carries.
        """
        moved = replace(
            changed,
            state=transition(changed.state, step, to),
            version=changed.version + 1,
            updated_at=unit.now,
        )
        await unit.documents.changed(moved)
        unit.announcements.document_updated(moved)
        return moved
