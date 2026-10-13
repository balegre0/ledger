# 0008. Path major plus dated API versions, TypeID public ids, thin webhooks

- Status: Proposed
- Date: 2026-10-13
- Spec: `docs/superpowers/specs/2026-10-13-ledger-platform-v2-design.md` §9

## Context

The public API's shape is the most expensive thing to change once clients exist. Stripe has shipped about a
hundred breaking changes under `/v1` with pinned dated versions and backward response transforms; retrofitting
pinning after clients exist is the costly part. The public id format is a breaking change on every object.

## Decision

- `/v1` in the path is the major. The header `Ledger-Version: YYYY-MM-DD` selects a dated version; organizations
  and API clients are pinned to the current version when created; a request may override it.
- The `ApiVersions` registry, backward response transforms, forward request transforms and the generated
  changelog ship now with zero changes registered. At most two breaking releases a year.
- Public ids are TypeIDs (`inv_` plus the base32 UUIDv7), prefixes declared in manifests; UUIDs inside.
- Every resource has `id`, `object`, `livemode`, `created`, `updated`, `metadata`; country fields live in
  `fiscal.<regime>` sub-objects; `GET /v1/countries/{cc}` publishes country rules as data.
- Webhooks use Standard Webhooks signatures and thin, unversioned payloads, so versions never break receivers.

## Consequences

- Positive: breaking changes without a `/v2`; debuggable, typed ids; webhook receivers decoupled from versions.
- Negative: transform machinery maintained from day one (small while empty).
