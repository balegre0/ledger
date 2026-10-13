"""``chassis_schema_v1`` as PostgreSQL built it in the template: tables, indexes, storage, RLS."""

import uuid
from typing import Final
lazy from collections.abc import Iterator

import psycopg
import pytest
from psycopg import sql
from pydantic import TypeAdapter
from sqlalchemy import Column, MetaData, Text

from ledger_platform.data.roles import APP
from ledger_platform.data.tables import chassis_tables, platform_tables
from ledger_testing.database import TEST_LOGINS, libpq_as
from ledger_testing.migrations import pending_changes
lazy from ledger_testing.postgres import WorkerDatabase

CHASSIS = ("audit_events", "idempotency_keys", "inbox", "jobs", "outbox", "subscriptions")
A, B = uuid.uuid7(), uuid.uuid7()
INSERT_KEY = """
    INSERT INTO probe.idempotency_keys
        (organization_id, mode, principal, route, key, request_hash, created_at, expires_at)
    VALUES (%s, %s, 'user:1', 'POST /v1/things', %s, '\\x00', now() - interval '8 days', now() + %s::interval)
"""
OUTBOX = """
    INSERT INTO probe.outbox (id, type, version, source, mode, occurred_at, payload)
    VALUES (gen_random_uuid(), 't', 1, 's', %s, now(), jsonb_build_object('x', repeat('a', %s)))
"""
"""An outbox row whose payload, as text, is 9 bytes longer than the repeated string (``{"x": "..."}``)."""
INBOX = """
    INSERT INTO probe.inbox (consumer, event_id, type, version, source, occurred_at, payload, status)
    VALUES ('c', gen_random_uuid(), 't', 1, 's', now(), '{}', %s)
"""
INBOX_DEFAULTS = """
    INSERT INTO probe.inbox (consumer, event_id, type, version, source, occurred_at, payload)
    VALUES ('c', gen_random_uuid(), 't', 1, 's', now(), '{}')
"""
JOB = """
    INSERT INTO probe.jobs (kind, key, status, run_at, max_attempts, created_at, updated_at)
    VALUES ('k', 'one', %s, now(), 3, now(), now())
"""
SUBSCRIPTION = "INSERT INTO probe.subscriptions VALUES ('c', 't', true, now())"


def index(name: str, table: str, columns: str, status: str | None = None) -> str:
    """An index as ``pg_indexes.indexdef`` prints it, partial on ``status`` when given."""
    where = "" if status is None else f" WHERE (status {status}::text)"
    return f"CREATE INDEX {name} ON probe.{table} USING btree ({columns}){where}"


INDEXES = {
    "outbox_due": "CREATE INDEX outbox_due ON probe.outbox USING btree (id) WHERE (unrouted_since IS NULL)",
    "inbox_due": index("inbox_due", "inbox", "consumer, next_attempt_at", "= 'ready'"),
    "inbox_expired": index("inbox_expired", "inbox", "consumer, lease_until", "= 'running'"),
    "inbox_head": index("inbox_head", "inbox", "consumer, subject, event_id", "<> 'dead'"),
    "jobs_due": index("jobs_due", "jobs", "kind, run_at", "= 'ready'"),
    "jobs_expired": index("jobs_expired", "jobs", "kind, lease_until", "= 'running'"),
    "jobs_parked": index("jobs_parked", "jobs", "kind, company_id, parked_reason", "= 'parked'"),
    "idempotency_expiry": index("idempotency_expiry", "idempotency_keys", "expires_at"),
}
"""§6.4's indexes as PostgreSQL prints them back: a change to the DDL shows here first."""
QUEUE = [
    "autovacuum_vacuum_scale_factor=0.0",
    "autovacuum_vacuum_threshold=1000",
    "autovacuum_vacuum_cost_delay=0",
]
_KEYS: Final = TypeAdapter(list[tuple[uuid.UUID, str]])
_NAMED: Final = TypeAdapter(list[tuple[str, str]])
_OPTIONS: Final = TypeAdapter(dict[str, list[str] | None])
_SECURED: Final = TypeAdapter(list[tuple[str, bool, bool]])


@pytest.fixture
def connection(chassis: WorkerDatabase) -> Iterator[psycopg.Connection]:
    """The application's login on the worker's database (a test enters ``probe_rw`` per unit)."""
    with psycopg.connect(libpq_as(chassis, role=APP, password=TEST_LOGINS.app)) as opened:
        yield opened


def admin(chassis: WorkerDatabase) -> psycopg.Connection:
    return psycopg.connect(chassis.admin_url())


def unit(
    connection: psycopg.Connection,
    organization: uuid.UUID | None = None,
    mode: str = "",
    *,
    role: str = "probe_rw",
) -> None:
    """Enter ``role`` and set the unit's settings (``''`` for what it has not, as units do)."""
    connection.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role)))
    connection.execute(
        "SELECT set_config('ledger.organization_id', %s, true), set_config('ledger.mode', %s, true)",
        ("" if organization is None else str(organization), mode),
    )


def expiring(connection: psycopg.Connection) -> None:
    """Keys of A (expired), B (expired) and B (live), each written in its own unit."""
    rows = ((A, "a-expired", "-1 second"), (B, "b-expired", "-1 day"), (B, "b-live", "1 day"))
    for organization, key, expires in rows:
        with connection.transaction():
            unit(connection, organization, "test")
            connection.execute(INSERT_KEY, (organization, "test", key, expires))


def keys(connection: psycopg.Connection) -> list[tuple[uuid.UUID, str]]:
    rows = connection.execute(
        "SELECT organization_id, key FROM probe.idempotency_keys ORDER BY key"
    ).fetchall()
    return _KEYS.validate_python(rows)


def test_every_chassis_table_exists_in_the_schema(chassis: WorkerDatabase) -> None:
    with admin(chassis) as connection:
        tables = connection.execute(
            """SELECT schemaname::text, tablename::text FROM pg_tables
               WHERE schemaname = ANY(%s) ORDER BY 1, 2""",
            (["platform", "probe", "other"],),
        ).fetchall()
        assert connection.execute("SELECT version FROM platform.routing").fetchall() == [(0,)]
    assert tables == [
        *(("other", name) for name in CHASSIS),
        *(("platform", name) for name in ("alembic_version", *CHASSIS[:4], "outbox", "routing")),
        ("platform", "schedule_leases"),
        ("platform", "subscriptions"),
        *(("probe", name) for name in CHASSIS),
    ]


def test_the_routing_version_is_one_row(chassis: WorkerDatabase) -> None:
    with admin(chassis) as connection, pytest.raises(psycopg.errors.UniqueViolation):
        connection.execute("INSERT INTO platform.routing (version) VALUES (1)")


def models_of(schema: str) -> MetaData:
    """The chassis's tables of ``schema`` (and the platform's own in ``platform``) in a metadata."""
    metadata = MetaData()
    tables = chassis_tables(schema)
    platform = platform_tables()
    every = [tables.outbox, tables.inbox, tables.jobs, tables.subscriptions, tables.idempotency_keys]
    every += [
        tables.audit_events,
        *((platform.routing, platform.schedule_leases) if schema == "platform" else ()),
    ]
    for table in every:
        table.to_metadata(metadata)
    return metadata


def superuser_url(chassis: WorkerDatabase) -> str:
    server = chassis.server
    return chassis.url(role=server.user, password=server.password)


@pytest.mark.parametrize("schema", ["probe", "other", "platform"])
def test_the_models_match_the_migrated_tables(chassis: WorkerDatabase, schema: str) -> None:
    assert pending_changes(superuser_url(chassis), models_of(schema), schema=schema) is None


def test_a_model_that_differs_from_the_tables_is_noticed(chassis: WorkerDatabase) -> None:
    metadata = models_of("probe")
    metadata.tables["probe.outbox"].append_column(Column("extra", Text))
    assert "extra" in (pending_changes(superuser_url(chassis), metadata, schema="probe") or "")


def test_the_partial_indexes_are_the_specs(chassis: WorkerDatabase) -> None:
    with admin(chassis) as connection:
        rows = connection.execute(
            """SELECT indexname::text, indexdef FROM pg_indexes
               WHERE schemaname = 'probe' AND indexname NOT LIKE '%%_pkey'"""
        ).fetchall()
    assert dict(_NAMED.validate_python(rows)) == INDEXES


def test_storage_parameters_favour_queues(chassis: WorkerDatabase) -> None:
    with admin(chassis) as connection:
        rows = connection.execute(
            """SELECT relname::text, reloptions FROM pg_class
               WHERE relnamespace = 'probe'::regnamespace AND relkind = 'r'"""
        ).fetchall()
    assert _OPTIONS.validate_python(dict(rows)) == {
        "audit_events": None,
        "idempotency_keys": None,
        "inbox": ["fillfactor=80", *QUEUE],
        "jobs": ["fillfactor=80", *QUEUE],
        "outbox": ["fillfactor=90", *QUEUE],
        "subscriptions": None,
    }


def test_idempotency_keys_and_audit_events_are_under_forced_row_level_security(
    chassis: WorkerDatabase,
) -> None:
    with admin(chassis) as connection:
        rows = connection.execute(
            """SELECT relname::text, relrowsecurity, relforcerowsecurity FROM pg_class
               WHERE relnamespace = 'probe'::regnamespace AND relkind = 'r'
                 AND (relrowsecurity OR relforcerowsecurity)"""
        ).fetchall()
        policies = connection.execute(
            "SELECT policyname::text, cmd FROM pg_policies WHERE schemaname = 'probe' ORDER BY 1"
        ).fetchall()
    assert sorted(_SECURED.validate_python(rows)) == [
        ("audit_events", True, True),
        ("idempotency_keys", True, True),
    ]
    assert _NAMED.validate_python(policies) == [
        ("audit_events_insert", "INSERT"),
        ("audit_events_select", "SELECT"),
        ("idempotency_keys_delete", "DELETE"),
        ("idempotency_keys_insert", "INSERT"),
        ("idempotency_keys_purge_delete", "DELETE"),
        ("idempotency_keys_purge_select", "SELECT"),
        ("idempotency_keys_select", "SELECT"),
        ("idempotency_keys_update", "UPDATE"),
    ]


def test_idempotency_keys_are_seen_per_organization_and_mode(connection: psycopg.Connection) -> None:
    with connection.transaction():
        unit(connection, A, "test")
        connection.execute(INSERT_KEY, (A, "test", "k", "1 day"))
        assert keys(connection) == [(A, "k")]
    for organization, mode in ((A, "live"), (B, "test"), (None, ""), (A, "")):
        with connection.transaction():
            unit(connection, organization, mode)
            assert keys(connection) == []
            assert connection.execute("UPDATE probe.idempotency_keys SET response_status = 200").rowcount == 0
            assert connection.execute("DELETE FROM probe.idempotency_keys").rowcount == 0
    refused = pytest.raises(psycopg.errors.InsufficientPrivilege, match="row-level security")
    with connection.transaction():
        unit(connection, A, "test")
        with refused, connection.transaction():
            connection.execute(INSERT_KEY, (B, "test", "theirs", "1 day"))
        with refused, connection.transaction():
            connection.execute(INSERT_KEY, (A, "live", "other mode", "1 day"))
        with refused, connection.transaction():
            connection.execute("UPDATE probe.idempotency_keys SET organization_id = %s", (B,))


def test_the_purge_role_sees_and_deletes_expired_keys_and_nothing_else(
    connection: psycopg.Connection,
) -> None:
    expiring(connection)
    with connection.transaction():
        unit(connection, role="probe_purge")
        assert keys(connection) == [(A, "a-expired"), (B, "b-expired")]
        with pytest.raises(psycopg.errors.InsufficientPrivilege), connection.transaction():
            connection.execute("UPDATE probe.idempotency_keys SET response_status = 200")
        with pytest.raises(psycopg.errors.InsufficientPrivilege), connection.transaction():
            connection.execute(INSERT_KEY, (A, "test", "purger's own", "-1 day"))
        for table in ("outbox", "jobs", "audit_events", "inbox"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege), connection.transaction():
                connection.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier("probe", table)))
        assert connection.execute("DELETE FROM probe.idempotency_keys").rowcount == 2
    with connection.transaction():
        unit(connection, B, "test")
        assert keys(connection) == [(B, "b-live")]


def test_the_purge_role_never_sees_live_keys_whatever_it_sets(connection: psycopg.Connection) -> None:
    expiring(connection)
    with connection.transaction():
        unit(connection, B, "test", role="probe_purge")
        connection.execute("SELECT set_config('ledger.company_id', %s, true)", (str(B),))
        assert keys(connection) == [(A, "a-expired"), (B, "b-expired")]
        assert connection.execute("DELETE FROM probe.idempotency_keys WHERE key = 'b-live'").rowcount == 0


def test_the_purge_role_of_one_schema_reaches_no_other(connection: psycopg.Connection) -> None:
    with connection.transaction():
        unit(connection, role="other_purge")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            keys(connection)


@pytest.mark.parametrize("local", [True, False])
def test_the_purge_setting_opens_nothing_to_the_rw_role(
    connection: psycopg.Connection, *, local: bool
) -> None:
    expiring(connection)
    with connection.transaction():
        connection.execute("SELECT set_config('ledger.purge', 'on', %s)", (local,))
        unit(connection)
        assert keys(connection) == []
        assert connection.execute("DELETE FROM probe.idempotency_keys").rowcount == 0
        unit(connection, A, "test")
        assert keys(connection) == [(A, "a-expired")]


def test_queue_tables_are_outside_row_level_security(connection: psycopg.Connection) -> None:
    with connection.transaction():
        unit(connection)
        connection.execute(OUTBOX, (None, 1))
        connection.execute(INBOX, ("ready",))
        connection.execute(JOB, ("ready",))
        connection.execute(SUBSCRIPTION)
        for name in ("outbox", "inbox", "jobs", "subscriptions"):
            table = sql.Identifier("probe", name)
            assert connection.execute(sql.SQL("SELECT count(*) FROM {}").format(table)).fetchone() == (1,)
            assert connection.execute(sql.SQL("DELETE FROM {}").format(table)).rowcount == 1


def test_defaults_are_the_specs(connection: psycopg.Connection) -> None:
    with connection.transaction():
        unit(connection)
        connection.execute(INBOX_DEFAULTS)
        connection.execute(JOB, ("ready",))
        inbox = connection.execute(
            "SELECT status, attempts, next_attempt_at <= now() FROM probe.inbox"
        ).fetchone()
        jobs = connection.execute("SELECT attempts, payload FROM probe.jobs").fetchone()
    assert inbox == ("ready", 0, True)
    assert jobs == (0, {})


def test_payloads_above_64_kib_are_refused_by_the_table(connection: psycopg.Connection) -> None:
    with connection.transaction():
        unit(connection)
        connection.execute(OUTBOX, (None, 65536 - 9))
        assert connection.execute("SELECT octet_length(payload::text) FROM probe.outbox").fetchone() == (
            65536,
        )
        with (
            pytest.raises(psycopg.errors.CheckViolation, match="outbox_payload_check"),
            connection.transaction(),
        ):
            connection.execute(OUTBOX, (None, 65536 - 8))


@pytest.mark.parametrize(
    ("statement", "value", "constraint"),
    [
        (OUTBOX, ("staging", 1), "outbox_mode_check"),
        (INBOX, ("done",), "inbox_status_check"),
        (JOB, ("waiting",), "jobs_status_check"),
    ],
)
def test_the_tables_check_what_the_spec_bounds(
    connection: psycopg.Connection, statement: str, value: tuple[object, ...], constraint: str
) -> None:
    with connection.transaction():
        unit(connection)
        with pytest.raises(psycopg.errors.CheckViolation, match=constraint), connection.transaction():
            connection.execute(statement.encode(), value)


def test_an_idempotency_key_is_at_most_255_characters(connection: psycopg.Connection) -> None:
    with connection.transaction():
        unit(connection, A, "test")
        connection.execute(INSERT_KEY, (A, "test", "k" * 255, "1 day"))
        too_long = pytest.raises(psycopg.errors.CheckViolation, match="idempotency_keys_key_check")
        with too_long, connection.transaction():
            connection.execute(INSERT_KEY, (A, "test", "k" * 256, "1 day"))
