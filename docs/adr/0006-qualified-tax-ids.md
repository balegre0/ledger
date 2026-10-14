# 0006. Qualified tax identifiers and one global registry of active taxpayers

- Status: Proposed
- Date: 2026-10-13
- Spec: `docs/superpowers/specs/2026-10-13-ledger-platform-v2-design.md` §7.3

## Context

v1's `TaxId` was "RNC or cédula" in the kernel, stored as `String(11)`, with a global unique index on the bare
value: an RNC and another country's id with the same digits would collide. DGII has issued numbers that fail
its own check-digit algorithms (python-stdnum keeps hundreds of such cédulas).

## Decision

- `TaxId(scheme, value)` with scheme keys `<cc>.<name>` (`do.rnc`, `do.cedula`, `mx.rfc`, `es.nif`), declared by
  regimes as `TaxIdSchemeDef` with normalization, a check returning `valid | checksum_mismatch |
  invalid_format`, and `check_digit_enforced`.
- `<cc>` is one assigned ISO 3166-1 country, never a group of countries: there are no multi-country scheme
  groups. EU VAT ids are issued per member state, so each is its own scheme of that country's regime (`es.nif`,
  `fr.tva`, …), not one `eu.vat`. The kernel refuses a scheme key whose `<cc>` is not an assigned country.
- A format error refuses; a checksum mismatch refuses only where the scheme enforces it (not for `do.rnc` or
  `do.cedula`) and is otherwise reported as a warning.
- A company holds one or more tax ids. Identity's `tax_registrations` keeps `(scheme, normalized value)` unique
  among active companies across organizations: the only cross-organization uniqueness, and the future cell
  router's table. Registering a held id answers 409 `tax_id_taken` and nothing more.
- Values are masked everywhere except inside the database and internal events; never sent in webhooks.

## Consequences

- Positive: any country's ids without schema changes; the receiver routes by tax id unambiguously.
- Negative: clients always send the scheme.
