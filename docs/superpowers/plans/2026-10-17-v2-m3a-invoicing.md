# v2-M3a: thin invoicing (commercial documents) — plan and decisions

Status: executing, 2026-10-17 (the owner asked for the application to be finished; this slice is the first of
M3 and ends with the gate green and the owner's review, as §16.3 asks of every milestone).

**Goal.** A company's commercial documents: an `invoicing` module with the `Invoice` aggregate (invoice, credit
note, debit note; sales or purchase direction), its lines with a resolved tax snapshot, totals computed with the
kernel's `TaxCalculator` of the company's regime, a business key (`external_reference`), the state machine
`draft → issued → voided`, events `invoicing.invoice.issued` / `voided`, its HTTP API under `/v1/invoices`, and
its in-process `InvoicingApi`. No fiscal channel yet: an issued invoice has `fiscal: []` (§8.5, the fiscal
module and the `IssuePolicy` chain arrive with M3b). Spec: §7.5 (documents), §7.4 (money), §9 (API), §6 (data),
§3.4 (in-process APIs). It follows identity v2 and takes nothing it did not build: the company, its fiscal
profile and its establishments come through `IdentityApi`.

## Decisions (the spec does not fix these; the owner reviews them)

1. **Company**: every route takes the company in `Ledger-Company: cmp_…` (§9.7, the ISV seam), through
   `company_in_header`; a caller with no role in it gets 404, a role without the permission 403.
2. **Permissions** (manifest, roles per §7.7): `invoicing.invoices.read` (owner, admin, accountant, issuer,
   viewer), `.create` and `.issue` (owner, admin, accountant, issuer), `.void` (owner, admin, accountant).
3. **Create**: `POST /v1/invoices` (`Idempotency-Key`, `Ledger-Mode`) issues at once by default (the POS path of
   §8.5); with `"draft": true` it stores a draft. `POST /v1/invoices/{id}/issue` issues a draft. A draft is
   replaced, not edited: no `PATCH` in this slice (a draft that is wrong is voided by deleting it: out of scope;
   drafts are purged by a schedule, as the spec's manifest example shows, in a later slice).
4. **Lines**: `description` (1-500), `quantity` > 0 (≤ 6 places), `unit_price` ≥ 0 (≤ 12 places), `discount` ≥ 0
   (an amount, ≤ the gross of the line), `taxes`: one or more category keys of the regime (a tax-free line names
   its regime's exempt category: `TaxLine` needs at least one). A line's taxable base is
   `round_half_up(quantity × unit_price − discount)` to the currency's minor unit (stated, not implied).
5. **Money**: amounts are strings on the wire; the invoice's currency defaults to the company's functional
   currency; another currency requires `exchange_rate` (> 0, ≤ 12 places, units of functional per one of the
   invoice's) and the functional totals are computed once, rounded as the regime says, and stored.
6. **Tax point and dates**: `issue_date` defaults to today in the establishment's time zone (the company's
   `MAIN` unless `establishment` names another), `tax_point` to the issue date (I11/rule 12: rates are chosen by
   the tax point, never by `now()`). Both are dates, not instants.
7. **Parties**: the seller is the company's snapshot at issue (legal name, primary tax id); the buyer is an
   inline snapshot `{tax_id?, legal_name, address?, email?}` (§7.2). Tax ids are qualified `{scheme, value}`,
   read with the installed regimes' schemes, masked in every log, span and error.
9. **Business key**: `external_reference` (1-100, optional) is unique per `(company, mode, type)` forever; a
   duplicate answers 409 `external_reference_taken` carrying `existing_id`.
10. **Corrections**: a credit or debit note names the invoice it corrects in `corrects` (an issued invoice of the
    same company and mode; the regime's document types decide nothing yet: no `ecf_type` field exists in the DO
    regime).
11. **Void**: only an `issued` invoice, with an optional reason; refused (409 `invoice_not_voidable`) when any
    channel accepted it (the mirror is empty in this slice, so the rule is stated and unreachable until M3b).
12. **Country fields** `fiscal.<regime>.<field>`: accepted only for fields the company's regime declares; any
    other key is 422 `field_unknown` (§9.8). The DO regime declares none yet.
13. **Facade**: `InvoicingApi.document(call, invoice)` (for fiscal) in `api.py`, bound in `application/facade.py`;
    `apply_fiscal_status` and `IssuePolicy` arrive with `fiscal`.

## Tasks (each ends with `uv run poe check` green, one commit)

1. Plan and skeleton: this file; `modules/invoicing` (manifest, README, layers), `uv sync`, devtools sync.
2. Domain: money lines, tax snapshots, totals, parties, the aggregate and its state machine (pure; unit and
   property tests).
3. Application: create / issue / void use cases and ports, with the in-memory fake of the module's tests.
4. Persistence: schema `invoicing` (tables, RLS tenant scope, chassis v1 + v2), repositories, migrations.
5. HTTP: `/v1/invoices` router, models, OpenAPI snapshot, idempotency, TypeIDs, `Ledger-Company`.
6. In-process API and events: `InvoicingApi`, `invoicing.invoice.issued/voided`.
7. Review pass: security (RLS, tax ids in messages), money edge cases, docs, review file.

## Out of scope (named so they are not mistaken for omissions)

Fiscal routing, numbering, signing, channels (M3b); editing drafts; credit-note balance rules; the printed
representation; PDF; webhooks; the `parties` module; `ecf_type` and the DO channel's fields.
