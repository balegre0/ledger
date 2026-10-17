"""invoicing's assembly (§3.10): what the apps mount of it.

The ``api`` app mounts its routers, ``migrate`` runs its revisions, and every app checks its schema is
current (readiness).
"""

from typing import Final

from ledger_invoicing._manifest import MANIFEST
from ledger_invoicing.adapters.postgres.schema import migrations
from ledger_platform.runtime import Module, ModuleParts
lazy from ledger_platform.runtime import ModuleContext


def assemble(context: ModuleContext) -> ModuleParts:
    """The routers ``api`` mounts and the migrations ``migrate`` runs, of invoicing."""
    del context
    return ModuleParts(migrations=migrations())


MODULE: Final = Module(manifest=MANIFEST, assemble=assemble)
"""What the entry point ``ledger.modules: invoicing`` names (Decision 13)."""
