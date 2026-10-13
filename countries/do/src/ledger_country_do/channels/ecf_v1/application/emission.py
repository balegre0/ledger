"""Emitting an e-CF end to end: total, route, write, sign, check, send, poll, map (spec §8.5, §8.6.3-§8.6.4).

Emission is two steps, so a caller can keep what was signed before anything leaves (v2-M3 persists it):

* ``sign(draft) -> Signed``. Totals with the regime's rates on the issue date; the route (``RFCE`` for a type
  32 under the threshold). The e-CF written and checked, signed (``FechaHoraFirma`` is the clock's moment of
  signing) and checked again; its security code is the first six characters of its signature value. On the
  RFCE route the summary is written with that code, signed and checked too. Nothing is signed unless DGII's
  side is ready (``Authority.require_ready``: hosts allowed, connections open) and the signing certificate is
  valid from the moment of signing to ``MAX_SIGNING_SKEW`` after it (``CertificateNotValidError``); nothing
  is returned whose signature came back more than ``MAX_SIGNING_SKEW`` from that moment (``SigningSkewError``:
  a queued signer, a clock that jumped). ``sign`` raises, or returns everything signed with its QR.
* ``send(signed) -> Emitted`` **never raises a pack error**: whatever stops it parks the document, ``Emitted``
  carrying the signed documents, any track id and the last answer, and ``unknown`` saying why (``Unknown``:
  the error's code, DGII's failure where there is one, and whether the document left: ``False`` certainly
  not, ``True`` DGII answered about it, ``None`` it may be at DGII). A parked document is never sent again
  here: whoever resumes it asks DGII first (track ids for an e-CF, ``consultarfce`` for an RFCE: v2-M3).

  - RFCE route: sent once to the ``fc`` host and decided at once; the full signed e-CF is kept by the caller
    (IT §9.1).
  - Reception route: sent once. If DGII may have taken it with its answer unknown, the emitter waits a settle
    delay (``SETTLE_DELAY``, at least the client's per-attempt time, so a request still in flight has landed),
    then asks DGII for the e-NCF's track ids and adopts the newest received no earlier than
    ``RECEIPT_TOLERANCE`` before the moment of signing (an older one is another, earlier document). Anything
    else (none listed yet, only older ones, CerteCF has no such service, the question fails) parks it: the
    spike never sends a document twice. Then polled on the schedule (1, 2, 5, 10, 30, 60 s) until a final
    code; after it the document is still pending with its track id (v2-M3's job continues).
  - An answer that names another issuer or e-NCF than ours is never taken as ours (``AnswerMismatchError``).
  - ``budget`` (``SEND_BUDGET``) bounds the whole send, calls, settle delay and polls together: a poll whose
    wait would pass it is not made (still pending), and a call still running when it runs out is cut short
    (parked, ``do_ecf_send_budget_spent``).
  - DGII's codes mapped by ``status_map.decide``: Aceptado Condicional is valid, its messages kept; a
    rejection with ``secuenciaUtilizada = false`` releases the number, any other burns it; a refusal at the
    door burns it too (never reuse what DGII may have recorded).

``emit(draft)`` is ``send(await sign(draft))``. The use case holds no state between documents: numbering,
persistence and retries across processes are v2-M3's (the spike's caller supplies each e-NCF).
"""

import asyncio
from dataclasses import dataclass
from datetime import timedelta
from typing import ClassVar, Final, Required, TypedDict, Unpack, final
lazy from collections.abc import Awaitable, Callable
lazy from datetime import datetime

from ledger_country_do.channels.ecf_v1.application.ports import (
    AuthorityUnavailableError,
    Failure,
    OutcomeUnknownError,
    Received,
    Refused,
)
from ledger_country_do.channels.ecf_v1.domain.errors import EcfConflictError, EcfError, EcfUnavailableError
from ledger_country_do.channels.ecf_v1.domain.printed import (
    OFFICIAL_QR,
    EcfQrData,
    RfceQrData,
    qr_url,
    rfce_qr_url,
    security_code,
)
from ledger_country_do.channels.ecf_v1.domain.routing import Route, route
from ledger_country_do.channels.ecf_v1.domain.status_map import DgiiCode, decide
from ledger_country_do.channels.ecf_v1.domain.totals import compute_totals
from ledger_country_do.regime import REGIME
lazy from ledger_country_do.channels.ecf_v1.application.ports import (
    Answer,
    Authority,
    EcfFormat,
    Reception,
    XmlSigner,
)
lazy from ledger_country_do.channels.ecf_v1.domain.drafts import EcfDraft
lazy from ledger_country_do.channels.ecf_v1.domain.environments import Environment
lazy from ledger_country_do.channels.ecf_v1.domain.printed import QrHosts
lazy from ledger_country_do.channels.ecf_v1.domain.status_map import Outcome
lazy from ledger_country_do.channels.ecf_v1.domain.totals import Totals
lazy from ledger_country_do.regime.definitions import RegimeDef

POLL_SCHEDULE: Final = (1.0, 2.0, 5.0, 10.0, 30.0, 60.0)
MAX_SIGNING_SKEW: Final = timedelta(minutes=5)
"""How far a signature may come back from its ``FechaHoraFirma``, either way."""
SETTLE_DELAY: Final = 30.0
"""Seconds waited after an unknown outcome before DGII is asked: more than ``DgiiTimeouts.total`` (20 s)."""
RECEIPT_TOLERANCE: Final = timedelta(minutes=2)
"""How long before the moment of signing DGII's reception time of our document may read (clocks differ)."""
SEND_BUDGET: Final = 300.0
"""Seconds one ``send`` may take in all: the schedule (108 s), the settle delay and the calls' own limits."""
_NOT_SENT: Final = frozenset({Failure.UNREACHABLE, Failure.THROTTLED, Failure.TIMED_OUT, Failure.NOT_OPEN})
"""A submission's failures that mean it never left (the client's ``timed_out`` there is before sending)."""

type Clock = Callable[[], datetime]
type Sleep = Callable[[float], Awaitable[None]]


class CertificateNotValidError(EcfConflictError):
    """The signing certificate is expired or not yet valid at the moment of signing: renew or wait."""

    code: ClassVar[str] = "do_ecf_certificate_not_valid"

    def __init__(self) -> None:
        super().__init__("the signing certificate is not valid at the moment of signing")


class SigningSkewError(EcfUnavailableError):
    """A signature came back too far from its stated moment of signing; signing again may succeed."""

    code: ClassVar[str] = "do_ecf_signing_skew"

    def __init__(self) -> None:
        minutes = int(MAX_SIGNING_SKEW.total_seconds() // 60)
        super().__init__(f"a signature came back more than {minutes} minutes from its moment of signing")


class AnswerMismatchError(EcfUnavailableError):
    """DGII answered about another issuer or e-NCF than the document asked about."""

    code: ClassVar[str] = "do_ecf_dgii_answer_mismatch"

    def __init__(self) -> None:
        super().__init__("DGII's answer names another issuer or e-NCF than the document sent")


SEND_BUDGET_SPENT: Final = "do_ecf_send_budget_spent"
"""``Unknown.code`` of a send cut short by its budget."""


class EmitterOptions(TypedDict, total=False):
    """``Emitter``'s keywords beyond its ports: three required, the rest with defaults.

    The environment, a clock (``FechaHoraFirma``) and a sleep (the settle delay, between polls); the poll
    schedule (``POLL_SCHEDULE``), the settle delay (``SETTLE_DELAY``), the send budget (``SEND_BUDGET``), the
    QR's hosts (``OFFICIAL_QR``) and the regime (``REGIME``).
    """

    environment: Required[Environment]
    clock: Required[Clock]
    sleep: Required[Sleep]
    schedule: tuple[float, ...]
    settle: float
    budget: float
    qr_hosts: QrHosts
    regime: RegimeDef


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Signed:
    """A document signed and checked, ready to send and to keep: what a printed representation shows too."""

    draft: EcfDraft
    totals: Totals
    route: Route
    signed_at: datetime
    signed_ecf: bytes
    signed_rfce: bytes | None
    """The signed RFCE on the RFCE route; ``None`` on the reception route."""
    security_code: str
    qr: str


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
    return decide(DgiiCode.REJECTED, sequence_used=None, messages=refusal.messages)


def _decided(answer: Answer, draft: EcfDraft, *, rfce: bool) -> Outcome:
    """``answer`` mapped, once it is about ``draft`` (an issuer or e-NCF it does not name is not checked)."""
    if (answer.rnc is not None and answer.rnc != draft.issuer.rnc) or (
        answer.encf is not None and answer.encf.upper() != str(draft.encf)
    ):
        raise AnswerMismatchError
    return decide(answer.code, sequence_used=answer.sequence_used, messages=answer.messages, rfce=rfce)


@final
class Emitter:
    """Signs, then sends, one document at a time (see the module)."""

    __slots__ = (
        "_authority",
        "_budget",
        "_clock",
        "_environment",
        "_qr_hosts",
        "_regime",
        "_schedule",
        "_settle",
        "_signer",
        "_sleep",
        "_xml",
    )

    def __init__(
        self, *, xml: EcfFormat, signer: XmlSigner, authority: Authority, **options: Unpack[EmitterOptions]
    ) -> None:
        self._xml = xml
        self._signer = signer
        self._authority = authority
        self._environment = options["environment"]
        self._clock = options["clock"]
        self._sleep = options["sleep"]
        self._schedule = options.get("schedule", POLL_SCHEDULE)
        self._settle = options.get("settle", SETTLE_DELAY)
        self._budget = options.get("budget", SEND_BUDGET)
        self._qr_hosts = options.get("qr_hosts", OFFICIAL_QR)
        self._regime = options.get("regime", REGIME)
        authority.require_ready(self._environment)

    async def emit(self, draft: EcfDraft) -> Emitted:
        """``draft`` signed, then sent (see the module)."""
        return await self.send(await self.sign(draft))

    async def sign(self, draft: EcfDraft) -> Signed:
        """``draft`` signed and checked, its RFCE too on the RFCE route (see the module)."""
        self._authority.require_ready(self._environment)
        totals = compute_totals(draft, self._regime)
        where = route(draft, totals, self._regime)
        signed_at = self._clock()
        validity = await self._signer.certificate_validity()
        if not (validity.covers(signed_at) and validity.covers(signed_at + MAX_SIGNING_SKEW)):
            raise CertificateNotValidError
        signed = await self._sign(self._xml.ecf(draft, totals, signed_at=signed_at), signed_at)
        code = security_code(self._xml.signed(draft, signed, rfce=False))
        if where is Route.RFCE:
            unsigned = self._xml.rfce(draft, totals, security_code=code)
            summary = await self._sign(unsigned, signed_at)
            self._xml.signed(draft, summary, rfce=True)
            qr = rfce_qr_url(
                RfceQrData(draft.issuer.rnc, draft.encf, totals.total, code),
                environment=self._environment,
                hosts=self._qr_hosts,
            )
            return Signed(
                draft=draft,
                totals=totals,
                route=where,
                signed_at=signed_at,
                signed_ecf=signed,
                signed_rfce=summary,
                security_code=code,
                qr=qr,
            )
        data = EcfQrData(
            issuer_rnc=draft.issuer.rnc,
            buyer_rnc=draft.buyer.rnc,
            encf=draft.encf,
            issued_on=draft.issued_on,
            total=totals.total,
            signed_at=signed_at,
            security_code=code,
        )
        return Signed(
            draft=draft,
            totals=totals,
            route=where,
            signed_at=signed_at,
            signed_ecf=signed,
            signed_rfce=None,
            security_code=code,
            qr=qr_url(data, environment=self._environment, hosts=self._qr_hosts),
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

    async def _sign(self, document: bytes, signed_at: datetime) -> bytes:
        """``document`` signed, once its signature is back within ``MAX_SIGNING_SKEW`` of ``signed_at``."""
        signed = await self._signer.sign_document(document)
        if abs(self._clock() - signed_at) > MAX_SIGNING_SKEW:
            raise SigningSkewError
        return signed

    async def _submitted[R](self, submit: Awaitable[R], progress: _Progress) -> R:
        """``submit``'s answer, ``progress.sent`` saying whether the document left (see ``Unknown.sent``)."""
        progress.sent = None
        try:
            answer = await submit
        except OutcomeUnknownError:
            raise
        except AuthorityUnavailableError as error:
            progress.sent = error.failure not in _NOT_SENT
            raise
        except EcfError:
            progress.sent = False
            raise
        progress.sent = True
        return answer

    async def _summary_sent(self, signed: Signed, summary: bytes, progress: _Progress) -> None:
        draft = signed.draft
        answer = await self._submitted(
            self._authority.submit_rfce(self._environment, draft.issuer.rnc, draft.encf, summary), progress
        )
        progress.outcome = (
            _refused(answer) if isinstance(answer, Refused) else _decided(answer, draft, rfce=True)
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
            answer = await self._authority.result(self._environment, reception.track_id)
            progress.outcome = _decided(answer, signed.draft, rfce=False)
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
            self._environment, draft.issuer.rnc, draft.encf, signed.signed_ecf
        )
        try:
            return await self._submitted(submit, progress)
        except OutcomeUnknownError as unknown:
            await self._waited(self._settle, progress)
            try:
                known = await self._authority.track_ids(self._environment, draft.issuer.rnc, draft.encf)
            except EcfError as error:
                raise unknown from error
            since = signed.signed_at - RECEIPT_TOLERANCE
            fresh = [each for each in known or () if each.received_at >= since]
            if not fresh:
                raise
            progress.sent = True
            return Received(max(fresh, key=lambda each: each.received_at).track_id)
