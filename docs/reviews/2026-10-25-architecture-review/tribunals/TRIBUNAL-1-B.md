# TRIBUNAL-1-B: business and market judge (survival of a solo founder, no revenue, deadline closing)

## 1. Verdict: option (D), a cash-first variant of (B). One plan.

"P5, then P2 gated, with a revenue bridge in parallel and a hard stop on engine work that does not touch the real DGII."

The question is not which architecture is best. It is which sequence gets a first paid invoice before the founder runs out of money or patience. On that test the panels are right about P5 first, but P5 as they describe it is not yet a business, for four reasons:

- **Nothing has touched the real DGII.** No TesteCF run has happened. The backlog lists the first run as owner-side work, after all the CERT tasks. 469 commits and well over 100k lines have never met the counterparty. The biggest risk is not Python versus Frappe. It is that the XML, signature or state machine is wrong in ways only DGII shows.
- **The engine cannot be sold as an authorized provider yet.** The sequence is own RNC certified, then CerteCF's 14 steps, then PSFE application (about 10 business days for an answer), then tests, a sworn statement and INDOTEL custody. ADR 0010 is still "Proposed" because the owner has not chosen the custody backend. Months of calendar time sit outside the founder's control.
- **The 15 Nov 2026 window closes in 21 days and cannot be met as a provider.** The repo's own handoff says so. The demand tail after it is real but shrinking.
- **The unit economics are unproven.** Wholesale is about US$0.015-0.03 per document and the remote-signing fee per signature is "unknown: get a quote" (jury §6, §7). If the signing fee is a large fraction of the per-document price, P5 is a commodity at negative margin.

So the plan is P5, but run as a market test with an income bridge. Breadth (P1 or P2) is not started until numbers say so. The default is P2 (Frappe), because for a person with no revenue buying breadth is faster to a paid customer than building it. P1 is the fallback, and only in a narrow form.

### Why default P2 and not P1, even though P1 scored higher (6.20 vs 5.62)

- P1 is a framework project. A metadata registry, saved views, a rule engine, a report runner, print templates and a generated UI are the first 6-12 months of any CRM company. The panel's own gate says a "narrow" P1 prototype that takes more than about 6 weeks is a failure signal. That is an admission of the risk.
- P2 puts the unproven part (customers wanting a CRM/ERP from this founder) on someone else's mature code. Wrong, it costs a connector of about 1.5k lines. P1 wrong costs a quarter.
- The staff-engineer and type-checker arguments for P1 are about code quality. A buyer pays for a working Spanish-language invoice, quote and pipeline. A founder with zero revenue should not spend their scarce attention on whether basedpyright "all" passes on CRUD.

### The 90-day plan (day 0 = 2026-10-26, so day 90 = 2027-01-24)

**Days 0-7: owner-side, no code**
1. File the emitter application for the owner's own RNC and get TesteCF access. Buy the certificate. Quote remote signing per signature. These are the jury's own week-one items and they are still undone.
2. Book counsel for one session on three questions: INDOTEL Art. 22 (local versus remote custody), whether the self-certifying-emitter route allows earlier revenue, and a separate legal entity for the PSFE.
3. Get a quote or price list for remote signing. Compute the margin at US$0.02 and US$0.03 per document. Write the number in the backlog.
4. Name 10 prospects (vendors, Odoo integrators, accountants) and talk to 5. The target is 3 signed letters of intent (LOI) by day 60.
5. Add a LICENSE file (proprietary, all rights reserved, or an explicit decision). None exists today.

**Days 7-35: one narrow code goal, "first real DGII round trip"**
- Drop CERT-tagged tasks that TesteCF does not need first (Task 24 printed PDF/QR, Task 25, Task 27 cleanup, fake-DGII polish in Task 26). Do the shortest path to emit, sign, submit and read a result on TesteCF for the owner's own RNC (Tasks 20-22 core only).
- Replace self-written goldens with DGII-sourced samples and the TesteCF response as the oracle for totals and semantics.
- Freeze: chassis, devtools, spec edits, mutation work, K8s/Helm, any ERP module, any UI beyond a certificate upload and invoice list page.

**Days 35-60: CerteCF and the sales test**
- Run the 14 CerteCF steps for the owner's own RNC. File the PSFE application as soon as the RNC is certified.
- Parallel: build an Odoo CE connector and an ERPNext connector as a paid or pilot deliverable. This is the 5-day experiment, done for revenue. Score each against: share of a DR SME month covered, lines of connector code, and who owns the tenant.
- Bridge income: sell implementation and integration as a service to the late-adopter tail (Odoo or ERPNext plus e-CF by self-certifying taxpayers, if counsel allows). Target the first US$1-2k invoice by day 60. This is cash and also the demand signal.

**Days 60-90: decide**

### Gate criteria (numbers, evaluated on day 90 or earlier if all hit)

| Signal | Pass | Fail |
|---|---|---|
| TesteCF round trip for the owner's RNC | 10 of 10 documents of types 31, 32, 33, 34 accepted, with at least one rejection path and one contingency path exercised | Not done by day 45, or a systemic rejection not fixed by day 60 |
| PSFE application | Filed by day 60 | Not filed by day 75 |
| Signing margin | Contribution margin above 60% at US$0.02 per document | Below 40%: reprice or drop to connectors only |
| Demand | 3 or more LOIs (named company, named volume) by day 60 and at least 1 paying pilot or invoice by day 90 | 0 paying and fewer than 3 LOIs |
| Bridge income | At least US$1.5k collected by day 90 | Under US$500 |
| Breadth experiment | A connector under 1.5k lines covering 80% or more of a sample DR SME month, run on Frappe, built in under 15 working days | Over 25 working days |

**Decision on day 90:**
- All demand and margin gates pass and the Frappe connector passes: go P2. Create a separate repo `<brand>_do` for the Frappe app. Do not fork ERPNext.
- Demand passes but Frappe fails (or Odoo wins on partner distribution): P3 only if at least 2 of the LOIs are Odoo integrators. Otherwise narrow P1, scoped to custom fields, pipelines and 3 entities, with a hard 6-week cap.
- Demand fails (fewer than 3 LOIs and no paid invoice): stop building. Spend the next 30 days on services income and customer discovery. Re-evaluate the engine only if TesteCF passed.
- Certification blocked more than 90 days: pivot to selling the integration (connectors plus the self-certifying emitter route) and keep the engine as the asset.

## 2. Where the panels were wrong or biased

1. **They scored architecture, not runway.** Roles in Panel 02 (data), Panel 05 (productivity) and the type-system roles score P1 high for type safety, RLS elegance and agent steering. None of that pays rent. Panel 05's staff-engineer role gives P1 8/10 on the strength of "the gate is the harness". The gate is not the product, and the gate has holes (below).
2. **Shared context and shared inputs.** All 100 roles read the same R1-R8 and CONTEXT.md, which lean toward "the code is right, only the layer above is missing". The result is a convergence on "P5 first" that is partly one framing repeated. The independent facts that raised doubt (no TesteCF run, no LICENSE, self-written goldens, ADR 0010 open) came from the few panels that opened the repo, and they were not weighted into the scores.
3. **P5 is scored as if it were a market.** Panel 02 gave P5 9/10 from the FinOps role on low burn. Low burn is not revenue. Panel 01's own objections (190 providers, Alanube behind Alegra, US$0.02-0.05 per document, regulator-gated revenue) are the main thing, yet the scores stay high. The mean 7.14 hides that P5 has no paying customer, no authorization and an unverified margin.
4. **The gate was mostly "after certification".** Many kill criteria start the clock at TesteCF or PSFE authorization. That can be months away, and the founder pays rent in the meantime. A gate must run from today, on calendar time, not from a milestone outside the founder's control.
5. **P1 versus P2 was treated as a technical tie.** It is not. P1 consumes founder months before any evidence of demand. P2 does not.

## What the panels missed

- **Quality bar versus the facts.** AGENTS.md and the panels' P0 text say "mutation-checked critical paths". Mutation testing is not in `poe check` (grep of `pyproject.toml`: 0 matches). The 100% branch coverage is real, but the e-CF goldens are written by the builders under test (`countries/do/tests/contract/test_golden.py`, line 151). Coverage plus self-written goldens do not prove DGII acceptance.
- **No LICENSE file and no legal entity.** There is no `LICENSE` at the repo root. A buyer, an integrator or an investor cannot lawfully use code with no stated terms. The PSFE-in-its-own-entity advice is not done.
- **Agent spend and throughput as a business cost.** The decision ledger records a usage limit hit. The two-week build rate (469 commits) is a burn the plan never prices.
- **The 3.15-only lazy-import lock-in.** 519 files use 3.15-only syntax and `requires-python >= 3.15`. A vendor who wants to self-host, or any hire, must run a two-week-old interpreter. This matters to selling a library or SDK; it can be handled by shipping a container.
- **Remote-signing unit cost** is the single number that decides whether the wholesale price works, and nobody has it.

## 3. Strongest case against my recommendation

"Choosing P2 as the default is a bet on a framework the founder has never run, in a stack with untyped documents, GPL ERPNext, database-per-site tenancy and a second ops stack. The staff engineers and the type-checker roles say agents cannot reach the repo's quality bar inside Frappe. The Frappe Cloud economics for 1,000 SME sites are unproven. P1 reuses the repo's tenancy, ledger and gate, and a registry done well is a smaller moat than a copied CRM. A founder who adopts Frappe becomes an ERPNext reseller with an add-on and loses the 'lo mejor de lo mejor' ambition."

**Why it still loses:** the objection is about quality after traction, and my plan does not choose the breadth path until day 90 and until three conditions are met (LOIs, a margin, a connector that passes in about 3 weeks). The decision is cheap to reverse because the connector is about 1.5k lines and the engine stays unchanged. P1 is not rejected; it is the fallback with a 6-week cap. And "best of the best" requires customers first. A perfect metadata layer for zero users is the way solo founders die.

## 4. Folder structure decision (using R8)

- **Now (days 0-90): keep this repo as is, trimmed in intent not in files.** R8 §5 says the layout is above the median of platforms surveyed. No re-layout, no new top-level directories until revenue. Only two additions are allowed: `clients/python/` (generated SDK from the OpenAPI, needed by the connectors) and `docs/spec/` split of the 2,187-line spec into an indexed map (agents benefit; do it in one afternoon when touching the spec for a real reason, not as a project).
- **Connectors in separate repositories** (R8 §4b): `<brand>-odoo/` (OCA-style, one branch per Odoo major) and `apps/<brand>_do` as a Frappe app (the India Compliance pattern). They call the engine through the generated client. Reasons from R8: different release clocks, different toolchains (the 100% branch strict gate does not fit Odoo or Frappe test runners), and the engine seam must be an external contract anyway since vendors will use it.
- **If the gate goes P1 instead:** follow R8 §4a: `packages/metadata`, `modules/<m>/metadata/`, entity slices for breadth modules, `apps/web` with uv `exclude`. Not before.
- **Add** `poe check-fast <dir>` (the gate is one long sequence; F2 of Panel 05) when touching the loop next, because the founder's iteration speed is part of runway.

## 5. Verified claims

| # | Claim | Evidence | Verdict |
|---|---|---|---|
| 1 | Mutation testing is not part of the gate | `C:\Users\brian\Desktop\ledger\pyproject.toml` (grep for mutation/mutmut: 0 matches; `check` sequence lists format, lint, suppressions, types, arch, deps, spell, proto, devtools, dead, test) | True |
| 2 | Golden e-CF XML is written by the code under test | `countries/do/tests/contract/test_golden.py:151` "each golden written by its builder" | True |
| 3 | 519 files use 3.15-only lazy imports | Search for `lazy import/from` lines in `*.py` returned 519 files (repo-wide count including any non-tracked copies; the count matches the panel's) | True (matches) |
| 4 | No LICENSE file | `ls LICENSE*` in repo root: no such file | True |
| 5 | ADR 0010 (signer) is still Proposed with open production choices (KMS, backend we operate, INDOTEL custody model) | `docs/adr/0010-signer-isolation.md`: "Status: Proposed" and "the owner has not accepted the production choices" | True |
| 6 | No real TesteCF run has happened; it is owner-side future work | `docs/handoff/BACKLOG.md` section 2: "First real TesteCF run with the owner's own RNC and certificate"; `docs/handoff/PROGRAM.md` line 20 | True |
| 7 | Authorization path: own RNC certified, then PSFE application (about 10 business days), tests, sworn statement, INDOTEL custody | `docs/handoff/BACKLOG.md` section 2; jury synthesis §6 | True (repo's own statement; DGII primary source not fetched) |
| 8 | 15 Nov 2026 deadline for small, micro and unclassified taxpayers; cannot be met as an authorized provider | HANDOFF.md line 26; jury synthesis; web search shows Aviso 06-26 six-month extension to 15 Nov 2026 (blogs: https://blog.alegra.com/republica-dominicana/prorroga-ecf-pymes/, https://siemprealdia.co/republica-dominicana/impuestos/facturacion-electronica/) | Deadline confirmed by secondary sources; "cannot be met" is the repo's judgment; DGII primary notice not verified |
| 9 | 190 authorized providers, over 80,000 emitters, price US$0.02-0.05 per document | `docs/reviews/2026-10-20-stack-jury-synthesis.md` lines 101-104 | Present in the jury file; no primary source checked by me, treat as unverified |
| 10 | Remote-signing fee per signature is unknown | jury synthesis §7: "remote-signing fee per signature (unknown: get a quote)" | True (an open gap) |
| 11 | The spec wins over code, so P1 needs spec and ADR amendments first | `AGENTS.md` "When code and spec disagree, the spec wins" | True |
| 12 | Repo size and age | `git log`: 469 commits since 2026-10-11 | True; my line counts were polluted by non-source files and are not used |
