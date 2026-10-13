# 0005. Countries as regime and channel packs registered by entry points

- Status: Proposed
- Date: 2026-10-13
- Spec: `docs/superpowers/specs/2026-10-13-ledger-platform-v2-design.md` §8

## Context

v1 put the Dominican tax id in the kernel and planned one service per country. GOBL, Odoo, OCA Brazil, Tryton
and Peppol show that tax law and authority formats change on different calendars, that one country often has
several formats (Spain has four), and that generic EDI state machines fail (Odoo's first EDI framework).

## Decision

- `countries/<iso2>/` is one package holding a **regime** (pure typed, effective-dated data: currency, time
  zone, tax-id schemes, tax categories, document types, rounding, extension fields, sources; it imports the
  kernel only) and one or more **channels** keyed `<cc>-<format>-v<N>` (builders, vendored schemas, signing
  profile, gateway, numbering, status map, explicit state machine, contingency, receiver routes, requirements).
- Registries load the entry points `ledger.countries.regimes` and `ledger.countries.channels` at startup,
  validate and freeze them. Modules never import countries; countries never import modules or each other;
  channels get what they need through fiscal-kit ports bound by the apps.
- `fiscal-kit` generalizes plumbing (drafts, numbering ranges and voids, submission steps, circuit breaker,
  status-map exhaustiveness, hardened XML, conformance suites), never an authority's states.
- One regime-parameterized tax calculator in the kernel computes totals for invoicing; channels verify with it.
- Quality tiers (Bronze, Silver, Gold) per channel are declared in the manifest and checked by devtools.

## Consequences

- Positive: a new country or format version is a new package or channel, with no module edits.
- Negative: two registries and a kit to maintain; logic can accumulate in packs (review rule: logic two packs
  need moves into the kit).
