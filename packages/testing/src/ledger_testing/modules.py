"""A module's tests, run the way the apps run it (§14.2): what the plugin's module fixtures are made of.

A module's ``conftest.py`` names what it tests and prepares the template with it, in two lines::

    UNDER_TEST = ModuleUnderTest(MANIFEST, MIGRATIONS)


    def pytest_ledger_template(template: Template) -> None:
        UNDER_TEST.prepare(template)

and returns ``UNDER_TEST`` from a ``module_under_test`` fixture. The plugin's fixtures then give its tests the
worker's database migrated as production migrates it (``migrated``, emptied after each test), a pool as
``ledger_app`` (``database``), the module's units in its own role (``module_database``), a superuser session
(``admin``), the apps' settings (``settings``), the ``api`` app and a client of it (``api``, ``client``), and
a fresh, empty database with the schemas (``fresh_database_name``, ``fresh``, ``fresh_admin_url``). No module
copies another's conftest.
"""

from dataclasses import dataclass
from typing import ClassVar, final
lazy from collections.abc import Sequence

from pydantic import SecretStr

from ledger_kernel.errors import LedgerError
from ledger_platform.config.names import PLATFORM
from ledger_platform.config.settings import Settings
from ledger_platform.data.migrations import platform_migrations
from ledger_testing.database import bootstrap, migrate
lazy from ledger_platform.config.manifest import PackageManifest
lazy from ledger_platform.data.migrations import SchemaMigrations
lazy from ledger_platform.data.notifier import Channel
lazy from ledger_testing.issuer import LocalIssuer
lazy from ledger_testing.postgres import Template


class SchemaLessModuleError(LedgerError):
    """A package under test that owns no schema: there is nothing to migrate, bind or empty."""

    code: ClassVar[str] = "testing_module_schema_missing"

    def __init__(self, package: str) -> None:
        super().__init__(f"{package!r} owns no schema: its manifest has no [data] schema to test")


@final
@dataclass(frozen=True, slots=True)
class ModuleUnderTest:
    """The package a test suite exercises: its manifest and its schema's revisions."""

    manifest: PackageManifest
    migrations: SchemaMigrations

    def __post_init__(self) -> None:
        if self.manifest.schema_name is None:
            raise SchemaLessModuleError(self.manifest.package.name)

    @property
    def schema(self) -> str:
        """The schema the package owns."""
        return self.migrations.schema

    @property
    def schemas(self) -> tuple[str, ...]:
        """The platform's schema, then the package's: what its migrations need, in migration order."""
        return tuple(dict.fromkeys((PLATFORM, self.schema)))

    @property
    def publishes(self) -> frozenset[str]:
        """The event types the package's units may publish."""
        return frozenset(event.type for event in self.manifest.events.publishes)

    def prepare(self, template: Template) -> None:
        """The roles and the schemas, each at head as ``ledger_migrator`` with the relay granted."""
        bootstrap(template, self.schemas)
        revisions: Sequence[SchemaMigrations] = (
            (self.migrations,) if self.schema == PLATFORM else (platform_migrations(), self.migrations)
        )
        migrate(template, *revisions)


@final
class QuietSignals:
    """``Signals`` that tell no one: a module's tests run no worker to wake."""

    __slots__ = ()

    def signal(self, channel: Channel) -> None:
        """Nothing listens."""
        del channel


def app_settings(*, database_url: str, direct_url: str, issuer: LocalIssuer) -> Settings:
    """The apps' settings on a database (units through ``database_url``) and the test's identity provider."""
    return Settings(
        environment="test",
        database_url=SecretStr(database_url),
        database_direct_url=SecretStr(direct_url),
        token_issuer=issuer.issuer,
        token_audience=issuer.audience,
        token_jwks_url=issuer.keys_url,
    )
