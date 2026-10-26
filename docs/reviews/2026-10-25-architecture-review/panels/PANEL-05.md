# PANEL-05: engineering productivity and AI

Ten roles, judged on R1-R8, CONTEXT.md, PROPOSALS.md and a read-only look at the repo (`main`, e95b082).

Repo facts this panel checked itself (they are not in R1-R8):
- **F1. Mutation testing is not part of the gate.** `pyproject.toml` lines 99-109: `poe check` runs format, lint,
  suppressions, both type checkers, arch, deps, spell, proto-lint, proto-breaking, devtools, dead and test. It has no
  mutation task, and the v2 spec never mentions mutation testing. The "mutation checks everywhere" in P0 is an
  aspiration, not current practice.
- **F2. The gate is one slow sequence.** `check` runs mypy one package at a time, then every package's suites,
  including Postgres and LocalStack integration tests (`pyproject.toml` lines 115-173). There is no fast gate for
  one package or one changed file (R7 §2.2 "fast inner loop").
- **F3. The e-CF goldens are written by the code under test.** `countries/do/tests/contract/test_golden.py` line 151
  says "each golden written by its builder". The only outside oracle is DGII's published XSD. Totals and semantics
  are checked against oracles the agents wrote themselves (R7 §2.2 last bullet).
- **F4. Pinning back to Python N-1 would not be cheap.** 519 `.py` files use 3.15-only `lazy` imports, and
  `requires-python = ">=3.15"`. R5 §3 calls pinning to 3.14 a cheap fix; in this repo it would be a codemod across
  519 files.
- **F5. The spec mandates the boundaries P1 loosens.** AGENTS.md says "When code and spec disagree, the spec wins".
  P1 therefore first needs spec and ADR amendments (ADR 0004, ADR 0011, the events-only rules), not just code.

Today is 2026-10-25. The last DR e-CF cohort deadline (15 Nov 2026, R6 [38]) is **21 days away**.

## 1. Scores

| Role | P0 | P1 | P2 | P3 | P4 | P5 | P6 | Favorite |
|---|---|---|---|---|---|---|---|---|
| 1. AI coding-agent researcher | 2 | 5 | 7 | 6 | 3 | 8 | 1 | P5→P2 |
| 2. Staff engineer running a team of agents | 3 | 8 | 5 | 4 | 4 | 7 | 1 | P1 |
| 3. Test architect | 3 | 7 | 5 | 4 | 4 | 9 | 2 | P5→P1 |
| 4. DevEx / inner-loop lead | 2 | 8 | 5 | 4 | 5 | 7 | 2 | P1 |
| 5. Senior code reviewer, regulated fintech | 4 | 6 | 4 | 3 | 3 | 9 | 1 | P5 |
| 6. Docs-as-code / technical writer | 3 | 7 | 6 | 5 | 4 | 8 | 2 | P5→P1 |
| 7. OSS maintainer of a large Python framework | 1 | 4 | 8 | 6 | 4 | 7 | 1 | P2 |
| 8. LLM platform engineer (MCP, agent APIs) | 3 | 7 | 6 | 4 | 6 | 9 | 1 | P5→P1 |
| 9. Static analysis / type-system expert | 5 | 8 | 3 | 3 | 6 | 7 | 3 | P1 |
| 10. Engineering manager hiring the first engineers | 2 | 5 | 6 | 7 | 5 | 7 | 4 | P5→P3 |
| **Mean** | **2.8** | **6.5** | **5.5** | **4.6** | **4.4** | **7.8** | **1.8** | |

## 2. Per-role judgments

**1. AI coding-agent researcher.**
- **Top: P5 (8).** Agents stay on a deep, narrow domain with a pass/fail oracle.
- **Bottom: P6 (1).** It drops Python, the best-benchmarked language (63% vs 31-58%, R5 [33]), and forces
  re-certification.
- **Why.** ERP breadth is where agents are weakest: under 25% on SWE-Bench Pro's ~107-line multi-file fixes
  (R7 [7]), and METR measured a slowdown on high-standard repos (R7 [1]). P0/P1 maximise those tasks; a framework
  outsources them.
- **Mind-changer:** agents finish ≥60% of cross-module metadata tasks first-pass under this repo's gate.
- **Folder:** R8 §4b: a trimmed `ledger-engine` with generated `clients/python`, plus a separate Frappe app repo.

**2. Staff engineer who runs a team of coding agents daily.**
- **Top: P1 (8).** The gate is the harness: strict types and import contracts steer agents (R5 [38]; R7 §2.2).
  Frappe and Odoo code is untyped, in-process and without RLS; agent code there "cannot reach the repo's quality
  bar" (R4 §3).
- **Bottom: P6 (1).** It discards the harness.
- **Condition.** Single writers plus a clean-context reviewer (R7 [12]), not the parallel writers used so far.
- **Mind-changer:** the first P1 slice (one custom field end to end, R4 §5) takes more than 10 working days.
- **Folder:** R8 §4a: `packages/metadata`, `modules/<m>/metadata/`, entity slices in breadth modules.

**3. Test architect (TDD, property-based and mutation testing).**
- **Top: P5 (9).** The system shrinks to a surface with outside oracles: XSD, TesteCF, DGII samples.
- **Bottom: P6 (2).** 72k test lines would need re-proving (R5 §3).
- **Why.** Coverage proves execution, not correctness (R7 §2.2). Here the goldens are self-written (F3) and mutation
  testing is not in the gate (F1). P1's tiered gate (strict on money, lighter on CRUD) is right.
- **Mind-changer:** TesteCF passes end to end with DGII-sourced fixtures. Then breadth testing (P1) is the
  bottleneck.
- **Folder:** tests mirror `src` (R8 §3.3.5), with `contract/dgii-official/` apart from `golden/`.

**4. DevEx / inner-loop lead.**
- **Top: P1 (8).** It reuses the manifest → codegen pipeline (ADR 0009). Only entity descriptors are missing (R4 §4).
- **Bottom: P0 and P6 (2).** P0 means 6 directories and 8+ files per entity (R8 §3.3.2); P6 resets the toolchain.
- **Must-fix for every option.** The gate is one long sequence (F2). Agents batch work when feedback is slow, and
  OpenAI capped builds at about a minute (R7 [9]).
- **Mind-changer:** Frappe delivers a field, form, list and report in under a day while P1 needs more than a week.
- **Folder:** `apps/web` with uv `exclude` and `kind = "web"` (R8 §3.3.3), plus `poe check-fast <dir>`.

**5. Senior code reviewer for regulated fintech code.**
- **Top: P5 (9).** The audit surface is smallest: custody, numbering, the DGII state machine and the archive, all
  already isolated (ADR 0010; R2 §4).
- **Bottom: P6 (1).** It discards audited code.
- **Others.** P0's 127k lines in two weeks exceed review capacity, and DORA links AI throughput to instability
  (R7 [5][6]). P2/P3 put the GL in in-process plug-ins without RLS; Odoo's own SaaS bans custom Python (R4 [7]).
- **Mind-changer:** DR integrators refuse third-party custody of their signing keys.
- **Folder:** `ledger-engine` with dated `contracts/openapi/` (R8 §4b); the signer stays its own app.

**6. Docs-as-code / technical writer.**
- **Top: P5 (8).** For an API product the docs are the product: dated versions, RFC 9457 errors, Standard Webhooks
  (R4 §4).
- **Bottom: P6 (2).** It resets the 11-ADR trail.
- **P1's hidden cost (F5).** The 2,187-line spec mandates events-only boundaries, so ADRs 0004 and 0011 must be
  superseded first. Split the spec into a map: long context files lower agent success and add more than 20% cost
  (R8 [55]).
- **Mind-changer:** the spec cannot be split and re-ratified in a week. Then take P2, whose docs are someone else's.
- **Folder:** `docs/spec/<section>.md` with an `index.md` (R8 §4a), plus superseding ADRs.

**7. OSS maintainer of a large Python framework.**
- **Top: P2 (8).** Frappe is MIT-licensed, Python, metadata-first, with DocType colocation (R8 §2.2), MCP built in
  (R7 [17]) and a proven country-app pattern (R8 [21]).
- **Bottom: P0 and P6 (1).** Both mean hand-building a framework.
- **Why.** P1's "narrow layer" is how every framework begins; one person then maintains it with a bus factor of one.
  Twenty's CRM-only engine has about 17k commits (R4 [3]). P3 adds a porting tax of about 75% per version (R4 [8]).
- **Mind-changer:** DR e-CF chains need `override_doctype_class` on core ERPNext, where the last installed app wins
  (R4 [5]).
- **Folder:** R8 §4b: a `<brand>_do` Frappe app calling the engine. Never fork ERPNext.

**8. LLM platform engineer (MCP servers, agent-facing APIs).**
- **Top: P5 (9).** Idempotency, RFC 9457 errors, per-module OpenAPI, test/live modes and audit make the API
  agent-grade (R6 §4). An OAuth-scoped MCP wrapper takes days; HubSpot shows buyers expect one (R6 [28]).
- **Bottom: P6 (1).**
- **Next.** P1's registry later yields per-tenant OpenAPI and MCP tools, as Twenty does (R1 [43]). Odoo's RPC
  surface over an untyped ORM is weaker [role judgment].
- **Mind-changer:** outcome-priced agents (R6 §4) sell only where you own the customer timeline. Then breadth comes
  sooner.
- **Folder:** a thin `apps/mcp` over the generated client (R8 §3.2).

**9. Static analysis and type-system expert.**
- **Top: P1 (8).** Generated typed models for first-party objects, with tenant fields behind one validated JSONB
  boundary (R3 §2.2), keep zero `Any` and zero suppressions.
- **Bottom: P2/P3 (3).** Frappe `_dict` documents and Odoo recordsets are dynamic. They cannot pass basedpyright
  "all" without a stub project [role judgment; R4 §3].
- **Note.** Pinning to 3.14 is not cheap here (F4).
- **Mind-changer:** stubs generated from DocType JSON type-check a real Frappe app with no suppressions.
- **Folder:** `packages/metadata` emits GENERATED `_models.py` per module, plus new import contracts (R8 §4a).

**10. Engineering manager who must hire the founder's first engineers.**
- **Top: P3 and P5 (7).** P3 has a hireable skill market (Odoo partners common in LATAM, R5 §2, DR not verified)
  and a community `l10n-dominicana` (R8 [14]).
- **Bottom: P0 (2).** A bespoke 17k-line chassis means Salesforce-Core-style months of onboarding (R1 [5]), while
  DR developers leave for USD jobs (R5 [44]).
- **On P6 (4).** .NET is the largest DR pool (R5 [43]), but the more-than-5-engineers trigger is not met.
- **Mind-changer:** DR has as many Python/FastAPI developers as Odoo developers.
- **Folder:** R8 §4b Odoo flavor: an OCA-style `<brand>-odoo` repo with `l10n_do_ecf`.

## 3. Panel summary

**Means:** P5 7.8 · P1 6.5 · P2 5.5 · P3 4.6 · P4 4.4 · P0 2.8 · P6 1.8.

**Favorite counts:**
- P1: 3 (staff engineer, DevEx lead, type-system expert)
- P5→P1: 3 (test architect, technical writer, LLM platform engineer)
- P5: 1 (fintech reviewer)
- P5→P2: 1 (agent researcher)
- P2: 1 (OSS maintainer)
- P5→P3: 1 (engineering manager)

Grouped:
- **Engine first:** 6 of 10 (P5 or P5→X).
- **Own Python layer for breadth:** 6 of 10 (P1 or P5→P1).
- **Framework breadth:** 3 of 10 (P2, P5→P2, P5→P3).
- **Nobody** favors P0, P4 or P6.

### Consensus recommendation: P5→P1, gated, with a time-boxed P1-vs-Frappe spike

**1. Now, for the next 4-8 weeks: P5.**
- Ship the e-CF engine as an API product: TesteCF certification, a managed container platform rather than K8s
  (R6 §3), 3-5 symptom pages, a restore drill and per-tenant export (R6 §2.2).
- Add a thin MCP server over the existing OpenAPI.
- Freeze the chassis.

**2. Prerequisites that serve both phases, done during step 1:**
- A fast per-package gate (F2).
- Mutation testing wired into the gate only for fiscal and money packages (F1).
- DGII-sourced fixtures beside the self-written goldens (F3).
- Single-writer agents with a clean-context reviewer (R7 [12]).
- Split the spec, and draft the ADRs that will supersede 0004 and 0011 (F5).

**3. Decision gate, after certification and before any breadth module.**
- Run one 10-working-day spike that builds the same slice twice: a custom field end to end (storage, API, form,
  list, filter, export) plus one cross-module report. Build it once as P1 (`packages/metadata`, a reporting view)
  and once as a Frappe app (R4 §5 checklist).
- **Choose P1** if its slice is done inside the box with zero gate suppressions.
- **Otherwise choose P2.** P3 is the fallback only if hiring pressure dominates.

**4. Breadth starts only on pull.** Build the CRM and invoicing UI when engine customers or SME prospects ask for it
(R7 §4), not before.

### Three strongest objections

1. **The market clock.** The last cohort deadline is in 21 days, 100k emitters already issue e-CF (R6 [38]) and
   190 providers are already authorised (CONTEXT). The engine may be a commodity by the time it is certified.
   Engine-first could mean selling into a closed window, while funded players (Alegra, Siigo, Odoo) take the SME
   product market (R6 §4).
2. **P1 is a framework in disguise.** "Narrow" metadata layers creep: renderers, permissions, migrations, reports.
   Salesforce's own data shows customisation sprawl (R4 [13][14]), and R1 §4 calls the full layer a year-scale
   platform bet. The spike may pass while the long tail fails.
3. **Deferral is itself a decision.** The 5-day framework experiment was recommended on 2026-10-20 and has not run.
   Each week of engine work adds chassis-coupled code and doctrine, which raises the switching cost to P2.

**Panel blind spot (stated openly):** these ten roles are paid to value types, gates and harnesses. That biases the
panel toward P1 and against untyped frameworks, and underweights distribution, which is where R6 §4 and R7 §4 say
small teams actually win or lose.

### Dissent

- **OSS maintainer (P2), agent researcher (P5→P2) and engineering manager (P5→P3)** hold that one person should
  never maintain a metadata framework. They would run the framework spike first, this month, and use P1 only for
  engine-adjacent configuration (print templates, numbering).
- **Staff engineer, DevEx lead and type-system expert** dissent the other way. They would start P1's descriptor
  work now, in parallel with P5, because the codegen pipeline already exists and waiting wastes it.

### Kill criteria (abandon the recommendation if any holds)

- **No market for the engine.** No signed engine customer, LOI or paid pilot within 90 days of TesteCF
  certification. Then pivot to distribution through an ecosystem: an Odoo or Frappe DR e-CF connector (R7 §3
  table).
- **The P1 slice fails.** It overruns 10 working days, or needs any suppression or `Any` to type tenant fields.
  Then switch to P2.
- **The inner loop stays slow.** The fast gate cannot get under about 3 minutes per package, or the clone/churn
  ratio rises release over release (R7 [3]). Agents are outrunning verification, so cut scope before adding breadth.
- **Buyers reject custody.** Integrators refuse third-party custody of their signing keys. Then P5's moat is gone,
  and the product must be the SME application (P2 or P3) with the engine embedded.
- **The deadline effect is gone.** DGII grants no further extension and new-emitter onboarding flattens after
  15 Nov 2026. Then the engine market is replacement-only, and breadth must come sooner.
