"""The signing step (``numbered → signed``): what the signer is told, what it refuses, what is refused after.

Moved from the spike's emission flow (``test_emission_flow``): ``Channel.issue`` and the ``do_ecf.advance``
job sign through ``Signing`` itself, the range's last day given by the caller (the range's, not the draft's).
"""

from dataclasses import replace
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from typing import Final, final, override
lazy from datetime import date

import pytest

from ledger_country_do.channels.ecf_v1.adapters.signing.port import DocumentSigner
from ledger_country_do.channels.ecf_v1.adapters.xml.documents import DgiiXml
from ledger_country_do.channels.ecf_v1.application.ports import (
    CertificateMissingError,
    CertificateNotValidError,
    Signatures,
    SignerClockSkewError,
    SignerUnavailableError,
)
from ledger_country_do.channels.ecf_v1.application.signing import (
    MAX_SIGNING_SKEW,
    SequenceExpiredError,
    Signing,
    SigningSkewError,
)
from ledger_country_do.channels.ecf_v1.domain.drafts import BillingIndicator, Line
from ledger_country_do.channels.ecf_v1.domain.environments import Environment
from ledger_country_do.channels.ecf_v1.domain.routing import Route
from ledger_country_do.regime import REGIME
from ledger_country_do.regime.rounding import ROUNDING
from ledger_country_do_testing.local_signer import SIGNING_AS
from ledger_country_do_testing.samples import SEQUENCE_EXPIRES_ON, consumer_invoice, credit_invoice
from ledger_kernel.money import RoundingMode
from ledger_testing.clock import ManualClock
lazy from ledger_country_do.channels.ecf_v1.application.ports import SignedDocument, SigningAs
lazy from ledger_country_do.channels.ecf_v1.domain.drafts import EcfDraft
lazy from ledger_country_do.channels.ecf_v1.domain.errors import EcfError
lazy from ledger_country_do_testing.local_signer import KeyPairSigner
lazy from ledger_kernel.regimes import RegimeDef

NOW: Final = datetime(2026, 10, 13, 13, 30, tzinfo=UTC)
WHO: Final = SIGNING_AS
INSTANT: Final = timedelta(0)


@final
class WatchedSigner(Signatures):
    """A real signer whose signing is recorded, may take (clock) time, or is refused with ``refusal``."""

    __slots__ = ("_clock", "_inner", "_late_from", "_refusal", "_takes", "asked")

    def __init__(
        self,
        inner: KeyPairSigner,
        clock: ManualClock,
        *,
        takes: timedelta = INSTANT,
        late_from: int = 1,
        refusal: EcfError | None = None,
    ) -> None:
        self._inner = DocumentSigner(inner)
        self._clock = clock
        self._takes = takes
        self._late_from = late_from
        self._refusal = refusal
        self.asked: list[tuple[datetime, str]] = []
        """``(signed_at, reference)`` of every document asked to be signed."""

    @property
    def signed(self) -> int:
        return len(self.asked)

    @override
    async def document(
        self, who: SigningAs, document: bytes, *, signed_at: datetime, reference: str
    ) -> SignedDocument:
        assert who == WHO
        self.asked.append((signed_at, reference))
        if self._refusal is not None:
            raise self._refusal
        if self.signed >= self._late_from:
            self._clock.advance(self._takes)
        return await self._inner.document(who, document, signed_at=signed_at, reference=reference)

    @override
    async def seed(self, who: SigningAs, seed: bytes, *, audience: str) -> bytes:
        raise AssertionError  # the signing step signs documents only


def signing(signer: Signatures, clock: ManualClock, regime: RegimeDef = REGIME) -> Signing:
    return Signing(DgiiXml(), signer, clock=clock, regime=regime)


async def signed_at(
    signer: KeyPairSigner, moment: datetime, draft: EcfDraft, expires_on: date | None
) -> tuple[datetime, int]:
    """When ``draft`` was signed at ``moment`` from a range ending ``expires_on``, and how many signatures."""
    clock = ManualClock(moment)
    watched = WatchedSigner(signer, clock)
    signed = await signing(watched, clock).sign(
        WHO, draft, environment=Environment.TESTECF, expires_on=expires_on
    )
    return signed.signed_at, watched.signed


async def test_the_signer_is_told_the_moment_of_signing_and_the_e_ncf(signer: KeyPairSigner) -> None:
    clock = ManualClock(NOW)
    watched = WatchedSigner(signer, clock)
    full = await signing(watched, clock).sign(
        WHO, credit_invoice(), environment=Environment.TESTECF, expires_on=None
    )
    summary = WatchedSigner(signer, clock)
    rfce = await signing(summary, clock).sign(
        WHO, consumer_invoice(), environment=Environment.TESTECF, expires_on=None
    )
    assert watched.asked == [(NOW, "E310000000001")]
    assert summary.asked == [(NOW, "E320000000001"), (NOW, "E320000000001")]
    assert (full.route, full.signed_rfce, full.rfce_certificate) == (Route.RECEPTION, None, None)
    assert (rfce.route, rfce.rfce_certificate) == (Route.RFCE, rfce.certificate)
    assert rfce.signed_rfce is not None
    assert "testecf/consultatimbre?" in full.qr
    assert "testecf/consultatimbrefc?" in rfce.qr


@pytest.mark.parametrize(
    "refusal",
    [CertificateNotValidError(), CertificateMissingError(), SignerClockSkewError(), SignerUnavailableError()],
    ids=["not-valid", "missing", "clock-skew", "unavailable"],
)
async def test_a_certificate_the_signer_refuses_raises_the_channels_error(
    signer: KeyPairSigner, refusal: EcfError
) -> None:
    """Validity at signing is the signer's: nothing is asked first."""
    clock = ManualClock(NOW)
    watched = WatchedSigner(signer, clock, refusal=refusal)
    with pytest.raises(type(refusal)) as caught:
        await signing(watched, clock).sign(
            WHO, credit_invoice(), environment=Environment.TESTECF, expires_on=None
        )
    assert caught.value is refusal
    assert watched.asked == [(NOW, "E310000000001")]


async def test_a_range_that_expired_before_the_moment_of_signing_is_not_signed(
    signer: KeyPairSigner,
) -> None:
    """A document numbered on the range's last day and parked for days is not signed after the range ended.

    The range's day, whatever the type: a 32's draft states no expiry, yet TesteCF dates its range.
    """
    last_day_ends = datetime.combine(SEQUENCE_EXPIRES_ON + timedelta(days=1), time(4), tzinfo=UTC)
    # 04:00 UTC is midnight in Santo Domingo (UTC-4): the range ran up to that day, inclusive.
    for draft in (credit_invoice(), consumer_invoice()):
        before = last_day_ends - timedelta(minutes=1)
        assert (await signed_at(signer, before, draft, SEQUENCE_EXPIRES_ON))[0] == before
        clock = ManualClock(last_day_ends)
        watched = WatchedSigner(signer, clock)
        with pytest.raises(SequenceExpiredError, match="expired before the moment of signing"):
            await signing(watched, clock).sign(
                WHO, draft, environment=Environment.TESTECF, expires_on=SEQUENCE_EXPIRES_ON
            )
        assert watched.signed == 0


async def test_a_range_dgii_dated_not_is_signed_whenever(signer: KeyPairSigner) -> None:
    moment = datetime.combine(SEQUENCE_EXPIRES_ON + timedelta(days=400), time(15), tzinfo=UTC)
    assert await signed_at(signer, moment, consumer_invoice(), None) == (moment, 2)


def test_the_channel_s_skew_is_the_signer_s_default() -> None:
    # Pinned: the signer refuses unless the certificate covers [signed_at, signed_at + its skew] and its clock
    # is within that skew (LEDGER_SIGNER_SIGNING_SKEW_SECONDS, default 300). The channel accepts a signature
    # back within MAX_SIGNING_SKEW: the signer's skew must never be configured below it.
    assert timedelta(seconds=300) == MAX_SIGNING_SKEW


async def test_a_signature_far_from_its_moment_of_signing_is_refused(signer: KeyPairSigner) -> None:
    late = MAX_SIGNING_SKEW + timedelta(seconds=1)
    for draft, late_from in ((credit_invoice(), 1), (consumer_invoice(), 1), (consumer_invoice(), 2)):
        clock = ManualClock(NOW)
        watched = WatchedSigner(signer, clock, takes=late, late_from=late_from)
        with pytest.raises(SigningSkewError, match="5 minutes"):
            await signing(watched, clock).sign(WHO, draft, environment=Environment.TESTECF, expires_on=None)
        assert watched.signed == late_from
    clock = ManualClock(NOW)
    slow = WatchedSigner(signer, clock, takes=MAX_SIGNING_SKEW)
    signed = await signing(slow, clock).sign(
        WHO, credit_invoice(), environment=Environment.TESTECF, expires_on=None
    )
    assert signed.signed_at == NOW


async def test_a_clock_that_runs_back_while_signing_is_refused_too(signer: KeyPairSigner) -> None:
    clock = ManualClock(NOW)
    back = WatchedSigner(signer, clock, takes=-(MAX_SIGNING_SKEW + timedelta(seconds=1)))
    with pytest.raises(SigningSkewError):
        await signing(back, clock).sign(
            WHO, credit_invoice(), environment=Environment.TESTECF, expires_on=None
        )


async def test_the_step_totals_and_rounds_with_the_regime_it_was_given(signer: KeyPairSigner) -> None:
    down = replace(REGIME, rounding=replace(ROUNDING, mode=RoundingMode.DOWN))
    line = Line(
        name="ITEM", quantity=Decimal(1), unit_price=Decimal("10.0099"), billing=BillingIndicator.EXEMPT
    )
    draft = replace(consumer_invoice(), lines=(line,))
    clock = ManualClock(NOW)
    signed = await signing(DocumentSigner(signer), clock, down).sign(
        WHO, draft, environment=Environment.TESTECF, expires_on=None
    )
    assert (signed.totals.line_amounts, signed.totals.total) == ((Decimal("10.00"),), Decimal("10.00"))
