"""identity's assembly (§3.10): what the apps mount of it.

The ``api`` app mounts its routers, ``migrate`` runs its revisions, and every app checks its schema is
current (readiness). It publishes events and consumes none, and runs no job. Tax ids are read with the
schemes of the installed regimes: identity names no country.
"""

from typing import Final

from ledger_identity._manifest import MANIFEST
from ledger_identity.adapters.http import branches, companies, emission_points, memberships, organization
from ledger_identity.adapters.http.context import ApiContext
from ledger_identity.adapters.postgres.schema import migrations
from ledger_platform.runtime import Module, ModuleParts
lazy from ledger_platform.runtime import ModuleContext


def assemble(context: ModuleContext) -> ModuleParts:
    """The routers ``api`` mounts and the migrations ``migrate`` runs, of identity."""
    api = ApiContext.build(
        database=context.database,
        authentication=context.auth,
        tax_ids=context.regimes.tax_id_schemes,
    )
    routers = (
        organization.router(api),
        companies.router(api),
        branches.router(api),
        emission_points.router(api),
        memberships.router(api),
    )
    return ModuleParts(routers=routers, migrations=migrations())


MODULE: Final = Module(manifest=MANIFEST, assemble=assemble)
"""What the entry point ``ledger.modules: identity`` names (Decision 13)."""
