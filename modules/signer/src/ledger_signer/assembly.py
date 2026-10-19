"""signer's assembly (§3.10): what the apps mount of it.

signer is isolated in the signer's role (``[data] isolated_in``, ADR 0010): only the ``signer`` app will run
its gRPC service, its consumer, its jobs and its schedules; every other app assembles it as a client, its API
and ``SignerPort`` reaching the signer over gRPC. Until that client and that service exist, every role binds a
stand-in that answers ``signer_unavailable`` (``adapters.unavailable``), and ``migrate`` runs its revisions.
No role hands a readiness check that calls the signer: no app's readiness depends on it (spec §3.7).
"""

from typing import Final

from ledger_fiscal_kit.ports import SIGNER_PORT
from ledger_platform.runtime import Module, ModuleParts
from ledger_signer._manifest import MANIFEST
from ledger_signer.adapters.postgres.schema import MIGRATIONS, migrations
from ledger_signer.adapters.settings import SignerSettings
from ledger_signer.adapters.unavailable import UnavailableSigner
from ledger_signer.api import SIGNER_API
lazy from ledger_platform.runtime import ModuleContext


def assemble(context: ModuleContext) -> ModuleParts:
    """The API and ``SignerPort`` (stand-ins until the client and the facade exist), and the migrations."""
    del context
    unavailable = UnavailableSigner()
    return ModuleParts(
        api=SIGNER_API.bind(unavailable),
        provides=(SIGNER_PORT.bind(unavailable),),
        migrations=migrations(),
    )


MODULE: Final = Module(manifest=MANIFEST, assemble=assemble, settings=SignerSettings, migrations=MIGRATIONS)
"""What the entry point ``ledger.modules: signer`` names (Decision 13)."""
