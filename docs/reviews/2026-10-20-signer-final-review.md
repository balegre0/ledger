# Signer (v2-M2b) final review, 2026-10-20

Plan Task 15 (`docs/superpowers/plans/2026-10-19-v2-m2b-signer.md`). Whole-module review of `modules/signer` and
of what the signer added to the platform, devtools and deployment (the custody adapters, the gRPC guard, server
and client, `/v1/certificates`, `apps/signer`, the compose service, the runbooks), at `feat/signer` `476ccb9`
(signer Tasks 1-14 merged, with M3b Tasks 1-12 and 16 and `main`). Read against ADR 0010, spec §3.3, §3.7, §6.2,
§10, the plan's Decisions 1-18, its Review Focus and the accepted risks recorded at the reviews of Tasks 1-14.
Input for the production checklist: `docs/reviews/2026-10-20-stack-jury-synthesis.md` (on `main`'s working tree,
uncommitted).

**Verdict: READY TO MERGE, NOT READY FOR PRODUCTION.** No Critical finding. Every cross-cutting claim below
holds in code and is pinned by a named test. Two Important findings are design-level and need the owner (I3,
I4); the documentation drift (I1, I2) is fixed in this branch. Production is blocked on purpose: the only KMS is
the local file, refused outside `local` and `test` (see "Signer readiness for production").

## Findings

### Critical

None.

### Important

- **I1. ADR 0010 and the plan's Decision 12 disagreed on the allowlists.** ADR 0010 said `api` imports and
  describes, `worker` and `receiver` sign (`docs/adr/0010-signer-isolation.md:29-30` before this commit). As
  built (`modules/signer/src/ledger_signer/adapters/grpc/server.py:60-66`), `api` also calls
  `RetireCertificate` and `worker`/`receiver` also call `DescribeCertificates`. **Fixed**: the ADR's decision
  and spec §3.7 list the built methods; the ADR gains an "As built (v2-M2b)" section.
- **I2. Spec §10 stated deferred custody checks as if built.** "Subject must match the company (M3 decision 1);
  validity window; chain to an accepted DR certification authority" read as present. As built, the import
  admits RSA ≥ 2048, a signing certificate of its own key, valid now and for at least the skew, and refuses a
  non-SHA-2 issuer signature as a stopgap (`adapters/custody/facts.py:26-50`, `domain/certificates.py`
  `admit`). **Fixed**: §10 says what is built, what is deferred (subject binding v2-M3b; chain and revocation
  v2-M5) and the stopgap. §3.3 (`signer.expiry_watch` → `signer.expiry_notice` jobs and the signer's own
  purges), §3.7 (two-phase signing, validity at signing, `RetireCertificate`, `_PREVIOUS` tokens, forwarded
  identity, bounds, `isolated_in`, KMS status), §4.2 (`isolated_in`) and §8.4 (fiscal-kit's `SignerPort` and
  `xml` built in M2b; the spike's integration checklist item 2 as built) are amended too.
- **I3. A test-mode credential obtains signatures by the company's real certificate.** A certificate installed
  for both modes serves test (`domain/certificates.py` `serves`; `application/signing.py:118-123` picks the
  active certificate of the request's mode). ADR 0007 calls a test-mode credential safe to hand out, but a
  test-mode API client can have the company's real key sign test e-CF content it chose (DGII's test
  environments take the company's real certificate, so one certificate for both modes is the normal case). No
  production submission follows from it (the channel maps test to `testecf`/`certecf`), but the signed XML is
  the company's signature over that content. **Owner decision, not fixed.** Options: (a) accept and say so in
  ADR 0007; (b) never hand a test-mode client the signed XML (representation only, watermarked); (c) require a
  test-only certificate when DGII allows one (unverified). Cheapest: (a) now, (b) with M3b's representation
  task.
- **I4. The signer is a signing oracle for every company it holds a key of.** It signs whatever document its
  caller sends for the company the caller names (`application/signing.py:116-153`): it does not check that the
  document's issuer (`RNCEmisor`) is the certificate's company, and the certificate's subject is not yet bound
  to the company (deferred, v2-M3b). The jury asked for this check (synthesis §4 item 3): it is confirmed
  absent. With a stolen worker or receiver token (plaintext gRPC until TLS) any company's key signs anything,
  audited but not refused. **Not fixed** (not small: the signer is country-agnostic, so the check is a
  channel-declared hook run in the signer before custody signs). Recorded as ADR 0010 accepted risk 9;
  follow-up F2.

### Minor

- **M1. Imports and signatures share the event loop's default executor.** Each has its own semaphore
  (`application/importing.py:154`, `application/signing.py:90`), but `asyncio.to_thread`
  (`adapters/custody/local.py:65,73`, `remote.py:73`, `signing.py:129`) runs both on the default executor,
  `min(32, CPUs + 4)` threads. A comment said a bundle's key derivation "never starves signing"; on a 2-vCPU task
  (6 threads) 4 signatures and 2 imports fill it. **Fixed (wording)** in `importing.py` and `local.py`.
  Follow-up F6 if a load test shows signing latency under imports.
- **M2. Memory under a flood of calls.** A bundle is buffered whole (2 MiB) before the import semaphore
  (`adapters/grpc/server.py:108-117`), and up to 64 calls run at once; with 5 MiB messages a signing flood can
  hold about 320 MiB of documents against the compose limit of 512 MiB (`deploy/local/compose.yaml:150`).
  Reachable only with a caller's token. Follow-up F6.
- **M3. Import deadlines are two settings that must agree.** The api waits
  `LEDGER_SIGNER_IMPORT_DEADLINE_SECONDS + 2` (`assembly.py:65`) with its own value of the setting; the signer
  waits its own (`adapters/settings.py:59-63`). Different values in the two processes turn a slow stream into a
  503 instead of a 408. Compose sets the signer's (`compose.yaml:143`); the deployment must set both from one
  value.
- **M4. The "client role never loads custody" probe imports the assembly but does not assemble a client role**
  (`tests/unit/test_assembly.py:440-446`). Assembly in a client role reaches `SignerClient`, the router and the
  stand-ins only (read), so it holds today; a probe that assembles `api` would keep it holding.
- **M5. `Idempotency.once`/`stored` still live in `ledger_platform.http.idempotency`**
  (`adapters/postgres/import_keys.py:13`) although the signer's gRPC import uses them. Task 10's note asked
  for a move to `ledger_platform.data`; not done (cross-module, F7). Task 10's other note (the stored answer
  mapped by the gRPC codec) is done: `import_keys.py` stores `codec.import_response` bytes.
- **M6. `apps/receiver` does not exist on this branch.** The `signer-custody` contract's sources
  (`.importlinter:1087-1105`) are generated from the manifests; when M3b Task 13 adds `ledger_app_receiver`,
  check after the merge that `ledger-devtools sync` lists it.

## Cross-cutting claims, verified

| Claim | Where it holds | Pinned by |
|---|---|---|
| Only the signer's role reaches custody | import-linter `signer-custody` (every package but `ledger_signer` and `ledger_app_signer`, indirect imports included), `signer-custody-signer-apps`, `signer-http-holds-no-key`, `signer-http-opens-no-key` (no `cryptography` under `adapters.http`); `assembly.py:42-50` imports serving and the server lazily; grimp sees `lazy` imports (checked: `assembly` → `serving` → `custody.kms_local` is a chain it finds) | `tests/unit/test_assembly.py::test_a_client_role_never_loads_custody` and its counter-probe; devtools planted-import tests |
| `ledger_app` cannot read the signer's tables | `ledger_signer` alone is a member of `signer_rw`/`signer_purge` (`deploy/local/postgres/init.sql:55-58`) | `tests/integration/test_isolation.py` |
| No secret in logs, errors, `repr`, spans, audit rows, events | `Secret`, `SecretStr`, `repr=False` on key material, data keys, sealed bytes, DER, subject; parser errors stripped (`signing.py:43-53`); guard and server log codes, never requests | sentinel tests: `unit/test_server.py::test_secrets_and_documents_never_leave` (logs, errors, 5 spans, audit, events), `integration/test_service.py::test_secrets_never_leave` (rendered stdout of a real app, audit/outbox/jobs/certificates rows, sealed bytes), `unit/test_http_router.py::test_a_password_or_a_credential_never_reaches_a_log_or_a_problem` and `…rendered_log_of_a_defect…`, `integration/test_certificates_api.py::test_the_password_and_the_credential_never_leave_in_a_log_or_a_problem`, `unit/test_client.py:538,794`, `unit/test_backends.py:319`, `unit/test_settings.py:66`, `unit/test_application.py:831,840` |
| Allowlists | `CALLER_METHODS` (`server.py:60-66`): api import/describe/retire; worker and receiver sign/describe. Matches Decision 12; ADR 0010 and spec §3.7 now match it (I1) | `unit/test_server.py` (wrong caller per method), `contract/test_proto.py` (allowlists against the contract) |
| Tokens | ≥ 32 visible ASCII, none shared, digests compared with `hmac.compare_digest` over every stored digest (`packages/platform/src/ledger_platform/grpc/guard.py:126-200`); `_PREVIOUS` server-only; the client holds its own | `packages/platform/tests/unit/test_grpc_guard.py`, `integration/test_grpc_guard_served.py`, `unit/test_settings.py` |
| Forwarded actor and tenant | the signer binds the caller from the token (`current_caller`) and trusts the forwarded organization, company, mode and actor; the call id must match the metadata (`server.py:80-89`); the api asserts role and permission in the company first (`adapters/http/router.py:213-222`) | `integration/test_certificates_api.py::test_who_may_not_act_in_the_company_never_reaches_the_signer`; residual: ADR 0010 risk 8 |
| Audit before answer | unit 2 records the audit and commits before `Signed` is returned (`signing.py:140-153`); a cancelled call after signing answers nothing | `unit/test_application_signing.py::test_a_signature_whose_audit_cannot_be_written_is_never_answered`; `integration/test_service.py` (revoked INSERT → defect, nothing answered) |
| The five port errors | `application/facade.py` `port_error` maps the signer's codes to `SigningCertificateNotValidError`, `SigningClockSkewError`, `SigningCertificateMissingError`, `SignatureRefusedError`, `SignerUnavailableError`; the client uses the same mapping; anything else is a defect | `unit/test_client.py`, `unit/test_application_signing.py` |
| Idempotency | key scoped to organization, request mode, principal and route `grpc ImportCertificate`; request hash over company, mode, modes, backend and the source's SHA-256, never the password; a replay is looked up before custody (`importing.py:183-194`); another request under a used key is `idempotency_key_reused`; a failure keeps nothing | `unit/test_application.py`, `integration/test_units.py`, `integration/test_certificates_api.py` (one key across bundle and remote routes) |
| Test-mode safety | `require_import_modes` (`importing.py:91-101`, also at the edge, `router.py:185-192`); `Retiring.retire` refuses a test-mode retirement of a certificate serving live (`certificates.py:453-454`) | `integration/test_certificates_api.py::test_a_test_mode_request_never_installs_nor_retires_what_serves_live`; see I3 for what it does not cover |
| Import CPU bounds | iteration ceiling 1,000,000 read before opening (`adapters/custody/pkcs12.py:32,183-201`), depth 32, 2 MiB; imports semaphore (default 2); signing semaphore (default 4); `max_concurrent_rpcs` 64 (`grpc/server.py:436`); import stream deadline (`server.py:147-154`) | `unit/test_pkcs12.py`, `unit/test_server.py` (held-open stream) |
| Start checks | tokens → local KMS (environment first, key file) → backends (`remote` needs an entity) → port (`0` only in `local`/`test`) (`adapters/serving.py:9-17,115-170`, `assembly.py:77-101`) | `unit/test_assembly.py`, `unit/test_serving.py`, `unit/test_kms_local.py` |
| KMS status | `kms: Literal["local-file"]` (`adapters/settings.py:76`); refused outside `local`/`test` (`kms_local.py:39-49`); no cloud adapter | `unit/test_kms_local.py`; production blocked (checklist) |
| Envelope | AES-256-GCM, random 96-bit nonce per seal, AAD = purpose, organization, company, certificate and material kind (`custody/envelope.py:134-189`); the KMS binds the same context; a retired certificate's sealed material is deleted in the retiring unit (`adapters/postgres/repositories.py:300-310`) | `unit/test_envelope.py::test_nonces_never_repeat`, `integration/test_repositories.py`, `integration/test_tables.py` |
| SHA-2 stopgap | `facts.py:26-50`, recorded in the module, the README and now spec §10 | `unit/test_facts.py` |

## Accepted risks (all recorded in ADR 0010, "Accepted risks and follow-ups")

1. The signer process imports every installed entry point (Task 3). ADR item 1.
2. `platform_rw` gives `ledger_signer` the whole platform schema (Task 3). ADR item 2.
3. An isolated module installed while the signer is absent: its inbox fills (Task 3). ADR item 3; runbook
   `docs/runbooks/signer/signer-unavailable.md`.
4. No client readiness on the signer (Task 3). ADR item 4; holds (tested in Tasks 12-14).
5. `ledger_migrator` may `SET ROLE signer_owner` and read sealed rows (Tasks 1, 7). ADR item 5.
6. An import's request hash holds an unsalted SHA-256 of the entity's credential (Task 9). ADR item 6.
7. A bundle's password travels in `Ledger-Certificate-Password`, base64 of its UTF-8 (Task 13). ADR item 7.
8. The signer trusts the forwarded tenant and actor, over plaintext gRPC, until mTLS (Tasks 2, 12). ADR item 8
   (new).
9. No document-to-company check: a caller's token is a signing oracle (this review, I4). ADR item 9 (new).
10. `ledger_relay` (so every `ledger_app` process) may `INSERT` into `signer.inbox` (Task 1). ADR item 10 (new).
11. `UNIMPLEMENTED` before the token check; refusals logged without a rate limit (Task 2). ADR item 11 (new).
12. A retry of an import needs the signer reachable (503 otherwise) and the manage permission still (403 after a
    downgrade); `DELETE` scoping across modes; `updated_at` approximate (Task 13). ADR item 12 (new).
13. The network edge exists in compose only (Task 14). ADR item 13 (new).
14. Residuals named in the code: a key bag inside an encrypted PKCS#12 safe escapes the iteration ceiling;
    decrypted key bytes are Python `bytes` and cannot be zeroed; `SignSeed`'s audience is audited, not bound
    into the signature (Task 6).

## Follow-up tasks (larger fixes, with sizing)

| # | Task | Size | When |
|---|---|---|---|
| F1 | AWS KMS adapter (`KeyManagement`: `GenerateDataKey`/`Decrypt` with `EncryptionContext`, key ARN and version as `key_id`, `ready` = `DescribeKey`; `kms = "aws"` setting; LocalStack integration tests like the S3 store's) | 1 day | before any shared environment |
| F2 | Document-to-company check: a channel-declared hook (`do-ecf-v1`: `RNCEmisor` equals the company's RNC) run in the signer after `dsig.prepare`, plus the certificate subject binding at import (v2-M3b) | 1-2 days | with M3b's channel requirements |
| F3 | TLS on the signer's gRPC port: server cert/key from the secret store, clients pin a private CA; tokens kept | 0.5-1 day | before go-live |
| F4 | Certification-entity adapter (CSC API v2 `credentials/info`, `signatures/signHash`), `LEDGER_SIGNER_ENTITY_*`, HMAC the credential digest if guessable (ADR item 6) | 2-3 days after the entity is chosen | if the owner launches on `remote` |
| F5 | Owner decision on I3 (test-mode signatures by the real certificate) and its implementation | 0.5 day for (a) or (b) | before the first test-mode client |
| F6 | Dedicated thread pools sized to the two semaphores, and memory caps (a smaller `max_message_bytes` for `SignXml`, `max_concurrent_rpcs` from settings) after a load test | 0.5 day | after the load test |
| F7 | Move `Idempotency`, `Attempt`, `StoredResponse`, `Outcome` and their errors to `ledger_platform.data` (HTTP helpers stay); update invoicing, fiscal, signer imports | 0.5 day | any time; mechanical |
| F8 | Path validation (chain to an accepted DR authority, algorithms, OCSP/CRL) replacing the SHA-2 stopgap | 2-3 days | v2-M5 |
| F9 | Decide whether a certificate keeps `updated_at` (row, domain, contract) | 0.25 day | before v2-M6 |
| F10 | Narrow group role for the signer's `platform` grants; migrator not able to assume `signer_owner` outside a rollout; alerts (signer inbox age, readiness, `signer_owner` sessions, `company_deactivated` retirements without the identity event) | 1 day | with the deploy pipeline |
| F11 | A probe that assembles the `api` role and asserts custody stays unloaded (M4) | 0.25 day | any time |

## Signer readiness for production

What blocks go-live, and the cheapest path for each, in the target the jury recommends (AWS us-east-1, ECS
Fargate, OpenTofu; synthesis §2, §9). Nothing below needs a change to the signer's ports or its callers.

| Blocker | Why it blocks | Cheapest path | Size |
|---|---|---|---|
| **AWS KMS adapter** | The signer refuses to start outside `local`/`test` with the only KMS that exists (`kms_local_refused`) | F1: one adapter over `aiobotocore`/`boto3` in a thread, the signer's ECS task role allowed `kms:GenerateDataKey`/`kms:Decrypt` on one symmetric key with an `kms:EncryptionContext:purpose = signer.custody` condition; KMS reached through a VPC interface endpoint (no internet egress). Later, per-company non-exportable asymmetric keys (synthesis §2) | 1 day + IaC |
| **INDOTEL custody decision** | Decreto 587-24 Art. 22: `local` custody needs our own INDOTEL authorization; `remote` needs a contract with an authorized entity and its adapter | Owner + counsel (synthesis §10 decision 3 recommends `remote`). Cheapest legal path: `remote` through an authorized entity (F4, after a per-signature quote); `local` only once authorized. Until decided, only `local` runs, and only where it is lawful (our own RNC's certification) | owner; F4 2-3 days |
| **TLS on the gRPC port** | Tokens and documents cross the network in clear (ADR item 8); the jury prefers TLS now over a mesh later | F3: a private CA made by OpenTofu's `tls` provider, the signer's certificate and key in Secrets Manager, clients given the CA (no ACM Private CA: its monthly fee buys nothing here); `add_secure_port`/`secure_channel` behind a setting, refused off in `staging`/`production` | 0.5-1 day |
| **Network policy and egress** | No deployment exists, so nothing yet admits only the callers or limits the signer's egress (ADR item 13); the jury found no egress control anywhere (synthesis §4 item 7) | Security groups: the signer's admits 50051 from the api, worker and receiver groups only, and its probe port from the load balancer's health checks only (no ALB in front of gRPC: Cloud Map service discovery); private subnets without a NAT route for the signer (PostgreSQL and the KMS endpoint only). `remote` adds one egress to the entity's host: through the Smokescreen proxy the jury recommends for DGII, with the entity on its allowlist | in the cell module |
| **Deploy pipeline** | Nothing builds, signs or deploys the image; no secrets reach a task | OpenTofu cell module (ECS service `signer`, 2 tasks, its own task role and security group, `ledger_signer`'s DB password and the three caller tokens in Secrets Manager, each caller's task given its own token only); GitHub Actions deploying by image digest with cosign and an SBOM; `migrate` as a one-off task before each rollout; ALB health checks on `/livez`, not `/readyz` (synthesis §4 item 4) | 3-5 days (shared with every app) |
| **Edge redaction of the password header** | `Ledger-Certificate-Password` is logged by default by proxies, WAFs and APM agents (ADR item 7) | AWS WAF `redacted_fields` for the header in its logging configuration; no APM agent that records headers; a deployment check that sends a sentinel and greps the logs | 0.5 day |
| **Operations** | Signer outage, inbox backlog and expiring certificates must page someone | The two runbooks exist (`docs/runbooks/signer/`); add the alerts of F10 to the jury's six paging alerts; restore drill covers `signer.custody` (sealed rows are useless without the KMS key: back the key's policy up, never the key) | 1 day |

Not blocking go-live, decided before it: I3 (test-mode signatures), I4/F2 (document-to-company check), the
migrator's reach (ADR item 5).

## Gate

`LEDGER_TEST_JOBS=2 uv run poe check` on `9ff1ed8` (this review's commit, before this section was filled in):
exit 0, seen to finish, on its first run. Coverage 100 % in every package. Tests: platform 1846, DO 2317, signer
650, identity 438, invoicing 325, kernel 620, fiscal-kit 218, devtools 210, testing 126, DO testing 114, fiscal
78, apps 7. Devtools was the slowest package at 8 min, on a shared machine. The compose smoke run (plan Task 15
Step 3) was not repeated: Zitadel is not up here, and Task 14 had already run it. The integration tests
`test_certificates_api.py` and `test_service.py` (import through the api, sign as the worker, one audit row)
cover the same path.
