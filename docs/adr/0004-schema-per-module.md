# 0004. One PostgreSQL cluster, a schema and a DML role per module, RLS on tenant and mode

- Status: Proposed
- Date: 2026-10-13
- Spec: `docs/superpowers/specs/2026-10-13-ledger-platform-v2-design.md` §5.3, §6

## Context

v1 gave each service its own database and role, with row-level security on `(organization_id, company_id)`. A
modular monolith needs the same ownership guarantees without a database per module, and must keep each module
extractable and tenants shardable later.

## Decision

- One database; each module, each channel family and the platform own one schema (`identity`, `invoicing`,
  `do_ecf`, `platform`, ...). No cross-schema foreign key, join, view or function.
- Roles: `<schema>_owner` (DDL, used only by `migrate`), `<schema>_rw` (DML); logins `ledger_app` (`NOINHERIT`
  member of every `_rw` except the signer's), `ledger_signer`, `ledger_migrator`; group `ledger_relay` for the
  event fan-out (outbox, inbox and subscriptions tables only). No superuser and no `BYPASSRLS` anywhere.
- Every unit of work starts with `SET LOCAL ROLE <schema>_rw` and the transaction-local settings
  `ledger.organization_id`, `ledger.company_id`, `ledger.mode`. The role is bound when the runtime assembles the
  module, never passed by callers. Tables are schema-qualified; there is no `search_path`. One bounded pool serves
  every module while the database still refuses cross-schema access.
- All per-transaction state is transaction-scoped, so a transaction pooler works; `LISTEN` connections and
  `migrate` (session advisory locks, DDL) connect directly. Schedules use lease rows, not session locks.
- RLS: one policy per command, from one builder, in three scopes (tenant; organization; moded organization);
  settings read as `(SELECT NULLIF(current_setting('ledger.x', true), ''))` because a pooled connection returns
  an empty string, not NULL, once a setting has been used. A generated deny-by-default test per table.
- Every schema starts with `chassis_schema_v1`: outbox, inbox, jobs, subscriptions, idempotency keys and
  append-only audit events, with partial claim indexes and queue-friendly fillfactor and autovacuum settings.
- `organization_id` on every tenant row is the shard key; nothing is unique across organizations except
  identity's global tax-id registry.

## Considered options

- Database per module: rejected (connections and backups multiply, no gain inside one process).
- One schema with table prefixes: rejected (ownership not enforced by the database; harder extraction).
- One connection pool per module role: rejected (N pools per process); `SET LOCAL ROLE` chosen.

## Consequences

- Positive: database-enforced ownership; extraction by logical replication of one schema; bounded connections;
  pooler-compatible.
- Negative: a chassis bug could set the wrong role; mitigated because only the runtime-bound unit of work sets it.
