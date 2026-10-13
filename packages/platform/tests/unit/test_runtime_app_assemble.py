"""``App``'s assembly (§3.10): modules in dependency order, then channels; what they hand back is checked."""

from datetime import date
from decimal import Decimal
from functools import partial
from typing import Final, Protocol, TypeIs, final, runtime_checkable
lazy from collections.abc import Callable, Mapping, Sequence

import pytest
from pydantic import SecretStr

from ledger_kernel.dated import Since, Timeline
from ledger_kernel.jurisdiction import CountryCode, Jurisdiction
from ledger_kernel.money import Currency, RoundingMode
from ledger_kernel.regimes import Locale, RegimeDef, RegimeKey, RoundingPoint, RoundingRule, Source
from ledger_platform.auth.tokens import TokenSettingsMissingError
from ledger_platform.config.settings import Settings
from ledger_platform.data.migrations import SchemaMigrations
from ledger_platform.events.consumers import ConsumerSpec
from ledger_platform.jobs.outcomes import Done, JobSpec
from ledger_platform.runtime.app import (
    App,
    DirectUrlMissingError,
    RoleMismatchError,
    RoleNotBuiltError,
    SchemaUnknownError,
)
from ledger_platform.runtime.assembly import (
    ConsumerNotProvidedError,
    ConsumerUndeclaredError,
    CronScheduleError,
    DuplicateSchemaError,
    ForeignSchemaError,
    HandlerDuplicateError,
    JobNotProvidedError,
    JobUndeclaredError,
    MigrationsMissingError,
    ModuleCycleError,
    ModuleMissingError,
    SchemaMissingError,
)
from ledger_platform.runtime.contexts import (
    ChannelPack,
    ChannelParts,
    ChannelsNotAssembledError,
    Module,
    ModuleParts,
)
from ledger_platform.runtime.facade import UnguardedFacadeError, facade
from ledger_platform.runtime.keys import BindingMissingError, Key, UndeclaredDependencyError
from ledger_platform.runtime.manifest import ChannelEntry, load_manifest
from ledger_platform.runtime.registries import CHANNELS, MODULES, REGIMES, Discovered
lazy from ledger_platform.data.unit_of_work import UnitOfWork
lazy from ledger_platform.events.consumers import RawEvent
lazy from ledger_platform.jobs.outcomes import Job, JobOutcome
lazy from ledger_platform.runtime.contexts import AssembledChannels, ChannelContext, ModuleContext
lazy from ledger_platform.runtime.registries import Discover

URL: Final = "postgresql+psycopg://ledger_app:app@db.invalid:5432/ledger"
DIRECT: Final = "postgresql+psycopg://ledger_migrator:migrator@db.invalid:5432/ledger"
SETTINGS: Final = Settings(environment="test", database_url=SecretStr(URL))
"""No token settings: enough for every role but ``api``."""
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
    name: str, body: str = "", *, schema: str | None = None, depends: Sequence[str] = ()
) -> str:
    schema_name = name if schema is None else schema
    modules = ", ".join(f'"{module}"' for module in depends)
    return (
        f'[package]\nname = "{name}"\nkind = "module"\nsummary = "A probe."\nowners = ["@o"]\n\n'
        f'[dependencies]\npackages = ["kernel", "platform"]\nmodules = [{modules}]\n\n'
        f'[data]\nschema = "{schema_name}"\n\n{body}'
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


def channel_entry(schema: str = "us_probe") -> ChannelEntry:
    return ChannelEntry.model_validate(
        {
            "key": "us-probe-v1",
            "entry_point": "ledger_country_us.channels.probe:PACK",
            "assembly": "ledger_country_us.channels.probe.assembly:assemble",
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
        ('[extension_points]\ndeclares = ["Policy"]\n', "'beta.Policy'"),
        ('[extension_points]\nprovides = ["ProbePort"]\n', "'ProbePort'"),
    ],
    ids=["extension-point", "port"],
)
def test_every_declared_extension_point_and_port_is_bound_after_assembly(body: str, name: str) -> None:
    with pytest.raises(BindingMissingError, match=name):
        App("worker", settings=SETTINGS, discover=discovering(module("beta", body=body)))


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


def test_signer_and_receiver_are_not_built_yet() -> None:
    for role in ("signer", "receiver"):
        with pytest.raises(RoleNotBuiltError, match=role):
            App(role, settings=SETTINGS, discover=discovering())


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
    with pytest.raises(DirectUrlMissingError):
        App("migrate", settings=SETTINGS, discover=discovering()).migrate()
    migrator = SETTINGS.model_copy(update={"database_direct_url": SecretStr(DIRECT)})
    assert App("migrate", settings=migrator, discover=discovering()).modules == ()


def test_spans_are_exported_to_the_collector_the_settings_name() -> None:
    """The exporter is made, not connected: assembly does no I/O."""
    traced = SETTINGS.model_copy(update={"otlp_endpoint": "http://collector.invalid:4317"})
    assert App("worker", settings=traced, discover=discovering()).modules == ()
