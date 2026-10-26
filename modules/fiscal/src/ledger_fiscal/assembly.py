"""fiscal's assembly (§3.10): what the apps mount of it.

The ``api`` app mounts its routes, ``migrate`` runs its revisions, and every app checks its schema is current
(readiness). Every app binds its API (``FiscalApi``) for the modules that depend on it, provides
fiscal-kit's ``CompanyDirectory`` to the channels (Decision 4), and implements invoicing's ``IssuePolicy``
and ``Renderer`` (ADR 0011): enrollments are checked and documents routed to the channels, numbered within
the request's budget and mirrored in invoicing. The worker runs its consumers (the recovery path, the status
mirror, the channels' requirements, the certificates installed) and its ``fiscal.route`` job. The channels
are read at call time (``ChannelRegistry`` over the app's assembled channels), never while fiscal assembles.
"""

from typing import Final

from ledger_fiscal._manifest import MANIFEST
from ledger_fiscal.adapters.http import enrollments
from ledger_fiscal.adapters.http.context import ApiContext
from ledger_fiscal.adapters.postgres.schema import MIGRATIONS, migrations
from ledger_fiscal.adapters.postgres.unit import ROUTE, PgUnits
from ledger_fiscal.adapters.worker import certificate_installed, requirements_changed, route_job
from ledger_fiscal.api import FISCAL_API
from ledger_fiscal.application.certificates import CERTIFICATES, Certificates
from ledger_fiscal.application.directory import FiscalDirectory
from ledger_fiscal.application.enrolling import Enrolling
from ledger_fiscal.application.facade import FiscalFacade
from ledger_fiscal.application.issuing import FiscalPolicy, Router
from ledger_fiscal.application.mirroring import (
    CHANNEL_DOCUMENTS,
    COMMERCIAL_DOCUMENTS,
    REQUIREMENTS,
    Consumers,
)
from ledger_fiscal.application.rendering import FiscalRenderer
from ledger_fiscal_kit.channels import ChannelRegistry
from ledger_fiscal_kit.ports import COMPANY_DIRECTORY
from ledger_identity.api import IDENTITY_API
from ledger_invoicing.api import INVOICING_API, ISSUE_POLICY, RENDERER
from ledger_platform.events.consumers import ConsumerSpec
from ledger_platform.jobs.outcomes import JobSpec
from ledger_platform.runtime import Module, ModuleParts
lazy from ledger_platform.runtime import ModuleContext


def assemble(context: ModuleContext) -> ModuleParts:
    """What fiscal hands the app (see the module).

    Its routes, migrations, API and directory, its implementations of invoicing's points, its consumers and
    its job.
    """
    units = PgUnits(context.database)
    channels = ChannelRegistry(context.channels)  # read at call time: filled once the channels assembled
    identity = context.apis.get(IDENTITY_API)
    invoicing = context.apis.get(INVOICING_API)
    enrolling = Enrolling(units, channels, identity=identity, regimes=context.regimes, clock=context.clock)
    directory = FiscalDirectory(identity=identity, units=units, channels=channels)
    router = Router(
        units=units,
        channels=channels,
        invoicing=invoicing,
        enrolling=enrolling,
        regimes=context.regimes,
        clock=context.clock,
    )
    consumers = Consumers(router=router, invoicing=invoicing, channels=channels)
    certificates = Certificates(units=units, channels=channels)
    api = ApiContext.build(
        database=context.database,
        authentication=context.auth,
        permissions=directory,
        enrolling=enrolling,
    )
    return ModuleParts(
        api=FISCAL_API.bind(FiscalFacade(units)),
        implements=(
            ISSUE_POLICY.bind(FiscalPolicy(router, regimes=context.regimes)),
            RENDERER.bind(FiscalRenderer(units, channels)),
        ),
        provides=(COMPANY_DIRECTORY.bind(directory),),
        routers=(enrollments.router(api),),
        consumers=(
            ConsumerSpec(name=COMMERCIAL_DOCUMENTS, outside=consumers.on_issued),
            ConsumerSpec(name=CHANNEL_DOCUMENTS, outside=consumers.on_channel_document),
            ConsumerSpec(name=REQUIREMENTS, handler=requirements_changed()),
            ConsumerSpec(name=CERTIFICATES, outside=certificate_installed(certificates)),
        ),
        jobs=(JobSpec(kind=ROUTE, handler=route_job(router)),),
        migrations=migrations(),
    )


MODULE: Final = Module(manifest=MANIFEST, assemble=assemble, migrations=MIGRATIONS)
"""What the entry point ``ledger.modules: fiscal`` names (Decision 13)."""
