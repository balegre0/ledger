# Panel 10: big tech and ecosystem perspectives

Ten roles, judged independently against CONTEXT.md, PROPOSALS.md, R1-R8 and the repo (read-only). Scores run from 0
to 10, where 10 is the best direction for this owner: a solo founder in the DR, building with AI agents, a CRM with
ERP features, and the e-CF market deadline.

Repo facts the panel relied on beyond the reports:
- No TesteCF run has happened yet. It needs the owner's RNC and certificate (`docs/handoff/BACKLOG.md` line 68,
  `docs/handoff/PROGRAM.md` line 20).
- The 15 Nov 2026 deadline "cannot be met as an authorized provider" (`docs/handoff/HANDOFF.md` line 26).
- DGII lists 190 authorized providers. Alegra's e-CF runs on Alanube, and Odoo's app store already sells DR e-CF
  modules at about US$80-295 (`docs/reviews/2026-10-20-stack-jury-synthesis.md` lines 101-102).

## 1. Scores

| Role | P0 | P1 | P2 | P3 | P4 | P5 | P6 | Favorite |
|---|---|---|---|---|---|---|---|---|
| 1. Google L7 staff engineer | 3 | 8 | 5 | 4 | 3 | 7 | 1 | P5→P1 |
| 2. Amazon principal engineer | 1 | 5 | 6 | 6 | 2 | 9 | 0 | P5→(P2 or P3, chosen by pilot customers) |
| 3. Dynamics 365 / Dataverse PM | 1 | 5 | 8 | 6 | 6 | 5 | 1 | P2 |
| 4. Shopify staff engineer | 2 | 9 | 5 | 4 | 4 | 6 | 1 | P1 |
| 5. Stripe API designer | 2 | 7 | 5 | 5 | 3 | 9 | 0 | P5→P1 |
| 6. Kraken Technologies engineer | 2 | 8 | 6 | 4 | 2 | 6 | 1 | P1 |
| 7. Atlassian Marketplace lead | 1 | 4 | 6 | 7 | 3 | 8 | 0 | P5→P3 (connectors sold through marketplaces) |
| 8. HubSpot platform PM | 1 | 8 | 6 | 4 | 6 | 4 | 1 | P1 (with an MCP server) |
| 9. Twenty CRM core maintainer | 1 | 5 | 5 | 3 | 8 | 6 | 2 | P4 (extend Twenty, do not build a TS platform) |
| 10. Frappe core maintainer | 1 | 4 | 9 | 4 | 3 | 6 | 1 | P2 |
| **Mean** | **1.5** | **6.3** | **6.1** | **4.7** | **4.0** | **6.6** | **0.8** | |

## 2. Per-role judgments

**1. Google L7 staff engineer.**
- **Top: P1 (8).** One monorepo, one build graph and one gate. R8 §5 rates the layout above the median, so evolve
  it rather than split it into two repos and two toolchains. Splitting loses atomic changes (R8 §4b).
- **Bottom: P6 (1).** A port buys nothing measurable (R5 §3) and resets DGII certification.
- **Favorite: P5→P1.** Sell the engine while the metadata layer grows on the same trunk.
- **Blind spot I admit:** I am comfortable funding platform work that a solo founder cannot staff [role judgment].
- **Would change my mind:** a P1 spike where the cost of adding an entity does not fall after the third entity.
- **Folder:** the R8 §4a tree as one uv+pnpm monorepo, with CODEOWNERS generated from the manifests and the
  2,187-line spec split into an indexed `docs/spec/`.

**2. Amazon principal engineer.**
- **Top: P5 (9).** Working backwards, the press release "DR software vendors issue certified e-CF through one API"
  can be written today. "One founder ships Odoo-class CRM+ERP" cannot: Odoo needed 100+ staff (R7 §2.1).
- **The catch:** that press release has no proof yet, because TesteCF has never been run (BACKLOG line 68).
  Customer obsession means certify first.
- **Bottom: P6 (0).**
- **Favorite: P5, then P2 or P3,** whichever ERP the pilot customers already run. A one-person team owns one
  service with a hard API (R4 §2 T3).
- **Would change my mind:** 20 conversations with vendors and integrators that produce zero letters of intent.
- **Folder:** the R8 §4b `ledger-engine`, with a generated `clients/python` and `contracts/openapi` as the published
  API.

**3. Dynamics 365 / Dataverse PM.**
- **The case against P1:** in Dataverse everything is a row (R1 [9][11]). Every metadata platform starts
  "narrow" and none stays so; custom objects, rules, roles and packaging follow (R1 §4, items 1-7).
- **Top: P2 (8).** Frappe already has Dataverse's shape: a DocType is itself a DocType, Custom Field and Property
  Setter overlays exist (R4 [4]), and an MCP server is built in (R7 [17]).
- **Bottom: P0 and P6 (1).**
- **Blind spot:** I underweight the trust model. Frappe runs third-party Python in-process (R4 §3).
- **Would change my mind:** proof that DR buyers want invoicing plus light CRM with no customization.
- **Folder:** the R8 §4b Frappe flavor, with tenant configuration exported as fixtures in git (the analogue of a
  managed solution).

**4. Shopify staff engineer.**
- **The repo is what Shopify's monolith wanted to become** (R8 §3.2). But Packwerk's privacy rules became
  API-design friction and were removed (R2 [3]); schema + role + events-only repeats that at higher cost (R2 §3).
- **Top: P1 (9).** Loosen the rules to a ratchet against new dependencies, keep the signer and fiscal core strict,
  and build the narrow metadata layer. Later, extend through out-of-process apps (R4 [15][16]).
- **Bottom: P6 (1).** P2 and P3 put the product on someone else's core.
- **Would change my mind:** a spike showing the UI tier needs more than three months to reach a first pilot.
- **Folder:** R8 §4a. Breadth modules become entity slices under an import-linter independence contract; deep
  modules stay as they are.

**5. Stripe API designer.**
- **The engine's API is already Stripe-grade:** dated versions, idempotency, test and live modes, Standard Webhooks
  and RFC 9457 errors (R4 §4).
- **Top: P5 (9).** Stripe started with one job behind one API and layered Dashboard, Billing and Invoicing on that
  same API later [role judgment].
- **Bottom: P6 (0).** It breaks the version contract before there is a single client.
- **P1 (7) is the natural second act:** the first-party UI is just one more client generated from OpenAPI (R5 §4).
- **Blind spot:** the DR developer market is small, and 190 providers compete in it.
- **Would change my mind:** evidence that vendors only buy e-CF as a bundled module (the US$80-295 Odoo price
  anchor).
- **Folder:** the R8 §4b engine with dated `contracts/openapi` and generated `clients/`. Add `apps/web` only at the
  P1 step.

**6. Kraken Technologies engineer.**
- **Kraken is the closest analogue to this repo:** a Python monolith, import-linter, and clients → territories →
  core layering (R5 [9]), at 15M lines and 2,000+ ORM models (R2 [13]).
- **Two lessons:**
  - Kraken shares one database and one ORM, with nothing like per-module schema + role + outbox (R2 §4).
  - It stands on Django, a framework, not on a hand-built chassis (R7 §2.3).
- **Top: P1 (8).** Loosen data isolation and freeze the chassis.
- **Bottom: P6 (1).**
- **P2 (6):** Frappe is the Django-like ERP choice, and I respect it.
- **Would change my mind:** new generic features still landing in `packages/platform` after the freeze. Then go P2.
- **Folder:** R8 §4a, with `countries/do` as a territory under independence contracts. Watch for territory bloat
  (R8 §2.3).

**7. Atlassian Marketplace ecosystem lead.**
- **Distribution beats architecture.** Odoo has 28M users and is growing 48 % a year (R6 [35]), and its store
  already sells DR e-CF modules.
- **Top: P5 (8), in its connector form:** an `l10n_do_ecf` addon on Odoo Apps and an app on the Frappe
  marketplace, both backed by the engine. P3 (7) is the follow-on.
- **Platform risk is real:** Atlassian ended Server, and partners lost their businesses [role judgment]. Odoo S.A.
  also competes in SaaS, and the yearly porting tax applies (R4 [7][8]).
- **Bottom: P6 (0).**
- **Lesson from Atlassian's 2022 outage:** build per-tenant restore early (R6 [6]).
- **Would change my mind:** Odoo shipping native e-CF in Community `l10n_do`.
- **Folder:** the R8 §4b Odoo flavor, an OCA-style repo with one branch per Odoo major.

**8. HubSpot platform PM.**
- **What buyers now expect:** custom properties, pipelines, an MCP endpoint that acts "as the user", and
  outcome-priced agents (R6 §4, [27][28]).
- **HubSpot's own path matches P1's scope:** for years it shipped fixed objects plus custom properties, and custom
  objects came only later and only on Enterprise (R1 [31]).
- **Top: P1 (8).** Own the data model and the MCP surface. Idempotency, audit and RFC 9457 errors already give
  agent-safe foundations (R6 §4).
- **Bottom: P0 and P6 (1).**
- **P5 (4):** an engine-only business gives away the customer relationship and the timeline data that agents need.
- **Would change my mind:** pilots showing that DR SMEs buy compliance, not CRM.
- **Folder:** R8 §4a plus `packages/metadata`, with an MCP server as another thin root under `apps/`.

**9. Twenty CRM core maintainer.**
- **My bias, stated plainly:** Twenty already built the engine that P1 wants: metadata as rows, a migration runner,
  and GraphQL, REST and MCP generated per workspace (R1 [43]). Apps that leave the core unmodified can be
  proprietary (R7 [20]).
- **Top: P4 (8), meaning "extend Twenty",** not a home-built TS platform. The e-CF engine stays a Python service.
- **Bottom: P0 (1).** A fixed-schema CRM with the UI "later" is exactly what Twenty replaces.
- **Honest limits of Twenty:**
  - CRM only, with no GL (R7 §3).
  - Per-workspace DDL (R3 §2.1).
  - Weaker TS agent benchmarks (R5 [33][34]).
- **Would change my mind:** first customers who need accounting before a pipeline.
- **Folder:** a separate Twenty app repo scaffolded with the SDK (`defineObject`), next to the R8 §4b
  `ledger-engine`.

**10. Frappe framework core maintainer.**
- **What Frappe gives:** MIT; a DocType yields table, API, form, list, report and permissions; Custom Field
  overlays give tenants T1 (R4 [4]); MCP built in (R7 [17]); India Compliance as the country-app template
  (R8 [21]).
- **Top: P2 (9).**
- **Bottom: P0 and P6 (1).**
- **Honest costs:**
  - A database per site (R1 [41]).
  - In-process Python with full database access (R4 §3).
  - GPL for ERPNext if distributed (R7 §3).
  - The 100 % typed gate will not hold on a Frappe app (R8 §4b).
- **Would change my mind:** an owner who requires pool-tenancy RLS for the CRM, or a failed licence review.
- **Folder:** the R8 §4b `<brand>_do` app with DocType folders and `engine/client.py`, plus a separate `<brand>_crm`
  app.

## 3. Panel summary

**Means:** P0 1.5 | P1 6.3 | P2 6.1 | P3 4.7 | P4 4.0 | P5 6.6 | P6 0.8

**Favorite counts:**
- P1 alone: 3 (Shopify, Kraken, HubSpot)
- P5→P1: 2 (Google, Stripe)
- P2: 2 (Dataverse, Frappe)
- P5→P2/P3: 1 (Amazon)
- P5→P3: 1 (Atlassian)
- P4: 1 (Twenty)

Counted by first step, 4 of 10 start with P5. Counted by end state for breadth:
- own Python layer (P1): 5
- framework (P2/P3): 4
- Twenty: 1

No role favors P0 or P6.

**Consensus recommendation: P5 now, then P1 behind a measured gate, with P2 (Frappe) as the agreed fallback.**

1. **Weeks 0-4.**
   - Freeze `packages/platform`.
   - Replace managed K8s with a managed container platform (R6 §5).
   - Write no other code until the first TesteCF/CerteCF run passes with the owner's RNC and certificate.
2. **Sell the engine (P5).**
   - Offer the API, plus one connector into the ERP the first pilots already run (the R8 §4b layout).
   - That connector doubles as the jury's 5-day framework experiment, done for revenue.
   - Expose the engine API through an MCP server.
3. **Gate, after 3 paying engine customers or 90 days.** Run a 3-week P1 spike:
   - `packages/metadata` (R8 §4a), covering Party and Deal, typed JSONB custom fields, one pipeline, generated
     list and form views, and one published reporting view (R3 §2.5).
   - **Pass criteria:**
     - A new first-party entity goes end to end in at most one day of founder review time.
     - A tenant's custom field appears in the API, list, filter and export with no release.
   - **Fail:** move breadth to a Frappe app beside the engine (P2 scores 6.1 against P3's 4.7). The repo stays
     `ledger-engine`.
4. **Boundary loosening (P1) applies only to the breadth modules.** The signer, the fiscal core and the DR channel
   keep today's strict gate and their layered hexagon.

**Three strongest objections to the consensus:**
1. **P1 rebuilds a solved problem.** P1's "narrow" layer is the first step of a year-scale platform. Twenty's engine
   alone took about 17k commits (R4 §5), and R7 §2.1 holds that one person cannot own breadth. The gate helps, but a
   founder who loves their chassis will be tempted to rationalize a failed spike (Dataverse PM, Frappe maintainer).
2. **Engine-first may have no market.**
   - The deadline cannot be met as an authorized provider.
   - 190 providers already compete.
   - Alanube already serves Alegra.
   - Odoo modules anchor the price at about US$80-295.

   Meanwhile Siigo, Alegra and Odoo consolidate the SME market with AI (R6 [35-37]). P5 revenue could be small and
   still delay the product (HubSpot PM).
3. **Two businesses for one person.** A vendor-facing API carries a 99.9 % SLO, fiscal-deadline alerts and DGII
   contingency duty (R6 §3). Operating it can consume the founder before the CRM phase begins.

**Dissent:**
- **Dataverse PM and Frappe maintainer:** skip P1 and run P2 now, in parallel with the engine.
- **HubSpot PM:** start P1 now, because the CRM relationship is the moat.
- **Atlassian lead:** P5→P3 inside Odoo's distribution.
- **Twenty maintainer:** P4 if the product turns out CRM-first.

**Kill criteria** (each one should make the owner abandon or reroute the recommendation):
1. TesteCF/CerteCF has not passed within about 6 weeks of the owner's enrolment. The engine thesis is then
   unproven: partner with an existing provider and become a localization shop on a framework (P2 or P3).
2. Selling to 20+ vendors, integrators and accountants yields no paying customer or letter of intent within 90
   days. Drop the engine-as-a-business and go product-first on Frappe (P2).
3. The P1 spike overruns 2× (more than 6 weeks), or the cost per entity does not fall by the third entity. Switch to
   P2.
4. Pilots demand a GL and inventory before the CRM. Use ERPNext or Odoo breadth, not P1.
5. Odoo ships native e-CF in Community `l10n_do`, or DGII opens a free channel that removes the API's value. Re-plan
   the go-to-market.
6. New generic features still appear in `packages/platform` after the freeze. That is the status-quo failure mode
   (P0), and it means P1 discipline has failed.
