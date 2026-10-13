import asyncio
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Final, final, override

import pytest

from ledger_country_do.channels.ecf_v1.adapters.dgii.endpoints import LiveHostRefusedError
from ledger_country_do.channels.ecf_v1.adapters.xml.documents import DgiiXml
from ledger_country_do.channels.ecf_v1.application.emission import (
    MAX_SIGNING_SKEW,
    RECEIPT_TOLERANCE,
    SETTLE_DELAY,
    CertificateNotValidError,
    Emitter,
    SettleTooShortError,
    SigningSkewError,
    Unknown,
)
from ledger_country_do.channels.ecf_v1.application.ports import (
    Answer,
    Authority,
    AuthorityUnavailableError,
    CertificateValidity,
    Failure,
    OutcomeUnknownError,
    Received,
    Refused,
    TrackId,
    XmlSigner,
)
from ledger_country_do.channels.ecf_v1.domain.drafts import BillingIndicator, Line
from ledger_country_do.channels.ecf_v1.domain.environments import Environment
from ledger_country_do.channels.ecf_v1.domain.errors import EcfConflictError, EcfError
from ledger_country_do.channels.ecf_v1.domain.routing import Route
from ledger_country_do.channels.ecf_v1.domain.status_map import DgiiMessage, EcfState, NumberFate
from ledger_country_do.regime import REGIME
from ledger_country_do.regime.rounding import ROUNDING
from ledger_country_do_testing.clock import ManualClock
from ledger_country_do_testing.samples import consumer_invoice, credit_invoice
from ledger_kernel.money import RoundingMode
lazy from ledger_country_do.channels.ecf_v1.application.emission import Signed
lazy from ledger_country_do.channels.ecf_v1.application.ports import Reception
lazy from ledger_country_do.channels.ecf_v1.domain.encf import Encf
lazy from ledger_country_do_testing.local_signer import KeyPairSigner

NOW: Final = datetime(2026, 10, 13, 13, 30, tzinfo=UTC)
INSTANT: Final = timedelta(0)
ISSUER: Final = "101850043"
OUTCOME_UNKNOWN: Final = "do_ecf_dgii_outcome_unknown"
UNAVAILABLE: Final = "do_ecf_dgii_unavailable"
MISMATCH: Final = "do_ecf_dgii_answer_mismatch"
BUDGET: Final = "do_ecf_send_budget_spent"
REFUSAL: Final = Refused((DgiiMessage("400", "Tipo de archivo no válido"),))


@final
@dataclass
class ScriptedAuthority(Authority):
    """DGII as a test scripts it; ``events`` records each call (and the emitter's sleeps, see ``emitter``)."""

    submissions: list[Reception | EcfError] = field(default_factory=list)
    summaries: list[Answer | Refused | EcfError] = field(default_factory=lambda: [REFUSAL])
    answers: list[Answer | EcfError] = field(default_factory=list)
    known: tuple[TrackId, ...] | EcfError | None = ()
    not_ready: EcfError | None = None
    attempt: float = 20.0
    stall_submission: float = 0.0
    stall_result: float = 0.0
    events: list[str] = field(default_factory=list)

    def sent(self, call: str) -> int:
        return self.events.count(call)

    @override
    def require_ready(self, environment: Environment) -> None:
        if self.not_ready is not None:
            raise self.not_ready

    @override
    def attempt_limit(self) -> float:
        return self.attempt

    @override
    async def submit_ecf(
        self, environment: Environment, issuer_rnc: str, encf: Encf, signed: bytes
    ) -> Reception:
        self.events.append("submit_ecf")
        await asyncio.sleep(self.stall_submission)
        found = self.submissions.pop(0)
        if isinstance(found, EcfError):
            raise found
        return found

    @override
    async def submit_rfce(
        self, environment: Environment, issuer_rnc: str, encf: Encf, signed: bytes
    ) -> Answer | Refused:
        self.events.append("submit_rfce")
        found = self.summaries.pop(0)
        if isinstance(found, EcfError):
            raise found
        return found

    @override
    async def result(self, environment: Environment, track_id: str) -> Answer:
        self.events.append(f"result {track_id}")
        await asyncio.sleep(self.stall_result)
        found = self.answers.pop(0)
        if isinstance(found, EcfError):
            raise found
        return found

    @override
    async def track_ids(
        self, environment: Environment, issuer_rnc: str, encf: Encf
    ) -> tuple[TrackId, ...] | None:
        self.events.append("track_ids")
        if isinstance(self.known, EcfError):
            raise self.known
        return self.known


@final
class WatchedSigner(XmlSigner):
    """A real signer whose signing is counted, may take (clock) time, and whose validity may be scripted."""

    __slots__ = ("_clock", "_inner", "_late_from", "_takes", "_validity", "signed")

    def __init__(
        self,
        inner: KeyPairSigner,
        clock: ManualClock,
        *,
        takes: timedelta = INSTANT,
        late_from: int = 1,
        validity: CertificateValidity | None = None,
    ) -> None:
        self._inner = inner
        self._clock = clock
        self._takes = takes
        self._late_from = late_from
        self._validity = validity
        self.signed = 0

    @override
    async def certificate_validity(self) -> CertificateValidity:
        return self._validity or await self._inner.certificate_validity()

    @override
    async def sign_document(self, document: bytes) -> bytes:
        self.signed += 1
        if self.signed >= self._late_from:
            self._clock.advance(self._takes)
        return await self._inner.sign_document(document)

    @override
    async def sign_seed(self, seed: bytes) -> bytes:
        return await self._inner.sign_seed(seed)


def emitter(
    authority: ScriptedAuthority,
    signer: XmlSigner,
    schedule: tuple[float, ...] = (1.0, 2.0),
    clock: ManualClock | None = None,
    budget: float | None = None,
) -> Emitter:
    async def sleep(seconds: float) -> None:
        authority.events.append(f"sleep {seconds}")

    if budget is None:
        return Emitter(
            xml=DgiiXml(),
            signer=signer,
            authority=authority,
            environment=Environment.TESTECF,
            clock=clock or (lambda: NOW),
            sleep=sleep,
            schedule=schedule,
        )
    return Emitter(
        xml=DgiiXml(),
        signer=signer,
        authority=authority,
        environment=Environment.TESTECF,
        clock=clock or (lambda: NOW),
        sleep=sleep,
        schedule=schedule,
        budget=budget,
    )


IN_PROCESS = Answer(code=3, messages=(), sequence_used=None)
ACCEPTED = Answer(code=1, messages=(), sequence_used=True)
FRESH = TrackId("fresh", NOW + timedelta(seconds=2))
STALE = TrackId("stale", NOW - RECEIPT_TOLERANCE - timedelta(seconds=1))


def broken() -> OutcomeUnknownError:
    return OutcomeUnknownError("reception", Failure.BROKEN)


async def test_a_track_id_dgii_lists_after_the_settle_delay_is_adopted_never_resent(
    signer: KeyPairSigner,
) -> None:
    authority = ScriptedAuthority(submissions=[broken()], answers=[ACCEPTED], known=(STALE, FRESH))
    emitted = await emitter(authority, signer).emit(credit_invoice())
    assert authority.events == [
        "submit_ecf",
        f"sleep {SETTLE_DELAY}",
        "track_ids",
        "sleep 1.0",
        "result fresh",
    ]
    assert (emitted.track_id, emitted.pending, emitted.unknown) == ("fresh", False, None)


async def test_the_newest_fresh_track_id_is_adopted_from_the_tolerance_on(signer: KeyPairSigner) -> None:
    edge = TrackId("edge", NOW - RECEIPT_TOLERANCE)
    newest = TrackId("newest", NOW + timedelta(seconds=5))
    for known, adopted in (((edge,), "edge"), ((newest, edge, FRESH), "newest")):
        authority = ScriptedAuthority(submissions=[broken()], answers=[ACCEPTED], known=known)
        emitted = await emitter(authority, signer).emit(credit_invoice())
        assert (emitted.track_id, authority.sent("submit_ecf")) == (adopted, 1)


@pytest.mark.parametrize(
    "known",
    [(), (STALE,), None, AuthorityUnavailableError(Failure.SERVER_ERROR, "track_ids"), EcfConflictError("x")],
    ids=["listed-late", "stale-only", "certecf", "outage", "any-error"],
)
async def test_an_unknown_outcome_dgii_does_not_resolve_is_parked_never_resent(
    signer: KeyPairSigner, known: tuple[TrackId, ...] | EcfError | None
) -> None:
    authority = ScriptedAuthority(submissions=[broken()], known=known)
    emitted = await emitter(authority, signer).emit(credit_invoice())
    assert authority.events == ["submit_ecf", f"sleep {SETTLE_DELAY}", "track_ids"]
    assert (emitted.unknown, emitted.track_id, emitted.outcome, emitted.pending) == (
        Unknown(OUTCOME_UNKNOWN, Failure.BROKEN, sent=None),
        None,
        None,
        True,
    )
    assert emitted.signed.signed_ecf.startswith(b"<?xml")


@pytest.mark.parametrize("failure", list(Failure))
async def test_a_submission_dgii_certainly_did_not_take_is_parked_as_never_sent(
    signer: KeyPairSigner, failure: Failure
) -> None:
    # ``Authority.submit_ecf``: ``AuthorityUnavailableError`` only when DGII certainly did not take it.
    authority = ScriptedAuthority(submissions=[AuthorityUnavailableError(failure, "reception")])
    emitted = await emitter(authority, signer).emit(credit_invoice())
    assert (emitted.unknown, authority.events) == (Unknown(UNAVAILABLE, failure, sent=False), ["submit_ecf"])


async def test_a_configuration_refused_at_submission_is_parked_as_never_sent(signer: KeyPairSigner) -> None:
    authority = ScriptedAuthority(submissions=[LiveHostRefusedError()])
    emitted = await emitter(authority, signer).emit(credit_invoice())
    assert emitted.unknown == Unknown("do_ecf_live_host_refused", None, sent=False)


async def test_an_authority_not_ready_fails_before_anything_is_signed(signer: KeyPairSigner) -> None:
    closed = AuthorityUnavailableError(Failure.NOT_OPEN, "reception")
    with pytest.raises(AuthorityUnavailableError, match="not_open"):
        emitter(ScriptedAuthority(not_ready=closed), signer)
    clock = ManualClock(NOW)
    authority = ScriptedAuthority()
    watched = WatchedSigner(signer, clock)
    built = emitter(authority, watched, clock=clock)
    authority.not_ready = LiveHostRefusedError()
    with pytest.raises(LiveHostRefusedError):
        await built.sign(credit_invoice())
    assert (watched.signed, authority.events) == (0, [])


async def test_signing_asks_dgii_nothing_and_what_is_signed_is_sent_later(signer: KeyPairSigner) -> None:
    authority = ScriptedAuthority(submissions=[Received("t")], answers=[ACCEPTED])
    built = emitter(authority, signer)
    signed: Signed = await built.sign(credit_invoice())
    assert authority.events == []
    assert (signed.route, signed.signed_rfce, signed.signed_at) == (Route.RECEPTION, None, NOW)
    assert signed.qr.startswith("https://ecf.dgii.gov.do/testecf/consultatimbre?RncEmisor=101850043")
    emitted = await built.send(signed)
    assert (emitted.signed, emitted.track_id, emitted.pending) == (signed, "t", False)
    summary = await built.sign(consumer_invoice())
    assert (summary.route, authority.events) == (Route.RFCE, ["submit_ecf", "sleep 1.0", "result t"])
    assert summary.signed_rfce is not None


async def test_after_the_schedule_a_document_still_in_process_is_pending(signer: KeyPairSigner) -> None:
    authority = ScriptedAuthority(submissions=[Received("t")], answers=[IN_PROCESS, IN_PROCESS])
    emitted = await emitter(authority, signer).emit(credit_invoice())
    assert (emitted.pending, emitted.unknown) == (True, None)
    assert emitted.outcome is not None
    assert emitted.outcome.state is EcfState.IN_PROCESS
    nothing = await emitter(ScriptedAuthority(submissions=[Received("t")]), signer, schedule=()).emit(
        credit_invoice()
    )
    assert (nothing.outcome, nothing.pending) == (None, True)


async def test_an_outage_while_polling_parks_the_received_document_with_its_track_id(
    signer: KeyPairSigner,
) -> None:
    outage = AuthorityUnavailableError(Failure.UNREACHABLE, "result")
    authority = ScriptedAuthority(submissions=[Received("t")], answers=[IN_PROCESS, outage])
    emitted = await emitter(authority, signer).emit(credit_invoice())
    assert (emitted.track_id, emitted.pending, emitted.unknown) == (
        "t",
        True,
        Unknown(UNAVAILABLE, Failure.UNREACHABLE, sent=True),
    )
    assert emitted.outcome is not None
    assert emitted.outcome.state is EcfState.IN_PROCESS


async def test_a_code_dgii_never_documented_while_polling_is_not_guessed_and_keeps_the_track_id(
    signer: KeyPairSigner,
) -> None:
    authority = ScriptedAuthority(submissions=[Received("t")], answers=[Answer(9, (), None)])
    emitted = await emitter(authority, signer).emit(credit_invoice())
    assert (emitted.track_id, emitted.outcome, emitted.unknown) == (
        "t",
        None,
        Unknown("do_ecf_dgii_code_unknown", None, sent=True),
    )


@pytest.mark.parametrize(
    "answer",
    [
        Answer(1, (), sequence_used=True, rnc="131098193", encf="E310000000001"),
        Answer(1, (), sequence_used=True, rnc=ISSUER, encf="E310000000002"),
        Answer(1, (), sequence_used=True, encf="E320000000001"),
    ],
    ids=["other-rnc", "other-e-ncf", "other-type"],
)
async def test_a_result_about_another_document_is_parked_never_taken_as_ours(
    signer: KeyPairSigner, answer: Answer
) -> None:
    authority = ScriptedAuthority(submissions=[Received("t")], answers=[answer])
    emitted = await emitter(authority, signer).emit(credit_invoice())
    assert (emitted.outcome, emitted.unknown) == (None, Unknown(MISMATCH, None, sent=True))


async def test_a_result_naming_our_document_in_either_case_is_ours(signer: KeyPairSigner) -> None:
    answer = Answer(1, (), sequence_used=True, rnc=ISSUER, encf="e310000000001")
    authority = ScriptedAuthority(submissions=[Received("t")], answers=[answer])
    emitted = await emitter(authority, signer).emit(credit_invoice())
    assert (emitted.pending, emitted.unknown) == (False, None)


async def test_a_refusal_at_the_door_burns_the_number(signer: KeyPairSigner) -> None:
    refused = Refused((DgiiMessage("400", "x"),))
    emitted = await emitter(ScriptedAuthority(submissions=[refused]), signer).emit(credit_invoice())
    assert emitted.outcome is not None
    assert (emitted.outcome.state, emitted.outcome.number, emitted.track_id) == (
        EcfState.REJECTED,
        NumberFate.BURNED,
        None,
    )
    summary = await emitter(ScriptedAuthority(), signer).emit(consumer_invoice())
    assert summary.outcome is not None
    assert summary.outcome.number is NumberFate.BURNED


async def test_an_rfce_whose_outcome_is_unknown_is_parked_never_sent_again(signer: KeyPairSigner) -> None:
    authority = ScriptedAuthority(summaries=[OutcomeUnknownError("rfce", Failure.BROKEN)])
    emitted = await emitter(authority, signer).emit(consumer_invoice())
    assert (emitted.signed.route, authority.events) == (Route.RFCE, ["submit_rfce"])
    assert (emitted.unknown, emitted.outcome, emitted.pending, emitted.track_id) == (
        Unknown(OUTCOME_UNKNOWN, Failure.BROKEN, sent=None),
        None,
        True,
        None,
    )
    assert emitted.signed.signed_rfce is not None


async def test_an_rfce_answered_with_a_code_dgii_never_documented_is_parked(signer: KeyPairSigner) -> None:
    authority = ScriptedAuthority(summaries=[Answer(code=3, messages=(), sequence_used=None)])
    emitted = await emitter(authority, signer).emit(consumer_invoice())
    assert (emitted.unknown, emitted.outcome, emitted.pending) == (
        Unknown("do_ecf_dgii_code_unknown", None, sent=True),
        None,
        True,
    )


async def test_an_rfce_answer_about_another_e_ncf_is_parked(signer: KeyPairSigner) -> None:
    authority = ScriptedAuthority(summaries=[Answer(1, (), sequence_used=True, encf="E320000000002")])
    emitted = await emitter(authority, signer).emit(consumer_invoice())
    assert (emitted.outcome, emitted.unknown) == (None, Unknown(MISMATCH, None, sent=True))
    accepted = ScriptedAuthority(
        summaries=[Answer(1, (), sequence_used=True, rnc=ISSUER, encf="E320000000001")]
    )
    assert not (await emitter(accepted, signer).emit(consumer_invoice())).pending


async def test_the_send_budget_bounds_polls_and_calls(signer: KeyPairSigner) -> None:
    planned = ScriptedAuthority(submissions=[Received("t")], answers=[IN_PROCESS, ACCEPTED])
    within = await emitter(planned, signer, schedule=(1.0, 2.0), budget=2.5).emit(credit_invoice())
    assert (planned.events, within.pending, within.unknown) == (
        ["submit_ecf", "sleep 1.0", "result t"],
        True,
        None,
    )
    sending = ScriptedAuthority(submissions=[Received("t")], stall_submission=0.5)
    cut = await emitter(sending, signer, budget=0.2).emit(credit_invoice())
    assert (cut.unknown, cut.track_id) == (Unknown(BUDGET, Failure.TIMED_OUT, sent=None), None)
    polling = ScriptedAuthority(submissions=[Received("t")], answers=[ACCEPTED], stall_result=0.5)
    stopped = await emitter(polling, signer, schedule=(0.1,), budget=0.2).emit(credit_invoice())
    assert (stopped.unknown, stopped.track_id) == (Unknown(BUDGET, Failure.TIMED_OUT, sent=True), "t")


async def test_a_certificate_must_cover_the_moment_of_signing_and_the_skew_after_it(
    signer: KeyPairSigner,
) -> None:
    refused = (
        CertificateValidity(NOW - timedelta(days=400), NOW - timedelta(seconds=1)),
        CertificateValidity(NOW + timedelta(seconds=1), NOW + timedelta(days=400)),
        CertificateValidity(NOW - timedelta(days=1), NOW + MAX_SIGNING_SKEW - timedelta(seconds=1)),
    )
    for window in refused:
        clock = ManualClock(NOW)
        watched = WatchedSigner(signer, clock, validity=window)
        authority = ScriptedAuthority(submissions=[Received("t")], answers=[ACCEPTED])
        with pytest.raises(CertificateNotValidError, match="not valid at the moment of signing"):
            await emitter(authority, watched, clock=clock).emit(credit_invoice())
        assert (watched.signed, authority.events) == (0, [])
    clock = ManualClock(NOW)
    edges = WatchedSigner(signer, clock, validity=CertificateValidity(NOW, NOW + MAX_SIGNING_SKEW))
    signed = await emitter(ScriptedAuthority(), edges, clock=clock).sign(credit_invoice())
    assert (edges.signed, signed.signed_at) == (1, NOW)


async def test_a_signature_far_from_its_moment_of_signing_is_never_sent(signer: KeyPairSigner) -> None:
    late = MAX_SIGNING_SKEW + timedelta(seconds=1)
    for draft, late_from in ((credit_invoice(), 1), (consumer_invoice(), 1), (consumer_invoice(), 2)):
        clock = ManualClock(NOW)
        authority = ScriptedAuthority(submissions=[Received("t")], answers=[ACCEPTED])
        watched = WatchedSigner(signer, clock, takes=late, late_from=late_from)
        with pytest.raises(SigningSkewError, match="5 minutes"):
            await emitter(authority, watched, clock=clock).emit(draft)
        assert (authority.events, watched.signed) == ([], late_from)
    clock = ManualClock(NOW)
    authority = ScriptedAuthority(submissions=[Received("t")], answers=[ACCEPTED])
    slow = WatchedSigner(signer, clock, takes=MAX_SIGNING_SKEW)
    emitted = await emitter(authority, slow, clock=clock).emit(credit_invoice())
    assert (emitted.signed.signed_at, emitted.pending) == (NOW, False)


async def test_a_clock_that_runs_back_while_signing_is_refused_too(signer: KeyPairSigner) -> None:
    clock = ManualClock(NOW)
    back = WatchedSigner(signer, clock, takes=-(MAX_SIGNING_SKEW + timedelta(seconds=1)))
    with pytest.raises(SigningSkewError):
        await emitter(ScriptedAuthority(), back, clock=clock).sign(credit_invoice())


def test_the_settle_delay_is_at_least_the_longest_attempt_at_sending(signer: KeyPairSigner) -> None:
    async def sleep(seconds: float) -> None:
        del seconds

    def built(attempt: float, settle: float) -> Emitter:
        return Emitter(
            xml=DgiiXml(),
            signer=signer,
            authority=ScriptedAuthority(attempt=attempt),
            environment=Environment.TESTECF,
            clock=lambda: NOW,
            sleep=sleep,
            settle=settle,
        )

    with pytest.raises(SettleTooShortError) as raised:
        built(attempt=SETTLE_DELAY + 0.5, settle=SETTLE_DELAY)
    assert raised.value.kind == "internal"
    built(attempt=SETTLE_DELAY, settle=SETTLE_DELAY)
    built(attempt=5.0, settle=5.0)


async def test_the_emitter_totals_and_rounds_with_the_regime_it_was_given(signer: KeyPairSigner) -> None:
    async def sleep(seconds: float) -> None:
        del seconds

    down = replace(REGIME, rounding=replace(ROUNDING, mode=RoundingMode.DOWN))
    line = Line(
        name="ITEM", quantity=Decimal(1), unit_price=Decimal("10.0099"), billing=BillingIndicator.EXEMPT
    )
    draft = replace(consumer_invoice(), lines=(line,))
    built = Emitter(
        xml=DgiiXml(),
        signer=signer,
        authority=ScriptedAuthority(),
        environment=Environment.TESTECF,
        clock=lambda: NOW,
        sleep=sleep,
        regime=down,
    )
    signed = await built.sign(draft)
    assert (signed.totals.line_amounts, signed.totals.total) == ((Decimal("10.00"),), Decimal("10.00"))
