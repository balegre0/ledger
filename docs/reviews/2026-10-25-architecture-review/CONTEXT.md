# Context for the architecture review (read this first)

## The owner and the goal
- Solo founder in the Dominican Republic (DR), building with AI coding agents. Wants to sell software "lo mejor de lo mejor".
- Product ambition (stated 2026-10-25): **a CRM with ERP features** (customers, sales pipeline, quotes, invoicing with DR electronic invoicing e-CF/DGII, accounting, inventory, purchasing, payments, reporting; many countries later). Earlier they also said "Alegra / Odoo / SAP class".
- They suspect the current architecture is the wrong path and wants an evidence-based verdict.

## What exists today (repo C:\Users\brian\Desktop\ledger, branch main; read-only for you)
- Python 3.15 **modular monolith**, uv workspace: `packages/` (kernel, platform = hand-built chassis, testing, fiscal-kit), `modules/` (identity, invoicing, fiscal, signer), `countries/do` (DR regime + e-CF channel: XML builders for all e-CF types, DGII client, state machine, numbering, receiver), `apps/` (api, worker, migrate, signer, receiver), `tools/devtools` (manifest-driven code generation + checks).
- Chassis: PostgreSQL, one schema + role per module, row-level security (tenant/company/mode), transactional outbox/inbox eventing in Postgres, jobs with leases/fencing, idempotency keys, GCRA rate limit, RFC 9457 errors, test/live modes, import-linter layering contracts, gRPC isolated signer process (key custody), S3 object-lock archive (10-year legal retention), FastAPI/Starlette, SQLAlchemy Core, Pydantic, Granian, Zitadel (identity provider).
- Size (measured 2026-10-20): ~55k lines of production Python, ~72k lines of tests; the platform chassis alone ~16.5k lines (~30%). Strict quality bar: basedpyright + mypy strict, zero suppressions, 100% branch coverage, TDD, mutation-checked critical paths. No UI at all. API-first. Not in production. Built in ~2 weeks with parallel AI agents.
- Key docs to skim: `docs/superpowers/specs/2026-10-13-ledger-platform-v2-design.md` (the spec), `docs/adr/` (11 ADRs), `docs/reviews/2026-10-20-stack-jury-synthesis.md` (an earlier 6-juror review), `docs/handoff/HANDOFF.md` and `BACKLOG.md`.
- The earlier jury's conclusion: keep Python/Postgres/modular monolith as a fiscal-compliance ENGINE; do not build ERP breadth on this chassis (no metadata layer, no UI/report engine, compile-time-only extensions, no cross-module read path); run a 5-day experiment Odoo CE vs ERPNext/Frappe vs custom GL; sell the engine to software vendors first; deadline-driven DR e-CF market (190 authorized providers, ~80k emitters).

## The question you serve
"What architecture (style, patterns, data model, extensibility model, language/runtime, platform/framework, build vs buy) should a solo founder + AI agents use to build a CRM with ERP features that can scale (tenants, modules, countries, team) and be sold, starting from the DR market? Is the current architecture the wrong path?"

## Rules for every agent
- Cite every claim with a URL; prefer primary sources (company engineering blogs, official docs, papers, conference talks, books). Mark vendor claims and anecdotes as such. Never invent sources or numbers; say "not found" when you can't verify.
- Do not edit the repo. Do not push. Write only to the scratchpad folder if you write at all. Answer in English (the owner reads the final summary in Spanish).
