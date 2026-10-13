"""Emitting an e-CF end to end: total, route, write, sign, check, send, poll, map (spec §8.5, §8.6.3-§8.6.4).

1. Totals with the regime's rates on the issue date; the route (``RFCE`` for a type 32 under the threshold).
2. The e-CF written and checked, signed (``FechaHoraFirma`` is the clock's moment of signing), checked again;
   its security code is the first six characters of its signature value. Nothing is signed unless the
   signing certificate is valid at that moment (``CertificateNotValidError``), and nothing is sent whose
   signature came back more than ``MAX_SIGNING_SKEW`` from it (``SigningSkewError``: a queued signer, a clock
   that jumped): a document never states a signing time its certificate or the clock contradicts.
3. RFCE route: the summary written with that code, signed, checked, sent to the ``fc`` host and decided at
   once. The full signed e-CF is kept by the caller (IT §9.1). An RFCE is never sent again here.
4. Reception route: sent once. If DGII may have taken it with its answer unknown, DGII is asked for the
   e-NCF's track ids before anything is sent again: the newest is polled; with none the document is sent
   once more (DGII never recorded it); where DGII cannot say (CerteCF) or the question fails, the outcome
   stays unknown (never "not sent"). Then polled on the schedule (1, 2, 5, 10, 30, 60 s) until a final code;
   after it the document is still pending with its track id (v2-M3's job continues).
5. DGII's codes mapped by ``status_map.decide``: Aceptado Condicional is valid, its messages kept; a rejection
   with ``secuenciaUtilizada = false`` releases the number, any other burns it; a refusal at the door burns
   it too (never reuse what DGII may have recorded).

**Once the e-CF is signed and its security code known, emission never raises.** Whatever fails after that
point (the RFCE's signing or checks, an outcome DGII leaves unknown, an outage, a code DGII never
documented) parks the document: ``Emitted`` carries everything signed so far, its security code, any track
id and the last answer, and ``unknown`` says why (``Unknown``: the error's code, and DGII's failure where
there is one). A parked document may be at DGII: whoever resumes it asks DGII first (track ids for an e-CF,
``consultarfce`` for an RFCE: v2-M3) and never sends it blindly.

The use case holds no state between documents: numbering, persistence and retries across processes are
v2-M3's (the spike's caller supplies each e-NCF).
"""

from dataclasses import dataclass
from datetime import timedelta
from typing import ClassVar, Final, Required, TypedDict, Unpack, final
lazy from collections.abc import Awaitable, Callable
lazy from datetime import datetime

from ledger_country_do.channels.ecf_v1.application.ports import (
    AuthorityUnavailableError,
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
    Failure,
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


class EmitterOptions(TypedDict, total=False):
    """``Emitter``'s keywords beyond its ports: three required, the rest with defaults.

    The environment, a clock (``FechaHoraFirma``) and a sleep (between polls); the poll schedule
    (``POLL_SCHEDULE``), the QR's hosts (``OFFICIAL_QR``) and the regime (``REGIME``).
    """

    environment: Required[Environment]
    clock: Required[Clock]
    sleep: Required[Sleep]
    schedule: tuple[float, ...]
    qr_hosts: QrHosts
    regime: RegimeDef


@final
@dataclass(frozen=True, slots=True)
class Unknown:
    """Why a signed document was parked: the code of the error that stopped emission, and DGII's failure.

    ``failure`` is the ``OutcomeUnknownError``'s reason or the ``AuthorityUnavailableError``'s failure;
    ``None`` for any other error (a code DGII never documented, a signature too late, a check refused).
    """

    code: str
    failure: Failure | None

    @classmethod
    def of(cls, error: EcfError) -> Unknown:
        """What ``error`` says of why the document was parked."""
        if isinstance(error, OutcomeUnknownError):
            return cls(error.code, error.reason)
        if isinstance(error, AuthorityUnavailableError):
            return cls(error.code, error.failure)
        return cls(error.code, None)


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Emitted:
    """What emitting a document produced: what was signed and sent, and DGII's mapped answer."""

    draft: EcfDraft
    totals: Totals
    route: Route
    signed_at: datetime
    signed_ecf: bytes
    signed_rfce: bytes | None
    """The signed RFCE; ``None`` on the reception route, or if emission stopped before it was signed."""
    security_code: str
    qr: str
    track_id: str | None
    outcome: Outcome | None
    """``None`` if DGII was never asked or never answered; a non-final outcome when still pending."""
    unknown: Unknown | None = None
    """Why the document was parked (see the module); ``None`` if emission ran its course."""

    @property
    def pending(self) -> bool:
        """Whether DGII's decision is not known yet: not final, never answered, or parked."""
        return self.outcome is None or not self.outcome.final


@final
@dataclass(frozen=True, slots=True)
class _Signed:
    totals: Totals
    route: Route
    signed_at: datetime
    document: bytes
    security_code: str


def _refused(refusal: Refused) -> Outcome:
    return decide(DgiiCode.REJECTED, sequence_used=None, messages=refusal.messages)


def _decided(answer: Answer, *, rfce: bool) -> Outcome:
    return decide(answer.code, sequence_used=answer.sequence_used, messages=answer.messages, rfce=rfce)


@final
class Emitter:
    """Emits one document at a time (see the module)."""

    __slots__ = (
        "_authority",
        "_clock",
        "_environment",
        "_qr_hosts",
        "_regime",
        "_schedule",
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
        self._qr_hosts = options.get("qr_hosts", OFFICIAL_QR)
        self._regime = options.get("regime", REGIME)

    async def emit(self, draft: EcfDraft) -> Emitted:
        """``draft`` emitted (see the module)."""
        signed = await self._signed_ecf(draft)
        if signed.route is Route.RFCE:
            return await self._summarized(draft, signed)
        return await self._received(draft, signed)

    async def _sign(self, document: bytes, signed_at: datetime) -> bytes:
        """``document`` signed, once its signature is back within ``MAX_SIGNING_SKEW`` of ``signed_at``."""
        signed = await self._signer.sign_document(document)
        if abs(self._clock() - signed_at) > MAX_SIGNING_SKEW:
            raise SigningSkewError
        return signed

    async def _signed_ecf(self, draft: EcfDraft) -> _Signed:
        totals = compute_totals(draft, self._regime)
        where = route(draft, totals, self._regime)
        signed_at = self._clock()
        if not (await self._signer.certificate_validity()).covers(signed_at):
            raise CertificateNotValidError
        signed = await self._sign(self._xml.ecf(draft, totals, signed_at=signed_at), signed_at)
        code = security_code(self._xml.signed(draft, signed, rfce=False))
        return _Signed(totals, where, signed_at, signed, code)

    async def _summarized(self, draft: EcfDraft, signed: _Signed) -> Emitted:
        summary: bytes | None = None
        outcome: Outcome | None = None
        unknown: Unknown | None = None
        try:
            unsigned = self._xml.rfce(draft, signed.totals, security_code=signed.security_code)
            summary = await self._sign(unsigned, signed.signed_at)
            self._xml.signed(draft, summary, rfce=True)
            answer = await self._authority.submit_rfce(
                self._environment, draft.issuer.rnc, draft.encf, summary
            )
            outcome = _refused(answer) if isinstance(answer, Refused) else _decided(answer, rfce=True)
        except EcfError as error:
            unknown = Unknown.of(error)
        qr = rfce_qr_url(
            RfceQrData(draft.issuer.rnc, draft.encf, signed.totals.total, signed.security_code),
            environment=self._environment,
            hosts=self._qr_hosts,
        )
        return Emitted(
            draft=draft,
            totals=signed.totals,
            route=signed.route,
            signed_at=signed.signed_at,
            signed_ecf=signed.document,
            signed_rfce=summary,
            security_code=signed.security_code,
            qr=qr,
            track_id=None,
            outcome=outcome,
            unknown=unknown,
        )

    async def _received(self, draft: EcfDraft, signed: _Signed) -> Emitted:
        track_id: str | None = None
        outcome: Outcome | None = None
        unknown: Unknown | None = None
        try:
            reception = await self._reception(draft, signed.document)
            if isinstance(reception, Refused):
                outcome = _refused(reception)
            else:
                track_id = reception.track_id
                for delay in self._schedule:
                    await self._sleep(delay)
                    answer = await self._authority.result(self._environment, track_id)
                    outcome = _decided(answer, rfce=False)
                    if outcome.final:
                        break
        except EcfError as error:
            unknown = Unknown.of(error)
        data = EcfQrData(
            issuer_rnc=draft.issuer.rnc,
            buyer_rnc=draft.buyer.rnc,
            encf=draft.encf,
            issued_on=draft.issued_on,
            total=signed.totals.total,
            signed_at=signed.signed_at,
            security_code=signed.security_code,
        )
        return Emitted(
            draft=draft,
            totals=signed.totals,
            route=signed.route,
            signed_at=signed.signed_at,
            signed_ecf=signed.document,
            signed_rfce=None,
            security_code=signed.security_code,
            qr=qr_url(data, environment=self._environment, hosts=self._qr_hosts),
            track_id=track_id,
            outcome=outcome,
            unknown=unknown,
        )

    async def _reception(self, draft: EcfDraft, signed: bytes) -> Reception:
        """Sent once; sent again only once DGII names no track id for the e-NCF (see the module)."""
        issuer = draft.issuer.rnc
        try:
            return await self._authority.submit_ecf(self._environment, issuer, draft.encf, signed)
        except OutcomeUnknownError as unknown:
            try:
                known = await self._authority.track_ids(self._environment, issuer, draft.encf)
            except EcfUnavailableError as error:
                raise unknown from error
            if known is None:
                raise
            if known:
                return Received(known[-1])
        return await self._authority.submit_ecf(self._environment, issuer, draft.encf, signed)
