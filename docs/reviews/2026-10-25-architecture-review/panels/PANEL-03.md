# PANEL-03: ERP/CRM domain panel

Ten roles from the ERP/CRM implementation world. The evidence is R1-R8, CONTEXT.md, SYNTHESIS.md and the repo
(read-only). Two repo checks were made for this panel:
- `countries/do/src/ledger_country_do/regime/document_types.py` covers all ten e-CF types (31-34, 41, 43-47).
- A grep of the spec (`docs/superpowers/specs/2026-10-13-ledger-platform-v2-design.md`) finds no withholding
  ("retención"/"withhold") and no DGII 606/607 report formats. Accounting and inventory are "designed, not built"
  (spec line 84).

Scores run from 0 to 10, where 10 is the best direction for this owner.

## 1. Scores

| Role | P0 | P1 | P2 | P3 | P4 | P5 | P6 | Favorite |
|---|---|---|---|---|---|---|---|---|
| 1. SAP S/4HANA functional consultant (finance, clean core) | 2 | 6 | 7 | 6 | 2 | 8 | 1 | P5→P2 |
| 2. Odoo implementation partner, LATAM | 1 | 4 | 5 | 9 | 2 | 7 | 1 | P3 (the engine behind it) |
| 3. ERPNext/Frappe implementer | 1 | 5 | 9 | 5 | 3 | 7 | 1 | P2 |
| 4. Salesforce certified technical architect | 2 | 8 | 5 | 3 | 6 | 6 | 2 | P1 |
| 5. CRM product manager, mid-market CRM vendor | 1 | 7 | 4 | 4 | 8 | 5 | 1 | P4 (extend Twenty, engine as service) |
| 6. CPA in the DR serving SMEs | 3 | 7 | 5 | 5 | 2 | 8 | 1 | P5→P1 |
| 7. LATAM e-invoicing / tax-tech specialist | 3 | 5 | 5 | 5 | 2 | 9 | 0 | P5 |
| 8. Inventory / supply-chain ERP consultant | 0 | 3 | 8 | 7 | 1 | 6 | 1 | P2 |
| 9. ERP implementation PM for SMEs | 0 | 5 | 6 | 6 | 2 | 9 | 0 | P5→(P2 or P3, by the 5-day experiment) |
| 10. Business Central / Dynamics partner (AL) | 1 | 6 | 6 | 6 | 3 | 8 | 3 | P5 |

## 2. Per-role judgments

**1. SAP S/4HANA functional consultant (finance, clean core).**
- Top, P5 (8): clean core plus side-by-side is the pattern SAP spent a decade retrofitting (R1 §3.7, [13][14]).
  The certified e-CF engine is the side-by-side extension, and no CRM/ERP core should own it.
- Bottom, P6 (1): a port re-certifies with DGII and still lacks a Universal-Journal-grade ledger (R1 P9, R5 §3).
- Favorite: P5→P2. ERPNext's GL is one journal with dimensions, closer to ACDOCA than anything the owner could
  hand-build quickly.
- Mind-changer: the 5-day experiment showing that ERPNext cannot post e-CF types 41/43/47 (self-billed purchases)
  without modifying core DocTypes, which is a Level-D extension.
- Folder: R8 §4b, i.e. `ledger-engine` plus `clients/python`, and a separate Frappe app.

**2. Odoo implementation partner, LATAM.**
- Top, P3 (9): Odoo 18 CE already ships `l10n_do` (R8 §2.3 [11]). DR e-CF modules already sell at US$80-295
  (jury synthesis line 102), and Odoo grows about 48 % a year (R6 [35]). The buyers and the partner channel exist
  today.
- Bottom, P0 (1): hand-built breadth with no UI is a year behind any Odoo partner's demo.
- Honest caveats [role judgment]: full accounting reports are an Enterprise feature, so CE needs OCA addons. The
  yearly porting tax is real (R4 [7][8]).
- Favorite: P3, with the engine behind a `l10n_do_ecf` addon.
- Mind-changer: Odoo S.A. shipping native DR e-CF in `l10n_do`, as it has done for other LATAM e-invoicing
  [role judgment].
- Folder: OCA-style repo, one addon per directory, a branch per major version (R8 §4b).

**3. ERPNext/Frappe implementer.**
- Top, P2 (9): the Frappe framework is MIT, which allows proprietary apps. It brings DocTypes, the generated UI,
  the report builder and an MCP server (R7 [17]). India Compliance is the proven pattern for a heavy country
  (R8 §2.3 [21]).
- Bottom, P0 (1): the chassis rebuilds what Frappe already gives (R7 §2.3).
- Weaknesses I admit: ERPNext is GPL, there is one site and database per tenant to operate, and when two apps
  override a DocType class, "last installed app wins" (R4 [5]).
- Favorite: P2.
- Mind-changer: per-site hosting cost making a US$15-30/month DR micro-SME unprofitable on Frappe Cloud or
  self-run benches (not measured in R6).
- Folder: `<brand>_do` Frappe app with a `dr_compliance/doctype/ecf_submission/` slice, and the engine client as a
  thin wrapper (R8 §4b).

**4. Salesforce certified technical architect.**
- Top, P1 (8): the moat is owning the metadata contract that a marketplace stands on (R1 P8). An ISV on someone
  else's platform has none. R3 §2.2 shows a narrow registry plus JSONB on pool tenancy is the Salesforce
  UDD-plus-pivot design scaled down. RLS pooling already beats DB-per-tenant on cost (R3 [2][12]).
- Bottom, P6 (2): it rewrites the part that is already right.
- Favorite: P1, kept narrow: no custom objects at launch, given that 51 % of custom objects go unused (R4 [14]).
- Mind-changer: if a field registry plus a generated form/list for three objects is not demo-able in about
  6 weeks, the founder cannot own the platform and should adopt one.
- Folder: R8 §4a, with `packages/metadata`, `modules/<m>/metadata/` and entity slices.

**5. CRM product manager, mid-market CRM vendor.**
- Top, P4 (8): buyers judge the CRM on pipeline UX, the activity timeline, MCP access and AI (R6 §4 [28][29]).
  Odoo and ERPNext CRM screens are generic [role judgment]. Twenty is metadata-driven and allows proprietary apps
  under the AGPL exception (R7 [20]).
- Bottom, P0 (1): "UI later" means no CRM at all.
- I discount R5's agent benchmarks for TypeScript, because they come from older models and small samples
  (R5 [33][34]).
- Favorite: P4, extending Twenty rather than building, with the Python engine as a service.
- Mind-changer: if DR pilots rank e-CF plus books above pipeline, the ERP half leads and P4 loses.
- Folder: a separate Twenty app repo built with `defineObject` (R1 [43]), and the engine untouched (R8 §4b).

**6. CPA in the DR serving SMEs.**
- Top, P5 (8): my clients need accepted e-CF and books that close, not a CRM. Accountants are a channel: one firm
  handles dozens of RNCs (jury synthesis line 104).
- Pool tenancy with org/company dimensions (R3 §3) suits a multi-client accountant portal better than Odoo's
  "multi-company in one DB has no isolation" (R1 [36]).
- Bottom, P4 (2): no ledger.
- The gap I see: the spec has no ITBIS/ISR withholding and no 606/607 (grep). Where DGII still requires them,
  these belong in the engine [role judgment].
- Favorite: P5→P1.
- Mind-changer: DGII raising the free facturador's 150-invoice cap, used by about 64 % of emitters (jury synthesis
  line 101), which would shrink the paid small-SME market.
- Folder: `countries/do/reports/` for statutory reports (R8 §4a).

**7. LATAM e-invoicing and tax-tech specialist (DGII, SAT, DIAN).**
- Top, P5 (9): certification, contingency handling, the receiver and the 10-year WORM archive are the moat, and the
  repo already has them (R3 §2.4, SYNTHESIS §1.4). This is how PACs in Mexico and DIAN providers make money: a
  wholesale API per document [role judgment].
- Bottom, P6 (0): it voids certification evidence for nothing (R5 §3, R7 §3B).
- Next countries should come as new `countries/<cc>` channels, not as ERP breadth.
- Favorite: P5.
- Mind-changer: DGII provider authorization requiring a local entity, audits or 24/7 support that a solo founder
  cannot supply.
- Folder: R8 §4b `ledger-engine`, where every country is `countries/<cc>/channels/<channel>_v1/`, plus generated
  SDKs.

**8. Inventory and supply-chain ERP consultant.**
- Top, P2 (8): stock valuation (FIFO/moving average), lots and serials, landed cost and multi-warehouse take years
  to get right [role judgment]. ERPNext's stock module is mature.
- Under P0/P1's events-only rules, a stock move to GL posting becomes eventual consistency, which is the exact
  failure R2 §3 names for document chains.
- Bottom, P0 (0).
- Favorite: P2, with inventory and accounting left untouched in ERPNext.
- Mind-changer: target customers being services SMEs with no stock (DR professional services). Then inventory
  drops out and P1 becomes viable.
- Folder: no stock code in this repo. The Frappe app only hooks `Sales Invoice` and `Purchase Invoice` (R8 §4b).

**9. ERP implementation PM for SMEs.**
- Top, P5 (9): the last cohort's deadline is 15 Nov 2026, three weeks away (R6 [38]). The engine is not in
  production or certified, so the greenfield rush is already lost. What remains is switchers and vendors that need
  a certified backend, which is a timeboxable B2B sale.
- Bottom, P0/P6 (0): no ship date.
- Favorite: P5 for 90 days, then P2 or P3 chosen by the jury's 5-day experiment.
- Mind-changer: no three named prospects (jury synthesis line 138) with signed LOIs within 60 days.
- Folder: two repos with a contract test on each side (R8 §4b), and no shared code.

**10. Business Central / Dynamics partner (AL).**
- Top, P5 (8): BC itself is "built almost entirely of extensions" by Microsoft's organization (R5 [15]). A solo
  founder should be the extension: one certified e-CF service with connectors for Odoo, ERPNext, BC and SAP B1.
  This is the Avalara pattern [role judgment].
- I score P6 higher than the panel (3): .NET is the DR's largest pool (R5 [43]), but only on R5's hiring trigger.
- Bottom, P0 (1).
- Favorite: P5.
- Mind-changer: Alanube-style providers already locking ERP vendors in (Alegra runs on Alanube; jury synthesis
  line 102).
- Folder: R8 §4b `ledger-engine` with `clients/python` and `clients/typescript`. Each connector, AL included,
  lives in its own repo.

## 3. Panel summary

**Mean scores:** P0 1.4 | P1 5.6 | P2 6.0 | P3 5.6 | P4 3.1 | P5 7.3 | P6 1.1

**Favorite counts:**
- P5-first: 5 (P5 alone ×2, P5→P2, P5→P1, P5→P2/P3)
- P2: 2
- P3: 1
- P1: 1
- P4: 1

If second steps are counted, P2 appears in 4 favorites, P1 in 2 and P3 in 2.

### Consensus recommendation: P5 now, gated to a breadth decision, leaning P2

1. **Next 90 days: engine-only (P5).**
   - Freeze the chassis; no new generic platform features.
   - Deploy on a managed container platform (R6 §2.3).
   - Pass TesteCF with tests taken from DGII samples (R7 §2.2).
   - Ship the API, the receiver and signer custody.
   - Build one connector where buyers already are. The panel splits between Odoo (an existing module market) and
     ERPNext.
   - Add DR statutory outputs (withholding, and 606/607 where still required) under `countries/do/reports/`, so
     that accountants become a channel.
2. **In parallel, within those 90 days: run the jury's 5-day Odoo CE vs ERPNext experiment.** Use R4 §5's
   checklist: the cost of a custom field, a pipeline stage and a print template; out-of-process extension; the
   upgrade path. Add the inventory and self-billed-purchase (41/43/47) posting tests from this panel.
3. **Day 90 gate.** With at least 3 paying API customers or signed LOIs, choose the breadth path:
   - **P2 (Frappe) by default.** The MIT framework allows proprietary apps, it does not make you compete with Odoo
     S.A., and it has a mature stock and GL.
   - **P3** if the first revenue comes through Odoo partners.
   - **P1** only if accountant multi-client pool tenancy proves decisive, and then only narrow (R4 §2).

Under every branch, P0 and P6 are rejected.

### Three strongest objections

1. **Commodity revenue.** There are 190 authorized providers, and prices are converging on US$0.02-0.05 per
   document (jury synthesis lines 101-104). An engine-only business may never fund the CRM the owner actually
   wants. P5 can turn into a permanent detour from the stated goal (CONTEXT.md).
2. **The window argument is half spent.** 15 Nov 2026 is three weeks away (R6 [38]) and the engine is not in
   production. Incumbents (Alegra/Alanube, Odoo DR modules) already hold the onboarding wave. P5 must win on
   switching and reliability, not on the deadline.
3. **Deferral risk.** Postponing the breadth choice lets agents keep polishing the chassis, which is P0 by drift
   (R7 §2.3). Connector work for the platform that loses the experiment is partly wasted. A platform dependency
   (P2/P3) also hands roadmap control to Frappe or Odoo S.A.

### Dissent

- **CRM PM (P4) and Salesforce CTA (P1).** P5 abandons the CRM half, and the moat is owning the metadata and UX,
  not a commoditizing pipe. Both want a CRM surface in front of DR SMEs within 6 months.
- **Odoo partner (P3).** Skip the generic API phase and ship `l10n_do_ecf` into the existing Odoo market now.
- **Inventory consultant and Frappe implementer.** Commit to P2 today rather than after 90 days, because
  inventory and GL correctness are the long pole.

### Kill criteria (signals to abandon this recommendation)

- No 3 named prospects with LOIs by day 60, or fewer than 3 paying API customers by day 90.
- TesteCF/DGII certification not passed within 8 weeks.
- Odoo or Alegra ships native DR e-CF that removes connector demand, or the wholesale price per document falls
  below the cost of support.
- `packages/platform` grows during the P5 phase: the freeze has failed.
- Pilots consistently ask for pipeline/CRM first. Switch to P1 (narrow) or P4.
- The 5-day experiment shows a framework covers less than 80 % of DR SME needs (the jury's threshold), or that
  e-CF purchase types cannot post without core modification. Then breadth goes to P1, not P2/P3.
