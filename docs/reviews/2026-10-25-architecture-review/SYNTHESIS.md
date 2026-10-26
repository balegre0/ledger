# Phase 2: research verdict and the proposals on trial

Inputs: R1 (incumbents), R2 (styles/decomposition), R3 (data/tenancy/ledger), R4 (extensibility), R5 (language/stack),
R6 (scale/ops/market), R7 (solo founder + AI agents, build vs buy), R8 (folder/code structure; arrives later).
273 distinct cited URLs across R1-R7 (deduplicated by URL), more than half primary.

## 1. Where all seven researchers agree (unanimous findings)

1. **Style is right.** Modular monolith, one Postgres, outbox/jobs in Postgres, no broker, no microservices, no cells
   now. Evidence: Shopify, GitHub, Kraken (15M lines of Python, import-linter, country layering), Basecamp, Stack
   Overflow, Segment/Istio/Prime Video reversals, Fowler, Newman, Uber's own advice. (R1, R2, R5, R6, R7)
2. **Language is right.** Python is where Odoo and Frappe/ERPNext live, leads repo-level agent benchmarks, and the
   companies that left Python did so for compute cost or GC latency at massive scale. TypeScript only for the UI
   (client generated from OpenAPI). Re-open only on concrete triggers (DR hiring > 3-5 engineers → .NET; hotspot →
   Go/Rust for that component). (R5, R1, R7)
3. **Storage/tenancy is right, even exemplary.** Pool tenancy, RLS with FORCE, tenant in every PK/FK/unique, no global
   uniques, organization id as future shard key, append-only double-entry ledger in the same Postgres. One primary
   carries thousands of SME tenants; sharding triggers (~0.5-1 TB tables, CPU > 70-90 %, vacuum/XID) are years away.
   (R3, R6, R2)
4. **The fiscal/e-CF engine is the real asset and core domain.** Signer custody, DGII state machine, numbering,
   receiver, WORM archive. Build and isolate it. (all)
5. **The decisive gap: no metadata/application layer.** Every configurable CRM/ERP (Salesforce, Dataverse, Workday,
   Frappe, Odoo, Twenty, HubSpot, Attio, Business Central) has one: one entity definition yields storage, API, forms,
   lists, permissions, reports, import/export; tenants add fields, pipelines, rules without a release. The repo has
   none, no UI, no report engine, no user-facing field history, no search, no CRM entities. (R1, R2, R3, R4, R5, R6, R7)
6. **Do not rewrite.** A port costs little code-wise (est. 4-10 weeks) but costs DGII re-certification and the market
   window, and does not solve the breadth problem. (R5, R7)

## 2. Where they found real problems in the current design

| Problem | Who | Severity |
|---|---|---|
| No metadata layer / tenant customization tier (T0-T2), no UI | all | Decisive |
| Decomposition ceremony tuned for multi-team extraction: schema + role + per-module outbox/inbox/jobs + events-only cross-module + acyclic graph counting subscriptions (ADR 0011 flows already a symptom); facade/Protocol/DTO/events/manifest per entity | R2, R3 | High for ERP breadth |
| No cross-module read path (every ERP report = an event-fed projection project) | R2, R3 | High |
| Innovation tokens spent on a ~17k-line hand-built chassis before product/customer; Python 3.15 two weeks after GA (forces `cryptography` pin) | R2, R5, R6, R7 | High |
| One person cannot own Odoo-class breadth hand-built at this quality bar (verification/ownership, not typing, is the bottleneck; METR, DORA, GitClear, SWE-Bench Pro) | R7, R1 | High |
| Ops plan heavier than a solo founder can carry (managed K8s + Helm, 5 processes, ~15 alert types) | R6 | Medium |
| Reliability gaps where B2B SaaS really fails: per-tenant export/restore, soft delete, restore drills, staged rollouts | R6 | Medium |
| Test/live mode on every row: right for the API product, a tax on ERP tables | R2, R3 | Low-medium |
| Parallel writer agents on shared chassis code; tests authored by the same agent as the code (fiscal tests should come from DGII samples/TesteCF) | R7 | Medium |
| Market moving to metadata-driven CRMs, MCP access for outside agents, usage/outcome-metered AI; DR window closing (100k emitters, last cohort 15 Nov 2026; Alegra, Siigo, Odoo funded) | R6 | Strategic |

## 3. Where they disagree (the question the trials must settle)

- **Build a narrow metadata layer on the current Python stack** (R3: "registry + JSONB custom fields + generic
  records/links; must come before the next functional module"; R4: "viable only if narrow: fields, pipelines, views,
  templates on fixed first-party objects"; R2: "adopt one or build a thin model-driven layer") **versus adopt a
  framework for breadth and keep the repo as an engine** (R1: "clean core + side-by-side, get breadth from an existing
  metadata platform"; R7: "extend, don't build"; R6: "build it, or buy Odoo/ERPNext-class").
- **Which framework if adopting**: Frappe (MIT framework, GPL ERPNext, metadata-first, MCP server built in, site =
  database per tenant, in-process apps) vs Odoo Community (LGPL, proprietary modules allowed, Enterprise off-limits,
  yearly porting tax, Odoo S.A. competes in SaaS) vs Twenty (AGPL, TS, CRM only).
- **Sequencing**: engine-first (sell e-CF API to vendors/integrators/accountants, then breadth where customers pull)
  vs product-first (CRM+invoicing for SMEs directly).

## 4. Research verdict (Phase 2)

**The owner is not on a wrong path in the foundations; they are on a wrong path in what they are building next.**
Python, Postgres, modular monolith, RLS pool tenancy, outbox, ledger design and the fiscal engine are right and well
above industry baseline. What is wrong is (a) treating a hand-built, extraction-ready chassis as the base for CRM/ERP
breadth without a metadata/application layer, (b) boundary ceremony that makes every ERP entity and report expensive,
(c) spending effort on platform plumbing and heavy ops before a UI, a deployment and a first customer. The fix is not
a rewrite; it is a change of direction on top of what exists.

## 5. The proposals on trial

Each is a complete direction, not a slogan. Panels may propose hybrids; the tribunals will refine.

- **P0 — Status quo.** Keep building CRM/ERP breadth on the current chassis exactly as the spec says: per-module
  schema + role isolation, events-only cross-module coordination, compile-time extension only, generated contracts per
  module, managed K8s, 100 % branch coverage and mutation checks everywhere; UI later.
- **P1 — Evolve in place: engine + own thin application layer (Python).** Keep the stack and the monorepo. Freeze the
  chassis (no new generic platform features). Loosen boundaries for ERP areas: same-transaction cross-module commands
  through facades, published read-only reporting views on a replica, events kept for side effects and integration;
  strict isolation only for signer/custody and the fiscal core. Add a narrow metadata layer: entity/field descriptors
  in manifests (first-party objects) plus a per-tenant field registry (typed custom fields in JSONB with opt-in
  indexes), configurable pipelines, picklists, saved views, print/email templates, a small trigger-condition-action
  rule engine on the existing job queue; no custom objects, marketplace or BPMN at launch. Generated TS/React UI and
  client from OpenAPI + metadata. Strict gate kept on fiscal/money paths, lighter (still typed, tested) on CRUD.
  Managed container platform instead of K8s. Go-to-market: e-CF engine API first, then CRM + invoicing for SMEs.
- **P2 — Engine + Frappe for breadth.** The repo becomes a headless e-CF compliance engine service (chassis kept there
  because custody, archive and DGII justify it). CRM/ERP breadth on Frappe Framework (MIT): own proprietary apps for
  CRM, plus ERPNext (GPL) modules for accounting/inventory where useful; a DR localization app talks to the engine over
  its API. Hosting on Frappe Cloud or self-managed benches.
- **P3 — Engine + Odoo Community for breadth.** Same split, but the breadth product is Odoo CE (LGPL) with proprietary
  modules (DR e-CF connector, CRM extras); compete inside the Odoo ecosystem; Enterprise features off-limits; yearly
  version porting.
- **P4 — TypeScript metadata platform.** Build (or extend Twenty for) the CRM/ERP application layer in full-stack
  TypeScript with a Twenty-style metadata engine; the Python e-CF engine stays as a service.
- **P5 — Engine-only, API-first B2B.** Sell only the e-CF engine (API, connectors for Odoo/ERPNext/others, receiver,
  signer custody) to software vendors, integrators and accountants; build no own CRM/ERP until revenue and customer
  pull justify it; then choose P1/P2/P3 with evidence.
- **P6 — Rewrite on another stack** (.NET for DR hiring, or Go) as a full port, with its own metadata layer.

Folder/code structure: each proposal implies a repository layout; R8 supplies the evidence and candidate trees.
