# v2-M1-lite: Restructure and Chassis-lite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the repository to the v2 tree (`packages/`, `modules/`, `countries/`, `apps/`, `contracts/`, `tools/devtools`) with its git history, and replace the NATS-based service chassis with the v2 chassis-lite: kernel v2, a platform split into small public subpackages, PostgreSQL eventing (outbox, subscriptions, fan-out, inbox), a durable job queue with leases and fencing, `chassis_schema_v1` in every schema, a schema and role per module entered with `SET LOCAL ROLE` under row-level security, runtime composition (`App`, `ModuleContext`/`ModuleParts`, `ChannelContext`/`ChannelParts`), manifest-driven generation (`ledger-devtools sync`/`check`), the `api`/`worker`/`migrate` apps, and identity moved onto all of it with the gate green after every task.

**Architecture:** Moves first, by `git mv`, so history follows every file that survives; then the kernel's new vocabulary next to the old; then the chassis built beside the v1 pieces identity still runs on; then identity switched over in one cutover task; then the v1 pieces deleted; then the vocabulary cutover (qualified tax ids in `countries/do`, open permissions); then generation from manifests replaces every hand-kept list. Nothing is ever half-switched: each task ends with `uv run poe check` green.

**Tech Stack:** Python 3.15 (lazy imports, PEP 695, `TypeIs`, `uuid7`, `sys.monitoring` coverage), uv 0.13 workspace (members by glob), FastAPI 0.143 + pydantic 2.14, SQLAlchemy 2.1 Core + psycopg 3.3 (async), Alembic 1.20, PostgreSQL 18.6 (`SET LOCAL ROLE`, RLS, `FOR UPDATE SKIP LOCKED`, `LISTEN/NOTIFY`), structlog, OpenTelemetry, prometheus-client, joserfc, Granian 2.8, pytest 9 + pytest-asyncio + pytest-xdist + pytest-cov (sysmon) + hypothesis + testcontainers, ruff, basedpyright, mypy, import-linter, deptry, vulture, typos, buf.

**Spec:** `docs/superpowers/specs/2026-10-13-ledger-platform-v2-design.md` (authoritative): §2 (rules), §3.3-§3.6 and §3.10 (apps, in-process APIs, events, jobs, composition), §4 (layout, manifests, naming, layering, import-linter, devtools), §5 (kernel, platform subpackages, unit of work), §6 (data, roles, RLS, `chassis_schema_v1`, migrations, pooling), §14 (testing), §16.1 (keep/move/delete), §16.3 row v2-M1a, §16.4 (cut list). ADRs `docs/adr/0001`-`0010`. Audit `.superpowers/research/05-audit.md` §2, §3, §7. Executors read the spec sections a task cites before starting it.

---

## Preflight (run once, before Task 1; paste the output into the Task 1 report)

```bash
cd /c/Users/brian/Desktop/ledger
git status --short                       # expected: clean (only untracked .superpowers/ content, which is ignored)
git rev-parse --abbrev-ref HEAD          # expected: main
git log --oneline -3                     # expected: this plan's commit on top of e898391
uv --version                             # expected: uv 0.13.x
uv python find 3.15                      # expected: a CPython 3.15 interpreter path
docker version --format '{{.Server.Version}}'   # expected: 29.x (the daemon answers)
docker info --format '{{.OperatingSystem}}'     # expected: Docker Desktop
uv sync --locked --all-packages          # expected: resolves without changes
uv run poe check                         # expected: green on the starting commit (record the duration)
```

Ports (check, never free them by killing anything):

```bash
# Windows (PowerShell): Get-NetTCPConnection -State Listen | Where-Object LocalPort -in 5432,8080,8100,8101
netstat -ano | grep -E 'LISTENING' | grep -E ':(5432|8080|8100|8101) ' || echo "free"
```

- **8000 belongs to another project of the owner: never bind it, curl it or stop what listens on it.** Inside containers the apps listen on 8000; on the host the `api` app is published on `127.0.0.1:${LEDGER_API_PORT:-8100}` and the worker's probes on `127.0.0.1:${LEDGER_WORKER_PORT:-8101}`. A command run on the host always sets `LEDGER_HTTP_PORT=8100` (or 8101).
- 5432 is used by the local compose stack only (Tasks 20-21); tests use testcontainers on random ports. If 5432 is taken, set `LEDGER_POSTGRES_PORT` for compose and say so in the report; never stop the other listener.
- If any expectation above fails, stop and report; do not start Task 1.

## Global Constraints

Every task's requirements include this section.

- Repository `C:\Users\brian\Desktop\ledger`, branch `main`. **Commit locally only; never push, never rewrite or force anything, never delete a branch you did not create.** Parallel tasks (marked ∥) run in worktrees: `git worktree add ../ledger-<task> -b v2m1/<task> main`, then `git switch main && git merge --no-ff v2m1/<task>` once its gate is green, then `git worktree remove ../ledger-<task>`; a conflict in `pyproject.toml` or `uv.lock` is resolved by keeping both sides' edits and running `uv lock`.
- Commit messages: Conventional Commits (`feat(platform): ...`, `refactor: ...`, `chore: ...`, `test: ...`, `docs: ...`), body optional, and the message ends with a blank line followed by `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **The gate is green at the end of every task**: `uv run poe check` (ruff format check, ruff `select = ["ALL"]`, the `suppressions` step, basedpyright `typeCheckingMode = "all"` with warnings failing, mypy `--strict` with the extra error codes of the root config, import-linter, deptry, typos, buf, pytest with 100 % branch coverage and warnings as errors). From Task 19 the gate also runs `ledger-devtools check`, vulture and the per-package loops.
- **Zero suppressions**: no `# noqa`, `# type: ignore`, `# pyright:`/`# basedpyright:`, `# mypy:`, `# ruff:`, `# flake8:`, `# isort:`, `# pragma: no cover`/`no branch`; no `ignore_imports` in import-linter; no vulture whitelist of symbol names (patterns of framework entry points only, Task 19). No `Any`, no `cast` (a value of unknown type is narrowed with `isinstance`, a `TypeIs` function or `TypeAdapter(...).validate_python(...)`, as `ledger_testing.issuer` does for `getsockname`).
- **Python 3.15 idioms where they fit**: `lazy from ... import ...` for type-only imports (route signatures and pydantic field types stay ordinary imports: FastAPI and pydantic read them at runtime); PEP 695 generics and `type` aliases; `@final`, `@override`, `Final`, `ClassVar`, `Self`, `Never`, `NewType`, `Literal`, `TypeIs`; `uuid.uuid7`; deferred annotations (never `from __future__ import annotations`); exhaustive `match` with `assert_type` where a union is matched.
- Errors: every deliberate error is a `LedgerError` subclass with a `kind` and a stable snake_case `code` unique in the repository; its message is built inside the class (`raise X(field)`, never `raise X("literal")`); untrusted input is never echoed whole (`excerpt`, `described`); tax ids, tokens, bodies and payloads never reach a message, a log line or a span.
- `git mv` for every file that survives a move, so `git log --follow` keeps its history. A module this plan rewrites from scratch is a new file; its v1 predecessor is deleted (Task 16) when its last user is gone, and the task says so.
- Generated files (Task 19 onward) start with `GENERATED by ledger-devtools sync` and are never edited by hand: edit the manifest, run `uv run ledger-devtools sync`.
- **Never kill processes by image name** (`taskkill /IM`, `pkill -f`, `Stop-Process -Name`): stop only a process you started, by the PID you recorded. Containers you start are stopped by their id or by `docker compose -p <project> down -v` with the project name you chose.
- **Never print `.env` or `*.env` values** (no `cat`, `type`, `Get-Content`, `docker compose config` without `--quiet`, no `env | grep LEDGER`). Edit such files by variable name only.
- Docker must be running for integration tests (testcontainers PostgreSQL `postgres:18.6`). Image pins live in `deploy/local/images.toml` from Task 6; nothing else spells a pin without `ledger-devtools check` comparing it (Task 19).
- Windows host: psycopg's async driver needs a selector event loop; the `ledger_testing` plugin provides it from Task 6 (the root `conftest.py` until then). Use forward slashes and Git Bash for the commands below unless a step shows PowerShell.
- Dependency floors introduced by this plan: `pytest-xdist>=3.8.0`, `vulture>=2.14`, `tzdata>=2025.2` (platform: `zoneinfo` needs it on Windows). Removed: `nats-py`, `protobuf`, `grpcio-tools`, `mypy-protobuf`, `types-protobuf` (Task 16; the signer milestone adds the protobuf tooling back for its own contract). `uv.lock` pins exact versions; record them in the task report.
- When a checker objects to a detail of the code given in a step, fix the detail without changing the interface the step's **Interfaces** block names, and say so in the report. When the code given contradicts the spec, the spec wins: stop and report.

## Review Focus

The five input classes and failure modes most likely to hurt a user that the spec implies and no single feature test would catch. Each line names the task whose tests pin it.

1. **A pooled connection carrying one unit's tenant or role into the next unit.** With a pool of one connection: a unit scoped to company A, then a unit with no scope, then a unit scoped to company B, then a unit of another module's role. Expected: the second sees no tenant row (settings read `''` → `NULLIF` → no match), the third sees only B's rows, the fourth is refused (`permission denied for schema`) on the first module's tables; a mode-less scope sees no moded row; a rolled-back unit leaves nothing behind. Tests in Tasks 8 and 9.
2. **Two workers racing on the same queue rows, and a zombie worker.** Two dispatchers and two inbox runners on one schema, a handler that outlives its lease, a job whose lease expires and is reclaimed while the first worker is still running it. Expected: every event lands once per consumer, every handler effect commits once, the late writer's record is a no-op logged `job_lease_lost` (or `inbox_lease_lost`), attempts are counted on claim. Tests in Tasks 10 and 11.
3. **Events nobody subscribes to, and subscriptions that change.** An event published before its consumer's subscription exists; a rollback to a runner that does not know a newer event type; a consumer removed from a manifest. Expected: the outbox row is kept with `unrouted_since` and fanned out after the routing version moves; a runner never claims a type it has no handler for, the row waits; a removed subscription stops new fan-out without deleting what was already delivered. Tests in Tasks 10 and 14.
4. **The same `Idempotency-Key` across routes, modes and principals, and twins.** One key used on two routes, in test and in live mode, by two principals of one organization, twice at the same moment, after a failure, with another body, after expiry (and the purge under forced RLS). Expected: keys are scoped per (organization, mode, principal, route); twins wait and replay with `Idempotent-Replayed: true`; another body is 422 `idempotency_key_reused`; a failure stores nothing; a missing `Ledger-Mode` on an idempotent route is 400 `mode_required`; expired keys are purged without any unit seeing another organization's live keys. Tests in Tasks 13 and 15.
5. **Migrations and roles under concurrency and misuse.** Two `ledger-migrate` runs at once, a rerun, a rollout whose runtime role tries DDL, an `UPDATE`/`DELETE` of `audit_events`, a module bug that names another schema's table. Expected: the second migrate waits for the schema's advisory lock then finds head and does nothing; the runtime role cannot create, alter or drop; the `rw` role cannot change audit rows; a cross-schema statement fails with a permission error even inside a valid unit. Tests in Tasks 8, 14 and 15.

## Decisions this plan adds (spec gaps, each with its reason)

The spec is authoritative; where it is silent or contradicts itself, this plan decides as follows. The owner may veto any of these in review.

1. **`Event` lives in the kernel** (`ledger_kernel.events.Event`) and `ledger_platform.events` re-exports it. §4.5 says a module's `events.py` imports only `ledger_kernel`, while §5.2 lists `Event` under `platform.events`; a kernel base satisfies both.
2. **`Tenant.mode` may be `None`** (`Tenant(organization, company, mode: Mode | None)`). §7.1 says a company, its establishments and its memberships are shared across modes, so a unit working on them has no mode. Row-level security compares `mode` with the unit's setting, and `NULL` never matches: a mode-less unit sees no moded row (fail closed).
3. **A fourth RLS scope, `company`** (`organization_id` and `company_id`, no mode), for company rows shared by both modes (identity's branches and emission points now, establishments in v2-M2). §6.3 lists three scopes and no home for those rows.
4. **An idempotent route is a moded route.** `idempotency_keys` is under the `moded_organization` scope (§6.4) with `mode NOT NULL` in its primary key, so a key can only be stored in a moded unit: every route that takes `Idempotency-Key` requires `Ledger-Mode` (400 `mode_required` without it, §9.7). In this milestone identity's `POST`s therefore require the header; API-client credentials that carry their mode come with v2-M2.
5. **400 answers.** The kind table (§9.6) has no 400, but `mode_required` and `version_unknown` are 400 (§9.2, §9.7). The platform's HTTP layer gets `BadRequestError(InvalidInputError)`, answered 400; the kind stays `INVALID`.
6. **Expired idempotency keys are purged under forced RLS** by a dedicated role, never a setting (Task 8 review): each schema has a `NOLOGIN` role `<s>_purge`, granted to its login like `<s>_rw` (`INHERIT FALSE, SET TRUE`), with `SELECT, DELETE` on `<s>.idempotency_keys` only, and two extra policies `TO <s>_purge` (`FOR SELECT` and `FOR DELETE`, `USING (expires_at < now())`). `BoundDatabase.purging()` enters it with `SET LOCAL ROLE <s>_purge`. The `rw` role sees nothing more whatever it sets (`ledger.purge` means nothing); what the purge role can see is expired by definition, and it can neither insert nor update.
7. **Per-table grants to `ledger_relay` are applied by `ledger-migrate`** as the schema's owner after each schema's revisions (`SchemaMigrations.upgrade` runs `roles.migrated_statements`: `GRANT SELECT, DELETE ON <s>.outbox`, `UPDATE (attempts, unrouted_since) ON <s>.outbox`, `INSERT ON <s>.inbox`, `SELECT ON <s>.subscriptions`, `SELECT ON platform.routing`, and `REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON <s>.alembic_version FROM <s>_rw`): the tables do not exist when `init.sql` runs. `init.sql` makes roles (created if missing, then `ALTER ROLE` to exactly their options), schemas, schema `USAGE`, default privileges, and closes the database to `PUBLIC` (`CONNECT`, `TEMPORARY`, schema `public`, `EXECUTE` on the owners' functions).
8. **The `platform` schema runs `chassis_schema_v1` too** (its schedules enqueue jobs into `platform.jobs`), then adds `routing` and `schedule_leases`.
9. **Platform schedules are `platform.purge_idempotency_keys` and `platform.purge_done_jobs`.** §3.2 names `platform.purge_inbox`, but §6.4 says handled inbox rows are deleted by their handler and dead ones are kept; the purges §6.4 asks for are idempotency keys and done jobs.
10. **Audit rows take their actor from the running call.** `uow.audit(...)` (§5.3) receives neither actor nor call id, so the runtime binds an `Actor(name, call_id)` in a context variable (`ledger_platform.data.audit.acting_as`) wherever it makes a `Call` or `SystemCall`; an audit without one raises `MissingActorError`.
11. **`ChannelContext` is typed without fiscal-kit.** The platform sits below `ledger_fiscal_kit` (§4.6 contract 1), so `ChannelContext.definition` is the platform's `ChannelEntry` (from the manifest) and the ports are reached through a typed `Ports` directory (`context.ports.get(SIGNER_PORT)`); fiscal-kit (v2-M3) defines the port keys and its typed `ChannelRegistry` over the platform's `Channels`. The outbound `HttpClientFactory` comes with the first channel (v2-M3).
12. **Directories are keyed by typed keys, not Protocol classes.** mypy refuses a Protocol where `type[T]` is expected (`type-abstract`), so `apis.get(IdentityApi)` is spelled `apis.get(IDENTITY_API)` with `IDENTITY_API: Final = Key[IdentityApi]("identity", is_identity_api)`, the second argument a `TypeIs` function: typed, cast-free, checked at bind time.
13. **Entry points name an assembled object.** Group `ledger.modules` points at `ledger_<m>.assembly:MODULE` (a `Module(manifest=MANIFEST, assemble=assemble)`), not at `_manifest.py`: the runtime needs both, and loading through `TypeAdapter(InstanceOf[Module])` keeps `Any` out.
14. **One `Settings` class for every app.** One `deploy/local/ledger.env` serves all apps and settings fail closed on unknown `LEDGER_*` names (§13), so every app accepts the same variables; token settings are optional in the class and required by the `api` role at start. Per-module sections (`LEDGER_<MODULE>_*`) come with the first module that needs one.
15. **Schedules come from manifests only** (`every = "1h"`); `ModuleParts` has no `schedules` field (nothing a module could add), and `cron` is refused by `ledger-devtools check` until a schedule needs it.
16. **Consumers receive `RawEvent`** (the envelope and a JSON payload); typed decoding comes with the first typed consumer (v2-M3).
17. **Poll safety net per runner**: each runner polls its own claim every second; the single `UNION ALL` probe of §3.5 is an optimization for when idle polling costs something.
18. **Ordered consumers are blocked only by older rows that are not dead**, as the `inbox_head` index's predicate (`status <> 'dead'`) implies.
19. **One manifest model**: `ledger_platform.runtime.manifest` (pydantic models, `extra="forbid"`) is what `ledger-devtools` validates `manifest.toml` with and what the runtime reads; each generated `_manifest.py` is the manifest's data validated at import. Packages may declare console scripts (`[package] scripts`), which §4.2 lacks and `ledger-probe` and `ledger-devtools` need.
20. **Image pins**: compose cannot read TOML, so `deploy/local/images.toml` is the source, read by `ledger_testing`, and `ledger-devtools check` fails when `compose.yaml` or the Dockerfile names any other image.
21. **buf with no protos**: until the signer contract exists (v2-M2) `contracts/proto` holds no `.proto`; the `proto-lint` task skips buf when there is none, and CI's `buf breaking` step is skipped likewise.
22. **Per-package coverage without a `gate` command**: each generated `pyproject.toml` carries its package's `[tool.coverage]`; the root poe tasks loop over the packages that have a `manifest.toml` (`pytest <dir> --cov --cov-config=<dir>/pyproject.toml`, `mypy <dir>`, `deptry <dir>/src`). `ledger-devtools gate` stays deferred (§16.4 keeps only `sync` and `check` for this cut).
23. **Event JSON-Schema snapshots and generated role grants are v2-M2** (identity's events have no consumer yet; the role table is identity's v2 work). OpenAPI snapshots are generated by `sync` from Task 19.
24. **Plans**: §4.1 says plans live untracked in `.superpowers/`; the owner asked for this plan under `docs/superpowers/plans/`, so it stays tracked, and Task 1 removes the v1 plans m1-m5 as the owner ruled.
25. **The image installs every app by directory glob** (`for d in apps/*/; do uv sync --inexact --package ledger-app-$(basename $d); done`), so the Dockerfile lists no package.
26. **identity's HTTP package moves to `adapters/http/`** (§4.1's layout); `ledger_identity.api` is reserved for the `IdentityApi` Protocol (v2-M2).
27. **A country's dev-only package** lives at `countries/<cc>/testing` with `name = "<cc>-testing"` (§4.4's "name == directory" cannot hold: `testing` is taken by `packages/testing`); devtools derives its distribution `ledger-country-<cc>-testing` and import package `ledger_country_<cc>_testing` from the directory, and never writes `[tool.uv.sources]` into it (uv refuses a nested member's workspace sources).

## Deferred: what this milestone does not build

- **Identity v2 (next milestone, v2-M2)**: `jurisdiction` and `functional_currency` on companies; several tax ids per company and the global `identity.tax_registrations` registry (this milestone keeps one qualified tax id per company and the existing global unique index on active companies); `mode` columns on identity's moded rows and API clients; TypeIDs on the wire (`cmp_…`; the codec exists in the kernel); `Ledger-Version` pin storage and echo; `ROLE_GRANTS` generated from manifests (identity keeps its hand table with the new permission codes); establishments (branches and emission points stay as they are, under the `company` RLS scope); the Zitadel claims profile as a pluggable port (the code moves to `auth/providers/zitadel.py`, the port comes later); `IdentityApi` facade and `CompanyDirectory`; `GET /v1/countries`; checksum warnings on responses; one organization-admin check instead of the route guard plus the use case (audit D3: it changes an error code clients see); the platform's "bound unit" route dependency (audit D25: identity's routes are rewritten with establishments); the generated deny-by-default test per tenant table; `ledger-api --with-worker` (the single-process dev mode of §3.3).
- **Chassis beyond lite**: `blobs` (`ImmutableStore`, `ArchivePort`) with v2-M3; `grpc` per-caller tokens and method allowlists with the signer (v2-M2; `grpc/` is moved, unchanged); the `receiver` and `signer` apps (the roles exist in `AppRole` and the `App` refuses them until their modules exist); one kind → status table shared by HTTP and gRPC (audit D5; gRPC is untouched until the signer); dated-version transforms; metrics gauges that read queue tables (v2-M6 with alerts; counters for lease loss, dead rows and unrouted events are built); operator subcommands (`ledger-worker replay`, wakes); devtools `gate`, `affected`, `scaffold`, CODEOWNERS; event schema snapshots; LocalStack and the fake DGII in compose (signer milestone and the DGII spike).
- Everything on §16.4's cut list.

## Coordination with the DGII spike (v2-M1b)

The spike plan (`docs/superpowers/plans/2026-10-13-v2-dgii-spike.md`, written in parallel) builds `countries/do`
(`ledger-country-do`: regime data with provisional copies of the kernel's regime types, the `do-ecf-v1` channel as
a library) and `countries/do/testing` (`ledger-country-do-testing`, dev-only) in its own worktree, importing nothing
of the workspace. Both plans merge into `main`; these rules keep them compatible:

1. **Root `pyproject.toml`.** The spike adds its two distributions to v1's hand lists. If it merges before Task 1,
   Task 1 moves its paths with everything else (`countries/` is already at its v2 place); if after, the merge adds
   them to the lists as they are then. From Task 19 the lists are generated and the spike's packages get manifests.
2. **Nested member** (the spike's verified uv finding): `countries/<cc>/testing` must not declare
   `[tool.uv.sources]` of its own. Task 7's generator writes no sources for a nested member, and its naming rule
   maps `countries/<cc>/testing` to the name `<cc>-testing` and the distribution `ledger-country-<cc>-testing`
   (Decision 27).
3. **The kernel's `countries/do.py`.** Task 18 moves it to `countries/do/src/ledger_country_do/regime/tax_ids.py`
   only if the spike has not merged. If it has, that file is the spike's: Task 18 deletes the kernel module
   (`git rm`), keeps the spike's file, and points the spike's regime at kernel v2 instead of its provisional
   copies (`regime/definitions.py` is deleted; its users import `ledger_kernel.regimes`, `ledger_kernel.dated`,
   `ledger_kernel.tax_ids`; field differences are adapted in the spike's data, never in the kernel), and makes the
   spike's error bases subclasses of the kernel's. Task 18's report says which path it took.
4. **Manifests.** Task 19 writes `countries/do/manifest.toml` from §4.3 (regime `do`; the channel entry only once
   the channel has an assembly and a schema, v2-M3) and `countries/do/testing/manifest.toml` (`kind = "package"`,
   `name = "do-testing"`).

## Task graph

```
T1 tree move
 ├─► T2 kernel v2 (additive) ───────────┐
 └─► T3 platform subpackages (git mv) ──┤        G1 = {T2 ∥ T3}
                                        ├─► T4 tenancy everywhere ─┐
                                        └─► T5 manifest model ─────┤  G2 = {T4 ∥ T5}
                                                                   ├─► T6 testing plugin + xdist ─┐
                                                                   └─► T7 devtools core ──────────┤  G3 = {T6 ∥ T7}
T6 ─► T8 data: roles, RLS, chassis_schema_v1, schema migrations ─► T9 data: bound units, queue
T9 ─► T10 events ∥ T11 jobs ∥ T13 http + auth                                                      G4
{T10, T11} ─► T12 runtime types (may overlap T13)
{T12, T13} ─► T14 App ─► T15 identity cutover (needs T7) ─► T16 delete v1 chassis
T16 ─► T17 apps ∥ T18 vocabulary cutover (countries/do, qualified tax ids, open permissions)       G5 = {T17 ∥ T18}
{T17, T18} ─► T19 devtools wiring ─► T20 deploy, CI, README ─► T21 verification
```

| # | Task | After | Group |
|---|---|---|---|
| 1 | Preflight, tree move, v1 plans out | — | — |
| 2 | Kernel v2 additions | 1 | G1 |
| 3 | Platform subpackages by `git mv` | 1 | G1 |
| 4 | Tenancy, scopes and public ids everywhere | 2, 3 | G2 |
| 5 | The manifest model | 3 | G2 |
| 6 | Testing plugin, xdist, per-worker databases, images.toml | 4 | G3 |
| 7 | `ledger-devtools` core (`check`, `sync` of pyproject, `_manifest.py`, `.importlinter`) | 5 | G3 |
| 8 | Data I: roles, RLS builder, `chassis_schema_v1`, per-schema migrations | 6 | — |
| 9 | Data II: `BoundDatabase`, unit of work, notifier, audit, queue | 8 | — |
| 10 | Events: codec, subscriptions, routing, dispatcher, inbox runner, wake-ups | 9 | G4 |
| 11 | Jobs: runner with leases and fencing, schedules, purges | 9 | G4 |
| 12 | Runtime types: calls, facade guard, directories, contexts and parts, registries | 10, 11 | ∥ 13 |
| 13 | HTTP and auth split, `Ledger-Mode`, idempotency v2 | 9 | G4 |
| 14 | `App`: assemble, start, stop, migrate | 10-13 | — |
| 15 | Identity cutover to the v2 chassis | 14, 7 | — |
| 16 | Delete the v1 chassis, NATS and the Python contracts | 15 | — |
| 17 | Apps `api`, `worker`, `migrate` | 16 | G5 |
| 18 | Vocabulary cutover: `countries/do`, qualified tax ids, open permissions | 16 | G5 |
| 19 | Manifests everywhere, generated files, per-package gate, vulture | 17, 18 | — |
| 20 | One image, compose without NATS/Tilt, CI, README | 19 | — |
| 21 | Final verification | 20 | — |

## File map (end state)

```
ledger/
├── pyproject.toml            tool config only; members by glob; poe tasks loop over manifests
├── .importlinter             GENERATED
├── README.md  AGENTS.md      short (Task 20)
├── packages/
│   ├── kernel/   manifest.toml pyproject.toml(GEN) src/ledger_kernel/{errors,ids,tenancy,access,money,jurisdiction,
│   │             tax_ids,dated,regimes,events,pagination,clock}.py  tests/unit/
│   ├── platform/ manifest.toml pyproject.toml(GEN) src/ledger_platform/
│   │   ├── _manifest.py(GEN)
│   │   ├── config/{settings,options}.py
│   │   ├── observability/{logs,tracing,health,probe,telemetry}.py
│   │   ├── data/{database,bound,unit_of_work,notifier,audit,codec,queue,rls,roles,tables,chassis_schema,migrations}.py
│   │   │   └── revisions/{env.py, versions/r0001_platform.py}
│   │   ├── events/{__init__ (Event, RawEvent, ...), bus,routing,subscriptions,dispatcher,inbox,wakeups}.py
│   │   ├── jobs/{__init__, outcomes,runner,schedules,maintenance}.py
│   │   ├── runtime/{app,roles,calls,facade,keys,contexts,registries,manifest,supervision,start}.py
│   │   ├── http/{app,problems,metrics,probes,deps,mode,idempotency,request_fields,openapi}.py
│   │   ├── auth/{keys,tokens,access}.py  auth/providers/zitadel.py
│   │   └── grpc/{server,client}.py
│   ├── testing/  src/ledger_testing/{plugin,hooks,postgres,images,issuer,api,clock,logs,migrations,waiting,ports}.py
├── modules/identity/ manifest.toml pyproject.toml(GEN) openapi.json(GEN)
│   └── src/ledger_identity/{_manifest(GEN),assembly,events}.py domain/ application/ adapters/{postgres,http}/
├── countries/do/ manifest.toml pyproject.toml(GEN) src/ledger_country_do/{_manifest(GEN)}.py regime/{__init__,tax_ids}.py
├── apps/{api,worker,migrate}/ manifest.toml pyproject.toml(GEN) src/ledger_app_<role>/main.py tests/
├── contracts/buf.yaml        (protos arrive with the signer)
├── tools/devtools/ manifest.toml pyproject.toml(GEN) src/ledger_devtools/{cli,workspace,checks,generate,names,sql,openapi}.py
├── deploy/docker/ledger.Dockerfile
├── deploy/local/{compose.yaml, images.toml, ledger.env, postgres/init.sql(GEN), postgres/zitadel.sql, zitadel/}
└── docs/{adr/, runbooks/identity/orphaned-company.md, superpowers/specs/, superpowers/plans/(this plan only)}
```

---

### Task 1: Preflight, the v2 tree by `git mv`, v1 plans out of the tree

Sequential (everything else starts from it). Spec §4.1, §16.1 (root rows), §16.3 (plans removed in v2-M1).

**Files:**
- Move: `libs/kernel` → `packages/kernel`; `libs/platform` → `packages/platform`; `libs/testing` → `packages/testing`; `libs/contracts` → `contracts` (its Python parts stay a workspace member until Task 16 deletes them); `services/identity` → `modules/identity`; `docs/runbooks/identity-orphaned-company.md` → `docs/runbooks/identity/orphaned-company.md`
- Delete: `docs/superpowers/plans/2026-10-11-milestone-1-foundations.md`, `2026-10-12-milestone-2-identity.md`, `2026-10-12-milestone-3-vault.md`, `2026-10-12-milestone-4-fiscal-do.md`, `2026-10-12-milestone-5-invoicing.md` (this plan stays)
- Modify: `pyproject.toml`, `_typos.toml`, `tests/test_workspace.py`, `modules/identity/Dockerfile`, `deploy/local/compose.yaml`, `Tiltfile`, `.github/workflows/ci.yml`, `uv.lock` (regenerated)

**Interfaces:**
- Consumes: nothing.
- Produces: the tree every later task names. Workspace members `["packages/*", "modules/*", "contracts"]`. No import name changes: `ledger_kernel`, `ledger_platform`, `ledger_testing`, `ledger_contracts`, `ledger`, `ledger_identity` are where they were inside their packages.

- [ ] **Step 1: Preflight**

Run the Preflight section above. Paste its output in the report. Stop on any unexpected answer.

- [ ] **Step 2: Move with history**

```bash
cd /c/Users/brian/Desktop/ledger
mkdir -p packages modules docs/runbooks/identity
git mv libs/kernel packages/kernel
git mv libs/platform packages/platform
git mv libs/testing packages/testing
git mv libs/contracts contracts
git mv services/identity modules/identity
git mv docs/runbooks/identity-orphaned-company.md docs/runbooks/identity/orphaned-company.md
git rm -q docs/superpowers/plans/2026-10-11-milestone-1-foundations.md \
          docs/superpowers/plans/2026-10-12-milestone-2-identity.md \
          docs/superpowers/plans/2026-10-12-milestone-3-vault.md \
          docs/superpowers/plans/2026-10-12-milestone-4-fiscal-do.md \
          docs/superpowers/plans/2026-10-12-milestone-5-invoicing.md
rmdir libs services 2>/dev/null; ls libs services 2>&1 | head -2   # expected: both "No such file or directory"
git status --short | grep -v '^R ' | grep -v '^D ' || true          # expected: nothing (only renames and deletions)
```

- [ ] **Step 3: Point the root configuration at the new paths**

`pyproject.toml` — replace each spelling below (and nothing else):

| Where | Before | After |
|---|---|---|
| `[tool.uv.workspace] members` | `["libs/*", "services/*"]` | `["packages/*", "modules/*", "contracts"]` |
| poe `mypy-kernel` … `mypy-identity` | `libs/kernel`, `libs/contracts`, `libs/platform`, `libs/testing`, `services/identity` | `packages/kernel`, `contracts`, `packages/platform`, `packages/testing`, `modules/identity` |
| poe `deps-*` | `libs/<x>/src --config libs/<x>/pyproject.toml`, `services/identity/...` | `packages/<x>/...`, `contracts/src --config contracts/pyproject.toml`, `modules/identity/...` |
| poe `proto` | `--proto_path=${POE_ROOT}/libs/contracts/proto --python_out=libs/contracts/src ... libs/contracts/proto/ledger/**/*.proto` | `--proto_path=${POE_ROOT}/contracts/proto --python_out=contracts/src ... contracts/proto/ledger/**/*.proto` (all four `*_out`) |
| poe `proto-lint` | `--volume ${POE_ROOT}/libs/contracts:/workspace` | `--volume ${POE_ROOT}/contracts:/workspace` |
| poe `openapi` | `services/identity/openapi.json` | `modules/identity/openapi.json` |
| `suppressions` exclude | `":(exclude)libs/contracts/src/ledger"` | `":(exclude)contracts/src/ledger"` |
| `[tool.ruff] extend-exclude` | `"libs/contracts/src/ledger"` | `"contracts/src/ledger"` |
| `[tool.ruff.lint.per-file-ignores]` key | `"{libs/*/tests,services/*/tests,tests}/**"` | `"{packages/*/tests,modules/*/tests,contracts/tests,tests}/**"` |
| `[tool.basedpyright]` | `include = ["libs", "services", "tests", "conftest.py"]`, `exclude = ["libs/contracts/src/ledger", ...]` | `include = ["packages", "modules", "contracts", "tests", "conftest.py"]`, `exclude = ["contracts/src/ledger", "**/.venv", "**/__pycache__"]` |
| `[tool.mypy] exclude` | `["libs/contracts/src/ledger/"]` | `["contracts/src/ledger/"]` |
| `[tool.pytest] testpaths` | `["libs", "services", "tests"]` | `["packages", "modules", "contracts", "tests"]` |
| `[tool.coverage.run] source` | `["services/identity/src/ledger_identity/adapters/postgres/migrations"]` | `["modules/identity/src/ledger_identity/adapters/postgres/migrations"]` |

`_typos.toml`: `extend-exclude = ["uv.lock", "contracts/src/ledger/"]`.

`tests/test_workspace.py`: it discovers members from `[tool.uv.workspace] members`; make every literal `libs/...`/`services/...` path in it the new one (`git grep -n "libs/\|services/" tests/test_workspace.py` lists them). The file is deleted in Task 19; until then it must pass.

`modules/identity/Dockerfile`: the build-command comment and the `COPY <member>/pyproject.toml <member>/` lines name `packages/kernel`, `contracts`, `packages/platform`, `packages/testing`, `modules/identity`. `deploy/local/compose.yaml`: `dockerfile: modules/identity/Dockerfile`. `Tiltfile`: `dockerfile="modules/identity/Dockerfile"`, `only=["pyproject.toml", "uv.lock", ".python-version", "packages", "modules", "contracts"]`. `.github/workflows/ci.yml`: `git add --intent-to-add contracts/src/ledger`, `git diff --exit-code -- contracts/src/ledger`, `git cat-file -e origin/main:contracts/buf.yaml`, `--workdir /repo/contracts`, `'/repo/.git#branch=origin/main,subdir=contracts'`, `file: modules/identity/Dockerfile`.

```bash
git grep -n "libs/\|services/" -- ':!docs' ':!uv.lock'   # expected: no line (README is rewritten in Task 20; say in the report which README lines still name old paths)
uv lock && uv sync --locked --all-packages
```

Expected: `uv lock` rewrites the workspace members' `source = { editable = ... }` paths and nothing else (`git diff --stat uv.lock` shows only those lines).

- [ ] **Step 4: Prove history follows**

```bash
git log --follow --oneline -- packages/kernel/src/ledger_kernel/money.py | tail -1   # expected: the M1 commit that created it
git log --follow --oneline -- modules/identity/src/ledger_identity/domain/company.py | tail -1
```

- [ ] **Step 5: Gate**

Run: `uv run poe check`
Expected: green; the same test count as the Preflight run.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "refactor: move to the v2 tree (packages, modules, contracts) and drop the v1 plans

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Kernel v2 additions (∥ Task 3)

Spec §5.1, §6.6, §7.3, §7.4, §8.2, ADRs 0005-0007. Additive: no dependent of the kernel changes in this task. The kernel stays standard library only.

**Files:**
- Create: `packages/kernel/src/ledger_kernel/{tenancy,jurisdiction,tax_ids,dated,regimes,events}.py`
- Modify: `packages/kernel/src/ledger_kernel/errors.py` (+ `InvalidValueError`), `packages/kernel/src/ledger_kernel/money.py` (ISO 4217 table, `RoundingMode`, `Money.quantized`), `packages/kernel/src/ledger_kernel/access.py` (`InvalidPrincipalError` → `InvalidValueError`)
- Move: `packages/kernel/tests/test_*.py` → `packages/kernel/tests/unit/test_*.py` (`git mv`, one by one)
- Test: `packages/kernel/tests/unit/test_{tenancy,jurisdiction,tax_ids,dated,regimes,events}.py`; modify `test_money.py`, `test_errors.py`, `test_access.py`

**Interfaces:**
- Consumes: `LedgerError`, `InvalidInputError`, `excerpt`, `described` (errors); `OrganizationId`, `CompanyId` (ids); `Money` (money).
- Produces (later tasks rely on these exact names):
  - `errors.InvalidValueError(*, field: str, requirement: str)`, code `invalid_value`, kind `INTERNAL`.
  - `tenancy.Mode(StrEnum)` `TEST="test"`, `LIVE="live"`; `tenancy.Tenant(organization, company, mode: Mode | None)`; `tenancy.OrganizationScope(organization, mode: Mode | None)` with property `company -> None`; `type tenancy.Scope = Tenant | OrganizationScope`.
  - `money.Currency(code: str, numeric: int, minor_units: int)` with `Currency.of(code) -> Currency`; `money.CURRENCIES: Mapping[str, Currency]`; `money.RoundingMode` (`HALF_UP`, `HALF_EVEN`, `DOWN`, `UP`); `Money.quantized(places: int, mode: RoundingMode) -> Money`; `Money.rounded()` uses `currency.minor_units`.
  - `jurisdiction.CountryCode = NewType(str)`, `parse_country(text) -> CountryCode` (`InvalidCountryError`, code `invalid_country`), `Jurisdiction(country, subdivision: str | None = None)`, `parse_jurisdiction(country, subdivision=None) -> Jurisdiction` (`InvalidSubdivisionError`, code `invalid_subdivision`).
  - `tax_ids.TaxIdScheme = NewType(str)`, `parse_scheme(text)` (`InvalidTaxIdSchemeError`, code `invalid_tax_id_scheme`), `TaxIdCheck` (`VALID`, `CHECKSUM_MISMATCH`, `INVALID_FORMAT`), `TaxId(scheme, value)` with a masked `repr`, `InvalidTaxIdError(scheme, problem: TaxIdCheck)` (code `invalid_tax_id`), `MAX_TAX_ID_LENGTH = 40`.
  - `dated.Since[T](since: date, value: T, until: date | None = None)`, `dated.Timeline[T](*entries)` with `.at(day) -> T` and `.entries`; `InvalidTimelineError` (internal, code `invalid_timeline`), `NotInEffectError` (code `not_in_effect`).
  - `regimes`: `Locale`, `RegimeKey`, `Source`, `RateValue`, `TaxCategoryKind`, `TaxCategoryDef`, `DocumentKind`, `Direction`, `DocumentTypeDef`, `TaxIdSchemeDef`, `FieldKind`, `FieldDef`, `RoundingPoint`, `RoundingRule`, `RegimeDef`, `TaxIdSchemes`, `ParsedTaxId`, `TaxLine`, `CategoryTotal`, `Totals`, `TaxCalculator`, `InvalidRegimeError` (internal, `invalid_regime`), `TaxIdSchemeUnknownError` (`tax_id_scheme_unknown`), `TaxCategoryUnknownError` (`tax_category_unknown`).
  - `events.Event` (base dataclass: `TYPE`, `VERSION` class variables; fields `scope: Scope | None`, `subject: UUID | None`, `aggregate_version: int | None`), `InvalidEventClassError` (internal, `invalid_event_class`).

- [ ] **Step 1: Move the kernel's tests under `tests/unit`**

```bash
cd /c/Users/brian/Desktop/ledger/packages/kernel/tests
mkdir -p unit && for f in test_*.py; do git mv "$f" "unit/$f"; done
cd /c/Users/brian/Desktop/ledger && uv run pytest packages/kernel -q -p no:randomly 2>&1 | tail -1
```

Expected: the same number of passed tests as before the move.

- [ ] **Step 2: Write the failing tests**

`packages/kernel/tests/unit/test_tenancy.py`:

```python
from uuid import uuid7

from ledger_kernel.ids import CompanyId, OrganizationId
from ledger_kernel.tenancy import Mode, OrganizationScope, Scope, Tenant

ORGANIZATION = OrganizationId(uuid7())
COMPANY = CompanyId(uuid7())


def parts(scope: Scope) -> tuple[OrganizationId, CompanyId | None, Mode | None]:
    return scope.organization, scope.company, scope.mode


def test_a_tenant_names_its_organization_company_and_mode() -> None:
    assert parts(Tenant(ORGANIZATION, COMPANY, Mode.LIVE)) == (ORGANIZATION, COMPANY, Mode.LIVE)


def test_a_tenant_without_a_mode_reaches_only_what_both_modes_share() -> None:
    assert parts(Tenant(ORGANIZATION, COMPANY, None)) == (ORGANIZATION, COMPANY, None)


def test_an_organization_scope_has_no_company() -> None:
    assert parts(OrganizationScope(ORGANIZATION, Mode.TEST)) == (ORGANIZATION, None, Mode.TEST)


def test_modes_are_the_words_rows_store() -> None:
    assert [mode.value for mode in Mode] == ["test", "live"]
```

`packages/kernel/tests/unit/test_jurisdiction.py`:

```python
import pytest

from ledger_kernel.errors import InvalidValueError
from ledger_kernel.jurisdiction import (
    CountryCode,
    InvalidCountryError,
    InvalidSubdivisionError,
    Jurisdiction,
    parse_country,
    parse_jurisdiction,
)


def test_every_assigned_alpha_2_code_is_a_country() -> None:
    assert parse_country("DO") == "DO"
    assert parse_country("ZW") == "ZW"


@pytest.mark.parametrize("text", ["do", "XX", "EU", "D", "DOM", "", " DO", 7])
def test_anything_else_is_refused_without_echoing_it_whole(text: object) -> None:
    with pytest.raises(InvalidCountryError) as caught:
        parse_country(text)  # the boundary hands over whatever it got
    assert caught.value.code == "invalid_country"


def test_a_subdivision_belongs_to_its_country() -> None:
    assert parse_jurisdiction("US", "US-CA") == Jurisdiction(CountryCode("US"), "US-CA")
    with pytest.raises(InvalidSubdivisionError):
        parse_jurisdiction("US", "MX-CMX")
    with pytest.raises(InvalidSubdivisionError):
        parse_jurisdiction("US", "US-california")


def test_a_jurisdiction_built_by_our_code_from_bad_parts_is_a_defect() -> None:
    with pytest.raises(InvalidValueError):
        Jurisdiction(CountryCode("XX"))
    with pytest.raises(InvalidValueError):
        Jurisdiction(CountryCode("US"), "MX-CMX")
```

(The production signatures take `object` — `parse_country(text: object)`, `Currency.of(code: object)` — because a boundary hands over whatever arrived, so the tests pass non-`str` values without any checker complaint.)

`packages/kernel/tests/unit/test_tax_ids.py`:

```python
import pytest

from ledger_kernel.errors import InvalidValueError
from ledger_kernel.tax_ids import (
    InvalidTaxIdError,
    InvalidTaxIdSchemeError,
    TaxId,
    TaxIdCheck,
    TaxIdScheme,
    parse_scheme,
)


def test_a_scheme_is_a_country_and_a_name() -> None:
    assert parse_scheme("do.rnc") == "do.rnc"
    assert parse_scheme("eu.vat") == "eu.vat"


@pytest.mark.parametrize("text", ["rnc", "DO.rnc", "do.", "do.RNC", "dom.rnc", "do.rnc.x", ""])
def test_anything_else_is_not_a_scheme(text: str) -> None:
    with pytest.raises(InvalidTaxIdSchemeError):
        parse_scheme(text)


def test_repr_masks_all_but_the_last_three_characters() -> None:
    tax_id = TaxId(TaxIdScheme("do.rnc"), "101850043")
    assert repr(tax_id) == "TaxId(scheme='do.rnc', value='******043')"
    assert "101850043" not in str(tax_id)


def test_a_short_value_is_masked_whole() -> None:
    assert repr(TaxId(TaxIdScheme("xx.id"), "12")) == "TaxId(scheme='xx.id', value='***')"


@pytest.mark.parametrize("value", ["", "12 34", "x" * 41, "ñ123"])
def test_a_tax_id_built_from_a_bad_value_is_a_defect(value: str) -> None:
    with pytest.raises(InvalidValueError):
        TaxId(TaxIdScheme("do.rnc"), value)


def test_an_error_names_the_scheme_and_the_problem_never_the_value() -> None:
    error = InvalidTaxIdError(TaxIdScheme("do.rnc"), TaxIdCheck.INVALID_FORMAT)
    assert str(error) == "not a valid do.rnc identifier: invalid_format"
    assert error.code == "invalid_tax_id"
```

`packages/kernel/tests/unit/test_dated.py`:

```python
from datetime import date

import pytest

from ledger_kernel.dated import InvalidTimelineError, NotInEffectError, Since, Timeline


def test_the_newest_value_in_effect_on_a_day_wins() -> None:
    rates = Timeline(Since(date(2026, 1, 1), 18), Since(date(2013, 1, 1), 16))
    assert rates.at(date(2026, 1, 1)) == 18
    assert rates.at(date(2025, 12, 31)) == 16


def test_before_the_oldest_value_nothing_is_in_effect() -> None:
    with pytest.raises(NotInEffectError) as caught:
        Timeline(Since(date(2013, 1, 1), 16)).at(date(2012, 12, 31))
    assert str(caught.value) == "no value is in effect on 2012-12-31"


def test_a_value_that_ended_is_not_in_effect_after_its_end() -> None:
    abolished = Timeline(Since(date(2020, 1, 1), 5, until=date(2021, 1, 1)))
    assert abolished.at(date(2020, 12, 31)) == 5
    with pytest.raises(NotInEffectError):
        abolished.at(date(2021, 1, 1))


@pytest.mark.parametrize(
    ("entries", "problem"),
    [
        ((), "holds no value"),
        ((Since(date(2013, 1, 1), 1), Since(date(2026, 1, 1), 2)), "is not newest first"),
        ((Since(date(2026, 1, 1), 1), Since(date(2026, 1, 1), 2)), "is not newest first"),
        ((Since(date(2026, 1, 1), 1), Since(date(2013, 1, 1), 2, until=date(2027, 1, 1))), "has overlapping values"),
        ((Since(date(2026, 1, 1), 1, until=date(2026, 1, 1)),), "has a value that ends before it starts"),
    ],
)
def test_a_timeline_that_contradicts_itself_is_refused(entries: tuple[Since[int], ...], problem: str) -> None:
    with pytest.raises(InvalidTimelineError, match=rf"^a timeline {problem}$"):
        Timeline(*entries)
```

`packages/kernel/tests/unit/test_events.py`:

```python
from dataclasses import FrozenInstanceError, dataclass
from typing import ClassVar, final
from uuid import uuid7

import pytest

from ledger_kernel.events import Event, InvalidEventClassError


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Renamed(Event):
    TYPE: ClassVar[str] = "probe.thing.renamed"
    VERSION: ClassVar[int] = 1
    name: str


def test_an_event_carries_its_envelope_fields_and_its_payload() -> None:
    subject = uuid7()
    event = Renamed(scope=None, subject=subject, aggregate_version=3, name="x")
    assert (event.TYPE, event.VERSION, event.subject, event.aggregate_version, event.name) == (
        "probe.thing.renamed", 1, subject, 3, "x",
    )
    with pytest.raises(FrozenInstanceError):
        event.name = "y"  # frozen: a fact does not change


def test_an_event_class_without_a_type_is_refused_when_it_is_declared() -> None:
    with pytest.raises(InvalidEventClassError, match="Untyped"):

        @dataclass(frozen=True, slots=True, kw_only=True)
        class Untyped(Event):
            VERSION: ClassVar[int] = 1


@pytest.mark.parametrize(("type_name", "version"), [("renamed", 1), ("probe.Thing.renamed", 1), ("probe.thing.renamed", 0)])
def test_an_event_class_with_a_bad_type_or_version_is_refused(type_name: str, version: int) -> None:
    with pytest.raises(InvalidEventClassError):
        type("Bad", (Event,), {"TYPE": type_name, "VERSION": version, "__annotations__": {}})
```

`packages/kernel/tests/unit/test_regimes.py` (the calculator's laws are hypothesis properties; the fixtures build a synthetic regime, never the Dominican one):

```python
from datetime import date
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ledger_kernel.dated import Since, Timeline
from ledger_kernel.jurisdiction import CountryCode, Jurisdiction
from ledger_kernel.money import Currency, Money, RoundingMode
from ledger_kernel.regimes import (
    CategoryTotal,
    Direction,
    DocumentKind,
    DocumentTypeDef,
    InvalidRegimeError,
    Locale,
    RateValue,
    RegimeDef,
    RegimeKey,
    RoundingPoint,
    RoundingRule,
    Source,
    TaxCalculator,
    TaxCategoryDef,
    TaxCategoryKind,
    TaxCategoryUnknownError,
    TaxIdSchemeDef,
    TaxIdSchemes,
    TaxIdSchemeUnknownError,
    TaxLine,
)
from ledger_kernel.tax_ids import InvalidTaxIdError, TaxId, TaxIdCheck, TaxIdScheme

SOURCE = Source(title="Probe tax law", url="https://law.invalid/probe", version="1", retrieved_on=date(2026, 10, 13))
USD = Currency.of("USD")
LABELS = {Locale("en"): "Probe"}


def digits_check(value: str) -> TaxIdCheck:
    if len(value) != 4 or not value.isascii() or not value.isdigit():
        return TaxIdCheck.INVALID_FORMAT
    return TaxIdCheck.VALID if sum(map(int, value)) % 2 == 0 else TaxIdCheck.CHECKSUM_MISMATCH


def scheme(*, enforced: bool) -> TaxIdSchemeDef:
    return TaxIdSchemeDef(
        key=TaxIdScheme("us.probe"),
        country=CountryCode("US"),
        labels=LABELS,
        normalize=lambda text: text.replace("-", ""),
        check=digits_check,
        check_digit_enforced=enforced,
    )


def category(key: str, percent: str) -> TaxCategoryDef:
    return TaxCategoryDef(
        key=key,
        labels=LABELS,
        kind=TaxCategoryKind.STANDARD,
        rates=Timeline(Since(date(2020, 1, 1), RateValue(percent=Decimal(percent), source=SOURCE))),
    )


def regime(*, points: frozenset[RoundingPoint] = frozenset({RoundingPoint.LINE}), **changes: object) -> RegimeDef:
    values: dict[str, object] = {
        "key": RegimeKey("us"),
        "jurisdiction": Jurisdiction(CountryCode("US")),
        "currency": USD,
        "time_zone": "America/New_York",
        "labels": LABELS,
        "tax_id_schemes": (scheme(enforced=False),),
        "tax_categories": (category("us.sales.standard", "18"), category("us.sales.reduced", "16")),
        "document_types": (
            DocumentTypeDef(code="1", labels=LABELS, kind=DocumentKind.INVOICE, direction=Direction.SALES, requires_buyer_tax_id=False),
            DocumentTypeDef(code="2", labels=LABELS, kind=DocumentKind.CREDIT_NOTE, direction=Direction.SALES, requires_buyer_tax_id=False, corrects=("1",)),
        ),
        "rounding": RoundingRule(mode=RoundingMode.HALF_UP, points=points, places=2, line_tolerance=Decimal("0.01")),
        "fields": (),
        "parameters": {},
        "sources": (SOURCE,),
    } | changes
    return RegimeDef(**values)  # the test builds every variant from one dict


def test_a_checksum_mismatch_is_a_warning_unless_the_scheme_enforces_it() -> None:
    lenient = TaxIdSchemes((scheme(enforced=False),))
    parsed = lenient.parse("us.probe", "12-35")
    assert parsed.tax_id == TaxId(TaxIdScheme("us.probe"), "1235")
    assert parsed.check is TaxIdCheck.CHECKSUM_MISMATCH
    with pytest.raises(InvalidTaxIdError):
        TaxIdSchemes((scheme(enforced=True),)).parse("us.probe", "1235")


def test_a_bad_format_and_an_unknown_scheme_are_refused() -> None:
    schemes = TaxIdSchemes((scheme(enforced=False),))
    with pytest.raises(InvalidTaxIdError):
        schemes.parse("us.probe", "12a4")
    with pytest.raises(InvalidTaxIdError):
        schemes.parse("us.probe", "1" * 41)
    with pytest.raises(TaxIdSchemeUnknownError):
        schemes.parse("us.other", "1234")


@pytest.mark.parametrize(
    ("changes", "problem"),
    [
        ({"key": RegimeKey("usa")}, "has a key that is not"),
        ({"key": RegimeKey("mx")}, "is keyed outside its jurisdiction"),
        ({"time_zone": "not a zone"}, "has a time zone that is not an IANA name"),
        ({"labels": {}}, "has no label"),
        ({"sources": ()}, "names no source"),
        ({"tax_id_schemes": (scheme(enforced=False), scheme(enforced=True))}, "declares tax id scheme 'us.probe' twice"),
        ({"tax_categories": (category("mx.sales.standard", "1"),)}, "declares a tax category outside its key"),
        ({"tax_categories": (category("us.sales.standard", "1"), category("us.sales.standard", "2"))}, "declares tax category 'us.sales.standard' twice"),
        ({"document_types": (DocumentTypeDef(code="2", labels=LABELS, kind=DocumentKind.CREDIT_NOTE, direction=Direction.SALES, requires_buyer_tax_id=False, corrects=("9",)),)}, "has document type '2' correcting an unknown type"),
    ],
)
def test_a_regime_that_contradicts_itself_is_refused(changes: dict[str, object], problem: str) -> None:
    with pytest.raises(InvalidRegimeError, match=problem):
        regime(**changes)


def test_totals_round_where_the_regime_says() -> None:
    lines = [TaxLine(base=Money.of("10.05", USD), category="us.sales.standard")] * 3
    by_line = TaxCalculator(regime(points=frozenset({RoundingPoint.LINE}))).totals(lines, date(2026, 11, 2))
    by_document = TaxCalculator(regime(points=frozenset({RoundingPoint.DOCUMENT}))).totals(lines, date(2026, 11, 2))
    assert by_line.tax == Money.of("5.43", USD)  # 1.809 → 1.81, three times
    assert by_document.tax == Money.of("5.43", USD)  # 5.427 → 5.43
    assert by_line.categories == (
        CategoryTotal(category="us.sales.standard", percent=Decimal(18), base=Money.of("30.15", USD), tax=Money.of("5.43", USD)),
    )


def test_an_unknown_category_is_refused() -> None:
    with pytest.raises(TaxCategoryUnknownError):
        TaxCalculator(regime()).totals([TaxLine(base=Money.of("1", USD), category="us.sales.none")], date(2026, 1, 1))


def test_no_lines_total_nothing_in_the_regime_currency() -> None:
    totals = TaxCalculator(regime()).totals([], date(2026, 1, 1))
    assert (totals.net, totals.tax, totals.gross, totals.categories) == (Money.zero(USD), Money.zero(USD), Money.zero(USD), ())


amounts = st.decimals(min_value=Decimal(0), max_value=Decimal(10**9), places=2).map(lambda value: Money(value, USD))
lines = st.lists(st.builds(TaxLine, base=amounts, category=st.sampled_from(["us.sales.standard", "us.sales.reduced"])), max_size=30)
points = st.frozensets(st.sampled_from(list(RoundingPoint)))


@given(lines, points)
def test_the_total_is_the_sum_of_its_parts(items: list[TaxLine], rounding: frozenset[RoundingPoint]) -> None:
    totals = TaxCalculator(regime(points=rounding)).totals(items, date(2026, 1, 1))
    assert totals.gross == totals.net + totals.tax
    assert totals.net == sum((category.base for category in totals.categories), start=Money.zero(USD))


@given(lines, points, st.permutations(range(30)))
def test_the_order_of_the_lines_never_changes_the_totals(
    items: list[TaxLine], rounding: frozenset[RoundingPoint], order: list[int]
) -> None:
    calculator = TaxCalculator(regime(points=rounding))
    shuffled = [items[index] for index in order if index < len(items)]
    assert calculator.totals(items, date(2026, 1, 1)) == calculator.totals(shuffled, date(2026, 1, 1))
```

(`regime(**changes)` builds `RegimeDef(**values)` from a `dict[str, object]`: the checkers refuse that spread. Write `regime` with explicit keyword parameters defaulting to the fixture values instead — one parameter per field the parametrized test changes — so every call is typed.)

Append to `test_money.py` (and replace every `Currency.DOP`/`Currency.USD`/`Currency.EUR` in it with `DOP = Currency.of("DOP")` etc. at module level):

```python
from ledger_kernel.money import CURRENCIES, Currency, InvalidCurrencyError, Money, RoundingMode


@pytest.mark.parametrize(
    ("code", "numeric", "minor_units"),
    [("DOP", 214, 2), ("USD", 840, 2), ("EUR", 978, 2), ("JPY", 392, 0), ("BHD", 48, 3), ("CLF", 990, 4), ("XCG", 532, 2)],
)
def test_the_iso_4217_table_has_each_currency_minor_units(code: str, numeric: int, minor_units: int) -> None:
    assert Currency.of(code) == Currency(code, numeric, minor_units)


def test_codes_and_numbers_are_unique_and_withdrawn_codes_absent() -> None:
    assert len({currency.numeric for currency in CURRENCIES.values()}) == len(CURRENCIES)
    assert {"ANG", "HRK", "SLL", "ZWL", "CUC"}.isdisjoint(CURRENCIES)


@pytest.mark.parametrize("code", ["dop", "XXX", "XAU", "", 214])
def test_anything_else_is_not_a_currency(code: object) -> None:
    with pytest.raises(InvalidCurrencyError):
        Currency.of(code)


def test_a_currency_not_from_the_table_cannot_make_money() -> None:
    with pytest.raises(InvalidCurrencyError):
        Money.of("1", Currency("DOP", 214, 3))


def test_rounding_follows_the_currency_minor_unit() -> None:
    assert Money.of("1.5", Currency.of("JPY")).rounded() == Money.of("2", Currency.of("JPY"))
    assert Money.of("1.0005", Currency.of("BHD")).rounded() == Money.of("1.001", Currency.of("BHD"))


@pytest.mark.parametrize(
    ("mode", "expected"),
    [(RoundingMode.HALF_UP, "2.13"), (RoundingMode.HALF_EVEN, "2.12"), (RoundingMode.DOWN, "2.12"), (RoundingMode.UP, "2.13")],
)
def test_quantized_rounds_with_the_mode_asked(mode: RoundingMode, expected: str) -> None:
    assert Money.of("2.125", DOP).quantized(2, mode) == Money.of(expected, DOP)


@pytest.mark.parametrize("places", [-1, 25, True])
def test_quantized_refuses_impossible_places(places: int) -> None:
    with pytest.raises(InvalidValueError):
        Money.of("1", DOP).quantized(places, RoundingMode.HALF_UP)
```

In `test_errors.py` add `InvalidValueError` to the INTERNAL rows (code `invalid_value`, message `"x must be y"` for `InvalidValueError(field="x", requirement="y")`). In `test_access.py` replace `InvalidPrincipalError` with `InvalidValueError` (the message becomes `"principal.roles must be a frozenset of text"`; see Step 3).

- [ ] **Step 3: Run the tests to see them fail**

Run: `uv run pytest packages/kernel -q -p no:randomly`
Expected: collection errors for the new modules (`ModuleNotFoundError: No module named 'ledger_kernel.tenancy'`, …) and failures in `test_money.py`.

- [ ] **Step 4: Implement**

`errors.py` — append after `UnavailableError`:

```python
class InvalidValueError(LedgerError):
    """A value our own code built from parts that do not fit: a defect of that code, never the client's input.

    It stays ``INTERNAL`` on purpose: it crosses a boundary as a server error. The message names the field and
    what it must be, never the value (``Principal``, ``TaxId``, ``Jurisdiction`` and the regime types raise it).
    """

    code: ClassVar[str] = "invalid_value"

    def __init__(self, *, field: str, requirement: str) -> None:
        super().__init__(f"{field} must be {requirement}")
```

`access.py`: delete `InvalidPrincipalError`; `Principal.__post_init__` raises `InvalidValueError(field="principal.roles", requirement="a frozenset of text")` and `InvalidValueError(field="principal.provider_organization", requirement=...)`. Run `git grep -n InvalidPrincipalError` across the repo; every hit outside the kernel is a test that names the class: update it to `InvalidValueError` (this is the only edit outside `packages/kernel` in this task).

`tenancy.py`:

```python
"""Whose data a unit of work, a request or an event is about (§6.3, ADR 0007).

A tenant is a company of an organization in one mode. ``mode=None`` reaches only what both modes share (the
company itself, its establishments, its memberships): row-level security compares a moded row's mode with the
unit's, and a unit without one matches none (fail closed). An organization scope reaches the organization's own
rows, in a mode (API clients, idempotency keys) or in none (its companies).
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import final
lazy from ledger_kernel.ids import CompanyId, OrganizationId


class Mode(StrEnum):
    """Test or live: every moded row, credential and request is one of them, never both."""

    TEST = "test"
    LIVE = "live"


@final
@dataclass(frozen=True, slots=True)
class Tenant:
    """One company of an organization, in ``mode`` (``None``: only what both modes share)."""

    organization: OrganizationId
    company: CompanyId
    mode: Mode | None


@final
@dataclass(frozen=True, slots=True)
class OrganizationScope:
    """An organization's own rows, in ``mode`` (``None``: only what both modes share)."""

    organization: OrganizationId
    mode: Mode | None

    @property
    def company(self) -> None:
        """An organization scope reaches no company's rows."""
        return None


type Scope = Tenant | OrganizationScope
"""Every scope answers ``organization``, ``company`` (``None`` for an organization) and ``mode``."""
```

`money.py` — replace the `Currency` enum and `_MINOR_UNIT` with the table, and add `RoundingMode` and `quantized`:

```python
class RoundingMode(StrEnum):
    """How an amount is rounded when a regime asks for it (``RoundingRule``)."""

    HALF_UP = "half_up"
    HALF_EVEN = "half_even"
    DOWN = "down"
    UP = "up"


_DECIMAL_MODES: Final[Mapping[RoundingMode, str]] = MappingProxyType(
    {
        RoundingMode.HALF_UP: ROUND_HALF_UP,
        RoundingMode.HALF_EVEN: ROUND_HALF_EVEN,
        RoundingMode.DOWN: ROUND_DOWN,
        RoundingMode.UP: ROUND_UP,
    }
)

# ISO 4217 list A (current currencies and funds that have minor units), as amended to 2026: code, number, minor
# units. Withdrawn: ANG (→ XCG, 2025), HRK (→ EUR, 2023), SLL (→ SLE), ZWL (→ ZWG, 2024), CUC (2021), BGN (→ EUR,
# 2026). Codes without a minor unit (XAU, XAG, XPD, XPT, XDR, XSU, XUA, XBA-XBD, XTS, XXX) are not money here.
# Verify against SIX's list one (https://www.six-group.com/en/products-services/financial-information/data-standards.html)
# when it is amended; a test pins the entries the platform depends on.
_ISO_4217: Final = """
AED 784 2  AFN 971 2  ALL 008 2  AMD 051 2  AOA 973 2  ARS 032 2  AUD 036 2  AWG 533 2  AZN 944 2  BAM 977 2
BBD 052 2  BDT 050 2  BHD 048 3  BIF 108 0  BMD 060 2  BND 096 2  BOB 068 2  BOV 984 2  BRL 986 2  BSD 044 2
BTN 064 2  BWP 072 2  BYN 933 2  BZD 084 2  CAD 124 2  CDF 976 2  CHE 947 2  CHF 756 2  CHW 948 2  CLF 990 4
CLP 152 0  CNY 156 2  COP 170 2  COU 970 2  CRC 188 2  CUP 192 2  CVE 132 2  CZK 203 2  DJF 262 0  DKK 208 2
DOP 214 2  DZD 012 2  EGP 818 2  ERN 232 2  ETB 230 2  EUR 978 2  FJD 242 2  FKP 238 2  GBP 826 2  GEL 981 2
GHS 936 2  GIP 292 2  GMD 270 2  GNF 324 0  GTQ 320 2  GYD 328 2  HKD 344 2  HNL 340 2  HTG 332 2  HUF 348 2
IDR 360 2  ILS 376 2  INR 356 2  IQD 368 3  IRR 364 2  ISK 352 0  JMD 388 2  JOD 400 3  JPY 392 0  KES 404 2
KGS 417 2  KHR 116 2  KMF 174 0  KPW 408 2  KRW 410 0  KWD 414 3  KYD 136 2  KZT 398 2  LAK 418 2  LBP 422 2
LKR 144 2  LRD 430 2  LSL 426 2  LYD 434 3  MAD 504 2  MDL 498 2  MGA 969 2  MKD 807 2  MMK 104 2  MNT 496 2
MOP 446 2  MRU 929 2  MUR 480 2  MVR 462 2  MWK 454 2  MXN 484 2  MXV 979 2  MYR 458 2  MZN 943 2  NAD 516 2
NGN 566 2  NIO 558 2  NOK 578 2  NPR 524 2  NZD 554 2  OMR 512 3  PAB 590 2  PEN 604 2  PGK 598 2  PHP 608 2
PKR 586 2  PLN 985 2  PYG 600 0  QAR 634 2  RON 946 2  RSD 941 2  RUB 643 2  RWF 646 0  SAR 682 2  SBD 090 2
SCR 690 2  SDG 938 2  SEK 752 2  SGD 702 2  SHP 654 2  SLE 925 2  SOS 706 2  SRD 968 2  SSP 728 2  STN 930 2
SVC 222 2  SYP 760 2  SZL 748 2  THB 764 2  TJS 972 2  TMT 934 2  TND 788 3  TOP 776 2  TRY 949 2  TTD 780 2
TWD 901 2  TZS 834 2  UAH 980 2  UGX 800 0  USD 840 2  USN 997 2  UYI 940 0  UYU 858 2  UYW 927 4  UZS 860 2
VED 926 2  VES 928 2  VND 704 0  VUV 548 0  WST 882 2  XAF 950 0  XCD 951 2  XCG 532 2  XOF 952 0  XPF 953 0
YER 886 2  ZAR 710 2  ZMW 967 2  ZWG 924 2
"""


@final
@dataclass(frozen=True, slots=True)
class Currency:
    """An ISO 4217 currency: its code, its number and the decimal places of its minor unit (data, not an enum)."""

    code: str
    numeric: int
    minor_units: int

    @classmethod
    def of(cls, code: object) -> Currency:
        """The currency ``code`` names, as ISO 4217 writes it (upper case); ``InvalidCurrencyError`` otherwise."""
        found = CURRENCIES.get(code) if type(code) is str else None
        if found is None:
            raise InvalidCurrencyError(described(code))
        return found

    @override
    def __str__(self) -> str:
        return self.code


def _table(text: str) -> Mapping[str, Currency]:
    words = text.split()
    rows = zip(words[0::3], words[1::3], words[2::3], strict=True)
    return MappingProxyType({code: Currency(code, int(number), int(minor)) for code, number, minor in rows})


CURRENCIES: Final = _table(_ISO_4217)
"""Every currency money can be in, by code."""
```

`InvalidCurrencyError.__init__` says `f"{value} is not an ISO 4217 currency"` (it receives `described(...)`). `_require_currency(value)` becomes `if type(value) is not Currency or CURRENCIES.get(value.code) != value: raise InvalidCurrencyError(described(value))`. `Money._same` compares with `!=` instead of `is not`. Add the imports `ROUND_DOWN, ROUND_HALF_EVEN, ROUND_UP` (decimal), `MappingProxyType`, `override`. Replace `rounded` and add `quantized`:

```python
    def rounded(self) -> Money:
        """To the currency's minor unit, half away from zero (0.005 → 0.01, -0.005 → -0.01)."""
        return self.quantized(self.currency.minor_units, RoundingMode.HALF_UP)

    def quantized(self, places: int, mode: RoundingMode) -> Money:
        """Rounded to ``places`` decimal places with ``mode``: what a regime's ``RoundingRule`` asks for."""
        if type(places) is not int or not 0 <= places <= MAX_SCALE:
            raise InvalidValueError(field="places", requirement=f"an int from 0 to {MAX_SCALE}")
        context = Context(prec=_PRECISION, rounding=_DECIMAL_MODES[mode], traps=list(_FAILURES))
        return Money(_operate(context.quantize, self.amount, _WHOLE.scaleb(-places)), self.currency)
```

Delete `_ROUNDING` (now built per mode).

`jurisdiction.py`:

```python
"""Where a company is: a country (ISO 3166-1 alpha-2) and, where law differs inside it, a subdivision (ISO 3166-2)."""

import re
from dataclasses import dataclass
from typing import ClassVar, Final, NewType, final

from ledger_kernel.errors import InvalidInputError, InvalidValueError, described

CountryCode = NewType("CountryCode", str)
"""An assigned ISO 3166-1 alpha-2 code, upper case (``DO``)."""

_ASSIGNED: Final = frozenset(
    """
    AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ
    CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO
    FR GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE
    JM JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO
    MP MQ MR MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW
    PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM
    TN TO TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW
    """.split()
)
"""The 249 officially assigned codes (ISO 3166-1, 2026)."""
_SUBDIVISION: Final = re.compile(r"[A-Z]{2}-[A-Z0-9]{1,3}", re.ASCII)


class InvalidCountryError(InvalidInputError):
    """Text that is not an assigned ISO 3166-1 alpha-2 code."""

    code: ClassVar[str] = "invalid_country"

    def __init__(self, value: object) -> None:
        super().__init__(f"not an ISO 3166-1 alpha-2 country code: {described(value)}")


class InvalidSubdivisionError(InvalidInputError):
    """Text that is not an ISO 3166-2 code of the country it was given with."""

    code: ClassVar[str] = "invalid_subdivision"

    def __init__(self, value: object) -> None:
        super().__init__(f"not an ISO 3166-2 subdivision of the country: {described(value)}")


def parse_country(text: object) -> CountryCode:
    """Read a country code; anything but an assigned upper-case alpha-2 code is an ``InvalidCountryError``."""
    if type(text) is not str or text not in _ASSIGNED:
        raise InvalidCountryError(text)
    return CountryCode(text)


def _is_subdivision_of(country: str, subdivision: object) -> bool:
    return (
        type(subdivision) is str
        and _SUBDIVISION.fullmatch(subdivision) is not None
        and subdivision.startswith(f"{country}-")
    )


@final
@dataclass(frozen=True, slots=True)
class Jurisdiction:
    """A country and, where law differs inside it, one of its subdivisions."""

    country: CountryCode
    subdivision: str | None = None

    def __post_init__(self) -> None:
        if self.country not in _ASSIGNED:
            raise InvalidValueError(field="jurisdiction.country", requirement="an assigned ISO 3166-1 alpha-2 code")
        if self.subdivision is not None and not _is_subdivision_of(self.country, self.subdivision):
            raise InvalidValueError(field="jurisdiction.subdivision", requirement="an ISO 3166-2 code of its country")


def parse_jurisdiction(country: object, subdivision: object = None) -> Jurisdiction:
    """Read a jurisdiction sent by a client: errors are the client's (``InvalidInputError``)."""
    code = parse_country(country)
    if subdivision is None:
        return Jurisdiction(code)
    if not _is_subdivision_of(code, subdivision) or type(subdivision) is not str:
        raise InvalidSubdivisionError(subdivision)
    return Jurisdiction(code, subdivision)
```

`tax_ids.py`:

```python
"""Qualified tax identifiers (§7.3, ADR 0006): a scheme ``<cc>.<name>`` and a normalized value.

Which schemes exist, how a value is normalized and whether its check digit is enforced is regime data
(``ledger_kernel.regimes.TaxIdSchemeDef``); this module holds the value type. A tax id is personal data: ``repr``
shows the scheme and the last three characters, an error the scheme and the problem, never the value.
"""

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar, Final, NewType, final, override

from ledger_kernel.errors import InvalidInputError, InvalidValueError, excerpt

TaxIdScheme = NewType("TaxIdScheme", str)
"""``<cc>.<name>``: ``do.rnc``, ``do.cedula``, ``mx.rfc``, ``eu.vat``."""

MAX_TAX_ID_LENGTH: Final = 40
_SCHEME: Final = re.compile(r"[a-z]{2}\.[a-z][a-z0-9_]{0,30}", re.ASCII)
_SHOWN: Final = 3


class TaxIdCheck(StrEnum):
    """What a scheme's check says of a value."""

    VALID = "valid"
    CHECKSUM_MISMATCH = "checksum_mismatch"
    """Well formed, but the check digit is not the one the algorithm predicts (refused only where enforced)."""
    INVALID_FORMAT = "invalid_format"


class InvalidTaxIdSchemeError(InvalidInputError):
    """Text that is not a scheme key."""

    code: ClassVar[str] = "invalid_tax_id_scheme"

    def __init__(self, text: str) -> None:
        super().__init__(f"not a tax id scheme (<country>.<name>): {excerpt(text)}")


class InvalidTaxIdError(InvalidInputError):
    """A value its scheme refuses; the message names the scheme and the problem, never the value."""

    code: ClassVar[str] = "invalid_tax_id"

    def __init__(self, scheme: TaxIdScheme, problem: TaxIdCheck) -> None:
        super().__init__(f"not a valid {scheme} identifier: {problem}")


def parse_scheme(text: str) -> TaxIdScheme:
    """Read a scheme key: two lower-case letters, a dot, a lower-case name."""
    if _SCHEME.fullmatch(text) is None:
        raise InvalidTaxIdSchemeError(text)
    return TaxIdScheme(text)


def _is_storable(value: object) -> bool:
    return (
        type(value) is str
        and 1 <= len(value) <= MAX_TAX_ID_LENGTH
        and value.isascii()
        and value.isprintable()
        and " " not in value
    )


@final
@dataclass(frozen=True, slots=True, repr=False)
class TaxId:
    """A tax id as its scheme normalized it. Built through ``TaxIdSchemes.parse`` from a client's input."""

    scheme: TaxIdScheme
    value: str

    def __post_init__(self) -> None:
        if type(self.scheme) is not str or _SCHEME.fullmatch(self.scheme) is None:
            raise InvalidValueError(field="tax_id.scheme", requirement="a scheme key")
        if not _is_storable(self.value):
            raise InvalidValueError(
                field="tax_id.value", requirement=f"1 to {MAX_TAX_ID_LENGTH} printable ASCII characters, no space"
            )

    @override
    def __repr__(self) -> str:
        value = self.value
        masked = "***" if len(value) <= _SHOWN else "*" * (len(value) - _SHOWN) + value[-_SHOWN:]
        return f"TaxId(scheme={self.scheme!r}, value={masked!r})"
```

(`str(tax_id)` is the dataclass default, which is `repr`: masked. Documents read `.value`.)

`dated.py`:

```python
"""Values that change on dates (§8.2, rule 12): a rate, a threshold, a limit, as the law set it over time."""

from dataclasses import dataclass
from itertools import pairwise
from typing import ClassVar, final
lazy from datetime import date

from ledger_kernel.errors import InvalidInputError, LedgerError


class InvalidTimelineError(LedgerError):
    """Dated values that contradict themselves: a defect of the regime pack, found when it is built."""

    code: ClassVar[str] = "invalid_timeline"

    def __init__(self, problem: str) -> None:
        super().__init__(f"a timeline {problem}")


class NotInEffectError(InvalidInputError):
    """No value is in effect on the day asked for (before the first, or after one ended)."""

    code: ClassVar[str] = "not_in_effect"

    def __init__(self, day: date) -> None:
        super().__init__(f"no value is in effect on {day.isoformat()}")


@final
@dataclass(frozen=True, slots=True)
class Since[T]:
    """``value`` in effect from ``since`` (inclusive) until ``until`` (exclusive) or the next newer value."""

    since: date
    value: T
    until: date | None = None


@final
class Timeline[T]:
    """Dated values, newest first; ``at(day)`` is the one in effect on ``day``."""

    __slots__ = ("_entries",)

    def __init__(self, *entries: Since[T]) -> None:
        if not entries:
            raise InvalidTimelineError("holds no value")
        for entry in entries:
            if entry.until is not None and entry.until <= entry.since:
                raise InvalidTimelineError("has a value that ends before it starts")
        for newer, older in pairwise(entries):
            if not older.since < newer.since:
                raise InvalidTimelineError("is not newest first")
            if older.until is not None and older.until > newer.since:
                raise InvalidTimelineError("has overlapping values")
        self._entries = entries

    @property
    def entries(self) -> tuple[Since[T], ...]:
        """Every value, newest first."""
        return self._entries

    def at(self, day: date) -> T:
        """The value in effect on ``day``; ``NotInEffectError`` when there is none."""
        for entry in self._entries:
            if entry.since <= day:
                if entry.until is not None and day >= entry.until:
                    raise NotInEffectError(day)
                return entry.value
        raise NotInEffectError(day)
```

`events.py`:

```python
"""The base of every event (§3.5): a fact a module or a channel announces, as a frozen dataclass.

A subclass sets ``TYPE`` (``<module>.<aggregate>.<past_participle>``) and ``VERSION`` and adds its payload as
fields. The envelope fields are here: ``scope`` (whose data; ``None`` for no tenant's), ``subject`` (the
aggregate's id) and ``aggregate_version`` (its version after the change, so projections apply only newer ones).
The platform's codec turns the payload into JSON and ``uow.publish`` writes it to the producer's outbox.
"""

import re
from dataclasses import dataclass
from typing import ClassVar, Final
lazy from uuid import UUID

from ledger_kernel.errors import LedgerError
lazy from ledger_kernel.tenancy import Scope

_TYPE: Final = re.compile(r"[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*", re.ASCII)


class InvalidEventClassError(LedgerError):
    """An event class without a well-formed ``TYPE`` or with a ``VERSION`` below 1: a defect, found at import."""

    code: ClassVar[str] = "invalid_event_class"

    def __init__(self, name: str) -> None:
        super().__init__(f"event class {name} needs TYPE <module>.<aggregate>.<past_participle> and VERSION >= 1")


@dataclass(frozen=True, slots=True, kw_only=True)
class Event:
    """A fact, as the producer states it (see the module)."""

    TYPE: ClassVar[str]
    VERSION: ClassVar[int]
    scope: Scope | None
    subject: UUID | None
    aggregate_version: int | None

    def __init_subclass__(cls) -> None:
        super().__init_subclass__()
        try:
            type_name, version = cls.TYPE, cls.VERSION
        except AttributeError as error:
            raise InvalidEventClassError(cls.__qualname__) from error
        if _TYPE.fullmatch(type_name) is None or version < 1:
            raise InvalidEventClassError(cls.__qualname__)
```

`regimes.py`:

```python
"""The pure regime schema (§8.2, ADR 0005): a jurisdiction's tax law as typed, effective-dated data, and the one
tax calculator.

Standard library only and no loading: ``ledger_platform.runtime.registries.Regimes`` reads packs from entry
points, checks what needs more than the standard library (that the time zone exists) and freezes them. A
``RegimeDef`` refuses, when it is built, duplicate keys, schemes or categories outside its jurisdiction,
corrections that name no document type, and a pack without sources.
"""

import re
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import ClassVar, Final, NewType, final

from ledger_kernel.errors import InvalidInputError, InvalidValueError, LedgerError, excerpt
from ledger_kernel.money import Money
from ledger_kernel.tax_ids import MAX_TAX_ID_LENGTH, InvalidTaxIdError, TaxId, TaxIdCheck, parse_scheme
lazy from collections.abc import Callable, Iterable, Mapping, Sequence
lazy from datetime import date

lazy from ledger_kernel.dated import Timeline
lazy from ledger_kernel.jurisdiction import CountryCode, Jurisdiction
lazy from ledger_kernel.money import Currency, RoundingMode
lazy from ledger_kernel.tax_ids import TaxIdScheme

Locale = NewType("Locale", str)
"""A BCP 47 language tag: ``es``, ``en``, ``es-DO``."""
RegimeKey = NewType("RegimeKey", str)
"""``<cc>`` or ``<cc>-<subdivision>`` when the law differs inside a country: ``do``, ``us-ca``."""

_REGIME_KEY: Final = re.compile(r"[a-z]{2}(?:-[a-z0-9]{1,3})?", re.ASCII)
_TIME_ZONE: Final = re.compile(r"UTC|[A-Za-z]+(?:/[A-Za-z0-9_+-]+){1,2}", re.ASCII)
_ZERO: Final = Decimal(0)
_HUNDRED: Final = Decimal(100)


class InvalidRegimeError(LedgerError):
    """A regime pack that contradicts itself: a defect of the pack, found when it is built."""

    code: ClassVar[str] = "invalid_regime"

    def __init__(self, regime: str, problem: str) -> None:
        super().__init__(f"regime {regime!r} {problem}")


class TaxIdSchemeUnknownError(InvalidInputError):
    """No registered regime declares the scheme a client named."""

    code: ClassVar[str] = "tax_id_scheme_unknown"

    def __init__(self, scheme: str) -> None:
        super().__init__(f"no regime declares the tax id scheme {excerpt(scheme)}")


class TaxCategoryUnknownError(InvalidInputError):
    """A line names a tax category its regime does not declare."""

    code: ClassVar[str] = "tax_category_unknown"

    def __init__(self, category: str) -> None:
        super().__init__(f"the regime declares no tax category {excerpt(category)}")


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Source:
    """Where a regime's values come from: a law, a regulation, an authority's technical document."""

    title: str
    url: str
    version: str
    retrieved_on: date


class TaxCategoryKind(StrEnum):
    """The generic kind of a tax category, for formats (UBL) that speak generically."""

    STANDARD = "standard"
    REDUCED = "reduced"
    ZERO = "zero"
    EXEMPT = "exempt"
    OUTSIDE_SCOPE = "outside_scope"


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class RateValue:
    """A rate in percent and where the law says so."""

    percent: Decimal
    source: Source

    def __post_init__(self) -> None:
        percent = self.percent
        if type(percent) is not Decimal or not percent.is_finite() or not _ZERO <= percent <= _HUNDRED:
            raise InvalidValueError(field="rate.percent", requirement="a finite Decimal from 0 to 100")


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class TaxCategoryDef:
    """A tax category (``<regime>.<tax>.<category>``, e.g. ``do.itbis.standard``) and its dated rates."""

    key: str
    labels: Mapping[Locale, str]
    kind: TaxCategoryKind
    rates: Timeline[RateValue]


class DocumentKind(StrEnum):
    """The commercial kind of a document (§7.5)."""

    INVOICE = "invoice"
    CREDIT_NOTE = "credit_note"
    DEBIT_NOTE = "debit_note"


class Direction(StrEnum):
    """Sales, or a self-billed purchase issued on a supplier's behalf."""

    SALES = "sales"
    PURCHASE = "purchase"


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class DocumentTypeDef:
    """A document type the law defines (DGII's 31-47), and which types it may correct."""

    code: str
    labels: Mapping[Locale, str]
    kind: DocumentKind
    direction: Direction
    requires_buyer_tax_id: bool
    corrects: tuple[str, ...] = ()


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class TaxIdSchemeDef:
    """A tax id scheme: how a value is normalized and checked, and whether a checksum mismatch is refused."""

    key: TaxIdScheme
    country: CountryCode
    labels: Mapping[Locale, str]
    normalize: Callable[[str], str]
    check: Callable[[str], TaxIdCheck]
    check_digit_enforced: bool
    peppol_icd: str | None = None


class FieldKind(StrEnum):
    """What a country field holds on the wire (``fiscal.<regime>.<field>``)."""

    TEXT = "text"
    CODE = "code"
    DATE = "date"
    DECIMAL = "decimal"
    OBJECT = "object"


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class FieldDef:
    """A law-level field a regime declares under ``fiscal.<regime>``."""

    name: str
    labels: Mapping[Locale, str]
    kind: FieldKind
    required: bool = False


class RoundingPoint(StrEnum):
    """Where amounts are rounded: each line's tax, each category's total, the document's total."""

    LINE = "line"
    CATEGORY = "category"
    DOCUMENT = "document"


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class RoundingRule:
    """How and where a regime rounds, and how far a document may stray per line (channels verify with it)."""

    mode: RoundingMode
    points: frozenset[RoundingPoint]
    places: int
    line_tolerance: Decimal


def _unique(regime: str, what: str, keys: Iterable[str]) -> frozenset[str]:
    seen: set[str] = set()
    for key in keys:
        if key in seen:
            raise InvalidRegimeError(regime, f"declares {what} {key!r} twice")
        seen.add(key)
    return frozenset(seen)


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class RegimeDef:
    """A jurisdiction's tax law as data (§8.2)."""

    key: RegimeKey
    jurisdiction: Jurisdiction
    currency: Currency
    time_zone: str
    labels: Mapping[Locale, str]
    tax_id_schemes: tuple[TaxIdSchemeDef, ...]
    tax_categories: tuple[TaxCategoryDef, ...]
    document_types: tuple[DocumentTypeDef, ...]
    rounding: RoundingRule
    fields: tuple[FieldDef, ...]
    parameters: Mapping[str, Timeline[Decimal | int | str]]
    sources: tuple[Source, ...]

    def __post_init__(self) -> None:
        key, country = self.key, self.jurisdiction.country
        if _REGIME_KEY.fullmatch(key) is None:
            raise InvalidRegimeError(key, "has a key that is not <cc> or <cc>-<subdivision>")
        if not key.startswith(country.lower()):
            raise InvalidRegimeError(key, "is keyed outside its jurisdiction")
        if _TIME_ZONE.fullmatch(self.time_zone) is None:
            raise InvalidRegimeError(key, "has a time zone that is not an IANA name")
        if not self.labels:
            raise InvalidRegimeError(key, "has no label")
        if not self.sources:
            raise InvalidRegimeError(key, "names no source")
        _unique(key, "tax id scheme", (scheme.key for scheme in self.tax_id_schemes))
        for scheme in self.tax_id_schemes:
            if scheme.country != country or not scheme.key.startswith(f"{country.lower()}."):
                raise InvalidRegimeError(key, "declares a tax id scheme of another country")
        _unique(key, "tax category", (category.key for category in self.tax_categories))
        for category in self.tax_categories:
            if not category.key.startswith(f"{key}."):
                raise InvalidRegimeError(key, "declares a tax category outside its key")
        codes = _unique(key, "document type", (document.code for document in self.document_types))
        for document in self.document_types:
            if not codes.issuperset(document.corrects):
                raise InvalidRegimeError(key, f"has document type {document.code!r} correcting an unknown type")
        _unique(key, "field", (field_def.name for field_def in self.fields))


@final
@dataclass(frozen=True, slots=True)
class ParsedTaxId:
    """A tax id a scheme accepted, and what its check said (``CHECKSUM_MISMATCH`` is a warning here)."""

    tax_id: TaxId
    check: TaxIdCheck


@final
class TaxIdSchemes:
    """Every scheme of the registered regimes: reads a client's ``{scheme, value}``."""

    __slots__ = ("_by_key",)

    def __init__(self, schemes: Iterable[TaxIdSchemeDef]) -> None:
        by_key: dict[str, TaxIdSchemeDef] = {}
        for scheme in schemes:
            if scheme.key in by_key:
                raise InvalidRegimeError(scheme.key.partition(".")[0], f"declares tax id scheme {scheme.key!r} twice")
            by_key[scheme.key] = scheme
        self._by_key: Mapping[str, TaxIdSchemeDef] = MappingProxyType(by_key)

    def parse(self, scheme: str, value: str) -> ParsedTaxId:
        """Normalize and check ``value`` with ``scheme``'s rules; a refusal never repeats the value."""
        key = parse_scheme(scheme)
        definition = self._by_key.get(key)
        if definition is None:
            raise TaxIdSchemeUnknownError(key)
        if type(value) is not str or len(value) > MAX_TAX_ID_LENGTH:
            raise InvalidTaxIdError(key, TaxIdCheck.INVALID_FORMAT)
        normalized = definition.normalize(value)
        check = definition.check(normalized)
        refused = check is TaxIdCheck.INVALID_FORMAT or (
            check is TaxIdCheck.CHECKSUM_MISMATCH and definition.check_digit_enforced
        )
        if refused:
            raise InvalidTaxIdError(key, check)
        return ParsedTaxId(TaxId(key, normalized), check)


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class TaxLine:
    """A line's taxable base and the category it is taxed in."""

    base: Money
    category: str


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class CategoryTotal:
    """One category's base and tax on a document."""

    category: str
    percent: Decimal
    base: Money
    tax: Money


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Totals:
    """A document's net, its taxes per category, its tax and its gross; ``gross == net + tax``."""

    net: Money
    categories: tuple[CategoryTotal, ...]
    tax: Money
    gross: Money


@final
class TaxCalculator:
    """The one tax calculator (§8.2): invoicing computes with it, channels verify with it."""

    __slots__ = ("_categories", "_regime")

    def __init__(self, regime: RegimeDef) -> None:
        self._regime = regime
        self._categories: Mapping[str, TaxCategoryDef] = MappingProxyType(
            {category.key: category for category in regime.tax_categories}
        )

    def totals(self, lines: Sequence[TaxLine], tax_point: date) -> Totals:
        """The totals of ``lines`` at the rates in effect on ``tax_point``, rounded where the regime says.

        Every line is in one currency (``CurrencyMismatchError`` otherwise); no lines total zero in the regime's.
        Categories are listed by key, so the order of the lines never changes the result.
        """
        rule = self._regime.rounding
        currency = lines[0].base.currency if lines else self._regime.currency
        zero = Money.zero(currency)
        bases: dict[str, Money] = {}
        taxes: dict[str, Money] = {}
        percents: dict[str, Decimal] = {}
        for line in lines:
            category = self._categories.get(line.category)
            if category is None:
                raise TaxCategoryUnknownError(line.category)
            percent = category.rates.at(tax_point).percent
            tax = line.base * percent.scaleb(-2)
            if RoundingPoint.LINE in rule.points:
                tax = tax.quantized(rule.places, rule.mode)
            bases[category.key] = bases.get(category.key, zero) + line.base
            taxes[category.key] = taxes.get(category.key, zero) + tax
            percents[category.key] = percent
        categories = tuple(
            CategoryTotal(
                category=key,
                percent=percents[key],
                base=bases[key],
                tax=taxes[key].quantized(rule.places, rule.mode) if RoundingPoint.CATEGORY in rule.points else taxes[key],
            )
            for key in sorted(bases)
        )
        net = sum((category.base for category in categories), start=zero)
        tax_total = sum((category.tax for category in categories), start=zero)
        if RoundingPoint.DOCUMENT in rule.points:
            tax_total = tax_total.quantized(rule.places, rule.mode)
        return Totals(net=net, categories=categories, tax=tax_total, gross=net + tax_total)
```

- [ ] **Step 5: Run the kernel's tests**

Run: `uv run pytest packages/kernel -q`
Expected: all pass; `uv run pytest packages/kernel --cov=ledger_kernel --cov-branch --cov-report=term-missing:skip-covered -q` reports 100 %. Add the test a missing branch asks for (each one is a behaviour: name it).

- [ ] **Step 6: Gate and commit**

Run: `uv run poe check` — expected green (the import-linter "Kernel is standard library only" contract holds: the new modules import nothing outside the standard library).

```bash
git add -A packages/kernel $(git grep -l InvalidValueError -- 'packages/*/tests' 'modules/*/tests' | tr '\n' ' ')
git commit -m "feat(kernel): tenancy, jurisdictions, qualified tax ids, ISO 4217, dated values, regimes, events

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Platform subpackages by `git mv` (∥ Task 2)

Spec §5.2, §16.1 (`libs/platform` table), audit §4.3, §7.5. Moves only: no behaviour changes. The v1 modules that the chassis replaces (or that are rewritten from scratch) stay flat until Task 16 deletes them: `service`, `migrations`, `schema`, `outbox`, `consumer`, `streams`, `jetstream`, `interruptions`, `identity_client`, `lifecycle`, `idempotency`.

**Files:**
- Move (in `packages/platform/src/ledger_platform/`): `settings.py` → `config/settings.py`; `options.py` → `config/options.py`; `logs.py` → `observability/logs.py`; `tracing.py` → `observability/tracing.py`; `health.py` → `observability/health.py`; `probe.py` → `observability/probe.py`; `supervision.py` → `runtime/supervision.py`; `database.py` → `data/database.py`; `grpc_server.py` → `grpc/server.py`; `grpc_client.py` → `grpc/client.py`; `authentication.py` → `auth/tokens.py`; `authorization.py` → `auth/access.py`; `web.py` → `http/app.py`; `routes.py` → `http/deps.py`; `request_fields.py` → `http/request_fields.py`
- Create: `config/__init__.py`, `observability/__init__.py`, `runtime/__init__.py`, `data/__init__.py`, `grpc/__init__.py`, `auth/__init__.py`, `http/__init__.py` (a one-line docstring each; their `__all__` is curated in Task 16)
- Move (tests, in `packages/platform/tests/`): unit → `tests/unit/`, integration → `tests/integration/` (Step 3)
- Modify: every importer (`packages/**`, `modules/**`, `contracts/**` `.py` files), `packages/platform/pyproject.toml` (`ledger-probe` target), root `pyproject.toml` (`runtime-evaluated-base-classes`, the platform layers contract)

**Interfaces:**
- Consumes: nothing new.
- Produces: the module paths later tasks import: `ledger_platform.config.{settings,options}`, `ledger_platform.observability.{logs,tracing,health,probe}`, `ledger_platform.runtime.supervision`, `ledger_platform.data.database`, `ledger_platform.grpc.{server,client}`, `ledger_platform.auth.{tokens,access}`, `ledger_platform.http.{app,deps,request_fields}`. Every name inside them is unchanged.

- [ ] **Step 1: Move the modules**

```bash
cd /c/Users/brian/Desktop/ledger/packages/platform/src/ledger_platform
mkdir -p config observability runtime data grpc auth http
git mv settings.py config/settings.py && git mv options.py config/options.py
git mv logs.py observability/logs.py && git mv tracing.py observability/tracing.py
git mv health.py observability/health.py && git mv probe.py observability/probe.py
git mv supervision.py runtime/supervision.py
git mv database.py data/database.py
git mv grpc_server.py grpc/server.py && git mv grpc_client.py grpc/client.py
git mv authentication.py auth/tokens.py && git mv authorization.py auth/access.py
git mv web.py http/app.py && git mv routes.py http/deps.py && git mv request_fields.py http/request_fields.py
for d in config observability runtime data grpc auth http; do test -f $d/__init__.py || : > $d/__init__.py; done
```

Write each `__init__.py` as one docstring: `"""Configuration: settings from LEDGER_* variables and option checks."""`, `"""Logs, traces, metrics and health."""`, `"""Running an app: supervision of its workers (composition arrives in Task 12)."""`, `"""PostgreSQL: pools, units of work, row-level security, migrations."""`, `"""gRPC for cross-process surfaces (the signer)."""`, `"""Who calls: bearer tokens, keys, access to a company."""`, `"""The HTTP edge: the app, problems, dependencies."""`.

- [ ] **Step 2: Rewrite the imports**

```bash
cd /c/Users/brian/Desktop/ledger
git ls-files '*.py' '*.pyi' 'pyproject.toml' 'packages/*/pyproject.toml' 'modules/*/pyproject.toml' | xargs sed -i -E \
  -e 's/\bledger_platform\.settings\b/ledger_platform.config.settings/g' \
  -e 's/\bledger_platform\.options\b/ledger_platform.config.options/g' \
  -e 's/\bledger_platform\.logs\b/ledger_platform.observability.logs/g' \
  -e 's/\bledger_platform\.tracing\b/ledger_platform.observability.tracing/g' \
  -e 's/\bledger_platform\.health\b/ledger_platform.observability.health/g' \
  -e 's/\bledger_platform\.probe\b/ledger_platform.observability.probe/g' \
  -e 's/\bledger_platform\.supervision\b/ledger_platform.runtime.supervision/g' \
  -e 's/\bledger_platform\.database\b/ledger_platform.data.database/g' \
  -e 's/\bledger_platform\.grpc_server\b/ledger_platform.grpc.server/g' \
  -e 's/\bledger_platform\.grpc_client\b/ledger_platform.grpc.client/g' \
  -e 's/\bledger_platform\.authentication\b/ledger_platform.auth.tokens/g' \
  -e 's/\bledger_platform\.authorization\b/ledger_platform.auth.access/g' \
  -e 's/\bledger_platform\.web\b/ledger_platform.http.app/g' \
  -e 's/\bledger_platform\.routes\b/ledger_platform.http.deps/g' \
  -e 's/\bledger_platform\.request_fields\b/ledger_platform.http.request_fields/g'
git grep -n "from ledger_platform import" -- '*.py'   # expected: nothing; fix any hit by hand to the new module path
git grep -nE "ledger_platform\.(settings|options|logs|tracing|health|probe|supervision|database|grpc_server|grpc_client|authentication|authorization|web|routes|request_fields)\b"   # expected: nothing
```

The sed also rewrites string literals (monkeypatch targets, logger names asserted by tests, `ledger-probe = "ledger_platform.observability.probe:main"`, `runtime-evaluated-base-classes` → `"ledger_platform.data.database.PlatformTable"`). Run `uv sync --all-packages` so the console script points at the new module.

- [ ] **Step 3: Split the platform's tests into unit and integration**

```bash
cd /c/Users/brian/Desktop/ledger/packages/platform/tests
mkdir -p unit integration
grep -lE "\b(database|database_url|fresh_database_url|postgres|nats_url|jetstream|connect|small_payload_nats_url)\b" test_*.py   # the integration ones
```

Move each file listed by the `grep` with `git mv test_x.py integration/test_x.py`, every other `test_*.py` with `git mv test_x.py unit/test_x.py`, renaming as you go: `test_web.py` → `unit/test_http_app.py`, `test_routes.py` → `unit/test_http_deps.py`, `test_authentication.py` → `unit/test_auth_tokens.py`, `test_authorization.py` → `unit/test_auth_access.py`. `conftest.py` stays in `tests/`, shared by both. Report which files went where.

- [ ] **Step 4: Replace the platform layers contract**

In root `pyproject.toml`, the contract `Platform layers: service -> adapters -> messaging -> infrastructure -> foundations` becomes (subpackages are the layer units; the flat v1 modules keep their places until Task 16):

```toml
# Inside the platform, top to bottom. Subpackages are layers; the v1 modules that Task 16 deletes keep the place
# they had. Peers (`|`) never import each other. Exhaustive: a new module or subpackage must be given its layer.
[[tool.importlinter.contracts]]
name = "Platform layers"
type = "layers"
containers = ["ledger_platform"]
exhaustive = true
layers = [
    "service | migrations",
    "schema",
    "http | consumer | identity_client",
    "outbox | idempotency | auth | grpc",
    "streams",
    "data | observability | jetstream",
    "config | runtime | lifecycle | interruptions",
]
```

- [ ] **Step 5: Gate**

Run: `uv run poe check`
Expected: green, with the same number of tests as before (`pytest --collect-only -q | tail -1` before and after).

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "refactor(platform): config, observability, runtime, data, grpc, auth and http subpackages

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Tenancy, scopes and public ids everywhere (∥ Task 5)

Spec §5.1 (`tenancy`, `ids`), §6.6, audit D14. After Tasks 2 and 3. Ends the four hand-written `match` copies on `Scope` and the kernel's v1 `Tenant`.

**Files:**
- Modify: `packages/kernel/src/ledger_kernel/ids.py` (delete `Tenant`; rename `parse_id` → `parse_uuid`; add the TypeID codec), `packages/platform/src/ledger_platform/data/database.py` (`Scope` from the kernel; `SET_SCOPE` sets `ledger.mode`), `packages/platform/src/ledger_platform/idempotency.py`, `packages/platform/src/ledger_platform/migrations.py`, `packages/platform/src/ledger_platform/auth/access.py`, `packages/platform/src/ledger_platform/http/deps.py`, `modules/identity/src/ledger_identity/**` (every `Tenant`, `parse_id` and organization-as-scope call site), their tests
- Test: `packages/kernel/tests/unit/test_ids.py` (TypeID), platform `tests/integration/test_database.py` (mode setting)

**Interfaces:**
- Consumes: `ledger_kernel.tenancy.{Mode, Tenant, OrganizationScope, Scope}` (Task 2).
- Produces: `ledger_kernel.ids.parse_uuid(text: str) -> UUID` (the v1 `parse_id`, renamed; `InvalidIdError` unchanged); `ledger_kernel.ids.format_id(prefix: str, value: UUID) -> str` and `ledger_kernel.ids.parse_id(prefix: str, text: str) -> UUID` (TypeID: `<prefix>_<26 Crockford base32 characters>`, lower case; prefix `[a-z]{2,5}`), `InvalidPublicIdError(prefix, text)` (code `invalid_public_id`, kind `INVALID`; the HTTP edge turns it into 404 in v2-M2), `InvalidIdPrefixError(prefix)` (internal, code `invalid_id_prefix`). `ledger_platform.data.database.Database.transaction(scope: Scope | None)` takes the kernel `Scope`; `scope_settings(scope) -> dict[str, str]` returns `organization`, `company`, `mode` (empty string when absent). `CompanyAccess.tenant` is a kernel `Tenant` with `mode=None`.

- [ ] **Step 1: Rename the v1 reader first (mechanical, stays green)**

```bash
cd /c/Users/brian/Desktop/ledger
git grep -ln "\bparse_id\b" -- '*.py' | xargs sed -i -E 's/\bparse_id\b/parse_uuid/g'
uv run pytest -q -p no:randomly 2>&1 | tail -1   # expected: everything passes, as before
```

- [ ] **Step 2: Write the failing tests**

`packages/kernel/tests/unit/test_ids.py` — add:

```python
from uuid import UUID, uuid7

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ledger_kernel.ids import InvalidIdPrefixError, InvalidPublicIdError, format_id, parse_id, parse_uuid


def test_a_public_id_is_the_typeid_of_the_uuid() -> None:
    value = UUID("01890a5d-ac96-774b-bcce-b302099a8057")
    assert format_id("inv", value) == "inv_01h455vb4pex5vsknk084sn02q"  # the TypeID specification's own example
    assert parse_id("inv", "inv_01h455vb4pex5vsknk084sn02q") == value


@given(st.uuids())
def test_parse_reads_back_what_format_wrote(value: UUID) -> None:
    assert parse_id("cmp", format_id("cmp", value)) == value


@pytest.mark.parametrize(
    "text",
    [
        "cmp_01h455vb4pex5vsknk084sn02q",   # another prefix
        "inv01h455vb4pex5vsknk084sn02q",    # no separator
        "inv_01h455vb4pex5vsknk084sn02",    # 25 characters
        "inv_81h455vb4pex5vsknk084sn02q",   # first character above 7: more than 128 bits
        "inv_01H455VB4PEX5VSKNK084SN02Q",   # upper case
        "inv_01h455vb4pex5vsknk084sn0iq",   # 'i' is not in the alphabet
        "",
    ],
)
def test_anything_else_is_not_an_id_of_the_prefix(text: str) -> None:
    with pytest.raises(InvalidPublicIdError) as caught:
        parse_id("inv", text)
    assert caught.value.code == "invalid_public_id"


@pytest.mark.parametrize("prefix", ["i", "invoice", "Inv", "in_v", "1nv"])
def test_a_prefix_outside_the_naming_rule_is_a_defect(prefix: str) -> None:
    with pytest.raises(InvalidIdPrefixError):
        format_id(prefix, uuid7())


def test_a_uuid_in_canonical_form_still_reads() -> None:
    value = uuid7()
    assert parse_uuid(str(value)) == value
```

(The expected TypeID string is the TypeID specification's documented example for that UUID; confirm it from the spec's test vectors at `https://github.com/jetify-com/typeid/blob/main/spec/valid.yml` when online, and if the string differs, the specification wins: fix the test, report it.)

In platform `tests/integration/test_database.py` add a test that the unit sees its mode:

```python
async def test_a_transaction_sets_the_mode_of_its_scope(database: Database) -> None:
    scope = Tenant(OrganizationId(new_id()), CompanyId(new_id()), Mode.LIVE)
    async with database.transaction(scope) as unit:
        mode = (await unit.connection.execute(text("SELECT current_setting('ledger.mode', true)"))).scalar_one()
    assert mode == "live"
```

- [ ] **Step 3: Run them to see them fail**

Run: `uv run pytest packages/kernel/tests/unit/test_ids.py packages/platform/tests/integration/test_database.py -q -p no:randomly`
Expected: `ImportError` (`format_id`, `InvalidPublicIdError`) and the mode test failing (`''`).

- [ ] **Step 4: Implement the codec and switch every user**

`ids.py` — delete `Tenant` and its imports; add:

```python
_PREFIX: Final = re.compile(r"[a-z]{2,5}", re.ASCII)
_ALPHABET: Final = "0123456789abcdefghjkmnpqrstvwxyz"
"""Crockford's base32, lower case: TypeID's alphabet."""
_DIGITS: Final = {character: value for value, character in enumerate(_ALPHABET)}
_SUFFIX_LENGTH: Final = 26
_HIGHEST_FIRST: Final = "7"
"""26 characters hold 130 bits; a UUID has 128, so the first character is at most 7."""


class InvalidIdPrefixError(LedgerError):
    """A public id prefix outside the naming rule (2-5 lower-case letters): a defect of the code that names it."""

    code: ClassVar[str] = "invalid_id_prefix"

    def __init__(self, prefix: str) -> None:
        super().__init__(f"a public id prefix is 2 to 5 lower-case letters, not {excerpt(prefix)}")


class InvalidPublicIdError(InvalidInputError):
    """Text that is not a public id of the expected type."""

    code: ClassVar[str] = "invalid_public_id"

    def __init__(self, prefix: str, text: str) -> None:
        super().__init__(f"not a {prefix}_ id: {excerpt(text)}")


def _require_prefix(prefix: str) -> None:
    if _PREFIX.fullmatch(prefix) is None:
        raise InvalidIdPrefixError(prefix)


def format_id(prefix: str, value: UUID) -> str:
    """The public id (TypeID) of ``value`` with ``prefix``: ``inv_01h455vb4pex5vsknk084sn02q``."""
    _require_prefix(prefix)
    number = value.int
    characters = [""] * _SUFFIX_LENGTH
    for index in range(_SUFFIX_LENGTH - 1, -1, -1):
        characters[index] = _ALPHABET[number & 0b11111]
        number >>= 5
    return f"{prefix}_{''.join(characters)}"


def parse_id(prefix: str, text: str) -> UUID:
    """The UUID inside a public id of type ``prefix``; anything else is an ``InvalidPublicIdError``."""
    _require_prefix(prefix)
    head = f"{prefix}_"
    suffix = text.removeprefix(head)
    if (
        suffix is text
        or len(suffix) != _SUFFIX_LENGTH
        or suffix[0] > _HIGHEST_FIRST
        or not all(character in _DIGITS for character in suffix)
    ):
        raise InvalidPublicIdError(prefix, text)
    number = 0
    for character in suffix:
        number = number << 5 | _DIGITS[character]
    return UUID(int=number)
```

(`suffix is text`: `removeprefix` returns the same object when nothing was removed. If a checker objects to the identity test on `str`, compare `not text.startswith(head)` instead.)

Then let the type checkers find every call site:

```bash
git grep -n "from ledger_kernel.ids import.*Tenant\|ids\.Tenant"   # every hit: import Tenant from ledger_kernel.tenancy
uv run basedpyright 2>&1 | tail -30                                # then fix what it lists, file by file
```

The rules for each kind of call site:

- `Tenant(organization, company)` → `Tenant(organization, company, None)` (identity's rows are shared by both modes in this milestone).
- `database.transaction(principal.organization)` and every other organization passed as a scope → `database.transaction(OrganizationScope(principal.organization, None))`.
- `data/database.py`: `type Scope = Tenant | OrganizationId` is deleted; import `Scope` from `ledger_kernel.tenancy`; `SET_SCOPE` and `scope_settings`:

```python
SET_SCOPE: Final = text(
    """
    SELECT set_config('ledger.organization_id', :organization, true),
           set_config('ledger.company_id', :company, true),
           set_config('ledger.mode', :mode, true)
    """
)


def scope_settings(scope: Scope | None) -> dict[str, str]:
    """What ``ledger.organization_id``, ``ledger.company_id`` and ``ledger.mode`` are set to; empty if absent."""
    if scope is None:
        return {"organization": "", "company": "", "mode": ""}
    company, mode = scope.company, scope.mode
    return {
        "organization": str(scope.organization),
        "company": "" if company is None else str(company),
        "mode": "" if mode is None else mode.value,
    }
```

- `idempotency._organization_of(scope)` becomes `None if scope is None else scope.organization`; `migrations._named` and the `rows_by_scope`/`run_per_scope` helpers read `scope.organization`/`scope.company` the same way (no `match`).
- `auth/access.py`: `CompanyAccess.tenant: Tenant` (kernel); `http/deps.py` `_granted` builds `Tenant(principal.organization, company, None)`.
- identity `adapters/postgres/unit.py`: `_tenancy_of` and its `match` are deleted; `bind` raises `UnscopedUnitError` when `unit.scope is None`, else reads `scope.organization` and `scope.company`. `Company.tenant` returns `Tenant(self.organization, self.id, None)`.

- [ ] **Step 5: Run every test**

Run: `uv run pytest -q`
Expected: all pass; then `uv run poe check` green.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "refactor: kernel tenancy and scopes everywhere, TypeID public ids, parse_uuid

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: The manifest model (∥ Task 4)

Spec §4.2, §4.3, §4.4, ADR 0009; Decision 19. After Task 3. One model for `ledger-devtools` (validation) and the runtime (typed values).

**Files:**
- Create: `packages/platform/src/ledger_platform/runtime/manifest.py`
- Test: `packages/platform/tests/unit/test_runtime_manifest.py`
- Modify: root `pyproject.toml` (the platform layers contract already lists `runtime`; nothing to add)

**Interfaces:**
- Consumes: `LedgerError` (kernel).
- Produces (exact names; Tasks 7, 12, 14, 19 use them):
  - `type AppRole = Literal["api", "worker", "signer", "receiver", "migrate"]`
  - `PackageKind` (`MODULE`, `COUNTRY`, `APP`, `PACKAGE`, `TOOL`), `PackageStatus` (`ACTIVE`, `SUSPENDED`)
  - pydantic models (frozen, `extra="forbid"`): `PackageInfo(name, kind, summary, owners, status, scripts: Mapping[str, str])`, `Dependencies(packages, modules, python)`, `DataSection(schema_name: alias "schema")`, `IdPrefix(prefix, resource)`, `PermissionDecl(code, summary, roles)`, `PublishedEvent(type, version, public)`, `ConsumerDecl(consumer, types, max_attempts=10, ordered=False)`, `EventsSection(publishes, consumes)`, `JobDecl(kind, lease=60s, max_attempts=20, deadline=None, concurrency=8)`, `ScheduleDecl(name, every, cron, enqueues)`, `ExtensionPoints(declares, implements, provides)`, `HttpSection(mounted_in, prefixes)`, `AppSection(script, role)`, `Authority(name, short, url)`, `CountrySection(iso, name, currencies, time_zone, authority)`, `RegimeEntry(key, entry_point)`, `ChannelEntry(key, entry_point, assembly, schema_name, valid_from, valid_until, environments, live_environments, document_kinds, directions, http_prefixes, tier, ids, permissions, events, jobs, schedules)`, `PackageManifest(package, dependencies, data, ids, permissions, events, jobs, schedules, extension_points, http, app, country, regimes, channels)` with properties `schema_name -> str | None` and `distribution -> str` (`ledger-<name>`, `ledger-country-<name>`, `ledger-app-<name>`) and `import_name -> str`.
  - `parse_duration(text: object) -> timedelta` (`"60s"`, `"10m"`, `"1h"`, `"15d"`).
  - `load_manifest(text: str) -> PackageManifest`; `InvalidManifestError(problems: tuple[str, ...])` (internal, code `invalid_manifest`), its message lists every problem by location, never a value.

- [ ] **Step 1: Write the failing tests**

`packages/platform/tests/unit/test_runtime_manifest.py`:

```python
from datetime import date, timedelta

import pytest

from ledger_platform.runtime.manifest import (
    InvalidManifestError,
    PackageKind,
    load_manifest,
    parse_duration,
)

MODULE = """
[package]
name = "invoicing"
kind = "module"
summary = "Commercial documents."
owners = ["@balegre0"]

[dependencies]
packages = ["kernel", "platform"]
modules = ["identity"]
python = ["fastapi>=0.143", "sqlalchemy[asyncio]>=2.1.4"]

[data]
schema = "invoicing"

[[ids]]
prefix = "inv"
resource = "invoice"

[[permissions]]
code = "invoicing.invoices.read"
summary = "Read commercial documents."
roles = ["owner", "admin", "viewer"]

[[events.publishes]]
type = "invoicing.invoice.issued"
version = 1
public = "invoice.issued"

[[events.consumes]]
consumer = "invoicing.companies"
types = ["identity.company.registered"]
ordered = true

[[jobs]]
kind = "invoicing.export"
deadline = "72h"

[[schedules]]
name = "invoicing.purge_drafts"
every = "24h"
enqueues = "invoicing.export"

[http]
mounted_in = ["api"]
prefixes = ["/v1/invoices"]
"""

COUNTRY = """
[package]
name = "do"
kind = "country"
summary = "Dominican Republic."
owners = ["@balegre0"]

[dependencies]
packages = ["kernel", "platform"]

[country]
iso = "DO"
name = "Dominican Republic"
currencies = ["DOP"]
time_zone = "America/Santo_Domingo"
authority = { name = "Dirección General de Impuestos Internos", short = "DGII", url = "https://dgii.gov.do" }

[[regimes]]
key = "do"
entry_point = "ledger_country_do.regime:REGIME"

[[channels]]
key = "do-ecf-v1"
entry_point = "ledger_country_do.channels.ecf_v1:CHANNEL"
assembly = "ledger_country_do.channels.ecf_v1.assembly"
schema = "do_ecf"
valid_from = 2019-01-01
environments = ["testecf", "certecf", "ecf"]
live_environments = ["ecf"]
document_kinds = ["invoice", "credit_note", "debit_note"]
directions = ["sales", "purchase"]
http_prefixes = ["/v1/do"]
tier = "bronze"

[[channels.jobs]]
kind = "do_ecf.advance"
max_attempts = 50
deadline = "15d"
concurrency = 16
"""


def test_the_spec_example_of_a_module_reads() -> None:
    manifest = load_manifest(MODULE)
    assert manifest.package.kind is PackageKind.MODULE
    assert manifest.schema_name == "invoicing"
    assert manifest.distribution == "ledger-invoicing"
    assert manifest.import_name == "ledger_invoicing"
    assert manifest.events.consumes[0].ordered is True
    assert manifest.events.consumes[0].max_attempts == 10
    assert manifest.jobs[0].lease == timedelta(seconds=60)
    assert manifest.jobs[0].deadline == timedelta(hours=72)
    assert manifest.schedules[0].every == timedelta(hours=24)


def test_the_spec_example_of_a_country_reads() -> None:
    manifest = load_manifest(COUNTRY)
    assert manifest.distribution == "ledger-country-do"
    assert manifest.channels[0].valid_from == date(2019, 1, 1)
    assert manifest.channels[0].schema_name == "do_ecf"
    assert manifest.channels[0].jobs[0].concurrency == 16


def test_an_app_names_its_script_and_role() -> None:
    manifest = load_manifest(
        '[package]\nname = "api"\nkind = "app"\nsummary = "The API."\nowners = ["@o"]\n'
        '[app]\nscript = "ledger-api"\nrole = "api"\n'
    )
    assert (manifest.distribution, manifest.app is not None and manifest.app.role) == ("ledger-app-api", "api")


@pytest.mark.parametrize(
    ("text", "location"),
    [
        (MODULE + "\n[surprise]\nx = 1\n", "surprise"),
        (MODULE.replace('code = "invoicing.invoices.read"', 'code = "invoicing.read"'), "permissions.0.code"),
        (MODULE.replace('roles = ["owner", "admin", "viewer"]', 'roles = ["root"]'), "permissions.0.roles.0"),
        (MODULE.replace('every = "24h"', 'every = "24h"\ncron = "0 3 * * *"'), "schedules.0"),
        (MODULE.replace('every = "24h"', ""), "schedules.0"),
        (MODULE.replace('deadline = "72h"', 'deadline = "3 days"'), "jobs.0.deadline"),
        (MODULE.replace('prefixes = ["/v1/invoices"]', 'prefixes = ["/invoices"]'), "http.prefixes.0"),
        (MODULE.replace('prefix = "inv"', 'prefix = "invoice"'), "ids.0.prefix"),
        (MODULE.replace('kind = "module"', 'kind = "app"'), "app"),
        (COUNTRY + '\n[dependencies.extra]\n', "dependencies.extra"),
        (COUNTRY.replace('packages = ["kernel", "platform"]', 'packages = ["kernel"]\nmodules = ["identity"]'), "dependencies.modules"),
        ("not = [toml", "line 1"),
    ],
)
def test_a_bad_manifest_lists_where_it_is_wrong_never_the_value(text: str, location: str) -> None:
    with pytest.raises(InvalidManifestError) as caught:
        load_manifest(text)
    assert location in str(caught.value)
    assert "3 days" not in str(caught.value) and "/invoices" not in str(caught.value).replace("/v1/invoices", "")


@pytest.mark.parametrize(("text", "seconds"), [("60s", 60), ("10m", 600), ("1h", 3600), ("15d", 1_296_000)])
def test_durations_read_in_four_units(text: str, seconds: int) -> None:
    assert parse_duration(text) == timedelta(seconds=seconds)


@pytest.mark.parametrize("text", ["0s", "1w", "1.5h", "h", "", 60, "60 s"])
def test_anything_else_is_not_a_duration(text: object) -> None:
    with pytest.raises(ValueError, match="duration"):
        parse_duration(text)
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest packages/platform/tests/unit/test_runtime_manifest.py -q -p no:randomly`
Expected: `ModuleNotFoundError: No module named 'ledger_platform.runtime.manifest'`.

- [ ] **Step 3: Implement**

`packages/platform/src/ledger_platform/runtime/manifest.py`:

```python
"""A workspace package's manifest (§4.2, §4.3): what its ``manifest.toml`` declares, as validated values.

One model serves both sides. ``ledger-devtools`` reads every ``manifest.toml`` with ``load_manifest`` (unknown
keys, malformed names and impossible combinations are listed together, by location, never by value) and writes
each package's ``_manifest.py``, which validates the same data again when it is imported; the runtime reads
those values. Rules that need the whole workspace (a name equal to its directory, unique schemas and prefixes,
an acyclic module graph, schedules that enqueue a declared job kind) are ``ledger-devtools check``'s.
"""

import re
import tomllib
from collections.abc import Mapping
from datetime import date, timedelta
from enum import StrEnum
from typing import Annotated, ClassVar, Final, Literal, Self, final

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, ValidationError, model_validator

from ledger_kernel.errors import LedgerError

type AppRole = Literal["api", "worker", "signer", "receiver", "migrate"]
"""What an app process runs (§3.3)."""
type BuiltInRole = Literal["owner", "admin", "accountant", "issuer", "viewer"]
"""The roles a permission may be granted to by default (§7.7)."""
type Tier = Literal["bronze", "silver", "gold"]

_FROZEN: Final = ConfigDict(extra="forbid", frozen=True)
_DURATION: Final = re.compile(r"([1-9][0-9]*)([smhd])", re.ASCII)
_UNIT_SECONDS: Final[Mapping[str, int]] = {"s": 1, "m": 60, "h": 3600, "d": 86_400}

type Name = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{1,30}$")]
type SchemaName = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{1,30}$")]
type Prefix = Annotated[str, Field(pattern=r"^[a-z]{2,5}$")]
type PermissionCode = Annotated[
    str, Field(pattern=r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*\.(read|create|issue|void|manage|own)$")
]
type EventType = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")]
type PublicName = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")]
type Dotted = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")]
"""A consumer (``<module>.<purpose>``), a job kind or a schedule name (``<module or channel>.<name>``)."""
type HttpPrefix = Annotated[str, Field(pattern=r"^/v1(/[a-z0-9_-]+)+$")]
type Requirement = Annotated[
    str, Field(pattern=r"^[a-z0-9][a-z0-9_.-]*(\[[a-z0-9_,-]+\])?>=[0-9][0-9a-z.]*(,<[0-9][0-9a-z.]*)?$")
]
"""A third-party distribution with a floor: ``fastapi>=0.143``, ``psycopg[binary]>=3.3.6``."""
type ObjectPath = Annotated[str, Field(pattern=r"^[a-z_][a-z0-9_.]*(:[A-Za-z_][A-Za-z0-9_]*)?$")]
type Script = Annotated[str, Field(pattern=r"^ledger-[a-z][a-z0-9-]*$")]


class _DurationFormatError(ValueError):
    def __init__(self) -> None:
        super().__init__("a duration is a positive whole number and one of s, m, h, d (60s, 72h, 15d)")


def parse_duration(text: object) -> timedelta:
    """``60s``, ``10m``, ``1h`` or ``15d``; a ``timedelta`` passes through (values already read)."""
    if isinstance(text, timedelta):
        return text
    found = _DURATION.fullmatch(text) if type(text) is str else None
    if found is None:
        raise _DurationFormatError
    return timedelta(seconds=int(found[1]) * _UNIT_SECONDS[found[2]])


type Duration = Annotated[timedelta, BeforeValidator(parse_duration)]


class PackageKind(StrEnum):
    """What a package is (§4.2)."""

    MODULE = "module"
    COUNTRY = "country"
    APP = "app"
    PACKAGE = "package"
    TOOL = "tool"


class PackageStatus(StrEnum):
    """A suspended package is neither assembled nor migrated."""

    ACTIVE = "active"
    SUSPENDED = "suspended"


class _Section(BaseModel):
    model_config: ClassVar[ConfigDict] = _FROZEN


@final
class PackageInfo(_Section):
    """``[package]``."""

    name: Name
    kind: PackageKind
    summary: str = Field(min_length=1, max_length=200)
    owners: tuple[str, ...] = Field(min_length=1)
    status: PackageStatus = PackageStatus.ACTIVE
    scripts: Mapping[Script, ObjectPath] = Field(default_factory=dict)
    """Console scripts of a package or tool (``ledger-probe``); an app's is ``[app] script``."""


@final
class Dependencies(_Section):
    """``[dependencies]``: workspace packages, modules (a DAG) and third-party distributions with floors."""

    packages: tuple[Name, ...] = ()
    modules: tuple[Name, ...] = ()
    python: tuple[Requirement, ...] = ()


@final
class DataSection(_Section):
    """``[data]``: the PostgreSQL schema the package owns, which is also its roles' prefix."""

    schema_name: SchemaName = Field(alias="schema")


@final
class IdPrefix(_Section):
    """``[[ids]]``: a public id prefix the package mints."""

    prefix: Prefix
    resource: SchemaName


@final
class PermissionDecl(_Section):
    """``[[permissions]]``."""

    code: PermissionCode
    summary: str = Field(min_length=1, max_length=200)
    roles: tuple[BuiltInRole, ...] = ()


@final
class PublishedEvent(_Section):
    """``[[events.publishes]]``."""

    type: EventType
    version: int = Field(ge=1)
    public: PublicName | None = None


@final
class ConsumerDecl(_Section):
    """``[[events.consumes]]``: a consumer and the event types it handles."""

    consumer: Dotted
    types: tuple[EventType, ...] = Field(min_length=1)
    max_attempts: int = Field(default=10, ge=1)
    ordered: bool = False


@final
class EventsSection(_Section):
    """``[events]``."""

    publishes: tuple[PublishedEvent, ...] = ()
    consumes: tuple[ConsumerDecl, ...] = ()


@final
class JobDecl(_Section):
    """``[[jobs]]``."""

    kind: Dotted
    lease: Duration = timedelta(seconds=60)
    max_attempts: int = Field(default=20, ge=1)
    deadline: Duration | None = None
    concurrency: int = Field(default=8, ge=1)


@final
class ScheduleDecl(_Section):
    """``[[schedules]]``: exactly one of ``every`` and ``cron``; it only enqueues a job (§3.6)."""

    name: Dotted
    every: Duration | None = None
    cron: str | None = None
    enqueues: Dotted

    @model_validator(mode="after")
    def exactly_one_cadence(self) -> Self:
        """A schedule runs on an interval or on a cron expression, never both, never neither."""
        if (self.every is None) == (self.cron is None):
            raise _CadenceError
        return self


class _CadenceError(ValueError):
    def __init__(self) -> None:
        super().__init__("a schedule has exactly one of every and cron")


@final
class ExtensionPoints(_Section):
    """``[extension_points]``."""

    declares: tuple[str, ...] = ()
    implements: tuple[str, ...] = ()
    provides: tuple[str, ...] = ()


@final
class HttpSection(_Section):
    """``[http]``: the apps that mount the package's routers and the path prefixes they own."""

    mounted_in: tuple[AppRole, ...] = Field(min_length=1)
    prefixes: tuple[HttpPrefix, ...] = Field(min_length=1)


@final
class AppSection(_Section):
    """``[app]``."""

    script: Script
    role: AppRole


@final
class Authority(_Section):
    """The tax authority of a country."""

    name: str
    short: str
    url: str


@final
class CountrySection(_Section):
    """``[country]``."""

    iso: str = Field(pattern=r"^[A-Z]{2}$")
    name: str
    currencies: tuple[str, ...] = Field(min_length=1)
    time_zone: str
    authority: Authority


@final
class RegimeEntry(_Section):
    """``[[regimes]]``: a regime key and the entry point that holds its ``RegimeDef``."""

    key: str = Field(pattern=r"^[a-z]{2}(-[a-z0-9]{1,3})?$")
    entry_point: ObjectPath


@final
class ChannelEntry(_Section):
    """``[[channels]]``: treated like a module by devtools (schema, roles, grants, routing)."""

    key: str = Field(pattern=r"^[a-z]{2}-[a-z0-9]+-v[0-9]+$")
    entry_point: ObjectPath
    assembly: ObjectPath
    schema_name: SchemaName = Field(alias="schema")
    valid_from: date
    valid_until: date | None = None
    environments: tuple[str, ...] = Field(min_length=1)
    live_environments: tuple[str, ...] = ()
    document_kinds: tuple[Literal["invoice", "credit_note", "debit_note"], ...] = Field(min_length=1)
    directions: tuple[Literal["sales", "purchase"], ...] = Field(min_length=1)
    http_prefixes: tuple[HttpPrefix, ...] = ()
    tier: Tier
    ids: tuple[IdPrefix, ...] = ()
    permissions: tuple[PermissionDecl, ...] = ()
    events: EventsSection = EventsSection()
    jobs: tuple[JobDecl, ...] = ()
    schedules: tuple[ScheduleDecl, ...] = ()


class _KindRuleError(ValueError):
    def __init__(self, rule: str) -> None:
        super().__init__(rule)


_DISTRIBUTION_PREFIX: Final[Mapping[PackageKind, str]] = {
    PackageKind.MODULE: "ledger-",
    PackageKind.PACKAGE: "ledger-",
    PackageKind.TOOL: "ledger-",
    PackageKind.COUNTRY: "ledger-country-",
    PackageKind.APP: "ledger-app-",
}


@final
class PackageManifest(_Section):
    """A whole ``manifest.toml``."""

    package: PackageInfo
    dependencies: Dependencies = Dependencies()
    data: DataSection | None = None
    ids: tuple[IdPrefix, ...] = ()
    permissions: tuple[PermissionDecl, ...] = ()
    events: EventsSection = EventsSection()
    jobs: tuple[JobDecl, ...] = ()
    schedules: tuple[ScheduleDecl, ...] = ()
    extension_points: ExtensionPoints = ExtensionPoints()
    http: HttpSection | None = None
    app: AppSection | None = None
    country: CountrySection | None = None
    regimes: tuple[RegimeEntry, ...] = ()
    channels: tuple[ChannelEntry, ...] = ()

    @model_validator(mode="after")
    def kind_rules(self) -> Self:
        """What each kind may declare (§4.2, §4.3)."""
        kind = self.package.kind
        if (self.app is not None) != (kind is PackageKind.APP):
            raise _KindRuleError("app: [app] is declared by an app, and only by one")
        if (self.country is not None) != (kind is PackageKind.COUNTRY):
            raise _KindRuleError("country: [country] is declared by a country, and only by one")
        if (self.regimes or self.channels) and kind is not PackageKind.COUNTRY:
            raise _KindRuleError("regimes: only a country declares regimes and channels")
        if kind is PackageKind.COUNTRY and self.dependencies.modules:
            raise _KindRuleError("dependencies.modules: a country may not depend on a module")
        owns_data = self.data is not None or self.events.publishes or self.events.consumes or self.jobs or self.schedules
        if owns_data and kind not in {PackageKind.MODULE, PackageKind.PACKAGE}:
            raise _KindRuleError("data: only a module or the platform owns a schema, events, jobs or schedules")
        return self

    @property
    def schema_name(self) -> str | None:
        """The schema the package owns, if any."""
        return None if self.data is None else self.data.schema_name

    @property
    def distribution(self) -> str:
        """The distribution's name (§4.4): ``ledger-<name>``, ``ledger-country-<cc>``, ``ledger-app-<name>``."""
        return _DISTRIBUTION_PREFIX[self.package.kind] + self.package.name

    @property
    def import_name(self) -> str:
        """The one top-level import package of the distribution."""
        return self.distribution.replace("-", "_")


class InvalidManifestError(LedgerError):
    """A ``manifest.toml`` the model refuses; every problem, by location, never by value."""

    code: ClassVar[str] = "invalid_manifest"

    def __init__(self, problems: tuple[str, ...]) -> None:
        super().__init__("manifest: " + "; ".join(problems))


def load_manifest(text: str) -> PackageManifest:
    """The manifest ``text`` holds (the content of a ``manifest.toml``)."""
    try:
        return PackageManifest.model_validate(tomllib.loads(text))
    except tomllib.TOMLDecodeError as error:
        raise InvalidManifestError((f"not TOML at line {error.lineno}",)) from error
    except ValidationError as error:
        problems = tuple(
            ".".join(str(part) for part in issue["loc"]) + f": {issue['msg']}"
            for issue in error.errors(include_input=False, include_url=False)
        )
        raise InvalidManifestError(problems) from error
```

(`_CadenceError` and `_KindRuleError` are raised inside validators: pydantic reports them as `Value error, <message>` at the model's location — the `schedules.0` and `app` locations the tests look for. The kind rules put the rule's location first in the message text (`"app: ..."`), since a model-level error has an empty `loc`; the parametrized test's `location` is found in the message either way.)

- [ ] **Step 4: Run the tests, then the gate**

Run: `uv run pytest packages/platform/tests/unit/test_runtime_manifest.py -q` → all pass. `uv run poe check` → green (100 % branch coverage of `manifest.py`).

- [ ] **Step 5: Commit**

```bash
git add packages/platform/src/ledger_platform/runtime/manifest.py packages/platform/tests/unit/test_runtime_manifest.py
git commit -m "feat(platform): the manifest model, one for devtools and the runtime

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Testing plugin, xdist, per-worker databases, images.toml (∥ Task 7)

Spec §14.1-§14.2, §13 (image pins), audit D26-D30, §6.2-§6.3. After Task 4. `ledger_testing` becomes a pytest plugin (`pytest11`); xdist runs every package's tests; one PostgreSQL container serves all workers; each worker has its own database, cloned from a template that the packages' hooks prepare once; RSA keys are made once per session.

**Files:**
- Create: `packages/testing/src/ledger_testing/{plugin,hooks,postgres,images,ports}.py`, `deploy/local/images.toml`
- Modify: `packages/testing/src/ledger_testing/{issuer,clock,containers,waiting}.py`, `packages/testing/pyproject.toml` (entry point `pytest11`, dependency on `pytest-xdist` is dev-only at the root), root `pyproject.toml` (dev dependency `pytest-xdist`, poe `test` runs `pytest -n auto`), `packages/platform/tests/conftest.py`, `modules/identity/tests/conftest.py`, `packages/testing/tests/conftest.py`, the tests that declared their own `issuer`/`occupy_a_port`
- Delete: root `conftest.py` (its loop factory moves into the plugin)
- Test: `packages/testing/tests/{test_plugin,test_postgres,test_images,test_clock,test_ports}.py`

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - Plugin fixtures: `postgres: PostgresServer` (session; one server per run, shared by xdist workers), `worker_name: str` (session; `"main"` or the xdist worker id), `worker_database: WorkerDatabase` (session; this worker's clone of the template), `issuer_keys: IssuerKeys` (session), `local_issuer: LocalIssuer` (function, async), `clock: ManualClock` (function), `restore_logging` (function, autouse).
  - Hook (implemented by package conftests): `pytest_ledger_template(template: Template) -> None`.
  - `ledger_testing.postgres`: `PostgresServer(host, port, user, password)` with `libpq(database="postgres") -> str`, `sqlalchemy(database, *, role, password) -> str`, `database(name, *, role, password) -> str` (a new database owned by a new login role, as v1's `Postgres.database`), `to_json()`, `from_json(text)`; `started_postgres() -> ContextManager[PostgresServer]`; `Template` with `name`, `execute(statements, *, database=None)`, `url(*, role, password)`; `WorkerDatabase(server, name)` with `url(*, role, password) -> str`, `admin_url() -> str` (superuser, libpq), `truncate(schemas: Sequence[str]) -> None`; `TEMPLATE = "ledger_template"`.
  - `ledger_testing.images.image(name: str) -> str` (from `deploy/local/images.toml`).
  - `ledger_testing.issuer`: `IssuerKeys.generate()`; `LocalIssuer.started(*, keys: IssuerKeys | None = None, issuer=..., audience=...)`.
  - `ledger_testing.clock.ManualClock(now: datetime = START)` — callable `() -> datetime`, `.advance(delta) -> datetime`; `START = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)`.
  - `ledger_testing.ports.occupy_a_port() -> socket.socket`, `port_of(listener) -> int`.

- [ ] **Step 1: Pin the images once**

`deploy/local/images.toml`:

```toml
# Every container image the repository runs, pinned once. ledger_testing reads these values; compose.yaml and the
# Dockerfile name the same strings, and `ledger-devtools check` fails when they differ (Task 19).
[images]
postgres = "postgres:18.6"
nats = "nats:2.12.15"
nats_alpine = "nats:2.12.15-alpine"
zitadel = "ghcr.io/zitadel/zitadel:v4.19.2@sha256:956a298405189a8f6cd4ec92e8828b1bd10caa7da8b139c39d251d7053e33784"
uv = "ghcr.io/astral-sh/uv:0.13.0-trixie-slim@sha256:11bee95ce27982f00acb0c85aa1b5ee6203a9bb958b7def6f27ead851d454e8d"
distroless = "gcr.io/distroless/cc-debian13:nonroot@sha256:e792ab3d241a468a4fd7519ddbbebe66b49b5f365771716ea688ad40b6c6f1c2"
buf = "bufbuild/buf:1.73.0@sha256:75d8f756f0919dcded71e67968a91adfe55fe4dd62c4900c57bbfcd7fe625608"
```

(The `nats` entries go in Task 16.)

- [ ] **Step 2: Write the failing tests**

`packages/testing/tests/test_images.py`:

```python
from pathlib import Path

import pytest

from ledger_testing.images import ImagesFileNotFoundError, image, images


def test_images_are_read_from_the_repository_file() -> None:
    assert image("postgres") == "postgres:18.6"


def test_the_file_is_found_from_a_subdirectory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(Path("packages/testing"))
    images.cache_clear()
    assert image("postgres") == "postgres:18.6"
    images.cache_clear()


def test_outside_the_repository_there_is_no_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    images.cache_clear()
    with pytest.raises(ImagesFileNotFoundError):
        image("postgres")
    images.cache_clear()
```

`packages/testing/tests/test_postgres.py` (integration: real container through the plugin's `postgres` fixture):

```python
import psycopg

from ledger_testing.postgres import TEMPLATE, PostgresServer, Template, WorkerDatabase, clone, ensure_template


def test_the_server_round_trips_through_json(postgres: PostgresServer) -> None:
    assert PostgresServer.from_json(postgres.to_json()) == postgres


def test_a_template_is_built_once_and_cloned_per_worker(postgres: PostgresServer) -> None:
    built: list[str] = []

    def prepare(template: Template) -> None:
        built.append(template.name)
        template.execute(("CREATE TABLE probe (n int)", "INSERT INTO probe VALUES (7)"))

    ensure_template(postgres, prepare, name="probe_template")
    ensure_template(postgres, prepare, name="probe_template")
    clone(postgres, "probe_clone", template="probe_template")
    with psycopg.connect(postgres.libpq("probe_clone")) as connection:
        assert connection.execute("SELECT n FROM probe").fetchone() == (7,)
    assert built == ["probe_template_building"]


def test_a_worker_database_is_the_plugins_clone(worker_database: WorkerDatabase, worker_name: str) -> None:
    assert worker_database.name == f"ledger_{worker_name}"
    with psycopg.connect(worker_database.admin_url()) as connection:
        assert connection.execute("SELECT current_database()").fetchone() == (worker_database.name,)


def test_truncate_empties_the_named_schemas(worker_database: WorkerDatabase) -> None:
    with psycopg.connect(worker_database.admin_url(), autocommit=True) as connection:
        connection.execute("CREATE SCHEMA IF NOT EXISTS scratch; CREATE TABLE IF NOT EXISTS scratch.t (n int)")
        connection.execute("INSERT INTO scratch.t VALUES (1)")
    worker_database.truncate(("scratch",))
    with psycopg.connect(worker_database.admin_url()) as connection:
        assert connection.execute("SELECT count(*) FROM scratch.t").fetchone() == (0,)


def test_the_default_template_is_named_for_the_repository() -> None:
    assert TEMPLATE == "ledger_template"
```

`packages/testing/tests/conftest.py` implements the hook so the default template exists for the test above:

```python
from ledger_testing.postgres import Template


def pytest_ledger_template(template: Template) -> None:
    """The testing package's own tests need nothing in the template."""
    del template
```

`packages/testing/tests/test_plugin.py` (the controller and worker branches, in process with `pytester`; add `pytest_plugins = ["pytester"]` to this test module):

```python
import json

import pytest

from ledger_testing import plugin
from ledger_testing.postgres import PostgresServer

SERVER = PostgresServer(host="db.invalid", port=5432, user="u", password="p")


class Node:
    def __init__(self, config: pytest.Config) -> None:
        self.config = config
        self.workerinput: dict[str, str] = {}


def test_the_controller_starts_one_server_and_hands_it_to_every_worker(
    pytestconfig: pytest.Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    started: list[int] = []

    def fake_start() -> object:
        started.append(1)
        return plugin.nullcontext_of(SERVER)

    monkeypatch.setattr(plugin, "started_postgres", fake_start)
    first, second = Node(pytestconfig), Node(pytestconfig)
    plugin.pytest_configure_node(first)
    plugin.pytest_configure_node(second)
    assert started == [1]
    assert json.loads(first.workerinput["ledger_postgres"]) == json.loads(SERVER.to_json())
    assert first.workerinput == second.workerinput
    plugin.pytest_unconfigure(pytestconfig)


def test_a_worker_uses_the_server_it_was_handed(pytester: pytest.Pytester) -> None:
    pytester.makeconftest(
        f"""
def pytest_configure(config):
    config.workerinput = {{"ledger_postgres": {SERVER.to_json()!r}}}
"""
    )
    pytester.makepyfile(
        """
def test_server(postgres):
    assert postgres.host == "db.invalid"
"""
    )
    pytester.runpytest("-p", "no:randomly").assert_outcomes(passed=1)
```

(`plugin.nullcontext_of(server)` is a tiny helper in `plugin.py`: `contextlib.nullcontext(server)` typed as `AbstractContextManager[PostgresServer]`, used by the plugin when a server is given in `workerinput`; the test reuses it as a stand-in for `started_postgres`. If the implementer prefers, the test may monkeypatch with `lambda: contextlib.nullcontext(SERVER)`.)

`packages/testing/tests/test_clock.py` — add:

```python
from datetime import timedelta

from ledger_testing.clock import START, ManualClock


def test_a_manual_clock_stands_still_until_moved() -> None:
    clock = ManualClock()
    assert clock() == START == clock()
    assert clock.advance(timedelta(seconds=90)) == START + timedelta(seconds=90) == clock()
```

`packages/testing/tests/test_ports.py`:

```python
import socket

import pytest

from ledger_testing.ports import occupy_a_port, port_of


def test_an_occupied_port_cannot_be_bound_again() -> None:
    with occupy_a_port() as taken:
        number = port_of(taken)
        with pytest.raises(OSError), socket.create_server(("", number)):
            pass
```

`packages/testing/tests/test_issuer.py` — add: two `LocalIssuer`s started with the same `IssuerKeys` publish the same `kid` and verify each other's tokens; `rotate()` still makes a new key.

- [ ] **Step 3: Run them to see them fail**

Run: `uv run pytest packages/testing -q -p no:randomly`
Expected: `ModuleNotFoundError` for `ledger_testing.images`, `.postgres`, `.plugin`, `.ports`; fixture `postgres` unknown.

- [ ] **Step 4: Implement**

`packages/testing/src/ledger_testing/images.py`:

```python
"""The container images the repository pins, read from ``deploy/local/images.toml`` (one place, §13)."""

import tomllib
from functools import cache
from pathlib import Path
from types import MappingProxyType
from typing import Final
lazy from collections.abc import Mapping

from pydantic import TypeAdapter

IMAGES_FILE: Final = Path("deploy/local/images.toml")
_FILE: Final = TypeAdapter(dict[str, dict[str, str]])


class ImagesFileNotFoundError(FileNotFoundError):
    """Tests run inside the repository: no ``deploy/local/images.toml`` above the current directory."""

    def __init__(self, start: Path) -> None:
        super().__init__(f"no {IMAGES_FILE.as_posix()} in {start} or any directory above it")


@cache
def images() -> Mapping[str, str]:
    """Every pinned image by name, from the nearest ``deploy/local/images.toml`` at or above the current directory."""
    here = Path.cwd().resolve()
    for directory in (here, *here.parents):
        candidate = directory / IMAGES_FILE
        if candidate.is_file():
            return MappingProxyType(_FILE.validate_python(tomllib.loads(candidate.read_text("utf-8")))["images"])
    raise ImagesFileNotFoundError(here)


def image(name: str) -> str:
    """The pinned image ``name`` (``postgres``, ``zitadel``, ...)."""
    return images()[name]
```

`packages/testing/src/ledger_testing/postgres.py`:

```python
"""One PostgreSQL server per test session, and per-worker databases cloned from one prepared template (§14.2).

Under xdist the controller starts the server and hands its address to every worker (``ledger_testing.plugin``).
The template is built once per server, under an advisory lock, into ``<name>_building`` and renamed when the
hooks (``pytest_ledger_template``) are done, so a failed build never leaves a half template behind. Clones are
serialized by the same lock: ``CREATE DATABASE ... TEMPLATE`` refuses a template another session is using.
Every connection here is the superuser's; tests connect as the roles a service connects as.
"""

import json
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from typing import Final, final
lazy from collections.abc import Callable, Generator, Iterable, Sequence

import psycopg
from psycopg import sql
from pydantic import TypeAdapter
from testcontainers.community.postgres import PostgresContainer

from ledger_testing.images import image

TEMPLATE: Final = "ledger_template"
_LOCK: Final = 0x6C65646774706C00
"""``ledgtpl`` in ASCII: the advisory lock that serializes template builds and clones."""
_PORT: Final = 5432
_NUMBER: Final = TypeAdapter(int)


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class PostgresServer:
    """Where the session's server listens, and its superuser."""

    host: str
    port: int
    user: str
    password: str

    def libpq(self, database: str = "postgres") -> str:
        """The superuser's libpq URL of ``database``."""
        return f"postgresql://{self.user}:{self.password}@{self.host}:{self.port}/{database}"

    def sqlalchemy(self, database: str, *, role: str, password: str) -> str:
        """The SQLAlchemy URL (async psycopg driver) of ``database`` as ``role``."""
        return f"postgresql+psycopg://{role}:{password}@{self.host}:{self.port}/{database}"

    def database(self, name: str, *, role: str, password: str) -> str:
        """A new database ``name`` owned by a new login role ``role`` (no superuser, no BYPASSRLS): its URL."""
        with psycopg.connect(self.libpq(), autocommit=True) as admin:
            admin.execute(
                sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS").format(
                    sql.Identifier(role), sql.Literal(password)
                )
            )
            admin.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(name), sql.Identifier(role)))
        return self.sqlalchemy(name, role=role, password=password)

    def to_json(self) -> str:
        """The address, for an xdist worker's ``workerinput``."""
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, text: str) -> PostgresServer:
        """The address a controller handed over."""
        return _SERVER.validate_json(text)


_SERVER: Final = TypeAdapter(PostgresServer)


@contextmanager
def started_postgres() -> Generator[PostgresServer]:
    """A PostgreSQL server (the pinned image) for the length of the block."""
    with PostgresContainer(image("postgres"), driver=None) as container:
        yield PostgresServer(
            host=container.get_container_host_ip(),
            port=_NUMBER.validate_python(container.get_exposed_port(_PORT)),
            user=container.username,
            password=container.password,
        )


@final
class Template:
    """The database being prepared as the template (``pytest_ledger_template`` receives it)."""

    __slots__ = ("_server", "name")

    def __init__(self, server: PostgresServer, name: str) -> None:
        self._server = server
        self.name = name

    @property
    def server(self) -> PostgresServer:
        """The server it lives on (roles are the server's, shared by every database)."""
        return self._server

    def execute(self, statements: Iterable[str], *, database: str | None = None) -> None:
        """Run ``statements`` as the superuser, in the template (or in ``database``), each committed."""
        with psycopg.connect(self._server.libpq(database or self.name), autocommit=True) as connection:
            for statement in statements:
                connection.execute(sql.SQL(statement))  # statements written by the tests, never input

    def url(self, *, role: str, password: str) -> str:
        """The template's SQLAlchemy URL as ``role`` (migrations run as the migrator role)."""
        return self._server.sqlalchemy(self.name, role=role, password=password)


@contextmanager
def _locked(server: PostgresServer) -> Generator[psycopg.Connection]:
    with psycopg.connect(server.libpq(), autocommit=True) as admin:
        admin.execute("SELECT pg_advisory_lock(%s)", (_LOCK,))
        try:
            yield admin
        finally:
            admin.execute("SELECT pg_advisory_unlock(%s)", (_LOCK,))


def ensure_template(server: PostgresServer, prepare: Callable[[Template], None], *, name: str = TEMPLATE) -> None:
    """Build the template ``name`` with ``prepare`` unless it exists (once per server, whatever the workers)."""
    building = f"{name}_building"
    with _locked(server) as admin:
        if admin.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone() is not None:
            return
        admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(building)))
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(building)))
        prepare(Template(server, building))
        admin.execute(sql.SQL("ALTER DATABASE {} RENAME TO {}").format(sql.Identifier(building), sql.Identifier(name)))


def clone(server: PostgresServer, name: str, *, template: str = TEMPLATE) -> None:
    """A fresh database ``name``, a copy of ``template`` (an old one of that name is dropped first)."""
    with _locked(server) as admin:
        admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))
        admin.execute(sql.SQL("CREATE DATABASE {} TEMPLATE {}").format(sql.Identifier(name), sql.Identifier(template)))


@final
@dataclass(frozen=True, slots=True)
class WorkerDatabase:
    """This worker's database: a clone of the template, its own for the whole session."""

    server: PostgresServer
    name: str

    def url(self, *, role: str, password: str) -> str:
        """Its SQLAlchemy URL as ``role``."""
        return self.server.sqlalchemy(self.name, role=role, password=password)

    def admin_url(self) -> str:
        """Its superuser libpq URL: for a test's setup and cleanup only."""
        return self.server.libpq(self.name)

    def truncate(self, schemas: Sequence[str]) -> None:
        """Empty every table of ``schemas`` but Alembic's version tables and ``platform.routing``."""
        query = """
            SELECT format('%I.%I', schemaname, tablename) FROM pg_tables
            WHERE schemaname = ANY(%s) AND tablename <> 'alembic_version'
              AND (schemaname, tablename) <> ('platform', 'routing')
        """
        with psycopg.connect(self.admin_url(), autocommit=True) as connection:
            tables = [row[0] for row in connection.execute(query, (list(schemas),)).fetchall()]
            if tables:
                connection.execute(sql.SQL("TRUNCATE {}").format(sql.SQL(", ").join(map(sql.SQL, tables))))
```

(`connection.execute(sql.SQL(statement))`: psycopg's typed `execute` takes `LiteralString | Composable`; wrapping test-written DDL in `sql.SQL` is the typed way. `row[0]` comes back typed `Any` from a plain cursor: use `connection.cursor(row_factory=args_row(str))` as `ledger_testing.waiting` does, so the list is `list[str]`.)

`packages/testing/src/ledger_testing/hooks.py`:

```python
"""The hook packages implement in their ``conftest.py`` to prepare the template database (§14.2)."""

import pytest
lazy from ledger_testing.postgres import Template


@pytest.hookspec
def pytest_ledger_template(template: Template) -> None:
    """Prepare ``template`` once per server: roles, schemas, migrations. Every implementation runs."""
```

`packages/testing/src/ledger_testing/plugin.py`:

```python
"""The ledger's pytest plugin (entry point ``pytest11``): what every package's tests share (§14.2, audit D26-D28).

- ``postgres``: one PostgreSQL server per run. Under xdist the controller starts it when the first worker is
  configured and hands its address to every worker (``workerinput``); without xdist the session starts its own.
- ``worker_database``: this worker's own database, a clone of a template the packages' ``pytest_ledger_template``
  hooks prepare once per server.
- ``issuer_keys`` (session) and ``local_issuer`` (per test): an OpenID provider whose RSA key is made once.
- ``clock``: a wall clock the test moves. ``restore_logging`` (autouse): the plain logging baseline.
- A selector event loop on Windows: psycopg's async driver cannot run on the proactor loop.
"""

import asyncio
import os
import sys
from contextlib import AbstractContextManager, ExitStack, nullcontext
from typing import Final
lazy from collections.abc import AsyncGenerator, Callable, Iterator, Mapping

import pytest
from pydantic import TypeAdapter
lazy from xdist.workermanage import WorkerController

from ledger_testing import hooks
from ledger_testing.clock import ManualClock
from ledger_testing.issuer import IssuerKeys, LocalIssuer
from ledger_testing.logs import restored_logging
from ledger_testing.postgres import PostgresServer, WorkerDatabase, clone, ensure_template, started_postgres

_SERVER_INPUT: Final = "ledger_postgres"
_WORKER_INPUT: Final = TypeAdapter(dict[str, str])
_SERVER: Final = pytest.StashKey[PostgresServer]()
_STACK: Final = pytest.StashKey[ExitStack]()


def nullcontext_of(server: PostgresServer) -> AbstractContextManager[PostgresServer]:
    """A server someone else started, as a context manager that stops nothing."""
    return nullcontext(server)


def pytest_addhooks(pluginmanager: pytest.PytestPluginManager) -> None:
    """Declare ``pytest_ledger_template``."""
    pluginmanager.add_hookspecs(hooks)


def pytest_asyncio_loop_factories(
    config: pytest.Config, item: pytest.Item
) -> Mapping[str, Callable[[], asyncio.AbstractEventLoop]]:
    """A selector event loop on Windows; the default elsewhere."""
    del config, item
    if sys.platform == "win32":
        return {"selector": asyncio.SelectorEventLoop}
    return {"default": asyncio.new_event_loop}


def _controller_server(config: pytest.Config) -> PostgresServer:
    if _SERVER not in config.stash:
        stack = ExitStack()
        config.stash[_STACK] = stack
        config.stash[_SERVER] = stack.enter_context(started_postgres())
    return config.stash[_SERVER]


@pytest.hookimpl(optionalhook=True)
def pytest_configure_node(node: WorkerController) -> None:
    """xdist's controller, for each worker: the one server, started at the first worker."""
    node.workerinput[_SERVER_INPUT] = _controller_server(node.config).to_json()


def pytest_unconfigure(config: pytest.Config) -> None:
    """The controller stops the server it started."""
    stack = config.stash.get(_STACK, None)
    if stack is not None:
        stack.close()


@pytest.fixture(scope="session")
def postgres(request: pytest.FixtureRequest) -> Iterator[PostgresServer]:
    """The run's PostgreSQL server (see the module)."""
    given = _WORKER_INPUT.validate_python(getattr(request.config, "workerinput", {})).get(_SERVER_INPUT)
    opened = started_postgres() if given is None else nullcontext_of(PostgresServer.from_json(given))
    with opened as server:
        yield server


@pytest.fixture(scope="session")
def worker_name() -> str:
    """``main`` without xdist, else the worker's id (``gw0``, ``gw1``, ...)."""
    return os.environ.get("PYTEST_XDIST_WORKER", "main")


@pytest.fixture(scope="session")
def worker_database(postgres: PostgresServer, worker_name: str, request: pytest.FixtureRequest) -> WorkerDatabase:
    """This worker's own database, cloned from the template the packages' hooks prepared."""
    ensure_template(postgres, lambda template: request.config.hook.pytest_ledger_template(template=template))
    name = f"ledger_{worker_name}"
    clone(postgres, name)
    return WorkerDatabase(postgres, name)


@pytest.fixture(autouse=True)
def restore_logging() -> Iterator[None]:
    """Each test starts from, and leaves, the plain logging baseline (logging is process-wide state)."""
    with restored_logging():
        yield


@pytest.fixture(scope="session")
def issuer_keys() -> IssuerKeys:
    """The local OpenID provider's first RSA key, made once per session (§14.2)."""
    return IssuerKeys.generate()


@pytest.fixture
async def local_issuer(issuer_keys: IssuerKeys) -> AsyncGenerator[LocalIssuer]:
    """An OpenID provider on 127.0.0.1 signing with the session's key."""
    async with LocalIssuer.started(keys=issuer_keys) as started:
        yield started


@pytest.fixture
def clock() -> ManualClock:
    """A wall clock the test moves (``Clock``)."""
    return ManualClock()
```

(`request.config.hook.pytest_ledger_template(...)` returns pluggy's untyped result inside a `lambda` whose return the template builder discards: type the lambda as `Callable[[Template], None]` through a small `def` if basedpyright reports the `Any` result; never `cast`.)

`issuer.py`: add

```python
@final
@dataclass(frozen=True, slots=True)
class IssuerKeys:
    """The provider's first signing key, made once and shared by every ``LocalIssuer`` of a session."""

    first: RSAKey

    @classmethod
    def generate(cls) -> IssuerKeys:
        """A new RSA key, ``kid`` ``key-1``."""
        return cls(RSAKey.generate_key(_KEY_SIZE, {"kid": "key-1", "use": "sig", "alg": "RS256"}, private=True))
```

and `LocalIssuer.__init__(self, *, issuer=ISSUER, audience=AUDIENCE, keys: IssuerKeys | None = None)`: when `keys` is given, `self._keys = {"key-1": keys.first}`, `self._published = ["key-1"]`, `self._current = "key-1"` instead of calling `rotate()`; `started(...)` passes `keys` through. `rotate()` is unchanged (it numbers after the keys held).

`clock.py`: add `START: Final = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)` and

```python
@final
class ManualClock:
    """A wall clock (``Clock``) that stands still until the test moves it."""

    __slots__ = ("now",)

    def __init__(self, now: datetime = START) -> None:
        self.now = now

    def __call__(self) -> datetime:
        """The time the test set."""
        return self.now

    def advance(self, delta: timedelta) -> datetime:
        """Move forward by ``delta``; the new time."""
        self.now += delta
        return self.now
```

`ports.py`: move `occupy_a_port` and `port_of` from `packages/platform/tests/unit/test_grpc_server.py` (and the copy in `tests/integration/test_service.py`) here, with the `TypeAdapter` that types `getsockname`'s answer; the two test modules import them.

`containers.py`: `POSTGRES_IMAGE` and `NATS_IMAGE` constants become `image("postgres")` and `image("nats")` at call time; `Postgres` (v1) is deleted in favour of `PostgresServer` (its `database(...)` method is the same behaviour); `nats_server` and `nats_address` stay until Task 16.

`packages/testing/pyproject.toml`: add

```toml
[project.entry-points.pytest11]
ledger = "ledger_testing.plugin"
```

and `"pytest>=9.1.1"` to its dependencies (the plugin imports pytest at runtime; it is dev-only by construction). Root `pyproject.toml`: add `"pytest-xdist>=3.8.0"` to `[dependency-groups] dev`; poe `test = { cmd = "pytest -n auto", help = "Tests with coverage, in parallel" }`. Delete root `conftest.py` and remove `"conftest.py"` from `[tool.basedpyright] include` and from `[tool.ruff.lint.per-file-ignores]`.

The packages' conftests:

- `packages/platform/tests/conftest.py`: delete `restore_logging` and `postgres` (the plugin's); `database_url(postgres: PostgresServer, worker_name: str)` returns `postgres.database(f"ledger_{worker_name}_v1", role=f"ledger_app_{worker_name}", password="app-password")`; `fresh_database_url` uses `postgres.database(...)`; the NATS fixtures stay (one NATS container per worker, until Task 16). It also implements `pytest_ledger_template` as a no-op (the platform's v1 tests do not use the template; Task 8 fills it).
- `modules/identity/tests/conftest.py`: delete `restore_logging`, `postgres`, `issuer` (tests take the plugin's `local_issuer`: rename the parameter in every test and fixture that used `issuer`); `database_url(postgres, worker_name)` creates `identity_<worker>` owned by role `identity_<worker>` and runs `MIGRATIONS.upgrade(url)`; same no-op hook.
- Every test module that declared its own `issuer` fixture (`test_auth_tokens.py`, `test_http_deps.py`) uses `local_issuer` instead.

- [ ] **Step 5: Run every package's tests in parallel**

```bash
uv sync --all-packages
uv run pytest -n auto -q 2>&1 | tail -3
docker ps --filter ancestor=postgres:18.6 --format '{{.ID}}' | wc -l   # while it runs, in another shell: expected 1
```

Expected: all pass with `-n auto`; exactly one PostgreSQL container for the run (NATS: one per worker until Task 16). Then `uv run poe check` → green.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "test: ledger_testing as a pytest plugin, xdist with per-worker databases, images pinned once

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: `ledger-devtools` core: `check` and `sync` of pyproject, `_manifest.py`, `.importlinter` (∥ Task 6)

Spec §4.2-§4.7, §2 rule 11, ADR 0009; Decisions 19, 22. After Task 5. The tool is built and tested on fixture trees; it is **not** run on the repository until Task 19 (the repository has no manifests yet, and the tool refuses a tree without them).

**Files:**
- Create: `tools/devtools/pyproject.toml` (hand-written now; Task 19 makes it generated), `tools/devtools/src/ledger_devtools/{__init__,workspace,names,checks,generate,cli}.py`, `tools/devtools/src/ledger_devtools/py.typed`
- Test: `tools/devtools/tests/unit/{conftest,test_workspace,test_checks,test_generate,test_cli}.py`
- Modify: root `pyproject.toml` (members `"tools/*"`, dev dependency `ledger-devtools` + its source, `known-first-party`, ruff test globs, basedpyright `include`, pytest `testpaths`, coverage `source_pkgs`, poe `mypy-devtools` and `deps-devtools` folded into `mypy` and `deps`)

**Interfaces:**
- Consumes: `ledger_platform.runtime.manifest.{PackageManifest, PackageKind, load_manifest, InvalidManifestError}` (Task 5).
- Produces:
  - `ledger_devtools.workspace`: `MANIFEST_GLOBS`, `Package(directory: Path, manifest: PackageManifest, raw: Mapping[str, object])` with `name`, `kind`, `nested_country`, `distribution`, `import_name` (the last two derived from the directory for `countries/<cc>/testing`, Decision 27), `source -> Path` (`<dir>/src/<import_name>`), `is_production -> bool`; `Workspace(root, packages)` with `Workspace.load(root) -> Workspace`, `by_name(name) -> Package`, `modules`, `countries`, `apps`, `schemas() -> tuple[str, ...]` (platform first, then modules and channels in dependency order); `WorkspaceError(problems)`.
  - `ledger_devtools.names`: `distribution_of(requirement: str) -> str`, `import_names(distribution: str) -> tuple[str, ...]`.
  - `ledger_devtools.checks`: `Problem(where: str, message: str)`; `check(workspace) -> tuple[Problem, ...]` (manifest rules that need the whole workspace).
  - `ledger_devtools.generate`: `GENERATED_HEADER`; `pyproject_text(package, workspace) -> str`; `manifest_module_text(package) -> str` (formatted with ruff); `importlinter_text(workspace) -> str`; `targets(workspace) -> dict[Path, str]` (every generated file → its content; Task 19 adds `init.sql` and OpenAPI snapshots).
  - `ledger_devtools.cli`: console script `ledger-devtools` with `sync` (writes `targets`, prints the paths it changed) and `check` (problems + files `sync` would change; exit 1 if any, 2 if the tree has no manifest).

- [ ] **Step 1: Write the failing tests**

`tools/devtools/tests/unit/conftest.py` — a fixture that writes a small workspace into `tmp_path`:

```python
from pathlib import Path
from textwrap import dedent

import pytest

KERNEL = """
[package]
name = "kernel"
kind = "package"
summary = "Value types."
owners = ["@o"]
"""
PLATFORM = """
[package]
name = "platform"
kind = "package"
summary = "The chassis."
owners = ["@o"]
scripts = { "ledger-probe" = "ledger_platform.observability.probe:main" }

[dependencies]
packages = ["kernel"]
python = ["sqlalchemy[asyncio]>=2.1.4", "prometheus-client>=0.26.0"]

[data]
schema = "platform"

[[jobs]]
kind = "platform.purge_done_jobs"

[[schedules]]
name = "platform.purge_done_jobs"
every = "24h"
enqueues = "platform.purge_done_jobs"
"""
IDENTITY = """
[package]
name = "identity"
kind = "module"
summary = "Organizations and companies."
owners = ["@o"]

[dependencies]
packages = ["kernel", "platform"]
python = ["fastapi>=0.143"]

[data]
schema = "identity"

[[events.publishes]]
type = "identity.company.registered"
version = 1

[http]
mounted_in = ["api"]
prefixes = ["/v1/companies"]
"""
API = """
[package]
name = "api"
kind = "app"
summary = "The API app."
owners = ["@o"]

[dependencies]
packages = ["platform"]
python = ["granian>=2.8.4"]

[app]
script = "ledger-api"
role = "api"
"""


def write(root: Path, directory: str, manifest: str, children: tuple[str, ...] = ()) -> None:
    package = root / directory
    package.mkdir(parents=True)
    (package / "manifest.toml").write_text(dedent(manifest), encoding="utf-8")
    name = manifest.split('name = "', 1)[1].split('"', 1)[0]
    kind = manifest.split('kind = "', 1)[1].split('"', 1)[0]
    prefix = {"app": "ledger_app_", "country": "ledger_country_"}.get(kind, "ledger_")
    source = package / "src" / f"{prefix}{name}"
    source.mkdir(parents=True)
    (source / "__init__.py").write_text('"""Package."""\n', encoding="utf-8")
    for child in children:
        (source / child).mkdir()
        (source / child / "__init__.py").write_text('"""Layer."""\n', encoding="utf-8")


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    write(tmp_path, "packages/kernel", KERNEL)
    write(tmp_path, "packages/platform", PLATFORM)
    write(tmp_path, "modules/identity", IDENTITY, ("adapters", "application", "domain"))
    write(tmp_path, "apps/api", API)
    return tmp_path
```

`test_workspace.py`:

```python
from pathlib import Path

import pytest

from ledger_devtools.workspace import Workspace, WorkspaceError


def test_every_manifest_is_found_and_read(tree: Path) -> None:
    workspace = Workspace.load(tree)
    assert [package.distribution for package in workspace.packages] == [
        "ledger-api", "ledger-identity", "ledger-kernel", "ledger-platform",
    ]
    assert workspace.by_name("identity").source == Path("modules/identity/src/ledger_identity")
    assert workspace.schemas() == ("platform", "identity")


def test_a_tree_without_manifests_is_refused(tmp_path: Path) -> None:
    with pytest.raises(WorkspaceError, match="no manifest.toml"):
        Workspace.load(tmp_path)


def test_an_invalid_manifest_is_reported_with_its_path(tree: Path) -> None:
    (tree / "apps/api/manifest.toml").write_text("[package]\nname = 1\n", encoding="utf-8")
    with pytest.raises(WorkspaceError, match="apps/api/manifest.toml"):
        Workspace.load(tree)


def test_dev_only_distributions_are_not_production(tree: Path) -> None:
    workspace = Workspace.load(tree)
    assert workspace.by_name("identity").is_production
```

`test_checks.py`:

```python
from pathlib import Path

from ledger_devtools.checks import check
from ledger_devtools.workspace import Workspace
from conftest import write  # tests' own helper module (tests are not a package: --import-mode=importlib finds it)


def messages(root: Path) -> list[str]:
    return [f"{problem.where}: {problem.message}" for problem in check(Workspace.load(root))]


def test_a_coherent_workspace_has_no_problem(tree: Path) -> None:
    assert messages(tree) == []


def test_a_name_must_be_its_directory(tree: Path) -> None:
    (tree / "apps/api").rename(tree / "apps/front")
    assert messages(tree) == ["apps/front/manifest.toml: package.name 'api' is not the directory name 'front'"]


def test_schemas_prefixes_permissions_events_consumers_jobs_and_http_prefixes_are_unique(tree: Path) -> None:
    write(tree, "modules/copy", IDENTITY_COPY)
    assert sorted(messages(tree)) == [
        "modules/copy/manifest.toml: event type 'identity.company.registered' is published by identity too",
        "modules/copy/manifest.toml: http prefix '/v1/companies' is owned by identity too",
        "modules/copy/manifest.toml: schema 'identity' is owned by identity too",
    ]


def test_a_dependency_on_an_unknown_package_or_module_is_reported(tree: Path) -> None:
    ...  # a module listing modules = ["ghost"] → "dependencies.modules names 'ghost', which is not a module"


def test_consuming_is_a_dependency_edge_and_cycles_are_refused(tree: Path) -> None:
    ...  # identity consumes billing.thing.done while billing depends on identity →
    #      "the module graph has a cycle: billing -> identity -> billing"


def test_a_consumer_of_a_type_nobody_publishes_is_reported(tree: Path) -> None:
    ...  # "consumer 'identity.ghosts' handles 'ghost.thing.done', which no package publishes"


def test_a_schedule_enqueues_a_job_kind_its_package_declares(tree: Path) -> None:
    ...  # "schedule 'platform.purge_done_jobs' enqueues 'platform.nothing', which platform does not declare"


def test_cron_schedules_are_not_built_yet(tree: Path) -> None:
    ...  # "schedule 'platform.purge_done_jobs' uses cron, which is not built until a schedule needs it (§16.4)"


def test_a_job_kind_and_a_consumer_carry_their_owners_name(tree: Path) -> None:
    ...  # a module 'identity' declaring job kind 'invoicing.export' →
    #      "job kind 'invoicing.export' does not start with its package's name or channel key"


def test_a_module_submodule_outside_the_layers_is_reported(tree: Path) -> None:
    ...  # modules/identity/src/ledger_identity/utils/ →
    #      "ledger_identity.utils is not a layer (assembly, adapters, application, api, domain, events, _manifest)"
```

(Write each elided test in full with its fixture edit, the exact expected message being the one in its comment; `IDENTITY_COPY` is `IDENTITY` with `name = "copy"`.)

`test_generate.py`:

```python
from pathlib import Path

from ledger_devtools.generate import GENERATED_HEADER, importlinter_text, manifest_module_text, pyproject_text, targets
from ledger_devtools.workspace import Workspace


def test_a_modules_pyproject_comes_from_its_manifest(tree: Path) -> None:
    workspace = Workspace.load(tree)
    text = pyproject_text(workspace.by_name("identity"), workspace)
    assert text.startswith(GENERATED_HEADER)
    assert 'name = "ledger-identity"' in text
    assert '    "ledger-kernel",\n    "ledger-platform",\n    "fastapi>=0.143",\n' in text
    assert '[project.entry-points."ledger.modules"]\nidentity = "ledger_identity.assembly:MODULE"' in text
    assert 'ledger-platform = { workspace = true }' in text
    assert 'source_pkgs = ["ledger_identity"]' in text
    assert 'source = ["modules/identity/src/ledger_identity/adapters/postgres/migrations"]' in text
    assert "fail_under = 100" in text


def test_scripts_and_third_party_module_names_are_written(tree: Path) -> None:
    workspace = Workspace.load(tree)
    platform = pyproject_text(workspace.by_name("platform"), workspace)
    assert '[project.scripts]\nledger-probe = "ledger_platform.observability.probe:main"' in platform
    assert '"prometheus-client" = "prometheus_client"' in platform
    assert 'source = ["packages/platform/src/ledger_platform/data/revisions"]' in platform
    app = pyproject_text(workspace.by_name("api"), workspace)
    assert '[project.scripts]\nledger-api = "ledger_app_api.main:main"' in app


def test_the_manifest_module_validates_the_manifest_data_at_import(tree: Path) -> None:
    text = manifest_module_text(Workspace.load(tree).by_name("identity"))
    namespace: dict[str, object] = {}
    exec(compile(text, "_manifest.py", "exec"), namespace)
    assert type(namespace["MANIFEST"]).__name__ == "PackageManifest"
    assert text.startswith('"""GENERATED by ledger-devtools sync')


def test_importlinter_contracts_follow_the_manifests(tree: Path) -> None:
    text = importlinter_text(Workspace.load(tree))
    assert "root_packages =\n    ledger_app_api\n    ledger_identity\n    ledger_kernel\n    ledger_platform\n" in text
    assert "layers =\n    ledger_app_api\n    ledger_identity\n    ledger_platform\n    ledger_kernel\n" in text
    assert "[importlinter:contract:kernel-is-stdlib-only]" in text
    assert "    sqlalchemy\n" in text and "    fastapi\n" in text
    assert "[importlinter:contract:identity-layers]" in text
    assert "    adapters\n    application\n    domain\n" in text
    assert "[importlinter:contract:identity-is-protected]" in text
    assert "[importlinter:contract:apps-only-compose]" in text


def test_targets_cover_every_generated_file(tree: Path) -> None:
    paths = set(targets(Workspace.load(tree)))
    assert {
        Path(".importlinter"),
        Path("modules/identity/pyproject.toml"),
        Path("modules/identity/src/ledger_identity/_manifest.py"),
        Path("packages/platform/src/ledger_platform/_manifest.py"),
        Path("apps/api/pyproject.toml"),
    } <= paths
    assert Path("packages/kernel/src/ledger_kernel/_manifest.py") not in paths  # no schema: the runtime never reads it
```

`test_cli.py`: `sync` on the fixture tree writes every target and prints them, a second `sync` prints nothing; `check` after `sync` exits 0; editing a generated file makes `check` exit 1 and name it; `check` on an empty directory exits 2 (`main(["check", "--root", str(tmp_path)])` returns the code; `capsys` reads the output).

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tools/devtools -q -p no:randomly`
Expected: `ModuleNotFoundError: No module named 'ledger_devtools'` (after Step 3's packaging, failures for the missing functions).

- [ ] **Step 3: Package the tool**

`tools/devtools/pyproject.toml`:

```toml
[project]
name = "ledger-devtools"
version = "0.1.0"
description = "Generates and checks what the manifests imply: pyproject files, manifests modules, contracts."
requires-python = ">=3.15"
dependencies = ["ledger-kernel", "ledger-platform", "pydantic>=2.14.1", "ruff>=0.17.0"]

[project.scripts]
ledger-devtools = "ledger_devtools.cli:main"

[tool.uv.sources]
ledger-kernel = { workspace = true }
ledger-platform = { workspace = true }

[build-system]
requires = ["uv_build>=0.13.0,<0.14.0"]
build-backend = "uv_build"

[tool.deptry.per_rule_ignores]
DEP002 = ["ruff"]  # run as a program (formatting generated code), never imported
```

Root `pyproject.toml`: `members = ["packages/*", "modules/*", "contracts", "tools/*"]`; `dev` gains `"ledger-devtools"`; `[tool.uv.sources] ledger-devtools = { workspace = true }`; `known-first-party` gains `ledger_devtools`; the tests glob becomes `"{packages/*/tests,modules/*/tests,contracts/tests,tools/*/tests,tests}/**"`; basedpyright `include` gains `"tools"`; pytest `testpaths` gains `"tools"`; coverage `source_pkgs` gains `ledger_devtools`; poe `mypy-devtools = { cmd = "mypy tools/devtools" }` and `deps-devtools = { cmd = "deptry tools/devtools/src --config tools/devtools/pyproject.toml" }` join the `mypy` and `deps` sequences; import-linter `root_packages` gains `ledger_devtools`. Then `uv lock && uv sync --all-packages`.

- [ ] **Step 4: Implement**

`names.py`:

```python
"""Third-party distributions and the modules they install: what deptry and import-linter are told."""

import re
from types import MappingProxyType
from typing import Final
lazy from collections.abc import Mapping

_DISTRIBUTION: Final = re.compile(r"[a-z0-9][a-z0-9_.-]*", re.ASCII)
_IMPORTS: Final[Mapping[str, tuple[str, ...]]] = MappingProxyType(
    {
        "grpcio": ("grpc",),
        "opentelemetry-api": ("opentelemetry",),
        "opentelemetry-sdk": ("opentelemetry",),
        "opentelemetry-exporter-otlp-proto-grpc": ("opentelemetry",),
        "prometheus-client": ("prometheus_client",),
        "pydantic-settings": ("pydantic_settings",),
        "pytest-asyncio": ("pytest_asyncio",),
        "pytest-xdist": ("xdist",),
    }
)
TEST_TOOLING: Final = ("pytest", "hypothesis", "testcontainers", "xdist", "ledger_testing")
"""What production code never imports (contract 13)."""


def distribution_of(requirement: str) -> str:
    """``psycopg[binary]>=3.3.6`` → ``psycopg``."""
    found = _DISTRIBUTION.match(requirement)
    return "" if found is None else found[0]


def import_names(distribution: str) -> tuple[str, ...]:
    """The top-level modules ``distribution`` installs."""
    return _IMPORTS.get(distribution, (distribution.replace("-", "_"),))
```

`workspace.py`:

```python
"""The workspace as its manifests describe it: every package, found by glob (§4.1, ADR 0009)."""

import tomllib
from dataclasses import dataclass, field
from graphlib import TopologicalSorter
from pathlib import Path
from typing import ClassVar, Final, final
lazy from collections.abc import Mapping

from ledger_kernel.errors import LedgerError
from ledger_platform.runtime.manifest import InvalidManifestError, PackageKind, PackageManifest, load_manifest

MANIFEST_GLOBS: Final = (
    "packages/*/manifest.toml",
    "modules/*/manifest.toml",
    "countries/*/manifest.toml",
    "countries/*/testing/manifest.toml",
    "apps/*/manifest.toml",
    "tools/*/manifest.toml",
)
DEV_ONLY: Final = frozenset({"ledger-testing", "ledger-devtools"})
"""Distributions never shipped in the image (with every ``*-testing`` one)."""


class WorkspaceError(LedgerError):
    """The workspace cannot be read: no manifest, or manifests the model refuses."""

    code: ClassVar[str] = "workspace_unreadable"

    def __init__(self, problems: tuple[str, ...]) -> None:
        super().__init__("; ".join(problems))


@final
@dataclass(frozen=True, slots=True)
class Package:
    """One workspace member: its directory (relative to the root, POSIX), its manifest and the TOML it came from."""

    directory: Path
    manifest: PackageManifest
    raw: Mapping[str, object] = field(compare=False)

    @property
    def name(self) -> str:
        return self.manifest.package.name

    @property
    def kind(self) -> PackageKind:
        return self.manifest.package.kind

    @property
    def nested_country(self) -> str | None:
        """The country code of a ``countries/<cc>/testing`` package (Decision 27), else ``None``."""
        parts = self.directory.parts
        return parts[1] if len(parts) == 3 and parts[0] == "countries" and parts[2] == "testing" else None

    @property
    def distribution(self) -> str:
        country = self.nested_country
        return self.manifest.distribution if country is None else f"ledger-country-{country}-testing"

    @property
    def import_name(self) -> str:
        return self.distribution.replace("-", "_")

    @property
    def source(self) -> Path:
        """``<directory>/src/<import package>``."""
        return self.directory / "src" / self.import_name

    @property
    def is_production(self) -> bool:
        """Whether the distribution ships in the image."""
        return self.distribution not in DEV_ONLY and not self.distribution.endswith("-testing")


@final
@dataclass(frozen=True, slots=True)
class Workspace:
    """Every package of the repository at ``root``, sorted by distribution."""

    root: Path
    packages: tuple[Package, ...]

    @classmethod
    def load(cls, root: Path) -> Workspace:
        """Read every manifest under ``root``; every problem is reported at once, with its path."""
        found = sorted({path for pattern in MANIFEST_GLOBS for path in root.glob(pattern)})
        if not found:
            raise WorkspaceError((f"no manifest.toml under {root}",))
        packages: list[Package] = []
        problems: list[str] = []
        for path in found:
            relative = path.relative_to(root).as_posix()
            text = path.read_text(encoding="utf-8")
            try:
                manifest = load_manifest(text)
            except InvalidManifestError as error:
                problems.append(f"{relative}: {error}")
                continue
            packages.append(Package(path.parent.relative_to(root), manifest, tomllib.loads(text)))
        if problems:
            raise WorkspaceError(tuple(problems))
        return cls(root, tuple(sorted(packages, key=lambda package: package.distribution)))

    def by_name(self, name: str) -> Package:
        """The package named ``name`` (``KeyError`` if none)."""
        return next(package for package in self.packages if package.name == name)

    def of_kind(self, kind: PackageKind) -> tuple[Package, ...]:
        return tuple(package for package in self.packages if package.kind is kind)

    def module_order(self) -> tuple[Package, ...]:
        """Modules, each after the modules it depends on or consumes from (``check`` proves it is a DAG)."""
        modules = {package.name: package for package in self.of_kind(PackageKind.MODULE)}
        graph = {name: set(module_edges(self, package)) & modules.keys() for name, package in modules.items()}
        return tuple(modules[name] for name in TopologicalSorter(graph).static_order())

    def schemas(self) -> tuple[str, ...]:
        """Every schema in migration order: the platform's, then each module's (then each channel's, Task 18+)."""
        owners = [self.by_name("platform"), *self.module_order(), *self.of_kind(PackageKind.COUNTRY)]
        names: list[str] = []
        for package in owners:
            if package.manifest.schema_name is not None:
                names.append(package.manifest.schema_name)
            names.extend(channel.schema_name for channel in package.manifest.channels)
        return tuple(dict.fromkeys(names))


def module_edges(workspace: Workspace, package: Package) -> tuple[str, ...]:
    """The modules ``package`` depends on: those it imports (declared) and those whose events it consumes."""
    producers = {
        event.type: other.name
        for other in workspace.packages
        for event in other.manifest.events.publishes
    }
    consumed = (
        producers[event_type]
        for consumer in package.manifest.events.consumes
        for event_type in consumer.types
        if event_type in producers
    )
    return tuple(dict.fromkeys((*package.manifest.dependencies.modules, *consumed)) - {package.name})
```

(`dict.fromkeys(...) - {...}` is not valid on a dict: write `tuple(name for name in dict.fromkeys(...) if name != package.name)`. Accessors on `Package` each get a one-line docstring.)

`checks.py` — `check(workspace)` returns the problems below, each `Problem(where=<manifest path>, message=<exact text>)`; one function per rule, composed in `check`:

| Rule | Message |
|---|---|
| name equals directory | `package.name '<name>' is not the directory name '<dir>'` |
| unique schema (modules, platform, channels) | `schema '<s>' is owned by <other> too` |
| unique id prefix | `id prefix '<p>' is minted by <other> too` |
| unique permission code | `permission '<c>' is declared by <other> too` |
| unique published event type | `event type '<t>' is published by <other> too` |
| unique consumer / job kind / schedule name | `consumer '<c>' is declared by <other> too` (and `job kind`, `schedule`) |
| unique http prefix | `http prefix '<p>' is owned by <other> too` |
| consumer and job kind names start with the package name (or the channel key's schema) | `job kind '<k>' does not start with its package's name or channel key` (same for `consumer`, `schedule`) |
| dependencies name existing packages / modules | `dependencies.packages names '<x>', which is not a package` / `dependencies.modules names '<x>', which is not a module` |
| consumed types are published somewhere | `consumer '<c>' handles '<t>', which no package publishes` |
| module graph (imports + subscriptions) is acyclic | `the module graph has a cycle: a -> b -> a` (from `graphlib.CycleError.args[1]`) |
| schedules enqueue a job kind the same package declares | `schedule '<n>' enqueues '<k>', which <package> does not declare` |
| no cron | `schedule '<n>' uses cron, which is not built until a schedule needs it (§16.4)` |
| a nested `countries/<cc>/testing` package is named `<cc>-testing` (Decision 27) | `package.name '<name>' is not '<cc>-testing'` |
| a module's top-level children are layers | `<import>.<child> is not a layer (assembly, adapters, application, api, domain, events, _manifest)` |

The "other" in a duplicate message is the first package (in distribution order) that declared the value.

`generate.py`:

```python
"""What ``sync`` writes: each file derived from the manifests (§4.7).

Text is built deterministically (sorted, stable), so ``check`` can compare it with what is on disk. Python is
formatted by ruff with the repository's configuration, so a generated module passes the gate as written.
"""

import json
import subprocess
from pathlib import Path
from typing import Final
lazy from collections.abc import Iterable, Mapping, Sequence

from ledger_devtools.names import TEST_TOOLING, distribution_of, import_names
from ledger_platform.runtime.manifest import PackageKind
lazy from ledger_devtools.workspace import Package, Workspace

GENERATED_HEADER: Final = (
    "# GENERATED by ledger-devtools sync from manifest.toml: edit the manifest, then run `uv run ledger-devtools sync`.\n"
)
_BUILD: Final = '[build-system]\nrequires = ["uv_build>=0.13.0,<0.14.0"]\nbuild-backend = "uv_build"\n'
_LAYERS: Final = ("assembly", "adapters", "application", "api", "domain", "events", "_manifest")
"""Top to bottom, inside a module or a channel (§4.5)."""
PLATFORM_LAYERS: Final = (
    "runtime",
    "http | grpc",
    "events | jobs",
    "auth",
    "data",
    "observability",
    "config",
)
"""The chassis's own layering (§5.2): fixed by the platform's design, not by a manifest."""


def _string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _list(values: Iterable[str], indent: str = "    ") -> str:
    items = list(values)
    if not items:
        return "[]"
    return "[\n" + "".join(f"{indent}{_string(item)},\n" for item in items) + "]"


def _migrations_directory(package: Package) -> str | None:
    if package.manifest.schema_name is None:
        return None
    if package.kind is PackageKind.PACKAGE:
        return (package.source / "data" / "revisions").as_posix()
    return (package.source / "adapters" / "postgres" / "migrations").as_posix()


def pyproject_text(package: Package, workspace: Workspace) -> str:
    """The ``pyproject.toml`` of ``package``: metadata, dependencies, entry points, coverage, deptry."""
    manifest = package.manifest
    workspace_deps = [
        workspace.by_name(name).distribution
        for name in (*manifest.dependencies.packages, *manifest.dependencies.modules)
    ]
    sections = [
        GENERATED_HEADER,
        "[project]\n"
        f"name = {_string(package.distribution)}\n"
        'version = "0.1.0"\n'
        f"description = {_string(manifest.package.summary)}\n"
        'requires-python = ">=3.15"\n'
        f"dependencies = {_list([*workspace_deps, *manifest.dependencies.python])}\n",
    ]
    scripts = dict(manifest.package.scripts)
    if manifest.app is not None:
        scripts[manifest.app.script] = f"{package.import_name}.main:main"
    if scripts:
        sections.append("[project.scripts]\n" + "".join(f"{k} = {_string(v)}\n" for k, v in sorted(scripts.items())))
    if package.kind is PackageKind.MODULE:
        sections.append(
            '[project.entry-points."ledger.modules"]\n'
            f"{package.name} = {_string(f'{package.import_name}.assembly:MODULE')}\n"
        )
    if manifest.regimes:
        sections.append(
            '[project.entry-points."ledger.countries.regimes"]\n'
            + "".join(f"{regime.key} = {_string(regime.entry_point)}\n" for regime in manifest.regimes)
        )
    if manifest.channels:
        sections.append(
            '[project.entry-points."ledger.countries.channels"]\n'
            + "".join(f'"{channel.key}" = {_string(channel.entry_point)}\n' for channel in manifest.channels)
        )
    if workspace_deps and package.nested_country is None:  # uv refuses a nested member's sources (Decision 27)
        sections.append(
            "[tool.uv.sources]\n" + "".join(f"{name} = {{ workspace = true }}\n" for name in workspace_deps)
        )
    sections.append(_BUILD)
    coverage = (
        "[tool.coverage.run]\nbranch = true\ncore = \"sysmon\"\n"
        f"source_pkgs = [{_string(package.import_name)}]\n"
    )
    migrations = _migrations_directory(package)
    if migrations is not None:
        coverage += f"source = [{_string(migrations)}]\n"
    sections.append(coverage)
    sections.append("[tool.coverage.report]\nfail_under = 100\nshow_missing = true\nskip_covered = true\n")
    renamed = {
        distribution: names
        for requirement in manifest.dependencies.python
        if (names := import_names(distribution := distribution_of(requirement))) != (distribution.replace("-", "_"),)
    }
    if renamed:
        sections.append(
            "[tool.deptry.package_module_name_map]\n"
            + "".join(
                f"{_string(distribution)} = {_string(names[0]) if len(names) == 1 else _list(names)}\n"
                for distribution, names in sorted(renamed.items())
            )
        )
    return "\n".join(sections)


def _python_literal(value: object, depth: int = 0) -> str:
    """``value`` (TOML data: tables, arrays, strings, numbers, booleans, dates) as Python source."""
    match value:
        case bool() | int():
            return repr(value)
        case str():
            return _string(value)
        case list() | tuple():
            return "[" + ", ".join(_python_literal(item, depth + 1) for item in value) + "]"
        case dict():
            return "{" + ", ".join(f"{_string(str(key))}: {_python_literal(item, depth + 1)}" for key, item in value.items()) + "}"
        case _:
            return _string(str(value))  # dates: pydantic reads ISO 8601 text
```

(Write `_python_literal` with `isinstance` narrowing per case so neither checker sees `Any`: `value` arrives as `object` from `Package.raw`, a `Mapping[str, object]`; a `date` falls to the last case. `tomllib` yields `float` only for TOML floats, which no manifest has: the last case would quote one; `check` has no float key to report.)

```python
def _formatted(source: str, path: Path) -> str:
    """``source`` as ``ruff format`` writes it with the repository's configuration."""
    done = subprocess.run(
        ["ruff", "format", "--stdin-filename", path.as_posix(), "-"],
        input=source, capture_output=True, text=True, check=True,
    )
    return done.stdout


def manifest_module_text(package: Package) -> str:
    """``_manifest.py``: the manifest's data, validated by the platform's model when the module is imported."""
    source = (
        '"""GENERATED by ledger-devtools sync from manifest.toml: edit the manifest, then run '
        '`uv run ledger-devtools sync`."""\n\n'
        "from typing import Final\n\n"
        "from ledger_platform.runtime.manifest import PackageManifest\n\n"
        f"MANIFEST: Final = PackageManifest.model_validate({_python_literal(package.raw)})\n"
    )
    return _formatted(source, package.source / "_manifest.py")
```

`importlinter_text(workspace)` writes, in this order (INI, `[importlinter]` then one `[importlinter:contract:<id>]` per contract; lists one item per line, indented four spaces; every list sorted):

1. `[importlinter]`: `root_packages` = every package's import name; `include_external_packages = True`.
2. `workspace-layers` (layers): apps joined with ` | `; then each module in `module_order()` reversed (dependents above), one per line; then countries joined with ` | `; then `ledger_fiscal_kit` when present; `ledger_platform`; `ledger_kernel`.
3. `kernel-is-stdlib-only` (forbidden): source `ledger_kernel`; forbidden = every third-party import name of every manifest plus `TEST_TOOLING`.
4. `platform-layers` (layers, `containers = ledger_platform`, `exhaustive = True`): `PLATFORM_LAYERS`, plus `_manifest` as the lowest layer.
5. per country `<cc>-regime-is-pure` (forbidden): source `ledger_country_<cc>.regime`; forbidden = every other workspace import name but `ledger_kernel`, and every third-party name.
6. `modules-never-import-countries` and `countries-never-import-modules` (forbidden) when both kinds exist; `countries-are-independent` (independence) when there are two or more.
7. per module `<m>-is-protected` (forbidden, `allow_indirect_imports = True`): sources = every other production root except apps; forbidden = those of `<m>.domain`, `<m>.application`, `<m>.adapters`, `<m>.assembly`, `<m>._manifest` that exist on disk.
8. per module `<m>-declared-edges` (forbidden): source `<m>`; forbidden = every other module not in its `dependencies.modules` (omitted when that is empty).
9. per module `<m>-layers` (layers, `containers = <m>`, `exhaustive = True`): the module's existing children in `_LAYERS` order.
10. per module `<m>-domain-is-pure` (forbidden): sources = existing `<m>.domain`, `<m>.events`, `<m>.application`; forbidden = every third-party name.
11. `production-never-imports-test-tooling` (forbidden): sources = every production root; forbidden = `TEST_TOOLING`.
12. `apps-only-compose` (forbidden, `allow_indirect_imports = True`) when apps exist: sources = the apps; forbidden = every module's existing `domain`, `application`, `adapters`, and every country.

`targets(workspace)`: `{.importlinter: importlinter_text}`, every package's `pyproject.toml`, and `_manifest.py` for every package that has a schema or is a country.

`cli.py`:

```python
"""``ledger-devtools``: ``sync`` writes what the manifests imply, ``check`` proves nothing is stale or wrong."""

import argparse
import sys
from pathlib import Path
from typing import Final
lazy from collections.abc import Sequence

from ledger_devtools.checks import check
from ledger_devtools.generate import targets
from ledger_devtools.workspace import Workspace, WorkspaceError

_PROBLEMS: Final = 1
_UNREADABLE: Final = 2


def _stale(workspace: Workspace) -> list[Path]:
    return [
        path
        for path, text in targets(workspace).items()
        if not (workspace.root / path).is_file() or (workspace.root / path).read_text("utf-8") != text
    ]


def main(arguments: Sequence[str] | None = None) -> int:
    """Run ``sync`` or ``check`` on ``--root`` (the current directory by default)."""
    parser = argparse.ArgumentParser(prog="ledger-devtools")
    parser.add_argument("command", choices=("sync", "check"))
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parsed = parser.parse_args(arguments)
    command: str = parsed.command
    root: Path = parsed.root
    try:
        workspace = Workspace.load(root)
    except WorkspaceError as error:
        sys.stderr.write(f"{error}\n")
        return _UNREADABLE
    if command == "sync":
        for path in _stale(workspace):
            (root / path).write_text(targets(workspace)[path], encoding="utf-8", newline="\n")
            sys.stdout.write(f"wrote {path.as_posix()}\n")
        return 0
    problems = [f"{problem.where}: {problem.message}" for problem in check(workspace)]
    problems += [f"{path.as_posix()}: stale, run `uv run ledger-devtools sync`" for path in _stale(workspace)]
    for line in problems:
        sys.stdout.write(f"{line}\n")
    return _PROBLEMS if problems else 0
```

(`parsed.command` is typed `Any` by argparse's stubs: read it through `TypeAdapter(Literal["sync", "check"]).validate_python(parsed.command)` and `TypeAdapter(Path).validate_python(parsed.root)` so no `Any` survives. Compute `targets(workspace)` once in `sync`.)

- [ ] **Step 5: Run the tests and the gate**

Run: `uv run pytest tools/devtools -q` → all pass, 100 % branch coverage of `ledger_devtools`. `uv run poe check` → green.

- [ ] **Step 6: Commit**

```bash
git add -A tools/devtools pyproject.toml uv.lock
git commit -m "feat(devtools): ledger-devtools sync and check over manifests

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Data I — roles, the RLS builder, `chassis_schema_v1`, per-schema migrations

Spec §6.1-§6.5, §2 rules 1 and 8, ADR 0004; Decisions 3, 6, 7, 8. After Task 6. New files beside the v1 ones: identity keeps running on v1's `migrations.py` and `schema.py` until Task 15; Task 16 deletes them.

**Files:**
- Create: `packages/platform/src/ledger_platform/data/{roles,rls,tables,chassis_schema,migrations}.py`, `packages/platform/src/ledger_platform/data/revisions/{__init__,env}.py`, `packages/platform/src/ledger_platform/data/revisions/versions/{__init__,r0001_platform}.py`, `packages/testing/src/ledger_testing/database.py`
- Modify: `packages/testing/pyproject.toml` (depends on `ledger-platform`), `packages/platform/tests/conftest.py` (implements `pytest_ledger_template`), root `pyproject.toml` (coverage `source` gains `packages/platform/src/ledger_platform/data/revisions`; the platform layers contract: `data` sits where it is — the new modules import only `data`, `config` and the kernel)
- Test: `packages/platform/tests/unit/{test_data_rls,test_data_roles}.py`, `packages/platform/tests/integration/{test_data_chassis_schema,test_data_roles_enforced,test_data_migrations}.py`

**Interfaces:**
- Consumes: `Database` (v1, `data/database.py`, for `probing()`), `AnyTable`; `ledger_testing.postgres.{Template, WorkerDatabase, PostgresServer}` (Task 6).
- Produces:
  - `data.roles`: `APP = "ledger_app"`, `MIGRATOR = "ledger_migrator"`, `SIGNER = "ledger_signer"`, `RELAY = "ledger_relay"`; `SchemaRoles(*, schema: str, signer: bool = False)` with `owner`, `rw`; `Logins(*, app: str, migrator: str, signer: str | None = None)`; `cluster_statements(schemas, logins) -> tuple[str, ...]`; `database_statements(schemas) -> tuple[str, ...]`; `relay_statements(schema: str) -> tuple[str, ...]`; `InvalidSchemaNameError` (internal, `invalid_schema_name`).
  - `data.rls`: `RlsScope` (`TENANT`, `COMPANY`, `ORGANIZATION`, `MODED_ORGANIZATION`); `predicate(scope, *, organization_column="organization_id") -> str`; `policy(table: str, scope: RlsScope, *, organization_column="organization_id", purge_expired=False) -> tuple[str, ...]`.
  - `data.tables`: `ChassisTables(outbox, inbox, jobs, subscriptions, idempotency_keys, audit_events)`; `chassis_tables(schema: str) -> ChassisTables` (cached); `PlatformTables(routing, schedule_leases)`; `platform_tables() -> PlatformTables`.
  - `data.chassis_schema`: `chassis_schema_v1(op: Operations, schema: str) -> None`; `platform_schema_v1(op: Operations) -> None`.
  - `data.migrations`: `SchemaMigrations(*, schema: str, scripts: str, metadata: MetaData | None = None)` with `.schema`, `.head`, `.upgrade(url: str, *, lock_wait: timedelta = MIGRATION_LOCK_WAIT) -> None`, `async .readiness(database: Database) -> None` (raises `SchemaNotCurrentError` until ready, then remembers); `run_schema_migrations() -> None` (an `env.py`'s one call); `PLATFORM_MIGRATIONS: Final = SchemaMigrations(schema="platform", scripts="ledger_platform.data:revisions")`; errors `OfflineMigrationsError`, `MigrationTargetMissingError`, `MigrationLockTimeoutError` (unavailable, `migration_lock_timeout`), `SchemaNotCurrentError` (unavailable, `schema_not_current`).
  - `ledger_testing.database`: `TEST_LOGINS = Logins(app="app-password", migrator="migrator-password", signer="signer-password")`; `bootstrap(template: Template, schemas: Sequence[str]) -> None`; `migrate(template: Template, *migrations: SchemaMigrations) -> None` (upgrade as `ledger_migrator`, then the relay grants); `create_chassis(template: Template, schema: str) -> None` (runs `chassis_schema_v1` for a test schema through Alembic `Operations`, as its owner); `fresh_database(server: PostgresServer, schemas: Sequence[str]) -> str` (a new empty database with the schemas, its libpq name returned); `app_url(worker: WorkerDatabase) -> str` and `migrator_url(worker) -> str`.

- [ ] **Step 1: Write the failing tests**

`packages/platform/tests/unit/test_data_rls.py` — the SQL is pinned: changing it is a new version (like `chassis_schema_v1`):

```python
import pytest

from ledger_platform.data.rls import RlsScope, policy, predicate

ORG = "(SELECT NULLIF(current_setting('ledger.organization_id', true), '')::uuid)"
COMPANY = "(SELECT NULLIF(current_setting('ledger.company_id', true), '')::uuid)"
MODE = "(SELECT NULLIF(current_setting('ledger.mode', true), ''))"
PURGE = "((SELECT current_setting('ledger.purge', true)) = 'on' AND expires_at < now())"


@pytest.mark.parametrize(
    ("scope", "expected"),
    [
        (RlsScope.TENANT, f"organization_id = {ORG} AND company_id = {COMPANY} AND mode = {MODE}"),
        (RlsScope.COMPANY, f"organization_id = {ORG} AND company_id = {COMPANY}"),
        (RlsScope.ORGANIZATION, f"organization_id = {ORG}"),
        (RlsScope.MODED_ORGANIZATION, f"organization_id = {ORG} AND mode = {MODE}"),
    ],
)
def test_each_scope_reads_the_settings_through_nullif_once_per_statement(scope: RlsScope, expected: str) -> None:
    assert predicate(scope) == expected


def test_the_organization_column_can_be_named() -> None:
    assert predicate(RlsScope.ORGANIZATION, organization_column="id") == f"id = {ORG}"


def test_a_policy_per_command_forced_on_the_owner() -> None:
    rule = predicate(RlsScope.COMPANY)
    assert policy("identity.branches", RlsScope.COMPANY) == (
        "ALTER TABLE identity.branches ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE identity.branches FORCE ROW LEVEL SECURITY",
        f"CREATE POLICY branches_select ON identity.branches FOR SELECT USING ({rule})",
        f"CREATE POLICY branches_insert ON identity.branches FOR INSERT WITH CHECK ({rule})",
        f"CREATE POLICY branches_update ON identity.branches FOR UPDATE USING ({rule}) WITH CHECK ({rule})",
        f"CREATE POLICY branches_delete ON identity.branches FOR DELETE USING ({rule})",
    )


def test_a_purgeable_table_lets_a_purging_unit_read_and_delete_expired_rows_only() -> None:
    rule = predicate(RlsScope.MODED_ORGANIZATION)
    statements = policy("probe.idempotency_keys", RlsScope.MODED_ORGANIZATION, purge_expired=True)
    assert statements[2] == f"CREATE POLICY idempotency_keys_select ON probe.idempotency_keys FOR SELECT USING (({rule}) OR {PURGE})"
    assert statements[3] == f"CREATE POLICY idempotency_keys_insert ON probe.idempotency_keys FOR INSERT WITH CHECK ({rule})"
    assert statements[5] == f"CREATE POLICY idempotency_keys_delete ON probe.idempotency_keys FOR DELETE USING (({rule}) OR {PURGE})"


@pytest.mark.parametrize("table", ["branches", "Identity.branches", "identity.branches; DROP", "a.b.c"])
def test_a_table_must_be_schema_qualified_and_plain(table: str) -> None:
    with pytest.raises(ValueError, match="schema-qualified"):
        policy(table, RlsScope.TENANT)
```

`packages/platform/tests/unit/test_data_roles.py`:

```python
import pytest

from ledger_platform.data.roles import (
    InvalidSchemaNameError,
    Logins,
    SchemaRoles,
    cluster_statements,
    database_statements,
    relay_statements,
)

LOGINS = Logins(app="a'pp", migrator="m", signer="s")


def test_cluster_roles_are_idempotent_and_never_superusers() -> None:
    statements = cluster_statements((SchemaRoles(schema="platform"), SchemaRoles(schema="identity")), LOGINS)
    assert statements[1] == (
        "DO $$ BEGIN CREATE ROLE ledger_app LOGIN NOINHERIT NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE "
        "PASSWORD 'a''pp'; EXCEPTION WHEN duplicate_object THEN NULL; END $$"
    )
    assert "GRANT identity_owner TO ledger_migrator" in statements
    assert "GRANT identity_rw TO ledger_app" in statements
    assert "GRANT ledger_relay TO ledger_app" in statements
    assert not any("ledger_signer" in statement for statement in statements)


def test_a_signer_schema_belongs_to_the_signer_login_only() -> None:
    statements = cluster_statements((SchemaRoles(schema="platform"), SchemaRoles(schema="signer", signer=True)), LOGINS)
    assert "GRANT signer_rw TO ledger_signer" in statements
    assert "GRANT signer_rw TO ledger_app" not in statements
    assert "GRANT platform_rw TO ledger_signer" in statements


def test_a_signer_schema_needs_the_signer_password() -> None:
    with pytest.raises(InvalidSchemaNameError, match="signer"):
        cluster_statements((SchemaRoles(schema="signer", signer=True),), Logins(app="a", migrator="m"))


def test_each_schema_is_owned_by_its_owner_and_used_by_its_rw_role_and_the_relay() -> None:
    assert database_statements((SchemaRoles(schema="identity"),)) == (
        "CREATE SCHEMA IF NOT EXISTS identity AUTHORIZATION identity_owner",
        "GRANT USAGE ON SCHEMA identity TO identity_rw, ledger_relay",
        "ALTER DEFAULT PRIVILEGES FOR ROLE identity_owner IN SCHEMA identity GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO identity_rw",
        "ALTER DEFAULT PRIVILEGES FOR ROLE identity_owner IN SCHEMA identity GRANT USAGE, SELECT ON SEQUENCES TO identity_rw",
    )


def test_the_relay_reaches_the_queue_tables_and_nothing_else() -> None:
    assert relay_statements("identity") == (
        "GRANT SELECT, UPDATE, DELETE ON identity.outbox TO ledger_relay",
        "GRANT INSERT ON identity.inbox TO ledger_relay",
        "GRANT SELECT ON identity.subscriptions TO ledger_relay",
    )
    assert relay_statements("platform")[-1] == "GRANT SELECT ON platform.routing TO ledger_relay"


@pytest.mark.parametrize("schema", ["Identity", "a", "identity-x", "x" * 32, "public; DROP"])
def test_a_schema_name_that_could_not_be_a_role_prefix_is_refused(schema: str) -> None:
    with pytest.raises(InvalidSchemaNameError):
        SchemaRoles(schema=schema)
```

`packages/platform/tests/conftest.py` — implement the template hook (the v1 fixtures stay until Task 16):

```python
from ledger_platform.data.migrations import PLATFORM_MIGRATIONS
from ledger_testing.database import bootstrap, create_chassis, migrate
from ledger_testing.postgres import Template

SCHEMAS = ("platform", "probe", "other")
"""The platform's schema, and two test schemas with the chassis's tables: one unit's and another module's."""


def pytest_ledger_template(template: Template) -> None:
    bootstrap(template, SCHEMAS)
    migrate(template, PLATFORM_MIGRATIONS)
    create_chassis(template, "probe")
    create_chassis(template, "other")
```

`packages/platform/tests/integration/test_data_roles_enforced.py` (connects as `ledger_app` to the worker's database; each test is one `psycopg` connection, so session state is visible):

```python
import psycopg
import pytest

from ledger_testing.database import TEST_LOGINS
from ledger_testing.postgres import WorkerDatabase


def app(worker_database: WorkerDatabase) -> psycopg.Connection:
    return psycopg.connect(
        worker_database.server.libpq(worker_database.name).replace(
            f"{worker_database.server.user}:{worker_database.server.password}", f"ledger_app:{TEST_LOGINS.app}"
        )
    )


def test_the_app_login_reaches_no_table_without_a_module_role(worker_database: WorkerDatabase) -> None:
    with app(worker_database) as connection, pytest.raises(psycopg.errors.InsufficientPrivilege):
        connection.execute("SELECT 1 FROM probe.outbox")


def test_a_unit_in_a_module_role_reaches_its_schema_only(worker_database: WorkerDatabase) -> None:
    with app(worker_database) as connection:
        with connection.transaction():
            connection.execute("SET LOCAL ROLE probe_rw")
            connection.execute("SELECT count(*) FROM probe.outbox")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                with connection.transaction():  # a savepoint: the denial does not end the outer transaction
                    connection.execute("SELECT count(*) FROM other.outbox")
        assert connection.execute("SELECT current_user").fetchone() == ("ledger_app",)  # SET LOCAL ended with it


def test_the_rw_role_cannot_change_the_schema(worker_database: WorkerDatabase) -> None:
    with app(worker_database) as connection, connection.transaction():
        connection.execute("SET LOCAL ROLE probe_rw")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute("CREATE TABLE probe.extra (n int)")


def test_audit_events_are_append_only_for_the_rw_role(worker_database: WorkerDatabase) -> None:
    with app(worker_database) as connection, connection.transaction():
        connection.execute("SET LOCAL ROLE probe_rw")
        connection.execute(
            "INSERT INTO probe.audit_events (id, occurred_at, actor, call_id, action, subject_type, subject_id)"
            " VALUES (gen_random_uuid(), now(), 'system:test', 'c', 'probe.touch', 'probe', 'x')"
        )
        for statement in ("UPDATE probe.audit_events SET actor = 'x'", "DELETE FROM probe.audit_events", "TRUNCATE probe.audit_events"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege), connection.transaction():
                connection.execute(statement)


def test_the_app_login_cannot_become_an_owner(worker_database: WorkerDatabase) -> None:
    with app(worker_database) as connection, pytest.raises(psycopg.errors.InsufficientPrivilege):
        connection.execute("SET ROLE probe_owner")
```

(Use a helper in `ledger_testing.database`, `libpq_as(worker, role, password) -> str`, instead of the `replace` above; it is spelled out here only to show which login the test uses.)

`packages/platform/tests/integration/test_data_chassis_schema.py`:

- `test_every_chassis_table_exists_in_the_schema`: the six tables of §6.4 in `probe` (`pg_tables`), and `routing` and `schedule_leases` in `platform`, `platform.routing` holding exactly one row, version 0.
- `test_the_models_match_the_migrated_tables`: Alembic's comparison (`ledger_testing.migrations` helper) of `chassis_tables("probe")` against the database reports no difference for tables, columns, types, nullability and primary keys; the partial indexes' definitions (`pg_indexes.indexdef`) are the ones of §6.4, byte for byte after PostgreSQL's normalization (pin the normalized text the test reads the first time; the test then guards it).
- `test_storage_parameters_favour_queues`: `pg_class.reloptions` of `probe.outbox` is `["fillfactor=90", "autovacuum_vacuum_scale_factor=0.0", "autovacuum_vacuum_threshold=1000", "autovacuum_vacuum_cost_delay=0"]`, of `inbox` and `jobs` the same with `fillfactor=80`.
- `test_idempotency_keys_are_seen_per_organization_and_mode`: as `probe_rw`, with settings for organization A in `test`, insert a key; the same unit sees it; a unit of A in `live`, of B in `test`, and one with no settings see none; an `INSERT` for B while scoped to A is refused (`new row violates row-level security policy`).
- `test_a_purging_unit_sees_expired_keys_and_nothing_else`: keys of A (expired), B (expired), B (live): a unit with `ledger.purge = 'on'` and no tenant sees and deletes the two expired ones; the live one survives and stays invisible to it.
- `test_queue_tables_are_outside_row_level_security`: a unit with no settings reads and writes `outbox`, `inbox`, `jobs`, `subscriptions`.
- `test_payloads_above_64_kib_are_refused_by_the_table`: an `outbox` row with a 65,537-byte payload violates `outbox_payload_check`.

`packages/platform/tests/integration/test_data_migrations.py`:

- `test_the_platform_schema_migrates_to_head_and_a_rerun_does_nothing` — on a `fresh_database(postgres, ("platform",))`: `PLATFORM_MIGRATIONS.upgrade(url)` twice; `platform.alembic_version` holds `PLATFORM_MIGRATIONS.head`; `platform.routing` one row.
- `test_tables_belong_to_the_owner_role` — `pg_tables.tableowner` is `platform_owner` for every table of `platform`.
- `test_a_second_migrator_waits_for_the_first_then_gives_up` — a session holds the schema's advisory lock (`SELECT pg_advisory_lock(<key>)`, key from `SchemaMigrations.lock_key`), `upgrade(url, lock_wait=timedelta(seconds=1))` raises `MigrationLockTimeoutError` (code `migration_lock_timeout`), message naming the schema and the wait.
- `test_readiness_follows_the_version_table` — on a fresh database: `readiness` raises `SchemaNotCurrentError` before any upgrade (no version table), passes after it, and passes for a revision this code does not know (`UPDATE platform.alembic_version SET version_num = 'ffff'` as owner: a newer version migrated it). Once passed it does not query again (count probe connections with a `Database` whose `probing` is wrapped).
- `test_offline_mode_is_refused` and `test_alembic_run_without_a_target_is_refused` — run `alembic.command.upgrade` with `sql=True`, and with a `Config` lacking the target attribute: `OfflineMigrationsError`, `MigrationTargetMissingError`.

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest packages/platform/tests/unit/test_data_rls.py packages/platform/tests/unit/test_data_roles.py -q -p no:randomly`
Expected: `ModuleNotFoundError: No module named 'ledger_platform.data.rls'` (the integration modules fail at the template hook's imports).

- [ ] **Step 3: Implement the SQL builders**

`data/roles.py`:

```python
"""The roles, schemas and grants of §6.2, as SQL statements: one builder for ``init.sql`` (devtools), the test
template (``ledger_testing``) and ``ledger-migrate``'s relay grants.

Roles are the cluster's, shared by every database on it, so each ``CREATE ROLE`` tolerates one that exists. No
role is a superuser or ``BYPASSRLS``. Logins are ``NOINHERIT``: ``ledger_app`` reaches a module's tables only after
``SET LOCAL ROLE <schema>_rw`` (one unit at a time), and ``ledger_migrator`` a schema only after ``SET ROLE
<schema>_owner``. Schema names are checked before they reach SQL; passwords are written as SQL literals.
"""

import re
from dataclasses import dataclass
from typing import ClassVar, Final, final
lazy from collections.abc import Sequence

from ledger_kernel.errors import LedgerError

APP: Final = "ledger_app"
MIGRATOR: Final = "ledger_migrator"
SIGNER: Final = "ledger_signer"
RELAY: Final = "ledger_relay"
_SCHEMA: Final = re.compile(r"[a-z][a-z0-9_]{1,30}", re.ASCII)
_PLAIN: Final = "NOSUPERUSER NOBYPASSRLS"
_LOGIN: Final = f"LOGIN NOINHERIT {_PLAIN} NOCREATEDB NOCREATEROLE"


class InvalidSchemaNameError(LedgerError):
    """A schema name that cannot be a role prefix, or roles that cannot be made: a defect of the caller."""

    code: ClassVar[str] = "invalid_schema_name"

    def __init__(self, problem: str) -> None:
        super().__init__(problem)


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class SchemaRoles:
    """A schema and its two roles; ``signer``: its ``rw`` role belongs to ``ledger_signer``, not ``ledger_app``."""

    schema: str
    signer: bool = False

    def __post_init__(self) -> None:
        if _SCHEMA.fullmatch(self.schema) is None:
            raise InvalidSchemaNameError("a schema name is 2 to 31 lower-case letters, digits or underscores")

    @property
    def owner(self) -> str:
        return f"{self.schema}_owner"

    @property
    def rw(self) -> str:
        return f"{self.schema}_rw"


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Logins:
    """The passwords of the login roles (local and test values only; production takes them from secrets)."""

    app: str
    migrator: str
    signer: str | None = None


def _literal(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def _role(name: str, options: str) -> str:
    return f"DO $$ BEGIN CREATE ROLE {name} {options}; EXCEPTION WHEN duplicate_object THEN NULL; END $$"


def cluster_statements(schemas: Sequence[SchemaRoles], logins: Logins) -> tuple[str, ...]:
    """The roles of every schema and the logins, with their memberships (run once per server)."""
    statements = [
        _role(RELAY, f"NOLOGIN {_PLAIN}"),
        _role(APP, f"{_LOGIN} PASSWORD {_literal(logins.app)}"),
        _role(MIGRATOR, f"{_LOGIN} PASSWORD {_literal(logins.migrator)}"),
    ]
    signed = any(schema.signer for schema in schemas)
    if signed:
        if logins.signer is None:
            raise InvalidSchemaNameError("a signer schema needs the signer login's password")
        statements.append(_role(SIGNER, f"{_LOGIN} PASSWORD {_literal(logins.signer)}"))
    for schema in schemas:
        statements += [
            _role(schema.owner, f"NOLOGIN {_PLAIN}"),
            _role(schema.rw, f"NOLOGIN {_PLAIN}"),
            f"GRANT {schema.owner} TO {MIGRATOR}",
            f"GRANT {schema.rw} TO {SIGNER if schema.signer else APP}",
        ]
    statements.append(f"GRANT {RELAY} TO {APP}")
    if signed:
        statements.append(f"GRANT platform_rw TO {SIGNER}")
    return tuple(statements)


def database_statements(schemas: Sequence[SchemaRoles]) -> tuple[str, ...]:
    """Each schema, owned by its owner role, used by its ``rw`` role and the relay (run in the database)."""
    statements: list[str] = []
    for schema in schemas:
        name, owner, rw = schema.schema, schema.owner, schema.rw
        default = f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner} IN SCHEMA {name} GRANT"
        statements += [
            f"CREATE SCHEMA IF NOT EXISTS {name} AUTHORIZATION {owner}",
            f"GRANT USAGE ON SCHEMA {name} TO {rw}, {RELAY}",
            f"{default} SELECT, INSERT, UPDATE, DELETE ON TABLES TO {rw}",
            f"{default} USAGE, SELECT ON SEQUENCES TO {rw}",
        ]
    return tuple(statements)


def relay_statements(schema: str) -> tuple[str, ...]:
    """What ``ledger_relay`` may do in ``schema`` once its tables exist (run by ``ledger-migrate`` as the owner)."""
    name = SchemaRoles(schema=schema).schema
    statements = [
        f"GRANT SELECT, UPDATE, DELETE ON {name}.outbox TO {RELAY}",
        f"GRANT INSERT ON {name}.inbox TO {RELAY}",
        f"GRANT SELECT ON {name}.subscriptions TO {RELAY}",
    ]
    if name == "platform":
        statements.append(f"GRANT SELECT ON platform.routing TO {RELAY}")
    return tuple(statements)
```

`data/rls.py`:

```python
"""Row-level security policies from one builder (§6.3, audit D13): one policy per command, forced on the owner.

Settings are read through ``NULLIF(..., '')`` because ``current_setting(name, true)`` answers ``''``, not ``NULL``,
once a pooled connection has seen the setting, and ``''::uuid`` would raise; wrapped in ``(SELECT ...)`` they are
evaluated once per statement. A missing setting matches nothing: a unit without a scope sees no protected row.

The text is versioned like ``chassis_schema_v1``: migrations call ``policy`` and their meaning must not move, so
the tests pin every statement, and a different policy is a new function.
"""

import re
from enum import StrEnum
from typing import Final

_TABLE: Final = re.compile(r"([a-z][a-z0-9_]{1,30})\.([a-z][a-z0-9_]{0,40})", re.ASCII)
_ORGANIZATION: Final = "(SELECT NULLIF(current_setting('ledger.organization_id', true), '')::uuid)"
_COMPANY: Final = "(SELECT NULLIF(current_setting('ledger.company_id', true), '')::uuid)"
_MODE: Final = "(SELECT NULLIF(current_setting('ledger.mode', true), ''))"
_PURGING: Final = "((SELECT current_setting('ledger.purge', true)) = 'on' AND expires_at < now())"


class RlsScope(StrEnum):
    """Whose rows a table holds (§6.3; ``company`` is Decision 3)."""

    TENANT = "tenant"
    """A company's rows in one mode."""
    COMPANY = "company"
    """A company's rows shared by both modes (establishments, branches)."""
    ORGANIZATION = "organization"
    """An organization's rows, visible to it and to each of its companies."""
    MODED_ORGANIZATION = "moded_organization"
    """An organization's rows in one mode (API clients, idempotency keys)."""


class _UnqualifiedTableError(ValueError):
    def __init__(self) -> None:
        super().__init__("a policy names a schema-qualified table: <schema>.<table>, lower case")


def predicate(scope: RlsScope, *, organization_column: str = "organization_id") -> str:
    """The condition a row of ``scope`` must meet in the current unit."""
    match scope:
        case RlsScope.TENANT:
            return f"organization_id = {_ORGANIZATION} AND company_id = {_COMPANY} AND mode = {_MODE}"
        case RlsScope.COMPANY:
            return f"organization_id = {_ORGANIZATION} AND company_id = {_COMPANY}"
        case RlsScope.ORGANIZATION:
            return f"{organization_column} = {_ORGANIZATION}"
        case RlsScope.MODED_ORGANIZATION:
            return f"organization_id = {_ORGANIZATION} AND mode = {_MODE}"


def policy(
    table: str, scope: RlsScope, *, organization_column: str = "organization_id", purge_expired: bool = False
) -> tuple[str, ...]:
    """DDL that puts ``table`` (``schema.table``) under ``scope``: one permissive policy per command.

    ``purge_expired`` (idempotency keys, Decision 6): a unit that set ``ledger.purge = 'on'`` may also read and
    delete rows whose ``expires_at`` has passed, and nothing else.
    """
    found = _TABLE.fullmatch(table)
    if found is None:
        raise _UnqualifiedTableError
    name = found[2]
    rule = predicate(scope, organization_column=organization_column)
    readable = f"({rule}) OR {_PURGING}" if purge_expired else rule
    return (
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        f"CREATE POLICY {name}_select ON {table} FOR SELECT USING ({readable})",
        f"CREATE POLICY {name}_insert ON {table} FOR INSERT WITH CHECK ({rule})",
        f"CREATE POLICY {name}_update ON {table} FOR UPDATE USING ({rule}) WITH CHECK ({rule})",
        f"CREATE POLICY {name}_delete ON {table} FOR DELETE USING ({readable})",
    )
```

(`match` over a `StrEnum` without a default case: both checkers prove exhaustiveness; the function needs no fallback.)

`data/chassis_schema.py`:

```python
"""``chassis_schema_v1`` (§6.4): the tables every module, channel and the platform keep in their own schema.

Called by each schema's first revision. Versioned and immutable: a change ships ``chassis_schema_v2`` and every
schema adds a revision that calls it (audit D20). The DDL is the spec's, statement for statement; ``{s}`` is the
schema, checked before it is substituted.
"""

from typing import Final
lazy from alembic.operations import Operations

from ledger_platform.data.rls import RlsScope, policy
from ledger_platform.data.roles import SchemaRoles

_QUEUE: Final = "autovacuum_vacuum_scale_factor = 0.0, autovacuum_vacuum_threshold = 1000, autovacuum_vacuum_cost_delay = 0"
_V1: Final = (
    f"""CREATE TABLE {{s}}.outbox (
  id uuid PRIMARY KEY,
  type text NOT NULL, version smallint NOT NULL, source text NOT NULL,
  organization_id uuid, company_id uuid, mode text CHECK (mode IN ('test','live')),
  subject uuid, aggregate_version bigint,
  occurred_at timestamptz NOT NULL, traceparent text,
  payload jsonb NOT NULL CHECK (octet_length(payload::text) <= 65536),
  attempts int NOT NULL DEFAULT 0,
  unrouted_since timestamptz
) WITH (fillfactor = 90, {_QUEUE})""",
    "CREATE INDEX outbox_due ON {s}.outbox (id) WHERE unrouted_since IS NULL",
    f"""CREATE TABLE {{s}}.inbox (
  consumer text NOT NULL, event_id uuid NOT NULL,
  type text NOT NULL, version smallint NOT NULL, source text NOT NULL,
  organization_id uuid, company_id uuid, mode text,
  subject uuid, aggregate_version bigint, occurred_at timestamptz NOT NULL, traceparent text,
  payload jsonb NOT NULL,
  status text NOT NULL DEFAULT 'ready' CHECK (status IN ('ready','running','dead')),
  attempts int NOT NULL DEFAULT 0, next_attempt_at timestamptz NOT NULL DEFAULT now(),
  lease_until timestamptz, lease_token uuid, last_error_code text,
  PRIMARY KEY (consumer, event_id)
) WITH (fillfactor = 80, {_QUEUE})""",
    "CREATE INDEX inbox_due ON {s}.inbox (consumer, next_attempt_at) WHERE status = 'ready'",
    "CREATE INDEX inbox_expired ON {s}.inbox (consumer, lease_until) WHERE status = 'running'",
    "CREATE INDEX inbox_head ON {s}.inbox (consumer, subject, event_id) WHERE status <> 'dead'",
    f"""CREATE TABLE {{s}}.jobs (
  kind text NOT NULL, key text NOT NULL,
  organization_id uuid, company_id uuid, mode text,
  status text NOT NULL CHECK (status IN ('ready','running','parked','done','dead')),
  run_at timestamptz NOT NULL, lease_until timestamptz, lease_token uuid,
  attempts int NOT NULL DEFAULT 0, max_attempts int NOT NULL, deadline_at timestamptz,
  parked_reason text, payload jsonb NOT NULL DEFAULT '{{}}', last_error_code text, traceparent text,
  created_at timestamptz NOT NULL, updated_at timestamptz NOT NULL,
  PRIMARY KEY (kind, key)
) WITH (fillfactor = 80, {_QUEUE})""",
    "CREATE INDEX jobs_due ON {s}.jobs (kind, run_at) WHERE status = 'ready'",
    "CREATE INDEX jobs_expired ON {s}.jobs (kind, lease_until) WHERE status = 'running'",
    "CREATE INDEX jobs_parked ON {s}.jobs (kind, company_id, parked_reason) WHERE status = 'parked'",
    """CREATE TABLE {s}.subscriptions (
  consumer text NOT NULL, event_type text NOT NULL, active boolean NOT NULL,
  updated_at timestamptz NOT NULL, PRIMARY KEY (consumer, event_type))""",
    """CREATE TABLE {s}.idempotency_keys (
  organization_id uuid NOT NULL, mode text NOT NULL, principal text NOT NULL,
  route text NOT NULL,
  key text NOT NULL CHECK (length(key) <= 255),
  request_hash bytea NOT NULL, response_status smallint, response_body bytea, response_headers jsonb,
  created_at timestamptz NOT NULL, expires_at timestamptz NOT NULL,
  PRIMARY KEY (organization_id, mode, principal, route, key))""",
    "CREATE INDEX idempotency_expiry ON {s}.idempotency_keys (expires_at)",
    """CREATE TABLE {s}.audit_events (
  id uuid PRIMARY KEY, occurred_at timestamptz NOT NULL,
  organization_id uuid, company_id uuid, mode text,
  actor text NOT NULL,
  call_id text NOT NULL,
  action text NOT NULL, subject_type text NOT NULL, subject_id text NOT NULL,
  before_hash bytea, after_hash bytea,
  detail jsonb NOT NULL DEFAULT '{}')""",
    "REVOKE UPDATE, DELETE, TRUNCATE ON {s}.audit_events FROM {s}_rw",
)
"""§6.4's DDL. ``{s}`` is replaced with ``str.replace`` (never ``format``: the DDL holds ``'{}'``); the doubled
braces above are the f-strings' own escapes of the jsonb defaults."""


def chassis_schema_v1(op: Operations, schema: str) -> None:
    """Create the chassis's tables in ``schema``, then put ``idempotency_keys`` under its RLS (Decision 6)."""
    name = SchemaRoles(schema=schema).schema
    for statement in _V1[:-1]:
        op.execute(statement.replace("{s}", name))
    for statement in policy(f"{name}.idempotency_keys", RlsScope.MODED_ORGANIZATION, purge_expired=True):
        op.execute(statement)
    op.execute(_V1[-1].replace("{s}", name))


def platform_schema_v1(op: Operations) -> None:
    """The platform's own tables (§6.4): the routing version (one row) and the schedules' lease rows."""
    op.execute("CREATE TABLE platform.routing (version bigint NOT NULL)")
    op.execute("CREATE UNIQUE INDEX routing_one_row ON platform.routing ((true))")
    op.execute("INSERT INTO platform.routing (version) VALUES (0)")
    op.execute(
        "CREATE TABLE platform.schedule_leases ("
        "name text PRIMARY KEY, holder text, leased_until timestamptz, next_run_at timestamptz NOT NULL)"
    )
```

(The jobs statement is an f-string, so its default is written `'{{}}'` and reads `'{}'`; the audit statement is a plain string and writes `'{}'` directly. A unit test asserts `"DEFAULT '{}'"` appears exactly twice in `"".join(_V1)` and `"{{"` never does.)

`data/tables.py` — SQLAlchemy Core tables equal to the DDL above, for the engines (Task 9-11) and for the model-equals-migration test:

```python
"""The chassis's tables as SQLAlchemy Core tables, one set per schema (the DDL is ``chassis_schema``'s)."""

from dataclasses import dataclass
from functools import cache
from typing import final

from sqlalchemy import (
    BigInteger, Boolean, Column, DateTime, Index, Integer, LargeBinary, MetaData, SmallInteger, Table, Text, Uuid, text,
)
from sqlalchemy.dialects.postgresql import JSONB

from ledger_platform.data.roles import SchemaRoles
lazy from ledger_platform.data.database import AnyTable


def _moment(name: str, *, nullable: bool = True) -> Column[object]:
    return Column(name, DateTime(timezone=True), nullable=nullable)


def _envelope() -> tuple[Column[object], ...]:
    return (
        Column("type", Text, nullable=False),
        Column("version", SmallInteger, nullable=False),
        Column("source", Text, nullable=False),
        Column("organization_id", Uuid),
        Column("company_id", Uuid),
        Column("mode", Text),
        Column("subject", Uuid),
        Column("aggregate_version", BigInteger),
        _moment("occurred_at", nullable=False),
        Column("traceparent", Text),
        Column("payload", JSONB, nullable=False),
    )


@final
@dataclass(frozen=True, slots=True)
class ChassisTables:
    """One schema's chassis tables."""

    outbox: AnyTable
    inbox: AnyTable
    jobs: AnyTable
    subscriptions: AnyTable
    idempotency_keys: AnyTable
    audit_events: AnyTable


@cache
def chassis_tables(schema: str) -> ChassisTables:
    """The chassis's tables in ``schema`` (built once per schema)."""
    metadata = MetaData(schema=SchemaRoles(schema=schema).schema)
    outbox = Table(
        "outbox", metadata,
        Column("id", Uuid, primary_key=True), *_envelope(),
        Column("attempts", Integer, nullable=False, server_default=text("0")),
        _moment("unrouted_since"),
        Index("outbox_due", "id", postgresql_where=text("unrouted_since IS NULL")),
    )
    inbox = Table(
        "inbox", metadata,
        Column("consumer", Text, primary_key=True), Column("event_id", Uuid, primary_key=True), *_envelope(),
        Column("status", Text, nullable=False, server_default=text("'ready'")),
        Column("attempts", Integer, nullable=False, server_default=text("0")),
        Column("next_attempt_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
        _moment("lease_until"), Column("lease_token", Uuid), Column("last_error_code", Text),
        Index("inbox_due", "consumer", "next_attempt_at", postgresql_where=text("status = 'ready'")),
        Index("inbox_expired", "consumer", "lease_until", postgresql_where=text("status = 'running'")),
        Index("inbox_head", "consumer", "subject", "event_id", postgresql_where=text("status <> 'dead'")),
    )
    jobs = Table(
        "jobs", metadata,
        Column("kind", Text, primary_key=True), Column("key", Text, primary_key=True),
        Column("organization_id", Uuid), Column("company_id", Uuid), Column("mode", Text),
        Column("status", Text, nullable=False),
        _moment("run_at", nullable=False), _moment("lease_until"), Column("lease_token", Uuid),
        Column("attempts", Integer, nullable=False, server_default=text("0")),
        Column("max_attempts", Integer, nullable=False), _moment("deadline_at"),
        Column("parked_reason", Text), Column("payload", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
        Column("last_error_code", Text), Column("traceparent", Text),
        _moment("created_at", nullable=False), _moment("updated_at", nullable=False),
        Index("jobs_due", "kind", "run_at", postgresql_where=text("status = 'ready'")),
        Index("jobs_expired", "kind", "lease_until", postgresql_where=text("status = 'running'")),
        Index("jobs_parked", "kind", "company_id", "parked_reason", postgresql_where=text("status = 'parked'")),
    )
    subscriptions = Table(
        "subscriptions", metadata,
        Column("consumer", Text, primary_key=True), Column("event_type", Text, primary_key=True),
        Column("active", Boolean, nullable=False), _moment("updated_at", nullable=False),
    )
    idempotency_keys = Table(
        "idempotency_keys", metadata,
        Column("organization_id", Uuid, primary_key=True), Column("mode", Text, primary_key=True),
        Column("principal", Text, primary_key=True), Column("route", Text, primary_key=True),
        Column("key", Text, primary_key=True),
        Column("request_hash", LargeBinary, nullable=False), Column("response_status", SmallInteger),
        Column("response_body", LargeBinary), Column("response_headers", JSONB),
        _moment("created_at", nullable=False), _moment("expires_at", nullable=False),
        Index("idempotency_expiry", "expires_at"),
    )
    audit_events = Table(
        "audit_events", metadata,
        Column("id", Uuid, primary_key=True), _moment("occurred_at", nullable=False),
        Column("organization_id", Uuid), Column("company_id", Uuid), Column("mode", Text),
        Column("actor", Text, nullable=False), Column("call_id", Text, nullable=False),
        Column("action", Text, nullable=False), Column("subject_type", Text, nullable=False),
        Column("subject_id", Text, nullable=False),
        Column("before_hash", LargeBinary), Column("after_hash", LargeBinary),
        Column("detail", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
    return ChassisTables(outbox, inbox, jobs, subscriptions, idempotency_keys, audit_events)


@final
@dataclass(frozen=True, slots=True)
class PlatformTables:
    """The platform schema's own tables."""

    routing: AnyTable
    schedule_leases: AnyTable


@cache
def platform_tables() -> PlatformTables:
    """``platform.routing`` and ``platform.schedule_leases``."""
    metadata = MetaData(schema="platform")
    routing = Table("routing", metadata, Column("version", BigInteger, nullable=False))
    leases = Table(
        "schedule_leases", metadata,
        Column("name", Text, primary_key=True), Column("holder", Text),
        _moment("leased_until"), _moment("next_run_at", nullable=False),
    )
    return PlatformTables(routing, leases)
```

(`Column[object]` is how `AnyTable` spells untyped columns, as `data/database.py` explains. `routing` has no primary key: SQLAlchemy Core tables accept that; statements on it are `select(routing.c.version)` and `update(routing)`.)

- [ ] **Step 4: Implement the migrations runner and the platform's revision**

`data/migrations.py`:

```python
"""Each schema's Alembic revisions, run under its owner role (§6.5, ADR 0004).

``SchemaMigrations`` names one schema's revisions (a package resource, ``ledger_identity.adapters.postgres:migrations``).
``upgrade(url)`` connects as ``ledger_migrator`` directly to PostgreSQL (never through a pooler: it holds a session
lock), takes the schema's advisory lock (waiting at most ``MIGRATION_LOCK_WAIT``), becomes ``<schema>_owner`` and
runs every pending revision, each in its own transaction, with Alembic's version table inside the schema. A
second migrator waits for the first, then finds head and does nothing; every other lock is waited for at most
ten seconds. ``readiness`` is the running app's check: ready when the schema is at this version's head or at a
revision this version does not know (a newer version migrated it during a rollout); a schema without a version
table was never migrated. A revision package's ``env.py`` is one call: ``run_schema_migrations()``.
"""

import hashlib
from dataclasses import dataclass
from datetime import timedelta
from typing import ClassVar, Final, final
lazy from collections.abc import Callable

import psycopg
from alembic import command, context
from alembic.config import Config
from alembic.script import ScriptDirectory
from pydantic import InstanceOf, TypeAdapter, ValidationError
from sqlalchemy import Connection, MetaData, create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import NullPool

from ledger_kernel.errors import LedgerError, UnavailableError
from ledger_platform.data.roles import SchemaRoles
lazy from ledger_platform.data.database import Database

MIGRATION_LOCK_WAIT: Final = timedelta(minutes=10)
_OTHER_LOCKS: Final = "10s"
_TARGET: Final = "ledger.target"
_MILLISECOND: Final = timedelta(milliseconds=1)


class OfflineMigrationsError(LedgerError):
    """Someone asked for the migrations as SQL text: they run against a database only."""

    code: ClassVar[str] = "offline_migrations"

    def __init__(self) -> None:
        super().__init__("migrations run against a database, not as SQL text")


class MigrationTargetMissingError(LedgerError):
    """Alembic was run directly, not through ``SchemaMigrations``: it knows no schema or connection."""

    code: ClassVar[str] = "migration_target_missing"

    def __init__(self) -> None:
        super().__init__("the migrations were started without a target: run them through SchemaMigrations")


class MigrationLockTimeoutError(UnavailableError):
    """Another migrator held the schema's lock for longer than this one waits."""

    code: ClassVar[str] = "migration_lock_timeout"

    def __init__(self, schema: str, waited: timedelta) -> None:
        super().__init__(f"another migration of schema {schema} held its lock for longer than {waited}")


class SchemaNotCurrentError(UnavailableError):
    """The schema is behind this version's head, or was never migrated."""

    code: ClassVar[str] = "schema_not_current"

    def __init__(self, schema: str, head: str) -> None:
        super().__init__(f"schema {schema} is not at revision {head}, the one this version needs")


@final
@dataclass(frozen=True, slots=True)
class _Target:
    schema: str
    connection: Connection
    metadata: MetaData | None


_TARGET_OF: Final = TypeAdapter(InstanceOf[_Target])


def run_schema_migrations() -> None:
    """The whole of a revision package's ``env.py``: run the pending revisions on the target's connection."""
    if context.is_offline_mode():
        raise OfflineMigrationsError
    try:
        target = _TARGET_OF.validate_python(context.config.attributes.get(_TARGET))
    except ValidationError as error:
        raise MigrationTargetMissingError from error
    context.configure(
        connection=target.connection,
        target_metadata=target.metadata,
        version_table_schema=target.schema,
        transaction_per_migration=True,
    )
    with context.begin_transaction():
        context.run_migrations()


@final
class SchemaMigrations:
    """One schema's revisions (see the module)."""

    __slots__ = ("_ready", "_scripts", "metadata", "schema")

    def __init__(self, *, schema: str, scripts: str, metadata: MetaData | None = None) -> None:
        self.schema = SchemaRoles(schema=schema).schema
        self.metadata = metadata
        self._scripts = scripts
        self._ready = False

    def _config(self) -> Config:
        config = Config()
        config.set_main_option("script_location", self._scripts)
        return config

    @property
    def head(self) -> str:
        """The revision this version's scripts end at."""
        head = ScriptDirectory.from_config(self._config()).get_current_head()
        return "" if head is None else head

    @property
    def lock_key(self) -> int:
        """The schema's advisory lock (a signed 64-bit number derived from its name)."""
        return int.from_bytes(hashlib.sha256(f"ledger.migrate.{self.schema}".encode()).digest()[:8], "big", signed=True)

    def upgrade(self, url: str, *, lock_wait: timedelta = MIGRATION_LOCK_WAIT) -> None:
        """Bring the schema at ``url`` (as ``ledger_migrator``, direct) to head (see the module)."""
        engine = create_engine(url, poolclass=NullPool)
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT set_config('lock_timeout', :wait, false)"), {"wait": f"{lock_wait // _MILLISECOND}ms"})
                try:
                    connection.execute(text("SELECT pg_advisory_lock(:key)"), {"key": self.lock_key})
                except OperationalError as error:
                    if isinstance(error.orig, psycopg.errors.LockNotAvailable):
                        raise MigrationLockTimeoutError(self.schema, lock_wait) from error
                    raise
                connection.execute(text("SELECT set_config('lock_timeout', :wait, false)"), {"wait": _OTHER_LOCKS})
                connection.execute(text(f"SET ROLE {self.schema}_owner"))
                connection.commit()
                config = self._config()
                config.attributes[_TARGET] = _Target(self.schema, connection, self.metadata)
                command.upgrade(config, "head")
        finally:
            engine.dispose()  # closing the session releases the advisory lock and the role

    async def readiness(self, database: Database) -> None:
        """Raise ``SchemaNotCurrentError`` until the schema is at head or past it; then never ask again."""
        if self._ready:
            return
        known = {revision.revision for revision in ScriptDirectory.from_config(self._config()).walk_revisions()}
        async with database.probing() as connection:
            await connection.execute(text(f"SET LOCAL ROLE {self.schema}_rw"))
            exists = (await connection.execute(text("SELECT to_regclass(:table) IS NOT NULL"), {"table": f"{self.schema}.alembic_version"})).scalar_one()
            version = (
                (await connection.execute(text(f"SELECT version_num FROM {self.schema}.alembic_version"))).scalar_one_or_none()
                if exists
                else None
            )
        if version is None or (version in known and version != self.head):
            raise SchemaNotCurrentError(self.schema, self.head)
        self._ready = True


PLATFORM_MIGRATIONS: Final = SchemaMigrations(schema="platform", scripts="ledger_platform.data:revisions")
"""The platform schema's revisions, run first by ``ledger-migrate``."""
```

(`scalar_one()` returns `Any` from a textual select: read it with `TypeAdapter(bool)` / `TypeAdapter(str | None)` `.validate_python(...)`, which the checkers accept, as elsewhere. Unused `Callable` import: drop it.)

`data/revisions/env.py`:

```python
"""Alembic's entry point for the platform schema."""

from ledger_platform.data.migrations import run_schema_migrations

run_schema_migrations()
```

`data/revisions/versions/r0001_platform.py`:

```python
"""Revision 0001 of the platform schema: the chassis's tables (its schedules enqueue jobs), then its own."""

lazy from collections.abc import Sequence

from alembic import op

from ledger_platform.data.chassis_schema import chassis_schema_v1, platform_schema_v1

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """``chassis_schema_v1`` in ``platform``, then ``routing`` and ``schedule_leases``."""
    chassis_schema_v1(op, "platform")
    platform_schema_v1(op)


def downgrade() -> None:
    """Revisions are expand-only (§6.5): nothing goes back past 0001 but a new database."""
```

(`op` is Alembic's module-level proxy for `Operations`; `chassis_schema_v1(op, ...)` takes `Operations`: if the checkers refuse the proxy's type, give the functions a `Protocol` parameter type with just `execute(sqltext: str) -> None` — `class Executes(Protocol)` in `chassis_schema.py` — which both the proxy and `Operations` satisfy.)

`packages/testing/src/ledger_testing/database.py`:

```python
"""Preparing test databases the way production's are: roles, schemas, migrations (§6.2, §14.2)."""

import secrets
from typing import Final
lazy from collections.abc import Sequence

import psycopg
from alembic.migration import MigrationContext
from alembic.operations import Operations
from psycopg import sql
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from ledger_platform.data.chassis_schema import chassis_schema_v1
from ledger_platform.data.roles import APP, MIGRATOR, Logins, SchemaRoles, cluster_statements, database_statements, relay_statements
lazy from ledger_platform.data.migrations import SchemaMigrations
lazy from ledger_testing.postgres import PostgresServer, Template, WorkerDatabase

TEST_LOGINS: Final = Logins(app="app-password", migrator="migrator-password", signer="signer-password")


def bootstrap(template: Template, schemas: Sequence[str]) -> None:
    """The cluster's roles and the template's schemas, as ``init.sql`` makes them (Task 19)."""
    roles = tuple(SchemaRoles(schema=schema) for schema in schemas)
    template.execute(cluster_statements(roles, TEST_LOGINS), database="postgres")
    template.execute(database_statements(roles))


def migrate(template: Template, *migrations: SchemaMigrations) -> None:
    """Each schema's revisions as ``ledger_migrator``, then the relay's grants, as ``ledger-migrate`` does."""
    url = template.url(role=MIGRATOR, password=TEST_LOGINS.migrator)
    for migration in migrations:
        migration.upgrade(url)
        template.execute(relay_statements(migration.schema))


def create_chassis(template: Template, schema: str) -> None:
    """``chassis_schema_v1`` in a test schema, run as its owner (for the platform's own tests)."""
    engine = create_engine(template.url(role=MIGRATOR, password=TEST_LOGINS.migrator), poolclass=NullPool)
    try:
        with engine.begin() as connection:
            connection.execute(text(f"SET LOCAL ROLE {SchemaRoles(schema=schema).owner}"))
            chassis_schema_v1(Operations(MigrationContext.configure(connection)), schema)
    finally:
        engine.dispose()
    template.execute(relay_statements(schema))


def fresh_database(server: PostgresServer, schemas: Sequence[str]) -> str:
    """A new, empty database with ``schemas`` (roles already made by the template): its name."""
    name = f"fresh_{secrets.token_hex(6)}"
    with psycopg.connect(server.libpq(), autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    with psycopg.connect(server.libpq(name), autocommit=True) as connection:
        for statement in database_statements(tuple(SchemaRoles(schema=schema) for schema in schemas)):
            connection.execute(sql.SQL(statement))
    return name


def app_url(worker: WorkerDatabase) -> str:
    """The worker's database as ``ledger_app`` (SQLAlchemy URL)."""
    return worker.url(role=APP, password=TEST_LOGINS.app)


def migrator_url(worker: WorkerDatabase) -> str:
    """The worker's database as ``ledger_migrator``."""
    return worker.url(role=MIGRATOR, password=TEST_LOGINS.migrator)


def libpq_as(worker: WorkerDatabase, *, role: str, password: str) -> str:
    """The worker's database as ``role`` (libpq URL, for plain psycopg connections in tests)."""
    server = worker.server
    return f"postgresql://{role}:{password}@{server.host}:{server.port}/{worker.name}"
```

(`template.execute(..., database="postgres")` needs `Template.execute` to accept another database: it does, by its `database` argument. `packages/testing/pyproject.toml` gains `"ledger-platform"`, `"alembic>=1.20.0"`, `"sqlalchemy>=2.1.4"` in its dependencies.)

- [ ] **Step 5: Run every test and the gate**

Run: `uv run pytest packages/platform packages/testing -n auto -q` → all pass. `uv run poe check` → green, with the platform revisions directory in coverage `source`.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(platform): roles, RLS builder, chassis_schema_v1 and per-schema migrations

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Data II — `BoundDatabase`, the v2 unit of work, notifier, audit, codec, queue primitives

Spec §5.3, §3.4 (rule 13 guard), §3.5 (publish, NOTIFY), §3.6 (enqueue, wake, wake_all), §6.4 (audit), §6.7; Decisions 6, 10. After Task 8. New modules; v1's `Database.transaction` and `UnitOfWork` stay in `data/database.py` for identity until Task 16.

**Amended by the Task 9 review (binding; the code in the steps below predates it, the implementation follows this):**
- **One clock, the database's.** A unit's `now` is its transaction's `now()` (read with the scope settings);
  `BoundDatabase` takes no `clock`. Every time a unit writes (`occurred_at`, `run_at`, `deadline_at`,
  `created_at`) and every due or lease comparison in SQL (`run_at <= now()`, `lease_until < now()`) is on that
  clock. `claim` takes no `now`: leases run from `now()` (`lease_until = now() + lease`), and callers' `due`
  predicates use `func.now()`. Injectable clocks stay only where the database is not involved.
- `publish` refuses an event outside the unit's scope (`EventScopeMismatchError`, `event_scope_mismatch`): same
  organization; the company and the mode too where the unit names them; an unscoped unit publishes for anyone.
  (Task 15 relaxed the mode: compared only where the event names one too. A mode-less event is about data both
  modes share, which a moded unit changes when an idempotent route acts on a company: Decision 4.)
- Job keys are unique per kind across tenants, so a key embeds a globally unique id. Enqueue restarts a done or
  dead job only when the existing row's scope columns are the enqueuer's (`IS NOT DISTINCT FROM`); `wake` and
  `wake_all` touch only jobs inside the unit's scope (organization, and company and mode where named).
- A unit or a purge opened while the task holds a unit raises `NestedUnitError` (`unit_nested`).
- `audit` needs an organization scope (`MissingAuditScopeError`, `audit_scope_missing`); `purging()` makes no
  tenant setting and the purge deletes without `RETURNING`.
- The codec refuses floats (amounts are `Decimal`) and text or keys with NUL; aware datetimes are written in UTC
  (equal instants, equal bytes); a mapping key appears in an error path only when it looks like a name.
- `Notifier`: one sender per channel with a dirty mark (no pile-up when the pool is busy); `aclose` gathers with
  `return_exceptions=True`. A dedicated small connection for it is Task 14's.
- The pool resets every returned connection (`RESET ROLE`, `RESET ALL`: `Database`'s `reset` hook).

**Files:**
- Create: `packages/platform/src/ledger_platform/data/{codec,audit,notifier,unit_of_work,bound,queue,scopes}.py`
- Modify: `packages/platform/src/ledger_platform/data/database.py` (`Database.create`, `Database.aclose`, `open` built on them; `_is_committable` → public `is_committable`), `packages/platform/src/ledger_platform/observability/tracing.py` (module function `current_traceparent() -> str | None`)
- Test: `packages/platform/tests/unit/{test_data_codec,test_data_audit,test_data_queue}.py`, `packages/platform/tests/integration/{test_data_bound,test_data_notifier}.py`

**Interfaces:**
- Consumes: `chassis_tables`, `SET_SCOPE`, `scope_settings`, `AbortedTransactionError`, `ClosedUnitOfWorkError` (v1 `data/database.py`), `Event` (kernel), `Scope`, `CompanyId`, `new_id`, `Clock`, `utc_now`.
- Produces:
  - `data.codec`: `type JsonValue = str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]`; `encode(value: object) -> JsonValue`; `payload_of(event: Event) -> dict[str, JsonValue]`; `canonical_json(value: JsonValue) -> bytes`; `jsonb_size(value: JsonValue) -> int`; `MAX_PAYLOAD = 65_536`; `UnencodableValueError(path, kind)` (internal, `unencodable_value`).
  - `data.audit`: `Actor(name: str, call_id: str)`; `acting_as(actor) -> ContextManager[None]`; `current_actor() -> Actor` (`MissingActorError`, internal, `missing_actor`).
  - `data.notifier`: `type Channel = Literal["ledger_outbox", "ledger_inbox"]`; `OUTBOX`, `INBOX`; `Signals` (Protocol: `signal(channel: Channel) -> None`); `Notifier(database, *, window: float = 0.01)` implementing it, with `async aclose()`.
  - `data.unit_of_work`: `JobPolicy(*, max_attempts: int, deadline: timedelta | None)`; `UnitOfWork` with `connection: AsyncConnection`, `scope: Scope | None`, `now: datetime`, `is_open: bool`, `publish(event) -> None`, `jobs: UnitJobs` (`enqueue(kind, key, *, run_at=None, payload=None) -> None`, `wake(kind, key) -> None`, `wake_all(kind, company: CompanyId, reason: str) -> None`), `audit(*, action, subject, before=None, after=None, detail=None) -> None`, `after_commit(callback) -> None`; `open_unit() -> UnitOfWork | None`; errors `EventTooLargeError` (`event_too_large`), `UndeclaredEventTypeError` (`event_type_undeclared`), `UndeclaredJobKindError` (`job_kind_undeclared`), all internal.
  - `data.bound`: `BoundDatabase(database, *, schema: str, source: str, publishes: frozenset[str] = frozenset(), jobs: Mapping[str, JobPolicy] = {}, signals: Signals)` (a unit's `now` is the database's) with `schema`, `database`, `tables -> ChassisTables`, `unit(scope: Scope | None) -> AsyncContextManager[UnitOfWork]`, `purging() -> AsyncContextManager[AsyncConnection]`.
  - `data.queue`: `Backoff(steps: tuple[timedelta, ...], then: timedelta)` with `after(attempts: int) -> timedelta`; `DEFAULT_BACKOFF` (1 s, 10 s, 1 min, 5 min, then 10 min); `async claim(connection, table, *, keys, due, order_by, limit, lease, extra=None) -> Sequence[RowMapping]` (leases from the database's `now()`).
  - `Database.create(url, *, pool_size=10, application_name="ledger", limits=DEFAULT_LIMITS) -> Database` (no I/O), `async Database.aclose()`.
  - `data.scopes.scope_of(organization: UUID | None, company: UUID | None, mode: str | None) -> Scope | None` (the scope a queue row's envelope columns name; Tasks 10 and 11 use it).

- [ ] **Step 1: Write the failing tests**

`packages/platform/tests/unit/test_data_codec.py`:

```python
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import ClassVar, final
from uuid import UUID

import pytest

from ledger_kernel.events import Event
from ledger_kernel.money import Currency, Money
from ledger_kernel.tax_ids import TaxId, TaxIdScheme
from ledger_platform.data.codec import UnencodableValueError, canonical_json, encode, jsonb_size, payload_of


class Colour(StrEnum):
    RED = "red"


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Priced(Event):
    TYPE: ClassVar[str] = "probe.item.priced"
    VERSION: ClassVar[int] = 1
    price: Money
    tax_id: TaxId
    tags: tuple[str, ...]


ID = UUID("01890a5d-ac96-774b-bcce-b302099a8057")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None), (True, True), (3, 3), ("x", "x"),
        (Decimal("1E+2"), "100"), (Decimal("0.10"), "0.10"),
        (ID, str(ID)), (date(2026, 11, 15), "2026-11-15"),
        (datetime(2026, 11, 15, 4, 0, tzinfo=UTC), "2026-11-15T04:00:00+00:00"),
        (Colour.RED, "red"), (Currency.of("DOP"), "DOP"),
        ((1, "a"), [1, "a"]), ({"k": Decimal(1)}, {"k": "1"}),
    ],
)
def test_values_have_one_json_form(value: object, expected: object) -> None:
    assert encode(value) == expected


def test_an_events_payload_is_its_own_fields() -> None:
    event = Priced(scope=None, subject=ID, aggregate_version=2, price=Money.of("12.50", Currency.of("DOP")),
                   tax_id=TaxId(TaxIdScheme("do.rnc"), "101850043"), tags=("a",))
    assert payload_of(event) == {
        "price": {"amount": "12.50", "currency": "DOP"},
        "tax_id": {"scheme": "do.rnc", "value": "101850043"},
        "tags": ["a"],
    }


@pytest.mark.parametrize(
    ("value", "where"),
    [(datetime(2026, 1, 1), "$"), ({"k": object()}, "$.k"), ({1: "x"}, "$"), ([1.5, float("nan")], "$[1]"), (b"x", "$")],
)
def test_anything_without_a_json_form_is_a_defect_named_by_its_path(value: object, where: str) -> None:
    with pytest.raises(UnencodableValueError, match=rf"^\{where} holds"):
        encode(value)


def test_canonical_json_is_compact_and_sorted() -> None:
    assert canonical_json({"b": 1, "a": [True, None]}) == b'{"a":[true,null],"b":1}'


def test_jsonb_size_counts_bytes_as_postgresql_prints_jsonb() -> None:
    assert jsonb_size({"a": "ñ", "b": 1}) == len('{"a": "ñ", "b": 1}'.encode())
```

`packages/platform/tests/unit/test_data_scopes.py`: `scope_of(org, company, "live")` is `Tenant(org, company, Mode.LIVE)`; `scope_of(org, None, None)` is `OrganizationScope(org, None)`; `scope_of(None, company, "test")` is `None`; `scope_of(org, company, "prod")` raises `InvalidValueError`.

`packages/platform/tests/unit/test_data_audit.py`: `current_actor()` raises `MissingActorError` outside `acting_as`; nested `acting_as` restores the outer actor on exit; an actor bound in a task is not seen by a sibling task started before it.

`packages/platform/tests/unit/test_data_queue.py`:

```python
from datetime import timedelta

import pytest

from ledger_platform.data.queue import DEFAULT_BACKOFF


@pytest.mark.parametrize(
    ("attempts", "delay"),
    [(1, timedelta(seconds=1)), (2, timedelta(seconds=10)), (3, timedelta(minutes=1)), (4, timedelta(minutes=5)), (5, timedelta(minutes=10)), (40, timedelta(minutes=10))],
)
def test_the_default_backoff_is_the_specs(attempts: int, delay: timedelta) -> None:
    assert DEFAULT_BACKOFF.after(attempts) == delay
```

`packages/platform/tests/integration/test_data_bound.py` — fixtures (in this module or `tests/conftest.py`):

```python
@pytest.fixture
async def database(worker_database: WorkerDatabase) -> AsyncGenerator[Database]:
    opened = Database.create(app_url(worker_database), pool_size=1)  # one connection: every unit reuses it
    try:
        yield opened
    finally:
        await opened.aclose()
        worker_database.truncate(("probe", "other", "platform"))


class RecordedSignals:
    def __init__(self) -> None:
        self.sent: list[str] = []

    def signal(self, channel: Channel) -> None:
        self.sent.append(channel)


@pytest.fixture
def signals() -> RecordedSignals:
    return RecordedSignals()


@pytest.fixture
def probe(database: Database, signals: RecordedSignals, clock: ManualClock) -> BoundDatabase:
    return BoundDatabase(
        database, schema="probe", source="probe", publishes=frozenset({Renamed.TYPE}),
        jobs={"probe.export": JobPolicy(max_attempts=5, deadline=timedelta(hours=1))},
        signals=signals, clock=clock,
    )
```

Tests (each a behaviour; write them in full):

- `test_a_unit_runs_in_its_modules_role_with_its_scope` — inside `probe.unit(Tenant(org, company, Mode.LIVE))`: `current_user` is `probe_rw`; the three settings read back as the scope's values.
- `test_one_pooled_connection_never_carries_a_scope_or_a_role_into_the_next_unit` (Review Focus 1) — with `pool_size=1`: a unit scoped to A in `test` inserts an `idempotency_keys` row; then a unit with `None` scope counts 0 rows; then a unit scoped to B counts 0; then a unit of A in `live` counts 0; then a unit of A in `test` counts 1; then a unit of `BoundDatabase(..., schema="other")` selecting `probe.idempotency_keys` fails with `permission denied for schema probe`; finally `SELECT current_user` on a raw pool connection is `ledger_app`.
- `test_a_rolled_back_unit_leaves_nothing_and_signals_nothing` — a unit that publishes, enqueues and audits, then raises: no row in `outbox`, `jobs`, `audit_events`; `signals.sent == []`; `open_unit()` is `None` after it.
- `test_publish_writes_the_envelope_with_the_units_time_and_signals_after_commit` — `Renamed(scope=Tenant(org, company, Mode.TEST), subject=s, aggregate_version=3, name="x")` published: one outbox row with `type`, `version`, `source="probe"`, `organization_id`, `company_id`, `mode="test"`, `subject`, `aggregate_version=3`, `occurred_at == clock()`, `payload == {"name": "x"}`, `attempts == 0`, `unrouted_since IS NULL`; `signals.sent == ["ledger_outbox"]` only after the block.
- `test_publish_carries_the_current_traceparent` — inside a span of an in-memory tracer provider, the row's `traceparent` is the span's.
- `test_an_undeclared_event_type_and_an_oversized_payload_are_refused_at_publish` — `UndeclaredEventTypeError` for a type not in `publishes`; `EventTooLargeError` for a 70,000-character `name` (raised by `publish`, before any SQL).
- `test_recording_after_the_unit_ended_is_refused` — keep the `uow` past the block: `publish`, `jobs.enqueue`, `audit` raise `ClosedUnitOfWorkError`.
- `test_enqueue_writes_a_ready_job_with_its_kinds_policy` — row: `status = 'ready'`, `run_at == clock()` (or the given `run_at`), `max_attempts == 5`, `deadline_at == clock() + 1h`, `payload`, scope columns, `created_at == updated_at == clock()`, `attempts == 0`.
- `test_enqueue_ignores_a_live_job_and_restarts_a_finished_one` — enqueue twice: one row, first payload kept; set it `done` (admin), enqueue with a new payload: `ready`, `attempts == 0`, new payload; same for `dead`; `running` and `parked` are left as they are.
- `test_an_undeclared_job_kind_is_refused` — `UndeclaredJobKindError`.
- `test_wake_and_wake_all_ready_only_the_parked_jobs_they_name` — three parked jobs (company A reason `certificate_missing`, company A reason `signer_unavailable`, company B reason `certificate_missing`): `wake_all("probe.export", A, "certificate_missing")` readies only the first; `wake("probe.export", key3)` readies the third; `run_at` of a woken job is the unit's `now`.
- `test_audit_writes_who_did_what_with_hashes_never_data` — within `acting_as(Actor("user:2001", "req-1"))`: the row's `actor`, `call_id`, `action="probe.rename"`, `subject_type="probe"`, `subject_id`, `before_hash == sha256(canonical_json(encode(old)))`, `after_hash` likewise, `detail == {"field": "name"}`; without an actor, `MissingActorError` at `audit`.
- `test_the_open_unit_is_known_to_the_task_and_forgotten_after` — `open_unit()` is the unit inside; `None` after; a task created inside and awaited after the block sees `None` (the unit is closed even though the context variable was copied).
- `test_a_caught_statement_failure_never_commits` — inside a unit, a failing statement caught without a savepoint: the block's exit raises `AbortedTransactionError` and nothing commits.
- `test_a_purging_connection_sees_expired_keys_of_every_organization_only` — Decision 6, through `probe.purging()` (role `probe_purge`): expired keys of A and B, never a live one; it cannot insert or update; a `probe.unit(None)` that sets `ledger.purge = 'on'` sees nothing.

`packages/platform/tests/integration/test_data_notifier.py`:

- `test_signals_within_the_window_become_one_notify_per_channel` — a LISTEN connection (`psycopg.AsyncConnection`, autocommit, `LISTEN ledger_outbox; LISTEN ledger_inbox`); `Notifier(database, window=0.05)`; `signal(OUTBOX)` three times and `signal(INBOX)` once; `await notifier.aclose()`; then a sentinel `NOTIFY ledger_outbox, 'end'` from the test's own connection; read `connection.notifies()` until the sentinel: exactly one empty `ledger_outbox` and one `ledger_inbox` before it (delivery follows commit order, so the sentinel bounds the read without any sleep).
- `test_a_failed_notify_is_logged_and_not_raised` — `Notifier` over a `Database` whose URL points at a closed port: `signal` then `aclose()` raise nothing, and one `notifier.notify_failed` log line names the channel (capture with structlog's `capture_logs`).

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest packages/platform/tests/unit/test_data_codec.py packages/platform/tests/integration/test_data_bound.py -q -p no:randomly`
Expected: `ModuleNotFoundError: No module named 'ledger_platform.data.codec'`.

- [ ] **Step 3: Implement**

`data/database.py`: split `open` into `create` (validation and engines, no I/O: SQLAlchemy connects on first use) and `aclose` (dispose the pool, then the probe engine, the second even when the first fails); `open` becomes `database = cls.create(...)`, `try: yield database`, `finally: await database.aclose()`. Rename `_is_committable` → `is_committable` (two users now).

`observability/tracing.py`: add

```python
def current_traceparent() -> str | None:
    """The W3C ``traceparent`` of the current span, or ``None`` outside a sampled one (rows store it, §11)."""
    carrier: dict[str, str] = {}
    _PROPAGATOR.inject(carrier)
    return carrier.get(_TRACEPARENT)
```

and make `Tracing.current_traceparent` call it.

`data/codec.py`:

```python
"""One JSON codec for event payloads, job payloads and audit hashes (§3.5).

``Decimal`` as a string, never a float; UUIDs as strings; aware dates and times as ISO 8601; enumerations as their
value; a ``Currency`` as its code; other dataclasses (``Money``, ``TaxId``) as objects of their fields; tuples and
lists as arrays; mappings with text keys as objects. Anything else is a defect of the code that built the value,
named by its path (``$.price.amount``), never shown.
"""

import json
import math
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import ClassVar, Final
from uuid import UUID

from ledger_kernel.errors import LedgerError
from ledger_kernel.events import Event
from ledger_kernel.money import Currency

type JsonValue = str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]
MAX_PAYLOAD: Final = 65_536
"""An event's payload, in bytes as PostgreSQL prints ``jsonb`` (the outbox's check)."""
_ENVELOPE: Final = frozenset({"scope", "subject", "aggregate_version"})


class UnencodableValueError(LedgerError):
    """A value with no JSON form reached the codec: a defect of the code that built it."""

    code: ClassVar[str] = "unencodable_value"

    def __init__(self, path: str, kind: str) -> None:
        super().__init__(f"{path} holds a {kind}, which has no JSON form")


def encode(value: object, path: str = "$") -> JsonValue:
    """``value``'s JSON form (see the module)."""
    match value:
        case None | bool() | str():
            return value
        case int():
            return value
        case float() if math.isfinite(value):
            return value
        case Decimal() if value.is_finite():
            return format(value, "f")
        case UUID():
            return str(value)
        case datetime() if value.tzinfo is not None:
            return value.isoformat()
        case datetime():
            raise UnencodableValueError(path, "naive datetime")
        case date():
            return value.isoformat()
        case Enum():
            return encode(value.value, path)
        case Currency():
            return value.code
        case tuple() | list():
            return [encode(item, f"{path}[{index}]") for index, item in enumerate(value)]
        case Mapping():
            encoded: dict[str, JsonValue] = {}
            for key, item in value.items():
                if not isinstance(key, str):
                    raise UnencodableValueError(path, "mapping with a key that is not text")
                encoded[key] = encode(item, f"{path}.{key}")
            return encoded
        case _ if is_dataclass(value) and not isinstance(value, type):
            return {field.name: encode(getattr(value, field.name), f"{path}.{field.name}") for field in fields(value)}
        case _:
            raise UnencodableValueError(path, type(value).__name__)


def payload_of(event: Event) -> dict[str, JsonValue]:
    """An event's payload: its own fields, not the envelope's."""
    return {
        field.name: encode(getattr(event, field.name), f"$.{field.name}")
        for field in fields(event)
        if field.name not in _ENVELOPE
    }


def canonical_json(value: JsonValue) -> bytes:
    """Compact, keys sorted, UTF-8: equal values give equal bytes (what audit hashes)."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def jsonb_size(value: JsonValue) -> int:
    """The size of ``value`` as PostgreSQL prints ``jsonb`` (``", "`` and ``": "`` separators), in bytes."""
    return len(json.dumps(value, ensure_ascii=False).encode())
```

(`float('nan')` falls to the last case and is reported as `float`; `Decimal('NaN')` likewise as `Decimal`. If a checker reports the `getattr` result as `Any`, keep it as the argument of `encode`, which takes `object`, as `ledger_testing.issuer` does with `getsockname`.)

`data/scopes.py`:

```python
"""The scope a queue row's envelope columns name (outbox, inbox and jobs rows carry them outside RLS)."""

from typing import Final
lazy from uuid import UUID

from ledger_kernel.errors import InvalidValueError
from ledger_kernel.ids import CompanyId, OrganizationId
from ledger_kernel.tenancy import Mode, OrganizationScope, Tenant
lazy from ledger_kernel.tenancy import Scope

_MODES: Final = frozenset(mode.value for mode in Mode)


def scope_of(organization: UUID | None, company: UUID | None, mode: str | None) -> Scope | None:
    """``Tenant`` with a company, ``OrganizationScope`` without one, ``None`` without an organization."""
    if mode is not None and mode not in _MODES:
        raise InvalidValueError(field="row.mode", requirement="test, live or null")
    parsed = None if mode is None else Mode(mode)
    if organization is None:
        return None
    if company is None:
        return OrganizationScope(OrganizationId(organization), parsed)
    return Tenant(OrganizationId(organization), CompanyId(company), parsed)
```

`data/audit.py`:

```python
"""Who acts: the actor and call id every audit row records (§6.4; Decision 10).

The runtime binds them where it makes a ``Call`` (from a request) or a ``SystemCall`` (a consumer, a job, the
receiver); ``uow.audit`` reads them. An audit without an actor is a defect: the row would not say who.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import ClassVar, Final, final
lazy from collections.abc import Generator

from ledger_kernel.errors import LedgerError


@final
@dataclass(frozen=True, slots=True)
class Actor:
    """``name``: a principal's subject, an API client's id, or ``system:<reason>``; ``call_id``: the request's."""

    name: str
    call_id: str


class MissingActorError(LedgerError):
    """``uow.audit`` was called where no call is bound: a defect of the code path."""

    code: ClassVar[str] = "missing_actor"

    def __init__(self) -> None:
        super().__init__("an audit event needs the acting call: bind it with acting_as")


_ACTOR: Final[ContextVar[Actor | None]] = ContextVar("ledger_actor", default=None)


@contextmanager
def acting_as(actor: Actor) -> Generator[None]:
    """``actor`` is who acts for the length of the block (restored after it)."""
    token = _ACTOR.set(actor)
    try:
        yield
    finally:
        _ACTOR.reset(token)


def current_actor() -> Actor:
    """The acting call; ``MissingActorError`` when none is bound."""
    actor = _ACTOR.get()
    if actor is None:
        raise MissingActorError
    return actor
```

`data/notifier.py`:

```python
"""After-commit NOTIFYs, coalesced: at most one per channel per window per process (§3.5).

A commit that wrote outbox rows signals ``ledger_outbox``; the fan-out signals ``ledger_inbox``. Workers LISTEN on
both (``ledger_platform.events.Wakeups``); the one-second poll is the safety net, so a NOTIFY that fails is
logged and dropped, never raised.
"""

import asyncio
from typing import Final, Literal, Protocol, final

import structlog
from sqlalchemy import text
lazy from ledger_platform.data.database import Database

type Channel = Literal["ledger_outbox", "ledger_inbox"]
OUTBOX: Final[Channel] = "ledger_outbox"
INBOX: Final[Channel] = "ledger_inbox"
_NOTIFY: Final = text("SELECT pg_notify(:channel, '')")
_logger = structlog.stdlib.get_logger(__name__)


class Signals(Protocol):
    """What a unit tells after it commits (tests record it; ``Notifier`` sends it)."""

    def signal(self, channel: Channel) -> None:
        """Say that ``channel`` has news, after a commit."""


@final
class Notifier(Signals):
    """Sends a NOTIFY per channel at most once per ``window`` seconds."""

    __slots__ = ("_database", "_pending", "_tasks", "_window")

    def __init__(self, database: Database, *, window: float = 0.01) -> None:
        self._database = database
        self._window = window
        self._pending: set[Channel] = set()
        self._tasks: set[asyncio.Task[None]] = set()

    def signal(self, channel: Channel) -> None:
        """Schedule one NOTIFY on ``channel`` unless one is already waiting to go."""
        if channel in self._pending:
            return
        self._pending.add(channel)
        task = asyncio.get_running_loop().create_task(self._send(channel))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _send(self, channel: Channel) -> None:
        try:
            await asyncio.sleep(self._window)  # the coalescing window, not a wait for anything to happen
            self._pending.discard(channel)
            async with self._database.engine.connect() as connection:
                await connection.execute(_NOTIFY, {"channel": channel})
                await connection.commit()
        except Exception:
            self._pending.discard(channel)
            _logger.exception("notifier.notify_failed", channel=channel)

    async def aclose(self) -> None:
        """Wait for the NOTIFYs already scheduled."""
        await asyncio.gather(*self._tasks, return_exceptions=True)
```

(`Signals` is a structural Protocol; `Notifier` subclasses it explicitly so the checkers verify the method; the class carries `@override` on `signal`.)

`data/queue.py`:

```python
"""The claim/lease/fencing/retry engine shared by inboxes and jobs (§3.5, §3.6).

A claim selects due rows ``FOR UPDATE SKIP LOCKED`` (two workers never claim one row), marks them ``running``
with a lease (``lease_until``) and a fresh fencing token (``lease_token``), and counts the attempt on the claim,
so a worker that crashes mid-run still used it. A row whose lease ran out is due again. Whoever records the
outcome names the token; a late writer whose lease was taken over records nothing.
"""

from dataclasses import dataclass
from datetime import timedelta
from typing import Final, final
lazy from collections.abc import Mapping, Sequence
lazy from datetime import datetime

from sqlalchemy import func, select, update
lazy from sqlalchemy import ColumnElement, RowMapping
lazy from sqlalchemy.ext.asyncio import AsyncConnection

lazy from ledger_platform.data.database import AnyTable


@final
@dataclass(frozen=True, slots=True)
class Backoff:
    """Waits after the 1st, 2nd, ... failed attempt, then ``then`` for every later one."""

    steps: tuple[timedelta, ...]
    then: timedelta

    def after(self, attempts: int) -> timedelta:
        """How long after the failure of attempt number ``attempts`` (counted from 1) the row is due again."""
        return self.steps[attempts - 1] if 1 <= attempts <= len(self.steps) else self.then


DEFAULT_BACKOFF: Final = Backoff(
    steps=(timedelta(seconds=1), timedelta(seconds=10), timedelta(minutes=1), timedelta(minutes=5)),
    then=timedelta(minutes=10),
)


async def claim(
    connection: AsyncConnection,
    table: AnyTable,
    *,
    keys: Sequence[str],
    due: ColumnElement[bool],
    order_by: Sequence[ColumnElement[object]],
    limit: int,
    now: datetime,
    lease: timedelta,
    extra: Mapping[str, object] | None = None,
) -> Sequence[RowMapping]:
    """Claim up to ``limit`` rows of ``table`` that are ``due`` (see the module); the claimed rows, as stored now."""
    chosen = (
        select(*(table.c[key] for key in keys))
        .where(due)
        .order_by(*order_by)
        .limit(limit)
        .with_for_update(skip_locked=True)
        .cte("chosen")
    )
    claimed = (
        update(table)
        .where(*(table.c[key] == chosen.c[key] for key in keys))
        .values(
            status="running",
            lease_until=now + lease,
            lease_token=func.gen_random_uuid(),
            attempts=table.c.attempts + 1,
            **(extra or {}),
        )
        .returning(*table.c)
    )
    return (await connection.execute(claimed)).mappings().all()
```

`data/unit_of_work.py`:

```python
"""The v2 unit of work (§5.3): one transaction, one module role, one tenant scope.

``BoundDatabase.unit(scope)`` opens it. Inside, the module changes its own rows through ``connection`` and records
what the transaction also decides: events (``publish``), jobs (``jobs``) and audit events (``audit``). Records are
validated at once and written when the block ends, before the commit, in the order they were made: the commit
makes them real with the module's changes or not at all, and the NOTIFYs go out after it. The unit marks itself
open on the task (``open_unit``) for the facade guard (§3.4, rule 13).
"""

import hashlib
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import timedelta
from typing import ClassVar, Final, final
from uuid import UUID
lazy from collections.abc import Callable, Mapping
lazy from datetime import datetime

from sqlalchemy import and_, insert, update
from sqlalchemy.dialects.postgresql import insert as upsert
lazy from sqlalchemy import Executable
lazy from sqlalchemy.ext.asyncio import AsyncConnection

from ledger_kernel.errors import LedgerError
from ledger_kernel.ids import new_id
from ledger_platform.data.audit import current_actor
from ledger_platform.data.codec import MAX_PAYLOAD, JsonValue, canonical_json, encode, jsonb_size, payload_of
from ledger_platform.data.database import ClosedUnitOfWorkError
from ledger_platform.observability.tracing import current_traceparent
lazy from ledger_kernel.events import Event
lazy from ledger_kernel.ids import CompanyId
lazy from ledger_kernel.tenancy import Scope
lazy from ledger_platform.data.tables import ChassisTables

type Callback = Callable[[], None]


class EventTooLargeError(LedgerError):
    """An event's payload exceeds 64 KiB (§3.5): a defect of the producer."""

    code: ClassVar[str] = "event_too_large"

    def __init__(self, event_type: str, size: int) -> None:
        super().__init__(f"a {event_type} payload is {size} bytes, above {MAX_PAYLOAD}")


class UndeclaredEventTypeError(LedgerError):
    """A module published an event type its manifest does not declare (rule 11)."""

    code: ClassVar[str] = "event_type_undeclared"

    def __init__(self, event_type: str, schema: str) -> None:
        super().__init__(f"{schema} publishes {event_type}, which its manifest does not declare")


class UndeclaredJobKindError(LedgerError):
    """A module enqueued a job kind its manifest does not declare."""

    code: ClassVar[str] = "job_kind_undeclared"

    def __init__(self, kind: str, schema: str) -> None:
        super().__init__(f"{schema} enqueues {kind}, which its manifest does not declare")


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class JobPolicy:
    """A job kind's limits, from the manifest."""

    max_attempts: int
    deadline: timedelta | None


_OPEN: Final[ContextVar[UnitOfWork | None]] = ContextVar("ledger_open_unit", default=None)


def open_unit() -> UnitOfWork | None:
    """The unit open on the current task, if any (a closed one inherited by a child task does not count)."""
    unit = _OPEN.get()
    return unit if unit is not None and unit.is_open else None


def _scope_columns(scope: Scope | None) -> dict[str, object]:
    if scope is None:
        return {"organization_id": None, "company_id": None, "mode": None}
    mode = scope.mode
    return {"organization_id": scope.organization, "company_id": scope.company, "mode": None if mode is None else mode.value}


def _digest(value: object) -> bytes | None:
    return None if value is None else hashlib.sha256(canonical_json(encode(value))).digest()


@final
class UnitJobs:
    """The unit's jobs: enqueue, wake one, wake all parked ones of a company and reason (§3.6)."""

    __slots__ = ("_unit",)

    def __init__(self, unit: UnitOfWork) -> None:
        self._unit = unit

    def enqueue(self, kind: str, key: str, *, run_at: datetime | None = None, payload: Mapping[str, object] | None = None) -> None:
        """A job of ``kind`` keyed ``key``: ignored while one is ready, running or parked; restarted when done or dead."""
        unit = self._unit
        policy = unit.policy_of(kind)
        jobs = unit.tables.jobs
        encoded = encode(dict(payload or {}))
        fresh = upsert(jobs).values(
            kind=kind, key=key, **_scope_columns(unit.scope), status="ready",
            run_at=run_at or unit.now, max_attempts=policy.max_attempts,
            deadline_at=None if policy.deadline is None else unit.now + policy.deadline,
            payload=encoded, traceparent=current_traceparent(), created_at=unit.now, updated_at=unit.now,
        )
        unit.record(
            fresh.on_conflict_do_update(
                index_elements=("kind", "key"),
                set_={
                    "status": "ready", "attempts": 0, "payload": fresh.excluded.payload, "run_at": fresh.excluded.run_at,
                    "deadline_at": fresh.excluded.deadline_at, "lease_until": None, "lease_token": None,
                    "parked_reason": None, "last_error_code": None, "updated_at": fresh.excluded.updated_at,
                },
                where=jobs.c.status.in_(("done", "dead")),
            ),
            jobs=True,
        )

    def wake(self, kind: str, key: str) -> None:
        """Make the parked job ``(kind, key)`` ready now."""
        unit = self._unit
        unit.policy_of(kind)
        jobs = unit.tables.jobs
        unit.record(
            update(jobs)
            .where(jobs.c.kind == kind, jobs.c.key == key, jobs.c.status == "parked")
            .values(status="ready", run_at=unit.now, parked_reason=None, updated_at=unit.now),
            jobs=True,
        )

    def wake_all(self, kind: str, company: CompanyId, reason: str) -> None:
        """Make every job of ``kind`` parked for ``company`` with ``reason`` ready now."""
        unit = self._unit
        unit.policy_of(kind)
        jobs = unit.tables.jobs
        unit.record(
            update(jobs)
            .where(and_(jobs.c.kind == kind, jobs.c.company_id == company, jobs.c.status == "parked", jobs.c.parked_reason == reason))
            .values(status="ready", run_at=unit.now, parked_reason=None, updated_at=unit.now),
            jobs=True,
        )


@final
class UnitOfWork:
    """One open transaction (see the module). Made by ``BoundDatabase.unit`` only."""

    __slots__ = ("_after_commit", "_jobs", "_open", "_pending", "_policies", "_publishes", "_published", "_source",
                 "connection", "jobs", "now", "scope", "tables")

    def __init__(
        self,
        connection: AsyncConnection,
        *,
        scope: Scope | None,
        now: datetime,
        tables: ChassisTables,
        source: str,
        publishes: frozenset[str],
        policies: Mapping[str, JobPolicy],
    ) -> None:
        self.connection = connection
        self.scope = scope
        self.now = now
        self.tables = tables
        self._source = source
        self._publishes = publishes
        self._policies = policies
        self._pending: list[Executable] = []
        self._after_commit: list[Callback] = []
        self._open = True
        self._published = False
        self.jobs = UnitJobs(self)

    @property
    def is_open(self) -> bool:
        """Whether the transaction is still open (records are accepted)."""
        return self._open

    @property
    def published(self) -> bool:
        """Whether the unit recorded an event (its commit signals ``ledger_outbox``)."""
        return self._published

    def record(self, statement: Executable, *, jobs: bool = False) -> None:
        """Keep ``statement`` to run before the commit (the chassis's own writers use it)."""
        del jobs
        if not self._open:
            raise ClosedUnitOfWorkError
        self._pending.append(statement)

    def policy_of(self, kind: str) -> JobPolicy:
        """``kind``'s policy; ``UndeclaredJobKindError`` if the manifest does not declare it."""
        policy = self._policies.get(kind)
        if policy is None:
            raise UndeclaredJobKindError(kind, self.tables.jobs.schema or "")
        return policy

    def publish(self, event: Event) -> None:
        """Write ``event`` to the module's outbox when the unit commits (rule 3)."""
        if event.TYPE not in self._publishes:
            raise UndeclaredEventTypeError(event.TYPE, self.tables.outbox.schema or "")
        payload = payload_of(event)
        size = jsonb_size(payload)
        if size > MAX_PAYLOAD:
            raise EventTooLargeError(event.TYPE, size)
        self.record(
            insert(self.tables.outbox).values(
                id=new_id(), type=event.TYPE, version=event.VERSION, source=self._source,
                **_scope_columns(event.scope), subject=event.subject, aggregate_version=event.aggregate_version,
                occurred_at=self.now, traceparent=current_traceparent(), payload=payload,
            )
        )
        self._published = True

    def audit(
        self,
        *,
        action: str,
        subject: UUID | str,
        before: object = None,
        after: object = None,
        detail: Mapping[str, str | int] | None = None,
    ) -> None:
        """Append who did ``action`` to ``subject``, with hashes of the canonical before and after (§6.4)."""
        actor = current_actor()
        self.record(
            insert(self.tables.audit_events).values(
                id=new_id(), occurred_at=self.now, **_scope_columns(self.scope),
                actor=actor.name, call_id=actor.call_id, action=action,
                subject_type=action.partition(".")[0], subject_id=str(subject),
                before_hash=_digest(before), after_hash=_digest(after), detail=dict(detail or {}),
            )
        )

    def after_commit(self, callback: Callback) -> None:
        """Run ``callback`` once the transaction has committed; never after a rollback."""
        if not self._open:
            raise ClosedUnitOfWorkError
        self._after_commit.append(callback)

    async def flush(self) -> None:
        """Write what was recorded, in order (``BoundDatabase.unit`` calls it before the commit)."""
        for statement in self._pending:
            await self.connection.execute(statement)
        self._pending.clear()

    def close(self) -> tuple[Callback, ...]:
        """Take no more records; the after-commit callbacks, for the caller to run if it committed."""
        self._open = False
        callbacks = tuple(self._after_commit)
        self._after_commit.clear()
        return callbacks

    def mark_open(self) -> object:
        """Make this the task's open unit; the token ``unmark`` takes."""
        return _OPEN.set(self)

    @staticmethod
    def unmark(token: object) -> None:
        """Undo ``mark_open``."""
        _OPEN.reset(token)
```

(`mark_open`/`unmark` keep `_OPEN` private to this module while `bound.py` sets it; type the token as `Token[UnitOfWork | None]` from `contextvars` instead of `object`. Drop the unused `jobs` keyword of `record` if the job writers need no distinction: it exists only if `BoundDatabase` signals on job writes, which this milestone does not.)

`data/bound.py`:

```python
"""A database bound to one module's schema and role: the only way a module opens units (§5.3, ADR 0004).

The runtime makes one per module from its manifest (schema, the event types it publishes, its job kinds); the
module never names a role or a schema. ``unit(scope)`` begins with ``SET LOCAL ROLE <schema>_rw`` and the scope's
settings, both transaction-local, so a pooled (or pgbouncer) connection carries nothing into the next unit.
"""

from contextlib import asynccontextmanager
from types import MappingProxyType
from typing import final
lazy from collections.abc import AsyncGenerator, Mapping

import structlog
from sqlalchemy import text
lazy from sqlalchemy.ext.asyncio import AsyncConnection

from ledger_kernel.clock import utc_now
from ledger_platform.data.database import SET_SCOPE, AbortedTransactionError, is_committable, scope_settings
from ledger_platform.data.notifier import OUTBOX
from ledger_platform.data.roles import SchemaRoles
from ledger_platform.data.tables import chassis_tables
from ledger_platform.data.unit_of_work import UnitOfWork
lazy from ledger_kernel.clock import Clock
lazy from ledger_kernel.tenancy import Scope
lazy from ledger_platform.data.database import Database
lazy from ledger_platform.data.notifier import Signals
lazy from ledger_platform.data.tables import ChassisTables
lazy from ledger_platform.data.unit_of_work import JobPolicy

_logger = structlog.stdlib.get_logger(__name__)


@final
class BoundDatabase:
    """One module's units (see the module)."""

    __slots__ = ("_clock", "_jobs", "_publishes", "_role", "_signals", "_source", "database", "schema")

    def __init__(
        self,
        database: Database,
        *,
        schema: str,
        source: str,
        signals: Signals,
        publishes: frozenset[str] = frozenset(),
        jobs: Mapping[str, JobPolicy] = MappingProxyType({}),
        clock: Clock = utc_now,
    ) -> None:
        roles = SchemaRoles(schema=schema)
        self.database = database
        self.schema = roles.schema
        self._role = text(f"SET LOCAL ROLE {identifier(roles.rw)}")
        self._purge = text(f"SET LOCAL ROLE {identifier(roles.purge)}")
        self._source = source
        self._signals = signals
        self._publishes = publishes
        self._jobs = jobs
        self._clock = clock

    @property
    def tables(self) -> ChassisTables:
        """The schema's chassis tables."""
        return chassis_tables(self.schema)

    @asynccontextmanager
    async def unit(self, scope: Scope | None) -> AsyncGenerator[UnitOfWork]:
        """A unit in this module's role and ``scope``: commits on normal exit, rolls back on an exception."""
        async with self.database.engine.begin() as connection:
            await connection.execute(self._role)
            await connection.execute(SET_SCOPE, scope_settings(scope))
            unit = UnitOfWork(
                connection, scope=scope, now=self._clock(), tables=self.tables, source=self._source,
                publishes=self._publishes, policies=self._jobs,
            )
            token = unit.mark_open()
            try:
                yield unit
                await unit.flush()
                if not await is_committable(connection):
                    raise AbortedTransactionError
            finally:
                callbacks = unit.close()
                UnitOfWork.unmark(token)
        if unit.published:
            self._signals.signal(OUTBOX)
        for callback in callbacks:
            try:
                callback()
            except Exception:
                _logger.exception("unit.after_commit_failed")

    @asynccontextmanager
    async def purging(self) -> AsyncGenerator[AsyncConnection]:
        """A transaction in this schema's ``purge`` role: it reads and deletes expired idempotency keys only."""
        async with self.database.engine.begin() as connection:
            await connection.execute(self._purge)
            yield connection
```

- [ ] **Step 4: Run the tests, then the gate**

Run: `uv run pytest packages/platform -n auto -q` → all pass. `uv run poe check` → green.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(platform): bound units with SET LOCAL ROLE, outbox/jobs/audit records, coalesced NOTIFY

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Events — subscriptions, routing, the fan-out, the inbox runner, wake-ups (∥ Tasks 11, 13)

Spec §3.5 (all of it), §2 rules 2-4, ADR 0002; Decisions 7, 16, 17, 18. After Task 9. Files only under `packages/platform/src/ledger_platform/events/` and its tests (the root `pyproject.toml` contract line is the one shared edit: add `events` to the layer `"outbox | idempotency | auth | grpc"`, which Task 11 extends with `jobs`).

**Amended by the Task 9 review:** the database's clock. Claims call `claim(..., order_by=..., limit=...,
lease=...)` without `now`; due predicates compare with `func.now()` (`next_attempt_at <= now()`,
`lease_until < now()`), and retries set `next_attempt_at = now() + backoff`. A runner's `now=` parameters below
are dropped; tests make rows due by moving their columns (as admin), not a clock. A bound unit's `now` is its
transaction's `now()`.

**Files:**
- Create: `packages/platform/src/ledger_platform/events/{__init__,consumers,subscriptions,routing,dispatcher,inbox,wakeups,bus}.py`
- Test: `packages/platform/tests/integration/{test_events_subscriptions,test_events_dispatcher,test_events_inbox,test_events_wakeups}.py`, `packages/platform/tests/unit/test_events_consumers.py`

**Interfaces:**
- Consumes: `BoundDatabase`, `UnitOfWork`, `Database`, `chassis_tables`, `platform_tables`, `claim`, `Backoff`, `DEFAULT_BACKOFF`, `Signals`, `INBOX`, `OUTBOX`, `Actor`, `acting_as`, `JsonValue`, `scope_of` (Task 9); `ConsumerDecl` (Task 5); `Tracing` (observability); `Event` (kernel).
- Produces (`ledger_platform.events.__all__`):
  - `Event` (re-export of the kernel's, Decision 1); `RawEvent(id, type, version, source, scope, subject, aggregate_version, occurred_at, traceparent, payload: Mapping[str, JsonValue])`.
  - `type Handler = Callable[[RawEvent, UnitOfWork], Awaitable[None]]`; `ConsumerSpec(name: str, handler: Handler)` (what a module's `ModuleParts.consumers` holds); `Consumer(name, types: frozenset[str], max_attempts: int, ordered: bool, handler)` with `Consumer.of(spec, decl: ConsumerDecl) -> Consumer` (what runners run; the runtime merges the two).
  - `sync_subscriptions(connection: Connection, schema: str, declared: Mapping[str, frozenset[str]], *, now: datetime) -> bool` (sync, run by migrate as the schema's owner); `bump_routing(connection: Connection) -> int` (the new version).
  - `Route(schema, consumer)`, `Routing(version, routes: Mapping[str, tuple[Route, ...]])`, `async load_routing(connection, schemas) -> Routing`.
  - `Dispatcher(database, *, schemas, signals, clock=utc_now, batch=100, registry=None)` with `async run_once() -> int`, `async run_until_idle() -> int`, `async run(wakeups: Wakeups) -> Never`.
  - `InboxRunner(*, database: BoundDatabase, consumer: Consumer, clock=utc_now, lease=timedelta(seconds=60), batch=20, backoff=DEFAULT_BACKOFF, tracing: Tracing | None = None, registry=None)` with `run_once`, `run_until_idle`, `run(wakeups)`.
  - `Wakeups(url: str)` with `async run() -> Never` (connects directly, LISTENs on `ledger_outbox` and `ledger_inbox`, reconnects through its supervisor), `subscribe(channel) -> asyncio.Event`, `async wait(event, *, poll: float = 1.0)`, `connected: bool`.
  - `EventBus` (Protocol: `publish(unit: UnitOfWork, event: Event) -> None`), `PostgresEventBus` (`unit.publish(event)`).
  - Metrics (prometheus, no tenant labels): `ledger_outbox_unrouted_total{schema}`, `ledger_inbox_handled_total{consumer,outcome}` (`done`, `retry`, `dead`, `lease_lost`).

- [ ] **Step 1: Write the failing tests**

Shared fixtures go in `packages/platform/tests/integration/conftest.py` (create it): `database` (pool of 4 as `ledger_app`), `migrator` (a sync SQLAlchemy engine as `ledger_migrator`), `signals` (the recording double of Task 9), `probe` and `other` (`BoundDatabase`s; `probe` publishes `Renamed`, `other` publishes nothing), and a helper `subscribe(migrator, schema, consumer, types)` that runs `sync_subscriptions` as `<schema>_owner` and `bump_routing` as `platform_owner` in one transaction each. Every test truncates `probe`, `other` and `platform` (but `routing`) after itself; `platform.routing` is reset to 0 by the fixture.

`test_events_subscriptions.py`:

```python
def test_subscriptions_are_upserted_and_undeclared_ones_deactivated(migrator: Engine, clock: ManualClock) -> None:
    with migrator.begin() as connection:
        connection.execute(text("SET LOCAL ROLE other_owner"))
        assert sync_subscriptions(connection, "other", {"other.renames": frozenset({"probe.thing.renamed"})}, now=clock())
        assert not sync_subscriptions(connection, "other", {"other.renames": frozenset({"probe.thing.renamed"})}, now=clock())
        assert sync_subscriptions(connection, "other", {}, now=clock())
        rows = connection.execute(text("SELECT consumer, event_type, active FROM other.subscriptions")).all()
    assert [tuple(row) for row in rows] == [("other.renames", "probe.thing.renamed", False)]


def test_bumping_the_routing_version_counts_up(migrator: Engine) -> None:
    with migrator.begin() as connection:
        connection.execute(text("SET LOCAL ROLE platform_owner"))
        assert (bump_routing(connection), bump_routing(connection)) == (1, 2)
```

`test_events_dispatcher.py` (each is a behaviour; write all in full):

- `test_an_event_reaches_every_subscribed_consumer_once_and_leaves_the_outbox` — subscribe `other.renames` and `probe.echo` (a consumer in the producer's own schema) to `probe.thing.renamed`; publish two events in one unit; `await dispatcher.run_until_idle()` returns 2; each inbox holds one row per event with every envelope column copied, `status = 'ready'`, `attempts = 0`, `next_attempt_at = clock()`; `probe.outbox` is empty; `signals.sent` ends with `"ledger_inbox"`.
- `test_an_event_nobody_subscribes_to_is_kept_and_counted` — no subscription: after `run_until_idle()` the row is still there with `unrouted_since == clock()`, `ledger_outbox_unrouted_total{schema="probe"} == 1`, and a second `run_until_idle()` returns 0 (no busy loop).
- `test_an_unrouted_event_fans_out_once_its_subscription_arrives` (Review Focus 3) — continue the previous state: `subscribe(...)` (bumps the version); `run_until_idle()` returns 1; the consumer's inbox has the event; the outbox is empty.
- `test_two_dispatchers_racing_deliver_each_event_once` (Review Focus 2) — 50 events; two `Dispatcher`s on two `Database` pools run `run_until_idle()` together (`asyncio.gather`); the inbox holds exactly 50 rows, the outbox none.
- `test_a_redelivered_outbox_row_never_duplicates_an_inbox_row` — after a fan-out, insert the same outbox row again (as admin, same `id`); `run_until_idle()` leaves one inbox row and an empty outbox.
- `test_the_relay_role_reaches_the_queue_tables_only` — on a `ledger_app` connection, in a transaction with `SET LOCAL ROLE ledger_relay`: `SELECT` from `probe.outbox`, `probe.subscriptions` and `platform.routing` work; `SELECT` from `probe.inbox` and `probe.audit_events` fail with `permission denied` (the relay may only insert into an inbox), and `INSERT INTO probe.jobs` fails likewise.

`test_events_inbox.py` (write all in full):

- `test_a_handler_runs_once_in_its_modules_role_and_scope_and_its_row_goes_with_its_writes` — the handler records `unit.scope`, `SELECT current_user`, and `unit.audit(action="other.rename_seen", subject=event.subject)`; after `run_until_idle()`: called once with a `RawEvent` whose `payload == {"name": "x"}` and `scope == Tenant(org, company, Mode.TEST)`; user `other_rw`; the audit row's actor is `system:consumer:other.renames` and its `call_id` the event id; the inbox row is gone.
- `test_a_failing_handler_is_retried_with_backoff` — the handler raises `UnavailableError` (a retryable kind): row `ready`, `attempts == 1`, `next_attempt_at == clock() + 1s`, `last_error_code == "unavailable"`; `run_until_idle()` again at the same clock does nothing (not due); `clock.advance(1s)`: retried, attempts 2, next delay 10 s.
- `test_an_invalid_event_is_dead_at_once_and_kept` — the handler raises an `InvalidInputError`: `status == 'dead'`, kept; `ledger_inbox_handled_total{outcome="dead"} == 1`.
- `test_after_max_attempts_the_row_is_dead` — `max_attempts=2`: two failures → dead.
- `test_a_handler_that_lost_its_lease_commits_nothing` (Review Focus 2) — runner A claims (`await runner_a.claim_now()`, a test seam that returns the claimed rows without handling them), the clock moves past the lease, runner B handles the row (deleted), then `await runner_a.handle(row)`: the handler's write (an audit row) is rolled back, `inbox_lease_lost` is logged, `ledger_inbox_handled_total{outcome="lease_lost"} == 1`, and the audit table holds only B's row.
- `test_an_ordered_consumer_handles_a_subjects_events_in_order` — `ordered=True`; events e1, e2 of subject S and e3 of subject T; the handler fails e1 once: after the first `run_once()`, e1 is `ready` (retry), e2 untouched (`attempts == 0`), e3 handled; after advancing the clock, `run_until_idle()` handles e1 then e2 (the handler's call log is `[e1, e3, e1, e2]`).
- `test_a_dead_row_does_not_block_its_subject` — Decision 18: e1 dead, e2 is then claimed.
- `test_a_runner_never_claims_a_type_it_has_no_handler_for` (Review Focus 3) — an inbox row of type `probe.thing.created` for `other.renames` (whose types are only `renamed`): `run_until_idle()` returns 0 and the row stays `ready`, attempts 0.

`test_events_wakeups.py`:

- `test_a_notify_wakes_its_subscribers_only` — `Wakeups(url)` started as a task (`asyncio.create_task(wakeups.run())`); `eventually(lambda: wakeups.connected)`; two subscribers on `ledger_inbox`, one on `ledger_outbox`; a `NOTIFY ledger_inbox` from the admin connection: both inbox events become set (`eventually`), the outbox one stays clear (asserted after a sentinel `NOTIFY ledger_outbox, 'x'` sets it, proving delivery order without sleeping: check the inbox ones were set first by recording the order in subscriber callbacks).
- `test_wait_returns_on_the_poll_when_nothing_arrives` — `await wakeups.wait(event, poll=0.05)` returns (the poll path; presence, not absence).
- `test_a_lost_connection_is_reported_and_raised_for_the_supervisor` — terminate the LISTEN backend (`pg_terminate_backend` as admin): `run()` raises, `connected` is false.

`packages/platform/tests/unit/test_events_consumers.py`: `Consumer.of(spec, decl)` takes types, `max_attempts`, `ordered` from the declaration and refuses a spec whose name differs from the declaration's (`ConsumerMismatchError`, internal, `consumer_mismatch`); `raw_event_of(row)` builds the `RawEvent` of an inbox row mapping (its scope through `scope_of`).

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest packages/platform/tests/unit/test_events_consumers.py packages/platform/tests/integration/test_events_dispatcher.py -q -p no:randomly`
Expected: `ModuleNotFoundError: No module named 'ledger_platform.events'`.

- [ ] **Step 3: Implement**

`events/consumers.py`:

```python
"""Consumers (§3.5): who handles which events, and the events as a handler receives them (Decision 16).

A module's assembly hands ``ConsumerSpec(name, handler)``; its manifest declares the same name with its event
types, attempts and ordering (``ConsumerDecl``); the runtime merges them into a ``Consumer`` the runner runs. A
handler receives a ``RawEvent`` (the envelope and the JSON payload) and the unit it must write in: its effects and
the deletion of its inbox row commit together.
"""

from dataclasses import dataclass
from typing import ClassVar, final
from uuid import UUID
lazy from collections.abc import Awaitable, Callable, Mapping
lazy from datetime import datetime

from pydantic import TypeAdapter

from ledger_kernel.errors import LedgerError
from ledger_platform.data.scopes import scope_of
lazy from sqlalchemy import RowMapping

lazy from ledger_kernel.tenancy import Scope
lazy from ledger_platform.data.codec import JsonValue
lazy from ledger_platform.data.unit_of_work import UnitOfWork
lazy from ledger_platform.runtime.manifest import ConsumerDecl

type Handler = Callable[[RawEvent, UnitOfWork], Awaitable[None]]


class ConsumerMismatchError(LedgerError):
    """An assembly handed a consumer its manifest does not declare: a defect of the module."""

    code: ClassVar[str] = "consumer_mismatch"

    def __init__(self, spec: str, declared: str) -> None:
        super().__init__(f"consumer {spec} is not the declared {declared}")


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class RawEvent:
    """An event as stored: its envelope and its payload (JSON)."""

    id: UUID
    type: str
    version: int
    source: str
    scope: Scope | None
    subject: UUID | None
    aggregate_version: int | None
    occurred_at: datetime
    traceparent: str | None
    payload: Mapping[str, JsonValue]


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class _StoredEnvelope:
    event_id: UUID
    type: str
    version: int
    source: str
    organization_id: UUID | None
    company_id: UUID | None
    mode: str | None
    subject: UUID | None
    aggregate_version: int | None
    occurred_at: datetime
    traceparent: str | None
    payload: dict[str, JsonValue]


_ENVELOPE: Final = TypeAdapter(_StoredEnvelope)


def raw_event_of(row: RowMapping) -> RawEvent:
    """The event an inbox row holds."""
    stored = _ENVELOPE.validate_python(dict(row))
    return RawEvent(
        id=stored.event_id, type=stored.type, version=stored.version, source=stored.source,
        scope=scope_of(stored.organization_id, stored.company_id, stored.mode),
        subject=stored.subject, aggregate_version=stored.aggregate_version, occurred_at=stored.occurred_at,
        traceparent=stored.traceparent, payload=stored.payload,
    )


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class ConsumerSpec:
    """What an assembly hands: a declared consumer's name and its handler."""

    name: str
    handler: Handler


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Consumer:
    """A consumer as the runner runs it: the declaration and the handler, merged."""

    name: str
    types: frozenset[str]
    max_attempts: int
    ordered: bool
    handler: Handler

    @classmethod
    def of(cls, spec: ConsumerSpec, decl: ConsumerDecl) -> Consumer:
        """Merge an assembly's spec with its manifest declaration."""
        if spec.name != decl.consumer:
            raise ConsumerMismatchError(spec.name, decl.consumer)
        return cls(name=spec.name, types=frozenset(decl.types), max_attempts=decl.max_attempts, ordered=decl.ordered, handler=spec.handler)
```

(Inbox rows store the event id as `event_id`; `_StoredEnvelope` names the columns the claim returns and ignores the queue columns: give it `model_config`-equivalent `extra="ignore"` by building the adapter with `TypeAdapter(_StoredEnvelope, config=ConfigDict(extra="ignore"))` — or select only the envelope columns in the claim's `RETURNING`. Add `Final` to the imports.)

`events/subscriptions.py`:

```python
"""Subscriptions in the database (§3.5, ADR 0002): written by ``ledger-migrate`` before a rollout.

Each consuming schema's ``subscriptions`` table lists its consumers' event types; migrate upserts the declared
rows, deactivates the rest (a rollback keeps the newer rows: an older runner simply claims only types it knows),
and bumps ``platform.routing.version`` so every fan-out reloads its routing table at its next pass.
"""

from sqlalchemy import Connection, func, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert
lazy from collections.abc import Mapping
lazy from datetime import datetime

from ledger_platform.data.tables import chassis_tables, platform_tables


def sync_subscriptions(connection: Connection, schema: str, declared: Mapping[str, frozenset[str]], *, now: datetime) -> bool:
    """Make ``schema``'s subscriptions what ``declared`` says; whether anything changed. Run as its owner."""
    table = chassis_tables(schema).subscriptions
    pairs = sorted((consumer, event_type) for consumer, types in declared.items() for event_type in types)
    changed = 0
    if pairs:
        fresh = insert(table).values([{"consumer": c, "event_type": t, "active": True, "updated_at": now} for c, t in pairs])
        changed += connection.execute(
            fresh.on_conflict_do_update(
                index_elements=("consumer", "event_type"),
                set_={"active": True, "updated_at": now},
                where=table.c.active.is_(False),
            )
        ).rowcount
    stale = update(table).where(table.c.active.is_(True)).values(active=False, updated_at=now)
    if pairs:
        stale = stale.where(tuple_(table.c.consumer, table.c.event_type).not_in(pairs))
    changed += connection.execute(stale).rowcount
    return changed > 0


def bump_routing(connection: Connection) -> int:
    """Move ``platform.routing.version`` on; the new version. Run as ``platform_owner``."""
    routing = platform_tables().routing
    return connection.execute(update(routing).values(version=routing.c.version + 1).returning(routing.c.version)).scalar_one()
```

(The first upsert writes new rows and re-activates inactive ones; an already active row is left alone, so `rowcount` counts real changes. Read `scalar_one()` through `TypeAdapter(int)`.)

`events/routing.py`:

```python
"""The routing table the fan-out uses: event type → the consumers (and their schemas) subscribed to it."""

from dataclasses import dataclass
from typing import Final, final
lazy from collections.abc import Mapping, Sequence

from pydantic import TypeAdapter
from sqlalchemy import select
lazy from sqlalchemy.ext.asyncio import AsyncConnection

from ledger_platform.data.tables import chassis_tables, platform_tables

_VERSION: Final = TypeAdapter(int)
_PAIRS: Final = TypeAdapter(list[tuple[str, str]])


@final
@dataclass(frozen=True, slots=True)
class Route:
    """A consumer, and the schema whose inbox it reads."""

    schema: str
    consumer: str


@final
@dataclass(frozen=True, slots=True)
class Routing:
    """The routing table at one version."""

    version: int
    routes: Mapping[str, tuple[Route, ...]]


async def routing_version(connection: AsyncConnection) -> int:
    """The current version (one indexed read; the fan-out asks once per pass)."""
    return _VERSION.validate_python((await connection.execute(select(platform_tables().routing.c.version))).scalar_one())


async def load_routing(connection: AsyncConnection, schemas: Sequence[str]) -> Routing:
    """Every active subscription of ``schemas``, with the version it was read at."""
    version = await routing_version(connection)
    routes: dict[str, list[Route]] = {}
    for schema in schemas:
        table = chassis_tables(schema).subscriptions
        rows = _PAIRS.validate_python(
            [tuple(row) for row in (await connection.execute(select(table.c.consumer, table.c.event_type).where(table.c.active))).all()]
        )
        for consumer, event_type in rows:
            routes.setdefault(event_type, []).append(Route(schema, consumer))
    return Routing(version, {event_type: tuple(found) for event_type, found in routes.items()})
```

`events/dispatcher.py`:

```python
"""The fan-out (§3.5): outbox rows of every schema into the inboxes of their consumers, as ``ledger_relay``.

One transaction per schema and pass: claim up to ``batch`` outbox rows (``FOR UPDATE SKIP LOCKED``, oldest first),
insert one inbox row per subscribed consumer (``ON CONFLICT DO NOTHING``: a repeat is absorbed), delete the
outbox rows; rows nobody subscribes to are kept and marked ``unrouted_since`` (counted, alerted). The routing
table is cached and reloaded when ``platform.routing.version`` moves, and then the unrouted rows of newly routed
types are fanned out. A commit that moved rows signals ``ledger_inbox``.
"""

from typing import Final, Never, final
lazy from collections.abc import Sequence

from prometheus_client import CollectorRegistry, Counter
from sqlalchemy import delete, select, text, update
from sqlalchemy.dialects.postgresql import insert

from ledger_kernel.clock import utc_now
from ledger_platform.data.notifier import INBOX, OUTBOX
from ledger_platform.data.tables import chassis_tables
from ledger_platform.events.routing import Routing, load_routing, routing_version
lazy from ledger_kernel.clock import Clock
lazy from sqlalchemy.ext.asyncio import AsyncConnection

lazy from ledger_platform.data.database import Database
lazy from ledger_platform.data.notifier import Signals
lazy from ledger_platform.events.wakeups import Wakeups

_RELAY: Final = text("SET LOCAL ROLE ledger_relay")
_COPIED: Final = (
    "type", "version", "source", "organization_id", "company_id", "mode",
    "subject", "aggregate_version", "occurred_at", "traceparent", "payload",
)
POLL: Final = 1.0
"""Seconds between passes when no NOTIFY arrives (§3.5's safety net)."""


@final
class Dispatcher:
    """See the module."""

    __slots__ = ("_batch", "_clock", "_database", "_routing", "_schemas", "_signals", "_unrouted")

    def __init__(
        self,
        database: Database,
        *,
        schemas: Sequence[str],
        signals: Signals,
        clock: Clock = utc_now,
        batch: int = 100,
        registry: CollectorRegistry | None = None,
    ) -> None:
        self._database = database
        self._schemas = tuple(schemas)
        self._signals = signals
        self._clock = clock
        self._batch = batch
        self._routing: Routing | None = None
        self._unrouted = Counter(
            "ledger_outbox_unrouted", "Events kept because no consumer subscribed to their type.", ["schema"],
            registry=registry,
        )

    async def _current(self, connection: AsyncConnection) -> Routing:
        cached = self._routing
        if cached is not None and cached.version == await routing_version(connection):
            return cached
        fresh = await load_routing(connection, self._schemas)
        if fresh.routes:
            for schema in self._schemas:
                outbox = chassis_tables(schema).outbox
                await connection.execute(
                    update(outbox)
                    .where(outbox.c.unrouted_since.is_not(None), outbox.c.type.in_(tuple(fresh.routes)))
                    .values(unrouted_since=None)
                )
        self._routing = fresh
        return fresh

    async def _pass(self, schema: str) -> tuple[int, bool]:
        outbox = chassis_tables(schema).outbox
        now = self._clock()
        async with self._database.engine.begin() as connection:
            await connection.execute(_RELAY)
            routing = await self._current(connection)
            rows = (
                await connection.execute(
                    select(outbox)
                    .where(outbox.c.unrouted_since.is_(None))
                    .order_by(outbox.c.id)
                    .limit(self._batch)
                    .with_for_update(skip_locked=True)
                )
            ).mappings().all()
            deliveries: dict[str, list[dict[str, object]]] = {}
            routed: list[object] = []
            unrouted: list[object] = []
            for row in rows:
                routes = routing.routes.get(str(row["type"]), ())
                if not routes:
                    unrouted.append(row["id"])
                    continue
                routed.append(row["id"])
                for route in routes:
                    deliveries.setdefault(route.schema, []).append(
                        {"consumer": route.consumer, "event_id": row["id"], "next_attempt_at": now}
                        | {column: row[column] for column in _COPIED}
                    )
            for target, values in deliveries.items():
                await connection.execute(insert(chassis_tables(target).inbox).values(values).on_conflict_do_nothing())
            if routed:
                await connection.execute(delete(outbox).where(outbox.c.id.in_(routed)))
            if unrouted:
                await connection.execute(update(outbox).where(outbox.c.id.in_(unrouted)).values(unrouted_since=now))
                self._unrouted.labels(schema=schema).inc(len(unrouted))
        return len(rows), bool(routed)

    async def run_once(self) -> int:
        """One pass over every schema; how many outbox rows it fanned out or marked."""
        handled, delivered = 0, False
        for schema in self._schemas:
            count, moved = await self._pass(schema)
            handled += count
            delivered |= moved
        if delivered:
            self._signals.signal(INBOX)
        return handled

    async def run_until_idle(self) -> int:
        """Pass until a pass finds nothing (tests drain with it: absence is proved by draining, §14.2)."""
        total = 0
        while handled := await self.run_once():
            total += handled
        return total

    async def run(self, wakeups: Wakeups) -> Never:
        """Forever: drain, then wait for a NOTIFY on ``ledger_outbox`` or the poll (the worker's supervised loop)."""
        woken = wakeups.subscribe(OUTBOX)
        while True:
            await self.run_until_idle()
            await wakeups.wait(woken, poll=POLL)
```

(Row values come back as `Any` from `mappings()`: validate the claimed rows with a `TypeAdapter(list[OutboxRow])` over a frozen dataclass of the outbox's columns, as `consumers.py` does, and build the inbox values from it, so nothing typed `Any` is touched; the sketch indexes the mapping only for brevity. Counter names drop `_total`: prometheus-client appends it.)

`events/inbox.py`:

```python
"""The inbox runner (§3.5): one consumer's rows, claimed with a lease and handled exactly once in effect.

A claim takes due rows of the consumer's own types (``ready`` and due, or ``running`` with an expired lease),
counts the attempt, and sets a fencing token. Each row is handled in its own unit, in the consumer's module role
and the event's scope: the handler's writes and the deletion of the row (``WHERE lease_token = :token``) commit
together; if the deletion finds nothing, another worker took the row over and this unit rolls back
(``inbox_lease_lost``). A failure is recorded in another unit: retried with backoff, or ``dead`` (kept) when the
error is ``INVALID`` or the attempts are spent. An ordered consumer claims only the oldest live row of each subject.
"""
```

Implementation outline (write it in full, with the module docstring above):

- `__init__` stores its arguments, builds `Counter("ledger_inbox_handled", ..., ["consumer", "outcome"])`.
- `_due(now)` returns the `ColumnElement[bool]`:

```python
inbox = self._database.tables.inbox
due = and_(
    inbox.c.consumer == self._consumer.name,
    inbox.c.type.in_(sorted(self._consumer.types)),
    or_(
        and_(inbox.c.status == "ready", inbox.c.next_attempt_at <= now),
        and_(inbox.c.status == "running", inbox.c.lease_until <= now),
    ),
)
if self._consumer.ordered:
    older = inbox.alias("older")
    due = and_(
        due,
        ~exists().where(
            older.c.consumer == inbox.c.consumer,
            older.c.subject == inbox.c.subject,
            older.c.event_id < inbox.c.event_id,
            older.c.status != "dead",
        ),
    )
return due
```

- `claim_now()` (public test seam, used by `run_once`): `async with self._database.unit(None) as unit:` `return await claim(unit.connection, inbox, keys=("consumer", "event_id"), due=self._due(now), order_by=(inbox.c.next_attempt_at, inbox.c.event_id), limit=self._batch, now=now, lease=self._lease)` — rows validated into a frozen `_Claimed` dataclass (`event_id`, `lease_token`, `attempts`, the envelope).
- `handle(row)`: `event = raw_event_of(row)`; `with self._span(event), acting_as(Actor(f"system:consumer:{name}", str(event.id))):` then

```python
try:
    async with self._database.unit(event.scope) as unit:
        await self._consumer.handler(event, unit)
        deleted = await unit.connection.execute(
            delete(inbox)
            .where(inbox.c.consumer == name, inbox.c.event_id == event.id, inbox.c.lease_token == row.lease_token)
            .returning(inbox.c.event_id)
        )
        if deleted.first() is None:
            raise _LeaseLostError
except _LeaseLostError:
    _logger.warning("inbox_lease_lost", consumer=name, event_id=str(event.id))
    self._handled.labels(consumer=name, outcome="lease_lost").inc()
except Exception as error:
    await self._failed(row, error)
else:
    self._handled.labels(consumer=name, outcome="done").inc()
```

  (`_span(event)` is `tracing.consuming(name, event.traceparent)` when a `Tracing` was given, else `nullcontext()`.)
- `_failed(row, error)`: `dead = (isinstance(error, LedgerError) and error.kind is ErrorKind.INVALID) or row.attempts >= self._consumer.max_attempts`; `code = error.code if isinstance(error, LedgerError) else "internal"`; in `self._database.unit(None)`: `update(inbox).where(<consumer, event_id, lease_token == row.lease_token>).values(status="dead" if dead else "ready", next_attempt_at=<now + backoff.after(row.attempts)> unless dead, lease_until=None, lease_token=None, last_error_code=code)`; zero rows → `inbox_lease_lost`; log `inbox.handler_failed` with `consumer`, `event_type`, `attempts`, `dead`, `error_type(error)` (never the message: §10); count `dead` or `retry`.
- `run_once()`: `rows = await self.claim_now()`; handle each in order; `return len(rows)`. `run_until_idle()` and `run(wakeups)` (subscribe to `INBOX`, drain, wait, forever) as the dispatcher's.

`events/wakeups.py`:

```python
"""Wake-ups (§3.5, §6.7): one LISTEN connection per process, opened directly to PostgreSQL.

``run()`` connects, LISTENs on ``ledger_outbox`` and ``ledger_inbox`` and sets every subscriber's event when a
NOTIFY arrives; when the connection is lost it raises, and the worker's supervisor starts it again. Runners never
depend on it: they wait for their event *or* the poll, whichever comes first.
"""

import asyncio
from typing import Final, Never, final

import psycopg

from ledger_platform.data.notifier import INBOX, OUTBOX
lazy from ledger_platform.data.notifier import Channel


class _ListenerStoppedError(ConnectionError):
    def __init__(self) -> None:
        super().__init__("the LISTEN connection stopped delivering notifications")


@final
class Wakeups:
    """See the module."""

    __slots__ = ("_connected", "_subscribers", "_url")

    def __init__(self, url: str) -> None:
        self._url = url
        self._subscribers: dict[str, list[asyncio.Event]] = {OUTBOX: [], INBOX: []}
        self._connected = False

    @property
    def connected(self) -> bool:
        """Whether the LISTEN connection is up (the worker's readiness)."""
        return self._connected

    def subscribe(self, channel: Channel) -> asyncio.Event:
        """An event set whenever ``channel`` is notified."""
        event = asyncio.Event()
        self._subscribers[channel].append(event)
        return event

    async def wait(self, event: asyncio.Event, *, poll: float = 1.0) -> None:
        """Return when ``event`` is set or after ``poll`` seconds; ``event`` is cleared."""
        try:
            async with asyncio.timeout(poll):
                await event.wait()
        except TimeoutError:
            pass
        finally:
            event.clear()

    async def run(self) -> Never:
        """Listen until the connection fails, then raise (see the module)."""
        try:
            async with await psycopg.AsyncConnection.connect(self._url, autocommit=True) as connection:
                await connection.execute("LISTEN ledger_outbox")
                await connection.execute("LISTEN ledger_inbox")
                self._connected = True
                async for notification in connection.notifies():
                    for event in self._subscribers.get(notification.channel, ()):
                        event.set()
        finally:
            self._connected = False
        raise _ListenerStoppedError
```

(`asyncio.timeout` with a `TimeoutError` caught is the poll; `contextlib.suppress(TimeoutError)` reads better: use it.)

`events/bus.py`:

```python
"""The ``EventBus`` port (§3.5, §3.9): today the outbox; an extracted module gets a broker adapter behind it."""

from typing import Protocol, final, override
lazy from ledger_kernel.events import Event
lazy from ledger_platform.data.unit_of_work import UnitOfWork


class EventBus(Protocol):
    """Publishes an event as part of a unit's transaction."""

    def publish(self, unit: UnitOfWork, event: Event) -> None:
        """Make ``event`` part of ``unit``: it is announced only if the unit commits."""


@final
class PostgresEventBus(EventBus):
    """The outbox in the unit's own schema."""

    @override
    def publish(self, unit: UnitOfWork, event: Event) -> None:
        unit.publish(event)
```

`events/__init__.py`: docstring and `__all__` with `Event`, `RawEvent`, `Handler`, `ConsumerSpec`, `Consumer`, `EventBus`, `PostgresEventBus`, `Dispatcher`, `InboxRunner`, `Wakeups`, `Route`, `Routing`, `load_routing`, `sync_subscriptions`, `bump_routing` (re-exported from their modules; `Event` from `ledger_kernel.events`).

- [ ] **Step 4: Run the tests, then the gate**

Run: `uv run pytest packages/platform -n auto -q` → all pass. `uv run poe check` → green.

- [ ] **Step 5: Commit**

```bash
git add -A packages/platform pyproject.toml
git commit -m "feat(platform): PostgreSQL eventing - subscriptions, fan-out, inbox runner, LISTEN wake-ups

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Jobs — the runner with leases and fencing, schedules on lease rows, the platform's purges (∥ Tasks 10, 13)

Spec §3.6 (all of it), §6.4 (purges), §2 rule 4, ADR 0003; Decisions 8, 9, 15. After Task 9. Files only under `packages/platform/src/ledger_platform/jobs/` and its tests (the shared root edit: add `jobs` to the layer that Task 10 gave `events`).

**Amended by the Task 9 review:** the database's clock. `claim` takes no `now`; job due predicates are
`run_at <= now()` and `lease_until < now()`, outcomes write `now()`-based times (`RunAgain`, backoff,
`deadline_at < now()`), and schedule leases compare `next_run_at` and `leased_until` with `now()`. The
`now=` parameters of `run_due`, `claim_now` and `handle` below, and the scheduler's and `maintenance_jobs`'
`clock`, are dropped; tests make rows due by moving their columns (as admin), not a `ManualClock`.

**Files:**
- Create: `packages/platform/src/ledger_platform/jobs/{__init__,outcomes,runner,schedules,maintenance}.py`
- Test: `packages/platform/tests/integration/{test_jobs_runner,test_jobs_schedules,test_jobs_maintenance}.py`, `packages/platform/tests/unit/test_jobs_outcomes.py`

**Interfaces:**
- Consumes: `BoundDatabase`, `UnitOfWork`, `JobPolicy`, `claim`, `Backoff`, `DEFAULT_BACKOFF`, `JsonValue`, `Actor`, `acting_as`, `scope_of` (`ledger_platform.data.scopes`), `platform_tables` (Tasks 8-9); `JobDecl`, `ScheduleDecl` (Task 5).
- Produces (`ledger_platform.jobs.__all__`):
  - `Job(kind, key, scope, payload: Mapping[str, JsonValue], attempts, max_attempts, deadline_at, lease_token, traceparent)`.
  - `Done()`, `RunAgain(at: datetime)`, `Park(reason: str)`, `type JobOutcome = Done | RunAgain | Park`; `type JobHandler = Callable[[Job], Awaitable[JobOutcome]]`; `JobSpec(kind: str, handler: JobHandler)`.
  - `JobRunner(*, database: BoundDatabase, kinds: Mapping[str, tuple[JobDecl, JobHandler]], clock=utc_now, batch=10, backoff=DEFAULT_BACKOFF, tracing=None, registry=None)` with `async claim_now(*, now) -> Sequence[Job]`, `async handle(job, *, now) -> None`, `async run_due(*, now: datetime | None = None) -> int`, `async run() -> Never` (per-kind concurrency from the manifest).
  - `Schedule(name, every: timedelta, enqueues: str, target: BoundDatabase)`; `Scheduler(*, platform: BoundDatabase, schedules, holder: str, clock=utc_now, lease=timedelta(seconds=30))` with `async run_due(*, now=None) -> int`, `async run() -> Never`.
  - `PURGE_IDEMPOTENCY_KEYS = "platform.purge_idempotency_keys"`, `PURGE_DONE_JOBS = "platform.purge_done_jobs"`, `DONE_JOBS_KEPT = timedelta(days=7)`; `maintenance_jobs(databases: Sequence[BoundDatabase], *, clock=utc_now) -> tuple[JobSpec, JobSpec]`.
  - Metrics: `ledger_job_lease_lost_total{kind}`, `ledger_jobs_finished_total{kind,outcome}` (`done`, `run_again`, `parked`, `retry`, `dead`).

- [ ] **Step 1: Write the failing tests**

`packages/platform/tests/unit/test_jobs_outcomes.py`: the three outcomes are frozen; `Park("")` is a defect (`InvalidValueError`: a parked job says why); `RunAgain` with a naive datetime is a defect.

`packages/platform/tests/integration/test_jobs_runner.py` — the `probe` bound database declares `probe.export` (`JobPolicy(max_attempts=3, deadline=timedelta(hours=1))`) and `probe.poll`; `JobDecl`s with `lease="60s"`; a scripted handler whose answers per call the test sets. Write every test in full:

- `test_a_due_job_runs_once_and_is_done` — enqueue at `clock()`; `await runner.run_due(now=clock())` returns 1; the handler got a `Job` with the payload and scope; the row is `done`, lease cleared; `run_due` again returns 0.
- `test_a_job_before_its_time_waits` — `run_at = clock() + 5min`: `run_due(now=clock())` returns 0; at `clock() + 5min` it runs.
- `test_run_again_reschedules_without_counting_a_failure` — the handler returns `RunAgain(clock() + 30s)`: `ready`, `run_at` that, `attempts == 0`; it runs again at that time.
- `test_park_waits_for_a_wake` — `Park("certificate_missing")`: `parked`, reason kept; `run_due` far later returns 0; `unit.jobs.wake_all("probe.export", company, "certificate_missing")` in a unit; it runs at the next `run_due`.
- `test_a_retryable_error_backs_off_and_counts_on_claim` — the handler raises `UnavailableError`: `ready` at `now + 1s`, `attempts == 1`, `last_error_code == "unavailable"`; a second failure: `now + 10s`, attempts 2.
- `test_invalid_and_conflict_errors_and_spent_attempts_are_dead` — `InvalidInputError` → dead at once; `ConflictError` → dead at once; three `UnavailableError`s with `max_attempts = 3` → dead; dead rows are kept.
- `test_a_job_past_its_deadline_is_dead` — the handler keeps answering `RunAgain(...)` past `deadline_at`: the record that would cross it marks the job `dead` with `last_error_code == "deadline_passed"`.
- `test_a_worker_that_crashed_mid_run_leaves_the_job_to_be_claimed_again` — `claim_now(now=clock())` and never handle (the crash); `run_due(now=clock())` returns 0 (the lease holds); at `clock() + 61s` it runs; the row's `attempts == 2` (counted on each claim).
- `test_a_zombie_records_nothing_after_its_lease_was_taken_over` (Review Focus 2) — runner A claims; the clock passes the lease; runner B claims (new token) and records `Done`; A's handler then returns `Park("x")`: A's `handle` changes nothing (`done` stays), `job_lease_lost` is logged with the kind, and `ledger_job_lease_lost_total{kind="probe.export"} == 1`.
- `test_two_runners_never_run_one_job_twice` — 20 jobs; two runners on two pools `run_due` concurrently; the handler's call count per key is exactly 1.
- `test_a_handler_acts_as_its_job` — inside the handler, a unit's `audit(...)` records actor `system:job:probe.export` and call id `probe.export:<key>`.

`test_jobs_schedules.py` (a `platform` bound database; schedules `probe.nightly` every 24 h enqueueing `probe.export` into `probe`):

- `test_a_schedule_enqueues_one_job_per_slot` — `run_due(now=clock())` returns 1 and enqueues key `probe.nightly@<clock().isoformat()>`; again at the same time: 0; at `clock() + 24h`: 1 (the next slot's key).
- `test_missed_slots_are_not_replayed` — first run at `clock()`, next at `clock() + 72h`: one job (the slot `clock() + 72h` falls on a boundary: the lease row's `next_run_at` moves to the first slot after now).
- `test_two_schedulers_fire_a_slot_once` — two `Scheduler`s (holders `a`, `b`) `run_due` concurrently: one job.
- `test_a_lease_held_by_a_crashed_scheduler_expires` — set the lease row `holder = 'dead'`, `leased_until = clock() + 30s` (as admin): `run_due(now=clock())` returns 0; at `clock() + 31s` it fires.

`test_jobs_maintenance.py`:

- `test_expired_idempotency_keys_of_every_organization_are_purged` — keys of two organizations, two expired and one live (inserted as admin, `expires_at` relative to the database's `now()`): running the purge handler deletes the expired two in every bound schema and keeps the live one.
- `test_done_jobs_are_purged_after_seven_days_and_dead_ones_kept` — `done` updated 8 days ago (purged), `done` 6 days ago (kept), `dead` 30 days ago (kept).

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest packages/platform/tests/integration/test_jobs_runner.py -q -p no:randomly`
Expected: `ModuleNotFoundError: No module named 'ledger_platform.jobs'`.

- [ ] **Step 3: Implement**

`jobs/outcomes.py`:

```python
"""What a job handler answers (§3.6), and the job as it receives it."""

from dataclasses import dataclass
from typing import final
from uuid import UUID
lazy from collections.abc import Awaitable, Callable, Mapping
lazy from datetime import datetime

from ledger_kernel.errors import InvalidValueError
lazy from ledger_kernel.tenancy import Scope
lazy from ledger_platform.data.codec import JsonValue


@final
@dataclass(frozen=True, slots=True)
class Done:
    """The work is finished; the row is purged after ``DONE_JOBS_KEPT``."""


@final
@dataclass(frozen=True, slots=True)
class RunAgain:
    """Poll again at ``at``: not a failure, attempts start over."""

    at: datetime

    def __post_init__(self) -> None:
        if self.at.tzinfo is None:
            raise InvalidValueError(field="run_again.at", requirement="an aware datetime")


@final
@dataclass(frozen=True, slots=True)
class Park:
    """Wait for an outside fact; ``uow.jobs.wake`` or ``wake_all`` with ``reason`` makes the job ready."""

    reason: str

    def __post_init__(self) -> None:
        if not self.reason:
            raise InvalidValueError(field="park.reason", requirement="a reason a wake can name")


type JobOutcome = Done | RunAgain | Park


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Job:
    """A claimed job, as its handler receives it."""

    kind: str
    key: str
    scope: Scope | None
    payload: Mapping[str, JsonValue]
    attempts: int
    max_attempts: int
    deadline_at: datetime | None
    lease_token: UUID
    traceparent: str | None


type JobHandler = Callable[[Job], Awaitable[JobOutcome]]
"""Runs outside any unit: it reads in one, calls out, records in another (no transaction spans a network call)."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class JobSpec:
    """What an assembly hands: a declared job kind and its handler."""

    kind: str
    handler: JobHandler
```

`jobs/runner.py` — write in full from this outline (module docstring: §3.6's claim/record/outcomes rules in prose):

```python
@final
class JobRunner:
    __slots__ = ("_backoff", "_batch", "_clock", "_database", "_finished", "_kinds", "_lease_lost", "_tracing")

    def __init__(self, *, database, kinds, clock=utc_now, batch=10, backoff=DEFAULT_BACKOFF, tracing=None, registry=None) -> None:
        ...  # counters ledger_job_lease_lost{kind}, ledger_jobs_finished{kind,outcome}

    async def claim_now(self, *, now: datetime) -> Sequence[Job]:
        jobs = self._database.tables.jobs
        claimed: list[Job] = []
        async with self._database.unit(None) as unit:
            for kind, (decl, _) in self._kinds.items():
                due = and_(
                    jobs.c.kind == kind,
                    or_(and_(jobs.c.status == "ready", jobs.c.run_at <= now),
                        and_(jobs.c.status == "running", jobs.c.lease_until <= now)),
                )
                rows = await claim(unit.connection, jobs, keys=("kind", "key"), due=due, order_by=(jobs.c.run_at,),
                                   limit=self._batch, now=now, lease=decl.lease, extra={"updated_at": now})
                claimed += [_job_of(row) for row in rows]
        return claimed

    async def handle(self, job: Job, *, now: datetime) -> None:
        _, handler = self._kinds[job.kind]
        with self._span(job), acting_as(Actor(f"system:job:{job.kind}", f"{job.kind}:{job.key}")):
            try:
                outcome = await handler(job)
            except Exception as error:
                await self._record(job, self._failure(job, error, now), now=now, outcome="dead" if ... else "retry")
                return
        await self._record(job, self._values_of(job, outcome, now), now=now, outcome=...)

    def _values_of(self, job, outcome, now) -> dict[str, object]:
        match outcome:
            case Done():
                return {"status": "done"}
            case RunAgain(at=at) if job.deadline_at is not None and at > job.deadline_at:
                return {"status": "dead", "last_error_code": "deadline_passed"}
            case RunAgain(at=at):
                return {"status": "ready", "run_at": at, "attempts": 0}
            case Park(reason=reason):
                return {"status": "parked", "parked_reason": reason}

    def _failure(self, job, error, now) -> dict[str, object]:
        code = error.code if isinstance(error, LedgerError) else "internal"
        final_kind = isinstance(error, LedgerError) and error.kind in {ErrorKind.INVALID, ErrorKind.CONFLICT}
        past_deadline = job.deadline_at is not None and now >= job.deadline_at
        if final_kind or job.attempts >= job.max_attempts or past_deadline:
            return {"status": "dead", "last_error_code": code}
        return {"status": "ready", "run_at": now + self._backoff.after(job.attempts), "last_error_code": code}

    async def _record(self, job, values, *, now, outcome) -> None:
        jobs = self._database.tables.jobs
        async with self._database.unit(None) as unit:
            done = await unit.connection.execute(
                update(jobs)
                .where(jobs.c.kind == job.kind, jobs.c.key == job.key, jobs.c.lease_token == job.lease_token)
                .values(lease_until=None, lease_token=None, updated_at=now, **values)
                .returning(jobs.c.key)
            )
            recorded = done.first() is not None
        if recorded:
            self._finished.labels(kind=job.kind, outcome=outcome).inc()
        else:
            _logger.warning("job_lease_lost", kind=job.kind)
            self._lease_lost.labels(kind=job.kind).inc()

    async def run_due(self, *, now: datetime | None = None) -> int:
        moment = self._clock() if now is None else now
        total = 0
        while jobs := await self.claim_now(now=moment):
            for job in jobs:
                await self.handle(job, now=moment)
            total += len(jobs)
        return total

    async def run(self) -> Never:
        ...  # per kind: asyncio.Semaphore(decl.concurrency); every POLL second claim at most the free slots of
        #      each kind and start a task per job inside one TaskGroup; never more than `concurrency` running per kind
```

(`outcome` labels: `done`, `run_again`, `parked`, `retry`, `dead` — compute the label from the values' `status` and the outcome type rather than passing `...`. `_job_of(row)` validates the claimed `RowMapping` through a `TypeAdapter` over a frozen dataclass of the job columns and builds `Job`, using `scope_of` for the scope. A `run_due` loop terminates because claimed jobs are no longer due at `moment` unless a handler answers `RunAgain(at <= moment)`: guard by stopping when every claimed job of a pass was rescheduled to `<= moment` — or simpler, claim at most once per kind per `run_due` call and let callers loop; write it the simple way and make the tests call `run_due` as often as they need.)

`jobs/schedules.py` — write in full:

- `Schedule` (frozen dataclass: `name`, `every`, `enqueues`, `target: BoundDatabase`).
- `Scheduler.run_due(now)`:
  1. one `platform` unit: `INSERT INTO platform.schedule_leases (name, next_run_at) VALUES ... ON CONFLICT (name) DO NOTHING` for every schedule (first sight: due now);
  2. per schedule, one `platform` unit: `SELECT next_run_at FROM platform.schedule_leases WHERE name = :name AND next_run_at <= :now AND (leased_until IS NULL OR leased_until <= :now) FOR UPDATE SKIP LOCKED`; none → skip; else `UPDATE ... SET holder = :me, leased_until = :now + lease` and remember the slot;
  3. a unit of `schedule.target` (scope `None`, `acting_as(Actor(f"system:schedule:{name}", f"{name}@{slot}"))`): `unit.jobs.enqueue(schedule.enqueues, key=f"{name}@{slot.isoformat()}")`;
  4. one `platform` unit: `next = slot + every * (1 + (now - slot) // every)` (the first slot after now); `UPDATE ... SET next_run_at = :next, holder = NULL, leased_until = NULL WHERE name = :name AND holder = :me`.
  A crash between 3 and 4 lets the lease expire; the next scheduler enqueues the same key again, which the job queue ignores while that job is live (an already done slot job runs again: harmless for the purges, documented for future schedules).
- `run()`: forever `run_due()` then `asyncio.sleep(POLL)` (the poll is the cadence of a schedule, not a proof of anything).

`jobs/maintenance.py`:

```python
"""The platform's own jobs (§6.4; Decision 9): purge expired idempotency keys and old done jobs, every schema."""

from datetime import timedelta
from typing import Final
lazy from collections.abc import Sequence

from sqlalchemy import delete, func

from ledger_kernel.clock import utc_now
from ledger_platform.jobs.outcomes import Done, JobSpec
lazy from ledger_kernel.clock import Clock
lazy from ledger_platform.data.bound import BoundDatabase
lazy from ledger_platform.jobs.outcomes import Job, JobOutcome

PURGE_IDEMPOTENCY_KEYS: Final = "platform.purge_idempotency_keys"
PURGE_DONE_JOBS: Final = "platform.purge_done_jobs"
DONE_JOBS_KEPT: Final = timedelta(days=7)


def maintenance_jobs(databases: Sequence[BoundDatabase], *, clock: Clock = utc_now) -> tuple[JobSpec, JobSpec]:
    """The two purge jobs over every bound schema (the platform's assembly hands them to the runner)."""

    async def purge_keys(job: Job) -> JobOutcome:
        del job
        for database in databases:
            keys = database.tables.idempotency_keys
            async with database.purging() as connection:
                await connection.execute(delete(keys).where(keys.c.expires_at < func.now()))
        return Done()

    async def purge_jobs(job: Job) -> JobOutcome:
        del job
        cutoff = clock() - DONE_JOBS_KEPT
        for database in databases:
            jobs = database.tables.jobs
            async with database.unit(None) as unit:
                await unit.connection.execute(delete(jobs).where(jobs.c.status == "done", jobs.c.updated_at < cutoff))
        return Done()

    return JobSpec(kind=PURGE_IDEMPOTENCY_KEYS, handler=purge_keys), JobSpec(kind=PURGE_DONE_JOBS, handler=purge_jobs)
```

`jobs/__init__.py`: docstring and `__all__` (`Job`, `Done`, `RunAgain`, `Park`, `JobOutcome`, `JobHandler`, `JobSpec`, `JobRunner`, `Schedule`, `Scheduler`, `maintenance_jobs`, `PURGE_IDEMPOTENCY_KEYS`, `PURGE_DONE_JOBS`).

- [ ] **Step 4: Run the tests, then the gate**

Run: `uv run pytest packages/platform -n auto -q` → all pass. `uv run poe check` → green.

- [ ] **Step 5: Commit**

```bash
git add -A packages/platform pyproject.toml
git commit -m "feat(platform): durable jobs with leases, fencing and attempts on claim; schedules on lease rows; purges

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: Runtime types — settings, calls, the facade guard, typed directories, contexts and parts, registries (∥ Task 13)

Spec §3.4, §3.10, §5.2 (`runtime`, `config`), §8.1-§8.2 (registries), ADR 0005; Decisions 2, 10, 11, 12, 13, 14. After Tasks 10 and 11 (`ModuleParts` names their `ConsumerSpec` and `JobSpec`); may overlap Task 13. Files only under `runtime/` and `config/settings.py` (+ tests). The shared root edit: in the platform layers contract, drop `runtime` from the bottom layer and set `exhaustive = false` until Task 16 restores the final contract (the composition root imports every layer, while v1's flat modules still import `runtime.supervision` from below).

**Files:**
- Create: `packages/platform/src/ledger_platform/runtime/{calls,facade,keys,contexts,registries}.py`
- Modify: `packages/platform/src/ledger_platform/config/settings.py` (add `Settings`), `packages/platform/pyproject.toml` (`tzdata>=2025.2`)
- Test: `packages/platform/tests/unit/{test_runtime_calls,test_runtime_keys,test_runtime_registries,test_config_settings_v2}.py`, `packages/platform/tests/integration/test_runtime_facade.py`

**Interfaces:**
- Consumes: `open_unit` (Task 9), `Actor`, `acting_as` (Task 9), `PackageManifest`, `ChannelEntry`, `AppRole` (Task 5), `RegimeDef`, `TaxIdSchemes` (kernel), `Principal`, `Scope`, `BoundDatabase`, `SchemaMigrations`, `ConsumerSpec` (Task 10), `JobSpec` (Task 11), `Check` (observability.health), `Supervisor`.
- Produces:
  - `config.settings.Settings` (one class for every app, Decision 14): `environment`, `log_level`, `log_format`, `database_url: SecretStr`, `database_direct_url: SecretStr | None`, `http_port: int = 8000`, `otlp_endpoint`, `token_issuer: str | None`, `token_audience: str | None`, `token_jwks_url: str | None`, `token_algorithms`, `token_leeway_seconds`; fail-closed on unknown `LEDGER_*`; property `listen_url -> str` (the direct URL as libpq, falling back to the database URL).
  - `runtime.calls`: `Call(*, principal, scope, request_id, traceparent=None)`, `SystemCall(*, reason, scope, call_id=<new>, traceparent=None)`, `actor_of(call) -> Actor`, `acting(call) -> ContextManager[None]`.
  - `runtime.facade`: `facade` (decorator for async methods), `UnitOpenError` (internal, `unit_open`).
  - `runtime.keys`: `Key[T](name: str, check: Callable[[object], TypeIs[T]])` with `bind(value: T) -> Binding` and `take(value: object) -> T`; `Binding(name, value)`; `Directory(label)` with `add(binding)`, `view(allowed: frozenset[str]) -> DirectoryView`; `DirectoryView.get[T](key: Key[T]) -> T`; `type ApiDirectory = DirectoryView`, `type ExtensionDirectory = DirectoryView`, `type Ports = DirectoryView`; errors `UndeclaredDependencyError` (`dependency_undeclared`), `BindingMissingError` (`binding_missing`), `BindingTypeError` (`binding_type`), `DuplicateBindingError` (`binding_duplicate`), all internal.
  - `runtime.contexts`: `Telemetry(logger, tracer, registry)` with `Telemetry.of(name: str, tracing: Tracing, registry: CollectorRegistry)` (a logger bound to `package=name`, the tracer of `tracing`); `ModuleContext(*, manifest, role, settings, database, clock, regimes, channels, apis, extensions, telemetry)` (Task 14 adds `auth`); `ModuleParts(*, api: Binding | None = None, implements: tuple[Binding, ...] = (), provides: tuple[Binding, ...] = (), routers: Sequence[APIRouter] = (), consumers: Sequence[ConsumerSpec] = (), jobs: Sequence[JobSpec] = (), checks: Mapping[str, Check] = {}, resources: Sequence[AbstractAsyncContextManager[object]] = (), migrations: SchemaMigrations | None = None)`; `Module(*, manifest, assemble: Callable[[ModuleContext], ModuleParts])`; `ChannelContext(*, definition: ChannelEntry, regime: RegimeDef, role, settings, database, clock, ports: Ports, telemetry)`; `ChannelParts(*, channel: object, routers=(), receiver_routers=(), consumers=(), jobs=(), checks={}, resources=(), migrations=None)`; `ChannelPack` (runtime-checkable Protocol: `key: str`, `regime: str`, `assemble(context) -> ChannelParts`); `AssembledChannels` (`add(key, channel)` and `seal()` for the app; `get(key) -> object` and `keys()` for modules, at call time; reading before `seal()` raises `ChannelsNotAssembledError`, internal, `channels_not_assembled`; adding after it or twice is a defect). `ChannelPack` also carries `entry: ChannelEntry` (its manifest entry, from the country's `_manifest.py`).
  - `runtime.registries`: `Discovered(name, load: Callable[[], object])`, `type Discover = Callable[[str], Sequence[Discovered]]`, `installed(group) -> tuple[Discovered, ...]`, groups `MODULES = "ledger.modules"`, `REGIMES = "ledger.countries.regimes"`, `CHANNELS = "ledger.countries.channels"`; `Regimes` (`load(discover=installed)`, `get(key)`, `keys()`, `tax_id_schemes`), `Channels` (`load(regimes, discover=installed)`, `packs`), `load_modules(discover=installed) -> tuple[Module, ...]` (suspended skipped); `InvalidPackError` (internal, `invalid_pack`), `RegimeUnknownError` (internal, `regime_unknown`).

- [ ] **Step 1: Write the failing tests**

`test_runtime_keys.py`:

```python
from typing import Protocol, TypeIs, runtime_checkable

import pytest

from ledger_platform.runtime.keys import (
    BindingMissingError,
    BindingTypeError,
    Directory,
    DuplicateBindingError,
    Key,
    UndeclaredDependencyError,
)


@runtime_checkable
class Greeter(Protocol):
    def greet(self) -> str: ...


def is_greeter(value: object) -> TypeIs[Greeter]:
    return isinstance(value, Greeter)


GREETER = Key[Greeter]("greeter", is_greeter)


class English:
    def greet(self) -> str:
        return "hello"


def test_a_bound_value_is_read_typed_through_a_declared_dependency() -> None:
    directory = Directory("apis")
    directory.add(GREETER.bind(English()))
    assert directory.view(frozenset({"greeter"})).get(GREETER).greet() == "hello"


def test_an_undeclared_dependency_is_refused_even_when_bound() -> None:
    directory = Directory("apis")
    directory.add(GREETER.bind(English()))
    with pytest.raises(UndeclaredDependencyError, match="greeter"):
        directory.view(frozenset()).get(GREETER)


def test_a_missing_or_doubled_binding_is_a_defect() -> None:
    directory = Directory("apis")
    with pytest.raises(BindingMissingError):
        directory.view(frozenset({"greeter"})).get(GREETER)
    directory.add(GREETER.bind(English()))
    with pytest.raises(DuplicateBindingError):
        directory.add(GREETER.bind(English()))


def test_a_value_of_the_wrong_type_under_a_name_is_a_defect() -> None:
    directory = Directory("apis")
    directory.add(Key[object]("greeter", lambda value: True).bind(42))
    with pytest.raises(BindingTypeError):
        directory.view(frozenset({"greeter"})).get(GREETER)
```

(`lambda value: True` does not narrow: write a named `def anything(value: object) -> TypeIs[object]: return True`.)

`test_runtime_calls.py`: `actor_of(Call(principal=..., scope=..., request_id="r-1"))` is `Actor(<subject>, "r-1")`; `actor_of(SystemCall(reason="consumer:x", scope=None, call_id="c"))` is `Actor("system:consumer:x", "c")`; a `SystemCall` without `call_id` gets a fresh UUIDv7 text; `acting(call)` binds the actor for the block.

`integration/test_runtime_facade.py` (Review Focus: rule 13):

```python
class Greeting:
    @facade
    async def hello(self, name: str) -> str:
        return f"hello {name}"


async def test_a_facade_answers_outside_any_unit(probe: BoundDatabase) -> None:
    assert await Greeting().hello("x") == "hello x"


async def test_a_facade_called_while_a_unit_is_open_is_refused(probe: BoundDatabase) -> None:
    async with probe.unit(None):
        with pytest.raises(UnitOpenError, match="Greeting.hello"):
            await Greeting().hello("x")


async def test_a_task_started_inside_a_unit_may_call_after_it_closed(probe: BoundDatabase) -> None:
    gate = asyncio.Event()

    async def later() -> str:
        await gate.wait()
        return await Greeting().hello("y")

    async with probe.unit(None):
        task = asyncio.create_task(later())
    gate.set()
    assert await task == "hello y"
```

`test_runtime_registries.py` (fake discovery, no installed packages needed):

- `test_regimes_load_validate_and_freeze` — two regimes discovered; `get("us")`; `keys() == ("mx", "us")`; `tax_id_schemes.parse("us.probe", "1234")` works across regimes.
- `test_a_duplicate_regime_key_an_unknown_time_zone_or_a_wrong_object_fails_the_start` — each raises `InvalidPackError` naming the entry point: two entries with key `us`; a regime with `time_zone="Mars/Olympus"` (format valid, `zoneinfo` refuses it); an entry whose `load()` returns `42`; an entry named `xx` whose regime key is `us`.
- `test_channels_need_a_known_regime_and_a_unique_key` — `Channels.load(regimes, discover)` with a pack whose `regime` is unknown → `InvalidPackError`; two packs of one key → `InvalidPackError`.
- `test_modules_load_and_suspended_ones_are_skipped` — two `Module`s discovered, one with `status = "suspended"`: `load_modules` returns the active one; an entry named differently from its manifest → `InvalidPackError`.
- `test_installed_reads_entry_points` — `installed("ledger.no-such-group") == ()`.

`test_config_settings_v2.py`: `Settings()` from a monkeypatched environment: required `LEDGER_DATABASE_URL`; an unknown `LEDGER_NOPE` fails with the name in the error (fail closed); token settings optional; a non-`https` JWKS URL is refused in `staging`; `listen_url` is the direct URL in libpq form, or the database URL's when no direct URL is set; `SecretStr` values never appear in `repr(settings)`.

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest packages/platform/tests/unit/test_runtime_keys.py packages/platform/tests/unit/test_runtime_registries.py -q -p no:randomly`
Expected: `ModuleNotFoundError: No module named 'ledger_platform.runtime.keys'`.

- [ ] **Step 3: Implement**

`runtime/keys.py`:

```python
"""Typed directories (§3.10; Decision 12): a module's API, an extension point's implementation, a channel's port.

A ``Key[T]`` names one bound thing and knows how to recognize it (a ``TypeIs`` function, usually ``isinstance``
on a runtime-checkable Protocol): binding checks the type statically, reading checks it at runtime, and no cast
is ever needed. A module reads only the keys of its declared dependencies (``view(allowed)``): an undeclared edge
fails at assembly, never later.
"""

from dataclasses import dataclass
from typing import ClassVar, TypeIs, final
lazy from collections.abc import Callable

from ledger_kernel.errors import LedgerError


class UndeclaredDependencyError(LedgerError):
    code: ClassVar[str] = "dependency_undeclared"

    def __init__(self, label: str, name: str) -> None:
        super().__init__(f"{label} {name!r} is read by a package that does not declare it")


class BindingMissingError(LedgerError):
    code: ClassVar[str] = "binding_missing"

    def __init__(self, label: str, name: str) -> None:
        super().__init__(f"{label} {name!r} is declared but nothing provides it")


class BindingTypeError(LedgerError):
    code: ClassVar[str] = "binding_type"

    def __init__(self, name: str) -> None:
        super().__init__(f"what is bound as {name!r} is not of the type its key names")


class DuplicateBindingError(LedgerError):
    code: ClassVar[str] = "binding_duplicate"

    def __init__(self, label: str, name: str) -> None:
        super().__init__(f"{label} {name!r} is provided twice")


@final
@dataclass(frozen=True, slots=True)
class Binding:
    """A value bound under a key's name."""

    name: str
    value: object


@final
class Key[T]:
    """The typed name of a bound value."""

    __slots__ = ("_check", "name")

    def __init__(self, name: str, check: Callable[[object], TypeIs[T]]) -> None:
        self.name = name
        self._check = check

    def bind(self, value: T) -> Binding:
        """``value`` under this key (statically checked)."""
        return Binding(self.name, value)

    def take(self, value: object) -> T:
        """``value`` as ``T`` (checked at runtime)."""
        if self._check(value):
            return value
        raise BindingTypeError(self.name)


@final
class DirectoryView:
    """What one package may read of a directory."""

    __slots__ = ("_allowed", "_directory")

    def __init__(self, directory: Directory, allowed: frozenset[str]) -> None:
        self._directory = directory
        self._allowed = allowed

    def get[T](self, key: Key[T]) -> T:
        """The value bound under ``key``, if the reader declared it."""
        if key.name not in self._allowed:
            raise UndeclaredDependencyError(self._directory.label, key.name)
        return key.take(self._directory.value_of(key.name))


@final
class Directory:
    """Bindings by name, filled at assembly."""

    __slots__ = ("_values", "label")

    def __init__(self, label: str) -> None:
        self.label = label
        self._values: dict[str, object] = {}

    def add(self, binding: Binding) -> None:
        """Bind; a name is bound once."""
        if binding.name in self._values:
            raise DuplicateBindingError(self.label, binding.name)
        self._values[binding.name] = binding.value

    def value_of(self, name: str) -> object:
        """The value bound under ``name`` (``BindingMissingError`` if none)."""
        if name not in self._values:
            raise BindingMissingError(self.label, name)
        return self._values[name]

    def view(self, allowed: frozenset[str]) -> DirectoryView:
        """What a package that declared ``allowed`` may read."""
        return DirectoryView(self, allowed)


type ApiDirectory = DirectoryView
type ExtensionDirectory = DirectoryView
type Ports = DirectoryView
```

`runtime/facade.py`:

```python
"""The facade guard (§3.4, rule 13): no cross-module call while a unit of work is open on the task."""

import functools
from typing import ClassVar
lazy from collections.abc import Callable, Coroutine

from ledger_kernel.errors import LedgerError
from ledger_platform.data.unit_of_work import open_unit


class UnitOpenError(LedgerError):
    """A facade was called while the caller held a transaction: a defect (it could hold locks across modules)."""

    code: ClassVar[str] = "unit_open"

    def __init__(self, name: str) -> None:
        super().__init__(f"{name} was called while a unit of work is open on the task (rule 13)")


def facade[**P, R](method: Callable[P, Coroutine[object, object, R]]) -> Callable[P, Coroutine[object, object, R]]:
    """Refuse calls to ``method`` made while a unit of work is open on the calling task."""

    @functools.wraps(method)
    async def guarded(*args: P.args, **kwargs: P.kwargs) -> R:
        if open_unit() is not None:
            raise UnitOpenError(method.__qualname__)
        return await method(*args, **kwargs)

    return guarded
```

`runtime/calls.py`:

```python
"""Calls (§3.4): who asks, for whom, under which request; and the actor every audit row then records."""

from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import final
lazy from collections.abc import Generator

from ledger_kernel.ids import new_id
from ledger_platform.data.audit import Actor, acting_as
lazy from ledger_kernel.access import Principal
lazy from ledger_kernel.tenancy import Scope


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Call:
    """A request made by a principal: built at the HTTP edge only."""

    principal: Principal
    scope: Scope
    request_id: str
    traceparent: str | None = None


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class SystemCall:
    """A call the platform makes itself (a consumer, a job, the receiver); it names its reason."""

    reason: str
    scope: Scope | None
    call_id: str = field(default_factory=lambda: new_id().hex)
    traceparent: str | None = None


def actor_of(call: Call | SystemCall) -> Actor:
    """Who acts in ``call``, as audit rows record it."""
    match call:
        case Call(principal=principal, request_id=request_id):
            return Actor(principal.subject, request_id)
        case SystemCall(reason=reason, call_id=call_id):
            return Actor(f"system:{reason}", call_id)


@contextmanager
def acting(call: Call | SystemCall) -> Generator[None]:
    """``call``'s actor for the length of the block."""
    with acting_as(actor_of(call)):
        yield
```

`runtime/contexts.py` and `runtime/registries.py`: write them with the dataclasses and functions of the Interfaces block (frozen, slotted, keyword-only dataclasses with one-line field docstrings taken from §3.10; `EMPTY` mappings as `MappingProxyType({})`). The registries read discovered objects through `TypeAdapter(InstanceOf[RegimeDef])`, `TypeAdapter(InstanceOf[ChannelPack])` and `TypeAdapter(InstanceOf[Module])`; a `ValidationError` becomes `InvalidPackError(group, name)`; `Regimes` checks each `time_zone` with `zoneinfo.ZoneInfo` (`ZoneInfoNotFoundError` → `InvalidPackError`) and builds one `TaxIdSchemes` over every regime's schemes. `installed`:

```python
def installed(group: str) -> tuple[Discovered, ...]:
    """The entry points of ``group`` installed in this environment."""
    return tuple(Discovered(entry.name, entry.load) for entry in entry_points(group=group))
```

`config/settings.py` — add beside `ServiceSettings` (which Task 16 deletes):

```python
class Settings(BaseSettings):
    """Settings of every app process (Decision 14): one class, so one environment file serves every app."""

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_prefix="LEDGER_", extra="forbid", frozen=True, env_ignore_empty=True, hide_input_in_errors=True
    )

    environment: Literal["local", "test", "staging", "production"] = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: LogFormat = LogFormat.CONSOLE
    database_url: SecretStr = Field(default=...)
    """``postgresql+psycopg://ledger_app:...@pooler:5432/ledger``: units, through a transaction pooler if any."""
    database_direct_url: SecretStr | None = None
    """Direct to PostgreSQL (§6.7): the worker's LISTEN connection and ``ledger-migrate`` (as ``ledger_migrator``)."""
    http_port: Annotated[int, Field(ge=1, le=65535)] = 8000
    otlp_endpoint: str | None = None
    token_issuer: str | None = Field(default=None, min_length=1, max_length=2048)
    token_audience: str | None = Field(default=None, min_length=1, max_length=255)
    token_jwks_url: str | None = Field(default=None, min_length=1, max_length=2048)
    token_algorithms: frozenset[TokenAlgorithm] = Field(default=frozenset({"RS256"}), min_length=1)
    token_leeway_seconds: Annotated[int, Field(ge=0, le=300)] = 60
```

with the `database_url`/`database_direct_url` psycopg validator, the https-JWKS rule (when a JWKS URL is set), the fail-closed source (`settings_customise_sources`, as `ServiceSettings`), and:

```python
    @property
    def listen_url(self) -> str:
        """The direct URL in libpq form (psycopg's LISTEN connection); the database URL when none is set."""
        url = (self.database_direct_url or self.database_url).get_secret_value()
        return url.replace("postgresql+psycopg://", "postgresql://", 1)
```

- [ ] **Step 4: Run the tests, then the gate**

Run: `uv run pytest packages/platform -n auto -q` → all pass. `uv run poe check` → green.

- [ ] **Step 5: Commit**

```bash
git add -A packages/platform pyproject.toml uv.lock
git commit -m "feat(platform): runtime types - calls, facade guard, typed directories, contexts, registries, Settings

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 13: HTTP and auth split; `Ledger-Mode`; idempotency v2 (∥ Tasks 10, 11, 12)

Spec §5.2 (`http`, `auth`), §9.5-§9.7, §10 (identity provider row), audit §4.1, D22-D24; Decisions 4, 5. After Task 9. Files only under `http/` and `auth/` (+ tests, + the identity and platform test imports of moved names).

**Files:**
- Create: `packages/platform/src/ledger_platform/http/{problems,metrics,probes,models,openapi,mode,idempotency}.py`, `packages/platform/src/ledger_platform/auth/{keys}.py`, `packages/platform/src/ledger_platform/auth/providers/{__init__,zitadel}.py`
- Modify: `http/app.py` (keeps `create_app`, `ApiInfo`, `Observability`; the problem builder, route labels/metrics middleware and probes move out to the new modules), `auth/tokens.py` (keys and Zitadel rules move out), importers of moved names (platform tests, identity src and tests)
- Test: `packages/platform/tests/unit/{test_http_problems,test_http_models,test_http_openapi,test_http_mode,test_auth_keys,test_auth_zitadel}.py` (tests move with the code from `test_http_app.py` and `test_auth_tokens.py`), `packages/platform/tests/integration/test_http_idempotency.py`

**Interfaces:**
- Consumes: `UnitOfWork`, `BoundDatabase`, `chassis_tables` (Task 9); `Mode` (kernel); v1 `http/deps.py` stays as is for identity until Task 15.
- Produces:
  - `http.problems`: `PROBLEM_JSON`, `Problem` (pydantic: `type`, `title`, `status`, `code`, `detail`, `request_id`; extra members allowed), `BadRequestError(InvalidInputError)` (answered 400, kind `INVALID`), `status_of(error: LedgerError) -> HTTPStatus`.
  - `http.models`: `Page[T]` (pydantic generic: `items: list[T]`, `next_cursor: str | None`), `page_of_items[T](page: kernel Page[...], render: Callable) -> Page[T]` (optional helper if identity needs it).
  - `http.openapi`: `problems(*statuses: int) -> Answers`, `success(status: HTTPStatus, *headers: SuccessHeader) -> Answers`, `precondition_checked(*stale: type[Exception]) -> ContextManager[None]` (412 when one of `stale` is raised inside).
  - `http.mode`: `MODE_HEADER = "Ledger-Mode"`, `ModeRequiredError(BadRequestError)` (`mode_required`), `ModeInvalidError(BadRequestError)` (`mode_invalid`), dependency `async def ledger_mode(header: Annotated[str | None, Header(alias="Ledger-Mode")] = None) -> Mode`.
  - `http.idempotency` (v2): `DEFAULT_RETENTION = timedelta(days=7)`, `MAX_KEY_LENGTH = 255`, `MAX_STORED_BODY = 1 MiB`, `REPLAYED_HEADER = "Idempotent-Replayed"`; `CanonicalBody`, `canonical_body(model | None)`; `StoredResponse(status, body, headers)`; `Outcome(response, replayed)`; `Attempt(*, principal: str, route: str, key: str, request_hash: bytes)` with `Attempt.of(principal: Principal, key: str, *, route: str, body: CanonicalBody)`; `Idempotency(*, retention=DEFAULT_RETENTION)` with `async once(unit: UnitOfWork, attempt: Attempt, work: Callable[[], Awaitable[StoredResponse]]) -> Outcome`; dependency `idempotency_key` (the `Idempotency-Key` header); `attempt_of(request, principal, key, body=None) -> Attempt` (route = method + the route's path template); `json_response(status, body, headers=...) -> StoredResponse`; `answer(outcome) -> Response`; errors `InvalidIdempotencyKeyError` (`invalid_idempotency_key`), `IdempotencyKeyReusedError` (`idempotency_key_reused`, 422), `IdempotencyKeyInUseError` (`idempotency_key_in_use`, 409), `UnmodedIdempotencyScopeError` (internal, `idempotency_scope_unmoded`), `UnansweredIdempotencyKeyError`, `OversizedStoredResponseError`, `ServerFailureResponseError` (internal).
  - `auth.keys`: `fetch_keys`, `HttpKeys`, `PublishedKeys`, `TokenKeysUnavailableError`, `KeysClientClosedError` (moved, unchanged).
  - `auth.tokens`: `TokenPolicy`, `TokenVerifier`, `TokenProblem`, `MissingTokenError`, `InvalidTokenError`, `InvalidTokenOptionError` (unchanged behaviour; the claim rules are read from `auth.providers.zitadel`).
  - `auth.providers.zitadel`: `ORGANIZATION_CLAIM`, `project_roles_claim(project)`, `organization_of(provider_organization) -> OrganizationId`, the access-token/id-token claim sets (moved unchanged).

- [ ] **Step 1: Move the code and its tests (no behaviour change)**

Cut `fetch_keys`, `HttpKeys`, `PublishedKeys`, their constants and errors from `auth/tokens.py` into `auth/keys.py`; cut `ORGANIZATION_CLAIM`, `organization_of`, `project_roles_claim`, `ACCESS_TOKEN_CLAIMS`, `ID_TOKEN_CLAIMS`, `ACCESS_TOKEN_TYPES` and `_granted_roles` into `auth/providers/zitadel.py`; `tokens.py` imports them. Cut the problem builder and the kind → status table from `http/app.py` into `http/problems.py`, the route labels and the measuring middleware into `http/metrics.py`, the probe routes into `http/probes.py`; `create_app` composes them. Move the tests that cover the moved code into the matching new test modules (`git mv` the test file first when most of it moves, then cut the rest). Update importers (`git grep -n "organization_of\|HttpKeys\|PublishedKeys\|fetch_keys\|PROBLEM_JSON"` lists them, identity's tests and `main.py` among them).

Run: `uv run poe check` → green with the same test count. Commit:

```bash
git add -A
git commit -m "refactor(platform): http and auth split into problems, metrics, probes, keys and the zitadel provider

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 2: Write the failing tests**

`test_http_mode.py`:

```python
from typing import Annotated

import httpx
import pytest
from fastapi import Depends, FastAPI

from ledger_kernel.tenancy import Mode
from ledger_platform.http.mode import ledger_mode
from ledger_platform.http.problems import install_problem_handlers


def app() -> FastAPI:
    built = FastAPI()
    install_problem_handlers(built)

    @built.get("/moded")
    async def moded(mode: Annotated[Mode, Depends(ledger_mode)]) -> dict[str, str]:
        return {"mode": mode.value}

    return built


@pytest.mark.parametrize("sent", ["test", "live"])
async def test_the_mode_header_names_the_mode(sent: str) -> None:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app()), base_url="http://t") as client:
        assert (await client.get("/moded", headers={"Ledger-Mode": sent})).json() == {"mode": sent}


@pytest.mark.parametrize(("headers", "code"), [({}, "mode_required"), ({"Ledger-Mode": "prod"}, "mode_invalid")])
async def test_a_moded_route_without_a_valid_mode_is_a_bad_request(headers: dict[str, str], code: str) -> None:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app()), base_url="http://t") as client:
        response = await client.get("/moded", headers=headers)
    assert (response.status_code, response.json()["code"]) == (400, code)
    assert "prod" not in response.text
```

(`install_problem_handlers(app)` is the part of `create_app` that answers errors as problems, made public by Step 1's split so a test app can have it without the chassis's lifespan.)

`test_http_models.py`: `Page[CompanyView]` serializes as `{"items": [...], "next_cursor": null}` and its OpenAPI schema is named `Page_CompanyView_` (pydantic's generic naming; pin what it produces); `test_http_openapi.py`: `problems(404, 409)` documents both and 503 with `application/problem+json` and the `Problem` schema; `success(HTTPStatus.CREATED, "Location", "ETag")` documents the two headers; `precondition_checked(StaleError)` turns a `StaleError` raised inside into `HTTPException(412)` and lets any other error through.

`test_http_problems.py` gains: a `BadRequestError` subclass answers 400 with its code and `application/problem+json`; every other kind keeps its status.

`packages/platform/tests/integration/test_http_idempotency.py` (Review Focus 4) — `probe` bound database, a work function that inserts an audit-free marker row into `probe.jobs` through `unit.jobs.enqueue` (any write of the unit's own schema) and returns `json_response(201, Model(...))`; write each in full:

- `test_the_first_use_runs_and_a_retry_replays_the_same_bytes` — `once` twice in two units with one `Attempt`: the work ran once; the second `Outcome.replayed` is true and its body, status and headers equal the first's.
- `test_another_body_under_the_same_key_is_refused` — same key, other `request_hash`: `IdempotencyKeyReusedError` (code `idempotency_key_reused`).
- `test_keys_are_scoped_per_route_mode_and_principal` — the same key on `POST /v1/a` and `POST /v1/b`, in `test` and in `live`, by principals `2001` and `2002`: four independent first uses.
- `test_a_failed_request_keeps_nothing` — the work raises inside the unit: the unit rolls back; a retry runs the work.
- `test_a_twin_waits_for_the_first_then_replays` — unit 1 claims and its work awaits an `asyncio.Event`; unit 2 starts `once` with the same attempt in another task; `eventually(lambda: waits_for_a_lock(url))`; set the event; unit 2's outcome is replayed with unit 1's bytes.
- `test_a_twin_that_waits_too_long_is_told_the_key_is_in_use` — unit 2 sets `SET LOCAL lock_timeout = '100ms'` before `once`: `IdempotencyKeyInUseError` (409).
- `test_an_expired_key_runs_again` — set `expires_at` in the past (admin): the next use runs the work and stores a new answer.
- `test_a_unit_without_a_mode_cannot_hold_keys` — `once` in a unit scoped `OrganizationScope(org, None)`: `UnmodedIdempotencyScopeError` before any SQL.
- `test_server_failures_and_huge_answers_are_not_stored` — a 500 response: `ServerFailureResponseError`; a body over 1 MiB: `OversizedStoredResponseError`.

- [ ] **Step 3: Run them to see them fail**

Run: `uv run pytest packages/platform/tests/unit/test_http_mode.py packages/platform/tests/integration/test_http_idempotency.py -q -p no:randomly`
Expected: `ModuleNotFoundError: No module named 'ledger_platform.http.mode'`.

- [ ] **Step 4: Implement**

`http/mode.py`:

```python
"""``Ledger-Mode`` (§9.7, ADR 0007): which mode a human token's request acts in; required on moded routes."""

from typing import Annotated, ClassVar, Final

from fastapi import Header

from ledger_kernel.tenancy import Mode
from ledger_platform.http.problems import BadRequestError

MODE_HEADER: Final = "Ledger-Mode"


class ModeRequiredError(BadRequestError):
    """A moded route was called without ``Ledger-Mode``."""

    code: ClassVar[str] = "mode_required"

    def __init__(self) -> None:
        super().__init__(f"this route acts in a mode: send {MODE_HEADER}: test or live")


class ModeInvalidError(BadRequestError):
    """``Ledger-Mode`` names no mode."""

    code: ClassVar[str] = "mode_invalid"

    def __init__(self) -> None:
        super().__init__(f"{MODE_HEADER} is test or live")


_MODES: Final = {mode.value: mode for mode in Mode}


async def ledger_mode(
    header: Annotated[str | None, Header(alias=MODE_HEADER, description="test or live: the mode the request acts in.")] = None,
) -> Mode:
    """The request's mode (API-client credentials carry theirs from v2-M2)."""
    if header is None:
        raise ModeRequiredError
    mode = _MODES.get(header)
    if mode is None:
        raise ModeInvalidError
    return mode
```

`http/problems.py` (the moved builder plus):

```python
class BadRequestError(InvalidInputError):
    """A request the HTTP layer refuses before reading its body (Decision 5): answered 400, kind INVALID."""

    code: ClassVar[str] = "bad_request"


def status_of(error: LedgerError) -> HTTPStatus:
    """400 for a ``BadRequestError``, else the kind's status (§9.6)."""
    return HTTPStatus.BAD_REQUEST if isinstance(error, BadRequestError) else _STATUS[error.kind]
```

and the problem handler uses `status_of`.

`http/models.py`:

```python
"""Response models every module's API shares (audit D22-D23)."""

from pydantic import BaseModel, ConfigDict
from typing import ClassVar


class Page[T](BaseModel):
    """A page of a list: its items and the cursor of the next page (``null`` on the last)."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    items: list[T]
    next_cursor: str | None
```

`http/openapi.py`: identity's `problems()`, `success()`, `_SUCCESS_HEADERS`, `Answers`, `SuccessHeader` moved here unchanged (with `Problem` from `http.problems`), and:

```python
@contextmanager
def precondition_checked(*stale: type[Exception]) -> Generator[None]:
    """An update whose ``If-Match`` names no current version answers 412 (RFC 9110), not 409."""
    try:
        yield
    except stale as error:
        raise HTTPException(HTTPStatus.PRECONDITION_FAILED) from error
```

`http/idempotency.py` (v2; a new file — v1's flat `idempotency.py` serves identity until Task 15 and is deleted in Task 16):

```python
"""``Idempotency-Key`` (§9.5): the work of a ``POST`` runs once per key; a retry replays its answer.

A key is scoped to the organization, the mode, the principal and the route (method and path template), and
kept seven days in the module's own ``idempotency_keys`` table, in the transaction of the work: the claim, the
module's changes, its events and the stored answer commit together, and a failed request keeps nothing. A twin
waits on the claim's row lock and replays the first's answer; one that waits past the lock timeout is told the
key is in use (409). Another body under a used key is 422. Rows are purged by the platform's job (Decision 6).
"""
```

Implementation — `Idempotency.once`:

```python
async def once(self, unit: UnitOfWork, attempt: Attempt, work: Callable[[], Awaitable[StoredResponse]]) -> Outcome:
    scope = unit.scope
    if scope is None or scope.mode is None:
        raise UnmodedIdempotencyScopeError
    keys = unit.tables.idempotency_keys
    identity = {
        "organization_id": scope.organization, "mode": scope.mode.value,
        "principal": attempt.principal, "route": attempt.route, "key": attempt.key,
    }
    fresh = insert(keys).values(
        **identity, request_hash=attempt.request_hash, created_at=unit.now, expires_at=unit.now + self._retention
    )
    claim = fresh.on_conflict_do_update(
        index_elements=tuple(identity),
        set_={
            "request_hash": fresh.excluded.request_hash, "response_status": None, "response_body": None,
            "response_headers": None, "created_at": fresh.excluded.created_at, "expires_at": fresh.excluded.expires_at,
        },
        where=keys.c.expires_at <= unit.now,
    ).returning(keys.c.key)
    try:
        claimed = (await unit.connection.execute(claim)).first() is not None
    except OperationalError as error:
        if isinstance(error.orig, psycopg.errors.LockNotAvailable):
            raise IdempotencyKeyInUseError from error
        raise
    where = [keys.c[column] == value for column, value in identity.items()]
    if not claimed:
        stored = (await unit.connection.execute(select(keys).where(*where))).mappings().one()
        ...  # validate through a TypeAdapter of the stored columns; another request_hash → IdempotencyKeyReusedError;
        #      no response yet → UnansweredIdempotencyKeyError; else Outcome(StoredResponse(...), replayed=True)
    response = await work()
    await unit.connection.execute(
        update(keys).where(*where).values(
            response_status=response.status, response_body=response.body, response_headers=_storable(response)
        )
    )
    return Outcome(response, replayed=False)
```

`Attempt.of` hashes `route` and the canonical body as v1 hashed method, path and body (length-prefixed parts); `attempt_of(request, principal, key, body)` takes the route template from `request.scope["route"]` (validated with `TypeAdapter(InstanceOf[APIRoute])`) as `f"{request.method} {route.path}"`; `idempotency_key`, `json_response`, `answer`, `canonical_body`, `_storable`, `REPLAYED_HEADER` are v1's, moved here (v1's copies stay in `http/deps.py` for identity until Task 15).

- [ ] **Step 5: Run the tests, then the gate**

Run: `uv run pytest packages/platform modules/identity -n auto -q` → all pass. `uv run poe check` → green.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(platform): Ledger-Mode, 400 problems, Page[T], OpenAPI helpers, route-scoped idempotency keys

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 14: `App` — assemble, start, stop, migrate

Spec §3.3, §3.5 (migrate writes subscriptions and bumps the routing version), §3.6 (schedules), §3.10 (all of it), §6.5, §11 (health); Decisions 7, 11, 13, 14, 15. After Tasks 12 and 13 (and 10, 11).

**Files:**
- Create: `packages/platform/src/ledger_platform/runtime/app.py`, `packages/platform/src/ledger_platform/http/authentication.py`, `packages/platform/manifest.toml`, `packages/platform/src/ledger_platform/_manifest.py` (generated with Task 7's function, Step 1)
- Modify: `runtime/contexts.py` (`ModuleContext.auth: Authentication`), `observability/logs.py` (`configure_logging(settings, *, service: str)` over a small `LogSettings` Protocol; v1 `Service` passes `service=settings.service_name`), `auth/tokens.py` (`TokenPolicy.of_settings(settings: Settings)` → `TokenSettingsMissingError` when a token field is unset)
- Test: `packages/platform/tests/integration/{test_runtime_app_assemble,test_runtime_app_api,test_runtime_app_worker,test_runtime_app_migrate}.py`, `packages/platform/tests/support/probe_module.py` (a fake module used by these tests; `support/` is imported with `--import-mode=importlib` by path through `conftest.py`'s `sys.path` setup, or defined inside each test module)

**Interfaces:**
- Consumes: everything of Tasks 8-13.
- Produces:
  - `runtime.app.App(role: AppRole, *, settings: Settings, version: str = "0.0.0", discover: Discover = installed, clock: Clock = utc_now)` with `asgi() -> FastAPI` (roles `api` and `worker`), `migrate() -> None` (role `migrate`), `modules -> tuple[PackageManifest, ...]` (assembly order), `bound(schema) -> BoundDatabase`; errors `RoleNotBuiltError` (`role_not_built`: `signer`, `receiver`), `ConsumerNotProvidedError`/`ConsumerUndeclaredError`, `JobNotProvidedError`/`JobUndeclaredError`, `MigrationsMissingError`, `DirectUrlMissingError`, `TokenSettingsMissingError` (all internal: a defect of a module or of the deployment, found at start).
  - `http.authentication.Authentication` with `Authentication.of(settings, registry) -> Authentication`, `configured: bool`, `caller` (the FastAPI dependency giving a route its `Principal`), `resources() -> tuple[AbstractAsyncContextManager[object], ...]`.
  - `ledger_platform._manifest.MANIFEST` (the platform's own manifest: schema `platform`, id prefix `evt`, jobs and schedules `platform.purge_idempotency_keys` (every 1 h) and `platform.purge_done_jobs` (every 24 h), script `ledger-probe`).

- [ ] **Step 1: The platform's manifest**

`packages/platform/manifest.toml`:

```toml
[package]
name = "platform"
kind = "package"
summary = "The chassis: configuration, observability, data, events, jobs, runtime, HTTP, auth, gRPC."
owners = ["@balegre0"]
scripts = { "ledger-probe" = "ledger_platform.observability.probe:main" }

[dependencies]
packages = ["kernel"]
python = [
    "alembic>=1.20.0",
    "fastapi>=0.143.0",
    "grpcio>=1.84.0",
    "httpx>=0.28.1",
    "joserfc>=1.7.5",
    "opentelemetry-api>=1.45.1",
    "opentelemetry-exporter-otlp-proto-grpc>=1.45.1",
    "opentelemetry-sdk>=1.45.1",
    "prometheus-client>=0.26.0",
    "psycopg[binary]>=3.3.6",
    "pydantic>=2.14.1",
    "pydantic-settings>=2.15.0",
    "sqlalchemy[asyncio]>=2.1.4",
    "starlette>=1.7.0",
    "structlog>=26.1.0",
    "tzdata>=2025.2",
]

[data]
schema = "platform"

[[ids]]
prefix = "evt"
resource = "event"

[[jobs]]
kind = "platform.purge_idempotency_keys"

[[jobs]]
kind = "platform.purge_done_jobs"

[[schedules]]
name = "platform.purge_idempotency_keys"
every = "1h"
enqueues = "platform.purge_idempotency_keys"

[[schedules]]
name = "platform.purge_done_jobs"
every = "24h"
enqueues = "platform.purge_done_jobs"
```

(`nats-py` and the `ledger-contracts` dependency leave the platform in Task 16; until then the hand-written `pyproject.toml` keeps them and this manifest does not list them — Task 19 generates the `pyproject.toml` from this file.)

Generate `_manifest.py` with Task 7's function, once, by hand:

```bash
uv run python - <<'EOF'
import tomllib
from pathlib import Path
from ledger_devtools.generate import manifest_module_text
from ledger_devtools.workspace import Package
from ledger_platform.runtime.manifest import load_manifest
text = Path("packages/platform/manifest.toml").read_text("utf-8")
package = Package(Path("packages/platform"), load_manifest(text), tomllib.loads(text))
Path("packages/platform/src/ledger_platform/_manifest.py").write_text(manifest_module_text(package), "utf-8", newline="\n")
EOF
```

- [ ] **Step 2: Write the failing tests**

The tests build `App`s over fake discovery: `discover(group)` returns the fake modules, regimes and channel packs the test defines. The fake module `probe`:

```python
PROBE = load_manifest("""
[package]
name = "probe"
kind = "module"
summary = "A module for the chassis's tests."
owners = ["@o"]

[dependencies]
packages = ["kernel", "platform"]

[data]
schema = "probe"

[[events.publishes]]
type = "probe.thing.renamed"
version = 1

[[events.consumes]]
consumer = "probe.echo"
types = ["probe.thing.renamed"]

[[jobs]]
kind = "probe.export"

[[schedules]]
name = "probe.nightly"
every = "24h"
enqueues = "probe.export"

[http]
mounted_in = ["api"]
prefixes = ["/v1/probes"]
""")
```

and `assemble(context)` returns `ModuleParts(routers=(router,), consumers=(ConsumerSpec(name="probe.echo", handler=record),), jobs=(JobSpec(kind="probe.export", handler=export),), migrations=SchemaMigrations(schema="probe", scripts="probe_migrations:migrations"))`, where `probe_migrations` is a package the test writes into `tmp_path` (an `env.py` calling `run_schema_migrations()` and `versions/r0001_probe.py` calling `chassis_schema_v1(op, "probe")`) and puts on `sys.path` with `monkeypatch.syspath_prepend`.

`test_runtime_app_assemble.py` (write each in full):

- `test_modules_assemble_in_dependency_order_and_read_only_declared_apis` — modules `a` (provides `A_API`) and `b` (depends on `a`, reads `A_API` in `assemble`): `App("api", ...)` succeeds, `app.modules` names `platform`-less modules in order `a`, `b`; a module `c` reading `A_API` without declaring `a` fails construction with `UndeclaredDependencyError`.
- `test_a_consumer_or_job_must_be_declared_and_provided` — four variants: a spec without a declaration and a declaration without a spec, for consumers and for jobs, each failing construction with its error.
- `test_channels_assemble_after_modules_with_the_ports_modules_provide` — a regime `us` and a channel pack `us-probe-v1` (regime `us`, `entry` with schema `probe`); module `a` provides `PROBE_PORT`; the pack's `assemble` reads `context.ports.get(PROBE_PORT)` and `context.regime.key`; after construction the module's `context.channels.get("us-probe-v1")` answers the pack's channel; read during `assemble` it raises `ChannelsNotAssembledError`.
- `test_signer_and_receiver_are_not_built_yet` — `RoleNotBuiltError` for both.
- `test_the_api_needs_token_settings` — `App("api", settings=<no token settings>)` raises `TokenSettingsMissingError`; `App("worker", ...)` with the same settings does not.

`test_runtime_app_api.py` — on a database migrated by `App("migrate")` (a `fresh_database`): `App("api").asgi()` in its lifespan (`app.router.lifespan_context(app)`), with `httpx.ASGITransport`: `GET /v1/probes/ping` with a `local_issuer` token answers the router's answer; `/readyz` is 200 with checks `database`, `schema:platform`, `schema:probe`; on a database never migrated `/readyz` is 503 naming `schema:probe`; `/livez` is 200; the OpenAPI document lists `/v1/probes/ping` and no worker route.

`test_runtime_app_worker.py` — `App("worker").asgi()` in its lifespan on a migrated database (settings with `database_direct_url`): publish `probe.thing.renamed` through `app.bound("probe").unit(...)`; `eventually` the `probe.echo` handler has the event (presence, bounded); enqueue `probe.export` and `eventually` it is `done`; the scheduler has enqueued `probe.nightly@...` (`eventually`); `/readyz` includes `listen` and `workers`; leaving the lifespan stops every worker (no task of the app left: `asyncio.all_tasks()` minus the test's own is empty).

`test_runtime_app_migrate.py` (Review Focus 5):

- `test_migrate_brings_every_schema_to_head_grants_the_relay_and_writes_the_subscriptions` — on a `fresh_database` with the roles: `App("migrate").migrate()`; `platform.alembic_version` and `probe.alembic_version` at head; `probe.subscriptions` holds `("probe.echo", "probe.thing.renamed", true)`; `platform.routing.version == 1`; a `ledger_relay` transaction can `SELECT` `probe.outbox`.
- `test_migrate_again_changes_nothing_but_the_routing_version` — a second `migrate()`: same heads, same subscriptions, version 2 (every rollout bumps; fan-outs reload).
- `test_two_migrations_at_once_serialize` — two threads run `migrate()` together (`asyncio.to_thread` twice): both finish, heads are right, no error (the schema advisory lock serializes them).
- `test_a_removed_consumer_is_deactivated_not_deleted` — migrate with `probe.echo` declared, then with an `App` whose probe manifest drops it: the row is `active = false`.
- `test_migrate_needs_the_direct_url` — settings without `database_direct_url`: `DirectUrlMissingError`.

- [ ] **Step 3: Run them to see them fail**

Run: `uv run pytest packages/platform/tests/integration/test_runtime_app_assemble.py -q -p no:randomly`
Expected: `ModuleNotFoundError: No module named 'ledger_platform.runtime.app'`.

- [ ] **Step 4: Implement**

`http/authentication.py`:

```python
"""The chassis's request authentication, shared by every module's routers (§10).

Made once per app from the token settings (Decision 14): the identity provider's keys are fetched by one HTTP
client, opened as a resource while the app runs, and one verifier checks every bearer token.
"""
```

`Authentication.of(settings, registry)`: if all three token settings are set, build `TokenPolicy.of_settings(settings)`, `HttpKeys()`, `PublishedKeys(policy, fetch=keys, registry=registry)`, `TokenVerifier(policy, keys=...)`, `caller = authenticated(verifier)`; else an unconfigured instance whose `caller` dependency raises `TokenSettingsMissingError` (never mounted: the `api` role refuses to start without the settings) and whose `resources()` is empty.

`runtime/app.py` — module docstring:

```python
"""An app (§3.3, §3.10): the composition root every console script runs, in two phases.

Assemble (at construction, no I/O): load the platform's and the modules' manifests, the regimes and the channels
(entry points); make the pool object and one ``BoundDatabase`` per schema; assemble the modules in dependency
order, each reading only the APIs, extensions and ports of its declared dependencies; then the channels, with the
ports modules provide; then merge what they handed back and check it against the manifests (every declared
consumer and job is provided, nothing undeclared is). Start (``asgi()``'s lifespan, or ``migrate()``): open
what the role needs, nothing more.

- ``api``: the routers of every module mounted in ``api``; the identity provider's keys as a resource.
- ``worker``: the fan-out, an inbox runner per consumer, a job runner per schema with jobs, the scheduler and the
  LISTEN connection, each supervised; ``/livez``, ``/readyz`` and ``/metrics`` on the worker's HTTP port.
- ``migrate``: every schema's revisions in order (platform first), the relay's grants, the subscriptions, the
  routing version.
- ``signer``, ``receiver``: refused until their modules exist (v2-M2, v2-M4).
"""
```

Write `App` in full following the docstring. The construction steps, in code order:

1. `role in {"signer", "receiver"}` → `RoleNotBuiltError`. `role == "api"` and `Authentication.of(...)` unconfigured → `TokenSettingsMissingError`.
2. `configure_logging(settings, service=f"ledger-{role}")`; `registry = CollectorRegistry()` (+ process, platform, GC collectors); `tracing = Tracing.create(service_name=f"ledger-{role}", exporter=<OTLP when settings.otlp_endpoint>)`.
3. `database = Database.create(settings.database_url, application_name=f"ledger-{role}")`; `signals = Notifier(database)`.
4. `regimes = Regimes.load(discover)`; `channel_packs = Channels.load(regimes, discover)`; `modules = load_modules(discover)` ordered with `graphlib.TopologicalSorter` over `dependencies.modules`.
5. `self._bound = {"platform": BoundDatabase(database, schema="platform", source="platform", signals=signals, publishes=..., jobs=...)}` and one per module schema (`source` = module name; `publishes` = its event types; `jobs` = `{decl.kind: JobPolicy(max_attempts=decl.max_attempts, deadline=decl.deadline)}`).
6. `apis, extensions, ports = Directory("api"), Directory("extension"), Directory("port")`; `assembled = AssembledChannels()`; for each module: `ModuleContext(manifest, role, settings, database=self._bound[schema], clock, regimes, channels=assembled, apis=apis.view(frozenset(manifest.dependencies.modules)), extensions=extensions.view(frozenset(manifest.extension_points.declares)), telemetry=Telemetry.of(name, tracing, registry), auth=authentication)`; `parts = module.assemble(context)`; an `api` binding must be named after the module (`BindingTypeError` otherwise); add `parts.api`, `parts.implements`, `parts.provides`.
7. For each channel pack: a `BoundDatabase` for `pack.entry.schema_name` (source = channel key); `ChannelContext(definition=pack.entry, regime=regimes.get(pack.regime), role, settings, database, clock, ports=ports.view(<every provided name>), telemetry)`; `assembled.add(pack.key, pack.assemble(context).channel)`; then `assembled.seal()`.
8. Merge: for each module, `Consumer.of(spec, decl)` for every spec (declared names from `manifest.events.consumes`; a spec without a declaration or a declaration without a spec raises), `{decl.kind: (decl, spec.handler)}` likewise for jobs; schedules: `Schedule(name, every, enqueues, target=self._bound[<owner schema>])` from every manifest (platform's included); the platform's own job handlers are `maintenance_jobs(tuple(self._bound.values()), clock=clock)`.

`asgi()`: routers of modules whose `http.mounted_in` holds the role; `Health` with `database` (`database.ping`), one `schema:<name>` check per `SchemaMigrations` (platform's and each module's `parts.migrations`; a module with a schema and no migrations → `MigrationsMissingError` at construction), each module's `parts.checks` under `<module>:<name>`, and for the worker `listen` (`wakeups.connected`) and `workers` (no dead worker; liveness too); `create_app(info=ApiInfo(title=f"ledger-{role}", version=version), observability=Observability(health=..., registry=..., tracer_provider=tracing.provider), lifespan=self._lifespan, routers=...)`. The lifespan (an `AsyncExitStack`): register tracing globally; enter every `parts.resources` and `authentication.resources()`; for the worker, start supervised workers `wakeups` (`Wakeups(settings.listen_url).run`), `dispatcher` (`Dispatcher(database, schemas=<every schema, platform first>, signals, clock, registry).run(wakeups)`), `inbox <consumer>` per consumer (`InboxRunner(database=self._bound[schema], consumer, clock, tracing, registry).run(wakeups)`), `jobs <schema>` per schema with jobs (`JobRunner(...).run()`), `scheduler` (`Scheduler(platform=self._bound["platform"], schedules, holder=f"{socket.gethostname()}:{os.getpid()}", clock).run()`); on exit: `supervisor.stop(grace)`, `signals.aclose()`, `database.aclose()`, tracing flushed (the v1 `Service`'s stop order, without NATS).

`migrate()`: requires `settings.database_direct_url` (`DirectUrlMissingError`); for `platform` then each module in order: `migrations.upgrade(url)`; then, on one sync connection as `ledger_migrator`, per schema: `SET ROLE <schema>_owner`, the `relay_statements(schema)`, `sync_subscriptions(connection, schema, {decl.consumer: frozenset(decl.types) for decl in manifest.events.consumes}, now=clock())`, `RESET ROLE`, commit; then `SET ROLE platform_owner`, `bump_routing(connection)`, commit.

- [ ] **Step 5: Run the tests, then the gate**

Run: `uv run pytest packages/platform -n auto -q` → all pass. `uv run poe check` → green.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(platform): App - assemble modules and channels, run api and worker roles, migrate every schema

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 15: Identity cutover to the v2 chassis

Spec §3.10 (assembly), §4.1 (module layout), §4.5, §5.3, §6.3-§6.5 (schema `identity`, roles, squashed `r0001`), §9.5, §9.7, §16.1 (`services/identity` table: rows marked for this milestone); Decisions 3, 4, 13, 26; "Deferred" list (what stays as it is). After Tasks 14 and 7. Identity keeps its domain, use cases, routes and API shapes; it changes how it is composed, stored, announced and tested. The old kernel `TaxId` (DO) and `Permission` enum stay until Task 18.

**Files:**
- Create: `modules/identity/manifest.toml`, `modules/identity/src/ledger_identity/{_manifest,assembly}.py`, `modules/identity/src/ledger_identity/adapters/postgres/schema.py`
- Move: `modules/identity/src/ledger_identity/api/` → `adapters/http/` (`git mv`); `domain/events.py` → `events.py` (`git mv`); `modules/identity/Dockerfile` → `deploy/docker/ledger.Dockerfile` (`git mv`; rewritten in Task 20); `modules/identity/tests/test_*.py` → `tests/unit/` (domain, application with fakes) or `tests/integration/` (everything with a database or the app) or `tests/contract/` (`test_openapi.py`)
- Delete: `main.py`, `settings.py`, `openapi.py` (its writer becomes the snapshot test's update mode), `adapters/events.py`, `adapters/http/rest.py` (folded into `assembly.py`), `adapters/http/rpc.py`, `adapters/postgres/migrate.py`, `adapters/postgres/migrations/versions/r0002_tax_id_per_taxpayer.py`, `tests/test_rpc.py`
- Modify: `adapters/postgres/{tables,repositories,unit}.py`, `adapters/postgres/migrations/env.py`, `adapters/postgres/migrations/versions/r0001_identity.py` (squashed, rewritten), `adapters/permissions.py`, `adapters/http/{context,models,companies,branches,emission_points,memberships,organization}.py`, `application/{ports,commands}.py`, `domain/{company,membership}.py`, `modules/identity/pyproject.toml` (hand edit until Task 19: dependencies, the `ledger.modules` entry point, no scripts), `modules/identity/openapi.json` (regenerated), `packages/testing/src/ledger_testing/api.py` (`Caller.posting(key, mode="test")` sends `Ledger-Mode`), root `pyproject.toml` (the identity contracts become the v2 layers; coverage `source` path unchanged), `deploy/local/compose.yaml` (the `identity` and `identity-migrate` services are removed; `api` and `migrate` arrive in Task 20), `Tiltfile` (identity resources removed), `.github/workflows/ci.yml` (identity image and stack smoke steps removed until Task 20), `deploy/local/identity.env` (kept, unused until Task 20)

**Interfaces:**
- Consumes: `App`, `Module`, `ModuleContext`, `ModuleParts`, `Authentication` (Task 14); `BoundDatabase`, `UnitOfWork`, `SchemaMigrations`, `run_schema_migrations`, `chassis_schema_v1`, `RlsScope`, `policy` (Tasks 8-9); `Idempotency`, `attempt_of`, `idempotency_key`, `json_response`, `answer`, `REPLAYED_HEADER`, `ledger_mode`, `Page`, `problems`, `success`, `precondition_checked` (Task 13); `Event` (kernel); `manifest_module_text` (Task 7).
- Produces: `ledger_identity.assembly.MODULE: Module` (entry point `ledger.modules: identity`); `ledger_identity.events.{CompanyRegistered, CompanyUpdated, CompanyDeactivated, MembershipGranted, MembershipRevoked}` (types `identity.company.registered|updated|deactivated`, `identity.membership.granted|revoked`, version 1); `ledger_identity.adapters.postgres.schema.MIGRATIONS: SchemaMigrations` (schema `identity`); `ledger_testing.api.Caller.posting(key, mode="test")`.

- [ ] **Step 1: Manifest, entry point, generated manifest module**

`modules/identity/manifest.toml`:

```toml
[package]
name = "identity"
kind = "module"
summary = "Organizations, companies, their branches and emission points, memberships and roles."
owners = ["@balegre0"]

[dependencies]
packages = ["kernel", "platform"]
python = ["alembic>=1.20.0", "fastapi>=0.143.0", "pydantic>=2.14.1", "sqlalchemy>=2.1.4"]

[data]
schema = "identity"

[[ids]]
prefix = "org"
resource = "organization"

[[ids]]
prefix = "cmp"
resource = "company"

[[ids]]
prefix = "mbr"
resource = "membership"

[[permissions]]
code = "identity.companies.read"
summary = "Read the company, its branches and its emission points."
roles = ["owner", "admin", "accountant", "issuer", "viewer"]

[[permissions]]
code = "identity.companies.manage"
summary = "Change or deactivate the company; open branches and emission points."
roles = ["owner", "admin"]

[[permissions]]
code = "identity.memberships.read"
summary = "See who holds which role in the company."
roles = ["owner", "admin", "accountant"]

[[permissions]]
code = "identity.memberships.manage"
summary = "Grant and revoke every role but the owner's."
roles = ["owner", "admin"]

[[permissions]]
code = "identity.ownership.manage"
summary = "Grant and revoke the owner role."
roles = ["owner"]

[[events.publishes]]
type = "identity.company.registered"
version = 1

[[events.publishes]]
type = "identity.company.updated"
version = 1

[[events.publishes]]
type = "identity.company.deactivated"
version = 1

[[events.publishes]]
type = "identity.membership.granted"
version = 1

[[events.publishes]]
type = "identity.membership.revoked"
version = 1

[http]
mounted_in = ["api"]
prefixes = ["/v1/organization", "/v1/companies"]
```

Generate `src/ledger_identity/_manifest.py` with the snippet of Task 14 Step 1 (paths of identity). In `modules/identity/pyproject.toml`: dependencies become `ledger-kernel`, `ledger-platform`, `alembic`, `fastapi`, `pydantic`, `sqlalchemy` (no `ledger-contracts`, `grpcio`, `protobuf`, `prometheus-client`, `granian`); remove `[project.scripts]`; add

```toml
[project.entry-points."ledger.modules"]
identity = "ledger_identity.assembly:MODULE"
```

then `uv sync --all-packages` (entry points are installed metadata).

- [ ] **Step 2: Events as kernel dataclasses**

`git mv src/ledger_identity/domain/events.py src/ledger_identity/events.py`, then:

```python
"""What identity announces (§3.5): the facts other modules build on. Imported by the domain to emit them."""

from dataclasses import dataclass
from typing import ClassVar, final

from ledger_kernel.events import Event
lazy from ledger_kernel.access import SubjectId
lazy from ledger_kernel.countries.do import TaxId


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class CompanyRegistered(Event):
    """A company was registered (subject: the company; scope: its tenant, no mode)."""

    TYPE: ClassVar[str] = "identity.company.registered"
    VERSION: ClassVar[int] = 1
    tax_id: TaxId
    legal_name: str


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class CompanyUpdated(Event):
    """A company's details changed."""

    TYPE: ClassVar[str] = "identity.company.updated"
    VERSION: ClassVar[int] = 1
    tax_id: TaxId
    legal_name: str


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class CompanyDeactivated(Event):
    """A company was deactivated."""

    TYPE: ClassVar[str] = "identity.company.deactivated"
    VERSION: ClassVar[int] = 1


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class MembershipGranted(Event):
    """A principal was given a role in a company, or its role changed (subject: the membership)."""

    TYPE: ClassVar[str] = "identity.membership.granted"
    VERSION: ClassVar[int] = 1
    member: SubjectId
    role: str
    previous: str | None


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class MembershipRevoked(Event):
    """A principal's role in a company was taken away."""

    TYPE: ClassVar[str] = "identity.membership.revoked"
    VERSION: ClassVar[int] = 1
    member: SubjectId
    role: str


type IdentityEvent = CompanyRegistered | CompanyUpdated | CompanyDeactivated | MembershipGranted | MembershipRevoked
```

(Roles travel as their words: `events` sits below `domain` and may not import `Role`. `CompanyCreated` is renamed `CompanyRegistered`, its type the spec's.) In `domain/company.py` and `domain/membership.py` every event is built with `scope=self.tenant` (`Tenant(organization, id, None)`), `subject=<company id | membership id>`, `aggregate_version=<version after the change>`, the old `version` field dropped (it is `aggregate_version` now), `role=role.value`, `member=subject`. The domain tests assert the new fields.

`application/ports.py`: `Events.publish` becomes synchronous (`def publish(self, event: IdentityEvent) -> None`, abstract); `application/commands.py` drops the `await` of the six `unit.events.publish(...)` calls.

- [ ] **Step 3: Schema `identity`: tables and the squashed revision**

`adapters/postgres/tables.py`: `IdentityTable.metadata = MetaData(schema="identity")`; every `ForeignKey("x.y")` and `ForeignKeyConstraint([...], ["x.y", ...])` names `identity.x.y`; the rest is unchanged (the tax id columns change in Task 18). `adapters/postgres/schema.py`:

```python
"""identity's schema: its revisions, run by ``ledger-migrate`` as ``identity_owner``."""

from typing import Final

from ledger_identity.adapters.postgres.tables import IdentityTable
from ledger_platform.data.migrations import SchemaMigrations

MIGRATIONS: Final = SchemaMigrations(
    schema="identity", scripts="ledger_identity.adapters.postgres:migrations", metadata=IdentityTable.metadata
)
```

`adapters/postgres/migrations/env.py`:

```python
"""Alembic's entry point for identity's schema."""

from ledger_platform.data.migrations import run_schema_migrations

run_schema_migrations()
```

`git rm adapters/postgres/migrations/versions/r0002_tax_id_per_taxpayer.py` and rewrite `r0001_identity.py` (nothing is in production: v1's two revisions become one, §6.5):

```python
"""Revision 0001 of schema ``identity``: the chassis's tables, then identity's, under row-level security.

Every type, length, word and constraint is spelled out (a revision records what was done and must not move with
the models); the tests keep the models equal to it. The chassis's tables and the RLS policies come from their
versioned builders, whose output never changes (``chassis_schema_v1``, ``rls.policy``).
"""

lazy from collections.abc import Sequence
lazy from datetime import datetime

import sqlalchemy as sa
from alembic import op

from ledger_platform.data.chassis_schema import chassis_schema_v1
from ledger_platform.data.rls import RlsScope, policy

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

S = "identity"


def _moment(name: str) -> sa.Column[datetime]:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False)


def _of_company(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["organization_id", "company_id"],
        [f"{S}.companies.organization_id", f"{S}.companies.id"],
        name=f"fk_{table}_company",
    )


def upgrade() -> None:
    """The chassis's tables, identity's, their policies, and one active company per tax id everywhere."""
    chassis_schema_v1(op, S)
    op.create_table(
        "organizations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("provider_id", sa.String(128), nullable=False),
        _moment("created_at"),
        schema=S,
    )
    op.create_table(
        "companies",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey(f"{S}.organizations.id"), nullable=False),
        sa.Column("tax_id", sa.String(11), nullable=False),
        sa.Column("legal_name", sa.String(150), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        _moment("created_at"),
        _moment("updated_at"),
        sa.UniqueConstraint("organization_id", "tax_id", name="uq_companies_tax_id"),
        sa.UniqueConstraint("organization_id", "id", name="uq_companies_organization"),
        sa.CheckConstraint("status IN ('active', 'inactive')", name="ck_companies_status"),
        sa.CheckConstraint("version > 0", name="ck_companies_version"),
        schema=S,
    )
    op.create_index(
        "ux_companies_active_tax_id", "companies", ["tax_id"], unique=True,
        postgresql_where=sa.text("status = 'active'"), schema=S,
    )
    # memberships, branches, emission_points: v1's r0001 definitions, each with schema=S, foreign keys and
    # _of_company naming identity.companies / identity.branches, and the same indexes with schema=S.
    for table, scope, column in (
        ("organizations", RlsScope.ORGANIZATION, "id"),
        ("companies", RlsScope.ORGANIZATION, "organization_id"),
        ("memberships", RlsScope.ORGANIZATION, "organization_id"),
        ("branches", RlsScope.COMPANY, "organization_id"),
        ("emission_points", RlsScope.COMPANY, "organization_id"),
    ):
        for statement in policy(f"{S}.{table}", scope, organization_column=column):
            op.execute(statement)


def downgrade() -> None:
    """Expand-only (§6.5): a schema goes back only by restoring a backup."""
```

(Write the memberships, branches and emission points tables in full from v1's `r0001` — every column, constraint, index and the two composite foreign keys — with `schema=S`; the comment above marks where they go, not something to leave as a comment. Check v1's `r0002` for the exact index it created and keep its name and predicate.)

- [ ] **Step 4: The unit, the permissions, the HTTP context and the routes**

`adapters/postgres/unit.py`:

```python
"""identity's unit of work over a v2 chassis unit: its repositories, bound to the unit's tenant, and its events."""

from typing import ClassVar, final, override

from ledger_identity.adapters.postgres.repositories import (
    PgBranches, PgCompanies, PgEmissionPoints, PgMemberships, PgOrganizations,
)
from ledger_identity.application.ports import Events, IdentityUnit
from ledger_kernel.errors import LedgerError
lazy from ledger_identity.events import IdentityEvent
lazy from ledger_platform.data.unit_of_work import UnitOfWork


class UnscopedUnitError(LedgerError):
    """identity's state was to be read in a unit scoped to nobody: a defect of the caller."""

    code: ClassVar[str] = "unscoped_unit"

    def __init__(self) -> None:
        super().__init__("identity's state is read and changed in a unit scoped to an organization")


@final
class UnitEvents(Events):
    """identity's events, recorded in the unit's outbox (they commit with the change, rule 3)."""

    __slots__ = ("_unit",)

    def __init__(self, unit: UnitOfWork) -> None:
        self._unit = unit

    @override
    def publish(self, event: IdentityEvent) -> None:
        self._unit.publish(event)


def bind(unit: UnitOfWork) -> IdentityUnit:
    """The identity view of ``unit``: repositories bound to its organization (and company), its time, its events."""
    scope = unit.scope
    if scope is None:
        raise UnscopedUnitError
    organization, company, connection = scope.organization, scope.company, unit.connection
    return IdentityUnit(
        organizations=PgOrganizations(connection, organization),
        companies=PgCompanies(connection, organization),
        branches=PgBranches(connection, organization, company),
        emission_points=PgEmissionPoints(connection, organization, company),
        memberships=PgMemberships(connection, organization),
        events=UnitEvents(unit),
        now=unit.now,
    )
```

`adapters/permissions.py`: `MembershipPermissions(database: BoundDatabase)`; `permissions(...)` opens `self._database.unit(OrganizationScope(principal.organization, None))`.

`adapters/http/context.py`:

```python
@final
@dataclass(frozen=True, slots=True, kw_only=True)
class ApiContext:
    """What every router of identity shares, built once by the assembly."""

    database: BoundDatabase
    caller: Caller
    in_company: InCompany
    idempotency: Idempotency

    @classmethod
    def build(cls, *, database: BoundDatabase, authentication: Authentication) -> ApiContext:
        """The context of identity's routes: the caller from the chassis, its access from identity's memberships."""
        caller = authentication.caller
        permissions = MembershipPermissions(database)

        async def permission_source() -> PermissionSource:
            return permissions

        return cls(database=database, caller=caller, in_company=company_in_path(caller, permission_source), idempotency=Idempotency())
```

`problems`, `success`, `precondition_checked` leave this file for `ledger_platform.http.openapi` (routes call `precondition_checked(StaleVersionError)`); `models.py` takes `Problem` from `ledger_platform.http.problems` and replaces `CompanyPage`, `BranchPage`, `EmissionPointPage`, `MembershipPage` with `Page[CompanyView]` etc. (`ledger_platform.http.models.Page`).

Every route follows one pattern. `create_company`, in full:

```python
    @companies.post(
        "",
        status_code=HTTPStatus.CREATED,
        response_model=CompanyView,
        responses=problems(400, 401, 403, 409, 422) | success(HTTPStatus.CREATED, "Location", "ETag", REPLAYED_HEADER),
    )
    async def create_company(
        principal: Annotated[Principal, Depends(registrar)],
        mode: Annotated[Mode, Depends(ledger_mode)],
        body: NewCompany,
        request: Request,
        key: Annotated[str, Depends(idempotency_key)],
    ) -> Response:
        """Register a company of the caller's organization; the caller becomes its owner.

        The caller must hold the identity provider's project role ``ledger.organization.admin``. The request acts
        in ``Ledger-Mode``: its ``Idempotency-Key`` is kept per mode.
        """
        tax_id, legal_name = TaxId.parse(body.tax_id), parse_legal_name(body.legal_name)
        attempt = attempt_of(request, principal, key, body)
        async with context.database.unit(OrganizationScope(principal.organization, mode)) as transaction:

            async def register() -> StoredResponse:
                created = await commands.create_company(bind(transaction), principal, tax_id=tax_id, legal_name=legal_name)
                return stored(HTTPStatus.CREATED, created)

            outcome = await context.idempotency.once(transaction, attempt, register)
        return answer(outcome)
```

The rules for the others: a route with `Idempotency-Key` (`POST /v1/companies`, `POST .../deactivate`, `POST .../branches`, `POST .../emission_points`) takes `mode: Annotated[Mode, Depends(ledger_mode)]` and opens `OrganizationScope(principal.organization, mode)` or `Tenant(org, company, mode)`; every other route opens the same scope with `None` for the mode (`access.tenant` already has `mode=None`); `context.bind(transaction, current.outbox)` becomes `bind(transaction)`; the `runtime` dependency disappears. Routes import `bind` from `ledger_identity.adapters.postgres.unit`: both live under `adapters`, and the HTTP adapter may use the PostgreSQL one (identity's v1 `api` sat beside `adapters` only to keep that edge out).

`assembly.py`:

```python
"""identity's assembly (§3.10): what the apps mount of it."""

from typing import Final

from ledger_identity._manifest import MANIFEST
from ledger_identity.adapters.http import branches, companies, emission_points, memberships, organization
from ledger_identity.adapters.http.context import ApiContext
from ledger_identity.adapters.postgres.schema import MIGRATIONS
from ledger_platform.runtime.contexts import Module, ModuleContext, ModuleParts


def assemble(context: ModuleContext) -> ModuleParts:
    """identity's routers (mounted by ``api``) and its migrations (run by ``migrate``)."""
    api = ApiContext.build(database=context.database, authentication=context.auth)
    routers = tuple(
        router.router(api) for router in (organization, companies, branches, emission_points, memberships)
    )
    return ModuleParts(routers=routers, migrations=MIGRATIONS)


MODULE: Final = Module(manifest=MANIFEST, assemble=assemble)
```

Root `pyproject.toml`: the two identity contracts ("Identity's domain is the standard library and the kernel", "Identity is hexagonal", "Identity's use cases know ports...") become:

```toml
[[tool.importlinter.contracts]]
name = "identity layers"
type = "layers"
containers = ["ledger_identity"]
exhaustive = true
layers = ["assembly", "adapters", "application", "domain", "events", "_manifest"]

[[tool.importlinter.contracts]]
name = "identity domain, events and use cases are pure"
type = "forbidden"
source_modules = ["ledger_identity.domain", "ledger_identity.events", "ledger_identity.application"]
forbidden_modules = [
    "ledger_platform", "pydantic", "pydantic_settings", "sqlalchemy", "nats", "fastapi", "starlette", "structlog",
    "google", "grpc", "opentelemetry", "prometheus_client", "psycopg", "httpx", "joserfc", "cryptography",
    "alembic", "granian",
]
```

(`ledger_identity.application` may not import `ledger_platform` today: its ports are its own. `ruff`'s `runtime-evaluated-base-classes` keeps `ledger_identity.adapters.postgres.tables.IdentityTable`.)

- [ ] **Step 5: Tests on the new chassis**

`modules/identity/tests/conftest.py` (replace):

```python
"""identity's tests: a worker database migrated as production migrates it, and identity served by the api app."""

from typing import Final
lazy from collections.abc import AsyncGenerator

import httpx
import pytest
from pydantic import SecretStr

from ledger_identity.domain.organization import ORGANIZATION_ADMIN
from ledger_platform.config.settings import Settings
from ledger_platform.data.database import Database
from ledger_platform.runtime.app import App
from ledger_testing.api import Caller, created
from ledger_testing.database import TEST_LOGINS, app_url, bootstrap, migrator_url
from ledger_testing.issuer import LocalIssuer
from ledger_testing.postgres import Template, WorkerDatabase

COMPANY: Final = {"tax_id": "101850043", "legal_name": "Duarte"}


def pytest_ledger_template(template: Template) -> None:
    """The roles and schemas of every installed package, migrated by the migrate app itself."""
    bootstrap(template, ("platform", "identity"))
    url = template.url(role="ledger_migrator", password=TEST_LOGINS.migrator)
    App("migrate", settings=Settings(database_url=SecretStr(url), database_direct_url=SecretStr(url))).migrate()


@pytest.fixture
def settings(worker_database: WorkerDatabase, local_issuer: LocalIssuer) -> Settings:
    return Settings(
        environment="test",
        database_url=SecretStr(app_url(worker_database)),
        database_direct_url=SecretStr(migrator_url(worker_database)),
        token_issuer=local_issuer.issuer,
        token_audience=local_issuer.audience,
        token_jwks_url=local_issuer.keys_url,
    )


@pytest.fixture
async def database(worker_database: WorkerDatabase) -> AsyncGenerator[Database]:
    """A pool as ledger_app; every table of the identity schema is emptied after the test."""
    opened = Database.create(app_url(worker_database), pool_size=4)
    try:
        yield opened
    finally:
        await opened.aclose()
        worker_database.truncate(("identity",))


@pytest.fixture
async def client(database: Database, settings: Settings) -> AsyncGenerator[httpx.AsyncClient]:
    """identity, mounted by the api app on the worker's database."""
    del database
    app = App("api", settings=settings).asgi()
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://identity.test") as opened:
            yield opened


@pytest.fixture
def owner(local_issuer: LocalIssuer) -> Caller:
    """Subject ``2001`` of provider organization ``1001``, its administrator: it owns every company it registers."""
    return Caller(local_issuer, roles=(ORGANIZATION_ADMIN,))


@pytest.fixture
async def company(client: httpx.AsyncClient, owner: Caller) -> str:
    return await created(client, "/v1/companies", COMPANY, owner.posting("c-1"))
```

The test modules:

- `test_domain.py`, `test_application.py` → `tests/unit/` (application tests use their in-memory fakes: the `Events` fake's `publish` becomes synchronous; event assertions name the new classes and fields).
- `test_repositories.py`, `test_tables.py`, `test_migrations.py`, `test_events.py`, `test_*_api.py`, `test_readiness.py` → `tests/integration/`. Repositories run inside `BoundDatabase(database, schema="identity", source="identity", signals=<recording double>, publishes=<identity's types>).unit(scope)` (a fixture `identity_db` in the conftest). `test_tables.py` compares `IdentityTable.metadata` + `chassis_tables("identity")` with the migrated schema. `test_migrations.py` keeps what still applies: head reached by `App("migrate")` on a `fresh_database`, every tenant table forced under RLS with the policy names `<table>_select/_insert/_update/_delete`, the global unique active tax id (moved from v1's `r0002` test), a rerun changes nothing; downgrade tests are deleted (expand-only).
- `test_events.py` is rewritten: no NATS. Each change of the API writes its event to `identity.outbox` (read as admin) with `type`, `version 1`, `source "identity"`, scope columns (`mode` null), `subject`, `aggregate_version` and the payload; a failed request writes none; a revoked-twice membership writes one `identity.membership.revoked` (v1's test of that rule, kept).
- `test_rpc.py` is deleted with the gRPC service.
- API tests: `Caller.posting(key)` now also sends `Ledger-Mode: test` (`packages/testing/src/ledger_testing/api.py`: `def posting(self, key: str, mode: str = "test") -> dict[str, str]: return self.bearer() | {"Idempotency-Key": key, "Ledger-Mode": mode}`). Add to `test_companies_api.py`: a `POST /v1/companies` without `Ledger-Mode` is 400 `mode_required`; the same body under the same key, first in `test` then in `live`, is not replayed in `live` (the key is new there): the request runs and answers 409 `tax_id_taken` (one active company per tax id, whatever the mode), proving keys are kept per mode.
- `test_readiness.py`: `/readyz` names `schema:platform` and `schema:identity` and answers 200 on the migrated worker database.
- `tests/contract/test_openapi.py`: the document of `App("api", settings=<placeholders>).asgi().openapi()`, restricted to identity's prefixes and serialized as before (`json.dumps(..., indent=2, sort_keys=True, ensure_ascii=False) + "\n"`), equals `modules/identity/openapi.json`; with `LEDGER_UPDATE_SNAPSHOTS=1` in the environment the test writes the file instead and fails with "snapshot updated" (Task 19 moves the writing into `ledger-devtools sync` and removes the switch). Regenerate it now and review the diff: `Ledger-Mode` on the four `POST`s, 400 in their answers, `Page_*` schemas, no gRPC.

- [ ] **Step 6: Compose, Tilt and CI follow**

Remove the `identity-migrate` and `identity` services from `deploy/local/compose.yaml` and their resources from `Tiltfile` (both files are rewritten or deleted in Tasks 16 and 20); remove the CI steps "Identity's production image" and "Local stack starts healthy..." (Task 20 adds the v2 ones). `git mv modules/identity/Dockerfile deploy/docker/ledger.Dockerfile` (rewritten in Task 20; until then it is not built).

- [ ] **Step 7: Run everything, then the gate**

Run: `uv run pytest modules/identity packages -n auto -q` → all pass. `uv run poe check` → green.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "refactor(identity): run on the v2 chassis - schema identity, assembly, outbox events, moded idempotent POSTs

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 16: Delete the v1 chassis, NATS and the Python contracts; curate the public surface

Spec §5.2 ("Deleted outright"), §16.1 (`libs/contracts`, `libs/platform`, `libs/testing` tables), §2 rule 11, audit §3.1, §5, D7, D9, D29; Decision 21. After Task 15: nothing imports the v1 pieces any more.

**Files:**
- Delete (`git rm`): `packages/platform/src/ledger_platform/{service,migrations,schema,outbox,consumer,streams,jetstream,interruptions,identity_client,lifecycle,idempotency}.py`; their tests (`tests/integration/test_{service,migrations,schema,outbox,consumer,streams,identity_client,idempotency}.py`, `tests/unit/test_{lifecycle,interruptions}.py`); `packages/testing/src/ledger_testing/caches.py` and its test; `contracts/pyproject.toml`, `contracts/src/`, `contracts/tests/`, every file under `contracts/proto/` (keep `contracts/buf.yaml`; add `contracts/proto/.gitkeep`); `Tiltfile`; `deploy/local/identity.env` is kept for Task 20 (renamed there)
- Modify: the constant duplicates of audit D10, D11, D16 (validation-issue rendering shared by `observability/logs.py` and `http/problems.py` from one place in `observability`; one millisecond/ceiling constant in `data/database.py` used by `data/migrations.py`; the `postgresql+psycopg://` literal once, `config.settings.DATABASE_URL_SCHEME`, used by settings, database and `ledger_testing`); `data/database.py` (remove `Database.transaction`, v1 `UnitOfWork`, `_Callbacks`, `tenant_isolation*`, `organization_isolation*`, `PlatformTable`, `MissingTenantColumnError`, `missing_tables`, `ddl_name` if unused; keep `Database`, `DatabaseLimits`, `AbortedTransactionError`, `ClosedUnitOfWorkError`, `SET_SCOPE`, `scope_settings`, `is_committable`, `AnyTable`, `probing`, `ping`); `config/settings.py` (remove `ServiceSettings`, `ApiSettings`, the NATS validator and `grpc_port`; keep `Settings`, `LogFormat`, `TokenAlgorithm`); `config/options.py` (one `InvalidOptionError(*, component: str, option: str, requirement: str, value: object)`, its code `invalid_option`, replacing the per-module subclasses — audit D7 — every raiser passes its component name); `auth/access.py` (remove `CachedPermissions`, `_Kept`, `DEFAULT_TTL`, `DEFAULT_CAPACITY`, `DEFAULT_EMPTY_CAPACITY`, `InvalidAuthorizationOptionError`); `http/deps.py` (remove the v1 idempotency glue: `idempotency_key`, `attempt_of`, `json_response`, `answer`, `REPLAYED_HEADER` now live in `http/idempotency.py`; `company_in_header` reads `Ledger-Company`, §9.7); `observability/logs.py` (`configure_logging(settings, *, service)` only); `packages/testing/src/ledger_testing/{containers,api}.py` (NATS helpers removed; `Caller` → `TestPrincipal`, audit D9); every subpackage `__init__.py` gets its curated `__all__` (Step 3); `packages/platform/pyproject.toml` and `packages/testing/pyproject.toml` (drop `nats-py`, `ledger-contracts`); root `pyproject.toml` (members drop `"contracts"`; dev group drops `grpcio-tools`, `mypy-protobuf`, `types-protobuf`; poe `proto` deleted and `proto-lint` replaced (Step 2); the excludes and the mypy override for `contracts/src/ledger` removed; the `suppressions` exclude removed; the platform layers contract becomes the final one (Step 4); the contracts that name `ledger_contracts` lose it); `deploy/local/images.toml` (`nats`, `nats_alpine` removed); `deploy/local/compose.yaml` (the `nats` service and volume removed); `.github/workflows/ci.yml` (the proto regeneration step removed; the buf breaking step guarded by Step 2's rule)

**Interfaces:**
- Consumes: Tasks 8-15.
- Produces: the platform's public surface (Step 3); `ledger_testing.api.TestPrincipal` (was `Caller`); `InvalidOptionError(*, component, option, requirement, value)`.

- [ ] **Step 1: Delete, then let the checkers list what still points at the deleted code**

```bash
cd /c/Users/brian/Desktop/ledger
git rm -q packages/platform/src/ledger_platform/{service,migrations,schema,outbox,consumer,streams,jetstream,interruptions,identity_client,lifecycle,idempotency}.py
git rm -q packages/platform/tests/integration/test_{service,migrations,schema,outbox,consumer,streams,identity_client,idempotency}.py \
          packages/platform/tests/unit/test_{lifecycle,interruptions}.py
git rm -q packages/testing/src/ledger_testing/caches.py packages/testing/tests/test_caches.py 2>/dev/null || true
git rm -q -r contracts/src contracts/tests contracts/proto contracts/pyproject.toml Tiltfile
mkdir -p contracts/proto && : > contracts/proto/.gitkeep
git grep -nE "nats|jetstream|ledger_contracts|identity_client|CachedPermissions|ServiceSettings|ApiSettings|PlatformTable|\.transaction\(" -- '*.py' '*.toml' '*.yaml' '*.yml' ':!uv.lock' ':!docs'
```

Expected: the `git grep` lists only the files this task modifies; fix each as the **Files** block says, then `uv lock && uv sync --all-packages`.

- [ ] **Step 2: buf without protos (Decision 21)**

Replace poe `proto-lint` with:

```toml
[tool.poe.tasks.proto-lint]
help = "buf lint over contracts/ (skipped while no .proto exists: the signer milestone adds the first)"
imports = ["glob", "subprocess", "tomllib"]
assert = true
expr = '''
not glob.glob("contracts/proto/**/*.proto", recursive=True) or subprocess.run([
    "docker", "run", "--rm", "--volume", f"{__import__('os').getcwd()}/contracts:/workspace", "--workdir", "/workspace",
    tomllib.load(open("deploy/local/images.toml", "rb"))["images"]["buf"], "lint",
]).returncode == 0
'''
```

(`__import__` inside an expression is hard to read: add `"os"` to `imports` and write `os.getcwd()`; the image is read from `images.toml`, the one place it is pinned.) The CI step "Protobuf breaking changes against main" runs only when `contracts/proto` holds a `.proto` (`if: hashFiles('contracts/proto/**/*.proto') != ''`) and reads the image from `deploy/local/images.toml` (`python -c` with `tomllib`).

- [ ] **Step 3: Curated surfaces (§5.2: about 35 names an application author touches)**

Each subpackage's `__init__.py` re-exports its public names in `__all__` and nothing else; modules inside stay importable for tests, but production code outside the platform imports from the subpackage. The surfaces:

| Subpackage | `__all__` |
|---|---|
| `config` | `Settings`, `LogFormat`, `InvalidOptionError` |
| `observability` | `configure_logging`, `Tracing`, `Health`, `Check`, `Telemetry` |
| `data` | `Database`, `BoundDatabase`, `UnitOfWork`, `JobPolicy`, `SchemaMigrations`, `run_schema_migrations`, `chassis_schema_v1`, `RlsScope`, `policy`, `Actor`, `acting_as` |
| `events` | `Event`, `RawEvent`, `ConsumerSpec`, `EventBus`, `PostgresEventBus` |
| `jobs` | `JobSpec`, `Job`, `Done`, `RunAgain`, `Park` |
| `runtime` | `App`, `AppRole`, `Settings`, `Module`, `ModuleContext`, `ModuleParts`, `ChannelContext`, `ChannelParts`, `ChannelPack`, `Call`, `SystemCall`, `facade`, `UnitOpenError`, `Key`, `Supervisor` |
| `http` | `create_app`, `Problem`, `Page`, `BadRequestError`, `authenticated`, `company_in_path`, `company_in_header`, `allowed`, `holding`, `in_organization`, `ledger_mode`, `page_request`, `if_match`, `etag`, `idempotency_key`, `Idempotency`, `attempt_of`, `json_response`, `answer`, `problems`, `success`, `precondition_checked`, `Authentication` |
| `auth` | `TokenVerifier`, `TokenPolicy`, `PublishedKeys`, `HttpKeys`, `PermissionSource`, `CompanyAccess`, `PermissionDeniedError`, `NoCompanyAccessError` (the last two move to the kernel in Task 18) |
| `grpc` | `serve_grpc`, `call_unary`, `RemoteError` |

Identity switches its imports to the subpackages (`from ledger_platform.http import ...`). Engines (`Dispatcher`, `InboxRunner`, `JobRunner`, `Scheduler`, `Wakeups`) are the runtime's business: not exported.

- [ ] **Step 4: The final platform layers contract**

```toml
[[tool.importlinter.contracts]]
name = "Platform layers"
type = "layers"
containers = ["ledger_platform"]
exhaustive = true
layers = ["runtime", "http | grpc", "events | jobs", "auth", "data", "observability", "config", "_manifest"]
```

(Task 19 generates the same contract into `.importlinter`.) If a module violates it, move the code to the layer it belongs to; never add an exception.

- [ ] **Step 5: Gate**

Run: `uv run poe check` → green. `git grep -niE "\bnats\b|jetstream|tilt" -- ':!docs/superpowers/specs' ':!docs/adr' ':!uv.lock'` → nothing (the README is rewritten in Task 20: report its stale lines).

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "refactor: delete the v1 chassis, NATS and the Python contracts; curated platform surface

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 17: The `api`, `worker` and `migrate` apps (∥ Task 18)

Spec §3.3, §13 (console scripts), §4.6 contract 14; ADR 0001. After Task 16 (apps import the curated `ledger_platform.runtime`). Apps contain no business code: each builds an `App` from the environment and serves it.

**Files:**
- Create: `apps/{api,worker,migrate}/manifest.toml`, `apps/{api,worker,migrate}/pyproject.toml` (hand-written until Task 19), `apps/api/src/ledger_app_api/{__init__,main,py.typed}`, same for `worker` and `migrate`, `apps/*/tests/unit/test_main.py`, `apps/migrate/tests/integration/test_migrate.py`
- Modify: root `pyproject.toml` (members `"apps/*"`; ruff tests glob, basedpyright `include`, pytest `testpaths`, coverage `source_pkgs` gain the apps until Task 19 makes them per package; the "apps only compose" contract); `packages/platform/src/ledger_platform/config/settings.py` (`Settings.http_host`)

**Interfaces:**
- Consumes: `App`, `Settings` (`ledger_platform.runtime`).
- Produces: console scripts `ledger-api`, `ledger-worker`, `ledger-migrate`; `ledger_app_api.main.create_app() -> FastAPI` and `ledger_app_worker.main.create_app() -> FastAPI` (Granian factories); `ledger_app_<role>.main.main() -> int`.

- [ ] **Step 1: Write the failing tests**

`apps/api/tests/unit/test_main.py`:

```python
import pytest
from fastapi import FastAPI

from ledger_app_api import main


@pytest.fixture
def environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEDGER_DATABASE_URL", "postgresql+psycopg://ledger_app:x@127.0.0.1:1/ledger")
    monkeypatch.setenv("LEDGER_TOKEN_ISSUER", "https://issuer.invalid")
    monkeypatch.setenv("LEDGER_TOKEN_AUDIENCE", "ledger")
    monkeypatch.setenv("LEDGER_TOKEN_JWKS_URL", "https://issuer.invalid/keys")
    monkeypatch.setenv("LEDGER_HTTP_PORT", "8100")


def test_the_factory_builds_the_api_app_without_connecting(environment: None) -> None:
    app = main.create_app()
    assert isinstance(app, FastAPI)
    assert app.title == "ledger-api"
    assert "/v1/companies" in app.openapi()["paths"]


def test_main_serves_the_factory_with_granian_on_the_configured_port(environment: None, monkeypatch: pytest.MonkeyPatch) -> None:
    served: list[dict[str, object]] = []

    class FakeGranian:
        def __init__(self, target: str, **options: object) -> None:
            served.append({"target": target, **options})

        def serve(self) -> None:
            served.append({"served": True})

    monkeypatch.setattr(main, "Granian", FakeGranian)
    assert main.main() == 0
    assert served[0]["target"] == "ledger_app_api.main:create_app"
    assert (served[0]["factory"], served[0]["port"], served[0]["workers"]) == (True, 8100, 1)
    assert served[1] == {"served": True}
```

`apps/worker/tests/unit/test_main.py`: the same two tests for `ledger-worker` (title `ledger-worker`, no `/v1/...` path, `/readyz` present; token variables not required). `apps/migrate/tests/integration/test_migrate.py`: with `LEDGER_DATABASE_URL` and `LEDGER_DATABASE_DIRECT_URL` pointing at a `fresh_database` (roles bootstrapped by the template), `main.main()` returns 0 and `identity.alembic_version` is at head; without `LEDGER_DATABASE_DIRECT_URL`, `main.main()` raises `DirectUrlMissingError` (the process exits non-zero). The tests never bind a port.

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest apps -q -p no:randomly`
Expected: `ModuleNotFoundError: No module named 'ledger_app_api'` (after Step 3's packaging: failures for the missing functions).

- [ ] **Step 3: Package and implement**

`apps/api/manifest.toml`:

```toml
[package]
name = "api"
kind = "app"
summary = "The public API: every module's routers."
owners = ["@balegre0"]

[dependencies]
packages = ["platform"]
python = ["fastapi>=0.143.0", "granian[uvloop]>=2.8.4"]

[app]
script = "ledger-api"
role = "api"
```

(`worker`: summary "Fan-out, consumers, jobs and schedules.", role `worker`, same dependencies; `migrate`: summary "Every schema's migrations, subscriptions and routing, before a rollout.", role `migrate`, `python = []`.) Each `pyproject.toml` (hand-written now, generated in Task 19): name `ledger-app-<role>`, dependencies `ledger-platform` and the manifest's `python`, `[project.scripts] ledger-<role> = "ledger_app_<role>.main:main"`, `[tool.uv.sources] ledger-platform = { workspace = true }`, uv_build. Modules are found through entry points at run time, so an app does not depend on them; the image installs them (Task 20).

`apps/api/src/ledger_app_api/main.py`:

```python
"""``ledger-api``: the public API (§3.3). Granian serves the app ``create_app`` builds from ``LEDGER_*``."""

from importlib.metadata import version
from typing import Final

from fastapi import FastAPI
from granian import Granian
from granian.constants import Interfaces

from ledger_platform.runtime import App, Settings

VERSION: Final = version("ledger-app-api")
WORKER_KILL_TIMEOUT: Final = 30
"""Seconds Granian gives a worker to stop before it kills it: the platform's grace must be longer (§13)."""


def create_app() -> FastAPI:
    """Granian's factory: the api app of the environment's settings."""
    return App("api", settings=Settings(), version=VERSION).asgi()


def main() -> int:
    """Serve ``create_app`` on ``LEDGER_HTTP_HOST:LEDGER_HTTP_PORT``: one worker per replica (scale by replicas, §13)."""
    settings = Settings()
    Granian(
        "ledger_app_api.main:create_app",
        factory=True,
        interface=Interfaces.ASGI,
        address=settings.http_host,
        port=settings.http_port,
        workers=1,
        respawn_failed_workers=True,
        workers_kill_timeout=WORKER_KILL_TIMEOUT,
        log_access=False,
    ).serve()
    return 0
```

(Check Granian 2.8's constructor keyword names against its source in the environment — `uv run python -c "import inspect, granian; print(inspect.signature(granian.Granian))"` — and use those; the event loop is Granian's default (uvloop where installed, i.e. Linux). `Settings` gains `http_host: str = "127.0.0.1"` in this task (a host-side run binds loopback only); the image sets `LEDGER_HTTP_HOST=0.0.0.0` in its environment (Task 20), so no code spells an all-interfaces address — ruff's S104 would refuse it. The test asserts `served[0]["address"] == "127.0.0.1"`.)

`apps/worker/src/ledger_app_worker/main.py`: the same with role `worker` (its HTTP port serves the probes and metrics). `apps/migrate/src/ledger_app_migrate/main.py`:

```python
"""``ledger-migrate``: every schema's migrations, the relay's grants, the subscriptions, the routing version."""

from ledger_platform.runtime import App, Settings


def main() -> int:
    """Run once before each rollout (§3.3); a failure exits non-zero and the rollout stops."""
    App("migrate", settings=Settings()).migrate()
    return 0
```

The "Apps only compose" contract (until Task 19 generates it): `ledger_app_api`, `ledger_app_worker`, `ledger_app_migrate` may not import `ledger_identity.domain`, `.application`, `.adapters` (allow indirect imports); they import only `ledger_platform.runtime` and third-party serving code.

- [ ] **Step 4: Run, then the gate**

Run: `uv run pytest apps -n auto -q` → all pass. `uv run poe check` → green.

- [ ] **Step 5: Commit**

```bash
git add -A apps pyproject.toml uv.lock
git commit -m "feat(apps): ledger-api, ledger-worker and ledger-migrate composition roots

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 18: Vocabulary cutover — `countries/do`, qualified tax ids, open permissions (∥ Task 17)

Spec §5.1 (`access`, `tax_ids`), §7.3, §7.7, §8.2, §8.6.2 (tax-id schemes), §16.1 (`countries/do.py` Mv + R, `access.py` R, `domain/text.py` K*); ADRs 0005, 0006. After Task 16.

**Files:**
- Create: `countries/do/manifest.toml`, `countries/do/pyproject.toml` (hand-written until Task 19), `countries/do/src/ledger_country_do/{__init__,py.typed}`, `countries/do/src/ledger_country_do/regime/__init__.py`, `countries/do/tests/unit/test_regime.py`
- Move: `packages/kernel/src/ledger_kernel/countries/do.py` → `countries/do/src/ledger_country_do/regime/tax_ids.py` (`git mv`, then rewritten as scheme definitions); `packages/kernel/tests/unit/test_do.py` → `countries/do/tests/unit/test_tax_ids.py` (`git mv`, then adapted)
- Delete: `packages/kernel/src/ledger_kernel/countries/__init__.py`
- Modify: `packages/kernel/src/ledger_kernel/access.py` (`Permission` becomes a validated value type; `PermissionDeniedError` and `CompanyNotFoundError` move here: audit D1-D2), identity (`domain/{company,roles,membership,text}.py`, `events.py`, `adapters/postgres/{tables,repositories}.py`, `adapters/postgres/migrations/versions/r0001_identity.py`, `adapters/http/{companies,models}.py`, `adapters/http/context.py`, `assembly.py`), `packages/platform/src/ledger_platform/auth/access.py` and `http/deps.py` (the `Permission` type), their tests, `modules/identity/openapi.json`, root `pyproject.toml` (members `"countries/*"`, the country contracts until Task 19)

**Interfaces:**
- Consumes: `TaxId`, `TaxIdScheme`, `TaxIdCheck`, `TaxIdSchemeDef`, `RegimeDef`, `TaxIdSchemes`, `Jurisdiction`, `Currency` (Task 2); `Regimes` (Task 12; through `ModuleContext.regimes`).
- Produces:
  - `ledger_country_do.regime.REGIME: RegimeDef` (key `do`; DOP; `America/Santo_Domingo`; schemes `do.rnc` (9 digits, weights 7 9 8 6 5 4 3 2) and `do.cedula` (11 digits, Luhn), both `check_digit_enforced = False`; tax categories, document types, fields and parameters left empty until v2-M3 adds the e-CF regime data with its sources; one `Source` for the schemes); entry point `ledger.countries.regimes: do`.
  - `ledger_kernel.access.Permission = NewType("Permission", str)`, `parse_permission(text) -> Permission` (`<module>.<resource>.<action>`, action in `read|create|issue|void|manage|own`; `InvalidPermissionError`, code `invalid_permission`). The enum and its future-milestone members are gone.
  - `ledger_kernel.access.PermissionDeniedError(permission)` (`permission_denied`, FORBIDDEN) and `ledger_kernel.access.CompanyNotFoundError(company)` (`company_not_found`, NOT_FOUND): one class per code (audit D1, D2). The platform's `auth.access` raises them (its `PermissionDeniedError` and `NoCompanyAccessError` are deleted) and identity's domain raises them (its `ActorNotAllowedError` and `CompanyNotFoundError` are deleted); identity's `InvalidStateError` becomes the kernel's `InvalidValueError` (audit D4).
  - identity: `Company.tax_id: ledger_kernel.tax_ids.TaxId`; API `tax_id: {"scheme": "do.rnc", "value": "101850043"}` (request and response); `MAX_LEGAL_NAME = 300` (generic; e-CF's 150 is the channel's, v2-M3); role → permission table over the codes of identity's manifest.

- [ ] **Step 1: Write the failing tests**

`countries/do/tests/unit/test_tax_ids.py` — v1's `test_do.py`, adapted: the known valid RNCs and cédulas, the known checksum-failing-but-real ones, the hostile inputs (too long, non-ASCII digits, separators). Through the regime:

```python
import pytest

from ledger_country_do.regime import REGIME
from ledger_kernel.regimes import TaxIdSchemes
from ledger_kernel.tax_ids import InvalidTaxIdError, TaxId, TaxIdCheck, TaxIdScheme

SCHEMES = TaxIdSchemes(REGIME.tax_id_schemes)


@pytest.mark.parametrize(("scheme", "text", "value"), [("do.rnc", "1-01-85004-3", "101850043"), ("do.cedula", "001-0000001-1", "00100000011")])
def test_hyphens_and_spaces_are_ignored(scheme: str, text: str, value: str) -> None:
    parsed = SCHEMES.parse(scheme, text)
    assert parsed.tax_id == TaxId(TaxIdScheme(scheme), value)


def test_a_check_digit_mismatch_is_reported_not_refused() -> None:
    parsed = SCHEMES.parse("do.rnc", "101850042")
    assert parsed.check is TaxIdCheck.CHECKSUM_MISMATCH


@pytest.mark.parametrize(("scheme", "text"), [("do.rnc", "1018500433"), ("do.cedula", "101850043"), ("do.rnc", "١٠١٨٥٠٠٤٣"), ("do.rnc", "x" * 41)])
def test_a_wrong_length_or_non_ascii_digits_are_refused(scheme: str, text: str) -> None:
    with pytest.raises(InvalidTaxIdError):
        SCHEMES.parse(scheme, text)
```

(The first test checks normalization only: whether `00100000011` passes Luhn does not matter there, since `do.cedula` reports a mismatch without refusing. Keep every sample of v1's `test_do.py` — the valid ones assert `TaxIdCheck.VALID`, the real-but-failing ones `CHECKSUM_MISMATCH` — as further parametrized cases.)

`countries/do/tests/unit/test_regime.py`: `REGIME.key == "do"`, `REGIME.currency == Currency.of("DOP")`, `REGIME.jurisdiction == Jurisdiction(CountryCode("DO"))`; `Regimes.load(discover=installed)` finds `do` through the installed entry point.

`packages/kernel/tests/unit/test_access.py`: `parse_permission("identity.companies.read")` works; `"identity.read"`, `"Identity.companies.read"`, `"identity.companies.delete"`, `""` raise `InvalidPermissionError` (the message quotes an excerpt).

Identity: `test_companies_api.py` — every body sends `"tax_id": {"scheme": "do.rnc", "value": "101850043"}` and every answer carries the same object; an unknown scheme is 422 `tax_id_scheme_unknown`; a malformed value 422 `invalid_tax_id`; a checksum mismatch is accepted (`do.rnc` does not enforce it); two active companies with the same scheme and value in two organizations: the second is 409 `tax_id_taken`; the same value under `do.cedula` and `do.rnc` cannot collide (different schemes). `test_domain.py` and `test_application.py` build `TaxId(TaxIdScheme("do.rnc"), "101850043")`. Role tests assert the new codes (`identity.companies.read`, ...).

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest countries modules/identity packages/kernel -q -p no:randomly`
Expected: `ModuleNotFoundError: No module named 'ledger_country_do'` and identity's API tests failing on the body shape.

- [ ] **Step 3: Implement**

```bash
mkdir -p countries/do/src/ledger_country_do/regime countries/do/tests/unit
git mv packages/kernel/src/ledger_kernel/countries/do.py countries/do/src/ledger_country_do/regime/tax_ids.py
git mv packages/kernel/tests/unit/test_do.py countries/do/tests/unit/test_tax_ids.py
git rm -q packages/kernel/src/ledger_kernel/countries/__init__.py
```

`regime/tax_ids.py` keeps the algorithms (`_rnc_check_digit`, `_luhn_valid`, the ASCII-digit rule, the 20-character reading limit) and exposes, instead of the class:

```python
def _digits(text: str) -> str:
    """What DGII's identifiers are written as: digits, with hyphens and spaces ignored."""
    return text.replace("-", "").replace(" ", "")


def _checked(length: int, valid: Callable[[str], bool]) -> Callable[[str], TaxIdCheck]:
    def check(digits: str) -> TaxIdCheck:
        if len(digits) != length or not _is_ascii_digits(digits):
            return TaxIdCheck.INVALID_FORMAT
        return TaxIdCheck.VALID if valid(digits) else TaxIdCheck.CHECKSUM_MISMATCH
    return check


RNC: Final = TaxIdSchemeDef(
    key=TaxIdScheme("do.rnc"), country=CountryCode("DO"), labels={Locale("es"): "RNC", Locale("en"): "Taxpayer number (RNC)"},
    normalize=_digits, check=_checked(9, _rnc_valid), check_digit_enforced=False,
)
CEDULA: Final = TaxIdSchemeDef(
    key=TaxIdScheme("do.cedula"), country=CountryCode("DO"), labels={Locale("es"): "Cédula", Locale("en"): "National identity card"},
    normalize=_digits, check=_checked(11, _luhn_valid), check_digit_enforced=False,
)
```

`regime/__init__.py`:

```python
"""Regime ``do``: the Dominican Republic's tax law as data (§8.6.2). Imports the kernel only (contract 3).

This milestone holds the tax-id schemes; the ITBIS categories, the document types 31-47, the rounding points and
the dated parameters arrive with the e-CF channel (v2-M3), each with its source.
"""

from datetime import date
from decimal import Decimal
from typing import Final

from ledger_country_do.regime.tax_ids import CEDULA, RNC
from ledger_kernel.jurisdiction import CountryCode, Jurisdiction
from ledger_kernel.money import Currency, RoundingMode
from ledger_kernel.regimes import Locale, RegimeDef, RegimeKey, RoundingPoint, RoundingRule, Source

REGIME: Final = RegimeDef(
    key=RegimeKey("do"),
    jurisdiction=Jurisdiction(CountryCode("DO")),
    currency=Currency.of("DOP"),
    time_zone="America/Santo_Domingo",
    labels={Locale("es"): "República Dominicana", Locale("en"): "Dominican Republic"},
    tax_id_schemes=(RNC, CEDULA),
    tax_categories=(),
    document_types=(),
    rounding=RoundingRule(
        mode=RoundingMode.HALF_UP, points=frozenset({RoundingPoint.LINE}), places=2, line_tolerance=Decimal(1)
    ),
    fields=(),
    parameters={},
    sources=(
        Source(
            title="DGII: Registro Nacional de Contribuyentes (RNC) and cédula de identidad formats",
            url="https://dgii.gov.do",
            version="2026",
            retrieved_on=date(2026, 10, 13),
        ),
    ),
)
```

(The rounding rule here is a placeholder the calculator never meets this milestone: no category exists to compute with. v2-M3 replaces it with §8.6.2's rule — half up, two places at DGII's three points, unit prices to four places — and its source. Say so in the module's docstring, as above, and in the report.)

`countries/do/manifest.toml`:

```toml
[package]
name = "do"
kind = "country"
summary = "Dominican Republic: regime 'do' (the e-CF channel arrives in v2-M3)."
owners = ["@balegre0"]

[dependencies]
packages = ["kernel"]

[country]
iso = "DO"
name = "Dominican Republic"
currencies = ["DOP"]
time_zone = "America/Santo_Domingo"
authority = { name = "Dirección General de Impuestos Internos", short = "DGII", url = "https://dgii.gov.do" }

[[regimes]]
key = "do"
entry_point = "ledger_country_do.regime:REGIME"
```

`countries/do/pyproject.toml` (until Task 19): name `ledger-country-do`, dependency `ledger-kernel`, `[project.entry-points."ledger.countries.regimes"] do = "ledger_country_do.regime:REGIME"`. Root: members gain `"countries/*"`; the contract "Regimes are pure" (`ledger_country_do.regime` → everything but `ledger_kernel` and the standard library) and "Modules never import countries" (`ledger_identity` ↛ `ledger_country_do`) are added by hand until Task 19 generates them.

> **If the DGII spike has merged** (it creates `countries/do` with its own `regime/tax_ids.py`, provisional regime types and the channel library): follow "Coordination with the DGII spike" rule 3 instead of the `git mv` above — `git rm` the kernel module, keep the spike's `tax_ids.py` and regime data, replace its provisional types with the kernel's, and add only what is missing here (`REGIME` built from kernel types, the entry point, the manifest). Report the path taken.

`access.py`:

```python
Permission = NewType("Permission", str)
"""What a principal may do in a company: ``<module>.<resource>.<action>``, declared in a manifest (§7.7)."""

_PERMISSION: Final = re.compile(r"[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*\.(?:read|create|issue|void|manage|own)", re.ASCII)


class InvalidPermissionError(InvalidInputError):
    """Text that is not a permission code."""

    code: ClassVar[str] = "invalid_permission"

    def __init__(self, text: str) -> None:
        super().__init__(f"not a permission (<module>.<resource>.<action>): {excerpt(text)}")


def parse_permission(text: str) -> Permission:
    """Read a permission code."""
    if _PERMISSION.fullmatch(text) is None:
        raise InvalidPermissionError(text)
    return Permission(text)
```

Identity:

- `domain/roles.py`: constants from the manifest's codes (`COMPANIES_READ: Final = Permission("identity.companies.read")`, ... five), and the table: owner = all five; admin = all but `identity.ownership.manage`; accountant = companies read, memberships read; issuer = companies read; viewer = companies read (§7.7; the invoice permissions arrive with invoicing's manifest; the generated `ROLE_GRANTS` is v2-M2). `domain/membership.py`, `adapters/http/*.py` name these constants instead of the enum members.
- `domain/text.py`: `MAX_LEGAL_NAME: Final = 300` (generic, §7.1; a channel enforces its own).
- `domain/company.py` and `events.py`: `tax_id: TaxId` from `ledger_kernel.tax_ids`.
- `adapters/postgres/tables.py`: `tax_scheme: String(32)` and `tax_value: String(40)` replace `tax_id: String(11)`; `uq_companies_tax_id` becomes `(organization_id, tax_scheme, tax_value)`; `ux_companies_active_tax_id` becomes unique on `(tax_scheme, tax_value) WHERE status = 'active'`; `legal_name` becomes `String(300)`. `r0001_identity.py` is edited the same way (nothing outside this branch ever ran it: it is still this milestone's first revision). `repositories.py` maps the two columns to `TaxId`.
- `adapters/http/models.py`: `TaxIdBody(BaseModel)` with `scheme: str` and `value: str` (strict, extra forbidden); `NewCompany.tax_id: TaxIdBody`; `CompanyView.tax_id: TaxIdBody`.
- `adapters/http/companies.py`: the route parses with `context.tax_ids.parse(body.tax_id.scheme, body.tax_id.value).tax_id`, where `ApiContext.tax_ids` is `context.regimes.tax_id_schemes` handed in by `assembly.py` (`ApiContext.build(..., tax_ids=context.regimes.tax_id_schemes)`).
- `platform/auth/access.py` and `http/deps.py` name `Permission` (the type is the same name, now a `NewType`: only `frozenset(Permission)` uses of the enum need a change).
- Duplicated errors (audit D1, D2, D4): move `PermissionDeniedError` and `CompanyNotFoundError` into `ledger_kernel.access` with the messages the tests pin today (`git grep -n "permission_denied\|company_not_found"` lists the assertions: codes and messages stay); delete platform `auth.access.PermissionDeniedError`/`NoCompanyAccessError` and identity `domain/errors.py`'s `ActorNotAllowedError`/`CompanyNotFoundError`/`InvalidStateError`, and point every raiser and `pytest.raises` at the survivors. `Actor.require` in `domain/membership.py` raises the kernel's error, as `CompanyAccess.require` does.
- Regenerate `modules/identity/openapi.json` (`LEDGER_UPDATE_SNAPSHOTS=1 uv run pytest modules/identity/tests/contract`) and review: `tax_id` is an object; `legal_name` max length 300.

- [ ] **Step 4: Run, then the gate**

Run: `uv sync --all-packages && uv run pytest countries modules packages -n auto -q` → all pass. `uv run poe check` → green.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: countries/do regime, qualified tax ids in identity, open permissions declared by manifests

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 19: Manifests everywhere; generated pyprojects, contracts, roles and snapshots; per-package gate; vulture

Spec §2 rule 11, §4.2, §4.6, §4.7 (`sync`, `check`), §6.2 (generated roles), §14.2-§14.3; ADR 0009; Decisions 7, 19, 20, 22, 23. After Tasks 17 and 18. From this task on, no list of packages exists outside the manifests.

**Files:**
- Create: `packages/kernel/manifest.toml`, `packages/testing/manifest.toml`, `tools/devtools/manifest.toml`, `tools/devtools/src/ledger_devtools/{sql,openapi,images}.py`, `deploy/local/postgres/zitadel.sql`, `.importlinter` (generated)
- Generate (`ledger-devtools sync`): every package's `pyproject.toml`, `_manifest.py` (platform, identity, do), `.importlinter`, `deploy/local/postgres/init.sql`, `modules/identity/openapi.json`
- Modify: root `pyproject.toml` (Step 4), `.gitattributes` (generated files), `tools/devtools/src/ledger_devtools/{generate,checks,cli}.py` (new targets and the image check), `modules/identity/tests/contract/test_openapi.py` (no update switch: `sync` writes, the test compares)
- Delete: `tests/test_workspace.py` (and the empty `tests/`), `deploy/local/postgres/init.sql`'s hand-written content (it becomes generated; the Zitadel lines move to `zitadel.sql` with `git mv` first so they keep their history)

**Interfaces:**
- Consumes: `ledger_devtools` (Task 7), `cluster_statements`, `database_statements`, `SchemaRoles`, `Logins` (Task 8), `App` (Task 14).
- Produces: `ledger_devtools.sql.init_sql_text(workspace) -> str` and `LOCAL_LOGINS`; `ledger_devtools.openapi.openapi_text(package, document) -> str` and `api_document() -> dict[str, object]`; `ledger_devtools.images.image_problems(root, pins) -> tuple[Problem, ...]`; `targets(workspace)` covers them all.

- [ ] **Step 1: The remaining manifests**

```toml
# packages/kernel/manifest.toml
[package]
name = "kernel"
kind = "package"
summary = "Value types every package shares: ids, tenancy, money, tax ids, regimes, events. Standard library only."
owners = ["@balegre0"]
```

```toml
# packages/testing/manifest.toml
[package]
name = "testing"
kind = "package"
summary = "The shared test harness: a pytest plugin, PostgreSQL, a local OpenID provider, clocks. Dev-only."
owners = ["@balegre0"]

[dependencies]
packages = ["kernel", "platform"]
python = [
    "alembic>=1.20.0", "httpx>=0.28.1", "joserfc>=1.7.5", "psycopg[binary]>=3.3.6", "pydantic>=2.14.1",
    "pytest>=9.1.1", "sqlalchemy>=2.1.4", "structlog>=26.1.0", "testcontainers>=4.15.0",
]
```

```toml
# tools/devtools/manifest.toml
[package]
name = "devtools"
kind = "tool"
summary = "Generates and checks what the manifests imply."
owners = ["@balegre0"]
scripts = { "ledger-devtools" = "ledger_devtools.cli:main" }

[dependencies]
packages = ["kernel", "platform"]
python = ["pydantic>=2.14.1", "ruff>=0.17.0"]
```

If the DGII spike has merged ("Coordination with the DGII spike"), also: `countries/do/manifest.toml`'s `python` lists the third-party distributions its hand-written `pyproject.toml` declares (lxml, cryptography, httpx, ...), and `countries/do/testing/manifest.toml` is written with `kind = "package"`, `name = "do-testing"`, its dependencies and its console script (`ledger-dgii-spike`) from its hand-written `pyproject.toml`; after `sync`, compare each regenerated `pyproject.toml` with the hand-written one it replaces (nothing may be lost but the hand list entries of the root).

The testing package's `pytest11` entry point is not a manifest key: `pyproject_text` writes `[project.entry-points.pytest11] ledger = "ledger_testing.plugin"` for the package named `testing` (a fixed rule of the generator, tested), and devtools' `DEP002` ignore of `ruff` likewise for the package named `devtools`.

- [ ] **Step 2: Write the failing tests of the new targets**

`tools/devtools/tests/unit/test_sql.py`: for the fixture workspace (platform + identity), `init_sql_text` starts with `-- GENERATED by ledger-devtools sync`, holds `CREATE DATABASE ledger;`, a `\connect ledger` line, the cluster statements before it (relay, `ledger_app`, `ledger_migrator` with `LOCAL_LOGINS`' passwords, `platform_owner`, `platform_rw`, `identity_owner`, `identity_rw`, the grants) and the schema statements after it, each statement ending with `;`; with a channel `do-ecf-v1` in a country manifest, `do_ecf_owner`/`do_ecf_rw` appear too.

`tools/devtools/tests/unit/test_images.py`: a `compose.yaml` naming `postgres:18.6` and `ledger:local` and a Dockerfile `FROM <uv pin> AS build` and `FROM <distroless pin>` pass against an `images.toml` holding those pins; a compose naming `postgres:18.7` reports `deploy/local/compose.yaml: image 'postgres:18.7' is not pinned in deploy/local/images.toml`; `ledger:<tag>` (the image the repository builds) is never reported.

`tools/devtools/tests/unit/test_openapi.py`: given a document with paths `/v1/companies`, `/v1/companies/{company_id}`, `/v1/organization`, `/v1/invoices`, `openapi_text(identity, document)` keeps the identity paths only (by the manifest's prefixes), keeps only the component schemas those paths reference (transitively), and is `json.dumps(..., indent=2, sort_keys=True, ensure_ascii=False) + "\n"`.

Run: `uv run pytest tools/devtools -q -p no:randomly` → failures for the missing modules.

- [ ] **Step 3: Implement the targets and the image check**

`sql.py`:

```python
"""``deploy/local/postgres/init.sql`` (§6.2): the roles, the database and the schemas, from the manifests."""

from typing import Final
lazy from ledger_devtools.workspace import Workspace

from ledger_platform.data.roles import Logins, SchemaRoles, cluster_statements, database_statements

LOCAL_LOGINS: Final = Logins(app="ledger_app", migrator="ledger_migrator", signer="ledger_signer")
"""Passwords of the local stack only (its PostgreSQL listens on 127.0.0.1); production takes them from secrets."""
_HEADER: Final = (
    "-- GENERATED by ledger-devtools sync from manifest.toml: edit the manifests, then run `uv run ledger-devtools sync`.\n"
    "-- Runs once, as the administrator, when PostgreSQL first creates its data directory (compose.yaml).\n"
)


def init_sql_text(workspace: Workspace) -> str:
    """Roles (cluster), the ``ledger`` database, then every schema with its grants."""
    roles = tuple(SchemaRoles(schema=schema, signer=schema == "signer") for schema in workspace.schemas())
    cluster = "".join(f"{statement};\n" for statement in cluster_statements(roles, LOCAL_LOGINS))
    schemas = "".join(f"{statement};\n" for statement in database_statements(roles))
    return f"{_HEADER}\n{cluster}\nCREATE DATABASE ledger;\n\\connect ledger\n\n{schemas}"
```

`openapi.py`: `api_document()` builds `App("api", settings=Settings(database_url=SecretStr("postgresql+psycopg://openapi@localhost/openapi"), token_issuer="https://issuer.invalid", token_audience="ledger", token_jwks_url="https://issuer.invalid/keys"), discover=installed).asgi().openapi()` (nothing connects); `openapi_text(package, document)` filters as the test says. `generate.targets` calls `api_document()` once when any module mounts HTTP in `api` and adds `<module dir>/openapi.json` for each. `images.py`: reads `deploy/local/images.toml`, then every `image:` value of `deploy/local/compose.yaml` and every `FROM <image>` of `deploy/docker/*.Dockerfile` (regular expressions on lines; `ARG`-based images are not used), reporting each that is neither a pin nor `ledger:<tag>`; `check` adds these problems.

`git mv deploy/local/postgres/init.sql deploy/local/postgres/zitadel.sql`, keep only its Zitadel role and database lines (and a header saying it is hand-written and why: Zitadel is not a manifest package); `sync` then writes the new `init.sql`.

- [ ] **Step 4: Switch the repository to the generated files and the per-package gate**

```bash
uv run ledger-devtools sync        # expected: prints every pyproject.toml, the _manifest.py files, .importlinter, init.sql, openapi.json
git diff --stat                    # review: every pyproject's content follows from its manifest; no dependency lost
uv lock && uv sync --all-packages
uv run ledger-devtools check       # expected: no output, exit 0
```

Root `pyproject.toml`:

- `[project]` keeps no `dependencies`; `[tool.uv.workspace] members = ["packages/*", "modules/*", "countries/*", "countries/*/testing", "apps/*", "tools/*"]`; `[tool.uv.sources]` names only the dev-group members (`ledger-testing`, `ledger-devtools`).
- Delete `[tool.importlinter]` and its contracts (`.importlinter` is generated) and `[tool.coverage]` (each package's own).
- `[tool.ruff.lint.per-file-ignores]` tests glob: `"*/*/tests/**"` and `"countries/*/testing/tests/**"`; `known-first-party`: the import names of every package (the generator cannot write the root file: list them, and `check` compares this list with the workspace — add that rule to `checks.py`, message `pyproject.toml: known-first-party is not the workspace's import packages`). `runtime-evaluated-base-classes`: `pydantic.BaseModel`, `pydantic_settings.BaseSettings`, `sqlalchemy.orm.DeclarativeBase`, `ledger_identity.adapters.postgres.tables.IdentityTable`.
- `[tool.basedpyright] include = ["packages", "modules", "countries", "apps", "tools"]`.
- `[tool.pytest]`: no `testpaths`, no `--cov` in `addopts` (the loop adds it per package).
- `[tool.vulture]`: `min_confidence = 60`; `ignore_decorators` = the framework registrations found in `src` (`"@*.get"`, `"@*.post"`, `"@*.put"`, `"@*.patch"`, `"@*.delete"`, `"@*.exception_handler"`, `"@field_validator"`, `"@model_validator"`, `"@pytest.fixture"`, `"@pytest.hookimpl"`, `"@pytest.hookspec"`, `"@override"`, `"@asynccontextmanager"` where a framework enters it); `ignore_names` = framework-defined names only (`"model_config"`, `"pytest_*"`, `"__init_subclass__"`). A vulture finding is either dead code (delete it) or read by a framework (add the *pattern* above, with its reason in a comment); a specific symbol's name is never listed.
- poe tasks (replacing every per-package task):

```toml
[tool.poe.tasks.mypy]
help = "mypy --strict, one package at a time (each with its own tests)"
imports = ["glob", "os", "subprocess"]
assert = true
expr = '''
all([subprocess.run(["mypy", directory]).returncode == 0 for directory in sorted(
    os.path.dirname(path) for path in glob.glob("*/*/manifest.toml") + glob.glob("countries/*/testing/manifest.toml")
)])
'''

[tool.poe.tasks.deps]
help = "deptry, one package at a time"
imports = ["glob", "os", "subprocess"]
assert = true
expr = '''
all([subprocess.run(["deptry", f"{directory}/src", "--config", f"{directory}/pyproject.toml"]).returncode == 0 for directory in sorted(
    os.path.dirname(path) for path in glob.glob("*/*/manifest.toml") + glob.glob("countries/*/testing/manifest.toml")
)])
'''

[tool.poe.tasks.test]
help = "Each package's tests in parallel workers, with its own 100 % branch coverage"
imports = ["glob", "os", "subprocess"]
assert = true
expr = '''
all([subprocess.run([
    "pytest", directory, "-c", "pyproject.toml", "-n", "auto", "--cov", f"--cov-config={directory}/pyproject.toml",
    "--cov-report=term-missing:skip-covered",
]).returncode == 0 for directory in sorted(
    os.path.dirname(path) for path in glob.glob("*/*/manifest.toml") + glob.glob("countries/*/testing/manifest.toml")
)])
'''
```

plus `devtools = { cmd = "ledger-devtools check", help = "Manifests valid, generated files current" }`, `dead = { cmd = "vulture packages/*/src modules/*/src countries/*/src apps/*/src tools/*/src", help = "Dead code (vulture)" }`, and `check = { sequence = ["format-check", "lint", "suppressions", "types", "arch", "deps", "spell", "proto-lint", "devtools", "dead", "test"] }`. (`all([...])` builds the whole list first, so every package runs and reports even after one fails.)

`.gitattributes` gains `linguist-generated=true` for `packages/*/pyproject.toml`, `modules/*/pyproject.toml`, `countries/*/pyproject.toml`, `apps/*/pyproject.toml`, `tools/*/pyproject.toml`, `**/_manifest.py`, `**/openapi.json`, `.importlinter`, `deploy/local/postgres/init.sql`.

`git rm tests/test_workspace.py` (the lists it guarded no longer exist).

- [ ] **Step 5: Run the whole gate; prove `check` catches staleness**

```bash
uv run poe check                                                      # expected: green, every package at 100 %
echo "# drift" >> modules/identity/pyproject.toml && uv run ledger-devtools check; echo "exit $?"
git checkout -- modules/identity/pyproject.toml                       # expected above: the file named stale, exit 1
```

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "build: every list generated from manifests; per-package gate, devtools check and vulture

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 20: One image, compose without NATS or Tilt, CI, README

Spec §4.1 (`deploy/`, README, AGENTS.md), §13, §14.4, §16.1 (root, CI, deploy rows); Decisions 20, 25. After Task 19. Never bind or touch host port 8000.

**Files:**
- Modify: `deploy/docker/ledger.Dockerfile` (rewritten), `deploy/local/compose.yaml` (rewritten), `.github/workflows/ci.yml` (rewritten), `README.md` (rewritten), `_typos.toml` (the comment that cites "the plan" goes), `.dockerignore`
- Move: `deploy/local/identity.env` → `deploy/local/ledger.env` (`git mv`; variable names edited, values never printed)
- Create: `AGENTS.md`

**Interfaces:**
- Consumes: the console scripts of Task 17; `init.sql` and `zitadel.sql` (Task 19); `images.toml`.
- Produces: image `ledger:local`; compose services `postgres`, `migrate`, `api` (host `127.0.0.1:${LEDGER_API_PORT:-8100}`), `worker` (host `127.0.0.1:${LEDGER_WORKER_PORT:-8101}`), `zitadel` (profile).

- [ ] **Step 1: The image**

`deploy/docker/ledger.Dockerfile`: v1's build stage kept as it was (uv's pinned image by digest, the uv-managed CPython 3.15.0 and its trimming, `UV_*` variables), then:

```dockerfile
WORKDIR /src
COPY . .
# Every production distribution, found by directory (no list to keep): modules, countries and apps. Dev-only
# distributions (ledger-testing, ledger-devtools, *-testing) are never installed. --inexact keeps what each
# earlier sync installed.
RUN --mount=type=cache,target=/root/.cache/uv \
    set -eu; \
    for d in modules/*/; do uv sync --locked --no-dev --no-editable --inexact --package "ledger-$(basename "$d")"; done; \
    for d in countries/*/; do uv sync --locked --no-dev --no-editable --inexact --package "ledger-country-$(basename "$d")"; done; \
    for d in apps/*/; do uv sync --locked --no-dev --no-editable --inexact --package "ledger-app-$(basename "$d")"; done

FROM gcr.io/distroless/cc-debian13:nonroot@sha256:e792ab3d241a468a4fd7519ddbbebe66b49b5f365771716ea688ad40b6c6f1c2
LABEL org.opencontainers.image.title="ledger" \
      org.opencontainers.image.description="The ledger platform: api, worker and migrate entrypoints" \
      org.opencontainers.image.source="https://github.com/balegre0/ledger"
COPY --from=build /python /python
COPY --from=build /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 LEDGER_HTTP_HOST=0.0.0.0
USER nonroot
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=3 \
    CMD ["/app/.venv/bin/ledger-probe", "http://127.0.0.1:8000/readyz"]
CMD ["/app/.venv/bin/ledger-api"]
```

`.dockerignore`: keep v1's entries (tests, docs and deploy excluded) and add `tools`. Build: `docker build -f deploy/docker/ledger.Dockerfile -t ledger:local .` → succeeds; `docker run --rm --entrypoint /app/.venv/bin/python ledger:local -c "import ledger_identity, ledger_country_do, ledger_app_api; import importlib.util as u; print(u.find_spec('ledger_testing'))"` prints `None`.

- [ ] **Step 2: The local stack**

`git mv deploy/local/identity.env deploy/local/ledger.env`; then, by name only (never print the file):

```bash
sed -i -E 's#^LEDGER_DATABASE_URL=.*#LEDGER_DATABASE_URL=postgresql+psycopg://ledger_app:ledger_app@postgres:5432/ledger#' deploy/local/ledger.env
sed -i -E '/^LEDGER_(SERVICE_NAME|NATS_URL|GRPC_PORT)=/d' deploy/local/ledger.env
grep -c '^LEDGER_' deploy/local/ledger.env     # a count only
```

`deploy/local/compose.yaml` (keep the Zitadel service of v1 as it is; its image must equal `images.toml`'s pin):

```yaml
# The local stack (§13): PostgreSQL with the generated roles, the migrate job, the api and the worker from one image,
# and Zitadel behind the profile `zitadel`. Every port is bound to 127.0.0.1. The api is published on
# LEDGER_API_PORT (8100 by default: another project of this machine uses 8000), the worker's probes on
# LEDGER_WORKER_PORT (8101). Image pins equal deploy/local/images.toml (`ledger-devtools check`).
name: ledger

x-ledger: &ledger
  image: ledger:local
  build: {context: ../.., dockerfile: deploy/docker/ledger.Dockerfile}
  env_file:
    - ledger.env
    - path: zitadel/.local/ledger.env
      required: false
  read_only: true
  tmpfs: [/tmp]
  cap_drop: [ALL]
  security_opt: ["no-new-privileges:true"]

services:
  postgres:
    image: postgres:18.6
    environment: {POSTGRES_USER: postgres, POSTGRES_PASSWORD: postgres}
    ports: ["127.0.0.1:${LEDGER_POSTGRES_PORT:-5432}:5432"]
    volumes:
      - postgres:/var/lib/postgresql
      - ./postgres/init.sql:/docker-entrypoint-initdb.d/10-ledger.sql:ro
      - ./postgres/zitadel.sql:/docker-entrypoint-initdb.d/20-zitadel.sql:ro
    healthcheck:
      test: ["CMD-SHELL", "PGPASSWORD=ledger_app psql -h 127.0.0.1 -U ledger_app -d ledger -tAc 'SELECT 1'"]
      interval: 5s
      timeout: 3s
      retries: 10

  migrate:
    <<: *ledger
    command: ["/app/.venv/bin/ledger-migrate"]
    environment:
      LEDGER_DATABASE_DIRECT_URL: postgresql+psycopg://ledger_migrator:ledger_migrator@postgres:5432/ledger
    restart: "no"
    depends_on:
      postgres: {condition: service_healthy}

  api:
    <<: *ledger
    command: ["/app/.venv/bin/ledger-api"]
    ports: ["127.0.0.1:${LEDGER_API_PORT:-8100}:8000"]
    stop_grace_period: 40s
    depends_on:
      migrate: {condition: service_completed_successfully}

  worker:
    <<: *ledger
    command: ["/app/.venv/bin/ledger-worker"]
    environment:
      LEDGER_DATABASE_DIRECT_URL: postgresql+psycopg://ledger_app:ledger_app@postgres:5432/ledger
    ports: ["127.0.0.1:${LEDGER_WORKER_PORT:-8101}:8000"]
    stop_grace_period: 40s
    depends_on:
      migrate: {condition: service_completed_successfully}

  zitadel:
    # v1's service, unchanged (profile `zitadel`); see deploy/local/zitadel/README.md
    ...

volumes:
  postgres: {}
  zitadel-bootstrap: {}
```

(Copy v1's `zitadel` service block verbatim in place of `...`, and update `deploy/local/zitadel/bootstrap.sh` and its README where they write `zitadel/.local/identity.env`: they now write `zitadel/.local/ledger.env`.)

```bash
docker compose -f deploy/local/compose.yaml config --quiet && docker compose -f deploy/local/compose.yaml --profile zitadel config --quiet
```

- [ ] **Step 3: CI**

`.github/workflows/ci.yml`: one job (`check`, 30 min): checkout (`fetch-depth: 0`), setup-uv (cache), `uv sync --locked --all-packages`, `uv run poe check`, the guarded buf breaking step (Task 16), both `compose config --quiet`, buildx, `docker/build-push-action` of `deploy/docker/ledger.Dockerfile` tagged `ledger:local` (cache scope `ledger`), and the smoke test:

```bash
compose='docker compose -p ledger-ci -f deploy/local/compose.yaml'
trap '$compose down -v' EXIT
$compose up -d --wait --no-build || { $compose logs; exit 1; }
api="http://127.0.0.1:${LEDGER_API_PORT:-8100}"
worker="http://127.0.0.1:${LEDGER_WORKER_PORT:-8101}"
curl --fail --silent --show-error "$api/readyz"
curl --fail --silent --show-error "$worker/readyz"
test "$(curl --silent --output /dev/null --write-out '%{http_code}' "$api/v1/companies")" = 401
test "$(docker image inspect ledger:local --format '{{.Config.User}}')" = nonroot
$compose run --rm migrate
```

- [ ] **Step 4: README and AGENTS.md**

`README.md` (about 80 lines; nothing a module docstring or the spec already says): what the platform is (two sentences, link to the spec), the layout (the six top directories, one line each), develop (`uv sync --all-packages`, `uv run poe check`, Docker needed, `uv run ledger-devtools sync` after editing a manifest, generated files are never edited), run locally (`docker compose -f deploy/local/compose.yaml up -d --build --wait`, ports 8100 and 8101, the Zitadel profile and its README), where decisions live (`docs/adr/`), runbooks (`docs/runbooks/`).

`AGENTS.md` (short): read the spec and the ADRs first; the gate and the zero-suppression rule; manifests are the source of truth and generated files carry a header; one task, one commit, never push; never kill processes by image name; never print `.env` files; never use host port 8000.

- [ ] **Step 5: Gate and commit**

Run: `uv run poe check` → green (`ledger-devtools check` compares the compose and Dockerfile images with `images.toml`).

```bash
git add -A
git commit -m "build: one image for every app, compose without NATS or Tilt, one CI job, short README

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 21: Final verification

Spec §14.2 (no time-based proofs), §14.3, §16.3 (v2-M1a "done when"). After Task 20. Verification first; a fix found here is its own commit (`fix:` or `test:`), each with the gate green.

- [ ] **Step 1: The gate from a clean checkout**

```bash
git status --short                       # expected: clean
uv sync --locked --all-packages
time uv run poe check                    # expected: green; record the duration (target < 10 min, §12)
```

- [ ] **Step 2: No sleep proves an absence; no `sleep(0)` orders tasks**

```bash
git grep -nE "asyncio\.sleep\(" -- '*/*/tests/**' 'countries/*/testing/tests/**'
git grep -nE "time\.sleep\(" -- '*/*/tests/**'
```

Every hit must be a bounded presence wait inside `eventually`, or a deliberate stall a test needs (a slow server), named as such. A `sleep` followed by an assertion that something did *not* happen is rewritten to drain first (`run_until_idle()`, `run_due(now=...)`, a sentinel NOTIFY) and assert after; a `sleep(0)` used to let another task run (v1's `test_health.py`, `test_supervision.py`) is rewritten with an `asyncio.Event` the other task sets. Commit as `test: prove absence by draining, order tasks with events`.

- [ ] **Step 3: Zero suppressions, zero dead code, small surfaces**

```bash
uv run poe suppressions && uv run poe dead
git grep -n "ignore_imports" -- .importlinter pyproject.toml          # expected: nothing
uv run python - <<'EOF'
import importlib
for name in ("config", "observability", "data", "events", "jobs", "runtime", "http", "auth", "grpc"):
    module = importlib.import_module(f"ledger_platform.{name}")
    print(name, len(module.__all__))
EOF
```

Expected: no suppression, no vulture finding, no import-linter exception; the surfaces of Task 16's table.

- [ ] **Step 4: The local stack (host ports 8100/8101; never 8000)**

```bash
compose='docker compose -p ledger-v2m1 -f deploy/local/compose.yaml'
$compose up -d --build --wait
curl --fail --silent --show-error http://127.0.0.1:${LEDGER_API_PORT:-8100}/readyz
curl --fail --silent --show-error http://127.0.0.1:${LEDGER_WORKER_PORT:-8101}/readyz
test "$(curl --silent --output /dev/null --write-out '%{http_code}' http://127.0.0.1:${LEDGER_API_PORT:-8100}/v1/companies)" = 401
$compose run --rm migrate             # expected: exits 0, changes only the routing version
$compose down -v                      # this project only; nothing else is stopped
```

With the `zitadel` profile (optional, as v1's README describes): a token from `deploy/local/zitadel/token.sh` registers a company through `POST /v1/companies` with `Ledger-Mode: test`, and the worker's `/metrics` shows the fan-out's counters moving for `identity.company.registered` (unrouted: no module consumes it yet — expected, and visible as `ledger_outbox_unrouted_total{schema="identity"}`).

- [ ] **Step 5: The milestone's "done when" (§16.3, v2-M1a), item by item**

Write in the report, for each item, the file or test that proves it: new tree; `ledger-devtools sync/check` (`gate` deferred, Decision 22); kernel v2; platform `config`, `observability`, `runtime` (App, contexts, parts, facade guard, registries), `data` (units with `SET LOCAL ROLE`, RLS builder with `NULLIF`, `chassis_schema_v1`, migrations), `events` (outbox, subscriptions, fan-out, inbox runner), `jobs` (enqueue, claim with fencing, schedules on lease rows), `http` basics; `api`/`worker`/`migrate` apps; testing plugin and xdist; one image; compose without NATS. Then the Decisions and the Deferred list of this plan, unchanged or with what the execution changed, for the owner.
