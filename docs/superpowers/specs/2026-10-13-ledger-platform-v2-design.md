# Ledger platform v2: a modular monolith for many countries and many features

| | |
|---|---|
| Status | Draft, revision 2 (review verdict "approve with changes" applied); for owner review |
| Date | 2026-10-13 |
| Supersedes | [`2026-10-11-ledger-platform-design.md`](2026-10-11-ledger-platform-design.md) (v1). v1 stays in the tree as history; where the two disagree, this document wins. The milestone plans 3, 4 and 5 written against v1 are superseded (§16.3) |
| Binding inputs | `.superpowers/research/00-controller-rulings.md` (R1-R6); evidence in `01-localization`, `02-modularity`, `03-layout-tooling`, `04-saas-platforms`, `05-audit`, `06-dgii-facts` (cited as **[01]**...**[06]**, rulings as **R1**...**R6**) |
| Decisions recorded as | `docs/adr/0001`...`0010` (§17) |
| As built (v2-M1) | Reconciled with the code at the end of v2-M1: the manifest model is `ledger_platform.config.manifest`; the root's package lists are a block `sync` generates; settings sections per package (§3.10); the pool sized by settings and checked (§6.7); `serve(role)`; four RLS scopes and the purge role (§6.3); the route guards (§5.2); devtools `gate` deferred (§4.7) |

> **Supersedes v1.** v1 chose seven microservices on NATS JetStream and Temporal with one database per service.
> v2 keeps v1's product, its DGII requirements, its security, observability and SLO rules, and the chassis
> capabilities proven in milestones 1 and 2, and changes the shape: one codebase, a uv workspace of small
> packages driven by manifests, modules talking in process, events and jobs on PostgreSQL, countries as
> regime and channel packs, a separate signer process. Nothing is in production, so the change is free now and
> expensive later.

**Built now vs decided now.** The deadline (§1.5) forces a short critical path (§16.3). Some things are only
*decided* now, because changing them later is expensive, and built minimally: test/live mode on every row,
TypeID public ids and the resource envelope, qualified tax ids and the global registry, `organization_id` as
shard key, schema and role per module, the `Ledger-Version` pin, country fields under `fiscal.<regime>`,
business-key idempotency, effective-dated regime data with resolved values stored on documents, regime/channel
split. Everything on the cut list (§16.4) is designed here and built after go-live or when a second
implementation needs it (rule of two).

---

## 1. Purpose, scope and goals

### 1.1 Product

A business platform in the family of Odoo and Alegra, built to be sold, starting where demand is forced by
law: Dominican Republic electronic invoicing (e-CF). Every taxpayer must emit e-CF; small, micro and
unclassified taxpayers (and state entities) by **15 November 2026** (Aviso 06-26's six-month extension of
15 May 2026, [06] §5). About 64 % of current emitters use DGII's free "facturador", capped at 150 invoices a
month, so a large share of the market will outgrow it.

The first product is an **API**: software vendors, ERPs, POS systems and online stores send a commercial
invoice as JSON; the platform turns it into a valid, signed e-CF, numbers and signs it at once so the client can
print it, sends it to DGII, follows it to a final state, delivers it to the buyer when the buyer is an
electronic receiver, renders the printed representation, receives other emitters' e-CF on the company's behalf,
and notifies the client. We are our own DGII integration (we certify as a PSFE, *Proveedor de Servicios de
Facturación Electrónica*), not a reseller.

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
   32 including RFCE, 33, 34, 41, 43, 44, 45, 46, 47) in DGII's `TesteCF` through the public API, in **test
   mode**; it receives the e-NCF, security code and QR data in the `POST` response.
2. We certify our own RNC as an electronic emitter (DGII's 14-step process), then as a PSFE (set of tests,
   sworn statement, authorization), then onboard clients through DGII's simplified "with a certified PSFE" flow
   ([06] §6).
3. No accepted invoice is ever lost, duplicated or given two e-NCF; no e-NCF is ever skipped silently (every
   number is used by a document, burned by a rejection, released for reuse by DGII's `secuenciaUtilizada =
   false`, or formally voided).
4. A second country is added as `countries/<cc>/` plus manifest data: no module, no app and no other country
   changes (review rule on the diff; the automated `affected` check comes later, §16.4).
5. The quality gate holds in every package: ruff ALL, basedpyright `all` + mypy `--strict`, zero suppressions,
   100 % branch coverage per package, TDD, reviews (R6).

### 1.4 Non-goals of the first product

- An end-user web UI (API, its documentation, the printed representation and a minimal operator CLI only).
- Accounting, inventory, payroll, POS (accounting's core rules are designed in §7.9, not built).
- Countries other than the Dominican Republic (their seams are designed; generalizing country plumbing waits
  for the second channel, §8.4).
- Billing our own customers automatically (manual invoicing until volume justifies it).
- Everything on the cut list (§16.4).

### 1.5 Deadline and the certification path

15 November 2026 is the market's legal deadline ([06] §5, confirmed), not a date we control. DGII's PSFE
process requires that we first be a certified electronic emitter ourselves; DGII answers a PSFE request within
10 business days and the rest depends on how fast we pass its test sets; every new e-CF format version requires
re-certification ([06] §6). The order of work (§16.3) therefore starts with a week-one DGII spike and puts
emission, reception, buyer delivery and the printed representation (all part of DGII's certification set) on the
critical path; everything else is cut (§16.4). Whether pilot clients may start on `CerteCF` before our PSFE
authorization is an owner decision (§19, Q1).

**Legal constraint on signing ([06] §6, Decreto 587-24 Art. 22).** A SaaS PSFE that holds clients' signing
certificates must either sign through the infrastructure of an INDOTEL-authorized certification entity (with a
contract) or hold its own INDOTEL authorization for managing signature-creation data on the signer's behalf. The
signer therefore supports two backends behind one port (§3.7); which one we operate is an owner and counsel
decision (§19, Q5).

---

## 2. Rules that hold everywhere

These replace v1's ten rules. Each is enforced by a tool where a tool can enforce it.

| # | Rule | Enforced by |
|---|---|---|
| 1 | **A module owns its data.** One PostgreSQL schema and one DML role per module; no cross-schema foreign key, join or query. Data crosses modules through the owner's `api` (commands and queries) or its `events`. | DB grants (§6.2); import-linter (§4.6) |
| 2 | **Commands are in-process calls through a Protocol, facts are events.** `ledger_<m>.api` holds typed Protocols and DTOs; `ledger_<m>.events` holds frozen event types. Nothing else of a module is importable by another. **Consuming an event is a dependency edge exactly like an import**: the module graph, imports and subscriptions together must be acyclic. A process that coordinates several modules is a **flow** (`flows/<name>`), a leaf above modules that no module depends on (ADR 0011). | import-linter `protected`; `ledger-devtools check` on the manifest graph |
| 3 | **Every state change and the event announcing it commit together** (outbox in the producer's schema). | `UnitOfWork.publish` is the only way to emit |
| 4 | **Every consumer and every job is idempotent** (inbox keyed by consumer and event id; jobs keyed by kind and key); facade commands callable from consumers or jobs take an idempotency token or a version. | chassis tables' primary keys; facade signatures |
| 5 | **Every external write is idempotent**: `Idempotency-Key` (7 days, scoped per route) on the public API and a permanent business key (`external_reference`). | http dependencies; unique indexes |
| 6 | **Country rules live in country packs.** Modules never import a country; countries never import a module or each other; a regime imports only the kernel. Modules reach countries through registries (entry points). | import-linter `forbidden`/`independence` |
| 7 | **Private keys never leave the signer** (local backend) or never reach us (remote certification-entity backend). Other processes ask the signer to sign over gRPC; no API exports a key. | separate entrypoint, DB role, network policy, per-caller method allowlists |
| 8 | **Tenancy is checked twice**: in the application (principal, company, mode) and by PostgreSQL row-level security. | RLS policies from one builder; deny-by-default test per table |
| 9 | **Contracts are versioned and checked for breaking changes in CI**: protobuf (`buf breaking`) for the signer, JSON Schema snapshots for events, OpenAPI snapshots for the public API. | `ledger-devtools check`, `buf breaking` |
| 10 | **Everything is observable**: one trace from the client's call through events, jobs, signing and DGII calls. | chassis instrumentation; trace context stored with outbox rows and jobs |
| 11 | **Every list is generated** from manifests: workspace dependencies, entry points, import-linter contracts, coverage targets, database roles, subscriptions, role grants. | `ledger-devtools check` fails on a stale file |
| 12 | **Legal rules are effective-dated data**, selected by the document's tax point in the establishment's time zone (falling back to the regime's), never by `now()`; resolved values are stored on the document. | regime tests (§8.7) |
| 13 | **No facade call inside an open unit of work.** A module never holds a transaction while calling another module. | runtime guard: a facade call while a unit is open on the task raises `UnitOpenError` |

---

## 3. Architecture

### 3.1 Shape

A **modular monolith**: one repository, one Python environment, one OCI image, five entrypoints (*apps*).
Modules are bounded contexts with a public surface; countries are packs registered against extension points;
apps compose them (R1, ADR 0001).

```
                     clients (ERP, POS, stores, ISVs)                  DGII · other emitters · buyers' receivers
                                │ HTTPS                                          │ HTTPS (e-CF, ACECF, seeds)
                                ▼                                                ▼
   Zitadel ◄─── JWKS ──┐ ┌────────────────────────────────────┐        ┌────────────────────────────┐
   (identity provider) └─┤ app: api  (N replicas)             │        │ app: receiver (N replicas) │
                         │  modules' HTTP adapters: identity, │        │  channel receiver routes:  │
                         │  signer (http face), invoicing,    │        │  /do-ecf-v1/{rnc}/fe/...   │
                         │  fiscal, webhooks; channels' public│        │  seed, token, reception,   │
                         │  routes /v1/do/...; countries API  │        │  commercial approval       │
                         │  sync path: invoice → number → sign│        └──────────────┬─────────────┘
                         └──────────────┬─────────────────────┘                       │
                                        │ in-process api calls; one UnitOfWork per module at a time
                                        ▼                                             ▼
   ┌──────────────────────────────── PostgreSQL 18 (one cluster) ────────────────────────────────────┐
   │ schemas: platform · identity · signer · invoicing · fiscal · webhooks · do_ecf                   │
   │ each module schema: its tables (RLS) + outbox · inbox · jobs · subscriptions · idempotency_keys  │
   │                     · audit_events   (chassis_schema_v1)                                          │
   └───────────────▲───────────────────────────────▲──────────────────────────────▲──────────────────┘
                   │ LISTEN/NOTIFY + SKIP LOCKED   │                              │ signer schema only
   ┌───────────────┴──────────────────────┐        │                  ┌───────────┴────────────────┐
   │ app: worker (N replicas)             │        │ gRPC             │ app: signer (2 replicas)   │
   │  outbox fan-out → inboxes (all       │        │ SignXml/SignSeed │  certificates, backends:   │
   │  schemas, signer's included)         │────────┼─────────────────►│  local key (KMS envelope)  │
   │  consumers, jobs, schedules (all     │        │                  │  or remote cert. entity    │
   │  modules but signer)                 │────────┘                  │  own consumers + schedules │
   │  DGII submission, buyer delivery,    │                           └───────────┬────────────────┘
   │  webhooks                            │                                       ▼
   └───────────────┬──────────────────────┘                          cloud KMS / INDOTEL-authorized entity
                   │ HTTPS
                   ▼
          DGII (TesteCF / CerteCF / eCF), buyers' receivers, client webhook endpoints, object storage (WORM)

   app: migrate — one-shot job before each rollout: every schema's migrations in dependency order, then the
                  subscriptions and the routing version (§3.5)
```

### 3.2 Modules (bounded contexts)

| Module | Owns (schema) | Public API (`api.py` Protocol) | Publishes | Consumes | Built in |
|---|---|---|---|---|---|
| `identity` | organizations, companies (jurisdiction, functional currency, qualified tax ids), the global tax-id registry, establishments (minimal: code, name, time zone), memberships, API clients (mode-bound, pinned API version) | `IdentityApi`: company by id, company by tax id, an establishment of a company by code (`MAIN` by default; code, name, time zone), permissions of a principal in a company, lock fiscal profile | `identity.company.registered/updated/deactivated`, `identity.membership.granted/revoked` | — | v2-M2 |
| `signer` | certificates (local: sealed keys; remote: entity credential references), signing audit | `SignerApi`: describe certificates, sign XML, sign authority seed (a gRPC client in every app but `signer`) | `signer.certificate.installed/retired/expiring` | `identity.company.deactivated` (runner in the signer app) | v2-M2 |
| `invoicing` | commercial documents (invoice, credit note, debit note; sales or self-billed purchase), lines, totals, `external_reference`, the per-channel fiscal status mirror | `InvoicingApi`: get document, `apply_fiscal_status`; extension point `IssuePolicy` (implemented by `fiscal`) | `invoicing.invoice.issued/voided/fiscal_updated` | — (nothing: its fiscal mirror is written by `fiscal` through `apply_fiscal_status`) | v2-M3 (thin) |
| `fiscal` | enrollments (company × mode × channel, environment, requirements), routings (invoice → channel documents, 1:N keyed by channel) | `FiscalApi`: enrollments of a company; implements `invoicing.IssuePolicy`; provides `CompanyDirectory` to channels | `fiscal.enrollment.changed` | `invoicing.invoice.issued` (recovery path, §8.5), `fiscal_channel.document.*`, `signer.certificate.installed` | v2-M3 (thin) |
| `webhooks` | endpoints, sealed secrets, deliveries | — | — | generic envelopes of every event that has a public name (§9.9) | v2-M3 (minimal) |
| `parties`, `catalog` | parties with roles; products, units, tax-category selections | `PartiesApi`, `CatalogApi` | `parties.party.*`, `catalog.item.*` | — | after go-live |
| `accounting` | journal, accounts, periods (§7.9) | — | — | invoicing and fiscal events | later |

`platform` also owns a schema (`platform`: routing version, schedule leases) and declares its own manifest
(`packages/platform/manifest.toml`: schema, role `platform_rw`, the `evt` id prefix, the jobs and hourly and
daily schedules `platform.purge_idempotency_keys` and `platform.purge_done_jobs`, one at a time each).

Channels (`countries/<cc>/…/channels/<channel>`) also own a schema (`do_ecf`) and run inside the `api`,
`worker` and `receiver` apps. They are not modules: they depend on `fiscal-kit`, `platform` and `kernel` only,
and receive everything else through ports (§3.10).

Module dependency graph (imports and subscriptions; acyclic; webhooks is a sink nothing depends on):

```
 webhooks ──consumes──► public events of identity, invoicing, fiscal, signer, channels (generic envelopes)
 fiscal ─────► identity (api), invoicing (api: IssuePolicy, apply_fiscal_status; events: issued), signer (events)
 invoicing ──► identity (api)
 signer ─────► identity (events)
 identity ───► (none)
```

`invoicing` never depends on `fiscal`: it declares the `IssuePolicy` extension point and exposes
`apply_fiscal_status`; `fiscal` implements the one and calls the other ([02] Kraken dependency inversion).

### 3.3 Apps (deployables)

One image (`ledger`), five console scripts. An app is a composition root: `platform.runtime.App` loads the
manifests, the modules' assemblies and the registered regimes and channels, and mounts what the app's role
needs. Apps contain no business code (import-linter: an app imports only `ledger_platform.runtime` and
`ledger_<m>.assembly`).

| App | Console script | Mounts | Scaling, exposure |
|---|---|---|---|
| `api` | `ledger-api` | every module's routers, channels' public routes (`/v1/do/...`), the countries API, the synchronous numbering-and-signing path (§8.5) | stateless, N replicas, public behind ingress |
| `worker` | `ledger-worker` | outbox fan-out for every schema (the signer's included), consumers, jobs and schedules of every module and channel except the signer's | N replicas; work shared through `SKIP LOCKED`; pools by job kind later |
| `signer` | `ledger-signer` | the signer's gRPC server, its own consumer runner (`signer.company_deactivations`), its jobs (`signer.expiry_notice`, planned per certificate at import, instead of an expiry-watch schedule) and its own purges (`signer.purge_idempotency_keys`, `signer.purge_done_jobs`: no other role's login enters its schema) | 2 replicas, private network, own credentials (§3.7) |
| `receiver` | `ledger-receiver` | channels' authority- and emitter-facing routes | N replicas, public, own host name and rate limits (§3.8) |
| `migrate` | `ledger-migrate` | every schema's migrations in dependency order; subscriptions upsert; routing version bump | one-shot job before each rollout |

A single-process *dev mode* (`ledger-api --with-worker`) runs `api` and `worker` together for local
debugging; it is never deployed. The signer always runs as its own process, locally too.

### 3.4 In-process APIs

A module's `api.py` contains **only** Protocols, frozen DTOs and the module's public error types. Its
implementation lives in `application/facade.py`; the assembly binds it. Callers depend on the Protocol and
receive an instance from the composition root, so a test passes a fake and an extraction passes a gRPC client
(§3.9).

```python
# modules/invoicing/src/ledger_invoicing/api.py
class InvoicingApi(Protocol):
    async def document(self, call: Call | SystemCall, document: InvoiceId) -> InvoiceInfo: ...
    async def apply_fiscal_status(
        self, call: SystemCall, document: InvoiceId, status: FiscalStatusView, version: int
    ) -> Applied: ...   # idempotent: applies only when version > the stored version for status.channel

class IssuePolicy(Protocol):                               # extension point, implemented by fiscal
    async def check(self, call: Call, document: InvoiceInfo) -> Sequence[Objection]: ...      # before the unit
    async def after_issue(self, call: Call, document: InvoiceInfo, *, budget: timedelta) -> FiscalOutcome: ...
```

- `Call` (platform `runtime.calls`) carries the principal, the tenant scope (`organization`, `company`,
  `mode`), the request id, the resolved API version and the trace context. `SystemCall` is a call made by the
  platform itself (a consumer, a job, the receiver) and names its reason; it is never built from a request.
- A facade method opens **its own** unit of work in its module's role. Two modules never share a transaction
  (rule 1). **Runtime guard** (rule 13): the platform tracks the open unit on the current task (a
  `ContextVar`); a facade entry point decorated `@facade` raises `UnitOpenError` when called while one is
  open, so a cross-module call can never be made while holding locks.
- Facade **commands** reachable from consumers or jobs take an idempotency token (the event id or job key) or
  a monotonic version (`apply_fiscal_status`), so a redelivery is harmless.
- Facades take and return DTOs, never domain objects or rows; DTOs are `@dataclass(frozen=True, slots=True,
  kw_only=True)` with kernel value types. A DTO is named `*Info` (`CompanyInfo`, `InvoiceInfo`); `*View` names a module's
  HTTP response models, so no two classes of a module share a name.
- Errors crossing a facade are `LedgerError` subclasses with a kind and a stable code (§9.6), exported from
  the module's `api.py`.

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
organization-wide events), `mode` (nullable: `null` for an event about data both modes share), `subject`
(aggregate id), `aggregate_version`, `occurred_at`, `traceparent`, `payload` (JSON by the platform codec:
`Decimal` as string, ids as UUID strings, dates ISO 8601). Payload ≤ 64 KiB, refused at `publish`.

- **An event's scope lies inside its unit's** (`publish` refuses any other, `event_scope_mismatch`): the unit's
  organization; its company where the unit names one; its mode where both name one. `mode` is `null` for an
  event about data both modes share (a company, its memberships, §7.1), and a moded unit may publish such an
  event (an idempotent route acts in a moded unit, §9.5); an event of the other mode is refused. A unit without
  a scope (the platform's own) publishes for anyone.

**Subscriptions** (C3, ADR 0002). Manifests declare consumers and their event types. `ledger-migrate`, before a
rollout and after the schema migrations, upserts each consumer's rows into its own schema's `subscriptions`
table (`consumer, event_type, active`) under that module's owner role, deactivates rows no longer declared, and
bumps `platform.routing.version`. The fan-out reads every schema's `subscriptions` once, caches the routing
table, and reloads it when `platform.routing.version` changes (checked once per pass, one indexed read). The
running code's subscriptions are therefore in the database before the new version starts. After a rollback the
newer subscriptions stay; an older runner claims only the event types it has handlers for, so rows of a newer
type wait in the inbox for the next roll forward (alerted by age), never fail.

**Flow** (ADR 0002):

```
 producer transaction (module role)                     worker: fan-out (role ledger_relay)
 ─────────────────────────────────────                  ───────────────────────────────────────────────
 UPDATE invoicing.documents ...                          SELECT ... FROM <schema>.outbox WHERE unrouted_since IS NULL
 INSERT INTO invoicing.outbox (event)                      ORDER BY id LIMIT 100 FOR UPDATE SKIP LOCKED
 COMMIT ── after commit: one coalesced NOTIFY           for each row:
          ledger_outbox per process per 10 ms              consumers = routing[type]
                                                           if none: UPDATE outbox SET unrouted_since = now()  (kept,
                                                                    alerted; re-tried when the routing version changes)
                                                           else: INSERT INTO <consumer schema>.inbox ... ON CONFLICT
                                                                 DO NOTHING; DELETE the outbox row
                                                         COMMIT; after commit: coalesced NOTIFY ledger_inbox

 worker (or signer app for the signer's consumers): consumer runner (consumer's module role)
 ──────────────────────────────────────────────────────────────────────────────────────────────
 claim due inbox rows of <consumer> FOR UPDATE SKIP LOCKED; lease 60 s; attempts += 1 on claim
 per row, one transaction in the consumer's role and the event's tenant scope:
   handler(event, uow) ; DELETE the inbox row                        -- same transaction
 retryable failure: next_attempt_at = backoff(attempts) ; INVALID or attempts = max: status = 'dead' (kept)
```

- **A consumer handles its event inside a unit (as built, v2-M1).** The handler and the delete of its inbox row
  share one transaction, which is what makes the effect exactly once. A facade call inside it raises
  `UnitOpenError` (rule 13), so until an "outside" consumer style exists (the handler runs with no unit and the
  runner deletes the row in a second unit under its token, the facade command carrying the event id as its
  idempotency token), a consumer writes its own schema and enqueues a job for any cross-module call. The
  consumers §8.5 shows calling `InvoicingApi.apply_fiscal_status` (`fiscal.channel_documents`) need the outside
  style first. **As built (v2-M3b):** a consumer declared `style = "outside"` (never with `ordered = true`) hands
  `ConsumerSpec(outside=handler)`, a handler of the event alone that runs with no unit open (so it may call
  facades) as `system:consumer:<name>`, its lease extended under its token before it starts and renewed every
  third of it while it runs (a renewal that finds the row taken over or fails stops it), its row then deleted,
  or its failure recorded by the retry, `dead` and replay rules above, in another unit fenced by the token (a
  lost lease records nothing, `inbox_lease_lost`): its effects are at least once, so what it calls takes the
  event id as idempotency token or a version (rule 4), a version-monotonic facade answering a stale version as a
  no-op success (a `CONFLICT` that reaches the runner is retried, like any but `INVALID`). A run past the
  consumer's `timeout` (manifest, default 60 s) is stopped and retried as `inbox_handler_timed_out` (dead once
  its attempts are spent), so a hung call never holds the consumer, nor a stopping worker, longer than that.
- **Exactly-once effects over at-least-once delivery.** Fan-out inserts and the outbox delete commit together,
  so one outbox row is fanned out once; the inbox primary key `(consumer, event_id)` absorbs any repeat. A
  handled inbox row is deleted in the handler's transaction (nothing can deliver it again: its outbox row is
  gone); dead rows are kept for inspection and replay.
- **An event with no subscriber is never deleted**: it is marked `unrouted_since`, counted by
  `ledger_outbox_unrouted` and alerted; it is fanned out when a routing version adds a subscriber, or deleted by
  an operator.
- **A row that cannot be fanned out never holds back the others**: a failed batch is redone one row at a time
  (savepoints of the pass), the failing row's `attempts` is counted (`ledger_outbox_failed`, logged with the
  error's type only) only when the error is the row's own (unreadable, a constraint, a trigger's refusal), and
  at 10 it is set aside, kept and alerted until an operator resets `attempts`. Any other error (a revoked grant,
  a lost connection) blames no row: the row is skipped uncounted, a pass where every row failed so rolls back
  and is logged (`outbox.pass_failed`, counted per schema), the other schemas still go, and only when every
  schema failed does the fan-out raise for the worker's supervisor to back off.
- **Ordering.** None promised across aggregates. Default for projections: **version-monotonic upserts** (a
  handler writes only when the event's `aggregate_version` is above the stored one, so order does not matter).
  Opt-in per consumer (`ordered = true` in the manifest): **head-of-line claim per (consumer, subject)**: the
  runner claims only the first row of each subject that is not dead, in the order (`aggregate_version`, event
  id) (an event published without a version is stored with 0), so a subject's events run in their aggregate's
  order even when their ids are not, and a failing one blocks only its own subject. The heads are read once per
  claim from `inbox_head` (`DISTINCT ON (subject)`). Known limit: that costs O(the consumer's live rows) per
  claim; a skip scan (a recursive CTE from one subject's head to the next) is the fix when a backlog needs it. One dispatcher fans out a schema at a time (a
  transaction-level advisory lock per schema), so a subject's rows reach the inbox in id order. A dead row no
  longer blocks its subject (the rows behind it go on); replaying it is refused while an earlier live row of its
  subject waits. Events without a subject are not ordered.
  **As built (v2-M1), known limit:** subject order holds while the fan-out is healthy. When the row-by-row path
  skips a row (its target refused it) or sets one aside (10 failures), later events of the same subject go on to
  an ordered consumer's inbox ahead of it, and run first; the earlier one arrives late as the subject's new head.
  Before the first ordered consumer ships, the fan-out must hold a subject back behind a skipped or set-aside
  row of its own (skip later rows of that subject in the pass; let a set-aside row block its subject), or the
  consumer must be a version-monotonic upsert, which does not depend on order.
- **Retries**: backoff 1 s, 10 s, 1 min, 5 min, then every 10 min; `max_attempts` 10 (overridable per
  consumer); then `dead`. A row whose worker died (its lease ran out) is retried the same way, a backoff after
  the lease ran out, and is `dead` with `worker_lost` once its attempts are spent (one rule for inboxes and
  jobs). Operators replay with `ledger-worker replay --consumer X --event evt_…`.
- **Wake-ups**: one `LISTEN ledger_outbox, ledger_inbox` connection per worker process, opened **directly**
  to PostgreSQL (never through a transaction pooler, §6.7); NOTIFYs are coalesced per process (at most one per
  channel per 10 ms). The poll safety net is 1 s per worker for all schemas together (one `UNION ALL` probe of
  partial indexes). Scale path when idle polling matters: lengthen the poll to 5 s and rely on NOTIFY, or move
  to one `platform.wakeups` table.
- **The `EventBus` port** stays: `platform.events.EventBus` has the Postgres implementation now; an extracted
  module gets a broker adapter behind the same port (§3.9).
- **Signer events**: the signer publishes into `signer.outbox` and consumes from `signer.inbox`; the worker's
  fan-out (role `ledger_relay`) moves rows in and out of those two tables only; the signer's own runner handles
  its inbox. No other role touches the signer's business tables.

### 3.6 Jobs and schedules (durable work without Temporal)

Long-running processes (submitting an e-CF and following it, buyer delivery, webhook deliveries, expiry scans)
are **jobs** in the owning module's or channel's `jobs` table (DDL in §6.4), advanced by **explicit, persisted
state machines** (ADR 0003).

- **Enqueue** `uow.jobs.enqueue(kind, key, *, run_at, payload)` inside the transaction that decides the work.
  On an existing `(kind, key)`: ignored when the job is `ready`, `running` or `parked`; reset to `ready`
  (attempts 0, new payload) when `done` or `dead`.
- **Time** is the database's: a unit's `now` is its transaction's `now()`, and every due, lease and
  deadline comparison is made in SQL against `now()`, so workers on hosts with drifting clocks agree.
- **Keys** are unique per kind across tenants: a key embeds a globally unique id (a document's, an
  event's). A job belongs to the scope that enqueued it; another scope's enqueue never restarts it, and a
  unit wakes only jobs inside its own scope.
- **Claim**: `status = 'ready' AND run_at <= now()`, `FOR UPDATE SKIP LOCKED`, sets `status = 'running'`,
  `lease_until = now() + lease` (per kind, default 60 s), a fresh `lease_token` (fencing token), and
  `attempts += 1` (counted on claim, so a crash mid-run still counts).
- **Record**: every write of a handler's outcome checks `WHERE kind = … AND key = … AND lease_token = :token`;
  a lost lease (another worker reclaimed it) makes the late writer's record a no-op and it logs
  `job_lease_lost`. Business writes of a step re-read their row under lock and are idempotent, so a zombie step
  cannot do harm before it records.
- **A handler has a time of its own** (`timeout` in the manifest, default 15 min): a run past it is stopped
  and retried as a retryable failure (`job_timed_out`), so a hung call cannot hold its slot and renew its lease
  for ever.
- **Outcomes**: `Done`; `RunAgain(at)` (polling: does not count as a failure; attempts reset); `Park(reason)`
  (waiting for an outside fact; `uow.jobs.wake(kind, key)` or `uow.jobs.wake_all(kind, company, reason)` makes
  matching parked jobs `ready`); a retryable error → `ready` at the kind's backoff; an `INVALID`/`CONFLICT`
  error, `attempts = max_attempts` or `deadline_at` passed → `dead` (alerted).
- **No transaction spans a network call**: a handler reads in one unit, calls out (DGII, signer, buyers,
  object storage) outside it, and records in another that re-reads the row under its lock (kept from v1/M4).
- **Concurrency**: per kind per worker process (`concurrency` in the manifest, default 8). Per-company fairness
  is deferred (§16.4).
- **Jobs are outside RLS**: the claim reads across tenants; a job row holds ids, states, dates and a small
  payload only; the handler opens a scoped unit (`Tenant` from the row) for business data.
- **Schedules** (manifest `every = "1h"` or `cron`) run under a **lease row** in `platform.schedule_leases`
  (`name, holder, leased_until, next_run_at`, claimed with `FOR UPDATE SKIP LOCKED`), which works through a
  transaction pooler (§6.7); a schedule only enqueues jobs.
- **Revisit Temporal when** any of: a process needs more than five cross-module steps with compensation; more
  than three hand-written state machines duplicate timer/retry logic; or job volume needs more than one
  PostgreSQL primary for queues alone (ADR 0003).

### 3.7 Signer isolation and signing backends

Rule 7, ADR 0010.

- **Process**: `ledger-signer`, its own Deployment, its own PostgreSQL login (`ledger_signer`, member of
  `signer_rw`, `signer_purge` and, for its schedule leases, `platform_rw`: §6.2), its own KMS and certification-entity credentials. Network policy: only `api`, `worker` and
  `receiver` pods reach its gRPC port; it reaches only PostgreSQL, KMS and the certification entity.
- **Backends** (`SigningBackend` port inside the signer; one per certificate, chosen at import):
  - `local`: the `.p12` is parsed in the signer; the private key is encrypted with a per-certificate data key
    (AES-256-GCM, associated data binds certificate id and company); the data key is wrapped by the cloud KMS
    (envelope). Requires our own INDOTEL authorization ([06] §6).
  - `remote`: signing is delegated to an INDOTEL-authorized certification entity's signing API; we store only
    the entity's credential reference and the certificate's public part; the key never reaches us.
  Both produce the same `SignXml`/`SignSeed` answers, so channels and callers do not know which is used.
- **Contract**: `contracts/proto/ledger/signer/v1/signer.proto`, `SignerService`: `ImportCertificate`
  (bytes + password streamed through `api`, never parsed outside the signer; carries the client's
  `Idempotency-Key`, stored in `signer.idempotency_keys`), `DescribeCertificates`, `RetireCertificate`
  (naturally idempotent), `SignXml` (enveloped XMLDSig, RSA-SHA256, the channel's profile id), `SignSeed`.
  Importing a certificate while one is active installs the new one and retires the old (a compare-and-set
  `RotateCertificate` is deferred). One certificate may serve both modes (`modes = {test, live}`); a test-mode
  request installs for test alone and never retires a certificate that serves live (ADR 0007). Deadlines set
  by the caller (≤ 10 s; an import, whose bundle streams, ≤ 30 s, the signer waiting at most 28 s for its
  stream); stable error codes in trailing metadata.
- **Signing is two-phase and the validity is the signer's**: fiscal-kit's `xml.dsig` prepares the document
  (hardened parse, C14N, digest of `SignedInfo`) in the signer, custody signs (or the entity signs the
  SHA-256 of `SignedInfo`), `dsig` completes the document. The signer refuses a signature unless its
  certificate covers the document's `signed_at` to `signed_at + skew` and `signed_at` is within the skew of
  its clock (`LEDGER_SIGNER_SIGNING_SKEW_SECONDS`, 300 to 900). A channel reads a certificate's validity
  through `DescribeCertificates` (`SignerPort.certificate_validity`).
- **Callers authenticate** with per-caller tokens (`LEDGER_SIGNER_TOKEN_API`, `_WORKER`, `_RECEIVER`, rotated
  by the secret manager; during a rotation the signer also accepts `LEDGER_SIGNER_TOKEN_<CALLER>_PREVIOUS`;
  each at least 32 visible ASCII characters, none shared, compared as SHA-256 digests in constant time) and a
  **method allowlist** per caller: `api` → `ImportCertificate`, `DescribeCertificates`, `RetireCertificate`;
  `worker`, `receiver` → `SignXml`, `SignSeed`, `DescribeCertificates`. A client process holds its own token
  only. The signer trusts the organization, company, mode and actor its caller forwards (the api asserts
  them before it calls) and binds the caller's name from the token: a residual risk until mTLS from the mesh
  replaces the tokens (ADR 0010, accepted risk 8). The port is plaintext until then.
- **Bounds**: imports `LEDGER_SIGNER_IMPORT_CONCURRENCY` at once (default 2), signatures
  `LEDGER_SIGNER_SIGNING_CONCURRENCY` (default 4), at most 64 calls in flight, messages at most 5 MiB, a bundle
  at most 2 MiB; a PKCS#12 bundle naming more than 1,000,000 key-derivation or MAC iterations is refused
  unopened (a key bag inside an encrypted safe is the residual).
- **Isolation in the manifest**: `[data] isolated_in = "signer"` (§4.2) makes the signer's role the only one
  that assembles the module's server side; the client roles bind its gRPC client, or, without
  `LEDGER_SIGNER_ADDRESS` and their own token, stand-ins answering `signer_unavailable`.
- **KMS**: until a cloud KMS adapter exists, only the local-file KMS does, refused outside `local` and `test`:
  no shared environment can run the signer before that adapter (and, for `remote`, a certification entity's).
- **Audit**: every signature (who, company, mode, document SHA-256, reference such as an e-NCF) is written to
  `signer.audit_events` and the signature is returned only after the audit commits (M3 decisions 5, 7, 9, 10
  kept: bounded signing pool, data-key cache off by default).
- **Faces**: the signer module's HTTP adapter (`/v1/certificates`) is mounted in `api` and forwards to gRPC;
  the gRPC server, consumer runner, jobs and purges are mounted only in `signer`. import-linter forbids every
  package but the signer's own and its app from importing `ledger_signer.adapters.custody`, which the assembly
  imports lazily, in the signer's role only.
- `api`'s readiness does **not** depend on the signer (a signer outage must not take the API out of rotation;
  certificate routes answer 503 `signer_unavailable`, and the synchronous signing path falls back to the job).

### 3.8 Receiver

Authority- and emitter-facing endpoints are a separate app ([02] R2). The receiver mounts each registered
channel's `receiver_routers`. For `do-ecf-v1` (paths fixed by DGII, case-insensitive; [06] §4), under a
per-company base `https://<receiver host>/do-ecf-v1/{rnc}` registered with DGII's directory per taxpayer:

| Route | Use |
|---|---|
| `GET /fe/autenticacion/api/semilla` | issues a seed `SemillaModel{valor, fecha}`: 32 random bytes, stored hashed in `do_ecf.receiver_seeds` with a 5 min expiry |
| `POST /fe/autenticacion/api/validacioncertificado` | verifies the sender's XMLDSig over an unexpired, unused seed with a certificate chaining to an accepted DR certification authority; issues an opaque bearer token (random, stored hashed, 1 h, bound to sender RNC and receiving company); answers `{token, expira, expedido}` |
| `POST /fe/recepcion/api/ecf` | bearer required (we publish the optional auth URL); size limit, hardened parse, sender signature verified, buyer RNC == `{rnc}`; raw XML archived; received document + event recorded in one unit; answers a signed ARECF |
| `POST /fe/aprobacioncomercial/api/ecf` | bearer required; records a buyer's ACECF about one of our company's emitted documents; 200 / 400 |

The company is resolved through identity's global tax-id registry (`CompanyDirectory`); an unknown or
inactive RNC answers 404. Nothing else runs in the receiver.

### 3.9 Extraction path

A module is extracted only for a runtime reason (security isolation, scaling or latency profile, data
residency, failure isolation; [02] R8), never to organize code. The procedure is mechanical:

| Seam | In process (now) | Extracted |
|---|---|---|
| Commands/queries | `api.py` Protocol bound to `application.facade` | same Protocol bound to a gRPC client; the module serves gRPC (`contracts/proto/ledger/<m>/v1`) |
| Events | `PostgresEventBus` (outbox → fan-out → inbox in one cluster) | broker adapter of `EventBus`; envelope unchanged |
| Data | schema `<m>` in the shared cluster, role `<m>_rw` | own database, schema copied by logical replication, then cut over |
| Deploy | mounted by `api`/`worker` | its own app entry in `apps/`, same image |

The signer is the worked example from day one.

### 3.10 Composition seam (runtime, modules, channels)

`platform.runtime.App(role)` builds an app in two phases, **assemble** then **start**:

1. Load manifests (entry point group `ledger.modules`, generated `_manifest.py`), regimes
   (`ledger.countries.regimes`) and channels (`ledger.countries.channels`) through `Regimes.load()` and
   `Channels.load()` (`Regimes` in `platform.runtime.registries`, `Channels` and the modules' loader in
   `platform.runtime.packs`; validated and frozen; a duplicate or invalid pack fails the start).
2. Assemble modules in topological order. Each gets a `ModuleContext`; its `ModuleParts` add its API
   implementation to the API directory (for later modules), its extension-point implementations, and the ports
   it provides to channels.
3. Build each channel's `ChannelContext` from the provided ports (signer module → `SignerPort`, fiscal →
   `CompanyDirectory`, `platform.blobs` → `ArchivePort`) and assemble it. The assembled channels populate the
   `ChannelRegistry` already handed to `fiscal` (read only at call time, never during assembly).
4. Merge every `ModuleParts`/`ChannelParts` and keep what the app's role mounts; start resources, consumers,
   jobs, schedules, servers.

```python
type AppRole = Literal["api", "worker", "signer", "receiver", "migrate"]

@dataclass(frozen=True, slots=True, kw_only=True)
class ModuleContext:
    manifest: PackageManifest           # generated, typed (its _manifest.py)
    role: AppRole
    settings: PackageSettings | None    # this module's own section (LEDGER_<MODULE>_*), validated; never the app's
    database: BoundDatabase             # units in this module's role and schema only; no role argument exists
    clock: Clock
    regimes: Regimes
    channels: ChannelRegistry           # populated after channel assembly; empty during assemble()
    apis: ApiDirectory                  # apis.get(IdentityApi): only Protocols of declared dependencies
    extensions: ExtensionDirectory      # extensions.get(IssuePolicy): bound by the app (dependency inversion)
    telemetry: Telemetry                # logger, tracer, meter scoped to the module
    auth: Authentication                # auth.caller: the one caller every route declares (§9.7)

@dataclass(frozen=True, slots=True, kw_only=True)
class ModuleParts:
    api: object | None = None                                   # implements the module's api Protocol
    implements: Mapping[type, object] = EMPTY_MAPPING            # extension point Protocol → implementation
    provides: Mapping[type, object] = EMPTY_MAPPING              # fiscal-kit port Protocol → implementation
    routers: Sequence[APIRouter] = ()                            # mounted by api
    grpc_services: Sequence[GrpcService] = ()                    # mounted by signer
    consumers: Sequence[ConsumerSpec] = ()                       # name from the manifest + handler
    jobs: Sequence[JobSpec] = ()                                 # kind from the manifest + handler
    schedules: Sequence[ScheduleSpec] = ()
    checks: Sequence[Check] = ()                                 # readiness
    resources: Sequence[AbstractAsyncContextManager[object]] = ()   # opened at start, closed at stop
    migrations: MigrationSet | None = None                       # used by migrate

@dataclass(frozen=True, slots=True, kw_only=True)
class ChannelContext:
    definition: ChannelDef
    regime: RegimeDef
    role: AppRole
    settings: PackageSettings | None    # its own section, LEDGER_<SCHEMA>_* (e.g. LEDGER_DO_ECF_*)
    database: BoundDatabase             # role do_ecf_rw, schema do_ecf
    clock: Clock
    signer: SignerPort
    directory: CompanyDirectory
    archive: ArchivePort
    http: HttpClientFactory             # outbound clients with timeouts, tracing, egress allowlist
    telemetry: Telemetry

@dataclass(frozen=True, slots=True, kw_only=True)
class ChannelParts:
    channel: Channel                    # the Protocol implementation fiscal routes to
    routers: Sequence[APIRouter] = ()                # public (/v1/do/...), mounted by api
    receiver_routers: Sequence[APIRouter] = ()       # mounted by receiver
    consumers: Sequence[ConsumerSpec] = ()
    jobs: Sequence[JobSpec] = ()
    schedules: Sequence[ScheduleSpec] = ()
    checks: Sequence[Check] = ()
    resources: Sequence[AbstractAsyncContextManager[object]] = ()
    migrations: MigrationSet | None = None
```

`ledger-devtools` treats each `[[channels]]` entry like a module for schemas, roles, grants, id prefixes,
permissions, subscriptions and job kinds.

**Settings sections (as built).** A module (`[package] settings = true`) or a channel (`settings = true` on its
entry) declares a section: its variables are `LEDGER_<SECTION>_*`, the section named after the module or the
channel's schema. Its assembly hands over the model that reads them (`Module.settings`, `ChannelPack.settings`, a
`PackageSettings` subclass whose `env_prefix` is `section_prefix(section)`). The app reads every declared section
when it is made, before any package assembles: a declared section without a model, a model for an undeclared one
or of another prefix is the package's defect; values that cannot work are listed together, never echoed. A
context holds its own section (`context.section(Model)`), never the app's `Settings`, and `BoundDatabase` keeps its
pool private: no package reads the database URLs or another's secrets. `sync` registers each section as an entry
point of group `ledger.settings`; the platform's `Settings` reads those names (it imports nothing), stays closed
for its own names and leaves the variables under the declared prefixes to their sections; every other unknown
`LEDGER_*` name is refused. `check` refuses a section whose prefix holds a platform variable or another
section's.

**Serving (as built).** `ledger_platform.runtime.serve(role)` holds the one Granian setup (`api`, `worker`): one
worker per replica, no respawn, the server's logs in the app's format, and a kill timeout that follows from the
stop budget (`WORKER_KILL_TIMEOUT` = `STOP_BUDGET` + 5 s < the orchestrator's 40 s grace). An app's `main` calls
it and nothing else.

Assembly fails the start, never a later call, when: two packages claim one schema (or a module claims
`platform`); a module or channel has no schema or no migrations, or hands over migrations of another schema;
a consumer or job kind is declared without a handler or handed over undeclared; a **required** extension
point has no implementation or a declared port no provider after every module assembled (a channel reads its ports
while it assembles, so a missing one fails there); an API, extension point implementation or port has a public
coroutine that is not a `@facade`. A port or an API is bound once (one provider each); an extension point
is **chained** instead (ADR 0011, which replaces the former one-implementation rule): every module that
implements it is in its chain, in `(order, module name)` order, and the declaring module reads the ordered
tuple (`extensions.all(KEY)`), empty when nobody implements an optional point.

---

## 4. Repository layout and tooling

### 4.1 Tree

```
ledger/
├── pyproject.toml            workspace root: tool config; members by glob; the lists naming packages are a block `sync` writes
├── uv.lock
├── README.md                 ~80 lines: what, layout, develop, run (no chassis manual; [05] §7.2)
├── AGENTS.md                 conventions for coding agents (points at this spec and the ADRs)
├── .importlinter             GENERATED from manifests (§4.6)
├── .github/workflows/ci.yml  one job running `poe check` (per-package matrix later, §16.4)
├── packages/
│   ├── kernel/               ledger-kernel        generic value types, regime schema types, tax calculator; stdlib only
│   ├── platform/             ledger-platform      the chassis (§5); owns schema `platform`
│   ├── fiscal-kit/           ledger-fiscal-kit    the minimal country-agnostic fiscal contract (§8.4)
│   └── testing/              ledger-testing       dev-only harness + pytest plugin (§14)
├── modules/
│   ├── identity/  signer/  invoicing/  fiscal/  webhooks/      ledger-<name>   (parties/, catalog/ later)
├── countries/
│   └── do/
│       ├── (package)         ledger-country-do          regime "do" + channel "do-ecf-v1"
│       └── testing/          ledger-country-do-testing  dev-only: fake DGII (`ledger-fake-dgii`), samples
├── apps/
│   ├── api/  worker/  signer/  receiver/  migrate/     ledger-app-<name>: composition roots only
├── contracts/
│   ├── buf.yaml              one buf module; protos for cross-process surfaces only
│   └── proto/ledger/signer/v1/signer.proto
├── tools/
│   └── devtools/             ledger-devtools: sync, check (§4.7; gate deferred)
├── deploy/
│   ├── docker/ledger.Dockerfile     the one image (every app in it; no dev-only distributions)
│   ├── local/                compose.yaml, images.toml, postgres/init.sql (GENERATED roles), zitadel/, localstack/
│   └── helm/ledger/          one chart, one values block per app (v2-M6)
├── docs/
│   ├── adr/NNNN-kebab-title.md       MADR; immutable once accepted; superseded-by links
│   ├── runbooks/<area>/<incident>.md   one place for every runbook (identity/, do/, ...)
│   └── superpowers/specs/            design specs (plans live in .superpowers/, untracked)
└── tests/                    whole-repo tests only (architecture invariants no tool expresses)
```

Inside every package (module shown; the DR country in §8.6):

```
modules/invoicing/
├── manifest.toml             source of truth (§4.2)
├── pyproject.toml            GENERATED from the manifest (header says so)
├── README.md                 one screen: what it owns, its public API, links
├── src/ledger_invoicing/
│   ├── __init__.py           docstring only
│   ├── _manifest.py          GENERATED: typed ModuleManifest
│   ├── api.py                PUBLIC: Protocols, DTOs, public errors
│   ├── events.py             PUBLIC: event dataclasses
│   ├── assembly.py           for apps only: assemble(ModuleContext) -> ModuleParts
│   ├── domain/               aggregates, value objects, state machines, domain errors: kernel only
│   ├── application/          use cases, ports (Protocols), facade.py (implements api)
│   └── adapters/
│       ├── postgres/         tables.py (schema-qualified), repositories/<aggregate>.py, migrations/versions/rNNNN_*.py
│       ├── http/             routers, request/response models (pydantic), OpenAPI fragments
│       └── <other>/          e.g. dgii/, xml/, kms/ — one directory per outside system
├── openapi.json              GENERATED snapshot of this module's public operations (contract test)
└── tests/{unit,integration,contract}/
```

### 4.2 Manifest schema: modules and platform (`manifest.toml`)

Validated by `ledger-devtools check` against a typed model (`ledger_platform.config.manifest`, which every
generated `_manifest.py` imports too); unknown keys are errors. Names are read with one rule each: permission
codes, event types and id prefixes with the kernel's parsers, schema names with the roles' rule
(`ledger_platform.config.names`), extension points as Protocol names. Every key below is defined; nothing else exists.

```toml
[package]
name = "invoicing"                  # required; == directory name; [a-z][a-z0-9_]{1,30}
kind = "module"                     # required; "module" | "country" | "app" | "package" | "tool"
summary = "Commercial documents: invoices, credit and debit notes, their lines and totals."
owners = ["@balegre0"]              # required (kept for CODEOWNERS generation later)
status = "active"                   # "active" | "suspended" (suspended: not assembled by any app, not migrated)
settings = true                     # optional: a settings section LEDGER_INVOICING_* (§3.10); modules only

[dependencies]
packages = ["kernel", "platform"]                 # workspace packages (not modules)
modules = ["identity"]                            # modules whose api/events this one imports or consumes; a DAG
python = ["fastapi>=0.143", "sqlalchemy>=2.1.4"]  # third-party, with floors; deptry checks usage
test = ["hypothesis>=6.168.5"]                    # what its tests and type checks need besides: the root's dev group

[data]
schema = "invoicing"                # PostgreSQL schema == role prefix; unique across the repo
# isolated_in = "signer"            # optional: the one role whose own login holds the schema's roles (ADR 0010)

[[ids]]                             # public TypeID prefixes this package mints; unique across the repo
prefix = "inv"
resource = "invoice"

[[permissions]]                     # open set, declared here, composed into roles by identity
code = "invoicing.invoices.read"    # <module>.<resource>.<action>; action in read|create|issue|void|manage|own
summary = "Read commercial documents and their fiscal status."
roles = ["owner", "admin", "accountant", "issuer", "viewer"]   # built-in roles granted it (§7.7)

[[events.publishes]]
type = "invoicing.invoice.issued"   # <module>.<aggregate>.<past_participle>
version = 1
public = "invoice.issued"           # optional: public name (<resource>.<verb>) exported to webhooks (§9.9)

[[events.consumes]]
consumer = "fiscal.commercial_documents"        # <module>.<purpose>; declared in the consuming module's manifest
types = ["invoicing.invoice.issued"]
max_attempts = 10                               # optional (default 10)
ordered = false                                 # optional: head-of-line claim per (consumer, subject) (§3.5)

[[jobs]]
kind = "invoicing.export"           # <module>.<verb_or_noun>
lease = "60s"                       # optional (default 60s)
timeout = "15m"                     # optional (default 15m): the longest one run may take; past it, stopped and retried
max_attempts = 20                   # optional (default 20)
deadline = "72h"                    # optional
concurrency = 8                     # optional: per worker process (default 8)

[[schedules]]
name = "invoicing.purge_drafts"
every = "24h"                       # or cron = "0 3 * * *" (UTC); exactly one of the two
enqueues = "invoicing.purge_drafts"

[extension_points]
declares = ["IssuePolicy"]          # Protocols in this module's api.py that other modules may implement
required = []                       # optional: those of them that must have an implementation (default: none);
                                    # an optional point nobody implements is an empty chain (ADR 0011)
implements = []                     # "<module>.<Protocol>" this module implements (bound by apps)
# [extension_points.order]          # optional: where each implementation goes in the point's chain, lowest first
# "invoicing.IssuePolicy" = 10      # 0-1000, default 100, ties by module name (ADR 0011)
provides = []                       # ports it provides: fiscal-kit's to channels ("SignerPort", "CompanyDirectory"),
                                    # or the platform's "StandingsPort" to the app's caller (identity, §9.7)

[http]
mounted_in = ["api"]                # apps whose HTTP surface includes this module's routers
prefixes = ["/v1/invoices"]         # unique across the repo
```

`packages/platform/manifest.toml` uses the same schema (`kind = "package"`, `schema = "platform"`, `ids =
[{prefix = "evt", resource = "event"}]`, the two purge schedules). Other packages, apps and tools have
`[package]` and `[dependencies]` only; an app also has `[app] script = "ledger-api"`, `role = "api"`.

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

[[channels]]                        # treated like a module by devtools (schema, roles, grants, routing)
key = "do-ecf-v1"                   # <cc>-<format>-v<N>; one key per format major version
entry_point = "ledger_country_do.channels.ecf_v1:CHANNEL"   # its ChannelPack: entry, regime, assembly
schema = "do_ecf"                   # one schema per channel family; v2 of the format shares it
valid_from = "2019-01-01"           # legal window of this format version; valid_until optional
environments = ["testecf", "certecf", "ecf"]
live_environments = ["ecf"]         # environments allowed in live mode
document_kinds = ["invoice", "credit_note", "debit_note"]   # commercial kinds it covers (§7.6)
directions = ["sales", "purchase"]
http_prefixes = ["/v1/do"]          # public API (api app); receiver routes under /do-ecf-v1 (receiver app)
tier = "bronze"                     # claimed quality tier (§8.8); checked by review until tier machinery exists
settings = true                     # optional: a settings section LEDGER_DO_ECF_* (§3.10)

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
type = "fiscal_channel.document.updated"        # fiscal-kit-owned event types (§8.4)
version = 1

[[channels.events.publishes]]
type = "fiscal_channel.received_document.recorded"
version = 1
public = "received_document.created"

[[channels.jobs]]
kind = "do_ecf.advance"
max_attempts = 50                   # failures; RunAgain (polling) does not count
deadline = "15d"
concurrency = 16

[[channels.schedules]]
name = "do_ecf.range_watch"
every = "1h"
enqueues = "do_ecf.range_watch"
```

### 4.4 Naming conventions

| Thing | Rule | Example |
|---|---|---|
| Directory | `packages/<name>`, `modules/<name>`, `countries/<iso2>`, `apps/<name>`; lowercase, `-` only in package names | `modules/invoicing`, `packages/fiscal-kit` |
| Distribution | `ledger-<name>`; country `ledger-country-<cc>`; its dev-only testing `ledger-country-<cc>-testing`; app `ledger-app-<name>` | `ledger-country-do` |
| Import package | distribution with `_`; exactly one top-level package per distribution; no namespace packages | `ledger_fiscal_kit` |
| Console script | `ledger-<app>`; dev tools `ledger-<tool>` | `ledger-worker`, `ledger-fake-dgii` |
| Manifest key | `package.name` == directory name ([03] HA rule) | |
| Schema / DB roles | schema = module name (`do_ecf` for a channel family); roles `<schema>_owner`, `<schema>_rw`; logins `ledger_app`, `ledger_signer`, `ledger_migrator`; group `ledger_relay` | `invoicing_rw` |
| Permission | `<module>.<resource>.<action>`, plural resource, action from a closed verb list | `fiscal.enrollments.manage` |
| Event type | `<module>.<aggregate>.<past_participle>`; kit-owned: `fiscal_channel.<aggregate>.<past_participle>` | `identity.company.registered` |
| Public event name | `<resource>.<verb>` (exactly two segments) | `invoice.fiscal_updated` |
| Consumer | `<module>.<purpose>` | `fiscal.commercial_documents` |
| Job kind / schedule | `<module or channel>.<verb_or_noun>` | `do_ecf.advance` |
| Public id prefix | 2-5 lowercase letters, unique, declared in a manifest | `inv_01jc5...` |
| Regime key / channel key | `<cc>` or `<cc>-<sub>` / `<cc>-<format>-v<N>` | `do`, `do-ecf-v1` |
| Tax-id scheme | `<cc>.<scheme>`, `<cc>` one assigned ISO 3166-1 country, never a group (EU VAT ids are per member state) | `do.rnc`, `mx.rfc`, `es.nif`, `fr.tva` |
| Country field (wire) | `fiscal.<regime>.<field>` (regime law-level fields and channel format fields share the regime namespace) | `fiscal.do.income_type` |
| Error code | `<thing>_<problem>`, snake_case, unique across the repo | `range_exhausted` |
| Env var | `LEDGER_<SETTING>`; module or channel settings `LEDGER_<MODULE>_<SETTING>` | `LEDGER_DO_ECF_DGII_TIMEOUT_SECONDS` |
| Proto package | `ledger.<module>.v<N>`, in `contracts/proto/ledger/<module>/v<N>/`; its Python code generated into `ledger_<module>.adapters.grpc._generated.v<N>` | `ledger.signer.v1`, `ledger_signer.adapters.grpc._generated.v1` |
| Python modules | nouns, `lower_with_under`; never `utils`, `helpers`, `common`, `misc`, `shared` | `numbering.py` |
| Tests | `tests/unit|integration|contract/test_<subject>.py`; support modules prefixed by package (`invoicing_samples.py`) | |
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
`ledger_fiscal_kit`'s pure types); `application` may import `ledger_platform.data` *ports* (`UnitOfWork`,
`Jobs`, `Clock`) but no adapter; third-party imports below `adapters` are forbidden (one generated contract per
package, replacing v1's three hand-copied lists, [05] D35). A CRUD module may have an empty `domain/` ([02] R9);
only the public surfaces and the layering are mandated.

### 4.6 import-linter contracts (generated into `.importlinter`)

| # | Contract | Type | Content |
|---|---|---|---|
| 1 | Workspace layers | `layers` | `ledger_app_*` > (`ledger_<module>` \| `ledger_country_*`) > `ledger_fiscal_kit` > `ledger_platform` > `ledger_kernel` |
| 2 | Kernel is stdlib only | `forbidden` | `ledger_kernel` → every third-party distribution in `uv.lock` |
| 3 | Regimes are pure | `forbidden` | `ledger_country_*.regime` → everything but `ledger_kernel` and the stdlib |
| 4 | Modules never import countries | `forbidden` | `ledger_<module>` → `ledger_country_*` |
| 5 | Countries never import modules | `forbidden` | `ledger_country_*` → `ledger_<module>` |
| 6 | Countries are independent | `independence` | all `ledger_country_*` |
| 7 | Module public surface | `protected` (one per module) | `ledger_<m>.*` importable only by `ledger_<m>` itself, except `ledger_<m>.api` and `ledger_<m>.events` (by declared dependents) and `ledger_<m>.assembly` (by apps) |
| 8 | Declared module edges only | `forbidden` (one per module) | `ledger_<m>` → every module not in its `dependencies.modules` |
| 9 | Acyclic modules | `acyclic_siblings` | over the module set (subscription edges checked by `ledger-devtools check`) |
| 9b | Flows are leaves | `forbidden` | `ledger_<module>` → `ledger_flow_*` (ADR 0011); flows reach modules through `api`, `events` and declared edges only. **Deferred**: not generated until the first flow exists (§16.4) |
| 10 | Layers inside each package | `layers` with `containers` | §4.5 per module, per channel |
| 11 | Domain purity | `forbidden` (one per package) | `*.domain`, `*.events`, `*.application` → third-party |
| 12 | Signer custody | `forbidden` | everything but `ledger_app_signer` and `ledger_signer` → `ledger_signer.adapters.custody` |
| 13 | Test code never in production | `forbidden` | every production distribution → `ledger_testing`, `ledger_country_*_testing` |
| 14 | Apps only compose | `forbidden` | `ledger_app_*` → `ledger_<m>.{domain,application,adapters}`, `ledger_country_*` |
| 15 | Only the chassis proves tokens | `forbidden` | `ledger_<module>`, `ledger_country_*` → `ledger_platform.http.proving` (the token-only caller; every route declares `Authentication.caller`, which applies how its caller stands) |

No `ignore_imports` in new code; if one is ever needed, `ledger-devtools check` counts it as a tracked debt
metric ([02] Kraken).

### 4.7 devtools (`tools/devtools`, `ledger-devtools`)

A workspace member under the full gate. Built now: `sync` and `check`. Deferred (§16.4): `gate` (`uv run poe
check` is the gate, §14.3), `affected` and the CI matrix, `scaffold` (copier templates), CODEOWNERS, generated
reference docs and event schema snapshots. identity's role table is generated (§7.7).

| Command | Does |
|---|---|
| `sync` | From manifests: each package's `pyproject.toml` (dependencies, entry points `ledger.modules`, `ledger.countries.regimes`, `ledger.countries.channels`, `ledger.settings`, console scripts, coverage targets, deptry's module map read from the installed distributions' metadata); `_manifest.py` for a module, a package with a schema and a country with a channel; identity's role table (`ledger_identity/domain/_role_grants.py`, §7.7); `.importlinter`; `deploy/local/postgres/init.sql` (schemas, roles, grants, for modules, channels and platform); each module's `openapi.json`; the root `pyproject.toml`'s generated block (the `workspace` dependency group with every dev-only package and every manifest's test dependencies, `[tool.uv.sources]`, ruff's `known-first-party`), so adding a package touches only its directory and generated files. In two phases: every other file first, then the OpenAPI snapshots, made in a spawned interpreter that imports the files just written (the app they come from imports a module's `_manifest.py` and identity's role table) |
| `check` | Validates every manifest (schema, name == directory, a country's `iso` == its directory and its regimes' keys its own, unique schema/prefix/permission/event type/consumer/job kind/schedule/http prefix, module and package DAGs including subscription edges, schedules enqueuing declared kinds, modules made of layers, settings sections apart from the platform's names and each other); fails if `sync` would change or delete any file; checks the repository around the manifests (the root's block, members without a manifest, image pins with digests, the build context). Deferred: event schema snapshots, channel `valid_from` ordering (deptry runs in the gate) |
| `gate` | Deferred: `uv run poe check` runs the gate (§14.3) |

---

## 5. Platform chassis (`packages/platform`, `ledger_platform`)

### 5.1 Kernel (`packages/kernel`, `ledger_kernel`), for reference

Generic value types, stdlib only, no country rules (R3):

| Module | Public surface |
|---|---|
| `errors` | `ErrorKind`, `LedgerError` + six kind bases, `InvalidValueError(field, requirement)` (the single "our code built a bad value" error, [05] D4), `excerpt`, `truncate` |
| `ids` | `OrganizationId`, `CompanyId`, `new_id()` (UUIDv7), TypeID codec (`format_id(prefix, uuid)`, `parse_id(prefix, text)`) |
| `tenancy` | `Mode` (`test`/`live`), `Tenant(organization, company, mode)`, `Scope` = `Tenant` \| `OrganizationScope(organization, mode | None)` with `.organization`/`.company`/`.mode` (ends the four `match` copies, [05] D14) |
| `access` | `SubjectId`, `Principal`, `Permission` (validated `NewType`, `<module>.<resource>.<action>`) |
| `money` | `Currency` (ISO 4217 table: code, numeric, minor units, kept as data), `Money` (exact `Decimal`, laws as v1 §7), `RoundingMode` |
| `jurisdiction` | `CountryCode` (ISO 3166-1 alpha-2 table), `Jurisdiction(country, subdivision: str | None)` (ISO 3166-2) |
| `tax_ids` | `TaxId(scheme, value)`, `TaxIdScheme` (`<cc>.<name>`), `TaxIdCheck` (`valid` \| `checksum_mismatch` \| `invalid_format`), masked `repr` |
| `dated` | `Timeline[T]` (newest-first `Since[T]` values, `at(date)`, order validated) |
| `regimes` | the pure regime schema types (§8.2): `RegimeDef`, `TaxCategoryDef`, `RateValue`, `DocumentTypeDef`, `TaxIdSchemeDef`, `FieldDef`, `Source`, `RoundingRule`, `Locale`, and the single `TaxCalculator`. **No loading**: the entry-point registry is `platform.runtime.registries.Regimes` (I15; an accepted deviation from R3's "kernel holds value types only", recorded in ADR 0005) |
| `pagination` | `PageRequest`, `Page[T]`, cursor codec (kept) |
| `clock` | `Clock`, `MonotonicClock`, `Sleep`, `utc_now` (kept) |

### 5.2 Subpackages and public surface

Every subpackage has an `__init__.py` with an explicit `__all__`; everything else is private. The public surface
an application author touches is the ~35 names below ([05] §5: v1 exposed 263 symbols, 148 unused). R3 placed
outbox, inbox and jobs under `data`; v2 keeps their **tables** in `data` (`chassis_schema_v1`) and their
**engines** in `events` and `jobs`, so `data` stays storage-only.

| Subpackage | Public surface | From v1 (`libs/platform`) |
|---|---|---|
| `config` | `Settings` (base, `LEDGER_*`, fail-closed on unknown variables, the declared sections' prefixes left to them), `PackageSettings` and `section_prefix` (a package's section, §3.10), `InvalidOptionError(component, option, requirement, value)` (one code, `invalid_option`); `config.names` (`PLATFORM`, the schema-name rule); the manifest model `config.manifest` (what devtools validates `manifest.toml` with and every generated `_manifest.py` imports, hence below every other layer but `_manifest`'s data) | `settings.py` (kept), `options.py` (12 subclasses collapsed into one, [05] D7) |
| `observability` | `configure_logging`, `Tracing`, `Health`, `Check`, metrics helpers (`Telemetry`, the per-package logger and tracer, is `runtime`'s: it lives in `runtime.contexts`) | `logs.py`, `tracing.py`, `health.py`, `probe.py` (kept, moved); validation-issue rendering shared with `http` ([05] D10) |
| `runtime` | `App`, `AppRole`, `ModuleContext`, `ModuleParts`, `ChannelContext`, `ChannelParts`, `Call`, `SystemCall`, `facade` (decorator with the open-unit guard), `UnitOpenError`, `Supervisor`, `Telemetry`, `registries.Regimes`, `packs.Channels`, `serve(role)` (Granian, §3.10) | `service.py` split, `supervision.py` kept, `lifecycle.py` merged into `AsyncExitStack` use, `interruptions.py` deleted |
| `data` | `Database` (`Pooling`: overflow, through a pooler), `BoundDatabase` (`unit(scope)`; its pool private), `UnitOfWork` (`uow.publish(event)`, `uow.jobs`, `uow.audit(...)`), `ModuleTable` (the base of every package's tables: `class IdentityTable(ModuleTable, schema="identity")`, the one class ruff is told SQLAlchemy reads), `RlsScope` and `policy(table, scope)`, `chassis_schema_v1(op, schema)`, `SchemaMigrations` | `database.py` split, `migrations.py` kept (data-migration helpers deleted until needed), `schema.py` merged |
| `data.queue` | the claim/lease/fencing/retry engine shared by inbox and jobs (`claim`, `Backoff`, `Lease`), and where a stopped worker's loop ends (`parked`, `being_cancelled`), below `events` and `jobs` | new; replaces the relay half of `outbox.py` and the delivery loop of `consumer.py` |
| `events` | `Event`, `RawEvent` (generic envelope, for webhooks), `EventBus`, `PostgresEventBus`, `ConsumerSpec`, `Dispatcher` (fan-out), codec | `outbox.py` writer kept, `consumer.py` inbox part kept; JetStream parts deleted |
| `jobs` | `Jobs` (enqueue, wake, wake_all), `JobSpec`, `ScheduleSpec`, `Done`, `RunAgain`, `Park` | new |
| `auth` | `TokenVerifier`, `PublishedKeys`, `ClaimsProfile` port + `zitadel` profile, `Access`, `PermissionDeniedError`, `CompanyNotFoundError` | `authentication.py` split ([05] §4.1), `authorization.py` minus `CachedPermissions` |
| `http` | `create_app`, `Problem`, `Page[T]`, route guards (`authenticated`; the company from the path, `company_in_path`, or from the `Ledger-Company` header, `company_in_header`; `allowed(in_company, permission)`; `holding(caller, role)`, a provider role in the caller's organization; `in_organization`; `unguarded_routes` for the test that every route is guarded), `page_query`, `if_match`, `idempotency_key`, `ledger_mode_of(caller)` (the request's mode, settled once its caller is proven), `etag`, `precondition_checked`, `problems()`/`success()` OpenAPI helpers; `api_version` arrives with dated versions | `web.py` split, `routes.py` split into deps, `idempotency.py` and `request_fields.py` moved here; identity's `api/context.py` helpers moved in ([05] D22-D25) |
| `grpc` | `serve`, `GuardInterceptor` (per-caller token + method allowlist), `call_unary`, `RemoteError` family | `grpc_server.py`, `grpc_client.py` (kept, signer only); one kind → status table shared with `http` ([05] D5) |
| `blobs` | `ImmutableStore` (`put_once(key, bytes, retain_until)`, `get`), `S3ImmutableStore` (object lock, compliance mode, `If-None-Match: *`, SHA-256 checksums); also the `ArchivePort` implementation handed to channels | new (from M4 plan's archive adapter) |

Deleted outright: `streams.py`, `jetstream.py`, `interruptions.py`, `identity_client.py`, `CachedPermissions`
and its tests, `lifecycle.py` (folded), the NATS connection policy in `service.py` (R2, R5).

### 5.3 Unit of work

```python
async with context.database.unit(call.scope) as uow:     # role and schema bound at assembly, not passed here
    document = await documents.get(uow, document_id, for_update=True)
    ...
    uow.publish(InvoiceIssued(...))       # invoicing.outbox, same transaction
    uow.jobs.enqueue("invoicing.export", key=str(document.id), run_at=...)
    uow.audit(action="invoice.void", subject=document.id, before=old, after=new)
```

- One process-wide pool logs in as `ledger_app` (`NOINHERIT`, member of every `<schema>_rw` but `signer_rw`).
  `BoundDatabase` is created by the runtime from the manifest's schema; each unit begins with
  `SET LOCAL ROLE <schema>_rw` and `set_config('ledger.organization_id' | 'ledger.company_id' | 'ledger.mode',
  …, true)`. Tables are **schema-qualified** in their SQLAlchemy definitions; there is no `search_path`
  setting. A module's transaction therefore cannot read or write another schema even through a bug (ADR
  0004) — a mistake, not hostile SQL: the login is a member of every `_rw` role, so code that runs its own
  `SET ROLE` can switch; isolation against hostile code would need a login per module. The pool also
  resets every connection it takes back (`RESET ROLE`, `RESET ALL`).
- The unit marks itself open on the task (`ContextVar`) for the facade guard (§3.4).
- Commit only if every statement succeeded; after-commit callbacks (coalesced NOTIFY) never run after a rollback
  (v1 §7 kept). Connection limits as v1 (connect 5 s, statement 30 s, lock 5 s, idle-in-transaction 60 s, pool
  wait 10 s; `hide_parameters`).

---

## 6. Data

### 6.1 One cluster, schema per module

PostgreSQL 18, one cluster (managed in production), one database `ledger`. Each module, each channel family and
the platform own one schema (ADR 0004). No foreign key, view, join or function crosses schemas; cross-module
reads go through facades or event-fed read models. A schema can move to its own database by logical replication
(§3.9). v1's unused `ledger` app database and `ledger_app` narrative are replaced ([05] §3.3).

### 6.2 Roles

| Role | Login | Privileges |
|---|---|---|
| `<schema>_owner` | no | owns schema `<schema>` and its objects; `FORCE ROW LEVEL SECURITY` on tenant tables |
| `<schema>_rw` | no | `USAGE` on its schema; DML on its tables; no DDL; not `BYPASSRLS`; read-only on `alembic_version` |
| `<schema>_purge` | no | `USAGE` on its schema; `SELECT, DELETE` on `idempotency_keys`, whose policies `TO <schema>_purge` admit expired rows only |
| `ledger_migrator` | yes | member of every `_owner`; used only by `migrate` (`SET ROLE <schema>_owner` per schema) |
| `ledger_app` | yes | `NOINHERIT` member of every `_rw` and `_purge` except the signer's, and of `ledger_relay`; used by `api`, `worker`, `receiver` |
| `ledger_relay` | no | `SELECT, DELETE` and `UPDATE (attempts, unrouted_since)` on every `<schema>.outbox`; `INSERT` on every `<schema>.inbox`; `SELECT` on every `<schema>.subscriptions` and `platform.routing` — the signer's two queue tables included; nothing else |
| `ledger_signer` | yes | member of `signer_rw`, `signer_purge` and `platform_rw` (schedule leases) only; used only by `signer` |

No role is a superuser or `BYPASSRLS`. Each role is created if missing and then altered to exactly its
options; memberships are granted `WITH ADMIN FALSE, INHERIT FALSE, SET TRUE`. Each database revokes `CONNECT`
and `TEMPORARY` from `PUBLIC` (the logins get `CONNECT`), all on schema `public`, and `EXECUTE` on the owners'
new functions. Generated by `ledger-devtools sync` into `init.sql` (local) and the chart's bootstrap job
(production).

### 6.3 Row-level security

Four scopes, one builder (`platform.data.rls.policy(table, scope)`, [05] D13), one policy per command; the
settings are read through `NULLIF(…, '')` because `current_setting(…, true)` returns `''` (not `NULL`) once a
pooled connection has seen the setting, and `''::uuid` would raise:

```sql
-- scope "tenant": organization_id, company_id, mode
USING (organization_id = (SELECT NULLIF(current_setting('ledger.organization_id', true), '')::uuid)
   AND company_id      = (SELECT NULLIF(current_setting('ledger.company_id', true), '')::uuid)
   AND mode            = (SELECT NULLIF(current_setting('ledger.mode', true), '')))
-- scope "company": organization_id, company_id; a company's rows shared by both modes
-- scope "organization": organization_id only (organizations, companies, tax ids, memberships, establishments);
--   visible to the organization's transactions and to those of its companies. A company's establishments are
--   here because registering a company writes its `MAIN` one in the same unit, before any company scope exists
-- scope "moded_organization": organization_id + mode (organization-level idempotency keys). API clients are
--   under "organization" (identity's revision 0003): a client's mode is a column it is bound to, read by its
--   subject across modes in the standings' one transaction; every other read and write names its mode
```

- Settings wrapped in `(SELECT …)` are evaluated once per statement ([04] rule 7). Every hot index leads with
  `(organization_id, company_id, mode)` or `(company_id, mode)`.
- Foreign keys carry the tenant (`(organization_id, company_id, mode, x_id)`), so a row cannot point at another
  tenant's (v1 kept).
- Every ordinary policy is `TO <schema>_rw, <schema>_owner`; the purge policies are `TO <schema>_purge` only,
  so no other role sees a protected row whatever settings it sets.
- **Chassis tables under RLS**: `idempotency_keys` (`moded_organization`; expired rows also visible to the
  `<schema>_purge` role, for the purge) and `audit_events` (`organization`, append-only: `SELECT` and `INSERT`
  policies only, `UPDATE`/`DELETE`/`TRUNCATE` revoked).
- **Outside RLS by design**: `outbox`, `inbox`, `jobs`, `subscriptions`, `platform.*`, identity's
  `tax_registrations`, `do_ecf.receiver_seeds`/`receiver_tokens`. They hold ids, states, dates, hashes and event
  payloads; only platform or receiver code reads them.
- A **generated test per tenant table** proves deny-by-default without context and allow/deny across two
  tenants and two modes. A missing context fails closed.

### 6.4 Chassis schema v1

Every module and channel schema's first revision calls `platform.data.chassis_schema_v1(op, schema)`. The
function is versioned and immutable; a later change ships `chassis_schema_v2` and each schema adds a revision
calling it ([05] D20). Its DDL (`{s}` = schema):

```sql
CREATE TABLE {s}.outbox (
  id               uuid PRIMARY KEY,                 -- event id (UUIDv7: time-ordered)
  type             text NOT NULL, version smallint NOT NULL, source text NOT NULL,
  organization_id  uuid, company_id uuid, mode text CHECK (mode IN ('test','live')),
  subject          uuid, aggregate_version bigint NOT NULL DEFAULT 0,   -- 0: the producer gave none
  occurred_at      timestamptz NOT NULL, traceparent text,
  payload          jsonb NOT NULL CHECK (octet_length(payload::text) <= 65536),
  attempts         int NOT NULL DEFAULT 0,          -- failed fan-outs of this row; set aside at 10
  unrouted_since   timestamptz                       -- no subscriber at fan-out time (kept, alerted)
) WITH (fillfactor = 90, autovacuum_vacuum_scale_factor = 0.0, autovacuum_vacuum_threshold = 1000,
        autovacuum_vacuum_cost_delay = 0);
CREATE INDEX outbox_due ON {s}.outbox (id) WHERE unrouted_since IS NULL;

CREATE TABLE {s}.inbox (
  consumer text NOT NULL, event_id uuid NOT NULL,
  type text NOT NULL, version smallint NOT NULL, source text NOT NULL,
  organization_id uuid, company_id uuid, mode text,
  subject uuid, aggregate_version bigint NOT NULL DEFAULT 0, occurred_at timestamptz NOT NULL, traceparent text,
  payload jsonb NOT NULL,
  status text NOT NULL DEFAULT 'ready' CHECK (status IN ('ready','running','dead')),
  attempts int NOT NULL DEFAULT 0, next_attempt_at timestamptz NOT NULL DEFAULT now(),
  lease_until timestamptz, lease_token uuid, last_error_code text,
  PRIMARY KEY (consumer, event_id)
) WITH (fillfactor = 80, autovacuum_vacuum_scale_factor = 0.0, autovacuum_vacuum_threshold = 1000,
        autovacuum_vacuum_cost_delay = 0);
CREATE INDEX inbox_due     ON {s}.inbox (consumer, next_attempt_at) WHERE status = 'ready';
CREATE INDEX inbox_expired ON {s}.inbox (consumer, lease_until)     WHERE status = 'running';
CREATE INDEX inbox_head    ON {s}.inbox (consumer, subject, aggregate_version, event_id)
  WHERE status <> 'dead';                                     -- ordered consumers: head of each subject

CREATE TABLE {s}.jobs (
  kind text NOT NULL, key text NOT NULL,
  organization_id uuid, company_id uuid, mode text,
  status text NOT NULL CHECK (status IN ('ready','running','parked','done','dead')),
  run_at timestamptz NOT NULL, lease_until timestamptz, lease_token uuid,
  attempts int NOT NULL DEFAULT 0, max_attempts int NOT NULL, deadline_at timestamptz,
  parked_reason text, payload jsonb NOT NULL DEFAULT '{}', last_error_code text, traceparent text,
  created_at timestamptz NOT NULL, updated_at timestamptz NOT NULL,
  PRIMARY KEY (kind, key)
) WITH (fillfactor = 80, autovacuum_vacuum_scale_factor = 0.0, autovacuum_vacuum_threshold = 1000,
        autovacuum_vacuum_cost_delay = 0);
CREATE INDEX jobs_due     ON {s}.jobs (kind, run_at)        WHERE status = 'ready';
CREATE INDEX jobs_expired ON {s}.jobs (kind, lease_until)   WHERE status = 'running';
CREATE INDEX jobs_parked  ON {s}.jobs (kind, company_id, parked_reason) WHERE status = 'parked';
-- chassis_schema_v2 (a revision in every schema) adds the two the housekeeping reads, as built:
CREATE INDEX jobs_deadline ON {s}.jobs (kind, deadline_at)
  WHERE status IN ('ready','running') AND deadline_at IS NOT NULL;  -- the deadline sweep
CREATE INDEX jobs_done ON {s}.jobs (updated_at) WHERE status = 'done'; -- the done-job purge, oldest first
-- and the mark a wake (wake, wake_all) leaves on a running job: its next Park is ready at once. Cleared when
-- the job is claimed, recovered, released or settled; parked_reason holds reasons only
ALTER TABLE {s}.jobs ADD COLUMN woken_at timestamptz;
-- done jobs are deleted by the purge after 7 days unless the kind keeps them; dead jobs are kept

CREATE TABLE {s}.subscriptions (
  consumer text NOT NULL, event_type text NOT NULL, active boolean NOT NULL,
  updated_at timestamptz NOT NULL, PRIMARY KEY (consumer, event_type));

CREATE TABLE {s}.idempotency_keys (
  organization_id uuid NOT NULL, mode text NOT NULL, principal text NOT NULL,
  route text NOT NULL,                              -- method + route template: keys are scoped per route
  key text NOT NULL CHECK (length(key) <= 255),
  request_hash bytea NOT NULL, response_status smallint, response_body bytea, response_headers jsonb,
  created_at timestamptz NOT NULL, expires_at timestamptz NOT NULL,
  PRIMARY KEY (organization_id, mode, principal, route, key));
CREATE INDEX idempotency_expiry ON {s}.idempotency_keys (expires_at);
-- RLS scope "moded_organization"; expired rows also visible to <s>_purge (SELECT, DELETE only)

CREATE TABLE {s}.audit_events (                     -- append-only
  id uuid PRIMARY KEY, occurred_at timestamptz NOT NULL,
  organization_id uuid, company_id uuid, mode text,
  actor text NOT NULL,                               -- "human:<subject>", "machine:<subject>" or "system:<reason>"
  call_id text NOT NULL,                             -- request id or SystemCall id
  action text NOT NULL, subject_type text NOT NULL, subject_id text NOT NULL,
  before_hash bytea, after_hash bytea,               -- SHA-256 of the canonical before/after, never the data
  detail jsonb NOT NULL DEFAULT '{}');               -- ids and codes only
-- RLS scope "organization", SELECT and INSERT policies only
REVOKE UPDATE, DELETE, TRUNCATE ON {s}.audit_events FROM {s}_rw;   -- the rw role may only INSERT and SELECT
```

`platform` schema: `routing(version bigint)` (one row), `schedule_leases(name PRIMARY KEY, holder, leased_until,
next_run_at)`. Purges (platform schedules): idempotency keys past `expires_at`; done jobs after 7 days. Handled
inbox rows are deleted by their handler's transaction (§3.5); dead inbox rows and dead jobs are kept until an
operator acts.

### 6.5 Migrations

- Alembic per schema (`version_table_schema = <schema>`), revisions in the package
  (`adapters/postgres/migrations/versions/rNNNN_*.py`), run by `ledger-migrate` in the manifests' dependency
  order (platform first), each schema under its owner role, one session advisory lock per schema (10 min wait),
  each revision one transaction, never SQL text files (v1 kept).
- After migrations: upsert subscriptions, bump `platform.routing.version` (§3.5).
- Expand-only while two versions run; contract steps in a later release. Readiness checks the schema is at or
  past this version's head (v1 kept; a missing version table is "not migrated", M4 decision 18).
- v1's identity revisions `r0001`/`r0002` are squashed into a new `r0001` (nothing is in production).

### 6.6 Ids, modes, shard key, time

- **Ids**: UUIDv7 inside (`uuid` columns), typed `NewType` per entity; **public ids are TypeIDs**
  (`inv_01jc5r3ahte8sb0d3zpx8n9q0k`), formatted and parsed only at the HTTP edge (§9.4); prefixes declared in
  manifests and unique.
- **Mode**: every tenant row carries `mode`; every credential is bound to one mode (a certificate may be bound
  to both); a query never mixes modes because RLS includes the mode (ADR 0007).
- **Shard key**: `organization_id` on every tenant and organization row. Invariant: *no table, index, sequence
  or cache key is unique across organizations*, except identity's `tax_registrations (scheme, value) → company`,
  the future cell router's table ([04] rule 1).
- **Time**: `timestamptz` UTC; legal dates computed at the domain edge in the **establishment's time zone**,
  falling back to the regime's default (I11).
- Money columns: `numeric` with the value's own scale; currency `char(3)`.

### 6.7 Connection pooling

Application traffic may go through a transaction-mode pooler (PgBouncer or the managed equivalent): every unit
uses only transaction-scoped state (`SET LOCAL ROLE`, `set_config(…, true)`), and schedules use lease rows, not
session advisory locks. Two things connect **directly** to PostgreSQL (`LEDGER_DATABASE_DIRECT_URL`): each
worker's `LISTEN` connection, and `ledger-migrate` (session advisory locks, DDL). `LEDGER_DATABASE_POOLED=true` says
`LEDGER_DATABASE_URL` goes through a transaction pooler: psycopg then prepares no statement on it. The worker
refuses to start without the direct URL.

The pool is sized by `LEDGER_DATABASE_POOL_SIZE` (default 30) and grows by `LEDGER_DATABASE_POOL_OVERFLOW`
(default 10) for a burst; the check counts on no overflow. An app refuses to start with a pool smaller than its
role may hold at once (`PoolTooSmallError`, with the numbers): for the worker, every job kind's `concurrency`
twice (a running job's handler holds a unit while its lease is renewed in another), one connection per inbox
runner, per job runner, the fan-out and the scheduler, and a headroom of 2; for the others, the headroom. A
suspended module's settings section is validated too: its variables are read all the same.

### 6.8 Legal archive and backups

Signed XML (emitted, RFCE summaries, received), ARECF/ACECF, authority requests and responses, and rendered
printed representations go to object storage with object lock in compliance mode for **10 years** (Código
Tributario art. 50 h; DGII technical report §9.1; [06] §5), keyed by content digest, never overwritten. The full
XML of type-32 documents summarized by RFCE is archived too ([06] §2). PostgreSQL stores the index and hashes,
never the only copy. Point-in-time recovery for the cluster, restore tested monthly.

---

## 7. Domain model

### 7.1 Tenancy

```
Organization (Zitadel organization; shard key; pinned API version)
 ├── Company (a legal entity / taxpayer)            ── jurisdiction, functional currency, tax ids, status
 │    ├── Establishment (code, name, time zone; minimal CRUD; "main" created with the company)
 │    ├── Membership (principal, role)               ── shared across modes
 │    └── per mode (test | live): enrollments, certificate bindings, ranges, invoices, webhooks, idempotency keys
 └── ApiClient (Zitadel machine user ↔ organization, mode, pinned API version)
```

- **Organization** = the Zitadel organization; its ledger id is UUIDv5 of the provider id (v1 kept, now inside
  the `zitadel` claims profile, [05] §4.1). It holds `api_version`, pinned at creation (§9.2).
- **Company**: `id`, `organization`, `legal_name` (generic limit 300; a channel enforces its own, e-CF 150),
  `jurisdiction` (country + optional ISO 3166-2 subdivision), `functional_currency`, `tax_ids: tuple[TaxId,
  ...]` (at least one; the primary one is the registry key), `status`, `fiscal_profile_locked_at` (set through
  `IdentityApi.lock_fiscal_profile` by `fiscal` before a company's first numbered document; afterwards
  its whole fiscal profile, jurisdiction, functional currency and tax ids, is immutable; R4), `version` (ETag).
- **The fiscal profile** (jurisdiction, functional currency, tax ids) is judged by the installed regimes, at
  registration and at every change: the jurisdiction must have a regime (its subdivision's, else its country's;
  422 `jurisdiction_unsupported`), the functional currency must be one the regime lets a company keep its books
  in (`RegimeDef.currency`, or one of its `functional_currencies`; 422 `currency_unsupported`), and every tax id
  scheme must be of the jurisdiction's country unless the regime allows foreign ones (`foreign_tax_ids`; 422
  `tax_id_foreign`). Until the profile is locked it is edited with `PATCH /v1/companies/{id}` and `If-Match`
  (`jurisdiction`, `functional_currency`, `tax_ids` as the whole list: a dropped id is released from the registry,
  an added one is registered, 409 `tax_id_taken` if an active company holds it); once locked the whole profile is
  refused (409 `fiscal_profile_locked`), the legal name still changes. A jurisdiction no installed regime governs
  cannot be registered, so a company's time zone defaults to its regime's.
- **Establishment**: `code`, `name`, `time_zone` (IANA; defaults to the regime's). A document names an
  establishment (default "main"); its time zone fixes the document's tax point date (I11). v1's *emission
  points* are deleted from identity; a channel that needs issuing points models them itself ([05] §4.2).
- **Mode** is a tenancy dimension (ADR 0007): company, establishments and memberships are shared; everything a
  company *does* exists per mode.
- **ISV access** (design only): `company_grants(grantee_organization, company, permissions, expires_at)`; the
  chassis would resolve a grant and set the RLS context to the *owner's* organization ([04] rule 3). The
  `Ledger-Company` header already exists for it.

### 7.2 Parties

One `Party` with roles (`customer`, `supplier`, later `employee`), qualified tax ids, legal name, addresses (R4,
[04] rule 4). Until the `parties` module ships (after go-live), commercial documents carry an inline **party
snapshot** (`{tax_id?, legal_name, address?, email?}`), which they keep forever anyway; later a document may also
name `party: pty_…`.

### 7.3 Qualified tax identifiers

- `TaxId(scheme, value)`; scheme keys `<cc>.<name>` declared by regimes as `TaxIdSchemeDef(key, country, labels,
  normalize, check, check_digit_enforced, peppol_icd)` (ADR 0006). `<cc>` is one assigned ISO 3166-1 country:
  no multi-country scheme groups. A VAT id is issued by one EU member state, so each state's is its own scheme
  (`es.nif`, `fr.tva`, …) declared by that country's regime; `eu.vat` is not a scheme.
- Validation through the regime registry: `invalid_format` refuses; `checksum_mismatch` refuses only where the
  scheme enforces check digits (not `do.rnc`/`do.cedula`, [01] A7) and is otherwise a warning on the response.
- **Global registry** (`identity.tax_registrations`): unique `(scheme, normalized_value)` among active
  companies, across organizations, outside RLS. A held id answers 409 `tax_id_taken`, nothing more (v1 kept).
  The receiver resolves `/do-ecf-v1/{rnc}` through it (`CompanyDirectory`).
- Masking: logs, spans, errors and `repr` show the scheme and the last three characters; internal events carry
  the value in clear; webhooks never do (v1 §9 kept).

### 7.4 Money and currency

`Money(amount: Decimal, currency: Currency)` with v1's exact-arithmetic laws. `Currency` comes from the full ISO
4217 table with minor units (v1's three-value enum is deleted). Rounding only when asked, with the regime's
`RoundingRule`. A document in a currency other than the company's functional currency records the exchange
rate at issue (DGII requires it in the XML), and functional amounts are computed and stored once.

### 7.5 Commercial vs fiscal documents

Two aggregates, two state machines (R4, [01] R6):

```
invoicing.Invoice     draft ──issue──► issued ──void──► voided      (void refused once any channel accepted it)
                       └─ fiscal mirror per channel: {channel → (status, version, authority number, messages)}
                          written only through InvoicingApi.apply_fiscal_status (newer version wins)
channel document      channel-specific state machine (do-ecf-v1: §8.6.4), one per (invoice, channel)
```

- `Invoice.type ∈ {invoice, credit_note, debit_note}`, `direction ∈ {sales, purchase}` (purchase = a
  self-billed document issued on a supplier's behalf; DGII types 41, 43, 47). One resource covers all; v1/M5's
  separate `/v1/do/documents` is gone.
- An accepted document is never voided; a credit note corrects it.
- Lines carry a **resolved tax snapshot**: `{category: "do.itbis.standard", kind, percent, since}` taken from
  the regime at the tax point (rule 12); the category and `since` name the rate in the regime's timeline.
- **Business key**: `external_reference` unique per `(company, mode, type)` forever; a duplicate answers 409
  `external_reference_taken` with `existing_id`.

### 7.6 Enrollments and routing

- `fiscal.Enrollment(enr_…)`: `company`, `mode`, `channel`, `environment` (immutable; one of the channel's
  environments, `live_environments` only in live mode), `status ∈ {pending, active, disabled}`, `requirements =
  {currently_due: [...], disabled_reason}` computed by the channel (Stripe capabilities, [01] R10).
  **Unique among non-disabled enrollments per (company, mode, channel)**; changing environment means disabling
  the enrollment and creating another.
- **Live-mode gate** (C4): a live enrollment becomes `active` only when the channel's requirements are met **and**
  the company's authority authorization is recorded with evidence (for DGII: the company's authorization as an
  electronic emitter through our PSFE, recorded by an operator command with the evidence document archived), and
  the channel's manifest claims at least **Silver** (§8.8). Gold (30 days of nightly runs) is a post-launch goal,
  not a gate.
- **Routing**: channels declare the commercial `document_kinds` and `directions` they cover. An issued invoice
  routes to **every** active enrollment of its company and mode whose channel covers its kind (normally one), so
  invoice → channel documents is **1:N keyed by channel**; `fiscal.routings(invoice, channel, enrollment,
  routed_at)` makes routing idempotent. No active enrollment → the invoice's fiscal mirror stays empty
  (`fiscal: []` on the wire).
- HTTP: `POST /v1/fiscal/enrollments` (`{channel, environment}`), `GET /v1/fiscal/enrollments`, `GET
  /v1/fiscal/enrollments/{id}` (with requirements), `POST /v1/fiscal/enrollments/{id}/disable`; permissions
  `fiscal.enrollments.read` / `fiscal.enrollments.manage` (owner, admin).

### 7.7 Permissions and roles

- Permissions are **open**, declared by modules and channels in manifests. The kernel holds only the `Permission`
  value type; v1's closed enum and identity's hard-coded table are deleted (R4, [05] S1).
- Built-in roles (`owner`, `admin`, `accountant`, `issuer`, `viewer`; five kept, owner ruling) are identity's
  vocabulary; each permission lists the built-in roles that hold it; `ledger-devtools sync` generates identity's
  `ROLE_GRANTS` into `ledger_identity/domain/_role_grants.py` (domain data: it imports the kernel only; built);
  `check` validates role names and fails while the table is stale. identity names only its own codes. `owner` holds every
  permission; `identity.ownership.manage` is `owner` only.
- An organization-level Zitadel project role (`ledger.organization.admin`) registers companies (v1 kept).
- Authorization per request (no cache): `IdentityApi.permissions(call, principal, company)`, one indexed read.
  v1's cache, its invalidation events and the gRPC query service are deleted. No role in a company → 404; a role
  without the permission → 403 (v1 kept). A deactivated company grants only `*.read`: the naming convention
  every manifest keeps is that a permission that only reads has the action `read`, and no other does
  (`only_reads`).
- Custom roles are a later identity feature; the manifest model supports it.

### 7.8 Entitlements (design only)

`plan → features{code, kind: gate|quota, value}` with per-organization overrides, resolved on the `Call`
(`call.entitled("fiscal.channels.do-ecf-v1")`) ([04] rule 33). Not built for go-live.

### 7.9 Accounting (design only, R4)

Immutable journal entries of ≥ 2 postings balanced per currency (debit/credit, positive amounts, functional amount
and rate, dimensions `party`, `establishment`, `cost_center`, `project`); corrections by reversal only;
`effective_date` vs `recorded_at`; closed periods refuse postings; balances derived with optimistic versions;
every entry unique by `(company, mode, source_type, source_id, purpose)`; accounting consumes invoicing and fiscal
events; charts of accounts and country taxonomies are regime data ([04] rules 18-25).

### 7.10 Languages

Regime and channel labels are `Mapping[Locale, str]` (at least `es` for DO, and `en`); problem documents are
English with a stable `code` (clients translate by code); authority messages are passed through verbatim with
their language (`{"code": "...", "message": "...", "lang": "es"}`), never translated or logged.

---

## 8. Localization architecture

### 8.1 Regime vs channel

| | Regime | Channel |
|---|---|---|
| What | Tax law of a jurisdiction | One authority format + transport, one major version |
| Key | `do` (or `us-ca`) | `do-ecf-v1`, later `mx-cfdi-v4`, `co-dian-v1.9`, `eu-peppol-bis3-v1` |
| Content | currency, time zone, tax-id schemes, tax categories with dated rates, document types and correction rules, rounding, law-level fields, dated parameters, sources | builders, vendored schemas + pins, signing profile, gateway, numbering, status map, state machine, contingency, delivery, printed representation, receiver routes, requirements, format-level fields |
| Code shape | pure typed data + pure functions; imports `ledger_kernel` only | layered like a module; imports `ledger_fiscal_kit`, `ledger_platform`, `ledger_kernel` |
| Used by | any module through `Regimes` | `fiscal` through `ChannelRegistry`; the `receiver` app |
| Registry | entry points `ledger.countries.regimes`, loaded by `platform.runtime.registries.Regimes` | `ledger.countries.channels`, loaded by `platform.runtime.packs.Channels` |

A fiscal document records the regime and channel keys it was produced with (ADR 0005, [01] R1).

### 8.2 Regime packs

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class RegimeDef:                                   # ledger_kernel.regimes (pure types)
    key: RegimeKey                                 # "do"
    jurisdiction: Jurisdiction
    currency: Currency
    time_zone: str                                 # default; establishments may override
    labels: Mapping[Locale, str]
    tax_id_schemes: tuple[TaxIdSchemeDef, ...]
    tax_categories: tuple[TaxCategoryDef, ...]     # each with rates: Timeline[RateValue]
    document_types: tuple[DocumentTypeDef, ...]    # code, labels, kind, direction, requires_buyer_tax_id, corrects
    rounding: RoundingRule                         # mode, points (line | category | document), tolerance per line
    fields: tuple[FieldDef, ...]                   # law-level fields under fiscal.<regime>.<field>
    parameters: Mapping[str, Timeline[Decimal | int | str]]   # dated thresholds, e.g. "rfce_threshold"
    sources: tuple[Source, ...]                    # title, url, version, retrieved_on
```

- `RateValue(since, percent, until)`; lookup by tax point in the establishment's time zone; values newest
  first, ordering validated at load ([01] R4).
- **The single tax calculator** `TaxCalculator(regime).totals(lines, tax_point) -> Totals` is used by invoicing
  to compute and by channels to verify (they refuse `totals_disagree` rather than send a different total).
- `Regimes.load()` (in `platform.runtime.registries`) reads entry points once, validates (unique keys, ordered
  timelines, sources present, corrections resolvable) and freezes; a bad pack fails the start.

### 8.3 Channels

```python
class Channel(Protocol):                                   # ledger_fiscal_kit.channels
    definition: ChannelDef      # key, regime, valid_from/until, environments, live_environments,
                                # document_kinds, directions, fields (format-level), labels, sources
    def requirements(self, state: EnrollmentState) -> Requirements: ...
    async def check(self, call: SystemCall, draft: FiscalDraft) -> Sequence[Objection]: ...   # pure, no writes
    async def issue(self, call: SystemCall, draft: FiscalDraft, *, sign_within: timedelta | None) -> Issued: ...
        # idempotent by (draft.document, channel); numbers in one unit; signs synchronously when sign_within
        # is given and the signer answers in time, else leaves signing to the channel's job
    async def resume(self, call: SystemCall, company: CompanyId, reason: ResumeReason) -> None: ...
    async def representation(self, call: SystemCall, document: InvoiceId) -> Representation: ...  # PDF + fields
```

- **Versioned formats**: a channel key names one major format version; minor authority revisions are new
  pinned schema sets inside the channel selected by `valid_from`. Two versions coexist during a transition as two
  channels; the enrollment names one ([01] B3). DGII requires PSFE re-certification per format version ([06] §6).
- **State machines are per channel** and explicit (an enum of states, a transition table, a `transition()`
  that refuses anything else); the kit never generalizes an authority's states ([01] B6.1).
- **Status mapping tables**: every authority code maps to `(channel_state, FiscalStatus, severity)` in one table
  per channel; a test fails if a code in the vendored documentation is unmapped.
- **Numbering** goes through the kit's `NumberingPolicy` port (§8.4); only DO's authorized-ranges policy is
  implemented.
- **Severity drives retry**: `info | warning` retried by the job; `error` parks the document until a person acts
  ([01] A2).

### 8.4 `fiscal-kit` (`packages/fiscal-kit`, `ledger_fiscal_kit`): the minimal contract

Rule of two: the kit holds only what the `fiscal` module and a channel must agree on. Numbering allocation,
submission loops, circuit breaker and the conformance harness stay in `countries/do` until a second channel
needs them, and then move here with both channels as tests.

| Module | Public surface |
|---|---|
| `channels` | `Channel`, `ChannelDef`, `ChannelRegistry`, `Requirements`, `Objection`, `Issued`, `Representation`, `ResumeReason`, `EnrollmentState` |
| `drafts` | `FiscalDraft` (seller snapshot, establishment and its time zone, buyer snapshot, lines with resolved tax, totals, currency + rate, references, `fields` under the regime namespace, issue date, tax point, mode, environment) |
| `status` | `FiscalStatus` (`pending`, `accepted`, `accepted_with_observations`, `rejected`, `voided`), `Severity`, `StatusMap[Code]` (with an exhaustiveness check) |
| `numbering` | **`NumberingPolicy` port** only: `allocate(uow, draft) -> FiscalNumber`; two documented shapes — *authorized ranges* (authority-granted ranges with expiry and an optional opaque artifact, e.g. Chile's signed folio-authorization file kept as bytes) and *free series* (series per establishment or issuing point, e.g. Mexico's folios) |
| `ports` | `SignerPort` (sign XML, sign seed; implemented by the signer module's client), `CompanyDirectory` (company by tax id; implemented by `fiscal` over `IdentityApi`), `ArchivePort` (implemented over `platform.blobs`) |
| `xml` | hardened parser (no entities, DTDs, network; size limits), `SchemaSet` (vendored XSDs, SHA-256 pins, load-time corrections pinned by count), C14N helpers |
| `events` | kit-owned event types every channel publishes: `fiscal_channel.document.updated` (invoice id, channel, state, `FiscalStatus`, version, authority number, authority messages ≤ 20 × 500 chars) and `fiscal_channel.received_document.recorded` |

As built in v2-M2b (ahead of v2-M3): the kit started minimal with `ports.SignerPort` and `xml` (`xml.hardened`,
`xml.dsig`, moved from the DO channel). `SignerPort` signs a document (`XmlToSign`: tenant with its mode,
profile, document, the `signed_at` it states, a reference) or a seed (`SeedToSign`: tenant, profile, seed,
audience), answers `Signed` (the document and the certificate's id) and reads `certificate_validity`; it raises
five errors only (`SigningCertificateNotValidError`, `SigningClockSkewError`, `SigningCertificateMissingError`,
`SignatureRefusedError`, `SignerUnavailableError`). This is the DGII spike's integration checklist item 2 as
built: the channel's `XmlSigner` became `SignerPort`, the signing half moved to `modules/signer`, and
verification, trust, certificates and names stay in the channel.

### 8.5 How a document flows (any country)

```
POST /v1/invoices (api)
 1 invoicing: validate; TaxCalculator(regime) totals
 2 IssuePolicy.check(document)         fiscal: enrollments → FiscalDraft → channel.check     (no unit open)
     objections → 422 invalid_document, nothing stored
 3 UoW(invoicing): store issued + publish invoicing.invoice.issued; commit
 4 IssuePolicy.after_issue(document, budget=1.5 s)                                          (no unit open)
     fiscal: route (fiscal.routings) → channel.issue(draft, sign_within=remaining budget)
       channel UoW: allocate the number under lock, store, enqueue <channel>.advance, publish
                    fiscal_channel.document.updated (numbered)                    → commit
       sign via SignerPort, archive, record (signed) in a second unit             → commit, if within budget
     fiscal → InvoicingApi.apply_fiscal_status(...)  (numbered/signed, with number, security code, QR data)
 5 201 with fiscal[] (number, security code, QR data when signed in time; status pending)
   if step 4 fails or exceeds its budget: 201 with fiscal[] empty or `numbered`; the recovery path finishes it

worker, recovery path: consumer fiscal.commercial_documents (invoicing.invoice.issued) → the same routing and
       channel.issue, idempotent by (invoice, channel)
worker: <channel>.advance job → sign (if not yet) → submit → poll → deliver to buyer …  (RunAgain/Park/Done)
       each state change publishes fiscal_channel.document.updated
worker: fiscal.channel_documents consumer (fiscal_channel.document.updated)
       → InvoicingApi.apply_fiscal_status(call, invoice, status, version)   (newer version wins)
       → invoicing publishes invoicing.invoice.fiscal_updated (public: invoice.fiscal_updated)
worker: webhooks.public_events consumer → delivery jobs → client endpoint
```

The synchronous path (I10) lets a POS print the document with its e-NCF, security code and QR the moment the
`POST` returns; the consumer of `invoicing.invoice.issued` makes the outcome independent of that path.

### 8.6 The Dominican pack: regime `do` and channel `do-ecf-v1`

#### 8.6.1 Layout

```
countries/do/
├── manifest.toml
├── pyproject.toml                         GENERATED
├── README.md
├── docs/{sources.md, certification/, runbooks/}
├── src/ledger_country_do/
│   ├── regime/                            imports ledger_kernel only (contract 3)
│   │   ├── __init__.py                    REGIME: RegimeDef
│   │   ├── tax_ids.py                     do.rnc, do.cedula; check digit reported, not enforced
│   │   ├── taxes.py                       ITBIS 18 %, 16 %, 0 %, exempt (ISC, legal tip later): dated
│   │   ├── document_types.py              31, 32, 33, 34, 41, 43, 44, 45, 46, 47
│   │   ├── corrections.py                 33/34 → originals; modification codes incl. 4 (replaces contingency NCF)
│   │   ├── parameters.py                  rfce_threshold, contingency windows, legal name limit
│   │   └── fields.py                      law-level fields under fiscal.do.* (ecf_type, income_type, ...)
│   ├── channels/ecf_v1/
│   │   ├── __init__.py                    CHANNEL
│   │   ├── events.py                      channel-only events (range low/expiring)
│   │   ├── assembly.py
│   │   ├── domain/                        ecf.py (state machine), types/<one module per type>.py, numbering.py
│   │   │                                  (authorized ranges, released numbers), voids.py, delivery.py,
│   │   │                                  received.py, approvals.py, contingency.py, status_map.py,
│   │   │                                  security_code.py, fields.py (format-level fiscal.do.* fields)
│   │   ├── application/                   issuing.py, advancing.py, delivering.py, voiding.py, receiving.py,
│   │   │                                  approving.py, ranges.py, representation.py, queries.py, ports.py
│   │   └── adapters/
│   │       ├── xml/                       writer, names (read from the XSDs), builders: ecf, rfce, anecf, acecf, arecf
│   │       ├── xsd/                       DGII's XSDs byte for byte + published.py (URL, SHA-256, corrections)
│   │       ├── dgii/                      endpoints.py, client.py, tokens.py, breaker.py, directory.py
│   │       ├── buyers/                    client for buyers' receivers (seed auth, e-CF send, ARECF check)
│   │       ├── pdf/                       printed representation renderer (QR, security code, legends)
│   │       ├── postgres/                  tables.py, repositories/, migrations/ (schema do_ecf)
│   │       ├── http/                      public routes /v1/do/... (api app)
│   │       └── receiver/                  /do-ecf-v1/{rnc}/fe/... (receiver app)
└── tests/{unit, integration, contract}/   golden/ (one XML per type, RFCE, ANECF, ACECF, ARECF)

countries/do/testing/                      ledger-country-do-testing (dev-only distribution)
└── src/ledger_country_do_testing/         fake_dgii/ (ledger-fake-dgii: DGII's services and a buyer receiver,
                                           written from DGII's documentation), samples
```

#### 8.6.2 Regime `do` (data)

| Item | Value (each with a `Source`) |
|---|---|
| Currency, time zone | DOP; `America/Santo_Domingo` |
| Tax-id schemes | `do.rnc` (9 digits, weights 7 9 8 6 5 4 3 2), `do.cedula` (11 digits, Luhn); `check_digit_enforced = false` |
| Tax categories | `do.itbis.standard` 18 %, `do.itbis.reduced` 16 %, `do.itbis.zero` 0 % (exports; DGII "ITBIS 3"), `do.itbis.exempt`; generic UBL category mapping (`standard`, `reduced`, `zero`, `exempt`, `outside_scope`) kept from M5 decision 3 |
| Document types | 31 crédito fiscal, 32 consumo, 33 nota de débito, 34 nota de crédito, 41 compras, 43 gastos menores, 44 regímenes especiales, 45 gubernamental, 46 exportaciones, 47 pagos al exterior; kind, direction, buyer-tax-id rule, corrections |
| Rounding | half up, two places at DGII's three points; unit prices to four places; tolerance ±1 per line and globally as many units as lines (IT §12; beyond it DGII answers *Aceptado Condicional*) |
| Parameters (dated) | `rfce_threshold` DOP 250,000 (type 32 below it goes as RFCE; from it, in full); `contingency_connectivity_window` 72 h; `contingency_paper_max` 15 calendar days; `contingency_replacement_window` 30 calendar days; `legal_name_max` 150; type-32-RFCE line limit 10,000, others 1,000 |
| Default document type | invoice → 31 if the buyer has a tax id, else 32; credit note → 34; debit note → 33; purchase → `fiscal.do.ecf_type` required (41, 43 or 47); 44/45/46 by explicit `fiscal.do.ecf_type` |

#### 8.6.3 Channel `do-ecf-v1`: DGII endpoints and client rules ([06] quick reference)

- Environments `testecf`, `certecf` (test mode), `ecf` (live only). Auth: `GET
  https://ecf.dgii.gov.do/{env}/autenticacion/api/autenticacion/semilla`, sign the seed (`SignSeed`), `POST
  …/validarsemilla` → token (1 h), cached **per replica** in memory per company × environment until 5 min before
  expiry; one retry on 401.
- Submit: `POST https://ecf.dgii.gov.do/{env}/recepcion/api/facturaselectronicas` → `{trackId, error,
  mensaje}`. Result: `GET …/consultaresultado/api/consultas/estado?trackid=`. Track ids by e-NCF: `GET
  …/consultatrackids/api/trackids/consulta?rncemisor=&encf=` (not in `certecf`). RFCE: `POST
  https://fc.dgii.gov.do/{env}/recepcionfc/api/recepcion/ecf` (synchronous, no TrackId). Commercial approval to
  DGII: `POST …/aprobacioncomercial/api/aprobacioncomercial`. Range voids: `POST
  …/anulacionrangos/api/operaciones/anularrango` (not in `certecf`). Directory: `GET
  …/consultadirectorio/api/consultas/obtenerdirectorioporrnc?RNC=`. QR: `…/consultatimbre?…`; RFCE QR:
  `https://fc.dgii.gov.do/{env}/consultatimbrefc?…`.
- Live mode refuses any host but DGII's (M4 decision 12). Retries only for calls that cannot change DGII's state;
  a submission is never retried blindly: a connection broken after the request may have left records
  `outcome_unknown`, and the next step asks for the e-NCF's track ids first (M4 decision 15).
- Circuit breaker per environment (5 failures; 30 s doubling to 10 min); its closing time is recorded per
  environment as `connectivity_restored_at` (the contingency clock, §8.6.6).
- Numbering: e-NCF = `E` + 2-digit type + 10-digit sequence, from client-registered DGII authorizations
  (`POST /v1/do/numbering_ranges`); dated except 32 and 34 (M4 decision 13); TesteCF ranges 1-10,000,000 (type 32
  to 50,000,000) expiring 2028-12-31. Allocation takes a **released number** of the same type first (below),
  else the range's next number, under the range's row lock in the unit that records the document.
- **Number accounting invariant**: every number in `[first, next)` is exactly one of: used by a non-rejected
  document; burned by a rejection with `secuenciaUtilizada = true`; released (rejection with
  `secuenciaUtilizada = false`, [06] §1, reusable by the next allocation); voided by an accepted ANECF. A daily
  check proves it.

#### 8.6.4 e-CF state machine

```
                    ┌──────────── Park(certificate missing/expired; signer unavailable) ──────────┐
                    ▼                                                                             │
 numbered ──sign──► signed ──submit──► submitted ──poll──► in_process ──poll──┐                   │
   │  │   (XSD-valid, signed, archived,  │ (trackId)                          ▼                   │
   │  │    security code, emission_mode) │                     accepted | accepted_conditionally │ rejected
   │  │                                  │                          │                            │
   │  │                                  ├─(32 < threshold)─► RFCE: accepted | accepted_conditionally | rejected (sync)
   │  │                                  └─(broken after send)─► outcome_unknown ─(track ids)─► submitted | signed
   │  └─ blocked (error severity) ─ operator ─► numbered
   └──────────────── ANECF accepted ──────────────► voided        (from numbered or blocked only)

 accepted | accepted_conditionally ──► delivering sub-flow (§8.6.5) ──► delivered | delivery_not_applicable
 rejected: number burned or released per secuenciaUtilizada; the client corrects with a new document
```

Status map (DGII `codigo` → state, `FiscalStatus`, severity):

| Code | DGII | State | FiscalStatus | Notes |
|---|---|---|---|---|
| 1 | Aceptado | `accepted` | `accepted` | valid for tax purposes |
| 4 | Aceptado Condicional | `accepted_conditionally` | `accepted_with_observations` | **valid**; delivered like an accepted one; `mensajes` surfaced as warnings to fix in future documents, no re-issue ([06] §1) |
| 2 | Rechazado | `rejected` | `rejected` | `secuenciaUtilizada` decides burned vs released |
| 3 | En Proceso | `in_process` | `pending` | poll with backoff (DGII averages ~200 ms) |
| 0 | No encontrado | `outcome_unknown` | `pending` | resolve with `consultatrackids` |

Polling `RunAgain`: 1 s, 2 s, 5 s, 10 s, 30 s, 1 min, then every 5 min; DGII down: `RunAgain` at the breaker's
reopen; certificate or signer problems: `Park`, woken by `fiscal` on `signer.certificate.installed`
(`Channel.resume`). Job deadline 15 days; alerts long before (§11).

#### 8.6.5 Buyer delivery (`delivering` sub-flow) and commercial approval

DGII requires the issuer to send an accepted e-CF to an electronic receiver ([06] §1, §4):

1. **Applicable** when the document is accepted or accepted conditionally, is not an RFCE-summarized type 32,
   and the buyer has an RNC/cédula. Otherwise `delivery_not_applicable` (the client hands the printed
   representation to a non-electronic buyer).
2. **Directory lookup**: `obtenerdirectorioporrnc?RNC=` (cached 24 h per RNC, negative answers 1 h). No entry →
   `delivery_not_applicable` (buyer is not an electronic receiver).
3. **Auth** when the buyer publishes `urlOpcional`: GET the buyer's seed, sign it (`SignSeed` with the issuing
   company's certificate), POST to `validacioncertificado`, use the bearer token.
4. **Send** the signed e-CF (multipart `xml`) to `urlRecepcion`; expect a **signed ARECF**; verify its signature;
   archive it; state `delivered`. Failures retry on the delivery job's own backoff (1 min to 6 h, deadline 7 days);
   then `delivery_failed` (alert; the client may resend through `POST /v1/do/deliveries/{inv}/retry`).
5. **Commercial approval**: the buyer may later POST an ACECF to our receiver (§3.8); it is recorded
   (`approved` / `rejected` with reason) and published (public `invoice.fiscal_updated`).
6. **Our companies as buyers** (§3.8): received e-CF are recorded; the client approves or rejects commercially
   (`POST /v1/do/received_documents/{id}/approval`), which enqueues an ACECF to the emitter's `urlAceptacion` and
   to DGII (DGII accepts approvals only for accepted e-CF).

#### 8.6.6 Contingency ([06] §3, Decreto 587-24 Arts. 39-43)

| Case | Trigger | What changes | Clock and states |
|---|---|---|---|
| **Connectivity loss** (we can generate but not send) | the submission step finds the DGII breaker open or the network failing at the time the document is **signed** | `emission_mode = contingency` recorded at signing (immutable); the XML is unchanged; the printed representation carries the legend "e-CF emitido en modalidad de Contingencia" | submit ≤ 72 h after `connectivity_restored_at` of the environment; state stays `signed` until submitted; alert at 50 % and 90 % of the window |
| **DGII down** | breaker open because DGII fails | store and forward (normal e-NCF) | `RunAgain` at the breaker's reopen; alert when DGII has been down > 1 business day (Art. 43 allows OFV book upload after 15 business days) |
| **Client cannot issue e-CF at all** (paper NCF, B-series) | the client declares the contingency in DGII's OFV and issues paper NCF | after it, the client issues each **replacement e-CF** through us with `fiscal.do.replaces_ncf: {ncf, date}` → modification code 4 with the replaced NCF and its date in the reference block (FMT); sent **only to DGII** (no buyer delivery) | contingency ≤ 15 calendar days; replacements ≤ 30 calendar days after it ends (validated against `contingency_*` parameters); built in v2-M4 |
| **Envío diferido** (DGII-authorized deferred sending, 24 h) | — | not offered at launch | — |

#### 8.6.7 Printed representation (representación impresa)

A platform feature of the channel (C5): `GET /v1/invoices/{id}/representation?channel=do-ecf-v1` returns a PDF
rendered from the signed document: issuer and buyer data, lines and totals, e-NCF, signature date, **security
code** (first six characters of the `SignatureValue`, M4 decision 14 reading), **QR** pointing to DGII's
`consultatimbre` (or `consultatimbrefc` for RFCE, QR version 8) with DGII's parameters, and the contingency legend
when applicable; legible for 10 years (IT §18.1); archived on first render. Every field it uses is also in the
invoice's `fiscal[].do` object (§9.8), so clients may render their own. The PDF library is chosen in the M4 plan
after verifying CPython 3.15 wheels.

#### 8.6.8 Public DO resources

`/v1/do/numbering_ranges`, `/v1/do/range_voids`, `/v1/do/received_documents` (+ `/approval`),
`/v1/do/deliveries/{invoice}/retry`. Everything else of a DO e-CF is in the invoice's `fiscal[]` entry for
`do-ecf-v1`.

### 8.7 Tests for regimes and channels

- **Regime tests** (DO now, a shared `RegimeConformance` when a second regime exists): timelines ordered and
  non-overlapping; every value has a source; corrections resolvable; calculator laws (total = sum of parts;
  independent of line order; rounding at the declared points) as hypothesis properties; tax-id properties and
  the known checksum-failing-but-valid samples.
- **Channel tests** (in `countries/do` until the rule of two moves a harness into the kit): golden draft →
  document for every type (canonicalized, byte-compared) validated against the pinned XSDs with libxml2; schema
  fingerprint pins; every DGII code mapped; numbering properties (no duplicate, no silent gap, released numbers
  reused, under concurrency); state-machine exhaustiveness; signer round trip verified by xmlsec1; job tests against
  the fake DGII (outages, `En Proceso` loops, conditional acceptance, unknown outcomes, `secuenciaUtilizada`
  both ways, buyer receiver with and without auth); masking. Hypothesis-generated documents and the second XSD
  validator (xmlschema) are deferred (§16.4).
- **Certification scripts** (opt-in, never CI): DGII's certification steps on `certecf`, evidence committed under
  `countries/do/docs/certification/`.

### 8.8 Quality tiers (per channel)

| Tier | Requires |
|---|---|
| **Bronze** | regime complete for the channel's scope; channel tests green; every document type golden and schema-valid |
| **Silver** | end to end in the authority's test environment (emission, RFCE, reception, delivery, approvals, voids, printed representation); contingency and unknown-outcome paths tested; runbooks exist. **Required for live mode** |
| **Gold** | two owners; nightly run against the test environment green for 30 days. Post-launch goal |

The tier is a manifest claim checked in review; the automated rule catalogue and its `check` are deferred (§16.4).

---

## 9. Public API conventions

### 9.1 Shape

REST + JSON, OpenAPI 3.1 generated from code (one document assembled from every mounted module's and channel's
fragment), published, and snapshotted per package (`openapi.json`, contract test). Strict request schemas
(unknown fields rejected); clients must ignore unknown response fields (stated in the compatibility contract).

### 9.2 Versioning: path major + dated versions

- `/v1` in the path is the major (ADR 0008).
- **Dated versions** inside it: `Ledger-Version: YYYY-MM-DD`. Organizations and API clients are **pinned** to the
  current version at creation. Precedence: the request header, else the API client's pin, else the
  organization's pin. The response echoes the resolved `Ledger-Version`; an unknown date is 400
  `version_unknown`.
- **When a pin is set.** An organization is pinned when identity first keeps it, which is a write: its first
  company or its first API client, to the version current then. Before that identity knows nothing of it, and
  its requests speak what they name, else the current version (the same date the pin then takes). How a caller
  stands (its pins, its API client) is remembered per replica 30 s, an answer that knows nothing 5 s (so a new
  organization's pin, or a new API client, counts within 5 s), with one question at a time per caller; identity
  answers it in one transaction of the caller's organization.
- Built now: header parsing, pin storage, precedence and echo. The transform machinery (`VersionChange`
  backward response / forward request transforms, generated changelog) is built with the first breaking change
  (§16.4). Cadence then: monthly non-breaking versions, at most two breaking releases a year, each change reviewed
  in writing ([04] rule 8).
- Webhook payloads are thin and unversioned (§9.9), so versions never break receivers.

### 9.3 Envelope

Every resource: `id` (TypeID), `object` (`"invoice"`), `livemode` (bool), `created`, `updated` (ISO 8601 UTC),
`metadata` (mutable objects only: ≤ 50 keys, key ≤ 40 chars, value ≤ 500 chars, strings only, never indexed,
never PII by contract). Polymorphism by `type` + a sub-object named after it ([04] Stripe). Optional heavy fields
by `include[]`, never recursive `expand` ([04] rule 13).

### 9.4 Ids

Public ids are TypeIDs (§6.6); a path parameter with the wrong prefix is a 404 `not_found` (not a 422), so ids
cannot be probed by type. Internal UUIDs never appear in the API.

### 9.5 Idempotency and business keys

- `Idempotency-Key` required on every `POST`, ≤ 255 chars, scoped to (organization, mode,
  principal, **route**), stored **7 days** with the response in the owning module's `idempotency_keys` table in the
  same transaction as the work (v1 semantics: a failed request keeps nothing; a concurrent twin waits and replays
  with `Idempotent-Replayed: true`; same key with another body → 422 `idempotency_key_reused`).
  **The company is not part of the scope** (as built, invoicing review M2): a request's `Ledger-Company` is part
  of what it fingerprints, so the same key used for another company's request is 422 `idempotency_key_reused`.
  An integrator acting for several companies uses a key per company (the header's OpenAPI description says so).
- What "the same request" is: the route, the concrete path, the canonical query and the canonical body, each
  length-prefixed. The canonical query is the `(name, value)` pairs **sorted**, so parameters sent in another
  order are the same request, and so are the values of a repeated name sent in another order (`?tag=a&tag=b`
  equals `?tag=b&tag=a`): a route that reads a repeated parameter as an *ordered* list must not take an
  `Idempotency-Key` (none does today; `POST`s carry their data in the body), or must put the order in its body.
  The canonical body is the validated model's JSON, keys sorted, defaults included.
- A route that takes `Idempotency-Key` requires `Ledger-Mode` (400 `mode_required` without it): the key is kept
  per mode (Decision 4 of the v2-M1 plan).
- `DELETE` takes no `Idempotency-Key`: it is naturally idempotent (deleting or revoking what is gone already
  answers as before and changes nothing), so a retry needs no stored response (as built: revoking an API
  client, revoking a membership).
- Routes that forward to the signer (`POST /v1/certificates`) pass the key through `ImportCertificate`; the signer
  stores it in `signer.idempotency_keys`, in the transaction that installs the certificate.
- `external_reference` (§7.5) is the permanent business key for documents.

### 9.6 Errors (RFC 9457, kept)

`application/problem+json` with `type` (`urn:ledger:error:<code>`), `title`, `status`, `code`, `detail`,
`request_id` (also the `Request-Id` header), and extension members per code (`errors[]` ≤ 20 for
`invalid_request`, each `{location, type, message}`; `errors[]` ≤ 20 for `invalid_document` (the
`IssuePolicy` chain's objections, M3b), each `{code, field, message}`, `field` a path or `null`; both with
`omitted_errors`, the count left out; `authority_messages[]` (`{code, message, lang}`, verbatim) for fiscal rejections, `existing_id`
for business-key conflicts). Problems are English; clients translate by `code` (§7.10). One kind → status table
shared by HTTP and gRPC:

| Kind | HTTP | gRPC |
|---|---|---|
| `INVALID` | 422 | `INVALID_ARGUMENT` |
| `NOT_FOUND` | 404 | `NOT_FOUND` |
| `CONFLICT` | 409 (412 for a stale `If-Match`, 428 without it) | `FAILED_PRECONDITION` |
| `UNAUTHENTICATED` | 401 + `WWW-Authenticate: Bearer` | `UNAUTHENTICATED` |
| `FORBIDDEN` | 403 | `PERMISSION_DENIED` |
| `UNAVAILABLE` | 503 (+ `Retry-After` when known) | `UNAVAILABLE` |
| `INTERNAL` | 500, code `internal`, nothing else | `INTERNAL`, code `internal` |

Error codes are part of the versioned contract and unique across the repo (`ledger-devtools check`); the v1
README/spec copies of the table are deleted ([05] D36).

### 9.7 Tenancy on the wire, pagination, concurrency

- Company-scoped resources take the company in the header `Ledger-Company: cmp_…` (the ISV seam, §7.1);
  `/v1/companies/{id}` takes it in the path. Mode comes from the credential (API clients) or, for human tokens,
  the **required** header `Ledger-Mode: test|live` (400 `mode_required` without it on moded routes; owner ruling).
- **How a caller stands is the chassis's, by default.** Every route declares the one caller a module is given
  (`ModuleContext.auth.caller`); the token-only caller is the chassis's own and stays private. Once the token is
  proven it asks the platform's `StandingsPort`, which identity provides (`[extension_points] provides =
  ["StandingsPort"]`) and the app binds once after assembly: a revoked API client is refused (401
  `api_client_revoked`, within 30 s: each replica remembers a standing that long), a machine that is no API client too (403 `api_client_unregistered`), the client's mode
  becomes the request's (`ledger_mode_of(caller)` depends on the caller, so the order a route declares them in
  never matters) and the pins settle the version. An app in which no module provides the port knows no API
  client: it refuses every machine (fail closed). A module opts into nothing: invoicing's routes get all of it.
- Cursor pagination (`limit` 1-100, opaque `starting_after`/`ending_before` cursors, one at a time), `has_more`
  (in the direction read); one generic `Page[T]` model ([05] D23): `items` in id order, `has_more`,
  `next_cursor` (for `starting_after`, `null` when nothing follows) and `previous_cursor` (for `ending_before`,
  `null` on the first page and whenever nothing comes before).
- `ETag` = resource version; `If-Match` required on updates (428 without, 412 stale), except idempotent set-value
  `PUT`s (v1 kept).
- Amounts as strings; dates ISO 8601; country codes ISO 3166-1 alpha-2 upper case; currencies ISO 4217.

### 9.8 Country rules as data, country fields namespaced

- `GET /v1/countries` and `GET /v1/countries/{cc}` publish each regime and its channels as data: currencies,
  time zone, tax-id schemes, tax categories with dated rates, document types, rounding, fields, channel versions,
  environments, document kinds and requirements ([04] rule 12). Clients build forms from it.
- **Country request fields** live under `fiscal.<regime>.<field>` (I12): `"fiscal": {"do": {"ecf_type": "44",
  "income_type": "01", "replaces_ncf": {"ncf": "B0100000123", "date": "2026-11-20"}}}`. The **regime** declares
  law-level fields (e.g. `ecf_type`: the document type is law); each **channel** declares its format-level fields
  in the same regime namespace (e.g. `income_type`, `payment_type`). A request is validated against the regime
  and the channels of the company's active enrollments for the mode; a key none of them declares is 422
  `field_unknown`; a channel ignores another channel's fields.
- **Country response fields** live in the per-channel fiscal entries: `invoice.fiscal = [{"channel":
  "do-ecf-v1", "environment": "testecf", "status": "accepted_with_observations", "version": 7, "messages":
  [{"code": "…", "message": "…", "lang": "es"}], "do": {"encf": "E310000000001", "security_code": "A1b2C3",
  "signed_at": "…", "qr_url": "…", "track_id": "…", "emission_mode": "normal", "delivery": "delivered",
  "commercial_approval": null}}]`. The generic invoice never learns DGII's words; every field the printed
  representation uses is present (§8.6.7).
- Line taxes: `taxes: [{"category": "do.itbis.standard", "percent": "18", "amount": "…"}]`; party tax ids:
  `{"scheme": "do.rnc", "value": "…"}`.

### 9.9 Webhooks (Standard Webhooks, minimal)

- **Consumption**: the webhooks module consumes **generic envelopes** (`RawEvent`), never imports another
  module's event classes. The public mapping is declared next to each event in its producer's manifest
  (`public = "<resource>.<verb>"`); `ledger-devtools sync` turns it into webhooks' subscription list and an
  `event type → public name` table, and each publishing package registers a small **public mapper**
  (`object type, object id` from the envelope's `subject` and `type`) so payloads stay thin without importing
  types. Webhooks is a sink: these consumption edges are acyclic by construction.
- Endpoints per company and mode (`hook_`), sealed secrets (`whsec_` shown once; AES-256-GCM under a keyring, M5
  decision 12). Event filters and secret rotation with overlap are deferred (§16.4).
- Wire format: [Standard Webhooks](https://www.standardwebhooks.com/) `webhook-id`, `webhook-timestamp`,
  `webhook-signature: v1,<base64 HMAC-SHA256("id.timestamp.body")>`; signed per attempt.
- **Thin payloads**: `{id: "evt_…", type: "invoice.fiscal_updated", created, livemode, data: {object: {id,
  object}, changes?: [field names]}}`; never lines, parties or tax ids. Receivers fetch the object.
- At least once, no ordering, dedupe by `webhook-id`; retries for 3 days (1 min, 5 min, 30 min, 2 h, 6 h, then
  every 12 h); deliveries are jobs; SSRF defence at registration and at every attempt with pinned resolution (M5
  decision 13 kept).
- Public events (`<resource>.<verb>`): `invoice.issued`, `invoice.voided`, `invoice.fiscal_updated`,
  `received_document.created`, `certificate.expiring`, `numbering_range.low`, `numbering_range.expiring`,
  `enrollment.updated`, `webhook.tested`.
- A public events API (`GET /v1/events`, 30 days) is designed, built after go-live (R4).

---

## 10. Security

v1 §9 holds except where noted.

| Concern | Measure |
|---|---|
| Identity provider | Zitadel v4, unmodified (AGPL network use). Token verification generic (issuer, audience, asymmetric algorithms only, leeway, RFC 9068 access-token shape); provider claims only in `auth.providers.zitadel`: organization from `urn:zitadel:iam:user:resourceowner:id`, roles only from the audience project's claim (v1 kept) |
| API credentials | Zitadel machine users (private-key JWT or client credentials) registered in `identity.api_clients` with organization, mode and pinned version. A principal is a person or a machine (`Principal.kind`). The signed claim `urn:ledger:principal_kind` (`human`/`machine`) decides where a token carries it: a v1 Action of the organization's Complement Token flow, trigger Pre access token creation, sets it from the user's type (`deploy/local/zitadel/bootstrap.sh` installs it locally; a **deployment step** in every other environment), a token whose claim contradicts its shape or names no kind is refused (401 `principal_kind`), and `LEDGER_TOKEN_KIND_CLAIM_REQUIRED` makes it mandatory, which `Settings` requires in staging and production. Without the claim the shape decides (checked against Zitadel v4.19.2): a token whose `client_id` is one of its audiences, other than the project itself, was issued to an application a person signed in through; a machine user's own credentials name the machine as `client_id` and no application. A machine acts only as an active API client of its organization (403 `api_client_unregistered` otherwise, 401 `api_client_revoked` once revoked). Whoever an API client names is bound to its mode and revocation whatever its token's kind: a person registered by mistake is held to that client until it is revoked (accepted), and a machine passing for a person gains nothing; registering refuses a subject known to be a person (the registering person, 422 `api_client_human`) |
| Certificate custody | §3.7: signer process only. `local` backend: KMS envelope encryption, requires our INDOTEL authorization; `remote` backend: keys stay with an INDOTEL-authorized certification entity (Decreto 587-24 Art. 22, [06] §6). As built (v2-M2b), a certificate is admitted at import if its key is RSA of 2048 bits or more, it is a signing certificate (digital-signature key usage, no authority) of the key it came with, and it is valid now and for at least the signing skew; a stopgap refuses one its issuer signed with anything but a SHA-2 hash (it looks at the one certificate only, until path validation replaces it). Deferred: the subject matching the company (M3 decision 1; v2-M3b, the channel's requirements), the chain to an accepted DR certification authority and revocation checks (v2-M5). The signer does not check that a document it signs names the certificate's company (ADR 0010, accepted risk 9) |
| Signer data | the signer's **business tables** are reachable only by `ledger_signer`; its `outbox` and `inbox` are also reachable by `ledger_relay` (fan-out); `api` reaches the signer only through gRPC with a per-caller token and method allowlist |
| Secrets | from the cloud secret manager (External Secrets Operator); never in images, repos, `repr`, logs or errors |
| Telemetry leaks | v1 rules kept verbatim: excerpts only, exception type only on spans, no locals in tracebacks, DB/validation errors logged by SQLSTATE/locations, never values |
| Tax ids | clear inside the database and internal events; masked in logs, spans, errors, `repr`; never in webhooks |
| Transport | TLS 1.3 at the edge; DGII's and buyers' certificates verified; mTLS inside with the mesh later (§16.4) |
| Public API | OAuth 2.0 only; idempotency; strict schemas; request size limits in the app (1 MiB JSON, 2 MiB for a certificate upload) and at the ingress |
| Rate limits | per credential at the ingress; GCRA in process per replica as a backstop |
| Receiver | separate host name; per-RNC and per-IP rate limits; seeds single-use and short-lived; tokens hashed at rest; size limit before parsing; hardened XML parser; sender signature verified before anything but the raw archive copy is stored |
| Outbound | DGII hosts allowlisted per environment; buyers' receiver URLs only from DGII's directory, `https` only, resolved and checked public at every attempt (SSRF), timeouts on every call |
| XML | lxml with entity resolution, DTDs and network disabled; size limits before parsing |
| Database | per-module roles (§6.2); no superuser/BYPASSRLS anywhere |
| Supply chain | uv lock with hashes, Renovate, image signing (cosign), SBOM, vulnerability scan, pip-audit |
| Audit | `audit_events` in every schema (§6.4), append-only: signatures, certificate changes, membership and API client changes, enrollments, range registrations and voids, operator replays and wakes |
| Compliance | Decreto 587-24 Art. 31 PSFE duties (confidentiality, secure storage of data and certificates, SLAs, help desk, audit log, backups) and DOC-PSFE annexes (security, BCP, Ley 172-13 data-protection policy) tracked as a v2-M5 checklist ([06] §5) |

---

## 11. Observability and operations

- **Traces**: OpenTelemetry; HTTP, gRPC (signer), SQL, DGII and buyer calls, jobs and consumers. The trace crosses
  the outbox and the job queue: rows store the `traceparent` of the span that wrote them; a consumer or job span
  continues it (links when one job serves many events). At the public edge a caller's `traceparent` is
  **linked, never adopted**: each request starts a trace of its own (its id is the `Request-Id` every traced
  response carries, so a client cannot choose it or share it between requests) with a link to the caller's;
  an app behind a trusted edge may adopt it instead. Exporters drop exception messages, stack traces, status
  descriptions and raw paths (v1 kept).
- **Metrics** (Prometheus, `ledger_` prefix, no tenant labels): HTTP and gRPC RED by route template;
  `ledger_outbox_pending{schema}`, `ledger_outbox_oldest_seconds{schema}`, `ledger_outbox_unrouted{schema}`;
  `ledger_inbox_pending{consumer}`, `ledger_inbox_dead{consumer}`; `ledger_jobs_ready{kind}`,
  `ledger_jobs_oldest_ready_seconds{kind}`, `ledger_jobs_parked{kind,reason}`, `ledger_jobs_dead{kind}`,
  `ledger_job_lease_lost_total{kind}`; `ledger_fiscal_documents{channel,state}` (periodic count),
  `ledger_authority_requests_seconds{channel,environment,operation,outcome}`,
  `ledger_authority_breaker_open{channel,environment}`, `ledger_contingency_oldest_seconds{channel,environment}`;
  `ledger_certificates_expiring{days_bucket}`; pool usage. Queue gauges read tables: aggregate with `max`.
- **Logs**: structlog JSON; app, environment, request id, trace/span ids, tenant (`organization_id`,
  `company_id`, `mode`); never personal data beyond ids, never XML, keys or tokens.
- **Health**: every app serves `/livez`, `/readyz`, `/metrics` (signer: HTTP probes on a private port). Readiness:
  database (own connection), schema at head, LISTEN connection (worker), the app's own checks. **The signer is
  not part of `api`'s or `receiver`'s readiness** (§3.7). Liveness only fails on wedged checks or a worker
  stopped for good (v1 semantics, NATS parts removed). Bounded shutdown: HTTP drain, leases released, spans
  flushed, each with a time limit.
- **Alerts**: SLO burn rates; any dead inbox row or dead job; any unrouted outbox event; outbox oldest > 1 min;
  jobs oldest ready > 2 min; any e-CF non-final after 1 h with DGII up; contingency age > 50 % and > 90 % of its
  legal window; delivery failed; DGII breaker open > 10 min; certificate expiring < 7 days; range < 20 % left or
  expiring < 7 days.
- **Data-quality check** (daily schedule, paging on failure): the numbering-accounting invariant (§8.6.3). Other
  checks are deferred (§16.4).
- **Operator controls** (`ledger-worker` subcommands, audited): list stuck documents by company/state, wake a
  parked job or all parked jobs of a kind and company, replay a dead inbox row, re-route or delete an unrouted
  outbox event, record an enrollment's authority evidence.
- Dashboards as code; a runbook linked from every alert (`docs/runbooks/<area>/`).

## 12. Performance targets and SLOs

| Target | Value | Notes |
|---|---|---|
| API availability | 99.9 % monthly | v1 |
| `POST /v1/invoices` latency | p95 < 300 ms, p99 < 800 ms, including the synchronous numbering and signing; hard budget 1.5 s for step 4 (§8.5) | excludes DGII |
| Read endpoints | p95 < 100 ms | single-company reads |
| Final DGII state | 99 % of documents within 5 min when DGII is up | v1; measured from `issued` |
| Buyer delivery | 99 % delivered within 15 min of acceptance when the buyer's receiver is up | |
| Event fan-out | p95 < 250 ms commit → handler start (NOTIFY path), p99 < 1.5 s (poll path) | |
| Job pickup | p95 < 1 s from `run_at` | |
| Signing | p95 `SignXml` < 50 ms end to end over gRPC (local backend); remote backend budget set with the entity | M3 measured p95 0.81 ms in process |
| Build + validate + sign | p95 < 100 ms for a 60-line e-CF | M4 measured p95 7.4-10.9 ms |
| Capacity | planning peak 20 documents/s at month-end (to confirm, Q4); **5× smoke load test** before go-live; 20× after (§16.4) | [04] rule 37 |
| Test suite | full gate < 10 min in CI with xdist; a package's unit tests < 30 s locally | §14 |

## 13. Deployment

- **One image** (`deploy/docker/ledger.Dockerfile`): multi-stage `uv sync --locked --no-dev` of the production
  distributions, distroless base, non-root, read-only file system; console scripts `ledger-api`,
  `ledger-worker`, `ledger-signer`, `ledger-receiver`, `ledger-migrate`. Dev-only distributions (the testing
  plugin, the fake DGII) are never in it.
- **Local**: `docker compose -f deploy/local/compose.yaml up --wait` (PostgreSQL 18 with generated roles,
  LocalStack 4.14.0 for KMS and S3 with object lock, the fake DGII from `ledger-country-do-testing`, the five
  apps; Zitadel behind profile `zitadel`); `docker compose watch` rebuilds on change. Tilt and NATS are removed.
  Image pins live once in `deploy/local/images.toml`, read by compose and `ledger_testing.containers` ([05] D30).
- **Production, minimal (v2-M6)**: managed Kubernetes, one Helm chart `deploy/helm/ledger` with a values block per
  app (replicas, resources, probes, network policies: signer reachable only from api/worker/receiver, receiver on
  its own ingress host), plain `helm upgrade` from CI, managed PostgreSQL with PITR (and a transaction pooler,
  §6.7), object storage with object lock, cloud KMS, External Secrets, an ingress with request size and rate
  limits. Argo CD and the service mesh (mTLS) are deferred (§16.4).
- Configuration only through `LEDGER_*` variables, fail-closed on unknown names (v1 kept).
- Region chosen by the owner (Q3); no data-residency rule was found in Ley 32-23, Decreto 587-24 or DGII's
  technical documents; Ley 172-13 (personal data) is with counsel ([06] §5).

## 14. Testing strategy

### 14.1 Levels and layout

| Directory | What | Infrastructure |
|---|---|---|
| `tests/unit` | domain, application with fakes, regime data, builders | none; runs on Windows in seconds |
| `tests/integration` | repositories, RLS, migrations, outbox/inbox/jobs, HTTP through the app, gRPC signer, fake DGII flows | PostgreSQL (testcontainers), LocalStack, fake DGII |
| `tests/contract` | OpenAPI snapshot, event JSON Schemas, protobuf (buf), golden XML | none |
| repo `tests/` | architecture invariants no tool expresses (every tenant table has a policy test; every facade is guarded) | PostgreSQL |

### 14.2 Rules

- **Per-package coverage**: each package's tests measure only that package (`--cov=<import package>`), branch,
  `fail_under = 100`, `core = "sysmon"` (M4 decision 17).
- **Parallel**: `pytest-xdist -n auto`; one PostgreSQL container per session; each xdist worker gets its own
  database cloned from a migrated template (`CREATE DATABASE w<n> TEMPLATE ledger_template`).
- **Session-scoped expensive things**: containers, the local issuer's RSA keys, the migrated template, the app
  instance where tests isolate by organization ([05] §6.2).
- **No time-based proofs**: no `sleep` to prove something did *not* happen and no `sleep(0)` to order tasks.
  Absence is proved by draining deterministically (`await dispatcher.run_until_idle()`, `await
  jobs.run_due(now=clock.at(...))`), then asserting. Presence may use a bounded `eventually`. Time is an injected
  `Clock` advanced by tests.
- **Shared harness** = `ledger_testing` registered as a pytest plugin (`pytest11`) providing `postgres`,
  `database` (per worker), `local_issuer`, `signer_fake`, `clock`, `restore_logging`, `tracing`, and helpers
  (`waiting_for_a_lock`, `fail_rollback`, `occupy_a_port`) once ([05] D26-D28).
- **Property tests** (hypothesis) for money, calculator, tax-id schemes, numbering, state machines, codecs.
- **End to end**: compose stack against the fake DGII; nightly against `TesteCF` once a test taxpayer exists
  (Q2); certification scripts opt-in. Schemathesis against the OpenAPI document is deferred (§16.4).
- **Load**: k6 (pinned digest, open-model constant arrival rate), 5× smoke before go-live.

### 14.3 Gate (R6)

ruff (ALL), basedpyright `all` + mypy `--strict` (warnings fail), import-linter (generated), deptry, vulture on
`src` and tests (`poe dead-src` reports code only tests use), typos, `buf lint` (`buf breaking` in CI),
`ledger-devtools check`, pytest per package with 100 % branch; zero `type: ignore`/`noqa`/`pragma: no
cover`/tool directives (the `suppressions` step). As built: `uv run poe check`; pip-audit is not in it yet.

### 14.4 CI

One job runs `poe check` (the whole gate) on every PR and on `main`. The affected-set planner and per-package
matrix are deferred (§16.4).

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
| XML, crypto | lxml, signxml (second verifier in tests only), cryptography; xmlschema deferred (§16.4) |
| Printed representation | a PDF library with CPython 3.15 wheels, chosen in the v2-M4 plan; a QR encoder |
| Identity | Zitadel v4, joserfc |
| Cloud | boto3 (KMS, S3 with object lock) |
| Observability | OpenTelemetry SDK, prometheus-client, structlog |
| HTTP client | httpx |
| Tooling | ruff, basedpyright, mypy, import-linter, deptry, vulture, typos, buf, pytest (+asyncio, xdist, randomly, cov), hypothesis, testcontainers, poethepoet, k6 (copier with `scaffold`, §16.4) |

---

## 16. Migration from v1

### 16.1 Keep / move / split / delete / rewrite

Verdicts: **K** keep · **Mv** move · **S** split · **M** merge · **D** delete · **R** rewrite. Audit section in
brackets.

**Root, CI, deploy, docs**

| v1 item | Verdict | v2 target / reason |
|---|---|---|
| `pyproject.toml` | R | members by glob; the lists naming packages a generated block; `poe check` is the gate [05 §7.1, D32] |
| `tests/test_workspace.py` | D | lists are generated and checked by `ledger-devtools check` [05 §7.1] |
| `conftest.py` (root, Windows selector loop) | Mv | `ledger_testing` pytest plugin |
| `.github/workflows/ci.yml` | R | one job running `poe check` (matrix later, §16.4); buf image from one variable [05 D31] |
| `services/identity/Dockerfile` | D | `deploy/docker/ledger.Dockerfile` [05 D33] |
| `Tiltfile` | D | `docker compose watch` |
| `deploy/local/compose.yaml` | R | one image, five apps, LocalStack, fake DGII; NATS removed |
| `deploy/local/postgres/init.sql` | R | generated roles/schemas (§6.2); `ledger_app`/`ledger` DB narrative removed [05 §3.3] |
| `deploy/local/identity.env` | M | one `deploy/local/ledger.env` |
| `deploy/local/zitadel/*` | K | |
| `_typos.toml`, `.gitignore`, `.dockerignore`, `.gitattributes` | K\* | mark generated files `linguist-generated`; DGII XSDs `-text` |
| `README.md` | R | ~80 lines; chassis manual, tables and backlog removed [05 §7.2, D36-D40] |
| `docs/superpowers/specs/2026-10-11-…` | K | history; superseded by this document |
| `docs/superpowers/plans/m1, m2` | D (tree, in v2-M1; owner ruling) | executed; in git history [05 D41] |
| `docs/superpowers/plans/m3, m4, m5` | D (tree, in v2-M1; owner ruling) | superseded; their *decisions* are carried into §3.7, §8.6, §9.9 of this spec and cited |
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
| — | new | `tenancy`, `jurisdiction`, `tax_ids`, `dated`, `regimes` (pure types + calculator; loading lives in `platform.runtime.registries`) |

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
| — | new | `contracts/proto/ledger/signer/v1/signer.proto`, generated into `ledger_signer.adapters.grpc._generated.v1` |

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
| `schema.py` | M | `data/chassis_schema.py` with `chassis_schema_v1` (outbox, inbox, jobs, subscriptions, idempotency keys, audit events; §6.4) [05 D20] |
| `outbox.py` (570) | S | `events/{outbox,dispatcher}` + `data/queue`; JetStream publish deleted |
| `consumer.py` (563) | S | inbox + runner kept in `events/`; JetStream consumer management deleted |
| `streams.py`, `jetstream.py`, `interruptions.py` | D | R2 |
| `idempotency.py` | Mv | `http/idempotency.py`, retention 7 days |
| `authentication.py` (604) | S | `auth/{keys,tokens}` + `auth/providers/zitadel` [05 §4.1] |
| `authorization.py` | S | `auth/access.py`; `CachedPermissions` D |
| `identity_client.py` | D | in-process `IdentityApi` [05 §3.1] |
| `grpc_server.py`, `grpc_client.py` | K | `grpc/`; shared status table [05 D5, D6]; guard gains per-caller tokens and method allowlists |
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
| `domain/branches.py` | R | `establishments.py` (code, name, time zone; minimal CRUD); emission points D [05 §4.2] |
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

v1's plans `milestone-3-vault`, `milestone-4-fiscal-do` and `milestone-5-invoicing` are **superseded**; their
decisions are carried into §3.7, §8.6 and §9.9 and cited. v1 plan files m1-m5 are removed from the tracked tree in
v2-M1 (git history keeps them; owner ruling). Each milestone gets its own plan and ends with the gate green and the
owner's review.

| # | Milestone | Done when | Runs |
|---|---|---|---|
| v2-M1a | **Chassis-lite** (week 1) | new tree (packages, modules, countries, apps, tools); `ledger-devtools sync/check`; kernel v2; platform `config`, `observability`, `runtime` (App, contexts, parts, facade guard, registries), `data` (units with `SET LOCAL ROLE`, RLS builder with NULLIF, `chassis_schema_v1`, migrations), `events` (outbox, subscriptions, fan-out, inbox runner), `jobs` (enqueue, claim with fencing, schedules on lease rows), `http` basics; `api`/`worker`/`migrate` apps; testing plugin, xdist; one image; compose without NATS | in parallel with M1b |
| v2-M1b | **DGII spike** (week 1) | seed → signed seed → token → sign → submit → poll for a type 31, and an RFCE for a type 32, against `TesteCF`; until access exists (Q2), against the fake DGII written from DGII's documentation (`ledger-country-do-testing`). Throwaway code allowed outside the gate in `.superpowers/spikes/`; findings recorded; XSDs vendored and pinned (kept) | in parallel with M1a |
| v2-M2 | **Identity v2 ∥ Signer** | identity moved and adapted (jurisdiction, currency, qualified tax ids + registry, modes, TypeIDs, `Ledger-Version` pin storage/echo, permissions from manifests, minimal establishments with time zone, API clients, Zitadel claims profile, `GET /v1/countries`); signer module + app: gRPC contract, local backend (KMS envelope, LocalStack) **and** the remote-backend port with a fake entity, import/describe/sign XML/sign seed, audit, per-caller tokens, expiry schedule | two tracks |
| v2-M3 | **`do-ecf-v1` emission with thin invoicing and fiscal** | fiscal-kit minimal (§8.4); regime `do`; builders for 10 types + RFCE; ranges with released numbers; e-CF state machine on jobs; DGII client with breaker; archive; thin invoicing (`/v1/invoices`, `external_reference`, `IssuePolicy`, `apply_fiscal_status`); thin fiscal (enrollments, routing, synchronous numbering and signing, recovery consumer); webhooks minimal; every type accepted on the fake DGII, then on `TesteCF` | critical path |
| v2-M4 | **Reception, delivery, printed representation, ANECF** | receiver app (seed/token, reception with ARECF, commercial approval); buyer delivery via the DGII directory; ACECF both ways; PDF representation with QR, security code and legends; range voids; contingency (connectivity clock, paper-NCF replacement with code 4); channel Silver on `TesteCF` | critical path |
| v2-M5 | **Certification on `CerteCF`** | our RNC certified as electronic emitter (DGII's 14 steps), then the PSFE set of tests, sworn statement and authorization; INDOTEL path per Q5; 5× smoke load; security review; runbooks; the numbering-accounting daily check | critical path |
| v2-M6 | **Minimal production: DR e-CF go-live** | Helm chart, managed services, pooler, ingress, alerts and dashboards, first client onboarded through DGII's "with certified PSFE" flow, live enrollments gated (§7.6) | critical path |

### 16.4 Cut list (designed here, built later)

Built only when go-live is done or when a second implementation needs it (rule of two). The "decided now"
items in the header note are **not** on this list.

| Deferred | Until | What remains now |
|---|---|---|
| Compounding taxes in the kernel's `TaxLine` (ISC, then ITBIS on a base that includes it) | v2-M3, the first ISC line | stacked categories each on the line's base; a golden-file test holds the kernel's totals equal to `do-ecf-v1`'s |
| Import-linter contract 9b (flows are leaves) and the `flow` package kind | the first flow (ADR 0011, Proposed) | the rule in ADR 0011 and §4.6; nothing generates the contract yet |
| devtools `gate`, `affected`, CI matrix, `scaffold` (copier templates), CODEOWNERS, generated reference docs | > 8 packages or a second contributor | `sync`, `check`; `poe check` as the gate; one CI job |
| `countries/zz` synthetic country | second country work | review rule on country diffs |
| Quality-tier rule catalogue and its automated check | second channel | tier claim in the manifest, checked in review |
| Dated-version transforms (`VersionChange`, changelog generation) | first breaking change | header parsing, pin storage (organization, API client), precedence header > API client > organization, echo |
| Webhooks beyond minimal: event filters per endpoint, secret rotation with overlap, events API (`GET /v1/events`) | first client asking | endpoints CRUD, Standard Webhooks signatures, thin payloads, retries 3 days, SSRF defence |
| Per-company job fairness | a tenant > 5 % of load | per-kind concurrency per process |
| fiscal-kit generalization (numbering allocation, submission loop, breaker, conformance harness, `FakeAuthority` base) | second channel | `Channel`, `FiscalDraft`, `FiscalStatus`, `StatusMap`, kit events, hardened XML, `NumberingPolicy`/`SignerPort`/`CompanyDirectory`/`ArchivePort` ports |
| fiscal neutral projection (`/v1/fiscal_documents`) | a second channel or a client need | per-channel mirror on the invoice |
| Hypothesis-generated e-CF documents; second XSD validator (xmlschema) | after certification | golden documents validated by libxml2 |
| 20× load test | before the first large client | 5× smoke |
| Data-quality checks other than numbering accounting | after go-live | the daily numbering-accounting check |
| `RotateCertificate` (compare-and-set) | after go-live | import replaces the active certificate |
| Establishments beyond minimal CRUD (addresses, per-establishment settings) | a country needing them | code, name, time zone |
| Service mesh (mTLS), Argo CD | after go-live | per-caller signer tokens, `helm upgrade` from CI |
| Certificate-expiry email (webhook only now) | after go-live | `certificate.expiring` webhook and alert |
| Zitadel user sync (Actions v2) and subject verification | after go-live | v1's accepted limitation and runbook |
| Schemathesis against the OpenAPI document | after go-live | OpenAPI snapshot tests |
| parties, catalog, ISV grants, entitlements, custom roles, accounting | product roadmap | inline party snapshots; designs in §7 |

---

## 17. Architecture decision records

Written as `docs/adr/NNNN-*.md` (MADR, short), status *Proposed* until the owner accepts this spec.

| ADR | Decision |
|---|---|
| [0001](../../adr/0001-modular-monolith.md) | One codebase as a modular monolith with five deployables; modules talk in process; consuming an event is a dependency edge; extraction only for runtime reasons |
| [0002](../../adr/0002-postgres-eventing.md) | Events through outbox → fan-out → inbox in PostgreSQL; subscriptions in tables written by `migrate`; unrouted events kept; NATS removed; `EventBus` port kept |
| [0003](../../adr/0003-no-temporal-yet.md) | Durable processes as persisted state machines driven by a PostgreSQL job queue with leases and fencing; Temporal not adopted, with revisit triggers |
| [0004](../../adr/0004-schema-per-module.md) | One cluster, a schema and a DML role per module, `SET LOCAL ROLE` per unit, schema-qualified tables, RLS on tenant and mode with `NULLIF`, pooler-safe, `organization_id` as shard key |
| [0005](../../adr/0005-regime-channel-split.md) | Countries as packs of pure regime data and versioned channels, loaded by runtime registries; minimal fiscal-kit under the rule of two; regime types in the kernel (accepted deviation) |
| [0006](../../adr/0006-qualified-tax-ids.md) | Tax identifiers are `(scheme, value)` validated by regimes, check digits reported unless enforced, one global registry of active taxpayers |
| [0007](../../adr/0007-test-and-live-modes.md) | Test vs live mode is a tenancy dimension on every moded row, credential and RLS policy; live mode gated on authority authorization |
| [0008](../../adr/0008-dated-api-versions.md) | `/v1` path major plus pinned dated versions (transforms deferred); TypeID public ids; thin Standard Webhooks |
| [0009](../../adr/0009-manifest-driven-workspace.md) | A uv workspace with one package per unit, `manifest.toml` as the source of truth, generated lists and contracts, devtools sync/check (gate deferred) |
| [0010](../../adr/0010-signer-isolation.md) | Private keys only in a separate signer process, reached over gRPC with per-caller allowlists; two signing backends (local key, remote INDOTEL-authorized entity) |
| [0011](../../adr/0011-flows-and-chained-extension-points.md) | Flows (`flows/<name>`) coordinate several modules as leaves above them; extension points are chained by declared order |

## 18. Risks

| Risk | Mitigation |
|---|---|
| The market deadline (2026-11-15) is 33 days away and v2 restarts the layout | Most code moves rather than being rewritten (§16.2); week-one DGII spike; cut list (§16.4); Q1 decides the launch posture |
| PSFE certification takes longer than our build (we must first certify our own RNC; DGII sets the pace) | Start our emitter certification as soon as M1b proves the DGII path; pilot clients per Q1 |
| INDOTEL authorization for holding clients' keys | Remote-signing backend behind the same port; owner and counsel decide (Q5) |
| PostgreSQL as queue becomes a bottleneck | Gauges; queues per schema; batch fan-out; vacuum settings in the chassis DDL; `EventBus` port allows a broker; ADR 0003 triggers |
| Hand-written state machines grow Temporal-like complexity | Plumbing extracted to the kit at the second channel; ADR 0003 triggers |
| A shared runtime login with `SET LOCAL ROLE` could be misused by a bug | Role bound at assembly, never passed by callers; per-schema grants; extraction gives a module its own login |
| DGII specs change (XSDs, endpoints, codes) and every format version needs re-certification | Vendored and pinned with SHA-256; corrections pinned by count; nightly `TesteCF` run; status-map exhaustiveness; a format version is a new channel |
| Code drifts into country packs instead of being generalized ([02] Kraken) | Rule of two with review; logic two packs need moves into the kit or kernel |
| AGPL-3.0 (Zitadel) | Unmodified, separate service, no linking |

## 19. Open questions for the owner

Resolved by the owner's rulings in review: human tokens must send `Ledger-Mode`; the five built-in roles stay;
v1 plan files m1-m5 leave the tracked tree in v2-M1.

1. **Launch posture.** May pilot clients start emitting on `CerteCF` (test mode) before our PSFE authorization
   completes?
2. **DGII test enrollment.** Which taxpayer (ours or a pilot's) enrolls so we get `TesteCF` access and a test
   certificate? M1b uses the fake DGII until then.
3. **Cloud and region.** No residency rule was found in Ley 32-23, Decreto 587-24 or DGII's documents; Ley
   172-13 (personal data) to be checked with counsel before choosing.
4. **Capacity.** Confirm the planning peak (20 documents/s at month-end, 5× smoke before go-live) or give the
   expected client mix.
5. **INDOTEL signing mode.** Operate the `local` backend (we apply for INDOTEL authorization to manage
   signature-creation data on clients' behalf) or the `remote` backend through an INDOTEL-authorized
   certification entity (which one, under which contract)? Both are built behind one port; the choice decides
   M5's legal track.
