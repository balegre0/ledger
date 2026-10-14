# ledger-app-worker

`ledger-worker`: the outbox fan-out, every consumer, job and schedule (spec §3.3). Holds no business code.
`main` calls `ledger_platform.runtime.serve("worker")`: its HTTP port serves `/livez`, `/readyz` and `/metrics`
only (8101 on a host). Needs `LEDGER_DATABASE_DIRECT_URL` (its LISTEN connection) and a pool sized for its jobs
(`LEDGER_DATABASE_POOL_SIZE`).
