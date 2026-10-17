import pytest

from ledger_platform.data.chassis_schema import chassis_schema_v1, chassis_schema_v2, platform_schema_v1
from ledger_platform.data.rls import RlsScope, policy
from ledger_platform.data.roles import InvalidSchemaNameError


class Recorder:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, sqltext: str) -> None:
        self.statements.append(sqltext)


def recorded(schema: str = "probe") -> list[str]:
    recorder = Recorder()
    chassis_schema_v1(recorder, schema)
    return recorder.statements


def test_every_statement_names_the_quoted_schema_and_no_placeholder_is_left() -> None:
    statements = recorded()
    joined = "\n".join(statements)
    assert "{s}" not in joined
    assert "{{" not in joined
    assert joined.count("DEFAULT '{}'") == 2
    created = [statement.split(" (")[0] for statement in statements if statement.startswith("CREATE TABLE")]
    assert created == [
        'CREATE TABLE "probe".outbox',
        'CREATE TABLE "probe".inbox',
        'CREATE TABLE "probe".jobs',
        'CREATE TABLE "probe".subscriptions',
        'CREATE TABLE "probe".idempotency_keys',
        'CREATE TABLE "probe".audit_events',
    ]


def test_row_level_security_and_grants_follow_the_tables() -> None:
    keys = policy("probe.idempotency_keys", RlsScope.MODED_ORGANIZATION, purge_expired=True)
    audit = policy("probe.audit_events", RlsScope.ORGANIZATION, append_only=True)
    tail = [
        *keys,
        'GRANT SELECT, DELETE ON "probe".idempotency_keys TO "probe_purge"',
        *audit,
        'REVOKE UPDATE, DELETE, TRUNCATE ON "probe".audit_events FROM "probe_rw"',
    ]
    assert recorded()[-len(tail) :] == tail


def test_the_schema_is_checked_before_it_reaches_sql() -> None:
    recorder = Recorder()
    with pytest.raises(InvalidSchemaNameError):
        chassis_schema_v1(recorder, "probe; DROP")
    assert recorder.statements == []


def test_the_platform_keeps_one_routing_row_and_the_schedule_leases() -> None:
    recorder = Recorder()
    platform_schema_v1(recorder)
    assert recorder.statements == [
        'CREATE TABLE "platform".routing (version bigint NOT NULL)',
        'CREATE UNIQUE INDEX routing_one_row ON "platform".routing ((true))',
        'INSERT INTO "platform".routing (version) VALUES (0)',
        """CREATE TABLE "platform".schedule_leases (
  name text PRIMARY KEY, holder text, leased_until timestamptz, next_run_at timestamptz NOT NULL)""",
    ]


def test_version_two_adds_the_two_housekeeping_indexes_and_nothing_else() -> None:
    recorder = Recorder()
    chassis_schema_v2(recorder, "probe")
    assert recorder.statements == [
        (
            'CREATE INDEX jobs_deadline ON "probe".jobs (kind, deadline_at)'
            " WHERE status IN ('ready','running') AND deadline_at IS NOT NULL"
        ),
        "CREATE INDEX jobs_done ON \"probe\".jobs (updated_at) WHERE status = 'done'",
    ]
    assert not set(recorder.statements) & set(recorded())  # version 1's statements are never run again


def test_version_two_checks_the_schema_before_it_reaches_sql() -> None:
    recorder = Recorder()
    with pytest.raises(InvalidSchemaNameError):
        chassis_schema_v2(recorder, "probe; DROP")
    assert recorder.statements == []
