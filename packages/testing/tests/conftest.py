"""The testing package's own tests: the platform is the module under test of the module fixtures' tests.

Its schema is in the template, with ``vault``'s: a probe module isolated in the signer's role, its schema
holding the chassis's tables, for the fixtures of an isolated module. The other tests make the databases they
need.
"""

from typing import Final

import pytest

from ledger_platform._manifest import MANIFEST
from ledger_platform.config.manifest import load_manifest
from ledger_platform.data.migrations import SchemaMigrations, platform_migrations
from ledger_testing.database import bootstrap, create_chassis
from ledger_testing.modules import ModuleUnderTest
lazy from ledger_testing.postgres import Template

UNDER_TEST: Final = ModuleUnderTest(MANIFEST, platform_migrations())
VAULT_MANIFEST: Final = (
    '[package]\nname = "vault"\nkind = "module"\nsummary = "A probe isolated module."\nowners = ["@o"]\n'
    '[data]\nschema = "vault"\nisolated_in = "signer"\n'
)
VAULT: Final = ModuleUnderTest(
    load_manifest(VAULT_MANIFEST),
    SchemaMigrations(schema="vault", scripts="vault_unused:migrations"),
)
"""Its schema has no revisions: the template gets the chassis's tables in it instead."""


def pytest_ledger_template(template: Template) -> None:
    """The platform's schema at head; ``vault`` the signer login's, with the chassis's tables."""
    UNDER_TEST.prepare(template)
    bootstrap(template, VAULT.schemas, isolated=VAULT.isolated)
    create_chassis(template, VAULT.schema)


@pytest.fixture
def module_under_test() -> ModuleUnderTest:
    """The platform, as a module's suite names its own."""
    return UNDER_TEST


@pytest.fixture
def isolated_under_test() -> ModuleUnderTest:
    """``vault``, the module an isolated module's suite would name (a test module returns it as its own)."""
    return VAULT
