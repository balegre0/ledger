"""identity's composition root: settings, adapters and interfaces wired into one chassis ``Service``."""

from importlib.metadata import version
from typing import Final
lazy from collections.abc import Sequence
lazy from contextlib import AbstractAsyncContextManager

from prometheus_client import CollectorRegistry
lazy from fastapi import APIRouter, FastAPI

from ledger_identity.adapters.events import IDENTITY
from ledger_identity.adapters.permissions import MembershipPermissions
from ledger_identity.adapters.postgres.migrate import MIGRATIONS
from ledger_identity.adapters.postgres.unit import bind
from ledger_identity.api.context import ApiContext
from ledger_identity.api.rest import routes
from ledger_identity.api.rpc import services
from ledger_identity.settings import IdentitySettings
from ledger_platform.auth.tokens import HttpKeys, PublishedKeys, TokenPolicy, TokenVerifier
from ledger_platform.idempotency import Idempotency
from ledger_platform.service import Events, Interfaces, Service
lazy from ledger_platform.service import Runtime, RuntimeDependency

VERSION: Final = version("ledger-identity")


def build(settings: IdentitySettings) -> Service[IdentitySettings]:
    """The identity service ``settings`` describe: built, not started (its app's lifespan starts it).

    Its resource is the HTTP client that fetches the identity provider's keys, open while the service runs;
    the age of the keys it fetched is a metric of the service. Its own readiness check, ``migrations``: the
    database is at the revision this version's migrations end at, or at a newer one (``SchemaReadiness``):
    a new version on a database not yet migrated (or never migrated) stays out of rotation, while an old one
    keeps serving through a rollout.
    """
    policy = TokenPolicy.of(settings)
    registry = CollectorRegistry()
    keys = HttpKeys()
    verifier = TokenVerifier(policy, keys=PublishedKeys(policy, fetch=keys, registry=registry))
    idempotency = Idempotency()
    schema = MIGRATIONS.readiness()

    def http(runtime: RuntimeDependency[IdentitySettings]) -> Sequence[APIRouter]:
        context = ApiContext.build(
            runtime=runtime,
            verifier=verifier,
            bind=bind,
            permissions=MembershipPermissions,
            idempotency=idempotency,
        )
        return routes(context)

    def resources(running: IdentitySettings) -> AbstractAsyncContextManager[HttpKeys]:
        del running
        return keys.opened()

    async def migrations_applied(runtime: Runtime[IdentitySettings]) -> None:
        await schema(runtime.database)

    return Service(
        settings=settings,
        version=VERSION,
        interfaces=Interfaces(
            http=http,
            grpc=services(bind),
            resources=resources,
            checks={"migrations": migrations_applied},
            registry=registry,
        ),
        events=Events(streams=(IDENTITY,)),
    )


def create_app() -> FastAPI:
    """Granian's factory (``--factory``): the app of the service the ``LEDGER_*`` environment configures."""
    return build(IdentitySettings()).app
