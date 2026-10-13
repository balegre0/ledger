# 0007. Test and live modes as a tenancy dimension

- Status: Proposed
- Date: 2026-10-13
- Spec: `docs/superpowers/specs/2026-10-13-ledger-platform-v2-design.md` §6.5, §7.1, §9.7

## Context

Companies certify with DGII (`TesteCF`, `CerteCF`) while others, or the same company later, emit in production
(`eCF`). v1 configured the environment per company, so certification and production documents shared tables,
ids and queries. Stripe separates test and live data by credential.

## Decision

- Every moded row carries `mode` (`test` | `live`); RLS includes `ledger.mode`; business keys include it.
- Company and memberships are shared across modes; enrollments, certificates, ranges, documents, webhook
  endpoints, idempotency keys and API clients belong to one mode.
- API clients are bound to a mode; human tokens name it with the `Ledger-Mode` header on moded routes.
- Channels map modes to authority environments (`do-ecf-v1`: test to `testecf` or `certecf`, live to `ecf` only).
- Every resource exposes `livemode`.

## Consequences

- Positive: certification data cannot leak into production queries; clients integrate safely in test mode.
- Negative: one more column and predicate on every moded table; human interfaces must choose a mode.
