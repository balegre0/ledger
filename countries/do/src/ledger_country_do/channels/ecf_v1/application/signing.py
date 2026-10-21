"""Signing an e-CF (spec §8.6.4, ``numbered → signed``): total, route, write, sign, check, QR.

``Signing.sign(who, draft, *, environment, expires_on) -> Signed``. Totals with the regime's rates on the
issue date; the route (``RFCE`` for a type 32 under the threshold). The e-CF written and checked, signed
(``FechaHoraFirma`` is the clock's moment of signing) and checked again; its security code is the first six
characters of its signature value. On the RFCE route the summary is written with that code, signed and
checked too. The signer is told the moment of signing and the e-NCF; whether the certificate covers that
moment is the signer's rule, not asked here first (``Signatures``' errors: ``CertificateNotValidError``,
``CertificateMissingError``...). Nothing is returned whose signature came back more than
``MAX_SIGNING_SKEW`` from that moment (``SigningSkewError``: a queued signer, a clock that jumped).

``expires_on`` is the last day of the **range** the number came from (Santo Domingo's date), whatever the
type: TesteCF dates a 32's range too, though the draft of a 32 or 34 states no expiry. A moment of signing
after it is ``SequenceExpiredError``: a document numbered on a range's last day and signed later needs
another number.

Signing asks DGII nothing: an ``api`` app signs within a request's budget without DGII's clients, which only
the worker opens.
"""

from dataclasses import dataclass
from datetime import timedelta
from typing import ClassVar, Final, final
lazy from datetime import date, datetime
lazy from uuid import UUID

from ledger_country_do.channels.ecf_v1.domain.errors import EcfConflictError, EcfUnavailableError
from ledger_country_do.channels.ecf_v1.domain.notation import local_date
from ledger_country_do.channels.ecf_v1.domain.printed import (
    OFFICIAL_QR,
    EcfQrData,
    RfceQrData,
    qr_url,
    rfce_qr_url,
    security_code,
)
from ledger_country_do.channels.ecf_v1.domain.routing import Route, route
from ledger_country_do.channels.ecf_v1.domain.totals import compute_totals
from ledger_country_do.regime import REGIME
lazy from ledger_country_do.channels.ecf_v1.application.ports import (
    EcfFormat,
    Signatures,
    SignedDocument,
    SigningAs,
)
lazy from ledger_country_do.channels.ecf_v1.domain.drafts import EcfDraft
lazy from ledger_country_do.channels.ecf_v1.domain.environments import Environment
lazy from ledger_country_do.channels.ecf_v1.domain.printed import QrHosts
lazy from ledger_country_do.channels.ecf_v1.domain.totals import Totals
lazy from ledger_kernel.clock import Clock
lazy from ledger_kernel.regimes import RegimeDef

MAX_SIGNING_SKEW: Final = timedelta(minutes=5)
"""How far a signature may come back from its ``FechaHoraFirma``, either way.

Coupled to the signer: its ``LEDGER_SIGNER_SIGNING_SKEW_SECONDS`` defaults to the same 300 s and must never be
configured below it (a test pins the value)."""


class SequenceExpiredError(EcfConflictError):
    """The number's range ended before the moment of signing: a document parked past it needs a new number."""

    code: ClassVar[str] = "do_ecf_sequence_expired"

    def __init__(self) -> None:
        super().__init__("the number's range expired before the moment of signing")


class SigningSkewError(EcfUnavailableError):
    """A signature came back too far from its stated moment of signing; signing again may succeed.

    The signer's own clock disagreeing with that moment is another matter (``SignerClockSkewError``, a
    conflict): signing again from the same clock would be refused again.
    """

    code: ClassVar[str] = "do_ecf_signing_skew"

    def __init__(self) -> None:
        minutes = int(MAX_SIGNING_SKEW.total_seconds() // 60)
        super().__init__(f"a signature came back more than {minutes} minutes from its moment of signing")


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Signed:
    """A document signed and checked, ready to keep and send: what a printed representation shows too."""

    draft: EcfDraft
    totals: Totals
    route: Route
    signed_at: datetime
    signed_ecf: bytes
    certificate: UUID
    """The certificate that signed the e-CF (the signer's id)."""
    signed_rfce: bytes | None
    """The signed RFCE on the RFCE route; ``None`` on the reception route."""
    rfce_certificate: UUID | None
    """The certificate that signed the RFCE; ``None`` on the reception route."""
    security_code: str
    qr: str


@final
class Signing:
    """Signs one document at a time, for whomever the call names (see the module)."""

    __slots__ = ("_clock", "_qr_hosts", "_regime", "_signer", "_xml")

    def __init__(
        self,
        xml: EcfFormat,
        signer: Signatures,
        *,
        clock: Clock,
        qr_hosts: QrHosts = OFFICIAL_QR,
        regime: RegimeDef = REGIME,
    ) -> None:
        """``clock`` states each ``FechaHoraFirma``; ``qr_hosts`` the QR's (DGII's unless a test's fake)."""
        self._xml = xml
        self._signer = signer
        self._clock = clock
        self._qr_hosts = qr_hosts
        self._regime = regime

    def now(self) -> datetime:
        """The moment its clock reads: what a number is allocated at, as a signature states it."""
        return self._clock()

    async def sign(
        self, who: SigningAs, draft: EcfDraft, *, environment: Environment, expires_on: date | None
    ) -> Signed:
        """``draft`` signed as ``who`` and checked, its RFCE too on the RFCE route (see the module)."""
        totals = compute_totals(draft, self._regime)
        where = route(draft, totals, self._regime)
        signed_at = self._clock()
        if expires_on is not None and local_date(signed_at) > expires_on:
            raise SequenceExpiredError  # DGII dates the range: it ran up to that day, in Santo Domingo
        reference = str(draft.encf)
        unsigned = self._xml.ecf(draft, totals, signed_at=signed_at)
        signed = await self._sign(who, unsigned, signed_at, reference)
        code = security_code(self._xml.signed(draft, signed.document, rfce=False))
        if where is Route.RFCE:
            unsigned_summary = self._xml.rfce(draft, totals, security_code=code)
            summary = await self._sign(who, unsigned_summary, signed_at, reference)
            self._xml.signed(draft, summary.document, rfce=True)
            qr = rfce_qr_url(
                RfceQrData(draft.issuer.rnc, draft.encf, totals.total, code),
                environment=environment,
                hosts=self._qr_hosts,
            )
            return Signed(
                draft=draft,
                totals=totals,
                route=where,
                signed_at=signed_at,
                signed_ecf=signed.document,
                certificate=signed.certificate,
                signed_rfce=summary.document,
                rfce_certificate=summary.certificate,
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
            signed_ecf=signed.document,
            certificate=signed.certificate,
            signed_rfce=None,
            rfce_certificate=None,
            security_code=code,
            qr=qr_url(data, environment=environment, hosts=self._qr_hosts),
        )

    async def _sign(
        self, who: SigningAs, document: bytes, signed_at: datetime, reference: str
    ) -> SignedDocument:
        """``document`` signed, once its signature is back within ``MAX_SIGNING_SKEW`` of ``signed_at``."""
        signed = await self._signer.document(who, document, signed_at=signed_at, reference=reference)
        if abs(self._clock() - signed_at) > MAX_SIGNING_SKEW:
            raise SigningSkewError
        return signed
