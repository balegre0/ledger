# Panel 07: UX, frontend and end users

Ten roles judge P0-P6 for this owner: a solo founder in the DR, building with AI agents, selling a CRM with ERP
features, up against the e-CF deadline. Evidence comes from R1-R8 and the repo (read-only). Any claim not found in
those sources is marked [role judgment].

Repo facts this panel leaned on:
- Spec §1.4 lists "An end-user web UI" as a non-goal of the first product. Accounting, inventory and POS are
  non-goals too.
- Spec §7.10: labels are `Mapping[Locale, str]` with at least `es` and `en`. Problem documents are English with a
  stable `code` that clients translate.
- Spec §8.6.6: the contingency regime covers two cases: "we can generate but not send", and paper NCF replaced
  later.
- ADR 0010: the signer is a separate server-side process (key custody).

## 1. Scores

| Role | P0 | P1 | P2 | P3 | P4 | P5 | P6 | Favorite |
|---|---|---|---|---|---|---|---|---|
| 1. B2B SaaS UX designer | 2 | **8** | 5 | 4 | 7 | 4 | 2 | P1 (hand-design 3 screens first) |
| 2. Frontend architect (React/TS) | 2 | 7 | 4 | 3 | **8** | 6 | 3 | P5→P4 |
| 3. Design-systems lead | 2 | **8** | 4 | 3 | 6 | 7 | 2 | P5→P1 |
| 4. Accessibility specialist | 3 | **8** | 5 | 4 | 6 | 6 | 2 | P1 |
| 5. Mobile / offline-first engineer | 2 | 6 | 5 | 6 | 4 | **8** | 3 | P5 (then decide by pull) |
| 6. Low-code / no-code builder | 1 | 5 | **9** | 7 | 7 | 3 | 1 | P2 |
| 7. Accounting clerk (8 h/day) | 2 | 4 | **8** | 6 | 2 | 6 | 2 | P2 |
| 8. Sales rep (CRM pipeline) | 1 | 6 | 5 | 5 | **8** | 2 | 2 | P4 |
| 9. Tenant administrator | 1 | 7 | **8** | 5 | 6 | 3 | 2 | P2 |
| 10. i18n / l10n engineer | 3 | **8** | 6 | 5 | 4 | 7 | 2 | P5→P1 |
| **Mean** | **1.9** | **6.7** | **5.9** | **4.8** | **5.8** | **5.2** | **2.1** | |

## 2. Per-role reasoning

**1. B2B SaaS UX designer (favorite P1).**
- **Top, P1 (8):** it is the only option where I own form, table and workflow design end to end, generated from
  one entity definition (R1 §2 P2; R4 §2 T1).
- **Bottom, P0 (2):** spec §1.4 makes the end-user UI a non-goal. No users means no usability feedback (R2 §4
  "no feedback loop", Fowler [14]).
- **Second, P4 (7):** Twenty already proves modern views and kanban (R1 [43]).
- **Condition:** hand-design the first three screens (customer, invoice, deal) with real DR users before
  generalizing the renderer.
- **Mind-changer:** a moderated test in which DR SME users finish quote→invoice faster in Frappe Desk than in a
  prototype.
- **Folder:** `apps/web` as a shell only, renderers in `packages/ui`, screens declared in
  `modules/<m>/metadata/views/` (R8 §4a).

**2. Frontend architect (favorite P5→P4).**
- **Top, P4 (8):** the expensive half is the metadata-driven UI, and R4 §3 says "nobody has started it".
  - Twenty already ships it in React, with 60 feature modules in `twenty-front` (R8 [28]).
  - Its AGPL app exception allows proprietary apps (R7 [20]).
  - One language across the client and the metadata engine removes the Python/TS seam that R5 §3 concedes.
- **Costs I accept:**
  - TS scores lower on agent benchmarks (R5 [33][34]).
  - Twenty is CRM-only.
- **Bottom, P0 (2):** a UI bolted on later to compile-time-only contracts (R4 §4 gap 1).
- **Mind-changer:** a spike in which the generated TS client plus JSON-schema metadata gives P1 a working form and
  list renderer in under four weeks.
- **Folder:** a separate pnpm repository for the Twenty app. The engine keeps a generated `clients/typescript`
  (R8 §4b).

**3. Design-systems lead (favorite P5→P1).**
- **Top, P1 (8):** one token set and one component library (`packages/ui`, R8 §4a) can serve three surfaces with
  one brand: the engine's developer console, the printed e-CF representation and the back office.
- **Low, P2 and P3 (4 and 3):** I inherit the framework's design language, plus a second one for everything
  outside it (R8 §4b: two repositories, two toolchains).
- **P5 first (7):** the first surfaces are docs and a console, small enough to set tokens before the app grows.
- **Bottom, P0 and P6 (2).**
- **Mind-changer:** Frappe or Odoo UI can be themed to the brand without forking views ([role judgment]; not
  measured in R1-R8).
- **Folder:** `packages/ui` owns tokens and components, `apps/web` only consumes them, and the client is generated
  into `packages/client-ts`.

**4. Accessibility specialist (favorite P1).**
- **Top, P1 (8):** a metadata-driven renderer is a single choke point. Labels, errors, focus order and table
  semantics are fixed once and apply to every entity (R1 §2 P2: one definition yields screens). Hand-built pages per
  entity make every screen a fresh source of defects.
- **Bottom, P6 (2), with P0 at 3:** "UI later" means accessibility gets retrofitted later. That is the
  retrofit-cost pattern R1 §3.7 documents for clean core.
- **Mid-scale for P2-P4:** no report measured WCAG conformance of Frappe Desk, Odoo or Twenty (not found).
- **Mind-changer:** an independent WCAG 2.2 AA audit of Frappe Desk that comes back clean.
- **Folder:** axe checks in the `apps/web` Playwright e2e tests and in the `packages/ui` component tests, inside
  `poe check`.

**5. Mobile and offline-first engineer (favorite P5).**
- **Top, P5 (8):** no option solves an offline device.
  - Signing is server-side by design (ADR 0010).
  - Contingency covers "generate but not send" and paper NCF (spec §8.6.6). It does not cover a device with no
    connection.
  - No researcher designs an offline client.
  - An offline-first POS or field-sales app is a separate product. Selling the engine to existing POS vendors
    reaches devices without building one (R6 §4: Siigo bought Bsale POS).
- **P3 (6):** Odoo ships a POS app ([role judgment]; not in the reports).
- **Bottom, P0 (2).**
- **Mind-changer:** the first DR prospects demand an offline POS bundled with the CRM.
- **Folder:** the engine repo gains generated SDKs in `clients/` (R8 §4b). A later `apps/mobile` would use the same
  client plus a local outbox.

**6. Low-code / no-code builder (favorite P2).**
- **Top, P2 (9):** Frappe gives on day one what Airtable and Power Apps users expect:
  - Custom Field, Property Setter, server and client scripts, role permissions, a report builder, REST and an MCP
    server (R4 [4]; R7 [17]);
  - customizations are records layered over a DocType that is never modified (R4 [4]).
- **Against P1:** P1 excludes custom objects at launch, which caps what I can build.
- **Bottom, P0 (1):** extension is compile-time only. Tenants get only 50 unindexed string keys of `metadata` (R4 §4
  gap 1).
- **Mind-changer:** proof that one database per site (R1 [41]) makes tenant operations unaffordable for one person,
  or that server scripts break the engine's trust model (R4 §3).
- **Folder:** a separate Frappe app with each entity colocated in `doctype/<entity>/` (R8 §4b).

**7. Accounting clerk, 8 hours a day (favorite P2).**
- **Top, P2 (8):** I need a mature GL, bank reconciliation, aged receivables and statutory books now.
  - ERPNext's GPL accounting can be used in a SaaS (R7 §3 C).
  - The e-CF engine sits behind Sales Invoice via `doc_events` (R8 §4b).
- **Against P0 and P1:** a hand-built GL means years before a keyboard-fast, clerk-grade flow exists. Spec §1.4 even
  lists accounting as a non-goal.
- **P3 (6):** I suspect full accounting sits in Odoo Enterprise, which is off-limits ([role judgment]; verify).
- **Bottom, P4 (2):** Twenty has no GL and no inventory (R7 §3 C).
- **Mind-changer:** a timed test in which I am slower in ERPNext than in Alegra.
- **Folder:** `<brand>_do/dr_compliance/report/` for the DGII sales and purchase books (R8 §4b).

**8. Sales rep who lives in the pipeline (favorite P4).**
- **Top, P4 (8):** I live in kanban boards, activity timelines and record pages.
  - Buyers compare against metadata-driven, AI-native CRMs (Attio and Twenty, R6 §4 trend 3).
  - Twenty is CRM-first.
  - R3 §3 gap 3: Party, Deal, Activity, the timeline and ownership are not built here at all.
- **P1 (6):** the narrow layer brings pipelines and saved views, but it would still be a home-made pipeline while
  Alegra and Odoo market AI (R6 [35][37]).
- **Bottom, P5 (2):** engine-only gives me nothing to use.
- **Mind-changer:** buyers turn out to want only invoicing plus a light pipeline.
- **Folder:** CRM entities as Twenty-style objects in `src/modules/<object>` (R8 §2.2), with the engine as a
  separate service.

**9. Tenant administrator (favorite P2).**
- **Top, P2 (8):** I must add fields, picklists, roles, print formats and email templates without waiting for a
  release. Frappe's Customize Form and Custom DocPerm are data, and the base DocType is never altered (R4 [4]).
- **P1 close (7):** it delivers T0-T2 (R4 §2) with stronger isolation (RLS, R3 §3). But it has no custom objects and
  no permission UI yet.
- **P3 (5):** custom modules are ported every version, at about 75 % of the original cost (R4 [8], partner claim).
- **Bottom, P0 (1).**
- **Mind-changer:** a P1 prototype in which I add a field, a pipeline stage and a print template in under a day
  each.
- **Folder (P2):** `fixtures/` for shipped configuration (R1 [40]). Tenant changes stay as site records.

**10. i18n / l10n engineer (favorite P5→P1).**
- **Top, P1 (8):** the bones are right.
  - Labels are `Mapping[Locale, str]` with `es` and `en`, and errors carry stable codes (spec §7.10).
  - Countries are packs that core never imports (R8 §2.3).
- **What P1 must add:**
  - label translations in the field registry (R3 §2.2);
  - ICU message catalogs and locale-aware DOP, number and date formatting from the first screen ([role judgment]).
- **P5 first (7):** country channels are the multi-country moat. Odoo's ~102 localizations (R8 [12]) are tempting,
  but its `l10n_do` holds the chart and taxes, not e-CF (R8 [11]).
- **Bottom, P6 (2).**
- **Mind-changer:** a second-country buyer who needs a localization Odoo already ships.
- **Folder:** `countries/<cc>/metadata/`, `i18n/` catalogs per module and per `packages/ui`, with string extraction
  checked in the gate.

## 3. Panel summary

**Means:** P0 1.9 | P1 6.7 | P2 5.9 | P3 4.8 | P4 5.8 | P5 5.2 | P6 2.1.

**Favorites:**

| Group | Count | Who |
|---|---|---|
| Ends in P1 | 4 | P1 alone: UX designer, accessibility. P5→P1: design systems, i18n |
| P2 | 3 | Low-code builder, clerk, tenant admin |
| Ends in P4 | 2 | P4 alone: sales rep. P5→P4: frontend architect |
| P5 alone | 1 | Mobile engineer |

Four roles want the engine first (P5) as their first step. Nobody favors P0, P3 or P6.

**Consensus recommendation: P5→P1 for the front office, with a measured P2 exit for the back office.**

1. **Ship the engine now (P5)** together with the one end-user surface it needs anyway.
   - That surface: a Spanish-first web console to issue, track and reprint e-CF.
   - This reverses spec §1.4's "no UI" non-goal for that console only.
   - The console seeds `packages/ui` and the design tokens.
2. **Build P1's narrow layer front office first:** customers and contacts, deal pipeline, quotes, invoices and the
   activity timeline. It comes with these conditions, all from this panel:
   - (a) hand-design and test 3 screens with DR users before generalizing the metadata renderer;
   - (b) keyboard-first data tables (bulk entry, inline edit, saved views) as a first-class component;
   - (c) WCAG 2.2 AA checks and i18n string extraction (`es-DO` default, `en`) in `poe check` from screen one;
   - (d) user-facing field history, separate from the hash-only audit (R3 §2.4);
   - (e) a size cap on the UI framework, so it does not become a second chassis (R7 §2.3).
3. **Do not hand-build a GL or inventory in year 1.**
   - Meet back-office needs through the engine's connectors (Odoo, ERPNext, local accounting software).
   - Before any own-GL work, run a two-week Frappe spike scored on two things:
     - R4 §5's checklist (custom field, pipeline stage, print template, upgrade of one customized module);
     - timed clerk and tenant-admin tasks.
   - If Frappe wins, the back office goes P2 beside the engine, following R8 §4b.

**Three strongest objections:**
1. **The renderer is the expensive half.** The metadata-driven UI is the costliest part of a CRM/ERP (R4 §3; Twenty
   has about 17k commits). The repo has no UI experience, and P1 risks repeating the chassis over-build on the front
   end (R2 §3, "building the platform instead of the product").
2. **Clerks and admins need breadth on day one.** Frappe ships Customize Form, role permissions, a report builder,
   print formats and MCP on day one (R4 [4]; R7 [17]). P1 at launch has no custom objects and no report builder, so
   early adopters get less configurability than a free framework gives them.
3. **The CRM will look dated against funded rivals.** A home-made narrow CRM competes with Attio- and Twenty-class
   UX and with Alegra, Siigo and Odoo, which are already marketing AI (R6 §4). Nobody, including P1, answers
   offline field sales or POS (spec §8.6.6 assumes a server is reachable).

**Dissent:**
- **P2 bloc (low-code builder, clerk, tenant admin; 3 of 10):** adopt Frappe now for all breadth, not only the back
  office. A narrow own layer delays exactly the configurability and accounting depth their users pay for.
- **P4 bloc (frontend architect, sales rep; 2 of 10):** the CRM is the product, so take Twenty's React metadata UI
  and keep Python only for the engine. They accept the weaker TS agent benchmarks (R5 [33][34]) and the AGPL
  boundary (R7 [20]).
- **Mobile engineer:** stop at P5 until a paying buyer defines the device story.

**Kill criteria (abandon or switch the recommendation if any of these hold):**
- **Renderer too slow (switch the front office to P2 or P4):** after 6 weeks of UI work, the P1 renderer cannot
  show customer, invoice and deal list and form views from metadata, or adding a tenant custom field end to end
  takes more than one day.
- **UI framework over-building (freeze it and reassess):** the generic UI framework (`packages/ui` plus the metadata
  engine) grows past roughly the size of the chassis's first year before a paying tenant uses it.
- **Back office pulled forward (adopt P2 for accounting):** 5 of the first 10 serious prospects make GL, bank
  reconciliation or statutory books a condition of purchase.
- **Clerk too slow:** in a moderated test, a clerk enters 20 invoices with e-CF more slowly in our UI than in Alegra
  or ERPNext.
- **Engine not selling (product-first over engine-first):** no paying vendor, integrator or accounting firm for the
  engine by the end of Q1 2027.
- **Offline required (re-scope around a POS partner or Odoo POS):** prospects require offline POS or field sales as
  a condition of purchase.
