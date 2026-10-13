"""``chassis_schema_v1`` (§6.4): the tables every module, channel and the platform keep in their own schema.

Called by each schema's first revision, as the schema's owner. Versioned and immutable: a change ships
``chassis_schema_v2`` and every schema adds a revision that calls it (audit D20). The DDL is the spec's,
statement for statement; ``{s}`` is the schema, checked before it is substituted.
"""

from abc import ABCMeta, abstractmethod
from typing import Final, Protocol

from ledger_platform.data.rls import RlsScope, policy
from ledger_platform.data.roles import SchemaRoles

_QUEUE: Final = (
    "autovacuum_vacuum_scale_factor = 0.0, autovacuum_vacuum_threshold = 1000,"
    " autovacuum_vacuum_cost_delay = 0"
)
"""Queue tables churn: vacuum them after a fixed number of dead rows, never throttled."""
_V1: Final = (
    f"""CREATE TABLE {{s}}.outbox (
  id uuid PRIMARY KEY,
  type text NOT NULL, version smallint NOT NULL, source text NOT NULL,
  organization_id uuid, company_id uuid, mode text CHECK (mode IN ('test','live')),
  subject uuid, aggregate_version bigint,
  occurred_at timestamptz NOT NULL, traceparent text,
  payload jsonb NOT NULL CHECK (octet_length(payload::text) <= 65536),
  attempts int NOT NULL DEFAULT 0,
  unrouted_since timestamptz
) WITH (fillfactor = 90, {_QUEUE})""",
    "CREATE INDEX outbox_due ON {s}.outbox (id) WHERE unrouted_since IS NULL",
    f"""CREATE TABLE {{s}}.inbox (
  consumer text NOT NULL, event_id uuid NOT NULL,
  type text NOT NULL, version smallint NOT NULL, source text NOT NULL,
  organization_id uuid, company_id uuid, mode text,
  subject uuid, aggregate_version bigint, occurred_at timestamptz NOT NULL, traceparent text,
  payload jsonb NOT NULL,
  status text NOT NULL DEFAULT 'ready' CHECK (status IN ('ready','running','dead')),
  attempts int NOT NULL DEFAULT 0, next_attempt_at timestamptz NOT NULL DEFAULT now(),
  lease_until timestamptz, lease_token uuid, last_error_code text,
  PRIMARY KEY (consumer, event_id)
) WITH (fillfactor = 80, {_QUEUE})""",
    "CREATE INDEX inbox_due ON {s}.inbox (consumer, next_attempt_at) WHERE status = 'ready'",
    "CREATE INDEX inbox_expired ON {s}.inbox (consumer, lease_until) WHERE status = 'running'",
    "CREATE INDEX inbox_head ON {s}.inbox (consumer, subject, event_id) WHERE status <> 'dead'",
    f"""CREATE TABLE {{s}}.jobs (
  kind text NOT NULL, key text NOT NULL,
  organization_id uuid, company_id uuid, mode text,
  status text NOT NULL CHECK (status IN ('ready','running','parked','done','dead')),
  run_at timestamptz NOT NULL, lease_until timestamptz, lease_token uuid,
  attempts int NOT NULL DEFAULT 0, max_attempts int NOT NULL, deadline_at timestamptz,
  parked_reason text, payload jsonb NOT NULL DEFAULT '{{}}', last_error_code text, traceparent text,
  created_at timestamptz NOT NULL, updated_at timestamptz NOT NULL,
  PRIMARY KEY (kind, key)
) WITH (fillfactor = 80, {_QUEUE})""",
    "CREATE INDEX jobs_due ON {s}.jobs (kind, run_at) WHERE status = 'ready'",
    "CREATE INDEX jobs_expired ON {s}.jobs (kind, lease_until) WHERE status = 'running'",
    "CREATE INDEX jobs_parked ON {s}.jobs (kind, company_id, parked_reason) WHERE status = 'parked'",
    """CREATE TABLE {s}.subscriptions (
  consumer text NOT NULL, event_type text NOT NULL, active boolean NOT NULL,
  updated_at timestamptz NOT NULL, PRIMARY KEY (consumer, event_type))""",
    """CREATE TABLE {s}.idempotency_keys (
  organization_id uuid NOT NULL, mode text NOT NULL, principal text NOT NULL,
  route text NOT NULL,
  key text NOT NULL CHECK (length(key) <= 255),
  request_hash bytea NOT NULL, response_status smallint, response_body bytea, response_headers jsonb,
  created_at timestamptz NOT NULL, expires_at timestamptz NOT NULL,
  PRIMARY KEY (organization_id, mode, principal, route, key))""",
    "CREATE INDEX idempotency_expiry ON {s}.idempotency_keys (expires_at)",
    """CREATE TABLE {s}.audit_events (
  id uuid PRIMARY KEY, occurred_at timestamptz NOT NULL,
  organization_id uuid, company_id uuid, mode text,
  actor text NOT NULL,
  call_id text NOT NULL,
  action text NOT NULL, subject_type text NOT NULL, subject_id text NOT NULL,
  before_hash bytea, after_hash bytea,
  detail jsonb NOT NULL DEFAULT '{}')""",
    "REVOKE UPDATE, DELETE, TRUNCATE ON {s}.audit_events FROM {s}_rw",
)
"""§6.4's DDL. ``{s}`` is replaced with ``str.replace`` (never ``format``: the DDL holds ``'{}'``); the
doubled braces above are the f-strings' own escapes of the jsonb default and the schema placeholder."""


class Executes(Protocol, metaclass=ABCMeta):
    """What the functions here need of Alembic's ``op`` (the module's proxy) or an ``Operations``."""

    @abstractmethod
    def execute(self, sqltext: str) -> None:
        """Run one statement in the migration's transaction."""


def chassis_schema_v1(op: Executes, schema: str) -> None:
    """Create the chassis's tables in ``schema``, then put ``idempotency_keys`` under its RLS (Decision 6)."""
    name = SchemaRoles(schema=schema).schema
    *tables, append_only = _V1
    for statement in tables:
        op.execute(statement.replace("{s}", name))
    for statement in policy(f"{name}.idempotency_keys", RlsScope.MODED_ORGANIZATION, purge_expired=True):
        op.execute(statement)
    op.execute(append_only.replace("{s}", name))


def platform_schema_v1(op: Executes) -> None:
    """The platform's own tables (§6.4): the routing version (one row) and the schedules' lease rows."""
    op.execute("CREATE TABLE platform.routing (version bigint NOT NULL)")
    op.execute("CREATE UNIQUE INDEX routing_one_row ON platform.routing ((true))")
    op.execute("INSERT INTO platform.routing (version) VALUES (0)")
    op.execute(
        """CREATE TABLE platform.schedule_leases (
  name text PRIMARY KEY, holder text, leased_until timestamptz, next_run_at timestamptz NOT NULL)"""
    )
