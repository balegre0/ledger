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

## Accepted risks and follow-ups

Recorded at the review of the runtime's signer role (v2-M2b Task 3); each names who closes it.

1. **The signer process imports every installed entry point.** The `signer` app loads every module, regime and
   channel entry point of the one image before keeping only the modules isolated in its role (the others are
   not assembled and their settings are not read, but their code is imported). Accepted for the one-image
   deployment. Follow-up: filter entry points by distribution metadata before importing them.
2. **`platform_rw` gives the signer login the whole platform schema.** `ledger_signer` needs
   `platform.schedule_leases` (and `SELECT` on `platform.alembic_version` for readiness) but is a member of
   the whole `platform_rw`. Follow-up: a narrow group role holding exactly those two grants.
3. **An isolated module installed while the signer is absent or not ready.** The worker keeps fanning events
   into the signer's inbox, where they pile up without an unrouted flag (they are routed; nobody consumes
   them). Follow-up (v2-M2b Task 14, alerts in v2-M6): a runbook and an alert on the age of the oldest ready
   row of the signer's inbox, and on the signer's readiness.
4. **No client readiness on the signer.** The client side of the signer module (in `api`, `worker`,
   `receiver`) must hand no readiness check that calls the signer: their readiness never depends on it
   (spec §3.7). v2-M2b Tasks 12 and 13 test it.
5. **The migrator can read the sealed rows.** `ledger_migrator` may `SET ROLE signer_owner`, which owns
   `signer.certificates` and `signer.custody` and can disable their row security and read every sealed key
   and credential (recorded at the review of v2-M2b Task 7). The protection is the envelope: sealed material
   is useless without the KMS, which only the signer's workload identity may call (port contract
   `KeyManagement`). Follow-up (v2-M6): run migrations under a migrator that cannot assume the signer's owner
   outside a rollout, and alert on any session of `signer_owner` that is not a migration.
6. **An import's request hash holds an unsalted digest of the credential.** The idempotency fingerprint of a
   remote import (plan Decision 9) is SHA-256 over the request, the SHA-256 of the entity's credential among
   its parts, kept seven days in `signer.idempotency_keys` (recorded at the review of v2-M2b Task 9). Whoever
   reads that table could test guesses of a low-entropy credential against it; a bundle's digest reveals
   nothing that the bundle (sealed elsewhere) does not. The table is reachable by `ledger_signer` alone (and
   the migrator, item 5), and an entity's credential is expected to be a long random secret. Follow-up (with
   the real entity adapter, v2-M5): key the credential's digest with a secret of the signer (HMAC) if the
   entity's credentials can be guessed.
