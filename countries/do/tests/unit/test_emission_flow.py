from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Final, final, override

import pytest

from ledger_country_do.channels.ecf_v1.adapters.xml.documents import DgiiXml
from ledger_country_do.channels.ecf_v1.application.emission import (
    MAX_SIGNING_SKEW,
    CertificateNotValidError,
    Emitter,
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
    XmlSigner,
)
from ledger_country_do.channels.ecf_v1.domain.environments import Environment
from ledger_country_do.channels.ecf_v1.domain.errors import EcfError
from ledger_country_do.channels.ecf_v1.domain.routing import Route
from ledger_country_do.channels.ecf_v1.domain.status_map import DgiiMessage, EcfState, NumberFate
from ledger_country_do_testing.clock import ManualClock
from ledger_country_do_testing.samples import consumer_invoice, credit_invoice
lazy from ledger_country_do.channels.ecf_v1.application.ports import Reception
lazy from ledger_country_do.channels.ecf_v1.domain.encf import Encf
lazy from ledger_country_do_testing.local_signer import KeyPairSigner

NOW: Final = datetime(2026, 10, 13, 13, 30, tzinfo=UTC)
INSTANT: Final = timedelta(0)


@final
@dataclass
class ScriptedAuthority(Authority):
    submissions: list[Reception | EcfError] = field(default_factory=list)
    summaries: list[Answer | Refused | EcfError] = field(
        default_factory=lambda: [Refused((DgiiMessage("400", "Tipo de archivo no válido"),))]
    )
    answers: list[Answer | AuthorityUnavailableError] = field(default_factory=list)
    known: tuple[str, ...] | AuthorityUnavailableError | None = ()
    sent: int = 0
    summaries_sent: int = 0

    @override
    async def submit_ecf(
        self, environment: Environment, issuer_rnc: str, encf: Encf, signed: bytes
    ) -> Reception:
        self.sent += 1
        found = self.submissions.pop(0)
        if isinstance(found, EcfError):
            raise found
        return found

    @override
    async def submit_rfce(
        self, environment: Environment, issuer_rnc: str, encf: Encf, signed: bytes
    ) -> Answer | Refused:
        self.summaries_sent += 1
        found = self.summaries.pop(0)
        if isinstance(found, EcfError):
            raise found
        return found

    @override
    async def result(self, environment: Environment, track_id: str) -> Answer:
        found = self.answers.pop(0)
        if isinstance(found, AuthorityUnavailableError):
            raise found
        return found

    @override
    async def track_ids(
        self, environment: Environment, issuer_rnc: str, encf: Encf
    ) -> tuple[str, ...] | None:
        if isinstance(self.known, AuthorityUnavailableError):
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
    authority: Authority,
    signer: XmlSigner,
    schedule: tuple[float, ...] = (1.0, 2.0),
    clock: ManualClock | None = None,
) -> Emitter:
    async def sleep(seconds: float) -> None:
        del seconds

    return Emitter(
        xml=DgiiXml(),
        signer=signer,
        authority=authority,
        environment=Environment.TESTECF,
        clock=clock or (lambda: NOW),
        sleep=sleep,
        schedule=schedule,
    )


IN_PROCESS = Answer(code=3, messages=(), sequence_used=None)
ACCEPTED = Answer(code=1, messages=(), sequence_used=True)
BROKEN = OutcomeUnknownError("reception", Failure.BROKEN)


async def test_an_unknown_outcome_with_a_track_id_at_dgii_is_polled_not_resent(signer: KeyPairSigner) -> None:
    authority = ScriptedAuthority(submissions=[BROKEN], answers=[ACCEPTED], known=("t1", "t2"))
    emitted = await emitter(authority, signer).emit(credit_invoice())
    assert (authority.sent, emitted.track_id, emitted.pending) == (1, "t2", False)


async def test_an_unknown_outcome_dgii_never_recorded_is_sent_again(signer: KeyPairSigner) -> None:
    authority = ScriptedAuthority(submissions=[BROKEN, Received("t3")], answers=[ACCEPTED])
    emitted = await emitter(authority, signer).emit(credit_invoice())
    assert (authority.sent, emitted.track_id) == (2, "t3")


async def test_an_unknown_outcome_where_dgii_cannot_say_is_parked_with_the_signed_e_cf(
    signer: KeyPairSigner,
) -> None:
    authority = ScriptedAuthority(submissions=[BROKEN], known=None)
    emitted = await emitter(authority, signer).emit(credit_invoice())
    assert authority.sent == 1
    assert (emitted.unknown, emitted.track_id, emitted.outcome, emitted.pending) == (
        Unknown("do_ecf_dgii_outcome_unknown", Failure.BROKEN),
        None,
        None,
        True,
    )
    assert emitted.signed_ecf.startswith(b"<?xml")
    assert len(emitted.security_code) == 6


async def test_track_ids_that_cannot_be_read_leave_the_outcome_unknown_never_an_outage(
    signer: KeyPairSigner,
) -> None:
    outage = AuthorityUnavailableError(Failure.SERVER_ERROR, "track_ids")
    authority = ScriptedAuthority(submissions=[BROKEN], known=outage)
    emitted = await emitter(authority, signer).emit(credit_invoice())
    assert (emitted.unknown, authority.sent) == (Unknown("do_ecf_dgii_outcome_unknown", Failure.BROKEN), 1)


async def test_a_second_unknown_outcome_after_resending_is_not_sent_a_third_time(
    signer: KeyPairSigner,
) -> None:
    again = OutcomeUnknownError("reception", Failure.TIMED_OUT)
    authority = ScriptedAuthority(submissions=[BROKEN, again])
    emitted = await emitter(authority, signer).emit(credit_invoice())
    assert (emitted.unknown, authority.sent) == (Unknown("do_ecf_dgii_outcome_unknown", Failure.TIMED_OUT), 2)


async def test_after_the_schedule_a_document_still_in_process_is_pending(signer: KeyPairSigner) -> None:
    authority = ScriptedAuthority(submissions=[Received("t")], answers=[IN_PROCESS, IN_PROCESS])
    emitted = await emitter(authority, signer).emit(credit_invoice())
    assert emitted.pending
    assert emitted.outcome is not None
    assert emitted.outcome.state is EcfState.IN_PROCESS
    nothing = await emitter(ScriptedAuthority(submissions=[Received("t")]), signer, schedule=()).emit(
        credit_invoice()
    )
    assert (nothing.outcome, nothing.pending) == (None, True)


async def test_an_outage_while_polling_leaves_the_received_document_pending_with_its_track_id(
    signer: KeyPairSigner,
) -> None:
    outage = AuthorityUnavailableError(Failure.UNREACHABLE, "result")
    authority = ScriptedAuthority(submissions=[Received("t")], answers=[IN_PROCESS, outage])
    emitted = await emitter(authority, signer).emit(credit_invoice())
    assert (emitted.track_id, emitted.pending, emitted.unknown) == (
        "t",
        True,
        Unknown("do_ecf_dgii_unavailable", Failure.UNREACHABLE),
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
        Unknown("do_ecf_dgii_code_unknown", None),
    )
    assert emitted.pending


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
    assert (emitted.route, authority.summaries_sent, authority.sent) == (Route.RFCE, 1, 0)
    assert (emitted.unknown, emitted.outcome, emitted.pending, emitted.track_id) == (
        Unknown("do_ecf_dgii_outcome_unknown", Failure.BROKEN),
        None,
        True,
        None,
    )
    assert emitted.signed_rfce is not None
    assert len(emitted.security_code) == 6


async def test_a_certificate_not_valid_at_the_moment_of_signing_signs_nothing(signer: KeyPairSigner) -> None:
    windows = (
        CertificateValidity(NOW - timedelta(days=400), NOW - timedelta(seconds=1)),
        CertificateValidity(NOW + timedelta(seconds=1), NOW + timedelta(days=400)),
    )
    for window in windows:
        clock = ManualClock(NOW)
        watched = WatchedSigner(signer, clock, validity=window)
        authority = ScriptedAuthority(submissions=[Received("t")], answers=[ACCEPTED])
        with pytest.raises(CertificateNotValidError, match="not valid at the moment of signing"):
            await emitter(authority, watched, clock=clock).emit(credit_invoice())
        assert (watched.signed, authority.sent) == (0, 0)


async def test_a_certificate_valid_at_its_bounds_signs(signer: KeyPairSigner) -> None:
    for window in (
        CertificateValidity(NOW, NOW + timedelta(days=1)),
        CertificateValidity(NOW - timedelta(days=1), NOW),
    ):
        clock = ManualClock(NOW)
        watched = WatchedSigner(signer, clock, validity=window)
        authority = ScriptedAuthority(submissions=[Received("t")], answers=[ACCEPTED])
        emitted = await emitter(authority, watched, clock=clock).emit(credit_invoice())
        assert (watched.signed, emitted.signed_at) == (1, NOW)


async def test_a_signature_far_from_its_moment_of_signing_is_never_sent(signer: KeyPairSigner) -> None:
    late = MAX_SIGNING_SKEW + timedelta(seconds=1)
    for draft in (credit_invoice(), consumer_invoice()):
        clock = ManualClock(NOW)
        authority = ScriptedAuthority(submissions=[Received("t")], answers=[ACCEPTED])
        with pytest.raises(SigningSkewError, match="5 minutes"):
            await emitter(authority, WatchedSigner(signer, clock, takes=late), clock=clock).emit(draft)
        assert (authority.sent, authority.summaries_sent) == (0, 0)
    clock = ManualClock(NOW)
    authority = ScriptedAuthority(submissions=[Received("t")], answers=[ACCEPTED])
    slow = WatchedSigner(signer, clock, takes=MAX_SIGNING_SKEW)
    emitted = await emitter(authority, slow, clock=clock).emit(credit_invoice())
    assert (emitted.signed_at, emitted.pending) == (NOW, False)


async def test_a_clock_that_runs_back_while_signing_is_refused_too(signer: KeyPairSigner) -> None:
    clock = ManualClock(NOW)
    back = WatchedSigner(signer, clock, takes=-(MAX_SIGNING_SKEW + timedelta(seconds=1)))
    with pytest.raises(SigningSkewError):
        await emitter(ScriptedAuthority(), back, clock=clock).emit(credit_invoice())


async def test_an_rfce_answered_with_a_code_dgii_never_documented_is_parked(signer: KeyPairSigner) -> None:
    authority = ScriptedAuthority(summaries=[Answer(code=3, messages=(), sequence_used=None)])
    emitted = await emitter(authority, signer).emit(consumer_invoice())
    assert (emitted.unknown, emitted.outcome, emitted.pending, authority.summaries_sent) == (
        Unknown("do_ecf_dgii_code_unknown", None),
        None,
        True,
        1,
    )
    assert emitted.signed_rfce is not None


async def test_an_e_cf_dgii_did_not_take_is_parked_too(signer: KeyPairSigner) -> None:
    throttled = AuthorityUnavailableError(Failure.THROTTLED, "reception")
    authority = ScriptedAuthority(submissions=[throttled])
    emitted = await emitter(authority, signer).emit(credit_invoice())
    assert (emitted.unknown, emitted.track_id, authority.sent) == (
        Unknown("do_ecf_dgii_unavailable", Failure.THROTTLED),
        None,
        1,
    )


async def test_an_rfce_whose_signature_comes_back_late_is_parked_unsent_with_the_signed_e_cf(
    signer: KeyPairSigner,
) -> None:
    clock = ManualClock(NOW)
    late = WatchedSigner(signer, clock, takes=MAX_SIGNING_SKEW + timedelta(seconds=1), late_from=2)
    authority = ScriptedAuthority()
    emitted = await emitter(authority, late, clock=clock).emit(consumer_invoice())
    assert (emitted.unknown, emitted.signed_rfce, authority.summaries_sent, late.signed) == (
        Unknown("do_ecf_signing_skew", None),
        None,
        0,
        2,
    )
    assert len(emitted.security_code) == 6
