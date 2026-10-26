# PANEL-04: Product and business

Ten roles judge P0-P6 for a solo DR founder who uses AI agents, wants a CRM with ERP features and faces the e-CF
deadline. All roles read CONTEXT.md, PROPOSALS.md and R1-R8, and checked the repo read-only.

**Business facts every role used (they come from the repo, not from R1-R8):**
- Today is 2026-10-25. The last cohort's deadline is 15 Nov 2026 (R6 #38).
- The owner's RNC has not had its first TesteCF run. It has not done the 14 CerteCF steps, and it has not applied
  to be a PSFE. A PSFE application needs the emitter certification first, and DGII answers it in about 10 business
  days (`docs/handoff/BACKLOG.md` §2; spec §1.5). Nothing gets certified in time to sell into the deadline rush.
- About 64 % of emitters use DGII's free facturador, which is capped at 150 invoices a month (spec §1.1).
- 190 PSFEs are authorized, and Alegra's e-CF runs on Alanube.
- Odoo app-store e-CF modules sell for about US$80-295.
- Expected prices: about US$0.02-0.05 per document at retail. Wholesale is about US$99-299 a month plus about
  US$0.015-0.03 per document (`docs/reviews/2026-10-20-stack-jury-synthesis.md` §6).
- Odoo Online does not allow custom Python modules (R4 #7).

## 1. Scores

| Role | P0 | P1 | P2 | P3 | P4 | P5 | P6 | Favorite |
|---|---|---|---|---|---|---|---|---|
| 1. Bootstrapped solo SaaS founder | 1 | 4 | 6 | 7 | 2 | 9 | 0 | P5→P3 |
| 2. Seed VC, LATAM B2B SaaS | 1 | 5 | 4 | 2 | 5 | 8 | 1 | P5 (multi-country compliance API, no own CRM) |
| 3. Pricing and packaging strategist | 2 | 8 | 6 | 3 | 4 | 6 | 1 | P5→P1 |
| 4. B2B product manager (SMB) | 1 | 6 | 8 | 6 | 4 | 6 | 1 | P5→P2 |
| 5. GTM / growth lead, LATAM SMB fintech | 1 | 7 | 6 | 5 | 3 | 5 | 0 | P1 (invoicing-first SME app, engine API in parallel) |
| 6. Dominican SME owner (buyer) | 0 | 6 | 6 | 7 | 4 | 3 | 1 | P3 |
| 7. Channel and partnerships manager | 1 | 3 | 5 | 6 | 2 | 9 | 1 | P5 (pure, neutral engine plus connectors) |
| 8. Competitive analyst (Alegra, Siigo, Odoo) | 1 | 4 | 7 | 4 | 3 | 7 | 1 | P5→P2 |
| 9. Customer success / support lead | 1 | 6 | 5 | 3 | 3 | 8 | 1 | P5→P1 (portal-lite, then invoicing) |
| 10. SaaS CFO | 1 | 5 | 6 | 5 | 2 | 9 | 0 | P5→(P2 or P3, chosen by the 5-day experiment, after an MRR gate) |

## 2. Per-role judgments

**1. Bootstrapped solo SaaS founder.**
- **Top, P5 (9):** it is the only option that can bill someone this quarter with code that already exists. R7 §2.1
  says small teams win with narrow, deep products. The jury's wholesale prices (§6) are enough to pay a solo founder.
- **Bottom, P6 (0):** weeks of zero revenue plus re-certification (R5 §3).
- **Second step, P3:** I would sell an Odoo add-on, because buyers already shop for paid DR e-CF modules there.
- **Mind-changer:** no paying vendor, integrator or accountant signs up within 60 days of certification. Then I would
  sell the Odoo add-on directly instead of the API.
- **Folder:** R8 §4b. Trim this repo to `ledger-engine`, and keep a separate OCA-style `<brand>-odoo/l10n_do_ecf`
  repository with a contract test against the engine.

**2. Seed VC, LATAM B2B SaaS.**
- **Top, P5 (8):** a compliance API that spans LATAM mandates is the only venture-shaped wedge here. The spec already
  plans Mexico CFDI, Colombia DIAN, Chile SII and Peppol (spec §1.1).
- **Bottom, P0 (1, tied with P6):** a solo-built ERP has no fundable story. R7 #15 and #19 show that ERP breadth took
  Odoo and Frappe whole organizations.
- **P3 (2):** it caps the company as a module vendor on a platform whose owner also sells SaaS. Odoo is valued at
  €10bn and growing 48 % a year (R6 #35).
- **P4 (5):** it fits the AI-native CRM narrative (R6 #30, #31), but not with one founder.
- **Mind-changer:** wholesale per-document prices fall below about US$0.01 because 190 providers commoditize them.
  Then the API is not venture-scale, and I would back P1 or P4 only if the founder adds a cofounder.
- **Folder:** R8 §4b engine repository with generated `clients/`. `countries/<cc>/` is the axis the business scales
  on.

**3. Pricing and packaging strategist.**
- **Top, P1 (8):** owning the metadata layer is what creates tier fences. HubSpot keeps custom objects for
  Enterprise only (R1 #31). It also lets you build the hybrid of seats plus metered AI that the market is moving to
  (R6 §4).
- **Bottom, P6 (1):** no pricing upside.
- **P3 (3):** it prices under Odoo's umbrella. Modules sell for US$80-295, and Odoo just reset Enterprise to €35 per
  user (R6 #35).
- **P5 (6):** retail per-document prices are converging on US$0.02-0.05, so the engine must be packaged on value
  (receiver, 10-year archive, custody as tiers), not on volume.
- **Mind-changer:** DR SMEs will pay only for compliance and not for CRM features. Then P1 adds nothing I can capture.
- **Folder:** R8 §4a, with `packages/metadata` exposing an entitlement check per field or feature.

**4. B2B product manager (SMB).**
- **Top, P2 (8):** Frappe gives forms, list views, a report builder, permissions and REST on day one (R7 #17, R2 #25).
  That puts real SME users in front of a product within weeks. R2 §3 lists "no user feedback loop" as a top risk.
- **Bottom, P0 and P6 (1):** both mean no UI and no learning.
- **Sequence:** P5 comes first because certification blocks every option anyway.
- **Mind-changer:** the 5-day experiment shows that a pleasant quote→invoice→e-CF flow for a non-accountant owner
  needs forking Frappe Desk. Then I would choose P1.
- **Folder:** R8 §4b Frappe flavor. A separate `apps/<brand>_do/` app, following the India Compliance pattern, that
  calls the engine's generated client.

**5. GTM / growth lead, LATAM SMB fintech.** I dissent from the panel.
- **Top, P1 (7):** the volume is direct. About 64 % of emitters are on the free facturador, capped at 150 invoices a
  month (spec §1.1), and the ones who outgrow it are a self-serve funnel reached through accountants and WhatsApp
  [role judgment]. P5 sells to a few vendors with long sales cycles.
- **Bottom, P6 (0).**
- **P4 (3):** wrong language for the agents (R5 §3) and wrong market.
- **What I would build:** portal-lite (`BACKLOG.md` item 12) growing into an invoicing app, with no CRM until
  activation data asks for it.
- **Mind-changer:** direct DR SME acquisition costs more than 3 months of revenue per account [role judgment]. Then
  channels (P5) win.
- **Folder:** R8 §4a, `apps/web` first, with `parties` and `invoicing` as slices and no `crm/` yet.

**6. Dominican SME owner (buyer).**
- **Top, P3 (7):** I do not buy APIs, so P5 scores 3 for me. I buy an app, from my accountant or a software vendor.
  - Odoo CE already ships `l10n_do` (R8 #11).
  - If a solo vendor disappears, an open platform means another partner can take over. That matters to me more than
    architecture.
- **Bottom, P0 (0):** nothing I can use (R2 §4).
- **My worry about P3:** Odoo Online forbids custom modules (R4 #7), so I would need partner hosting. Upgrades cost
  50-70 hours even with zero customizations (R4 #9). Alegra already publishes DR e-CF guides (R6 #37).
- **Mind-changer:** the total cost of an Odoo setup is higher than a simple hosted subscription. Then I would want P1
  or P2 on Frappe Cloud.
- **Folder:** R8 §4b Odoo flavor.

**7. Channel and partnerships manager.**
- **Top, P5 (9):** neutrality. An engine that does not compete with the ISVs, integrators and accountants who use it
  gets resold. That is the jury's wholesale route (§6).
- **Bottom, P0 and P6 (1).**
- **P1 (3):** selling CRM and invoicing to SMEs puts you in competition with the vendors you sell the engine to. That
  channel conflict is the risk nobody in R1-R8 priced.
- **P3 (6):** an Odoo connector is itself a channel.
- **Mind-changer:** my 3 named ISV or integrator prospects already use a PSFE such as Alanube and will not switch.
  Then the channel is closed and we go direct.
- **Folder:** R8 §4b. The engine has generated SDKs, and each connector lives in its own repository per platform,
  with contract tests on both sides.

**8. Competitive analyst (Alegra, Siigo, Odoo).**
- **Top, P2 and P5 (7 each):** a head-on SME suite loses. Alegra is in 19+ countries and raised for AI. Siigo has a
  US$100M plan and 1.3M customers. Odoo is growing 48 % a year (R6 #35-37).
- **The whitespace is ERPNext:** its `regional/` folder has no DR (R8 #19). A "DR Compliance" app, like India
  Compliance (R8 #21), is uncontested.
- **Odoo is crowded:** CE `l10n_do`, the community `l10n-dominicana` (R8 #11, #14) and paid modules.
- **P5 caveat:** Alanube already powers Alegra. The engine must win on custody, receiver, archive and an agent-ready
  MCP surface (R6 #28).
- **Bottom, P0 and P6 (1).**
- **Mind-changer:** evidence that ERPNext has almost no DR install base. Then the whitespace is empty for a reason,
  and I would pick P3.
- **Folder:** R8 §4b Frappe flavor.

**9. Customer success / support lead.**
- **Top, P5 (8):** support load grows with the number of vendors, not the number of SMEs. Idempotency, RFC 9457
  errors and test/live modes already deflect tickets (R6 §4). Spanish error text is already planned (jury §9).
- **Bottom, P0 and P6 (1).**
- **P3 (3):** every yearly Odoo upgrade would mean a support storm, at 50-70 hours even with zero customizations (R4
  #7-#9).
- **Second step, P1:** an own codebase keeps upgrade timing in our hands.
- **Mind-changer:** vendors pass their SMEs' "why was my e-CF rejected?" tickets through to us. Then P5 stops being
  low-touch, and support has to be priced into wholesale.
- **Folder:** R8 §4a for the later invoicing work. Add `docs/runbooks/<area>/` and an error catalog generated from
  RFC 9457 codes.

**10. SaaS CFO.**
- **Top, P5 (9):** fastest to first dollar, at high gross margin.
  - Infrastructure stays at or under about 10 % of revenue (jury §7). Hosting is about 8 % of spend under US$1M ARR
    (R6 #24).
  - Fixed legal and compliance cost is about US$5-15k a year (jury §7).
  - Illustration: 15 vendors × US$199 plus 150k documents × US$0.02 ≈ US$6k MRR [role judgment from jury §6].
- **Bottom, P6 (0):** founder months with no revenue, plus re-certification.
- **The real burn:** founder time, not infrastructure. ECS Fargate is already chosen (`docs/handoff/HANDOFF.md`
  line 105).
- **Mind-changer:** the remote-signing fee per signature (unknown, jury §7) is above about US$0.01. That would wipe
  out the margin at US$0.015-0.03 wholesale, unless we reprice or get our own INDOTEL authorization.
- **Folder:** R8 §4b. Breadth goes in a separate repository only after an MRR gate.

## 3. Panel summary

**Mean scores:** P0 1.0 | P1 5.4 | P2 5.9 | P3 4.8 | P4 3.2 | **P5 7.0** | P6 0.7

**Favorite counts:**

| Favorite | Roles |
|---|---|
| P5→P2 | 2 |
| P5→P1 | 2 |
| P5 pure | 2 |
| P5→P3 | 1 |
| P5→(P2 or P3 by experiment) | 1 |
| P1 | 1 |
| P3 | 1 |

That makes 8 of 10 roles P5-first. For the step after P5, the votes are: framework 4 (P2 2, P3 1, P2-or-P3 1), own
P1 2, never 2.

### Consensus recommendation: P5 now, behind commercial gates, then P2 or P3 by experiment; P1 only for a narrow invoicing surface

1. **Certification is the critical path, not architecture.** This week the owner files the RNC emitter application
   and does the first TesteCF run (`BACKLOG.md` §2; jury §9).
   - No proposal reaches the 15 Nov 2026 rush.
   - The market after that date is switchers, people outgrowing the free facturador, and late adopters.
   - Ask counsel now about the jury's earlier-revenue route: selling software to a taxpayer who certifies itself as
     an emitter.
2. **Sell the engine wholesale, as a neutral supplier.** Customers are ISVs, Odoo/ERPNext integrators and
   accountants. Pricing is a monthly platform fee plus a per-document fee, with receiver, archive and custody as paid
   tiers.
   - Before signing anyone, get a quote for the remote-signing fee.
   - Name 3 prospects before more engine work.
   - Only the production blockers continue on the chassis: KMS, the signer RNC match, TLS, the deploy pipeline.
     Freeze everything else.
3. **The only direct-SME surface is portal-lite** (`BACKLOG.md` item 12). It is the learning loop the PM and growth
   lead ask for, and it is a deliberately small product that does not compete with the channel.
4. **Breadth gate.** Open the CRM/ERP decision only when three things are true:
   - at least US$3-5k MRR or at least 10 paying accounts;
   - at least 3 customers asking for the same breadth feature;
   - a channel policy that states which segment the own product serves without undercutting engine customers.

   The breadth path is then picked by the 5-day experiment, with R4's checklist. The default is a framework: P2 if
   ERPNext's DR whitespace holds, P3 if buyers are on Odoo. P1's narrow metadata layer applies only if packaging,
   upgrade control or UI quality forces it.
5. **Folder structure:** R8 §4b now.
   - This repo becomes `ledger-engine` with generated `clients/`.
   - Connectors live in their own repositories, one per platform (OCA-style addon or a Frappe app like India
     Compliance), with contract tests.
   - Fix the 700-800-line `repositories.py` drift and split the spec into an index (R8 §3.3).
   - Add no `packages/metadata` before the gate.

### Three strongest objections
1. **The price and market ceiling of P5** (VC, pricing strategist).
   - 190 authorized providers, retail per-document prices converging on US$0.02-0.05, and Alanube entrenched behind
     Alegra.
   - Engine-only may cap at a lifestyle-sized business, and the owner said they want to build CRM/ERP. Motivation and
     ambition are risks too.
2. **The wrong funnel** (growth lead, buyer). The volume is about 100k SME emitters, most of whom buy an app and not
   an API. A vendor-only motion with long sales cycles may lose the post-deadline wave of facturador outgrowers to
   Alegra and Odoo partners.
3. **Channel conflict and deferred platform cost** (channel manager, pricing strategist).
   - The day breadth ships (P1, P2 or P3), the ISVs you supply become competitors.
   - Postponing the metadata decision means R3's rule ("metadata registry before the next functional module") will
     come due under customer pressure.

### Dissent
- **Growth lead: P1, invoicing-first, product-first.** Direct SMEs are where the volume is, and portal-lite should
  grow into the product now, not after a gate.
- **SME buyer: P3.** Buyers want a familiar platform with an open-source exit and local partners, and the engine
  alone sells nothing to them.
- **VC: P5 forever.** Wants no own CRM at all, scaling by adding countries instead.

### Kill criteria (signals to abandon the recommendation)
- **The emitter certification for the owner's RNC is not done by about the end of January 2027, or PSFE
  authorization is not realistic by Q2 2027.** Switch to software for self-certifying emitters, or an
  Odoo/ERPNext add-on that rides on another PSFE.
- **Fewer than 3 paying pilots or signed LOIs from ISVs, integrators or accountants within 90 days of
  certification.** Go direct (growth lead's P1 or the buyer's P3).
- **The realized wholesale price is below about US$0.01 per document, or the remote-signing fee takes more than
  half the per-document margin.** Reprice, or stop selling the engine as the main product.
- **Support passthrough: more than about 2 tickets per paying account per month from vendors' end customers**
  [role judgment]. P5's low-touch economics are false.
- **Counsel says INDOTEL Art. 22 custody cannot be met** (no certification-entity contract and no own
  authorization). The engine cannot legally hold keys, so P5's custody moat is gone.
