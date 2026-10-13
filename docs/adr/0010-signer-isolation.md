# 0010. Signer isolation and signing backends

- Status: Proposed
- Date: 2026-10-13
- Spec: `docs/superpowers/specs/2026-10-13-ledger-platform-v2-design.md` §3.7

## Context

A company's signing certificate is its legal identity before DGII. v1 isolated certificates in a `vault`
service. In a modular monolith every module shares the process; private keys must not. Decreto 587-24 Art. 22
adds a legal constraint: a SaaS PSFE that holds clients' certificates must either sign through the
infrastructure of an INDOTEL-authorized certification entity (under contract) or hold its own INDOTEL
authorization to manage signature-creation data on the signer's behalf.

## Decision

- The `signer` module's custody code runs only in the `signer` app: its own Deployment, PostgreSQL login
  (`ledger_signer`; the signer's business tables are reachable only by it, its outbox and inbox also by the
  event fan-out), KMS and certification-entity credentials, network policy (reachable only from `api`, `worker`
  and `receiver`), its own consumer runner and schedules.
- Two **signing backends** behind one `SigningBackend` port, chosen per certificate: `local` (key held by us,
  per-certificate AES-256-GCM data key wrapped by the cloud KMS; requires our INDOTEL authorization) and `remote`
  (signing delegated to an INDOTEL-authorized certification entity; the key never reaches us). Callers and
  channels see the same `SignXml`/`SignSeed` answers. Which backend we operate is an owner and counsel decision.
- Contract `ledger.signer.v1.SignerService` over gRPC: import and describe certificates, sign XML, sign
  authority seeds; deadlines set by the caller; stable error codes in trailing metadata; certificate uploads
  stream through `api` without being parsed there and carry the client's idempotency key. Rotation by
  compare-and-set is deferred (an import replaces the active certificate).
- Callers authenticate with **per-caller tokens and method allowlists**: `api` may import and describe;
  `worker` and `receiver` may sign. mTLS from a service mesh replaces the tokens later.
- Every signature is audited before it is returned. `api`'s readiness does not depend on the signer.
- import-linter forbids every other app from importing `ledger_signer.adapters.custody`.

## Consequences

- Positive: a compromised API process exposes no private keys; the legal model can change without touching
  channels; the signer is the template for later extractions.
- Negative: one gRPC hop per signature (far under the 50 ms p95 target for the local backend; the remote
  backend's latency is set by the entity) and one more deployable.
