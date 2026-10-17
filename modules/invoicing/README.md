# ledger-invoicing

Commercial documents: invoices, credit and debit notes, their lines and totals (spec §7.5; plan
`docs/superpowers/plans/2026-10-17-v2-m3a-invoicing.md`).

- Owns schema `invoicing` and id prefix `inv`, and declares the `invoicing.invoices.*` permissions.
- Depends on `identity` (the company, its fiscal profile and its establishments, through `IdentityApi`).
- Assembly: `assembly.py` (`MODULE`, entry point `ledger.modules: invoicing`).
