# TRIBUNAL-1, judge C (long-term platform lens: lock-in, licensing, upgrade tax, ceiling)

## 1. Verdict

**Recommended: (A) P5 then P1, gated, with three amendments that make it a plan of my own (call it A+).**
Not (B). Not (C). Reason in one paragraph: in three years the owner regrets the choice that fixes the data
layer, the tenancy model and the license posture of the thing they sell. P2 fixes all three in someone else's
hands, and the panels never priced that. P1's lock-in is only "I maintain it", which a thin, deliberately narrow
layer on a stack they already own can carry. (C) "no default" is a refusal to decide: the panels' own evidence
(Panel 05 objection 3) says an experiment promised on 2026-10-20 has not run, and an undefaulted experiment never
fires. A default with numeric kill criteria is the honest version of a decision gate.

### Why P2 is the worse long-term bet (facts the panels and R1-R8 missed)
1. **Frappe is MariaDB-first.** Its own README: "uses Python and MariaDB". Postgres is partial/beta for the
   framework and ERPNext has no official Postgres support (forum evidence: threads cited below; ~1,300 queries
   unverified, ERPNext v16 on current Postgres reported broken). So P2 means a second database engine, no RLS, no
   shared transaction with the ledger, no same-Postgres outbox, and database-per-site tenancy. The "exemplary"
   storage layer that all seven researchers praised (R3) is thrown away for the breadth half of the product. No
   panel and no report mentions MariaDB (grep of R1-R8 and PANEL-01..10: only an unrelated R1 line).
2. **Two stacks, two operational models, one founder.** Frappe benches + MariaDB + Redis + Node asset build, next
   to the Postgres/Granian engine. The ops plan was already "heavier than a solo founder can carry" (SYNTHESIS).
3. **Licensing ceiling.** Frappe framework is MIT (verified), but every accounting/inventory feature worth
   buying is ERPNext GPL-3. Any self-hosted distribution to customers of an app importing ERPNext likely goes GPL
   (R7 row 114). A proprietary GL on top of GPL code is a legal question the owner cannot afford to learn about
   at customer number 50. The owner's own repo has **no LICENSE file** (verified), so the engine's own posture is
   undecided too.
4. **Upgrade tax and vendor gravity.** Frappe/ERPNext ship yearly majors; custom doctypes and overrides
   (`override_doctype_class`, last installed app wins, R4 [5]) break on them. Frappe Cloud is the natural host and
   is also the vendor's own SaaS. This mirrors the Odoo problem the panels rightly flagged for P3 but did not
   apply to P2.
5. **Type system and gate lost.** The repo's whole value proposition to agents (strict types, import contracts,
   100% coverage) does not extend into Frappe code (Panel 05 roles 2, 9).

### Why P1 is the better long-term bet, bounded
- Same Postgres, RLS, same-transaction ledger, one language, one gate. Ceiling: a narrow metadata layer
  (typed fields, JSONB custom fields, pipelines, saved views, templates) covers SME CRM + invoicing. It does not
  cover "SAP class" and I do not claim it does; at that point the honest options are hire, or buy/partner.
- The seam is kept open: the engine publishes a dated OpenAPI and a generated Python client (R8 4b). That makes the
  P2/P3 exit **reversible**: if the gate fails, a Frappe or Odoo connector is a client of the same engine. A+
  therefore does not burn the P2 option; P2 burns the P1 option (once on MariaDB, you do not come back).

### The A+ amendments to "P5 then P1"
1. **Do not sequence P5 as pure revenue-first.** The facts: the owner is **not yet an authorized provider**
   (HANDOFF: 15 Nov deadline "cannot be met as an authorized provider"; BACKLOG: first real TesteCF run not done,
   14 CerteCF steps, then PSFE application, ~10 business days answer, tests, sworn statement, INDOTEL custody;
   ADR 0010 still "Proposed"). So the first 90 days of P5 are certification, not sales. Plan for zero revenue until
   about day 60-90.
2. **Run the metadata slice in parallel, not after.** Certification waits on DGII/INDOTEL, not on code. Agents
   idle there. Use the idle calendar for the P1 slice (below) so the gate is evidence, not a promise.
3. **Price reality check in the gate.** 64% of emitters use DGII's free facturador; 190 providers; retail
   converges to US$0.02-0.05/doc (stack jury synthesis, verified in repo). The engine is a wholesale niche, not a
   mass market. The gate must test willingness to pay by vendors/integrators, not interest.

## 2. 90-day plan with numeric gates

Days 0-14 (before/around 15 Nov)
- Owner-side: get .p12 + TesteCF access; first real TesteCF run via `docs/runbooks/do/testecf-spike.md`. Decide
  INDOTEL mode (remote custody via an authorized entity is the faster path; accept ADR 0010 with that choice).
- Add `LICENSE` (proprietary, all rights reserved, for now) and a one-page license decision note (engine vs any
  future open connectors). Counsel on Art. 22 custody and Ley 172-13.
- Freeze chassis: no new generic platform features. Add `check-fast <package>` target and mutation testing for
  fiscal/money packages only. Add DGII-sourced fixtures beside builder-written goldens.
- Outreach: 15 conversations with vendors/integrators/accountants (target list from the 190 providers' non-provider
  customers, Odoo/ERPNext integrators in DR). Question: would you pay US$99-299/month plus per-document?

Days 15-45
- Complete the 14 CerteCF steps; settle spike findings F1-F16; submit PSFE application.
- Managed container platform deploy (not K8s), restore drill, per-tenant export, 3-5 symptom runbooks.
- Start the P1 slice on a branch (single writer agent + clean-context reviewer): `packages/metadata` with one
  object (customer/contact), field registry (typed JSONB custom fields), generic CRUD/list/filter API, one
  generated TS form+list, one cross-module reporting view. No spec bypass: amend ADR 0004/0011 by superseding ADRs
  first (spec wins, AGENTS.md).

Days 46-90
- Finish slice; thin MCP server over OpenAPI; sandbox customers on CerteCF.
- Gate at day 90 (or 14 days after PSFE answer, whichever is later, max day 120).

### Gate criteria (numbers)
Choose **P1 (continue)** if ALL hold:
- Slice (field defined by tenant -> stored -> API -> form -> list -> filter -> CSV export + one cross-module
  report) done in <= 10 working days of agent time, 0 suppressions, 0 `Any`, `poe check` green, per-package fast
  gate <= 3 min.
- Mutation score >= 80% on fiscal+money packages; TesteCF end-to-end pass with >= 1 DGII-sourced fixture per
  e-CF type issued.
- Demand: >= 3 written LOIs or paid pilots (any size) from vendors/integrators/accountants; or >= 1 paying
  customer, within 90 days of TesteCF certification.
Choose **P2/P3 connector route (engine only + ecosystem plugin, no own breadth)** if the slice fails (>15 working
days or any suppression) but demand is >= 3 LOIs: then ship a Frappe/Odoo DR e-CF connector as a client of the
engine, and do not build breadth.
Choose **stop breadth, run engine as a lifestyle service** if demand < 3 LOIs and no paid pilot: the market is
too thin; do not invest in breadth.

Kill criteria for the whole plan: buyers refuse third-party custody; DGII certification blocked > 120 days;
PSFE denied.

## 3. Where the panels were wrong or biased

1. **They compared P1 and P2 as if only typing cost mattered.** Nobody priced the data-layer switch (MariaDB,
   database-per-site, no RLS). Panel 02 favored P1 for DB reasons but its "mind-changer" (Frappe Cloud 1,000+
   sites) tests ops, not the engine split. Panel 05's spike plans a "Frappe app" in 10 days without noting the
   DB mismatch with the ledger design.
2. **Shared-context bias.** All panels read the same R1-R8 and the same SYNTHESIS. The synthesis lists "Frappe
   (MIT, in-process)" as a peer of P1; the one fact that breaks the peerage (MariaDB) was in none of the reports.
   Convergent scores (P5 first) reflect common inputs, not independent confirmation.
3. **Type-system / harness bias (Panel 05 admitted it).** Roles paid to value gates over-score P1; roles paid
   for distribution over-score P2/P3. Neither group priced license risk; no panel read GPL ERPNext vs proprietary
   distribution.
4. **P5 treated as revenue-first, but not yet certified.** Panels cited the deadline "21 days away" as a market
   clock; HANDOFF already states the owner cannot meet it as an authorized provider. The window is the late tail,
   not the cohort, and revenue is 60-90+ days away.
5. **Spec rigidity (F5) is underweighted.** AGENTS.md: spec wins. P1 needs superseding ADRs before code; panels
   flagged it but sequenced the slice after the gate.
6. **"Mutation-checked critical paths" is folklore.** Verified: not in `poe check`; mutation checks exist only as
   manual notes in agent task reports. CONTEXT.md overstated the bar.

What the panels missed: license file absent; MariaDB; 64% free-facturador share means price pressure;
reversibility argument (A+ preserves the P2 exit, P2 forecloses P1).

## 4. Strongest case against A+

The "narrow metadata layer" is how every framework begins, and a solo founder with agents will maintain it
forever: renderers, permissions, migrations, reports, import/export, i18n. Frappe gives these in a day and
thousands of contributors maintain them. If DR SMEs mostly need quotes + invoices + inventory, Frappe/ERPNext (or
Odoo CE) gets to feature parity in months and the owner's engine plus a DR connector ships faster; the MariaDB
split is then just an API boundary, not a problem, since the engine stays Postgres.

Why A+ still wins: (a) that is exactly the P2 route A+ keeps as a *fallback* with the same engine seam, so the
cost of being wrong is a connector, not a rewrite; (b) the framework route's real cost is not build time but the
three-year tail (GPL accounting, yearly majors, second DB, vendor gravity, no typed gate) and a product that
competes with Odoo/Alegra on their ground; (c) the demand gate decides whether any breadth is built, so the
maintenance burden arrives only if customers pull.

## 5. Folder structure (R8)

Keep the current tree (R8 5: not the wrong path). Following R8 4a, but staged:
- Now: no change except `docs/spec/` split into indexed files with superseding ADRs, `contracts/openapi/` dated
  versions, `clients/python/` generated, `deploy/` for the managed platform, `LICENSE`.
- Slice (day 15+): add `packages/metadata/` (engine; import contract: below modules, above kernel/platform) and
  `modules/<m>/metadata/objects/*.toml` for the single customer object; entity-slice layout (`customers/` with
  domain, commands, queries, repository, http) for new breadth modules; keep layered hexagon for fiscal, signer,
  `countries/do`. Tests mirror src; `contract/dgii-official/` apart from `golden/`.
- Only if the gate passes: `apps/web` + `packages/ui` + `packages/client-ts` with uv `exclude`, `packages/reporting`.
- Not now: second repo. Create `ledger-engine` split (R8 4b) only if the connector route is chosen, because the
  trimmed tree is nearly identical.
- Single-schema outbox/jobs for breadth modules (Panel 02 DBA point) as an ADR, not a new package.

## 6. Verified claims

| Claim | Evidence | Verdict |
|---|---|---|
| No LICENSE file in repo | `ls LICENSE*` at repo root: not found; no license key in pyproject.toml | TRUE |
| 519 files use 3.15-only lazy imports; `requires-python >=3.15` | grep `^lazy import/from` count 519; pyproject.toml line 5 | TRUE |
| Mutation testing absent from the gate | `poe check` sequence (pyproject.toml): no mutation task; spec/ADR grep empty; mutation only in `.superpowers/sdd/*/task-*-report.md` manual notes | TRUE (CONTEXT's "mutation-checked" is manual, not enforced) |
| Golden XML is written by the code under test | `countries/do/tests/contract/test_golden.py`: "each golden written by its builder" | TRUE for the "other documents" set (partial; XSD is the outside oracle) |
| No real TesteCF run yet | docs/handoff/BACKLOG.md section 2: "First real TesteCF run with the owner's own RNC" still listed as to-do | TRUE |
| Owner cannot be an authorized provider by 15 Nov 2026 | HANDOFF.md line 26; stack-jury-synthesis line 18; BACKLOG PSFE path (~10 business days, tests, sworn statement, INDOTEL custody) | TRUE; changes P5 timing |
| ADR 0010 signer still "Proposed" with INDOTEL Art. 22 custody dependency | BACKLOG section 3 header; ADR 0010 lines 10-12, 40 | TRUE |
| 190 providers, 80k emitters, ~64% on free facturador, US$0.02-0.05/doc | docs/reviews/2026-10-20-stack-jury-synthesis.md lines 101, 104 | TRUE as reported (secondary, from DGII per the jury); I did not re-verify at DGII |
| Frappe Framework is MIT, uses MariaDB | https://github.com/frappe/frappe (fetched): MIT; "Python and MariaDB" | TRUE |
| Frappe Postgres is partial/beta; ERPNext has no official Postgres support | https://discuss.frappe.io/t/is-erpnext-officially-supported-with-postgresql/163329 , https://discuss.frappe.io/t/postgres-support/162440 , https://github.com/frappe/erpnext/issues/24389 (search summary, community not official; confirm in release notes) | LIKELY TRUE; not verified in official release notes |
| ERPNext is GPL-3.0 | R7 row 16 citing https://github.com/frappe/erpnext (not re-fetched by me) | ACCEPTED from report |
| 11 ADRs; ADR 0004 schema-per-module, 0011 flows | `ls docs/adr` | TRUE |
