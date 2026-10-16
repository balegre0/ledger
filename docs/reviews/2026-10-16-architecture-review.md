# Architecture and code review, 2026-10-16

Six reviewers read the repository by area (data and jobs, events and runtime, HTTP and auth, kernel and identity,
the Dominican pack, tooling and deployment) against the spec and the ADRs. This file records what they found and
what became of each finding, so the reasoning outlives the conversation. The commit subjects name the changes.

**State of the repository at the time.** Only v2-M1 is built: the chassis, identity (still v1 code in the new
layout) and a Dominican pack that is closer to a spike than to a channel. `signer`, `invoicing`, `fiscal`,
`fiscal-kit`, the receiver, numbering, the DGII circuit breaker, contingency and the Helm chart are not yet.

## Fixed

| Finding | Change |
|---|---|
| A wake or enqueue that arrives while a job runs is lost, so it can park for ever | `fix(jobs): a wake that arrives while a job runs is not lost` |
| The idempotency fingerprint ignores the company when it comes from `Ledger-Company` | `fix(http): the company a request acts for is part of its idempotency fingerprint` |
| Issuer and buyer RNC written to an INFO log line | `fix(do): the default seed audit masks the tax ids it logs` |
| No request body limit (spec §10 asks for 1 MiB) | `feat(http): refuse a request body over 1 MiB with 413 …` |
| No rate limit (spec §10 asks for a per-replica backstop) | `feat(http): an in-process GCRA rate limit per credential …`, then the rounding fix |
| gRPC docstring leaves transport security to a mesh the spec defers | `docs(grpc): say what protects a service until a mesh exists` |
| Purges are one `DELETE` under the statement timeout | `fix(jobs): purge expired keys and done jobs in short batches` |
| A hung handler holds its slot and renews its lease for ever | `feat(jobs): a job handler has a timeout of its own …` |
| A tax line carries one category, so stacked taxes double-count the base | `feat(kernel): a tax line may be taxed in several categories` |
| A document parked past its range's expiry is still signed | `fix(do): never sign a document after its number's range expired` |
| One extension implementation per point makes modules fight over a hook | `feat(runtime): extension points are chained …` (ADR 0011) |
| A public caller's `baggage` is carried to outbound calls | `fix(http): a public caller's baggage goes with its trace` |
| `test_two_migrations_at_once_serialize` raced on alembic's process-global `op` | `test: serialize migrations through the database lock, not two threads` |
| A company holds one tax id and no jurisdiction or currency; two organizations could register one taxpayer | `feat(identity): …` fiscal profile, qualified tax ids, the global `tax_registrations` registry |
| Branches and emission points are Dominican shapes in identity | `feat(identity): establishments replace branches and emission points` (code, name, time zone; `MAIN` from registration; the company's regime gives its zone). Spec §6.3 listed establishments under the *company* RLS scope; they are under the *organization* scope like memberships and tax ids, because registering a company writes its `MAIN` in the unit that creates the company, before a company scope exists. The spec line is amended; flagged for the owner. |

## Recorded in the spec as limits of what is built

- Consumers handle their event inside a unit, so a facade call from one raises `UnitOpenError`, although §8.5
  shows consumers calling `InvoicingApi`. An "outside" consumer style is needed before the first such consumer.
- Subject order holds only while the fan-out is healthy: a skipped or set-aside row lets later events of its
  subject pass. The fix is written down for the first ordered consumer.

## Decided not to change, and why

- **Move `Role`, `PermissionDeniedError` and `CompanyNotFoundError` out of the kernel.** Identity's `domain` may
  import only the kernel and raises them, so the layering puts them there. The closed list of permission verbs is
  deliberate (spec §4.4).
- **Rename `docs/superpowers` and drop the tracked plans.** The restructure plan records that the owner asked
  for the plans under `docs/superpowers/plans/` and for them to stay tracked.
- **Extract `fiscal-kit` now.** The first channel is its only user; the spec's own rule of two (ADR 0005,
  §16.4) puts the generalization at the second channel. Extracting early would move tests and manifests for no
  second user.
- **A `flow` package kind in devtools.** ADR 0011 stays Proposed for it: it touches the manifest model, devtools
  and the import-linter generator, and no flow exists to use it. The chained extension points, which a module
  needs soon (`IssuePolicy`), are built.
- **A tolerant lease renewal.** A reviewer feared `enqueue` holds the running row's lock for seconds. Writes of a
  unit are flushed at commit, so the lock lasts milliseconds. The existing decision (one failed renewal means the
  lease cannot be vouched for) stands.
- **Channel verifies totals against the kernel calculator.** It needs the `FiscalDraft` of v2-M3, which does not
  exist; the check would have no caller.
- **Bumping the routing version only when subscriptions changed.** The spec (§3.5, §6.5) bumps it after every
  migration on purpose, so unrouted events are tried again.

## Open

| Finding | Why it waits |
|---|---|
| The deadline sweep of the job claim reads every due row of the kind, and `deadline_at` is in no index | Needs `chassis_schema_v2` and a revision in every schema |
| The done-job purge has no index on `updated_at` | Same |
| Idle polling grows with kinds × replicas; wake-ups reach every runner | The spec's one-probe `UNION ALL` is not built |
| Inbox claims a batch of 20 under one 60 s lease and handles it one row at a time | Needs a per-consumer concurrency and lease design |
| Metrics for queue depth, oldest event and dead rows; the `ledger-worker replay` command | Not built yet; the spec lists them |
| `Settings` refuses unknown `LEDGER_<SECTION>_*` variables, which can crash old pods during a rollout | Needs a decision on the rollout contract |
| Identity: tax-id uniqueness is an index, not the `tax_registrations` table the spec describes; no establishments, TypeIDs or `IdentityApi` | Planned as v2-M2; redo `r0001` then, since nothing is in production |
| DGII client: no circuit breaker, no negative caching of authentication, retries after 429 ignore `Retry-After` | v2-M3 |
| CI is one serial job; `.importlinter` grows quadratically with packages; no dependency, image or migration scanning | See the tooling review; split CI before the next modules |

## Where the gate stands in this environment

`uv run poe check` passes every static step. Three results come from the sandbox, not the repository:

- `countries/do/tests/integration/test_signing_xmlsec1.py` (13 errors) and two tests of `countries/do/testing`:
  the xmlsec1 image build cannot resolve `deb.debian.org` from inside a container.
- `packages/testing` sits at 99.72 % because `socket.has_dualstack_ipv6()` is false here, so one branch of
  `occupy_a_port` is not taken.
