# Architecture verdict: CRM with ERP features (2026-10-25)

The conclusion of the 2026-10-25 architecture review. It changes no code and amends no ADR or spec section; the
decisions it asks for are listed at the end. Evidence and the full reasoning are in this folder (see `README.md`).

## The question

The owner wants a CRM with ERP features (customers, pipeline, quotes, invoicing with DR e-CF, accounting, inventory,
purchasing, payments, reports; more countries later), suspects the current architecture is the wrong path, and plans
a company of about 25-30 people. Is the architecture, the patterns or the language wrong, and what should be done?

## Method

1. Eight research reports with about 340 distinct cited sources (273 distinct URLs in R1-R7, 67 in R8), more than
   half primary: incumbents (R1), architecture styles (R2), data and tenancy (R3), extensibility (R4), language and
   stack (R5), scale, operations and market (R6), solo founder with AI agents and build vs buy (R7), folder structure
   (R8).
2. Seven proposals (`PROPOSALS.md`), given to the judges without the synthesis verdict to limit anchoring.
3. 100 judges: ten panels of ten roles (architecture, data, ERP/CRM domain, product and business, AI-assisted
   engineering, operations and security, UX and end users, legal and organization, stack advocates, big tech and
   ecosystems). Every role scored every proposal 0-10.
4. Tribunal 1: three independent judges (technical skeptic, business, long-term platform) who checked the panels'
   claims against the repo.
5. A fact verification pass against official sources (`research/VERIFY.md`).
6. An appeal tribunal: a red-team judge and an operator judge, each attacking the candidate plan and writing their own.

Every judge read the same reports, so their agreement is partly one framing repeated; tribunal 1 and the appeal
judges were asked to correct for that and did.

## Verdict

**The foundations are right. Building CRM/ERP breadth by hand on this chassis now is the wrong path. Change the
sequence, not the stack.**

### Architecture: keep the style, change the order of work

Keep:
- A modular monolith on one Postgres. Shopify (2.8M lines, 37 components), GitHub (about 2M lines, 1,000+ engineers)
  and Kraken (about 15M lines of Python, about 400 developers, import-linter, country layers) run this way. Segment,
  Istio and Prime Video went back from distributed designs. Uber advises small organizations to wait on
  microservices. This holds at 25-30 people.
- Pool tenancy with RLS (`FORCE`, tenant in every PK/FK/unique, no global uniques, organization as the future shard
  key), the transactional outbox and jobs in Postgres, the append-only double-entry ledger in the same database, the
  signer as the only separate process, countries as packages core never imports. One primary carries thousands of SME
  tenants; the points where Notion and Figma sharded (0.5-1 TB tables, CPU above 70-90 %, vacuum and XID pressure) are
  years away.
- No microservices, cells, Kafka, Redis or multi-region now.

Missing, and decisive for a CRM/ERP: a metadata/application layer. Every configurable CRM/ERP studied (Salesforce,
Dataverse, Workday, Odoo, Frappe, HubSpot, Attio, Twenty, Business Central) generates storage, API, forms, lists,
permissions and reports from one entity definition, and lets a tenant add fields, stages and rules without a release.
The repo has no such layer, no UI, no report engine, no user-facing field history and no search.

Too heavy for the current stage: about 17k hand-built chassis lines and a Kubernetes plan before a UI, a deployment or
a customer; and module isolation stricter than any cited monolith (schema + role per module, events as the only
cross-module link), which makes every cross-module ERP report a project. With a team of several groups those strict
boundaries start to pay (ownership, CODEOWNERS, import contracts), so they stay; what gets added later is a sanctioned
read path and same-transaction commands where a business process crosses modules.

### Patterns

Keep: outbox, idempotency keys, RLS with tenant-carrying keys, dated API versions, RFC 9457 errors, Standard
Webhooks, import-linter contracts, manifests as metadata-as-code for first-party code, typed ordered extension points,
an append-only ledger (never event sourcing), no workflow engine until sagas with compensation multiply (ADR 0003).

Adjust, when breadth starts (each needs a spec or ADR amendment first, because the spec wins):
1. Full ports-and-adapters layering only in deep modules (fiscal, signer, the DR channel); breadth modules use one
   folder per entity with plain CRUD.
2. Events for integration and side effects, not as the only glue between modules of one process (ADR 0004, 0011).
3. Sandbox organizations instead of the test/live `mode` column on ERP tables (it stays on the e-CF API product).
4. A published read-only reporting schema per module, read by a reporting role on a replica.
5. Tiered quality: 100 % branch coverage and mutation checks on fiscal, signer, money and ledger paths; typed and
   tested, with a fast per-module check, on CRUD.

### Language: stay on Python

Do not migrate the backend to Go, TypeScript or Rust.
- A port would cost weeks of agent time but repeats DGII certification and buys nothing measurable. Language effects
  on defects are "very small" (TOPLAS 2019 reanalysis); static types catch about 15 % of bugs (ICSE 2017), and the repo
  already runs strict basedpyright and mypy.
- Agents do best on Python: with the same agent, 63 % on SWE-bench Verified (Python) against 31 % Go, 34.9 % JS/TS and
  58.1 % Rust on SWE-bench Multilingual (small per-language samples, consistent direction).
- Odoo and Frappe are Python; Twenty is TypeScript; Business Central is .NET with AL. The metadata layer is
  language-independent.
- TypeScript only for the UI, with its client generated from the modules' OpenAPI documents. Rust or Go only for a
  measured hotspot. Reopen the language question only on a trigger: hiring more than 3-5 engineers locally where
  Python is scarce (then .NET for new modules, never a full port), hosting cost dominating margin, a measured latency
  problem, or the product becoming mainly a UI-first CRM (then TypeScript for that layer, the e-CF engine stays).
- Python 3.15 two weeks after release already forced a `cryptography` pin; going back to 3.14 is not cheap (519 files
  use 3.15-only lazy imports). Accept the wheel lag; do not chase new interpreter releases.

## Scores (means over ten panels)

| Proposal | Mean |
|---|---|
| P5 engine only, API-first | 7.14 |
| P1 evolve in place with an own thin Python application layer | 6.20 |
| P2 engine + Frappe for breadth | 5.62 |
| P3 engine + Odoo Community for breadth | 4.80 |
| P4 full-stack TypeScript metadata platform | 3.91 |
| P0 status quo | 2.22 |
| P6 rewrite on another stack | 1.57 |

P0, P4 and P6 were rejected by every panel. Nine of ten panels put the engine first; the UX panel was the exception
(users need screens).

## What the tribunals corrected

Checked in the repo by tribunal 1:
- no LICENSE file;
- no real TesteCF run yet, and no CerteCF step or PSFE application for the owner's RNC;
- the e-CF golden XML files are written by the builders under test (only DGII's XSD checks from outside);
- mutation testing is done by hand in task reports, not in `poe check`;
- ADR 0010 is still Proposed, with open signer risks (certificate password in a header, plaintext gRPC, no check that
  a document belongs to the certificate's company, no production network policy);
- the 15 Nov 2026 deadline cannot be met as an authorized provider.

The panels scored architecture quality rather than runway; the owner's monthly burn, legal entity and signing cost
appear in no document.

## Verified facts (`research/VERIFY.md`)

- Frappe has no confirmed official Postgres support for production (its install guide sets up MariaDB); ERPNext's
  Postgres CI is opt-in and a user reported failing queries in March 2026. Choosing Frappe means a second database
  engine and database-per-site tenancy, discarding the RLS pool design.
- Odoo Community is Postgres-only and LGPL-3; its `l10n_do` has accounting and NCF sequences but no e-CF; third-party
  e-CF modules sell for USD 59-299.
- ERPNext has no Dominican regional folder; no Frappe DR e-CF app was found. The e-CF engine is the scarce piece.
- Licences: Frappe MIT, ERPNext GPL-3, Odoo CE LGPL-3, Twenty AGPL-3 with commercial files. A Frappe app depending on
  ERPNext probably inherits GPL-3 (needs counsel).
- DGII: 190 authorized providers; about 100,000 emitters by mid-October 2026; the last cohort's deadline is
  15 Nov 2026; about 64 % of emitters use DGII's free facturador.
- Not verified: any public per-document price (the USD 0.02-0.05 band has no source), the signing cost, and whether
  Alegra issues through Alanube.

## The plan

Engine first, sales first, breadth decided by numbers. The appeal judges agree on the core.

### Next 14 days (only what the owner can do)

- Certificate and TesteCF access; one real document accepted on TesteCF.
- A LICENSE file.
- One paid counsel session on four points:
  - operating as a PSFE as a solo founder;
  - custody under Decreto 587-24 Art. 22;
  - Ley 172-13;
  - an OEM route under an existing authorized provider.
- A remote-signing cost quote.
- The owner's runway figure and legal entity.
- 10-20 customer conversations.
- Everything else stays frozen; no new chassis features.

### Days 15-90

- CerteCF for the owner's RNC; the PSFE filing or an OEM agreement.
- The certification-path engineering in `docs/handoff/BACKLOG.md` §1: Tasks 20-27 and 34.
- Close ADR 0010 and its open signer risks (BACKLOG §3: F2, F3, then F1/F4 after the owner's choices).
- DGII-sourced fixtures beside the builder-written goldens; mutation testing automated on fiscal and money paths.
- A capped Odoo connector in its own repository.
- Portal-lite.
- A capped revenue bridge from integration work.
- A managed container platform instead of Kubernetes, 3-5 symptom pages, a monthly restore drill, per-tenant export
  and soft delete.

### Gate (about 2027-01-24, written down and sent to an outside person)

- TesteCF green for types 31, 32, 33 and 34.
- An authorization route in motion (own PSFE or OEM).
- At least 3 letters of intent.
- At least 1 paying customer and about USD 1,000 collected.
- Margin per document of at least 50-60 %.

### After the gate

- First a fixed-screen accountant console: customers, quotes, invoices, and DR statutory outputs (606/607 and
  withholdings, to confirm with an accountant).
- The generic metadata layer opens only when three partners ask for the same missing capability and signed MRR
  reaches about USD 2,000, or at about 10 paying tenants. It is narrow: typed custom fields in JSONB with a registry and
  opt-in indexes, picklists, pipelines, saved views, print and email templates, a small trigger-condition-action rule
  engine on the job queue, reporting views. No custom objects, marketplace, BPMN or Temporal at first.
- Breadth fallback: Odoo Community (Postgres-only, LGPL, proprietary modules allowed), not Frappe.

### Do not

- grow the chassis;
- build a marketplace, custom objects, BPMN or Temporal;
- start the metadata layer to avoid the sales calls;
- hold clients' signing keys before counsel and insurance are in place;
- migrate the language.

## At 25-30 people

The owner expects a company of 25-30. That changes the build-vs-buy balance, not the sequence.

What changes:
- An own metadata layer becomes the main breadth path rather than a fallback. Three to five platform engineers can own
  it, and it avoids Odoo's yearly porting tax, ERPNext's GPL and Frappe's MariaDB. Odoo stays a sales channel and
  connector.
- The strict module boundaries earn their keep: one owning team per module, CODEOWNERS, import contracts.
- A TypeScript UI team and a design system.
- Managed Kubernetes becomes reasonable once one or two SREs exist; an on-call rotation needs at least six to eight
  people.

A plausible shape (Team Topologies: stream-aligned teams per domain on a platform):

| Group | People |
|---|---|
| Platform (metadata, reporting, permissions, API, security) | 3-4 |
| Fiscal and countries (e-CF engine, DGII, signer, new countries) | 3-4 |
| CRM and invoicing | 3-4 |
| Accounting and inventory (when it comes) | 2-3 |
| Frontend and design (embedded across teams) | 2-3 |
| SRE | 1-2 |
| Product and design | 3-4 |
| Accountants and fiscal experts | 2-3 |
| Sales, customer success, support | 6-8 |

Hire in stages, each tied to revenue or funding:
1. First four hires: a senior fiscal backend engineer, a frontend lead, a product designer and an SRE.
2. Months 6-12: 8-12 people.
3. Months 12-24: 25-30.

Python developers are easier to hire remotely across LATAM than locally in the DR (where .NET, PHP and Java are the
common stacks and developers leave for USD remote work). The main risk at this size is burn, not technology: a 30-person
team without engine revenue becomes the problem. Compete in a niche first (accountants or one vertical), not against
Alegra, Siigo and Odoo everywhere at once.

## Folder structure (R8)

Keep the current tree. It is better structured than most compared platforms: business-domain top level, one manifest
per unit, countries as packages core never imports, 48 generated import contracts, small files.

Add now:
- a LICENSE file;
- DGII-sourced fixtures (`dgii-official/`);
- a split spec with an index.

Add when the metadata layer opens:
- `packages/metadata` and `packages/reporting`;
- `modules/<m>/metadata/`, and one folder per entity in breadth modules, with tests mirroring them;
- `apps/web` (TypeScript) with `packages/ui` and a generated client; exclude the TypeScript workspace from the Python
  tooling.

Connectors live in their own repositories and call the engine through its generated client. If the breadth route ever
becomes Odoo, this repository becomes `ledger-engine` nearly unchanged and the ERP side follows Odoo's addon
conventions in its own repository.

## Kill criteria

Abandon or rethink the plan if any of these happens:
- TesteCF not passed in about 8 weeks;
- no paying pilot or letter of intent within 90 days of certification;
- margin under about USD 0.01 per document;
- the PSFE or custody route stalled past about 150 days;
- counsel rules out custody;
- operations take more than about 30 % of founder time;
- new chassis features appear after the freeze.

## Confidence

58-62 %. About 85 % on what not to do and on the first 14 days; about 40 % on the revenue path.

Open facts that could change the plan:
- the per-document price and the signing cost;
- whether a solo founder can lawfully be a PSFE with outsourced custody;
- whether partners pay recurring fees against USD 299 one-off modules and DGII's free facturador.

Confidence rises to about 75 % if counsel answers favorably and 3 of the first 8 calls ask for a price.

## Decisions for the owner

1. Adopt this plan, or say what to change.
2. Choose the LICENSE (proprietary or an open licence).
3. Book the counsel session and get the signing-cost quote.
4. Write down the runway figure and the legal entity.
5. Answer the open decisions in `docs/reviews/2026-10-20-stack-jury-synthesis.md` and the signer choices behind ADR
   0010 (KMS, remote signing, test mode).
