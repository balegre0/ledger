# PANEL-09: stack advocates and contrarians

Ten roles, each arguing for its own stack or stance, then scoring honestly for THIS owner: a solo founder in the DR,
working with AI agents, building a CRM with ERP features, with the e-CF deadline closing (the last cohort's extension
ends 15 Nov 2026, R6 #38). Citations: "R5 #33" means source 33 in R5. "§" means a section of a report. Repo paths are
read-only evidence. "[role judgment]" marks an opinion with no source in the reports.

## 1. Scores

| Role | P0 | P1 | P2 | P3 | P4 | P5 | P6 | Favorite |
|---|---|---|---|---|---|---|---|---|
| 1. Full-stack TypeScript advocate | 2 | 5 | 4 | 3 | 8 | 6 | 2 | P5→P4 |
| 2. .NET / C# enterprise advocate | 2 | 5 | 4 | 4 | 4 | 7 | 6 | P5→P6 (.NET strangler for new breadth modules, not a port of the engine) |
| 3. Rails "one person framework" advocate | 1 | 4 | 8 | 6 | 3 | 6 | 3 | P5→P2 |
| 4. Go advocate | 2 | 5 | 5 | 4 | 3 | 8 | 4 | P5 |
| 5. Elixir / Phoenix LiveView advocate | 1 | 6 | 6 | 4 | 4 | 7 | 2 | P5→P1 (server-rendered UI, no SPA) |
| 6. Rust advocate | 3 | 6 | 4 | 3 | 3 | 8 | 3 | P5 |
| 7. No-code / low-code advocate | 0 | 3 | 9 | 6 | 4 | 6 | 1 | P2 (now, in parallel with the engine) |
| 8. "Buy SaaS and integrate" advocate | 0 | 3 | 4 | 5 | 2 | 9 | 0 | P5 |
| 9. Big-rewrite advocate | 1 | 3 | 6 | 4 | 6 | 4 | 7 | P6 (.NET, before the first customer) |
| 10. Spolsky "never rewrite" advocate | 4 | 8 | 5 | 4 | 3 | 7 | 0 | P5→P1 |

## 2. Roles

**1. Full-stack TypeScript advocate.** The front end will be TypeScript whatever happens (R5 §2). Twenty shows the
modern CRM blueprint: metadata rows, then DDL, then generated GraphQL, REST and MCP per workspace (R1 #43). TS is #1 on
GitHub (R5 #39), and type-constrained decoding cuts compile errors by 52-75 % (R5 #38). One language spares a solo
founder the Python/TS context switch. **Top, P4 (8):** the only option that puts the UI and the metadata engine in one
runtime. **Bottom, P0/P6 (2):** P0 is the breadth wall (R2 §3), and P6 ports the wrong thing. Honest concession: TS
scored 34.9 % against Python's 63 % on agent benchmarks (R5 #33). So sell the engine first. **Mind-changer:** a lawyer
saying that a DR ERP built on Twenty needs changes to the AGPL core, outside the app exception (R7 #20). **Folder:** a
Twenty-style monorepo with `packages/server/src/engine/metadata-modules` and `packages/front/src/modules/<object>`. The
Python engine becomes `ledger-engine`, reached through its generated TS client (R8 §4b, #24 #26).

**2. .NET / C# enterprise advocate.** .NET/C# is the most common DR stack (R5 #43), and the DR pool is small and drained
by remote USD jobs (R5 #44). Stack Overflow ran on a handful of servers (R5 #14). Microsoft's own SMB ERP gets its
extensibility from AL on .NET (R5 #15). XMLDSig ships in the base class library (`SignedXml`) [role judgment].
**Top, P5 (7):** cash first, and the hiring trigger (more than 3-5 local engineers, R5 §3) is not true yet. **Bottom,
P0 (2):** it grows a hand-built chassis and hires against Python in a .NET market. P6 gets a 6, not 9, because .NET has no
agent benchmark at all (R5 table D) and a port means re-running TesteCF (R7 §3B). **Mind-changer:** a plan to hire
fewer than 3 people within 2 years removes the case. **Folder:** if adopted, Business Central-style `src/Apps/<Module>`
plus `src/Layers/<CC>` country layers, with NetArchTest architecture tests (R8 #33 #42).

**3. Rails "one person framework" advocate.** The lesson from Rails is not the language. It is that one integrated
framework lets one person ship a whole product (R5 #13). This repo hand-built what Rails 8 ships by default: a
DB-backed queue (Solid Queue, R6 #17) and an ORM, plus 17k chassis lines (R2 §4). The nearest Python equivalent with
ERP metadata built in is Frappe: one DocType JSON yields table, API, form, list and permissions (R2 #25).
**Top, P2 (8):** convention over configuration, server-rendered Desk, MIT licence (R7 #17). **Bottom, P0 (1):** it
spends innovation tokens on plumbing (R7 #28). P1's generated React SPA is the two-language tax DHH warns against.
**Mind-changer:** the 5-day experiment showing that a custom field, view, print format and report on Frappe takes
longer than a day. **Folder:** a separate Frappe app with one folder per DocType (`doctype/ecf_submission/{.json,.py,.js,test_}`),
following India Compliance (R8 §4b, #20 #21).

**4. Go advocate.** Go suits services that look like infrastructure: one binary, low memory, Postgres-backed jobs
(River, R6 #14), Monzo and Uber fleets (R5 #16 #17). The e-CF engine (receiver, signer, DGII channel) is exactly that
kind of product. Honesty, though: Go has no business-app metadata culture (R5 table F), scored 31 % on SWE-bench
Multilingual (R5 #33), and no trigger to switch applies (R5 §3). **Top, P5 (8):** a narrow API product is Go's
territory, and today it ships fastest in Python. **Bottom, P4 (3):** a second app runtime. **Mind-changer:** measured
hosting cost per tenant dominating gross margin, or receiver memory/latency problems; then port that component only,
as Khan and Dropbox did (R5 #4 #5). **Folder:** keep the engine monorepo. If one service is ever ported, use
`cmd/receiver` plus `internal/<module>`, mirroring today's `apps/` and `modules/` (R8 §3.2).

**5. Elixir / Phoenix LiveView advocate.** LiveView's lesson is that a solo founder should not run a separate SPA:
server-rendered, stateful UI keeps one language and one deploy. The Ash framework gives declarative resources, a
metadata-like layer [role judgment]. Honesty: the ecosystem is small (Discord had to build libraries, R5 #18), Elixir
is absent from agent benchmarks and DR hiring data (R5 table H), and the XMLDSig risk is real. **Top, P5 (7)**, then
P1 with server-rendered pages generated from the same metadata (Jinja/HTMX-style) instead of a React SPA. **Bottom,
P0 (1):** no UI at all after 127k lines. **Mind-changer:** customers paying for real-time collaboration (shared pipeline
boards, presence). That is BEAM's niche (R5 #18), and it would justify Phoenix for the UI tier only. **Folder:** R8 §4a
with `packages/ui` replaced by server templates colocated in each slice (`quotes/views/`), Frappe-style locality.

**6. Rust advocate.** For key custody and fiscal correctness, Rust's guarantees are the gold standard. Rust also scored
58.1 % on SWE-bench Multilingual, the best non-Python score (R5 #33; samples are small). Still, the evidence only
supports component rewrites for measured hot spots (Discord, Figma, R5 #19 #20). **Top, P5 (8):** the engine is the
regulated, deep component where rigor pays, and the repo's strict gate is already Rust-grade discipline. **Bottom, P3
(3):** in-process, untyped modules with full database access (R4 §3) are the opposite of custody isolation. **Mind-
changer:** a CPython 3.15 wheel lag delaying a `cryptography` security fix (R5 #48). That would make a Rust signer
behind the existing gRPC seam (ADR 0010) worth doing. **Folder:** keep `modules/signer` behind `contracts/proto`. A
later Rust signer would be `apps/signer-rs/` implementing the same proto, with no change to callers.

**7. No-code / low-code advocate.** Every configurable CRM/ERP climbs the same ladder: configuration, then declarative
automation, then sandboxed code, then side-by-side apps (R1 P6). Customers customize through the UI (R4 §4 gap 2). A
"narrow metadata layer" (P1) means hand-building a worse Customize Form. Frappe already ships Custom Field, Property
Setter, Server Script, a report builder and an MCP server (R4 #4, R7 #17). **Top, P2 (9).** **Bottom, P0 (0):** a
tenant cannot add a field without a release (R4 §4). P3 gets only 6 because Studio is Enterprise-only and off-limits
(R7 #14). **Mind-changer:** evidence that DR SMEs do not customize: 51 % of Salesforce custom objects go unused
(R4 #14), and 64 % of DR emitters use DGII's free facturador (jury §5). Then metadata matters less than a fixed,
cheap app. **Folder:** Frappe flavor of R8 §4b. Customizations ship as fixtures in git, never as unmanaged production
edits (R1 #12).

**8. "Buy SaaS and integrate, don't build" advocate.** CRM is a generic subdomain: buy it (R2 #23). Funded consolidators
already own the SME base: Siigo with 1.3M customers, Alegra in 19+ countries, Odoo growing 48 % a year (R6 #35-37).
HubSpot exposes its CRM to agents through MCP (R6 #28). The only asset here that nobody else sells is a well-built,
custody-grade e-CF engine. Sell it wholesale and plug it into their ecosystems. **Top, P5 (9).** **Bottom, P0/P6
(0):** building ERP breadth alone took Odoo 100+ staff (R7 #15). **Mind-changer:** wholesale e-CF priced at or below
US$0.015/document against 190 providers (jury §5). If there is no margin even in the engine, become an Odoo or Alegra
partner instead of a vendor. Note that Odoo Online bans custom Python modules (R4 #7), so connectors must call an
external API. **Folder:** R8 §4b `ledger-engine` with `clients/python` and `clients/typescript` generated, plus one thin
connector repo per ecosystem.

**9. Big-rewrite advocate.** Joel's warning does not apply: no customers, no field-earned fixes, two weeks of code
(R7 §3B). The current design pays per-entity ceremony: about 6 directories and 8+ files per entity (R8 §3.3). ADR 0011
already had to invent "flows" (R2 §3). The direct cost of a port is 4-10 weeks (R5 §3), and it only rises once
customers exist. Rewrite before the first contract, onto the stack with the largest DR hiring pool (R5 #43) and its
own metadata layer. **Top, P6 (7).** **Bottom, P0 (1):** it locks in the ceremony. P5 gets only 4 because it defers
the stack decision until a rewrite is expensive. **Mind-changer:** DGII re-certification of a new implementation
taking more than 4 weeks or queueing behind the deadline rush. Then re-certification, not code, dominates the cost
(R5 §3 point 1). **Folder:** start from R8 §4a's breadth-slice layout from day one, not the layered hexagon.

**10. Spolsky "never rewrite" advocate.** R7 is right that Netscape's field bug fixes are absent here. But the asset
that would be thrown away is the 72k test lines encoding e-CF types, the DGII state machine, numbering and signing
(R5 §3), plus golden files (`countries/do/tests/contract/golden`). That is the hard-won knowledge. Architecture
problems are fixed by refactoring (R7 #26): freeze the chassis, loosen boundaries, add the metadata registry and a
reporting contract (R3 §4). **Top, P1 (8).** **Bottom, P6 (0):** delay in a deadline market, re-certification, and the
same breadth problem left unsolved (R7 §3B). P4 gets 3 because it is a rewrite of the app tier in a second language.
**Mind-changer:** the 5-day experiment showing a Frappe connector plus custom field/view/print working in under a
week, against a P1 estimate of months. **Folder:** R8 §4a exactly: `packages/metadata`, `packages/reporting`, entity
slices in breadth modules, and the hexagon kept in fiscal/signer/DR.

## 3. Panel summary

### Means (10 roles)

P0 **1.6** · P1 **4.8** · P2 **5.5** · P3 **4.3** · P4 **4.0** · P5 **6.8** · P6 **2.8**

### Favorite counts

- Sequences that start with P5: **8 of 10**.
  - Pure P5: 3 (Go, Rust, SaaS).
  - P5→P1: 2 (Elixir, Spolsky).
  - P5→P2: 1 (Rails).
  - P5→P4: 1 (TypeScript).
  - P5→P6 as a .NET strangler: 1 (.NET).
- Not starting with P5: P2 now, 1 (low-code); P6 now, 1 (rewrite).
- By end state for breadth: P1 2 · P2 2 (Rails, low-code) · P4 1 · P6 2 (.NET strangler, rewrite) · undecided/engine
  only 3.
- P0 is nobody's favorite and has the lowest mean. P3 is nobody's favorite.

### Consensus recommendation: "P5 now, P2 by default for breadth, P1 as the gated fallback"

1. **Now (weeks 0-8): P5.**
   - Keep Python and the repo as the e-CF engine, and freeze the chassis.
   - Finish TesteCF certification, with tests drawn from DGII samples, not agent-authored expectations (R7 §2.2).
   - Move from K8s to a managed container platform with 3-5 symptom pages (R6 §3).
   - Pin production CPython to N-1 (R5 §3).
   - Name 3 wholesale prospects (vendors, integrators, accountants).
2. **The experiment is the first connector.** Build the Frappe/ERPNext connector app (R8 §4b Frappe flavor) against
   the engine's generated Python client. It is both a sellable integration and the 5-day measurement: cost to add a
   custom field, a pipeline stage, a print format and a report; whether custody stays out of process; and the upgrade
   path (R4 §5).
3. **Breadth: P2 by default**, if all of these hold:
   - the experiment passes;
   - a lawyer clears MIT Frappe plus GPL ERPNext for the intended distribution (R7 §3C);
   - at least one paying engine customer or LOI exists.
4. **P1-narrow as the fallback**, if Frappe fails the experiment or the licence review:
   - custom fields in JSONB plus a registry, pipelines, views and templates;
   - a reporting-views contract;
   - no custom objects (R3 §2.2, R4 §2).
5. **P4 and P6 stay closed** unless their written triggers fire:
   - a UI-first CRM pivot with the Python side reduced to the engine (then P4);
   - a DR hiring plan of more than 3-5 engineers (then a .NET strangler, never a port) (R5 §3).

### Three strongest objections

1. **The timing may already be gone.** The greenfield e-CF wave crests on 15 Nov 2026 (R6 #38). There are 190
   authorized providers, and Alegra's e-CF already runs on Alanube (jury §5). An engine certified after the deadline
   sells into a switching market, which is slower and more price-driven than the panel's sequencing assumes.
2. **It does not build what the owner asked for.** They want a CRM with ERP features. "P5, then decide with evidence"
   can turn into an indefinite deferral, and a solo founder selling legal key custody to integrators faces
   counterparty-risk objections that no architecture fixes [panel judgment]. Waiting for customer pull may wait
   forever.
3. **The engine is still heavy.** P5 keeps the full 17k-line chassis, per-module schema/role/outbox sets and the 100 %
   gate (R2 §4, R3 §3 gap 5). Selling an API that carries fiscal and legal obligations implies near-24/7 reliability,
   which one person cannot carry (R6 §3). The engine may need slimming, not just freezing.

### Dissent

- **Big-rewrite (P6 now):** every month of P5 makes a stack change more expensive. Decide the forever stack before the
  first contract.
- **Low-code (P2 now, in parallel):** do not wait for engine revenue. The SME market will judge a UI with custom
  fields, and Frappe gives one in weeks.
- **TypeScript (P5→P4):** if the product ends up CRM-first, the metadata engine should live in the UI's language, and
  defaulting to Frappe locks breadth into Desk.
- **Spolsky:** P1 should be the default and P2 the fallback, not the reverse, because the repo's RLS and isolation are
  stronger than anything Frappe's ORM-level tenancy offers (R3 §4, R4 §3).

### Kill criteria (abandon or re-plan the recommendation if any of these occur)

- TesteCF/DGII certification not passed within ~8 weeks of starting P5.
- Fewer than 3 named prospects, or no signed LOI or paying engine customer, within ~90 days of certification.
- Achievable wholesale price at or below ~US$0.015/document, or 6-month engine revenue below hosting plus compliance
  cost (jury estimate US$5-15k/yr) (jury §5).
- The Frappe experiment needs more than 2 days for custom field + view + print + report, cannot keep custody out of
  process, or fails the licence review. Then switch to P1-narrow, not P0.
- The chassis grows after the freeze. Any new generic platform feature means the freeze failed.
- Buyers reject the engine and ask instead for a finished SME CRM+invoicing app. Then move straight to breadth (P2/P1)
  and treat the engine as an internal component.
- A measured DR hiring plan of more than 3-5 engineers, or a UI-first pivot. Then reopen P6 (.NET strangler) or P4 as
  R5 §3 specifies.
