# Invoicing (v2-M3a) whole-branch review, 2026-10-19

Plan Task 7 (review pass). Range `main..feat/invoicing` at `2ab3369`: Tasks 2-6 and their fix commits
(`bc1a9a9`, `96cd1c2`, `51cf423`, `724e4bc`, `7f326bc`, `f8f108b`, `173b581`, `2ab3369`). Read against the plan
(every "Rules decided" section), spec §3.4, §7.3-§7.5, §8.5, §9, the 2026-10-16 architecture review and
AGENTS.md. `173b581` and `2ab3369` had passed only the implementer's gate before this review.

**Verdict: READY AFTER FIXES.** Nothing in the branch leaks across tenants, prints a tax id or gets an amount
wrong for the documents it accepts. One tax rule is missing (I1) and should land before M3b Task 12 builds on
notes. The branch merges into `main` without conflicts (`main` gained only plan files since the fork). Merging
now is safe if I1 is the first follow-up commit on `main`.

**Gate.** `uv run poe check` passed on its first run (see "Gate" at the end).

## What was re-verified

### `173b581` (Task 4-5 review fixes)

- **Replay before the specific permission.** `_replayed` (`adapters/http/invoices.py:170-183`) runs after the
  route's reader dependency (401/404/403 read), in a unit of the request's tenant, and only then calls
  `access.require(needed)`. The stored answer is returned only when the request hash matches. That hash covers
  the route, the concrete path, the query, **the company** and the canonical body
  (`ledger_platform/http/idempotency.py` `Attempt.of`), and the row is the caller's own (principal, route,
  organization, mode). **Exposure check:** a downgraded caller (still a reader) gets back a document it
  created and can read anyway. A caller with no role left gets 404 before any lookup. A revoked API client gets
  401 from the standings. A retry for another company never matches the hash. Replay hands out nothing beyond
  what the caller received, or could read, already. Tests:
  `test_a_retry_after_*` (`tests/integration/test_invoices_api.py:409-463`).
- **Paired-column check constraints.** `ck_invoices_seller_tax_id`, `_buyer_tax_id`, `_buyer_named`, `_void_reason`,
  `_functional` and `ck_invoice_taxes_functional` are in the migration and the models in the same words
  (`r0001_invoicing.py:115-135`, `tables.py:115-135`). The reader refuses a partial row
  (`repositories.py:201-225`).
- **Per-module stored-response limit.** `Idempotency(max_stored_body=...)` is validated and the platform default
  stays 1 MiB. invoicing sets 4 MiB (`context.py:21`), and a 1000-line answer over 1 MiB is stored and replayed
  byte for byte (`test_the_largest_document_…`). Side effects are under Minor (M6).
- **Faithful fake / `existing_id`.** The 409 schema declares `existing_id` (`invoices.py:69-96`), and the problem
  handler drops any member that would replace a standard one (`problems.py`, `_STANDARD_MEMBERS`).

### `2ab3369` (Task 6)

- **Can `document` read another tenant's invoice?** No, through invoicing. The tenant comes from the call's
  scope (`tenant_of`, `commands.py:113-118`). The argument is only an id looked up inside that tenant, by the
  repository's `WHERE` (`_mine`, `repositories.py:548-553`) and by RLS `tenant` on all four tables. Another
  company or mode raises `InvoiceNotFoundError`, which never names the id (`test_facade.py:104-118`).
- **Does accepting `SystemCall` widen access?** No. In-process code can build a `Call` with any scope just as
  easily as a `SystemCall`, so the trust boundary is the same: the API trusts its caller, and identity's does
  too. The consequence is for M3b: any fiscal route or renderer that exposes a view must ask for
  `invoicing.invoices.read` itself. The facade cannot.
- **`@facade`** guards `document`, and a call made inside an open unit raises `UnitOpenError` (tested).
- **Events** `issued` v1 `{kind, direction, corrects}` and `voided` v1 `{kind, direction}` carry no party, tax
  id, description or amount. They are scoped to the document's tenant with the mode, and `aggregate_version` is
  the document's version. They commit with the change. A draft, a 422/409/412 and a replay write no outbox row
  (`test_events.py`).
- **`tenant: Tenant`** on `InvoiceView` is always moded, as `Invoice.__post_init__` enforces.
- **`repr`s** of `InvoiceView`, `PartyView`, `SellerView`, `LineView`, `Invoice`, `Party` and `Seller` show ids,
  kinds, statuses, versions, masked tax ids and category keys only. invoicing writes no log line or span of its
  own. Problems never repeat client text (`test_a_refused_document_s_problem_never_repeats_what_was_sent`
  covers description, tax category, quantity, email, tax id, reference, field, establishment and unknown key).

### Money and tax, end to end

- Line gross `quantity × unit_price` is exact: at most 6 + 12 = 18 places, inside money's 24.
  `discount ≤ gross` is checked exactly. The base is `round_half_up` to the currency's minor unit, and
  Decision 8 makes that equal to the regime's places. Totals come only from the kernel's `TaxCalculator`. Any
  amount at or above 10^18 is `amount_out_of_range` and is never repeated.
- The rate snapshot's `since` is the newest entry on or before the tax point. This holds because
  `Timeline` enforces newest-first order (`ledger_kernel/dated.py`), but it repeats `Timeline.at`'s choice (M4).
- Functional totals: each category's base and tax and the net are converted once and rounded by the regime's
  rule. Tax is the sum of the converted categories, and gross is net plus tax, so `gross == net + tax` holds in
  both currencies. The sum of functional category bases can differ from the functional net when a line has two
  taxes. That is inherent, not a defect.
- The compounding gap (ITBIS on a price that includes ISC) is stated in `domain/pricing.py`'s docstring, in
  `TaxLine`, in the plan (Rules, Tasks 2-3) and in M3b Task 32 [LATER]. Until then a Dominican seller of goods
  subject to ISC cannot invoice them correctly. This is known and flagged for the owner.

## Findings

### Critical

None.

### Important

**I1. A note's dates are not tied to the invoice it corrects.**
Files: `domain/invoice.py:220-236` (`_require_correctable`), `domain/pricing.py:231-236`.

- **Scenario.** An invoice is issued 2026-10-19. A credit note against it sent with `issue_date: 2026-09-01` is
  accepted, so the note predates what it corrects.
- **Scenario.** A rate changes between the invoice and the note. The note's tax point defaults to its own issue
  date, so it credits at the new rate what was charged at the old one.
- **Scenario.** An invoice older than `MAX_TAX_POINT_AGE` (400 days) cannot be credited at its own tax point.
- **Fix.** Refuse a note whose `issue_date` is before the corrected invoice's (`CorrectionProblem.BEFORE_INVOICE`,
  422 `correction_invalid`). This is checked under the lock in `apply`, where the invoice is already read.
- **Owner decision needed.** Should a note's lines be priced at the corrected invoice's tax point by default, and
  is that one exempt from the 400-day bound? DGII's reference block of a note also needs the corrected invoice's
  date (see M3b below).

### Minor

**M1. "Only a new request has its body read" is not what happens.**
Files: `invoices.py:11-12`, plan "Rules decided in Task 5", second bullet.

- **Scenario.** `body: NewInvoice` is a FastAPI parameter, so the body is validated before the route runs. A
  viewer who posts a malformed body gets 422 `invalid_request`, not 403. Nothing leaks: the problem never echoes
  input, and the fingerprint needs the parsed body anyway. What does wait for the permission is the semantic
  reading (tax ids, text, `creation`).
- **Fix.** Say so in both places and pin the order with a test (viewer plus malformed body gives 422).

**M2. One `Idempotency-Key` cannot be reused across companies.**
Files: `ledger_platform/http/idempotency.py` (`_identity`), invoicing's `idempotency_keys` under
`MODED_ORGANIZATION`.

- **Scenario.** The key's row is (organization, mode, principal, route, key) and the company is only in the
  hash. An integrator acting for several companies with per-order keys gets 422 `idempotency_key_reused` when
  two companies' orders share a key. This is safe and follows §9.5's scope.
- **Fix.** State it in §9.5 and in the `Idempotency-Key` header's OpenAPI description ("unique per organization,
  mode and caller, whatever the company"), or add the company to the key's identity in a chassis v3.

**M3. Spec §3.4 still shows `document(self, call: Call, …)`.**

- **Fix.** Write `Call | SystemCall`, as built and as the plan's Task 6 rules say.

**M4. Duplicated or confusable code.**

- `type AnyCall = Call | SystemCall` is defined in both `ledger_identity/api.py:35` and
  `ledger_invoicing/api.py:62`. Move it to `ledger_platform.runtime.calls`.
- `_NO_FIELDS` is defined three times (`commands.py:52`, `facade.py:27`, `pricing.py:98`).
- Two different functions are named `seller_of`: `commands.py:121` (from `CompanyInfo`) and
  `repositories.py:219` (from columns). Rename the second `seller_from_columns`.
- `InvoiceView`, `LineView`, `LineTaxView`, `PartyView` and `SellerView` exist both in `api.py` (DTOs) and in
  `adapters/http/models.py` (wire models). Suffix the wire ones (`InvoiceBody`/`…Out`) before M3b adds a third
  mapping.
- `pricing.py:308` re-derives the entry `Timeline.at` chose. Add `Timeline.entry_at(day) -> Since[T]` to the
  kernel.
- `CompanyLookup` also answers establishments. `IdentityPort` names it better. It is cosmetic.

**M5. Production code that only tests call.**

- `InvoicingCommands.create/issue/void` and `_made` (`commands.py:138-148, 257-260`): the HTTP edge uses
  `prepare_*` only. They are a convenience for tests. Keep them as documented, or move them to a test helper.
- `uv run poe dead-src` flags only DTO fields (`livemode`, `functional_totals`), which are false positives.

**M6. The 4 MiB answer limit.**

- Each key can hold about 4× its request for 7 days, and every retry reads the whole row twice (`replayed`,
  then `once` if the first missed). This is acceptable behind the 1 MiB body limit and the rate limit.
- Revisit it if M3b's re-read replay (its Decision 23) makes the stored body unnecessary: it could store only
  the id.

**M7. Wording.**

- `LineTaxView.amount`'s description, "rounded as the regime rounds a category" (`models.py:221`), is untrue for
  a regime that rounds only per document or per line. In those regimes the per-line amount is unrounded or
  line-rounded.
- The base is always rounded `HALF_UP` (Decision 4), whatever the regime's `RoundingRule.mode`. That is fine for
  DO. Flag it for the owner with Decision 8.

**M8. Authorization window.**

- It is stated in the plan (Task 5 rules): no membership-event recheck, and the window is milliseconds.
- The replay path lengthens nothing: it only returns stored answers.
- Nothing to change now. When `identity.membership.revoked` is consumed anywhere, a cache of permissions must
  not be added to invoicing without it.

## Layering, codes, health

- **Error codes.** Every invoicing code is unique across the workspace. The only duplicated literals are in
  test fixtures, and devtools' error-code check is part of the gate.
- **Import contracts.** Sync added the `api` layer to `.importlinter`. `api.py` imports domain and platform types
  only, and the facade is in `application/`, as §3.4 asks.
- **Tests.**
  - Fixed clocks (`NOW`) are used throughout, and nothing sleeps on the wall clock.
  - The four lock-order tests (`test_repositories.py:555-639`) wait on `pg_stat_activity` lock waits of the
    test role, within `eventually`'s 5 s. Packages run serially, so a false positive is unlikely. On a heavily
    loaded machine the 5 s could be tight. Watch them before raising the patience.
  - invoicing's suite takes about 78 s, most of it the API integration tests.

## What M3b must adapt (Tasks 12 and 20)

1. **Assumption B1 vs as built.**
   - `tenant: Tenant` replaces `organization`, `company` and `mode`.
   - `kind` replaces `type`.
   - Line taxes have no `rate_key`: the category plus `since` name the rate.
   - `fields` is namespaced `{"do": {...}}`.
   - The view adds `regime`, `created_at`, `updated_at`, `issued_at`, `voided_at`, `void_reason` and
     `livemode`.
   - `discount`, `quantity` and `unit_price` are `Decimal`; `gross`, `base` and amounts are `Money`.
   - B6 (establishment code and time zone) is satisfied.
2. **Task 12 "build the InvoiceView the unit will store" has no seam yet.** `prepare_create` builds the
   `Invoice` inside `apply`, with the unit's `now` and the corrected invoice read under lock. Task 12 must split
   this:
   - build the prospective `Invoice` (or its view) outside the unit, from `Opening`, `Pricing`, `Seller` and the
     clock, with times replaced by the unit's;
   - recheck the correction rules under the lock, as now.

   A draft's view has `seller=None`, so `check` must run on the issued shape, and so must `/issue`, which
   reprices.
3. **Replay re-read (its Decision 23).** The stored body then becomes only a pointer. Keep the replay ahead of
   the specific permission, as this branch does. The re-read is a read, which the reader dependency already
   allows, so the exposure analysis above still holds.
4. **The mirror replaces `accepted_by`.** `PgInvoices` refuses any document with `accepted_by`
   (`UnmirroredAcceptanceError`, `repositories.py:534-539`). Task 12 must derive acceptance from
   `fiscal_statuses` inside the void's unit, after the invoice lock. It must also drop or replace the
   `accepted_by` field and the defect error.
5. **Country fields.** `_country_fields` (`pricing.py:369-387`) refuses undeclared keys (B4). Task 12's
   Decision 24 moves that decision to the chain, so `price` must accept and keep them.
6. **Corrections need the corrected invoice's data.** `InvoiceView.corrects` is an id. `fiscal_draft` needs the
   corrected invoice's issue date and its authority number, from a second `document()` call plus the mirror. The
   DO modification code (the reason a note corrects) has no field yet. See also I1.
7. **Channel limits are tighter than invoicing's.**

   | Field | invoicing | DO e-CF |
   |---|---|---|
   | quantity places | 6 | 2 |
   | unit price places | 12 | 4 |
   | integer digits | 18 | 16 |
   | description / item name | 500 | 80 |
   | legal name | 300 | 150 |
   | address | 500 | 100 |

   `channel.check` must turn these into `invalid_document` objections. Document them for POS integrators, who
   will only see them once enrolled.
8. **Authorization at fiscal's own edges.** The facade trusts its call, so fiscal's representation route and any
   view it exposes must require `invoicing.invoices.read` itself.
9. **`apply_fiscal_status(call: SystemCall, …)`** can reuse `tenant_of` (it already accepts `SystemCall`) and
   `@facade`.

## Gate

`uv run poe check` at `2ab3369`: **green** (exit 0, 8 min wall clock on the loaded machine, first run, no
flake). Every static step passed: format, lint, suppressions, both type checkers, import contracts, deps,
spelling, proto-lint, devtools, dead code. Every package reached 100 % branch coverage. Test counts: invoicing
243 (78 s), identity 438, platform 1402, DO 948, kernel 619, devtools 149 (363 s, the slowest).
