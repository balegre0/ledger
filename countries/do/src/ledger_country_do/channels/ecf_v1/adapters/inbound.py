"""What the receiver takes in, read and trusted (spec §3.8, §10; Decision 17): ``Inbound``.

The documents other emitters send the receiver are signed under certificates that chain to the configured
roots (``CounterpartyTrust``). The receiver reads them at start as the worker does (``outbound.read_roots``,
in a thread): a receiver whose roots cannot be read, or hold no certificate, does not start
(``TrustedRootsUnreadableError``, which never names the file), rather than refusing every document later.
Outside the app's run the policy is not there (``InboundClosedError``: a defect).

Each document is read first (``adapters.xml.inbound``: hardened, its schema and values), then its signature
verified (``INBOUND``), then its certificate trusted: for the e-CF's ``RNCEmisor``, the ACECF's
``RNCComprador``, and for a seed whoever its certificate names (``CounterpartyTrust.vouched``). An e-CF or an
approval may arrive any time after it was signed (``late``: deliveries are retried for days); a seed is
signed now. A certificate whose SHA-256 fingerprint is one of the stand-ins (``TEST_SENDER_STANDINS``: DGII's
during certification) is trusted for any party, its chain still required, in a test environment only.
"""

import asyncio
from contextlib import asynccontextmanager
from typing import ClassVar, final, override
lazy from collections.abc import AsyncGenerator
lazy from datetime import datetime
lazy from pathlib import Path

from cryptography.hazmat.primitives import hashes

from ledger_country_do.channels.ecf_v1.adapters.outbound import read_roots
from ledger_country_do.channels.ecf_v1.adapters.receiver.seeds import (
    SeedUnreadableError,
    SeedUntrustedError,
    SignedSeed,
)
from ledger_country_do.channels.ecf_v1.adapters.signing.trust import CounterpartyTrust
from ledger_country_do.channels.ecf_v1.adapters.signing.verifier import INBOUND, verify_enveloped
from ledger_country_do.channels.ecf_v1.adapters.xml.arecf import render_arecf
from ledger_country_do.channels.ecf_v1.adapters.xml.inbound import (
    InboundDocumentInvalidError,
    name_inbound,
    read_inbound_acecf,
    read_inbound_ecf,
    read_inbound_seed,
)
from ledger_country_do.channels.ecf_v1.application.receiving import (
    InboundFormat,
    Sender,
    Trusted,
    Unreadable,
    Untrusted,
)
from ledger_country_do.channels.ecf_v1.domain.errors import EcfError, EcfInvalidError
from ledger_fiscal_kit.xml.hardened import XmlDocumentError
lazy from ledger_country_do.channels.ecf_v1.adapters.signing.verifier import VerifiedSignature
lazy from ledger_country_do.channels.ecf_v1.application.receiving import Inspection
lazy from ledger_country_do.channels.ecf_v1.domain.environments import Environment
lazy from ledger_country_do.channels.ecf_v1.domain.exchanges import CommercialApproval, Receipt


class InboundClosedError(EcfError):
    """The receiver's trust was reached while the app is not running: a defect of what reached it."""

    code: ClassVar[str] = "do_ecf_inbound_closed"

    def __init__(self) -> None:
        super().__init__("the receiver's trusted roots are read only while the app runs")


def fingerprint(verified: VerifiedSignature) -> str:
    """The signing certificate's SHA-256 fingerprint, as the stand-ins setting spells it."""
    return verified.certificate.fingerprint(hashes.SHA256()).hex()


@final
class Inbound(InboundFormat):
    """What the receiver takes in (see the module), over the roots it reads at start."""

    __slots__ = ("_roots", "_standins", "_trust")

    def __init__(self, roots: Path, *, standins: frozenset[str]) -> None:
        self._roots = roots
        self._standins = standins
        self._trust: CounterpartyTrust | None = None

    @asynccontextmanager
    async def opened(self) -> AsyncGenerator[Inbound]:
        """The policy over the roots, read when the block opens (see the module)."""
        self._trust = CounterpartyTrust(roots=await asyncio.to_thread(read_roots, self._roots))
        try:
            yield self
        finally:
            self._trust = None

    @property
    def trust(self) -> CounterpartyTrust:
        """The policy, while the app runs."""
        if self._trust is None:
            raise InboundClosedError
        return self._trust

    @override
    def ecf(self, document: bytes, *, environment: Environment, received_at: datetime) -> Inspection:
        try:
            ecf = read_inbound_ecf(document)
        except InboundDocumentInvalidError:
            return Unreadable(name_inbound(document))
        try:
            self._trusted(
                document, ecf.issuer_rnc, environment, signed_at=ecf.signed_at, received_at=received_at
            )
        except EcfInvalidError, XmlDocumentError:
            return Untrusted(ecf)
        return Trusted(ecf)

    @override
    def approval(
        self, document: bytes, *, environment: Environment, received_at: datetime
    ) -> CommercialApproval | None:
        try:
            approval = read_inbound_acecf(document)
            self._trusted(
                document, approval.buyer_rnc, environment, signed_at=approval.at, received_at=received_at
            )
        except EcfInvalidError, XmlDocumentError:
            return None
        return approval

    @override
    def receipt(self, receipt: Receipt) -> bytes:
        return render_arecf(receipt)

    def seed(self, document: bytes, *, environment: Environment, received_at: datetime) -> SignedSeed:
        """``document``, a seed signed for a token: its value and who signed it (see the module).

        ``SeedUnreadableError`` when it is no signed ``SemillaModel``; ``SeedUntrustedError`` when its
        signature does not hold, its certificate is not trusted, or it names nobody and stands in for nobody.
        """
        try:
            value = read_inbound_seed(document)
        except InboundDocumentInvalidError as error:
            raise SeedUnreadableError from error
        try:
            verified = verify_enveloped(document, INBOUND)
            named = self.trust.vouched(verified, signed_at=received_at, received_at=received_at)
        except (EcfInvalidError, XmlDocumentError) as error:
            raise SeedUntrustedError from error
        stand_in = self._stands_in(verified, environment)
        if not (named or stand_in):
            raise SeedUntrustedError
        return SignedSeed(value=value, sender=Sender(identities=named, stand_in=stand_in))

    def _trusted(
        self,
        document: bytes,
        party: str,
        environment: Environment,
        *,
        signed_at: datetime,
        received_at: datetime,
    ) -> None:
        """Refuse ``document``'s signature unless it holds and its certificate may speak for ``party``."""
        verified = verify_enveloped(document, INBOUND)
        if self._stands_in(verified, environment):
            _ = self.trust.vouched(verified, signed_at=signed_at, received_at=received_at, late=True)
            return
        self.trust.check(
            verified, counterparty=party, signed_at=signed_at, received_at=received_at, late=True
        )

    def _stands_in(self, verified: VerifiedSignature, environment: Environment) -> bool:
        """Whether ``verified``'s certificate is a stand-in sender here: a test environment only."""
        return not environment.live and fingerprint(verified) in self._standins
