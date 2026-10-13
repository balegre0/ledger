"""identity's migrations: from nothing to this version's schema and back, equal to the models, under RLS."""

import re
from typing import Final

import pytest
from alembic import command
from alembic.autogenerate import produce_migrations, render_python_code
from alembic.config import Config
from alembic.migration import MigrationContext
from pydantic import TypeAdapter
from sqlalchemy import CheckConstraint, MetaData, create_engine, inspect, text
from sqlalchemy.pool import NullPool

from ledger_identity.adapters.postgres import migrate
from ledger_identity.adapters.postgres.migrate import (
    SCRIPTS,
    MigrationUrlMissingError,
    OfflineMigrationsError,
    SchemaNotCurrentError,
    downgrade,
    head,
    require_head,
    upgrade,
)
from ledger_identity.adapters.postgres.tables import IdentityTable
from ledger_identity.domain.company import CompanyStatus
from ledger_identity.domain.membership import MembershipStatus
from ledger_identity.domain.roles import Role
from ledger_kernel.errors import ErrorKind
from ledger_platform.database import Database
from ledger_platform.schema import CHASSIS_METADATA

IDENTITY_TABLES: Final = frozenset(
    {"organizations", "companies", "memberships", "branches", "emission_points"}
)
CHASSIS_TABLES: Final = frozenset({"outbox", "inbox", "idempotency_keys"})
POLICIES: Final = TypeAdapter(list[tuple[str, str]])
SECURITY: Final = TypeAdapter(list[tuple[str, bool, bool]])
CHECKS: Final = TypeAdapter(dict[str, str])
CHECKS_SQL: Final = text(
    """
    SELECT conname::text, pg_get_constraintdef(oid) FROM pg_constraint
    WHERE contype = 'c' AND connamespace = 'public'::regnamespace
    """
)
SECURITY_SQL: Final = text(
    """
    SELECT relname::text, relrowsecurity, relforcerowsecurity FROM pg_class
    WHERE relkind = 'r' AND relnamespace = 'public'::regnamespace
    """
)
QUOTED: Final = re.compile(r"'([^']*)'")
"""A literal in a constraint's definition as PostgreSQL prints it back: ``'owner'::character varying``."""


def tables_of(url: str) -> set[str]:
    engine = create_engine(url, poolclass=NullPool)
    try:
        with engine.connect() as connection:
            return set(inspect(connection).get_table_names())
    finally:
        engine.dispose()


def models() -> MetaData:
    """The chassis's tables and identity's, in one metadata, as the migrations must make them."""
    combined = MetaData()
    for table in (*CHASSIS_METADATA.tables.values(), *IdentityTable.metadata.tables.values()):
        table.to_metadata(combined)
    return combined


def pending_changes(url: str) -> str | None:
    """The migration the models would still need, as Alembic would write it; ``None`` when there is none."""
    engine = create_engine(url, poolclass=NullPool)
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(
                connection, opts={"compare_type": True, "compare_server_default": True}
            )
            operations = produce_migrations(context, models()).upgrade_ops
            return None if operations is None or operations.is_empty() else render_python_code(operations)
    finally:
        engine.dispose()


def checks_of(url: str) -> dict[str, str]:
    """Every check constraint of the database, by name, as PostgreSQL prints its definition back."""
    engine = create_engine(url, poolclass=NullPool)
    try:
        with engine.connect() as connection:
            return CHECKS.validate_python(dict(tuple(row) for row in connection.execute(CHECKS_SQL)))
    finally:
        engine.dispose()


def scripts() -> Config:
    config = Config()
    config.set_main_option("script_location", SCRIPTS)
    return config


def test_the_schema_is_built_from_nothing_and_taken_back_to_nothing(fresh_database_url: str) -> None:
    upgrade(fresh_database_url)
    assert tables_of(fresh_database_url) == {*IDENTITY_TABLES, *CHASSIS_TABLES, "alembic_version"}
    downgrade(fresh_database_url)
    assert tables_of(fresh_database_url) == {"alembic_version"}
    upgrade(fresh_database_url)
    assert tables_of(fresh_database_url) >= IDENTITY_TABLES


def test_upgrading_a_database_already_at_this_version_changes_nothing(database_url: str) -> None:
    upgrade(database_url)
    assert pending_changes(database_url) is None


def test_the_migrated_schema_is_exactly_the_models(database_url: str) -> None:
    assert pending_changes(database_url) is None


def test_a_schema_that_differs_from_the_models_is_noticed(fresh_database_url: str) -> None:
    """The comparison sees the expression index of the outbox, so a schema without it is not "the models"."""
    upgrade(fresh_database_url)
    engine = create_engine(fresh_database_url, poolclass=NullPool)
    try:
        with engine.begin() as connection:
            connection.execute(text("DROP INDEX ix_outbox_due"))
    finally:
        engine.dispose()
    assert "ix_outbox_due" in (pending_changes(fresh_database_url) or "")


def test_the_check_constraints_are_the_models_and_allow_exactly_the_domains_words(database_url: str) -> None:
    """Alembic does not compare check constraints: their names and the words they allow are compared here."""
    checks = checks_of(database_url)
    declared = {
        str(constraint.name)
        for table in IdentityTable.metadata.tables.values()
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert set(checks) == declared
    for name, words in {
        "ck_companies_status": CompanyStatus,
        "ck_memberships_role": Role,
        "ck_memberships_status": MembershipStatus,
    }.items():
        assert set(QUOTED.findall(checks[name])) == {str(word) for word in words}, name
    assert {name: checks[name] for name in ("ck_companies_version", "ck_memberships_version")} == {
        "ck_companies_version": "CHECK ((version > 0))",
        "ck_memberships_version": "CHECK ((version > 0))",
    }


def test_every_table_of_a_tenants_data_is_under_its_policy_forced_on_its_owner(database_url: str) -> None:
    engine = create_engine(database_url, poolclass=NullPool)
    try:
        with engine.connect() as connection:
            policies = POLICIES.validate_python(
                [
                    tuple(row)
                    for row in connection.execute(
                        text("SELECT tablename::text, policyname::text FROM pg_policies")
                    )
                ]
            )
            security = SECURITY.validate_python([tuple(row) for row in connection.execute(SECURITY_SQL)])
    finally:
        engine.dispose()
    assert sorted(policies) == [
        ("branches", "tenant_isolation"),
        ("companies", "organization_isolation"),
        ("emission_points", "tenant_isolation"),
        ("idempotency_keys", "organization_isolation"),
        ("memberships", "organization_isolation"),
        ("organizations", "organization_isolation"),
    ]
    forced = {name for name, enabled, force in security if enabled and force}
    assert forced == IDENTITY_TABLES | {
        "idempotency_keys"
    }  # the outbox and the inbox stay outside on purpose


async def test_the_readiness_check_passes_on_a_database_at_this_version_only(
    database: Database, fresh_database_url: str
) -> None:
    assert head() == "0001"
    await require_head(database, head())
    upgrade(fresh_database_url)
    downgrade(fresh_database_url)  # alembic_version is left, empty
    async with Database.open(fresh_database_url, pool_size=1) as behind:
        with pytest.raises(SchemaNotCurrentError) as caught:
            await require_head(behind, head())
    assert (caught.value.kind, caught.value.code) == (ErrorKind.UNAVAILABLE, "schema_not_current")


def test_migrations_are_never_written_out_as_sql() -> None:
    config = scripts()
    config.attributes["url"] = "postgresql+psycopg://nobody@localhost/nothing"
    with pytest.raises(OfflineMigrationsError, match="run against a database"):
        command.upgrade(config, "head", sql=True)


def test_migrations_need_a_database_to_run_against() -> None:
    with pytest.raises(MigrationUrlMissingError, match="without a database URL"):
        command.upgrade(scripts(), "head")


def test_the_command_line_migrates_the_database_its_settings_name(
    monkeypatch: pytest.MonkeyPatch, fresh_database_url: str
) -> None:
    for name, value in {
        "LEDGER_DATABASE_URL": fresh_database_url,
        "LEDGER_TOKEN_ISSUER": "https://issuer.example",
        "LEDGER_TOKEN_AUDIENCE": "ledger",
        "LEDGER_TOKEN_JWKS_URL": "https://issuer.example/keys",
    }.items():
        monkeypatch.setenv(name, value)
    assert migrate.main() == 0
    assert tables_of(fresh_database_url) >= IDENTITY_TABLES
