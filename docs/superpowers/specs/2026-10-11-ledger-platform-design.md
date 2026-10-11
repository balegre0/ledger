# Ledger platform: base platform + Dominican Republic e-CF API

| | |
|---|---|
| Status | Draft for review |
| Date | 2026-10-11 |
| Name | `ledger` (provisional) |
| Scope of this spec | First sub-project: the platform base and electronic invoicing (e-CF) for the Dominican Republic, sold as an API |

## 1. Purpose

A business platform in the family of Odoo and Alegra, built to be sold, starting where demand is
forced by law: Dominican Republic electronic invoicing. Every taxpayer must emit e-CF; small, micro
and RST taxpayers by **15 November 2026**. About 64 % of current emitters use the free DGII
"facturador", capped at 150 invoices a month, so a large share of the market will outgrow it.

The first product is an **API**: software vendors, ERPs, POS systems and e-commerce stores send a
commercial invoice as JSON; the platform turns it into a valid, signed e-CF, sends it to DGII, follows
it to a final state, receives other emitters' e-CF on the company's behalf and notifies the client.
The platform is our own DGII integration (we certify as a PSFE, *Proveedor de Servicios de Facturación
Electrónica*), not a reseller of another provider.

The design must survive the next steps without a rewrite: more countries (Mexico CFDI, Colombia DIAN),
then accounting, inventory and a web app on the same base.

### Success criteria

1. A client company creates an account, uploads its digital certificate, and emits an accepted e-CF of
   every type (31, 32, 33, 34, 41, 43, 44, 45, 46, 47) in DGII's test environment through the API.
2. The platform passes DGII's certification as a PSFE and as an emitter/receiver on behalf of clients.
3. No accepted invoice is ever lost, duplicated, or given two e-NCF; no e-NCF is ever skipped silently.
4. A second country can be added as a new service without changing existing services.
5. The quality gate of ShieldBot holds in every package: strict typing in two checkers, every lint
   rule, 100 % branch coverage, zero suppressions.

### Non-goals for this sub-project

- A web UI for end users (the API, its docs and a minimal admin console only).
- Accounting, inventory, payroll, POS.
- Countries other than the Dominican Republic (their extension points are designed, not built).
- Billing our own customers automatically (manual invoicing until volume justifies it).

## 2. Architecture overview

```
                 clients (ERP, POS, stores)                 DGII
                          │ HTTPS                          ▲   │ HTTPS (other emitters, DGII)
                          ▼                                │   ▼
                ┌───────────────────┐             ┌──────────────────────┐
  Zitadel ◄────►│   Envoy Gateway   │             │   fiscal-receiver    │ public, per-company URLs
  (identity)    │ JWT, rate limits  │             └──────────┬───────────┘
                └─────────┬─────────┘                        │ events
                          │ HTTP (routes per service)        │
     ┌──────────┬─────────┼────────────┬───────────┐         │
     ▼          ▼         ▼            ▼           ▼         ▼
 identity   catalog   invoicing ──► fiscal-do ──► vault    webhooks
     │          │         │   gRPC     │   gRPC    │         │
     └──────────┴─────────┴─────┬──────┴───────────┴─────────┘
                                ▼
                        NATS JetStream (integration events)
     each service: its own PostgreSQL database · fiscal-do: Temporal workflows · object storage (archive)
```

Ten rules hold everywhere:

1. **A service owns its data.** No service reads another's database. Data crosses boundaries only
   through gRPC calls, events, or the public API.
2. **Commands are synchronous, facts are asynchronous.** gRPC for "do this now and tell me"; NATS
   events for "this happened".
3. **Every state change and the event announcing it commit together** (transactional outbox).
4. **Every consumer is idempotent** (inbox table keyed by event id).
5. **Every external write is idempotent** (`Idempotency-Key` on the public API; deterministic ids
   inside).
6. **Country rules live in a country service** (`fiscal-do`). `invoicing` knows commercial documents,
   never DGII.
7. **Private keys never leave `vault`.** Services ask `vault` to sign; nobody can export a key.
8. **Tenancy is checked twice**: in the application and by PostgreSQL row-level security.
9. **Contracts are versioned and checked for breaking changes in CI** (protobuf for gRPC and events).
10. **Everything is observable**: one trace from the client's HTTP call through events, workflows and
    DGII calls.

## 3. Services and data ownership

| Service | Owns | Exposes | Consumes |
|---|---|---|---|
| `identity` | organizations, companies (RNC, legal name, fiscal regime), branches, emission points, memberships, roles per company, API clients (mirrors Zitadel machine users) | public REST (`/v1/organizations`, `/v1/companies`...), gRPC `IdentityQuery` (company by id, permissions of a principal) | Zitadel webhooks (Actions v2) for user lifecycle |
| `catalog` | parties (customers and suppliers: RNC/cédula, names), products and services, tax configuration (ITBIS rates, exemptions, ISC), units | public REST, gRPC `CatalogQuery` | `identity.company.*` |
| `invoicing` | commercial documents (invoice, credit note, debit note), their lines, totals, lifecycle independent of any country | public REST (`/v1/invoices`...), gRPC `InvoicingQuery` | `fiscal.document.*` (to show fiscal status) |
| `fiscal-do` | e-NCF sequences and authorized ranges, e-CF XML, DGII tokens, track ids, fiscal states, contingency queue, RFCE summaries, ARECF/ACECF/ANECF, QR and security codes, legal archive index | public REST (`/v1/do/...`: sequences, fiscal status, received documents), gRPC `FiscalDo` | `invoicing.document.issued`, `fiscal.received.*` |
| `fiscal-receiver` | nothing durable but a short-lived semilla/token store; raw received documents go straight to object storage and an event | DGII-facing endpoints per company: `/fe/{rnc}/autenticacion/api/semilla`, `/validacioncertificado`, `/fe/recepcion/api/ecf`, `/fe/aprobacioncomercial/api/ecf` | gRPC `Vault.Sign`, `IdentityQuery` |
| `vault` | certificates (encrypted, envelope with KMS), their metadata (subject, RNC, validity), signing audit log | gRPC `Vault` (upload, describe, sign XML, sign semilla, rotate); no public API: uploads come through `identity` | `identity.company.deleted` |
| `webhooks` | client endpoints, secrets, delivery attempts | public REST (`/v1/webhooks`), test delivery | every public-facing event |

Future country services (`fiscal-mx`, `fiscal-co`) consume `invoicing.document.issued` exactly like
`fiscal-do` and ignore companies outside their country. Nothing else changes.

### Shared libraries (`libs/`)

| Library | Contains | May depend on |
|---|---|---|
| `kernel` | typed ids, `Money` and currency, `Rnc`/`Cedula` value objects with check digits, clock, the base domain exception hierarchy, pagination primitives | stdlib only |
| `contracts` | `.proto` files for gRPC services and event payloads, generated code and stubs, event envelope | `kernel`, protobuf, grpcio |
| `platform` | the service chassis: settings, logging, tracing, metrics, PostgreSQL session and unit of work, outbox relay, inbox, NATS client, gRPC server/client factories, HTTP app factory, health, lifecycle, authz context | `kernel`, `contracts`, third-party infrastructure libraries |

A service may depend on `libs/*`, never on another service. import-linter enforces it.

## 4. Communication

### 4.1 Events (NATS JetStream)

- Streams per producing service (`IDENTITY`, `INVOICING`, `FISCAL_DO`...), subjects
  `<service>.<aggregate>.<event>`: `invoicing.document.issued`, `fiscal.document.accepted`.
- Envelope: CloudEvents 1.0 attributes in NATS headers (`ce-id`, `ce-type`, `ce-source`,
  `ce-time`, `ce-subject` = company id, `traceparent`), payload as a versioned protobuf message
  (`ledger.events.invoicing.v1.DocumentIssued`).
- JetStream message de-duplication uses `Nats-Msg-Id` = event id (window 2 minutes); consumers still
  de-duplicate durably through the inbox.
- Durable pull consumers, explicit ack, `max_deliver` 10 with backoff 1 s → 10 min, then the message
  goes to a dead-letter stream with an alert.
- Ordering: only per aggregate matters; events carry the aggregate version and consumers reject a
  version gap by not acking (redelivery after the missing one arrives).

**Outbox.** In the same transaction as the state change, the service inserts the event into
`outbox`. A relay in the same process reads with `FOR UPDATE SKIP LOCKED`, publishes, waits for the
JetStream ack, marks it sent. PostgreSQL `LISTEN/NOTIFY` wakes the relay; a 1 s poll is the fallback.
Several replicas share the work safely.

**Inbox.** A consumer handles a message inside a transaction that inserts `(consumer, event_id)` into
`inbox`; a duplicate violates the primary key and is acked without effect.

### 4.2 Synchronous calls (gRPC)

- `grpc.aio` with generated stubs and `mypy-protobuf` types; one `.proto` package per service and
  major version (`ledger.fiscal_do.v1`).
- Deadlines always set by the caller and propagated; retries only on idempotent methods
  (declared in the service config), with hedging off.
- Errors use the gRPC rich error model (`google.rpc.Status` with `ErrorInfo` reasons such as
  `CERTIFICATE_EXPIRED`), mapped to domain exceptions by `platform`.
- Identity and tenant travel in metadata, signed by the gateway (see §8); services trust only that.
- mTLS between pods by the service mesh (Linkerd), so services hold no TLS code.

### 4.3 Long-running processes (Temporal)

Sending an e-CF is a process that lasts seconds to days: get a token, sign, send, poll the track id,
wait out a DGII outage, notify. Hand-written saga tables reinvent durable timers and retries badly.
`fiscal-do` runs these as **Temporal workflows** (deterministic workflow code, side effects in
activities). Temporal is internal to `fiscal-do`; other services still see only events and gRPC. The
workflow id is deterministic (`ecf-{company}-{encf}`), so a duplicate start is impossible.

## 5. Key flows

### 5.1 Emitting an e-CF

```
client ─POST /v1/invoices (Idempotency-Key)─► invoicing
invoicing: validate, compute totals, store document (status issued) + outbox DocumentIssued ─► 201
fiscal-do (consumer of DocumentIssued, company in DO):
  1. assign the next e-NCF of the document's type and emission point (row lock on the sequence,
     same transaction as the fiscal document + outbox FiscalNumberAssigned)
  2. start workflow EmitEcf(document id)
EmitEcf workflow:
  a. build the XML for the type (builder per type) and validate it against the official XSD
  b. Vault.Sign (XMLDSig enveloped, inclusive C14N, RSA-SHA256, Reference URI="")
  c. compute security code and QR URL; store the signed XML in the archive (object storage, WORM)
  d. if type 32 and total < RD$250,000: send the RFCE summary instead of the full e-CF
  e. get a DGII token (cached per company until shortly before expiry: semilla → signed → token)
  f. send; receive trackId; persist it
  g. poll status with backoff until final: Aceptado, Aceptado Condicional, Rechazado
  h. record the final state + outbox fiscal.document.{accepted|conditionally_accepted|rejected}
invoicing and webhooks consume the result; the client gets a webhook and can read the status.
```

- **Validation before sending.** Everything DGII checks that we can check locally is checked first
  (XSD, totals, RNC check digit, date rules, sequence range, certificate validity); a local failure
  never consumes a DGII round trip and is returned to the client synchronously at `POST`, before any
  e-NCF is assigned.
- **Rejected documents.** The e-NCF stays used (DGII rules); the client corrects through a new
  document. The state and DGII's messages are returned verbatim.
- **DGII outage (contingency).** Network errors and 5xx keep the workflow retrying with backoff; the
  document is valid to deliver to the buyer with its e-NCF and security code meanwhile. The workflow
  submits when DGII answers. An alert fires when any document waits over a configurable threshold.
- **Expired or missing certificate.** The workflow stops in `blocked_certificate`; uploading a new
  certificate signals all blocked workflows of the company to continue.

### 5.2 Receiving an e-CF from another emitter

```
other emitter ─► fiscal-receiver /fe/recepcion/api/ecf (with token from our semilla flow)
fiscal-receiver: verify the emitter's signature and that the buyer RNC is a company we serve;
                 store raw XML; publish fiscal.received.document; answer the ARECF (signed by vault)
fiscal-do: index the received document; publish for webhooks
client: approves or rejects commercially through the API ─► fiscal-do sends ACECF to the emitter
        and to DGII (workflow, same retry rules as emission)
```

### 5.3 Other processes

| Process | Trigger | Implementation |
|---|---|---|
| Annul unused e-NCF ranges (ANECF) | API call | workflow: build, sign, send, record |
| Credit/debit notes (33, 34) | `invoicing` document referencing another | same emission workflow, references validated against the archive |
| Sequence ranges | client registers the range DGII authorized | `fiscal-do` stores ranges per type, warns at 80 % and on expiry date |
| Certificate expiry | daily schedule | event 30, 7 and 1 day before expiry → webhook + email |

## 6. Inside `fiscal-do` (template for every service)

Hexagonal layout, enforced by import-linter:

```
services/fiscal-do/src/ledger_fiscal_do/
├── domain/          aggregates, value objects, state machine, rules: stdlib + kernel only
│   ├── document.py      FiscalDocument aggregate, its states and allowed transitions
│   ├── sequence.py      EncfSequence: ranges, next number, exhaustion
│   ├── types/           one module per e-CF type: required fields, validations
│   └── errors.py
├── application/     use cases (commands and queries), ports (Protocols), unit of work
├── workflows/       Temporal workflows (deterministic) and activities (call ports)
├── adapters/
│   ├── xml/             builders per type (lxml), XSD validation (xmlschema), QR/security code
│   ├── dgii/            DGII HTTP client: auth, send, status, RFCE, directory
│   ├── postgres/        repositories (SQLAlchemy 2.1 Core), migrations (Alembic)
│   ├── vault_grpc.py    signing port → vault
│   └── archive_s3.py    object storage
├── api/             REST routes (FastAPI) and gRPC servicer: thin, translate and delegate
└── main.py          composition root
```

Patterns, each for a reason:

| Pattern | Where | Why |
|---|---|---|
| Aggregate + explicit state machine | `FiscalDocument`, `EncfSequence` | Illegal transitions (accepted → pending) must be impossible, not just unlikely |
| Repository + Unit of Work | application ↔ postgres | One transaction for state, outbox and inbox; domain free of SQL |
| Strategy (registry by type code) | e-CF types, XML builders | Adding a type adds a module, touches no `if` chain |
| Builder | XML generation | Typed, ordered construction matching the XSD sequence |
| Adapter + Port (`Protocol`) | DGII, vault, storage, clock | Fakes in tests; DGII test/production environments switched by configuration |
| Transactional outbox / inbox | every service | Exactly-once effects over at-least-once delivery |
| Durable workflow (saga) | emission, approval, annulment | Retries, timers and compensation survive restarts |
| Circuit breaker | DGII client | An outage stops hammering DGII and is visible as a state |
| Anti-corruption layer | `invoicing` event → fiscal document | Commercial vocabulary never leaks DGII's, and vice versa |

DGII environments are configuration: `TesteCF`, `CerteCF`, `eCF` (production) per company, so a
company can certify while another runs in production.

## 7. Data

- PostgreSQL 18, one database per service, one role per service with access only to its database.
- SQLAlchemy 2.1 Core with typed tables (no ORM sessions in the domain); psycopg 3 driver (async,
  pipeline mode, binary protocol); Alembic migrations, reviewed and tested up and down in CI.
- Every tenant-owned table has `organization_id` and `company_id`; **row-level security** policies
  filter by the session variables that `platform` sets at the start of every transaction. A missing
  tenant context fails closed.
- Ids: UUIDv7 (time-ordered) for every entity, wrapped in typed `NewType`s per entity.
- Money: `Decimal` with currency, never float; DGII rounding rules applied in one place
  (`kernel.money`), with property tests.
- Times: `timestamptz` in UTC; DGII's local-time fields converted at the XML adapter only
  (`America/Santo_Domingo`).
- **Legal archive.** Signed XML (emitted and received) and DGII responses go to object storage with
  object lock (WORM, compliance mode) for the legal retention period (10 years, configurable; the
  exact period is confirmed against Ley 32-23 and Norma 01-2020 in the plan). PostgreSQL stores the
  index and hashes, never the only copy.
- Backups: point-in-time recovery for every database (managed PostgreSQL), restore tested monthly.

## 8. Identity, tenancy and authorization

- **Zitadel v4** (self-hosted, unmodified, AGPL-3.0 network use is fine because we do not modify it)
  is the identity provider: human users with MFA and passkeys; **machine users** with private-key JWT
  or client credentials for API clients.
- Organization in Zitadel = organization in `ledger`. Companies are ours (`identity` service), and
  roles are granted per company: `owner`, `admin`, `accountant`, `issuer`, `viewer`.
- **Envoy Gateway** validates the access token (JWKS from Zitadel), applies rate limits per client,
  and forwards the verified claims. Services do not re-validate tokens; they trust only headers set
  by the gateway, which strips any client-supplied copy.
- Authorization is decided in the service, per request: `platform` loads the principal's permissions
  for the target company through `IdentityQuery` (cached 30 s, invalidated by `identity.membership.*`
  events) and checks a typed permission (`Permission.INVOICE_ISSUE`).
- Every request carries one `company_id`; it sets the RLS context. Cross-company queries exist only
  in admin endpoints with a separate permission.

## 9. Security

| Concern | Measure |
|---|---|
| Certificate custody | `.p12` uploaded once, parsed in `vault`, private key encrypted with a per-certificate data key; the data key encrypted by the cloud KMS (envelope). The KMS key never leaves the KMS |
| Key use | Only `vault.Sign*` uses keys, in memory for the operation; every signature is logged (who, company, document hash) |
| Certificate checks | Subject RNC must match the company; validity window; chain to an accepted DR certification authority |
| Secrets | Kubernetes secrets from the cloud secret manager (External Secrets Operator); none in images or repos |
| Transport | TLS 1.3 at the edge; mTLS inside (Linkerd); DGII calls verify DGII's certificate |
| Public API | OAuth 2.0 only; idempotency keys; request size limits; JSON schemas strict (unknown fields rejected) |
| Webhooks | HMAC-SHA256 signature with timestamp (replay window 5 min); secrets rotatable with overlap |
| XML | lxml with entity resolution, DTDs and network access disabled; size limits before parsing |
| Supply chain | uv lockfile with hashes, Renovate, image signing (cosign), SBOM per image, vulnerability scan in CI |
| Audit | Append-only audit log per service for security-relevant actions, exported to the log store |
| Compliance | Reglamento 587-24 obligations for PSFE (incl. where data and servers are located) checked item by item before choosing the cloud region; see §13 |

## 10. Public API

- REST with JSON, OpenAPI 3.1 generated from the code and published; versioned in the path (`/v1`).
- Errors: RFC 9457 `application/problem+json` with stable `type` URIs and, for DGII rejections, the
  DGII codes and messages.
- `Idempotency-Key` required on every `POST`; stored 24 h with the response; a different body with
  the same key is a 422.
- Cursor pagination, `ETag` + `If-Match` on updates, ISO 8601 dates, amounts as strings.
- Webhook event types mirror public events (`invoice.fiscal.accepted`...), each with a version.
- SDKs generated from OpenAPI later (not in this sub-project).

## 11. Observability and operations

- **Traces:** OpenTelemetry in every service: HTTP, gRPC, SQL, NATS (context in headers), Temporal
  (interceptor), DGII calls. Exported over OTLP to the collector.
- **Metrics:** Prometheus: RED metrics per endpoint and RPC, outbox lag, consumer lag, DLQ size,
  workflow states, DGII latency and error rate per environment, certificates expiring.
- **Logs:** structlog JSON with trace id, company id, never personal data beyond ids, never XML
  bodies or keys.
- **SLOs:** API availability 99.9 %; p95 `POST /v1/invoices` < 300 ms (excluding DGII); 99 % of
  documents reach a final DGII state within 5 minutes when DGII is up.
- **Alerts:** SLO burn rate, DLQ non-empty, outbox lag > 1 min, DGII circuit open > 10 min,
  certificate expiring < 7 days, contingency queue age.
- Grafana dashboards as code; runbooks linked from every alert.

## 12. Stack

All verified installable on Python 3.15 (cp315 or abi3 wheels, or pure Python) on 2026-10-11.

| Concern | Choice | Version | Why this one |
|---|---|---|---|
| Language | Python 3.15 | | Lazy imports, latest typing |
| Packaging | uv workspace | | One lockfile, fast, already proven in ShieldBot |
| Public HTTP | FastAPI + Pydantic v2 | 0.143 / core 2.50 | Best OpenAPI output, typed models, mature ecosystem |
| ASGI server | Granian | 2.8 | Rust server, HTTP/2, fast, low memory |
| gRPC | grpcio (aio) + grpcio-tools + mypy-protobuf | 1.84 / 5.1 | Reference implementation; typed stubs |
| Protobuf toolchain | buf | | Lint and breaking-change checks |
| Database | PostgreSQL 18, SQLAlchemy 2.1 Core, psycopg 3, Alembic | 2.1.4 / 3.3 / 1.20 | Typed SQL, RLS, migrations |
| Events | NATS JetStream, nats-py | 2.16 | Light, persistent, de-duplication, simple ops |
| Workflows | Temporal (self-hosted or Temporal Cloud), temporalio | 1.34 | Durable sagas without hand-written state tables |
| XML | lxml, signxml, xmlschema | 6.1 / 5.1 / 4.3 | C14N + XMLDSig, XSD 1.0/1.1 validation |
| Crypto | cryptography | 50.0 | PKCS#12, RSA, envelope encryption |
| Identity | Zitadel v4, joserfc | 1.7 | OIDC, machine users, MFA; JOSE for client assertions |
| Gateway | Envoy Gateway | | Kubernetes Gateway API, JWT, rate limits |
| Observability | OpenTelemetry SDK, prometheus-client, structlog | 1.45 / 0.26 / 26.1 | Standards, same as ShieldBot |
| Serialization | orjson | 3.13 | Fast JSON for responses and logs |
| Event loop | uvloop | 0.23 | |
| HTTP client | httpx | 0.28 | DGII calls, HTTP/2, typed |

## 13. Repository, tooling and quality gate

```
ledger/
├── pyproject.toml             workspace root: shared tool configuration
├── uv.lock
├── libs/{kernel,contracts,platform}/
├── services/{identity,catalog,invoicing,fiscal-do,fiscal-receiver,vault,webhooks}/
│   └── <service>/{pyproject.toml, src/, tests/, migrations/, Dockerfile}
├── proto/                     .proto sources (buf module)
├── deploy/{helm/, local/}     Helm charts; compose + Tilt for local development
└── docs/                      architecture, ADRs, runbooks, specs
```

Quality gate (`uv run poe check`, every package, CI on Linux):

| Step | Tool |
|---|---|
| Format, lint | ruff (every rule) |
| Types | basedpyright `all` + mypy `--strict`, both with warnings as errors |
| Architecture | import-linter: libs never import services; services never import each other; hexagonal layers inside each service |
| Contracts | `buf lint`, `buf breaking` against `main` |
| Dependencies, spelling | deptry, typos |
| Tests | pytest + hypothesis, 100 % branch coverage, warnings are errors, random order |
| Security | pip-audit, image scan, secret scan |

Zero `type: ignore`, `noqa`, `pragma: no cover`. Generated protobuf code is excluded from linting but
its `.pyi` stubs are type-checked against.

## 14. Testing

| Level | What | How |
|---|---|---|
| Unit | domain, use cases with fakes | pytest, hypothesis (money, check digits, sequences, state machine) |
| XML | every e-CF type | golden files validated against DGII's XSDs; signatures verified by an independent verifier (xmlsec1) |
| Adapters | repositories, outbox, inbox, NATS, gRPC | testcontainers: real PostgreSQL and NATS |
| Workflows | emission, approval, annulment | Temporal test environment with time skipping; DGII fake with scripted outages |
| Contracts | protobuf, events, OpenAPI | buf breaking; schemathesis against the OpenAPI document |
| End to end | full stack | compose environment; nightly run against DGII's `TesteCF` with a test certificate |
| Load | emission throughput | k6 against staging before each release |

## 15. Deployment

- One OCI image per service (multi-stage, non-root, read-only file system, distroless-like base).
- Managed Kubernetes; Helm chart per service; Argo CD (GitOps) for staging and production.
- Managed PostgreSQL, NATS (Helm, 3 replicas, JetStream file storage), Temporal (Temporal Cloud or
  Helm), Zitadel (Helm), object storage with object lock, cloud KMS.
- Local development: `tilt up` builds and runs everything with live reload; compose for the
  infrastructure alone.
- Region chosen after the Reglamento 587-24 check (§9). Until that check, the design assumes no
  provider-specific service beyond Kubernetes, PostgreSQL, object storage and KMS, all available
  from every major cloud.

## 16. Delivery milestones

| # | Milestone | Done when |
|---|---|---|
| 1 | Foundations | workspace, gate, `kernel`, `contracts`, `platform` chassis, local stack (Tilt) |
| 2 | Identity | Zitadel integration, organizations, companies, roles, RLS end to end |
| 3 | Vault | certificate upload, envelope encryption, signing over gRPC, audit |
| 4 | Fiscal core | XML for all types, XSD validation, signing, DGII auth, send and status against `TesteCF` |
| 5 | Invoicing + workflows | public invoice API, emission workflow, outbox/inbox, webhooks |
| 6 | Reception | `fiscal-receiver`, ARECF, ACECF, received-documents API |
| 7 | Certification | DGII certification steps as emitter and PSFE; hardening, load tests, runbooks |
| 8 | Production | Helm, Argo CD, monitoring, first paying clients |

Each milestone gets its own implementation plan.

## 17. Risks and open points

| Risk | Mitigation |
|---|---|
| Microservices multiply operational work for a small team (chosen deliberately) | Shared chassis in `platform`, one Helm library chart, Tilt locally, managed services where possible |
| Temporal's Python sandbox on 3.15 is unproven | First task of milestone 5 is a spike; fallback is the workflow sandbox disabled with determinism enforced by tests, or Temporal's passthrough modules |
| DGII specs change (XSD versions, endpoints) | XSDs and endpoints versioned as data; nightly `TesteCF` run catches changes early |
| e-CF types 46 and 47 names and fields | Taken from the official XSDs when downloaded in milestone 4, not from secondary sources |
| PSFE certification requirements (3 certified client emitters, FI-GDF-017, Reglamento 587-24) | Tracked as milestone 7 checklist; find pilot clients during milestones 4-6 |
| Legal retention period and data location | Confirmed against the primary norms before milestone 8 |
| AGPL-3.0 (Zitadel) | Used unmodified as a separate service; no linking |
