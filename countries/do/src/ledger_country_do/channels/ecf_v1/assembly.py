"""do-ecf-v1's assembly (§3.10): what the apps make of the channel, and the pack its entry point names.

Every app builds the channel fiscal routes to (``DoEcfChannel``) over the channel's units, the signer's
port (``DocumentSigner``) and the archive, with its ``do_ecf.advance`` job (which reaches DGII through the
worker's client only: in any other role reaching it is a defect) and its ``do_ecf.deliver`` job (the worker's
buyer client likewise), and checks its settings: DGII's
hosts (``DgiiHosts``), an attempt no longer than the settle delay (``SettleTooShortError``), no test
stand-in in production. It reads the ports it needs from the app (fiscal-kit's keys): the signer, fiscal's
company directory (who may do what, and who is enrolled), the archive in the roles that archive (none: the
channel does not start there, since every document it signs is archived first; ``migrate`` reads none,
keeps nothing, and gets the channel's revisions alone). The ``api`` app mounts its routes (``/v1/do``: the
ranges, a delivery's retry, the delivery options); the
worker opens its DGII and buyer clients at start (their trusted roots, which it requires, read then); the
receiver requires the trusted roots too, and reads them at start the same way (the documents emitters send
it chain to them: ``inbound``).
``migrate`` runs its revisions; every app checks its schema is current. Documents are signed with the QR's
hosts of the DGII hosts configured (a test's fake's, or DGII's).
"""

from dataclasses import replace
from enum import StrEnum
from typing import ClassVar, Final

from ledger_country_do._manifest import MANIFEST
from ledger_country_do.channels.ecf_v1.adapters.archive import PortArchive, UnboundArchive
from ledger_country_do.channels.ecf_v1.adapters.channel import DoEcfChannel
from ledger_country_do.channels.ecf_v1.adapters.dgii.endpoints import DgiiHosts
from ledger_country_do.channels.ecf_v1.adapters.enrollments import DirectoryEnrollments
from ledger_country_do.channels.ecf_v1.adapters.http import DgiiTimeouts
from ledger_country_do.channels.ecf_v1.adapters.inbound import inbound_trust
from ledger_country_do.channels.ecf_v1.adapters.jobs import advance_job, deliver_job
from ledger_country_do.channels.ecf_v1.adapters.logs import AdvanceLog, DeliveryLog, SigningLog
from ledger_country_do.channels.ecf_v1.adapters.outbound import (
    DgiiAccess,
    Outbound,
    unopened,
    unopened_buyers,
)
from ledger_country_do.channels.ecf_v1.adapters.postgres.schema import migrations
from ledger_country_do.channels.ecf_v1.adapters.postgres.unit import PgUnits
from ledger_country_do.channels.ecf_v1.adapters.routes import deliveries, options, ranges
from ledger_country_do.channels.ecf_v1.adapters.signing.port import DocumentSigner
from ledger_country_do.channels.ecf_v1.adapters.xml.documents import DgiiXml
from ledger_country_do.channels.ecf_v1.application.advancing import Advancing, SettleTooShortError
from ledger_country_do.channels.ecf_v1.application.delivering import Delivering
from ledger_country_do.channels.ecf_v1.application.issuing import Issuing
from ledger_country_do.channels.ecf_v1.application.ranges import Ranging
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
lazy from ledger_platform.runtime.contexts import ChannelContext

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
    advancing = Advancing(units, issuing, archive, authority, AdvanceLog(context.telemetry))
    delivering = Delivering(units, archive, buyers, DeliveryLog(context.telemetry))
    channel = DoEcfChannel(definition, requiring, issuing, advancing)
    jobs = (advance_job(advancing), deliver_job(delivering))
    if not archiving:  # migrate: its revisions, nothing to serve, open or keep
        return ChannelParts(channel=channel, jobs=jobs, migrations=migrations())
    managed = deliveries.DeliveriesApi(
        database=context.database,
        delivering=delivering,
        authentication=context.auth,
        permissions=directory,
    )
    routers = (ranges.router(api), deliveries.router(managed), options.router(managed))
    parts = ChannelParts(channel=channel, routers=routers, jobs=jobs, migrations=migrations())
    if context.role == "receiver":
        roots = settings.trusted_roots
        if roots is None:
            raise SettingRefusedError(Refusal.RECEIVER_WITHOUT_ROOTS)
        return replace(parts, resources=(inbound_trust(roots),))
    if outbound is None:
        return parts
    return replace(parts, resources=(outbound.opened(),))


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
