"""``App``'s assembly (§3.10): modules in dependency order, then channels; what they hand back is checked."""

import asyncio
import contextlib
import time
from contextlib import asynccontextmanager
from datetime import date
from decimal import Decimal
from functools import partial
from typing import ClassVar, Final, Protocol, TypeIs, final, runtime_checkable
lazy from collections.abc import AsyncGenerator, Callable, Mapping, Sequence
lazy from pathlib import Path

import pytest
from pydantic import SecretStr
from pydantic_settings import SettingsConfigDict
from structlog.testing import capture_logs

from ledger_kernel.dated import Since, Timeline
from ledger_kernel.jurisdiction import CountryCode, Jurisdiction
from ledger_kernel.money import Currency, RoundingMode
from ledger_kernel.regimes import Locale, RegimeDef, RegimeKey, RoundingPoint, RoundingRule, Source
from ledger_platform.auth.tokens import TokenSettingsMissingError
from ledger_platform.blobs.archive import ARCHIVE_PORT_NAME
from ledger_platform.config.manifest import ChannelEntry, load_manifest
from ledger_platform.config.settings import PackageSettings, Settings, section_prefix
from ledger_platform.data.database import Database
from ledger_platform.data.migrations import SchemaMigrations
from ledger_platform.events.consumers import ConsumerMismatchError, ConsumerSpec
from ledger_platform.jobs.maintenance import maintenance_jobs
from ledger_platform.jobs.outcomes import Done, JobSpec
from ledger_platform.runtime import app as runtime_app
from ledger_platform.runtime import assembly as runtime_assembly
from ledger_platform.runtime.app import (
    STOP,
    STOP_BUDGET,
    App,
    AppAlreadyStartedError,
    DirectUrlMissingError,
    PoolTooSmallError,
    RoleMismatchError,
    RoleNotBuiltError,
    SchemaUnknownError,
    StopTimes,
)
from ledger_platform.runtime.archiving import StoreArchive
from ledger_platform.runtime.assembly import (
    ConsumerNotProvidedError,
    ConsumerUndeclaredError,
    CronScheduleError,
    DuplicateSchemaError,
    ForeignSchemaError,
    HandlerDuplicateError,
    IsolatedModuleMissingError,
    IsolatedPartsError,
    JobNotProvidedError,
    JobUndeclaredError,
    MigrationsMissingError,
    ModuleCycleError,
    ModuleMissingError,
    PortReservedError,
    SchemaMissingError,
)
from ledger_platform.runtime.contexts import (
    ChannelPack,
    ChannelParts,
    ChannelsNotAssembledError,
    Module,
    ModuleParts,
    SectionMissingError,
)
from ledger_platform.runtime.facade import UnguardedFacadeError, facade
from ledger_platform.runtime.keys import (
    Binding,
    BindingMissingError,
    Key,
    UndeclaredDependencyError,
)
from ledger_platform.runtime.registries import CHANNELS, MODULES, REGIMES, Discovered
from ledger_platform.runtime.sections import (
    InvalidSettingsError,
    SettingsNotProvidedError,
    SettingsPrefixError,
    SettingsUndeclaredError,
)
from ledger_testing.waiting import Watched
lazy from ledger_platform.config.manifest import AppRole
lazy from ledger_platform.data.bound import BoundDatabase
lazy from ledger_platform.data.unit_of_work import UnitOfWork
lazy from ledger_platform.events.consumers import RawEvent
lazy from ledger_platform.jobs.outcomes import Job, JobOutcome
lazy from ledger_platform.runtime import Environment
lazy from ledger_platform.runtime.assembly import Member
lazy from ledger_platform.runtime.contexts import AssembledChannels, ChannelContext, ModuleContext
lazy from ledger_platform.runtime.registries import Discover

URL: Final = "postgresql+psycopg://ledger_app:app@db.invalid:5432/ledger"
DIRECT: Final = "postgresql+psycopg://ledger_migrator:migrator@db.invalid:5432/ledger"
SETTINGS: Final = Settings(
    environment="test", database_url=SecretStr(URL), database_direct_url=SecretStr(DIRECT)
)
"""No token settings: enough for every role but ``api``."""
BARE: Final = SETTINGS.model_copy(update={"database_direct_url": None})
"""No direct URL either: the worker and migrate refuse it."""
TOKENS: Final = SETTINGS.model_copy(
    update={
        "token_issuer": "https://issuer.invalid",
        "token_audience": "ledger",
        "token_jwks_url": "https://issuer.invalid/keys",
    }
)
LABELS: Final[Mapping[Locale, str]] = {Locale("en"): "Probe"}


@runtime_checkable
class BetaApi(Protocol):
    async def greet(self, name: str) -> str: ...


@runtime_checkable
class ProbePort(Protocol):
    async def sign(self, payload: bytes) -> bytes: ...


@runtime_checkable
class Policy(Protocol):
    async def allows(self, document: str) -> bool: ...


def is_beta_api(value: object) -> TypeIs[BetaApi]:
    return isinstance(value, BetaApi)


def is_probe_port(value: object) -> TypeIs[ProbePort]:
    return isinstance(value, ProbePort)


def is_policy(value: object) -> TypeIs[Policy]:
    return isinstance(value, Policy)


BETA_API: Final = Key[BetaApi]("beta", is_beta_api)
PROBE_PORT: Final = Key[ProbePort]("ProbePort", is_probe_port)
BETA_POLICY: Final = Key[Policy]("beta.Policy", is_policy)


@final
class Beta:
    @facade
    async def greet(self, name: str) -> str:
        return f"hello {name}"


@final
class Signer:
    @facade
    async def sign(self, payload: bytes) -> bytes:
        return payload


@final
class UnguardedSigner:
    async def sign(self, payload: bytes) -> bytes:
        return payload


async def record(event: RawEvent, unit: UnitOfWork) -> None:
    del event, unit


async def export(job: Job) -> JobOutcome:
    del job
    return Done()


def manifest_text(
    name: str,
    body: str = "",
    *,
    schema: str | None = None,
    depends: Sequence[str] = (),
    isolated: bool = False,
) -> str:
    schema_name = name if schema is None else schema
    modules = ", ".join(f'"{module}"' for module in depends)
    isolation = 'isolated_in = "signer"\n' if isolated else ""
    return (
        f'[package]\nname = "{name}"\nkind = "module"\nsummary = "A probe."\nowners = ["@o"]\n\n'
        f'[dependencies]\npackages = ["kernel", "platform"]\nmodules = [{modules}]\n\n'
        f'[data]\nschema = "{schema_name}"\n{isolation}\n{body}'
    )


def migrations(schema: str) -> SchemaMigrations:
    """Revisions never run by these tests: assembly does no I/O."""
    return SchemaMigrations(schema=schema, scripts="probe_unused:migrations")


def module(
    name: str,
    assemble: Callable[[ModuleContext], ModuleParts] | None = None,
    body: str = "",
    *,
    schema: str | None = None,
    depends: Sequence[str] = (),
) -> Module:
    def parts_only(context: ModuleContext) -> ModuleParts:
        return ModuleParts(migrations=migrations(context.database.schema))

    return Module(
        manifest=load_manifest(manifest_text(name, body, schema=schema, depends=depends)),
        assemble=parts_only if assemble is None else assemble,
    )


def same(value: object) -> object:
    return value


def discovering(
    *modules: Module, regimes: Sequence[RegimeDef] = (), packs: Sequence[ChannelPack] = ()
) -> Discover:
    groups: Mapping[str, tuple[Discovered, ...]] = {
        MODULES: tuple(Discovered(found.manifest.package.name, partial(same, found)) for found in modules),
        REGIMES: tuple(Discovered(found.key, partial(same, found)) for found in regimes),
        CHANNELS: tuple(Discovered(found.key, partial(same, found)) for found in packs),
    }

    def discover(group: str) -> Sequence[Discovered]:
        return groups[group]

    return discover


def us_regime() -> RegimeDef:
    return RegimeDef(
        key=RegimeKey("us"),
        jurisdiction=Jurisdiction(CountryCode("US"), None),
        currency=Currency.of("USD"),
        time_zone="America/New_York",
        labels=LABELS,
        tax_id_schemes=(),
        tax_categories=(),
        document_types=(),
        rounding=RoundingRule(
            mode=RoundingMode.HALF_UP,
            points=frozenset({RoundingPoint.LINE}),
            places=2,
            line_tolerance=Decimal("0.01"),
        ),
        fields=(),
        parameters={"threshold": Timeline(Since(date(2020, 1, 1), Decimal(1000)))},
        sources=(
            Source(
                title="Probe law", url="https://law.invalid", version="1", retrieved_on=date(2026, 10, 13)
            ),
        ),
    )


def channel_entry(schema: str = "us_probe", *, settings: bool = False) -> ChannelEntry:
    return ChannelEntry.model_validate(
        {
            "settings": settings,
            "key": "us-probe-v1",
            "entry_point": "ledger_country_us.channels.probe:PACK",
            "schema": schema,
            "valid_from": date(2026, 1, 1),
            "environments": ["test"],
            "document_kinds": ["invoice"],
            "directions": ["sales"],
            "tier": "bronze",
        }
    )


def beta(context: ModuleContext) -> ModuleParts:
    return ModuleParts(api=BETA_API.bind(Beta()), migrations=migrations(context.database.schema))


def test_modules_assemble_in_dependency_order_and_read_only_declared_apis() -> None:
    greeted: list[str] = []

    def alpha(context: ModuleContext) -> ModuleParts:
        greeted.append(type(context.apis.get(BETA_API)).__name__)
        return ModuleParts(migrations=migrations(context.database.schema))

    app = App(
        "worker",
        settings=SETTINGS,
        discover=discovering(module("alpha", alpha, depends=["beta"]), module("beta", beta)),
    )
    assert [manifest.package.name for manifest in app.modules] == ["beta", "alpha"]
    assert greeted == ["Beta"]
    assert app.bound("alpha").schema == "alpha"
    assert app.bound("platform").schema == "platform"
    with pytest.raises(SchemaUnknownError, match="'nowhere'"):
        app.bound("nowhere")

    with pytest.raises(UndeclaredDependencyError, match="'beta'"):
        App("worker", settings=SETTINGS, discover=discovering(module("gamma", alpha), module("beta", beta)))


def test_a_dependency_must_be_installed_and_dependencies_never_loop() -> None:
    with pytest.raises(ModuleMissingError, match="'beta'"):
        App("worker", settings=SETTINGS, discover=discovering(module("alpha", depends=["beta"])))
    looping = discovering(module("alpha", depends=["beta"]), module("beta", depends=["alpha"]))
    with pytest.raises(ModuleCycleError):
        App("worker", settings=SETTINGS, discover=looping)


CONSUMES: Final = '[[events.consumes]]\nconsumer = "probe.echo"\ntypes = ["probe.thing.renamed"]\n'
JOBS: Final = '[[jobs]]\nkind = "probe.export"\n'


def consuming(context: ModuleContext) -> ModuleParts:
    return ModuleParts(
        consumers=(ConsumerSpec(name="probe.echo", handler=record),),
        migrations=migrations(context.database.schema),
    )


def exporting(context: ModuleContext) -> ModuleParts:
    return ModuleParts(
        jobs=(JobSpec(kind="probe.export", handler=export),), migrations=migrations(context.database.schema)
    )


def twice(context: ModuleContext) -> ModuleParts:
    spec = ConsumerSpec(name="probe.echo", handler=record)
    return ModuleParts(consumers=(spec, spec), migrations=migrations(context.database.schema))


@pytest.mark.parametrize(
    ("probe", "error"),
    [
        (module("probe", consuming), ConsumerUndeclaredError),
        (module("probe", body=CONSUMES), ConsumerNotProvidedError),
        (module("probe", exporting), JobUndeclaredError),
        (module("probe", body=JOBS), JobNotProvidedError),
        (module("probe", twice, CONSUMES), HandlerDuplicateError),
    ],
    ids=[
        "consumer-undeclared",
        "consumer-not-provided",
        "job-undeclared",
        "job-not-provided",
        "consumer-twice",
    ],
)
def test_a_consumer_or_job_must_be_declared_and_provided(probe: Module, error: type[Exception]) -> None:
    with pytest.raises(error, match=r"probe\.(echo|export)"):
        App("worker", settings=SETTINGS, discover=discovering(probe))


def test_one_consumer_name_in_two_modules_is_refused() -> None:
    """Each consumer runs as a worker named after it: two of one name could not be told apart."""
    with pytest.raises(HandlerDuplicateError, match=r"consumer 'probe\.echo'"):
        App(
            "worker",
            settings=SETTINGS,
            discover=discovering(module("probe", consuming, CONSUMES), module("other", consuming, CONSUMES)),
        )


def test_declared_and_provided_consumers_and_jobs_assemble() -> None:
    def both(context: ModuleContext) -> ModuleParts:
        return ModuleParts(
            consumers=(ConsumerSpec(name="probe.echo", handler=record),),
            jobs=(JobSpec(kind="probe.export", handler=export),),
            migrations=migrations(context.database.schema),
        )

    nightly = '[[schedules]]\nname = "probe.nightly"\nevery = "24h"\nenqueues = "probe.export"\n'
    app = App(
        "worker", settings=SETTINGS, discover=discovering(module("probe", both, CONSUMES + JOBS + nightly))
    )
    assert [manifest.package.name for manifest in app.modules] == ["probe"]


def test_a_cron_schedule_is_refused_until_one_is_needed() -> None:
    cron = '[[schedules]]\nname = "probe.nightly"\ncron = "0 3 * * *"\nenqueues = "probe.export"\n'
    with pytest.raises(CronScheduleError, match=r"probe\.nightly"):
        App("worker", settings=SETTINGS, discover=discovering(module("probe", exporting, JOBS + cron)))


def test_channels_assemble_after_modules_with_the_ports_modules_provide() -> None:
    seen: dict[str, object] = {}
    during: list[type[Exception]] = []

    def alpha(context: ModuleContext) -> ModuleParts:
        seen["channels"] = context.channels
        seen["entries"] = context.channel_entries  # data: known while the module assembles
        try:
            context.channels.get("us-probe-v1")
        except ChannelsNotAssembledError as error:
            during.append(type(error))
        return ModuleParts(
            provides=(PROBE_PORT.bind(Signer()),), migrations=migrations(context.database.schema)
        )

    def channel(context: ChannelContext) -> ChannelParts:
        seen["port"] = context.ports.get(PROBE_PORT)
        seen["regime"] = context.regime.key
        seen["schema"] = context.database.schema
        return ChannelParts(channel="the us probe channel", migrations=migrations(context.database.schema))

    App(
        "worker",
        settings=SETTINGS,
        discover=discovering(
            module("alpha", alpha, '[extension_points]\nprovides = ["ProbePort"]\n'),
            regimes=(us_regime(),),
            packs=(ChannelPack(entry=channel_entry(), regime="us", assemble=channel),),
        ),
    )
    assert during == [ChannelsNotAssembledError]
    channels = seen["channels"]
    assert isinstance(channels, AssembledChannels)
    assert channels.get("us-probe-v1") == "the us probe channel"
    assert seen["entries"] == (channel_entry(),)
    assert isinstance(seen["port"], Signer)
    assert (seen["regime"], seen["schema"]) == ("us", "us_probe")


def test_a_channel_needs_its_migrations_and_a_schema_of_its_own() -> None:
    def bare(context: ChannelContext) -> ChannelParts:
        return ChannelParts(channel=context.definition)

    def migrated(context: ChannelContext) -> ChannelParts:
        return ChannelParts(channel=context.definition, migrations=migrations(context.database.schema))

    with pytest.raises(MigrationsMissingError, match="'us_probe'"):
        App(
            "worker",
            settings=SETTINGS,
            discover=discovering(
                regimes=(us_regime(),),
                packs=(ChannelPack(entry=channel_entry(), regime="us", assemble=bare),),
            ),
        )
    with pytest.raises(DuplicateSchemaError, match="'alpha'"):
        App(
            "worker",
            settings=SETTINGS,
            discover=discovering(
                module("alpha"),
                regimes=(us_regime(),),
                packs=(ChannelPack(entry=channel_entry("alpha"), regime="us", assemble=migrated),),
            ),
        )


def test_ports_are_guarded_like_apis() -> None:
    def alpha(context: ModuleContext) -> ModuleParts:
        return ModuleParts(
            provides=(PROBE_PORT.bind(UnguardedSigner()),), migrations=migrations(context.database.schema)
        )

    with pytest.raises(UnguardedFacadeError, match=r"UnguardedSigner\.sign"):
        App(
            "worker",
            settings=SETTINGS,
            discover=discovering(module("alpha", alpha, '[extension_points]\nprovides = ["ProbePort"]\n')),
        )


@pytest.mark.parametrize(
    ("body", "name"),
    [
        ('[extension_points]\ndeclares = ["Policy"]\nrequired = ["Policy"]\n', "'beta.Policy'"),
        ('[extension_points]\nprovides = ["ProbePort"]\n', "'ProbePort'"),
    ],
    ids=["extension-point", "port"],
)
def test_every_required_extension_point_and_port_is_bound_after_assembly(body: str, name: str) -> None:
    with pytest.raises(BindingMissingError, match=name):
        App("worker", settings=SETTINGS, discover=discovering(module("beta", body=body)))


def test_an_extension_point_nobody_must_implement_assembles_with_an_empty_chain() -> None:
    """Only a required point fails the start unimplemented; an optional one reads as no implementation."""
    app = App(
        "worker",
        settings=SETTINGS,
        discover=discovering(module("beta", body='[extension_points]\ndeclares = ["Policy"]\n')),
    )
    assert [manifest.package.name for manifest in app.modules] == ["beta"]


def test_a_declared_extension_point_implemented_by_another_module_assembles() -> None:
    def gamma(context: ModuleContext) -> ModuleParts:
        return ModuleParts(
            implements=(BETA_POLICY.bind(Allowing()),), migrations=migrations(context.database.schema)
        )

    App(
        "worker",
        settings=SETTINGS,
        discover=discovering(
            module("beta", body='[extension_points]\ndeclares = ["Policy"]\n'),
            module("gamma", gamma, '[extension_points]\nimplements = ["beta.Policy"]\n'),
        ),
    )


@final
class Allowing:
    @facade
    async def allows(self, document: str) -> bool:
        return bool(document)


def test_every_module_owns_a_schema_of_its_own_with_its_migrations() -> None:
    schemaless = Module(
        manifest=load_manifest(
            '[package]\nname = "alpha"\nkind = "module"\nsummary = "A probe."\nowners = ["@o"]\n'
        ),
        assemble=beta,
    )
    with pytest.raises(SchemaMissingError, match="'alpha'"):
        App("worker", settings=SETTINGS, discover=discovering(schemaless))
    with pytest.raises(DuplicateSchemaError, match="'shared'"):
        App(
            "worker",
            settings=SETTINGS,
            discover=discovering(module("alpha", schema="shared"), module("beta", schema="shared")),
        )
    with pytest.raises(DuplicateSchemaError, match="'platform'"):
        App("worker", settings=SETTINGS, discover=discovering(module("alpha", schema="platform")))

    def unmigrated(context: ModuleContext) -> ModuleParts:
        del context
        return ModuleParts()

    def foreign(context: ModuleContext) -> ModuleParts:
        del context
        return ModuleParts(migrations=migrations("platform"))

    with pytest.raises(MigrationsMissingError, match="'alpha'"):
        App("worker", settings=SETTINGS, discover=discovering(module("alpha", unmigrated)))
    with pytest.raises(ForeignSchemaError, match="'platform'"):
        App("worker", settings=SETTINGS, discover=discovering(module("alpha", foreign)))


def test_the_receiver_is_not_built_yet() -> None:
    with pytest.raises(RoleNotBuiltError, match="receiver"):
        App("receiver", settings=SETTINGS, discover=discovering())


def test_the_api_needs_token_settings() -> None:
    with pytest.raises(TokenSettingsMissingError, match="token_issuer"):
        App("api", settings=SETTINGS, discover=discovering())
    partly = SETTINGS.model_copy(update={"token_issuer": "https://issuer.invalid"})
    with pytest.raises(TokenSettingsMissingError, match="token_audience"):
        App("worker", settings=partly, discover=discovering())
    App("worker", settings=SETTINGS, discover=discovering())
    App("api", settings=TOKENS, discover=discovering())


def test_each_role_runs_only_what_it_is_for() -> None:
    with pytest.raises(RoleMismatchError, match="migrate"):
        App("worker", settings=SETTINGS, discover=discovering()).migrate()
    with pytest.raises(RoleMismatchError, match="serve HTTP"):
        App("migrate", settings=SETTINGS, discover=discovering()).asgi()
    with pytest.raises(DirectUrlMissingError, match="migrate"):
        App("migrate", settings=BARE, discover=discovering()).migrate()
    assert App("migrate", settings=SETTINGS, discover=discovering()).modules == ()


def test_the_worker_needs_a_direct_url_when_it_is_made() -> None:
    """Its LISTEN connection never goes through a pooler (§6.7): refused at the start, not at a wait."""
    with pytest.raises(DirectUrlMissingError, match="worker"):
        App("worker", settings=BARE, discover=discovering())


def test_the_stop_steps_fit_the_stop_budget() -> None:
    assert STOP.total <= STOP_BUDGET
    assert STOP.total == sum(
        (STOP.drain, STOP.workers, STOP.resources, STOP.notifications, STOP.pools, STOP.spans)
    )


class CloseFailedError(Exception):
    def __init__(self) -> None:
        super().__init__("the resource failed to close")


@asynccontextmanager
async def recorded(log: list[str], name: str) -> AsyncGenerator[None]:
    log.append(f"opened {name}")
    yield
    log.append(f"closed {name}")


@asynccontextmanager
async def hanging(free: asyncio.Event, closing: Watched) -> AsyncGenerator[None]:
    """Its close waits for ``free``, through the stop's cancellation: the stop must leave it behind.

    ``closing`` watches the task its close runs in.
    """
    yield
    closing.enter()
    while not free.is_set():
        with contextlib.suppress(asyncio.CancelledError):
            await free.wait()


@asynccontextmanager
async def failing() -> AsyncGenerator[None]:
    yield
    raise CloseFailedError


@final
class SchedulerBrokenError(Exception):
    def __init__(self) -> None:
        super().__init__("the scheduler cannot be made")


def broken_scheduler(**_: object) -> object:
    raise SchedulerBrokenError


async def test_a_worker_whose_start_fails_halfway_stops_what_it_started(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The scheduler, made last, fails: the workers started before it are stopped all the same."""
    monkeypatch.setattr(runtime_app, "Scheduler", broken_scheduler)
    quick = StopTimes(drain=0.2, workers=0.5, resources=0.1, notifications=0.1, pools=0.5, spans=0.5)
    monkeypatch.setattr(runtime_app, "STOP", quick)
    served = App("worker", settings=SETTINGS, discover=discovering()).asgi()
    before = asyncio.all_tasks()
    with pytest.raises(SchedulerBrokenError):
        async with served.router.lifespan_context(served):
            pass
    assert asyncio.all_tasks() - before == set()


async def test_a_stop_step_that_hangs_or_fails_never_holds_the_rest(monkeypatch: pytest.MonkeyPatch) -> None:
    quick = StopTimes(drain=0.1, workers=0.1, resources=0.4, notifications=0.1, pools=0.5, spans=0.5)
    monkeypatch.setattr(runtime_app, "STOP", quick)
    log: list[str] = []
    free, closing = asyncio.Event(), Watched()

    def alpha(context: ModuleContext) -> ModuleParts:
        resources = (recorded(log, "first"), hanging(free, closing), failing(), recorded(log, "last"))
        return ModuleParts(resources=resources, migrations=migrations(context.database.schema))

    served = App("api", settings=TOKENS, discover=discovering(module("alpha", alpha))).asgi()
    began = time.monotonic()
    with capture_logs() as logs:
        async with served.router.lifespan_context(served):
            assert log == ["opened first", "opened last"]
    assert time.monotonic() - began < quick.total
    assert log == ["opened first", "opened last", "closed last", "closed first"]
    events = {entry["event"] for entry in logs}
    assert {"app.stop_step_timed_out", "app.stop_step_failed"} <= events
    free.set()
    await closing.ended()  # the close left behind ends now
    with pytest.raises(AppAlreadyStartedError):
        async with served.router.lifespan_context(served):
            pass


def test_spans_are_exported_to_the_collector_the_settings_name() -> None:
    """The exporter is made, not connected: assembly does no I/O."""
    traced = SETTINGS.model_copy(update={"otlp_endpoint": "http://collector.invalid:4317"})
    assert App("worker", settings=traced, discover=discovering()).modules == ()


# ── Settings sections (§3.10): each package reads its own, validated when the app is made ───────────


class BooksSettings(PackageSettings):
    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(env_prefix=section_prefix("books"))

    limit: int = 10
    token: SecretStr | None = None


class MisnamedSettings(PackageSettings):
    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(env_prefix="LEDGER_BOOK_")


class ProbeChannelSettings(PackageSettings):
    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(env_prefix=section_prefix("us_probe"))

    timeout_seconds: float = 5.0


def sectioned(
    settings: type[PackageSettings] | None,
    seen: list[ModuleContext] | None = None,
    *,
    declared: bool = True,
) -> Module:
    """The module ``books``, its section declared or not, handing over ``settings`` as its model."""
    contexts: list[ModuleContext] = [] if seen is None else seen

    def assemble(context: ModuleContext) -> ModuleParts:
        contexts.append(context)
        return ModuleParts(migrations=migrations(context.database.schema))

    text = manifest_text("books")
    if declared:
        text = text.replace('owners = ["@o"]\n', 'owners = ["@o"]\nsettings = true\n')
    return Module(manifest=load_manifest(text), assemble=assemble, settings=settings)


def public_values(value: object) -> list[object]:
    return [getattr(value, name) for name in dir(value) if not name.startswith("_")]


def probe_pack() -> ChannelPack:
    def channel(context: ChannelContext) -> ChannelParts:
        return ChannelParts(channel=context, migrations=migrations(context.database.schema))

    return ChannelPack(
        entry=channel_entry(settings=True), regime="us", assemble=channel, settings=ProbeChannelSettings
    )


def test_a_module_reads_its_own_section_and_no_secret_of_the_platform(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LEDGER_BOOKS_LIMIT", "5")
    monkeypatch.setenv("LEDGER_BOOKS_TOKEN", "t0ken")
    seen: list[ModuleContext] = []
    App("worker", settings=SETTINGS, discover=discovering(sectioned(BooksSettings, seen)))
    [context] = seen
    section = context.section(BooksSettings)
    assert context.settings is section
    assert section.limit == 5
    assert section.token is not None
    assert section.token.get_secret_value() == "t0ken"
    # nothing of the app's own settings reaches it, nor the pool behind its units (and its URL)
    assert not any(isinstance(value, Settings) for value in public_values(context))
    assert not any(isinstance(value, Database) for value in public_values(context.database))


def test_a_module_without_a_section_has_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEDGER_BOOKS_LIMIT", "5")  # what the platform's Settings left to a section
    seen: list[ModuleContext] = []
    App("worker", settings=SETTINGS, discover=discovering(sectioned(None, seen, declared=False)))
    [context] = seen
    assert context.settings is None
    with pytest.raises(SectionMissingError, match="books"):
        context.section(BooksSettings)


def test_a_section_is_read_by_the_model_of_its_own_prefix_only() -> None:
    with pytest.raises(SettingsNotProvidedError, match="'books' declares a settings section"):
        App("worker", settings=SETTINGS, discover=discovering(sectioned(None)))
    with pytest.raises(SettingsUndeclaredError, match="'books' hands over a settings model"):
        App("worker", settings=SETTINGS, discover=discovering(sectioned(BooksSettings, declared=False)))
    with pytest.raises(SettingsPrefixError, match="LEDGER_BOOK_, not LEDGER_BOOKS_"):
        App("worker", settings=SETTINGS, discover=discovering(sectioned(MisnamedSettings)))


def test_a_channel_reads_its_own_section(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEDGER_US_PROBE_TIMEOUT_SECONDS", "2.5")
    seen: list[ChannelContext] = []

    def channel(context: ChannelContext) -> ChannelParts:
        seen.append(context)
        return ChannelParts(channel="probe", migrations=migrations(context.database.schema))

    pack = ChannelPack(
        entry=channel_entry(settings=True), regime="us", assemble=channel, settings=ProbeChannelSettings
    )
    App("worker", settings=SETTINGS, discover=discovering(regimes=(us_regime(),), packs=(pack,)))
    [context] = seen
    assert context.section(ProbeChannelSettings).timeout_seconds == 2.5
    assert not any(isinstance(value, Settings) for value in public_values(context))


def test_every_section_s_problems_are_listed_at_once_and_no_value_is_echoed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LEDGER_BOOKS_LIMIT", "many")
    monkeypatch.setenv("LEDGER_BOOKS_CEILING", "1")
    monkeypatch.setenv("LEDGER_US_PROBE_TIMEOUT_SECONDS", "soon")
    discover = discovering(sectioned(BooksSettings), regimes=(us_regime(),), packs=(probe_pack(),))
    with pytest.raises(InvalidSettingsError) as caught:
        App("worker", settings=SETTINGS, discover=discover)
    assert [problem.split(": ")[:2] for problem in caught.value.problems] == [
        ["books", "limit"],
        ["books", "LEDGER_BOOKS_CEILING"],
        ["us-probe-v1", "timeout_seconds"],
    ]
    assert "many" not in str(caught.value)
    assert "soon" not in str(caught.value)


# ── The pool (§6.7): sized for what the role runs at once ───────────────────────────────────────────


def sized(settings: Settings, pool: int) -> Settings:
    return settings.model_copy(update={"database_pool_size": pool})


BUSY: Final = JOBS + "concurrency = 6\n\n" + CONSUMES


def busy(context: ModuleContext) -> ModuleParts:
    return ModuleParts(
        consumers=(ConsumerSpec(name="probe.echo", handler=record),),
        jobs=(JobSpec(kind="probe.export", handler=export),),
        migrations=migrations(context.database.schema),
    )


def test_a_worker_s_pool_holds_every_job_its_renewals_its_runners_and_headroom() -> None:
    """Jobs 6 (probe) + 1 + 1 (the platform's purges), each with a lease renewal of its own while it runs;
    runners 1 inbox + 2 job runners + fan-out + scheduler; headroom 2: 23. No overflow is counted on."""
    discover = discovering(module("probe", busy, BUSY))
    no_overflow = SETTINGS.model_copy(update={"database_pool_overflow": 0})
    App("worker", settings=sized(no_overflow, 23), discover=discover)
    with pytest.raises(PoolTooSmallError) as caught:
        App("worker", settings=sized(no_overflow, 15), discover=discover)  # the need without the renewals
    assert str(caught.value) == (
        "the worker's pool of 15 connections (LEDGER_DATABASE_POOL_SIZE) is smaller than the 23 it may hold "
        "at once: 8 for jobs (their concurrency), 8 for lease renewals (running jobs, outside consumers), "
        "5 for its runners, "
        "2 of headroom"
    )
    assert caught.value.code == "pool_too_small"


async def notice(event: RawEvent) -> None:
    del event


OUTSIDE: Final = CONSUMES + 'style = "outside"\n'


def noticing(context: ModuleContext) -> ModuleParts:
    return ModuleParts(
        consumers=(ConsumerSpec(name="probe.echo", outside=notice),),
        migrations=migrations(context.database.schema),
    )


def test_an_outside_consumer_holds_a_second_connection_for_its_lease_renewals() -> None:
    """Its handler's units and its row's lease renewals at once (§3.5): jobs 1 + 1 (the platform's purges),
    renewals 2 + 1; runners 1 inbox + 1 job runner + fan-out + scheduler; headroom 2: 11."""
    discover = discovering(module("probe", noticing, OUTSIDE))
    no_overflow = SETTINGS.model_copy(update={"database_pool_overflow": 0})
    (consumer,) = members(App("worker", settings=sized(no_overflow, 11), discover=discover))[
        "probe"
    ].consumers
    assert consumer.outside
    with pytest.raises(PoolTooSmallError) as caught:
        App("worker", settings=sized(no_overflow, 10), discover=discover)
    assert str(caught.value) == (
        "the worker's pool of 10 connections (LEDGER_DATABASE_POOL_SIZE) is smaller than the 11 it may hold "
        "at once: 2 for jobs (their concurrency), 3 for lease renewals (running jobs, outside consumers), "
        "4 for its runners, "
        "2 of headroom"
    )


def test_an_outside_consumer_of_an_inside_declaration_is_a_defect() -> None:
    with pytest.raises(ConsumerMismatchError, match=r"probe\.echo \(outside\)"):
        App("worker", settings=SETTINGS, discover=discovering(module("probe", noticing, CONSUMES)))


def test_the_api_and_migrate_need_the_headroom_only() -> None:
    discover = discovering(module("probe", busy, BUSY))
    App("api", settings=sized(TOKENS, 2), discover=discover)
    App("migrate", settings=sized(SETTINGS, 2), discover=discover)
    with pytest.raises(PoolTooSmallError, match="the api's pool of 1 connections"):
        App("api", settings=sized(TOKENS, 1), discover=discover)


def test_an_app_names_every_schema_s_revisions_in_migration_order() -> None:
    app = App(
        "migrate", settings=SETTINGS, discover=discovering(module("alpha", depends=["beta"]), module("beta"))
    )
    assert [migrations.schema for migrations in app.migrations] == ["platform", "beta", "alpha"]


def test_an_app_that_assembles_no_module_says_so(capsys: pytest.CaptureFixture[str]) -> None:
    """Its logs are configured as it is made: the warning is a line of the app's own output."""
    App("worker", settings=SETTINGS, discover=discovering(module("alpha")))
    assert "app.no_modules" not in capsys.readouterr().out
    App("worker", settings=SETTINGS, discover=discovering())
    [line] = [line for line in capsys.readouterr().out.splitlines() if "app.no_modules" in line]
    assert "warning" in line
    assert "role=worker" in line


def test_a_suspended_module_s_section_is_validated_too(monkeypatch: pytest.MonkeyPatch) -> None:
    """It is neither assembled nor migrated, but its variables are read: a mistake there fails the start."""
    suspended = sectioned(BooksSettings)
    text = manifest_text("books").replace(
        'owners = ["@o"]\n', 'owners = ["@o"]\nsettings = true\nstatus = "suspended"\n'
    )
    module_of = Module(manifest=load_manifest(text), assemble=suspended.assemble, settings=BooksSettings)
    assert App("worker", settings=SETTINGS, discover=discovering(module_of)).modules == ()
    monkeypatch.setenv("LEDGER_BOOKS_LIMIT", "many")
    with pytest.raises(InvalidSettingsError, match="books: limit"):
        App("worker", settings=SETTINGS, discover=discovering(module_of))


# ── Isolated modules (Decisions 2, 11): their own role runs them; every other role assembles and relays ────

VAULT: Final = (
    '[[events.consumes]]\nconsumer = "vault.renamed"\ntypes = ["probe.thing.renamed"]\n\n'
    '[[jobs]]\nkind = "vault.export"\nconcurrency = 3\n\n'
    '[[schedules]]\nname = "vault.nightly"\nevery = "24h"\nenqueues = "vault.export"\n'
)


async def ready() -> None:
    """A readiness check that passes."""


@final
class Vault:
    """The module ``vault``, isolated in the signer's role and depending on ``beta``; what its assemblies saw.

    ``outside``: what it hands over in any role but its own as well (a defect); ``reads``: it reads beta's API
    while it assembles, and ``read`` records what that gave.
    """

    __slots__ = ("contexts", "outside", "read", "reads")

    def __init__(self, *, outside: Sequence[str] = (), reads: bool = False) -> None:
        self.contexts: list[ModuleContext] = []
        self.outside = frozenset(outside)
        self.reads = reads
        self.read: list[str] = []

    def module(self) -> Module:
        manifest = manifest_text("vault", VAULT, depends=["beta"], isolated=True)
        return Module(manifest=load_manifest(manifest), assemble=self._assemble)

    def _assemble(self, context: ModuleContext) -> ModuleParts:
        self.contexts.append(context)
        if self.reads:
            try:
                self.read.append(type(context.apis.get(BETA_API)).__name__)
            except UndeclaredDependencyError as error:
                self.read.append(error.code)
        own = context.role == "signer"
        consumers = own or "consumers" in self.outside
        jobs = own or "jobs" in self.outside
        return ModuleParts(
            consumers=(ConsumerSpec(name="vault.renamed", handler=record),) if consumers else (),
            jobs=(JobSpec(kind="vault.export", handler=export),) if jobs else (),
            checks={"ready": ready},
            migrations=migrations(context.database.schema),
        )


def members(app: App) -> dict[str, Member]:
    return {member.name: member for member in app.members}


def test_the_signer_role_assembles_only_isolated_modules() -> None:
    vault = Vault()
    app = App(
        "signer",
        settings=SETTINGS,
        discover=discovering(vault.module(), module("beta", beta), module("probe")),
    )
    assert [manifest.package.name for manifest in app.modules] == ["vault"]
    assert list(members(app)) == ["platform", "vault"]
    own = members(app)["vault"]
    assert not own.relay_only
    assert [consumer.name for consumer in own.consumers] == ["vault.renamed"]
    assert list(own.kinds) == ["vault.export"]
    assert [decl.name for decl in own.schedules] == ["vault.nightly"]
    assert [context.role for context in vault.contexts] == ["signer"]
    with pytest.raises(SchemaUnknownError, match="'probe'"):
        app.bound("probe")
    # the module it depends on need not be installed: its role assembles none
    alone = App("signer", settings=SETTINGS, discover=discovering(Vault().module()))
    assert [manifest.package.name for manifest in alone.modules] == ["vault"]


def test_the_signer_role_assembles_no_channel_and_reads_no_other_section(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Neither another module's variables nor a channel's are the signer's: a wrong one never stops it."""
    monkeypatch.setenv("LEDGER_BOOKS_LIMIT", "many")
    monkeypatch.setenv("LEDGER_US_PROBE_TIMEOUT_SECONDS", "soon")
    assembled: list[str] = []

    def channel(context: ChannelContext) -> ChannelParts:
        assembled.append(context.definition.key)
        return ChannelParts(channel="probe", migrations=migrations(context.database.schema))

    pack = ChannelPack(
        entry=channel_entry(settings=True), regime="us", assemble=channel, settings=ProbeChannelSettings
    )
    vault = Vault()
    app = App(
        "signer",
        settings=SETTINGS,
        discover=discovering(vault.module(), sectioned(BooksSettings), regimes=(us_regime(),), packs=(pack,)),
    )
    assert assembled == []
    with pytest.raises(SchemaUnknownError, match="'us_probe'"):
        app.bound("us_probe")
    [context] = vault.contexts
    assert context.channel_entries == ()
    assert context.channels.keys() == ()


def recording(purged: list[tuple[str, ...]]) -> Callable[[Sequence[BoundDatabase]], tuple[JobSpec, JobSpec]]:
    """``maintenance_jobs`` as assembly calls it, the schemas of each call written down in ``purged``."""

    def jobs(databases: Sequence[BoundDatabase]) -> tuple[JobSpec, JobSpec]:
        purged.append(tuple(database.schema for database in databases))
        return maintenance_jobs(databases)

    return jobs


def test_the_signer_role_runs_no_platform_job_or_schedule(monkeypatch: pytest.MonkeyPatch) -> None:
    """The worker purges the schemas it reaches; the signer hands its own purges over (Decision 11)."""
    purged: list[tuple[str, ...]] = []
    monkeypatch.setattr(runtime_assembly, "maintenance_jobs", recording(purged))
    app = App("signer", settings=SETTINGS, discover=discovering(Vault().module()))
    platform = members(app)["platform"]
    assert (platform.consumers, dict(platform.kinds), platform.schedules) == ((), {}, ())
    assert purged == []
    assert app.bound("platform").schema == "platform"  # its schedule leases


@pytest.mark.parametrize("role", ["api", "worker", "migrate"])
def test_outside_its_role_an_isolated_module_is_relay_only(
    role: AppRole, monkeypatch: pytest.MonkeyPatch
) -> None:
    purged: list[tuple[str, ...]] = []
    monkeypatch.setattr(runtime_assembly, "maintenance_jobs", recording(purged))
    vault = Vault()
    app = App(role, settings=TOKENS, discover=discovering(vault.module(), module("beta", beta)))
    assert [manifest.package.name for manifest in app.modules] == ["beta", "vault"]
    assert [context.role for context in vault.contexts] == [role]
    relayed = members(app)["vault"]
    assert relayed.relay_only
    assert (relayed.consumers, dict(relayed.kinds), relayed.schedules) == ((), {}, ())
    assert list(relayed.checks) == ["ready"]
    assert [decl.consumer for decl in relayed.events.consumes] == ["vault.renamed"]  # migrate subscribes it
    assert not members(app)["beta"].relay_only
    assert purged == [("platform", "beta")]  # never a schema its login cannot enter


@pytest.mark.parametrize("part", ["consumers", "jobs"])
def test_an_isolated_module_handing_consumers_outside_its_role_is_a_defect(part: str) -> None:
    discover = discovering(Vault(outside=[part]).module(), module("beta", beta))
    with pytest.raises(IsolatedPartsError) as caught:
        App("worker", settings=SETTINGS, discover=discover)
    assert caught.value.code == "isolated_parts_outside_role"
    assert str(caught.value) == (
        "'vault' is isolated in the signer's role and hands over consumers or jobs in the worker app: "
        "only its own role runs them"
    )
    App("signer", settings=SETTINGS, discover=discover)


def test_an_isolated_module_s_outside_consumer_runs_in_its_role_alone() -> None:
    """Isolation holds whatever the style: relayed in another role, handing a consumer there is a defect."""
    body = 'isolated_in = "signer"\n\n' + OUTSIDE

    def sealed(context: ModuleContext) -> ModuleParts:
        own = context.role == "signer"
        return ModuleParts(
            consumers=(ConsumerSpec(name="probe.echo", outside=notice),) if own else (),
            migrations=migrations(context.database.schema),
        )

    signer = App("signer", settings=SETTINGS, discover=discovering(module("probe", sealed, body)))
    assert [consumer.outside for consumer in members(signer)["probe"].consumers] == [True]
    worker = App("worker", settings=SETTINGS, discover=discovering(module("probe", sealed, body)))
    assert members(worker)["probe"].relay_only
    assert members(worker)["probe"].consumers == ()
    with pytest.raises(IsolatedPartsError):
        App("worker", settings=SETTINGS, discover=discovering(module("probe", noticing, body)))


def test_an_isolated_module_reads_no_api_in_its_role() -> None:
    vault = Vault(reads=True)
    discover = discovering(vault.module(), module("beta", beta))
    App("signer", settings=SETTINGS, discover=discover)
    App("worker", settings=SETTINGS, discover=discover)
    assert vault.read == ["dependency_undeclared", "Beta"]


def test_the_signer_role_needs_the_direct_url() -> None:
    """It listens for wake-ups, as the worker does (§6.7)."""
    with pytest.raises(DirectUrlMissingError, match="signer"):
        App("signer", settings=BARE, discover=discovering())
    with pytest.raises(RoleMismatchError, match="signer app does not migrate"):
        App("signer", settings=SETTINGS, discover=discovering(Vault().module())).migrate()


def test_a_signer_without_an_isolated_module_does_not_start() -> None:
    """Every other role warns (``app.no_modules``); the signer's whole purpose is its isolated modules."""
    suspended = manifest_text("vault", VAULT, isolated=True).replace(
        'owners = ["@o"]\n', 'owners = ["@o"]\nstatus = "suspended"\n'
    )
    for discover in (
        discovering(),
        discovering(module("beta", beta)),
        discovering(Module(manifest=load_manifest(suspended), assemble=beta)),
    ):
        with pytest.raises(IsolatedModuleMissingError) as caught:
            App("signer", settings=SETTINGS, discover=discover)
        assert caught.value.code == "isolated_module_missing"
        assert str(caught.value) == (
            "the signer app runs the modules isolated in its role, and none is installed and active"
        )


def test_the_signer_pool_need_counts_its_runners() -> None:
    """Jobs 3 (vault's), each with its renewal; runners 1 inbox + 1 job runner + the scheduler (no fan-out);
    headroom 2: 11. A worker counts nothing of what it only relays: the purges' 2 + 2, a job runner, the
    fan-out and the scheduler, 2: 9."""
    discover = discovering(Vault().module(), module("beta", beta))
    no_overflow = SETTINGS.model_copy(update={"database_pool_overflow": 0})
    App("signer", settings=sized(no_overflow, 11), discover=discover)
    with pytest.raises(PoolTooSmallError) as caught:
        App("signer", settings=sized(no_overflow, 10), discover=discover)
    assert str(caught.value) == (
        "the signer's pool of 10 connections (LEDGER_DATABASE_POOL_SIZE) is smaller than the 11 it may hold "
        "at once: 3 for jobs (their concurrency), 3 for lease renewals (running jobs, outside consumers), "
        "3 for its runners, "
        "2 of headroom"
    )
    App("worker", settings=sized(no_overflow, 9), discover=discover)
    with pytest.raises(PoolTooSmallError, match="the 9 it may hold"):
        App("worker", settings=sized(no_overflow, 8), discover=discover)


@pytest.mark.parametrize("environment", ["local", "production"])
def test_contexts_carry_the_environment(environment: Environment) -> None:
    """A module may refuse a local-only adapter outside ``local`` and ``test`` (Decision 14)."""
    seen: list[ModuleContext] = []
    settings = SETTINGS.model_copy(update={"environment": environment})
    App("worker", settings=settings, discover=discovering(sectioned(None, seen, declared=False)))
    [context] = seen
    assert context.environment == environment


def is_store_archive(value: object) -> TypeIs[StoreArchive]:
    return isinstance(value, StoreArchive)


ARCHIVE: Final = Key[StoreArchive](ARCHIVE_PORT_NAME, is_store_archive)
"""fiscal-kit's ``ARCHIVE_PORT`` as the platform's tests can name it (the platform imports no kit)."""


def archived(settings: Settings, provider: Module | None = None) -> list[StoreArchive]:
    """The archive a channel reads while it assembles, in an app with ``settings``."""
    seen: list[StoreArchive] = []

    def channel(context: ChannelContext) -> ChannelParts:
        seen.append(context.ports.get(ARCHIVE))
        return ChannelParts(channel="archiving", migrations=migrations(context.database.schema))

    modules = () if provider is None else (provider,)
    App(
        "worker",
        settings=settings,
        discover=discovering(
            *modules,
            regimes=(us_regime(),),
            packs=(ChannelPack(entry=channel_entry(), regime="us", assemble=channel),),
        ),
    )
    return seen


async def test_a_channel_reads_the_archive_the_app_binds(tmp_path: Path) -> None:
    filed = SETTINGS.model_copy(update={"archive_backend": "file", "archive_path": tmp_path})
    [archive] = archived(filed)
    stored = await archive.put_once(
        "probe/a.xml", b"<a/>", retain_until=date(2040, 1, 1), media_type="text/xml"
    )
    assert (tmp_path / "probe" / "a.xml").read_bytes() == b"<a/>"
    assert await archive.get("probe/a.xml", sha256=stored.sha256) == b"<a/>"


def test_without_an_archive_configured_a_channel_that_reads_one_does_not_start() -> None:
    with pytest.raises(BindingMissingError, match="'ArchivePort'"):
        archived(SETTINGS)


def test_no_module_provides_the_archive_the_app_binds(tmp_path: Path) -> None:
    def providing(context: ModuleContext) -> ModuleParts:
        return ModuleParts(
            provides=(Binding(ARCHIVE_PORT_NAME, Signer()),), migrations=migrations(context.database.schema)
        )

    filed = SETTINGS.model_copy(update={"archive_backend": "file", "archive_path": tmp_path})
    provider = module("alpha", providing, '[extension_points]\nprovides = ["ArchivePort"]\n')
    for settings in (filed, SETTINGS):  # configured or not, the archive is the app's alone
        with pytest.raises(
            PortReservedError, match="'alpha' provides port 'ArchivePort', which the app binds"
        ):
            archived(settings, provider)
