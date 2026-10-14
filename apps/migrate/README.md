# ledger-app-migrate

`ledger-migrate`: a one-shot job before each rollout (spec §3.3, §6.5). Brings every installed schema to head
(platform first, then the modules in dependency order) as `ledger_migrator` over `LEDGER_DATABASE_DIRECT_URL`,
grants the relay, then writes every schema's subscriptions and bumps the routing version in one transaction.
A failure exits non-zero and the rollout stops.
