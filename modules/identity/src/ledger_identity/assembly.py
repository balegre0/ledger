"""identity's assembly (§3.10): what the apps mount of it.

The ``api`` app mounts its routers, ``migrate`` runs its revisions, and every app checks its schema is
current (readiness). It publishes events and consumes none, and runs no job. Tax ids are read with the
schemes of the installed regimes: identity names no country, and is not ready while no regime is installed
(readiness ``identity:regimes``): it could read no tax id, and every company registration would be refused.
"""

from typing import ClassVar, Final

from ledger_identity._manifest import MANIFEST
from ledger_identity.adapters.http import companies, establishments, memberships, organization
from ledger_identity.adapters.http.context import ApiContext
from ledger_identity.adapters.postgres.schema import migrations
from ledger_kernel.errors import UnavailableError
from ledger_platform.runtime import Module, ModuleParts
lazy from ledger_platform.runtime import ModuleContext


class NoRegimeInstalledError(UnavailableError):
    """No country registers a regime in this installation: no tax id can be read (a deployment's defect)."""

    code: ClassVar[str] = "regime_not_installed"

    def __init__(self) -> None:
        super().__init__("no regime is installed: tax ids cannot be read")


def assemble(context: ModuleContext) -> ModuleParts:
    """The routers ``api`` mounts and the migrations ``migrate`` runs, of identity."""
    api = ApiContext.build(
        database=context.database,
        authentication=context.auth,
        regimes=context.regimes,
    )
    routers = (
        organization.router(api),
        companies.router(api),
        establishments.router(api),
        memberships.router(api),
    )

    async def regimes() -> None:
        """Ready once at least one regime is installed (``NoRegimeInstalledError`` otherwise)."""
        if not context.regimes.keys():
            raise NoRegimeInstalledError

    return ModuleParts(routers=routers, checks={"regimes": regimes}, migrations=migrations())


MODULE: Final = Module(manifest=MANIFEST, assemble=assemble)
"""What the entry point ``ledger.modules: identity`` names (Decision 13)."""
