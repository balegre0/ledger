"""fiscal's assembly (§3.10): what the apps mount of it.

The ``api`` app mounts its routes, ``migrate`` runs its revisions, and every app checks its schema is current
(readiness). Every app binds its API (``FiscalApi``) for the modules that depend on it, and provides
fiscal-kit's ``CompanyDirectory`` to the channels (Decision 4): which company a tax id names on a channel's
environment, a company's names, and what a principal may do in it. The channels are read at call time
(``ChannelRegistry`` over the app's assembled channels), never while fiscal assembles. It publishes
``fiscal.enrollment.changed`` and consumes nothing yet (Tasks 20 and 25 add its consumers).
"""

from typing import Final

from ledger_fiscal._manifest import MANIFEST
from ledger_fiscal.adapters.http import enrollments
from ledger_fiscal.adapters.http.context import ApiContext
from ledger_fiscal.adapters.postgres.schema import MIGRATIONS, migrations
from ledger_fiscal.adapters.postgres.unit import PgUnits
from ledger_fiscal.api import FISCAL_API
from ledger_fiscal.application.directory import FiscalDirectory
from ledger_fiscal.application.enrolling import Enrolling
from ledger_fiscal.application.facade import FiscalFacade
from ledger_fiscal_kit.channels import ChannelRegistry
from ledger_fiscal_kit.ports import COMPANY_DIRECTORY
from ledger_identity.api import IDENTITY_API
from ledger_platform.runtime import Module, ModuleParts
lazy from ledger_platform.runtime import ModuleContext


def assemble(context: ModuleContext) -> ModuleParts:
    """The routes ``api`` mounts, the migrations ``migrate`` runs, fiscal's API and its directory."""
    units = PgUnits(context.database)
    channels = ChannelRegistry(context.channels)  # read at call time: filled once the channels assembled
    directory = FiscalDirectory(identity=context.apis.get(IDENTITY_API), units=units, channels=channels)
    enrolling = Enrolling(units, channels)
    api = ApiContext.build(
        database=context.database,
        authentication=context.auth,
        permissions=directory,
        enrolling=enrolling,
    )
    return ModuleParts(
        api=FISCAL_API.bind(FiscalFacade(units)),
        provides=(COMPANY_DIRECTORY.bind(directory),),
        routers=(enrollments.router(api),),
        migrations=migrations(),
    )


MODULE: Final = Module(manifest=MANIFEST, assemble=assemble, migrations=MIGRATIONS)
"""What the entry point ``ledger.modules: fiscal`` names (Decision 13)."""
