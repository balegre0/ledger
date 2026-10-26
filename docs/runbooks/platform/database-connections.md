# Runbook: PostgreSQL connections per app

Every app holds a SQLAlchemy pool of `LEDGER_DATABASE_POOL_SIZE` connections (30 by default) and may open as
many again as overflow while a burst lasts, so an app can hold up to **twice** its pool size. The worker and
the signer refuse to start when their pool is smaller than what they may hold at once (`pool_too_small`, at
start: the error names the jobs' concurrency, their lease renewals, the runners and the headroom).

## The budget

| App | `LEDGER_DATABASE_POOL_SIZE` | Up to (pool + overflow) | Why |
|---|---|---|---|
| api | 30 (default) | 60 | requests |
| worker | 70 | 140 | 28 for the jobs' concurrency (`do_ecf.advance` 16, `do_ecf.deliver` 8, `fiscal.route`, `do_ecf.purge_receiver_credentials` and the maintenance jobs), 31 for lease renewals (those jobs and fiscal's three outside consumers), 9 for its runners, 2 of headroom; each job kind or outside consumer added raises it |
| signer | 30 (default) | 60 | its jobs and gRPC calls |
| receiver (when deployed) | 30 (default) | 60 | DGII's and emitters' calls |
| ledger-migrate | a few, for its run | | |
| zitadel | its own pool | | identity provider |

Per replica: scale the sum by the replicas of each app. PostgreSQL's `max_connections` must exceed the sum,
plus its reserved superuser slots. Locally compose sets `max_connections=356` (`deploy/local/compose.yaml`): api 60, worker 140, signer 60,
receiver 60 at twice their pools, and zitadel's.

## When to act

- **A worker or signer that does not start with `pool_too_small`**: raise its `LEDGER_DATABASE_POOL_SIZE` to
  the number the error gives, and raise `max_connections` (or the pooler's limit) with it.
- **Connections above 60 % of `max_connections`** (`count(*) FROM pg_stat_activity`, sustained): put a
  transaction pooler (PgBouncer, `LEDGER_DATABASE_POOLED=true`) in front of the apps before adding replicas;
  the worker and the signer keep their direct connections (`LEDGER_DATABASE_DIRECT_URL`) for LISTEN and locks.
- **`too many clients already`** in an app's log: the budget above was exceeded; lower overflow by sizing
  pools to their need, or raise `max_connections` after checking the server's memory (each connection costs
  a backend process).
