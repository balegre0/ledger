# PANEL-01: Software architecture

Ten roles judged P0-P6 using CONTEXT.md, PROPOSALS.md, R1-R8 and a read-only check of the repo
(`docs/adr/`, `docs/reviews/2026-10-20-stack-jury-synthesis.md` §5-§6). Scores run from 0 to 10, where 10 means the
best direction for this owner. "Jury §n" means the earlier stack-jury synthesis in the repo.

## 1. Scores

| # | Role | P0 | P1 | P2 | P3 | P4 | P5 | P6 | Favorite |
|---|---|---|---|---|---|---|---|---|---|
| 1 | Principal software architect, large SaaS | 2 | 6 | 7 | 5 | 3 | 8 | 1 | P5→P2 (gated by the experiment) |
| 2 | DDD consultant (strategic) | 1 | 5 | 7 | 6 | 2 | 9 | 1 | P5→buy the generic subdomains (P2 or P3) |
| 3 | Staff engineer, large modular monolith (Shopify/Kraken) | 3 | 9 | 5 | 4 | 3 | 7 | 1 | P1 |
| 4 | Microservices advocate (ex-Uber/Netflix) | 5 | 4 | 7 | 5 | 8 | 7 | 2 | P5→P4 |
| 5 | Event-driven / CQRS / ES specialist | 3 | 8 | 5 | 4 | 5 | 7 | 2 | P1 |
| 6 | Evolutionary architecture / fitness functions | 2 | 7 | 6 | 5 | 3 | 9 | 1 | P5→(P1 or P2, chosen by fitness test) |
| 7 | Metadata-platform engineer (Salesforce/Dataverse-style kernel) | 1 | 4 | 9 | 6 | 4 | 6 | 1 | P2 |
| 8 | Clean/hexagonal purist | 6 | 6 | 4 | 3 | 3 | 8 | 2 | P5 |
| 9 | Pragmatic startup CTO (three B2B products) | 1 | 5 | 5 | 6 | 2 | 9 | 0 | P5 + a minimal issuing portal |
| 10 | Legacy modernization / rewrite consultant | 2 | 5 | 6 | 7 | 2 | 7 | 1 | P5→P3 |
| | **Mean** | **2.6** | **5.9** | **6.1** | **5.1** | **3.5** | **7.7** | **1.2** | |

## 2. Per-role reasoning

**1. Principal software architect (large SaaS).**
- Every configurable CRM/ERP studied is metadata-driven (R1 §2); the repo has none, and building one is a
  year-scale bet (R1 §4).
- Top, P5 (8): sell the strong part now (R2 §4; R3 verdict), then take breadth from Frappe, an MIT framework that
  ships metadata, UI, REST and reports (R7 [17]).
- Bottom, P6 (1): it changes the language, not the breadth problem (R5 §3).
- Mind-changer: Frappe fails the jury's 5-day rule (under 80% of a DR SME month, or a connector of 1.5k lines or
  more; jury §5).
- Folder: R8 §4b. Trim to `ledger-engine` with a generated `clients/python`; the ERP side is its own Frappe app repo.

**2. DDD consultant (strategic).**
- Core = e-CF, numbering, DGII lifecycle, custody, country packs. CRM and generic GL are supporting or generic:
  buy them (R2 §2; [23]).
- Top, P5 (9): build only the core; each ERP becomes a downstream context behind an anticorruption layer.
- Bottom, P0 (1, tied with P6): a context per schema, joined only by events, fights how ERP data is read (R2 §3,
  rated High).
- Mind-changer: interviews showing the DR differentiator is a CRM workflow (say, accountant-client collaboration).
  CRM would then be core, and the answer P1.
- Folder: the anticorruption layer lives in each connector (`services/engine_client.py`, R8 §4b), never in the
  engine; `countries/do` stays independent.

**3. Staff engineer (Shopify/Kraken-style modular monolith).**
- Kraken runs about 15M lines of Python in one monolith with import-linter and territories (R5 [8-9]): this repo's
  shape. Boundaries are a ratchet, not an ideal graph (R2 §2).
- Top, P1 (9): one codeline (R1 P4), a frozen chassis, the R3 §2.2 registry, no second stack or tenancy model.
- Bottom, P6 (1): it ports a tested monolith for nothing (R5 §3).
- Mind-changer: a 2-week spike where custom fields plus generated list and form views for three entities do not cut
  the cost of a new entity (today about 8 files in 6 directories, R8 §3.3).
- Folder: R8 §4a. `packages/metadata`, `modules/<m>/metadata/`, entity slices; split the 800-line `repositories.py`.

**4. Microservices advocate (ex-Uber/Netflix).**
- I concede that even Uber tells small organizations to wait (R2 [6]). But the engine is a real service boundary:
  custody, archive and DGII (ADR 0010; R7 §3 D).
- Top, P4 (8): behind that boundary, build the UI-heavy app layer in TypeScript, the Twenty pattern (R5 §2-§3).
- Bottom, P6 (2). P1 gets 4: same-transaction cross-module commands close off extraction.
- Mind-changer: ops above about 3 hours a week for two runtimes (R6 §3), or the TS agent gap persisting on current
  models (R5 [33-35]).
- Folder: two repos. A pnpm/Nx TypeScript workspace after Twenty (R8 [24]), with contract tests on both sides of
  `contracts/openapi`.

**5. Event-driven / CQRS / ES specialist.**
- Keep the outbox, transactional enqueue and the append-only journal (R2 §2; R3 §2.3).
- Event notification as glue inside one process hides the flow (Fowler, R2 [27]); ADR 0011's flows are the first
  symptom (R2 §3).
- Top, P1 (8): events for side effects only; reads through a published reporting schema, which is light CQRS
  (R3 §2.5).
- Bottom, P6 (2). P0 gets 3: every ERP report becomes a consumer project.
- Mind-changer: DR SMEs buying packaged suites so firmly that an own app layer has no room. I would then pick P5.
- Folder: R8 §4a, plus per-module `reporting/*.sql` views beside `api.py` and `events.py`.

**6. Evolutionary architecture / fitness-functions practitioner.**
- Defer one-way doors until there is evidence. P5 keeps the engine shippable and the app-layer choice reversible
  (R2 [39]).
- Top, P5 (9): pick P1 or P2 later with fitness functions (files per new entity, connector lines, upgrade hours per
  customized module; R4 §5).
- Bottom, P6 (1). P0 gets 2: it pays for extraction seams nobody has needed (R2 §4).
- Mind-changer: proof that the vendor channel cannot give a revenue signal within a quarter. The breadth decision
  then cannot wait.
- Folder: keep the 48 import-linter contracts (R8 §3.1), add file-count and duplication checks (R7 [3]) and a
  1-minute per-module gate (R7 [9]), and split the spec.

**7. Metadata-platform engineer (built a Salesforce/Dataverse-style kernel).**
- "Narrow" metadata layers creep into custom objects, layouts, field security and packaging (R1 §4). Twenty, a
  CRM-only engine, has about 17k commits (R4 [3]), so P1 gets 4.
- Top, P2 (9): Frappe stores metadata as data, generates UI, REST, permissions and reports, and is MIT (R1 [39];
  R7 [17]). P3 gets 6 for code-as-model and the upgrade tax (R4 [7-9]).
- Bottom, P0 and P6 (1).
- Mind-changer: Frappe's database per site costing too much at thousands of tenants (DB-per-tenant suits "5 or 50",
  R3 [12]). Then P1.
- Folder: R8 §4b, Frappe flavor: `<brand>_do` plus a separate `<brand>_crm` app; never patch ERPNext.

**8. Clean/hexagonal purist.**
- Ports and adapters pay off in the engine: volatile DGII and signer, deep rules (R2 §2; [34]).
- Top, P5 (8): the engine stays pure, and every ERP is an out-of-process adapter at the edge.
- P0 gets 6 for its seams, though I accept they cost too much for CRUD (R8 §3.3). P3 gets 3: `_inherit` welds
  domain logic to the ORM (R2 [26]).
- Bottom, P6 (2): it discards the best-tested domain core.
- Mind-changer: data showing agents keep the hexagon in CRUD modules at no extra cost per entity.
- Folder: keep `domain/ application/ adapters/`; fix the drift to `repositories/<aggregate>.py` the spec requires
  (R8 §3.3).

**9. Pragmatic startup CTO (three B2B products).**
- Facts: 100k e-CF emitters, and a 15 Nov 2026 deadline that cannot be met as a provider (R6 [38]; jury §1). About
  64% of emitters use the free facturador, and Odoo modules for DR e-CF sell for about US$80-295 (jury §6).
- Top, P5 (9): sell what exists, wholesale, plus a thin portal for emitters who outgrow the free facturador.
- Bottom, P6 (0): weeks lost and re-certification in a deadline market (R7 §3 B).
- Mind-changer: fewer than 2 letters of intent from 10 vendor or accountant calls. Then go product-first through an
  Odoo module (P3).
- Folder: the R8 §4b engine; any portal goes in `apps/web` with uv `exclude` (R8 §3.3).

**10. Legacy modernization / rewrite consultant.**
- This is not legacy code: two weeks old, no customers (R7 §3 B). The assets are the fixtures, golden files and
  specs.
- Custom ERPs sink because they get built at all: Odoo had 100+ staff by 2010 (R7 [15]). Side-by-side extension is
  SAP's lesson (R1 [13]).
- Top, P3 (7): largest ecosystem, proprietary modules allowed under LGPL (R7 [14]), `l10n_do` already in CE
  (R8 [11]).
- Bottom, P6 (1).
- Mind-changer: most target SMEs on Odoo Online, which bans custom Python (R4 [7]). Then P2.
- Folder: R8 §4b, Odoo flavor: an OCA-style repo, one branch per major, `l10n_do_ecf`, CI pinned to an engine image.

## 3. Panel summary

### Means
P0 2.6 | P1 5.9 | P2 6.1 | P3 5.1 | P4 3.5 | **P5 7.7** | P6 1.2

### Favorite counts
- **First step:**
  - P5 first in 7 roles (1, 2, 4, 6, 8, 9, 10).
  - P1 in 2 roles (3, 5).
  - P2 in 1 role (7).
- **Breadth step named after P5, or as the favorite:**
  - P2 in 2 roles (1, 7), plus 2 who split between P2 and P1 or P3 (2, 6).
  - P1 in 2 roles (3, 5).
  - P3 in 1 role (10).
  - P4 in 1 role (4).
- No role favors P0 or P6.

### Consensus recommendation: P5 now, then a gated choice between P2 and P1

**Now (from today until the gate):**
- Ship the engine as the product: an API, a receiver, signer custody, and connectors for Odoo and ERPNext, sold to
  vendors, integrators and accountants (R7 §4; jury §5).
- Freeze the chassis: no new generic platform features.
- Cut operations to a managed container platform: one image, 2-3 process types, a managed IdP, and 3-5 symptom pages
  (R6 §3).
- Add the cheap reliability items: per-tenant export and restore, universal soft delete, and a monthly restore drill
  (R6 §2.2).
- Add an agent-safe surface: MCP over the API, plus usage metering (R6 §4).
- The Odoo and ERPNext connectors double as the jury's 5-day experiment. Score them with R4 §5's extensibility
  checklist.

**At the gate:** the gate falls at the first of: about 90 days, 3 paying API customers, or a confirmed request from
customers for CRM plus invoicing.
- **Choose P2** if a framework meets the jury rule (80% or more of a DR SME month, a connector under about 1.5k
  lines) and Frappe's per-site tenancy costs fit SME pricing. Prefer Frappe to Odoo because of the MIT framework,
  metadata as data, and no Odoo S.A. competing in SaaS. Choose P3 only if partner distribution is the deciding factor.
- **Otherwise choose a strictly narrow P1:**
  - The metadata registry and the reporting views come before any new functional module (R3 §4).
  - No custom objects (R4 §2).
  - Breadth modules use entity slices (R8 §4a).
  - The strict gate stays on fiscal and money paths.
- **Never P0 or P6.** P4 only on R5's trigger: the product becomes a UI-first CRM and Python shrinks to the engine.

### Three strongest objections
1. **The engine market may be too thin to fund the founder.** There are 190 authorized providers. Alanube already
   powers Alegra. Odoo modules sell for US$80-295, and retail prices converge on about US$0.02-0.05 per document
   (jury §6). P5 could leave a solo founder with a commodity, and it does not match their stated CRM+ERP ambition.
2. **Revenue is gated by regulators, not by code.** Provider authorization needs a certified RNC, PSFE tests and
   INDOTEL custody (jury §6). P5 can stall for months outside the founder's control while nothing is learned about
   CRM users.
3. **Deferring can become permanent, or two-headed.** P1 and P2 are nearly tied (5.9 vs 6.1), so the gate may not
   decide. P2 also means two tenancy models (Frappe database per site vs pooled RLS, R3 §2.1) and two ops stacks for
   one person (R6 §3). Roles 3 and 5 add that retrofitting a metadata layer later is the expensive path (R1 §4).

### Dissent
- **Roles 3 and 5 (P1 now):** start the registry and reporting views in parallel, because every module added without
  them deepens the retrofit (R3 §3).
- **Role 7 (P2 now):** skip the experiment. Frappe is the only MIT metadata platform, and a home-grown "narrow" layer
  will creep.
- **Role 4 (P4 as the eventual app layer):** use TypeScript for the UI-first CRM.
- **Role 10 (P3):** distribution beats architecture.
- **Role 8:** P0's seams are worth more than the panel credits.

### Kill criteria (signals to abandon the recommendation)
- **No demand:** after about 8-12 weeks of selling, fewer than 3 paying customers or letters of intent for the API.
  Then abandon engine-first and go product-first: through the experiment's winning framework, or an Odoo app-store
  module (P3).
- **Blocked certification:** TesteCF or provider authorization blocked for more than about 3 months. Then pivot to
  the self-certifying-emitter software route (jury §6) or to selling connectors only.
- **Both breadth paths fail:** no framework meets the 80% / 1.5k-line rule, and a narrow P1 prototype (custom fields
  plus generated list and form for three entities) takes more than about 6 weeks. Then narrow the product to a
  vertical CRM plus invoicing and stop chasing ERP breadth.
- **Too much ops:** steady-state ops above about 3 hours a week once two stacks are running (R6 §3). Then collapse to
  one stack.
- **Odoo Online dominates:** most target SMEs run on Odoo Online, where custom modules are banned (R4 [7]). Then drop
  the P3 path and the Odoo connector priority.
- **The chassis keeps growing:** any new generic platform feature lands in `packages/platform` while P5 runs. That
  signals P0 creeping back.
