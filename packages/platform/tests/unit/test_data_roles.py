import pytest

from ledger_platform.data.roles import (
    APP,
    MIGRATOR,
    RELAY,
    RESERVED_WORDS,
    SIGNER,
    InvalidSchemaNameError,
    Logins,
    SchemaRoles,
    cluster_statements,
    database_statements,
    identifier,
    migrated_statements,
    relay_statements,
)

LOGINS = Logins(app="a'pp", migrator="m", signer="s")
GROUP = "NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION"
LOGIN = "LOGIN NOINHERIT NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION"
MEMBER = "WITH ADMIN FALSE, INHERIT FALSE, SET TRUE"


def created(role: str) -> str:
    return f'DO $$ BEGIN CREATE ROLE "{role}"; EXCEPTION WHEN duplicate_object THEN NULL; END $$'


def test_the_login_and_group_roles_are_named_once() -> None:
    assert (APP, MIGRATOR, SIGNER, RELAY) == (
        "ledger_app",
        "ledger_migrator",
        "ledger_signer",
        "ledger_relay",
    )


def test_a_schema_has_an_owner_a_rw_and_a_purge_role() -> None:
    roles = SchemaRoles(schema="identity")
    assert (roles.owner, roles.rw, roles.purge, roles.login) == (
        "identity_owner",
        "identity_rw",
        "identity_purge",
        APP,
    )
    assert SchemaRoles(schema="signer", signer=True).login == SIGNER


def test_identifiers_are_quoted() -> None:
    assert identifier("user") == '"user"'
    assert identifier('a"b') == '"a""b"'


def test_each_role_is_created_if_missing_then_normalized() -> None:
    statements = cluster_statements((SchemaRoles(schema="platform"), SchemaRoles(schema="identity")), LOGINS)
    assert statements[:6] == (
        created(RELAY),
        f'ALTER ROLE "ledger_relay" WITH {GROUP}',
        created(APP),
        f"ALTER ROLE \"ledger_app\" WITH {LOGIN} PASSWORD E'a''pp'",
        created(MIGRATOR),
        f"ALTER ROLE \"ledger_migrator\" WITH {LOGIN} PASSWORD E'm'",
    )
    assert f'ALTER ROLE "identity_purge" WITH {GROUP}' in statements
    assert f'GRANT "identity_owner" TO "ledger_migrator" {MEMBER}' in statements
    assert f'GRANT "identity_rw" TO "ledger_app" {MEMBER}' in statements
    assert f'GRANT "identity_purge" TO "ledger_app" {MEMBER}' in statements
    assert f'GRANT "ledger_relay" TO "ledger_app" {MEMBER}' in statements
    assert not any("ledger_signer" in statement for statement in statements)
    altered = [statement for statement in statements if statement.startswith("ALTER ROLE")]
    assert len(altered) == 9
    assert all("NOSUPERUSER NOBYPASSRLS" in statement for statement in altered)


def test_a_password_never_reaches_a_dollar_quoted_block() -> None:
    hostile = "x$$; DROP ROLE ledger_app; $$'\\"
    statements = cluster_statements((), Logins(app=hostile, migrator="m"))
    assert all(hostile not in statement for statement in statements if statement.startswith("DO"))
    assert (
        statements[3]
        == f"ALTER ROLE \"ledger_app\" WITH {LOGIN} PASSWORD E'x$$; DROP ROLE ledger_app; $$''\\\\'"
    )


def test_a_signer_schema_belongs_to_the_signer_login_only() -> None:
    statements = cluster_statements(
        (SchemaRoles(schema="platform"), SchemaRoles(schema="signer", signer=True)), LOGINS
    )
    assert f'GRANT "signer_rw" TO "ledger_signer" {MEMBER}' in statements
    assert f'GRANT "signer_purge" TO "ledger_signer" {MEMBER}' in statements
    assert f'GRANT "signer_rw" TO "ledger_app" {MEMBER}' not in statements
    assert f'GRANT "platform_rw" TO "ledger_signer" {MEMBER}' in statements
    assert f"ALTER ROLE \"ledger_signer\" WITH {LOGIN} PASSWORD E's'" in statements


def test_a_signer_schema_needs_the_signer_password() -> None:
    with pytest.raises(InvalidSchemaNameError, match="signer") as raised:
        cluster_statements((SchemaRoles(schema="signer", signer=True),), Logins(app="a", migrator="m"))
    assert raised.value.code == "invalid_schema_name"


def on_database(action: str, target: str) -> str:
    return f"DO $$ BEGIN EXECUTE format('{action} ON DATABASE %I {target}', current_database()); END $$"


def test_the_database_is_closed_to_public_and_each_schema_owned_by_its_owner() -> None:
    default = 'ALTER DEFAULT PRIVILEGES FOR ROLE "identity_owner"'
    assert database_statements((SchemaRoles(schema="identity"),)) == (
        on_database("REVOKE TEMPORARY, CONNECT", "FROM PUBLIC"),
        on_database("GRANT CONNECT", 'TO "ledger_app", "ledger_migrator"'),
        "REVOKE ALL ON SCHEMA public FROM PUBLIC",
        'CREATE SCHEMA IF NOT EXISTS "identity" AUTHORIZATION "identity_owner"',
        'GRANT USAGE ON SCHEMA "identity" TO "identity_rw", "identity_purge", "ledger_relay"',
        f'{default} IN SCHEMA "identity" GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "identity_rw"',
        f'{default} IN SCHEMA "identity" GRANT USAGE, SELECT ON SEQUENCES TO "identity_rw"',
        f"{default} REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC",
    )


def test_the_signer_login_connects_when_a_schema_is_the_signers() -> None:
    statements = database_statements((SchemaRoles(schema="signer", signer=True),))
    assert '"ledger_signer"' in statements[1]


def test_the_relay_reaches_the_queue_tables_and_nothing_else() -> None:
    assert relay_statements("identity") == (
        'REVOKE ALL ON "identity".outbox FROM "ledger_relay"',
        'GRANT SELECT, DELETE ON "identity".outbox TO "ledger_relay"',
        'GRANT UPDATE (attempts, unrouted_since) ON "identity".outbox TO "ledger_relay"',
        'GRANT INSERT ON "identity".inbox TO "ledger_relay"',
        'GRANT SELECT ON "identity".subscriptions TO "ledger_relay"',
    )
    assert relay_statements("platform")[-1] == 'GRANT SELECT ON "platform".routing TO "ledger_relay"'


def test_a_migrated_schema_gets_the_relays_grants_and_a_read_only_version_table() -> None:
    assert migrated_statements("identity") == (
        *relay_statements("identity"),
        'REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON "identity".alembic_version FROM "identity_rw"',
    )


@pytest.mark.parametrize(
    "schema",
    [
        "Identity",
        "a",
        "identity-x",
        "x" * 32,
        "public; DROP",
        "1abc",
        "pg",
        "pg_x",
        "public",
        "information_schema",
        "user",
        "select",
        "table",
        "all",
        "authorization",
        "join",
    ],
)
def test_a_schema_name_that_could_not_be_a_role_prefix_is_refused(schema: str) -> None:
    with pytest.raises(InvalidSchemaNameError, match="schema name"):
        SchemaRoles(schema=schema)


def test_names_that_merely_contain_a_keyword_are_fine() -> None:
    assert SchemaRoles(schema="users").schema == "users"
    assert SchemaRoles(schema="pgx").schema == "pgx"
    assert "user" in RESERVED_WORDS


def test_a_refused_schema_name_is_not_echoed_whole() -> None:
    with pytest.raises(InvalidSchemaNameError) as raised:
        SchemaRoles(schema="X" * 200)
    assert "X" * 41 not in str(raised.value)
