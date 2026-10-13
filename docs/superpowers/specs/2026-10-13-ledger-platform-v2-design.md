# Ledger platform v2: a modular monolith for many countries and many features

| | |
|---|---|
| Status | Draft for owner review |
| Date | 2026-10-13 |
| Supersedes | [`2026-10-11-ledger-platform-design.md`](2026-10-11-ledger-platform-design.md) (v1). v1 stays in the tree as history; where the two disagree, this document wins. The milestone plans 3, 4 and 5 written against v1 are superseded (§16.3) |
| Binding inputs | `.superpowers/research/00-controller-rulings.md` (R1-R6); evidence in `01-localization`, `02-modularity`, `03-layout-tooling`, `04-saas-platforms`, `05-audit` (cited below as **[01]**...**[05]**, rulings as **R1**...**R6**) |
| Decisions recorded as | `docs/adr/0001`...`0010` (§17) |

> **Supersedes v1.** v1 chose seven microservices on NATS JetStream and Temporal with one database per service.
> v2 keeps v1's product, its DGII requirements, its security, observability and SLO rules, and the chassis
> capabilities proven in milestones 1 and 2, and changes the shape: one codebase, a uv workspace of small
> packages driven by manifests, modules talking in process, events and jobs on PostgreSQL, countries as
> regime and channel packs, a separate signer process. Nothing is in production, so the change is free now and
> expensive later.

---

## 1. Purpose, scope and goals

### 1.1 Product

A business platform in the family of Odoo and Alegra, built to be sold, starting where demand is forced by
law: Dominican Republic electronic invoicing (e-CF). Every taxpayer must emit e-CF; small, micro and RST
taxpayers by **15 November 2026**. About 64 % of current emitters use DGII's free "facturador", capped at 150
invoices a month, so a large share of the market will outgrow it.

The first product is an **API**: software vendors, ERPs, POS systems and online stores send a commercial
invoice as JSON; the platform turns it into a valid, signed e-CF, sends it to DGII, follows it to a final
state, receives other emitters' e-CF on the company's behalf and notifies the client. We are our own DGII
integration (we certify as a PSFE, *Proveedor de Servicios de Facturación Electrónica*), not a reseller.

After it, on the same base and without a rewrite: more countries (Mexico CFDI 4.0, Colombia DIAN UBL, Chile
SII DTE, Peppol BIS 3), more channels per country, and more features (parties and catalog, accounting,
inventory, a web app).

### 1.2 Goals

1. **Many countries.** A country is a package under `countries/<iso2>/` holding a *regime* (tax law as typed,
   effective-dated data) and one or more *channels* (an authority format + transport, versioned). Adding a
   country or a channel edits no module (§8).
2. **Many features.** A feature is a module under `modules/<name>/` with a manifest that declares its
   dependencies, permissions, events, jobs, schema and id prefixes. Adding a module edits no other module and
   no hand-maintained list (§4).
3. **Small team, small operations.** One image, five entrypoints, one PostgreSQL cluster, no broker, no
   workflow server (R1, R2).
4. **Top-tier quality, no duplication.** Each concept has one home; each list is generated from manifests;
   each package carries its own 100 % branch coverage (R6).
5. **Extractable later.** Any module can become a separate deployable by changing transports (event bus
   adapter, gRPC client adapter, its own database), never its domain (§3.9).

### 1.3 Success criteria

1. A client company creates an account, uploads its certificate and emits an accepted e-CF of every type (31,
   32, 33, 34, 41, 43, 44, 45, 46, 47) in DGII's `TesteCF` through the public API, in **test mode**.
2. The platform passes DGII's certification as a PSFE and as emitter/receiver on behalf of clients.
3. No accepted invoice is ever lost, duplicated or given two e-NCF; no e-NCF is ever skipped silently (every
   number is used by a document or formally voided).
4. A second country is added as `countries/<cc>/` plus manifest data: no module, no app and no other country
   changes (checked by `ledger-devtools affected`: the diff touches only the new directory and generated files).
5. The quality gate holds in every package: ruff ALL, basedpyright `all` + mypy `--strict`, zero suppressions,
   100 % branch coverage per package, TDD, reviews (R6).

### 1.4 Non-goals of the first product

- An end-user web UI (API, its documentation and a minimal operator console only).
- Accounting, inventory, payroll, POS (accounting's core rules are designed in §7.9, not built).
- Countries other than the Dominican Republic (their seams are built and tested with a synthetic test regime,
  `countries/zz`, never shipped; §14.5).
- Billing our own customers automatically (manual invoicing until volume justifies it).
- A public events API, ISV company grants, custom roles, entitlements by plan: designed here, built later (§16.4).

### 1.5 Deadline

15 November 2026 is the market's legal deadline, not a date we control: certification as PSFE depends on DGII's
calendar and needs three certified client emitters. The order of work (§16.3) puts everything on the
critical path to a certified first client before anything else; parties, catalog and the events API come after
go-live. Whether to launch before certification with clients emitting through `CerteCF` is an owner decision
(§19, Q1).

---

## 2. Rules that hold everywhere

These replace v1's ten rules. Each is enforced by a tool where a tool can enforce it.

| # | Rule | Enforced by |
|---|---|---|
| 1 | **A module owns its data.** One PostgreSQL schema and one DML role per module; no cross-schema foreign key, join or query. Data crosses modules through the owner's `api` (commands and queries) or its `events`. | DB grants (§6.2); import-linter (§4.6) |
| 2 | **Commands are in-process calls through a Protocol, facts are events.** `ledger_<m>.api` holds typed Protocols and DTOs; `ledger_<m>.events` holds frozen event types. Nothing else of a module is importable by another. | import-linter `protected` |
| 3 | **Every state change and the event announcing it commit together** (outbox in the producer's schema). | chassis `UnitOfWork.publish` is the only way to emit |
| 4 | **Every consumer and every job is idempotent** (inbox keyed by consumer and event id; jobs keyed by kind and key). | chassis tables' primary keys |
| 5 | **Every external write is idempotent**: `Idempotency-Key` (7 days) on the public API and a permanent business key (`external_reference`). | http dependencies; unique indexes |
| 6 | **Country rules live in country packs.** Modules never import a country; countries never import a module or each other. Modules reach countries through registries (entry points). | import-linter `forbidden`/`independence` |
| 7 | **Private keys never leave the signer process.** Other processes ask it to sign over gRPC; no API exports a key. | separate image entrypoint, DB role, network policy |
| 8 | **Tenancy is checked twice**: in the application (principal, company, mode) and by PostgreSQL row-level security. | RLS policies generated per table; deny-by-default test per table |
| 9 | **Contracts are versioned and checked for breaking changes in CI**: protobuf (`buf breaking`) for the signer, JSON Schema snapshots for events, OpenAPI snapshots for the public API. | `ledger-devtools check`, `buf breaking` |
| 10 | **Everything is observable**: one trace from the client's call through events, jobs, signing and DGII calls. | chassis instrumentation; trace context stored with outbox rows and jobs |
| 11 | **Every list is generated** from manifests: workspace dependencies, entry points, import-linter contracts, coverage targets, CODEOWNERS, role grants, reference docs. | `ledger-devtools check` fails on a stale file |
| 12 | **Legal rules are effective-dated data**, selected by the document's tax point in the regime's time zone, never by `now()`; resolved values are stored on the document. | regime conformance suite (§8.7) |

---

## 3. Architecture

### 3.1 Shape

A **modular monolith**: one repository, one Python environment, one OCI image, five entrypoints (*apps*).
Modules are bounded contexts with a public surface; countries are packs registered against extension points;
apps compose them (R1, ADR 0001).

```
                     clients (ERP, POS, stores, ISVs)                        DGII and other emitters
                                │ HTTPS                                          │ HTTPS (reception, approvals)
                                ▼                                                ▼
   Zitadel ◄─── JWKS ──┐ ┌────────────────────────────────────┐        ┌────────────────────────────┐
   (identity provider) └─┤ app: api  (Granian, N replicas)    │        │ app: receiver (N replicas) │
                         │  modules' HTTP adapters:           │        │  channel receiver routes:  │
                         │  identity, signer(http face),      │        │  /do-ecf-v1/{rnc}/fe/...   │
                         │  invoicing, fiscal, webhooks,      │        │  (seed, token, reception,  │
                         │  channels' public routes /v1/do/...│        │   commercial approval)     │
                         └──────────────┬─────────────────────┘        └──────────────┬─────────────┘
                                        │ in-process api calls; UnitOfWork per module │
                                        ▼                                             ▼
   ┌──────────────────────────────── PostgreSQL 18 (one cluster) ────────────────────────────────────┐
   │ schemas: identity · signer · invoicing · fiscal · webhooks · do_ecf · (parties, catalog later)   │
   │ each with: its tables (RLS) + outbox + inbox + jobs + idempotency_keys                           │
   └───────────────▲───────────────────────────────▲──────────────────────────────▲──────────────────┘
                   │ LISTEN/NOTIFY + SKIP LOCKED   │                              │ signer schema only
   ┌───────────────┴──────────────────────┐        │                  ┌───────────┴────────────────┐
   │ app: worker (N replicas)             │        │ gRPC (mTLS later)│ app: signer (2 replicas)   │
   │  outbox fan-out → inboxes            │────────┼─────────────────►│  certificates, KMS envelope│
   │  consumers, jobs, schedules          │        │  SignXml          │  sign XML / seeds, audit   │
   │  fiscal submissions (DGII), webhooks │────────┘                  └───────────┬────────────────┘
   └───────────────┬──────────────────────┘                                      │
                   │ HTTPS                                                       ▼
                   ▼                                                        cloud KMS
          DGII (TesteCF / CerteCF / eCF), client webhook endpoints, object storage (WORM archive)

   app: migrate — one-shot job before each rollout: every module's and channel's migrations, in dependency order
```

### 3.2 Modules (bounded contexts)

| Module | Owns (schema) | Public API (`api.py` Protocol) | Publishes | Consumes | Built in |
|---|---|---|---|---|---|
| `identity` | organizations, companies (jurisdiction, functional currency, qualified tax ids), the global tax-id registry, establishments, memberships, API clients (mode-bound, pinned API version) | `IdentityApi`: company by id, company by tax id, permissions of a principal in a company, lock fiscal profile | `identity.company.registered/updated/deactivated`, `identity.membership.granted/revoked` | — | v2-M2 |
| `signer` | certificates (sealed private keys), signing audit | `SignerApi`: describe certificates, sign XML, sign authority seed (a gRPC client in every app but `signer`) | `signer.certificate.installed/rotated/retired/expiring` | `identity.company.deactivated` | v2-M3 |
| `invoicing` | commercial documents (invoice, credit note, debit note; sales or self-billed purchase), lines, totals, `external_reference`, the mirror of their fiscal status | `InvoicingApi`: get document; extension point `IssuePolicy` (implemented by `fiscal`) | `invoicing.invoice.issued/voided/fiscal_status_changed` | `fiscal.document.updated` | v2-M5 |
| `fiscal` | enrollments (company × mode × channel × environment, requirements), the neutral fiscal-document projection, the channel routing, country catalog read models | `FiscalApi`: enrollment of a company, fiscal document by commercial id; implements `invoicing.IssuePolicy` | `fiscal.document.updated`, `fiscal.enrollment.changed` | `invoicing.invoice.issued`, channel events (`fiscal_channel.*`), `signer.certificate.installed` | v2-M5 |
| `webhooks` | endpoints, sealed secrets, deliveries | `WebhooksApi` (none needed by other modules at first) | `webhooks.delivery.failed` | every event declared `public = true` | v2-M5 |
| `parties` | parties (one object, roles customer/supplier/...), qualified tax ids, addresses | `PartiesApi` | `parties.party.*` | — | after go-live |
| `catalog` | products and services, units, tax-category selections (regime keys) | `CatalogApi` | `catalog.item.*` | — | after go-live |
| `accounting` | journal, accounts, periods (§7.9) | — | — | invoicing and fiscal events | later |

Channels (`countries/<cc>/…/channels/<channel>`) also own a schema (`do_ecf`) and run inside the `api`,
`worker` and `receiver` apps. They are not modules: they depend on `fiscal-kit`, `platform` and `kernel` only,
and receive everything else through ports the `fiscal` module provides (§8.4).

Module dependency graph (acyclic, checked by `ledger-devtools check` and import-linter):

```
 webhooks ──► (public events of every module, by subscription only; no api import)
 invoicing ──► identity
 fiscal ─────► identity, invoicing(api: IssuePolicy, events), signer(events)   ··· channels (registry, not imports)
 signer ─────► identity(events)
 parties, catalog ──► identity
```

`invoicing` never depends on `fiscal`: it declares the `IssuePolicy` extension point and the composition root
binds `fiscal`'s implementation to it (dependency inversion, [02] Kraken).

### 3.3 Apps (deployables)

One image (`ledger`), five console scripts. An app is a composition root: it loads the platform runtime, the
modules' assemblies and the registered countries, and mounts what its role needs. Apps contain no business
code (import-linter: an app imports only `ledger_platform.runtime` and `ledger_<m>.assembly`).

| App | Console script | Mounts | Scaling, exposure |
|---|---|---|---|
| `api` | `ledger-api` | every module's HTTP adapter, channels' public routes (`/v1/do/...`), the countries API; no background work except after-commit wake-ups | stateless, N replicas, public behind ingress |
| `worker` | `ledger-worker` | outbox fan-out, every consumer, every job kind, schedules; no HTTP but probes | N replicas; work shared through `SKIP LOCKED`; can be split into pools by job kind later |
| `signer` | `ledger-signer` | `signer` module's gRPC server and its database role only | 2 replicas, private network, own credentials (§3.7) |
| `receiver` | `ledger-receiver` | channels' authority-facing routes (DGII reception, approvals, seed/token) | N replicas, public, separate host name and rate limits (§3.8) |
| `migrate` | `ledger-migrate` | every module's and channel's migrations in dependency order | one-shot job before each rollout |

A single-process *dev mode* (`ledger-api --with-worker`) runs `api` and `worker` together for local
debugging; it is never deployed. The signer always runs as its own process, locally too.

### 3.4 In-process APIs

A module's `api.py` contains **only** Protocols, frozen DTOs and the module's public error types. Its
implementation lives in `application/facade.py`; the assembly binds it. Callers depend on the Protocol and
receive an instance from the composition root, so a test passes a fake and an extraction passes a gRPC client
(§3.9).

```python
# modules/identity/src/ledger_identity/api.py
class IdentityApi(Protocol):
    async def company(self, call: Call, company: CompanyId) -> CompanyView: ...
    async def company_by_tax_id(self, call: SystemCall, tax_id: TaxId) -> CompanyView | None: ...
    async def permissions(self, call: Call, principal: Principal, company: CompanyId) -> frozenset[Permission]: ...
    async def lock_fiscal_profile(self, call: Call, company: CompanyId) -> None: ...
```

- `Call` (platform `runtime.calls`) carries the principal, the tenant scope (`organization`, `company`,
  `mode`), the request id, the pinned API version and the trace context. `SystemCall` is a call made by the
  platform itself (a consumer, a job, the receiver) and names the reason; it is never built from a request.
- A facade method opens **its own** unit of work in its module's role. Two modules never share a transaction
  (rule 1); a command that must be atomic with another module's state is redesigned as an event.
- Facades take and return DTOs, never domain objects or rows; DTOs are `@dataclass(frozen=True, slots=True,
  kw_only=True)` with kernel value types.
- Errors crossing a facade are `LedgerError` subclasses with a kind and a stable code (§9.6); a module's
  public error types are exported from its `api.py`.

### 3.5 Events: outbox, fan-out, inbox (no broker)

Events are frozen dataclasses declared in the producer's `events.py`:

```python
@final
@dataclass(frozen=True, slots=True, kw_only=True)
class InvoiceIssued(Event):
    TYPE: ClassVar = "invoicing.invoice.issued"
    VERSION: ClassVar = 1
    document: InvoiceId
    ...
```

**Envelope** (columns, CloudEvents-shaped so a broker adapter maps 1:1): `id` (UUIDv7, public `evt_`),
`type`, `version`, `source` (producing module or channel key), `organization_id`, `company_id` (nullable for
organization-wide events), `mode`, `subject` (aggregate id), `aggregate_version`, `occurred_at`, `traceparent`,
`payload` (JSON from the dataclass by the platform codec: `Decimal` as string, ids as UUID strings, dates ISO
8601). Payload ≤ 64 KiB, refused at `publish` (an event no dispatcher could deliver fails its transaction).

**Flow** (ADR 0002):

```
 producer transaction (module role)                     worker: fan-out (role ledger_relay)
 ─────────────────────────────────────                  ─────────────────────────────────────────────
 UPDATE invoicing.documents ...                          SELECT ... FROM <schema>.outbox
 INSERT INTO invoicing.outbox (event)                      ORDER BY id LIMIT 100 FOR UPDATE SKIP LOCKED
 COMMIT ── after commit: NOTIFY ledger_outbox, 'invoicing' for each row: for each subscribed consumer:
                                                           INSERT INTO <consumer schema>.inbox (consumer, event...)
                                                           ON CONFLICT DO NOTHING
                                                         DELETE the outbox rows; COMMIT
                                                         after commit: NOTIFY ledger_inbox, '<consumer>'

 worker: consumer runner (consumer's module role)
 ─────────────────────────────────────────────────
 claim due inbox rows of <consumer> FOR UPDATE SKIP LOCKED (lease 60 s)
 per row, one transaction in the consumer's role and the event's tenant scope:
   handler(event, uow) ; UPDATE inbox SET handled_at = now()   -- same transaction
 on retryable failure: attempts+1, next_attempt_at = backoff ; on INVALID or attempts = max: status = 'dead'
```

- **Subscriptions are static**: the consumer's manifest declares `consumer` name and the event `types`;
  `ledger-devtools sync` generates the routing table (`type → consumers`) shipped with each module's generated
  `_manifest.py`; the runtime refuses to start if a subscribed type has no producer.
- **Exactly-once effects over at-least-once delivery**: fan-out and outbox deletion commit together; the
  inbox primary key `(consumer, event_id)` absorbs a repeated fan-out; the handler and its `handled_at` commit
  together.
- **Ordering**: none promised across aggregates. Within an aggregate a handler that needs order checks
  `aggregate_version` and raises `OutOfOrderError` (retryable) on a gap, as in v1.
- **Retries**: backoff 1 s, 10 s, 1 min, 5 min, then every 10 min, `max_attempts` 10 (overridable per
  consumer in the manifest); then `dead`. Dead rows are the dead letters: listed and replayed by an operator
  command (`ledger-worker replay --consumer X --event evt_…`), alerted on (§11).
- **Wake-ups**: `LISTEN ledger_outbox` / `ledger_inbox` on one dedicated connection per worker process; a
  1 s poll is the safety net (a lost notification costs at most the poll).
- **Retention**: handled inbox rows are purged after 7 days by a platform schedule (they hold ids and the
  payload; no bodies elsewhere).
- **The `EventBus` port** stays: `platform.events.EventBus` has the Postgres implementation now; an extracted
  module gets a broker adapter (NATS JetStream or similar) behind the same port (§3.9).

### 3.6 Jobs and schedules (durable work without Temporal)

Long-running processes (submitting an e-CF and following it for days, delivering webhooks, expiry scans) are
**jobs** in the owning module's `jobs` table, advanced by **explicit, persisted state machines** (ADR 0003).

```sql
-- in every module schema (chassis schema v1)
jobs(
  kind text, key text,                         -- PRIMARY KEY (kind, key): enqueue is idempotent
  organization_id uuid, company_id uuid, mode text,
  status text CHECK (status IN ('ready','running','parked','done','dead')),
  run_at timestamptz, lease_until timestamptz, attempts int, max_attempts int, deadline_at timestamptz,
  payload jsonb,                               -- ids and small words only, never a document body
  last_error_code text, traceparent text, created_at timestamptz, updated_at timestamptz)
```

- `Jobs.enqueue(uow, kind, key, *, run_at, payload)` inside the transaction that decides the work.
- A handler returns an outcome: `Done`, `RunAgain(at)` (polling), `Park(reason)` (waiting for an outside fact,
  e.g. a certificate; a consumer of that fact calls `Jobs.wake(kind, key)`), or raises. A retryable error
  reschedules with the kind's backoff; an `INVALID`/`CONFLICT` error or the deadline makes the job `dead`.
- **No transaction spans a network call**: a handler reads in one unit of work, calls out (DGII, signer,
  object storage) outside it, and records in another that re-reads the row under its lock (v1/M4 rule, kept).
- Fairness: claims order by `run_at` and take at most `per_company_concurrency` (default 4) running jobs of a
  kind per company, so one tenant's backlog does not starve others ([04] rule 6).
- **Schedules** (cron-like) are declared in manifests (`every = "1h"`); one worker replica runs each at a time
  under a PostgreSQL advisory lock; a schedule only enqueues jobs, it does no work itself.
- **Revisit Temporal when** any of: a process needs more than five cross-module steps with compensation; more
  than three hand-written state machines duplicate timer/retry logic the kit cannot absorb; or job volume needs
  more than one PostgreSQL primary for queues alone (ADR 0003).

### 3.7 Signer isolation

Private keys live only in the `signer` process (rule 7, ADR 0010).

- **Process**: `ledger-signer`, its own Deployment, its own PostgreSQL login role (`ledger_signer`, member of
  `signer_rw` only), its own KMS credentials; no other app holds either. Network policy: only `api`, `worker`
  and `receiver` pods reach its gRPC port; it reaches only PostgreSQL and KMS.
- **Contract**: `contracts/proto/ledger/signer/v1/signer.proto`, service `SignerService`: `ImportCertificate`
  (bytes + password stream through, never parsed outside the signer), `RotateCertificate`,
  `DescribeCertificates`, `SignXml` (enveloped XMLDSig, RSA-SHA256, the channel's profile id), `SignSeed`.
  Deadlines always set by the caller (≤ 10 s); errors carry the kind's status and the stable code in trailing
  metadata (v1 §4.2, kept).
- **Custody**: `.p12` parsed in the signer; the private key encrypted with a per-certificate data key (AES-256-GCM,
  associated data binds certificate id and company); the data key wrapped by the cloud KMS (envelope). Every
  signature is audited (who, company, mode, document SHA-256, reference such as an e-NCF) and the signature is
  returned only after its audit row commits (v1/M3 decisions 5, 7, 9, 10 kept: bounded signing pool, data-key
  cache off by default).
- **Faces**: the `signer` module has an HTTP adapter (`/v1/certificates`) mounted in `api`, which forwards to
  gRPC, and a gRPC server mounted only in `signer`. import-linter forbids every app but `signer` from importing
  `ledger_signer.adapters.custody`.
- Until the service mesh exists (deployment milestone), gRPC is plaintext inside the cluster network and the
  signer authenticates callers by a shared-secret interceptor (`LEDGER_SIGNER_CLIENT_TOKEN`, rotated); mTLS
  replaces it.

### 3.8 Receiver

Authority-facing endpoints are a separate app because their exposure, rate limits and availability differ
from the client API ([02] R2). The receiver mounts each registered channel's `receiver_routes`; for
`do-ecf-v1`: seed (`/fe/autenticacion/api/semilla`), seed validation (`/fe/autenticacion/api/validacioncertificado`),
reception (`/fe/recepcion/api/ecf`) and commercial approval (`/fe/aprobacioncomercial/api/ecf`), under a
per-company prefix `/do-ecf-v1/{rnc}` resolved through identity's global tax-id registry (§7.3). It verifies
the sender's signature, stores the raw XML in the archive, records the received document and its event in the
channel's schema in one transaction, and answers DGII's acknowledgement (ARECF) signed by the signer. Nothing
else runs there.

### 3.9 Extraction path

A module is extracted only for a runtime reason (security isolation, scaling or latency profile, data
residency, failure isolation; [02] R8), never to organize code. The procedure is mechanical:

| Seam | In process (now) | Extracted |
|---|---|---|
| Commands/queries | `api.py` Protocol bound to `application.facade` | same Protocol bound to a gRPC client; the module serves gRPC (`contracts/proto/ledger/<m>/v1`) |
| Events | `PostgresEventBus` (outbox → fan-out → inbox in one cluster) | broker adapter of `EventBus` (outbox relay publishes; inbox consumer reads); envelope unchanged |
| Data | schema `<m>` in the shared cluster, role `<m>_rw` | own database, schema copied by logical replication, then cut over |
| Deploy | mounted by `api`/`worker` | its own app entry in `apps/`, same image |

The signer is the worked example from day one.

---

## 4. Repository layout and tooling

### 4.1 Tree

```
ledger/
├── pyproject.toml            workspace root: tool config only; members by glob; no per-package lists (generated parts marked)
├── uv.lock
├── README.md                 ~80 lines: what, layout, develop, run (no chassis manual; [05] §7.2)
├── AGENTS.md                 conventions for coding agents (points at this spec and the ADRs)
├── .importlinter             GENERATED from manifests (§4.6)
├── .github/
│   ├── CODEOWNERS            GENERATED from manifests
│   └── workflows/ci.yml      plan (affected set) → matrix of per-package jobs → gate job
├── packages/
│   ├── kernel/               ledger-kernel        generic value types; stdlib only (§5.1)
│   ├── platform/             ledger-platform      the chassis (§5)
│   ├── fiscal-kit/           ledger-fiscal-kit    country-agnostic fiscal plumbing (§8.4)
│   └── testing/              ledger-testing       dev-only harness + pytest plugin (§14)
├── modules/
│   ├── identity/             ledger-identity
│   ├── signer/               ledger-signer
│   ├── invoicing/            ledger-invoicing
│   ├── fiscal/               ledger-fiscal
│   └── webhooks/             ledger-webhooks      (parties/, catalog/, accounting/ later)
├── countries/
│   ├── do/                   ledger-country-do    regime "do" + channel "do-ecf-v1"
│   └── zz/                   ledger-country-zz    synthetic test country, never deployed (§14.5)
├── apps/
│   ├── api/  worker/  signer/  receiver/  migrate/     ledger-app-<name>: composition roots only
├── contracts/
│   ├── buf.yaml              one buf module; protos for cross-process surfaces only
│   └── proto/ledger/signer/v1/signer.proto
├── tools/
│   └── devtools/             ledger-devtools: sync, check, affected, scaffold, gate (§4.7)
│       └── templates/        copier templates: module/, country/, channel/
├── deploy/
│   ├── docker/ledger.Dockerfile     the one image (ARG-free; every app in it)
│   ├── local/                compose.yaml, postgres/init.sql (GENERATED roles), zitadel/, localstack/
│   └── helm/ledger/          one chart, one values block per app (deployment milestone)
├── docs/
│   ├── adr/NNNN-kebab-title.md       MADR; immutable once accepted; superseded-by links
│   ├── architecture/                 living overview diagrams (this spec's §3 once accepted)
│   ├── reference/                    GENERATED: modules, permissions, events (+ JSON Schemas), id prefixes, countries × tier
│   ├── runbooks/<package>/<incident>.md
│   └── superpowers/specs/            design specs (plans live in .superpowers/, untracked)
└── tests/                    whole-repo tests only (architecture invariants that no tool expresses)
```

Inside every package (module shown; countries in §8.6):

```
modules/invoicing/
├── manifest.toml             source of truth (§4.2)
├── pyproject.toml            GENERATED from the manifest (header says so)
├── README.md                 one screen: what it owns, its public API, links
├── src/ledger_invoicing/
│   ├── __init__.py           docstring only
│   ├── _manifest.py          GENERATED: typed ModuleManifest + routing table
│   ├── api.py                PUBLIC: Protocols, DTOs, public errors
│   ├── events.py             PUBLIC: event dataclasses
│   ├── assembly.py           for apps only: assemble(ModuleContext) -> ModuleParts
│   ├── domain/               aggregates, value objects, state machines, domain errors: kernel only
│   ├── application/          use cases, ports (Protocols), facade.py (implements api)
│   └── adapters/
│       ├── postgres/         tables.py, repositories/<aggregate>.py, migrations/versions/rNNNN_*.py
│       ├── http/             routers, request/response models (pydantic), OpenAPI fragments
│       └── <other>/          e.g. dgii/, xml/, kms/ — one directory per outside system
├── openapi.json              GENERATED snapshot of this module's public operations (contract test)
└── tests/{unit,integration,contract}/
```

### 4.2 Manifest schema: modules (`manifest.toml`)

Validated by `ledger-devtools check` against a typed model (`ledger_devtools.manifests.ModuleManifest`);
unknown keys are errors.

```toml
[package]
name = "invoicing"                  # required; == directory name; [a-z][a-z0-9_]{1,30}
kind = "module"                     # required; "module" | "country" | "app" | "package" | "tool"
summary = "Commercial documents: invoices, credit and debit notes, their lines and totals."
owners = ["@balegre0"]              # required; GitHub users/teams → CODEOWNERS
status = "active"                   # "planned" | "incubating" | "active" | "suspended"
                                    #   suspended: dropped from apps, CI and deploy in one revertable commit

[dependencies]
packages = ["kernel", "platform"]                 # workspace packages (not modules)
modules = ["identity"]                            # modules whose api/events this one imports; must be a DAG
python = ["fastapi>=0.143", "sqlalchemy>=2.1.4"]  # third-party, with floors; deptry checks usage

[data]
schema = "invoicing"                # PostgreSQL schema == role prefix; unique across the repo
moded = true                        # tenant tables carry `mode` (test|live); false only for identity's org-level tables

[[ids]]                             # public TypeID prefixes this module mints; unique across the repo
prefix = "inv"
resource = "invoice"

[[permissions]]                     # open set, declared here, composed into roles by identity
code = "invoicing.invoices.read"    # <module>.<resource>.<action>; action in read|create|issue|void|manage|own
summary = "Read commercial documents and their fiscal status."
roles = ["owner", "admin", "accountant", "issuer", "viewer"]   # built-in roles granted it (§7.7)

[[permissions]]
code = "invoicing.invoices.issue"
summary = "Issue invoices, credit notes and debit notes."
roles = ["owner", "admin", "accountant", "issuer"]

[[events.publishes]]
type = "invoicing.invoice.issued"   # <module>.<aggregate>.<past_participle>
version = 1
public = "invoice.issued"           # optional: exported to webhooks under this public name (§9.9)

[[events.consumes]]
consumer = "invoicing.fiscal_status"            # <module>.<purpose>; the inbox name
types = ["fiscal.document.updated"]
max_attempts = 10                               # optional (default 10)

[[jobs]]
kind = "invoicing.export"           # <module>.<verb>
max_attempts = 20
deadline = "72h"
per_company_concurrency = 4

[[schedules]]
name = "invoicing.purge_drafts"
every = "24h"                       # or cron = "0 3 * * *" (UTC)
enqueues = "invoicing.purge_drafts"

[extension_points]
declares = ["invoicing.IssuePolicy"]            # Protocols in api.py others may implement
implements = []                                 # "<module>.<Protocol>" this module implements (bound by apps)

[http]
mounted_in = ["api"]                # apps whose HTTP surface includes this module's routers
prefixes = ["/v1/invoices"]         # unique across the repo

[quality]
coverage = 100                      # branch coverage floor for this package; only 100 is accepted for modules
```

### 4.3 Manifest schema: countries (`countries/<cc>/manifest.toml`)

```toml
[package]
name = "do"                         # ISO 3166-1 alpha-2, lowercase; == directory
kind = "country"
summary = "Dominican Republic: regime 'do' and the DGII e-CF channel."
owners = ["@balegre0"]
status = "active"

[dependencies]
packages = ["kernel", "platform", "fiscal-kit"]   # a country may not list modules
python = ["lxml>=6.1.3", "xmlschema>=4.3.2", "httpx>=0.28.1"]

[country]
iso = "DO"
name = "Dominican Republic"
currencies = ["DOP"]
time_zone = "America/Santo_Domingo"
authority = { name = "Dirección General de Impuestos Internos", short = "DGII", url = "https://dgii.gov.do" }

[[regimes]]
key = "do"                          # regime key: country, or country-subdivision ("us-ca") when law differs
entry_point = "ledger_country_do.regime:REGIME"

[[channels]]
key = "do-ecf-v1"                   # <cc>-<format>-v<N>; one key per format major version
entry_point = "ledger_country_do.channels.ecf_v1:CHANNEL"
schema = "do_ecf"                   # one schema per channel family; v2 of the format shares it
valid_from = "2019-01-01"           # legal window of this format version; until = optional
environments = ["testecf", "certecf", "ecf"]     # authority environments; ecf is live-mode only
live_environments = ["ecf"]
http_prefixes = ["/v1/do"]          # public API (api app); receiver routes are under /do-ecf-v1 (receiver app)
receiver = true

[[channels.ids]]                    # same shape as a module's [[ids]]
prefix = "rng"
resource = "numbering_range"

[[channels.ids]]
prefix = "anl"
resource = "range_void"

[[channels.ids]]
prefix = "rdoc"
resource = "received_document"

[[channels.permissions]]
code = "do_ecf.ranges.manage"
summary = "Register DGII-authorized e-NCF ranges and void unused numbers."
roles = ["owner", "admin"]

[[channels.events.publishes]]
type = "fiscal_channel.document.numbered"       # fiscal-kit-owned event types (§8.4.4)
version = 1

[[channels.jobs]]
kind = "do_ecf.advance"
max_attempts = 1000                 # polling is RunAgain, not an attempt; attempts count failures
deadline = "15d"
per_company_concurrency = 8

[[channels.schedules]]
name = "do_ecf.range_watch"
every = "1h"
enqueues = "do_ecf.range_watch"

[quality]
tier = "bronze"                     # claimed tier (§8.8); devtools rejects a claim with a todo at or below it
coverage = 100

[[quality.rules]]
id = "golden-documents"             # rule ids come from fiscal-kit's tier catalogue
status = "done"                     # "done" | "todo" | "exempt"
reason = ""                         # mandatory for exempt
```

Apps and packages have the `[package]` and `[dependencies]` tables only; an app also has
`[app] script = "ledger-api"` and `mounts = ["http", "receiver"] | ["worker"] | ...`.

### 4.4 Naming conventions

| Thing | Rule | Example |
|---|---|---|
| Directory | `packages/<name>`, `modules/<name>`, `countries/<iso2>`, `apps/<name>`; lowercase, `-` only in package names | `modules/invoicing`, `packages/fiscal-kit` |
| Distribution | `ledger-<name>`; country `ledger-country-<cc>`; app `ledger-app-<name>` | `ledger-country-do` |
| Import package | distribution with `_`; exactly one top-level package per distribution; no namespace packages | `ledger_fiscal_kit` |
| Console script | `ledger-<app>` (apps only) | `ledger-worker` |
| Manifest key | `package.name` == directory name ([03] HA rule) | |
| Schema / DB roles | schema = module name (`do_ecf` for a channel family); roles `<schema>_owner`, `<schema>_rw`; logins `ledger_app`, `ledger_signer`, `ledger_migrator`, `ledger_relay` | `invoicing_rw` |
| Permission | `<module>.<resource>.<action>`, plural resource, action from a closed verb list | `fiscal.enrollments.manage` |
| Event type | `<module>.<aggregate>.<past_participle>`; kit-owned: `fiscal_channel.<aggregate>.<verb>` | `identity.company.registered` |
| Public event name | `<resource>.<verb>` (webhooks) | `invoice.fiscal.accepted` |
| Consumer | `<module>.<purpose>` | `fiscal.commercial_documents` |
| Job kind / schedule | `<module or channel>.<verb_or_noun>` | `do_ecf.advance` |
| Public id prefix | 2-5 lowercase letters, unique, declared in a manifest | `inv_01jc5...` |
| Regime key / channel key | `<cc>` or `<cc>-<sub>` / `<cc>-<format>-v<N>` | `do`, `do-ecf-v1` |
| Tax-id scheme | `<cc>.<scheme>` | `do.rnc`, `mx.rfc`, `eu.vat` |
| `ext` key | `<regime or channel>.<field>` | `do-ecf-v1.income_type` |
| Error code | `<thing>_<problem>`, snake_case, unique across the repo (generated catalogue) | `range_exhausted` |
| Env var | `LEDGER_<SETTING>`; module-owned settings `LEDGER_<MODULE>_<SETTING>` | `LEDGER_DO_ECF_DGII_TIMEOUT_SECONDS` |
| Proto package | `ledger.<module>.v<N>` | `ledger.signer.v1` |
| Python modules | nouns, `lower_with_under`; never `utils`, `helpers`, `common`, `misc`, `shared` | `numbering.py` |
| Tests | `tests/unit|integration|contract|conformance/test_<subject>.py`; support modules prefixed by package (`invoicing_samples.py`) | |
| ADR | `docs/adr/NNNN-kebab-title.md` | `0005-regime-channel-split.md` |
| Vocabulary (one word per concept, [05] §4.4) | *tenant* = (organization, company, mode); *principal* = authenticated caller; *member* = principal with a role in a company; *unit of work* (variable `uow`); *call* (in-process request context) | — |

### 4.5 Layering inside a package

```
assembly          (apps only)        wires adapters, facade, consumers, jobs, routers
adapters          http, postgres, xml, dgii, kms, grpc ...
api               Protocols + DTOs + public errors (no implementation)
application       use cases, ports, facade (implements api)
domain            aggregates, value objects, state machines, domain errors
events            public event dataclasses (imported by domain to emit)
```

A layer imports only layers below it. `domain` and `events` import only `ledger_kernel` (and, in a channel,
`ledger_fiscal_kit.domain`); `application` may import `ledger_platform.data` *ports* (`UnitOfWork`,
`Jobs`, `Clock`) but no adapter; third-party imports below `adapters` are forbidden (one generated
`forbidden` contract per package, replacing v1's three hand-copied lists, [05] D35). [02] R9: a CRUD module may
have an empty `domain/`; only the public surfaces and the layering are mandated.

### 4.6 import-linter contracts (generated into `.importlinter`)

| # | Contract | Type | Content |
|---|---|---|---|
| 1 | Workspace layers | `layers` | `ledger_app_*` > (`ledger_<module>` \| `ledger_country_*`) > `ledger_fiscal_kit` > `ledger_platform` > `ledger_kernel` |
| 2 | Kernel is stdlib only | `forbidden` | `ledger_kernel` → every third-party distribution in `uv.lock` |
| 3 | Modules never import countries | `forbidden` | `ledger_<module>` → `ledger_country_*` |
| 4 | Countries never import modules | `forbidden` | `ledger_country_*` → `ledger_<module>` |
| 5 | Countries are independent | `independence` | all `ledger_country_*` |
| 6 | Module public surface | `protected` (one per module) | `ledger_<m>.*` importable only by `ledger_<m>` itself, except `ledger_<m>.api` and `ledger_<m>.events` (by declared dependents) and `ledger_<m>.assembly` (by apps) |
| 7 | Declared module edges only | `forbidden` (one per module) | `ledger_<m>` → every module not in its `dependencies.modules` |
| 8 | Acyclic modules | `acyclic_siblings` | over the module set (also checked on the manifest DAG) |
| 9 | Layers inside each package | `layers` with `containers` | §4.5 per module, per channel |
| 10 | Domain purity | `forbidden` (one per package) | `*.domain`, `*.events`, `*.application` → third-party |
| 11 | Signer custody | `forbidden` | everything but `ledger_app_signer` and `ledger_signer` → `ledger_signer.adapters.custody` |
| 12 | Test code never in production | `forbidden` | every production package → `ledger_testing`, `*.testing` |
| 13 | Apps only compose | `forbidden` | `ledger_app_*` → `ledger_<m>.{domain,application,adapters}`, `ledger_country_*` |

No `ignore_imports` in new code; if one is ever needed, `ledger-devtools check` counts it and the count is a
tracked debt metric ([02] Kraken).

### 4.7 devtools (`tools/devtools`, `ledger-devtools`)

A workspace member under the full gate (our hassfest/Breeze, [03]).

| Command | Does | CI |
|---|---|---|
| `sync` | From manifests: each package's `pyproject.toml` (dependencies, entry points `ledger.modules`, `ledger.countries.regimes`, `ledger.countries.channels`, console scripts); `_manifest.py` per package; `.importlinter`; `[tool.coverage]` per-package targets; `.github/CODEOWNERS`; `deploy/local/postgres/init.sql` (schemas, roles, grants); `docs/reference/*` (permissions, events with JSON Schemas, id prefixes, error codes, countries × tier); the role → permission table identity ships (§7.7) | — |
| `check` | Validates every manifest (schema, name == directory, unique schema/prefix/permission/event type/error code/http prefix, module DAG, declared deps == imports via deptry, tier claims vs rules, event schema snapshots additive within a version, channel `valid_from` ordering); fails if `sync` would change any file | every PR |
| `affected --base <ref>` | Changed paths → owning package (nearest `manifest.toml`) → transitive dependents (from manifests); a core list (`packages/platform/**`, `packages/kernel/**`, `packages/testing/**`, `tools/**`, `pyproject.toml`, `uv.lock`, `.github/**`, `contracts/**`) selects everything; prints the CI matrix as JSON | plan job |
| `scaffold module|country|channel <name>` | Renders the copier template, then runs `sync`; never copy a sibling ([03] anti-pattern 11) | — |
| `gate [--affected]` | Runs per package: ruff, basedpyright, mypy, pytest with that package's coverage target, deptry, vulture; then repo-wide: import-linter, typos, buf lint/breaking, `check` | `poe check` wraps it |

---

## 5. Platform chassis (`packages/platform`, `ledger_platform`)

### 5.1 Kernel (`packages/kernel`, `ledger_kernel`), for reference

Generic value types, stdlib only, no country rules (R3):

| Module | Public surface |
|---|---|
| `errors` | `ErrorKind`, `LedgerError` + six kind bases, `InvalidValueError(field, requirement)` (the single "our code built a bad value" error, [05] D4), `excerpt`, `truncate` |
| `ids` | `OrganizationId`, `CompanyId`, `new_id()` (UUIDv7), `TypeId` codec (`format_id(prefix, uuid)`, `parse_id(prefix, text)`) |
| `tenancy` | `Mode` (`test`/`live`), `Tenant(organization, company, mode)`, `Scope` = `Tenant` \| `OrganizationScope(organization, mode)` with `.organization`/`.company` (ends the four `match` copies, [05] D14) |
| `access` | `SubjectId`, `Principal`, `Permission` (validated `NewType`, `<module>.<resource>.<action>`) |
| `money` | `Currency` (ISO 4217 table: code, numeric, minor units, kept as data), `Money` (exact `Decimal`, laws as v1 §7), `RoundingMode` |
| `jurisdiction` | `CountryCode` (ISO 3166-1 alpha-2 table), `Jurisdiction(country, subdivision: str | None)` (ISO 3166-2) |
| `tax_ids` | `TaxId(scheme, value)`, `TaxIdScheme` (`<cc>.<name>`), `TaxIdCheck` (`valid` \| `checksum_mismatch` \| `invalid_format`), masked `repr` |
| `dated` | `Timeline[T]` (newest-first `Since[T]` values, `at(date)`, order validated) |
| `regimes` | the regime schema types and the registry (§8.2): `RegimeDef`, `TaxCategoryDef`, `RateDef`, `DocumentTypeDef`, `TaxIdSchemeDef`, `ExtDef`, `Source`, `RoundingRule`, `TaxCalculator`, `Regimes.load()` |
| `pagination` | `PageRequest`, `Page[T]`, cursor codec (kept) |
| `clock` | `Clock`, `MonotonicClock`, `Sleep`, `utc_now` (kept) |

### 5.2 Subpackages and public surface

Every subpackage has an `__init__.py` with an explicit `__all__`; everything else is private (leading
underscore modules). The whole public surface an application author touches is the ~35 names below ([05] §5:
v1 exposed 263 symbols, 148 unused).

| Subpackage | Public surface | From v1 (`libs/platform`) |
|---|---|---|
| `config` | `Settings` (base, `LEDGER_*`, fail-closed on unknown variables), `InvalidOptionError(component, option, requirement)` | `settings.py` (kept), `options.py` (12 subclasses collapsed into one, [05] D7) |
| `observability` | `configure_logging`, `Tracing`, `Health`, `Check`, `metrics` registry helpers | `logs.py`, `tracing.py`, `health.py`, `probe.py` (kept, moved); validation-issue rendering shared with `http` ([05] D10) |
| `runtime` | `App` (builder: settings, modules, countries, role), `ModuleContext`, `ModuleParts`, `Call`, `SystemCall`, `Supervisor`, `run(app)` | `service.py` split (spec/lifecycle/start order), `supervision.py` kept, `lifecycle.py` merged into `AsyncExitStack` use, `interruptions.py` deleted |
| `data` | `Database`, `UnitOfWork` (`uow.publish(event)`, `uow.jobs`), `Table` helpers (`tenant_table`, `organization_table` adding scope columns, FKs that carry the tenant, RLS), `chassis_schema_v1(op, schema)`, `Migrations` | `database.py` split, `migrations.py` kept (data-migration helpers `rows_by_scope`/`run_per_scope` deleted until needed), `schema.py` merged |
| `data.queue` | the claim/lease/retry engine shared by inbox and jobs (`claim`, `Backoff`, `Outcome`) | new; replaces the relay loop half of `outbox.py` and the delivery loop of `consumer.py` |
| `events` | `Event` base, `EventBus` port, `PostgresEventBus`, `Consumer` (declared handler), `Dispatcher` (fan-out), codec | `outbox.py` (table, writer kept), `consumer.py` inbox part kept; JetStream parts deleted |
| `jobs` | `Jobs` (enqueue/wake), `JobHandler`, `Done`, `RunAgain`, `Park`, `Schedule` | new |
| `auth` | `TokenVerifier`, `PublishedKeys`, `ClaimsProfile` port + `zitadel` profile, `Access` (permission check on a `Call`), `PermissionDeniedError`, `CompanyNotFoundError` | `authentication.py` split (keys, tokens, providers/zitadel; [05] §4.1), `authorization.py` minus `CachedPermissions` |
| `http` | `create_app`, `Problem`, `Page[T]` response model, dependencies (`authenticated`, `in_company`, `in_organization`, `requires(permission)`, `page_request`, `if_match`, `idempotency_key`, `api_version`), `etag`, `precondition_checked`, `problems()`/`success()` OpenAPI helpers, `ApiVersions` registry | `web.py` split (app, problems, metrics, probes), `routes.py` split into deps, `idempotency.py` and `request_fields.py` moved here; identity's `api/context.py` helpers moved in ([05] D22-D25) |
| `grpc` | `serve`, `GuardInterceptor`, `call_unary`, `RemoteError` family | `grpc_server.py`, `grpc_client.py` (kept, used by signer only); one shared kind → status table with `http` ([05] D5) |
| `blobs` | `ImmutableStore` port (`put_once(key, bytes, retain_until)`, `get`), `S3ImmutableStore` (object lock, compliance mode, `If-None-Match: *`, SHA-256 checksums) | new (from M4 plan's archive adapter) |

Deleted outright: `streams.py`, `jetstream.py`, `interruptions.py`, `identity_client.py`, `CachedPermissions`
and its tests, `lifecycle.py` (folded), the NATS connection policy in `service.py` (R2, R5).

### 5.3 Unit of work

```python
async with database.unit(call.scope, role="invoicing") as uow:   # SET LOCAL ROLE invoicing_rw; RLS settings
    document = await documents.get(uow, document_id, for_update=True)
    ...
    uow.publish(InvoiceIssued(...))       # outbox row in invoicing.outbox, same transaction
    uow.jobs.enqueue("invoicing.export", key=str(document.id), run_at=...)
```

- One process-wide pool logs in as `ledger_app` (`NOINHERIT`, member of every `<schema>_rw` but
  `signer_rw`); each unit begins with `SET LOCAL ROLE <schema>_rw` and `SET LOCAL search_path = <schema>`, and
  sets `ledger.organization_id`, `ledger.company_id`, `ledger.mode` from its scope. A module's transaction
  therefore cannot read or write another schema even through a bug, while the process keeps one bounded pool
  (decision beyond R2, ADR 0004).
- Commit only if every statement succeeded; after-commit callbacks (NOTIFY wake-ups) never run after a
  rollback (v1 §7 kept). Limits on every connection as v1 (connect 5 s, statement 30 s, lock 5 s,
  idle-in-transaction 60 s, pool wait 10 s; `hide_parameters`).

---

## 6. Data

### 6.1 One cluster, schema per module

PostgreSQL 18, one cluster (managed in production), one database `ledger`. Each module and each channel family
owns one schema (ADR 0004). No foreign key, view, join or function crosses schemas; cross-module reads go
through facades or event-fed read models. A schema can move to its own database by logical replication (§3.9).
The unused `ledger` app database and `ledger_app` narrative of v1's local stack are replaced ([05] §3.3).

### 6.2 Roles

| Role | Login | Privileges |
|---|---|---|
| `<schema>_owner` | no | owns schema `<schema>` and its objects; `FORCE ROW LEVEL SECURITY` on tenant tables |
| `<schema>_rw` | no | `USAGE` on its schema; DML on its tables; no DDL; not `BYPASSRLS` |
| `ledger_migrator` | yes | member of every `_owner`; used only by `migrate` (`SET ROLE <schema>_owner` per module) |
| `ledger_app` | yes | `NOINHERIT` member of every `_rw` except `signer_rw`; used by `api`, `worker`, `receiver` |
| `ledger_relay` | no | `SELECT, DELETE` on every `<schema>.outbox`, `INSERT` on every `<schema>.inbox`; `ledger_app` member, used only by the fan-out |
| `ledger_signer` | yes | member of `signer_rw` only; used only by `signer` |

No role is a superuser or `BYPASSRLS`. Generated by `ledger-devtools sync` into `init.sql` (local) and the Helm
chart's bootstrap job (production).

### 6.3 Row-level security

- **Tenant tables** (`organization_id`, `company_id`, `mode` + data): one policy per command, predicate
  `organization_id = (select current_setting('ledger.organization_id', true))::uuid AND company_id = (select
  current_setting('ledger.company_id', true))::uuid AND mode = (select current_setting('ledger.mode', true))`
  ([04] rule 7: evaluated once per statement). Every hot index leads with `(organization_id, company_id, mode)`
  or `(company_id, mode)`.
- **Organization tables** (`organization_id`, optionally `mode`): visible to the organization's transactions and
  those of its companies (v1 semantics kept).
- **Unscoped chassis tables** (`outbox`, `inbox`, `jobs` claim index, global registries): outside RLS by design,
  holding ids, states, dates and event payloads only; their readers are platform code.
- Foreign keys carry the tenant (`(organization_id, company_id, mode, x_id)`), so a row cannot point at another
  tenant's (v1 kept).
- Policies are produced by one parameterised builder (`platform.data.rls.policy(table, scope="tenant"|"organization")`,
  [05] D13) that migrations call; a **generated test per table** proves deny-by-default without context, and
  allow/deny across two tenants and two modes.
- A missing context fails closed (no rows seen, none written).

### 6.4 Migrations

- Alembic per schema (`version_table_schema = <schema>`), revisions in the package
  (`adapters/postgres/migrations/versions/rNNNN_*.py`), run by `ledger-migrate` in the manifests' dependency
  order, each schema under its owner role, one advisory lock per schema (10 min wait), each revision one
  transaction, never SQL text files (v1 kept).
- A module's first revision calls `platform.data.chassis_schema_v1(op, schema)` (outbox, inbox, jobs,
  idempotency_keys) instead of copying DDL ([05] D20). The function is versioned and immutable; a later chassis
  change ships `chassis_schema_v2` and each module adds a revision calling it.
- Expand-only while two versions run; contract steps in a later release. Readiness checks the schema is at or
  past this version's head (v1 kept).
- v1's identity revisions `r0001`/`r0002` are squashed into a new `r0001` (nothing is in production; [05] §7.7).

### 6.5 Ids, modes, shard key

- **Ids**: UUIDv7 inside (`uuid` columns), typed `NewType` per entity; **public ids are TypeIDs**
  (`inv_01jc5r3ahte8sb0d3zpx8n9q0k`: prefix + `_` + 26-char Crockford base32 of the UUIDv7), formatted and
  parsed only at the HTTP edge (§9.4). Prefixes are declared in manifests and unique (ADR 0008 context).
- **Mode**: every tenant row carries `mode` (`test` \| `live`); every credential is bound to one mode; a query
  never mixes modes because RLS includes the mode (ADR 0007).
- **Shard key**: `organization_id` on every tenant and organization row. Invariant: *no table, index, sequence
  or cache key is unique across organizations*, except the deliberate global registries: identity's
  `tax_registrations (scheme, value) → company` (one active company per taxpayer) and the receiver's route
  resolution that reads it. That registry is the future cell router's table ([04] rule 1).
- Times: `timestamptz` UTC; legal dates (`date`) computed in the regime's time zone at the domain edge.
- Money columns: `numeric` with the value's own scale (documents keep up to the regime's precision, e.g. unit
  prices to 4 places); currency `char(3)`.

### 6.6 Legal archive and backups

Signed XML (emitted and received), authority requests and responses go to object storage with object lock in
compliance mode for **10 years** (Código Tributario art. 50 h; DGII technical report §9; M4 decision 9),
keyed by content digest, never overwritten (`If-None-Match: *`). PostgreSQL stores the index and hashes, never
the only copy. Point-in-time recovery for the cluster, restore tested monthly.

---

## 7. Domain model

### 7.1 Tenancy

```
Organization (Zitadel organization; shard key)
 ├── Company (a legal entity / taxpayer)            ── jurisdiction, functional currency, tax ids, status
 │    ├── Establishment (neutral: code, name, address)
 │    ├── Membership (principal, role)               ── shared across modes
 │    └── per mode (test | live):  enrollments, certificates, ranges, invoices, webhooks, idempotency keys ...
 └── ApiClient (Zitadel machine user ↔ organization, mode, pinned API version)
```

- **Organization** = the Zitadel organization; its ledger id is UUIDv5 of the provider id (v1 kept, now inside
  the `zitadel` claims profile, [05] §4.1). It holds `api_version` (pinned at creation, §9.2).
- **Company**: `id`, `organization`, `legal_name` (generic limit 300; a channel enforces its own, e.g. e-CF 150),
  `jurisdiction: Jurisdiction` (country + optional ISO 3166-2 subdivision), `functional_currency: Currency`,
  `tax_ids: tuple[TaxId, ...]` (at least one; the primary one is the registry key), `status`
  (`active`/`inactive`), `fiscal_profile_locked_at` (set through `IdentityApi.lock_fiscal_profile` by `fiscal`
  before its first numbered document; after it, jurisdiction, functional currency and the primary tax id are
  immutable; R4), `version` (ETag).
- **Mode** is a tenancy dimension (ADR 0007): company and memberships are shared; everything a company *does*
  exists per mode. A company enrolled in `do-ecf-v1` uses `testecf` or `certecf` in test mode and `ecf` in live
  mode; live mode requires a live enrollment whose requirements are met (§7.6).
- **Establishments** replace v1's branches; v1's *emission points* are deleted from identity (a DGII-unneeded,
  country-specific concept; a channel that needs issuing points, e.g. Ecuador's *punto de emisión*, models
  them in its own schema; [05] §4.2).
- **ISV access** (design only, built after go-live): `company_grants(grantee_organization, company,
  permissions, expires_at)`; when built, the chassis resolves a grant and sets the RLS context to the *owner's*
  organization, so RLS is unchanged ([04] rule 3). The company header (`Ledger-Company`) already exists for it.

### 7.2 Parties

One `Party` with roles (`customer`, `supplier`, later `employee`), its qualified tax ids, legal name, addresses
(R4, [04] rule 4). Until the `parties` module ships (after go-live), commercial documents carry an inline
**party snapshot** (`{tax_id?, legal_name, address?, email?}`), which they keep forever anyway (a document
records what was true when it was issued); later a document may also name `party: pty_…` and the snapshot is
filled from it.

### 7.3 Qualified tax identifiers

- `TaxId(scheme: TaxIdScheme, value: str)`; scheme keys `<cc>.<name>` (`do.rnc`, `do.cedula`, `mx.rfc`,
  `co.nit`, `cl.rut`, `br.cnpj`, `eu.vat`) declared by regimes as `TaxIdSchemeDef(key, country, label,
  normalize, check, check_digit_enforced: bool, peppol_icd: str | None)` (ADR 0006).
- Validation goes through the regime registry: `Regimes.tax_id_scheme(key).check(value) -> TaxIdCheck`.
  `invalid_format` refuses; `checksum_mismatch` refuses only where the scheme says `check_digit_enforced`
  (false for `do.rnc` and `do.cedula`: DGII has issued numbers that fail the algorithm, [01] A7) and is
  otherwise reported as a warning on the response.
- **Global registry** (`identity.tax_registrations`): unique `(scheme, normalized_value)` among active
  companies, across organizations, outside RLS. Registering a held id answers 409 `tax_id_taken` and says
  nothing more (v1 kept); a deactivated company frees it. The receiver resolves `/do-ecf-v1/{rnc}` through it.
- Masking: `repr`, logs, spans and error messages show the scheme and the last three characters; events inside
  the database carry the value in clear (consumers need it), public webhooks never do (v1 §9 kept).

### 7.4 Money and currency

`Money(amount: Decimal, currency: Currency)` with v1's exact-arithmetic laws (no float, no silent rounding,
mixed currencies are an error, `< 10^18`, ≤ 24 decimals). `Currency` comes from the full ISO 4217 table with
minor units (R4; v1's three-value enum is deleted). Rounding happens only when asked, with a `RoundingRule`
from the regime (§8.2). A document has a document currency; when it differs from the company's functional
currency it records the exchange rate at issue (DGII requires it in the XML for foreign-currency e-CF), and
amounts in functional currency are computed and stored once.

### 7.5 Commercial vs fiscal documents

Two aggregates, two state machines (R4, [01] R6):

```
invoicing.Invoice      draft ──issue──► issued ──void──► voided            (void only if fiscal status allows)
                         └──────────────────────────────► (fiscal mirror: pending | accepted | accepted_with_observations
                                                           | rejected | not_applicable)
channel document       channel-specific state machine (do-ecf-v1 in §8.6.4), projected into fiscal's neutral
                       FiscalStatus by the channel's status map
```

- `Invoice.type ∈ {invoice, credit_note, debit_note}`, `direction ∈ {sales, purchase}` (purchase = a
  self-billed document the company issues on a supplier's behalf; DGII types 41, 43, 47). One resource covers
  all; v1/M5's separate `/v1/do/documents` disappears ([05]-style duplication removed).
- An accepted document is never voided; a credit note corrects it.
- Lines carry a **resolved tax snapshot**: `{category: "do.itbis.standard", rate_key, percent, since}` taken
  from the regime at the document's tax point (rule 12). Lines never reference a mutable rate.
- **Business key**: `external_reference` unique per `(company, mode, type)` forever; a duplicate answers 409
  `external_reference_taken` with the existing id in the problem's `existing_id`.

### 7.6 Enrollments and capabilities

`fiscal.Enrollment(company, mode, channel, environment, status, requirements)` — Stripe Connect capabilities
for fiscal channels ([01] R10): `status ∈ {pending, active, disabled}`; `requirements = {currently_due:
["certificate", "range.31", "authority_registration"], disabled_reason}` computed by the channel
(`Channel.requirements(company_state)`). Onboarding a company = satisfying requirements; adding a country =
publishing a new requirement set. A document is routed to the channel of the company's active enrollment for its
mode; no active enrollment → `fiscal.status = not_applicable` (the invoice is still a commercial document).

### 7.7 Permissions and roles

- Permissions are **open**, declared by modules and channels in manifests (`<module>.<resource>.<action>`).
  The kernel holds only the `Permission` value type; v1's closed `Permission` enum and identity's hard-coded
  role table are deleted (R4, [05] S1).
- Built-in roles (`owner`, `admin`, `accountant`, `issuer`, `viewer`) are identity's vocabulary; each
  permission's manifest lists the built-in roles that hold it; `ledger-devtools sync` generates identity's
  `ROLE_GRANTS` table into `ledger_identity/_role_grants.py` from every installed manifest, and `check`
  validates that role names exist. `owner` holds every permission by construction; `identity.ownership.manage`
  is `owner` only.
- An organization-level role (`ledger.organization.admin`, a Zitadel project role) registers companies (v1 kept).
- Authorization per request (no cache): `IdentityApi.permissions(call, principal, company)` in process,
  one indexed read; v1's 30 s cache, its invalidation events and the gRPC `IdentityQueryService` are deleted
  ([05] §3.1). A caller without any role in a company gets 404 for it; a role without the permission gets 403
  (v1 kept). A deactivated company grants only `*.read` permissions.
- Custom roles (organization-defined permission sets) are designed as a later identity feature; the manifest
  model already supports it (roles are data).

### 7.8 Entitlements (design only)

`plan → features{code, kind: gate|quota, value}` with per-organization overrides, resolved next to permissions
on the `Call` (`call.entitled("fiscal.channels.do-ecf-v1")`); countries and modules are features ([04] rule 33).
Not built for go-live; nothing in the design prevents it.

### 7.9 Accounting (design only, R4)

Immutable journal entries of ≥ 2 postings balanced per currency (debit/credit, positive amounts, functional
amount and rate, dimensions `party`, `establishment`, `cost_center`, `project`); corrections by reversal only;
`effective_date` vs `recorded_at`; closed periods refuse postings; balances derived with optimistic versions;
every entry names its source and is unique by `(company, mode, source_type, source_id, purpose)`; accounting
consumes invoicing and fiscal events; charts of accounts and country taxonomies (SAT código agrupador, PUC) are
regime data ([04] rules 18-25).

---

## 8. Localization architecture

### 8.1 Regime vs channel

| | Regime | Channel |
|---|---|---|
| What | Tax law of a jurisdiction | One authority format + transport, one major version |
| Key | `do` (or `us-ca`) | `do-ecf-v1`, later `mx-cfdi-v4`, `co-dian-v1.9`, `eu-peppol-bis3-v1` |
| Content | currency, time zone, tax-id schemes, tax categories with dated rates, document types and correction rules, rounding rule, `ext` definitions, sources | XML/JSON builders, vendored schemas + pins, signing profile, gateway client, numbering, status map, state machine, contingency, receiver routes, requirements, conformance suite |
| Code shape | pure typed data + pure functions; imports `ledger_kernel` only; no I/O | layered like a module; imports `ledger_fiscal_kit`, `ledger_platform`, `ledger_kernel` |
| Used by | any module through `Regimes` (identity: tax ids; invoicing: calculator; fiscal: catalog API) | the `fiscal` module through `Channels`; the `receiver` app |
| Registry | entry points `ledger.countries.regimes` | entry points `ledger.countries.channels` |

One country routinely has several channels and each versions on its own legal calendar; a fiscal document
records the regime and channel keys it was produced with (ADR 0005, [01] R1).

### 8.2 Regime packs

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class RegimeDef:
    key: RegimeKey                         # "do"
    jurisdiction: Jurisdiction
    currency: Currency
    time_zone: str                         # IANA
    tax_id_schemes: tuple[TaxIdSchemeDef, ...]
    tax_categories: tuple[TaxCategoryDef, ...]      # each with rates: Timeline[RateValue]
    document_types: tuple[DocumentTypeDef, ...]     # code, label, kind, direction, requires_buyer_tax_id, corrects
    rounding: RoundingRule                 # mode (half_up), points (line | category | document), tolerance per line
    ext: tuple[ExtDef, ...]                # namespaced extension fields: key, type, allowed values, required_when
    parameters: Mapping[str, Timeline[Decimal | int | str]]   # dated thresholds, e.g. "rfce_threshold"
    sources: tuple[Source, ...]            # title, url, version, retrieved_on: every value cites a norm
```

- `RateValue(since: date, percent: Decimal, until: date | None)`; lookup by tax point in the regime's time
  zone; values newest first, ordering validated at load ([01] R4).
- **The single tax calculator** `TaxCalculator(regime).totals(lines, tax_point) -> Totals` (per line, per
  category, document; remainder handling explicit) is used by invoicing to compute and by channels to verify
  (they refuse `totals_disagree` rather than send a different total; M5 decision 6 generalized). An external
  engine (Avalara, Stripe Tax) would be an adapter returning the same `Totals` ([01] R11).
- `Regimes.load()` reads the entry points once at startup, validates (unique keys, ordered timelines, sources
  present, every document type's corrections resolvable), freezes into a `Mapping[RegimeKey, RegimeDef]`.
  A duplicate or invalid pack fails the start.

### 8.3 Channels

```python
class Channel(Protocol):                                   # ledger_fiscal_kit.channels
    definition: ChannelDef                                 # key, regime, valid_from/until, environments, sources
    def requirements(self, state: EnrollmentState) -> Requirements: ...
    async def check(self, call: SystemCall, draft: FiscalDraft) -> Sequence[Objection]: ...     # no side effects
    async def issue(self, call: SystemCall, draft: FiscalDraft) -> Issued: ...                  # idempotent by draft.document
    async def resume(self, call: SystemCall, company: CompanyId, reason: ResumeReason) -> None: ...
    def assemble(self, context: ChannelContext) -> ChannelParts: ...   # routers, receiver routes, jobs, consumers, migrations
```

- **Versioned formats**: a channel key names one major format version; a minor authority revision (a
  corrected XSD) is a new pinned schema set inside the same channel, selected by `valid_from`. Two versions of a
  format coexist during a legal transition as two channels (`co-dian-v1.8`, `co-dian-v1.9`); the enrollment
  names one ([01] B3).
- **State machines are per channel** and explicit (`enum` of states, a transition table, a `transition()` that
  refuses anything else); the kit generalizes the plumbing, never the authority's states ([01] B6.1).
- **Status mapping tables**: every authority code maps to `(channel_state, FiscalStatus, severity)` in one
  table per channel; the conformance suite fails if an authority code in the vendored documentation is
  unmapped ([01] B5).
- **Numbering ranges & voiding**: `AuthorizedRange(document_type, prefix, first, last, expires_on | None)`,
  allocation under the range's row lock in the transaction that records the document; unused numbers leave only
  through a formal void (`RangeVoid`), itself a document with its own small state machine; invariant: every
  number in `[first, next)` is used by a document or covered by a void ([01] B6.11).
- **Contingency** is a document attribute fixed when the document is signed (`emission_mode = normal |
  contingency`), with the channel's submission window as dated regime data ([01] R8).
- **Severity drives retry**: `info | warning` retried by the job; `error` parks the document until a person acts
  ([01] A2 `blocking_level`).

### 8.4 `fiscal-kit` (`packages/fiscal-kit`, `ledger_fiscal_kit`)

Country-agnostic plumbing every channel reuses; depends on `ledger_platform` and `ledger_kernel`.

| Module | Public surface |
|---|---|
| `channels` | `Channel`, `ChannelDef`, `Channels.load()` (registry), `ChannelContext`, `ChannelParts`, `Requirements`, `Objection` |
| `drafts` | `FiscalDraft` (neutral document handed to channels: seller snapshot, buyer snapshot, lines with resolved tax, totals, currency + rate, references, `ext`, issue date, tax point, mode, environment) |
| `status` | `FiscalStatus` (`pending`, `accepted`, `accepted_with_observations`, `rejected`, `cancelled`, `voided`), `Severity`, `StatusMap[Code]` (exhaustiveness check) |
| `numbering` | `AuthorizedRange`, `NumberAllocator` (row-lock allocation), `RangeVoid`, range watch thresholds (80 % used; 30/7/1 days before expiry) |
| `submission` | `SubmissionStep` protocol, `advance()` loop for the `<channel>.advance` job (read → act outside a transaction → record), `CircuitBreaker` (per environment), `PollSchedule` |
| `ports` | `SignerPort` (sign XML, sign seed; implemented by the signer module's client), `CompanyDirectory` (company by tax id; implemented by `fiscal` over `IdentityApi`), `ArchivePort` (over `platform.blobs`) |
| `xml` | hardened parser (no entities, DTDs, network; size limits), `SchemaSet` (vendored XSDs, SHA-256 pins, load-time corrections pinned by count), C14N helpers |
| `events` | kit-owned event types every channel publishes: `fiscal_channel.document.numbered`, `.finalized` (status, authority messages ≤ 20 × 500 chars), `.blocked`, `fiscal_channel.received_document.recorded` |
| `testing` | `ChannelConformance` (pytest suite), `GoldenFiles` harness, `FakeAuthority` base (scripted outages, delays, unknown outcomes), `RegimeConformance` |

### 8.5 How a document flows (any country)

```
POST /v1/invoices (api)
  invoicing: validate, TaxCalculator(regime) totals
  → IssuePolicy.check(document)            [fiscal: enrollment → FiscalDraft → channel.check]  objections → 422, nothing stored
  → UoW(invoicing): store issued + publish invoicing.invoice.issued                             → 201 with fiscal.status = pending
worker: fiscal.commercial_documents consumer
  → channel.issue(draft)                    [channel UoW: allocate number under lock, store, enqueue <channel>.advance,
                                             publish fiscal_channel.document.numbered]
worker: <channel>.advance job                [sign via SignerPort → archive → submit → poll … RunAgain/Park/Done]
  → fiscal_channel.document.finalized
worker: fiscal.channel_documents consumer → fiscal projection → fiscal.document.updated
worker: invoicing.fiscal_status consumer  → invoice.fiscal mirror → invoicing.invoice.fiscal_status_changed (public)
worker: webhooks.public_events consumer   → delivery jobs → client endpoint
```

### 8.6 The Dominican pack: regime `do` and channel `do-ecf-v1`

#### 8.6.1 Layout

```
countries/do/
├── manifest.toml
├── pyproject.toml                         GENERATED
├── README.md
├── docs/{sources.md, certification/, runbooks/}
├── src/ledger_country_do/
│   ├── regime/                            imports ledger_kernel only
│   │   ├── __init__.py                    REGIME: RegimeDef
│   │   ├── tax_ids.py                     do.rnc (9 digits), do.cedula (11 digits); check digit reported, not enforced
│   │   ├── taxes.py                       ITBIS (18 %, 16 %, 0 %, exempt), ISC, legal tip 10 %: dated
│   │   ├── document_types.py              31, 32, 33, 34, 41, 43, 44, 45, 46, 47
│   │   ├── corrections.py                 33/34 → originals; credit notes after 30 days carry the late indicator
│   │   ├── parameters.py                  rfce_threshold DOP 250,000; contingency window; legal name limit 150
│   │   └── ext.py                         do-ecf-v1.* extension definitions (income type, payment type, ...)
│   ├── channels/ecf_v1/
│   │   ├── __init__.py                    CHANNEL: Channel
│   │   ├── events.py                      channel-only events (range low/expiring), kit events re-exported
│   │   ├── assembly.py
│   │   ├── domain/                        ecf.py (state machine), types/<one module per type>.py, ranges.py,
│   │   │                                  voids.py, received.py, approvals.py, status_map.py, security_code.py
│   │   ├── application/                   issuing.py, advancing.py (steps), voiding.py, receiving.py,
│   │   │                                  approving.py, ranges.py, queries.py, ports.py
│   │   └── adapters/
│   │       ├── xml/                       writer, names (element names read from the XSDs), builders per format:
│   │       │                              ecf, rfce, anecf, acecf, arecf; qr.py
│   │       ├── xsd/                       DGII's 15 XSDs byte for byte + published.py (URL, SHA-256, corrections)
│   │       ├── dgii/                      endpoints.py, client.py (seed→token per company×environment, 1 h),
│   │       │                              wire.py, breaker via fiscal-kit
│   │       ├── postgres/                  tables.py, repositories/, migrations/ (schema do_ecf)
│   │       ├── http/                      public routes /v1/do/... (api app)
│   │       └── receiver/                  /do-ecf-v1/{rnc}/fe/... (receiver app)
│   └── testing/                           fake_dgii/ (written from DGII's documentation), samples
└── tests/{unit, integration, contract, conformance}/   golden/ (one XML per type, RFCE, ANECF, ACECF, ARECF)
```

#### 8.6.2 Regime `do` (data)

| Item | Value (each with a `Source`) |
|---|---|
| Currency, time zone | DOP; `America/Santo_Domingo` |
| Tax-id schemes | `do.rnc` (9 digits, weights 7 9 8 6 5 4 3 2), `do.cedula` (11 digits, Luhn); `check_digit_enforced = false` |
| Tax categories | `do.itbis.standard` 18 %, `do.itbis.reduced` 16 %, `do.itbis.zero` 0 % (exports; DGII "ITBIS 3"), `do.itbis.exempt`, `do.isc.*` (later), `do.tip.legal` 10 % (later); generic UBL category mapping (`standard`, `reduced`, `zero`, `exempt`, `outside_scope`) kept from M5 decision 3 |
| Document types | 31 crédito fiscal, 32 consumo, 33 nota de débito, 34 nota de crédito, 41 compras, 43 gastos menores, 44 regímenes especiales, 45 gubernamental, 46 exportaciones, 47 pagos al exterior; each with kind, direction, buyer-tax-id rule, which types it may correct |
| Rounding | half up, to two places at DGII's three points (line amount; taxable base from tax-included prices; category tax); unit prices to four places; tolerance ±1 per line and, globally, as many units as lines (technical report §12-13) |
| Parameters (dated) | RFCE threshold DOP 250,000 (type 32 below it is summarized); consumer invoices from DOP 250,000 name their buyer; contingency submission window (72 h after reconnection per technical report §19; DGII's contingency guide states 15 calendar days: to verify, Q6); legal name ≤ 150 |
| Default document type | invoice → 31 if the buyer has a tax id, else 32; credit note → 34; debit note → 33; purchase → `fiscal.do.ecf_type` required (41, 43 or 47); 44/45/46 by explicit `fiscal.do.ecf_type` |

#### 8.6.3 Channel `do-ecf-v1`: environments, endpoints, numbering

- Environments `testecf`, `certecf` (test mode), `ecf` (live mode only). Hosts `https://ecf.dgii.gov.do/{env}/...`
  (seed, seed validation, reception, result, track ids, voids) and `https://fc.dgii.gov.do/{env}/recepcionfc/...`
  (RFCE). `certecf` publishes neither the track-id query nor voids (refused before recording). In live mode the
  client refuses any host but DGII's (M4 decision 12).
- Token: seed → signed by the signer (`SignSeed`) → validated → token, cached per company × environment in the
  worker's memory until 5 minutes before its 1 h expiry; one retry on 401.
- Retries only for calls that cannot change DGII's state (seed, validation, results, track ids). A submission is
  never retried blindly: when the connection breaks after the request may have left, the document records
  `outcome_unknown` and the next step asks DGII for the e-NCF's track ids first (M4 decision 15).
- Circuit breaker per environment (5 failures; 30 s doubling to 10 min); its state is a gauge.
- Numbering: e-NCF = `E` + 2-digit type + 10-digit sequence; ranges registered by the client from DGII's
  authorization (`POST /v1/do/numbering_ranges`); every type's range is dated except 32 and 34 (registering a
  range dated when its type is not, or the reverse, is refused, M4 decision 13); TesteCF ranges 1-10,000,000
  (type 32 to 50,000,000).
- Range voids (ANECF): withdraw not-yet-used numbers of one range; numbering skips them; their own states
  `requested → signed → submitted → accepted | rejected`.

#### 8.6.4 e-CF state machine

```
               ┌──────────────── Park(certificate missing/expired) ───────────────┐
               ▼                                                                   │
 numbered ──sign──► signed ──submit──► submitted ──poll──► in_process ──poll──┐    │
    │      (XSD-valid, signed,  │  (track id)       │                         │    │
    │       archived, security  │                   ▼                         ▼    │
    │       code, emission_mode)│        accepted | accepted_conditionally | rejected   (terminal)
    │                           │
    │                           ├─(type 32 < threshold)─► summary_submitted ─► accepted | rejected (decided at once)
    │                           └─(connection broke after send)─► outcome_unknown ─(track-id query)─► submitted | signed
    └─ blocked (error severity: e.g. range expired after numbering) ─ operator action ─► numbered
```

Status map (DGII result code → state, `FiscalStatus`): `1 Aceptado → accepted, accepted`; `4 Aceptado
Condicional → accepted_conditionally, accepted_with_observations`; `2 Rechazado → rejected, rejected`;
`3 En Proceso → in_process, pending`; `0 No encontrado → outcome_unknown, pending` (exact semantics of 4 and of
the RFCE answer to verify against the current *Descripción Técnica*, Q6). A rejected e-NCF stays used; the
client corrects through a new document.

Polling `RunAgain` schedule: 2 s, 5 s, 10 s, 30 s, 1 min, then every 5 min; DGII outage: `RunAgain` at the
breaker's reopen (1 min growing to 15 min); certificate missing or expired: `Park`, woken by `fiscal` on
`signer.certificate.installed`; job deadline 15 days, alert long before (§11).

#### 8.6.5 Reception and approvals

Received e-CF (§3.8) are recorded per company and mode (`rdoc_`), announced as
`fiscal_channel.received_document.recorded` (public `received_document.created`); the client approves or
rejects commercially (`POST /v1/do/received_documents/{id}/approval`), which enqueues an ACECF to the emitter
and to DGII on the same job machinery.

#### 8.6.6 Public DO resources

`/v1/do/numbering_ranges`, `/v1/do/range_voids`, `/v1/do/received_documents` (+ approval). Everything else of
a DO e-CF is in the invoice's `fiscal.do` sub-object (§9.8).

### 8.7 Conformance suites

- **`RegimeConformance`** (every regime): timelines ordered and non-overlapping; every value has a source;
  document-type corrections resolvable; calculator laws (total = sum of parts; independent of line order;
  rounding at the declared points) with hypothesis; tax-id scheme property tests and the known
  checksum-failing-but-valid samples.
- **`ChannelConformance`** (every channel): golden draft → document for every document type (canonicalized,
  byte-compared); every golden and every hypothesis-generated document validates against the vendored schemas
  with two independent validators (libxml2 and xmlschema, M4 decision 5); schema fingerprint pins; every
  authority code mapped; numbering properties (no duplicate, no silent gap, under concurrency); state machine
  exhaustiveness; signer round trip verified by an independent verifier (xmlsec1); workflow tests against the
  `FakeAuthority` (outages, `En Proceso` loops, conditional acceptance, unknown outcomes); masking (no tax id,
  token or document in logs).
- **Certification scripts** (opt-in, never CI): the authority's certification steps against its test
  environment (`certecf`), evidence written to `countries/<cc>/docs/certification/`.

### 8.8 Quality tiers (per channel)

| Tier | Requires |
|---|---|
| **Bronze** | regime complete for the channel's scope; `ChannelConformance` green; every document type golden and schema-valid |
| **Silver** | end to end in the authority's test environment; contingency and unknown-outcome paths tested; runbooks exist |
| **Gold** | authority certification passed (evidence committed); two owners; nightly run against the test environment green for 30 days |

Each rule is `done`, `todo` or `exempt(reason)` in the manifest; `check` rejects a tier claim with a `todo` at or
below it ([03] HA quality scale). `do-ecf-v1` must be Gold before live mode is offered.

---

## 9. Public API conventions

### 9.1 Shape

REST + JSON, OpenAPI 3.1 generated from code (one document assembled from every mounted module's fragment),
published, and snapshotted per module (`openapi.json`, contract test). Strict request schemas (unknown fields
rejected); clients must ignore unknown response fields (stated in the compatibility contract).

### 9.2 Versioning: path major + dated versions

- `/v1` in the path is the major (ADR 0008).
- **Dated versions** inside it: header `Ledger-Version: 2026-11-01`. Each organization and each API client is
  **pinned** to the current version at creation; a request may override with the header. The response echoes
  `Ledger-Version`.
- Machinery from day one, zero changes registered: `http.ApiVersions` holds `[(date, [VersionChange])]`; core
  code builds only the current shape; a response is walked backwards through the changes between now and the
  caller's version (`VersionChange.downgrade(resource, body)`), request bodies forwards. A change that is not a
  pure transformation is flagged `has_side_effects` and read with `call.version_before(date)`; rare and reviewed.
- Cadence ([04] rule 8): monthly non-breaking versions; at most two breaking releases a year; each change has a
  written review and a changelog entry generated from its `VersionChange` docstring.
- Webhook payloads are thin and unversioned (§9.9), so versions never break receivers.

### 9.3 Envelope

Every resource: `id` (TypeID), `object` (`"invoice"`), `livemode` (bool), `created`, `updated` (ISO 8601 UTC),
`metadata` (mutable objects only: ≤ 50 keys, key ≤ 40 chars, value ≤ 500 chars, strings only, never indexed,
never PII by contract). Polymorphism by `type` + a sub-object named after it ([04] Stripe). Optional heavy
fields by `include[]`, never recursive `expand` ([04] rule 13).

### 9.4 Ids

Public ids are TypeIDs (§6.5); a path parameter with the wrong prefix is a 404 `not_found` (not a 422), so ids
cannot be probed by type. Internal UUIDs never appear in the API.

### 9.5 Idempotency and business keys

- `Idempotency-Key` required on every `POST` (and `DELETE`), ≤ 255 chars, scoped to (organization, mode,
  principal), stored **7 days** with the response in the module's `idempotency_keys` table in the same
  transaction as the work (v1 semantics: a failed request keeps nothing; a concurrent twin waits and replays
  with `Idempotent-Replayed: true`; same key with another body → 422 `idempotency_key_reused`).
- `external_reference` (§7.5) is the permanent business key for documents.

### 9.6 Errors (RFC 9457, kept)

`application/problem+json` with `type` (`urn:ledger:error:<code>`), `title`, `status`, `code`, `detail`,
`request_id` (also the `Request-Id` header), `doc_url`, and extension members per code (`errors[]` ≤ 20 for
`invalid_request`, `authority_messages[]` for fiscal rejections, `existing_id` for business-key conflicts). One
kind → status table shared by HTTP and gRPC:

| Kind | HTTP | gRPC |
|---|---|---|
| `INVALID` | 422 | `INVALID_ARGUMENT` |
| `NOT_FOUND` | 404 | `NOT_FOUND` |
| `CONFLICT` | 409 (412 for a stale `If-Match`, 428 without it) | `FAILED_PRECONDITION` |
| `UNAUTHENTICATED` | 401 + `WWW-Authenticate: Bearer` | `UNAUTHENTICATED` |
| `FORBIDDEN` | 403 | `PERMISSION_DENIED` |
| `UNAVAILABLE` | 503 (+ `Retry-After` when known) | `UNAVAILABLE` |
| `INTERNAL` | 500, code `internal`, nothing else | `INTERNAL`, code `internal` |

Error codes are part of the versioned contract; the catalogue in `docs/reference/errors.md` is generated
(the README/spec copies of v1 are deleted, [05] D36).

### 9.7 Tenancy on the wire, pagination, concurrency

- Company-scoped resources take the company in the header `Ledger-Company: cmp_…` (Stripe-Account style; the
  ISV seam, §7.1); `/v1/companies/{id}` takes it in the path. Mode comes from the credential (API clients) or,
  for human tokens, the required header `Ledger-Mode: test|live` (400 `mode_required` without it on moded routes).
- Cursor pagination (`limit` 1-100, opaque `starting_after`/`ending_before` cursors), `has_more`; one generic
  `Page[T]` model ([05] D23).
- `ETag` = resource version; `If-Match` required on updates (428 without, 412 stale), except idempotent
  set-value `PUT`s (v1 kept).
- Amounts as strings; dates ISO 8601; country codes ISO 3166-1 alpha-2 upper case; currencies ISO 4217.

### 9.8 Country rules as data, country fields namespaced

- `GET /v1/countries` and `GET /v1/countries/{cc}` publish each regime and its channels as data: currencies,
  time zone, tax-id schemes, tax categories with dated rates, document types, rounding, `ext` definitions,
  channel versions and environments, requirements ([04] rule 12). Clients build forms from it.
- Country-specific *request* fields go under `fiscal.<regime>` (`"fiscal": {"do": {"ecf_type": "44",
  "income_type": "01"}}`), validated by the channel's `ext` definitions (unknown key → 422 `field_unknown`).
- Country-specific *response* fields live in a discriminated sub-object: `invoice.fiscal = {"status":
  "accepted", "channel": "do-ecf-v1", "environment": "testecf", "do": {"encf": "E310000000001", "track_id":
  "…", "security_code": "…", "qr_url": "…", "signed_at": "…"}}`. The generic invoice never learns DGII's words.
- Line taxes: `taxes: [{"category": "do.itbis.standard", "percent": "18", "amount": "…"}]`; party tax ids:
  `{"scheme": "do.rnc", "value": "…"}`.

### 9.9 Webhooks (Standard Webhooks)

- Endpoints per company and mode (`hook_`), event filter by public names, sealed secrets (`whsec_` shown once;
  AES-256-GCM under a rotating keyring, M5 decision 12), rotation with overlap (both signatures sent).
- Wire format: [Standard Webhooks](https://www.standardwebhooks.com/) `webhook-id`, `webhook-timestamp`,
  `webhook-signature: v1,<base64 HMAC-SHA256("id.timestamp.body")>`; signed per attempt (fresh timestamp).
- **Thin payloads**: `{id: "evt_…", type: "invoice.fiscal.accepted", created, livemode, data: {object: {id,
  object}, changes?: [field names]}}`: never lines, parties or tax ids. Receivers fetch the object.
- At least once, no ordering, dedupe by `webhook-id`; retries for 3 days with backoff (1 min, 5 min, 30 min,
  2 h, 6 h, then every 12 h); deliveries are jobs; SSRF defence at registration and at every attempt with
  pinned resolution (M5 decision 13 kept).
- Public events (initial): `invoice.issued`, `invoice.voided`, `invoice.fiscal.numbered`,
  `invoice.fiscal.accepted`, `invoice.fiscal.rejected`, `received_document.created`, `certificate.expiring`,
  `numbering_range.low`, `numbering_range.expiring`, `webhook.test`.
- A public events API (`GET /v1/events`, 30 days) is designed, built after go-live (R4).

---

## 10. Security

v1 §9 holds except where noted.

| Concern | Measure |
|---|---|
| Identity provider | Zitadel v4, unmodified (AGPL network use). Token verification generic (issuer, audience, asymmetric algorithms only, leeway, access-token shape per RFC 9068); provider-specific claims only in `auth.providers.zitadel` (`ClaimsProfile`): organization from `urn:zitadel:iam:user:resourceowner:id`, roles only from the audience project's claim (v1 kept) |
| API credentials | Zitadel machine users (private-key JWT or client credentials) registered in `identity.api_clients` with their organization, mode and pinned version |
| Certificate custody | §3.7: signer process only; envelope encryption with KMS; subject must match the company (M3 decision 1's reading); validity window; chain to an accepted DR certification authority; revocation checks with the certification milestone |
| Secrets | from the cloud secret manager (External Secrets Operator); never in images, repos, `repr`, logs or errors |
| Telemetry leaks | v1 rules kept verbatim: excerpts only, exception type only on spans, no locals in tracebacks, DB/validation errors logged by SQLSTATE/locations, never values |
| Tax ids | clear inside the database and internal events; masked in logs, spans, errors, `repr`; never in webhooks |
| Transport | TLS 1.3 at the edge; DGII's certificate verified; mTLS inside with the mesh (deployment milestone); until then signer callers authenticate with a rotated shared token |
| Public API | OAuth 2.0 only; idempotency; strict schemas; request size limits in the app (1 MiB JSON, 64 KiB per webhook body, 2 MiB for a certificate upload) and later at the ingress |
| Rate limits | per credential, GCRA in process per replica (limit ÷ replicas) until the ingress takes it; per-company job concurrency (§3.6) |
| Receiver | separate host name; per-RNC and per-IP rate limits; size limit before parsing; hardened XML parser; signature verified before anything is stored but the raw archive copy |
| XML | lxml with entity resolution, DTDs and network disabled; size limits before parsing |
| Database | per-module roles (§6.2); no superuser/BYPASSRLS anywhere; signer schema reachable only by the signer login |
| Supply chain | uv lock with hashes, Renovate, image signing (cosign), SBOM, vulnerability scan, pip-audit |
| Audit | append-only audit tables for security-relevant acts (signatures, certificate changes, membership changes, API client changes, operator replays) |
| Compliance | Reglamento 587-24 obligations for PSFE (data location included) checked item by item before choosing the cloud region |

---

## 11. Observability and operations

- **Traces**: OpenTelemetry; HTTP, gRPC (signer), SQL (SQLAlchemy instrumentation), DGII calls, jobs and
  consumers. The trace crosses the outbox and the job queue: rows store the `traceparent` of the span that wrote
  them; a consumer or job span continues it (links, not parents, when one job serves many events). Exporters drop
  exception messages, stack traces, status descriptions and raw paths (v1 kept).
- **Metrics** (Prometheus, `ledger_` prefix, no tenant labels, [04] rule 35): HTTP and gRPC RED by route
  template; `ledger_outbox_pending{schema}`, `ledger_outbox_oldest_seconds{schema}`;
  `ledger_inbox_pending{consumer}`, `ledger_inbox_dead_total{consumer}`; `ledger_jobs_ready{kind}`,
  `ledger_jobs_oldest_ready_seconds{kind}`, `ledger_jobs_parked{kind,reason}`, `ledger_jobs_dead_total{kind}`;
  `ledger_fiscal_documents{channel,state}` (from a periodic count), `ledger_authority_requests_seconds{channel,
  environment,operation,outcome}`, `ledger_authority_breaker_open{channel,environment}`;
  `ledger_certificates_expiring{days_bucket}`; DB pool usage. Queue gauges read tables: aggregate with `max`.
- **Logs**: structlog JSON; every record carries app, environment, request id, trace/span ids, and the tenant
  (`organization_id`, `company_id`, `mode`) bound by the platform; never personal data beyond ids, never XML,
  keys or tokens.
- **Health**: every app serves `/livez`, `/readyz`, `/metrics` (signer: HTTP probes on a private port beside
  gRPC). Readiness: database (own connection), schema at head, LISTEN connection (worker), signer reachable
  (api/worker/receiver), the app's own checks. Liveness only fails on wedged checks or a worker stopped for good
  (v1 semantics kept, NATS parts removed). Bounded shutdown: HTTP drain, job leases released, spans flushed,
  each with a time limit.
- **Alerts**: SLO burn rates; any new dead inbox row or dead job; outbox oldest > 1 min; jobs oldest ready > 2
  min; any e-CF non-final after 1 h with DGII up, or contingency age > 50 % of the legal window; DGII breaker
  open > 10 min; certificate expiring < 7 days; range < 20 % or expiring < 7 days.
- **Data-quality checks** (schedules, paging on failure; [04] rule 24): every issued invoice with an active
  enrollment has a fiscal document within 5 min; every number in every range is used or voided; no fiscal
  document is non-final beyond its deadline.
- **Operator controls**: list stuck documents by company/state, retry/wake a parked job, replay a dead inbox
  row, all audited (`ledger-worker` subcommands; later an admin console).
- Dashboards as code; a runbook linked from every alert (`docs/runbooks/<package>/`).

## 12. Performance targets and SLOs

| Target | Value | Notes |
|---|---|---|
| API availability | 99.9 % monthly | v1 |
| `POST /v1/invoices` latency | p95 < 300 ms, p99 < 800 ms | excludes DGII; includes the channel check |
| Read endpoints | p95 < 100 ms | single-company reads |
| Final DGII state | 99 % of documents within 5 min when DGII is up | v1; measured from `issued` |
| Event fan-out | p95 < 250 ms commit → handler start (NOTIFY path), p99 < 1.5 s (poll path) | |
| Job pickup | p95 < 1 s from `run_at` | |
| Signing | p95 `SignXml` < 50 ms end to end over gRPC | M3 measured p95 0.81 ms in process |
| Build + validate + sign | p95 < 100 ms for a 60-line e-CF | M4 measured p95 7.4-10.9 ms |
| Capacity | sustain 50 documents/s per worker replica; load-test at 20× the expected peak before go-live and month-end | [04] rule 37; peak assumption to be confirmed (Q4) |
| Test suite | full gate < 10 min in CI with xdist; unit tests of a package < 30 s locally | §14 |

## 13. Deployment

- **One image** (`deploy/docker/ledger.Dockerfile`): multi-stage `uv sync --locked --no-dev` of the whole
  workspace, distroless base, non-root, read-only file system; console scripts `ledger-api`, `ledger-worker`,
  `ledger-signer`, `ledger-receiver`, `ledger-migrate`. No per-package Dockerfile lists workspace manifests
  ([05] D33).
- **Local**: `docker compose -f deploy/local/compose.yaml up --wait` (PostgreSQL 18 with generated roles,
  LocalStack 4.14.0 for KMS and S3 with object lock, the fake DGII, the five apps from the one image; Zitadel
  behind profile `zitadel`); `docker compose watch` rebuilds on change. Tilt and NATS are removed. Image pins
  live once in `deploy/local/images.toml`, read by compose generation and `ledger_testing.containers` ([05] D30).
- **Kubernetes smoke**: `kind` with the Helm chart in CI nightly (from the deployment milestone).
- **Production** (deployment milestone): managed Kubernetes, one Helm chart `deploy/helm/ledger` with a values
  block per app (replicas, resources, probes, network policies: signer reachable only from api/worker/receiver,
  receiver on its own ingress host), Argo CD, managed PostgreSQL with PITR, object storage with object lock, cloud
  KMS, External Secrets. Ingress (rate limits, request size) chosen then; Envoy Gateway is not assumed (R2).
- Configuration only through `LEDGER_*` variables, fail-closed on unknown names (v1 kept).
- Region chosen after the Reglamento 587-24 check.

## 14. Testing strategy

### 14.1 Levels and layout

| Directory | What | Infrastructure |
|---|---|---|
| `tests/unit` | domain, application with fakes, regime data, builders | none; runs on Windows in seconds |
| `tests/integration` | repositories, RLS, migrations, outbox/inbox/jobs, HTTP through the app, gRPC signer | PostgreSQL (testcontainers), LocalStack |
| `tests/contract` | OpenAPI snapshot, event JSON Schemas, protobuf (buf), golden XML | none |
| `tests/conformance` | `RegimeConformance`, `ChannelConformance` instances | as needed |
| repo `tests/` | architecture invariants no tool expresses (e.g. every tenant table has a policy test) | PostgreSQL |

### 14.2 Rules

- **Per-package coverage**: each package's tests measure only that package (`--cov=<import package>`), branch,
  `fail_under = 100`, `core = "sysmon"` (M4 decision 17); no package covers another's code ([03] anti-pattern 7).
- **Parallel**: `pytest-xdist -n auto`; one PostgreSQL container per session; each xdist worker gets its own
  database cloned from a migrated template (`CREATE DATABASE w<n> TEMPLATE ledger_template`), so lock-detection
  helpers never see another worker's sessions.
- **Session-scoped expensive things**: containers, the RSA keys of the local issuer, the migrated template, the
  app instance where tests isolate by organization (a fresh organization per test is cheap; [05] §6.2).
- **No time-based proofs**: no `sleep` to prove something did *not* happen and no `sleep(0)` to order tasks.
  Absence is proved by draining deterministically: `await dispatcher.run_until_idle()`, `await
  jobs.run_due(now=clock.at(...))`, then assert. Presence may use `eventually` (bounded). Time is a `Clock`
  injected everywhere and advanced by tests.
- **Shared harness** = `ledger_testing` registered as a pytest plugin (`pytest11` entry point) providing
  `postgres`, `database` (per worker), `local_issuer`, `signer_fake`, `clock`, `restore_logging`, `tracing`, and
  helpers (`waiting_for_a_lock`, `fail_rollback`, `occupy_a_port`) once ([05] D26-D28). A package's `conftest.py`
  holds only its own fixtures.
- **Property tests** (hypothesis) for money, calculator, tax-id schemes, numbering, state machines, codecs.
- **End to end**: compose stack; nightly against DGII's `TesteCF` with a test certificate once an enrolled test
  taxpayer exists (Q2); certification scripts opt-in.
- **Load**: k6 (pinned digest, open-model constant arrival rate) against staging before each release.

### 14.3 Gate (R6)

ruff (ALL), basedpyright `all` + mypy `--strict` (warnings fail), import-linter (generated), deptry, vulture on
`src` only (no test-only production code), typos, `buf lint`/`buf breaking`, `ledger-devtools check`, pytest
per package with 100 % branch, pip-audit; zero `type: ignore`/`noqa`/`pragma: no cover`/tool directives (the
`suppressions` step).

### 14.4 CI

`plan` job (`ledger-devtools affected` → matrix) → one job per affected package (lint, types, tests with its
coverage) → `gate` job (repo-wide steps, image build, compose smoke). `main` and nightly run everything.

### 14.5 The synthetic country `zz`

`countries/zz` is a tiny regime (two tax categories, one dated rate change, a checksum scheme) and a JSON channel
with a fake authority. It proves the seams (registries, routing, enrollment, conformance suites, tier checks)
without DGII, keeps modules honest about country-neutrality, and is excluded from images and the countries API
(`status = "incubating"`, `deploy = false`).

## 15. Stack

As v1 §12, minus NATS/nats-py, Temporal/temporalio, Envoy Gateway, Tilt; versions are floors verified on
CPython 3.15 in M1-M4 work.

| Concern | Choice |
|---|---|
| Language, packaging | Python 3.15, uv workspace (members by glob), one lock |
| HTTP | FastAPI + Pydantic v2, Granian, uvloop, orjson |
| Database | PostgreSQL 18, SQLAlchemy 2.1 Core (typed tables, no ORM sessions), psycopg 3 async, Alembic |
| Events, jobs | PostgreSQL (outbox/inbox/jobs, `SKIP LOCKED`, `LISTEN/NOTIFY`) |
| gRPC (signer only) | grpcio aio, grpcio-tools, mypy-protobuf, buf |
| XML, crypto | lxml, xmlschema, signxml (second verifier only), cryptography |
| Identity | Zitadel v4, joserfc |
| Cloud | boto3 (KMS, S3 with object lock) |
| Observability | OpenTelemetry SDK, prometheus-client, structlog |
| HTTP client | httpx |
| Tooling | ruff, basedpyright, mypy, import-linter, deptry, vulture, typos, buf, pytest (+asyncio, xdist, randomly, cov), hypothesis, testcontainers, copier, poethepoet, k6 |

---

## 16. Migration from v1

### 16.1 Keep / move / split / delete / rewrite

Verdicts: **K** keep · **Mv** move · **S** split · **M** merge · **D** delete · **R** rewrite. Audit section in
brackets.

**Root, CI, deploy, docs**

| v1 item | Verdict | v2 target / reason |
|---|---|---|
| `pyproject.toml` | R | members by glob; per-package lists generated or removed; one `gate` task [05 §7.1, D32] |
| `tests/test_workspace.py` | D | lists are generated and checked by `ledger-devtools check` [05 §7.1] |
| `conftest.py` (root, Windows selector loop) | Mv | `ledger_testing` pytest plugin |
| `.github/workflows/ci.yml` | R | plan → matrix → gate; buf image from one variable [05 D31] |
| `services/identity/Dockerfile` | D | `deploy/docker/ledger.Dockerfile` [05 D33] |
| `Tiltfile` | D | `docker compose watch` |
| `deploy/local/compose.yaml` | R | one image, five apps, LocalStack, fake DGII; NATS removed |
| `deploy/local/postgres/init.sql` | R | generated roles/schemas (§6.2); `ledger_app`/`ledger` DB narrative removed [05 §3.3] |
| `deploy/local/identity.env` | M | one `deploy/local/ledger.env` |
| `deploy/local/zitadel/*` | K | |
| `_typos.toml`, `.gitignore`, `.dockerignore`, `.gitattributes` | K\* | mark generated files `linguist-generated`; DGII XSDs `-text` |
| `README.md` | R | ~80 lines; chassis manual, tables and backlog removed [05 §7.2, D36-D40] |
| `docs/superpowers/specs/2026-10-11-…` | K | history; superseded by this document |
| `docs/superpowers/plans/m1, m2` | D (tree) | executed; in git history [05 D41] |
| `docs/superpowers/plans/m3, m4, m5` | D (tree) | superseded; their *decisions* are carried into §3.7, §8.6, §9.9 of this spec and cited |
| `docs/runbooks/identity-orphaned-company.md` | Mv | `docs/runbooks/identity/orphaned-company.md` |

**`libs/kernel` → `packages/kernel`**

| v1 module | Verdict | v2 |
|---|---|---|
| `errors.py` | K\* | + `InvalidValueError` replacing `InvalidPrincipalError`/identity `InvalidStateError` [05 D4] |
| `ids.py` | K\* | + TypeID codec; `Tenant` moves to `tenancy.py` with `mode`; `Scope` class [05 D14] |
| `clock.py`, `pagination.py` | K | |
| `access.py` | R | `Permission` becomes a validated value type; the enum and its future-milestone members deleted [05 §7.3] |
| `money.py` | R | `Currency` from the ISO 4217 table; `Money` kept; `_require_type` shared [05 D8] |
| `countries/do.py` | Mv + R | `countries/do/src/ledger_country_do/regime/tax_ids.py` behind `TaxIdSchemeDef` |
| `countries/__init__.py` | D | |
| — | new | `tenancy`, `jurisdiction`, `tax_ids`, `dated`, `regimes` |

**`libs/contracts` → `contracts/` + owners**

| v1 item | Verdict | v2 |
|---|---|---|
| `envelope.py` (CloudEvents over NATS headers) | R | `platform.events` envelope as columns (§3.5); header codec deleted |
| `money.proto` + generated | D | unused [05 §3.1] |
| `events.proto` (identity) | D | events are Python dataclasses + generated JSON Schemas |
| `identity_query.proto` | D | in-process `IdentityApi` |
| empty `*_pb2_grpc.py(i)` | D | stubs generated only for protos with services |
| `buf.yaml` | Mv | `contracts/buf.yaml` |
| tests | D | envelope tests rewritten in platform; proto round trips are `buf breaking`'s job [05 §3.3] |
| — | new | `contracts/proto/ledger/signer/v1/signer.proto`, generated into `ledger_signer.adapters.grpc._generated` |

**`libs/platform` → `packages/platform`** (target subpackage per §5.2)

| v1 module | Verdict | v2 |
|---|---|---|
| `service.py` (706) | S | `runtime/{app,spec,start}`; NATS policy deleted [05 §4.3] |
| `lifecycle.py` | M | `AsyncExitStack` in `runtime` |
| `supervision.py` | K | `runtime/supervision.py` |
| `settings.py` | K\* | `config/settings.py`; token fields → `auth.providers` settings; `nats_url`, `grpc_port` (except signer) removed |
| `options.py` | K\* | one `InvalidOptionError` [05 D7] |
| `logs.py`, `tracing.py`, `health.py`, `probe.py` | K | `observability/` |
| `database.py` (531) | S | `data/{database,unit_of_work,rls,tables}`; RLS builders collapsed [05 D13]; `SET LOCAL ROLE` added |
| `migrations.py` | K\* | `data/migrations.py`; `rows_by_scope`/`run_per_scope` D until a data migration needs them; missing version table handled (M4 decision 18) |
| `schema.py` | M | `data/chassis_schema.py` with `chassis_schema_v1` [05 D20] |
| `outbox.py` (570) | S | `events/{outbox,dispatcher}` + `data/queue`; JetStream publish deleted |
| `consumer.py` (563) | S | inbox + runner kept in `events/`; JetStream consumer management deleted |
| `streams.py`, `jetstream.py`, `interruptions.py` | D | R2 |
| `idempotency.py` | Mv | `http/idempotency.py`, retention 7 days |
| `authentication.py` (604) | S | `auth/{keys,tokens}` + `auth/providers/zitadel` [05 §4.1] |
| `authorization.py` | S | `auth/access.py`; `CachedPermissions` D |
| `identity_client.py` | D | in-process `IdentityApi` [05 §3.1] |
| `grpc_server.py`, `grpc_client.py` | K | `grpc/`; shared status table [05 D5, D6] |
| `web.py` (550) | S | `http/{app,problems,metrics,probes}`; owns `Problem`, `Page[T]` [05 D22-D23] |
| `routes.py` | S | `http/deps/*`; `company_in_header` kept as the `Ledger-Company` dependency; + `precondition_checked`, bound-unit dependency [05 D25] |
| `request_fields.py` | M | `http/` |
| `__init__.py` | R | curated facades per subpackage |

**`libs/testing` → `packages/testing`**

| v1 module | Verdict | v2 |
|---|---|---|
| package | R | pytest plugin (`pytest11`) owning shared fixtures [05 D26-D27] |
| `containers.py` | K\* | PostgreSQL + LocalStack; NATS removed; image pins from `deploy/local/images.toml` [05 D30] |
| `issuer.py` | K\* | session-scoped keys; claim names from the zitadel profile [05 D18] |
| `api.py` | K\* | `Caller` → `TestPrincipal`; `Page` from platform [05 D9] |
| `waiting.py`, `clock.py`, `logs.py`, `migrations.py` | K | absorb helper copies [05 D28]; `logs.use_the_plain_baseline` private |
| `caches.py` | D | the permission cache is gone |

**`services/identity` → `modules/identity`**

| v1 item | Verdict | v2 |
|---|---|---|
| `domain/company.py` | R | jurisdiction, functional currency, `tax_ids: tuple[TaxId, ...]`, `fiscal_profile_locked_at` |
| `domain/branches.py` | R | `establishments.py` (neutral); emission points D [05 §4.2] |
| `domain/errors.py` | K\* | `ActorNotAllowedError` → platform `PermissionDeniedError`; `CompanyNotFoundError` unified; `InvalidStateError` → kernel `InvalidValueError` [05 D1-D4] |
| `domain/organization.py` | K\* | one organization-admin check (the use case), the route guard removed [05 D3] |
| `domain/roles.py` | R | built-in role names + generated `ROLE_GRANTS` from manifests |
| `domain/membership.py` | K\* | `Actor.require` reuses platform access [05 D1] |
| `domain/text.py` | K\* | legal name limit generic (300); e-CF's 150 moves to the DO regime |
| `domain/events.py`, `domain/ids.py` | K\* | events move to public `events.py`; `EmissionPointId` D |
| `application/*` | K | adapted to the new types; facade added |
| `adapters/postgres/tables.py` | R | jurisdiction, currency, `tax_registrations` table, `mode` where moded, `api_clients` |
| `adapters/postgres/repositories.py` | S | one module per aggregate [05 §4.3] |
| `adapters/postgres/unit.py` | D | platform `UnitOfWork` with scope |
| `adapters/postgres/migrations/r0001, r0002` | R | squashed into one `r0001` calling `chassis_schema_v1` |
| `adapters/postgres/migrate.py`, `env.py` | M | `ledger-migrate` runs every schema |
| `adapters/events.py` (protobuf mapping, stream) | D | events are dataclasses [05 D21] |
| `adapters/permissions.py` | M | facade `permissions()` |
| `api/context.py` | S | OpenAPI helpers and `precondition_checked` → platform `http`; remainder D [05 D24] |
| `api/models.py` | K\* | `Problem`/`*Page` from platform; tax ids as `{scheme, value}` |
| `api/companies.py`, `memberships.py`, `organization.py` | K\* | bound-unit dependency; TypeIDs; mode |
| `api/branches.py`, `api/emission_points.py` | R / D | establishments router; emission points removed |
| `api/rest.py` | M | `assembly.py` |
| `api/rpc.py` | D | no gRPC for identity |
| `main.py` | R | `assembly.py` |
| `openapi.py` | M | `ledger-devtools sync` writes snapshots |
| `settings.py` | D | adds nothing [05 §7.7] |
| `openapi.json` | R | regenerated snapshot |
| tests | K\* | move to `tests/{unit,integration,contract}`; fixtures from the plugin; service session-scoped [05 §6] |

### 16.2 What survives in numbers

Of v1's ~11.6 k source lines, the chassis capabilities (config, logging redaction, tracing, health,
supervision, problems, auth, access, idempotency, ETags, pagination, RLS, migrations, outbox, inbox) and
identity's domain and application are moved and edited, not rewritten (R5). Deleted: NATS machinery (~1,000
source lines + ~2,000 test lines), the identity client and cache path (~400 + ~800), the protobuf event and
query contracts, dead exports (§5.2 of [05]), 2.6 MB of plans from the tree.

### 16.3 Order of work (v2 milestones)

v1's plans `milestone-3-vault`, `milestone-4-fiscal-do` and `milestone-5-invoicing` are **superseded**; each v2
milestone gets a new plan. Each milestone ends with the full gate green and the owner's review.

| # | Milestone | Done when | v1 source carried |
|---|---|---|---|
| v2-M1 | **Workspace and chassis** | new tree; `ledger-devtools sync/check/affected/scaffold/gate`; kernel v2; platform subpackages with curated surfaces; Postgres events (outbox → fan-out → inbox) and jobs with schedules; `SET LOCAL ROLE` units; `api`/`worker`/`migrate` apps; testing plugin, xdist; one image; compose without NATS; `countries/zz` skeleton; ADRs accepted | M1 chassis |
| v2-M2 | **Identity v2** | identity moved; jurisdiction, functional currency, qualified tax ids + global registry; modes; TypeIDs; dated-version machinery (zero changes); permissions from manifests; establishments; API clients; Zitadel claims profile; `GET /v1/countries` from regimes (`do` regime data lands here) | M2 |
| v2-M3 | **Signer** | `modules/signer` + `signer` app; gRPC contract; KMS envelope (LocalStack); import/rotate/describe/sign XML/sign seed; audit; expiry schedule; network and role isolation in compose | v1 plan M3 decisions 1-16 |
| v2-M4 | **fiscal-kit + `do-ecf-v1` emission** | fiscal-kit (channels, drafts, status, numbering, submission, ports, xml, conformance); DGII XSDs vendored and pinned; builders for 10 types + RFCE; ranges; e-CF state machine on jobs; DGII client with breaker; fake DGII; archive; ChannelConformance green (Bronze) | v1 plan M4 decisions 1-22 (Temporal replaced by jobs) |
| v2-M5 | **Invoicing, fiscal routing, webhooks** | public invoice API with `external_reference`, `IssuePolicy`, totals via `TaxCalculator`; fiscal enrollments and requirements; routing; `invoice.fiscal` sub-object; Standard Webhooks thin events; every type emitted end to end against the fake DGII; then against `TesteCF` (Silver) | v1 plan M5 decisions 1-21 |
| v2-M6 | **Reception and voids** | `receiver` app; seed/token for senders; reception with signature checks; ARECF; received documents API; ACECF; ANECF range voids | v1 M6 scope |
| v2-M7 | **Certification and hardening** | DGII certification as emitter and PSFE on `CerteCF` (Gold); load test 20×; security review; runbooks; DQ checks | v1 M7 |
| v2-M8 | **Production: DR e-CF go-live** | Helm, Argo CD, managed services, ingress, mesh/mTLS, monitoring and alerts, first paying clients in live mode | v1 M8 |
| after | parties, catalog, events API, ISV grants, entitlements, second country, accounting | — | — |

The critical path to the 15 November market date is M1 → M2 → M3 → M4 → M5; M3 and the regime data of M4 can
proceed in parallel with M2 once M1's chassis surfaces are fixed.

---

## 17. Architecture decision records

Written as `docs/adr/NNNN-*.md` (MADR, short), status *Proposed* until the owner accepts this spec.

| ADR | Decision |
|---|---|
| [0001](../../adr/0001-modular-monolith.md) | One codebase as a modular monolith with five deployables; modules talk in process; extraction only for runtime reasons |
| [0002](../../adr/0002-postgres-eventing.md) | Events through outbox → fan-out → inbox in PostgreSQL with `SKIP LOCKED` and `LISTEN/NOTIFY`; NATS removed; `EventBus` port kept |
| [0003](../../adr/0003-no-temporal-yet.md) | Durable processes as persisted state machines driven by a PostgreSQL job queue; Temporal not adopted, with explicit revisit triggers |
| [0004](../../adr/0004-schema-per-module.md) | One cluster, one schema and one DML role per module, `SET LOCAL ROLE` per unit of work, RLS on tenant and mode, `organization_id` as shard key |
| [0005](../../adr/0005-regime-channel-split.md) | Countries as packs of pure regime data and versioned channels, registered by entry points; fiscal-kit generalizes plumbing, not state machines |
| [0006](../../adr/0006-qualified-tax-ids.md) | Tax identifiers are `(scheme, value)` validated by regimes, check digits reported unless the scheme enforces them, one global registry of active taxpayers |
| [0007](../../adr/0007-test-and-live-modes.md) | Test vs live mode is a tenancy dimension on every moded row, credential and RLS policy |
| [0008](../../adr/0008-dated-api-versions.md) | `/v1` path major plus pinned dated versions with backward response transforms; TypeID public ids; thin webhooks |
| [0009](../../adr/0009-manifest-driven-workspace.md) | A uv workspace with one package per unit, `manifest.toml` as the source of truth, generated lists and contracts, devtools sync/check/affected/scaffold |
| [0010](../../adr/0010-signer-isolation.md) | Private keys only in a separate signer process with its own DB role, KMS credentials and network policy, reached over gRPC |

## 18. Risks

| Risk | Mitigation |
|---|---|
| The market deadline (2026-11-15) is 33 days away and v2 restarts the layout | Most code moves rather than being rewritten (§16.2); critical path ordered (§16.3); parties/catalog/events API deferred; Q1 decides the launch posture |
| PostgreSQL as queue becomes a bottleneck | Measured gauges; queues per schema spread load; fan-out batches; vacuum tuning for queue tables (fillfactor, autovacuum thresholds); the `EventBus` port allows a broker; revisit trigger in ADR 0003 |
| Hand-written state machines grow Temporal-like complexity | Plumbing in fiscal-kit (`advance`, `PollSchedule`, breaker); revisit triggers in ADR 0003 |
| A shared runtime login with `SET LOCAL ROLE` could be misused by a bug | Roles set only by the platform's unit of work; import-linter keeps SQL inside adapters; per-schema grants still refuse cross-module access; extraction gives a module its own login |
| DGII specs change (XSDs, endpoints, codes) | Vendored and pinned with SHA-256; corrections pinned by count; nightly `TesteCF` run; status-map exhaustiveness |
| PSFE certification requirements (three certified client emitters, FI-GDF-017, Reglamento 587-24) | Tracked in M7; pilot clients sought during M4-M6 |
| Code drifts into country packs instead of being generalized ([02] Kraken) | Review rule: logic needed by two packs moves into fiscal-kit or the kernel behind a port; `countries/zz` keeps the seams honest |
| AGPL-3.0 (Zitadel) | Unmodified, separate service, no linking |

## 19. Open questions for the owner

1. **Launch posture vs the deadline.** Offer the API to pilot clients in test mode (`CerteCF`) before our PSFE
   certification completes, or only after? This decides whether M6 (reception) can slip past first clients.
2. **DGII test enrollment.** Which taxpayer (ours or a pilot's) enrolls as an electronic issuer so we get
   `TesteCF` access and a test certificate? M5's Silver tier and the nightly run depend on it.
3. **Human callers' mode.** v2 requires `Ledger-Mode` on moded routes for human tokens. Accept, or default
   humans to `test`?
4. **Capacity assumption.** Confirm the planning peak (assumed 20 documents/s at month-end at launch, tested at
   400/s) or give the expected client mix.
5. **Built-in roles.** Keep v1's five roles (`owner`, `admin`, `accountant`, `issuer`, `viewer`) or merge
   `accountant` into `issuer` (they differ only by reading memberships)?
6. **Items to verify against DGII's current documents before M4 closes** (not owner decisions, but owner-visible
   risks): semantics of *Aceptado Condicional*; whether RFCE answers carry a TrackId; the contingency window (72 h
   in the technical report vs 15 days in DGII's contingency guide); whether the receiver URL registered with DGII may
   carry a per-company path segment (`/do-ecf-v1/{rnc}/fe/...`) or must be per-company host names.
7. **Cloud and region.** Choose after the Reglamento 587-24 data-location check; v2 assumes only Kubernetes,
   PostgreSQL, S3-compatible object lock and KMS.
8. **Plans in the tree.** Delete v1 plans m1-m5 from the tracked tree in v2-M1 (history keeps them), as the audit
   recommends?
