"""Emitting an e-CF end to end for the spike CLI: sign, send, poll, map (spec §8.5, §8.6.3-§8.6.4).

The channel itself signs in ``Channel.issue`` and its ``do_ecf.advance`` job (``signing``; Task 19's job
steps submit and poll, one call per run); this module keeps the spike's own loop (``ledger-dgii-spike``,
retired in Task 27). Emission is two steps, so a caller can keep what was signed before anything leaves:

* ``sign(draft) -> Signed``: ``signing.Signing.sign``, once DGII's side is ready
  (``Authority.require_ready``: hosts allowed, connections open), the range expiring when the draft says.
* ``send(signed) -> Emitted`` **never raises a pack error**: whatever stops it parks the document, ``Emitted``
  carrying the signed documents, any track id and the last answer, and ``unknown`` saying why (``Unknown``:
  the error's code, DGII's failure where there is one, and whether the document left: ``False`` certainly
  not, ``True`` DGII answered about it, ``None`` it may be at DGII). A parked document is never sent again
  here: whoever resumes it asks DGII first (track ids for an e-CF, ``consultarfce`` for an RFCE).
  **Binding rule:** any other exception escaping ``send`` (a defect: not a pack error) means the outcome is
  unknown: the document may be at DGII, and the caller must never send it again; it resumes it as a parked
  one, asking DGII first.

  - RFCE route: sent once to the ``fc`` host and decided at once; the full signed e-CF is kept by the caller
    (IT §9.1).
  - Reception route: sent once. If DGII may have taken it with its answer unknown, the emitter waits a settle
    delay (``SETTLE_DELAY``, at least the authority's ``attempt_limit``, so a request still in flight has
    landed: an emitter whose settle delay is shorter is never built, ``SettleTooShortError``),
    then asks DGII for the e-NCF's track ids and adopts the newest received no earlier than
    ``RECEIPT_TOLERANCE`` before the moment of signing (an older one is another, earlier document). Anything
    else (none listed yet, only older ones, CerteCF has no such service, the question fails) parks it: the
    spike never sends a document twice. Then polled on the schedule (1, 2, 5, 10, 30, 60 s) until a final
    code; after it the document is still pending with its track id.
  - An answer that names another issuer or e-NCF than ours is never taken as ours (``AnswerMismatchError``).
  - ``budget`` (``SEND_BUDGET``) bounds the whole send, calls, settle delay and polls together: a poll whose
    wait would pass it is not made (still pending), and a call still running when it runs out is cut short
    (parked, ``do_ecf_send_budget_spent``).
  - DGII's codes mapped by ``status_map.decide``: Aceptado Condicional is valid, its messages kept; a
    rejection with ``secuenciaUtilizada = false`` releases the number, any other burns it; a refusal at the
    door burns it too (never reuse what DGII may have recorded).

``emit(draft)`` is ``send(await sign(draft))``. The use case holds no state between documents: the spike's
caller supplies each e-NCF.
"""

import asyncio
from dataclasses import dataclass
from typing import ClassVar, Final, Required, TypedDict, Unpack, final
lazy from collections.abc import Awaitable

from ledger_country_do.channels.ecf_v1.application.ports import (
    AuthorityUnavailableError,
    Failure,
    OutcomeUnknownError,
    Received,
    Refused,
)
from ledger_country_do.channels.ecf_v1.application.signing import Signing
from ledger_country_do.channels.ecf_v1.domain import polling
from ledger_country_do.channels.ecf_v1.domain.errors import EcfError, EcfUnavailableError
from ledger_country_do.channels.ecf_v1.domain.printed import OFFICIAL_QR
from ledger_country_do.channels.ecf_v1.domain.status_map import DgiiCode, DgiiService, decide
from ledger_country_do.regime import REGIME
lazy from ledger_country_do.channels.ecf_v1.application.ports import (
    Answer,
    Authority,
    EcfFormat,
    Reception,
    Signatures,
    SigningAs,
)
lazy from ledger_country_do.channels.ecf_v1.application.signing import Signed
lazy from ledger_country_do.channels.ecf_v1.domain.drafts import EcfDraft
lazy from ledger_country_do.channels.ecf_v1.domain.environments import Environment
lazy from ledger_country_do.channels.ecf_v1.domain.printed import QrHosts
lazy from ledger_country_do.channels.ecf_v1.domain.status_map import Outcome
lazy from ledger_kernel.clock import Clock, Sleep
lazy from ledger_kernel.regimes import RegimeDef

POLL_SCHEDULE: Final = tuple(float(seconds) for seconds in polling.POLL_SCHEDULE)
SETTLE_DELAY: Final = polling.SETTLE_DELAY.total_seconds()
"""Seconds waited after an unknown outcome before DGII is asked: at least ``Authority.attempt_limit`` (20 s by
default, ``DgiiTimeouts.total``), which ``Emitter`` checks when it is built."""
SEND_BUDGET: Final = 300.0
"""Seconds one ``send`` may take in all: the schedule (108 s), the settle delay and the calls' own limits."""


class SettleTooShortError(EcfError):
    """An emitter that would ask DGII before its authority's longest attempt is over: a configuration defect.

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


SEND_BUDGET_SPENT: Final = "do_ecf_send_budget_spent"
"""``Unknown.code`` of a send cut short by its budget."""


class EmitterOptions(TypedDict, total=False):
    """``Emitter``'s keywords beyond its ports: four required, the rest with defaults.

    Whom it emits for (``who``: the issuing company, its signatures and DGII's token), the environment, a
    clock (``FechaHoraFirma``) and a sleep (the settle delay, between polls); the poll
    schedule (``POLL_SCHEDULE``), the settle delay (``SETTLE_DELAY``), the send budget (``SEND_BUDGET``) and
    the regime (``REGIME``). The QR's hosts are ``Emitter``'s own keyword (``OFFICIAL_QR`` by default).
    """

    who: Required[SigningAs]
    environment: Required[Environment]
    clock: Required[Clock]
    sleep: Required[Sleep]
    schedule: tuple[float, ...]
    settle: float
    budget: float
    regime: RegimeDef


@final
@dataclass(frozen=True, slots=True)
class Unknown:
    """Why a signed document was parked (see the module)."""

    code: str
    failure: Failure | None
    sent: bool | None
    """``False``: it certainly never left; ``True``: DGII answered about it; ``None``: it may be at DGII."""

    @classmethod
    def of(cls, error: EcfError, *, sent: bool | None) -> Unknown:
        """What ``error`` says of why the document was parked."""
        if isinstance(error, OutcomeUnknownError):
            return cls(error.code, error.reason, sent)
        if isinstance(error, AuthorityUnavailableError):
            return cls(error.code, error.failure, sent)
        return cls(error.code, None, sent)


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Emitted:
    """What sending a signed document produced: DGII's mapped answer, or why it was parked."""

    signed: Signed
    track_id: str | None
    outcome: Outcome | None
    """``None`` if DGII was never asked or never answered; a non-final outcome when still pending."""
    unknown: Unknown | None = None
    """Why the document was parked (see the module); ``None`` if sending ran its course."""

    @property
    def pending(self) -> bool:
        """Whether DGII's decision is not known yet: not final, never answered, or parked."""
        return self.outcome is None or not self.outcome.final


@dataclass(slots=True)
class _Progress:
    """How far a send got: where it stops, the ``Emitted`` is built from it."""

    begun: float
    """When the send began, on the event loop's clock."""
    slept: float = 0.0
    """How long the injected sleep was asked to wait (a test's sleep returns at once)."""
    sent: bool | None = False
    track_id: str | None = None
    outcome: Outcome | None = None


def _refused(refusal: Refused) -> Outcome:
    return decide(
        DgiiCode.REJECTED, sequence_used=None, messages=refusal.messages, service=DgiiService.RESULT
    )


def _decided(answer: Answer, draft: EcfDraft, *, service: DgiiService) -> Outcome:
    """``answer`` mapped, once it is about ``draft`` (an issuer or e-NCF it does not name is not checked)."""
    if (answer.rnc is not None and answer.rnc != draft.issuer.rnc) or (
        answer.encf is not None and answer.encf.upper() != str(draft.encf)
    ):
        raise AnswerMismatchError
    return decide(answer.code, sequence_used=answer.sequence_used, messages=answer.messages, service=service)


@final
class Emitter:
    """Signs, then sends, one document at a time (see the module)."""

    __slots__ = (
        "_authority",
        "_budget",
        "_environment",
        "_schedule",
        "_settle",
        "_signing",
        "_sleep",
        "_who",
    )

    def __init__(
        self,
        *,
        xml: EcfFormat,
        signer: Signatures,
        authority: Authority,
        qr_hosts: QrHosts = OFFICIAL_QR,
        **options: Unpack[EmitterOptions],
    ) -> None:
        self._signing = Signing(
            xml, signer, clock=options["clock"], qr_hosts=qr_hosts, regime=options.get("regime", REGIME)
        )
        self._authority = authority
        self._who = options["who"]
        self._environment = options["environment"]
        self._sleep = options["sleep"]
        self._schedule = options.get("schedule", POLL_SCHEDULE)
        self._settle = options.get("settle", SETTLE_DELAY)
        self._budget = options.get("budget", SEND_BUDGET)
        if self._settle < authority.attempt_limit():
            raise SettleTooShortError
        authority.require_ready(self._environment)

    async def emit(self, draft: EcfDraft) -> Emitted:
        """``draft`` signed, then sent (see the module)."""
        return await self.send(await self.sign(draft))

    async def sign(self, draft: EcfDraft) -> Signed:
        """``draft`` signed and checked, its RFCE too on the RFCE route (see the module)."""
        self._authority.require_ready(self._environment)
        return await self._signing.sign(
            self._who, draft, environment=self._environment, expires_on=draft.sequence_expires_on
        )

    async def send(self, signed: Signed) -> Emitted:
        """``signed`` sent and decided, or parked; never raises a pack error (see the module)."""
        progress = _Progress(begun=asyncio.get_running_loop().time())
        unknown: Unknown | None = None
        try:
            async with asyncio.timeout(self._budget):
                if signed.signed_rfce is not None:
                    await self._summary_sent(signed, signed.signed_rfce, progress)
                else:
                    await self._ecf_sent(signed, progress)
        except TimeoutError:
            unknown = Unknown(SEND_BUDGET_SPENT, Failure.TIMED_OUT, progress.sent)
        except EcfError as error:
            unknown = Unknown.of(error, sent=progress.sent)
        return Emitted(signed=signed, track_id=progress.track_id, outcome=progress.outcome, unknown=unknown)

    async def _submitted[R](self, submit: Awaitable[R], progress: _Progress) -> R:
        """``submit``'s answer, ``progress.sent`` saying whether the document left (see ``Unknown.sent``).

        The ``Authority``'s definition (the DGII client's module): an answer means DGII answered about it;
        ``OutcomeUnknownError`` that it may be at DGII; any other error that it certainly never left.
        """
        progress.sent = None
        try:
            answer = await submit
        except OutcomeUnknownError:
            raise
        except EcfError:
            progress.sent = False
            raise
        progress.sent = True
        return answer

    async def _summary_sent(self, signed: Signed, summary: bytes, progress: _Progress) -> None:
        draft = signed.draft
        answer = await self._submitted(
            self._authority.submit_rfce(self._who, self._environment, draft.issuer.rnc, draft.encf, summary),
            progress,
        )
        progress.outcome = (
            _refused(answer)
            if isinstance(answer, Refused)
            else _decided(answer, draft, service=DgiiService.RFCE)
        )

    async def _ecf_sent(self, signed: Signed, progress: _Progress) -> None:
        reception = await self._reception(signed, progress)
        if isinstance(reception, Refused):
            progress.outcome = _refused(reception)
            return
        progress.track_id = reception.track_id
        for delay in self._schedule:
            if self._spent(progress) + delay > self._budget:
                return
            await self._waited(delay, progress)
            answer = await self._authority.result(self._who, self._environment, reception.track_id)
            progress.outcome = _decided(answer, signed.draft, service=DgiiService.RESULT)
            if progress.outcome.final:
                return

    @staticmethod
    def _spent(progress: _Progress) -> float:
        """The budget spent: the time passed, or the waits asked for when the sleep returns at once."""
        return max(asyncio.get_running_loop().time() - progress.begun, progress.slept)

    async def _waited(self, seconds: float, progress: _Progress) -> None:
        await self._sleep(seconds)
        progress.slept += seconds

    async def _reception(self, signed: Signed, progress: _Progress) -> Reception:
        """Sent once; after an unknown outcome, a fresh track id DGII lists is adopted, or it is parked."""
        draft = signed.draft
        submit = self._authority.submit_ecf(
            self._who, self._environment, draft.issuer.rnc, draft.encf, signed.signed_ecf
        )
        try:
            return await self._submitted(submit, progress)
        except OutcomeUnknownError as unknown:
            await self._waited(self._settle, progress)
            try:
                known = await self._authority.track_ids(
                    self._who, self._environment, draft.issuer.rnc, draft.encf
                )
            except EcfError as error:
                raise unknown from error
            adopted = polling.adoptable(known, signed_at=signed.signed_at, excluded=frozenset())
            if adopted is None:
                raise
            progress.sent = True
            return Received(adopted.track_id)
