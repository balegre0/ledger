# ledger-invoicing

Commercial documents: invoices, credit and debit notes, their lines and totals (spec §7.5; plan
`docs/superpowers/plans/2026-10-17-v2-m3a-invoicing.md`).

- Owns schema `invoicing` (one revision, `0001`: the chassis's tables and a document's four, under the tenant's
  row-level security) and id prefix `inv`, and declares the `invoicing.invoices.*` permissions.
- Depends on `identity` (the company, its establishments and what a caller may do there, through
  `IdentityApi`).
- HTTP: `/v1/invoices` (`adapters/http`): create (`Idempotency-Key`, `Ledger-Mode`), list, read, issue and void
  (`If-Match`), the company in `Ledger-Company`. Its OpenAPI snapshot is `openapi.json` (`uv run poe sync`).
- In-process API (`api.py`, §3.4): `InvoicingApi.document(call, invoice) -> InvoiceView`, bound under
  `INVOICING_API` by `application/facade.py` (`@facade`: it opens its own unit, never inside the caller's). The
  call names the tenant read (a company in a mode: a request's `Call` or a `SystemCall`); another tenant's
  document is `InvoiceNotFoundError`. A view carries the document as kept, tax ids in clear (inside the tenant's
  boundary, §7.3); its `repr` shows its id, kind, status and version, a party's its tax id masked.
  `apply_fiscal_status` and the `IssuePolicy` chain arrive with `fiscal` (M3b).
- Events (`events.py`, through the outbox in the unit of the change): `invoicing.invoice.issued` v1 `{kind,
  direction, corrects}` and `invoicing.invoice.voided` v1 `{kind, direction}`, scoped to the document's
  organization, company and mode, subject the document, `aggregate_version` its version. A draft, a refused
  request and a replay announce nothing; payloads carry no party, tax id or amount.
- Assembly: `assembly.py` (`MODULE`, entry point `ledger.modules: invoicing`).
