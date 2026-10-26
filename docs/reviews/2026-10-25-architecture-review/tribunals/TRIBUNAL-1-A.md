# TRIBUNAL 1, judge A (skeptical technical judge)

Method: I read the brief, CONTEXT, PROPOSALS, SYNTHESIS, the R8 verdict and the panel digests, then checked the
claims below against the repo (read-only, `main`) instead of trusting the panels. Today is 2026-10-25.

## 1. Recommendation: option (A) with two amendments, called "P5-cert, then P1 only if pulled"

Single plan: **certify first, sell the engine, build nothing new in breadth until customers pull it, and default to P1
(not P2) when they do.** Frappe (P2) is the named fallback, not the default.

### Why P5 first (this is where the panels are right, but for a reason they underweight)

The engine is **not yet usable by anyone but the owner.** The BACKLOG (section 2) lists, as still to do: the first real
TesteCF run, settling spike findings F1-F16 against the real DGII, the 14 CerteCF steps for the owner's own RNC, then
the PSFE application (about 10 business days for DGII's answer, then tests, a sworn statement, INDOTEL-compliant
custody). ADR 0010 (signer) is still "Proposed": the owner has not accepted the KMS, the operated backend or the
custody model. Every vendor, integrator or accountant who might buy P5 depends on that chain. So the next 90 days are
decided by certification, not by P1 versus P2. A CRM built on top of an uncertified engine, by anyone's framework, is
a CRM that cannot legally issue the invoice.

### Why P1 beats P2 as the default (and why that is a weak preference)

- P2 means two runtimes, two test cultures and two security models run by one person. Frappe/ERPNext code is not held
  to this repo's gate (basedpyright all, mypy strict, zero suppressions, 100 % branch). Database-per-site also
  mismatches an engine whose isolation is RLS in one database. The panels' DBA and data roles said so with reasons.
- P1's cost is bounded if kept narrow (field registry, picklists, one pipeline, generated UI), and the manifest ->
  codegen pipeline (ADR 0009) already exists, so descriptors are an extension, not a new machine.
- But my confidence is moderate. P1 is also where the owner's sunk cost (about 52k lines, 469 commits in two weeks)
  pulls. The panels were fed the same reports and mostly the same repo, so their P1 lean is not independent evidence.
  That is why the plan sets P1 as default only and puts a numeric gate and a time-boxed Frappe spike in front of it.

### 90-day plan

Days 0-10 (hygiene, cheap, no new features)
1. Add `LICENSE` (proprietary or chosen) and a dependency-licence check to `poe check`. There is none today.
2. Accept or amend ADR 0010 in writing (KMS choice, custody model, who holds the keys). Do not leave it Proposed
   while selling custody.
3. Freeze the chassis: no new generic platform feature without a failing customer-facing test.
4. Put one automated mutation run (mutmut or cosmic-ray) on the fiscal, numbering, state-machine and signer paths
   into CI. Today it is manual (see section 5).

Days 0-45 (certify)
5. First real TesteCF run with the owner's RNC and certificate (runbook `docs/runbooks/do/testecf-spike.md`).
6. Replace the self-written goldens by DGII-sourced fixtures: put DGII's own samples and XSDs in
   `contract/dgii-official/`, keep builder output in `golden/` as a regression, never as the oracle.
7. Complete the 14 CerteCF steps for the owner's own RNC. This is also the cheapest first "customer": the owner.
8. Do not chase the 15 Nov 2026 cohort. A PSFE application started now cannot finish before it. The deadline still
   creates a long tail of late and penalised emitters; aim at that tail.

Days 30-90 (sell)
9. File the PSFE application by day 45. Sell the engine meanwhile as a pilot: 15 conversations with Odoo/ERPNext
   partners, accounting firms and small POS/ERP vendors; offer a connector for one ecosystem (Odoo CE module) as the
   demo, nothing more.
10. Build only "portal-lite" (BACKLOG item 12: certificate upload, customers, invoices, PDF, QR) as plain generated
    UI. This is the first thing pilot users will ask for and is not yet a metadata layer.
11. Day 60: if the gate below is not yet met, run a 3-day Frappe/ERPNext spike with a fixed script (one custom field,
    one pipeline, one DR invoice through the engine API). Record the result. Do not extend it.

### Gate at day 90 (numbers)

Open P1 (narrow metadata layer, then CRM) only if ALL hold:
- TesteCF end-to-end green on DGII-sourced fixtures, and the 14 CerteCF steps done for the owner's RNC.
- PSFE application filed (and, ideally, DGII's first answer received).
- At least 3 external parties (not friends) in pilot, at least 1 sending documents in test, and at least 2 of the
  3 asking unprompted for the same non-API feature (UI, report, CRM object).
- At least US$1,000 in signed MRR or paid pilot commitments, or at least 2 written paid commitments of at least
  US$150/month.
- The Frappe spike (if run) shows P1's first slice (one custom field end to end) is feasible in at most 10 working
  days; otherwise reopen P2.

Switch to P2 (or an Odoo connector business, P3-lite) if: the customers' pull is for accounting/inventory breadth
(not CRM/invoicing UI), or the P1 first slice exceeds 10 working days, or the spike shows Frappe delivering the same
slice in at most 3.

Stop and rethink (kill) if by day 90: zero paying pilots, wholesale per-document margin is under about US$0.01, or
PSFE authorization is refused or stalls past day 150 with no clear path.

## 2. Where the panels were wrong or biased

1. **Shared-context bias, presented as 100 independent judges.** All 100 roles read the same R1-R8 and mostly the same
   repo digest. The 7.14 vs 6.20 vs 5.62 spread is a measure of agreement with the reports, not of independent
   evidence. P1 versus P2 differs by 0.58 on a 10-point scale with high variance; it is a tie, and should have been
   reported as one.
2. **Panels treated the deadline as the main constraint when it is mostly out of reach.** 15 Nov 2026 is 21 days away.
   BACKLOG section 2 shows the path to a PSFE authorisation is longer than that. Several panels used the date as an
   argument for "sell now" without noting the engine cannot be sold as a provider until it is authorised. They also
   compared the owner to 190 incumbents without asking what share of those are real engines versus resellers.
3. **Mutation testing was misread.** Panel 5 said it is "not part of the gate" (true) and drew the conclusion that
   "mutation checks everywhere" in P0 is only an aspiration. The task reports show mutation checks done by hand
   and recorded per task (apply, run, revert). It is practiced but not enforced. The right fix is to automate it on
   the money paths, not to discount the claim.
4. **P1's price was under-counted.** The spec wins over code (AGENTS.md); P1 loosens ADR 0004 and ADR 0011 and the
   events-only rule, so it needs spec and ADR amendments first (Panel 5 found this; most panels ignored it).
   Also the gate takes a long time and mutation is manual, so "lighter gate on CRUD" has to be built, not just said.
5. **Market numbers are weakly sourced.** The price band of US$0.02-0.05 per document appears in the panels as
   "[role judgment from jury]" and a panel illustration, not a primary DGII or vendor price list. The 80,000 vs
   100,000 emitter figures differ between the jury synthesis (80,000, 64 % on the free facturador) and R6 (100,000).
   Nobody reconciled them. Revenue arithmetic (such as the US$6k MRR illustration) is built on unverified inputs.

### What the panels missed
- The owner is the only tested customer. Certification of their own RNC is both the cheapest proof and a hard
  prerequisite, yet only a few panels made it step one.
- ADR 0010 is still Proposed while the engine's value proposition is custody (Decreto 587-24 Art. 22 is cited in the
  ADR itself). Selling custody on an unaccepted ADR is a legal and trust risk.
- No LICENSE file: a vendor cannot legally integrate or evaluate the code.
- 519 files rely on a language feature that exists only in Python 3.15. A pin back is a codemod, not a config change,
  so R5's "cheap fix" is wrong for this repo. It is a dependency and hiring risk to record, not to fix now.
- No measure of where the founder's time goes: 17.0k lines of platform chassis (about 32 % of non-test code) were built
  before one external user.

## 3. Strongest case against my recommendation

"P5 is a commodity business. 190 authorised providers, Alegra on Alanube, prices near US$0.02-0.05 per document: the
owner will spend 90 days on certification and arrive with a product that sells at near cost. The real money is the
application layer, and the window (15 Nov, Alegra, Siigo, Odoo) is closing. Go straight to P2 or P3 and ship a
CRM + invoicing product on a framework in 30 days."

Why it still loses:
- The framework path also needs the e-CF engine certified; Odoo and Frappe localisations for DR would have to call
  something that signs, numbers and archives. Skipping certification skips the product's legal ability to invoice.
- A CRM on Frappe or Odoo in a market where Odoo, Alegra and Siigo are funded is a feature fight against better
  resourced teams, and the owner's advantage (a strict, typed, auditable fiscal core) is not visible there.
- The cost of being wrong is asymmetric. The 90-day plan is mostly work that every other option also needs
  (certification, licence, custody decision). Only the optional day-60 spike and the portal-lite are P5-specific.
- If the commodity thesis is true, the day-90 gate shows it (margin under US$0.01, no paid pilots) and triggers the
  Odoo-connector pivot with three months spent on shared prerequisites, not wasted.

## 4. Folder structure decision (R8)

- **Days 0-90: no re-layout.** R8's own verdict is that the layout is above the median of the platforms surveyed.
  Changes allowed: `LICENSE` at root; split the 2,187-line spec into an indexed set of files (R8 "a map, not a
  manual"); `countries/do/tests/contract/dgii-official/` beside `golden/`; fix the 800-line `repositories.py` drift.
  Keep `AGENTS.md` short.
- **If the gate opens P1:** R8 Scenario A. Add `packages/metadata/` (registry, loader, generic CRUD/list API),
  `packages/reporting/` later, `apps/web/` and `packages/client-ts` as TypeScript units excluded from the uv
  workspace, `modules/<m>/metadata/objects/*.toml` for first-party objects, entity slices (quotes/, sales_orders/) in
  breadth modules, and the layered hexagon kept for fiscal, signer and the DR channel. New import-linter contract:
  metadata engine below modules. Tenant overlays are rows, not files.
- **If the gate opens P2:** R8 Scenario B. This repo becomes `ledger-engine` almost unchanged, with a generated
  `clients/python`; the Frappe app lives in a separate repository following its own conventions. Do not mix the trees.

## 5. Verified claims

Evidence paths are under `C:\Users\brian\Desktop\ledger` unless noted.

| # | Claim | Evidence | Verdict |
|---|---|---|---|
| 1 | Mutation testing is absent from the gate | `pyproject.toml` `check` sequence: format-check, lint, suppressions, types, arch, deps, spell, proto-lint, proto-breaking, devtools, dead, test; no mutation task | Confirmed. Nuance: mutation checks are done by hand and logged in `.superpowers/sdd/2026-10-11-milestone-1-foundations/task-5-report.md` (line 29 onward) and others. Practiced, not enforced |
| 2 | 519 files use 3.15-only `lazy` imports | grep for `^\s*lazy (import\|from)` over `*.py` returned 519 files; `requires-python = ">=3.15"`, `.python-version` is 3.15 | Confirmed (count matches exactly) |
| 3 | The e-CF goldens are written by the code under test | `countries/do/tests/contract/test_golden.py:151`: "each golden written by its builder" | Confirmed for the docstring. I did not audit whether XSD validation is also applied to every golden |
| 4 | No LICENSE file | `ls LICENSE*` at repo root: no such file | Confirmed |
| 5 | ADR 0010 has open risks and is not accepted | `docs/adr/0010-signer-isolation.md` line 3 "Status: Proposed", lines 38-40 "the owner has not accepted the production choices (KMS, backend, INDOTEL custody model)"; Art. 22 cited at line 10 | Confirmed |
| 6 | No real TesteCF run yet | `docs/handoff/BACKLOG.md` section 2: "First real TesteCF run with the owner's own RNC and certificate"; response shapes and `ACECF.xml` name "UNCONFIRMED: verify on TesteCF" | Confirmed |
| 7 | Path to authorisation is long | `BACKLOG.md` section 2: 14 CerteCF steps, then PSFE application, DGII answers in about 10 business days, tests, sworn statement, INDOTEL custody | Confirmed (from repo's own plan, not from DGII) |
| 8 | No UI exists | `git ls-files` matches zero `.ts`/`.tsx` files | Confirmed |
| 9 | Size: chassis about 30 % of the code | Non-test Python: platform 17,038; modules 19,488; countries 15,928; apps 49 lines. Tests counted 85,922 lines (CONTEXT says 72k; my count includes all `tests` paths) | Platform share about 32 %: confirmed. Test total differs from CONTEXT; immaterial |
| 10 | 190 providers and 80k emitters | `docs/reviews/2026-10-20-stack-jury-synthesis.md:101` ("190 authorized providers and over 80,000 electronic emitters, about 64 % on the free facturador") | Confirmed as an internal statement; not checked at DGII. Conflicts with R6's 100,000 emitters |
| 11 | 15 Nov 2026 last cohort deadline | R6.md lines 185-192 citing its source [38] | Not independently verified (no primary fetch). Plausible; treat as secondary |
| 12 | Price US$0.02-0.05 per document | Only in panel text (PANEL-01 line 171, PANEL-04 line 14) tagged as role judgment / jury | Not verified; no primary source found |
| 13 | P1 requires spec/ADR changes first | `AGENTS.md`: "When code and spec disagree, the spec wins"; ADRs 0004 (schema per module) and 0011 exist | Confirmed |
| 14 | 100 independent judges | Panels were told to read the same CONTEXT/PROPOSALS/R1-R8 (`PANEL-BRIEF.md`) | Confirmed: shared inputs, not independent |
