"""The roles of §6.2 as PostgreSQL enforces them: one ``psycopg`` connection per test, session state shows."""

import uuid
from contextlib import contextmanager
from typing import Final
lazy from collections.abc import Generator

import psycopg
import pytest
from psycopg import sql
from pydantic import TypeAdapter

from ledger_platform.data.rls import RlsScope, policy
from ledger_platform.data.roles import (
    APP,
    MIGRATOR,
    RELAY,
    RESERVED_WORDS,
    Logins,
    SchemaRoles,
    cluster_statements,
)
from ledger_testing.database import TEST_LOGINS, libpq_as
lazy from ledger_testing.postgres import WorkerDatabase

A, B = uuid.uuid7(), uuid.uuid7()
_COUNT: Final = TypeAdapter(tuple[int])
_WORDS: Final = TypeAdapter(list[tuple[str]])
GRANTS: Final = TypeAdapter(list[tuple[str, str]])
HOSTILE = "p$$w'o\\rd$$; DROP ROLE ledger_app; --"
"""A password with dollar quotes, a quote and a backslash."""
_ROLES: Final = TypeAdapter(list[tuple[str, bool, bool, bool, bool, bool, bool]])
A1, A2, B1 = uuid.uuid7(), uuid.uuid7(), uuid.uuid7()
INSERT_KEY = """
    INSERT INTO probe.idempotency_keys
        (organization_id, mode, principal, route, key, request_hash, created_at, expires_at)
    VALUES (%s, %s, 'user:1', 'POST /v1/things', %s, '\\x00', now(), now() + interval '1 day')
"""


def app(chassis: WorkerDatabase) -> psycopg.Connection:
    return psycopg.connect(libpq_as(chassis, role=APP, password=TEST_LOGINS.app))


def as_role(connection: psycopg.Connection, role: str) -> None:
    connection.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role)))


def scope(
    connection: psycopg.Connection,
    organization: uuid.UUID | None = None,
    company: uuid.UUID | None = None,
    mode: str | None = None,
) -> None:
    """What a unit of work sets: ``''`` for a part it has not, as the platform's units do."""
    connection.execute(
        """SELECT set_config('ledger.organization_id', %s, true), set_config('ledger.company_id', %s, true),
                  set_config('ledger.mode', %s, true)""",
        (
            "" if organization is None else str(organization),
            "" if company is None else str(company),
            mode or "",
        ),
    )


def count(connection: psycopg.Connection, table: sql.Composable) -> int:
    row = connection.execute(sql.SQL("SELECT count(*) FROM {}").format(table)).fetchone()
    return _COUNT.validate_python(row)[0]


def keys(connection: psycopg.Connection) -> int:
    return count(connection, sql.Identifier("probe", "idempotency_keys"))


@contextmanager
def rolled_back(url: str) -> Generator[psycopg.Connection]:
    """A connection whose one transaction is rolled back at the end, whatever happened in it."""
    with psycopg.connect(url) as connection:
        try:
            yield connection
        finally:
            connection.rollback()


@pytest.mark.parametrize(
    "table", ["probe.outbox", "probe.idempotency_keys", "platform.jobs", "platform.routing"]
)
def test_the_app_login_reaches_no_table_without_a_module_role(chassis: WorkerDatabase, table: str) -> None:
    schema, name = table.split(".")
    with app(chassis) as connection, pytest.raises(psycopg.errors.InsufficientPrivilege):
        count(connection, sql.Identifier(schema, name))


def test_the_migrator_login_reaches_no_table_and_makes_none_without_an_owner_role(
    chassis: WorkerDatabase,
) -> None:
    with psycopg.connect(libpq_as(chassis, role=MIGRATOR, password=TEST_LOGINS.migrator)) as connection:
        with pytest.raises(psycopg.errors.InsufficientPrivilege), connection.transaction():
            count(connection, sql.Identifier("probe", "outbox"))
        with pytest.raises(psycopg.errors.InsufficientPrivilege), connection.transaction():
            connection.execute("CREATE TABLE probe.extra (n int)")
        with pytest.raises(psycopg.errors.InsufficientPrivilege), connection.transaction():
            as_role(connection, "probe_rw")


@pytest.mark.parametrize("table", ["other.outbox", "other.idempotency_keys", "platform.jobs"])
def test_a_unit_in_a_module_role_reaches_its_schema_only(chassis: WorkerDatabase, table: str) -> None:
    schema, name = table.split(".")
    with app(chassis) as connection:
        with connection.transaction():
            as_role(connection, "probe_rw")
            assert count(connection, sql.Identifier("probe", "outbox")) == 0
            with pytest.raises(psycopg.errors.InsufficientPrivilege), connection.transaction():
                count(connection, sql.Identifier(schema, name))  # a savepoint: the outer unit goes on
            assert count(connection, sql.Identifier("probe", "jobs")) == 0
        assert connection.execute("SELECT current_user").fetchone() == (APP,)  # SET LOCAL ended with it


@pytest.mark.parametrize(
    "statement",
    [
        "CREATE TABLE probe.extra (n int)",
        "ALTER TABLE probe.outbox ADD COLUMN extra int",
        "DROP TABLE probe.jobs",
        "CREATE INDEX extra ON probe.jobs (kind)",
        "ALTER TABLE probe.idempotency_keys DISABLE ROW LEVEL SECURITY",
        "DROP POLICY idempotency_keys_select ON probe.idempotency_keys",
    ],
)
def test_the_rw_role_cannot_change_the_schema(chassis: WorkerDatabase, statement: str) -> None:
    with app(chassis) as connection, connection.transaction():
        as_role(connection, "probe_rw")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute(statement.encode())


def test_the_rw_role_cannot_pass_its_privileges_on(chassis: WorkerDatabase) -> None:
    """PostgreSQL answers a grant without the grant option with a warning, and grants nothing."""
    with app(chassis) as connection, connection.transaction():
        as_role(connection, "probe_rw")
        connection.execute("GRANT SELECT, INSERT ON probe.outbox TO other_rw")
        assert connection.execute(
            "SELECT has_table_privilege('other_rw', 'probe.outbox', 'SELECT, INSERT')"
        ).fetchone() == (False,)


AUDIT = """
    INSERT INTO probe.audit_events
        (id, occurred_at, organization_id, actor, call_id, action, subject_type, subject_id)
    VALUES (gen_random_uuid(), now(), %s, 'system:test', 'c', 'probe.touch', 'probe', 'x')
"""


def test_audit_events_are_append_only_for_the_rw_role(chassis: WorkerDatabase) -> None:
    with app(chassis) as connection, connection.transaction():
        as_role(connection, "probe_rw")
        scope(connection, A)
        connection.execute(AUDIT, (A,))
        for statement in (
            b"UPDATE probe.audit_events SET actor = 'x'",
            b"DELETE FROM probe.audit_events",
            b"TRUNCATE probe.audit_events",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege), connection.transaction():
                connection.execute(statement)
        assert count(connection, sql.Identifier("probe", "audit_events")) == 1


def test_audit_events_are_seen_and_written_per_organization(chassis: WorkerDatabase) -> None:
    audit = sql.Identifier("probe", "audit_events")
    refused = pytest.raises(psycopg.errors.InsufficientPrivilege, match="row-level security")
    with app(chassis) as connection:
        for organization in (A, B):
            with connection.transaction():
                as_role(connection, "probe_rw")
                scope(connection, organization, A1, "test")
                connection.execute(AUDIT, (organization,))
        with connection.transaction():
            as_role(connection, "probe_rw")
            scope(connection, A, A1, "live")
            assert count(connection, audit) == 1  # the organization's rows, whatever the company and mode
            with refused, connection.transaction():
                connection.execute(AUDIT, (B,))
            with refused, connection.transaction():
                connection.execute(AUDIT, (None,))
        with connection.transaction():
            as_role(connection, "probe_rw")
            assert count(connection, audit) == 0
            with refused, connection.transaction():
                connection.execute(AUDIT, (A,))


@pytest.mark.parametrize("role", ["probe_owner", "ledger_migrator", "other_owner", "platform_owner"])
def test_the_app_login_cannot_become_an_owner_or_another_login(chassis: WorkerDatabase, role: str) -> None:
    with app(chassis) as connection, pytest.raises(psycopg.errors.InsufficientPrivilege):
        connection.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(role)))


def test_no_role_of_the_platform_is_a_superuser_or_bypasses_row_level_security(
    chassis: WorkerDatabase,
) -> None:
    owned = [
        f"{schema}_{suffix}"
        for schema in ("platform", "probe", "other")
        for suffix in ("owner", "rw", "purge")
    ]
    with psycopg.connect(chassis.admin_url()) as connection:
        rows = connection.execute(
            """SELECT rolname::text, rolsuper, rolbypassrls, rolcreatedb, rolcreaterole, rolinherit,
                      rolcanlogin
               FROM pg_roles WHERE rolname = ANY(%s)""",
            ([APP, MIGRATOR, RELAY, *owned],),
        ).fetchall()
    found = _ROLES.validate_python(rows)
    assert len(found) == 12
    for name, superuser, bypass, createdb, createrole, inherit, login in found:
        assert (superuser, bypass, createdb, createrole) == (False, False, False, False), name
        assert login == (name in {APP, MIGRATOR}), name
        assert not (login and inherit), name  # a login reaches a schema only through SET ROLE


def test_the_relay_reaches_the_queue_tables_and_nothing_else(chassis: WorkerDatabase) -> None:
    allowed = (
        b"SELECT id FROM probe.outbox",
        b"SELECT id FROM probe.outbox FOR UPDATE SKIP LOCKED",
        b"UPDATE probe.outbox SET attempts = attempts + 1, unrouted_since = now()",
        b"UPDATE probe.outbox SET unrouted_since = NULL",
        b"DELETE FROM probe.outbox",
        (
            b"INSERT INTO probe.inbox (consumer, event_id, type, version, source, occurred_at, payload)"
            b" VALUES ('c', gen_random_uuid(), 't', 1, 's', now(), '{}')"
        ),
        b"SELECT consumer FROM probe.subscriptions",
        b"SELECT version FROM platform.routing",
        b"SELECT id FROM platform.outbox",
    )
    refused = (
        (
            b"INSERT INTO probe.outbox (id, type, version, source, occurred_at, payload)"
            b" VALUES (gen_random_uuid(), 't', 1, 's', now(), '{}')"
        ),
        b"UPDATE probe.outbox SET payload = '{}'",
        b"UPDATE probe.outbox SET type = 'other'",
        b"SELECT consumer FROM probe.inbox",
        b"SELECT kind FROM probe.jobs",
        b"SELECT key FROM probe.idempotency_keys",
        b"SELECT id FROM probe.audit_events",
        b"INSERT INTO probe.subscriptions VALUES ('c', 't', true, now())",
        b"UPDATE platform.routing SET version = version + 1",
        b"SELECT name FROM platform.schedule_leases",
    )
    with app(chassis) as connection, connection.transaction():
        as_role(connection, RELAY)
        for statement in allowed:
            with connection.transaction():
                connection.execute(statement)
        for statement in refused:
            with pytest.raises(psycopg.errors.InsufficientPrivilege), connection.transaction():
                connection.execute(statement)


class UnitFailedError(Exception):
    pass


def failing_unit(connection: psycopg.Connection) -> None:
    with connection.transaction():
        as_role(connection, "probe_rw")
        scope(connection, A, mode="test")
        connection.execute(INSERT_KEY, (A, "test", "rolled-back"))
        raise UnitFailedError


def test_a_pooled_connection_carries_nothing_from_one_unit_into_the_next(chassis: WorkerDatabase) -> None:
    """Review focus 1: one connection, as a pool of one hands it from unit to unit."""
    with app(chassis) as connection:
        for organization, mode in ((A, "test"), (B, "test")):
            with connection.transaction():
                as_role(connection, "probe_rw")
                scope(connection, organization, mode=mode)
                connection.execute(INSERT_KEY, (organization, mode, f"key-{organization}"))
        with connection.transaction():  # scoped to A: A's key only
            as_role(connection, "probe_rw")
            scope(connection, A, A1, "test")
            assert keys(connection) == 1
        with connection.transaction():  # no scope: the settings read '' now, NULLIF makes them match nothing
            as_role(connection, "probe_rw")
            assert connection.execute(
                "SELECT current_setting('ledger.organization_id', true)"
            ).fetchone() == ("",)
            assert keys(connection) == 0
            with pytest.raises(psycopg.errors.InsufficientPrivilege), connection.transaction():
                connection.execute(INSERT_KEY, (A, "test", "unscoped"))
        with connection.transaction():  # scoped to B: B's key only
            as_role(connection, "probe_rw")
            scope(connection, B, mode="test")
            row = connection.execute("SELECT organization_id FROM probe.idempotency_keys").fetchall()
            assert row == [(B,)]
        with connection.transaction():  # a mode-less scope sees no moded row
            as_role(connection, "probe_rw")
            scope(connection, B)
            assert keys(connection) == 0
        with connection.transaction():  # another module's role: refused on the first module's tables
            as_role(connection, "other_rw")
            scope(connection, A, A1, "test")
            with pytest.raises(psycopg.errors.InsufficientPrivilege), connection.transaction():
                keys(connection)
        with pytest.raises(UnitFailedError):  # a rolled-back unit leaves nothing behind
            failing_unit(connection)
        assert connection.execute(
            """SELECT current_user::text, current_setting('ledger.organization_id', true),
                      current_setting('ledger.mode', true)"""
        ).fetchone() == (APP, "", "")
        connection.rollback()
        with connection.transaction():
            as_role(connection, "probe_rw")
            scope(connection, A, mode="test")
            assert keys(connection) == 1


ROWS = ((A, A1, "test"), (A, A1, "live"), (A, A2, "test"), (B, B1, "test"))
"""Two organizations, three companies, two modes: what each scope's policy is tried on."""
UNITS = (
    (None, None, None),
    (A, A1, "test"),
    (A, A1, None),
    (A, None, None),
    (A, None, "test"),
    (B, B1, "test"),
    (A, B1, "test"),
)
"""The units that read them: no scope, a tenant, a company without a mode, an organization (with and without a
mode), another organization's tenant, and a company of another organization claimed by the first."""


@pytest.mark.parametrize(
    ("scope_of", "visible"),
    [
        (RlsScope.TENANT, (0, 1, 0, 0, 0, 1, 0)),
        (RlsScope.COMPANY, (0, 2, 2, 0, 0, 1, 0)),
        (RlsScope.ORGANIZATION, (0, 3, 3, 3, 3, 1, 3)),
        (RlsScope.MODED_ORGANIZATION, (0, 2, 0, 0, 2, 1, 2)),
    ],
)
def test_each_scope_shows_its_rows_and_refuses_every_other_write(
    chassis: WorkerDatabase, scope_of: RlsScope, visible: tuple[int, ...]
) -> None:
    table = f"probe.scoped_{scope_of.value}"
    name = sql.Identifier("probe", f"scoped_{scope_of.value}")
    insert = sql.SQL("INSERT INTO {} (organization_id, company_id, mode, n) VALUES (%s, %s, %s, 1)").format(
        name
    )
    with rolled_back(chassis.admin_url()) as connection:
        as_role(connection, "probe_owner")
        connection.execute(
            sql.SQL(
                "CREATE TABLE {} (organization_id uuid NOT NULL, company_id uuid, mode text, n int)"
            ).format(name)
        )
        for statement in policy(table, scope_of):
            connection.execute(statement.encode())
        as_role(connection, "probe_rw")
        for organization, company, mode in ROWS:
            scope(connection, organization, company, mode)
            connection.execute(insert, (organization, company, mode))
        seen: list[int] = []
        for settings in UNITS:
            scope(connection, *settings)
            seen.append(count(connection, name))
        assert tuple(seen) == visible
        scope(connection)
        assert connection.execute(sql.SQL("UPDATE {} SET n = 2").format(name)).rowcount == 0
        assert connection.execute(sql.SQL("DELETE FROM {}").format(name)).rowcount == 0
        with pytest.raises(psycopg.errors.InsufficientPrivilege), connection.transaction():
            connection.execute(insert, (A, A1, "test"))
        scope(connection, A, A1, "test")
        with pytest.raises(psycopg.errors.InsufficientPrivilege), connection.transaction():
            connection.execute(insert, (B, B1, "test"))
        with pytest.raises(psycopg.errors.InsufficientPrivilege), connection.transaction():
            connection.execute(sql.SQL("UPDATE {} SET organization_id = %s").format(name), (B,))
        assert connection.execute(sql.SQL("DELETE FROM {}").format(name)).rowcount == visible[1]
        scope(connection, B, B1, "test")
        assert count(connection, name) == 1


def test_the_relays_grants_were_made_by_each_schemas_owner(chassis: WorkerDatabase) -> None:
    with psycopg.connect(chassis.admin_url()) as connection:
        rows = connection.execute(
            """SELECT DISTINCT table_schema::text, grantor::text FROM information_schema.table_privileges
               WHERE grantee = 'ledger_relay'
               UNION
               SELECT DISTINCT table_schema::text, grantor::text FROM information_schema.column_privileges
               WHERE grantee = 'ledger_relay'
               ORDER BY 1"""
        ).fetchall()
    # Other packages' tests migrate their schemas into the same template (identity's): each its owner's too.
    grants = GRANTS.validate_python([tuple(row) for row in rows])
    assert all(grantor == f"{schema}_owner" for schema, grantor in grants)
    assert {("other", "other_owner"), ("platform", "platform_owner"), ("probe", "probe_owner")} <= set(grants)


def test_the_version_table_is_read_only_for_the_rw_role(chassis: WorkerDatabase) -> None:
    with app(chassis) as connection, connection.transaction():
        as_role(connection, "platform_rw")
        assert connection.execute("SELECT version_num FROM platform.alembic_version").fetchall() == [
            ("0001",)
        ]
        for statement in (
            b"UPDATE platform.alembic_version SET version_num = 'ffff'",
            b"DELETE FROM platform.alembic_version",
            b"INSERT INTO platform.alembic_version VALUES ('ffff')",
            b"TRUNCATE platform.alembic_version",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege), connection.transaction():
                connection.execute(statement)


def test_the_reserved_words_are_postgresqls(chassis: WorkerDatabase) -> None:
    with psycopg.connect(chassis.admin_url()) as connection:
        rows = connection.execute("SELECT word FROM pg_get_keywords() WHERE catcode IN ('R', 'T')").fetchall()
    assert {word for (word,) in _WORDS.validate_python(rows)} == RESERVED_WORDS


def test_a_login_signs_in_with_its_password_whose_verifier_alone_reached_the_server(
    chassis: WorkerDatabase,
) -> None:
    """The server keeps what it was sent (a SCRAM verifier), and the password still opens a session."""
    statements = cluster_statements((), Logins(app=HOSTILE, migrator="m"))
    verifier = statements[3].split(" PASSWORD ", 1)[1]
    server = chassis.server
    role = f"scram_{uuid.uuid7().hex}"

    def sign_in(password: str) -> None:
        with psycopg.connect(
            host=server.host, port=server.port, dbname="postgres", user=role, password=password
        ) as connection:
            connection.execute("SELECT 1")

    with psycopg.connect(chassis.admin_url(), autocommit=True) as admin:
        admin.execute(f"CREATE ROLE {role} LOGIN PASSWORD {verifier}".encode())
        try:
            kept = admin.execute("SELECT rolpassword FROM pg_authid WHERE rolname = %s", (role,)).fetchone()
            sign_in(HOSTILE)
            with pytest.raises(psycopg.OperationalError, match="password authentication failed"):
                sign_in(HOSTILE + "x")
        finally:
            admin.execute(f"DROP ROLE {role}".encode())
    assert kept is not None
    assert kept[0] == verifier.removeprefix("E'").removesuffix("'")


def test_roles_that_exist_with_other_options_are_normalized(chassis: WorkerDatabase) -> None:
    roles = SchemaRoles(schema="stale")
    with psycopg.connect(chassis.admin_url(), autocommit=True) as connection:
        try:
            connection.execute(
                """CREATE ROLE stale_owner LOGIN INHERIT BYPASSRLS CREATEDB PASSWORD 'old'
                   CONNECTION LIMIT 1 VALID UNTIL '2000-01-01'"""
            )
            connection.execute("CREATE ROLE stale_rw INHERIT CONNECTION LIMIT 0")
            connection.execute("CREATE ROLE stale_intruder LOGIN")
            connection.execute("CREATE ROLE stale_grantor")
            connection.execute("GRANT stale_rw TO ledger_app WITH INHERIT TRUE, ADMIN TRUE")
            # memberships nobody asked for: the app login in an owner role, an intruder in a rw role, a
            # module role in a predefined one, the migrator login in a predefined one
            connection.execute("GRANT stale_owner TO ledger_app")
            connection.execute("GRANT stale_rw TO stale_intruder")
            connection.execute("GRANT pg_read_all_stats TO stale_rw")
            connection.execute("GRANT pg_read_all_stats TO ledger_migrator")
            # an expected pair granted a second time, by another grantor and with other options
            connection.execute("GRANT stale_rw TO stale_grantor WITH ADMIN TRUE")
            connection.execute("GRANT stale_rw TO ledger_app WITH INHERIT TRUE GRANTED BY stale_grantor")
            for statement in cluster_statements((roles,), TEST_LOGINS):
                connection.execute(statement.encode())
            options = connection.execute(
                """SELECT rolname::text, rolcanlogin, rolbypassrls, rolcreatedb, rolpassword IS NULL,
                          rolconnlimit, rolvaliduntil = 'infinity'
                   FROM pg_authid WHERE rolname IN ('stale_owner', 'stale_purge', 'stale_rw') ORDER BY 1"""
            ).fetchall()
            member = connection.execute(
                """SELECT inherit_option, admin_option, set_option FROM pg_auth_members
                   WHERE roleid = 'stale_rw'::regrole AND member = 'ledger_app'::regrole"""
            ).fetchall()
            memberships = connection.execute(
                """SELECT granted.rolname::text, member.rolname::text
                   FROM pg_auth_members
                   JOIN pg_roles granted ON granted.oid = roleid
                   JOIN pg_roles member ON member.oid = pg_auth_members.member
                   WHERE granted.rolname LIKE 'stale%%' OR member.rolname LIKE 'stale%%'
                      OR (granted.rolname = 'pg_read_all_stats' AND member.rolname = 'ledger_migrator')
                   ORDER BY 1, 2"""
            ).fetchall()
        finally:
            connection.execute(
                "DROP ROLE IF EXISTS stale_owner, stale_rw, stale_purge, stale_intruder, stale_grantor"
            )
    assert options == [
        ("stale_owner", False, False, False, True, -1, True),
        ("stale_purge", False, False, False, True, -1, True),
        ("stale_rw", False, False, False, True, -1, True),
    ]
    assert member == [(False, False, True)]
    assert memberships == [
        ("stale_owner", "ledger_migrator"),
        ("stale_purge", "ledger_app"),
        ("stale_rw", "ledger_app"),
    ]
