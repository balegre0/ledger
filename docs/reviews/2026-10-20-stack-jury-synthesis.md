# Stack review: jury synthesis (2026-10-20)

Six independent jurors reviewed the platform at `feat/signer` (c8cadde), read-only, with web research:
security/crypto/compliance, data architecture, SRE/cloud/cost, DR fiscal/regulatory, software architecture,
product/business. The owner's scope is a full business platform in the class of Alegra / Odoo / SAP, with e-CF
as the first module. This document merges the six opinions. Unverified claims are marked; nothing here is legal advice.

## 1. Verdict in five lines

1. **The core stack is right. Keep it:** Python + FastAPI, PostgreSQL (schema per module, RLS, outbox/inbox/jobs in Postgres),
   S3 Object Lock for the legal archive, isolated signer. No Redis, Kafka, Temporal, ClickHouse or OpenSearch yet.
2. **The code is ahead of everything around it.** Nothing deploys it (no IaC, no pipeline), nothing has run against the real DGII,
   there is no UI, no production KMS adapter, and the e-CF issuance path (M3b 17-22, 24, 26) is not finished.
3. **The ERP ambition is feasible as a direction, not as a solo plan.** The custom chassis is the right base for the regulated
   core (fiscal engine, custody, tenancy, ledger). It is the wrong base for ERP breadth (no metadata/UI/report/extension layers).
4. **Do not fork Odoo or ERPNext as the base** (unanimous on isolation grounds); **do** run a 5-day experiment on using one of them
   as the ERP layer next to the engine, and decide in ADR 0012.
5. **The 15 Nov 2026 deadline is 26 days away and cannot be met as an authorized provider.** Aim at the late-adopter tail and at
   software vendors, and start the owner-side work this week.

## 2. Where the jurors agree

| Topic | Consensus |
|---|---|
| Database | PostgreSQL 18, one cluster per cell, RDS Multi-AZ, PITR + AWS Backup Vault Lock + cross-region copy |
| Queue/cache/search | Postgres outbox/inbox/jobs; no cache outside process memory; `pg_trgm` for search. Switch triggers are written down per component |
| Cloud | AWS us-east-1 (the code already uses KMS and S3 Object Lock compliance) |
| Orchestration | ECS Fargate, not Kubernetes/Helm/External Secrets; per-service security groups; task roles for KMS |
| Edge | ALB + AWS WAF (count mode first); **no API gateway yet** (the app does auth, GCRA, body limits). Not Cloudflare in front of the receiver or of PKCS#12 traffic |
| IaC / CI | OpenTofu with a reusable "cell" module; GitHub Actions deploy by image digest; cosign, SBOM, pip-audit, Trivy, Renovate |
| Keys | AWS KMS adapter now; later per-company keys imported as non-exportable asymmetric KMS keys; INDOTEL custody decides local vs remote |
| Signer transport | TLS on the signer's gRPC port + per-caller tokens; mesh/mTLS later |
| Observability | OTel + Prometheus + Grafana Cloud; page on six alerts only |
| Process | Drop mypy (keep basedpyright); 100% branch coverage only on fiscal, money, signer and kernel code; one review round on non-fiscal code; split CI into parallel jobs |
| Python 3.15 | Defensible (GA 2026-10-09, cp315 wheels present), pinned by digest, GIL build, JIT off, move to 3.15.1 promptly |

## 3. Where they disagree, and the call

| Question | Positions | Call |
|---|---|---|
| Zitadel | Security: Zitadel Cloud, custom domain. Infra: self-host by year 1. Architecture: keep for operators, own API keys for integrators. Product: maybe drop | **Zitadel Cloud with a custom issuer domain now; plain API keys for integrators; revisit self-hosting when per-user pricing bites** |
| Signer as separate process | Product: run in-process until INDOTEL requires isolation. Security/fiscal/architecture: keep | **Keep it.** It is built, reviewed, and Art. 22 custody is the legal exposure |
| Build vs adopt for the ERP layer | Data and security: do not fork Odoo/ERPNext. Architecture: 5-day experiment, adopt a framework if it covers 80%. Product: sell the engine behind existing ERPs first | **Engine first, ERP layer after the experiment** (section 5) |
| `mode` (test/live) on every row | Data: wrong for GL/inventory | **Keep for the API and fiscal channels; model ERP sandboxes as sandbox companies** (decide before the accounting schema) |
| 100% coverage rule | Several jurors: too uniform | **Tier it** (see section 2) |

## 4. Defects and gaps the jury found (cheap to fix now)

1. **Test-mode documents get a 10-year compliance lock** (one archive bucket). Split a test bucket (governance mode, short lifecycle) before the first test object reaches production S3.
2. **No production KMS adapter.** Blocks go-live; about one day of work.
3. **Signing oracle.** Plaintext gRPC + shared-class tokens: TLS on the signer port; verify the signer rejects an XML whose `RNCEmisor` does not match the certificate's company (not confirmed in the code).
4. **ALB health check on `/readyz`** would cause restart storms on ECS; use `/livez`. Also `stopTimeout` 45 s, deregistration delay 30 s.
5. **DGII token cache keyed by environment only** (`dgii/client.py`). If DGII binds a token to the signing RNC, the key must include the certificate. Unverified; check against the spike.
6. **`audit_events` cannot be partitioned later without a rewrite.** Partition monthly now (tables are empty).
7. **Egress.** Nothing enforces it at the network level; buyer URLs are attacker-registrable. Add an egress proxy (Smokescreen) with a DGII allowlist.
8. **A PITR restore can re-issue or skip e-NCF numbers.** Write the reconciliation runbook (S3 archive + DGII track-id query).
9. **Per-IP limits on the receiver see the ALB address** (nothing reads `X-Forwarded-For`).
10. **Idle polling grows with every module** (per-kind claim loops, per-schema fan-out). One claim loop per process + NOTIFY before the module count grows.
11. **Connection budget.** Pools are sized per process; set explicit `max_connections`, add PgBouncer (transaction mode, not RDS Proxy: it pins on `SET`) at 60%.
12. **Reporting needs an `issue_date` index** (invoices are paged by `id` only).
13. **Supply chain.** None of the controls the spec promises exist in CI yet.
14. **Audit integrity.** Owner/migrator can rewrite audit rows: daily hash-chained digest into the locked bucket + pgaudit.
15. **Code-4 replacement e-CF (M3b Task 33) is LATER but belongs in LIVE.** Our own outage forces clients into paper contingency, and each paper NCF must be replaced by an e-CF within 30 days.

## 5. The ERP question

**Do not build ERP breadth on this chassis, and do not fork Odoo/ERPNext into it.** The chassis has no metadata layer (custom fields, views,
record rules), no UI/report/print engine, only compile-time extension points, and no cross-schema read path. Odoo/ERPNext enforce tenancy
in the ORM (Odoo: database per tenant, ERPNext: site per tenant) and run third-party modules in-process with full database access, so
the database-enforced isolation, the custody model and a safe marketplace cannot be retrofitted onto them.

**Two layers:**
1. **Engine (what exists):** fiscal compliance, custody, tenancy, ledger, public API. Sold directly to POS/ERP vendors and used by our own apps.
2. **ERP layer:** decided by a 5-day experiment after the first TesteCF run. Run Odoo CE and ERPNext in Docker with a thin connector to
   the engine; model one Dominican small-business month (chart of accounts, ITBIS, a stock item, a POS sale, bank reconciliation, 606/607);
   measure connector size, what needs Enterprise or OCA modules, upgrade cost, and customization effort. In parallel, a 3-day GL-core spike on the chassis for a cost reference.
   Decision rule: a framework covering at least 80% with a connector under about 1.5k lines is adopted. Record as ADR 0012.

**Decide before any ERP module (one-way doors):**
- GL model: double entry, immutable entries, reversals only, partitioned by month, balances maintained asynchronously.
- Sandbox modelling (sandbox company vs `mode`) and consolidation scope (RLS read policy over a set of companies).
- Cross-organization model for accountants and partners.
- UI architecture (metadata-driven vs hand-built).
- Shared master data (parties, products) home.
- Third-party code policy: marketplace apps out-of-process only (OAuth, webhooks, API), tenant logic in a WASM sandbox, no tenant SQL, no in-process foreign code.
- Fine-grained permission naming (`module.resource.action` + scopes) and a per-tenant export format.
- Cell/region model (`organizations.home_cell`), object keys prefixed `org/{id}/`.

**Revenue order (fiscal and product jurors agree):** e-CF engine and connectors, portal invoicing + POS-lite, accounting/GL with DGII
reports (606 and the IT-1 data first; accountants choose the software for small businesses), inventory/purchasing, bank import and
reconciliation, payroll via a partner (regulated, high liability), second country, then extensions/marketplace. CRM and BI: integrate.

**Feasibility, honestly.** Agents can generate ERP-breadth code in 1-2 years. They cannot supply accounting judgment, regulatory
verification, support, implementation services or sales. A solo founder can realistically reach a deep Dominican small-business suite plus the engine
by year 3; it needs 3-6 people. Full Odoo/SAP breadth across many countries is a direction, not a plan.

## 6. Business and regulatory facts the jurors established

- Deadline 15 Nov 2026 for small, micro and unclassified taxpayers (automatic six-month extension); large local and medium taxpayers lose the
  B series on 31 Oct. A KPMG note is ambiguous; no second extension found (unverified).
- DGII reports 190 authorized providers and over 80,000 electronic emitters, about 64% on the free facturador (capped at 150 invoices a month).
  Alegra's e-CF runs on Alanube; Odoo's app store already sells DR e-CF modules (about US$80-295).
- Test-environment (CerteCF) documents have no fiscal validity: a pilot there is not a launch.
- Retail price per document converges to about US$0.02-0.05; sell wholesale (US$99-299/month platform + about US$0.015-0.03 per document) to software vendors, Odoo integrators and accountants.
- Possible route to earlier revenue (to confirm with counsel): sell software to a taxpayer who certifies itself as an emitter.
- Provider authorization needs our own RNC certified first, then the PSFE tests, sworn statement, and INDOTEL-compliant custody (Decreto 587-24 Art. 22).
- Put the PSFE in its own legal entity so a DGII revocation (Art. 33) does not take the ERP down.
- Payroll (TSS, IR-3, Ministerio de Trabajo, cesantia) is a second regulated product: partner or defer.
- Offline POS must not put keys on devices; design with the contingency rules before building POS.

## 7. Cost sketch (USD/month, us-east-1, estimates; several unit prices from memory)

| Stage | Cost |
|---|---|
| Pre-revenue / staging | about 90 |
| 10 customers (prod + staging) | about 320 |
| 500 e-CF customers | about 970 (about 850 with a savings plan) |
| 500 ERP tenants, one cell | about 2,900 (about 5.80 per tenant) |
| 5,000 ERP tenants, four cells | about 19,000 (about 3.80 per tenant) |

Infrastructure stays at or under about 10% of revenue at Alegra-class prices. The costs that break unit economics are unmetered
messaging, report CPU, support staff and the remote-signing fee per signature (unknown: get a quote). Legal/compliance setup
(counsel, policies, insurance, penetration test) is roughly US$5-15k per year (estimate).

## 8. Capacity

Year 1 (300 tenants): about 25k documents a day, peak about 5 per second. Year 3 (2,000 tenants): about 165k a day, peak about 35 per second,
about 1.3M GL postings a day if full ERP. One m7g.large Multi-AZ primary holds year 3 for e-CF. First bottlenecks, in order: DGII latency and
429s (worker concurrency and the connections it implies), report queries on the primary, hot balance/stock rows, idle polling overhead. The load
test must throttle the fake DGII (1-2 s latency, occasional 429s) or it measures the wrong thing.

## 9. Proposed actions

**Owner side, this week (nothing in code depends more on it):**
1. File your RNC's electronic-emitter application and get TesteCF access (Q2).
2. Buy a digital certificate from an INDOTEL-authorized entity (about US$30-70 per year, estimate) and get a quote for unattended remote signing per signature (Q5).
3. Book counsel on: INDOTEL Art. 22 custody, Ley 172-13 cross-border hosting vs 10-year retention, liability caps and insurance, whether to use a separate PSFE entity.
4. Name three prospective customers or channel partners (software vendors, Odoo integrators, accountants).
5. Register the permanent domain (receiver hostname is registered with DGII per taxpayer and cannot change cheaply).

**Engineering, in order:**
1. Finish the CERT path: M3b 13-15 (in progress), 17-22, 24, 26, 27; signer 14-15.
2. Add tasks: AWS KMS adapter; test-mode archive bucket split; signer TLS; `/livez` for the ALB; code-4 replacement e-CF promoted to LIVE; OpenTofu cell module + deploy pipeline + signer/receiver console scripts; restore drill; CI split.
3. Add developer-adoption tasks: plain API keys, optional `Idempotency-Key`, Spanish error text, docs site from OpenAPI, one SDK, portal-lite (certificate upload, customers, invoices, PDF/QR).
4. Freeze: platform chassis, devtools, spec revisions, LIVE/LATER tasks except the above, Helm/Argo/k6.
5. After the first real TesteCF run: the 5-day ERP-layer experiment, then ADR 0012 and the one-way-door ADRs in section 5.

## 10. Decisions needed from the owner

1. Confirm AWS us-east-1 + ECS Fargate + OpenTofu as the target (vs another provider).
2. Confirm the two-layer strategy (engine now, ERP base decided by the experiment) instead of building ERP modules on the chassis.
3. Confirm launch on the `remote` signing backend through an INDOTEL-authorized entity (pending counsel), not `local` custody.
4. Confirm the freeze list and the process changes (drop mypy, tiered coverage, one review round outside fiscal/money/signer).
5. Confirm the first wedge: wholesale engine for software vendors and integrators, with a thin portal for the migration wave from the free facturador.
