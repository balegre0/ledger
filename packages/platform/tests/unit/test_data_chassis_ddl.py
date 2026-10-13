import pytest

from ledger_platform.data.chassis_schema import chassis_schema_v1, platform_schema_v1
from ledger_platform.data.rls import RlsScope, policy
from ledger_platform.data.roles import InvalidSchemaNameError


class Recorder:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, sqltext: str) -> None:
        self.statements.append(sqltext)


def test_every_statement_names_the_schema_and_no_placeholder_is_left() -> None:
    recorder = Recorder()
    chassis_schema_v1(recorder, "probe")
    joined = "\n".join(recorder.statements)
    assert "{s}" not in joined
    assert "{{" not in joined
    assert joined.count("DEFAULT '{}'") == 2
    created = [
        statement.split(" (")[0] for statement in recorder.statements if statement.startswith("CREATE TABLE")
    ]
    assert created == [
        "CREATE TABLE probe.outbox",
        "CREATE TABLE probe.inbox",
        "CREATE TABLE probe.jobs",
        "CREATE TABLE probe.subscriptions",
        "CREATE TABLE probe.idempotency_keys",
        "CREATE TABLE probe.audit_events",
    ]


def test_idempotency_keys_are_put_under_rls_and_audit_rows_made_append_only_last() -> None:
    recorder = Recorder()
    chassis_schema_v1(recorder, "probe")
    rls = policy("probe.idempotency_keys", RlsScope.MODED_ORGANIZATION, purge_expired=True)
    assert tuple(recorder.statements[-1 - len(rls) : -1]) == rls
    assert recorder.statements[-1] == "REVOKE UPDATE, DELETE, TRUNCATE ON probe.audit_events FROM probe_rw"


def test_the_schema_is_checked_before_it_reaches_sql() -> None:
    recorder = Recorder()
    with pytest.raises(InvalidSchemaNameError):
        chassis_schema_v1(recorder, "probe; DROP")
    assert recorder.statements == []


def test_the_platform_keeps_one_routing_row_and_the_schedule_leases() -> None:
    recorder = Recorder()
    platform_schema_v1(recorder)
    assert recorder.statements == [
        "CREATE TABLE platform.routing (version bigint NOT NULL)",
        "CREATE UNIQUE INDEX routing_one_row ON platform.routing ((true))",
        "INSERT INTO platform.routing (version) VALUES (0)",
        """CREATE TABLE platform.schedule_leases (
  name text PRIMARY KEY, holder text, leased_until timestamptz, next_run_at timestamptz NOT NULL)""",
    ]
