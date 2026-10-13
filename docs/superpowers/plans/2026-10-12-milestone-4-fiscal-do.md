# Milestone 4: Fiscal Core (fiscal-do) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The `fiscal-do` service (`services/fiscal-do`, package `ledger_fiscal_do`): the Dominican Republic's e-CF from a document a client issues to DGII's final answer. Ten e-CF types checked by rules of their own and written as DGII's schemas say, validated against DGII's own XSDs, signed by vault, archived in object storage under object lock (WORM, ten years), sent to DGII (token, reception, result, track ids, consumer-invoice summaries) by a durable Temporal workflow that survives DGII's outages and missing certificates, and announced as events (`fiscal.document.numbered|accepted|conditionally_accepted|rejected`, `fiscal.sequence.low|expiring`, `fiscal.annulment.requested|decided`). Its public REST API (`/v1/do/documents`, `/v1/do/sequences`, `/v1/do/annulments`, `/v1/do/profile`) is idempotent, permission-checked (`invoice.issue`, `invoice.read`, `company.manage`, `company.read`) and tenant-isolated by row-level security. A fake DGII with teeth (schema checks, signature checks, scripted outcomes and outages) stands in for DGII in tests and locally; every type is emitted through it end to end.

**Architecture:** A shared library first: `libs/dgii` (`ledger_dgii`) holds what DGII publishes, as data: its fifteen XSDs vendored byte for byte with their source and SHA-256, the three corrections without which three of them do not compile, validation (libxml2, hardened parser) and the names of DGII's elements as its schemas declare them; fiscal-do, the fake DGII and Milestone 6's fiscal-receiver share it. The service is hexagonal (§6): `domain` (stdlib + kernel: values, totals, one rules module per type, the document's state machine, number ranges, annulments, the fiscal profile, events) ← `application` (issuing, the emission and annulment steps, ranges, profiles, queries; ports) ← `settings` ← `workflows` (Temporal: `EmitEcf`, `AnnulRange` and their activities) ← `adapters` (XML writers, DGII's HTTP client with token cache and circuit breaker, vault and identity over gRPC, S3 with object lock, PostgreSQL, the outbox, Temporal's client) ← `resources` ← `api` (REST routes, consumers, workers) ← `main`. fiscal-do starts each document's workflow from its own `fiscal.document.numbered` event, so the outbox is the one record of what is to be done.

**Tech Stack:** Python 3.15, lxml 6.1.3, xmlschema 4.3.2 (tests and the fake DGII only), httpx 0.28.1, temporalio 1.34.0 (with nexus-rpc 1.4.0), boto3/botocore 1.43.111 (+ types-boto3-s3 1.43.106), Starlette 1.7.0 and python-multipart 0.0.32 (the fake DGII), granian 2.8.4 (serving; the fake in tests, embedded), SQLAlchemy 2.1 / psycopg 3.3 / Alembic 1.20, PostgreSQL 18.6, NATS 2.12.15, LocalStack 4.14.0 (S3 with object lock, beside vault's KMS), Temporal server 1.9.1 (`temporal server start-dev`), hypothesis, testcontainers 4.15.

**Spec:** `docs/superpowers/specs/2026-10-11-ledger-platform-design.md` (milestone 4 of §16: "XML for all types, XSD validation, signing, DGII auth, send and status against `TesteCF`"; §3 fiscal-do row, §4.3, §5.1, §5.3, §6, §7, §9, §10, §11, §14, §17).

## Global Constraints

- Python `>=3.15`; every package's `pyproject.toml` says `requires-python = ">=3.15"`.
- Code, comments, docs and messages in English; DGII's own words (its element names, its statuses, its messages) stay DGII's.
- Zero `# type: ignore`, `# pyright: ignore`, `# noqa`, `# pragma: no cover`, and no tool directive comment: the `suppressions` gate step fails on any.
- `uv run poe check` passes after every task: ruff format + ruff (`select = ["ALL"]`), basedpyright (`all`, warnings fail), mypy `--strict` per package, import-linter, deptry per package, typos, `buf lint`, pytest with **100 % branch coverage**, warnings as errors.
- Errors build their message inside their class; a message never echoes a document, an amount a client sent, a token or a tax id. DGII's messages travel verbatim to the client who sent the document, never to a log.
- Type-only imports use `lazy from ... import ...`, with one exception: **`temporalio.client` is always imported eagerly** (ruff's `exempt-modules`, Task 11). A lazy import of `temporalio.client` that runs before `temporalio.workflow`'s first import makes temporalio 1.34's own import cycle fail on CPython 3.15 (`AttributeError: cannot access submodule 'nexus'`, verified).
- lxml objects are never named in an annotation (`etree._Element` is private to basedpyright `all`): code keeps them as inferred locals. XML is written with the standard library's `ElementTree` (public, typed) and read only by `ledger_dgii`'s hardened parser.
- DGII's element names are never spelled in fiscal-do's code: writers name elements in lowercase and `ledger_dgii.names` returns the name the document's schema declares (Decision 4). The repository's spell checker reads English; DGII's names live in DGII's files.
- No transaction spans a network call: every emission step reads in one unit, calls vault, the archive or DGII outside it, and records in another, where the row is read again under its lock.
- Dependency floors introduced here, verified on 2026-10-12 to install on CPython 3.15 in a scratch environment (pure Python, `abi3` or `cp315` wheels): `temporalio>=1.34.0` (`cp310-abi3`, uploaded 2026-09-30; pulls `nexus-rpc>=1.4.0`), `xmlschema>=4.3.2` (2026-06-30; pulls `elementpath>=5.1.4`), `starlette>=1.7.0`, `python-multipart>=0.0.32` (2026-06-04), `granian>=2.8.4`; dev: `types-boto3-s3>=1.43.106`. Milestone 3's floors are reused: `lxml>=6.1.3`, `boto3>=1.43.111`, `botocore>=1.43.111`, `signxml>=5.1.0`. `uv.lock` pins the exact versions.
- Docker must be running: tests use PostgreSQL `postgres:18.6`, NATS `nats:2.12.15`, LocalStack `localstack/localstack:4.14.0@sha256:3ebc37595918b8accb852f8048fef2aff047d465167edd655528065b07bc364a` (S3) and Temporal `temporalio/temporal:1.9.1@sha256:ad4c82c97bd12b417d1ea942610dbcd511afb250c4d5ed26c694009533df447e`; the workflow tests download Temporal's test server once (`WorkflowEnvironment.start_time_skipping`).
- Work on branch `milestone-4-fiscal-do`, created from `main` once Milestone 3 (vault) has been merged; one commit per task; never push or rewrite `main`; commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **Milestones 2 and 3 are being executed while this plan is written.** This plan builds on the surface their plans declare and on what has landed (`libs/platform` at commit `5ca3de2`: `Database.transaction(scope)`, `UnitOfWork.scope`, `Outbox`, `Idempotency`, `ledger_platform.routes`, `ledger_platform.authorization`, `Service(settings=, version=, interfaces=Interfaces(http=, grpc=, resources=, checks=), events=Events(streams=, consumers=, workers=))`, `Runtime.resources(Kind)`). From the plans: `ledger_platform.grpc_client` (`Channel`, `call_unary`, `RemoteError` and kinds; M2 Task 8, M3 Task 3), `ledger_platform.identity_client.RemotePermissions` (M2 Task 8), `ledger_platform.migrations` (M3 Task 3), `ledger.identity.v1` and `ledger.vault.v1` (M2 Task 7, M3 Task 1), `ledger_contracts.vault` (M3 Task 1), `ledger_testing.{containers,logs,waiting,issuer,pki}` (M2 Task 2, M3 Task 2). Every task that uses one of these says **Re-check before starting** with the names to confirm; where the landed name or signature differs, the task uses the landed one and says so in its report (a renamed parameter is an edit of this plan's code, never a reason to change the platform, identity or vault back).
- Every line of code in this plan was run as written in a scratch workspace on 2026-10-12 against the landed platform (`5ca3de2`) plus scratch copies of what the Milestone 2 and 3 plans declare (`grpc_client`, `identity_client`, `migrations`, the identity and vault protos): 396 tests, 100 % branch coverage, ruff, basedpyright and mypy clean. The images, the compose services and the CI step (Task 14) were not built in that workspace (no workspace lockfile there); `docker compose config` validated the compose file.

## Review Focus

1. **Every document DGII's schema accepts, and only those**: each of the ten types' rules (`domain/types`) against the vendored XSDs: a hypothesis property generates documents the rules accept and asserts libxml2 *and* xmlschema accept their XML; the golden files are the samples' XML byte for byte; the type-specific edges (consumer invoices from DOP 250,000 name their buyer, credit notes over 30 days carry the late indicator, foreign payments carry ISR and no ITBIS, exports only ITBIS 3, minor expenses no buyer). Tests in Tasks 3 and 4.
2. **Exact money**: totals computed as DGII computes them (half up, to two places, prices to four, taxed bases from ITBIS-included prices), with hypothesis laws (the total is the sum of its parts, a document's total never depends on line order). Tests in Task 3.
3. **Never a number twice, never a document sent twice**: numbering under the range's row lock (five documents issued at once take five numbers), `uq_documents_encf`, idempotency keys on every `POST`; a document whose submission broke mid-flight is never sent again blindly (DGII's track ids are asked first); every step runs again harmlessly (each reads the document under its lock before recording). Tests in Tasks 5, 6, 10 and 12.
4. **Durability**: the workflow sleeps through outages (1 minute up to every 15), polls with backoff, waits for a certificate (a signal from vault's event, or an hour), continues as new before its history grows, and replays deterministically; a defect fails it without retry. Tests in Task 11 (time skipping, replay) and Task 12 (end to end, every type).
5. **Tenancy and archive**: documents, ranges, annulments, profiles and the archive index under `tenant_isolation`; the two watches outside it on purpose, holding ids, states and dates only; the archive refuses deletion and shortened retention (S3 object lock, compliance mode) and never overwrites (`If-None-Match`). Tests in Tasks 7, 9 and 10.
6. **DGII's client**: retries only what cannot change DGII's state, a token per company and environment cached until five minutes before it expires, one retry on a 401, a circuit breaker per environment, timeouts on every call, no token or document in any log or span. Tests in Task 6.

## Decisions

The owner's scope for this milestone is binding; these are the decisions this plan adds and the facts it found, each with its reason and, for research, its source and date.

1. **Sources (research, 2026-10-12).** DGII's page of e-CF documentation, https://dgii.gov.do/cicloContribuyente/facturacion/comprobantesFiscalesElectronicosE-CF/Paginas/documentacionSobreE-CF.aspx, fetched on 2026-10-12 with every document it links: the e-CF format v1.0 (October 2025), DGII's e-CF technical report v1.0 (updated 2026-03-23), *Descripción Técnica Servicios DGII* (2026-01-02), *Descripción Técnica Emisores Electrónicos*, *Firmado de e-CF*, the RFCE, ANECF, ACECF and ARECF formats, the certification process documents and the fifteen XSDs. Every one was fetched; none was unavailable. The XSDs are vendored by Task 1, each pinned to its source URL and SHA-256 (`ledger_dgii.published`).
2. **A library of what DGII publishes, `libs/dgii` (`ledger_dgii`).** The XSDs, their corrections, validation and DGII's element names are data and code three components need: fiscal-do (writes and checks), the fake DGII (checks, independently) and Milestone 6's fiscal-receiver (reads). The files are DGII's byte for byte (BOMs and CRLF included; `.gitattributes` marks them `-text`), renamed only to lose the spaces in DGII's names.
3. **Three of DGII's fifteen XSDs do not compile as published**, and are corrected when loaded, never on disk, each correction pinned to the exact number of times it applies (`CORRECTIONS`; a schema DGII changes fails the pin): e-CF 31 names a type with a leading space (`name=" IndicadorServicioTodoIncluidoType"`), and ACECF (once) and RFCE (four times) use Perl's non-capturing group `(?:`, which XML Schema's regular expressions do not have. Every schema also demands the XML signature (`xs:any`, at least once): a document is validated before signing with an empty `Signature` in its place (`unsigned=True`).
4. **DGII's element names come from DGII's schemas.** Writers name an element in lowercase (`rncemisor`) and `ledger_dgii.names.element(format, name)` returns the name the document's schema declares (`RNCEmisor`, or `RncEmisor` in the ANECF): a name the schema does not declare fails where it is written, not later as a schema violation, and DGII's Spanish names are never spelled again in fiscal-do's code (the repository's spell checker reads English). Two names of one schema that differ only in case (`SubTotalImpuestoAdicional`, `SubtotalImpuestoAdicional`, additional taxes this milestone does not write) are left out of the lookup.
5. **Validation runs on libxml2; xmlschema is the second opinion.** Measured on 2026-10-12 for a 60-line e-CF: lxml's `XMLSchema` p95 0.49 ms, xmlschema 4.3.2 p95 18 ms. fiscal-do validates with lxml (per thread: lxml's schemas and parsers keep per-object state); the tests validate every golden document and every generated one with xmlschema too, and the fake DGII validates with xmlschema only, so two implementations that share no code agree on every document.
6. **Temporal lands in Milestone 4**, not after a Milestone 5 spike (§17). Verified on 2026-10-12 in a scratch environment: temporalio 1.34.0 (`cp310-abi3`, uploaded 2026-09-30) installs and runs on CPython 3.15; the workflow sandbox runs with PEP 810 `lazy` imports and keeps its restrictions (`datetime.now` refused inside a workflow); the time-skipping test environment skipped six hours of timers and activity retries in 0.6 s on both the proactor and the selector event loops; the development server (`temporal server start-dev`, image `temporalio/temporal:1.9.1` pinned by digest) answers its health check. The sandbox stays on; the fallback, if a later temporalio breaks it, is `workflow.unsafe.imports_passed_through` for more modules, then the sandbox off with determinism held by the replay test (Task 11). Workflow inputs and activity results are ids and words only (the default JSON converter does not carry `Decimal`, and a document's content never belongs in Temporal's history). One caveat found and handled: a `lazy` import of `temporalio.client` before `temporalio.workflow` is first imported breaks temporalio's own import cycle on 3.15, so `temporalio.client` is exempt from ruff's type-checking rule (Global Constraints).
7. **Workflows start from fiscal-do's own events.** `POST /v1/do/documents` numbers, stores and announces `fiscal.document.numbered` in one transaction; fiscal-do's consumer of that event starts `EmitEcf`, and of `fiscal.annulment.requested` starts `AnnulRange`. A committed document is a started workflow whatever crashes in between, and a redelivered event starts nothing more: workflow ids are derived (`ecf-{company}-{document}`, `anecf-{company}-{annulment}`) and Temporal refuses a second start (`REJECT_DUPLICATE`). The spec's `ecf-{company}-{encf}` is as deterministic; the document's id is used because the certificate signal reaches documents by what `Documents.blocked` returns.
8. **Object storage is S3 with object lock; MinIO is not used.** The owner asked for MinIO; on 2026-10-12 its Docker Hub images had been removed (September 2026), its Quay repository required authentication, its GitHub repository was archived, and Chainguard's image was published as `latest` only and failed to start in testing ("file access denied"). LocalStack 4.14.0, already pinned for vault's KMS, serves S3 with object lock, and was verified to refuse, in compliance mode, a version's deletion, a shortened retention and a governance bypass, and to honor `If-None-Match: *` and SHA-256 checksums. Production uses AWS S3 (or any S3 with object lock in compliance mode); the adapter is boto3 behind the `Archive` port.
9. **Ten years of retention, in compliance mode.** Código Tributario, art. 50 h, and DGII's e-CF technical report, §9: every signed document and every DGII answer is kept ten years (`RETENTION = 3653 days`), each object locked until its own date; keys name their content's digest, so keeping a step's output twice finds it there.
10. **One stream, three subject trees.** Stream `FISCAL_DO` holds `fiscal.document.>`, `fiscal.sequence.>` and `fiscal.annulment.>`, not `fiscal.>`: Milestone 6's fiscal-receiver publishes `fiscal.received.>` into a stream of its own, and NATS refuses two streams whose subjects overlap.
11. **Two watches outside row-level security**, as vault's `expiry_watch` is (its Decision 8): `sequence_watch` (a range's expiry and the smallest threshold announced) and `pipeline` (each document not yet decided, its state, since when). Workers read them across companies without a scope; they hold ids, states and dates, nothing a document says. Each expiry announcement is claimed in the company's own transaction.
12. **DGII's environments and services** (*Descripción Técnica Servicios DGII*, 2026-01-02): `https://ecf.dgii.gov.do/{testecf|certecf|ecf}/...` for authentication (`semilla`, `validarsemilla`), reception, result, track ids and annulments, and `https://fc.dgii.gov.do/{env}/recepcionfc/...` for consumer-invoice summaries. CerteCF publishes neither the track-id query nor the annulment service (`ServiceUnavailableInEnvironmentError`; an annulment there is refused before it is recorded). A token lasts one hour. Result codes: 0 not found, 1 accepted, 2 rejected, 3 in process, 4 accepted conditionally. In production fiscal-do refuses any host but DGII's (`FiscalSettings`).
13. **Dates of ranges.** DGII dates every range it authorizes except those of consumer invoices (32) and credit notes (34), and an e-CF of a dated type must carry its range's date (its schema requires it). Registering a range dated when its type is not, or undated when it is, is refused (`sequence_expiry_invalid`): found by this plan's end-to-end test, where an undated debit-note range produced an e-CF its schema refused. TesteCF hands out ranges 1 to 10,000,000 (type 32 up to 50,000,000) expiring 2028-12-31.
14. **What DGII's documents settle, and one reading of ours.** Rounding is half up; DGII tolerates ±1 per line and, globally, as many units as lines (technical report §12-13). Contingency (§19): documents emitted while DGII cannot be reached are sent within 72 hours of the connection's return. Consumer invoices under DOP 250,000 are sent as a summary (RFCE) and decided at once. The QR link's parameters are DGII's list (§18.2.3). *Our reading:* the security code is the first six characters of the `SignatureValue` (base64), as DGII's own printed examples show; the text says "dígitos del hash".
15. **DGII's client retries only what cannot change DGII's state** (the seed, its validation, results, track ids), twice, 0.2 s and 0.5 s apart; a document sent is never retried by the client: when the connection breaks after the request may have left, the step learns `DgiiOutcomeUnknownError`, records contingency, and the next attempt asks DGII for the e-NCF's track ids before sending again. A circuit breaker per environment (5 failures; 30 s, doubling to 10 minutes) turns an outage into one fast `UNAVAILABLE`; its state is a gauge.
16. **The fake DGII is written from DGII's documentation, independently of the client.** Its paths and answers come from *Descripción Técnica Servicios DGII*; it validates with xmlschema and checks signatures through a `SignatureCheck` (xmlsec1 in tests that need it, `TrustingCheck` otherwise and in the local stack). The two meet in the tests; DGII's own TesteCF arbitrates (Decision 22).
17. **Coverage reads with `sys.monitoring` (`core = "sysmon"`).** With the C tracer, lines of a route that run after SQLAlchemy's asynchronous driver switched greenlets were reported unexecuted although they ran (verified: `sysmon` reports them, and agrees everywhere else). Alembic loads revision files under module names of its own (`env_py`, `r0001_fiscal_do_py`), which `source_pkgs` cannot match: the migrations directory is listed in `source` by path. If Milestones 2 or 3 already changed the core, keep theirs.
18. **A defect in the Milestone 3 plan's migration runner**, found here: `Migrations.require_head` documents `SchemaNotCurrentError` for a database without Alembic's version table, but its code lets PostgreSQL's `UndefinedTable` through. fiscal-do's test of its readiness check uses a database taken back to base (the table exists, empty), which works either way; the runner should catch the missing table (reported for Milestone 3's executor, not changed here).
19. **The API names the company in a header** (`Ledger-Company-Id`, as `ledger_platform.routes.company_in_header` reads it): fiscal-do's paths carry no company. Permissions: `invoice.issue` to issue, `invoice.read` to read documents, ranges and annulments, `company.manage` to register ranges, annul numbers and change the fiscal profile, `company.read` to read it. `PUT /v1/do/profile` creates without `If-Match` and replaces the version `If-Match` names (412 otherwise).
20. **The benchmark measures fiscal-do's share.** Build, validate, sign over gRPC (a stand-in vault that signs with RSA-2048 and SHA-256) and validate again, for a 60-line credit invoice (25 KB): p50 4.8-7.6 ms, p95 7.4-10.9 ms over three runs on the scratch machine, against a target of 100 ms; vault's own signing p95 is below 50 ms end to end (Milestone 3, Task 14).
21. **Ranges, annulments and their numbers.** An annulment (ANECF) withdraws numbers not yet handed out, from one range, and is refused once DGII decided it; numbering skips withdrawn numbers. A range announces once that it has used 80 % of its numbers (`fiscal.sequence.low`) and once at each of 30, 7 and 1 days before it expires (`fiscal.sequence.expiring`).
22. **Deferred, recorded in the spec and the README (Task 15):** a run against DGII's TesteCF (it needs a taxpayer enrolled as an electronic issuer and its certificate; no such enrollment exists for this project yet: the client's paths are DGII's documented ones, exercised against the fake only); additional taxes (ISC, tips), currencies other than DOP, export and transport details, informative subtotals and global discounts or surcharges; the gRPC `FiscalDo` service (Milestone 5, when invoicing calls); checking an emission point against identity's; certificate revocation; ACECF and ARECF (Milestone 6); xmlsec1 in the local stack's fake DGII.

## File map

```
ledger/
├── pyproject.toml  uv.lock  .gitattributes  _typos.toml  Tiltfile  README.md  .github/workflows/ci.yml
├── deploy/local/{compose.yaml, fiscal-do.env, postgres/init.sql, localstack/init-archive.sh}
├── docs/superpowers/specs/2026-10-11-ledger-platform-design.md
├── libs/dgii/                                                                      (new, Task 1)
│   ├── pyproject.toml
│   ├── src/ledger_dgii/{__init__, published, schemas, names}.py, py.typed
│   ├── src/ledger_dgii/xsd/{ecf-31 ... ecf-47, rfce-32, anecf, acecf, arecf, semilla}.xsd     (DGII's, vendored)
│   └── tests/{test_published, test_schemas, test_names}.py
├── libs/contracts/proto/ledger/events/fiscal_do/v1/events.proto                     (Task 2)
├── libs/contracts/src/ledger/events/fiscal_do/v1/events_pb2{,_grpc}.py{,i}          (generated)
├── libs/contracts/src/ledger_contracts/fiscal_do.py
├── libs/contracts/tests/test_fiscal_do_contracts.py
├── libs/testing/src/ledger_testing/{dgii, serving}.py                               (new, Tasks 6, 12)
├── libs/testing/src/ledger_testing/containers.py                                    (modified, Tasks 7, 12)
├── libs/testing/fake-dgii.Dockerfile                                                (Task 14)
├── libs/testing/tests/{test_dgii, test_serving}.py
└── services/fiscal-do/
    ├── pyproject.toml  Dockerfile
    ├── benchmarks/emit_latency.py
    ├── src/ledger_fiscal_do/{__init__, settings, resources, main}.py, py.typed
    ├── src/ledger_fiscal_do/domain/{__init__, vocabulary, ids, encf, problems, content, form, notation, totals,
    │                                verdict, registry, checking, events, document, sequence, annulment, profile}.py
    ├── src/ledger_fiscal_do/domain/types/{__init__, base, credit_invoice, consumer_invoice, notes, debit_note,
    │                                      credit_note, purchases, minor_expenses, special_regimes, government,
    │                                      exports, foreign_payments}.py
    ├── src/ledger_fiscal_do/application/{__init__, ports, issuing, emission, annulling, ranges, profiles, queries}.py
    ├── src/ledger_fiscal_do/workflows/{__init__, orders, pacing, activities, emission}.py
    ├── src/ledger_fiscal_do/adapters/{__init__, archive, vault, identity, temporal, events}.py
    ├── src/ledger_fiscal_do/adapters/xml/{__init__, writer, formats, ecf, rfce, anecf, stamp, qr, documents}.py
    ├── src/ledger_fiscal_do/adapters/dgii/{__init__, endpoints, breaker, wire, client}.py
    ├── src/ledger_fiscal_do/adapters/postgres/{__init__, codec, tables, repositories, units, migrate}.py
    ├── src/ledger_fiscal_do/adapters/postgres/migrations/{__init__, env}.py, versions/{__init__, r0001_fiscal_do}.py
    ├── src/ledger_fiscal_do/api/{__init__, context, models, documents, sequences, profile, rest, consumers,
    │                             workers}.py
    └── tests/{conftest, samples, fakes, world, remotes}.py (support),
              tests/{test_encf, test_totals, test_checking, test_document, test_sequence, test_profile, test_xml,
                     test_ports, test_application, test_dgii_client, test_archive, test_remote, test_settings,
                     test_migrations, test_codec, test_events, test_repositories, test_workflows, test_service,
                     test_workers}.py,
              tests/golden/{ecf-31 ... ecf-47, rfce-32, anecf}.xml (written by Task 4, reviewed)
```

---

### Task 1: DGII's schemas vendored, corrected and checked (`libs/dgii`)

**Files:**
- Create: `libs/dgii/pyproject.toml`, `libs/dgii/src/ledger_dgii/__init__.py`, `py.typed`, `published.py`, `schemas.py`, `names.py`
- Create (downloaded by Step 3, never edited): `libs/dgii/src/ledger_dgii/xsd/{ecf-31,ecf-32,ecf-33,ecf-34,ecf-41,ecf-43,ecf-44,ecf-45,ecf-46,ecf-47,rfce-32,anecf,acecf,arecf,semilla}.xsd`
- Modify: `pyproject.toml` (root), `.gitattributes`, `_typos.toml`
- Test: `libs/dgii/tests/test_published.py`, `libs/dgii/tests/test_schemas.py`, `libs/dgii/tests/test_names.py`

**Interfaces:**
- Consumes: `ledger_kernel.errors` (`LedgerError`, `InvalidInputError`, `truncate`).
- Produces: `ledger_dgii.published`: `SOURCE`, `FETCHED_ON`, `Format` (`ECF_31` ... `ECF_47`, `RFCE`, `ANECF`, `ACECF`, `ARECF`, `SEED`; values are the vendored file names), `Published(format, name, sha256, root)` with `url`, `PUBLISHED: Mapping[Format, Published]`, `Correction(format, wrong, right, count, reason)`, `CORRECTIONS`. `ledger_dgii.schemas`: `DSIG`, `SIGNATURE`, `MAX_DOCUMENT` (4 MiB), `MAX_VIOLATIONS` (20), errors `SchemaDriftError`, `SchemaChangedError`, `CorrectionDriftError`, `UnreadableDocumentError` (`INVALID`) with `DocumentTooLargeError`, `DoctypeRefusedError`, `MalformedDocumentError`; `Violation(line, path, message)`; `published_bytes(format)`, `corrected(format)`, `parser()` (hardened, per thread), `schema(format)` (compiled, per thread), `require_readable(document, *, limit=MAX_DOCUMENT)`, `violations(format, document, *, unsigned=False) -> tuple[Violation, ...]`. `ledger_dgii.names`: `UndeclaredElementError`, `declared(format) -> Mapping[str, str]`, `element(format, name) -> str`.

Re-check before starting: the root `pyproject.toml` as Milestones 2 and 3 left it (workspace members `libs/*` and `services/*`, the dev group, the `poe` tasks `mypy-*` and `deps-*`, the import-linter contracts): this task adds to each, never replaces.

- [ ] **Step 1: Package, workspace and repository settings**

`libs/dgii/pyproject.toml`:

```toml
[project]
name = "ledger-dgii"
version = "0.1.0"
description = "What DGII publishes for electronic invoicing, as data: its XML schemas, and validation against them."
requires-python = ">=3.15"
dependencies = ["ledger-kernel", "lxml>=6.1.3"]

[tool.uv.sources]
ledger-kernel = { workspace = true }

[build-system]
requires = ["uv_build>=0.13.0,<0.14.0"]
build-backend = "uv_build"
```

`libs/dgii/src/ledger_dgii/__init__.py`:

```python
"""What DGII publishes for electronic invoicing (e-CF), as data: its XSDs, their corrections, validation."""
```

and an empty `libs/dgii/src/ledger_dgii/py.typed`.

`.gitattributes` — append (DGII's files keep their BOMs and CRLF line ends; the pinned SHA-256 is of the bytes as published):

```
libs/dgii/src/ledger_dgii/xsd/*.xsd -text
```

`_typos.toml` — `[files] extend-exclude` gains DGII's files and the documents written from them, which hold DGII's Spanish names (`libs/dgii/src/ledger_dgii/xsd/`, `services/fiscal-do/tests/golden/`):

```toml
[files]
extend-exclude = [
    "uv.lock",
    "libs/contracts/src/ledger/",
    "libs/dgii/src/ledger_dgii/xsd/",
    "services/fiscal-do/tests/golden/",
]
```

Root `pyproject.toml`:

- `[project] dependencies` gains `"ledger-dgii"`; `[tool.uv.sources]` gains `ledger-dgii = { workspace = true }`; the dev group gains `"xmlschema>=4.3.2"`.
- ruff: `known-first-party` gains `"ledger_dgii"`.
- coverage: `source_pkgs` gains `"ledger_dgii"`.
- poe: `mypy-dgii = { cmd = "mypy libs/dgii" }` and `deps-dgii = { cmd = "deptry libs/dgii/src --config libs/dgii/pyproject.toml" }`, each added to its sequence.
- import-linter: `"ledger_dgii"` joins the "Production code never imports test tooling" contract's `source_modules`, and a new contract:

```toml
[[tool.importlinter.contracts]]
name = "DGII's library knows DGII's files and the kernel, nothing of the platform"
type = "forbidden"
source_modules = ["ledger_dgii"]
forbidden_modules = ["ledger_platform", "ledger_contracts", "pydantic", "sqlalchemy", "fastapi", "httpx", "grpc"]
```

Run: `uv lock && uv sync`

- [ ] **Step 2: Write the failing tests**

`libs/dgii/tests/test_published.py`:

```python
import hashlib
from types import MappingProxyType

import pytest
import xmlschema
from lxml import etree

from ledger_dgii import schemas
from ledger_dgii.published import CORRECTIONS, PUBLISHED, SOURCE, Format, Published
from ledger_dgii.schemas import SchemaDriftError, corrected, published_bytes

XS = "{http://www.w3.org/2001/XMLSchema}"
NOT_AS_PUBLISHED = frozenset(correction.format for correction in CORRECTIONS)


@pytest.mark.parametrize("format_", list(Format))
def test_every_vendored_schema_is_the_file_dgii_published(format_: Format) -> None:
    published = PUBLISHED[format_]
    assert hashlib.sha256(published_bytes(format_)).hexdigest() == published.sha256
    assert published.url.startswith(SOURCE)
    assert " " not in published.url


@pytest.mark.parametrize("format_", list(Format))
def test_every_schema_compiles_in_both_implementations_once_corrected(format_: Format) -> None:
    data = corrected(format_)
    etree.XMLSchema(etree.fromstring(data))
    xmlschema.XMLSchema10(data.decode("utf-8"))
    root = etree.fromstring(data).find(f"{XS}element")
    assert root is not None
    assert root.get("name") == PUBLISHED[format_].root


@pytest.mark.parametrize("format_", sorted(NOT_AS_PUBLISHED))
def test_the_corrected_schemas_do_not_compile_as_published(format_: Format) -> None:
    with pytest.raises(etree.XMLSchemaParseError):
        etree.XMLSchema(etree.fromstring(published_bytes(format_)))


def test_the_types_46_and_47_are_named_as_the_schemas_name_them() -> None:
    text = published_bytes(Format.ECF_46).decode("utf-8-sig")
    assert '<xs:enumeration value="46"/> <!-- Comprobante de Exportaciones Electrónico -->' in text
    assert '<xs:enumeration value="47"/> <!-- Comprobante para Pagos al Exterior Electrónico -->' in text


def test_a_schema_that_changed_is_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    def changed(format_: Format) -> bytes:
        del format_
        return b"<changed/>"

    monkeypatch.setattr(schemas, "published_bytes", changed)
    with pytest.raises(SchemaDriftError, match="is not the file pinned"):
        corrected(Format.SEED)


def test_a_correction_that_no_longer_applies_is_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    fixed = published_bytes(Format.ACECF).replace(b"(?:", b"(")
    pinned = Published(
        format=Format.ACECF, name="ACECF v.1.0.xsd", sha256=hashlib.sha256(fixed).hexdigest(), root="ACECF"
    )

    def fixed_by_dgii(format_: Format) -> bytes:
        del format_
        return fixed

    monkeypatch.setattr(schemas, "published_bytes", fixed_by_dgii)
    monkeypatch.setattr(schemas, "PUBLISHED", MappingProxyType({Format.ACECF: pinned}))
    with pytest.raises(SchemaDriftError, match="holds 0 of a correction pinned at 1"):
        corrected(Format.ACECF)
```


`libs/dgii/tests/test_schemas.py`:

```python
import threading
from typing import Final

import pytest

from ledger_dgii.published import Format
from ledger_dgii.schemas import MAX_DOCUMENT, UnreadableDocumentError, require_readable, schema, violations

SEED: Final = b"<SemillaModel><valor>abc</valor><fecha>2026-10-12T09:00:00-04:00</fecha></SemillaModel>"
SIGNED_SEED: Final = SEED.replace(
    b"</SemillaModel>", b'<Signature xmlns="http://www.w3.org/2000/09/xmldsig#"/></SemillaModel>'
)


def test_a_document_is_checked_as_it_will_be_once_signed() -> None:
    assert violations(Format.SEED, SEED, unsigned=True) == ()
    assert violations(Format.SEED, SIGNED_SEED) == ()
    (missing,) = violations(Format.SEED, SEED)
    assert missing.path == "/SemillaModel"
    assert "Missing child element" in missing.message


def test_a_violation_says_where_and_cuts_what_libxml2_quotes() -> None:
    wrong = SEED.replace(b"2026-10-12T09:00:00-04:00", b"x" * 400)
    (violation,) = violations(Format.SEED, wrong, unsigned=True)
    assert (violation.line, violation.path) == (1, "/SemillaModel/fecha")
    assert len(violation.message) <= 163


@pytest.mark.parametrize(
    ("document", "code"),
    [
        (b"x" * (MAX_DOCUMENT + 1), "document_too_large"),
        (b'<?xml version="1.0"?><!DOCTYPE a [<!ENTITY b "c">]><a>&b;</a>', "document_doctype_refused"),
        (b"<!doctype a><a/>", "document_doctype_refused"),
        (b"<a>", "document_malformed"),
    ],
    ids=["too-large", "doctype", "doctype-lowercase", "malformed"],
)
def test_what_cannot_be_read_safely_is_refused_unread(document: bytes, code: str) -> None:
    with pytest.raises(UnreadableDocumentError) as caught:
        violations(Format.SEED, document)
    assert (caught.value.code, caught.value.kind.value) == (code, "invalid")
    assert "ENTITY" not in str(caught.value)


def test_the_limit_is_the_caller_s() -> None:
    require_readable(b"<a/>", limit=4)
    with pytest.raises(UnreadableDocumentError):
        require_readable(b"<ab/>", limit=4)


def test_each_thread_compiles_its_own_schema() -> None:
    here = schema(Format.SEED)
    assert schema(Format.SEED) is here
    elsewhere: list[object] = []
    thread = threading.Thread(target=lambda: elsewhere.append(schema(Format.SEED)))
    thread.start()
    thread.join()
    assert elsewhere[0] is not here
```


`libs/dgii/tests/test_names.py`:

```python
import pytest

from ledger_dgii.names import UndeclaredElementError, declared, element
from ledger_dgii.published import Format


def test_an_element_is_named_as_its_schema_declares_it() -> None:
    assert element(Format.ECF_31, "rncemisor") == "RNCEmisor"
    assert element(Format.ANECF, "rncemisor") == "RncEmisor"
    assert element(Format.RFCE, "codigoseguridadecf") == "CodigoSeguridadeCF"


def test_an_element_a_schema_does_not_declare_is_refused() -> None:
    with pytest.raises(UndeclaredElementError, match=r"ecf-43.xsd"):
        element(Format.ECF_43, "comprador")


def test_names_that_differ_only_in_case_are_left_out() -> None:
    assert "subtotalimpuestoadicional" not in declared(Format.ECF_31)
    assert all(key == key.lower() for key in declared(Format.ECF_31))
```


- [ ] **Step 3: Implement**

`libs/dgii/src/ledger_dgii/published.py`:

```python
"""What DGII publishes for e-CF, as it published it: the XSDs, where each came from, and their SHA-256.

The files under ``xsd/`` are DGII's own, byte for byte (BOMs and CRLF line ends included:
``.gitattributes`` keeps git from touching them), renamed only so that a path holds no space. Each is pinned
to the SHA-256 it had when it was fetched: a file that changed fails ``test_published``, and replacing one is
a deliberate change of this module (a new hash, a new date), never a silent download.

Three of the fifteen do not compile as DGII publishes them; ``CORRECTIONS`` holds the smallest edit that makes
each compile, applied when the schema is loaded and never to the file.
"""

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final, final
lazy from collections.abc import Mapping

SOURCE: Final = (
    "https://dgii.gov.do/cicloContribuyente/facturacion/comprobantesFiscalesElectronicosE-CF/"
    "Documentacin%20sobre%20eCF/Documentaci%C3%B3n%20T%C3%A9cnica%20(XSD)/"
)
"""Where DGII publishes its XSDs (its page of e-CF documentation links each file under it)."""
FETCHED_ON: Final = "2026-10-12"


class Format(StrEnum):
    """Each XML format DGII defines, by the file of its schema."""

    ECF_31 = "ecf-31.xsd"
    ECF_32 = "ecf-32.xsd"
    ECF_33 = "ecf-33.xsd"
    ECF_34 = "ecf-34.xsd"
    ECF_41 = "ecf-41.xsd"
    ECF_43 = "ecf-43.xsd"
    ECF_44 = "ecf-44.xsd"
    ECF_45 = "ecf-45.xsd"
    ECF_46 = "ecf-46.xsd"
    ECF_47 = "ecf-47.xsd"
    RFCE = "rfce-32.xsd"
    """The summary of a consumer invoice (type 32) under DOP 250,000."""
    ANECF = "anecf.xsd"
    """The annulment of unused e-NCF ranges."""
    ACECF = "acecf.xsd"
    """A buyer's commercial approval or rejection."""
    ARECF = "arecf.xsd"
    """A buyer's acknowledgement of receipt."""
    SEED = "semilla.xsd"
    """The authentication seed (``SemillaModel``), signed to obtain a token."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Published:
    """One schema as DGII published it."""

    format: Format
    name: str
    """The file's name on DGII's site."""
    sha256: str
    """Of the file as fetched, lowercase hexadecimal."""
    root: str
    """The root element of the documents it describes."""

    @property
    def url(self) -> str:
        """Where it was fetched from."""
        return SOURCE + self.name.replace(" ", "%20")


def _published(format_: Format, name: str, sha256: str, root: str) -> Published:
    return Published(format=format_, name=name, sha256=sha256, root=root)


PUBLISHED: Final[Mapping[Format, Published]] = MappingProxyType(
    {
        entry.format: entry
        for entry in (
            _published(
                Format.ECF_31,
                "e-CF 31 v.1.0.xsd",
                "6f2909a93d84919518d2ae3c77fead4b35c3e8c95996b8af67b0040c2e2be298",
                "ECF",
            ),
            _published(
                Format.ECF_32,
                "e-CF 32 v.1.0.xsd",
                "151676df03c61335b4ac1eca97f99395488d57ea92793b49353602871e651f3c",
                "ECF",
            ),
            _published(
                Format.ECF_33,
                "e-CF 33 v.1.0.xsd",
                "087cd71beba11c19cf548e22b96363cb4aa210dc6f5c3c8a62122a42c88d2866",
                "ECF",
            ),
            _published(
                Format.ECF_34,
                "e-CF 34 v.1.0.xsd",
                "6c62ebabdea9a6f6d62ae85d9e1c2eb788f44f3f0b61945fe116700c4eaac2da",
                "ECF",
            ),
            _published(
                Format.ECF_41,
                "e-CF 41 v.1.0.xsd",
                "eae1993c637375bc1cbe80932411d87f5680cd54e77e4d1b2752d72a6c8b2ab3",
                "ECF",
            ),
            _published(
                Format.ECF_43,
                "e-CF 43 v.1.0.xsd",
                "776f030980c2c50cf0221e9263c55f367c8631727adff00ab45e2c7c1abafc52",
                "ECF",
            ),
            _published(
                Format.ECF_44,
                "e-CF 44 v.1.0.xsd",
                "19834f37a9f0e2db40f00c80d07c2bf92f9019f23474480f32cef1ba06af4e67",
                "ECF",
            ),
            _published(
                Format.ECF_45,
                "e-CF 45 v.1.0.xsd",
                "030492cc8ef7d1a09a89b16b241f8e5c4920dfd09c0b629b1db8e68406ecd6ca",
                "ECF",
            ),
            _published(
                Format.ECF_46,
                "e-CF 46 v.1.0.xsd",
                "e7f8613ade25c7efb88e84d34d7c0ad330d1b22238ad9ef90c80aed01911d67b",
                "ECF",
            ),
            _published(
                Format.ECF_47,
                "e-CF 47 v.1.0.xsd",
                "14daf18f52f63dd80e439a18fb60a102d74e71243ee90ee253367f61ce8e1994",
                "ECF",
            ),
            _published(
                Format.RFCE,
                "RFCE 32 v.1.0.xsd",
                "6aad535875661b05eef072963295202fc94b3b320b257791feb8d3c39a5f8ee6",
                "RFCE",
            ),
            _published(
                Format.ANECF,
                "ANECF v.1.0.xsd",
                "af2e6a16c2900dfa55264d6ebec58ccbbbb25c1eb9821ee18ccc44fc142d0e78",
                "ANECF",
            ),
            _published(
                Format.ACECF,
                "ACECF v.1.0.xsd",
                "072f65de202df8ec136a8d4493e0592172690a4e181047245401cc2e7b23c095",
                "ACECF",
            ),
            _published(
                Format.ARECF,
                "ARECF v1.0.xsd",
                "c6d186167159110959eb3f54706ecc7462ad9128fc2c3e03440c8352b1c66f10",
                "ARECF",
            ),
            _published(
                Format.SEED,
                "Semilla v.1.0.xsd",
                "bebef2033c930a30c8cef45b513db00403423125a7f3f48e100a73e3e355f67e",
                "SemillaModel",
            ),
        )
    }
)


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Correction:
    """The least edit that makes a published schema compile: ``wrong``, ``count`` times, becomes ``right``."""

    format: Format
    wrong: str
    right: str
    count: int
    reason: str


CORRECTIONS: Final[tuple[Correction, ...]] = (
    Correction(
        format=Format.ECF_31,
        wrong='name=" IndicadorServicioTodoIncluidoType"',
        right='name="IndicadorServicioTodoIncluidoType"',
        count=1,
        reason="a space before the type's name leaves the element that uses it with no type",
    ),
    Correction(
        format=Format.ACECF,
        wrong="(?:",
        right="(",
        count=1,
        reason="a non-capturing group is Perl's, not XML Schema's: the date pattern does not compile",
    ),
    Correction(
        format=Format.RFCE,
        wrong="(?:",
        right="(",
        count=4,
        reason="a non-capturing group is Perl's, not XML Schema's: date and amount patterns do not compile",
    ),
)
"""Every correction, each checked to apply exactly ``count`` times: when DGII fixes a file, its hash changes
and ``test_published`` says so before a correction could silently stop applying."""
```


`libs/dgii/src/ledger_dgii/schemas.py`:

```python
"""DGII's schemas, loaded with their corrections, and the validation of documents against them.

Validation runs on libxml2 (lxml's ``XMLSchema``): about half a millisecond for a 60-line e-CF, where the
pure-Python ``xmlschema`` takes twenty (measured on 2026-10-12); the tests validate every golden document with
``xmlschema`` as well, a second implementation that shares no code with the first. Neither a compiled schema
nor a parser is shared between threads (lxml keeps per-object state): each thread makes its own on first use.

Every DGII document ends with its XML signature, which each schema demands (``xs:any``, at least once). A
document is checked before it is signed as it will be once signed: ``unsigned=True`` validates it with an
empty ``Signature`` element in the place the signature will take.

Documents are read by a parser that resolves no entity, loads no DTD and reaches no network (§9), after a
size check, and a document that declares a DOCTYPE is refused before it is parsed.
"""

import hashlib
import threading
from dataclasses import dataclass
from importlib.resources import files
from typing import ClassVar, Final, final

from lxml import etree

from ledger_dgii.published import CORRECTIONS, PUBLISHED
from ledger_kernel.errors import InvalidInputError, LedgerError, truncate
lazy from ledger_dgii.published import Format

DSIG: Final = "http://www.w3.org/2000/09/xmldsig#"
SIGNATURE: Final = f"{{{DSIG}}}Signature"
MAX_DOCUMENT: Final = 4 * 1024 * 1024
"""The largest document read: a type-32 e-CF of 10,000 lines is about 3.5 MiB."""
MAX_VIOLATIONS: Final = 20
"""How many problems a refusal lists."""
_MESSAGE: Final = 160
"""How much of libxml2's message a violation keeps: it names the element and the facet, may quote a value."""
_DOCTYPE: Final = b"<!DOCTYPE"
_PROLOGUE: Final = 1024
"""Where a DOCTYPE could stand: before the root element, after the XML declaration and comments."""


class SchemaDriftError(LedgerError):
    """A vendored schema is not as pinned: a defect to fix, never a client's mistake."""

    code: ClassVar[str] = "dgii_schema_drift"


class SchemaChangedError(SchemaDriftError):
    """A vendored file is not the one whose SHA-256 ``ledger_dgii.published`` pins."""

    def __init__(self, format_: Format) -> None:
        super().__init__(f"DGII's schema {format_.value} is not the file pinned in ledger_dgii.published")


class CorrectionDriftError(SchemaDriftError):
    """A correction no longer applies as many times as pinned."""

    def __init__(self, format_: Format, *, found: int, pinned: int) -> None:
        super().__init__(f"DGII's schema {format_.value} holds {found} of a correction pinned at {pinned}")


class UnreadableDocumentError(InvalidInputError):
    """Bytes that are not a document read here; each subclass says why, never quoting the document."""

    code: ClassVar[str] = "document_unreadable"


class DocumentTooLargeError(UnreadableDocumentError):
    """A document larger than the limit: refused unread."""

    code: ClassVar[str] = "document_too_large"

    def __init__(self, limit: int) -> None:
        super().__init__(f"a document is at most {limit} bytes")


class DoctypeRefusedError(UnreadableDocumentError):
    """A document that declares a DOCTYPE: no DTD, internal or external, is ever read."""

    code: ClassVar[str] = "document_doctype_refused"

    def __init__(self) -> None:
        super().__init__("a document that declares a DOCTYPE is refused")


class MalformedDocumentError(UnreadableDocumentError):
    """Not well-formed XML. The parser's message quotes the document, so it is not repeated."""

    code: ClassVar[str] = "document_malformed"

    def __init__(self) -> None:
        super().__init__("the document is not well-formed XML")


@final
@dataclass(frozen=True, slots=True)
class Violation:
    """One way a document breaks its schema: where (line and element path), and what libxml2 says.

    libxml2's message can quote the value it refused, so it is cut short and never logged: it reaches only the
    client whose document it is, in the answer to its own request.
    """

    line: int
    path: str
    message: str


def published_bytes(format_: Format) -> bytes:
    """The schema of ``format_`` exactly as DGII published it."""
    return files("ledger_dgii").joinpath("xsd", format_.value).read_bytes()


def corrected(format_: Format) -> bytes:
    """The schema of ``format_`` with its corrections applied; ``SchemaDriftError`` if it is not as pinned."""
    data = published_bytes(format_)
    if hashlib.sha256(data).hexdigest() != PUBLISHED[format_].sha256:
        raise SchemaChangedError(format_)
    text = data.decode("utf-8-sig")
    for correction in CORRECTIONS:
        if correction.format is not format_:
            continue
        found = text.count(correction.wrong)
        if found != correction.count:
            raise CorrectionDriftError(format_, found=found, pinned=correction.count)
        text = text.replace(correction.wrong, correction.right)
    return text.encode("utf-8")


@final
class _PerThread(threading.local):
    """What this thread made: its parser and its compiled schemas."""

    def __init__(self) -> None:
        self.parser: etree.XMLParser = etree.XMLParser(
            resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False
        )
        self.schemas: dict[Format, etree.XMLSchema] = {}


_THREAD: Final = _PerThread()


def parser() -> etree.XMLParser:
    """The calling thread's hardened parser (see the module)."""
    return _THREAD.parser


def schema(format_: Format) -> etree.XMLSchema:
    """The compiled schema of ``format_`` for the calling thread."""
    found = _THREAD.schemas.get(format_)
    if found is None:
        found = _THREAD.schemas[format_] = etree.XMLSchema(etree.fromstring(corrected(format_), parser()))
    return found


def require_readable(document: bytes, *, limit: int = MAX_DOCUMENT) -> None:
    """Refuse, unread, a document over ``limit`` bytes or one that declares a DOCTYPE."""
    if len(document) > limit:
        raise DocumentTooLargeError(limit)
    if _DOCTYPE in document[:_PROLOGUE].upper():
        raise DoctypeRefusedError


def violations(format_: Format, document: bytes, *, unsigned: bool = False) -> tuple[Violation, ...]:
    """How ``document`` breaks the schema of ``format_``; empty when it does not (``unsigned``: the module).

    An ``UnreadableDocumentError`` when it cannot be read at all.
    """
    require_readable(document)
    try:
        root = etree.fromstring(document, parser())
    except etree.XMLSyntaxError as error:
        raise MalformedDocumentError from error
    if unsigned:
        etree.SubElement(root, SIGNATURE)
    checked = schema(format_)
    if checked.validate(root):
        return ()
    return tuple(
        Violation(line=entry.line, path=entry.path or "", message=truncate(entry.message, _MESSAGE))
        for entry in list(checked.error_log)[:MAX_VIOLATIONS]
    )
```


`libs/dgii/src/ledger_dgii/names.py`:

```python
"""The names of DGII's elements, as each schema declares them, looked up by their lowercase spelling.

Code that writes a DGII document names each element in lowercase (``rncemisor``) and gets the name its
schema declares (``RNCEmisor``): every name written is one DGII's schema holds, so a misspelled one fails
where it is written (``UndeclaredElementError``), not later as a schema violation, and DGII's names, which are
Spanish, live in DGII's files only (the vendored XSDs), not spelled again in ours.

Two names of one schema that differ only in case cannot be looked up this way and are left out: DGII's
``SubTotalImpuestoAdicional`` and ``SubtotalImpuestoAdicional`` (additional taxes, which fiscal-do does not
write yet).
"""

from collections import Counter
from functools import cache
from types import MappingProxyType
from typing import ClassVar, Final
lazy from collections.abc import Mapping

from lxml import etree

from ledger_dgii.schemas import corrected, parser
from ledger_kernel.errors import LedgerError
lazy from ledger_dgii.published import Format

ELEMENT: Final = "{http://www.w3.org/2001/XMLSchema}element"


class UndeclaredElementError(LedgerError):
    """Code named an element its schema does not declare: a defect of the writer."""

    code: ClassVar[str] = "dgii_element_undeclared"

    def __init__(self, format_: Format, name: str) -> None:
        super().__init__(f"DGII's schema {format_.value} declares no element {name}")


@cache
def declared(format_: Format) -> Mapping[str, str]:
    """The elements ``format_``'s schema declares, by their lowercase names (see the module)."""
    root = etree.fromstring(corrected(format_), parser())
    names = {name for element in root.iter(ELEMENT) if (name := element.get("name")) is not None}
    spellings = Counter(name.lower() for name in names)
    return MappingProxyType({name.lower(): name for name in names if spellings[name.lower()] == 1})


def element(format_: Format, name: str) -> str:
    """The element ``format_`` declares as ``name`` in lowercase; ``UndeclaredElementError`` if none."""
    found = declared(format_).get(name)
    if found is None:
        raise UndeclaredElementError(format_, name)
    return found
```


Vendor the fifteen files from DGII, each checked against its pinned SHA-256 before it is written (a file DGII changed since 2026-10-12 stops the download: compare it with the published one, then pin the new hash and date deliberately):

```bash
uv run python - <<'EOF'
import hashlib
from pathlib import Path

import httpx

from ledger_dgii.published import PUBLISHED

target = Path("libs/dgii/src/ledger_dgii/xsd")
target.mkdir(exist_ok=True)
with httpx.Client(timeout=60, follow_redirects=True) as client:
    for entry in PUBLISHED.values():
        data = client.get(entry.url).raise_for_status().content
        found = hashlib.sha256(data).hexdigest()
        if found != entry.sha256:
            raise SystemExit(f"{entry.name}: {found} is not the pinned {entry.sha256}")
        (target / entry.format.value).write_bytes(data)
print(sorted(path.name for path in target.iterdir()))
EOF
```

Expected: the fifteen file names. (DGII's site answered scripted downloads on 2026-10-12; if it refuses one, download it from the page in a browser into the same name: the hash still decides.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest libs/dgii -q --no-cov`
Expected: PASS (`test_every_vendored_schema_is_the_file_dgii_published` checks all fifteen hashes; `test_every_schema_compiles_in_both_implementations_once_corrected` compiles all fifteen in lxml and in xmlschema).

- [ ] **Step 5: Gate**

Run: `uv run poe fmt && uv run poe check`
Expected: every step passes, coverage 100 %.

- [ ] **Step 6: Commit**

```bash
git add .gitattributes _typos.toml pyproject.toml uv.lock libs/dgii
git commit -m "feat(dgii): DGII's XSDs vendored and pinned, corrected on load, hardened validation

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Contracts: fiscal-do's events and their subjects

**Files:**
- Create: `libs/contracts/proto/ledger/events/fiscal_do/v1/events.proto`, `libs/contracts/src/ledger_contracts/fiscal_do.py`
- Generate (committed): `libs/contracts/src/ledger/events/fiscal_do/v1/events_pb2.py`, `events_pb2.pyi`, `events_pb2_grpc.py`, `events_pb2_grpc.pyi`
- Test: `libs/contracts/tests/test_fiscal_do_contracts.py`

**Interfaces:**
- Consumes: `Envelope` (`ledger_contracts.envelope`), `ledger.common.v1.Money`, `google/protobuf/timestamp.proto`.
- Produces: package `ledger.events.fiscal_do.v1`: enums `DgiiEnvironment`, `Verdict`; messages `DgiiMessage`, `DocumentNumbered`, `DocumentAccepted`, `DocumentConditionallyAccepted`, `DocumentRejected`, `SequenceLow`, `SequenceExpiring`, `AnnulmentRequested`, `AnnulmentDecided`. `ledger_contracts.fiscal_do`: `STREAM = "FISCAL_DO"`, `SUBJECTS` (the three trees), and one constant per subject (`DOCUMENT_NUMBERED` ... `ANNULMENT_DECIDED`).

Re-check before starting: how Milestones 2 and 3 import generated modules whose package has no `__init__.py` (`from ledger.events.vault.v1 import events_pb2`): fiscal-do imports `import ledger.events.fiscal_do.v1.events_pb2 as wire`, which basedpyright resolved in the scratch workspace where the `from ... import` form reported a missing stub; use whichever form the landed contracts use if it type-checks.

- [ ] **Step 1: Write the failing test**

`libs/contracts/tests/test_fiscal_do_contracts.py`:

```python
from datetime import UTC, datetime

import ledger.events.fiscal_do.v1.events_pb2 as wire
from ledger.common.v1.money_pb2 import Money
from ledger_contracts import fiscal_do
from ledger_contracts.envelope import Envelope
from ledger_kernel.ids import CompanyId, OrganizationId, Tenant, new_id

TENANT = Tenant(OrganizationId(new_id()), CompanyId(new_id()))


def test_every_subject_lies_in_one_of_the_stream_s_trees_and_none_overlaps_reception() -> None:
    subjects = (
        fiscal_do.DOCUMENT_NUMBERED,
        fiscal_do.DOCUMENT_ACCEPTED,
        fiscal_do.DOCUMENT_CONDITIONALLY_ACCEPTED,
        fiscal_do.DOCUMENT_REJECTED,
        fiscal_do.SEQUENCE_LOW,
        fiscal_do.SEQUENCE_EXPIRING,
        fiscal_do.ANNULMENT_REQUESTED,
        fiscal_do.ANNULMENT_DECIDED,
    )
    trees = tuple(tree.removesuffix(">") for tree in fiscal_do.SUBJECTS)
    assert all(sum(subject.startswith(tree) for tree in trees) == 1 for subject in subjects)
    assert not any("fiscal.received.".startswith(tree) for tree in trees)
    assert fiscal_do.STREAM == "FISCAL_DO"


def test_an_event_travels_in_an_envelope_and_back() -> None:
    numbered = wire.DocumentNumbered(
        document_id=str(new_id()),
        organization_id=str(TENANT.organization),
        company_id=str(TENANT.company),
        encf="E310000000001",
        version=1,
        total=Money(amount="1180.00", currency="DOP"),
        environment=wire.DGII_ENVIRONMENT_PRECERTIFICATION,
    )
    envelope = Envelope.wrap(
        numbered, source="fiscal-do", tenant=TENANT, clock=lambda: datetime(2026, 10, 12, tzinfo=UTC)
    )
    received = Envelope.from_headers(envelope.headers(), envelope.data)
    assert received.unwrap(wire.DocumentNumbered) == numbered
    assert received.type == "ledger.events.fiscal_do.v1.DocumentNumbered"
```


- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest libs/contracts/tests/test_fiscal_do_contracts.py -q --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'ledger.events.fiscal_do'`.

- [ ] **Step 3: Implement**

`libs/contracts/proto/ledger/events/fiscal_do/v1/events.proto`:

```proto
syntax = "proto3";

package ledger.events.fiscal_do.v1;

import "google/protobuf/timestamp.proto";
import "ledger/common/v1/money.proto";

// What fiscal-do announces about Dominican e-CF documents, their number ranges and their annulments.
// Every event is scoped to its company and organization (the envelope carries both too). Ids are canonical
// UUIDs; an e-NCF is "E" + the type's two digits + ten digits (E310000000001); a type is DGII's number (31).

// DGII's environments: testecf (pre-certification), certecf (certification), ecf (production).
enum DgiiEnvironment {
  DGII_ENVIRONMENT_UNSPECIFIED = 0;
  DGII_ENVIRONMENT_PRECERTIFICATION = 1;
  DGII_ENVIRONMENT_CERTIFICATION = 2;
  DGII_ENVIRONMENT_PRODUCTION = 3;
}

// One message DGII gave with its answer, verbatim.
message DgiiMessage {
  string code = 1;
  string text = 2;
}

// A document has its e-NCF: it will be signed and sent. Subject fiscal.document.numbered.
message DocumentNumbered {
  string document_id = 1;
  string organization_id = 2;
  string company_id = 3;
  string encf = 4;
  int64 version = 5;
  ledger.common.v1.Money total = 6;
  DgiiEnvironment environment = 7;
}

// DGII accepted the document. Subject fiscal.document.accepted.
message DocumentAccepted {
  string document_id = 1;
  string organization_id = 2;
  string company_id = 3;
  string encf = 4;
  int64 version = 5;
}

// DGII accepted the document with observations. Subject fiscal.document.conditionally_accepted.
message DocumentConditionallyAccepted {
  string document_id = 1;
  string organization_id = 2;
  string company_id = 3;
  string encf = 4;
  int64 version = 5;
  repeated DgiiMessage messages = 6;
}

// DGII rejected the document; its e-NCF stays used. Subject fiscal.document.rejected.
message DocumentRejected {
  string document_id = 1;
  string organization_id = 2;
  string company_id = 3;
  string encf = 4;
  int64 version = 5;
  repeated DgiiMessage messages = 6;
}

// A range has used 80 % of its numbers. Subject fiscal.sequence.low.
message SequenceLow {
  string sequence_id = 1;
  string organization_id = 2;
  string company_id = 3;
  int32 ecf_type = 4;
  int64 remaining = 5;
  int64 version = 6;
}

// A range expires within days_left days (30, 7 or 1). Subject fiscal.sequence.expiring.
message SequenceExpiring {
  string sequence_id = 1;
  string organization_id = 2;
  string company_id = 3;
  int32 ecf_type = 4;
  // The last day the range may be used, in the Dominican Republic: midnight UTC of that date.
  google.protobuf.Timestamp expires_on = 5;
  int32 days_left = 6;
}

// Unused numbers of a range were withdrawn; DGII is to be told. Subject fiscal.annulment.requested.
message AnnulmentRequested {
  string annulment_id = 1;
  string organization_id = 2;
  string company_id = 3;
  string sequence_id = 4;
  string first_encf = 5;
  string last_encf = 6;
}

// DGII's verdict on a document or an annulment.
enum Verdict {
  VERDICT_UNSPECIFIED = 0;
  VERDICT_ACCEPTED = 1;
  VERDICT_ACCEPTED_CONDITIONALLY = 2;
  VERDICT_REJECTED = 3;
}

// DGII answered an annulment. Subject fiscal.annulment.decided.
message AnnulmentDecided {
  string annulment_id = 1;
  string organization_id = 2;
  string company_id = 3;
  Verdict verdict = 4;
  repeated DgiiMessage messages = 5;
}
```


`libs/contracts/src/ledger_contracts/fiscal_do.py`:

```python
"""fiscal-do's events on the wire: the subjects they are published on, and the stream that holds them.

Stream ``FISCAL_DO`` holds three explicit subject trees, ``fiscal.document.>``, ``fiscal.sequence.>`` and
``fiscal.annulment.>``, never ``fiscal.>``: Milestone 6's fiscal-receiver publishes ``fiscal.received.>``
into a stream of its own, and NATS refuses two streams whose subjects overlap.
"""

from typing import Final

STREAM: Final = "FISCAL_DO"
SUBJECTS: Final = ("fiscal.document.>", "fiscal.sequence.>", "fiscal.annulment.>")
DOCUMENT_NUMBERED: Final = "fiscal.document.numbered"
DOCUMENT_ACCEPTED: Final = "fiscal.document.accepted"
DOCUMENT_CONDITIONALLY_ACCEPTED: Final = "fiscal.document.conditionally_accepted"
DOCUMENT_REJECTED: Final = "fiscal.document.rejected"
SEQUENCE_LOW: Final = "fiscal.sequence.low"
SEQUENCE_EXPIRING: Final = "fiscal.sequence.expiring"
ANNULMENT_REQUESTED: Final = "fiscal.annulment.requested"
ANNULMENT_DECIDED: Final = "fiscal.annulment.decided"
```


Run: `uv run poe proto && uv run poe proto-lint`
Expected: the four generated files appear under `libs/contracts/src/ledger/events/fiscal_do/v1/`; `buf lint` passes (verified with `bufbuild/buf:1.73.0`).

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest libs/contracts/tests/test_fiscal_do_contracts.py -q --no-cov`
Expected: PASS.

- [ ] **Step 5: Gate**

Run: `uv run poe fmt && uv run poe check`
Expected: every step passes (CI's regeneration diff included), coverage 100 %.

- [ ] **Step 6: Commit**

```bash
git add libs/contracts
git commit -m "feat(contracts): fiscal-do's events, one stream of three subject trees

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: The fiscal-do package and its domain: values, ten types' rules, the life of documents and ranges

**Files:**
- Create: `services/fiscal-do/pyproject.toml`, `services/fiscal-do/src/ledger_fiscal_do/__init__.py`, `py.typed`, `domain/__init__.py`, `domain/vocabulary.py`, `domain/ids.py`, `domain/encf.py`, `domain/problems.py`, `domain/content.py`, `domain/form.py`, `domain/notation.py`, `domain/totals.py`, `domain/verdict.py`, `domain/types/__init__.py`, `domain/types/base.py`, one module per type (`credit_invoice`, `consumer_invoice`, `notes`, `debit_note`, `credit_note`, `purchases`, `minor_expenses`, `special_regimes`, `government`, `exports`, `foreign_payments`), `domain/registry.py`, `domain/checking.py`, `domain/events.py`, `domain/document.py`, `domain/sequence.py`, `domain/annulment.py`, `domain/profile.py`
- Modify: `pyproject.toml` (root)
- Test: `services/fiscal-do/tests/samples.py` (test support), `test_encf.py`, `test_totals.py`, `test_checking.py`, `test_document.py`, `test_sequence.py`, `test_profile.py`

**Interfaces:**
- Consumes: `ledger_kernel.money` (`Money`, `Currency`, `Money.of`, `rounded`), `ledger_kernel.countries.do.TaxId`, `ledger_kernel.ids` (`Tenant`, `new_id`), `ledger_kernel.errors`.
- Produces: `vocabulary`: `EcfType` (31, 32, 33, 34, 41, 43, 44, 45, 46, 47, named as DGII's XSDs comment them), `TITLES`, `IncomeType`, `PaymentTerms`, `PaymentMethod`, `Billing` (0-4), `ItemKind`, `Modification` (1-5), `DgiiEnvironment` (`testecf`, `certecf`, `ecf`), `OFFICIAL_ECF`, `OFFICIAL_FC`. `ids`: `DocumentId`, `SequenceId`, `AnnulmentId`, `EmissionPointId` (UUIDs), `TrackId` (str). `encf`: `Encf(ecf_type, number)` with `parse`/`__str__`, `InvalidEncfError`, `EncfNumberError`, `require_number`. `problems`: `ProblemCode`, `Problem(field, code)`, `ProblemsError` (`problems`, `omitted`, `MAX_PROBLEMS = 20`), `DocumentInvalidError` (`ecf_invalid`), `ProfileInvalidError` (`fiscal_profile_invalid`), `require_no_problems`. `content`: `Issuer`, `Buyer`, `Withholding`, `Line`, `Payment`, `Reference`, `Content`. `form`: the limits and the checks of each kind of value (`text`, `area_code`, `phone`, `ncf`, `amount`, `quantity`, `unit_price`, `places`). `notation`: `SANTO_DOMINGO`, `dgii_date`, `dgii_moment`, `local_date`, `dgii_amount`, `dgii_number`. `totals`: `ITBIS_RATES`, `TAXED`, `ZERO`, `gross`, `line_amount`, `taxed_base`, `itbis`, `TaxedGroup`, `Totals` (`group`, `taxed_total`, `itbis_total`, `total`), `compute(content)`. `verdict`: `Verdict`, `DgiiMessage(code, text)`, `Outcome(verdict, messages, decided_at)`.
- Produces (rules and life): `types.base`: `Presence`, `Special`, `Indicator`, `nothing_more`, `EVERY_METHOD_BUT_GIFTS`, `BuyerShape(tax_id, foreign_id, name, details=True, country=False, always=False)`, `TypeRules` (`ecf_type`, `sequence_expires`, `income`, `terms`, `deadline`, `payments`, `methods`, `prices_include_itbis`, `buyer`, `billing`, `withholding`, `itbis_withholding`, `discounts`, `reference`, `modifications`, `reason`, `services_only`, `late_credit`, `special`) with `check(content, totals) -> tuple[Problem, ...]`; one module per type, each with its `RULES` (`consumer_invoice.SUMMARY_THRESHOLD` = DOP 250,000; `credit_note.late`). `registry.RULES: Mapping[EcfType, TypeRules]`. `checking`: `form_problems(content)`, `checked(content) -> Totals` (`DocumentInvalidError`). `events`: `DocumentNumbered`, `DocumentAccepted`, `DocumentConditionallyAccepted`, `DocumentRejected`, `SequenceLow`, `SequenceExpiring`, `AnnulmentRequested`, `AnnulmentDecided`, `DocumentEvent`, `FiscalEvent`. `document`: `DocumentState`, `FINAL`, `TRANSITIONS`, `IllegalTransitionError`, `DocumentNotFoundError`, `require_move`, `Signature`, `Draft.numbered(...) -> (FiscalDocument, DocumentNumbered)`, `FiscalDocument` (`signed`, `blocked`, `resumed`, `unreachable`, `sent`, `decided`, `final`). `sequence`: `LOW_SHARE`, `MAX_ANNULLED_RANGES`, `InvalidRangeError`, `SequenceExpiryError` (Decision 13), `SequenceOverlapError`, `NoSequenceError`, `SequenceNotFoundError`, `AnnulmentRangeError`, `NumberRange`, `EncfSequence` (`registered`, `available`, `usable_on`, `take`, `annul`), `require_no_overlap`, `EXPIRY_THRESHOLDS`, `due_expiry_announcement`. `annulment`: `AnnulmentState`, `AnnulmentNotFoundError`, `AnnulmentDecidedError`, `AnnulmentUnavailableError`, `Annulment` (`requested`, `decided`). `profile`: `ProfileMissingError`, `FiscalProfile` (`changed`, `issuer`). Test support `samples.py`: `TENANT`, `POINT`, `SEQUENCE`, `DOCUMENT`, `ISSUED_ON`, `NOW`, `EXPIRES_ON`, `ISSUER`, `CUSTOMER`, `dop`, `line`, `service`, `REFERENCE`, `CONTENTS` (one valid content per type), `draft`, `numbered`, `sample`, `expiry`.

Re-check before starting: `services/*` is a workspace member (Milestone 2 made it one); `Money`'s rounding (`rounded()`, half away from zero, the kernel's) and `TaxId.parse` as Milestone 1 left them.

- [ ] **Step 1: Package and repository settings**

`services/fiscal-do/pyproject.toml`:

```toml
[project]
name = "ledger-fiscal-do"
version = "0.1.0"
description = "The fiscal-do service: Dominican Republic e-CF, from a commercial document to DGII's final answer."
requires-python = ">=3.15"
dependencies = ["ledger-kernel"]

[tool.uv.sources]
ledger-kernel = { workspace = true }

[build-system]
requires = ["uv_build>=0.13.0,<0.14.0"]
build-backend = "uv_build"
```

`services/fiscal-do/src/ledger_fiscal_do/__init__.py`:

```python
"""fiscal-do: the Dominican Republic's electronic fiscal documents (e-CF), from issue to DGII's answer."""
```

`services/fiscal-do/src/ledger_fiscal_do/domain/__init__.py`:

```python
"""fiscal-do's domain: the values, rules and life of an e-CF, standard library and kernel only."""
```

and an empty `py.typed`.

Root `pyproject.toml`, as Milestones 2 and 3 did for their services:

- `[project] dependencies` gains `"ledger-fiscal-do"`; `[tool.uv.sources]` gains `ledger-fiscal-do = { workspace = true }`.
- ruff: `known-first-party` gains `"ledger_fiscal_do"`; `[tool.ruff.lint.isort] known-local-folder` gains `"samples"`, `"fakes"`, `"world"` and `"remotes"` (the tests' plain modules: Tasks 3, 5 and 8).
- basedpyright: an execution environment for the tests' plain modules, before the catch-all one:

```toml
[[tool.basedpyright.executionEnvironments]]
root = "services/fiscal-do/tests"
extraPaths = ["services/fiscal-do/tests"]
```

- pytest: `pythonpath` gains `"services/fiscal-do/tests"` (so that `from samples import ...` works under `--import-mode=importlib`).
- coverage: `source_pkgs` gains `"ledger_fiscal_do"`.
- poe: `mypy-fiscal-do = { cmd = "mypy services/fiscal-do" }` and `deps-fiscal-do = { cmd = "deptry services/fiscal-do/src --config services/fiscal-do/pyproject.toml" }`, each added to its sequence.
- import-linter: `"ledger_fiscal_do"` joins "Production code never imports test tooling", and:

```toml
[[tool.importlinter.contracts]]
name = "fiscal-do is hexagonal"
type = "layers"
containers = ["ledger_fiscal_do"]
layers = ["domain"]

[[tool.importlinter.contracts]]
name = "fiscal-do's domain is standard library and kernel only"
type = "forbidden"
source_modules = ["ledger_fiscal_do.domain"]
forbidden_modules = ["ledger_dgii", "ledger_platform", "ledger_contracts", "lxml", "pydantic", "sqlalchemy", "temporalio", "httpx", "boto3", "fastapi"]
```

(Each later task adds its layer to "fiscal-do is hexagonal"; Task 12 leaves it `["main", "api", "resources", "adapters", "workflows", "settings", "application", "domain"]`.)

Run: `uv lock && uv sync`

- [ ] **Step 2: Write the failing tests**

`services/fiscal-do/tests/test_encf.py`:

```python
import pytest

from ledger_fiscal_do.domain.encf import Encf, EncfNumberError, InvalidEncfError
from ledger_fiscal_do.domain.vocabulary import TITLES, EcfType


def test_an_encf_is_e_its_type_and_ten_digits() -> None:
    encf = Encf(EcfType.CREDIT_INVOICE, 1)
    assert str(encf) == "E310000000001"
    assert Encf.parse("E479999999999") == Encf(EcfType.FOREIGN_PAYMENTS, 9_999_999_999)
    assert Encf.parse(str(encf)) == encf
    assert sorted([Encf(EcfType.DEBIT_NOTE, 2), encf]) == [encf, Encf(EcfType.DEBIT_NOTE, 2)]


@pytest.mark.parametrize(
    "text", ["E310000000", "e310000000001", "E420000000001", "B0100000001", "E31000000000x"]
)
def test_anything_else_is_not_an_encf(text: str) -> None:
    with pytest.raises(InvalidEncfError) as caught:
        Encf.parse(text)
    assert caught.value.code == "invalid_encf"


@pytest.mark.parametrize("number", [0, 10_000_000_000, -1])
def test_a_number_is_what_ten_digits_hold(number: int) -> None:
    with pytest.raises(EncfNumberError):
        Encf(EcfType.CONSUMER_INVOICE, number)


def test_every_type_has_its_printed_name() -> None:
    assert set(TITLES) == set(EcfType)
    assert TITLES[EcfType.EXPORTS] == "Comprobante de Exportaciones Electrónico"
```


`services/fiscal-do/tests/samples.py`:

```python
"""A valid document of every type of e-CF, and the pieces tests build others from.

Every sample passes its type's rules and renders to an XML DGII's schema accepts: ``test_xml`` proves it, and
the golden files under ``golden/`` are these samples rendered.
"""

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Final
from uuid import UUID

from ledger_fiscal_do.domain.checking import checked
from ledger_fiscal_do.domain.content import Buyer, Content, Issuer, Line, Payment, Reference, Withholding
from ledger_fiscal_do.domain.document import Draft
from ledger_fiscal_do.domain.encf import Encf
from ledger_fiscal_do.domain.ids import DocumentId, EmissionPointId, SequenceId
from ledger_fiscal_do.domain.registry import RULES
from ledger_fiscal_do.domain.vocabulary import (
    Billing,
    DgiiEnvironment,
    EcfType,
    IncomeType,
    ItemKind,
    Modification,
    PaymentMethod,
    PaymentTerms,
)
from ledger_kernel.countries.do import TaxId
from ledger_kernel.ids import CompanyId, OrganizationId, Tenant
from ledger_kernel.money import Currency, Money
lazy from ledger_fiscal_do.domain.document import FiscalDocument

TENANT: Final = Tenant(
    OrganizationId(UUID("01890000-0000-7000-8000-000000000001")),
    CompanyId(UUID("01890000-0000-7000-8000-000000000002")),
)
POINT: Final = EmissionPointId(UUID("01890000-0000-7000-8000-000000000003"))
SEQUENCE: Final = SequenceId(UUID("01890000-0000-7000-8000-000000000004"))
DOCUMENT: Final = DocumentId(UUID("01890000-0000-7000-8000-000000000010"))
ISSUED_ON: Final = date(2026, 10, 12)
NOW: Final = datetime(2026, 10, 12, 13, 30, tzinfo=UTC)
EXPIRES_ON: Final = date(2027, 12, 31)
ISSUER: Final = Issuer(
    tax_id=TaxId.parse("101850043"),
    legal_name="FERRETERIA DUARTE SRL",
    trade_name="Ferretería Duarte",
    branch="Principal",
    address="Av. Duarte 123, Santo Domingo",
    municipality="010101",
    province="010000",
    phones=("809-555-0100",),
    email="facturas@duarte.example",
)
CUSTOMER: Final = Buyer(
    tax_id=TaxId.parse("131880738"),
    name="CONSTRUCTORA DEL CARIBE SRL",
    contact="Ana Gómez 809-555-0101",
    email="compras@caribe.example",
    address="C/ 5 #12, Santiago",
    municipality="250101",
    province="250000",
)


def dop(text: str) -> Money:
    """An amount in Dominican pesos."""
    return Money.of(text, Currency.DOP)


def line(
    number: int = 1,
    *,
    billing: Billing = Billing.ITBIS_1,
    quantity: str = "2",
    price: str = "1000.0000",
    discount: Money | None = None,
) -> Line:
    """A line of hammers, taxed at 18 %, unless told otherwise."""
    return Line(
        number=number,
        name=f"Martillo {number}",
        description="Martillo de carpintero, mango de madera",
        kind=ItemKind.GOOD,
        quantity=Decimal(quantity),
        unit=43,
        unit_price=Decimal(price),
        discount=discount,
        billing=billing,
    )


def service(base: Line, withholding: Withholding | None = None) -> Line:
    """``base`` as a service, with what the buyer withholds on it."""
    return replace(base, kind=ItemKind.SERVICE, withholding=withholding)


REFERENCE: Final = Reference(
    modified="E310000000001",
    issued_on=date(2026, 9, 1),
    code=Modification.CORRECTS_AMOUNTS,
    reason="error en precio",
)

CONTENTS: Final[dict[EcfType, Content]] = {
    EcfType.CREDIT_INVOICE: Content(
        ecf_type=EcfType.CREDIT_INVOICE,
        issued_on=ISSUED_ON,
        income=IncomeType.OPERATIONS,
        terms=PaymentTerms.CREDIT,
        deadline=date(2026, 11, 11),
        payments=(Payment(PaymentMethod.CREDIT, dop("3438.87")),),
        buyer=CUSTOMER,
        lines=(
            line(1, discount=dop("100.00")),
            line(2, billing=Billing.ITBIS_2, quantity="1.5", price="400.5000"),
            service(line(3, billing=Billing.EXEMPT, price="250.0000")),
        ),
    ),
    EcfType.CONSUMER_INVOICE: Content(
        ecf_type=EcfType.CONSUMER_INVOICE,
        issued_on=ISSUED_ON,
        income=IncomeType.OPERATIONS,
        terms=PaymentTerms.CASH,
        payments=(Payment(PaymentMethod.CASH, dop("1180.00")), Payment(PaymentMethod.CARD, dop("1180.00"))),
        prices_include_itbis=True,
        lines=(line(1, price="1180.0000"),),
    ),
    EcfType.DEBIT_NOTE: Content(
        ecf_type=EcfType.DEBIT_NOTE,
        issued_on=ISSUED_ON,
        terms=PaymentTerms.CASH,
        buyer=CUSTOMER,
        lines=(service(line(1, quantity="1", price="150.0000")),),
        reference=REFERENCE,
    ),
    EcfType.CREDIT_NOTE: Content(
        ecf_type=EcfType.CREDIT_NOTE,
        issued_on=ISSUED_ON,
        terms=PaymentTerms.CASH,
        buyer=CUSTOMER,
        lines=(line(1, quantity="1", price="500.0000"),),
        reference=REFERENCE,
    ),
    EcfType.PURCHASES: Content(
        ecf_type=EcfType.PURCHASES,
        issued_on=ISSUED_ON,
        terms=PaymentTerms.CASH,
        buyer=Buyer(tax_id=TaxId.parse("00112345678"), name="JUAN PEREZ"),
        lines=(
            service(
                line(1, quantity="1", price="10000.0000"),
                Withholding(itbis=dop("1800.00"), isr=dop("1000.00")),
            ),
        ),
    ),
    EcfType.MINOR_EXPENSES: Content(
        ecf_type=EcfType.MINOR_EXPENSES,
        issued_on=ISSUED_ON,
        lines=(service(line(1, billing=Billing.EXEMPT, quantity="1", price="300.0000")),),
    ),
    EcfType.SPECIAL_REGIMES: Content(
        ecf_type=EcfType.SPECIAL_REGIMES,
        issued_on=ISSUED_ON,
        income=IncomeType.OPERATIONS,
        terms=PaymentTerms.CASH,
        buyer=Buyer(tax_id=TaxId.parse("130000012"), name="ZONA FRANCA SANTIAGO SA"),
        lines=(line(1, billing=Billing.EXEMPT),),
    ),
    EcfType.GOVERNMENT: Content(
        ecf_type=EcfType.GOVERNMENT,
        issued_on=ISSUED_ON,
        income=IncomeType.OPERATIONS,
        terms=PaymentTerms.CREDIT,
        deadline=date(2026, 12, 11),
        buyer=Buyer(tax_id=TaxId.parse("401007551"), name="MINISTERIO DE HACIENDA"),
        lines=(line(1, quantity="10"),),
    ),
    EcfType.EXPORTS: Content(
        ecf_type=EcfType.EXPORTS,
        issued_on=ISSUED_ON,
        income=IncomeType.OPERATIONS,
        terms=PaymentTerms.CASH,
        buyer=Buyer(foreign_id="US-TAX-123456789", name="ACME IMPORTS LLC", country="Estados Unidos"),
        lines=(line(1, billing=Billing.ITBIS_3, quantity="100", price="250.0000"),),
    ),
    EcfType.FOREIGN_PAYMENTS: Content(
        ecf_type=EcfType.FOREIGN_PAYMENTS,
        issued_on=ISSUED_ON,
        buyer=Buyer(foreign_id="ES-B12345678", name="CONSULTORES IBERICOS SL"),
        lines=(
            service(
                line(1, billing=Billing.EXEMPT, quantity="1", price="50000.0000"),
                Withholding(isr=dop("13500.00")),
            ),
        ),
    ),
}


def draft(content: Content, issuer: Issuer = ISSUER) -> Draft:
    """``content`` checked, as a draft of the test company."""
    return Draft(
        id=DOCUMENT,
        tenant=TENANT,
        emission_point=POINT,
        environment=DgiiEnvironment.PRECERTIFICATION,
        issuer=issuer,
        content=content,
        totals=checked(content),
        created_at=NOW,
    )


def numbered(content: Content, number: int = 1, issuer: Issuer = ISSUER) -> FiscalDocument:
    """``content`` checked and numbered ``number`` of its type."""
    document, _ = draft(content, issuer).numbered(
        Encf(content.ecf_type, number), SEQUENCE, EXPIRES_ON, now=NOW
    )
    return document


def sample(ecf_type: EcfType) -> FiscalDocument:
    """The sample of ``ecf_type``, numbered 1."""
    return numbered(CONTENTS[ecf_type])


def expiry(ecf_type: EcfType) -> date | None:
    """When a range of ``ecf_type`` expires in the samples: none for the types DGII leaves undated."""
    return date(2027, 12, 31) if RULES[ecf_type].sequence_expires else None
```


`services/fiscal-do/tests/test_totals.py`:

```python
from dataclasses import replace
from decimal import Decimal
from typing import Final

from hypothesis import given
from hypothesis import strategies as st

from ledger_fiscal_do.domain.content import Content
from ledger_fiscal_do.domain.form import places
from ledger_fiscal_do.domain.totals import TAXED, compute, itbis, line_amount, taxed_base
from ledger_fiscal_do.domain.vocabulary import Billing, EcfType
lazy from ledger_fiscal_do.domain.content import Line
lazy from ledger_kernel.money import Money

from samples import CONTENTS, ISSUED_ON, dop, line

CENT: Final = Decimal("0.01")
prices: Final = st.decimals(min_value=0, max_value=10**7, places=4, allow_nan=False, allow_infinity=False)
quantities: Final = st.decimals(
    min_value=CENT, max_value=10**5, places=2, allow_nan=False, allow_infinity=False
)


@st.composite
def lines(draw: st.DrawFn) -> tuple[Line, ...]:
    count = draw(st.integers(min_value=1, max_value=12))
    drawn: list[Line] = []
    for number in range(1, count + 1):
        price, quantity = draw(prices), draw(quantities)
        gross = (price * quantity).quantize(CENT)
        discount = draw(st.one_of(st.none(), st.decimals(min_value=0, max_value=gross, places=2)))
        billing = draw(st.sampled_from(list(Billing)))
        drawn.append(
            replace(
                line(number, billing=billing),
                unit_price=price,
                quantity=quantity,
                discount=None if discount is None else dop(str(discount)),
            )
        )
    return tuple(drawn)


def content_of(drawn: tuple[Line, ...], *, include_itbis: bool) -> Content:
    return Content(
        ecf_type=EcfType.CONSUMER_INVOICE,
        issued_on=ISSUED_ON,
        lines=drawn,
        prices_include_itbis=include_itbis,
    )


def test_the_totals_of_a_document_are_dgii_s() -> None:
    totals = compute(CONTENTS[EcfType.CREDIT_INVOICE])
    assert [str(amount.amount) for amount in totals.line_amounts] == ["1900.00", "600.75", "500.00"]
    one, two = totals.group(Billing.ITBIS_1), totals.group(Billing.ITBIS_2)
    assert one is not None
    assert two is not None
    assert (one.rate, one.base, one.itbis) == (18, dop("1900.00"), dop("342.00"))
    assert (two.rate, two.base, two.itbis) == (16, dop("600.75"), dop("96.12"))
    assert totals.group(Billing.ITBIS_3) is None
    assert (totals.taxed_total, totals.exempt, totals.itbis_total) == (
        dop("2500.75"),
        dop("500.00"),
        dop("438.12"),
    )
    assert totals.total == dop("3438.87")
    assert (totals.not_billable, totals.itbis_withheld, totals.isr_withheld) == (None, None, None)


def test_amounts_that_include_itbis_have_it_taken_out() -> None:
    totals = compute(CONTENTS[EcfType.CONSUMER_INVOICE])
    group = totals.group(Billing.ITBIS_1)
    assert group is not None
    assert (group.base, group.itbis, totals.total) == (dop("2000.00"), dop("360.00"), dop("2360.00"))


def test_rounding_is_half_up_at_the_third_decimal_only_where_dgii_rounds() -> None:
    assert line_amount(replace(line(), unit_price=Decimal("1.0050"), quantity=Decimal(1))) == dop("1.01")
    assert line_amount(replace(line(), unit_price=Decimal("0.3333"), quantity=Decimal(3))) == dop("1.00")
    assert itbis(dop("100.03"), 18) == dop("18.01")
    assert itbis(dop("100.02"), 18) == dop("18.00")
    assert taxed_base(dop("100.00"), 18, includes_itbis=True) == dop("84.75")
    assert taxed_base(dop("100.00"), 0, includes_itbis=True) == dop("100.00")


def test_withholdings_and_unbillable_lines_are_summed_apart() -> None:
    totals = compute(CONTENTS[EcfType.PURCHASES])
    assert (totals.itbis_withheld, totals.isr_withheld) == (dop("1800.00"), dop("1000.00"))
    unbillable = replace(CONTENTS[EcfType.CREDIT_INVOICE], lines=(line(1, billing=Billing.NOT_BILLABLE),))
    totals = compute(unbillable)
    assert (totals.not_billable, totals.total, totals.taxed_total) == (dop("2000.00"), dop("0"), None)


@given(drawn=lines(), include_itbis=st.booleans())
def test_the_total_is_exactly_the_sum_of_its_parts(*, drawn: tuple[Line, ...], include_itbis: bool) -> None:
    totals = compute(content_of(drawn, include_itbis=include_itbis))
    parts = [part for part in (totals.taxed_total, totals.exempt, totals.itbis_total) if part is not None]
    total = sum((part.amount for part in parts), Decimal(0))
    assert totals.total.amount == total
    amounts: list[Money] = [
        *totals.line_amounts,
        totals.total,
        *(g.base for g in totals.taxed),
        *(g.itbis for g in totals.taxed),
    ]
    assert all(places(amount.amount) <= 2 and amount.amount >= 0 for amount in amounts)


@given(drawn=lines())
def test_a_line_s_amount_is_its_exact_amount_to_the_cent(*, drawn: tuple[Line, ...]) -> None:
    for each in drawn:
        exact = each.unit_price * each.quantity - (
            Decimal(0) if each.discount is None else each.discount.amount
        )
        assert abs(line_amount(each).amount - exact) <= Decimal("0.005")


@given(drawn=lines())
def test_taxed_bases_without_itbis_in_the_prices_are_the_lines_sums(*, drawn: tuple[Line, ...]) -> None:
    totals = compute(content_of(drawn, include_itbis=False))
    for billing in TAXED:
        group = totals.group(billing)
        amounts = [
            a.amount for each, a in zip(drawn, totals.line_amounts, strict=True) if each.billing is billing
        ]
        assert (group is None) == (not amounts)
        assert group is None or group.base.amount == sum(amounts, Decimal(0))


@given(lines())
def test_itbis_taken_out_of_prices_and_put_back_gives_the_lines_sums_within_two_cents(
    drawn: tuple[Line, ...],
) -> None:
    totals = compute(content_of(drawn, include_itbis=True))
    for group in totals.taxed:
        amounts = [
            a.amount
            for each, a in zip(drawn, totals.line_amounts, strict=True)
            if each.billing is group.billing
        ]
        assert abs(group.base.amount + group.itbis.amount - sum(amounts, Decimal(0))) <= Decimal("0.02")


@given(drawn=lines(), include_itbis=st.booleans())
def test_the_order_of_the_lines_changes_no_total(*, drawn: tuple[Line, ...], include_itbis: bool) -> None:
    reordered = tuple(replace(each, number=index + 1) for index, each in enumerate(reversed(drawn)))
    forward = compute(content_of(drawn, include_itbis=include_itbis))
    backward = compute(content_of(reordered, include_itbis=include_itbis))
    assert (forward.taxed, forward.exempt, forward.total) == (backward.taxed, backward.exempt, backward.total)
```


`services/fiscal-do/tests/test_checking.py`:

```python
from dataclasses import replace
from datetime import date
from decimal import Decimal
from typing import Final

import pytest

from ledger_fiscal_do.domain.checking import checked
from ledger_fiscal_do.domain.content import Buyer, Payment, Withholding
from ledger_fiscal_do.domain.problems import MAX_PROBLEMS, DocumentInvalidError, Problem, ProblemCode
from ledger_fiscal_do.domain.registry import RULES
from ledger_fiscal_do.domain.types.base import Presence
from ledger_fiscal_do.domain.vocabulary import EcfType, IncomeType, ItemKind, Modification, PaymentMethod
from ledger_kernel.countries.do import TaxId
from ledger_kernel.money import Currency, Money
lazy from ledger_fiscal_do.domain.content import Content

from samples import CONTENTS, CUSTOMER, REFERENCE, dop, line

CREDIT: Final = CONTENTS[EcfType.CREDIT_INVOICE]
FOREIGN: Final = Buyer(tax_id=TaxId.parse("131880738"), address="Somewhere")


def problems_of(content: Content) -> set[Problem]:
    with pytest.raises(DocumentInvalidError) as caught:
        checked(content)
    assert caught.value.code == "ecf_invalid"
    return set(caught.value.problems)


def found(content: Content) -> set[tuple[str, str]]:
    return {(problem.field, problem.code.value) for problem in problems_of(content)}


@pytest.mark.parametrize("ecf_type", list(EcfType))
def test_every_type_has_its_rules_and_its_sample_passes_them(ecf_type: EcfType) -> None:
    assert RULES[ecf_type].ecf_type is ecf_type
    assert checked(CONTENTS[ecf_type]).total.amount > 0


def test_the_form_of_every_value_is_checked_at_once() -> None:
    broken = replace(
        CREDIT,
        lines=(
            replace(line(1), name=" ", quantity=Decimal(0), unit_price=Decimal("1.00001")),
            replace(line(5), description="x" * 1001, discount=Money.of("1", Currency.USD)),
            replace(line(3), name="tab\there", unit_price=Decimal(-1)),
        ),
        payments=(Payment(PaymentMethod.CASH, dop("1.001")),),
        buyer=replace(CUSTOMER, municipality="1234", email="x" * 81),
        reference=replace(REFERENCE, modified="E31"),
    )
    assert problems_of(broken) == {
        Problem("lines[0].name", ProblemCode.TEXT_BLANK),
        Problem("lines[0].quantity", ProblemCode.QUANTITY_NOT_POSITIVE),
        Problem("lines[0].unit_price", ProblemCode.AMOUNT_TOO_PRECISE),
        Problem("lines[1].number", ProblemCode.LINES_NOT_NUMBERED),
        Problem("lines[1].description", ProblemCode.TEXT_TOO_LONG),
        Problem("lines[1].discount", ProblemCode.WRONG_CURRENCY),
        Problem("lines[2].name", ProblemCode.TEXT_CONTROL_CHARACTERS),
        Problem("lines[2].unit_price", ProblemCode.AMOUNT_NEGATIVE),
        Problem("payments[0].amount", ProblemCode.AMOUNT_TOO_PRECISE),
        Problem("buyer.municipality", ProblemCode.CODE_MALFORMED),
        Problem("buyer.email", ProblemCode.TEXT_TOO_LONG),
        Problem("reference.modified", ProblemCode.CODE_MALFORMED),
    }


def test_amounts_beyond_dgii_s_fields_are_refused() -> None:
    huge = replace(line(), unit_price=Decimal(9999999999999999), quantity=Decimal(2))
    over = replace(line(), discount=dop("2000.01"))
    large = replace(line(), unit_price=Decimal(10) ** 16)
    assert found(replace(CREDIT, lines=(huge,))) == {("lines[0]", "amount_too_large")}
    assert found(replace(CREDIT, lines=(over,))) == {("lines[0].discount", "discount_exceeds_amount")}
    assert found(replace(CREDIT, lines=(large,))) == {("lines[0].unit_price", "amount_too_large")}


def test_lines_and_payments_are_counted() -> None:
    assert found(replace(CREDIT, lines=())) == {("lines", "lines_empty")}
    too_many = tuple(line(number) for number in range(1, 1002))
    assert found(replace(CREDIT, lines=too_many)) == {("lines", "too_many")}
    payments = tuple(Payment(PaymentMethod.CASH, dop("1.00")) for _ in range(8))
    assert found(replace(CREDIT, payments=payments)) == {("payments", "too_many")}


def test_a_refusal_lists_at_most_twenty_problems_and_counts_the_rest() -> None:
    lines = tuple(replace(line(number), name=" ") for number in range(1, 31))
    with pytest.raises(DocumentInvalidError) as caught:
        checked(replace(CREDIT, lines=lines))
    assert (len(caught.value.problems), caught.value.omitted) == (MAX_PROBLEMS, 10)
    assert "and 10 more" in str(caught.value)


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (replace(CREDIT, income=None, terms=None), {("income", "required"), ("terms", "required")}),
        (replace(CONTENTS[EcfType.PURCHASES], income=IncomeType.OPERATIONS), {("income", "not_allowed")}),
        (replace(CREDIT, deadline=None), {("deadline", "required")}),
        (replace(CREDIT, deadline=date(2026, 1, 1)), {("deadline", "date_before_issue")}),
        (
            replace(CONTENTS[EcfType.MINOR_EXPENSES], deadline=date(2026, 12, 1)),
            {("deadline", "not_allowed")},
        ),
        (
            replace(CONTENTS[EcfType.CREDIT_NOTE], payments=(Payment(PaymentMethod.CASH, dop("1.00")),)),
            {("payments", "not_allowed")},
        ),
        (
            replace(CREDIT, payments=(Payment(PaymentMethod.GIFT_VOUCHERS, dop("1.00")),)),
            {("payments[0].method", "method_not_allowed")},
        ),
        (
            replace(CONTENTS[EcfType.SPECIAL_REGIMES], prices_include_itbis=True),
            {("prices_include_itbis", "not_allowed")},
        ),
        (replace(CONTENTS[EcfType.MINOR_EXPENSES], buyer=CUSTOMER), {("buyer", "not_allowed")}),
        (replace(CREDIT, buyer=None), {("buyer", "required")}),
        (replace(CREDIT, buyer=Buyer(name="X")), {("buyer.tax_id", "required")}),
        (
            replace(
                CONTENTS[EcfType.CONSUMER_INVOICE], buyer=replace(FOREIGN, address=None, foreign_id="P1")
            ),
            {("buyer", "buyer_identified_twice")},
        ),
        (
            replace(CONTENTS[EcfType.FOREIGN_PAYMENTS], buyer=FOREIGN),
            {("buyer.tax_id", "not_allowed"), ("buyer.address", "not_allowed")},
        ),
        (replace(CREDIT, buyer=replace(CUSTOMER, country="Haiti")), {("buyer.country", "not_allowed")}),
        (replace(CONTENTS[EcfType.DEBIT_NOTE], reference=None), {("reference", "required")}),
        (
            replace(CREDIT, reference=replace(REFERENCE, code=Modification.ANNULS, reason=None)),
            {("reference.code", "modification_not_allowed")},
        ),
        (
            replace(CREDIT, reference=replace(REFERENCE, code=Modification.REPLACES_CONTINGENCY_NCF)),
            {("reference.reason", "not_allowed")},
        ),
    ],
)
def test_each_type_demands_and_refuses_what_its_format_says(
    content: Content, expected: set[tuple[str, str]]
) -> None:
    assert found(content) == expected


def test_lines_are_taxed_and_withheld_as_the_type_allows() -> None:
    exports = replace(CONTENTS[EcfType.EXPORTS], lines=(line(1, discount=dop("1.00")),))
    assert found(exports) == {("lines[0].billing", "billing_not_allowed")}
    abroad = CONTENTS[EcfType.FOREIGN_PAYMENTS]
    goods = replace(
        abroad.lines[0], kind=ItemKind.GOOD, discount=dop("1.00"), withholding=Withholding(itbis=dop("1.00"))
    )
    assert found(replace(abroad, lines=(goods,))) == {
        ("lines[0].kind", "service_required"),
        ("lines[0].discount", "not_allowed"),
        ("lines[0].withholding.itbis", "not_allowed"),
        ("lines[0].withholding.isr", "required"),
    }
    purchase = CONTENTS[EcfType.PURCHASES]
    unwithheld = replace(purchase.lines[0], withholding=None)
    perceived = replace(purchase.lines[0], withholding=Withholding(perception=True))
    assert found(replace(purchase, lines=(unwithheld,))) == {("lines[0].withholding", "required")}
    assert found(replace(purchase, lines=(perceived,))) == {
        ("lines[0].withholding", "perception_not_in_force")
    }
    consumer = CONTENTS[EcfType.CONSUMER_INVOICE]
    withheld = replace(consumer.lines[0], withholding=Withholding(isr=dop("1.00")))
    assert found(replace(consumer, lines=(withheld,))) == {("lines[0].withholding", "not_allowed")}


def test_a_consumer_invoice_from_dop_250_000_names_its_buyer() -> None:
    consumer = CONTENTS[EcfType.CONSUMER_INVOICE]
    big = replace(consumer, lines=(line(1, quantity="250", price="1000.0000"),), payments=())
    assert found(big) == {("buyer", "buyer_identification_required"), ("buyer.name", "required")}
    assert found(replace(big, buyer=Buyer(name="JUAN"))) == {("buyer", "buyer_identification_required")}
    assert checked(replace(big, buyer=Buyer(foreign_id="PAS-123", name="JANE ROE"))).total == dop("250000.00")
    shape = RULES[EcfType.CONSUMER_INVOICE].buyer
    assert shape is not None
    assert (shape.name, shape.always) == (Presence.OPTIONAL, True)


def test_a_note_that_corrects_text_carries_no_amounts() -> None:
    note = replace(
        CONTENTS[EcfType.CREDIT_NOTE], reference=replace(REFERENCE, code=Modification.CORRECTS_TEXT)
    )
    assert found(note) == {("lines[0]", "text_correction_has_amounts")}
    assert checked(replace(note, lines=(line(1, price="0.0000"),))).total.amount == 0


def test_a_credit_note_says_whether_it_comes_late() -> None:
    late = RULES[EcfType.CREDIT_NOTE].late_credit
    assert late is not None
    note = CONTENTS[EcfType.CREDIT_NOTE]
    assert late(note)
    assert not late(replace(note, reference=replace(REFERENCE, issued_on=date(2026, 9, 12))))
    assert not late(replace(note, reference=None))
    assert RULES[EcfType.CREDIT_INVOICE].late_credit is None
```


`services/fiscal-do/tests/test_document.py`:

```python
from dataclasses import replace
from datetime import timedelta
from itertools import product
from typing import Final

import pytest

from ledger_fiscal_do.domain.document import (
    FINAL,
    TRANSITIONS,
    DocumentNotFoundError,
    DocumentState,
    IllegalTransitionError,
    Signature,
    require_move,
)
from ledger_fiscal_do.domain.events import (
    DocumentAccepted,
    DocumentConditionallyAccepted,
    DocumentNumbered,
    DocumentRejected,
)
from ledger_fiscal_do.domain.ids import TrackId
from ledger_fiscal_do.domain.verdict import DgiiMessage, Outcome, Verdict
from ledger_fiscal_do.domain.vocabulary import EcfType
lazy from ledger_fiscal_do.domain.document import FiscalDocument

from samples import NOW, SEQUENCE, TENANT, draft, sample

LATER: Final = NOW + timedelta(minutes=1)
SIGNATURE: Final = Signature(
    signed_at=NOW, security_code="dcp79q", archive_key="k", sha256="0" * 64, summary=False
)
MESSAGES: Final = (DgiiMessage("1", "Monto total no cuadra"),)


def document() -> FiscalDocument:
    return sample(EcfType.CREDIT_INVOICE)


def test_a_draft_is_numbered_and_announced() -> None:
    first = draft(sample(EcfType.CREDIT_INVOICE).content)
    assert first.state is DocumentState.DRAFT
    numbered, event = first.numbered(document().encf, SEQUENCE, None, now=LATER)
    assert (numbered.state, numbered.version, numbered.updated_at, numbered.created_at) == (
        DocumentState.NUMBERED,
        1,
        LATER,
        NOW,
    )
    assert event == DocumentNumbered(
        tenant=TENANT,
        document=first.id,
        encf=numbered.encf,
        version=1,
        total=numbered.totals.total,
        environment=numbered.environment,
    )


def test_the_way_to_acceptance() -> None:
    signed = document().signed(SIGNATURE, now=LATER)
    sent = signed.sent(TrackId("track-1"), now=LATER)
    accepted, event = sent.decided(Outcome(verdict=Verdict.ACCEPTED, decided_at=LATER), now=LATER)
    assert (signed.signature, sent.track_id, accepted.state, accepted.version) == (
        SIGNATURE,
        "track-1",
        DocumentState.ACCEPTED,
        4,
    )
    assert accepted.final
    assert event == DocumentAccepted(tenant=TENANT, document=accepted.id, encf=accepted.encf, version=4)


def test_a_conditional_acceptance_and_a_rejection_keep_dgii_s_words() -> None:
    sent = document().signed(SIGNATURE, now=LATER).sent(TrackId("t"), now=LATER)
    conditional, kept = sent.decided(
        Outcome(verdict=Verdict.ACCEPTED_CONDITIONALLY, messages=MESSAGES, decided_at=LATER), now=LATER
    )
    rejected, refused = sent.decided(
        Outcome(verdict=Verdict.REJECTED, messages=MESSAGES, decided_at=LATER), now=LATER
    )
    assert isinstance(kept, DocumentConditionallyAccepted)
    assert isinstance(refused, DocumentRejected)
    assert kept.messages == refused.messages == MESSAGES
    assert (conditional.state, rejected.state) == (
        DocumentState.ACCEPTED_CONDITIONALLY,
        DocumentState.REJECTED,
    )


def test_contingency_lasts_from_the_first_failure_until_dgii_holds_it() -> None:
    signed = document().signed(SIGNATURE, now=NOW)
    unreachable = signed.unreachable(now=NOW)
    again = unreachable.unreachable(now=LATER)
    assert again is unreachable
    assert (again.state, again.contingency_since) == (DocumentState.CONTINGENCY, NOW)
    assert again.sent(TrackId("t"), now=LATER).state is DocumentState.SENT


def test_a_blocked_document_resumes_where_it_stood() -> None:
    numbered = document()
    blocked = numbered.blocked(now=LATER)
    assert blocked.blocked(now=LATER) is blocked
    assert (blocked.state, blocked.blocked_from) == (
        DocumentState.BLOCKED_CERTIFICATE,
        DocumentState.NUMBERED,
    )
    assert blocked.resumed(now=LATER).state is DocumentState.NUMBERED
    assert numbered.resumed(now=LATER) is numbered
    signed = numbered.signed(SIGNATURE, now=LATER).blocked(now=LATER)
    assert signed.resumed(now=LATER).state is DocumentState.SIGNED
    assert blocked.signed(SIGNATURE, now=LATER).blocked_from is None


@pytest.mark.parametrize(("current", "target"), list(product(DocumentState, DocumentState)))
def test_every_move_outside_the_machine_is_impossible(current: DocumentState, target: DocumentState) -> None:
    if target in TRANSITIONS[current]:
        require_move(current, target)
        return
    with pytest.raises(IllegalTransitionError) as caught:
        require_move(current, target)
    assert (caught.value.kind.value, caught.value.code) == ("conflict", "illegal_transition")


def test_a_final_document_moves_no_more() -> None:
    assert all(not TRANSITIONS[state] for state in FINAL)
    accepted = replace(document(), state=DocumentState.ACCEPTED)
    with pytest.raises(IllegalTransitionError, match="accepted cannot become sent"):
        accepted.sent(None, now=LATER)
    assert str(DocumentNotFoundError("E310000000001")) == "document E310000000001 was not found"
```


`services/fiscal-do/tests/test_sequence.py`:

```python
from dataclasses import replace
from datetime import date
from typing import Final
from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ledger_fiscal_do.domain.annulment import (
    Annulment,
    AnnulmentDecidedError,
    AnnulmentNotFoundError,
    AnnulmentState,
    AnnulmentUnavailableError,
)
from ledger_fiscal_do.domain.encf import Encf
from ledger_fiscal_do.domain.events import AnnulmentDecided, AnnulmentRequested, SequenceLow
from ledger_fiscal_do.domain.ids import AnnulmentId, SequenceId
from ledger_fiscal_do.domain.sequence import (
    AnnulmentRangeError,
    EncfSequence,
    InvalidRangeError,
    NoSequenceError,
    NumberRange,
    SequenceNotFoundError,
    SequenceOverlapError,
    due_expiry_announcement,
    require_no_overlap,
)
from ledger_fiscal_do.domain.verdict import DgiiMessage, Outcome, Verdict
from ledger_fiscal_do.domain.vocabulary import DgiiEnvironment, EcfType

from samples import ISSUER, NOW, POINT, SEQUENCE, TENANT

TODAY: Final = date(2026, 10, 12)
EXPIRY: Final = date(2027, 12, 31)
ANNULMENT: Final = AnnulmentId(UUID("01890000-0000-7000-8000-000000000020"))


def sequence(first: int = 1, last: int = 10, expires_on: date | None = EXPIRY) -> EncfSequence:
    return EncfSequence.registered(
        (SEQUENCE, TENANT, POINT),
        ecf_type=EcfType.CREDIT_INVOICE,
        numbers=NumberRange(first, last),
        expires_on=expires_on,
        now=NOW,
    )


def taken(current: EncfSequence, count: int) -> tuple[EncfSequence, list[int], list[SequenceLow]]:
    numbers: list[int] = []
    lows: list[SequenceLow] = []
    for _ in range(count):
        current, encf, low = current.take(on=TODAY)
        numbers.append(encf.number)
        lows.extend([low] if low is not None else [])
    return current, numbers, lows


def test_numbers_are_taken_in_order_and_running_low_is_said_once() -> None:
    after, numbers, lows = taken(sequence(), 10)
    assert numbers == list(range(1, 11))
    assert lows == [
        SequenceLow(tenant=TENANT, sequence=SEQUENCE, ecf_type=EcfType.CREDIT_INVOICE, remaining=2, version=9)
    ]
    assert (after.available, after.usable_on(TODAY), after.version) == (0, False, 11)
    with pytest.raises(NoSequenceError, match="type 31"):
        after.take(on=TODAY)


def test_an_expired_range_gives_no_number() -> None:
    expiring = sequence(expires_on=TODAY)
    assert expiring.usable_on(TODAY)
    assert not expiring.usable_on(date(2026, 10, 13))
    assert sequence(expires_on=None).usable_on(date(2099, 1, 1))


def test_unused_numbers_are_withdrawn_and_skipped() -> None:
    current, _, _ = taken(sequence(), 2)
    middle = current.annul(NumberRange(4, 5))
    assert middle.annulled == (NumberRange(4, 5),)
    assert middle.available == 6
    after, numbers, _ = taken(middle, 6)
    assert numbers == [3, 6, 7, 8, 9, 10]
    assert after.available == 0
    next_one = current.annul(NumberRange(3, 3))
    assert next_one.next == 4


@pytest.mark.parametrize("withdrawn", [NumberRange(1, 3), NumberRange(9, 11), NumberRange(5, 6)])
def test_only_numbers_never_taken_nor_withdrawn_can_be_withdrawn(withdrawn: NumberRange) -> None:
    current, _, _ = taken(sequence(), 2)
    current = current.annul(NumberRange(6, 6))
    with pytest.raises(AnnulmentRangeError):
        current.annul(withdrawn)


def test_withdrawals_are_bounded() -> None:
    current = sequence(1, 1000)
    for number in range(500, 600):
        current = current.annul(NumberRange(number, number))
    with pytest.raises(AnnulmentRangeError):
        current.annul(NumberRange(700, 700))


@given(st.lists(st.integers(min_value=1, max_value=40), max_size=6), st.integers(min_value=0, max_value=60))
def test_no_number_is_taken_twice_nor_withdrawn_and_taken(withdraw: list[int], takes: int) -> None:
    current = sequence(1, 40)
    withdrawn: set[int] = set()
    for number in withdraw:
        try:
            current = current.annul(NumberRange(number, number))
        except AnnulmentRangeError:
            continue
        withdrawn.add(number)
    numbers: list[int] = []
    while takes and current.available:
        current, encf, _ = current.take(on=TODAY)
        numbers.append(encf.number)
        takes -= 1
    assert numbers == sorted(set(numbers))
    assert not withdrawn & set(numbers)
    assert numbers == [n for n in range(1, 41) if n not in withdrawn][: len(numbers)]


def test_a_range_never_overlaps_another_of_its_type() -> None:
    registered = (sequence(1, 10), sequence(21, 30))
    require_no_overlap(NumberRange(11, 20), registered)
    with pytest.raises(SequenceOverlapError):
        require_no_overlap(NumberRange(10, 11), registered)
    with pytest.raises(InvalidRangeError):
        NumberRange(5, 4)
    assert str(SequenceNotFoundError(SEQUENCE)).startswith("sequence ")


@pytest.mark.parametrize(
    ("days_left", "announced", "due"),
    [(31, None, None), (30, None, 30), (5, None, 7), (5, 7, None), (0, 7, 1), (-3, None, 1), (-3, 1, None)],
)
def test_expiry_is_announced_once_per_threshold(
    days_left: int, announced: int | None, due: int | None
) -> None:
    expires_on = date.fromordinal(TODAY.toordinal() + days_left)
    assert due_expiry_announcement(expires_on, TODAY, announced) == due


def test_an_annulment_withdraws_its_numbers_and_records_dgii_s_answer() -> None:
    current, _, _ = taken(sequence(), 2)
    with pytest.raises(AnnulmentUnavailableError):
        Annulment.requested(
            ANNULMENT,
            current,
            NumberRange(5, 10),
            issuer=(ISSUER.tax_id, DgiiEnvironment.CERTIFICATION),
            now=NOW,
        )
    annulment, withdrawn, requested = Annulment.requested(
        ANNULMENT, current, NumberRange(5, 10), issuer=(ISSUER.tax_id, DgiiEnvironment.PRODUCTION), now=NOW
    )
    assert (annulment.state, withdrawn.available) == (AnnulmentState.REQUESTED, 2)
    assert requested == AnnulmentRequested(
        tenant=TENANT,
        annulment=ANNULMENT,
        sequence=SEQUENCE,
        first=Encf(EcfType.CREDIT_INVOICE, 5),
        last=Encf(EcfType.CREDIT_INVOICE, 10),
    )
    accepted, decided = annulment.decided(Outcome(verdict=Verdict.ACCEPTED_CONDITIONALLY, decided_at=NOW))
    assert accepted.state is AnnulmentState.ACCEPTED
    assert decided == AnnulmentDecided(
        tenant=TENANT, annulment=ANNULMENT, verdict=Verdict.ACCEPTED_CONDITIONALLY, messages=()
    )
    with pytest.raises(AnnulmentDecidedError):
        accepted.decided(Outcome(verdict=Verdict.REJECTED, decided_at=NOW))
    refused = (DgiiMessage("2", "Las secuencias que está intentando anular han sido utilizadas"),)
    rejected, _ = annulment.decided(Outcome(verdict=Verdict.REJECTED, messages=refused, decided_at=NOW))
    assert (rejected.state, rejected.outcome) == (
        AnnulmentState.REJECTED,
        Outcome(verdict=Verdict.REJECTED, messages=refused, decided_at=NOW),
    )
    reopened, _ = replace(rejected, state=AnnulmentState.REQUESTED).decided(
        Outcome(verdict=Verdict.ACCEPTED, decided_at=NOW)
    )
    assert reopened.state is AnnulmentState.ACCEPTED
    assert "was not found" in str(AnnulmentNotFoundError(SequenceId(ANNULMENT)))
```


`services/fiscal-do/tests/test_profile.py`:

```python
from dataclasses import replace
from typing import Final

import pytest

from ledger_fiscal_do.domain.problems import Problem, ProblemCode, ProfileInvalidError
from ledger_fiscal_do.domain.profile import FiscalProfile, ProfileMissingError
from ledger_fiscal_do.domain.vocabulary import DgiiEnvironment
from ledger_kernel.countries.do import TaxId

from samples import ISSUER, NOW, TENANT

PROFILE: Final = FiscalProfile(
    tenant=TENANT,
    environment=DgiiEnvironment.PRECERTIFICATION,
    address="Av. Duarte 123, Santo Domingo",
    trade_name="Ferretería Duarte",
    branch="Principal",
    municipality="010101",
    province="010000",
    phones=("809-555-0100",),
    email="facturas@duarte.example",
    updated_at=NOW,
)


def test_a_profile_names_the_issuer_with_identity_s_rnc_and_name() -> None:
    assert PROFILE.issuer(TaxId.parse("101850043"), "FERRETERIA DUARTE SRL") == ISSUER
    assert PROFILE.issuer(TaxId.parse("101850043"), "X" * 200).legal_name == "X" * 150


def test_a_changed_profile_is_the_next_version_of_the_same_company() -> None:
    moved = replace(PROFILE, environment=DgiiEnvironment.PRODUCTION, phones=(), version=9)
    changed = PROFILE.changed(moved)
    assert (changed.tenant, changed.environment, changed.phones, changed.version) == (
        TENANT,
        DgiiEnvironment.PRODUCTION,
        (),
        2,
    )


def test_what_dgii_s_fields_cannot_carry_is_refused_at_once() -> None:
    with pytest.raises(ProfileInvalidError) as caught:
        replace(PROFILE, address="", branch="B" * 21, phones=("8095550100", "1", "2", "3"), municipality="x")
    assert set(caught.value.problems) == {
        Problem("address", ProblemCode.TEXT_BLANK),
        Problem("branch", ProblemCode.TEXT_TOO_LONG),
        Problem("municipality", ProblemCode.CODE_MALFORMED),
        Problem("phones", ProblemCode.TOO_MANY),
        Problem("phones[0]", ProblemCode.CODE_MALFORMED),
        Problem("phones[1]", ProblemCode.CODE_MALFORMED),
        Problem("phones[2]", ProblemCode.CODE_MALFORMED),
        Problem("phones[3]", ProblemCode.CODE_MALFORMED),
    }
    assert caught.value.code == "fiscal_profile_invalid"
    assert ProfileMissingError().code == "fiscal_profile_missing"
```


- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest services/fiscal-do/tests -q --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'ledger_fiscal_do.domain'`.

- [ ] **Step 4: Implement the values of an e-CF**

`services/fiscal-do/src/ledger_fiscal_do/domain/vocabulary.py`:

```python
"""DGII's codes, named: the types of e-CF and every enumerated value fiscal-do writes into one.

Each value is the code DGII's *Formato Comprobante Fiscal Electrónico (e-CF) v1.0* (October 2025) defines;
the names are ours, in English, so that no other module spells a bare DGII number.
"""

from enum import IntEnum, StrEnum
from typing import Final
lazy from collections.abc import Mapping


class EcfType(IntEnum):
    """The ten types of e-CF (``TipoeCF``), named as the official XSDs' own comments name them."""

    CREDIT_INVOICE = 31
    """Factura de Crédito Fiscal Electrónica."""
    CONSUMER_INVOICE = 32
    """Factura de Consumo Electrónica."""
    DEBIT_NOTE = 33
    """Nota de Débito Electrónica."""
    CREDIT_NOTE = 34
    """Nota de Crédito Electrónica."""
    PURCHASES = 41
    """Compras Electrónico."""
    MINOR_EXPENSES = 43
    """Gastos Menores Electrónico."""
    SPECIAL_REGIMES = 44
    """Regímenes Especiales Electrónico."""
    GOVERNMENT = 45
    """Gubernamental Electrónico."""
    EXPORTS = 46
    """Comprobante de Exportaciones Electrónico."""
    FOREIGN_PAYMENTS = 47
    """Comprobante para Pagos al Exterior Electrónico."""


TITLES: Final[Mapping[EcfType, str]] = {
    EcfType.CREDIT_INVOICE: "Factura de Crédito Fiscal Electrónica",
    EcfType.CONSUMER_INVOICE: "Factura de Consumo Electrónica",
    EcfType.DEBIT_NOTE: "Nota de Débito Electrónica",
    EcfType.CREDIT_NOTE: "Nota de Crédito Electrónica",
    EcfType.PURCHASES: "Compras Electrónico",
    EcfType.MINOR_EXPENSES: "Gastos Menores Electrónico",
    EcfType.SPECIAL_REGIMES: "Regímenes Especiales Electrónico",
    EcfType.GOVERNMENT: "Gubernamental Electrónico",
    EcfType.EXPORTS: "Comprobante de Exportaciones Electrónico",
    EcfType.FOREIGN_PAYMENTS: "Comprobante para Pagos al Exterior Electrónico",
}
"""Each type's name, as DGII prints it (the printed representation shows the type in words)."""


class IncomeType(StrEnum):
    """``TipoIngresos``: what kind of income the document records."""

    OPERATIONS = "01"
    FINANCIAL = "02"
    EXTRAORDINARY = "03"
    LEASES = "04"
    DEPRECIABLE_ASSET_SALE = "05"
    OTHER = "06"


class PaymentTerms(IntEnum):
    """``TipoPago``: cash, credit, or free (a free delivery is not valid for tax credit)."""

    CASH = 1
    CREDIT = 2
    FREE = 3


class PaymentMethod(IntEnum):
    """``FormaPago``: how the document is paid."""

    CASH = 1
    CHECK_TRANSFER_DEPOSIT = 2
    CARD = 3
    CREDIT = 4
    GIFT_VOUCHERS = 5
    """Gift vouchers or certificates: on a consumer invoice (type 32) only."""
    BARTER = 6
    CREDIT_NOTE = 7
    OTHER = 8


class Billing(IntEnum):
    """``IndicadorFacturacion``: how a line is taxed with ITBIS."""

    NOT_BILLABLE = 0
    ITBIS_1 = 1
    """Rate 1, 18 %."""
    ITBIS_2 = 2
    """Rate 2, 16 %."""
    ITBIS_3 = 3
    """Rate 3, 0 % (exports)."""
    EXEMPT = 4


class ItemKind(IntEnum):
    """``IndicadorBienoServicio``."""

    GOOD = 1
    SERVICE = 2


class Modification(IntEnum):
    """Why a document refers to another: the e-CF's modification code."""

    ANNULS = 1
    CORRECTS_TEXT = 2
    CORRECTS_AMOUNTS = 3
    REPLACES_CONTINGENCY_NCF = 4
    """Replaces a paper NCF issued during a contingency."""
    REFERENCES_CONSUMER_INVOICE = 5
    """A credit invoice (type 31) that refers to a consumer invoice."""


class DgiiEnvironment(StrEnum):
    """DGII's environments, each its own path segment in DGII's URLs: a company is in exactly one."""

    PRECERTIFICATION = "testecf"
    """TesteCF: pre-certification, for integration tests (documents kept 60 days)."""
    CERTIFICATION = "certecf"
    """CerteCF: the certification tests a company passes before production."""
    PRODUCTION = "ecf"
    """eCF: every document has fiscal effect."""


OFFICIAL_ECF: Final = "https://ecf.dgii.gov.do"
"""DGII's host for its services to emitters (*Descripción Técnica Servicios DGII*, January 2026)."""
OFFICIAL_FC: Final = "https://fc.dgii.gov.do"
"""DGII's host for consumer-invoice summaries."""
```


`services/fiscal-do/src/ledger_fiscal_do/domain/ids.py`:

```python
"""Identifiers of fiscal-do's own entities; organizations and companies use the kernel's."""

from typing import NewType
from uuid import UUID

DocumentId = NewType("DocumentId", UUID)
SequenceId = NewType("SequenceId", UUID)
AnnulmentId = NewType("AnnulmentId", UUID)
EmissionPointId = NewType("EmissionPointId", UUID)
"""An emission point of identity's (``identity`` owns it; fiscal-do keeps its id with each range)."""
TrackId = NewType("TrackId", str)
"""DGII's receipt for a document it received, with which its result is asked for."""
```


`services/fiscal-do/src/ledger_fiscal_do/domain/encf.py`:

```python
"""The e-NCF: the number DGII authorizes for each e-CF, ``E`` + the type's two digits + ten digits.

DGII's e-CF technical report v1.0, §7: the letter E is the e-CF's series, the next two digits its type, the
last ten its sequential number.
"""

import re
from dataclasses import dataclass
from typing import ClassVar, Final, Self, final, override

from ledger_fiscal_do.domain.vocabulary import EcfType
from ledger_kernel.errors import InvalidInputError, excerpt

SERIES: Final = "E"
DIGITS: Final = 10
LARGEST: Final = 10**DIGITS - 1
_PATTERN: Final = re.compile(rf"E(?P<type>[0-9]{{2}})(?P<number>[0-9]{{{DIGITS}}})", re.ASCII)


class InvalidEncfError(InvalidInputError):
    """Text that is not an e-NCF of a known type."""

    code: ClassVar[str] = "invalid_encf"

    def __init__(self, text: str) -> None:
        super().__init__(f"an e-NCF is E, a type of e-CF and ten digits, not {excerpt(text)}")


class EncfNumberError(InvalidInputError):
    """A sequential number outside what ten digits hold."""

    code: ClassVar[str] = "invalid_encf_number"

    def __init__(self, number: int) -> None:
        super().__init__(f"an e-NCF's number is from 1 to {LARGEST}, not {number}")


def require_number(number: int) -> int:
    """``number`` if ten digits hold it (and it is not 0), else ``EncfNumberError``."""
    if not 1 <= number <= LARGEST:
        raise EncfNumberError(number)
    return number


@final
@dataclass(frozen=True, slots=True, order=True)
class Encf:
    """An e-NCF: its type and its sequential number."""

    ecf_type: EcfType
    number: int

    def __post_init__(self) -> None:
        require_number(self.number)

    @classmethod
    def parse(cls, text: str) -> Self:
        """Read an e-NCF (``E310000000001``); the series letter is upper case, as DGII writes it."""
        match = _PATTERN.fullmatch(text)
        if match is None or int(match["type"]) not in EcfType:
            raise InvalidEncfError(text)
        return cls(EcfType(int(match["type"])), int(match["number"]))

    @override
    def __str__(self) -> str:
        return f"{SERIES}{self.ecf_type.value}{self.number:0{DIGITS}d}"
```


`services/fiscal-do/src/ledger_fiscal_do/domain/problems.py`:

```python
"""Why a document cannot become an e-CF, field by field, said without repeating what was sent.

A document is checked whole before anything is refused: every problem found is listed (at most
``MAX_PROBLEMS``), each naming where (``lines[2].unit_price``) and what (a stable code), so that a client
fixes them all at once. A message never quotes a value: names, addresses and tax ids are personal data.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar, Final, final

from ledger_kernel.errors import InvalidInputError

MAX_PROBLEMS: Final = 20


class ProblemCode(StrEnum):
    """Each rule a document can break; the value is the code a client matches on."""

    REQUIRED = "required"
    """The type of e-CF needs this, and it is missing."""
    NOT_ALLOWED = "not_allowed"
    """The type of e-CF has no such field: DGII's schema for it refuses it."""
    TEXT_BLANK = "text_blank"
    TEXT_TOO_LONG = "text_too_long"
    TEXT_CONTROL_CHARACTERS = "text_control_characters"
    CODE_MALFORMED = "code_malformed"
    """A municipality, province, phone or NCF that is not in DGII's form."""
    AMOUNT_NEGATIVE = "amount_negative"
    AMOUNT_TOO_PRECISE = "amount_too_precise"
    """More decimal places than DGII's field has (amounts 2, quantities 2, unit prices 4)."""
    AMOUNT_TOO_LARGE = "amount_too_large"
    QUANTITY_NOT_POSITIVE = "quantity_not_positive"
    WRONG_CURRENCY = "wrong_currency"
    """Amounts are in Dominican pesos (DOP): DGII's fields are."""
    DISCOUNT_EXCEEDS_AMOUNT = "discount_exceeds_amount"
    LINES_EMPTY = "lines_empty"
    LINES_NOT_NUMBERED = "lines_not_numbered"
    """Line numbers are 1, 2, 3... in order, as DGII's ``NumeroLinea`` is."""
    TOO_MANY = "too_many"
    """More lines (1,000), payments (7) or phones (3) than DGII's schema holds."""
    BILLING_NOT_ALLOWED = "billing_not_allowed"
    """The type of e-CF taxes its lines one way only (43, 44, 47 exempt; 46 at 0 %)."""
    SERVICE_REQUIRED = "service_required"
    """A payment abroad (47) is for services only."""
    WITHHOLDING_REQUIRED = "withholding_required"
    PERCEPTION_NOT_IN_FORCE = "perception_not_in_force"
    """DGII: "Régimen de percepción no está vigente"."""
    METHOD_NOT_ALLOWED = "method_not_allowed"
    """Gift vouchers pay a consumer invoice (32) only."""
    DATE_BEFORE_ISSUE = "date_before_issue"
    BUYER_IDENTIFIED_TWICE = "buyer_identified_twice"
    """A buyer has a Dominican tax id or a foreign identifier, never both."""
    BUYER_IDENTIFICATION_REQUIRED = "buyer_identification_required"
    """A consumer invoice of DOP 250,000 or more names its buyer, by tax id or foreign identifier."""
    MODIFICATION_NOT_ALLOWED = "modification_not_allowed"
    """The reason a document refers to another is not one its type may give."""
    TEXT_CORRECTION_HAS_AMOUNTS = "text_correction_has_amounts"
    """A note that corrects text carries no amounts."""


@final
@dataclass(frozen=True, slots=True)
class Problem:
    """One rule broken, at one place of the document."""

    field: str
    code: ProblemCode


class ProblemsError(InvalidInputError):
    """Something sent breaks rules field by field: every problem found, at most ``MAX_PROBLEMS``."""

    code: ClassVar[str] = "invalid_fields"
    subject: ClassVar[str] = "the request"

    def __init__(self, problems: tuple[Problem, ...]) -> None:
        self.problems: tuple[Problem, ...] = problems[:MAX_PROBLEMS]
        self.omitted: int = max(len(problems) - MAX_PROBLEMS, 0)
        listed = ", ".join(f"{problem.field}: {problem.code.value}" for problem in self.problems)
        more = f" and {self.omitted} more" if self.omitted else ""
        super().__init__(f"{self.subject} breaks rules: {listed}{more}")


class DocumentInvalidError(ProblemsError):
    """The document cannot become an e-CF of its type."""

    code: ClassVar[str] = "ecf_invalid"
    subject: ClassVar[str] = "the document"


class ProfileInvalidError(ProblemsError):
    """A fiscal profile DGII's fields could not carry."""

    code: ClassVar[str] = "fiscal_profile_invalid"
    subject: ClassVar[str] = "the fiscal profile"


def require_no_problems(
    problems: tuple[Problem, ...], error: type[ProblemsError] = DocumentInvalidError
) -> None:
    """Return when there is no problem; otherwise ``error`` with every one."""
    if problems:
        raise error(problems)
```


`services/fiscal-do/src/ledger_fiscal_do/domain/content.py`:

```python
"""What an e-CF says, in fiscal-do's words: the issuer, the buyer, the lines, the payments, the reference.

These are values, built by the anti-corruption layer from a commercial document (the public API's body
now, ``invoicing``'s event from milestone 5) and checked by ``ledger_fiscal_do.domain.checking``: each value's
form, then its type's rules (``ledger_fiscal_do.domain.types``). Amounts are ``Money`` in DOP; quantities and
unit prices are ``Decimal``s, which DGII counts with more decimal places than an amount has. Names,
addresses and tax ids are personal data: no ``repr`` shows them.
"""

from dataclasses import dataclass, field
from typing import final
lazy from datetime import date
lazy from decimal import Decimal

lazy from ledger_fiscal_do.domain.vocabulary import (
    Billing,
    EcfType,
    IncomeType,
    ItemKind,
    Modification,
    PaymentMethod,
    PaymentTerms,
)
lazy from ledger_kernel.countries.do import TaxId
lazy from ledger_kernel.money import Money


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Issuer:
    """The company that issues, as it stood when the document was issued (its RNC and name from identity)."""

    tax_id: TaxId
    legal_name: str = field(repr=False)
    trade_name: str | None = field(default=None, repr=False)
    branch: str | None = None
    """The branch's name as the document shows it (``Sucursal``)."""
    address: str = field(repr=False)
    municipality: str | None = None
    """DGII's six-digit code (*Tabla III, Codificación Provincias y Municipios*)."""
    province: str | None = None
    phones: tuple[str, ...] = field(default=(), repr=False)
    """At most three, ``809-555-0100``."""
    email: str | None = field(default=None, repr=False)


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Buyer:
    """Who buys (for a purchase, type 41, who sells to the company).

    Every field is optional here; each type of e-CF says which it needs and which it refuses.
    """

    tax_id: TaxId | None = None
    foreign_id: str | None = field(default=None, repr=False)
    """A foreign buyer's identifier, when it has no Dominican tax id (``IdentificadorExtranjero``)."""
    name: str | None = field(default=None, repr=False)
    contact: str | None = field(default=None, repr=False)
    email: str | None = field(default=None, repr=False)
    address: str | None = field(default=None, repr=False)
    municipality: str | None = None
    province: str | None = None
    country: str | None = None
    """The country an export goes to (type 46 only), in words."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Withholding:
    """What the buyer withholds on a line (``Retencion``): ITBIS, income tax (ISR), or both."""

    itbis: Money | None = None
    isr: Money | None = None
    perception: bool = False
    """``IndicadorAgenteRetencionoPercepcion`` 2: refused, perception is not in force (DGII)."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Line:
    """One line of the document (``Item``)."""

    number: int
    name: str = field(repr=False)
    description: str | None = field(default=None, repr=False)
    kind: ItemKind
    quantity: Decimal
    unit: int | None = None
    """DGII's unit of measure code (*Tabla IV*), when the line states one."""
    unit_price: Decimal
    discount: Money | None = None
    billing: Billing
    withholding: Withholding | None = None


@final
@dataclass(frozen=True, slots=True)
class Payment:
    """One way the document is paid, and how much (``FormaDePago``)."""

    method: PaymentMethod
    amount: Money


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Reference:
    """The document this one modifies or replaces (the e-CF's reference section)."""

    modified: str
    """The NCF or e-NCF modified: 11, 13 or 19 characters (``B0100000001``, ``E310000000001``...)."""
    issued_on: date
    code: Modification
    reason: str | None = field(default=None, repr=False)
    """Why, in words: notes only (types 33, 34)."""
    other_taxpayer: TaxId | None = None
    """When the modified document was issued under another RNC (dissolution, merger, split)."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Content:
    """Everything an e-CF says but its number, its issuer and its signature."""

    ecf_type: EcfType
    issued_on: date
    """The issue date: a date of the Dominican Republic's calendar (America/Santo_Domingo)."""
    income: IncomeType | None = None
    terms: PaymentTerms | None = None
    deadline: date | None = None
    """When a sale on credit is due (the e-CF's payment deadline)."""
    payments: tuple[Payment, ...] = ()
    prices_include_itbis: bool = False
    """``IndicadorMontoGravado``: the lines' amounts include ITBIS (DGII then takes the taxed base out)."""
    buyer: Buyer | None = None
    lines: tuple[Line, ...]
    reference: Reference | None = None
```


`services/fiscal-do/src/ledger_fiscal_do/domain/form.py`:

```python
"""The form of each value an e-CF holds, as DGII's fields allow it, whatever the type of e-CF.

Texts: not blank, no control character (XML 1.0 cannot carry most of them), within the field's length.
Amounts: Dominican pesos, never negative, at most 16 integer digits and two decimal places; quantities the
same but above zero; unit prices up to four decimal places (DGII's e-CF technical report v1.0, §13: "Se
exceptúan de esta regla los campos 'Precio Unitario Ítem'..., donde se permiten cifras con hasta cuatro (4)
dígitos decimales"). Codes in DGII's forms. Each check adds its problems to a list, so that a document's every
problem is reported at once.
"""

import re
import unicodedata
from decimal import Decimal
from typing import Final

from ledger_fiscal_do.domain.problems import Problem, ProblemCode
from ledger_kernel.money import Currency
lazy from ledger_kernel.money import Money

INTEGER_DIGITS: Final = 16
AMOUNT_PLACES: Final = 2
QUANTITY_PLACES: Final = 2
PRICE_PLACES: Final = 4
MAX_LINES: Final = 1000
"""``NumeroLinea`` stops at 1,000 in every e-CF schema (a consumer invoice under DOP 250,000 may have 10,000
lines by DGII's format, but its full e-CF is still written in the same schema)."""
MAX_PAYMENTS: Final = 7
MAX_PHONES: Final = 3
CEILING: Final = Decimal(10) ** INTEGER_DIGITS
_SIX_DIGITS: Final = re.compile(r"[0-9]{6}", re.ASCII)
_PHONE: Final = re.compile(r"[0-9]{3}-[0-9]{3}-[0-9]{4}", re.ASCII)
_NCF: Final = re.compile(r"[A-Za-z0-9]{11}|[A-Za-z0-9]{13}|[A-Za-z0-9]{19}", re.ASCII)


type Problems = list[Problem]
"""What a check adds its findings to."""


def places(value: Decimal) -> int:
    """How many decimal places ``value`` is written with (``1.50``: 2; ``1E+2``: 0)."""
    exponent = value.as_tuple().exponent
    return -exponent if isinstance(exponent, int) and exponent < 0 else 0


def text(problems: Problems, where: str, value: str | None, *, limit: int) -> None:
    """A text field, when there is one: not blank, no control character, at most ``limit`` characters."""
    if value is None:
        return
    if not value.strip():
        problems.append(Problem(where, ProblemCode.TEXT_BLANK))
    elif len(value) > limit:
        problems.append(Problem(where, ProblemCode.TEXT_TOO_LONG))
    elif any(unicodedata.category(character) == "Cc" for character in value):
        problems.append(Problem(where, ProblemCode.TEXT_CONTROL_CHARACTERS))


def area_code(problems: Problems, where: str, value: str | None) -> None:
    """A municipality or province: DGII's six digits (whether DGII's table holds it, its schema says)."""
    if value is not None and _SIX_DIGITS.fullmatch(value) is None:
        problems.append(Problem(where, ProblemCode.CODE_MALFORMED))


def phone(problems: Problems, where: str, value: str) -> None:
    """A phone number as DGII writes one: ``809-555-0100``."""
    if _PHONE.fullmatch(value) is None:
        problems.append(Problem(where, ProblemCode.CODE_MALFORMED))


def ncf(problems: Problems, where: str, value: str) -> None:
    """A fiscal number another document modifies: a paper NCF (11 or 19) or an e-NCF (13)."""
    if _NCF.fullmatch(value) is None:
        problems.append(Problem(where, ProblemCode.CODE_MALFORMED))


def _decimal(problems: Problems, where: str, value: Decimal, *, decimals: int) -> None:
    if value < 0:
        problems.append(Problem(where, ProblemCode.AMOUNT_NEGATIVE))
    elif value >= CEILING:
        problems.append(Problem(where, ProblemCode.AMOUNT_TOO_LARGE))
    elif places(value) > decimals:
        problems.append(Problem(where, ProblemCode.AMOUNT_TOO_PRECISE))


def amount(problems: Problems, where: str, value: Money | None) -> None:
    """An amount in DOP: never negative, 16 integer digits and 2 decimal places at most."""
    if value is None:
        return
    if value.currency is not Currency.DOP:
        problems.append(Problem(where, ProblemCode.WRONG_CURRENCY))
        return
    _decimal(problems, where, value.amount, decimals=AMOUNT_PLACES)


def quantity(problems: Problems, where: str, value: Decimal) -> None:
    """A line's quantity: above zero, 2 decimal places at most."""
    if value <= 0:
        problems.append(Problem(where, ProblemCode.QUANTITY_NOT_POSITIVE))
        return
    _decimal(problems, where, value, decimals=QUANTITY_PLACES)


def unit_price(problems: Problems, where: str, value: Decimal) -> None:
    """A line's unit price: never negative, 4 decimal places at most."""
    _decimal(problems, where, value, decimals=PRICE_PLACES)
```


`services/fiscal-do/src/ledger_fiscal_do/domain/notation.py`:

```python
"""How DGII writes what its documents and links hold: dates, moments, amounts, numbers.

Dates ``dd-MM-yyyy``; moments ``dd-MM-yyyy HH:mm:ss`` in the Dominican Republic's time (§7: DGII's local-time
fields are converted here and only here); amounts with two decimal places; quantities and prices as they are,
never in exponent form.
"""

from datetime import timedelta, timezone
from typing import Final
lazy from datetime import date, datetime
lazy from decimal import Decimal

lazy from ledger_kernel.money import Money

SANTO_DOMINGO: Final = timezone(timedelta(hours=-4), "AST")
"""The Dominican Republic's time: UTC-04:00 all year, with no daylight saving time. A fixed offset rather than
``ZoneInfo("America/Santo_Domingo")``, which would need the time zone database on every machine."""
_DATE: Final = "%d-%m-%Y"
_MOMENT: Final = "%d-%m-%Y %H:%M:%S"


def dgii_date(value: date) -> str:
    """``dd-MM-yyyy``."""
    return value.strftime(_DATE)


def dgii_moment(value: datetime) -> str:
    """``dd-MM-yyyy HH:mm:ss`` in the Dominican Republic's time."""
    return value.astimezone(SANTO_DOMINGO).strftime(_MOMENT)


def local_date(value: datetime) -> date:
    """The Dominican Republic's date at ``value``."""
    return value.astimezone(SANTO_DOMINGO).date()


def dgii_amount(value: Money) -> str:
    """An amount with two decimal places, as every amount of DGII's formats is written."""
    return f"{value.amount:.2f}"


def dgii_number(value: Decimal) -> str:
    """A quantity or a price as it is, never in exponent form."""
    return format(value, "f")
```


`services/fiscal-do/src/ledger_fiscal_do/domain/totals.py`:

```python
"""An e-CF's totals, computed as DGII computes them, rounding only where DGII rounds.

DGII's rules (*Formato Comprobante Fiscal Electrónico (e-CF) v1.0*, Totales; DGII's e-CF technical report
v1.0, §12-13):

* a line's amount (``MontoItem``) is unit price x quantity - discount, to two decimal places;
* the taxed base of each ITBIS rate (``MontoGravadoI1..3``) is the sum of its lines' amounts, or, when the
  amounts include ITBIS (``IndicadorMontoGravado`` 1), that sum divided by 1 + the rate, to two places;
* each rate's ITBIS (``TotalITBIS1..3``) is its base x the rate, to two places;
* the total (``MontoTotal``) is the taxed bases + the exempt amount + the ITBIS, exactly;
* rounding is half up at the third decimal ("si es igual o mayor a 5, se debe incrementar el segundo
  decimal en una unidad"): what ``Money.rounded`` does to a non-negative amount.

Every product and quotient runs in an explicit decimal context wide enough to be exact before the one
rounding, never in the ambient one.
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Context, Decimal
from types import MappingProxyType
from typing import Final, final
lazy from collections.abc import Iterable, Mapping

from ledger_fiscal_do.domain.vocabulary import Billing
from ledger_kernel.money import Currency, Money
lazy from ledger_fiscal_do.domain.content import Content, Line

ITBIS_RATES: Final[Mapping[Billing, int]] = MappingProxyType(
    {
        Billing.ITBIS_1: 18,
        Billing.ITBIS_2: 16,
        Billing.ITBIS_3: 0,
    }
)
"""Each rate's percentage, as ``ITBIS1..3`` write it and DGII's format defines them (18 %, 16 %, 0 %)."""
TAXED: Final = (Billing.ITBIS_1, Billing.ITBIS_2, Billing.ITBIS_3)
_CONTEXT: Final = Context(prec=80, rounding=ROUND_HALF_UP)
"""Wide enough that a product of a 20-digit price and an 18-digit quantity, or a quotient taken to 40 places
before its one rounding, is exact where it matters."""
_CENT: Final = Decimal("0.01")
_HUNDRED: Final = Decimal(100)
ZERO: Final = Money.zero(Currency.DOP)


def _cents(value: Decimal) -> Money:
    return Money(_CONTEXT.quantize(value, _CENT), Currency.DOP)


def gross(line: Line) -> Decimal:
    """Unit price x quantity, exact."""
    return _CONTEXT.multiply(line.unit_price, line.quantity)


def line_amount(line: Line) -> Money:
    """``MontoItem``: unit price x quantity - discount, to two decimal places."""
    discount = Decimal(0) if line.discount is None else line.discount.amount
    return _cents(_CONTEXT.subtract(gross(line), discount))


def taxed_base(amount: Money, rate: int, *, includes_itbis: bool) -> Money:
    """A rate's taxed base from the sum of its lines: that sum, or without its ITBIS when it includes it."""
    if not includes_itbis:
        return amount
    divisor = _CONTEXT.add(Decimal(1), _CONTEXT.divide(Decimal(rate), _HUNDRED))
    return _cents(_CONTEXT.divide(amount.amount, divisor))


def itbis(base: Money, rate: int) -> Money:
    """A rate's ITBIS: its base x the rate, to two decimal places."""
    return _cents(_CONTEXT.divide(_CONTEXT.multiply(base.amount, Decimal(rate)), _HUNDRED))


def _sum(amounts: Iterable[Money]) -> Money:
    total = ZERO
    for amount in amounts:
        total += amount
    return total


@final
@dataclass(frozen=True, slots=True)
class TaxedGroup:
    """The lines taxed at one ITBIS rate: their base and their ITBIS."""

    billing: Billing
    rate: int
    base: Money
    itbis: Money


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Totals:
    """An e-CF's amounts: per line, per rate, and in all. A group with no line is absent (``None``)."""

    line_amounts: tuple[Money, ...]
    taxed: tuple[TaxedGroup, ...]
    """One group per rate that has lines, in DGII's order of rates."""
    exempt: Money | None
    not_billable: Money | None
    itbis_withheld: Money | None
    isr_withheld: Money | None

    def group(self, billing: Billing) -> TaxedGroup | None:
        """The group of ``billing``'s rate, if any line has it."""
        return next((group for group in self.taxed if group.billing is billing), None)

    @property
    def taxed_total(self) -> Money | None:
        """``MontoGravadoTotal``."""
        return _sum(group.base for group in self.taxed) if self.taxed else None

    @property
    def itbis_total(self) -> Money | None:
        """``TotalITBIS``."""
        return _sum(group.itbis for group in self.taxed) if self.taxed else None

    @property
    def total(self) -> Money:
        """``MontoTotal``: taxed bases + exempt + ITBIS."""
        parts = (self.taxed_total, self.exempt, self.itbis_total)
        return _sum(part for part in parts if part is not None)


def _present(amounts: list[Money]) -> Money | None:
    return _sum(amounts) if amounts else None


def compute(content: Content) -> Totals:
    """The totals of ``content``, whose values ``ledger_fiscal_do.domain.form`` has checked."""
    amounts = tuple(line_amount(line) for line in content.lines)
    by_billing: dict[Billing, list[Money]] = {}
    for line, amount in zip(content.lines, amounts, strict=True):
        by_billing.setdefault(line.billing, []).append(amount)
    taxed: list[TaxedGroup] = []
    for billing in TAXED:
        lines = by_billing.get(billing)
        if lines is None:
            continue
        rate = ITBIS_RATES[billing]
        base = taxed_base(_sum(lines), rate, includes_itbis=content.prices_include_itbis)
        taxed.append(TaxedGroup(billing, rate, base, itbis(base, rate)))
    withholdings = [line.withholding for line in content.lines if line.withholding is not None]
    return Totals(
        line_amounts=amounts,
        taxed=tuple(taxed),
        exempt=_present(by_billing.get(Billing.EXEMPT, [])),
        not_billable=_present(by_billing.get(Billing.NOT_BILLABLE, [])),
        itbis_withheld=_present([held.itbis for held in withholdings if held.itbis is not None]),
        isr_withheld=_present([held.isr for held in withholdings if held.isr is not None]),
    )
```


`services/fiscal-do/src/ledger_fiscal_do/domain/verdict.py`:

```python
"""DGII's final answer on a document, and the messages that come with it, verbatim."""

from dataclasses import dataclass
from enum import StrEnum
from typing import final
lazy from datetime import datetime


class Verdict(StrEnum):
    """DGII's final answer."""

    ACCEPTED = "accepted"
    ACCEPTED_CONDITIONALLY = "accepted_conditionally"
    REJECTED = "rejected"


@final
@dataclass(frozen=True, slots=True)
class DgiiMessage:
    """One message DGII gave with its answer, verbatim: its code and its text."""

    code: str
    text: str


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Outcome:
    """DGII's final answer on a document, as DGII gave it."""

    verdict: Verdict
    messages: tuple[DgiiMessage, ...] = ()
    decided_at: datetime
```


- [ ] **Step 5: Implement the rules of the types**

`services/fiscal-do/src/ledger_fiscal_do/domain/types/__init__.py`:

```python
"""The ten types of e-CF, one module each: what each demands and refuses.

``ledger_fiscal_do.domain.registry`` finds a type's rules by its code.
"""
```


`services/fiscal-do/src/ledger_fiscal_do/domain/types/base.py`:

```python
"""What a type of e-CF demands and refuses: the strategy each type's module declares.

A type's rules are data: which areas and fields its schema carries and which DGII's format makes required
(code 1), conditional (2) or optional (3), which taxes its lines may have, which reasons may refer to another
document. One check applies them (``TypeRules.check``); a type with a rule no table expresses adds it as
``special`` (a consumer invoice of DOP 250,000 or more names its buyer). Adding a type of e-CF is adding a
module and a line in the registry, never an ``if`` on the type.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Final, final

from ledger_fiscal_do.domain.problems import Problem, ProblemCode
from ledger_fiscal_do.domain.vocabulary import ItemKind, PaymentMethod, PaymentTerms
lazy from ledger_fiscal_do.domain.content import Buyer, Content
lazy from ledger_fiscal_do.domain.totals import Totals
lazy from ledger_fiscal_do.domain.vocabulary import Billing, EcfType, Modification

EVERY_METHOD_BUT_GIFTS: Final = frozenset(PaymentMethod) - {PaymentMethod.GIFT_VOUCHERS}
"""How a document is paid, gift vouchers aside: they pay a consumer invoice (32) only."""


class Presence(Enum):
    """Whether a type's e-CF must, may or must not carry something."""

    REQUIRED = auto()
    OPTIONAL = auto()
    ABSENT = auto()


type Special = Callable[[Content, Totals], Iterable[Problem]]
"""A rule of one type that its table cannot express."""
type Indicator = Callable[[Content], bool]
"""A yes-or-no one type's schema asks of its content."""


def nothing_more(content: Content, totals: Totals) -> Iterable[Problem]:
    """The ``special`` of a type whose table says it all."""
    del content, totals
    return ()


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class BuyerShape:
    """Which of the buyer's fields a type carries (``Comprador``)."""

    tax_id: Presence
    foreign_id: Presence
    name: Presence
    details: bool = True
    """Contact, email, address, municipality and province."""
    country: bool = False
    always: bool = False
    """The type's schema demands the buyer's area even when nothing in it is required (a consumer invoice)."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class TypeRules:
    """One type of e-CF's rules (see the module)."""

    ecf_type: EcfType
    sequence_expires: bool
    """Its schema carries the sequence's expiry date (``FechaVencimientoSecuencia``)."""
    income: Presence
    terms: Presence
    deadline: bool
    """It may carry a payment deadline, required for a sale on credit."""
    payments: bool
    methods: frozenset[PaymentMethod] = EVERY_METHOD_BUT_GIFTS
    prices_include_itbis: bool
    """It may say that its lines' amounts include ITBIS."""
    buyer: BuyerShape | None
    """``None``: it has no buyer at all (a minor expense, 43)."""
    billing: frozenset[Billing]
    withholding: Presence
    """Per line."""
    itbis_withholding: bool
    discounts: bool
    reference: Presence
    modifications: frozenset[Modification]
    reason: bool
    """Its reference may say why, in words (notes only)."""
    services_only: bool = False
    late_credit: Indicator | None = None
    """``IndicadorNotaCredito``: only a credit note's schema carries it."""
    special: Special = field(default=nothing_more)

    def check(self, content: Content, totals: Totals) -> tuple[Problem, ...]:
        """Every problem of ``content`` against these rules."""
        problems: list[Problem] = []
        _present(problems, "income", content.income, self.income)
        _present(problems, "terms", content.terms, self.terms)
        self._payment(problems, content)
        self._buyer(problems, content.buyer)
        self._lines(problems, content)
        self._reference(problems, content)
        problems.extend(self.special(content, totals))
        return tuple(problems)

    def _payment(self, problems: list[Problem], content: Content) -> None:
        if not self.deadline:
            _present(problems, "deadline", content.deadline, Presence.ABSENT)
        elif content.terms is PaymentTerms.CREDIT and content.deadline is None:
            problems.append(Problem("deadline", ProblemCode.REQUIRED))
        if content.deadline is not None and content.deadline < content.issued_on:
            problems.append(Problem("deadline", ProblemCode.DATE_BEFORE_ISSUE))
        if content.payments and not self.payments:
            problems.append(Problem("payments", ProblemCode.NOT_ALLOWED))
        for index, payment in enumerate(content.payments):
            if payment.method not in self.methods:
                problems.append(Problem(f"payments[{index}].method", ProblemCode.METHOD_NOT_ALLOWED))
        if content.prices_include_itbis and not self.prices_include_itbis:
            problems.append(Problem("prices_include_itbis", ProblemCode.NOT_ALLOWED))

    def _buyer(self, problems: list[Problem], buyer: Buyer | None) -> None:
        shape = self.buyer
        if shape is None:
            _present(problems, "buyer", buyer, Presence.ABSENT)
            return
        if buyer is None:
            if Presence.REQUIRED in (shape.tax_id, shape.name):
                problems.append(Problem("buyer", ProblemCode.REQUIRED))
            return
        _present(problems, "buyer.tax_id", buyer.tax_id, shape.tax_id)
        _present(problems, "buyer.foreign_id", buyer.foreign_id, shape.foreign_id)
        _present(problems, "buyer.name", buyer.name, shape.name)
        if buyer.tax_id is not None and buyer.foreign_id is not None:
            problems.append(Problem("buyer", ProblemCode.BUYER_IDENTIFIED_TWICE))
        details = (buyer.contact, buyer.email, buyer.address, buyer.municipality, buyer.province)
        if not shape.details:
            for name, value in zip(_DETAILS, details, strict=True):
                _present(problems, f"buyer.{name}", value, Presence.ABSENT)
        if not shape.country:
            _present(problems, "buyer.country", buyer.country, Presence.ABSENT)

    def _lines(self, problems: list[Problem], content: Content) -> None:
        for index, line in enumerate(content.lines):
            where = f"lines[{index}]"
            if line.billing not in self.billing:
                problems.append(Problem(f"{where}.billing", ProblemCode.BILLING_NOT_ALLOWED))
            if self.services_only and line.kind is not ItemKind.SERVICE:
                problems.append(Problem(f"{where}.kind", ProblemCode.SERVICE_REQUIRED))
            if line.discount is not None and not self.discounts:
                problems.append(Problem(f"{where}.discount", ProblemCode.NOT_ALLOWED))
            held = line.withholding
            _present(problems, f"{where}.withholding", held, self.withholding)
            if held is None:
                continue
            if held.perception:
                problems.append(Problem(f"{where}.withholding", ProblemCode.PERCEPTION_NOT_IN_FORCE))
            if held.itbis is not None and not self.itbis_withholding:
                problems.append(Problem(f"{where}.withholding.itbis", ProblemCode.NOT_ALLOWED))

    def _reference(self, problems: list[Problem], content: Content) -> None:
        reference = content.reference
        _present(problems, "reference", reference, self.reference)
        if reference is None:
            return
        if reference.code not in self.modifications:
            problems.append(Problem("reference.code", ProblemCode.MODIFICATION_NOT_ALLOWED))
        if reference.reason is not None and not self.reason:
            problems.append(Problem("reference.reason", ProblemCode.NOT_ALLOWED))


def _present(problems: list[Problem], where: str, value: object, presence: Presence) -> None:
    if value is None and presence is Presence.REQUIRED:
        problems.append(Problem(where, ProblemCode.REQUIRED))
    elif value is not None and presence is Presence.ABSENT:
        problems.append(Problem(where, ProblemCode.NOT_ALLOWED))


_DETAILS: Final = ("contact", "email", "address", "municipality", "province")
```


`services/fiscal-do/src/ledger_fiscal_do/domain/types/credit_invoice.py`:

```python
"""Type 31, Factura de Crédito Fiscal Electrónica: a sale to a taxpayer, who may claim its ITBIS."""

from typing import Final

from ledger_fiscal_do.domain.types.base import BuyerShape, Presence, TypeRules
from ledger_fiscal_do.domain.vocabulary import Billing, EcfType, Modification

RULES: Final = TypeRules(
    ecf_type=EcfType.CREDIT_INVOICE,
    sequence_expires=True,
    income=Presence.REQUIRED,
    terms=Presence.REQUIRED,
    deadline=True,
    payments=True,
    prices_include_itbis=True,
    buyer=BuyerShape(tax_id=Presence.REQUIRED, foreign_id=Presence.ABSENT, name=Presence.REQUIRED),
    billing=frozenset(Billing),
    withholding=Presence.OPTIONAL,
    itbis_withholding=True,
    discounts=True,
    reference=Presence.OPTIONAL,
    modifications=frozenset(
        {Modification.REPLACES_CONTINGENCY_NCF, Modification.REFERENCES_CONSUMER_INVOICE}
    ),
    reason=False,
)
```


`services/fiscal-do/src/ledger_fiscal_do/domain/types/consumer_invoice.py`:

```python
"""Type 32, Factura de Consumo Electrónica: a sale to a final consumer.

Under DOP 250,000 its buyer is optional and DGII receives a summary of it instead of the whole e-CF (RFCE);
from DOP 250,000 on, the buyer is named, by a Dominican tax id or, for a foreigner, a foreign identifier
(*Formato e-CF v1.0*, ``RNCComprador`` b) and d), ``IdentificadorExtranjero``). Gift vouchers pay it, and only
it.
"""

from decimal import Decimal
from typing import Final
lazy from collections.abc import Iterable

from ledger_fiscal_do.domain.problems import Problem, ProblemCode
from ledger_fiscal_do.domain.types.base import BuyerShape, Presence, TypeRules
from ledger_fiscal_do.domain.vocabulary import Billing, EcfType, Modification, PaymentMethod
from ledger_kernel.money import Currency, Money
lazy from ledger_fiscal_do.domain.content import Content
lazy from ledger_fiscal_do.domain.totals import Totals

SUMMARY_THRESHOLD: Final = Money(Decimal(250_000), Currency.DOP)
"""From this total on, a consumer invoice names its buyer and DGII receives it whole."""


def named_from_threshold(content: Content, totals: Totals) -> Iterable[Problem]:
    """A consumer invoice of DOP 250,000 or more names its buyer: an identifier and a name."""
    if totals.total < SUMMARY_THRESHOLD:
        return ()
    buyer = content.buyer
    problems: list[Problem] = []
    if buyer is None or (buyer.tax_id is None and buyer.foreign_id is None):
        problems.append(Problem("buyer", ProblemCode.BUYER_IDENTIFICATION_REQUIRED))
    if buyer is None or buyer.name is None:
        problems.append(Problem("buyer.name", ProblemCode.REQUIRED))
    return problems


RULES: Final = TypeRules(
    ecf_type=EcfType.CONSUMER_INVOICE,
    sequence_expires=False,
    income=Presence.REQUIRED,
    terms=Presence.REQUIRED,
    deadline=True,
    payments=True,
    methods=frozenset(PaymentMethod),
    prices_include_itbis=True,
    buyer=BuyerShape(
        tax_id=Presence.OPTIONAL, foreign_id=Presence.OPTIONAL, name=Presence.OPTIONAL, always=True
    ),
    billing=frozenset(Billing),
    withholding=Presence.ABSENT,
    itbis_withholding=False,
    discounts=True,
    reference=Presence.OPTIONAL,
    modifications=frozenset({Modification.REPLACES_CONTINGENCY_NCF}),
    reason=False,
    special=named_from_threshold,
)
```


`services/fiscal-do/src/ledger_fiscal_do/domain/types/notes.py`:

```python
"""What debit notes (33) and credit notes (34) share: a reference to the document they modify, and why."""

from typing import Final
lazy from collections.abc import Iterable

from ledger_fiscal_do.domain.problems import Problem, ProblemCode
from ledger_fiscal_do.domain.types.base import BuyerShape, Presence
from ledger_fiscal_do.domain.vocabulary import Modification
lazy from ledger_fiscal_do.domain.content import Content
lazy from ledger_fiscal_do.domain.totals import Totals

NOTE_MODIFICATIONS: Final = frozenset(
    {
        Modification.ANNULS,
        Modification.CORRECTS_TEXT,
        Modification.CORRECTS_AMOUNTS,
    }
)
"""A note annuls, corrects text or corrects amounts: codes 1 to 3, which "solo aplican para Nota de Crédito o
Débito Electrónica"."""
NOTE_BUYER: Final = BuyerShape(tax_id=Presence.OPTIONAL, foreign_id=Presence.OPTIONAL, name=Presence.OPTIONAL)


def text_corrections_carry_no_amounts(content: Content, totals: Totals) -> Iterable[Problem]:
    """A note that corrects text has every line at zero.

    DGII's format: ``MontoItem`` is zero when a credit note only corrects text.
    """
    reference = content.reference
    if reference is None or reference.code is not Modification.CORRECTS_TEXT:
        return ()
    return tuple(
        Problem(f"lines[{index}]", ProblemCode.TEXT_CORRECTION_HAS_AMOUNTS)
        for index, amount in enumerate(totals.line_amounts)
        if amount.amount != 0
    )
```


`services/fiscal-do/src/ledger_fiscal_do/domain/types/debit_note.py`:

```python
"""Type 33, Nota de Débito Electrónica: what the seller charges after a document, referring to it."""

from typing import Final

from ledger_fiscal_do.domain.types.base import Presence, TypeRules
from ledger_fiscal_do.domain.types.notes import (
    NOTE_BUYER,
    NOTE_MODIFICATIONS,
    text_corrections_carry_no_amounts,
)
from ledger_fiscal_do.domain.vocabulary import Billing, EcfType

RULES: Final = TypeRules(
    ecf_type=EcfType.DEBIT_NOTE,
    sequence_expires=True,
    income=Presence.OPTIONAL,
    terms=Presence.REQUIRED,
    deadline=True,
    payments=True,
    prices_include_itbis=True,
    buyer=NOTE_BUYER,
    billing=frozenset(Billing),
    withholding=Presence.OPTIONAL,
    itbis_withholding=True,
    discounts=True,
    reference=Presence.REQUIRED,
    modifications=NOTE_MODIFICATIONS,
    reason=True,
    special=text_corrections_carry_no_amounts,
)
```


`services/fiscal-do/src/ledger_fiscal_do/domain/types/credit_note.py`:

```python
"""Type 34, Nota de Crédito Electrónica: a return, a discount or an annulment of a document it refers to.

Its sequence has no expiry date, it lists no payments, and it says whether it was issued more than 30 days
after the document it modifies (``IndicadorNotaCredito``, which ``late`` decides): past 30 days, a return of
taxed goods restores the price without its ITBIS (Reglamento 293-11, arts. 8 and 28).
"""

from datetime import timedelta
from typing import Final

from ledger_fiscal_do.domain.types.base import Presence, TypeRules
from ledger_fiscal_do.domain.types.notes import (
    NOTE_BUYER,
    NOTE_MODIFICATIONS,
    text_corrections_carry_no_amounts,
)
from ledger_fiscal_do.domain.vocabulary import Billing, EcfType
lazy from ledger_fiscal_do.domain.content import Content

LATE_AFTER: Final = timedelta(days=30)


def late(content: Content) -> bool:
    """Whether the note comes more than 30 calendar days after the document it modifies."""
    reference = content.reference
    return reference is not None and content.issued_on - reference.issued_on > LATE_AFTER


RULES: Final = TypeRules(
    ecf_type=EcfType.CREDIT_NOTE,
    sequence_expires=False,
    income=Presence.OPTIONAL,
    terms=Presence.REQUIRED,
    deadline=True,
    payments=False,
    prices_include_itbis=True,
    buyer=NOTE_BUYER,
    billing=frozenset(Billing),
    withholding=Presence.OPTIONAL,
    itbis_withholding=True,
    discounts=True,
    reference=Presence.REQUIRED,
    modifications=NOTE_MODIFICATIONS,
    reason=True,
    late_credit=late,
    special=text_corrections_carry_no_amounts,
)
```


`services/fiscal-do/src/ledger_fiscal_do/domain/types/purchases.py`:

```python
"""Type 41, Compras Electrónico: a purchase from someone not registered as a taxpayer.

The "buyer" area names the seller; every line says what the company withholds (``Retencion`` is required).
"""

from typing import Final

from ledger_fiscal_do.domain.types.base import BuyerShape, Presence, TypeRules
from ledger_fiscal_do.domain.vocabulary import Billing, EcfType, Modification

RULES: Final = TypeRules(
    ecf_type=EcfType.PURCHASES,
    sequence_expires=True,
    income=Presence.ABSENT,
    terms=Presence.OPTIONAL,
    deadline=True,
    payments=True,
    prices_include_itbis=True,
    buyer=BuyerShape(tax_id=Presence.REQUIRED, foreign_id=Presence.ABSENT, name=Presence.REQUIRED),
    billing=frozenset({Billing.ITBIS_1, Billing.ITBIS_2, Billing.ITBIS_3, Billing.EXEMPT}),
    withholding=Presence.REQUIRED,
    itbis_withholding=True,
    discounts=True,
    reference=Presence.OPTIONAL,
    modifications=frozenset({Modification.REPLACES_CONTINGENCY_NCF}),
    reason=False,
)
```


`services/fiscal-do/src/ledger_fiscal_do/domain/types/minor_expenses.py`:

```python
"""Type 43, Gastos Menores Electrónico: the company's own minor expenses (fares, parking, tolls).

It names no buyer, its lines are exempt, and it lists no payments: its amounts cannot be claimed as ITBIS
paid in advance.
"""

from typing import Final

from ledger_fiscal_do.domain.types.base import Presence, TypeRules
from ledger_fiscal_do.domain.vocabulary import Billing, EcfType, Modification

RULES: Final = TypeRules(
    ecf_type=EcfType.MINOR_EXPENSES,
    sequence_expires=True,
    income=Presence.ABSENT,
    terms=Presence.OPTIONAL,
    deadline=False,
    payments=False,
    prices_include_itbis=False,
    buyer=None,
    billing=frozenset({Billing.EXEMPT}),
    withholding=Presence.ABSENT,
    itbis_withholding=False,
    discounts=False,
    reference=Presence.OPTIONAL,
    modifications=frozenset({Modification.REPLACES_CONTINGENCY_NCF}),
    reason=False,
)
```


`services/fiscal-do/src/ledger_fiscal_do/domain/types/special_regimes.py`:

```python
"""Type 44, Regímenes Especiales Electrónico: a sale exempt of ITBIS to a buyer under a special regime."""

from typing import Final

from ledger_fiscal_do.domain.types.base import BuyerShape, Presence, TypeRules
from ledger_fiscal_do.domain.vocabulary import Billing, EcfType, Modification

RULES: Final = TypeRules(
    ecf_type=EcfType.SPECIAL_REGIMES,
    sequence_expires=True,
    income=Presence.REQUIRED,
    terms=Presence.REQUIRED,
    deadline=True,
    payments=True,
    prices_include_itbis=False,
    buyer=BuyerShape(tax_id=Presence.OPTIONAL, foreign_id=Presence.OPTIONAL, name=Presence.REQUIRED),
    billing=frozenset({Billing.EXEMPT}),
    withholding=Presence.ABSENT,
    itbis_withholding=False,
    discounts=True,
    reference=Presence.OPTIONAL,
    modifications=frozenset({Modification.REPLACES_CONTINGENCY_NCF}),
    reason=False,
)
```


`services/fiscal-do/src/ledger_fiscal_do/domain/types/government.py`:

```python
"""Type 45, Gubernamental Electrónico: a sale to the State (central government, its institutions)."""

from typing import Final

from ledger_fiscal_do.domain.types.base import BuyerShape, Presence, TypeRules
from ledger_fiscal_do.domain.vocabulary import Billing, EcfType, Modification

RULES: Final = TypeRules(
    ecf_type=EcfType.GOVERNMENT,
    sequence_expires=True,
    income=Presence.REQUIRED,
    terms=Presence.REQUIRED,
    deadline=True,
    payments=True,
    prices_include_itbis=True,
    buyer=BuyerShape(tax_id=Presence.REQUIRED, foreign_id=Presence.ABSENT, name=Presence.REQUIRED),
    billing=frozenset(Billing),
    withholding=Presence.ABSENT,
    itbis_withholding=False,
    discounts=True,
    reference=Presence.OPTIONAL,
    modifications=frozenset({Modification.REPLACES_CONTINGENCY_NCF}),
    reason=False,
)
```


`services/fiscal-do/src/ledger_fiscal_do/domain/types/exports.py`:

```python
"""Type 46, Comprobante de Exportaciones Electrónico: a sale of goods abroad, taxed at the 0 % rate.

It may name the country the goods go to (``PaisComprador``).
"""

from typing import Final

from ledger_fiscal_do.domain.types.base import BuyerShape, Presence, TypeRules
from ledger_fiscal_do.domain.vocabulary import Billing, EcfType, Modification

RULES: Final = TypeRules(
    ecf_type=EcfType.EXPORTS,
    sequence_expires=True,
    income=Presence.REQUIRED,
    terms=Presence.REQUIRED,
    deadline=True,
    payments=True,
    prices_include_itbis=False,
    buyer=BuyerShape(
        tax_id=Presence.OPTIONAL, foreign_id=Presence.OPTIONAL, name=Presence.REQUIRED, country=True
    ),
    billing=frozenset({Billing.ITBIS_3}),
    withholding=Presence.ABSENT,
    itbis_withholding=False,
    discounts=True,
    reference=Presence.OPTIONAL,
    modifications=frozenset({Modification.REPLACES_CONTINGENCY_NCF}),
    reason=False,
)
```


`services/fiscal-do/src/ledger_fiscal_do/domain/types/foreign_payments.py`:

```python
"""Type 47, Comprobante para Pagos al Exterior Electrónico: a payment abroad.

A payment of Dominican-source income to someone not resident, on which the company withholds the whole income
tax (Código Tributario, arts. 297 and 305). Its lines are exempt services, each with the income tax
withheld; its buyer, if named, by a foreign identifier only.
"""

from typing import Final
lazy from collections.abc import Iterable

from ledger_fiscal_do.domain.problems import Problem, ProblemCode
from ledger_fiscal_do.domain.types.base import BuyerShape, Presence, TypeRules
from ledger_fiscal_do.domain.vocabulary import Billing, EcfType, Modification
lazy from ledger_fiscal_do.domain.content import Content
lazy from ledger_fiscal_do.domain.totals import Totals


def income_tax_withheld(content: Content, totals: Totals) -> Iterable[Problem]:
    """Every line states the income tax withheld (``MontoISRRetenido`` is required in type 47)."""
    del totals
    return tuple(
        Problem(f"lines[{index}].withholding.isr", ProblemCode.REQUIRED)
        for index, line in enumerate(content.lines)
        if line.withholding is not None and line.withholding.isr is None
    )


RULES: Final = TypeRules(
    ecf_type=EcfType.FOREIGN_PAYMENTS,
    sequence_expires=True,
    income=Presence.ABSENT,
    terms=Presence.OPTIONAL,
    deadline=True,
    payments=True,
    prices_include_itbis=False,
    buyer=BuyerShape(
        tax_id=Presence.ABSENT, foreign_id=Presence.OPTIONAL, name=Presence.OPTIONAL, details=False
    ),
    billing=frozenset({Billing.EXEMPT}),
    withholding=Presence.REQUIRED,
    itbis_withholding=False,
    discounts=False,
    reference=Presence.OPTIONAL,
    modifications=frozenset({Modification.REPLACES_CONTINGENCY_NCF}),
    reason=False,
    services_only=True,
    special=income_tax_withheld,
)
```


`services/fiscal-do/src/ledger_fiscal_do/domain/registry.py`:

```python
"""The registry of the types of e-CF: each type's rules by its code."""

from types import MappingProxyType
from typing import Final
lazy from collections.abc import Mapping

from ledger_fiscal_do.domain.types import (
    consumer_invoice,
    credit_invoice,
    credit_note,
    debit_note,
    exports,
    foreign_payments,
    government,
    minor_expenses,
    purchases,
    special_regimes,
)
lazy from ledger_fiscal_do.domain.types.base import TypeRules
lazy from ledger_fiscal_do.domain.vocabulary import EcfType

RULES: Final[Mapping[EcfType, TypeRules]] = MappingProxyType(
    {
        rules.ecf_type: rules
        for rules in (
            credit_invoice.RULES,
            consumer_invoice.RULES,
            debit_note.RULES,
            credit_note.RULES,
            purchases.RULES,
            minor_expenses.RULES,
            special_regimes.RULES,
            government.RULES,
            exports.RULES,
            foreign_payments.RULES,
        )
    }
)
"""Each type's rules by its code: the strategy registry (``test_types`` holds it to every type)."""
```


`services/fiscal-do/src/ledger_fiscal_do/domain/checking.py`:

```python
"""A document checked whole, before it is numbered: each value's form, then its type's rules.

``checked`` returns the document's totals or raises ``DocumentInvalidError`` with every problem found. The
form comes first: the type's rules and the totals read amounts whose form holds, and a document whose lines
are malformed is refused for them before its type is consulted.
"""

from typing import Final

from ledger_fiscal_do.domain import form, totals
from ledger_fiscal_do.domain.problems import Problem, ProblemCode, require_no_problems
from ledger_fiscal_do.domain.registry import RULES
lazy from ledger_fiscal_do.domain.content import Buyer, Content, Line, Reference
lazy from ledger_fiscal_do.domain.form import Problems
lazy from ledger_fiscal_do.domain.totals import Totals

NAME: Final = 150
ITEM_NAME: Final = 80
DESCRIPTION: Final = 1000
ADDRESS: Final = 100
CONTACT: Final = 80
EMAIL: Final = 80
FOREIGN_ID: Final = 20
COUNTRY: Final = 60
REASON: Final = 90


def _buyer(problems: Problems, buyer: Buyer) -> None:
    form.text(problems, "buyer.foreign_id", buyer.foreign_id, limit=FOREIGN_ID)
    form.text(problems, "buyer.name", buyer.name, limit=NAME)
    form.text(problems, "buyer.contact", buyer.contact, limit=CONTACT)
    form.text(problems, "buyer.email", buyer.email, limit=EMAIL)
    form.text(problems, "buyer.address", buyer.address, limit=ADDRESS)
    form.area_code(problems, "buyer.municipality", buyer.municipality)
    form.area_code(problems, "buyer.province", buyer.province)
    form.text(problems, "buyer.country", buyer.country, limit=COUNTRY)


def _line(problems: Problems, where: str, line: Line) -> None:
    form.text(problems, f"{where}.name", line.name, limit=ITEM_NAME)
    form.text(problems, f"{where}.description", line.description, limit=DESCRIPTION)
    form.quantity(problems, f"{where}.quantity", line.quantity)
    form.unit_price(problems, f"{where}.unit_price", line.unit_price)
    form.amount(problems, f"{where}.discount", line.discount)
    if line.withholding is not None:
        form.amount(problems, f"{where}.withholding.itbis", line.withholding.itbis)
        form.amount(problems, f"{where}.withholding.isr", line.withholding.isr)
    if problems:
        return
    if totals.gross(line) >= form.CEILING:
        problems.append(Problem(where, ProblemCode.AMOUNT_TOO_LARGE))
    elif line.discount is not None and line.discount.amount > totals.gross(line):
        problems.append(Problem(f"{where}.discount", ProblemCode.DISCOUNT_EXCEEDS_AMOUNT))


def _lines(problems: Problems, lines: tuple[Line, ...]) -> None:
    if not lines:
        problems.append(Problem("lines", ProblemCode.LINES_EMPTY))
    elif len(lines) > form.MAX_LINES:
        problems.append(Problem("lines", ProblemCode.TOO_MANY))
        return
    for index, line in enumerate(lines):
        where = f"lines[{index}]"
        if line.number != index + 1:
            problems.append(Problem(f"{where}.number", ProblemCode.LINES_NOT_NUMBERED))
        found: Problems = []
        _line(found, where, line)
        problems.extend(found)


def _reference(problems: Problems, reference: Reference) -> None:
    form.ncf(problems, "reference.modified", reference.modified)
    form.text(problems, "reference.reason", reference.reason, limit=REASON)


def form_problems(content: Content) -> tuple[Problem, ...]:
    """Every problem of form in ``content``, whatever its type."""
    problems: Problems = []
    _lines(problems, content.lines)
    if len(content.payments) > form.MAX_PAYMENTS:
        problems.append(Problem("payments", ProblemCode.TOO_MANY))
    for index, payment in enumerate(content.payments):
        form.amount(problems, f"payments[{index}].amount", payment.amount)
    if content.buyer is not None:
        _buyer(problems, content.buyer)
    if content.reference is not None:
        _reference(problems, content.reference)
    return tuple(problems)


def checked(content: Content) -> Totals:
    """The totals of ``content``, once its form and its type's rules hold; ``DocumentInvalidError`` if not."""
    require_no_problems(form_problems(content))
    computed = totals.compute(content)
    require_no_problems(RULES[content.ecf_type].check(content, computed))
    return computed
```


- [ ] **Step 6: Implement the life of documents, ranges, annulments and profiles**

`services/fiscal-do/src/ledger_fiscal_do/domain/events.py`:

```python
"""What fiscal-do announces (§4.1): the facts other services build on, as the domain states them.

A document's events carry its version, a sequence's its version, so that a consumer can order them. Two
events are fiscal-do's own business as much as anyone's: it reacts to ``DocumentNumbered`` and to
``AnnulmentRequested`` itself, by starting their workflows (Decision 7 of the plan).
"""

from dataclasses import dataclass
from typing import final
lazy from datetime import date

lazy from ledger_fiscal_do.domain.encf import Encf
lazy from ledger_fiscal_do.domain.ids import AnnulmentId, DocumentId, SequenceId
lazy from ledger_fiscal_do.domain.verdict import DgiiMessage, Verdict
lazy from ledger_fiscal_do.domain.vocabulary import DgiiEnvironment, EcfType
lazy from ledger_kernel.ids import Tenant
lazy from ledger_kernel.money import Money


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class DocumentNumbered:
    """A document has its e-NCF: it will be signed and sent."""

    tenant: Tenant
    document: DocumentId
    encf: Encf
    version: int
    total: Money
    environment: DgiiEnvironment


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class DocumentAccepted:
    """DGII accepted the document."""

    tenant: Tenant
    document: DocumentId
    encf: Encf
    version: int


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class DocumentConditionallyAccepted:
    """DGII accepted the document, with observations to correct in later ones."""

    tenant: Tenant
    document: DocumentId
    encf: Encf
    version: int
    messages: tuple[DgiiMessage, ...]


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class DocumentRejected:
    """DGII rejected the document: its e-NCF stays used, the client issues a new one."""

    tenant: Tenant
    document: DocumentId
    encf: Encf
    version: int
    messages: tuple[DgiiMessage, ...]


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class SequenceLow:
    """A sequence has used 80 % of its numbers: time to ask DGII for the next range."""

    tenant: Tenant
    sequence: SequenceId
    ecf_type: EcfType
    remaining: int
    version: int


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class SequenceExpiring:
    """A sequence expires within ``days_left`` days (30, 7 or 1), each announced once."""

    tenant: Tenant
    sequence: SequenceId
    ecf_type: EcfType
    expires_on: date
    days_left: int


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class AnnulmentRequested:
    """Unused numbers of a sequence were withdrawn from use; DGII is to be told (ANECF)."""

    tenant: Tenant
    annulment: AnnulmentId
    sequence: SequenceId
    first: Encf
    last: Encf


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class AnnulmentDecided:
    """DGII answered an annulment."""

    tenant: Tenant
    annulment: AnnulmentId
    verdict: Verdict
    messages: tuple[DgiiMessage, ...]


type DocumentEvent = DocumentNumbered | DocumentAccepted | DocumentConditionallyAccepted | DocumentRejected
type FiscalEvent = DocumentEvent | SequenceLow | SequenceExpiring | AnnulmentRequested | AnnulmentDecided
```


`services/fiscal-do/src/ledger_fiscal_do/domain/document.py`:

```python
"""The fiscal document: an e-CF from its number to DGII's final answer, through an explicit state machine.

::

    draft ──► numbered ──► signed ──► sent ──► accepted | accepted_conditionally | rejected
                 │  ▲         │  ▲      ▲
                 ▼  │         ▼  │      │
          blocked_certificate ◄─ contingency

* **draft**: checked, not numbered: it lives only inside the transaction that numbers it (``Draft``);
* **numbered**: its e-NCF is taken from its sequence, in the same transaction as the document is stored;
* **signed**: vault signed it, its security code is known, the signed XML is in the archive (WORM);
* **blocked_certificate**: the company has no usable certificate; it resumes where it stood once one is;
* **contingency**: DGII could not be reached to send it; it may be handed over meanwhile, and is sent as soon
  as DGII answers (DGII's e-CF technical report v1.0, §19: within 72 hours);
* **sent**: DGII holds it (a track id; a summary is answered at once and decided in the same step);
* **accepted**, **accepted_conditionally**, **rejected**: DGII's final answer, kept verbatim. A rejected
  document's e-NCF stays used: the client corrects with a new document.

Any move not drawn is impossible (``IllegalTransitionError``); each move adds one to the version, which the
document's events carry so that consumers can order them.
"""

from dataclasses import dataclass, field, replace
from enum import StrEnum
from types import MappingProxyType
from typing import ClassVar, Final, Self, final
lazy from collections.abc import Mapping
lazy from datetime import date, datetime
lazy from uuid import UUID

from ledger_fiscal_do.domain.events import (
    DocumentAccepted,
    DocumentConditionallyAccepted,
    DocumentNumbered,
    DocumentRejected,
)
from ledger_fiscal_do.domain.verdict import Verdict
from ledger_kernel.errors import ConflictError, NotFoundError
lazy from ledger_fiscal_do.domain.content import Content, Issuer
lazy from ledger_fiscal_do.domain.encf import Encf
lazy from ledger_fiscal_do.domain.ids import DocumentId, EmissionPointId, SequenceId, TrackId
lazy from ledger_fiscal_do.domain.totals import Totals
lazy from ledger_fiscal_do.domain.verdict import Outcome
lazy from ledger_fiscal_do.domain.vocabulary import DgiiEnvironment
lazy from ledger_kernel.ids import Tenant


class DocumentState(StrEnum):
    """Where a fiscal document stands (see the module)."""

    DRAFT = "draft"
    NUMBERED = "numbered"
    SIGNED = "signed"
    BLOCKED_CERTIFICATE = "blocked_certificate"
    CONTINGENCY = "contingency"
    SENT = "sent"
    ACCEPTED = "accepted"
    ACCEPTED_CONDITIONALLY = "accepted_conditionally"
    REJECTED = "rejected"


FINAL: Final = frozenset(
    {DocumentState.ACCEPTED, DocumentState.ACCEPTED_CONDITIONALLY, DocumentState.REJECTED}
)
TRANSITIONS: Final[Mapping[DocumentState, frozenset[DocumentState]]] = MappingProxyType(
    {
        DocumentState.DRAFT: frozenset({DocumentState.NUMBERED}),
        DocumentState.NUMBERED: frozenset({DocumentState.SIGNED, DocumentState.BLOCKED_CERTIFICATE}),
        DocumentState.BLOCKED_CERTIFICATE: frozenset({DocumentState.NUMBERED, DocumentState.SIGNED}),
        DocumentState.SIGNED: frozenset(
            {
                DocumentState.SENT,
                DocumentState.CONTINGENCY,
                DocumentState.BLOCKED_CERTIFICATE,
            }
        ),
        DocumentState.CONTINGENCY: frozenset({DocumentState.SENT, DocumentState.BLOCKED_CERTIFICATE}),
        DocumentState.SENT: FINAL,
        DocumentState.ACCEPTED: frozenset(),
        DocumentState.ACCEPTED_CONDITIONALLY: frozenset(),
        DocumentState.REJECTED: frozenset(),
    }
)
"""Every move a document may make; anything else is ``IllegalTransitionError``."""


class IllegalTransitionError(ConflictError):
    """A move the state machine does not allow: the document is not where the caller thinks."""

    code: ClassVar[str] = "illegal_transition"

    def __init__(self, current: DocumentState, target: DocumentState) -> None:
        super().__init__(f"a document that is {current.value} cannot become {target.value}")


class DocumentNotFoundError(NotFoundError):
    """No fiscal document by this id (or e-NCF) in the company."""

    code: ClassVar[str] = "document_not_found"

    def __init__(self, document: UUID | str) -> None:
        super().__init__(f"document {document} was not found")


def require_move(current: DocumentState, target: DocumentState) -> None:
    """Return when the state machine allows ``current`` to become ``target``."""
    if target not in TRANSITIONS[current]:
        raise IllegalTransitionError(current, target)


_STATE_OF: Final[Mapping[Verdict, DocumentState]] = MappingProxyType(
    {
        Verdict.ACCEPTED: DocumentState.ACCEPTED,
        Verdict.ACCEPTED_CONDITIONALLY: DocumentState.ACCEPTED_CONDITIONALLY,
        Verdict.REJECTED: DocumentState.REJECTED,
    }
)


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Signature:
    """What signing a document left: when, its security code, and where its signed XML is kept."""

    signed_at: datetime
    """``FechaHoraFirma``: written into the document just before vault signed it."""
    security_code: str
    """The first six characters of the signature's value: printed, and in the QR link."""
    archive_key: str
    sha256: str
    """Of the signed document, lowercase hexadecimal: the index's proof of what the archive holds."""
    summary: bool
    """Sent as a consumer invoice's summary (RFCE), not whole."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Draft:
    """A document checked (``ledger_fiscal_do.domain.checking``) and not yet numbered."""

    id: DocumentId
    tenant: Tenant
    emission_point: EmissionPointId
    environment: DgiiEnvironment
    """DGII's environment it goes to: the company's when it is issued, wherever the company moves later."""
    issuer: Issuer = field(repr=False)
    content: Content = field(repr=False)
    totals: Totals = field(repr=False)
    created_at: datetime

    @property
    def state(self) -> DocumentState:
        """A draft is a draft."""
        return DocumentState.DRAFT

    def numbered(
        self, encf: Encf, sequence: SequenceId, expires_on: date | None, *, now: datetime
    ) -> tuple[FiscalDocument, DocumentNumbered]:
        """The document with the e-NCF taken from ``sequence``, announced."""
        require_move(self.state, DocumentState.NUMBERED)
        document = FiscalDocument(
            id=self.id,
            tenant=self.tenant,
            emission_point=self.emission_point,
            environment=self.environment,
            issuer=self.issuer,
            content=self.content,
            totals=self.totals,
            encf=encf,
            sequence=sequence,
            sequence_expires_on=expires_on,
            state=DocumentState.NUMBERED,
            version=1,
            created_at=self.created_at,
            updated_at=now,
        )
        numbered = DocumentNumbered(
            tenant=self.tenant,
            document=self.id,
            encf=encf,
            version=document.version,
            total=self.totals.total,
            environment=self.environment,
        )
        return document, numbered


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class FiscalDocument:
    """An e-CF of one company, numbered, at its current state (see the module)."""

    id: DocumentId
    tenant: Tenant
    emission_point: EmissionPointId
    environment: DgiiEnvironment
    issuer: Issuer = field(repr=False)
    content: Content = field(repr=False)
    totals: Totals = field(repr=False)
    encf: Encf
    sequence: SequenceId
    sequence_expires_on: date | None
    state: DocumentState
    version: int
    created_at: datetime
    updated_at: datetime
    signature: Signature | None = None
    track_id: TrackId | None = None
    outcome: Outcome | None = None
    blocked_from: DocumentState | None = None
    contingency_since: datetime | None = None

    @property
    def final(self) -> bool:
        """Whether DGII has given its final answer."""
        return self.state in FINAL

    def _moved(self, target: DocumentState, now: datetime) -> Self:
        require_move(self.state, target)
        return replace(self, state=target, version=self.version + 1, updated_at=now)

    def signed(self, signature: Signature, *, now: datetime) -> Self:
        """Signed by vault and archived."""
        return replace(self._moved(DocumentState.SIGNED, now), signature=signature, blocked_from=None)

    def blocked(self, *, now: datetime) -> Self:
        """The company has no usable certificate: it waits for one, remembering where it stood."""
        if self.state is DocumentState.BLOCKED_CERTIFICATE:
            return self
        return replace(self._moved(DocumentState.BLOCKED_CERTIFICATE, now), blocked_from=self.state)

    def resumed(self, *, now: datetime) -> Self:
        """A certificate is usable again: back where it stood (numbered, or signed)."""
        if self.state is not DocumentState.BLOCKED_CERTIFICATE:
            return self
        back = DocumentState.SIGNED if self.signature is not None else DocumentState.NUMBERED
        return replace(self._moved(back, now), blocked_from=None)

    def unreachable(self, *, now: datetime) -> Self:
        """DGII could not be reached to send it: in contingency, since the first time."""
        if self.state is DocumentState.CONTINGENCY:
            return self
        return replace(self._moved(DocumentState.CONTINGENCY, now), contingency_since=now)

    def sent(self, track_id: TrackId | None, *, now: datetime) -> Self:
        """DGII holds it: a track id to ask for its answer by, or none for a summary (answered at once)."""
        return replace(self._moved(DocumentState.SENT, now), track_id=track_id)

    def decided(
        self, outcome: Outcome, *, now: datetime
    ) -> tuple[Self, DocumentAccepted | DocumentConditionallyAccepted | DocumentRejected]:
        """DGII's final answer: the document's last state, announced."""
        document = replace(self._moved(_STATE_OF[outcome.verdict], now), outcome=outcome)
        tenant, encf, version = self.tenant, self.encf, document.version
        if outcome.verdict is Verdict.ACCEPTED:
            return document, DocumentAccepted(tenant=tenant, document=self.id, encf=encf, version=version)
        if outcome.verdict is Verdict.ACCEPTED_CONDITIONALLY:
            conditional = DocumentConditionallyAccepted(
                tenant=tenant, document=self.id, encf=encf, version=version, messages=outcome.messages
            )
            return document, conditional
        rejected = DocumentRejected(
            tenant=tenant, document=self.id, encf=encf, version=version, messages=outcome.messages
        )
        return document, rejected
```


`services/fiscal-do/src/ledger_fiscal_do/domain/sequence.py`:

```python
"""The e-NCF ranges DGII authorized a company, and the numbers taken from them, none skipped silently.

A company asks DGII (in its virtual office) for a range of numbers per type of e-CF, and registers here each
range it obtained: ``EncfSequence``, for one type and one of its emission points. Numbers are taken in order
(``take``), each exactly once, under the sequence's row lock, in the transaction that stores the document
that gets it (``test_repositories`` proves two issues at once never share one). A range expires on its date
(DGII: "estará vigente desde la fecha de autorización hasta el 31 de diciembre del año siguiente"; consumer
invoices and credit notes have no expiry in TesteCF and none is required), after which nothing is taken.

Numbers never used can be withdrawn (``annul``): DGII is told with an ANECF, and numbering skips them. Only
numbers not yet taken: a number taken belongs to a document, which DGII must receive.

When 80 % of a range's numbers are gone, ``SequenceLow`` says so once: time to ask DGII for the next range.
"""

from dataclasses import dataclass, replace
from typing import ClassVar, Final, Self, final
lazy from datetime import date, datetime
lazy from uuid import UUID

from ledger_fiscal_do.domain.encf import Encf, require_number
from ledger_fiscal_do.domain.events import SequenceLow
from ledger_kernel.errors import ConflictError, InvalidInputError, NotFoundError
lazy from ledger_fiscal_do.domain.ids import EmissionPointId, SequenceId
lazy from ledger_fiscal_do.domain.vocabulary import EcfType
lazy from ledger_kernel.ids import Tenant

LOW_SHARE: Final = 0.8
"""What share of its numbers a range has used when it is announced as running low."""
MAX_ANNULLED_RANGES: Final = 100
"""How many withdrawn ranges a sequence keeps (each an ANECF; more is a sign of something else wrong)."""


class InvalidRangeError(InvalidInputError):
    """A range whose first number is after its last."""

    code: ClassVar[str] = "invalid_sequence_range"

    def __init__(self, first: int, last: int) -> None:
        super().__init__(f"a range runs from its first number to its last, not from {first} to {last}")


class SequenceExpiryError(InvalidInputError):
    """A range dated when its type's ranges are not, or undated when they are.

    DGII dates every range it authorizes but those of consumer invoices (32) and credit notes (34), and an
    e-CF of a dated type carries its range's date (``FechaVencimientoSecuencia``), which its schema requires.
    """

    code: ClassVar[str] = "sequence_expiry_invalid"

    def __init__(self, ecf_type: EcfType, *, dated: bool) -> None:
        wanted = "need an expiry date" if dated else "take no expiry date"
        super().__init__(f"ranges of type {int(ecf_type)} {wanted}")


class SequenceOverlapError(ConflictError):
    """A range that shares numbers with one the company already registered for the type."""

    code: ClassVar[str] = "sequence_overlap"

    def __init__(self) -> None:
        super().__init__("the range shares numbers with a range already registered for this type of e-CF")


class NoSequenceError(ConflictError):
    """No range of the type at the emission point has a number left, valid at the date."""

    code: ClassVar[str] = "no_sequence"

    def __init__(self, ecf_type: EcfType) -> None:
        super().__init__(f"no registered range of type {ecf_type.value} has an unexpired number left here")


class SequenceNotFoundError(NotFoundError):
    """No sequence by this id in the company."""

    code: ClassVar[str] = "sequence_not_found"

    def __init__(self, sequence: UUID) -> None:
        super().__init__(f"sequence {sequence} was not found")


class AnnulmentRangeError(ConflictError):
    """Numbers to withdraw that are not all unused numbers of the range."""

    code: ClassVar[str] = "annulment_range_unavailable"

    def __init__(self) -> None:
        super().__init__("only numbers of the range not yet taken, nor already withdrawn, can be annulled")


@final
@dataclass(frozen=True, slots=True, order=True)
class NumberRange:
    """Numbers from ``first`` to ``last``, both included."""

    first: int
    last: int

    def __post_init__(self) -> None:
        require_number(self.first)
        require_number(self.last)
        if self.first > self.last:
            raise InvalidRangeError(self.first, self.last)

    @property
    def size(self) -> int:
        """How many numbers it holds."""
        return self.last - self.first + 1

    def holds(self, number: int) -> bool:
        """Whether ``number`` is in it."""
        return self.first <= number <= self.last

    def overlaps(self, other: NumberRange) -> bool:
        """Whether the two share a number."""
        return self.first <= other.last and other.first <= self.last


def _skipping(number: int, annulled: tuple[NumberRange, ...]) -> int:
    """``number``, or the first after it that no withdrawn range holds."""
    for withdrawn in sorted(annulled):
        if withdrawn.holds(number):
            number = withdrawn.last + 1
    return number


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class EncfSequence:
    """A range of e-NCF of one type, at one emission point of one company, and how far it has gone."""

    id: SequenceId
    tenant: Tenant
    ecf_type: EcfType
    emission_point: EmissionPointId
    numbers: NumberRange
    expires_on: date | None
    next: int
    """The next number to take; past ``numbers.last`` when it is exhausted."""
    annulled: tuple[NumberRange, ...] = ()
    low_announced: bool = False
    version: int = 1
    created_at: datetime

    @classmethod
    def registered(
        cls,
        identity: tuple[SequenceId, Tenant, EmissionPointId],
        *,
        ecf_type: EcfType,
        numbers: NumberRange,
        expires_on: date | None,
        now: datetime,
    ) -> Self:
        """A range DGII authorized, registered with nothing taken."""
        sequence, tenant, point = identity
        return cls(
            id=sequence,
            tenant=tenant,
            ecf_type=ecf_type,
            emission_point=point,
            numbers=numbers,
            expires_on=expires_on,
            next=numbers.first,
            created_at=now,
        )

    @property
    def available(self) -> int:
        """How many numbers are left to take."""
        if self.next > self.numbers.last:
            return 0
        left = NumberRange(self.next, self.numbers.last)
        withdrawn = sum(
            min(each.last, left.last) - max(each.first, left.first) + 1
            for each in self.annulled
            if each.overlaps(left)
        )
        return left.size - withdrawn

    def usable_on(self, day: date) -> bool:
        """Whether a number can be taken on ``day``: one is left, and the range has not expired."""
        return self.available > 0 and (self.expires_on is None or day <= self.expires_on)

    def take(self, *, on: date) -> tuple[Self, Encf, SequenceLow | None]:
        """The next number, the sequence after it, and the announcement that it runs low (once)."""
        if not self.usable_on(on):
            raise NoSequenceError(self.ecf_type)
        number = self.next
        taken = replace(self, next=_skipping(number + 1, self.annulled), version=self.version + 1)
        used = taken.numbers.size - taken.available
        if taken.low_announced or used < LOW_SHARE * taken.numbers.size:
            return taken, Encf(self.ecf_type, number), None
        low = replace(taken, low_announced=True)
        announcement = SequenceLow(
            tenant=self.tenant,
            sequence=self.id,
            ecf_type=self.ecf_type,
            remaining=low.available,
            version=low.version,
        )
        return low, Encf(self.ecf_type, number), announcement

    def annul(self, withdrawn: NumberRange) -> Self:
        """The sequence with ``withdrawn``'s numbers, unused, never to be taken."""
        unused = self.numbers.holds(withdrawn.first) and self.numbers.holds(withdrawn.last)
        if (
            not unused
            or withdrawn.first < self.next
            or any(each.overlaps(withdrawn) for each in self.annulled)
        ):
            raise AnnulmentRangeError
        if len(self.annulled) >= MAX_ANNULLED_RANGES:
            raise AnnulmentRangeError
        annulled = (*self.annulled, withdrawn)
        return replace(self, annulled=annulled, next=_skipping(self.next, annulled), version=self.version + 1)


def require_no_overlap(candidate: NumberRange, registered: tuple[EncfSequence, ...]) -> None:
    """Return when ``candidate`` shares no number with the type's registered ranges (all emission points)."""
    if any(sequence.numbers.overlaps(candidate) for sequence in registered):
        raise SequenceOverlapError


EXPIRY_THRESHOLDS: Final = (30, 7, 1)
"""Days before a range expires at which it is announced, the widest first, each once."""


def due_expiry_announcement(expires_on: date, today: date, announced: int | None) -> int | None:
    """The announcement due on ``today`` for a range expiring on ``expires_on``, if any.

    The smallest threshold reached, unless ``announced`` (the smallest made so far) covers it already: a range
    registered five days before it expires is announced at 7 days, never at 30 as well.
    """
    left = (expires_on - today).days
    reached = [days for days in EXPIRY_THRESHOLDS if left <= days]
    if not reached:
        return None
    due = min(reached)
    return None if announced is not None and announced <= due else due
```


`services/fiscal-do/src/ledger_fiscal_do/domain/annulment.py`:

```python
"""The annulment of unused e-NCF (ANECF): numbers withdrawn from a sequence, and DGII told so.

The numbers are withdrawn at once, in the transaction that records the request, so that numbering never
takes them; DGII is told afterwards, by a workflow (``AnnulRange``), and its answer recorded. A refused
annulment leaves the numbers withdrawn (they are never reused silently): its state says so, with DGII's
messages, for the company to settle with DGII. DGII's certification environment (CerteCF) offers no
annulment service (*Descripción Técnica Servicios DGII*, Anulación de e-NCF): none is asked there.
"""

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import ClassVar, Self, final
lazy from datetime import datetime
lazy from uuid import UUID

from ledger_fiscal_do.domain.encf import Encf
from ledger_fiscal_do.domain.events import AnnulmentDecided, AnnulmentRequested
from ledger_fiscal_do.domain.verdict import Verdict
from ledger_fiscal_do.domain.vocabulary import DgiiEnvironment
from ledger_kernel.errors import ConflictError, NotFoundError
lazy from ledger_fiscal_do.domain.ids import AnnulmentId, SequenceId
lazy from ledger_fiscal_do.domain.sequence import EncfSequence, NumberRange
lazy from ledger_fiscal_do.domain.verdict import Outcome
lazy from ledger_fiscal_do.domain.vocabulary import EcfType
lazy from ledger_kernel.countries.do import TaxId
lazy from ledger_kernel.ids import Tenant


class AnnulmentState(StrEnum):
    """Where an annulment stands."""

    REQUESTED = "requested"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class AnnulmentNotFoundError(NotFoundError):
    """No annulment by this id in the company."""

    code: ClassVar[str] = "annulment_not_found"

    def __init__(self, annulment: UUID) -> None:
        super().__init__(f"annulment {annulment} was not found")


class AnnulmentDecidedError(ConflictError):
    """DGII's answer on an annulment is recorded already."""

    code: ClassVar[str] = "annulment_decided"

    def __init__(self) -> None:
        super().__init__("DGII has answered this annulment already")


class AnnulmentUnavailableError(ConflictError):
    """DGII's certification environment has no annulment service."""

    code: ClassVar[str] = "annulment_unavailable"

    def __init__(self) -> None:
        super().__init__("DGII's certification environment (CerteCF) annuls no e-NCF")


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Annulment:
    """Unused numbers of one sequence, withdrawn, and what DGII said of it."""

    id: AnnulmentId
    tenant: Tenant
    sequence: SequenceId
    ecf_type: EcfType
    numbers: NumberRange
    issuer: TaxId
    environment: DgiiEnvironment
    state: AnnulmentState
    requested_at: datetime
    outcome: Outcome | None = None

    @classmethod
    def requested(
        cls,
        annulment: AnnulmentId,
        sequence: EncfSequence,
        numbers: NumberRange,
        *,
        issuer: tuple[TaxId, DgiiEnvironment],
        now: datetime,
    ) -> tuple[Self, EncfSequence, AnnulmentRequested]:
        """``numbers`` withdrawn from ``sequence`` (``AnnulmentRangeError`` if one is used), to tell DGII.

        ``issuer``: the company's RNC and the environment DGII is told in.
        """
        tax_id, environment = issuer
        if environment is DgiiEnvironment.CERTIFICATION:
            raise AnnulmentUnavailableError
        withdrawn = sequence.annul(numbers)
        recorded = cls(
            id=annulment,
            tenant=sequence.tenant,
            sequence=sequence.id,
            ecf_type=sequence.ecf_type,
            numbers=numbers,
            issuer=tax_id,
            environment=environment,
            state=AnnulmentState.REQUESTED,
            requested_at=now,
        )
        event = AnnulmentRequested(
            tenant=sequence.tenant,
            annulment=annulment,
            sequence=sequence.id,
            first=Encf(sequence.ecf_type, numbers.first),
            last=Encf(sequence.ecf_type, numbers.last),
        )
        return recorded, withdrawn, event

    def decided(self, outcome: Outcome) -> tuple[Self, AnnulmentDecided]:
        """DGII's answer; a conditional acceptance is an acceptance."""
        if self.state is not AnnulmentState.REQUESTED:
            raise AnnulmentDecidedError
        state = AnnulmentState.REJECTED if outcome.verdict is Verdict.REJECTED else AnnulmentState.ACCEPTED
        decided = replace(self, state=state, outcome=outcome)
        event = AnnulmentDecided(
            tenant=self.tenant, annulment=self.id, verdict=outcome.verdict, messages=outcome.messages
        )
        return decided, event
```


`services/fiscal-do/src/ledger_fiscal_do/domain/profile.py`:

```python
"""A company's fiscal profile: what its e-CF say of it beyond its RNC and name, and its DGII environment.

identity owns the company's RNC and legal name; fiscal-do keeps what only e-CF need (the address DGII
requires, the trade name, phones, email) and which of DGII's environments the company emits in: TesteCF
while it integrates, CerteCF while it certifies, eCF in production. Each document takes the environment it was
issued in, so moving a company to production never sends an old test document there.
"""

from dataclasses import dataclass, field
from typing import ClassVar, Final, Self, final
lazy from datetime import datetime

from ledger_fiscal_do.domain import form
from ledger_fiscal_do.domain.content import Issuer
from ledger_fiscal_do.domain.problems import Problem, ProblemCode, ProfileInvalidError, require_no_problems
from ledger_kernel.errors import ConflictError
lazy from ledger_fiscal_do.domain.form import Problems
lazy from ledger_fiscal_do.domain.vocabulary import DgiiEnvironment
lazy from ledger_kernel.countries.do import TaxId
lazy from ledger_kernel.ids import Tenant

TRADE_NAME: Final = 150
LEGAL_NAME: Final = 150
ADDRESS: Final = 100
BRANCH: Final = 20
EMAIL: Final = 80


class ProfileMissingError(ConflictError):
    """The company has no fiscal profile: it issues nothing until it has one."""

    code: ClassVar[str] = "fiscal_profile_missing"

    def __init__(self) -> None:
        super().__init__("the company has no fiscal profile yet: PUT /v1/do/profile first")


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class FiscalProfile:
    """A company's fiscal profile, at its version."""

    tenant: Tenant
    environment: DgiiEnvironment
    address: str = field(repr=False)
    trade_name: str | None = field(default=None, repr=False)
    branch: str | None = None
    municipality: str | None = None
    province: str | None = None
    phones: tuple[str, ...] = field(default=(), repr=False)
    email: str | None = field(default=None, repr=False)
    version: int = 1
    updated_at: datetime

    def __post_init__(self) -> None:
        problems: Problems = []
        form.text(problems, "address", self.address, limit=ADDRESS)
        form.text(problems, "trade_name", self.trade_name, limit=TRADE_NAME)
        form.text(problems, "branch", self.branch, limit=BRANCH)
        form.area_code(problems, "municipality", self.municipality)
        form.area_code(problems, "province", self.province)
        if len(self.phones) > form.MAX_PHONES:
            problems.append(Problem("phones", ProblemCode.TOO_MANY))
        for index, phone in enumerate(self.phones):
            form.phone(problems, f"phones[{index}]", phone)
        form.text(problems, "email", self.email, limit=EMAIL)
        require_no_problems(tuple(problems), ProfileInvalidError)

    def changed(self, replacement: Self) -> Self:
        """``replacement`` as the next version of this profile."""
        return type(self)(
            tenant=self.tenant,
            environment=replacement.environment,
            address=replacement.address,
            trade_name=replacement.trade_name,
            branch=replacement.branch,
            municipality=replacement.municipality,
            province=replacement.province,
            phones=replacement.phones,
            email=replacement.email,
            version=self.version + 1,
            updated_at=replacement.updated_at,
        )

    def issuer(self, tax_id: TaxId, legal_name: str) -> Issuer:
        """The issuer an e-CF of the company names, with its RNC and legal name as identity holds them."""
        return Issuer(
            tax_id=tax_id,
            legal_name=legal_name[:LEGAL_NAME],
            trade_name=self.trade_name,
            branch=self.branch,
            address=self.address,
            municipality=self.municipality,
            province=self.province,
            phones=self.phones,
            email=self.email,
        )
```


- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest services/fiscal-do/tests -q --no-cov`
Expected: PASS (the hypothesis properties of `test_totals` run 100 examples each).

- [ ] **Step 8: Gate**

Run: `uv run poe fmt && uv run poe check`
Expected: every step passes, coverage 100 %.

- [ ] **Step 9: Commit**

```bash
git add pyproject.toml uv.lock services/fiscal-do
git commit -m "feat(fiscal-do): the domain: e-CF values and totals, ten types' rules, documents, ranges, annulments

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: DGII's XML written and checked, the QR link and the security code; the application's ports

**Files:**
- Create: `services/fiscal-do/src/ledger_fiscal_do/application/__init__.py`, `application/ports.py`, `adapters/__init__.py`, `adapters/xml/__init__.py`, `adapters/xml/writer.py`, `adapters/xml/formats.py`, `adapters/xml/ecf.py`, `adapters/xml/rfce.py`, `adapters/xml/anecf.py`, `adapters/xml/stamp.py`, `adapters/xml/qr.py`, `adapters/xml/documents.py`
- Create (generated by Step 4, reviewed, committed): `services/fiscal-do/tests/golden/{ecf-31,ecf-32,ecf-33,ecf-34,ecf-41,ecf-43,ecf-44,ecf-45,ecf-46,ecf-47,rfce-32,anecf}.xml`
- Modify: `services/fiscal-do/pyproject.toml`, `pyproject.toml` (root)
- Test: `services/fiscal-do/tests/test_xml.py`, `services/fiscal-do/tests/test_ports.py`

**Interfaces:**
- Consumes: `ledger_dgii` (Task 1): `Format`, `violations`, `DSIG`, `parser`, `require_readable`, `element`; the domain (Task 3).
- Produces: `application.ports` (every port of the use cases, implemented by Tasks 4-12): `Documents`, `Sequences`, `Annulments`, `Profiles`, `ArchiveKind`, `Archived`, `ArchiveIndex`, `Events`, `FiscalUnit`, `Units`, `CompanyFacts`, `Companies`, `CompanyUnknownError`, `CompanyInactiveError`, `CertificateUnavailableError`, `Signer`, `Archive`, `Failure`, `DgiiUnavailableError`, `DgiiOutcomeUnknownError`, `Received`, `Refused`, `Decision`, `Pending`, `Reception`, `Result`, `DgiiCall`, `Dgii`, `FiscalXml`, `Emissions`, `ExpiringRange`, `ExpiringRanges`, `Watched`, `Pipeline`. `adapters.xml`: `writer.Node` (`root(format, name)`, `child`, `text`, `amount`, `number`, `document`), `formats.FORMATS`, `ecf.render(document, *, signed_at)`, `rfce.render(document, *, security_code)`, `anecf.render(annulment, *, at)`, `stamp.security_code(signed)` and `SignatureValueMissingError`, `qr.QrHosts`, `qr.qr_link(document, hosts)`, `qr.ISSUED_ON`, `documents.SchemaViolationError` (`ecf_schema_invalid`), `documents.checked`, `documents.DgiiXml` (the `FiscalXml` port).

Re-check before starting: nothing of other milestones.

- [ ] **Step 1: Dependencies and layers**

`services/fiscal-do/pyproject.toml`: `dependencies = ["ledger-kernel", "ledger-dgii", "lxml>=6.1.3"]`, and `ledger-dgii = { workspace = true }` in `[tool.uv.sources]`. Root: "fiscal-do is hexagonal" `layers = ["adapters", "application", "domain"]`; the domain's forbidden list already holds `ledger_dgii` and `lxml`. Run: `uv lock && uv sync`.

- [ ] **Step 2: Write the failing test**

`services/fiscal-do/tests/test_xml.py`:

```python
from dataclasses import replace
from datetime import date
from decimal import Decimal
from functools import cache
from pathlib import Path
from typing import Final

import pytest
import xmlschema
from hypothesis import HealthCheck, given, reject, settings
from hypothesis import strategies as st

from ledger_dgii.published import Format
from ledger_dgii.schemas import corrected
from ledger_fiscal_do.adapters.xml import ecf
from ledger_fiscal_do.adapters.xml.documents import DgiiXml, SchemaViolationError
from ledger_fiscal_do.adapters.xml.formats import FORMATS
from ledger_fiscal_do.adapters.xml.qr import ISSUED_ON, QrHosts, qr_link
from ledger_fiscal_do.adapters.xml.stamp import SignatureValueMissingError, security_code
from ledger_fiscal_do.domain.annulment import Annulment
from ledger_fiscal_do.domain.content import Buyer, Payment
from ledger_fiscal_do.domain.document import Signature
from ledger_fiscal_do.domain.ids import AnnulmentId
from ledger_fiscal_do.domain.notation import local_date
from ledger_fiscal_do.domain.problems import DocumentInvalidError
from ledger_fiscal_do.domain.registry import RULES
from ledger_fiscal_do.domain.sequence import EncfSequence, NumberRange
from ledger_fiscal_do.domain.vocabulary import DgiiEnvironment, EcfType, PaymentMethod
lazy from ledger_fiscal_do.domain.content import Content, Line

from samples import CONTENTS, ISSUER, NOW, POINT, SEQUENCE, TENANT, dop, numbered, sample

GOLDEN: Final = Path(__file__).with_name("golden")
ENVIRONMENT: Final = DgiiEnvironment.PRECERTIFICATION
XML: Final = DgiiXml()
HOSTS: Final = QrHosts(ecf="https://ecf.dgii.gov.do", fc="https://fc.dgii.gov.do")
DSIG: Final = "http://www.w3.org/2000/09/xmldsig#"
SIGNED_BY_VAULT: Final = (
    f'<Signature xmlns="{DSIG}"><SignedInfo/><SignatureValue>\n  dcp+9q/xyzAB==</SignatureValue></Signature>'
).encode()
"""The shape a signature has once vault signed (its value only matters here)."""


def signed(document: bytes) -> bytes:
    """``document`` with a signature where vault puts it: last."""
    return document.replace(b"</ECF>", SIGNED_BY_VAULT + b"</ECF>").replace(
        b"</RFCE>", SIGNED_BY_VAULT + b"</RFCE>"
    )


@cache
def _second(format_: Format) -> xmlschema.XMLSchema10:
    return xmlschema.XMLSchema10(corrected(format_).decode("utf-8"))


def second_opinion(format_: Format, document: bytes) -> list[str]:
    """What ``xmlschema``, an implementation that shares no code with libxml2, says of ``document``."""
    return [
        f"{error.path}: {error.reason}" for error in _second(format_).iter_errors(document.decode("utf-8"))
    ]


@pytest.mark.parametrize("ecf_type", list(EcfType))
def test_every_type_s_sample_is_its_golden_file_and_both_validators_accept_it(ecf_type: EcfType) -> None:
    document = sample(ecf_type)
    unsigned = XML.ecf(document, signed_at=NOW)
    assert unsigned == (GOLDEN / f"ecf-{ecf_type.value}.xml").read_bytes()
    assert second_opinion(FORMATS[ecf_type], signed(unsigned)) == []
    assert XML.signed_ecf(document, signed(unsigned)) == "dcp+9q"


def test_a_summary_and_an_annulment_are_their_golden_files_and_both_validators_accept_them() -> None:
    consumer = sample(EcfType.CONSUMER_INVOICE)
    summary = XML.summary(consumer, security_code="dcp+9q")
    assert summary == (GOLDEN / "rfce-32.xml").read_bytes()
    assert second_opinion(Format.RFCE, signed(summary)) == []
    XML.signed_summary(signed(summary))
    sequence = EncfSequence.registered(
        (SEQUENCE, TENANT, POINT),
        ecf_type=EcfType.CREDIT_INVOICE,
        numbers=NumberRange(1, 100),
        expires_on=None,
        now=NOW,
    )
    annulment, _, _ = Annulment.requested(
        AnnulmentId(SEQUENCE), sequence, NumberRange(51, 100), issuer=(ISSUER.tax_id, ENVIRONMENT), now=NOW
    )
    anecf = XML.annulment(annulment, at=NOW)
    assert anecf == (GOLDEN / "anecf.xml").read_bytes()
    with_signature = anecf.replace(b"</ANECF>", SIGNED_BY_VAULT + b"</ANECF>")
    assert second_opinion(Format.ANECF, with_signature) == []
    XML.signed_annulment(with_signature)


def test_a_summary_names_its_buyer_when_there_is_one_and_lists_payments_when_there_are() -> None:
    base = replace(
        CONTENTS[EcfType.CONSUMER_INVOICE], buyer=Buyer(foreign_id="P-1", name="JANE"), payments=()
    )
    summary = XML.summary(numbered(base), security_code="abcdef")
    assert b"<IdentificadorExtranjero>P-1</IdentificadorExtranjero>" in summary
    assert b"TablaFormasPago" not in summary


def test_what_a_schema_refuses_is_answered_with_where() -> None:
    odd = numbered(
        replace(
            CONTENTS[EcfType.CREDIT_INVOICE],
            buyer=replace(CONTENTS[EcfType.CREDIT_INVOICE].buyer or Buyer(), municipality="999999"),
        )
    )
    with pytest.raises(SchemaViolationError) as caught:
        XML.ecf(odd, signed_at=NOW)
    assert caught.value.code == "ecf_schema_invalid"
    (violation,) = caught.value.violations
    assert violation.path == "/ECF/Encabezado/Comprador/MunicipioComprador"
    with pytest.raises(SchemaViolationError):
        XML.signed_ecf(sample(EcfType.CREDIT_INVOICE), XML.ecf(sample(EcfType.CREDIT_INVOICE), signed_at=NOW))


def test_the_security_code_is_the_first_six_characters_of_the_signature_value() -> None:
    assert security_code(signed(b"<ECF></ECF>")) == "dcp+9q"
    with pytest.raises(SignatureValueMissingError):
        security_code(b"<ECF/>")
    short = f'<ECF><Signature xmlns="{DSIG}"><SignatureValue>abc</SignatureValue></Signature></ECF>'.encode()
    with pytest.raises(SignatureValueMissingError):
        security_code(short)


def test_the_qr_link_is_dgii_s_with_every_value_encoded() -> None:
    signature = Signature(
        signed_at=NOW, security_code="dcp+9q", archive_key="k", sha256="0" * 64, summary=False
    )
    credit = sample(EcfType.CREDIT_INVOICE)
    assert qr_link(credit, HOSTS) is None
    assert qr_link(credit.signed(signature, now=NOW), HOSTS) == (
        "https://ecf.dgii.gov.do/testecf/consultatimbre?RncEmisor=101850043&RncComprador=131880738"
        f"&ENCF=E310000000001&{ISSUED_ON}=12-10-2026&MontoTotal=3438.87"
        "&FechaFirma=12-10-2026%2009%3A30%3A00&CodigoSeguridad=dcp%2B9q"
    )
    foreign = sample(EcfType.EXPORTS).signed(signature, now=NOW)
    assert "RncComprador" not in (qr_link(foreign, HOSTS) or "")
    summary = sample(EcfType.CONSUMER_INVOICE).signed(replace(signature, summary=True), now=NOW)
    assert qr_link(summary, HOSTS) == (
        "https://fc.dgii.gov.do/testecf/consultatimbrefc?RncEmisor=101850043&ENCF=E320000000001"
        "&MontoTotal=2360.00&CodigoSeguridad=dcp%2B9q"
    )
    assert local_date(NOW) == date(2026, 10, 12)


@st.composite
def contents(draw: st.DrawFn) -> Content:
    """A document of any type, varied within its type's rules."""
    ecf_type = draw(st.sampled_from(list(EcfType)))
    rules, base = RULES[ecf_type], CONTENTS[ecf_type]
    count = draw(st.integers(min_value=1, max_value=4))
    drawn: list[Line] = []
    for number in range(1, count + 1):
        each = replace(
            base.lines[0],
            number=number,
            billing=draw(st.sampled_from(sorted(rules.billing))),
            quantity=draw(st.decimals(min_value=Decimal("0.01"), max_value=1000, places=2)),
            unit_price=draw(st.decimals(min_value=0, max_value=100_000, places=4)),
            unit=draw(st.one_of(st.none(), st.integers(min_value=1, max_value=62))),
            description=draw(st.one_of(st.none(), st.text(alphabet="abc &<>\"'ñÁ", min_size=1, max_size=30))),
            discount=dop("0.01") if rules.discounts and draw(st.booleans()) else None,
        )
        drawn.append(each)
    payments = (
        tuple(
            Payment(PaymentMethod.CASH, dop("1.00"))
            for _ in range(draw(st.integers(min_value=0, max_value=2)))
        )
        if rules.payments
        else ()
    )
    return replace(
        base,
        lines=tuple(drawn),
        payments=payments,
        prices_include_itbis=rules.prices_include_itbis and draw(st.booleans()),
    )


@settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(content=contents(), phones=st.booleans())
def test_every_document_its_type_s_rules_accept_is_one_dgii_s_schema_accepts(
    *, content: Content, phones: bool
) -> None:
    issuer = ISSUER if phones else replace(ISSUER, phones=(), trade_name=None, email=None)
    try:
        document = numbered(content, issuer=issuer)
    except DocumentInvalidError:
        reject()
    rendered = ecf.render(document, signed_at=NOW)
    assert XML.ecf(document, signed_at=NOW) == rendered
    assert second_opinion(FORMATS[content.ecf_type], signed(rendered)) == []
```


`services/fiscal-do/tests/test_ports.py`:

```python
import pytest

from ledger_fiscal_do.application.ports import (
    CertificateUnavailableError,
    CompanyInactiveError,
    CompanyUnknownError,
    DgiiOutcomeUnknownError,
    DgiiUnavailableError,
    Failure,
)
from ledger_kernel.errors import ErrorKind
lazy from ledger_kernel.errors import LedgerError


@pytest.mark.parametrize(
    ("error", "kind", "code"),
    [
        (CompanyUnknownError(), ErrorKind.CONFLICT, "company_unknown"),
        (CompanyInactiveError(), ErrorKind.CONFLICT, "company_inactive"),
        (CertificateUnavailableError("certificate_expired"), ErrorKind.CONFLICT, "certificate_unavailable"),
        (DgiiUnavailableError(Failure.SERVER_ERROR), ErrorKind.UNAVAILABLE, "dgii_unavailable"),
        (DgiiOutcomeUnknownError("DGII's reception"), ErrorKind.UNAVAILABLE, "dgii_outcome_unknown"),
    ],
    ids=["unknown", "inactive", "certificate", "dgii", "outcome"],
)
def test_every_refusal_of_a_port_has_its_kind_and_code(
    error: LedgerError, kind: ErrorKind, code: str
) -> None:
    assert (error.kind, error.code) == (kind, code)


def test_dgii_s_failures_say_how_dgii_failed_and_an_unknown_outcome_is_a_broken_call() -> None:
    assert str(DgiiUnavailableError(Failure.UNREACHABLE)) == "DGII could not be reached"
    assert DgiiOutcomeUnknownError("DGII's reception").failure is Failure.BROKEN
```


- [ ] **Step 3: Run the test to verify it fails**

Run: `uv run pytest services/fiscal-do/tests/test_xml.py services/fiscal-do/tests/test_ports.py -q --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'ledger_fiscal_do.adapters'`.

- [ ] **Step 4: Implement**

`services/fiscal-do/src/ledger_fiscal_do/application/__init__.py`:

```python
"""fiscal-do's use cases, over ports: issuing, emission to DGII, sequences, annulments, profiles."""
```


`services/fiscal-do/src/ledger_fiscal_do/application/ports.py`:

```python
"""What fiscal-do's use cases need of the world.

Two kinds of port, as in vault. Those of a unit of work (``FiscalUnit``): fiscal-do's state as one transaction
scoped to one company sees it, through repositories that name that company in every statement while row-level
security enforces it again. And those that work around a unit, never inside one, so that no transaction stays
open across a network call: identity (``Companies``), vault (``Signer``), the archive (``Archive``), DGII
(``Dgii``), the workflows (``Emissions``). ``FiscalXml`` writes and checks DGII's formats: CPU only.

The methods are abstract so that they need no body: a ``...`` is a statement no test could reach.
"""

from abc import ABCMeta, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import ClassVar, Protocol, final
lazy from collections.abc import Sequence
lazy from contextlib import AbstractAsyncContextManager
lazy from datetime import date, datetime

from ledger_kernel.errors import ConflictError, UnavailableError
lazy from ledger_fiscal_do.domain.annulment import Annulment
lazy from ledger_fiscal_do.domain.document import DocumentState, FiscalDocument
lazy from ledger_fiscal_do.domain.encf import Encf
lazy from ledger_fiscal_do.domain.events import FiscalEvent
lazy from ledger_fiscal_do.domain.ids import AnnulmentId, DocumentId, EmissionPointId, SequenceId, TrackId
lazy from ledger_fiscal_do.domain.profile import FiscalProfile
lazy from ledger_fiscal_do.domain.sequence import EncfSequence
lazy from ledger_fiscal_do.domain.verdict import DgiiMessage, Outcome
lazy from ledger_fiscal_do.domain.vocabulary import DgiiEnvironment, EcfType
lazy from ledger_kernel.countries.do import TaxId
lazy from ledger_kernel.ids import Tenant
lazy from ledger_kernel.money import Money
lazy from ledger_kernel.pagination import Page, PageRequest

# ── Inside a unit of work ─────────────────────────────────────────────────────────────────────────────────


class Documents(Protocol, metaclass=ABCMeta):
    """The fiscal documents of the unit's company."""

    @abstractmethod
    async def add(self, document: FiscalDocument) -> None:
        """Keep a newly numbered document."""

    @abstractmethod
    async def get(self, document: DocumentId) -> FiscalDocument | None:
        """One of the company's documents, or ``None``."""

    @abstractmethod
    async def locked(self, document: DocumentId) -> FiscalDocument:
        """One of the company's documents, locked until the unit ends (its next move waits).

        ``DocumentNotFoundError`` when the company has no such document.
        """

    @abstractmethod
    async def by_encf(self, encf: Encf) -> FiscalDocument | None:
        """The company's document of e-NCF ``encf``, or ``None``."""

    @abstractmethod
    async def update(self, document: FiscalDocument) -> None:
        """Keep ``document`` in place of its previous version."""

    @abstractmethod
    async def credited(self, encf: Encf) -> Money:
        """What the company's credit notes referring to ``encf`` total, every one not rejected."""

    @abstractmethod
    async def blocked(self) -> tuple[DocumentId, ...]:
        """The company's documents that wait for a usable certificate."""

    @abstractmethod
    async def page(self, request: PageRequest) -> Page[FiscalDocument]:
        """The company's documents, oldest first."""


class Sequences(Protocol, metaclass=ABCMeta):
    """The e-NCF ranges of the unit's company."""

    @abstractmethod
    async def lock(self) -> None:
        """Serialize the registration of ranges: the next one waits until this unit ends."""

    @abstractmethod
    async def add(self, sequence: EncfSequence) -> None:
        """Keep a newly registered range."""

    @abstractmethod
    async def get(self, sequence: SequenceId) -> EncfSequence | None:
        """One of the company's ranges, or ``None``."""

    @abstractmethod
    async def locked(self, sequence: SequenceId) -> EncfSequence | None:
        """One of the company's ranges, locked until the unit ends, or ``None``."""

    @abstractmethod
    async def of_type(self, ecf_type: EcfType) -> tuple[EncfSequence, ...]:
        """Every range of ``ecf_type`` the company registered, at every emission point."""

    @abstractmethod
    async def usable(self, ecf_type: EcfType, point: EmissionPointId, *, on: date) -> EncfSequence | None:
        """The range ``ecf_type``'s numbers at ``point`` come from on ``on``, locked; ``None`` if none.

        The oldest range with a number left that has not expired.
        """

    @abstractmethod
    async def update(self, sequence: EncfSequence) -> None:
        """Keep ``sequence`` in place of its previous version."""

    @abstractmethod
    async def page(self, request: PageRequest) -> Page[EncfSequence]:
        """The company's ranges, oldest first."""

    @abstractmethod
    async def claim_expiry(self, sequence: SequenceId, days: int) -> bool:
        """Record that the ``days`` expiry announcement of ``sequence`` is made; false when it was already."""


class Annulments(Protocol, metaclass=ABCMeta):
    """The annulments of the unit's company."""

    @abstractmethod
    async def add(self, annulment: Annulment) -> None:
        """Keep a new annulment."""

    @abstractmethod
    async def get(self, annulment: AnnulmentId) -> Annulment | None:
        """One of the company's annulments, or ``None``."""

    @abstractmethod
    async def locked(self, annulment: AnnulmentId) -> Annulment:
        """One of the company's annulments, locked until the unit ends; ``AnnulmentNotFoundError`` if none."""

    @abstractmethod
    async def update(self, annulment: Annulment) -> None:
        """Keep ``annulment`` decided."""


class Profiles(Protocol, metaclass=ABCMeta):
    """The unit's company's fiscal profile."""

    @abstractmethod
    async def get(self) -> FiscalProfile | None:
        """The profile, or ``None`` before the first."""

    @abstractmethod
    async def put(self, profile: FiscalProfile) -> bool:
        """Keep ``profile`` over the version before it (or as the first); whether it was kept."""


class ArchiveKind(StrEnum):
    """What an archived object is."""

    ECF = "ecf"
    """A signed e-CF."""
    SUMMARY = "rfce"
    """A signed summary of a consumer invoice."""
    ANNULMENT = "anecf"
    """A signed annulment."""
    ANSWER = "answer"
    """DGII's answer, as received."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Archived:
    """An object kept in the archive, as the archive answered for it."""

    key: str
    version: str
    """The store's version id of the object: with object lock, the version that is retained."""
    sha256: str
    size: int
    retain_until: datetime


class ArchiveIndex(Protocol, metaclass=ABCMeta):
    """The index of what the company's documents left in the archive (§7: PostgreSQL never the only copy)."""

    @abstractmethod
    async def add(self, owner: DocumentId | AnnulmentId, kind: ArchiveKind, archived: Archived) -> None:
        """Index ``archived`` as ``owner``'s ``kind``."""

    @abstractmethod
    async def find(self, owner: DocumentId | AnnulmentId, kind: ArchiveKind) -> Archived | None:
        """The latest object of ``kind`` indexed for ``owner``, or ``None``."""


class Events(Protocol, metaclass=ABCMeta):
    """What the unit announces when it commits."""

    @abstractmethod
    async def publish(self, event: FiscalEvent) -> None:
        """Announce ``event`` when, and only if, the unit commits (the outbox)."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class FiscalUnit:
    """One unit of work for one company: fiscal-do's state as one transaction sees it."""

    tenant: Tenant
    documents: Documents
    sequences: Sequences
    annulments: Annulments
    profiles: Profiles
    archive: ArchiveIndex
    events: Events
    now: datetime
    """The unit's time: what it records and announces happened then."""


class Units(Protocol, metaclass=ABCMeta):
    """Where units of work come from."""

    @abstractmethod
    def unit(self, tenant: Tenant) -> AbstractAsyncContextManager[FiscalUnit]:
        """A unit of work for ``tenant``'s company: it commits when the block ends normally."""


# ── Around units of work ──────────────────────────────────────────────────────────────────────────────────


@final
@dataclass(frozen=True, slots=True)
class CompanyFacts:
    """What identity holds of a company that its e-CF carry."""

    tax_id: TaxId
    legal_name: str = field(repr=False)
    active: bool


class Companies(Protocol, metaclass=ABCMeta):
    """identity's companies."""

    @abstractmethod
    async def company(self, tenant: Tenant) -> CompanyFacts:
        """The company of ``tenant``; ``CompanyUnknownError`` when identity has none."""


class CompanyUnknownError(ConflictError):
    """identity knows no such company in the organization."""

    code: ClassVar[str] = "company_unknown"

    def __init__(self) -> None:
        super().__init__("identity knows no such company")


class CompanyInactiveError(ConflictError):
    """The company is deactivated: it issues nothing."""

    code: ClassVar[str] = "company_inactive"

    def __init__(self) -> None:
        super().__init__("the company is deactivated and issues nothing")


class CertificateUnavailableError(ConflictError):
    """vault has no usable certificate for the company (none, or expired): nothing can be signed now."""

    code: ClassVar[str] = "certificate_unavailable"

    def __init__(self, reason: str) -> None:
        super().__init__(f"the company has no usable certificate ({reason})")


class Signer(Protocol, metaclass=ABCMeta):
    """vault: signs documents and DGII's authentication seed with the company's certificate."""

    @abstractmethod
    async def sign(self, tenant: Tenant, document: bytes, *, reference: str) -> bytes:
        """``document`` signed, enveloped, as DGII verifies it.

        ``CertificateUnavailableError`` when vault has no usable certificate for the company.
        """

    @abstractmethod
    async def sign_seed(self, tenant: Tenant, seed: bytes) -> bytes:
        """DGII's seed signed, to obtain a token; ``CertificateUnavailableError`` as ``sign``."""


class Archive(Protocol, metaclass=ABCMeta):
    """Object storage under object lock (WORM, compliance mode): what is kept cannot be changed or deleted."""

    @abstractmethod
    async def keep(self, key: str, content: bytes, *, retain_until: datetime) -> Archived:
        """``content`` kept at ``key`` until ``retain_until``; the same content kept again changes nothing."""

    @abstractmethod
    async def read(self, key: str) -> bytes:
        """The content kept at ``key``."""


class Failure(StrEnum):
    """How DGII failed a call."""

    UNREACHABLE = "could not be reached"
    BROKEN = "failed in transit"
    SERVER_ERROR = "answered with a server error or asked to slow down"
    UNREADABLE = "answered something that cannot be read"
    CIRCUIT_OPEN = "is not called while its circuit is open"
    NOT_OPEN = "is not called before its client is open"


class DgiiUnavailableError(UnavailableError):
    """DGII could not be reached, or failed (network, timeout, 5xx, its circuit open): try again later."""

    code: ClassVar[str] = "dgii_unavailable"

    def __init__(self, failure: Failure, service: str = "DGII") -> None:
        self.failure: Failure = failure
        super().__init__(f"{service} {failure.value}")


class DgiiOutcomeUnknownError(DgiiUnavailableError):
    """A document was on its way to DGII when the connection failed: DGII may hold it, or not."""

    code: ClassVar[str] = "dgii_outcome_unknown"

    def __init__(self, service: str) -> None:
        super().__init__(Failure.BROKEN, service)


@final
@dataclass(frozen=True, slots=True)
class Received:
    """DGII took a document in: its track id, and DGII's answer as received (for the archive)."""

    track_id: TrackId
    answer: bytes = field(repr=False)


@final
@dataclass(frozen=True, slots=True)
class Refused:
    """DGII refused a document at reception (its answer named an error and no track id)."""

    messages: tuple[DgiiMessage, ...]
    answer: bytes = field(repr=False)


@final
@dataclass(frozen=True, slots=True)
class Decision:
    """DGII's final answer, and the answer as received (for the archive)."""

    outcome: Outcome
    answer: bytes = field(repr=False)


@final
@dataclass(frozen=True, slots=True)
class Pending:
    """DGII has not validated the document yet ("En Proceso"), or does not find its track id yet."""


type Reception = Received | Refused
type Result = Decision | Pending


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class DgiiCall:
    """Who a call to DGII is for: the company (its token), its RNC, and DGII's environment."""

    tenant: Tenant
    issuer: TaxId
    environment: DgiiEnvironment


class Dgii(Protocol, metaclass=ABCMeta):
    """DGII's web services for an emitter.

    Every method raises ``DgiiUnavailableError`` when DGII cannot be reached, and authenticates the company
    first when it holds no valid token.
    """

    @abstractmethod
    async def submit_ecf(self, call: DgiiCall, encf: Encf, signed: bytes) -> Reception:
        """Send a signed e-CF; ``DgiiOutcomeUnknownError`` when it may have arrived."""

    @abstractmethod
    async def submit_summary(self, call: DgiiCall, encf: Encf, signed: bytes) -> Decision:
        """Send a signed RFCE; DGII answers it at once."""

    @abstractmethod
    async def result(self, call: DgiiCall, track_id: TrackId) -> Result:
        """DGII's answer on the document of ``track_id``."""

    @abstractmethod
    async def track_ids(self, call: DgiiCall, encf: Encf) -> tuple[TrackId, ...]:
        """The track ids DGII gave documents of ``encf``, oldest first.

        Empty where the environment has no such service (CerteCF).
        """

    @abstractmethod
    async def annul(self, call: DgiiCall, signed: bytes) -> Decision:
        """Send a signed ANECF; DGII answers it at once."""


class FiscalXml(Protocol, metaclass=ABCMeta):
    """DGII's XML formats, written and checked against DGII's schemas (CPU only, no I/O)."""

    @abstractmethod
    def ecf(self, document: FiscalDocument, *, signed_at: datetime) -> bytes:
        """``document``'s unsigned e-CF, checked as it will be once signed."""

    @abstractmethod
    def summary(self, document: FiscalDocument, *, security_code: str) -> bytes:
        """A consumer invoice's unsigned RFCE, checked as it will be once signed."""

    @abstractmethod
    def annulment(self, annulment: Annulment, *, at: datetime) -> bytes:
        """An annulment's unsigned ANECF, checked as it will be once signed."""

    @abstractmethod
    def signed_ecf(self, document: FiscalDocument, signed: bytes) -> str:
        """Check ``document``'s signed e-CF against its schema; its security code."""

    @abstractmethod
    def signed_summary(self, signed: bytes) -> None:
        """Check a signed RFCE against its schema."""

    @abstractmethod
    def signed_annulment(self, signed: bytes) -> None:
        """Check a signed ANECF against its schema."""


class Emissions(Protocol, metaclass=ABCMeta):
    """The workflows that take documents and annulments to DGII (Temporal)."""

    @abstractmethod
    async def start(self, tenant: Tenant, document: DocumentId) -> None:
        """Start ``document``'s emission; harmless when it runs or ran already (its id is the document's)."""

    @abstractmethod
    async def resume(self, tenant: Tenant, documents: Sequence[DocumentId]) -> None:
        """Tell the emissions of ``documents``, blocked on the certificate, that one may be usable now."""

    @abstractmethod
    async def annul(self, tenant: Tenant, annulment: AnnulmentId) -> None:
        """Start ``annulment``'s workflow; harmless when it runs or ran already."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class ExpiringRange:
    """A range with an expiry date, as the expiry announcements see it, outside any company."""

    sequence: SequenceId
    tenant: Tenant
    ecf_type: EcfType
    expires_on: date
    announced: int | None
    """The smallest announcement made so far (in days), or ``None``."""


class ExpiringRanges(Protocol, metaclass=ABCMeta):
    """Every company's ranges with an expiry date, read outside any company's scope."""

    @abstractmethod
    async def due(self, until: date, limit: int) -> Sequence[ExpiringRange]:
        """Up to ``limit`` ranges expiring on or before ``until`` with numbers left, soonest first."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Watched:
    """A document not yet decided, as the contingency watch sees it, outside any company."""

    document: DocumentId
    tenant: Tenant
    state: DocumentState
    since: datetime
    """When it was numbered."""


class Pipeline(Protocol, metaclass=ABCMeta):
    """Every company's documents not yet decided, read outside any company's scope (for the alert)."""

    @abstractmethod
    async def oldest(self, limit: int) -> Sequence[Watched]:
        """Up to ``limit`` documents not yet decided, oldest first."""
```


`services/fiscal-do/src/ledger_fiscal_do/adapters/__init__.py`:

```python
"""How fiscal-do reaches the world: DGII, vault, identity, PostgreSQL, the archive, the outbox, XML."""
```


`services/fiscal-do/src/ledger_fiscal_do/adapters/xml/__init__.py`:

```python
"""DGII's XML formats, written and checked against DGII's schemas.

The e-CF of every type, the summary of a consumer invoice (RFCE), the annulment of unused numbers (ANECF), and
the security code of a signed document.
"""
```


`services/fiscal-do/src/ledger_fiscal_do/adapters/xml/writer.py`:

```python
"""Writing DGII's XML: one element after another in the schema's order, never an empty one.

DGII's rule: no XML may hold an empty tag, and a tag not used is left out (*Descripción Técnica Emisores
Electrónicos*). So a field without a value is never written, and values are written as ``domain.notation``
says.

Elements are named in lowercase and written as their schema declares them (``ledger_dgii.names``): a name the
document's schema does not declare fails where it is written.

The tree is the standard library's ``ElementTree``, used to build and serialize only (it never parses here:
documents are read by ``ledger_dgii.schemas``' hardened parser): its ``Element`` is a public, typed class.
"""

from io import BytesIO
from typing import final
from xml.etree.ElementTree import Element, ElementTree, SubElement
lazy from decimal import Decimal

from ledger_dgii.names import element
from ledger_fiscal_do.domain.notation import dgii_amount, dgii_number
lazy from ledger_dgii.published import Format
lazy from ledger_kernel.money import Money


@final
class Node:
    """An element being written: its children are added in the order the schema wants them."""

    __slots__ = ("element", "format")

    def __init__(self, format_: Format, written: Element) -> None:
        self.format: Format = format_
        self.element: Element = written

    @classmethod
    def root(cls, format_: Format, name: str) -> Node:
        """A document's root element, of ``format_``'s schema."""
        return cls(format_, Element(element(format_, name)))

    def child(self, name: str) -> Node:
        """A new child element, last."""
        return Node(self.format, SubElement(self.element, element(self.format, name)))

    def text(self, name: str, value: str | None) -> None:
        """A child with ``value`` as its text; nothing when there is no value."""
        if value is not None:
            SubElement(self.element, element(self.format, name)).text = value

    def amount(self, name: str, value: Money | None) -> None:
        """A child with an amount; nothing when there is none."""
        self.text(name, None if value is None else dgii_amount(value))

    def number(self, name: str, value: Decimal | int | None) -> None:
        """A child with a number written as it is; nothing when there is none."""
        if value is not None:
            self.text(name, str(value) if isinstance(value, int) else dgii_number(value))

    def document(self) -> bytes:
        """The whole document, UTF-8, with its XML declaration."""
        written = BytesIO()
        ElementTree(self.element).write(written, encoding="utf-8", xml_declaration=True)
        return written.getvalue()
```


`services/fiscal-do/src/ledger_fiscal_do/adapters/xml/formats.py`:

```python
"""Which of DGII's schemas each document fiscal-do writes follows."""

from types import MappingProxyType
from typing import Final
lazy from collections.abc import Mapping

from ledger_dgii.published import Format
from ledger_fiscal_do.domain.vocabulary import EcfType

FORMATS: Final[Mapping[EcfType, Format]] = MappingProxyType(
    {
        EcfType.CREDIT_INVOICE: Format.ECF_31,
        EcfType.CONSUMER_INVOICE: Format.ECF_32,
        EcfType.DEBIT_NOTE: Format.ECF_33,
        EcfType.CREDIT_NOTE: Format.ECF_34,
        EcfType.PURCHASES: Format.ECF_41,
        EcfType.MINOR_EXPENSES: Format.ECF_43,
        EcfType.SPECIAL_REGIMES: Format.ECF_44,
        EcfType.GOVERNMENT: Format.ECF_45,
        EcfType.EXPORTS: Format.ECF_46,
        EcfType.FOREIGN_PAYMENTS: Format.ECF_47,
    }
)
"""The schema of each type of e-CF."""
```


`services/fiscal-do/src/ledger_fiscal_do/adapters/xml/ecf.py`:

```python
"""The e-CF of every type, written in the order of DGII's schemas (*Formato e-CF v1.0*, sections A to G).

One builder writes all ten types: their schemas are one sequence of elements in one order, each type keeping
a subset of it (verified on the vendored XSDs: every sample and generated document validates). Which elements
a document holds follows from its content, which its type's rules (``ledger_fiscal_do.domain.types``) have
checked, and from what those rules say for the layout alone: whether the type carries the sequence's expiry,
the late-credit indicator, the buyer's area even when empty. The signature is vault's to add, after
``FechaHoraFirma``.

What this milestone writes of the schemas: identification, issuer, buyer, totals (ITBIS and withholdings),
lines (codes, withholding, discounts) and the reference. Not yet (no client of milestone 4 needs them, and
the domain refuses nothing it would need them for): additional taxes (ISC, tips, CDT), other currencies,
transport and export details, informative subtotals, global discounts or surcharges, pagination.
"""

from typing import Final
lazy from datetime import datetime

from ledger_fiscal_do.adapters.xml.formats import FORMATS
from ledger_fiscal_do.adapters.xml.writer import Node
from ledger_fiscal_do.domain.notation import dgii_date, dgii_moment
from ledger_fiscal_do.domain.registry import RULES
from ledger_fiscal_do.domain.totals import TAXED, ZERO
from ledger_fiscal_do.domain.vocabulary import Billing
lazy from ledger_fiscal_do.domain.content import Buyer, Content, Issuer, Line, Reference
lazy from ledger_fiscal_do.domain.document import FiscalDocument
lazy from ledger_fiscal_do.domain.totals import Totals
lazy from ledger_fiscal_do.domain.types.base import TypeRules
lazy from ledger_kernel.money import Money

VERSION: Final = "1.0"
_RATE_TAGS: Final = {Billing.ITBIS_1: "1", Billing.ITBIS_2: "2", Billing.ITBIS_3: "3"}
_RETENTION: Final = "1"
_AMOUNT_DISCOUNT: Final = "$"


def _id_doc(node: Node, document: FiscalDocument, rules: TypeRules) -> None:
    content = document.content
    ident = node.child("iddoc")
    ident.number("tipoecf", content.ecf_type.value)
    ident.text("encf", str(document.encf))
    if rules.late_credit is not None:
        ident.text("indicadornotacredito", "1" if rules.late_credit(content) else "0")
    if rules.sequence_expires and document.sequence_expires_on is not None:
        ident.text("fechavencimientosecuencia", dgii_date(document.sequence_expires_on))
    taxed = any(line.billing in TAXED for line in content.lines)
    if rules.prices_include_itbis and taxed:
        ident.text("indicadormontogravado", "1" if content.prices_include_itbis else "0")
    ident.text("tipoingresos", None if content.income is None else content.income.value)
    ident.number("tipopago", None if content.terms is None else content.terms.value)
    ident.text("fechalimitepago", None if content.deadline is None else dgii_date(content.deadline))
    if content.payments:
        table = ident.child("tablaformaspago")
        for payment in content.payments:
            way = table.child("formadepago")
            way.number("formapago", payment.method.value)
            way.amount("montopago", payment.amount)


def _issuer(node: Node, issuer: Issuer, content: Content) -> None:
    issuing = node.child("emisor")
    issuing.text("rncemisor", str(issuer.tax_id))
    issuing.text("razonsocialemisor", issuer.legal_name)
    issuing.text("nombrecomercial", issuer.trade_name)
    issuing.text("sucursal", issuer.branch)
    issuing.text("direccionemisor", issuer.address)
    issuing.text("municipio", issuer.municipality)
    issuing.text("provincia", issuer.province)
    if issuer.phones:
        phones = issuing.child("tablatelefonoemisor")
        for phone in issuer.phones:
            phones.text("telefonoemisor", phone)
    issuing.text("correoemisor", issuer.email)
    issuing.text("fechaemision", dgii_date(content.issued_on))


def _buyer(node: Node, buyer: Buyer | None, rules: TypeRules) -> None:
    shape = rules.buyer
    if shape is None or (buyer is None and not shape.always):
        return
    buying = node.child("comprador")
    if buyer is None:
        return
    buying.text("rnccomprador", None if buyer.tax_id is None else str(buyer.tax_id))
    buying.text("identificadorextranjero", buyer.foreign_id)
    buying.text("razonsocialcomprador", buyer.name)
    buying.text("contactocomprador", buyer.contact)
    buying.text("correocomprador", buyer.email)
    buying.text("direccioncomprador", buyer.address)
    buying.text("municipiocomprador", buyer.municipality)
    buying.text("provinciacomprador", buyer.province)
    buying.text("paiscomprador", buyer.country)


def _totals(node: Node, totals: Totals) -> None:
    summed = node.child("totales")
    summed.amount("montogravadototal", totals.taxed_total)
    for billing in TAXED:
        group = totals.group(billing)
        summed.amount(f"montogravadoi{_RATE_TAGS[billing]}", None if group is None else group.base)
    summed.amount("montoexento", totals.exempt)
    for billing in TAXED:
        group = totals.group(billing)
        summed.number(f"itbis{_RATE_TAGS[billing]}", None if group is None else group.rate)
    summed.amount("totalitbis", totals.itbis_total)
    for billing in TAXED:
        group = totals.group(billing)
        summed.amount(f"totalitbis{_RATE_TAGS[billing]}", None if group is None else group.itbis)
    summed.amount("montototal", totals.total)
    summed.amount("montonofacturable", totals.not_billable)
    summed.amount("totalitbisretenido", totals.itbis_withheld)
    summed.amount("totalisrretencion", totals.isr_withheld)


def _line(node: Node, line: Line, amount: Money, rules: TypeRules) -> None:
    item = node.child("item")
    item.number("numerolinea", line.number)
    item.number("indicadorfacturacion", line.billing.value)
    held = line.withholding
    if held is not None:
        withheld = item.child("retencion")
        withheld.text("indicadoragenteretencionopercepcion", _RETENTION)
        exempt_zero = ZERO if rules.itbis_withholding and line.billing is Billing.EXEMPT else None
        withheld.amount("montoitbisretenido", held.itbis if held.itbis is not None else exempt_zero)
        withheld.amount("montoisrretenido", held.isr)
    item.text("nombreitem", line.name)
    item.number("indicadorbienoservicio", line.kind.value)
    item.text("descripcionitem", line.description)
    item.number("cantidaditem", line.quantity)
    item.number("unidadmedida", line.unit)
    item.number("preciounitarioitem", line.unit_price)
    if line.discount is not None:
        item.amount("descuentomonto", line.discount)
        sub = item.child("tablasubdescuento").child("subdescuento")
        sub.text("tiposubdescuento", _AMOUNT_DISCOUNT)
        sub.amount("montosubdescuento", line.discount)
    item.amount("montoitem", amount)


def _reference(node: Node, reference: Reference | None) -> None:
    if reference is None:
        return
    referring = node.child("informacionreferencia")
    referring.text("ncfmodificado", reference.modified)
    other = reference.other_taxpayer
    referring.text("rncotrocontribuyente", None if other is None else str(other))
    referring.text("fechancfmodificado", dgii_date(reference.issued_on))
    referring.number("codigomodificacion", reference.code.value)
    referring.text("razonmodificacion", reference.reason)


def render(document: FiscalDocument, *, signed_at: datetime) -> bytes:
    """``document`` as its unsigned e-CF, ``FechaHoraFirma`` set to ``signed_at``."""
    content = document.content
    rules = RULES[content.ecf_type]
    root = Node.root(FORMATS[content.ecf_type], "ecf")
    header = root.child("encabezado")
    header.text("version", VERSION)
    _id_doc(header, document, rules)
    _issuer(header, document.issuer, content)
    _buyer(header, content.buyer, rules)
    _totals(header, document.totals)
    items = root.child("detallesitems")
    for line, amount in zip(content.lines, document.totals.line_amounts, strict=True):
        _line(items, line, amount, rules)
    _reference(root, content.reference)
    root.text("fechahorafirma", dgii_moment(signed_at))
    return root.document()
```


`services/fiscal-do/src/ledger_fiscal_do/adapters/xml/rfce.py`:

```python
"""The summary of a consumer invoice under DOP 250,000 (RFCE), which DGII receives instead of the e-CF.

*Formato Resumen Factura Consumo Electrónica v1.0*: the invoice's identification, issuer, buyer and totals,
and the security code of the full e-CF (``CodigoSeguridadeCF``), which the company keeps signed in its archive
(DGII's e-CF technical report v1.0, §9). The summary is signed in its turn.
"""

from typing import Final

from ledger_dgii.published import Format
from ledger_fiscal_do.adapters.xml.writer import Node
from ledger_fiscal_do.domain.notation import dgii_date
from ledger_fiscal_do.domain.totals import TAXED
from ledger_fiscal_do.domain.vocabulary import Billing
lazy from ledger_fiscal_do.domain.document import FiscalDocument

VERSION: Final = "1.0"
_RATE_TAGS: Final = {Billing.ITBIS_1: "1", Billing.ITBIS_2: "2", Billing.ITBIS_3: "3"}


def render(document: FiscalDocument, *, security_code: str) -> bytes:
    """The unsigned RFCE of ``document`` (a consumer invoice whose e-CF is signed, with ``security_code``)."""
    content, totals = document.content, document.totals
    root = Node.root(Format.RFCE, "rfce")
    header = root.child("encabezado")
    header.text("version", VERSION)
    ident = header.child("iddoc")
    ident.number("tipoecf", content.ecf_type.value)
    ident.text("encf", str(document.encf))
    ident.text("tipoingresos", None if content.income is None else content.income.value)
    ident.number("tipopago", None if content.terms is None else content.terms.value)
    if content.payments:
        table = ident.child("tablaformaspago")
        for payment in content.payments:
            way = table.child("formadepago")
            way.number("formapago", payment.method.value)
            way.amount("montopago", payment.amount)
    issuing = header.child("emisor")
    issuing.text("rncemisor", str(document.issuer.tax_id))
    issuing.text("razonsocialemisor", document.issuer.legal_name)
    issuing.text("fechaemision", dgii_date(content.issued_on))
    buying = header.child("comprador")
    buyer = content.buyer
    if buyer is not None:
        buying.text("rnccomprador", None if buyer.tax_id is None else str(buyer.tax_id))
        buying.text("identificadorextranjero", buyer.foreign_id)
        buying.text("razonsocialcomprador", buyer.name)
    summed = header.child("totales")
    summed.amount("montogravadototal", totals.taxed_total)
    for billing in TAXED:
        group = totals.group(billing)
        summed.amount(f"montogravadoi{_RATE_TAGS[billing]}", None if group is None else group.base)
    summed.amount("montoexento", totals.exempt)
    summed.amount("totalitbis", totals.itbis_total)
    for billing in TAXED:
        group = totals.group(billing)
        summed.amount(f"totalitbis{_RATE_TAGS[billing]}", None if group is None else group.itbis)
    summed.amount("montototal", totals.total)
    summed.amount("montonofacturable", totals.not_billable)
    header.text("codigoseguridadecf", security_code)
    return root.document()
```


`services/fiscal-do/src/ledger_fiscal_do/adapters/xml/anecf.py`:

```python
"""The annulment of unused e-NCF (ANECF), *Formato Anulación de e-NCF v1.0*: one range of one type."""

from typing import Final
lazy from datetime import datetime

from ledger_dgii.published import Format
from ledger_fiscal_do.adapters.xml.writer import Node
from ledger_fiscal_do.domain.encf import Encf
from ledger_fiscal_do.domain.notation import dgii_moment
lazy from ledger_fiscal_do.domain.annulment import Annulment

VERSION: Final = "1.0"
_LINE: Final = "1"


def render(annulment: Annulment, *, at: datetime) -> bytes:
    """The unsigned ANECF of ``annulment``, dated ``at``."""
    count = str(annulment.numbers.size)
    root = Node.root(Format.ANECF, "anecf")
    header = root.child("encabezado")
    header.text("version", VERSION)
    header.text("rncemisor", str(annulment.issuer))
    header.text("cantidadencfanulados", count)
    header.text("fechahoraanulacionencf", dgii_moment(at))
    detail = root.child("detalleanulacion").child("anulacion")
    detail.text("nolinea", _LINE)
    detail.number("tipoecf", annulment.ecf_type.value)
    numbers = detail.child("tablarangosecuenciasanuladasencf").child("secuencias")
    numbers.text("secuenciaencfdesde", str(Encf(annulment.ecf_type, annulment.numbers.first)))
    numbers.text("secuenciaencfhasta", str(Encf(annulment.ecf_type, annulment.numbers.last)))
    detail.text("cantidadencfanulados", count)
    return root.document()
```


`services/fiscal-do/src/ledger_fiscal_do/adapters/xml/stamp.py`:

```python
"""The security code of a signed document.

DGII's e-CF technical report v1.0, §18.2.3: the code is "los primeros seis (6) dígitos del hash generado en el
SignatureValue de la firma digital del e-CF": the first six characters of the ``SignatureValue`` (base64), as
DGII's own examples show (``dcp79q``).
"""

from typing import ClassVar, Final

from lxml import etree

from ledger_dgii.schemas import DSIG, parser, require_readable
from ledger_kernel.errors import LedgerError

SECURITY_CODE_LENGTH: Final = 6
_SIGNATURE_VALUE: Final = f"{{{DSIG}}}SignatureValue"


class SignatureValueMissingError(LedgerError):
    """A document said to be signed holds no signature value: vault answered something it never answers."""

    code: ClassVar[str] = "signature_value_missing"

    def __init__(self) -> None:
        super().__init__("the signed document holds no SignatureValue")


def security_code(signed: bytes) -> str:
    """The first six characters of the signature's value in ``signed``."""
    require_readable(signed)
    root = etree.fromstring(signed, parser())
    value = root.find(f".//{_SIGNATURE_VALUE}")
    text = "" if value is None or value.text is None else "".join(value.text.split())
    if len(text) < SECURITY_CODE_LENGTH:
        raise SignatureValueMissingError
    return text[:SECURITY_CODE_LENGTH]
```


`services/fiscal-do/src/ledger_fiscal_do/adapters/xml/qr.py`:

```python
"""The QR link a signed e-CF's printed representation carries (DGII's e-CF technical report v1.0, §18.2.3).

It names DGII's ``ConsultaTimbre`` (``ConsultaTimbreFC`` for a consumer invoice under DOP 250,000, sent as a
summary) in the document's environment, with the parameters DGII lists, each value percent-encoded
(*Descripción Técnica Servicios Emisores*: the characters a URL reserves). DGII's hosts are configuration.
The issue date's parameter is named as the e-CF's element (``ledger_dgii.names``).
"""

from dataclasses import dataclass
from typing import Final, final
from urllib.parse import quote

from ledger_dgii.names import element
from ledger_dgii.published import Format
from ledger_fiscal_do.domain.notation import dgii_amount, dgii_date, dgii_moment
lazy from ledger_fiscal_do.domain.document import FiscalDocument

ISSUED_ON: Final = element(Format.ECF_31, "fechaemision")


@final
@dataclass(frozen=True, slots=True)
class QrHosts:
    """Where DGII answers the QR's queries: ``https://ecf.dgii.gov.do`` and ``https://fc.dgii.gov.do``."""

    ecf: str
    fc: str


def _query(parameters: tuple[tuple[str, str], ...]) -> str:
    return "&".join(f"{name}={quote(value, safe='')}" for name, value in parameters)


def qr_link(document: FiscalDocument, hosts: QrHosts) -> str | None:
    """The link of ``document``'s QR code; ``None`` until it is signed."""
    signature = document.signature
    if signature is None:
        return None
    environment = document.environment.value
    total = dgii_amount(document.totals.total)
    issuer, encf, code = str(document.issuer.tax_id), str(document.encf), signature.security_code
    if signature.summary:
        summary = (("RncEmisor", issuer), ("ENCF", encf), ("MontoTotal", total), ("CodigoSeguridad", code))
        return f"{hosts.fc}/{environment}/consultatimbrefc?{_query(summary)}"
    buyer = document.content.buyer
    buyer_tax_id = None if buyer is None or buyer.tax_id is None else str(buyer.tax_id)
    parameters = (
        ("RncEmisor", issuer),
        *((("RncComprador", buyer_tax_id),) if buyer_tax_id is not None else ()),
        ("ENCF", encf),
        (ISSUED_ON, dgii_date(document.content.issued_on)),
        ("MontoTotal", total),
        ("FechaFirma", dgii_moment(signature.signed_at)),
        ("CodigoSeguridad", code),
    )
    return f"{hosts.ecf}/{environment}/consultatimbre?{_query(parameters)}"
```


`services/fiscal-do/src/ledger_fiscal_do/adapters/xml/documents.py`:

```python
"""fiscal-do's XML port over DGII's formats: written, then checked against DGII's own schema.

A document is checked unsigned, as it will be once signed (``ledger_dgii.schemas``), before vault sees it,
and checked again once signed, as DGII will check it. A schema's refusal is answered with the schema's words,
cut short, and the place: at issue time the client's data broke a format rule the domain does not repeat (a
municipality code DGII's table lacks: 422); after it, a defect of ours, which the workflow does not retry.
"""

from typing import ClassVar, final, override
lazy from datetime import datetime

from ledger_dgii.published import Format
from ledger_dgii.schemas import violations
from ledger_fiscal_do.adapters.xml import anecf, ecf, rfce
from ledger_fiscal_do.adapters.xml.formats import FORMATS
from ledger_fiscal_do.adapters.xml.stamp import security_code
from ledger_fiscal_do.application.ports import FiscalXml
from ledger_kernel.errors import InvalidInputError
lazy from ledger_dgii.schemas import Violation
lazy from ledger_fiscal_do.domain.annulment import Annulment
lazy from ledger_fiscal_do.domain.document import FiscalDocument


class SchemaViolationError(InvalidInputError):
    """A document DGII's schema refuses, with where and why (at most 20 problems, each cut short)."""

    code: ClassVar[str] = "ecf_schema_invalid"

    def __init__(self, format_: Format, found: tuple[Violation, ...]) -> None:
        self.violations: tuple[Violation, ...] = found
        listed = "; ".join(f"{violation.path}: {violation.message}" for violation in found)
        super().__init__(f"DGII's schema {format_.value} refuses the document: {listed}")


def checked(format_: Format, document: bytes, *, unsigned: bool) -> bytes:
    """``document``, once ``format_``'s schema accepts it; ``SchemaViolationError`` otherwise."""
    found = violations(format_, document, unsigned=unsigned)
    if found:
        raise SchemaViolationError(format_, found)
    return document


@final
class DgiiXml(FiscalXml):
    """DGII's formats, written and checked (see the module)."""

    __slots__ = ()

    @override
    def ecf(self, document: FiscalDocument, *, signed_at: datetime) -> bytes:
        rendered = ecf.render(document, signed_at=signed_at)
        return checked(FORMATS[document.content.ecf_type], rendered, unsigned=True)

    @override
    def summary(self, document: FiscalDocument, *, security_code: str) -> bytes:
        return checked(Format.RFCE, rfce.render(document, security_code=security_code), unsigned=True)

    @override
    def annulment(self, annulment: Annulment, *, at: datetime) -> bytes:
        return checked(Format.ANECF, anecf.render(annulment, at=at), unsigned=True)

    @override
    def signed_ecf(self, document: FiscalDocument, signed: bytes) -> str:
        checked(FORMATS[document.content.ecf_type], signed, unsigned=False)
        return security_code(signed)

    @override
    def signed_summary(self, signed: bytes) -> None:
        checked(Format.RFCE, signed, unsigned=False)

    @override
    def signed_annulment(self, signed: bytes) -> None:
        checked(Format.ANECF, signed, unsigned=False)
```


Write the golden files from the samples, then read every one of them before committing: each is the sample's XML byte for byte, and the test checks that both libxml2 and xmlschema accept it (so a golden file is not trusted because the code wrote it, but because two validators that share no code accept it and a reviewer read it):

```bash
uv run python - <<'EOF'
import sys
from pathlib import Path

sys.path.insert(0, "services/fiscal-do/tests")
from samples import ISSUER, NOW, POINT, SEQUENCE, TENANT, sample

from ledger_fiscal_do.adapters.xml.documents import DgiiXml
from ledger_fiscal_do.domain.annulment import Annulment
from ledger_fiscal_do.domain.ids import AnnulmentId
from ledger_fiscal_do.domain.sequence import EncfSequence, NumberRange
from ledger_fiscal_do.domain.vocabulary import DgiiEnvironment, EcfType

golden = Path("services/fiscal-do/tests/golden")
golden.mkdir(exist_ok=True)
xml = DgiiXml()
for ecf_type in EcfType:
    (golden / f"ecf-{ecf_type.value}.xml").write_bytes(xml.ecf(sample(ecf_type), signed_at=NOW))
(golden / "rfce-32.xml").write_bytes(xml.summary(sample(EcfType.CONSUMER_INVOICE), security_code="dcp+9q"))
sequence = EncfSequence.registered(
    (SEQUENCE, TENANT, POINT), ecf_type=EcfType.CREDIT_INVOICE, numbers=NumberRange(1, 100), expires_on=None, now=NOW
)
annulment, _, _ = Annulment.requested(
    AnnulmentId(SEQUENCE), sequence, NumberRange(51, 100), issuer=(ISSUER.tax_id, DgiiEnvironment.PRECERTIFICATION), now=NOW
)
(golden / "anecf.xml").write_bytes(xml.annulment(annulment, at=NOW))
print(sorted(path.name for path in golden.iterdir()))
EOF
```

Expected: the twelve file names.

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run pytest services/fiscal-do/tests/test_xml.py services/fiscal-do/tests/test_ports.py -q --no-cov`
Expected: PASS, including `test_every_document_its_type_s_rules_accept_is_one_dgii_s_schema_accepts` (hypothesis: documents generated within the rules of each type, all accepted by libxml2 and by xmlschema).

- [ ] **Step 6: Gate**

Run: `uv run poe fmt && uv run poe check`
Expected: every step passes, coverage 100 % (a port's abstract method is its docstring; its refusals are covered by `test_ports`).

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock services/fiscal-do
git commit -m "feat(fiscal-do): every e-CF type, RFCE and ANECF written as DGII's schemas declare, checked twice

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: The use cases: issuing, the steps of an emission and an annulment, ranges, profiles, queries

**Files:**
- Create: `services/fiscal-do/src/ledger_fiscal_do/application/issuing.py`, `application/emission.py`, `application/annulling.py`, `application/ranges.py`, `application/profiles.py`, `application/queries.py`
- Test: `services/fiscal-do/tests/fakes.py`, `services/fiscal-do/tests/world.py` (test support), `services/fiscal-do/tests/test_application.py`

**Interfaces:**
- Consumes: the ports (Task 4), `DgiiXml` (Task 4, as the tests' XML), the domain (Task 3), `ledger_kernel.ids.new_id`, `ledger_kernel.pagination` (`Page`, `PageRequest`).
- Produces: `issuing`: `CreditExceedsOriginalError`, `ReferenceRejectedError`, `IssueRequest(emission_point, content)`, `issue(unit, request, *, company, xml) -> FiscalDocument`. `emission`: `RETENTION` (3653 days), `Step` (`signed`, `blocked`, `sent`, `unreachable`, `pending`, `decided`), `Stage`, `NotReadyError`, `is_summary`, `archive_key`, `Emission(units, signer, xml, archive, dgii, clock, retention)` with `sign`, `submit`, `poll`, `resume` (each `(tenant, document) -> Step`, idempotent). `annulling`: `annulment_key`, `Annulling(...)` with `annul(tenant, annulment) -> Step`. `ranges`: `RangeRequest(ecf_type, emission_point, numbers, expires_on)`, `register(unit, request)`, `annul(unit, sequence, numbers, *, company)`, `announce_expiring(units, candidates, *, clock, limit)`. `profiles`: `ProfileExistsError`, `StaleProfileError`, `ProfileNotFoundError`, `put_profile(unit, profile, *, replaces)`, `profile(unit)`. `queries`: `document`, `documents`, `sequence`, `sequences`, `annulment`. Test support: `fakes` (`State`, the in-memory ports, `FakeSigner`, `FakeArchive`, `FakeDgii`, `Clock`, `Hook`, `vault_signed`, `accepted`, `unavailable`), `world` (`COMPANY`, `PROFILE`, `HUNDRED`, `Fiscal`: the use cases over the fakes for one company).

Re-check before starting: `ledger_kernel.pagination.page_of` as Milestone 2 left it (`page_of(rows, request, *, key)`, the extra row telling there is a next page).

- [ ] **Step 1: Write the failing tests**

`services/fiscal-do/tests/fakes.py`:

```python
"""In-memory fakes of fiscal-do's ports, for the tests of its use cases and workflows.

The repositories keep every company's state in one ``State`` (so a test can look across companies) and answer
inside their unit's company only, as the PostgreSQL ones do. The signer signs the way vault's output looks
(an enveloped ``Signature`` last, its ``SignatureValue`` the base64 of the document's digest); DGII answers as
a test scripts it.
"""

import base64
import hashlib
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Final, final, override
lazy from collections.abc import AsyncGenerator, Callable, Sequence
lazy from datetime import date

from ledger_fiscal_do.application.ports import (
    Annulments,
    Archive,
    Archived,
    ArchiveIndex,
    CertificateUnavailableError,
    Companies,
    Decision,
    Dgii,
    DgiiCall,
    DgiiUnavailableError,
    Documents,
    Events,
    ExpiringRange,
    ExpiringRanges,
    Failure,
    FiscalUnit,
    Pending,
    Profiles,
    Received,
    Reception,
    Result,
    Sequences,
    Signer,
    Units,
)
from ledger_fiscal_do.domain.annulment import AnnulmentNotFoundError
from ledger_fiscal_do.domain.document import DocumentNotFoundError, DocumentState
from ledger_fiscal_do.domain.ids import TrackId
from ledger_fiscal_do.domain.verdict import Outcome, Verdict
from ledger_fiscal_do.domain.vocabulary import EcfType
from ledger_kernel.money import Currency, Money
from ledger_kernel.pagination import page_of
lazy from ledger_fiscal_do.application.ports import ArchiveKind, CompanyFacts
lazy from ledger_fiscal_do.domain.annulment import Annulment
lazy from ledger_fiscal_do.domain.document import FiscalDocument
lazy from ledger_fiscal_do.domain.encf import Encf
lazy from ledger_fiscal_do.domain.events import FiscalEvent
lazy from ledger_fiscal_do.domain.ids import AnnulmentId, DocumentId, EmissionPointId, SequenceId
lazy from ledger_fiscal_do.domain.profile import FiscalProfile
lazy from ledger_fiscal_do.domain.sequence import EncfSequence
lazy from ledger_kernel.ids import Tenant
lazy from ledger_kernel.pagination import Page, PageRequest

DSIG: Final = "http://www.w3.org/2000/09/xmldsig#"


@final
class State:
    """fiscal-do's state, as the fakes keep it: every company's."""

    def __init__(self) -> None:
        self.documents: dict[DocumentId, FiscalDocument] = {}
        self.sequences: dict[SequenceId, EncfSequence] = {}
        self.expiry_claims: dict[SequenceId, int] = {}
        self.annulments: dict[AnnulmentId, Annulment] = {}
        self.profiles: dict[Tenant, FiscalProfile] = {}
        self.index: list[tuple[DocumentId | AnnulmentId, ArchiveKind, Archived]] = []
        self.events: list[FiscalEvent] = []
        self.locks = 0
        self.locked_reads = 0


@final
class FakeDocuments(Documents):
    def __init__(self, state: State, tenant: Tenant) -> None:
        self._state, self._tenant = state, tenant

    def _mine(self) -> list[FiscalDocument]:
        return [document for document in self._state.documents.values() if document.tenant == self._tenant]

    @override
    async def add(self, document: FiscalDocument) -> None:
        self._state.documents[document.id] = document

    @override
    async def get(self, document: DocumentId) -> FiscalDocument | None:
        return next((found for found in self._mine() if found.id == document), None)

    @override
    async def locked(self, document: DocumentId) -> FiscalDocument:
        self._state.locked_reads += 1
        found = await self.get(document)
        if found is None:
            raise DocumentNotFoundError(document)
        return found

    @override
    async def by_encf(self, encf: Encf) -> FiscalDocument | None:
        return next((found for found in self._mine() if found.encf == encf), None)

    @override
    async def update(self, document: FiscalDocument) -> None:
        self._state.documents[document.id] = document

    @override
    async def credited(self, encf: Encf) -> Money:
        total = Money.zero(Currency.DOP)
        for found in self._mine():
            reference = found.content.reference
            credit = (
                found.content.ecf_type is EcfType.CREDIT_NOTE and found.state is not DocumentState.REJECTED
            )
            if credit and reference is not None and reference.modified == str(encf):
                total += found.totals.total
        return total

    @override
    async def blocked(self) -> tuple[DocumentId, ...]:
        return tuple(found.id for found in self._mine() if found.state is DocumentState.BLOCKED_CERTIFICATE)

    @override
    async def page(self, request: PageRequest) -> Page[FiscalDocument]:
        mine = sorted(self._mine(), key=lambda found: found.id)
        after = [found for found in mine if request.after is None or found.id > request.after]
        return page_of(after[: request.size + 1], request, key=lambda found: found.id)


@final
class FakeSequences(Sequences):
    def __init__(self, state: State, tenant: Tenant) -> None:
        self._state, self._tenant = state, tenant

    def _mine(self) -> list[EncfSequence]:
        return [sequence for sequence in self._state.sequences.values() if sequence.tenant == self._tenant]

    @override
    async def lock(self) -> None:
        self._state.locks += 1

    @override
    async def add(self, sequence: EncfSequence) -> None:
        self._state.sequences[sequence.id] = sequence

    @override
    async def get(self, sequence: SequenceId) -> EncfSequence | None:
        return next((found for found in self._mine() if found.id == sequence), None)

    @override
    async def locked(self, sequence: SequenceId) -> EncfSequence | None:
        return await self.get(sequence)

    @override
    async def of_type(self, ecf_type: EcfType) -> tuple[EncfSequence, ...]:
        return tuple(found for found in self._mine() if found.ecf_type is ecf_type)

    @override
    async def usable(self, ecf_type: EcfType, point: EmissionPointId, *, on: date) -> EncfSequence | None:
        usable = [
            found
            for found in self._mine()
            if found.ecf_type is ecf_type and found.emission_point == point and found.usable_on(on)
        ]
        return min(usable, key=lambda found: found.numbers.first, default=None)

    @override
    async def update(self, sequence: EncfSequence) -> None:
        self._state.sequences[sequence.id] = sequence

    @override
    async def page(self, request: PageRequest) -> Page[EncfSequence]:
        mine = sorted(self._mine(), key=lambda found: found.id)
        return page_of(mine[: request.size + 1], request, key=lambda found: found.id)

    @override
    async def claim_expiry(self, sequence: SequenceId, days: int) -> bool:
        announced = self._state.expiry_claims.get(sequence)
        if announced is not None and announced <= days:
            return False
        self._state.expiry_claims[sequence] = days
        return True


@final
class FakeExpiringRanges(ExpiringRanges):
    """The ranges with an expiry, read across companies; or rows a test fixed (read before a change)."""

    def __init__(self, state: State, rows: Sequence[ExpiringRange] | None = None) -> None:
        self._state, self._rows = state, rows

    @override
    async def due(self, until: date, limit: int) -> Sequence[ExpiringRange]:
        if self._rows is not None:
            return self._rows
        due = [
            ExpiringRange(
                sequence=found.id,
                tenant=found.tenant,
                ecf_type=found.ecf_type,
                expires_on=found.expires_on,
                announced=self._state.expiry_claims.get(found.id),
            )
            for found in self._state.sequences.values()
            if found.expires_on is not None and found.expires_on <= until and found.available
        ]
        return sorted(due, key=lambda watched: watched.expires_on)[:limit]


@final
class FakeAnnulments(Annulments):
    def __init__(self, state: State, tenant: Tenant) -> None:
        self._state, self._tenant = state, tenant

    @override
    async def add(self, annulment: Annulment) -> None:
        self._state.annulments[annulment.id] = annulment

    @override
    async def get(self, annulment: AnnulmentId) -> Annulment | None:
        found = self._state.annulments.get(annulment)
        return found if found is not None and found.tenant == self._tenant else None

    @override
    async def locked(self, annulment: AnnulmentId) -> Annulment:
        found = await self.get(annulment)
        if found is None:
            raise AnnulmentNotFoundError(annulment)
        return found

    @override
    async def update(self, annulment: Annulment) -> None:
        self._state.annulments[annulment.id] = annulment


@final
class FakeProfiles(Profiles):
    def __init__(self, state: State, tenant: Tenant) -> None:
        self._state, self._tenant = state, tenant
        self.overtaken = False
        """Another request replaced the profile between this unit's read and its write."""

    @override
    async def get(self) -> FiscalProfile | None:
        return self._state.profiles.get(self._tenant)

    @override
    async def put(self, profile: FiscalProfile) -> bool:
        current = self._state.profiles.get(self._tenant)
        if self.overtaken or (current.version if current is not None else 0) != profile.version - 1:
            return False
        self._state.profiles[self._tenant] = profile
        return True


@final
class FakeIndex(ArchiveIndex):
    def __init__(self, state: State) -> None:
        self._state = state

    @override
    async def add(self, owner: DocumentId | AnnulmentId, kind: ArchiveKind, archived: Archived) -> None:
        self._state.index.append((owner, kind, archived))

    @override
    async def find(self, owner: DocumentId | AnnulmentId, kind: ArchiveKind) -> Archived | None:
        return next(
            (found for who, what, found in reversed(self._state.index) if (who, what) == (owner, kind)), None
        )


@final
class FakeEvents(Events):
    def __init__(self, state: State) -> None:
        self._state = state

    @override
    async def publish(self, event: FiscalEvent) -> None:
        self._state.events.append(event)


@final
class FakeUnits(Units):
    def __init__(self, state: State, clock: Clock) -> None:
        self._state, self._clock = state, clock

    def bound(self, tenant: Tenant) -> FiscalUnit:
        return FiscalUnit(
            tenant=tenant,
            documents=FakeDocuments(self._state, tenant),
            sequences=FakeSequences(self._state, tenant),
            annulments=FakeAnnulments(self._state, tenant),
            profiles=FakeProfiles(self._state, tenant),
            archive=FakeIndex(self._state),
            events=FakeEvents(self._state),
            now=self._clock.now,
        )

    @override
    @asynccontextmanager
    async def unit(self, tenant: Tenant) -> AsyncGenerator[FiscalUnit]:
        yield self.bound(tenant)


@final
@dataclass
class Clock:
    """A wall clock a test moves by hand."""

    now: datetime

    def __call__(self) -> datetime:
        return self.now


type Hook = Callable[[], None]
"""What a test has happen just before a fake answers: another replica's work, a race."""


def nothing() -> None:
    """The hook of a fake no test interferes with."""


def vault_signed(document: bytes) -> bytes:
    """``document`` with a signature shaped as vault's, last in its root."""
    value = base64.b64encode(hashlib.sha256(document).digest()).decode("ascii")
    signature = f'<Signature xmlns="{DSIG}"><SignedInfo/><SignatureValue>{value}</SignatureValue></Signature>'
    head, _, tail = document.rpartition(b"</")
    return head + signature.encode("ascii") + b"</" + tail


@final
class FakeSigner(Signer):
    """vault as a test wants it: signing, or without a usable certificate."""

    def __init__(self) -> None:
        self.available = True
        self.before: Hook = nothing
        self.signed: list[tuple[Tenant, str]] = []
        self.seeds = 0

    @override
    async def sign(self, tenant: Tenant, document: bytes, *, reference: str) -> bytes:
        self.before()
        if not self.available:
            raise CertificateUnavailableError(EXPIRED)
        self.signed.append((tenant, reference))
        return vault_signed(document)

    @override
    async def sign_seed(self, tenant: Tenant, seed: bytes) -> bytes:
        self.seeds += 1
        return vault_signed(seed)


EXPIRED: Final = "certificate_expired"


@final
class FakeArchive(Archive):
    """Object storage that keeps the first content at each key."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    @override
    async def keep(self, key: str, content: bytes, *, retain_until: datetime) -> Archived:
        self.objects.setdefault(key, content)
        return Archived(
            key=key,
            version=str(len(self.objects)),
            sha256=hashlib.sha256(content).hexdigest(),
            size=len(content),
            retain_until=retain_until,
        )

    @override
    async def read(self, key: str) -> bytes:
        return self.objects[key]


def accepted() -> Decision:
    """DGII's acceptance, at a fixed moment."""
    outcome = Outcome(verdict=Verdict.ACCEPTED, decided_at=datetime(2026, 10, 12, 14, 0, tzinfo=UTC))
    return Decision(outcome, b'{"codigo": 1}')


def unavailable() -> DgiiUnavailableError:
    """DGII failing."""
    return DgiiUnavailableError(Failure.SERVER_ERROR)


def _answer[T](queue: deque[T | Exception], default: T) -> T:
    answer = queue.popleft() if queue else default
    if isinstance(answer, Exception):
        raise answer
    return answer


@final
@dataclass
class FakeDgii(Dgii):
    """DGII as a test scripts it: a queue of answers per service; an empty queue answers the default."""

    receptions: deque[Reception | Exception] = field(default_factory=deque[Reception | Exception])
    results: deque[Result | Exception] = field(default_factory=deque[Result | Exception])
    summaries: deque[Decision | Exception] = field(default_factory=deque[Decision | Exception])
    annulments: deque[Decision | Exception] = field(default_factory=deque[Decision | Exception])
    known: dict[str, tuple[TrackId, ...]] = field(default_factory=dict[str, tuple[TrackId, ...]])
    sent: list[tuple[DgiiCall, str]] = field(default_factory=list[tuple[DgiiCall, str]])
    before: Hook = nothing

    @override
    async def submit_ecf(self, call: DgiiCall, encf: Encf, signed: bytes) -> Reception:
        self.before()
        self.sent.append((call, str(encf)))
        received: Reception = Received(TrackId(f"track-{encf}"), b'{"trackId": "t"}')
        return _answer(self.receptions, received)

    @override
    async def submit_summary(self, call: DgiiCall, encf: Encf, signed: bytes) -> Decision:
        self.before()
        self.sent.append((call, f"summary {encf}"))
        return _answer(self.summaries, accepted())

    @override
    async def result(self, call: DgiiCall, track_id: TrackId) -> Result:
        self.before()
        pending: Result = Pending()
        return _answer(self.results, pending)

    @override
    async def track_ids(self, call: DgiiCall, encf: Encf) -> tuple[TrackId, ...]:
        return self.known.get(str(encf), ())

    @override
    async def annul(self, call: DgiiCall, signed: bytes) -> Decision:
        self.before()
        self.sent.append((call, "annulment"))
        return _answer(self.annulments, accepted())


@final
class FakeCompanies(Companies):
    """identity, answering one company's facts."""

    def __init__(self, facts: CompanyFacts) -> None:
        self.facts = facts

    @override
    async def company(self, tenant: Tenant) -> CompanyFacts:
        return self.facts
```


`services/fiscal-do/tests/world.py`:

```python
"""fiscal-do's use cases over the fakes, for one company with a fiscal profile.

What the tests of the use cases and of the workflows' activities share.
"""

from dataclasses import replace
from typing import Final, final

from ledger_fiscal_do.adapters.xml.documents import DgiiXml
from ledger_fiscal_do.application import ranges
from ledger_fiscal_do.application.annulling import Annulling
from ledger_fiscal_do.application.emission import Emission, Step
from ledger_fiscal_do.application.issuing import IssueRequest, issue
from ledger_fiscal_do.application.ports import CompanyFacts
from ledger_fiscal_do.domain.profile import FiscalProfile
from ledger_fiscal_do.domain.sequence import NumberRange
from ledger_fiscal_do.domain.vocabulary import DgiiEnvironment
from ledger_kernel.countries.do import TaxId
lazy from ledger_fiscal_do.domain.content import Content
lazy from ledger_fiscal_do.domain.document import DocumentState, FiscalDocument
lazy from ledger_fiscal_do.domain.ids import DocumentId, TrackId
lazy from ledger_fiscal_do.domain.sequence import EncfSequence
lazy from ledger_fiscal_do.domain.vocabulary import EcfType

from fakes import Clock, FakeArchive, FakeDgii, FakeSigner, FakeUnits, State
from samples import CONTENTS, NOW, POINT, TENANT, expiry

COMPANY: Final = CompanyFacts(
    tax_id=TaxId.parse("101850043"), legal_name="FERRETERIA DUARTE SRL", active=True
)
PROFILE: Final = FiscalProfile(
    tenant=TENANT, environment=DgiiEnvironment.PRECERTIFICATION, address="Av. Duarte 123", updated_at=NOW
)
HUNDRED: Final = NumberRange(1, 100)


@final
class Fiscal:
    """fiscal-do's use cases over fakes, for a company that has a profile."""

    def __init__(self) -> None:
        self.state: State = State()
        self.clock: Clock = Clock(NOW)
        self.units: FakeUnits = FakeUnits(self.state, self.clock)
        self.signer: FakeSigner = FakeSigner()
        self.archive: FakeArchive = FakeArchive()
        self.dgii: FakeDgii = FakeDgii()
        self.emission: Emission = Emission(
            units=self.units,
            signer=self.signer,
            xml=DgiiXml(),
            archive=self.archive,
            dgii=self.dgii,
            clock=self.clock,
        )
        self.annulling: Annulling = Annulling(
            units=self.units,
            signer=self.signer,
            xml=DgiiXml(),
            archive=self.archive,
            dgii=self.dgii,
            clock=self.clock,
        )
        self.state.profiles[TENANT] = PROFILE

    async def register(self, ecf_type: EcfType, numbers: NumberRange = HUNDRED) -> EncfSequence:
        request = ranges.RangeRequest(
            ecf_type=ecf_type, emission_point=POINT, numbers=numbers, expires_on=expiry(ecf_type)
        )
        return await ranges.register(self.units.bound(TENANT), request)

    async def issue(self, content: Content, company: CompanyFacts = COMPANY) -> FiscalDocument:
        unit = self.units.bound(TENANT)
        return await issue(
            unit, IssueRequest(emission_point=POINT, content=content), company=company, xml=DgiiXml()
        )

    async def issued(self, ecf_type: EcfType) -> FiscalDocument:
        await self.register(ecf_type)
        return await self.issue(CONTENTS[ecf_type])

    async def signed(self, ecf_type: EcfType) -> FiscalDocument:
        document = await self.issued(ecf_type)
        assert await self.emission.sign(TENANT, document.id) is Step.SIGNED
        return self.stored(document.id)

    def stored(self, document: DocumentId) -> FiscalDocument:
        return self.state.documents[document]

    def at(self, document: DocumentId, state: DocumentState, track_id: TrackId | None = None) -> None:
        self.state.documents[document] = replace(self.stored(document), state=state, track_id=track_id)
```


`services/fiscal-do/tests/test_application.py`:

```python
from dataclasses import replace
from datetime import date, timedelta
from typing import Final

import pytest

from ledger_fiscal_do.adapters.xml.documents import SchemaViolationError
from ledger_fiscal_do.application import profiles, queries, ranges
from ledger_fiscal_do.application.emission import NotReadyError, Step
from ledger_fiscal_do.application.issuing import (
    CreditExceedsOriginalError,
    ReferenceRejectedError,
)
from ledger_fiscal_do.application.ports import (
    ArchiveKind,
    CertificateUnavailableError,
    CompanyInactiveError,
    Refused,
)
from ledger_fiscal_do.domain.annulment import AnnulmentNotFoundError, AnnulmentState
from ledger_fiscal_do.domain.content import Buyer, Reference
from ledger_fiscal_do.domain.document import DocumentNotFoundError, DocumentState
from ledger_fiscal_do.domain.encf import Encf
from ledger_fiscal_do.domain.events import (
    AnnulmentDecided,
    AnnulmentRequested,
    DocumentAccepted,
    DocumentNumbered,
    DocumentRejected,
    SequenceExpiring,
    SequenceLow,
)
from ledger_fiscal_do.domain.ids import AnnulmentId, DocumentId, SequenceId, TrackId
from ledger_fiscal_do.domain.problems import DocumentInvalidError
from ledger_fiscal_do.domain.profile import ProfileMissingError
from ledger_fiscal_do.domain.sequence import (
    NoSequenceError,
    NumberRange,
    SequenceExpiryError,
    SequenceNotFoundError,
    SequenceOverlapError,
)
from ledger_fiscal_do.domain.verdict import DgiiMessage
from ledger_fiscal_do.domain.vocabulary import DgiiEnvironment, EcfType, Modification
from ledger_kernel.ids import CompanyId, OrganizationId, Tenant, new_id
from ledger_kernel.pagination import PageRequest

from fakes import (
    FakeExpiringRanges,
    FakeProfiles,
    accepted,
    unavailable,
)
from samples import CONTENTS, NOW, POINT, REFERENCE, TENANT, dop, line
from world import COMPANY, HUNDRED, PROFILE, Fiscal

MESSAGES: Final = (DgiiMessage("613", "El RNC del comprador no existe"),)


@pytest.fixture
def fiscal() -> Fiscal:
    return Fiscal()


# ── Issuing ─────────────────────────────────────────────────────────────────────


async def test_an_issued_document_is_numbered_stored_and_announced(fiscal: Fiscal) -> None:
    document = await fiscal.issued(EcfType.CREDIT_INVOICE)
    assert (document.encf, document.state, document.environment) == (
        Encf(EcfType.CREDIT_INVOICE, 1),
        DocumentState.NUMBERED,
        DgiiEnvironment.PRECERTIFICATION,
    )
    assert document.issuer.legal_name == COMPANY.legal_name
    assert fiscal.stored(document.id) == document
    (numbered,) = fiscal.state.events
    assert isinstance(numbered, DocumentNumbered)
    (sequence,) = fiscal.state.sequences.values()
    assert sequence.next == 2


async def test_a_range_running_low_is_announced_with_the_document_that_crosses_80_percent(
    fiscal: Fiscal,
) -> None:
    await fiscal.register(EcfType.CREDIT_INVOICE, NumberRange(1, 5))
    for _ in range(4):
        await fiscal.issue(CONTENTS[EcfType.CREDIT_INVOICE])
    assert [type(event) for event in fiscal.state.events][-2:] == [DocumentNumbered, SequenceLow]


async def test_nothing_is_numbered_for_a_document_that_cannot_be_issued(fiscal: Fiscal) -> None:
    invoice = CONTENTS[EcfType.CREDIT_INVOICE]
    with pytest.raises(NoSequenceError):
        await fiscal.issue(invoice)
    await fiscal.register(EcfType.CREDIT_INVOICE)
    with pytest.raises(CompanyInactiveError):
        await fiscal.issue(invoice, replace(COMPANY, active=False))
    with pytest.raises(DocumentInvalidError):
        await fiscal.issue(replace(invoice, lines=()))
    customer = invoice.buyer or Buyer()
    with pytest.raises(SchemaViolationError):
        await fiscal.issue(replace(invoice, buyer=replace(customer, municipality="999999")))
    del fiscal.state.profiles[TENANT]
    with pytest.raises(ProfileMissingError):
        await fiscal.issue(invoice)
    assert fiscal.state.documents == {}
    assert next(iter(fiscal.state.sequences.values())).next == 1


async def test_a_credit_note_never_credits_more_than_the_document_it_modifies(fiscal: Fiscal) -> None:
    invoice = await fiscal.issued(EcfType.CREDIT_INVOICE)
    await fiscal.register(EcfType.CREDIT_NOTE)
    reference = replace(REFERENCE, modified=str(invoice.encf))
    note = replace(CONTENTS[EcfType.CREDIT_NOTE], reference=reference, lines=(line(1),))
    first = await fiscal.issue(note)
    assert first.totals.total == dop("2360.00")
    with pytest.raises(CreditExceedsOriginalError):
        await fiscal.issue(note)
    debit = replace(CONTENTS[EcfType.DEBIT_NOTE], reference=reference)
    await fiscal.register(EcfType.DEBIT_NOTE)
    await fiscal.issue(debit)
    fiscal.at(invoice.id, DocumentState.REJECTED)
    with pytest.raises(ReferenceRejectedError):
        await fiscal.issue(debit)


async def test_a_note_on_a_paper_ncf_or_another_issuer_s_e_ncf_is_left_to_dgii(fiscal: Fiscal) -> None:
    await fiscal.register(EcfType.CREDIT_NOTE)
    paper = Reference(modified="B0100000001", issued_on=date(2026, 9, 1), code=Modification.ANNULS)
    elsewhere = replace(paper, modified="E319999999999")
    for reference in (paper, elsewhere):
        assert (
            await fiscal.issue(replace(CONTENTS[EcfType.CREDIT_NOTE], reference=reference))
        ).encf.number > 0


# ── Emission ────────────────────────────────────────────────────────────────────


async def test_a_document_is_signed_archived_sent_and_accepted_each_step_once(fiscal: Fiscal) -> None:
    document = await fiscal.signed(EcfType.CREDIT_INVOICE)
    assert await fiscal.emission.sign(TENANT, document.id) is Step.SIGNED
    assert document.signature is not None
    assert len(document.signature.security_code) == 6
    assert fiscal.archive.objects[document.signature.archive_key].startswith(b"<?xml")
    assert len(fiscal.signer.signed) == 1
    assert await fiscal.emission.submit(TENANT, document.id) is Step.SENT
    assert await fiscal.emission.submit(TENANT, document.id) is Step.SENT
    assert fiscal.stored(document.id).track_id == f"track-{document.encf}"
    assert await fiscal.emission.poll(TENANT, document.id) is Step.PENDING
    fiscal.dgii.results.append(accepted())
    assert await fiscal.emission.poll(TENANT, document.id) is Step.DECIDED
    assert await fiscal.emission.poll(TENANT, document.id) is Step.DECIDED
    assert await fiscal.emission.submit(TENANT, document.id) is Step.DECIDED
    assert fiscal.stored(document.id).state is DocumentState.ACCEPTED
    assert isinstance(fiscal.state.events[-1], DocumentAccepted)
    kinds = [kind for owner, kind, _ in fiscal.state.index if owner == document.id]
    assert kinds == [ArchiveKind.ECF, ArchiveKind.ANSWER]
    assert len(fiscal.dgii.sent) == 1


async def test_a_consumer_invoice_under_250_000_goes_as_a_summary_answered_at_once(fiscal: Fiscal) -> None:
    document = await fiscal.signed(EcfType.CONSUMER_INVOICE)
    assert document.signature is not None
    assert document.signature.summary
    assert await fiscal.emission.submit(TENANT, document.id) is Step.DECIDED
    assert fiscal.stored(document.id).state is DocumentState.ACCEPTED
    assert fiscal.dgii.sent[-1][1] == f"summary {document.encf}"
    assert [kind for _, kind, _ in fiscal.state.index] == [
        ArchiveKind.ECF,
        ArchiveKind.SUMMARY,
        ArchiveKind.ANSWER,
    ]


async def test_a_summary_never_kept_cannot_be_sent(fiscal: Fiscal) -> None:
    document = await fiscal.signed(EcfType.CONSUMER_INVOICE)
    fiscal.state.index.clear()
    with pytest.raises(NotReadyError, match="submitted as a summary"):
        await fiscal.emission.submit(TENANT, document.id)


async def test_without_a_certificate_a_document_waits_and_resumes_where_it_stood(fiscal: Fiscal) -> None:
    document = await fiscal.issued(EcfType.CREDIT_INVOICE)
    fiscal.signer.available = False
    assert await fiscal.emission.sign(TENANT, document.id) is Step.BLOCKED
    assert await fiscal.emission.sign(TENANT, document.id) is Step.BLOCKED
    assert fiscal.stored(document.id).state is DocumentState.BLOCKED_CERTIFICATE
    assert await fiscal.units.bound(TENANT).documents.blocked() == (document.id,)
    await fiscal.emission.resume(TENANT, document.id)
    await fiscal.emission.resume(TENANT, document.id)
    assert fiscal.stored(document.id).state is DocumentState.NUMBERED
    fiscal.signer.available = True
    assert await fiscal.emission.sign(TENANT, document.id) is Step.SIGNED


async def test_a_token_that_needs_a_missing_certificate_blocks_sending_and_polling(fiscal: Fiscal) -> None:
    document = await fiscal.signed(EcfType.CREDIT_INVOICE)
    fiscal.dgii.receptions.append(CertificateUnavailableError("no_active_certificate"))
    assert await fiscal.emission.submit(TENANT, document.id) is Step.BLOCKED
    assert fiscal.stored(document.id).state is DocumentState.BLOCKED_CERTIFICATE
    await fiscal.emission.resume(TENANT, document.id)
    assert fiscal.stored(document.id).state is DocumentState.SIGNED
    assert await fiscal.emission.submit(TENANT, document.id) is Step.SENT
    fiscal.dgii.results.append(CertificateUnavailableError("certificate_expired"))
    assert await fiscal.emission.poll(TENANT, document.id) is Step.BLOCKED
    assert fiscal.stored(document.id).state is DocumentState.SENT


async def test_an_outage_is_a_contingency_and_what_dgii_received_is_never_sent_twice(fiscal: Fiscal) -> None:
    document = await fiscal.signed(EcfType.CREDIT_INVOICE)
    fiscal.dgii.receptions.append(unavailable())
    assert await fiscal.emission.submit(TENANT, document.id) is Step.UNREACHABLE
    contingency = fiscal.stored(document.id)
    assert (contingency.state, contingency.contingency_since) == (DocumentState.CONTINGENCY, NOW)
    fiscal.dgii.known[str(document.encf)] = (TrackId("first"), TrackId("second"))
    assert await fiscal.emission.submit(TENANT, document.id) is Step.SENT
    assert fiscal.stored(document.id).track_id == "second"
    assert len(fiscal.dgii.sent) == 1
    fiscal.dgii.results.append(unavailable())
    assert await fiscal.emission.poll(TENANT, document.id) is Step.UNREACHABLE


async def test_a_contingency_with_nothing_received_sends_again(fiscal: Fiscal) -> None:
    document = await fiscal.signed(EcfType.CREDIT_INVOICE)
    fiscal.dgii.receptions.extend([unavailable(), unavailable()])
    assert await fiscal.emission.submit(TENANT, document.id) is Step.UNREACHABLE
    assert await fiscal.emission.submit(TENANT, document.id) is Step.UNREACHABLE
    assert await fiscal.emission.submit(TENANT, document.id) is Step.SENT
    assert len(fiscal.dgii.sent) == 3


async def test_a_document_refused_at_the_door_is_rejected_with_dgii_s_words(fiscal: Fiscal) -> None:
    document = await fiscal.signed(EcfType.CREDIT_INVOICE)
    fiscal.dgii.receptions.append(Refused(MESSAGES, b'{"error": "613"}'))
    assert await fiscal.emission.submit(TENANT, document.id) is Step.DECIDED
    rejected = fiscal.stored(document.id)
    assert rejected.state is DocumentState.REJECTED
    assert rejected.outcome is not None
    assert rejected.outcome.messages == MESSAGES
    assert isinstance(fiscal.state.events[-1], DocumentRejected)


async def test_steps_out_of_order_are_defects_and_unknown_documents_are_not_found(fiscal: Fiscal) -> None:
    document = await fiscal.issued(EcfType.CREDIT_INVOICE)
    with pytest.raises(NotReadyError, match="numbered cannot be submitted"):
        await fiscal.emission.submit(TENANT, document.id)
    with pytest.raises(NotReadyError, match="numbered cannot be polled"):
        await fiscal.emission.poll(TENANT, document.id)
    with pytest.raises(DocumentNotFoundError):
        await fiscal.emission.sign(TENANT, DocumentId(new_id()))
    stranger = Tenant(OrganizationId(new_id()), CompanyId(new_id()))
    with pytest.raises(DocumentNotFoundError):
        await fiscal.emission.resume(stranger, document.id)


async def test_a_step_another_replica_recorded_first_is_not_recorded_twice(fiscal: Fiscal) -> None:
    document = await fiscal.signed(EcfType.CREDIT_INVOICE)
    events = len(fiscal.state.events)
    fiscal.dgii.before = lambda: fiscal.at(document.id, DocumentState.SENT, TrackId("other"))
    fiscal.dgii.receptions.append(unavailable())
    assert await fiscal.emission.submit(TENANT, document.id) is Step.UNREACHABLE
    assert fiscal.stored(document.id).state is DocumentState.SENT
    fiscal.at(document.id, DocumentState.SIGNED)
    assert await fiscal.emission.submit(TENANT, document.id) is Step.SENT
    assert fiscal.stored(document.id).track_id == "other"
    fiscal.at(document.id, DocumentState.SIGNED)
    fiscal.dgii.before = lambda: fiscal.at(document.id, DocumentState.ACCEPTED)
    fiscal.dgii.receptions.append(Refused(MESSAGES, b"{}"))
    assert await fiscal.emission.submit(TENANT, document.id) is Step.DECIDED
    fiscal.at(document.id, DocumentState.SENT, TrackId("t"))
    fiscal.dgii.results.append(accepted())
    assert await fiscal.emission.poll(TENANT, document.id) is Step.DECIDED
    assert fiscal.stored(document.id).state is DocumentState.ACCEPTED
    assert len(fiscal.state.events) == events


async def test_a_signature_another_replica_recorded_first_is_kept(fiscal: Fiscal) -> None:
    first = await fiscal.signed(EcfType.CREDIT_INVOICE)
    fiscal.state.documents[first.id] = replace(first, signature=None, state=DocumentState.NUMBERED)

    def signed_meanwhile() -> None:
        fiscal.state.documents[first.id] = first

    fiscal.signer.before = signed_meanwhile
    assert await fiscal.emission.sign(TENANT, first.id) is Step.SIGNED
    assert fiscal.stored(first.id).signature == first.signature


async def test_a_missing_certificate_leaves_a_document_dgii_holds_where_it_is(fiscal: Fiscal) -> None:
    document = await fiscal.issued(EcfType.CREDIT_INVOICE)
    fiscal.signer.available = False
    fiscal.signer.before = lambda: fiscal.at(document.id, DocumentState.SENT, TrackId("t"))
    assert await fiscal.emission.sign(TENANT, document.id) is Step.BLOCKED
    assert fiscal.stored(document.id).state is DocumentState.SENT


# ── Annulments ──────────────────────────────────────────────────────────────────


async def test_an_annulment_withdraws_numbers_and_dgii_is_told_once(fiscal: Fiscal) -> None:
    sequence = await fiscal.register(EcfType.CREDIT_INVOICE)
    unit = fiscal.units.bound(TENANT)
    annulment = await ranges.annul(unit, sequence.id, NumberRange(51, 100), company=COMPANY)
    requested = fiscal.state.events[-1]
    assert isinstance(requested, AnnulmentRequested)
    assert fiscal.state.sequences[sequence.id].available == 50
    fiscal.signer.available = False
    assert await fiscal.annulling.annul(TENANT, annulment.id) is Step.BLOCKED
    fiscal.signer.available = True
    fiscal.dgii.annulments.append(unavailable())
    assert await fiscal.annulling.annul(TENANT, annulment.id) is Step.UNREACHABLE
    assert await fiscal.annulling.annul(TENANT, annulment.id) is Step.DECIDED
    assert await fiscal.annulling.annul(TENANT, annulment.id) is Step.DECIDED
    assert fiscal.state.annulments[annulment.id].state is AnnulmentState.ACCEPTED
    decided = fiscal.state.events[-1]
    assert isinstance(decided, AnnulmentDecided)
    assert [kind for owner, kind, _ in fiscal.state.index if owner == annulment.id] == [
        ArchiveKind.ANNULMENT,
        ArchiveKind.ANSWER,
    ]
    assert (await queries.annulment(unit, annulment.id)).id == annulment.id


async def test_an_annulment_needs_a_profile_a_range_and_is_answered_once(fiscal: Fiscal) -> None:
    sequence = await fiscal.register(EcfType.CREDIT_INVOICE)
    unit = fiscal.units.bound(TENANT)
    with pytest.raises(SequenceNotFoundError):
        await ranges.annul(unit, SequenceId(new_id()), NumberRange(1, 1), company=COMPANY)
    with pytest.raises(AnnulmentNotFoundError):
        await fiscal.annulling.annul(TENANT, AnnulmentId(new_id()))
    with pytest.raises(AnnulmentNotFoundError):
        await queries.annulment(unit, AnnulmentId(new_id()))
    annulment = await ranges.annul(unit, sequence.id, NumberRange(90, 100), company=COMPANY)

    def decided_meanwhile() -> None:
        current = fiscal.state.annulments[annulment.id]
        fiscal.state.annulments[annulment.id] = replace(current, state=AnnulmentState.REJECTED)

    fiscal.dgii.before = decided_meanwhile
    assert await fiscal.annulling.annul(TENANT, annulment.id) is Step.DECIDED
    assert fiscal.state.annulments[annulment.id].state is AnnulmentState.REJECTED
    del fiscal.state.profiles[TENANT]
    with pytest.raises(ProfileMissingError):
        await ranges.annul(unit, sequence.id, NumberRange(1, 1), company=COMPANY)


# ── Ranges and their expiry ─────────────────────────────────────────────────────


async def test_ranges_of_a_type_never_overlap(fiscal: Fiscal) -> None:
    first = await fiscal.register(EcfType.CREDIT_INVOICE, NumberRange(1, 10))
    await fiscal.register(EcfType.CONSUMER_INVOICE, NumberRange(1, 10))
    with pytest.raises(SequenceOverlapError):
        await fiscal.register(EcfType.CREDIT_INVOICE, NumberRange(5, 20))
    assert fiscal.state.locks == 3
    unit = fiscal.units.bound(TENANT)
    assert (await queries.sequence(unit, first.id)).id == first.id
    assert len((await queries.sequences(unit, PageRequest())).items) == 2
    with pytest.raises(SequenceNotFoundError):
        await queries.sequence(unit, SequenceId(new_id()))


async def test_expiring_ranges_are_announced_once_per_threshold(fiscal: Fiscal) -> None:
    candidates = FakeExpiringRanges(fiscal.state)
    request = ranges.RangeRequest(
        ecf_type=EcfType.CREDIT_INVOICE,
        emission_point=POINT,
        numbers=NumberRange(1, 10),
        expires_on=NOW.date() + timedelta(days=20),
    )
    await ranges.register(fiscal.units.bound(TENANT), request)
    assert await ranges.announce_expiring(fiscal.units, candidates, clock=fiscal.clock) == 1
    assert await ranges.announce_expiring(fiscal.units, candidates, clock=fiscal.clock) == 0
    fiscal.clock.now = NOW + timedelta(days=14)
    stale = await candidates.due(NOW.date() + timedelta(days=60), 10)
    assert await ranges.announce_expiring(fiscal.units, candidates, clock=fiscal.clock) == 1
    read_before = FakeExpiringRanges(fiscal.state, stale)
    assert await ranges.announce_expiring(fiscal.units, read_before, clock=fiscal.clock) == 0
    expiring = [event.days_left for event in fiscal.state.events if isinstance(event, SequenceExpiring)]
    assert expiring == [30, 7]


# ── Profiles and reads ──────────────────────────────────────────────────────────


async def test_a_profile_is_created_once_then_replaced_at_the_version_read(fiscal: Fiscal) -> None:
    unit = fiscal.units.bound(TENANT)
    with pytest.raises(profiles.ProfileExistsError):
        await profiles.put_profile(unit, PROFILE, replaces=None)
    with pytest.raises(profiles.StaleProfileError):
        await profiles.put_profile(unit, PROFILE, replaces=7)
    moved = replace(PROFILE, environment=DgiiEnvironment.PRODUCTION)
    stored = await profiles.put_profile(unit, moved, replaces=1)
    assert (stored.version, stored.environment) == (2, DgiiEnvironment.PRODUCTION)
    assert await profiles.profile(unit) == stored
    del fiscal.state.profiles[TENANT]
    with pytest.raises(profiles.ProfileNotFoundError):
        await profiles.profile(unit)
    with pytest.raises(profiles.StaleProfileError):
        await profiles.put_profile(unit, PROFILE, replaces=1)
    assert (await profiles.put_profile(unit, PROFILE, replaces=None)).version == 1


async def test_a_profile_replaced_meanwhile_is_stale(fiscal: Fiscal) -> None:
    overtaken = FakeProfiles(fiscal.state, TENANT)
    overtaken.overtaken = True
    unit = replace(fiscal.units.bound(TENANT), profiles=overtaken)
    with pytest.raises(profiles.StaleProfileError):
        await profiles.put_profile(unit, PROFILE, replaces=1)


async def test_documents_are_read_in_their_company_only(fiscal: Fiscal) -> None:
    document = await fiscal.issued(EcfType.CREDIT_INVOICE)
    unit = fiscal.units.bound(TENANT)
    assert (await queries.document(unit, document.id)).id == document.id
    assert [found.id for found in (await queries.documents(unit, PageRequest())).items] == [document.id]
    stranger = fiscal.units.bound(Tenant(TENANT.organization, CompanyId(new_id())))
    with pytest.raises(DocumentNotFoundError):
        await queries.document(stranger, document.id)


async def test_a_range_is_dated_exactly_when_its_type_s_ranges_are(fiscal: Fiscal) -> None:
    unit = fiscal.units.bound(TENANT)
    for ecf_type, expires_on in (
        (EcfType.CREDIT_INVOICE, None),
        (EcfType.CONSUMER_INVOICE, date(2027, 12, 31)),
    ):
        request = ranges.RangeRequest(
            ecf_type=ecf_type, emission_point=POINT, numbers=HUNDRED, expires_on=expires_on
        )
        with pytest.raises(SequenceExpiryError):
            await ranges.register(unit, request)
    assert fiscal.state.sequences == {}
```


- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest services/fiscal-do/tests/test_application.py -q --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'ledger_fiscal_do.application.issuing'`.

- [ ] **Step 3: Implement**

`services/fiscal-do/src/ledger_fiscal_do/application/issuing.py`:

```python
"""Issuing an e-CF: a commercial document checked whole, numbered, and stored with its announcement.

§5.1, "Validation before sending": everything DGII checks that fiscal-do can check is checked first, and a
local failure is answered at once, before any e-NCF is taken. So, in one unit of work: the content's form and
its type's rules (``checked``), the company's profile, the reference of a note, the next number of a usable
range (under its row lock), and the XML the document will be checked against DGII's schema (still before the
unit commits: a schema's refusal rolls the number back). Then the document is stored, ``DocumentNumbered``
goes to the outbox with it, and, when the range runs low, ``SequenceLow``. Signing and sending are the
emission's, started by fiscal-do itself when it reads ``DocumentNumbered`` (Decision 7).
"""

from dataclasses import dataclass, field
from typing import ClassVar, final

from ledger_fiscal_do.application.ports import CompanyInactiveError
from ledger_fiscal_do.domain.checking import checked
from ledger_fiscal_do.domain.document import DocumentState, Draft
from ledger_fiscal_do.domain.encf import Encf, InvalidEncfError
from ledger_fiscal_do.domain.ids import DocumentId
from ledger_fiscal_do.domain.notation import local_date
from ledger_fiscal_do.domain.profile import ProfileMissingError
from ledger_fiscal_do.domain.sequence import NoSequenceError
from ledger_fiscal_do.domain.vocabulary import EcfType
from ledger_kernel.errors import InvalidInputError
from ledger_kernel.ids import new_id
lazy from ledger_fiscal_do.application.ports import CompanyFacts, FiscalUnit, FiscalXml
lazy from ledger_fiscal_do.domain.content import Content
lazy from ledger_fiscal_do.domain.document import FiscalDocument
lazy from ledger_fiscal_do.domain.ids import EmissionPointId
lazy from ledger_fiscal_do.domain.totals import Totals


class CreditExceedsOriginalError(InvalidInputError):
    """A credit note that would credit more than the document it modifies, with the notes before it."""

    code: ClassVar[str] = "credit_exceeds_original"

    def __init__(self) -> None:
        super().__init__("the credit notes of a document may total at most the document's own total")


class ReferenceRejectedError(InvalidInputError):
    """A note that modifies a document of the company's that DGII rejected: that document never counted."""

    code: ClassVar[str] = "reference_rejected"

    def __init__(self) -> None:
        super().__init__("the document referred to was rejected by DGII: issue a new document instead")


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class IssueRequest:
    """What a client asks to issue: the content, at one of the company's emission points."""

    emission_point: EmissionPointId
    content: Content = field(repr=False)


async def _check_reference(unit: FiscalUnit, content: Content, totals: Totals) -> None:
    """A note's reference, when it names a document of the company's: not rejected, never over-credited."""
    reference = content.reference
    if reference is None:
        return
    try:
        modified = Encf.parse(reference.modified)
    except InvalidEncfError:
        return  # a paper NCF, or an e-NCF fiscal-do never issued: DGII checks it
    original = await unit.documents.by_encf(modified)
    if original is None:
        return
    if original.state is DocumentState.REJECTED:
        raise ReferenceRejectedError
    if content.ecf_type is EcfType.CREDIT_NOTE:
        credited = await unit.documents.credited(modified)
        if credited + totals.total > original.totals.total:
            raise CreditExceedsOriginalError


async def issue(
    unit: FiscalUnit, request: IssueRequest, *, company: CompanyFacts, xml: FiscalXml
) -> FiscalDocument:
    """``request`` issued in the unit's company, as identity describes it (see the module)."""
    if not company.active:
        raise CompanyInactiveError
    content = request.content
    totals = checked(content)
    profile = await unit.profiles.get()
    if profile is None:
        raise ProfileMissingError
    await _check_reference(unit, content, totals)
    today = local_date(unit.now)
    sequence = await unit.sequences.usable(content.ecf_type, request.emission_point, on=today)
    if sequence is None:
        raise NoSequenceError(content.ecf_type)
    advanced, encf, low = sequence.take(on=today)
    draft = Draft(
        id=DocumentId(new_id()),
        tenant=unit.tenant,
        emission_point=request.emission_point,
        environment=profile.environment,
        issuer=profile.issuer(company.tax_id, company.legal_name),
        content=content,
        totals=totals,
        created_at=unit.now,
    )
    document, numbered = draft.numbered(encf, sequence.id, sequence.expires_on, now=unit.now)
    xml.ecf(document, signed_at=unit.now)
    await unit.sequences.update(advanced)
    await unit.documents.add(document)
    await unit.events.publish(numbered)
    if low is not None:
        await unit.events.publish(low)
    return document
```


`services/fiscal-do/src/ledger_fiscal_do/application/emission.py`:

```python
"""Taking a numbered document to DGII's final answer, one idempotent step at a time.

The emission workflow (``ledger_fiscal_do.workflows.emission``) decides when; these steps decide what, each
reading the document first and doing nothing twice:

* ``sign``: the e-CF written with its signing time, signed by vault, checked again against DGII's schema, its
  security code read, kept in the archive (WORM); a consumer invoice under DOP 250,000 also gets its summary
  (RFCE), signed and kept. No certificate: the document waits, ``blocked_certificate``.
* ``submit``: the e-CF to DGII (or the summary, answered at once). DGII unreachable: ``contingency``, and the
  next attempt first asks DGII which track ids it gave the e-NCF, so that a document DGII received before the
  connection failed is not sent twice. Refused at the door: ``rejected``, with DGII's words.
* ``poll``: DGII's answer by track id: still in process, or final (kept verbatim, archived, announced).

No unit of work spans a network call: each step reads in one unit, works outside, and records in another,
where the document is read again under its row lock (another replica may have recorded the step first).
"""

import hashlib
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from typing import ClassVar, Final, final
lazy from collections.abc import Callable
lazy from datetime import datetime

from ledger_fiscal_do.application.ports import (
    ArchiveKind,
    CertificateUnavailableError,
    Decision,
    DgiiCall,
    DgiiUnavailableError,
    Pending,
    Received,
)
from ledger_fiscal_do.domain.document import DocumentNotFoundError, DocumentState, Signature
from ledger_fiscal_do.domain.types.consumer_invoice import SUMMARY_THRESHOLD
from ledger_fiscal_do.domain.verdict import Outcome, Verdict
from ledger_fiscal_do.domain.vocabulary import EcfType
from ledger_kernel.errors import LedgerError
lazy from ledger_fiscal_do.application.ports import (
    Archive,
    Archived,
    Dgii,
    FiscalXml,
    Reception,
    Signer,
    Units,
)
lazy from ledger_fiscal_do.domain.document import FiscalDocument
lazy from ledger_fiscal_do.domain.ids import DocumentId, TrackId
lazy from ledger_kernel.ids import Tenant

RETENTION: Final = timedelta(days=3653)
"""Ten years (Código Tributario, art. 50 h; DGII's e-CF technical report, §9): what the archive keeps every
signed document and DGII answer for, at least."""
_SUBMITTABLE: Final = frozenset({DocumentState.SIGNED, DocumentState.CONTINGENCY})
_BLOCKABLE: Final = frozenset({DocumentState.NUMBERED, DocumentState.SIGNED, DocumentState.CONTINGENCY})


class Step(StrEnum):
    """Where a step left the document, for the workflow to decide what comes next."""

    SIGNED = "signed"
    BLOCKED = "blocked"
    """No usable certificate: wait for one."""
    SENT = "sent"
    UNREACHABLE = "unreachable"
    """DGII could not be reached: try again later."""
    PENDING = "pending"
    """DGII has not decided yet."""
    DECIDED = "decided"


class Stage(StrEnum):
    """A step, as a refusal names it."""

    SUBMIT = "submitted"
    SUMMARY = "submitted as a summary"
    POLL = "polled"


class NotReadyError(LedgerError):
    """A step asked of a document that is not at it: a defect of the workflow, never retried."""

    code: ClassVar[str] = "emission_step_out_of_order"

    def __init__(self, stage: Stage, state: DocumentState) -> None:
        super().__init__(f"a document that is {state.value} cannot be {stage.value}")


def _done_with_sending(document: FiscalDocument) -> Step | None:
    """Where a document DGII holds already stands; ``None`` while it is still to send."""
    if document.final:
        return Step.DECIDED
    return Step.SENT if document.state is DocumentState.SENT else None


def is_summary(document: FiscalDocument) -> bool:
    """Whether DGII receives the document as a summary: a consumer invoice under DOP 250,000."""
    consumer = document.content.ecf_type is EcfType.CONSUMER_INVOICE
    return consumer and document.totals.total < SUMMARY_THRESHOLD


def archive_key(document: FiscalDocument, kind: ArchiveKind, content: bytes) -> str:
    """Where ``content`` is kept: by environment, issuer, month and e-NCF, named by its kind and digest."""
    issued = document.content.issued_on
    digest = hashlib.sha256(content).hexdigest()[:16]
    extension = "json" if kind is ArchiveKind.ANSWER else "xml"
    return (
        f"{document.environment.value}/{document.issuer.tax_id}/{issued:%Y/%m}/{document.encf}"
        f"/{kind.value}-{digest}.{extension}"
    )


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Emission:
    """The steps of an emission (see the module), over their ports."""

    units: Units
    signer: Signer
    xml: FiscalXml
    archive: Archive
    dgii: Dgii
    clock: Callable[[], datetime]
    retention: timedelta = RETENTION

    async def _read(self, tenant: Tenant, document: DocumentId) -> FiscalDocument:
        async with self.units.unit(tenant) as unit:
            found = await unit.documents.get(document)
        if found is None:
            raise DocumentNotFoundError(document)
        return found

    async def _keep(self, document: FiscalDocument, kind: ArchiveKind, content: bytes) -> Archived:
        key = archive_key(document, kind, content)
        return await self.archive.keep(key, content, retain_until=self.clock() + self.retention)

    async def _blocked(self, tenant: Tenant, document: DocumentId) -> Step:
        async with self.units.unit(tenant) as unit:
            current = await unit.documents.locked(document)
            if current.state in _BLOCKABLE:
                await unit.documents.update(current.blocked(now=unit.now))
        return Step.BLOCKED

    async def sign(self, tenant: Tenant, document: DocumentId) -> Step:
        """Sign and archive ``document``, once (see the module)."""
        current = await self._read(tenant, document)
        if current.signature is not None:
            return Step.SIGNED
        signed_at = self.clock()
        summary = is_summary(current)
        try:
            signed = await self.signer.sign(
                tenant, self.xml.ecf(current, signed_at=signed_at), reference=str(current.encf)
            )
            code = self.xml.signed_ecf(current, signed)
            signed_summary = await self._signed_summary(current, code) if summary else None
        except CertificateUnavailableError:
            return await self._blocked(tenant, document)
        archived = await self._keep(current, ArchiveKind.ECF, signed)
        kept = (
            None if signed_summary is None else await self._keep(current, ArchiveKind.SUMMARY, signed_summary)
        )
        signature = Signature(
            signed_at=signed_at,
            security_code=code,
            archive_key=archived.key,
            sha256=archived.sha256,
            summary=summary,
        )
        async with self.units.unit(tenant) as unit:
            locked = await unit.documents.locked(document)
            if locked.signature is None:
                await unit.documents.update(locked.resumed(now=unit.now).signed(signature, now=unit.now))
                await unit.archive.add(document, ArchiveKind.ECF, archived)
                if kept is not None:
                    await unit.archive.add(document, ArchiveKind.SUMMARY, kept)
        return Step.SIGNED

    async def _signed_summary(self, document: FiscalDocument, security_code: str) -> bytes:
        summary = self.xml.summary(document, security_code=security_code)
        signed = await self.signer.sign(document.tenant, summary, reference=str(document.encf))
        self.xml.signed_summary(signed)
        return signed

    async def submit(self, tenant: Tenant, document: DocumentId) -> Step:
        """Send ``document`` to DGII, once (see the module)."""
        current = await self._read(tenant, document)
        done = _done_with_sending(current)
        if done is not None:
            return done
        if current.signature is None or current.state not in _SUBMITTABLE:
            raise NotReadyError(Stage.SUBMIT, current.state)
        try:
            if current.signature.summary:
                return await self._record_decision(current, await self._summary_decision(current), sent=True)
            reception = await self._reception(current, current.signature)
        except DgiiUnavailableError:
            return await self._unreachable(tenant, document)
        except CertificateUnavailableError:
            return await self._blocked(tenant, document)
        if isinstance(reception, Received):
            await self._keep(current, ArchiveKind.ANSWER, reception.answer)
            return await self._record_sent(current, reception.track_id)
        refused = Outcome(verdict=Verdict.REJECTED, messages=reception.messages, decided_at=self.clock())
        return await self._record_decision(current, Decision(refused, reception.answer), sent=True)

    def _call(self, document: FiscalDocument) -> DgiiCall:
        return DgiiCall(
            tenant=document.tenant, issuer=document.issuer.tax_id, environment=document.environment
        )

    async def _summary_decision(self, document: FiscalDocument) -> Decision:
        async with self.units.unit(document.tenant) as unit:
            kept = await unit.archive.find(document.id, ArchiveKind.SUMMARY)
        if kept is None:
            raise NotReadyError(Stage.SUMMARY, document.state)
        return await self.dgii.submit_summary(
            self._call(document), document.encf, await self.archive.read(kept.key)
        )

    async def _reception(self, document: FiscalDocument, signature: Signature) -> Reception:
        call = self._call(document)
        if document.state is DocumentState.CONTINGENCY:
            known = await self.dgii.track_ids(call, document.encf)
            if known:
                return Received(known[-1], b"")
        return await self.dgii.submit_ecf(call, document.encf, await self.archive.read(signature.archive_key))

    async def _unreachable(self, tenant: Tenant, document: DocumentId) -> Step:
        async with self.units.unit(tenant) as unit:
            current = await unit.documents.locked(document)
            if current.state in _SUBMITTABLE:
                await unit.documents.update(current.unreachable(now=unit.now))
        return Step.UNREACHABLE

    async def _record_sent(self, document: FiscalDocument, track_id: TrackId) -> Step:
        async with self.units.unit(document.tenant) as unit:
            locked = await unit.documents.locked(document.id)
            if locked.state in _SUBMITTABLE:
                await unit.documents.update(locked.sent(track_id, now=unit.now))
        return Step.SENT

    async def _record_decision(self, document: FiscalDocument, decision: Decision, *, sent: bool) -> Step:
        answer = await self._keep(document, ArchiveKind.ANSWER, decision.answer)
        async with self.units.unit(document.tenant) as unit:
            locked = await unit.documents.locked(document.id)
            if locked.final:
                return Step.DECIDED
            moved = locked.sent(None, now=unit.now) if sent and locked.state in _SUBMITTABLE else locked
            decided, event = moved.decided(decision.outcome, now=unit.now)
            await unit.documents.update(decided)
            await unit.archive.add(document.id, ArchiveKind.ANSWER, answer)
            await unit.events.publish(event)
        return Step.DECIDED

    async def poll(self, tenant: Tenant, document: DocumentId) -> Step:
        """DGII's answer on ``document``, recorded once final (see the module)."""
        current = await self._read(tenant, document)
        if current.final:
            return Step.DECIDED
        if current.state is not DocumentState.SENT or current.track_id is None:
            raise NotReadyError(Stage.POLL, current.state)
        try:
            result = await self.dgii.result(self._call(current), current.track_id)
        except DgiiUnavailableError:
            return Step.UNREACHABLE
        except CertificateUnavailableError:
            return Step.BLOCKED
        if isinstance(result, Pending):
            return Step.PENDING
        return await self._record_decision(current, result, sent=False)

    async def resume(self, tenant: Tenant, document: DocumentId) -> None:
        """A certificate may be usable again: a blocked document goes back where it stood."""
        async with self.units.unit(tenant) as unit:
            current = await unit.documents.locked(document)
            if current.state is DocumentState.BLOCKED_CERTIFICATE:
                await unit.documents.update(current.resumed(now=unit.now))
```


`services/fiscal-do/src/ledger_fiscal_do/application/annulling.py`:

```python
"""Telling DGII of an annulment (ANECF): written, signed, kept, sent, and DGII's answer recorded, once."""

import hashlib
from dataclasses import dataclass
from typing import final
lazy from collections.abc import Callable
lazy from datetime import datetime, timedelta

from ledger_fiscal_do.application.emission import RETENTION, Step
from ledger_fiscal_do.application.ports import (
    ArchiveKind,
    CertificateUnavailableError,
    DgiiCall,
    DgiiUnavailableError,
)
from ledger_fiscal_do.domain.annulment import AnnulmentNotFoundError, AnnulmentState
lazy from ledger_fiscal_do.application.ports import Archive, Archived, Dgii, FiscalXml, Signer, Units
lazy from ledger_fiscal_do.domain.annulment import Annulment
lazy from ledger_fiscal_do.domain.ids import AnnulmentId
lazy from ledger_kernel.ids import Tenant


def annulment_key(annulment: Annulment, kind: ArchiveKind, content: bytes) -> str:
    """Where an annulment's documents are kept: by environment, issuer, request date and annulment."""
    digest = hashlib.sha256(content).hexdigest()[:16]
    extension = "json" if kind is ArchiveKind.ANSWER else "xml"
    return (
        f"{annulment.environment.value}/{annulment.issuer}/{annulment.requested_at:%Y/%m}/annulments"
        f"/{annulment.id}/{kind.value}-{digest}.{extension}"
    )


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Annulling:
    """The one step of an annulment's workflow."""

    units: Units
    signer: Signer
    xml: FiscalXml
    archive: Archive
    dgii: Dgii
    clock: Callable[[], datetime]
    retention: timedelta = RETENTION

    async def annul(self, tenant: Tenant, annulment: AnnulmentId) -> Step:
        """Tell DGII of ``annulment``, unless its answer is recorded already."""
        async with self.units.unit(tenant) as unit:
            current = await unit.annulments.get(annulment)
        if current is None:
            raise AnnulmentNotFoundError(annulment)
        if current.state is not AnnulmentState.REQUESTED:
            return Step.DECIDED
        unsigned = self.xml.annulment(current, at=self.clock())
        call = DgiiCall(tenant=tenant, issuer=current.issuer, environment=current.environment)
        try:
            signed = await self.signer.sign(tenant, unsigned, reference=f"ANECF-{current.id}")
            self.xml.signed_annulment(signed)
            kept = await self._keep(current, ArchiveKind.ANNULMENT, signed)
            decision = await self.dgii.annul(call, signed)
        except CertificateUnavailableError:
            return Step.BLOCKED
        except DgiiUnavailableError:
            return Step.UNREACHABLE
        answer = await self._keep(current, ArchiveKind.ANSWER, decision.answer)
        async with self.units.unit(tenant) as unit:
            locked = await unit.annulments.locked(annulment)
            if locked.state is not AnnulmentState.REQUESTED:
                return Step.DECIDED
            decided, event = locked.decided(decision.outcome)
            await unit.annulments.update(decided)
            await unit.archive.add(annulment, ArchiveKind.ANNULMENT, kept)
            await unit.archive.add(annulment, ArchiveKind.ANSWER, answer)
            await unit.events.publish(event)
        return Step.DECIDED

    async def _keep(self, annulment: Annulment, kind: ArchiveKind, content: bytes) -> Archived:
        key = annulment_key(annulment, kind, content)
        return await self.archive.keep(key, content, retain_until=self.clock() + self.retention)
```


`services/fiscal-do/src/ledger_fiscal_do/application/ranges.py`:

```python
"""The company's e-NCF ranges: registered as DGII authorized them, annulled where unused, announced expiring.

Registering takes the company's lock first, so two registrations at once serialize and the second sees the
first's range: no two ranges of a type share a number, whatever their emission point (DGII numbers per RNC
and type). An annulment withdraws unused numbers in the unit that records it, and ``AnnulmentRequested`` makes
fiscal-do tell DGII (Decision 7). Expiring ranges are announced 30, 7 and 1 days ahead, each once, read across
companies and announced in each company's own unit, as vault announces expiring certificates.
"""

from dataclasses import dataclass
from typing import Final, final
lazy from collections.abc import Callable
lazy from datetime import date, datetime, timedelta

from ledger_fiscal_do.domain.annulment import Annulment
from ledger_fiscal_do.domain.events import SequenceExpiring
from ledger_fiscal_do.domain.ids import AnnulmentId, SequenceId
from ledger_fiscal_do.domain.notation import local_date
from ledger_fiscal_do.domain.profile import ProfileMissingError
from ledger_fiscal_do.domain.registry import RULES
from ledger_fiscal_do.domain.sequence import (
    EXPIRY_THRESHOLDS,
    EncfSequence,
    SequenceExpiryError,
    SequenceNotFoundError,
    due_expiry_announcement,
    require_no_overlap,
)
from ledger_kernel.ids import new_id
lazy from ledger_fiscal_do.application.ports import CompanyFacts, ExpiringRanges, FiscalUnit, Units
lazy from ledger_fiscal_do.domain.ids import EmissionPointId
lazy from ledger_fiscal_do.domain.sequence import NumberRange
lazy from ledger_fiscal_do.domain.vocabulary import EcfType

ANNOUNCEMENTS_PER_PASS: Final = 500


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class RangeRequest:
    """A range DGII authorized: its type, the emission point that uses it, its numbers and expiry."""

    ecf_type: EcfType
    emission_point: EmissionPointId
    numbers: NumberRange
    expires_on: date | None


async def register(unit: FiscalUnit, request: RangeRequest) -> EncfSequence:
    """Register ``request`` for the unit's company.

    ``SequenceExpiryError`` when it is dated and its type's ranges are not, or the other way round;
    ``SequenceOverlapError`` when it shares a number with another range of its type.
    """
    dated = RULES[request.ecf_type].sequence_expires
    if dated != (request.expires_on is not None):
        raise SequenceExpiryError(request.ecf_type, dated=dated)
    await unit.sequences.lock()
    require_no_overlap(request.numbers, await unit.sequences.of_type(request.ecf_type))
    sequence = EncfSequence.registered(
        (SequenceId(new_id()), unit.tenant, request.emission_point),
        ecf_type=request.ecf_type,
        numbers=request.numbers,
        expires_on=request.expires_on,
        now=unit.now,
    )
    await unit.sequences.add(sequence)
    return sequence


async def annul(
    unit: FiscalUnit, sequence: SequenceId, numbers: NumberRange, *, company: CompanyFacts
) -> Annulment:
    """Withdraw ``numbers`` of ``sequence``, unused, and have DGII told (``AnnulmentRequested``)."""
    profile = await unit.profiles.get()
    if profile is None:
        raise ProfileMissingError
    current = await unit.sequences.locked(sequence)
    if current is None:
        raise SequenceNotFoundError(sequence)
    annulment, withdrawn, requested = Annulment.requested(
        AnnulmentId(new_id()), current, numbers, issuer=(company.tax_id, profile.environment), now=unit.now
    )
    await unit.sequences.update(withdrawn)
    await unit.annulments.add(annulment)
    await unit.events.publish(requested)
    return annulment


async def announce_expiring(
    units: Units,
    candidates: ExpiringRanges,
    *,
    clock: Callable[[], datetime],
    limit: int = ANNOUNCEMENTS_PER_PASS,
) -> int:
    """Announce every range expiry due now, each in its company's unit; how many were announced."""
    today = local_date(clock())
    horizon = today + timedelta(days=max(EXPIRY_THRESHOLDS))
    announced = 0
    for watched in await candidates.due(horizon, limit):
        days = due_expiry_announcement(watched.expires_on, today, watched.announced)
        if days is None:
            continue
        async with units.unit(watched.tenant) as unit:
            if not await unit.sequences.claim_expiry(watched.sequence, days):
                continue  # another replica announced it first
            expiring = SequenceExpiring(
                tenant=watched.tenant,
                sequence=watched.sequence,
                ecf_type=watched.ecf_type,
                expires_on=watched.expires_on,
                days_left=days,
            )
            await unit.events.publish(expiring)
        announced += 1
    return announced
```


`services/fiscal-do/src/ledger_fiscal_do/application/profiles.py`:

```python
"""A company's fiscal profile: created once, then replaced at the version read (compare-and-set)."""

from typing import ClassVar

from ledger_kernel.errors import ConflictError, NotFoundError
lazy from ledger_fiscal_do.application.ports import FiscalUnit
lazy from ledger_fiscal_do.domain.profile import FiscalProfile


class ProfileExistsError(ConflictError):
    """The company has a profile: replacing it names the version replaced (``If-Match``)."""

    code: ClassVar[str] = "fiscal_profile_exists"

    def __init__(self) -> None:
        super().__init__("the company has a fiscal profile: name the version you replace")


class StaleProfileError(ConflictError):
    """The profile is not at the version named: someone changed it meanwhile."""

    code: ClassVar[str] = "stale_version"

    def __init__(self) -> None:
        super().__init__("the fiscal profile is not at the version named")


class ProfileNotFoundError(NotFoundError):
    """The company has no fiscal profile yet."""

    code: ClassVar[str] = "fiscal_profile_not_found"

    def __init__(self) -> None:
        super().__init__("the company has no fiscal profile yet")


async def put_profile(unit: FiscalUnit, profile: FiscalProfile, *, replaces: int | None) -> FiscalProfile:
    """Keep ``profile`` as the company's first (``replaces`` ``None``) or in place of version ``replaces``."""
    current = await unit.profiles.get()
    if current is None and replaces is not None:
        raise StaleProfileError
    if current is not None and replaces is None:
        raise ProfileExistsError
    if current is not None and replaces != current.version:
        raise StaleProfileError
    stored = profile if current is None else current.changed(profile)
    if not await unit.profiles.put(stored):
        raise StaleProfileError
    return stored


async def profile(unit: FiscalUnit) -> FiscalProfile:
    """The company's profile; ``ProfileNotFoundError`` before its first."""
    found = await unit.profiles.get()
    if found is None:
        raise ProfileNotFoundError
    return found
```


`services/fiscal-do/src/ledger_fiscal_do/application/queries.py`:

```python
"""What reads fiscal-do's state, each in a unit scoped to the asking company."""

from ledger_fiscal_do.domain.annulment import AnnulmentNotFoundError
from ledger_fiscal_do.domain.document import DocumentNotFoundError
from ledger_fiscal_do.domain.sequence import SequenceNotFoundError
lazy from ledger_fiscal_do.application.ports import FiscalUnit
lazy from ledger_fiscal_do.domain.annulment import Annulment
lazy from ledger_fiscal_do.domain.document import FiscalDocument
lazy from ledger_fiscal_do.domain.ids import AnnulmentId, DocumentId, SequenceId
lazy from ledger_fiscal_do.domain.sequence import EncfSequence
lazy from ledger_kernel.pagination import Page, PageRequest


async def document(unit: FiscalUnit, document_id: DocumentId) -> FiscalDocument:
    """One of the company's documents; ``DocumentNotFoundError`` otherwise."""
    found = await unit.documents.get(document_id)
    if found is None:
        raise DocumentNotFoundError(document_id)
    return found


async def documents(unit: FiscalUnit, request: PageRequest) -> Page[FiscalDocument]:
    """The company's documents, a page at a time, oldest first."""
    return await unit.documents.page(request)


async def sequence(unit: FiscalUnit, sequence_id: SequenceId) -> EncfSequence:
    """One of the company's ranges; ``SequenceNotFoundError`` otherwise."""
    found = await unit.sequences.get(sequence_id)
    if found is None:
        raise SequenceNotFoundError(sequence_id)
    return found


async def sequences(unit: FiscalUnit, request: PageRequest) -> Page[EncfSequence]:
    """The company's ranges, a page at a time, oldest first."""
    return await unit.sequences.page(request)


async def annulment(unit: FiscalUnit, annulment_id: AnnulmentId) -> Annulment:
    """One of the company's annulments; ``AnnulmentNotFoundError`` otherwise."""
    found = await unit.annulments.get(annulment_id)
    if found is None:
        raise AnnulmentNotFoundError(annulment_id)
    return found
```


- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest services/fiscal-do/tests -q --no-cov`
Expected: PASS.

- [ ] **Step 5: Gate**

Run: `uv run poe fmt && uv run poe check`
Expected: every step passes, coverage 100 %.

- [ ] **Step 6: Commit**

```bash
git add services/fiscal-do
git commit -m "feat(fiscal-do): issuing, and emission and annulment as idempotent steps over ports

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: DGII's client (tokens, retries, a circuit breaker), and a fake DGII with teeth

**Files:**
- Create: `services/fiscal-do/src/ledger_fiscal_do/adapters/dgii/__init__.py`, `adapters/dgii/endpoints.py`, `adapters/dgii/breaker.py`, `adapters/dgii/wire.py`, `adapters/dgii/client.py`, `libs/testing/src/ledger_testing/dgii.py`
- Modify: `services/fiscal-do/pyproject.toml`, `libs/testing/pyproject.toml`
- Test: `services/fiscal-do/tests/test_dgii_client.py`, `libs/testing/tests/test_dgii.py`

**Interfaces:**
- Consumes: the ports `Dgii`, `DgiiCall`, `Signer`, `Received`, `Refused`, `Decision`, `Pending`, `Failure`, `DgiiUnavailableError`, `DgiiOutcomeUnknownError` (Task 4); `ledger_kernel.clock.MonotonicClock`, `ledger_kernel.errors.error_type`; `ledger_dgii` (Task 1); `fakes.FakeSigner`, `fakes.vault_signed`, `fakes.Clock` (Task 5); the golden files (Task 4).
- Produces: `adapters.dgii.endpoints`: `Service`, `PATHS`, `offered(service, environment)`, `ServiceUnavailableInEnvironmentError` (`dgii_service_not_in_environment`), `DgiiEndpoints(ecf, fc).url(service, environment)`. `adapters.dgii.breaker`: `BreakerState`, `DgiiCircuitOpenError` (`dgii_circuit_open`), `CircuitBreaker(*, failures=5, cooldown=30, max_cooldown=600, clock=monotonic)` with `state`, `admit`, `succeeded`, `failed`. `adapters.dgii.wire`: DGII's JSON answers (`TokenAnswer`, `Message`, `ReceptionAnswer`, `ResultAnswer`, `SummaryAnswer`, `TrackIdAnswer`, `AnnulmentAnswer`). `adapters.dgii.client`: `MAX_ANSWER`, `TOKEN_MARGIN`, `TOKEN_CEILING`, `RETRY_DELAYS`, `OFFICIAL`, `DgiiAnswerError` (`dgii_answer_unreadable`), `DgiiTimeouts(connect, total)`, `DgiiOptions(endpoints, timeouts, breaker, clock, transport)`, `DgiiMetrics(registry)`, `DgiiClient(signer, options, *, metrics=None, tracer=None)` with `opened()` and `breaker(environment)`, implementing `Dgii`. `ledger_testing.dgii`: `Status`, `STATUS_WORDS`, `SignatureCheck`, `TrustingCheck`, `Scripted(status, messages, processing)`, `Script(by_encf, default, outage, latency)`, `Received`, `FakeDgii(check, *, clock=None)` with `app`, `script`, `received`, `token_for_test()`, `revoke_tokens()`, and `create_app()` (the local stack's: every signature trusted).

Re-check before starting: `ledger_kernel.clock` names (`MonotonicClock`) and `ledger_kernel.errors.error_type` as Milestone 1's fix wave left them; `ledger_testing`'s `pyproject.toml` as Milestones 2 and 3 left it (this task adds to its dependencies).

- [ ] **Step 1: Dependencies**

`services/fiscal-do/pyproject.toml` — `dependencies` gains `"httpx>=0.28.1"`, `"opentelemetry-api>=1.45.1"`, `"prometheus-client>=0.26.0"`, `"pydantic>=2.14.1"`, and:

```toml
[tool.deptry.package_module_name_map]
"opentelemetry-api" = "opentelemetry"
"prometheus-client" = "prometheus_client"
```

`libs/testing/pyproject.toml` — `dependencies` gains `"ledger-dgii"`, `"httpx>=0.28.1"`, `"lxml>=6.1.3"`, `"python-multipart>=0.0.32"`, `"starlette>=1.7.0"`, `"xmlschema>=4.3.2"` (`ledger-dgii = { workspace = true }` in `[tool.uv.sources]`), and, because Starlette imports `python_multipart` itself when it reads a form:

```toml
[tool.deptry.per_rule_ignores]
DEP002 = ["python-multipart"]  # Starlette's form parser imports it; nothing here does by name
```

Root: "fiscal-do is hexagonal" is unchanged (`adapters/dgii` sits in `adapters`). Run: `uv lock && uv sync`.

- [ ] **Step 2: Write the failing tests**

`services/fiscal-do/tests/test_dgii_client.py`:

```python
from datetime import timedelta
from pathlib import Path
from typing import Final, final
lazy from collections.abc import AsyncGenerator, Awaitable, Callable

import httpx
import pytest
from prometheus_client import CollectorRegistry

from ledger_fiscal_do.adapters.dgii.breaker import BreakerState, CircuitBreaker, DgiiCircuitOpenError
from ledger_fiscal_do.adapters.dgii.client import DgiiAnswerError, DgiiClient, DgiiMetrics, DgiiOptions
from ledger_fiscal_do.adapters.dgii.endpoints import (
    DgiiEndpoints,
    Service,
    ServiceUnavailableInEnvironmentError,
)
from ledger_fiscal_do.application.ports import (
    Decision,
    DgiiCall,
    DgiiOutcomeUnknownError,
    DgiiUnavailableError,
    Failure,
    Pending,
    Received,
    Refused,
)
from ledger_fiscal_do.domain.encf import Encf
from ledger_fiscal_do.domain.ids import TrackId
from ledger_fiscal_do.domain.verdict import DgiiMessage, Verdict
from ledger_fiscal_do.domain.vocabulary import OFFICIAL_ECF, OFFICIAL_FC, DgiiEnvironment
from ledger_kernel.countries.do import TaxId
from ledger_testing.dgii import FakeDgii, Scripted, Status, TrustingCheck

from fakes import Clock, FakeSigner, vault_signed
from samples import NOW, TENANT

GOLDEN: Final = Path(__file__).parent / "golden"
HOSTS: Final = DgiiEndpoints(ecf="http://ecf.dgii.test", fc="http://fc.dgii.test")
CALL: Final = DgiiCall(
    tenant=TENANT, issuer=TaxId.parse("101850043"), environment=DgiiEnvironment.PRECERTIFICATION
)
CERTIFICATION: Final = DgiiCall(tenant=TENANT, issuer=CALL.issuer, environment=DgiiEnvironment.CERTIFICATION)
FIRST: Final = Encf.parse("E310000000001")
REJECTION: Final = (("613", "El RNC del comprador no existe"),)


def signed(name: str) -> bytes:
    return vault_signed((GOLDEN / name).read_bytes())


@final
class Ticks:
    """A monotonic clock a test moves by hand."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@final
class Dgii:
    """A client and the fake DGII it talks to, with the clocks, signer and metrics a test looks at."""

    def __init__(self) -> None:
        self.clock = Clock(NOW)
        self.ticks = Ticks()
        self.fake = FakeDgii(TrustingCheck(), clock=self.clock)
        self.signer = FakeSigner()
        self.registry = CollectorRegistry()
        self.metrics = DgiiMetrics(self.registry)
        self.client = self.connected(httpx.ASGITransport(app=self.fake.app))

    def connected(self, transport: httpx.AsyncBaseTransport) -> DgiiClient:
        options = DgiiOptions(
            endpoints=HOSTS,
            breaker=lambda: CircuitBreaker(failures=3, cooldown=30, clock=self.ticks),
            clock=self.clock,
            transport=transport,
        )
        return DgiiClient(self.signer, options, metrics=self.metrics)

    def metric(self, name: str, **labels: str) -> float | None:
        return self.registry.get_sample_value(name, labels)


@pytest.fixture
async def dgii() -> AsyncGenerator[Dgii]:
    setup = Dgii()
    async with setup.client.opened():
        yield setup


def answering(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.MockTransport:
    """A transport whose seed and token are DGII's, and whose every other answer is ``handler``'s."""

    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/semilla"):
            return httpx.Response(200, content=b"<SemillaModel><valor>abc</valor></SemillaModel>")
        if request.url.path.endswith("/validarsemilla"):
            return httpx.Response(200, json={"token": "t", "expira": "2026-10-12T14:30:00Z"})
        return handler(request)

    return httpx.MockTransport(answer)


def failing(error: type[httpx.TransportError]) -> Callable[[httpx.Request], httpx.Response]:
    def fail(request: httpx.Request) -> httpx.Response:
        message = "down"
        raise error(message, request=request)

    return fail


async def test_an_ecf_is_received_then_accepted(dgii: Dgii) -> None:
    reception = await dgii.client.submit_ecf(CALL, FIRST, signed("ecf-31.xml"))
    assert isinstance(reception, Received)
    result = await dgii.client.result(CALL, reception.track_id)
    assert isinstance(result, Decision)
    assert result.outcome.verdict is Verdict.ACCEPTED
    assert result.outcome.decided_at == NOW
    assert b'"codigo":1' in result.answer
    assert [each.kind for each in dgii.fake.received] == ["ecf"]
    assert dgii.signer.seeds == 1


async def test_every_ecf_type_passes_the_fakes_schema_check(dgii: Dgii) -> None:
    for path in sorted(GOLDEN.glob("ecf-*.xml")):
        encf = Encf.parse(f"E{path.stem[4:]}0000000001")
        reception = await dgii.client.submit_ecf(CALL, encf, signed(path.name))
        assert isinstance(reception, Received)
        result = await dgii.client.result(CALL, reception.track_id)
        assert isinstance(result, Decision)
        assert result.outcome.verdict is Verdict.ACCEPTED, path.name


async def test_a_result_in_process_is_pending(dgii: Dgii) -> None:
    dgii.fake.script.default = Scripted(
        status=Status.ACCEPTED_CONDITIONALLY, messages=REJECTION, processing=1
    )
    reception = await dgii.client.submit_ecf(CALL, FIRST, signed("ecf-31.xml"))
    assert isinstance(reception, Received)
    assert isinstance(await dgii.client.result(CALL, reception.track_id), Pending)
    result = await dgii.client.result(CALL, reception.track_id)
    assert isinstance(result, Decision)
    assert result.outcome.verdict is Verdict.ACCEPTED_CONDITIONALLY
    assert result.outcome.messages == (DgiiMessage("613", "El RNC del comprador no existe"),)


async def test_an_unknown_track_id_is_pending(dgii: Dgii) -> None:
    assert isinstance(await dgii.client.result(CALL, TrackId("unknown")), Pending)


async def test_a_scripted_rejection_carries_dgiis_messages(dgii: Dgii) -> None:
    dgii.fake.script.by_encf[str(FIRST)] = Scripted(status=Status.REJECTED, messages=REJECTION)
    reception = await dgii.client.submit_ecf(CALL, FIRST, signed("ecf-31.xml"))
    assert isinstance(reception, Received)
    result = await dgii.client.result(CALL, reception.track_id)
    assert isinstance(result, Decision)
    assert result.outcome.verdict is Verdict.REJECTED
    assert result.outcome.messages[0].code == "613"


async def test_an_unsigned_document_is_rejected_by_the_fakes_schema_check(dgii: Dgii) -> None:
    reception = await dgii.client.submit_ecf(CALL, FIRST, (GOLDEN / "ecf-31.xml").read_bytes())
    assert isinstance(reception, Received)
    result = await dgii.client.result(CALL, reception.track_id)
    assert isinstance(result, Decision)
    assert result.outcome.verdict is Verdict.REJECTED
    assert "XSD" in result.outcome.messages[0].text


async def test_a_document_that_is_not_xml_is_refused_at_reception(dgii: Dgii) -> None:
    reception = await dgii.client.submit_ecf(CALL, FIRST, b"not xml")
    assert isinstance(reception, Refused)
    assert reception.messages[0].code == "400"


async def test_a_summary_is_decided_at_once(dgii: Dgii) -> None:
    decision = await dgii.client.submit_summary(CALL, Encf.parse("E320000000001"), signed("rfce-32.xml"))
    assert decision.outcome.verdict is Verdict.ACCEPTED
    assert [each.kind for each in dgii.fake.received] == ["rfce"]


async def test_track_ids_list_an_encfs_receptions_in_order(dgii: Dgii) -> None:
    assert await dgii.client.track_ids(CALL, FIRST) == ()
    first = await dgii.client.submit_ecf(CALL, FIRST, signed("ecf-31.xml"))
    dgii.clock.now += timedelta(seconds=1)
    second = await dgii.client.submit_ecf(CALL, FIRST, signed("ecf-31.xml"))
    assert isinstance(first, Received)
    assert isinstance(second, Received)
    assert await dgii.client.track_ids(CALL, FIRST) == (first.track_id, second.track_id)


async def test_certification_has_no_track_ids_or_annulments(dgii: Dgii) -> None:
    assert await dgii.client.track_ids(CERTIFICATION, FIRST) == ()
    with pytest.raises(ServiceUnavailableInEnvironmentError):
        await dgii.client.annul(CERTIFICATION, signed("anecf.xml"))


async def test_an_annulment_is_decided_at_once(dgii: Dgii) -> None:
    decision = await dgii.client.annul(CALL, signed("anecf.xml"))
    assert decision.outcome.verdict is Verdict.ACCEPTED
    assert decision.outcome.messages[0].code == "1"


async def test_a_refused_annulment_is_rejected(dgii: Dgii) -> None:
    decision = await dgii.client.annul(CALL, (GOLDEN / "anecf.xml").read_bytes())
    assert decision.outcome.verdict is Verdict.REJECTED


async def test_the_token_is_reused_until_shortly_before_it_expires(dgii: Dgii) -> None:
    await dgii.client.result(CALL, TrackId("a"))
    dgii.clock.now += timedelta(minutes=54)
    await dgii.client.result(CALL, TrackId("a"))
    assert dgii.signer.seeds == 1
    dgii.clock.now += timedelta(minutes=2)
    await dgii.client.result(CALL, TrackId("a"))
    assert dgii.signer.seeds == 2


async def test_a_refused_token_is_replaced_once(dgii: Dgii) -> None:
    await dgii.client.result(CALL, TrackId("a"))
    dgii.fake.revoke_tokens()
    assert isinstance(await dgii.client.result(CALL, TrackId("a")), Pending)
    assert dgii.signer.seeds == 2


async def test_idempotent_calls_are_retried_through_a_short_outage(dgii: Dgii) -> None:
    await dgii.client.result(CALL, TrackId("a"))
    dgii.fake.script.outage = 2
    assert isinstance(await dgii.client.result(CALL, TrackId("a")), Pending)
    assert (
        dgii.metric(
            "ledger_fiscal_do_dgii_calls_total", environment="testecf", service="result", outcome="failed"
        )
        == 2
    )


async def test_a_document_is_never_resent_by_the_client(dgii: Dgii) -> None:
    await dgii.client.result(CALL, TrackId("a"))
    dgii.fake.script.outage = 1
    with pytest.raises(DgiiUnavailableError) as raised:
        await dgii.client.submit_ecf(CALL, FIRST, signed("ecf-31.xml"))
    assert raised.value.failure is Failure.SERVER_ERROR
    assert dgii.fake.received == []
    assert dgii.fake.script.outage == 0


async def test_the_circuit_opens_after_failures_and_lets_a_probe_through_later(dgii: Dgii) -> None:
    dgii.fake.script.outage = -1
    with pytest.raises(DgiiUnavailableError):
        await dgii.client.result(CALL, TrackId("a"))
    assert dgii.client.breaker(DgiiEnvironment.PRECERTIFICATION).state is BreakerState.OPEN
    assert dgii.metric("ledger_fiscal_do_dgii_circuit_open", environment="testecf") == 1
    with pytest.raises(DgiiCircuitOpenError):
        await dgii.client.result(CALL, TrackId("a"))
    assert (
        dgii.metric(
            "ledger_fiscal_do_dgii_calls_total", environment="testecf", service="seed", outcome="open"
        )
        == 1
    )
    dgii.fake.script.outage = 0
    dgii.ticks.now += 30
    assert isinstance(await dgii.client.result(CALL, TrackId("a")), Pending)
    assert dgii.client.breaker(DgiiEnvironment.PRECERTIFICATION).state is BreakerState.CLOSED
    assert dgii.metric("ledger_fiscal_do_dgii_circuit_open", environment="testecf") == 0


async def test_a_client_not_open_calls_nobody() -> None:
    setup = Dgii()
    with pytest.raises(DgiiUnavailableError) as raised:
        await setup.client.result(CALL, TrackId("a"))
    assert raised.value.failure is Failure.NOT_OPEN


@pytest.mark.parametrize(
    ("error", "failure"),
    [(httpx.ConnectError, Failure.UNREACHABLE), (httpx.ReadError, Failure.BROKEN)],
)
async def test_a_failed_idempotent_call_is_unavailable(
    error: type[httpx.TransportError], failure: Failure
) -> None:
    setup = Dgii()
    client = setup.connected(answering(failing(error)))
    async with client.opened():
        with pytest.raises(DgiiUnavailableError) as raised:
            await client.result(CALL, TrackId("a"))
    assert raised.value.failure is failure
    assert not isinstance(raised.value, DgiiOutcomeUnknownError)


async def test_a_document_cut_off_in_transit_has_an_unknown_outcome() -> None:
    setup = Dgii()
    client = setup.connected(answering(failing(httpx.ReadError)))
    async with client.opened():
        with pytest.raises(DgiiOutcomeUnknownError):
            await client.submit_ecf(CALL, FIRST, signed("ecf-31.xml"))
    assert (
        setup.metric(
            "ledger_fiscal_do_dgii_calls_total", environment="testecf", service="reception", outcome="unknown"
        )
        == 1
    )


@pytest.mark.parametrize(
    ("service", "answer"),
    [
        (Service.RESULT, httpx.Response(200, content=b"<html>")),
        (Service.RESULT, httpx.Response(200, json={"codigo": 9})),
        (Service.RESULT, httpx.Response(200, content=b" " * (1024 * 1024 + 1))),
        (Service.SUMMARY, httpx.Response(200, json={"codigo": 3})),
    ],
)
async def test_an_answer_that_cannot_be_read_is_unavailable(service: Service, answer: httpx.Response) -> None:
    setup = Dgii()
    client = setup.connected(answering(lambda _: answer))
    async with client.opened():
        with pytest.raises(DgiiAnswerError):
            await asking(client, service)


def asking(client: DgiiClient, service: Service) -> Awaitable[object]:
    """The call to ``service`` a test of unreadable answers makes."""
    if service is Service.RESULT:
        return client.result(CALL, TrackId("a"))
    return client.submit_summary(CALL, FIRST, b"<RFCE/>")


async def test_a_token_with_an_unreadable_expiry_is_unavailable() -> None:
    setup = Dgii()

    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/semilla"):
            return httpx.Response(200, content=b"<SemillaModel/>")
        return httpx.Response(200, json={"token": "t", "expira": "tomorrow"})

    client = setup.connected(httpx.MockTransport(answer))
    async with client.opened():
        with pytest.raises(DgiiAnswerError):
            await client.result(CALL, TrackId("a"))


def test_every_service_has_its_url_on_its_host() -> None:
    official = DgiiEndpoints()
    environment = DgiiEnvironment.PRODUCTION
    assert (
        official.url(Service.SEED, environment)
        == f"{OFFICIAL_ECF}/ecf/autenticacion/api/autenticacion/semilla"
    )
    assert official.url(Service.SUMMARY, environment) == f"{OFFICIAL_FC}/ecf/recepcionfc/api/recepcion/ecf"
    assert all(official.url(service, environment).startswith("https://") for service in Service)


def state(breaker: CircuitBreaker) -> BreakerState:
    """Where ``breaker`` stands, read afresh (a property a type checker would narrow)."""
    return breaker.state


def test_a_failed_probe_reopens_the_breaker_for_twice_as_long_up_to_a_ceiling() -> None:
    ticks = Ticks()
    breaker = CircuitBreaker(failures=1, cooldown=10, max_cooldown=15, clock=ticks)
    breaker.failed()
    assert state(breaker) is BreakerState.OPEN
    ticks.now = 10
    assert state(breaker) is BreakerState.HALF_OPEN
    breaker.admit("testecf")
    with pytest.raises(DgiiCircuitOpenError):
        breaker.admit("testecf")
    breaker.failed()
    ticks.now = 24
    assert state(breaker) is BreakerState.OPEN
    ticks.now = 25
    breaker.admit("testecf")
    breaker.succeeded()
    assert state(breaker) is BreakerState.CLOSED
    breaker.admit("testecf")


async def test_a_client_with_the_defaults_works_on_the_wall_clock_unmeasured() -> None:
    fake = FakeDgii(TrustingCheck())
    client = DgiiClient(
        FakeSigner(), DgiiOptions(endpoints=HOSTS, transport=httpx.ASGITransport(app=fake.app))
    )
    async with client.opened():
        assert isinstance(await client.result(CALL, TrackId("a")), Pending)
```


`libs/testing/tests/test_dgii.py`:

```python
import base64
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final, final, override
lazy from collections.abc import AsyncGenerator

import httpx
import pytest

from ledger_testing.dgii import FakeDgii, SignatureCheck, Status, TrustingCheck, create_app

DSIG: Final = "http://www.w3.org/2000/09/xmldsig#"
GOLDEN: Final = Path(__file__).parents[3] / "services" / "fiscal-do" / "tests" / "golden"
BASE: Final = "http://dgii.test/testecf"
CERTIFICATION: Final = "http://dgii.test/certecf"
NOW: Final = datetime(2026, 10, 12, 13, 30, tzinfo=UTC)


def signed(document: bytes) -> bytes:
    value = base64.b64encode(hashlib.sha256(document).digest()).decode("ascii")
    signature = f'<Signature xmlns="{DSIG}"><SignedInfo/><SignatureValue>{value}</SignatureValue></Signature>'
    head, _, tail = document.rpartition(b"</")
    return head + signature.encode("ascii") + b"</" + tail


def golden(name: str) -> bytes:
    return signed((GOLDEN / name).read_bytes())


@final
class Refusing(SignatureCheck):
    @override
    async def verify(self, signed: bytes) -> bool:
        del signed
        return False


@final
class Moment:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def moment() -> Moment:
    return Moment()


@pytest.fixture
def fake(moment: Moment) -> FakeDgii:
    return FakeDgii(TrustingCheck(), clock=moment)


@pytest.fixture
async def http(fake: FakeDgii) -> AsyncGenerator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fake.app), base_url="http://dgii.test"
    ) as client:
        yield client


def bearer(fake: FakeDgii) -> dict[str, str]:
    return {"Authorization": f"Bearer {fake.token_for_test()}"}


def upload(document: bytes) -> dict[str, tuple[str, bytes, str]]:
    return {"xml": ("document.xml", document, "text/xml")}


async def token(http: httpx.AsyncClient) -> httpx.Response:
    seed = await http.get(f"{BASE}/autenticacion/api/autenticacion/semilla")
    return await http.post(
        f"{BASE}/autenticacion/api/autenticacion/validarsemilla", files=upload(signed(seed.content))
    )


async def test_a_signed_seed_is_exchanged_for_a_token_valid_an_hour(http: httpx.AsyncClient) -> None:
    answer = await token(http)
    assert answer.status_code == 200
    assert answer.json()["expira"] == "2026-10-12T14:30:00Z"
    reception = await http.get(
        f"{BASE}/consultaresultado/api/consultas/estado",
        params={"trackid": "x"},
        headers={"Authorization": f"Bearer {answer.json()['token']}"},
    )
    assert reception.json()["codigo"] == Status.NOT_FOUND


async def test_a_seed_is_good_once_and_for_five_minutes(http: httpx.AsyncClient, moment: Moment) -> None:
    seed = await http.get(f"{BASE}/autenticacion/api/autenticacion/semilla")
    validate = f"{BASE}/autenticacion/api/autenticacion/validarsemilla"
    moment.now += timedelta(minutes=6)
    late = await http.post(validate, files=upload(signed(seed.content)))
    assert late.status_code == 400
    again = await http.post(validate, files=upload(signed(seed.content)))
    assert again.json()["mensaje"] == "La firma del XML no es válida"


async def test_a_seed_that_is_not_signed_or_not_a_file_is_refused(http: httpx.AsyncClient) -> None:
    seed = await http.get(f"{BASE}/autenticacion/api/autenticacion/semilla")
    validate = f"{BASE}/autenticacion/api/autenticacion/validarsemilla"
    assert (await http.post(validate, files=upload(seed.content))).status_code == 400
    assert (await http.post(validate, data={"xml": "text"})).status_code == 400
    assert (await http.post(validate, files=upload(b"<a"))).status_code == 400


async def test_a_bad_signature_refuses_the_seed_and_rejects_documents(moment: Moment) -> None:
    fake = FakeDgii(Refusing(), clock=moment)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fake.app), base_url="http://dgii.test"
    ) as http:
        assert (await token(http)).status_code == 400
        await http.post(
            f"{BASE}/recepcion/api/facturaselectronicas",
            files=upload(golden("ecf-31.xml")),
            headers=bearer(fake),
        )
    assert fake.received[0].status is Status.REJECTED
    assert fake.received[0].messages == (("2", "La firma del XML no es válida"),)


async def test_services_want_a_live_bearer_token(
    http: httpx.AsyncClient, fake: FakeDgii, moment: Moment
) -> None:
    result = f"{BASE}/consultaresultado/api/consultas/estado"
    assert (await http.get(result)).status_code == 401
    assert (await http.get(result, headers={"Authorization": "Basic x"})).status_code == 401
    headers = bearer(fake)
    moment.now += timedelta(hours=1)
    assert (await http.get(result, headers=headers)).status_code == 401
    headers = bearer(fake)
    fake.revoke_tokens()
    assert (await http.get(result, headers=headers)).status_code == 401


async def test_reception_wants_an_ecf_of_a_known_type(http: httpx.AsyncClient, fake: FakeDgii) -> None:
    reception = f"{BASE}/recepcion/api/facturaselectronicas"
    unknown = await http.post(reception, files=upload(b"<ECF><Encabezado/></ECF>"), headers=bearer(fake))
    assert unknown.json()["mensaje"] == "El tipo de comprobante no es válido"
    assert (await http.post(reception, files=upload(b"<"), headers=bearer(fake))).status_code == 400


async def test_summaries_and_annulments_want_xml(http: httpx.AsyncClient, fake: FakeDgii) -> None:
    for path in ("recepcionfc/api/recepcion/ecf", "anulacionrangos/api/operaciones/anularrango"):
        assert (
            await http.post(f"{BASE}/{path}", files=upload(b"<"), headers=bearer(fake))
        ).status_code == 400


async def test_certification_has_no_track_ids_or_annulments(http: httpx.AsyncClient, fake: FakeDgii) -> None:
    track_ids = await http.get(
        f"{CERTIFICATION}/consultatrackids/api/trackids/consulta", headers=bearer(fake)
    )
    assert track_ids.status_code == 404
    annulment = await http.post(
        f"{CERTIFICATION}/anulacionrangos/api/operaciones/anularrango",
        files=upload(golden("anecf.xml")),
        headers=bearer(fake),
    )
    assert annulment.status_code == 404


async def test_a_script_set_over_http_decides_and_delays(http: httpx.AsyncClient, fake: FakeDgii) -> None:
    script = {
        "by_encf": {"E310000000001": {"status": 2, "messages": [["613", "No existe"]], "processing": 1}},
        "outage": 1,
    }
    assert (await http.post("/_fake/script", json=script)).status_code == 204
    assert (await http.post("/_fake/script", json={"outage": "x"})).status_code == 422
    assert (await http.post("/_fake/script", json={})).status_code == 204
    reception = f"{BASE}/recepcion/api/facturaselectronicas"
    assert (
        await http.post(reception, files=upload(golden("ecf-31.xml")), headers=bearer(fake))
    ).status_code == 503
    sent = await http.post(reception, files=upload(golden("ecf-31.xml")), headers=bearer(fake))
    result = f"{BASE}/consultaresultado/api/consultas/estado"
    first = await http.get(result, params={"trackid": sent.json()["trackId"]}, headers=bearer(fake))
    assert (first.json()["codigo"], first.json()["mensajes"]) == (Status.IN_PROCESS, [])
    second = await http.get(result, params={"trackid": sent.json()["trackId"]}, headers=bearer(fake))
    assert second.json()["mensajes"] == [{"valor": "No existe", "codigo": 613}]
    assert (await http.get("/_fake/received")).json() == [
        {"trackId": sent.json()["trackId"], "kind": "ecf", "encf": "E310000000001", "status": 2}
    ]


async def test_latency_and_a_lasting_outage(http: httpx.AsyncClient, fake: FakeDgii) -> None:
    fake.script.latency = 0.01
    fake.script.outage = -1
    seed = f"{BASE}/autenticacion/api/autenticacion/semilla"
    assert [(await http.get(seed)).status_code for _ in range(3)] == [503, 503, 503]
    fake.script.outage = 0
    assert (await http.get(seed)).status_code == 200


async def test_a_rejected_annulment_says_so(http: httpx.AsyncClient, fake: FakeDgii) -> None:
    unsigned = (GOLDEN / "anecf.xml").read_bytes()
    answer = await http.post(
        f"{BASE}/anulacionrangos/api/operaciones/anularrango", files=upload(unsigned), headers=bearer(fake)
    )
    assert answer.json()["codigo"] == "2"
    assert answer.json()["mensajes"][0][:27] == "El XML no cumple con el XSD"


async def test_the_local_stack_s_fake_answers_seeds() -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://dgii.test"
    ) as http:
        assert (await http.get(f"{BASE}/autenticacion/api/autenticacion/semilla")).status_code == 200
```


- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest services/fiscal-do/tests/test_dgii_client.py libs/testing/tests/test_dgii.py -q --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'ledger_fiscal_do.adapters.dgii'` and `... 'ledger_testing.dgii'`.

- [ ] **Step 4: Implement the fake DGII**

`libs/testing/src/ledger_testing/dgii.py`:

```python
"""A fake of DGII's web services for emitters: what fiscal-do talks to in tests and in the local stack.

It answers on the paths, with the shapes, DGII's *Descripción Técnica Servicios DGII* (January 2026)
documents, for every environment segment (``testecf``, ``certecf``, ``ecf``) and both of DGII's hosts at once:

* authentication: ``GET /{env}/autenticacion/api/autenticacion/semilla`` hands out a ``SemillaModel``;
  ``POST .../validarsemilla`` takes it back signed (multipart ``xml``) and answers a token valid an hour;
* reception: ``POST /{env}/recepcion/api/facturaselectronicas`` (bearer token) answers a track id;
  ``GET /{env}/consultaresultado/api/consultas/estado?trackid=`` its result;
  ``GET /{env}/consultatrackids/api/trackids/consulta?rncemisor=&encf=`` an e-NCF's track ids
  (not in CerteCF);
* a consumer invoice's summary: ``POST /{env}/recepcionfc/api/recepcion/ecf``, answered at once;
* annulment: ``POST /{env}/anulacionrangos/api/operaciones/anularrango``, answered at once (not in CerteCF).

A fake with teeth: every document is checked against DGII's schema by ``xmlschema`` (an implementation that
shares no code with fiscal-do's libxml2 validation) and its signature by ``SignatureCheck`` (xmlsec1); a
document that fails is rejected with a message, as DGII rejects it. Otherwise it answers what ``Script``
says: accepted by default; per e-NCF accepted conditionally, rejected, or "En Proceso" for some polls; outages
(503 for the next N requests, or until set back) and latency. Its paths are written from DGII's documentation
independently of fiscal-do's client: the two meet in the tests, and the nightly run against DGII's own TesteCF
arbitrates between them. It is not DGII: it checks no RNC against a registry, no sequence against an
authorization, no amount against DGII's tolerances.
"""

import asyncio
import base64
import secrets
from abc import ABCMeta, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import IntEnum
from functools import cache
from http import HTTPStatus
from typing import Final, Protocol, final, override
lazy from collections.abc import Awaitable, Callable

import xmlschema
from lxml import etree
from pydantic import TypeAdapter, ValidationError
from starlette.applications import Starlette
from starlette.datastructures import UploadFile
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
lazy from starlette.requests import Request

from ledger_dgii.published import Format
from ledger_dgii.schemas import corrected, parser, require_readable

TOKEN_LIFETIME: Final = timedelta(hours=1)
SEED_LIFETIME: Final = timedelta(minutes=5)
WITHOUT_TRACK_IDS_OR_ANNULMENTS: Final = "certecf"
_ECF_FORMATS: Final = {
    "31": Format.ECF_31,
    "32": Format.ECF_32,
    "33": Format.ECF_33,
    "34": Format.ECF_34,
    "41": Format.ECF_41,
    "43": Format.ECF_43,
    "44": Format.ECF_44,
    "45": Format.ECF_45,
    "46": Format.ECF_46,
    "47": Format.ECF_47,
}
_UTC_FORMAT: Final = "%Y-%m-%dT%H:%M:%SZ"
_NOT_A_FILE: Final = "Tipo de archivo no válido, favor proveer un XML"
_BAD_SIGNATURE: Final = "La firma del XML no es válida"

type Answer = Callable[[Request], Awaitable[Response]]


class Status(IntEnum):
    """DGII's result codes (*Consulta de resultado e-CF*)."""

    NOT_FOUND = 0
    ACCEPTED = 1
    REJECTED = 2
    IN_PROCESS = 3
    ACCEPTED_CONDITIONALLY = 4


STATUS_WORDS: Final = {
    Status.NOT_FOUND: "No encontrado",
    Status.ACCEPTED: "Aceptado",
    Status.REJECTED: "Rechazado",
    Status.IN_PROCESS: "En Proceso",
    Status.ACCEPTED_CONDITIONALLY: "Aceptado Condicional",
}


class SignatureCheck(Protocol, metaclass=ABCMeta):
    """Whether a document's enveloped signature holds, and its certificate chains to a trusted root."""

    @abstractmethod
    async def verify(self, signed: bytes) -> bool:
        """Whether ``signed``'s signature holds."""


@final
class TrustingCheck(SignatureCheck):
    """A check that takes every signature: for tests of the protocol alone, never of signatures."""

    @override
    async def verify(self, signed: bytes) -> bool:
        del signed
        return True


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Scripted:
    """What the fake answers for one e-NCF."""

    status: Status = Status.ACCEPTED
    messages: tuple[tuple[str, str], ...] = ()
    """``(code, text)`` pairs, as DGII words its messages."""
    processing: int = 0
    """How many result queries answer "En Proceso" first."""


@final
@dataclass(slots=True)
class Script:
    """What the fake answers, as a test (or ``POST /_fake/script``) sets it."""

    by_encf: dict[str, Scripted] = field(default_factory=dict[str, Scripted])
    default: Scripted = field(default_factory=Scripted)
    outage: int = 0
    """How many of the next requests answer 503; a negative number: every one, until set back to 0."""
    latency: float = 0.0
    """Seconds every answer waits."""


@final
@dataclass(slots=True, kw_only=True)
class Received:
    """A document the fake received, and how it is answered."""

    track_id: str
    environment: str
    kind: str
    """``ecf``, ``rfce`` or ``anecf``."""
    rnc: str
    encf: str
    document: bytes = field(repr=False)
    received_at: datetime
    status: Status
    messages: tuple[tuple[str, str], ...]
    polls_left: int


@cache
def _schema(format_: Format) -> xmlschema.XMLSchema10:
    return xmlschema.XMLSchema10(corrected(format_).decode("utf-8"))


def _schema_problems(format_: Format, document: bytes) -> tuple[tuple[str, str], ...]:
    errors = list(_schema(format_).iter_errors(document.decode("utf-8")))
    return tuple(("1", f"El XML no cumple con el XSD: {error.path}") for error in errors[:5])


def _readable(document: bytes) -> bool:
    try:
        require_readable(document)
        etree.fromstring(document, parser())
    except etree.XMLSyntaxError, ValueError:
        return False
    return True


def _field(document: bytes, *paths: str) -> str:
    """The first of ``paths`` the document holds, stripped; empty when it holds none."""
    root = etree.fromstring(document, parser())
    return next((found.strip() for path in paths if (found := root.findtext(path)) is not None), "")


def _environment(request: Request) -> str:
    """The environment's segment of the request's path: ``testecf``, ``certecf`` or ``ecf``."""
    return request.url.path.split("/")[1]


@final
@dataclass(frozen=True, slots=True)
class _ScriptBody:
    """What ``POST /_fake/script`` takes: outcomes by e-NCF, and an outage."""

    by_encf: dict[str, Scripted] = field(default_factory=dict[str, Scripted])
    outage: int | None = None


_SCRIPT_BODY: Final = TypeAdapter(_ScriptBody)


def _bad_request(message: str) -> Response:
    return JSONResponse(
        {"trackId": None, "error": "400", "mensaje": message}, status_code=HTTPStatus.BAD_REQUEST
    )


@final
class FakeDgii:
    """DGII's services for emitters, scripted (see the module); ``app`` is the ASGI application."""

    def __init__(self, check: SignatureCheck, *, clock: Callable[[], datetime] | None = None) -> None:
        self.check: SignatureCheck = check
        self.script: Script = Script()
        self.received: list[Received] = []
        self._clock: Callable[[], datetime] = clock if clock is not None else (lambda: datetime.now(UTC))
        self._seeds: dict[str, datetime] = {}
        self._tokens: dict[str, datetime] = {}
        environment = "/{environment:str}"
        self.app: Starlette = Starlette(
            routes=[
                Route(f"{environment}/autenticacion/api/autenticacion/semilla", self._open(self._seed)),
                Route(
                    f"{environment}/autenticacion/api/autenticacion/validarsemilla",
                    self._open(self._validate),
                    methods=["POST"],
                ),
                Route(
                    f"{environment}/recepcion/api/facturaselectronicas",
                    self._closed(self._reception),
                    methods=["POST"],
                ),
                Route(f"{environment}/consultaresultado/api/consultas/estado", self._closed(self._result)),
                Route(f"{environment}/consultatrackids/api/trackids/consulta", self._closed(self._track_ids)),
                Route(
                    f"{environment}/recepcionfc/api/recepcion/ecf",
                    self._closed(self._summary),
                    methods=["POST"],
                ),
                Route(
                    f"{environment}/anulacionrangos/api/operaciones/anularrango",
                    self._closed(self._annulment),
                    methods=["POST"],
                ),
                Route("/_fake/script", self._set_script, methods=["POST"]),
                Route("/_fake/received", self._list_received),
            ]
        )

    def token_for_test(self) -> str:
        """A valid token, as if the seed had been validated: for tests of a single service."""
        token = secrets.token_urlsafe(32)
        self._tokens[token] = self._clock() + TOKEN_LIFETIME
        return token

    def revoke_tokens(self) -> None:
        """Forget every token handed out, as DGII does when it rotates its keys."""
        self._tokens.clear()

    def _open(self, answer: Answer) -> Answer:
        async def scripted(request: Request) -> Response:
            if self.script.latency:
                await asyncio.sleep(self.script.latency)
            if self.script.outage:
                self.script.outage -= 1 if self.script.outage > 0 else 0
                return Response(status_code=HTTPStatus.SERVICE_UNAVAILABLE)
            return await answer(request)

        return scripted

    def _closed(self, answer: Answer) -> Answer:
        async def authorized(request: Request) -> Response:
            scheme, _, token = request.headers.get("authorization", "").partition(" ")
            expires = self._tokens.get(token) if scheme.lower() == "bearer" else None
            if expires is None or expires <= self._clock():
                return Response(status_code=HTTPStatus.UNAUTHORIZED)
            return await answer(request)

        return self._open(authorized)

    async def _upload(self, request: Request) -> bytes | None:
        async with request.form() as form:
            upload = form.get("xml")
            return await upload.read() if isinstance(upload, UploadFile) else None

    async def _seed(self, request: Request) -> Response:
        del request
        value = base64.b64encode(secrets.token_bytes(48)).decode("ascii")
        now = self._clock()
        self._seeds[value] = now + SEED_LIFETIME
        body = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<SemillaModel xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
            ' xmlns:xsd="http://www.w3.org/2001/XMLSchema">'
            f"<valor>{value}</valor><fecha>{now.isoformat()}</fecha></SemillaModel>"
        )
        return Response(body, media_type="text/xml")

    async def _validate(self, request: Request) -> Response:
        signed = await self._upload(request)
        if signed is None or not _readable(signed) or _schema_problems(Format.SEED, signed):
            return _bad_request(_NOT_A_FILE)
        expires = self._seeds.pop(_field(signed, "valor"), None)
        if expires is None or expires < self._clock() or not await self.check.verify(signed):
            return _bad_request(_BAD_SIGNATURE)
        token, now = self.token_for_test(), self._clock()
        expiry = (now + TOKEN_LIFETIME).strftime(_UTC_FORMAT)
        return JSONResponse({"token": token, "expira": expiry, "expedido": now.strftime(_UTC_FORMAT)})

    async def _record(self, request: Request, kind: str, document: bytes, format_: Format) -> Received:
        problems = _schema_problems(format_, document)
        if not problems and not await self.check.verify(document):
            problems = (("2", _BAD_SIGNATURE),)
        encf = _field(document, "Encabezado/IdDoc/eNCF")
        scripted = self.script.by_encf.get(encf, self.script.default)
        received = Received(
            track_id=secrets.token_hex(16),
            environment=_environment(request),
            kind=kind,
            rnc=_field(document, "Encabezado/Emisor/RNCEmisor", "Encabezado/RncEmisor"),
            encf=encf,
            document=document,
            received_at=self._clock(),
            status=Status.REJECTED if problems else scripted.status,
            messages=problems or scripted.messages,
            polls_left=0 if problems else scripted.processing,
        )
        self.received.append(received)
        return received

    async def _reception(self, request: Request) -> Response:
        document = await self._upload(request)
        if document is None or not _readable(document):
            return _bad_request(_NOT_A_FILE)
        format_ = _ECF_FORMATS.get(_field(document, "Encabezado/IdDoc/TipoeCF"))
        if format_ is None:
            return _bad_request("El tipo de comprobante no es válido")
        received = await self._record(request, "ecf", document, format_)
        return JSONResponse({"trackId": received.track_id, "error": None, "mensaje": None})

    async def _result(self, request: Request) -> Response:
        track_id = request.query_params.get("trackid", "")
        found = next((each for each in self.received if each.track_id == track_id), None)
        if found is None:
            return JSONResponse(
                {"trackId": track_id, "codigo": 0, "estado": STATUS_WORDS[Status.NOT_FOUND], "mensajes": []}
            )
        status = found.status
        if found.polls_left:
            found.polls_left -= 1
            status = Status.IN_PROCESS
        messages = (
            []
            if status is Status.IN_PROCESS
            else [{"valor": text, "codigo": int(code)} for code, text in found.messages]
        )
        body = {
            "trackId": track_id,
            "codigo": int(status),
            "estado": STATUS_WORDS[status],
            "rnc": found.rnc,
            "eNCF": found.encf,
            "secuenciaUtilizada": True,
            "fechaRecepcion": found.received_at.isoformat(),
            "mensajes": messages,
        }
        return JSONResponse(body)

    async def _track_ids(self, request: Request) -> Response:
        if _environment(request) == WITHOUT_TRACK_IDS_OR_ANNULMENTS:
            return Response(status_code=HTTPStatus.NOT_FOUND)
        rnc, encf = request.query_params.get("rncemisor", ""), request.query_params.get("encf", "")
        found = [each for each in self.received if (each.kind, each.rnc, each.encf) == ("ecf", rnc, encf)]
        return JSONResponse(
            [
                {
                    "trackId": each.track_id,
                    "estado": STATUS_WORDS[each.status],
                    "fechaRecepcion": each.received_at.isoformat(),
                }
                for each in found
            ]
        )

    async def _summary(self, request: Request) -> Response:
        document = await self._upload(request)
        if document is None or not _readable(document):
            return _bad_request(_NOT_A_FILE)
        received = await self._record(request, "rfce", document, Format.RFCE)
        messages = [{"codigo": code, "valor": text} for code, text in received.messages]
        body = {
            "codigo": int(received.status),
            "estado": STATUS_WORDS[received.status],
            "mensajes": messages,
            "encf": received.encf,
            "secuenciaUtilizada": True,
        }
        return JSONResponse(body)

    async def _annulment(self, request: Request) -> Response:
        if _environment(request) == WITHOUT_TRACK_IDS_OR_ANNULMENTS:
            return Response(status_code=HTTPStatus.NOT_FOUND)
        document = await self._upload(request)
        if document is None or not _readable(document):
            return _bad_request(_NOT_A_FILE)
        received = await self._record(request, "anecf", document, Format.ANECF)
        refused = received.status is Status.REJECTED
        texts = [text for _, text in received.messages] or ["Las secuencias fueron anuladas correctamente"]
        body = {
            "rnc": received.rnc,
            "codigo": "2" if refused else "1",
            "nombre": STATUS_WORDS[received.status],
            "mensajes": texts,
        }
        return JSONResponse(body)

    async def _set_script(self, request: Request) -> Response:
        try:
            body = _SCRIPT_BODY.validate_json(await request.body())
        except ValidationError:
            return Response(status_code=HTTPStatus.UNPROCESSABLE_ENTITY)
        self.script.by_encf.update(body.by_encf)
        if body.outage is not None:
            self.script.outage = body.outage
        return Response(status_code=HTTPStatus.NO_CONTENT)

    async def _list_received(self, request: Request) -> Response:
        del request
        return JSONResponse(
            [
                {"trackId": each.track_id, "kind": each.kind, "encf": each.encf, "status": int(each.status)}
                for each in self.received
            ]
        )


def create_app() -> Starlette:
    """Granian's factory for the local stack's fake DGII: every signature trusted, every schema checked.

    The local stack has no xmlsec1 beside it: a document vault signed is taken as signed, and still has to be
    what DGII's schema allows.
    """
    return FakeDgii(TrustingCheck()).app
```


- [ ] **Step 5: Implement DGII's client**

`services/fiscal-do/src/ledger_fiscal_do/adapters/dgii/__init__.py`:

```python
"""DGII's web services for an emitter, over HTTPS: authentication, reception, results, annulments."""
```


`services/fiscal-do/src/ledger_fiscal_do/adapters/dgii/endpoints.py`:

```python
"""Where DGII answers each service, per environment (*Descripción Técnica Servicios DGII*, January 2026).

Every URL is a host, the environment's segment (``testecf``, ``certecf``, ``ecf``), the service and its
resource: ``https://ecf.dgii.gov.do/testecf/recepcion/api/facturaselectronicas``. Consumer-invoice summaries
go to a host of their own, ``fc.dgii.gov.do``. The hosts are configuration (the fake DGII stands in for both
in tests and locally); the paths are DGII's and live here only. DGII's services ignore case; the
documentation's lower-case spelling is used. CerteCF publishes neither the track-id query nor the annulment
service: asking for them there is ``ServiceUnavailableInEnvironmentError``.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar, Final, final

from ledger_fiscal_do.domain.vocabulary import OFFICIAL_ECF, OFFICIAL_FC, DgiiEnvironment
from ledger_kernel.errors import ConflictError


class Service(StrEnum):
    """DGII's services for an emitter, as metrics, spans and errors name them."""

    SEED = "seed"
    VALIDATE_SEED = "validate_seed"
    RECEPTION = "reception"
    RESULT = "result"
    TRACK_IDS = "track_ids"
    SUMMARY = "summary"
    ANNULMENT = "annulment"


PATHS: Final = {
    Service.SEED: "autenticacion/api/autenticacion/semilla",
    Service.VALIDATE_SEED: "autenticacion/api/autenticacion/validarsemilla",
    Service.RECEPTION: "recepcion/api/facturaselectronicas",
    Service.RESULT: "consultaresultado/api/consultas/estado",
    Service.TRACK_IDS: "consultatrackids/api/trackids/consulta",
    Service.SUMMARY: "recepcionfc/api/recepcion/ecf",
    Service.ANNULMENT: "anulacionrangos/api/operaciones/anularrango",
}
"""Each service's path after the environment's segment."""
_NOT_IN_CERTIFICATION: Final = frozenset({Service.TRACK_IDS, Service.ANNULMENT})


class ServiceUnavailableInEnvironmentError(ConflictError):
    """DGII publishes no such service in the environment."""

    code: ClassVar[str] = "dgii_service_not_in_environment"

    def __init__(self, service: Service, environment: DgiiEnvironment) -> None:
        super().__init__(f"DGII has no {service.value} service in {environment.value}")


def offered(service: Service, environment: DgiiEnvironment) -> bool:
    """Whether DGII answers ``service`` in ``environment``."""
    return environment is not DgiiEnvironment.CERTIFICATION or service not in _NOT_IN_CERTIFICATION


@final
@dataclass(frozen=True, slots=True)
class DgiiEndpoints:
    """DGII's hosts, and the URL of each service in an environment."""

    ecf: str = OFFICIAL_ECF
    fc: str = OFFICIAL_FC

    def url(self, service: Service, environment: DgiiEnvironment) -> str:
        """Where ``service`` answers in ``environment``; ``ServiceUnavailableInEnvironmentError`` if none."""
        if not offered(service, environment):
            raise ServiceUnavailableInEnvironmentError(service, environment)
        host = self.fc if service is Service.SUMMARY else self.ecf
        return f"{host}/{environment.value}/{PATHS[service]}"
```


`services/fiscal-do/src/ledger_fiscal_do/adapters/dgii/breaker.py`:

```python
"""A circuit breaker per DGII environment: an outage stops the hammering and shows as a state (§6, §11).

Closed, every call goes through and failures are counted; ``failures`` failures in a row open it. Open, no
call goes through (``DgiiCircuitOpenError`` at once, an ``UNAVAILABLE``) until its cooldown has passed; then
one call is let through, half-open: its success closes the breaker, its failure opens it again for twice as
long, up to ``max_cooldown``. A failure is DGII failing (no connection, a timeout, a 5xx or 429), never
DGII's refusal of a document. Its state is a gauge (``ledger_fiscal_do_dgii_circuit_open``), for the alert
"circuit open for more than 10 minutes" (§11).
"""

from enum import StrEnum
from time import monotonic
from typing import ClassVar, Final, final

from ledger_fiscal_do.application.ports import DgiiUnavailableError, Failure
lazy from ledger_kernel.clock import MonotonicClock

DEFAULT_FAILURES: Final = 5
DEFAULT_COOLDOWN: Final = 30.0
MAX_COOLDOWN: Final = 600.0


class BreakerState(StrEnum):
    """Where a circuit breaker stands."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class DgiiCircuitOpenError(DgiiUnavailableError):
    """DGII failed too often lately: no call is made until the breaker lets one through."""

    code: ClassVar[str] = "dgii_circuit_open"

    def __init__(self, environment: str) -> None:
        super().__init__(Failure.CIRCUIT_OPEN, f"DGII's {environment}")


@final
class CircuitBreaker:
    """One environment's breaker (see the module). Not thread-safe: it lives on one event loop."""

    __slots__ = (
        "_base",
        "_ceiling",
        "_clock",
        "_cooldown",
        "_failures",
        "_limit",
        "_opened_at",
        "_probing",
        "_state",
    )

    def __init__(
        self,
        *,
        failures: int = DEFAULT_FAILURES,
        cooldown: float = DEFAULT_COOLDOWN,
        max_cooldown: float = MAX_COOLDOWN,
        clock: MonotonicClock = monotonic,
    ) -> None:
        self._limit = failures
        self._base = self._cooldown = cooldown
        self._ceiling = max_cooldown
        self._clock = clock
        self._failures = 0
        self._state = BreakerState.CLOSED
        self._opened_at = 0.0
        self._probing = False

    @property
    def state(self) -> BreakerState:
        """Where it stands now: an open breaker whose cooldown has passed is half-open."""
        if self._state is BreakerState.OPEN and self._clock() - self._opened_at >= self._cooldown:
            return BreakerState.HALF_OPEN
        return self._state

    def admit(self, environment: str) -> None:
        """Return when a call may go through; ``DgiiCircuitOpenError`` otherwise."""
        state = self.state
        if state is BreakerState.CLOSED:
            return
        if state is BreakerState.HALF_OPEN and not self._probing:
            self._probing = True
            return
        raise DgiiCircuitOpenError(environment)

    def succeeded(self) -> None:
        """The call went through: the breaker closes and its count and cooldown start over."""
        self._failures = 0
        self._probing = False
        self._cooldown = self._base
        self._state = BreakerState.CLOSED

    def failed(self) -> None:
        """DGII failed the call: counted, and the breaker opens at the limit; a probe's failure reopens it."""
        if self._probing:
            self._probing = False
            self._cooldown = min(self._cooldown * 2, self._ceiling)
            self._open()
            return
        self._failures += 1
        if self._failures >= self._limit:
            self._open()

    def _open(self) -> None:
        self._state = BreakerState.OPEN
        self._opened_at = self._clock()
        self._failures = 0
```


`services/fiscal-do/src/ledger_fiscal_do/adapters/dgii/wire.py`:

```python
"""DGII's answers as they arrive (JSON), read strictly enough to trust, loosely enough to survive additions.

Each model takes the fields fiscal-do reads by DGII's names and ignores the rest (DGII adds fields without
notice: ``secuenciaUtilizada`` arrived in 2022). A field of the wrong type is an answer fiscal-do cannot read:
``DgiiAnswerError``, retried later like an outage, never a guess.
"""

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field


class _Answer(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", frozen=True, populate_by_name=True)


class TokenAnswer(_Answer):
    """``validarsemilla``: ``{"token", "expira", "expedido"}`` (UTC, ``yyyy-MM-ddTHH:mm:ssZ``)."""

    token: str = Field(min_length=1, max_length=4096)
    expira: str = Field(max_length=64)


class Message(_Answer):
    """One message of DGII's: its code (a number or a word) and its text."""

    codigo: int | str = ""
    valor: str = Field(default="", max_length=2000)


class ReceptionAnswer(_Answer):
    """``facturaselectronicas``: a track id, or an error and its message."""

    track_id: str | None = Field(default=None, alias="trackId", max_length=128)
    error: str | None = Field(default=None, max_length=256)
    mensaje: str | None = Field(default=None, max_length=2000)


class ResultAnswer(_Answer):
    """``consultas/estado``: the status code and DGII's messages."""

    codigo: int
    estado: str = Field(default="", max_length=64)
    mensajes: list[Message] = Field(default_factory=list[Message], max_length=200)


class SummaryAnswer(_Answer):
    """``recepcionfc``: the status code and DGII's messages, at once."""

    codigo: int
    estado: str = Field(default="", max_length=64)
    mensajes: list[Message] = Field(default_factory=list[Message], max_length=200)


class TrackIdAnswer(_Answer):
    """One entry of ``trackids/consulta``."""

    track_id: str = Field(alias="trackId", min_length=1, max_length=128)
    estado: str = Field(default="", max_length=64)
    fecha_recepcion: str = Field(default="", alias="fechaRecepcion", max_length=64)


class AnnulmentAnswer(_Answer):
    """``anularrango``: a code, a name for it and messages."""

    codigo: str = Field(max_length=16)
    nombre: str = Field(default="", max_length=256)
    mensajes: list[str] = Field(default_factory=list[str], max_length=200)
```


`services/fiscal-do/src/ledger_fiscal_do/adapters/dgii/client.py`:

```python
"""DGII's web services for an emitter, over HTTPS (httpx): the ``Dgii`` port.

* **Authentication.** A company's token comes from DGII's seed, signed by vault (``Signer.sign_seed``) and
  exchanged at ``validarsemilla``. It is cached per environment and company until five minutes before it
  expires (DGII gives an hour), fetched once at a time per company; a 401 drops it and the call is made once
  more with a new one. Tokens live in memory only: never stored, logged or traced.
* **Retries.** Only what cannot change DGII's state is retried here, at once (twice, 0.2 s and 0.5 s apart):
  the seed, its validation, results and track ids. A document sent is never retried here: when the
  connection breaks after its request may have left, the caller learns ``DgiiOutcomeUnknownError`` and asks
  DGII for the e-NCF's track ids before sending it again (``ledger_fiscal_do.application.emission``).
* **Failures.** No connection, a broken one, a timeout, a 5xx or 429, an answer that cannot be read or is
  larger than 1 MiB: ``DgiiUnavailableError``, counted by the environment's circuit breaker. DGII's refusal of
  a document is an answer, never a failure.
* **Observability.** Every call is a client span (environment, service, status; an error's type only) and
  is measured: ``ledger_fiscal_do_dgii_calls_total{environment,service,outcome}``,
  ``ledger_fiscal_do_dgii_call_seconds{environment,service}``,
  ``ledger_fiscal_do_dgii_circuit_open{environment}``. No body, document or token is ever logged.

DGII's certificate is checked against the system's authorities; redirects are not followed; the
environment's proxies and ``.netrc`` are ignored (``trust_env=False``).
"""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from time import monotonic
from typing import ClassVar, Final, final, override
lazy from collections.abc import AsyncGenerator, Callable, Mapping

import httpx
from opentelemetry import trace
from opentelemetry.trace import SpanKind, Status, StatusCode
from prometheus_client import Counter, Gauge, Histogram
from pydantic import TypeAdapter, ValidationError
lazy from opentelemetry.trace import Tracer
lazy from prometheus_client import CollectorRegistry

from ledger_fiscal_do.adapters.dgii.breaker import BreakerState, CircuitBreaker
from ledger_fiscal_do.adapters.dgii.endpoints import DgiiEndpoints, Service, offered
from ledger_fiscal_do.adapters.dgii.wire import (
    AnnulmentAnswer,
    ReceptionAnswer,
    ResultAnswer,
    SummaryAnswer,
    TokenAnswer,
    TrackIdAnswer,
)
from ledger_fiscal_do.application.ports import (
    Decision,
    Dgii,
    DgiiOutcomeUnknownError,
    DgiiUnavailableError,
    Failure,
    Pending,
    Received,
    Refused,
)
from ledger_fiscal_do.domain.ids import TrackId
from ledger_fiscal_do.domain.verdict import DgiiMessage, Outcome, Verdict
from ledger_kernel.errors import error_type
lazy from ledger_fiscal_do.adapters.dgii.wire import Message
lazy from ledger_fiscal_do.application.ports import DgiiCall, Reception, Result, Signer
lazy from ledger_fiscal_do.domain.encf import Encf
lazy from ledger_fiscal_do.domain.vocabulary import DgiiEnvironment

MAX_ANSWER: Final = 1024 * 1024
TOKEN_MARGIN: Final = timedelta(minutes=5)
TOKEN_CEILING: Final = timedelta(hours=1)
RETRY_DELAYS: Final = (0.2, 0.5)
_EXPIRY: Final = "%Y-%m-%dT%H:%M:%SZ"
_VERDICTS: Final[Mapping[int, Verdict]] = {
    1: Verdict.ACCEPTED,
    2: Verdict.REJECTED,
    4: Verdict.ACCEPTED_CONDITIONALLY,
}
_PENDING: Final = frozenset({0, 3})
"""Not found yet (a result may lag its reception), and "En Proceso"."""
_TRACK_IDS: Final = TypeAdapter(list[TrackIdAnswer])
_SERVER_ERROR: Final = 500
_TOO_MANY: Final = 429
_UNAUTHORIZED: Final = 401
_ANNULLED: Final = "1"
OFFICIAL: Final = DgiiEndpoints()
"""DGII's own hosts."""


class DgiiAnswerError(DgiiUnavailableError):
    """DGII answered something fiscal-do cannot read, or too much of it: treated as an outage."""

    code: ClassVar[str] = "dgii_answer_unreadable"

    def __init__(self, service: Service) -> None:
        super().__init__(Failure.UNREADABLE, f"DGII's {service.value}")


@final
@dataclass(frozen=True, slots=True)
class DgiiTimeouts:
    """How long a call to DGII may take: to connect, and in all (each attempt)."""

    connect: float = 5.0
    total: float = 20.0


def _now() -> datetime:
    return datetime.now(UTC)


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class DgiiOptions:
    """Where DGII is and how it is called: the hosts, timeouts, breakers, clock and (in tests) transport."""

    endpoints: DgiiEndpoints = OFFICIAL
    timeouts: DgiiTimeouts = DgiiTimeouts()
    breaker: Callable[[], CircuitBreaker] = CircuitBreaker
    clock: Callable[[], datetime] = _now
    transport: httpx.AsyncBaseTransport | None = None


@final
@dataclass(slots=True)
class _Token:
    value: str
    good_until: datetime


@final
class DgiiMetrics:
    """The DGII client's metrics, in the service's registry."""

    __slots__ = ("calls", "circuit", "seconds")

    def __init__(self, registry: CollectorRegistry) -> None:
        self.calls: Counter = Counter(
            "ledger_fiscal_do_dgii_calls",
            "Calls to DGII by environment, service and outcome (ok, failed, unknown, open).",
            ("environment", "service", "outcome"),
            registry=registry,
        )
        self.seconds: Histogram = Histogram(
            "ledger_fiscal_do_dgii_call_seconds",
            "How long DGII took to answer, by environment and service.",
            ("environment", "service"),
            registry=registry,
        )
        self.circuit: Gauge = Gauge(
            "ledger_fiscal_do_dgii_circuit_open",
            "1 while the circuit breaker of an environment is not closed, else 0.",
            ("environment",),
            registry=registry,
        )


def _messages(found: list[Message]) -> tuple[DgiiMessage, ...]:
    return tuple(DgiiMessage(str(message.codigo), message.valor) for message in found)


def _read[T](model: TypeAdapter[T], response: httpx.Response, service: Service) -> T:
    if len(response.content) > MAX_ANSWER:
        raise DgiiAnswerError(service)
    try:
        return model.validate_json(response.content)
    except ValidationError as error:
        raise DgiiAnswerError(service) from error


async def _sent(
    http: httpx.AsyncClient, request: httpx.Request, service: Service, *, idempotent: bool
) -> httpx.Response | DgiiUnavailableError:
    """DGII's answer to ``request``, or the failure the attempt was (returned, for the caller to decide)."""
    try:
        response = await http.send(request)
    except httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout:
        return DgiiUnavailableError(Failure.UNREACHABLE, f"DGII's {service.value}")
    except httpx.TransportError:
        if idempotent:
            return DgiiUnavailableError(Failure.BROKEN, f"DGII's {service.value}")
        return DgiiOutcomeUnknownError(f"DGII's {service.value}")
    if response.status_code >= _SERVER_ERROR or response.status_code == _TOO_MANY:
        return DgiiUnavailableError(Failure.SERVER_ERROR, f"DGII's {service.value}")
    return response


_DEFAULTS: Final = DgiiOptions()


@final
class DgiiClient(Dgii):
    """``Dgii`` over HTTPS (see the module); ``opened`` is the service's resource."""

    def __init__(
        self,
        signer: Signer,
        options: DgiiOptions = _DEFAULTS,
        *,
        metrics: DgiiMetrics | None = None,
        tracer: Tracer | None = None,
    ) -> None:
        self._signer = signer
        self._endpoints = options.endpoints
        self._timeouts = options.timeouts
        self._new_breaker = options.breaker
        self._breakers: dict[DgiiEnvironment, CircuitBreaker] = {}
        self._metrics = metrics
        self._tracer: Tracer = tracer if tracer is not None else trace.get_tracer("ledger_fiscal_do.dgii")
        self._clock = options.clock
        self._transport = options.transport
        self._tokens: dict[tuple[DgiiEnvironment, str], _Token] = {}
        self._locks: dict[tuple[DgiiEnvironment, str], asyncio.Lock] = {}
        self._http: httpx.AsyncClient | None = None

    @asynccontextmanager
    async def opened(self) -> AsyncGenerator[DgiiClient]:
        """This client with its HTTP connections open, for the length of the block."""
        timeout = httpx.Timeout(self._timeouts.total, connect=self._timeouts.connect)
        async with httpx.AsyncClient(
            timeout=timeout, follow_redirects=False, trust_env=False, transport=self._transport
        ) as http:
            self._http = http
            try:
                yield self
            finally:
                self._http = None
                self._tokens.clear()

    def breaker(self, environment: DgiiEnvironment) -> CircuitBreaker:
        """The circuit breaker of ``environment``."""
        found = self._breakers.get(environment)
        if found is None:
            found = self._breakers[environment] = self._new_breaker()
        return found

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            raise DgiiUnavailableError(Failure.NOT_OPEN)
        return self._http

    def _measured(self, environment: DgiiEnvironment, service: Service, outcome: str, started: float) -> None:
        if self._metrics is None:
            return
        self._metrics.calls.labels(environment.value, service.value, outcome).inc()
        self._metrics.seconds.labels(environment.value, service.value).observe(monotonic() - started)
        not_closed = self.breaker(environment).state is not BreakerState.CLOSED
        self._metrics.circuit.labels(environment.value).set(1 if not_closed else 0)

    async def _call(
        self, environment: DgiiEnvironment, service: Service, request: httpx.Request, *, idempotent: bool
    ) -> httpx.Response:
        """``request``, through the environment's breaker, retried when ``idempotent`` (see the module)."""
        http = self._client()
        breaker = self.breaker(environment)
        delays = iter(RETRY_DELAYS if idempotent else ())
        while True:
            started = monotonic()
            try:
                breaker.admit(environment.value)
            except DgiiUnavailableError:
                self._measured(environment, service, "open", started)
                raise
            outcome = await self._attempt(http, request, environment, service, idempotent=idempotent)
            if isinstance(outcome, httpx.Response):
                breaker.succeeded()
                self._measured(environment, service, "ok", started)
                return outcome
            breaker.failed()
            unknown = isinstance(outcome, DgiiOutcomeUnknownError)
            self._measured(environment, service, "unknown" if unknown else "failed", started)
            delay = next(delays, None)
            if delay is None:
                raise outcome
            await asyncio.sleep(delay)

    async def _attempt(
        self,
        http: httpx.AsyncClient,
        request: httpx.Request,
        environment: DgiiEnvironment,
        service: Service,
        *,
        idempotent: bool,
    ) -> httpx.Response | DgiiUnavailableError:
        with self._tracer.start_as_current_span(
            f"DGII {service.value}",
            kind=SpanKind.CLIENT,
            attributes={"dgii.environment": environment.value, "dgii.service": service.value},
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            outcome = await _sent(http, request, service, idempotent=idempotent)
            if isinstance(outcome, httpx.Response):
                span.set_attribute("http.response.status_code", outcome.status_code)
            else:
                span.set_status(Status(StatusCode.ERROR))
                span.add_event("exception", {"exception.type": error_type(outcome)})
            return outcome

    def _get(self, url: str, token: str | None, params: Mapping[str, str] | None = None) -> httpx.Request:
        headers = {"Accept": "application/json"} | (
            {} if token is None else {"Authorization": f"Bearer {token}"}
        )
        return self._client().build_request("GET", url, headers=headers, params=params)

    def _post(self, url: str, token: str | None, document: tuple[str, bytes]) -> httpx.Request:
        headers = {"Accept": "application/json"} | (
            {} if token is None else {"Authorization": f"Bearer {token}"}
        )
        files = {"xml": (document[0], document[1], "text/xml")}
        return self._client().build_request("POST", url, headers=headers, files=files)

    async def _token(self, call: DgiiCall) -> str:
        key = (call.environment, str(call.tenant.company))
        async with self._locks.setdefault(key, asyncio.Lock()):
            cached = self._tokens.get(key)
            if cached is not None and cached.good_until > self._clock():
                return cached.value
            seed_url = self._endpoints.url(Service.SEED, call.environment)
            seed = await self._call(
                call.environment, Service.SEED, self._get(seed_url, None), idempotent=True
            )
            signed = await self._signer.sign_seed(call.tenant, seed.content)
            validate = self._post(
                self._endpoints.url(Service.VALIDATE_SEED, call.environment), None, ("semilla.xml", signed)
            )
            answer = await self._call(call.environment, Service.VALIDATE_SEED, validate, idempotent=True)
            token = _read(TypeAdapter(TokenAnswer), answer, Service.VALIDATE_SEED)
            try:
                expires = datetime.strptime(token.expira, _EXPIRY).replace(tzinfo=UTC)
            except ValueError as error:
                raise DgiiAnswerError(Service.VALIDATE_SEED) from error
            good_until = min(expires, self._clock() + TOKEN_CEILING) - TOKEN_MARGIN
            self._tokens[key] = _Token(token.token, good_until)
            return token.token

    async def _authenticated(
        self, call: DgiiCall, service: Service, build: Callable[[str], httpx.Request], *, idempotent: bool
    ) -> httpx.Response:
        """A call with the company's token; once more with a new one when DGII says the token is not good."""
        response = await self._call(
            call.environment, service, build(await self._token(call)), idempotent=idempotent
        )
        if response.status_code != _UNAUTHORIZED:
            return response
        self._tokens.pop((call.environment, str(call.tenant.company)), None)
        return await self._call(
            call.environment, service, build(await self._token(call)), idempotent=idempotent
        )

    def _outcome(self, verdict: Verdict, messages: tuple[DgiiMessage, ...]) -> Outcome:
        return Outcome(verdict=verdict, messages=messages, decided_at=self._clock())

    @override
    async def submit_ecf(self, call: DgiiCall, encf: Encf, signed: bytes) -> Reception:
        url, name = self._endpoints.url(Service.RECEPTION, call.environment), f"{call.issuer}{encf}.xml"
        response = await self._authenticated(
            call, Service.RECEPTION, lambda token: self._post(url, token, (name, signed)), idempotent=False
        )
        answer = _read(TypeAdapter(ReceptionAnswer), response, Service.RECEPTION)
        if response.is_success and answer.track_id:
            return Received(TrackId(answer.track_id), response.content)
        refusal = DgiiMessage(answer.error or str(response.status_code), answer.mensaje or "")
        return Refused((refusal,), response.content)

    @override
    async def submit_summary(self, call: DgiiCall, encf: Encf, signed: bytes) -> Decision:
        url, name = self._endpoints.url(Service.SUMMARY, call.environment), f"{call.issuer}{encf}.xml"
        response = await self._authenticated(
            call, Service.SUMMARY, lambda token: self._post(url, token, (name, signed)), idempotent=False
        )
        answer = _read(TypeAdapter(SummaryAnswer), response, Service.SUMMARY)
        verdict = _VERDICTS.get(answer.codigo)
        if verdict is None:
            raise DgiiAnswerError(Service.SUMMARY)
        return Decision(self._outcome(verdict, _messages(answer.mensajes)), response.content)

    @override
    async def result(self, call: DgiiCall, track_id: TrackId) -> Result:
        url, params = self._endpoints.url(Service.RESULT, call.environment), {"trackid": track_id}
        response = await self._authenticated(
            call, Service.RESULT, lambda token: self._get(url, token, params), idempotent=True
        )
        answer = _read(TypeAdapter(ResultAnswer), response, Service.RESULT)
        if answer.codigo in _PENDING:
            return Pending()
        verdict = _VERDICTS.get(answer.codigo)
        if verdict is None:
            raise DgiiAnswerError(Service.RESULT)
        return Decision(self._outcome(verdict, _messages(answer.mensajes)), response.content)

    @override
    async def track_ids(self, call: DgiiCall, encf: Encf) -> tuple[TrackId, ...]:
        if not offered(Service.TRACK_IDS, call.environment):
            return ()
        url = self._endpoints.url(Service.TRACK_IDS, call.environment)
        params = {"rncemisor": str(call.issuer), "encf": str(encf)}
        response = await self._authenticated(
            call, Service.TRACK_IDS, lambda token: self._get(url, token, params), idempotent=True
        )
        entries = _read(_TRACK_IDS, response, Service.TRACK_IDS)
        found = sorted(entries, key=lambda entry: entry.fecha_recepcion)
        return tuple(TrackId(entry.track_id) for entry in found)

    @override
    async def annul(self, call: DgiiCall, signed: bytes) -> Decision:
        url, name = self._endpoints.url(Service.ANNULMENT, call.environment), f"{call.issuer}ANECF.xml"
        response = await self._authenticated(
            call, Service.ANNULMENT, lambda token: self._post(url, token, (name, signed)), idempotent=False
        )
        answer = _read(TypeAdapter(AnnulmentAnswer), response, Service.ANNULMENT)
        verdict = Verdict.ACCEPTED if answer.codigo == _ANNULLED else Verdict.REJECTED
        messages = tuple(DgiiMessage(answer.codigo, text) for text in answer.mensajes)
        return Decision(self._outcome(verdict, messages), response.content)
```


- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest services/fiscal-do/tests/test_dgii_client.py libs/testing/tests/test_dgii.py -q --no-cov`
Expected: PASS (about 10 s: three tests wait out the client's real retry delays, 0.7 s each; every e-CF type and the summary pass the fake's xmlschema check).

- [ ] **Step 7: Gate**

Run: `uv run poe fmt && uv run poe check`
Expected: every step passes, coverage 100 % (`ledger_testing.dgii` included).

- [ ] **Step 8: Commit**

```bash
git add uv.lock services/fiscal-do libs/testing
git commit -m "feat(fiscal-do): DGII's client with token cache, idempotent retries and a breaker; a fake DGII

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: The WORM archive: S3 with object lock in compliance mode

**Files:**
- Create: `services/fiscal-do/src/ledger_fiscal_do/adapters/archive.py`
- Modify: `libs/testing/src/ledger_testing/containers.py`, `services/fiscal-do/pyproject.toml`, `pyproject.toml` (root)
- Test: `services/fiscal-do/tests/test_archive.py`

**Interfaces:**
- Consumes: `Archive`, `Archived` (Task 4); `LOCALSTACK_IMAGE`, `LOCALSTACK_PORT`, `localstack_address` (`ledger_testing.containers`, Milestone 3 Task 2).
- Produces: `adapters.archive`: `COMPLIANCE`, `ArchiveUnavailableError` (`archive_unavailable`), `ArchiveConflictError` (`archive_conflict`), `ArchivedObjectMissingError` (`archived_object_missing`), `ArchiveNotLockedError` (`archive_not_locked`), `s3_client(*, region, endpoint_url=None) -> S3Client`, `S3Archive(client, bucket)` with `opened()`, `require_locked()`, `keep(key, content, *, retain_until) -> Archived`, `read(key) -> bytes`. `ledger_testing.containers.localstack_s3() -> DockerContainer`.

Re-check before starting: `ledger_testing.containers` as Milestone 3 left it (`LOCALSTACK_IMAGE` pinned by digest, `localstack_kms`, `localstack_address`, and how it waits for LocalStack: this task's `localstack_s3` waits the same way); how vault's `kms/aws.py` types its boto3 client (the one `cast` of Milestone 3): the archive's `s3_client` is the same `cast`, for S3.

- [ ] **Step 1: Dependencies and the container**

`services/fiscal-do/pyproject.toml` — `dependencies` gains `"boto3>=1.43.111"` and `"botocore>=1.43.111"`. Root dev group gains `"types-boto3-s3>=1.43.106"` (the archive types its client lazily from it; deptry reads a `lazy` import as type-only, as it reads vault's `types_boto3_kms`: re-check). Run: `uv lock && uv sync`.

`libs/testing/src/ledger_testing/containers.py` — beside `localstack_kms`:

```python
def localstack_s3() -> DockerContainer:
    """LocalStack with its S3 only (object lock included), ready once it says so."""
    return (
        DockerContainer(LOCALSTACK_IMAGE)
        .with_env("SERVICES", "s3")
        .with_exposed_ports(LOCALSTACK_PORT)
        .waiting_for(LogMessageWaitStrategy("Ready."))
    )
```

- [ ] **Step 2: Write the failing test**

`services/fiscal-do/tests/test_archive.py`:

```python
import hashlib
from datetime import UTC, datetime, timedelta
from typing import Final
lazy from collections.abc import Generator

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
lazy from types_boto3_s3 import S3Client

from ledger_fiscal_do.adapters.archive import (
    ArchiveConflictError,
    ArchivedObjectMissingError,
    ArchiveNotLockedError,
    ArchiveUnavailableError,
    S3Archive,
    s3_client,
)
from ledger_testing.containers import localstack_address, localstack_s3

LOCKED: Final = "ledger-archive-test"
UNLOCKED: Final = "ledger-unlocked-test"
LOCKABLE: Final = "ledger-lockable-test"
"""Object lock enabled, but no default retention: not the archive's bucket either."""
REGION: Final = "us-east-1"
UNTIL: Final = datetime.now(UTC).replace(microsecond=0) + timedelta(days=3653)


@pytest.fixture(scope="module")
def client(monkeypatch_module: pytest.MonkeyPatch) -> Generator[S3Client]:
    """LocalStack's S3 with a bucket locked in compliance mode and one without a lock."""
    monkeypatch_module.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch_module.setenv("AWS_SECRET_ACCESS_KEY", "test")
    with localstack_s3() as container:
        opened = s3_client(region=REGION, endpoint_url=localstack_address(container))
        opened.create_bucket(Bucket=LOCKED, ObjectLockEnabledForBucket=True)
        opened.put_object_lock_configuration(
            Bucket=LOCKED,
            ObjectLockConfiguration={
                "ObjectLockEnabled": "Enabled",
                "Rule": {"DefaultRetention": {"Mode": "COMPLIANCE", "Days": 1}},
            },
        )
        opened.create_bucket(Bucket=UNLOCKED)
        opened.create_bucket(Bucket=LOCKABLE, ObjectLockEnabledForBucket=True)
        yield opened


@pytest.fixture(scope="module")
def monkeypatch_module() -> Generator[pytest.MonkeyPatch]:
    with pytest.MonkeyPatch.context() as patch:
        yield patch


async def test_a_document_is_kept_under_compliance_retention_and_read_back(client: S3Client) -> None:
    archive = S3Archive(client, LOCKED)
    await archive.require_locked()
    kept = await archive.keep(
        "testecf/101850043/2026/10/E310000000001/ecf-1.xml", b"<ECF/>", retain_until=UNTIL
    )
    assert (kept.sha256, kept.size, kept.retain_until) == (hashlib.sha256(b"<ECF/>").hexdigest(), 6, UNTIL)
    assert kept.version
    assert await archive.read(kept.key) == b"<ECF/>"
    head = client.head_object(Bucket=LOCKED, Key=kept.key)
    assert (head.get("ObjectLockMode"), head.get("ObjectLockRetainUntilDate")) == ("COMPLIANCE", UNTIL)


async def test_what_is_kept_can_be_neither_deleted_nor_released_early(client: S3Client) -> None:
    archive = S3Archive(client, LOCKED)
    kept = await archive.keep("worm/answer-1.json", b"{}", retain_until=UNTIL)
    with pytest.raises(ClientError):
        client.delete_object(Bucket=LOCKED, Key=kept.key, VersionId=kept.version)
    with pytest.raises(ClientError):
        client.put_object_retention(
            Bucket=LOCKED,
            Key=kept.key,
            VersionId=kept.version,
            Retention={"Mode": "COMPLIANCE", "RetainUntilDate": datetime.now(UTC) + timedelta(days=1)},
        )


async def test_keeping_the_same_content_again_answers_what_is_there(client: S3Client) -> None:
    archive = S3Archive(client, LOCKED)
    first = await archive.keep("again/ecf-2.xml", b"<ECF>2</ECF>", retain_until=UNTIL)
    second = await archive.keep("again/ecf-2.xml", b"<ECF>2</ECF>", retain_until=UNTIL + timedelta(days=1))
    assert second == first
    with pytest.raises(ArchiveConflictError):
        await archive.keep("again/ecf-2.xml", b"<ECF>3</ECF>", retain_until=UNTIL)


async def test_a_missing_object_and_an_unlocked_bucket_are_refused(client: S3Client) -> None:
    with pytest.raises(ArchivedObjectMissingError):
        await S3Archive(client, LOCKED).read("nothing/here.xml")
    with pytest.raises(ArchiveNotLockedError):
        await S3Archive(client, UNLOCKED).require_locked()
    with pytest.raises(ArchiveNotLockedError):
        await S3Archive(client, LOCKABLE).require_locked()
    with pytest.raises(ArchiveNotLockedError):
        await S3Archive(client, "no-such-bucket").require_locked()
    with pytest.raises(ArchiveUnavailableError):
        await S3Archive(client, "no-such-bucket").keep("k", b"x", retain_until=UNTIL)
    with pytest.raises(ArchiveUnavailableError):
        await S3Archive(client, "no-such-bucket").read("k")


async def test_an_unreachable_archive_is_unavailable() -> None:
    archive = S3Archive(s3_client(region=REGION, endpoint_url="http://127.0.0.1:9"), LOCKED)
    async with archive.opened() as opened:
        for attempt in (
            opened.require_locked(),
            opened.keep("k", b"x", retain_until=UNTIL),
            opened.read("k"),
        ):
            with pytest.raises(ArchiveUnavailableError):
                await attempt


async def test_an_archive_that_fails_while_checking_what_it_holds_is_unavailable(client: S3Client) -> None:
    archive = S3Archive(client, LOCKED)
    await archive.keep("checked/ecf-9.xml", b"<ECF>9</ECF>", retain_until=UNTIL)

    def unreachable(**_: object) -> None:
        raise EndpointConnectionError(endpoint_url="http://archive.invalid")

    client.meta.events.register("before-send.s3.HeadObject", unreachable)
    try:
        with pytest.raises(ArchiveUnavailableError):
            await archive.keep("checked/ecf-9.xml", b"<ECF>9</ECF>", retain_until=UNTIL)
    finally:
        client.meta.events.unregister("before-send.s3.HeadObject", unreachable)
```


- [ ] **Step 3: Run the test to verify it fails**

Run: `uv run pytest services/fiscal-do/tests/test_archive.py -q --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'ledger_fiscal_do.adapters.archive'`.

- [ ] **Step 4: Implement**

`services/fiscal-do/src/ledger_fiscal_do/adapters/archive.py`:

```python
"""The WORM archive: S3 with object lock in compliance mode (§7; AWS S3 in production, LocalStack locally).

Every signed document and DGII answer is kept for ten years (``RETENTION``) in a bucket created with object
lock: each object is written with ``COMPLIANCE`` retention until its own date, so neither fiscal-do, nor an
operator, nor the account's root can delete it or shorten its retention before then (verified on LocalStack
4.14.0: a version's deletion, a shorter retention and a governance bypass are all refused). A write never
replaces: ``If-None-Match: *`` refuses a key that exists, and keys name their content's digest, so keeping the
same document again (a step run twice) finds the object there, checks its SHA-256, and answers it. S3 checks
the SHA-256 it is sent (``ChecksumSHA256``) and returns it on reads, where botocore verifies it.

boto3 is synchronous; each call runs on a thread (its client is thread-safe). The bucket is configuration,
created by infrastructure (Terraform; ``deploy/local/localstack/init-archive.sh`` locally), never by the
service; ``require_locked`` is a readiness check that it has object lock on, in compliance mode.
"""

import asyncio
import base64
import hashlib
from contextlib import asynccontextmanager
from typing import ClassVar, Final, cast, final, override
lazy from collections.abc import AsyncGenerator
lazy from datetime import datetime

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
lazy from types_boto3_s3 import S3Client

from ledger_fiscal_do.application.ports import Archive, Archived
from ledger_kernel.errors import ConflictError, LedgerError, UnavailableError

COMPLIANCE: Final = "COMPLIANCE"
_EXISTS: Final = frozenset({"PreconditionFailed", "ConditionalRequestConflict"})
_MISSING: Final = frozenset({"NoSuchKey", "404"})
_TIMEOUTS: Final = Config(connect_timeout=5, read_timeout=20, retries={"mode": "standard", "max_attempts": 3})


class ArchiveUnavailableError(UnavailableError):
    """The archive could not be reached or failed: nothing was kept, try again later."""

    code: ClassVar[str] = "archive_unavailable"

    def __init__(self) -> None:
        super().__init__("the archive could not be reached")


class ArchiveConflictError(ConflictError):
    """Other content is kept at a key that names this content's digest: never expected, never replaced."""

    code: ClassVar[str] = "archive_conflict"

    def __init__(self) -> None:
        super().__init__("the archive holds other content at this key")


class ArchivedObjectMissingError(LedgerError):
    """An object the index names is not in the archive: a defect, or a bucket that is not the archive."""

    code: ClassVar[str] = "archived_object_missing"

    def __init__(self) -> None:
        super().__init__("an archived object is missing")


class ArchiveNotLockedError(UnavailableError):
    """The bucket has no object lock in compliance mode: nothing may be kept there."""

    code: ClassVar[str] = "archive_not_locked"

    def __init__(self) -> None:
        super().__init__("the archive's bucket has no object lock in compliance mode")


def _code(error: ClientError) -> str:
    return str(error.response.get("Error", {}).get("Code", ""))


def s3_client(*, region: str, endpoint_url: str | None = None) -> S3Client:
    """An S3 client; credentials are boto3's business (``AWS_*`` variables, a web identity on Kubernetes).

    The one ``cast`` of this adapter: ``boto3.client`` is typed by name only when the full stubs are installed
    (as in vault's ``kms.aws``).
    """
    return cast(
        "S3Client", boto3.client("s3", region_name=region, endpoint_url=endpoint_url, config=_TIMEOUTS)
    )


@final
class S3Archive(Archive):
    """``Archive`` over one S3 bucket with object lock (see the module)."""

    __slots__ = ("_bucket", "_client")

    def __init__(self, client: S3Client, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    @asynccontextmanager
    async def opened(self) -> AsyncGenerator[S3Archive]:
        """This archive while the service runs; its client's connections closed after."""
        try:
            yield self
        finally:
            self._client.close()

    async def require_locked(self) -> None:
        """The readiness check: the bucket exists and locks objects in compliance mode by default."""
        try:
            answer = await asyncio.to_thread(self._client.get_object_lock_configuration, Bucket=self._bucket)
        except ClientError as error:
            raise ArchiveNotLockedError from error
        except BotoCoreError as error:
            raise ArchiveUnavailableError from error
        configuration = answer.get("ObjectLockConfiguration", {})
        mode = configuration.get("Rule", {}).get("DefaultRetention", {}).get("Mode")
        if configuration.get("ObjectLockEnabled") != "Enabled" or mode != COMPLIANCE:
            raise ArchiveNotLockedError

    @override
    async def keep(self, key: str, content: bytes, *, retain_until: datetime) -> Archived:
        digest = hashlib.sha256(content)
        checksum = base64.b64encode(digest.digest()).decode("ascii")
        try:
            answer = await asyncio.to_thread(
                self._client.put_object,
                Bucket=self._bucket,
                Key=key,
                Body=content,
                ChecksumSHA256=checksum,
                ObjectLockMode=COMPLIANCE,
                ObjectLockRetainUntilDate=retain_until,
                IfNoneMatch="*",
            )
        except ClientError as error:
            if _code(error) not in _EXISTS:
                raise ArchiveUnavailableError from error
            return await self._kept(key, checksum, digest.hexdigest())
        except BotoCoreError as error:
            raise ArchiveUnavailableError from error
        return Archived(
            key=key,
            version=answer.get("VersionId", ""),
            sha256=digest.hexdigest(),
            size=len(content),
            retain_until=retain_until,
        )

    async def _kept(self, key: str, checksum: str, sha256: str) -> Archived:
        try:
            head = await asyncio.to_thread(
                self._client.head_object, Bucket=self._bucket, Key=key, ChecksumMode="ENABLED"
            )
        except (ClientError, BotoCoreError) as error:
            raise ArchiveUnavailableError from error
        if head.get("ChecksumSHA256") != checksum:
            raise ArchiveConflictError
        return Archived(
            key=key,
            version=head.get("VersionId", ""),
            sha256=sha256,
            size=head.get("ContentLength", 0),
            retain_until=head["ObjectLockRetainUntilDate"],
        )

    @override
    async def read(self, key: str) -> bytes:
        try:
            answer = await asyncio.to_thread(
                self._client.get_object, Bucket=self._bucket, Key=key, ChecksumMode="ENABLED"
            )
            return await asyncio.to_thread(answer["Body"].read)
        except ClientError as error:
            if _code(error) in _MISSING:
                raise ArchivedObjectMissingError from error
            raise ArchiveUnavailableError from error
        except BotoCoreError as error:
            raise ArchiveUnavailableError from error
```


- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run pytest services/fiscal-do/tests/test_archive.py -q --no-cov`
Expected: PASS (about 45 s: LocalStack starts once for the module; the unreachable endpoint's test waits out botocore's three attempts). Verified as written against LocalStack 4.14.0 on 2026-10-12: a version's deletion and a shortened retention are refused in compliance mode, `If-None-Match: *` refuses an existing key.

- [ ] **Step 6: Gate**

Run: `uv run poe fmt && uv run poe check`
Expected: every step passes, coverage 100 % (`localstack_s3` is covered by this test).

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock services/fiscal-do libs/testing
git commit -m "feat(fiscal-do): the WORM archive on S3 object lock, compliance mode, never overwritten

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: vault signs, identity knows the company: the gRPC adapters

**Files:**
- Create: `services/fiscal-do/src/ledger_fiscal_do/adapters/vault.py`, `adapters/identity.py`
- Modify: `services/fiscal-do/pyproject.toml`
- Test: `services/fiscal-do/tests/remotes.py` (test support), `services/fiscal-do/tests/test_remote.py`

**Interfaces:**
- Consumes: `Signer`, `Companies`, `CompanyFacts`, `CompanyUnknownError`, `CertificateUnavailableError` (Task 4); `Channel`, `call_unary`, `RemoteConflictError`, `RemoteNotFoundError`, `RemoteDefectError`, `ERROR_CODE_KEY` (`ledger_platform.grpc_client`, M2 Task 8 and M3 Task 3); `VaultServiceStub`, `SignXmlRequest`, `SignSeedRequest` and the rest of `ledger.vault.v1` (M3 Task 1); `ledger_contracts.vault` (`SIGN_XML`, `SIGN_SEED`, `NO_ACTIVE_CERTIFICATE`, `IDEMPOTENT_METHODS`); `IdentityQueryServiceStub`, `GetCompanyRequest`, `COMPANY_STATUS_ACTIVE` (`ledger.identity.v1`, M2 Task 7).
- Produces: `adapters.vault`: `SIGN_DEADLINE` (5 s), `CERTIFICATE_EXPIRED`, `VaultSigner(channel)` (the `Signer` port: vault's `no_active_certificate` and `certificate_expired` become `CertificateUnavailableError`). `adapters.identity`: `GET_COMPANY`, `IdentityCompanies(channel)` (the `Companies` port: `NOT_FOUND` becomes `CompanyUnknownError`). Test support `remotes`: `FakeVault`, `FakeIdentity` (`organization`, `grants`), `Remotes(address, vault, identity)`, `remotes()` (both on one gRPC server on 127.0.0.1), the tenants `INACTIVE`, `STRANGER`, `NO_CERTIFICATE`, `EXPIRED`, `CHANGED`.

Re-check before starting: `Channel(target, *, caller, version, idempotent=())`, `.opened()`, `.current()` and `call_unary(method, request, *, rpc, deadline=...)` as Milestone 3 Task 3 left them (`tracing=` is optional there and not passed here: the active trace still travels in `traceparent`); vault's code for an expired active certificate (`ActiveCertificateExpiredError.code`, `certificate_expired`, M3 Task 4) and identity's `company_not_found`; whether the generated servicers of Milestones 2 and 3 are abstract (the fakes implement every method, refusing with `UNIMPLEMENTED` those fiscal-do never calls).

- [ ] **Step 1: Dependencies**

`services/fiscal-do/pyproject.toml` — `dependencies` gains `"ledger-platform"` and `"ledger-contracts"` (`{ workspace = true }` sources), and the deptry map gains `"ledger-contracts" = ["ledger_contracts", "ledger"]`. Root: "fiscal-do's domain is standard library and kernel only" already forbids both to the domain. Run: `uv lock && uv sync`.

- [ ] **Step 2: Write the failing test**

`services/fiscal-do/tests/remotes.py`:

```python
"""vault and identity as fiscal-do sees them, over gRPC: fakes served on 127.0.0.1 for the length of a test.

The fake vault signs as vault's output looks (``fakes.vault_signed``), or refuses by company; the fake
identity knows one organization's companies and the permissions of its callers.
"""

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Final, NoReturn, final, override
lazy from collections.abc import AsyncGenerator

import grpc

from ledger.identity.v1.identity_query_pb2 import (
    COMPANY_STATUS_ACTIVE,
    COMPANY_STATUS_INACTIVE,
    Company,
    GetCompanyResponse,
    GetPermissionsResponse,
)
from ledger.identity.v1.identity_query_pb2_grpc import (
    IdentityQueryServiceServicer,
    add_IdentityQueryServiceServicer_to_server,
)
from ledger.vault.v1.vault_pb2 import SignSeedResponse, SignXmlResponse
from ledger.vault.v1.vault_pb2_grpc import VaultServiceServicer, add_VaultServiceServicer_to_server
from ledger_kernel.ids import CompanyId, OrganizationId, Tenant, new_id
from ledger_platform.grpc_client import ERROR_CODE_KEY
lazy from ledger.identity.v1.identity_query_pb2 import GetCompanyRequest, GetPermissionsRequest
lazy from ledger.vault.v1.vault_pb2 import (
    GetCertificateRequest,
    GetCertificateResponse,
    ListCertificatesRequest,
    ListCertificatesResponse,
    RotateCertificateRequest,
    RotateCertificateResponse,
    SignSeedRequest,
    SignXmlRequest,
    UploadCertificateRequest,
    UploadCertificateResponse,
)

from fakes import vault_signed
from samples import TENANT

INACTIVE: Final = Tenant(TENANT.organization, CompanyId(new_id()))
STRANGER: Final = Tenant(OrganizationId(new_id()), CompanyId(new_id()))
NO_CERTIFICATE: Final = Tenant(TENANT.organization, CompanyId(new_id()))
EXPIRED: Final = Tenant(TENANT.organization, CompanyId(new_id()))
CHANGED: Final = Tenant(TENANT.organization, CompanyId(new_id()))
EVERYTHING: Final = ("company.read", "company.manage", "invoice.read", "invoice.issue")

type Context = grpc.aio.ServicerContext[object, object]
"""A call being answered, as the fakes see it."""


async def refuse(context: Context, status: grpc.StatusCode, code: str) -> NoReturn:
    """End the call as a chassis server ends a refused one: its status, and its code in the trailers."""
    await context.abort(status, code, trailing_metadata=((ERROR_CODE_KEY, code),))


@final
class FakeVault(VaultServiceServicer):
    """vault's signing methods: a signature shaped as vault's, or its refusals by company."""

    def __init__(self) -> None:
        self.references: list[str] = []
        self.refusals: dict[str, tuple[grpc.StatusCode, str]] = {
            str(NO_CERTIFICATE.company): (grpc.StatusCode.FAILED_PRECONDITION, "no_active_certificate"),
            str(EXPIRED.company): (grpc.StatusCode.FAILED_PRECONDITION, "certificate_expired"),
            str(CHANGED.company): (grpc.StatusCode.FAILED_PRECONDITION, "certificate_changed"),
            str(STRANGER.company): (grpc.StatusCode.INVALID_ARGUMENT, "document_malformed"),
        }

    async def _refused(self, company: str, context: Context) -> None:
        refusal = self.refusals.get(company)
        if refusal is not None:
            await refuse(context, *refusal)

    @override
    async def SignXml(self, request: SignXmlRequest, context: Context) -> SignXmlResponse:
        await self._refused(request.company_id, context)
        self.references.append(request.reference)
        return SignXmlResponse(signed_document=vault_signed(request.document))

    @override
    async def SignSeed(self, request: SignSeedRequest, context: Context) -> SignSeedResponse:
        await self._refused(request.company_id, context)
        return SignSeedResponse(signed_seed=vault_signed(request.seed))

    @override
    async def UploadCertificate(
        self, request: UploadCertificateRequest, context: Context
    ) -> UploadCertificateResponse:
        await refuse(context, grpc.StatusCode.UNIMPLEMENTED, "not_faked")

    @override
    async def RotateCertificate(
        self, request: RotateCertificateRequest, context: Context
    ) -> RotateCertificateResponse:
        await refuse(context, grpc.StatusCode.UNIMPLEMENTED, "not_faked")

    @override
    async def GetCertificate(
        self, request: GetCertificateRequest, context: Context
    ) -> GetCertificateResponse:
        await refuse(context, grpc.StatusCode.UNIMPLEMENTED, "not_faked")

    @override
    async def ListCertificates(
        self, request: ListCertificatesRequest, context: Context
    ) -> ListCertificatesResponse:
        await refuse(context, grpc.StatusCode.UNIMPLEMENTED, "not_faked")


@final
@dataclass(slots=True)
class FakeIdentity(IdentityQueryServiceServicer):
    """identity's queries: ``TENANT``'s organization has an active company and an inactive one."""

    organization: str = str(TENANT.organization)
    """The one organization identity knows."""
    grants: dict[tuple[str, str], tuple[str, ...]] = field(
        default_factory=dict[tuple[str, str], tuple[str, ...]]
    )
    """Permissions by (company, subject); a subject without an entry holds none."""

    @override
    async def GetCompany(self, request: GetCompanyRequest, context: Context) -> GetCompanyResponse:
        statuses = {
            str(TENANT.company): COMPANY_STATUS_ACTIVE,
            str(INACTIVE.company): COMPANY_STATUS_INACTIVE,
        }
        status = statuses.get(request.company_id)
        if status is None or request.organization_id != self.organization:
            await refuse(context, grpc.StatusCode.NOT_FOUND, "company_not_found")
        company = Company(
            id=request.company_id,
            organization_id=request.organization_id,
            tax_id="101850043",
            legal_name="FERRETERIA DUARTE SRL",
            status=status,
            version=1,
        )
        return GetCompanyResponse(company=company)

    @override
    async def GetPermissions(
        self, request: GetPermissionsRequest, context: Context
    ) -> GetPermissionsResponse:
        del context
        return GetPermissionsResponse(permissions=self.grants.get((request.company_id, request.subject), ()))


@final
@dataclass(frozen=True, slots=True)
class Remotes:
    """The fakes, and where they answer."""

    address: str
    vault: FakeVault
    identity: FakeIdentity


@asynccontextmanager
async def remotes() -> AsyncGenerator[Remotes]:
    """vault and identity, faked, on one gRPC server on 127.0.0.1."""
    vault, identity = FakeVault(), FakeIdentity()
    server = grpc.aio.server()
    add_VaultServiceServicer_to_server(vault, server)
    add_IdentityQueryServiceServicer_to_server(identity, server)
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    try:
        yield Remotes(f"127.0.0.1:{port}", vault, identity)
    finally:
        await server.stop(None)
```


`services/fiscal-do/tests/test_remote.py`:

```python
lazy from collections.abc import AsyncGenerator

import pytest

from ledger_fiscal_do.adapters.identity import IdentityCompanies
from ledger_fiscal_do.adapters.vault import VaultSigner
from ledger_fiscal_do.application.ports import CertificateUnavailableError, CompanyUnknownError
from ledger_platform.grpc_client import Channel, RemoteConflictError, RemoteDefectError
lazy from ledger_kernel.ids import Tenant

from remotes import CHANGED, EXPIRED, INACTIVE, NO_CERTIFICATE, STRANGER, remotes
from samples import TENANT


@pytest.fixture
async def channel() -> AsyncGenerator[Channel]:
    async with (
        remotes() as served,
        Channel(served.address, caller="fiscal-do", version="0.1.0").opened() as opened,
    ):
        yield opened


async def test_vault_signs_documents_and_seeds(channel: Channel) -> None:
    signer = VaultSigner(channel)
    signed = await signer.sign(TENANT, b"<ECF><a/></ECF>", reference="E310000000001")
    assert signed.endswith(b"</Signature></ECF>")
    seed = b"<SemillaModel><valor>v</valor></SemillaModel>"
    assert (await signer.sign_seed(TENANT, seed)).endswith(b"</Signature></SemillaModel>")


@pytest.mark.parametrize("tenant", [NO_CERTIFICATE, EXPIRED], ids=["none", "expired"])
async def test_without_a_usable_certificate_the_document_waits(channel: Channel, tenant: Tenant) -> None:
    signer = VaultSigner(channel)
    with pytest.raises(CertificateUnavailableError):
        await signer.sign(tenant, b"<ECF/>", reference="E310000000001")
    with pytest.raises(CertificateUnavailableError):
        await signer.sign_seed(tenant, b"<SemillaModel/>")


async def test_other_refusals_stay_the_platform_s(channel: Channel) -> None:
    signer = VaultSigner(channel)
    with pytest.raises(RemoteConflictError):
        await signer.sign(CHANGED, b"<ECF/>", reference="E310000000001")
    with pytest.raises(RemoteDefectError):
        await signer.sign(STRANGER, b"<ECF/>", reference="E310000000001")


async def test_identity_answers_whether_a_company_is_active(channel: Channel) -> None:
    companies = IdentityCompanies(channel)
    active = await companies.company(TENANT)
    assert (str(active.tax_id), active.active) == ("101850043", True)
    assert not (await companies.company(INACTIVE)).active
    with pytest.raises(CompanyUnknownError):
        await companies.company(STRANGER)
```


- [ ] **Step 3: Run the test to verify it fails**

Run: `uv run pytest services/fiscal-do/tests/test_remote.py -q --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'ledger_fiscal_do.adapters.identity'`.

- [ ] **Step 4: Implement**

`services/fiscal-do/src/ledger_fiscal_do/adapters/vault.py`:

```python
"""``Signer`` over vault's gRPC (``ledger.vault.v1.VaultService``): fiscal-do never holds a private key (§2).

vault refuses to sign for a company without a usable certificate with ``FAILED_PRECONDITION`` and the code
``no_active_certificate`` or ``certificate_expired``: that is ``CertificateUnavailableError``, on which the
emission waits (``blocked_certificate``) instead of failing. Any other refusal is a defect of fiscal-do's (a
document vault cannot read) or an outage, raised as the platform's ``RemoteError``. The reference vault keeps
with its audit is the e-NCF (``E310000000001``), or ``ANECF-<annulment id>``: 1 to 64 visible ASCII
characters.
"""

from typing import Final, final, override

from ledger.vault.v1.vault_pb2 import SignSeedRequest, SignXmlRequest
from ledger.vault.v1.vault_pb2_grpc import VaultServiceStub
from ledger_contracts.vault import NO_ACTIVE_CERTIFICATE, SIGN_SEED, SIGN_XML
from ledger_fiscal_do.application.ports import CertificateUnavailableError, Signer
from ledger_platform.grpc_client import RemoteConflictError, call_unary
lazy from ledger_kernel.ids import Tenant
lazy from ledger_platform.grpc_client import Channel

SIGN_DEADLINE: Final = 5.0
"""vault's p95 is under 50 ms (Milestone 3, Task 14); five seconds is an outage, not a slow signature."""
CERTIFICATE_EXPIRED: Final = "certificate_expired"
"""vault's code for an active certificate past its ``not_after`` (``ActiveCertificateExpiredError``)."""
_UNUSABLE: Final = frozenset({NO_ACTIVE_CERTIFICATE, CERTIFICATE_EXPIRED})


def _unusable(error: RemoteConflictError) -> CertificateUnavailableError | RemoteConflictError:
    return CertificateUnavailableError(error.remote_code) if error.remote_code in _UNUSABLE else error


@final
class VaultSigner(Signer):
    """Signatures by vault, over the service's channel to it."""

    __slots__ = ("_channel",)

    def __init__(self, channel: Channel) -> None:
        self._channel = channel

    @override
    async def sign(self, tenant: Tenant, document: bytes, *, reference: str) -> bytes:
        request = SignXmlRequest(
            organization_id=str(tenant.organization),
            company_id=str(tenant.company),
            document=document,
            reference=reference,
        )
        try:
            answer = await call_unary(
                VaultServiceStub(self._channel.current()).SignXml,
                request,
                rpc=SIGN_XML,
                deadline=SIGN_DEADLINE,
            )
        except RemoteConflictError as error:
            raise _unusable(error) from error
        return answer.signed_document

    @override
    async def sign_seed(self, tenant: Tenant, seed: bytes) -> bytes:
        request = SignSeedRequest(
            organization_id=str(tenant.organization), company_id=str(tenant.company), seed=seed
        )
        try:
            answer = await call_unary(
                VaultServiceStub(self._channel.current()).SignSeed,
                request,
                rpc=SIGN_SEED,
                deadline=SIGN_DEADLINE,
            )
        except RemoteConflictError as error:
            raise _unusable(error) from error
        return answer.signed_seed
```


`services/fiscal-do/src/ledger_fiscal_do/adapters/identity.py`:

```python
"""``Companies`` over identity's gRPC (``ledger.identity.v1.IdentityQueryService``).

identity owns companies (§3): their tax id, legal name and whether they are active. fiscal-do asks for them
when it issues or annuls, never caches them (a deactivated company issues nothing from the moment it is
deactivated), and copies them into the document it numbers (the issuer as of that moment). A company identity
does not know in the caller's organization is ``CompanyUnknownError``.
"""

from typing import Final, final, override

from ledger.identity.v1.identity_query_pb2 import COMPANY_STATUS_ACTIVE, GetCompanyRequest
from ledger.identity.v1.identity_query_pb2_grpc import IdentityQueryServiceStub
from ledger_fiscal_do.application.ports import Companies, CompanyFacts, CompanyUnknownError
from ledger_kernel.countries.do import TaxId
from ledger_platform.grpc_client import RemoteNotFoundError, call_unary
lazy from ledger_kernel.ids import Tenant
lazy from ledger_platform.grpc_client import Channel

GET_COMPANY: Final = "/ledger.identity.v1.IdentityQueryService/GetCompany"


@final
class IdentityCompanies(Companies):
    """Companies as identity holds them, over the service's channel to it."""

    __slots__ = ("_channel",)

    def __init__(self, channel: Channel) -> None:
        self._channel = channel

    @override
    async def company(self, tenant: Tenant) -> CompanyFacts:
        request = GetCompanyRequest(organization_id=str(tenant.organization), company_id=str(tenant.company))
        try:
            answer = await call_unary(
                IdentityQueryServiceStub(self._channel.current()).GetCompany, request, rpc=GET_COMPANY
            )
        except RemoteNotFoundError as error:
            raise CompanyUnknownError from error
        found = answer.company
        return CompanyFacts(
            tax_id=TaxId.parse(found.tax_id),
            legal_name=found.legal_name,
            active=found.status == COMPANY_STATUS_ACTIVE,
        )
```


- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run pytest services/fiscal-do/tests/test_remote.py -q --no-cov`
Expected: PASS.

- [ ] **Step 6: Gate**

Run: `uv run poe fmt && uv run poe check`
Expected: every step passes, coverage 100 %.

- [ ] **Step 7: Commit**

```bash
git add uv.lock services/fiscal-do
git commit -m "feat(fiscal-do): signatures by vault and company facts from identity, over gRPC

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: fiscal-do's settings, schema and migrations

**Files:**
- Create: `services/fiscal-do/src/ledger_fiscal_do/settings.py`, `adapters/postgres/__init__.py`, `adapters/postgres/tables.py`, `adapters/postgres/migrate.py`, `adapters/postgres/migrations/__init__.py`, `adapters/postgres/migrations/env.py`, `adapters/postgres/migrations/versions/__init__.py`, `adapters/postgres/migrations/versions/r0001_fiscal_do.py`
- Modify: `services/fiscal-do/pyproject.toml`, `pyproject.toml` (root)
- Test: `services/fiscal-do/tests/conftest.py`, `services/fiscal-do/tests/test_settings.py`, `services/fiscal-do/tests/test_migrations.py`

**Interfaces:**
- Consumes: `ApiSettings` (`ledger_platform.settings`, M2 Task 3); `Migrations`, `run_migrations`, `BASE`, `HEAD`, `SchemaNotCurrentError` (`ledger_platform.migrations`, M3 Task 3); `CHASSIS_METADATA`, `CHASSIS_POLICIES` (`ledger_platform.schema`); `tenant_isolation`, `Database`; `Outbox`, `Tracing`; `Postgres`, `restored_logging` (`ledger_testing`); `OFFICIAL_ECF`, `OFFICIAL_FC` (Task 3).
- Produces: `settings`: `ADDRESS`, `FiscalProblem`, `FiscalSettings(ApiSettings)` with `service_name = "fiscal-do"`, `identity_address`, `vault_address`, `temporal_address` (required, `host:port`), `temporal_namespace = "default"`, `temporal_task_queue = "fiscal-do"`, `dgii_ecf_url`, `dgii_fc_url` (DGII's by default; production refuses others), `dgii_connect_seconds = 5`, `dgii_call_seconds = 20` (at most 25), `archive_bucket` (required), `archive_region = "us-east-1"`, `archive_endpoint_url` (HTTPS beyond a laptop), `contingency_alert_seconds = 21600`, `expiry_check_seconds = 3600`, `pipeline_check_seconds = 60`. `adapters.postgres.tables`: `FiscalTable`, `ProfileRow` (`fiscal_profiles`), `SequenceRow` (`sequences`), `DocumentRow` (`documents`), `AnnulmentRow` (`annulments`), `ArchiveIndexRow` (`archive_index`), `SequenceWatchRow` (`sequence_watch`), `PipelineRow` (`pipeline`). `adapters.postgres.migrate`: `SCRIPTS`, `MIGRATIONS`, `upgrade`, `downgrade`, `head`, `require_head`, `main` (console script `ledger-fiscal-do-migrate`). Revision `0001`: the chassis's tables and policies, fiscal-do's seven tables, `tenant_isolation` on the five of a company. Test fixtures (`conftest.py`): `restore_logging`, `postgres` (session), `database_url` (session, migrated), `fresh_database_url`, `database` (every table emptied after each test), `outbox`.

Re-check before starting: the chassis DDL as Milestones 2 and 3 spell it in their first revisions (this revision spells it the same way; `test_the_migrated_schema_is_exactly_the_models` fails on any difference with `CHASSIS_METADATA`, and passed against the platform at `5ca3de2`, which indexes `outbox.stored_at`); `ApiSettings`' fields; vault's and identity's `conftest.py` (fiscal-do's follows them).

- [ ] **Step 1: Dependencies, script, layers, coverage**

`services/fiscal-do/pyproject.toml` — `dependencies` gains `"alembic>=1.20.0"` and `"sqlalchemy>=2.1.4"`, and:

```toml
[project.scripts]
ledger-fiscal-do-migrate = "ledger_fiscal_do.adapters.postgres.migrate:main"
```

Root `pyproject.toml`:

- ruff: `runtime-evaluated-base-classes` gains `"ledger_fiscal_do.adapters.postgres.tables.FiscalTable"`.
- "fiscal-do is hexagonal": `layers = ["adapters", "settings", "application", "domain"]`.
- coverage (Decision 17): `[tool.coverage.run]` gains `source = ["services/fiscal-do/src/ledger_fiscal_do/adapters/postgres/migrations"]` beside `source_pkgs`, and `core = "sysmon"` (in place of `"ctrace"`, unless Milestone 2 or 3 already changed it).

Run: `uv lock && uv sync`

- [ ] **Step 2: Write the failing tests**

`services/fiscal-do/tests/conftest.py`:

```python
"""Fixtures of fiscal-do's tests: a real PostgreSQL migrated to this version's schema, an outbox."""

from typing import Final
lazy from collections.abc import AsyncGenerator, Generator

import pytest
from opentelemetry.sdk.trace import TracerProvider
from prometheus_client import CollectorRegistry
from sqlalchemy import text

from ledger_fiscal_do.adapters.postgres.migrate import upgrade
from ledger_kernel.ids import new_id
from ledger_platform.database import Database
from ledger_platform.outbox import Outbox
from ledger_platform.tracing import Tracing
from ledger_testing.containers import Postgres
from ledger_testing.logs import restored_logging

EVERY_TABLE: Final = (
    "pipeline, sequence_watch, archive_index, annulments, documents, sequences, fiscal_profiles,"
    " outbox, inbox, idempotency_keys"
)
EMPTY_EVERY_TABLE: Final = text(f"TRUNCATE {EVERY_TABLE}")
"""``TRUNCATE`` is the owner's and is not filtered by row-level security; in production the runtime role holds
no ``TRUNCATE`` (§7: the migration owner is another role)."""


@pytest.fixture(autouse=True)
def restore_logging() -> Generator[None]:
    """Each test starts from, and leaves, the plain baseline: building a service configures logging."""
    with restored_logging():
        yield


@pytest.fixture(scope="session")
def postgres() -> Generator[Postgres]:
    """A real PostgreSQL for the whole session."""
    with Postgres.started() as server:
        yield server


@pytest.fixture(scope="session")
def database_url(postgres: Postgres) -> str:
    """fiscal-do's database, migrated to this version; the service connects as its owner, no superuser."""
    url = postgres.database("fiscal_do", role="fiscal_do", password="fiscal-password")
    upgrade(url)
    return url


@pytest.fixture
def fresh_database_url(postgres: Postgres) -> str:
    """An empty database of its own, owned by a role of its own: for the tests of the migrations."""
    name = f"fresh_{new_id().hex[16:]}"
    return postgres.database(name, role=name, password="fresh-password")


@pytest.fixture
async def database(database_url: str) -> AsyncGenerator[Database]:
    """A pool on fiscal-do's database, every table emptied after the test."""
    async with Database.open(database_url, pool_size=4) as opened:
        yield opened
        async with opened.engine.begin() as connection:
            await connection.execute(EMPTY_EVERY_TABLE)


@pytest.fixture
def outbox() -> Outbox:
    """An outbox of the test's own: its own tracer provider and metrics registry."""
    return Outbox(tracing=Tracing(TracerProvider()), registry=CollectorRegistry())
```


`services/fiscal-do/tests/test_settings.py`:

```python
from typing import Final

import pytest
from pydantic import SecretStr, ValidationError

from ledger_fiscal_do.domain.vocabulary import OFFICIAL_ECF, OFFICIAL_FC
from ledger_fiscal_do.settings import FiscalSettings

COMMON: Final[dict[str, object]] = {
    "database_url": SecretStr("postgresql+psycopg://fiscal_do:secret@localhost/fiscal_do"),
    "token_issuer": "https://auth.example.com",
    "token_audience": "ledger",
    "token_jwks_url": "https://auth.example.com/oauth/v2/keys",
    "identity_address": "identity:50051",
    "vault_address": "vault:50051",
    "temporal_address": "temporal:7233",
    "archive_bucket": "ledger-archive",
}


def test_a_laptop_runs_against_dgii_s_hosts_by_default() -> None:
    settings = FiscalSettings.model_validate(COMMON)
    assert (settings.service_name, settings.dgii_ecf_url, settings.dgii_fc_url) == (
        "fiscal-do",
        OFFICIAL_ECF,
        OFFICIAL_FC,
    )
    assert (settings.temporal_namespace, settings.temporal_task_queue) == ("default", "fiscal-do")
    local = FiscalSettings.model_validate(COMMON | {"dgii_ecf_url": "http://fake-dgii:8080"})
    assert local.dgii_ecf_url == "http://fake-dgii:8080"


@pytest.mark.parametrize(
    ("changes", "problem"),
    [
        ({"environment": "production", "dgii_ecf_url": "https://elsewhere.example"}, "DGII's own hosts"),
        ({"environment": "production", "dgii_fc_url": "http://fake-dgii:8080"}, "DGII's own hosts"),
        ({"environment": "staging", "archive_endpoint_url": "http://localstack:4566"}, "https"),
        ({"dgii_ecf_url": "https://ecf.dgii.gov.do/testecf"}, "dgii_ecf_url"),
        ({"vault_address": "vault"}, "vault_address"),
        ({"dgii_call_seconds": 30}, "dgii_call_seconds"),
    ],
    ids=["production-elsewhere", "production-fake", "archive-in-clear", "path", "address", "timeout"],
)
def test_a_configuration_that_cannot_run_safely_does_not_start(
    changes: dict[str, object], problem: str
) -> None:
    with pytest.raises(ValidationError, match=problem):
        FiscalSettings.model_validate(COMMON | changes)


def test_staging_reaches_the_archive_over_https() -> None:
    staging = FiscalSettings.model_validate(
        COMMON | {"environment": "staging", "archive_endpoint_url": "https://s3.internal"}
    )
    assert staging.archive_endpoint_url == "https://s3.internal"
```


`services/fiscal-do/tests/test_migrations.py`:

```python
from typing import Final

import pytest
from alembic.autogenerate import produce_migrations, render_python_code
from alembic.migration import MigrationContext
from pydantic import TypeAdapter
from sqlalchemy import MetaData, create_engine, inspect, text
from sqlalchemy.pool import NullPool

from ledger_fiscal_do.adapters.postgres import migrate
from ledger_fiscal_do.adapters.postgres.migrate import downgrade, head, require_head, upgrade
from ledger_fiscal_do.adapters.postgres.tables import FiscalTable
from ledger_kernel.errors import ErrorKind
from ledger_platform.database import Database
from ledger_platform.migrations import SchemaNotCurrentError
from ledger_platform.schema import CHASSIS_METADATA

FISCAL_TABLES: Final = frozenset(
    {
        "fiscal_profiles",
        "sequences",
        "documents",
        "annulments",
        "archive_index",
        "sequence_watch",
        "pipeline",
    }
)
CHASSIS_TABLES: Final = frozenset({"outbox", "inbox", "idempotency_keys"})
PAIRS: Final = TypeAdapter(list[tuple[str, str]])


def tables_of(url: str) -> set[str]:
    engine = create_engine(url, poolclass=NullPool)
    try:
        with engine.connect() as connection:
            return set(inspect(connection).get_table_names())
    finally:
        engine.dispose()


def models() -> MetaData:
    combined = MetaData()
    for table in (*CHASSIS_METADATA.tables.values(), *FiscalTable.metadata.tables.values()):
        table.to_metadata(combined)
    return combined


def pending_changes(url: str) -> str | None:
    engine = create_engine(url, poolclass=NullPool)
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(
                connection, opts={"compare_type": True, "compare_server_default": True}
            )
            operations = produce_migrations(context, models()).upgrade_ops
            return None if operations is None or operations.is_empty() else render_python_code(operations)
    finally:
        engine.dispose()


def test_the_schema_is_built_from_nothing_and_taken_back_to_nothing(fresh_database_url: str) -> None:
    upgrade(fresh_database_url)
    assert tables_of(fresh_database_url) == {*FISCAL_TABLES, *CHASSIS_TABLES, "alembic_version"}
    downgrade(fresh_database_url)
    assert tables_of(fresh_database_url) == {"alembic_version"}
    upgrade(fresh_database_url)
    assert tables_of(fresh_database_url) >= FISCAL_TABLES


def test_the_migrated_schema_is_exactly_the_models(database_url: str) -> None:
    upgrade(database_url)
    assert pending_changes(database_url) is None


def test_a_company_s_data_is_under_its_policy_and_the_watches_are_not(database_url: str) -> None:
    engine = create_engine(database_url, poolclass=NullPool)
    try:
        with engine.connect() as connection:
            rows = connection.execute(text("SELECT tablename::text, policyname::text FROM pg_policies"))
            policies = PAIRS.validate_python([tuple(row) for row in rows])
    finally:
        engine.dispose()
    assert sorted(policies) == [
        ("annulments", "tenant_isolation"),
        ("archive_index", "tenant_isolation"),
        ("documents", "tenant_isolation"),
        ("fiscal_profiles", "tenant_isolation"),
        ("idempotency_keys", "organization_isolation"),
        ("sequences", "tenant_isolation"),
    ]


async def test_the_readiness_check_passes_on_a_database_at_this_version_only(
    database: Database, fresh_database_url: str
) -> None:
    assert head() == "0001"
    await require_head(database, head())
    upgrade(fresh_database_url)
    downgrade(fresh_database_url)
    async with Database.open(fresh_database_url, pool_size=1) as empty:
        with pytest.raises(SchemaNotCurrentError) as caught:
            await require_head(empty, head())
    assert caught.value.kind is ErrorKind.UNAVAILABLE


def test_the_command_line_migrates_the_database_its_settings_name(
    monkeypatch: pytest.MonkeyPatch, fresh_database_url: str
) -> None:
    for name, value in {
        "LEDGER_DATABASE_URL": fresh_database_url,
        "LEDGER_TOKEN_ISSUER": "https://auth.example.com",
        "LEDGER_TOKEN_AUDIENCE": "ledger",
        "LEDGER_TOKEN_JWKS_URL": "https://auth.example.com/oauth/v2/keys",
        "LEDGER_IDENTITY_ADDRESS": "identity:50051",
        "LEDGER_VAULT_ADDRESS": "vault:50051",
        "LEDGER_TEMPORAL_ADDRESS": "temporal:7233",
        "LEDGER_ARCHIVE_BUCKET": "ledger-archive",
    }.items():
        monkeypatch.setenv(name, value)
    assert migrate.main() == 0
    assert tables_of(fresh_database_url) >= FISCAL_TABLES
```


- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest services/fiscal-do/tests/test_settings.py services/fiscal-do/tests/test_migrations.py -q --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'ledger_fiscal_do.settings'`.

- [ ] **Step 4: Implement**

`services/fiscal-do/src/ledger_fiscal_do/settings.py`:

```python
"""fiscal-do's settings: the chassis's and its API's, the services it calls, DGII, the archive, its watches.

DGII's hosts are configuration so that the fake DGII can stand in for them in tests and locally; production
refuses any but DGII's own (``https://ecf.dgii.gov.do``, ``https://fc.dgii.gov.do``): a document sent
elsewhere is a document not sent. Beyond a laptop the archive is reached over HTTPS. AWS credentials are
boto3's business (``AWS_*`` variables, a web identity on Kubernetes), never settings of fiscal-do's.
"""

from enum import StrEnum
from typing import Annotated, Final, Self

from pydantic import Field, model_validator

from ledger_fiscal_do.domain.vocabulary import OFFICIAL_ECF, OFFICIAL_FC
from ledger_platform.settings import ApiSettings

ADDRESS: Final = r"^[A-Za-z0-9.-]+:[0-9]{1,5}$"
_BEYOND_A_LAPTOP: Final = frozenset({"staging", "production"})


class FiscalProblem(StrEnum):
    """Why a configuration cannot run fiscal-do safely, naming settings, never their values."""

    DGII_ELSEWHERE = "dgii_ecf_url and dgii_fc_url must be DGII's own hosts in production"
    ARCHIVE_IN_CLEAR = "archive_endpoint_url must be https in staging and production"


class _FiscalSettingsError(ValueError):
    """A configuration that cannot run fiscal-do safely."""

    def __init__(self, problem: FiscalProblem) -> None:
        super().__init__(problem.value)


class FiscalSettings(ApiSettings):
    """fiscal-do's settings (see the module)."""

    service_name: str = Field(default="fiscal-do", pattern=r"^[a-z][a-z0-9-]{1,30}$")
    identity_address: str = Field(default=..., min_length=3, max_length=255, pattern=ADDRESS)
    """identity's gRPC, ``host:port``: the company's facts and the caller's permissions."""
    vault_address: str = Field(default=..., min_length=3, max_length=255, pattern=ADDRESS)
    """vault's gRPC, ``host:port``: signatures."""
    temporal_address: str = Field(default=..., min_length=3, max_length=255, pattern=ADDRESS)
    """Temporal's frontend, ``host:port``."""
    temporal_namespace: str = Field(default="default", min_length=1, max_length=255)
    temporal_task_queue: str = Field(default="fiscal-do", min_length=1, max_length=255)
    dgii_ecf_url: str = Field(default=OFFICIAL_ECF, pattern=r"^https?://[^/?#]+$")
    """DGII's host for authentication, reception, results, track ids and annulments."""
    dgii_fc_url: str = Field(default=OFFICIAL_FC, pattern=r"^https?://[^/?#]+$")
    """DGII's host for consumer-invoice summaries."""
    dgii_connect_seconds: Annotated[float, Field(gt=0, le=30)] = 5.0
    dgii_call_seconds: Annotated[float, Field(gt=0, le=25)] = 20.0
    """Each attempt; at most 25 so that two attempts fit in an activity's minute."""
    archive_bucket: str = Field(default=..., min_length=3, max_length=63, pattern=r"^[a-z0-9][a-z0-9.-]+$")
    archive_region: str = Field(default="us-east-1", pattern=r"^[a-z]{2}(-[a-z]+)+-[0-9]$")
    archive_endpoint_url: str | None = Field(default=None, max_length=2048)
    """Another endpoint than AWS's own: LocalStack's, locally."""
    contingency_alert_seconds: Annotated[int, Field(ge=300, le=259_200)] = 21_600
    """How long a document may wait in contingency before the pipeline worker warns (DGII allows 72 hours)."""
    expiry_check_seconds: Annotated[int, Field(ge=60, le=86_400)] = 3_600
    pipeline_check_seconds: Annotated[int, Field(ge=10, le=3_600)] = 60

    @model_validator(mode="after")
    def require_dgii_and_a_safe_archive(self) -> Self:
        """Refuse another DGII than DGII's in production, and an archive in clear beyond a laptop."""
        official = (self.dgii_ecf_url, self.dgii_fc_url) == (OFFICIAL_ECF, OFFICIAL_FC)
        if self.environment == "production" and not official:
            raise _FiscalSettingsError(FiscalProblem.DGII_ELSEWHERE)
        endpoint = self.archive_endpoint_url
        if (
            self.environment in _BEYOND_A_LAPTOP
            and endpoint is not None
            and not endpoint.startswith("https://")
        ):
            raise _FiscalSettingsError(FiscalProblem.ARCHIVE_IN_CLEAR)
        return self
```


`services/fiscal-do/src/ledger_fiscal_do/adapters/postgres/__init__.py`:

```python
"""fiscal-do's state in PostgreSQL: tables, repositories, units of work, migrations (SQLAlchemy, Alembic)."""
```


`services/fiscal-do/src/ledger_fiscal_do/adapters/postgres/tables.py`:

```python
"""fiscal-do's tables, typed for SQLAlchemy Core: what the migrations create and the repositories use.

A company's data (``fiscal_profiles``, ``sequences``, ``documents``, ``annulments``, ``archive_index``) is
under ``tenant_isolation``; rows refer to each other through ``(company_id, id)``, so none can name another
company's (PostgreSQL's referential checks bypass row-level security). Two watches are outside row-level
security on purpose, as vault's ``expiry_watch`` is (its Decision 8): ``sequence_watch`` (a range's expiry and
the smallest threshold announced) and ``pipeline`` (each document not yet decided, its state and since when).
Workers read them across companies without a scope; they hold ids, states and dates, nothing a document says.

``documents.e_ncf`` is unique per company: an e-NCF is used once, ever (DGII's rule, and the backstop of the
sequence's row lock). A document's content and issuer are ``jsonb`` (``codec``); its total and the e-NCF it
modifies are columns too, for the one query that sums credit notes.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import ClassVar, Final
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Identity,
    Index,
    MetaData,
    Numeric,
    SmallInteger,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql.schema import SchemaItem

WORD: Final = 32
ENCF: Final = 13
TAX_ID: Final = 11
KEY: Final = 512
VERSION_ID: Final = 1024
DIGEST: Final = 64
TRACK_ID: Final = 128
CODE: Final = 6
STATES: Final = (
    "'draft', 'numbered', 'signed', 'blocked_certificate', 'contingency', 'sent', 'accepted',"
    " 'accepted_conditionally', 'rejected'"
)
ENVIRONMENTS: Final = "'testecf', 'certecf', 'ecf'"
SIGNED_TOGETHER: Final = (
    "(signed_at IS NULL) = (security_code IS NULL)"
    " AND (signed_at IS NULL) = (archive_key IS NULL)"
    " AND (signed_at IS NULL) = (sha256 IS NULL)"
    " AND (signed_at IS NULL) = (summary IS NULL)"
)
"""A signature's five columns are all set, or none is."""


class FiscalTable(DeclarativeBase):
    """fiscal-do's own tables; the chassis's are ``PlatformTable``'s."""

    metadata: ClassVar[MetaData] = MetaData()


class ProfileRow(FiscalTable):
    """A company's fiscal profile: what its documents say of the issuer beyond identity's facts."""

    __tablename__: str = "fiscal_profiles"
    __table_args__: tuple[SchemaItem, ...] = (
        CheckConstraint(f"environment IN ({ENVIRONMENTS})", name="ck_fiscal_profiles_environment"),
        CheckConstraint("version > 0", name="ck_fiscal_profiles_version"),
    )

    company_id: Mapped[UUID] = mapped_column(primary_key=True)
    organization_id: Mapped[UUID] = mapped_column()
    environment: Mapped[str] = mapped_column(String(WORD))
    address: Mapped[str] = mapped_column(String(100))
    trade_name: Mapped[str | None] = mapped_column(String(150))
    branch: Mapped[str | None] = mapped_column(String(20))
    municipality: Mapped[str | None] = mapped_column(String(6))
    province: Mapped[str | None] = mapped_column(String(6))
    phones: Mapped[list[str]] = mapped_column(JSONB)
    email: Mapped[str | None] = mapped_column(String(80))
    version: Mapped[int] = mapped_column(BigInteger)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SequenceRow(FiscalTable):
    """A range of e-NCF numbers DGII authorized for one type at one emission point."""

    __tablename__: str = "sequences"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint("company_id", "id", name="uq_sequences_company"),
        CheckConstraint("ecf_type IN (31, 32, 33, 34, 41, 43, 44, 45, 46, 47)", name="ck_sequences_type"),
        CheckConstraint("first_number BETWEEN 1 AND last_number", name="ck_sequences_range"),
        CheckConstraint("last_number <= 9999999999", name="ck_sequences_last"),
        CheckConstraint("next_number BETWEEN first_number AND last_number + 1", name="ck_sequences_next"),
        CheckConstraint("version > 0", name="ck_sequences_version"),
        Index("ix_sequences_type", "company_id", "ecf_type", "emission_point_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    organization_id: Mapped[UUID] = mapped_column()
    company_id: Mapped[UUID] = mapped_column()
    ecf_type: Mapped[int] = mapped_column(SmallInteger)
    emission_point_id: Mapped[UUID] = mapped_column()
    first_number: Mapped[int] = mapped_column(BigInteger)
    last_number: Mapped[int] = mapped_column(BigInteger)
    expires_on: Mapped[date | None] = mapped_column(Date)
    next_number: Mapped[int] = mapped_column(BigInteger)
    annulled: Mapped[list[list[int]]] = mapped_column(JSONB)
    low_announced: Mapped[bool] = mapped_column(Boolean)
    version: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DocumentRow(FiscalTable):
    """A fiscal document, from numbered to DGII's final answer."""

    __tablename__: str = "documents"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint("company_id", "id", name="uq_documents_company"),
        UniqueConstraint("company_id", "e_ncf", name="uq_documents_encf"),
        ForeignKeyConstraint(
            ["company_id", "sequence_id"],
            ["sequences.company_id", "sequences.id"],
            name="fk_documents_sequence",
        ),
        CheckConstraint(f"state IN ({STATES})", name="ck_documents_state"),
        CheckConstraint(f"environment IN ({ENVIRONMENTS})", name="ck_documents_environment"),
        CheckConstraint("e_ncf ~ '^E[0-9]{12}$'", name="ck_documents_encf"),
        CheckConstraint(SIGNED_TOGETHER, name="ck_documents_signature"),
        CheckConstraint("version > 0", name="ck_documents_version"),
        Index("ix_documents_blocked", "company_id", postgresql_where=text("state = 'blocked_certificate'")),
        Index(
            "ix_documents_modified",
            "company_id",
            "modified_encf",
            postgresql_where=text("modified_encf IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    organization_id: Mapped[UUID] = mapped_column()
    company_id: Mapped[UUID] = mapped_column()
    emission_point_id: Mapped[UUID] = mapped_column()
    environment: Mapped[str] = mapped_column(String(WORD))
    ecf_type: Mapped[int] = mapped_column(SmallInteger)
    e_ncf: Mapped[str] = mapped_column(String(ENCF))
    sequence_id: Mapped[UUID] = mapped_column()
    sequence_expires_on: Mapped[date | None] = mapped_column(Date)
    state: Mapped[str] = mapped_column(String(WORD))
    blocked_from: Mapped[str | None] = mapped_column(String(WORD))
    contingency_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(BigInteger)
    issuer: Mapped[dict[str, object]] = mapped_column(JSONB)
    content: Mapped[dict[str, object]] = mapped_column(JSONB)
    total: Mapped[Decimal] = mapped_column(Numeric(20, 2))
    modified_encf: Mapped[str | None] = mapped_column(String(ENCF))
    signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    security_code: Mapped[str | None] = mapped_column(String(CODE))
    archive_key: Mapped[str | None] = mapped_column(String(KEY))
    sha256: Mapped[str | None] = mapped_column(String(DIGEST))
    summary: Mapped[bool | None] = mapped_column(Boolean)
    track_id: Mapped[str | None] = mapped_column(String(TRACK_ID))
    outcome: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AnnulmentRow(FiscalTable):
    """Unused numbers of a range withdrawn, and DGII's answer (ANECF)."""

    __tablename__: str = "annulments"
    __table_args__: tuple[SchemaItem, ...] = (
        ForeignKeyConstraint(
            ["company_id", "sequence_id"],
            ["sequences.company_id", "sequences.id"],
            name="fk_annulments_sequence",
        ),
        CheckConstraint("state IN ('requested', 'accepted', 'rejected')", name="ck_annulments_state"),
        CheckConstraint(f"environment IN ({ENVIRONMENTS})", name="ck_annulments_environment"),
        CheckConstraint("first_number BETWEEN 1 AND last_number", name="ck_annulments_range"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    organization_id: Mapped[UUID] = mapped_column()
    company_id: Mapped[UUID] = mapped_column()
    sequence_id: Mapped[UUID] = mapped_column()
    ecf_type: Mapped[int] = mapped_column(SmallInteger)
    first_number: Mapped[int] = mapped_column(BigInteger)
    last_number: Mapped[int] = mapped_column(BigInteger)
    issuer: Mapped[str] = mapped_column(String(TAX_ID))
    environment: Mapped[str] = mapped_column(String(WORD))
    state: Mapped[str] = mapped_column(String(WORD))
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[dict[str, object] | None] = mapped_column(JSONB)


class ArchiveIndexRow(FiscalTable):
    """Where an object of a document or an annulment is kept in the archive (§7: never the only copy)."""

    __tablename__: str = "archive_index"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint("company_id", "owner_id", "kind", "key", name="uq_archive_index_object"),
        CheckConstraint("kind IN ('ecf', 'rfce', 'anecf', 'answer')", name="ck_archive_index_kind"),
        Index("ix_archive_index_owner", "company_id", "owner_id", "kind"),
    )

    position: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    organization_id: Mapped[UUID] = mapped_column()
    company_id: Mapped[UUID] = mapped_column()
    owner_id: Mapped[UUID] = mapped_column()
    kind: Mapped[str] = mapped_column(String(WORD))
    key: Mapped[str] = mapped_column(String(KEY))
    version_id: Mapped[str] = mapped_column(String(VERSION_ID))
    sha256: Mapped[str] = mapped_column(String(DIGEST))
    size: Mapped[int] = mapped_column(BigInteger)
    retain_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SequenceWatchRow(FiscalTable):
    """A range with an expiry, watched across companies (outside row-level security)."""

    __tablename__: str = "sequence_watch"
    __table_args__: tuple[SchemaItem, ...] = (
        ForeignKeyConstraint(
            ["company_id", "sequence_id"],
            ["sequences.company_id", "sequences.id"],
            name="fk_sequence_watch_sequence",
        ),
        CheckConstraint("announced IS NULL OR announced IN (30, 7, 1)", name="ck_sequence_watch_announced"),
        Index("ix_sequence_watch_due", "expires_on"),
    )

    sequence_id: Mapped[UUID] = mapped_column(primary_key=True)
    organization_id: Mapped[UUID] = mapped_column()
    company_id: Mapped[UUID] = mapped_column()
    ecf_type: Mapped[int] = mapped_column(SmallInteger)
    expires_on: Mapped[date] = mapped_column(Date)
    announced: Mapped[int | None] = mapped_column(SmallInteger)


class PipelineRow(FiscalTable):
    """A document DGII has not decided yet, watched across companies (outside row-level security)."""

    __tablename__: str = "pipeline"
    __table_args__: tuple[SchemaItem, ...] = (
        ForeignKeyConstraint(
            ["company_id", "document_id"],
            ["documents.company_id", "documents.id"],
            name="fk_pipeline_document",
        ),
        CheckConstraint(f"state IN ({STATES})", name="ck_pipeline_state"),
        Index("ix_pipeline_since", "since"),
    )

    document_id: Mapped[UUID] = mapped_column(primary_key=True)
    organization_id: Mapped[UUID] = mapped_column()
    company_id: Mapped[UUID] = mapped_column()
    state: Mapped[str] = mapped_column(String(WORD))
    since: Mapped[datetime] = mapped_column(DateTime(timezone=True))
```


`services/fiscal-do/src/ledger_fiscal_do/adapters/postgres/migrate.py`:

```python
"""fiscal-do's schema brought to this version's: ``ledger_platform.migrations`` with fiscal-do's revisions."""

from typing import Final

from ledger_fiscal_do.settings import FiscalSettings
from ledger_platform.migrations import BASE, HEAD, Migrations
lazy from ledger_platform.database import Database

SCRIPTS: Final = "ledger_fiscal_do.adapters.postgres:migrations"
MIGRATIONS: Final = Migrations(SCRIPTS, owner="fiscal-do")


def upgrade(url: str, revision: str = HEAD) -> None:
    """Bring fiscal-do's database at ``url`` up to ``revision``."""
    MIGRATIONS.upgrade(url, revision)


def downgrade(url: str, revision: str = BASE) -> None:
    """Take fiscal-do's database at ``url`` down to ``revision``."""
    MIGRATIONS.downgrade(url, revision)


def head() -> str:
    """The revision fiscal-do's migrations end at."""
    return MIGRATIONS.head()


async def require_head(database: Database, expected: str) -> None:
    """fiscal-do's readiness check: the database is at ``expected``."""
    await MIGRATIONS.require_head(database, expected)


def main() -> int:
    """``ledger-fiscal-do-migrate``: bring the database of ``LEDGER_DATABASE_URL`` to this schema."""
    upgrade(FiscalSettings().database_url.get_secret_value())
    return 0
```


`services/fiscal-do/src/ledger_fiscal_do/adapters/postgres/migrations/__init__.py`:

```python
"""fiscal-do's Alembic environment and migration scripts."""
```


`services/fiscal-do/src/ledger_fiscal_do/adapters/postgres/migrations/env.py`:

```python
"""Alembic's entry point for fiscal-do: the platform's runner with fiscal-do's tables."""

from ledger_fiscal_do.adapters.postgres.tables import FiscalTable
from ledger_platform.migrations import run_migrations

run_migrations(FiscalTable.metadata)
```


`services/fiscal-do/src/ledger_fiscal_do/adapters/postgres/migrations/versions/__init__.py`:

```python
"""fiscal-do's migration scripts, one per revision."""
```


`services/fiscal-do/src/ledger_fiscal_do/adapters/postgres/migrations/versions/r0001_fiscal_do.py`:

```python
"""Revision 0001, from nothing: fiscal-do's first schema.

The chassis's tables (as ``ledger_platform`` declares them at this revision), then fiscal profiles, ranges,
documents, annulments, the archive's index and the two watches; a company's data under row-level security,
the watches outside it (see ``tables``).
"""

from typing import Final
lazy from collections.abc import Sequence
lazy from datetime import datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

from ledger_platform.database import tenant_isolation
from ledger_platform.schema import CHASSIS_POLICIES

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEWEST_FIRST: Final = (
    "idempotency_keys",
    "inbox",
    "outbox",
    "pipeline",
    "sequence_watch",
    "archive_index",
    "annulments",
    "documents",
    "sequences",
    "fiscal_profiles",
)
_STATES: Final = (
    "'draft', 'numbered', 'signed', 'blocked_certificate', 'contingency', 'sent', 'accepted',"
    " 'accepted_conditionally', 'rejected'"
)
_ENVIRONMENTS: Final = "'testecf', 'certecf', 'ecf'"
_SIGNED_TOGETHER: Final = (
    "(signed_at IS NULL) = (security_code IS NULL)"
    " AND (signed_at IS NULL) = (archive_key IS NULL)"
    " AND (signed_at IS NULL) = (sha256 IS NULL)"
    " AND (signed_at IS NULL) = (summary IS NULL)"
)


def _apply(statements: Sequence[str]) -> None:
    for statement in statements:
        op.execute(statement)


def _moment(name: str, *, nullable: bool = False) -> sa.Column[datetime]:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def _chassis() -> None:
    """The platform's tables and their row-level security, spelled out as every service's first revision."""
    op.create_table(
        "outbox",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("subject", sa.String(), nullable=False),
        sa.Column("headers", JSONB(), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        _moment("created_at"),
        sa.Column("stored_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "next_attempt_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_outbox_due", "outbox", [sa.text("(attempts > 0)"), "created_at", "id", "next_attempt_at"]
    )
    op.create_index("ix_outbox_stored_at", "outbox", ["stored_at"])
    op.create_table(
        "inbox",
        sa.Column("consumer", sa.String(), primary_key=True),
        sa.Column("event_id", sa.Uuid(), primary_key=True),
        _moment("processed_at"),
    )
    op.create_table(
        "idempotency_keys",
        sa.Column("organization_id", sa.Uuid(), primary_key=True),
        sa.Column("subject", sa.String(), primary_key=True),
        sa.Column("key", sa.String(), primary_key=True),
        sa.Column("fingerprint", sa.LargeBinary(), nullable=False),
        sa.Column("status", sa.Integer(), nullable=True),
        sa.Column("headers", JSONB(), nullable=True),
        sa.Column("body", sa.LargeBinary(), nullable=True),
        _moment("expires_at"),
    )
    op.create_index("ix_idempotency_keys_expiry", "idempotency_keys", ["organization_id", "expires_at"])
    _apply(CHASSIS_POLICIES)


def _profiles() -> None:
    profiles = op.create_table(
        "fiscal_profiles",
        sa.Column("company_id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("environment", sa.String(32), nullable=False),
        sa.Column("address", sa.String(100), nullable=False),
        sa.Column("trade_name", sa.String(150), nullable=True),
        sa.Column("branch", sa.String(20), nullable=True),
        sa.Column("municipality", sa.String(6), nullable=True),
        sa.Column("province", sa.String(6), nullable=True),
        sa.Column("phones", JSONB(), nullable=False),
        sa.Column("email", sa.String(80), nullable=True),
        sa.Column("version", sa.BigInteger(), nullable=False),
        _moment("updated_at"),
        sa.CheckConstraint(f"environment IN ({_ENVIRONMENTS})", name="ck_fiscal_profiles_environment"),
        sa.CheckConstraint("version > 0", name="ck_fiscal_profiles_version"),
    )
    _apply(tenant_isolation(profiles))


def _sequences() -> None:
    sequences = op.create_table(
        "sequences",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("ecf_type", sa.SmallInteger(), nullable=False),
        sa.Column("emission_point_id", sa.Uuid(), nullable=False),
        sa.Column("first_number", sa.BigInteger(), nullable=False),
        sa.Column("last_number", sa.BigInteger(), nullable=False),
        sa.Column("expires_on", sa.Date(), nullable=True),
        sa.Column("next_number", sa.BigInteger(), nullable=False),
        sa.Column("annulled", JSONB(), nullable=False),
        sa.Column("low_announced", sa.Boolean(), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        _moment("created_at"),
        sa.UniqueConstraint("company_id", "id", name="uq_sequences_company"),
        sa.CheckConstraint("ecf_type IN (31, 32, 33, 34, 41, 43, 44, 45, 46, 47)", name="ck_sequences_type"),
        sa.CheckConstraint("first_number BETWEEN 1 AND last_number", name="ck_sequences_range"),
        sa.CheckConstraint("last_number <= 9999999999", name="ck_sequences_last"),
        sa.CheckConstraint("next_number BETWEEN first_number AND last_number + 1", name="ck_sequences_next"),
        sa.CheckConstraint("version > 0", name="ck_sequences_version"),
    )
    op.create_index("ix_sequences_type", "sequences", ["company_id", "ecf_type", "emission_point_id"])
    _apply(tenant_isolation(sequences))


def _documents() -> None:
    documents = op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("emission_point_id", sa.Uuid(), nullable=False),
        sa.Column("environment", sa.String(32), nullable=False),
        sa.Column("ecf_type", sa.SmallInteger(), nullable=False),
        sa.Column("e_ncf", sa.String(13), nullable=False),
        sa.Column("sequence_id", sa.Uuid(), nullable=False),
        sa.Column("sequence_expires_on", sa.Date(), nullable=True),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("blocked_from", sa.String(32), nullable=True),
        _moment("contingency_since", nullable=True),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("issuer", JSONB(), nullable=False),
        sa.Column("content", JSONB(), nullable=False),
        sa.Column("total", sa.Numeric(20, 2), nullable=False),
        sa.Column("modified_encf", sa.String(13), nullable=True),
        _moment("signed_at", nullable=True),
        sa.Column("security_code", sa.String(6), nullable=True),
        sa.Column("archive_key", sa.String(512), nullable=True),
        sa.Column("sha256", sa.String(64), nullable=True),
        sa.Column("summary", sa.Boolean(), nullable=True),
        sa.Column("track_id", sa.String(128), nullable=True),
        sa.Column("outcome", JSONB(), nullable=True),
        _moment("created_at"),
        _moment("updated_at"),
        sa.UniqueConstraint("company_id", "id", name="uq_documents_company"),
        sa.UniqueConstraint("company_id", "e_ncf", name="uq_documents_encf"),
        sa.ForeignKeyConstraint(
            ["company_id", "sequence_id"],
            ["sequences.company_id", "sequences.id"],
            name="fk_documents_sequence",
        ),
        sa.CheckConstraint(f"state IN ({_STATES})", name="ck_documents_state"),
        sa.CheckConstraint(f"environment IN ({_ENVIRONMENTS})", name="ck_documents_environment"),
        sa.CheckConstraint("e_ncf ~ '^E[0-9]{12}$'", name="ck_documents_encf"),
        sa.CheckConstraint(_SIGNED_TOGETHER, name="ck_documents_signature"),
        sa.CheckConstraint("version > 0", name="ck_documents_version"),
    )
    op.create_index(
        "ix_documents_blocked",
        "documents",
        ["company_id"],
        postgresql_where=sa.text("state = 'blocked_certificate'"),
    )
    op.create_index(
        "ix_documents_modified",
        "documents",
        ["company_id", "modified_encf"],
        postgresql_where=sa.text("modified_encf IS NOT NULL"),
    )
    _apply(tenant_isolation(documents))


def _annulments() -> None:
    annulments = op.create_table(
        "annulments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("sequence_id", sa.Uuid(), nullable=False),
        sa.Column("ecf_type", sa.SmallInteger(), nullable=False),
        sa.Column("first_number", sa.BigInteger(), nullable=False),
        sa.Column("last_number", sa.BigInteger(), nullable=False),
        sa.Column("issuer", sa.String(11), nullable=False),
        sa.Column("environment", sa.String(32), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        _moment("requested_at"),
        sa.Column("outcome", JSONB(), nullable=True),
        sa.ForeignKeyConstraint(
            ["company_id", "sequence_id"],
            ["sequences.company_id", "sequences.id"],
            name="fk_annulments_sequence",
        ),
        sa.CheckConstraint("state IN ('requested', 'accepted', 'rejected')", name="ck_annulments_state"),
        sa.CheckConstraint(f"environment IN ({_ENVIRONMENTS})", name="ck_annulments_environment"),
        sa.CheckConstraint("first_number BETWEEN 1 AND last_number", name="ck_annulments_range"),
    )
    _apply(tenant_isolation(annulments))


def _archive_index() -> None:
    index = op.create_table(
        "archive_index",
        sa.Column("position", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("key", sa.String(512), nullable=False),
        sa.Column("version_id", sa.String(1024), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        _moment("retain_until"),
        sa.UniqueConstraint("company_id", "owner_id", "kind", "key", name="uq_archive_index_object"),
        sa.CheckConstraint("kind IN ('ecf', 'rfce', 'anecf', 'answer')", name="ck_archive_index_kind"),
    )
    op.create_index("ix_archive_index_owner", "archive_index", ["company_id", "owner_id", "kind"])
    _apply(tenant_isolation(index))


def _watches() -> None:
    op.create_table(
        "sequence_watch",
        sa.Column("sequence_id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("ecf_type", sa.SmallInteger(), nullable=False),
        sa.Column("expires_on", sa.Date(), nullable=False),
        sa.Column("announced", sa.SmallInteger(), nullable=True),
        sa.ForeignKeyConstraint(
            ["company_id", "sequence_id"],
            ["sequences.company_id", "sequences.id"],
            name="fk_sequence_watch_sequence",
        ),
        sa.CheckConstraint(
            "announced IS NULL OR announced IN (30, 7, 1)", name="ck_sequence_watch_announced"
        ),
    )
    op.create_index("ix_sequence_watch_due", "sequence_watch", ["expires_on"])
    op.create_table(
        "pipeline",
        sa.Column("document_id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        _moment("since"),
        sa.ForeignKeyConstraint(
            ["company_id", "document_id"],
            ["documents.company_id", "documents.id"],
            name="fk_pipeline_document",
        ),
        sa.CheckConstraint(f"state IN ({_STATES})", name="ck_pipeline_state"),
    )
    op.create_index("ix_pipeline_since", "pipeline", ["since"])


def upgrade() -> None:
    """Create the chassis's tables and fiscal-do's, a company's data under row-level security."""
    _chassis()
    _profiles()
    _sequences()
    _documents()
    _annulments()
    _archive_index()
    _watches()


def downgrade() -> None:
    """Drop every table this revision created (their policies go with them)."""
    for table in _NEWEST_FIRST:
        op.drop_table(table)
```


- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest services/fiscal-do/tests/test_settings.py services/fiscal-do/tests/test_migrations.py -q --no-cov`
Expected: PASS. (The readiness check's test takes a fresh database up and back to base before asking: Decision 18.)

- [ ] **Step 6: Gate**

Run: `uv run poe fmt && uv run poe check`
Expected: every step passes, coverage 100 % (the revision and `env.py` measured through `source`).

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock services/fiscal-do
git commit -m "feat(fiscal-do): settings that keep production on DGII, schema with tenant isolation and two watches

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: fiscal-do's state in PostgreSQL: repositories, units, the outbox's events, the watches

**Files:**
- Create: `services/fiscal-do/src/ledger_fiscal_do/adapters/postgres/codec.py`, `adapters/postgres/repositories.py`, `adapters/postgres/units.py`, `adapters/events.py`
- Modify: `services/fiscal-do/pyproject.toml`
- Test: `services/fiscal-do/tests/test_codec.py`, `services/fiscal-do/tests/test_events.py`, `services/fiscal-do/tests/test_repositories.py`

**Interfaces:**
- Consumes: the ports (Task 4), the tables (Task 9), `Database.transaction(scope)`, `UnitOfWork.connection`, `UnitOfWork.scope`, `Outbox.add(unit, envelope, *, subject)`, `Envelope.wrap`, `StreamSpec`, `page_of`; `ledger.events.fiscal_do.v1` and `ledger_contracts.fiscal_do` (Task 2); `ledger_kernel.clock` (`Clock`, `utc_now`).
- Produces: `adapters.postgres.codec`: `VERSION`, `Json`, `Tree`, `UnreadableStoredDocumentError`, `issuer_json`/`issuer_of`, `content_json`/`content_of`, `outcome_json`/`outcome_of`, `stored`. `adapters.postgres.repositories`: `PgDocuments`, `PgSequences`, `PgAnnulments`, `PgProfiles`, `PgArchiveIndex` (each `(connection, tenant)`), `PgExpiringRanges(database)`, `PgPipeline(database)`. `adapters.postgres.units`: `UnscopedUnitError`, `bind(unit, outbox, *, clock=utc_now) -> FiscalUnit`, `PgUnits(database, outbox, *, clock=utc_now)`. `adapters.events`: `FISCAL_DO` (`StreamSpec`), `SOURCE = "fiscal-do"`, `outgoing(event) -> tuple[Message, str]`, `OutboxEvents(unit, outbox, *, clock)`.

Re-check before starting: `UnitOfWork.scope`'s type (`Tenant | OrganizationId | None`), `Outbox`'s constructor (`tracing=`, `registry=`) and `Envelope.from_headers(headers, data)` as the platform at `5ca3de2` has them; the `StreamSpec` fields.

- [ ] **Step 1: Dependencies**

`services/fiscal-do/pyproject.toml` — `dependencies` gains `"protobuf>=7.36.2"`, and the deptry map gains `"protobuf" = "google"`. Run: `uv lock && uv sync`.

- [ ] **Step 2: Write the failing tests**

`services/fiscal-do/tests/test_codec.py`:

```python
from dataclasses import replace

import pytest

from ledger_fiscal_do.adapters.postgres import codec
from ledger_fiscal_do.adapters.postgres.codec import UnreadableStoredDocumentError
from ledger_fiscal_do.domain.content import Buyer, Withholding
from ledger_fiscal_do.domain.verdict import DgiiMessage, Outcome, Verdict
from ledger_fiscal_do.domain.vocabulary import EcfType
from ledger_kernel.countries.do import TaxId

from samples import CONTENTS, ISSUER, NOW, REFERENCE, dop, line


@pytest.mark.parametrize("ecf_type", list(EcfType))
def test_every_sample_content_survives_the_round_trip(ecf_type: EcfType) -> None:
    content = CONTENTS[ecf_type]
    assert codec.content_of(codec.stored(codec.content_json(content))) == content


def test_every_optional_field_survives_the_round_trip() -> None:
    full = replace(
        CONTENTS[EcfType.CREDIT_NOTE],
        buyer=Buyer(
            tax_id=TaxId.parse("131880738"),
            foreign_id="P-1",
            name="Comprador",
            contact="Ana",
            email="ana@example.com",
            address="C/ 1",
            municipality="010101",
            province="010000",
            country="Estados Unidos",
        ),
        reference=replace(REFERENCE, reason="Devolución", other_taxpayer=TaxId.parse("131880738")),
        lines=(
            replace(
                line(1, discount=dop("1.00")),
                description="Detalle",
                unit=43,
                withholding=Withholding(itbis=dop("18.00"), isr=dop("10.00"), perception=True),
            ),
        ),
    )
    assert codec.content_of(codec.stored(codec.content_json(full))) == full
    assert codec.issuer_of(codec.stored(codec.issuer_json(ISSUER))) == ISSUER
    outcome = Outcome(verdict=Verdict.REJECTED, messages=(DgiiMessage("613", "No existe"),), decided_at=NOW)
    assert codec.outcome_of(codec.stored(codec.outcome_json(outcome))) == outcome


@pytest.mark.parametrize(
    "change",
    [
        {"v": 2},
        {"lines": "not a list"},
        {"lines": ["not an object"]},
        {"ecf_type": "31"},
        {"ecf_type": True},
        {"issued_on": 20261012},
        {"prices_include_itbis": "yes"},
        {"lines": [{"number": 1}]},
    ],
)
def test_json_this_version_did_not_write_is_refused(change: dict[str, codec.Json]) -> None:
    tree = codec.content_json(CONTENTS[EcfType.CREDIT_INVOICE]) | change
    with pytest.raises(UnreadableStoredDocumentError):
        codec.content_of(tree)


def test_a_withholding_without_its_perception_flag_is_refused() -> None:
    tree = codec.content_json(
        replace(CONTENTS[EcfType.CREDIT_INVOICE], lines=(replace(line(1), withholding=Withholding()),))
    )
    lines = tree["lines"]
    assert isinstance(lines, list)
    first = lines[0]
    assert isinstance(first, dict)
    withholding = first["withholding"]
    assert isinstance(withholding, dict)
    withholding["perception"] = "no"
    with pytest.raises(UnreadableStoredDocumentError):
        codec.content_of(tree)
```


`services/fiscal-do/tests/test_events.py`:

```python
from datetime import date
from typing import Final

import pytest

import ledger.events.fiscal_do.v1.events_pb2 as wire
from ledger_contracts import fiscal_do
from ledger_contracts.envelope import Envelope
from ledger_fiscal_do.adapters.events import FISCAL_DO, SOURCE, outgoing
from ledger_fiscal_do.domain.encf import Encf
from ledger_fiscal_do.domain.events import (
    AnnulmentDecided,
    AnnulmentRequested,
    DocumentAccepted,
    DocumentConditionallyAccepted,
    DocumentNumbered,
    DocumentRejected,
    SequenceExpiring,
    SequenceLow,
)
from ledger_fiscal_do.domain.ids import AnnulmentId, DocumentId, SequenceId
from ledger_fiscal_do.domain.verdict import DgiiMessage, Verdict
from ledger_fiscal_do.domain.vocabulary import DgiiEnvironment, EcfType
from ledger_kernel.ids import new_id
lazy from ledger_fiscal_do.domain.events import FiscalEvent

from samples import TENANT, dop

ENCF: Final = Encf(EcfType.CREDIT_INVOICE, 1)
MESSAGES: Final = (DgiiMessage("613", "El RNC del comprador no existe"),)
DOCUMENT: Final = DocumentId(new_id())
SEQUENCE: Final = SequenceId(new_id())
ANNULMENT: Final = AnnulmentId(new_id())
EVENTS: Final[tuple[tuple[FiscalEvent, str], ...]] = (
    (
        DocumentNumbered(
            tenant=TENANT,
            document=DOCUMENT,
            encf=ENCF,
            version=1,
            total=dop("1180.00"),
            environment=DgiiEnvironment.CERTIFICATION,
        ),
        "fiscal.document.numbered",
    ),
    (DocumentAccepted(tenant=TENANT, document=DOCUMENT, encf=ENCF, version=4), "fiscal.document.accepted"),
    (
        DocumentConditionallyAccepted(
            tenant=TENANT, document=DOCUMENT, encf=ENCF, version=4, messages=MESSAGES
        ),
        "fiscal.document.conditionally_accepted",
    ),
    (
        DocumentRejected(tenant=TENANT, document=DOCUMENT, encf=ENCF, version=4, messages=MESSAGES),
        "fiscal.document.rejected",
    ),
    (
        SequenceLow(
            tenant=TENANT, sequence=SEQUENCE, ecf_type=EcfType.CREDIT_INVOICE, remaining=20, version=81
        ),
        "fiscal.sequence.low",
    ),
    (
        SequenceExpiring(
            tenant=TENANT,
            sequence=SEQUENCE,
            ecf_type=EcfType.CREDIT_INVOICE,
            expires_on=date(2028, 12, 31),
            days_left=7,
        ),
        "fiscal.sequence.expiring",
    ),
    (
        AnnulmentRequested(
            tenant=TENANT,
            annulment=ANNULMENT,
            sequence=SEQUENCE,
            first=ENCF,
            last=Encf(EcfType.CREDIT_INVOICE, 9),
        ),
        "fiscal.annulment.requested",
    ),
    (
        AnnulmentDecided(tenant=TENANT, annulment=ANNULMENT, verdict=Verdict.REJECTED, messages=MESSAGES),
        "fiscal.annulment.decided",
    ),
)


@pytest.mark.parametrize(("event", "subject"), EVENTS, ids=[subject for _, subject in EVENTS])
def test_every_event_has_its_subject_inside_the_stream(event: FiscalEvent, subject: str) -> None:
    message, found = outgoing(event)
    assert found == subject
    assert any(found.startswith(tree.removesuffix(">")) for tree in FISCAL_DO.subjects)
    envelope = Envelope.wrap(message, source=SOURCE, tenant=TENANT)
    assert envelope.type == f"ledger.events.fiscal_do.v1.{type(event).__name__}"
    assert envelope.tenant == TENANT


def test_the_wire_says_what_the_domain_said() -> None:
    expiring, _ = outgoing(EVENTS[5][0])
    assert isinstance(expiring, wire.SequenceExpiring)
    assert expiring.expires_on.ToDatetime().date() == date(2028, 12, 31)
    decided, _ = outgoing(EVENTS[7][0])
    assert isinstance(decided, wire.AnnulmentDecided)
    assert (decided.verdict, decided.messages[0].code) == (wire.VERDICT_REJECTED, "613")
    assert FISCAL_DO.subjects == fiscal_do.SUBJECTS
```


`services/fiscal-do/tests/test_repositories.py`:

```python
import asyncio
from dataclasses import replace
from datetime import date, timedelta
from typing import Final, final

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

import ledger.events.fiscal_do.v1.events_pb2 as wire
from ledger_contracts.envelope import Envelope
from ledger_fiscal_do.adapters.postgres.repositories import PgExpiringRanges, PgPipeline
from ledger_fiscal_do.adapters.postgres.units import PgUnits, UnscopedUnitError, bind
from ledger_fiscal_do.adapters.xml.documents import DgiiXml
from ledger_fiscal_do.application import profiles, queries, ranges
from ledger_fiscal_do.application.annulling import Annulling
from ledger_fiscal_do.application.emission import Emission, Step
from ledger_fiscal_do.application.issuing import IssueRequest, issue
from ledger_fiscal_do.application.ports import Archived, ArchiveKind
from ledger_fiscal_do.domain.annulment import AnnulmentNotFoundError, AnnulmentState
from ledger_fiscal_do.domain.document import DocumentNotFoundError, DocumentState
from ledger_fiscal_do.domain.encf import Encf
from ledger_fiscal_do.domain.ids import AnnulmentId, DocumentId, SequenceId
from ledger_fiscal_do.domain.sequence import NumberRange
from ledger_fiscal_do.domain.vocabulary import EcfType
from ledger_kernel.ids import CompanyId, OrganizationId, Tenant, new_id
from ledger_kernel.money import Currency, Money
from ledger_kernel.pagination import PageRequest, decode_cursor
from ledger_platform.outbox import OutboxRecord
lazy from ledger_fiscal_do.domain.content import Content
lazy from ledger_fiscal_do.domain.document import FiscalDocument
lazy from ledger_fiscal_do.domain.sequence import EncfSequence
lazy from ledger_platform.database import Database
lazy from ledger_platform.outbox import Outbox

from fakes import Clock, FakeArchive, FakeDgii, FakeSigner, accepted
from samples import CONTENTS, NOW, POINT, REFERENCE, TENANT, expiry, line
from world import COMPANY, PROFILE

OTHER: Final = Tenant(OrganizationId(new_id()), CompanyId(new_id()))
HUNDRED: Final = NumberRange(1, 100)


@final
class Stack:
    """fiscal-do's use cases over PostgreSQL, with vault, the archive and DGII faked."""

    def __init__(self, database: Database, outbox: Outbox) -> None:
        self.database = database
        self.clock = Clock(NOW)
        self.units = PgUnits(database, outbox, clock=self.clock)
        self.dgii = FakeDgii()
        archive, signer = FakeArchive(), FakeSigner()
        self.emission = Emission(
            units=self.units, signer=signer, xml=DgiiXml(), archive=archive, dgii=self.dgii, clock=self.clock
        )
        self.annulling = Annulling(
            units=self.units, signer=signer, xml=DgiiXml(), archive=archive, dgii=self.dgii, clock=self.clock
        )

    async def profiled(self, tenant: Tenant = TENANT) -> None:
        async with self.units.unit(tenant) as unit:
            await profiles.put_profile(unit, replace(PROFILE, tenant=tenant), replaces=None)

    async def register(
        self, ecf_type: EcfType, numbers: NumberRange = HUNDRED, tenant: Tenant = TENANT
    ) -> EncfSequence:
        request = ranges.RangeRequest(
            ecf_type=ecf_type, emission_point=POINT, numbers=numbers, expires_on=expiry(ecf_type)
        )
        async with self.units.unit(tenant) as unit:
            return await ranges.register(unit, request)

    async def issue(self, content: Content, tenant: Tenant = TENANT) -> FiscalDocument:
        async with self.units.unit(tenant) as unit:
            request = IssueRequest(emission_point=POINT, content=content)
            return await issue(unit, request, company=COMPANY, xml=DgiiXml())

    async def read(self, document: DocumentId, tenant: Tenant = TENANT) -> FiscalDocument | None:
        async with self.units.unit(tenant) as unit:
            return await unit.documents.get(document)


@pytest.fixture
async def stack(database: Database, outbox: Outbox) -> Stack:
    created = Stack(database, outbox)
    await created.profiled()
    return created


@pytest.mark.parametrize("ecf_type", list(EcfType))
async def test_every_type_of_document_reads_back_as_it_was_issued(stack: Stack, ecf_type: EcfType) -> None:
    await stack.register(ecf_type)
    if ecf_type in {EcfType.DEBIT_NOTE, EcfType.CREDIT_NOTE}:
        await stack.register(EcfType.CREDIT_INVOICE)
        original = await stack.issue(CONTENTS[EcfType.CREDIT_INVOICE])
        content = replace(CONTENTS[ecf_type], reference=replace(REFERENCE, modified=str(original.encf)))
    else:
        content = CONTENTS[ecf_type]
    issued = await stack.issue(content)
    assert await stack.read(issued.id) == issued
    async with stack.units.unit(TENANT) as unit:
        assert await unit.documents.by_encf(issued.encf) == issued


async def test_a_document_is_signed_sent_and_decided_in_postgresql(stack: Stack) -> None:
    await stack.register(EcfType.CREDIT_INVOICE)
    issued = await stack.issue(CONTENTS[EcfType.CREDIT_INVOICE])
    assert [watched.state for watched in await PgPipeline(stack.database).oldest(10)] == [
        DocumentState.NUMBERED
    ]
    assert await stack.emission.sign(TENANT, issued.id) is Step.SIGNED
    assert await stack.emission.submit(TENANT, issued.id) is Step.SENT
    stack.dgii.results.append(accepted())
    assert await stack.emission.poll(TENANT, issued.id) is Step.DECIDED
    decided = await stack.read(issued.id)
    assert decided is not None
    assert decided.state is DocumentState.ACCEPTED
    assert decided.signature is not None
    assert decided.outcome is not None
    assert await PgPipeline(stack.database).oldest(10) == ()
    async with stack.units.unit(TENANT) as unit:
        kept = await unit.archive.find(issued.id, ArchiveKind.ECF)
        assert kept is not None
        assert kept.key == decided.signature.archive_key
        assert await unit.archive.find(issued.id, ArchiveKind.SUMMARY) is None


async def test_two_documents_issued_at_once_take_two_numbers(stack: Stack) -> None:
    await stack.register(EcfType.CREDIT_INVOICE)
    invoice = CONTENTS[EcfType.CREDIT_INVOICE]
    issued = await asyncio.gather(*(stack.issue(invoice) for _ in range(5)))
    assert sorted(document.encf.number for document in issued) == [1, 2, 3, 4, 5]


async def test_a_company_sees_none_of_another_s_documents_or_ranges(stack: Stack) -> None:
    sequence = await stack.register(EcfType.CREDIT_INVOICE)
    issued = await stack.issue(CONTENTS[EcfType.CREDIT_INVOICE])
    async with stack.units.unit(OTHER) as unit:
        assert await unit.documents.get(issued.id) is None
        assert await unit.documents.by_encf(issued.encf) is None
        assert await unit.sequences.get(sequence.id) is None
        assert await unit.profiles.get() is None
        assert (await unit.documents.page(PageRequest())).items == ()
        with pytest.raises(DocumentNotFoundError):
            await unit.documents.locked(issued.id)
    async with stack.database.transaction(OTHER) as transaction:
        count = await transaction.connection.execute(text("SELECT count(*) FROM documents"))
        assert count.scalar_one() == 0


async def test_numbering_is_announced_through_the_outbox(stack: Stack) -> None:
    await stack.register(EcfType.CREDIT_INVOICE, NumberRange(1, 5))
    for _ in range(4):
        await stack.issue(CONTENTS[EcfType.CREDIT_INVOICE])
    async with stack.database.transaction(None) as transaction:
        rows = (
            await transaction.connection.execute(
                select(OutboxRecord.subject, OutboxRecord.headers, OutboxRecord.data)
            )
        ).all()
    subjects = [subject for subject, _, _ in rows]
    assert subjects.count("fiscal.document.numbered") == 4
    assert subjects.count("fiscal.sequence.low") == 1
    _, headers, data = next(row for row in rows if row[0] == "fiscal.document.numbered")
    numbered = Envelope.from_headers(headers, data).unwrap(wire.DocumentNumbered)
    assert (numbered.encf[:3], numbered.total.currency, numbered.environment) == (
        "E31",
        "DOP",
        wire.DGII_ENVIRONMENT_PRECERTIFICATION,
    )


async def test_credit_notes_are_summed_and_blocked_documents_listed(stack: Stack) -> None:
    await stack.register(EcfType.CREDIT_INVOICE)
    await stack.register(EcfType.CREDIT_NOTE)
    invoice = await stack.issue(CONTENTS[EcfType.CREDIT_INVOICE])
    reference = replace(REFERENCE, modified=str(invoice.encf))
    note = await stack.issue(replace(CONTENTS[EcfType.CREDIT_NOTE], reference=reference, lines=(line(1),)))
    async with stack.units.unit(TENANT) as unit:
        assert await unit.documents.credited(invoice.encf) == note.totals.total
        assert await unit.documents.credited(Encf(EcfType.CREDIT_INVOICE, 99)) == Money.zero(Currency.DOP)
        locked = await unit.documents.locked(note.id)
        await unit.documents.update(locked.blocked(now=unit.now))
    async with stack.units.unit(TENANT) as unit:
        assert await unit.documents.blocked() == (note.id,)
        first = await unit.documents.page(PageRequest(size=1))
        assert first.next_cursor is not None
        assert len(first.items) == 1


async def test_ranges_never_overlap_and_their_expiry_is_announced_once(stack: Stack) -> None:
    sequence = await stack.register(EcfType.CREDIT_INVOICE)
    await stack.register(EcfType.CREDIT_INVOICE, NumberRange(101, 200))
    await stack.profiled(OTHER)
    await stack.register(EcfType.CREDIT_INVOICE, tenant=OTHER)
    candidates = PgExpiringRanges(stack.database)
    due = await candidates.due(date(2027, 12, 31), 10)
    assert len(due) == 3
    async with stack.units.unit(TENANT) as unit:
        assert await unit.sequences.claim_expiry(sequence.id, 30)
        assert not await unit.sequences.claim_expiry(sequence.id, 30)
        assert await unit.sequences.claim_expiry(sequence.id, 7)
        assert len(await unit.sequences.of_type(EcfType.CREDIT_INVOICE)) == 2
        assert len((await unit.sequences.page(PageRequest(size=1))).items) == 1
        assert await unit.sequences.locked(SequenceId(new_id())) is None
    announced = next(
        watched for watched in await candidates.due(date(2027, 12, 31), 10) if watched.sequence == sequence.id
    )
    assert announced.announced == 7


async def test_an_exhausted_range_is_watched_no_more(stack: Stack) -> None:
    await stack.register(EcfType.CREDIT_INVOICE, NumberRange(1, 1))
    await stack.issue(CONTENTS[EcfType.CREDIT_INVOICE])
    assert await PgExpiringRanges(stack.database).due(date(2030, 1, 1), 10) == ()


async def test_an_annulment_reads_back_and_is_decided_once(stack: Stack) -> None:
    sequence = await stack.register(EcfType.CREDIT_INVOICE)
    async with stack.units.unit(TENANT) as unit:
        annulment = await ranges.annul(unit, sequence.id, NumberRange(51, 100), company=COMPANY)
    async with stack.units.unit(TENANT) as unit:
        assert await unit.annulments.get(annulment.id) == annulment
        assert (await queries.sequence(unit, sequence.id)).available == 50
        with pytest.raises(AnnulmentNotFoundError):
            await unit.annulments.locked(AnnulmentId(new_id()))
    assert await stack.annulling.annul(TENANT, annulment.id) is Step.DECIDED
    async with stack.units.unit(TENANT) as unit:
        decided = await unit.annulments.get(annulment.id)
    assert decided is not None
    assert decided.state is AnnulmentState.ACCEPTED
    assert decided.outcome is not None


async def test_a_profile_is_replaced_only_at_the_version_read(stack: Stack) -> None:
    async with stack.units.unit(TENANT) as unit:
        current = await unit.profiles.get()
        assert current == PROFILE
        assert not await unit.profiles.put(replace(PROFILE, version=1))
        assert await unit.profiles.put(
            replace(PROFILE, version=2, branch="Sucursal 2", phones=("809-555-0101",))
        )
    async with stack.units.unit(TENANT) as unit:
        assert await unit.profiles.get() == replace(
            PROFILE, version=2, branch="Sucursal 2", phones=("809-555-0101",)
        )


async def test_the_archive_index_keeps_each_object_once_and_finds_the_latest(stack: Stack) -> None:
    owner = DocumentId(new_id())
    first = Archived(key="a", version="1", sha256="0" * 64, size=1, retain_until=NOW + timedelta(days=3653))
    second = replace(first, key="b", version="2")
    async with stack.units.unit(TENANT) as unit:
        await unit.archive.add(owner, ArchiveKind.ANSWER, first)
        await unit.archive.add(owner, ArchiveKind.ANSWER, second)
        await unit.archive.add(owner, ArchiveKind.ANSWER, first)
        assert await unit.archive.find(owner, ArchiveKind.ANSWER) == second
        assert await unit.archive.find(owner, ArchiveKind.ECF) is None


async def test_the_watches_are_outside_row_level_security_and_documents_are_not(stack: Stack) -> None:
    await stack.register(EcfType.CREDIT_INVOICE)
    await stack.issue(CONTENTS[EcfType.CREDIT_INVOICE])
    assert len(await PgPipeline(stack.database).oldest(10)) == 1
    async with stack.database.transaction(None) as transaction:
        assert (
            await transaction.connection.execute(text("SELECT count(*) FROM documents"))
        ).scalar_one() == 0
    async with stack.database.transaction(OTHER) as transaction:
        moved = await transaction.connection.execute(
            text("UPDATE sequences SET company_id = :company"), {"company": OTHER.company}
        )
        assert moved.rowcount == 0
    with pytest.raises(DBAPIError):
        await smuggle_profile(stack.database)


SMUGGLED_ROW: Final = (
    "INSERT INTO fiscal_profiles VALUES (:company, :organization, 'testecf', 'x', NULL, NULL, NULL, NULL,"
    " '[]', NULL, 1, now())"
)
SMUGGLED: Final = text(SMUGGLED_ROW)


async def smuggle_profile(database: Database) -> None:
    """Write a profile of ``TENANT`` from a transaction scoped to ``OTHER``: row-level security refuses it."""
    async with database.transaction(OTHER) as transaction:
        await transaction.connection.execute(
            SMUGGLED, {"company": TENANT.company, "organization": TENANT.organization}
        )


async def test_a_unit_needs_a_company(database: Database, outbox: Outbox) -> None:
    async with database.transaction(None) as transaction:
        with pytest.raises(UnscopedUnitError):
            bind(transaction, outbox)


async def test_pages_continue_after_their_cursor_and_a_range_without_expiry_is_not_watched(
    stack: Stack,
) -> None:
    request = ranges.RangeRequest(
        ecf_type=EcfType.CONSUMER_INVOICE, emission_point=POINT, numbers=HUNDRED, expires_on=None
    )
    async with stack.units.unit(TENANT) as unit:
        await ranges.register(unit, request)
    await stack.register(EcfType.CREDIT_INVOICE)
    for _ in range(3):
        await stack.issue(CONTENTS[EcfType.CONSUMER_INVOICE])
    assert len(await PgExpiringRanges(stack.database).due(date(2030, 1, 1), 10)) == 1
    async with stack.units.unit(TENANT) as unit:
        first = await queries.documents(unit, PageRequest(size=2))
        assert first.next_cursor is not None
        rest = await queries.documents(unit, PageRequest(size=2, after=decode_cursor(first.next_cursor)))
        assert (len(first.items), len(rest.items), rest.next_cursor) == (2, 1, None)
        ranges_first = await queries.sequences(unit, PageRequest(size=1))
        assert ranges_first.next_cursor is not None
        after = PageRequest(size=1, after=decode_cursor(ranges_first.next_cursor))
        assert len((await queries.sequences(unit, after)).items) == 1
```


- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest services/fiscal-do/tests/test_codec.py services/fiscal-do/tests/test_events.py services/fiscal-do/tests/test_repositories.py -q --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'ledger_fiscal_do.adapters.postgres.codec'`.

- [ ] **Step 4: Implement**

`services/fiscal-do/src/ledger_fiscal_do/adapters/postgres/codec.py`:

```python
"""A document's content, its issuer and DGII's outcomes as JSON (``jsonb``), written and read exactly.

What a document says is kept as the client sent it, once checked: a ``Content`` is a tree of a hundred
optional fields that no query reads one by one, so a column each would only spell the tree out twice. The JSON
is fiscal-do's own, versioned (``"v": 1``): amounts and quantities are decimal strings (never a float), dates
ISO 8601, enumerations their values; an absent optional field is absent, not ``null``. A reader of version 1
reads every document version 1 wrote: a later format adds a version, and a migration rewrites nothing.

The totals are not stored: they are computed again from the content when a document is read (exact, and the
same computation that numbered it), so content and totals cannot disagree.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import ClassVar, Final

from pydantic import TypeAdapter

from ledger_fiscal_do.domain.content import Buyer, Content, Issuer, Line, Payment, Reference, Withholding
from ledger_fiscal_do.domain.verdict import DgiiMessage, Outcome, Verdict
from ledger_fiscal_do.domain.vocabulary import (
    Billing,
    EcfType,
    IncomeType,
    ItemKind,
    Modification,
    PaymentMethod,
    PaymentTerms,
)
from ledger_kernel.countries.do import TaxId
from ledger_kernel.errors import LedgerError
from ledger_kernel.money import Currency, Money

VERSION: Final = 1
type Json = str | int | bool | list[Json] | dict[str, Json]
"""What the codec writes: no float, no ``null``."""
type Tree = dict[str, Json]
_TREE: Final = TypeAdapter(dict[str, Json])


class UnreadableStoredDocumentError(LedgerError):
    """A stored document's JSON is not one this version reads: a defect, or a row written by hand."""

    code: ClassVar[str] = "stored_document_unreadable"

    def __init__(self) -> None:
        super().__init__("a stored document cannot be read")


def _money(amount: Money) -> str:
    return format(amount.amount, "f")


def _dop(text: Json) -> Money:
    return Money.of(_text(text), Currency.DOP)


def _text(value: Json) -> str:
    if not isinstance(value, str):
        raise UnreadableStoredDocumentError
    return value


def _number(value: Json) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise UnreadableStoredDocumentError
    return value


def _tree(value: Json) -> Tree:
    if not isinstance(value, dict):
        raise UnreadableStoredDocumentError
    return value


def _items(value: Json) -> list[Json]:
    if not isinstance(value, list):
        raise UnreadableStoredDocumentError
    return value


def _field(tree: Tree, name: str) -> Json:
    found = tree.get(name)
    if found is None:
        raise UnreadableStoredDocumentError
    return found


def _some(tree: Tree, **fields: Json | None) -> Tree:
    """``tree`` with every field of ``fields`` that is not ``None``."""
    return tree | {name: value for name, value in fields.items() if value is not None}


def _optional_text(tree: Tree, name: str) -> str | None:
    value = tree.get(name)
    return None if value is None else _text(value)


def _optional_date(tree: Tree, name: str) -> date | None:
    value = tree.get(name)
    return None if value is None else date.fromisoformat(_text(value))


def _optional_dop(tree: Tree, name: str) -> Money | None:
    value = tree.get(name)
    return None if value is None else _dop(value)


def _optional_tax_id(tree: Tree, name: str) -> TaxId | None:
    value = tree.get(name)
    return None if value is None else TaxId.parse(_text(value))


# ── Writing ─────────────────────────────────────────────────────────────────────────────────────────────


def issuer_json(issuer: Issuer) -> Tree:
    """``issuer`` as JSON."""
    return _some(
        {
            "tax_id": str(issuer.tax_id),
            "legal_name": issuer.legal_name,
            "address": issuer.address,
            "phones": list(issuer.phones),
        },
        trade_name=issuer.trade_name,
        branch=issuer.branch,
        municipality=issuer.municipality,
        province=issuer.province,
        email=issuer.email,
    )


def _buyer_json(buyer: Buyer) -> Tree:
    return _some(
        {},
        tax_id=None if buyer.tax_id is None else str(buyer.tax_id),
        foreign_id=buyer.foreign_id,
        name=buyer.name,
        contact=buyer.contact,
        email=buyer.email,
        address=buyer.address,
        municipality=buyer.municipality,
        province=buyer.province,
        country=buyer.country,
    )


def _withholding_json(withholding: Withholding) -> Tree:
    return _some(
        {"perception": withholding.perception},
        itbis=None if withholding.itbis is None else _money(withholding.itbis),
        isr=None if withholding.isr is None else _money(withholding.isr),
    )


def _line_json(line: Line) -> Tree:
    return _some(
        {
            "number": line.number,
            "name": line.name,
            "kind": int(line.kind),
            "quantity": format(line.quantity, "f"),
            "unit_price": format(line.unit_price, "f"),
            "billing": int(line.billing),
        },
        description=line.description,
        unit=line.unit,
        discount=None if line.discount is None else _money(line.discount),
        withholding=None if line.withholding is None else _withholding_json(line.withholding),
    )


def _reference_json(reference: Reference) -> Tree:
    return _some(
        {
            "modified": reference.modified,
            "issued_on": reference.issued_on.isoformat(),
            "code": int(reference.code),
        },
        reason=reference.reason,
        other_taxpayer=None if reference.other_taxpayer is None else str(reference.other_taxpayer),
    )


def content_json(content: Content) -> Tree:
    """``content`` as JSON (see the module)."""
    return _some(
        {
            "v": VERSION,
            "ecf_type": int(content.ecf_type),
            "issued_on": content.issued_on.isoformat(),
            "payments": [
                {"method": int(payment.method), "amount": _money(payment.amount)}
                for payment in content.payments
            ],
            "prices_include_itbis": content.prices_include_itbis,
            "lines": [_line_json(line) for line in content.lines],
        },
        income=None if content.income is None else content.income.value,
        terms=None if content.terms is None else int(content.terms),
        deadline=None if content.deadline is None else content.deadline.isoformat(),
        buyer=None if content.buyer is None else _buyer_json(content.buyer),
        reference=None if content.reference is None else _reference_json(content.reference),
    )


def outcome_json(outcome: Outcome) -> Tree:
    """DGII's outcome as JSON."""
    return {
        "verdict": outcome.verdict.value,
        "messages": [{"code": message.code, "text": message.text} for message in outcome.messages],
        "decided_at": outcome.decided_at.isoformat(),
    }


# ── Reading ─────────────────────────────────────────────────────────────────────────────────────────────


def stored(value: object) -> Tree:
    """A ``jsonb`` value as read from PostgreSQL, checked to be the JSON the codec writes."""
    return _TREE.validate_python(value, strict=True)


def issuer_of(tree: Tree) -> Issuer:
    """The issuer ``issuer_json`` wrote."""
    return Issuer(
        tax_id=TaxId.parse(_text(_field(tree, "tax_id"))),
        legal_name=_text(_field(tree, "legal_name")),
        trade_name=_optional_text(tree, "trade_name"),
        branch=_optional_text(tree, "branch"),
        address=_text(_field(tree, "address")),
        municipality=_optional_text(tree, "municipality"),
        province=_optional_text(tree, "province"),
        phones=tuple(_text(phone) for phone in _items(_field(tree, "phones"))),
        email=_optional_text(tree, "email"),
    )


def _buyer_of(tree: Tree) -> Buyer:
    return Buyer(
        tax_id=_optional_tax_id(tree, "tax_id"),
        foreign_id=_optional_text(tree, "foreign_id"),
        name=_optional_text(tree, "name"),
        contact=_optional_text(tree, "contact"),
        email=_optional_text(tree, "email"),
        address=_optional_text(tree, "address"),
        municipality=_optional_text(tree, "municipality"),
        province=_optional_text(tree, "province"),
        country=_optional_text(tree, "country"),
    )


def _withholding_of(tree: Tree) -> Withholding:
    perception = _field(tree, "perception")
    if not isinstance(perception, bool):
        raise UnreadableStoredDocumentError
    return Withholding(
        itbis=_optional_dop(tree, "itbis"), isr=_optional_dop(tree, "isr"), perception=perception
    )


def _line_of(tree: Tree) -> Line:
    unit = tree.get("unit")
    withholding = tree.get("withholding")
    return Line(
        number=_number(_field(tree, "number")),
        name=_text(_field(tree, "name")),
        description=_optional_text(tree, "description"),
        kind=ItemKind(_number(_field(tree, "kind"))),
        quantity=Decimal(_text(_field(tree, "quantity"))),
        unit=None if unit is None else _number(unit),
        unit_price=Decimal(_text(_field(tree, "unit_price"))),
        discount=_optional_dop(tree, "discount"),
        billing=Billing(_number(_field(tree, "billing"))),
        withholding=None if withholding is None else _withholding_of(_tree(withholding)),
    )


def _reference_of(tree: Tree) -> Reference:
    return Reference(
        modified=_text(_field(tree, "modified")),
        issued_on=date.fromisoformat(_text(_field(tree, "issued_on"))),
        code=Modification(_number(_field(tree, "code"))),
        reason=_optional_text(tree, "reason"),
        other_taxpayer=_optional_tax_id(tree, "other_taxpayer"),
    )


def content_of(tree: Tree) -> Content:
    """The content ``content_json`` wrote; ``UnreadableStoredDocumentError`` for any other JSON."""
    if tree.get("v") != VERSION:
        raise UnreadableStoredDocumentError
    income, terms = tree.get("income"), tree.get("terms")
    buyer, reference = tree.get("buyer"), tree.get("reference")
    prices_include_itbis = _field(tree, "prices_include_itbis")
    if not isinstance(prices_include_itbis, bool):
        raise UnreadableStoredDocumentError
    return Content(
        ecf_type=EcfType(_number(_field(tree, "ecf_type"))),
        issued_on=date.fromisoformat(_text(_field(tree, "issued_on"))),
        income=None if income is None else IncomeType(_text(income)),
        terms=None if terms is None else PaymentTerms(_number(terms)),
        deadline=_optional_date(tree, "deadline"),
        payments=tuple(
            Payment(
                method=PaymentMethod(_number(_field(entry, "method"))), amount=_dop(_field(entry, "amount"))
            )
            for entry in map(_tree, _items(_field(tree, "payments")))
        ),
        prices_include_itbis=prices_include_itbis,
        buyer=None if buyer is None else _buyer_of(_tree(buyer)),
        lines=tuple(_line_of(_tree(entry)) for entry in _items(_field(tree, "lines"))),
        reference=None if reference is None else _reference_of(_tree(reference)),
    )


def outcome_of(tree: Tree) -> Outcome:
    """The outcome ``outcome_json`` wrote."""
    return Outcome(
        verdict=Verdict(_text(_field(tree, "verdict"))),
        messages=tuple(
            DgiiMessage(_text(_field(entry, "code")), _text(_field(entry, "text")))
            for entry in map(_tree, _items(_field(tree, "messages")))
        ),
        decided_at=datetime.fromisoformat(_text(_field(tree, "decided_at"))),
    )
```


`services/fiscal-do/src/ledger_fiscal_do/adapters/events.py`:

```python
"""What fiscal-do announces on the wire (§4.1): protobuf messages in envelopes, through the outbox."""

from datetime import UTC, datetime, time
from typing import Final, final, override
lazy from collections.abc import Sequence

from google.protobuf.timestamp_pb2 import Timestamp
lazy from google.protobuf.message import Message

import ledger.events.fiscal_do.v1.events_pb2 as wire
from ledger.common.v1.money_pb2 import Money as WireMoney
from ledger_contracts import fiscal_do as subjects
from ledger_contracts.envelope import Envelope
from ledger_fiscal_do.application.ports import Events
from ledger_fiscal_do.domain.events import (
    AnnulmentRequested,
    DocumentAccepted,
    DocumentConditionallyAccepted,
    DocumentNumbered,
    DocumentRejected,
    SequenceExpiring,
    SequenceLow,
)
from ledger_fiscal_do.domain.verdict import Verdict
from ledger_fiscal_do.domain.vocabulary import DgiiEnvironment
from ledger_kernel.clock import utc_now
from ledger_platform.streams import StreamSpec
lazy from ledger_fiscal_do.domain.events import DocumentEvent, FiscalEvent
lazy from ledger_fiscal_do.domain.verdict import DgiiMessage
lazy from ledger_kernel.clock import Clock
lazy from ledger_platform.database import UnitOfWork
lazy from ledger_platform.outbox import Outbox

FISCAL_DO: Final = StreamSpec(name=subjects.STREAM, subjects=subjects.SUBJECTS)
"""The stream of fiscal-do's events, declared by fiscal-do at start."""
SOURCE: Final = "fiscal-do"
_ENVIRONMENTS: Final = {
    DgiiEnvironment.PRECERTIFICATION: wire.DGII_ENVIRONMENT_PRECERTIFICATION,
    DgiiEnvironment.CERTIFICATION: wire.DGII_ENVIRONMENT_CERTIFICATION,
    DgiiEnvironment.PRODUCTION: wire.DGII_ENVIRONMENT_PRODUCTION,
}
_VERDICTS: Final = {
    Verdict.ACCEPTED: wire.VERDICT_ACCEPTED,
    Verdict.ACCEPTED_CONDITIONALLY: wire.VERDICT_ACCEPTED_CONDITIONALLY,
    Verdict.REJECTED: wire.VERDICT_REJECTED,
}


def _messages(messages: Sequence[DgiiMessage]) -> list[wire.DgiiMessage]:
    return [wire.DgiiMessage(code=message.code, text=message.text) for message in messages]


def _day(day: datetime) -> Timestamp:
    stamp = Timestamp()
    stamp.FromDatetime(day)
    return stamp


def _document_event(event: DocumentEvent, organization: str, company: str) -> tuple[Message, str]:
    match event:
        case DocumentNumbered():
            total = WireMoney(amount=format(event.total.amount, "f"), currency=event.total.currency.value)
            numbered = wire.DocumentNumbered(
                document_id=str(event.document),
                organization_id=organization,
                company_id=company,
                encf=str(event.encf),
                version=event.version,
                total=total,
                environment=_ENVIRONMENTS[event.environment],
            )
            return numbered, subjects.DOCUMENT_NUMBERED
        case DocumentAccepted():
            accepted = wire.DocumentAccepted(
                document_id=str(event.document),
                organization_id=organization,
                company_id=company,
                encf=str(event.encf),
                version=event.version,
            )
            return accepted, subjects.DOCUMENT_ACCEPTED
        case DocumentConditionallyAccepted():
            conditionally = wire.DocumentConditionallyAccepted(
                document_id=str(event.document),
                organization_id=organization,
                company_id=company,
                encf=str(event.encf),
                version=event.version,
                messages=_messages(event.messages),
            )
            return conditionally, subjects.DOCUMENT_CONDITIONALLY_ACCEPTED
        case _:
            rejected = wire.DocumentRejected(
                document_id=str(event.document),
                organization_id=organization,
                company_id=company,
                encf=str(event.encf),
                version=event.version,
                messages=_messages(event.messages),
            )
            return rejected, subjects.DOCUMENT_REJECTED


def outgoing(event: FiscalEvent) -> tuple[Message, str]:
    """The payload of ``event`` and the subject it is published on."""
    organization, company = str(event.tenant.organization), str(event.tenant.company)
    match event:
        case DocumentNumbered() | DocumentAccepted() | DocumentConditionallyAccepted() | DocumentRejected():
            return _document_event(event, organization, company)
        case SequenceLow():
            low = wire.SequenceLow(
                sequence_id=str(event.sequence),
                organization_id=organization,
                company_id=company,
                ecf_type=int(event.ecf_type),
                remaining=event.remaining,
                version=event.version,
            )
            return low, subjects.SEQUENCE_LOW
        case SequenceExpiring():
            expiring = wire.SequenceExpiring(
                sequence_id=str(event.sequence),
                organization_id=organization,
                company_id=company,
                ecf_type=int(event.ecf_type),
                expires_on=_day(datetime.combine(event.expires_on, time(), tzinfo=UTC)),
                days_left=event.days_left,
            )
            return expiring, subjects.SEQUENCE_EXPIRING
        case AnnulmentRequested():
            requested = wire.AnnulmentRequested(
                annulment_id=str(event.annulment),
                organization_id=organization,
                company_id=company,
                sequence_id=str(event.sequence),
                first_encf=str(event.first),
                last_encf=str(event.last),
            )
            return requested, subjects.ANNULMENT_REQUESTED
        case _:
            decided = wire.AnnulmentDecided(
                annulment_id=str(event.annulment),
                organization_id=organization,
                company_id=company,
                verdict=_VERDICTS[event.verdict],
                messages=_messages(event.messages),
            )
            return decided, subjects.ANNULMENT_DECIDED


@final
class OutboxEvents(Events):
    """Announces fiscal-do's events through the outbox of the unit's transaction."""

    __slots__ = ("_clock", "_outbox", "_unit")

    def __init__(self, unit: UnitOfWork, outbox: Outbox, *, clock: Clock = utc_now) -> None:
        self._unit = unit
        self._outbox = outbox
        self._clock = clock

    @override
    async def publish(self, event: FiscalEvent) -> None:
        message, subject = outgoing(event)
        envelope = Envelope.wrap(message, source=SOURCE, tenant=event.tenant, clock=self._clock)
        await self._outbox.add(self._unit, envelope, subject=subject)
```


`services/fiscal-do/src/ledger_fiscal_do/adapters/postgres/repositories.py`:

```python
"""fiscal-do's repositories: the ports of ``application.ports`` over one transaction (SQLAlchemy Core).

Every repository is bound to the unit's company and names its organization and company in every statement;
row-level security enforces the same scope again (§2). Numbering is serialized by the range's row lock
(``usable`` reads its candidates ``FOR UPDATE``): two documents issued at once take two numbers, in turn, and
``uq_documents_encf`` is the backstop. Registering ranges is serialized per company by an advisory lock
(no row exists yet to lock). ``PgExpiringRanges`` and ``PgPipeline`` alone read across companies, without a
scope, the two watches outside row-level security; the repositories keep them in step with what they watch.
"""

from typing import Final, final, override
from uuid import UUID
lazy from collections.abc import Sequence
lazy from datetime import date, datetime

from sqlalchemy import and_, delete, func, literal, or_, select, update
from sqlalchemy.dialects.postgresql import insert
lazy from sqlalchemy import ColumnElement, Row
lazy from sqlalchemy.ext.asyncio import AsyncConnection

from ledger_fiscal_do.adapters.postgres import codec
from ledger_fiscal_do.adapters.postgres.tables import (
    AnnulmentRow,
    ArchiveIndexRow,
    DocumentRow,
    PipelineRow,
    ProfileRow,
    SequenceRow,
    SequenceWatchRow,
)
from ledger_fiscal_do.application.ports import (
    Annulments,
    Archived,
    ArchiveIndex,
    Documents,
    ExpiringRange,
    ExpiringRanges,
    Pipeline,
    Profiles,
    Sequences,
    Watched,
)
from ledger_fiscal_do.domain.annulment import Annulment, AnnulmentNotFoundError, AnnulmentState
from ledger_fiscal_do.domain.document import DocumentNotFoundError, DocumentState, FiscalDocument, Signature
from ledger_fiscal_do.domain.encf import Encf
from ledger_fiscal_do.domain.ids import AnnulmentId, DocumentId, EmissionPointId, SequenceId, TrackId
from ledger_fiscal_do.domain.profile import FiscalProfile
from ledger_fiscal_do.domain.sequence import EncfSequence, NumberRange
from ledger_fiscal_do.domain.totals import compute
from ledger_fiscal_do.domain.vocabulary import DgiiEnvironment, EcfType
from ledger_kernel.countries.do import TaxId
from ledger_kernel.ids import CompanyId, OrganizationId, Tenant
from ledger_kernel.money import Currency, Money
from ledger_kernel.pagination import page_of
lazy from ledger_fiscal_do.application.ports import ArchiveKind
lazy from ledger_kernel.pagination import Page, PageRequest
lazy from ledger_platform.database import Database

_DOCUMENT: Final = select(
    DocumentRow.id,
    DocumentRow.emission_point_id,
    DocumentRow.environment,
    DocumentRow.e_ncf,
    DocumentRow.sequence_id,
    DocumentRow.sequence_expires_on,
    DocumentRow.state,
    DocumentRow.blocked_from,
    DocumentRow.contingency_since,
    DocumentRow.version,
    DocumentRow.issuer,
    DocumentRow.content,
    DocumentRow.signed_at,
    DocumentRow.security_code,
    DocumentRow.archive_key,
    DocumentRow.sha256,
    DocumentRow.summary,
    DocumentRow.track_id,
    DocumentRow.outcome,
    DocumentRow.created_at,
    DocumentRow.updated_at,
)
type _DocumentRow = Row[
    UUID,
    UUID,
    str,
    str,
    UUID,
    date | None,
    str,
    str | None,
    datetime | None,
    int,
    dict[str, object],
    dict[str, object],
    datetime | None,
    str | None,
    str | None,
    str | None,
    bool | None,
    str | None,
    dict[str, object] | None,
    datetime,
    datetime,
]
_SEQUENCE: Final = select(
    SequenceRow.id,
    SequenceRow.ecf_type,
    SequenceRow.emission_point_id,
    SequenceRow.first_number,
    SequenceRow.last_number,
    SequenceRow.expires_on,
    SequenceRow.next_number,
    SequenceRow.annulled,
    SequenceRow.low_announced,
    SequenceRow.version,
    SequenceRow.created_at,
)
type _SequenceRow = Row[UUID, int, UUID, int, int, date | None, int, list[list[int]], bool, int, datetime]
_ANNULMENT: Final = select(
    AnnulmentRow.id,
    AnnulmentRow.sequence_id,
    AnnulmentRow.ecf_type,
    AnnulmentRow.first_number,
    AnnulmentRow.last_number,
    AnnulmentRow.issuer,
    AnnulmentRow.environment,
    AnnulmentRow.state,
    AnnulmentRow.requested_at,
    AnnulmentRow.outcome,
)
type _AnnulmentRow = Row[UUID, UUID, int, int, int, str, str, str, datetime, dict[str, object] | None]
_UNDECIDED: Final = frozenset(
    state
    for state in DocumentState
    if state not in {DocumentState.ACCEPTED, DocumentState.ACCEPTED_CONDITIONALLY, DocumentState.REJECTED}
)


def _signature(row: _DocumentRow) -> Signature | None:
    signed_at, security_code, archive_key, sha256, summary = row[12:17]
    if signed_at is None or security_code is None or archive_key is None or sha256 is None or summary is None:
        return None
    return Signature(
        signed_at=signed_at,
        security_code=security_code,
        archive_key=archive_key,
        sha256=sha256,
        summary=summary,
    )


def _document(tenant: Tenant, row: _DocumentRow) -> FiscalDocument:
    content = codec.content_of(codec.stored(row[11]))
    outcome, blocked_from, track_id = row[18], row[7], row[17]
    return FiscalDocument(
        id=DocumentId(row[0]),
        tenant=tenant,
        emission_point=EmissionPointId(row[1]),
        environment=DgiiEnvironment(row[2]),
        issuer=codec.issuer_of(codec.stored(row[10])),
        content=content,
        totals=compute(content),
        encf=Encf.parse(row[3]),
        sequence=SequenceId(row[4]),
        sequence_expires_on=row[5],
        state=DocumentState(row[6]),
        version=row[9],
        created_at=row[19],
        updated_at=row[20],
        signature=_signature(row),
        track_id=None if track_id is None else TrackId(track_id),
        outcome=None if outcome is None else codec.outcome_of(codec.stored(outcome)),
        blocked_from=None if blocked_from is None else DocumentState(blocked_from),
        contingency_since=row[8],
    )


def _document_values(document: FiscalDocument) -> dict[str, object]:
    signature, reference, outcome = document.signature, document.content.reference, document.outcome
    return {
        "state": document.state.value,
        "blocked_from": None if document.blocked_from is None else document.blocked_from.value,
        "contingency_since": document.contingency_since,
        "version": document.version,
        "signed_at": None if signature is None else signature.signed_at,
        "security_code": None if signature is None else signature.security_code,
        "archive_key": None if signature is None else signature.archive_key,
        "sha256": None if signature is None else signature.sha256,
        "summary": None if signature is None else signature.summary,
        "track_id": document.track_id,
        "outcome": None if outcome is None else codec.outcome_json(outcome),
        "updated_at": document.updated_at,
        "modified_encf": None if reference is None else reference.modified,
    }


@final
class PgDocuments(Documents):
    """The documents of the unit's company, in PostgreSQL; each change kept in step with the pipeline."""

    __slots__ = ("_connection", "_tenant")

    def __init__(self, connection: AsyncConnection, tenant: Tenant) -> None:
        self._connection = connection
        self._tenant = tenant

    def _of_company(self) -> ColumnElement[bool]:
        return and_(
            DocumentRow.organization_id == self._tenant.organization,
            DocumentRow.company_id == self._tenant.company,
        )

    async def _one(self, condition: ColumnElement[bool], *, lock: bool = False) -> FiscalDocument | None:
        statement = _DOCUMENT.where(self._of_company(), condition)
        row = (await self._connection.execute(statement.with_for_update() if lock else statement)).first()
        return None if row is None else _document(self._tenant, row)

    @override
    async def add(self, document: FiscalDocument) -> None:
        await self._connection.execute(
            insert(DocumentRow).values(
                id=document.id,
                organization_id=self._tenant.organization,
                company_id=self._tenant.company,
                emission_point_id=document.emission_point,
                environment=document.environment.value,
                ecf_type=int(document.encf.ecf_type),
                e_ncf=str(document.encf),
                sequence_id=document.sequence,
                sequence_expires_on=document.sequence_expires_on,
                issuer=codec.issuer_json(document.issuer),
                content=codec.content_json(document.content),
                total=document.totals.total.amount,
                created_at=document.created_at,
                **_document_values(document),
            )
        )
        await self._watch(document)

    @override
    async def get(self, document: DocumentId) -> FiscalDocument | None:
        return await self._one(DocumentRow.id == document)

    @override
    async def locked(self, document: DocumentId) -> FiscalDocument:
        found = await self._one(DocumentRow.id == document, lock=True)
        if found is None:
            raise DocumentNotFoundError(document)
        return found

    @override
    async def by_encf(self, encf: Encf) -> FiscalDocument | None:
        return await self._one(DocumentRow.e_ncf == str(encf))

    @override
    async def update(self, document: FiscalDocument) -> None:
        await self._connection.execute(
            update(DocumentRow)
            .where(self._of_company(), DocumentRow.id == document.id)
            .values(**_document_values(document))
        )
        await self._watch(document)

    async def _watch(self, document: FiscalDocument) -> None:
        if document.state not in _UNDECIDED:
            await self._connection.execute(delete(PipelineRow).where(PipelineRow.document_id == document.id))
            return
        values = {"state": document.state.value, "since": document.updated_at}
        await self._connection.execute(
            insert(PipelineRow)
            .values(
                document_id=document.id,
                organization_id=self._tenant.organization,
                company_id=self._tenant.company,
                **values,
            )
            .on_conflict_do_update(index_elements=[PipelineRow.document_id], set_=values)
        )

    @override
    async def credited(self, encf: Encf) -> Money:
        statement = select(func.coalesce(func.sum(DocumentRow.total), 0)).where(
            self._of_company(),
            DocumentRow.modified_encf == str(encf),
            DocumentRow.ecf_type == int(EcfType.CREDIT_NOTE),
            DocumentRow.state != DocumentState.REJECTED.value,
        )
        return Money.of((await self._connection.execute(statement)).scalar_one(), Currency.DOP)

    @override
    async def blocked(self) -> tuple[DocumentId, ...]:
        statement = select(DocumentRow.id).where(
            self._of_company(), DocumentRow.state == DocumentState.BLOCKED_CERTIFICATE.value
        )
        return tuple(DocumentId(found) for found in (await self._connection.execute(statement)).scalars())

    @override
    async def page(self, request: PageRequest) -> Page[FiscalDocument]:
        statement = _DOCUMENT.where(self._of_company())
        if request.after is not None:
            statement = statement.where(DocumentRow.id > request.after)
        rows = await self._connection.execute(statement.order_by(DocumentRow.id).limit(request.size + 1))
        return page_of([_document(self._tenant, row) for row in rows], request, key=lambda found: found.id)


def _sequence(tenant: Tenant, row: _SequenceRow) -> EncfSequence:
    return EncfSequence(
        id=SequenceId(row[0]),
        tenant=tenant,
        ecf_type=EcfType(row[1]),
        emission_point=EmissionPointId(row[2]),
        numbers=NumberRange(row[3], row[4]),
        expires_on=row[5],
        next=row[6],
        annulled=tuple(NumberRange(first, last) for first, last in row[7]),
        low_announced=row[8],
        version=row[9],
        created_at=row[10],
    )


@final
class PgSequences(Sequences):
    """The e-NCF ranges of the unit's company, in PostgreSQL; their expiries kept in the watch."""

    __slots__ = ("_connection", "_tenant")

    def __init__(self, connection: AsyncConnection, tenant: Tenant) -> None:
        self._connection = connection
        self._tenant = tenant

    def _of_company(self) -> ColumnElement[bool]:
        return and_(
            SequenceRow.organization_id == self._tenant.organization,
            SequenceRow.company_id == self._tenant.company,
        )

    async def _all(self, *conditions: ColumnElement[bool], lock: bool = False) -> tuple[EncfSequence, ...]:
        statement = _SEQUENCE.where(self._of_company(), *conditions).order_by(SequenceRow.first_number)
        rows = await self._connection.execute(statement.with_for_update() if lock else statement)
        return tuple(_sequence(self._tenant, row) for row in rows)

    @override
    async def lock(self) -> None:
        key = func.hashtextextended(literal(f"sequences:{self._tenant.company}"), 0)
        await self._connection.execute(select(func.pg_advisory_xact_lock(key)))

    @override
    async def add(self, sequence: EncfSequence) -> None:
        await self._connection.execute(
            insert(SequenceRow).values(
                id=sequence.id,
                organization_id=self._tenant.organization,
                company_id=self._tenant.company,
                ecf_type=int(sequence.ecf_type),
                emission_point_id=sequence.emission_point,
                first_number=sequence.numbers.first,
                last_number=sequence.numbers.last,
                expires_on=sequence.expires_on,
                next_number=sequence.next,
                annulled=[[each.first, each.last] for each in sequence.annulled],
                low_announced=sequence.low_announced,
                version=sequence.version,
                created_at=sequence.created_at,
            )
        )
        if sequence.expires_on is not None and sequence.available:
            await self._connection.execute(
                insert(SequenceWatchRow).values(
                    sequence_id=sequence.id,
                    organization_id=self._tenant.organization,
                    company_id=self._tenant.company,
                    ecf_type=int(sequence.ecf_type),
                    expires_on=sequence.expires_on,
                    announced=None,
                )
            )

    @override
    async def get(self, sequence: SequenceId) -> EncfSequence | None:
        found = await self._all(SequenceRow.id == sequence)
        return found[0] if found else None

    @override
    async def locked(self, sequence: SequenceId) -> EncfSequence | None:
        found = await self._all(SequenceRow.id == sequence, lock=True)
        return found[0] if found else None

    @override
    async def of_type(self, ecf_type: EcfType) -> tuple[EncfSequence, ...]:
        return await self._all(SequenceRow.ecf_type == int(ecf_type))

    @override
    async def usable(self, ecf_type: EcfType, point: EmissionPointId, *, on: date) -> EncfSequence | None:
        candidates = await self._all(
            SequenceRow.ecf_type == int(ecf_type),
            SequenceRow.emission_point_id == point,
            SequenceRow.next_number <= SequenceRow.last_number,
            or_(SequenceRow.expires_on.is_(None), SequenceRow.expires_on >= on),
            lock=True,
        )
        return next((found for found in candidates if found.usable_on(on)), None)

    @override
    async def update(self, sequence: EncfSequence) -> None:
        await self._connection.execute(
            update(SequenceRow)
            .where(self._of_company(), SequenceRow.id == sequence.id)
            .values(
                next_number=sequence.next,
                annulled=[[each.first, each.last] for each in sequence.annulled],
                low_announced=sequence.low_announced,
                version=sequence.version,
            )
        )
        if not sequence.available:
            await self._connection.execute(
                delete(SequenceWatchRow).where(
                    SequenceWatchRow.company_id == self._tenant.company,
                    SequenceWatchRow.sequence_id == sequence.id,
                )
            )

    @override
    async def page(self, request: PageRequest) -> Page[EncfSequence]:
        statement = _SEQUENCE.where(self._of_company())
        if request.after is not None:
            statement = statement.where(SequenceRow.id > request.after)
        rows = await self._connection.execute(statement.order_by(SequenceRow.id).limit(request.size + 1))
        found = [_sequence(self._tenant, row) for row in rows]
        return page_of(found, request, key=lambda sequence: sequence.id)

    @override
    async def claim_expiry(self, sequence: SequenceId, days: int) -> bool:
        claimed = await self._connection.execute(
            update(SequenceWatchRow)
            .where(
                SequenceWatchRow.company_id == self._tenant.company,
                SequenceWatchRow.sequence_id == sequence,
                or_(SequenceWatchRow.announced.is_(None), SequenceWatchRow.announced > days),
            )
            .values(announced=days)
            .returning(SequenceWatchRow.sequence_id)
        )
        return claimed.first() is not None


def _annulment(tenant: Tenant, row: _AnnulmentRow) -> Annulment:
    outcome = row[9]
    return Annulment(
        id=AnnulmentId(row[0]),
        tenant=tenant,
        sequence=SequenceId(row[1]),
        ecf_type=EcfType(row[2]),
        numbers=NumberRange(row[3], row[4]),
        issuer=TaxId.parse(row[5]),
        environment=DgiiEnvironment(row[6]),
        state=AnnulmentState(row[7]),
        requested_at=row[8],
        outcome=None if outcome is None else codec.outcome_of(codec.stored(outcome)),
    )


@final
class PgAnnulments(Annulments):
    """The annulments of the unit's company, in PostgreSQL."""

    __slots__ = ("_connection", "_tenant")

    def __init__(self, connection: AsyncConnection, tenant: Tenant) -> None:
        self._connection = connection
        self._tenant = tenant

    def _of_company(self) -> ColumnElement[bool]:
        return and_(
            AnnulmentRow.organization_id == self._tenant.organization,
            AnnulmentRow.company_id == self._tenant.company,
        )

    @override
    async def add(self, annulment: Annulment) -> None:
        await self._connection.execute(
            insert(AnnulmentRow).values(
                id=annulment.id,
                organization_id=self._tenant.organization,
                company_id=self._tenant.company,
                sequence_id=annulment.sequence,
                ecf_type=int(annulment.ecf_type),
                first_number=annulment.numbers.first,
                last_number=annulment.numbers.last,
                issuer=str(annulment.issuer),
                environment=annulment.environment.value,
                state=annulment.state.value,
                requested_at=annulment.requested_at,
                outcome=None,
            )
        )

    @override
    async def get(self, annulment: AnnulmentId) -> Annulment | None:
        row = (
            await self._connection.execute(_ANNULMENT.where(self._of_company(), AnnulmentRow.id == annulment))
        ).first()
        return None if row is None else _annulment(self._tenant, row)

    @override
    async def locked(self, annulment: AnnulmentId) -> Annulment:
        statement = _ANNULMENT.where(self._of_company(), AnnulmentRow.id == annulment).with_for_update()
        row = (await self._connection.execute(statement)).first()
        if row is None:
            raise AnnulmentNotFoundError(annulment)
        return _annulment(self._tenant, row)

    @override
    async def update(self, annulment: Annulment) -> None:
        outcome = annulment.outcome
        await self._connection.execute(
            update(AnnulmentRow)
            .where(self._of_company(), AnnulmentRow.id == annulment.id)
            .values(
                state=annulment.state.value,
                outcome=None if outcome is None else codec.outcome_json(outcome),
            )
        )


@final
class PgProfiles(Profiles):
    """The unit's company's fiscal profile, in PostgreSQL; replaced only at the version read."""

    __slots__ = ("_connection", "_tenant")

    def __init__(self, connection: AsyncConnection, tenant: Tenant) -> None:
        self._connection = connection
        self._tenant = tenant

    @override
    async def get(self) -> FiscalProfile | None:
        statement = select(
            ProfileRow.environment,
            ProfileRow.address,
            ProfileRow.trade_name,
            ProfileRow.branch,
            ProfileRow.municipality,
            ProfileRow.province,
            ProfileRow.phones,
            ProfileRow.email,
            ProfileRow.version,
            ProfileRow.updated_at,
        ).where(
            ProfileRow.organization_id == self._tenant.organization,
            ProfileRow.company_id == self._tenant.company,
        )
        row = (await self._connection.execute(statement)).first()
        if row is None:
            return None
        environment, address, trade_name, branch, municipality, province, phones, email, version, updated = (
            row
        )
        return FiscalProfile(
            tenant=self._tenant,
            environment=DgiiEnvironment(environment),
            address=address,
            trade_name=trade_name,
            branch=branch,
            municipality=municipality,
            province=province,
            phones=tuple(phones),
            email=email,
            version=version,
            updated_at=updated,
        )

    @override
    async def put(self, profile: FiscalProfile) -> bool:
        values = {
            "environment": profile.environment.value,
            "address": profile.address,
            "trade_name": profile.trade_name,
            "branch": profile.branch,
            "municipality": profile.municipality,
            "province": profile.province,
            "phones": list(profile.phones),
            "email": profile.email,
            "version": profile.version,
            "updated_at": profile.updated_at,
        }
        statement = (
            insert(ProfileRow)
            .values(organization_id=self._tenant.organization, company_id=self._tenant.company, **values)
            .on_conflict_do_update(
                index_elements=[ProfileRow.company_id],
                set_=values,
                where=ProfileRow.version == profile.version - 1,
            )
            .returning(ProfileRow.version)
        )
        return (await self._connection.execute(statement)).first() is not None


@final
class PgArchiveIndex(ArchiveIndex):
    """The unit's company's index of the archive, in PostgreSQL."""

    __slots__ = ("_connection", "_tenant")

    def __init__(self, connection: AsyncConnection, tenant: Tenant) -> None:
        self._connection = connection
        self._tenant = tenant

    @override
    async def add(self, owner: DocumentId | AnnulmentId, kind: ArchiveKind, archived: Archived) -> None:
        await self._connection.execute(
            insert(ArchiveIndexRow)
            .values(
                organization_id=self._tenant.organization,
                company_id=self._tenant.company,
                owner_id=owner,
                kind=kind.value,
                key=archived.key,
                version_id=archived.version,
                sha256=archived.sha256,
                size=archived.size,
                retain_until=archived.retain_until,
            )
            .on_conflict_do_nothing(constraint="uq_archive_index_object")
        )

    @override
    async def find(self, owner: DocumentId | AnnulmentId, kind: ArchiveKind) -> Archived | None:
        statement = (
            select(
                ArchiveIndexRow.key,
                ArchiveIndexRow.version_id,
                ArchiveIndexRow.sha256,
                ArchiveIndexRow.size,
                ArchiveIndexRow.retain_until,
            )
            .where(
                ArchiveIndexRow.organization_id == self._tenant.organization,
                ArchiveIndexRow.company_id == self._tenant.company,
                ArchiveIndexRow.owner_id == owner,
                ArchiveIndexRow.kind == kind.value,
            )
            .order_by(ArchiveIndexRow.position.desc())
            .limit(1)
        )
        row = (await self._connection.execute(statement)).first()
        if row is None:
            return None
        key, version, sha256, size, retain_until = row
        return Archived(key=key, version=version, sha256=sha256, size=size, retain_until=retain_until)


@final
class PgExpiringRanges(ExpiringRanges):
    """Every company's ranges with an expiry, read without a scope (``sequence_watch``)."""

    __slots__ = ("_database",)

    def __init__(self, database: Database) -> None:
        self._database = database

    @override
    async def due(self, until: date, limit: int) -> Sequence[ExpiringRange]:
        statement = (
            select(
                SequenceWatchRow.sequence_id,
                SequenceWatchRow.organization_id,
                SequenceWatchRow.company_id,
                SequenceWatchRow.ecf_type,
                SequenceWatchRow.expires_on,
                SequenceWatchRow.announced,
            )
            .where(SequenceWatchRow.expires_on <= until)
            .order_by(SequenceWatchRow.expires_on)
            .limit(limit)
        )
        async with self._database.transaction(None) as unit:
            rows = (await unit.connection.execute(statement)).all()
        return tuple(
            ExpiringRange(
                sequence=SequenceId(sequence),
                tenant=Tenant(OrganizationId(organization), CompanyId(company)),
                ecf_type=EcfType(ecf_type),
                expires_on=expires_on,
                announced=announced,
            )
            for sequence, organization, company, ecf_type, expires_on, announced in rows
        )


@final
class PgPipeline(Pipeline):
    """Every company's documents not yet decided, oldest first, read without a scope (``pipeline``)."""

    __slots__ = ("_database",)

    def __init__(self, database: Database) -> None:
        self._database = database

    @override
    async def oldest(self, limit: int) -> Sequence[Watched]:
        statement = (
            select(
                PipelineRow.document_id,
                PipelineRow.organization_id,
                PipelineRow.company_id,
                PipelineRow.state,
                PipelineRow.since,
            )
            .order_by(PipelineRow.since)
            .limit(limit)
        )
        async with self._database.transaction(None) as unit:
            rows = (await unit.connection.execute(statement)).all()
        return tuple(
            Watched(
                document=DocumentId(document),
                tenant=Tenant(OrganizationId(organization), CompanyId(company)),
                state=DocumentState(state),
                since=since,
            )
            for document, organization, company, state, since in rows
        )
```


`services/fiscal-do/src/ledger_fiscal_do/adapters/postgres/units.py`:

```python
"""fiscal-do's unit of work over one chassis transaction scoped to a company: repositories and outbox."""

from contextlib import asynccontextmanager
from typing import ClassVar, final, override
lazy from collections.abc import AsyncGenerator

from ledger_fiscal_do.adapters.events import OutboxEvents
from ledger_fiscal_do.adapters.postgres.repositories import (
    PgAnnulments,
    PgArchiveIndex,
    PgDocuments,
    PgProfiles,
    PgSequences,
)
from ledger_fiscal_do.application.ports import FiscalUnit, Units
from ledger_kernel.clock import utc_now
from ledger_kernel.errors import LedgerError
from ledger_kernel.ids import Tenant
lazy from ledger_kernel.clock import Clock
lazy from ledger_platform.database import Database, UnitOfWork
lazy from ledger_platform.outbox import Outbox


class UnscopedUnitError(LedgerError):
    """fiscal-do's state was to be read in a transaction scoped to no company: a defect of the caller."""

    code: ClassVar[str] = "unscoped_unit"

    def __init__(self) -> None:
        super().__init__("fiscal-do's state is read and changed in a transaction scoped to one company")


def bind(unit: UnitOfWork, outbox: Outbox, *, clock: Clock = utc_now) -> FiscalUnit:
    """The fiscal view of ``unit``, a transaction scoped to a company; ``clock`` is read once."""
    tenant = unit.scope
    if not isinstance(tenant, Tenant):
        raise UnscopedUnitError
    now = clock()
    connection = unit.connection
    return FiscalUnit(
        tenant=tenant,
        documents=PgDocuments(connection, tenant),
        sequences=PgSequences(connection, tenant),
        annulments=PgAnnulments(connection, tenant),
        profiles=PgProfiles(connection, tenant),
        archive=PgArchiveIndex(connection, tenant),
        events=OutboxEvents(unit, outbox, clock=lambda: now),
        now=now,
    )


@final
class PgUnits(Units):
    """Units of work over fiscal-do's database."""

    __slots__ = ("_clock", "_database", "_outbox")

    def __init__(self, database: Database, outbox: Outbox, *, clock: Clock = utc_now) -> None:
        self._database = database
        self._outbox = outbox
        self._clock = clock

    @override
    @asynccontextmanager
    async def unit(self, tenant: Tenant) -> AsyncGenerator[FiscalUnit]:
        async with self._database.transaction(tenant) as transaction:
            yield bind(transaction, self._outbox, clock=self._clock)
```


(The rows' columns are read by position into typed names, as Milestones 2 and 3 read theirs; `ruff format` lays the long `Row[...]` aliases out one type per line.)

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest services/fiscal-do/tests -q --no-cov`
Expected: PASS: every type's document reads back equal to what was issued (content, issuer, totals recomputed), five documents issued at once take five numbers under the range's row lock, a company sees none of another's rows, the watches are read across companies and nothing else is.

- [ ] **Step 6: Gate**

Run: `uv run poe fmt && uv run poe check`
Expected: every step passes, coverage 100 %.

- [ ] **Step 7: Commit**

```bash
git add uv.lock services/fiscal-do
git commit -m "feat(fiscal-do): repositories under tenant isolation, numbering under the range's lock, outbox events

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Durable emission: Temporal's `EmitEcf` and `AnnulRange`, their activities, and starting them once

**Files:**
- Create: `services/fiscal-do/src/ledger_fiscal_do/workflows/__init__.py`, `workflows/orders.py`, `workflows/pacing.py`, `workflows/activities.py`, `workflows/emission.py`, `adapters/temporal.py`
- Modify: `services/fiscal-do/pyproject.toml`, `pyproject.toml` (root)
- Test: `services/fiscal-do/tests/test_workflows.py`

**Interfaces:**
- Consumes: `Emission`, `Annulling`, `Step` (Task 5); `Emissions` (Task 4); `world.Fiscal`, `fakes.accepted` (Task 5).
- Produces: `workflows.orders`: `TASK_QUEUE`, the activity names `SIGN`, `SUBMIT`, `POLL`, `RESUME`, `ANNUL`, `CERTIFICATE_AVAILABLE` (the signal), `EmissionOrder(organization, company, document)` and `AnnulmentOrder(organization, company, annulment)` with `tenant()`, `Activity`, `emission_id(tenant, document)`, `annulment_id(tenant, annulment)`. `workflows.pacing`: `UNREACHABLE`, `DECIDING` (schedules), `CERTIFICATE_LOOK_AGAIN`, `ACTIVITY_TIMEOUT`, `ACTIVITY_RETRIES`, `delay(schedule, attempt)`. `workflows.activities.FiscalActivities(emission, annulling)` with `sign`, `submit`, `poll`, `resume`, `annul`, `all()`. `workflows.emission`: `EmitEcf`, `AnnulRange` (each with the `certificate_available` signal), `WORKFLOWS`. `adapters.temporal.TemporalEmissions(client, *, task_queue=TASK_QUEUE)` (the `Emissions` port).

Re-check before starting: nothing of other milestones. The first run downloads Temporal's test server (`WorkflowEnvironment.start_time_skipping`); CI caches it like any download, or reaches the network.

- [ ] **Step 1: Dependencies, layers, the eager import**

`services/fiscal-do/pyproject.toml` — `dependencies` gains `"temporalio>=1.34.0"`. Root `pyproject.toml`:

- "fiscal-do is hexagonal": `layers = ["adapters", "workflows", "settings", "application", "domain"]` (the workflows know the steps; the Temporal adapter knows the workflows).
- ruff (Global Constraints, Decision 6):

```toml
[tool.ruff.lint.flake8-type-checking]
exempt-modules = ["typing", "typing_extensions", "temporalio.client"]
```

(beside the existing `strict` and `runtime-evaluated-base-classes`; `temporalio.client` is always imported eagerly.)

Run: `uv lock && uv sync`

- [ ] **Step 2: Write the failing test**

`services/fiscal-do/tests/test_workflows.py`:

```python
import asyncio
from datetime import timedelta
from typing import Final, final, override
from uuid import UUID
lazy from collections.abc import AsyncGenerator, Sequence

import pytest
import pytest_asyncio
from temporalio import activity
from temporalio.client import (
    Client,
    Interceptor,
    OutboundInterceptor,
    SignalWorkflowInput,
    WorkflowFailureError,
)
from temporalio.service import RPCError, RPCStatusCode
from temporalio.testing import ActivityEnvironment, WorkflowEnvironment
from temporalio.worker import Replayer, Worker

from ledger_fiscal_do.adapters.temporal import TemporalEmissions
from ledger_fiscal_do.application.emission import Step
from ledger_fiscal_do.domain.document import DocumentNotFoundError, DocumentState
from ledger_fiscal_do.domain.ids import AnnulmentId, DocumentId
from ledger_fiscal_do.domain.vocabulary import EcfType
from ledger_fiscal_do.workflows import pacing
from ledger_fiscal_do.workflows.activities import FiscalActivities
from ledger_fiscal_do.workflows.emission import WORKFLOWS, AnnulRange, EmitEcf
from ledger_fiscal_do.workflows.orders import (
    ANNUL,
    POLL,
    RESUME,
    SIGN,
    SUBMIT,
    TASK_QUEUE,
    AnnulmentOrder,
    EmissionOrder,
    emission_id,
)
from ledger_kernel.ids import new_id
lazy from ledger_fiscal_do.workflows.orders import Activity

from fakes import accepted
from samples import TENANT
from world import Fiscal

ORDER: Final = EmissionOrder(str(TENANT.organization), str(TENANT.company), str(new_id()))
pytestmark = pytest.mark.asyncio(loop_scope="module")
ANNULMENT: Final = AnnulmentOrder(str(TENANT.organization), str(TENANT.company), str(new_id()))


@final
class Script:
    """Activities answering what a test scripts, step by step, and remembering what was asked."""

    def __init__(self, **steps: list[Step]) -> None:
        self.steps: dict[str, list[Step]] = {name: list(words) for name, words in steps.items()}
        self.calls: list[str] = []

    def _next(self, name: str) -> str:
        self.calls.append(name)
        return self.steps[name].pop(0).value

    def activities(self) -> Sequence[Activity]:
        @activity.defn(name=SIGN)
        async def sign(order: EmissionOrder) -> str:
            del order
            return self._next("sign")

        @activity.defn(name=SUBMIT)
        async def submit(order: EmissionOrder) -> str:
            del order
            return self._next("submit")

        @activity.defn(name=POLL)
        async def poll(order: EmissionOrder) -> str:
            del order
            return self._next("poll")

        @activity.defn(name=RESUME)
        async def resume(order: EmissionOrder) -> None:
            del order
            self.calls.append("resume")

        @activity.defn(name=ANNUL)
        async def annul(order: AnnulmentOrder) -> str:
            del order
            return self._next("annul")

        return [sign, submit, poll, resume, annul]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def environment() -> AsyncGenerator[WorkflowEnvironment]:
    async with await WorkflowEnvironment.start_time_skipping() as started:
        yield started


async def emitted(environment: WorkflowEnvironment, script: Script, order: EmissionOrder = ORDER) -> str:
    async with Worker(
        environment.client, task_queue=TASK_QUEUE, workflows=WORKFLOWS, activities=script.activities()
    ):
        return await environment.client.execute_workflow(
            EmitEcf.run, order, id=f"test-{new_id()}", task_queue=TASK_QUEUE
        )


async def test_a_document_is_signed_sent_and_polled_until_decided(environment: WorkflowEnvironment) -> None:
    script = Script(sign=[Step.SIGNED], submit=[Step.SENT], poll=[Step.PENDING, Step.PENDING, Step.DECIDED])
    started = await environment.get_current_time()
    assert await emitted(environment, script) == "decided"
    assert script.calls == ["sign", "submit", "poll", "poll", "poll"]
    waited = await environment.get_current_time() - started
    assert waited >= sum(pacing.DECIDING[:3], timedelta())


async def test_a_summary_decided_at_once_is_not_polled(environment: WorkflowEnvironment) -> None:
    script = Script(sign=[Step.SIGNED], submit=[Step.DECIDED])
    assert await emitted(environment, script) == "decided"
    assert script.calls == ["sign", "submit"]


async def test_dgii_unreachable_is_tried_again_later_and_longer(environment: WorkflowEnvironment) -> None:
    script = Script(
        sign=[Step.SIGNED],
        submit=[Step.UNREACHABLE, Step.UNREACHABLE, Step.SENT],
        poll=[Step.UNREACHABLE, Step.DECIDED],
    )
    started = await environment.get_current_time()
    assert await emitted(environment, script) == "decided"
    assert script.calls == ["sign", "submit", "submit", "submit", "poll", "poll"]
    assert await environment.get_current_time() - started >= timedelta(minutes=3)


async def test_without_a_certificate_the_document_waits_for_a_signal(
    environment: WorkflowEnvironment,
) -> None:
    script = Script(
        sign=[Step.BLOCKED, Step.SIGNED],
        submit=[Step.BLOCKED, Step.DECIDED],
    )
    async with Worker(
        environment.client, task_queue=TASK_QUEUE, workflows=WORKFLOWS, activities=script.activities()
    ):
        handle = await environment.client.start_workflow(
            EmitEcf.run, ORDER, id=f"test-{new_id()}", task_queue=TASK_QUEUE
        )
        await handle.signal(EmitEcf.certificate_available)
        assert await handle.result() == "decided"
    assert script.calls == ["sign", "resume", "sign", "submit", "resume", "submit"]


async def test_an_annulment_is_tried_until_dgii_answers(environment: WorkflowEnvironment) -> None:
    script = Script(annul=[Step.UNREACHABLE, Step.BLOCKED, Step.DECIDED])
    async with Worker(
        environment.client, task_queue=TASK_QUEUE, workflows=WORKFLOWS, activities=script.activities()
    ):
        handle = await environment.client.start_workflow(
            AnnulRange.run, ANNULMENT, id=f"test-{new_id()}", task_queue=TASK_QUEUE
        )
        await handle.signal(AnnulRange.certificate_available)
        assert await handle.result() == "decided"
    assert script.calls == ["annul", "annul", "annul"]


async def test_a_defect_fails_the_workflow_without_retrying(environment: WorkflowEnvironment) -> None:
    @activity.defn(name=SIGN)
    async def sign(order: EmissionOrder) -> str:
        raise DocumentNotFoundError(DocumentId(UUID(order.document)))

    async with Worker(environment.client, task_queue=TASK_QUEUE, workflows=WORKFLOWS, activities=[sign]):
        with pytest.raises(WorkflowFailureError):
            await environment.client.execute_workflow(
                EmitEcf.run,
                ORDER,
                id=f"test-{new_id()}",
                task_queue=TASK_QUEUE,
                execution_timeout=timedelta(minutes=5),
            )


async def test_a_long_wait_continues_as_new_and_finds_its_work_done(environment: WorkflowEnvironment) -> None:
    pending = [Step.PENDING] * 400
    script = Script(
        sign=[Step.SIGNED, Step.SIGNED], submit=[Step.SENT, Step.SENT], poll=[*pending, Step.DECIDED]
    )
    assert await emitted(environment, script) == "decided"
    assert script.calls.count("sign") == 2


async def test_the_workflows_replay_deterministically(environment: WorkflowEnvironment) -> None:
    script = Script(
        sign=[Step.BLOCKED, Step.SIGNED],
        submit=[Step.UNREACHABLE, Step.SENT],
        poll=[Step.PENDING, Step.DECIDED],
    )
    async with Worker(
        environment.client, task_queue=TASK_QUEUE, workflows=WORKFLOWS, activities=script.activities()
    ):
        handle = await environment.client.start_workflow(
            EmitEcf.run, ORDER, id=f"test-{new_id()}", task_queue=TASK_QUEUE
        )
        await handle.signal(EmitEcf.certificate_available)
        await handle.result()
    history = await handle.fetch_history()
    await Replayer(workflows=WORKFLOWS).replay_workflow(history)


async def test_an_emission_starts_once_and_a_finished_one_ignores_signals(
    environment: WorkflowEnvironment,
) -> None:
    client: Client = environment.client
    emissions = TemporalEmissions(client)
    document, annulment = DocumentId(new_id()), AnnulmentId(new_id())
    script = Script(sign=[Step.SIGNED], submit=[Step.DECIDED], annul=[Step.DECIDED])
    async with Worker(client, task_queue=TASK_QUEUE, workflows=WORKFLOWS, activities=script.activities()):
        await emissions.start(TENANT, document)
        await emissions.start(TENANT, document)
        await emissions.annul(TENANT, annulment)
        await emissions.annul(TENANT, annulment)
        assert await client.get_workflow_handle(emission_id(TENANT, document)).result() == "decided"
        await asyncio.sleep(0)
    await emissions.resume(TENANT, [document, DocumentId(new_id())])
    assert script.calls.count("sign") == 1
    assert script.calls.count("annul") == 1


async def test_the_activities_run_the_steps_of_the_use_cases() -> None:
    fiscal = Fiscal()
    document = await fiscal.issued(EcfType.CREDIT_INVOICE)
    activities = FiscalActivities(fiscal.emission, fiscal.annulling)
    order = EmissionOrder(str(TENANT.organization), str(TENANT.company), str(document.id))
    run = ActivityEnvironment().run
    assert await run(activities.sign, order) == "signed"
    assert await run(activities.submit, order) == "sent"
    assert await run(activities.poll, order) == "pending"
    fiscal.dgii.results.append(accepted())
    assert await run(activities.poll, order) == "decided"
    await run(activities.resume, order)
    assert fiscal.stored(document.id).state is DocumentState.ACCEPTED
    missing = AnnulmentOrder(str(TENANT.organization), str(TENANT.company), str(new_id()))
    with pytest.raises(Exception, match="annulment"):
        await run(activities.annul, missing)
    assert len(activities.all()) == 5


@final
class Refusing(Interceptor):
    """A client whose every signal the server refuses, as an unauthorized one would be."""

    @override
    def intercept_client(self, next: OutboundInterceptor) -> OutboundInterceptor:
        return RefusingSignals(next)


@final
class RefusingSignals(OutboundInterceptor):
    @override
    async def signal_workflow(self, input: SignalWorkflowInput) -> None:
        del input
        message = "refused"
        raise RPCError(message, RPCStatusCode.PERMISSION_DENIED, b"")


async def test_a_signal_refused_for_another_reason_than_a_finished_workflow_is_raised(
    environment: WorkflowEnvironment,
) -> None:
    client = Client(environment.client.service_client, interceptors=[Refusing()])
    with pytest.raises(RPCError):
        await TemporalEmissions(client).resume(TENANT, [DocumentId(new_id())])
```


- [ ] **Step 3: Run the test to verify it fails**

Run: `uv run pytest services/fiscal-do/tests/test_workflows.py -q --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'ledger_fiscal_do.workflows'`.

- [ ] **Step 4: Implement**

`services/fiscal-do/src/ledger_fiscal_do/workflows/__init__.py`:

```python
"""Durable workflows (Temporal): a document's emission and an annulment, and the activities they call."""
```


`services/fiscal-do/src/ledger_fiscal_do/workflows/orders.py`:

```python
"""What a workflow is started with, and what its activities are named: plain strings, nothing else.

Temporal keeps a workflow's input and every activity's input and result in its history, as JSON (its default
converter). Only ids travel: an amount, a document or a token never enters Temporal's history; each activity
reads what it needs from fiscal-do's database. The activity names are the contract between the workflows'
history and the worker: renaming one breaks every workflow running.
"""

from dataclasses import dataclass
from typing import Final, final
from uuid import UUID
lazy from collections.abc import Awaitable, Callable

from ledger_kernel.ids import CompanyId, OrganizationId, Tenant

TASK_QUEUE: Final = "fiscal-do"
SIGN: Final = "fiscal_do.sign"
SUBMIT: Final = "fiscal_do.submit"
POLL: Final = "fiscal_do.poll"
RESUME: Final = "fiscal_do.resume"
ANNUL: Final = "fiscal_do.annul"
CERTIFICATE_AVAILABLE: Final = "certificate_available"
"""The signal a waiting workflow is sent when its company may sign again."""


@final
@dataclass(frozen=True, slots=True)
class EmissionOrder:
    """Take one numbered document to DGII's final answer."""

    organization: str
    company: str
    document: str

    def tenant(self) -> Tenant:
        """The company the document is of."""
        return Tenant(OrganizationId(UUID(self.organization)), CompanyId(UUID(self.company)))


@final
@dataclass(frozen=True, slots=True)
class AnnulmentOrder:
    """Tell DGII of one annulment and record its answer."""

    organization: str
    company: str
    annulment: str

    def tenant(self) -> Tenant:
        """The company the annulment is of."""
        return Tenant(OrganizationId(UUID(self.organization)), CompanyId(UUID(self.company)))


type Activity = Callable[[EmissionOrder], Awaitable[str | None]] | Callable[[AnnulmentOrder], Awaitable[str]]
"""An activity of fiscal-do's worker, as the worker is handed it."""


def emission_id(tenant: Tenant, document: UUID) -> str:
    """The id of a document's emission workflow: one per document, ever (a second start is refused)."""
    return f"ecf-{tenant.company}-{document}"


def annulment_id(tenant: Tenant, annulment: UUID) -> str:
    """The id of an annulment's workflow."""
    return f"anecf-{tenant.company}-{annulment}"
```


`services/fiscal-do/src/ledger_fiscal_do/workflows/pacing.py`:

```python
"""How long a workflow waits between attempts: plain data, read inside the sandbox.

* **DGII unreachable** (contingency): 1, 2, 5, 10, then every 15 minutes. DGII allows 72 hours to send a
  document emitted in contingency (DGII's e-CF technical report, §19); the pipeline worker alerts long before.
* **DGII deciding**: 2, 5, 10, 30, 60 seconds, then every 5 minutes. Most results are ready in seconds.
* **No certificate**: the workflow waits for the ``certificate_available`` signal (vault announced a new one),
  and looks again every hour in case a signal was missed.
* **Activities**: retried by Temporal on an unexpected failure (the database down, a worker lost) from 1 s up
  to every minute, forever; a defect (``NotReadyError``, a document that does not exist) is not retried.
"""

from datetime import timedelta
from typing import Final

from temporalio.common import RetryPolicy

UNREACHABLE: Final = (
    timedelta(minutes=1),
    timedelta(minutes=2),
    timedelta(minutes=5),
    timedelta(minutes=10),
    timedelta(minutes=15),
)
DECIDING: Final = (
    timedelta(seconds=2),
    timedelta(seconds=5),
    timedelta(seconds=10),
    timedelta(seconds=30),
    timedelta(seconds=60),
    timedelta(minutes=5),
)
CERTIFICATE_LOOK_AGAIN: Final = timedelta(hours=1)
ACTIVITY_TIMEOUT: Final = timedelta(seconds=60)
"""Longer than a DGII call with its retries (three 20-second attempts would not fit, two do with room)."""
ACTIVITY_RETRIES: Final = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(minutes=1),
    non_retryable_error_types=["NotReadyError", "DocumentNotFoundError", "AnnulmentNotFoundError"],
)


def delay(schedule: tuple[timedelta, ...], attempt: int) -> timedelta:
    """The wait before attempt ``attempt`` (0-based) of ``schedule``: its last entry once past its end."""
    return schedule[min(attempt, len(schedule) - 1)]
```


`services/fiscal-do/src/ledger_fiscal_do/workflows/activities.py`:

```python
"""The activities the workflows call by name: each one step of ``Emission`` or ``Annulling``.

An activity returns the ``Step`` word the workflow decides on. Every step is idempotent, so Temporal may run
one again (a worker lost mid-step, a timeout) without effect. A defect (``NotReadyError``, a document that
does not exist) fails the activity without retry (``pacing.ACTIVITY_RETRIES``); anything else unexpected (the
database down) is retried.
"""

from dataclasses import dataclass
from typing import final
from uuid import UUID
lazy from collections.abc import Sequence

from temporalio import activity

from ledger_fiscal_do.domain.ids import AnnulmentId, DocumentId
from ledger_fiscal_do.workflows.orders import ANNUL, POLL, RESUME, SIGN, SUBMIT
lazy from ledger_fiscal_do.application.annulling import Annulling
lazy from ledger_fiscal_do.application.emission import Emission
lazy from ledger_fiscal_do.workflows.orders import Activity, AnnulmentOrder, EmissionOrder


@final
@dataclass(frozen=True, slots=True)
class FiscalActivities:
    """The activities of fiscal-do's worker, over the steps they run."""

    emission: Emission
    annulling: Annulling

    @activity.defn(name=SIGN)
    async def sign(self, order: EmissionOrder) -> str:
        """Sign and archive the document."""
        return (await self.emission.sign(order.tenant(), DocumentId(UUID(order.document)))).value

    @activity.defn(name=SUBMIT)
    async def submit(self, order: EmissionOrder) -> str:
        """Send the document to DGII."""
        return (await self.emission.submit(order.tenant(), DocumentId(UUID(order.document)))).value

    @activity.defn(name=POLL)
    async def poll(self, order: EmissionOrder) -> str:
        """Ask DGII for its answer."""
        return (await self.emission.poll(order.tenant(), DocumentId(UUID(order.document)))).value

    @activity.defn(name=RESUME)
    async def resume(self, order: EmissionOrder) -> None:
        """A blocked document goes back where it stood."""
        await self.emission.resume(order.tenant(), DocumentId(UUID(order.document)))

    @activity.defn(name=ANNUL)
    async def annul(self, order: AnnulmentOrder) -> str:
        """Tell DGII of the annulment."""
        return (await self.annulling.annul(order.tenant(), AnnulmentId(UUID(order.annulment)))).value

    def all(self) -> Sequence[Activity]:
        """Every activity, for the worker."""
        return (self.sign, self.submit, self.poll, self.resume, self.annul)
```


`services/fiscal-do/src/ledger_fiscal_do/workflows/emission.py`:

```python
"""The workflows of fiscal-do: a document's emission and an annulment, durable across restarts (Temporal).

``EmitEcf`` decides *when*; the steps of ``ledger_fiscal_do.application.emission`` decide *what*, each
idempotent (it reads the document first and does nothing twice), so a step Temporal runs again after a lost
worker changes nothing. Sign; while no certificate, wait for one. Submit; while DGII is unreachable, wait and
submit again (the step asks DGII for the e-NCF's track ids first). Then poll until DGII decides. The workflow
continues as new when its history grows long (a document DGII leaves "En Proceso" for days); the new run's
first steps find their work done and go straight back to polling.

``AnnulRange`` runs the one step of an annulment until DGII answers it.

The workflows run in Temporal's sandbox: what they import passes through it (``Step``, ``pacing``, ``orders``
and ``FiscalActivities``, whose methods name the activities and type their results), and they read time only
from Temporal (``workflow.sleep``, ``workflow.wait_condition``).
"""

from contextlib import suppress
from typing import Final, final
lazy from collections.abc import Awaitable, Callable

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from ledger_fiscal_do.application.emission import Step
    from ledger_fiscal_do.workflows import pacing
    from ledger_fiscal_do.workflows.activities import FiscalActivities
    from ledger_fiscal_do.workflows.orders import CERTIFICATE_AVAILABLE
lazy from ledger_fiscal_do.workflows.orders import AnnulmentOrder, EmissionOrder


async def _step[Order](activity: Callable[[FiscalActivities, Order], Awaitable[str]], order: Order) -> Step:
    word = await workflow.execute_activity_method(
        activity,
        order,
        start_to_close_timeout=pacing.ACTIVITY_TIMEOUT,
        retry_policy=pacing.ACTIVITY_RETRIES,
    )
    return Step(word)


async def _resume(order: EmissionOrder) -> None:
    await workflow.execute_activity_method(
        FiscalActivities.resume,
        order,
        start_to_close_timeout=pacing.ACTIVITY_TIMEOUT,
        retry_policy=pacing.ACTIVITY_RETRIES,
    )


class _CertificateWait:
    """Waiting for a company's certificate: a signal, or an hour, whichever comes first."""

    def __init__(self) -> None:
        self.available: bool = False

    async def wait(self) -> None:
        with suppress(TimeoutError):
            await workflow.wait_condition(lambda: self.available, timeout=pacing.CERTIFICATE_LOOK_AGAIN)
        self.available = False


@final
@workflow.defn(name="EmitEcf")
class EmitEcf:
    """One document, from numbered to DGII's final answer (see the module)."""

    def __init__(self) -> None:
        self._certificate: _CertificateWait = _CertificateWait()

    @workflow.signal(name=CERTIFICATE_AVAILABLE)
    def certificate_available(self) -> None:
        """The company may sign again (vault announced a certificate)."""
        self._certificate.available = True

    @workflow.run
    async def run(self, order: EmissionOrder) -> str:
        """Where the document ended: always ``decided``, however long it takes."""
        step = await _step(FiscalActivities.sign, order)
        while step is Step.BLOCKED:
            await self._certificate.wait()
            await _resume(order)
            step = await _step(FiscalActivities.sign, order)
        step = await self._sent(order)
        attempt = 0
        while step is not Step.DECIDED:
            if workflow.info().is_continue_as_new_suggested():
                workflow.continue_as_new(order)
            await workflow.sleep(pacing.delay(pacing.DECIDING, attempt))
            attempt += 1
            step = await _step(FiscalActivities.poll, order)
        return step.value

    async def _sent(self, order: EmissionOrder) -> Step:
        step = await _step(FiscalActivities.submit, order)
        attempt = 0
        while step in {Step.UNREACHABLE, Step.BLOCKED}:
            if step is Step.BLOCKED:
                await self._certificate.wait()
                await _resume(order)
            else:
                await workflow.sleep(pacing.delay(pacing.UNREACHABLE, attempt))
                attempt += 1
            step = await _step(FiscalActivities.submit, order)
        return step


@final
@workflow.defn(name="AnnulRange")
class AnnulRange:
    """One annulment, until DGII answers it (see the module)."""

    def __init__(self) -> None:
        self._certificate: _CertificateWait = _CertificateWait()

    @workflow.signal(name=CERTIFICATE_AVAILABLE)
    def certificate_available(self) -> None:
        """The company may sign again."""
        self._certificate.available = True

    @workflow.run
    async def run(self, order: AnnulmentOrder) -> str:
        """Always ``decided``, however long it takes."""
        attempt = 0
        step = await _step(FiscalActivities.annul, order)
        while step is not Step.DECIDED:
            if step is Step.BLOCKED:
                await self._certificate.wait()
            else:
                await workflow.sleep(pacing.delay(pacing.UNREACHABLE, attempt))
                attempt += 1
            step = await _step(FiscalActivities.annul, order)
        return step.value


WORKFLOWS: Final = (EmitEcf, AnnulRange)
"""What fiscal-do's worker runs."""
```


`services/fiscal-do/src/ledger_fiscal_do/adapters/temporal.py`:

```python
"""``Emissions`` over Temporal: one workflow per document or annulment, signalled when a company may sign.

A workflow's id is derived from what it works on (``orders.emission_id``), and Temporal refuses a second
workflow by an id it has seen (``REJECT_DUPLICATE``): starting the emission of a document twice (the event
redelivered, two replicas) starts it once. Signalling a workflow that finished or never existed is no error:
the document it would wake is not waiting.
"""

from typing import Final, final, override
lazy from collections.abc import Sequence

from temporalio.client import Client
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError, RPCStatusCode

from ledger_fiscal_do.application.ports import Emissions
from ledger_fiscal_do.workflows.emission import AnnulRange, EmitEcf
from ledger_fiscal_do.workflows.orders import (
    CERTIFICATE_AVAILABLE,
    TASK_QUEUE,
    AnnulmentOrder,
    EmissionOrder,
    annulment_id,
    emission_id,
)
lazy from ledger_fiscal_do.domain.ids import AnnulmentId, DocumentId
lazy from ledger_kernel.ids import Tenant

_GONE: Final = frozenset({RPCStatusCode.NOT_FOUND})


@final
class TemporalEmissions(Emissions):
    """Starts and signals fiscal-do's workflows (see the module)."""

    __slots__ = ("_client", "_task_queue")

    def __init__(self, client: Client, *, task_queue: str = TASK_QUEUE) -> None:
        self._client = client
        self._task_queue = task_queue

    @override
    async def start(self, tenant: Tenant, document: DocumentId) -> None:
        order = EmissionOrder(str(tenant.organization), str(tenant.company), str(document))
        try:
            await self._client.start_workflow(
                EmitEcf.run,
                order,
                id=emission_id(tenant, document),
                task_queue=self._task_queue,
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
            )
        except WorkflowAlreadyStartedError:
            return

    @override
    async def resume(self, tenant: Tenant, documents: Sequence[DocumentId]) -> None:
        for document in documents:
            await self._signal(emission_id(tenant, document))

    @override
    async def annul(self, tenant: Tenant, annulment: AnnulmentId) -> None:
        order = AnnulmentOrder(str(tenant.organization), str(tenant.company), str(annulment))
        try:
            await self._client.start_workflow(
                AnnulRange.run,
                order,
                id=annulment_id(tenant, annulment),
                task_queue=self._task_queue,
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
            )
        except WorkflowAlreadyStartedError:
            return

    async def _signal(self, workflow: str) -> None:
        handle = self._client.get_workflow_handle(workflow)
        try:
            await handle.signal(CERTIFICATE_AVAILABLE)
        except RPCError as error:
            if error.status not in _GONE:
                raise
```


- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run pytest services/fiscal-do/tests/test_workflows.py -q --no-cov`
Expected: PASS in about 10 s: hours of timers skipped, a workflow that waits for its certificate wakes on the signal, one that polls 400 times continues as new and finds its work done, a recorded history replays deterministically, a defect fails the workflow without retries, and a second start of the same emission starts nothing.

- [ ] **Step 6: Gate**

Run: `uv run poe fmt && uv run poe check`
Expected: every step passes, coverage 100 %.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock services/fiscal-do
git commit -m "feat(fiscal-do): Temporal workflows for emission and annulment, started once, replayable

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: The public API, the consumers, the workers, and the service assembled; every type emitted end to end

**Files:**
- Create: `services/fiscal-do/src/ledger_fiscal_do/resources.py`, `api/__init__.py`, `api/context.py`, `api/models.py`, `api/documents.py`, `api/sequences.py`, `api/profile.py`, `api/rest.py`, `api/consumers.py`, `api/workers.py`, `main.py`, `libs/testing/src/ledger_testing/serving.py`
- Modify: `libs/testing/src/ledger_testing/containers.py`, `libs/testing/pyproject.toml`, `services/fiscal-do/pyproject.toml`, `pyproject.toml` (root)
- Test: `services/fiscal-do/tests/test_service.py`, `services/fiscal-do/tests/test_workers.py`, `libs/testing/tests/test_serving.py`

**Interfaces:**
- Consumes: everything of Tasks 3 to 11; `Service`, `Interfaces`, `Events`, `Runtime`, `RuntimeDependency` (`ledger_platform.service`); `authenticated`, `company_in_header`, `page_request`, `idempotency_key`, `attempt_of`, `json_response`, `answer`, `etag`, `parse_if_match`, `precondition_failed` (`ledger_platform.routes`); `CachedPermissions`, `CompanyAccess`, `PermissionSource` (`ledger_platform.authorization`); `RemotePermissions`, `GET_PERMISSIONS` (`ledger_platform.identity_client`, M2 Task 8); `HttpKeys`, `PublishedKeys`, `TokenPolicy`, `TokenVerifier`, `organization_of` (`ledger_platform.authentication`); `Idempotency`; `ConsumerSpec`; `Work` (`ledger_platform.supervision`); `PROBLEM_JSON` (`ledger_platform.web`); `ensure_stream`, `StreamSpec`; `Permission` (`ledger_kernel.access`); `LocalIssuer`, `nats_server`, `nats_address`, `eventually` (`ledger_testing`); `remotes` (Task 8).
- Produces: `resources`: `VERSION`, `FiscalResources(permissions, companies, signer, temporal, archive)`, `opened(settings, *, keys)`. `api.context`: `Problem`, `ApiContext(runtime, caller, in_company, idempotency)` with `build(*, runtime, verifier, idempotency)`, `problems(*statuses)`. `api.models`: `QR_HOSTS`, `NewDocument` (and its parts `LineShape`, `PaymentShape`, `BuyerShape`, `ReferenceShape`, `WithholdingShape`, each with `domain()`), `DocumentView`, `DocumentPage`, `OutcomeView`, `MessageView`, `NewSequence`, `SequenceView`, `SequencePage`, `NewAnnulment`, `AnnulmentView`, `ProfileShape`, `ProfileView`. Routes: `POST /v1/do/documents` (201, `Location`, `ETag`; `Idempotency-Key`; `invoice.issue`), `GET /v1/do/documents` (`limit`, `cursor`; `invoice.read`), `GET /v1/do/documents/{document_id}` (`ETag`), `POST /v1/do/sequences` (201; `Idempotency-Key`; `company.manage`), `GET /v1/do/sequences`, `GET /v1/do/sequences/{sequence_id}`, `POST /v1/do/annulments` (201; `Idempotency-Key`; `company.manage`), `GET /v1/do/annulments/{annulment_id}`, `GET /v1/do/profile` (`company.read`; `ETag`), `PUT /v1/do/profile` (`company.manage`; `If-Match` to replace). The company of every route: header `Ledger-Company-Id`. `api.consumers`: `VAULT_STREAM`, `CERTIFICATE_UPLOADED`, `CERTIFICATE_ROTATED`, the durable names `fiscal-do-emissions`, `fiscal-do-annulments`, `fiscal-do-certificate-uploads`, `fiscal-do-certificate-rotations`, `EventWithoutCompanyError`, `consumers(runtime)`. `api.workers`: `WATCHED`, `WorkflowWorkerStoppedError`, `serve(worker)`, `PipelineGauges(registry)` with `observe(documents, now, *, alert_after)`, `workers(runtime)` (`workflows`, `sequence expiry`, `pipeline`). `main`: `build(settings) -> Service[FiscalSettings]` (checks `migrations`, `archive`, `temporal`), `create_app()`. `ledger_testing.serving`: `READY_WITHIN`, `free_port()`, `answers(base)`, `served(app)`. `ledger_testing.containers`: `TEMPORAL_IMAGE`, `TEMPORAL_PORT`, `temporal_dev()`, `temporal_address(container)`.

Re-check before starting: `Interfaces`, `Events`, `Runtime` and `Service.runtime()` (a method at `5ca3de2`) as landed; `company_in_header`'s parameters; `RemotePermissions(channel)` and `CachedPermissions(source)` as Milestone 2 Task 8 left them; `HttpKeys.opened()`; whether Milestone 2 moved `problems()` and its `Problem` model into the platform (then fiscal-do imports them and drops its own); how identity's tests run a service (`app.router.lifespan_context(app)` with `httpx.ASGITransport`, as here); vault's stream name (`VAULT`) and subjects (`vault.certificate.uploaded`, `vault.certificate.rotated`, M3 Task 10). The vault consumers need the `VAULT` stream to exist: in the stack vault declares it; the test declares it as vault does.

- [ ] **Step 1: Dependencies, layers, test containers and serving**

`services/fiscal-do/pyproject.toml` — `dependencies` gains `"fastapi>=0.143.0"` and `"structlog>=26.1.0"`. `libs/testing/pyproject.toml` — `dependencies` gains `"granian>=2.8.4"` (its embedded server serves the fake DGII to a client that cannot be handed a transport). Root: "fiscal-do is hexagonal" `layers = ["main", "api", "resources", "adapters", "workflows", "settings", "application", "domain"]`. Run: `uv lock && uv sync`.

`libs/testing/src/ledger_testing/containers.py` — beside LocalStack's:

```python
TEMPORAL_IMAGE: Final = (
    "temporalio/temporal:1.9.1@sha256:ad4c82c97bd12b417d1ea942610dbcd511afb250c4d5ed26c694009533df447e"
)
TEMPORAL_PORT: Final = 7233


def temporal_dev() -> DockerContainer:
    """Temporal's development server (in memory, namespace ``default``), ready once its frontend serves."""
    return (
        DockerContainer(TEMPORAL_IMAGE)
        .with_command("server start-dev --ip 0.0.0.0 --headless")
        .with_exposed_ports(TEMPORAL_PORT)
        .waiting_for(LogMessageWaitStrategy("Temporal Server:"))
    )


def temporal_address(container: DockerContainer) -> str:
    """``host:port`` of ``container``'s frontend."""
    return f"{container.get_container_host_ip()}:{container.get_exposed_port(TEMPORAL_PORT)}"
```

(The development server prints `Temporal Server:   0.0.0.0:7233` once its frontend serves: verified on 2026-10-12, a client connected and its health check answered.)

`libs/testing/src/ledger_testing/serving.py`:

```python
"""An ASGI application served over real HTTP on 127.0.0.1 for the length of a test (granian, embedded).

For a client a test cannot hand a transport, such as a service's own HTTP client of a fake it calls. Granian
marks its embedded server experimental; the tests use it for fakes only, never to serve a service under test
(those are called through ``httpx.ASGITransport``).
"""

import asyncio
import socket
from contextlib import asynccontextmanager
from typing import Final, Protocol
lazy from collections.abc import AsyncGenerator

import httpx
from granian.constants import Interfaces
from granian.server.embed import Server
from pydantic import TypeAdapter
lazy from starlette.types import ASGIApp

from ledger_testing.waiting import eventually

READY_WITHIN: Final = 5.0
_ADDRESS: Final = TypeAdapter(tuple[str, int])


class _Stoppable(Protocol):
    """What the tests ask of granian's embedded server, typed (granian leaves ``stop`` unannotated)."""

    def stop(self) -> None:
        """Ask the server to stop."""


def free_port() -> int:
    """A TCP port nothing listens on at the moment of asking."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return _ADDRESS.validate_python(probe.getsockname())[1]


async def answers(base: str) -> bool:
    """Whether anything answers HTTP at ``base``."""
    async with httpx.AsyncClient(base_url=base) as client:
        try:
            await client.get("/")
        except httpx.ConnectError:
            return False
    return True


@asynccontextmanager
async def served(app: ASGIApp) -> AsyncGenerator[str]:
    """``app`` served on 127.0.0.1; its base URL (``http://127.0.0.1:<port>``), once it answers."""
    port = free_port()
    server = Server(app, address="127.0.0.1", port=port, interface=Interfaces.ASGINL, log_enabled=False)
    serving = asyncio.create_task(server.serve())
    base = f"http://127.0.0.1:{port}"
    try:
        await eventually(lambda: answers(base), within=READY_WITHIN)
        yield base
    finally:
        stoppable: _Stoppable = server
        stoppable.stop()
        await serving
```


- [ ] **Step 2: Write the failing tests**

`services/fiscal-do/tests/test_service.py`:

```python
"""fiscal-do started whole: its API, consumers and workflow worker, against PostgreSQL, NATS, LocalStack's S3,
Temporal's development server, the fake DGII over HTTP and vault and identity faked over gRPC."""

from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from typing import Final, final
lazy from collections.abc import AsyncGenerator, Generator

import httpx
import pytest
from google.protobuf.empty_pb2 import Empty
from grpc import StatusCode
from nats.aio.client import Client
from nats.js import JetStreamContext
from pydantic import SecretStr, TypeAdapter

from ledger_contracts.envelope import Envelope
from ledger_fiscal_do.adapters.archive import s3_client
from ledger_fiscal_do.adapters.postgres.codec import content_json
from ledger_fiscal_do.api.consumers import EventWithoutCompanyError, consumers
from ledger_fiscal_do.api.models import DocumentView, ProfileView, SequencePage, SequenceView
from ledger_fiscal_do.domain.vocabulary import EcfType
from ledger_fiscal_do.main import build
from ledger_fiscal_do.settings import FiscalSettings
from ledger_kernel.ids import Tenant
from ledger_platform.authentication import organization_of
from ledger_platform.streams import StreamSpec, ensure_stream
from ledger_testing.containers import (
    localstack_address,
    localstack_s3,
    nats_address,
    nats_server,
    temporal_address,
    temporal_dev,
)
from ledger_testing.dgii import FakeDgii, Scripted, Status, TrustingCheck
from ledger_testing.issuer import PROVIDER_ORGANIZATION, SUBJECT, LocalIssuer
from ledger_testing.serving import served
from ledger_testing.waiting import eventually
lazy from ledger_fiscal_do.domain.content import Content
lazy from ledger_platform.database import Database
lazy from ledger_platform.service import Service

from remotes import remotes
from samples import CONTENTS, ISSUED_ON, REFERENCE, TENANT, expiry
lazy from remotes import Remotes

BUCKET: Final = "ledger-archive"
VAULT: Final = StreamSpec(name="VAULT", subjects=("vault.>",))
"""vault's stream, as vault declares it: the consumers of its events need it to exist."""
COMPANY: Final = Tenant(organization_of(PROVIDER_ORGANIZATION), TENANT.company)
POINT: Final = "0192f7a4-6b1e-7c3d-8e4f-5a6b7c8d9e0f"
BODY: Final = TypeAdapter(dict[str, object])
INVOICE: Final[dict[str, object]] = {
    "emission_point_id": POINT,
    "ecf_type": 31,
    "issued_on": "2026-10-12",
    "income": "01",
    "terms": 1,
    "payments": [{"method": 1, "amount": "1180.00"}],
    "buyer": {"tax_id": "131880738", "name": "COMPRADORA SRL"},
    "lines": [
        {"number": 1, "name": "Martillo", "kind": 1, "quantity": "1", "unit_price": "1000.00", "billing": 1}
    ],
}


@final
@dataclass(frozen=True, slots=True)
class Infrastructure:
    """What fiscal-do runs on beside PostgreSQL, started once for the session."""

    nats_url: str
    archive_endpoint: str
    temporal: str


@pytest.fixture(scope="session")
def infrastructure() -> Generator[Infrastructure]:
    """NATS, LocalStack's S3 with the archive's bucket locked in compliance mode, Temporal's dev server."""
    with (
        pytest.MonkeyPatch.context() as patch,
        nats_server() as nats_container,
        localstack_s3() as s3_container,
        temporal_dev() as temporal_container,
    ):
        patch.setenv("AWS_ACCESS_KEY_ID", "test")
        patch.setenv("AWS_SECRET_ACCESS_KEY", "test")
        endpoint = localstack_address(s3_container)
        client = s3_client(region="us-east-1", endpoint_url=endpoint)
        client.create_bucket(Bucket=BUCKET, ObjectLockEnabledForBucket=True)
        client.put_object_lock_configuration(
            Bucket=BUCKET,
            ObjectLockConfiguration={
                "ObjectLockEnabled": "Enabled",
                "Rule": {"DefaultRetention": {"Mode": "COMPLIANCE", "Days": 1}},
            },
        )
        yield Infrastructure(nats_address(nats_container), endpoint, temporal_address(temporal_container))


@pytest.fixture
async def fakes() -> AsyncGenerator[tuple[Remotes, FakeDgii, str]]:
    dgii = FakeDgii(TrustingCheck())
    async with remotes() as faked, served(dgii.app) as dgii_url:
        faked.identity.organization = str(COMPANY.organization)
        faked.identity.grants[str(COMPANY.company), SUBJECT] = (
            "company.read",
            "company.manage",
            "invoice.read",
            "invoice.issue",
        )
        yield faked, dgii, dgii_url


@pytest.fixture
async def service(
    database: Database,
    database_url: str,
    infrastructure: Infrastructure,
    fakes: tuple[Remotes, FakeDgii, str],
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[tuple[Service[FiscalSettings], LocalIssuer]]:
    del database  # emptied after the test
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    connection = Client()
    await connection.connect(infrastructure.nats_url)
    await ensure_stream(JetStreamContext(connection), VAULT)
    await connection.close()
    faked, _, dgii_url = fakes
    async with LocalIssuer.started() as issuer:
        settings = FiscalSettings(
            environment="test",
            database_url=SecretStr(database_url),
            nats_url=SecretStr(infrastructure.nats_url),
            token_issuer=issuer.issuer,
            token_audience=issuer.audience,
            token_jwks_url=issuer.keys_url,
            identity_address=faked.address,
            vault_address=faked.address,
            temporal_address=infrastructure.temporal,
            dgii_ecf_url=dgii_url,
            dgii_fc_url=dgii_url,
            archive_bucket=BUCKET,
            archive_endpoint_url=infrastructure.archive_endpoint,
        )
        yield build(settings), issuer


@asynccontextmanager
async def running(service: Service[FiscalSettings]) -> AsyncGenerator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=service.app, raise_app_exceptions=False)
    async with (
        service.app.router.lifespan_context(service.app),
        httpx.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        yield client


def headers(issuer: LocalIssuer, key: str | None = None) -> dict[str, str]:
    sent = {"Authorization": f"Bearer {issuer.token()}", "Ledger-Company-Id": str(COMPANY.company)}
    return sent | ({} if key is None else {"Idempotency-Key": key})


async def prepared(client: httpx.AsyncClient, issuer: LocalIssuer) -> None:
    profile = {"environment": "testecf", "address": "Av. Duarte 123"}
    assert (await client.put("/v1/do/profile", json=profile, headers=headers(issuer))).status_code == 201
    sequence = {
        "ecf_type": 31,
        "emission_point_id": POINT,
        "first": 1,
        "last": 100,
        "expires_on": "2028-12-31",
    }
    created = await client.post("/v1/do/sequences", json=sequence, headers=headers(issuer, "range-1"))
    assert created.status_code == 201, created.text


async def test_a_document_is_issued_then_signed_sent_and_accepted(
    service: tuple[Service[FiscalSettings], LocalIssuer],
) -> None:
    fiscal, issuer = service
    async with running(fiscal) as client:
        assert (await client.get("/readyz")).status_code == 200
        await prepared(client, issuer)
        issued = await client.post("/v1/do/documents", json=INVOICE, headers=headers(issuer, "invoice-1"))
        assert issued.status_code == 201, issued.text
        again = await client.post("/v1/do/documents", json=INVOICE, headers=headers(issuer, "invoice-1"))
        assert (again.content, again.headers["Idempotent-Replayed"]) == (issued.content, "true")
        document = BODY.validate_json(issued.content)
        assert (document["encf"], document["state"]) == ("E310000000001", "numbered")
        location = issued.headers["Location"]

        async def accepted() -> bool:
            read = BODY.validate_json((await client.get(location, headers=headers(issuer))).content)
            return read["state"] == "accepted"

        await eventually(accepted, within=30)
        read = BODY.validate_json((await client.get(location, headers=headers(issuer))).content)
        assert isinstance(read["security_code"], str)
        assert str(read["qr_link"]).startswith(
            "https://ecf.dgii.gov.do/testecf/consultatimbre?RncEmisor=101850043"
        )
        listed = BODY.validate_json((await client.get("/v1/do/documents", headers=headers(issuer))).content)
        assert listed["next_cursor"] is None


async def test_dgii_s_rejection_and_an_annulment_are_recorded(
    service: tuple[Service[FiscalSettings], LocalIssuer], fakes: tuple[Remotes, FakeDgii, str]
) -> None:
    fiscal, issuer = service
    _, dgii, _ = fakes
    dgii.script.by_encf["E310000000001"] = Scripted(status=Status.REJECTED, messages=(("613", "No existe"),))
    async with running(fiscal) as client:
        await prepared(client, issuer)
        issued = await client.post("/v1/do/documents", json=INVOICE, headers=headers(issuer, "invoice-2"))
        location = issued.headers["Location"]
        listed = await client.get("/v1/do/sequences", headers=headers(issuer))
        first = SequencePage.model_validate_json(listed.content).items[0]
        annulled = await client.post(
            "/v1/do/annulments",
            json={"sequence_id": str(first.id), "first": 51, "last": 100},
            headers=headers(issuer, "annul-1"),
        )
        assert annulled.status_code == 201, annulled.text

        async def decided() -> bool:
            document = BODY.validate_json((await client.get(location, headers=headers(issuer))).content)
            annulment = BODY.validate_json(
                (await client.get(annulled.headers["Location"], headers=headers(issuer))).content
            )
            return (document["state"], annulment["state"]) == ("rejected", "accepted")

        await eventually(decided, within=30)
        sequence = await client.get(f"/v1/do/sequences/{first.id}", headers=headers(issuer))
        assert SequenceView.model_validate_json(sequence.content).available == 49


async def test_a_caller_needs_a_token_a_role_and_the_permission(
    service: tuple[Service[FiscalSettings], LocalIssuer], fakes: tuple[Remotes, FakeDgii, str]
) -> None:
    fiscal, issuer = service
    faked, _, _ = fakes
    faked.identity.grants[str(COMPANY.company), "2002"] = ("invoice.read",)
    async with running(fiscal) as client:
        assert (await client.get("/v1/do/documents")).status_code == 401
        company = {"Ledger-Company-Id": str(COMPANY.company)}
        reader = company | {"Authorization": f"Bearer {issuer.token(subject='2002')}"}
        stranger = company | {"Authorization": f"Bearer {issuer.token(subject='2003')}"}
        assert (await client.get("/v1/do/documents", headers=stranger)).status_code == 404
        refused = await client.post(
            "/v1/do/documents", json=INVOICE, headers=reader | {"Idempotency-Key": "k"}
        )
        assert BODY.validate_json(refused.content)["code"] == "permission_denied"


async def test_a_new_certificate_wakes_the_documents_that_wait_for_one(
    service: tuple[Service[FiscalSettings], LocalIssuer], fakes: tuple[Remotes, FakeDgii, str]
) -> None:
    fiscal, issuer = service
    faked, _, _ = fakes
    faked.vault.refusals[str(COMPANY.company)] = (StatusCode.FAILED_PRECONDITION, "no_active_certificate")
    async with running(fiscal) as client:
        await prepared(client, issuer)
        issued = await client.post("/v1/do/documents", json=INVOICE, headers=headers(issuer, "invoice-3"))
        location = issued.headers["Location"]

        async def state_is(state: str) -> bool:
            return (
                BODY.validate_json((await client.get(location, headers=headers(issuer))).content)["state"]
                == state
            )

        await eventually(lambda: state_is("blocked_certificate"), within=30)
        del faked.vault.refusals[str(COMPANY.company)]
        runtime = fiscal.runtime()
        handlers = {spec.subject: spec.handler for spec in consumers(runtime)}
        envelope = Envelope.wrap(Empty(), source="vault", tenant=COMPANY)
        async with runtime.database.transaction(COMPANY) as unit:
            await handlers["vault.certificate.uploaded"](envelope, unit)
        await eventually(lambda: state_is("accepted"), within=30)
        anonymous = Envelope.wrap(Empty(), source="vault", tenant=None)
        async with runtime.database.transaction(COMPANY) as unit:
            with pytest.raises(EventWithoutCompanyError):
                await handlers["vault.certificate.uploaded"](anonymous, unit)


def as_json(content: Content) -> dict[str, object]:
    """A sample's content as a client sends it: the shape ``NewDocument`` reads."""
    sent: dict[str, object] = {key: value for key, value in content_json(content).items() if key != "v"}
    return sent | {"emission_point_id": POINT}


async def test_every_type_is_signed_and_accepted_through_the_fake_dgii(
    service: tuple[Service[FiscalSettings], LocalIssuer], fakes: tuple[Remotes, FakeDgii, str]
) -> None:
    fiscal, issuer = service
    _, dgii, _ = fakes
    async with running(fiscal) as client:
        await prepared(client, issuer)
        for ecf_type in EcfType:
            if ecf_type is not EcfType.CREDIT_INVOICE:
                dated = expiry(ecf_type)
                sequence = {
                    "ecf_type": int(ecf_type),
                    "emission_point_id": POINT,
                    "first": 1,
                    "last": 100,
                    "expires_on": None if dated is None else dated.isoformat(),
                }
                created = await client.post(
                    "/v1/do/sequences", json=sequence, headers=headers(issuer, f"range-{int(ecf_type)}")
                )
                assert created.status_code == 201, created.text
        invoice = await client.post("/v1/do/documents", json=INVOICE, headers=headers(issuer, "type-31"))
        original = DocumentView.model_validate_json(invoice.content).encf
        reference = replace(REFERENCE, modified=original, issued_on=ISSUED_ON)
        locations = [invoice.headers["Location"]]
        for ecf_type in EcfType:
            if ecf_type is EcfType.CREDIT_INVOICE:
                continue
            content = CONTENTS[ecf_type]
            if ecf_type in {EcfType.DEBIT_NOTE, EcfType.CREDIT_NOTE}:
                content = replace(content, reference=reference)
            issued = await client.post(
                "/v1/do/documents", json=as_json(content), headers=headers(issuer, f"type-{int(ecf_type)}")
            )
            assert issued.status_code == 201, (ecf_type, issued.text)
            locations.append(issued.headers["Location"])

        async def all_accepted() -> bool:
            states = [
                DocumentView.model_validate_json(
                    (await client.get(where, headers=headers(issuer))).content
                ).state
                for where in locations
            ]
            return states == ["accepted"] * len(locations)

        await eventually(all_accepted, within=60)
    kinds = sorted({each.kind for each in dgii.received})
    assert kinds == ["ecf", "rfce"]


async def test_a_profile_is_read_and_replaced_at_the_version_named(
    service: tuple[Service[FiscalSettings], LocalIssuer],
) -> None:
    fiscal, issuer = service
    async with running(fiscal) as client:
        assert (await client.get("/v1/do/profile", headers=headers(issuer))).status_code == 404
        await prepared(client, issuer)
        read = await client.get("/v1/do/profile", headers=headers(issuer))
        assert (read.status_code, read.headers["ETag"]) == (200, '"1"')
        changed = {"environment": "testecf", "address": "Av. Duarte 124", "branch": "Sucursal 2"}
        again = await client.put("/v1/do/profile", json=changed, headers=headers(issuer))
        assert BODY.validate_json(again.content)["code"] == "fiscal_profile_exists"
        for stale in ('"7"', "*", '"1", "2"'):
            refused = await client.put(
                "/v1/do/profile", json=changed, headers=headers(issuer) | {"If-Match": stale}
            )
            assert refused.status_code == 412
        replaced = await client.put(
            "/v1/do/profile", json=changed, headers=headers(issuer) | {"If-Match": '"1"'}
        )
        assert (replaced.status_code, replaced.headers["ETag"]) == (200, '"2"')
        assert ProfileView.model_validate_json(replaced.content).branch == "Sucursal 2"
```


`services/fiscal-do/tests/test_workers.py`:

```python
import asyncio
from datetime import timedelta

import pytest
from prometheus_client import CollectorRegistry
from pydantic import BaseModel
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from ledger_fiscal_do.api.workers import PipelineGauges, WorkflowWorkerStoppedError, serve
from ledger_fiscal_do.application.ports import Watched
from ledger_fiscal_do.domain.document import DocumentState
from ledger_fiscal_do.domain.ids import DocumentId
from ledger_fiscal_do.main import create_app
from ledger_fiscal_do.workflows.emission import WORKFLOWS
from ledger_fiscal_do.workflows.orders import TASK_QUEUE
from ledger_kernel.ids import new_id

from samples import NOW, TENANT


class OpenApi(BaseModel):
    """What the test reads of the published document."""

    paths: dict[str, object]


def watched(state: DocumentState, waited: timedelta) -> Watched:
    return Watched(document=DocumentId(new_id()), tenant=TENANT, state=state, since=NOW - waited)


def test_a_pass_counts_documents_by_state_and_warns_of_a_long_contingency() -> None:
    registry = CollectorRegistry()
    gauges = PipelineGauges(registry)
    documents = (
        watched(DocumentState.SENT, timedelta(seconds=30)),
        watched(DocumentState.SENT, timedelta(seconds=90)),
        watched(DocumentState.CONTINGENCY, timedelta(hours=7)),
        watched(DocumentState.CONTINGENCY, timedelta(minutes=5)),
    )
    assert gauges.observe(documents, NOW, alert_after=timedelta(hours=6)) == 1
    sample = registry.get_sample_value
    assert sample("ledger_fiscal_do_documents_waiting", {"state": "sent"}) == 2
    assert sample("ledger_fiscal_do_oldest_waiting_seconds", {"state": "sent"}) == 90
    assert sample("ledger_fiscal_do_oldest_waiting_seconds", {"state": "numbered"}) == 0


async def test_a_worker_that_stops_is_reported_for_the_supervisor_to_start_again() -> None:
    async with await WorkflowEnvironment.start_time_skipping() as environment:
        worker = Worker(environment.client, task_queue=TASK_QUEUE, workflows=WORKFLOWS, activities=[])
        running = asyncio.create_task(serve(worker))
        await asyncio.sleep(0.5)
        await worker.shutdown()
        with pytest.raises(WorkflowWorkerStoppedError):
            await running


def test_the_app_is_built_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in {
        "LEDGER_DATABASE_URL": "postgresql+psycopg://fiscal_do:secret@localhost/fiscal_do",
        "LEDGER_TOKEN_ISSUER": "https://auth.example.com",
        "LEDGER_TOKEN_AUDIENCE": "ledger",
        "LEDGER_TOKEN_JWKS_URL": "https://auth.example.com/oauth/v2/keys",
        "LEDGER_IDENTITY_ADDRESS": "identity:50051",
        "LEDGER_VAULT_ADDRESS": "vault:50051",
        "LEDGER_TEMPORAL_ADDRESS": "temporal:7233",
        "LEDGER_ARCHIVE_BUCKET": "ledger-archive",
    }.items():
        monkeypatch.setenv(name, value)
    paths = set(OpenApi.model_validate(create_app().openapi()).paths)
    assert {"/v1/do/documents", "/v1/do/sequences", "/v1/do/annulments", "/v1/do/profile"} <= paths
```


`libs/testing/tests/test_serving.py`:

```python
import httpx
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from ledger_testing.serving import answers, free_port, served


async def hello(request: object) -> PlainTextResponse:
    del request
    return PlainTextResponse("hello")


async def test_an_app_is_served_over_real_http_until_the_block_ends() -> None:
    app = Starlette(routes=[Route("/hello", hello)])
    async with served(app) as base, httpx.AsyncClient(base_url=base) as client:
        assert (await client.get("/hello")).text == "hello"
    assert not await answers(base)


async def test_nothing_answers_on_a_free_port() -> None:
    assert not await answers(f"http://127.0.0.1:{free_port()}")
```


- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest services/fiscal-do/tests/test_service.py services/fiscal-do/tests/test_workers.py libs/testing/tests/test_serving.py -q --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'ledger_fiscal_do.api'`.

- [ ] **Step 4: Implement the resources and the API**

`services/fiscal-do/src/ledger_fiscal_do/resources.py`:

```python
"""What fiscal-do talks to beside the chassis, opened when it starts and closed when it stops.

One object (the chassis's resources hook yields one): the identity provider's keys, gRPC channels to identity
and vault, the permissions read through identity, Temporal's client and the archive, each opened in order and
closed in reverse. DGII's client is not one of them: it lives with the workflow worker, the only code that
calls DGII, whose metrics it reports.
"""

from contextlib import asynccontextmanager
from dataclasses import dataclass
from importlib.metadata import version
from typing import Final, final
lazy from collections.abc import AsyncGenerator

from temporalio.client import Client

from ledger_contracts.vault import IDEMPOTENT_METHODS as VAULT_READS
from ledger_fiscal_do.adapters.archive import S3Archive, s3_client
from ledger_fiscal_do.adapters.identity import GET_COMPANY, IdentityCompanies
from ledger_fiscal_do.adapters.vault import VaultSigner
from ledger_platform.authorization import CachedPermissions
from ledger_platform.grpc_client import Channel
from ledger_platform.identity_client import GET_PERMISSIONS, RemotePermissions
lazy from ledger_fiscal_do.settings import FiscalSettings
lazy from ledger_platform.authentication import HttpKeys

VERSION: Final = version("ledger-fiscal-do")


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class FiscalResources:
    """What the routes, handlers, workers and checks of a running fiscal-do use (see the module)."""

    permissions: CachedPermissions
    companies: IdentityCompanies
    signer: VaultSigner
    temporal: Client
    archive: S3Archive


@asynccontextmanager
async def opened(settings: FiscalSettings, *, keys: HttpKeys) -> AsyncGenerator[FiscalResources]:
    """Every resource of fiscal-do, open for the length of the block."""
    caller = settings.service_name
    identity = Channel(
        settings.identity_address, caller=caller, version=VERSION, idempotent=(GET_COMPANY, GET_PERMISSIONS)
    )
    vault = Channel(settings.vault_address, caller=caller, version=VERSION, idempotent=VAULT_READS)
    client = s3_client(region=settings.archive_region, endpoint_url=settings.archive_endpoint_url)
    async with (
        keys.opened(),
        identity.opened(),
        vault.opened(),
        S3Archive(client, settings.archive_bucket).opened() as archive,
    ):
        temporal = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)
        yield FiscalResources(
            permissions=CachedPermissions(RemotePermissions(identity.current())),
            companies=IdentityCompanies(identity),
            signer=VaultSigner(vault),
            temporal=temporal,
            archive=archive,
        )
```


`services/fiscal-do/src/ledger_fiscal_do/api/__init__.py`:

```python
"""fiscal-do's interfaces: its public HTTP API, the events it consumes, its workers."""
```


`services/fiscal-do/src/ledger_fiscal_do/api/context.py`:

```python
"""What every route of fiscal-do shares: the runtime, the caller, its access to the company, idempotency.

The company a request acts in is named by the ``Ledger-Company-Id`` header (fiscal-do's paths carry no
company); the caller's permissions there come from identity (``RemotePermissions``, cached 30 seconds and
dropped on a membership event), so a caller without a role in the company gets 404 ``company_not_found``.
"""

from dataclasses import dataclass
from http import HTTPStatus
from typing import Annotated, ClassVar, Final, final

from fastapi import Depends
from pydantic import BaseModel, ConfigDict

from ledger_fiscal_do.resources import FiscalResources
from ledger_platform.routes import authenticated, company_in_header
from ledger_platform.web import PROBLEM_JSON
lazy from ledger_fiscal_do.settings import FiscalSettings
lazy from ledger_platform.authentication import TokenVerifier
lazy from ledger_platform.authorization import PermissionSource
lazy from ledger_platform.idempotency import Idempotency
lazy from ledger_platform.routes import Caller, InCompany
lazy from ledger_platform.service import Runtime, RuntimeDependency


class Problem(BaseModel):
    """An error answer (RFC 9457): what every failure of the API holds."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")

    type: str
    title: str
    status: int
    code: str
    detail: str | None = None


_PROBLEM_SCHEMA: Final = Problem.model_json_schema()


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class ApiContext:
    """The dependencies every router shares, built once with the app."""

    runtime: RuntimeDependency[FiscalSettings]
    caller: Caller
    in_company: InCompany
    idempotency: Idempotency

    @classmethod
    def build(
        cls, *, runtime: RuntimeDependency[FiscalSettings], verifier: TokenVerifier, idempotency: Idempotency
    ) -> ApiContext:
        """The context of fiscal-do's routes: the caller from ``verifier``, its access from identity."""
        caller = authenticated(verifier)

        async def permission_source(
            current: Annotated[Runtime[FiscalSettings], Depends(runtime)],
        ) -> PermissionSource:
            return current.resources(FiscalResources).permissions

        return cls(
            runtime=runtime,
            caller=caller,
            in_company=company_in_header(caller, permission_source),
            idempotency=idempotency,
        )


def problems(*statuses: int) -> dict[int | str, dict[str, object]]:
    """The error answers an operation documents: problem documents, ``application/problem+json``."""
    answers: dict[int | str, dict[str, object]] = {
        status: {
            "description": HTTPStatus(status).phrase,
            "content": {PROBLEM_JSON: {"schema": _PROBLEM_SCHEMA}},
        }
        for status in statuses
    }
    return answers
```


`services/fiscal-do/src/ledger_fiscal_do/api/models.py`:

```python
"""The shapes of fiscal-do's public API, translated to and from the domain (its anti-corruption layer).

What clients send has its unknown fields refused.

Amounts, quantities and prices travel as decimal strings (``"1180.00"``), never JSON numbers: a float cannot
hold every amount exactly. Codes are DGII's numbers (``ecf_type`` 31, ``billing`` 1); dates ISO 8601. What the
shapes check is the JSON's form only; every fiscal rule is the domain's, reported as ``ecf_invalid`` with each
field and the rule it breaks (never the value).
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, ClassVar, Final, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from ledger_fiscal_do.adapters.xml.qr import QrHosts, qr_link
from ledger_fiscal_do.domain.content import Buyer, Content, Line, Payment, Reference, Withholding
from ledger_fiscal_do.domain.sequence import NumberRange
from ledger_fiscal_do.domain.vocabulary import (
    OFFICIAL_ECF,
    OFFICIAL_FC,
    Billing,
    EcfType,
    IncomeType,
    ItemKind,
    Modification,
    PaymentMethod,
    PaymentTerms,
)
from ledger_kernel.countries.do import TaxId
from ledger_kernel.money import Currency, Money
lazy from ledger_fiscal_do.domain.annulment import Annulment
lazy from ledger_fiscal_do.domain.document import FiscalDocument
lazy from ledger_fiscal_do.domain.profile import FiscalProfile
lazy from ledger_fiscal_do.domain.sequence import EncfSequence
lazy from ledger_fiscal_do.domain.verdict import Outcome

QR_HOSTS: Final = QrHosts(ecf=OFFICIAL_ECF, fc=OFFICIAL_FC)
"""A QR code always names DGII's own hosts: whoever scans it checks the document with DGII."""
type DecimalText = Annotated[str, StringConstraints(pattern=r"^[0-9]{1,16}(\.[0-9]{1,4})?$", max_length=21)]
type Text = Annotated[str, StringConstraints(min_length=1, max_length=1000)]
type TaxIdText = Annotated[str, StringConstraints(pattern=r"^[0-9]{9}$|^[0-9]{11}$")]


class _Shape(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)


def _dop(text: str | None) -> Money | None:
    return None if text is None else Money.of(text, Currency.DOP)


def _tax_id(text: str | None) -> TaxId | None:
    return None if text is None else TaxId.parse(text)


class WithholdingShape(_Shape):
    """What the buyer withholds on a line: ITBIS and ISR, and whether it is a perception."""

    itbis: DecimalText | None = None
    isr: DecimalText | None = None
    perception: bool = False

    def domain(self) -> Withholding:
        """The domain's withholding."""
        return Withholding(itbis=_dop(self.itbis), isr=_dop(self.isr), perception=self.perception)


class LineShape(_Shape):
    """One line of the document."""

    number: Annotated[int, Field(ge=1, le=1000)]
    name: Text
    description: Text | None = None
    kind: ItemKind
    quantity: DecimalText
    unit: Annotated[int, Field(ge=1, le=99)] | None = None
    unit_price: DecimalText
    discount: DecimalText | None = None
    billing: Billing
    withholding: WithholdingShape | None = None

    def domain(self) -> Line:
        """The domain's line."""
        return Line(
            number=self.number,
            name=self.name,
            description=self.description,
            kind=self.kind,
            quantity=Decimal(self.quantity),
            unit=self.unit,
            unit_price=Decimal(self.unit_price),
            discount=_dop(self.discount),
            billing=self.billing,
            withholding=None if self.withholding is None else self.withholding.domain(),
        )


class PaymentShape(_Shape):
    """How much was paid, and how."""

    method: PaymentMethod
    amount: DecimalText

    def domain(self) -> Payment:
        """The domain's payment."""
        return Payment(method=self.method, amount=Money.of(self.amount, Currency.DOP))


class BuyerShape(_Shape):
    """The buyer, as the document names it (what a type requires is the domain's rule)."""

    tax_id: TaxIdText | None = None
    foreign_id: Text | None = None
    name: Text | None = None
    contact: Text | None = None
    email: Text | None = None
    address: Text | None = None
    municipality: Text | None = None
    province: Text | None = None
    country: Text | None = None

    def domain(self) -> Buyer:
        """The domain's buyer."""
        return Buyer(
            tax_id=_tax_id(self.tax_id),
            foreign_id=self.foreign_id,
            name=self.name,
            contact=self.contact,
            email=self.email,
            address=self.address,
            municipality=self.municipality,
            province=self.province,
            country=self.country,
        )


class ReferenceShape(_Shape):
    """The document a note modifies."""

    modified: Annotated[str, StringConstraints(min_length=11, max_length=19)]
    issued_on: date
    code: Modification
    reason: Text | None = None
    other_taxpayer: TaxIdText | None = None

    def domain(self) -> Reference:
        """The domain's reference."""
        return Reference(
            modified=self.modified,
            issued_on=self.issued_on,
            code=self.code,
            reason=self.reason,
            other_taxpayer=_tax_id(self.other_taxpayer),
        )


class NewDocument(_Shape):
    """A document to issue: its emission point and what it says."""

    emission_point_id: UUID
    ecf_type: EcfType
    issued_on: date
    income: IncomeType | None = None
    terms: PaymentTerms | None = None
    deadline: date | None = None
    payments: Annotated[tuple[PaymentShape, ...], Field(max_length=7)] = ()
    prices_include_itbis: bool = False
    buyer: BuyerShape | None = None
    lines: Annotated[tuple[LineShape, ...], Field(min_length=1, max_length=1000)]
    reference: ReferenceShape | None = None

    def content(self) -> Content:
        """What the document says, as the domain reads it."""
        return Content(
            ecf_type=self.ecf_type,
            issued_on=self.issued_on,
            income=self.income,
            terms=self.terms,
            deadline=self.deadline,
            payments=tuple(payment.domain() for payment in self.payments),
            prices_include_itbis=self.prices_include_itbis,
            buyer=None if self.buyer is None else self.buyer.domain(),
            lines=tuple(line.domain() for line in self.lines),
            reference=None if self.reference is None else self.reference.domain(),
        )


class MessageView(_Shape):
    """One message of DGII's, verbatim."""

    code: str
    text: str


class OutcomeView(_Shape):
    """DGII's final answer."""

    verdict: str
    messages: list[MessageView]
    decided_at: datetime

    @classmethod
    def of(cls, outcome: Outcome) -> Self:
        """The view of ``outcome``."""
        return cls(
            verdict=outcome.verdict.value,
            messages=[MessageView(code=message.code, text=message.text) for message in outcome.messages],
            decided_at=outcome.decided_at,
        )


class DocumentView(_Shape):
    """A document: its e-NCF, where it stands, its totals and, once signed, its security code and QR link."""

    id: UUID
    encf: str
    ecf_type: int
    state: str
    environment: str
    issued_on: date
    total: str
    itbis_total: str | None
    version: int
    security_code: str | None
    qr_link: str | None
    signed_at: datetime | None
    outcome: OutcomeView | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, document: FiscalDocument) -> Self:
        """The view of ``document``."""
        signature, outcome, itbis = document.signature, document.outcome, document.totals.itbis_total
        return cls(
            id=document.id,
            encf=str(document.encf),
            ecf_type=int(document.encf.ecf_type),
            state=document.state.value,
            environment=document.environment.value,
            issued_on=document.content.issued_on,
            total=format(document.totals.total.amount, "f"),
            itbis_total=None if itbis is None else format(itbis.amount, "f"),
            version=document.version,
            security_code=None if signature is None else signature.security_code,
            qr_link=qr_link(document, QR_HOSTS),
            signed_at=None if signature is None else signature.signed_at,
            outcome=None if outcome is None else OutcomeView.of(outcome),
            created_at=document.created_at,
            updated_at=document.updated_at,
        )


class DocumentPage(_Shape):
    """A page of the company's documents."""

    items: list[DocumentView]
    next_cursor: str | None


class NewSequence(_Shape):
    """A range of e-NCF numbers DGII authorized (*Solicitud de Autorización de Secuencias*)."""

    ecf_type: EcfType
    emission_point_id: UUID
    first: Annotated[int, Field(ge=1, le=9_999_999_999)]
    last: Annotated[int, Field(ge=1, le=9_999_999_999)]
    expires_on: date | None = None

    def numbers(self) -> NumberRange:
        """Its numbers (``invalid_sequence_range`` when ``first`` is after ``last``)."""
        return NumberRange(self.first, self.last)


class SequenceView(_Shape):
    """A range, how many numbers it has left and the next one it hands out."""

    id: UUID
    ecf_type: int
    emission_point_id: UUID
    first: int
    last: int
    next: int
    available: int
    expires_on: date | None
    annulled: list[tuple[int, int]]
    version: int

    @classmethod
    def of(cls, sequence: EncfSequence) -> Self:
        """The view of ``sequence``."""
        return cls(
            id=sequence.id,
            ecf_type=int(sequence.ecf_type),
            emission_point_id=sequence.emission_point,
            first=sequence.numbers.first,
            last=sequence.numbers.last,
            next=sequence.next,
            available=sequence.available,
            expires_on=sequence.expires_on,
            annulled=[(each.first, each.last) for each in sequence.annulled],
            version=sequence.version,
        )


class SequencePage(_Shape):
    """A page of the company's ranges."""

    items: list[SequenceView]
    next_cursor: str | None


class NewAnnulment(_Shape):
    """Unused numbers of a range to withdraw, and tell DGII of (ANECF)."""

    sequence_id: UUID
    first: Annotated[int, Field(ge=1, le=9_999_999_999)]
    last: Annotated[int, Field(ge=1, le=9_999_999_999)]

    def numbers(self) -> NumberRange:
        """The numbers to withdraw."""
        return NumberRange(self.first, self.last)


class AnnulmentView(_Shape):
    """An annulment: its numbers and DGII's answer once given."""

    id: UUID
    sequence_id: UUID
    ecf_type: int
    first: int
    last: int
    state: str
    requested_at: datetime
    outcome: OutcomeView | None

    @classmethod
    def of(cls, annulment: Annulment) -> Self:
        """The view of ``annulment``."""
        outcome = annulment.outcome
        return cls(
            id=annulment.id,
            sequence_id=annulment.sequence,
            ecf_type=int(annulment.ecf_type),
            first=annulment.numbers.first,
            last=annulment.numbers.last,
            state=annulment.state.value,
            requested_at=annulment.requested_at,
            outcome=None if outcome is None else OutcomeView.of(outcome),
        )


class ProfileShape(_Shape):
    """A company's fiscal profile: its DGII environment and what its documents say of it beyond identity."""

    environment: Annotated[str, StringConstraints(pattern=r"^(testecf|certecf|ecf)$")]
    address: Text
    trade_name: Text | None = None
    branch: Text | None = None
    municipality: Text | None = None
    province: Text | None = None
    phones: Annotated[tuple[str, ...], Field(max_length=3)] = ()
    email: Text | None = None


class ProfileView(ProfileShape):
    """The profile, with its version (also its ``ETag``)."""

    version: int
    updated_at: datetime

    @classmethod
    def of(cls, profile: FiscalProfile) -> Self:
        """The view of ``profile``."""
        return cls(
            environment=profile.environment.value,
            address=profile.address,
            trade_name=profile.trade_name,
            branch=profile.branch,
            municipality=profile.municipality,
            province=profile.province,
            phones=profile.phones,
            email=profile.email,
            version=profile.version,
            updated_at=profile.updated_at,
        )
```


`services/fiscal-do/src/ledger_fiscal_do/api/documents.py`:

```python
"""``/v1/do/documents``: issue a document (numbered at once, signed and sent by its workflow), list, read.

``POST`` answers 201 with the document numbered: its e-NCF is the client's to print from that moment, and
``state`` tells where DGII's answer stands (``GET`` it again, or follow ``fiscal.document.*``). Issuing asks
identity for the company first (outside the transaction), then numbers, stores and announces in one
transaction, keyed by ``Idempotency-Key``: a retry answers the same document, never a second number.
"""

from http import HTTPStatus
from typing import Annotated, Final
lazy from uuid import UUID

from fastapi import APIRouter, Depends
lazy from fastapi import Request, Response

from ledger_fiscal_do.adapters.postgres.units import bind
from ledger_fiscal_do.adapters.xml.documents import DgiiXml
from ledger_fiscal_do.api.context import problems
from ledger_fiscal_do.api.models import DocumentPage, DocumentView
from ledger_fiscal_do.application import queries
from ledger_fiscal_do.application.issuing import IssueRequest, issue
from ledger_fiscal_do.domain.ids import DocumentId, EmissionPointId
from ledger_fiscal_do.resources import FiscalResources
from ledger_kernel.access import Permission
from ledger_platform.routes import answer, attempt_of, etag, idempotency_key, json_response, page_request
lazy from ledger_fiscal_do.api.context import ApiContext
lazy from ledger_fiscal_do.api.models import NewDocument
lazy from ledger_fiscal_do.settings import FiscalSettings
lazy from ledger_kernel.pagination import PageRequest
lazy from ledger_platform.authorization import CompanyAccess
lazy from ledger_platform.idempotency import StoredResponse
lazy from ledger_platform.service import Runtime

XML: Final = DgiiXml()


def router(context: ApiContext) -> APIRouter:
    """The documents' routes."""
    documents = APIRouter(prefix="/v1/do/documents", tags=["documents"])
    in_company, runtime = context.in_company, context.runtime

    @documents.post(
        "",
        status_code=HTTPStatus.CREATED,
        response_model=DocumentView,
        responses=problems(401, 403, 404, 409, 422, 503),
    )
    async def issue_document(
        body: NewDocument,
        request: Request,
        access: Annotated[CompanyAccess, Depends(in_company)],
        key: Annotated[str, Depends(idempotency_key)],
        current: Annotated[Runtime[FiscalSettings], Depends(runtime)],
    ) -> Response:
        """Issue a document: checked against its type's rules, numbered from the emission point's range."""
        access.require(Permission.INVOICE_ISSUE)
        issued = IssueRequest(emission_point=EmissionPointId(body.emission_point_id), content=body.content())
        company = await current.resources(FiscalResources).companies.company(access.tenant)
        attempt = attempt_of(request, access.principal, key, body)
        async with current.database.transaction(access.tenant) as transaction:

            async def numbered() -> StoredResponse:
                unit = bind(transaction, current.outbox)
                document = await issue(unit, issued, company=company, xml=XML)
                headers = {"Location": f"/v1/do/documents/{document.id}", "ETag": etag(document.version)}
                return json_response(HTTPStatus.CREATED, DocumentView.of(document), headers)

            outcome = await context.idempotency.once(transaction, attempt, numbered)
        return answer(outcome)

    @documents.get("", response_model=DocumentPage, responses=problems(401, 403, 404, 422))
    async def list_documents(
        access: Annotated[CompanyAccess, Depends(in_company)],
        page: Annotated[PageRequest, Depends(page_request)],
        current: Annotated[Runtime[FiscalSettings], Depends(runtime)],
    ) -> DocumentPage:
        """The company's documents, oldest first."""
        access.require(Permission.INVOICE_READ)
        async with current.database.transaction(access.tenant) as transaction:
            found = await queries.documents(bind(transaction, current.outbox), page)
        return DocumentPage(
            items=[DocumentView.of(each) for each in found.items], next_cursor=found.next_cursor
        )

    @documents.get("/{document_id}", response_model=DocumentView, responses=problems(401, 403, 404, 422))
    async def read_document(
        document_id: UUID,
        access: Annotated[CompanyAccess, Depends(in_company)],
        response: Response,
        current: Annotated[Runtime[FiscalSettings], Depends(runtime)],
    ) -> DocumentView:
        """A document, with its version as ``ETag``."""
        access.require(Permission.INVOICE_READ)
        async with current.database.transaction(access.tenant) as transaction:
            found = await queries.document(bind(transaction, current.outbox), DocumentId(document_id))
        response.headers["ETag"] = etag(found.version)
        return DocumentView.of(found)

    return documents
```


`services/fiscal-do/src/ledger_fiscal_do/api/sequences.py`:

```python
"""``/v1/do/sequences`` and ``/v1/do/annulments``: the e-NCF ranges DGII authorized, and their annulments.

Registering a range is the company's management (``company.manage``), as is withdrawing its unused numbers
(ANECF, which DGII is told of by a workflow); reading them is ``invoice.read``. A range never overlaps another
of its type (409 ``sequence_overlap``); only numbers not yet handed out can be annulled (409
``annulment_range_unavailable``), and never in DGII's certification environment, which has no such service.
"""

from http import HTTPStatus
from typing import Annotated
lazy from uuid import UUID

from fastapi import APIRouter, Depends
lazy from fastapi import Request, Response

from ledger_fiscal_do.adapters.postgres.units import bind
from ledger_fiscal_do.api.context import problems
from ledger_fiscal_do.api.models import AnnulmentView, SequencePage, SequenceView
from ledger_fiscal_do.application import queries, ranges
from ledger_fiscal_do.domain.ids import AnnulmentId, EmissionPointId, SequenceId
from ledger_fiscal_do.resources import FiscalResources
from ledger_kernel.access import Permission
from ledger_platform.routes import answer, attempt_of, etag, idempotency_key, json_response, page_request
lazy from ledger_fiscal_do.api.context import ApiContext
lazy from ledger_fiscal_do.api.models import NewAnnulment, NewSequence
lazy from ledger_fiscal_do.settings import FiscalSettings
lazy from ledger_kernel.pagination import PageRequest
lazy from ledger_platform.authorization import CompanyAccess
lazy from ledger_platform.idempotency import StoredResponse
lazy from ledger_platform.service import Runtime


def router(context: ApiContext) -> APIRouter:
    """The routes of ranges and annulments."""
    sequences = APIRouter(prefix="/v1/do", tags=["sequences"])
    in_company, runtime = context.in_company, context.runtime

    @sequences.post(
        "/sequences",
        status_code=HTTPStatus.CREATED,
        response_model=SequenceView,
        responses=problems(401, 403, 404, 409, 422),
    )
    async def register_sequence(
        body: NewSequence,
        request: Request,
        access: Annotated[CompanyAccess, Depends(in_company)],
        key: Annotated[str, Depends(idempotency_key)],
        current: Annotated[Runtime[FiscalSettings], Depends(runtime)],
    ) -> Response:
        """Register a range DGII authorized for one type at one emission point."""
        access.require(Permission.COMPANY_MANAGE)
        wanted = ranges.RangeRequest(
            ecf_type=body.ecf_type,
            emission_point=EmissionPointId(body.emission_point_id),
            numbers=body.numbers(),
            expires_on=body.expires_on,
        )
        attempt = attempt_of(request, access.principal, key, body)
        async with current.database.transaction(access.tenant) as transaction:

            async def registered() -> StoredResponse:
                sequence = await ranges.register(bind(transaction, current.outbox), wanted)
                headers = {"Location": f"/v1/do/sequences/{sequence.id}", "ETag": etag(sequence.version)}
                return json_response(HTTPStatus.CREATED, SequenceView.of(sequence), headers)

            outcome = await context.idempotency.once(transaction, attempt, registered)
        return answer(outcome)

    @sequences.get("/sequences", response_model=SequencePage, responses=problems(401, 403, 404, 422))
    async def list_sequences(
        access: Annotated[CompanyAccess, Depends(in_company)],
        page: Annotated[PageRequest, Depends(page_request)],
        current: Annotated[Runtime[FiscalSettings], Depends(runtime)],
    ) -> SequencePage:
        """The company's ranges."""
        access.require(Permission.INVOICE_READ)
        async with current.database.transaction(access.tenant) as transaction:
            found = await queries.sequences(bind(transaction, current.outbox), page)
        return SequencePage(
            items=[SequenceView.of(each) for each in found.items], next_cursor=found.next_cursor
        )

    @sequences.get(
        "/sequences/{sequence_id}", response_model=SequenceView, responses=problems(401, 403, 404, 422)
    )
    async def read_sequence(
        sequence_id: UUID,
        access: Annotated[CompanyAccess, Depends(in_company)],
        current: Annotated[Runtime[FiscalSettings], Depends(runtime)],
    ) -> SequenceView:
        """A range, how many numbers it has left and the next one."""
        access.require(Permission.INVOICE_READ)
        async with current.database.transaction(access.tenant) as transaction:
            found = await queries.sequence(bind(transaction, current.outbox), SequenceId(sequence_id))
        return SequenceView.of(found)

    @sequences.post(
        "/annulments",
        status_code=HTTPStatus.CREATED,
        response_model=AnnulmentView,
        responses=problems(401, 403, 404, 409, 422, 503),
    )
    async def annul_numbers(
        body: NewAnnulment,
        request: Request,
        access: Annotated[CompanyAccess, Depends(in_company)],
        key: Annotated[str, Depends(idempotency_key)],
        current: Annotated[Runtime[FiscalSettings], Depends(runtime)],
    ) -> Response:
        """Withdraw unused numbers of a range; DGII is told of them (ANECF) by the annulment's workflow."""
        access.require(Permission.COMPANY_MANAGE)
        numbers = body.numbers()
        company = await current.resources(FiscalResources).companies.company(access.tenant)
        attempt = attempt_of(request, access.principal, key, body)
        async with current.database.transaction(access.tenant) as transaction:

            async def withdrawn() -> StoredResponse:
                unit = bind(transaction, current.outbox)
                sequence = SequenceId(body.sequence_id)
                annulment = await ranges.annul(unit, sequence, numbers, company=company)
                headers = {"Location": f"/v1/do/annulments/{annulment.id}"}
                return json_response(HTTPStatus.CREATED, AnnulmentView.of(annulment), headers)

            outcome = await context.idempotency.once(transaction, attempt, withdrawn)
        return answer(outcome)

    @sequences.get(
        "/annulments/{annulment_id}", response_model=AnnulmentView, responses=problems(401, 403, 404, 422)
    )
    async def read_annulment(
        annulment_id: UUID,
        access: Annotated[CompanyAccess, Depends(in_company)],
        current: Annotated[Runtime[FiscalSettings], Depends(runtime)],
    ) -> AnnulmentView:
        """An annulment, and DGII's answer once given."""
        access.require(Permission.INVOICE_READ)
        async with current.database.transaction(access.tenant) as transaction:
            found = await queries.annulment(bind(transaction, current.outbox), AnnulmentId(annulment_id))
        return AnnulmentView.of(found)

    return sequences
```


`services/fiscal-do/src/ledger_fiscal_do/api/profile.py`:

```python
"""``/v1/do/profile``: the company's fiscal profile, which every document it issues names.

``PUT`` without ``If-Match`` creates the first profile (409 ``fiscal_profile_exists`` when there is one);
with ``If-Match: "<version>"`` it replaces that version (412 when the profile changed meanwhile). A profile
holds no secret; its environment decides which of DGII's environments the company's documents go to.
"""

from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, Depends, Header
lazy from fastapi import Response

from ledger_fiscal_do.adapters.postgres.units import bind
from ledger_fiscal_do.api.context import problems
from ledger_fiscal_do.api.models import ProfileView
from ledger_fiscal_do.application import profiles
from ledger_fiscal_do.application.profiles import StaleProfileError
from ledger_fiscal_do.domain.profile import FiscalProfile
from ledger_fiscal_do.domain.vocabulary import DgiiEnvironment
from ledger_kernel.access import Permission
from ledger_platform.routes import etag, parse_if_match, precondition_failed
lazy from ledger_fiscal_do.api.context import ApiContext
lazy from ledger_fiscal_do.api.models import ProfileShape
lazy from ledger_fiscal_do.settings import FiscalSettings
lazy from ledger_platform.authorization import CompanyAccess
lazy from ledger_platform.service import Runtime


def _replaces(value: str | None) -> int | None:
    """The one version ``If-Match`` names; none without the header; 412 for ``*`` or several."""
    if value is None:
        return None
    versions = parse_if_match(value).versions
    if versions is None or len(versions) != 1:
        raise precondition_failed()
    (version,) = versions
    return version


def router(context: ApiContext) -> APIRouter:
    """The profile's routes."""
    profile = APIRouter(prefix="/v1/do/profile", tags=["profile"])
    in_company, runtime = context.in_company, context.runtime

    @profile.get("", response_model=ProfileView, responses=problems(401, 403, 404))
    async def read_profile(
        access: Annotated[CompanyAccess, Depends(in_company)],
        response: Response,
        current: Annotated[Runtime[FiscalSettings], Depends(runtime)],
    ) -> ProfileView:
        """The company's fiscal profile, with its version as ``ETag``."""
        access.require(Permission.COMPANY_READ)
        async with current.database.transaction(access.tenant) as transaction:
            found = await profiles.profile(bind(transaction, current.outbox))
        response.headers["ETag"] = etag(found.version)
        return ProfileView.of(found)

    @profile.put("", response_model=ProfileView, responses=problems(401, 403, 404, 409, 412, 422))
    async def put_profile(
        body: ProfileShape,
        access: Annotated[CompanyAccess, Depends(in_company)],
        response: Response,
        current: Annotated[Runtime[FiscalSettings], Depends(runtime)],
        if_match: Annotated[str | None, Header(alias="If-Match", max_length=1024)] = None,
    ) -> ProfileView:
        """Create the company's profile, or replace the version ``If-Match`` names."""
        access.require(Permission.COMPANY_MANAGE)
        replaces = _replaces(if_match)
        async with current.database.transaction(access.tenant) as transaction:
            unit = bind(transaction, current.outbox)
            wanted = FiscalProfile(
                tenant=access.tenant,
                environment=DgiiEnvironment(body.environment),
                address=body.address,
                trade_name=body.trade_name,
                branch=body.branch,
                municipality=body.municipality,
                province=body.province,
                phones=body.phones,
                email=body.email,
                updated_at=unit.now,
            )
            try:
                stored = await profiles.put_profile(unit, wanted, replaces=replaces)
            except StaleProfileError as error:
                raise precondition_failed() from error
        response.status_code = HTTPStatus.OK if replaces is not None else HTTPStatus.CREATED
        response.headers["ETag"] = etag(stored.version)
        return ProfileView.of(stored)

    return profile
```


`services/fiscal-do/src/ledger_fiscal_do/api/rest.py`:

```python
"""fiscal-do's public API: every router, built once with the app."""

lazy from collections.abc import Sequence

lazy from fastapi import APIRouter

from ledger_fiscal_do.api import documents, profile, sequences
lazy from ledger_fiscal_do.api.context import ApiContext


def routes(context: ApiContext) -> Sequence[APIRouter]:
    """The routers of ``/v1/do``."""
    return (documents.router(context), sequences.router(context), profile.router(context))
```


- [ ] **Step 5: Implement the consumers, the workers and the composition root**

`services/fiscal-do/src/ledger_fiscal_do/api/consumers.py`:

```python
"""The events fiscal-do consumes: its own, to start workflows, and vault's, to wake documents that wait.

* ``fiscal.document.numbered`` starts the document's emission workflow; ``fiscal.annulment.requested`` the
  annulment's. Starting from the event, not from the request, makes the outbox the one record of what is to
  be done: a document committed is a workflow started, whatever crashed in between (Decision 7). Temporal
  refuses a second workflow by the same id, so a redelivered event starts nothing more.
* ``vault.certificate.uploaded`` and ``vault.certificate.rotated``: the company may sign again, and every
  document of it waiting for a certificate is signalled. (An annulment waiting looks again within the hour.)

Every handler reads the company from the envelope; an event without one is a defect, failed for good.
"""

from typing import ClassVar, Final
from uuid import UUID
lazy from collections.abc import Sequence

import ledger.events.fiscal_do.v1.events_pb2 as wire
from ledger_contracts import fiscal_do as subjects
from ledger_fiscal_do.adapters.events import FISCAL_DO
from ledger_fiscal_do.adapters.postgres.units import bind
from ledger_fiscal_do.adapters.temporal import TemporalEmissions
from ledger_fiscal_do.domain.ids import AnnulmentId, DocumentId
from ledger_fiscal_do.resources import FiscalResources
from ledger_kernel.errors import LedgerError
from ledger_platform.consumer import ConsumerSpec
lazy from ledger_contracts.envelope import Envelope
lazy from ledger_fiscal_do.settings import FiscalSettings
lazy from ledger_kernel.ids import Tenant
lazy from ledger_platform.database import UnitOfWork
lazy from ledger_platform.service import Runtime

VAULT_STREAM: Final = "VAULT"
"""vault's stream (``ledger_vault.adapters.events.VAULT``)."""
CERTIFICATE_UPLOADED: Final = "vault.certificate.uploaded"
CERTIFICATE_ROTATED: Final = "vault.certificate.rotated"
EMISSIONS: Final = "fiscal-do-emissions"
ANNULMENTS: Final = "fiscal-do-annulments"
UPLOADS: Final = "fiscal-do-certificate-uploads"
ROTATIONS: Final = "fiscal-do-certificate-rotations"


class EventWithoutCompanyError(LedgerError):
    """An event fiscal-do handles named no company: a defect of its publisher."""

    code: ClassVar[str] = "event_without_company"

    def __init__(self) -> None:
        super().__init__("an event fiscal-do handles names no company")


def _tenant(envelope: Envelope) -> Tenant:
    if envelope.tenant is None:
        raise EventWithoutCompanyError
    return envelope.tenant


def consumers(runtime: Runtime[FiscalSettings]) -> Sequence[ConsumerSpec]:
    """fiscal-do's durable consumers, over the running service."""
    resources = runtime.resources(FiscalResources)
    emissions = TemporalEmissions(resources.temporal, task_queue=runtime.settings.temporal_task_queue)

    async def numbered(envelope: Envelope, unit: UnitOfWork) -> None:
        del unit
        event = envelope.unwrap(wire.DocumentNumbered)
        await emissions.start(_tenant(envelope), DocumentId(UUID(event.document_id)))

    async def requested(envelope: Envelope, unit: UnitOfWork) -> None:
        del unit
        event = envelope.unwrap(wire.AnnulmentRequested)
        await emissions.annul(_tenant(envelope), AnnulmentId(UUID(event.annulment_id)))

    async def certificate(envelope: Envelope, unit: UnitOfWork) -> None:
        tenant = _tenant(envelope)
        waiting = await bind(unit, runtime.outbox).documents.blocked()
        await emissions.resume(tenant, waiting)

    return (
        ConsumerSpec(
            name=EMISSIONS, stream=FISCAL_DO.name, subject=subjects.DOCUMENT_NUMBERED, handler=numbered
        ),
        ConsumerSpec(
            name=ANNULMENTS, stream=FISCAL_DO.name, subject=subjects.ANNULMENT_REQUESTED, handler=requested
        ),
        ConsumerSpec(name=UPLOADS, stream=VAULT_STREAM, subject=CERTIFICATE_UPLOADED, handler=certificate),
        ConsumerSpec(name=ROTATIONS, stream=VAULT_STREAM, subject=CERTIFICATE_ROTATED, handler=certificate),
    )
```


`services/fiscal-do/src/ledger_fiscal_do/api/workers.py`:

```python
"""fiscal-do's background workers, supervised by the chassis (restarted after a failure).

* ``workflows``: the Temporal worker of fiscal-do's task queue, running ``EmitEcf`` and ``AnnulRange`` and the
  activities they call, with DGII's client (its breaker and metrics live here, the only code that calls
  DGII). One per replica; Temporal spreads the work.
* ``sequence expiry``: announces each range's expiry at 30, 7 and 1 days, once each, every
  ``expiry_check_seconds``.
* ``pipeline``: every ``pipeline_check_seconds``, how many documents wait in each state
  (``ledger_fiscal_do_documents_waiting{state}``) and how long the oldest has
  (``ledger_fiscal_do_oldest_waiting_seconds{state}``), and a warning for each document in contingency
  longer than ``contingency_alert_seconds`` (DGII allows 72 hours).
"""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import ClassVar, Final, Never, final
lazy from collections.abc import Mapping, Sequence

import structlog
from prometheus_client import Gauge
from temporalio.worker import Worker
lazy from prometheus_client import CollectorRegistry

from ledger_fiscal_do.adapters.dgii.client import DgiiClient, DgiiMetrics, DgiiOptions, DgiiTimeouts
from ledger_fiscal_do.adapters.dgii.endpoints import DgiiEndpoints
from ledger_fiscal_do.adapters.postgres.repositories import PgExpiringRanges, PgPipeline
from ledger_fiscal_do.adapters.postgres.units import PgUnits
from ledger_fiscal_do.adapters.xml.documents import DgiiXml
from ledger_fiscal_do.application import ranges
from ledger_fiscal_do.application.annulling import Annulling
from ledger_fiscal_do.application.emission import Emission
from ledger_fiscal_do.domain.document import DocumentState
from ledger_fiscal_do.resources import FiscalResources
from ledger_fiscal_do.workflows.activities import FiscalActivities
from ledger_fiscal_do.workflows.emission import WORKFLOWS
from ledger_kernel.clock import utc_now
from ledger_kernel.errors import UnavailableError
lazy from ledger_fiscal_do.application.ports import Watched
lazy from ledger_fiscal_do.settings import FiscalSettings
lazy from ledger_platform.service import Runtime
lazy from ledger_platform.supervision import Work

WATCHED: Final = 10_000
"""How many waiting documents a pipeline pass reads at most (the oldest)."""
_logger = structlog.stdlib.get_logger(__name__)


class WorkflowWorkerStoppedError(UnavailableError):
    """The Temporal worker returned: it is started again by the supervisor."""

    code: ClassVar[str] = "workflow_worker_stopped"

    def __init__(self) -> None:
        super().__init__("the workflow worker stopped")


async def serve(worker: Worker) -> Never:
    """Run ``worker`` until it fails or is shut down; either way the supervisor starts it again."""
    await worker.run()
    raise WorkflowWorkerStoppedError


@final
class PipelineGauges:
    """The pipeline's metrics, and what one pass over the waiting documents makes of them."""

    __slots__ = ("_oldest", "_waiting")

    def __init__(self, registry: CollectorRegistry) -> None:
        self._waiting: Gauge = Gauge(
            "ledger_fiscal_do_documents_waiting",
            "Documents DGII has not decided yet, by state.",
            ("state",),
            registry=registry,
        )
        self._oldest: Gauge = Gauge(
            "ledger_fiscal_do_oldest_waiting_seconds",
            "How long the oldest document in each state has waited.",
            ("state",),
            registry=registry,
        )

    def observe(self, documents: Sequence[Watched], now: datetime, *, alert_after: timedelta) -> int:
        """Set the gauges from ``documents``; warn of each one in contingency longer than ``alert_after``.

        How many were warned of.
        """
        for state in DocumentState:
            ages = [(now - each.since).total_seconds() for each in documents if each.state is state]
            self._waiting.labels(state.value).set(len(ages))
            self._oldest.labels(state.value).set(max(ages, default=0.0))
        late = [
            each
            for each in documents
            if each.state is DocumentState.CONTINGENCY and now - each.since > alert_after
        ]
        for each in late:
            _logger.warning("document_in_contingency", document_id=str(each.document))
        return len(late)


def workers(runtime: Runtime[FiscalSettings]) -> Mapping[str, Work]:
    """fiscal-do's workers, over the running service; their metrics registered once."""
    settings, resources = runtime.settings, runtime.resources(FiscalResources)
    units = PgUnits(runtime.database, runtime.outbox)
    dgii_metrics = DgiiMetrics(runtime.registry)
    gauges = PipelineGauges(runtime.registry)

    async def workflows() -> Never:
        endpoints = DgiiEndpoints(ecf=settings.dgii_ecf_url, fc=settings.dgii_fc_url)
        timeouts = DgiiTimeouts(connect=settings.dgii_connect_seconds, total=settings.dgii_call_seconds)
        dgii = DgiiClient(
            resources.signer, DgiiOptions(endpoints=endpoints, timeouts=timeouts), metrics=dgii_metrics
        )
        async with dgii.opened():
            signer, xml, archive = resources.signer, DgiiXml(), resources.archive
            activities = FiscalActivities(
                Emission(units=units, signer=signer, xml=xml, archive=archive, dgii=dgii, clock=utc_now),
                Annulling(units=units, signer=signer, xml=xml, archive=archive, dgii=dgii, clock=utc_now),
            )
            await serve(
                Worker(
                    resources.temporal,
                    task_queue=settings.temporal_task_queue,
                    workflows=WORKFLOWS,
                    activities=activities.all(),
                )
            )

    async def sequence_expiry() -> Never:
        candidates = PgExpiringRanges(runtime.database)
        while True:
            await ranges.announce_expiring(units, candidates, clock=utc_now)
            await asyncio.sleep(settings.expiry_check_seconds)

    async def pipeline() -> Never:
        watch = PgPipeline(runtime.database)
        alert_after = timedelta(seconds=settings.contingency_alert_seconds)
        while True:
            gauges.observe(await watch.oldest(WATCHED), datetime.now(UTC), alert_after=alert_after)
            await asyncio.sleep(settings.pipeline_check_seconds)

    return {"workflows": workflows, "sequence expiry": sequence_expiry, "pipeline": pipeline}
```


`services/fiscal-do/src/ledger_fiscal_do/main.py`:

```python
"""fiscal-do's composition root: settings, adapters and interfaces wired into one chassis ``Service``."""

lazy from collections.abc import Sequence
lazy from contextlib import AbstractAsyncContextManager

lazy from fastapi import APIRouter, FastAPI

from ledger_fiscal_do.adapters.events import FISCAL_DO
from ledger_fiscal_do.adapters.postgres.migrate import head, require_head
from ledger_fiscal_do.api.consumers import consumers
from ledger_fiscal_do.api.context import ApiContext
from ledger_fiscal_do.api.rest import routes
from ledger_fiscal_do.api.workers import workers
from ledger_fiscal_do.resources import VERSION, FiscalResources, opened
from ledger_fiscal_do.settings import FiscalSettings
from ledger_platform.authentication import HttpKeys, PublishedKeys, TokenPolicy, TokenVerifier
from ledger_platform.idempotency import Idempotency
from ledger_platform.service import Events, Interfaces, Service
lazy from ledger_platform.service import Runtime, RuntimeDependency


def build(settings: FiscalSettings) -> Service[FiscalSettings]:
    """The fiscal-do service ``settings`` describe: built, not started (its app's lifespan starts it).

    Its readiness checks beside the chassis's: ``migrations`` (the database is at this version's revision),
    ``archive`` (the bucket locks objects in compliance mode) and ``temporal`` (its frontend answers).
    """
    policy = TokenPolicy.of(settings)
    keys = HttpKeys()
    verifier = TokenVerifier(policy, keys=PublishedKeys(policy, fetch=keys))
    idempotency = Idempotency()
    expected = head()

    def http(runtime: RuntimeDependency[FiscalSettings]) -> Sequence[APIRouter]:
        return routes(ApiContext.build(runtime=runtime, verifier=verifier, idempotency=idempotency))

    def resources(current: FiscalSettings) -> AbstractAsyncContextManager[FiscalResources]:
        return opened(current, keys=keys)

    async def migrations(runtime: Runtime[FiscalSettings]) -> None:
        await require_head(runtime.database, expected)

    async def archive(runtime: Runtime[FiscalSettings]) -> None:
        await runtime.resources(FiscalResources).archive.require_locked()

    async def temporal(runtime: Runtime[FiscalSettings]) -> None:
        await runtime.resources(FiscalResources).temporal.service_client.check_health()

    return Service(
        settings=settings,
        version=VERSION,
        interfaces=Interfaces(
            http=http,
            resources=resources,
            checks={"migrations": migrations, "archive": archive, "temporal": temporal},
        ),
        events=Events(streams=(FISCAL_DO,), consumers=consumers, workers=workers),
    )


def create_app() -> FastAPI:
    """Granian's factory (``--factory``): the app of the service the ``LEDGER_*`` environment configures."""
    return build(FiscalSettings()).app
```


- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest services/fiscal-do/tests/test_service.py services/fiscal-do/tests/test_workers.py libs/testing/tests/test_serving.py -q --no-cov`
Expected: PASS in about a minute (PostgreSQL, NATS, LocalStack's S3 and Temporal's development server start once): fiscal-do started whole issues a document, its consumer starts the workflow, the worker signs through the fake vault, archives in S3 under object lock and sends through the fake DGII over HTTP until DGII accepts; every one of the ten types (and the consumer invoice's summary) is accepted that way; a rejection and an annulment are recorded; a document without a certificate waits and is woken by vault's event; a caller needs a token, a role and the permission.

- [ ] **Step 7: Gate**

Run: `uv run poe fmt && uv run poe check`
Expected: every step passes, coverage 100 %.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml uv.lock services/fiscal-do libs/testing
git commit -m "feat(fiscal-do): the /v1/do API, consumers that start workflows, workers, the service

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 13: The emission benchmark: build, validate and sign in under 100 ms

**Files:**
- Create: `services/fiscal-do/benchmarks/emit_latency.py`
- Modify: `pyproject.toml` (root: a `poe` task outside `check`, ruff's per-file ignores)

**Interfaces:**
- Consumes: `DgiiXml` (Task 4), `VaultSigner` (Task 8), `Channel` (M3 Task 3), the domain (Task 3), `ledger.vault.v1` (M3 Task 1).
- Produces: `uv run poe bench-fiscal-do`: prints p50, p95 and p99 of build + XSD validation + signature over gRPC + validation of the signed e-CF for a 60-line credit invoice (25 KB), 300 runs after 30 to warm up; exits 1 when p95 reaches 100 ms.

Not part of `poe check`, as vault's benchmark is not: timing assertions do not belong on shared CI machines. Run it before and after any change of the XML or signing path.

Re-check before starting: how Milestone 3 lints its `services/vault/benchmarks` (a per-file ignore of `INP001` for scripts run by path, or none if it named the directory otherwise): fiscal-do's follows it.

- [ ] **Step 1: The script**

`services/fiscal-do/benchmarks/emit_latency.py`:

```python
"""How long fiscal-do takes to build, check and sign an e-CF: the target is p95 under 100 ms (§11).

What is measured, for a credit invoice of 60 lines (25 KB), 300 times after 30 to warm up:

1. ``DgiiXml.ecf``: the e-CF written (stdlib ElementTree) and validated against DGII's schema (libxml2);
2. ``VaultSigner.sign``: a gRPC call over loopback to a vault stand-in that signs with RSA-2048 and SHA-256
   (``vault_shaped``);
3. ``DgiiXml.signed_ecf``: the signed e-CF validated again and its security code read.

Run: ``uv run python services/fiscal-do/benchmarks/emit_latency.py`` (exit 1 when p95 >= 100 ms). The
end-to-end figure with the real vault, PostgreSQL and KMS adds vault's measured p95 (< 50 ms, Milestone 3,
Task 14); this one isolates fiscal-do's share.
"""

import asyncio
import base64
import statistics
import sys
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from time import perf_counter
from typing import Final

import grpc
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
lazy from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey

from ledger.vault.v1.vault_pb2 import SignXmlRequest, SignXmlResponse
from ledger_fiscal_do.adapters.vault import VaultSigner
from ledger_fiscal_do.adapters.xml.documents import DgiiXml
from ledger_fiscal_do.domain.checking import checked
from ledger_fiscal_do.domain.content import Buyer, Content, Issuer, Line, Payment
from ledger_fiscal_do.domain.document import Draft
from ledger_fiscal_do.domain.encf import Encf
from ledger_fiscal_do.domain.ids import DocumentId, EmissionPointId, SequenceId
from ledger_fiscal_do.domain.vocabulary import (
    Billing,
    DgiiEnvironment,
    EcfType,
    IncomeType,
    ItemKind,
    PaymentMethod,
    PaymentTerms,
)
from ledger_kernel.countries.do import TaxId
from ledger_kernel.ids import CompanyId, OrganizationId, Tenant, new_id
from ledger_kernel.money import Currency, Money
from ledger_platform.grpc_client import Channel

LINES: Final = 60
WARM_UP: Final = 30
RUNS: Final = 300
TARGET_MS: Final = 100.0
DSIG: Final = "http://www.w3.org/2000/09/xmldsig#"
TENANT: Final = Tenant(OrganizationId(new_id()), CompanyId(new_id()))


def vault_shaped(document: bytes, key: RSAPrivateKey) -> bytes:
    """``document`` with an enveloped ``Signature`` last, its value a real RSA-2048 signature of its digest.

    Not DGII's XMLDSig (vault's own signer writes that, measured by Milestone 3 at p95 0.81 ms): the same
    work in kind, a SHA-256 digest and an RSA signature, and a ``Signature`` DGII's schema accepts.
    """
    value = base64.b64encode(key.sign(document, padding.PKCS1v15(), hashes.SHA256())).decode("ascii")
    signature = f'<Signature xmlns="{DSIG}"><SignedInfo/><SignatureValue>{value}</SignatureValue></Signature>'
    head, _, tail = document.rpartition(b"</")
    return head + signature.encode("ascii") + b"</" + tail


def signing_vault(key: RSAPrivateKey) -> grpc.GenericRpcHandler:
    """Vault's ``SignXml`` over gRPC, signing with ``key``; nothing else of vault's."""

    async def sign_xml(
        request: SignXmlRequest, context: grpc.aio.ServicerContext[object, object]
    ) -> SignXmlResponse:
        del context
        return SignXmlResponse(signed_document=vault_shaped(request.document, key))

    handler = grpc.unary_unary_rpc_method_handler(
        sign_xml,
        request_deserializer=SignXmlRequest.FromString,
        response_serializer=SignXmlResponse.SerializeToString,
    )
    return grpc.method_handlers_generic_handler("ledger.vault.v1.VaultService", {"SignXml": handler})


def _invoice() -> Content:
    item = Line(
        number=1,
        name="Martillo de carpintero",
        description="Mango de madera, cabeza de acero forjado",
        kind=ItemKind.GOOD,
        quantity=Decimal(2),
        unit=43,
        unit_price=Decimal("1000.0000"),
        billing=Billing.ITBIS_1,
    )
    lines = tuple(replace(item, number=number) for number in range(1, LINES + 1))
    total = Money.of("141600.00", Currency.DOP)
    return Content(
        ecf_type=EcfType.CREDIT_INVOICE,
        issued_on=date(2026, 10, 12),
        income=IncomeType.OPERATIONS,
        terms=PaymentTerms.CASH,
        payments=(Payment(method=PaymentMethod.CASH, amount=total),),
        buyer=Buyer(tax_id=TaxId.parse("131880738"), name="CONSTRUCTORA DEL CARIBE SRL"),
        lines=lines,
    )


async def main() -> int:
    """Measure, print, and fail when p95 misses the target."""
    content = _invoice()
    issuer = Issuer(
        tax_id=TaxId.parse("101850043"), legal_name="FERRETERIA DUARTE SRL", address="Av. Duarte 123"
    )
    now = datetime.now(UTC)
    draft = Draft(
        id=DocumentId(new_id()),
        tenant=TENANT,
        emission_point=EmissionPointId(new_id()),
        environment=DgiiEnvironment.PRECERTIFICATION,
        issuer=issuer,
        content=content,
        totals=checked(content),
        created_at=now,
    )
    document, _ = draft.numbered(
        Encf(EcfType.CREDIT_INVOICE, 1), SequenceId(new_id()), date(2028, 12, 31), now=now
    )
    xml = DgiiXml()
    server = grpc.aio.server()
    server.add_generic_rpc_handlers(
        (signing_vault(rsa.generate_private_key(public_exponent=65537, key_size=2048)),)
    )
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    timings: list[float] = []
    size = 0
    try:
        async with Channel(f"127.0.0.1:{port}", caller="fiscal-do", version="bench").opened() as channel:
            signer = VaultSigner(channel)
            for run in range(WARM_UP + RUNS):
                started = perf_counter()
                unsigned = xml.ecf(document, signed_at=now)
                size = len(unsigned)
                signed = await signer.sign(TENANT, unsigned, reference=str(document.encf))
                xml.signed_ecf(document, signed)
                if run >= WARM_UP:
                    timings.append((perf_counter() - started) * 1000)
    finally:
        await server.stop(None)
    p50, p95, p99 = (statistics.quantiles(timings, n=100)[index] for index in (49, 94, 98))
    report = f"{LINES} lines, {size} bytes: p50 {p50:.2f} ms, p95 {p95:.2f} ms, p99 {p99:.2f} ms"
    sys.stdout.write(f"build + validate + sign, {report}\n")
    return 0 if p95 < TARGET_MS else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
```


Root `pyproject.toml`:

```toml
bench-fiscal-do = { cmd = "python services/fiscal-do/benchmarks/emit_latency.py", help = "fiscal-do's build, validate and sign latency (not part of check)" }
```

and, unless Milestone 3 added it for vault's benchmark already:

```toml
"services/*/benchmarks/**" = ["INP001"]  # scripts run by path, not a package
```

in `[tool.ruff.lint.per-file-ignores]`. `services/fiscal-do/benchmarks` is linted and type-checked with the rest (`mypy services/fiscal-do` reads it too); it is outside `src`, so coverage and deptry do not.

- [ ] **Step 2: Run it**

Run: `uv run poe bench-fiscal-do`
Expected: exit 0. Measured on 2026-10-12 on the plan's scratch machine (Windows 11, CPython 3.15): p50 4.8-7.6 ms, p95 7.4-10.9 ms, p99 8.6-12.6 ms over three runs. Record the line in the task report with the machine. A p95 near the target means the XML path regressed: profile `DgiiXml.ecf` first (its XSD validation alone is about half a millisecond: Decision 5).

- [ ] **Step 3: Gate**

Run: `uv run poe fmt && uv run poe check`
Expected: every step passes.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml services/fiscal-do/benchmarks
git commit -m "test(fiscal-do): benchmark of build, validation and signature, p95 under 100 ms

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 14: The images, the local stack (Temporal, S3, the fake DGII) and CI

**Files:**
- Create: `services/fiscal-do/Dockerfile`, `libs/testing/fake-dgii.Dockerfile`, `deploy/local/fiscal-do.env`, `deploy/local/localstack/init-archive.sh`
- Modify: `services/fiscal-do/pyproject.toml`, `deploy/local/compose.yaml`, `deploy/local/postgres/init.sql`, `Tiltfile`, `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: `create_app` and `ledger-fiscal-do-migrate` (Tasks 9, 12); `ledger_testing.dgii.create_app` (Task 6); `ledger-probe` (M2 Task 17); identity's and vault's images and compose services, LocalStack's service with its `init-kms.sh` (M2 Task 17, M3 Task 15).
- Produces: images `ledger-fiscal-do:local` (distroless, `nonroot`, Granian on uvloop, health check on `/readyz`) and `ledger-fake-dgii:local`; compose services `temporal` (development server, pinned by digest, health-checked), `fake-dgii` (`127.0.0.1:8090`), `fiscal-do-migrate` (one-shot) and `fiscal-do` (`127.0.0.1:8002`); LocalStack serves S3 beside KMS, with the bucket `ledger-archive-local` locked in compliance mode at its start; database `fiscal_do` owned by role `fiscal_do`. CI builds both images, starts the stack and checks fiscal-do's readiness and its 401; the end-to-end emission of every type through the fake DGII runs in the test step (Task 12's `test_service.py`).

Re-check before starting: identity's and vault's `Dockerfile`s, compose services, `.dockerignore` and CI step as Milestones 2 and 3 left them (fiscal-do's follow them exactly but for the package and the port); the LocalStack service (it gains `s3` and a second init script); the HTTP ports identity (8000) and vault (8001) took (fiscal-do takes 8002).

- [ ] **Step 1: The images**

`services/fiscal-do/pyproject.toml` — its final form: the server added (no module imports it):

`services/fiscal-do/pyproject.toml`:

```toml
[project]
name = "ledger-fiscal-do"
version = "0.1.0"
description = "The fiscal-do service: Dominican Republic e-CF, from a commercial document to DGII's final answer."
requires-python = ">=3.15"
dependencies = [
    "ledger-kernel",
    "ledger-contracts",
    "ledger-dgii",
    "ledger-platform",
    "alembic>=1.20.0",
    "boto3>=1.43.111",
    "botocore>=1.43.111",
    "fastapi>=0.143.0",
    "granian[uvloop]>=2.8.4",
    "httpx>=0.28.1",
    "lxml>=6.1.3",
    "opentelemetry-api>=1.45.1",
    "prometheus-client>=0.26.0",
    "protobuf>=7.36.2",
    "pydantic>=2.14.1",
    "sqlalchemy>=2.1.4",
    "structlog>=26.1.0",
    "temporalio>=1.34.0",
]

[project.scripts]
ledger-fiscal-do-migrate = "ledger_fiscal_do.adapters.postgres.migrate:main"

[tool.uv.sources]
ledger-kernel = { workspace = true }
ledger-contracts = { workspace = true }
ledger-dgii = { workspace = true }
ledger-platform = { workspace = true }

[build-system]
requires = ["uv_build>=0.13.0,<0.14.0"]
build-backend = "uv_build"

[tool.deptry.package_module_name_map]
"ledger-contracts" = ["ledger_contracts", "ledger"]
"opentelemetry-api" = "opentelemetry"
"prometheus-client" = "prometheus_client"
"protobuf" = "google"

[tool.deptry.per_rule_ignores]
DEP002 = ["granian"]  # the ASGI server the image runs the app with; no module imports it
```


`.dockerignore` already leaves out `**/tests`; the fake DGII's image needs no test file either (`ledger_testing` is a library).

`services/fiscal-do/Dockerfile`:

```dockerfile
# syntax=docker/dockerfile:1
# The fiscal-do service: its API, its consumers and its Temporal worker in one process. Built from the
# workspace's lockfile with uv; run by Granian on uvloop, as an unprivileged user, from a file system it never
# writes to. Build from the repository root:
#   docker build -f services/fiscal-do/Dockerfile -t ledger-fiscal-do:local .

FROM ghcr.io/astral-sh/uv:0.13.0-trixie-slim AS build
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_INSTALL_DIR=/python \
    UV_PYTHON_PREFERENCE=only-managed \
    UV_PROJECT_ENVIRONMENT=/app/.venv
RUN uv python install 3.15.0
WORKDIR /src
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable --package ledger-fiscal-do

# Distroless: glibc, CA certificates and time zones, no shell; user nonroot (65532). lxml and temporalio bring
# their native parts in their wheels.
FROM gcr.io/distroless/cc-debian13:nonroot@sha256:e792ab3d241a468a4fd7519ddbbebe66b49b5f365771716ea688ad40b6c6f1c2
LABEL org.opencontainers.image.title="ledger-fiscal-do" \
      org.opencontainers.image.description="Dominican e-CF: numbering, XML, signing, archive, DGII" \
      org.opencontainers.image.source="https://github.com/balegre0/ledger"
COPY --from=build /python /python
COPY --from=build /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
USER nonroot
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=3 \
    CMD ["/app/.venv/bin/ledger-probe", "http://127.0.0.1:8000/readyz"]
ENTRYPOINT ["/app/.venv/bin/granian", "--interface", "asgi", "--factory", "--host", "0.0.0.0", "--port", "8000", \
            "--loop", "uvloop", "--workers", "1", "--no-access-log", "--respawn-failed-workers", \
            "ledger_fiscal_do.main:create_app"]
```


`libs/testing/fake-dgii.Dockerfile`:

```dockerfile
# syntax=docker/dockerfile:1
# The fake DGII of the local stack (ledger_testing.dgii.create_app): DGII's services for emitters, every schema
# checked, every signature trusted. Never deployed beyond a laptop or CI. Build from the repository root:
#   docker build -f libs/testing/fake-dgii.Dockerfile -t ledger-fake-dgii:local .

FROM ghcr.io/astral-sh/uv:0.13.0-trixie-slim AS build
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_INSTALL_DIR=/python \
    UV_PYTHON_PREFERENCE=only-managed \
    UV_PROJECT_ENVIRONMENT=/app/.venv
RUN uv python install 3.15.0
WORKDIR /src
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable --package ledger-testing

FROM gcr.io/distroless/cc-debian13:nonroot@sha256:e792ab3d241a468a4fd7519ddbbebe66b49b5f365771716ea688ad40b6c6f1c2
LABEL org.opencontainers.image.title="ledger-fake-dgii" \
      org.opencontainers.image.description="A fake of DGII's e-CF services, for the local stack and CI only"
COPY --from=build /python /python
COPY --from=build /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
USER nonroot
EXPOSE 8080
ENTRYPOINT ["/app/.venv/bin/granian", "--interface", "asgi", "--factory", "--host", "0.0.0.0", "--port", "8080", \
            "--workers", "1", "--no-access-log", "ledger_testing.dgii:create_app"]
```


Run: `docker build -f services/fiscal-do/Dockerfile -t ledger-fiscal-do:local . && docker build -f libs/testing/fake-dgii.Dockerfile -t ledger-fake-dgii:local .`
Expected: both images tagged; `docker image inspect ledger-fiscal-do:local --format '{{.Config.User}}'` prints `nonroot`.

- [ ] **Step 2: The local stack**

`deploy/local/postgres/init.sql` — append:

```sql
-- The fiscal-do service's own database and role, on the same terms as identity's and vault's.
CREATE ROLE fiscal_do LOGIN PASSWORD 'fiscal_do' NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
CREATE DATABASE fiscal_do OWNER fiscal_do;
```

`deploy/local/fiscal-do.env`:

```
# The fiscal-do service in the local stack: the fake DGII in place of DGII's hosts, LocalStack's S3 as the
# archive (any credentials do), Temporal's development server, identity and vault inside this network.
LEDGER_ENVIRONMENT=local
LEDGER_LOG_FORMAT=json
LEDGER_DATABASE_URL=postgresql+psycopg://fiscal_do:fiscal_do@postgres:5432/fiscal_do
LEDGER_NATS_URL=nats://nats:4222
LEDGER_TOKEN_ISSUER=http://zitadel.localhost:8080
LEDGER_TOKEN_AUDIENCE=ledger-local
LEDGER_TOKEN_JWKS_URL=http://zitadel.localhost:8080/oauth/v2/keys
LEDGER_IDENTITY_ADDRESS=identity:50051
LEDGER_VAULT_ADDRESS=vault:50051
LEDGER_TEMPORAL_ADDRESS=temporal:7233
LEDGER_DGII_ECF_URL=http://fake-dgii:8080
LEDGER_DGII_FC_URL=http://fake-dgii:8080
LEDGER_ARCHIVE_BUCKET=ledger-archive-local
LEDGER_ARCHIVE_ENDPOINT_URL=http://localstack:4566
AWS_ACCESS_KEY_ID=local
AWS_SECRET_ACCESS_KEY=local
```


`deploy/local/localstack/init-archive.sh`:

```sh
#!/bin/sh
# Runs inside LocalStack once its services are ready (/etc/localstack/init/ready.d): the bucket fiscal-do archives
# into, with object lock in compliance mode (a day by default; fiscal-do sets ten years on every object it keeps).
# LocalStack's state is not persisted: the archive starts empty at every start.
set -eu
awslocal s3api create-bucket --bucket ledger-archive-local --object-lock-enabled-for-bucket
awslocal s3api put-object-lock-configuration --bucket ledger-archive-local \
  --object-lock-configuration '{"ObjectLockEnabled": "Enabled", "Rule": {"DefaultRetention": {"Mode": "COMPLIANCE", "Days": 1}}}'
```


(committed with the executable bit: `git update-index --chmod=+x deploy/local/localstack/init-archive.sh`.)

`deploy/local/compose.yaml` — the `localstack` service serves S3 too and runs both init scripts:

```yaml
    environment:
      SERVICES: kms,s3
    volumes:
      - ./localstack/init-kms.sh:/etc/localstack/init/ready.d/init-kms.sh:ro
      - ./localstack/init-archive.sh:/etc/localstack/init/ready.d/init-archive.sh:ro
```

and these services follow vault's:

```yaml
  temporal:
    # Temporal's development server (in memory, namespace `default`) for fiscal-do's workflows: workflows do not
    # survive `docker compose down`; production runs Temporal Cloud or Temporal's Helm chart (§15).
    image: temporalio/temporal:1.9.1@sha256:ad4c82c97bd12b417d1ea942610dbcd511afb250c4d5ed26c694009533df447e
    command: ["server", "start-dev", "--ip", "0.0.0.0", "--headless"]
    healthcheck:
      test: ["CMD", "temporal", "operator", "cluster", "health", "--address", "127.0.0.1:7233"]
      interval: 5s
      timeout: 3s
      retries: 20

  fake-dgii:
    # DGII's services for emitters, faked (ledger_testing.dgii): schemas checked, signatures trusted. fiscal-do's
    # fiscal-do.env points DGII's hosts here; production refuses any host but DGII's own.
    image: ledger-fake-dgii:local
    build:
      context: ../..
      dockerfile: libs/testing/fake-dgii.Dockerfile
    ports: ["127.0.0.1:8090:8080"]
    read_only: true
    tmpfs: [/tmp]
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]

  fiscal-do-migrate:
    image: ledger-fiscal-do:local
    build:
      context: ../..
      dockerfile: services/fiscal-do/Dockerfile
    entrypoint: ["/app/.venv/bin/ledger-fiscal-do-migrate"]
    env_file: [fiscal-do.env]
    read_only: true
    tmpfs: [/tmp]
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]
    restart: "no"
    depends_on:
      postgres: {condition: service_healthy}

  fiscal-do:
    image: ledger-fiscal-do:local
    env_file:
      - fiscal-do.env
      - path: zitadel/.local/identity.env
        required: false
    ports: ["127.0.0.1:8002:8000"]
    read_only: true
    tmpfs: [/tmp]
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]
    depends_on:
      fiscal-do-migrate: {condition: service_completed_successfully}
      nats: {condition: service_healthy}
      localstack: {condition: service_healthy}
      temporal: {condition: service_healthy}
      identity: {condition: service_healthy}
      vault: {condition: service_healthy}
      fake-dgii: {condition: service_started}
```

(`zitadel/.local/identity.env` holds the local Zitadel's audience, `LEDGER_TOKEN_AUDIENCE`, which fiscal-do shares with identity: M2 Task 18.) Validated on 2026-10-12 with `docker compose config --quiet` against the landed compose file plus stand-ins for the services of Milestones 2 and 3.

`Tiltfile` — beside vault's:

```python
docker_build(
    "ledger-fiscal-do:local",
    ".",
    dockerfile="services/fiscal-do/Dockerfile",
    only=["pyproject.toml", "uv.lock", ".python-version", "libs", "services"],
    ignore=["**/tests", "**/benchmarks", "**/__pycache__", "**/.venv"],
)
docker_build(
    "ledger-fake-dgii:local",
    ".",
    dockerfile="libs/testing/fake-dgii.Dockerfile",
    only=["pyproject.toml", "uv.lock", ".python-version", "libs", "services"],
    ignore=["**/tests", "**/benchmarks", "**/__pycache__", "**/.venv"],
)

dc_resource("temporal", labels=["infrastructure"])
dc_resource("fake-dgii", labels=["infrastructure"])
dc_resource("fiscal-do-migrate", labels=["fiscal-do"])
dc_resource("fiscal-do", labels=["fiscal-do"], links=[link("http://localhost:8002/docs", "fiscal-do API")])
```

Run:

```bash
docker compose -f deploy/local/compose.yaml down -v
docker compose -f deploy/local/compose.yaml up -d --wait --build
curl --fail --silent --show-error http://127.0.0.1:8002/readyz
test "$(curl --silent --output /dev/null --write-out '%{http_code}' http://127.0.0.1:8002/v1/do/documents)" = 401
docker compose -f deploy/local/compose.yaml run --rm fiscal-do-migrate
docker compose -f deploy/local/compose.yaml down -v
```

Expected: every service healthy; fiscal-do's `/readyz` lists `database`, `nats`, `workers`, the chassis's tables, `migrations`, `archive` and `temporal`, all `true`; the documents route answers 401 without a token; the second migration exits 0.

- [ ] **Step 3: CI**

In `.github/workflows/ci.yml`, the local-stack step builds fiscal-do's two images too and checks fiscal-do:

```yaml
          $compose build identity-migrate vault-migrate fiscal-do-migrate fake-dgii
          $compose up -d --wait || { $compose logs; exit 1; }
          curl --fail --silent --show-error http://127.0.0.1:8002/readyz
          test "$(curl --silent --output /dev/null --write-out '%{http_code}' http://127.0.0.1:8002/v1/do/documents)" = 401
          test "$(docker image inspect ledger-fiscal-do:local --format '{{.Config.User}}')" = nonroot
          $compose run --rm fiscal-do-migrate
```

(in place of the step's `build` line, and after its existing checks). The test step already runs `test_service.py`, which emits every type through the fake DGII over HTTP with the workflow worker running: CI's smoke of the emission. If the job outgrows its `timeout-minutes`, raise it and say so in the report.

- [ ] **Step 4: Gate**

Run: `uv run poe fmt && uv run poe check && docker compose -f deploy/local/compose.yaml config --quiet`
Expected: every step passes; the compose file is valid.

- [ ] **Step 5: Commit**

```bash
git add Tiltfile .github deploy services/fiscal-do libs/testing/fake-dgii.Dockerfile uv.lock
git commit -m "build(fiscal-do): distroless image, local stack with Temporal, S3 object lock and a fake DGII

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 15: README and the spec brought in line

**Files:**
- Modify: `README.md`, `docs/superpowers/specs/2026-10-11-ledger-platform-design.md`

**Interfaces:**
- Consumes: Decisions 1-22 and what Tasks 1-14 built.
- Produces: documentation only.

Re-check before starting: the README and the spec as Milestones 2 and 3 left them (each edit below names the section it changes; keep their wording where it still holds, and change only what fiscal-do makes untrue or adds).

- [ ] **Step 1: README**

1. The opening paragraph's "What exists today" sentence names milestone 4: identity, vault and fiscal-do, which takes a company's documents to DGII (against a fake DGII locally and in CI; DGII's TesteCF waits for an enrolled taxpayer).
2. "Layout": add the rows

```markdown
| `libs/dgii` | `ledger_dgii`: DGII's fifteen XSDs vendored byte for byte, each pinned to its source and SHA-256, the corrections three of them need to compile, hardened validation (libxml2), and DGII's element names as its schemas declare them |
| `services/fiscal-do` | `ledger_fiscal_do`: Dominican e-CF. Ten types' rules, numbering under the range's lock, DGII's XML, signing through vault, a WORM archive on S3 object lock, DGII's client, Temporal workflows, `/v1/do/*` |
```

3. "The local stack": Temporal's development server, LocalStack's S3 (bucket `ledger-archive-local`, object lock in compliance mode), the fake DGII on `127.0.0.1:8090` and fiscal-do on `127.0.0.1:8002`; a short example of issuing a document:

```sh
TOKEN=$(sh deploy/local/zitadel/token.sh)
COMPANY=<a company's id, from identity>
curl -X PUT http://127.0.0.1:8002/v1/do/profile -H "Authorization: Bearer $TOKEN" \
  -H "Ledger-Company-Id: $COMPANY" -H 'Content-Type: application/json' \
  -d '{"environment": "testecf", "address": "Av. Duarte 123"}'
curl -X POST http://127.0.0.1:8002/v1/do/sequences -H "Authorization: Bearer $TOKEN" \
  -H "Ledger-Company-Id: $COMPANY" -H "Idempotency-Key: $(date +%s%N)" -H 'Content-Type: application/json' \
  -d '{"ecf_type": 31, "emission_point_id": "<an emission point id>", "first": 1, "last": 10000000, "expires_on": "2028-12-31"}'
```

then `POST /v1/do/documents` with a document (the request's shape is the OpenAPI document's `NewDocument`, at `http://127.0.0.1:8002/docs`), and `GET` it until `state` is `accepted`.

4. "Not done yet": add Decision 22's list (a run against TesteCF, additional taxes, other currencies, export and transport details, the gRPC `FiscalDo` service, emission points checked against identity's, revocation, ACECF and ARECF, xmlsec1 in the local stack's fake DGII).

- [ ] **Step 2: The spec**

1. §3, the shared libraries table: add `| dgii | DGII's published XSDs (vendored, pinned by SHA-256, corrected on load), validation, DGII's element names | kernel, lxml |`.
2. §4.3: "The workflow id is deterministic (`ecf-{company}-{document}`, `anecf-{company}-{annulment}`), so a duplicate start is impossible. fiscal-do starts each workflow from its own event (`fiscal.document.numbered`, `fiscal.annulment.requested`), so the outbox is the one record of work to do." (Decisions 6, 7).
3. §5.1: step 1 happens at `POST /v1/do/documents` in milestone 4 (and from `invoicing`'s `DocumentIssued` from milestone 5 on); step 2 is "fiscal-do's consumer of its own `fiscal.document.numbered` starts `EmitEcf`"; step a validates with libxml2 (xmlschema checks the same documents in the tests and in the fake DGII); step e's token is cached until five minutes before its hour ends. Add after the list: "A summary (RFCE) is decided at once; documents waiting for a certificate are woken by vault's `vault.certificate.uploaded` and `rotated` events."
4. §6, the tree: `adapters/xml/` writes with the standard library's ElementTree and names elements through `ledger_dgii.names`; `adapters/vault.py`, `adapters/identity.py`, `adapters/archive.py`, `adapters/temporal.py` in place of `vault_grpc.py` and `archive_s3.py`; `domain/errors.py` does not exist (each module holds its errors).
5. §7 (archive): "Object storage is S3 with object lock in compliance mode (AWS S3 in production, LocalStack locally); every signed document and DGII answer is locked for ten years from when it was kept; a key names its content's digest and is never overwritten."
6. §12: the XML row reads `lxml, xmlschema (tests and the fake DGII), signxml (tests) | 6.1 / 4.3 / 5.1 | libxml2 validation at runtime (0.5 ms for 60 lines), a second implementation in tests`; add `| Workflow server, local | temporalio/temporal (start-dev) | 1.9.1 | Temporal's development server, in memory |` and `| Object storage, local | LocalStack S3 | 4.14.0 | object lock in compliance mode, no account (MinIO's images are no longer published) |`.
7. §14, the workflows row: "Temporal's time-skipping test environment, a replay test of recorded histories; DGII faked with scripted outcomes and outages; every type emitted end to end through the fake over HTTP."
8. §16: milestone 4's row: "XML for all types, XSD validation, signing, archive, DGII auth, send and status, durable workflows; against a fake DGII (TesteCF when a taxpayer is enrolled)".
9. §17: the Temporal row is resolved: "Verified on 3.15 in milestone 4 (sandbox on, time skipping, replay); a `lazy` import of `temporalio.client` is avoided (an import-cycle on 3.15)". The types 46 and 47 row is resolved: "Named from the official XSDs (milestone 4): 46 *Comprobante de Exportaciones Electrónico*, 47 *Comprobante para Pagos al Exterior Electrónico*". Add a row: "DGII's XSDs as published do not all compile | Three are corrected on load, each correction pinned; a changed XSD fails its SHA-256".

- [ ] **Step 3: Gate**

Run: `uv run poe check`
Expected: every step passes (typos reads both files).

- [ ] **Step 4: Commit**

```bash
git add README.md docs/superpowers/specs/2026-10-11-ledger-platform-design.md
git commit -m "docs: README and spec in line with milestone 4 (fiscal-do)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## The fiscal-do service

What a client sees, once the plan is executed:

| Route | Permission | What it does |
|---|---|---|
| `PUT /v1/do/profile` | `company.manage` | creates the fiscal profile (DGII environment, address, branch, phones...), or replaces the version `If-Match` names |
| `GET /v1/do/profile` | `company.read` | the profile, `ETag` its version |
| `POST /v1/do/sequences` | `company.manage` | registers a range DGII authorized (dated unless type 32 or 34) |
| `GET /v1/do/sequences[/{id}]` | `invoice.read` | ranges, numbers left, the next one |
| `POST /v1/do/documents` | `invoice.issue` | checks, numbers, stores and announces a document; 201 with its e-NCF; DGII's answer follows |
| `GET /v1/do/documents[/{id}]` | `invoice.read` | where each stands; once signed, its security code and QR link; once decided, DGII's words |
| `POST /v1/do/annulments` | `company.manage` | withdraws unused numbers of a range and has DGII told (ANECF; not in CerteCF) |
| `GET /v1/do/annulments/{id}` | `invoice.read` | the annulment and DGII's answer |

Every route names its company in `Ledger-Company-Id`; every `POST` takes an `Idempotency-Key`. Events on stream `FISCAL_DO`: `fiscal.document.numbered|accepted|conditionally_accepted|rejected`, `fiscal.sequence.low|expiring`, `fiscal.annulment.requested|decided`. Metrics beyond the chassis's: `ledger_fiscal_do_dgii_calls_total{environment,service,outcome}`, `ledger_fiscal_do_dgii_call_seconds{environment,service}`, `ledger_fiscal_do_dgii_circuit_open{environment}`, `ledger_fiscal_do_documents_waiting{state}`, `ledger_fiscal_do_oldest_waiting_seconds{state}`.

## Self-review (for the plan author; already applied)

1. **Scope against the owner's list.** `services/fiscal-do`, hexagonal: Tasks 3-12 and the import-linter layers. FiscalDocument's state machine and EncfSequence with its 80 % warning and expiry: Task 3 (`document`, `sequence`), announced by Tasks 5 and 12. Ten type strategy modules: Task 3 (`domain/types`). lxml builder and XSD validation, golden files, property tests: Tasks 1 and 4 (the builder writes with the standard library's ElementTree, the validator is lxml's: lxml's element type cannot be annotated under basedpyright `all`; Global Constraints). Signing through vault's gRPC, security code and QR link: Tasks 4 and 8. WORM archive: Task 7 (S3 object lock; MinIO replaced: Decision 8). DGII's httpx client with token cache, circuit breaker, timeouts and retries only where idempotent: Task 6. A fake DGII with XSD validation, signature checks and scripted outcomes: Task 6 (xmlsec1 behind `SignatureCheck`; `TrustingCheck` in the stack). Temporal in M4 or M5: M4 (Decision 6, Task 11). REST API with idempotency, `invoice.issue`, RLS: Tasks 9, 10, 12. The events: Tasks 2, 3, 10. Benchmark p95 < 100 ms: Task 13. Dockerfile, compose and a CI smoke of every type through the fake: Tasks 12 and 14.
2. **Every code step is whole.** Each file is given entire, as it ran in the scratch workspace; the XSDs and the golden files are produced by the commands of Tasks 1 and 4 (DGII's files are never typed into a plan; the golden files are the samples' output, reviewed and checked by two validators).
3. **Names agree across tasks.** The ports of Task 4 are what Tasks 5-12 implement and call (`Emissions.start/resume/annul`, `Signer.sign/sign_seed`, `Dgii.submit_ecf/submit_summary/result/track_ids/annul`, `Archive.keep/read`, `Units.unit`); the activity names of `workflows.orders` are the names `FiscalActivities` registers and the workflows call (through its methods); the subjects of `ledger_contracts.fiscal_do` are those `adapters.events.outgoing` publishes and `api.consumers` reads.
4. **Gates per task.** Each task's tests cover what it adds, so `poe check` (100 % branch coverage) passes after each one: Task 4's `test_ports` covers the ports' refusals before their adapters exist; `ledger_testing`'s additions are covered by the tests of the task that adds them.
5. **What rests on unlanded milestones** is named in each task's "Re-check before starting", with the scratch copies it ran against (Global Constraints).

## Changelog

- 2026-10-12: first version. Research on DGII's site, the dependency checks on CPython 3.15, the Temporal and LocalStack probes and every line of code run in a scratch workspace the same day; the plan file was written once the workspace's gate (ruff, basedpyright, mypy, 396 tests, 100 % branch coverage) passed.
