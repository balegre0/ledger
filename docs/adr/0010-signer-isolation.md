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
- Contract `ledger.signer.v1.SignerService` over gRPC: import, describe and retire certificates, sign XML,
  sign authority seeds; deadlines set by the caller; stable error codes in trailing metadata; certificate
  uploads stream through `api` without being parsed there and carry the client's idempotency key. Rotation by
  compare-and-set is deferred (an import replaces the active certificate).
- Callers authenticate with **per-caller tokens and method allowlists**: `api` may call `ImportCertificate`,
  `DescribeCertificates`, `RetireCertificate` and `SignXml` of an e-CF or an RFCE alone (amended 2026-10-26,
  v2-M3b Task 26: the api numbers and signs an invoice's e-CF within the request, spec §8.5; it never signs a
  seed nor any other document, accepted risks 8 and 9); `worker` and `receiver` may
  call `SignXml`, `SignSeed` and `DescribeCertificates` (a channel reads a certificate's validity through it; no
  method returns a secret).
  mTLS from a service mesh replaces the tokens later.
- Every signature is audited before it is returned. `api`'s readiness does not depend on the signer.
- import-linter forbids every other app from importing `ledger_signer.adapters.custody`.

## As built (v2-M2b)

Plan `docs/superpowers/plans/2026-10-19-v2-m2b-signer.md`; final review
`docs/reviews/2026-10-20-signer-final-review.md`. The status stays Proposed: the owner has not accepted the
production choices it depends on (the KMS, the backend we operate, the INDOTEL custody model).

- **Isolation**: the manifest key `[data] isolated_in = "signer"` makes the signer's role the only one that
  assembles the module's server side; the client roles bind a gRPC client (or, without
  `LEDGER_SIGNER_ADDRESS` and their own token, stand-ins answering `signer_unavailable`). Custody is imported
  lazily by the assembly and only in the signer's role (a subprocess test proves a client role never loads it);
  import-linter's `signer-custody` contracts forbid every other package from importing it.
- **Tokens**: `LEDGER_SIGNER_TOKEN_<CALLER>` (and, during a rotation, `_PREVIOUS`, accepted by the server
  only); at least 32 visible ASCII characters, none shared between callers, kept and compared as SHA-256
  digests with `hmac.compare_digest`; a client process holds its own token only.
- **Two-phase signing**: fiscal-kit's `xml.dsig` prepares the document (hardened parse, C14N, digest) in the
  signer, custody signs `SignedInfo` (locally, or the entity signs its SHA-256), `dsig` completes it. The
  signer decides validity at signing: the certificate must cover `signed_at` to `signed_at + skew` and
  `signed_at` must be within the skew (300 to 900 s) of the signer's clock.
- **Forwarded identity**: the signer trusts the organization, company, mode and actor its caller forwards
  (the api asserts them against identity before calling); it binds only the caller's name itself (from the
  token). See accepted risk 8.
- **CPU and memory bounds**: imports `LEDGER_SIGNER_IMPORT_CONCURRENCY` (default 2, at most 16) at once, in a
  semaphore of their own; signatures `LEDGER_SIGNER_SIGNING_CONCURRENCY` (default 4, at most 64); at most 64
  calls in flight (`max_concurrent_rpcs`, past it `RESOURCE_EXHAUSTED`); messages at most 5 MiB; a bundle at
  most 2 MiB; an import's stream must end within `LEDGER_SIGNER_IMPORT_DEADLINE_SECONDS` (at most 28 s; the
  api waits 2 s more); a PKCS#12 bundle naming more than 1,000,000 key-derivation or MAC iterations is refused
  unopened (residual: a key bag inside an *encrypted* safe shows its count only once decrypted). The CPU work
  runs in the event loop's default executor, shared by both semaphores.
- **Test-mode safety (ADR 0007)**: a test-mode request installs a certificate for test alone
  (`certificate_modes_not_allowed`) and never retires one that serves live (`certificate_serves_live`).
- **KMS**: only the local-file KMS exists, refused outside `local` and `test` (`kms_local_refused`): no
  shared or production environment can start the signer until a cloud KMS adapter honouring the plan's
  `KeyManagement` contract exists. No certification-entity adapter exists either: offering `remote` refuses
  to start (`entity_not_configured`).
- **Deferred import checks**: the certificate's subject is not yet bound to the company (v2-M3b, channel
  requirements), its chain to an accepted Dominican certification authority and its revocation are not
  checked (v2-M5). Meanwhile a certificate its issuer signed with anything but a SHA-2 hash is refused
  (`certificate_not_for_signing`), a stopgap that path validation replaces.

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
7. **A bundle's password travels in a request header.** `POST /v1/certificates` takes the PKCS#12 bundle as
   the raw body, so that the api streams it to the signer unread, and its password in
   `Ledger-Certificate-Password`, as base64 of its UTF-8 (a header is Latin-1 to the server: a password past
   ASCII would otherwise reach the bundle changed). A header is what reverse proxies, WAFs and APM agents log
   by default, and base64 hides nothing (recorded at the review of v2-M2b Task 13). The app never logs it, nor
   any header. Accepted for the streaming it keeps (a multipart body would let Starlette spool the bundle to
   disk). Follow-up (v2-M6, with the ingress and the APM agent): redact `Ledger-Certificate-Password` at every
   proxy, WAF and APM agent in front of `api`, and test the redaction in the deployment's checks.
8. **The signer trusts the tenant and actor its caller forwards, over plaintext gRPC.** Until a mesh adds mTLS
   (spec §16.4), the gRPC port is plaintext and a call is admitted by its token alone. The api asserts the
   caller's role in the company before it calls; the signer cannot check it. A stolen api token imports,
   describes and retires certificates of any company; a stolen worker or receiver token obtains signatures
   for any company's active certificate (each audited with the caller and the reference). No token exports a
   key. A party on the pod network can read tokens and documents in transit (recorded at the reviews of
   v2-M2b Tasks 2 and 12). Follow-up (before go-live, cheaper than the mesh): TLS on the signer's port with a
   private CA and the clients pinning it; the network policy of item 13.
   **Amended 2026-10-26 (v2-M3b Task 26):** the api now signs within `POST /v1/invoices` (spec §8.5), so the
   internet-facing api, its process or its token, also obtains signatures under every company's active
   certificate: of an e-CF or an RFCE alone (`CALLER_DOCUMENTS`, by the document's root element), never of a
   seed (`SignSeed` stays the worker's and the receiver's) nor of any other document (an ACECF, an ANECF, an
   ARECF: refused `grpc_method_not_allowed`). Each is audited with the caller `api`.
9. **The signer signs whatever document its caller sends for the company it names.** It does not check that
   the document's issuer (`RNCEmisor` for DGII) is the company the certificate serves, nor yet that the
   certificate's subject is that company (deferred to v2-M3b's channel requirements). A caller's defect (a
   document of company B sent for company A) is then refused by the authority, not by us; a stolen worker
   or receiver token is a signing oracle for every company (item 8). Follow-up (v2-M3b, with the subject
   binding): a channel-declared check, run in the signer before custody signs, that the document names the
   certificate's company.
   **Amended 2026-10-26 (v2-M3b Task 26):** the api is a caller of `SignXml` too, bounded to e-CF and RFCE
   (item 8). That follow-up (the signer final review's F2) would not limit an api compromise: the api forwards
   the company it names, and an e-CF or RFCE of that company's own RNC passes the check; only the narrowing by
   document and the audit bound it. Owner item 32 of the M3b plan confirms the change or reverts it.
10. **The fan-out may write into the signer's inbox.** `ledger_relay` (a member of `ledger_app`, so of the
    `api`, `worker` and `receiver` processes) holds `INSERT` on `signer.inbox`. A compromised `ledger_app`
    process can forge `identity.company.deactivated` into it and have any company's certificates retired
    (denial of service, no key exposure; recorded at the review of v2-M2b Task 1). Follow-up (v2-M6): an
    alert on certificates retired `company_deactivated` without the matching identity event.
11. **What the guard tells and logs.** A call to a method nobody registered answers `UNIMPLEMENTED` before any
    token is checked, so a party on the pod network learns which methods exist (not who may call them, nor
    anything they return); every refusal is logged (`grpc.caller_refused`) without a rate limit (recorded at
    the review of v2-M2b Task 2). Revisit with the mesh.
12. **The api cannot replay an import before it calls the signer.** The signer keeps the import's key, so a
    retry of an import that succeeded needs the signer reachable (else 503 `signer_unavailable`) and the
    caller's `signer.certificates.manage` still (a caller downgraded since is 403, never answered); such a
    caller confirms the outcome with `GET /v1/certificates` (read permission). A `DELETE` finds the
    certificate among those of the request's mode and retires it for every mode it serves (a live-mode
    request may retire a certificate that also serves test). A certificate's `updated_at` is approximate (its
    retirement, else its installation): no moment is kept when a newer certificate takes one of its modes;
    decide before v2-M6 whether the resource keeps it (recorded at the review of v2-M2b Task 13).
13. **The network edge is compose's alone.** Locally the signer's gRPC port is on an internal network shared
    with its callers and PostgreSQL only, never published; its probe and metrics port is published on
    loopback. No production network policy exists yet (no deployment exists), and nothing enforces the
    signer's egress (recorded at the review of v2-M2b Task 14). Follow-up (deployment task): the signer's
    security group or network policy admits `api`, `worker` and `receiver` only, and its egress reaches only
    PostgreSQL, the KMS and the certification entity.
