"""A workspace package's manifest (§4.2, §4.3): what its ``manifest.toml`` declares, as validated values.

One model serves both sides. ``ledger-devtools`` reads every ``manifest.toml`` with ``load_manifest`` (unknown
keys and malformed names are listed together, by location, never by value; what a kind may declare is
checked once the fields are valid, so its problem is reported after the field problems are fixed) and writes
each package's ``_manifest.py``, which validates the same data again when it is imported; the runtime reads
those values. Rules that need the whole workspace (a name equal to its directory, unique schemas and prefixes,
an acyclic module graph, schedules that enqueue a declared job kind) are ``ledger-devtools check``'s.
"""

import re
import tomllib
from collections.abc import Mapping
from datetime import date, timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import Annotated, ClassVar, Final, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)

from ledger_kernel.errors import LedgerError

type AppRole = Literal["api", "worker", "signer", "receiver", "migrate"]
"""What an app process runs (§3.3)."""
type BuiltInRole = Literal["owner", "admin", "accountant", "issuer", "viewer"]
"""The roles a permission may be granted to by default (§7.7)."""
type Tier = Literal["bronze", "silver", "gold"]

_FROZEN: Final = ConfigDict(extra="forbid", frozen=True)
_VALUE_ERROR: Final = "Value error, "
"""What pydantic puts before the message of a ``ValueError`` raised by a validator."""
_DURATION: Final = re.compile(r"([1-9][0-9]*)([smhd])", re.ASCII)
_UNIT_SECONDS: Final[Mapping[str, int]] = {"s": 1, "m": 60, "h": 3600, "d": 86_400}

type Name = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{1,30}$")]
type SchemaName = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{1,30}$")]
type Prefix = Annotated[str, Field(pattern=r"^[a-z]{2,5}$")]
type PermissionCode = Annotated[
    str, Field(pattern=r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*\.(read|create|issue|void|manage|own)$")
]
type EventType = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")]
type PublicName = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")]
type Dotted = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")]
"""A consumer (``<module>.<purpose>``), a job kind or a schedule name (``<module or channel>.<name>``)."""
type HttpPrefix = Annotated[str, Field(pattern=r"^/v1(/[a-z0-9_-]+)+$")]
type Requirement = Annotated[
    str, Field(pattern=r"^[a-z0-9][a-z0-9_.-]*(\[[a-z0-9_,-]+\])?>=[0-9][0-9a-z.]*(,<[0-9][0-9a-z.]*)?$")
]
"""A third-party distribution with a floor: ``fastapi>=0.143``, ``psycopg[binary]>=3.3.6``."""
type ObjectPath = Annotated[str, Field(pattern=r"^[a-z_][a-z0-9_.]*(:[A-Za-z_][A-Za-z0-9_]*)?$")]
type Script = Annotated[str, Field(pattern=r"^ledger-[a-z][a-z0-9-]{0,32}$")]
"""A console script: at most 40 characters, so a generated line that names one fits."""


class _DurationFormatError(ValueError):
    def __init__(self) -> None:
        super().__init__("a duration is a positive whole number and one of s, m, h, d (60s, 72h, 15d)")


def parse_duration(text: object) -> timedelta:
    """``60s``, ``10m``, ``1h`` or ``15d``; a ``timedelta`` passes through (values already read)."""
    if isinstance(text, timedelta):
        return text
    found = _DURATION.fullmatch(text) if type(text) is str else None
    if found is None:
        raise _DurationFormatError
    return timedelta(seconds=int(found[1]) * _UNIT_SECONDS[found[2]])


type Duration = Annotated[timedelta, BeforeValidator(parse_duration)]


def _read_only(scripts: Mapping[str, str]) -> Mapping[str, str]:
    return MappingProxyType(dict(scripts))


type Scripts = Annotated[Mapping[Script, ObjectPath], AfterValidator(_read_only)]
"""Console scripts by name; read-only once validated."""


class PackageKind(StrEnum):
    """What a package is (§4.2)."""

    MODULE = "module"
    COUNTRY = "country"
    APP = "app"
    PACKAGE = "package"
    TOOL = "tool"


class PackageStatus(StrEnum):
    """A suspended package is neither assembled nor migrated."""

    ACTIVE = "active"
    SUSPENDED = "suspended"


class _Section(BaseModel):
    model_config: ClassVar[ConfigDict] = _FROZEN


class PackageInfo(_Section):
    """``[package]``."""

    name: Name
    kind: PackageKind
    summary: str = Field(min_length=1, max_length=200)
    owners: tuple[str, ...] = Field(min_length=1)
    status: PackageStatus = PackageStatus.ACTIVE
    scripts: Scripts = Field(default_factory=dict, validate_default=True)
    """Console scripts of a package or tool (``ledger-probe``); an app's is ``[app] script``."""


class Dependencies(_Section):
    """``[dependencies]``: workspace packages, modules (a DAG) and third-party distributions with floors."""

    packages: tuple[Name, ...] = ()
    modules: tuple[Name, ...] = ()
    python: tuple[Requirement, ...] = ()
    test: tuple[Requirement, ...] = ()
    """What its tests and type checks need besides (installed by the root's dev group, never shipped)."""


class DataSection(_Section):
    """``[data]``: the PostgreSQL schema the package owns, which is also its roles' prefix."""

    schema_name: SchemaName = Field(alias="schema")


class IdPrefix(_Section):
    """``[[ids]]``: a public id prefix the package mints."""

    prefix: Prefix
    resource: SchemaName


class PermissionDecl(_Section):
    """``[[permissions]]``."""

    code: PermissionCode
    summary: str = Field(min_length=1, max_length=200)
    roles: tuple[BuiltInRole, ...] = ()


class PublishedEvent(_Section):
    """``[[events.publishes]]``."""

    type: EventType
    version: int = Field(ge=1)
    public: PublicName | None = None


class ConsumerDecl(_Section):
    """``[[events.consumes]]``: a consumer and the event types it handles."""

    consumer: Dotted
    types: tuple[EventType, ...] = Field(min_length=1)
    max_attempts: int = Field(default=10, ge=1)
    ordered: bool = False


class EventsSection(_Section):
    """``[events]``."""

    publishes: tuple[PublishedEvent, ...] = ()
    consumes: tuple[ConsumerDecl, ...] = ()


class JobDecl(_Section):
    """``[[jobs]]``."""

    kind: Dotted
    lease: Duration = timedelta(seconds=60)
    max_attempts: int = Field(default=20, ge=1)
    deadline: Duration | None = None
    concurrency: int = Field(default=8, ge=1)


class ScheduleDecl(_Section):
    """``[[schedules]]``: exactly one of ``every`` and ``cron``; it only enqueues a job (§3.6)."""

    name: Dotted
    every: Duration | None = None
    cron: str | None = None
    enqueues: Dotted

    @model_validator(mode="after")
    def exactly_one_cadence(self) -> Self:
        """A schedule runs on an interval or on a cron expression, never both, never neither."""
        if (self.every is None) == (self.cron is None):
            raise _CadenceError
        return self


class _CadenceError(ValueError):
    def __init__(self) -> None:
        super().__init__("a schedule has exactly one of every and cron")


class ExtensionPoints(_Section):
    """``[extension_points]``."""

    declares: tuple[str, ...] = ()
    implements: tuple[str, ...] = ()
    provides: tuple[str, ...] = ()


class HttpSection(_Section):
    """``[http]``: the apps that mount the package's routers and the path prefixes they own."""

    mounted_in: tuple[AppRole, ...] = Field(min_length=1)
    prefixes: tuple[HttpPrefix, ...] = Field(min_length=1)


class AppSection(_Section):
    """``[app]``."""

    script: Script
    role: AppRole


class Authority(_Section):
    """The tax authority of a country."""

    name: str
    short: str
    url: str


class CountrySection(_Section):
    """``[country]``."""

    iso: str = Field(pattern=r"^[A-Z]{2}$")
    name: str
    currencies: tuple[str, ...] = Field(min_length=1)
    time_zone: str
    authority: Authority


class RegimeEntry(_Section):
    """``[[regimes]]``: a regime key and the entry point that holds its ``RegimeDef``."""

    key: str = Field(pattern=r"^[a-z]{2}(-[a-z0-9]{1,3})?$")
    entry_point: ObjectPath


class ChannelEntry(_Section):
    """``[[channels]]``: treated like a module by devtools (schema, roles, grants, routing)."""

    key: str = Field(pattern=r"^[a-z]{2}-[a-z0-9]+-v[0-9]+$")
    entry_point: ObjectPath
    assembly: ObjectPath
    schema_name: SchemaName = Field(alias="schema")
    valid_from: date
    valid_until: date | None = None
    environments: tuple[str, ...] = Field(min_length=1)
    live_environments: tuple[str, ...] = ()
    document_kinds: tuple[Literal["invoice", "credit_note", "debit_note"], ...] = Field(min_length=1)
    directions: tuple[Literal["sales", "purchase"], ...] = Field(min_length=1)
    http_prefixes: tuple[HttpPrefix, ...] = ()
    tier: Tier
    ids: tuple[IdPrefix, ...] = ()
    permissions: tuple[PermissionDecl, ...] = ()
    events: EventsSection = EventsSection()
    jobs: tuple[JobDecl, ...] = ()
    schedules: tuple[ScheduleDecl, ...] = ()


class _KindRule(StrEnum):
    """A rule of what a kind may declare; its value is the location the rule is reported at."""

    APP = "app"
    COUNTRY = "country"
    REGIMES = "regimes"
    COUNTRY_MODULES = "dependencies.modules"
    DATA = "data"


_KIND_RULES: Final[Mapping[_KindRule, str]] = {
    _KindRule.APP: "[app] is declared by an app, and only by one",
    _KindRule.COUNTRY: "[country] is declared by a country, and only by one",
    _KindRule.REGIMES: "only a country declares regimes and channels",
    _KindRule.COUNTRY_MODULES: "a country may not depend on a module",
    _KindRule.DATA: "only a module or the platform owns a schema, events, jobs or schedules",
}


class _KindRuleError(ValueError):
    """A section the package's kind may not declare; the message starts with the rule's location."""

    def __init__(self, rule: _KindRule) -> None:
        super().__init__(f"{rule}: {_KIND_RULES[rule]}")


_DISTRIBUTION_PREFIX: Final[Mapping[PackageKind, str]] = {
    PackageKind.MODULE: "ledger-",
    PackageKind.PACKAGE: "ledger-",
    PackageKind.TOOL: "ledger-",
    PackageKind.COUNTRY: "ledger-country-",
    PackageKind.APP: "ledger-app-",
}


class PackageManifest(_Section):
    """A whole ``manifest.toml``."""

    package: PackageInfo
    dependencies: Dependencies = Dependencies()
    data: DataSection | None = None
    ids: tuple[IdPrefix, ...] = ()
    permissions: tuple[PermissionDecl, ...] = ()
    events: EventsSection = EventsSection()
    jobs: tuple[JobDecl, ...] = ()
    schedules: tuple[ScheduleDecl, ...] = ()
    extension_points: ExtensionPoints = ExtensionPoints()
    http: HttpSection | None = None
    app: AppSection | None = None
    country: CountrySection | None = None
    regimes: tuple[RegimeEntry, ...] = ()
    channels: tuple[ChannelEntry, ...] = ()

    @model_validator(mode="after")
    def kind_rules(self) -> Self:
        """What each kind may declare (§4.2, §4.3)."""
        kind = self.package.kind
        if (self.app is not None) != (kind is PackageKind.APP):
            raise _KindRuleError(_KindRule.APP)
        if (self.country is not None) != (kind is PackageKind.COUNTRY):
            raise _KindRuleError(_KindRule.COUNTRY)
        if (self.regimes or self.channels) and kind is not PackageKind.COUNTRY:
            raise _KindRuleError(_KindRule.REGIMES)
        if kind is PackageKind.COUNTRY and self.dependencies.modules:
            raise _KindRuleError(_KindRule.COUNTRY_MODULES)
        owns_data = (
            self.data is not None
            or self.events.publishes
            or self.events.consumes
            or self.jobs
            or self.schedules
        )
        if owns_data and kind not in {PackageKind.MODULE, PackageKind.PACKAGE}:
            raise _KindRuleError(_KindRule.DATA)
        return self

    @property
    def schema_name(self) -> str | None:
        """The schema the package owns, if any."""
        return None if self.data is None else self.data.schema_name

    @property
    def distribution(self) -> str:
        """The distribution (§4.4): ``ledger-<name>``, ``ledger-country-<cc>``, ``ledger-app-<name>``."""
        return _DISTRIBUTION_PREFIX[self.package.kind] + self.package.name

    @property
    def import_name(self) -> str:
        """The one top-level import package of the distribution."""
        return self.distribution.replace("-", "_")


class InvalidManifestError(LedgerError):
    """A ``manifest.toml`` the model refuses; each problem reads ``<location>: <rule>``, never its value."""

    code: ClassVar[str] = "invalid_manifest"

    def __init__(self, problems: tuple[str, ...]) -> None:
        super().__init__("manifest: " + "; ".join(problems))
        self.problems: Final = problems


def _problem(location: tuple[str, ...], message: str) -> str:
    """``<location>: <rule>``; a kind rule's location is already the start of its own message."""
    rule = message.removeprefix(_VALUE_ERROR)
    return rule if not location else f"{'.'.join(location)}: {rule}"


def load_manifest(text: str) -> PackageManifest:
    """The manifest ``text`` holds (the content of a ``manifest.toml``)."""
    try:
        return PackageManifest.model_validate(tomllib.loads(text))
    except tomllib.TOMLDecodeError as error:
        raise InvalidManifestError((f"line {error.lineno}: not TOML",)) from error
    except ValidationError as error:
        problems = tuple(
            _problem(tuple(str(part) for part in issue["loc"]), issue["msg"])
            for issue in error.errors(include_input=False, include_url=False)
        )
        raise InvalidManifestError(problems) from error
