import pytest

from ledger_platform.data.roles import (
    APP,
    MIGRATOR,
    RELAY,
    SIGNER,
    InvalidSchemaNameError,
    Logins,
    SchemaRoles,
    cluster_statements,
    database_statements,
    relay_statements,
)

LOGINS = Logins(app="a'pp", migrator="m", signer="s")


def test_the_login_and_group_roles_are_named_once() -> None:
    assert (APP, MIGRATOR, SIGNER, RELAY) == (
        "ledger_app",
        "ledger_migrator",
        "ledger_signer",
        "ledger_relay",
    )


def test_a_schema_has_an_owner_and_a_rw_role() -> None:
    roles = SchemaRoles(schema="identity")
    assert (roles.owner, roles.rw, roles.signer) == ("identity_owner", "identity_rw", False)


def test_cluster_roles_are_idempotent_and_never_superusers() -> None:
    statements = cluster_statements((SchemaRoles(schema="platform"), SchemaRoles(schema="identity")), LOGINS)
    assert statements[0] == (
        "DO $$ BEGIN CREATE ROLE ledger_relay NOLOGIN NOSUPERUSER NOBYPASSRLS; "
        "EXCEPTION WHEN duplicate_object THEN NULL; END $$"
    )
    assert statements[1] == (
        "DO $$ BEGIN CREATE ROLE ledger_app LOGIN NOINHERIT NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE "
        "PASSWORD 'a''pp'; EXCEPTION WHEN duplicate_object THEN NULL; END $$"
    )
    assert "GRANT identity_owner TO ledger_migrator" in statements
    assert "GRANT identity_rw TO ledger_app" in statements
    assert "GRANT ledger_relay TO ledger_app" in statements
    assert not any("ledger_signer" in statement for statement in statements)
    created = [statement for statement in statements if "CREATE ROLE" in statement]
    assert len(created) == 7
    assert all("NOSUPERUSER NOBYPASSRLS" in statement for statement in created)


def test_a_signer_schema_belongs_to_the_signer_login_only() -> None:
    statements = cluster_statements(
        (SchemaRoles(schema="platform"), SchemaRoles(schema="signer", signer=True)), LOGINS
    )
    assert "GRANT signer_rw TO ledger_signer" in statements
    assert "GRANT signer_rw TO ledger_app" not in statements
    assert "GRANT platform_rw TO ledger_signer" in statements
    assert any(
        statement.startswith("DO $$ BEGIN CREATE ROLE ledger_signer LOGIN") for statement in statements
    )


def test_a_signer_schema_needs_the_signer_password() -> None:
    with pytest.raises(InvalidSchemaNameError, match="signer") as raised:
        cluster_statements((SchemaRoles(schema="signer", signer=True),), Logins(app="a", migrator="m"))
    assert raised.value.code == "invalid_schema_name"


def test_each_schema_is_owned_by_its_owner_and_used_by_its_rw_role_and_the_relay() -> None:
    default = "ALTER DEFAULT PRIVILEGES FOR ROLE identity_owner IN SCHEMA identity GRANT"
    assert database_statements((SchemaRoles(schema="identity"),)) == (
        "CREATE SCHEMA IF NOT EXISTS identity AUTHORIZATION identity_owner",
        "GRANT USAGE ON SCHEMA identity TO identity_rw, ledger_relay",
        f"{default} SELECT, INSERT, UPDATE, DELETE ON TABLES TO identity_rw",
        f"{default} USAGE, SELECT ON SEQUENCES TO identity_rw",
    )


def test_the_relay_reaches_the_queue_tables_and_nothing_else() -> None:
    assert relay_statements("identity") == (
        "GRANT SELECT, UPDATE, DELETE ON identity.outbox TO ledger_relay",
        "GRANT INSERT ON identity.inbox TO ledger_relay",
        "GRANT SELECT ON identity.subscriptions TO ledger_relay",
    )
    assert relay_statements("platform")[-1] == "GRANT SELECT ON platform.routing TO ledger_relay"


@pytest.mark.parametrize("schema", ["Identity", "a", "identity-x", "x" * 32, "public; DROP", "1abc", "pg_x"])
def test_a_schema_name_that_could_not_be_a_role_prefix_is_refused(schema: str) -> None:
    with pytest.raises(InvalidSchemaNameError, match="schema name"):
        SchemaRoles(schema=schema)


def test_a_refused_schema_name_is_not_echoed_whole() -> None:
    with pytest.raises(InvalidSchemaNameError) as raised:
        SchemaRoles(schema="X" * 200)
    assert "X" * 41 not in str(raised.value)
