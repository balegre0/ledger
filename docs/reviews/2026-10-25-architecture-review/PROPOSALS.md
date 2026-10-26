# The proposals on trial

Read CONTEXT.md first, then the research reports R1-R8 in this folder (R8 covers folder/code structure). Judge from the
evidence in those reports and in the repo (C:\Users\brian\Desktop\ledger, read-only), not from this file's ordering.

## Open questions the researchers disagree on

- Build a narrow metadata/application layer on the current Python stack, or adopt a framework (Frappe, Odoo CE,
  Twenty) for CRM/ERP breadth and keep the repo as an engine?
- If adopting: Frappe (MIT framework, GPL ERPNext, metadata-first, site = database per tenant, in-process apps) vs
  Odoo Community (LGPL, proprietary modules allowed, Enterprise off-limits, yearly porting tax, Odoo S.A. competes in
  SaaS) vs Twenty (AGPL with app exception, TypeScript, CRM only)?
- Sequencing: engine-first (sell the e-CF API to vendors/integrators/accountants, then breadth where customers pull) or
  product-first (CRM + invoicing for SMEs directly)?
- Repository and module folder structure for the chosen direction.

## Proposals

- **P0 — Status quo.** Keep building CRM/ERP breadth on the current chassis exactly as the spec says: per-module
  schema + role isolation, events-only cross-module coordination, compile-time extension only, generated contracts per
  module, managed K8s, 100 % branch coverage and mutation checks everywhere; UI later.
- **P1 — Evolve in place: engine + own thin application layer (Python).** Keep the stack and the monorepo. Freeze the
  chassis (no new generic platform features). Loosen boundaries for ERP areas: same-transaction cross-module commands
  through facades, published read-only reporting views on a replica, events kept for side effects and integration;
  strict isolation only for signer/custody and the fiscal core. Add a narrow metadata layer: entity/field descriptors
  in manifests (first-party objects) plus a per-tenant field registry (typed custom fields in JSONB with opt-in
  indexes), configurable pipelines, picklists, saved views, print/email templates, a small trigger-condition-action
  rule engine on the existing job queue; no custom objects, marketplace or BPMN at launch. Generated TS/React UI and
  client from OpenAPI + metadata. Strict gate kept on fiscal/money paths, lighter (still typed, tested) on CRUD.
  Managed container platform instead of K8s. Go-to-market: e-CF engine API first, then CRM + invoicing for SMEs.
- **P2 — Engine + Frappe for breadth.** The repo becomes a headless e-CF compliance engine service (chassis kept there
  because custody, archive and DGII justify it). CRM/ERP breadth on Frappe Framework (MIT): own proprietary apps for
  CRM, plus ERPNext (GPL) modules for accounting/inventory where useful; a DR localization app talks to the engine over
  its API. Hosting on Frappe Cloud or self-managed benches.
- **P3 — Engine + Odoo Community for breadth.** Same split, but the breadth product is Odoo CE (LGPL) with proprietary
  modules (DR e-CF connector, CRM extras); compete inside the Odoo ecosystem; Enterprise features off-limits; yearly
  version porting.
- **P4 — TypeScript metadata platform.** Build (or extend Twenty for) the CRM/ERP application layer in full-stack
  TypeScript with a Twenty-style metadata engine; the Python e-CF engine stays as a service.
- **P5 — Engine-only, API-first B2B.** Sell only the e-CF engine (API, connectors for Odoo/ERPNext/others, receiver,
  signer custody) to software vendors, integrators and accountants; build no own CRM/ERP until revenue and customer
  pull justify it; then choose P1/P2/P3 with evidence.
- **P6 — Rewrite on another stack** (.NET for DR hiring, or Go) as a full port, with its own metadata layer.

Hybrids and sequences (for example "P5 now, then P1 or P2") are allowed and expected; name them explicitly.
