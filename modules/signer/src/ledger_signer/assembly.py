"""signer's assembly (§3.10): what the apps mount of it.

signer is isolated in the signer's role (``[data] isolated_in``, ADR 0010): only the ``signer`` app will run
its gRPC service, its consumer, its jobs and its schedules. Every client role (``api``, ``worker``,
``receiver``) reaches it over gRPC: given the signer's address and its own token (``LEDGER_SIGNER_ADDRESS``,
``LEDGER_SIGNER_TOKEN_<ROLE>``), it binds one ``SignerClient`` as ``SignerApi`` and ``SignerPort`` and hands
it as a resource (its channel opened at start, closed at stop). It reads its own role's token, never another
caller's. Without either, it binds the stand-ins answering ``signer_unavailable`` and logs
``signer.not_configured`` once: nothing fails at the start (Decision 13). Until the signer's service exists,
the signer's role binds the stand-ins too; ``migrate`` runs its revisions. No role hands a readiness check
that calls the signer: no app's readiness depends on it (spec §3.7).
"""

from typing import Final

from ledger_fiscal_kit.ports import SIGNER_PORT
from ledger_platform.runtime import Module, ModuleParts
from ledger_signer._manifest import MANIFEST
from ledger_signer.adapters.grpc.client import SignerClient
from ledger_signer.adapters.postgres.schema import MIGRATIONS, migrations
from ledger_signer.adapters.settings import SignerSettings
from ledger_signer.adapters.unavailable import UnavailableSigner
from ledger_signer.api import SIGNER_API
lazy from ledger_platform.runtime import ModuleContext
lazy from ledger_signer.adapters.settings import Caller


def _client(settings: SignerSettings, caller: Caller) -> SignerClient | None:
    """``caller``'s client of the signer, with its own token; ``None`` without the address or the token."""
    address, token = settings.address, settings.token_of(caller)
    if address is None or token is None:
        return None
    return SignerClient(address, token, deadline=settings.deadline_seconds)


def assemble(context: ModuleContext) -> ModuleParts:
    """The client of the signer in a configured client role, the stand-ins otherwise; the migrations."""
    match context.role:
        case "api" | "worker" | "receiver" as caller:
            client = _client(context.section(SignerSettings), caller)
            if client is not None:
                return ModuleParts(
                    api=SIGNER_API.bind(client),
                    provides=(SIGNER_PORT.bind(client),),
                    resources=(client,),
                    migrations=migrations(),
                )
            context.telemetry.logger.warning("signer.not_configured", role=caller)
        case _:
            pass
    unavailable = UnavailableSigner()
    return ModuleParts(
        api=SIGNER_API.bind(unavailable),
        provides=(SIGNER_PORT.bind(unavailable),),
        migrations=migrations(),
    )


MODULE: Final = Module(manifest=MANIFEST, assemble=assemble, settings=SignerSettings, migrations=MIGRATIONS)
"""What the entry point ``ledger.modules: signer`` names (Decision 13)."""
