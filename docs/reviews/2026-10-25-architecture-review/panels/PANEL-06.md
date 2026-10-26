# PANEL-06: operations, security, compliance

Ten roles judge P0-P6 for this owner: a solo founder in the DR, building with AI agents a CRM with ERP features
that depends on the e-CF market. Evidence: R1-R8, SYNTHESIS.md and the repo (read-only). Repo facts used:
`docs/adr/0010-signer-isolation.md` (custody, Decreto 587-24 Art. 22, accepted risks 1-13),
`docs/superpowers/specs/2026-10-13-ledger-platform-v2-design.md` §1.1-1.3 (PSFE route, success criteria),
`docs/reviews/2026-10-20-stack-jury-synthesis.md` §1-2 (deadline, ECS Fargate, six pages),
`docs/handoff/PROGRAM.md` (M3/M4 items deferred or blocked on the owner), and `deploy/` (compose only, no IaC).

## 1. Scores

| Role | P0 | P1 | P2 | P3 | P4 | P5 | P6 | Favorite |
|---|---|---|---|---|---|---|---|---|
| 1. Site reliability engineer | 2 | 7 | 5 | 4 | 3 | 9 | 1 | P5 → P1 |
| 2. Security architect, multi-tenant SaaS | 4 | 8 | 4 | 4 | 3 | 7 | 1 | P1 (engine-first go-to-market, which P1 already includes) |
| 3. PKI / key-custody specialist | 4 | 7 | 5 | 4 | 4 | 9 | 1 | P5 |
| 4. Compliance auditor (SOC 2, ISO 27001) | 3 | 7 | 4 | 4 | 3 | 9 | 1 | P5 |
| 5. Platform / DevOps engineer | 1 | 8 | 4 | 4 | 3 | 7 | 2 | P1 |
| 6. FinOps / cloud cost analyst | 2 | 7 | 4 | 4 | 3 | 8 | 1 | P5 → P1 |
| 7. Incident commander | 2 | 6 | 6 | 5 | 3 | 8 | 1 | P5 → P2 (on Frappe Cloud) |
| 8. Identity and access management architect | 3 | 8 | 4 | 4 | 4 | 6 | 1 | P1 |
| 9. Application penetration tester | 3 | 6 | 4 | 4 | 3 | 9 | 1 | P5 |
| 10. DGII regulatory advisor (e-CF, Ley 32-23) | 3 | 6 | 5 | 6 | 3 | 9 | 0 | P5 (Odoo connector first) → P3 only on pull |
| **Mean** | **2.7** | **7.0** | **4.5** | **4.3** | **3.2** | **8.1** | **1.0** | |

## 2. Per-role judgments

**1. Site reliability engineer.** Top P5 (9): three user-centric SLOs (API availability, e-CF final within N
minutes, buyer delivery) fit one person's error budget on one Postgres primary (R6 §2.1, §3). Bottom P6 (1): a
port resets every reliability signal. P0 scores 2: K8s, Helm and about 15 alert types exceed one person (R6
§2.3). P2 gets a nod: DB-per-site makes per-tenant restore trivial, the Atlassian lesson (R6 #6). Mind-changer:
if per-tenant export and restore on pooled RLS takes more than about two weeks, P2 wins. Folder: R8 §4b
`ledger-engine`, plus `docs/runbooks/` and an SLO document copied from the SRE Workbook (R6 #2).

**2. Security architect, multi-tenant SaaS.** Top P1 (8): one trust model, FORCE RLS with tenant-carrying FKs
("above industry baseline", R3 §3), and only out-of-process third-party code (R4 §4). Bottom P6 (1): it rebuilds
the tenancy proofs from zero. P2 and P3 score 4: in-process apps with full ORM and database access (R4 §3), which
Odoo's own SaaS bans (R4 #7), and two tenancy models joined by stored engine credentials. Mind-changer: a review
showing the Frappe/Odoo connector can hold per-tenant, scoped, revocable engine tokens with no shared superuser
key. Folder: R8 §4a, with `packages/metadata` held below modules by an import-linter contract and RLS policies
generated from metadata.

**3. PKI / key-custody specialist.** Top P5 (9): custody is unfinished and is the real legal risk. Only a
local-file KMS exists, there is no certification-entity adapter, chain, revocation and subject binding are
deferred, and plaintext signer gRPC makes a stolen worker token a signing oracle (ADR 0010, risks 8, 9, 13).
Decreto 587-24 Art. 22 forces the INDOTEL local-or-remote decision first. Bottom P6 (1): re-porting XMLDSig/C14N
is correctness risk (R5 §3). P2 and P3 are fine only if Odoo or Frappe never holds a .p12 [role judgment].
Mind-changer: a remote-signing INDOTEL entity at a workable price, which shrinks custody to an adapter. Folder:
`modules/signer` stays a deep hexagon, `isolated_in` and the custody import ban unchanged (R8 §4b).

**4. Compliance auditor (SOC 2, ISO 27001).** Top P5 (9): the smallest audit scope (one system, one IdP, one
subprocessor list), and the gate already yields change-management evidence (ADRs, `poe check`). B2B integrators,
the P5 buyers, are the ones who send security questionnaires [role judgment]. Bottom P6 (1): it discards the
evidence trail. P2 and P3 score 4: two stacks to patch, review and log, and Frappe Cloud or Odoo.sh becomes a
subprocessor whose SOC 2 report I could not verify (not found). Mind-changer: DR SME buyers never asking for
attestations, so scope size matters less. Folder: CODEOWNERS generated from the manifests' owners (R8 §2.7);
`docs/adr/` stays the decision log.

**5. Platform / DevOps engineer.** Top P1 (8): one image, one uv workspace, one CI gate, and a managed container
platform instead of K8s (R6 §2.3; the jury chose ECS Fargate, jury §2). The TypeScript UI slots in with uv
`exclude` (R8 §4a). Bottom P0 (1): Helm and five Deployments before any deploy exists (`deploy/` is compose only).
P2 scores 4: self-managed benches add Redis, a second database engine and workers [role judgment]. P3's yearly
port is a scheduled tax (R1 #37, R4 #8-9). P5 scores 7 because it builds no UI pipeline yet. Mind-changer: Frappe
Cloud absorbing all bench operations at a known price. Folder: R8 §4a, with OpenTofu under `deploy/` and a
manifest `kind = "web"`.

**6. FinOps / cloud cost analyst.** Top P5 (8): a fixed floor of hundreds of USD a month (R6 §3), per-document
revenue from day one, and hosting near 8 % of spend under $1M ARR (R6 #24). Bottom P6 (1): weeks of agent spend
for no unit-economics gain. P2 and P3 score 4: database-per-tenant fits "5 or 50 tenants" (R3 #12). Against Odoo
at €19.90-35 per user (R6 #35), a database per small tenant eats margin, while pooled RLS costs cents per tenant
(R6 §3). LLM inference is the cost that really hurts, so meter it in every option (R6 §4). Mind-changer: a
managed per-site price beating pooled cost below 300 tenants. Folder: R8 §4b, then §4a plus a metering slice.

**7. Incident commander.** Top P5 (8): one stack, one runbook set. Multi-day outages come from bad deletes,
untested backups and unstaged global changes (R6 #6-8). Bottom P6 (1): a new system nobody has run. I break from
the panel: when breadth comes, buy its operations (Frappe Cloud) instead of owning a second incident domain. One
person cannot command a 3 a.m. incident in a homegrown metadata engine and rule engine on top of DGII contingency
(R7 §2.1). P1 and P2 both score 6: P1 adds surface the founder alone restores, and P2 adds a seam where invoices
get stuck between systems. Mind-changer: Frappe Cloud refusing a contractual restore RTO. Folder: R8 §4b, two
repos, contract tests on both sides.

**8. Identity and access management architect.** Top P1 (8): one identity plane (Zitadel, RLS, permissions
from manifests) that extends to field-level permissions and to the agent-grade OAuth scopes and MCP buyers now
expect (R6 #28, §4). Bottom P6 (1): it rebuilds authorization from nothing. P2 and P3 score 4: their own user and
role models mean SSO bridging, duplicated authorization and engine secrets stored inside the ERP. P5 scores 6
because it postpones end-user IAM I would fix now. Mind-changer: the 5-day experiment showing Zitadel OIDC login
to Frappe/Odoo plus token exchange to the engine, with no shared secrets. Folder: R8 §4a, per-field permissions
in `modules/<m>/metadata/objects/*.toml`.

**9. Application penetration tester.** Top P5 (9): the smallest surface, with known, fixable holes: the
certificate password in a header (risk 7), plaintext signer gRPC (risk 8), the signing oracle (risk 9), the
receiver's public edge (commits `5c6a06d`, `9a28de3`). Bottom P6 (1): new, untested code everywhere. P1 scores 6:
generic CRUD-from-metadata plus a rule engine is IDOR and mass-assignment ground, RLS stops only cross-tenant
leaks, and AI-written code is less secure yet over-trusted (R7 #13). P2 and P3 score 4: widely deployed, routinely
scanned targets [role judgment]. Mind-changer: an external pen test of a P1 prototype finding no authorization
bugs. Folder: R8 §4b, `apps/receiver` kept a separate public edge.

**10. DGII regulatory advisor (Dominican e-CF, Ley 32-23).** Top P5 (9): DGII certifies the engine, not a CRM:
the owner's RNC as emitter (14 steps), then PSFE, then the simplified client flow (spec §1.3). The 15 Nov 2026
deadline cannot be met as a provider (jury §1), so the market is the late tail plus the 64 % on DGII's free
facturador, capped at 150 invoices a month (spec §1.1). Bottom P6 (0): every new implementation needs
re-certification (spec, risk table). P3 scores 6: Odoo ships `l10n_do` (R8 #11), so an Odoo connector is the
fastest channel, though module prices (US$80-295) cap it (R7 §3). Mind-changer: PSFE authorization stalled beyond
about 6 months. Folder: R8 §4b, an OCA-style `l10n_do_ecf` addon calling `clients/python`.

## 3. Panel summary

**Means:** P0 2.7 | P1 7.0 | P2 4.5 | P3 4.3 | P4 3.2 | **P5 8.1** | P6 1.0.

**Favorite counts:**
- P5 alone: 3 (PKI, compliance, pen tester).
- P1 alone: 3 (security architect, DevOps, IAM).
- P5 → P1: 2 (SRE, FinOps).
- P5 → P2: 1 (incident commander).
- P5 → P3: 1 (DGII advisor).

Grouped, 7 of 10 put P5 first, and 5 of 10 name P1 as the place breadth should end up. Nobody favors P0, P4 or
P6.

**Consensus recommendation: P5 now, then a gated, narrow P1, with P2 as the named fallback for accounting and
inventory breadth.**

1. **Phase 0 (now until PSFE authorization and the first paying integrators): engine only, made operable and
   certifiable.** Freeze the chassis.
   - Finish e-CF issuance and run the real TesteCF test set (`PROGRAM.md` M4).
   - Get the INDOTEL custody decision (Decreto 587-24 Art. 22) and ship the matching backend: a cloud KMS adapter
     or a remote-entity adapter.
   - Close ADR 0010 risks 7, 8, 9 and 13 before go-live: proxy and APM redaction, TLS on the signer port, the
     document-issuer check, and network policy and egress.
   - Deploy on a managed container platform with IaC and one image.
   - Monitoring: 3-5 symptom pages, an SLO document plus an error-budget policy, monitored WAL archiving with a
     monthly automated restore drill, per-tenant export and restore, universal soft delete, staged rollout, and a
     status page outside the product (R6 §3).
   - Ship connectors: the Odoo connector first (DGII advisor), then ERPNext.
2. **Gate to Phase 1, all four required:**
   - PSFE authorized;
   - at least two consecutive months within the error budget;
   - one restore drill passed end to end, per tenant;
   - concrete SME pull (paying pilots or signed intent) for CRM plus invoicing.
3. **Phase 1: narrow P1 on the same stack and the same trust model.**
   - Fields, picklists, pipelines, saved views and templates on fixed first-party objects (R4 §2).
   - Per-field permissions and a value-bearing field history (R3 §2.4).
   - An agent-grade API with OAuth scopes and MCP.
   - An external pen test of the generic metadata API before any tenant uses it.
4. **Accounting and inventory breadth is not hand-built.** If customers pull for it, run the jury's 5-day
   experiment and attach Frappe or Odoo beside the engine (P2 or P3). The signer and the archive never move into
   the framework.

**Three strongest objections:**
1. **P5's market may be too thin.**
   - 190 providers already compete, and Alegra and Odoo bundle e-CF.
   - Connector prices sit at US$80-295 (R6 #35-38, R7 §3), and the deadline wave passes before PSFE.
   - Engine revenue may never fund Phase 1. This panel weighs operability above market timing, and that is its
     bias.
2. **The P1 metadata layer is homegrown platform work for one person.** R7 §2.1 says ownership, not typing, is
   the constraint. R1 §4 calls a metadata layer a "year-scale platform bet". A framework's battle-tested metadata,
   UI and permissions might be safer than the agent-written ones the pen tester worries about.
3. **The panel's preference for pooled RLS conflicts with its own reliability lesson.** Per-tenant restore, the
   Atlassian failure mode, comes for free with DB-per-site (P2 and P3) but must be built on pooled RLS (R6 #21:
   PITR restores the whole cluster only).

**Dissent:**
- **Incident commander:** buy breadth operations (P5 → P2 on Frappe Cloud) rather than own a second incident
  domain.
- **DGII advisor:** the DR distribution channel is Odoo partners and accountants, so lean toward P3 if breadth is
  ever needed.
- **Security architect, IAM and DevOps:** commit to P1 now and design the end-user IAM and metadata permissions
  early, because retrofitting authorization is costly (R1 §3.7, the SAP clean-core lesson). They reject "wait for
  pull" as an indefinite postponement.
- **PKI and pen tester:** nothing beyond the engine until custody and the signer's accepted risks are closed.

**Kill criteria (signals to abandon or re-route the recommendation):**
- **PSFE or the INDOTEL custody route is blocked or stalled beyond about 6 months.** The engine cannot be sold as a
  provider. Re-route to selling an Odoo/ERPNext module to emitters who certify themselves, or partner with an
  existing PSFE.
- **Fewer than about 5 paying integrators or vendors within 90 days of PSFE authorization**, or price per document
  below the infra plus support cost per tenant. P5 alone is not a business. Move to product-first through the
  Odoo connector (P3 path) or P1 earlier.
- **The error budget burns two months running, a restore drill fails, or ops and incidents take more than about
  30 % of the founder's time.** Stop features until fixed. If it recurs, move more operations to managed services.
- **In Phase 1, the narrow metadata layer (fields, pipelines, views and generated UI on 3 objects) takes more than
  about 8 weeks, or forces new generic chassis features.** Stop P1 and take P2 for breadth.
- **Any cross-tenant data exposure in the external pen test of the metadata API**, or an intra-tenant
  authorization bug class that recurs after fixes. Halt P1 exposure until the permission model is redesigned.
