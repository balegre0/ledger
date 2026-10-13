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
rows (lease, fencing token, attempts counted on claim), run the handler and delete the row in the handler's own
transaction; retries back off, then the row is `dead` and kept for replay. `LISTEN/NOTIFY` (coalesced per
process, on a direct connection) wakes workers after commit; a one-second poll is the safety net.

Subscriptions are declared in manifests and written by `ledger-migrate`, before each rollout, into each consuming
schema's `subscriptions` table; `migrate` then bumps `platform.routing.version`, and the fan-out reloads its
cached routing table when the version changes. An event with no subscriber is never deleted: it is marked
unrouted and alerted. Projections use version-monotonic upserts; a consumer that needs order opts into a
head-of-line claim per (consumer, subject). The `EventBus` port remains so an extracted module can use a broker
adapter.

## Considered options

- Keep NATS JetStream: rejected for now (an extra system with subtle consumer-configuration rules).
- In-process listeners without durable per-consumer rows: rejected (no durability across processes and restarts).
- PostgreSQL outbox, fan-out and inbox: chosen.

## Consequences

- Positive: exactly-once effects in one database; no broker; dead letters are rows an operator can query and replay.
- Negative: queue tables need vacuum tuning; throughput is bounded by one primary (far above projected load).
- Revisit when a module is extracted or an external consumer needs a stream: add a broker adapter behind `EventBus`.
