# 0004. One PostgreSQL cluster, a schema and a DML role per module, RLS on tenant and mode

- Status: Proposed
- Date: 2026-10-13
- Spec: `docs/superpowers/specs/2026-10-13-ledger-platform-v2-design.md` §5.3, §6

## Context

v1 gave each service its own database and role, with row-level security on `(organization_id, company_id)`. A
modular monolith needs the same ownership guarantees without a database per module, and must keep each module
extractable and tenants shardable later.

## Decision

- One database; each module and each channel family owns one schema (`identity`, `invoicing`, `do_ecf`, ...).
  No cross-schema foreign key, join, view or function.
- Roles: `<schema>_owner` (DDL, used only by `migrate`), `<schema>_rw` (DML); logins `ledger_app` (`NOINHERIT`
  member of every `_rw` except the signer's), `ledger_signer`, `ledger_migrator`; `ledger_relay` for the event
  fan-out. No superuser and no `BYPASSRLS` anywhere.
- Every unit of work starts with `SET LOCAL ROLE <schema>_rw`, `SET LOCAL search_path` and the RLS settings
  `ledger.organization_id`, `ledger.company_id`, `ledger.mode`: one bounded pool serves every module while the
  database still refuses cross-schema access.
- RLS policies per command, produced by one builder, with the mode in the predicate; a generated
  deny-by-default test per table.
- `organization_id` on every tenant row is the shard key; nothing is unique across organizations except
  identity's global tax-id registry.

## Considered options

- Database per module: rejected (connections and backups multiply, no gain inside one process).
- One schema with table prefixes: rejected (ownership not enforced by the database; harder extraction).
- One connection pool per module role: rejected (N pools per process); `SET LOCAL ROLE` chosen.

## Consequences

- Positive: database-enforced ownership; extraction by logical replication of one schema; bounded connections.
- Negative: a chassis bug could set the wrong role; mitigated because only the unit of work sets it.
