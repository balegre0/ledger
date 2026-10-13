# 0001. Modular monolith with five deployables

- Status: Proposed
- Date: 2026-10-13
- Spec: `docs/superpowers/specs/2026-10-13-ledger-platform-v2-design.md` §3

## Context

v1 planned seven microservices (identity, catalog, invoicing, fiscal-do, fiscal-receiver, vault, webhooks) and a
new service per country. The team is small and the product must grow along two axes, countries and features.
Shopify, GitLab, Kraken, Stripe and Instagram run modular monoliths at hundreds of engineers; Segment's
per-destination services grew operations linearly with integrations; no case shows a small team succeeding
because of microservices. Our hardest invariants (no duplicated or skipped e-NCF) are transactional and cheaper
inside one process.

## Decision

One codebase organized as modules (bounded contexts), each with a public `api.py` (Protocols and DTOs) and
`events.py`, enforced by generated import-linter contracts. Five deployables from one image: `api`, `worker`,
`signer`, `receiver`, `migrate`. Modules call each other in process through their API Protocols and publish
facts as events. A module is extracted only for a runtime reason (security isolation, scaling or latency
profile, data residency, failure isolation); extraction swaps transports, never domain code.

## Considered options

- Keep v1's microservices: rejected (operations grow with countries; distributed sagas for transactional rules).
- One flat application without enforced boundaries: rejected (it decays into an import web).
- Modular monolith with enforced boundaries: chosen.

## Consequences

- Positive: one deploy, one lock, one trace path; no network hops between modules; boundaries checked on every PR.
- Negative: weaker fault isolation between modules sharing a process; discipline enforced by tools, not networks.
- Follow-ups: ADR 0002 (events), 0004 (data), 0009 (workspace), 0010 (the signer, the first separate process).
