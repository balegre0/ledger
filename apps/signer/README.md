# ledger-app-signer

`ledger-signer`: the signer (spec §3.3, §3.7, ADR 0010), the one process that holds certificates and signs. Holds
no business code: `main` calls `ledger_platform.runtime.serve("signer")`, and the platform assembles the modules
isolated in the `signer` role (the `signer` module) and nothing else. Its HTTP port serves `/livez`, `/readyz`
(`database`, `schema:platform`, `schema:signer`, `listen`, `workers`, `signer:grpc`, `signer:kms`) and
`/metrics` only (8102 on a host, 8000 in the image); its gRPC service (`ledger.signer.v1.SignerService`) listens on
`LEDGER_SIGNER_GRPC_PORT` (50051), has no health service and is never published. The only app that may import the
signer's custody (import-linter's `signer-custody` contracts), through the module's assembly.

Needs the login `ledger_signer` in `LEDGER_DATABASE_URL` and `LEDGER_DATABASE_DIRECT_URL` (its LISTEN
connection), every caller's token (`LEDGER_SIGNER_TOKEN_API`, `_WORKER`, `_RECEIVER`: 32+ visible ASCII
characters, distinct), `LEDGER_SIGNER_KMS_KEY_FILE` and a fixed `LEDGER_SIGNER_GRPC_PORT`; each defect fails the
start with its code ([the runbook](../../docs/runbooks/signer/signer-unavailable.md)). It cannot run in staging or
production yet: the only KMS is the local key file, refused outside `local` and `test`.
