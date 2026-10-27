"""do-ecf-v1's assembly (§3.10): what the apps make of the channel, and the pack its entry point names.

Every app builds the channel fiscal routes to (``DoEcfChannel``) over the channel's units, the signer's
port (``DocumentSigner``) and the archive, with its ``do_ecf.advance`` job (which reaches DGII through the
worker's client only: in any other role reaching it is a defect), its ``do_ecf.deliver`` job (the worker's
buyer client likewise) and its ``do_ecf.approve`` job (both clients: a received e-CF's commercial approval
sent to its emitter and to DGII), and checks its settings: DGII's
hosts (``DgiiHosts``), an attempt no longer than the settle delay (``SettleTooShortError``), no test
stand-in in production. It reads the ports it needs from the app (fiscal-kit's keys): the signer, fiscal's
company directory (who may do what, and who is enrolled), the archive in the roles that archive (none: the
channel does not start there, since every document it signs is archived first; ``migrate`` reads none,
keeps nothing, and gets the channel's revisions alone). The ``api`` app mounts its routes (``/v1/do``: the
ranges, a delivery's retry, the delivery options, the received documents and their approval); the
worker opens its DGII and buyer clients at start (their trusted roots, which it requires, read then); the
receiver requires the trusted roots too, reads them at start the same way (the documents emitters send it
chain to them: ``inbound``) and mounts the channel's receiver routes (``adapters.receiver``): seeds and
tokens, e-CF received and answered with a signed receipt, commercial approvals. Every role hands the
receiver's purge (``do_ecf.purge_receiver_credentials``) beside ``do_ecf.advance``.
``migrate`` runs its revisions; every app checks its schema is current. Documents are signed with the QR's
hosts of the DGII hosts configured (a test's fake's, or DGII's), and printed as PDFs (``adapters.pdf``).
"""

from dataclasses import replace
from enum import StrEnum
from typing import ClassVar, Final
lazy from pathlib import Path

from ledger_country_do._manifest import MANIFEST
from ledger_country_do.channels.ecf_v1.adapters.archive import PortArchive, UnboundArchive
from ledger_country_do.channels.ecf_v1.adapters.channel import DoEcfChannel
from ledger_country_do.channels.ecf_v1.adapters.dgii.endpoints import DgiiHosts
from ledger_country_do.channels.ecf_v1.adapters.enrollments import DirectoryEnrollments
from ledger_country_do.channels.ecf_v1.adapters.http import DgiiTimeouts
from ledger_country_do.channels.ecf_v1.adapters.inbound import Inbound
from ledger_country_do.channels.ecf_v1.adapters.jobs import advance_job, approve_job, deliver_job, purge_job
from ledger_country_do.channels.ecf_v1.adapters.logs import (
    AdvanceLog,
    ApprovalLog,
    DeliveryLog,
    ReceptionLog,
    SigningLog,
)
from ledger_country_do.channels.ecf_v1.adapters.outbound import (
    DgiiAccess,
    Outbound,
    unopened,
    unopened_buyers,
    unopened_emitters,
)
from ledger_country_do.channels.ecf_v1.adapters.pdf.render import PdfPrinting
from ledger_country_do.channels.ecf_v1.adapters.postgres.schema import migrations
from ledger_country_do.channels.ecf_v1.adapters.postgres.unit import PgUnits
from ledger_country_do.channels.ecf_v1.adapters.receiver import routes as receiver
from ledger_country_do.channels.ecf_v1.adapters.receiver.tokens import ReceiverCredentials
from ledger_country_do.channels.ecf_v1.adapters.routes import deliveries, options, ranges, received
from ledger_country_do.channels.ecf_v1.adapters.signing.port import DocumentSigner
from ledger_country_do.channels.ecf_v1.adapters.xml.documents import DgiiXml
from ledger_country_do.channels.ecf_v1.application.advancing import Advancing, SettleTooShortError
from ledger_country_do.channels.ecf_v1.application.approvals_in import Approving
from ledger_country_do.channels.ecf_v1.application.approving import (
    ApprovalSending,
    ApprovalSigning,
    CommercialApprovals,
)
from ledger_country_do.channels.ecf_v1.application.delivering import Delivering
from ledger_country_do.channels.ecf_v1.application.issuing import Issuing
from ledger_country_do.channels.ecf_v1.application.ranges import Ranging
from ledger_country_do.channels.ecf_v1.application.receiving import Receiving
from ledger_country_do.channels.ecf_v1.application.representation import Representing
from ledger_country_do.channels.ecf_v1.application.requirements import Requiring
from ledger_country_do.channels.ecf_v1.application.signing import Signing
from ledger_country_do.channels.ecf_v1.domain.channel import KEY
from ledger_country_do.channels.ecf_v1.domain.errors import EcfError
from ledger_country_do.channels.ecf_v1.domain.fields import CHANNEL_FIELDS
from ledger_country_do.channels.ecf_v1.domain.polling import SETTLE_DELAY
from ledger_country_do.channels.ecf_v1.settings import DoEcfSettings
from ledger_country_do.regime.sources import DECREE_587_24, FORMAT_ECF, SERVICES_DGII, TECHNICAL_REPORT
from ledger_fiscal_kit.channels import ChannelDef
from ledger_fiscal_kit.ports import ARCHIVE_PORT, COMPANY_DIRECTORY, SIGNER_PORT
from ledger_kernel.regimes import Locale
from ledger_platform.runtime.contexts import ChannelPack, ChannelParts
lazy from ledger_platform.config.manifest import AppRole
lazy from ledger_platform.config.settings import Environment
lazy from ledger_platform.runtime.contexts import ChannelContext

_LOCAL_STACK: Final[frozenset[Environment]] = frozenset({"local", "test"})
"""Where DGII's hosts may be a local service's over plain http (compose's fake DGII, a test's)."""
ARCHIVING_ROLES: Final[frozenset[AppRole]] = frozenset({"api", "worker", "receiver"})
"""The roles that sign or receive documents, and archive them: they need the archive; ``migrate`` does not."""
LABELS: Final = {
    Locale("es"): "Comprobante Fiscal Electrónico (e-CF) de la DGII",
    Locale("en"): "DGII electronic fiscal voucher (e-CF)",
}
SOURCES: Final = (FORMAT_ECF, TECHNICAL_REPORT, SERVICES_DGII, DECREE_587_24)
"""DGII's documents the channel follows: the format, the technical report, the services, contingency."""


class Refusal(StrEnum):
    """A setting the app's role or environment refuses, and why."""

    STANDINS_IN_PRODUCTION = "TEST_SENDER_STANDINS is refused in production: certification runs only"
    PLAIN_HTTP_DEPLOYED = "ECF_HOST and FC_HOST reach a local service over http in local and test only"
    WORKER_WITHOUT_ROOTS = "TRUSTED_ROOTS is required by the worker: it verifies counterparties"
    RECEIVER_WITHOUT_ROOTS = "TRUSTED_ROOTS is required by the receiver: it verifies what emitters send"


class SettingRefusedError(EcfError):
    """A setting the app's role or environment refuses: a defect of the deployment, named, never its value."""

    code: ClassVar[str] = "do_ecf_setting_refused"

    def __init__(self, refusal: Refusal) -> None:
        super().__init__(f"LEDGER_DO_ECF_{refusal.value}")


def _checked(settings: DoEcfSettings, context: ChannelContext) -> DgiiTimeouts:
    """DGII's waits from ``settings``, once they and the rest suit the app (see the module)."""
    if settings.test_sender_standins and context.environment == "production":
        raise SettingRefusedError(Refusal.STANDINS_IN_PRODUCTION)
    hosts = DgiiHosts(ecf=settings.ecf_host, fc=settings.fc_host)
    if hosts.beyond_loopback and context.environment not in _LOCAL_STACK:
        raise SettingRefusedError(Refusal.PLAIN_HTTP_DEPLOYED)
    timeouts = DgiiTimeouts(
        connect=settings.dgii_connect_seconds,
        total=settings.dgii_attempt_seconds,
        call=settings.dgii_call_seconds,
    )
    if SETTLE_DELAY.total_seconds() < timeouts.total:
        raise SettleTooShortError
    return timeouts


def assemble(context: ChannelContext) -> ChannelParts:
    """The channel, its routes, the worker's clients and its migrations (see the module)."""
    settings = context.section(DoEcfSettings)
    timeouts = _checked(settings, context)
    hosts = DgiiHosts(ecf=settings.ecf_host, fc=settings.fc_host)
    signer = DocumentSigner(context.ports.get(SIGNER_PORT))
    directory = context.ports.get(COMPANY_DIRECTORY)
    units = PgUnits(context.database)
    requiring = Requiring(
        units, signer, receiver=settings.receiver_public_url is not None, clock=context.clock
    )
    definition = ChannelDef.of(
        context.definition, regime=context.regime.key, fields=CHANNEL_FIELDS, labels=LABELS, sources=SOURCES
    )
    api = ranges.RangesApi(
        database=context.database,
        ranging=Ranging(units, DirectoryEnrollments(directory)),
        authentication=context.auth,
        permissions=directory,
    )
    archiving = context.role in ARCHIVING_ROLES
    archive = (
        PortArchive(context.ports.get(ARCHIVE_PORT), clock=context.clock) if archiving else UnboundArchive()
    )
    issuing = Issuing(
        units,
        Signing(DgiiXml(), signer, clock=context.clock, qr_hosts=hosts.qr, regime=context.regime),
        archive,
        SigningLog(context.telemetry),
        regime=context.regime,
    )
    outbound = _outbound(settings, context, signer, DgiiAccess(hosts=hosts, timeouts=timeouts))
    authority = unopened if outbound is None else outbound.authority
    buyers = unopened_buyers if outbound is None else outbound.deliveries
    emitters = unopened_emitters if outbound is None else outbound.approvals
    advancing = Advancing(units, issuing, archive, authority, AdvanceLog(context.telemetry))
    delivering = Delivering(units, archive, buyers, DeliveryLog(context.telemetry))
    approvals = CommercialApprovals(
        units,
        archive,
        ApprovalSigning(signatures=signer, xml=DgiiXml(), clock=context.clock),
        ApprovalSending(authority=authority, emitters=emitters, report=ApprovalLog(context.telemetry)),
    )
    representing = Representing(units, archive, PdfPrinting())
    channel = DoEcfChannel(definition, requiring, issuing, advancing, representing)
    credentials = ReceiverCredentials(context.database, clock=context.clock)
    jobs = (
        advance_job(advancing),
        deliver_job(delivering),
        approve_job(approvals),
        purge_job(credentials),
    )
    if not archiving:  # migrate: its revisions, nothing to serve, open or keep
        return ChannelParts(channel=channel, jobs=jobs, migrations=migrations())
    managed = deliveries.DeliveriesApi(
        database=context.database,
        delivering=delivering,
        authentication=context.auth,
        permissions=directory,
    )
    deciding = received.ReceivedApi(
        database=context.database, approvals=approvals, authentication=context.auth, permissions=directory
    )
    routers = (
        ranges.router(api),
        deliveries.router(managed),
        options.router(managed),
        received.router(deciding),
    )
    parts = ChannelParts(channel=channel, routers=routers, jobs=jobs, migrations=migrations())
    if context.role == "receiver":
        roots = settings.trusted_roots
        if roots is None:
            raise SettingRefusedError(Refusal.RECEIVER_WITHOUT_ROOTS)
        return _receiving(parts, context, roots, definition, issuing)
    if outbound is None:
        return parts
    return replace(parts, resources=(outbound.opened(),))


def _receiving(
    parts: ChannelParts, context: ChannelContext, roots: Path, definition: ChannelDef, issuing: Issuing
) -> ChannelParts:
    """The receiver's: its routes over the trusted roots it reads at start (``Inbound``), the parts' own."""
    settings = context.section(DoEcfSettings)
    inbound = Inbound(roots, standins=settings.test_sender_standins)
    units = PgUnits(context.database)
    archive = PortArchive(context.ports.get(ARCHIVE_PORT), clock=context.clock)
    api = receiver.ReceiverApi(
        definition=definition,
        directory=context.ports.get(COMPANY_DIRECTORY),
        credentials=ReceiverCredentials(context.database, clock=context.clock),
        inbound=inbound,
        receiving=Receiving(
            units,
            inbound,
            archive,
            DocumentSigner(context.ports.get(SIGNER_PORT)),
            clock=context.clock,
        ),
        report=ReceptionLog(context.telemetry),
        approving=Approving(units, inbound, archive, issuing, clock=context.clock),
        clock=context.clock,
    )
    return replace(parts, receiver_routers=(receiver.router(api),), resources=(inbound.opened(),))


def _outbound(
    settings: DoEcfSettings, context: ChannelContext, signer: DocumentSigner, access: DgiiAccess
) -> Outbound | None:
    """The worker's DGII and buyer clients (its trusted roots required); ``None`` in every other role."""
    if context.role != "worker":
        return None
    roots = settings.trusted_roots
    if roots is None:
        raise SettingRefusedError(Refusal.WORKER_WITHOUT_ROOTS)
    return Outbound(signer, http=context.http, access=access, roots=roots, clock=context.clock)


def _pack() -> ChannelPack:
    """The pack of the manifest's entry ``KEY``."""
    (entry,) = (entry for entry in MANIFEST.channels if entry.key == KEY)
    return ChannelPack(entry=entry, regime="do", assemble=assemble, settings=DoEcfSettings)


CHANNEL: Final = _pack()
"""What the entry point ``ledger.countries.channels: do-ecf-v1`` names (and its settings section's)."""
