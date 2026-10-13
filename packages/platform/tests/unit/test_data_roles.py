import base64
import hashlib
import hmac

import pytest

from ledger_platform.data.roles import (
    APP,
    MIGRATOR,
    RELAY,
    RESERVED_WORDS,
    SCRAM_ITERATIONS,
    SIGNER,
    InvalidSchemaNameError,
    Logins,
    SchemaRoles,
    ShortSaltError,
    UnsupportedPasswordError,
    cluster_statements,
    database_statements,
    identifier,
    migrated_statements,
    random_salt,
    relay_statements,
    scram_verifier,
)

LOGINS = Logins(app="a'pp", migrator="m", signer="s")
UNLIMITED = "CONNECTION LIMIT -1 VALID UNTIL 'infinity'"
GROUP = f"NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION {UNLIMITED} PASSWORD NULL"
LOGIN = f"LOGIN NOINHERIT NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION {UNLIMITED}"
MEMBER = "WITH ADMIN FALSE, INHERIT FALSE, SET TRUE"
SALT = b"0123456789abcdef"


def salted(login: str) -> bytes:
    """A salt of the login's name: the statements a test compares come out the same every time."""
    return login.encode().ljust(16, b"-")[:16]


def password(login: str, secret: str) -> str:
    return f"PASSWORD E'{scram_verifier(secret, salt=salted(login))}'"


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
    statements = cluster_statements(
        (SchemaRoles(schema="platform"), SchemaRoles(schema="identity")), LOGINS, salt=salted
    )
    assert statements[:6] == (
        created(RELAY),
        f'ALTER ROLE "ledger_relay" WITH {GROUP}',
        created(APP),
        f'ALTER ROLE "ledger_app" WITH {LOGIN} {password(APP, "a'pp")}',
        created(MIGRATOR),
        f'ALTER ROLE "ledger_migrator" WITH {LOGIN} {password(MIGRATOR, "m")}',
    )
    assert f'ALTER ROLE "identity_purge" WITH {GROUP}' in statements
    assert f'GRANT "identity_owner" TO "ledger_migrator" {MEMBER}' in statements
    assert f'GRANT "identity_rw" TO "ledger_app" {MEMBER}' in statements
    assert f'GRANT "identity_purge" TO "ledger_app" {MEMBER}' in statements
    assert f'GRANT "ledger_relay" TO "ledger_app" {MEMBER}' in statements
    assert not any("ledger_signer" in statement for statement in statements[:-1])  # the last: its strays
    altered = [statement for statement in statements if statement.startswith("ALTER ROLE")]
    assert len(altered) == 9
    assert all("NOSUPERUSER NOBYPASSRLS" in statement for statement in altered)


def test_a_password_never_reaches_the_server_only_its_scram_verifier_does() -> None:
    hostile = "x$$; DROP ROLE ledger_app; $$'\\"
    statements = cluster_statements((), Logins(app=hostile, migrator="m"), salt=salted)
    assert all(hostile not in statement for statement in statements)
    assert statements[3] == f'ALTER ROLE "ledger_app" WITH {LOGIN} {password(APP, hostile)}'


def test_a_verifier_is_postgresqls_scram_sha_256_form() -> None:
    verifier = scram_verifier("pencil", salt=SALT)
    method, count_and_salt, keys = verifier.split("$")
    iterations, salt = count_and_salt.split(":")
    stored, server = keys.split(":")
    assert (method, int(iterations), base64.b64decode(salt)) == ("SCRAM-SHA-256", SCRAM_ITERATIONS, SALT)
    assert len(base64.b64decode(stored)) == len(base64.b64decode(server)) == 32


def test_a_verifier_answers_rfc_7677s_exchange() -> None:
    """RFC 7677 section 3: its keys make the server signature the RFC's server sent and check its proof."""
    verifier = scram_verifier("pencil", salt=base64.b64decode("W22ZaJ0SNY7soEsUEjb6gQ=="))
    stored, server = (base64.b64decode(key) for key in verifier.rsplit("$", 1)[1].split(":"))
    nonce = "rOprNGfwEbeRWgbNEkqO%hvYDpWUa2RaTCAfuxFIlj)hNlF$k0"
    message = f"n=user,r=rOprNGfwEbeRWgbNEkqO,r={nonce},s=W22ZaJ0SNY7soEsUEjb6gQ==,i=4096,c=biws,r={nonce}"
    signature = hmac.digest(server, message.encode(), "sha256")
    assert base64.b64encode(signature) == b"6rriTRBi23WpRR/wtup+mMhUZUn/dB5nLTJRsjl95G4="
    proof = base64.b64decode("dHzbZapWIk4jUhN+Ute9ytag9zjfMHgsqmmiz7AndVQ=")
    mask = hmac.digest(stored, message.encode(), "sha256")
    client = bytes(a ^ b for a, b in zip(proof, mask, strict=True))
    assert hashlib.sha256(client).digest() == stored


def test_salts_are_random_unless_given() -> None:
    assert len(random_salt(APP)) == 16
    assert random_salt(APP) != random_salt(APP)
    first, second = (cluster_statements((), Logins(app="a", migrator="m"))[3] for _ in range(2))
    assert first != second


@pytest.mark.parametrize("secret", ["contraseña", "", "tab\there"])
def test_a_password_postgresql_would_prepare_otherwise_is_refused(secret: str) -> None:
    """SASLprep rewrites what is not printable ASCII before PostgreSQL hashes it; an empty one is none."""
    with pytest.raises(UnsupportedPasswordError) as raised:
        scram_verifier(secret, salt=SALT)
    assert raised.value.code == "unsupported_password"
    assert secret not in str(raised.value) or not secret


def test_memberships_nobody_granted_here_are_revoked_last() -> None:
    statements = cluster_statements((SchemaRoles(schema="identity"),), LOGINS, salt=salted)
    revoke = statements[-1]
    assert revoke.startswith("DO $$")
    assert "REVOKE %I FROM %I GRANTED BY %I CASCADE" in revoke
    assert "('identity_rw', 'ledger_app')" in revoke
    assert "('identity_owner', 'ledger_migrator')" in revoke
    assert "('ledger_relay', 'ledger_app')" in revoke
    assert "'a''pp'" not in revoke


def test_an_expected_membership_with_other_options_is_a_stray_too() -> None:
    """Another grantor's row of an expected pair (PostgreSQL 16 keeps one per grantor) needs the options."""
    revoke = cluster_statements((SchemaRoles(schema="identity"),), LOGINS, salt=salted)[-1]
    assert (
        "AND NOT membership.admin_option AND NOT membership.inherit_option AND membership.set_option"
        in revoke
    )


def test_a_salt_shorter_than_16_bytes_is_refused() -> None:
    with pytest.raises(ShortSaltError) as raised:
        scram_verifier("pencil", salt=SALT[:15])
    assert raised.value.code == "scram_salt_too_short"
    assert scram_verifier("pencil", salt=SALT + b"x").startswith("SCRAM-SHA-256$4096:")


def test_the_logins_never_show_their_passwords() -> None:
    shown = repr(Logins(app="app-secret", migrator="migrator-secret", signer="signer-secret"))
    assert "secret" not in shown


def test_a_signer_schema_belongs_to_the_signer_login_only() -> None:
    statements = cluster_statements(
        (SchemaRoles(schema="platform"), SchemaRoles(schema="signer", signer=True)), LOGINS, salt=salted
    )
    assert f'GRANT "signer_rw" TO "ledger_signer" {MEMBER}' in statements
    assert f'GRANT "signer_purge" TO "ledger_signer" {MEMBER}' in statements
    assert f'GRANT "signer_rw" TO "ledger_app" {MEMBER}' not in statements
    assert f'GRANT "platform_rw" TO "ledger_signer" {MEMBER}' in statements
    assert f'ALTER ROLE "ledger_signer" WITH {LOGIN} {password(SIGNER, "s")}' in statements
    assert "('platform_rw', 'ledger_signer')" in statements[-1]


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
