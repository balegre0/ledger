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
  zone, tax-id schemes, tax categories, document types, rounding, law-level fields, dated parameters, sources;
  it imports the kernel only) and one or more **channels** keyed `<cc>-<format>-v<N>` (builders, vendored
  schemas, signing profile, gateway, numbering, status map, explicit state machine, contingency, delivery,
  printed representation, receiver routes, requirements, format-level fields). Dev-only fakes live in a separate
  distribution (`ledger-country-<cc>-testing`).
- At startup the runtime loads the entry points `ledger.countries.regimes` (`Regimes`, in
  `platform.runtime.registries`) and `ledger.countries.channels` (`Channels`, in `platform.runtime.packs`, which
  also loads `ledger.modules`: packs name the contexts they assemble with), validates and freezes them. Modules never import countries; countries
  never import modules or each other; channels receive ports (`SignerPort`, `CompanyDirectory`, `ArchivePort`)
  through a `ChannelContext` built by the runtime from what modules provide.
- `fiscal-kit` holds only the contract between `fiscal` and channels: `Channel`, `FiscalDraft`, `FiscalStatus`,
  `StatusMap`, the kit events, hardened XML helpers and the ports above plus `NumberingPolicy` (authorized
  ranges with an optional opaque artifact, or free series per establishment). Numbering allocation, submission
  loops, the circuit breaker and the conformance harness stay in the first channel until a second one needs them
  (rule of two). The kit never generalizes an authority's states.
- Country fields share the wire namespace `fiscal.<regime>.<field>`: the regime declares law-level fields,
  channels declare format-level ones; requests are validated against the company's enrolled channels.
- The pure regime schema types and the one regime-parameterized tax calculator live in the kernel (stdlib only);
  invoicing computes totals with it and channels verify with it. **Accepted deviation from R3** (the kernel was to
  hold generic value types only), accepted by the controller on the owner's behalf; if the owner vetoes, the same
  contents move to a `ledger-regime-sdk` package.
- Quality tiers (Bronze, Silver, Gold) per channel are declared in the manifest; live mode needs Silver plus the
  authority's authorization evidence; the automated rule catalogue is deferred.

## Consequences

- Positive: a new country or format version is a new package or channel, with no module edits.
- Negative: two registries and a small kit to maintain; logic can accumulate in packs (review rule: logic two
  packs need moves into the kit).
