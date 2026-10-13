# Milestone 5: Invoicing and Webhooks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Two new services and fiscal-do moved onto the event path. `services/invoicing` (`ledger_invoicing`): commercial documents, country-agnostic — invoices, credit notes and debit notes referring to an invoice of the same company — with lines that carry their own tax (a value-added-tax category at a rate), exact totals rounded at three documented points, a lifecycle (`draft → issued → fiscally_accepted | fiscally_rejected`, `voided`) whose fiscal half is mirrored from the fiscal service's events, an idempotent `POST /v1/invoices`, `ETag`/`If-Match`, cursor pages, and the events `invoicing.document.issued|voided|fiscally_numbered|fiscally_accepted|fiscally_rejected`. Before it issues, invoicing asks every country's fiscal service over gRPC (`ledger.fiscal.v1.FiscalRulesService`) what that country would refuse, so a local failure is answered at `POST` (spec §5.1). fiscal-do consumes `invoicing.document.issued` through an anti-corruption layer that translates the commercial document into an e-CF's content and numbers it under invoicing's own id; what it can no longer number it declines (`fiscal.document.declined`). `services/webhooks` (`ledger_webhooks`): a company's endpoints with event-type filters, every public event (`invoice.issued`, `invoice.voided`, `invoice.fiscal.numbered|accepted|rejected`, `webhook.test`, each versioned) delivered signed (Standard Webhooks v1: HMAC-SHA256 over id, timestamp and body; a 5-minute replay window; secrets rotated with overlap and sealed at rest), retried with exponential backoff until delivered or dead, guarded against SSRF (public HTTPS only, every resolved address checked, the connection pinned to it), with a test delivery and a log of attempts that never keeps a body. Images, the local stack, CI, and a k6 load test of the spec's SLO (p95 of `POST /v1/invoices` under 300 ms, DGII excluded).

**Architecture:** Each service is hexagonal (§6): `domain` (standard library and kernel) ← `application` (use cases, ports) ← `settings` ← `adapters` (PostgreSQL, the wire, gRPC, HTTP, sealing) ← `resources` ← `api` (REST routes, consumers, workers) ← `main` ← `openapi`. invoicing never names a tax authority: a document's country-specific data travels unread in `fiscal_fields`, and fiscal-do alone translates (`ledger_fiscal_do.adapters.commercial`) and answers (`FiscalRules`). One id names a document everywhere: fiscal-do keeps invoicing's document id as its fiscal document's, so a redelivered event can never number a document twice and invoicing reads fiscal-do's events about its documents without a mapping table. webhooks reads only invoicing's events and tells them in public words (`ledger_webhooks.adapters.translation`): the internal vocabulary never reaches a client's server. A delivery queue outside row-level security, claimed with leases (`FOR UPDATE SKIP LOCKED`), lets every replica's worker send what is due across companies without a transaction ever spanning a network call.

**Tech Stack:** Python 3.15, FastAPI 0.143 / Pydantic 2.14 (core 2.50), SQLAlchemy 2.1.4 Core / psycopg 3.3.6 / Alembic 1.20.0, grpcio 1.84 / protobuf 7.36.2, httpx 0.28.1 (httpcore 1.0.9), cryptography 50.0.2 (AES-GCM), granian 2.8.4, hypothesis 6.169, testcontainers 4.15, PostgreSQL 18.6, NATS 2.12.15, k6 2.3.0 (`grafana/k6`, pinned by digest).

**Spec:** `docs/superpowers/specs/2026-10-11-ledger-platform-design.md` (milestone 5 of §16: "public invoice API, emission workflow, outbox/inbox, webhooks"; §2 rules 1-10, §3 invoicing and webhooks rows, §5.1, §5.3 credit and debit notes, §6 anti-corruption layer, §7, §9 webhooks, §10, §11 SLO, §14 load).

## Global Constraints

- Python `>=3.15`; every package's `pyproject.toml` says `requires-python = ">=3.15"`.
- Code, comments, docs and messages in English. invoicing and webhooks never spell a tax authority's word; DGII's words stay in fiscal-do.
- Zero `# type: ignore`, `# pyright: ignore`, `# noqa`, `# pragma: no cover`, and no tool directive comment: the `suppressions` gate step fails on any.
- `uv run poe check` passes after every task: ruff format + ruff (`select = ["ALL"]`), basedpyright (`all`, warnings fail), mypy `--strict` per package, import-linter, deptry per package, typos, `buf lint`, pytest with **100 % branch coverage**, warnings as errors.
- Errors build their message inside their class; a message never echoes a document, an amount, a URL, a secret or a tax id a client sent. A webhook's URL is the client's and may carry secrets in its query: it is never in an error, a log or a span.
- Type-only imports use `lazy from ... import ...` (ruff decides which; a pydantic field's or a FastAPI route's annotation imported lazily resolves at first use, verified for `SecretStr` and the route models on 3.15).
- No transaction spans a network call: invoicing asks the fiscal services before its unit of work, and webhooks claims, sends and records in three steps. (fiscal-do's consumer calls identity inside its inbox transaction, as Milestone 4's consumers call Temporal: bounded by the call's deadline.)
- Money is `ledger_kernel.money.Money`, exact; a value is rounded only at the points `ledger_invoicing.domain.totals` names, once each.
- Test support modules are named per service (`invoicing_samples`, `invoicing_fakes`, `invoicing_remotes`, `webhook_samples`, `webhook_fakes`, `webhook_remotes`): pytest's `pythonpath` puts every service's `tests/` in one namespace, where fiscal-do's `samples`, `fakes`, `world` and `remotes` already live (Decision 16).
- Dependency floors introduced here, verified on 2026-10-12 to install and run on CPython 3.15.0 in the scratch workspace (`uv sync`, then the tests): no new third-party package. invoicing and webhooks use the floors Milestones 1-4 set (`alembic>=1.20.0`, `fastapi>=0.143.0`, `granian[uvloop]>=2.8.4`, `httpx>=0.28.1`, `protobuf>=7.36.2`, `pydantic>=2.14.1`, `sqlalchemy>=2.1.4`); webhooks adds `cryptography>=50.0.2` as a direct dependency (the workspace's constraint since Milestone 2). The load test runs `grafana/k6:2.3.0@sha256:9c2dee7f8ed74d317e4027c06a10f169b625638189de8d4555d0b3486a5aeb34` (image created 2026-09-21).
- Docker must be running: tests use PostgreSQL `postgres:18.6` and NATS `nats:2.12.15`; fiscal-do's own tests keep Milestone 4's LocalStack and Temporal.
- Work on branch `milestone-5-invoicing`, created from `main` once Milestone 4 (fiscal-do) has been merged; one commit per task; never push or rewrite `main`; commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **Milestones 2, 3 and 4 are being executed while this plan is written.** This plan builds on the surface their plans declare and on what has landed (`libs/platform` at commit `44b1b5f`: `Service`, `Interfaces(http, grpc, resources, checks)`, `Events(streams, consumers, workers)`, `Runtime.resources`, `routes` (`authenticated`, `company_in_header`, `page_request`, `if_match`, `IfMatch`, `etag`, `precondition_failed`, `idempotency_key`, `attempt_of`, `json_response`, `answer`), `Idempotency`, `Outbox`, `ConsumerSpec`, `serve_grpc`, `call_unary`, `require_deadline`, `RemotePermissions`, `CachedPermissions`, `grpc_client.DEFAULT_DEADLINE`). From the plans: `ledger_platform.grpc_client.Channel` and `ledger_platform.migrations` (M3 Task 3), `ledger_testing.api` (`Caller`, `created`, `document`, `page`: M2 Task 13), the whole of `ledger_fiscal_do` (M4), `ledger_contracts.fiscal_do` and `ledger.events.fiscal_do.v1` (M4 Task 2). Every task that uses one of these says **Re-check before starting** with the names to confirm; where the landed name or signature differs, the task uses the landed one and says so in its report.
- Every line of code in this plan for kernel, contracts, invoicing, webhooks and fiscal-do's new modules was run as written in a scratch workspace on 2026-10-12 (CPython 3.15.0 on Windows, selector event loop): the landed `libs/*` plus scratch copies of what the Milestone 3 plan declares (`Channel`, `migrations`), Milestone 2's `ledger_testing.api`, and Milestone 4's fiscal-do `domain` and `application` extracted from its plan with this plan's changes applied: 736 tests in random order, 100 % branch coverage of `ledger_kernel`, `ledger_contracts`, `ledger_invoicing`, `ledger_webhooks` and fiscal-do's new modules, ruff, basedpyright `all`, mypy `--strict`, import-linter, deptry and typos clean, `buf lint` (`bufbuild/buf:1.73.0`) clean. **Not run** (they need the whole of Milestones 2-4 built): fiscal-do's changed consumers, composition root and the additions to its `test_service.py` (Task 8, Steps 6-7); the images, the compose stack and the CI step (Task 14; `docker compose config` validated the new services against the landed compose file plus stand-ins); the load test against the stack (Task 15; the script was validated with `k6 inspect` and a run against a stand-in server).

## Review Focus

1. **Exact totals, rounded where documented and nowhere else** (`ledger_invoicing.domain.totals`, `Money.divided`): a hypothesis law that a quotient is the exact rational rounded half away from zero (a quotient a hair below a half, which a rounding to 60 digits would push onto it, stays below); the total is the sum of the parts a client sees; each rounding within half a cent; totals never depend on line order. Tests in Tasks 1 and 3.
2. **One document, one fiscal number, never two**: invoicing's id is fiscal-do's; a redelivered `DocumentIssued` finds the document and does nothing; numbering a declined or refused document takes nothing; the fiscal mirror applies only newer fiscal versions, so a late `numbered` after an `accepted` changes nothing. Tests in Tasks 3, 4, 6, 8.
3. **Answered before issuing, and replay-safe**: invoicing's own rules, then every fiscal service's, before anything is kept; their objections are raised inside the idempotent work, so a retry of a request that succeeded replays its answer whatever the services say now; a service that cannot answer is a 503 and nothing is issued. Tests in Tasks 4, 6, 7.
4. **Credit notes**: only of a settled invoice of the same company and currency, never crediting more than the invoice with the notes before it, under the invoice's row lock. Tests in Tasks 4, 6.
5. **SSRF**: every private, loopback, link-local, unique-local, multicast, reserved and metadata address refused, also when carried in an IPv6 form (mapped, 6to4, NAT64; Teredo refused) — a hypothesis property over every private IPv4; one private answer among public ones refuses them all; the connection goes to the checked address with the name only in `Host` and SNI; no redirect followed; plain HTTP and private targets refused at start beyond a laptop. Tests in Tasks 9, 11, 13.
6. **Signatures and secrets**: Standard Webhooks v1 verified by an independent check (`verify`); a replay outside 5 minutes refused; both secrets sign during a rotation's overlap; secrets sealed with AES-256-GCM bound to their endpoint (a ciphertext moved to another row does not open); never in an answer an idempotency key stores. Tests in Tasks 9, 11, 12, 13.
7. **Deliveries**: at least once, each due delivery claimed by one worker (leases, `SKIP LOCKED`, across companies), backoff with jitter up to dead after 12 attempts, a body kept only while pending (a check constraint), no body in the attempts log, a gone endpoint's deliveries cancelled unsent. Tests in Tasks 10, 12, 13.
8. **Tenancy**: invoicing's documents, webhooks' endpoints, deliveries and attempts under `tenant_isolation`; references between rows through `(company_id, id)`; the delivery queue the only table outside, holding ids and times. Tests in Tasks 5, 6, 12.

## Decisions

The owner's scope for this milestone is binding; these are the decisions this plan adds, each with its reason, and the facts the scratch workspace established.

1. **invoicing → fiscal-do through an anti-corruption layer on the event path; `/v1/do/documents` is kept, not deprecated.** invoicing publishes `invoicing.document.issued` carrying the whole `CommercialDocument`; fiscal-do's consumer `fiscal-do-commercial-documents` translates it (`adapters.commercial`) and issues it through Milestone 4's own `issue` use case, so numbering, the workflow and DGII are unchanged. `/v1/do/documents` stays because it serves what invoicing does not model and should not: the purchase-side documents a company issues on a supplier's behalf (types 41, 43, 47 — `not_commercial` when asked of invoicing), DGII's certification test sets of Milestone 7, which are written in DGII's vocabulary, and integrators that already speak DGII. Both paths meet in one `issue`, under one range lock, so neither can take a number the other took. Deprecating it would remove a capability before its replacement exists; it is documented as the DGII-native API, and invoicing as the API for everyone else.
2. **Country rules are fiscal-do's, exposed over gRPC as a service every country implements (`ledger.fiscal.v1.FiscalRulesService.CheckDocument`), not a kernel tax module.** Rule 6 of the spec puts country rules in the country's service; a kernel module would make a second copy of DGII's rules (rates, which type needs what) that fiscal-do would have to agree with forever, and would have to change for every country added. invoicing asks every service it is configured with (`LEDGER_FISCAL_RULES_ADDRESSES`), concurrently, within `fiscal_check_seconds`; a service answers `applies = false` for a company it does not serve (fiscal-do: one with no fiscal profile), so a second country is one more address, nothing else. The check takes nothing and writes nothing; fiscal-do answers from its own database without calling identity (a deactivated company is declined when adopted).
3. **Lines carry their own tax: a value-added-tax category (`standard`, `reduced`, `zero`, `exempt`, `outside_scope`, UBL's categories) at a rate, and optional withholdings.** The catalog service is not in this milestone, and a line must be issuable without it. invoicing checks only what holds in every country (a rate from 0 to 100 %, 0 in the untaxed categories, at most six decimal places) and computes with the rate the client sent; fiscal-do maps `standard` at 18 % to ITBIS 1, `reduced` at 16 % to ITBIS 2, `zero` to ITBIS 3, `exempt` to exempt and `outside_scope` to not billable, and refuses any other rate (`tax_rate_not_in_force`). When the catalog arrives (spec §3), it fills lines with the same two values.
4. **What only one country needs travels in `fiscal_fields`, a bounded map invoicing keeps and passes on unread** (at most 16 keys, `snake_case`, values up to 200 characters). fiscal-do reads `ecf_type` (to issue 44, 45 or 46, or to force 31 or 32), `income_type` and `destination_country`, and refuses any other key (`field_unknown`), so a client's typo is an answer, not a silently ignored field. Without `ecf_type`, a credit note is 34, a debit note 33, and an invoice 31 when its customer has a tax id, 32 when not.
5. **One id names a document everywhere.** fiscal-do issues an invoicing document under invoicing's id (`IssueRequest.document`, a Milestone 4 field added here): its `documents` primary key makes a second numbering impossible whatever the inbox did, and invoicing reads `fiscal.document.*` about its documents by id, ignoring those of documents issued at `/v1/do` (not found in its tables). fiscal-do's events need no new field.
6. **Totals are exact and rounded at three points** (`ledger_invoicing.domain.totals`): a line's amount (price x quantity - discount), a tax group's base when the prices include the tax (sum / (1 + rate)), a group's tax (base x rate), each to the currency's minor unit, half away from zero. These are the points of DGII's format (Milestone 4's `totals`), so for the Dominican Republic both services compute the same total; fiscal-do refuses a document whose total differs (`totals_disagree`) rather than sending one. The kernel gains `Money.divided(divisor)`, a quotient rounded once by integer division and its remainder: computing the quotient to some precision and rounding it again can move a value a hair below a half onto it (the test `test_a_quotient_just_below_a_half_is_not_rounded_up_twice` is such a value). Quantities and unit prices have at most six places and stay below 10^7 and 10^11, so their product stays below `Money`'s 10^18.
7. **Asking before issuing, and replays.** `POST /v1/invoices` checks invoicing's rules, then asks the fiscal services (outside any transaction), then, in one unit of work keyed by `Idempotency-Key`, raises the services' objections, checks a note's reference under the referred invoice's lock, stores the document and adds `DocumentIssued` to the outbox. The objections are raised inside the keyed work, not before it: a retry of a request that succeeded replays its stored answer even if the services would object now (a range used up since). A service that cannot answer is `RemoteUnavailableError`, a 503: nothing is issued past a fiscal service that could not look. Drafts ask nobody.
8. **The lifecycle and the fiscal mirror.** `draft → issued → fiscally_accepted | fiscally_rejected`; `voided` from a draft, from `fiscally_rejected`, or from `issued` when no fiscal service serves the company (`fiscal.status = not_applicable`); an accepted document is never voided (a credit note corrects it, as DGII requires). The fiscal side (`numbered`, `accepted`, `conditionally_accepted`, `rejected`, `declined`, with the authority's messages, at most 20, each cut to 500 characters) is mirrored from fiscal-do's events by version: an event applies only when its fiscal version is above the one mirrored, so events can arrive in any order and a redelivery changes nothing. Every mirrored change is announced (`invoicing.document.fiscally_*`) for webhooks. A note refers only to a settled invoice (accepted, or issued without a fiscal life) of the same company and currency, and credit notes never total more than their invoice.
9. **`fiscal.document.declined`** (an additive message in `ledger.events.fiscal_do.v1`, on the stream's existing `fiscal.document.>` tree): what fiscal-do refuses after invoicing issued (no range left since the check, the company deactivated, a rule broken since) is announced with the refusal's code and message and takes no number; invoicing marks the document `fiscally_rejected` with status `declined`, and the client issues a corrected one. Errors that may pass (a dependency unavailable, a defect) are not declined: the event is retried on the consumer's backoff.
10. **Public events are invoicing's, told in public words, versioned; webhooks reads no other service's events in this milestone.** `invoice.issued`, `invoice.voided`, `invoice.fiscal.numbered`, `invoice.fiscal.accepted` (`conditional`, messages), `invoice.fiscal.rejected` (`declined`), and `webhook.test`, each version 1; within a version fields are only added, a breaking change is a new version. A public event's id is the internal event's (receivers de-duplicate by `webhook-id`). Its data names the document and what changed, never its lines, customer or tax ids: an endpoint is a third party's server, and clients read the rest from the API with their own permissions. Public events for documents issued at `/v1/do` come with Milestone 6, beside received documents' (fiscal-do's events would need an origin field).
11. **Signatures follow Standard Webhooks v1** (`webhook-id`, `webhook-timestamp`, `webhook-signature: v1,<base64 HMAC-SHA256 of "id.timestamp.body">`, secrets shown as `whsec_` + base64): clients verify with an existing library in their language instead of our own scheme. Every attempt is signed when sent, so a retry hours later carries a fresh timestamp and passes a receiver's 5-minute window (§9); during a rotation's overlap (1 to 72 hours, 24 by default) every request carries both signatures.
12. **Secrets are sealed at rest with AES-256-GCM under a keyring of the service's** (`LEDGER_SECRET_KEYS`, from the secret manager; a key id in each sealed value, so the keyring rotates without losing a secret), the associated data binding each to its endpoint and its own id. Not vault's KMS envelope: vault holds signing certificates and never hands out key material, while a webhook secret must be read back to sign every request; KMS-backed sealing of the keyring is Milestone 8's hardening. No answer an idempotency key stores holds a secret (stored answers live 24 hours in a table): a client reads them from `GET /v1/webhooks/{id}/secret` (`company.manage`, `Cache-Control: no-store`), which stores nothing.
13. **SSRF defence: refuse at registration, check every address at every attempt, pin the connection.** A URL is refused unless `https` (plain `http` only where `allow_http_targets`, local and test only), with a host, no credentials and no fragment, its name not local by definition (`localhost`, `*.local`, `*.internal`, `*.home.arpa`, a single label) and, when an IP literal, public. At each attempt the name is resolved once, every address must be public (one private answer refuses them all), and the request goes to the first, with the name only in `Host` and in TLS's server name (httpx's `sni_hostname` extension, which httpcore 1.0.9 also verifies the certificate against: read in its source). Found on CPython 3.15: `ipaddress.is_global` is true for multicast (`224.0.0.1`) and for NAT64 addresses carrying loopback (`64:ff9b::7f00:1`), so "public" is `is_global`, not multicast, and judged on the IPv4 address any mapped, 6to4 or NAT64 address carries; Teredo is refused. Connections are never kept alive (a pooled connection is keyed by address, and one opened under one name must not carry a request for another), redirects are never followed, environment proxies are ignored, and the answer's body is never read.
14. **Deliveries are at least once, from a queue outside row-level security, claimed with leases.** One delivery per endpoint and event (unique), its body made once and kept only while pending (`ck_deliveries_body`), due at once. The worker of each replica claims up to `delivery_batch` due rows across companies (`UPDATE ... WHERE delivery_id IN (SELECT ... FOR UPDATE SKIP LOCKED) RETURNING`, a lease of `delivery_lease_seconds`), reads each in its company's unit, sends outside any unit, records in another. A worker dying mid-way lets the lease expire and the delivery is sent again (hence `webhook-id`). Failures wait one minute, doubling up to six hours, each wait scaled by 0.8-1.2 at random; after 12 attempts (about 25 hours) the delivery is dead. The queue holds ids and times only, like Milestone 4's watches. A test delivery is a `webhook.test` delivery like any other, answered `202` with its `Location`, never a request made while the client waits.
15. **Endpoints belong to one company** (`tenant_isolation`), managed with `company.manage` and read with `company.read`: the permissions identity already grants, so identity is unchanged. Organization-wide endpoints wait for an organization-level permission from identity.
16. **Test support modules are named per service.** In the scratch workspace, webhooks' `samples` was imported as invoicing's (`ImportError: cannot import name 'TARGET' from 'samples' (...services\invoicing\tests\samples.py)`): pytest's `pythonpath` lists every service's `tests/` and plain modules share one namespace. fiscal-do (Milestone 4) took `samples`, `fakes`, `world` and `remotes`; invoicing's and webhooks' are prefixed.
17. **The load test is k6 2.3.0, pinned by digest, run on demand as the compose profile `load`.** The spec names k6 (§14); its constant-arrival-rate executor is an open model (a slow answer does not lower the offered load, which a closed model such as Locust's users would, hiding the latency it is meant to find); thresholds make the run fail with exit 99 when the SLO does not hold; it is a Go binary in its own container, so the generator's CPU is not the service's and nothing enters the Python 3.15 dependency set. The script (`services/invoicing/loadtest/invoices.js`) issues consumer invoices at 20 per second for two minutes by default, each with its own `Idempotency-Key`, the measure being `http_req_duration{name:create_invoice}` p95 < 300 ms, which includes the gRPC check with fiscal-do and excludes DGII as the SLO says. Validated on 2026-10-12: `k6 inspect` parses it, and a 3-second run against a stand-in server passed every threshold. Measured in process in the scratch workspace (one replica, Windows, real PostgreSQL and NATS, fiscal-do faked over real gRPC, JWT verified per request): p95 33 ms one at a time, 196 ms at 10 concurrent requests in one process; the compose run is the release gate.
18. **invoicing and fiscal-do do not wait for each other's health in the local stack**: each consumes the other's stream, so `service_healthy` both ways would never start. Each declares its own stream at start; a consumer whose stream is not there yet is started again by the chassis's supervisor (at most a minute later, failing neither probe meanwhile: spec §11) and binds once it is.
19. **An `INSERT ... ON CONFLICT DO NOTHING` reports what it did through `RETURNING`, not `rowcount`**: in the scratch workspace (psycopg 3.3.6, SQLAlchemy 2.1.4, async) `rowcount` reported 0 for a row it had inserted, so `PgDeliveries.add` answered "already there" for every new delivery; found by `test_a_delivery_keeps_its_body_while_pending_and_its_attempts_without_one`. `UPDATE`'s `rowcount` (the compare-and-set of invoicing's and webhooks' updates) is reliable and tested.
20. **Coverage reads with `sys.monitoring` (`core = "sysmon"`)**, as Milestone 4's Decision 17 found: with the C tracer, the lines of a route that run after SQLAlchemy's asynchronous driver switched greenlets were reported unexecuted (verified again here on invoicing's routes); the migrations directories are listed in `source` by path.
21. **Deferred, recorded in the README and the spec (Task 16):** organization-wide endpoints; redelivering a dead delivery and disabling an endpoint that keeps failing; the `InvoicingQuery` gRPC service (§3: nothing calls it yet); the catalog service; currencies other than DOP for fiscal documents (Milestone 4's Decision 22); public events of documents issued at `/v1/do`; KMS-backed sealing of webhooks' keyring; an end-to-end run of the whole chain against DGII's TesteCF (Milestone 4's deferral holds).

## File map

```
ledger/
├── pyproject.toml  uv.lock  _typos.toml  Tiltfile  README.md  .github/workflows/ci.yml
├── deploy/local/{compose.yaml, invoicing.env, webhooks.env, postgres/init.sql}
├── docs/superpowers/specs/2026-10-11-ledger-platform-design.md
├── libs/kernel/src/ledger_kernel/money.py                                         (modified, Task 1)
├── libs/kernel/tests/test_money_division.py
├── libs/contracts/proto/ledger/invoicing/v1/document.proto                        (new, Task 2)
├── libs/contracts/proto/ledger/fiscal/v1/fiscal_rules.proto                       (new, Task 2)
├── libs/contracts/proto/ledger/events/invoicing/v1/events.proto                   (new, Task 2)
├── libs/contracts/proto/ledger/events/fiscal_do/v1/events.proto                   (modified, Task 2)
├── libs/contracts/src/ledger/{invoicing,fiscal,events/invoicing}/v1/*_pb2{,_grpc}.py{,i}   (generated)
├── libs/contracts/src/ledger_contracts/{invoicing, fiscal}.py, fiscal_do.py (modified)
├── libs/contracts/tests/test_invoicing_contracts.py
├── services/invoicing/
│   ├── pyproject.toml  Dockerfile  openapi.json
│   ├── loadtest/invoices.js
│   ├── src/ledger_invoicing/{__init__, settings, resources, main, openapi}.py, py.typed
│   ├── src/ledger_invoicing/domain/{__init__, ids, vocabulary, problems, content, form, totals, fiscal,
│   │                                events, document}.py
│   ├── src/ledger_invoicing/application/{__init__, ports, commands, outcomes, queries}.py
│   ├── src/ledger_invoicing/adapters/{__init__, wire, events, fiscal_rules}.py
│   ├── src/ledger_invoicing/adapters/postgres/{__init__, tables, codec, repositories, units, migrate}.py
│   ├── src/ledger_invoicing/adapters/postgres/migrations/{__init__, env}.py, versions/{__init__, r0001_invoicing}.py
│   ├── src/ledger_invoicing/api/{__init__, context, models, invoices, consumers}.py
│   └── tests/{conftest, invoicing_samples, invoicing_fakes, invoicing_remotes}.py (support),
│             tests/{test_totals, test_form, test_document, test_application, test_settings, test_migrations,
│                    test_repositories, test_wire, test_fiscal_rules, test_service, test_openapi}.py
├── services/fiscal-do/                                                            (Task 8)
│   ├── src/ledger_fiscal_do/domain/{problems, events}.py, application/issuing.py,
│   │   adapters/events.py, api/consumers.py, main.py                              (modified)
│   ├── src/ledger_fiscal_do/application/commercial.py, adapters/commercial.py, api/rules.py   (new)
│   └── tests/test_commercial.py (new), tests/test_service.py (modified)
└── services/webhooks/
    ├── pyproject.toml  Dockerfile  openapi.json
    ├── src/ledger_webhooks/{__init__, settings, resources, main, openapi}.py, py.typed
    ├── src/ledger_webhooks/domain/{__init__, ids, catalog, targets, signing, delivery, endpoint, payloads}.py
    ├── src/ledger_webhooks/application/{__init__, ports, endpoints, fanout, sending}.py
    ├── src/ledger_webhooks/adapters/{__init__, sealing, network, translation}.py
    ├── src/ledger_webhooks/adapters/postgres/{__init__, tables, repositories, units, migrate}.py
    ├── src/ledger_webhooks/adapters/postgres/migrations/{__init__, env}.py, versions/{__init__, r0001_webhooks}.py
    ├── src/ledger_webhooks/api/{__init__, context, models, endpoints, consumers, workers}.py
    └── tests/{conftest, webhook_samples, webhook_fakes, webhook_remotes}.py (support),
              tests/{test_targets, test_domain, test_application, test_sealing, test_network, test_translation,
                     test_settings, test_migrations, test_repositories, test_service, test_openapi}.py
```

---

### Task 1: Kernel: a quotient rounded once (`Money.divided`)

**Files:**
- Modify: `libs/kernel/src/ledger_kernel/money.py`
- Test: `libs/kernel/tests/test_money_division.py`

**Interfaces:**
- Consumes: `Money`, `AmountProblem`, `InvalidAmountError` and the module's contexts (`_EXACT`) and checks (`_require_type`, `_FACTOR_TYPES`, `_operate`, `_MINOR_UNIT`), as Milestone 1 left them.
- Produces: `AmountProblem.NOT_A_DIVISOR`; `Money.divided(divisor: Decimal | int) -> Money`: `self / divisor` rounded once to the currency's minor unit, half away from zero (Decision 6). `InvalidAmountError` for a divisor that is zero, not finite or of another type, and for a quotient out of range.

Re-check before starting: `money.py` as it is on `main` (`_operate`, `_EXACT`, `_MINOR_UNIT`, `_FACTOR_TYPES` and `_require_type` by those names; Milestone 4 used `Money.rounded` only).

- [ ] **Step 1: Write the failing test**

`libs/kernel/tests/test_money_division.py`:

```python
import json
import operator
from decimal import Decimal
from fractions import Fraction

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ledger_kernel.money import Currency, InvalidAmountError, Money

amounts = st.decimals(min_value=Decimal("-1e15"), max_value=Decimal("1e15"), places=6)
magnitudes = st.decimals(min_value=Decimal("0.01"), max_value=Decimal(1_000_000), places=6)
divisors = st.tuples(magnitudes, st.booleans()).map(lambda drawn: -drawn[0] if drawn[1] else drawn[0])
"""Divisors from 0.01 up, of either sign: the quotient of the largest amount stays below 10^18."""


def dop(amount: str) -> Money:
    return Money.of(amount, Currency.DOP)


def half_away(exact: Fraction) -> Fraction:
    """``exact`` rounded to hundredths, half away from zero: the oracle, in rational arithmetic."""
    hundredths = exact * 100
    whole = int(abs(hundredths))
    if abs(hundredths) - whole >= Fraction(1, 2):
        whole += 1
    return Fraction(whole if hundredths >= 0 else -whole, 100)


@pytest.mark.parametrize(
    ("amount", "divisor", "expected"),
    [
        ("10", 3, "3.33"),
        ("0.05", 2, "0.03"),
        ("-0.05", 2, "-0.03"),
        ("1", Decimal(-8), "-0.13"),
        ("118", Decimal("1.18"), "100.00"),
        ("0.00", 7, "0.00"),
        ("1180.005", 10, "118.00"),
    ],
    ids=["third", "half-up", "half-away-negative", "negative-divisor", "tax-included", "zero", "below-half"],
)
def test_a_quotient_is_rounded_once_to_the_minor_unit(
    amount: str, divisor: int | Decimal, expected: str
) -> None:
    quotient = dop(amount).divided(divisor)
    assert quotient.amount.as_tuple() == Decimal(expected).as_tuple()
    assert quotient.currency is Currency.DOP


@given(amounts, divisors)
def test_the_quotient_is_the_exact_fraction_rounded_half_away_from_zero(
    amount: Decimal, divisor: Decimal
) -> None:
    quotient = Money(amount, Currency.DOP).divided(divisor)
    assert Fraction(quotient.amount) == half_away(Fraction(amount) / Fraction(divisor))
    assert quotient.amount.as_tuple().exponent == -2


def test_a_quotient_just_below_a_half_is_not_rounded_up_twice() -> None:
    # 0.0049999999999999999999999999999999999999999999999999999999999999 rounded to 60 digits is 0.005: a
    # quotient computed to some precision and rounded again would give 0.01.
    divisor = Decimal("200.00000000000000000000000000000000000000000000000000000001")
    assert dop("1").divided(divisor) == dop("0.00")


@pytest.mark.parametrize(
    "divisor", [0, Decimal(0), Decimal("NaN"), Decimal("Infinity")], ids=["int", "zero", "nan", "infinity"]
)
def test_what_is_no_divisor_is_refused(divisor: int | Decimal) -> None:
    with pytest.raises(InvalidAmountError, match="is not a divisor"):
        _ = dop("1").divided(divisor)


@pytest.mark.parametrize("payload", ["true", "1.5", '"2"', "null"], ids=["bool", "float", "text", "null"])
def test_a_divisor_of_another_type_is_refused(payload: str) -> None:
    # `methodcaller` takes untyped arguments, like a real boundary: `json.loads` hands over whatever was sent.
    divide = operator.methodcaller("divided", json.loads(payload))
    with pytest.raises(InvalidAmountError, match="unsupported type"):
        divide(dop("1"))


def test_a_quotient_out_of_range_is_an_error() -> None:
    with pytest.raises(InvalidAmountError, match="10\\^18"):
        _ = dop("999999999999999999").divided(Decimal("0.5"))
    with pytest.raises(InvalidAmountError, match="exactly"):
        _ = dop("999999999999999999").divided(Decimal("1E-50"))
```


- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest libs/kernel/tests/test_money_division.py -q --no-cov`
Expected: FAIL with `AttributeError: 'Money' object has no attribute 'divided'`.

- [ ] **Step 3: Implement**

In `libs/kernel/src/ledger_kernel/money.py`, `AmountProblem` gains, after `NOT_EXACT`:

```python
    NOT_A_DIVISOR = "is not a divisor: zero, or not finite"
```

and `Money` gains, after `rounded`:

```python
    def divided(self, divisor: Decimal | int) -> Money:
        """``self / divisor`` rounded once to the minor unit, half away from zero (``Money.rounded``'s rule).

        Exact: the quotient is never computed to some precision and rounded again (a double rounding can
        move a result that lies just below a half onto it). The amount is counted in minor units, divided
        with its remainder, and the remainder decides: half the divisor or more rounds away from zero.
        """
        _require_type(divisor, _FACTOR_TYPES)
        by = Decimal(divisor)
        if not by.is_finite() or by.is_zero():
            raise InvalidAmountError(AmountProblem.NOT_A_DIVISOR, str(by))
        minor = _MINOR_UNIT[self.currency]
        units = _operate(_EXACT.divide, self.amount, minor)
        try:
            whole, remainder = _EXACT.divmod(units, by)
            half_or_more = _EXACT.multiply(remainder.copy_abs(), Decimal(2)) >= by.copy_abs()
        except DecimalException as error:
            raise InvalidAmountError(AmountProblem.NOT_EXACT, f"{self.amount} and {by}") from error
        if half_or_more:
            away = Decimal(-1) if units.is_signed() != by.is_signed() else Decimal(1)
            whole = _operate(_EXACT.add, whole, away)
        return Money(_operate(_EXACT.multiply, whole, minor), self.currency)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest libs/kernel -q --no-cov`
Expected: PASS (the hypothesis law over amounts of up to 10^15 with six places and divisors from 0.01 to 10^6 of either sign).

- [ ] **Step 5: Gate**

Run: `uv run poe fmt && uv run poe check`
Expected: every step passes, coverage 100 %.

- [ ] **Step 6: Commit**

```bash
git add libs/kernel
git commit -m "feat(kernel): a quotient rounded once to the minor unit

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Contracts: the commercial document, the fiscal rules service, invoicing's events, fiscal-do's declined

**Files:**
- Create: `libs/contracts/proto/ledger/invoicing/v1/document.proto`, `libs/contracts/proto/ledger/fiscal/v1/fiscal_rules.proto`, `libs/contracts/proto/ledger/events/invoicing/v1/events.proto`, `libs/contracts/src/ledger_contracts/invoicing.py`, `libs/contracts/src/ledger_contracts/fiscal.py`
- Modify: `libs/contracts/proto/ledger/events/fiscal_do/v1/events.proto`, `libs/contracts/src/ledger_contracts/fiscal_do.py`
- Generate (committed): `libs/contracts/src/ledger/invoicing/v1/document_pb2{,_grpc}.py{,i}`, `libs/contracts/src/ledger/fiscal/v1/fiscal_rules_pb2{,_grpc}.py{,i}`, `libs/contracts/src/ledger/events/invoicing/v1/events_pb2{,_grpc}.py{,i}`, and `events/fiscal_do/v1` regenerated
- Test: `libs/contracts/tests/test_invoicing_contracts.py`

**Interfaces:**
- Consumes: `Envelope`, `ledger.common.v1.Money`; Milestone 4's `ledger.events.fiscal_do.v1` and `ledger_contracts.fiscal_do`.
- Produces: package `ledger.invoicing.v1`: `CommercialDocument` and its parts (`DocumentKind`, `Party`, `Address`, `ItemKind`, `TaxCategory`, `Tax`, `Withholding`, `Line`, `PaymentTermsKind`, `Terms`, `PaymentMethod`, `Payment`, `Correction`, `Reference`, `TaxGroup`, `Totals`). `ledger.fiscal.v1`: `FiscalRulesService.CheckDocument(CheckDocumentRequest) -> CheckDocumentResponse` (`applies`, `problems`, `omitted`), `Problem`. `ledger.events.invoicing.v1`: `DocumentIssued`, `DocumentVoided`, `FiscalMessage`, `DocumentFiscallyNumbered`, `DocumentFiscallyAccepted`, `DocumentFiscallyRejected`. `ledger.events.fiscal_do.v1.DocumentDeclined` (additive). `ledger_contracts.invoicing`: `STREAM = "INVOICING"`, `SUBJECTS`, one constant per subject. `ledger_contracts.fiscal`: `CHECK_DOCUMENT`, `IDEMPOTENT_METHODS`, `MAX_PROBLEMS`. `ledger_contracts.fiscal_do.DOCUMENT_DECLINED`.

Re-check before starting: how the landed contracts import generated modules (`import ledger.events.fiscal_do.v1.events_pb2 as wire`, as Milestone 4 does), and that `fiscal.document.>` is one of `FISCAL_DO`'s subject trees (Milestone 4, Decision 10): `fiscal.document.declined` lies in it, so the stream is unchanged.

- [ ] **Step 1: Write the failing test**

`libs/contracts/tests/test_invoicing_contracts.py`:

```python
from typing import Final

import pytest
lazy from google.protobuf.descriptor import Descriptor
lazy from google.protobuf.message import Message

import ledger.events.fiscal_do.v1.events_pb2 as fiscal_wire
import ledger.events.invoicing.v1.events_pb2 as wire
import ledger.fiscal.v1.fiscal_rules_pb2 as rules
import ledger.invoicing.v1.document_pb2 as document
from ledger.common.v1.money_pb2 import Money
from ledger_contracts import fiscal, fiscal_do, invoicing
from ledger_contracts.envelope import Envelope
from ledger_kernel.ids import CompanyId, OrganizationId, Tenant, new_id

TENANT: Final = Tenant(OrganizationId(new_id()), CompanyId(new_id()))
EVENTS: Final[tuple[Message, ...]] = (
    wire.DocumentIssued(),
    wire.DocumentVoided(),
    wire.DocumentFiscallyNumbered(),
    wire.DocumentFiscallyAccepted(),
    wire.DocumentFiscallyRejected(),
)
COUNTRY_WORDS: Final = ("dgii", "itbis", "ecf", "encf", "ncf", "rnc", "cedula")


def field_names(descriptor: Descriptor, seen: set[str] | None = None) -> set[str]:
    """Every field name of ``descriptor`` and of the messages it holds, recursively."""
    seen = set() if seen is None else seen
    if descriptor.full_name in seen:
        return set()
    seen.add(descriptor.full_name)
    names = {field.name for field in descriptor.fields}
    for field in descriptor.fields:
        if field.message_type is not None:
            names |= field_names(field.message_type, seen)
    return names


@pytest.mark.parametrize("event", EVENTS, ids=[type(event).__name__ for event in EVENTS])
def test_every_invoicing_event_is_a_message_of_the_versioned_events_package(event: Message) -> None:
    assert event.DESCRIPTOR.full_name == f"ledger.events.invoicing.v1.{type(event).__name__}"


def test_every_subject_lies_in_the_stream_s_tree() -> None:
    subjects = (
        invoicing.DOCUMENT_ISSUED,
        invoicing.DOCUMENT_VOIDED,
        invoicing.DOCUMENT_FISCALLY_NUMBERED,
        invoicing.DOCUMENT_FISCALLY_ACCEPTED,
        invoicing.DOCUMENT_FISCALLY_REJECTED,
    )
    (tree,) = invoicing.SUBJECTS
    assert all(subject.startswith(tree.removesuffix(">")) for subject in subjects)
    assert invoicing.STREAM == "INVOICING"
    assert fiscal_do.DOCUMENT_DECLINED.startswith("fiscal.document.")


def test_the_commercial_document_speaks_no_country_s_words() -> None:
    names = field_names(document.CommercialDocument.DESCRIPTOR)
    assert {"fiscal_fields", "prices_include_tax", "value_added_tax", "unit_price"} <= names
    assert not [name for name in names if any(word in name.split("_") for word in COUNTRY_WORDS)]


def test_an_issued_document_travels_whole_in_an_envelope_and_back() -> None:
    issued = wire.DocumentIssued(
        document=document.CommercialDocument(
            document_id=str(new_id()),
            organization_id=str(TENANT.organization),
            company_id=str(TENANT.company),
            kind=document.DOCUMENT_KIND_INVOICE,
            version=2,
            issue_date="2026-10-12",
            currency="DOP",
            lines=[
                document.Line(
                    number=1,
                    kind=document.ITEM_KIND_GOOD,
                    name="Item",
                    quantity="2",
                    unit_price="500.00",
                    tax=document.Tax(category=document.TAX_CATEGORY_STANDARD, rate="18"),
                    amount=Money(amount="1000.00", currency="DOP"),
                )
            ],
            totals=document.Totals(total=Money(amount="1180.00", currency="DOP")),
            fiscal_fields={"income_type": "01"},
        )
    )
    envelope = Envelope.wrap(issued, source="invoicing", tenant=TENANT)
    read = Envelope.from_headers(envelope.headers(), envelope.data)
    assert read.unwrap(wire.DocumentIssued) == issued
    assert read.type == "ledger.events.invoicing.v1.DocumentIssued"


def test_the_fiscal_rules_service_and_the_declined_event_have_their_names() -> None:
    service = rules.DESCRIPTOR.services_by_name["FiscalRulesService"]
    assert f"/{service.full_name}/CheckDocument" == fiscal.CHECK_DOCUMENT
    assert fiscal.IDEMPOTENT_METHODS == (fiscal.CHECK_DOCUMENT,)
    answer = rules.CheckDocumentResponse(
        applies=True, problems=[rules.Problem(field="lines[0]", code="x")], omitted=1
    )
    assert rules.CheckDocumentResponse.FromString(answer.SerializeToString()) == answer
    declined = fiscal_wire.DocumentDeclined(document_id=str(new_id()), version=1)
    assert declined.DESCRIPTOR.full_name == "ledger.events.fiscal_do.v1.DocumentDeclined"
```


- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest libs/contracts/tests/test_invoicing_contracts.py -q --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'ledger.events.invoicing'`.

- [ ] **Step 3: Implement**

`libs/contracts/proto/ledger/invoicing/v1/document.proto`:

```proto
syntax = "proto3";

package ledger.invoicing.v1;

import "ledger/common/v1/money.proto";

// A commercial document as invoicing issues it: what a country's fiscal service reads to make it a fiscal
// document. Country-agnostic: no field names a tax authority's code. What only one country needs travels in
// fiscal_fields, which invoicing keeps without reading. Ids are canonical UUIDs; dates are ISO 8601 calendar
// dates ("2026-10-12"); quantities, prices and rates are plain decimals ("1.5", "118.0000", "18"); every Money
// is in the document's currency.
message CommercialDocument {
  string document_id = 1;
  string organization_id = 2;
  string company_id = 3;
  DocumentKind kind = 4;
  // The document's version when it was issued (every change adds one).
  int64 version = 5;
  // identity's emission point the document is issued from.
  string emission_point_id = 6;
  string issue_date = 7;
  // ISO 4217.
  string currency = 8;
  // The lines' unit prices include their tax: each group's tax is taken out of its sum.
  bool prices_include_tax = 9;
  Party customer = 10;
  repeated Line lines = 11;
  Terms terms = 12;
  repeated Payment payments = 13;
  // Set for a credit or debit note only.
  Reference reference = 14;
  Totals totals = 15;
  // What one country's fiscal service needs and invoicing does not interpret (keys: lowercase snake case).
  map<string, string> fiscal_fields = 16;
  // The client's own reference for the document (an order number), if any.
  string external_reference = 17;
}

enum DocumentKind {
  DOCUMENT_KIND_UNSPECIFIED = 0;
  DOCUMENT_KIND_INVOICE = 1;
  DOCUMENT_KIND_CREDIT_NOTE = 2;
  DOCUMENT_KIND_DEBIT_NOTE = 3;
}

// Who buys. Every field may be empty: each country's rules say what a kind of document needs.
message Party {
  // The customer's tax identifier as its country writes it.
  string tax_id = 1;
  // An identifier for a customer without a tax id of the issuer's country (a passport, a foreign tax id).
  string foreign_id = 2;
  string name = 3;
  string email = 4;
  Address address = 5;
}

message Address {
  string line = 1;
  // The city or municipality in its country's official coding, if it has one.
  string city_code = 2;
  // The region or province in its country's official coding, if it has one.
  string region_code = 3;
  // ISO 3166-1 alpha-2.
  string country_code = 4;
}

enum ItemKind {
  ITEM_KIND_UNSPECIFIED = 0;
  ITEM_KIND_GOOD = 1;
  ITEM_KIND_SERVICE = 2;
}

// How a line is taxed, in words every value-added tax shares.
enum TaxCategory {
  TAX_CATEGORY_UNSPECIFIED = 0;
  // The general rate.
  TAX_CATEGORY_STANDARD = 1;
  // A reduced rate.
  TAX_CATEGORY_REDUCED = 2;
  // Taxed at zero (exports).
  TAX_CATEGORY_ZERO = 3;
  // Exempt by law.
  TAX_CATEGORY_EXEMPT = 4;
  // Outside the tax's scope (not billable).
  TAX_CATEGORY_OUTSIDE_SCOPE = 5;
}

message Tax {
  TaxCategory category = 1;
  // A percentage ("18", "16", "0").
  string rate = 2;
}

// What the customer withholds on the line and pays to the tax authority itself.
message Withholding {
  ledger.common.v1.Money value_added_tax = 1;
  ledger.common.v1.Money income_tax = 2;
}

message Line {
  // 1, 2, 3... in order.
  int32 number = 1;
  ItemKind kind = 2;
  string name = 3;
  string description = 4;
  string quantity = 5;
  // A unit of measure in its country's official coding, if any.
  string unit_code = 6;
  string unit_price = 7;
  // Absent when the line has no discount.
  ledger.common.v1.Money discount = 8;
  Tax tax = 9;
  Withholding withholding = 10;
  // Unit price x quantity - discount, rounded once to the currency's minor unit.
  ledger.common.v1.Money amount = 11;
}

enum PaymentTermsKind {
  PAYMENT_TERMS_KIND_UNSPECIFIED = 0;
  PAYMENT_TERMS_KIND_CASH = 1;
  PAYMENT_TERMS_KIND_CREDIT = 2;
  // Given free: not a sale.
  PAYMENT_TERMS_KIND_FREE = 3;
}

message Terms {
  PaymentTermsKind kind = 1;
  // When a sale on credit is due; empty otherwise.
  string due_date = 2;
}

enum PaymentMethod {
  PAYMENT_METHOD_UNSPECIFIED = 0;
  PAYMENT_METHOD_CASH = 1;
  PAYMENT_METHOD_BANK_TRANSFER = 2;
  PAYMENT_METHOD_CARD = 3;
  PAYMENT_METHOD_CREDIT = 4;
  PAYMENT_METHOD_VOUCHER = 5;
  PAYMENT_METHOD_BARTER = 6;
  PAYMENT_METHOD_CREDIT_NOTE = 7;
  PAYMENT_METHOD_OTHER = 8;
}

message Payment {
  PaymentMethod method = 1;
  ledger.common.v1.Money amount = 2;
}

// Why a note changes the document it refers to.
enum Correction {
  CORRECTION_UNSPECIFIED = 0;
  // The note cancels the document.
  CORRECTION_ANNULMENT = 1;
  // The note corrects text only, no amount.
  CORRECTION_TEXT = 2;
  // The note corrects amounts.
  CORRECTION_AMOUNTS = 3;
}

// The invoicing document a note refers to: one of the same company's.
message Reference {
  string document_id = 1;
  Correction correction = 2;
  string reason = 3;
}

// One group of lines taxed alike.
message TaxGroup {
  TaxCategory category = 1;
  string rate = 2;
  // What the group's tax applies to.
  ledger.common.v1.Money base = 3;
  ledger.common.v1.Money tax = 4;
}

message Totals {
  // The sum of the lines' amounts.
  ledger.common.v1.Money lines = 1;
  ledger.common.v1.Money discounts = 2;
  repeated TaxGroup groups = 3;
  ledger.common.v1.Money tax = 4;
  // The groups' bases and taxes: what the customer owes.
  ledger.common.v1.Money total = 5;
  ledger.common.v1.Money withheld_value_added_tax = 6;
  ledger.common.v1.Money withheld_income_tax = 7;
}
```

`libs/contracts/proto/ledger/fiscal/v1/fiscal_rules.proto`:

```proto
syntax = "proto3";

package ledger.fiscal.v1;

import "ledger/invoicing/v1/document.proto";

// What every country's fiscal service answers about a commercial document before it is issued: whether the
// document is the service's (its company is enrolled in that country's fiscal regime) and, when it is, every
// rule of the country it breaks. invoicing asks each fiscal service it knows before it issues, so that a local
// failure is answered to the client at once, before any fiscal number is taken (spec §5.1). Asking changes
// nothing: the method is idempotent.
service FiscalRulesService {
  rpc CheckDocument(CheckDocumentRequest) returns (CheckDocumentResponse);
}

message CheckDocumentRequest {
  ledger.invoicing.v1.CommercialDocument document = 1;
}

message CheckDocumentResponse {
  // False when the document's company is not the service's: its answer then says nothing more.
  bool applies = 1;
  // Every rule broken, at most 20, each where (a path into the request, "lines[2].tax") and which (a stable
  // code). Never a value the client sent.
  repeated Problem problems = 2;
  // How many more problems were found than are listed.
  int32 omitted = 3;
}

message Problem {
  string field = 1;
  string code = 2;
}
```

`libs/contracts/proto/ledger/events/invoicing/v1/events.proto`:

```proto
syntax = "proto3";

package ledger.events.invoicing.v1;

import "ledger/invoicing/v1/document.proto";

// What invoicing announces about commercial documents. Every event is scoped to its company and organization
// (the envelope carries both too) and carries the document's version after the change, which only grows.

// A document was issued: every country's fiscal service reads it whole. Subject invoicing.document.issued.
message DocumentIssued {
  ledger.invoicing.v1.CommercialDocument document = 1;
}

// A document was voided: it counts for nothing. Subject invoicing.document.voided.
message DocumentVoided {
  string document_id = 1;
  string organization_id = 2;
  string company_id = 3;
  ledger.invoicing.v1.DocumentKind kind = 4;
  int64 version = 5;
  string reason = 6;
}

// One message of a tax authority about a document, verbatim.
message FiscalMessage {
  string code = 1;
  string text = 2;
}

// The fiscal service gave the document its fiscal number: valid to hand to the customer while the tax
// authority decides. Subject invoicing.document.fiscally_numbered.
message DocumentFiscallyNumbered {
  string document_id = 1;
  string organization_id = 2;
  string company_id = 3;
  ledger.invoicing.v1.DocumentKind kind = 4;
  int64 version = 5;
  string fiscal_number = 6;
}

// The tax authority accepted the document, with observations when conditional. Subject
// invoicing.document.fiscally_accepted.
message DocumentFiscallyAccepted {
  string document_id = 1;
  string organization_id = 2;
  string company_id = 3;
  ledger.invoicing.v1.DocumentKind kind = 4;
  int64 version = 5;
  string fiscal_number = 6;
  bool conditional = 7;
  repeated FiscalMessage messages = 8;
}

// The tax authority rejected the document, or the fiscal service declined it before numbering it (then it
// has no fiscal number). Subject invoicing.document.fiscally_rejected.
message DocumentFiscallyRejected {
  string document_id = 1;
  string organization_id = 2;
  string company_id = 3;
  ledger.invoicing.v1.DocumentKind kind = 4;
  int64 version = 5;
  string fiscal_number = 6;
  bool declined = 7;
  repeated FiscalMessage messages = 8;
}
```

`libs/contracts/proto/ledger/events/fiscal_do/v1/events.proto` — append (field numbers new, nothing renumbered: `buf breaking` passes):

```proto
// fiscal-do declined to number a commercial document invoicing issued (a rule broken since invoicing checked
// it, no usable range, the company deactivated): no e-NCF was taken, and none will be. The document's id is
// invoicing's. Subject fiscal.document.declined.
message DocumentDeclined {
  string document_id = 1;
  string organization_id = 2;
  string company_id = 3;
  int64 version = 4;
  repeated DgiiMessage messages = 5;
}
```

`libs/contracts/src/ledger_contracts/invoicing.py`:

```python
"""invoicing's events on the wire: the subjects they are published on, and the stream that holds them.

Stream ``INVOICING`` holds ``invoicing.document.>``. Every country's fiscal service reads ``issued``; webhooks
reads them all, to tell clients.
"""

from typing import Final

STREAM: Final = "INVOICING"
SUBJECTS: Final = ("invoicing.document.>",)
DOCUMENT_ISSUED: Final = "invoicing.document.issued"
DOCUMENT_VOIDED: Final = "invoicing.document.voided"
DOCUMENT_FISCALLY_NUMBERED: Final = "invoicing.document.fiscally_numbered"
DOCUMENT_FISCALLY_ACCEPTED: Final = "invoicing.document.fiscally_accepted"
DOCUMENT_FISCALLY_REJECTED: Final = "invoicing.document.fiscally_rejected"
```

`libs/contracts/src/ledger_contracts/fiscal.py`:

```python
"""What every country's fiscal service answers over gRPC (``ledger.fiscal.v1.FiscalRulesService``)."""

from typing import Final

CHECK_DOCUMENT: Final = "/ledger.fiscal.v1.FiscalRulesService/CheckDocument"
IDEMPOTENT_METHODS: Final = (CHECK_DOCUMENT,)
"""Methods a caller may retry: asking changes nothing."""
MAX_PROBLEMS: Final = 20
"""The most problems an answer lists; the rest are counted in ``omitted``."""
```

`libs/contracts/src/ledger_contracts/fiscal_do.py` — append:

```python
DOCUMENT_DECLINED: Final = "fiscal.document.declined"
```

Run: `uv run poe proto && uv run poe proto-lint`
Expected: the generated files appear under `libs/contracts/src/ledger/invoicing/v1/`, `fiscal/v1/` and `events/invoicing/v1/`; `buf lint` passes (verified with `bufbuild/buf:1.73.0` on 2026-10-12).

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest libs/contracts -q --no-cov`
Expected: PASS. (CI's `buf breaking` against `main` passes too: every change is a new file or a new message.)

- [ ] **Step 5: Gate**

Run: `uv run poe fmt && uv run poe check`
Expected: every step passes, coverage 100 %.

- [ ] **Step 6: Commit**

```bash
git add libs/contracts
git commit -m "feat(contracts): the commercial document, fiscal rules over gRPC, invoicing's events

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: The invoicing package and its domain: values, form, totals, the document's life

**Files:**
- Create: `services/invoicing/pyproject.toml`, `services/invoicing/src/ledger_invoicing/__init__.py`, `py.typed`, `domain/__init__.py`, `domain/ids.py`, `domain/vocabulary.py`, `domain/problems.py`, `domain/content.py`, `domain/form.py`, `domain/totals.py`, `domain/fiscal.py`, `domain/events.py`, `domain/document.py`
- Modify: `pyproject.toml` (root)
- Test: `services/invoicing/tests/invoicing_samples.py` (support), `services/invoicing/tests/test_totals.py`, `services/invoicing/tests/test_form.py`, `services/invoicing/tests/test_document.py`

**Interfaces:**
- Consumes: `ledger_kernel.money` (`Money`, `Currency`, `Money.rounded`, `Money.divided` of Task 1), `ledger_kernel.ids` (`Tenant`, `new_id`), `ledger_kernel.errors`.
- Produces: `ids`: `DocumentId`, `EmissionPointId`. `vocabulary`: `DocumentKind`, `ItemKind`, `TaxCategory`, `UNTAXED`, `PaymentTerms`, `PaymentMethod`, `Correction`, `DocumentState`, `FiscalStatus`. `problems`: `MAX_PROBLEMS`, `ProblemCode`, `Problem(field, code)`, `ProblemsError` (`invoice_invalid`), `FiscalProblemsError` (`fiscal_invalid`), `require_no_problems`. `content`: `Address`, `Party`, `Tax`, `Withholding`, `Line`, `Terms`, `Payment`, `Reference`, `Content`. `form`: the limits (`MAX_LINES`, `MAX_PAYMENTS`, `MAX_FISCAL_FIELDS`, `MAX_PLACES`, `PRICE_CEILING`, `QUANTITY_CEILING`...), `places`, `form_problems(content)`, `totals_problems(content, totals)`, `checked(content) -> Totals`. `totals`: `gross`, `line_amount`, `base_of`, `tax_of`, `TaxGroup`, `Totals` (`zero`), `compute(content)`. `fiscal`: `MAX_MESSAGES`, `FiscalMessage` (`of`), `FiscalRecord` (`admits`), `NO_FISCAL_RECORD`. `events`: `DocumentIssued`, `DocumentVoided`, `DocumentFiscallyNumbered`, `DocumentFiscallyAccepted`, `DocumentFiscallyRejected`, `FiscalEvent`, `InvoicingEvent`. `document`: `TRANSITIONS`, `IllegalTransitionError` (`invoice_state_conflict`), `DocumentNotFoundError` (`invoice_not_found`), `require_move`, `Document` (`drafted`, `announcement`, `rewritten`, `issued`, `voided`, `fiscally_numbered`, `fiscally_accepted`, `fiscally_rejected`). Test support `invoicing_samples`: `TENANT`, `OTHER_TENANT`, `POINT`, `TODAY`, `NOW`, `STANDARD`, `EXEMPT`, `CUSTOMER`, `INVOICE`, `dop`, `line`, `note`, `draft`, `issued`.

Re-check before starting: `services/*` is a workspace member (Milestone 2 made it one); the root `pyproject.toml` as Milestones 2-4 left it (this task adds to each list, never replaces); Milestone 4's `samples`, `fakes`, `world` and `remotes` in `[tool.ruff.lint.isort] known-local-folder` (Decision 16: invoicing's support modules are prefixed).

- [ ] **Step 1: Package and repository settings**

`services/invoicing/pyproject.toml`:

```toml
[project]
name = "ledger-invoicing"
version = "0.1.0"
description = "The invoicing service: commercial documents, country-agnostic, from draft to their tax authority's answer."
requires-python = ">=3.15"
dependencies = ["ledger-kernel"]

[tool.uv.sources]
ledger-kernel = { workspace = true }

[build-system]
requires = ["uv_build>=0.13.0,<0.14.0"]
build-backend = "uv_build"
```

`services/invoicing/src/ledger_invoicing/__init__.py`:

```python
"""invoicing: commercial documents (invoices, credit and debit notes), country-agnostic."""
```

`services/invoicing/src/ledger_invoicing/domain/__init__.py`:

```python
"""invoicing's domain: the values, totals and life of a commercial document; standard library and kernel."""
```

and an empty `services/invoicing/src/ledger_invoicing/py.typed`.

Root `pyproject.toml`, as Milestones 2-4 did for their services:

- `[project] dependencies` gains `"ledger-invoicing"`; `[tool.uv.sources]` gains `ledger-invoicing = { workspace = true }`.
- ruff: `known-first-party` gains `"ledger_invoicing"`; `[tool.ruff.lint.isort] known-local-folder` gains `"invoicing_samples"`, `"invoicing_fakes"` and `"invoicing_remotes"`.
- basedpyright: an execution environment for the tests' plain modules, before the catch-all one:

```toml
[[tool.basedpyright.executionEnvironments]]
root = "services/invoicing/tests"
extraPaths = ["services/invoicing/tests"]
```

- pytest: `pythonpath` gains `"services/invoicing/tests"`.
- coverage: `source_pkgs` gains `"ledger_invoicing"`.
- poe: `mypy-invoicing = { cmd = "mypy services/invoicing" }` and `deps-invoicing = { cmd = "deptry services/invoicing/src --config services/invoicing/pyproject.toml" }`, each added to its sequence.
- import-linter: `"ledger_invoicing"` joins `root_packages`, the "Production code never imports test tooling" contract's `source_modules` and the "Libraries never import services" contract's `forbidden_modules`; the contract that keeps services apart gains it (if Milestone 4 left none, add `[[tool.importlinter.contracts]] name = "Services never import each other"`, `type = "independence"`, `modules` = every service package); and:

```toml
[[tool.importlinter.contracts]]
name = "invoicing is hexagonal"
type = "layers"
containers = ["ledger_invoicing"]
layers = ["domain"]

[[tool.importlinter.contracts]]
name = "invoicing's domain is standard library and kernel only"
type = "forbidden"
source_modules = ["ledger_invoicing.domain"]
forbidden_modules = ["ledger_platform", "ledger_contracts", "pydantic", "sqlalchemy", "fastapi", "grpc", "google", "httpx"]
```

(Each later task adds its layer to "invoicing is hexagonal"; Task 7 leaves it `["openapi", "main", "api", "resources", "adapters", "settings", "application", "domain"]`.)

Run: `uv lock && uv sync`

- [ ] **Step 2: Write the failing tests**

`services/invoicing/tests/invoicing_samples.py`:

```python
"""Documents the tests share: a company, an emission point, lines, and an invoice of each shape."""

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Final

from ledger_invoicing.domain.content import Address, Content, Line, Party, Payment, Reference, Tax, Terms
from ledger_invoicing.domain.document import Document
from ledger_invoicing.domain.ids import DocumentId, EmissionPointId
from ledger_invoicing.domain.totals import compute
from ledger_invoicing.domain.vocabulary import (
    Correction,
    DocumentKind,
    ItemKind,
    PaymentMethod,
    PaymentTerms,
    TaxCategory,
)
from ledger_kernel.ids import CompanyId, OrganizationId, Tenant, new_id
from ledger_kernel.money import Currency, Money

TENANT: Final = Tenant(OrganizationId(new_id()), CompanyId(new_id()))
OTHER_TENANT: Final = Tenant(TENANT.organization, CompanyId(new_id()))
POINT: Final = EmissionPointId(new_id())
TODAY: Final = date(2026, 10, 12)
NOW: Final = datetime(2026, 10, 12, 14, 30, tzinfo=UTC)
STANDARD: Final = Tax(TaxCategory.STANDARD, Decimal(18))
EXEMPT: Final = Tax(TaxCategory.EXEMPT, Decimal(0))
CUSTOMER: Final = Party(
    tax_id="101850043",
    name="Distribuidora del Caribe SRL",
    email="compras@example.com",
    address=Address(line="Av. Duarte 123", city_code="010101", region_code="010000", country_code="DO"),
)


def dop(amount: str) -> Money:
    return Money.of(amount, Currency.DOP)


def line(
    number: int = 1,
    *,
    quantity: str = "2",
    unit_price: str = "500.00",
    tax: Tax = STANDARD,
    discount: Money | None = None,
) -> Line:
    """A line of a good: 2 x 500.00 at 18 % unless told otherwise."""
    return Line(
        number=number,
        kind=ItemKind.GOOD,
        name=f"Item {number}",
        quantity=Decimal(quantity),
        unit_price=Decimal(unit_price),
        tax=tax,
        discount=discount,
    )


INVOICE: Final = Content(
    kind=DocumentKind.INVOICE,
    emission_point=POINT,
    issue_date=TODAY,
    currency=Currency.DOP,
    customer=CUSTOMER,
    lines=(line(1), line(2, quantity="1", unit_price="250.00", tax=EXEMPT)),
    terms=Terms(kind=PaymentTerms.CASH),
    payments=(Payment(PaymentMethod.CARD, dop("1430.00")),),
    fiscal_fields={"income_type": "01"},
    external_reference="ORDER-1",
)
"""1,000.00 at 18 % (180.00 of tax) and 250.00 exempt: 1,430.00 in all."""


def note(kind: DocumentKind, reference: DocumentId, *, total_line: str = "100.00") -> Content:
    """A note of ``kind`` correcting amounts of ``reference``: one line of ``total_line`` at 18 %."""
    return replace(
        INVOICE,
        kind=kind,
        lines=(line(1, quantity="1", unit_price=total_line),),
        payments=(),
        reference=Reference(document=reference, correction=Correction.AMOUNTS, reason="Price corrected"),
    )


def draft(content: Content = INVOICE, document: DocumentId | None = None) -> Document:
    """``content`` as a new draft."""
    return Document.drafted(
        document=document or DocumentId(new_id()),
        tenant=TENANT,
        content=content,
        totals=compute(content),
        now=NOW,
    )


def issued(content: Content = INVOICE, *, fiscal_applies: bool = True) -> Document:
    """``content`` drafted and issued."""
    document, _ = draft(content).issued(fiscal_applies=fiscal_applies, now=NOW)
    return document
```

`services/invoicing/tests/test_totals.py`:

```python
from dataclasses import replace
from decimal import Decimal
from fractions import Fraction
lazy from random import Random

from hypothesis import given
from hypothesis import strategies as st

from ledger_invoicing.domain.content import Tax, Withholding
from ledger_invoicing.domain.totals import compute
from ledger_invoicing.domain.vocabulary import TaxCategory
lazy from ledger_invoicing.domain.content import Content

from invoicing_samples import EXEMPT, INVOICE, STANDARD, dop, line

REDUCED = Tax(TaxCategory.REDUCED, Decimal(16))
taxes = st.sampled_from([STANDARD, REDUCED, EXEMPT, Tax(TaxCategory.ZERO, Decimal(0))])
quantities = st.decimals(min_value=Decimal("0.01"), max_value=Decimal(10_000), places=2)
prices = st.decimals(min_value=Decimal(0), max_value=Decimal(1_000_000), places=4)
lines = st.lists(st.tuples(quantities, prices, taxes), min_size=1, max_size=12)


def content_of(drawn: list[tuple[Decimal, Decimal, Tax]], *, includes_tax: bool = False) -> Content:
    return replace(
        INVOICE,
        lines=tuple(
            line(number, quantity=str(quantity), unit_price=str(price), tax=tax)
            for number, (quantity, price, tax) in enumerate(drawn, start=1)
        ),
        payments=(),
        prices_include_tax=includes_tax,
    )


def test_an_invoice_s_totals_are_its_groups_bases_and_taxes() -> None:
    totals = compute(INVOICE)
    assert totals.line_amounts == (dop("1000.00"), dop("250.00"))
    assert [(group.category, group.rate, group.base, group.tax) for group in totals.groups] == [
        (TaxCategory.STANDARD, Decimal(18), dop("1000.00"), dop("180.00")),
        (TaxCategory.EXEMPT, Decimal(0), dop("250.00"), dop("0.00")),
    ]
    assert (totals.lines, totals.tax, totals.total) == (dop("1250.00"), dop("180.00"), dop("1430.00"))
    assert totals.discounts == dop("0")
    assert totals.zero == dop("0")


def test_a_line_s_amount_is_rounded_once_half_away_from_zero_after_its_discount() -> None:
    priced = replace(INVOICE, lines=(line(1, quantity="3", unit_price="0.335", discount=dop("0.50")),))
    assert compute(priced).line_amounts == (dop("0.51"),)  # 1.005 - 0.50 = 0.505 -> 0.51


def test_prices_that_include_the_tax_give_a_base_without_it() -> None:
    included = replace(INVOICE, lines=(line(1, quantity="1", unit_price="118.00"),), prices_include_tax=True)
    totals = compute(included)
    assert (totals.groups[0].base, totals.groups[0].tax, totals.total) == (
        dop("100.00"),
        dop("18.00"),
        dop("118.00"),
    )


def test_rounding_the_base_and_the_tax_apart_can_leave_a_cent_off_the_price() -> None:
    # 0.10 including 18 %: base 0.0847... -> 0.08, tax 0.0144 -> 0.01, total 0.09. The documented points.
    included = replace(INVOICE, lines=(line(1, quantity="1", unit_price="0.10"),), prices_include_tax=True)
    assert compute(included).total == dop("0.09")


def test_the_same_rate_written_two_ways_is_one_group() -> None:
    written = replace(INVOICE, lines=(line(1), line(2, tax=Tax(TaxCategory.STANDARD, Decimal("18.00")))))
    (group,) = compute(written).groups
    assert (group.base, group.tax) == (dop("2000.00"), dop("360.00"))
    assert format(group.rate, "f") == "18"


def test_withholdings_and_discounts_are_summed_apart() -> None:
    held = replace(
        line(1, discount=dop("100.00")),
        withholding=Withholding(value_added_tax=dop("54.00"), income_tax=dop("10.00")),
    )
    totals = compute(replace(INVOICE, lines=(held, replace(line(2), withholding=Withholding()))))
    assert (totals.discounts, totals.withheld_value_added_tax, totals.withheld_income_tax) == (
        dop("100.00"),
        dop("54.00"),
        dop("10.00"),
    )


@given(drawn=lines, includes_tax=st.booleans())
def test_the_total_is_the_sum_of_its_parts(
    *, drawn: list[tuple[Decimal, Decimal, Tax]], includes_tax: bool
) -> None:
    totals = compute(content_of(drawn, includes_tax=includes_tax))
    bases = sum((group.base.amount for group in totals.groups), start=Decimal(0))
    assert totals.total.amount == bases + totals.tax.amount
    assert totals.lines.amount == sum((each.amount for each in totals.line_amounts), start=Decimal(0))
    for each in (*totals.line_amounts, totals.total, totals.tax):
        assert each.amount.as_tuple().exponent == -2


@given(lines)
def test_each_rounding_is_within_half_a_cent_of_the_exact_value(
    drawn: list[tuple[Decimal, Decimal, Tax]],
) -> None:
    totals = compute(content_of(drawn))
    for (quantity, price, _), each in zip(drawn, totals.line_amounts, strict=True):
        assert abs(Fraction(each.amount) - Fraction(quantity) * Fraction(price)) <= Fraction(1, 200)
    for group in totals.groups:
        exact = Fraction(group.base.amount) * Fraction(group.rate) / 100
        assert abs(Fraction(group.tax.amount) - exact) <= Fraction(1, 200)


@given(lines, st.randoms(use_true_random=False))
def test_a_document_s_totals_never_depend_on_its_lines_order(
    drawn: list[tuple[Decimal, Decimal, Tax]], random: Random
) -> None:
    shuffled = list(drawn)
    random.shuffle(shuffled)
    assert compute(content_of(shuffled)).total == compute(content_of(drawn)).total
```

`services/invoicing/tests/test_form.py`:

```python
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from ledger_invoicing.domain.content import (
    Address,
    Party,
    Payment,
    Reference,
    Tax,
    Terms,
    Withholding,
)
from ledger_invoicing.domain.form import MAX_FISCAL_FIELDS, MAX_LINES, MAX_PAYMENTS, checked, form_problems
from ledger_invoicing.domain.ids import DocumentId
from ledger_invoicing.domain.problems import MAX_PROBLEMS, FiscalProblemsError, Problem, ProblemsError
from ledger_invoicing.domain.vocabulary import (
    Correction,
    DocumentKind,
    PaymentMethod,
    PaymentTerms,
    TaxCategory,
)
from ledger_kernel.errors import ErrorKind
from ledger_kernel.ids import new_id
from ledger_kernel.money import Currency, Money
lazy from ledger_invoicing.domain.content import Content

from invoicing_samples import INVOICE, TODAY, dop, line, note

OTHER = DocumentId(new_id())


def problems_of(content: Content) -> set[tuple[str, str]]:
    with pytest.raises(ProblemsError) as caught:
        checked(content)
    return {(problem.field, problem.code) for problem in caught.value.problems}


def test_a_well_formed_invoice_passes_with_its_totals() -> None:
    assert checked(INVOICE).total == dop("1430.00")
    assert checked(replace(INVOICE, customer=None)).total == dop("1430.00")
    assert form_problems(note(DocumentKind.CREDIT_NOTE, OTHER)) == []


@pytest.mark.parametrize(
    ("changed", "expected"),
    [
        (replace(INVOICE, lines=()), ("lines", "lines_empty")),
        (replace(INVOICE, lines=(line(2),), payments=()), ("lines", "lines_not_numbered")),
        (
            replace(
                INVOICE, lines=tuple(line(n, unit_price="1") for n in range(1, MAX_LINES + 2)), payments=()
            ),
            ("lines", "too_many"),
        ),
        (
            replace(INVOICE, lines=(line(1, quantity="0"),), payments=()),
            ("lines[0].quantity", "quantity_not_positive"),
        ),
        (
            replace(INVOICE, lines=(line(1, quantity="10000000"),), payments=()),
            ("lines[0].quantity", "amount_too_large"),
        ),
        (
            replace(INVOICE, lines=(line(1, quantity="1.0000001"),), payments=()),
            ("lines[0].quantity", "amount_too_precise"),
        ),
        (
            replace(INVOICE, lines=(line(1, unit_price="-1"),), payments=()),
            ("lines[0].unit_price", "amount_negative"),
        ),
        (
            replace(INVOICE, lines=(line(1, unit_price="100000000000"),), payments=()),
            ("lines[0].unit_price", "amount_too_large"),
        ),
        (
            replace(INVOICE, lines=(line(1, tax=Tax(TaxCategory.STANDARD, Decimal(101))),), payments=()),
            ("lines[0].tax.rate", "rate_out_of_range"),
        ),
        (
            replace(INVOICE, lines=(line(1, tax=Tax(TaxCategory.EXEMPT, Decimal(18))),), payments=()),
            ("lines[0].tax.rate", "rate_out_of_range"),
        ),
        (
            replace(
                INVOICE, lines=(line(1, tax=Tax(TaxCategory.STANDARD, Decimal("18.0000001"))),), payments=()
            ),
            ("lines[0].tax.rate", "amount_too_precise"),
        ),
        (
            replace(INVOICE, lines=(line(1, discount=Money.of("1", Currency.USD)),), payments=()),
            ("lines[0].discount", "wrong_currency"),
        ),
        (
            replace(INVOICE, lines=(line(1, discount=dop("-1")),), payments=()),
            ("lines[0].discount", "amount_negative"),
        ),
        (
            replace(INVOICE, lines=(line(1, discount=dop("0.001")),), payments=()),
            ("lines[0].discount", "amount_too_precise"),
        ),
        (
            replace(
                INVOICE, lines=(replace(line(1), withholding=Withholding(income_tax=dop("-1"))),), payments=()
            ),
            ("lines[0].withholding.income_tax", "amount_negative"),
        ),
        (replace(INVOICE, lines=(replace(line(1), name=" "),), payments=()), ("lines[0].name", "text_blank")),
        (
            replace(INVOICE, lines=(replace(line(1), name="x" * 201),), payments=()),
            ("lines[0].name", "text_too_long"),
        ),
        (
            replace(INVOICE, lines=(replace(line(1), description="a\x00b"),), payments=()),
            ("lines[0].description", "text_control_characters"),
        ),
        (replace(INVOICE, customer=Party(name="")), ("customer.name", "text_blank")),
        (
            replace(INVOICE, customer=Party(address=Address(country_code="do"))),
            ("customer.address.country_code", "code_malformed"),
        ),
        (
            replace(INVOICE, terms=Terms(kind=PaymentTerms.CREDIT, due_date=TODAY - timedelta(days=1))),
            ("terms.due_date", "due_date_before_issue"),
        ),
        (
            replace(INVOICE, terms=Terms(kind=PaymentTerms.CASH, due_date=TODAY)),
            ("terms.due_date", "not_allowed"),
        ),
        (
            replace(INVOICE, payments=(Payment(PaymentMethod.CASH, dop("1")),) * (MAX_PAYMENTS + 1)),
            ("payments", "too_many"),
        ),
        (
            replace(INVOICE, payments=(Payment(PaymentMethod.CASH, Money.of("1", Currency.EUR)),)),
            ("payments[0].amount", "wrong_currency"),
        ),
        (replace(INVOICE, external_reference=""), ("external_reference", "text_blank")),
        (
            replace(INVOICE, fiscal_fields={f"field_{n}": "x" for n in range(MAX_FISCAL_FIELDS + 1)}),
            ("fiscal_fields", "too_many"),
        ),
        (replace(INVOICE, fiscal_fields={"Ecf-Type": "31"}), ("fiscal_fields", "field_name_malformed")),
        (replace(INVOICE, fiscal_fields={"ecf_type": " "}), ("fiscal_fields.ecf_type", "text_blank")),
        (
            replace(INVOICE, reference=Reference(document=OTHER, correction=Correction.AMOUNTS, reason="r")),
            ("reference", "not_allowed"),
        ),
        (replace(note(DocumentKind.DEBIT_NOTE, OTHER), reference=None), ("reference", "required")),
        (
            replace(
                note(DocumentKind.CREDIT_NOTE, OTHER),
                reference=Reference(document=OTHER, correction=Correction.AMOUNTS, reason=""),
            ),
            ("reference.reason", "text_blank"),
        ),
    ],
    ids=[
        "no-lines",
        "lines-misnumbered",
        "too-many-lines",
        "quantity-zero",
        "quantity-too-large",
        "quantity-too-precise",
        "price-negative",
        "price-too-large",
        "rate-over-100",
        "exempt-with-a-rate",
        "rate-too-precise",
        "discount-in-dollars",
        "discount-negative",
        "discount-in-thousandths",
        "withholding-negative",
        "name-blank",
        "name-too-long",
        "description-control-character",
        "customer-name-blank",
        "country-lowercase",
        "due-before-issue",
        "due-date-of-a-cash-sale",
        "too-many-payments",
        "payment-in-euros",
        "external-reference-blank",
        "too-many-fiscal-fields",
        "fiscal-field-name",
        "fiscal-field-blank",
        "invoice-with-reference",
        "note-without-reference",
        "note-without-reason",
    ],
)
def test_each_rule_of_form_names_where_and_what(changed: Content, expected: tuple[str, str]) -> None:
    assert expected in problems_of(changed)


@pytest.mark.parametrize(
    ("changed", "expected"),
    [
        (
            replace(INVOICE, lines=(line(1, discount=dop("1000.01")),), payments=()),
            ("lines[0].discount", "discount_exceeds_amount"),
        ),
        (
            replace(INVOICE, payments=(Payment(PaymentMethod.CASH, dop("1430.01")),)),
            ("payments", "payments_exceed_total"),
        ),
        (
            replace(
                note(DocumentKind.CREDIT_NOTE, OTHER),
                reference=Reference(document=OTHER, correction=Correction.TEXT, reason="Name corrected"),
            ),
            ("lines", "text_correction_has_amounts"),
        ),
    ],
    ids=["discount-over-the-line", "paid-over-the-total", "text-correction-with-amounts"],
)
def test_what_only_the_totals_tell_is_checked_once_the_form_holds(
    changed: Content, expected: tuple[str, str]
) -> None:
    assert problems_of(changed) == {expected}


def test_a_text_correction_of_zero_amounts_passes() -> None:
    corrected = replace(
        note(DocumentKind.CREDIT_NOTE, OTHER, total_line="0"),
        reference=Reference(document=OTHER, correction=Correction.TEXT, reason="Name corrected"),
    )
    assert checked(corrected).total == dop("0")


def test_every_problem_is_listed_up_to_twenty_and_the_rest_counted() -> None:
    nameless = tuple(replace(line(n), name=" ") for n in range(1, 31))
    with pytest.raises(ProblemsError) as caught:
        checked(replace(INVOICE, lines=nameless, payments=()))
    error = caught.value
    assert (len(error.problems), error.omitted) == (MAX_PROBLEMS, 10)
    assert error.kind is ErrorKind.INVALID
    assert error.code == "invoice_invalid"
    assert str(error).endswith("and 10 more")
    assert "Item" not in str(error)


def test_a_fiscal_service_s_problems_travel_in_the_same_shape() -> None:
    error = FiscalProblemsError((Problem("lines[0].tax", "tax_rate_not_in_force"),), omitted=3)
    assert (error.code, error.omitted) == ("fiscal_invalid", 3)
    assert str(error) == (
        "the document, for its country's fiscal rules, breaks rules: "
        "lines[0].tax: tax_rate_not_in_force and 3 more"
    )
```

`services/invoicing/tests/test_document.py`:

```python
from dataclasses import replace
from datetime import timedelta

import pytest

from ledger_invoicing.domain.document import (
    TRANSITIONS,
    DocumentNotFoundError,
    IllegalTransitionError,
    require_move,
)
from ledger_invoicing.domain.events import DocumentFiscallyAccepted, DocumentFiscallyRejected, DocumentIssued
from ledger_invoicing.domain.fiscal import MAX_MESSAGE_TEXT, MAX_MESSAGES, FiscalMessage
from ledger_invoicing.domain.totals import compute
from ledger_invoicing.domain.vocabulary import DocumentState, FiscalStatus
from ledger_kernel.errors import ErrorKind

from invoicing_samples import INVOICE, NOW, TENANT, draft, issued, line

LATER = NOW + timedelta(minutes=5)
ENCF = "E310000000001"
MESSAGE = FiscalMessage.of("1", "Aceptado")


def test_a_draft_is_version_one_and_says_nothing_fiscal() -> None:
    document = draft()
    assert (document.state, document.version, document.fiscal.status) == (
        DocumentState.DRAFT,
        1,
        FiscalStatus.NONE,
    )
    assert document.issued_at is None
    assert "Distribuidora" not in repr(document)


def test_a_draft_changes_freely_and_an_issued_document_never() -> None:
    changed = replace(INVOICE, lines=(line(1),), payments=())
    rewritten = draft().rewritten(changed, compute(changed), now=LATER)
    assert (rewritten.version, rewritten.updated_at, rewritten.totals.total) == (
        2,
        LATER,
        compute(changed).total,
    )
    with pytest.raises(IllegalTransitionError, match="issued cannot become draft"):
        issued().rewritten(changed, compute(changed), now=LATER)


def test_issuing_announces_the_document_whole() -> None:
    document, event = draft().issued(fiscal_applies=True, now=LATER)
    assert (document.state, document.version, document.issued_at) == (DocumentState.ISSUED, 2, LATER)
    assert document.fiscal.status is FiscalStatus.PENDING
    assert event == DocumentIssued(
        tenant=TENANT, document=document.id, version=2, content=document.content, totals=document.totals
    )
    without, _ = draft().issued(fiscal_applies=False, now=LATER)
    assert without.fiscal.status is FiscalStatus.NOT_APPLICABLE


def test_a_document_is_issued_once() -> None:
    with pytest.raises(IllegalTransitionError) as caught:
        issued().issued(fiscal_applies=True, now=LATER)
    assert (caught.value.kind, caught.value.code) == (ErrorKind.CONFLICT, "invoice_state_conflict")


def test_the_fiscal_life_is_mirrored_from_newer_events_only() -> None:
    numbered, event = issued().fiscally_numbered(ENCF, fiscal_version=1, now=LATER) or pytest.fail(
        "not newer"
    )
    assert (numbered.fiscal.status, numbered.fiscal.number, numbered.version) == (
        FiscalStatus.NUMBERED,
        ENCF,
        3,
    )
    assert event.fiscal_number == ENCF
    assert numbered.fiscally_numbered(ENCF, fiscal_version=1, now=LATER) is None
    accepted, _ = numbered.fiscally_accepted(
        ENCF, (MESSAGE,), conditional=False, fiscal_version=4, now=LATER
    ) or pytest.fail("not newer")
    assert (accepted.state, accepted.fiscal.status, accepted.fiscal.version) == (
        DocumentState.FISCALLY_ACCEPTED,
        FiscalStatus.ACCEPTED,
        4,
    )
    assert accepted.fiscally_numbered(ENCF, fiscal_version=1, now=LATER) is None


def test_an_acceptance_that_overtakes_the_numbering_is_applied_and_the_numbering_then_ignored() -> None:
    accepted, event = issued().fiscally_accepted(
        ENCF, (MESSAGE,), conditional=True, fiscal_version=5, now=LATER
    ) or pytest.fail("not newer")
    assert accepted.fiscal.status is FiscalStatus.CONDITIONALLY_ACCEPTED
    assert event == DocumentFiscallyAccepted(
        tenant=TENANT,
        document=accepted.id,
        kind=accepted.content.kind,
        version=accepted.version,
        fiscal_number=ENCF,
        conditional=True,
        messages=(MESSAGE,),
    )
    assert accepted.fiscally_numbered(ENCF, fiscal_version=1, now=LATER) is None
    assert accepted.fiscally_accepted(ENCF, (), conditional=True, fiscal_version=5, now=LATER) is None


def test_a_rejection_keeps_the_number_and_a_declining_has_none() -> None:
    many = tuple(FiscalMessage.of(str(n), "x" * (MAX_MESSAGE_TEXT + 1)) for n in range(MAX_MESSAGES + 5))
    rejected, event = issued().fiscally_rejected(ENCF, many, fiscal_version=3, now=LATER) or pytest.fail("x")
    assert (rejected.state, rejected.fiscal.status, rejected.fiscal.number) == (
        DocumentState.FISCALLY_REJECTED,
        FiscalStatus.REJECTED,
        ENCF,
    )
    assert len(rejected.fiscal.messages) == MAX_MESSAGES
    assert all(len(message.text) == MAX_MESSAGE_TEXT for message in rejected.fiscal.messages)
    assert not event.declined
    declined, declining = issued().fiscally_rejected(None, (), fiscal_version=1, now=LATER) or pytest.fail(
        "x"
    )
    assert (declined.fiscal.status, declined.fiscal.number) == (FiscalStatus.DECLINED, None)
    assert declining == DocumentFiscallyRejected(
        tenant=TENANT,
        document=declined.id,
        kind=declined.content.kind,
        version=declined.version,
        fiscal_number=None,
        declined=True,
        messages=(),
    )
    assert declined.fiscally_rejected(None, (), fiscal_version=1, now=LATER) is None


def test_a_draft_is_never_decided_by_a_tax_authority() -> None:
    with pytest.raises(IllegalTransitionError):
        draft().fiscally_numbered(ENCF, fiscal_version=1, now=LATER)
    with pytest.raises(IllegalTransitionError):
        draft().fiscally_accepted(ENCF, (), conditional=False, fiscal_version=2, now=LATER)


def test_what_may_be_voided() -> None:
    voided, event = draft().voided("Duplicated", now=LATER)
    assert (voided.state, voided.void_reason, event.reason, event.version) == (
        DocumentState.VOIDED,
        "Duplicated",
        "Duplicated",
        2,
    )
    rejected, _ = issued().fiscally_rejected(ENCF, (), fiscal_version=3, now=LATER) or pytest.fail("x")
    assert rejected.voided("Rejected by DGII", now=LATER)[0].state is DocumentState.VOIDED
    unserved = issued(fiscal_applies=False)
    assert unserved.voided("Wrong customer", now=LATER)[0].state is DocumentState.VOIDED


def test_what_may_never_be_voided() -> None:
    with pytest.raises(IllegalTransitionError, match="issued cannot become voided"):
        issued().voided("Too late", now=LATER)
    accepted, _ = issued().fiscally_accepted(
        ENCF, (), conditional=False, fiscal_version=4, now=LATER
    ) or pytest.fail("x")
    with pytest.raises(IllegalTransitionError, match="fiscally_accepted cannot become voided"):
        accepted.voided("Use a credit note", now=LATER)
    voided, _ = draft().voided("Once", now=LATER)
    with pytest.raises(IllegalTransitionError):
        voided.voided("Twice", now=LATER)


def test_the_machine_has_no_way_out_of_its_final_states() -> None:
    assert TRANSITIONS[DocumentState.FISCALLY_ACCEPTED] == frozenset()
    assert TRANSITIONS[DocumentState.VOIDED] == frozenset()
    require_move(DocumentState.DRAFT, DocumentState.ISSUED)
    with pytest.raises(IllegalTransitionError):
        require_move(DocumentState.VOIDED, DocumentState.DRAFT)


def test_a_missing_document_is_a_not_found() -> None:
    error = DocumentNotFoundError()
    assert (error.kind, error.code) == (ErrorKind.NOT_FOUND, "invoice_not_found")
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest services/invoicing/tests -q --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'ledger_invoicing.domain.content'`.

- [ ] **Step 4: Implement**

`services/invoicing/src/ledger_invoicing/domain/ids.py`:

```python
"""Identifiers of invoicing's own entities; organizations and companies use the kernel's."""

from typing import NewType
from uuid import UUID

DocumentId = NewType("DocumentId", UUID)
"""A commercial document: an invoice, a credit note or a debit note. Every fiscal service keeps it as the id
of the fiscal document it makes of it, so one id names the document everywhere."""
EmissionPointId = NewType("EmissionPointId", UUID)
"""An emission point of identity's (``identity`` owns it; invoicing keeps its id with each document)."""
```

`services/invoicing/src/ledger_invoicing/domain/vocabulary.py`:

```python
"""invoicing's words: kinds of document, how lines are taxed and paid, where a document stands.

Commercial words every country shares. No tax authority's code is spelled here: a country's fiscal service
translates these into its own (``fiscal-do`` maps ``TaxCategory.STANDARD`` at 18 % to DGII's ITBIS rate 1).
"""

from enum import StrEnum


class DocumentKind(StrEnum):
    """What a document is."""

    INVOICE = "invoice"
    CREDIT_NOTE = "credit_note"
    """Lowers what an invoice of the company's charged (or cancels it)."""
    DEBIT_NOTE = "debit_note"
    """Raises what an invoice of the company's charged."""


class ItemKind(StrEnum):
    """What a line sells."""

    GOOD = "good"
    SERVICE = "service"


class TaxCategory(StrEnum):
    """How a line is taxed, in the words every value-added tax shares."""

    STANDARD = "standard"
    REDUCED = "reduced"
    ZERO = "zero"
    """Taxed at zero (exports): the line is taxed, at 0 %."""
    EXEMPT = "exempt"
    OUTSIDE_SCOPE = "outside_scope"
    """Not subject to the tax at all."""


UNTAXED = frozenset({TaxCategory.ZERO, TaxCategory.EXEMPT, TaxCategory.OUTSIDE_SCOPE})
"""The categories whose rate is 0 by definition."""


class PaymentTerms(StrEnum):
    """When the customer pays."""

    CASH = "cash"
    CREDIT = "credit"
    FREE = "free"
    """Given free: not a sale."""


class PaymentMethod(StrEnum):
    """How the customer pays (part of) the document."""

    CASH = "cash"
    BANK_TRANSFER = "bank_transfer"
    CARD = "card"
    CREDIT = "credit"
    VOUCHER = "voucher"
    BARTER = "barter"
    CREDIT_NOTE = "credit_note"
    OTHER = "other"


class Correction(StrEnum):
    """Why a note changes the document it refers to."""

    ANNULMENT = "annulment"
    TEXT = "text"
    """Corrects text only: such a note carries no amount."""
    AMOUNTS = "amounts"


class DocumentState(StrEnum):
    """Where a document stands, commercially and as its tax authority decided."""

    DRAFT = "draft"
    """Being written: changed freely, issued when the client says so."""
    ISSUED = "issued"
    """Issued: unchangeable. Its fiscal service, if it has one, is taking it to its tax authority."""
    FISCALLY_ACCEPTED = "fiscally_accepted"
    FISCALLY_REJECTED = "fiscally_rejected"
    """The tax authority refused it, or its fiscal service declined it: it counts for nothing, and may be
    voided; the client issues a corrected document instead."""
    VOIDED = "voided"


class FiscalStatus(StrEnum):
    """What the document's fiscal service and tax authority have said, as invoicing mirrors it."""

    NONE = "none"
    """A draft: nothing to say yet."""
    NOT_APPLICABLE = "not_applicable"
    """Issued in a company no fiscal service serves: the document has no fiscal life."""
    PENDING = "pending"
    NUMBERED = "numbered"
    """It has its fiscal number: valid to hand to the customer while the tax authority decides."""
    ACCEPTED = "accepted"
    CONDITIONALLY_ACCEPTED = "conditionally_accepted"
    REJECTED = "rejected"
    DECLINED = "declined"
    """The fiscal service refused to number it: no fiscal number was taken."""
```

`services/invoicing/src/ledger_invoicing/domain/problems.py`:

```python
"""Why a document cannot be what it says, field by field, said without repeating what was sent.

A document is checked whole before anything is refused: every problem found is listed (at most
``MAX_PROBLEMS``), each naming where (``lines[2].unit_price``) and what (a stable code), so that a client
fixes them all at once. A message never quotes a value: names and tax ids are personal data. A country's
fiscal service answers its own problems in the same shape (``FiscalProblemsError``): its codes are its own.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar, Final, final

from ledger_kernel.errors import InvalidInputError

MAX_PROBLEMS: Final = 20


class ProblemCode(StrEnum):
    """Each rule of invoicing's a document can break; the value is the code a client matches on."""

    REQUIRED = "required"
    NOT_ALLOWED = "not_allowed"
    """This kind of document has no such field (a reference on an invoice)."""
    TEXT_BLANK = "text_blank"
    TEXT_TOO_LONG = "text_too_long"
    TEXT_CONTROL_CHARACTERS = "text_control_characters"
    CODE_MALFORMED = "code_malformed"
    """A country code that is not ISO 3166-1 alpha-2."""
    WRONG_CURRENCY = "wrong_currency"
    """An amount in another currency than the document's."""
    AMOUNT_NEGATIVE = "amount_negative"
    AMOUNT_TOO_PRECISE = "amount_too_precise"
    """More decimal places than the currency's minor unit (an amount) or than ``MAX_PLACES`` (a quantity, a
    unit price, a rate)."""
    AMOUNT_TOO_LARGE = "amount_too_large"
    QUANTITY_NOT_POSITIVE = "quantity_not_positive"
    RATE_OUT_OF_RANGE = "rate_out_of_range"
    """A tax rate below 0 or above 100, or not 0 in a category untaxed by definition."""
    DISCOUNT_EXCEEDS_AMOUNT = "discount_exceeds_amount"
    LINES_EMPTY = "lines_empty"
    LINES_NOT_NUMBERED = "lines_not_numbered"
    """Line numbers are 1, 2, 3... in order."""
    TOO_MANY = "too_many"
    DUE_DATE_BEFORE_ISSUE = "due_date_before_issue"
    PAYMENTS_EXCEED_TOTAL = "payments_exceed_total"
    TEXT_CORRECTION_HAS_AMOUNTS = "text_correction_has_amounts"
    FIELD_NAME_MALFORMED = "field_name_malformed"
    """A fiscal field's name is not lowercase snake case."""


@final
@dataclass(frozen=True, slots=True)
class Problem:
    """One rule broken, at one place of the document. ``code`` is invoicing's or a fiscal service's."""

    field: str
    code: str


class ProblemsError(InvalidInputError):
    """Something sent breaks rules field by field: every problem found, at most ``MAX_PROBLEMS``."""

    code: ClassVar[str] = "invoice_invalid"
    subject: ClassVar[str] = "the document"

    def __init__(self, problems: tuple[Problem, ...], *, omitted: int = 0) -> None:
        self.problems: tuple[Problem, ...] = problems[:MAX_PROBLEMS]
        self.omitted: int = max(len(problems) - MAX_PROBLEMS, 0) + omitted
        listed = ", ".join(f"{problem.field}: {problem.code}" for problem in self.problems)
        more = f" and {self.omitted} more" if self.omitted else ""
        super().__init__(f"{self.subject} breaks rules: {listed}{more}")


class FiscalProblemsError(ProblemsError):
    """A country's fiscal service found rules of its country the document breaks."""

    code: ClassVar[str] = "fiscal_invalid"
    subject: ClassVar[str] = "the document, for its country's fiscal rules,"


def require_no_problems(problems: list[Problem]) -> None:
    """Return when there is no problem; otherwise ``ProblemsError`` with every one."""
    if problems:
        raise ProblemsError(tuple(problems))
```

`services/invoicing/src/ledger_invoicing/domain/content.py`:

```python
"""What a commercial document says: its customer, lines, terms, payments, the document a note refers to.

Values, built by the API from what a client sent and checked whole by ``ledger_invoicing.domain.form``.
Amounts are ``Money``; quantities, unit prices and rates are ``Decimal``s. Names, addresses and tax ids are
personal data: no ``repr`` shows them. Nothing here is any country's: a customer's tax id is text its
country's fiscal service reads, and ``fiscal_fields`` carries what only one country needs, unread.
"""

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import final
lazy from collections.abc import Mapping
lazy from datetime import date
lazy from decimal import Decimal

lazy from ledger_invoicing.domain.ids import DocumentId, EmissionPointId
lazy from ledger_invoicing.domain.vocabulary import (
    Correction,
    DocumentKind,
    ItemKind,
    PaymentMethod,
    PaymentTerms,
    TaxCategory,
)
lazy from ledger_kernel.money import Currency, Money


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Address:
    """Where the customer is."""

    line: str | None = field(default=None, repr=False)
    city_code: str | None = None
    """The city or municipality in its country's official coding."""
    region_code: str | None = None
    country_code: str | None = None
    """ISO 3166-1 alpha-2."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Party:
    """Who buys. Every field is optional here; a country's rules say what each kind of document needs."""

    tax_id: str | None = field(default=None, repr=False)
    foreign_id: str | None = field(default=None, repr=False)
    name: str | None = field(default=None, repr=False)
    email: str | None = field(default=None, repr=False)
    address: Address | None = None


@final
@dataclass(frozen=True, slots=True)
class Tax:
    """How a line is taxed: a category and its rate, a percentage (``18``)."""

    category: TaxCategory
    rate: Decimal


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Withholding:
    """What the customer withholds on a line and pays to the tax authority itself."""

    value_added_tax: Money | None = None
    income_tax: Money | None = None


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Line:
    """One line of the document."""

    number: int
    kind: ItemKind
    name: str = field(repr=False)
    description: str | None = field(default=None, repr=False)
    quantity: Decimal
    unit_code: str | None = None
    unit_price: Decimal
    discount: Money | None = None
    tax: Tax
    withholding: Withholding | None = None


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Terms:
    """When the customer pays: at once, by ``due_date``, or never (a gift)."""

    kind: PaymentTerms
    due_date: date | None = None


@final
@dataclass(frozen=True, slots=True)
class Payment:
    """One way the document is paid, and how much."""

    method: PaymentMethod
    amount: Money


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Reference:
    """The document of the same company's a note refers to, and why."""

    document: DocumentId
    correction: Correction
    reason: str = field(repr=False)


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Content:
    """Everything a document says but its id, its state and its totals (which are computed from it)."""

    kind: DocumentKind
    emission_point: EmissionPointId
    issue_date: date
    currency: Currency
    prices_include_tax: bool = False
    customer: Party | None = None
    lines: tuple[Line, ...]
    terms: Terms
    payments: tuple[Payment, ...] = ()
    reference: Reference | None = None
    fiscal_fields: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    """What one country's fiscal service needs, kept and passed on unread (``ecf_type``, ``income_type``)."""
    external_reference: str | None = None
    """The client's own reference (an order number)."""
```

`services/invoicing/src/ledger_invoicing/domain/totals.py`:

```python
"""A document's totals: exact ``Money``, rounded at three documented points and nowhere else.

1. A line's amount is its unit price x its quantity - its discount, rounded once to the currency's minor
   unit (``Money.rounded``: half away from zero).
2. The lines taxed alike (one category at one rate) form a group, whose base is the sum of their amounts
   (exact); when the prices include the tax, the base is that sum / (1 + rate / 100), rounded once
   (``Money.divided``).
3. A group's tax is its base x rate / 100, rounded once (``Money.divided``).

Everything else is an exact sum: the total is the groups' bases plus their taxes, so the total is always the
sum of the parts a client sees. These are the points every value-added tax the platform plans for rounds at
(DGII's e-CF format, *Totales*); a country's fiscal service recomputes its own totals and refuses a document
whose total differs (``fiscal-do``'s ``totals_disagree``).
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Context, Decimal, DivisionByZero, Inexact, InvalidOperation, Overflow
from typing import Final, final
lazy from collections.abc import Iterable

from ledger_invoicing.domain.vocabulary import TaxCategory
from ledger_kernel.money import Money
lazy from ledger_invoicing.domain.content import Content, Line
lazy from ledger_kernel.money import Currency

HUNDRED: Final = Decimal(100)
_EXACT: Final = Context(
    prec=60, rounding=ROUND_HALF_UP, traps=[InvalidOperation, Overflow, DivisionByZero, Inexact]
)
"""Arithmetic on rates, never the ambient context: a rate has at most six places, so every result is exact."""
CATEGORY_ORDER: Final = tuple(TaxCategory)
"""Groups are listed in this order, then by rate: a document's totals never depend on its lines' order."""


def _sum(amounts: Iterable[Money], currency: Currency) -> Money:
    total = Money.zero(currency)
    for each in amounts:
        total += each
    return total


def gross(line: Line, currency: Currency) -> Money:
    """Unit price x quantity, exact."""
    return Money(line.unit_price, currency) * line.quantity


def line_amount(line: Line, currency: Currency) -> Money:
    """Rounding point 1: unit price x quantity - discount, to the minor unit."""
    discounted = gross(line, currency)
    if line.discount is not None:
        discounted -= line.discount
    return discounted.rounded()


def base_of(amounts: Money, rate: Decimal, *, includes_tax: bool) -> Money:
    """Rounding point 2: a group's base, its lines' sum without its tax when the prices include it."""
    if not includes_tax:
        return amounts
    return amounts.divided(_EXACT.add(Decimal(1), _EXACT.divide(rate, HUNDRED)))


def tax_of(base: Money, rate: Decimal) -> Money:
    """Rounding point 3: a group's tax, its base x rate / 100."""
    return (base * rate).divided(HUNDRED)


@final
@dataclass(frozen=True, slots=True)
class TaxGroup:
    """The lines taxed in one category at one rate: their base and their tax."""

    category: TaxCategory
    rate: Decimal
    base: Money
    tax: Money


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Totals:
    """A document's amounts: per line, per tax group, and in all."""

    line_amounts: tuple[Money, ...]
    lines: Money
    """The sum of the lines' amounts."""
    discounts: Money
    groups: tuple[TaxGroup, ...]
    tax: Money
    total: Money
    """The groups' bases plus their taxes: what the customer owes."""
    withheld_value_added_tax: Money
    withheld_income_tax: Money

    @property
    def zero(self) -> Money:
        """Nothing, in the document's currency."""
        return Money.zero(self.total.currency)


def compute(content: Content) -> Totals:
    """The totals of ``content``, whose values ``ledger_invoicing.domain.form`` found well formed."""
    currency = content.currency
    amounts = tuple(line_amount(line, currency) for line in content.lines)
    grouped: dict[tuple[TaxCategory, Decimal], list[Money]] = {}
    for line, each in zip(content.lines, amounts, strict=True):
        grouped.setdefault((line.tax.category, _EXACT.normalize(line.tax.rate)), []).append(each)
    groups: list[TaxGroup] = []
    for category, rate in sorted(grouped, key=lambda key: (CATEGORY_ORDER.index(key[0]), key[1])):
        base = base_of(_sum(grouped[category, rate], currency), rate, includes_tax=content.prices_include_tax)
        groups.append(TaxGroup(category, rate, base, tax_of(base, rate)))
    held = [line.withholding for line in content.lines if line.withholding is not None]
    tax = _sum((group.tax for group in groups), currency)
    return Totals(
        line_amounts=amounts,
        lines=_sum(amounts, currency),
        discounts=_sum((line.discount for line in content.lines if line.discount is not None), currency),
        groups=tuple(groups),
        tax=tax,
        total=_sum((group.base for group in groups), currency) + tax,
        withheld_value_added_tax=_sum(
            (each.value_added_tax for each in held if each.value_added_tax is not None), currency
        ),
        withheld_income_tax=_sum((each.income_tax for each in held if each.income_tax is not None), currency),
    )
```

`services/invoicing/src/ledger_invoicing/domain/form.py`:

```python
"""invoicing's own rules of a document, whatever its country: each value's form, and what each kind needs.

Texts are not blank, carry no control character and stay within their length; amounts are in the document's
currency, never negative and never finer than the currency's minor unit; quantities are above zero; unit
prices, quantities and rates have at most ``MAX_PLACES`` decimal places; lines are numbered 1, 2, 3...; a note
names the document it refers to and an invoice does not; payments never exceed the total. A country's
fiscal service checks its own, stricter rules afterwards (``FiscalProblemsError``).

Every check adds its problems to a list, so that a document's every problem is reported at once;
``checked`` runs them all and computes the totals.
"""

import re
import unicodedata
from decimal import Decimal
from typing import Final
lazy from collections.abc import Mapping

from ledger_invoicing.domain.problems import Problem, ProblemCode, require_no_problems
from ledger_invoicing.domain.totals import compute, gross
from ledger_invoicing.domain.vocabulary import UNTAXED, Correction, DocumentKind, PaymentTerms
lazy from ledger_invoicing.domain.content import Content, Line, Party
lazy from ledger_invoicing.domain.totals import Totals
lazy from ledger_kernel.money import Currency, Money

MAX_LINES: Final = 1000
MAX_PAYMENTS: Final = 10
MAX_FISCAL_FIELDS: Final = 16
MAX_PLACES: Final = 6
"""Decimal places of a quantity, a unit price or a rate: enough for every country this platform plans for
(DGII's prices have four)."""
PRICE_CEILING: Final = Decimal(10**11)
QUANTITY_CEILING: Final = Decimal(10**7)
"""A unit price stays below 10^11 and a quantity below 10^7, so their product stays below ``Money``'s 10^18
(a sum of lines that leaves that range is the kernel's ``InvalidAmountError``, never a silent rounding)."""
NAME_LENGTH: Final = 200
TEXT_LENGTH: Final = 1000
SHORT_LENGTH: Final = 64
FIELD_VALUE_LENGTH: Final = 200
HUNDRED: Final = Decimal(100)
_FIELD_NAME: Final = re.compile(r"[a-z][a-z0-9_]{0,39}", re.ASCII)
_COUNTRY: Final = re.compile(r"[A-Z]{2}", re.ASCII)
_MINOR_PLACES: Final = 2
"""Every currency the kernel knows (DOP, USD, EUR) has two."""

type Problems = list[Problem]


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


def amount(problems: Problems, where: str, value: Money | None, currency: Currency) -> None:
    """An amount in the document's currency: never negative, never finer than its minor unit."""
    if value is None:
        return
    if value.currency is not currency:
        problems.append(Problem(where, ProblemCode.WRONG_CURRENCY))
    elif value.amount < 0:
        problems.append(Problem(where, ProblemCode.AMOUNT_NEGATIVE))
    elif places(value.amount) > _MINOR_PLACES:
        problems.append(Problem(where, ProblemCode.AMOUNT_TOO_PRECISE))


def _decimal(problems: Problems, where: str, value: Decimal, *, ceiling: Decimal) -> None:
    if value < 0:
        problems.append(Problem(where, ProblemCode.AMOUNT_NEGATIVE))
    elif value >= ceiling:
        problems.append(Problem(where, ProblemCode.AMOUNT_TOO_LARGE))
    elif places(value) > MAX_PLACES:
        problems.append(Problem(where, ProblemCode.AMOUNT_TOO_PRECISE))


def _party(problems: Problems, party: Party) -> None:
    text(problems, "customer.tax_id", party.tax_id, limit=SHORT_LENGTH)
    text(problems, "customer.foreign_id", party.foreign_id, limit=SHORT_LENGTH)
    text(problems, "customer.name", party.name, limit=NAME_LENGTH)
    text(problems, "customer.email", party.email, limit=NAME_LENGTH)
    address = party.address
    if address is None:
        return
    text(problems, "customer.address.line", address.line, limit=TEXT_LENGTH)
    text(problems, "customer.address.city_code", address.city_code, limit=SHORT_LENGTH)
    text(problems, "customer.address.region_code", address.region_code, limit=SHORT_LENGTH)
    country = address.country_code
    if country is not None and _COUNTRY.fullmatch(country) is None:
        problems.append(Problem("customer.address.country_code", ProblemCode.CODE_MALFORMED))


def _line(problems: Problems, where: str, line: Line, currency: Currency) -> None:
    text(problems, f"{where}.name", line.name, limit=NAME_LENGTH)
    text(problems, f"{where}.description", line.description, limit=TEXT_LENGTH)
    text(problems, f"{where}.unit_code", line.unit_code, limit=SHORT_LENGTH)
    if line.quantity <= 0:
        problems.append(Problem(f"{where}.quantity", ProblemCode.QUANTITY_NOT_POSITIVE))
    else:
        _decimal(problems, f"{where}.quantity", line.quantity, ceiling=QUANTITY_CEILING)
    _decimal(problems, f"{where}.unit_price", line.unit_price, ceiling=PRICE_CEILING)
    amount(problems, f"{where}.discount", line.discount, currency)
    rate = line.tax.rate
    if not Decimal(0) <= rate <= HUNDRED or (line.tax.category in UNTAXED and rate != 0):
        problems.append(Problem(f"{where}.tax.rate", ProblemCode.RATE_OUT_OF_RANGE))
    elif places(rate) > MAX_PLACES:
        problems.append(Problem(f"{where}.tax.rate", ProblemCode.AMOUNT_TOO_PRECISE))
    held = line.withholding
    if held is not None:
        amount(problems, f"{where}.withholding.value_added_tax", held.value_added_tax, currency)
        amount(problems, f"{where}.withholding.income_tax", held.income_tax, currency)


def _lines(problems: Problems, content: Content) -> None:
    lines = content.lines
    if not lines:
        problems.append(Problem("lines", ProblemCode.LINES_EMPTY))
        return
    if len(lines) > MAX_LINES:
        problems.append(Problem("lines", ProblemCode.TOO_MANY))
        return
    if [line.number for line in lines] != list(range(1, len(lines) + 1)):
        problems.append(Problem("lines", ProblemCode.LINES_NOT_NUMBERED))
    for index, line in enumerate(lines):
        _line(problems, f"lines[{index}]", line, content.currency)


def _fiscal_fields(problems: Problems, fields: Mapping[str, str]) -> None:
    if len(fields) > MAX_FISCAL_FIELDS:
        problems.append(Problem("fiscal_fields", ProblemCode.TOO_MANY))
        return
    for name, value in fields.items():
        if _FIELD_NAME.fullmatch(name) is None:
            problems.append(Problem("fiscal_fields", ProblemCode.FIELD_NAME_MALFORMED))
        else:
            text(problems, f"fiscal_fields.{name}", value, limit=FIELD_VALUE_LENGTH)


def _kind(problems: Problems, content: Content) -> None:
    reference = content.reference
    if content.kind is DocumentKind.INVOICE:
        if reference is not None:
            problems.append(Problem("reference", ProblemCode.NOT_ALLOWED))
        return
    if reference is None:
        problems.append(Problem("reference", ProblemCode.REQUIRED))
        return
    text(problems, "reference.reason", reference.reason, limit=NAME_LENGTH)


def form_problems(content: Content) -> list[Problem]:
    """Every problem of ``content``'s form and of what its kind needs (see the module)."""
    problems: Problems = []
    _lines(problems, content)
    if content.customer is not None:
        _party(problems, content.customer)
    terms = content.terms
    if terms.due_date is not None and terms.due_date < content.issue_date:
        problems.append(Problem("terms.due_date", ProblemCode.DUE_DATE_BEFORE_ISSUE))
    if terms.kind is not PaymentTerms.CREDIT and terms.due_date is not None:
        problems.append(Problem("terms.due_date", ProblemCode.NOT_ALLOWED))
    if len(content.payments) > MAX_PAYMENTS:
        problems.append(Problem("payments", ProblemCode.TOO_MANY))
    for index, payment in enumerate(content.payments):
        amount(problems, f"payments[{index}].amount", payment.amount, content.currency)
    text(problems, "external_reference", content.external_reference, limit=SHORT_LENGTH)
    _fiscal_fields(problems, content.fiscal_fields)
    _kind(problems, content)
    return problems


def totals_problems(content: Content, totals: Totals) -> list[Problem]:
    """What only the totals tell: a discount over its line, payments over the total, a text note's amounts."""
    problems: Problems = []
    for index, line in enumerate(content.lines):
        if line.discount is not None and line.discount > gross(line, content.currency):
            problems.append(Problem(f"lines[{index}].discount", ProblemCode.DISCOUNT_EXCEEDS_AMOUNT))
    if content.payments:
        paid = sum((payment.amount for payment in content.payments), start=totals.zero)
        if paid > totals.total:
            problems.append(Problem("payments", ProblemCode.PAYMENTS_EXCEED_TOTAL))
    reference = content.reference
    if reference is not None and reference.correction is Correction.TEXT and totals.total.amount != 0:
        problems.append(Problem("lines", ProblemCode.TEXT_CORRECTION_HAS_AMOUNTS))
    return problems


def checked(content: Content) -> Totals:
    """``content``'s totals once its form and its totals hold; ``ProblemsError`` with every problem otherwise.

    The form first: totals are computed only from well-formed values (one currency, no negative amount).
    """
    require_no_problems(form_problems(content))
    totals = compute(content)
    require_no_problems(totals_problems(content, totals))
    return totals
```

`services/invoicing/src/ledger_invoicing/domain/fiscal.py`:

```python
"""What a document's fiscal service and tax authority said of it, as invoicing mirrors it from their events.

The fiscal service's events carry the fiscal document's version, which only grows: invoicing keeps the last
one it applied and ignores any event at or below it, so a redelivered or late event (a ``numbered`` that
arrives after the ``accepted`` it preceded) changes nothing, and the mirror never needs the events in order.
"""

from dataclasses import dataclass, field
from typing import Final, final

from ledger_invoicing.domain.vocabulary import FiscalStatus

MAX_MESSAGES: Final = 20
"""A tax authority's messages kept per document; DGII sends a handful."""
MAX_MESSAGE_TEXT: Final = 500
MAX_MESSAGE_CODE: Final = 64


@final
@dataclass(frozen=True, slots=True)
class FiscalMessage:
    """One message of a tax authority, verbatim (cut at ``MAX_MESSAGE_TEXT`` characters)."""

    code: str
    text: str

    @classmethod
    def of(cls, code: str, text: str) -> FiscalMessage:
        """A message as invoicing keeps it: its code and its text, each cut to its bound."""
        return cls(code[:MAX_MESSAGE_CODE], text[:MAX_MESSAGE_TEXT])


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class FiscalRecord:
    """The fiscal side of a document: its status, its fiscal number once it has one, the authority's words."""

    status: FiscalStatus
    number: str | None = None
    messages: tuple[FiscalMessage, ...] = field(default=())
    version: int = 0
    """The fiscal document's version last applied; 0 before any."""

    def admits(self, version: int) -> bool:
        """Whether an event of the fiscal document's ``version`` is newer than what is mirrored."""
        return version > self.version


NO_FISCAL_RECORD: Final = FiscalRecord(status=FiscalStatus.NONE)
"""A draft's: nothing issued, nothing to mirror."""
```

`services/invoicing/src/ledger_invoicing/domain/events.py`:

```python
"""What invoicing announces: a document issued (whole), voided, and its fiscal life as its service tells it.

Each event names the document's tenant and carries the document's version after the change. The adapters put
them on the wire (``ledger.events.invoicing.v1``) through the outbox, in the transaction of the change.
"""

from dataclasses import dataclass, field
from typing import final

lazy from ledger_invoicing.domain.content import Content
lazy from ledger_invoicing.domain.fiscal import FiscalMessage
lazy from ledger_invoicing.domain.ids import DocumentId
lazy from ledger_invoicing.domain.totals import Totals
lazy from ledger_invoicing.domain.vocabulary import DocumentKind
lazy from ledger_kernel.ids import Tenant


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class DocumentIssued:
    """A document was issued: what every fiscal service reads to make a fiscal document of it."""

    tenant: Tenant
    document: DocumentId
    version: int
    content: Content = field(repr=False)
    totals: Totals = field(repr=False)


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class DocumentVoided:
    """A document was voided: it counts for nothing."""

    tenant: Tenant
    document: DocumentId
    kind: DocumentKind
    version: int
    reason: str = field(repr=False)


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class DocumentFiscallyNumbered:
    """The document has its fiscal number."""

    tenant: Tenant
    document: DocumentId
    kind: DocumentKind
    version: int
    fiscal_number: str


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class DocumentFiscallyAccepted:
    """The tax authority accepted the document (with observations when ``conditional``)."""

    tenant: Tenant
    document: DocumentId
    kind: DocumentKind
    version: int
    fiscal_number: str
    conditional: bool
    messages: tuple[FiscalMessage, ...]


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class DocumentFiscallyRejected:
    """The tax authority rejected the document, or its fiscal service declined it (``declined``)."""

    tenant: Tenant
    document: DocumentId
    kind: DocumentKind
    version: int
    fiscal_number: str | None
    declined: bool
    messages: tuple[FiscalMessage, ...]


type FiscalEvent = DocumentFiscallyNumbered | DocumentFiscallyAccepted | DocumentFiscallyRejected
type InvoicingEvent = DocumentIssued | DocumentVoided | FiscalEvent
```

`services/invoicing/src/ledger_invoicing/domain/document.py`:

```python
"""The commercial document: an aggregate with an explicit state machine.

::

    draft ──issue──► issued ──(fiscal service)──► fiscally_accepted
      │                │    └─(fiscal service)──► fiscally_rejected ──void──► voided
      └──void──► voided └─void, only when no fiscal service serves the company──► voided

A draft changes freely; an issued document never changes again but by its fiscal life, which invoicing
mirrors from its fiscal service's events (``ledger_invoicing.domain.fiscal``). An accepted document is never
voided: a credit note corrects it, as tax authorities require. Every change adds one to ``version``, the
``ETag`` an update names in ``If-Match``. A move the machine does not allow is ``IllegalTransitionError``.
"""

from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import ClassVar, Final, Self, final
lazy from collections.abc import Mapping
lazy from datetime import datetime

from ledger_invoicing.domain.events import (
    DocumentFiscallyAccepted,
    DocumentFiscallyNumbered,
    DocumentFiscallyRejected,
    DocumentIssued,
    DocumentVoided,
)
from ledger_invoicing.domain.fiscal import MAX_MESSAGES, NO_FISCAL_RECORD, FiscalRecord
from ledger_invoicing.domain.vocabulary import DocumentState, FiscalStatus
from ledger_kernel.errors import ConflictError, NotFoundError
lazy from ledger_invoicing.domain.content import Content
lazy from ledger_invoicing.domain.fiscal import FiscalMessage
lazy from ledger_invoicing.domain.ids import DocumentId
lazy from ledger_invoicing.domain.totals import Totals
lazy from ledger_kernel.ids import Tenant

TRANSITIONS: Final[Mapping[DocumentState, frozenset[DocumentState]]] = MappingProxyType(
    {
        DocumentState.DRAFT: frozenset({DocumentState.ISSUED, DocumentState.VOIDED}),
        DocumentState.ISSUED: frozenset(
            {DocumentState.FISCALLY_ACCEPTED, DocumentState.FISCALLY_REJECTED, DocumentState.VOIDED}
        ),
        DocumentState.FISCALLY_ACCEPTED: frozenset(),
        DocumentState.FISCALLY_REJECTED: frozenset({DocumentState.VOIDED}),
        DocumentState.VOIDED: frozenset(),
    }
)
"""Every move the machine allows. ``issued → voided`` holds only without a fiscal service (``voided``)."""


class IllegalTransitionError(ConflictError):
    """A move the document's state forbids: an accepted document voided, a draft decided by its authority."""

    code: ClassVar[str] = "invoice_state_conflict"

    def __init__(self, current: DocumentState, wanted: DocumentState) -> None:
        super().__init__(f"a document {current} cannot become {wanted}")


class DocumentNotFoundError(NotFoundError):
    """The company has no such document."""

    code: ClassVar[str] = "invoice_not_found"

    def __init__(self) -> None:
        super().__init__("the company has no such document")


def require_move(current: DocumentState, wanted: DocumentState) -> None:
    """Return when the machine allows ``current → wanted``; ``IllegalTransitionError`` otherwise."""
    if wanted not in TRANSITIONS[current]:
        raise IllegalTransitionError(current, wanted)


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Document:
    """An invoice, a credit note or a debit note of one company (see the module)."""

    id: DocumentId
    tenant: Tenant
    content: Content = field(repr=False)
    totals: Totals = field(repr=False)
    state: DocumentState
    fiscal: FiscalRecord
    version: int
    created_at: datetime
    updated_at: datetime
    issued_at: datetime | None = None
    void_reason: str | None = field(default=None, repr=False)

    @classmethod
    def drafted(
        cls, *, document: DocumentId, tenant: Tenant, content: Content, totals: Totals, now: datetime
    ) -> Self:
        """A new draft, version 1 (issuing it at once makes it version 2)."""
        return cls(
            id=document,
            tenant=tenant,
            content=content,
            totals=totals,
            state=DocumentState.DRAFT,
            fiscal=NO_FISCAL_RECORD,
            version=1,
            created_at=now,
            updated_at=now,
        )

    def announcement(self) -> DocumentIssued:
        """What issuing this document announces: the document whole."""
        return DocumentIssued(
            tenant=self.tenant,
            document=self.id,
            version=self.version,
            content=self.content,
            totals=self.totals,
        )

    def rewritten(self, content: Content, totals: Totals, *, now: datetime) -> Self:
        """The draft with new content; an issued document never changes (``IllegalTransitionError``)."""
        if self.state is not DocumentState.DRAFT:
            raise IllegalTransitionError(self.state, DocumentState.DRAFT)
        return replace(self, content=content, totals=totals, version=self.version + 1, updated_at=now)

    def issued(self, *, fiscal_applies: bool, now: datetime) -> tuple[Self, DocumentIssued]:
        """The draft issued, and its announcement. ``fiscal_applies``: a fiscal service serves the company."""
        require_move(self.state, DocumentState.ISSUED)
        issued = replace(
            self,
            state=DocumentState.ISSUED,
            fiscal=_issued_record(fiscal_applies=fiscal_applies),
            issued_at=now,
            version=self.version + 1,
            updated_at=now,
        )
        return issued, issued.announcement()

    def voided(self, reason: str, *, now: datetime) -> tuple[Self, DocumentVoided]:
        """The document voided, and its announcement.

        A draft, a document its tax authority rejected, or an issued one no fiscal service serves; never an
        accepted one, nor one whose fiscal answer is pending.
        """
        if self.state is DocumentState.ISSUED and self.fiscal.status is not FiscalStatus.NOT_APPLICABLE:
            raise IllegalTransitionError(self.state, DocumentState.VOIDED)
        require_move(self.state, DocumentState.VOIDED)
        voided = replace(
            self, state=DocumentState.VOIDED, void_reason=reason, version=self.version + 1, updated_at=now
        )
        event = DocumentVoided(
            tenant=self.tenant,
            document=self.id,
            kind=self.content.kind,
            version=voided.version,
            reason=reason,
        )
        return voided, event

    def _mirrored(self, now: datetime, fiscal: FiscalRecord, state: DocumentState) -> Self:
        if state is not self.state:
            require_move(self.state, state)
        return replace(self, fiscal=fiscal, state=state, version=self.version + 1, updated_at=now)

    def fiscally_numbered(
        self, number: str, *, fiscal_version: int, now: datetime
    ) -> tuple[Self, DocumentFiscallyNumbered] | None:
        """The document with its fiscal number; ``None`` when the event is not newer than the mirror."""
        if not self.fiscal.admits(fiscal_version):
            return None
        if self.state is not DocumentState.ISSUED:
            raise IllegalTransitionError(self.state, DocumentState.ISSUED)
        record = FiscalRecord(status=FiscalStatus.NUMBERED, number=number, version=fiscal_version)
        numbered = self._mirrored(now, record, DocumentState.ISSUED)
        event = DocumentFiscallyNumbered(
            tenant=self.tenant,
            document=self.id,
            kind=self.content.kind,
            version=numbered.version,
            fiscal_number=number,
        )
        return numbered, event

    def fiscally_accepted(
        self,
        number: str,
        messages: tuple[FiscalMessage, ...],
        *,
        conditional: bool,
        fiscal_version: int,
        now: datetime,
    ) -> tuple[Self, DocumentFiscallyAccepted] | None:
        """The document its tax authority accepted; ``None`` for an event no newer than the mirror."""
        if not self.fiscal.admits(fiscal_version):
            return None
        status = FiscalStatus.CONDITIONALLY_ACCEPTED if conditional else FiscalStatus.ACCEPTED
        kept = messages[:MAX_MESSAGES]
        record = FiscalRecord(status=status, number=number, messages=kept, version=fiscal_version)
        accepted = self._mirrored(now, record, DocumentState.FISCALLY_ACCEPTED)
        event = DocumentFiscallyAccepted(
            tenant=self.tenant,
            document=self.id,
            kind=self.content.kind,
            version=accepted.version,
            fiscal_number=number,
            conditional=conditional,
            messages=kept,
        )
        return accepted, event

    def fiscally_rejected(
        self,
        number: str | None,
        messages: tuple[FiscalMessage, ...],
        *,
        fiscal_version: int,
        now: datetime,
    ) -> tuple[Self, DocumentFiscallyRejected] | None:
        """The document rejected (declined, without a ``number``); ``None`` for an event not newer."""
        if not self.fiscal.admits(fiscal_version):
            return None
        status = FiscalStatus.REJECTED if number is not None else FiscalStatus.DECLINED
        kept = messages[:MAX_MESSAGES]
        record = FiscalRecord(status=status, number=number, messages=kept, version=fiscal_version)
        rejected = self._mirrored(now, record, DocumentState.FISCALLY_REJECTED)
        event = DocumentFiscallyRejected(
            tenant=self.tenant,
            document=self.id,
            kind=self.content.kind,
            version=rejected.version,
            fiscal_number=number,
            declined=number is None,
            messages=kept,
        )
        return rejected, event


def _issued_record(*, fiscal_applies: bool) -> FiscalRecord:
    """A just-issued document's fiscal side: pending when a fiscal service serves its company."""
    return FiscalRecord(status=FiscalStatus.PENDING if fiscal_applies else FiscalStatus.NOT_APPLICABLE)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest services/invoicing/tests -q --no-cov`
Expected: PASS (59 tests, the hypothesis laws of the totals among them).

- [ ] **Step 6: Gate**

Run: `uv run poe fmt && uv run poe check`
Expected: every step passes, coverage 100 %.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock services/invoicing
git commit -m "feat(invoicing): commercial documents, exact totals and their life, country-agnostic

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: invoicing's use cases and their ports

**Files:**
- Create: `services/invoicing/src/ledger_invoicing/application/__init__.py`, `application/ports.py`, `application/commands.py`, `application/outcomes.py`, `application/queries.py`
- Modify: `pyproject.toml` (root: "invoicing is hexagonal" `layers = ["application", "domain"]`)
- Test: `services/invoicing/tests/invoicing_fakes.py` (support), `services/invoicing/tests/test_application.py`

**Interfaces:**
- Consumes: Task 3's domain; `ledger_kernel.pagination` (`Page`, `PageRequest`, `page_of`).
- Produces: `ports`: `Documents` (`add`, `get`, `locked`, `update`, `credited`, `page`), `Events` (`publish`), `InvoicingUnit(tenant, documents, events, now)`, `Units` (`unit(tenant)`), `FiscalVerdict(applies, problems, omitted)` with `require_no_problems`, `FiscalRules` (`check(issued)`), `ChangedMeanwhileError` (`invoice_changed`). `commands`: `Admits`, `StaleVersionError` (`precondition_failed`), `ReferenceInvalidError` (`reference_invalid`), `CreditExceedsOriginalError` (`credit_exceeds_original`), `verdict(rules, draft) -> FiscalVerdict | None`, `create(unit, draft, judged)`, `rewrite(unit, document, content, totals, *, admits)`, `issue(unit, draft, judged, *, admits)`, `void(unit, document, reason, *, admits)`. `outcomes`: `Numbered`, `Decided`, `Outcome`, `mirror(unit, outcome) -> Document | None`. `queries`: `document(unit, id)`, `documents(unit, page)`. Test support `invoicing_fakes`: `MemoryDocuments`, `RecordedEvents`, `MemoryUnits`, `ScriptedRules`.

- [ ] **Step 1: Write the failing tests**

`services/invoicing/tests/invoicing_fakes.py`:

```python
"""In-memory stand-ins of invoicing's ports, for the use cases' tests (PostgreSQL's are tested apart)."""

from contextlib import asynccontextmanager
from typing import final, override
lazy from collections.abc import AsyncGenerator
lazy from datetime import datetime

from ledger_invoicing.application.ports import Documents, Events, FiscalRules, InvoicingUnit, Units
from ledger_invoicing.domain.vocabulary import DocumentKind, DocumentState
from ledger_kernel.pagination import page_of
lazy from ledger_invoicing.application.ports import FiscalVerdict
lazy from ledger_invoicing.domain.document import Document
lazy from ledger_invoicing.domain.events import DocumentIssued, InvoicingEvent
lazy from ledger_invoicing.domain.ids import DocumentId
lazy from ledger_kernel.ids import Tenant
lazy from ledger_kernel.money import Money
lazy from ledger_kernel.pagination import Page, PageRequest

_UNCOUNTED = frozenset({DocumentState.DRAFT, DocumentState.VOIDED, DocumentState.FISCALLY_REJECTED})


@final
class MemoryDocuments(Documents):
    """One company's documents, in a dict."""

    def __init__(self) -> None:
        self.kept: dict[DocumentId, Document] = {}
        self.locks: list[DocumentId] = []

    @override
    async def add(self, document: Document) -> None:
        assert document.id not in self.kept
        self.kept[document.id] = document

    @override
    async def get(self, document: DocumentId) -> Document | None:
        return self.kept.get(document)

    @override
    async def locked(self, document: DocumentId) -> Document | None:
        self.locks.append(document)
        return self.kept.get(document)

    @override
    async def update(self, document: Document) -> None:
        assert self.kept[document.id].version == document.version - 1
        self.kept[document.id] = document

    @override
    async def credited(self, document: DocumentId) -> Money | None:
        notes = [
            each.totals.total
            for each in self.kept.values()
            if each.content.kind is DocumentKind.CREDIT_NOTE
            and each.content.reference is not None
            and each.content.reference.document == document
            and each.state not in _UNCOUNTED
        ]
        if not notes:
            return None
        total = notes[0]
        for each in notes[1:]:
            total += each
        return total

    @override
    async def page(self, request: PageRequest) -> Page[Document]:
        ordered = sorted(self.kept.values(), key=lambda each: each.id)
        after = [each for each in ordered if request.after is None or each.id > request.after]
        return page_of(after[: request.size + 1], request, key=lambda each: each.id)


@final
class RecordedEvents(Events):
    """What a unit announced, once it committed."""

    def __init__(self) -> None:
        self.pending: list[InvoicingEvent] = []
        self.published: list[InvoicingEvent] = []

    @override
    async def publish(self, event: InvoicingEvent) -> None:
        self.pending.append(event)


@final
class MemoryUnits(Units):
    """Units over one dict of documents per company: a unit's events count once it ends without an error."""

    def __init__(self, now: datetime) -> None:
        self.now = now
        self.documents: dict[Tenant, MemoryDocuments] = {}
        self.events = RecordedEvents()

    @override
    @asynccontextmanager
    async def unit(self, tenant: Tenant) -> AsyncGenerator[InvoicingUnit]:
        documents = self.documents.setdefault(tenant, MemoryDocuments())
        before = dict(documents.kept)
        try:
            yield InvoicingUnit(tenant=tenant, documents=documents, events=self.events, now=self.now)
        except BaseException:
            documents.kept = before
            self.events.pending.clear()
            raise
        self.events.published.extend(self.events.pending)
        self.events.pending.clear()


@final
class ScriptedRules(FiscalRules):
    """Fiscal services that answer what the test says, and remember what they were asked."""

    def __init__(self, verdict: FiscalVerdict) -> None:
        self.verdict = verdict
        self.asked: list[DocumentIssued] = []

    @override
    async def check(self, issued: DocumentIssued) -> FiscalVerdict:
        self.asked.append(issued)
        return self.verdict
```

`services/invoicing/tests/test_application.py`:

```python
from dataclasses import replace
from typing import Final

import pytest

from ledger_invoicing.application import commands, outcomes, queries
from ledger_invoicing.application.commands import (
    CreditExceedsOriginalError,
    ReferenceInvalidError,
    StaleVersionError,
)
from ledger_invoicing.application.outcomes import Decided, Numbered
from ledger_invoicing.application.ports import ChangedMeanwhileError, FiscalVerdict
from ledger_invoicing.domain.document import DocumentNotFoundError, IllegalTransitionError
from ledger_invoicing.domain.events import (
    DocumentFiscallyAccepted,
    DocumentFiscallyNumbered,
    DocumentIssued,
    DocumentVoided,
)
from ledger_invoicing.domain.fiscal import FiscalMessage
from ledger_invoicing.domain.form import checked
from ledger_invoicing.domain.ids import DocumentId
from ledger_invoicing.domain.problems import FiscalProblemsError, Problem
from ledger_invoicing.domain.vocabulary import DocumentKind, DocumentState, FiscalStatus
from ledger_kernel.ids import new_id
from ledger_kernel.money import Currency
from ledger_kernel.pagination import PageRequest
lazy from ledger_invoicing.domain.document import Document

from invoicing_fakes import MemoryUnits, ScriptedRules
from invoicing_samples import INVOICE, NOW, TENANT, draft, line, note

APPLIES: Final = FiscalVerdict(applies=True)
ENCF: Final = "E310000000001"


def anything(version: int) -> bool:
    del version
    return True


def nothing(version: int) -> bool:
    del version
    return False


async def issued(units: MemoryUnits, document: Document, verdict: FiscalVerdict = APPLIES) -> Document:
    judged = await commands.verdict(ScriptedRules(verdict), document)
    async with units.unit(TENANT) as unit:
        return await commands.create(unit, document, judged)


async def accepted(units: MemoryUnits, document: Document) -> Document:
    stored = await issued(units, document)
    async with units.unit(TENANT) as unit:
        decided = Decided(document=stored.id, fiscal_version=4, number=ENCF, accepted=True)
        return await outcomes.mirror(unit, decided) or pytest.fail("not ours")


async def test_a_draft_is_kept_and_announces_nothing() -> None:
    units = MemoryUnits(NOW)
    async with units.unit(TENANT) as unit:
        kept = await commands.create(unit, draft(), None)
    assert units.documents[TENANT].kept[kept.id] == kept
    assert units.events.published == []


async def test_issuing_asks_the_fiscal_services_about_the_document_it_announces() -> None:
    units, rules = MemoryUnits(NOW), ScriptedRules(APPLIES)
    document = draft()
    judged = await commands.verdict(rules, document)
    async with units.unit(TENANT) as unit:
        stored = await commands.create(unit, document, judged)
    assert (stored.state, stored.fiscal.status, stored.version) == (
        DocumentState.ISSUED,
        FiscalStatus.PENDING,
        2,
    )
    (asked,) = rules.asked
    assert units.events.published == [asked]
    assert asked == DocumentIssued(
        tenant=TENANT, document=document.id, version=2, content=stored.content, totals=stored.totals
    )


async def test_a_company_no_fiscal_service_serves_issues_without_a_fiscal_life() -> None:
    stored = await issued(MemoryUnits(NOW), draft(), FiscalVerdict(applies=False))
    assert stored.fiscal.status is FiscalStatus.NOT_APPLICABLE


async def test_a_fiscal_service_s_objection_stops_the_issue_before_anything_is_kept() -> None:
    objection = FiscalVerdict(
        applies=True, problems=(Problem("lines[0].tax", "tax_rate_not_in_force"),), omitted=2
    )
    units = MemoryUnits(NOW)
    with pytest.raises(FiscalProblemsError) as caught:
        await issued(units, draft(), objection)
    assert (caught.value.problems, caught.value.omitted) == (objection.problems, 2)
    assert units.documents[TENANT].kept == {}


async def test_what_is_no_longer_a_draft_is_not_asked_about_nor_issued_again() -> None:
    units, rules = MemoryUnits(NOW), ScriptedRules(APPLIES)
    stored = await issued(units, draft())
    assert await commands.verdict(rules, stored) is None
    assert rules.asked == []
    async with units.unit(TENANT) as unit:
        with pytest.raises(IllegalTransitionError, match="issued cannot become issued"):
            await commands.issue(unit, stored, None, admits=anything)


async def test_a_credit_note_refers_to_an_accepted_invoice_and_never_credits_more_than_it() -> None:
    units = MemoryUnits(NOW)
    invoice = await accepted(units, draft())
    first = await issued(units, draft(note(DocumentKind.CREDIT_NOTE, invoice.id, total_line="1000.00")))
    assert (
        first.totals.total == checked(note(DocumentKind.CREDIT_NOTE, invoice.id, total_line="1000.00")).total
    )
    with pytest.raises(CreditExceedsOriginalError):
        await issued(units, draft(note(DocumentKind.CREDIT_NOTE, invoice.id, total_line="250.00")))
    assert units.documents[TENANT].locks.count(invoice.id) >= 2
    second = await issued(units, draft(note(DocumentKind.CREDIT_NOTE, invoice.id, total_line="200.00")))
    assert second.state is DocumentState.ISSUED  # 1,180.00 + 236.00 = 1,416.00 <= 1,430.00
    debit = await issued(units, draft(note(DocumentKind.DEBIT_NOTE, invoice.id, total_line="5000.00")))
    assert debit.state is DocumentState.ISSUED


async def test_a_note_never_refers_to_what_is_not_a_settled_invoice_of_its_currency() -> None:
    units = MemoryUnits(NOW)
    pending = await issued(units, draft())
    invoice = await accepted(units, draft())
    note_of_note = await issued(units, draft(note(DocumentKind.DEBIT_NOTE, invoice.id)))
    in_dollars = replace(note(DocumentKind.CREDIT_NOTE, invoice.id), currency=Currency.USD)
    in_dollars = replace(in_dollars, lines=(line(1),))
    for wrong in (
        note(DocumentKind.CREDIT_NOTE, DocumentId(new_id())),
        note(DocumentKind.CREDIT_NOTE, pending.id),
        note(DocumentKind.CREDIT_NOTE, note_of_note.id),
        in_dollars,
    ):
        with pytest.raises(ReferenceInvalidError):
            await issued(units, draft(wrong))
    unserved = await issued(units, draft(), FiscalVerdict(applies=False))
    credited = await issued(units, draft(note(DocumentKind.CREDIT_NOTE, unserved.id)))
    assert credited.state is DocumentState.ISSUED


async def test_a_stored_draft_is_rewritten_and_issued_at_the_version_if_match_names() -> None:
    units = MemoryUnits(NOW)
    async with units.unit(TENANT) as unit:
        stored = await commands.create(unit, draft(), None)
    changed = replace(INVOICE, lines=(line(1),), payments=())
    async with units.unit(TENANT) as unit:
        rewritten = await commands.rewrite(unit, stored.id, changed, checked(changed), admits=anything)
    assert (rewritten.version, rewritten.totals.total) == (2, checked(changed).total)
    judged = await commands.verdict(ScriptedRules(APPLIES), rewritten)
    async with units.unit(TENANT) as unit:
        with pytest.raises(StaleVersionError):
            await commands.issue(unit, rewritten, judged, admits=nothing)
    async with units.unit(TENANT) as unit:
        with pytest.raises(ChangedMeanwhileError):
            await commands.issue(unit, stored, judged, admits=anything)
    async with units.unit(TENANT) as unit:
        done = await commands.issue(unit, rewritten, judged, admits=anything)
    assert (done.state, done.version) == (DocumentState.ISSUED, 3)
    assert isinstance(units.events.published[-1], DocumentIssued)


async def test_a_missing_document_is_not_found_by_every_change() -> None:
    units, missing = MemoryUnits(NOW), DocumentId(new_id())
    async with units.unit(TENANT) as unit:
        with pytest.raises(DocumentNotFoundError):
            await commands.void(unit, missing, "x", admits=anything)
        with pytest.raises(DocumentNotFoundError):
            await queries.document(unit, missing)


async def test_voiding_is_announced_and_an_accepted_invoice_is_never_voided() -> None:
    units = MemoryUnits(NOW)
    async with units.unit(TENANT) as unit:
        stored = await commands.create(unit, draft(), None)
    async with units.unit(TENANT) as unit:
        voided = await commands.void(unit, stored.id, "Duplicated", admits=anything)
    assert voided.state is DocumentState.VOIDED
    assert units.events.published == [
        DocumentVoided(
            tenant=TENANT, document=stored.id, kind=DocumentKind.INVOICE, version=2, reason="Duplicated"
        )
    ]
    invoice = await accepted(units, draft())
    async with units.unit(TENANT) as unit:
        with pytest.raises(IllegalTransitionError):
            await commands.void(unit, invoice.id, "Too late", admits=anything)


async def test_the_fiscal_life_is_mirrored_and_announced_and_a_stranger_s_ignored() -> None:
    units = MemoryUnits(NOW)
    stored = await issued(units, draft())
    message = FiscalMessage.of("613", "Observación")
    async with units.unit(TENANT) as unit:
        assert (
            await outcomes.mirror(
                unit, Numbered(document=DocumentId(new_id()), fiscal_version=1, number=ENCF)
            )
            is None
        )
        numbered = await outcomes.mirror(unit, Numbered(document=stored.id, fiscal_version=1, number=ENCF))
        late = await outcomes.mirror(unit, Numbered(document=stored.id, fiscal_version=1, number=ENCF))
        decided = await outcomes.mirror(
            unit,
            Decided(
                document=stored.id,
                fiscal_version=4,
                number=ENCF,
                accepted=True,
                conditional=True,
                messages=(message,),
            ),
        )
    assert numbered is not None
    assert late == numbered
    assert decided is not None
    assert decided.fiscal.status is FiscalStatus.CONDITIONALLY_ACCEPTED
    kinds = [type(event) for event in units.events.published[1:]]
    assert kinds == [DocumentFiscallyNumbered, DocumentFiscallyAccepted]


async def test_a_declined_document_is_rejected_without_a_number() -> None:
    units = MemoryUnits(NOW)
    stored = await issued(units, draft())
    async with units.unit(TENANT) as unit:
        declined = await outcomes.mirror(
            unit, Decided(document=stored.id, fiscal_version=1, number=None, accepted=False)
        )
    assert declined is not None
    assert (declined.state, declined.fiscal.status) == (
        DocumentState.FISCALLY_REJECTED,
        FiscalStatus.DECLINED,
    )


async def test_documents_are_read_a_page_at_a_time() -> None:
    units = MemoryUnits(NOW)
    async with units.unit(TENANT) as unit:
        kept = [await commands.create(unit, draft(), None) for _ in range(3)]
    async with units.unit(TENANT) as unit:
        first = await queries.documents(unit, PageRequest(size=2))
        assert [each.id for each in first.items] == [each.id for each in kept[:2]]
        assert first.next_cursor is not None
        assert await queries.document(unit, kept[2].id) == kept[2]


async def test_a_failed_unit_keeps_nothing_and_announces_nothing() -> None:
    units = MemoryUnits(NOW)
    with pytest.raises(ReferenceInvalidError):
        await issued(units, draft(note(DocumentKind.CREDIT_NOTE, DocumentId(new_id()))))
    assert units.documents[TENANT].kept == {}
    assert units.events.published == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest services/invoicing/tests/test_application.py -q --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'ledger_invoicing.application'`.

- [ ] **Step 3: Implement**

`services/invoicing/src/ledger_invoicing/application/__init__.py`:

```python
"""invoicing's use cases and the ports they need; the domain and the kernel only."""
```

`services/invoicing/src/ledger_invoicing/application/ports.py`:

```python
"""What invoicing's use cases need from the world, as protocols the adapters implement.

A unit of work is one transaction of one company: its documents and the events it announces commit together.
The fiscal services are asked through ``FiscalRules`` outside any unit (no transaction spans a network call).
"""

from abc import ABCMeta, abstractmethod
from dataclasses import dataclass
from typing import ClassVar, Protocol, final
lazy from contextlib import AbstractAsyncContextManager
lazy from datetime import datetime

from ledger_invoicing.domain.problems import FiscalProblemsError
from ledger_kernel.errors import ConflictError
lazy from ledger_invoicing.domain.document import Document
lazy from ledger_invoicing.domain.events import DocumentIssued, InvoicingEvent
lazy from ledger_invoicing.domain.ids import DocumentId
lazy from ledger_invoicing.domain.problems import Problem
lazy from ledger_kernel.ids import Tenant
lazy from ledger_kernel.money import Money
lazy from ledger_kernel.pagination import Page, PageRequest


class Documents(Protocol, metaclass=ABCMeta):
    """The commercial documents of the unit's company."""

    @abstractmethod
    async def add(self, document: Document) -> None:
        """Keep a new document."""

    @abstractmethod
    async def get(self, document: DocumentId) -> Document | None:
        """One of the company's documents, or ``None``."""

    @abstractmethod
    async def locked(self, document: DocumentId) -> Document | None:
        """One of the company's documents, locked until the unit ends (its next change waits), or ``None``."""

    @abstractmethod
    async def update(self, document: Document) -> None:
        """Keep ``document`` in place of the version before it (``document.version - 1``)."""

    @abstractmethod
    async def credited(self, document: DocumentId) -> Money | None:
        """What the company's issued credit notes referring to ``document`` total; ``None`` if there is none.

        Every credit note but drafts, voided ones and those its tax authority rejected.
        """

    @abstractmethod
    async def page(self, request: PageRequest) -> Page[Document]:
        """The company's documents, oldest first."""


class Events(Protocol, metaclass=ABCMeta):
    """What the unit announces when it commits."""

    @abstractmethod
    async def publish(self, event: InvoicingEvent) -> None:
        """Announce ``event`` when, and only if, the unit commits (the outbox)."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class InvoicingUnit:
    """One unit of work for one company."""

    tenant: Tenant
    documents: Documents
    events: Events
    now: datetime
    """The unit's time: what it records and announces happened then."""


class Units(Protocol, metaclass=ABCMeta):
    """Where units of work come from."""

    @abstractmethod
    def unit(self, tenant: Tenant) -> AbstractAsyncContextManager[InvoicingUnit]:
        """A unit of work for ``tenant``'s company: it commits when the block ends normally."""


@final
@dataclass(frozen=True, slots=True)
class FiscalVerdict:
    """What the fiscal services said of a document about to be issued."""

    applies: bool
    """A fiscal service serves the document's company: the document will have a fiscal life."""
    problems: tuple[Problem, ...] = ()
    omitted: int = 0

    def require_no_problems(self) -> None:
        """Return when the services found nothing; ``FiscalProblemsError`` with what they found otherwise."""
        if self.problems:
            raise FiscalProblemsError(self.problems, omitted=self.omitted)


class FiscalRules(Protocol, metaclass=ABCMeta):
    """Every country's fiscal service invoicing knows, asked about one document."""

    @abstractmethod
    async def check(self, issued: DocumentIssued) -> FiscalVerdict:
        """What the services say of the document ``issued`` announces. Asking changes nothing.

        A service that cannot answer is ``UNAVAILABLE`` (the document is not issued: retry later).
        """


class ChangedMeanwhileError(ConflictError):
    """The document changed between the fiscal services' check and its issue: check it again."""

    code: ClassVar[str] = "invoice_changed"

    def __init__(self) -> None:
        super().__init__("the document changed while it was being checked: issue it again")
```

`services/invoicing/src/ledger_invoicing/application/commands.py`:

```python
"""invoicing's use cases that change documents: create, rewrite a draft, issue, void.

Issuing asks the fiscal services first (``verdict``, outside any unit: a network call never runs inside a
transaction), then, in one unit: their objections raised, the reference of a note checked under the referred
document's row lock (so two credit notes of one invoice never both pass the credited total), the document
stored, and ``DocumentIssued`` in the outbox with it. A local failure is answered before anything is stored:
no fiscal number is ever taken for a document invoicing refuses (spec §5.1).

A change of a stored document names the version it applies to (``If-Match``): ``StaleVersionError`` when the
document has moved on.
"""

from typing import ClassVar
lazy from collections.abc import Callable

from ledger_invoicing.application.ports import ChangedMeanwhileError
from ledger_invoicing.domain.document import DocumentNotFoundError, IllegalTransitionError
from ledger_invoicing.domain.vocabulary import DocumentKind, DocumentState, FiscalStatus
from ledger_kernel.errors import ConflictError, InvalidInputError
lazy from ledger_invoicing.application.ports import FiscalRules, FiscalVerdict, InvoicingUnit
lazy from ledger_invoicing.domain.content import Content
lazy from ledger_invoicing.domain.document import Document
lazy from ledger_invoicing.domain.ids import DocumentId
lazy from ledger_invoicing.domain.totals import Totals

type Admits = Callable[[int], bool]
"""Whether a change may apply to a document at this version (what ``If-Match`` allowed)."""


class StaleVersionError(ConflictError):
    """The document is no longer at a version the change was allowed to apply to (``If-Match``)."""

    code: ClassVar[str] = "precondition_failed"

    def __init__(self) -> None:
        super().__init__("the document is not at the version If-Match names")


class ReferenceInvalidError(InvalidInputError):
    """A note refers to something else than a settled invoice of the company's, in the note's currency."""

    code: ClassVar[str] = "reference_invalid"

    def __init__(self) -> None:
        super().__init__("a note refers to an accepted invoice of the same company, in the note's currency")


class CreditExceedsOriginalError(InvalidInputError):
    """A credit note that would credit more than the invoice it refers to, with the notes before it."""

    code: ClassVar[str] = "credit_exceeds_original"

    def __init__(self) -> None:
        super().__init__("the credit notes of an invoice may total at most the invoice's own total")


def _settled(document: Document) -> bool:
    """Whether a note may refer to ``document``: accepted by its authority, or without a fiscal life."""
    return document.state is DocumentState.FISCALLY_ACCEPTED or (
        document.state is DocumentState.ISSUED and document.fiscal.status is FiscalStatus.NOT_APPLICABLE
    )


async def _check_reference(unit: InvoicingUnit, content: Content, totals: Totals) -> None:
    reference = content.reference
    if reference is None:
        return
    original = await unit.documents.locked(reference.document)
    if (
        original is None
        or original.content.kind is not DocumentKind.INVOICE
        or original.content.currency is not content.currency
        or not _settled(original)
    ):
        raise ReferenceInvalidError
    if content.kind is DocumentKind.CREDIT_NOTE:
        credited = await unit.documents.credited(original.id)
        so_far = totals.total if credited is None else credited + totals.total
        if so_far > original.totals.total:
            raise CreditExceedsOriginalError


async def verdict(rules: FiscalRules, draft: Document) -> FiscalVerdict | None:
    """What the fiscal services say of ``draft`` once issued; ``None`` when it is no draft (nothing to ask).

    Outside any unit: it calls the services. Their objections are kept in the verdict, not raised: the unit
    that issues raises them (``FiscalVerdict.require_no_problems``), inside the request's idempotent work, so
    that a retry of a request that succeeded replays its answer even if the services would object now.
    """
    if draft.state is not DocumentState.DRAFT:
        return None
    _, announcement = draft.issued(fiscal_applies=True, now=draft.updated_at)
    return await rules.check(announcement)


async def create(unit: InvoicingUnit, draft: Document, judged: FiscalVerdict | None) -> Document:
    """Keep ``draft``: as a draft without a verdict, else issued under it (and announced)."""
    if judged is None:
        await unit.documents.add(draft)
        return draft
    judged.require_no_problems()
    await _check_reference(unit, draft.content, draft.totals)
    document, issued = draft.issued(fiscal_applies=judged.applies, now=unit.now)
    await unit.documents.add(document)
    await unit.events.publish(issued)
    return document


async def _current(unit: InvoicingUnit, document: DocumentId, admits: Admits) -> Document:
    found = await unit.documents.locked(document)
    if found is None:
        raise DocumentNotFoundError
    if not admits(found.version):
        raise StaleVersionError
    return found


async def rewrite(
    unit: InvoicingUnit, document: DocumentId, content: Content, totals: Totals, *, admits: Admits
) -> Document:
    """The draft ``document`` with new content (checked by ``ledger_invoicing.domain.form.checked``)."""
    rewritten = (await _current(unit, document, admits)).rewritten(content, totals, now=unit.now)
    await unit.documents.update(rewritten)
    return rewritten


async def issue(
    unit: InvoicingUnit, draft: Document, judged: FiscalVerdict | None, *, admits: Admits
) -> Document:
    """Issue ``draft`` under ``judged``, if it is unchanged since (else ``ChangedMeanwhileError``).

    Without a verdict (``draft`` was no draft when it was read), the document's state refuses the move.
    """
    current = await _current(unit, draft.id, admits)
    if current.version != draft.version:
        raise ChangedMeanwhileError
    if judged is None:
        raise IllegalTransitionError(current.state, DocumentState.ISSUED)
    judged.require_no_problems()
    await _check_reference(unit, current.content, current.totals)
    document, issued = current.issued(fiscal_applies=judged.applies, now=unit.now)
    await unit.documents.update(document)
    await unit.events.publish(issued)
    return document


async def void(unit: InvoicingUnit, document: DocumentId, reason: str, *, admits: Admits) -> Document:
    """Void ``document`` (see ``Document.voided`` for which may be)."""
    voided, event = (await _current(unit, document, admits)).voided(reason, now=unit.now)
    await unit.documents.update(voided)
    await unit.events.publish(event)
    return voided
```

`services/invoicing/src/ledger_invoicing/application/outcomes.py`:

```python
"""The fiscal life of documents, mirrored from what their fiscal service announces.

Each use case runs in the unit of the consumer that read the event (its inbox claim commits with it). An
event about a document invoicing does not have is not invoicing's (a document issued straight at the fiscal
service's own API, ``/v1/do/documents``): it is ignored. An event no newer than what is mirrored changes
nothing (``ledger_invoicing.domain.fiscal``). Every change is announced, for webhooks.
"""

from dataclasses import dataclass
from typing import final

lazy from ledger_invoicing.application.ports import InvoicingUnit
lazy from ledger_invoicing.domain.document import Document
lazy from ledger_invoicing.domain.events import FiscalEvent
lazy from ledger_invoicing.domain.fiscal import FiscalMessage
lazy from ledger_invoicing.domain.ids import DocumentId


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Numbered:
    """The fiscal service gave the document its fiscal number."""

    document: DocumentId
    fiscal_version: int
    number: str


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Decided:
    """The tax authority decided, or the fiscal service declined (``number`` is ``None`` then)."""

    document: DocumentId
    fiscal_version: int
    number: str | None
    accepted: bool
    conditional: bool = False
    messages: tuple[FiscalMessage, ...] = ()


type Outcome = Numbered | Decided


def _applied(
    document: Document, outcome: Outcome, unit: InvoicingUnit
) -> tuple[Document, FiscalEvent] | None:
    version, now = outcome.fiscal_version, unit.now
    match outcome:
        case Numbered(number=number):
            return document.fiscally_numbered(number, fiscal_version=version, now=now)
        case Decided(accepted=True, number=str() as number):
            return document.fiscally_accepted(
                number, outcome.messages, conditional=outcome.conditional, fiscal_version=version, now=now
            )
        case _:
            return document.fiscally_rejected(
                outcome.number, outcome.messages, fiscal_version=version, now=now
            )


async def mirror(unit: InvoicingUnit, outcome: Outcome) -> Document | None:
    """Apply ``outcome`` to its document; the document as it now stands, or ``None`` when it is not ours."""
    document = await unit.documents.locked(outcome.document)
    if document is None:
        return None
    applied = _applied(document, outcome, unit)
    if applied is None:
        return document
    changed, event = applied
    await unit.documents.update(changed)
    await unit.events.publish(event)
    return changed
```

`services/invoicing/src/ledger_invoicing/application/queries.py`:

```python
"""invoicing's reads: one document, or a page of them."""

from ledger_invoicing.domain.document import DocumentNotFoundError
lazy from ledger_invoicing.application.ports import InvoicingUnit
lazy from ledger_invoicing.domain.document import Document
lazy from ledger_invoicing.domain.ids import DocumentId
lazy from ledger_kernel.pagination import Page, PageRequest


async def document(unit: InvoicingUnit, wanted: DocumentId) -> Document:
    """One of the company's documents; ``DocumentNotFoundError`` when it has none by that id."""
    found = await unit.documents.get(wanted)
    if found is None:
        raise DocumentNotFoundError
    return found


async def documents(unit: InvoicingUnit, request: PageRequest) -> Page[Document]:
    """A page of the company's documents, oldest first."""
    return await unit.documents.page(request)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest services/invoicing/tests -q --no-cov`
Expected: PASS.

- [ ] **Step 5: Gate**

Run: `uv run poe fmt && uv run poe check`
Expected: every step passes, coverage 100 %.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml services/invoicing
git commit -m "feat(invoicing): use cases that check before issuing and mirror the fiscal life

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: invoicing's settings, schema and migrations

**Files:**
- Create: `services/invoicing/src/ledger_invoicing/settings.py`, `adapters/__init__.py`, `adapters/postgres/__init__.py`, `adapters/postgres/tables.py`, `adapters/postgres/migrate.py`, `adapters/postgres/migrations/__init__.py`, `adapters/postgres/migrations/env.py`, `adapters/postgres/migrations/versions/__init__.py`, `adapters/postgres/migrations/versions/r0001_invoicing.py`
- Modify: `services/invoicing/pyproject.toml`, `pyproject.toml` (root)
- Test: `services/invoicing/tests/conftest.py`, `services/invoicing/tests/test_settings.py`, `services/invoicing/tests/test_migrations.py`

**Interfaces:**
- Consumes: `ApiSettings` (`ledger_platform.settings`); `Migrations`, `run_migrations`, `BASE`, `HEAD`, `SchemaNotCurrentError` (`ledger_platform.migrations`, M3 Task 3); `tenant_isolation`, `Database` (`ledger_platform.database`); `CHASSIS_POLICIES`, `CHASSIS_METADATA` (`ledger_platform.schema`); `Postgres` (`ledger_testing.containers`), `restored_logging`.
- Produces: `settings`: `ADDRESS`, `InvoicingSettings` (`identity_address`, `fiscal_rules_addresses`, `fiscal_check_seconds`; staging and production refuse an empty `fiscal_rules_addresses`). `adapters.postgres.tables`: `InvoicingTable`, `DocumentRow` (table `documents`). `adapters.postgres.migrate`: `SCRIPTS`, `MIGRATIONS`, `upgrade`, `downgrade`, `head`, `require_head`, `main` (console script `ledger-invoicing-migrate`). Revision `0001`.

Re-check before starting: `ledger_platform.migrations` as Milestone 3 landed it (`Migrations(scripts, owner=)`, `run_migrations(*metadata)`), and the chassis's tables (`outbox`, `inbox`, `idempotency_keys`) as `ledger_platform.schema` declares them at this milestone: `r0001_invoicing._chassis` spells them out as Milestones 2-4's first revisions do; if a later milestone changed a chassis table, spell it as that milestone's first revision does (the test `test_the_migrated_schema_is_exactly_the_models` says so at once).

- [ ] **Step 1: Dependencies and layers**

`services/invoicing/pyproject.toml` — `dependencies` gains `"ledger-platform"`, `"alembic>=1.20.0"`, `"pydantic>=2.14.1"` and `"sqlalchemy>=2.1.4"` (`ledger-platform = { workspace = true }` in `[tool.uv.sources]`), and:

```toml
[project.scripts]
ledger-invoicing-migrate = "ledger_invoicing.adapters.postgres.migrate:main"
```

Root `pyproject.toml`: "invoicing is hexagonal" `layers = ["adapters", "settings", "application", "domain"]`; coverage (Decision 20): `[tool.coverage.run] source` gains `"services/invoicing/src/ledger_invoicing/adapters/postgres/migrations"`, and `core = "sysmon"` if Milestone 4 has not set it. Run: `uv lock && uv sync`

- [ ] **Step 2: Write the failing tests**

`services/invoicing/tests/conftest.py`:

```python
"""Fixtures of invoicing's tests: a real PostgreSQL migrated to this version's schema, an outbox."""

from typing import Final
lazy from collections.abc import AsyncGenerator, Generator

import pytest
from opentelemetry.sdk.trace import TracerProvider
from prometheus_client import CollectorRegistry
from sqlalchemy import text

from ledger_invoicing.adapters.postgres.migrate import upgrade
from ledger_kernel.ids import new_id
from ledger_platform.database import Database
from ledger_platform.outbox import Outbox
from ledger_platform.tracing import Tracing
from ledger_testing.containers import Postgres
from ledger_testing.logs import restored_logging

EMPTY_EVERY_TABLE: Final = text("TRUNCATE documents, outbox, inbox, idempotency_keys")
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
    """invoicing's database, migrated to this version; the service connects as its owner, no superuser."""
    url = postgres.database("invoicing", role="invoicing", password="invoicing-password")
    upgrade(url)
    return url


@pytest.fixture
def fresh_database_url(postgres: Postgres) -> str:
    """An empty database of its own, owned by a role of its own: for the tests of the migrations."""
    name = f"fresh_{new_id().hex[16:]}"
    return postgres.database(name, role=name, password="fresh-password")


@pytest.fixture
async def database(database_url: str) -> AsyncGenerator[Database]:
    """A pool on invoicing's database, every table emptied after the test."""
    async with Database.open(database_url, pool_size=4) as opened:
        yield opened
        async with opened.engine.begin() as connection:
            await connection.execute(EMPTY_EVERY_TABLE)


@pytest.fixture
def outbox() -> Outbox:
    """An outbox of the test's own: its own tracer provider and metrics registry."""
    return Outbox(tracing=Tracing(TracerProvider()), registry=CollectorRegistry())
```

`services/invoicing/tests/test_settings.py`:

```python
from typing import Final

import pytest
from pydantic import SecretStr, ValidationError

from ledger_invoicing.settings import InvoicingSettings

COMMON: Final[dict[str, object]] = {
    "database_url": SecretStr("postgresql+psycopg://invoicing:secret@localhost/invoicing"),
    "token_issuer": "https://auth.example.com",
    "token_audience": "ledger",
    "token_jwks_url": "https://auth.example.com/oauth/v2/keys",
    "identity_address": "identity:50051",
    "fiscal_rules_addresses": ("fiscal-do:50051",),
}


def test_invoicing_asks_the_fiscal_services_it_is_given() -> None:
    settings = InvoicingSettings.model_validate(COMMON)
    assert (settings.service_name, settings.fiscal_rules_addresses, settings.fiscal_check_seconds) == (
        "invoicing",
        ("fiscal-do:50051",),
        2.0,
    )
    laptop = InvoicingSettings.model_validate(COMMON | {"fiscal_rules_addresses": ()})
    assert laptop.fiscal_rules_addresses == ()


@pytest.mark.parametrize(
    ("changes", "problem"),
    [
        ({"environment": "production", "fiscal_rules_addresses": ()}, "fiscal_rules_addresses must name"),
        ({"environment": "staging", "fiscal_rules_addresses": ()}, "fiscal_rules_addresses must name"),
        ({"fiscal_rules_addresses": ("fiscal-do",)}, "fiscal_rules_addresses"),
        ({"fiscal_check_seconds": 30}, "fiscal_check_seconds"),
        ({"identity_address": "identity"}, "identity_address"),
    ],
    ids=["production-without", "staging-without", "address", "deadline", "identity"],
)
def test_a_configuration_that_cannot_run_safely_does_not_start(
    changes: dict[str, object], problem: str
) -> None:
    with pytest.raises(ValidationError, match=problem):
        InvoicingSettings.model_validate(COMMON | changes)


def test_the_fiscal_services_are_read_from_the_environment_as_json(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in {
        "LEDGER_DATABASE_URL": "postgresql+psycopg://invoicing:secret@localhost/invoicing",
        "LEDGER_TOKEN_ISSUER": "https://auth.example.com",
        "LEDGER_TOKEN_AUDIENCE": "ledger",
        "LEDGER_TOKEN_JWKS_URL": "https://auth.example.com/oauth/v2/keys",
        "LEDGER_IDENTITY_ADDRESS": "identity:50051",
        "LEDGER_FISCAL_RULES_ADDRESSES": '["fiscal-do:50051", "fiscal-mx:50051"]',
    }.items():
        monkeypatch.setenv(name, value)
    assert InvoicingSettings().fiscal_rules_addresses == ("fiscal-do:50051", "fiscal-mx:50051")
```

`services/invoicing/tests/test_migrations.py`:

```python
from typing import Final

import pytest
from alembic.autogenerate import produce_migrations, render_python_code
from alembic.migration import MigrationContext
from pydantic import TypeAdapter
from sqlalchemy import MetaData, create_engine, inspect, text
from sqlalchemy.pool import NullPool

from ledger_invoicing.adapters.postgres import migrate
from ledger_invoicing.adapters.postgres.migrate import downgrade, head, require_head, upgrade
from ledger_invoicing.adapters.postgres.tables import InvoicingTable
from ledger_kernel.errors import ErrorKind
from ledger_platform.database import Database
from ledger_platform.migrations import SchemaNotCurrentError
from ledger_platform.schema import CHASSIS_METADATA

TABLES: Final = frozenset({"documents", "outbox", "inbox", "idempotency_keys"})
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
    for table in (*CHASSIS_METADATA.tables.values(), *InvoicingTable.metadata.tables.values()):
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
    assert tables_of(fresh_database_url) == {*TABLES, "alembic_version"}
    downgrade(fresh_database_url)
    assert tables_of(fresh_database_url) == {"alembic_version"}
    upgrade(fresh_database_url)
    assert tables_of(fresh_database_url) >= TABLES


def test_the_migrated_schema_is_exactly_the_models(database_url: str) -> None:
    assert pending_changes(database_url) is None


def test_a_company_s_documents_are_under_its_policy(database_url: str) -> None:
    engine = create_engine(database_url, poolclass=NullPool)
    try:
        with engine.connect() as connection:
            rows = connection.execute(text("SELECT tablename::text, policyname::text FROM pg_policies"))
            policies = PAIRS.validate_python([tuple(row) for row in rows])
    finally:
        engine.dispose()
    assert sorted(policies) == [
        ("documents", "tenant_isolation"),
        ("idempotency_keys", "organization_isolation"),
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
        "LEDGER_FISCAL_RULES_ADDRESSES": '["fiscal-do:50051"]',
    }.items():
        monkeypatch.setenv(name, value)
    assert migrate.main() == 0
    assert tables_of(fresh_database_url) >= TABLES
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest services/invoicing/tests/test_settings.py services/invoicing/tests/test_migrations.py -q --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'ledger_invoicing.adapters'`.

- [ ] **Step 4: Implement**

`services/invoicing/src/ledger_invoicing/settings.py`:

```python
"""invoicing's settings: the chassis's, its API's, identity's address, and the fiscal services it asks.

``fiscal_rules_addresses`` names every country's fiscal service (``["fiscal-do:50051"]``), each asked about
every document before it is issued; an empty list is a deployment without any (every document is issued
without a fiscal life), which only a laptop or a test may run: beyond them the list must name at least one.
"""

from typing import Annotated, Final, Self

from pydantic import Field, model_validator

from ledger_platform.settings import ApiSettings

ADDRESS: Final = r"^[A-Za-z0-9.-]+:[0-9]{1,5}$"
_BEYOND_A_LAPTOP: Final = frozenset({"staging", "production"})
_FISCAL_REQUIRED: Final = "fiscal_rules_addresses must name a fiscal service in staging and production"


class _InvoicingSettingsError(ValueError):
    """A configuration that cannot run invoicing safely."""

    def __init__(self) -> None:
        super().__init__(_FISCAL_REQUIRED)


class InvoicingSettings(ApiSettings):
    """invoicing's settings (see the module)."""

    service_name: str = Field(default="invoicing", pattern=r"^[a-z][a-z0-9-]{1,30}$")
    identity_address: str = Field(default=..., min_length=3, max_length=255, pattern=ADDRESS)
    """identity's gRPC, ``host:port``: the caller's permissions."""
    fiscal_rules_addresses: tuple[
        Annotated[str, Field(min_length=3, max_length=255, pattern=ADDRESS)], ...
    ] = Field(default=..., max_length=8)
    """Every country's fiscal service, ``host:port`` each (as JSON in the environment)."""
    fiscal_check_seconds: Annotated[float, Field(gt=0, le=10)] = 2.0
    """How long the fiscal services have to answer about a document; then it is not issued (503)."""

    @model_validator(mode="after")
    def require_a_fiscal_service_beyond_a_laptop(self) -> Self:
        """Refuse a staging or production that would issue every document without a fiscal life."""
        if self.environment in _BEYOND_A_LAPTOP and not self.fiscal_rules_addresses:
            raise _InvoicingSettingsError
        return self
```

`services/invoicing/src/ledger_invoicing/adapters/__init__.py`:

```python
"""invoicing's adapters: the wire, the outbox's events, the fiscal services over gRPC, PostgreSQL."""
```

`services/invoicing/src/ledger_invoicing/adapters/postgres/__init__.py`:

```python
"""invoicing's state in PostgreSQL: tables, repositories, units of work, migrations (SQLAlchemy, Alembic)."""
```

`services/invoicing/src/ledger_invoicing/adapters/postgres/tables.py`:

```python
"""invoicing's tables, typed for SQLAlchemy Core: what the migrations create and the repositories use.

One table, ``documents``, under ``tenant_isolation``. A note refers to its document through ``(company_id,
reference_id)``, so it can never name another company's (PostgreSQL's referential checks bypass row-level
security). What a document says, and its fiscal side, are ``jsonb`` (``codec``); what a query filters or sums
is a column of its own: the kind, the state, the total, the reference. Totals are recomputed from the content
on reading: they are a function of it (``ledger_invoicing.domain.totals``), and a stored copy could only
disagree.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import ClassVar, Final
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Index,
    MetaData,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql.schema import SchemaItem

WORD: Final = 32
CURRENCY: Final = 3
REASON: Final = 200
KINDS: Final = "'invoice', 'credit_note', 'debit_note'"
STATES: Final = "'draft', 'issued', 'fiscally_accepted', 'fiscally_rejected', 'voided'"
NOTES_REFER: Final = "(kind = 'invoice') = (reference_id IS NULL)"
"""A note refers to a document; an invoice to none."""


class InvoicingTable(DeclarativeBase):
    """invoicing's own tables; the chassis's are ``PlatformTable``'s."""

    metadata: ClassVar[MetaData] = MetaData()


class DocumentRow(InvoicingTable):
    """A commercial document, from draft to its tax authority's answer."""

    __tablename__: str = "documents"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint("company_id", "id", name="uq_documents_company"),
        ForeignKeyConstraint(
            ["company_id", "reference_id"],
            ["documents.company_id", "documents.id"],
            name="fk_documents_reference",
        ),
        CheckConstraint(f"kind IN ({KINDS})", name="ck_documents_kind"),
        CheckConstraint(f"state IN ({STATES})", name="ck_documents_state"),
        CheckConstraint(NOTES_REFER, name="ck_documents_reference"),
        CheckConstraint("version > 0", name="ck_documents_version"),
        Index(
            "ix_documents_reference",
            "company_id",
            "reference_id",
            postgresql_where=text("reference_id IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    organization_id: Mapped[UUID] = mapped_column()
    company_id: Mapped[UUID] = mapped_column()
    kind: Mapped[str] = mapped_column(String(WORD))
    state: Mapped[str] = mapped_column(String(WORD))
    version: Mapped[int] = mapped_column(BigInteger)
    emission_point_id: Mapped[UUID] = mapped_column()
    issue_date: Mapped[date] = mapped_column(Date)
    currency: Mapped[str] = mapped_column(String(CURRENCY))
    total: Mapped[Decimal] = mapped_column(Numeric(20, 2))
    reference_id: Mapped[UUID | None] = mapped_column()
    content: Mapped[dict[str, object]] = mapped_column(JSONB)
    fiscal: Mapped[dict[str, object]] = mapped_column(JSONB)
    void_reason: Mapped[str | None] = mapped_column(String(REASON))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
```

`services/invoicing/src/ledger_invoicing/adapters/postgres/migrate.py`:

```python
"""invoicing's schema brought to this version's: ``ledger_platform.migrations`` with invoicing's revisions."""

from typing import Final

from ledger_invoicing.settings import InvoicingSettings
from ledger_platform.migrations import BASE, HEAD, Migrations
lazy from ledger_platform.database import Database

SCRIPTS: Final = "ledger_invoicing.adapters.postgres:migrations"
MIGRATIONS: Final = Migrations(SCRIPTS, owner="invoicing")


def upgrade(url: str, revision: str = HEAD) -> None:
    """Bring invoicing's database at ``url`` up to ``revision``."""
    MIGRATIONS.upgrade(url, revision)


def downgrade(url: str, revision: str = BASE) -> None:
    """Take invoicing's database at ``url`` down to ``revision``."""
    MIGRATIONS.downgrade(url, revision)


def head() -> str:
    """The revision invoicing's migrations end at."""
    return MIGRATIONS.head()


async def require_head(database: Database, expected: str) -> None:
    """Invoicing's readiness check: the database is at ``expected``."""
    await MIGRATIONS.require_head(database, expected)


def main() -> int:
    """``ledger-invoicing-migrate``: bring the database of ``LEDGER_DATABASE_URL`` to this schema."""
    upgrade(InvoicingSettings().database_url.get_secret_value())
    return 0
```

`services/invoicing/src/ledger_invoicing/adapters/postgres/migrations/__init__.py`:

```python
"""invoicing's Alembic environment and migration scripts."""
```

`services/invoicing/src/ledger_invoicing/adapters/postgres/migrations/env.py`:

```python
"""Alembic's entry point for invoicing: the platform's runner with invoicing's tables."""

from ledger_invoicing.adapters.postgres.tables import InvoicingTable
from ledger_platform.migrations import run_migrations

run_migrations(InvoicingTable.metadata)
```

`services/invoicing/src/ledger_invoicing/adapters/postgres/migrations/versions/__init__.py`:

```python
"""invoicing's migration scripts, one per revision."""
```

`services/invoicing/src/ledger_invoicing/adapters/postgres/migrations/versions/r0001_invoicing.py`:

```python
"""Revision 0001, from nothing: invoicing's first schema.

The chassis's tables (as ``ledger_platform`` declares them at this revision), then ``documents`` under
row-level security (see ``tables``).
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

_NEWEST_FIRST: Final = ("idempotency_keys", "inbox", "outbox", "documents")
_KINDS: Final = "'invoice', 'credit_note', 'debit_note'"
_STATES: Final = "'draft', 'issued', 'fiscally_accepted', 'fiscally_rejected', 'voided'"


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


def _documents() -> None:
    documents = op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("emission_point_id", sa.Uuid(), nullable=False),
        sa.Column("issue_date", sa.Date(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("total", sa.Numeric(20, 2), nullable=False),
        sa.Column("reference_id", sa.Uuid(), nullable=True),
        sa.Column("content", JSONB(), nullable=False),
        sa.Column("fiscal", JSONB(), nullable=False),
        sa.Column("void_reason", sa.String(200), nullable=True),
        _moment("created_at"),
        _moment("updated_at"),
        _moment("issued_at", nullable=True),
        sa.UniqueConstraint("company_id", "id", name="uq_documents_company"),
        sa.ForeignKeyConstraint(
            ["company_id", "reference_id"],
            ["documents.company_id", "documents.id"],
            name="fk_documents_reference",
        ),
        sa.CheckConstraint(f"kind IN ({_KINDS})", name="ck_documents_kind"),
        sa.CheckConstraint(f"state IN ({_STATES})", name="ck_documents_state"),
        sa.CheckConstraint("(kind = 'invoice') = (reference_id IS NULL)", name="ck_documents_reference"),
        sa.CheckConstraint("version > 0", name="ck_documents_version"),
    )
    op.create_index(
        "ix_documents_reference",
        "documents",
        ["company_id", "reference_id"],
        postgresql_where=sa.text("reference_id IS NOT NULL"),
    )
    _apply(tenant_isolation(documents))


def upgrade() -> None:
    """From nothing to invoicing's first schema."""
    _chassis()
    _documents()


def downgrade() -> None:
    """Back to nothing: every table, newest first (their policies go with them)."""
    for name in _NEWEST_FIRST:
        op.drop_table(name)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest services/invoicing/tests/test_settings.py services/invoicing/tests/test_migrations.py -q --no-cov`
Expected: PASS (the schema is built from nothing and back, is exactly the models, `documents` is under `tenant_isolation`).

- [ ] **Step 6: Gate**

Run: `uv run poe fmt && uv run poe check`
Expected: every step passes, coverage 100 %.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock services/invoicing
git commit -m "feat(invoicing): settings, documents' schema under row-level security, migrations

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: invoicing's state in PostgreSQL, its events on the wire, the fiscal services over gRPC

**Files:**
- Create: `services/invoicing/src/ledger_invoicing/adapters/postgres/codec.py`, `adapters/postgres/repositories.py`, `adapters/postgres/units.py`, `adapters/wire.py`, `adapters/events.py`, `adapters/fiscal_rules.py`
- Modify: `services/invoicing/pyproject.toml`
- Test: `services/invoicing/tests/test_repositories.py`, `services/invoicing/tests/test_wire.py`, `services/invoicing/tests/test_fiscal_rules.py`

**Interfaces:**
- Consumes: Tasks 2-5; `Outbox`, `UnitOfWork`, `Database`; `StreamSpec`; `Envelope`; `Channel`, `call_unary`, `require_deadline`, `DEFAULT_DEADLINE`, `RemoteUnavailableError`, `InvalidGrpcClientOptionError` (`ledger_platform.grpc_client`); `serve_grpc`.
- Produces: `codec`: `content_json`, `content_of`, `fiscal_json`, `fiscal_of` (strict validation of what is read). `repositories`: `LostUpdateError` (`invoice_changed`), `PgDocuments`. `units`: `UnscopedUnitError`, `bind(unit, outbox, *, clock)`, `PgUnits`. `wire`: `KINDS`, `money`, `commercial_document(issued)`, `outgoing(event) -> (Message, subject)`, `outcome_of(envelope) -> Outcome | None` (fiscal-do's events in invoicing's words). `events`: `INVOICING` (the stream), `SOURCE`, `OutboxEvents`. `fiscal_rules`: `MAX_FIELD`, `MAX_CODE`, `verdict_of(answers)`, `RemoteFiscalRules(channels, *, deadline)`.

Re-check before starting: `Channel` (`opened()`, `current()`) as Milestone 3 landed it; Milestone 4's `ledger.events.fiscal_do.v1` messages (`DocumentNumbered`, `DocumentAccepted`, `DocumentConditionallyAccepted`, `DocumentRejected` with `encf`, `version`, `messages`) as landed.

- [ ] **Step 1: Dependencies**

`services/invoicing/pyproject.toml` — `dependencies` gains `"ledger-contracts"` and `"protobuf>=7.36.2"` (`ledger-contracts = { workspace = true }`), and:

```toml
[tool.deptry.package_module_name_map]
"ledger-contracts" = ["ledger_contracts", "ledger"]
"protobuf" = "google"
```

Run: `uv lock && uv sync`

- [ ] **Step 2: Write the failing tests**

`services/invoicing/tests/test_repositories.py`:

```python
from dataclasses import replace
from datetime import timedelta
from types import MappingProxyType

import pytest
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

import ledger.events.invoicing.v1.events_pb2 as wire
from ledger_contracts.envelope import Envelope
from ledger_invoicing.adapters.postgres.codec import content_of
from ledger_invoicing.adapters.postgres.repositories import LostUpdateError
from ledger_invoicing.adapters.postgres.units import PgUnits, UnscopedUnitError, bind
from ledger_invoicing.application import commands
from ledger_invoicing.application.ports import FiscalVerdict
from ledger_invoicing.domain.content import Terms, Withholding
from ledger_invoicing.domain.fiscal import FiscalMessage
from ledger_invoicing.domain.ids import DocumentId
from ledger_invoicing.domain.vocabulary import DocumentKind, DocumentState, PaymentTerms
from ledger_kernel.ids import new_id
from ledger_kernel.pagination import PageRequest
from ledger_platform.outbox import OutboxRecord
lazy from ledger_invoicing.domain.document import Document
lazy from ledger_platform.database import Database
lazy from ledger_platform.outbox import Outbox

from invoicing_samples import INVOICE, NOW, OTHER_TENANT, TENANT, TODAY, dop, draft, line, note

FULL = replace(
    INVOICE,
    lines=(
        replace(line(1, discount=dop("10.00")), description="Blue", unit_code="43"),
        replace(line(2), withholding=Withholding(value_added_tax=dop("54.00"), income_tax=None)),
    ),
    terms=Terms(kind=PaymentTerms.CREDIT, due_date=TODAY + timedelta(days=30)),
    payments=(),
    fiscal_fields=MappingProxyType({"ecf_type": "31", "income_type": "01"}),
    prices_include_tax=True,
)
ACCEPTED = (FiscalMessage.of("613", "Observación"),)


async def kept(units: PgUnits, document: Document, *, issue: bool = True) -> Document:
    """``document`` stored as it is (a draft), or issued: straight through the repository, no rule asked."""
    stored = document.issued(fiscal_applies=True, now=NOW)[0] if issue else document
    async with units.unit(document.tenant) as unit:
        await unit.documents.add(stored)
    return stored


async def test_a_document_reads_back_as_it_was_kept(database: Database, outbox: Outbox) -> None:
    units = PgUnits(database, outbox, clock=lambda: NOW)
    stored = await kept(units, draft(FULL))
    async with units.unit(TENANT) as unit:
        read = await unit.documents.get(stored.id)
    assert read == stored
    assert read is not None
    assert read.totals == stored.totals


async def test_a_change_applies_to_the_version_before_it_only(database: Database, outbox: Outbox) -> None:
    units = PgUnits(database, outbox, clock=lambda: NOW)
    stored = await kept(units, draft(), issue=False)
    voided, _ = stored.voided("Duplicated", now=NOW)
    async with units.unit(TENANT) as unit:
        await unit.documents.update(voided)
    with pytest.raises(LostUpdateError):
        async with units.unit(TENANT) as unit:
            await unit.documents.update(voided)
    async with units.unit(TENANT) as unit:
        assert await unit.documents.locked(stored.id) == voided
        assert await unit.documents.locked(DocumentId(new_id())) is None


async def test_credited_counts_the_issued_credit_notes_of_one_invoice(
    database: Database, outbox: Outbox
) -> None:
    units = PgUnits(database, outbox, clock=lambda: NOW)
    invoice = await kept(units, draft(), issue=False)
    async with units.unit(TENANT) as unit:
        assert await unit.documents.credited(invoice.id) is None
    for total_line in ("100.00", "200.00"):
        await kept(
            units, draft(note(DocumentKind.CREDIT_NOTE, invoice.id, total_line=total_line)), issue=False
        )
    first = await kept(units, draft(note(DocumentKind.CREDIT_NOTE, invoice.id, total_line="100.00")))
    second = await kept(units, draft(note(DocumentKind.CREDIT_NOTE, invoice.id, total_line="200.00")))
    third = await kept(units, draft(replace(note(DocumentKind.CREDIT_NOTE, invoice.id), customer=None)))
    await kept(units, draft(note(DocumentKind.DEBIT_NOTE, invoice.id, total_line="999.00")))
    rejected, _ = second.fiscally_rejected("E340000000002", (), fiscal_version=3, now=NOW) or pytest.fail("x")
    async with units.unit(TENANT) as unit:
        await unit.documents.update(rejected)
        assert await unit.documents.credited(invoice.id) == first.totals.total + third.totals.total
        assert (await unit.documents.get(third.id) or pytest.fail("x")).content.customer is None


async def test_documents_are_read_a_page_at_a_time_in_id_order(database: Database, outbox: Outbox) -> None:
    units = PgUnits(database, outbox, clock=lambda: NOW)
    stored = [await kept(units, draft(), issue=False) for _ in range(3)]
    async with units.unit(TENANT) as unit:
        first = await unit.documents.page(PageRequest(size=2))
        assert [each.id for each in first.items] == [each.id for each in stored[:2]]
        rest = await unit.documents.page(PageRequest(size=2, after=stored[1].id))
    assert [each.id for each in rest.items] == [stored[2].id]
    assert rest.next_cursor is None


async def test_a_company_never_sees_nor_refers_to_another_s_documents(
    database: Database, outbox: Outbox
) -> None:
    units = PgUnits(database, outbox, clock=lambda: NOW)
    theirs = await kept(units, draft(), issue=False)
    async with units.unit(OTHER_TENANT) as unit:
        assert await unit.documents.get(theirs.id) is None
        assert (await unit.documents.page(PageRequest())).items == ()
    stranger = replace(draft(note(DocumentKind.CREDIT_NOTE, theirs.id)), tenant=OTHER_TENANT)
    with pytest.raises(IntegrityError):
        async with units.unit(OTHER_TENANT) as unit:
            await unit.documents.add(stranger)


async def test_issuing_puts_the_document_whole_in_the_outbox(database: Database, outbox: Outbox) -> None:
    units = PgUnits(database, outbox, clock=lambda: NOW)
    async with units.unit(TENANT) as unit:
        stored = await commands.create(unit, draft(FULL), FiscalVerdict(applies=True))
    async with database.transaction(None) as transaction:
        rows = (
            await transaction.connection.execute(
                select(OutboxRecord.subject, OutboxRecord.headers, OutboxRecord.data)
            )
        ).all()
    ((subject, headers, data),) = rows
    assert subject == "invoicing.document.issued"
    issued = Envelope.from_headers(headers, data).unwrap(wire.DocumentIssued).document
    assert (issued.document_id, issued.version, issued.totals.total.amount) == (
        str(stored.id),
        stored.version,
        format(stored.totals.total.amount, "f"),
    )
    assert issued.fiscal_fields == {"ecf_type": "31", "income_type": "01"}


async def test_a_unit_without_a_company_is_a_defect(database: Database, outbox: Outbox) -> None:
    async with database.transaction(None) as transaction:
        with pytest.raises(UnscopedUnitError):
            bind(transaction, outbox)


async def test_a_row_edited_into_another_shape_fails_where_it_is_read(
    database: Database, outbox: Outbox
) -> None:
    units = PgUnits(database, outbox, clock=lambda: NOW)
    stored = await kept(units, draft(), issue=False)
    async with database.transaction(TENANT) as transaction:
        await transaction.connection.execute(
            text("UPDATE documents SET content = jsonb_set(content, '{lines,0,quantity}', '2')")
        )
    with pytest.raises(ValidationError):
        async with units.unit(TENANT) as unit:
            await unit.documents.get(stored.id)
    with pytest.raises(ValidationError):
        content_of({"kind": "invoice"})


async def test_the_fiscal_side_reads_back_with_its_messages(database: Database, outbox: Outbox) -> None:
    units = PgUnits(database, outbox, clock=lambda: NOW)
    stored = await kept(units, draft())
    accepted, _ = stored.fiscally_accepted(
        "E310000000001", ACCEPTED, conditional=True, fiscal_version=4, now=NOW
    ) or pytest.fail("x")
    async with units.unit(TENANT) as unit:
        await unit.documents.update(accepted)
        read = await unit.documents.get(stored.id)
    assert read is not None
    assert (read.state, read.fiscal) == (DocumentState.FISCALLY_ACCEPTED, accepted.fiscal)
```

`services/invoicing/tests/test_wire.py`:

```python
from dataclasses import replace
from datetime import timedelta
from types import MappingProxyType

import pytest
lazy from google.protobuf.message import Message

import ledger.events.fiscal_do.v1.events_pb2 as fiscal_wire
import ledger.events.invoicing.v1.events_pb2 as wire
import ledger.invoicing.v1.document_pb2 as document
from ledger_contracts.envelope import Envelope
from ledger_invoicing.adapters.wire import commercial_document, outcome_of, outgoing
from ledger_invoicing.application.outcomes import Decided, Numbered
from ledger_invoicing.domain.content import Address, Party, Reference, Terms, Withholding
from ledger_invoicing.domain.events import (
    DocumentFiscallyAccepted,
    DocumentFiscallyNumbered,
    DocumentFiscallyRejected,
    DocumentVoided,
)
from ledger_invoicing.domain.fiscal import FiscalMessage
from ledger_invoicing.domain.ids import DocumentId
from ledger_invoicing.domain.vocabulary import Correction, DocumentKind, PaymentTerms
from ledger_kernel.ids import new_id
lazy from ledger_invoicing.domain.events import InvoicingEvent

from invoicing_samples import INVOICE, TENANT, TODAY, dop, draft, line, note

MESSAGE = FiscalMessage.of("613", "Observación")


def test_a_document_is_written_whole_for_every_fiscal_service() -> None:
    content = replace(
        INVOICE,
        customer=Party(name="Juan", address=Address(country_code="DO")),
        lines=(replace(line(1, discount=dop("5.00")), withholding=Withholding(income_tax=dop("10.00"))),),
        terms=Terms(kind=PaymentTerms.CREDIT, due_date=TODAY + timedelta(days=30)),
        payments=(),
        fiscal_fields=MappingProxyType({"income_type": "01"}),
    )
    _, issued = draft(content).issued(fiscal_applies=True, now=draft().created_at)
    written = commercial_document(issued)
    assert (written.document_id, written.version, written.kind) == (
        str(issued.document),
        2,
        document.DOCUMENT_KIND_INVOICE,
    )
    assert (written.organization_id, written.company_id) == (str(TENANT.organization), str(TENANT.company))
    assert (written.issue_date, written.currency, written.terms.due_date) == (
        "2026-10-12",
        "DOP",
        "2026-11-11",
    )
    assert (written.customer.name, written.customer.tax_id, written.customer.address.country_code) == (
        "Juan",
        "",
        "DO",
    )
    (written_line,) = written.lines
    assert (written_line.quantity, written_line.unit_price, written_line.tax.rate) == ("2", "500.00", "18")
    assert (written_line.discount.amount, written_line.amount.amount) == ("5.00", "995.00")
    assert written_line.withholding.income_tax.amount == "10.00"
    assert not written_line.withholding.HasField("value_added_tax")
    assert written.totals.total.amount == "1174.10"
    assert dict(written.fiscal_fields) == {"income_type": "01"}
    assert not written.HasField("reference")


def test_a_note_names_the_document_it_refers_to() -> None:
    referred = DocumentId(new_id())
    content = replace(
        note(DocumentKind.CREDIT_NOTE, referred),
        customer=None,
        reference=Reference(document=referred, correction=Correction.ANNULMENT, reason="Cancelled"),
    )
    _, issued = draft(content).issued(fiscal_applies=True, now=draft().created_at)
    written = commercial_document(issued)
    assert (written.reference.document_id, written.reference.correction, written.reference.reason) == (
        str(referred),
        document.CORRECTION_ANNULMENT,
        "Cancelled",
    )
    assert not written.HasField("customer")
    assert not written.lines[0].HasField("discount")


def test_every_event_has_its_subject_and_message() -> None:
    document_id, kind = DocumentId(new_id()), DocumentKind.CREDIT_NOTE
    number = "E340000000001"
    cases: list[tuple[InvoicingEvent, str, type[Message]]] = [
        (
            DocumentVoided(tenant=TENANT, document=document_id, kind=kind, version=3, reason="x"),
            "invoicing.document.voided",
            wire.DocumentVoided,
        ),
        (
            DocumentFiscallyNumbered(
                tenant=TENANT, document=document_id, kind=kind, version=3, fiscal_number=number
            ),
            "invoicing.document.fiscally_numbered",
            wire.DocumentFiscallyNumbered,
        ),
        (
            DocumentFiscallyAccepted(
                tenant=TENANT,
                document=document_id,
                kind=kind,
                version=3,
                fiscal_number=number,
                conditional=True,
                messages=(MESSAGE,),
            ),
            "invoicing.document.fiscally_accepted",
            wire.DocumentFiscallyAccepted,
        ),
        (
            DocumentFiscallyRejected(
                tenant=TENANT,
                document=document_id,
                kind=kind,
                version=3,
                fiscal_number=None,
                declined=True,
                messages=(MESSAGE,),
            ),
            "invoicing.document.fiscally_rejected",
            wire.DocumentFiscallyRejected,
        ),
    ]
    for event, subject, message_type in cases:
        message, published = outgoing(event)
        assert (published, type(message)) == (subject, message_type)
    rejected, _ = outgoing(cases[-1][0])
    assert isinstance(rejected, wire.DocumentFiscallyRejected)
    assert (rejected.fiscal_number, rejected.declined, rejected.messages[0].text) == ("", True, "Observación")
    issued, subject = outgoing(draft().issued(fiscal_applies=True, now=draft().created_at)[1])
    assert (subject, type(issued)) == ("invoicing.document.issued", wire.DocumentIssued)


def envelope_of(message: Message) -> Envelope:
    return Envelope.wrap(message, source="fiscal-do", tenant=TENANT)


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (
            fiscal_wire.DocumentNumbered(
                document_id="0199d5a5-0000-7000-8000-000000000001", encf="E310000000001", version=1
            ),
            Numbered(document=DocumentId(new_id()), fiscal_version=1, number="E310000000001"),
        ),
        (
            fiscal_wire.DocumentAccepted(
                document_id="0199d5a5-0000-7000-8000-000000000001", encf="E310000000001", version=4
            ),
            Decided(document=DocumentId(new_id()), fiscal_version=4, number="E310000000001", accepted=True),
        ),
        (
            fiscal_wire.DocumentConditionallyAccepted(
                document_id="0199d5a5-0000-7000-8000-000000000001",
                encf="E310000000001",
                version=4,
                messages=[fiscal_wire.DgiiMessage(code="613", text="Observación")],
            ),
            Decided(
                document=DocumentId(new_id()),
                fiscal_version=4,
                number="E310000000001",
                accepted=True,
                conditional=True,
                messages=(MESSAGE,),
            ),
        ),
        (
            fiscal_wire.DocumentRejected(
                document_id="0199d5a5-0000-7000-8000-000000000001", encf="E310000000001", version=4
            ),
            Decided(document=DocumentId(new_id()), fiscal_version=4, number="E310000000001", accepted=False),
        ),
        (
            fiscal_wire.DocumentDeclined(
                document_id="0199d5a5-0000-7000-8000-000000000001",
                version=1,
                messages=[fiscal_wire.DgiiMessage(code="no_sequence", text="No range")],
            ),
            Decided(
                document=DocumentId(new_id()),
                fiscal_version=1,
                number=None,
                accepted=False,
                messages=(FiscalMessage("no_sequence", "No range"),),
            ),
        ),
    ],
    ids=["numbered", "accepted", "conditionally-accepted", "rejected", "declined"],
)
def test_fiscal_do_s_events_are_read_in_invoicing_s_words(
    message: Message, expected: Numbered | Decided
) -> None:
    read = outcome_of(envelope_of(message))
    assert read is not None
    assert str(read.document) == "0199d5a5-0000-7000-8000-000000000001"
    assert replace(read, document=expected.document) == expected


def test_an_event_invoicing_does_not_mirror_is_not_its() -> None:
    assert outcome_of(envelope_of(fiscal_wire.SequenceLow(sequence_id=str(new_id())))) is None
```

`services/invoicing/tests/test_fiscal_rules.py`:

```python
from contextlib import AsyncExitStack
from typing import Final, final, override

import grpc
import pytest
from prometheus_client import CollectorRegistry

from ledger.fiscal.v1.fiscal_rules_pb2 import CheckDocumentRequest, CheckDocumentResponse, Problem
from ledger.fiscal.v1.fiscal_rules_pb2_grpc import (
    FiscalRulesServiceServicer,
    add_FiscalRulesServiceServicer_to_server,
)
from ledger_invoicing.adapters.fiscal_rules import RemoteFiscalRules, verdict_of
from ledger_invoicing.domain import problems
from ledger_platform.grpc_client import Channel, InvalidGrpcClientOptionError, RemoteUnavailableError
from ledger_platform.grpc_server import serve_grpc

from invoicing_samples import NOW, draft

type Context = grpc.aio.ServicerContext[CheckDocumentRequest, CheckDocumentResponse]
ISSUED: Final = draft().issued(fiscal_applies=True, now=NOW)[1]


@final
class FakeFiscalService(FiscalRulesServiceServicer):
    """A country's fiscal service that answers what the test says, and remembers what it was asked."""

    def __init__(self, answer: CheckDocumentResponse) -> None:
        self.answer = answer
        self.asked: list[CheckDocumentRequest] = []

    @override
    async def CheckDocument(self, request: CheckDocumentRequest, context: Context) -> CheckDocumentResponse:
        del context
        self.asked.append(request)
        return self.answer


async def served(stack: AsyncExitStack, fake: FakeFiscalService) -> Channel:
    def register(server: grpc.aio.Server) -> None:
        add_FiscalRulesServiceServicer_to_server(fake, server)

    port = await stack.enter_async_context(
        serve_grpc(port=0, register=register, registry=CollectorRegistry())
    )
    return await stack.enter_async_context(
        Channel(f"127.0.0.1:{port}", caller="invoicing", version="0.1.0").opened()
    )


async def test_every_fiscal_service_is_asked_and_those_that_apply_decide() -> None:
    objecting = FakeFiscalService(
        CheckDocumentResponse(
            applies=True, problems=[Problem(field="lines[0].tax", code="tax_rate_not_in_force")]
        )
    )
    elsewhere = FakeFiscalService(
        CheckDocumentResponse(applies=False, problems=[Problem(field="ignored", code="ignored")])
    )
    async with AsyncExitStack() as stack:
        rules = RemoteFiscalRules([await served(stack, objecting), await served(stack, elsewhere)])
        verdict = await rules.check(ISSUED)
    assert verdict.applies
    assert [(each.field, each.code) for each in verdict.problems] == [
        ("lines[0].tax", "tax_rate_not_in_force")
    ]
    ((asked,), (also,)) = objecting.asked, elsewhere.asked
    assert asked == also
    assert (asked.document.document_id, asked.document.version) == (str(ISSUED.document), ISSUED.version)


async def test_no_fiscal_service_applying_means_no_fiscal_life() -> None:
    async with AsyncExitStack() as stack:
        rules = RemoteFiscalRules(
            [await served(stack, FakeFiscalService(CheckDocumentResponse(applies=False)))]
        )
        assert not (await rules.check(ISSUED)).applies
    assert not (await RemoteFiscalRules([]).check(ISSUED)).applies


async def test_a_fiscal_service_that_cannot_answer_stops_the_issue() -> None:
    async with Channel("127.0.0.1:1", caller="invoicing", version="0.1.0").opened() as nowhere:
        with pytest.raises(RemoteUnavailableError):
            await RemoteFiscalRules([nowhere], deadline=0.5).check(ISSUED)


def test_a_deadline_that_cannot_work_is_refused_when_the_client_is_made() -> None:
    with pytest.raises(InvalidGrpcClientOptionError):
        RemoteFiscalRules([], deadline=0)


def test_what_a_service_answers_is_bounded_before_it_reaches_a_client() -> None:
    hostile = CheckDocumentResponse(
        applies=True,
        problems=[
            Problem(field="x" * 200, code="Not A Code"),
            Problem(field="lines[0]\n", code="fine_code"),
            *(Problem(field=f"lines[{index}]", code="many") for index in range(25)),
        ],
        omitted=-5,
    )
    verdict = verdict_of([hostile, CheckDocumentResponse(applies=True, omitted=3)])
    assert verdict.problems[:2] == (
        problems.Problem("document", "unknown"),
        problems.Problem("document", "fine_code"),
    )
    assert (len(verdict.problems), verdict.omitted) == (problems.MAX_PROBLEMS, 3 + 7)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest services/invoicing/tests/test_repositories.py services/invoicing/tests/test_wire.py services/invoicing/tests/test_fiscal_rules.py -q --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'ledger_invoicing.adapters.postgres.codec'`.

- [ ] **Step 4: Implement**

`services/invoicing/src/ledger_invoicing/adapters/postgres/codec.py`:

```python
"""A document's content as ``jsonb`` and back: decimals as text (never a JSON number), dates in ISO 8601.

Reading validates the stored JSON against the shape writing gives it (a ``TypedDict`` per value, through
pydantic, strictly): a row someone edited by hand into another shape fails loudly where it is read, instead
of becoming a document no rule ever checked.
"""

from datetime import date
from decimal import Decimal
from types import MappingProxyType
from typing import Final, TypedDict
from uuid import UUID

from pydantic import TypeAdapter

from ledger_invoicing.domain.content import (
    Address,
    Content,
    Line,
    Party,
    Payment,
    Reference,
    Tax,
    Terms,
    Withholding,
)
from ledger_invoicing.domain.fiscal import FiscalMessage, FiscalRecord
from ledger_invoicing.domain.ids import DocumentId, EmissionPointId
from ledger_invoicing.domain.vocabulary import (
    Correction,
    DocumentKind,
    FiscalStatus,
    ItemKind,
    PaymentMethod,
    PaymentTerms,
    TaxCategory,
)
from ledger_kernel.money import Currency, Money


class AddressJson(TypedDict):
    """An ``Address``."""

    line: str | None
    city_code: str | None
    region_code: str | None
    country_code: str | None


class PartyJson(TypedDict):
    """A ``Party``."""

    tax_id: str | None
    foreign_id: str | None
    name: str | None
    email: str | None
    address: AddressJson | None


class WithholdingJson(TypedDict):
    """A ``Withholding``: amounts as text."""

    value_added_tax: str | None
    income_tax: str | None


class LineJson(TypedDict):
    """A ``Line``: decimals as text, its tax flattened."""

    number: int
    kind: str
    name: str
    description: str | None
    quantity: str
    unit_code: str | None
    unit_price: str
    discount: str | None
    tax_category: str
    tax_rate: str
    withholding: WithholdingJson | None


class PaymentJson(TypedDict):
    """A ``Payment``."""

    method: str
    amount: str


class ReferenceJson(TypedDict):
    """A ``Reference``."""

    document: str
    correction: str
    reason: str


class ContentJson(TypedDict):
    """A ``Content``: the ``jsonb`` of a row's ``content``."""

    kind: str
    emission_point: str
    issue_date: str
    currency: str
    prices_include_tax: bool
    customer: PartyJson | None
    lines: list[LineJson]
    terms: str
    due_date: str | None
    payments: list[PaymentJson]
    reference: ReferenceJson | None
    fiscal_fields: dict[str, str]
    external_reference: str | None


class MessageJson(TypedDict):
    """A ``FiscalMessage``."""

    code: str
    text: str


class FiscalJson(TypedDict):
    """A ``FiscalRecord``: the ``jsonb`` of a row's ``fiscal``."""

    status: str
    number: str | None
    messages: list[MessageJson]
    version: int


_CONTENT: Final = TypeAdapter(ContentJson)
_FISCAL: Final = TypeAdapter(FiscalJson)


def _text(value: Decimal) -> str:
    return format(value, "f")


def _amount(value: Money | None) -> str | None:
    return None if value is None else _text(value.amount)


def _party(party: Party | None) -> PartyJson | None:
    if party is None:
        return None
    address = party.address
    return {
        "tax_id": party.tax_id,
        "foreign_id": party.foreign_id,
        "name": party.name,
        "email": party.email,
        "address": None
        if address is None
        else {
            "line": address.line,
            "city_code": address.city_code,
            "region_code": address.region_code,
            "country_code": address.country_code,
        },
    }


def _line(line: Line) -> LineJson:
    held = line.withholding
    return {
        "number": line.number,
        "kind": line.kind.value,
        "name": line.name,
        "description": line.description,
        "quantity": _text(line.quantity),
        "unit_code": line.unit_code,
        "unit_price": _text(line.unit_price),
        "discount": _amount(line.discount),
        "tax_category": line.tax.category.value,
        "tax_rate": _text(line.tax.rate),
        "withholding": None
        if held is None
        else {"value_added_tax": _amount(held.value_added_tax), "income_tax": _amount(held.income_tax)},
    }


def content_json(content: Content) -> ContentJson:
    """``content`` as the ``jsonb`` of its row."""
    reference = content.reference
    return {
        "kind": content.kind.value,
        "emission_point": str(content.emission_point),
        "issue_date": content.issue_date.isoformat(),
        "currency": content.currency.value,
        "prices_include_tax": content.prices_include_tax,
        "customer": _party(content.customer),
        "lines": [_line(line) for line in content.lines],
        "terms": content.terms.kind.value,
        "due_date": None if content.terms.due_date is None else content.terms.due_date.isoformat(),
        "payments": [
            {"method": each.method.value, "amount": _text(each.amount.amount)} for each in content.payments
        ],
        "reference": None
        if reference is None
        else {
            "document": str(reference.document),
            "correction": reference.correction.value,
            "reason": reference.reason,
        },
        "fiscal_fields": dict(content.fiscal_fields),
        "external_reference": content.external_reference,
    }


def _money(value: str | None, currency: Currency) -> Money | None:
    return None if value is None else Money.of(value, currency)


def _read_party(party: PartyJson | None) -> Party | None:
    if party is None:
        return None
    address = party["address"]
    return Party(
        tax_id=party["tax_id"],
        foreign_id=party["foreign_id"],
        name=party["name"],
        email=party["email"],
        address=None
        if address is None
        else Address(
            line=address["line"],
            city_code=address["city_code"],
            region_code=address["region_code"],
            country_code=address["country_code"],
        ),
    )


def _read_line(line: LineJson, currency: Currency) -> Line:
    held = line["withholding"]
    return Line(
        number=line["number"],
        kind=ItemKind(line["kind"]),
        name=line["name"],
        description=line["description"],
        quantity=Decimal(line["quantity"]),
        unit_code=line["unit_code"],
        unit_price=Decimal(line["unit_price"]),
        discount=_money(line["discount"], currency),
        tax=Tax(TaxCategory(line["tax_category"]), Decimal(line["tax_rate"])),
        withholding=None
        if held is None
        else Withholding(
            value_added_tax=_money(held["value_added_tax"], currency),
            income_tax=_money(held["income_tax"], currency),
        ),
    )


def content_of(stored: object) -> Content:
    """The content a row's ``jsonb`` holds (see the module)."""
    data = _CONTENT.validate_python(stored, strict=True)
    currency = Currency(data["currency"])
    reference = data["reference"]
    due = data["due_date"]
    return Content(
        kind=DocumentKind(data["kind"]),
        emission_point=EmissionPointId(UUID(data["emission_point"])),
        issue_date=date.fromisoformat(data["issue_date"]),
        currency=currency,
        prices_include_tax=data["prices_include_tax"],
        customer=_read_party(data["customer"]),
        lines=tuple(_read_line(line, currency) for line in data["lines"]),
        terms=Terms(
            kind=PaymentTerms(data["terms"]), due_date=None if due is None else date.fromisoformat(due)
        ),
        payments=tuple(
            Payment(PaymentMethod(each["method"]), Money.of(each["amount"], currency))
            for each in data["payments"]
        ),
        reference=None
        if reference is None
        else Reference(
            document=DocumentId(UUID(reference["document"])),
            correction=Correction(reference["correction"]),
            reason=reference["reason"],
        ),
        fiscal_fields=MappingProxyType(dict(data["fiscal_fields"])),
        external_reference=data["external_reference"],
    )


def fiscal_json(record: FiscalRecord) -> FiscalJson:
    """A document's fiscal side as ``jsonb``."""
    return {
        "status": record.status.value,
        "number": record.number,
        "messages": [{"code": message.code, "text": message.text} for message in record.messages],
        "version": record.version,
    }


def fiscal_of(stored: object) -> FiscalRecord:
    """The fiscal side a row's ``jsonb`` holds."""
    data = _FISCAL.validate_python(stored, strict=True)
    return FiscalRecord(
        status=FiscalStatus(data["status"]),
        number=data["number"],
        messages=tuple(FiscalMessage(each["code"], each["text"]) for each in data["messages"]),
        version=data["version"],
    )
```

`services/invoicing/src/ledger_invoicing/adapters/postgres/repositories.py`:

```python
"""invoicing's documents in PostgreSQL, under the transaction's tenant (row-level security does the rest).

Every statement also names the company, so that the application's check and the database's agree (§8: tenancy
is checked twice). ``update`` is a compare and set on the version before the change: of two units that
changed one document at once, one keeps its change and the other fails instead of overwriting it.
"""

from datetime import datetime
from typing import ClassVar, Final, final, override
from uuid import UUID

from sqlalchemy import Row, insert, select, update
lazy from sqlalchemy import Select
lazy from sqlalchemy.ext.asyncio import AsyncConnection

from ledger_invoicing.adapters.postgres.codec import content_json, content_of, fiscal_json, fiscal_of
from ledger_invoicing.adapters.postgres.tables import DocumentRow
from ledger_invoicing.application.ports import Documents
from ledger_invoicing.domain.document import Document
from ledger_invoicing.domain.ids import DocumentId
from ledger_invoicing.domain.totals import compute
from ledger_invoicing.domain.vocabulary import DocumentKind, DocumentState
from ledger_kernel.errors import ConflictError
from ledger_kernel.money import Currency, Money
from ledger_kernel.pagination import page_of
lazy from ledger_kernel.ids import Tenant
lazy from ledger_kernel.pagination import Page, PageRequest

type DocumentRecord = Row[
    UUID,
    str,
    int,
    dict[str, object],
    dict[str, object],
    str | None,
    datetime,
    datetime,
    datetime | None,
]
_UNCOUNTED: Final = (
    DocumentState.DRAFT.value,
    DocumentState.VOIDED.value,
    DocumentState.FISCALLY_REJECTED.value,
)


class LostUpdateError(ConflictError):
    """A document changed under a unit that had not locked it: the change is refused, never overwritten."""

    code: ClassVar[str] = "invoice_changed"

    def __init__(self) -> None:
        super().__init__("the document changed while it was being changed: try again")


def _selected() -> Select[
    UUID,
    str,
    int,
    dict[str, object],
    dict[str, object],
    str | None,
    datetime,
    datetime,
    datetime | None,
]:
    return select(
        DocumentRow.id,
        DocumentRow.state,
        DocumentRow.version,
        DocumentRow.content,
        DocumentRow.fiscal,
        DocumentRow.void_reason,
        DocumentRow.created_at,
        DocumentRow.updated_at,
        DocumentRow.issued_at,
    )


def _document(record: DocumentRecord, tenant: Tenant) -> Document:
    identifier, state, version, content, fiscal, void_reason, created_at, updated_at, issued_at = record
    read = content_of(content)
    return Document(
        id=DocumentId(identifier),
        tenant=tenant,
        content=read,
        totals=compute(read),
        state=DocumentState(state),
        fiscal=fiscal_of(fiscal),
        version=version,
        created_at=created_at,
        updated_at=updated_at,
        issued_at=issued_at,
        void_reason=void_reason,
    )


@final
class PgDocuments(Documents):
    """The documents of one company, in its transaction."""

    __slots__ = ("_connection", "_tenant")

    def __init__(self, connection: AsyncConnection, tenant: Tenant) -> None:
        self._connection = connection
        self._tenant = tenant

    def _mine(
        self,
    ) -> Select[
        UUID,
        str,
        int,
        dict[str, object],
        dict[str, object],
        str | None,
        datetime,
        datetime,
        datetime | None,
    ]:
        return _selected().where(DocumentRow.company_id == self._tenant.company)

    @override
    async def add(self, document: Document) -> None:
        content = document.content
        await self._connection.execute(
            insert(DocumentRow).values(
                id=document.id,
                organization_id=self._tenant.organization,
                company_id=self._tenant.company,
                kind=content.kind.value,
                state=document.state.value,
                version=document.version,
                emission_point_id=content.emission_point,
                issue_date=content.issue_date,
                currency=content.currency.value,
                total=document.totals.total.amount,
                reference_id=None if content.reference is None else content.reference.document,
                content=content_json(content),
                fiscal=fiscal_json(document.fiscal),
                void_reason=document.void_reason,
                created_at=document.created_at,
                updated_at=document.updated_at,
                issued_at=document.issued_at,
            )
        )

    @override
    async def get(self, document: DocumentId) -> Document | None:
        found = (await self._connection.execute(self._mine().where(DocumentRow.id == document))).one_or_none()
        return None if found is None else _document(found, self._tenant)

    @override
    async def locked(self, document: DocumentId) -> Document | None:
        statement = self._mine().where(DocumentRow.id == document).with_for_update()
        found = (await self._connection.execute(statement)).one_or_none()
        return None if found is None else _document(found, self._tenant)

    @override
    async def update(self, document: Document) -> None:
        content = document.content
        changed = await self._connection.execute(
            update(DocumentRow)
            .where(
                DocumentRow.company_id == self._tenant.company,
                DocumentRow.id == document.id,
                DocumentRow.version == document.version - 1,
            )
            .values(
                state=document.state.value,
                version=document.version,
                issue_date=content.issue_date,
                currency=content.currency.value,
                total=document.totals.total.amount,
                emission_point_id=content.emission_point,
                reference_id=None if content.reference is None else content.reference.document,
                content=content_json(content),
                fiscal=fiscal_json(document.fiscal),
                void_reason=document.void_reason,
                updated_at=document.updated_at,
                issued_at=document.issued_at,
            )
        )
        if changed.rowcount != 1:
            raise LostUpdateError

    @override
    async def credited(self, document: DocumentId) -> Money | None:
        statement = select(DocumentRow.total, DocumentRow.currency).where(
            DocumentRow.company_id == self._tenant.company,
            DocumentRow.reference_id == document,
            DocumentRow.kind == DocumentKind.CREDIT_NOTE.value,
            DocumentRow.state.not_in(_UNCOUNTED),
        )
        notes = [
            Money(total, Currency(currency)) for total, currency in await self._connection.execute(statement)
        ]
        if not notes:
            return None
        credited = notes[0]
        for each in notes[1:]:
            credited += each
        return credited

    @override
    async def page(self, request: PageRequest) -> Page[Document]:
        statement = self._mine().order_by(DocumentRow.id).limit(request.size + 1)
        if request.after is not None:
            statement = statement.where(DocumentRow.id > request.after)
        rows = (await self._connection.execute(statement)).all()
        return page_of([_document(row, self._tenant) for row in rows], request, key=lambda each: each.id)
```

`services/invoicing/src/ledger_invoicing/adapters/wire.py`:

```python
"""invoicing's documents and events on the wire, and the fiscal services' events read back into outcomes.

Documents are ``ledger.invoicing.v1``, events ``ledger.events.invoicing.v1``.
What invoicing writes, every fiscal service reads: decimals as plain text (``format(value, "f")``, never an
exponent), dates in ISO 8601, absent values as empty strings or absent messages (protobuf's defaults).
"""

from types import MappingProxyType
from typing import Final
from uuid import UUID
lazy from collections.abc import Iterable, Mapping

lazy from google.protobuf.message import Message

import ledger.events.fiscal_do.v1.events_pb2 as fiscal_wire
import ledger.events.invoicing.v1.events_pb2 as wire
import ledger.invoicing.v1.document_pb2 as document
from ledger.common.v1.money_pb2 import Money as WireMoney
from ledger_contracts import invoicing as subjects
from ledger_invoicing.application.outcomes import Decided, Numbered
from ledger_invoicing.domain.events import (
    DocumentFiscallyAccepted,
    DocumentFiscallyNumbered,
    DocumentIssued,
    DocumentVoided,
)
from ledger_invoicing.domain.fiscal import FiscalMessage
from ledger_invoicing.domain.ids import DocumentId
from ledger_invoicing.domain.vocabulary import (
    Correction,
    DocumentKind,
    ItemKind,
    PaymentMethod,
    PaymentTerms,
    TaxCategory,
)
lazy from ledger_contracts.envelope import Envelope
lazy from ledger_invoicing.application.outcomes import Outcome
lazy from ledger_invoicing.domain.content import Content, Line, Party
lazy from ledger_invoicing.domain.events import InvoicingEvent
lazy from ledger_invoicing.domain.totals import Totals
lazy from ledger_kernel.money import Money

KINDS: Final[Mapping[DocumentKind, document.DocumentKind.ValueType]] = MappingProxyType(
    {
        DocumentKind.INVOICE: document.DOCUMENT_KIND_INVOICE,
        DocumentKind.CREDIT_NOTE: document.DOCUMENT_KIND_CREDIT_NOTE,
        DocumentKind.DEBIT_NOTE: document.DOCUMENT_KIND_DEBIT_NOTE,
    }
)
_ITEMS: Final = MappingProxyType(
    {ItemKind.GOOD: document.ITEM_KIND_GOOD, ItemKind.SERVICE: document.ITEM_KIND_SERVICE}
)
_CATEGORIES: Final = MappingProxyType(
    {
        TaxCategory.STANDARD: document.TAX_CATEGORY_STANDARD,
        TaxCategory.REDUCED: document.TAX_CATEGORY_REDUCED,
        TaxCategory.ZERO: document.TAX_CATEGORY_ZERO,
        TaxCategory.EXEMPT: document.TAX_CATEGORY_EXEMPT,
        TaxCategory.OUTSIDE_SCOPE: document.TAX_CATEGORY_OUTSIDE_SCOPE,
    }
)
_TERMS: Final = MappingProxyType(
    {
        PaymentTerms.CASH: document.PAYMENT_TERMS_KIND_CASH,
        PaymentTerms.CREDIT: document.PAYMENT_TERMS_KIND_CREDIT,
        PaymentTerms.FREE: document.PAYMENT_TERMS_KIND_FREE,
    }
)
_METHODS: Final = MappingProxyType(
    {
        PaymentMethod.CASH: document.PAYMENT_METHOD_CASH,
        PaymentMethod.BANK_TRANSFER: document.PAYMENT_METHOD_BANK_TRANSFER,
        PaymentMethod.CARD: document.PAYMENT_METHOD_CARD,
        PaymentMethod.CREDIT: document.PAYMENT_METHOD_CREDIT,
        PaymentMethod.VOUCHER: document.PAYMENT_METHOD_VOUCHER,
        PaymentMethod.BARTER: document.PAYMENT_METHOD_BARTER,
        PaymentMethod.CREDIT_NOTE: document.PAYMENT_METHOD_CREDIT_NOTE,
        PaymentMethod.OTHER: document.PAYMENT_METHOD_OTHER,
    }
)
_CORRECTIONS: Final = MappingProxyType(
    {
        Correction.ANNULMENT: document.CORRECTION_ANNULMENT,
        Correction.TEXT: document.CORRECTION_TEXT,
        Correction.AMOUNTS: document.CORRECTION_AMOUNTS,
    }
)


def money(value: Money | None) -> WireMoney | None:
    """An amount on the wire; ``None`` stays absent."""
    if value is None:
        return None
    return WireMoney(amount=format(value.amount, "f"), currency=value.currency.value)


def _party(party: Party | None) -> document.Party | None:
    if party is None:
        return None
    address = party.address
    return document.Party(
        tax_id=party.tax_id or "",
        foreign_id=party.foreign_id or "",
        name=party.name or "",
        email=party.email or "",
        address=None
        if address is None
        else document.Address(
            line=address.line or "",
            city_code=address.city_code or "",
            region_code=address.region_code or "",
            country_code=address.country_code or "",
        ),
    )


def _line(line: Line, amount: Money) -> document.Line:
    held = line.withholding
    return document.Line(
        number=line.number,
        kind=_ITEMS[line.kind],
        name=line.name,
        description=line.description or "",
        quantity=format(line.quantity, "f"),
        unit_code=line.unit_code or "",
        unit_price=format(line.unit_price, "f"),
        discount=money(line.discount),
        tax=document.Tax(category=_CATEGORIES[line.tax.category], rate=format(line.tax.rate, "f")),
        withholding=None
        if held is None
        else document.Withholding(
            value_added_tax=money(held.value_added_tax), income_tax=money(held.income_tax)
        ),
        amount=money(amount),
    )


def _totals(totals: Totals) -> document.Totals:
    return document.Totals(
        lines=money(totals.lines),
        discounts=money(totals.discounts),
        groups=[
            document.TaxGroup(
                category=_CATEGORIES[group.category],
                rate=format(group.rate, "f"),
                base=money(group.base),
                tax=money(group.tax),
            )
            for group in totals.groups
        ],
        tax=money(totals.tax),
        total=money(totals.total),
        withheld_value_added_tax=money(totals.withheld_value_added_tax),
        withheld_income_tax=money(totals.withheld_income_tax),
    )


def _content(content: Content, totals: Totals, into: document.CommercialDocument) -> None:
    into.kind = KINDS[content.kind]
    into.emission_point_id = str(content.emission_point)
    into.issue_date = content.issue_date.isoformat()
    into.currency = content.currency.value
    into.prices_include_tax = content.prices_include_tax
    customer = _party(content.customer)
    if customer is not None:
        into.customer.CopyFrom(customer)
    into.lines.extend(
        _line(line, amount) for line, amount in zip(content.lines, totals.line_amounts, strict=True)
    )
    into.terms.CopyFrom(
        document.Terms(
            kind=_TERMS[content.terms.kind],
            due_date="" if content.terms.due_date is None else content.terms.due_date.isoformat(),
        )
    )
    into.payments.extend(
        document.Payment(method=_METHODS[payment.method], amount=money(payment.amount))
        for payment in content.payments
    )
    reference = content.reference
    if reference is not None:
        into.reference.CopyFrom(
            document.Reference(
                document_id=str(reference.document),
                correction=_CORRECTIONS[reference.correction],
                reason=reference.reason,
            )
        )
    into.totals.CopyFrom(_totals(totals))
    into.fiscal_fields.update(content.fiscal_fields)
    into.external_reference = content.external_reference or ""


def commercial_document(issued: DocumentIssued) -> document.CommercialDocument:
    """The document ``issued`` announces, as every fiscal service reads it."""
    written = document.CommercialDocument(
        document_id=str(issued.document),
        organization_id=str(issued.tenant.organization),
        company_id=str(issued.tenant.company),
        version=issued.version,
    )
    _content(issued.content, issued.totals, written)
    return written


def _messages(messages: Iterable[FiscalMessage]) -> list[wire.FiscalMessage]:
    return [wire.FiscalMessage(code=message.code, text=message.text) for message in messages]


def outgoing(event: InvoicingEvent) -> tuple[Message, str]:
    """``event`` as its message and the subject it is published on."""
    match event:
        case DocumentIssued():
            return wire.DocumentIssued(document=commercial_document(event)), subjects.DOCUMENT_ISSUED
        case DocumentVoided():
            voided = wire.DocumentVoided(
                document_id=str(event.document),
                organization_id=str(event.tenant.organization),
                company_id=str(event.tenant.company),
                kind=KINDS[event.kind],
                version=event.version,
                reason=event.reason,
            )
            return voided, subjects.DOCUMENT_VOIDED
        case DocumentFiscallyNumbered():
            numbered = wire.DocumentFiscallyNumbered(
                document_id=str(event.document),
                organization_id=str(event.tenant.organization),
                company_id=str(event.tenant.company),
                kind=KINDS[event.kind],
                version=event.version,
                fiscal_number=event.fiscal_number,
            )
            return numbered, subjects.DOCUMENT_FISCALLY_NUMBERED
        case DocumentFiscallyAccepted():
            accepted = wire.DocumentFiscallyAccepted(
                document_id=str(event.document),
                organization_id=str(event.tenant.organization),
                company_id=str(event.tenant.company),
                kind=KINDS[event.kind],
                version=event.version,
                fiscal_number=event.fiscal_number,
                conditional=event.conditional,
                messages=_messages(event.messages),
            )
            return accepted, subjects.DOCUMENT_FISCALLY_ACCEPTED
        case _:
            rejected = wire.DocumentFiscallyRejected(
                document_id=str(event.document),
                organization_id=str(event.tenant.organization),
                company_id=str(event.tenant.company),
                kind=KINDS[event.kind],
                version=event.version,
                fiscal_number=event.fiscal_number or "",
                declined=event.declined,
                messages=_messages(event.messages),
            )
            return rejected, subjects.DOCUMENT_FISCALLY_REJECTED


def _read(messages: Iterable[fiscal_wire.DgiiMessage]) -> tuple[FiscalMessage, ...]:
    return tuple(FiscalMessage.of(message.code, message.text) for message in messages)


def outcome_of(envelope: Envelope) -> Outcome | None:
    """What a fiscal-do event says of an invoicing document; ``None`` for an event invoicing does not mirror.

    The anti-corruption layer's way back: DGII's vocabulary (an e-NCF, DGII's messages) becomes a fiscal
    number and a tax authority's messages.
    """
    match envelope.type:
        case fiscal_wire.DocumentNumbered.DESCRIPTOR.full_name:
            numbered = envelope.unwrap(fiscal_wire.DocumentNumbered)
            return Numbered(
                document=DocumentId(UUID(numbered.document_id)),
                fiscal_version=numbered.version,
                number=numbered.encf,
            )
        case fiscal_wire.DocumentAccepted.DESCRIPTOR.full_name:
            accepted = envelope.unwrap(fiscal_wire.DocumentAccepted)
            return Decided(
                document=DocumentId(UUID(accepted.document_id)),
                fiscal_version=accepted.version,
                number=accepted.encf,
                accepted=True,
            )
        case fiscal_wire.DocumentConditionallyAccepted.DESCRIPTOR.full_name:
            conditional = envelope.unwrap(fiscal_wire.DocumentConditionallyAccepted)
            return Decided(
                document=DocumentId(UUID(conditional.document_id)),
                fiscal_version=conditional.version,
                number=conditional.encf,
                accepted=True,
                conditional=True,
                messages=_read(conditional.messages),
            )
        case fiscal_wire.DocumentRejected.DESCRIPTOR.full_name:
            rejected = envelope.unwrap(fiscal_wire.DocumentRejected)
            return Decided(
                document=DocumentId(UUID(rejected.document_id)),
                fiscal_version=rejected.version,
                number=rejected.encf,
                accepted=False,
                messages=_read(rejected.messages),
            )
        case fiscal_wire.DocumentDeclined.DESCRIPTOR.full_name:
            declined = envelope.unwrap(fiscal_wire.DocumentDeclined)
            return Decided(
                document=DocumentId(UUID(declined.document_id)),
                fiscal_version=declined.version,
                number=None,
                accepted=False,
                messages=_read(declined.messages),
            )
        case _:
            return None
```

`services/invoicing/src/ledger_invoicing/adapters/events.py`:

```python
"""What invoicing announces, through the outbox: each event in an envelope, in its change's transaction."""

from typing import Final, final, override

from ledger_contracts import invoicing as subjects
from ledger_contracts.envelope import Envelope
from ledger_invoicing.adapters.wire import outgoing
from ledger_invoicing.application.ports import Events
from ledger_platform.streams import StreamSpec
lazy from ledger_invoicing.domain.events import InvoicingEvent
lazy from ledger_kernel.clock import Clock
lazy from ledger_platform.database import UnitOfWork
lazy from ledger_platform.outbox import Outbox

INVOICING: Final = StreamSpec(name=subjects.STREAM, subjects=subjects.SUBJECTS)
"""The stream of invoicing's events, declared by invoicing at start."""
SOURCE: Final = "invoicing"


@final
class OutboxEvents(Events):
    """Events added to ``unit``'s outbox, published once it commits."""

    __slots__ = ("_clock", "_outbox", "_unit")

    def __init__(self, unit: UnitOfWork, outbox: Outbox, *, clock: Clock) -> None:
        self._unit = unit
        self._outbox = outbox
        self._clock = clock

    @override
    async def publish(self, event: InvoicingEvent) -> None:
        message, subject = outgoing(event)
        envelope = Envelope.wrap(message, source=SOURCE, tenant=event.tenant, clock=self._clock)
        await self._outbox.add(self._unit, envelope, subject=subject)
```

`services/invoicing/src/ledger_invoicing/adapters/postgres/units.py`:

```python
"""invoicing's unit of work over one chassis transaction scoped to a company: its documents and its outbox."""

from contextlib import asynccontextmanager
from typing import ClassVar, final, override
lazy from collections.abc import AsyncGenerator

from ledger_invoicing.adapters.events import OutboxEvents
from ledger_invoicing.adapters.postgres.repositories import PgDocuments
from ledger_invoicing.application.ports import InvoicingUnit, Units
from ledger_kernel.clock import utc_now
from ledger_kernel.errors import LedgerError
from ledger_kernel.ids import Tenant
lazy from ledger_kernel.clock import Clock
lazy from ledger_platform.database import Database, UnitOfWork
lazy from ledger_platform.outbox import Outbox


class UnscopedUnitError(LedgerError):
    """invoicing's state was to be read in a transaction scoped to no company: a defect of the caller."""

    code: ClassVar[str] = "unscoped_unit"

    def __init__(self) -> None:
        super().__init__("invoicing's state is read and changed in a transaction scoped to one company")


def bind(unit: UnitOfWork, outbox: Outbox, *, clock: Clock = utc_now) -> InvoicingUnit:
    """The invoicing view of ``unit``, a transaction scoped to a company; ``clock`` is read once."""
    tenant = unit.scope
    if not isinstance(tenant, Tenant):
        raise UnscopedUnitError
    now = clock()
    return InvoicingUnit(
        tenant=tenant,
        documents=PgDocuments(unit.connection, tenant),
        events=OutboxEvents(unit, outbox, clock=lambda: now),
        now=now,
    )


@final
class PgUnits(Units):
    """Units of work over invoicing's database."""

    __slots__ = ("_clock", "_database", "_outbox")

    def __init__(self, database: Database, outbox: Outbox, *, clock: Clock = utc_now) -> None:
        self._database = database
        self._outbox = outbox
        self._clock = clock

    @override
    @asynccontextmanager
    async def unit(self, tenant: Tenant) -> AsyncGenerator[InvoicingUnit]:
        async with self._database.transaction(tenant) as transaction:
            yield bind(transaction, self._outbox, clock=self._clock)
```

`services/invoicing/src/ledger_invoicing/adapters/fiscal_rules.py`:

```python
"""The fiscal services, asked over gRPC (``ledger.fiscal.v1.FiscalRulesService``) about a document.

Every service invoicing knows is asked at once, each within the deadline; the document is the services' that
say it applies to them, and what any of those finds is a problem. One that cannot answer stops the issue
(``RemoteUnavailableError``, a 503): a document is never issued past a fiscal service that could not look at
it. What a service answers is another process's text: each field and code is bounded before it can reach a
client, and a code that is not a stable code (``snake_case``) is ``unknown``.
"""

import asyncio
import re
from typing import Final, final, override
lazy from collections.abc import Sequence

from ledger.fiscal.v1.fiscal_rules_pb2 import CheckDocumentRequest
from ledger.fiscal.v1.fiscal_rules_pb2_grpc import FiscalRulesServiceStub
from ledger_contracts.fiscal import CHECK_DOCUMENT, MAX_PROBLEMS
from ledger_invoicing.adapters.wire import commercial_document
from ledger_invoicing.application.ports import FiscalRules, FiscalVerdict
from ledger_invoicing.domain.problems import Problem
from ledger_platform.grpc_client import DEFAULT_DEADLINE, call_unary, require_deadline
lazy from ledger.fiscal.v1.fiscal_rules_pb2 import CheckDocumentResponse
lazy from ledger_invoicing.domain.events import DocumentIssued
lazy from ledger_platform.grpc_client import Channel

MAX_FIELD: Final = 128
MAX_CODE: Final = 64
_CODE: Final = re.compile(rf"[a-z][a-z0-9_]{{0,{MAX_CODE - 1}}}", re.ASCII)
_FIELD: Final = re.compile(rf"[!-~]{{1,{MAX_FIELD}}}", re.ASCII)
_UNKNOWN: Final = "unknown"


def _problem(field: str, code: str) -> Problem:
    """A remote service's problem, bounded: a field of visible ASCII, a stable code or ``unknown``."""
    return Problem(
        field if _FIELD.fullmatch(field) else "document",
        code if _CODE.fullmatch(code) else _UNKNOWN,
    )


def verdict_of(answers: Sequence[CheckDocumentResponse]) -> FiscalVerdict:
    """What the services' ``answers`` say together (see the module)."""
    applying = [answer for answer in answers if answer.applies]
    problems = tuple(_problem(each.field, each.code) for answer in applying for each in answer.problems)
    omitted = sum(max(answer.omitted, 0) for answer in applying) + max(len(problems) - MAX_PROBLEMS, 0)
    return FiscalVerdict(applies=bool(applying), problems=problems[:MAX_PROBLEMS], omitted=omitted)


@final
class RemoteFiscalRules(FiscalRules):
    """The fiscal services behind ``channels`` (none: no document has a fiscal life)."""

    __slots__ = ("_channels", "_deadline")

    def __init__(self, channels: Sequence[Channel], *, deadline: float = DEFAULT_DEADLINE) -> None:
        require_deadline(deadline)
        self._channels = tuple(channels)
        self._deadline = deadline

    async def _ask(self, channel: Channel, request: CheckDocumentRequest) -> CheckDocumentResponse:
        stub = FiscalRulesServiceStub(channel.current())
        return await call_unary(stub.CheckDocument, request, rpc=CHECK_DOCUMENT, deadline=self._deadline)

    @override
    async def check(self, issued: DocumentIssued) -> FiscalVerdict:
        request = CheckDocumentRequest(document=commercial_document(issued))
        answers = await asyncio.gather(*(self._ask(channel, request) for channel in self._channels))
        return verdict_of(answers)
```

(The rows' columns are read by unpacking the typed `Row`: `select` is typed up to ten columns in SQLAlchemy 2.1, so a document's fiscal side is one `jsonb` column. `Row.tuple()` is deprecated since 2.0.19 and raises a warning, which the tests treat as an error.)

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest services/invoicing/tests -q --no-cov`
Expected: PASS: a document reads back as kept, a stale update is refused, credited totals count only issued credit notes, a company never sees nor refers to another's documents, the outbox holds the document whole, fiscal-do's events read in invoicing's words, every fiscal service asked and bounded.

- [ ] **Step 6: Gate**

Run: `uv run poe fmt && uv run poe check`
Expected: every step passes, coverage 100 %.

- [ ] **Step 7: Commit**

```bash
git add uv.lock services/invoicing
git commit -m "feat(invoicing): documents in PostgreSQL, events through the outbox, fiscal services over gRPC

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: invoicing's public API, its consumer of fiscal outcomes, the service assembled

**Files:**
- Create: `services/invoicing/src/ledger_invoicing/resources.py`, `api/__init__.py`, `api/context.py`, `api/models.py`, `api/invoices.py`, `api/consumers.py`, `main.py`, `openapi.py`, `services/invoicing/openapi.json` (generated)
- Modify: `services/invoicing/pyproject.toml`, `services/invoicing/tests/conftest.py`, `pyproject.toml` (root)
- Test: `services/invoicing/tests/invoicing_remotes.py` (support), `services/invoicing/tests/test_service.py`, `services/invoicing/tests/test_openapi.py`

**Interfaces:**
- Consumes: Tasks 3-6; `Service`, `Interfaces`, `Events`, `Runtime`, `RuntimeDependency`; `authenticated`, `company_in_header`, `page_request`, `if_match`, `IfMatch`, `etag`, `precondition_failed`, `idempotency_key`, `attempt_of`, `json_response`, `answer` (`ledger_platform.routes`); `CachedPermissions`, `CompanyAccess`, `PermissionSource`; `RemotePermissions`, `GET_PERMISSIONS`; `HttpKeys`, `PublishedKeys`, `TokenPolicy`, `TokenVerifier`, `organization_of`; `Idempotency`; `ConsumerSpec`; `PROBLEM_JSON`; `ensure_stream`; `Permission`; `LocalIssuer`, `Caller`, `created`, `document`, `page` (`ledger_testing.api`, M2 Task 13), `nats_server`, `nats_address`, `eventually`.
- Produces: `resources`: `VERSION`, `InvoicingResources(permissions, rules)`, `opened(settings, *, keys)`. `api.context`: `Problem`, `Call` (`transaction`, `unit`, `rules`, `attempt`), `CallDependency`, `ApiContext` (`build`), `problems(*statuses)`, `precondition_checked()`. `api.models`: `DECIMAL`, the shapes (`AddressShape`, `PartyShape`, `TaxShape`, `WithholdingShape`, `LineShape`, `TermsShape`, `PaymentShape`, `ReferenceShape`, `InvoiceContent`, `NewInvoice`, `VoidRequest`) and views (`TaxGroupView`, `TotalsView`, `LineView`, `MessageView`, `FiscalView`, `InvoiceView`, `InvoicePage`). Routes (company in `Ledger-Company-Id`): `POST /v1/invoices` (201, `Location`, `ETag`; `Idempotency-Key`; `invoice.issue`; `draft: true` keeps a draft), `GET /v1/invoices` (`limit`, `cursor`; `invoice.read`), `GET /v1/invoices/{invoice_id}` (`ETag`), `PUT /v1/invoices/{invoice_id}` (a draft; `If-Match`), `POST /v1/invoices/{invoice_id}/issue` (`If-Match`, `Idempotency-Key`), `POST /v1/invoices/{invoice_id}/void` (`If-Match`, `Idempotency-Key`). `api.invoices`: `PREFIX`, `TAG`, `routers(context)`. `api.consumers`: `FISCAL_OUTCOMES` (`invoicing-fiscal-outcomes`), `FISCAL_DOCUMENTS` (`fiscal.document.>`), `consumers(runtime)`. `main`: `build(settings)` (check `migrations`), `create_app()`. `openapi`: `document()`, `main()` (console script `ledger-invoicing-openapi PATH`). Test support `invoicing_remotes`: `EVERYTHING`, `FakeIdentity`, `FakeFiscalService` (`answer`, `asked`, `down`), `Fakes`, `serving(fakes)`.

Re-check before starting: `ledger_testing.api` as Milestone 2 landed it; `company_in_header(caller, permissions)` and the chassis's readiness names (`database`, `nats`, `workers`, `schema`, as `test_invoicing_is_ready_once_its_schema_is_this_version_s` reads them); whether Milestone 2 moved `problems()` and `Problem` into the platform (then invoicing imports them and drops its own); Milestone 2's `openapi` task in the root `pyproject.toml` (this task adds invoicing's document to it).

- [ ] **Step 1: Dependencies, layers, the published document**

`services/invoicing/pyproject.toml` — its final form:

```toml
[project]
name = "ledger-invoicing"
version = "0.1.0"
description = "The invoicing service: commercial documents, country-agnostic, from draft to their tax authority's answer."
requires-python = ">=3.15"
dependencies = [
    "ledger-kernel",
    "ledger-contracts",
    "ledger-platform",
    "alembic>=1.20.0",
    "fastapi>=0.143.0",
    "granian[uvloop]>=2.8.4",
    "protobuf>=7.36.2",
    "pydantic>=2.14.1",
    "sqlalchemy>=2.1.4",
]

[project.scripts]
ledger-invoicing-migrate = "ledger_invoicing.adapters.postgres.migrate:main"
ledger-invoicing-openapi = "ledger_invoicing.openapi:main"

[tool.uv.sources]
ledger-kernel = { workspace = true }
ledger-contracts = { workspace = true }
ledger-platform = { workspace = true }

[build-system]
requires = ["uv_build>=0.13.0,<0.14.0"]
build-backend = "uv_build"

[tool.deptry.package_module_name_map]
"ledger-contracts" = ["ledger_contracts", "ledger"]
"protobuf" = "google"

[tool.deptry.per_rule_ignores]
DEP002 = ["granian"]  # the ASGI server the image runs the app with; no module imports it
```

Root `pyproject.toml`: "invoicing is hexagonal" `layers = ["openapi", "main", "api", "resources", "adapters", "settings", "application", "domain"]`; the poe `openapi` task also runs `ledger-invoicing-openapi services/invoicing/openapi.json`. Run: `uv lock && uv sync`

- [ ] **Step 2: Write the failing tests**

`services/invoicing/tests/invoicing_remotes.py`:

```python
"""The services invoicing calls, faked over real gRPC: identity (permissions) and a fiscal service."""

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Final, final, override
lazy from collections.abc import AsyncGenerator

import grpc
from prometheus_client import CollectorRegistry

from ledger.fiscal.v1.fiscal_rules_pb2 import CheckDocumentRequest, CheckDocumentResponse
from ledger.fiscal.v1.fiscal_rules_pb2_grpc import (
    FiscalRulesServiceServicer,
    add_FiscalRulesServiceServicer_to_server,
)
from ledger.identity.v1.identity_query_pb2 import (
    GetCompanyRequest,
    GetCompanyResponse,
    GetPermissionsRequest,
    GetPermissionsResponse,
)
from ledger.identity.v1.identity_query_pb2_grpc import (
    IdentityQueryServiceServicer,
    add_IdentityQueryServiceServicer_to_server,
)
from ledger_kernel.access import Permission
from ledger_kernel.errors import NotFoundError, UnavailableError
from ledger_platform.grpc_server import serve_grpc

type PermissionsContext = grpc.aio.ServicerContext[GetPermissionsRequest, GetPermissionsResponse]
type CompanyContext = grpc.aio.ServicerContext[GetCompanyRequest, GetCompanyResponse]
type CheckContext = grpc.aio.ServicerContext[CheckDocumentRequest, CheckDocumentResponse]

EVERYTHING: Final = frozenset({Permission.INVOICE_ISSUE, Permission.INVOICE_READ})


@final
class FakeIdentity(IdentityQueryServiceServicer):
    """identity as invoicing asks it: who may do what in which company (``grants[(company, subject)]``)."""

    def __init__(self) -> None:
        self.grants: dict[tuple[str, str], frozenset[Permission]] = {}

    @override
    async def GetCompany(self, request: GetCompanyRequest, context: CompanyContext) -> GetCompanyResponse:
        del request, context
        raise NotFoundError

    @override
    async def GetPermissions(
        self, request: GetPermissionsRequest, context: PermissionsContext
    ) -> GetPermissionsResponse:
        del context
        granted = self.grants.get((request.company_id, request.subject), frozenset())
        return GetPermissionsResponse(permissions=[permission.value for permission in granted])


@final
class FakeFiscalService(FiscalRulesServiceServicer):
    """A country's fiscal service that answers what the test says, and remembers what it was asked."""

    def __init__(self) -> None:
        self.answer = CheckDocumentResponse(applies=True)
        self.asked: list[CheckDocumentRequest] = []
        self.down = False

    @override
    async def CheckDocument(
        self, request: CheckDocumentRequest, context: CheckContext
    ) -> CheckDocumentResponse:
        del context
        if self.down:
            raise UnavailableError
        self.asked.append(request)
        return self.answer


@final
@dataclass(frozen=True, slots=True)
class Fakes:
    """The fakes a test's invoicing calls."""

    identity: FakeIdentity
    fiscal: FakeFiscalService


@asynccontextmanager
async def serving(fakes: Fakes) -> AsyncGenerator[tuple[str, str]]:
    """Both fakes served on free ports of 127.0.0.1: their ``host:port`` addresses (identity's, fiscal's)."""

    def register_identity(server: grpc.aio.Server) -> None:
        add_IdentityQueryServiceServicer_to_server(fakes.identity, server)

    def register_fiscal(server: grpc.aio.Server) -> None:
        add_FiscalRulesServiceServicer_to_server(fakes.fiscal, server)

    async with (
        serve_grpc(port=0, register=register_identity, registry=CollectorRegistry()) as identity_port,
        serve_grpc(port=0, register=register_fiscal, registry=CollectorRegistry()) as fiscal_port,
    ):
        yield f"127.0.0.1:{identity_port}", f"127.0.0.1:{fiscal_port}"
```

`services/invoicing/tests/conftest.py` becomes:

```python
"""Fixtures of invoicing's tests: PostgreSQL at this version, NATS, the identity provider, fakes over gRPC.

identity's and a country's fiscal service's fakes are served on real gRPC; invoicing starts on all of them.
"""

from typing import Final
lazy from collections.abc import AsyncGenerator, Callable, Generator, Iterator

import httpx
import pytest
from nats.aio.client import Client
from nats.js.client import JetStreamContext
from opentelemetry.sdk.trace import TracerProvider
from prometheus_client import CollectorRegistry
from pydantic import SecretStr
from sqlalchemy import text

from ledger_contracts import fiscal_do
from ledger_invoicing.adapters.postgres.migrate import upgrade
from ledger_invoicing.main import build
from ledger_invoicing.settings import InvoicingSettings
from ledger_kernel.ids import new_id
from ledger_platform.database import Database
from ledger_platform.outbox import Outbox
from ledger_platform.streams import StreamSpec, ensure_stream
from ledger_platform.tracing import Tracing
from ledger_testing.api import Caller
from ledger_testing.containers import Postgres, nats_address, nats_server
from ledger_testing.issuer import LocalIssuer
from ledger_testing.logs import restored_logging
lazy from ledger_platform.service import Service

from invoicing_remotes import FakeFiscalService, FakeIdentity, Fakes, serving

FISCAL_DO_STREAM: Final = StreamSpec(name=fiscal_do.STREAM, subjects=fiscal_do.SUBJECTS)
"""fiscal-do's stream, declared by the tests as fiscal-do does (invoicing reads it, never declares it)."""

EMPTY_EVERY_TABLE: Final = text("TRUNCATE documents, outbox, inbox, idempotency_keys")
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
    """invoicing's database, migrated to this version; the service connects as its owner, no superuser."""
    url = postgres.database("invoicing", role="invoicing", password="invoicing-password")
    upgrade(url)
    return url


@pytest.fixture
def fresh_database_url(postgres: Postgres) -> str:
    """An empty database of its own, owned by a role of its own: for the tests of the migrations."""
    name = f"fresh_{new_id().hex[16:]}"
    return postgres.database(name, role=name, password="fresh-password")


@pytest.fixture
async def database(database_url: str) -> AsyncGenerator[Database]:
    """A pool on invoicing's database, every table emptied after the test."""
    async with Database.open(database_url, pool_size=4) as opened:
        yield opened
        async with opened.engine.begin() as connection:
            await connection.execute(EMPTY_EVERY_TABLE)


@pytest.fixture
def outbox() -> Outbox:
    """An outbox of the test's own: its own tracer provider and metrics registry."""
    return Outbox(tracing=Tracing(TracerProvider()), registry=CollectorRegistry())


@pytest.fixture(scope="session")
def nats_url() -> Iterator[str]:
    """A real NATS server with JetStream."""
    with nats_server() as container:
        yield nats_address(container)


@pytest.fixture
async def jetstream(nats_url: str) -> AsyncGenerator[JetStreamContext]:
    """A JetStream client of the test's NATS, fiscal-do's stream declared as fiscal-do declares it.

    ``Client`` and ``JetStreamContext`` are built directly: nats-py's ``connect()`` and ``jetstream()`` take
    ``**options`` without a type, which strict type checking refuses to call.
    """
    client = Client()
    await client.connect(nats_url)
    try:
        context = JetStreamContext(client)
        await ensure_stream(context, FISCAL_DO_STREAM)
        yield context
    finally:
        await client.close()


@pytest.fixture
async def issuer() -> AsyncGenerator[LocalIssuer]:
    """The identity provider invoicing trusts in the tests."""
    async with LocalIssuer.started() as started:
        yield started


@pytest.fixture
def fakes() -> Fakes:
    """identity and a country's fiscal service: it applies to every company and objects to nothing."""
    return Fakes(identity=FakeIdentity(), fiscal=FakeFiscalService())


@pytest.fixture
def settings_for(nats_url: str, issuer: LocalIssuer) -> Callable[[str, str, str], InvoicingSettings]:
    """invoicing's settings on a database, identity's address and a fiscal service's."""

    def settings(database_url: str, identity: str, fiscal: str) -> InvoicingSettings:
        return InvoicingSettings(
            environment="test",
            database_url=SecretStr(database_url),
            nats_url=SecretStr(nats_url),
            token_issuer=issuer.issuer,
            token_audience=issuer.audience,
            token_jwks_url=issuer.keys_url,
            identity_address=identity,
            fiscal_rules_addresses=(fiscal,),
        )

    return settings


@pytest.fixture
async def service(
    database: Database,
    database_url: str,
    jetstream: JetStreamContext,
    settings_for: Callable[[str, str, str], InvoicingSettings],
    fakes: Fakes,
) -> AsyncGenerator[Service[InvoicingSettings]]:
    """invoicing, started on the test's PostgreSQL, NATS and fakes; ``database`` empties the tables later."""
    del database, jetstream
    async with serving(fakes) as (identity, fiscal):
        built = build(settings_for(database_url, identity, fiscal))
        async with built.app.router.lifespan_context(built.app):
            yield built


@pytest.fixture
async def client(service: Service[InvoicingSettings]) -> AsyncGenerator[httpx.AsyncClient]:
    """A client of invoicing's HTTP app."""
    transport = httpx.ASGITransport(app=service.app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://invoicing.test") as opened:
        yield opened


@pytest.fixture
def caller(issuer: LocalIssuer) -> Caller:
    """Subject ``2001`` of the provider's organization ``1001``."""
    return Caller(issuer)
```

`services/invoicing/tests/test_service.py`:

```python
from typing import Final

import pytest
from nats.errors import TimeoutError as NatsTimeoutError
lazy import httpx
lazy from nats.js.client import JetStreamContext

import ledger.events.fiscal_do.v1.events_pb2 as fiscal_wire
import ledger.events.invoicing.v1.events_pb2 as wire
from ledger.fiscal.v1.fiscal_rules_pb2 import CheckDocumentResponse, Problem
from ledger_contracts import fiscal_do, invoicing
from ledger_contracts.envelope import Envelope
from ledger_invoicing.api.consumers import FISCAL_OUTCOMES
from ledger_kernel.access import Permission
from ledger_kernel.ids import CompanyId, Tenant, new_id
from ledger_platform.authentication import organization_of
from ledger_testing.api import Caller, created, document, page
from ledger_testing.issuer import PROVIDER_ORGANIZATION, SUBJECT
from ledger_testing.waiting import eventually
lazy from ledger_invoicing.settings import InvoicingSettings
lazy from ledger_platform.service import Service

from invoicing_remotes import EVERYTHING
lazy from invoicing_remotes import Fakes

TENANT: Final = Tenant(organization_of(PROVIDER_ORGANIZATION), CompanyId(new_id()))
COMPANY: Final = str(TENANT.company)
INVOICE: Final[dict[str, object]] = {
    "kind": "invoice",
    "emission_point_id": str(new_id()),
    "issue_date": "2026-10-12",
    "currency": "DOP",
    "customer": {
        "tax_id": "101850043",
        "name": "Distribuidora del Caribe SRL",
        "address": {"line": "Av. Duarte 123", "country_code": "DO"},
    },
    "lines": [
        {
            "number": 1,
            "kind": "good",
            "name": "Item",
            "quantity": "2",
            "unit_price": "500.00",
            "tax": {"category": "standard", "rate": "18"},
        }
    ],
    "terms": {"kind": "cash"},
    "fiscal_fields": {"income_type": "01"},
}


def headers(caller: Caller, key: str | None = None, *, version: int | None = None) -> dict[str, str]:
    sent = caller.bearer() if key is None else caller.posting(key)
    sent["Ledger-Company-Id"] = COMPANY
    if version is not None:
        sent["If-Match"] = f'"{version}"'
    return sent


@pytest.fixture(autouse=True)
def granted(fakes: Fakes) -> None:
    fakes.identity.grants[COMPANY, SUBJECT] = EVERYTHING


async def announced(jetstream: JetStreamContext, subject: str, document_id: str) -> list[Envelope]:
    """The events on ``subject`` about ``document_id`` the invoicing stream holds."""
    subscription = await jetstream.pull_subscribe(subject, stream=invoicing.STREAM)
    try:
        messages = await subscription.fetch(1000, timeout=1)
    except NatsTimeoutError:
        return []
    finally:
        await subscription.unsubscribe()
    envelopes = [Envelope.from_headers(message.headers or {}, message.data) for message in messages]
    return [
        each
        for each in envelopes
        if str(each.tenant and each.tenant.company) == COMPANY and document_id in str(each.data)
    ]


async def test_an_invoice_is_checked_issued_and_announced_whole(
    client: httpx.AsyncClient, caller: Caller, fakes: Fakes, jetstream: JetStreamContext
) -> None:
    response = await client.post("/v1/invoices", json=INVOICE, headers=headers(caller, "i-1"))
    assert response.status_code == 201, response.text
    body = document(response)
    identifier = str(body["id"])
    assert (body["state"], body["version"], response.headers["ETag"]) == ("issued", 2, '"2"')
    assert response.headers["Location"] == f"/v1/invoices/{identifier}"
    assert body["totals"] == {
        "lines": "1000.00",
        "discounts": "0",
        "groups": [{"category": "standard", "rate": "18", "base": "1000.00", "tax": "180.00"}],
        "tax": "180.00",
        "total": "1180.00",
        "withheld_value_added_tax": "0",
        "withheld_income_tax": "0",
    }
    assert body["fiscal"] == {"status": "pending", "number": None, "messages": []}
    (asked,) = fakes.fiscal.asked
    assert asked.document.document_id == identifier

    async def published() -> bool:
        return len(await announced(jetstream, invoicing.DOCUMENT_ISSUED, identifier)) == 1

    await eventually(published)
    (event,) = await announced(jetstream, invoicing.DOCUMENT_ISSUED, identifier)
    assert event.unwrap(wire.DocumentIssued).document.totals.total.amount == "1180.00"


async def test_a_retry_answers_the_same_document_and_another_body_is_refused(
    client: httpx.AsyncClient, caller: Caller
) -> None:
    first = await client.post("/v1/invoices", json=INVOICE, headers=headers(caller, "i-1"))
    again = await client.post("/v1/invoices", json=INVOICE, headers=headers(caller, "i-1"))
    assert (again.status_code, again.content, again.headers["Idempotent-Replayed"]) == (
        201,
        first.content,
        "true",
    )
    other = await client.post(
        "/v1/invoices", json=INVOICE | {"issue_date": "2026-10-13"}, headers=headers(caller, "i-1")
    )
    assert other.status_code == 422
    assert len(page(await client.get("/v1/invoices", headers=headers(caller)))["items"]) == 1


async def test_a_fiscal_service_s_objection_is_answered_at_once_and_nothing_is_kept(
    client: httpx.AsyncClient, caller: Caller, fakes: Fakes
) -> None:
    fakes.fiscal.answer = CheckDocumentResponse(
        applies=True, problems=[Problem(field="lines[0].tax", code="tax_rate_not_in_force")]
    )
    response = await client.post("/v1/invoices", json=INVOICE, headers=headers(caller, "i-2"))
    assert response.status_code == 422
    problem = document(response)
    assert problem["code"] == "fiscal_invalid"
    assert "lines[0].tax: tax_rate_not_in_force" in str(problem["detail"])
    assert page(await client.get("/v1/invoices", headers=headers(caller)))["items"] == []


async def test_invoicing_s_own_rules_are_answered_with_every_problem_before_any_fiscal_service(
    client: httpx.AsyncClient, caller: Caller, fakes: Fakes
) -> None:
    terms: dict[str, object] = {"kind": "cash", "due_date": "2026-10-01"}
    wrong: dict[str, object] = {**INVOICE, "lines": [], "terms": terms}
    response = await client.post("/v1/invoices", json=wrong, headers=headers(caller, "i-3"))
    assert response.status_code == 422
    problem = document(response)
    assert problem["code"] == "invoice_invalid"
    assert "lines: lines_empty" in str(problem["detail"])
    assert "terms.due_date" in str(problem["detail"])
    assert fakes.fiscal.asked == []


async def test_a_draft_is_rewritten_and_issued_at_its_version(
    client: httpx.AsyncClient, caller: Caller
) -> None:
    draft_id = await created(client, "/v1/invoices", INVOICE | {"draft": True}, headers(caller, "d-1"))
    path = f"/v1/invoices/{draft_id}"
    changed = INVOICE | {"external_reference": "ORDER-7"}
    assert (await client.put(path, json=changed, headers=headers(caller))).status_code == 428
    assert (await client.put(path, json=changed, headers=headers(caller, version=9))).status_code == 412
    rewritten = await client.put(path, json=changed, headers=headers(caller, version=1))
    assert (rewritten.status_code, rewritten.headers["ETag"]) == (200, '"2"')
    stale = await client.post(f"{path}/issue", headers=headers(caller, "d-2", version=1))
    assert stale.status_code == 412
    issued = await client.post(f"{path}/issue", headers=headers(caller, "d-3", version=2))
    assert (issued.status_code, document(issued)["state"]) == (200, "issued")
    again = await client.post(f"{path}/issue", headers=headers(caller, "d-3", version=2))
    assert again.headers["Idempotent-Replayed"] == "true"
    twice = await client.post(f"{path}/issue", headers=headers(caller, "d-4", version=3))
    assert (twice.status_code, document(twice)["code"]) == (409, "invoice_state_conflict")
    read = await client.get(path, headers=headers(caller))
    assert (read.headers["ETag"], document(read)["external_reference"]) == ('"3"', "ORDER-7")


async def test_voiding_needs_the_version_and_a_reason(client: httpx.AsyncClient, caller: Caller) -> None:
    draft_id = await created(client, "/v1/invoices", INVOICE | {"draft": True}, headers(caller, "v-1"))
    path = f"/v1/invoices/{draft_id}/void"
    blank = await client.post(path, json={"reason": ""}, headers=headers(caller, "v-2", version=1))
    assert blank.status_code == 422
    voided = await client.post(path, json={"reason": "Duplicated"}, headers=headers(caller, "v-3", version=1))
    assert (voided.status_code, document(voided)["state"], document(voided)["void_reason"]) == (
        200,
        "voided",
        "Duplicated",
    )


async def test_the_fiscal_service_s_answer_is_mirrored_and_announced(
    client: httpx.AsyncClient, caller: Caller, jetstream: JetStreamContext
) -> None:
    invoice_id = await created(client, "/v1/invoices", INVOICE, headers(caller, "f-1"))
    accepted = fiscal_wire.DocumentConditionallyAccepted(
        document_id=invoice_id,
        organization_id=str(TENANT.organization),
        company_id=COMPANY,
        encf="E310000000001",
        version=4,
        messages=[fiscal_wire.DgiiMessage(code="613", text="Observación")],
    )
    envelope = Envelope.wrap(accepted, source="fiscal-do", tenant=TENANT)
    await jetstream.publish(
        fiscal_do.DOCUMENT_CONDITIONALLY_ACCEPTED, envelope.data, headers=envelope.headers()
    )

    async def mirrored() -> bool:
        read = document(await client.get(f"/v1/invoices/{invoice_id}", headers=headers(caller)))
        return read["state"] == "fiscally_accepted"

    await eventually(mirrored)
    read = document(await client.get(f"/v1/invoices/{invoice_id}", headers=headers(caller)))
    assert read["fiscal"] == {
        "status": "conditionally_accepted",
        "number": "E310000000001",
        "messages": [{"code": "613", "text": "Observación"}],
    }

    async def published() -> bool:
        return len(await announced(jetstream, invoicing.DOCUMENT_FISCALLY_ACCEPTED, invoice_id)) == 1

    await eventually(published)


async def test_what_is_not_about_one_of_invoicing_s_documents_changes_nothing(
    client: httpx.AsyncClient,
    caller: Caller,
    jetstream: JetStreamContext,
    service: Service[InvoicingSettings],
) -> None:
    invoice_id = await created(client, "/v1/invoices", INVOICE, headers(caller, "s-1"))
    stranger = fiscal_wire.DocumentAccepted(document_id=str(new_id()), encf="E310000000002", version=4)
    unknown = fiscal_wire.SequenceLow(sequence_id=str(new_id()), remaining=10)
    for subject, message in (
        (fiscal_do.DOCUMENT_ACCEPTED, stranger),
        ("fiscal.document.relabelled", unknown),
    ):
        envelope = Envelope.wrap(message, source="fiscal-do", tenant=TENANT)
        await jetstream.publish(subject, envelope.data, headers=envelope.headers())
    registry = service.runtime().registry
    labels = {"consumer": FISCAL_OUTCOMES, "outcome": "handled"}

    async def both_handled() -> bool:
        return (registry.get_sample_value("ledger_consumer_deliveries_total", labels) or 0) >= 2

    await eventually(both_handled)
    read = document(await client.get(f"/v1/invoices/{invoice_id}", headers=headers(caller)))
    assert read["state"] == "issued"


async def test_a_credit_note_of_a_settled_invoice_is_issued_through_the_api(
    client: httpx.AsyncClient, caller: Caller, fakes: Fakes
) -> None:
    fakes.fiscal.answer = CheckDocumentResponse(applies=False)
    invoice_id = await created(client, "/v1/invoices", INVOICE, headers(caller, "n-1"))
    note = INVOICE | {
        "kind": "credit_note",
        "reference": {"document_id": invoice_id, "correction": "amounts", "reason": "Discount"},
    }
    response = await client.post("/v1/invoices", json=note, headers=headers(caller, "n-2"))
    assert (response.status_code, document(response)["fiscal"]) == (
        201,
        {"status": "not_applicable", "number": None, "messages": []},
    )
    assert document(response)["reference"] == {
        "document_id": invoice_id,
        "correction": "amounts",
        "reason": "Discount",
    }
    over = await client.post("/v1/invoices", json=note, headers=headers(caller, "n-3"))
    assert (over.status_code, document(over)["code"]) == (422, "credit_exceeds_original")


async def test_who_may_do_what(client: httpx.AsyncClient, caller: Caller, fakes: Fakes) -> None:
    assert (await client.get("/v1/invoices", headers={"Ledger-Company-Id": COMPANY})).status_code == 401
    fakes.identity.grants[COMPANY, SUBJECT] = frozenset({Permission.INVOICE_READ})
    refused = await client.post("/v1/invoices", json=INVOICE, headers=headers(caller, "p-1"))
    assert (refused.status_code, document(refused)["code"]) == (403, "permission_denied")
    missing = await client.get(f"/v1/invoices/{new_id()}", headers=headers(caller))
    assert (missing.status_code, document(missing)["code"]) == (404, "invoice_not_found")
    stranger = Caller(caller.issuer, subject="2002")
    unknown = await client.get("/v1/invoices", headers=headers(stranger))
    assert (unknown.status_code, document(unknown)["code"]) == (404, "company_not_found")


async def test_a_fiscal_service_that_cannot_answer_makes_issuing_unavailable(
    client: httpx.AsyncClient, caller: Caller, fakes: Fakes
) -> None:
    fakes.fiscal.down = True
    response = await client.post("/v1/invoices", json=INVOICE, headers=headers(caller, "u-1"))
    assert response.status_code == 503
    drafted = await client.post(
        "/v1/invoices", json=INVOICE | {"draft": True}, headers=headers(caller, "u-2")
    )
    assert drafted.status_code == 201


async def test_invoicing_is_ready_once_its_schema_is_this_version_s(client: httpx.AsyncClient) -> None:
    response = await client.get("/readyz")
    assert response.status_code == 200
    assert document(response)["checks"] == {
        "database": True,
        "nats": True,
        "workers": True,
        "schema": True,
        "migrations": True,
    }
```

`services/invoicing/tests/test_openapi.py`:

```python
from pathlib import Path
from typing import Final, TypedDict

from pydantic import TypeAdapter
lazy import pytest

from ledger_invoicing import openapi
from ledger_invoicing.main import create_app
from ledger_invoicing.openapi import document
from ledger_invoicing.resources import VERSION

PUBLISHED: Final = Path(__file__).parents[1] / "openapi.json"
Parameter = TypedDict("Parameter", {"name": str, "in": str, "required": bool})


class Answer(TypedDict, total=False):
    content: dict[str, dict[str, object]]


class Operation(TypedDict, total=False):
    parameters: list[Parameter]
    security: list[dict[str, list[str]]]
    responses: dict[str, Answer]


class Document(TypedDict):
    openapi: str
    paths: dict[str, dict[str, Operation]]


DOCUMENT: Final = TypeAdapter(Document)


def test_the_published_document_is_the_one_the_code_makes() -> None:
    assert PUBLISHED.read_text(encoding="utf-8") == document(), "regenerate it: uv run poe openapi"


def test_the_document_is_openapi_3_1_and_every_post_takes_an_idempotency_key() -> None:
    parsed = DOCUMENT.validate_json(document())
    assert parsed["openapi"].startswith("3.1.")
    posts = [operations["post"] for operations in parsed["paths"].values() if "post" in operations]
    assert posts
    for post in posts:
        assert {"name": "Idempotency-Key", "in": "header", "required": True} in post.get("parameters", [])


def test_every_operation_is_for_bearers_of_a_token() -> None:
    parsed = DOCUMENT.validate_json(document())
    for operations in parsed["paths"].values():
        for operation in operations.values():
            assert operation.get("security") == [{"HTTPBearer": []}]


def test_every_error_an_operation_documents_is_a_problem_document() -> None:
    parsed = DOCUMENT.validate_json(document())
    errors = [
        answer
        for operations in parsed["paths"].values()
        for operation in operations.values()
        for status, answer in operation.get("responses", {}).items()
        if status.startswith(("4", "5"))
    ]
    assert errors
    for answer in errors:
        assert list(answer.get("content", {})) == ["application/problem+json"]


def test_the_command_line_writes_the_document(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "openapi.json"
    monkeypatch.setattr("sys.argv", ["ledger-invoicing-openapi", str(target)])
    assert openapi.main() == 0
    assert target.read_text(encoding="utf-8") == document()


def test_the_command_line_needs_a_path(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.argv", ["ledger-invoicing-openapi"])
    assert openapi.main() == 2
    assert capsys.readouterr().err == "usage: ledger-invoicing-openapi PATH\n"


def test_granians_factory_builds_the_app_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in {
        "LEDGER_DATABASE_URL": "postgresql+psycopg://invoicing:secret@localhost/invoicing",
        "LEDGER_TOKEN_ISSUER": "https://issuer.example",
        "LEDGER_TOKEN_AUDIENCE": "ledger",
        "LEDGER_TOKEN_JWKS_URL": "https://issuer.example/oauth/v2/keys",
        "LEDGER_IDENTITY_ADDRESS": "identity:50051",
        "LEDGER_FISCAL_RULES_ADDRESSES": '["fiscal-do:50051"]',
    }.items():
        monkeypatch.setenv(name, value)
    app = create_app()
    assert (app.title, app.version) == ("invoicing", VERSION)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest services/invoicing/tests/test_service.py services/invoicing/tests/test_openapi.py -q --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'ledger_invoicing.main'`.

- [ ] **Step 4: Implement**

`services/invoicing/src/ledger_invoicing/resources.py`:

```python
"""What invoicing talks to beside the chassis, opened when it starts and closed when it stops.

One object (the chassis's resources hook yields one): the identity provider's keys, a gRPC channel to identity
(the callers' permissions) and one to each fiscal service, opened in order and closed in reverse.
"""

from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from importlib.metadata import version
from typing import Final, final
lazy from collections.abc import AsyncGenerator

from ledger_contracts.fiscal import IDEMPOTENT_METHODS as FISCAL_READS
from ledger_invoicing.adapters.fiscal_rules import RemoteFiscalRules
from ledger_platform.authorization import CachedPermissions
from ledger_platform.grpc_client import Channel
from ledger_platform.identity_client import GET_PERMISSIONS, RemotePermissions
lazy from ledger_invoicing.settings import InvoicingSettings
lazy from ledger_platform.authentication import HttpKeys

VERSION: Final = version("ledger-invoicing")


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class InvoicingResources:
    """What the routes, handlers and checks of a running invoicing use (see the module)."""

    permissions: CachedPermissions
    rules: RemoteFiscalRules


@asynccontextmanager
async def opened(settings: InvoicingSettings, *, keys: HttpKeys) -> AsyncGenerator[InvoicingResources]:
    """Every resource of invoicing, open for the length of the block."""
    caller = settings.service_name
    identity = Channel(
        settings.identity_address, caller=caller, version=VERSION, idempotent=(GET_PERMISSIONS,)
    )
    async with AsyncExitStack() as stack:
        await stack.enter_async_context(keys.opened())
        await stack.enter_async_context(identity.opened())
        fiscal = [
            await stack.enter_async_context(
                Channel(address, caller=caller, version=VERSION, idempotent=FISCAL_READS).opened()
            )
            for address in settings.fiscal_rules_addresses
        ]
        yield InvoicingResources(
            permissions=CachedPermissions(RemotePermissions(identity.current())),
            rules=RemoteFiscalRules(fiscal, deadline=settings.fiscal_check_seconds),
        )
```

`services/invoicing/src/ledger_invoicing/api/__init__.py`:

```python
"""invoicing's interfaces: its public HTTP API and the events it consumes."""
```

`services/invoicing/src/ledger_invoicing/api/context.py`:

```python
"""What every route of invoicing shares: the runtime, the caller, its access to the company, idempotency.

The company a request acts in is named by the ``Ledger-Company-Id`` header (invoicing's paths carry no
company); the caller's permissions there come from identity (``RemotePermissions``, cached 30 seconds and
dropped on a membership event), so a caller without a role in the company gets 404 ``company_not_found``.
"""

from contextlib import contextmanager
from dataclasses import dataclass
from http import HTTPStatus
from typing import Annotated, ClassVar, Final, final
lazy from collections.abc import Callable, Coroutine, Generator
lazy from contextlib import AbstractAsyncContextManager

from fastapi import Depends
from pydantic import BaseModel, ConfigDict
lazy from fastapi import Request

from ledger_invoicing.adapters.postgres.units import bind
from ledger_invoicing.application.commands import StaleVersionError
from ledger_invoicing.resources import InvoicingResources
from ledger_platform.routes import attempt_of, authenticated, company_in_header, precondition_failed
from ledger_platform.web import PROBLEM_JSON
lazy from ledger_invoicing.application.ports import FiscalRules, InvoicingUnit
lazy from ledger_invoicing.settings import InvoicingSettings
lazy from ledger_platform.authentication import TokenVerifier
lazy from ledger_platform.authorization import CompanyAccess, PermissionSource
lazy from ledger_platform.database import UnitOfWork
lazy from ledger_platform.idempotency import Attempt, Idempotency
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
class Call:
    """One request in one company: the request, the caller's access there, and the running service."""

    request: Request
    access: CompanyAccess
    runtime: Runtime[InvoicingSettings]

    def transaction(self) -> AbstractAsyncContextManager[UnitOfWork]:
        """A transaction scoped to the request's company."""
        return self.runtime.database.transaction(self.access.tenant)

    def unit(self, transaction: UnitOfWork) -> InvoicingUnit:
        """Invoicing's unit of work over ``transaction``."""
        return bind(transaction, self.runtime.outbox)

    @property
    def rules(self) -> FiscalRules:
        """The fiscal services."""
        return self.runtime.resources(InvoicingResources).rules

    def attempt(self, key: str, body: BaseModel | None = None) -> Attempt:
        """The caller's use of ``key`` for this request, with ``body``."""
        return attempt_of(self.request, self.access.principal, key, body)


type CallDependency = Callable[
    [Request, CompanyAccess, Runtime[InvoicingSettings]], Coroutine[object, object, Call]
]


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class ApiContext:
    """The dependencies every router shares, built once with the app."""

    runtime: RuntimeDependency[InvoicingSettings]
    caller: Caller
    in_company: InCompany
    call: CallDependency
    idempotency: Idempotency

    @classmethod
    def build(
        cls,
        *,
        runtime: RuntimeDependency[InvoicingSettings],
        verifier: TokenVerifier,
        idempotency: Idempotency,
    ) -> ApiContext:
        """The context of invoicing's routes: the caller from ``verifier``, its access from identity."""
        caller = authenticated(verifier)

        async def permission_source(
            current: Annotated[Runtime[InvoicingSettings], Depends(runtime)],
        ) -> PermissionSource:
            return current.resources(InvoicingResources).permissions

        in_company = company_in_header(caller, permission_source)

        async def call(
            request: Request,
            access: Annotated[CompanyAccess, Depends(in_company)],
            current: Annotated[Runtime[InvoicingSettings], Depends(runtime)],
        ) -> Call:
            return Call(request=request, access=access, runtime=current)

        return cls(runtime=runtime, caller=caller, in_company=in_company, call=call, idempotency=idempotency)


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


@contextmanager
def precondition_checked() -> Generator[None]:
    """Inside the block, a change refused for ``If-Match`` (``StaleVersionError``) answers 412."""
    try:
        yield
    except StaleVersionError as error:
        raise precondition_failed() from error
```

`services/invoicing/src/ledger_invoicing/api/models.py`:

```python
"""The shapes of invoicing's public API, translated to and from the domain.

What clients send has its unknown fields refused. Amounts, quantities, prices and rates travel as decimal
strings (``"1180.00"``), never JSON numbers: a float cannot carry them exactly. A shape checks only what JSON
can say (types, lengths, the decimal form); every rule of invoicing's is the domain's
(``ledger_invoicing.domain.form``), answered with all its problems at once.
"""

from datetime import date, datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Annotated, ClassVar, Final, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ledger_invoicing.domain.content import (
    Address,
    Content,
    Line,
    Party,
    Payment,
    Reference,
    Tax,
    Terms,
    Withholding,
)
from ledger_invoicing.domain.ids import DocumentId, EmissionPointId
from ledger_invoicing.domain.vocabulary import (
    Correction,
    DocumentKind,
    DocumentState,
    FiscalStatus,
    ItemKind,
    PaymentMethod,
    PaymentTerms,
    TaxCategory,
)
from ledger_kernel.money import Currency, Money
lazy from ledger_invoicing.domain.document import Document
lazy from ledger_invoicing.domain.totals import Totals

DECIMAL: Final = r"^[0-9]{1,18}(\.[0-9]{1,12})?$"
"""A plain, non-negative decimal: no sign, exponent, spaces or separators (``"118.0000"``)."""
MAX_LINES: Final = 1000

type DecimalText = Annotated[str, Field(pattern=DECIMAL, max_length=32, examples=["118.00"])]
type Text = Annotated[str, Field(max_length=1000)]
type Short = Annotated[str, Field(max_length=200)]


def _decimal(text: str) -> Decimal:
    return Decimal(text)


def _money(text: str | None, currency: Currency) -> Money | None:
    return None if text is None else Money.of(text, currency)


def _text(value: Money | None) -> str | None:
    return None if value is None else format(value.amount, "f")


class _Shape(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)


class AddressShape(_Shape):
    """Where the customer is."""

    line: Text | None = None
    city_code: Short | None = None
    region_code: Short | None = None
    country_code: Short | None = Field(default=None, description="ISO 3166-1 alpha-2")

    def domain(self) -> Address:
        """The domain's value."""
        return Address(
            line=self.line,
            city_code=self.city_code,
            region_code=self.region_code,
            country_code=self.country_code,
        )

    @classmethod
    def of(cls, address: Address) -> Self:
        """The shape of ``address``."""
        return cls(
            line=address.line,
            city_code=address.city_code,
            region_code=address.region_code,
            country_code=address.country_code,
        )


class PartyShape(_Shape):
    """Who buys: each country's rules say which fields a kind of document needs."""

    tax_id: Short | None = None
    foreign_id: Short | None = None
    name: Short | None = None
    email: Short | None = None
    address: AddressShape | None = None

    def domain(self) -> Party:
        """The domain's value."""
        return Party(
            tax_id=self.tax_id,
            foreign_id=self.foreign_id,
            name=self.name,
            email=self.email,
            address=None if self.address is None else self.address.domain(),
        )

    @classmethod
    def of(cls, party: Party) -> Self:
        """The shape of ``party``."""
        return cls(
            tax_id=party.tax_id,
            foreign_id=party.foreign_id,
            name=party.name,
            email=party.email,
            address=None if party.address is None else AddressShape.of(party.address),
        )


class TaxShape(_Shape):
    """How a line is taxed: a category and its rate, a percentage."""

    category: TaxCategory
    rate: DecimalText


class WithholdingShape(_Shape):
    """What the customer withholds on the line."""

    value_added_tax: DecimalText | None = None
    income_tax: DecimalText | None = None


class LineShape(_Shape):
    """One line."""

    number: Annotated[int, Field(ge=1, le=MAX_LINES)]
    kind: ItemKind
    name: Short
    description: Text | None = None
    quantity: DecimalText
    unit_code: Short | None = None
    unit_price: DecimalText
    discount: DecimalText | None = None
    tax: TaxShape
    withholding: WithholdingShape | None = None

    def domain(self, currency: Currency) -> Line:
        """The domain's value, its amounts in ``currency``."""
        held = self.withholding
        return Line(
            number=self.number,
            kind=self.kind,
            name=self.name,
            description=self.description,
            quantity=_decimal(self.quantity),
            unit_code=self.unit_code,
            unit_price=_decimal(self.unit_price),
            discount=_money(self.discount, currency),
            tax=Tax(self.tax.category, _decimal(self.tax.rate)),
            withholding=None
            if held is None
            else Withholding(
                value_added_tax=_money(held.value_added_tax, currency),
                income_tax=_money(held.income_tax, currency),
            ),
        )


class TermsShape(_Shape):
    """When the customer pays."""

    kind: PaymentTerms
    due_date: date | None = None


class PaymentShape(_Shape):
    """One way (part of) the document is paid."""

    method: PaymentMethod
    amount: DecimalText


class ReferenceShape(_Shape):
    """The document of the company's a note refers to, and why."""

    document_id: UUID
    correction: Correction
    reason: Short


class InvoiceContent(_Shape):
    """What a document says: the body of a ``PUT``, and of a ``POST`` with ``draft``."""

    kind: DocumentKind
    emission_point_id: UUID
    issue_date: date
    currency: Currency
    prices_include_tax: bool = False
    customer: PartyShape | None = None
    lines: Annotated[list[LineShape], Field(max_length=MAX_LINES)]
    terms: TermsShape
    payments: Annotated[list[PaymentShape], Field(max_length=10)] = Field(default_factory=list[PaymentShape])
    reference: ReferenceShape | None = None
    fiscal_fields: Annotated[dict[Annotated[str, Field(max_length=40)], Short], Field(max_length=16)] = Field(
        default_factory=dict[str, str],
        description="What the company's country's fiscal service needs and invoicing passes on unread.",
    )
    external_reference: Annotated[str, Field(max_length=64)] | None = None

    def content(self) -> Content:
        """The domain's content (its rules are checked apart)."""
        currency = self.currency
        reference = self.reference
        return Content(
            kind=self.kind,
            emission_point=EmissionPointId(self.emission_point_id),
            issue_date=self.issue_date,
            currency=currency,
            prices_include_tax=self.prices_include_tax,
            customer=None if self.customer is None else self.customer.domain(),
            lines=tuple(line.domain(currency) for line in self.lines),
            terms=Terms(kind=self.terms.kind, due_date=self.terms.due_date),
            payments=tuple(
                Payment(payment.method, Money.of(payment.amount, currency)) for payment in self.payments
            ),
            reference=None
            if reference is None
            else Reference(
                document=DocumentId(reference.document_id),
                correction=reference.correction,
                reason=reference.reason,
            ),
            fiscal_fields=MappingProxyType(dict(self.fiscal_fields)),
            external_reference=self.external_reference,
        )


class NewInvoice(InvoiceContent):
    """The body of ``POST /v1/invoices``: a document, issued at once unless ``draft``."""

    draft: bool = False


class VoidRequest(_Shape):
    """Why a document is voided."""

    reason: Annotated[str, Field(min_length=1, max_length=200)]


class TaxGroupView(BaseModel):
    """The lines taxed in one category at one rate."""

    category: TaxCategory
    rate: str
    base: str
    tax: str


class TotalsView(BaseModel):
    """A document's amounts."""

    lines: str
    discounts: str
    groups: list[TaxGroupView]
    tax: str
    total: str
    withheld_value_added_tax: str
    withheld_income_tax: str

    @classmethod
    def of(cls, totals: Totals) -> Self:
        """The view of ``totals``."""
        return cls(
            lines=format(totals.lines.amount, "f"),
            discounts=format(totals.discounts.amount, "f"),
            groups=[
                TaxGroupView(
                    category=group.category,
                    rate=format(group.rate, "f"),
                    base=format(group.base.amount, "f"),
                    tax=format(group.tax.amount, "f"),
                )
                for group in totals.groups
            ],
            tax=format(totals.tax.amount, "f"),
            total=format(totals.total.amount, "f"),
            withheld_value_added_tax=format(totals.withheld_value_added_tax.amount, "f"),
            withheld_income_tax=format(totals.withheld_income_tax.amount, "f"),
        )


class LineView(BaseModel):
    """One line, with its amount."""

    number: int
    kind: ItemKind
    name: str
    description: str | None
    quantity: str
    unit_code: str | None
    unit_price: str
    discount: str | None
    tax: TaxShape
    withholding: WithholdingShape | None
    amount: str


class MessageView(BaseModel):
    """A tax authority's message, verbatim."""

    code: str
    text: str


class FiscalView(BaseModel):
    """What the document's fiscal service and tax authority said."""

    status: FiscalStatus
    number: str | None
    messages: list[MessageView]


class InvoiceView(BaseModel):
    """A document as the API shows it; its version is the ``ETag``."""

    id: UUID
    kind: DocumentKind
    state: DocumentState
    version: int
    emission_point_id: UUID
    issue_date: date
    currency: Currency
    prices_include_tax: bool
    customer: PartyShape | None
    lines: list[LineView]
    terms: TermsShape
    payments: list[PaymentShape]
    reference: ReferenceShape | None
    fiscal_fields: dict[str, str]
    external_reference: str | None
    totals: TotalsView
    fiscal: FiscalView
    void_reason: str | None
    created_at: datetime
    updated_at: datetime
    issued_at: datetime | None

    @classmethod
    def of(cls, document: Document) -> Self:
        """The view of ``document``."""
        content, totals = document.content, document.totals
        reference = content.reference
        return cls(
            id=document.id,
            kind=content.kind,
            state=document.state,
            version=document.version,
            emission_point_id=content.emission_point,
            issue_date=content.issue_date,
            currency=content.currency,
            prices_include_tax=content.prices_include_tax,
            customer=None if content.customer is None else PartyShape.of(content.customer),
            lines=[
                LineView(
                    number=line.number,
                    kind=line.kind,
                    name=line.name,
                    description=line.description,
                    quantity=format(line.quantity, "f"),
                    unit_code=line.unit_code,
                    unit_price=format(line.unit_price, "f"),
                    discount=_text(line.discount),
                    tax=TaxShape(category=line.tax.category, rate=format(line.tax.rate, "f")),
                    withholding=None
                    if line.withholding is None
                    else WithholdingShape(
                        value_added_tax=_text(line.withholding.value_added_tax),
                        income_tax=_text(line.withholding.income_tax),
                    ),
                    amount=format(amount.amount, "f"),
                )
                for line, amount in zip(content.lines, totals.line_amounts, strict=True)
            ],
            terms=TermsShape(kind=content.terms.kind, due_date=content.terms.due_date),
            payments=[
                PaymentShape(method=payment.method, amount=format(payment.amount.amount, "f"))
                for payment in content.payments
            ],
            reference=None
            if reference is None
            else ReferenceShape(
                document_id=reference.document, correction=reference.correction, reason=reference.reason
            ),
            fiscal_fields=dict(content.fiscal_fields),
            external_reference=content.external_reference,
            totals=TotalsView.of(totals),
            fiscal=FiscalView(
                status=document.fiscal.status,
                number=document.fiscal.number,
                messages=[MessageView(code=each.code, text=each.text) for each in document.fiscal.messages],
            ),
            void_reason=document.void_reason,
            created_at=document.created_at,
            updated_at=document.updated_at,
            issued_at=document.issued_at,
        )


class InvoicePage(BaseModel):
    """A page of documents, oldest first."""

    items: list[InvoiceView]
    next_cursor: str | None
```

`services/invoicing/src/ledger_invoicing/api/invoices.py`:

```python
"""``/v1/invoices``: invoices, credit notes and debit notes; create, list, read, rewrite a draft, issue, void.

``POST`` checks the document whole (invoicing's rules, then every fiscal service's), stores it and, unless it
is a draft, issues it: ``DocumentIssued`` leaves through the outbox in the same transaction, and the
company's fiscal service takes it from there (its answer comes back as ``fiscal``). Every ``POST`` is keyed by
``Idempotency-Key``: a retry answers the same document, never a second one. Changes of a stored document name
its version in ``If-Match`` (412 when it moved on, 428 without one).
"""

from http import HTTPStatus
from typing import Annotated, Final
lazy from uuid import UUID

from fastapi import APIRouter, Depends
lazy from fastapi import Response

from ledger_invoicing.api.context import precondition_checked, problems
from ledger_invoicing.api.models import InvoicePage, InvoiceView
from ledger_invoicing.application import commands, queries
from ledger_invoicing.domain.document import Document
from ledger_invoicing.domain.form import checked
from ledger_invoicing.domain.ids import DocumentId
from ledger_kernel.access import Permission
from ledger_kernel.clock import utc_now
from ledger_kernel.ids import new_id
from ledger_platform.routes import answer, etag, idempotency_key, if_match, json_response, page_request
lazy from ledger_invoicing.api.context import ApiContext, Call
lazy from ledger_invoicing.api.models import InvoiceContent, NewInvoice, VoidRequest
lazy from ledger_kernel.pagination import PageRequest
lazy from ledger_platform.idempotency import StoredResponse
lazy from ledger_platform.routes import IfMatch

PREFIX: Final = "/v1/invoices"
TAG: Final = "invoices"


def _stored(status: HTTPStatus, document: Document) -> StoredResponse:
    headers = {"ETag": etag(document.version)}
    if status is HTTPStatus.CREATED:
        headers["Location"] = f"/v1/invoices/{document.id}"
    return json_response(status, InvoiceView.of(document), headers)


def _reads(context: ApiContext) -> APIRouter:
    reads = APIRouter(prefix=PREFIX, tags=[TAG])
    call = context.call

    @reads.get("", response_model=InvoicePage, responses=problems(401, 403, 404, 422))
    async def list_invoices(
        current: Annotated[Call, Depends(call)], page: Annotated[PageRequest, Depends(page_request)]
    ) -> InvoicePage:
        """The company's documents, oldest first."""
        current.access.require(Permission.INVOICE_READ)
        async with current.transaction() as transaction:
            found = await queries.documents(current.unit(transaction), page)
        return InvoicePage(
            items=[InvoiceView.of(each) for each in found.items], next_cursor=found.next_cursor
        )

    @reads.get("/{invoice_id}", response_model=InvoiceView, responses=problems(401, 403, 404, 422))
    async def read_invoice(
        invoice_id: UUID, current: Annotated[Call, Depends(call)], response: Response
    ) -> InvoiceView:
        """A document, with its version as ``ETag``."""
        current.access.require(Permission.INVOICE_READ)
        async with current.transaction() as transaction:
            found = await queries.document(current.unit(transaction), DocumentId(invoice_id))
        response.headers["ETag"] = etag(found.version)
        return InvoiceView.of(found)

    return reads


def _writes(context: ApiContext) -> APIRouter:
    writes = APIRouter(prefix=PREFIX, tags=[TAG])
    call, idempotency = context.call, context.idempotency

    @writes.post(
        "",
        status_code=HTTPStatus.CREATED,
        response_model=InvoiceView,
        responses=problems(401, 403, 404, 409, 422, 503),
    )
    async def create_invoice(
        body: NewInvoice,
        current: Annotated[Call, Depends(call)],
        key: Annotated[str, Depends(idempotency_key)],
    ) -> Response:
        """Create a document: a draft, or issued at once (checked by every fiscal service first)."""
        current.access.require(Permission.INVOICE_ISSUE)
        content = body.content()
        draft = Document.drafted(
            document=DocumentId(new_id()),
            tenant=current.access.tenant,
            content=content,
            totals=checked(content),
            now=utc_now(),
        )
        judged = None if body.draft else await commands.verdict(current.rules, draft)
        async with current.transaction() as transaction:

            async def created() -> StoredResponse:
                return _stored(
                    HTTPStatus.CREATED, await commands.create(current.unit(transaction), draft, judged)
                )

            outcome = await idempotency.once(transaction, current.attempt(key, body), created)
        return answer(outcome)

    @writes.put(
        "/{invoice_id}", response_model=InvoiceView, responses=problems(401, 403, 404, 409, 412, 422, 428)
    )
    async def rewrite_invoice(
        invoice_id: UUID,
        body: InvoiceContent,
        current: Annotated[Call, Depends(call)],
        match: Annotated[IfMatch, Depends(if_match)],
        response: Response,
    ) -> InvoiceView:
        """Replace a draft's content, at the version ``If-Match`` names."""
        current.access.require(Permission.INVOICE_ISSUE)
        content = body.content()
        totals = checked(content)
        with precondition_checked():
            async with current.transaction() as transaction:
                rewritten = await commands.rewrite(
                    current.unit(transaction), DocumentId(invoice_id), content, totals, admits=match.admits
                )
        response.headers["ETag"] = etag(rewritten.version)
        return InvoiceView.of(rewritten)

    return writes


def _moves(context: ApiContext) -> APIRouter:
    moves = APIRouter(prefix=PREFIX, tags=[TAG])
    call, idempotency = context.call, context.idempotency

    @moves.post(
        "/{invoice_id}/issue",
        response_model=InvoiceView,
        responses=problems(401, 403, 404, 409, 412, 422, 428, 503),
    )
    async def issue_invoice(
        invoice_id: UUID,
        current: Annotated[Call, Depends(call)],
        match: Annotated[IfMatch, Depends(if_match)],
        key: Annotated[str, Depends(idempotency_key)],
    ) -> Response:
        """Issue a draft, at the version ``If-Match`` names (checked by every fiscal service first)."""
        current.access.require(Permission.INVOICE_ISSUE)
        async with current.transaction() as transaction:
            draft = await queries.document(current.unit(transaction), DocumentId(invoice_id))
        judged = await commands.verdict(current.rules, draft)
        with precondition_checked():
            async with current.transaction() as transaction:

                async def issued() -> StoredResponse:
                    document = await commands.issue(
                        current.unit(transaction), draft, judged, admits=match.admits
                    )
                    return _stored(HTTPStatus.OK, document)

                outcome = await idempotency.once(transaction, current.attempt(key), issued)
        return answer(outcome)

    @moves.post(
        "/{invoice_id}/void",
        response_model=InvoiceView,
        responses=problems(401, 403, 404, 409, 412, 422, 428),
    )
    async def void_invoice(
        invoice_id: UUID,
        body: VoidRequest,
        current: Annotated[Call, Depends(call)],
        match: Annotated[IfMatch, Depends(if_match)],
        key: Annotated[str, Depends(idempotency_key)],
    ) -> Response:
        """Void a draft, a document its tax authority rejected, or one no fiscal service serves."""
        current.access.require(Permission.INVOICE_ISSUE)
        with precondition_checked():
            async with current.transaction() as transaction:

                async def voided() -> StoredResponse:
                    unit = current.unit(transaction)
                    document = await commands.void(
                        unit, DocumentId(invoice_id), body.reason, admits=match.admits
                    )
                    return _stored(HTTPStatus.OK, document)

                outcome = await idempotency.once(transaction, current.attempt(key, body), voided)
        return answer(outcome)

    return moves


def routers(context: ApiContext) -> tuple[APIRouter, ...]:
    """The documents' routes, under ``/v1/invoices``: reading, writing, and moving a document on."""
    return (_reads(context), _writes(context), _moves(context))
```

`services/invoicing/src/ledger_invoicing/api/consumers.py`:

```python
"""The events invoicing consumes: what the fiscal services say of its documents.

One durable consumer, ``invoicing-fiscal-outcomes``, reads ``fiscal.document.>`` of fiscal-do's stream and
mirrors each event about one of invoicing's documents onto it (``ledger_invoicing.application.outcomes``),
announcing the change for webhooks in the same transaction as its inbox claim. A future country's service
adds a consumer of its own stream here; nothing else changes.
"""

from typing import Final
lazy from collections.abc import Sequence

from ledger_contracts import fiscal_do
from ledger_invoicing.adapters.postgres.units import bind
from ledger_invoicing.adapters.wire import outcome_of
from ledger_invoicing.application import outcomes
from ledger_platform.consumer import ConsumerSpec
lazy from ledger_contracts.envelope import Envelope
lazy from ledger_invoicing.settings import InvoicingSettings
lazy from ledger_platform.database import UnitOfWork
lazy from ledger_platform.service import Runtime

FISCAL_OUTCOMES: Final = "invoicing-fiscal-outcomes"
FISCAL_DOCUMENTS: Final = "fiscal.document.>"


def consumers(runtime: Runtime[InvoicingSettings]) -> Sequence[ConsumerSpec]:
    """Invoicing's durable consumers, over the running service."""

    async def fiscal_outcome(envelope: Envelope, unit: UnitOfWork) -> None:
        outcome = outcome_of(envelope)
        if outcome is not None:
            await outcomes.mirror(bind(unit, runtime.outbox), outcome)

    return (
        ConsumerSpec(
            name=FISCAL_OUTCOMES, stream=fiscal_do.STREAM, subject=FISCAL_DOCUMENTS, handler=fiscal_outcome
        ),
    )
```

`services/invoicing/src/ledger_invoicing/main.py`:

```python
"""invoicing's composition root: settings, adapters and interfaces wired into one chassis ``Service``."""

lazy from collections.abc import Sequence
lazy from contextlib import AbstractAsyncContextManager

lazy from fastapi import APIRouter, FastAPI

from ledger_invoicing.adapters.events import INVOICING
from ledger_invoicing.adapters.postgres.migrate import head, require_head
from ledger_invoicing.api import invoices
from ledger_invoicing.api.consumers import consumers
from ledger_invoicing.api.context import ApiContext
from ledger_invoicing.resources import VERSION, opened
from ledger_invoicing.settings import InvoicingSettings
from ledger_platform.authentication import HttpKeys, PublishedKeys, TokenPolicy, TokenVerifier
from ledger_platform.idempotency import Idempotency
from ledger_platform.service import Events, Interfaces, Service
lazy from ledger_invoicing.resources import InvoicingResources
lazy from ledger_platform.service import Runtime, RuntimeDependency


def build(settings: InvoicingSettings) -> Service[InvoicingSettings]:
    """The invoicing service ``settings`` describe: built, not started (its app's lifespan starts it).

    Its readiness check beside the chassis's: ``migrations`` (the database is at this version's revision).
    """
    policy = TokenPolicy.of(settings)
    keys = HttpKeys()
    verifier = TokenVerifier(policy, keys=PublishedKeys(policy, fetch=keys))
    idempotency = Idempotency()
    expected = head()

    def http(runtime: RuntimeDependency[InvoicingSettings]) -> Sequence[APIRouter]:
        return invoices.routers(ApiContext.build(runtime=runtime, verifier=verifier, idempotency=idempotency))

    def resources(current: InvoicingSettings) -> AbstractAsyncContextManager[InvoicingResources]:
        return opened(current, keys=keys)

    async def migrations(runtime: Runtime[InvoicingSettings]) -> None:
        await require_head(runtime.database, expected)

    return Service(
        settings=settings,
        version=VERSION,
        interfaces=Interfaces(http=http, resources=resources, checks={"migrations": migrations}),
        events=Events(streams=(INVOICING,), consumers=consumers),
    )


def create_app() -> FastAPI:
    """Granian's factory (``--factory``): the app of the service the ``LEDGER_*`` environment configures."""
    return build(InvoicingSettings()).app
```

`services/invoicing/src/ledger_invoicing/openapi.py`:

```python
"""invoicing's OpenAPI document (3.1), as published in ``services/invoicing/openapi.json`` (§10).

The document is made from the code: the service is built with placeholder settings (nothing connects) and its
app asked for its schema. A test keeps the published file equal to it.
"""

import json
import sys
from pathlib import Path
from typing import Final

from pydantic import SecretStr

from ledger_invoicing.main import build
from ledger_invoicing.settings import InvoicingSettings

_USAGE: Final = "usage: ledger-invoicing-openapi PATH\n"
_WRONG_USAGE: Final = 2
_DATABASE: Final = "postgresql+psycopg://openapi@localhost/openapi"
_ISSUER: Final = "https://issuer.invalid"
"""Placeholders: the document does not depend on whom the service trusts, and nothing is fetched."""
_AUDIENCE: Final = "ledger"
_KEYS: Final = "https://issuer.invalid/oauth/v2/keys"
_ADDRESS: Final = "localhost:50051"


def document() -> str:
    """The OpenAPI document of invoicing's public API, as JSON with sorted keys."""
    settings = InvoicingSettings(
        database_url=SecretStr(_DATABASE),
        token_issuer=_ISSUER,
        token_audience=_AUDIENCE,
        token_jwks_url=_KEYS,
        identity_address=_ADDRESS,
        fiscal_rules_addresses=(),
    )
    return json.dumps(build(settings).app.openapi(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> int:
    """``ledger-invoicing-openapi PATH``: write the document to ``PATH``."""
    match sys.argv[1:]:
        case [path]:
            Path(path).write_text(document(), encoding="utf-8")
            return 0
        case _:
            sys.stderr.write(_USAGE)
            return _WRONG_USAGE
```

Run: `uv run poe openapi`
Expected: `services/invoicing/openapi.json` written (3.1, every `POST` with a required `Idempotency-Key`, every error a problem document); commit it.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest services/invoicing -q --no-cov`
Expected: PASS in about 20 seconds (PostgreSQL and NATS start once): an invoice is checked, issued and announced whole; a retry answers the same document and another body is refused; a fiscal objection is answered at once and nothing is kept; drafts are rewritten and issued at their version; fiscal-do's answer is mirrored and announced; credit notes of a settled invoice; who may do what; a fiscal service that cannot answer is a 503; `/readyz` lists `migrations`.

- [ ] **Step 6: Gate**

Run: `uv run poe fmt && uv run poe check`
Expected: every step passes, coverage 100 %.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock services/invoicing
git commit -m "feat(invoicing): the /v1/invoices API, the fiscal outcomes consumer, the service

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: fiscal-do on the event path: the anti-corruption layer, `FiscalRules` over gRPC, adopting what invoicing issued

**Files:**
- Create: `services/fiscal-do/src/ledger_fiscal_do/application/commercial.py`, `services/fiscal-do/src/ledger_fiscal_do/adapters/commercial.py`, `services/fiscal-do/src/ledger_fiscal_do/api/rules.py`
- Modify: `services/fiscal-do/src/ledger_fiscal_do/domain/problems.py`, `services/fiscal-do/src/ledger_fiscal_do/domain/events.py`, `services/fiscal-do/src/ledger_fiscal_do/application/issuing.py`, `services/fiscal-do/src/ledger_fiscal_do/adapters/events.py`, `services/fiscal-do/src/ledger_fiscal_do/api/consumers.py`, `services/fiscal-do/src/ledger_fiscal_do/main.py`, `services/fiscal-do/pyproject.toml`
- Test: `services/fiscal-do/tests/test_commercial.py` (new), `services/fiscal-do/tests/test_service.py` (modified)

**Interfaces:**
- Consumes: Milestone 4's `issue`, `IssueRequest`, `checked`, `FiscalUnit`, `Units`, `CompanyFacts`, `FiscalXml`, `DgiiXml`, `PgUnits`, `bind`, `FiscalResources.companies`, `outgoing`, `ConsumerSpec`, the test support `samples` (`TENANT`, `POINT`, `NOW`, `expiry`) and `fakes` (`State`, `FakeUnits`, `Clock`); Task 2's `ledger.invoicing.v1`, `ledger.fiscal.v1`, `ledger.events.invoicing.v1`, `DocumentDeclined`, `ledger_contracts.invoicing`.
- Produces: `ProblemCode` gains `NOT_COMMERCIAL`, `TAX_RATE_NOT_IN_FORCE`, `FIELD_UNKNOWN`, `REFERENCE_NOT_FISCAL`, `NO_SEQUENCE`, `TOTALS_DISAGREE`. `domain.events.DocumentDeclined(tenant, document, messages, version=1)`, in `FiscalEvent`; `adapters.events.outgoing` publishes it on `fiscal.document.declined`. `IssueRequest.document: DocumentId | None`. `application.commercial`: `DECLINING`, `Referred`, `Commercial`, `Untranslatable`, `Translated`, `FiscalCheck`, `NOT_OURS`, `check(unit, translated)`, `adopt(unit, translated, *, company, xml)`. `adapters.commercial`: `COMMERCIAL_TYPES`, `FIELDS`, `tenant_of(document)`, `translate(document) -> Translated`. `api.rules.FiscalRules(units)`: the `FiscalRulesService` servicer. `api.consumers`: `COMMERCIAL` (`fiscal-do-commercial-documents`, stream `INVOICING`, subject `invoicing.document.issued`), `MismatchedTenantError`. `main.build` serves `FiscalRules` on the chassis's gRPC port.

Re-check before starting: fiscal-do as Milestone 4 landed it — `IssueRequest`, `issue()` (it makes `DocumentId(new_id())` where this task reads `request.document or ...`), `Documents.get`, `Sequences.of_type`, `EncfSequence.emission_point` and `usable_on`, `Content`/`Line`/`Buyer`/`Withholding`/`Reference` field names, `ProblemCode`, `DocumentInvalidError`, `checked`, `local_date`, `DgiiMessage`, `FiscalResources.companies`, `consumers()` and `_tenant`, `build()`'s `Interfaces(...)`, and `test_service.py`'s fixtures (`service`, `infrastructure`, `running`, `prepared`, `headers`, `BODY`, `COMPANY`, `POINT`). This task's new modules were run in the scratch workspace against Milestone 4's `domain` and `application` as its plan writes them (with this task's changes applied); the changed `consumers.py`, `main.py` and `test_service.py` were not (they need fiscal-do whole): run them first, and where a landed name differs, use it and say so in the report.

- [ ] **Step 1: Dependencies**

`services/fiscal-do/pyproject.toml` — `dependencies` gains `"grpcio>=1.84.0"` (`api.rules` and `main` name `grpc.aio`'s types). Run: `uv lock && uv sync`

- [ ] **Step 2: Write the failing tests**

`services/fiscal-do/tests/test_commercial.py`:

```python
from typing import Final, final, override
lazy from collections.abc import Callable
lazy from datetime import datetime

import grpc
import pytest
from prometheus_client import CollectorRegistry

import ledger.events.fiscal_do.v1.events_pb2 as fiscal_events
import ledger.invoicing.v1.document_pb2 as wire
from ledger.common.v1.money_pb2 import Money as WireMoney
from ledger.fiscal.v1.fiscal_rules_pb2 import CheckDocumentRequest
from ledger.fiscal.v1.fiscal_rules_pb2_grpc import (
    FiscalRulesServiceStub,
    add_FiscalRulesServiceServicer_to_server,
)
from ledger_fiscal_do.adapters.commercial import translate
from ledger_fiscal_do.adapters.events import outgoing
from ledger_fiscal_do.api.rules import FiscalRules
from ledger_fiscal_do.application import ranges
from ledger_fiscal_do.application.commercial import NOT_OURS, Commercial, Untranslatable, adopt, check
from ledger_fiscal_do.application.ports import CompanyFacts, FiscalXml
from ledger_fiscal_do.domain.events import DocumentDeclined, DocumentNumbered
from ledger_fiscal_do.domain.ids import DocumentId
from ledger_fiscal_do.domain.problems import ProblemCode
from ledger_fiscal_do.domain.profile import FiscalProfile
from ledger_fiscal_do.domain.sequence import NumberRange
from ledger_fiscal_do.domain.verdict import DgiiMessage
from ledger_fiscal_do.domain.vocabulary import (
    Billing,
    DgiiEnvironment,
    EcfType,
    IncomeType,
    Modification,
    PaymentMethod,
)
from ledger_kernel.countries.do import TaxId
from ledger_kernel.errors import UnavailableError
from ledger_kernel.ids import InvalidIdError, new_id
from ledger_platform.grpc_server import serve_grpc
lazy from ledger_fiscal_do.application.commercial import Translated
lazy from ledger_fiscal_do.domain.annulment import Annulment
lazy from ledger_fiscal_do.domain.document import FiscalDocument

from fakes import Clock, FakeUnits, State
from samples import NOW, POINT, TENANT, expiry

COMPANY: Final = CompanyFacts(
    tax_id=TaxId.parse("101850043"), legal_name="FERRETERIA DUARTE SRL", active=True
)
PROFILE: Final = FiscalProfile(
    tenant=TENANT, environment=DgiiEnvironment.PRECERTIFICATION, address="Av. Duarte 123", updated_at=NOW
)
HUNDRED: Final = NumberRange(1, 100)

type Change = Callable[[wire.CommercialDocument], object]


@final
class AcceptingXml(FiscalXml):
    """DGII's XML as far as numbering needs it: every document well formed (``test_xml`` proves the rest)."""

    def __init__(self, refusal: Exception | None = None) -> None:
        self.refusal = refusal

    @override
    def ecf(self, document: FiscalDocument, *, signed_at: datetime) -> bytes:
        if self.refusal is not None:
            raise self.refusal
        return b"<ECF/>"

    @override
    def summary(self, document: FiscalDocument, *, security_code: str) -> bytes:
        raise AssertionError

    @override
    def annulment(self, annulment: Annulment, *, at: datetime) -> bytes:
        raise AssertionError

    @override
    def signed_ecf(self, document: FiscalDocument, signed: bytes) -> str:
        raise AssertionError

    @override
    def signed_summary(self, signed: bytes) -> None:
        raise AssertionError

    @override
    def signed_annulment(self, signed: bytes) -> None:
        raise AssertionError


def money(amount: str, currency: str = "DOP") -> WireMoney:
    return WireMoney(amount=amount, currency=currency)


def taxed(
    category: wire.TaxCategory.ValueType, rate: str, *, quantity: str = "1", price: str = "1"
) -> wire.Line:
    return wire.Line(
        number=1,
        kind=wire.ITEM_KIND_GOOD,
        name="x",
        quantity=quantity,
        unit_price=price,
        tax=wire.Tax(category=category, rate=rate),
    )


def commercial(*changes: Change) -> wire.CommercialDocument:
    """A consumer invoice as invoicing writes it (2 x 500.00 at 18 %, 250.00 exempt: 1,430.00), changed."""
    document = wire.CommercialDocument(
        document_id=str(new_id()),
        organization_id=str(TENANT.organization),
        company_id=str(TENANT.company),
        kind=wire.DOCUMENT_KIND_INVOICE,
        version=2,
        emission_point_id=str(POINT),
        issue_date=NOW.date().isoformat(),
        currency="DOP",
        lines=[
            taxed(wire.TAX_CATEGORY_STANDARD, "18", quantity="2", price="500.00"),
            wire.Line(
                number=2,
                kind=wire.ITEM_KIND_SERVICE,
                name="Item 2",
                quantity="1",
                unit_price="250.00",
                unit_code="43",
                tax=wire.Tax(category=wire.TAX_CATEGORY_EXEMPT, rate="0"),
            ),
        ],
        terms=wire.Terms(kind=wire.PAYMENT_TERMS_KIND_CASH),
        payments=[wire.Payment(method=wire.PAYMENT_METHOD_CARD, amount=money("1430.00"))],
        totals=wire.Totals(total=money("1430.00")),
        fiscal_fields={"income_type": "01"},
    )
    for change in changes:
        change(document)
    return document


def attribute(name: str, value: str | int) -> Change:
    return lambda document: setattr(document, name, value)


def customer(party: wire.Party) -> Change:
    return lambda document: document.customer.CopyFrom(party)


def terms(chosen: wire.Terms) -> Change:
    return lambda document: document.terms.CopyFrom(chosen)


def fields(**values: str) -> Change:
    def change(document: wire.CommercialDocument) -> None:
        document.fiscal_fields.clear()
        document.fiscal_fields.update(values)

    return change


def lines(*replacement: wire.Line) -> Change:
    def change(document: wire.CommercialDocument) -> None:
        del document.lines[:]
        document.lines.extend(replacement)
        del document.payments[:]

    return change


def total(amount: str, currency: str = "DOP") -> Change:
    return lambda document: document.totals.CopyFrom(wire.Totals(total=money(amount, currency)))


def note(referred: str, correction: wire.Correction.ValueType = wire.CORRECTION_AMOUNTS) -> Change:
    def change(document: wire.CommercialDocument) -> None:
        document.kind = wire.DOCUMENT_KIND_CREDIT_NOTE
        document.fiscal_fields.clear()
        del document.payments[:]
        document.reference.CopyFrom(
            wire.Reference(document_id=referred, correction=correction, reason="Price")
        )

    return change


@final
class World:
    """fiscal-do's state for one company: its profile, and its ranges at ``POINT``."""

    def __init__(self, *, profile: bool = True) -> None:
        self.state = State()
        self.units = FakeUnits(self.state, Clock(NOW))
        if profile:
            self.state.profiles[TENANT] = PROFILE

    async def register(self, ecf_type: EcfType = EcfType.CONSUMER_INVOICE) -> None:
        request = ranges.RangeRequest(
            ecf_type=ecf_type, emission_point=POINT, numbers=HUNDRED, expires_on=expiry(ecf_type)
        )
        await ranges.register(self.units.bound(TENANT), request)


def codes(found: Translated) -> set[tuple[str, str]]:
    match found:
        case Untranslatable(problems=problems):
            return {(each.field, each.code.value) for each in problems}
        case _:
            return set()


def translated(document: wire.CommercialDocument) -> Commercial:
    read = translate(document)
    assert isinstance(read, Commercial), codes(read)
    return read


# ── The translation ─────────────────────────────────────────────────────────────


def test_a_consumer_invoice_reads_as_a_type_32_in_dgii_s_words() -> None:
    read = translated(commercial())
    content = read.content
    assert (content.ecf_type, content.income, read.emission_point, format(read.total.amount, "f")) == (
        EcfType.CONSUMER_INVOICE,
        IncomeType.OPERATIONS,
        POINT,
        "1430.00",
    )
    assert [(line.billing, line.unit) for line in content.lines] == [
        (Billing.ITBIS_1, None),
        (Billing.EXEMPT, 43),
    ]
    assert [payment.method for payment in content.payments] == [PaymentMethod.CARD]
    assert (content.buyer, read.referred) == (None, None)


def test_the_type_follows_the_customer_the_kind_and_the_fields() -> None:
    party = wire.Party(tax_id="101850043", name="Distribuidora", address=wire.Address(line="Av. Duarte 7"))
    with_tax_id = translated(commercial(customer(party)))
    assert with_tax_id.content.ecf_type is EcfType.CREDIT_INVOICE
    assert with_tax_id.content.buyer is not None
    assert with_tax_id.content.buyer.address == "Av. Duarte 7"
    assert translated(commercial(fields(ecf_type="45"))).content.ecf_type is EcfType.GOVERNMENT
    named = translated(commercial(customer(wire.Party(name="Juan")))).content
    assert (named.ecf_type, named.buyer.name if named.buyer else None) == (EcfType.CONSUMER_INVOICE, "Juan")
    referred = DocumentId(new_id())
    credit = translated(commercial(note(str(referred))))
    assert (credit.content.ecf_type, credit.content.income) == (EcfType.CREDIT_NOTE, None)
    assert credit.referred is not None
    assert (credit.referred.document, credit.referred.code) == (referred, Modification.CORRECTS_AMOUNTS)


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (fields(ecf_type="41"), ("fiscal_fields.ecf_type", "not_commercial")),
        (fields(ecf_type="x"), ("fiscal_fields.ecf_type", "code_malformed")),
        (fields(ecf_type="33"), ("fiscal_fields.ecf_type", "not_allowed")),
        (fields(income_type="99"), ("fiscal_fields.income_type", "code_malformed")),
        (fields(color="red"), ("fiscal_fields", "field_unknown")),
        (attribute("currency", "USD"), ("currency", "wrong_currency")),
        (attribute("issue_date", "12/10/2026"), ("issue_date", "code_malformed")),
        (
            terms(wire.Terms(kind=wire.PAYMENT_TERMS_KIND_CREDIT, due_date="soon")),
            ("terms.due_date", "code_malformed"),
        ),
        (
            customer(wire.Party(tax_id="12")),
            ("customer.tax_id", "code_malformed"),
        ),
        (
            attribute("emission_point_id", "x"),
            ("emission_point_id", "code_malformed"),
        ),
        (total("1", "XYZ"), ("totals.total", "code_malformed")),
        (lines(taxed(wire.TAX_CATEGORY_STANDARD, "20")), ("lines[0].tax", "tax_rate_not_in_force")),
        (
            lines(taxed(wire.TAX_CATEGORY_EXEMPT, "0", quantity="NaN")),
            ("lines[0].quantity", "code_malformed"),
        ),
        (lines(taxed(wire.TAX_CATEGORY_EXEMPT, "0", price="one")), ("lines[0].unit_price", "code_malformed")),
        (note("x"), ("reference.document_id", "code_malformed")),
        (note(str(new_id()), wire.CORRECTION_UNSPECIFIED), ("reference.correction", "code_malformed")),
    ],
    ids=[
        "purchase-type",
        "type-not-a-number",
        "note-type-on-an-invoice",
        "income",
        "unknown-field",
        "dollars",
        "date",
        "due-date",
        "tax-id",
        "emission-point",
        "total",
        "rate",
        "quantity",
        "price",
        "reference-id",
        "correction",
    ],
)
def test_what_does_not_translate_is_a_problem_of_its_field(change: Change, expected: tuple[str, str]) -> None:
    assert expected in codes(translate(commercial(change)))


def test_a_note_s_type_is_its_kind_s() -> None:
    debit = commercial(note(str(new_id())), attribute("kind", wire.DOCUMENT_KIND_DEBIT_NOTE))
    assert translated(debit).content.ecf_type is EcfType.DEBIT_NOTE
    wrong = commercial(note(str(new_id())), fields(ecf_type="33"))
    assert ("fiscal_fields.ecf_type", "not_allowed") in codes(translate(wrong))


def test_withholdings_and_discounts_cross_into_dgii_s_fields() -> None:
    held = taxed(wire.TAX_CATEGORY_STANDARD, "18", price="1000.00")
    held.discount.CopyFrom(money("100.00"))
    held.withholding.CopyFrom(wire.Withholding(value_added_tax=money("162.00"), income_tax=money("90.00")))
    (line,) = translated(commercial(lines(held))).content.lines
    assert line.discount is not None
    assert line.withholding is not None
    assert line.withholding.itbis is not None
    assert line.withholding.isr is not None
    assert [
        format(each.amount, "f") for each in (line.discount, line.withholding.itbis, line.withholding.isr)
    ] == [
        "100.00",
        "162.00",
        "90.00",
    ]


def test_a_document_of_no_company_is_refused_whole() -> None:
    with pytest.raises(InvalidIdError):
        translate(commercial(attribute("company_id", "nobody")))


# ── Checking before invoicing issues ────────────────────────────────────────────


async def test_a_company_without_a_fiscal_profile_is_not_fiscal_do_s() -> None:
    assert await check(World(profile=False).units.bound(TENANT), translate(commercial())) == NOT_OURS


async def test_a_document_that_would_be_numbered_has_no_problem_and_takes_nothing() -> None:
    world = World()
    await world.register()
    found = await check(world.units.bound(TENANT), translate(commercial()))
    assert (found.applies, found.problems, found.omitted) == (True, (), 0)
    assert (world.state.events, world.state.documents) == ([], {})


async def test_what_numbering_would_refuse_is_answered_beforehand() -> None:
    world = World()
    unit = world.units.bound(TENANT)
    no_range = await check(unit, translate(commercial()))
    assert [(each.field, each.code) for each in no_range.problems] == [
        ("emission_point_id", ProblemCode.NO_SEQUENCE)
    ]
    await world.register()
    disagreeing = await check(unit, translate(commercial(total("1430.01"))))
    assert [each.code for each in disagreeing.problems] == [ProblemCode.TOTALS_DISAGREE]
    untranslatable = await check(unit, translate(commercial(attribute("currency", "USD"))))
    assert [each.code for each in untranslatable.problems] == [ProblemCode.WRONG_CURRENCY]
    empty = await check(unit, translate(commercial(lines(), total("0"))))
    assert ProblemCode.LINES_EMPTY in {each.code for each in empty.problems}


async def test_a_note_refers_to_a_document_fiscal_do_numbered() -> None:
    world = World()
    await world.register(EcfType.CREDIT_NOTE)
    found = await check(world.units.bound(TENANT), translate(commercial(note(str(new_id())))))
    assert ("reference.document_id", ProblemCode.REFERENCE_NOT_FISCAL) in [
        (each.field, each.code) for each in found.problems
    ]


# ── Adopting what invoicing issued ──────────────────────────────────────────────


async def test_an_issued_document_is_numbered_once_under_invoicing_s_id() -> None:
    world = World()
    await world.register()
    issued = translated(commercial())
    document = await adopt(world.units.bound(TENANT), issued, company=COMPANY, xml=AcceptingXml())
    assert document is not None
    assert (document.id, str(document.encf)) == (issued.document, "E320000000001")
    assert await adopt(world.units.bound(TENANT), issued, company=COMPANY, xml=AcceptingXml()) == document
    assert [type(event) for event in world.state.events] == [DocumentNumbered]


async def test_a_note_is_numbered_with_the_e_ncf_of_the_document_it_refers_to() -> None:
    world = World()
    await world.register()
    await world.register(EcfType.CREDIT_NOTE)
    invoice = translated(commercial())
    original = await adopt(world.units.bound(TENANT), invoice, company=COMPANY, xml=AcceptingXml())
    assert original is not None
    refund = commercial(
        note(str(invoice.document)),
        lines(taxed(wire.TAX_CATEGORY_STANDARD, "18", price="100.00")),
        total("118.00"),
    )
    credited = await adopt(world.units.bound(TENANT), translated(refund), company=COMPANY, xml=AcceptingXml())
    assert credited is not None
    assert credited.content.reference is not None
    assert (credited.content.reference.modified, credited.content.reference.issued_on) == (
        str(original.encf),
        original.content.issued_on,
    )


async def test_what_cannot_be_numbered_after_all_is_declined_and_takes_nothing() -> None:
    world = World()
    unit = world.units.bound(TENANT)
    nothing_left = translate(commercial())
    untranslatable = translate(commercial(attribute("currency", "USD")))
    orphan = translate(commercial(note(str(new_id()))))
    for each in (nothing_left, untranslatable, orphan):
        assert await adopt(unit, each, company=COMPANY, xml=AcceptingXml()) is None
    declined = [event for event in world.state.events if isinstance(event, DocumentDeclined)]
    assert [(event.document, event.messages[0].code, event.version) for event in declined] == [
        (nothing_left.document, "no_sequence", 1),
        (untranslatable.document, "ecf_invalid", 1),
        (orphan.document, "ecf_invalid", 1),
    ]
    assert world.state.documents == {}


async def test_a_failing_dependency_is_retried_not_declined_and_a_stranger_s_document_ignored() -> None:
    world = World()
    await world.register()
    issued = translate(commercial())
    with pytest.raises(UnavailableError):
        await adopt(world.units.bound(TENANT), issued, company=COMPANY, xml=AcceptingXml(UnavailableError()))
    assert world.state.events == []
    stranger = World(profile=False)
    assert await adopt(stranger.units.bound(TENANT), issued, company=COMPANY, xml=AcceptingXml()) is None


# ── Over gRPC ───────────────────────────────────────────────────────────────────


async def test_invoicing_asks_over_grpc_and_is_answered_in_stable_codes() -> None:
    world = World()

    def register(server: grpc.aio.Server) -> None:
        add_FiscalRulesServiceServicer_to_server(FiscalRules(world.units), server)

    async with (
        serve_grpc(port=0, register=register, registry=CollectorRegistry()) as port,
        grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel,
    ):
        stub = FiscalRulesServiceStub(channel)
        answer = await stub.CheckDocument(CheckDocumentRequest(document=commercial()), timeout=5)
        await world.register()
        clean = await stub.CheckDocument(CheckDocumentRequest(document=commercial()), timeout=5)
        nobody = commercial(attribute("company_id", "x"))
        with pytest.raises(grpc.aio.AioRpcError) as caught:
            await stub.CheckDocument(CheckDocumentRequest(document=nobody), timeout=5)
    assert (answer.applies, [(each.field, each.code) for each in answer.problems]) == (
        True,
        [("emission_point_id", "no_sequence")],
    )
    assert (clean.applies, list(clean.problems), clean.omitted) == (True, [], 0)
    assert caught.value.code() is grpc.StatusCode.INVALID_ARGUMENT


def test_a_declined_document_is_announced_on_its_subject() -> None:
    document = DocumentId(new_id())
    message, subject = outgoing(
        DocumentDeclined(
            tenant=TENANT, document=document, messages=(DgiiMessage(code="no_sequence", text="None left"),)
        )
    )
    assert subject == "fiscal.document.declined"
    assert message == fiscal_events.DocumentDeclined(
        document_id=str(document),
        organization_id=str(TENANT.organization),
        company_id=str(TENANT.company),
        version=1,
        messages=[fiscal_events.DgiiMessage(code="no_sequence", text="None left")],
    )
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest services/fiscal-do/tests/test_commercial.py -q --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'ledger_fiscal_do.adapters.commercial'`.

- [ ] **Step 4: Implement the domain's and the application's part**

`services/fiscal-do/src/ledger_fiscal_do/domain/problems.py` — `ProblemCode` gains, after `TEXT_CORRECTION_HAS_AMOUNTS`:

```python
    NOT_COMMERCIAL = "not_commercial"
    """A type of e-CF no commercial document becomes (a purchase's: 41, 43, 47); it is issued at /v1/do."""
    TAX_RATE_NOT_IN_FORCE = "tax_rate_not_in_force"
    """A tax category at a rate the Dominican Republic does not levy (ITBIS: 18 %, 16 %, 0 % on exports)."""
    FIELD_UNKNOWN = "field_unknown"
    """A fiscal field fiscal-do does not read."""
    REFERENCE_NOT_FISCAL = "reference_not_fiscal"
    """A note refers to a document fiscal-do never numbered."""
    NO_SEQUENCE = "no_sequence"
    """No range of the type at the emission point has a number left today."""
    TOTALS_DISAGREE = "totals_disagree"
    """invoicing's total is not the total DGII's rules give: a defect of one of the two, never sent."""
```

`services/fiscal-do/src/ledger_fiscal_do/domain/events.py` — before the type aliases, and the aliases become:

```python
@final
@dataclass(frozen=True, slots=True, kw_only=True)
class DocumentDeclined:
    """A commercial document invoicing issued was not numbered after all, and never will be: no e-NCF taken.

    Its ``document`` is invoicing's id; ``messages`` say why, in fiscal-do's codes. Its version is 1: the
    fiscal document never existed beyond this.
    """

    tenant: Tenant
    document: DocumentId
    messages: tuple[DgiiMessage, ...]
    version: int = 1


type DocumentEvent = DocumentNumbered | DocumentAccepted | DocumentConditionallyAccepted | DocumentRejected
type FiscalEvent = (
    DocumentEvent | DocumentDeclined | SequenceLow | SequenceExpiring | AnnulmentRequested | AnnulmentDecided
)
```

`services/fiscal-do/src/ledger_fiscal_do/application/issuing.py` — `IssueRequest` gains, after `content`:

```python
    document: DocumentId | None = None
    """The fiscal document's id: invoicing's document's when it comes from invoicing (one id names the
    document everywhere, and a second numbering of it is a primary-key violation); a new one otherwise."""
```

and `issue()` makes the draft with `id=request.document or DocumentId(new_id()),` in place of `id=DocumentId(new_id()),`.

`services/fiscal-do/src/ledger_fiscal_do/adapters/events.py` — `DocumentDeclined` joins the imports of `ledger_fiscal_do.domain.events`, and `outgoing()` gains, before `case AnnulmentRequested():`:

```python
        case DocumentDeclined():
            declined = wire.DocumentDeclined(
                document_id=str(event.document),
                organization_id=organization,
                company_id=company,
                version=event.version,
                messages=_messages(event.messages),
            )
            return declined, subjects.DOCUMENT_DECLINED
```

`services/fiscal-do/src/ledger_fiscal_do/application/commercial.py`:

```python
"""invoicing's commercial documents, made fiscal: checked before they are issued, numbered once they are.

The anti-corruption layer's application side (spec §6). ``ledger_fiscal_do.adapters.commercial`` translates
invoicing's ``CommercialDocument`` into fiscal-do's words (``Commercial``: a ``Content``, an emission point,
the note's referred document); here, in a unit of the document's company:

* ``check`` answers invoicing's question before it issues (``FiscalRules.CheckDocument``): not fiscal-do's
  when the company has no fiscal profile (it is not enrolled in the Dominican e-CF); otherwise every problem
  the translation, the type's rules, the note's reference, the emission point's ranges and the totals show.
  It takes no number and writes nothing.
* ``adopt`` makes the fiscal document of an issued one (``invoicing.document.issued``), under invoicing's id:
  a redelivered event finds it there and does nothing more. A document it cannot number after all (a range
  used up since the check, the company deactivated, a rule broken) is declined, announced with
  ``DocumentDeclined``, and nothing is taken: invoicing marks it rejected, and the client issues a new one.
"""

from dataclasses import dataclass, field, replace
from typing import Final, final

from ledger_fiscal_do.application.issuing import IssueRequest, issue
from ledger_fiscal_do.domain.checking import checked
from ledger_fiscal_do.domain.content import Reference
from ledger_fiscal_do.domain.events import DocumentDeclined
from ledger_fiscal_do.domain.notation import local_date
from ledger_fiscal_do.domain.problems import (
    MAX_PROBLEMS,
    DocumentInvalidError,
    Problem,
    ProblemCode,
    ProblemsError,
)
from ledger_fiscal_do.domain.verdict import DgiiMessage
from ledger_kernel.errors import ErrorKind, LedgerError
lazy from ledger_fiscal_do.application.ports import CompanyFacts, FiscalUnit, FiscalXml
lazy from ledger_fiscal_do.domain.content import Content
lazy from ledger_fiscal_do.domain.document import FiscalDocument
lazy from ledger_fiscal_do.domain.ids import DocumentId, EmissionPointId
lazy from ledger_fiscal_do.domain.vocabulary import Modification
lazy from ledger_kernel.ids import Tenant
lazy from ledger_kernel.money import Money

DECLINING: Final = frozenset({ErrorKind.INVALID, ErrorKind.CONFLICT, ErrorKind.NOT_FOUND})
"""The kinds of refusal that decline a document; any other (a dependency unavailable, a defect) is retried."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Referred:
    """The invoicing document a note refers to, and why, in DGII's words."""

    document: DocumentId
    code: Modification
    reason: str = field(repr=False)


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Commercial:
    """An invoicing document in fiscal-do's words, its reference still to resolve."""

    document: DocumentId
    tenant: Tenant
    emission_point: EmissionPointId
    content: Content = field(repr=False)
    referred: Referred | None = None
    total: Money
    """invoicing's total: fiscal-do's own must agree with it."""


@final
@dataclass(frozen=True, slots=True)
class Untranslatable:
    """An invoicing document whose fields fiscal-do cannot read as an e-CF's: every problem found."""

    document: DocumentId
    tenant: Tenant
    problems: tuple[Problem, ...]


type Translated = Commercial | Untranslatable


@final
@dataclass(frozen=True, slots=True)
class FiscalCheck:
    """fiscal-do's answer to invoicing: whether the document is its, and what it would refuse."""

    applies: bool
    problems: tuple[Problem, ...] = ()
    omitted: int = 0


NOT_OURS: Final = FiscalCheck(applies=False)


async def _resolved(unit: FiscalUnit, commercial: Commercial) -> tuple[Content, list[Problem]]:
    referred = commercial.referred
    if referred is None:
        return commercial.content, []
    original = await unit.documents.get(referred.document)
    if original is None:
        return commercial.content, [Problem("reference.document_id", ProblemCode.REFERENCE_NOT_FISCAL)]
    reference = Reference(
        modified=str(original.encf),
        issued_on=original.content.issued_on,
        code=referred.code,
        reason=referred.reason,
    )
    return replace(commercial.content, reference=reference), []


async def _range_problems(unit: FiscalUnit, commercial: Commercial) -> list[Problem]:
    today = local_date(unit.now)
    ranges = await unit.sequences.of_type(commercial.content.ecf_type)
    usable = any(
        each.emission_point == commercial.emission_point and each.usable_on(today) for each in ranges
    )
    return [] if usable else [Problem("emission_point_id", ProblemCode.NO_SEQUENCE)]


def _bounded(problems: list[Problem], omitted: int = 0) -> FiscalCheck:
    kept = tuple(problems[:MAX_PROBLEMS])
    return FiscalCheck(applies=True, problems=kept, omitted=max(len(problems) - MAX_PROBLEMS, 0) + omitted)


async def check(unit: FiscalUnit, translated: Translated) -> FiscalCheck:
    """What fiscal-do would refuse of the document (see the module); it takes nothing and writes nothing."""
    if await unit.profiles.get() is None:
        return NOT_OURS
    if isinstance(translated, Untranslatable):
        return _bounded(list(translated.problems))
    content, problems = await _resolved(unit, translated)
    omitted = 0
    try:
        totals = checked(content)
    except ProblemsError as error:
        problems += error.problems
        omitted = error.omitted
    else:
        if totals.total != translated.total:
            problems.append(Problem("totals.total", ProblemCode.TOTALS_DISAGREE))
    problems += await _range_problems(unit, translated)
    return _bounded(problems, omitted)


async def _decline(unit: FiscalUnit, document: DocumentId, tenant: Tenant, why: LedgerError) -> None:
    message = DgiiMessage(code=why.code, text=str(why))
    await unit.events.publish(DocumentDeclined(tenant=tenant, document=document, messages=(message,)))


async def adopt(
    unit: FiscalUnit, translated: Translated, *, company: CompanyFacts, xml: FiscalXml
) -> FiscalDocument | None:
    """The fiscal document of an issued commercial one (see the module); ``None``: declined, or not ours."""
    if await unit.profiles.get() is None:
        return None
    existing = await unit.documents.get(translated.document)
    if existing is not None:
        return existing
    if isinstance(translated, Untranslatable):
        await _decline(
            unit, translated.document, translated.tenant, DocumentInvalidError(translated.problems)
        )
        return None
    content, problems = await _resolved(unit, translated)
    if problems:
        await _decline(unit, translated.document, translated.tenant, DocumentInvalidError(tuple(problems)))
        return None
    request = IssueRequest(
        emission_point=translated.emission_point, content=content, document=translated.document
    )
    try:
        return await issue(unit, request, company=company, xml=xml)
    except LedgerError as error:
        if error.kind not in DECLINING:
            raise
        await _decline(unit, translated.document, translated.tenant, error)
        return None
```

`services/fiscal-do/src/ledger_fiscal_do/adapters/commercial.py`:

```python
"""invoicing's ``CommercialDocument`` read in fiscal-do's words: the anti-corruption layer's wire side.

invoicing speaks commerce (an invoice, a credit note, a tax category at a rate, a payment by card); fiscal-do
speaks DGII (a type of e-CF, an ITBIS billing indicator, a ``FormaPago`` code). This module is the only place
the two vocabularies meet, in one direction; ``ledger_invoicing.adapters.wire`` is the way back. A value that
does not translate is a problem of the field, said in fiscal-do's codes, never an exception: invoicing answers
its client with every one at once.

* The type: ``fiscal_fields["ecf_type"]`` when given (an invoice's: 31, 32, 44, 45 or 46; a note's must be
  33 or 34), else a credit note is 34, a debit note 33, and an invoice 31 when its customer has a tax id, 32
  when not. Purchases' types (41, 43, 47) are no commercial document's: ``not_commercial`` (they are issued at
  ``/v1/do/documents``).
* Taxes: standard at 18 % is ITBIS 1, reduced at 16 % ITBIS 2, zero ITBIS 3, exempt is exempt, outside scope
  is not billable; any other rate is ``tax_rate_not_in_force``.
* ``fiscal_fields`` read: ``ecf_type``, ``income_type`` (DGII's code, ``01`` by default for an invoice) and
  ``destination_country`` (an export's); any other is ``field_unknown``.
* Amounts are Dominican pesos; a document in another currency is ``wrong_currency`` (fiscal-do does not yet
  write other currencies: milestone 4's Decision 22).
"""

from datetime import date
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Final
from uuid import UUID
lazy from collections.abc import Mapping

import ledger.invoicing.v1.document_pb2 as wire
from ledger_fiscal_do.application.commercial import Commercial, Referred, Untranslatable
from ledger_fiscal_do.domain.content import Buyer, Content, Line, Payment, Withholding
from ledger_fiscal_do.domain.ids import DocumentId, EmissionPointId
from ledger_fiscal_do.domain.problems import Problem, ProblemCode
from ledger_fiscal_do.domain.vocabulary import (
    Billing,
    EcfType,
    IncomeType,
    ItemKind,
    Modification,
    PaymentMethod,
    PaymentTerms,
)
from ledger_kernel.countries.do import InvalidTaxIdError, TaxId
from ledger_kernel.errors import InvalidInputError
from ledger_kernel.ids import CompanyId, OrganizationId, Tenant, parse_id
from ledger_kernel.money import Currency, Money
lazy from ledger.common.v1.money_pb2 import Money as WireMoney
lazy from ledger_fiscal_do.application.commercial import Translated

COMMERCIAL_TYPES: Final = frozenset(
    {
        EcfType.CREDIT_INVOICE,
        EcfType.CONSUMER_INVOICE,
        EcfType.DEBIT_NOTE,
        EcfType.CREDIT_NOTE,
        EcfType.SPECIAL_REGIMES,
        EcfType.GOVERNMENT,
        EcfType.EXPORTS,
    }
)
FIELDS: Final = frozenset({"ecf_type", "income_type", "destination_country"})
_NOTES: Final[Mapping[wire.DocumentKind.ValueType, EcfType]] = MappingProxyType(
    {wire.DOCUMENT_KIND_CREDIT_NOTE: EcfType.CREDIT_NOTE, wire.DOCUMENT_KIND_DEBIT_NOTE: EcfType.DEBIT_NOTE}
)
_BILLING: Final[Mapping[tuple[wire.TaxCategory.ValueType, Decimal], Billing]] = MappingProxyType(
    {
        (wire.TAX_CATEGORY_STANDARD, Decimal(18)): Billing.ITBIS_1,
        (wire.TAX_CATEGORY_REDUCED, Decimal(16)): Billing.ITBIS_2,
        (wire.TAX_CATEGORY_ZERO, Decimal(0)): Billing.ITBIS_3,
        (wire.TAX_CATEGORY_EXEMPT, Decimal(0)): Billing.EXEMPT,
        (wire.TAX_CATEGORY_OUTSIDE_SCOPE, Decimal(0)): Billing.NOT_BILLABLE,
    }
)
_ITEMS: Final = MappingProxyType(
    {wire.ITEM_KIND_GOOD: ItemKind.GOOD, wire.ITEM_KIND_SERVICE: ItemKind.SERVICE}
)
_TERMS: Final = MappingProxyType(
    {
        wire.PAYMENT_TERMS_KIND_CASH: PaymentTerms.CASH,
        wire.PAYMENT_TERMS_KIND_CREDIT: PaymentTerms.CREDIT,
        wire.PAYMENT_TERMS_KIND_FREE: PaymentTerms.FREE,
    }
)
_METHODS: Final = MappingProxyType(
    {
        wire.PAYMENT_METHOD_CASH: PaymentMethod.CASH,
        wire.PAYMENT_METHOD_BANK_TRANSFER: PaymentMethod.CHECK_TRANSFER_DEPOSIT,
        wire.PAYMENT_METHOD_CARD: PaymentMethod.CARD,
        wire.PAYMENT_METHOD_CREDIT: PaymentMethod.CREDIT,
        wire.PAYMENT_METHOD_VOUCHER: PaymentMethod.GIFT_VOUCHERS,
        wire.PAYMENT_METHOD_BARTER: PaymentMethod.BARTER,
        wire.PAYMENT_METHOD_CREDIT_NOTE: PaymentMethod.CREDIT_NOTE,
        wire.PAYMENT_METHOD_OTHER: PaymentMethod.OTHER,
    }
)
_CORRECTIONS: Final = MappingProxyType(
    {
        wire.CORRECTION_ANNULMENT: Modification.ANNULS,
        wire.CORRECTION_TEXT: Modification.CORRECTS_TEXT,
        wire.CORRECTION_AMOUNTS: Modification.CORRECTS_AMOUNTS,
    }
)

type Problems = list[Problem]


def tenant_of(document: wire.CommercialDocument) -> Tenant:
    """The document's company and organization; ``InvalidIdError`` when they are not ids."""
    return Tenant(
        OrganizationId(parse_id(document.organization_id)), CompanyId(parse_id(document.company_id))
    )


def _decimal(problems: Problems, where: str, text: str) -> Decimal:
    try:
        value = Decimal(text)
    except InvalidOperation:
        problems.append(Problem(where, ProblemCode.CODE_MALFORMED))
        return Decimal(0)
    if not value.is_finite():
        problems.append(Problem(where, ProblemCode.CODE_MALFORMED))
        return Decimal(0)
    return value


def _money(problems: Problems, where: str, value: WireMoney) -> Money:
    try:
        return Money.of(value.amount, Currency(value.currency))
    except InvalidInputError, ValueError:
        problems.append(Problem(where, ProblemCode.CODE_MALFORMED))
        return Money.zero(Currency.DOP)


def _text(value: str) -> str | None:
    return value or None


def _date(problems: Problems, where: str, text: str) -> date | None:
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        problems.append(Problem(where, ProblemCode.CODE_MALFORMED))
        return None


def _ecf_type(problems: Problems, document: wire.CommercialDocument) -> EcfType:
    named = document.fiscal_fields.get("ecf_type")
    note = _NOTES.get(document.kind)
    if named is None:
        if note is not None:
            return note
        return EcfType.CREDIT_INVOICE if document.customer.tax_id else EcfType.CONSUMER_INVOICE
    try:
        chosen = EcfType(int(named))
    except ValueError:
        problems.append(Problem("fiscal_fields.ecf_type", ProblemCode.CODE_MALFORMED))
        return note or EcfType.CONSUMER_INVOICE
    if chosen not in COMMERCIAL_TYPES:
        problems.append(Problem("fiscal_fields.ecf_type", ProblemCode.NOT_COMMERCIAL))
    elif (note is None) == (chosen in _NOTES.values()) or (note is not None and chosen is not note):
        problems.append(Problem("fiscal_fields.ecf_type", ProblemCode.NOT_ALLOWED))
    return chosen


def _buyer(problems: Problems, document: wire.CommercialDocument) -> Buyer | None:
    if not document.HasField("customer"):
        return None
    party = document.customer
    tax_id: TaxId | None = None
    if party.tax_id:
        try:
            tax_id = TaxId.parse(party.tax_id)
        except InvalidTaxIdError:
            problems.append(Problem("customer.tax_id", ProblemCode.CODE_MALFORMED))
    address = party.address
    return Buyer(
        tax_id=tax_id,
        foreign_id=_text(party.foreign_id),
        name=_text(party.name),
        email=_text(party.email),
        address=_text(address.line),
        municipality=_text(address.city_code),
        province=_text(address.region_code),
        country=_text(document.fiscal_fields.get("destination_country", "")),
    )


def _line(problems: Problems, index: int, line: wire.Line) -> Line:
    where = f"lines[{index}]"
    rate = _decimal(problems, f"{where}.tax.rate", line.tax.rate)
    billing = _BILLING.get((line.tax.category, rate))
    if billing is None:
        problems.append(Problem(f"{where}.tax", ProblemCode.TAX_RATE_NOT_IN_FORCE))
        billing = Billing.EXEMPT
    held = line.withholding
    withholding = (
        Withholding(
            itbis=_money(problems, f"{where}.withholding", held.value_added_tax)
            if held.HasField("value_added_tax")
            else None,
            isr=_money(problems, f"{where}.withholding", held.income_tax)
            if held.HasField("income_tax")
            else None,
        )
        if line.HasField("withholding")
        else None
    )
    unit = int(line.unit_code) if line.unit_code.isascii() and line.unit_code.isdigit() else None
    return Line(
        number=line.number,
        name=line.name,
        description=_text(line.description),
        kind=_ITEMS.get(line.kind, ItemKind.GOOD),
        quantity=_decimal(problems, f"{where}.quantity", line.quantity),
        unit=unit,
        unit_price=_decimal(problems, f"{where}.unit_price", line.unit_price),
        discount=_money(problems, f"{where}.discount", line.discount) if line.HasField("discount") else None,
        billing=billing,
        withholding=withholding,
    )


def _income(problems: Problems, document: wire.CommercialDocument, ecf_type: EcfType) -> IncomeType | None:
    named = document.fiscal_fields.get("income_type")
    if named is None:
        return None if ecf_type in _NOTES.values() else IncomeType.OPERATIONS
    try:
        return IncomeType(named)
    except ValueError:
        problems.append(Problem("fiscal_fields.income_type", ProblemCode.CODE_MALFORMED))
        return None


def _referred(problems: Problems, document: wire.CommercialDocument) -> Referred | None:
    if not document.HasField("reference"):
        return None
    reference = document.reference
    code = _CORRECTIONS.get(reference.correction)
    try:
        referred = DocumentId(parse_id(reference.document_id))
    except InvalidInputError:
        problems.append(Problem("reference.document_id", ProblemCode.CODE_MALFORMED))
        return None
    if code is None:
        problems.append(Problem("reference.correction", ProblemCode.CODE_MALFORMED))
        return None
    return Referred(document=referred, code=code, reason=reference.reason)


def translate(document: wire.CommercialDocument) -> Translated:
    """``document`` in fiscal-do's words, or every problem that kept it from them (see the module).

    The ids are read first: a document whose company is not an id is no one's (``InvalidIdError``).
    """
    tenant = tenant_of(document)
    identifier = DocumentId(parse_id(document.document_id))
    problems: Problems = [
        Problem("fiscal_fields", ProblemCode.FIELD_UNKNOWN)
        for name in document.fiscal_fields
        if name not in FIELDS
    ]
    if document.currency != Currency.DOP.value:
        problems.append(Problem("currency", ProblemCode.WRONG_CURRENCY))
    ecf_type = _ecf_type(problems, document)
    issued_on = _date(problems, "issue_date", document.issue_date)
    terms = document.terms
    content = Content(
        ecf_type=ecf_type,
        issued_on=issued_on or date.min,
        income=_income(problems, document, ecf_type),
        terms=_TERMS.get(terms.kind),
        deadline=_date(problems, "terms.due_date", terms.due_date),
        payments=tuple(
            Payment(
                _METHODS.get(payment.method, PaymentMethod.OTHER),
                _money(problems, f"payments[{index}]", payment.amount),
            )
            for index, payment in enumerate(document.payments)
        ),
        prices_include_itbis=document.prices_include_tax,
        buyer=_buyer(problems, document),
        lines=tuple(_line(problems, index, line) for index, line in enumerate(document.lines)),
    )
    referred = _referred(problems, document)
    total = _money(problems, "totals.total", document.totals.total)
    try:
        point = EmissionPointId(parse_id(document.emission_point_id))
    except InvalidInputError:
        problems.append(Problem("emission_point_id", ProblemCode.CODE_MALFORMED))
        point = EmissionPointId(UUID(int=0))
    if problems:
        return Untranslatable(identifier, tenant, tuple(problems))
    return Commercial(
        document=identifier,
        tenant=tenant,
        emission_point=point,
        content=content,
        referred=referred,
        total=total,
    )
```

`services/fiscal-do/src/ledger_fiscal_do/api/rules.py`:

```python
"""fiscal-do's answer to ``ledger.fiscal.v1.FiscalRulesService``: what it would refuse of a document.

invoicing asks before it issues (spec §5.1: a local failure is answered at once, before any number is taken).
The document names its company; the caller is invoicing, inside the mesh, never a client: fiscal-do trusts
the request's company as it trusts the events invoicing publishes. The answer takes nothing and writes
nothing, and it asks identity nothing (a deactivated company is declined when the document is adopted).
"""

from typing import final, override

import grpc

from ledger.fiscal.v1.fiscal_rules_pb2 import CheckDocumentRequest, CheckDocumentResponse, Problem
from ledger.fiscal.v1.fiscal_rules_pb2_grpc import FiscalRulesServiceServicer
from ledger_fiscal_do.adapters.commercial import translate
from ledger_fiscal_do.application.commercial import check
lazy from ledger_fiscal_do.application.ports import Units

type Context = grpc.aio.ServicerContext[CheckDocumentRequest, CheckDocumentResponse]


@final
class FiscalRules(FiscalRulesServiceServicer):
    """``CheckDocument`` over fiscal-do's units of work."""

    def __init__(self, units: Units) -> None:
        self._units = units

    @override
    async def CheckDocument(self, request: CheckDocumentRequest, context: Context) -> CheckDocumentResponse:
        del context
        translated = translate(request.document)
        async with self._units.unit(translated.tenant) as unit:
            found = await check(unit, translated)
        return CheckDocumentResponse(
            applies=found.applies,
            problems=[Problem(field=each.field, code=each.code.value) for each in found.problems],
            omitted=found.omitted,
        )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest services/fiscal-do/tests/test_commercial.py -q --no-cov`
Expected: PASS (31 tests: the translation and its every refusal, the check, adoption under invoicing's id, the declined, the servicer over real gRPC).

- [ ] **Step 6: The consumer and the gRPC service**

`services/fiscal-do/src/ledger_fiscal_do/api/consumers.py` — the module's docstring gains a bullet:

```text
* ``invoicing.document.issued`` (stream ``INVOICING``): invoicing's document made an e-CF through the
  anti-corruption layer (``ledger_fiscal_do.application.commercial.adopt``), under invoicing's id; what fiscal-do
  cannot number is declined (``fiscal.document.declined``). identity is asked for the company's facts inside the
  inbox transaction, bounded by the call's deadline, as the emission's consumer asks Temporal.
```

the imports gain `import ledger.events.invoicing.v1.events_pb2 as invoicing_wire`, `from ledger_contracts import invoicing`, `from ledger_fiscal_do.adapters.commercial import translate`, `from ledger_fiscal_do.adapters.xml.documents import DgiiXml`, `from ledger_fiscal_do.application.commercial import adopt` and `InvalidInputError` beside `LedgerError`; after `ROTATIONS`:

```python
COMMERCIAL: Final = "fiscal-do-commercial-documents"
"""The durable consumer of ``invoicing.document.issued``: invoicing's documents made e-CF."""


class MismatchedTenantError(InvalidInputError):
    """An invoicing event whose document names another company than its envelope: a defect of its publisher."""

    code: ClassVar[str] = "event_tenant_mismatch"

    def __init__(self) -> None:
        super().__init__("an invoicing event's document names another company than the event")
```

inside `consumers()`, after `certificate`:

```python
    xml = DgiiXml()

    async def commercial(envelope: Envelope, unit: UnitOfWork) -> None:
        issued = envelope.unwrap(invoicing_wire.DocumentIssued)
        translated = translate(issued.document)
        if translated.tenant != _tenant(envelope):
            raise MismatchedTenantError
        company = await resources.companies.company(translated.tenant)
        await adopt(bind(unit, runtime.outbox), translated, company=company, xml=xml)
```

and the tuple it returns gains:

```python
        ConsumerSpec(
            name=COMMERCIAL, stream=invoicing.STREAM, subject=invoicing.DOCUMENT_ISSUED, handler=commercial
        ),
```

`services/fiscal-do/src/ledger_fiscal_do/main.py` — the imports gain `import grpc`, `from ledger.fiscal.v1.fiscal_rules_pb2_grpc import add_FiscalRulesServiceServicer_to_server`, `from ledger_fiscal_do.adapters.postgres.units import PgUnits` and `from ledger_fiscal_do.api.rules import FiscalRules`; inside `build()`, after `temporal`:

```python
    def grpc_services(runtime: Runtime[FiscalSettings], server: grpc.aio.Server) -> None:
        rules = FiscalRules(PgUnits(runtime.database, runtime.outbox))
        add_FiscalRulesServiceServicer_to_server(rules, server)
```

and `Interfaces(...)` gains `grpc=grpc_services`. The docstring of `build` adds: "It serves ``ledger.fiscal.v1.FiscalRulesService`` on the chassis's gRPC port (``LEDGER_GRPC_PORT``, 50051), for invoicing."

`services/fiscal-do/tests/test_service.py` — the settings of the `service` fixture gain `grpc_port=0`; beside `VAULT`, `INVOICING: Final = StreamSpec(name=invoicing.STREAM, subjects=invoicing.SUBJECTS)`, declared in the fixture next to `VAULT` (`await ensure_stream(JetStreamContext(connection), INVOICING)`); the imports gain `import grpc`, `import ledger.events.fiscal_do.v1.events_pb2 as fiscal_wire`, `import ledger.events.invoicing.v1.events_pb2 as invoicing_wire`, `import ledger.invoicing.v1.document_pb2 as commercial_wire`, `from ledger.common.v1.money_pb2 import Money as WireMoney`, `from ledger.fiscal.v1.fiscal_rules_pb2 import CheckDocumentRequest`, `from ledger.fiscal.v1.fiscal_rules_pb2_grpc import FiscalRulesServiceStub`, `from nats.errors import TimeoutError as NatsTimeoutError`, `from ledger_contracts import fiscal_do, invoicing`, `from ledger_fiscal_do.api.consumers import COMMERCIAL, MismatchedTenantError`, `from ledger_kernel.ids import new_id`; and it gains:

```python
def commercial_invoice() -> commercial_wire.CommercialDocument:
    """What invoicing announces of a credit invoice to COMPRADORA SRL at ``POINT``: 1,000.00 at 18 %."""
    total = WireMoney(amount="1180.00", currency="DOP")
    return commercial_wire.CommercialDocument(
        document_id=str(new_id()),
        organization_id=str(COMPANY.organization),
        company_id=str(COMPANY.company),
        kind=commercial_wire.DOCUMENT_KIND_INVOICE,
        version=2,
        emission_point_id=POINT,
        issue_date="2026-10-12",
        currency="DOP",
        customer=commercial_wire.Party(tax_id="131880738", name="COMPRADORA SRL"),
        lines=[
            commercial_wire.Line(
                number=1,
                kind=commercial_wire.ITEM_KIND_GOOD,
                name="Martillo",
                quantity="1",
                unit_price="1000.00",
                tax=commercial_wire.Tax(category=commercial_wire.TAX_CATEGORY_STANDARD, rate="18"),
            )
        ],
        terms=commercial_wire.Terms(kind=commercial_wire.PAYMENT_TERMS_KIND_CASH),
        payments=[commercial_wire.Payment(method=commercial_wire.PAYMENT_METHOD_CASH, amount=total)],
        totals=commercial_wire.Totals(total=total),
    )


async def test_an_invoicing_document_is_checked_then_numbered_under_its_id_and_accepted(
    service: tuple[Service[FiscalSettings], LocalIssuer],
) -> None:
    fiscal, issuer = service
    async with running(fiscal) as client:
        await prepared(client, issuer)
        document = commercial_invoice()
        port = fiscal.grpc_port
        assert port is not None
        async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
            stub = FiscalRulesServiceStub(channel)
            answer = await stub.CheckDocument(CheckDocumentRequest(document=document), timeout=5)
        assert (answer.applies, list(answer.problems)) == (True, [])
        runtime = fiscal.runtime()
        handlers = {spec.name: spec.handler for spec in consumers(runtime)}
        issued = invoicing_wire.DocumentIssued(document=document)
        envelope = Envelope.wrap(issued, source="invoicing", tenant=COMPANY)
        for _ in range(2):  # a redelivery numbers nothing more
            async with runtime.database.transaction(COMPANY) as unit:
                await handlers[COMMERCIAL](envelope, unit)
        location = f"/v1/do/documents/{document.document_id}"

        async def accepted() -> bool:
            read = BODY.validate_json((await client.get(location, headers=headers(issuer))).content)
            return read["state"] == "accepted"

        await eventually(accepted, within=30)
        read = BODY.validate_json((await client.get(location, headers=headers(issuer))).content)
        assert read["encf"] == "E310000000001"
        listed = BODY.validate_json((await client.get("/v1/do/documents", headers=headers(issuer))).content)
        assert len(str(listed["items"]).split("'encf'")) == 2  # one document
        stranger = Envelope.wrap(issued, source="invoicing", tenant=Tenant(COMPANY.organization, TENANT.company))
        async with runtime.database.transaction(stranger.tenant) as unit:
            with pytest.raises(MismatchedTenantError):
                await handlers[COMMERCIAL](stranger, unit)


async def test_an_invoicing_document_fiscal_do_cannot_number_is_declined(
    service: tuple[Service[FiscalSettings], LocalIssuer], infrastructure: Infrastructure
) -> None:
    fiscal, issuer = service
    async with running(fiscal) as client:
        await prepared(client, issuer)
        document = commercial_invoice()
        document.ClearField("customer")  # a consumer invoice (32): no range of it at POINT
        runtime = fiscal.runtime()
        handlers = {spec.name: spec.handler for spec in consumers(runtime)}
        envelope = Envelope.wrap(invoicing_wire.DocumentIssued(document=document), source="invoicing", tenant=COMPANY)
        async with runtime.database.transaction(COMPANY) as unit:
            await handlers[COMMERCIAL](envelope, unit)
        missing = await client.get(f"/v1/do/documents/{document.document_id}", headers=headers(issuer))
        assert missing.status_code == 404
    connection = Client()
    await connection.connect(infrastructure.nats_url)
    try:
        subscription = await JetStreamContext(connection).pull_subscribe(
            fiscal_do.DOCUMENT_DECLINED, stream=fiscal_do.STREAM
        )

        async def announced() -> bool:
            try:
                messages = await subscription.fetch(10, timeout=1)
            except NatsTimeoutError:
                return False
            declined = [
                Envelope.from_headers(message.headers or {}, message.data).unwrap(fiscal_wire.DocumentDeclined)
                for message in messages
            ]
            return any(each.document_id == document.document_id for each in declined)

        await eventually(announced, within=10)
    finally:
        await connection.close()
```

Run: `uv run pytest services/fiscal-do -q --no-cov`
Expected: PASS: Milestone 4's tests unchanged, and an invoicing document checked over gRPC, numbered once under its own id (E310000000001) and accepted by the fake DGII; one fiscal-do cannot number is declined and announced.

- [ ] **Step 7: Gate**

Run: `uv run poe fmt && uv run poe check`
Expected: every step passes, coverage 100 %.

- [ ] **Step 8: Commit**

```bash
git add uv.lock services/fiscal-do
git commit -m "feat(fiscal-do): invoicing's documents through an anti-corruption layer, FiscalRules over gRPC

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: The webhooks package and its domain: the catalog, targets, signatures, deliveries, endpoints

**Files:**
- Create: `services/webhooks/pyproject.toml`, `services/webhooks/src/ledger_webhooks/__init__.py`, `py.typed`, `domain/__init__.py`, `domain/ids.py`, `domain/catalog.py`, `domain/targets.py`, `domain/signing.py`, `domain/delivery.py`, `domain/endpoint.py`, `domain/payloads.py`
- Modify: `pyproject.toml` (root)
- Test: `services/webhooks/tests/webhook_samples.py` (support), `services/webhooks/tests/test_targets.py`, `services/webhooks/tests/test_domain.py`

**Interfaces:**
- Consumes: `ledger_kernel.errors` (`InvalidInputError`, `UnavailableError`, `ConflictError`, `NotFoundError`, `LedgerError`, `excerpt`), `ledger_kernel.ids`.
- Produces: `ids`: `EndpointId`, `DeliveryId`, `EventId`, `SecretId`. `catalog`: `MAX_FILTERS`, `EVERY`, `EventType` (`invoice.issued`, `invoice.voided`, `invoice.fiscal.numbered|accepted|rejected`, `webhook.test`), `VERSIONS`, `DESCRIPTIONS`, `InvalidFilterError`, `FiltersCountError`, `Filters` (`parse`, `admits`; `*` and `invoice.*`). `targets`: `MAX_URL`, `LOCAL_NAMES`, `LOCAL_SUFFIXES`, `Address`, `TargetProblem`, `TargetRefusedError`, `AddressRefusedError`, `is_public(address)` (IPv4 inside IPv6's mapped, 6to4 and NAT64 forms checked; Teredo refused), `require_public(addresses, *, admits)`, `Target` (`parse(url, *, allow_http, allow_private)`, `address`), `TargetPolicy` (`parse`, `admits`), `STRICT`. `signing`: `PREFIX` (`whsec_`), `KEY_BYTES`, `TOLERANCE` (5 minutes), `MIN_OVERLAP`, `DEFAULT_OVERLAP`, `MAX_OVERLAP`, `VERSION`, `WEBHOOK_ID`, `WEBHOOK_TIMESTAMP`, `WEBHOOK_SIGNATURE`, `SignatureProblem`, `SignatureRefusedError`, `OverlapError`, `Secret` (`text`, `in_force`), `SecretRing` (`rotated`, `in_force`), `signature(message_id, timestamp, body, keys)`, `key_of(text)`, `verify(secrets, *, headers, body, now)`. `delivery`: `MAX_ATTEMPTS` (12), `FIRST_WAIT`, `LONGEST_WAIT`, `JITTER`, `DeliveryState`, `Failure`, `DeliveryNotFoundError`, `DeliveryFinishedError`, `Attempt` (`succeeded`), `Schedule` (`wait`: 1 minute doubling to 6 hours, ±20 %), `Delivery` (`first`, `attempted`, `cancelled`). `endpoint`: `MAX_DESCRIPTION`, `EndpointNotFoundError`, `Subscription`, `Endpoint` (`receives`, `changed`, `rotated`, `removed`). `payloads`: `MAX_PAYLOAD`, `PayloadTooLargeError`, `PublicEvent` (`version`, `body`). Test support `webhook_samples`: `TENANT`, `OTHER_TENANT`, `NOW`, `TARGET`, `EVERYTHING`, `secret`, `endpoint`, `event`.

Re-check before starting: the kernel's errors (`InvalidInputError`, `UnavailableError`, `excerpt`) as landed; the names in this task's Produces line are read from the code below, which is authoritative.

- [ ] **Step 1: Package and repository settings**

`services/webhooks/pyproject.toml`:

```toml
[project]
name = "ledger-webhooks"
version = "0.1.0"
description = "The webhooks service: client endpoints, deliveries signed, retried and guarded against SSRF."
requires-python = ">=3.15"
dependencies = ["ledger-kernel"]

[tool.uv.sources]
ledger-kernel = { workspace = true }

[build-system]
requires = ["uv_build>=0.13.0,<0.14.0"]
build-backend = "uv_build"
```

`services/webhooks/src/ledger_webhooks/__init__.py`:

```python
"""webhooks: a company's endpoints, and every public event delivered to them signed, retried, and guarded."""
```

`services/webhooks/src/ledger_webhooks/domain/__init__.py`:

```python
"""webhooks' domain: endpoints, secrets, signatures, targets, deliveries; standard library and kernel only."""
```

and an empty `services/webhooks/src/ledger_webhooks/py.typed`.

Root `pyproject.toml`, as Task 3 did for invoicing:

- `[project] dependencies` gains `"ledger-webhooks"`; `[tool.uv.sources]` gains `ledger-webhooks = { workspace = true }`.
- ruff: `known-first-party` gains `"ledger_webhooks"`; `known-local-folder` gains `"webhook_samples"`, `"webhook_fakes"` and `"webhook_remotes"`.
- basedpyright: an execution environment before the catch-all one:

```toml
[[tool.basedpyright.executionEnvironments]]
root = "services/webhooks/tests"
extraPaths = ["services/webhooks/tests"]
```

- pytest: `pythonpath` gains `"services/webhooks/tests"`. coverage: `source_pkgs` gains `"ledger_webhooks"`.
- poe: `mypy-webhooks = { cmd = "mypy services/webhooks" }`, `deps-webhooks = { cmd = "deptry services/webhooks/src --config services/webhooks/pyproject.toml" }`, each added to its sequence.
- import-linter: `"ledger_webhooks"` joins `root_packages`, "Production code never imports test tooling"'s `source_modules`, "Libraries never import services"' `forbidden_modules` and "Services never import each other"'s `modules`; and:

```toml
[[tool.importlinter.contracts]]
name = "webhooks is hexagonal"
type = "layers"
containers = ["ledger_webhooks"]
layers = ["domain"]

[[tool.importlinter.contracts]]
name = "webhooks' domain is standard library and kernel only"
type = "forbidden"
source_modules = ["ledger_webhooks.domain"]
forbidden_modules = ["ledger_platform", "ledger_contracts", "pydantic", "sqlalchemy", "fastapi", "grpc", "google", "httpx", "cryptography"]
```

(Each later task adds its layer; Task 13 leaves it `["openapi", "main", "api", "resources", "adapters", "settings", "application", "domain"]`.)

Run: `uv lock && uv sync`

- [ ] **Step 2: Write the failing tests**

`services/webhooks/tests/webhook_samples.py` (Task 12 adds the keyring and the storage):

```python
"""What the webhooks tests share: a company, a moment, secrets, an endpoint, a public event."""

from datetime import UTC, datetime
from typing import Final

from ledger_kernel.ids import CompanyId, OrganizationId, Tenant, new_id
from ledger_webhooks.domain.catalog import EventType, Filters
from ledger_webhooks.domain.endpoint import Endpoint
from ledger_webhooks.domain.ids import EndpointId, EventId, SecretId
from ledger_webhooks.domain.payloads import PublicEvent
from ledger_webhooks.domain.signing import Secret, SecretRing
from ledger_webhooks.domain.targets import Target

TENANT: Final = Tenant(OrganizationId(new_id()), CompanyId(new_id()))
OTHER_TENANT: Final = Tenant(TENANT.organization, CompanyId(new_id()))
NOW: Final = datetime(2026, 10, 12, 14, 30, tzinfo=UTC)
TARGET: Final = Target.parse("https://hooks.example.com/ledger")
EVERYTHING: Final = Filters.parse(["*"])


def secret(key: bytes = b"k" * 32, *, now: datetime = NOW) -> Secret:
    """A secret of ``key``."""
    return Secret(id=SecretId(new_id()), key=key, created_at=now)


def endpoint(filters: Filters = EVERYTHING, *, tenant: Tenant = TENANT, target: Target = TARGET) -> Endpoint:
    """A new endpoint of ``tenant``'s, receiving ``filters``."""
    return Endpoint(
        id=EndpointId(new_id()),
        tenant=tenant,
        target=target,
        filters=filters,
        secrets=SecretRing(secret()),
        created_at=NOW,
        updated_at=NOW,
    )


def event(kind: EventType = EventType.INVOICE_ISSUED, *, tenant: Tenant = TENANT) -> PublicEvent:
    """A public event of ``kind`` about a document of ``tenant``'s."""
    return PublicEvent(
        id=EventId(new_id()),
        type=kind,
        occurred_at=NOW,
        company=tenant.company,
        data={"invoice": {"id": str(new_id()), "kind": "invoice", "total": "1180.00"}},
    )
```

`services/webhooks/tests/test_targets.py`:

```python
import ipaddress

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ledger_kernel.errors import ErrorKind
from ledger_webhooks.domain.targets import (
    MAX_URL,
    AddressRefusedError,
    Target,
    TargetProblem,
    TargetRefusedError,
    is_public,
    require_public,
)
lazy from ledger_webhooks.domain.targets import Address

NOT_PUBLIC = [
    "127.0.0.1",
    "10.1.2.3",
    "172.16.0.1",
    "192.168.1.1",
    "169.254.169.254",
    "100.64.0.1",
    "100.100.100.200",
    str(ipaddress.IPv4Address(0)),
    "255.255.255.255",
    "224.0.0.1",
    "198.18.0.1",
    "240.0.0.1",
    "::1",
    "::",
    "fe80::1",
    "fd00:ec2::254",
    "ff02::1",
    "::ffff:127.0.0.1",
    "::ffff:10.0.0.1",
    "2002:7f00:1::1",
    "64:ff9b::7f00:1",
    "64:ff9b::a9fe:a9fe",
    "2001:0:4136:e378:8000:63bf:3fff:fdd2",
]
PUBLIC = [
    "8.8.8.8",
    "93.184.216.34",
    "2606:4700::1111",
    "::ffff:8.8.8.8",
    "64:ff9b::808:808",
    "2002:808:808::1",
]
private_v4 = st.integers(0, 2**32 - 1).map(ipaddress.IPv4Address).filter(lambda address: address.is_private)


@pytest.mark.parametrize("text", NOT_PUBLIC)
def test_no_address_inside_a_network_or_carrying_one_is_public(text: str) -> None:
    assert not is_public(ipaddress.ip_address(text))


@pytest.mark.parametrize("text", PUBLIC)
def test_public_addresses_are_public_whatever_their_form(text: str) -> None:
    assert is_public(ipaddress.ip_address(text))


@given(private_v4)
def test_a_private_ipv4_address_is_refused_in_every_ipv6_form_that_carries_it(
    address: ipaddress.IPv4Address,
) -> None:
    carried = int(address)
    forms: list[Address] = [
        address,
        ipaddress.IPv6Address(0xFFFF_0000_0000 | carried),
        ipaddress.IPv6Address((0x2002 << 112) | (carried << 80)),
        ipaddress.IPv6Address((0x0064_FF9B << 96) | carried),
    ]
    assert not any(is_public(form) for form in forms)


def test_one_private_address_among_public_ones_refuses_them_all() -> None:
    public, private = ipaddress.ip_address("8.8.8.8"), ipaddress.ip_address("10.0.0.1")
    assert require_public((public, ipaddress.ip_address("8.8.4.4"))) == public
    for refused in ((public, private), (private,), ()):
        with pytest.raises(AddressRefusedError) as caught:
            require_public(refused)
        assert caught.value.kind is ErrorKind.UNAVAILABLE


def test_a_url_becomes_a_target_with_its_parts() -> None:
    target = Target.parse("https://Hooks.Example.com/ledger/events?source=x")
    assert (target.scheme, target.host, target.port, target.path, target.address) == (
        "https",
        "hooks.example.com",
        443,
        "/ledger/events?source=x",
        None,
    )
    literal = Target.parse("https://93.184.216.34:8443")
    assert (literal.host, literal.port, literal.path, literal.address) == (
        "93.184.216.34",
        8443,
        "/",
        ipaddress.ip_address("93.184.216.34"),
    )
    assert Target.parse("https://[2606:4700::1111]/x").host == "2606:4700::1111"
    assert Target.parse("https://bücher.example/x").host == "xn--bcher-kva.example"
    assert Target.parse("http://hooks.example.com", allow_http=True).port == 80
    assert "hooks" not in repr(target).replace("hooks.example.com", "")


@pytest.mark.parametrize(
    ("url", "problem"),
    [
        ("https://example.com/" + "x" * MAX_URL, TargetProblem.TOO_LONG),
        ("http://hooks.example.com", TargetProblem.SCHEME),
        ("ftp://hooks.example.com", TargetProblem.SCHEME),
        ("https://", TargetProblem.MALFORMED),
        ("https://hooks.example.com:99999/", TargetProblem.MALFORMED),
        ("https://exa_mple.com/", TargetProblem.MALFORMED),
        ("https://-bad-.example.com/", TargetProblem.MALFORMED),
        ("https://user:pass@hooks.example.com/", TargetProblem.CREDENTIALS),
        ("https://hooks.example.com/#part", TargetProblem.FRAGMENT),
        ("https://hooks.example.com/#", TargetProblem.FRAGMENT),
        ("https://localhost/", TargetProblem.LOCAL_NAME),
        ("https://api.localhost/", TargetProblem.LOCAL_NAME),
        ("https://printer.local/", TargetProblem.LOCAL_NAME),
        ("https://metadata.google.internal/", TargetProblem.LOCAL_NAME),
        ("https://intranet/", TargetProblem.LOCAL_NAME),
        ("https://127.0.0.1/", TargetProblem.PRIVATE_ADDRESS),
        ("https://169.254.169.254/latest/meta-data/", TargetProblem.PRIVATE_ADDRESS),
        ("https://[::ffff:10.0.0.1]/", TargetProblem.PRIVATE_ADDRESS),
        ("https://[fd00:ec2::254]/", TargetProblem.PRIVATE_ADDRESS),
        ("https://xn--.example.com/", TargetProblem.MALFORMED),
        ("https://" + "a" * 64 + ".example.com/", TargetProblem.MALFORMED),
    ],
    ids=[
        "too-long",
        "http",
        "ftp",
        "no-host",
        "port",
        "underscore",
        "hyphens",
        "credentials",
        "fragment",
        "empty-fragment",
        "localhost",
        "localhost-subdomain",
        "dot-local",
        "metadata-name",
        "single-label",
        "loopback",
        "metadata-address",
        "mapped-private",
        "unique-local",
        "bad-punycode",
        "label-too-long",
    ],
)
def test_a_url_that_could_reach_inside_is_refused_without_being_echoed(
    url: str, problem: TargetProblem
) -> None:
    with pytest.raises(TargetRefusedError) as caught:
        Target.parse(url)
    assert caught.value.problem is problem
    assert (caught.value.kind, caught.value.code) == (ErrorKind.INVALID, "webhook_target_refused")
    assert "example" not in str(caught.value)
```

`services/webhooks/tests/test_domain.py`:

```python
from datetime import timedelta
lazy from collections.abc import Callable

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import TypeAdapter

from ledger_kernel.errors import ErrorKind
from ledger_kernel.ids import new_id
from ledger_webhooks.domain.catalog import (
    MAX_FILTERS,
    VERSIONS,
    EventType,
    Filters,
    FiltersCountError,
    InvalidFilterError,
)
from ledger_webhooks.domain.delivery import (
    FIRST_WAIT,
    LONGEST_WAIT,
    MAX_ATTEMPTS,
    Attempt,
    Delivery,
    DeliveryFinishedError,
    DeliveryState,
    Failure,
    Schedule,
)
from ledger_webhooks.domain.endpoint import EndpointNotFoundError, Subscription
from ledger_webhooks.domain.ids import DeliveryId, EventId
from ledger_webhooks.domain.payloads import MAX_PAYLOAD, PayloadTooLargeError, PublicEvent
from ledger_webhooks.domain.signing import (
    DEFAULT_OVERLAP,
    WEBHOOK_ID,
    WEBHOOK_SIGNATURE,
    WEBHOOK_TIMESTAMP,
    OverlapError,
    SecretRing,
    SignatureProblem,
    SignatureRefusedError,
    key_of,
    signature,
    verify,
)

from webhook_samples import NOW, TARGET, TENANT, endpoint, event, secret

MIDDLE = Schedule(lambda: 0.5)
BODY = TypeAdapter(dict[str, object])


def headers(message_id: str, timestamp: int, value: str) -> dict[str, str]:
    return {WEBHOOK_ID: message_id, WEBHOOK_TIMESTAMP: str(timestamp), WEBHOOK_SIGNATURE: value}


# ── Filters ─────────────────────────────────────────────────────────────────────


def test_filters_choose_types_prefixes_or_everything_and_never_hold_back_a_test() -> None:
    fiscal = Filters.parse(["invoice.fiscal.*", "invoice.voided"])
    assert fiscal.admits(EventType.INVOICE_FISCAL_ACCEPTED)
    assert fiscal.admits(EventType.INVOICE_VOIDED)
    assert not fiscal.admits(EventType.INVOICE_ISSUED)
    assert fiscal.admits(EventType.WEBHOOK_TEST)
    assert Filters.parse(["*"]).admits(EventType.INVOICE_ISSUED)
    assert set(VERSIONS.values()) == {1}


@pytest.mark.parametrize("text", ["invoice.paid", "payment.*", "invoice", "webhook.test", "*.issued"])
def test_a_filter_that_names_no_public_event_is_refused(text: str) -> None:
    with pytest.raises(InvalidFilterError) as caught:
        Filters.parse([text])
    assert caught.value.kind is ErrorKind.INVALID


def test_an_endpoint_has_one_to_twenty_filters() -> None:
    with pytest.raises(FiltersCountError):
        Filters.parse([])
    with pytest.raises(FiltersCountError):
        Filters.parse([f"invoice.{index}.*" for index in range(MAX_FILTERS + 1)])


# ── Signatures ──────────────────────────────────────────────────────────────────


@given(st.binary(max_size=2048), st.text(min_size=1, max_size=40).filter(str.isprintable))
def test_what_is_signed_verifies_and_any_change_does_not(body: bytes, message_id: str) -> None:
    one = secret()
    stamp = int(NOW.timestamp())
    signed = headers(message_id, stamp, signature(message_id, stamp, body, [one.key]))
    verify([one.text], headers=signed, body=body, now=NOW)
    with pytest.raises(SignatureRefusedError):
        verify([one.text], headers=signed, body=body + b"x", now=NOW)
    with pytest.raises(SignatureRefusedError):
        verify([secret(b"o" * 32).text], headers=signed, body=body, now=NOW)


def test_a_replay_outside_five_minutes_is_refused() -> None:
    one = secret()
    stamp = int(NOW.timestamp())
    signed = headers("msg", stamp, signature("msg", stamp, b"{}", [one.key]))
    verify([one.text], headers=signed, body=b"{}", now=NOW + timedelta(minutes=5))
    with pytest.raises(SignatureRefusedError) as caught:
        verify([one.text], headers=signed, body=b"{}", now=NOW + timedelta(minutes=5, seconds=1))
    assert caught.value.problem is SignatureProblem.STALE
    for stamp_text in ("", "-1", "1e9", "1" * 13):
        with pytest.raises(SignatureRefusedError) as unreadable:
            verify([one.text], headers=signed | {WEBHOOK_TIMESTAMP: stamp_text}, body=b"{}", now=NOW)
        assert unreadable.value.problem is SignatureProblem.TIMESTAMP
    with pytest.raises(SignatureRefusedError) as wrong_key:
        key_of("whsec_not base64!")
    assert wrong_key.value.problem is SignatureProblem.UNREADABLE_KEY


def test_during_a_rotation_both_secrets_sign_and_either_verifies() -> None:
    first, second = secret(b"1" * 32), secret(b"2" * 32)
    ring = SecretRing(first).rotated(second, overlap=DEFAULT_OVERLAP, now=NOW)
    in_force = ring.in_force(NOW)
    assert [each.key for each in in_force] == [second.key, first.key]
    stamp = int(NOW.timestamp())
    signed = headers("msg", stamp, signature("msg", stamp, b"{}", [each.key for each in in_force]))
    assert signed[WEBHOOK_SIGNATURE].count("v1,") == 2
    verify([first.text], headers=signed, body=b"{}", now=NOW)
    verify(
        [second.text],
        headers=signed | {WEBHOOK_SIGNATURE: "v2,x " + signed[WEBHOOK_SIGNATURE]},
        body=b"{}",
        now=NOW,
    )
    assert ring.in_force(NOW + DEFAULT_OVERLAP) == (ring.current,)
    for overlap in (timedelta(minutes=59), timedelta(hours=73)):
        with pytest.raises(OverlapError):
            ring.rotated(secret(), overlap=overlap, now=NOW)


def test_a_secret_is_never_shown_but_as_its_text() -> None:
    one = secret(bytes(range(32)))
    assert one.text.startswith("whsec_")
    assert key_of(one.text) == one.key
    assert "key" not in repr(one)
    assert one.in_force(NOW)


# ── Deliveries ──────────────────────────────────────────────────────────────────


def first_delivery() -> Delivery:
    return Delivery.first(
        delivery=DeliveryId(new_id()),
        endpoint=endpoint().id,
        event=EventId(new_id()),
        event_type=EventType.INVOICE_ISSUED,
        now=NOW,
    )


def attempt(number: int, *, status: int | None = 500, failure: Failure | None = None) -> Attempt:
    return Attempt(
        number=number, at=NOW, duration=timedelta(milliseconds=120), status=status, failure=failure
    )


def test_a_two_hundred_delivers_and_ends_the_delivery() -> None:
    delivered = first_delivery().attempted(attempt(1, status=204), MIDDLE)
    assert (delivered.state, delivered.attempts, delivered.next_attempt_at, delivered.last_status) == (
        DeliveryState.DELIVERED,
        1,
        None,
        204,
    )
    with pytest.raises(DeliveryFinishedError):
        delivered.attempted(attempt(2), MIDDLE)
    with pytest.raises(DeliveryFinishedError):
        delivered.cancelled()


def test_failures_are_retried_with_a_growing_wait_until_the_delivery_is_dead() -> None:
    delivery = first_delivery()
    waits: list[timedelta] = []
    for number in range(1, MAX_ATTEMPTS):
        failure = Failure.TIMEOUT if number % 2 else None
        status = None if failure else 302
        delivery = delivery.attempted(attempt(number, status=status, failure=failure), MIDDLE)
        assert delivery.state is DeliveryState.PENDING
        assert delivery.next_attempt_at is not None
        waits.append(delivery.next_attempt_at - NOW - timedelta(milliseconds=120))
    assert waits[0] == FIRST_WAIT
    assert waits == sorted(waits)
    assert max(waits) == LONGEST_WAIT
    dead = delivery.attempted(attempt(MAX_ATTEMPTS), MIDDLE)
    assert (dead.state, dead.attempts, dead.next_attempt_at) == (DeliveryState.DEAD, MAX_ATTEMPTS, None)


@given(st.integers(min_value=1, max_value=100), st.floats(min_value=0, max_value=0.999999))
def test_every_wait_stays_within_its_jitter(failures: int, draw: float) -> None:
    wait = Schedule(lambda: draw).wait(failures)
    nominal = min(FIRST_WAIT * (1 << min(failures - 1, 16)), LONGEST_WAIT)
    assert nominal * 0.8 <= wait <= nominal * 1.2


def test_a_delivery_of_a_gone_endpoint_is_cancelled() -> None:
    cancelled = first_delivery().cancelled()
    assert (cancelled.state, cancelled.next_attempt_at) == (DeliveryState.CANCELLED, None)


# ── Endpoints ───────────────────────────────────────────────────────────────────


def test_an_endpoint_receives_what_it_chose_while_enabled() -> None:
    chosen = endpoint(Filters.parse(["invoice.voided"]))
    assert chosen.receives(EventType.INVOICE_VOIDED)
    assert not chosen.receives(EventType.INVOICE_ISSUED)
    off = chosen.changed(
        Subscription(target=TARGET, filters=chosen.filters, enabled=False, description="Off"), now=NOW
    )
    assert (off.version, off.description, off.receives(EventType.INVOICE_VOIDED)) == (2, "Off", False)
    rotated = chosen.rotated(secret(b"n" * 32), overlap=DEFAULT_OVERLAP, now=NOW)
    assert (rotated.version, rotated.secrets.current.key) == (2, b"n" * 32)
    removed = chosen.removed(now=NOW)
    assert (removed.deleted, removed.receives(EventType.WEBHOOK_TEST)) == (True, False)
    changes: list[Callable[[], object]] = [
        lambda: removed.changed(Subscription(target=TARGET, filters=chosen.filters), now=NOW),
        lambda: removed.rotated(secret(), overlap=DEFAULT_OVERLAP, now=NOW),
        lambda: removed.removed(now=NOW),
    ]
    for change in changes:
        with pytest.raises(EndpointNotFoundError):
            change()
    assert chosen.tenant == TENANT


# ── Bodies ──────────────────────────────────────────────────────────────────────


def test_a_body_is_compact_sorted_json_with_its_version() -> None:
    public = event(EventType.INVOICE_FISCAL_ACCEPTED)
    body = public.body()
    assert b" " not in body.replace(b"1180.00", b"")
    read = BODY.validate_json(body)
    assert (read["type"], read["version"], read["id"], read["company_id"]) == (
        "invoice.fiscal.accepted",
        1,
        str(public.id),
        str(TENANT.company),
    )
    assert list(read) == sorted(read)


def test_a_body_over_the_bound_is_a_defect_never_sent() -> None:
    huge = PublicEvent(
        id=EventId(new_id()),
        type=EventType.INVOICE_ISSUED,
        occurred_at=NOW,
        company=TENANT.company,
        data={"x": "y" * MAX_PAYLOAD},
    )
    with pytest.raises(PayloadTooLargeError) as caught:
        huge.body()
    assert caught.value.kind is ErrorKind.INTERNAL
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest services/webhooks/tests -q --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'ledger_webhooks.domain.catalog'`.

- [ ] **Step 4: Implement**

`services/webhooks/src/ledger_webhooks/domain/ids.py`:

```python
"""Identifiers of webhooks' own entities; organizations and companies use the kernel's."""

from typing import NewType
from uuid import UUID

EndpointId = NewType("EndpointId", UUID)
DeliveryId = NewType("DeliveryId", UUID)
SecretId = NewType("SecretId", UUID)
EventId = NewType("EventId", UUID)
"""A public event: the id of the internal event it comes from, so that a redelivered event is the same one.
Receivers de-duplicate by it (``webhook-id``)."""
```

`services/webhooks/src/ledger_webhooks/domain/catalog.py`:

```python
"""The public events a webhook can carry, each with its version, and the filters endpoints choose them by.

Public events are the platform's promise to clients, named for what a client cares about
(``invoice.fiscal.accepted``), never for the internal event they come from. Each type has a version: a change
that could break a client's reader (a field removed, renamed or retyped) is a new version, announced as one;
within a version fields are only added. Every type is version 1 today.

An endpoint chooses its events by filters: a type (``invoice.voided``), every type under a prefix
(``invoice.fiscal.*``, ``invoice.*``), or every type (``*``). ``webhook.test`` is never filtered: it is sent
to the endpoint whose test was asked for.
"""

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import ClassVar, Final, final
lazy from collections.abc import Iterable, Mapping

from ledger_kernel.errors import InvalidInputError, excerpt

MAX_FILTERS: Final = 20
EVERY: Final = "*"


class EventType(StrEnum):
    """Every public event."""

    INVOICE_ISSUED = "invoice.issued"
    INVOICE_VOIDED = "invoice.voided"
    INVOICE_FISCAL_NUMBERED = "invoice.fiscal.numbered"
    """The document has its fiscal number: valid to hand to the customer while the tax authority decides."""
    INVOICE_FISCAL_ACCEPTED = "invoice.fiscal.accepted"
    """Accepted by the tax authority (``conditional`` with observations)."""
    INVOICE_FISCAL_REJECTED = "invoice.fiscal.rejected"
    """Rejected by the tax authority, or declined by the fiscal service (``declined``, no fiscal number)."""
    WEBHOOK_TEST = "webhook.test"
    """Sent when a client asks to test an endpoint."""


VERSIONS: Final[Mapping[EventType, int]] = MappingProxyType(dict.fromkeys(EventType, 1))
DESCRIPTIONS: Final[Mapping[EventType, str]] = MappingProxyType(
    {
        EventType.INVOICE_ISSUED: "A document (invoice, credit or debit note) was issued.",
        EventType.INVOICE_VOIDED: "A document was voided.",
        EventType.INVOICE_FISCAL_NUMBERED: "A document has its fiscal number; its authority has not decided.",
        EventType.INVOICE_FISCAL_ACCEPTED: "A document's tax authority accepted it.",
        EventType.INVOICE_FISCAL_REJECTED: "A document was rejected by its authority, or declined.",
        EventType.WEBHOOK_TEST: "A test the client asked for.",
    }
)


class InvalidFilterError(InvalidInputError):
    """A filter that names no public event."""

    code: ClassVar[str] = "webhook_filter_invalid"

    def __init__(self, text: str) -> None:
        super().__init__(f"{excerpt(text)} names no public event: a type, a prefix ending in .*, or *")


class FiltersCountError(InvalidInputError):
    """No filter, or more than ``MAX_FILTERS``."""

    code: ClassVar[str] = "webhook_filters_count"

    def __init__(self) -> None:
        super().__init__(f"an endpoint has 1 to {MAX_FILTERS} filters")


_TYPES: Final = frozenset(each.value for each in EventType)


def _prefix_of(text: str) -> str | None:
    return text.removesuffix("*") if text.endswith(".*") else None


@final
@dataclass(frozen=True, slots=True)
class Filters:
    """The public events an endpoint receives (see the module)."""

    patterns: frozenset[str]

    @classmethod
    def parse(cls, texts: Iterable[str]) -> Filters:
        """Filters from what a client sent; each must name at least one public event."""
        patterns = frozenset(texts)
        if not 1 <= len(patterns) <= MAX_FILTERS:
            raise FiltersCountError
        for text in patterns:
            prefix = _prefix_of(text)
            known = (
                text == EVERY
                or text in _TYPES
                or (prefix is not None and any(each.startswith(prefix) for each in EventType))
            )
            if not known or text == EventType.WEBHOOK_TEST:
                raise InvalidFilterError(text)
        return cls(patterns)

    def admits(self, event: EventType) -> bool:
        """Whether the endpoint receives ``event``; ``webhook.test`` is never filtered."""
        if event is EventType.WEBHOOK_TEST or EVERY in self.patterns or event.value in self.patterns:
            return True
        return any(
            prefix is not None and event.startswith(prefix) for prefix in map(_prefix_of, self.patterns)
        )
```

`services/webhooks/src/ledger_webhooks/domain/targets.py`:

```python
"""Where a webhook may be delivered: a public HTTPS URL, and only public addresses behind it (SSRF guard).

A client names a URL; the platform's servers then connect to it. Left unguarded, that is a way into the
platform's own network (server-side request forgery): a URL naming ``localhost``, a private or link-local
address (``169.254.169.254``, the cloud's instance metadata), or a name whose DNS answers one. Two checks:

* **The URL** (``Target.parse``), when an endpoint is registered or changed: ``https`` (``http`` only where
  the deployment allows it, a laptop's or a test's), a host and no credentials or fragment, at most
  ``MAX_URL`` characters, and no name that is local by definition (``localhost``, ``*.local``,
  ``*.internal``, ``*.home.arpa``) nor an IP literal that is not public.
* **The addresses** (``require_public``), at every attempt, after the name is resolved: every address the
  name resolves to must be public, and the connection is made to one of those, pinned (the HTTP client never
  resolves the name again: a DNS answer that changes between the check and the connection, DNS rebinding,
  cannot move the connection). An address is public when Python's ``is_global`` says so and it is not
  multicast, and an IPv6 address that carries an IPv4 one (mapped ``::ffff:0:0/96``, 6to4 ``2002::/16``,
  NAT64 ``64:ff9b::/96``) is judged by the IPv4 address it carries; Teredo is refused. Checked on CPython
  3.15: ``is_global`` alone accepts multicast (``224.0.0.1``) and NAT64 addresses that carry a loopback one
  (``64:ff9b::7f00:1``).

Errors name what is wrong, never the URL: a client's URL can carry secrets in its path or query.
"""

import ipaddress
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import ClassVar, Final, final
from urllib.parse import urlsplit
lazy from collections.abc import Callable
lazy from urllib.parse import SplitResult

from ledger_kernel.errors import InvalidInputError, UnavailableError

MAX_URL: Final = 2048
LOCAL_NAMES: Final = frozenset({"localhost", "localhost.localdomain"})
LOCAL_SUFFIXES: Final = (".localhost", ".local", ".internal", ".home.arpa", ".localdomain")
_NAT64: Final = ipaddress.IPv6Network("64:ff9b::/96")
_LABEL: Final = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?", re.ASCII)
_DEFAULT_PORTS: Final = {"https": 443, "http": 80}

type Address = ipaddress.IPv4Address | ipaddress.IPv6Address


class TargetProblem(StrEnum):
    """Why a URL is not a webhook target, said without repeating it."""

    TOO_LONG = f"is longer than {MAX_URL} characters"
    SCHEME = "is not https"
    MALFORMED = "is not an absolute URL with a host"
    CREDENTIALS = "carries credentials"
    FRAGMENT = "carries a fragment"
    LOCAL_NAME = "names a host that is local by definition"
    PRIVATE_ADDRESS = "names an address that is not public"


class TargetRefusedError(InvalidInputError):
    """A URL no webhook may be delivered to."""

    code: ClassVar[str] = "webhook_target_refused"

    def __init__(self, problem: TargetProblem) -> None:
        self.problem: TargetProblem = problem
        super().__init__(f"the webhook's URL {problem}")


class AddressRefusedError(UnavailableError):
    """A target's name resolved to an address that is not public: nothing was sent.

    ``UNAVAILABLE``, not ``INVALID``: DNS answers change; the attempt is retried on the delivery's schedule.
    """

    code: ClassVar[str] = "webhook_address_refused"

    def __init__(self) -> None:
        super().__init__("the webhook's host resolved to an address that is not public")


def _carried(address: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    """The IPv4 address an IPv6 one carries, if it is a form that carries one."""
    if address.ipv4_mapped is not None:
        return address.ipv4_mapped
    if address.sixtofour is not None:
        return address.sixtofour
    if address in _NAT64:
        return ipaddress.IPv4Address(int(address) & 0xFFFFFFFF)
    return None


def is_public(address: Address) -> bool:
    """Whether a webhook may be delivered to ``address`` (see the module)."""
    if isinstance(address, ipaddress.IPv6Address):
        if address.teredo is not None:
            return False
        carried = _carried(address)
        if carried is not None:
            return is_public(carried)
    return address.is_global and not address.is_multicast


def require_public(
    addresses: tuple[Address, ...], *, admits: Callable[[Address], bool] = is_public
) -> Address:
    """The address to connect to: the first of ``addresses``, once every one is public (``admits``).

    One private address among public ones refuses them all: a name that answers both is an attack, not a
    misconfiguration. A laptop's or a test's deployment admits more (``allow_private_targets``).
    """
    if not addresses or not all(admits(address) for address in addresses):
        raise AddressRefusedError
    return addresses[0]


def _literal(host: str) -> Address | None:
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


def _name(host: str, *, allow_private: bool) -> str:
    """``host`` as an ASCII name (IDNA), every label well formed; ``TargetRefusedError`` otherwise."""
    try:
        ascii_name = host.encode("idna").decode("ascii")
    except UnicodeError as error:
        raise TargetRefusedError(TargetProblem.MALFORMED) from error
    name = ascii_name.lower().rstrip(".")
    if not name or not all(_LABEL.fullmatch(label) for label in name.split(".")):
        raise TargetRefusedError(TargetProblem.MALFORMED)
    local = name in LOCAL_NAMES or name.endswith(LOCAL_SUFFIXES) or "." not in name
    if local and not allow_private:
        raise TargetRefusedError(TargetProblem.LOCAL_NAME)
    return name


def _split(url: str) -> SplitResult:
    try:
        parts = urlsplit(url)
        _ = parts.port
    except ValueError as error:
        raise TargetRefusedError(TargetProblem.MALFORMED) from error
    return parts


@final
@dataclass(frozen=True, slots=True)
class Target:
    """A URL a webhook may be delivered to: its scheme, host (a name or a public IP), port, and the rest."""

    url: str = field(repr=False)
    scheme: str
    host: str
    port: int
    path: str = field(repr=False)
    """The path and the query, as the request line carries them (``/hooks?x=1``)."""

    @property
    def address(self) -> Address | None:
        """The host as an address, when the URL names one rather than a name."""
        return _literal(self.host)

    @classmethod
    def parse(cls, url: str, *, allow_http: bool = False, allow_private: bool = False) -> Target:
        """Read ``url`` as a target (see the module); ``TargetRefusedError`` when it may not be one.

        ``allow_http`` and ``allow_private`` are a laptop's or a test's: plain HTTP, and private hosts (a
        compose service's name, a private address).
        """
        if len(url) > MAX_URL:
            raise TargetRefusedError(TargetProblem.TOO_LONG)
        parts = _split(url)
        if parts.scheme not in {"https", "http"} or (parts.scheme == "http" and not allow_http):
            raise TargetRefusedError(TargetProblem.SCHEME)
        if not parts.hostname or not parts.netloc:
            raise TargetRefusedError(TargetProblem.MALFORMED)
        if parts.username is not None or parts.password is not None:
            raise TargetRefusedError(TargetProblem.CREDENTIALS)
        if parts.fragment or url.endswith("#"):
            raise TargetRefusedError(TargetProblem.FRAGMENT)
        literal = _literal(parts.hostname)
        if literal is not None and not allow_private and not is_public(literal):
            raise TargetRefusedError(TargetProblem.PRIVATE_ADDRESS)
        host = str(literal) if literal is not None else _name(parts.hostname, allow_private=allow_private)
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        return cls(url, parts.scheme, host, parts.port or _DEFAULT_PORTS[parts.scheme], path)


@final
@dataclass(frozen=True, slots=True)
class TargetPolicy:
    """What a deployment lets endpoints name: only public HTTPS, unless it is a laptop's or a test's."""

    allow_http: bool = False
    allow_private: bool = False

    def parse(self, url: str) -> Target:
        """``url`` as a target under this policy."""
        return Target.parse(url, allow_http=self.allow_http, allow_private=self.allow_private)

    def admits(self, address: Address) -> bool:
        """Whether a delivery may connect to ``address`` under this policy."""
        return self.allow_private or is_public(address)


STRICT: Final = TargetPolicy()
"""Every deployment's but a laptop's or a test's."""
```

`services/webhooks/src/ledger_webhooks/domain/signing.py`:

```python
"""An endpoint's secrets, and the signature every delivery carries: Standard Webhooks v1, HMAC-SHA256.

Every request carries three headers (https://www.standardwebhooks.com, version 1.0.0):

* ``webhook-id``: the public event's id, the same on every attempt (receivers de-duplicate by it);
* ``webhook-timestamp``: when this attempt was signed, in whole seconds since the epoch;
* ``webhook-signature``: ``v1,<base64 HMAC-SHA256 of "{id}.{timestamp}.{body}">``, one per secret in force,
  separated by spaces.

A receiver recomputes the signature with its secret, compares in constant time, and refuses a timestamp more
than ``TOLERANCE`` (5 minutes) from its clock: a request captured and replayed later is refused (§9). Every
attempt is signed anew, so a retry hours later still carries a fresh timestamp. ``verify`` is that check, for
the tests and for clients' documentation; libraries of Standard Webhooks in many languages do the same.

A secret is 32 random bytes, shown to clients as ``whsec_`` + base64 (the standard's form). Rotating gives a
new current secret and keeps the previous one for an overlap (24 hours unless asked otherwise, 1 to 72):
during it every request is signed by both, so a client can switch its verifier at any moment of the overlap.
"""

import base64
import hashlib
import hmac
from dataclasses import dataclass, field, replace
from datetime import timedelta
from enum import StrEnum
from typing import ClassVar, Final, final
lazy from collections.abc import Iterable, Mapping
lazy from datetime import datetime

from ledger_kernel.errors import InvalidInputError
lazy from ledger_webhooks.domain.ids import SecretId

PREFIX: Final = "whsec_"
KEY_BYTES: Final = 32
TOLERANCE: Final = timedelta(minutes=5)
DEFAULT_OVERLAP: Final = timedelta(hours=24)
MIN_OVERLAP: Final = timedelta(hours=1)
MAX_OVERLAP: Final = timedelta(hours=72)
VERSION: Final = "v1"
WEBHOOK_ID: Final = "webhook-id"
WEBHOOK_TIMESTAMP: Final = "webhook-timestamp"
WEBHOOK_SIGNATURE: Final = "webhook-signature"
_MAX_TIMESTAMP_DIGITS: Final = 12


class SignatureProblem(StrEnum):
    """Why a request's signature does not hold."""

    UNREADABLE_KEY = "the secret is not a whsec_ one"
    TIMESTAMP = "it has no readable timestamp"
    STALE = "its timestamp is outside the tolerance"
    MISMATCH = "no signature matches"


class SignatureRefusedError(InvalidInputError):
    """A request whose signature does not hold, or whose timestamp is outside the tolerance."""

    code: ClassVar[str] = "webhook_signature_refused"

    def __init__(self, problem: SignatureProblem) -> None:
        self.problem: SignatureProblem = problem
        super().__init__(f"the webhook's signature does not hold: {problem}")


class OverlapError(InvalidInputError):
    """An overlap for a rotation outside 1 to 72 hours."""

    code: ClassVar[str] = "webhook_overlap_invalid"

    def __init__(self) -> None:
        super().__init__(f"a rotation keeps the previous secret {MIN_OVERLAP} to {MAX_OVERLAP}")


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Secret:
    """One signing key of an endpoint: never in a ``repr``, a log or an error."""

    id: SecretId
    key: bytes = field(repr=False)
    created_at: datetime
    expires_at: datetime | None = None
    """When it stops signing: set when a rotation made it the previous one."""

    @property
    def text(self) -> str:
        """The secret as a client configures its verifier with it: ``whsec_`` + base64."""
        return PREFIX + base64.b64encode(self.key).decode("ascii")

    def in_force(self, now: datetime) -> bool:
        """Whether it still signs at ``now``."""
        return self.expires_at is None or now < self.expires_at


@final
@dataclass(frozen=True, slots=True)
class SecretRing:
    """An endpoint's current secret, and the previous one while its overlap lasts."""

    current: Secret
    previous: Secret | None = None

    def rotated(self, new: Secret, *, overlap: timedelta, now: datetime) -> SecretRing:
        """``new`` as the current secret; the current one signs on for ``overlap`` (1 to 72 hours)."""
        if not MIN_OVERLAP <= overlap <= MAX_OVERLAP:
            raise OverlapError
        return SecretRing(new, replace(self.current, expires_at=now + overlap))

    def in_force(self, now: datetime) -> tuple[Secret, ...]:
        """The secrets that sign at ``now``: the current one first."""
        previous = self.previous
        return (self.current,) if previous is None or not previous.in_force(now) else (self.current, previous)


def _digest(key: bytes, message_id: str, timestamp: int, body: bytes) -> str:
    signed = f"{message_id}.{timestamp}.".encode() + body
    return base64.b64encode(hmac.digest(key, signed, hashlib.sha256)).decode("ascii")


def signature(message_id: str, timestamp: int, body: bytes, keys: Iterable[bytes]) -> str:
    """The ``webhook-signature`` header of a request: one ``v1,<base64>`` per key, separated by spaces."""
    return " ".join(f"{VERSION},{_digest(key, message_id, timestamp, body)}" for key in keys)


def key_of(text: str) -> bytes:
    """The key a ``whsec_`` secret holds (a receiver's side)."""
    try:
        return base64.b64decode(text.removeprefix(PREFIX), validate=True)
    except ValueError as error:
        raise SignatureRefusedError(SignatureProblem.UNREADABLE_KEY) from error


def verify(
    secrets: Iterable[str],
    *,
    headers: Mapping[str, str],
    body: bytes,
    now: datetime,
    tolerance: timedelta = TOLERANCE,
) -> None:
    """A receiver's check of a request (see the module): ``SignatureRefusedError`` unless it holds."""
    message_id = headers.get(WEBHOOK_ID, "")
    stamp = headers.get(WEBHOOK_TIMESTAMP, "")
    if not stamp.isascii() or not stamp.isdigit() or len(stamp) > _MAX_TIMESTAMP_DIGITS:
        raise SignatureRefusedError(SignatureProblem.TIMESTAMP)
    timestamp = int(stamp)
    if abs(now.timestamp() - timestamp) > tolerance.total_seconds():
        raise SignatureRefusedError(SignatureProblem.STALE)
    sent = {
        value
        for version, _, value in (
            part.partition(",") for part in headers.get(WEBHOOK_SIGNATURE, "").split(" ")
        )
        if version == VERSION
    }
    for secret in secrets:
        expected = _digest(key_of(secret), message_id, timestamp, body)
        if any(hmac.compare_digest(expected, each) for each in sent):
            return
    raise SignatureRefusedError(SignatureProblem.MISMATCH)
```

`services/webhooks/src/ledger_webhooks/domain/delivery.py`:

```python
"""One public event on its way to one endpoint: its attempts, their schedule, and how it ends.

::

    pending ──2xx──► delivered
       │  └─anything else─► pending (next attempt after the backoff) ──after MAX_ATTEMPTS──► dead
       └──the endpoint was deleted or disabled──► cancelled

An attempt succeeds on a 2xx answer only; every other answer (a 3xx included: redirects are never followed), a
timeout, a refused connection, an address that is not public, is a failure. Failures are retried with an
exponential backoff: one minute, then twice the wait each time, up to six hours, each wait scaled by a random
factor between 0.8 and 1.2 so that the deliveries an outage held back do not all come at once (``Schedule``).
After ``MAX_ATTEMPTS`` (12: about 25 hours of trying) the delivery is dead: it stays in the log with its
attempts, and nothing is sent again.

What an attempt leaves (``Attempt``) is its number, when, how long, the answer's status or the kind of
failure: never a body, the request's or the response's (a response can echo personal data back, and a log of
bodies is a second copy of every invoice).
"""

from dataclasses import dataclass, replace
from datetime import timedelta
from enum import StrEnum
from typing import ClassVar, Final, final
lazy from collections.abc import Callable
lazy from datetime import datetime

from ledger_kernel.errors import ConflictError, NotFoundError
lazy from ledger_webhooks.domain.catalog import EventType
lazy from ledger_webhooks.domain.ids import DeliveryId, EndpointId, EventId

MAX_ATTEMPTS: Final = 12
FIRST_WAIT: Final = timedelta(minutes=1)
LONGEST_WAIT: Final = timedelta(hours=6)
JITTER: Final = (0.8, 1.2)
_SUCCESS: Final = range(200, 300)
_MAX_DOUBLINGS: Final = 16
"""Past this the wait is long capped at ``LONGEST_WAIT``; the bound keeps the shift small."""

type Random = Callable[[], float]
"""A uniform draw from [0, 1) (``random.random``)."""


class DeliveryState(StrEnum):
    """Where a delivery stands."""

    PENDING = "pending"
    DELIVERED = "delivered"
    DEAD = "dead"
    CANCELLED = "cancelled"


class Failure(StrEnum):
    """Why an attempt got no answer: what the log says instead of a status."""

    TIMEOUT = "timeout"
    CONNECTION = "connection"
    """The connection could not be made, or broke."""
    TLS = "tls"
    """The server's certificate does not hold for its name."""
    DNS = "dns"
    """The name does not resolve."""
    ADDRESS_REFUSED = "address_refused"
    """The name resolved to an address that is not public: nothing was sent."""
    PROTOCOL = "protocol"
    """The answer was not HTTP."""


class DeliveryNotFoundError(NotFoundError):
    """The endpoint has no such delivery."""

    code: ClassVar[str] = "webhook_delivery_not_found"

    def __init__(self) -> None:
        super().__init__("the endpoint has no such delivery")


class DeliveryFinishedError(ConflictError):
    """An attempt recorded on a delivery that has ended: a defect of the caller."""

    code: ClassVar[str] = "delivery_finished"

    def __init__(self) -> None:
        super().__init__("the delivery has ended; nothing more is attempted")


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Attempt:
    """One attempt, as the log keeps it: no body."""

    number: int
    at: datetime
    duration: timedelta
    status: int | None = None
    failure: Failure | None = None

    @property
    def succeeded(self) -> bool:
        """A 2xx answer."""
        return self.status in _SUCCESS


@final
@dataclass(frozen=True, slots=True)
class Schedule:
    """When the attempt after a failure comes (see the module)."""

    random: Random

    def wait(self, failures: int) -> timedelta:
        """How long after the ``failures``-th failure (the first is 1) the next attempt comes."""
        doubled = FIRST_WAIT * (1 << min(failures - 1, _MAX_DOUBLINGS))
        low, high = JITTER
        return min(doubled, LONGEST_WAIT) * (low + (high - low) * self.random())


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Delivery:
    """One public event to one endpoint."""

    id: DeliveryId
    endpoint: EndpointId
    event: EventId
    event_type: EventType
    state: DeliveryState
    attempts: int
    next_attempt_at: datetime | None
    """When the next attempt is due; ``None`` once the delivery has ended."""
    created_at: datetime
    last_status: int | None = None
    last_failure: Failure | None = None

    @classmethod
    def first(
        cls,
        *,
        delivery: DeliveryId,
        endpoint: EndpointId,
        event: EventId,
        event_type: EventType,
        now: datetime,
    ) -> Delivery:
        """A new delivery, due at once."""
        return cls(
            id=delivery,
            endpoint=endpoint,
            event=event,
            event_type=event_type,
            state=DeliveryState.PENDING,
            attempts=0,
            next_attempt_at=now,
            created_at=now,
        )

    def attempted(self, attempt: Attempt, schedule: Schedule) -> Delivery:
        """The delivery after ``attempt``: delivered, due again later, or dead after ``MAX_ATTEMPTS``."""
        if self.state is not DeliveryState.PENDING:
            raise DeliveryFinishedError
        attempts = self.attempts + 1
        recorded = replace(self, attempts=attempts, last_status=attempt.status, last_failure=attempt.failure)
        if attempt.succeeded:
            return replace(recorded, state=DeliveryState.DELIVERED, next_attempt_at=None)
        if attempts >= MAX_ATTEMPTS:
            return replace(recorded, state=DeliveryState.DEAD, next_attempt_at=None)
        return replace(recorded, next_attempt_at=attempt.at + attempt.duration + schedule.wait(attempts))

    def cancelled(self) -> Delivery:
        """The delivery of an endpoint deleted or disabled meanwhile: nothing more is attempted."""
        if self.state is not DeliveryState.PENDING:
            raise DeliveryFinishedError
        return replace(self, state=DeliveryState.CANCELLED, next_attempt_at=None)
```

`services/webhooks/src/ledger_webhooks/domain/endpoint.py`:

```python
"""A company's webhook endpoint: where its events go, which ones, and the secrets they are signed with.

An endpoint belongs to one company (its deliveries carry that company's events only). It is enabled or not; a
deleted endpoint is kept, out of every list, so that its deliveries' log still names it. Every change adds one
to ``version``, the ``ETag`` a change names in ``If-Match``.
"""

from dataclasses import dataclass, field, replace
from typing import ClassVar, Final, final
lazy from datetime import datetime, timedelta

from ledger_kernel.errors import NotFoundError
lazy from ledger_kernel.ids import Tenant
lazy from ledger_webhooks.domain.catalog import EventType, Filters
lazy from ledger_webhooks.domain.ids import EndpointId
lazy from ledger_webhooks.domain.signing import Secret, SecretRing
lazy from ledger_webhooks.domain.targets import Target

MAX_DESCRIPTION: Final = 200


class EndpointNotFoundError(NotFoundError):
    """The company has no such endpoint (or it was deleted)."""

    code: ClassVar[str] = "webhook_not_found"

    def __init__(self) -> None:
        super().__init__("the company has no such webhook endpoint")


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Subscription:
    """What a client says of an endpoint: where, which events, whether it is on, and a note of its own."""

    target: Target
    filters: Filters
    enabled: bool = True
    description: str | None = field(default=None, repr=False)


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Endpoint:
    """Where one company's chosen events go (see the module)."""

    id: EndpointId
    tenant: Tenant
    target: Target
    description: str | None = field(default=None, repr=False)
    filters: Filters
    secrets: SecretRing = field(repr=False)
    created_at: datetime
    updated_at: datetime
    enabled: bool = True
    version: int = 1
    deleted: bool = False

    def receives(self, event: EventType) -> bool:
        """Whether ``event`` goes to the endpoint now: enabled, not deleted, admitted by its filters."""
        return self.enabled and not self.deleted and self.filters.admits(event)

    def changed(self, wanted: Subscription, *, now: datetime) -> Endpoint:
        """The endpoint as a client now wants it."""
        if self.deleted:
            raise EndpointNotFoundError
        return replace(
            self,
            target=wanted.target,
            filters=wanted.filters,
            enabled=wanted.enabled,
            description=wanted.description,
            version=self.version + 1,
            updated_at=now,
        )

    def rotated(self, secret: Secret, *, overlap: timedelta, now: datetime) -> Endpoint:
        """The endpoint signed by ``secret`` from now; the current secret signs on for ``overlap``."""
        if self.deleted:
            raise EndpointNotFoundError
        rotated = self.secrets.rotated(secret, overlap=overlap, now=now)
        return replace(self, secrets=rotated, version=self.version + 1, updated_at=now)

    def removed(self, *, now: datetime) -> Endpoint:
        """The endpoint deleted: no more deliveries, kept for its log."""
        if self.deleted:
            raise EndpointNotFoundError
        return replace(self, deleted=True, enabled=False, version=self.version + 1, updated_at=now)
```

`services/webhooks/src/ledger_webhooks/domain/payloads.py`:

```python
"""A public event's body: compact JSON, bounded.

::

    {
        "id": "...",
        "type": "invoice.fiscal.accepted",
        "version": 1,
        "occurred_at": "2026-10-12T14:30:00+00:00",
        "company_id": "...",
        "data": {...},
    }

The body is what was signed: JSON with sorted keys and no spaces, UTF-8, made once per delivery and never
re-encoded. It is at most ``MAX_PAYLOAD`` bytes: an event's data says what changed and names the document
(clients read the rest from the API), so a larger body is a defect of the translation, refused where it is
made rather than sent.
"""

import json
from dataclasses import dataclass, field
from typing import ClassVar, Final, final
lazy from collections.abc import Mapping
lazy from datetime import datetime

from ledger_kernel.errors import LedgerError
from ledger_webhooks.domain.catalog import VERSIONS
lazy from ledger_kernel.ids import CompanyId
lazy from ledger_webhooks.domain.catalog import EventType
lazy from ledger_webhooks.domain.ids import EventId

MAX_PAYLOAD: Final = 64 * 1024

type Json = str | int | bool | list[Json] | dict[str, Json] | None


class PayloadTooLargeError(LedgerError):
    """A public event whose body would exceed ``MAX_PAYLOAD``: a defect of the translation that made it."""

    code: ClassVar[str] = "webhook_payload_too_large"

    def __init__(self, size: int) -> None:
        super().__init__(f"a webhook body of {size} bytes exceeds {MAX_PAYLOAD}")


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class PublicEvent:
    """What happened, as clients are told."""

    id: EventId
    type: EventType
    occurred_at: datetime
    company: CompanyId
    data: Mapping[str, Json] = field(repr=False)

    @property
    def version(self) -> int:
        """The version of the event's type (``ledger_webhooks.domain.catalog``)."""
        return VERSIONS[self.type]

    def body(self) -> bytes:
        """The body every delivery of the event sends and signs (see the module)."""
        document: dict[str, Json] = {
            "id": str(self.id),
            "type": self.type.value,
            "version": self.version,
            "occurred_at": self.occurred_at.isoformat(),
            "company_id": str(self.company),
            "data": dict(self.data),
        }
        encoded = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        if len(encoded) > MAX_PAYLOAD:
            raise PayloadTooLargeError(len(encoded))
        return encoded
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest services/webhooks/tests -q --no-cov`
Expected: PASS: every private IPv4 refused also inside IPv6's forms (a hypothesis property), signatures verified independently and refused outside 5 minutes, both secrets signing during an overlap, the backoff and its jitter bounded, a payload over its limit refused.

- [ ] **Step 6: Gate**

Run: `uv run poe fmt && uv run poe check`
Expected: every step passes, coverage 100 %.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock services/webhooks
git commit -m "feat(webhooks): the catalog, safe targets, Standard Webhooks signatures, deliveries and their backoff

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: webhooks' use cases: endpoints and their secrets, the fan-out, sending

**Files:**
- Create: `services/webhooks/src/ledger_webhooks/application/__init__.py`, `application/ports.py`, `application/endpoints.py`, `application/fanout.py`, `application/sending.py`
- Modify: `pyproject.toml` (root: "webhooks is hexagonal" `layers = ["application", "domain"]`)
- Test: `services/webhooks/tests/webhook_fakes.py` (support), `services/webhooks/tests/test_application.py`

**Interfaces:**
- Consumes: Task 9's domain; `ledger_kernel.pagination`, `ledger_kernel.clock.Clock`.
- Produces: `ports`: `KeySource`, `Endpoints` (`add`, `get`, `locked`, `update`, `active`, `page`), `Deliveries` (`add -> bool`, `get`, `body`, `update`, `record`, `attempts`, `page`), `Queue` (`schedule`, `drop`), `WebhooksUnit`, `Units` (`unit(tenant)`), `Due(delivery, tenant)`, `DueDeliveries` (`claim(*, now, limit, lease)`), `Sent(status, failure, duration)`, `Sender` (`send(target, body, headers)`). `endpoints`: `Admits`, `StaleVersionError` (`precondition_failed`), `register`, `change`, `rotate`, `remove` (a gone endpoint's pending deliveries cancelled unsent), `endpoint_of`, `send_test` (a `webhook.test` delivery), `endpoints`, `deliveries`, `delivery_of`. `fanout`: `deliver(unit, event, endpoints)`, `fan_out(unit, event)` (one delivery per endpoint that receives it; a redelivered event adds none). `sending`: `CONTENT_TYPE`, `Sending(units, due, sender, schedule, clock, lease, limit, user_agent)` with `pass_once() -> int` (claim, then sign and send outside any transaction, then record). Test support `webhook_fakes`: `MemoryEndpoints`, `MemoryDeliveries`, `MemoryQueue`, `MemoryUnits`, `Request`, `ScriptedSender`, `Clock`.

- [ ] **Step 1: Write the failing tests**

`services/webhooks/tests/webhook_fakes.py`:

```python
"""In-memory stand-ins of webhooks' ports, for the use cases' tests (PostgreSQL's are tested apart)."""

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import final, override
lazy from collections.abc import AsyncGenerator, Mapping
lazy from datetime import datetime, timedelta

from ledger_kernel.pagination import page_of
from ledger_webhooks.application.ports import (
    Deliveries,
    Due,
    DueDeliveries,
    Endpoints,
    Queue,
    Sender,
    Units,
    WebhooksUnit,
)
from ledger_webhooks.domain.delivery import DeliveryState
lazy from ledger_kernel.ids import Tenant
lazy from ledger_kernel.pagination import Page, PageRequest
lazy from ledger_webhooks.application.ports import Sent
lazy from ledger_webhooks.domain.delivery import Attempt, Delivery
lazy from ledger_webhooks.domain.endpoint import Endpoint
lazy from ledger_webhooks.domain.ids import DeliveryId, EndpointId
lazy from ledger_webhooks.domain.targets import Target


@final
class MemoryEndpoints(Endpoints):
    def __init__(self) -> None:
        self.kept: dict[EndpointId, Endpoint] = {}

    @override
    async def add(self, endpoint: Endpoint) -> None:
        self.kept[endpoint.id] = endpoint

    @override
    async def get(self, endpoint: EndpointId) -> Endpoint | None:
        return self.kept.get(endpoint)

    @override
    async def locked(self, endpoint: EndpointId) -> Endpoint | None:
        return self.kept.get(endpoint)

    @override
    async def update(self, endpoint: Endpoint) -> None:
        assert self.kept[endpoint.id].version == endpoint.version - 1
        self.kept[endpoint.id] = endpoint

    @override
    async def active(self) -> tuple[Endpoint, ...]:
        return tuple(each for each in self.kept.values() if each.enabled and not each.deleted)

    @override
    async def page(self, request: PageRequest) -> Page[Endpoint]:
        alive = sorted((each for each in self.kept.values() if not each.deleted), key=lambda each: each.id)
        after = [each for each in alive if request.after is None or each.id > request.after]
        return page_of(after[: request.size + 1], request, key=lambda each: each.id)


@final
class MemoryDeliveries(Deliveries):
    def __init__(self) -> None:
        self.kept: dict[DeliveryId, Delivery] = {}
        self.bodies: dict[DeliveryId, bytes] = {}
        self.log: dict[DeliveryId, list[Attempt]] = {}

    @override
    async def add(self, delivery: Delivery, body: bytes) -> bool:
        if any(
            (each.endpoint, each.event) == (delivery.endpoint, delivery.event) for each in self.kept.values()
        ):
            return False
        self.kept[delivery.id] = delivery
        self.bodies[delivery.id] = body
        return True

    @override
    async def get(self, delivery: DeliveryId) -> Delivery | None:
        return self.kept.get(delivery)

    @override
    async def body(self, delivery: DeliveryId) -> bytes | None:
        return self.bodies.get(delivery)

    @override
    async def update(self, delivery: Delivery) -> None:
        self.kept[delivery.id] = delivery
        if delivery.state is not DeliveryState.PENDING:
            self.bodies.pop(delivery.id, None)

    @override
    async def record(self, delivery: DeliveryId, attempt: Attempt) -> None:
        self.log.setdefault(delivery, []).append(attempt)

    @override
    async def attempts(self, delivery: DeliveryId) -> tuple[Attempt, ...]:
        return tuple(self.log.get(delivery, []))

    @override
    async def page(self, endpoint: EndpointId, request: PageRequest) -> Page[Delivery]:
        mine = sorted(
            (each for each in self.kept.values() if each.endpoint == endpoint), key=lambda each: each.id
        )
        after = [each for each in mine if request.after is None or each.id > request.after]
        return page_of(after[: request.size + 1], request, key=lambda each: each.id)


@final
@dataclass
class Entry:
    tenant: Tenant
    due_at: datetime
    leased_until: datetime | None = None


@final
class MemoryQueue(Queue, DueDeliveries):
    """The queue of every company: written in a company's unit, claimed across companies."""

    def __init__(self) -> None:
        self.entries: dict[DeliveryId, Entry] = {}
        self.tenant: Tenant | None = None

    @override
    async def schedule(self, delivery: DeliveryId, at: datetime) -> None:
        assert self.tenant is not None
        self.entries[delivery] = Entry(self.tenant, at)

    @override
    async def drop(self, delivery: DeliveryId) -> None:
        self.entries.pop(delivery, None)

    @override
    async def claim(self, *, now: datetime, limit: int, lease: timedelta) -> tuple[Due, ...]:
        due = [
            (delivery, entry)
            for delivery, entry in sorted(self.entries.items(), key=lambda item: item[1].due_at)
            if entry.due_at <= now and (entry.leased_until is None or entry.leased_until <= now)
        ][:limit]
        for _, entry in due:
            entry.leased_until = now + lease
        return tuple(Due(delivery, entry.tenant) for delivery, entry in due)


@final
class MemoryUnits(Units):
    """Units over in-memory stores per company, sharing one queue (nothing is rolled back)."""

    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self.endpoints: dict[Tenant, MemoryEndpoints] = {}
        self.deliveries: dict[Tenant, MemoryDeliveries] = {}
        self.queue = MemoryQueue()

    @override
    @asynccontextmanager
    async def unit(self, tenant: Tenant) -> AsyncGenerator[WebhooksUnit]:
        self.queue.tenant = tenant
        yield WebhooksUnit(
            tenant=tenant,
            endpoints=self.endpoints.setdefault(tenant, MemoryEndpoints()),
            deliveries=self.deliveries.setdefault(tenant, MemoryDeliveries()),
            queue=self.queue,
            now=self.clock(),
        )


@final
@dataclass(frozen=True, slots=True)
class Request:
    target: Target
    body: bytes
    headers: Mapping[str, str]


@final
@dataclass
class ScriptedSender(Sender):
    """A network that answers each request with the next of ``answers`` (then the last one again)."""

    answers: list[Sent]
    sent: list[Request] = field(default_factory=list[Request])

    @override
    async def send(self, target: Target, body: bytes, headers: Mapping[str, str]) -> Sent:
        self.sent.append(Request(target, body, dict(headers)))
        return self.answers[min(len(self.sent), len(self.answers)) - 1]


@final
class Clock:
    """A clock the test moves."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, by: timedelta) -> None:
        self.now += by
```

`services/webhooks/tests/test_application.py`:

```python
from datetime import timedelta
from typing import Final, final, override
lazy from collections.abc import Mapping

import pytest

from ledger_kernel.ids import new_id
from ledger_kernel.pagination import PageRequest
from ledger_webhooks.application import endpoints
from ledger_webhooks.application.endpoints import StaleVersionError
from ledger_webhooks.application.fanout import fan_out
from ledger_webhooks.application.ports import Sender, Sent
from ledger_webhooks.application.sending import Sending
from ledger_webhooks.domain.catalog import EventType, Filters
from ledger_webhooks.domain.delivery import (
    MAX_ATTEMPTS,
    DeliveryNotFoundError,
    DeliveryState,
    Failure,
    Schedule,
)
from ledger_webhooks.domain.endpoint import EndpointNotFoundError, Subscription
from ledger_webhooks.domain.ids import DeliveryId, EndpointId
from ledger_webhooks.domain.signing import DEFAULT_OVERLAP, verify
lazy from ledger_webhooks.domain.delivery import Delivery
lazy from ledger_webhooks.domain.endpoint import Endpoint
lazy from ledger_webhooks.domain.targets import Target

from webhook_fakes import Clock, MemoryUnits, ScriptedSender
from webhook_samples import EVERYTHING, NOW, OTHER_TENANT, TARGET, TENANT, event

OK: Final = Sent(status=204, failure=None, duration=timedelta(milliseconds=40))
DOWN: Final = Sent(status=None, failure=Failure.CONNECTION, duration=timedelta(milliseconds=5))
KEYS: Final = iter(bytes([n]) * 32 for n in range(256))


def anything(version: int) -> bool:
    del version
    return True


def nothing(version: int) -> bool:
    del version
    return False


def key() -> bytes:
    return next(KEYS)


def sending(units: MemoryUnits, sender: ScriptedSender) -> Sending:
    return Sending(
        units=units,
        due=units.queue,
        sender=sender,
        schedule=Schedule(lambda: 0.5),
        clock=units.clock,
        lease=timedelta(seconds=60),
        limit=20,
        user_agent="ledger-webhooks/0.1.0",
    )


async def registered(units: MemoryUnits, filters: Filters = EVERYTHING) -> Endpoint:
    async with units.unit(TENANT) as unit:
        return await endpoints.register(unit, Subscription(target=TARGET, filters=filters), keys=key)


async def test_an_endpoint_is_registered_changed_rotated_and_removed_at_its_versions() -> None:
    units = MemoryUnits(Clock(NOW))
    endpoint = await registered(units)
    wanted = Subscription(
        target=TARGET, filters=Filters.parse(["invoice.*"]), enabled=False, description="ERP"
    )
    async with units.unit(TENANT) as unit:
        with pytest.raises(StaleVersionError):
            await endpoints.change(unit, endpoint.id, wanted, admits=nothing)
        changed = await endpoints.change(unit, endpoint.id, wanted, admits=anything)
        rotated = await endpoints.rotate(
            unit, endpoint.id, overlap=DEFAULT_OVERLAP, keys=key, admits=anything
        )
        removed = await endpoints.remove(unit, endpoint.id, admits=anything)
    assert (changed.version, changed.enabled, changed.description) == (2, False, "ERP")
    assert rotated.secrets.previous is not None
    assert rotated.secrets.previous.key == endpoint.secrets.current.key
    assert removed.deleted
    async with units.unit(TENANT) as unit:
        for missing in (endpoint.id, EndpointId(new_id())):
            with pytest.raises(EndpointNotFoundError):
                await endpoints.endpoint_of(unit, missing)
            with pytest.raises(EndpointNotFoundError):
                await endpoints.remove(unit, missing, admits=anything)
        assert (await endpoints.endpoints(unit, PageRequest())).items == ()


async def test_an_event_reaches_every_endpoint_of_the_company_that_receives_it_once() -> None:
    units = MemoryUnits(Clock(NOW))
    everything = await registered(units)
    voided_only = await registered(units, Filters.parse(["invoice.voided"]))
    issued = event(EventType.INVOICE_ISSUED)
    async with units.unit(TENANT) as unit:
        made = await fan_out(unit, issued)
        again = await fan_out(unit, issued)
    assert [each.endpoint for each in made] == [everything.id]
    assert again == ()
    async with units.unit(OTHER_TENANT) as unit:
        assert await fan_out(unit, event(EventType.INVOICE_ISSUED, tenant=OTHER_TENANT)) == ()
    assert voided_only.receives(EventType.INVOICE_VOIDED)
    assert list(units.queue.entries) == [made[0].id]


async def test_a_delivery_is_signed_with_every_secret_in_force_and_delivered() -> None:
    clock = Clock(NOW)
    units, sender = MemoryUnits(clock), ScriptedSender([OK])
    endpoint = await registered(units)
    async with units.unit(TENANT) as unit:
        rotated = await endpoints.rotate(
            unit, endpoint.id, overlap=DEFAULT_OVERLAP, keys=key, admits=anything
        )
        (delivery,) = await fan_out(unit, event())
    assert await sending(units, sender).pass_once() == 1
    (request,) = sender.sent
    assert request.target == TARGET
    assert request.headers["webhook-id"] == str(delivery.event)
    assert request.headers["webhook-signature"].count("v1,") == 2
    for secret in (rotated.secrets.current, endpoint.secrets.current):
        verify([secret.text], headers=dict(request.headers), body=request.body, now=NOW)
    async with units.unit(TENANT) as unit:
        done, attempts = await endpoints.delivery_of(unit, endpoint.id, delivery.id)
    assert (done.state, done.attempts, done.last_status) == (DeliveryState.DELIVERED, 1, 204)
    assert [(each.number, each.status) for each in attempts] == [(1, 204)]
    assert units.queue.entries == {}
    assert units.deliveries[TENANT].bodies == {}


async def test_failures_are_retried_on_the_schedule_until_the_delivery_is_dead() -> None:
    clock = Clock(NOW)
    units, sender = MemoryUnits(clock), ScriptedSender([DOWN])
    endpoint = await registered(units)
    async with units.unit(TENANT) as unit:
        (delivery,) = await fan_out(unit, event())
    work = sending(units, sender)
    assert await work.pass_once() == 1
    assert await work.pass_once() == 0
    for _ in range(MAX_ATTEMPTS - 1):
        clock.advance(timedelta(hours=8))
        assert await work.pass_once() == 1
    async with units.unit(TENANT) as unit:
        dead, attempts = await endpoints.delivery_of(unit, endpoint.id, delivery.id)
    assert (dead.state, dead.attempts, dead.last_failure) == (
        DeliveryState.DEAD,
        MAX_ATTEMPTS,
        Failure.CONNECTION,
    )
    assert len(attempts) == MAX_ATTEMPTS
    assert units.queue.entries == {}


async def test_a_claimed_delivery_is_not_claimed_again_while_its_lease_holds() -> None:
    clock = Clock(NOW)
    units = MemoryUnits(clock)
    await registered(units)
    async with units.unit(TENANT) as unit:
        await fan_out(unit, event())
    lease = timedelta(seconds=60)
    assert len(await units.queue.claim(now=NOW, limit=5, lease=lease)) == 1
    assert await units.queue.claim(now=NOW + timedelta(seconds=59), limit=5, lease=lease) == ()
    assert len(await units.queue.claim(now=NOW + lease, limit=5, lease=lease)) == 1


async def test_a_delivery_of_an_endpoint_gone_meanwhile_is_cancelled_unsent() -> None:
    units, sender = MemoryUnits(Clock(NOW)), ScriptedSender([OK])
    endpoint = await registered(units)
    async with units.unit(TENANT) as unit:
        (delivery,) = await fan_out(unit, event())
        await endpoints.remove(unit, endpoint.id, admits=anything)
    assert await sending(units, sender).pass_once() == 1
    assert sender.sent == []
    async with units.unit(TENANT) as unit:
        cancelled = await unit.deliveries.get(delivery.id)
    assert cancelled is not None
    assert cancelled.state is DeliveryState.CANCELLED


async def test_a_due_entry_whose_delivery_has_ended_is_dropped() -> None:
    units, sender = MemoryUnits(Clock(NOW)), ScriptedSender([OK])
    await registered(units)
    async with units.unit(TENANT) as unit:
        (delivery,) = await fan_out(unit, event())
        await unit.deliveries.update(delivery.cancelled())
        await unit.queue.schedule(DeliveryId(new_id()), NOW)
    assert await sending(units, sender).pass_once() == 2
    assert (sender.sent, units.queue.entries) == ([], {})


@final
class EndingMeanwhile(Sender):
    """A network during which the delivery ends another way (its endpoint's lease expired, say)."""

    def __init__(self, units: MemoryUnits, delivery: Delivery) -> None:
        self.units = units
        self.delivery = delivery

    @override
    async def send(self, target: Target, body: bytes, headers: Mapping[str, str]) -> Sent:
        async with self.units.unit(TENANT) as unit:
            await unit.deliveries.update(self.delivery.cancelled())
        return OK


async def test_a_delivery_that_ended_while_it_was_sent_is_left_as_it_ended() -> None:
    units = MemoryUnits(Clock(NOW))
    await registered(units)
    async with units.unit(TENANT) as unit:
        (delivery,) = await fan_out(unit, event())
    work = sending(units, ScriptedSender([OK]))
    racing = Sending(
        units=units,
        due=units.queue,
        sender=EndingMeanwhile(units, delivery),
        schedule=work.schedule,
        clock=work.clock,
        lease=work.lease,
        limit=work.limit,
        user_agent=work.user_agent,
    )
    assert await racing.pass_once() == 1
    async with units.unit(TENANT) as unit:
        ended = await unit.deliveries.get(delivery.id)
        assert await unit.deliveries.attempts(delivery.id) == ()
    assert ended is not None
    assert ended.state is DeliveryState.CANCELLED


async def test_a_test_reaches_its_endpoint_whatever_its_filters() -> None:
    units, sender = MemoryUnits(Clock(NOW)), ScriptedSender([OK])
    endpoint = await registered(units, Filters.parse(["invoice.voided"]))
    async with units.unit(TENANT) as unit:
        test = await endpoints.send_test(unit, endpoint.id)
    assert test.event_type is EventType.WEBHOOK_TEST
    await sending(units, sender).pass_once()
    (request,) = sender.sent
    assert b'"webhook.test"' in request.body
    async with units.unit(TENANT) as unit:
        page = await endpoints.deliveries(unit, endpoint.id, PageRequest())
        assert [each.id for each in page.items] == [test.id]
        with pytest.raises(DeliveryNotFoundError):
            await endpoints.delivery_of(unit, EndpointId(new_id()), test.id)
        with pytest.raises(DeliveryNotFoundError):
            await endpoints.delivery_of(unit, endpoint.id, DeliveryId(new_id()))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest services/webhooks/tests/test_application.py -q --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'ledger_webhooks.application'`.

- [ ] **Step 3: Implement**

`services/webhooks/src/ledger_webhooks/application/__init__.py`:

```python
"""webhooks' use cases and the ports they need; the domain and the kernel only."""
```

`services/webhooks/src/ledger_webhooks/application/ports.py`:

```python
"""What webhooks' use cases need from the world, as protocols the adapters implement.

A unit of work is one transaction of one company: its endpoints, its deliveries and their queue entries change
together. ``DueDeliveries`` is the one port that reads across companies: it hands the sending worker the
deliveries that are due, with their company, and leases them so that one worker sends each. ``Sender`` is the
network: it is called outside any unit.
"""

from abc import ABCMeta, abstractmethod
from dataclasses import dataclass
from typing import Protocol, final
lazy from collections.abc import Callable, Mapping
lazy from contextlib import AbstractAsyncContextManager
lazy from datetime import datetime, timedelta

lazy from ledger_kernel.ids import Tenant
lazy from ledger_kernel.pagination import Page, PageRequest
lazy from ledger_webhooks.domain.delivery import Attempt, Delivery, Failure
lazy from ledger_webhooks.domain.endpoint import Endpoint
lazy from ledger_webhooks.domain.ids import DeliveryId, EndpointId
lazy from ledger_webhooks.domain.targets import Target

type KeySource = Callable[[], bytes]
"""Makes a new secret's key: 32 bytes from the operating system's random source (``secrets.token_bytes``)."""


class Endpoints(Protocol, metaclass=ABCMeta):
    """The webhook endpoints of the unit's company."""

    @abstractmethod
    async def add(self, endpoint: Endpoint) -> None:
        """Keep a new endpoint."""

    @abstractmethod
    async def get(self, endpoint: EndpointId) -> Endpoint | None:
        """One of the company's endpoints, deleted ones included, or ``None``."""

    @abstractmethod
    async def locked(self, endpoint: EndpointId) -> Endpoint | None:
        """One of the company's endpoints, locked until the unit ends, or ``None``."""

    @abstractmethod
    async def update(self, endpoint: Endpoint) -> None:
        """Keep ``endpoint`` in place of the version before it."""

    @abstractmethod
    async def active(self) -> tuple[Endpoint, ...]:
        """The company's endpoints that are enabled and not deleted."""

    @abstractmethod
    async def page(self, request: PageRequest) -> Page[Endpoint]:
        """The company's endpoints that are not deleted, oldest first."""


class Deliveries(Protocol, metaclass=ABCMeta):
    """The deliveries of the unit's company, the bodies of those pending, and their attempts."""

    @abstractmethod
    async def add(self, delivery: Delivery, body: bytes) -> bool:
        """Keep a new delivery and its body; false, keeping nothing, when the endpoint has that event."""

    @abstractmethod
    async def get(self, delivery: DeliveryId) -> Delivery | None:
        """One of the company's deliveries, or ``None``."""

    @abstractmethod
    async def body(self, delivery: DeliveryId) -> bytes | None:
        """What a pending delivery sends; ``None`` once it has ended (bodies are not kept past their end)."""

    @abstractmethod
    async def update(self, delivery: Delivery) -> None:
        """Keep ``delivery`` as it now stands; its body is dropped once it has ended."""

    @abstractmethod
    async def record(self, delivery: DeliveryId, attempt: Attempt) -> None:
        """Log ``attempt`` of ``delivery``: no body, ever."""

    @abstractmethod
    async def attempts(self, delivery: DeliveryId) -> tuple[Attempt, ...]:
        """The attempts of ``delivery``, first first."""

    @abstractmethod
    async def page(self, endpoint: EndpointId, request: PageRequest) -> Page[Delivery]:
        """The deliveries of ``endpoint``, oldest first."""


class Queue(Protocol, metaclass=ABCMeta):
    """When each pending delivery of the unit's company is due."""

    @abstractmethod
    async def schedule(self, delivery: DeliveryId, at: datetime) -> None:
        """``delivery`` is due at ``at`` (its lease, if any, ends)."""

    @abstractmethod
    async def drop(self, delivery: DeliveryId) -> None:
        """``delivery`` has ended: it is due no more."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class WebhooksUnit:
    """One unit of work for one company."""

    tenant: Tenant
    endpoints: Endpoints
    deliveries: Deliveries
    queue: Queue
    now: datetime


class Units(Protocol, metaclass=ABCMeta):
    """Where units of work come from."""

    @abstractmethod
    def unit(self, tenant: Tenant) -> AbstractAsyncContextManager[WebhooksUnit]:
        """A unit of work for ``tenant``'s company: it commits when the block ends normally."""


@final
@dataclass(frozen=True, slots=True)
class Due:
    """A delivery the sending worker holds the lease of, and its company."""

    delivery: DeliveryId
    tenant: Tenant


class DueDeliveries(Protocol, metaclass=ABCMeta):
    """The deliveries due, across companies (no business data: ids and times)."""

    @abstractmethod
    async def claim(self, *, now: datetime, limit: int, lease: timedelta) -> tuple[Due, ...]:
        """Up to ``limit`` deliveries due at ``now`` that no lease holds, each leased for ``lease``."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Sent:
    """What one request came to: a status, or the kind of failure; and how long it took."""

    status: int | None
    failure: Failure | None
    duration: timedelta


class Sender(Protocol, metaclass=ABCMeta):
    """The network, as the sending worker uses it."""

    @abstractmethod
    async def send(self, target: Target, body: bytes, headers: Mapping[str, str]) -> Sent:
        """``POST`` ``body`` to ``target``; a network failure is a ``Sent``, never an exception."""
```

`services/webhooks/src/ledger_webhooks/application/fanout.py`:

```python
"""A public event fanned out: one delivery per endpoint of the company that receives it, due at once.

It runs in the unit of the consumer that read the internal event (its inbox claim commits with it). A delivery
is unique per endpoint and event: an event read twice makes no second delivery, whatever the inbox did. The
body is made once, here, and kept with the delivery until it ends: every attempt sends the same bytes.
"""

lazy from collections.abc import Iterable

from ledger_kernel.ids import new_id
from ledger_webhooks.domain.delivery import Delivery
from ledger_webhooks.domain.ids import DeliveryId
lazy from ledger_webhooks.application.ports import WebhooksUnit
lazy from ledger_webhooks.domain.endpoint import Endpoint
lazy from ledger_webhooks.domain.payloads import PublicEvent


async def deliver(
    unit: WebhooksUnit, event: PublicEvent, endpoints: Iterable[Endpoint]
) -> tuple[Delivery, ...]:
    """A delivery of ``event`` to each of ``endpoints`` that has none yet, due now."""
    body = event.body()
    made: list[Delivery] = []
    for endpoint in endpoints:
        delivery = Delivery.first(
            delivery=DeliveryId(new_id()),
            endpoint=endpoint.id,
            event=event.id,
            event_type=event.type,
            now=unit.now,
        )
        if await unit.deliveries.add(delivery, body):
            await unit.queue.schedule(delivery.id, unit.now)
            made.append(delivery)
    return tuple(made)


async def fan_out(unit: WebhooksUnit, event: PublicEvent) -> tuple[Delivery, ...]:
    """``event`` delivered to every endpoint of the company that receives it now."""
    receiving = [endpoint for endpoint in await unit.endpoints.active() if endpoint.receives(event.type)]
    return await deliver(unit, event, receiving)
```

`services/webhooks/src/ledger_webhooks/application/endpoints.py`:

```python
"""webhooks' use cases for a company's endpoints: register, change, rotate the secret, remove, test, read.

A change names the version it applies to (``If-Match``): ``StaleVersionError`` when the endpoint has moved on.
A deleted endpoint is not found by any of them.
"""

from typing import ClassVar
lazy from collections.abc import Callable
lazy from datetime import timedelta

from ledger_kernel.errors import ConflictError
from ledger_kernel.ids import new_id
from ledger_webhooks.application.fanout import deliver
from ledger_webhooks.domain.catalog import EventType
from ledger_webhooks.domain.delivery import DeliveryNotFoundError
from ledger_webhooks.domain.endpoint import Endpoint, EndpointNotFoundError
from ledger_webhooks.domain.ids import EndpointId, EventId, SecretId
from ledger_webhooks.domain.payloads import PublicEvent
from ledger_webhooks.domain.signing import Secret, SecretRing
lazy from ledger_kernel.pagination import Page, PageRequest
lazy from ledger_webhooks.application.ports import KeySource, WebhooksUnit
lazy from ledger_webhooks.domain.delivery import Attempt, Delivery
lazy from ledger_webhooks.domain.endpoint import Subscription
lazy from ledger_webhooks.domain.ids import DeliveryId

type Admits = Callable[[int], bool]
"""Whether a change may apply to an endpoint at this version (what ``If-Match`` allowed)."""


class StaleVersionError(ConflictError):
    """The endpoint is no longer at a version the change was allowed to apply to (``If-Match``)."""

    code: ClassVar[str] = "precondition_failed"

    def __init__(self) -> None:
        super().__init__("the endpoint is not at the version If-Match names")


def _secret(unit: WebhooksUnit, keys: KeySource) -> Secret:
    return Secret(id=SecretId(new_id()), key=keys(), created_at=unit.now)


async def register(unit: WebhooksUnit, wanted: Subscription, *, keys: KeySource) -> Endpoint:
    """A new endpoint of the unit's company, as ``wanted``, with a new secret."""
    endpoint = Endpoint(
        id=EndpointId(new_id()),
        tenant=unit.tenant,
        target=wanted.target,
        filters=wanted.filters,
        enabled=wanted.enabled,
        description=wanted.description,
        secrets=SecretRing(_secret(unit, keys)),
        created_at=unit.now,
        updated_at=unit.now,
    )
    await unit.endpoints.add(endpoint)
    return endpoint


async def _current(unit: WebhooksUnit, endpoint: EndpointId, admits: Admits) -> Endpoint:
    found = await unit.endpoints.locked(endpoint)
    if found is None or found.deleted:
        raise EndpointNotFoundError
    if not admits(found.version):
        raise StaleVersionError
    return found


async def change(
    unit: WebhooksUnit, endpoint: EndpointId, wanted: Subscription, *, admits: Admits
) -> Endpoint:
    """The endpoint as a client now wants it."""
    changed = (await _current(unit, endpoint, admits)).changed(wanted, now=unit.now)
    await unit.endpoints.update(changed)
    return changed


async def rotate(
    unit: WebhooksUnit, endpoint: EndpointId, *, overlap: timedelta, keys: KeySource, admits: Admits
) -> Endpoint:
    """A new secret for the endpoint; the current one signs on for ``overlap``."""
    rotated = (await _current(unit, endpoint, admits)).rotated(
        _secret(unit, keys), overlap=overlap, now=unit.now
    )
    await unit.endpoints.update(rotated)
    return rotated


async def remove(unit: WebhooksUnit, endpoint: EndpointId, *, admits: Admits) -> Endpoint:
    """Delete the endpoint: its pending deliveries are cancelled when they fall due."""
    removed = (await _current(unit, endpoint, admits)).removed(now=unit.now)
    await unit.endpoints.update(removed)
    return removed


async def endpoint_of(unit: WebhooksUnit, endpoint: EndpointId) -> Endpoint:
    """One of the company's endpoints that is not deleted."""
    found = await unit.endpoints.get(endpoint)
    if found is None or found.deleted:
        raise EndpointNotFoundError
    return found


async def send_test(unit: WebhooksUnit, endpoint: EndpointId) -> Delivery:
    """A ``webhook.test`` event, delivered to the endpoint alone, as soon as the sending worker takes it."""
    found = await endpoint_of(unit, endpoint)
    event = PublicEvent(
        id=EventId(new_id()),
        type=EventType.WEBHOOK_TEST,
        occurred_at=unit.now,
        company=unit.tenant.company,
        data={"endpoint_id": str(found.id), "message": "A test of this endpoint, as the client asked."},
    )
    (delivery,) = await deliver(unit, event, (found,))
    return delivery


async def endpoints(unit: WebhooksUnit, request: PageRequest) -> Page[Endpoint]:
    """A page of the company's endpoints."""
    return await unit.endpoints.page(request)


async def deliveries(unit: WebhooksUnit, endpoint: EndpointId, request: PageRequest) -> Page[Delivery]:
    """A page of an endpoint's deliveries, oldest first."""
    await endpoint_of(unit, endpoint)
    return await unit.deliveries.page(endpoint, request)


async def delivery_of(
    unit: WebhooksUnit, endpoint: EndpointId, delivery: DeliveryId
) -> tuple[Delivery, tuple[Attempt, ...]]:
    """One delivery of an endpoint, with its attempts."""
    found = await unit.deliveries.get(delivery)
    if found is None or found.endpoint != endpoint:
        raise DeliveryNotFoundError
    return found, await unit.deliveries.attempts(delivery)
```

`services/webhooks/src/ledger_webhooks/application/sending.py`:

```python
"""Sending what is due: one pass of the sending worker.

A pass claims up to ``limit`` due deliveries across companies, each leased so that no other worker takes it,
and sends them concurrently. Each is read in its company's unit (the delivery, its endpoint, its body), sent
outside any unit, and recorded in another: the attempt logged, the delivery delivered, due again after its
backoff, or dead. A delivery whose endpoint was deleted or disabled meanwhile is cancelled unsent.

Every attempt is signed when it is sent (``webhook-timestamp`` is that moment) with every secret in force. A
worker that dies between sending and recording leaves the lease to expire: the delivery is sent again, which
is why receivers de-duplicate by ``webhook-id`` (delivery is at least once, as every webhook's is).
"""

import asyncio
from dataclasses import dataclass
from typing import Final, final
lazy from collections.abc import Mapping
lazy from datetime import timedelta

from ledger_webhooks.domain.delivery import Attempt, DeliveryState
from ledger_webhooks.domain.signing import WEBHOOK_ID, WEBHOOK_SIGNATURE, WEBHOOK_TIMESTAMP, signature
lazy from ledger_kernel.clock import Clock
lazy from ledger_webhooks.application.ports import Due, DueDeliveries, Sender, Units, WebhooksUnit
lazy from ledger_webhooks.domain.delivery import Delivery, Schedule
lazy from ledger_webhooks.domain.endpoint import Endpoint

CONTENT_TYPE: Final = "application/json"


@final
@dataclass(frozen=True, slots=True)
class _Ready:
    delivery: Delivery
    endpoint: Endpoint
    body: bytes


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Sending:
    """What a pass of the sending worker works with."""

    units: Units
    due: DueDeliveries
    sender: Sender
    schedule: Schedule
    clock: Clock
    lease: timedelta
    limit: int
    user_agent: str

    async def pass_once(self) -> int:
        """Send what is due now, at most ``limit`` deliveries; how many were claimed."""
        claimed = await self.due.claim(now=self.clock(), limit=self.limit, lease=self.lease)
        await asyncio.gather(*(self._deliver(each) for each in claimed))
        return len(claimed)

    async def _ready(self, unit: WebhooksUnit, due: Due) -> _Ready | None:
        delivery = await unit.deliveries.get(due.delivery)
        if delivery is None or delivery.state is not DeliveryState.PENDING:
            await unit.queue.drop(due.delivery)
            return None
        endpoint = await unit.endpoints.get(delivery.endpoint)
        body = await unit.deliveries.body(delivery.id)
        if endpoint is None or body is None or not endpoint.receives(delivery.event_type):
            await unit.deliveries.update(delivery.cancelled())
            await unit.queue.drop(delivery.id)
            return None
        return _Ready(delivery, endpoint, body)

    def _headers(self, ready: _Ready) -> Mapping[str, str]:
        now = self.clock()
        timestamp = int(now.timestamp())
        message = str(ready.delivery.event)
        keys = [secret.key for secret in ready.endpoint.secrets.in_force(now)]
        return {
            WEBHOOK_ID: message,
            WEBHOOK_TIMESTAMP: str(timestamp),
            WEBHOOK_SIGNATURE: signature(message, timestamp, ready.body, keys),
            "Content-Type": CONTENT_TYPE,
            "User-Agent": self.user_agent,
        }

    async def _deliver(self, due: Due) -> None:
        async with self.units.unit(due.tenant) as unit:
            ready = await self._ready(unit, due)
        if ready is None:
            return
        at = self.clock()
        sent = await self.sender.send(ready.endpoint.target, ready.body, self._headers(ready))
        attempt = Attempt(
            number=ready.delivery.attempts + 1,
            at=at,
            duration=sent.duration,
            status=sent.status,
            failure=sent.failure,
        )
        async with self.units.unit(due.tenant) as unit:
            current = await unit.deliveries.get(ready.delivery.id)
            if current is None or current.state is not DeliveryState.PENDING:
                return
            attempted = current.attempted(attempt, self.schedule)
            await unit.deliveries.record(attempted.id, attempt)
            await unit.deliveries.update(attempted)
            if attempted.next_attempt_at is None:
                await unit.queue.drop(attempted.id)
            else:
                await unit.queue.schedule(attempted.id, attempted.next_attempt_at)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest services/webhooks/tests -q --no-cov`
Expected: PASS.

- [ ] **Step 5: Gate**

Run: `uv run poe fmt && uv run poe check`
Expected: every step passes, coverage 100 %.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml services/webhooks
git commit -m "feat(webhooks): endpoints, rotations, the fan-out and sending outside any transaction

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: webhooks' adapters: sealing secrets, sending over a pinned connection, invoicing's events in public words

**Files:**
- Create: `services/webhooks/src/ledger_webhooks/adapters/__init__.py`, `adapters/sealing.py`, `adapters/network.py`, `adapters/translation.py`
- Modify: `services/webhooks/pyproject.toml`, `pyproject.toml` (root: "webhooks is hexagonal" `layers = ["adapters", "application", "domain"]`)
- Test: `services/webhooks/tests/test_sealing.py`, `services/webhooks/tests/test_network.py`, `services/webhooks/tests/test_translation.py`

**Interfaces:**
- Consumes: Tasks 2, 9, 10; `cryptography`'s `AESGCM`; `httpx`; `Envelope`; `ledger.events.invoicing.v1`, `ledger.invoicing.v1`.
- Produces: `sealing`: `NONCE`, `KEY_BYTES`, `SealingError`, `KeyringError`, `Keyring` (`of(encoded, current)`, `seal(secret, *, bound_to)`, `open(sealed, *, bound_to)`). `network`: `resolve(host, port)`, `client(*, connect, total)` (no keep-alive, no redirects, `trust_env=False`), `host_header(target)`, `HttpSender(client, *, resolve, admits, clock)` (`send`): resolves once, checks every address, connects to the checked one with the name only in `Host` and SNI (`sni_hostname`); a refusal, a timeout or a status is a `Sent` with its `Failure`, never an exception. `translation`: `KINDS`, `public_event(envelope) -> PublicEvent | None` (`invoice.*` in public words; an event of another kind is not public).

Re-check before starting: Task 2's generated modules' names (`ledger.events.invoicing.v1.events_pb2`, `ledger.invoicing.v1.document_pb2`) and `Envelope.unwrap`/`Envelope.wrap` as landed.

- [ ] **Step 1: Dependencies**

`services/webhooks/pyproject.toml` — `dependencies` gains `"ledger-contracts"`, `"cryptography>=50.0.2"` and `"httpx>=0.28.1"` (`ledger-contracts = { workspace = true }`), and:

```toml
[tool.deptry.package_module_name_map]
"ledger-contracts" = ["ledger_contracts", "ledger"]
```

Run: `uv lock && uv sync`

- [ ] **Step 2: Write the failing tests**

`services/webhooks/tests/test_sealing.py`:

```python
import base64
from typing import Final

import pytest

from ledger_kernel.errors import ErrorKind
from ledger_webhooks.adapters.sealing import Keyring, KeyringError, SealingError

OLD: Final = base64.b64encode(b"o" * 32).decode()
NEW: Final = base64.b64encode(b"n" * 32).decode()
OWNER: Final = b"endpoint+secret"


def test_a_secret_opens_where_it_was_sealed_and_nowhere_else() -> None:
    ring = Keyring.of({"k1": OLD}, "k1")
    sealed = ring.seal(b"s" * 32, bound_to=OWNER)
    assert sealed.startswith("k1:")
    assert ring.open(sealed, bound_to=OWNER) == b"s" * 32
    assert ring.seal(b"s" * 32, bound_to=OWNER) != sealed
    with pytest.raises(SealingError) as caught:
        ring.open(sealed, bound_to=b"another endpoint")
    assert caught.value.kind is ErrorKind.INTERNAL


def test_rotating_the_keyring_keeps_every_secret_readable() -> None:
    sealed = Keyring.of({"k1": OLD}, "k1").seal(b"s" * 32, bound_to=OWNER)
    rotated = Keyring.of({"k1": OLD, "k2": NEW}, "k2")
    assert rotated.open(sealed, bound_to=OWNER) == b"s" * 32
    assert rotated.seal(b"t" * 32, bound_to=OWNER).startswith("k2:")


@pytest.mark.parametrize(
    "sealed",
    ["k9:AAAA", "k1:not base64!", "k1:" + base64.b64encode(b"x" * 40).decode(), "no-colon"],
    ids=["unknown-key", "not-base64", "altered", "unreadable"],
)
def test_what_was_not_sealed_by_the_ring_does_not_open(sealed: str) -> None:
    with pytest.raises(SealingError):
        Keyring.of({"k1": OLD}, "k1").open(sealed, bound_to=OWNER)


@pytest.mark.parametrize(
    ("keys", "current"),
    [({"k1": OLD}, "k2"), ({"k1": base64.b64encode(b"short").decode()}, "k1"), ({"k1": "not base64!"}, "k1")],
    ids=["no-current", "short-key", "not-base64"],
)
def test_a_keyring_that_cannot_work_is_refused(keys: dict[str, str], current: str) -> None:
    with pytest.raises(KeyringError):
        Keyring.of(keys, current)
```

`services/webhooks/tests/test_network.py`:

```python
import asyncio
import ipaddress
import socket
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Final, final
lazy from collections.abc import AsyncGenerator

import pytest

from ledger_webhooks.adapters import network
from ledger_webhooks.adapters.network import HttpSender, resolve
from ledger_webhooks.domain.delivery import Failure
from ledger_webhooks.domain.targets import Target, TargetPolicy
lazy from ledger_webhooks.domain.targets import Address

LOOPBACK: Final = ipaddress.ip_address("127.0.0.1")
LOCAL: Final = TargetPolicy(allow_http=True, allow_private=True)
BODY: Final = b'{"type":"webhook.test"}'


@final
@dataclass
class Receiver:
    """A minimal HTTP server: what it was sent, and the answer it gives."""

    answer: bytes = b"HTTP/1.1 204 No Content\r\nContent-Length: 0\r\n\r\n"
    requests: list[bytes] = field(default_factory=list[bytes])
    silent: bool = False

    async def serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = await reader.readuntil(b"\r\n\r\n")
        length = next(
            (
                int(line.split(b":")[1])
                for line in head.split(b"\r\n")
                if line.lower().startswith(b"content-length")
            ),
            0,
        )
        self.requests.append(head + await reader.readexactly(length))
        if self.silent:
            await asyncio.sleep(5)
        writer.write(self.answer)
        await writer.drain()
        writer.close()


@asynccontextmanager
async def served(receiver: Receiver) -> AsyncGenerator[int]:
    server = await asyncio.start_server(receiver.serve, "127.0.0.1", 0)
    async with server:
        yield server.sockets[0].getsockname()[1]


def fixed(*addresses: Address) -> network.Resolve:
    async def answer(host: str, port: int) -> tuple[Address, ...]:
        del host, port
        return addresses

    return answer


async def failing(host: str, port: int) -> tuple[Address, ...]:
    del host, port
    raise socket.gaierror(socket.EAI_NONAME, "unknown")


async def test_a_request_goes_to_the_checked_address_under_the_target_s_name() -> None:
    receiver = Receiver()
    async with served(receiver) as port, network.client(connect=2, total=2) as client:
        sender = HttpSender(client, resolve=fixed(LOOPBACK), admits=LOCAL.admits)
        target = LOCAL.parse(f"http://hooks.example.com:{port}/ledger?x=1")
        sent = await sender.send(target, BODY, {"webhook-id": "e1", "Content-Type": "application/json"})
    assert (sent.status, sent.failure) == (204, None)
    assert sent.duration.total_seconds() >= 0
    (request,) = receiver.requests
    assert request.startswith(b"POST /ledger?x=1 HTTP/1.1\r\n")
    assert f"host: hooks.example.com:{port}".encode() in request.lower()
    assert b"webhook-id: e1" in request.lower()
    assert request.endswith(BODY)


async def test_a_redirect_is_never_followed() -> None:
    receiver = Receiver(
        answer=b"HTTP/1.1 302 Found\r\nLocation: http://169.254.169.254/\r\nContent-Length: 0\r\n\r\n"
    )
    async with served(receiver) as port, network.client(connect=2, total=2) as client:
        sender = HttpSender(client, resolve=fixed(LOOPBACK), admits=LOCAL.admits)
        sent = await sender.send(LOCAL.parse(f"http://hooks.example.com:{port}/"), BODY, {})
    assert (sent.status, len(receiver.requests)) == (302, 1)


async def test_a_name_that_resolves_to_a_private_address_is_never_connected_to() -> None:
    receiver = Receiver()
    async with served(receiver) as port, network.client(connect=2, total=2) as client:
        rebinding = HttpSender(client, resolve=fixed(ipaddress.ip_address("93.184.216.34"), LOOPBACK))
        sent = await rebinding.send(Target.parse(f"https://hooks.example.com:{port}/"), BODY, {})
    assert (sent.status, sent.failure, receiver.requests) == (None, Failure.ADDRESS_REFUSED, [])


@pytest.mark.parametrize(
    ("receiver", "url", "resolver", "failure", "patience"),
    [
        (Receiver(), "http://hooks.example.com:{port}/", failing, Failure.DNS, 0.5),
        (Receiver(silent=True), "http://hooks.example.com:{port}/", fixed(LOOPBACK), Failure.TIMEOUT, 0.5),
        (
            Receiver(answer=b"garbage\r\n\r\n"),
            "http://hooks.example.com:{port}/",
            fixed(LOOPBACK),
            Failure.PROTOCOL,
            0.5,
        ),
        (Receiver(), "https://hooks.example.com:{port}/", fixed(LOOPBACK), Failure.TLS, 0.5),
        (Receiver(), "http://hooks.example.com:1/", fixed(LOOPBACK), Failure.CONNECTION, 5.0),
    ],
    ids=["dns", "timeout", "protocol", "tls", "refused"],
)
async def test_every_network_failure_is_an_attempt_s_failure_never_an_exception(
    receiver: Receiver, url: str, resolver: network.Resolve, failure: Failure, patience: float
) -> None:
    async with served(receiver) as port, network.client(connect=patience, total=patience) as client:
        sender = HttpSender(client, resolve=resolver, admits=LOCAL.admits)
        sent = await sender.send(LOCAL.parse(url.format(port=port)), BODY, {})
    assert (sent.status, sent.failure) == (None, failure)


async def test_an_address_in_the_url_is_not_resolved() -> None:
    receiver = Receiver()
    async with served(receiver) as port, network.client(connect=2, total=2) as client:
        sender = HttpSender(client, resolve=failing, admits=LOCAL.admits)
        sent = await sender.send(LOCAL.parse(f"http://127.0.0.1:{port}/"), BODY, {})
    assert sent.status == 204
    assert f"host: 127.0.0.1:{port}".encode() in receiver.requests[0].lower()


async def test_the_system_resolver_answers_each_address_once() -> None:
    answered = await resolve("localhost", 443)
    assert answered
    assert len(set(answered)) == len(answered)
    assert all(address.is_loopback for address in answered)


def test_the_host_header_of_an_ipv6_target_on_its_default_port() -> None:
    target = Target.parse("https://[2606:4700::1111]/x")
    assert network.host_header(target) == "[2606:4700::1111]"
```

`services/webhooks/tests/test_translation.py`:

```python
import pytest
lazy from google.protobuf.message import Message

import ledger.events.invoicing.v1.events_pb2 as wire
import ledger.invoicing.v1.document_pb2 as document
from ledger.common.v1.money_pb2 import Money
from ledger_contracts.envelope import Envelope
from ledger_kernel.ids import new_id
from ledger_webhooks.adapters.translation import public_event
from ledger_webhooks.domain.catalog import EventType

from webhook_samples import TENANT

DOCUMENT = str(new_id())
MESSAGES = [wire.FiscalMessage(code="613", text="Observación")]


@pytest.mark.parametrize(
    ("message", "kind", "data"),
    [
        (
            wire.DocumentIssued(
                document=document.CommercialDocument(
                    document_id=DOCUMENT,
                    kind=document.DOCUMENT_KIND_INVOICE,
                    version=2,
                    issue_date="2026-10-12",
                    currency="DOP",
                    totals=document.Totals(total=Money(amount="1180.00", currency="DOP")),
                    customer=document.Party(tax_id="101850043", name="Distribuidora"),
                )
            ),
            EventType.INVOICE_ISSUED,
            {
                "invoice": {
                    "id": DOCUMENT,
                    "kind": "invoice",
                    "version": 2,
                    "issue_date": "2026-10-12",
                    "currency": "DOP",
                    "total": "1180.00",
                    "external_reference": None,
                }
            },
        ),
        (
            wire.DocumentVoided(
                document_id=DOCUMENT, kind=document.DOCUMENT_KIND_CREDIT_NOTE, version=3, reason="Dup"
            ),
            EventType.INVOICE_VOIDED,
            {"invoice": {"id": DOCUMENT, "kind": "credit_note", "version": 3}, "reason": "Dup"},
        ),
        (
            wire.DocumentFiscallyNumbered(
                document_id=DOCUMENT,
                kind=document.DOCUMENT_KIND_DEBIT_NOTE,
                version=3,
                fiscal_number="E330000000001",
            ),
            EventType.INVOICE_FISCAL_NUMBERED,
            {
                "invoice": {"id": DOCUMENT, "kind": "debit_note", "version": 3},
                "fiscal_number": "E330000000001",
            },
        ),
        (
            wire.DocumentFiscallyAccepted(
                document_id=DOCUMENT,
                kind=document.DOCUMENT_KIND_INVOICE,
                version=4,
                fiscal_number="E310000000001",
                conditional=True,
                messages=MESSAGES,
            ),
            EventType.INVOICE_FISCAL_ACCEPTED,
            {
                "invoice": {"id": DOCUMENT, "kind": "invoice", "version": 4},
                "fiscal_number": "E310000000001",
                "conditional": True,
                "messages": [{"code": "613", "text": "Observación"}],
            },
        ),
        (
            wire.DocumentFiscallyRejected(
                document_id=DOCUMENT, kind=document.DOCUMENT_KIND_INVOICE, version=4, declined=True
            ),
            EventType.INVOICE_FISCAL_REJECTED,
            {
                "invoice": {"id": DOCUMENT, "kind": "invoice", "version": 4},
                "fiscal_number": None,
                "declined": True,
                "messages": [],
            },
        ),
    ],
    ids=["issued", "voided", "numbered", "accepted", "declined"],
)
def test_each_invoicing_event_is_told_as_its_public_event(
    message: Message, kind: EventType, data: object
) -> None:
    envelope = Envelope.wrap(message, source="invoicing", tenant=TENANT)
    told = public_event(envelope)
    assert told is not None
    assert (told.id, told.type, told.occurred_at, told.company, told.data) == (
        envelope.id,
        kind,
        envelope.time,
        TENANT.company,
        data,
    )
    assert b"101850043" not in told.body()


def test_what_clients_are_not_told_of_is_none() -> None:
    other = Envelope.wrap(wire.FiscalMessage(code="x"), source="invoicing", tenant=TENANT)
    assert public_event(other) is None
    unscoped = Envelope.wrap(wire.DocumentVoided(document_id=DOCUMENT), source="invoicing", tenant=None)
    assert public_event(unscoped) is None
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest services/webhooks/tests/test_sealing.py services/webhooks/tests/test_network.py services/webhooks/tests/test_translation.py -q --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'ledger_webhooks.adapters'`.

- [ ] **Step 4: Implement**

`services/webhooks/src/ledger_webhooks/adapters/__init__.py`:

```python
"""webhooks' adapters: sealing, the network, invoicing's events translated, PostgreSQL."""
```

`services/webhooks/src/ledger_webhooks/adapters/sealing.py`:

```python
"""Endpoint secrets sealed at rest: AES-256-GCM under a key of the service's keyring, bound to their owner.

A secret must be read back to sign (HMAC needs the key itself), so it is encrypted, not hashed. The keyring
(``LEDGER_SECRET_KEYS``, from the cloud's secret manager) names each key; new secrets are sealed under the
current one (``LEDGER_SECRET_KEY_ID``) and every key that sealed something stays in the ring until a
re-sealing job retires it, so rotating the keyring never loses a secret. The associated data binds a sealed
secret to its endpoint and its own id: a ciphertext copied onto another row does not open.

Sealed form: ``<key id>:<base64(12-byte nonce + ciphertext and tag)>``. Neither the keys nor what they open
appear in a ``repr``, a log or an error.
"""

import base64
import os
from typing import ClassVar, Final, final
lazy from collections.abc import Mapping

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ledger_kernel.errors import LedgerError

NONCE: Final = 12
KEY_BYTES: Final = 32


class SealingError(LedgerError):
    """A sealed secret that does not open (unknown key, altered, moved): a defect or tampering."""

    code: ClassVar[str] = "secret_unsealable"

    def __init__(self) -> None:
        super().__init__("a sealed endpoint secret does not open")


class KeyringError(LedgerError):
    """A keyring that cannot work: no current key, or a key that is not 32 bytes of base64."""

    code: ClassVar[str] = "secret_keyring_invalid"

    def __init__(self) -> None:
        super().__init__("the secret keyring needs its current key, and every key 32 bytes in base64")


@final
class Keyring:
    """The service's sealing keys (see the module)."""

    __slots__ = ("_current", "_keys")

    def __init__(self, keys: Mapping[str, bytes], current: str) -> None:
        if current not in keys or any(len(key) != KEY_BYTES for key in keys.values()):
            raise KeyringError
        self._keys = {name: AESGCM(key) for name, key in keys.items()}
        self._current = current

    @classmethod
    def of(cls, encoded: Mapping[str, str], current: str) -> Keyring:
        """A keyring from its keys as base64 text (as settings hold them)."""
        try:
            keys = {name: base64.b64decode(text, validate=True) for name, text in encoded.items()}
        except ValueError as error:
            raise KeyringError from error
        return cls(keys, current)

    def seal(self, secret: bytes, *, bound_to: bytes) -> str:
        """``secret`` sealed under the current key, bound to ``bound_to``."""
        nonce = os.urandom(NONCE)
        sealed = self._keys[self._current].encrypt(nonce, secret, bound_to)
        return f"{self._current}:{base64.b64encode(nonce + sealed).decode('ascii')}"

    def open(self, sealed: str, *, bound_to: bytes) -> bytes:
        """What ``sealed`` holds, if it was sealed by a key of the ring for ``bound_to``."""
        name, _, text = sealed.partition(":")
        key = self._keys.get(name)
        if key is None:
            raise SealingError
        try:
            raw = base64.b64decode(text, validate=True)
            return key.decrypt(raw[:NONCE], raw[NONCE:], bound_to)
        except (ValueError, InvalidTag) as error:
            raise SealingError from error
```

`services/webhooks/src/ledger_webhooks/adapters/network.py`:

```python
"""Deliveries over HTTP, pinned to the addresses checked (SSRF and DNS rebinding defence) — httpx.

For each attempt the target's name is resolved once (``resolve``), every address is checked
(``ledger_webhooks.domain.targets.require_public``), and the request goes to the first one: the URL names the
address, the ``Host`` header and the TLS server name (``sni_hostname``, which httpcore also verifies the
certificate against) name the host. The HTTP client never resolves the name itself, so a DNS answer that
changes after the check cannot redirect the connection. Connections are never kept alive: a pooled connection
is keyed by its address, and one opened for one name must not carry a request for another name on the same
address. Redirects are never followed (a 3xx is a failed attempt), environment proxies are ignored, and the
answer's body is never read: the status is all a delivery keeps.

Every network failure becomes a ``Sent`` with its kind; nothing here raises for the network.
"""

import asyncio
import ipaddress
import socket
import ssl
import time
from datetime import timedelta
from typing import Final, final, override
lazy from collections.abc import Awaitable, Callable, Mapping

import httpx

from ledger_webhooks.application.ports import Sender, Sent
from ledger_webhooks.domain.delivery import Failure
from ledger_webhooks.domain.targets import AddressRefusedError, is_public, require_public
lazy from ledger_kernel.clock import MonotonicClock
lazy from ledger_webhooks.domain.targets import Address, Target

type Resolve = Callable[[str, int], Awaitable[tuple[Address, ...]]]
"""Every address a host name resolves to, for a port."""

_DEFAULT_PORTS: Final = {"https": 443, "http": 80}


async def resolve(host: str, port: int) -> tuple[Address, ...]:
    """Every address ``host`` resolves to (the system's resolver), each once, in the order it answered."""
    answers = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return tuple(dict.fromkeys(ipaddress.ip_address(answer[4][0]) for answer in answers))


def client(*, connect: float, total: float) -> httpx.AsyncClient:
    """The HTTP client deliveries go through: no keep-alive, no redirects, no environment proxies."""
    return httpx.AsyncClient(
        timeout=httpx.Timeout(total, connect=connect),
        limits=httpx.Limits(max_keepalive_connections=0),
        follow_redirects=False,
        trust_env=False,
    )


def _origin(address: Address) -> str:
    return f"[{address}]" if isinstance(address, ipaddress.IPv6Address) else str(address)


def host_header(target: Target) -> str:
    """The ``Host`` header of a request to ``target``: its host, and its port unless the scheme's own."""
    host = f"[{target.host}]" if ":" in target.host else target.host
    return host if target.port == _DEFAULT_PORTS[target.scheme] else f"{host}:{target.port}"


def _failure_of(error: Exception) -> Failure:
    cause: BaseException | None = error
    while cause is not None:
        if isinstance(cause, ssl.SSLError):
            return Failure.TLS
        cause = cause.__cause__ or cause.__context__
    match error:
        case httpx.TimeoutException():
            return Failure.TIMEOUT
        case httpx.RemoteProtocolError() | httpx.LocalProtocolError():
            return Failure.PROTOCOL
        case _:
            return Failure.CONNECTION


@final
class HttpSender(Sender):
    """Deliveries over HTTP (see the module); ``admits`` decides which addresses are public enough."""

    __slots__ = ("_admits", "_client", "_clock", "_resolve")

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        resolve: Resolve = resolve,
        admits: Callable[[Address], bool] = is_public,
        clock: MonotonicClock = time.monotonic,
    ) -> None:
        self._client = client
        self._resolve = resolve
        self._admits = admits
        self._clock = clock

    async def _address(self, target: Target) -> Address:
        literal = target.address
        addresses = (literal,) if literal is not None else await self._resolve(target.host, target.port)
        return require_public(addresses, admits=self._admits)

    def _sent(self, started: float, *, status: int | None = None, failure: Failure | None = None) -> Sent:
        elapsed = max(self._clock() - started, 0.0)
        return Sent(status=status, failure=failure, duration=timedelta(seconds=elapsed))

    @override
    async def send(self, target: Target, body: bytes, headers: Mapping[str, str]) -> Sent:
        started = self._clock()
        try:
            address = await self._address(target)
        except AddressRefusedError:
            return self._sent(started, failure=Failure.ADDRESS_REFUSED)
        except OSError:
            return self._sent(started, failure=Failure.DNS)
        url = f"{target.scheme}://{_origin(address)}:{target.port}{target.path}"
        extensions = {"sni_hostname": target.host} if target.address is None else {}
        try:
            async with self._client.stream(
                "POST",
                url,
                content=body,
                headers={**headers, "Host": host_header(target)},
                extensions=extensions,
            ) as response:
                return self._sent(started, status=response.status_code)
        except httpx.TransportError as error:
            return self._sent(started, failure=_failure_of(error))
```

`services/webhooks/src/ledger_webhooks/adapters/translation.py`:

```python
"""invoicing's events, as clients are told them: the layer between the wire and the public API.

Each internal event (``ledger.events.invoicing.v1``) becomes one public event, versioned, whose id is the
internal event's (a redelivered event is the same public event). Its data names the document and says what
changed; it carries no line, no customer and no tax id (clients read those from the API with their own
permissions, and a webhook endpoint is a third party's server):

* ``invoice.issued``: ``{"invoice": {id, kind, version, issue_date, currency, total, external_reference}}``;
* ``invoice.voided``: the invoice's ``id``, ``kind``, ``version``, and the ``reason``;
* ``invoice.fiscal.numbered``: the same, and the ``fiscal_number``;
* ``invoice.fiscal.accepted``: ``fiscal_number``, ``conditional``, the authority's ``messages``;
* ``invoice.fiscal.rejected``: ``fiscal_number`` (``null`` when ``declined``), ``declined``, ``messages``.
"""

from typing import Final
lazy from collections.abc import Iterable

import ledger.events.invoicing.v1.events_pb2 as wire
import ledger.invoicing.v1.document_pb2 as document
from ledger_webhooks.domain.catalog import EventType
from ledger_webhooks.domain.ids import EventId
from ledger_webhooks.domain.payloads import PublicEvent
lazy from ledger_contracts.envelope import Envelope
lazy from ledger_webhooks.domain.payloads import Json

KINDS: Final = {
    document.DOCUMENT_KIND_INVOICE: "invoice",
    document.DOCUMENT_KIND_CREDIT_NOTE: "credit_note",
    document.DOCUMENT_KIND_DEBIT_NOTE: "debit_note",
}


def _invoice(document_id: str, kind: document.DocumentKind.ValueType, version: int) -> dict[str, Json]:
    return {"id": document_id, "kind": KINDS.get(kind, "unknown"), "version": version}


def _messages(messages: Iterable[wire.FiscalMessage]) -> list[Json]:
    return [{"code": message.code, "text": message.text} for message in messages]


def _data(envelope: Envelope) -> tuple[EventType, dict[str, Json]] | None:
    match envelope.type:
        case wire.DocumentIssued.DESCRIPTOR.full_name:
            issued = envelope.unwrap(wire.DocumentIssued).document
            invoice = _invoice(issued.document_id, issued.kind, issued.version) | {
                "issue_date": issued.issue_date,
                "currency": issued.currency,
                "total": issued.totals.total.amount,
                "external_reference": issued.external_reference or None,
            }
            return EventType.INVOICE_ISSUED, {"invoice": invoice}
        case wire.DocumentVoided.DESCRIPTOR.full_name:
            voided = envelope.unwrap(wire.DocumentVoided)
            invoice = _invoice(voided.document_id, voided.kind, voided.version)
            return EventType.INVOICE_VOIDED, {"invoice": invoice, "reason": voided.reason}
        case wire.DocumentFiscallyNumbered.DESCRIPTOR.full_name:
            numbered = envelope.unwrap(wire.DocumentFiscallyNumbered)
            invoice = _invoice(numbered.document_id, numbered.kind, numbered.version)
            return EventType.INVOICE_FISCAL_NUMBERED, {
                "invoice": invoice,
                "fiscal_number": numbered.fiscal_number,
            }
        case wire.DocumentFiscallyAccepted.DESCRIPTOR.full_name:
            accepted = envelope.unwrap(wire.DocumentFiscallyAccepted)
            return EventType.INVOICE_FISCAL_ACCEPTED, {
                "invoice": _invoice(accepted.document_id, accepted.kind, accepted.version),
                "fiscal_number": accepted.fiscal_number,
                "conditional": accepted.conditional,
                "messages": _messages(accepted.messages),
            }
        case wire.DocumentFiscallyRejected.DESCRIPTOR.full_name:
            rejected = envelope.unwrap(wire.DocumentFiscallyRejected)
            return EventType.INVOICE_FISCAL_REJECTED, {
                "invoice": _invoice(rejected.document_id, rejected.kind, rejected.version),
                "fiscal_number": rejected.fiscal_number or None,
                "declined": rejected.declined,
                "messages": _messages(rejected.messages),
            }
        case _:
            return None


def public_event(envelope: Envelope) -> PublicEvent | None:
    """The public event an internal one is told as; ``None`` for one clients are not told of."""
    tenant = envelope.tenant
    translated = _data(envelope)
    if tenant is None or translated is None:
        return None
    kind, data = translated
    return PublicEvent(
        id=EventId(envelope.id), type=kind, occurred_at=envelope.time, company=tenant.company, data=data
    )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest services/webhooks/tests -q --no-cov`
Expected: PASS: a sealed secret opens only bound to its own endpoint and id, and under a rotated keyring; a private answer among public ones refuses them all and nothing is sent; the request reaches the checked address carrying the name in `Host`; a redirect is a failure, not followed; invoicing's events read in public words. (On Windows, a refused connection takes about 2 seconds to fail: the per-case timeout of `test_network.py` allows it.)

- [ ] **Step 6: Gate**

Run: `uv run poe fmt && uv run poe check`
Expected: every step passes, coverage 100 %.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock services/webhooks
git commit -m "feat(webhooks): sealed secrets, a pinned and checked connection, public events

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: webhooks' settings, schema, migrations and state in PostgreSQL

**Files:**
- Create: `services/webhooks/src/ledger_webhooks/settings.py`, `adapters/postgres/__init__.py`, `adapters/postgres/tables.py`, `adapters/postgres/repositories.py`, `adapters/postgres/units.py`, `adapters/postgres/migrate.py`, `adapters/postgres/migrations/__init__.py`, `adapters/postgres/migrations/env.py`, `adapters/postgres/migrations/versions/__init__.py`, `adapters/postgres/migrations/versions/r0001_webhooks.py`
- Modify: `services/webhooks/pyproject.toml`, `services/webhooks/tests/webhook_samples.py`, `pyproject.toml` (root)
- Test: `services/webhooks/tests/conftest.py`, `services/webhooks/tests/test_migrations.py`, `services/webhooks/tests/test_repositories.py`

**Interfaces:**
- Consumes: Tasks 9-11; `ApiSettings`; `Migrations`, `run_migrations`, `BASE`, `HEAD`, `SchemaNotCurrentError` (M3 Task 3); `tenant_isolation`, `Database`, `UnitOfWork`; `CHASSIS_POLICIES`, `CHASSIS_METADATA`; `Postgres`, `restored_logging`.
- Produces: `settings`: `ADDRESS`, `WebhooksProblem`, `WebhooksSettings` (`identity_address`, `secret_keys`, `secret_key_id`, `allow_http_targets`, `allow_private_targets`, `delivery_batch`, `delivery_poll_seconds`, `delivery_connect_seconds`, `delivery_timeout_seconds`, `delivery_lease_seconds`; unsafe targets beyond a laptop, a keyring without its current key and a lease not longer than an attempt refused at start). `tables`: `WebhooksTable`, `EndpointRow` (`endpoints`), `DeliveryRow` (`deliveries`, `ck_deliveries_body`), `AttemptRow` (`delivery_attempts`), `QueueRow` (`delivery_queue`, outside row-level security). `repositories`: `SealedSecret`, `LostUpdateError`, `PgEndpoints`, `PgDeliveries`, `PgQueue`, `PgDueDeliveries` (`claim`: `FOR UPDATE SKIP LOCKED` with a lease, across companies). `units`: `UnscopedUnitError`, `Storage(keyring, policy)`, `bind(unit, storage, *, clock)`, `PgUnits`. `migrate`: `SCRIPTS`, `MIGRATIONS`, `upgrade`, `downgrade`, `head`, `require_head`, `main` (console script `ledger-webhooks-migrate`). Revision `0001`. Test support `webhook_samples` gains `KEY` and `STORAGE`.

Re-check before starting: as Task 5 (`ledger_platform.migrations`, the chassis's tables as `ledger_platform.schema` declares them).

- [ ] **Step 1: Dependencies and layers**

`services/webhooks/pyproject.toml` — `dependencies` gains `"ledger-platform"`, `"alembic>=1.20.0"`, `"pydantic>=2.14.1"` and `"sqlalchemy>=2.1.4"` (`ledger-platform = { workspace = true }`), and:

```toml
[project.scripts]
ledger-webhooks-migrate = "ledger_webhooks.adapters.postgres.migrate:main"
```

Root `pyproject.toml`: "webhooks is hexagonal" `layers = ["adapters", "settings", "application", "domain"]`; `[tool.coverage.run] source` gains `"services/webhooks/src/ledger_webhooks/adapters/postgres/migrations"` (Decision 20). Run: `uv lock && uv sync`

- [ ] **Step 2: Write the failing tests**

`services/webhooks/tests/webhook_samples.py` becomes:

```python
"""What the webhooks tests share: a company, a moment, secrets, an endpoint, a public event."""

import base64
from datetime import UTC, datetime
from typing import Final

from ledger_kernel.ids import CompanyId, OrganizationId, Tenant, new_id
from ledger_webhooks.adapters.postgres.units import Storage
from ledger_webhooks.adapters.sealing import Keyring
from ledger_webhooks.domain.catalog import EventType, Filters
from ledger_webhooks.domain.endpoint import Endpoint
from ledger_webhooks.domain.ids import EndpointId, EventId, SecretId
from ledger_webhooks.domain.payloads import PublicEvent
from ledger_webhooks.domain.signing import Secret, SecretRing
from ledger_webhooks.domain.targets import Target, TargetPolicy

KEY: Final = base64.b64encode(b"t" * 32).decode()
"""The tests' sealing key, ``t1``."""
STORAGE: Final = Storage(Keyring.of({"t1": KEY}, "t1"), TargetPolicy(allow_http=True, allow_private=True))
TENANT: Final = Tenant(OrganizationId(new_id()), CompanyId(new_id()))
OTHER_TENANT: Final = Tenant(TENANT.organization, CompanyId(new_id()))
NOW: Final = datetime(2026, 10, 12, 14, 30, tzinfo=UTC)
TARGET: Final = Target.parse("https://hooks.example.com/ledger")
EVERYTHING: Final = Filters.parse(["*"])


def secret(key: bytes = b"k" * 32, *, now: datetime = NOW) -> Secret:
    """A secret of ``key``."""
    return Secret(id=SecretId(new_id()), key=key, created_at=now)


def endpoint(filters: Filters = EVERYTHING, *, tenant: Tenant = TENANT, target: Target = TARGET) -> Endpoint:
    """A new endpoint of ``tenant``'s, receiving ``filters``."""
    return Endpoint(
        id=EndpointId(new_id()),
        tenant=tenant,
        target=target,
        filters=filters,
        secrets=SecretRing(secret()),
        created_at=NOW,
        updated_at=NOW,
    )


def event(kind: EventType = EventType.INVOICE_ISSUED, *, tenant: Tenant = TENANT) -> PublicEvent:
    """A public event of ``kind`` about a document of ``tenant``'s."""
    return PublicEvent(
        id=EventId(new_id()),
        type=kind,
        occurred_at=NOW,
        company=tenant.company,
        data={"invoice": {"id": str(new_id()), "kind": "invoice", "total": "1180.00"}},
    )
```

`services/webhooks/tests/conftest.py`:

```python
"""Fixtures of webhooks' tests: PostgreSQL at this version (Task 13 adds NATS, the identity provider and
identity's fake)."""

from typing import Final
lazy from collections.abc import AsyncGenerator, Generator

import pytest
from sqlalchemy import text

from ledger_kernel.ids import new_id
from ledger_platform.database import Database
from ledger_testing.containers import Postgres
from ledger_testing.logs import restored_logging
from ledger_webhooks.adapters.postgres.migrate import upgrade

EMPTY_EVERY_TABLE: Final = text(
    "TRUNCATE delivery_queue, delivery_attempts, deliveries, endpoints, outbox, inbox, idempotency_keys"
)


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
    """webhooks' database, migrated to this version; the service connects as its owner, no superuser."""
    url = postgres.database("webhooks", role="webhooks", password="webhooks-password")
    upgrade(url)
    return url


@pytest.fixture
def fresh_database_url(postgres: Postgres) -> str:
    """An empty database of its own, owned by a role of its own: for the tests of the migrations."""
    name = f"fresh_{new_id().hex[16:]}"
    return postgres.database(name, role=name, password="fresh-password")


@pytest.fixture
async def database(database_url: str) -> AsyncGenerator[Database]:
    """A pool on webhooks' database, every table emptied after the test."""
    async with Database.open(database_url, pool_size=4) as opened:
        yield opened
        async with opened.engine.begin() as connection:
            await connection.execute(EMPTY_EVERY_TABLE)
```

`services/webhooks/tests/test_migrations.py`:

```python
from typing import Final

import pytest
from alembic.autogenerate import produce_migrations, render_python_code
from alembic.migration import MigrationContext
from pydantic import TypeAdapter
from sqlalchemy import MetaData, create_engine, inspect, text
from sqlalchemy.pool import NullPool

from ledger_kernel.errors import ErrorKind
from ledger_platform.database import Database
from ledger_platform.migrations import SchemaNotCurrentError
from ledger_platform.schema import CHASSIS_METADATA
from ledger_webhooks.adapters.postgres import migrate
from ledger_webhooks.adapters.postgres.migrate import downgrade, head, require_head, upgrade
from ledger_webhooks.adapters.postgres.tables import WebhooksTable

TABLES: Final = frozenset(
    {"endpoints", "deliveries", "delivery_attempts", "delivery_queue", "outbox", "inbox", "idempotency_keys"}
)
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
    for table in (*CHASSIS_METADATA.tables.values(), *WebhooksTable.metadata.tables.values()):
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
    assert tables_of(fresh_database_url) == {*TABLES, "alembic_version"}
    downgrade(fresh_database_url)
    assert tables_of(fresh_database_url) == {"alembic_version"}
    upgrade(fresh_database_url)
    assert tables_of(fresh_database_url) >= TABLES


def test_the_migrated_schema_is_exactly_the_models(database_url: str) -> None:
    assert pending_changes(database_url) is None


def test_a_company_s_data_is_under_its_policy_and_the_queue_is_not(database_url: str) -> None:
    engine = create_engine(database_url, poolclass=NullPool)
    try:
        with engine.connect() as connection:
            rows = connection.execute(text("SELECT tablename::text, policyname::text FROM pg_policies"))
            policies = PAIRS.validate_python([tuple(row) for row in rows])
    finally:
        engine.dispose()
    assert sorted(policies) == [
        ("deliveries", "tenant_isolation"),
        ("delivery_attempts", "tenant_isolation"),
        ("endpoints", "tenant_isolation"),
        ("idempotency_keys", "organization_isolation"),
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
        "LEDGER_SECRET_KEYS": '{"k1": "dGVzdHRlc3R0ZXN0dGVzdHRlc3R0ZXN0dGVzdHRlc3Q="}',
        "LEDGER_SECRET_KEY_ID": "k1",
    }.items():
        monkeypatch.setenv(name, value)
    assert migrate.main() == 0
    assert tables_of(fresh_database_url) >= TABLES
```

`services/webhooks/tests/test_repositories.py`:

```python
from datetime import timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from ledger_kernel.ids import new_id
from ledger_kernel.pagination import PageRequest
from ledger_webhooks.adapters.postgres.repositories import LostUpdateError, PgDueDeliveries
from ledger_webhooks.adapters.postgres.tables import EndpointRow
from ledger_webhooks.adapters.postgres.units import PgUnits, UnscopedUnitError, bind
from ledger_webhooks.adapters.sealing import SealingError
from ledger_webhooks.application.fanout import fan_out
from ledger_webhooks.domain.catalog import Filters
from ledger_webhooks.domain.delivery import Attempt, DeliveryState, Failure, Schedule
from ledger_webhooks.domain.endpoint import Subscription
from ledger_webhooks.domain.ids import DeliveryId, EndpointId
from ledger_webhooks.domain.signing import DEFAULT_OVERLAP
lazy from ledger_platform.database import Database
lazy from ledger_webhooks.domain.delivery import Delivery

from webhook_samples import NOW, OTHER_TENANT, STORAGE, TARGET, TENANT, endpoint, event, secret

LATER = NOW + timedelta(minutes=1)
MOVE_SECRETS = text(
    "UPDATE endpoints SET secrets = (SELECT secrets FROM endpoints WHERE id = :first) WHERE id = :second"
)


async def test_an_endpoint_reads_back_with_its_secrets_sealed_at_rest(database: Database) -> None:
    units = PgUnits(database, STORAGE, clock=lambda: NOW)
    kept = endpoint(Filters.parse(["invoice.fiscal.*"]))
    async with units.unit(TENANT) as unit:
        await unit.endpoints.add(kept)
        rotated = kept.rotated(secret(b"r" * 32), overlap=DEFAULT_OVERLAP, now=NOW)
        await unit.endpoints.update(rotated)
        assert await unit.endpoints.get(kept.id) == rotated
        assert await unit.endpoints.get(EndpointId(new_id())) is None
    async with database.transaction(TENANT) as transaction:
        stored = (await transaction.connection.execute(select(EndpointRow.secrets))).scalar_one()
    assert b"r" * 32 not in str(stored).encode()
    assert all(each["sealed"] is not None and each["sealed"].startswith("t1:") for each in stored)


async def test_a_sealed_secret_moved_onto_another_endpoint_does_not_open(database: Database) -> None:
    units = PgUnits(database, STORAGE, clock=lambda: NOW)
    first, second = endpoint(), endpoint()
    async with units.unit(TENANT) as unit:
        await unit.endpoints.add(first)
        await unit.endpoints.add(second)
    async with database.transaction(TENANT) as transaction:
        await transaction.connection.execute(MOVE_SECRETS, {"first": first.id, "second": second.id})
    with pytest.raises(SealingError):
        async with units.unit(TENANT) as unit:
            await unit.endpoints.get(second.id)


async def test_a_change_applies_to_the_version_before_it_only(database: Database) -> None:
    units = PgUnits(database, STORAGE, clock=lambda: NOW)
    kept = endpoint()
    changed = kept.changed(Subscription(target=TARGET, filters=kept.filters, enabled=False), now=LATER)
    async with units.unit(TENANT) as unit:
        await unit.endpoints.add(kept)
        await unit.endpoints.update(changed)
        assert await unit.endpoints.locked(kept.id) == changed
        assert await unit.endpoints.active() == ()
    with pytest.raises(LostUpdateError):
        async with units.unit(TENANT) as unit:
            await unit.endpoints.update(changed)


async def test_endpoints_are_read_a_page_at_a_time_without_the_deleted(database: Database) -> None:
    units = PgUnits(database, STORAGE, clock=lambda: NOW)
    kept = [endpoint() for _ in range(3)]
    async with units.unit(TENANT) as unit:
        for each in kept:
            await unit.endpoints.add(each)
        await unit.endpoints.update(kept[0].removed(now=LATER))
        first = await unit.endpoints.page(PageRequest(size=1))
        rest = await unit.endpoints.page(PageRequest(size=5, after=kept[1].id))
        assert [each.id for each in await unit.endpoints.active()] == [kept[1].id, kept[2].id]
    assert [each.id for each in first.items] == [kept[1].id]
    assert first.next_cursor is not None
    assert [each.id for each in rest.items] == [kept[2].id]


async def test_a_delivery_keeps_its_body_while_pending_and_its_attempts_without_one(
    database: Database,
) -> None:
    units = PgUnits(database, STORAGE, clock=lambda: NOW)
    kept = endpoint()
    async with units.unit(TENANT) as unit:
        await unit.endpoints.add(kept)
        (delivery,) = await fan_out(unit, event())
        assert await fan_out(unit, event()) != ()
        public = event()
        await fan_out(unit, public)
        assert await fan_out(unit, public) == ()
        assert await unit.deliveries.body(delivery.id) is not None
        failed = Attempt(number=1, at=NOW, duration=timedelta(milliseconds=250), failure=Failure.TIMEOUT)
        retried = delivery.attempted(failed, Schedule(lambda: 0.5))
        await unit.deliveries.record(delivery.id, failed)
        await unit.deliveries.update(retried)
        assert await unit.deliveries.body(delivery.id) is not None
        done = Attempt(number=2, at=LATER, duration=timedelta(milliseconds=40), status=200)
        delivered = retried.attempted(done, Schedule(lambda: 0.5))
        await unit.deliveries.record(delivery.id, done)
        await unit.deliveries.update(delivered)
        assert await unit.deliveries.body(delivery.id) is None
        assert await unit.deliveries.get(delivery.id) == delivered
        assert await unit.deliveries.attempts(delivery.id) == (failed, done)
        page = await unit.deliveries.page(kept.id, PageRequest(size=2))
        rest = await unit.deliveries.page(kept.id, PageRequest(size=2, after=page.items[-1].id))
        assert await unit.deliveries.get(DeliveryId(new_id())) is None
    assert len(page.items) + len(rest.items) == 3
    assert delivered.state is DeliveryState.DELIVERED


async def test_a_pending_delivery_without_a_body_is_refused_by_the_schema(database: Database) -> None:
    units = PgUnits(database, STORAGE, clock=lambda: NOW)
    async with units.unit(TENANT) as unit:
        await unit.endpoints.add(endpoint())
        (delivery,) = await fan_out(unit, event())
    with pytest.raises(IntegrityError):
        async with database.transaction(TENANT) as transaction:
            await transaction.connection.execute(text("UPDATE deliveries SET body = NULL"))
    assert delivery.state is DeliveryState.PENDING


async def test_what_is_due_is_claimed_across_companies_once_while_its_lease_holds(database: Database) -> None:
    units = PgUnits(database, STORAGE, clock=lambda: NOW)
    made: list[Delivery] = []
    for tenant in (TENANT, OTHER_TENANT):
        async with units.unit(tenant) as unit:
            await unit.endpoints.add(endpoint(tenant=tenant))
            made += await fan_out(unit, event(tenant=tenant))
    due = PgDueDeliveries(database)
    lease = timedelta(seconds=60)
    claimed = await due.claim(now=NOW, limit=10, lease=lease)
    assert {each.delivery for each in claimed} == {each.id for each in made}
    assert {each.tenant for each in claimed} == {TENANT, OTHER_TENANT}
    assert await due.claim(now=NOW + timedelta(seconds=30), limit=10, lease=lease) == ()
    assert len(await due.claim(now=NOW + lease, limit=1, lease=lease)) == 1
    async with units.unit(TENANT) as unit:
        await unit.queue.schedule(made[0].id, NOW + timedelta(hours=1))
        await unit.queue.drop(made[0].id)
    assert {each.delivery for each in await due.claim(now=NOW + 2 * lease, limit=10, lease=lease)} == {
        made[1].id
    }


async def test_a_company_never_sees_another_s_endpoints_or_deliveries(database: Database) -> None:
    units = PgUnits(database, STORAGE, clock=lambda: NOW)
    theirs = endpoint()
    async with units.unit(TENANT) as unit:
        await unit.endpoints.add(theirs)
        (delivery,) = await fan_out(unit, event())
    async with units.unit(OTHER_TENANT) as unit:
        assert await unit.endpoints.get(theirs.id) is None
        assert await unit.deliveries.get(delivery.id) is None
        assert await unit.deliveries.attempts(delivery.id) == ()


async def test_a_unit_without_a_company_is_a_defect(database: Database) -> None:
    async with database.transaction(None) as transaction:
        with pytest.raises(UnscopedUnitError):
            bind(transaction, STORAGE)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest services/webhooks/tests/test_migrations.py services/webhooks/tests/test_repositories.py -q --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'ledger_webhooks.adapters.postgres'`.

- [ ] **Step 4: Implement**

`services/webhooks/src/ledger_webhooks/settings.py`:

```python
"""webhooks' settings: the chassis's and its API's, identity, the secret keyring, and how deliveries are sent.

``secret_keys`` is the keyring endpoint secrets are sealed with (``{"k1": "<32 bytes, base64>"}``, JSON in the
environment, from the secret manager), ``secret_key_id`` the key new secrets are sealed under. Plain HTTP
targets and private targets (a compose service's name, a private address) are a laptop's or a test's only:
beyond them both are refused at start, so a misconfigured production can never deliver inside the network.
"""

from enum import StrEnum
from typing import Annotated, Final, Self

from pydantic import Field, model_validator
lazy from pydantic import SecretStr

from ledger_platform.settings import ApiSettings

ADDRESS: Final = r"^[A-Za-z0-9.-]+:[0-9]{1,5}$"
_BEYOND_A_LAPTOP: Final = frozenset({"staging", "production"})


class WebhooksProblem(StrEnum):
    """Why a configuration cannot run webhooks safely, naming settings, never their values."""

    UNSAFE_TARGETS = "allow_http_targets and allow_private_targets are for local and test only"
    NO_CURRENT_KEY = "secret_key_id must name a key of secret_keys"
    LEASE_TOO_SHORT = "delivery_lease_seconds must exceed delivery_timeout_seconds"


class _WebhooksSettingsError(ValueError):
    """A configuration that cannot run webhooks safely."""

    def __init__(self, problem: WebhooksProblem) -> None:
        super().__init__(problem.value)


class WebhooksSettings(ApiSettings):
    """webhooks' settings (see the module)."""

    service_name: str = Field(default="webhooks", pattern=r"^[a-z][a-z0-9-]{1,30}$")
    identity_address: str = Field(default=..., min_length=3, max_length=255, pattern=ADDRESS)
    """identity's gRPC, ``host:port``: the caller's permissions."""
    secret_keys: dict[Annotated[str, Field(pattern=r"^[a-z0-9-]{1,32}$")], SecretStr] = Field(
        default=..., min_length=1, max_length=16
    )
    secret_key_id: str = Field(default=..., pattern=r"^[a-z0-9-]{1,32}$")
    allow_http_targets: bool = False
    allow_private_targets: bool = False
    delivery_batch: Annotated[int, Field(ge=1, le=200)] = 20
    """How many deliveries one pass of a replica's worker sends at once."""
    delivery_poll_seconds: Annotated[float, Field(gt=0, le=60)] = 1.0
    """How long the worker waits after a pass that found nothing due."""
    delivery_connect_seconds: Annotated[float, Field(gt=0, le=10)] = 5.0
    delivery_timeout_seconds: Annotated[float, Field(gt=0, le=30)] = 10.0
    """The whole of one attempt: connecting, sending, and the answer's status."""
    delivery_lease_seconds: Annotated[float, Field(gt=0, le=600)] = 60.0
    """How long a claimed delivery is the worker's before another may take it."""

    @model_validator(mode="after")
    def require_safe_delivery(self) -> Self:
        """Refuse unsafe targets beyond a laptop, a keyring without its current key, too short a lease."""
        if self.environment in _BEYOND_A_LAPTOP and (self.allow_http_targets or self.allow_private_targets):
            raise _WebhooksSettingsError(WebhooksProblem.UNSAFE_TARGETS)
        if self.secret_key_id not in self.secret_keys:
            raise _WebhooksSettingsError(WebhooksProblem.NO_CURRENT_KEY)
        if self.delivery_lease_seconds <= self.delivery_timeout_seconds:
            raise _WebhooksSettingsError(WebhooksProblem.LEASE_TOO_SHORT)
        return self
```

`services/webhooks/src/ledger_webhooks/adapters/postgres/__init__.py`:

```python
"""webhooks' state in PostgreSQL: tables, repositories, units of work, migrations (SQLAlchemy, Alembic)."""
```

`services/webhooks/src/ledger_webhooks/adapters/postgres/tables.py`:

```python
"""webhooks' tables, typed for SQLAlchemy Core: what the migrations create and the repositories use.

A company's data (``endpoints``, ``deliveries``, ``delivery_attempts``) is under ``tenant_isolation``; rows
refer to each other through ``(company_id, id)``, so none can name another company's. ``delivery_queue`` is
outside row-level security on purpose, as vault's and fiscal-do's watches are: the sending worker claims what
is due across companies, and the queue holds ids and times, nothing a delivery says. A delivery keeps its body
while it is pending only (``ck_deliveries_body``); its attempts never keep one.
"""

from datetime import datetime
from typing import ClassVar, Final
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql.schema import SchemaItem

URL: Final = 2048
WORD: Final = 64
DESCRIPTION: Final = 200
STATES: Final = "'pending', 'delivered', 'dead', 'cancelled'"


class WebhooksTable(DeclarativeBase):
    """webhooks' own tables; the chassis's are ``PlatformTable``'s."""

    metadata: ClassVar[MetaData] = MetaData()


class EndpointRow(WebhooksTable):
    """A company's endpoint; its secrets sealed (``ledger_webhooks.adapters.sealing``)."""

    __tablename__: str = "endpoints"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint("company_id", "id", name="uq_endpoints_company"),
        CheckConstraint("version > 0", name="ck_endpoints_version"),
        Index("ix_endpoints_active", "company_id", postgresql_where=text("enabled AND NOT deleted")),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    organization_id: Mapped[UUID] = mapped_column()
    company_id: Mapped[UUID] = mapped_column()
    url: Mapped[str] = mapped_column(String(URL))
    description: Mapped[str | None] = mapped_column(String(DESCRIPTION))
    filters: Mapped[list[str]] = mapped_column(JSONB)
    enabled: Mapped[bool] = mapped_column(Boolean)
    deleted: Mapped[bool] = mapped_column(Boolean)
    secrets: Mapped[list[dict[str, str | None]]] = mapped_column(JSONB)
    version: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DeliveryRow(WebhooksTable):
    """One public event to one endpoint."""

    __tablename__: str = "deliveries"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint("company_id", "id", name="uq_deliveries_company"),
        UniqueConstraint("endpoint_id", "event_id", name="uq_deliveries_event"),
        ForeignKeyConstraint(
            ["company_id", "endpoint_id"],
            ["endpoints.company_id", "endpoints.id"],
            name="fk_deliveries_endpoint",
        ),
        CheckConstraint(f"state IN ({STATES})", name="ck_deliveries_state"),
        CheckConstraint("(state = 'pending') = (body IS NOT NULL)", name="ck_deliveries_body"),
        CheckConstraint("attempts >= 0", name="ck_deliveries_attempts"),
        Index("ix_deliveries_endpoint", "company_id", "endpoint_id", "id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    organization_id: Mapped[UUID] = mapped_column()
    company_id: Mapped[UUID] = mapped_column()
    endpoint_id: Mapped[UUID] = mapped_column()
    event_id: Mapped[UUID] = mapped_column()
    event_type: Mapped[str] = mapped_column(String(WORD))
    state: Mapped[str] = mapped_column(String(WORD))
    attempts: Mapped[int] = mapped_column(Integer)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_status: Mapped[int | None] = mapped_column(Integer)
    last_failure: Mapped[str | None] = mapped_column(String(WORD))
    body: Mapped[bytes | None] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AttemptRow(WebhooksTable):
    """One attempt of a delivery, as the log keeps it: never a body."""

    __tablename__: str = "delivery_attempts"
    __table_args__: tuple[SchemaItem, ...] = (
        ForeignKeyConstraint(
            ["company_id", "delivery_id"],
            ["deliveries.company_id", "deliveries.id"],
            name="fk_attempts_delivery",
        ),
        CheckConstraint("number > 0", name="ck_attempts_number"),
        CheckConstraint("duration_ms >= 0", name="ck_attempts_duration"),
    )

    delivery_id: Mapped[UUID] = mapped_column(primary_key=True)
    number: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[UUID] = mapped_column()
    company_id: Mapped[UUID] = mapped_column()
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int] = mapped_column(Integer)
    status: Mapped[int | None] = mapped_column(Integer)
    failure: Mapped[str | None] = mapped_column(String(WORD))


class QueueRow(WebhooksTable):
    """When a pending delivery is due, and until when a worker holds it: outside row-level security."""

    __tablename__: str = "delivery_queue"
    __table_args__: tuple[SchemaItem, ...] = (
        ForeignKeyConstraint(
            ["company_id", "delivery_id"],
            ["deliveries.company_id", "deliveries.id"],
            name="fk_queue_delivery",
        ),
        Index("ix_delivery_queue_due", "due_at"),
    )

    delivery_id: Mapped[UUID] = mapped_column(primary_key=True)
    organization_id: Mapped[UUID] = mapped_column()
    company_id: Mapped[UUID] = mapped_column()
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    leased_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
```

`services/webhooks/src/ledger_webhooks/adapters/postgres/repositories.py`:

```python
"""webhooks' state in PostgreSQL: a company's endpoints, deliveries and attempts; the queue of what is due.

Every company's statement also names the company, so that the application's check and the database's agree.
Endpoint secrets are sealed with the service's keyring, bound to their endpoint and their own id. The queue is
read across companies by ``PgDueDeliveries`` alone, in a transaction scoped to no company, with ``FOR UPDATE
SKIP LOCKED``: replicas claiming at once never claim one delivery twice.
"""

from datetime import datetime, timedelta
from typing import ClassVar, Final, TypedDict, final, override
from uuid import UUID

from pydantic import TypeAdapter
from sqlalchemy import Row, and_, delete, insert, or_, select, update
from sqlalchemy.dialects.postgresql import insert as upsert
lazy from sqlalchemy import Select
lazy from sqlalchemy.ext.asyncio import AsyncConnection

from ledger_kernel.errors import ConflictError
from ledger_kernel.ids import CompanyId, OrganizationId, Tenant
from ledger_kernel.pagination import page_of
from ledger_webhooks.adapters.postgres.tables import AttemptRow, DeliveryRow, EndpointRow, QueueRow
from ledger_webhooks.application.ports import Deliveries, Due, DueDeliveries, Endpoints, Queue
from ledger_webhooks.domain.catalog import EventType, Filters
from ledger_webhooks.domain.delivery import Attempt, Delivery, DeliveryState, Failure
from ledger_webhooks.domain.endpoint import Endpoint
from ledger_webhooks.domain.ids import DeliveryId, EndpointId, EventId, SecretId
from ledger_webhooks.domain.signing import Secret, SecretRing
lazy from ledger_kernel.pagination import Page, PageRequest
lazy from ledger_platform.database import Database
lazy from ledger_webhooks.adapters.sealing import Keyring
lazy from ledger_webhooks.domain.targets import TargetPolicy

_MILLISECOND: Final = timedelta(milliseconds=1)

type EndpointRecord = Row[
    UUID,
    str,
    str | None,
    list[str],
    bool,
    bool,
    list[dict[str, str | None]],
    int,
    datetime,
    datetime,
]
type DeliveryRecord = Row[
    UUID,
    UUID,
    UUID,
    str,
    str,
    int,
    datetime | None,
    int | None,
    str | None,
    datetime,
]


class SealedSecret(TypedDict):
    """A secret as its row keeps it."""

    id: str
    sealed: str
    created_at: str
    expires_at: str | None


_SECRETS: Final = TypeAdapter(list[SealedSecret])


class LostUpdateError(ConflictError):
    """An endpoint changed under a unit that had not locked it: the change is refused, never overwritten."""

    code: ClassVar[str] = "webhook_changed"

    def __init__(self) -> None:
        super().__init__("the endpoint changed while it was being changed: try again")


def _bound(endpoint: EndpointId, secret: SecretId) -> bytes:
    return endpoint.bytes + secret.bytes


@final
class PgEndpoints(Endpoints):
    """The endpoints of one company, in its transaction."""

    __slots__ = ("_connection", "_keyring", "_policy", "_tenant")

    def __init__(
        self, connection: AsyncConnection, tenant: Tenant, *, keyring: Keyring, policy: TargetPolicy
    ) -> None:
        self._connection = connection
        self._tenant = tenant
        self._keyring = keyring
        self._policy = policy

    def _sealed(self, endpoint: Endpoint) -> list[dict[str, str | None]]:
        ring = endpoint.secrets
        kept = (ring.current,) if ring.previous is None else (ring.current, ring.previous)
        return [
            {
                "id": str(secret.id),
                "sealed": self._keyring.seal(secret.key, bound_to=_bound(endpoint.id, secret.id)),
                "created_at": secret.created_at.isoformat(),
                "expires_at": None if secret.expires_at is None else secret.expires_at.isoformat(),
            }
            for secret in kept
        ]

    def _opened(self, endpoint: EndpointId, stored: object) -> SecretRing:
        secrets = [
            Secret(
                id=SecretId(UUID(each["id"])),
                key=self._keyring.open(each["sealed"], bound_to=_bound(endpoint, SecretId(UUID(each["id"])))),
                created_at=datetime.fromisoformat(each["created_at"]),
                expires_at=None if each["expires_at"] is None else datetime.fromisoformat(each["expires_at"]),
            )
            for each in _SECRETS.validate_python(stored, strict=True)
        ]
        return SecretRing(secrets[0], secrets[1] if len(secrets) > 1 else None)

    def _selected(
        self,
    ) -> Select[
        UUID,
        str,
        str | None,
        list[str],
        bool,
        bool,
        list[dict[str, str | None]],
        int,
        datetime,
        datetime,
    ]:
        return select(
            EndpointRow.id,
            EndpointRow.url,
            EndpointRow.description,
            EndpointRow.filters,
            EndpointRow.enabled,
            EndpointRow.deleted,
            EndpointRow.secrets,
            EndpointRow.version,
            EndpointRow.created_at,
            EndpointRow.updated_at,
        ).where(EndpointRow.company_id == self._tenant.company)

    def _endpoint(self, record: EndpointRecord) -> Endpoint:
        identifier, url, description, filters, enabled, deleted, secrets, version, created_at, updated_at = (
            record
        )
        endpoint = EndpointId(identifier)
        return Endpoint(
            id=endpoint,
            tenant=self._tenant,
            target=self._policy.parse(url),
            description=description,
            filters=Filters(frozenset(filters)),
            secrets=self._opened(endpoint, secrets),
            created_at=created_at,
            updated_at=updated_at,
            enabled=enabled,
            version=version,
            deleted=deleted,
        )

    @override
    async def add(self, endpoint: Endpoint) -> None:
        await self._connection.execute(
            insert(EndpointRow).values(
                id=endpoint.id,
                organization_id=self._tenant.organization,
                company_id=self._tenant.company,
                url=endpoint.target.url,
                description=endpoint.description,
                filters=sorted(endpoint.filters.patterns),
                enabled=endpoint.enabled,
                deleted=endpoint.deleted,
                secrets=self._sealed(endpoint),
                version=endpoint.version,
                created_at=endpoint.created_at,
                updated_at=endpoint.updated_at,
            )
        )

    @override
    async def get(self, endpoint: EndpointId) -> Endpoint | None:
        found = (
            await self._connection.execute(self._selected().where(EndpointRow.id == endpoint))
        ).one_or_none()
        return None if found is None else self._endpoint(found)

    @override
    async def locked(self, endpoint: EndpointId) -> Endpoint | None:
        statement = self._selected().where(EndpointRow.id == endpoint).with_for_update()
        found = (await self._connection.execute(statement)).one_or_none()
        return None if found is None else self._endpoint(found)

    @override
    async def update(self, endpoint: Endpoint) -> None:
        changed = await self._connection.execute(
            update(EndpointRow)
            .where(
                EndpointRow.company_id == self._tenant.company,
                EndpointRow.id == endpoint.id,
                EndpointRow.version == endpoint.version - 1,
            )
            .values(
                url=endpoint.target.url,
                description=endpoint.description,
                filters=sorted(endpoint.filters.patterns),
                enabled=endpoint.enabled,
                deleted=endpoint.deleted,
                secrets=self._sealed(endpoint),
                version=endpoint.version,
                updated_at=endpoint.updated_at,
            )
        )
        if changed.rowcount != 1:
            raise LostUpdateError

    @override
    async def active(self) -> tuple[Endpoint, ...]:
        statement = self._selected().where(EndpointRow.enabled, EndpointRow.deleted.is_(False))
        return tuple(self._endpoint(row) for row in await self._connection.execute(statement))

    @override
    async def page(self, request: PageRequest) -> Page[Endpoint]:
        statement = self._selected().where(EndpointRow.deleted.is_(False)).order_by(EndpointRow.id)
        if request.after is not None:
            statement = statement.where(EndpointRow.id > request.after)
        rows = (await self._connection.execute(statement.limit(request.size + 1))).all()
        return page_of([self._endpoint(row) for row in rows], request, key=lambda each: each.id)


def _delivery(record: DeliveryRecord) -> Delivery:
    identifier, endpoint, event, event_type, state, attempts, next_at, status, failure, created_at = record
    return Delivery(
        id=DeliveryId(identifier),
        endpoint=EndpointId(endpoint),
        event=EventId(event),
        event_type=EventType(event_type),
        state=DeliveryState(state),
        attempts=attempts,
        next_attempt_at=next_at,
        created_at=created_at,
        last_status=status,
        last_failure=None if failure is None else Failure(failure),
    )


@final
class PgDeliveries(Deliveries):
    """The deliveries of one company, their bodies while pending, their attempts."""

    __slots__ = ("_connection", "_tenant")

    def __init__(self, connection: AsyncConnection, tenant: Tenant) -> None:
        self._connection = connection
        self._tenant = tenant

    def _selected(
        self,
    ) -> Select[UUID, UUID, UUID, str, str, int, datetime | None, int | None, str | None, datetime]:
        return select(
            DeliveryRow.id,
            DeliveryRow.endpoint_id,
            DeliveryRow.event_id,
            DeliveryRow.event_type,
            DeliveryRow.state,
            DeliveryRow.attempts,
            DeliveryRow.next_attempt_at,
            DeliveryRow.last_status,
            DeliveryRow.last_failure,
            DeliveryRow.created_at,
        ).where(DeliveryRow.company_id == self._tenant.company)

    @override
    async def add(self, delivery: Delivery, body: bytes) -> bool:
        added = await self._connection.execute(
            upsert(DeliveryRow)
            .values(
                id=delivery.id,
                organization_id=self._tenant.organization,
                company_id=self._tenant.company,
                endpoint_id=delivery.endpoint,
                event_id=delivery.event,
                event_type=delivery.event_type.value,
                state=delivery.state.value,
                attempts=delivery.attempts,
                next_attempt_at=delivery.next_attempt_at,
                body=body,
                created_at=delivery.created_at,
            )
            .on_conflict_do_nothing(index_elements=[DeliveryRow.endpoint_id, DeliveryRow.event_id])
            .returning(DeliveryRow.id)
        )
        return added.scalar_one_or_none() is not None

    @override
    async def get(self, delivery: DeliveryId) -> Delivery | None:
        found = (
            await self._connection.execute(self._selected().where(DeliveryRow.id == delivery))
        ).one_or_none()
        return None if found is None else _delivery(found)

    @override
    async def body(self, delivery: DeliveryId) -> bytes | None:
        statement = select(DeliveryRow.body).where(
            DeliveryRow.company_id == self._tenant.company, DeliveryRow.id == delivery
        )
        return (await self._connection.execute(statement)).scalar_one_or_none()

    @override
    async def update(self, delivery: Delivery) -> None:
        statement = (
            update(DeliveryRow)
            .where(DeliveryRow.company_id == self._tenant.company, DeliveryRow.id == delivery.id)
            .values(
                state=delivery.state.value,
                attempts=delivery.attempts,
                next_attempt_at=delivery.next_attempt_at,
                last_status=delivery.last_status,
                last_failure=None if delivery.last_failure is None else delivery.last_failure.value,
            )
        )
        if delivery.state is not DeliveryState.PENDING:
            statement = statement.values(body=None)
        await self._connection.execute(statement)

    @override
    async def record(self, delivery: DeliveryId, attempt: Attempt) -> None:
        await self._connection.execute(
            insert(AttemptRow).values(
                delivery_id=delivery,
                number=attempt.number,
                organization_id=self._tenant.organization,
                company_id=self._tenant.company,
                at=attempt.at,
                duration_ms=attempt.duration // _MILLISECOND,
                status=attempt.status,
                failure=None if attempt.failure is None else attempt.failure.value,
            )
        )

    @override
    async def attempts(self, delivery: DeliveryId) -> tuple[Attempt, ...]:
        statement = (
            select(
                AttemptRow.number,
                AttemptRow.at,
                AttemptRow.duration_ms,
                AttemptRow.status,
                AttemptRow.failure,
            )
            .where(AttemptRow.company_id == self._tenant.company, AttemptRow.delivery_id == delivery)
            .order_by(AttemptRow.number)
        )
        return tuple(
            Attempt(
                number=number,
                at=at,
                duration=duration * _MILLISECOND,
                status=status,
                failure=None if failure is None else Failure(failure),
            )
            for number, at, duration, status, failure in await self._connection.execute(statement)
        )

    @override
    async def page(self, endpoint: EndpointId, request: PageRequest) -> Page[Delivery]:
        statement = self._selected().where(DeliveryRow.endpoint_id == endpoint).order_by(DeliveryRow.id)
        if request.after is not None:
            statement = statement.where(DeliveryRow.id > request.after)
        rows = (await self._connection.execute(statement.limit(request.size + 1))).all()
        return page_of([_delivery(row) for row in rows], request, key=lambda each: each.id)


@final
class PgQueue(Queue):
    """The queue entries of one company's deliveries, written in its transaction."""

    __slots__ = ("_connection", "_tenant")

    def __init__(self, connection: AsyncConnection, tenant: Tenant) -> None:
        self._connection = connection
        self._tenant = tenant

    @override
    async def schedule(self, delivery: DeliveryId, at: datetime) -> None:
        await self._connection.execute(
            upsert(QueueRow)
            .values(
                delivery_id=delivery,
                organization_id=self._tenant.organization,
                company_id=self._tenant.company,
                due_at=at,
                leased_until=None,
            )
            .on_conflict_do_update(
                index_elements=[QueueRow.delivery_id], set_={"due_at": at, "leased_until": None}
            )
        )

    @override
    async def drop(self, delivery: DeliveryId) -> None:
        await self._connection.execute(
            delete(QueueRow).where(
                QueueRow.company_id == self._tenant.company, QueueRow.delivery_id == delivery
            )
        )


@final
class PgDueDeliveries(DueDeliveries):
    """The queue read across companies (see the module)."""

    __slots__ = ("_database",)

    def __init__(self, database: Database) -> None:
        self._database = database

    @override
    async def claim(self, *, now: datetime, limit: int, lease: timedelta) -> tuple[Due, ...]:
        due = (
            select(QueueRow.delivery_id)
            .where(
                QueueRow.due_at <= now,
                or_(QueueRow.leased_until.is_(None), QueueRow.leased_until <= now),
            )
            .order_by(QueueRow.due_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        claim = (
            update(QueueRow)
            .where(and_(QueueRow.delivery_id.in_(due.scalar_subquery())))
            .values(leased_until=now + lease)
            .returning(QueueRow.delivery_id, QueueRow.organization_id, QueueRow.company_id)
        )
        async with self._database.transaction(None) as transaction:
            rows = (await transaction.connection.execute(claim)).all()
        return tuple(
            Due(DeliveryId(delivery), Tenant(OrganizationId(organization), CompanyId(company)))
            for delivery, organization, company in rows
        )
```

`services/webhooks/src/ledger_webhooks/adapters/postgres/units.py`:

```python
"""webhooks' unit of work over one chassis transaction scoped to a company: endpoints, deliveries, queue."""

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import ClassVar, final, override
lazy from collections.abc import AsyncGenerator

from ledger_kernel.clock import utc_now
from ledger_kernel.errors import LedgerError
from ledger_kernel.ids import Tenant
from ledger_webhooks.adapters.postgres.repositories import PgDeliveries, PgEndpoints, PgQueue
from ledger_webhooks.application.ports import Units, WebhooksUnit
lazy from ledger_kernel.clock import Clock
lazy from ledger_platform.database import Database, UnitOfWork
lazy from ledger_webhooks.adapters.sealing import Keyring
lazy from ledger_webhooks.domain.targets import TargetPolicy


class UnscopedUnitError(LedgerError):
    """webhooks' state was to be read in a transaction scoped to no company: a defect of the caller."""

    code: ClassVar[str] = "unscoped_unit"

    def __init__(self) -> None:
        super().__init__("webhooks' state is read and changed in a transaction scoped to one company")


@final
@dataclass(frozen=True, slots=True)
class Storage:
    """What reading and writing endpoints needs beside a transaction: the keyring, the deployment's policy."""

    keyring: Keyring
    policy: TargetPolicy


def bind(unit: UnitOfWork, storage: Storage, *, clock: Clock = utc_now) -> WebhooksUnit:
    """The webhooks view of ``unit``, a transaction scoped to a company; ``clock`` is read once."""
    tenant = unit.scope
    if not isinstance(tenant, Tenant):
        raise UnscopedUnitError
    connection = unit.connection
    return WebhooksUnit(
        tenant=tenant,
        endpoints=PgEndpoints(connection, tenant, keyring=storage.keyring, policy=storage.policy),
        deliveries=PgDeliveries(connection, tenant),
        queue=PgQueue(connection, tenant),
        now=clock(),
    )


@final
class PgUnits(Units):
    """Units of work over webhooks' database."""

    __slots__ = ("_clock", "_database", "_storage")

    def __init__(self, database: Database, storage: Storage, *, clock: Clock = utc_now) -> None:
        self._database = database
        self._storage = storage
        self._clock = clock

    @override
    @asynccontextmanager
    async def unit(self, tenant: Tenant) -> AsyncGenerator[WebhooksUnit]:
        async with self._database.transaction(tenant) as transaction:
            yield bind(transaction, self._storage, clock=self._clock)
```

`services/webhooks/src/ledger_webhooks/adapters/postgres/migrate.py`:

```python
"""webhooks' schema brought to this version's: ``ledger_platform.migrations`` with webhooks' revisions."""

from typing import Final

from ledger_platform.migrations import BASE, HEAD, Migrations
from ledger_webhooks.settings import WebhooksSettings
lazy from ledger_platform.database import Database

SCRIPTS: Final = "ledger_webhooks.adapters.postgres:migrations"
MIGRATIONS: Final = Migrations(SCRIPTS, owner="webhooks")


def upgrade(url: str, revision: str = HEAD) -> None:
    """Bring webhooks' database at ``url`` up to ``revision``."""
    MIGRATIONS.upgrade(url, revision)


def downgrade(url: str, revision: str = BASE) -> None:
    """Take webhooks' database at ``url`` down to ``revision``."""
    MIGRATIONS.downgrade(url, revision)


def head() -> str:
    """The revision webhooks' migrations end at."""
    return MIGRATIONS.head()


async def require_head(database: Database, expected: str) -> None:
    """Webhooks's readiness check: the database is at ``expected``."""
    await MIGRATIONS.require_head(database, expected)


def main() -> int:
    """``ledger-webhooks-migrate``: bring the database of ``LEDGER_DATABASE_URL`` to this schema."""
    upgrade(WebhooksSettings().database_url.get_secret_value())
    return 0
```

`services/webhooks/src/ledger_webhooks/adapters/postgres/migrations/__init__.py`:

```python
"""webhooks' Alembic environment and migration scripts."""
```

`services/webhooks/src/ledger_webhooks/adapters/postgres/migrations/env.py`:

```python
"""Alembic's entry point for webhooks: the platform's runner with webhooks' tables."""

from ledger_platform.migrations import run_migrations
from ledger_webhooks.adapters.postgres.tables import WebhooksTable

run_migrations(WebhooksTable.metadata)
```

`services/webhooks/src/ledger_webhooks/adapters/postgres/migrations/versions/__init__.py`:

```python
"""webhooks' migration scripts, one per revision."""
```

`services/webhooks/src/ledger_webhooks/adapters/postgres/migrations/versions/r0001_webhooks.py`:

```python
"""Revision 0001, from nothing: webhooks' first schema.

The chassis's tables (as ``ledger_platform`` declares them at this revision), then endpoints, deliveries and
their attempts under row-level security, and the delivery queue outside it (see ``tables``).
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
    "delivery_queue",
    "delivery_attempts",
    "deliveries",
    "endpoints",
)
_STATES: Final = "'pending', 'delivered', 'dead', 'cancelled'"


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


def _endpoints() -> None:
    endpoints = op.create_table(
        "endpoints",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("description", sa.String(200), nullable=True),
        sa.Column("filters", JSONB(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("deleted", sa.Boolean(), nullable=False),
        sa.Column("secrets", JSONB(), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        _moment("created_at"),
        _moment("updated_at"),
        sa.UniqueConstraint("company_id", "id", name="uq_endpoints_company"),
        sa.CheckConstraint("version > 0", name="ck_endpoints_version"),
    )
    op.create_index(
        "ix_endpoints_active",
        "endpoints",
        ["company_id"],
        postgresql_where=sa.text("enabled AND NOT deleted"),
    )
    _apply(tenant_isolation(endpoints))


def _deliveries() -> None:
    deliveries = op.create_table(
        "deliveries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("endpoint_id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("state", sa.String(64), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        _moment("next_attempt_at", nullable=True),
        sa.Column("last_status", sa.Integer(), nullable=True),
        sa.Column("last_failure", sa.String(64), nullable=True),
        sa.Column("body", sa.LargeBinary(), nullable=True),
        _moment("created_at"),
        sa.UniqueConstraint("company_id", "id", name="uq_deliveries_company"),
        sa.UniqueConstraint("endpoint_id", "event_id", name="uq_deliveries_event"),
        sa.ForeignKeyConstraint(
            ["company_id", "endpoint_id"],
            ["endpoints.company_id", "endpoints.id"],
            name="fk_deliveries_endpoint",
        ),
        sa.CheckConstraint(f"state IN ({_STATES})", name="ck_deliveries_state"),
        sa.CheckConstraint("(state = 'pending') = (body IS NOT NULL)", name="ck_deliveries_body"),
        sa.CheckConstraint("attempts >= 0", name="ck_deliveries_attempts"),
    )
    op.create_index("ix_deliveries_endpoint", "deliveries", ["company_id", "endpoint_id", "id"])
    _apply(tenant_isolation(deliveries))
    attempts = op.create_table(
        "delivery_attempts",
        sa.Column("delivery_id", sa.Uuid(), primary_key=True),
        sa.Column("number", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        _moment("at"),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("status", sa.Integer(), nullable=True),
        sa.Column("failure", sa.String(64), nullable=True),
        sa.ForeignKeyConstraint(
            ["company_id", "delivery_id"],
            ["deliveries.company_id", "deliveries.id"],
            name="fk_attempts_delivery",
        ),
        sa.CheckConstraint("number > 0", name="ck_attempts_number"),
        sa.CheckConstraint("duration_ms >= 0", name="ck_attempts_duration"),
    )
    _apply(tenant_isolation(attempts))
    op.create_table(
        "delivery_queue",
        sa.Column("delivery_id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        _moment("due_at"),
        _moment("leased_until", nullable=True),
        sa.ForeignKeyConstraint(
            ["company_id", "delivery_id"],
            ["deliveries.company_id", "deliveries.id"],
            name="fk_queue_delivery",
        ),
    )
    op.create_index("ix_delivery_queue_due", "delivery_queue", ["due_at"])


def upgrade() -> None:
    """From nothing to webhooks' first schema."""
    _chassis()
    _endpoints()
    _deliveries()


def downgrade() -> None:
    """Back to nothing: every table, newest first (their policies go with them)."""
    for name in _NEWEST_FIRST:
        op.drop_table(name)
```

(`PgDeliveries.add` learns whether it inserted from `RETURNING`, not from `rowcount`: Decision 19.)

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest services/webhooks/tests -q --no-cov`
Expected: PASS: the schema built from nothing and back and exactly the models; an endpoint's secrets read back only through the keyring and only bound to their row; a stale change refused; a delivery keeps its body while pending and its attempts without one; one fan-out per event; two workers never claim the same delivery; a company never sees another's endpoints or deliveries.

- [ ] **Step 6: Gate**

Run: `uv run poe fmt && uv run poe check`
Expected: every step passes, coverage 100 %.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock services/webhooks
git commit -m "feat(webhooks): settings, endpoints and deliveries in PostgreSQL, a leased queue

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 13: webhooks' public API, its consumer of invoicing's events, the delivery worker, the service assembled

**Files:**
- Create: `services/webhooks/src/ledger_webhooks/resources.py`, `api/__init__.py`, `api/context.py`, `api/models.py`, `api/endpoints.py`, `api/consumers.py`, `api/workers.py`, `main.py`, `openapi.py`, `services/webhooks/openapi.json` (generated)
- Modify: `services/webhooks/pyproject.toml`, `services/webhooks/tests/conftest.py`, `pyproject.toml` (root)
- Test: `services/webhooks/tests/webhook_remotes.py` (support), `services/webhooks/tests/test_settings.py`, `services/webhooks/tests/test_service.py`, `services/webhooks/tests/test_openapi.py`

**Interfaces:**
- Consumes: Tasks 9-12; as Task 7 (`Service`, `Interfaces`, `Events`, `Runtime`, the `routes` helpers, `CachedPermissions`, `RemotePermissions`, `GET_PERMISSIONS`, `Channel`, `HttpKeys`, `PublishedKeys`, `TokenPolicy`, `TokenVerifier`, `organization_of`, `Idempotency`, `ConsumerSpec`, `PROBLEM_JSON`, `ensure_stream`, `ledger_platform.supervision.Work`); `LocalIssuer`, `Caller`, `created`, `document`, `page`, `nats_server`, `nats_address`, `eventually`.
- Produces: `resources`: `VERSION`, `USER_AGENT`, `storage_of(settings)`, `WebhooksResources(permissions, storage, sender)`, `opened(settings, *, keys)`. `api.context`: `Problem`, `Call`, `ApiContext`, `problems(*statuses)`, `precondition_checked()`. `api.models`: `DEFAULT_OVERLAP_HOURS`, `EndpointShape`, `Rotation`, `EndpointView`, `EndpointPage`, `SecretView`, `SecretsView`, `AttemptView`, `DeliveryView`, `DeliveryPage`, `EventTypeView`, `EventTypes`. `api.endpoints`: `PREFIX` (`/v1/webhooks`), `TAG`, `new_key`, `routers(context)`. Routes (company in `Ledger-Company-Id`; `company.manage` to change and to read secrets, `company.read` to read): `GET /v1/webhooks/event-types`; `POST /v1/webhooks/endpoints` (201, `Idempotency-Key`; the stored answer never holds a secret); `GET /v1/webhooks/endpoints`, `GET|PUT|DELETE /v1/webhooks/endpoints/{endpoint_id}` (`ETag`/`If-Match`); `GET .../{endpoint_id}/secret`; `POST .../{endpoint_id}/secret/rotate`; `POST .../{endpoint_id}/test`; `GET .../{endpoint_id}/deliveries`, `GET .../deliveries/{delivery_id}` (attempts without bodies). `api.consumers`: `INVOICING_EVENTS` (`webhooks-invoicing`, `invoicing.document.>`), `consumers(runtime)`. `api.workers`: `sending_of(runtime)`, `workers(runtime)` (the delivery worker, one per replica). `main`: `build(settings)` (check `migrations`), `create_app()`. `openapi`: `document()`, `main()` (console script `ledger-webhooks-openapi PATH`). Test support `webhook_remotes`: `MANAGER`, `FakeIdentity`, `identity_served`, `Receiver`, `receiving`.

Re-check before starting: as Task 7, and `ledger_platform.supervision.Work` (the type `Events.workers` takes) as landed.

- [ ] **Step 1: Dependencies, layers, the published document**

`services/webhooks/pyproject.toml` — its final form:

```toml
[project]
name = "ledger-webhooks"
version = "0.1.0"
description = "The webhooks service: client endpoints, deliveries signed, retried and guarded against SSRF."
requires-python = ">=3.15"
dependencies = [
    "ledger-kernel",
    "ledger-contracts",
    "ledger-platform",
    "alembic>=1.20.0",
    "cryptography>=50.0.2",
    "fastapi>=0.143.0",
    "granian[uvloop]>=2.8.4",
    "httpx>=0.28.1",
    "pydantic>=2.14.1",
    "sqlalchemy>=2.1.4",
]

[project.scripts]
ledger-webhooks-migrate = "ledger_webhooks.adapters.postgres.migrate:main"
ledger-webhooks-openapi = "ledger_webhooks.openapi:main"

[tool.uv.sources]
ledger-kernel = { workspace = true }
ledger-contracts = { workspace = true }
ledger-platform = { workspace = true }

[build-system]
requires = ["uv_build>=0.13.0,<0.14.0"]
build-backend = "uv_build"

[tool.deptry.package_module_name_map]
"ledger-contracts" = ["ledger_contracts", "ledger"]

[tool.deptry.per_rule_ignores]
DEP002 = ["granian"]  # the ASGI server the image runs the app with; no module imports it
```

Root `pyproject.toml`: "webhooks is hexagonal" `layers = ["openapi", "main", "api", "resources", "adapters", "settings", "application", "domain"]`; the poe `openapi` task also runs `ledger-webhooks-openapi services/webhooks/openapi.json`. Run: `uv lock && uv sync`

- [ ] **Step 2: Write the failing tests**

`services/webhooks/tests/webhook_remotes.py`:

```python
"""What webhooks talks to, faked: identity over real gRPC, and a client's endpoint over real HTTP."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Final, final, override
lazy from collections.abc import AsyncGenerator

import grpc
from prometheus_client import CollectorRegistry

from ledger.identity.v1.identity_query_pb2 import (
    GetCompanyRequest,
    GetCompanyResponse,
    GetPermissionsRequest,
    GetPermissionsResponse,
)
from ledger.identity.v1.identity_query_pb2_grpc import (
    IdentityQueryServiceServicer,
    add_IdentityQueryServiceServicer_to_server,
)
from ledger_kernel.access import Permission
from ledger_kernel.errors import NotFoundError
from ledger_platform.grpc_server import serve_grpc

type PermissionsContext = grpc.aio.ServicerContext[GetPermissionsRequest, GetPermissionsResponse]
type CompanyContext = grpc.aio.ServicerContext[GetCompanyRequest, GetCompanyResponse]

MANAGER: Final = frozenset({Permission.COMPANY_MANAGE, Permission.COMPANY_READ})


@final
class FakeIdentity(IdentityQueryServiceServicer):
    """identity as webhooks asks it: who may do what in which company (``grants[(company, subject)]``)."""

    def __init__(self) -> None:
        self.grants: dict[tuple[str, str], frozenset[Permission]] = {}

    @override
    async def GetCompany(self, request: GetCompanyRequest, context: CompanyContext) -> GetCompanyResponse:
        del request, context
        raise NotFoundError

    @override
    async def GetPermissions(
        self, request: GetPermissionsRequest, context: PermissionsContext
    ) -> GetPermissionsResponse:
        del context
        granted = self.grants.get((request.company_id, request.subject), frozenset())
        return GetPermissionsResponse(permissions=[permission.value for permission in granted])


@asynccontextmanager
async def identity_served(identity: FakeIdentity) -> AsyncGenerator[str]:
    """``identity`` served on a free port of 127.0.0.1: its ``host:port``."""

    def register(server: grpc.aio.Server) -> None:
        add_IdentityQueryServiceServicer_to_server(identity, server)

    async with serve_grpc(port=0, register=register, registry=CollectorRegistry()) as port:
        yield f"127.0.0.1:{port}"


@final
@dataclass
class Receiver:
    """A client's endpoint: every request it got (head and body apart), and the status it answers."""

    status: int = 204
    received: list[tuple[dict[str, str], bytes]] = field(default_factory=list[tuple[dict[str, str], bytes]])

    async def serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = (await reader.readuntil(b"\r\n\r\n")).decode("latin-1").split("\r\n")
        headers = {
            name.strip().lower(): value.strip()
            for name, _, value in (line.partition(":") for line in head[1:] if line)
        }
        body = await reader.readexactly(int(headers.get("content-length", "0")))
        self.received.append((headers, body))
        writer.write(f"HTTP/1.1 {self.status} Answer\r\nContent-Length: 0\r\n\r\n".encode())
        await writer.drain()
        writer.close()


@asynccontextmanager
async def receiving(receiver: Receiver) -> AsyncGenerator[str]:
    """``receiver`` served on a free port of 127.0.0.1: its URL."""
    server = await asyncio.start_server(receiver.serve, "127.0.0.1", 0)
    async with server:
        yield f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}/hooks"
```

`services/webhooks/tests/conftest.py` becomes:

```python
"""Fixtures of webhooks' tests: PostgreSQL at this version, NATS, the identity provider, identity's fake.

webhooks starts on all of them with a keyring of the test's own, plain HTTP and private targets allowed (the
client's endpoint the tests deliver to listens on 127.0.0.1).
"""

from typing import Final
lazy from collections.abc import AsyncGenerator, Callable, Generator, Iterator

import httpx
import pytest
from nats.aio.client import Client
from nats.js.client import JetStreamContext
from pydantic import SecretStr
from sqlalchemy import text

from ledger_contracts import invoicing
from ledger_kernel.ids import new_id
from ledger_platform.database import Database
from ledger_platform.streams import StreamSpec, ensure_stream
from ledger_testing.api import Caller
from ledger_testing.containers import Postgres, nats_address, nats_server
from ledger_testing.issuer import LocalIssuer
from ledger_testing.logs import restored_logging
from ledger_webhooks.adapters.postgres.migrate import upgrade
from ledger_webhooks.main import build
from ledger_webhooks.settings import WebhooksSettings
lazy from ledger_platform.service import Service

from webhook_remotes import FakeIdentity, identity_served
from webhook_samples import KEY

INVOICING_STREAM: Final = StreamSpec(name=invoicing.STREAM, subjects=invoicing.SUBJECTS)
"""invoicing's stream, declared by the tests as invoicing does (webhooks reads it, never declares it)."""
EMPTY_EVERY_TABLE: Final = text(
    "TRUNCATE delivery_queue, delivery_attempts, deliveries, endpoints, outbox, inbox, idempotency_keys"
)


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
    """webhooks' database, migrated to this version; the service connects as its owner, no superuser."""
    url = postgres.database("webhooks", role="webhooks", password="webhooks-password")
    upgrade(url)
    return url


@pytest.fixture
def fresh_database_url(postgres: Postgres) -> str:
    """An empty database of its own, owned by a role of its own: for the tests of the migrations."""
    name = f"fresh_{new_id().hex[16:]}"
    return postgres.database(name, role=name, password="fresh-password")


@pytest.fixture
async def database(database_url: str) -> AsyncGenerator[Database]:
    """A pool on webhooks' database, every table emptied after the test."""
    async with Database.open(database_url, pool_size=4) as opened:
        yield opened
        async with opened.engine.begin() as connection:
            await connection.execute(EMPTY_EVERY_TABLE)


@pytest.fixture(scope="session")
def nats_url() -> Iterator[str]:
    """A real NATS server with JetStream."""
    with nats_server() as container:
        yield nats_address(container)


@pytest.fixture
async def jetstream(nats_url: str) -> AsyncGenerator[JetStreamContext]:
    """A JetStream client of the test's NATS, invoicing's stream declared as invoicing does.

    ``Client`` and ``JetStreamContext`` are built directly: nats-py's ``connect()`` and ``jetstream()`` take
    ``**options`` without a type, which strict type checking refuses to call.
    """
    client = Client()
    await client.connect(nats_url)
    try:
        context = JetStreamContext(client)
        await ensure_stream(context, INVOICING_STREAM)
        yield context
    finally:
        await client.close()


@pytest.fixture
async def issuer() -> AsyncGenerator[LocalIssuer]:
    """The identity provider webhooks trusts in the tests."""
    async with LocalIssuer.started() as started:
        yield started


@pytest.fixture
def identity() -> FakeIdentity:
    """identity, as webhooks asks it for permissions."""
    return FakeIdentity()


@pytest.fixture
def settings_for(nats_url: str, issuer: LocalIssuer) -> Callable[[str, str], WebhooksSettings]:
    """webhooks' settings on a database and identity's address: plain HTTP and private targets allowed, one
    delivery per pass (so that a pass that finds more due goes on at once)."""

    def settings(database_url: str, identity: str) -> WebhooksSettings:
        return WebhooksSettings(
            environment="test",
            database_url=SecretStr(database_url),
            nats_url=SecretStr(nats_url),
            token_issuer=issuer.issuer,
            token_audience=issuer.audience,
            token_jwks_url=issuer.keys_url,
            identity_address=identity,
            secret_keys={"t1": SecretStr(KEY)},
            secret_key_id="t1",
            allow_http_targets=True,
            allow_private_targets=True,
            delivery_poll_seconds=0.05,
            delivery_batch=1,
        )

    return settings


@pytest.fixture
async def service(
    database: Database,
    database_url: str,
    jetstream: JetStreamContext,
    settings_for: Callable[[str, str], WebhooksSettings],
    identity: FakeIdentity,
) -> AsyncGenerator[Service[WebhooksSettings]]:
    """webhooks, started on the test's PostgreSQL, NATS and fakes; ``database`` empties the tables later."""
    del database, jetstream
    async with identity_served(identity) as address:
        built = build(settings_for(database_url, address))
        async with built.app.router.lifespan_context(built.app):
            yield built


@pytest.fixture
async def client(service: Service[WebhooksSettings]) -> AsyncGenerator[httpx.AsyncClient]:
    """A client of webhooks' HTTP app."""
    transport = httpx.ASGITransport(app=service.app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://webhooks.test") as opened:
        yield opened


@pytest.fixture
def caller(issuer: LocalIssuer) -> Caller:
    """Subject ``2001`` of the provider's organization ``1001``."""
    return Caller(issuer)
```

`services/webhooks/tests/test_settings.py`:

```python
from typing import Final

import pytest
from pydantic import SecretStr, ValidationError

from ledger_webhooks.resources import storage_of
from ledger_webhooks.settings import WebhooksSettings

KEY: Final = "bmJubmJubmJubmJubmJubmJubmJubmJubmJubmJubmI="
COMMON: Final[dict[str, object]] = {
    "database_url": SecretStr("postgresql+psycopg://webhooks:secret@localhost/webhooks"),
    "token_issuer": "https://auth.example.com",
    "token_audience": "ledger",
    "token_jwks_url": "https://auth.example.com/oauth/v2/keys",
    "identity_address": "identity:50051",
    "secret_keys": {"k1": SecretStr(KEY)},
    "secret_key_id": "k1",
}


def test_a_deployment_delivers_to_public_https_targets_only_by_default() -> None:
    settings = WebhooksSettings.model_validate(COMMON)
    assert (settings.allow_http_targets, settings.allow_private_targets, settings.delivery_batch) == (
        False,
        False,
        20,
    )
    storage = storage_of(settings)
    assert (storage.policy.allow_http, storage.policy.allow_private) == (False, False)
    assert storage.keyring.open(storage.keyring.seal(b"s", bound_to=b"o"), bound_to=b"o") == b"s"
    assert KEY not in repr(settings)


@pytest.mark.parametrize(
    ("changes", "problem"),
    [
        ({"environment": "production", "allow_private_targets": True}, "local and test only"),
        ({"environment": "staging", "allow_http_targets": True}, "local and test only"),
        ({"secret_key_id": "k2"}, "secret_key_id must name"),
        ({"delivery_lease_seconds": 10}, "delivery_lease_seconds must exceed"),
        ({"secret_keys": {}}, "secret_keys"),
        ({"delivery_timeout_seconds": 60}, "delivery_timeout_seconds"),
    ],
    ids=["private-in-production", "http-in-staging", "no-current-key", "lease", "no-keys", "timeout"],
)
def test_a_configuration_that_cannot_deliver_safely_does_not_start(
    changes: dict[str, object], problem: str
) -> None:
    with pytest.raises(ValidationError, match=problem):
        WebhooksSettings.model_validate(COMMON | changes)


def test_a_laptop_may_deliver_to_its_own_network() -> None:
    local = WebhooksSettings.model_validate(
        COMMON | {"allow_http_targets": True, "allow_private_targets": True}
    )
    assert storage_of(local).policy.parse("http://receiver:8080/hooks").host == "receiver"
```

`services/webhooks/tests/test_service.py`:

```python
from datetime import UTC, datetime
from typing import Final, TypedDict

import pytest
from pydantic import TypeAdapter
lazy import httpx
lazy from nats.js.client import JetStreamContext

import ledger.events.invoicing.v1.events_pb2 as wire
import ledger.invoicing.v1.document_pb2 as document
from ledger_contracts import invoicing
from ledger_contracts.envelope import Envelope
from ledger_kernel.access import Permission
from ledger_kernel.ids import CompanyId, Tenant, new_id
from ledger_platform.authentication import organization_of
from ledger_testing.api import Caller, created, page
from ledger_testing.api import document as read
from ledger_testing.issuer import PROVIDER_ORGANIZATION, SUBJECT
from ledger_testing.waiting import eventually
from ledger_webhooks.domain.signing import SignatureRefusedError, verify

from webhook_remotes import MANAGER, Receiver, receiving
lazy from webhook_remotes import FakeIdentity

TENANT: Final = Tenant(organization_of(PROVIDER_ORGANIZATION), CompanyId(new_id()))
COMPANY: Final = str(TENANT.company)
SECRETS: Final = TypeAdapter(dict[str, list[dict[str, str | None]]])
BODY: Final = TypeAdapter(dict[str, object])


class Logged(TypedDict):
    number: int
    duration_ms: int
    status: int | None
    failure: str | None


class WithLog(TypedDict):
    log: list[Logged]


class Listed(TypedDict):
    type: str
    version: int


LOGGED: Final = TypeAdapter(WithLog)
CATALOG: Final = TypeAdapter(dict[str, list[Listed]])


def headers(caller: Caller, key: str | None = None, *, version: int | None = None) -> dict[str, str]:
    sent = caller.bearer() if key is None else caller.posting(key)
    sent["Ledger-Company-Id"] = COMPANY
    if version is not None:
        sent["If-Match"] = f'"{version}"'
    return sent


@pytest.fixture(autouse=True)
def granted(identity: FakeIdentity) -> None:
    identity.grants[COMPANY, SUBJECT] = MANAGER


def verifies(secret: str, sent: dict[str, str], payload: bytes) -> bool:
    try:
        verify([secret], headers=sent, body=payload, now=datetime.now(UTC))
    except SignatureRefusedError:
        return False
    return True


async def secrets_of(client: httpx.AsyncClient, caller: Caller, endpoint: str) -> list[str]:
    response = await client.get(f"/v1/webhooks/{endpoint}/secret", headers=headers(caller))
    assert response.headers["Cache-Control"] == "no-store"
    return [str(each["secret"]) for each in SECRETS.validate_json(response.content)["secrets"]]


async def test_an_invoicing_event_reaches_the_endpoint_signed(
    client: httpx.AsyncClient, caller: Caller, jetstream: JetStreamContext
) -> None:
    receiver = Receiver()
    async with receiving(receiver) as url:
        body = {"url": url, "event_types": ["invoice.*"], "description": "ERP"}
        response = await client.post("/v1/webhooks", json=body, headers=headers(caller, "w-1"))
        assert response.status_code == 201, response.text
        endpoint = str(read(response)["id"])
        assert "whsec_" not in response.text
        (secret,) = await secrets_of(client, caller, endpoint)
        await created(client, "/v1/webhooks", {"url": url, "event_types": ["*"]}, headers(caller, "w-2"))
        voided = wire.DocumentVoided(
            document_id=str(new_id()), kind=document.DOCUMENT_KIND_INVOICE, version=3, reason="Duplicated"
        )
        untold = Envelope.wrap(voided, source="invoicing", tenant=None)
        await jetstream.publish(invoicing.DOCUMENT_VOIDED, untold.data, headers=untold.headers())
        envelope = Envelope.wrap(voided, source="invoicing", tenant=TENANT)
        await jetstream.publish(invoicing.DOCUMENT_VOIDED, envelope.data, headers=envelope.headers())

        async def delivered() -> bool:
            return len(receiver.received) == 2

        await eventually(delivered)
    (sent, payload), (other, _) = receiver.received
    assert other["webhook-id"] == sent["webhook-id"] == str(envelope.id)
    signed = sent if verifies(secret, sent, payload) else other
    verify([secret], headers=signed, body=payload, now=datetime.now(UTC))
    told = BODY.validate_json(payload)
    assert (told["type"], told["version"], told["company_id"]) == ("invoice.voided", 1, COMPANY)
    listed = page(await client.get(f"/v1/webhooks/{endpoint}/deliveries", headers=headers(caller)))
    assert len(listed["items"]) == 1
    (delivery,) = listed["items"]

    async def recorded() -> bool:
        one = read(
            await client.get(f"/v1/webhooks/{endpoint}/deliveries/{delivery['id']}", headers=headers(caller))
        )
        return one["state"] == "delivered"

    await eventually(recorded)
    response = await client.get(
        f"/v1/webhooks/{endpoint}/deliveries/{delivery['id']}", headers=headers(caller)
    )
    (attempt,) = LOGGED.validate_json(response.content)["log"]
    assert (attempt["number"], attempt["status"], attempt["failure"]) == (1, 204, None)
    assert attempt["duration_ms"] >= 0


async def test_a_test_delivery_and_a_rotation_with_overlap(client: httpx.AsyncClient, caller: Caller) -> None:
    receiver = Receiver()
    async with receiving(receiver) as url:
        endpoint = await created(
            client, "/v1/webhooks", {"url": url, "event_types": ["invoice.voided"]}, headers(caller, "t-1")
        )
        (first,) = await secrets_of(client, caller, endpoint)
        stale = await client.post(
            f"/v1/webhooks/{endpoint}/secret/rotate", json={}, headers=headers(caller, "t-2", version=9)
        )
        assert stale.status_code == 412
        rotated = await client.post(
            f"/v1/webhooks/{endpoint}/secret/rotate",
            json={"overlap_hours": 2},
            headers=headers(caller, "t-3", version=1),
        )
        assert (rotated.status_code, read(rotated)["version"]) == (200, 2)
        assert read(rotated)["previous_secret_expires_at"] is not None
        second, previous = await secrets_of(client, caller, endpoint)
        assert previous == first
        tested = await client.post(f"/v1/webhooks/{endpoint}/test", headers=headers(caller, "t-4"))
        assert tested.status_code == 202
        assert tested.headers["Location"].startswith(f"/v1/webhooks/{endpoint}/deliveries/")

        async def delivered() -> bool:
            return len(receiver.received) == 1

        await eventually(delivered)
    ((sent, payload),) = receiver.received
    assert sent["webhook-signature"].count("v1,") == 2
    for secret in (first, second):
        verify([secret], headers=sent, body=payload, now=datetime.now(UTC))
    assert BODY.validate_json(payload)["type"] == "webhook.test"


async def test_an_endpoint_is_changed_and_deleted_at_its_version(
    client: httpx.AsyncClient, caller: Caller
) -> None:
    endpoint = await created(
        client,
        "/v1/webhooks",
        {"url": "https://erp.example.com/hooks", "event_types": ["*"]},
        headers(caller, "c-1"),
    )
    path = f"/v1/webhooks/{endpoint}"
    changes = {"url": "https://erp.example.com/v2", "event_types": ["invoice.fiscal.*"], "enabled": False}
    changed = await client.put(path, json=changes, headers=headers(caller, version=1))
    assert (changed.status_code, changed.headers["ETag"], read(changed)["enabled"]) == (200, '"2"', False)
    assert (await client.get(path, headers=headers(caller))).headers["ETag"] == '"2"'
    assert (await client.delete(path, headers=headers(caller, version=1))).status_code == 412
    assert (await client.delete(path, headers=headers(caller, version=2))).status_code == 204
    assert (await client.get(path, headers=headers(caller))).status_code == 404
    assert page(await client.get("/v1/webhooks", headers=headers(caller)))["items"] == []


async def test_what_cannot_be_an_endpoint_is_refused(client: httpx.AsyncClient, caller: Caller) -> None:
    for body, code in (
        ({"url": "ftp://erp.example.com/", "event_types": ["*"]}, "webhook_target_refused"),
        ({"url": "https://erp.example.com/", "event_types": ["payment.*"]}, "webhook_filter_invalid"),
    ):
        response = await client.post("/v1/webhooks", json=body, headers=headers(caller, code))
        assert (response.status_code, read(response)["code"]) == (422, code)
        assert "erp.example.com" not in str(read(response)["detail"])


async def test_who_may_do_what(client: httpx.AsyncClient, caller: Caller, identity: FakeIdentity) -> None:
    catalog = CATALOG.validate_json(
        (await client.get("/v1/webhooks/event-types", headers=caller.bearer())).content
    )
    assert ("invoice.fiscal.accepted", 1) in [(each["type"], each["version"]) for each in catalog["items"]]
    identity.grants[COMPANY, SUBJECT] = frozenset({Permission.COMPANY_READ})
    refused = await client.post(
        "/v1/webhooks",
        json={"url": "https://a.example.com/", "event_types": ["*"]},
        headers=headers(caller, "p-1"),
    )
    assert (refused.status_code, read(refused)["code"]) == (403, "permission_denied")
    stranger = Caller(caller.issuer, subject="2002")
    assert (await client.get("/v1/webhooks", headers=headers(stranger))).status_code == 404
    assert (await client.get(f"/v1/webhooks/{new_id()}", headers=headers(caller))).status_code == 404


async def test_webhooks_is_ready_once_its_schema_is_this_version_s(client: httpx.AsyncClient) -> None:
    response = await client.get("/readyz")
    assert (response.status_code, read(response)["checks"]) == (
        200,
        {"database": True, "nats": True, "workers": True, "schema": True, "migrations": True},
    )
```

`services/webhooks/tests/test_openapi.py`:

```python
from pathlib import Path
from typing import Final, TypedDict

from pydantic import TypeAdapter
lazy import pytest

from ledger_webhooks import openapi
from ledger_webhooks.main import create_app
from ledger_webhooks.openapi import document
from ledger_webhooks.resources import VERSION

PUBLISHED: Final = Path(__file__).parents[1] / "openapi.json"
Parameter = TypedDict("Parameter", {"name": str, "in": str, "required": bool})


class Answer(TypedDict, total=False):
    content: dict[str, dict[str, object]]


class Operation(TypedDict, total=False):
    parameters: list[Parameter]
    security: list[dict[str, list[str]]]
    responses: dict[str, Answer]


class Document(TypedDict):
    openapi: str
    paths: dict[str, dict[str, Operation]]


DOCUMENT: Final = TypeAdapter(Document)


def test_the_published_document_is_the_one_the_code_makes() -> None:
    assert PUBLISHED.read_text(encoding="utf-8") == document(), "regenerate it: uv run poe openapi"


def test_the_document_is_openapi_3_1_and_every_post_takes_an_idempotency_key() -> None:
    parsed = DOCUMENT.validate_json(document())
    assert parsed["openapi"].startswith("3.1.")
    posts = [operations["post"] for operations in parsed["paths"].values() if "post" in operations]
    assert posts
    for post in posts:
        assert {"name": "Idempotency-Key", "in": "header", "required": True} in post.get("parameters", [])


def test_every_operation_is_for_bearers_of_a_token() -> None:
    parsed = DOCUMENT.validate_json(document())
    for operations in parsed["paths"].values():
        for operation in operations.values():
            assert operation.get("security") == [{"HTTPBearer": []}]


def test_every_error_an_operation_documents_is_a_problem_document() -> None:
    parsed = DOCUMENT.validate_json(document())
    errors = [
        answer
        for operations in parsed["paths"].values()
        for operation in operations.values()
        for status, answer in operation.get("responses", {}).items()
        if status.startswith(("4", "5"))
    ]
    assert errors
    for answer in errors:
        assert list(answer.get("content", {})) == ["application/problem+json"]


def test_the_command_line_writes_the_document(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "openapi.json"
    monkeypatch.setattr("sys.argv", ["ledger-webhooks-openapi", str(target)])
    assert openapi.main() == 0
    assert target.read_text(encoding="utf-8") == document()


def test_the_command_line_needs_a_path(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.argv", ["ledger-webhooks-openapi"])
    assert openapi.main() == 2
    assert capsys.readouterr().err == "usage: ledger-webhooks-openapi PATH\n"


def test_granians_factory_builds_the_app_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in {
        "LEDGER_DATABASE_URL": "postgresql+psycopg://webhooks:secret@localhost/webhooks",
        "LEDGER_TOKEN_ISSUER": "https://issuer.example",
        "LEDGER_TOKEN_AUDIENCE": "ledger",
        "LEDGER_TOKEN_JWKS_URL": "https://issuer.example/oauth/v2/keys",
        "LEDGER_IDENTITY_ADDRESS": "identity:50051",
        "LEDGER_SECRET_KEYS": '{"k1": "dGVzdHRlc3R0ZXN0dGVzdHRlc3R0ZXN0dGVzdHRlc3Q="}',
        "LEDGER_SECRET_KEY_ID": "k1",
    }.items():
        monkeypatch.setenv(name, value)
    app = create_app()
    assert (app.title, app.version) == ("webhooks", VERSION)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest services/webhooks/tests/test_settings.py services/webhooks/tests/test_service.py services/webhooks/tests/test_openapi.py -q --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'ledger_webhooks.resources'`.

- [ ] **Step 4: Implement**

`services/webhooks/src/ledger_webhooks/resources.py`:

```python
"""What webhooks talks to beside the chassis, opened when it starts and closed when it stops.

One object: the identity provider's keys, a gRPC channel to identity (the callers' permissions), the keyring
endpoint secrets are sealed with, the deployment's target policy, and the HTTP client deliveries go through.
"""

from contextlib import asynccontextmanager
from dataclasses import dataclass
from importlib.metadata import version
from typing import Final, final
lazy from collections.abc import AsyncGenerator

from ledger_platform.authorization import CachedPermissions
from ledger_platform.grpc_client import Channel
from ledger_platform.identity_client import GET_PERMISSIONS, RemotePermissions
from ledger_webhooks.adapters import network
from ledger_webhooks.adapters.network import HttpSender
from ledger_webhooks.adapters.postgres.units import Storage
from ledger_webhooks.adapters.sealing import Keyring
from ledger_webhooks.domain.targets import TargetPolicy
lazy from ledger_platform.authentication import HttpKeys
lazy from ledger_webhooks.settings import WebhooksSettings

VERSION: Final = version("ledger-webhooks")
USER_AGENT: Final = f"ledger-webhooks/{VERSION}"


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class WebhooksResources:
    """What the routes, handlers, workers and checks of a running webhooks use (see the module)."""

    permissions: CachedPermissions
    storage: Storage
    sender: HttpSender


def storage_of(settings: WebhooksSettings) -> Storage:
    """The keyring and the target policy ``settings`` describe."""
    keys = {name: secret.get_secret_value() for name, secret in settings.secret_keys.items()}
    return Storage(
        keyring=Keyring.of(keys, settings.secret_key_id),
        policy=TargetPolicy(
            allow_http=settings.allow_http_targets, allow_private=settings.allow_private_targets
        ),
    )


@asynccontextmanager
async def opened(settings: WebhooksSettings, *, keys: HttpKeys) -> AsyncGenerator[WebhooksResources]:
    """Every resource of webhooks, open for the length of the block."""
    identity = Channel(
        settings.identity_address,
        caller=settings.service_name,
        version=VERSION,
        idempotent=(GET_PERMISSIONS,),
    )
    storage = storage_of(settings)
    client = network.client(
        connect=settings.delivery_connect_seconds, total=settings.delivery_timeout_seconds
    )
    async with keys.opened(), identity.opened(), client:
        yield WebhooksResources(
            permissions=CachedPermissions(RemotePermissions(identity.current())),
            storage=storage,
            sender=HttpSender(client, admits=storage.policy.admits),
        )
```

`services/webhooks/src/ledger_webhooks/api/__init__.py`:

```python
"""webhooks' interfaces: its public HTTP API, the events it consumes, its sending worker."""
```

`services/webhooks/src/ledger_webhooks/api/context.py`:

```python
"""What every route of webhooks shares: the runtime, the caller, its access to the company, idempotency.

The company is named by the ``Ledger-Company-Id`` header; the caller's permissions there come from identity.
Managing endpoints and reading their secrets takes ``company.manage``; reading them and their deliveries,
``company.read``.
"""

from contextlib import contextmanager
from dataclasses import dataclass
from http import HTTPStatus
from typing import Annotated, ClassVar, Final, final
lazy from collections.abc import Callable, Coroutine, Generator
lazy from contextlib import AbstractAsyncContextManager

from fastapi import Depends
from pydantic import BaseModel, ConfigDict
lazy from fastapi import Request

from ledger_platform.routes import attempt_of, authenticated, company_in_header, precondition_failed
from ledger_platform.web import PROBLEM_JSON
from ledger_webhooks.adapters.postgres.units import bind
from ledger_webhooks.application.endpoints import StaleVersionError
from ledger_webhooks.resources import WebhooksResources
lazy from ledger_platform.authentication import TokenVerifier
lazy from ledger_platform.authorization import CompanyAccess, PermissionSource
lazy from ledger_platform.database import UnitOfWork
lazy from ledger_platform.idempotency import Attempt, Idempotency
lazy from ledger_platform.routes import Caller, InCompany
lazy from ledger_platform.service import Runtime, RuntimeDependency
lazy from ledger_webhooks.adapters.postgres.units import Storage
lazy from ledger_webhooks.application.ports import WebhooksUnit
lazy from ledger_webhooks.settings import WebhooksSettings


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
class Call:
    """One request in one company: the request, the caller's access there, and the running service."""

    request: Request
    access: CompanyAccess
    runtime: Runtime[WebhooksSettings]

    @property
    def storage(self) -> Storage:
        """The keyring and the deployment's target policy."""
        return self.runtime.resources(WebhooksResources).storage

    def transaction(self) -> AbstractAsyncContextManager[UnitOfWork]:
        """A transaction scoped to the request's company."""
        return self.runtime.database.transaction(self.access.tenant)

    def unit(self, transaction: UnitOfWork) -> WebhooksUnit:
        """Webhooks' unit of work over ``transaction``."""
        return bind(transaction, self.storage)

    def attempt(self, key: str, body: BaseModel | None = None) -> Attempt:
        """The caller's use of ``key`` for this request, with ``body``."""
        return attempt_of(self.request, self.access.principal, key, body)


type CallDependency = Callable[
    [Request, CompanyAccess, Runtime[WebhooksSettings]], Coroutine[object, object, Call]
]


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class ApiContext:
    """The dependencies every router shares, built once with the app."""

    caller: Caller
    call: CallDependency
    idempotency: Idempotency

    @classmethod
    def build(
        cls,
        *,
        runtime: RuntimeDependency[WebhooksSettings],
        verifier: TokenVerifier,
        idempotency: Idempotency,
    ) -> ApiContext:
        """The context of webhooks' routes: the caller from ``verifier``, its access from identity."""
        caller = authenticated(verifier)

        async def permission_source(
            current: Annotated[Runtime[WebhooksSettings], Depends(runtime)],
        ) -> PermissionSource:
            return current.resources(WebhooksResources).permissions

        in_company: InCompany = company_in_header(caller, permission_source)

        async def call(
            request: Request,
            access: Annotated[CompanyAccess, Depends(in_company)],
            current: Annotated[Runtime[WebhooksSettings], Depends(runtime)],
        ) -> Call:
            return Call(request=request, access=access, runtime=current)

        return cls(caller=caller, call=call, idempotency=idempotency)


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


@contextmanager
def precondition_checked() -> Generator[None]:
    """Inside the block, a change refused for ``If-Match`` (``StaleVersionError``) answers 412."""
    try:
        yield
    except StaleVersionError as error:
        raise precondition_failed() from error
```

`services/webhooks/src/ledger_webhooks/api/models.py`:

```python
"""The shapes of webhooks' public API. What clients send has its unknown fields refused.

A secret is never in an endpoint's view, nor in any answer an ``Idempotency-Key`` stores (stored answers live
24 hours in a table): a client reads it from ``GET /v1/webhooks/{id}/secret``, which nothing stores.
"""

from datetime import datetime, timedelta
from typing import Annotated, ClassVar, Final, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ledger_webhooks.domain.catalog import DESCRIPTIONS, MAX_FILTERS, VERSIONS, EventType
from ledger_webhooks.domain.delivery import DeliveryState, Failure
from ledger_webhooks.domain.endpoint import MAX_DESCRIPTION
from ledger_webhooks.domain.signing import MAX_OVERLAP, MIN_OVERLAP
from ledger_webhooks.domain.targets import MAX_URL
lazy from ledger_webhooks.domain.delivery import Attempt, Delivery
lazy from ledger_webhooks.domain.endpoint import Endpoint
lazy from ledger_webhooks.domain.signing import Secret

DEFAULT_OVERLAP_HOURS: Final = 24
_HOUR: Final = timedelta(hours=1)
_MILLISECOND: Final = timedelta(milliseconds=1)

type Url = Annotated[str, Field(min_length=1, max_length=MAX_URL, examples=["https://erp.example.com/hooks"])]
type Filter = Annotated[str, Field(min_length=1, max_length=64, examples=["invoice.fiscal.*"])]


class _Shape(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)


class EndpointShape(_Shape):
    """An endpoint as a client says it: the body of ``POST`` and ``PUT``."""

    url: Url
    event_types: Annotated[list[Filter], Field(min_length=1, max_length=MAX_FILTERS)]
    """Types (``invoice.voided``), prefixes (``invoice.fiscal.*``) or ``*``."""
    enabled: bool = True
    description: Annotated[str, Field(max_length=MAX_DESCRIPTION)] | None = None


class Rotation(_Shape):
    """How long the current secret keeps signing beside the new one."""

    overlap_hours: Annotated[int, Field(ge=MIN_OVERLAP // _HOUR, le=MAX_OVERLAP // _HOUR)] = (
        DEFAULT_OVERLAP_HOURS
    )


class EndpointView(BaseModel):
    """An endpoint as the API shows it (never its secrets); its version is the ``ETag``."""

    id: UUID
    url: str
    event_types: list[str]
    enabled: bool
    description: str | None
    version: int
    previous_secret_expires_at: datetime | None
    """While a rotation's overlap lasts: when the previous secret stops signing."""
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, endpoint: Endpoint) -> Self:
        """The view of ``endpoint``."""
        previous = endpoint.secrets.previous
        return cls(
            id=endpoint.id,
            url=endpoint.target.url,
            event_types=sorted(endpoint.filters.patterns),
            enabled=endpoint.enabled,
            description=endpoint.description,
            version=endpoint.version,
            previous_secret_expires_at=None if previous is None else previous.expires_at,
            created_at=endpoint.created_at,
            updated_at=endpoint.updated_at,
        )


class EndpointPage(BaseModel):
    """A page of endpoints, oldest first."""

    items: list[EndpointView]
    next_cursor: str | None


class SecretView(BaseModel):
    """One secret in force: what a client configures its verifier with."""

    secret: str
    expires_at: datetime | None

    @classmethod
    def of(cls, secret: Secret) -> Self:
        """The view of ``secret``."""
        return cls(secret=secret.text, expires_at=secret.expires_at)


class SecretsView(BaseModel):
    """The secrets that sign the endpoint's requests now, the current one first."""

    secrets: list[SecretView]


class AttemptView(BaseModel):
    """One attempt, as the log keeps it: no body."""

    number: int
    at: datetime
    duration_ms: int
    status: int | None
    failure: Failure | None

    @classmethod
    def of(cls, attempt: Attempt) -> Self:
        """The view of ``attempt``."""
        return cls(
            number=attempt.number,
            at=attempt.at,
            duration_ms=attempt.duration // _MILLISECOND,
            status=attempt.status,
            failure=attempt.failure,
        )


class DeliveryView(BaseModel):
    """A delivery and, when read alone, its attempts."""

    id: UUID
    endpoint_id: UUID
    event_id: UUID
    event_type: EventType
    state: DeliveryState
    attempts: int
    next_attempt_at: datetime | None
    last_status: int | None
    last_failure: Failure | None
    created_at: datetime
    log: list[AttemptView] | None = None

    @classmethod
    def of(cls, delivery: Delivery, log: tuple[Attempt, ...] | None = None) -> Self:
        """The view of ``delivery``, with ``log`` when given."""
        return cls(
            id=delivery.id,
            endpoint_id=delivery.endpoint,
            event_id=delivery.event,
            event_type=delivery.event_type,
            state=delivery.state,
            attempts=delivery.attempts,
            next_attempt_at=delivery.next_attempt_at,
            last_status=delivery.last_status,
            last_failure=delivery.last_failure,
            created_at=delivery.created_at,
            log=None if log is None else [AttemptView.of(each) for each in log],
        )


class DeliveryPage(BaseModel):
    """A page of an endpoint's deliveries, oldest first."""

    items: list[DeliveryView]
    next_cursor: str | None


class EventTypeView(BaseModel):
    """A public event a webhook can carry."""

    type: EventType
    version: int
    description: str


class EventTypes(BaseModel):
    """Every public event."""

    items: list[EventTypeView]

    @classmethod
    def all(cls) -> Self:
        """The catalog."""
        return cls(
            items=[
                EventTypeView(type=kind, version=VERSIONS[kind], description=DESCRIPTIONS[kind])
                for kind in EventType
            ]
        )
```

`services/webhooks/src/ledger_webhooks/api/endpoints.py`:

```python
"""``/v1/webhooks``: a company's endpoints, their secrets, their tests, their deliveries; the event catalog.

Every ``POST`` takes an ``Idempotency-Key``; changes of an endpoint name its version in ``If-Match``. A secret
is read only from ``GET /v1/webhooks/{id}/secret`` (``Cache-Control: no-store``): no answer an idempotency key
stores ever holds one. A test is a ``webhook.test`` event delivered like any other: ``202`` with the delivery
to follow at ``Location``.
"""

import secrets
from datetime import timedelta
from http import HTTPStatus
from typing import Annotated, Final
lazy from uuid import UUID

from fastapi import APIRouter, Depends, Response

from ledger_kernel.access import Permission
from ledger_platform.routes import answer, etag, idempotency_key, if_match, json_response, page_request
from ledger_webhooks.api.context import precondition_checked, problems
from ledger_webhooks.api.models import (
    DeliveryPage,
    DeliveryView,
    EndpointPage,
    EndpointView,
    EventTypes,
    SecretsView,
    SecretView,
)
from ledger_webhooks.application import endpoints
from ledger_webhooks.domain.catalog import Filters
from ledger_webhooks.domain.endpoint import Subscription
from ledger_webhooks.domain.ids import DeliveryId, EndpointId
from ledger_webhooks.domain.signing import KEY_BYTES
lazy from ledger_kernel.access import Principal
lazy from ledger_kernel.pagination import PageRequest
lazy from ledger_platform.idempotency import StoredResponse
lazy from ledger_platform.routes import IfMatch
lazy from ledger_webhooks.api.context import ApiContext, Call
lazy from ledger_webhooks.api.models import EndpointShape, Rotation
lazy from ledger_webhooks.domain.endpoint import Endpoint

PREFIX: Final = "/v1/webhooks"
TAG: Final = "webhooks"


def new_key() -> bytes:
    """A new secret's key, from the operating system's random source."""
    return secrets.token_bytes(KEY_BYTES)


def _wanted(current: Call, body: EndpointShape) -> Subscription:
    return Subscription(
        target=current.storage.policy.parse(body.url),
        filters=Filters.parse(body.event_types),
        enabled=body.enabled,
        description=body.description,
    )


def _stored(status: HTTPStatus, endpoint: Endpoint) -> StoredResponse:
    headers = {"ETag": etag(endpoint.version)}
    if status is HTTPStatus.CREATED:
        headers["Location"] = f"{PREFIX}/{endpoint.id}"
    return json_response(status, EndpointView.of(endpoint), headers)


def _catalog(context: ApiContext) -> APIRouter:
    catalog = APIRouter(prefix=PREFIX, tags=[TAG])
    caller = context.caller

    @catalog.get("/event-types", response_model=EventTypes, responses=problems(401))
    async def event_types(principal: Annotated[Principal, Depends(caller)]) -> EventTypes:
        """Every public event a webhook can carry, with its version."""
        del principal
        return EventTypes.all()

    return catalog


def _endpoints(context: ApiContext) -> APIRouter:
    routes = APIRouter(prefix=PREFIX, tags=[TAG])
    call, idempotency = context.call, context.idempotency

    @routes.post(
        "",
        status_code=HTTPStatus.CREATED,
        response_model=EndpointView,
        responses=problems(401, 403, 404, 409, 422),
    )
    async def register(
        body: EndpointShape,
        current: Annotated[Call, Depends(call)],
        key: Annotated[str, Depends(idempotency_key)],
    ) -> Response:
        """Register an endpoint, with a new secret (read it from ``/secret``)."""
        current.access.require(Permission.COMPANY_MANAGE)
        wanted = _wanted(current, body)
        async with current.transaction() as transaction:

            async def registered() -> StoredResponse:
                endpoint = await endpoints.register(current.unit(transaction), wanted, keys=new_key)
                return _stored(HTTPStatus.CREATED, endpoint)

            outcome = await idempotency.once(transaction, current.attempt(key, body), registered)
        return answer(outcome)

    @routes.get("", response_model=EndpointPage, responses=problems(401, 403, 404, 422))
    async def list_endpoints(
        current: Annotated[Call, Depends(call)], page: Annotated[PageRequest, Depends(page_request)]
    ) -> EndpointPage:
        """The company's endpoints, oldest first."""
        current.access.require(Permission.COMPANY_READ)
        async with current.transaction() as transaction:
            found = await endpoints.endpoints(current.unit(transaction), page)
        return EndpointPage(
            items=[EndpointView.of(each) for each in found.items], next_cursor=found.next_cursor
        )

    @routes.get("/{endpoint_id}", response_model=EndpointView, responses=problems(401, 403, 404, 422))
    async def read_endpoint(
        endpoint_id: UUID, current: Annotated[Call, Depends(call)], response: Response
    ) -> EndpointView:
        """An endpoint, with its version as ``ETag``."""
        current.access.require(Permission.COMPANY_READ)
        async with current.transaction() as transaction:
            found = await endpoints.endpoint_of(current.unit(transaction), EndpointId(endpoint_id))
        response.headers["ETag"] = etag(found.version)
        return EndpointView.of(found)

    @routes.put(
        "/{endpoint_id}", response_model=EndpointView, responses=problems(401, 403, 404, 412, 422, 428)
    )
    async def change_endpoint(
        endpoint_id: UUID,
        body: EndpointShape,
        current: Annotated[Call, Depends(call)],
        match: Annotated[IfMatch, Depends(if_match)],
        response: Response,
    ) -> EndpointView:
        """Change where an endpoint is, which events it receives, whether it is on."""
        current.access.require(Permission.COMPANY_MANAGE)
        wanted = _wanted(current, body)
        with precondition_checked():
            async with current.transaction() as transaction:
                unit = current.unit(transaction)
                changed = await endpoints.change(unit, EndpointId(endpoint_id), wanted, admits=match.admits)
        response.headers["ETag"] = etag(changed.version)
        return EndpointView.of(changed)

    @routes.delete(
        "/{endpoint_id}", status_code=HTTPStatus.NO_CONTENT, responses=problems(401, 403, 404, 412, 422, 428)
    )
    async def remove_endpoint(
        endpoint_id: UUID,
        current: Annotated[Call, Depends(call)],
        match: Annotated[IfMatch, Depends(if_match)],
    ) -> Response:
        """Delete an endpoint: it receives nothing more; its deliveries' log stays."""
        current.access.require(Permission.COMPANY_MANAGE)
        with precondition_checked():
            async with current.transaction() as transaction:
                await endpoints.remove(
                    current.unit(transaction), EndpointId(endpoint_id), admits=match.admits
                )
        return Response(status_code=HTTPStatus.NO_CONTENT)

    return routes


def _secrets(context: ApiContext) -> APIRouter:
    routes = APIRouter(prefix=PREFIX, tags=[TAG])
    call, idempotency = context.call, context.idempotency

    @routes.get("/{endpoint_id}/secret", response_model=SecretsView, responses=problems(401, 403, 404, 422))
    async def read_secrets(
        endpoint_id: UUID, current: Annotated[Call, Depends(call)], response: Response
    ) -> SecretsView:
        """The secrets that sign the endpoint's requests now (two during a rotation's overlap)."""
        current.access.require(Permission.COMPANY_MANAGE)
        async with current.transaction() as transaction:
            unit = current.unit(transaction)
            found = await endpoints.endpoint_of(unit, EndpointId(endpoint_id))
        in_force = found.secrets.in_force(unit.now)
        response.headers["Cache-Control"] = "no-store"
        return SecretsView(secrets=[SecretView.of(secret) for secret in in_force])

    @routes.post(
        "/{endpoint_id}/secret/rotate",
        response_model=EndpointView,
        responses=problems(401, 403, 404, 412, 422, 428),
    )
    async def rotate_secret(
        endpoint_id: UUID,
        body: Rotation,
        current: Annotated[Call, Depends(call)],
        match: Annotated[IfMatch, Depends(if_match)],
        key: Annotated[str, Depends(idempotency_key)],
    ) -> Response:
        """A new secret; the current one keeps signing for ``overlap_hours``."""
        current.access.require(Permission.COMPANY_MANAGE)
        overlap = timedelta(hours=body.overlap_hours)
        with precondition_checked():
            async with current.transaction() as transaction:

                async def rotated() -> StoredResponse:
                    unit = current.unit(transaction)
                    endpoint = await endpoints.rotate(
                        unit, EndpointId(endpoint_id), overlap=overlap, keys=new_key, admits=match.admits
                    )
                    return _stored(HTTPStatus.OK, endpoint)

                outcome = await idempotency.once(transaction, current.attempt(key, body), rotated)
        return answer(outcome)

    return routes


def _deliveries(context: ApiContext) -> APIRouter:
    routes = APIRouter(prefix=PREFIX, tags=[TAG])
    call, idempotency = context.call, context.idempotency

    @routes.post(
        "/{endpoint_id}/test",
        status_code=HTTPStatus.ACCEPTED,
        response_model=DeliveryView,
        responses=problems(401, 403, 404, 422),
    )
    async def test_endpoint(
        endpoint_id: UUID,
        current: Annotated[Call, Depends(call)],
        key: Annotated[str, Depends(idempotency_key)],
    ) -> Response:
        """Send the endpoint a ``webhook.test`` event; follow its delivery at ``Location``."""
        current.access.require(Permission.COMPANY_MANAGE)
        async with current.transaction() as transaction:

            async def queued() -> StoredResponse:
                delivery = await endpoints.send_test(current.unit(transaction), EndpointId(endpoint_id))
                location = f"{PREFIX}/{endpoint_id}/deliveries/{delivery.id}"
                return json_response(HTTPStatus.ACCEPTED, DeliveryView.of(delivery), {"Location": location})

            outcome = await idempotency.once(transaction, current.attempt(key), queued)
        return answer(outcome)

    @routes.get(
        "/{endpoint_id}/deliveries", response_model=DeliveryPage, responses=problems(401, 403, 404, 422)
    )
    async def list_deliveries(
        endpoint_id: UUID,
        current: Annotated[Call, Depends(call)],
        page: Annotated[PageRequest, Depends(page_request)],
    ) -> DeliveryPage:
        """The endpoint's deliveries, oldest first."""
        current.access.require(Permission.COMPANY_READ)
        async with current.transaction() as transaction:
            found = await endpoints.deliveries(current.unit(transaction), EndpointId(endpoint_id), page)
        return DeliveryPage(
            items=[DeliveryView.of(each) for each in found.items], next_cursor=found.next_cursor
        )

    @routes.get(
        "/{endpoint_id}/deliveries/{delivery_id}",
        response_model=DeliveryView,
        responses=problems(401, 403, 404, 422),
    )
    async def read_delivery(
        endpoint_id: UUID, delivery_id: UUID, current: Annotated[Call, Depends(call)]
    ) -> DeliveryView:
        """A delivery, with every attempt (statuses and durations; never a body)."""
        current.access.require(Permission.COMPANY_READ)
        async with current.transaction() as transaction:
            unit = current.unit(transaction)
            found, log = await endpoints.delivery_of(unit, EndpointId(endpoint_id), DeliveryId(delivery_id))
        return DeliveryView.of(found, log)

    return routes


def routers(context: ApiContext) -> tuple[APIRouter, ...]:
    """Webhooks' routes, under ``/v1/webhooks``."""
    return (_catalog(context), _endpoints(context), _secrets(context), _deliveries(context))
```

`services/webhooks/src/ledger_webhooks/api/consumers.py`:

```python
"""The events webhooks consumes: invoicing's, each fanned out to the endpoints that receive it.

One durable consumer, ``webhooks-invoicing``, reads ``invoicing.document.>`` of invoicing's stream, translates
each event into its public one (``ledger_webhooks.adapters.translation``) and makes its deliveries in the same
transaction as its inbox claim. Another service's public events come with a consumer of their own here.
"""

from typing import Final
lazy from collections.abc import Sequence

from ledger_contracts import invoicing
from ledger_platform.consumer import ConsumerSpec
from ledger_webhooks.adapters.postgres.units import bind
from ledger_webhooks.adapters.translation import public_event
from ledger_webhooks.application.fanout import fan_out
from ledger_webhooks.resources import WebhooksResources
lazy from ledger_contracts.envelope import Envelope
lazy from ledger_platform.database import UnitOfWork
lazy from ledger_platform.service import Runtime
lazy from ledger_webhooks.settings import WebhooksSettings

INVOICING_EVENTS: Final = "webhooks-invoicing"


def consumers(runtime: Runtime[WebhooksSettings]) -> Sequence[ConsumerSpec]:
    """Webhooks' durable consumers, over the running service."""
    storage = runtime.resources(WebhooksResources).storage

    async def invoicing_event(envelope: Envelope, unit: UnitOfWork) -> None:
        event = public_event(envelope)
        if event is not None:
            await fan_out(bind(unit, storage), event)

    return (
        ConsumerSpec(
            name=INVOICING_EVENTS,
            stream=invoicing.STREAM,
            subject=invoicing.SUBJECTS[0],
            handler=invoicing_event,
        ),
    )
```

`services/webhooks/src/ledger_webhooks/api/workers.py`:

```python
"""webhooks' background worker, supervised by the chassis (restarted after a failure).

``deliveries``: claims what is due (up to ``delivery_batch``), sends it, records it; when a pass found less
than a full batch it waits ``delivery_poll_seconds`` before the next one. Every replica runs one: the leases
share the work.
"""

import asyncio
import random
from datetime import timedelta
from typing import Never
lazy from collections.abc import Mapping

from ledger_kernel.clock import utc_now
from ledger_webhooks.adapters.postgres.repositories import PgDueDeliveries
from ledger_webhooks.adapters.postgres.units import PgUnits
from ledger_webhooks.application.sending import Sending
from ledger_webhooks.domain.delivery import Schedule
from ledger_webhooks.resources import USER_AGENT, WebhooksResources
lazy from ledger_platform.service import Runtime
lazy from ledger_platform.supervision import Work
lazy from ledger_webhooks.settings import WebhooksSettings


def sending_of(runtime: Runtime[WebhooksSettings]) -> Sending:
    """The sending worker's work, over the running service."""
    settings, resources = runtime.settings, runtime.resources(WebhooksResources)
    return Sending(
        units=PgUnits(runtime.database, resources.storage),
        due=PgDueDeliveries(runtime.database),
        sender=resources.sender,
        schedule=Schedule(random.random),
        clock=utc_now,
        lease=timedelta(seconds=settings.delivery_lease_seconds),
        limit=settings.delivery_batch,
        user_agent=USER_AGENT,
    )


def workers(runtime: Runtime[WebhooksSettings]) -> Mapping[str, Work]:
    """Webhooks' workers, over the running service."""
    sending = sending_of(runtime)
    batch, poll = runtime.settings.delivery_batch, runtime.settings.delivery_poll_seconds

    async def deliveries() -> Never:
        while True:
            if await sending.pass_once() < batch:
                await asyncio.sleep(poll)

    return {"deliveries": deliveries}
```

`services/webhooks/src/ledger_webhooks/main.py`:

```python
"""webhooks' composition root: settings, adapters and interfaces wired into one chassis ``Service``."""

lazy from collections.abc import Sequence
lazy from contextlib import AbstractAsyncContextManager

lazy from fastapi import APIRouter, FastAPI

from ledger_platform.authentication import HttpKeys, PublishedKeys, TokenPolicy, TokenVerifier
from ledger_platform.idempotency import Idempotency
from ledger_platform.service import Events, Interfaces, Service
from ledger_webhooks.adapters.postgres.migrate import head, require_head
from ledger_webhooks.api import endpoints
from ledger_webhooks.api.consumers import consumers
from ledger_webhooks.api.context import ApiContext
from ledger_webhooks.api.workers import workers
from ledger_webhooks.resources import VERSION, opened
from ledger_webhooks.settings import WebhooksSettings
lazy from ledger_platform.service import Runtime, RuntimeDependency
lazy from ledger_webhooks.resources import WebhooksResources


def build(settings: WebhooksSettings) -> Service[WebhooksSettings]:
    """The webhooks service ``settings`` describe: built, not started (its app's lifespan starts it).

    Its readiness check beside the chassis's: ``migrations`` (the database is at this version's revision).
    """
    policy = TokenPolicy.of(settings)
    keys = HttpKeys()
    verifier = TokenVerifier(policy, keys=PublishedKeys(policy, fetch=keys))
    idempotency = Idempotency()
    expected = head()

    def http(runtime: RuntimeDependency[WebhooksSettings]) -> Sequence[APIRouter]:
        return endpoints.routers(
            ApiContext.build(runtime=runtime, verifier=verifier, idempotency=idempotency)
        )

    def resources(current: WebhooksSettings) -> AbstractAsyncContextManager[WebhooksResources]:
        return opened(current, keys=keys)

    async def migrations(runtime: Runtime[WebhooksSettings]) -> None:
        await require_head(runtime.database, expected)

    return Service(
        settings=settings,
        version=VERSION,
        interfaces=Interfaces(http=http, resources=resources, checks={"migrations": migrations}),
        events=Events(consumers=consumers, workers=workers),
    )


def create_app() -> FastAPI:
    """Granian's factory (``--factory``): the app of the service the ``LEDGER_*`` environment configures."""
    return build(WebhooksSettings()).app
```

`services/webhooks/src/ledger_webhooks/openapi.py`:

```python
"""webhooks' OpenAPI document (3.1), as published in ``services/webhooks/openapi.json`` (§10).

The document is made from the code: the service is built with placeholder settings (nothing connects) and its
app asked for its schema. A test keeps the published file equal to it.
"""

import json
import sys
from pathlib import Path
from typing import Final

from pydantic import SecretStr

from ledger_webhooks.main import build
from ledger_webhooks.settings import WebhooksSettings

_USAGE: Final = "usage: ledger-webhooks-openapi PATH\n"
_WRONG_USAGE: Final = 2
_DATABASE: Final = "postgresql+psycopg://openapi@localhost/openapi"
_ISSUER: Final = "https://issuer.invalid"
"""Placeholders: the document depends neither on whom the service trusts nor on its keys."""
_AUDIENCE: Final = "ledger"
_KEYS: Final = "https://issuer.invalid/oauth/v2/keys"
_ADDRESS: Final = "localhost:50051"
_KEY_ID: Final = "openapi"
_KEY: Final = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="


def document() -> str:
    """The OpenAPI document of webhooks' public API, as JSON with sorted keys."""
    settings = WebhooksSettings(
        database_url=SecretStr(_DATABASE),
        token_issuer=_ISSUER,
        token_audience=_AUDIENCE,
        token_jwks_url=_KEYS,
        identity_address=_ADDRESS,
        secret_keys={_KEY_ID: SecretStr(_KEY)},
        secret_key_id=_KEY_ID,
    )
    return json.dumps(build(settings).app.openapi(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> int:
    """``ledger-webhooks-openapi PATH``: write the document to ``PATH``."""
    match sys.argv[1:]:
        case [path]:
            Path(path).write_text(document(), encoding="utf-8")
            return 0
        case _:
            sys.stderr.write(_USAGE)
            return _WRONG_USAGE
```

Run: `uv run poe openapi`
Expected: `services/webhooks/openapi.json` written; commit it.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest services/webhooks -q --no-cov`
Expected: PASS (141 tests): an endpoint registered, its secret read from `/secret` only, a test delivery received and verified by an independent check; an invoicing event published on NATS reaches the endpoint signed, once; a failing endpoint retried and its attempts listed without bodies; a rotation signs with both secrets; who may do what; settings refused beyond a laptop.

- [ ] **Step 6: Gate**

Run: `uv run poe fmt && uv run poe check`
Expected: every step passes, coverage 100 %.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock services/webhooks
git commit -m "feat(webhooks): the /v1/webhooks API, invoicing's events delivered, the service

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 14: The images, the local stack and CI

**Files:**
- Create: `services/invoicing/Dockerfile`, `services/webhooks/Dockerfile`, `deploy/local/invoicing.env`, `deploy/local/webhooks.env`
- Modify: `deploy/local/compose.yaml`, `deploy/local/postgres/init.sql`, `Tiltfile`, `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: `create_app`, `ledger-invoicing-migrate`, `ledger-webhooks-migrate` (Tasks 5, 7, 12, 13); `ledger-probe` (M2 Task 17); fiscal-do's image and compose services (M4 Task 14), now serving `FiscalRulesService` on its gRPC port 50051 (the chassis's default `grpc_port`, Task 8).
- Produces: images `ledger-invoicing:local` and `ledger-webhooks:local` (distroless, `nonroot`, Granian on uvloop, health check on `/readyz`); compose services `invoicing-migrate`, `invoicing` (`127.0.0.1:8003`), `webhooks-migrate`, `webhooks` (`127.0.0.1:8004`) and `loadtest` (profile `load`, Task 15); databases `invoicing` and `webhooks`, each owned by its role.

Re-check before starting: fiscal-do's `Dockerfile`, compose services and CI step as Milestone 4 left them (invoicing's and webhooks' follow them exactly but for the package and the port); the HTTP ports taken (identity 8000, vault 8001, fiscal-do 8002: invoicing takes 8003, webhooks 8004); that fiscal-do's container serves gRPC on 50051 inside the network (no `ports` entry is needed for it).

- [ ] **Step 1: The images**

`services/invoicing/Dockerfile`:

```dockerfile
# syntax=docker/dockerfile:1
# The invoicing service: its API and its consumer of fiscal outcomes in one process. Built from the workspace's lockfile with uv; run by Granian on uvloop, as an
# unprivileged user, from a file system it never writes to. Build from the repository root:
#   docker build -f services/invoicing/Dockerfile -t ledger-invoicing:local .

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
    uv sync --locked --no-dev --no-editable --package ledger-invoicing

# Distroless: glibc, CA certificates and time zones, no shell; user nonroot (65532).
FROM gcr.io/distroless/cc-debian13:nonroot@sha256:e792ab3d241a468a4fd7519ddbbebe66b49b5f365771716ea688ad40b6c6f1c2
LABEL org.opencontainers.image.title="ledger-invoicing" \
      org.opencontainers.image.description="Commercial documents, country-agnostic, checked by every country's fiscal service" \
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
            "ledger_invoicing.main:create_app"]
```

`services/webhooks/Dockerfile`:

```dockerfile
# syntax=docker/dockerfile:1
# The webhooks service: its API, its consumer of invoicing's events and its delivery worker in one process. Built from the workspace's lockfile with uv; run by Granian on uvloop, as an
# unprivileged user, from a file system it never writes to. Build from the repository root:
#   docker build -f services/webhooks/Dockerfile -t ledger-webhooks:local .

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
    uv sync --locked --no-dev --no-editable --package ledger-webhooks

# Distroless: glibc, CA certificates and time zones, no shell; user nonroot (65532).
FROM gcr.io/distroless/cc-debian13:nonroot@sha256:e792ab3d241a468a4fd7519ddbbebe66b49b5f365771716ea688ad40b6c6f1c2
LABEL org.opencontainers.image.title="ledger-webhooks" \
      org.opencontainers.image.description="Client endpoints; deliveries signed, retried and guarded against SSRF" \
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
            "ledger_webhooks.main:create_app"]
```

(`cryptography` brings its native part in its wheel; `.dockerignore` already leaves out `**/tests`, and the load test is not in the image's package.)

Run: `docker build -f services/invoicing/Dockerfile -t ledger-invoicing:local . && docker build -f services/webhooks/Dockerfile -t ledger-webhooks:local .`
Expected: both images tagged; `docker image inspect ledger-webhooks:local --format '{{.Config.User}}'` prints `nonroot`.

- [ ] **Step 2: The local stack**

`deploy/local/postgres/init.sql` — append:

```sql
-- The invoicing and webhooks services' own databases and roles, on the same terms as the other services'.
CREATE ROLE invoicing LOGIN PASSWORD 'invoicing' NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
CREATE DATABASE invoicing OWNER invoicing;
CREATE ROLE webhooks LOGIN PASSWORD 'webhooks' NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
CREATE DATABASE webhooks OWNER webhooks;
```

`deploy/local/invoicing.env`:

```
# The invoicing service in the local stack: fiscal-do's FiscalRules over gRPC, identity inside this network.
LEDGER_ENVIRONMENT=local
LEDGER_LOG_FORMAT=json
LEDGER_DATABASE_URL=postgresql+psycopg://invoicing:invoicing@postgres:5432/invoicing
LEDGER_NATS_URL=nats://nats:4222
LEDGER_TOKEN_ISSUER=http://zitadel.localhost:8080
LEDGER_TOKEN_AUDIENCE=ledger-local
LEDGER_TOKEN_JWKS_URL=http://zitadel.localhost:8080/oauth/v2/keys
LEDGER_IDENTITY_ADDRESS=identity:50051
LEDGER_FISCAL_RULES_ADDRESSES=["fiscal-do:50051"]
```

`deploy/local/webhooks.env`:

```
# The webhooks service in the local stack. The sealing key is the laptop's own (never a deployment's: those come
# from the secret manager); plain HTTP and private targets are allowed so that a receiver inside this network,
# or on the host, can be an endpoint. Staging and production refuse both at start.
LEDGER_ENVIRONMENT=local
LEDGER_LOG_FORMAT=json
LEDGER_DATABASE_URL=postgresql+psycopg://webhooks:webhooks@postgres:5432/webhooks
LEDGER_NATS_URL=nats://nats:4222
LEDGER_TOKEN_ISSUER=http://zitadel.localhost:8080
LEDGER_TOKEN_AUDIENCE=ledger-local
LEDGER_TOKEN_JWKS_URL=http://zitadel.localhost:8080/oauth/v2/keys
LEDGER_IDENTITY_ADDRESS=identity:50051
LEDGER_SECRET_KEYS={"local-1": "bG9jYWwtb25seS1zZWFsaW5nLWtleS0zMi1ieXRlcyE="}
LEDGER_SECRET_KEY_ID=local-1
LEDGER_ALLOW_HTTP_TARGETS=true
LEDGER_ALLOW_PRIVATE_TARGETS=true
```

`deploy/local/compose.yaml` — these services follow fiscal-do's (Decision 18: invoicing and fiscal-do do not wait for each other's health):

```yaml
  invoicing-migrate:
    image: ledger-invoicing:local
    build:
      context: ../..
      dockerfile: services/invoicing/Dockerfile
    entrypoint: ["/app/.venv/bin/ledger-invoicing-migrate"]
    env_file: [invoicing.env]
    read_only: true
    tmpfs: [/tmp]
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]
    restart: "no"
    depends_on:
      postgres: {condition: service_healthy}

  invoicing:
    # invoicing and fiscal-do each consume the other's stream: neither waits for the other to be healthy (that
    # would never happen). Each declares its own stream at start; a consumer whose stream is not there yet is
    # started again by the chassis's supervisor (at most a minute later) and binds once it is.
    image: ledger-invoicing:local
    env_file:
      - invoicing.env
      - path: zitadel/.local/identity.env
        required: false
    ports: ["127.0.0.1:8003:8000"]
    read_only: true
    tmpfs: [/tmp]
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]
    depends_on:
      invoicing-migrate: {condition: service_completed_successfully}
      nats: {condition: service_healthy}
      identity: {condition: service_healthy}
      fiscal-do: {condition: service_started}

  webhooks-migrate:
    image: ledger-webhooks:local
    build:
      context: ../..
      dockerfile: services/webhooks/Dockerfile
    entrypoint: ["/app/.venv/bin/ledger-webhooks-migrate"]
    env_file: [webhooks.env]
    read_only: true
    tmpfs: [/tmp]
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]
    restart: "no"
    depends_on:
      postgres: {condition: service_healthy}

  webhooks:
    image: ledger-webhooks:local
    env_file:
      - webhooks.env
      - path: zitadel/.local/identity.env
        required: false
    ports: ["127.0.0.1:8004:8000"]
    read_only: true
    tmpfs: [/tmp]
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]
    depends_on:
      webhooks-migrate: {condition: service_completed_successfully}
      nats: {condition: service_healthy}
      identity: {condition: service_healthy}
      invoicing: {condition: service_healthy}

  loadtest:
    # The SLO load test of POST /v1/invoices (services/invoicing/loadtest/invoices.js), run on demand only:
    #   TOKEN=... COMPANY=... POINT=... docker compose -f deploy/local/compose.yaml --profile load run --rm loadtest
    image: grafana/k6:2.3.0@sha256:9c2dee7f8ed74d317e4027c06a10f169b625638189de8d4555d0b3486a5aeb34
    profiles: [load]
    command: ["run", "/scripts/invoices.js"]
    environment:
      TOKEN: ${TOKEN:-}
      COMPANY: ${COMPANY:-}
      POINT: ${POINT:-}
      RATE: ${RATE:-20}
      DURATION: ${DURATION:-2m}
    volumes: ["../../services/invoicing/loadtest:/scripts:ro"]
    read_only: true
    tmpfs: [/tmp]
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]
    depends_on:
      invoicing: {condition: service_healthy}
      fiscal-do: {condition: service_healthy}
```

`Tiltfile` — beside fiscal-do's:

```python
docker_build(
    "ledger-invoicing:local",
    ".",
    dockerfile="services/invoicing/Dockerfile",
    only=["pyproject.toml", "uv.lock", ".python-version", "libs", "services"],
    ignore=["**/tests", "**/benchmarks", "**/loadtest", "**/__pycache__", "**/.venv"],
)
docker_build(
    "ledger-webhooks:local",
    ".",
    dockerfile="services/webhooks/Dockerfile",
    only=["pyproject.toml", "uv.lock", ".python-version", "libs", "services"],
    ignore=["**/tests", "**/benchmarks", "**/loadtest", "**/__pycache__", "**/.venv"],
)

dc_resource("invoicing-migrate", labels=["invoicing"])
dc_resource("invoicing", labels=["invoicing"], links=[link("http://localhost:8003/docs", "invoicing API")])
dc_resource("webhooks-migrate", labels=["webhooks"])
dc_resource("webhooks", labels=["webhooks"], links=[link("http://localhost:8004/docs", "webhooks API")])
```

Run:

```bash
docker compose -f deploy/local/compose.yaml config --quiet
docker compose -f deploy/local/compose.yaml down -v
docker compose -f deploy/local/compose.yaml up -d --wait --build
curl --fail --silent --show-error http://127.0.0.1:8003/readyz
curl --fail --silent --show-error http://127.0.0.1:8004/readyz
test "$(curl --silent --output /dev/null --write-out '%{http_code}' http://127.0.0.1:8003/v1/invoices)" = 401
docker compose -f deploy/local/compose.yaml run --rm invoicing-migrate
docker compose -f deploy/local/compose.yaml down -v
```

Expected: every service healthy (the `loadtest` service, in profile `load`, does not start); invoicing's and webhooks' `/readyz` list `database`, `nats`, `workers`, the chassis's tables and `migrations`, all `true`; the invoices route answers 401 without a token; the second migration exits 0. (`docker compose config --quiet` was run on 2026-10-12 against the landed compose file with stand-ins for the services of Milestones 2-4; the stack itself was not started.)

- [ ] **Step 3: CI**

In `.github/workflows/ci.yml`, the local-stack step builds the two images too and checks both services, in place of its `build` line and after fiscal-do's checks:

```yaml
          $compose build identity-migrate vault-migrate fiscal-do-migrate fake-dgii invoicing-migrate webhooks-migrate
          $compose up -d --wait || { $compose logs; exit 1; }
          curl --fail --silent --show-error http://127.0.0.1:8003/readyz
          curl --fail --silent --show-error http://127.0.0.1:8004/readyz
          test "$(curl --silent --output /dev/null --write-out '%{http_code}' http://127.0.0.1:8003/v1/invoices)" = 401
          test "$(curl --silent --output /dev/null --write-out '%{http_code}' http://127.0.0.1:8004/v1/webhooks/endpoints)" = 401
          test "$(docker image inspect ledger-invoicing:local --format '{{.Config.User}}')" = nonroot
          test "$(docker image inspect ledger-webhooks:local --format '{{.Config.User}}')" = nonroot
          $compose run --rm invoicing-migrate
          $compose run --rm webhooks-migrate
```

If the job outgrows its `timeout-minutes`, raise it and say so in the report.

- [ ] **Step 4: Gate**

Run: `uv run poe fmt && uv run poe check && docker compose -f deploy/local/compose.yaml config --quiet`
Expected: every step passes; the compose file is valid.

- [ ] **Step 5: Commit**

```bash
git add Tiltfile .github deploy services/invoicing/Dockerfile services/webhooks/Dockerfile
git commit -m "build(invoicing, webhooks): distroless images, the local stack, CI smoke

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 15: The load test of the SLO: p95 of `POST /v1/invoices` under 300 ms

**Files:**
- Create: `services/invoicing/loadtest/invoices.js`
- Modify: `_typos.toml` only if `typos` flags the script (it did not in the scratch workspace)

**Interfaces:**
- Consumes: the local stack of Task 14 (the `loadtest` compose service, profile `load`); fiscal-do's `/v1/do/profile` and `/v1/do/sequences` (Milestone 4) to make a company able to issue; identity's local token (`deploy/local/zitadel/token.sh`, M2 Task 18), a company and an emission point of it.
- Produces: a k6 script run on demand (Decision 17): constant arrival rate (`RATE`/s, default 20, for `DURATION`, default 2m), thresholds `p(95)<300` on the tagged `create_invoice` requests, failures under 1 %, checks over 99 %; exit 99 when one does not hold.

Re-check before starting: identity's local token script and how a company and an emission point are created in the local stack (M2's README); fiscal-do's profile and sequence bodies as Milestone 4 landed them (the script sends `{"environment": "testecf", "address": ...}` and a type 32 range without an expiry).

- [ ] **Step 1: The script**

`services/invoicing/loadtest/invoices.js`:

```javascript
// The spec's SLO for issuing (§11): p95 of POST /v1/invoices under 300 ms, DGII excluded. k6 2.3.0.
//
// Every iteration issues one consumer invoice (no customer: fiscal-do makes it a type 32) with its own
// Idempotency-Key, at a constant arrival rate, so that a slow answer never lowers the load. invoicing checks
// every invoice with fiscal-do over gRPC before it stores it, so the measured time includes that check; what
// fiscal-do does afterwards (numbering, signing, DGII) is asynchronous and outside the measure, as the SLO says.
// The run fails (exit 99) when a threshold does not hold.
//
// Environment: TOKEN (a bearer token of the company's owner), COMPANY and POINT (a company and one of its
// emission points, from identity), and optionally INVOICING_URL, FISCAL_DO_URL, RATE (per second, 20) and
// DURATION (2m).

import http from "k6/http";
import { check, fail } from "k6";

const INVOICING = __ENV.INVOICING_URL || "http://invoicing:8000";
const FISCAL_DO = __ENV.FISCAL_DO_URL || "http://fiscal-do:8000";
const RATE = Number(__ENV.RATE || 20);

export const options = {
  scenarios: {
    issue: {
      executor: "constant-arrival-rate",
      rate: RATE,
      timeUnit: "1s",
      duration: __ENV.DURATION || "2m",
      preAllocatedVUs: RATE,
      maxVUs: RATE * 5,
    },
  },
  thresholds: {
    "http_req_duration{name:create_invoice}": ["p(95)<300"],
    "http_req_failed{name:create_invoice}": ["rate<0.01"],
    checks: ["rate>0.99"],
  },
  summaryTrendStats: ["avg", "med", "p(90)", "p(95)", "p(99)", "max"],
};

function headers(extra) {
  return Object.assign(
    {
      Authorization: `Bearer ${__ENV.TOKEN}`,
      "Ledger-Company-Id": __ENV.COMPANY,
      "Content-Type": "application/json",
    },
    extra,
  );
}

// The company's fiscal profile and a range of consumer-invoice numbers at the emission point, once per run.
// A range already registered by an earlier run is a 409: either way the company can issue.
export function setup() {
  if (!__ENV.TOKEN || !__ENV.COMPANY || !__ENV.POINT) {
    fail("TOKEN, COMPANY and POINT are required");
  }
  const profile = http.put(
    `${FISCAL_DO}/v1/do/profile`,
    JSON.stringify({ environment: "testecf", address: "Av. Duarte 123" }),
    { headers: headers({}) },
  );
  check(profile, { "fiscal profile in place": (r) => r.status === 200 || r.status === 201 || r.status === 412 });
  const range = http.post(
    `${FISCAL_DO}/v1/do/sequences`,
    JSON.stringify({ ecf_type: 32, emission_point_id: __ENV.POINT, first: 1, last: 50000000 }),
    { headers: headers({ "Idempotency-Key": `loadtest-range-${__ENV.POINT}` }) },
  );
  check(range, { "range in place": (r) => r.status === 201 || r.status === 409 });
}

export default function () {
  const today = new Date().toISOString().slice(0, 10);
  const invoice = {
    kind: "invoice",
    emission_point_id: __ENV.POINT,
    issue_date: today,
    currency: "DOP",
    lines: [
      { number: 1, kind: "good", name: "Item A", quantity: "2", unit_price: "150.00", tax: { category: "standard", rate: "18" } },
      { number: 2, kind: "good", name: "Item B", quantity: "1", unit_price: "89.90", tax: { category: "standard", rate: "18" } },
      { number: 3, kind: "service", name: "Delivery", quantity: "1", unit_price: "100.00", tax: { category: "exempt", rate: "0" } },
    ],
    terms: { kind: "cash" },
    payments: [{ method: "card", amount: "560.08" }],
  };
  const response = http.post(`${INVOICING}/v1/invoices`, JSON.stringify(invoice), {
    headers: headers({ "Idempotency-Key": `load-${__VU}-${__ITER}-${Date.now()}` }),
    tags: { name: "create_invoice" },
  });
  check(response, { "invoice issued": (r) => r.status === 201 });
}
```

Run: `docker run --rm -v "$PWD/services/invoicing/loadtest:/scripts:ro" grafana/k6:2.3.0@sha256:9c2dee7f8ed74d317e4027c06a10f169b625638189de8d4555d0b3486a5aeb34 inspect /scripts/invoices.js`
Expected: the options printed, the scenario `issue` with executor `constant-arrival-rate` (verified on 2026-10-12).

- [ ] **Step 2: Run it against the local stack**

```bash
docker compose -f deploy/local/compose.yaml up -d --wait --build
export TOKEN=$(sh deploy/local/zitadel/token.sh) COMPANY=<a company's id> POINT=<one of its emission points>
docker compose -f deploy/local/compose.yaml --profile load run --rm loadtest
```

Expected: k6's summary with `http_req_duration{name:create_invoice}` p(95) under 300 ms and every threshold `✓`; exit 0. Record the p95 and the rate in the report. (In the scratch workspace, in process without the network: p95 33 ms sequentially and 196 ms at 10 concurrent requests on a laptop's PostgreSQL in Docker; the script itself ran 3 s against a stand-in server and passed its thresholds. Not run against the stack.) If the threshold fails, profile before changing anything (`py-spy` on the container, `pg_stat_statements`), and report; do not raise the threshold.

- [ ] **Step 3: Gate**

Run: `uv run poe check`
Expected: every step passes (typos reads the script).

- [ ] **Step 4: Commit**

```bash
git add services/invoicing/loadtest
git commit -m "test(invoicing): k6 load test of the issuing SLO, p95 under 300 ms

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 16: README and the spec brought in line

**Files:**
- Modify: `README.md`, `docs/superpowers/specs/2026-10-11-ledger-platform-design.md`

**Interfaces:**
- Consumes: Decisions 1-21 and what Tasks 1-15 built.
- Produces: documentation only.

Re-check before starting: the README and the spec as Milestones 2-4 left them (each edit names the section it changes; keep their wording where it still holds, and change only what this milestone makes untrue or adds).

- [ ] **Step 1: README**

1. The opening paragraph's "What exists today" sentence names milestone 5: identity, vault, fiscal-do, invoicing (commercial documents, checked by fiscal-do before they are issued, then numbered and sent to DGII from its event) and webhooks (every public event delivered signed to a company's endpoints).
2. "Layout": add the rows

```markdown
| `services/invoicing` | `ledger_invoicing`: commercial documents, country-agnostic. Invoices, credit and debit notes, lines with their own tax, exact totals rounded at three points, `/v1/invoices`, the fiscal life mirrored from the country's service |
| `services/webhooks` | `ledger_webhooks`: a company's endpoints, Standard Webhooks signatures with rotation, sealed secrets, deliveries retried from a leased queue, SSRF refused, `/v1/webhooks` |
```

3. "The local stack": invoicing on `127.0.0.1:8003`, webhooks on `127.0.0.1:8004`; issuing an invoice (fiscal-do's profile and a range at the emission point first, as in milestone 4's example):

```sh
curl -X POST http://127.0.0.1:8003/v1/invoices -H "Authorization: Bearer $TOKEN" \
  -H "Ledger-Company-Id: $COMPANY" -H "Idempotency-Key: $(date +%s%N)" -H 'Content-Type: application/json' \
  -d '{"kind": "invoice", "emission_point_id": "'$POINT'", "issue_date": "2026-10-12", "currency": "DOP",
       "lines": [{"number": 1, "kind": "good", "name": "Item", "quantity": "1", "unit_price": "100.00",
                  "tax": {"category": "standard", "rate": "18"}}],
       "terms": {"kind": "cash"}, "payments": [{"method": "cash", "amount": "118.00"}]}'
```

then `GET` it until `fiscal.status` is `accepted`; register an endpoint with `POST http://127.0.0.1:8004/v1/webhooks/endpoints` (`{"url": ..., "event_types": ["invoice.*"]}`), read its secret from `GET .../{id}/secret` and `POST .../{id}/test`. The load test: `docker compose -f deploy/local/compose.yaml --profile load run --rm loadtest` with `TOKEN`, `COMPANY` and `POINT` set (Task 15).

4. "Not done yet": add Decision 21's list.

- [ ] **Step 2: The spec**

1. §3, the services table: invoicing's row reads "commercial documents (invoice, credit note, debit note), their lines with their own tax (a VAT category at a rate), exact totals, a lifecycle independent of any country; what one country needs travels unread in `fiscal_fields`" | "public REST (`/v1/invoices`...); gRPC `InvoicingQuery` (deferred: nothing calls it yet)" | "`fiscal.document.*` (to show fiscal status)"; webhooks' row consumes "invoicing's events (`invoicing.document.*`), told in public words"; fiscal-do's row adds "gRPC `ledger.fiscal.v1.FiscalRulesService` (every country service implements it)" and consumes `invoicing.document.issued`.
2. §4.2: add "`FiscalRulesService.CheckDocument`: invoicing asks every country's service, before it issues, what that country would refuse (Decision 2); idempotent (a caller may retry it), answered within invoicing's `fiscal_check_seconds` (2 s) or the document is not issued (503)."
3. §5.1: step 1 reads "invoicing: validate, compute totals, ask the fiscal services (`CheckDocument`, outside any transaction), store the document (status `issued`) + outbox `DocumentIssued` → 201"; after step 1: "fiscal-do's `fiscal-do-commercial-documents` consumer translates the document (anti-corruption layer) and numbers it under invoicing's id; what it can no longer number is announced as `fiscal.document.declined`." `/v1/do/documents` stays (Decision 1).
4. §5.3, credit and debit notes: "an invoicing document referring to a settled invoice of the same company and currency; a credit note never credits more than the invoice with the notes before it."
5. §9, the webhooks row: "Standard Webhooks v1 (HMAC-SHA256 over id, timestamp and body; 5-minute replay window); secrets rotated with an overlap of 1 minute to 7 days, sealed at rest with AES-256-GCM bound to their endpoint; every target public HTTPS, every resolved address checked at every attempt, the connection pinned to it, no redirects."
6. §10: webhook event types are `invoice.issued`, `invoice.voided`, `invoice.fiscal.numbered|accepted|rejected` and `webhook.test`, each version 1; a breaking change is a new version.
7. §14, the load row: "k6 2.3.0 (pinned by digest), constant arrival rate, the SLO's p95 as a threshold; on demand in the local stack (compose profile `load`), against staging before each release."
8. §16: milestone 5's row: "public invoice API, invoicing → fiscal-do on the event path through an anti-corruption layer, fiscal rules over gRPC, outbox/inbox, webhooks".
9. §17: add rows "A local fiscal failure answered at `POST` needs the country's rules before issuing | `FiscalRulesService.CheckDocument` (Decision 2); a service that cannot answer is a 503 and nothing is issued" and "Deferred from milestone 5 | Decision 21's list".

- [ ] **Step 3: Gate**

Run: `uv run poe check`
Expected: every step passes (typos reads both files).

- [ ] **Step 4: Commit**

```bash
git add README.md docs/superpowers/specs/2026-10-11-ledger-platform-design.md
git commit -m "docs: README and spec in line with milestone 5 (invoicing, webhooks)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## The invoicing service

What a client sees, once the plan is executed:

| Route | Permission | What it does |
|---|---|---|
| `POST /v1/invoices` | `invoice.issue` | checks an invoice, credit note or debit note (invoicing's rules, then every fiscal service's), stores and announces it; 201 with `Location` and `ETag`; `draft: true` keeps a draft instead |
| `GET /v1/invoices` | `invoice.read` | the company's documents, a page at a time (`limit`, `cursor`) |
| `GET /v1/invoices/{invoice_id}` | `invoice.read` | the document, its totals and its fiscal side (status, number, the authority's messages); `ETag` its version |
| `PUT /v1/invoices/{invoice_id}` | `invoice.issue` | rewrites a draft at the version `If-Match` names |
| `POST /v1/invoices/{invoice_id}/issue` | `invoice.issue` | issues a draft (checked as `POST /v1/invoices` checks) |
| `POST /v1/invoices/{invoice_id}/void` | `invoice.issue` | voids a draft, a fiscally rejected document, or an issued one no fiscal service serves |

Every route names its company in `Ledger-Company-Id`; every `POST` takes an `Idempotency-Key`. Events on stream `INVOICING`: `invoicing.document.issued|voided|fiscally_numbered|fiscally_accepted|fiscally_rejected`. It consumes `fiscal.document.>` (`invoicing-fiscal-outcomes`).

## The webhooks service

| Route | Permission | What it does |
|---|---|---|
| `GET /v1/webhooks/event-types` | authenticated | the public events, their versions and what each means |
| `POST /v1/webhooks/endpoints` | `company.manage` | registers an HTTPS endpoint with its event-type filters and a new secret; 201 with `Location` and `ETag` (never the secret: no answer an idempotency key stores holds one) |
| `GET /v1/webhooks/endpoints[/{endpoint_id}]` | `company.read` | endpoints, without secrets; `ETag` the version |
| `PUT /v1/webhooks/endpoints/{endpoint_id}` | `company.manage` | changes the URL, filters, description or whether it is enabled, at the version `If-Match` names |
| `DELETE /v1/webhooks/endpoints/{endpoint_id}` | `company.manage` | removes it; its pending deliveries are cancelled unsent |
| `GET /v1/webhooks/endpoints/{endpoint_id}/secret` | `company.manage` | the secrets in force (`whsec_...`; two during a rotation's overlap), `Cache-Control: no-store` |
| `POST /v1/webhooks/endpoints/{endpoint_id}/secret/rotate` | `company.manage` | a new secret; the old one signs too until the overlap ends |
| `POST /v1/webhooks/endpoints/{endpoint_id}/test` | `company.manage` | queues a signed `webhook.test` delivery |
| `GET /v1/webhooks/endpoints/{endpoint_id}/deliveries[/{delivery_id}]` | `company.read` | deliveries, their state and next attempt; one delivery with its attempts (status, failure, duration; never a body) |

Every delivery is a `POST` with `webhook-id`, `webhook-timestamp` and `webhook-signature` (Standard Webhooks v1), retried at 1 minute doubling to 6 hours (±20 %) until a 2xx or 12 attempts.

## Self-review (for the plan author; already applied)

1. **Scope against the owner's list.** `services/invoicing` (`ledger_invoicing`): Tasks 3-7. `services/webhooks` (`ledger_webhooks`): Tasks 9-13. fiscal-do switched to the event path through an anti-corruption layer: Task 8 (`adapters.commercial`, `application.commercial`, the consumer `fiscal-do-commercial-documents`). Keep or deprecate `/v1/do`: kept (Decision 1). Tax data: lines carry a VAT category at a rate; country rules stay in fiscal-do, asked over gRPC (Decisions 2-4; `Money.divided` in Task 1 for prices that include the tax). Deployment artifacts: Task 14. k6 load test of p95 `POST /v1/invoices` < 300 ms: Task 15. Docs: Task 16.
2. **Every code step is whole.** Each file is given entire, as it ran in the scratch workspace, read from it by the script that assembled this plan; modified files are given as the exact parts that change (fiscal-do's, the kernel's money, the contracts' fiscal-do events), and the test support that grows across tasks (`webhook_samples`, both `conftest.py`) is given at each task in the form that task runs.
3. **Names agree across tasks.** invoicing's `commercial_document` writes what fiscal-do's `translate` reads (one `ledger.invoicing.v1.CommercialDocument`; the round trip is tested on both sides); fiscal-do's events are what `outcome_of` reads (`DocumentDeclined` included); invoicing's events are what webhooks' `public_event` reads; `ledger_contracts.invoicing.SUBJECTS` are what invoicing publishes and what fiscal-do's and webhooks' consumers bind.
4. **Gates per task.** Each task's tests cover what it adds, so `poe check` (100 % branch coverage) passes after each one; a support module is created by the first task whose tests use it and extended only when a later task's names exist.
5. **What rests on unlanded milestones** is named in each task's "Re-check before starting", and what could not be run is named in Global Constraints and at the step concerned (Task 8 Steps 6-7, Task 14, Task 15 Step 2).

## Changelog

- 2026-10-12: first version. The dependency checks on CPython 3.15, `buf lint`, `docker compose config`, `k6 inspect` and every line of code run in a scratch workspace the same day; the plan file was written once the workspace's gate (ruff, basedpyright, mypy, import-linter, deptry, typos, 736 tests in random order, 100 % branch coverage) passed.
