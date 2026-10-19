# ledger-invoicing

Commercial documents: invoices, credit and debit notes, their lines and totals (spec §7.5; plan
`docs/superpowers/plans/2026-10-17-v2-m3a-invoicing.md`).

- Owns schema `invoicing` (one revision, `0001`: the chassis's tables and a document's four, under the tenant's
  row-level security) and id prefix `inv`, and declares the `invoicing.invoices.*` permissions.
- Depends on `identity` (the company, its establishments and what a caller may do there, through
  `IdentityApi`).
- HTTP: `/v1/invoices` (`adapters/http`): create (`Idempotency-Key`, `Ledger-Mode`), list, read, issue and void
  (`If-Match`), the company in `Ledger-Company`. Its OpenAPI snapshot is `openapi.json` (`uv run poe sync`).
- Assembly: `assembly.py` (`MODULE`, entry point `ledger.modules: invoicing`).
