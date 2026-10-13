# 0002. Events through a PostgreSQL outbox, fan-out and inbox instead of NATS

- Status: Proposed
- Date: 2026-10-13
- Spec: `docs/superpowers/specs/2026-10-13-ledger-platform-v2-design.md` §3.5

## Context

v1 used NATS JetStream for integration events, with streams and durable consumers managed by the chassis (about
1,000 source lines and 2,000 test lines). With modules in one process (ADR 0001) a broker is a second stateful
system to operate, back up and monitor, while every event already passes through a transactional outbox in
PostgreSQL.

## Decision

Events are written to the producer's `outbox` in the transaction of the state change. A worker fan-out claims
outbox rows with `FOR UPDATE SKIP LOCKED`, inserts one row per subscribed consumer into that consumer's `inbox`
(`ON CONFLICT DO NOTHING`) and deletes the outbox rows, in one transaction. Consumer runners claim due inbox
rows, run the handler and mark the row handled in the consumer's own transaction; retries back off, then the row
is `dead`. `LISTEN/NOTIFY` wakes workers after commit; a one-second poll is the safety net. Subscriptions are
static, declared in manifests. The `EventBus` port remains so an extracted module can use a broker adapter.

## Considered options

- Keep NATS JetStream: rejected for now (an extra system with subtle consumer-configuration rules).
- In-process listeners without durable per-consumer rows: rejected (no durability across processes and restarts).
- PostgreSQL outbox, fan-out and inbox: chosen.

## Consequences

- Positive: exactly-once effects in one database; no broker; dead letters are rows an operator can query and replay.
- Negative: queue tables need vacuum tuning; throughput is bounded by one primary (far above projected load).
- Revisit when a module is extracted or an external consumer needs a stream: add a broker adapter behind `EventBus`.
