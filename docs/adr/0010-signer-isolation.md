# 0010. Signer isolation: private keys only in a separate process

- Status: Proposed
- Date: 2026-10-13
- Spec: `docs/superpowers/specs/2026-10-13-ledger-platform-v2-design.md` §3.7

## Context

A company's signing certificate is its legal identity before DGII. v1 isolated certificates in a `vault`
service. In a modular monolith every module shares the process; private keys must not.

## Decision

- The `signer` module's custody code runs only in the `signer` app: its own Deployment, PostgreSQL login
  (`ledger_signer`, member of `signer_rw` only), KMS credentials and network policy (reachable only from `api`,
  `worker` and `receiver`; reaching only PostgreSQL and KMS).
- Contract `ledger.signer.v1.SignerService` over gRPC: import, rotate and describe certificates, sign XML, sign
  authority seeds; deadlines set by the caller; stable error codes in trailing metadata.
- Envelope encryption: a per-certificate AES-256-GCM data key wrapped by the cloud KMS; every signature is
  audited before it is returned; certificate uploads stream through `api` without being parsed there.
- import-linter forbids every other app from importing `ledger_signer.adapters.custody`. Until the service mesh
  provides mTLS, callers authenticate with a rotated shared token.

## Consequences

- Positive: a compromised API process does not expose private keys; the signer is the template for later extractions.
- Negative: one gRPC hop per signature (measured far under the 50 ms p95 target) and one more deployable.
