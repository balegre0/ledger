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
   the tax point, never by `now()`). Both are dates, not instants. The issue date is never after the
   establishment's today (422 `invalid_issue_date`); the tax point lies on the issue date or at most 400 days
   before it (422 `invalid_tax_point`; `MAX_TAX_POINT_AGE`, provisional until a regime states how late a supply
   may be invoiced). "Today" comes from the clock before any unit opens; the times a unit records are its
   own, so a document priced a moment before midnight may record a time of the next day.
7. **Parties**: the seller is the company's snapshot at issue (legal name, primary tax id); the buyer is an
   inline snapshot `{tax_id?, legal_name, address?, email?}` (§7.2). Tax ids are qualified `{scheme, value}`,
   read with the installed regimes' schemes, masked in every log, span and error.
8. **Currency and rounding** (added at the Task 2-3 review): the kernel's calculator rounds to the regime's
   `RoundingRule.places` whatever the currency, so a document whose currency (or whose company's functional
   currency) has other minor units than those places is refused (422 `currency_not_supported_by_regime`: JPY's
   0, KWD's 3 under DO's 2) until a regime declares how it rounds them. **For the owner (v2-M3):** whether the
   kernel should round at `min(places, minor_units)` or a regime should declare per-currency rounding.
9. **Business key**: `external_reference` (1-100, optional) is unique per `(company, mode, type)` forever; a
   duplicate answers 409 `external_reference_taken` carrying `existing_id`. It is kept exactly as sent and
   refused with white space around it, an invisible character, or in a form other than Unicode NFC.
10. **Corrections**: a credit or debit note names the invoice it corrects in `corrects`: an issued invoice (not
    a note) of the same company, mode and direction, in the note's currency (422 `correction_invalid`); the
    regime's document types decide nothing yet (no `ecf_type` field exists in the DO regime). Whether a document
    names one at all is checked first, before identity is asked (422 `correction_unexpected` /
    `correction_required`).
11. **Void**: only an `issued` invoice, with an optional reason; refused (409 `invoice_not_voidable`) when any
    channel accepted it (the mirror is empty in this slice, so the rule is stated and unreachable until M3b),
    and while a draft or issued note corrects it. Creating or issuing a note locks the invoice it corrects, and
    a void locks the invoice before it asks for its notes, so the two serialize (lock order: a note before its
    invoice).
12. **Country fields** `fiscal.<regime>.<field>`: accepted only for fields the company's regime declares; any
    other key is 422 `field_unknown` (§9.8). The DO regime declares none yet. Values are bounded (422
    `field_invalid`): text up to 500 characters and codes up to 100, none invisible; decimals below 10^18 with
    at most 12 places; objects of at most 20 text fields named in lower case, never nested.
13. **Facade**: `InvoicingApi.document(call, invoice)` (for fiscal) in `api.py`, bound in `application/facade.py`;
    `apply_fiscal_status` and `IssuePolicy` arrive with `fiscal`.

## Rules the decisions did not fix, decided in Tasks 2-3 (the owner reviews them)

- **Lines**: a discount has at most 12 decimal places, as a unit price; a document has 1 to 1000 lines; a line
  names one category of each tax (`<regime>.<tax>.<category>`), and taxes on one line do not compound (the
  kernel's `TaxLine` cannot express ITBIS charged on a price that includes ISC: no regime declares such a tax).
- **Parties**: the buyer is optional (a consumer sale names none); its email is at most 254 characters.
- **Same-currency rate**: an `exchange_rate` sent with a document in the functional currency is 422
  `exchange_rate_unused`.
- **Drafts** are priced when made and priced again when issued, on the day they are issued, with the seller as
  the company is then; a draft issued by another call meanwhile answers 409 `invoice_not_issuable`, one
  changed otherwise 409 `invoice_changed`.

## Rules decided in Task 4 (persistence; the owner reviews them)

- **One revision**: schema `invoicing` is made by one revision `0001` (the chassis's versions 1 and 2, then the
  four tables of a document and their `tenant` policies): Task 1's two chassis revisions are squashed into it,
  as identity's were, since nothing was released (§6.5). A database migrated from Task 1's `0002` is recreated.
- **Tables**: `invoices` (terms as sent, pricing, parties, totals, functional totals), `invoice_lines`,
  `invoice_line_taxes` (the rate resolved at the tax point) and `invoice_taxes` (the totals' categories, with
  the functional amounts). Every key leads with `(organization_id, company_id, mode)` and every foreign key
  carries it; rows keep their place (`position`, `place`), never a collation's order; amounts are `numeric`
  with their own scale. A change rewrites the lines and totals with the row (issuing a draft prices it again).
- **`accepted_by` is a tuple** of channel keys, not a set: the audit hashes a document as JSON
  (`ledger_platform.data.codec`), which has no sets (the in-memory fake never encoded one). Until the fiscal
  mirror exists (M3b) the repository refuses to keep a document a channel accepted
  (`invoice_acceptance_unmirrored`, a defect) rather than drop it silently.

## Tasks (each ends with `uv run poe check` green, one commit)

1. Plan and skeleton: this file; `modules/invoicing` (manifest, README, layers), `uv sync`, devtools sync.
2. Domain: money lines, tax snapshots, totals, parties, the aggregate and its state machine (pure; unit and
   property tests).
3. Application: create / issue / void use cases and ports, with the in-memory fake of the module's tests.
4. Persistence: schema `invoicing` (tables, RLS tenant scope, chassis v1 + v2), repositories, migrations.
   `Invoices.add` inserts with `ON CONFLICT DO NOTHING` on the business key's unique index and then selects the
   holder's id (never a caught unique violation, which would abort the unit's transaction). A repository test
   proves on PostgreSQL that a void of an invoice and the creation (and the issue) of a note correcting it
   serialize on the invoice's row lock, which the in-memory fake cannot.
5. HTTP: `/v1/invoices` router, models, OpenAPI snapshot, idempotency, TypeIDs, `Ledger-Company`.
6. In-process API and events: `InvoicingApi`, `invoicing.invoice.issued/voided`.
7. Review pass: security (RLS, tax ids in messages), money edge cases, docs, review file.

## Out of scope (named so they are not mistaken for omissions)

Fiscal routing, numbering, signing, channels (M3b); editing drafts; credit-note balance rules; the printed
representation; PDF; webhooks; the `parties` module; `ecf_type` and the DO channel's fields.
