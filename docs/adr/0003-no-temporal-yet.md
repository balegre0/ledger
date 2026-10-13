# 0003. Durable processes as persisted state machines on a PostgreSQL job queue; no Temporal yet

- Status: Proposed
- Date: 2026-10-13
- Spec: `docs/superpowers/specs/2026-10-13-ledger-platform-v2-design.md` §3.6, §8.3, §8.6.4

## Context

v1 ran e-CF emission as Temporal workflows. Temporal works on CPython 3.15 (verified while planning v1's
milestone 4) but adds a server cluster, a determinism discipline (sandbox, worker versioning, replay tests) and a
second place where a document's state lives. Authorities' lifecycles are best modelled as explicit per-channel
state machines persisted with the document; what is generic is queueing, retries, timers and locks.

## Decision

Each module schema has a `jobs` table (primary key `kind, key`; `run_at`, lease, attempts, deadline, a payload of
ids). Enqueueing an existing key is ignored while the job is active and resets it when done or dead. A claim sets
a lease and a fencing token and counts the attempt; every record checks the token. Handlers return `Done`,
`RunAgain(at)` or `Park(reason)` (woken by `wake` or `wake_all(kind, company, reason)`), or raise; retryable
errors back off; invalid ones or the deadline make the job `dead`. Jobs live outside RLS and hold ids only;
handlers open scoped units. Concurrency is per kind per process; per-company fairness is deferred. Fiscal
documents are explicit channel state machines persisted in PostgreSQL and advanced one step per run; no
transaction spans a network call. Schedules only enqueue jobs and run under a lease row in
`platform.schedule_leases` (safe behind a transaction pooler).

## Revisit triggers

Adopt a workflow engine when any of these holds: a process needs more than five cross-module steps with
compensation; more than three hand-written state machines duplicate timer or retry logic that the fiscal kit
cannot absorb; job volume needs more than one PostgreSQL primary for queues alone.

## Consequences

- Positive: one source of truth per document; no workflow server; jobs share the inbox's claim engine.
- Negative: timers and polling are our code (kept small in `platform.jobs` and `fiscal-kit.submission`).
