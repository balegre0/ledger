# TRIBUNAL-2, judge E (operator lens: can one person plus AI agents actually execute this starting tomorrow?)

Inputs read: brief, CONTEXT, SYNTHESIS, PROPOSALS, VERIFY, TRIBUNAL-1-A/B/C (full or head). I did not re-open the repo; repo facts below are the ones the three first-tribunal judges verified (no LICENSE, ADR 0010 Proposed, no real TesteCF run, self-written goldens, 519 files on Python 3.15-only `lazy`, mutation not in the gate). Panels were not re-read in full.

## 1. Breaking the candidate ("P5 then P1")

### The single most likely way the owner fails
They spend days 0-60 on the only part they enjoy and can control (code: DGII fixtures, mutation automation, spec split, ADR text, LICENSE, portal-lite), while the items that actually gate everything are owner-only, external and uncomfortable: buying the certificate, enrolling the RNC in TesteCF, DGII's waiting time, a notarized sworn statement, an INDOTEL custody decision, a legal entity, and 15 sales conversations. AI agents multiply code throughput; they do not multiply calendar time at DGII or the founder's willingness to cold-call. The candidate plan lists "15 conversations" as one bullet inside a certification-heavy 45-60 days. In practice it slides to day 50, the pilots are friends, the day-90 gate shows 0 external demand, and the plan then "opens P1" anyway because the sunk-cost pull (52k lines, 469 commits) is stronger than a gate with no data behind it. Failure mode = engineering drift disguised as certification, with the gate evaluated by the person who wants it to pass.

### Specific executability problems
1. Items a solo founder cannot do alone or cannot do well: (a) operate an INDOTEL-compliant custody/HSM service with real SLA and incident duty; (b) be a regulated PSFE (sworn statement, DGII audits, tests) while also being sales, support and product; (c) legal advice on Art. 22, entity structure, GPL/OPL questions (needs a lawyer, paid, one session); (d) trust-building with accountants and Odoo partners, which is slow, in-person and Spanish. Tribunal 1 flagged none of (a) and (b) as a solo-capacity risk; it treated them as to-do items.
2. "Day-90 numeric gate opens P1 only with signed MRR" has no enforcement. Nobody but the owner evaluates it. Needs a pre-commit (written, dated, shared with one outside person) or it is decoration.
3. "P1 first slice overruns 10 working days, fall back to Frappe/Odoo" is a switch with a hidden price: Frappe is MariaDB-first (VERIFY 1a-1d), so the fallback to Frappe is the weakest fallback. The honest fallback is Odoo CE, not Frappe.
4. A thin metadata layer plus generated TS/React UI, report runner and rule engine is a second product with a second language (TypeScript) and a UI toolchain. It is the single largest "cannot be reviewed by one person" item even with agents (verification is the bottleneck, R7). The candidate treats it as a gated expense; I treat it as something to avoid until the market forces it.
5. The calendar is ignored. 15 Nov is 21 days out; the candidate correctly says do not chase it, but no one says what to do with the emitters that will be penalised on 16 Nov. That tail is the first visible demand and it expires fastest.

### Is there a better plan nobody proposed?
Yes, a variant: **make Odoo CE the breadth base now and the engine the paid DR e-CF backend, with no P1 at all until Odoo demonstrably cannot serve.** Reasons, all from VERIFY:
- Odoo CE is Postgres-only (2a), same database engine as the engine; LGPL-3 (2b) so a proprietary connector is legal; Odoo has `l10n_do` with chart of accounts, ITBIS, NCF (2c) but no e-CF; third-party e-CF modules sell at USD 59-299 (2e) and none is shown as DGII-certified. That is a real gap with a real price anchor and a distribution channel (Odoo partners and apps store).
- It buys CRM, quotes, invoicing, inventory, purchasing, accounting, reports and a UI on day 1 with zero founder build time, which is the exact thing P1 would take 6-12 months to approximate.
- Cost: yearly port tax, Odoo S.A. competing, OPL/Enterprise limits, and dependence on someone else's roadmap. I accept these because they only matter after revenue.
Nobody proposed it as a standalone sequence (T1-A treats Odoo as a demo connector, T1-B as an alternative to Frappe). I propose it as the primary breadth route, with P1 demoted to "last resort, one vertical, only if Odoo partners tell me the same missing feature three times".

A second unproposed idea, labelled as unverified: while own certification and PSFE are pending, route a pilot customer through an already authorized provider's API for transmission and use the engine only for generation and validation. That brings revenue forward, but I have not verified that any provider would allow it or that it fits DGII rules, and Alanube/Alegra alliance is only partly sourced. Treat as a question for counsel, not a plan step.

## 2. My own final plan: "Certify for yourself, sell to Odoo partners, build nothing else"

Call it P5-Odoo. Rule: every week must contain one owner-only external action; engineering fills the gaps, not the reverse.

### Next 14 days (starts tomorrow, 26 Oct)
Owner-only (these come first, by calendar, and block everything else; do them Mon-Wed):
1. Buy the digital certificate (RD$500-2,000 per VERIFY 5d, or the DGII free one-year certificate if eligible), request TesteCF access for the owner's own RNC. Put the DGII's expected answer date in the calendar.
2. Book ONE paid lawyer session (2 hours) with a fixed question list: INDOTEL Art. 22 custody (is remote custody via an authorized entity enough for a PSFE?), whether a software vendor needs its own PSFE status or can be certified per client, entity structure, LICENSE choice, how to ship a proprietary connector against Odoo CE (LGPL/OPL). Write the answers into ADR 0010.
3. Get a written quote for remote signing/custody per signature and per month from one authorized custodian. Compute margin at USD 0.02 and 0.03 (none of the price figures are primary-sourced; they are inputs to check, not facts).
4. Make a list of 20 targets: Odoo partners in DR, accounting firms, small POS/ERP vendors. Contact all 20 by day 5, spoken to 8 by day 14. Script: "I am certifying an e-CF backend; would you pilot it in an Odoo/your product, what would you pay?" Record answers in a plain table in the repo (`docs/market/calls.md`). Include the late emitters hit by 15 Nov (they are the tail).
5. Add `LICENSE` (proprietary, all rights reserved) in a single commit. 15 minutes.

Code (agents, in the gaps while waiting):
6. Runbook run: first real TesteCF emission, sign, submit, read result (`docs/runbooks/do/testecf-spike.md`). Success = one accepted type-31 document. That is the only engineering goal of the first 14 days. No other feature.
7. Put DGII official XSDs and samples in `countries/do/tests/contract/dgii-official/`; goldens stay as regression only.
Freeze list for 14 days: chassis, devtools, spec splitting, K8s/Helm, metadata, UI, new modules, mutation work.

### Days 15-90
- Weeks 3-6: CerteCF 14 steps for own RNC; ADR 0010 accepted with the lawyer's answer; PSFE application filed by day 45 at the latest (if the lawyer says it is the right route; otherwise follow the per-client certification route).
- Weeks 3-8: build the Odoo CE connector (separate repo `ledger-odoo`, one branch per Odoo major, starting with the current LTS the partners actually run). Scope: send invoice to engine, receive state, show QR/PDF, receiver inbox, credit notes. Hard cap: 15 working days. Use `clients/python` generated from OpenAPI. Do not touch Odoo core.
- Weeks 4-12: 3 pilot partners in Odoo with the connector against TesteCF; price them as a monthly fee plus per-document, not a one-off module (competitors sell one-off USD 59-299; a recurring model is the differentiator and what the founder needs to survive).
- Optional, bounded: one afternoon per month of "portal-lite" only if two pilots ask unprompted. Else none: Odoo is the UI.
- Mutation automation on fiscal/money paths and the spec split happen only when a task touches those files (opportunistic, never scheduled as projects).
- Bridge income: sell integration work to the 15 Nov tail (Odoo + e-CF setup), target USD 1.5k collected by day 60. Cash buys the runway to wait for DGII.

### Day-90 gate (evaluated 24 Jan 2027, pre-committed in writing and sent to one outside person today)
Continue on the Odoo-connector path (no P1) if at least:
- TesteCF: 10/10 of types 31, 32, 33, 34 accepted for own RNC, one rejection path and one contingency path exercised.
- 8 completed customer conversations by day 14 and 20 by day 60; at least 3 pilots using the connector against TesteCF.
- At least USD 1,000 collected (services or pilot fees) by day 90 and at least 2 written commitments of USD 100+/month.
- Contribution margin above 50% per document at the quoted custody cost.
- PSFE route chosen with counsel and either filed or consciously replaced by per-client certification.
Open P1 (one vertical, one quarter, fixed scope) only if, additionally: three different partners/clients ask for the same missing capability that Odoo cannot provide, AND signed MRR is at least USD 2,000, AND the capability is an object type or workflow, not a report or a field.
Kill / pivot: zero paying pilots and fewer than 3 LOIs at day 90 means stop building, spend 30 days on services income and discovery; certification blocked past day 150 means sell the connector with a third-party PSFE as the transmission layer (needs counsel).

### 12 months
- Q1 (to Mar 2027): certification complete; 5-10 paying Odoo partner clients; 100-300 recurring paid emitters; rate and custody costs known. Hire none.
- Q2: second connector only if demand is evidenced (ERPNext/Frappe on MariaDB only if a partner pays for it; no Postgres bet). Public API docs, status page, per-tenant export/restore drills (the real B2B failure points in SYNTHESIS).
- Q3-Q4: if gate for P1 opened: one-vertical metadata slice (e.g. retail CRM+invoicing for a single niche) as in R8 Scenario A. If not: second country is a Spanish-speaking one with an Odoo localization hole, or stay and deepen DR. Consider the first part-time hire (a DR accountant or support person), not an engineer.
- Revenue target by month 12 for the plan to count as working: USD 5k MRR. Below USD 2k MRR at month 9 means this is a hobby; reconsider employment plus the engine as a side asset.

### What NOT to do
- No new platform/chassis features. No P2/Frappe default (MariaDB, GPL on ERPNext). No rewrite or Python pin-back. No UI or metadata engine before the P1 gate. No K8s/Helm; one VM or one managed container service plus managed Postgres. No chasing the 15 Nov cohort as provider. No hiring. No 100%-coverage requirement on connector code (it lives in another repo with its own test runner). No schedule item named "refactor spec".

### Folder / repo structure
- This repo stays as-is (ledger engine). Add `LICENSE`, `clients/python/` (generated), `countries/do/tests/contract/dgii-official/`, `docs/market/` (calls, pricing). No re-layout (R8: above median).
- `ledger-odoo/` separate repo (OCA-style, branch per Odoo major, depends only on the generated client). If a second ecosystem comes later: `ledger-frappe/` the same way. Rationale: different test runner and release clock, and the engine seam is an external contract anyway.
- If P1 opens: `packages/metadata`, `apps/web` (TS, outside the uv workspace), `modules/<m>/metadata/` per R8 Scenario A; not before.

### What would change my mind
- A lawyer or DGII says a software vendor must be a PSFE itself and custody must be operated in-house with a 24/7 duty: then the wholesale business is not solo-feasible; pivot to the connector with a third-party PSFE underneath.
- Three or more partners refuse Odoo because their customers are not on Odoo (they use Alegra/Siigo/POS): then P5 vs POS vendors becomes the main channel, and an own lightweight portal (P1-lite) moves up.
- Odoo S.A. ships an official certified DR e-CF localization: connector price anchor collapses, I move to P1 one vertical or to a different country.
- Margin under USD 0.01 per document or custody quote above ~40% of price: stop selling per document, sell implementation and subscriptions.
- Evidence that Frappe Postgres becomes officially supported with required CI: reopen P2.

## 3. Agreement and disagreement with the candidate

Agree: certify own RNC first; LICENSE and ADR 0010 must close; no rewrite; engine stays an isolated service; day-90 numeric gate; P1 not before evidence; connectors as the sales channel; Frappe weakened as default by the new facts.
Disagree:
1. Order: sales conversations start day 1, not after certification. Waiting for DGII is idle calendar time, the best moment to talk to buyers.
2. Odoo CE is the primary breadth route (Postgres-only, LGPL, DR accounting present, e-CF gap, USD 59-299 anchor), not just "connector first". The candidate's named fallback is Frappe; I replace it with Odoo.
3. P1 is demoted: not a general thin metadata layer behind a money gate, but one vertical, only after a stricter condition (3 repeated asks plus USD 2k MRR).
4. Drop scheduled engineering projects (mutation automation, spec split, portal-lite) in favour of "when touching those files". They look productive and do not move the gate.
5. Add what the candidate omits: legal entity, lawyer session, custody quote, solo-capacity check on being a regulated provider, bridge income, pre-committed gate with an outside witness.

## 4. Confidence: 62%

Reason: I am fairly sure about sequence (certification and talking to buyers in parallel, Postgres-compatible Odoo over MariaDB Frappe, P1 gated) because it uses only verified facts and is cheap to reverse. Confidence is capped by four unknowns I could not check: whether a solo founder can lawfully be a PSFE with outsourced custody (lawyer needed), the real remote-signing cost and the per-document price (no primary price found), Odoo partner willingness to pay recurring fees in a market where USD 299 one-off modules exist and the free DGII facturador serves ~64% of emitters, and the fact that every judge so far (including me) reads the same shared context. If the lawyer answers favorably and 3 of the first 8 conversations ask for a price, I would raise this to about 75%.
