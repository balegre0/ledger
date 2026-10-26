# TRIBUNAL-2, judge D (red-team adversary)

Date: 2026-10-25. Inputs read: TRIBUNAL-2-BRIEF, CONTEXT, SYNTHESIS, PROPOSALS, VERIFY, TRIBUNAL-1-A/B/C (A and B in full, C head), panel digests, and the repo's BACKLOG section 2 and HANDOFF line 26. I did not re-audit code; repo facts below are the ones judges A, B and C already verified, plus the BACKLOG/HANDOFF lines I re-read. Tags: [V] = in VERIFY.md, [R] = repo doc, [J] = my judgement, [?] = unverified.

## 1. The flaw first: how the owner most likely fails the candidate "P5 then P1"

### 1.1 The single most likely failure (ranked first)

**The owner spends 90 days inside a certification tunnel whose critical path is outside their control, fills the idle time with the work they are good at (quality gates, ADRs, mutation automation, spec split, then the P1 slice), has no real customer conversation until about day 45-60, and arrives at the day-90 gate with a clean repo, a half-finished DGII process, zero revenue and a gate that cannot pass.**

Why I rate this as the most likely, not just possible:

1. The candidate's first 45-60 days are a list of internal, controllable, comfortable tasks: LICENSE, ADR 0010, mutation testing, spec split, fixtures. Selling (15 conversations) sits in "days 30-90" in judge A's version. The founder has shipped ~55k lines and 469 commits in two weeks with agents. They have never had a customer, a DGII round trip or a price. The plan front-loads what they have proven they can do and defers what they have never done. That is the classic solo-founder failure and the plan builds it in.
2. **The day-90 gate is unreachable by construction.** The candidate gate wants signed MRR or paid pilots (A: US$1,000 MRR or 2 commitments of US$150/month) by day 90. But the buyers of a wholesale engine (software vendors, integrators) can only legally use it to issue e-CF through an authorized PSFE or as self-certified emitters [R: HANDOFF line 26 "cannot be met as an authorized provider"]. The PSFE chain is: own RNC certified, 14 CerteCF steps, application, DGII answer "in about 10 business days", then tests, sworn statement, INDOTEL-compliant custody [R: BACKLOG 2]. Only the first answer has a duration; everything after it is open-ended. So no vendor can pay for production use before authorization, and authorization has no date. A gate that cannot pass either gets quietly extended (the "experiment that never fires" problem judge C names) or kills a sound plan for an external reason.
3. **The market the plan sells into is thin on the facts we have.** ~100k emitters, ~64% on DGII's free facturador [V 5c, 6b], so roughly 36k emitters (my arithmetic, from 100k x 36%, mixing an Aug share with an Oct total) are the whole paid market, shared among ~190-200 providers [V 5b]. That is about 190 emitters per provider on average. No public per-document price exists [V 5d]; the "US$0.02-0.05" figure is a jury judgement, not data (judge A's check #12), and the signing cost per document is unknown (judge B). The candidate does not make price discovery and signing-cost quote the first-week deliverable; it should.
4. **The engine has never met the counterparty.** BACKLOG 2: response shapes, `ACECF.xml` file name, `anularrango` and `aprobacioncomercial` shapes, F1-F16 are all "UNCONFIRMED: verify on TesteCF"; goldens are written by the builders under test (judge A #3). 100% branch coverage measures the code against itself. The real risk is a systemic mismatch (signature, XML ordering, state machine) discovered at the first TesteCF run that invalidates weeks of "hygiene" work done before it. The candidate schedules the first real TesteCF run inside the 45-60 day block, behind other items. It should be the very first engineering act.

### 1.2 Other ways it breaks (each is a real defect in the candidate, not a nitpick)

- **"The owner's own RNC is the first customer" is false comfort.** It proves the engine can emit; it proves nothing about demand, price, onboarding or support. It also assumes the owner has an RNC, a legal entity, a purchasable certificate (RD$500-2,000 or US$30-70/yr [V 5d]) and runway. Nothing in the folder says what their monthly burn or months of runway are. Every numeric gate here needs that number; it is missing from all prior documents. [?]
- **The P1 trigger measures the wrong thing.** "Fallback to Frappe/Odoo if P1's first slice overruns 10 working days" times typing speed. The owner builds 50k lines in two weeks with agents; a slice will not overrun on typing. The real bottleneck is verification, UX decisions and ownership (R7: METR, DORA, GitClear). The trigger will almost never fire, so it protects nothing. Also "first slice" is undefined (one custom field end to end is a database column plus a form).
- **The named fallback is the weaker of the two.** Frappe-on-MariaDB means a second database engine, no shared Postgres/RLS/transaction, and unofficial Postgres support (ERPNext Postgres CI is opt-in and nightly, failing queries reported March 2026) [V 1a-1c]; ERPNext has no DR folder and no DR e-CF app [V 3c]; ERPNext app dependency is likely GPL-3 [V 3b, legal reading unsourced]. Odoo CE is Postgres-only, LGPL-3, and has no e-CF in `l10n_do` [V 2a-2c]. As a fallback the Odoo route is strictly better on the owner's own stack, and it is already the first connector in the candidate. The candidate names the wrong fallback.
- **P1's "thin metadata layer" is the wrong first build even if the gate opens.** All 7 researchers agree breadth needs a metadata layer, but that is a statement about a configurable CRM/ERP at scale. The first 3-10 paying tenants will need fixed screens (certificate, customers, invoices, receipts, 606/607 style reports) and will each ask for different things. A metadata layer built before then is a platform built for imagined users, which is exactly the chassis mistake the earlier jury flagged (~30% of non-test code before one user).
- **Agent-written everything is still agent-reviewed by the same agent.** Panel 5 / R7 note it; judge A confirms no automated mutation. The candidate fixes mutation but not the larger issue that the oracle for fiscal correctness must be DGII's, not the repo's.
- **Quality-bar identity trap.** "Lo mejor de lo mejor" plus 100% coverage plus zero suppressions is a bar the owner applies to everything. Applied to a portal, a connector or a sales page it will triple their cost. The candidate says "lighter gate on CRUD has to be built" but does not forbid building the polished thing first.
- **Motivation risk.** The owner's stated goal is a CRM with ERP features. A 12-month plan that is an engine-only wholesale niche, with the CRM as a conditional afterthought, can be abandoned by the owner for a P0-style build on day 40. The plan must keep a visible, honest path to the stated ambition, with its price.

## 2. Better plans nobody proposed (and what I did with each)

1. **OEM / subcontract through an existing PSFE (not "become a PSFE first").** There are ~190-200 authorized providers [V 5b]; many are resellers or thin products. The owner's scarce asset is a strict, typed engine with signer custody, receiver and WORM archive. Offer it as the technology under an already-authorized provider's licence, or ask whether a certified emitter can use it as its own software. This would bring revenue months before the owner's own authorization, if the regime allows it. Whether it does is [?]: ask counsel and DGII's PSFE desk in week 1. This is the highest-value unexplored option and costs one conversation to test. I include it in the plan as a parallel track; I do not claim it works.
2. **Odoo CE as the distribution channel, not the base.** Odoo CE is Postgres-only and LGPL-3, so it matches the owner's stack and allows a proprietary add-on [V 2a, 2b]; `l10n_do` has NCF only, no e-CF [V 2c]; third-party e-CF modules cost US$59-299 one time and some route through paid gateways [V 2e]. This is evidence of unmet demand and of a low price anchor for one-time modules. It is not evidence for building the owner's product inside Odoo: Odoo S.A. competes in SaaS, there is a yearly porting tax, and (my recollection, unverified [?]) full accounting reports and bank reconciliation are Odoo Enterprise features, so a DR accounting product on CE alone is limited. Therefore: Odoo CE connector first (as the candidate says), recurring SaaS engine behind it versus one-time modules, but not "Odoo CE as breadth base".
3. **Vertical-first instead of metadata-first: "accountant console".** Replace P1's generic metadata layer with one fixed-screen vertical for accounting firms that manage many client RNCs: multi-company, certificate custody, receive/emit, DGII state, purchase/sales reporting exports (the third-party IT-1 module sells at US$59 [V 2e], showing the pain exists), client portal. Accountants are a channel with 20-200 clients each, they were mentioned by every judge as a target, and they are the natural path to the SMEs outside the free-facturador tier. No metadata layer is needed to serve them. This is P1 limited to one vertical, with the metadata layer postponed behind a paying-tenant gate.
4. **A different sequence: customer contact and the first TesteCF run before any hygiene, and a revenue bridge with a cap.** Judge B's cash-first point survives (B's bridge was services income to the late-adopter tail). I keep it but cap it (section 3.2) so it cannot become a consultancy.
5. **Not chosen: Twenty/TS (P4), rewrite (P6), status quo (P0), Frappe default.** P4 and P6 change language and re-open DGII certification risk with no demand evidence; P0 repeats the chassis mistake; Frappe default is weakened by the verified Postgres and DR facts above. Python 3.15 lock-in (519 files, judge A) is a recorded risk, not a task: ship a container, do not codemod.

## 3. My final plan

### 3.0 Principles
1. Contact the counterparty first: DGII (TesteCF) and customers, before polishing anything.
2. Write the runway number on day 1. All gates below scale to it; I do not know it.
3. Build only what a named, paying or LOI-signed party asked for. Controlled hygiene is allowed only after the first TesteCF run.
4. One decision date per gate, in calendar days from today, not from external milestones.
5. Keep the stated ambition (CRM with ERP) as a priced future path: the vertical app is its first slice, not a detour.

### 3.1 Next 14 days (by 2026-11-08)

Engineering (one goal, nothing else):
- Days 1-5: obtain the .p12 and TesteCF access for the owner's RNC; run the first real TesteCF round trip following `docs/runbooks/do/testecf-spike.md`: e-CF 31 and 32 at least, plus one rejection. Log every divergence against F1-F16. Stop all other engineering until it runs. Fix only what the run shows.
- Days 1-14: collect DGII-official samples/XSDs into `countries/do/tests/contract/dgii-official/` as the oracle; goldens stay as regression only.
- Add `LICENSE` (proprietary, all rights reserved) the same day. Ten minutes.

Business (one goal: learn the price and the legal route):
- Day 1: write burn per month and months of runway into `docs/handoff/` (one line).
- Days 1-7: one counsel session, four questions: (a) PSFE application requirements, entity form, timeline; (b) INDOTEL Art. 22 custody (local vs remote); (c) can the engine run under an existing PSFE's authorization or a certified emitter's own software (OEM route); (d) can an emitter use third-party software with its own certificate after self-certifying. All [?]; answers decide the revenue date.
- Days 1-7: get a written signing/KMS cost per signature (AWS KMS per-request price is public; the operated-backend/INDOTEL-authorized-custodian quote is not). Compute margin at US$0.01, 0.02, 0.03 per document and write the number down.
- Days 3-14: hold at least 10 real conversations (not scheduled, held): 4 Odoo partners/integrators, 4 accounting firms with more than 20 client RNCs, 2 small ERP/POS vendors or small PSFEs. Questions: who signs and keeps the keys today, what do you pay per document or per month, what happens to your clients after 15 Nov, what would make you switch, would you pay a deposit. Record answers verbatim in a scratch file, not memory.
- Accept or explicitly defer ADR 0010 in writing by day 14 (date the deferral), following the counsel answer on custody.

Do not do in these 14 days: mutation automation, spec split, metadata layer, UI, K8s/Helm, new ERP modules, any chassis feature.

### 3.2 Days 15-90 (by 2027-01-23)

- Days 15-45: CerteCF 14 steps for the owner's RNC; fix findings; file the PSFE application by day 45 if counsel says the own-PSFE route is right, otherwise sign the OEM arrangement. Automate mutation testing on fiscal/numbering/state-machine/signer paths once (one task, 3-day cap, nightly job, not in the pre-commit gate), after the TesteCF run has settled the code.
- Days 15-60: Odoo CE connector as a separate LGPL repo, hard cap 1,500 lines and 15 working days, built on a generated Python client of the engine's OpenAPI; deliver to 2-3 design partners (partners or accountants who said yes). Portal-lite (certificate upload, customers, documents, PDF, QR, API keys) as plain, server-rendered screens, 5 screens max, relaxed gate on presentation code, strict gate stays on fiscal/money paths.
- Revenue bridge, capped: productized, fixed-price "Odoo + e-CF setup" for late-adopter tail emitters after 15 Nov (and implementation of existing third-party modules if needed), at most 1 day per week. Target US$1,000-1,500 collected by day 90, scaled to runway. If the cap is exceeded two weeks in a row, stop the bridge and review. Reason: services pay rent and also produce the demand signal, but uncapped they become a consultancy (the failure mode that kills the engine).
- Day 60 check (calendar, no external milestone): at least 15 held conversations, at least 3 LOIs naming volume and price, at least 1 design partner running documents on TesteCF through the connector. If fewer than 8 conversations, the problem is behaviour, not market: stop all building until 10 more are done.
- Spec split into an indexed set and mutation of docs are allowed after day 45 only if an agent misread the spec in practice. Otherwise skip.

### 3.3 The gate (day 90, numeric, evaluated on calendar date)

Open "vertical app v0" (accountant console on fixed screens, no metadata) only if all hold:
1. TesteCF round trip green for types 31, 32, 33, 34 on DGII-sourced fixtures (10 of 10 accepted, one rejection path, one contingency path exercised); 14 CerteCF steps done.
2. Authorization route decided and in motion: PSFE application filed OR OEM agreement signed (either is acceptable evidence; "not decided" fails).
3. At least 3 external parties using the connector or API in test, at least 2 asking unprompted for the same non-API feature.
4. At least 1 paying customer (any amount) and signed or collected revenue of at least US$1,000 including the bridge, or at least 3 written commitments of at least US$100/month starting at authorization. If the owner's runway is under 6 months, double both thresholds.
5. Contribution margin at the price the customers actually named is at least 60%.

Pivot rules:
- Fewer than 3 LOIs from 15+ held conversations: stop engine marketing to vendors; test accountants only, or move to Odoo-connector-plus-services mode. Do not build anything new for 30 days.
- Gate 1 fails by day 60 (systemic rejection): everything stops until it is fixed; do not sell.
- Authorization blocked or unclear beyond day 120 with no OEM route: connectors plus self-certifying-emitter service only, engine stays as an asset.
- Margin below 40%: reprice or drop engine custody to connector-only.

### 3.4 Months 4-12

- Months 4-6: vertical app v0 for 3-5 accounting firms (multi-company, document list and status, receive/accept, reporting exports, client portal). Second connector (ERPNext) only if two partners ask; do not build it speculatively [V 1c, 3c]. Authorization target by month 6 [?].
- Months 6-9: only if at least 10 paying tenants and at least 3 distinct tenants ask for tenant-specific fields/pipelines that fixed fields cannot serve: build the narrow metadata layer from the candidate's P1 (field registry, picklists, one pipeline, saved views), required spec/ADR amendments first (ADR 0004, ADR 0011, events-only rule) [judge A #13].
- Months 9-12: CRM objects (contacts, one pipeline, quotes that convert to invoices) built for the customers who already pay, which delivers the owner's stated ambition as an extension of invoicing rather than a parallel product. Accounting/inventory breadth: buy/partner (Odoo CE or hire) unless more than 25 paying tenants ask for it.
- Continue-full-time test at month 9: MRR at least 50% of monthly burn and at least 15 paying tenants; otherwise reduce scope or seek other income. Hiring: no engineer before roughly 25 tenants; first hire is customer-facing (support/sales) in the DR.
- Ops: managed container platform, not K8s; per-tenant export/restore and restore drill before the tenth customer (R6).

### 3.5 What NOT to do
- No rewrite, no language change, no Python downgrade codemod now.
- No new chassis or platform feature without a failing customer-facing test; no K8s/Helm.
- No Frappe/ERPNext as default; no Twenty; no Odoo-as-base for the owner's own product.
- No metadata layer before 10 paying tenants and 3 distinct custom-field requests.
- No hygiene work (mutation, spec split, ADR polish) before the first real TesteCF run.
- No chasing the 15 Nov 2026 cohort as an authorized provider.
- No quoting a per-document price to anyone until the cost number and at least 5 customer answers exist.
- No selling custody on an unaccepted ADR 0010.
- No building a polished UI to the 100%-coverage bar.

### 3.6 Folder / repo structure
- Stay in this repo; no re-layout (R8 verdict as judges A/B record it).
- Add now: `LICENSE`; `countries/do/tests/contract/dgii-official/`; `clients/python/` (generated from OpenAPI, needed by the connector).
- Separate repos: `ledger-odoo-connector` (LGPL-3 to sit in the Odoo ecosystem, thin, talks to the engine API only), later `ledger-erpnext-connector` only if pulled. Keeping them out of the monorepo keeps licence boundaries clean (GPL-3 ERPNext risk [V 3b]) and the engine's gate untouched.
- Portal-lite: `apps/portal/` (server-rendered) inside the monorepo, 5 screens. A TypeScript front-end (`apps/web/`, `packages/client-ts`, excluded from the uv workspace) only when screens exceed about 10.
- If the metadata gate opens: `packages/metadata/` as in R8 scenario A; manifests per first-party object; tenant overlays are rows.
- `docs/handoff/`: add a one-page `BUSINESS.md` (runway, conversations log summary, price and margin numbers, gate status) so every agent session reads the same facts.

### 3.7 What would change my mind
- Counsel or DGII says the engine cannot run under another PSFE or an emitter's own software and PSFE needs a long, expensive process (capital, audited entity): then P5 revenue starts after month 6; shift weight to connectors + services and treat the vertical as a month-6+ item.
- The first TesteCF run shows a systemic design mismatch (signing, numbering, state machine): re-plan the engine first; no selling.
- The 15 conversations show the real buyer is the SME with one invoice a day wanting a CRM/quotes, not vendors or accountants: then P1 (vertical, bounded) starts earlier, driven by named customers, not by the day-90 gate.
- A design partner wants accounting/inventory breadth and pays for it: reconsider Odoo CE as a base or partner, only after checking Odoo CE accounting limits [?].
- Margin at the customer-named price is under 40% for custody: drop to connector-only.
- A Frappe vendor/maintainer states official Postgres support, or ERPNext makes Postgres CI required [V 1b]: re-open Frappe as a breadth fallback.
- Runway under 4 months: switch to services-first immediately; the gate thresholds double.

## 4. Agreement and disagreement with the candidate

Agree:
- P5 first, certification is the critical path, the engine is the scarce asset [V 2c, 3c].
- No rewrite; Python/Postgres/modular monolith stay; no K8s.
- LICENSE, ADR 0010 closed, DGII-sourced fixtures, automated mutation on money paths (but after the first TesteCF run).
- Odoo connector first as a sales channel; portal-lite; spec split as optional.
- A numeric day-90 gate with a default direction rather than "no decision".

Disagree:
1. Sequence: candidate puts hygiene in days 0-45 and customer contact in 30-90; I put the TesteCF run and 10 held conversations in days 1-14 and hygiene after.
2. Gate: candidate gates on signed MRR that cannot exist before authorization; I gate on route-in-motion (filed or OEM), margin, held conversations, LOIs and one payment.
3. The P1 trigger (10 working days) measures typing; I use paying-tenant counts and custom-field demand.
4. P1 is generalised into a metadata layer too early; I replace it with a fixed-screen accountant vertical and move the metadata layer to months 6-9 behind 10 paying tenants.
5. Named fallback: Odoo connector (Postgres-only, LGPL, owned stack), not Frappe-on-MariaDB.
6. Add: OEM-through-existing-PSFE test, runway number, signing-cost quote, capped bridge income.
7. "The owner's own RNC is the first customer" is not a customer; treat it as a test fixture.

## 5. Confidence: 58%

Reasoning: I am highly confident (about 85%) in the things to not do (no rewrite, no Frappe default, no metadata layer before demand, no hygiene before the TesteCF run) and in the shape of the first 14 days, because they cost little and cap downside. I am much less confident (about 40%) in the revenue path and its dates, because four inputs that drive it are unverified: PSFE requirements and timeline, whether OEM or self-certified use is allowed, the real per-document price and the signing cost, and the owner's runway and entity status. The 190-provider, 64%-free market also leaves real doubt that a one-person engine wholesale business is large enough; the gate and pivot rules exist to find that out in 90 days at low cost. The blend is about 58%.
