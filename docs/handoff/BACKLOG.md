# HANDOFF BACKLOG: everything that remains (companion of HANDOFF.md, 2026-10-21)

Read `HANDOFF.md` first (owner rules, process, repo map, current state, pending decisions). This file is the full backlog in priority order.

## 0. Direction (architecture verdict, 2026-10-25)

`docs/reviews/2026-10-25-architecture-review/VERDICT.md` sets the order of work: the e-CF engine first, sales first,
CRM/ERP breadth decided by a numeric gate (about 2027-01-24). Keep Python, Postgres and the modular monolith; freeze
the chassis (no new generic platform features); no language migration. Until the owner adopts or amends the verdict,
read it as the recommended direction.

Owner-side actions (only the owner can do these; engineering does not replace them):
- Certificate and TesteCF access; the first real TesteCF run (§2).
- Choose the LICENSE.
- A counsel session: PSFE as a solo founder, custody under Decreto 587-24 Art. 22, Ley 172-13, an OEM route under an
  existing authorized provider.
- A remote-signing cost quote; the runway figure and the legal entity.
- 10-20 customer conversations.

Engineering added by the verdict (after §1, alongside §3):
- DGII-sourced fixtures beside the builder-written goldens (`countries/do/tests/contract/test_golden.py`).
- Mutation testing automated on fiscal and money paths (today it runs by hand in task reports).
- A fast per-package check next to the full gate.
- A managed container platform instead of managed Kubernetes in the deployment plan (§4 item 7); per-tenant export,
  soft delete, a monthly restore drill.
- Later, only through the gate: a capped Odoo connector in its own repository, portal-lite (§4 item 12), then the
  accountant console. The metadata layer, a reporting schema and boundary changes need a spec and ADR amendment first.

The 5-day ERP-layer experiment (§4 item 14) is replaced by the verdict's gate; if breadth is ever adopted rather than
built, the fallback is Odoo Community, not Frappe (Frappe has no confirmed official Postgres support).

## 1. Critical path to DGII certification (CERT-tagged; finish these first)

Plan: `docs/superpowers/plans/2026-10-19-v2-m3b-fiscal.md`. Each task has files, interfaces, steps and the review notes added later (the notes are binding).

1. **Task 19** (`do_ecf.advance` job) on `feat/m3b-advance-final` (worktree `ledger-t19`): reviewed, fix round done (review-fixed state); re-review the fix commits, then merge into `main`.
   Central property: a submission whose outcome may be unknown must never be resent.
   - the submission MARK is a column (`submission_started_at`), not a state;
   - track-id lookup before any resubmit; `outcome_unknown` parking;
   - `consultarfce` code 0 means not found, so it leaves the document in `outcome_unknown`;
   - released numbers when `secuenciaUtilizada=false`;
   - an answer about another issuer or e-NCF parks the job (`ParkReason.ANSWER_MISMATCH`) with the state unchanged;
   - fencing for concurrent advance; one test per sentence of the never-resend rules.
2. **Task 13** (`receiver` role, `apps/receiver`, port 8103): in `main`; reviewed on 2026-10-25 (CHANGES REQUIRED,
   no Critical) and fixed on the branch `feat/m3b-receiver-fix` (worktree `ledger-r13`): the receiver serves no
   OpenAPI document, its probes and metrics answer 404 through a proxy, it reads its trusted roots at start,
   IPv6 clients are limited by their /64 (the plan's Task 13 review note has the rest). Next: re-review if wanted,
   then merge into `main`. Owner/backlog notes from that review (not fixed):
   - `SignSeed` is probably unnecessary for the receiver (spec §3.7 lists it): a least-privilege question for the owner.
   - The receiver uses the `ledger_app` login (read-write on every module schema, spec §6): give the
     internet-facing app a narrower login.
   - The receiver archives (an archiving role) but has no `/archive` volume yet: Task 26 adds it, for raw inbound copies.
3. **Task 20** (fiscal module: `IssuePolicy`, routing, recovery and mirror consumers, `Renderer`). Binding notes, already in the plan:
   - add `invoicing` to fiscal's `dependencies.modules` and `implements = ["invoicing.IssuePolicy", "invoicing.Renderer"]`, then `uv run poe sync` (the layer order changes: check it);
   - every coroutine must be a `@facade`;
   - consume `fiscal_updated` UNORDERED (aggregate_version is None);
   - apply a `pending` status BEFORE allocating a number, on both the synchronous and the recovery paths;
   - treat `Applied.DOCUMENT_VOIDED` as "number nothing";
   - the recovery consumer is a no-op per (invoice, channel) if `after_issue` already numbered the document;
   - `after_issue` must catch `NoNumberAvailableError` after the invoice commit (never a client 409), and the consumer is woken by `RANGE_REGISTERED`;
   - `check` must object `field_unknown` to undeclared keys when no channel routes the document;
   - per-line withholdings are `fiscal.do.withholdings`, keyed by line index, and must be resent whenever `lines` change;
   - a void is refused while any fiscal status is non-final;
   - outside consumers use style "outside", timeout default 60 s, idempotent by event id or version;
   - **from Task 19's review:** `wake_all` does not move a job's `deadline_at`: a `do_ecf.advance` job parked for `certificate` or `outcome_unknown` and woken more than 15 days after enqueue dies at once (`deadline_passed`), leaving its document `numbered`/`signed`/`outcome_unknown`; the recovery consumer re-enqueues `do_ecf.advance` for an undecided document whose job is dead or missing (Owner item 13).
4. **Task 21** (DO delivery job, options, retry).
   - Call `deliver(..., authenticate=company_options.buyer_authentication)`.
   - Audit every `SeedSigned`, including `.seeds` on errors, masking the buyer.
   - Archive `ReceiptRefused.arecf` and never retry it; `BuyerRequiresAuthenticationError` is alertable.
   - Raise the worker pool size (see `docs/runbooks/platform/database-connections.md`).
   - From Task 19's review: enqueue `do_ecf.deliver` in `Advancing._decided` AND for every reception document already accepted with `delivery = not_started` when this lands.
5. **Task 22** (DO receiver routes).
   - `read_inbound_ecf`, then verify the signature with `INBOUND`, then trust.
   - Answer with `render_arecf(Receipt(...))` signed through SignerPort.
   - For an unreadable upload use `name_inbound`; if it returns None answer 400.
   - ARECF reason codes: 1 specification, 2 signature, 3 duplicate send, 4 buyer RNC mismatch.
   - `MAX_INBOUND` equals the route's 2 MiB limit; the receiver's mode comes from `ChannelDef.mode_of`.
6. **Task 23** (received documents API; commercial approval out).
   - Build a `CommercialApproval` and call `render_acecf`; send to the emitter with `BuyerClient.send_acecf` and to DGII with `DgiiClient.commercial_approval` (returns `Acknowledged | Refused`).
   - Response shapes and the file name `ACECF.xml` are UNCONFIRMED: verify on TesteCF.
7. **Task 24** printed representation (PDF + QR + security code; replaces the `representation` and `status` not-ready paths).
8. **Task 25** (`signer.certificate.installed` consumer in fiscal).
9. **Task 26** (fake DGII completed with consultarfce, anularrango and aprobacioncomercial; compose; end-to-end test against the fake DGII).
   - Needs the trusted-roots file (`deploy/local/do/roots.sh`) plus the buyer simulator's root, the `/archive` volume owned by uid 65532, and Postgres `max_connections=372` (compose sets it: 356 since Task 25, 372 since Task 23's `do_ecf.approve`).
10. **Task 27** (retire the spike CLI and `emission.py`; runbooks; as-built notes) and **Task 34** (final verification and whole-branch review).
    - From Task 19's review: the outcome-unknown alert and runbook also cover dead `do_ecf.advance` jobs (a signed + marked document whose job died logs no `do_ecf.waiting_for_a_person`) and `require_ready` failures counted as attempts during shutdown; keep the DO runbook's line on the one physical resend (the DGII client re-POSTs a document after a 401 with a renewed token, assuming a 401 means DGII did not take it).
    - LIVE Task 28 (from Task 19's review): honour a 429's `Retry-After` in the advance job. LIVE Task 31 (from Task 19's review): operator commands that wake, unblock or settle a document whose advance job is dead or past its deadline enqueue a fresh `do_ecf.advance` (or re-arm the deadline); reopening the track-id window or adopting a named track id is an operator command, never the wake (Owner item 13).
11. Decisions already taken inside those tasks:
    - promote Task 33 (code-4 replacement e-CF) from LATER to LIVE;
    - enable types 41/47 (`do_ecf_type_not_issued`) once Task 8's retention block is accepted;
    - the FMT PDF is not in the repo, so some field rules were checked only against the XSDs (open question for the owner or DGII: does a type 34 with `IndicadorNotaCredito`=1 still state ITBIS; Owner item 12).

## 2. Then: the real DGII (owner-side prerequisites first)

- First real TesteCF run with the owner's own RNC and certificate (runbook `docs/runbooks/do/testecf.md`).
- Settle spike findings F1-F16 against the real DGII.
- The 14 CerteCF certification steps for the owner's own RNC as emitter.
- Then the PSFE application: DGII answers in about 10 business days, then tests, a sworn statement, and INDOTEL-compliant custody.
- Unverified assumptions flagged in the plan: the shapes of `anularrango` and `aprobacioncomercial`, file names, and the buyer-seed relay question (spike F14).

## 3. Signer production blockers and follow-ups

Source: `docs/reviews/2026-10-20-signer-final-review.md`. ADR 0010 is still "Proposed" because the owner has not accepted the production choices.

| # | Follow-up | Size |
|---|---|---|
| F1 | AWS KMS adapter | 1 d |
| F2 | Check that the document's issuer RNC matches the certificate's company | 1-2 d |
| F3 | TLS on the gRPC port | 0.5-1 d |
| F4 | Certification-entity adapter for the `remote` backend (after a per-signature quote) | 2-3 d |
| F5 | Implement the owner's test-mode decision | 0.5 d |
| F6 | Dedicated thread pools and memory caps | 0.5 d |
| F7 | Move `Idempotency.once/stored` to `ledger_platform.data` | 0.5 d |
| F8 | Certificate path validation (chain and revocation) | 2-3 d |
| F9 | Exact `updated_at` on certificates | 0.25 d |
| F10 | Narrower DB roles and alerts | 1 d |
| F11 | Test that assembles the api role without loading custody | 0.25 d |

## 4. Jury-driven engineering tasks (not yet plan tasks; create them in this order)

1. Split a test-mode archive bucket (governance mode or short lifecycle) BEFORE any test object reaches production S3, because compliance locks last 10 years.
2. Partition `audit_events` monthly now (tables are empty).
3. Verify the DGII token cache key (environment only today; may need the certificate).
4. `/livez` for ALB/ECS health checks; `stopTimeout` 45 s; deregistration delay 30 s.
5. Egress proxy (Smokescreen) with a DGII allowlist.
6. Supply chain in CI: Renovate, pip-audit, Trivy, SBOM, cosign.
7. OpenTofu "cell" module and deploy pipeline (GitHub Actions by image digest, ECR, staging then prod); signer and receiver console scripts; restore-and-verify drill; a PITR numbering reconciliation runbook.
   Deployment checklist for the receiver (Task 13's review):
   - `LEDGER_RECEIVER_FORWARDED_HOPS=1` behind an ALB, or every client is the ALB's address and shares one allowance (the receiver logs `app.receiver_without_proxies` at start in staging and production with 0).
   - The ALB routes only `/<channel key>/` (`/do-ecf-v1/`) to the receiver.
   - Its health checks call the target directly, with no `X-Forwarded-For`: a probe or scrape that carries the header is answered 404.
8. Hash-chained audit anchors into the locked bucket, plus pgaudit.
9. Per-company claim fairness, one claim loop per process (idle polling), NOTIFY on enqueue.
10. Index `(organization_id, company_id, mode, issue_date)` for reports; 606/607 export (confirm the obligation with an accountant).
11. Developer adoption: plain API keys, optional `Idempotency-Key`, Spanish error text, docs site from OpenAPI, one SDK, Postman collection, status page.
12. Portal-lite: certificate upload, customers, invoices, PDF and QR.
13. CI split into parallel jobs; consider dropping mypy; tiered coverage.
14. The 5-day ERP-layer experiment (Odoo CE vs ERPNext vs a chassis GL spike) leading to ADR 0012, plus the one-way-door ADRs: GL model, sandbox company vs `mode`, consolidation RLS scope, accountant cross-organization model, UI architecture, master data home, third-party code policy, permission naming, cell and region model.

## 5. Milestones after M3b (see PROGRAM.md)

- v2-M4: reception completion.
- v2-M5: certification (CerteCF), PSFE, revocation/CRL.
- v2-M6: minimal production deployment (IaC, alerts, runbooks).
- Owner-minor follow-ups from the GitHub review: a null claim counts as absent; the snapshot child catches `Exception`; BodyLimit scan only if a larger-limit route exists; a rate-limiter comment; `Authentication.of` public.

## 6. Business actions (owner): see HANDOFF.md section 6. Do not build ERP breadth before the jury decisions are answered.

## 7. Definition of done

- All CERT tasks merged and reviewed.
- Full gate green twice on `feat/signer`, then a final whole-branch review.
- Merge into local `main`, and the gate green on `main`.
- Minimal production deployment (M6) with the signer blockers closed.
- DGII certification evidence collected.
- Never push without the owner's say-so.
