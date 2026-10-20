"""signer's assembly (§3.10): what the apps mount of it.

signer is isolated in the signer's role (``[data] isolated_in``, ADR 0010): only the ``signer`` app runs its
gRPC service, its consumer, its jobs and its schedules. There it checks its callers' tokens and its KMS before
anything starts (``adapters.serving``), binds its facade as ``SignerApi`` and ``SignerPort``, hands the gRPC
server as a resource (``LEDGER_SIGNER_GRPC_PORT``, traced by the app's tracer), the consumer
``signer.company_deactivations``, the jobs ``signer.expiry_notice`` and its own purges, and the readiness
checks ``grpc`` (the service serves) and ``kms`` (the KMS answers). What only the signer's role uses is
imported lazily: in any other role custody is never loaded, not even its modules.

Every client role (``api``, ``worker``, ``receiver``) reaches it over gRPC: given the signer's address and its
own token (``LEDGER_SIGNER_ADDRESS``, ``LEDGER_SIGNER_TOKEN_<ROLE>``), it binds one ``SignerClient`` as
``SignerApi`` and ``SignerPort`` and hands it as a resource (its channel opened at start, closed at stop). It
reads its own role's token, never another caller's. Without either, it binds the stand-ins answering
``signer_unavailable`` and logs ``signer.not_configured`` once: nothing fails at the start (Decision 13). No
client role hands a readiness check: no app's readiness depends on the signer (spec §3.7); none hands a
consumer or a job (only the signer's role runs them). ``migrate`` runs its revisions.
"""

from typing import Final

from ledger_fiscal_kit.ports import SIGNER_PORT
from ledger_platform.events.consumers import ConsumerSpec
from ledger_platform.grpc.server import ServerOptions
from ledger_platform.jobs.maintenance import maintenance_jobs
from ledger_platform.jobs.outcomes import JobSpec
from ledger_platform.runtime import Module, ModuleParts
from ledger_signer._manifest import MANIFEST
from ledger_signer.adapters.grpc.client import SignerClient
from ledger_signer.adapters.postgres.schema import MIGRATIONS, migrations
from ledger_signer.adapters.settings import SignerSettings
from ledger_signer.adapters.unavailable import UnavailableSigner
from ledger_signer.api import SIGNER_API
lazy from ledger_platform.runtime import ModuleContext
lazy from ledger_signer.adapters.grpc.server import SignerServer
lazy from ledger_signer.adapters.serving import (
    build,
    caller_tokens,
    expiry_notices,
    grpc_port,
    local_kms,
    retire_deactivated,
)
lazy from ledger_signer.adapters.settings import Caller
lazy from ledger_signer.application.notices import EXPIRY_NOTICE

COMPANY_DEACTIVATIONS: Final = "signer.company_deactivations"
PURGES: Final = ("signer.purge_idempotency_keys", "signer.purge_done_jobs")
"""signer's own purges of its schema (Decision 11): no other role's login enters it."""


def _client(settings: SignerSettings, caller: Caller) -> SignerClient | None:
    """``caller``'s client of the signer, with its own token; ``None`` without the address or the token."""
    address, token = settings.address, settings.token_of(caller)
    if address is None or token is None:
        return None
    return SignerClient(
        address, token, deadline=settings.deadline_seconds, import_deadline=settings.import_deadline_seconds
    )


def _signer(context: ModuleContext) -> ModuleParts:
    """The signer's own role: its checks first, then its service, consumer, jobs and readiness."""
    settings = context.section(SignerSettings)
    callers = caller_tokens(settings)
    kms = local_kms(settings, context.environment)
    signer = build(context.database, settings=settings, kms=kms, entity=None, clock=context.clock)
    server = SignerServer(
        signer.servicer,
        port=grpc_port(settings, context.environment),
        registry=context.telemetry.registry,
        callers=callers,
        options=ServerOptions(tracer=context.telemetry.tracer),
    )
    return ModuleParts(
        api=SIGNER_API.bind(signer.facade),
        provides=(SIGNER_PORT.bind(signer.facade),),
        consumers=(ConsumerSpec(name=COMPANY_DEACTIVATIONS, handler=retire_deactivated),),
        jobs=(
            JobSpec(kind=EXPIRY_NOTICE, handler=expiry_notices(signer.notices)),
            *maintenance_jobs((context.database,), kinds=PURGES),
        ),
        checks={"grpc": server.ready, "kms": kms.ready},
        resources=(server,),
        migrations=migrations(),
    )


def assemble(context: ModuleContext) -> ModuleParts:
    """The signer in its own role; its client in a configured client role, the stand-ins otherwise."""
    match context.role:
        case "signer":
            return _signer(context)
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
