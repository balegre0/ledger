# PANEL 08: legal, finance, organization

Ten roles judging P0-P6 for this owner: a solo founder in the DR, working with AI agents, aiming at a CRM with ERP features,
and facing the e-CF market deadline. The deadline for the last cohort is 15 Nov 2026 (R6 #38), three weeks from
today, 2026-10-25. Repo facts were checked read-only. Facts used: there is no LICENSE file at the repo root
(`git ls-files`). The spec §1.5 commits to own PSFE certification. Decreto 587-24 Art. 22 forces either remote signing
through an INDOTEL-authorized entity or our own INDOTEL authorization for key custody. Spec §19 Q5 leaves that choice
open. The spec's compliance row lists the Art. 31 PSFE duties: confidentiality, SLAs, help desk, audit, backups, BCP
and a Ley 172-13 policy. Ley 172-13 is marked "with counsel" (spec, region question Q3).

## 1. Scores

| Role | P0 | P1 | P2 | P3 | P4 | P5 | P6 | Favorite |
|---|---|---|---|---|---|---|---|---|
| 1. Open-source licensing lawyer | 5 | 8 | 5 | 4 | 3 | 9 | 5 | P5→P1 |
| 2. Dominican tax lawyer | 3 | 7 | 5 | 6 | 4 | 9 | 1 | P5 |
| 3. Data-protection lawyer (172-13, GDPR later) | 5 | 8 | 5 | 4 | 5 | 8 | 3 | P5→P1 |
| 4. Technical due-diligence lead (acquirer) | 3 | 7 | 6 | 5 | 3 | 8 | 2 | P5 |
| 5. Team Topologies / org-design consultant | 1 | 4 | 7 | 6 | 3 | 9 | 2 | P5→P2 |
| 6. Founder coach (solo technical founders) | 1 | 4 | 5 | 6 | 2 | 10 | 0 | P5 |
| 7. Engineering hiring lead, DR/LATAM | 2 | 5 | 4 | 8 | 6 | 7 | 5 | P5→P3 |
| 8. Risk and insurance advisor | 4 | 6 | 5 | 5 | 4 | 8 | 2 | P5 (remote-signing first) |
| 9. Platform vs vertical-SaaS strategist | 2 | 8 | 6 | 3 | 4 | 5 | 1 | P1 (product-first, one vertical) |
| 10. Angel investor, ex-ERP executive | 1 | 5 | 5 | 7 | 3 | 9 | 1 | P5→P3 |
| **Mean** | **2.7** | **6.2** | **5.3** | **5.4** | **3.7** | **8.2** | **2.2** | |

## 2. Per-role judgments

**1. Open-source licensing lawyer.**
- **Scores.** Top: P5 (9). Its IP is 100% owned, and its only open-source contact is a connector whose licence you
  choose. Bottom: P4 (3). Twenty is AGPLv3, so any core change served over a network must be published, and files
  marked `@license Enterprise` need a commercial licence (R7 #20).
- **Other options.** P2 is fine on Frappe (MIT). ERPNext is GPL: SaaS-only use is safe, but listing an app that
  imports ERPNext on a marketplace or shipping it to a self-hosted client is distribution (R7 #16). P3 lets you keep
  modules proprietary (R7 #14), but Odoo Online bans custom modules (R4 #7), which cuts off half the channel.
- **Fix now.** The repo has no LICENSE file.
- **Mind-changer.** A written counsel opinion plus a pure-SaaS-forever commitment would lift P2.
- **Folder.** Add `LICENSE`, `NOTICE` and a dependency-licence check to `poe check`. Keep the connectors in separate
  repos with deliberately chosen licences (R8 §4b).

**2. Dominican tax lawyer.**
- **Scores.** Top: P5 (9). The licence to operate is PSFE authorization. The emitter certification comes first, then
  DGII's PSFE test set, and every new format version means re-certification (spec §1.5). Anything that delays this
  destroys value. Bottom: P6 (1). A port forces re-certification for nothing (R5 §3, R7 §3).
- **Gaps in the alternatives.** Odoo CE's `l10n_do` is only the chart of accounts and taxes; there is no e-CF channel
  (R8 §2.3). That gap is the commercial opening. P0 spends the months on breadth instead of certification.
- **Mind-changer.** If DGII denies or stalls the PSFE authorization for more than about 90 days, sell through an
  already-certified PSFE and lead with product (P1 or P3).
- **Folder.** Keep the regime as effective-dated data. Add `countries/do/reports/` for the statutory 606/607/608
  books [role judgment]. Keep the DGII/TesteCF golden files as the contract (R8 §4a).

**3. Data-protection lawyer.**
- **Scores.** Top: P1 and P5 (8). P5 has the smallest personal-data surface (B2B fiscal data). P1 keeps RLS pool
  tenancy and the hash-only audit trail (R3 §3), so privacy by design can be extended to the CRM. Bottom: P6 (3).
- **Risks in the alternatives.** CRM data is mostly personal data. Field history must store values with retention
  rules (R3 §2.4), and erasure requests collide with the 10-year fiscal archive. P3 is weak: multi-company inside one
  Odoo database has no isolation (R1 #36), and partner modules run in-process with full database access (R4 §3). AI
  features add cross-border transfers.
- **Mind-changer.** Counsel finding that Ley 172-13 requires in-country hosting or registration that only a framework
  host can meet.
- **Folder.** Add `pii` and `retention` keys to each `manifest.toml` and generate a data map (records of processing)
  from them.

**4. Technical due-diligence lead.**
- **What I would buy.** A certified PSFE with API customers, clean IP and documented ADRs (P5, 8). In LATAM, Siigo
  buys ecosystems (R6 #36).
- **What worries me.**
  - Key-person risk on a 17k-line bespoke chassis that only the founder understands (R7 §2.3). P1 scores 7, not
    higher, because of it.
  - Bottom: P4 (3). A second runtime, AGPL entanglement, and a CRM-only base.
  - P0 (3) is a diligence red flag: 127k lines, zero revenue, no UI.
- **Mind-changer.** A likely acquirer is an Odoo-partner roll-up that values installed-base modules; then P3.
- **Folder.** Trim to R8 §4b `ledger-engine`, generated SDKs under `clients/`, plus an SBOM and generated CODEOWNERS
  so diligence can map ownership.

**5. Team Topologies consultant.**
- **Diagnosis.** One person is one stream-aligned team with a fixed cognitive budget (R2 #38). Today that team is
  also running a platform team's work: the chassis is about 30% of the code (CONTEXT).
- **Scores.** Top: P5 (9), one complicated-subsystem domain: signer, DGII, numbering. Then P2 (7): Frappe Cloud
  becomes the "thinnest viable platform" bought as a service, and the founder stays stream-aligned on DR CRM. Bottom:
  P0 (1), which needs several platform and stream teams that do not exist.
- **Against P1.** It adds a metadata platform plus CRM breadth: two more domains for the same person.
- **Mind-changer.** Hiring 2-3 engineers within six months, which makes a real platform team, and so P1, viable.
- **Folder.** Two repos, engine and breadth (R8 §4b). This is a deliberate inverse-Conway seam with an API contract
  between them.

**6. Founder coach.**
- **What I see.** 127k lines in two weeks, no customer, no UI, and the market deadline three weeks away. That is
  engineering as procrastination. Agents made typing cheap, not ownership (R7 §2.1, METR, DORA).
- **Scores.** Top: P5 (10). It is the only option that can bill this quarter. Bottom: P6 (0). A rewrite is pure
  avoidance. P1 scores only 4: a "narrow metadata layer" is another platform project with a friendlier name.
- **Rule I would set.** No breadth code until five paying customers.
- **Mind-changer.** Signed letters of intent from SMEs asking for CRM plus invoicing as one product. Real pull earns
  P1 or P3.
- **Folder.** No new top-level directories until revenue. The only additions are `clients/` and a connector repo.

**7. Engineering hiring lead, DR/LATAM.**
- **The market.** Every option gets judged by who I can hire. .NET is the biggest DR pool (R5 #43). Seniors leave
  for remote USD jobs at $3.5-8k a month (R5 #44). Odoo developers and functional consultants are plentiful across
  LATAM (R5 table; DR not verified).
- **Scores.** Top: P3 (8). Hires are productive on day one. Frappe talent is India-centric [role judgment], so P2
  scores 4. Bottom: P0 (2). Nobody local is ready for basedpyright-strict, 100% branch coverage and a bespoke chassis.
  P6 (.NET) solves hiring but costs re-certification.
- **Mind-changer.** Survey data showing DR Odoo developers are scarce while Python seniors are available below
  $3k a month.
- **Folder.** An OCA-style addon repo with standard OCA CI (R8 §4b), so outside hires follow known conventions.

**8. Risk and insurance advisor.**
- **The exposures.** Three exposures can be insured or shrunk:
  - key custody (Art. 22);
  - fiscal errors that become client penalties (errors and omissions);
  - data loss.
- **Scores.** Top: P5 (8). It is narrow and controllable, provided the remote-signing backend is chosen first so the
  keys never reach us (spec §3.7). Bottom: P6 (2).
- **Risks in the other options.** P5 concentrates risk: one engine bug fans out to every vendor's clients at once.
  That needs contractual liability caps and staged rollouts (R6 #8). ERP accounting (P1, P3) widens the
  errors-and-omissions surface. In-process third-party modules (P2, P3) add supply-chain risk.
- **Mind-changer.** No insurer underwriting a solo PSFE that holds keys at a sane premium. Then stay remote-signing
  only, or white-label another PSFE.
- **Folder.** Keep the signer as its own app and role. Add `docs/runbooks/` with a restore drill, plus a per-tenant
  export tool in `tools/` (R6 §2.2).

**9. Platform vs vertical-SaaS strategist (dissent).**
- **The case against P5.** It makes the founder a supplier in a market with about 190 authorized providers (CONTEXT).
  The big buyers (Alegra, Siigo, Odoo partners) certify themselves (R6 #36-37), so P5 means low ARPU and commodity
  pricing, and someone else owns the customer (P5: 5).
- **The case against P3 and P4.** P3 (3) puts the business inside Odoo S.A.'s rules, and Odoo can bundle e-CF at
  any time. Horizontal platforms need capital and ecosystems (R1 P8). P4 (4) is a horizontal CRM against funded
  players.
- **My pick.** Vertical SaaS wins through embedded regulated workflow. Pick one vertical (for example distributors),
  ship CRM plus quotes plus e-CF on P1 (8), and use the engine API as a side channel.
- **Mind-changer.** Over 20 vendors paying for the API within 90 days.
- **Folder.** R8 §4a, but only the modules that vertical needs.

**10. Angel investor, ex-ERP executive.**
- **What I have seen.** ERP is a services pit: implementations, migrations, support, accounting corrections. Nobody
  funds a solo ERP. What I fund is usage-priced compliance that rides someone else's growth.
- **Scores.** Top: P5 (9), with the Odoo connector as the first channel (P3, 7). Odoo grows 48% a year (R6 #35), and
  DR community modules do not confirm e-CF (R8 §2.3). Bottom: P0 and P6 (1).
- **Mind-changer.** Odoo S.A. shipping native DR e-CF, as it does for other countries. Then pivot to non-Odoo
  vendors and accountants, and to P1.
- **Folder.** Keep the engine monorepo and add an `l10n_do_ecf` addon repo using the generated Python client (R8 §4b).

## 3. Panel summary

**Means.** P0 2.7, P1 6.2, P2 5.3, P3 5.4, P4 3.7, **P5 8.2**, P6 2.2.

**Favorite counts.**

| Favorite | Count |
|---|---|
| P5 alone | 4 |
| P5→P1 | 2 |
| P5→P3 | 2 |
| P5→P2 | 1 |
| P1 (product-first) | 1 |

Nine of ten start with P5.

### Consensus recommendation: P5 now, with a gated, evidence-based second step

**1. Next 90 days: P5, engine only.**
- Freeze the chassis. Get our own emitter certification and then PSFE authorization; these are the gating milestones.
- Choose the remote INDOTEL-entity signing backend first. This keeps key-custody liability off the books while
  insurance and counsel catch up.
- Ship the Odoo connector as the first channel (an LGPL addon you may keep proprietary or open as marketing). Ship a
  generic REST/SDK for POS, e-commerce and accounting software.
- Lay the legal floor before the first live client:
  - a LICENSE file and dependency-licence check;
  - a Ley 172-13 policy and data map (required by Art. 31 annexes anyway);
  - contractual liability caps;
  - errors-and-omissions and cyber cover;
  - a monthly restore drill and per-tenant export (R6 §2.2).

**2. Decision gate at month 6** (thresholds are [panel judgment]). Choose breadth only on evidence:
- **P1, narrowed to one vertical:** if SMEs are asking for one integrated product (≥5 signed LOIs or paying pilots).
  The scope is R4's T0-T2 only; build entity slices and `packages/metadata` per R8 §4a.
- **P3 deepened** (more Odoo addons): if the Odoo connector is the main revenue source.
- **P2:** only with a written counsel opinion on GPL and a SaaS-only distribution model.
- **P0, P4 and P6 are rejected.**

### Three strongest objections

1. **The buyer pool for P5 may be thin and commoditized** (strategist).
   - There are about 190 certified providers, and the big vendors self-certify.
   - The deadline rush ends 15 Nov 2026, before we can be certified.
   - We would be a supplier who never owns the SME.
2. **PSFE certification is a single point of failure** on a timeline we do not control (tax lawyer, coach).
   - Without it, P5 has no product at all.
   - Art. 31 duties (help desk, SLAs, BCP) are heavy for one person.
3. **Concentration and motivation risk** (risk advisor, coach).
   - One engine defect hits every downstream vendor at once.
   - "Engine only" contradicts the owner's stated "CRM with ERP features, lo mejor de lo mejor" vision. A founder who
     feels they abandoned the vision may relapse into chassis building.

### Dissent

- **The strategist** wants P1 product-first in one vertical now, with the engine as a side channel.
- **Disagreement on step two.** The hiring lead and the angel prefer Odoo (P3) because of talent and distribution.
  The Team Topologies consultant prefers Frappe (P2) as a bought platform. The two lawyers and the diligence lead
  prefer the founder's own narrow layer (P1) for clean IP and privacy control.

### Kill criteria (abandon or re-route the recommendation if these appear)

| Signal | Re-route to |
|---|---|
| PSFE authorization not granted within about 90 days of applying, or DGII imposes Art. 31 obligations a solo operator cannot meet | Sell through an existing PSFE and lead with P1 or P3 |
| By month 6, fewer than 5 paying integrators or vendors, or engine revenue below its running cost | Stop P5 as the main business |
| Odoo S.A. ships native DR e-CF | Drop the Odoo-connector channel |
| Counsel finds Ley 172-13 or Decreto 587-24 requires hosting or registration we cannot provide | Revisit the hosting model |
| No affordable errors-and-omissions or cyber cover for key custody | Stay remote-signing only |
| More than 20% of founder time goes back to chassis or platform work during the freeze | The freeze has failed; impose a hard stop |
| Fifteen or more inbound SME requests to buy CRM plus invoicing as one product | Open the P1 gate early |
