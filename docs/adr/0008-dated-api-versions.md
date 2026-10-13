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
  and API clients are pinned to the current version when created. Precedence: request header, then API client,
  then organization. The response echoes the resolved version.
- Built now: header parsing, pin storage, precedence and echo. The transform registry (backward response and
  forward request transforms, generated changelog) is built with the first breaking change. At most two breaking
  releases a year.
- Public ids are TypeIDs (`inv_` plus the base32 UUIDv7), prefixes declared in manifests; UUIDs inside.
- Every resource has `id`, `object`, `livemode`, `created`, `updated`, `metadata`; country fields live under
  `fiscal.<regime>.<field>` in requests and in per-channel `fiscal[]` entries in responses;
  `GET /v1/countries/{cc}` publishes country rules as data.
- Webhooks use Standard Webhooks signatures and thin, unversioned payloads with two-segment public names
  (`<resource>.<verb>`), so versions never break receivers.

## Consequences

- Positive: breaking changes without a `/v2`; pins exist before the first client; debuggable, typed ids; webhook
  receivers decoupled from versions.
- Negative: the transform machinery is still to build when the first breaking change comes.
