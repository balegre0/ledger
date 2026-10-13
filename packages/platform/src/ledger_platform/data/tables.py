"""The chassis's tables as SQLAlchemy Core tables, one set per schema (the DDL is ``chassis_schema``'s).

For the engines that read and write them and for the test that keeps them equal to what the migration built.
"""

from dataclasses import dataclass
from functools import cache
from typing import final
lazy from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    SmallInteger,
    Table,
    Text,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
lazy from sqlalchemy.schema import SchemaItem

from ledger_platform.data.roles import PLATFORM, SchemaRoles
lazy from ledger_platform.data.database import AnyTable


def _moment(name: str, *, nullable: bool = True) -> Column[datetime]:
    return Column(name, DateTime(timezone=True), nullable=nullable)


def _tenant() -> tuple[SchemaItem, ...]:
    return (Column("organization_id", Uuid()), Column("company_id", Uuid()), Column("mode", Text))


def _envelope() -> tuple[SchemaItem, ...]:
    return (
        Column("type", Text, nullable=False),
        Column("version", SmallInteger, nullable=False),
        Column("source", Text, nullable=False),
        *_tenant(),
        Column("subject", Uuid()),
        Column("aggregate_version", BigInteger, nullable=False, server_default=text("0")),
        _moment("occurred_at", nullable=False),
        Column("traceparent", Text),
        Column("payload", JSONB, nullable=False),
    )


@final
@dataclass(frozen=True, slots=True)
class ChassisTables:
    """One schema's chassis tables."""

    outbox: AnyTable
    inbox: AnyTable
    jobs: AnyTable
    subscriptions: AnyTable
    idempotency_keys: AnyTable
    audit_events: AnyTable


@cache
def chassis_tables(schema: str) -> ChassisTables:
    """The chassis's tables in ``schema`` (built once per schema)."""
    metadata = MetaData(schema=SchemaRoles(schema=schema).schema)
    outbox = Table(
        "outbox",
        metadata,
        Column("id", Uuid(), primary_key=True),
        *_envelope(),
        Column("attempts", Integer, nullable=False, server_default=text("0")),
        _moment("unrouted_since"),
        Index("outbox_due", "id", postgresql_where=text("unrouted_since IS NULL")),
    )
    inbox = Table(
        "inbox",
        metadata,
        Column("consumer", Text, primary_key=True),
        Column("event_id", Uuid(), primary_key=True),
        *_envelope(),
        Column("status", Text, nullable=False, server_default=text("'ready'::text")),
        Column("attempts", Integer, nullable=False, server_default=text("0")),
        Column("next_attempt_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
        _moment("lease_until"),
        Column("lease_token", Uuid()),
        Column("last_error_code", Text),
        Index("inbox_due", "consumer", "next_attempt_at", postgresql_where=text("status = 'ready'")),
        Index("inbox_expired", "consumer", "lease_until", postgresql_where=text("status = 'running'")),
        Index(
            "inbox_head",
            "consumer",
            "subject",
            "aggregate_version",
            "event_id",
            postgresql_where=text("status <> 'dead'"),
        ),
    )
    jobs = Table(
        "jobs",
        metadata,
        Column("kind", Text, primary_key=True),
        Column("key", Text, primary_key=True),
        *_tenant(),
        Column("status", Text, nullable=False),
        _moment("run_at", nullable=False),
        _moment("lease_until"),
        Column("lease_token", Uuid()),
        Column("attempts", Integer, nullable=False, server_default=text("0")),
        Column("max_attempts", Integer, nullable=False),
        _moment("deadline_at"),
        Column("parked_reason", Text),
        Column("payload", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
        Column("last_error_code", Text),
        Column("traceparent", Text),
        _moment("created_at", nullable=False),
        _moment("updated_at", nullable=False),
        Index("jobs_due", "kind", "run_at", postgresql_where=text("status = 'ready'")),
        Index("jobs_expired", "kind", "lease_until", postgresql_where=text("status = 'running'")),
        Index(
            "jobs_parked", "kind", "company_id", "parked_reason", postgresql_where=text("status = 'parked'")
        ),
    )
    subscriptions = Table(
        "subscriptions",
        metadata,
        Column("consumer", Text, primary_key=True),
        Column("event_type", Text, primary_key=True),
        Column("active", Boolean, nullable=False),
        _moment("updated_at", nullable=False),
    )
    idempotency_keys = Table(
        "idempotency_keys",
        metadata,
        Column("organization_id", Uuid(), primary_key=True),
        Column("mode", Text, primary_key=True),
        Column("principal", Text, primary_key=True),
        Column("route", Text, primary_key=True),
        Column("key", Text, primary_key=True),
        Column("request_hash", LargeBinary, nullable=False),
        Column("response_status", SmallInteger),
        Column("response_body", LargeBinary),
        Column("response_headers", JSONB),
        _moment("created_at", nullable=False),
        _moment("expires_at", nullable=False),
        Index("idempotency_expiry", "expires_at"),
    )
    audit_events = Table(
        "audit_events",
        metadata,
        Column("id", Uuid(), primary_key=True),
        _moment("occurred_at", nullable=False),
        *_tenant(),
        Column("actor", Text, nullable=False),
        Column("call_id", Text, nullable=False),
        Column("action", Text, nullable=False),
        Column("subject_type", Text, nullable=False),
        Column("subject_id", Text, nullable=False),
        Column("before_hash", LargeBinary),
        Column("after_hash", LargeBinary),
        Column("detail", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
    return ChassisTables(outbox, inbox, jobs, subscriptions, idempotency_keys, audit_events)


@final
@dataclass(frozen=True, slots=True)
class PlatformTables:
    """The platform schema's own tables."""

    routing: AnyTable
    schedule_leases: AnyTable


@cache
def platform_tables() -> PlatformTables:
    """``platform.routing`` (one row: a unique index on a constant) and ``platform.schedule_leases``."""
    metadata = MetaData(schema=PLATFORM)
    routing = Table(
        "routing",
        metadata,
        Column("version", BigInteger, nullable=False),
        Index("routing_one_row", text("(true)"), unique=True),
    )
    leases = Table(
        "schedule_leases",
        metadata,
        Column("name", Text, primary_key=True),
        Column("holder", Text),
        _moment("leased_until"),
        _moment("next_run_at", nullable=False),
    )
    return PlatformTables(routing, leases)
