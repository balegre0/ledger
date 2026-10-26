# Panel 02: data and scale

Ten roles, judged from CONTEXT.md, PROPOSALS.md, R1-R8 and a read-only check of the repo. Repo facts checked for this
panel:
- RLS tenant context is transaction-local (`set_config(..., true)` in
  `packages/platform/src/ledger_platform/data/bound.py:40-42`), so it is safe behind a transaction-mode pooler (R6 [19]).
- A per-schema `purge` role exists (`bound.py:4-5`).
- Spec §7.9 (accounting) is design only.
- Ley 172-13 (personal data) is "with counsel" (spec lines 1822, 1894).

Anything not taken from a report or the repo is marked [role judgment].

## 1. Scores

| Role | P0 | P1 | P2 | P3 | P4 | P5 | P6 | Favorite |
|---|---|---|---|---|---|---|---|---|
| 1. PostgreSQL DBA / performance engineer | 4 | 8 | 4 | 4 | 3 | 7 | 2 | P1 |
| 2. Multi-tenant SaaS data architect | 4 | 9 | 5 | 4 | 4 | 6 | 2 | P1 |
| 3. Data engineer (CDC, warehouse, analytics) | 3 | 8 | 5 | 4 | 5 | 6 | 2 | P1 |
| 4. Sharding veteran | 5 | 8 | 6 | 5 | 4 | 8 | 2 | P5→P1 |
| 5. Fintech ledger engineer | 6 | 8 | 5 | 4 | 3 | 7 | 3 | P1 |
| 6. BI and reporting lead (ERP) | 1 | 6 | 8 | 7 | 5 | 4 | 2 | P2 |
| 7. Search and relevance engineer | 3 | 7 | 7 | 6 | 7 | 5 | 2 | P1 |
| 8. Zero-downtime migration specialist | 5 | 9 | 4 | 2 | 3 | 7 | 1 | P1 |
| 9. Data privacy engineer | 6 | 6 | 4 | 3 | 4 | 8 | 3 | P5→P1 |
| 10. Cost-conscious cloud DB architect (FinOps) | 3 | 7 | 6 | 5 | 4 | 9 | 1 | P5→P1 |
| **Mean** | **4.0** | **7.6** | **5.4** | **4.4** | **4.2** | **6.7** | **2.0** | |

## 2. Per-role judgments

**1. PostgreSQL DBA / performance engineer (favorite P1).**
- **Top, P1 (8):** it keeps the part that is already right. That is pool tenancy with RLS, the tenant leading in
  composite keys, and transaction-local tenant settings that a pooler can carry (`bound.py:40-42`; R3 §3 "above
  industry baseline").
- **Bottom, P6 (2):** it rewrites that layer for nothing.
- **Frameworks:** the database-per-tenant model of P2 and P3 fits "5 or 50 tenants" (R3 [12]). At thousands of SMEs it
  means thousands of autovacuum and XID surfaces and a migration fan-out on every release.
- **Fix inside P1:** the per-schema outbox, inbox and jobs tables multiply hot queue tables (R3 §3 gap 5). Breadth
  modules should share one queue schema.
- **Would change my mind:** Frappe Cloud shown to run 1,000+ sites with automated per-site migrations and no pager load
  on the founder.
- **Folder:** `packages/metadata` owns one `meta` schema, and breadth modules share a single outbox/jobs schema.

**2. Multi-tenant SaaS data architect (favorite P1).**
- **Top, P1 (9):** its custom-field design is R3 §2.2 almost word for word: a typed field registry, JSONB values with
  opt-in indexes, and no per-tenant DDL. Azure lists per-tenant columns as an antipattern (R3 [4]).
- **Bottom, P6 (2):** it throws away a tenancy foundation R3 calls textbook-correct.
- **Conditions on P1:**
  - No custom objects at launch; 51% of Salesforce custom objects go unused (R4 [14]).
  - Replace the test/live `mode` on ERP tables with sandbox organizations (R3 gap 6).
- **Would change my mind:** if the first paying customers demand a silo (their own database, self-hosting or a
  whole-database export). Database-per-tenant frameworks fit that natively.
- **Folder:** first-party objects in `modules/<m>/metadata/objects/*.toml`. Tenant overlays are rows in the registry,
  never files.

**3. Data engineer: CDC, warehouse, analytics (favorite P1).**
- **Top, P1 (8):** it gives one pooled Postgres, published reporting views on a replica, and an outbox that Debezium's
  router can read later without code changes (R3 [32], §2.5). Usage metering for AI pricing also needs cross-tenant
  data (R6 §4).
- **Bottom, P6 (2):** it does nothing for data.
- **Frameworks:** P2 and P3 mean one database per tenant, which brings back Notion's problem of one connector per shard.
  Notion replaced 480 Fivetran connectors with CDC (R3 [15]). Vendor-side analytics such as churn, adoption and metering
  get harder.
- **Would change my mind:** flat pricing with no cross-tenant analytics need. Then separate databases cost little.
- **Folder:** versioned SQL in `modules/<m>/reporting/*.sql`, published to a `reporting` schema, with a generated check
  that every view compiles after each migration.

**4. Sharding veteran (favorite P5→P1).**
- **The invariant is already paid for:** `organization_id` in every PK and FK, and no global uniques (R3 §3). This is
  what Notion, Figma and Instagram had to retrofit.
- **Scale is years away:** years 1-3 run on one primary (R6 §2.1), and Notion ran on one Postgres for 5 years
  (R6 [12]). Sharding should not decide this; the market should. That is why P5 ties P1 at 8.
- **Bottom, P6 (2):** it rewrites the scaling seam that already works.
- **Warning on P2/P3:** a database per tenant is "pre-sharded", but it breaks cross-tenant products, as it did for Slack
  Connect (R3 [18]). An accountant serving 40 SMEs is exactly such a product [role judgment].
- **Would change my mind:** a named customer whose tables would pass ~0.5 TB within 2 years (R3 §2.1).
- **Folder:** a devtools check that every metadata-generated index leads with the tenant columns.

**5. Fintech ledger engineer (favorite P1).**
- **Top, P1 (8):**
  - The ledger lives in the same Postgres and the same transaction as the invoice (R3 §2.3).
  - Spec §7.9 already matches Stripe and Modern Treasury: balanced postings, reversal only, unique per source.
  - P1's same-transaction facades post an invoice and its journal entry atomically. P0's events-only rule cannot.
- **Bottom, P4 (3):** Twenty is CRM-only and has no GL (R7 §3).
- **Add to the design:** clearing-account invariants and a pending/posted state (R3 [26][28]).
- **Frameworks:** ERPNext's cancel/amend and Odoo's reset-to-draft must be tested before anyone trusts them
  [role judgment].
- **Would change my mind:** a framework GL that passes the invariants in the 5-day experiment: posted entries immutable,
  accepted e-CF total equals revenue posted, and the AR subledger equals its control account.
- **Folder:** `modules/accounting` as a deep layered module, with insert-only journal tables and the invariants in
  `tests/contract`.

**6. BI and reporting lead for an ERP (favorite P2).**
- **Core argument:** an ERP is mostly reports (R3 gap 2).
- **Top, P2 (8):** Frappe ships a report builder, query and script reports, and print formats from metadata on day 1
  (R7 [17], R4 §3).
- **Bottom, P0 (1):** "no cross-schema view or join" makes every cross-module report an event-fed projection
  (R2 §3, R3 gap 2).
- **P1 (6):** its reporting views fix the read path. The report runner, pivots, dashboards and print engine are still
  unbuilt (R8 §3.3 gap 3).
- **Every customer needs them:** every SME and accountant needs the DGII sales and purchase books [role judgment].
- **Would change my mind:** P1 delivering a generic report runner (definition → SQL → table/CSV/PDF) plus both
  statutory books in about 3 weeks of agent time.
- **Folder:** Frappe `report/<name>/{json,py,js}` (R8 §4b). Under P1, `packages/reporting` plus
  `modules/<m>/metadata/reports/`.

**7. Search and relevance engineer (favorite P1).**
- **What users will search for:** exact RNC, cédula and NCF matches, plus accent-insensitive Spanish names. Postgres
  FTS (Spanish config, `unaccent`, `pg_trgm`) handles that, RLS-filtered and updated in the same transaction (R3 §2.6).
  ParadeDB is the in-database upgrade (R3 [35]).
- **Ties at 7:** P1, P2 (Frappe has a built-in global search) and P4 (Twenty ships search) [role judgment].
- **Why P1 wins the tie:** accountant search across client companies wants one pooled index governed by grants.
- **P5 (5):** it offers only identifier lookups.
- **Bottom, P6 (2):** it rebuilds the search foundation for no gain.
- **Would change my mind:** relevance-tuned fuzzy search over more than 10⁷ activity or email rows. That needs an
  external engine fed by CDC, which P1's outbox supports.
- **Folder:** `searchable: true` per field in `metadata/objects/*.toml`. `packages/metadata` generates the indexes; no
  per-module search code.

**8. Zero-downtime migration specialist (favorite P1).**
- **Top, P1 (9):** custom fields are data, not schema (R3 §2.7). Expand-only overlap migrations already exist (R3 §3),
  and N-1 compatibility is the Azure rule (R3 [4]).
- **Bottom, P3 (2):** Odoo custom modules sit outside the standard upgrade. Partners quote about 75% of the original
  cost for each yearly port, plus data scripts (R4 [7][8][9]).
- **P2 (4):** `patches.txt` has to run against every site's database.
- **P4 (3):** Twenty needs a migration runner that fans out per workspace schema (R3 [22]).
- **Would change my mind:** the 5-day experiment upgrading one customized module across 100 sites in under a day, with
  rollback.
- **Folder:** keep `modules/<m>/migrations/versions/`. The metadata-to-migration diff covers first-party objects only
  (R8 §4a), and a devtools check bans DDL keyed by tenant.

**9. Data privacy engineer (favorite P5→P1).**
- **Top, P5 (8):** data minimization. The engine holds fiscal data under a legal retention basis. A CRM adds emails,
  phones, notes and chats under Ley 172-13, which is still "with counsel" (spec line 1894).
- **Bottom, P3 (3):** in-process third-party Python sees the whole tenant database (R4 §3).
- **P1 (6):** it needs three things:
  - a value-bearing field history with retention rules, because the audit log stores hashes only (R3 §2.4);
  - a PII flag on every custom field, since JSONB hides what it holds;
  - CRM data kept out of the 10-year WORM archive.
- **Credit to P2/P3:** dropping a database makes erasure and per-tenant restore easy (R6 [6]).
- **Would change my mind:** counsel finding CRM processing adds no burden beyond the PSFE checklist (spec line 1822).
- **Folder:** a `pii:` attribute per field in metadata, with generated retention and erasure jobs that run under the
  existing `purge` role.

**10. Cost-conscious cloud database architect, FinOps (favorite P5→P1).**
- **Top, P5 (9):** the lowest burn and revenue first. Pooled Postgres keeps the marginal tenant cost in cents
  (R6 §3), and hosting is about 8% of spend under $1M ARR (R6 [24]). The real cost line is the founder's months.
- **Bottom, P6 (1):** it pays for the build twice.
- **P0 (3):** managed K8s plus Helm for five processes (R6 §2.3), and idle polling that grows with every schema (R2 §4).
- **P2 (6):** Frappe Cloud moves operations to a vendor at a price none of the reports gives.
- **The variable cost to watch:** LLM inference. It needs metering from day one (R6 §3).
- **Would change my mind:** a quoted per-tenant price on Frappe Cloud or Odoo.sh that is a small share of the planned
  ARPU.
- **Folder:** one image with thin `apps/`. `deploy/` targets a managed container platform, and a `modules/metering`
  module arrives when AI ships.

## 3. Panel summary

**Means:** P0 4.0 | **P1 7.6** | P2 5.4 | P3 4.4 | P4 4.2 | P5 6.7 | P6 2.0.

**Favorites:** P1 6 (DBA, SaaS data architect, data engineer, ledger, search, migration); P5→P1 3 (sharding, privacy,
FinOps); P2 1 (BI). No role favored P0, P3, P4 or P6.

### Consensus recommendation: P1, sequenced engine-first, with a data-layer prework clause and a P2 fallback

1. **Next ~8-12 weeks: P5 scope.** Ship and certify the e-CF engine, freeze chassis growth, and move from managed K8s
   to a managed container platform.
2. **In the same window, decide only the data choices that are cheap now and expensive later.** These are ADRs plus
   small code, not features:
   - the metadata registry and JSONB custom-field contract (typed, a PII flag per field, opt-in indexes, no per-tenant
     DDL, no custom objects);
   - a sanctioned `reporting` schema of read-only views per module, on a replica, as an ADR exception to the
     no-cross-schema rule;
   - ledger clearing invariants and a pending/posted state;
   - sandbox organizations instead of `mode` on ERP tables;
   - universal soft delete, per-tenant export and restore, and a monthly restore drill with WAL-archive alerts
     (R6 §2.2);
   - one shared queue schema for breadth modules.
3. **Build P1 breadth (CRM + invoicing for SMEs) only behind a gate:** paying engine customers, or SMEs asking for CRM.
   Start with the metadata layer and a generic report runner before the first new functional module (R3 verdict).
4. **Fallback:** switch the breadth layer to P2 (Frappe app beside the engine, R8 §4b) if the report-runner gate fails
   (no statutory books in about 3 weeks) or the 5-day experiment shows Frappe absorbing per-site operations.

### Three strongest objections

1. **This panel is biased toward what the repo already does well, which is storage.** R4 and R7 say the expensive half
   is UI, metadata and reports, and that ERP breadth "has historically taken an organisation" (R7 [15][19]). P1 risks
   becoming P0 with a metadata ADR attached.
2. **Timing.** The last DGII cohort's deadline is 15 Nov 2026 (R6 [38]), three weeks from now, and the engine is not in
   production. With 190 authorized providers and Alegra and Odoo already present (R6 [37]), the engine-first revenue
   premise may already be late.
3. **JSONB custom fields are weakest exactly where a CRM needs them.** The planner keeps no statistics inside JSONB
   (R3 [23]), and filtering and reporting on custom fields is the CRM's daily use. Frappe and Odoo use real columns
   per tenant database, which report better.

### Dissent

- **BI lead (P2):** reports are the product SMEs buy, and Frappe ships them now. Building a report engine is the
  same platform trap as the chassis.
- **Privacy, FinOps and sharding (P5→P1):** stricter than the consensus. Write the data ADRs, but no metadata or UI
  code until engine revenue exists.

### Kill criteria (abandon or re-route the recommendation)

- **The engine finds no buyer.** No paying vendor, integrator or accountant within ~90 days of the engine reaching
  production means the P5 premise is dead. Go product-first, deciding P1 vs P2 by the 5-day experiment.
- **The metadata layer stalls.** If ~6 weeks of agent time cannot produce typed custom fields, a generated list/form
  and the report runner on 3 first-party objects, switch breadth to P2.
- **Customers buy on custom objects or report depth.** If more than a third of lost SME deals cite them, the narrow
  layer is not enough. Adopt a framework.
- **Chassis or ops keeps eating the time.** If it takes more than ~25% of founder time for two consecutive months
  after the freeze, the chassis is still the product. Re-route.
- **Durability is unproven.** A failed or never-run restore drill, or a per-tenant restore that cannot be done, blocks
  any SME launch (R6 [6][7]).
- **Counsel requires silos.** If Ley 172-13 counsel or the first enterprise customers require per-tenant databases or
  residency that the pool cannot meet, that favors P2/P3 tenancy.
