# Milestone 2: Identity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The identity service (`services/identity`, package `ledger_identity`), the first real service on the chassis: organizations, companies, branches, emission points, memberships and roles behind a public `/v1` REST API and a gRPC `IdentityQueryService`, publishing its facts as events, with the platform additions every later service needs (bearer-token verification, the per-request authorization context, organization-scoped row-level security, idempotency keys, cursor pagination, `ETag`/`If-Match`, Alembic migrations), shipped as a production image and runnable locally next to a real Zitadel.

**Architecture:** Platform first: `ledger_platform` gains `authentication` (JWT/JWKS), `authorization` (permissions, cached), `idempotency`, `routes` (the FastAPI dependencies routes declare), `grpc_client` and `identity_client` (the platform's own client of identity), an organization scope for transactions and `organization_isolation`. A new dev-only workspace package `ledger_testing` holds what every package's tests share (containers, a local OpenID provider, the logging baseline). The service is hexagonal (§6): `domain` (stdlib + kernel) ← `application` (use cases over ports) ← `adapters` (PostgreSQL, events, permissions) and `api` (REST, gRPC), wired by `main` into one chassis `Service`. Organization-owned tables are isolated by organization, company-owned ones by organization and company; every change commits with its event in the outbox.

**Tech Stack:** Python 3.15, uv 0.13 workspace, FastAPI 0.143, pydantic 2.14, SQLAlchemy 2.1 (psycopg 3.3), Alembic 1.20, joserfc 1.7.5 (+ cryptography 50.0.2), httpx 0.28.1, nats-py 2.16, grpcio 1.84 + mypy-protobuf 5.1, Granian 2.8.4 with uvloop 0.23.0, PostgreSQL 18.6, NATS 2.12.15, Zitadel v4.19.2, distroless `cc-debian13`, Tilt, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-10-11-ledger-platform-design.md` (milestone 2 of §16: "Zitadel integration, organizations, companies, roles, RLS end to end"; §3, §4, §6-§11, §13-§16).

## Global Constraints

- Python `>=3.15`; every package's `pyproject.toml` says `requires-python = ">=3.15"`.
- Code, comments, docs and messages in English.
- Zero `# type: ignore`, `# pyright: ignore`, `# noqa`, `# pragma: no cover`, and no tool directive comment (`# ruff:`, `# mypy:`, `# flake8:`, `# isort:`, `# basedpyright:`): the `suppressions` gate step fails on any. Generated protobuf code (`libs/contracts/src/ledger/`) is excluded by configuration, not by comments.
- `uv run poe check` passes after every task: ruff format + ruff (`select = ["ALL"]`), basedpyright (`typeCheckingMode = "all"`, warnings fail), mypy `--strict` per package, import-linter, deptry per package, typos, `buf lint`, pytest with **100 % branch coverage** and warnings as errors.
- Raise errors with their message built inside the error class (`raise InvalidTokenError(problem)`), never `raise X("literal")` (ruff TRY003/EM101).
- Type-only imports use Python 3.15 `lazy from ... import ...`. A lazy import resolves on first use, so a route signature may name a lazily imported type (FastAPI reads it at runtime, as M1's `test_service.py` does); the field types of a pydantic model are ordinary imports (ruff treats `BaseModel` annotations as runtime).
- Never echo a whole untrusted input in an error message: use `excerpt()`. Never log, return or put in a span an access token, its claims, a request body or a response body.
- Docker must be running: tests use real PostgreSQL `postgres:18.6` and NATS `nats:2.12.15` through testcontainers; no test needs Zitadel.
- On Windows, psycopg's async driver needs a selector event loop: tests get one from the root `conftest.py`; services run in Linux containers.
- Dependency floors introduced here (verified on PyPI on 2026-10-12 to install on CPython 3.15: pure-Python, `abi3` or `cp315` wheels): `joserfc>=1.7.5`, `cryptography>=50.0.2`, `httpx>=0.28.1`, `alembic>=1.20.0`, `granian[uvloop]>=2.8.4` (uvloop 0.23.0 is pulled on Linux only, by granian's own marker). `uv.lock` pins the exact versions.
- Images are pinned: `postgres:18.6`, `nats:2.12.15` / `nats:2.12.15-alpine`, `ghcr.io/astral-sh/uv:0.13.0-trixie-slim`, `gcr.io/distroless/cc-debian13:nonroot@sha256:e792ab3d241a468a4fd7519ddbbebe66b49b5f365771716ea688ad40b6c6f1c2`, `ghcr.io/zitadel/zitadel:v4.19.2@sha256:956a298405189a8f6cd4ec92e8828b1bd10caa7da8b139c39d251d7053e33784`.
- Work on branch `milestone-2-identity`, created from `main` at `1f876b1` or later (the commit that holds Milestone 1's final-review fix wave, if it has landed); one commit per task; never push or rewrite `main` unless the owner asks; commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Milestone 1 has landed. Its surface this plan builds on: `Service(settings=, version=, interfaces=Interfaces(http=, grpc=), events=Events(streams=, consumers=), limits=)`, `Runtime` (`settings`, `database`, `nats`, `jetstream`, `outbox`, `tracing`, `registry`), `RuntimeDependency[S]`, `HttpRoutes[S]`, `GrpcServices[S]`, and `ledger_platform.schema.CHASSIS_METADATA` (the chassis's tables for a migration; module `schema` sits in the top platform layer, `"service | schema"`, and `supervision` in the foundations layer).
- Another agent may be applying Milestone 1's final-review fix wave (`.superpowers/sdd/2026-10-11-milestone-1-foundations/final-review-fixes.md`) to `libs/` while this plan starts. It adds, among others: `Events(workers=...)` (supervised service workers), `checks=` (a service's own readiness checks), a resources hook (async context managers the lifecycle enters at start and leaves at stop, exposed to routes and handlers), `pool_size` in `ServiceLimits`, a built-in readiness check that the chassis's tables exist, gRPC RED metrics and `ledger_`-prefixed metric names, gRPC grace and NATS drain in `ServiceLimits`, renamed `Clock`/`Sleep` aliases, a redacting log renderer, a `TaxId` whose `repr` and errors no longer show the digits, and a test that every workspace package is wired into coverage, mypy, deptry and import-linter. Task 1 re-checks the surface; every task that uses one of these names says "re-check ... before starting", and where a landed name or signature differs, the task uses the landed one and says so in its report (a renamed parameter is an edit to this plan's code, never a reason to change the chassis).

## Review Focus

1. **Hostile and broken tokens**: `alg: none`, an HMAC token signed with the public key as the secret, a key id nobody published (one at a time and in floods), garbage and megabyte-long headers, expired or not-yet-valid tokens at the edge of the leeway, a JWKS endpoint that is down, slow or answers garbage. Expected: 401 with a one-word reason and never the token, the keys already held keep working, a key-id flood costs at most one fetch per refresh floor, and 503 only when no key was ever obtained. Tests in Task 3.
2. **Reaching across tenants**: the id of a company of another organization, or of the caller's organization but where the caller holds no role, in the path, a header or a gRPC request; a branch id of company A asked for under company B; a row written in one organization that points at a company of another, or an emission point that points at a branch of another company. Expected: 404 `company_not_found` (or the entity's own not-found); PostgreSQL hides the rows even where the application filter is skipped (organization-owned rows visible to every company of that organization only, company-owned rows to that company only, nothing without a scope); the repositories still filter by the unit's organization with row-level security switched off; and a cross-tenant reference is refused by a composite foreign key, which row-level security does not cover. Tests in Tasks 4, 6, 12, 13, 14 and 15.
3. **The same `Idempotency-Key` twice**: the same body retried, a different body, two requests at the same moment, a retry after the first failed, a key older than 24 hours. Expected: the state changes once and both answers are byte-identical (the second says `Idempotent-Replayed: true`); a different body is 422 `idempotency_key_reused`; a concurrent twin waits and replays, or gets 409 `idempotency_key_in_use` after the lock timeout; a failure frees the key; an expired key runs again. Tests in Tasks 5 and 13.
4. **Concurrent and stale updates**: `PATCH` without `If-Match`, with a stale or weak `ETag`, with `*`, and two `PATCH`es racing on one version. Expected: 428, 412, applied, and exactly one of the two wins while the other gets 412. Tests in Tasks 6 and 13.
5. **Escalation and lock-out through memberships**: an admin granting or taking the owner role, revoking or demoting the last owner, granting a role in a deactivated company, an owner demoted while its own grant of the owner role waits for the company, a deactivated company's issuers asking another service to issue. Expected: 403 `owners_only`, 409 `last_owner`, 409 `company_inactive`; the waiting grant is decided on the role the actor holds once it has the company's lock (403 `owners_only`); a deactivated company grants other services the reading permissions only; and the company always keeps an owner. Tests in Tasks 9, 10, 12, 14 and 15.

## Decisions

The owner's decisions for this milestone are binding; these are the ones this plan adds, each with its reason.

1. **An organization's id is derived, not stored**: `OrganizationId = uuid5(uuid5(NAMESPACE_URL, "urn:ledger:organization"), <Zitadel organization id>)` (`ledger_platform.authentication.organization_of`). Every service learns the tenant's organization from the verified token alone, with no call to identity; the row-level security setting needs it on every request. It is not a UUIDv7: we do not create organizations, Zitadel does.
2. **The organization claim is `urn:zitadel:iam:user:resourceowner:id`**, and project roles come from `urn:zitadel:iam:org:project:{audience}:roles` and `urn:zitadel:iam:org:project:roles` (the role keys granted in the caller's organization). Verified against a running Zitadel v4.19.2 on 2026-10-12: `urn:zitadel:iam:org:id:{id}` is a *scope*, not a claim; a machine user's JWT access token carries `iss`, `aud` (the project id), `sub`, `exp`, `iat`, `nbf`, `client_id` and the resource-owner claims, header `{"alg": "RS256", "kid": ..., "typ": "JWT"}`.
3. **A new error kind, `UNAUTHENTICATED`**: HTTP 401 with `WWW-Authenticate: Bearer realm="ledger"`, gRPC `UNAUTHENTICATED`. 401 is not 403 (RFC 9110, RFC 6750); §4.2's table gains the row. The pinned problem code of a bare 401 becomes `unauthenticated`, the kind's word, as with every other kind.
4. **Two isolation scopes.** Organization-owned tables (`organizations`, `companies`, `memberships`, the chassis's `idempotency_keys`) are under `organization_isolation` (rows of the transaction's organization, whatever company it names); company-owned ones (`branches`, `emission_points`) under `tenant_isolation`. `Database.transaction` takes `Tenant | OrganizationId | None`, and the unit it opens knows that scope (`UnitOfWork.scope`), so a repository filters by the same tenant that row-level security enforces. The chassis's policy is part of its schema (`ledger_platform.schema.CHASSIS_POLICIES`), run by every service's first migration. Listing "my companies" is cross-company within one organization; §7's and §8's sentences are amended in Task 18.
5. **Permissions are kernel vocabulary** (`ledger_kernel.access.Permission`, stable dotted codes such as `invoice.issue`) and travel over gRPC as those strings; a reader ignores a code it does not know, so identity can grant permissions older services never check. Roles and the role → permission table are identity's domain.
6. **No role in a company is 404 `company_not_found`; a role without the permission is 403 `permission_denied`.** A caller learns nothing about companies it has no part in.
7. **Any authenticated principal of an organization may create a company and becomes its owner.** Organization-level roles (from Zitadel project roles, already in `Principal.roles`) can restrict this later without a schema change.
8. **Idempotency is claimed first, in the work's own transaction**: claim, state change, events and the stored response commit together. A failed request stores nothing (its rollback frees the key), so a retry runs again. The key's scope is `(organization, subject, key)`; the fingerprint is SHA-256 of method, path and the canonical JSON of the validated body. Rows live 24 h; each POST of an organization deletes up to 100 of its expired rows. Not a supervised worker (`Events(workers=...)`, Milestone 1's final review): a worker runs without a scope, and `organization_isolation` hides every row from such a transaction (fail closed); a policy exception for a sweeper would show other organizations' stored responses to any unscoped transaction. The cost, recorded in the README's operations list: the expired keys of an organization that stops posting stay until its next POST, until a maintenance role that may bypass row-level security exists (with the migration owner, before production). A replay says `Idempotent-Replayed: true`.
9. **Conditional updates**: strong `ETag: "<version>"`; `If-Match` takes `*`, one tag or a list (strong comparison: a weak tag never matches); missing is 428 `precondition_required`, no match is 412 `precondition_failed`. The version check happens inside the update (compare-and-set), so two racing updates cannot both apply.
10. **Cursors** are the base64url (no padding) of a version byte and the last id of the page: ids are UUIDv7, so id order is creation order. Page size 1 to 100, default 50. Pagination primitives live in the kernel, as §3 says.
11. **`httpx` becomes a runtime dependency of the platform** (fetching JWKS; §12 names it as the HTTP client) and leaves the "test tooling" ban list of import-linter.
12. **A shared test toolkit, `libs/testing` (`ledger_testing`)**, a dev-only workspace package: the PostgreSQL and NATS containers, the logging baseline and `LocalIssuer` (an OpenID provider on 127.0.0.1 with real keys). Named `LocalIssuer`, not `Test...`: pytest would try to collect a `Test*` class imported into a test module and fail on the warning. Production packages may not import it.
13. **Migrations live in the package** (`ledger_identity/adapters/postgres/migrations`, as §6 says), so they ship in the wheel and the image; §13's service-root `migrations/` is amended. They are written by hand, run on a synchronous psycopg engine (no event loop, so the same code runs in a test and in the container), and a test proves the migrated schema equals the models (Alembic's autogenerate comparison through `produce_migrations`, whose result is typed, verified on PostgreSQL 18.6 to report no difference for the outbox's expression index and server defaults).
14. **The gRPC service is `IdentityQueryService`** (buf STANDARD lint requires the `Service` suffix). Callers are authenticated by the mesh (Linkerd identity, deployment milestone); a request names the organization, company and subject it asks about, and identity answers inside that organization only. **Accepted risk, recorded in the spec (§4.2) and the README:** until the mesh is deployed, identity's gRPC server authenticates no caller, so anything that reaches port 50051 can read the companies and permissions of any organization whose ids it knows. The port is therefore never published outside the network of the services (compose publishes only the HTTP port), and a mesh policy that admits only the services that need `IdentityQueryService` is required before production. A shared-secret header now would be a second, weaker identity system to remove later.
15. **Membership changes reach caches through a core NATS subscription on every replica** (`identity.membership.>`): a durable consumer would hand each event to one replica only. It is best effort; the 30 s TTL bounds staleness, and an invalidation epoch keeps an answer fetched before a change from being cached after it.
16. **Deferred, with the reason recorded in the spec and the README (Task 18)**: tracing of gRPC calls and SQL statements (§11) until the first service-to-service caller (milestone 3); a call's deadline derived from its caller's remaining deadline, and retries of idempotent methods (§4.2), with that same first caller (milestone 3): until then every call has a fixed deadline of at most 30 s; schemathesis runs against the OpenAPI document (§14) until the deployment milestone's compose-based contract job; a company's fiscal regime (§3) until `fiscal-do` defines its values; request size limits (§9) at the gateway (deployment milestone); the append-only audit log of security-relevant actions (§9) with vault's signing audit (milestone 3), while a membership row keeps who changed it last; the identity-provider webhooks and API clients of §3 with Zitadel Actions. gRPC RED metrics come with Milestone 1's final review (its interceptor), not with this plan.
17. **API shapes**: assigning a role is `PUT /v1/companies/{company_id}/memberships/{subject}` (idempotent by its method; no `Idempotency-Key`, and no `If-Match`: it sets the role it names, so a retry is harmless and two racing grants serialize on the company's lock; §10's exemption is recorded in Task 18), revoking is `DELETE`; deactivating is `POST /v1/companies/{company_id}/deactivate` (with `Idempotency-Key` and `If-Match`); the caller's organization is `GET /v1/organization` and is 404 until its first company exists (the row is written with that company; no GET writes). A company's tax id never changes and a deactivated company is not reactivated in this milestone; opening a branch or an emission point emits no event (not in the milestone's event list).
18. **The runtime image** is uv-managed CPython 3.15.0 copied onto distroless `cc-debian13:nonroot` (verified on 2026-10-12: Granian with uvloop serves FastAPI from it, read-only, as `nonroot`). With no shell in the image, the health check is a console script (`ledger-probe`, stdlib `http.client`).
19. **Local Zitadel answers as `zitadel.localhost:8080`**: curl and browsers on the host resolve `*.localhost` to loopback and containers reach it through a network alias, so the issuer is the same string everywhere (Zitadel identifies its instance by the `Host` header). It is the optional compose profile `zitadel`; its bootstrap uses the management API v1 (verified on v4.19.2).
20. **Request log context**: the `routes` dependencies bind `organization_id`, `subject` and `company_id` for the rest of the request and unbind them after; every record written inside a span carries `trace_id` and `span_id`, so even the chassis's late error lines join their trace.
21. **A unit of work has one time**: `IdentityUnit.now`, read once from the clock when `bind` makes the unit; every row the unit writes and every event it announces carries it, as PostgreSQL's `now()` is the time of the transaction. Use cases take no clock or time argument, so their signatures stay within the lint's five arguments and a test sets the time where it makes the unit.
22. **Tenancy is checked twice, for real (§2 rule 8).** Every identity repository is bound to the organization of its unit's scope (`bind` reads `UnitOfWork.scope`) and names it in every statement, besides the company where the row is a company's; row-level security checks the same scope again. And because PostgreSQL's referential checks bypass row-level security, a row refers to another through a composite foreign key that carries the tenant: `(organization_id, company_id) → companies (organization_id, id)` and `(company_id, branch_id) → branches (company_id, id)`, over `UNIQUE (organization_id, id)` on companies and `UNIQUE (company_id, id)` on branches. A membership of one organization cannot point at another's company, nor an emission point at another company's branch.
23. **A membership is revoked, not deleted.** It keeps its row with status `revoked`, so its version only grows: granting a role again continues from the revoked version, and every `identity.membership.*` event carries the membership's version after the change (§4.1), which never goes back. The row also keeps when and by whom it last changed (`changed_at`, `changed_by`). Lists, owner counts and permissions read active memberships only.
24. **A change of memberships is decided on the actor's role as it is under the company's lock.** The route's check (`membership.manage`, from `CompanyAccess`) answers early; the use case then reads the actor's own membership after locking the company, so an owner demoted, or a member revoked, by a change that committed meanwhile acts with what it holds now (403 `owners_only` or `permission_denied`, or 404 when it holds nothing).
25. **A deactivated company grants other services the reading permissions only** (`company.read`, `membership.read`, `invoice.read`): `GetPermissions` is what invoicing (milestone 5) will act on, and it cannot tell a company is inactive. Identity's own routes keep the role's permissions: identity refuses every change of an inactive company itself (409 `company_inactive`, the precise answer) and must still let a role be revoked there (taking access away is never blocked).
26. **Readiness knows the schema.** Milestone 1's final review makes `/readyz` check that the chassis's tables exist; identity adds its own check through `checks=`: the database's Alembic revision is the one this version's migrations end at (`ledger_identity.adapters.postgres.migrate.require_head`). A replica of a new version on a database not yet migrated stays out of rotation instead of failing every request.
27. **What a service holds open, it holds as a resource.** The HTTP client that fetches the identity provider's keys (`HttpKeys`) is entered and left through `Service`'s resources hook (Milestone 1's final review), so it is closed in order at stop; a later service's gRPC channel to identity is opened the same way. Tools and tests that run no service fetch with a client per fetch (`fetch_keys`).

## File map

```
ledger/
├── pyproject.toml  uv.lock  .dockerignore  .gitignore  Tiltfile  README.md
├── .github/workflows/ci.yml
├── deploy/local/compose.yaml  deploy/local/identity.env  deploy/local/postgres/init.sql
├── deploy/local/zitadel/{bootstrap.sh, token.sh, README.md}
├── docs/superpowers/specs/2026-10-11-ledger-platform-design.md
├── libs/kernel/src/ledger_kernel/{errors, access, pagination}.py
├── libs/kernel/tests/test_{errors, access, pagination}.py
├── libs/contracts/proto/ledger/identity/v1/identity_query.proto
├── libs/contracts/proto/ledger/events/identity/v1/events.proto
├── libs/contracts/src/ledger/identity/v1/identity_query_pb2{,_grpc}.py{,i}        (generated)
├── libs/contracts/src/ledger/events/identity/v1/events_pb2{,_grpc}.py{,i}          (generated)
├── libs/contracts/tests/test_identity_contracts.py
├── libs/testing/pyproject.toml
├── libs/testing/src/ledger_testing/{__init__, containers, logs, clock, waiting, issuer, caches, api}.py, py.typed
├── libs/testing/tests/{conftest, test_containers, test_logs, test_clock, test_waiting, test_issuer, test_api}.py
├── libs/platform/pyproject.toml
├── libs/platform/src/ledger_platform/{settings, web, grpc_server, database, logs, schema}.py      (modified)
├── libs/platform/src/ledger_platform/{authentication, authorization, idempotency, routes,
│                                      grpc_client, identity_client, probe}.py                    (new)
├── libs/platform/tests/{conftest, test_web, test_grpc_server, test_database, test_logs, test_settings,
│                        test_schema}.py                                                          (modified)
├── libs/platform/tests/test_{authentication, authorization, idempotency, routes, grpc_client,
│                             identity_client, probe}.py                                         (new)
└── services/identity/
    ├── pyproject.toml  Dockerfile  openapi.json
    ├── src/ledger_identity/{__init__, settings, main, openapi}.py, py.typed
    ├── src/ledger_identity/domain/{__init__, ids, text, errors, roles, events, organization,
    │                               company, branches, membership}.py
    ├── src/ledger_identity/application/{__init__, ports, commands, queries}.py
    ├── src/ledger_identity/adapters/{__init__, events, permissions}.py
    ├── src/ledger_identity/adapters/postgres/{__init__, tables, repositories, unit, migrate}.py
    ├── src/ledger_identity/adapters/postgres/migrations/{__init__, env}.py
    ├── src/ledger_identity/adapters/postgres/migrations/versions/{__init__, r0001_identity}.py
    ├── src/ledger_identity/api/{__init__, context, models, organization, companies, branches,
    │                            emission_points, memberships, rest, rpc}.py
    └── tests/{conftest, test_domain, test_application, test_migrations, test_repositories,
               test_companies_api, test_readiness, test_establishments_api, test_memberships_api, test_rpc,
               test_events, test_openapi}.py
```

---

### Task 1: Kernel access vocabulary, the unauthenticated kind, pagination primitives

**Files:**
- Create: `libs/kernel/src/ledger_kernel/access.py`, `libs/kernel/src/ledger_kernel/pagination.py`
- Modify: `libs/kernel/src/ledger_kernel/errors.py`, `libs/platform/src/ledger_platform/web.py`, `libs/platform/src/ledger_platform/grpc_server.py`
- Test: `libs/kernel/tests/test_access.py`, `libs/kernel/tests/test_pagination.py`; modify `libs/kernel/tests/test_errors.py`, `libs/platform/tests/test_web.py`, `libs/platform/tests/test_grpc_server.py`

**Interfaces:**
- Consumes: `LedgerError`, `InvalidInputError`, `ErrorKind`, `excerpt` (kernel errors); `OrganizationId` (kernel ids).
- Produces: `ErrorKind.UNAUTHENTICATED` (value `"unauthenticated"`), `UnauthenticatedError(LedgerError)` (code `unauthenticated`). In `ledger_kernel.access`: `SubjectId = NewType("SubjectId", str)`, `MAX_SUBJECT_LENGTH = 128`, `parse_subject(text: str) -> SubjectId`, `InvalidSubjectError(text: str)` (code `invalid_subject`), `Permission(StrEnum)` with `COMPANY_READ`, `COMPANY_MANAGE`, `MEMBERSHIP_READ`, `MEMBERSHIP_MANAGE`, `OWNERSHIP_MANAGE`, `CERTIFICATE_MANAGE`, `INVOICE_READ`, `INVOICE_ISSUE`; `Principal(*, subject: SubjectId, organization: OrganizationId, provider_organization: str, roles: frozenset[str] = frozenset())` frozen. In `ledger_kernel.pagination`: `DEFAULT_PAGE_SIZE = 50`, `MAX_PAGE_SIZE = 100`, `PageRequest(*, size: int = 50, after: UUID | None = None)`, `Page[T](items: tuple[T, ...], next_cursor: str | None)`, `page_of[T](rows: Sequence[T], request: PageRequest, *, key: Callable[[T], UUID]) -> Page[T]`, `encode_cursor(after: UUID) -> str`, `decode_cursor(text: str) -> UUID`, `InvalidCursorError(text)` (code `invalid_cursor`), `InvalidPageSizeError(size)` (code `invalid_page_size`). Web: a `LedgerError` of kind `UNAUTHENTICATED` answers 401 with `WWW-Authenticate: Bearer realm="ledger"`, and `Bearer realm="ledger", error="invalid_token"` when its code is `invalid_token` (RFC 6750 §3.1: a token was sent and cannot be trusted); the pinned code of status 401 is `unauthenticated`. gRPC: kind `UNAUTHENTICATED` → `grpc.StatusCode.UNAUTHENTICATED`.

- [ ] **Step 1: Branch, and re-check the chassis this plan builds on**

```bash
git status --short
git switch main
git switch -c milestone-2-identity
git log --oneline -5
```

If `git status` lists changes under `libs/` that are not this milestone's (Milestone 1's final-review fix wave, which another agent may be applying), stop and ask the owner: the milestone starts from a committed chassis.

Read the current files and confirm each name below exists with this shape; where one differs, use the current name everywhere this plan uses the old one and say so in the task report (a renamed parameter is an edit to this plan's code, not a reason to change the chassis):

```bash
uv run python - <<'EOF'
import inspect
from ledger_platform import service, settings, web, database, outbox, schema
print(inspect.signature(service.Service.__init__))
print([f for f in service.Runtime.__dataclass_fields__])
print(service.RuntimeDependency, service.HttpRoutes, service.GrpcServices)
print([f for f in service.Interfaces.__dataclass_fields__], [f for f in service.Events.__dataclass_fields__])
print([f for f in service.ServiceLimits.__dataclass_fields__])
print(list(settings.ServiceSettings.model_fields))
print(inspect.signature(web.create_app))
print(inspect.signature(database.Database.transaction), inspect.signature(outbox.Outbox.add))
print(sorted(schema.CHASSIS_METADATA.tables))
EOF
git log --oneline -8 -- libs pyproject.toml
grep -n "layers = \[" -A 8 pyproject.toml
```

Expected: `Service.__init__(self, *, settings, version, interfaces=None, events=None, limits=...)`, plus `checks=` and the resources hook once the final-review fix wave has landed; `Runtime` fields `settings, database, nats, jetstream, outbox, tracing, registry` (and whatever the resources hook adds); `Interfaces(http, grpc)`, `Events(streams, consumers)` (plus `workers` after the fix wave); `ServiceSettings` fields include `service_name, environment, log_level, log_format, database_url, nats_url, http_port, grpc_port, otlp_endpoint`; `create_app(*, info, observability, lifespan, routers=())`; `Database.transaction(self, tenant)`; `Outbox.add(self, unit, envelope, *, subject)`; `schema.CHASSIS_METADATA` holds `['inbox', 'outbox']`. The platform layers contract has `"service | schema"` on top and `supervision` among the foundations: every later task that edits the contract keeps both, and any module the fix wave added. Write down the exact signatures of `checks=`, the resources hook and `Events(workers=...)`: Tasks 3, 13 and 15 are written against those names and are adjusted to the landed signatures.

- [ ] **Step 2: Write the failing tests**

`libs/kernel/tests/test_errors.py` — add the import and one row to the first parametrize list:

```python
from ledger_kernel.errors import (
    ConflictError,
    ErrorKind,
    ForbiddenError,
    InvalidInputError,
    LedgerError,
    NotFoundError,
    UnauthenticatedError,
    UnavailableError,
    excerpt,
)
```

```python
        (UnauthenticatedError, ErrorKind.UNAUTHENTICATED, "unauthenticated"),
```

`libs/kernel/tests/test_access.py`:

```python
from dataclasses import FrozenInstanceError

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ledger_kernel.access import (
    MAX_SUBJECT_LENGTH,
    InvalidSubjectError,
    Permission,
    Principal,
    SubjectId,
    parse_subject,
)
from ledger_kernel.ids import OrganizationId, new_id

SUBJECTS = st.from_regex(rf"[A-Za-z0-9._@:-]{{1,{MAX_SUBJECT_LENGTH}}}", fullmatch=True)


@given(SUBJECTS)
def test_a_subject_of_the_allowed_characters_reads_back_unchanged(text: str) -> None:
    assert parse_subject(text) == text


@pytest.mark.parametrize(
    "text",
    ["", " ", "a b", "394763379928989699\n", "josé", "x" * (MAX_SUBJECT_LENGTH + 1), "a/b", "<b>"],
    ids=["empty", "space", "inner-space", "newline", "non-ascii", "too-long", "slash", "markup"],
)
def test_anything_else_is_not_a_subject(text: str) -> None:
    with pytest.raises(InvalidSubjectError, match="not a subject"):
        parse_subject(text)


def test_a_huge_subject_is_not_echoed_back() -> None:
    with pytest.raises(InvalidSubjectError) as caught:
        parse_subject("x" * 10_000)
    assert len(str(caught.value)) < 80
    assert caught.value.code == "invalid_subject"


def test_permissions_have_stable_dotted_codes() -> None:
    assert {permission.value for permission in Permission} == {
        "company.read",
        "company.manage",
        "membership.read",
        "membership.manage",
        "ownership.manage",
        "certificate.manage",
        "invoice.read",
        "invoice.issue",
    }


def test_a_principal_is_an_immutable_value_without_roles_by_default() -> None:
    organization = OrganizationId(new_id())
    principal = Principal(subject=SubjectId("2001"), organization=organization, provider_organization="1001")
    assert principal == Principal(
        subject=SubjectId("2001"), organization=organization, provider_organization="1001"
    )
    assert principal.roles == frozenset()
    field = "subject"
    with pytest.raises(FrozenInstanceError):
        setattr(principal, field, "someone else")
```

`libs/kernel/tests/test_pagination.py`:

```python
lazy from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ledger_kernel.ids import new_id
from ledger_kernel.pagination import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    InvalidCursorError,
    InvalidPageSizeError,
    Page,
    PageRequest,
    decode_cursor,
    encode_cursor,
    page_of,
)


def same(item: UUID) -> UUID:
    return item


@given(st.uuids())
def test_a_cursor_reads_back_as_the_id_it_was_made_from(value: UUID) -> None:
    assert decode_cursor(encode_cursor(value)) == value


def test_a_cursor_is_short_and_url_safe() -> None:
    cursor = encode_cursor(new_id())
    assert len(cursor) == 23
    assert all(character.isascii() and (character.isalnum() or character in "-_") for character in cursor)


@pytest.mark.parametrize(
    "text",
    ["", "not-a-cursor", "AQ", encode_cursor(new_id())[:-1], "A" * 23, "!" * 23, "ü" * 23, "x" * 10_000],
    ids=["empty", "word", "short", "cut", "other-version", "symbols", "non-ascii", "huge"],
)
def test_a_cursor_that_was_not_made_here_is_refused_without_being_echoed(text: str) -> None:
    with pytest.raises(InvalidCursorError, match="not a cursor") as caught:
        decode_cursor(text)
    assert len(str(caught.value)) < 80
    assert caught.value.code == "invalid_cursor"


def test_a_page_request_holds_from_one_to_the_maximum_items() -> None:
    assert PageRequest().size == DEFAULT_PAGE_SIZE == 50
    assert PageRequest(size=MAX_PAGE_SIZE).size == 100
    assert PageRequest(size=1).after is None


@pytest.mark.parametrize("size", [0, -1, MAX_PAGE_SIZE + 1])
def test_a_page_request_outside_the_range_is_refused(size: int) -> None:
    with pytest.raises(InvalidPageSizeError, match=f"not {size}") as caught:
        PageRequest(size=size)
    assert caught.value.code == "invalid_page_size"


def test_a_page_holds_at_most_the_size_asked_for_and_says_where_to_continue() -> None:
    ids = sorted(new_id() for _ in range(3))
    page = page_of(ids, PageRequest(size=2), key=same)
    assert page.items == (ids[0], ids[1])
    assert page.next_cursor is not None
    assert decode_cursor(page.next_cursor) == ids[1]


def test_the_last_page_says_nothing_follows() -> None:
    ids = [new_id(), new_id()]
    assert page_of(ids, PageRequest(size=2), key=same) == Page(items=(ids[0], ids[1]), next_cursor=None)


def test_an_empty_page() -> None:
    nothing: list[UUID] = []
    assert page_of(nothing, PageRequest(), key=same) == Page(items=(), next_cursor=None)
```

`libs/platform/tests/test_web.py` — import `UnauthenticatedError` from `ledger_kernel.errors` with the others, add `"unauthenticated": UnauthenticatedError,` and `"refused-token": RefusedTokenError,` to `ERRORS`, add `("unauthenticated", HTTPStatus.UNAUTHORIZED),` to the parametrize list of `test_each_error_kind_has_its_status`, change the expected body of `test_an_http_exception_keeps_its_headers_and_does_not_repeat_its_detail` to `"type": "urn:ledger:error:unauthenticated"` and `"code": "unauthenticated"`, change the row `(401, "unauthorized")` of the parametrize list of `test_the_code_of_a_common_status_is_pinned` (line 748 at `1f876b1`) to `(401, "unauthenticated")`, add this class after `BrokenInvariantError`:

```python
class RefusedTokenError(UnauthenticatedError):
    """What the platform's token verifier raises (Task 3), as far as the HTTP app can tell: the code."""

    code: ClassVar[str] = "invalid_token"
```

and these tests:

```python
async def test_an_unauthenticated_caller_is_told_how_to_authenticate(client: httpx.AsyncClient) -> None:
    response = await client.get("/errors/unauthenticated")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == 'Bearer realm="ledger"'
    assert response.json()["code"] == "unauthenticated"


async def test_a_token_that_was_refused_is_named_in_the_challenge(client: httpx.AsyncClient) -> None:
    response = await client.get("/errors/refused-token")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == 'Bearer realm="ledger", error="invalid_token"'


async def test_only_an_unauthenticated_caller_gets_the_challenge(client: httpx.AsyncClient) -> None:
    response = await client.get("/errors/forbidden")
    assert response.status_code == 403
    assert "www-authenticate" not in response.headers
```

(If Milestone 1's final review has added a test that pins `_STATUS` against every `ErrorKind`, it covers the new kind once Step 4 adds it; nothing else to change there.)

`libs/platform/tests/test_grpc_server.py` — import `UnauthenticatedError`, and add one entry to each map:

```python
    ErrorKind.UNAUTHENTICATED: UnauthenticatedError,
```

```python
    ErrorKind.UNAUTHENTICATED: (grpc.StatusCode.UNAUTHENTICATED, "unauthenticated"),
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest libs/kernel libs/platform/tests/test_web.py libs/platform/tests/test_grpc_server.py -q --no-cov`
Expected: FAIL with `ImportError: cannot import name 'UnauthenticatedError'` (and `ModuleNotFoundError: No module named 'ledger_kernel.access'`).

- [ ] **Step 4: Implement**

`libs/kernel/src/ledger_kernel/errors.py` — add the kind between `NOT_FOUND`'s neighbours as shown, and the class after `ConflictError`:

```python
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    """The current state forbids it: a duplicate, a wrong state, a stale version."""
    UNAUTHENTICATED = "unauthenticated"
    """The caller did not prove who it is: no credentials, or credentials that do not hold."""
    FORBIDDEN = "forbidden"
```

```python
class UnauthenticatedError(LedgerError):
    """The caller is not known: it sent no credentials, or ones that cannot be trusted."""

    kind: ClassVar[ErrorKind] = ErrorKind.UNAUTHENTICATED
    code: ClassVar[str] = "unauthenticated"
```

`libs/kernel/src/ledger_kernel/access.py`:

```python
"""Who acts and what they may do: the vocabulary of authorization every service shares.

A ``Principal`` is a caller whose token was verified: the identity provider's id of the user or machine
(``subject``), the organization it acts in, and the roles the provider granted it there. What it may do in a
company is a set of ``Permission``s, which the identity service derives from the principal's role there.
Permissions cross service boundaries as their values, which therefore never change.
"""

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import ClassVar, Final, NewType, final

from ledger_kernel.errors import InvalidInputError, excerpt
lazy from ledger_kernel.ids import OrganizationId

SubjectId = NewType("SubjectId", str)
"""Who a principal is: the identity provider's id of a user or a machine (Zitadel's are digits)."""

MAX_SUBJECT_LENGTH: Final = 128
_SUBJECT: Final = re.compile(rf"[A-Za-z0-9._@:-]{{1,{MAX_SUBJECT_LENGTH}}}", re.ASCII)


class InvalidSubjectError(InvalidInputError):
    """Text that is not a subject: empty, too long, or with a character no provider uses in an id."""

    code: ClassVar[str] = "invalid_subject"

    def __init__(self, text: str) -> None:
        super().__init__(f"not a subject: {excerpt(text)}")


def parse_subject(text: str) -> SubjectId:
    """Read a subject: 1 to ``MAX_SUBJECT_LENGTH`` ASCII letters, digits or ``. _ @ : -``."""
    if _SUBJECT.fullmatch(text) is None:
        raise InvalidSubjectError(text)
    return SubjectId(text)


class Permission(StrEnum):
    """What a principal may do in a company. Stable codes: they cross service boundaries."""

    COMPANY_READ = "company.read"
    """Read the company, its branches and its emission points."""
    COMPANY_MANAGE = "company.manage"
    """Change the company, deactivate it, open branches and emission points."""
    MEMBERSHIP_READ = "membership.read"
    """See who holds which role in the company."""
    MEMBERSHIP_MANAGE = "membership.manage"
    """Grant and revoke every role but the owner's."""
    OWNERSHIP_MANAGE = "ownership.manage"
    """Grant and revoke the owner role."""
    CERTIFICATE_MANAGE = "certificate.manage"
    """Upload and replace the company's signing certificate (milestone 3)."""
    INVOICE_READ = "invoice.read"
    """Read the company's documents (milestone 5)."""
    INVOICE_ISSUE = "invoice.issue"
    """Issue documents in the company's name (milestone 5)."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Principal:
    """A caller whose credentials were verified."""

    subject: SubjectId
    organization: OrganizationId
    """The organization it acts in: derived from the identity provider's organization id."""
    provider_organization: str
    """The identity provider's own id of that organization (Zitadel's resource owner)."""
    roles: frozenset[str] = field(default_factory=frozenset[str])
    """Roles the identity provider granted in that organization (project roles); none by default."""
```

`libs/kernel/src/ledger_kernel/pagination.py`:

```python
"""Cursor pagination: a page of items in id order, and an opaque cursor saying where the next page starts.

Ids are UUIDv7, so id order is creation order, and a cursor is the last id a page held: the next page holds
the items after it. A cursor is opaque to clients (a version byte and the id, base64url without padding), so
its encoding can change; text that is not one of ours is refused, and never echoed whole.
"""

import base64
import re
from dataclasses import dataclass
from typing import ClassVar, Final, final
from uuid import UUID
lazy from collections.abc import Callable, Sequence

from ledger_kernel.errors import InvalidInputError, excerpt

DEFAULT_PAGE_SIZE: Final = 50
MAX_PAGE_SIZE: Final = 100
_VERSION: Final = b"\x01"
_CURSOR: Final = re.compile(r"[A-Za-z0-9_-]{23}", re.ASCII)
"""Seventeen bytes in base64url without padding: the version byte and the sixteen of the id."""


class InvalidCursorError(InvalidInputError):
    """Text that is not a cursor this service handed out."""

    code: ClassVar[str] = "invalid_cursor"

    def __init__(self, text: str) -> None:
        super().__init__(f"not a cursor: {excerpt(text)}")


class InvalidPageSizeError(InvalidInputError):
    """A page size outside 1..``MAX_PAGE_SIZE``."""

    code: ClassVar[str] = "invalid_page_size"

    def __init__(self, size: int) -> None:
        super().__init__(f"a page holds 1 to {MAX_PAGE_SIZE} items, not {size}")


def encode_cursor(after: UUID) -> str:
    """The cursor of the page that starts after ``after``."""
    return base64.urlsafe_b64encode(_VERSION + after.bytes).rstrip(b"=").decode("ascii")


def decode_cursor(text: str) -> UUID:
    """The id a cursor of ours continues after; anything else is an ``InvalidCursorError``."""
    if _CURSOR.fullmatch(text) is None:
        raise InvalidCursorError(text)
    raw = base64.urlsafe_b64decode(text + "=")
    if raw[:1] != _VERSION:
        raise InvalidCursorError(text)
    return UUID(bytes=raw[1:])


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class PageRequest:
    """How many items a page holds, and after which id it starts (``None``: from the first)."""

    size: int = DEFAULT_PAGE_SIZE
    after: UUID | None = None

    def __post_init__(self) -> None:
        if not 1 <= self.size <= MAX_PAGE_SIZE:
            raise InvalidPageSizeError(self.size)


@final
@dataclass(frozen=True, slots=True)
class Page[T]:
    """Items in id order, and the cursor of the next page (``None`` on the last)."""

    items: tuple[T, ...]
    next_cursor: str | None


def page_of[T](rows: Sequence[T], request: PageRequest, *, key: Callable[[T], UUID]) -> Page[T]:
    """The page ``request`` asked for, from ``rows`` read in id order, one more than ``request.size`` at most.

    The extra row, when it is there, only says that more follow: the cursor is the id of the last item kept.
    """
    items = tuple(rows[: request.size])
    more = len(rows) > request.size
    return Page(items, encode_cursor(key(items[-1])) if more else None)
```

`libs/platform/src/ledger_platform/web.py` — in `_CODES` change the 401 entry, in its docstring list 401 among the statuses whose code is the kind's word, add the kind to `_STATUS`, the challenge after `_STATUS`, and the header in the `LedgerError` handler (re-check the handler first: Milestone 1's final review may have moved the answer to bugs into an outer ASGI layer; the challenge belongs wherever a `LedgerError` of a kind other than `INTERNAL` is answered):

```python
    HTTPStatus.UNAUTHORIZED: "unauthenticated",
```

```python
"""The codes clients match on, written out: they must not follow the names CPython gives its statuses (3.13
renamed 413, 414, 416 and 422). Where ``ErrorKind`` has a word for it (404, 401, 403, 409, 422, 500, 503) the
code is that word, so a route's ``HTTPException`` and a ``LedgerError`` of the same meaning are one code."""
```

```python
    ErrorKind.UNAUTHENTICATED: HTTPStatus.UNAUTHORIZED,
```

```python
_CHALLENGE: Final = 'Bearer realm="ledger"'
"""What a 401 tells a client to send (RFC 6750): a bearer token. The problem's code says what was wrong."""
_REFUSED_CODE: Final = "invalid_token"
"""The one code a challenge repeats (RFC 6750 §3.1): a token was sent, and it cannot be trusted. A request
without one gets the bare challenge, as the RFC asks."""


def _challenge(code: str) -> Mapping[str, str]:
    """The ``WWW-Authenticate`` header of a 401 whose problem has the code ``code``."""
    error = f', error="{_REFUSED_CODE}"' if code == _REFUSED_CODE else ""
    return {"WWW-Authenticate": _CHALLENGE + error}
```

```python
    @app.exception_handler(LedgerError)
    async def ledger_error(request: Request, error: LedgerError) -> JSONResponse:
        status = _STATUS[error.kind]
        if error.kind is ErrorKind.INTERNAL:
            # A defect of ours: the client gets what any bug gets, the log gets the code and the message.
            _report_defect(request, error, error.code, labels)
            return _problem(status, "internal")
        challenge = _challenge(error.code) if error.kind is ErrorKind.UNAUTHENTICATED else None
        return _problem(status, error.code, detail=str(error) or None, headers=challenge)
```

`libs/platform/src/ledger_platform/grpc_server.py` — add to `_STATUS`:

```python
    ErrorKind.UNAUTHENTICATED: grpc.StatusCode.UNAUTHENTICATED,
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest libs/kernel libs/platform/tests/test_web.py libs/platform/tests/test_grpc_server.py -q --no-cov`
Expected: PASS.

- [ ] **Step 6: Gate**

Run: `uv run poe check`
Expected: every step passes, coverage 100 %.

- [ ] **Step 7: Commit**

```bash
git add libs/kernel libs/platform/src/ledger_platform/web.py libs/platform/src/ledger_platform/grpc_server.py libs/platform/tests/test_web.py libs/platform/tests/test_grpc_server.py
git commit -m "feat(kernel): principals, permissions, pagination and the unauthenticated kind

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: The shared test toolkit (`ledger_testing`)

**Files:**
- Create: `libs/testing/pyproject.toml`, `libs/testing/src/ledger_testing/__init__.py`, `py.typed`, `containers.py`, `logs.py`, `clock.py`, `waiting.py`
- Modify: `pyproject.toml` (root), `libs/platform/tests/conftest.py`
- Test: `libs/testing/tests/conftest.py`, `libs/testing/tests/test_containers.py`, `libs/testing/tests/test_logs.py`, `libs/testing/tests/test_clock.py`, `libs/testing/tests/test_waiting.py`

**Interfaces:**
- Consumes: nothing of the ledger packages.
- Produces: `ledger_testing.containers`: `POSTGRES_IMAGE = "postgres:18.6"`, `NATS_IMAGE = "nats:2.12.15"`, `NATS_PORT = 4222`, `Postgres` with `classmethod started() -> AbstractContextManager[Postgres]` and `database(name: str, *, role: str, password: str) -> str` (a `postgresql+psycopg://` URL of a new database owned by a new non-superuser role), `libpq_url(url: str) -> str` (the same URL as psycopg itself takes it), `nats_server(*arguments: str) -> DockerContainer`, `nats_address(container: DockerContainer) -> str`. `ledger_testing.logs`: `use_the_plain_baseline() -> None`, `restored_logging() -> AbstractContextManager[None]`. `ledger_testing.clock`: `Moment(now: float = 0.0)`, a clock a test moves by hand (`moment.now += 30`). `ledger_testing.waiting`: `PATIENCE = 5.0`, `async eventually(check: Callable[[], Awaitable[bool]], *, within: float = PATIENCE) -> None`, `async waits_for_a_lock(url: str) -> bool` (a session of that database waits for a lock: a racing twin is held behind the first). The root dev group gains `ledger-testing`; production packages may not import it.

Re-check before starting: Milestone 1's final review may have changed `libs/platform/tests/conftest.py` (its fixtures are rewritten below only where they move to the toolkit) and added a test that every workspace package is in coverage's `source_pkgs`, the per-package mypy and deptry tasks and import-linter's `root_packages`; Step 3 adds `ledger_testing` to each, so that test passes.

- [ ] **Step 1: Write the failing tests**

`libs/testing/tests/test_logs.py`:

```python
import logging

import pytest
import structlog

from ledger_testing.logs import restored_logging


def test_the_block_starts_from_the_baseline_and_leaves_logging_as_it_found_it() -> None:
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    with restored_logging():
        root.handlers[:] = [logging.NullHandler()]
        root.setLevel(logging.CRITICAL)
        structlog.contextvars.bind_contextvars(company_id="c-1")
    assert (root.handlers, root.level) == (handlers, level)
    assert structlog.contextvars.get_contextvars() == {}


def test_logging_is_restored_even_when_the_block_fails() -> None:
    root = logging.getLogger()
    level = root.level

    def fail() -> None:
        with restored_logging():
            root.setLevel(logging.CRITICAL)
            _ = 1 / 0

    with pytest.raises(ZeroDivisionError):
        fail()
    assert root.level == level


def test_the_baseline_writes_plain_text_without_colors(capsys: pytest.CaptureFixture[str]) -> None:
    with restored_logging():
        structlog.get_logger().info("baseline.works", answer=42)
    written = capsys.readouterr().out
    assert "baseline.works" in written
    assert "answer=42" in written
    assert "\x1b[" not in written
```

`libs/testing/tests/conftest.py`:

```python
"""Fixtures of the toolkit's own tests: one real PostgreSQL for the session."""

lazy from collections.abc import Iterator

import pytest

from ledger_testing.containers import Postgres


@pytest.fixture(scope="session")
def server() -> Iterator[Postgres]:
    """A real PostgreSQL; each test makes the databases it needs in it."""
    with Postgres.started() as started:
        yield started
```

`libs/testing/tests/test_containers.py`:

```python
import psycopg

from ledger_testing.containers import libpq_url
lazy from ledger_testing.containers import Postgres


def test_a_database_belongs_to_a_login_role_that_cannot_bypass_row_level_security(server: Postgres) -> None:
    url = server.database("probe", role="probe_owner", password="it's-a-password")
    with psycopg.connect(libpq_url(url)) as connection:
        row = connection.execute(
            "SELECT current_user, rolsuper, rolbypassrls, pg_get_userbyid(datdba) "
            "FROM pg_roles, pg_database WHERE rolname = current_user AND datname = current_database()"
        ).fetchone()
    assert row == ("probe_owner", False, False, "probe_owner")


def test_every_database_gets_a_role_of_its_own(server: Postgres) -> None:
    first = server.database("first", role="first_owner", password="one")
    server.database("second", role="second_owner", password="two")
    with psycopg.connect(libpq_url(first)) as connection:
        owners = connection.execute(
            "SELECT datname::text, pg_get_userbyid(datdba)::text FROM pg_database "
            "WHERE datname IN ('first', 'second') ORDER BY datname"
        ).fetchall()
        roles = connection.execute(
            "SELECT rolname::text, rolsuper, rolbypassrls FROM pg_roles "
            "WHERE rolname IN ('first_owner', 'second_owner') ORDER BY rolname"
        ).fetchall()
    assert owners == [("first", "first_owner"), ("second", "second_owner")]
    assert roles == [("first_owner", False, False), ("second_owner", False, False)]


def test_psycopg_takes_the_same_url_without_the_driver() -> None:
    assert libpq_url("postgresql+psycopg://app:secret@host:5432/db") == "postgresql://app:secret@host:5432/db"
```

(The apostrophe in the password checks that it reaches PostgreSQL as a quoted literal. A URL carries the password as it is, so the tests choose passwords without `@`, `:`, `/` or spaces.)

`libs/testing/tests/test_clock.py`:

```python
from ledger_testing.clock import Moment


def test_a_moment_stands_still_until_the_test_moves_it() -> None:
    moment = Moment(1_000.0)
    assert (moment(), moment()) == (1_000.0, 1_000.0)
    moment.now += 30
    assert moment() == 1_030.0
    assert Moment()() == 0.0
```

`libs/testing/tests/test_waiting.py`:

```python
import asyncio

import psycopg
import pytest

from ledger_testing.containers import libpq_url
from ledger_testing.waiting import eventually, waits_for_a_lock
lazy from ledger_testing.containers import Postgres


async def test_waiting_ends_as_soon_as_the_check_holds() -> None:
    answers = iter((False, False, True, False))

    async def check() -> bool:
        return next(answers)

    await eventually(check)
    assert next(answers) is False  # asked three times, not once more


async def test_a_check_that_never_holds_ends_in_a_timeout() -> None:
    async def never() -> bool:
        return False

    with pytest.raises(TimeoutError):
        await eventually(never, within=0.05)


async def test_a_session_held_behind_a_lock_is_seen_waiting(server: Postgres) -> None:
    url = server.database("locks", role="locks_owner", password="locks-password")
    assert not await waits_for_a_lock(url)
    async with (
        await psycopg.AsyncConnection.connect(libpq_url(url)) as holder,
        await psycopg.AsyncConnection.connect(libpq_url(url), autocommit=True) as waiter,
    ):
        await holder.execute("CREATE TABLE gate (id integer)")
        await holder.commit()
        await holder.execute("LOCK TABLE gate IN ACCESS EXCLUSIVE MODE")
        blocked = asyncio.create_task(waiter.execute("SELECT id FROM gate"))
        await eventually(lambda: waits_for_a_lock(url))
        await holder.rollback()
        await blocked
    assert not await waits_for_a_lock(url)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest libs/testing -q --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'ledger_testing'`.

- [ ] **Step 3: Implement**

`libs/testing/pyproject.toml`:

```toml
[project]
name = "ledger-testing"
version = "0.1.0"
description = "What the tests of every ledger package share: real PostgreSQL and NATS, a local OpenID provider, the logging baseline."
requires-python = ">=3.15"
dependencies = [
    "psycopg[binary]>=3.3.6",
    "structlog>=26.1.0",
    "testcontainers>=4.15.0",
]

[build-system]
requires = ["uv_build>=0.13.0,<0.14.0"]
build-backend = "uv_build"
```

`libs/testing/src/ledger_testing/__init__.py`:

```python
"""What the tests of every ledger package share. A dev dependency: production code never imports it."""
```

`libs/testing/src/ledger_testing/py.typed`: empty file.

`libs/testing/src/ledger_testing/containers.py`:

```python
"""Real PostgreSQL and NATS servers for tests, in containers, at the versions the services run on."""

from contextlib import contextmanager
from typing import Final, final
lazy from collections.abc import Iterator

import psycopg
from psycopg import sql
from testcontainers.community.postgres import PostgresContainer
from testcontainers.core.container import DockerContainer
from testcontainers.core.wait_strategies import LogMessageWaitStrategy

POSTGRES_IMAGE: Final = "postgres:18.6"
NATS_IMAGE: Final = "nats:2.12.15"
NATS_PORT: Final = 4222
_POSTGRES_PORT: Final = 5432


@final
class Postgres:
    """A PostgreSQL server; every database it makes belongs to a login role of its own that is no superuser.

    A superuser bypasses row-level security, and so does a table's owner unless the table forces it: tests
    connect as the kind of role a service connects as, never as the administrator.
    """

    __slots__ = ("_container",)

    def __init__(self, container: PostgresContainer) -> None:
        self._container = container

    @classmethod
    @contextmanager
    def started(cls) -> Iterator[Postgres]:
        """A server for the length of the block."""
        with PostgresContainer(POSTGRES_IMAGE, driver=None) as container:
            yield cls(container)

    def database(self, name: str, *, role: str, password: str) -> str:
        """A new database ``name`` owned by the new role ``role``: its SQLAlchemy URL, psycopg driver."""
        with psycopg.connect(self._container.get_connection_url(), autocommit=True) as admin:
            admin.execute(
                sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS").format(
                    sql.Identifier(role), sql.Literal(password)
                )
            )
            admin.execute(
                sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(name), sql.Identifier(role))
            )
        host = self._container.get_container_host_ip()
        port = self._container.get_exposed_port(_POSTGRES_PORT)
        return f"postgresql+psycopg://{role}:{password}@{host}:{port}/{name}"


def libpq_url(url: str) -> str:
    """The URL psycopg itself takes for ``url``, an SQLAlchemy ``postgresql+psycopg://`` URL."""
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def nats_server(*arguments: str) -> DockerContainer:
    """A NATS server with JetStream, started with ``arguments`` too, ready once it says so."""
    return (
        DockerContainer(NATS_IMAGE)
        .with_command(["-js", *arguments])
        .with_exposed_ports(NATS_PORT)
        .waiting_for(LogMessageWaitStrategy("Server is ready"))
    )


def nats_address(container: DockerContainer) -> str:
    """The URL a client reaches ``container``'s NATS server by."""
    return f"nats://{container.get_container_host_ip()}:{container.get_exposed_port(NATS_PORT)}"
```

`libs/testing/src/ledger_testing/logs.py`:

```python
"""The logging every test starts from: what a service writes to a terminal, without colors or locals."""

import logging
from contextlib import contextmanager
lazy from collections.abc import Iterator

import structlog


def use_the_plain_baseline() -> None:
    """Log as a service does to a terminal: no colors, and tracebacks as plain text without locals.

    structlog's own defaults render a traceback with ``rich`` and every local variable of every frame, which
    is slow (seconds for one logged exception) and prints exactly what ``configure_logging`` keeps out of a
    record. A test that logs gets the production rules, and ``capture_logs`` keeps working on top of them.
    """
    structlog.reset_defaults()
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.dev.ConsoleRenderer(colors=False, exception_formatter=structlog.dev.plain_traceback),
        ],
        cache_logger_on_first_use=False,
    )


@contextmanager
def restored_logging() -> Iterator[None]:
    """Run the block from the plain baseline, then leave the process's logging as the block found it.

    ``configure_logging`` changes process-wide state (structlog's configuration, the root logger's
    handlers and level): a test that builds a service must not leave it to the next. Context variables
    bound in the block are cleared, whatever the block raised.
    """
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    use_the_plain_baseline()
    try:
        yield
    finally:
        root.handlers[:] = handlers
        root.setLevel(level)
        use_the_plain_baseline()
        structlog.contextvars.clear_contextvars()
```

`libs/testing/src/ledger_testing/clock.py`:

```python
"""A clock a test moves by hand, for code that takes its clock as an argument."""

from typing import final


@final
class Moment:
    """Seconds on a monotonic clock that stands still until a test moves it: ``moment.now += 30``."""

    __slots__ = ("now",)

    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        """The time the test set."""
        return self.now
```

`libs/testing/src/ledger_testing/waiting.py`:

```python
"""Waiting, in a test, for what happens elsewhere: until a check holds, within a bound, never for a guess.

A test whose progress depends on a task of its own (a relay, a consumer, a transaction held behind a lock)
cannot await that progress: it waits until something it can observe says so. ``eventually`` polls a check;
``waits_for_a_lock`` is the check that a racing transaction is now held behind another one.
"""

import asyncio
from typing import Final
lazy from collections.abc import Awaitable, Callable

import psycopg
from psycopg.rows import args_row

from ledger_testing.containers import libpq_url

PATIENCE: Final = 5.0
"""Seconds ``eventually`` waits at most: a bound, not a delay; it returns as soon as the check holds."""
_POLL: Final = 0.02
_LOCK_WAITS: Final = (
    "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
    "WHERE datname = current_database() AND wait_event_type = 'Lock')"
)


async def eventually(check: Callable[[], Awaitable[bool]], *, within: float = PATIENCE) -> None:
    """Return as soon as ``check`` holds; ``TimeoutError`` if it does not within ``within`` seconds."""
    async with asyncio.timeout(within):
        while True:
            if await check():
                return
            await asyncio.sleep(_POLL)


async def waits_for_a_lock(url: str) -> bool:
    """Whether a session of the database at ``url`` (an SQLAlchemy psycopg URL) waits for a lock.

    The sessions of one role see each other in ``pg_stat_activity``, and the tests connect as the owner of
    the database they use.
    """
    async with await psycopg.AsyncConnection.connect(libpq_url(url), autocommit=True) as connection:
        cursor = connection.cursor(row_factory=args_row(bool))
        await cursor.execute(_LOCK_WAITS)
        return await cursor.fetchone() is True
```

Root `pyproject.toml`:

```toml
[dependency-groups]
dev = [
    # ... the existing entries, plus:
    "ledger-testing",
]
```

```toml
[tool.uv.sources]
ledger-kernel = { workspace = true }
ledger-contracts = { workspace = true }
ledger-platform = { workspace = true }
ledger-testing = { workspace = true }
```

In `[tool.poe.tasks]`: add `mypy-testing = { cmd = "mypy libs/testing" }` and append `"mypy-testing"` to the `mypy` sequence; add `deps-testing = { cmd = "deptry libs/testing/src --config libs/testing/pyproject.toml" }` and append `"deps-testing"` to the `deps` sequence. In `[tool.ruff.lint.isort]` add `"ledger_testing"` to `known-first-party`. In `[tool.coverage.run]` add `"ledger_testing"` to `source_pkgs`. In `[tool.importlinter]` add `"ledger_testing"` to `root_packages`, and to the `forbidden_modules` of the contract "Production code never imports test tooling":

```toml
forbidden_modules = ["pytest", "hypothesis", "testcontainers", "httpx", "ledger_testing"]
```

`libs/platform/tests/conftest.py` — the constants `POSTGRES_IMAGE`, `NATS_IMAGE`, `NATS_PORT`, the function `use_the_plain_baseline` and `nats_server`, and the imports only they used (`asyncio`, `psycopg`, `PostgresContainer`, `DockerContainer`, `LogMessageWaitStrategy`) go; the fixtures that used them, and `eventually`, become:

```python
from ledger_testing import waiting
from ledger_testing.containers import Postgres, nats_address, nats_server
from ledger_testing.logs import restored_logging

SMALL_PAYLOAD_CONFIG = "/small-payload.conf"


@pytest.fixture(autouse=True)
def restore_logging() -> Iterator[None]:
    """Each test starts from, and leaves, the plain baseline: ``configure_logging`` is process-wide state."""
    with restored_logging():
        yield


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    """A real PostgreSQL; the app connects as ``ledger_app``, which owns the database but is no superuser."""
    with Postgres.started() as server:
        yield server.database("ledger", role="ledger_app", password="app-password")


@pytest.fixture(scope="session")
def nats_url() -> Iterator[str]:
    """A real NATS server with JetStream."""
    with nats_server() as container:
        yield nats_address(container)


@pytest.fixture(scope="session")
def small_payload_nats_url() -> Iterator[str]:
    """A real NATS server with JetStream whose messages are at most 64 KiB, headers included.

    ``max_payload`` is set in a configuration file: the server takes no flag for it.
    """
    server = nats_server("--config", SMALL_PAYLOAD_CONFIG).with_copy_into_container(
        b"max_payload: 65536\n", SMALL_PAYLOAD_CONFIG
    )
    with server as container:
        yield nats_address(container)


@pytest.fixture
def eventually() -> Callable[[Callable[[], Awaitable[bool]]], Awaitable[None]]:
    """Waits until ``check`` is true, for at most five seconds (``ledger_testing.waiting.eventually``).

    For what happens in a task of its own, such as a relay or a consumer, whose progress a test cannot await.
    """
    return waiting.eventually
```

(The other fixtures, `database`, `connect`, `jetstream`, `exporter`, `tracing` and `registry`, stay as they are; `logging` and `structlog` stay imported only if a remaining fixture uses them.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv lock && uv sync && uv run pytest libs/testing libs/platform -q --no-cov`
Expected: PASS (the platform's tests run unchanged on the shared helpers).

- [ ] **Step 5: Gate**

Run: `uv run poe fmt && uv run poe check`
Expected: every step passes, coverage 100 % (`ledger_testing` included).

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock libs/testing libs/platform/tests/conftest.py
git commit -m "test: shared test toolkit for containers and the logging baseline

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Platform authentication: bearer tokens verified against the provider's keys

**Files:**
- Create: `libs/platform/src/ledger_platform/authentication.py`, `libs/testing/src/ledger_testing/issuer.py`
- Modify: `libs/platform/src/ledger_platform/settings.py`, `libs/platform/pyproject.toml`, `libs/testing/pyproject.toml`, `pyproject.toml` (root)
- Test: `libs/platform/tests/test_authentication.py`, `libs/testing/tests/test_issuer.py`; modify `libs/platform/tests/test_settings.py`

**Interfaces:**
- Consumes: `Principal`, `parse_subject`, `InvalidSubjectError`, `UnauthenticatedError`, `UnavailableError`, `OrganizationId` (Task 1); `InvalidOptionError`, `require_seconds` (`ledger_platform.options`); `ServiceSettings`; `Moment` (`ledger_testing.clock`, Task 2).
- Produces: in `ledger_platform.settings`: `TokenAlgorithm = Literal["RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512", "EdDSA"]` (an ordinary alias: its values are read at runtime); `ApiSettings(ServiceSettings)` with `token_issuer: str`, `token_audience: str`, `token_jwks_url: str` (all required), `token_algorithms: frozenset[TokenAlgorithm] = frozenset({"RS256"})`, `token_leeway_seconds: int = 60` (0..300); keys must be `https://` in `staging` and `production`. In `ledger_platform.authentication`: `ASYMMETRIC_ALGORITHMS: frozenset[str]` (the values of `TokenAlgorithm`), `MAX_TOKEN_LENGTH = 8192`, `MAX_KEYS_SIZE = 65536`, `ORGANIZATION_CLAIM`, `PROJECT_ROLES_CLAIM`, `organization_of(provider_organization: str) -> OrganizationId`, `TokenProblem(StrEnum)` (`MALFORMED`, `ALGORITHM`, `UNKNOWN_KEY`, `SIGNATURE`, `EXPIRED`, `NOT_YET_VALID`, `ISSUER`, `AUDIENCE`, `CLAIMS`), `MissingTokenError()` (code `missing_token`), `InvalidTokenError(problem: TokenProblem)` (code `invalid_token`, attribute `problem`), `TokenKeysUnavailableError()` (kind `UNAVAILABLE`, code `token_keys_unavailable`), `KeysClientClosedError()` (kind `INTERNAL`, code `keys_client_closed`), `InvalidTokenOptionError`, `TokenPolicy(*, issuer: str, audience: str, keys_url: str, algorithms: frozenset[str] = frozenset({"RS256"}), leeway: float = 60.0, keys_ttl: float = 600.0, refresh_floor: float = 10.0, fetch_timeout: float = 5.0)` with `classmethod of(settings: ApiSettings) -> TokenPolicy`, `type FetchKeys = Callable[[str], Awaitable[bytes]]`, `async fetch_keys(url: str) -> bytes` (a client of its own per fetch, for tools and tests; the caller bounds the whole fetch), `HttpKeys()` (a `FetchKeys` with one client for a service's run) with `opened() -> AbstractAsyncContextManager[HttpKeys]` (the service's resource), `PublishedKeys(policy, *, fetch: FetchKeys = fetch_keys, clock: Callable[[], float] = monotonic)` with `async key(kid: str) -> Key | None`, `TokenVerifier(policy, *, keys: PublishedKeys | None = None, clock: Callable[[], float] = time)` with `async verify(token: str) -> Principal`. Every fetch ignores the environment's proxies, CA bundle and `.netrc` (`trust_env=False`) and follows no redirect. In `ledger_testing.issuer`: `ISSUER = "https://issuer.ledger.test"`, `AUDIENCE = "ledger-test"`, `PROVIDER_ORGANIZATION = "1001"`, `SUBJECT = "2001"`, `ORGANIZATION_CLAIM`, `LocalIssuer(*, issuer=ISSUER, audience=AUDIENCE)` with `classmethod started(*, issuer=ISSUER, audience=AUDIENCE) -> AbstractAsyncContextManager[LocalIssuer]`, `keys_url: str`, `fetches: int`, `failure: tuple[HTTPStatus, bytes] | None`, `delay: float`, `rotate() -> str`, `retire(kid: str) -> None`, `published() -> bytes`, `signing_key(kid: str | None = None) -> RSAKey`, `token(*, subject=SUBJECT, organization=PROVIDER_ORGANIZATION, roles: Iterable[str] = (), claims: Mapping[str, object] | None = None, kid: str | None = None) -> str` (a claim given as `None` is left out).

- [ ] **Step 1: Dependencies and layering**

`libs/platform/pyproject.toml` — add to `dependencies`:

```toml
    "httpx>=0.28.1",
    "joserfc>=1.7.5",
```

`libs/testing/pyproject.toml` — add to `dependencies`:

```toml
    "joserfc>=1.7.5",
    "pydantic>=2.14.1",
```

Root `pyproject.toml`:

```toml
[tool.uv]
required-version = ">=0.13.0"
package = false
constraint-dependencies = ["cryptography>=50.0.2"]  # joserfc's: the first release with CPython 3.15 wheels of its own
```

In the import-linter contract "Production code never imports test tooling", `httpx` leaves the list (the platform fetches JWKS with it; §12 names it as the HTTP client):

```toml
forbidden_modules = ["pytest", "hypothesis", "testcontainers", "ledger_testing"]
```

In the contract "Platform layers", add `authentication` to the infrastructure layer, keeping every module Task 1's re-check found there:

```toml
    "database | tracing | health | logs | authentication",
```

Run: `uv lock && uv sync`
Expected: `joserfc 1.7.5`, `cryptography 50.0.2` and `cffi` installed.

- [ ] **Step 2: Write the failing tests**

`libs/testing/tests/test_issuer.py`:

```python
from http import HTTPStatus

import httpx
import pytest
from joserfc import jwt
from joserfc.jwk import KeySet, KeySetSerialization
from pydantic import TypeAdapter

from ledger_testing.issuer import (
    AUDIENCE,
    ISSUER,
    ORGANIZATION_CLAIM,
    PROVIDER_ORGANIZATION,
    SUBJECT,
    LocalIssuer,
)

JWKS = TypeAdapter(KeySetSerialization)
CLAIMS = TypeAdapter(dict[str, object])
HEADER = TypeAdapter(dict[str, str])


def keys_of(issuer: LocalIssuer) -> KeySet:
    return KeySet.import_key_set(JWKS.validate_json(issuer.published()))


def kids(issuer: LocalIssuer) -> list[str | None]:
    return [key.kid for key in keys_of(issuer)]


def claims_of(issuer: LocalIssuer, token: str) -> dict[str, object]:
    return CLAIMS.validate_python(jwt.decode(token, keys_of(issuer), algorithms=["RS256"]).claims)


async def test_the_keys_are_served_over_http_and_every_request_is_counted() -> None:
    async with LocalIssuer.started() as issuer, httpx.AsyncClient() as client:
        response = await client.get(issuer.keys_url)
        again = await client.get(issuer.keys_url)
    assert (response.status_code, response.content) == (200, issuer.published())
    assert response.headers["content-type"] == "application/json"
    assert again.status_code == 200
    assert issuer.fetches == 2
    assert issuer.keys_url.startswith("http://127.0.0.1:")


async def test_the_endpoint_answers_what_it_is_told_to() -> None:
    async with LocalIssuer.started() as issuer, httpx.AsyncClient() as client:
        issuer.failure = (HTTPStatus.SERVICE_UNAVAILABLE, b"down")
        response = await client.get(issuer.keys_url)
    assert (response.status_code, response.content) == (503, b"down")


async def test_a_request_still_waiting_when_the_issuer_stops_is_dropped() -> None:
    async with LocalIssuer.started() as issuer, httpx.AsyncClient(timeout=0.1) as client:
        issuer.delay = 30.0
        with pytest.raises(httpx.ReadTimeout):
            await client.get(issuer.keys_url)
    assert issuer.fetches == 1


def test_a_token_verifies_with_the_published_keys_and_carries_zitadels_claims() -> None:
    issuer = LocalIssuer()
    claims = claims_of(issuer, issuer.token(roles=("operator",)))
    assert claims["iss"] == ISSUER
    assert claims["aud"] == [AUDIENCE]
    assert claims["sub"] == SUBJECT
    assert claims[ORGANIZATION_CLAIM] == PROVIDER_ORGANIZATION
    assert claims[f"urn:zitadel:iam:org:project:{AUDIENCE}:roles"] == {
        "operator": {PROVIDER_ORGANIZATION: "ledger.test"}
    }
    assert {"exp", "iat", "nbf", "client_id"} <= claims.keys()


def test_claims_can_be_changed_and_left_out() -> None:
    issuer = LocalIssuer()
    claims = claims_of(issuer, issuer.token(claims={"aud": "elsewhere", "sub": None}))
    assert claims["aud"] == "elsewhere"
    assert "sub" not in claims
    assert f"urn:zitadel:iam:org:project:{AUDIENCE}:roles" not in claims


def test_a_new_key_is_published_beside_the_old_one_until_that_one_is_retired() -> None:
    issuer = LocalIssuer()
    assert issuer.rotate() == "key-2"
    assert kids(issuer) == ["key-1", "key-2"]
    issuer.retire("key-1")
    assert kids(issuer) == ["key-2"]
    newest = HEADER.validate_python(jwt.decode(issuer.token(), keys_of(issuer), algorithms=["RS256"]).header)
    assert newest["kid"] == "key-2"


def test_the_signing_key_is_the_newest_unless_another_is_named() -> None:
    issuer = LocalIssuer()
    issuer.rotate()
    assert issuer.signing_key().kid == "key-2"
    assert issuer.signing_key("key-1").kid == "key-1"
    assert issuer.token(kid="key-1").count(".") == 2
```

`libs/platform/tests/test_settings.py` — add these imports (merged with the file's own) and tests:

```python
from typing import Literal

from pydantic import SecretStr, ValidationError

from ledger_platform.settings import ApiSettings

API_DATABASE_URL = SecretStr("postgresql+psycopg://identity:secret@localhost:5432/identity")


def api_settings(
    environment: Literal["local", "test", "staging", "production"], keys_url: str = "http://localhost/keys"
) -> ApiSettings:
    return ApiSettings(
        service_name="identity",
        environment=environment,
        database_url=API_DATABASE_URL,
        token_issuer="https://issuer.example",
        token_audience="ledger",
        token_jwks_url=keys_url,
    )


def test_an_api_service_needs_its_token_policy() -> None:
    with pytest.raises(ValidationError) as caught:
        ApiSettings(service_name="identity", database_url=API_DATABASE_URL)
    assert {error["loc"][0] for error in caught.value.errors()} == {
        "token_issuer",
        "token_audience",
        "token_jwks_url",
    }


def test_the_token_policy_has_safe_defaults() -> None:
    settings = api_settings("local")
    assert (settings.token_algorithms, settings.token_leeway_seconds) == (frozenset({"RS256"}), 60)


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_keys_come_over_https_beyond_a_laptop(environment: Literal["staging", "production"]) -> None:
    with pytest.raises(ValidationError, match="must be https://"):
        api_settings(environment)
    assert (
        api_settings(environment, "https://issuer.example/keys").token_jwks_url
        == "https://issuer.example/keys"
    )


@pytest.mark.parametrize("environment", ["local", "test"])
def test_keys_may_come_over_http_on_a_laptop(environment: Literal["local", "test"]) -> None:
    assert api_settings(environment).token_jwks_url == "http://localhost/keys"


@pytest.mark.parametrize("algorithms", ['["HS256"]', '["none"]', '["RS256", "HS512"]', "[]"])
def test_only_asymmetric_algorithms_can_be_configured(
    monkeypatch: pytest.MonkeyPatch, algorithms: str
) -> None:
    monkeypatch.setenv("LEDGER_TOKEN_ALGORITHMS", algorithms)
    with pytest.raises(ValidationError):
        api_settings("local")


@pytest.mark.parametrize("leeway", ["-1", "301"])
def test_the_leeway_is_at_most_five_minutes(monkeypatch: pytest.MonkeyPatch, leeway: str) -> None:
    monkeypatch.setenv("LEDGER_TOKEN_LEEWAY_SECONDS", leeway)
    with pytest.raises(ValidationError):
        api_settings("local")
```

`libs/platform/tests/test_authentication.py`:

```python
import asyncio
import base64
import json
from http import HTTPStatus
from time import time
from typing import Final
from uuid import NAMESPACE_URL, uuid5
lazy from collections.abc import AsyncGenerator, Callable

import pytest
from hypothesis import given
from hypothesis import strategies as st
from joserfc import jws, jwt
from joserfc.jwk import OctKey
from pydantic import SecretStr
from structlog.testing import capture_logs

from ledger_kernel.errors import ErrorKind
from ledger_platform.authentication import (
    MAX_KEYS_SIZE,
    MAX_TOKEN_LENGTH,
    PROJECT_ROLES_CLAIM,
    HttpKeys,
    InvalidTokenError,
    InvalidTokenOptionError,
    KeysClientClosedError,
    MissingTokenError,
    PublishedKeys,
    TokenKeysUnavailableError,
    TokenPolicy,
    TokenProblem,
    TokenVerifier,
    organization_of,
)
from ledger_platform.settings import ApiSettings
from ledger_testing.clock import Moment
from ledger_testing.issuer import AUDIENCE, ISSUER, ORGANIZATION_CLAIM, PROVIDER_ORGANIZATION, LocalIssuer

KEYS: Final = "http://keys.invalid/keys"
OFFLINE: Final = LocalIssuer()
"""An issuer whose keys a fake fetch hands over: for property tests, which must not wait on sockets."""
OFFLINE_POLICY: Final = TokenPolicy(issuer=ISSUER, audience=AUDIENCE, keys_url=KEYS)
UNREACHABLE_PROXY: Final = "http://127.0.0.1:1"
"""A proxy that refuses every connection: a fetch that went through it would fail."""


async def offline_keys(url: str) -> bytes:
    del url
    return OFFLINE.published()


def offline_verifier() -> TokenVerifier:
    return TokenVerifier(OFFLINE_POLICY, keys=PublishedKeys(OFFLINE_POLICY, fetch=offline_keys))


def segment(value: object) -> str:
    return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()


def encoded(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def unsigned(header: dict[str, str]) -> str:
    """A token with ``header``, an empty payload and a signature of garbage."""
    return f"{segment(header)}.{segment({})}.c2lnbmF0dXJl"


def now() -> int:
    return int(time())


@pytest.fixture
async def issuer() -> AsyncGenerator[LocalIssuer]:
    async with LocalIssuer.started() as started:
        yield started


@pytest.fixture
def policy(issuer: LocalIssuer) -> TokenPolicy:
    return TokenPolicy(issuer=ISSUER, audience=AUDIENCE, keys_url=issuer.keys_url, fetch_timeout=1.0)


@pytest.fixture
def moment() -> Moment:
    return Moment(1_000.0)


@pytest.fixture
def verifier(policy: TokenPolicy, moment: Moment) -> TokenVerifier:
    return TokenVerifier(policy, keys=PublishedKeys(policy, clock=moment))


async def refused(verifier: TokenVerifier, token: str) -> TokenProblem:
    with pytest.raises(InvalidTokenError) as caught:
        await verifier.verify(token)
    assert not token or token not in str(caught.value)
    return caught.value.problem


# ── Accepted ────────────────────────────────────────────────────────────────────


async def test_a_token_of_the_provider_proves_who_calls(issuer: LocalIssuer, verifier: TokenVerifier) -> None:
    token = issuer.token(subject="394763379928989699", organization="394763327332483075")
    principal = await verifier.verify(token)
    assert principal.subject == "394763379928989699"
    assert principal.provider_organization == "394763327332483075"
    assert principal.organization == organization_of("394763327332483075")
    assert principal.roles == frozenset()


async def test_project_roles_granted_in_the_callers_organization_are_read(
    issuer: LocalIssuer, verifier: TokenVerifier
) -> None:
    generic = {"support": {PROVIDER_ORGANIZATION: "ledger.test"}, "auditor": {"9999": "other.test"}}
    token = issuer.token(roles=("operator",), claims={PROJECT_ROLES_CLAIM: generic})
    assert (await verifier.verify(token)).roles == frozenset({"operator", "support"})


async def test_an_audience_among_others_or_alone_is_enough(
    issuer: LocalIssuer, verifier: TokenVerifier
) -> None:
    assert await verifier.verify(issuer.token(claims={"aud": ["zitadel", AUDIENCE]}))
    assert await verifier.verify(issuer.token(claims={"aud": AUDIENCE}))


def test_an_organization_id_is_the_same_wherever_it_is_derived() -> None:
    namespace = uuid5(NAMESPACE_URL, "urn:ledger:organization")
    assert organization_of("1001") == uuid5(namespace, "1001") == organization_of("1001")
    assert organization_of("1001") != organization_of("1002")
    assert organization_of("1001").version == 5


# ── Time ────────────────────────────────────────────────────────────────────────


async def test_an_expired_token_is_refused_beyond_the_leeway(
    issuer: LocalIssuer, verifier: TokenVerifier
) -> None:
    assert await refused(verifier, issuer.token(claims={"exp": now() - 90})) is TokenProblem.EXPIRED
    assert await verifier.verify(issuer.token(claims={"exp": now() - 30}))


@pytest.mark.parametrize("claim", ["nbf", "iat"])
async def test_a_token_from_the_future_is_refused_beyond_the_leeway(
    issuer: LocalIssuer, verifier: TokenVerifier, claim: str
) -> None:
    assert await refused(verifier, issuer.token(claims={claim: now() + 90})) is TokenProblem.NOT_YET_VALID
    assert await verifier.verify(issuer.token(claims={claim: now() + 30}))


async def test_the_leeway_holds_to_its_last_second_and_not_one_more(
    issuer: LocalIssuer, policy: TokenPolicy
) -> None:
    moment = now()
    stopped = TokenVerifier(policy, clock=lambda: moment)
    leeway = int(policy.leeway)
    assert await stopped.verify(issuer.token(claims={"exp": moment - leeway}))
    assert await refused(stopped, issuer.token(claims={"exp": moment - leeway - 1})) is TokenProblem.EXPIRED
    assert await stopped.verify(issuer.token(claims={"nbf": moment + leeway, "iat": moment + leeway}))
    for claim in ("nbf", "iat"):
        late = issuer.token(claims={claim: moment + leeway + 1})
        assert await refused(stopped, late) is TokenProblem.NOT_YET_VALID


async def test_the_leeway_comes_from_the_policy(issuer: LocalIssuer, policy: TokenPolicy) -> None:
    strict = TokenVerifier(
        TokenPolicy(issuer=ISSUER, audience=AUDIENCE, keys_url=policy.keys_url, leeway=0.0)
    )
    assert await refused(strict, issuer.token(claims={"exp": now() - 30})) is TokenProblem.EXPIRED


async def test_the_verifier_reads_the_time_from_its_clock(issuer: LocalIssuer, policy: TokenPolicy) -> None:
    tomorrow = TokenVerifier(policy, clock=lambda: time() + 86_400)
    assert await refused(tomorrow, issuer.token()) is TokenProblem.EXPIRED


# ── Claims ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("claims", "problem"),
    [
        ({"iss": "https://impostor.example"}, TokenProblem.ISSUER),
        ({"aud": ["someone-else"]}, TokenProblem.AUDIENCE),
        ({"exp": None}, TokenProblem.CLAIMS),
        ({"exp": "tomorrow"}, TokenProblem.CLAIMS),
        ({"sub": None}, TokenProblem.CLAIMS),
        ({"sub": 2001}, TokenProblem.CLAIMS),
        ({"sub": "two thousand one"}, TokenProblem.CLAIMS),
        ({ORGANIZATION_CLAIM: None}, TokenProblem.CLAIMS),
        ({ORGANIZATION_CLAIM: "10 01"}, TokenProblem.CLAIMS),
        ({ORGANIZATION_CLAIM: 1001}, TokenProblem.CLAIMS),
        ({PROJECT_ROLES_CLAIM: ["operator"]}, TokenProblem.CLAIMS),
    ],
    ids=[
        "issuer",
        "audience",
        "no-exp",
        "text-exp",
        "no-sub",
        "number-sub",
        "spaced-sub",
        "no-organization",
        "spaced-organization",
        "number-organization",
        "roles-as-a-list",
    ],
)
async def test_a_token_with_wrong_or_missing_claims_is_refused(
    issuer: LocalIssuer, verifier: TokenVerifier, claims: dict[str, object], problem: TokenProblem
) -> None:
    assert await refused(verifier, issuer.token(claims=claims)) is problem


async def test_a_refusal_says_what_was_wrong_in_a_word(issuer: LocalIssuer, verifier: TokenVerifier) -> None:
    with pytest.raises(InvalidTokenError) as caught:
        await verifier.verify(issuer.token(claims={"exp": now() - 600}))
    assert str(caught.value) == "the access token was refused: expired"
    assert (caught.value.kind, caught.value.code) == (ErrorKind.UNAUTHENTICATED, "invalid_token")


def test_no_token_at_all_is_an_error_of_its_own() -> None:
    error = MissingTokenError()
    assert (error.kind, error.code, str(error)) == (
        ErrorKind.UNAUTHENTICATED,
        "missing_token",
        "a bearer access token is required",
    )


# ── Algorithms and signatures ──────────────────────────────────────────────────


async def test_an_unsigned_token_is_refused_before_any_key_is_fetched(
    issuer: LocalIssuer, verifier: TokenVerifier
) -> None:
    assert await refused(verifier, unsigned({"alg": "none", "kid": "key-1"})) is TokenProblem.ALGORITHM
    assert issuer.fetches == 0


async def test_an_hmac_token_keyed_with_the_public_key_is_refused(
    issuer: LocalIssuer, verifier: TokenVerifier
) -> None:
    secret = OctKey.import_key(issuer.published())
    forged = jwt.encode(
        {"alg": "HS256", "kid": "key-1"}, {"iss": ISSUER, "aud": AUDIENCE, "sub": "1"}, secret
    )
    assert await refused(verifier, forged) is TokenProblem.ALGORITHM


async def test_an_algorithm_the_policy_does_not_allow_is_refused(
    issuer: LocalIssuer, policy: TokenPolicy
) -> None:
    only_es256 = TokenPolicy(
        issuer=ISSUER, audience=AUDIENCE, keys_url=policy.keys_url, algorithms=frozenset({"ES256"})
    )
    assert await refused(TokenVerifier(only_es256), issuer.token()) is TokenProblem.ALGORITHM


async def test_a_tampered_token_fails_its_signature(issuer: LocalIssuer, verifier: TokenVerifier) -> None:
    header, _, signature = issuer.token().split(".")
    claims = {"iss": ISSUER, "aud": AUDIENCE, "sub": "root", "exp": now() + 60, ORGANIZATION_CLAIM: "1"}
    assert await refused(verifier, f"{header}.{segment(claims)}.{signature}") is TokenProblem.SIGNATURE


async def test_a_signed_payload_that_is_not_a_json_object_is_malformed(
    issuer: LocalIssuer, verifier: TokenVerifier
) -> None:
    array = jws.serialize_compact({"alg": "RS256", "kid": "key-1"}, b"[1, 2, 3]", issuer.signing_key())
    assert await refused(verifier, array) is TokenProblem.MALFORMED


async def test_a_signed_payload_that_is_not_json_is_malformed(
    issuer: LocalIssuer, verifier: TokenVerifier
) -> None:
    token = jws.serialize_compact({"alg": "RS256", "kid": "key-1"}, b"not json", issuer.signing_key())
    assert await refused(verifier, token) is TokenProblem.MALFORMED


@pytest.mark.parametrize(
    "token",
    ["", "not-a-token", "a.b", "a.b.c.d", "é.e30.", "!!!.e30.sig", segment({"alg": "RS256"}) + ".e30.sig"],
    ids=["empty", "word", "two-parts", "four-parts", "non-ascii", "bad-base64", "no-kid"],
)
async def test_text_that_is_not_a_signed_jwt_is_malformed(
    issuer: LocalIssuer, verifier: TokenVerifier, token: str
) -> None:
    assert await refused(verifier, token) is TokenProblem.MALFORMED
    assert issuer.fetches == 0


async def test_a_huge_token_is_refused_unread(issuer: LocalIssuer, verifier: TokenVerifier) -> None:
    huge = issuer.token(claims={"padding": "x" * MAX_TOKEN_LENGTH})
    assert await refused(verifier, huge) is TokenProblem.MALFORMED
    assert issuer.fetches == 0


@given(st.text(max_size=400))
async def test_no_text_makes_the_verifier_fail_otherwise_than_by_refusing_it(text: str) -> None:
    with pytest.raises(InvalidTokenError):
        await offline_verifier().verify(text)


@given(st.binary(max_size=200), st.binary(max_size=200))
async def test_no_payload_or_signature_under_a_valid_header_breaks_the_verifier(
    payload: bytes, signature: bytes
) -> None:
    token = f"{segment({'alg': 'RS256', 'kid': 'key-1'})}.{encoded(payload)}.{encoded(signature)}"
    with pytest.raises(InvalidTokenError):
        await offline_verifier().verify(token)


# ── Keys ────────────────────────────────────────────────────────────────────────


async def test_the_keys_are_fetched_once_and_kept(issuer: LocalIssuer, verifier: TokenVerifier) -> None:
    for _ in range(5):
        await verifier.verify(issuer.token())
    assert issuer.fetches == 1


async def test_the_keys_are_fetched_again_once_they_are_old(
    issuer: LocalIssuer, verifier: TokenVerifier, moment: Moment, policy: TokenPolicy
) -> None:
    await verifier.verify(issuer.token())
    moment.now += policy.keys_ttl
    await verifier.verify(issuer.token())
    assert issuer.fetches == 2


async def test_a_key_the_copy_lacks_refreshes_it_but_no_more_than_once_per_floor(
    issuer: LocalIssuer, verifier: TokenVerifier, moment: Moment, policy: TokenPolicy
) -> None:
    await verifier.verify(issuer.token())
    moment.now += policy.refresh_floor
    issuer.rotate()
    assert await verifier.verify(issuer.token())  # signed with the key just published
    assert issuer.fetches == 2
    for number in range(20):
        assert await refused(verifier, unsigned({"alg": "RS256", "kid": f"invented-{number}"})) is (
            TokenProblem.UNKNOWN_KEY
        )
    assert issuer.fetches == 2  # within the floor: invented ids cost nothing
    moment.now += policy.refresh_floor
    assert (
        await refused(verifier, unsigned({"alg": "RS256", "kid": "invented-again"}))
        is TokenProblem.UNKNOWN_KEY
    )
    assert issuer.fetches == 3


async def test_a_retired_key_stops_working_at_the_next_refresh(
    issuer: LocalIssuer, verifier: TokenVerifier, moment: Moment, policy: TokenPolicy
) -> None:
    old = issuer.token()
    assert await verifier.verify(old)
    issuer.rotate()
    issuer.retire("key-1")
    moment.now += policy.keys_ttl
    assert await refused(verifier, old) is TokenProblem.UNKNOWN_KEY


async def test_without_keys_no_token_can_be_checked_and_the_provider_is_not_hammered(
    issuer: LocalIssuer, verifier: TokenVerifier, moment: Moment, policy: TokenPolicy
) -> None:
    issuer.failure = (HTTPStatus.INTERNAL_SERVER_ERROR, b"oops")
    with capture_logs() as logs, pytest.raises(TokenKeysUnavailableError):
        await verifier.verify(issuer.token())
    with pytest.raises(TokenKeysUnavailableError) as caught:
        await verifier.verify(issuer.token())
    assert issuer.fetches == 1
    assert (caught.value.kind, caught.value.code) == (ErrorKind.UNAVAILABLE, "token_keys_unavailable")
    assert [log["event"] for log in logs] == ["tokens.keys_unavailable"]
    issuer.failure = None
    moment.now += policy.refresh_floor
    assert await verifier.verify(issuer.token())


async def test_the_keys_held_keep_working_while_the_provider_is_down(
    issuer: LocalIssuer, verifier: TokenVerifier, moment: Moment, policy: TokenPolicy
) -> None:
    await verifier.verify(issuer.token())
    issuer.failure = (HTTPStatus.SERVICE_UNAVAILABLE, b"")
    moment.now += policy.keys_ttl
    with capture_logs():
        assert await verifier.verify(issuer.token())
    assert issuer.fetches == 2


@pytest.mark.parametrize(
    "answer",
    [
        (HTTPStatus.OK, b"not json"),
        (HTTPStatus.OK, b'{"keys": []}'),
        (HTTPStatus.OK, b'{"keys": [{"kty": "RSA", "n": "AQAB"}]}'),
        (HTTPStatus.OK, b'{"keys": [' + b" " * MAX_KEYS_SIZE + b"]}"),
        (HTTPStatus.FOUND, b""),
    ],
    ids=["garbage", "empty", "broken-key", "too-large", "redirect"],
)
async def test_a_keys_document_that_cannot_be_used_is_no_keys(
    issuer: LocalIssuer, verifier: TokenVerifier, answer: tuple[HTTPStatus, bytes]
) -> None:
    issuer.failure = answer
    with capture_logs(), pytest.raises(TokenKeysUnavailableError):
        await verifier.verify(issuer.token())


async def test_a_provider_that_does_not_answer_in_time_is_no_keys(
    issuer: LocalIssuer, policy: TokenPolicy
) -> None:
    issuer.delay = 30.0
    impatient = TokenPolicy(issuer=ISSUER, audience=AUDIENCE, keys_url=policy.keys_url, fetch_timeout=0.1)
    with capture_logs(), pytest.raises(TokenKeysUnavailableError):
        await TokenVerifier(impatient).verify(issuer.token())


async def test_requests_that_find_the_keys_stale_together_share_one_fetch(
    issuer: LocalIssuer, verifier: TokenVerifier
) -> None:
    async with asyncio.TaskGroup() as group:
        for _ in range(10):
            group.create_task(verifier.verify(issuer.token()))
    assert issuer.fetches == 1


async def test_a_service_fetches_with_one_client_held_for_its_run(
    issuer: LocalIssuer, policy: TokenPolicy, moment: Moment
) -> None:
    keys = HttpKeys()
    async with keys.opened() as fetch:
        verifier = TokenVerifier(policy, keys=PublishedKeys(policy, fetch=fetch, clock=moment))
        assert await verifier.verify(issuer.token())
        moment.now += policy.keys_ttl
        assert await verifier.verify(issuer.token())
    assert issuer.fetches == 2
    with pytest.raises(KeysClientClosedError) as caught:
        await keys(issuer.keys_url)
    assert (caught.value.kind, caught.value.code) == (ErrorKind.INTERNAL, "keys_client_closed")


async def test_the_environment_s_proxies_do_not_reach_the_key_fetch(
    issuer: LocalIssuer, policy: TokenPolicy, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.setenv(name, UNREACHABLE_PROXY)
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    assert await TokenVerifier(policy).verify(issuer.token())
    async with HttpKeys().opened() as fetch:
        assert await fetch(issuer.keys_url) == issuer.published()


# ── Policy ──────────────────────────────────────────────────────────────────────


def test_the_policy_comes_from_the_settings() -> None:
    settings = ApiSettings(
        service_name="identity",
        database_url=SecretStr("postgresql+psycopg://identity:secret@localhost/identity"),
        token_issuer=ISSUER,
        token_audience=AUDIENCE,
        token_jwks_url="http://localhost/keys",
        token_leeway_seconds=10,
    )
    expected = TokenPolicy(issuer=ISSUER, audience=AUDIENCE, keys_url="http://localhost/keys", leeway=10.0)
    assert TokenPolicy.of(settings) == expected


@pytest.mark.parametrize(
    ("build", "option"),
    [
        (lambda: TokenPolicy(issuer="", audience=AUDIENCE, keys_url=KEYS), "issuer"),
        (lambda: TokenPolicy(issuer=ISSUER, audience="", keys_url=KEYS), "audience"),
        (lambda: TokenPolicy(issuer=ISSUER, audience=AUDIENCE, keys_url=""), "keys_url"),
        (
            lambda: TokenPolicy(issuer=ISSUER, audience=AUDIENCE, keys_url=KEYS, algorithms=frozenset()),
            "algorithms",
        ),
        (
            lambda: TokenPolicy(
                issuer=ISSUER, audience=AUDIENCE, keys_url=KEYS, algorithms=frozenset({"HS256"})
            ),
            "algorithms",
        ),
        (
            lambda: TokenPolicy(
                issuer=ISSUER, audience=AUDIENCE, keys_url=KEYS, algorithms=frozenset({"none"})
            ),
            "algorithms",
        ),
        (lambda: TokenPolicy(issuer=ISSUER, audience=AUDIENCE, keys_url=KEYS, leeway=-1.0), "leeway"),
        (lambda: TokenPolicy(issuer=ISSUER, audience=AUDIENCE, keys_url=KEYS, leeway=301.0), "leeway"),
        (lambda: TokenPolicy(issuer=ISSUER, audience=AUDIENCE, keys_url=KEYS, leeway=float("nan")), "leeway"),
        (lambda: TokenPolicy(issuer=ISSUER, audience=AUDIENCE, keys_url=KEYS, keys_ttl=0.0), "keys_ttl"),
        (
            lambda: TokenPolicy(issuer=ISSUER, audience=AUDIENCE, keys_url=KEYS, refresh_floor=float("inf")),
            "refresh_floor",
        ),
        (
            lambda: TokenPolicy(issuer=ISSUER, audience=AUDIENCE, keys_url=KEYS, fetch_timeout=-1.0),
            "fetch_timeout",
        ),
    ],
)
def test_a_policy_that_cannot_work_is_refused(build: Callable[[], TokenPolicy], option: str) -> None:
    with pytest.raises(InvalidTokenOptionError, match=f"token option {option} must be"):
        build()
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest libs/testing/tests/test_issuer.py libs/platform/tests/test_authentication.py libs/platform/tests/test_settings.py -q --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'ledger_testing.issuer'` and `ImportError: cannot import name 'ApiSettings'`.

- [ ] **Step 4: Implement**

`libs/testing/src/ledger_testing/issuer.py`:

```python
"""A local OpenID provider for tests: RSA keys, their JWKS served on 127.0.0.1, access tokens on demand.

The tokens look like the ones Zitadel v4 issues to a machine user (``iss``, ``aud``, ``sub``, ``exp``,
``iat``, ``nbf``, ``client_id``, the resource owner's organization id and, when asked, project roles), signed
RS256 with a key the JWKS document publishes. A test can rotate and retire keys, make the endpoint fail or
stall, and count how often it was asked.
"""

import asyncio
import contextlib
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from typing import Final, final
lazy from collections.abc import AsyncGenerator, Iterable, Mapping

from joserfc import jwt
from joserfc.jwk import RSAKey
from pydantic import TypeAdapter

ISSUER: Final = "https://issuer.ledger.test"
AUDIENCE: Final = "ledger-test"
PROVIDER_ORGANIZATION: Final = "1001"
SUBJECT: Final = "2001"
ORGANIZATION_CLAIM: Final = "urn:zitadel:iam:user:resourceowner:id"
_KEY_SIZE: Final = 2048
_LIFETIME: Final = timedelta(minutes=5)
_END_OF_HEAD: Final = b"\r\n\r\n"
_ADDRESS: Final = TypeAdapter(tuple[str, int] | tuple[str, int, int, int])
"""An internet address as a socket reports it: host and port, plus flow and scope for IPv6."""


def _answer_of(status: HTTPStatus, body: bytes) -> bytes:
    head = (
        f"HTTP/1.1 {status.value} {status.phrase}\r\nContent-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n"
    )
    return head.encode("ascii") + body


@final
class LocalIssuer:
    """An identity provider as a resource server sees one: published keys, and tokens signed with them."""

    __slots__ = (
        "_current",
        "_handlers",
        "_keys",
        "_port",
        "_published",
        "audience",
        "delay",
        "failure",
        "fetches",
        "issuer",
    )

    def __init__(self, *, issuer: str = ISSUER, audience: str = AUDIENCE) -> None:
        self.issuer = issuer
        self.audience = audience
        self.fetches = 0
        """How many times the JWKS document was asked for."""
        self.failure: tuple[HTTPStatus, bytes] | None = None
        """What the endpoint answers instead of the keys while it is set."""
        self.delay = 0.0
        """Seconds the endpoint waits before it answers."""
        self._keys: dict[str, RSAKey] = {}
        self._published: list[str] = []
        self._handlers: set[asyncio.Task[None]] = set()
        self._port = 0
        self._current = self.rotate()

    @classmethod
    @asynccontextmanager
    async def started(cls, *, issuer: str = ISSUER, audience: str = AUDIENCE) -> AsyncGenerator[LocalIssuer]:
        """An issuer whose JWKS endpoint listens on 127.0.0.1 for the length of the block."""
        local = cls(issuer=issuer, audience=audience)
        server = await asyncio.start_server(local._accept, "127.0.0.1", 0)
        local._port = _ADDRESS.validate_python(server.sockets[0].getsockname())[1]
        try:
            yield local
        finally:
            server.close()
            server.close_clients()
            pending = tuple(local._handlers)
            for handler in pending:
                handler.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            await server.wait_closed()

    @property
    def keys_url(self) -> str:
        """Where the JWKS document is served (once ``started``)."""
        return f"http://127.0.0.1:{self._port}/keys"

    def rotate(self) -> str:
        """Make a new key, publish it beside the others and sign with it from now on; its id."""
        kid = f"key-{len(self._keys) + 1}"
        self._keys[kid] = RSAKey.generate_key(
            _KEY_SIZE, {"kid": kid, "use": "sig", "alg": "RS256"}, private=True
        )
        self._published.append(kid)
        self._current = kid
        return kid

    def retire(self, kid: str) -> None:
        """Stop publishing ``kid``. Tokens can still be signed with it, as by anyone who kept it."""
        self._published.remove(kid)

    def published(self) -> bytes:
        """The JWKS document: the public half of every published key."""
        keys = [self._keys[kid].as_dict(private=False) for kid in self._published]
        return json.dumps({"keys": keys}).encode()

    def signing_key(self, kid: str | None = None) -> RSAKey:
        """The private key ``kid`` (the newest by default), for a test that signs something unusual."""
        return self._keys[kid or self._current]

    def token(
        self,
        *,
        subject: str = SUBJECT,
        organization: str = PROVIDER_ORGANIZATION,
        roles: Iterable[str] = (),
        claims: Mapping[str, object] | None = None,
        kid: str | None = None,
    ) -> str:
        """An access token for ``subject`` of ``organization``, valid for five minutes from now.

        ``roles`` are project roles granted in that organization. ``claims`` replace or add claims; one set
        to ``None`` is left out. ``kid`` signs with that key instead of the newest.
        """
        issued = int(datetime.now(UTC).timestamp())
        payload: dict[str, object] = {
            "iss": self.issuer,
            "aud": [self.audience],
            "sub": subject,
            "client_id": "ledger-test",
            "iat": issued,
            "nbf": issued,
            "exp": issued + int(_LIFETIME.total_seconds()),
            ORGANIZATION_CLAIM: organization,
        }
        granted = tuple(roles)
        if granted:
            payload[f"urn:zitadel:iam:org:project:{self.audience}:roles"] = {
                role: {organization: "ledger.test"} for role in granted
            }
        payload.update(claims or {})
        kept = {name: value for name, value in payload.items() if value is not None}
        return jwt.encode(
            {"alg": "RS256", "kid": kid or self._current, "typ": "JWT"}, kept, self.signing_key(kid)
        )

    def _accept(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        handler = asyncio.create_task(self._answer(reader, writer))
        self._handlers.add(handler)
        handler.add_done_callback(self._handlers.discard)

    async def _answer(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Answer one request with the keys (or the failure), whatever was asked, then close."""
        with contextlib.suppress(ConnectionError, asyncio.IncompleteReadError):
            await reader.readuntil(_END_OF_HEAD)
            self.fetches += 1
            await asyncio.sleep(self.delay)
            status, body = self.failure or (HTTPStatus.OK, self.published())
            writer.write(_answer_of(status, body))
            await writer.drain()
        writer.close()
        with contextlib.suppress(ConnectionError):
            await writer.wait_closed()
```

`libs/platform/src/ledger_platform/settings.py` — add `Self` to the `typing` imports and `model_validator` to the `pydantic` imports, then append:

```python
TokenAlgorithm = Literal[
    "RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512", "EdDSA"
]
"""Signature algorithms a service may accept: asymmetric only, so ``none`` and HMAC cannot be set.

An ordinary alias, not a ``type`` statement: the verifier reads its values at runtime (``get_args``), and the
value of a ``type`` alias is typed ``Any``, which the type checkers refuse."""

_HTTPS_ENVIRONMENTS: Final = frozenset({"staging", "production"})


class _InsecureKeysUrlError(ValueError):
    """Keys fetched in clear could be swapped on the way, and every token then forged."""

    def __init__(self) -> None:
        super().__init__("token_jwks_url must be https:// outside the local and test environments")


class ApiSettings(ServiceSettings):
    """Settings of a service with a public API: the chassis's, and the bearer-token policy (§8)."""

    token_issuer: str = Field(default=..., min_length=1, max_length=2048)
    """``iss`` of the tokens: the identity provider's issuer, e.g. ``https://auth.example.com``."""
    token_audience: str = Field(default=..., min_length=1, max_length=255)
    """A value ``aud`` must hold: the Zitadel project id of the platform's API."""
    token_jwks_url: str = Field(default=..., min_length=1, max_length=2048)
    """Where the provider publishes its keys: ``<issuer>/oauth/v2/keys`` for Zitadel."""
    token_algorithms: frozenset[TokenAlgorithm] = Field(default=frozenset({"RS256"}), min_length=1)
    token_leeway_seconds: Annotated[int, Field(ge=0, le=300)] = 60
    """How far the provider's clock and ours may disagree, for ``exp``, ``nbf`` and ``iat``."""

    @model_validator(mode="after")
    def require_https_keys_beyond_a_laptop(self) -> Self:
        """Refuse keys fetched in clear in staging and production."""
        if self.environment in _HTTPS_ENVIRONMENTS and not self.token_jwks_url.startswith("https://"):
            raise _InsecureKeysUrlError
        return self
```

`libs/platform/src/ledger_platform/authentication.py`:

```python
"""Bearer tokens: OAuth 2.0 access tokens (JWT) checked against the identity provider's published keys.

The gateway checks tokens too; a service checks them again (defense in depth) and reads from them who calls.
A token is accepted only when all of this holds:

* it is a JWS in compact form of at most ``MAX_TOKEN_LENGTH`` ASCII characters;
* its header names an algorithm the policy allows, all of them asymmetric (``none`` and the HMAC family
  cannot be configured, so a token "signed" with the public key as an HMAC secret is refused), and a key id
  (``kid``) the provider publishes;
* the signature verifies with that key;
* ``iss`` is the policy's issuer and ``aud`` holds its audience;
* ``exp`` has not passed, and ``nbf`` and ``iat`` are not ahead, each give or take ``leeway`` seconds;
* ``sub`` and the organization claim are there and well formed.

Keys come from the provider's JWKS document and are kept for ``keys_ttl`` seconds. A token whose key id the
copy lacks refreshes it at once (the provider rotated its keys), but never sooner than ``refresh_floor``
seconds after the last attempt: a stream of tokens with invented key ids costs one fetch per floor, not one
per token. Requests that find the keys stale together share one fetch. When a refresh fails, the keys
already held keep working; with none at all a token cannot be checked and the request gets a 503.

A service fetches with ``HttpKeys``: one HTTP client for the service's run, a resource the service opens at
start and closes at stop. ``fetch_keys``, a client per fetch, is for tools and tests that run no service.
Either way the environment cannot steer the fetch (no proxy, CA bundle or ``.netrc`` from it) and no redirect
is followed: the keys are where the configuration says.

A refused token is an ``InvalidTokenError`` that names the problem in a word (``expired``, ``audience``...).
Neither the token nor its claims are ever echoed or logged.
"""

import asyncio
import base64
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import StrEnum
from time import monotonic, time
from typing import ClassVar, Final, final, get_args
from uuid import NAMESPACE_URL, uuid5
lazy from collections.abc import AsyncGenerator, Awaitable, Callable, Iterator, Mapping

import httpx
import structlog
from joserfc import jwt
from joserfc.errors import (
    BadSignatureError,
    ExpiredTokenError,
    InvalidClaimError,
    JoseError,
    MissingClaimError,
)
from joserfc.jwk import KeySet, KeySetSerialization
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError
lazy from joserfc.jwk import Key

from ledger_kernel.access import InvalidSubjectError, Principal, parse_subject
from ledger_kernel.errors import LedgerError, UnauthenticatedError, UnavailableError
from ledger_kernel.ids import OrganizationId
from ledger_platform.options import InvalidOptionError, require_seconds
from ledger_platform.settings import TokenAlgorithm
lazy from ledger_platform.settings import ApiSettings

ASYMMETRIC_ALGORITHMS: Final = frozenset[str](get_args(TokenAlgorithm))
"""Every algorithm a token may be verified with: the ones the settings offer, all of them asymmetric."""
MAX_TOKEN_LENGTH: Final = 8192
"""Longer than any access token Zitadel issues; a longer one is refused unread."""
MAX_KEYS_SIZE: Final = 64 * 1024
"""The largest JWKS document read: a provider publishes a handful of keys."""
MAX_LEEWAY: Final = 300.0
ORGANIZATION_CLAIM: Final = "urn:zitadel:iam:user:resourceowner:id"
"""The caller's organization (asked for with the scope ``urn:zitadel:iam:user:resourceowner``)."""
PROJECT_ROLES_CLAIM: Final = "urn:zitadel:iam:org:project:roles"
"""Project roles, ``{role: {organization id: domain}}``; the per-project claim has the same shape."""
_RS256: Final = frozenset({"RS256"})
_DOTS: Final = 2
"""A compact JWS has three parts: header, payload, signature."""
_ORGANIZATIONS: Final = uuid5(NAMESPACE_URL, "urn:ledger:organization")
_logger = structlog.stdlib.get_logger(__name__)

type FetchKeys = Callable[[str], Awaitable[bytes]]
"""Fetches the JWKS document at a URL; ``PublishedKeys`` bounds the whole fetch. A test passes its own."""


class TokenProblem(StrEnum):
    """Why a token was refused, in a word a client can act on."""

    MALFORMED = "malformed"
    ALGORITHM = "algorithm"
    UNKNOWN_KEY = "unknown_key"
    SIGNATURE = "signature"
    EXPIRED = "expired"
    NOT_YET_VALID = "not_yet_valid"
    ISSUER = "issuer"
    AUDIENCE = "audience"
    CLAIMS = "claims"


_PROBLEM_OF_CLAIM: Final[Mapping[str, TokenProblem]] = {
    "iss": TokenProblem.ISSUER,
    "aud": TokenProblem.AUDIENCE,
    "nbf": TokenProblem.NOT_YET_VALID,
    "iat": TokenProblem.NOT_YET_VALID,
}


class MissingTokenError(UnauthenticatedError):
    """The request carries no bearer token."""

    code: ClassVar[str] = "missing_token"

    def __init__(self) -> None:
        super().__init__("a bearer access token is required")


class InvalidTokenError(UnauthenticatedError):
    """The bearer token cannot be trusted; ``problem`` says why, and the token is never repeated."""

    code: ClassVar[str] = "invalid_token"

    problem: TokenProblem

    def __init__(self, problem: TokenProblem) -> None:
        super().__init__(f"the access token was refused: {problem}")
        self.problem = problem


class TokenKeysUnavailableError(UnavailableError):
    """The provider's keys could not be fetched and none are held: no token can be checked now."""

    code: ClassVar[str] = "token_keys_unavailable"

    def __init__(self) -> None:
        super().__init__("the identity provider's signing keys could not be fetched")


class KeysClientClosedError(LedgerError):
    """``HttpKeys`` was asked to fetch while its client was not open: a service that never opened it."""

    code: ClassVar[str] = "keys_client_closed"

    def __init__(self) -> None:
        super().__init__("the client that fetches the provider's keys is not open")


class InvalidTokenOptionError(InvalidOptionError):
    """A token policy that cannot work."""

    code: ClassVar[str] = "invalid_token_option"
    label: ClassVar[str] = "token option"


class _KeysTooLargeError(ValueError):
    def __init__(self) -> None:
        super().__init__(f"the JWKS document is larger than {MAX_KEYS_SIZE} bytes")


def organization_of(provider_organization: str) -> OrganizationId:
    """The ledger id of the provider's organization: derived, so every service computes the same one."""
    return OrganizationId(uuid5(_ORGANIZATIONS, provider_organization))


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class TokenPolicy:
    """What a token must be to be accepted (see the module). Durations are in seconds."""

    issuer: str
    audience: str
    keys_url: str
    algorithms: frozenset[str] = _RS256
    leeway: float = 60.0
    keys_ttl: float = 600.0
    refresh_floor: float = 10.0
    fetch_timeout: float = 5.0

    def __post_init__(self) -> None:
        for option, value in (
            ("issuer", self.issuer),
            ("audience", self.audience),
            ("keys_url", self.keys_url),
        ):
            if not value:
                raise InvalidTokenOptionError(option=option, requirement="not empty", value=value)
        if not self.algorithms or not self.algorithms <= ASYMMETRIC_ALGORITHMS:
            raise InvalidTokenOptionError(
                option="algorithms",
                requirement=f"some of {', '.join(sorted(ASYMMETRIC_ALGORITHMS))}",
                value=sorted(self.algorithms),
            )
        if not 0 <= self.leeway <= MAX_LEEWAY:  # NaN included
            raise InvalidTokenOptionError(
                option="leeway", requirement=f"between 0 and {MAX_LEEWAY} seconds", value=self.leeway
            )
        require_seconds(InvalidTokenOptionError, "keys_ttl", self.keys_ttl)
        require_seconds(InvalidTokenOptionError, "refresh_floor", self.refresh_floor)
        require_seconds(InvalidTokenOptionError, "fetch_timeout", self.fetch_timeout)

    @classmethod
    def of(cls, settings: ApiSettings) -> TokenPolicy:
        """The policy a service's settings describe."""
        return cls(
            issuer=settings.token_issuer,
            audience=settings.token_audience,
            keys_url=settings.token_jwks_url,
            algorithms=frozenset(settings.token_algorithms),
            leeway=float(settings.token_leeway_seconds),
        )


def _client() -> httpx.AsyncClient:
    """A client that follows no redirect and takes nothing from the environment: proxies, CA bundle, netrc."""
    return httpx.AsyncClient(follow_redirects=False, trust_env=False)


async def _fetch(client: httpx.AsyncClient, url: str) -> bytes:
    """The JWKS document at ``url``: an error for any answer but 200 or a body over ``MAX_KEYS_SIZE`` bytes.

    The caller bounds the whole fetch, from connecting to the last byte (``PublishedKeys`` gives it
    ``fetch_timeout``): a provider that drips its answer cannot hold a request for longer.
    """
    body = bytearray()
    async with client.stream("GET", url, headers={"Accept": "application/json"}) as response:
        response.raise_for_status()
        async for chunk in response.aiter_bytes():
            body += chunk
            if len(body) > MAX_KEYS_SIZE:
                raise _KeysTooLargeError
    return bytes(body)


async def fetch_keys(url: str) -> bytes:
    """The JWKS document at ``url``, with a client of its own: for tools and tests that run no service."""
    async with _client() as client:
        return await _fetch(client, url)


@final
class HttpKeys:
    """Fetches JWKS documents with one HTTP client, open for a service's run: a resource of the service.

    The service enters ``opened()`` when it starts and leaves it when it stops (``Service``'s resources hook),
    which closes the client and its connections in order. A fetch while it is not open raises
    ``KeysClientClosedError``, which ``PublishedKeys`` treats as any failed fetch.
    """

    __slots__ = ("_client",)

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    @asynccontextmanager
    async def opened(self) -> AsyncGenerator[HttpKeys]:
        """This fetcher, its client open for the length of the block."""
        async with _client() as client:
            self._client = client
            try:
                yield self
            finally:
                self._client = None

    async def __call__(self, url: str) -> bytes:
        """The JWKS document at ``url``, as ``fetch_keys`` reads it."""
        client = self._client
        if client is None:
            raise KeysClientClosedError
        return await _fetch(client, url)


_JWKS: Final = TypeAdapter(KeySetSerialization)


def _parse(document: bytes) -> dict[str, Key]:
    """The keys of a JWKS document by id. A document without a key that can be read is an error."""
    return {
        key.kid: key for key in KeySet.import_key_set(_JWKS.validate_json(document)) if key.kid is not None
    }


@final
class PublishedKeys:
    """The provider's signing keys by id, fetched and refreshed as the module describes."""

    __slots__ = ("_clock", "_fetch", "_fetched_at", "_keys", "_lock", "_policy", "_tried_at")

    def __init__(
        self, policy: TokenPolicy, *, fetch: FetchKeys = fetch_keys, clock: Callable[[], float] = monotonic
    ) -> None:
        self._policy = policy
        self._fetch = fetch
        self._clock = clock
        self._keys: Mapping[str, Key] = {}
        self._fetched_at: float | None = None
        self._tried_at: float | None = None
        self._lock = asyncio.Lock()

    async def key(self, kid: str) -> Key | None:
        """The published key ``kid`` or ``None``; ``TokenKeysUnavailableError`` while no key was ever had."""
        if self._stale():
            await self._refresh()
        if kid not in self._keys and self._may_try():
            await self._refresh()
        if self._fetched_at is None:
            raise TokenKeysUnavailableError
        return self._keys.get(kid)

    def _stale(self) -> bool:
        fetched = self._fetched_at
        expired = fetched is None or self._clock() - fetched >= self._policy.keys_ttl
        return expired and self._may_try()

    def _may_try(self) -> bool:
        tried = self._tried_at
        return tried is None or self._clock() - tried >= self._policy.refresh_floor

    async def _refresh(self) -> None:
        async with self._lock:
            if not self._may_try():
                return  # another request refreshed while this one waited for the lock
            self._tried_at = self._clock()
            try:
                async with asyncio.timeout(self._policy.fetch_timeout):
                    document = await self._fetch(self._policy.keys_url)
                keys = _parse(document)
            except Exception:
                _logger.exception("tokens.keys_unavailable", url=self._policy.keys_url)
                return
            self._keys = keys
            self._fetched_at = self._tried_at


class _Header(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", frozen=True, strict=True)

    alg: str = Field(max_length=16)
    kid: str = Field(min_length=1, max_length=255)


class _Claims(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", frozen=True, strict=True)

    sub: str = Field(min_length=1)
    organization: str = Field(alias=ORGANIZATION_CLAIM, pattern=r"^[A-Za-z0-9_-]{1,64}$")


_OBJECT: Final = TypeAdapter(dict[str, object])
_ROLES: Final = TypeAdapter(dict[str, dict[str, str]])


def _header_of(token: str) -> _Header:
    """The JOSE header of a compact JWS, read before anything is verified: which algorithm, which key."""
    if not token.isascii() or len(token) > MAX_TOKEN_LENGTH or token.count(".") != _DOTS:
        raise InvalidTokenError(TokenProblem.MALFORMED)
    encoded = token.partition(".")[0]
    try:
        return _Header.model_validate_json(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
    except ValueError as error:  # binascii.Error and ValidationError are both ValueErrors
        raise InvalidTokenError(TokenProblem.MALFORMED) from error


def _granted_roles(claims: Mapping[str, object], names: tuple[str, ...], organization: str) -> Iterator[str]:
    """The roles the role claims ``names`` grant in ``organization``."""
    for name in names:
        roles = _ROLES.validate_python(claims.get(name, {}))
        yield from (role for role, organizations in roles.items() if organization in organizations)


@final
class TokenVerifier:
    """Turns a bearer token into the ``Principal`` it proves, or refuses it (see the module)."""

    __slots__ = ("_claims", "_keys", "_policy", "_role_claims")

    def __init__(
        self, policy: TokenPolicy, *, keys: PublishedKeys | None = None, clock: Callable[[], float] = time
    ) -> None:
        self._policy = policy
        self._keys = PublishedKeys(policy) if keys is None else keys
        self._claims = jwt.JWTClaimsRegistry(
            now=lambda: int(clock()),
            leeway=int(policy.leeway),
            iss={"essential": True, "value": policy.issuer},
            aud={"essential": True, "value": policy.audience},
            exp={"essential": True},
            sub={"essential": True},
        )
        self._role_claims = (f"urn:zitadel:iam:org:project:{policy.audience}:roles", PROJECT_ROLES_CLAIM)

    async def verify(self, token: str) -> Principal:
        """The principal ``token`` proves, else ``InvalidTokenError`` (or ``TokenKeysUnavailableError``)."""
        header = _header_of(token)
        if header.alg not in self._policy.algorithms:
            raise InvalidTokenError(TokenProblem.ALGORITHM)
        key = await self._keys.key(header.kid)
        if key is None:
            raise InvalidTokenError(TokenProblem.UNKNOWN_KEY)
        return self._principal(self._verified_claims(token, key, header.alg))

    def _verified_claims(self, token: str, key: Key, algorithm: str) -> dict[str, object]:
        try:
            decoded = jwt.decode(token, key, algorithms=[algorithm])
        except BadSignatureError as error:
            raise InvalidTokenError(TokenProblem.SIGNATURE) from error
        except (JoseError, ValueError) as error:
            raise InvalidTokenError(TokenProblem.MALFORMED) from error
        try:
            claims = _OBJECT.validate_python(decoded.claims)
        except ValidationError as error:
            raise InvalidTokenError(TokenProblem.MALFORMED) from error
        try:
            self._claims.validate(claims)
        except ExpiredTokenError as error:
            raise InvalidTokenError(TokenProblem.EXPIRED) from error
        except (InvalidClaimError, MissingClaimError) as error:
            raise InvalidTokenError(_PROBLEM_OF_CLAIM.get(error.claim, TokenProblem.CLAIMS)) from error
        return claims

    def _principal(self, claims: Mapping[str, object]) -> Principal:
        try:
            known = _Claims.model_validate(claims)
            subject = parse_subject(known.sub)
            roles = frozenset(_granted_roles(claims, self._role_claims, known.organization))
        except (ValidationError, InvalidSubjectError) as error:
            raise InvalidTokenError(TokenProblem.CLAIMS) from error
        return Principal(
            subject=subject,
            organization=organization_of(known.organization),
            provider_organization=known.organization,
            roles=roles,
        )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest libs/testing libs/platform/tests/test_authentication.py libs/platform/tests/test_settings.py -q --no-cov`
Expected: PASS.

- [ ] **Step 6: Gate**

Run: `uv run poe fmt && uv run poe check`
Expected: every step passes, coverage 100 %.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock libs/testing libs/platform
git commit -m "feat(platform): bearer tokens verified against the provider's published keys

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Platform: transactions scoped to an organization, and `organization_isolation`

**Files:**
- Modify: `libs/platform/src/ledger_platform/database.py`
- Test: modify `libs/platform/tests/test_database.py`

**Interfaces:**
- Consumes: `Tenant`, `OrganizationId` (kernel ids); the existing `tenant_isolation`, `MissingTenantColumnError`, `_IDENTIFIERS`.
- Produces: `type Scope = Tenant | OrganizationId`; `Database.transaction(scope: Scope | None) -> AbstractAsyncContextManager[UnitOfWork]` (an `OrganizationId` sets `ledger.organization_id` and leaves `ledger.company_id` empty); `UnitOfWork.scope -> Scope | None` (the scope the transaction was opened with, for repositories that filter by the same tenant); `organization_isolation(table: AnyTable, *, column: str = "organization_id") -> tuple[str, ...]` (policy `organization_isolation`); `organization_isolation_removal(table: AnyTable) -> tuple[str, ...]`. `tenant_isolation`'s DDL is unchanged, character for character.

Re-check before starting: Milestone 1's final review may have changed `database.py` (the readiness ping on a connection of its own, `pool_size` moving into `ServiceLimits`); the edits below touch only the module docstring, `Scope`, the policy helpers, `UnitOfWork` and `transaction`.

- [ ] **Step 1: Write the failing tests**

Append to `libs/platform/tests/test_database.py` (merging the imports: `organization_isolation`, `organization_isolation_removal` from `ledger_platform.database`, and `Scope` into its `lazy from ledger_platform.database import ...` line):

```python
# ── Organization-owned rows ─────────────────────────────────────────────────────

directory = Table(
    "directory",
    metadata,
    Column("id", Uuid(), primary_key=True),
    Column("organization_id", Uuid(), nullable=False),
    Column("name", Text, nullable=False),
)


@pytest.fixture
async def shared(database: Database) -> AsyncGenerator[Database]:
    """``directory`` created and put under ``organization_isolation``."""
    async with created(database, directory):
        await apply(database, organization_isolation(directory))
        yield database


def entry(organization: OrganizationId, name: str) -> Insert:
    return insert(directory).values(id=new_id(), organization_id=organization, name=name)


async def enter(database: Database, scope: Scope | None, organization: OrganizationId, name: str) -> None:
    async with database.transaction(scope) as unit:
        await unit.connection.execute(entry(organization, name))


async def names(database: Database, scope: Scope | None) -> list[str]:
    async with database.transaction(scope) as unit:
        return list(
            (await unit.connection.execute(select(directory.c.name).order_by(directory.c.name))).scalars()
        )


async def test_an_organization_sees_its_own_rows_from_any_of_its_companies(shared: Database) -> None:
    await enter(shared, ORGANIZATION, ORGANIZATION, "first")
    await enter(shared, ALPHA, ORGANIZATION, "second")
    assert await names(shared, ORGANIZATION) == ["first", "second"]
    assert await names(shared, ALPHA) == ["first", "second"]
    assert await names(shared, BETA) == ["first", "second"]


async def test_another_organization_sees_none_of_them(shared: Database) -> None:
    await enter(shared, ORGANIZATION, ORGANIZATION, "first")
    assert await names(shared, OTHER_ORGANIZATION) == []
    assert await names(shared, Tenant(OTHER_ORGANIZATION, ALPHA.company)) == []


async def test_without_a_scope_no_organization_row_is_visible_or_writable(shared: Database) -> None:
    await enter(shared, ORGANIZATION, ORGANIZATION, "first")
    assert await names(shared, None) == []
    with pytest.raises(DBAPIError, match="row-level security"):
        await enter(shared, None, ORGANIZATION, "orphan")


async def test_an_organization_cannot_write_rows_of_another(shared: Database) -> None:
    with pytest.raises(DBAPIError, match="row-level security"):
        await enter(shared, ORGANIZATION, OTHER_ORGANIZATION, "intruder")


async def test_an_organization_cannot_hand_its_rows_to_another(shared: Database) -> None:
    await enter(shared, ORGANIZATION, ORGANIZATION, "first")
    with pytest.raises(DBAPIError, match="row-level security"):
        async with shared.transaction(ORGANIZATION) as unit:
            await unit.connection.execute(update(directory).values(organization_id=OTHER_ORGANIZATION))


async def test_an_organization_scope_sees_no_company_owned_row(isolated: Database) -> None:
    await write(isolated, ALPHA, "alpha 1")
    async with isolated.transaction(ORGANIZATION) as unit:
        visible = list((await unit.connection.execute(select(notes.c.body))).scalars())
    assert visible == []
    with pytest.raises(DBAPIError, match="row-level security"):
        async with isolated.transaction(ORGANIZATION) as unit:
            await unit.connection.execute(note(ALPHA, "no company in scope"))


async def test_an_organization_scope_sets_the_organization_and_no_company(database: Database) -> None:
    organization = literal_column("current_setting('ledger.organization_id')", Text)
    company = literal_column("current_setting('ledger.company_id')", Text)
    async with database.transaction(ORGANIZATION) as unit:
        carried = (await unit.connection.execute(select(organization, company))).one()
    assert carried == (str(ORGANIZATION), "")


@pytest.mark.parametrize("scope", [ALPHA, ORGANIZATION, None], ids=["tenant", "organization", "none"])
async def test_a_unit_knows_the_scope_it_was_opened_with(database: Database, scope: Scope | None) -> None:
    async with database.transaction(scope) as unit:
        assert unit.scope == scope


def test_the_organization_policy_binds_the_organization_in_both_directions() -> None:
    enable, force, drop, policy = organization_isolation(table_named("directory"))
    assert enable == "ALTER TABLE directory ENABLE ROW LEVEL SECURITY"
    assert force == "ALTER TABLE directory FORCE ROW LEVEL SECURITY"
    assert drop == "DROP POLICY IF EXISTS organization_isolation ON directory"
    rule = "organization_id = NULLIF(current_setting('ledger.organization_id', true), '')::uuid"
    assert policy == f"CREATE POLICY organization_isolation ON directory USING ({rule}) WITH CHECK ({rule})"


def test_the_organization_column_can_be_another_and_is_quoted_when_it_must_be() -> None:
    *_, plain = organization_isolation(table_named("organizations"), column="id")
    assert "USING (id = NULLIF(current_setting('ledger.organization_id', true), '')::uuid)" in plain
    odd = Table("odd", MetaData(), Column("Owner Org", Uuid()))
    *_, quoted = organization_isolation(odd, column="Owner Org")
    assert 'USING ("Owner Org" = NULLIF(' in quoted


def test_organization_isolation_names_the_column_a_table_lacks() -> None:
    with pytest.raises(
        MissingTenantColumnError, match="'other' is missing the tenant columns organization_id"
    ):
        organization_isolation(Table("other", MetaData(), Column("id", Uuid())))


def test_the_organization_policy_removal_undoes_it_step_by_step() -> None:
    assert organization_isolation_removal(table_named("directory")) == (
        "DROP POLICY IF EXISTS organization_isolation ON directory",
        "ALTER TABLE directory NO FORCE ROW LEVEL SECURITY",
        "ALTER TABLE directory DISABLE ROW LEVEL SECURITY",
    )


async def test_removing_the_organization_policy_opens_the_table_and_it_can_be_put_back(
    shared: Database,
) -> None:
    await enter(shared, ORGANIZATION, ORGANIZATION, "first")
    await apply(shared, organization_isolation_removal(directory))
    assert await names(shared, None) == ["first"]
    await apply(shared, organization_isolation(directory))
    assert await names(shared, None) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest libs/platform/tests/test_database.py -q --no-cov`
Expected: FAIL with `ImportError: cannot import name 'organization_isolation'`.

- [ ] **Step 3: Implement**

In `libs/platform/src/ledger_platform/database.py`:

1. The module docstring's first paragraph becomes:

```python
"""PostgreSQL: one transaction per unit of work, with the tenant enforced by row-level security.

Every transaction sets ``ledger.organization_id`` and ``ledger.company_id`` (transaction-local) from its scope:
a company of an organization (``Tenant``), or an organization alone (``OrganizationId``). Tables protected by
``tenant_isolation`` show and accept only the rows of that organization and company; tables an organization owns
as a whole, protected by ``organization_isolation``, the rows of that organization. Without a scope they show
nothing and accept nothing (fail closed). A transaction commits only if every statement in it succeeded, and
what runs after the commit can no longer change the outcome.
"""
```

2. Import `Tenant` at runtime and `OrganizationId` lazily (replacing `lazy from ledger_kernel.ids import Tenant`):

```python
from ledger_kernel.ids import Tenant
lazy from ledger_kernel.ids import OrganizationId
```

3. Add after `_POLICY`:

```python
_ORGANIZATION_POLICY: Final = "organization_isolation"
_ORGANIZATION_SETTING: Final = "organization_id"
```

and after the `AnyTable` alias:

```python
type Scope = Tenant | OrganizationId
"""Whose rows a transaction may touch: one company of an organization (``Tenant``), or an organization's own
rows (``OrganizationId``), such as its list of companies. ``None`` touches no protected row at all."""
```

4. Replace `_matches_the_session` with:

```python
def _matches(column: str, setting: str) -> str:
    """``column`` equals the ``ledger.<setting>`` the transaction set; no setting, or an empty one, matches nothing."""
    return f"{_IDENTIFIERS.quote(column)} = NULLIF(current_setting('ledger.{setting}', true), '')::uuid"
```

and in `tenant_isolation` build the rule with it (the output is unchanged: both column names need no quoting):

```python
    rule = " AND ".join(_matches(column, column) for column in _TENANT_COLUMNS)
```

5. Add after `tenant_isolation_removal`:

```python
def organization_isolation(table: AnyTable, *, column: str = _ORGANIZATION_SETTING) -> tuple[str, ...]:
    """DDL (for a migration) that shows and accepts only the current organization's rows of ``table``.

    For a table an organization owns as a whole (its companies, its memberships): a transaction scoped to the
    organization, or to any of its companies, sees all of its rows and no other organization's; without a scope,
    none. ``column`` holds the organization's id (``id`` for the table of organizations itself). Running the DDL
    again replaces the policy. Like ``tenant_isolation``'s, the policy is permissive: another permissive policy on
    the same table would widen it.
    """
    if column not in table.c:
        raise MissingTenantColumnError(table.name, (column,))
    name = _IDENTIFIERS.format_table(table)
    rule = _matches(column, _ORGANIZATION_SETTING)
    return (
        f"ALTER TABLE {name} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {name} FORCE ROW LEVEL SECURITY",
        f"DROP POLICY IF EXISTS {_ORGANIZATION_POLICY} ON {name}",
        f"CREATE POLICY {_ORGANIZATION_POLICY} ON {name} USING ({rule}) WITH CHECK ({rule})",
    )


def organization_isolation_removal(table: AnyTable) -> tuple[str, ...]:
    """DDL (for a migration's downgrade) that undoes ``organization_isolation``; harmless on a table without it."""
    name = _IDENTIFIERS.format_table(table)
    return (
        f"DROP POLICY IF EXISTS {_ORGANIZATION_POLICY} ON {name}",
        f"ALTER TABLE {name} NO FORCE ROW LEVEL SECURITY",
        f"ALTER TABLE {name} DISABLE ROW LEVEL SECURITY",
    )
```

6. Add before `_Callbacks`:

```python
def _settings_of(scope: Scope | None) -> dict[str, str]:
    """What ``ledger.organization_id`` and ``ledger.company_id`` are set to for ``scope``: empty when absent."""
    if scope is None:
        return {"organization": "", "company": ""}
    if isinstance(scope, Tenant):
        return {"organization": str(scope.organization), "company": str(scope.company)}
    return {"organization": str(scope), "company": ""}
```

7. `UnitOfWork` keeps the scope it was opened with (`__slots__` gains `"_scope"`):

```python
    def __init__(self, connection: AsyncConnection, after_commit: _Callbacks, scope: Scope | None) -> None:
        self._connection = connection
        self._after_commit = after_commit
        self._scope = scope

    @property
    def scope(self) -> Scope | None:
        """Whose rows the transaction may touch, as ``Database.transaction`` was given it.

        For repositories that name the same tenant in their statements: row-level security enforces the scope,
        and the application filters by it as well (tenancy is checked twice, §2).
        """
        return self._scope
```

8. `Database.transaction` takes a scope and hands it to the unit:

```python
    @asynccontextmanager
    async def transaction(self, scope: Scope | None) -> AsyncGenerator[UnitOfWork]:
        """A transaction scoped to ``scope``: commits on normal exit, rolls back on an exception.

        A ``Tenant`` scopes it to one company of an organization, an ``OrganizationId`` to the rows the
        organization owns as a whole, ``None`` to no protected row. It never reports a commit that did not
        happen: if a statement failed and the caller went on, the exit raises ``AbortedTransactionError`` and the
        callbacks do not run.
        """
        callbacks = _Callbacks()
        try:
            async with self._engine.begin() as connection:
                await connection.execute(_SET_TENANT, _settings_of(scope))
                yield UnitOfWork(connection, callbacks, scope)
                if not await _is_committable(connection):
                    raise AbortedTransactionError
        finally:
            after_commit = callbacks.close()  # rolled back or committed, the unit takes no more callbacks
        _run(after_commit)  # reached only when the transaction committed
```

Every caller passes the scope positionally (`transaction(None)`, `transaction(envelope.tenant)`), so nothing else changes; confirm with `git grep -n "transaction(tenant=" libs` (expected: no output).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest libs/platform/tests/test_database.py -q --no-cov`
Expected: PASS (the existing `tenant_isolation` tests, which pin its DDL text, pass unchanged).

- [ ] **Step 5: Gate**

Run: `uv run poe fmt && uv run poe check`
Expected: every step passes, coverage 100 %.

- [ ] **Step 6: Commit**

```bash
git add libs/platform/src/ledger_platform/database.py libs/platform/tests/test_database.py
git commit -m "feat(platform): organization-scoped transactions and organization_isolation

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Platform idempotency keys

**Files:**
- Create: `libs/platform/src/ledger_platform/idempotency.py`
- Modify: `libs/platform/src/ledger_platform/schema.py`, `pyproject.toml` (root: import-linter layer)
- Test: `libs/platform/tests/test_idempotency.py`; modify `libs/platform/tests/test_schema.py`

**Interfaces:**
- Consumes: `PlatformTable`, `UnitOfWork`, `Database`, `organization_isolation` (Task 4); `CHASSIS_METADATA` (`ledger_platform.schema`, M1); `Principal`, `SubjectId` (Task 1); `InvalidOptionError`, `require_at_least`; `eventually`, `waits_for_a_lock` (`ledger_testing.waiting`, Task 2).
- Produces: `IdempotencyRecord(PlatformTable)` (table `idempotency_keys`: `organization_id uuid`, `subject varchar`, `key varchar` (primary key of the three), `fingerprint bytea`, `status integer NULL`, `headers jsonb NULL`, `body bytea NULL`, `expires_at timestamptz`; index `ix_idempotency_keys_expiry (organization_id, expires_at)`), registered in `ledger_platform.schema` beside the outbox and the inbox: `CHASSIS_METADATA` holds the three tables, and `CHASSIS_POLICIES: tuple[str, ...]` is the DDL of their row-level security (`organization_isolation` on `idempotency_keys`; the outbox and the inbox stay outside), which every service's first migration runs; `DEFAULT_RETENTION = timedelta(hours=24)`, `MAX_KEY_LENGTH = 255`, `PURGE_BATCH = 100`; `StoredResponse(status: int, body: bytes, headers: Mapping[str, str])`; `Outcome(response: StoredResponse, replayed: bool)`; `Attempt(*, organization, subject, key, fingerprint)` with `classmethod of(principal: Principal, key: str, *, method: str, path: str, body: bytes) -> Attempt`; `Idempotency(*, retention: timedelta = DEFAULT_RETENTION, purge_batch: int = PURGE_BATCH)` with `async once(unit: UnitOfWork, attempt: Attempt, work: Callable[[], Awaitable[StoredResponse]]) -> Outcome`; errors `InvalidIdempotencyKeyError` (422, `invalid_idempotency_key`), `IdempotencyKeyReusedError` (422, `idempotency_key_reused`), `IdempotencyKeyInUseError` (409, `idempotency_key_in_use`), `UnansweredIdempotencyKeyError` (INTERNAL, `unanswered_idempotency_key`), `InvalidIdempotencyOptionError`.

- [ ] **Step 1: Layering**

Re-check before starting: `libs/platform/src/ledger_platform/schema.py` and `libs/platform/tests/test_schema.py` as Milestone 1's final review left them (its built-in readiness check reads every table of `CHASSIS_METADATA`, so from this task on it also requires `idempotency_keys`); the edits below keep whatever the fix wave added and change only the tables and the policies.

In the root `pyproject.toml` contract "Platform layers", the messaging layer gains the module (`schema`, in the top layer, imports it):

```toml
    "outbox | streams | idempotency",
```

- [ ] **Step 2: Write the failing tests**

`libs/platform/tests/test_idempotency.py`:

```python
import asyncio
from datetime import timedelta
from typing import Final
lazy from collections.abc import AsyncGenerator, Awaitable, Callable

import psycopg
import pytest
from sqlalchemy import Column, Integer, MetaData, Table, Text, Uuid, func, insert, select, text, update
from sqlalchemy.exc import OperationalError

from ledger_kernel.access import Principal, SubjectId
from ledger_kernel.errors import ErrorKind
from ledger_kernel.ids import OrganizationId, new_id
from ledger_platform.database import Database, DatabaseLimits
from ledger_platform.idempotency import (
    MAX_KEY_LENGTH,
    Attempt,
    Idempotency,
    IdempotencyKeyInUseError,
    IdempotencyKeyReusedError,
    IdempotencyRecord,
    InvalidIdempotencyKeyError,
    InvalidIdempotencyOptionError,
    Outcome,
    StoredResponse,
    UnansweredIdempotencyKeyError,
)
from ledger_platform.schema import CHASSIS_METADATA, CHASSIS_POLICIES
from ledger_testing.waiting import eventually, waits_for_a_lock
lazy from ledger_platform.database import UnitOfWork

ORGANIZATION: Final = OrganizationId(new_id())
STRANGERS: Final = OrganizationId(new_id())
ALICE: Final = Principal(subject=SubjectId("alice"), organization=ORGANIZATION, provider_organization="1001")
BOB: Final = Principal(subject=SubjectId("bob"), organization=ORGANIZATION, provider_organization="1001")
MALLORY: Final = Principal(subject=SubjectId("alice"), organization=STRANGERS, provider_organization="1002")
KEYS_TABLE: Final = CHASSIS_METADATA.tables["idempotency_keys"]
CREATED: Final = StoredResponse(201, b'{"id": "thing-1"}', {"Location": "/v1/things/thing-1", "ETag": '"1"'})
IDEMPOTENCY: Final = Idempotency()

metadata = MetaData()
effects = Table(
    "effects", metadata, Column("id", Uuid(), primary_key=True), Column("label", Text, nullable=False)
)

type Work = Callable[[UnitOfWork], Awaitable[StoredResponse]]


def attempt(principal: Principal = ALICE, key: str = "key-1", body: bytes = b'{"name": "thing"}') -> Attempt:
    return Attempt.of(principal, key, method="POST", path="/v1/things", body=body)


async def create(unit: UnitOfWork) -> StoredResponse:
    """The work of a POST: one state change, and the response that announces it."""
    await unit.connection.execute(insert(effects).values(id=new_id(), label="created"))
    return CREATED


async def fail(unit: UnitOfWork) -> StoredResponse:
    await unit.connection.execute(insert(effects).values(id=new_id(), label="lost"))
    raise ZeroDivisionError


async def respond(
    database: Database, tried: Attempt, work: Work = create, idempotency: Idempotency = IDEMPOTENCY
) -> Outcome:
    async with database.transaction(tried.organization) as unit:
        return await idempotency.once(unit, tried, lambda: work(unit))


async def count_effects(database: Database) -> int:
    async with database.transaction(None) as unit:
        return (await unit.connection.execute(select(func.count()).select_from(effects))).scalar_one()


async def stored_keys(database: Database, organization: OrganizationId) -> list[str]:
    async with database.transaction(organization) as unit:
        rows = await unit.connection.execute(select(IdempotencyRecord.key).order_by(IdempotencyRecord.key))
        return list(rows.scalars())


async def expire(database: Database, organization: OrganizationId) -> None:
    async with database.transaction(organization) as unit:
        await unit.connection.execute(
            update(IdempotencyRecord).values(expires_at=func.now() - timedelta(seconds=1))
        )


@pytest.fixture
async def keys(database: Database) -> AsyncGenerator[Database]:
    """The chassis's tables under their policies (as a migration leaves them), and a table of effects."""
    async with database.engine.begin() as connection:
        for statement in CHASSIS_POLICIES:
            await connection.execute(text(statement))
        await connection.run_sync(metadata.create_all)
    yield database
    async with database.engine.begin() as connection:
        await connection.run_sync(metadata.drop_all)


# ── Once ────────────────────────────────────────────────────────────────────────


async def test_the_first_request_runs_and_a_retry_replays_its_response(keys: Database) -> None:
    first = await respond(keys, attempt())
    again = await respond(keys, attempt())
    assert first == Outcome(CREATED, replayed=False)
    assert again == Outcome(CREATED, replayed=True)
    assert await count_effects(keys) == 1


async def test_the_same_key_with_another_request_is_refused(keys: Database) -> None:
    await respond(keys, attempt())
    with pytest.raises(IdempotencyKeyReusedError) as caught:
        await respond(keys, attempt(body=b'{"name": "another thing"}'))
    assert (caught.value.kind, caught.value.code) == (ErrorKind.INVALID, "idempotency_key_reused")
    assert await count_effects(keys) == 1


async def test_a_key_belongs_to_its_caller(keys: Database) -> None:
    await respond(keys, attempt(ALICE))
    assert not (await respond(keys, attempt(BOB))).replayed
    assert not (await respond(keys, attempt(MALLORY))).replayed
    assert await count_effects(keys) == 3


async def test_a_request_that_fails_leaves_the_key_free(keys: Database) -> None:
    with pytest.raises(ZeroDivisionError):
        await respond(keys, attempt(), fail)
    assert await stored_keys(keys, ORGANIZATION) == []
    assert await respond(keys, attempt()) == Outcome(CREATED, replayed=False)
    assert await count_effects(keys) == 1


async def test_two_requests_with_one_key_at_once_run_the_work_once(keys: Database, database_url: str) -> None:
    holding, release = asyncio.Event(), asyncio.Event()

    async def slow(unit: UnitOfWork) -> StoredResponse:
        holding.set()
        await release.wait()
        return await create(unit)

    first = asyncio.create_task(respond(keys, attempt(), slow))
    await holding.wait()
    second = asyncio.create_task(respond(keys, attempt()))
    await eventually(lambda: waits_for_a_lock(database_url))  # the twin's claim waits for the first's row
    assert not second.done()
    release.set()
    assert (await first, await second) == (Outcome(CREATED, replayed=False), Outcome(CREATED, replayed=True))
    assert await count_effects(keys) == 1


async def test_a_twin_that_waits_longer_than_the_lock_timeout_is_told_to_retry(
    keys: Database, database_url: str
) -> None:
    holding, release = asyncio.Event(), asyncio.Event()

    async def slow(unit: UnitOfWork) -> StoredResponse:
        holding.set()
        await release.wait()
        return await create(unit)

    impatient_limits = DatabaseLimits(lock_timeout=timedelta(milliseconds=200))
    async with Database.open(database_url, pool_size=1, limits=impatient_limits) as impatient:
        first = asyncio.create_task(respond(keys, attempt(), slow))
        await holding.wait()
        with pytest.raises(IdempotencyKeyInUseError) as caught:
            await respond(impatient, attempt())
        release.set()
        await first
    assert (caught.value.kind, caught.value.code) == (ErrorKind.CONFLICT, "idempotency_key_in_use")
    assert await respond(keys, attempt()) == Outcome(CREATED, replayed=True)


async def test_a_claim_cancelled_for_another_reason_is_not_taken_for_a_key_in_use(
    keys: Database, database_url: str
) -> None:
    holding, release = asyncio.Event(), asyncio.Event()

    async def slow(unit: UnitOfWork) -> StoredResponse:
        holding.set()
        await release.wait()
        return await create(unit)

    hasty_limits = DatabaseLimits(statement_timeout=timedelta(milliseconds=100))
    async with Database.open(database_url, pool_size=1, limits=hasty_limits) as hasty:
        first = asyncio.create_task(respond(keys, attempt(), slow))
        await holding.wait()
        with pytest.raises(OperationalError) as caught:
            await respond(hasty, attempt())
        release.set()
        await first
    assert isinstance(
        caught.value.orig, psycopg.errors.QueryCanceled
    )  # the statement timeout, not the lock's


async def test_a_key_older_than_its_retention_runs_the_work_again(keys: Database) -> None:
    await respond(keys, attempt())
    await expire(keys, ORGANIZATION)
    assert await respond(keys, attempt(body=b'{"name": "a later thing"}')) == Outcome(CREATED, replayed=False)
    assert await respond(keys, attempt(body=b'{"name": "a later thing"}')) == Outcome(CREATED, replayed=True)
    assert await count_effects(keys) == 2


async def test_expired_keys_of_the_organization_are_purged_by_its_later_requests(keys: Database) -> None:
    for key in ("old-1", "old-2", "old-3"):
        await respond(keys, attempt(key=key))
    await respond(keys, attempt(MALLORY, key="theirs"))
    await expire(keys, ORGANIZATION)
    await expire(keys, STRANGERS)
    await respond(keys, attempt(key="new"), idempotency=Idempotency(purge_batch=2))
    assert len(await stored_keys(keys, ORGANIZATION)) == 2  # one expired key left, and the new one
    assert await stored_keys(keys, STRANGERS) == ["theirs"]  # another organization's are not touched


async def test_an_organization_sees_only_its_own_keys(keys: Database) -> None:
    await respond(keys, attempt())
    assert await stored_keys(keys, STRANGERS) == []
    assert await stored_keys(keys, ORGANIZATION) == ["key-1"]


async def test_a_committed_key_without_a_response_is_a_defect(keys: Database) -> None:
    tried = attempt()
    async with keys.transaction(ORGANIZATION) as unit:
        await unit.connection.execute(
            insert(IdempotencyRecord).values(
                organization_id=tried.organization,
                subject=tried.subject,
                key=tried.key,
                fingerprint=tried.fingerprint,
                expires_at=func.now() + timedelta(hours=1),
            )
        )
    with pytest.raises(UnansweredIdempotencyKeyError) as caught:
        await respond(keys, tried)
    assert caught.value.kind is ErrorKind.INTERNAL


# ── Attempts and options ────────────────────────────────────────────────────────


def test_the_fingerprint_is_of_the_method_the_path_and_the_body() -> None:
    base = Attempt.of(ALICE, "k", method="POST", path="/v1/things", body=b"{}")
    assert base == Attempt.of(ALICE, "k", method="POST", path="/v1/things", body=b"{}")
    assert base.fingerprint != Attempt.of(ALICE, "k", method="PUT", path="/v1/things", body=b"{}").fingerprint
    assert base.fingerprint != Attempt.of(ALICE, "k", method="POST", path="/v1/other", body=b"{}").fingerprint
    assert (
        base.fingerprint != Attempt.of(ALICE, "k", method="POST", path="/v1/things", body=b"[]").fingerprint
    )


def test_the_parts_of_a_fingerprint_cannot_be_shifted_into_each_other() -> None:
    shifted = Attempt.of(ALICE, "k", method="POST", path="/v1/ab", body=b"c")
    assert shifted.fingerprint != Attempt.of(ALICE, "k", method="POST", path="/v1/a", body=b"bc").fingerprint


def test_a_key_is_its_callers() -> None:
    tried = attempt(BOB)
    assert (tried.organization, tried.subject, tried.key) == (ORGANIZATION, "bob", "key-1")


@pytest.mark.parametrize(
    "key",
    ["", "with space", "tab\t", "café", "x" * (MAX_KEY_LENGTH + 1)],
    ids=["empty", "space", "tab", "non-ascii", "too-long"],
)
def test_a_key_is_visible_ascii_of_bounded_length(key: str) -> None:
    with pytest.raises(InvalidIdempotencyKeyError) as caught:
        attempt(key=key)
    assert len(str(caught.value)) < 160
    assert caught.value.code == "invalid_idempotency_key"


def test_the_longest_key_is_accepted() -> None:
    assert attempt(key="k" * MAX_KEY_LENGTH).key == "k" * MAX_KEY_LENGTH


@pytest.mark.parametrize(
    ("build", "option"),
    [
        (lambda: Idempotency(retention=timedelta(0)), "retention"),
        (lambda: Idempotency(retention=timedelta(seconds=-1)), "retention"),
        (lambda: Idempotency(purge_batch=0), "purge_batch"),
    ],
)
def test_options_that_cannot_work_are_refused(build: Callable[[], Idempotency], option: str) -> None:
    with pytest.raises(InvalidIdempotencyOptionError, match=f"idempotency option {option} must be"):
        build()


def test_the_table_has_the_columns_the_migrations_create() -> None:
    assert [column.name for column in KEYS_TABLE.columns] == [
        "organization_id",
        "subject",
        "key",
        "fingerprint",
        "status",
        "headers",
        "body",
        "expires_at",
    ]
    assert {index.name for index in KEYS_TABLE.indexes} == {"ix_idempotency_keys_expiry"}
    assert isinstance(KEYS_TABLE.c.status.type, Integer)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest libs/platform/tests/test_idempotency.py -q --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'ledger_platform.idempotency'`.

- [ ] **Step 4: Implement**

`libs/platform/src/ledger_platform/idempotency.py`:

```python
"""Idempotency keys: a POST sent again with the same key changes state once and answers the same both times.

A route that changes state takes an ``Idempotency-Key`` (§10) and runs its work through ``Idempotency.once``
inside the transaction that does the work: the key is claimed first, the work runs, and its response is stored
in the claim's row, so the claim, the state change, its events and the response commit together or not at all.

* The same key again with the same request (method, path and body) answers the stored response without running
  the work: the outcome is ``replayed``.
* The same key with another request is an ``IdempotencyKeyReusedError`` (422).
* Two requests with one key at once: the second's claim waits for the first's transaction to end, then replays
  its response; if the first rolled back, the second does the work. A wait longer than the database's
  ``lock_timeout`` is an ``IdempotencyKeyInUseError`` (409); retried later, the request replays.
* A request that fails stores nothing (its transaction rolls back), so its key is free for a retry.
* A key is kept for ``retention`` (24 hours) from its first use; after that the same key runs the work again.

A key belongs to its caller: a row is keyed by the organization, the subject and the key. The table is under
``organization_isolation``, so a transaction sees its organization's keys only, and expired rows are deleted
by the organization's own requests: each claim deletes up to ``purge_batch`` of them.
"""

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import ClassVar, Final, final
from uuid import UUID
lazy from collections.abc import Awaitable, Callable, Mapping

import psycopg
from sqlalchemy import DateTime, Index, LargeBinary, delete, func, select, tuple_, update
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Mapped, mapped_column
lazy from sqlalchemy import ColumnElement

from ledger_kernel.errors import ConflictError, InvalidInputError, LedgerError, excerpt
from ledger_platform.database import PlatformTable
from ledger_platform.options import InvalidOptionError, require_at_least
lazy from ledger_kernel.access import Principal, SubjectId
lazy from ledger_kernel.ids import OrganizationId
lazy from ledger_platform.database import UnitOfWork

DEFAULT_RETENTION: Final = timedelta(hours=24)
MAX_KEY_LENGTH: Final = 255
PURGE_BATCH: Final = 100
_KEY: Final = re.compile(rf"[!-~]{{1,{MAX_KEY_LENGTH}}}", re.ASCII)
"""Visible ASCII without spaces: what a client can put in a header and see back unchanged."""
_LENGTH_BYTES: Final = 8


class IdempotencyRecord(PlatformTable):
    """A key a caller used, the request it came with (as a fingerprint) and the response it got."""

    __tablename__: str = "idempotency_keys"

    organization_id: Mapped[UUID] = mapped_column(primary_key=True)
    subject: Mapped[str] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(primary_key=True)
    fingerprint: Mapped[bytes] = mapped_column(LargeBinary)
    """SHA-256 of the method, the path and the canonical body of the request that first used the key."""
    status: Mapped[int | None] = mapped_column()
    headers: Mapped[dict[str, str] | None] = mapped_column(JSONB)
    body: Mapped[bytes | None] = mapped_column(LargeBinary)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


Index("ix_idempotency_keys_expiry", IdempotencyRecord.organization_id, IdempotencyRecord.expires_at)
"""The purge reads an organization's expired keys without visiting the others."""


class InvalidIdempotencyKeyError(InvalidInputError):
    """A key that is not 1 to ``MAX_KEY_LENGTH`` visible ASCII characters."""

    code: ClassVar[str] = "invalid_idempotency_key"

    def __init__(self, key: str) -> None:
        super().__init__(
            f"an Idempotency-Key is 1 to {MAX_KEY_LENGTH} visible ASCII characters, not {excerpt(key)}"
        )


class IdempotencyKeyReusedError(InvalidInputError):
    """The key was used for another request: a client bug, never to be retried as it is."""

    code: ClassVar[str] = "idempotency_key_reused"

    def __init__(self) -> None:
        super().__init__("this Idempotency-Key was used for a different request")


class IdempotencyKeyInUseError(ConflictError):
    """A request with the same key is still being handled; retrying later replays its response."""

    code: ClassVar[str] = "idempotency_key_in_use"

    def __init__(self) -> None:
        super().__init__("a request with this Idempotency-Key is still being handled; retry later")


class UnansweredIdempotencyKeyError(LedgerError):
    """A committed key without a stored response: the claim and the response always commit together."""

    code: ClassVar[str] = "unanswered_idempotency_key"

    def __init__(self) -> None:
        super().__init__("a committed idempotency key has no stored response")


class InvalidIdempotencyOptionError(InvalidOptionError):
    """An idempotency option that cannot work."""

    code: ClassVar[str] = "invalid_idempotency_option"
    label: ClassVar[str] = "idempotency option"


@final
@dataclass(frozen=True, slots=True)
class StoredResponse:
    """A response as it is kept and replayed: the status, the body's bytes and the headers that go with it."""

    status: int
    body: bytes
    headers: Mapping[str, str]


@final
@dataclass(frozen=True, slots=True)
class Outcome:
    """The response of a request, and whether it was replayed from an earlier one with the same key."""

    response: StoredResponse
    replayed: bool


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Attempt:
    """One use of a key: whose, which key, and the fingerprint of the request it came with."""

    organization: OrganizationId
    subject: SubjectId
    key: str
    fingerprint: bytes

    @classmethod
    def of(cls, principal: Principal, key: str, *, method: str, path: str, body: bytes) -> Attempt:
        """``principal``'s use of ``key`` for the request ``method path`` with ``body`` (canonical bytes)."""
        if _KEY.fullmatch(key) is None:
            raise InvalidIdempotencyKeyError(key)
        digest = hashlib.sha256()
        for part in (method.encode(), path.encode(), body):
            digest.update(len(part).to_bytes(_LENGTH_BYTES, "big"))  # no part can run into the next
            digest.update(part)
        return cls(
            organization=principal.organization,
            subject=principal.subject,
            key=key,
            fingerprint=digest.digest(),
        )


def _row_of(attempt: Attempt) -> tuple[ColumnElement[bool], ...]:
    return (
        IdempotencyRecord.organization_id == attempt.organization,
        IdempotencyRecord.subject == attempt.subject,
        IdempotencyRecord.key == attempt.key,
    )


@final
class Idempotency:
    """Runs the work of a request once per key (see the module)."""

    __slots__ = ("_purge_batch", "_retention")

    def __init__(self, *, retention: timedelta = DEFAULT_RETENTION, purge_batch: int = PURGE_BATCH) -> None:
        if retention <= timedelta(0):
            raise InvalidIdempotencyOptionError(
                option="retention", requirement="longer than zero", value=retention
            )
        require_at_least(InvalidIdempotencyOptionError, "purge_batch", purge_batch)
        self._retention = retention
        self._purge_batch = purge_batch

    async def once(
        self, unit: UnitOfWork, attempt: Attempt, work: Callable[[], Awaitable[StoredResponse]]
    ) -> Outcome:
        """Claim ``attempt``'s key in ``unit`` and run ``work``, or replay the response stored for it.

        ``unit`` is the transaction the work runs in, scoped to ``attempt``'s organization (or one of its
        companies); it raises ``IdempotencyKeyReusedError`` and ``IdempotencyKeyInUseError``.
        """
        await self._purge(unit, attempt.organization)
        if await self._claim(unit, attempt):
            response = await work()
            await unit.connection.execute(
                update(IdempotencyRecord)
                .where(*_row_of(attempt))
                .values(status=response.status, headers=dict(response.headers), body=response.body)
            )
            return Outcome(response, replayed=False)
        return Outcome(await self._stored(unit, attempt), replayed=True)

    async def _claim(self, unit: UnitOfWork, attempt: Attempt) -> bool:
        """Insert the key, or take over an expired one; false when the key is still in force."""
        fresh = insert(IdempotencyRecord).values(
            organization_id=attempt.organization,
            subject=attempt.subject,
            key=attempt.key,
            fingerprint=attempt.fingerprint,
            expires_at=func.now() + self._retention,
        )
        claim = fresh.on_conflict_do_update(
            index_elements=[
                IdempotencyRecord.organization_id,
                IdempotencyRecord.subject,
                IdempotencyRecord.key,
            ],
            set_={
                "fingerprint": fresh.excluded["fingerprint"],
                "status": None,
                "headers": None,
                "body": None,
                "expires_at": fresh.excluded["expires_at"],
            },
            where=IdempotencyRecord.expires_at <= func.now(),
        ).returning(IdempotencyRecord.key)
        try:
            claimed = (await unit.connection.execute(claim)).first()
        except OperationalError as error:
            if isinstance(error.orig, psycopg.errors.LockNotAvailable):
                raise IdempotencyKeyInUseError from error
            raise
        return claimed is not None

    async def _stored(self, unit: UnitOfWork, attempt: Attempt) -> StoredResponse:
        """The response stored for the key, if the request is the one that first used it."""
        fingerprint, status, headers, body = (
            await unit.connection.execute(
                select(
                    IdempotencyRecord.fingerprint,
                    IdempotencyRecord.status,
                    IdempotencyRecord.headers,
                    IdempotencyRecord.body,
                ).where(*_row_of(attempt))
            )
        ).one()
        if fingerprint != attempt.fingerprint:
            raise IdempotencyKeyReusedError
        if status is None or headers is None or body is None:
            raise UnansweredIdempotencyKeyError
        return StoredResponse(status, body, headers)

    async def _purge(self, unit: UnitOfWork, organization: OrganizationId) -> None:
        """Delete up to ``purge_batch`` expired keys of the organization, skipping those others hold."""
        expired = (
            select(IdempotencyRecord.organization_id, IdempotencyRecord.subject, IdempotencyRecord.key)
            .where(
                IdempotencyRecord.organization_id == organization, IdempotencyRecord.expires_at <= func.now()
            )
            .limit(self._purge_batch)
            .with_for_update(skip_locked=True)
        )
        await unit.connection.execute(
            delete(IdempotencyRecord).where(
                tuple_(
                    IdempotencyRecord.organization_id, IdempotencyRecord.subject, IdempotencyRecord.key
                ).in_(expired)
            )
        )
```

`libs/platform/src/ledger_platform/schema.py` — the chassis's schema gains the table and its policy (keep anything Milestone 1's final review added to the module):

```python
"""The tables the chassis owns, which every service's migrations create, and their row-level security.

The tables are ``outbox``, ``inbox`` and ``idempotency_keys``. A service's first migration creates
``CHASSIS_METADATA`` beside the service's own tables and runs ``CHASSIS_POLICIES``. The chassis writes no
table by itself, and a service started without them is not ready.

A table is registered when the module of its class is imported, and the classes sit with the code that uses
them: ``OutboxRecord`` in ``outbox``, ``InboxRecord`` in ``consumer``, ``IdempotencyRecord`` in
``idempotency``. ``PlatformTable.metadata`` therefore lacks some of them in a process that has not imported
all three (a migration, say). This module imports them and is the one thing a migration needs to import.
"""

from typing import Final

from ledger_platform.consumer import InboxRecord
from ledger_platform.database import PlatformTable, organization_isolation
from ledger_platform.idempotency import IdempotencyRecord
from ledger_platform.outbox import OutboxRecord

__all__ = ["CHASSIS_METADATA", "CHASSIS_POLICIES", "IdempotencyRecord", "InboxRecord", "OutboxRecord"]

CHASSIS_METADATA: Final = PlatformTable.metadata
"""Every table of the chassis, for ``create_all`` and ``drop_all``."""
CHASSIS_POLICIES: Final = organization_isolation(CHASSIS_METADATA.tables["idempotency_keys"])
"""The row-level security of the chassis's tables, for a migration to run once it has created them.

``idempotency_keys`` holds what an organization's callers sent and were answered, so it is under
``organization_isolation``. ``outbox`` and ``inbox`` stay outside on purpose: the relay reads the outbox
across tenants, and the inbox is claimed before a handler runs in its event's tenant."""
```

`libs/platform/tests/test_schema.py`:

```python
import subprocess
import sys
from typing import Final

from ledger_platform.schema import CHASSIS_METADATA, CHASSIS_POLICIES

IMPORT_ONLY_THE_SCHEMA: Final = (
    "import ledger_platform.schema as schema; print(*sorted(schema.CHASSIS_METADATA.tables))"
)
"""All a migration imports of the chassis: the tables must be there with nothing else imported."""


def test_the_chassis_owns_the_outbox_the_inbox_and_the_idempotency_keys_and_nothing_else() -> None:
    assert set(CHASSIS_METADATA.tables) == {"outbox", "inbox", "idempotency_keys"}


def test_the_indexes_the_relay_and_the_purge_read_are_part_of_the_schema() -> None:
    indexes = {
        name: {index.name for index in table.indexes} for name, table in CHASSIS_METADATA.tables.items()
    }

    assert indexes == {
        "outbox": {"ix_outbox_due"},
        "inbox": set(),
        "idempotency_keys": {"ix_idempotency_keys_expiry"},
    }


def test_the_inbox_is_keyed_by_consumer_and_event() -> None:
    assert [column.name for column in CHASSIS_METADATA.tables["inbox"].primary_key] == [
        "consumer",
        "event_id",
    ]


def test_only_the_idempotency_keys_are_under_row_level_security() -> None:
    assert all(" idempotency_keys" in statement for statement in CHASSIS_POLICIES)
    assert CHASSIS_POLICIES[-1].startswith("CREATE POLICY organization_isolation ON idempotency_keys ")


def test_importing_the_schema_alone_registers_every_table() -> None:
    """In a process of its own: the tests here have imported the module of every table already."""
    imported = subprocess.run(
        [sys.executable, "-I", "-c", IMPORT_ONLY_THE_SCHEMA], check=True, capture_output=True, text=True
    )

    assert imported.stdout.split() == ["idempotency_keys", "inbox", "outbox"]
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest libs/platform/tests/test_idempotency.py libs/platform/tests/test_schema.py libs/platform/tests/test_database.py -q --no-cov`
Expected: PASS. The `database` fixture creates `CHASSIS_METADATA`, which now holds `idempotency_keys` too (without its policy, as the other platform tests need it); the `keys` fixture adds the policy as a migration does.

- [ ] **Step 6: Gate**

Run: `uv run poe fmt && uv run poe check`
Expected: every step passes, coverage 100 %.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml libs/platform/src/ledger_platform/idempotency.py libs/platform/src/ledger_platform/schema.py libs/platform/tests/test_idempotency.py libs/platform/tests/test_schema.py
git commit -m "feat(platform): idempotency keys claimed in the transaction that does the work

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Platform request context: authorization, the dependencies routes declare, trace ids in logs

**Files:**
- Create: `libs/platform/src/ledger_platform/authorization.py`, `libs/platform/src/ledger_platform/routes.py`
- Modify: `libs/platform/src/ledger_platform/logs.py`, `pyproject.toml` (root: import-linter layers)
- Test: `libs/platform/tests/test_authorization.py`, `libs/platform/tests/test_routes.py`; modify `libs/platform/tests/test_logs.py`

**Interfaces:**
- Consumes: `TokenVerifier`, `MissingTokenError` (Task 3); `Attempt`, `Outcome`, `StoredResponse`, `MAX_KEY_LENGTH` (Task 5); `Permission`, `Principal`, `SubjectId`, `PageRequest`, `decode_cursor`, `DEFAULT_PAGE_SIZE`, `MAX_PAGE_SIZE` (Task 1); `create_app`, `ApiInfo`, `Observability`, `Health`; `Moment` (Task 2). Re-check `create_app`, `Observability` and `Health` against Milestone 1's final review before starting (the test app is built with them), and `logs.py`'s processor list, which the fix wave's redacting renderer changes: the trace processor goes right after `merge_contextvars` whatever else the list holds.
- Produces: in `ledger_platform.authorization`: `PermissionSource` (protocol: `async permissions(principal: Principal, company: CompanyId) -> frozenset[Permission]`), `CompanyAccess(principal: Principal, tenant: Tenant, permissions: frozenset[Permission])` with `allows(permission) -> bool` and `require(permission) -> None`, `PermissionDeniedError(permission)` (403, `permission_denied`), `NoCompanyAccessError(company)` (404, `company_not_found`), `CachedPermissions(source, *, ttl: float = 30.0, capacity: int = 10_000, clock=monotonic)` with `forget(organization, company, subject) -> None`, `InvalidAuthorizationOptionError`. In `ledger_platform.routes`: `type Caller`, `type PermissionsDependency`, `type InCompany`; `authenticated(verifier: TokenVerifier) -> Caller`; `company_in_path(caller: Caller, permissions: PermissionsDependency) -> InCompany` (path parameter `company_id`); `company_in_header(caller, permissions) -> InCompany` (header `Ledger-Company-Id`); `COMPANY_HEADER`; `async page_request(limit: int = 50, cursor: str | None = None) -> PageRequest`; `IfMatch(versions: frozenset[int] | None)` with `admits(version: int) -> bool`; `parse_if_match(value: str) -> IfMatch`; `async if_match(value: str | None) -> IfMatch` (428 when absent); `etag(version: int) -> str`; `precondition_failed() -> HTTPException` (412); `async idempotency_key(key: str) -> str`; `attempt_of(request: Request, principal: Principal, key: str, body: BaseModel | None = None) -> Attempt`; `json_response(status: int, body: BaseModel, headers: Mapping[str, str] = {}) -> StoredResponse`; `answer(outcome: Outcome) -> Response`; `REPLAYED_HEADER = "Idempotent-Replayed"`. Logs: every record written inside a valid span carries `trace_id` and `span_id` (lowercase hex).

- [ ] **Step 1: Layering**

In the root `pyproject.toml` contract "Platform layers", the adapter and messaging layers gain the modules:

```toml
    "web | grpc_server | consumer | routes",
    "outbox | streams | idempotency | authorization",
```

- [ ] **Step 2: Write the failing tests**

`libs/platform/tests/test_authorization.py`:

```python
import asyncio
from typing import Final, final, override

import pytest

from ledger_kernel.access import Permission, Principal, SubjectId
from ledger_kernel.errors import ErrorKind
from ledger_kernel.ids import CompanyId, OrganizationId, Tenant, new_id
from ledger_platform.authorization import (
    CachedPermissions,
    CompanyAccess,
    InvalidAuthorizationOptionError,
    NoCompanyAccessError,
    PermissionDeniedError,
    PermissionSource,
)
from ledger_testing.clock import Moment

ORGANIZATION: Final = OrganizationId(new_id())
COMPANY: Final = CompanyId(new_id())
OTHER_COMPANY: Final = CompanyId(new_id())
ALICE: Final = Principal(subject=SubjectId("alice"), organization=ORGANIZATION, provider_organization="1001")
BOB: Final = Principal(subject=SubjectId("bob"), organization=ORGANIZATION, provider_organization="1001")
READ: Final = frozenset({Permission.COMPANY_READ})
MANAGE: Final = frozenset({Permission.COMPANY_READ, Permission.COMPANY_MANAGE})


@final
class Counting(PermissionSource):
    """Answers from a table and counts the questions; can be held mid-answer."""

    def __init__(self) -> None:
        self.granted: dict[tuple[SubjectId, CompanyId], frozenset[Permission]] = {}
        self.asked = 0
        self.hold: asyncio.Event | None = None
        self.answering = asyncio.Event()

    @override
    async def permissions(self, principal: Principal, company: CompanyId) -> frozenset[Permission]:
        self.asked += 1
        answer = self.granted.get((principal.subject, company), frozenset())
        if self.hold is not None:
            self.answering.set()
            await self.hold.wait()
        return answer


def test_access_allows_what_it_holds_and_refuses_the_rest() -> None:
    access = CompanyAccess(ALICE, Tenant(ORGANIZATION, COMPANY), READ)
    assert access.allows(Permission.COMPANY_READ)
    assert not access.allows(Permission.COMPANY_MANAGE)
    access.require(Permission.COMPANY_READ)
    with pytest.raises(PermissionDeniedError, match=r"needs the permission company\.manage") as caught:
        access.require(Permission.COMPANY_MANAGE)
    assert (caught.value.kind, caught.value.code, caught.value.permission) == (
        ErrorKind.FORBIDDEN,
        "permission_denied",
        Permission.COMPANY_MANAGE,
    )


def test_no_access_at_all_is_a_company_not_found() -> None:
    error = NoCompanyAccessError(COMPANY)
    assert (error.kind, error.code, str(error)) == (
        ErrorKind.NOT_FOUND,
        "company_not_found",
        f"company {COMPANY} was not found",
    )


async def test_an_answer_is_kept_for_its_ttl() -> None:
    source, moment = Counting(), Moment()
    source.granted[(ALICE.subject, COMPANY)] = READ
    cache = CachedPermissions(source, ttl=30.0, clock=moment)
    assert await cache.permissions(ALICE, COMPANY) == READ
    moment.now = 29.9
    assert await cache.permissions(ALICE, COMPANY) == READ
    assert source.asked == 1
    source.granted[(ALICE.subject, COMPANY)] = MANAGE
    moment.now = 30.0
    assert await cache.permissions(ALICE, COMPANY) == MANAGE
    assert source.asked == 2


async def test_answers_are_kept_per_principal_and_company() -> None:
    source = Counting()
    source.granted[(ALICE.subject, COMPANY)] = READ
    cache = CachedPermissions(source)
    assert await cache.permissions(ALICE, COMPANY) == READ
    assert await cache.permissions(BOB, COMPANY) == frozenset()
    assert await cache.permissions(ALICE, OTHER_COMPANY) == frozenset()
    assert source.asked == 3


async def test_forgetting_drops_the_answer_at_once() -> None:
    source = Counting()
    source.granted[(ALICE.subject, COMPANY)] = MANAGE
    cache = CachedPermissions(source)
    await cache.permissions(ALICE, COMPANY)
    source.granted[(ALICE.subject, COMPANY)] = READ
    cache.forget(ORGANIZATION, COMPANY, SubjectId("alice"))
    assert await cache.permissions(ALICE, COMPANY) == READ


async def test_an_answer_fetched_before_a_change_is_not_kept_after_it() -> None:
    source = Counting()
    source.granted[(ALICE.subject, COMPANY)] = MANAGE
    source.hold = asyncio.Event()
    cache = CachedPermissions(source)
    stale = asyncio.create_task(cache.permissions(ALICE, COMPANY))
    await source.answering.wait()
    cache.forget(ORGANIZATION, COMPANY, SubjectId("alice"))  # the membership changed meanwhile
    source.hold.set()
    assert await stale == MANAGE  # the caller still gets the answer it asked for
    source.hold = None
    source.granted[(ALICE.subject, COMPANY)] = READ
    assert await cache.permissions(ALICE, COMPANY) == READ  # but it was not kept
    assert source.asked == 2


async def test_the_least_recently_used_answers_go_beyond_the_capacity() -> None:
    source = Counting()
    cache = CachedPermissions(source, capacity=2)
    await cache.permissions(ALICE, COMPANY)
    await cache.permissions(BOB, COMPANY)
    await cache.permissions(ALICE, COMPANY)  # used again: Bob's is now the oldest
    await cache.permissions(ALICE, OTHER_COMPANY)  # one too many: Bob's goes
    assert source.asked == 3
    await cache.permissions(ALICE, COMPANY)
    assert source.asked == 3
    await cache.permissions(BOB, COMPANY)
    assert source.asked == 4


@pytest.mark.parametrize(
    ("ttl", "capacity", "option"),
    [(0.0, 10, "ttl"), (float("inf"), 10, "ttl"), (30.0, 0, "capacity")],
)
def test_a_cache_that_cannot_work_is_refused(ttl: float, capacity: int, option: str) -> None:
    with pytest.raises(InvalidAuthorizationOptionError, match=f"authorization option {option} must be"):
        CachedPermissions(Counting(), ttl=ttl, capacity=capacity)
```

`libs/platform/tests/test_routes.py`:

```python
from contextlib import asynccontextmanager
from typing import Annotated, Final, TypedDict, final, override
lazy from collections.abc import AsyncGenerator

import httpx
import pytest
import structlog
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, TypeAdapter
lazy from fastapi import FastAPI, Response
lazy from prometheus_client import CollectorRegistry

from ledger_kernel.access import Permission, Principal, SubjectId
from ledger_kernel.ids import CompanyId, new_id
from ledger_kernel.pagination import encode_cursor
from ledger_platform.authentication import TokenPolicy, TokenVerifier, organization_of
from ledger_platform.authorization import PermissionSource
from ledger_platform.health import Health
from ledger_platform.idempotency import Outcome
from ledger_platform.routes import (
    COMPANY_HEADER,
    REPLAYED_HEADER,
    IfMatch,
    answer,
    attempt_of,
    authenticated,
    company_in_header,
    company_in_path,
    etag,
    idempotency_key,
    if_match,
    json_response,
    page_request,
    parse_if_match,
    precondition_failed,
)
from ledger_platform.web import ApiInfo, Observability, create_app
from ledger_testing.issuer import AUDIENCE, ISSUER, PROVIDER_ORGANIZATION, LocalIssuer
lazy from ledger_kernel.pagination import PageRequest
lazy from ledger_platform.authorization import CompanyAccess
lazy from ledger_platform.tracing import Tracing

COMPANY: Final = CompanyId(new_id())
OTHER_COMPANY: Final = CompanyId(new_id())
PROBLEM: Final = TypeAdapter(dict[str, object])
BODY: Final = TypeAdapter(dict[str, object])


class Components(TypedDict):
    securitySchemes: dict[str, dict[str, str]]


class Document(TypedDict):
    components: Components


DOCUMENT: Final = TypeAdapter(Document)


class Thing(BaseModel):
    name: str


@final
class Grants(PermissionSource):
    def __init__(self) -> None:
        self.granted: dict[tuple[SubjectId, CompanyId], frozenset[Permission]] = {}
        self.asked = 0

    @override
    async def permissions(self, principal: Principal, company: CompanyId) -> frozenset[Permission]:
        self.asked += 1
        return self.granted.get((principal.subject, company), frozenset())


def applied(version: int, condition: IfMatch) -> dict[str, int]:
    """An update of ``version`` as a route answers it: 412 unless ``condition`` admits the version."""
    if not condition.admits(version):
        raise precondition_failed()
    return {"version": version}


def router(verifier: TokenVerifier, grants: Grants) -> APIRouter:
    routes = APIRouter()
    caller = authenticated(verifier)

    async def source() -> PermissionSource:
        return grants

    in_path = company_in_path(caller, source)
    in_header = company_in_header(caller, source)

    @routes.get("/whoami")
    async def whoami(principal: Annotated[Principal, Depends(caller)]) -> dict[str, object]:
        return {"subject": principal.subject, "context": structlog.contextvars.get_contextvars()}

    @routes.get("/companies/{company_id}/things")
    async def things(access: Annotated[CompanyAccess, Depends(in_path)]) -> dict[str, object]:
        access.require(Permission.COMPANY_READ)
        return {"company": str(access.tenant.company), "context": structlog.contextvars.get_contextvars()}

    @routes.get("/things")
    async def things_of_the_header(access: Annotated[CompanyAccess, Depends(in_header)]) -> dict[str, str]:
        return {"company": str(access.tenant.company)}

    @routes.get("/pages")
    async def pages(page: Annotated[PageRequest, Depends(page_request)]) -> dict[str, object]:
        return {"size": page.size, "after": None if page.after is None else str(page.after)}

    @routes.patch("/versions/{version}")
    async def versions(version: int, condition: Annotated[IfMatch, Depends(if_match)]) -> dict[str, bool]:
        return {"admitted": condition.admits(version)}

    @routes.put("/guarded/{version}")
    async def guarded(version: int, condition: Annotated[IfMatch, Depends(if_match)]) -> dict[str, int]:
        return applied(version, condition)

    @routes.post("/keys")
    async def keys(key: Annotated[str, Depends(idempotency_key)]) -> dict[str, str]:
        return {"key": key}

    @routes.post("/answers/{replayed}")
    async def answers(*, replayed: bool) -> Response:
        stored = json_response(201, Thing(name="thing"), {"Location": "/things/1"})
        return answer(Outcome(stored, replayed=replayed))

    return routes


@pytest.fixture
async def issuer() -> AsyncGenerator[LocalIssuer]:
    async with LocalIssuer.started() as started:
        yield started


@pytest.fixture
def grants() -> Grants:
    return Grants()


@asynccontextmanager
async def nothing_to_start(app: FastAPI) -> AsyncGenerator[None]:
    del app
    yield


@pytest.fixture
def app(issuer: LocalIssuer, grants: Grants, registry: CollectorRegistry, tracing: Tracing) -> FastAPI:
    verifier = TokenVerifier(TokenPolicy(issuer=ISSUER, audience=AUDIENCE, keys_url=issuer.keys_url))
    return create_app(
        info=ApiInfo(title="routes", version="1.0"),
        observability=Observability(health=Health(), registry=registry, tracer_provider=tracing.provider),
        lifespan=nothing_to_start,
        routers=(router(verifier, grants),),
    )


@pytest.fixture
async def client(app: FastAPI) -> AsyncGenerator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as opened:
        yield opened


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def problem_code(response: httpx.Response) -> object:
    return PROBLEM.validate_json(response.content)["code"]


# ── Who calls ───────────────────────────────────────────────────────────────────


async def test_the_caller_is_the_principal_its_token_proves(
    client: httpx.AsyncClient, issuer: LocalIssuer
) -> None:
    response = await client.get("/whoami", headers=bearer(issuer.token(subject="2001")))
    assert response.status_code == 200
    body = BODY.validate_json(response.content)
    assert body["subject"] == "2001"
    assert body["context"] == {
        "organization_id": str(organization_of(PROVIDER_ORGANIZATION)),
        "subject": "2001",
    }


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Basic YWxpY2U6c2VjcmV0"}, {"Authorization": "Bearer"}],
    ids=["none", "basic", "empty-bearer"],
)
async def test_a_request_without_a_bearer_token_is_unauthenticated(
    client: httpx.AsyncClient, headers: dict[str, str]
) -> None:
    response = await client.get("/whoami", headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == 'Bearer realm="ledger"'
    assert problem_code(response) == "missing_token"


async def test_a_bad_token_is_unauthenticated_and_never_echoed(
    client: httpx.AsyncClient, issuer: LocalIssuer
) -> None:
    token = issuer.token(claims={"aud": "someone-else"})
    response = await client.get("/whoami", headers=bearer(token))
    assert response.status_code == 401
    assert problem_code(response) == "invalid_token"
    assert response.headers["www-authenticate"] == 'Bearer realm="ledger", error="invalid_token"'
    assert token not in response.text
    assert "audience" in response.text


async def test_a_megabyte_of_authorization_is_refused_unread(
    client: httpx.AsyncClient, issuer: LocalIssuer
) -> None:
    response = await client.get("/whoami", headers=bearer("x" * 1_000_000))
    assert (response.status_code, problem_code(response)) == (401, "invalid_token")
    assert "malformed" in response.text
    assert len(response.content) < 1_000
    assert issuer.fetches == 0


async def test_the_bound_fields_do_not_outlive_the_request(
    client: httpx.AsyncClient, issuer: LocalIssuer
) -> None:
    await client.get("/whoami", headers=bearer(issuer.token()))
    assert structlog.contextvars.get_contextvars() == {}


async def test_the_api_document_declares_bearer_tokens(client: httpx.AsyncClient) -> None:
    document = DOCUMENT.validate_json((await client.get("/openapi.json")).content)
    assert document["components"]["securitySchemes"]["HTTPBearer"] == {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "JWT",
        "description": "An OAuth 2.0 access token (JWT) of the organization's identity provider.",
    }


# ── For which company ──────────────────────────────────────────────────────────


async def test_a_company_the_caller_holds_a_role_in_is_its_access(
    client: httpx.AsyncClient, issuer: LocalIssuer, grants: Grants
) -> None:
    grants.granted[(SubjectId("2001"), COMPANY)] = frozenset({Permission.COMPANY_READ})
    response = await client.get(f"/companies/{COMPANY}/things", headers=bearer(issuer.token()))
    assert response.status_code == 200
    body = BODY.validate_json(response.content)
    assert body["company"] == str(COMPANY)
    assert body["context"] == {
        "organization_id": str(organization_of(PROVIDER_ORGANIZATION)),
        "subject": "2001",
        "company_id": str(COMPANY),
    }


async def test_a_company_without_a_role_is_not_found(
    client: httpx.AsyncClient, issuer: LocalIssuer, grants: Grants
) -> None:
    grants.granted[(SubjectId("2001"), COMPANY)] = frozenset({Permission.COMPANY_READ})
    response = await client.get(f"/companies/{OTHER_COMPANY}/things", headers=bearer(issuer.token()))
    assert response.status_code == 404
    assert problem_code(response) == "company_not_found"


async def test_a_role_without_the_permission_is_forbidden(
    client: httpx.AsyncClient, issuer: LocalIssuer, grants: Grants
) -> None:
    grants.granted[(SubjectId("2001"), COMPANY)] = frozenset({Permission.INVOICE_READ})
    response = await client.get(f"/companies/{COMPANY}/things", headers=bearer(issuer.token()))
    assert response.status_code == 403
    assert problem_code(response) == "permission_denied"


async def test_a_company_id_that_is_not_an_id_is_refused_unechoed(
    client: httpx.AsyncClient, issuer: LocalIssuer
) -> None:
    response = await client.get(f"/companies/{'x' * 500}/things", headers=bearer(issuer.token()))
    assert response.status_code == 422
    assert problem_code(response) == "invalid_id"
    assert "x" * 100 not in response.text


async def test_the_company_can_come_in_a_header(
    client: httpx.AsyncClient, issuer: LocalIssuer, grants: Grants
) -> None:
    grants.granted[(SubjectId("2001"), COMPANY)] = frozenset({Permission.COMPANY_READ})
    headers = bearer(issuer.token()) | {COMPANY_HEADER: str(COMPANY)}
    response = await client.get("/things", headers=headers)
    assert (response.status_code, BODY.validate_json(response.content)["company"]) == (200, str(COMPANY))
    missing = await client.get("/things", headers=bearer(issuer.token()))
    assert (missing.status_code, problem_code(missing)) == (422, "invalid_request")


async def test_the_company_is_never_looked_up_for_an_unauthenticated_caller(
    client: httpx.AsyncClient, grants: Grants
) -> None:
    response = await client.get(f"/companies/{COMPANY}/things")
    assert (response.status_code, problem_code(response), grants.asked) == (401, "missing_token", 0)


# ── Pages ───────────────────────────────────────────────────────────────────────


async def test_a_page_has_a_default_size_and_starts_at_the_beginning(client: httpx.AsyncClient) -> None:
    assert BODY.validate_json((await client.get("/pages")).content) == {"size": 50, "after": None}


async def test_a_cursor_says_where_the_page_starts(client: httpx.AsyncClient) -> None:
    after = new_id()
    response = await client.get("/pages", params={"limit": 10, "cursor": encode_cursor(after)})
    assert BODY.validate_json(response.content) == {"size": 10, "after": str(after)}


@pytest.mark.parametrize(
    ("params", "code"),
    [
        ({"limit": 0}, "invalid_request"),
        ({"limit": 101}, "invalid_request"),
        ({"cursor": "nope"}, "invalid_cursor"),
    ],
)
async def test_a_page_that_cannot_be_served_is_refused(
    client: httpx.AsyncClient, params: dict[str, object], code: str
) -> None:
    response = await client.get("/pages", params=params)
    assert (response.status_code, problem_code(response)) == (422, code)


# ── Preconditions ───────────────────────────────────────────────────────────────


async def test_an_update_without_if_match_is_refused(client: httpx.AsyncClient) -> None:
    response = await client.patch("/versions/3")
    assert (response.status_code, problem_code(response)) == (428, "precondition_required")


@pytest.mark.parametrize(
    ("header", "admitted"),
    [
        ('"3"', True),
        ('"2", "3"', True),
        ("*", True),
        ('"4"', False),
        ('W/"3"', False),
        ("3", False),
        ("garbage", False),
    ],
)
async def test_if_match_admits_the_versions_it_names(
    client: httpx.AsyncClient, header: str, *, admitted: bool
) -> None:
    response = await client.patch("/versions/3", headers={"If-Match": header})
    assert BODY.validate_json(response.content) == {"admitted": admitted}


def test_if_match_reads_strong_tags_only() -> None:
    assert parse_if_match(' "1" , W/"2", "3", "",  "x", "1234567890123456789" ') == IfMatch(frozenset({1, 3}))
    assert parse_if_match("*") == IfMatch(None)
    assert IfMatch(None).admits(12)


async def test_an_update_whose_precondition_fails_is_a_412(client: httpx.AsyncClient) -> None:
    refused = await client.put("/guarded/3", headers={"If-Match": '"2"'})
    assert (refused.status_code, problem_code(refused)) == (412, "precondition_failed")
    applied = await client.put("/guarded/3", headers={"If-Match": '"3"'})
    assert BODY.validate_json(applied.content) == {"version": 3}


def test_an_etag_is_the_version_in_quotes() -> None:
    assert etag(7) == '"7"'
    assert parse_if_match(etag(7)).admits(7)


# ── Idempotent POSTs ────────────────────────────────────────────────────────────


async def test_a_post_needs_an_idempotency_key_of_visible_ascii(client: httpx.AsyncClient) -> None:
    missing = await client.post("/keys")
    assert (missing.status_code, problem_code(missing)) == (422, "invalid_request")
    spaced = await client.post("/keys", headers={"Idempotency-Key": "two words"})
    assert spaced.status_code == 422
    given = await client.post("/keys", headers={"Idempotency-Key": "2b6a3c1e"})
    assert BODY.validate_json(given.content) == {"key": "2b6a3c1e"}


async def test_an_outcome_is_answered_as_it_was_stored(client: httpx.AsyncClient) -> None:
    fresh = await client.post("/answers/false")
    replayed = await client.post("/answers/true")
    for response in (fresh, replayed):
        assert response.status_code == 201
        assert response.content == b'{"name":"thing"}'
        assert response.headers["content-type"] == "application/json"
        assert response.headers["location"] == "/things/1"
    assert REPLAYED_HEADER.lower() not in fresh.headers
    assert replayed.headers[REPLAYED_HEADER] == "true"


def test_an_attempt_is_of_the_method_the_path_and_the_canonical_body() -> None:
    principal = Principal(
        subject=SubjectId("2001"), organization=organization_of("1001"), provider_organization="1001"
    )
    scope = {"type": "http", "method": "POST", "path": "/v1/things", "headers": [], "query_string": b""}
    request = Request(scope)
    with_body = attempt_of(request, principal, "k", Thing(name="a"))
    assert (
        with_body.fingerprint
        == attempt_of(request, principal, "k", Thing.model_validate_json('{ "name" : "a" }')).fingerprint
    )
    assert with_body.fingerprint != attempt_of(request, principal, "k").fingerprint
    assert (with_body.organization, with_body.subject, with_body.key) == (
        organization_of("1001"),
        "2001",
        "k",
    )
```

`libs/platform/tests/test_logs.py` — add (imports: `from opentelemetry.sdk.trace import TracerProvider`, `from opentelemetry.trace import format_span_id, format_trace_id`):

```python
def test_a_record_written_inside_a_span_carries_its_trace_and_span(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging(settings(LogFormat.JSON))
    tracer = TracerProvider().get_tracer("test")
    with tracer.start_as_current_span("work") as span:
        structlog.stdlib.get_logger("ledger.test").info("inside")
        logging.getLogger("lib").info("inside too")
    context = span.get_span_context()
    for line in lines(capsys.readouterr().out):
        assert (line["trace_id"], line["span_id"]) == (
            format_trace_id(context.trace_id),
            format_span_id(context.span_id),
        )


def test_a_record_written_outside_any_span_carries_no_trace(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(settings(LogFormat.JSON))
    structlog.stdlib.get_logger("ledger.test").info("outside")
    (line,) = lines(capsys.readouterr().out)
    assert "trace_id" not in line
    assert "span_id" not in line
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest libs/platform/tests/test_authorization.py libs/platform/tests/test_routes.py libs/platform/tests/test_logs.py -q --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'ledger_platform.authorization'`.

- [ ] **Step 4: Implement**

`libs/platform/src/ledger_platform/authorization.py`:

```python
"""Authorization: what a principal may do in a company, looked up per request and checked by name.

A ``PermissionSource`` answers with the permissions a principal holds in a company of its organization. The
identity service reads its memberships; every other service asks identity over gRPC (``identity_client``),
through a ``CachedPermissions``. An empty answer means no role there, and the company is then answered as not
found (``NoCompanyAccessError``): a caller learns nothing of companies it has no part in. A role without the
permission an operation needs is a ``PermissionDeniedError``.
"""

from abc import ABCMeta, abstractmethod
from collections import OrderedDict
from dataclasses import dataclass
from time import monotonic
from typing import ClassVar, Final, Protocol, final, override
lazy from collections.abc import Callable

from ledger_kernel.errors import ForbiddenError, NotFoundError
from ledger_platform.options import InvalidOptionError, require_at_least, require_seconds
lazy from ledger_kernel.access import Permission, Principal, SubjectId
lazy from ledger_kernel.ids import CompanyId, OrganizationId, Tenant

DEFAULT_TTL: Final = 30.0
"""Seconds an answer is kept: how long a revoked role may still work in another service, at most."""
DEFAULT_CAPACITY: Final = 10_000


class PermissionDeniedError(ForbiddenError):
    """The caller holds a role in the company, but not one that allows this."""

    code: ClassVar[str] = "permission_denied"

    permission: Permission

    def __init__(self, permission: Permission) -> None:
        super().__init__(f"this needs the permission {permission.value}")
        self.permission = permission


class NoCompanyAccessError(NotFoundError):
    """The caller holds no role in the company: for it, the company does not exist."""

    code: ClassVar[str] = "company_not_found"

    def __init__(self, company: CompanyId) -> None:
        super().__init__(f"company {company} was not found")


class InvalidAuthorizationOptionError(InvalidOptionError):
    """An authorization option that cannot work."""

    code: ClassVar[str] = "invalid_authorization_option"
    label: ClassVar[str] = "authorization option"


class PermissionSource(Protocol, metaclass=ABCMeta):
    """Where the permissions of a principal in a company come from.

    The method is abstract so that it needs no body: a ``...`` is a statement no test could reach.
    """

    @abstractmethod
    async def permissions(self, principal: Principal, company: CompanyId) -> frozenset[Permission]:
        """What ``principal`` may do in ``company`` of its organization; empty without a role there."""


@final
@dataclass(frozen=True, slots=True)
class CompanyAccess:
    """A request's caller, the company it acts on (with its organization) and what it may do there."""

    principal: Principal
    tenant: Tenant
    permissions: frozenset[Permission]

    def allows(self, permission: Permission) -> bool:
        """Whether the caller holds ``permission`` in the company."""
        return permission in self.permissions

    def require(self, permission: Permission) -> None:
        """Refuse with ``PermissionDeniedError`` unless the caller holds ``permission`` in the company."""
        if permission not in self.permissions:
            raise PermissionDeniedError(permission)


type _Key = tuple[OrganizationId, CompanyId, SubjectId]


@final
class CachedPermissions(PermissionSource):
    """A ``PermissionSource`` that keeps answers ``ttl`` seconds, the ``capacity`` most recently used of them.

    ``forget`` drops an answer at once, when identity announces a membership change. One fetched before a
    ``forget`` and arriving after it is handed to its caller but not kept: it may be the old one. Two requests
    that miss together ask the source twice; both answers are right.
    """

    __slots__ = ("_answers", "_capacity", "_clock", "_epoch", "_source", "_ttl")

    def __init__(
        self,
        source: PermissionSource,
        *,
        ttl: float = DEFAULT_TTL,
        capacity: int = DEFAULT_CAPACITY,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        require_seconds(InvalidAuthorizationOptionError, "ttl", ttl)
        require_at_least(InvalidAuthorizationOptionError, "capacity", capacity)
        self._source = source
        self._ttl = ttl
        self._capacity = capacity
        self._clock = clock
        self._answers: OrderedDict[_Key, tuple[float, frozenset[Permission]]] = OrderedDict()
        self._epoch = 0

    @override
    async def permissions(self, principal: Principal, company: CompanyId) -> frozenset[Permission]:
        key = (principal.organization, company, principal.subject)
        cached = self._answers.get(key)
        if cached is not None and self._clock() - cached[0] < self._ttl:
            self._answers.move_to_end(key)
            return cached[1]
        epoch = self._epoch
        answer = await self._source.permissions(principal, company)
        if epoch == self._epoch:  # nothing was forgotten while the source answered
            self._answers[key] = (self._clock(), answer)
            self._answers.move_to_end(key)
            while len(self._answers) > self._capacity:
                self._answers.popitem(last=False)
        return answer

    def forget(self, organization: OrganizationId, company: CompanyId, subject: SubjectId) -> None:
        """Drop what is kept about ``subject`` in ``company``, and keep no answer fetched before this."""
        self._epoch += 1
        self._answers.pop((organization, company, subject), None)
```

`libs/platform/src/ledger_platform/routes.py`:

```python
"""What a service's routes declare: who calls, for which company, and how the request applies.

* ``authenticated(verifier)``: a dependency giving the caller (``Principal``) its bearer token proves; 401
  without one that holds. It is also the OpenAPI document's security scheme (HTTP bearer, JWT).
* ``company_in_path(caller, permissions)`` and ``company_in_header(...)``: the company of the request (the
  path's ``company_id``, or the ``Ledger-Company-Id`` header) and what the caller may do there
  (``CompanyAccess``); 404 when it may do nothing there.
* ``page_request``: the ``limit`` and ``cursor`` of a list.
* ``if_match``: the versions an update may apply to; 428 without ``If-Match``.
* ``idempotency_key``: the ``Idempotency-Key`` every POST requires.
* ``attempt_of``, ``json_response`` and ``answer``: run a POST's work once per key, and answer it.

The dependencies bind what they learn (``organization_id``, ``subject``, ``company_id``) to the log context
for the rest of the request, and unbind it when the request ends.
"""

from dataclasses import dataclass
from http import HTTPStatus
from types import MappingProxyType
from typing import Annotated, Final, final
lazy from collections.abc import AsyncGenerator, Callable, Coroutine, Mapping

import structlog
from fastapi import Depends, Header, Path, Query, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.exceptions import HTTPException
lazy from fastapi import Request
lazy from pydantic import BaseModel

from ledger_kernel.ids import CompanyId, Tenant, parse_id
from ledger_kernel.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, PageRequest, decode_cursor
from ledger_platform.authentication import MissingTokenError
from ledger_platform.authorization import CompanyAccess, NoCompanyAccessError
from ledger_platform.idempotency import MAX_KEY_LENGTH, Attempt, StoredResponse
lazy from ledger_kernel.access import Principal
lazy from ledger_platform.authentication import TokenVerifier
lazy from ledger_platform.authorization import PermissionSource
lazy from ledger_platform.idempotency import Outcome

COMPANY_HEADER: Final = "Ledger-Company-Id"
REPLAYED_HEADER: Final = "Idempotent-Replayed"
JSON: Final = "application/json"
_ANY: Final = "*"
_MAX_VERSION_DIGITS: Final = 18
_NO_HEADERS: Final[Mapping[str, str]] = MappingProxyType({})
_BEARER: Final = HTTPBearer(
    auto_error=False,
    bearerFormat="JWT",
    description="An OAuth 2.0 access token (JWT) of the organization's identity provider.",
)

type Caller = Callable[[HTTPAuthorizationCredentials | None], AsyncGenerator[Principal]]
"""The dependency ``authenticated`` makes: a route declares ``Annotated[Principal, Depends(caller)]``."""
type PermissionsDependency = Callable[..., Coroutine[object, object, PermissionSource]]
"""A dependency handing the service's ``PermissionSource``; it may itself depend on the service's runtime."""
type InCompany = Callable[..., AsyncGenerator[CompanyAccess]]
"""The dependency ``company_in_path`` or ``company_in_header`` makes."""


def authenticated(verifier: TokenVerifier) -> Caller:
    """The dependency that gives a route its caller, proven by the request's bearer token."""

    async def caller(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_BEARER)],
    ) -> AsyncGenerator[Principal]:
        if credentials is None:
            raise MissingTokenError
        principal = await verifier.verify(credentials.credentials)
        with structlog.contextvars.bound_contextvars(
            organization_id=str(principal.organization), subject=principal.subject
        ):
            yield principal

    return caller


async def _granted(principal: Principal, company_id: str, source: PermissionSource) -> CompanyAccess:
    company = CompanyId(parse_id(company_id))
    permissions = await source.permissions(principal, company)
    if not permissions:
        raise NoCompanyAccessError(company)
    return CompanyAccess(principal, Tenant(principal.organization, company), permissions)


def company_in_path(caller: Caller, permissions: PermissionsDependency) -> InCompany:
    """A route's company from its ``company_id`` path parameter, with the caller's access to it."""

    async def access(
        company_id: Annotated[str, Path()],
        principal: Annotated[Principal, Depends(caller)],
        source: Annotated[PermissionSource, Depends(permissions)],
    ) -> AsyncGenerator[CompanyAccess]:
        granted = await _granted(principal, company_id, source)
        with structlog.contextvars.bound_contextvars(company_id=str(granted.tenant.company)):
            yield granted

    return access


def company_in_header(caller: Caller, permissions: PermissionsDependency) -> InCompany:
    """A route's company from the ``Ledger-Company-Id`` header, with the caller's access to it."""

    async def access(
        company_id: Annotated[str, Header(alias=COMPANY_HEADER)],
        principal: Annotated[Principal, Depends(caller)],
        source: Annotated[PermissionSource, Depends(permissions)],
    ) -> AsyncGenerator[CompanyAccess]:
        granted = await _granted(principal, company_id, source)
        with structlog.contextvars.bound_contextvars(company_id=str(granted.tenant.company)):
            yield granted

    return access


async def page_request(
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE, description="How many items the page holds.")] = (
        DEFAULT_PAGE_SIZE
    ),
    cursor: Annotated[
        str | None,
        Query(max_length=64, description="Where the page starts: the next_cursor of the page before."),
    ] = None,
) -> PageRequest:
    """The page a list request asks for."""
    return PageRequest(size=limit, after=None if cursor is None else decode_cursor(cursor))


@final
@dataclass(frozen=True, slots=True)
class IfMatch:
    """The versions an update may apply to; ``None`` for any (``If-Match: *``)."""

    versions: frozenset[int] | None

    def admits(self, version: int) -> bool:
        """Whether the current ``version`` is one the client allowed the update to apply to."""
        return self.versions is None or version in self.versions


def etag(version: int) -> str:
    """The strong entity tag of ``version``."""
    return f'"{version}"'


def _version_of(tag: str) -> int | None:
    """The version a strong tag of ours names; ``None`` for anything else (a weak tag never matches)."""
    digits = tag.removeprefix('"').removesuffix('"')
    if (
        tag != f'"{digits}"'
        or not 0 < len(digits) <= _MAX_VERSION_DIGITS
        or not digits.isascii()
        or not digits.isdigit()
    ):
        return None
    return int(digits)


def parse_if_match(value: str) -> IfMatch:
    """The ``If-Match`` header read with strong comparison (RFC 9110): ``*``, one tag or a list of them."""
    text = value.strip()
    if text == _ANY:
        return IfMatch(None)
    versions = (_version_of(part.strip()) for part in text.split(","))
    return IfMatch(frozenset(version for version in versions if version is not None))


async def if_match(value: Annotated[str | None, Header(alias="If-Match", max_length=1024)] = None) -> IfMatch:
    """The ``If-Match`` an update requires: 428 when the request has none."""
    if value is None:
        raise HTTPException(HTTPStatus.PRECONDITION_REQUIRED)
    return parse_if_match(value)


def precondition_failed() -> HTTPException:
    """What an update answers when ``If-Match`` names no current version: 412."""
    return HTTPException(HTTPStatus.PRECONDITION_FAILED)


async def idempotency_key(
    key: Annotated[
        str,
        Header(
            alias="Idempotency-Key",
            min_length=1,
            max_length=MAX_KEY_LENGTH,
            pattern=r"^[!-~]+$",
            description="Makes a retry safe: the same key and body change state once and answer the same.",
        ),
    ],
) -> str:
    """The ``Idempotency-Key`` every POST requires (§10)."""
    return key


def attempt_of(request: Request, principal: Principal, key: str, body: BaseModel | None = None) -> Attempt:
    """``principal``'s use of ``key`` for ``request``: the body counts as its validated, canonical JSON."""
    canonical = b"" if body is None else body.model_dump_json().encode()
    return Attempt.of(principal, key, method=request.method, path=request.url.path, body=canonical)


def json_response(status: int, body: BaseModel, headers: Mapping[str, str] = _NO_HEADERS) -> StoredResponse:
    """A JSON response as it is stored for replays."""
    return StoredResponse(status, body.model_dump_json().encode(), {"Content-Type": JSON, **headers})


def answer(outcome: Outcome) -> Response:
    """The response of an idempotent request: byte for byte the stored one, marked when it is a replay."""
    headers = dict(outcome.response.headers)
    if outcome.replayed:
        headers[REPLAYED_HEADER] = "true"
    return Response(content=outcome.response.body, status_code=outcome.response.status, headers=headers)
```

`libs/platform/src/ledger_platform/logs.py` — import the trace API and add the processor right after `merge_contextvars` in `_shared_processors`:

```python
from opentelemetry import trace
from opentelemetry.trace import format_span_id, format_trace_id
```

```python
def _trace_fields(logger: object, method_name: str, event_dict: EventDict) -> EventDict:
    """Every record written inside a span names its trace and span, so a log line leads to its trace."""
    del logger, method_name
    context = trace.get_current_span().get_span_context()
    if context.is_valid:
        event_dict.setdefault("trace_id", format_trace_id(context.trace_id))
        event_dict.setdefault("span_id", format_span_id(context.span_id))
    return event_dict


def _shared_processors(settings: ServiceSettings) -> list[Processor]:
    """What both structlog records and standard-library records go through, in this order."""
    return [
        structlog.contextvars.merge_contextvars,
        _trace_fields,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _ProcessFields(settings.service_name, settings.environment),
    ]
```

and amend the module docstring's last sentence: "... and the trace and span of the work in progress (``trace_id``, ``span_id``) are added to every record written inside a span."

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest libs/platform/tests/test_authorization.py libs/platform/tests/test_routes.py libs/platform/tests/test_logs.py -q --no-cov`
Expected: PASS.

- [ ] **Step 6: Gate**

Run: `uv run poe fmt && uv run poe check`
Expected: every step passes, coverage 100 %.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml libs/platform
git commit -m "feat(platform): request context: caller, company access, pages, preconditions, idempotency

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Contracts: the identity query service and the identity events

**Files:**
- Create: `libs/contracts/proto/ledger/identity/v1/identity_query.proto`, `libs/contracts/proto/ledger/events/identity/v1/events.proto`
- Generate (committed): `libs/contracts/src/ledger/identity/v1/identity_query_pb2.py`, `identity_query_pb2.pyi`, `identity_query_pb2_grpc.py`, `identity_query_pb2_grpc.pyi`; `libs/contracts/src/ledger/events/identity/v1/events_pb2.py`, `events_pb2.pyi`, `events_pb2_grpc.py`, `events_pb2_grpc.pyi`
- Test: `libs/contracts/tests/test_identity_contracts.py`

**Interfaces:**
- Consumes: `Envelope` (`ledger_contracts.envelope`).
- Produces: package `ledger.identity.v1`: service `IdentityQueryService` with `GetCompany(GetCompanyRequest{organization_id, company_id}) -> GetCompanyResponse{company: Company}` and `GetPermissions(GetPermissionsRequest{organization_id, company_id, subject}) -> GetPermissionsResponse{repeated string permissions}`; `Company{id, organization_id, tax_id, legal_name, status: CompanyStatus, version: int64}`; `CompanyStatus{COMPANY_STATUS_UNSPECIFIED, COMPANY_STATUS_ACTIVE, COMPANY_STATUS_INACTIVE}`. Generated Python: `IdentityQueryServiceStub` (an `aio.Channel` gives the async stub), `IdentityQueryServiceServicer`, `add_IdentityQueryServiceServicer_to_server`. Package `ledger.events.identity.v1`: `Role{ROLE_UNSPECIFIED, ROLE_OWNER, ROLE_ADMIN, ROLE_ACCOUNTANT, ROLE_ISSUER, ROLE_VIEWER}`; `CompanyCreated{company_id, organization_id, tax_id, legal_name, version}`; `CompanyUpdated{company_id, organization_id, tax_id, legal_name, version}`; `CompanyDeactivated{company_id, organization_id, version}`; `MembershipGranted{company_id, organization_id, subject, role, previous_role, version}`; `MembershipRevoked{company_id, organization_id, subject, role, version}`.

- [ ] **Step 1: Write the failing tests**

`libs/contracts/tests/test_identity_contracts.py`:

```python
from typing import Final

import pytest
lazy from google.protobuf.message import Message

from ledger.events.identity.v1 import events_pb2
from ledger.events.identity.v1.events_pb2 import (
    ROLE_ADMIN,
    ROLE_OWNER,
    ROLE_UNSPECIFIED,
    CompanyCreated,
    CompanyDeactivated,
    CompanyUpdated,
    MembershipGranted,
    MembershipRevoked,
)
from ledger.identity.v1 import identity_query_pb2
from ledger.identity.v1.identity_query_pb2 import (
    COMPANY_STATUS_ACTIVE,
    Company,
    GetCompanyRequest,
    GetCompanyResponse,
    GetPermissionsRequest,
    GetPermissionsResponse,
)
from ledger_contracts.envelope import Envelope
from ledger_kernel.ids import CompanyId, OrganizationId, Tenant, new_id

TENANT: Final = Tenant(OrganizationId(new_id()), CompanyId(new_id()))
EVENTS: Final[tuple[Message, ...]] = (
    CompanyCreated(),
    CompanyUpdated(),
    CompanyDeactivated(),
    MembershipGranted(),
    MembershipRevoked(),
)


@pytest.mark.parametrize("event", EVENTS, ids=[type(event).__name__ for event in EVENTS])
def test_every_identity_event_is_a_message_of_the_versioned_events_package(event: Message) -> None:
    assert event.DESCRIPTOR.full_name == f"ledger.events.identity.v1.{type(event).__name__}"


def test_an_identity_event_travels_in_an_envelope_and_back() -> None:
    event = MembershipGranted(
        company_id=str(TENANT.company),
        organization_id=str(TENANT.organization),
        subject="394763379928989699",
        role=ROLE_ADMIN,
        previous_role=ROLE_OWNER,
        version=2,
    )
    envelope = Envelope.wrap(event, source="identity", tenant=TENANT)
    read = Envelope.from_headers(envelope.headers(), envelope.data)
    assert read.type == "ledger.events.identity.v1.MembershipGranted"
    assert read.tenant == TENANT
    assert read.unwrap(MembershipGranted) == event


def test_a_first_grant_has_no_previous_role() -> None:
    assert MembershipGranted(role=ROLE_OWNER).previous_role == ROLE_UNSPECIFIED


def test_roles_are_named_as_identity_names_them() -> None:
    assert [value.name for value in events_pb2.DESCRIPTOR.enum_types_by_name["Role"].values] == [
        "ROLE_UNSPECIFIED",
        "ROLE_OWNER",
        "ROLE_ADMIN",
        "ROLE_ACCOUNTANT",
        "ROLE_ISSUER",
        "ROLE_VIEWER",
    ]


def test_the_query_service_answers_companies_and_permissions() -> None:
    service = identity_query_pb2.DESCRIPTOR.services_by_name["IdentityQueryService"]
    assert [(method.name, method.input_type.name, method.output_type.name) for method in service.methods] == [
        ("GetCompany", "GetCompanyRequest", "GetCompanyResponse"),
        ("GetPermissions", "GetPermissionsRequest", "GetPermissionsResponse"),
    ]


def test_queries_and_answers_round_trip() -> None:
    company = Company(
        id=str(TENANT.company),
        organization_id=str(TENANT.organization),
        tax_id="101850043",
        legal_name="Ferretería Duarte, S.R.L.",
        status=COMPANY_STATUS_ACTIVE,
        version=3,
    )
    messages: tuple[Message, ...] = (
        GetCompanyRequest(organization_id=str(TENANT.organization), company_id=str(TENANT.company)),
        GetCompanyResponse(company=company),
        GetPermissionsRequest(organization_id="o", company_id="c", subject="s"),
        GetPermissionsResponse(permissions=["company.read", "invoice.issue"]),
    )
    for message in messages:
        assert type(message).FromString(message.SerializeToString()) == message
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest libs/contracts -q --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'ledger.events'`.

- [ ] **Step 3: Write the contracts**

`libs/contracts/proto/ledger/identity/v1/identity_query.proto`:

```proto
syntax = "proto3";

package ledger.identity.v1;

// What other services ask the identity service, synchronously. Every request names the organization the
// caller acts in, and the answer never crosses into another organization's data. Ids are canonical UUIDs
// (8-4-4-4-12, lowercase).
service IdentityQueryService {
  // A company of the organization. NOT_FOUND, code company_not_found, when the organization has none by that
  // id.
  rpc GetCompany(GetCompanyRequest) returns (GetCompanyResponse);
  // What a principal may do in a company of the organization: empty when it holds no role there.
  rpc GetPermissions(GetPermissionsRequest) returns (GetPermissionsResponse);
}

message GetCompanyRequest {
  string organization_id = 1;
  string company_id = 2;
}

message GetCompanyResponse {
  Company company = 1;
}

// One taxpayer (an RNC or a cedula) of an organization.
message Company {
  string id = 1;
  string organization_id = 2;
  // Digits only: 9 for an RNC, 11 for a cedula.
  string tax_id = 3;
  string legal_name = 4;
  CompanyStatus status = 5;
  // One more with every change.
  int64 version = 6;
}

enum CompanyStatus {
  COMPANY_STATUS_UNSPECIFIED = 0;
  COMPANY_STATUS_ACTIVE = 1;
  COMPANY_STATUS_INACTIVE = 2;
}

message GetPermissionsRequest {
  string organization_id = 1;
  string company_id = 2;
  // The principal: the identity provider's id of the user or machine.
  string subject = 3;
}

message GetPermissionsResponse {
  // Stable permission codes ("company.read", "invoice.issue"). A reader ignores a code it does not know: a
  // newer identity may grant permissions an older reader never checks.
  repeated string permissions = 1;
}
```

`libs/contracts/proto/ledger/events/identity/v1/events.proto`:

```proto
syntax = "proto3";

package ledger.events.identity.v1;

// The role a membership grants in a company.
enum Role {
  ROLE_UNSPECIFIED = 0;
  ROLE_OWNER = 1;
  ROLE_ADMIN = 2;
  ROLE_ACCOUNTANT = 3;
  ROLE_ISSUER = 4;
  ROLE_VIEWER = 5;
}

// A company was registered in an organization. Subject identity.company.created.
message CompanyCreated {
  string company_id = 1;
  string organization_id = 2;
  // Digits only: 9 for an RNC, 11 for a cedula.
  string tax_id = 3;
  string legal_name = 4;
  // The company's version after the change: 1.
  int64 version = 5;
}

// A company's details changed. Subject identity.company.updated.
message CompanyUpdated {
  string company_id = 1;
  string organization_id = 2;
  string tax_id = 3;
  string legal_name = 4;
  int64 version = 5;
}

// A company was deactivated: it changes no more. Subject identity.company.deactivated.
message CompanyDeactivated {
  string company_id = 1;
  string organization_id = 2;
  int64 version = 3;
}

// A principal was given a role in a company, or its role changed. Subject identity.membership.granted.
message MembershipGranted {
  string company_id = 1;
  string organization_id = 2;
  string subject = 3;
  Role role = 4;
  // The role it held before; ROLE_UNSPECIFIED when it held none.
  Role previous_role = 5;
  // The membership's version after the change. It only grows: a revoked membership is kept, and granting a
  // role again continues from its version, so a reader can order a principal's changes in a company.
  int64 version = 6;
}

// A principal's role in a company was taken away. Subject identity.membership.revoked.
message MembershipRevoked {
  string company_id = 1;
  string organization_id = 2;
  string subject = 3;
  // The role it held.
  Role role = 4;
  // The membership's version after the change.
  int64 version = 5;
}
```

- [ ] **Step 4: Generate, lint and run the tests**

Run: `uv run poe proto && uv run poe proto-lint && uv run pytest libs/contracts -q --no-cov`
Expected: the eight generated files appear under `libs/contracts/src/ledger/identity/v1/` and `libs/contracts/src/ledger/events/identity/v1/`; `buf lint` prints nothing; the tests PASS.

- [ ] **Step 5: Gate**

Run: `uv run poe fmt && uv run poe check`
Expected: every step passes, coverage 100 %.

- [ ] **Step 6: Commit**

```bash
git add libs/contracts
git commit -m "feat(contracts): identity query service and identity events

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Platform: gRPC client helpers, remote permissions, membership changes

**Files:**
- Create: `libs/platform/src/ledger_platform/grpc_client.py`, `libs/platform/src/ledger_platform/identity_client.py`, `libs/testing/src/ledger_testing/caches.py`
- Modify: `libs/platform/src/ledger_platform/grpc_server.py` (imports `ERROR_CODE_KEY` from `grpc_client`), `libs/platform/pyproject.toml` (deptry learns the generated `ledger` package), `libs/testing/pyproject.toml`, `pyproject.toml` (root: import-linter layers)
- Test: `libs/platform/tests/test_grpc_client.py`, `libs/platform/tests/test_identity_client.py`; modify `libs/platform/tests/test_grpc_server.py` (imports `ERROR_CODE_KEY` from `grpc_client`)

**Interfaces:**
- Consumes: `serve_grpc`, `ErrorInterceptor` (`grpc_server`); `PermissionSource`, `CachedPermissions` (Task 6); `Permission`, `Principal`, `SubjectId`, `parse_subject` (Task 1); `IdentityQueryServiceStub`, `GetPermissionsRequest`, `MembershipGranted`, `MembershipRevoked` (Task 7); `Envelope`; `truncate` (kernel errors). Re-check `grpc_server.py` before starting: Milestone 1's final review adds gRPC RED metrics to its interceptor and moves the server's grace into `ServiceLimits`; only the line that defines `ERROR_CODE_KEY` changes here.
- Produces: in `ledger_platform.grpc_client`: `ERROR_CODE_KEY = "ledger-error-code"` (`grpc_server` imports it from here; every importer names this module), `DEFAULT_DEADLINE = 2.0`, `MAX_DEADLINE = 30.0`, `REMOTE_CODE_LIMIT = 64`, `RemoteError(rpc: str, status: grpc.StatusCode, remote_code: str)` (a message that names neither the call nor more of the other side than its status and its code, cut at `REMOTE_CODE_LIMIT`; `rpc` stays an attribute, for the log) with subclasses `RemoteNotFoundError` (NOT_FOUND, `remote_not_found`), `RemoteConflictError` (CONFLICT, `remote_conflict`), `RemoteForbiddenError` (FORBIDDEN, `remote_forbidden`), `RemoteUnavailableError` (UNAVAILABLE, `remote_unavailable`), `RemoteDefectError` (INTERNAL, `remote_defect`); `remote_error(error: grpc.aio.AioRpcError, rpc: str) -> RemoteError`; `async call_unary[Request, Response](method: grpc.aio.UnaryUnaryMultiCallable[Request, Response], request: Request, *, rpc: str, deadline: float = DEFAULT_DEADLINE) -> Response`; `InvalidGrpcClientOptionError`. In `ledger_platform.identity_client`: `GET_PERMISSIONS`, `MEMBERSHIP_SUBJECTS = "identity.membership.>"`, `known_permissions(codes: Iterable[str]) -> frozenset[Permission]`, `RemotePermissions(channel: grpc.aio.Channel, *, deadline: float = DEFAULT_DEADLINE)` (a `PermissionSource`), `PermissionCache` (protocol: `forget(organization, company, subject) -> None`; `CachedPermissions` is one), `Subscriber` (protocol: `async subscribe(subject, *, cb) -> Subscription`; a nats `Client` is one), `MembershipChanges(cache: PermissionCache)` with `async follow(nats: Subscriber) -> Subscription` and `async handle(message: Msg) -> None`. In `ledger_testing.caches`: `Forgotten` (a `PermissionCache` that records what it is told to forget, with an `asyncio.Event` `arrived` set by each), shared by these tests and identity's (Task 16).

- [ ] **Step 1: Dependencies and layering**

`libs/platform/pyproject.toml` — the platform now imports the generated `ledger.*` modules, which the editable `ledger-contracts` does not declare to `importlib.metadata`; deptry is told whose they are (identity's `pyproject.toml` does the same in Task 12):

```toml
[tool.deptry.package_module_name_map]
"ledger-contracts" = ["ledger_contracts", "ledger"]
"nats-py" = "nats"
"opentelemetry-api" = "opentelemetry"
"opentelemetry-sdk" = "opentelemetry"
"opentelemetry-exporter-otlp-proto-grpc" = "opentelemetry"
"prometheus-client" = "prometheus_client"
"pydantic-settings" = "pydantic_settings"
```

`libs/testing/pyproject.toml` — `ledger_testing.caches` speaks the kernel's ids; add to `dependencies` `"ledger-kernel"`, and:

```toml
[tool.uv.sources]
ledger-kernel = { workspace = true }
```

Run: `uv lock && uv sync`

In the root `pyproject.toml` contract "Platform layers":

```toml
    "web | grpc_server | consumer | routes | identity_client",
```

```toml
    "database | tracing | health | logs | authentication | grpc_client",
```

- [ ] **Step 2: Write the failing tests**

`libs/platform/tests/test_grpc_client.py`:

```python
import asyncio
import math
from typing import ClassVar, Final
lazy from collections.abc import AsyncGenerator

import grpc
import pytest

from ledger_kernel.errors import ErrorKind, NotFoundError
from ledger_platform.grpc_client import (
    ERROR_CODE_KEY,
    MAX_DEADLINE,
    REMOTE_CODE_LIMIT,
    InvalidGrpcClientOptionError,
    RemoteConflictError,
    RemoteDefectError,
    RemoteError,
    RemoteForbiddenError,
    RemoteNotFoundError,
    RemoteUnavailableError,
    call_unary,
)
from ledger_platform.grpc_server import serve_grpc

RPC: Final = "/test.Remote/Call"
LONG_CODE: Final = "x" * 1_000
"""A code no chassis server writes, but another side could (within gRPC's 8 KiB of metadata)."""
type Context = grpc.aio.ServicerContext[bytes, bytes]


class MissingThingError(NotFoundError):
    code: ClassVar[str] = "missing_thing"

    def __init__(self) -> None:
        super().__init__("no thing 4111111111111111")


async def behave(request: bytes, context: Context) -> bytes:
    """Answer as the request says: echo it, fail with a status by name, fail as a ledger error, or stall."""
    text = request.decode()
    if text == "missing":
        raise MissingThingError
    if text == "slow":
        await asyncio.sleep(5)
    if text == "bare":
        await context.abort(grpc.StatusCode.NOT_FOUND, "no code")
    if text == "long":
        await context.abort(
            grpc.StatusCode.NOT_FOUND, "long", trailing_metadata=((ERROR_CODE_KEY, LONG_CODE),)
        )
    if text in grpc.StatusCode.__members__:
        await context.abort(
            grpc.StatusCode[text], "because", trailing_metadata=((ERROR_CODE_KEY, f"{text.lower()}_x"),)
        )
    return request


def register(server: grpc.aio.Server) -> None:
    handler = grpc.unary_unary_rpc_method_handler(
        behave, request_deserializer=bytes, response_serializer=bytes
    )
    server.add_generic_rpc_handlers((grpc.method_handlers_generic_handler("test.Remote", {"Call": handler}),))


@pytest.fixture
async def call() -> AsyncGenerator[grpc.aio.UnaryUnaryMultiCallable[bytes, bytes]]:
    async with (
        serve_grpc(port=0, register=register) as port,
        grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel,
    ):
        yield channel.unary_unary(RPC, request_serializer=bytes, response_deserializer=bytes)


async def failure(
    call: grpc.aio.UnaryUnaryMultiCallable[bytes, bytes], request: bytes, deadline: float = 2.0
) -> RemoteError:
    with pytest.raises(RemoteError) as caught:
        await call_unary(call, request, rpc=RPC, deadline=deadline)
    return caught.value


async def test_an_answer_comes_back_as_it_was_sent(
    call: grpc.aio.UnaryUnaryMultiCallable[bytes, bytes],
) -> None:
    assert await call_unary(call, b"hello", rpc=RPC) == b"hello"


async def test_a_ledger_error_of_the_other_side_comes_back_with_its_kind_and_code(
    call: grpc.aio.UnaryUnaryMultiCallable[bytes, bytes],
) -> None:
    error = await failure(call, b"missing")
    assert isinstance(error, RemoteNotFoundError)
    assert (error.kind, error.code, error.remote_code, error.status, error.rpc) == (
        ErrorKind.NOT_FOUND,
        "remote_not_found",
        "missing_thing",
        grpc.StatusCode.NOT_FOUND,
        RPC,
    )
    assert str(error) == "a service this one depends on answered NOT_FOUND (missing_thing)"
    assert "4111" not in str(error)
    assert RPC not in str(error)  # a client that gets the message as a problem's detail learns no RPC name


async def test_a_code_longer_than_any_stable_code_is_cut(
    call: grpc.aio.UnaryUnaryMultiCallable[bytes, bytes],
) -> None:
    error = await failure(call, b"long")
    assert error.remote_code == "x" * REMOTE_CODE_LIMIT + "..."
    assert len(str(error)) < 2 * REMOTE_CODE_LIMIT


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("NOT_FOUND", RemoteNotFoundError),
        ("FAILED_PRECONDITION", RemoteConflictError),
        ("ALREADY_EXISTS", RemoteConflictError),
        ("ABORTED", RemoteConflictError),
        ("PERMISSION_DENIED", RemoteForbiddenError),
        ("UNAUTHENTICATED", RemoteForbiddenError),
        ("INVALID_ARGUMENT", RemoteDefectError),
        ("OUT_OF_RANGE", RemoteDefectError),
        ("UNIMPLEMENTED", RemoteDefectError),
        ("UNAVAILABLE", RemoteUnavailableError),
        ("RESOURCE_EXHAUSTED", RemoteUnavailableError),
        ("INTERNAL", RemoteUnavailableError),
        ("UNKNOWN", RemoteUnavailableError),
        ("DATA_LOSS", RemoteUnavailableError),
    ],
)
async def test_each_status_of_the_other_side_is_a_kind_here(
    call: grpc.aio.UnaryUnaryMultiCallable[bytes, bytes], status: str, expected: type[RemoteError]
) -> None:
    error = await failure(call, status.encode())
    assert type(error) is expected
    assert error.remote_code == f"{status.lower()}_x"


async def test_an_ending_without_a_code_says_it_is_unknown(
    call: grpc.aio.UnaryUnaryMultiCallable[bytes, bytes],
) -> None:
    error = await failure(call, b"bare")
    assert (type(error), error.remote_code) == (RemoteNotFoundError, "unknown")


async def test_a_call_past_its_deadline_is_unavailable(
    call: grpc.aio.UnaryUnaryMultiCallable[bytes, bytes],
) -> None:
    error = await failure(call, b"slow", deadline=0.1)
    assert (type(error), error.status, error.kind) == (
        RemoteUnavailableError,
        grpc.StatusCode.DEADLINE_EXCEEDED,
        ErrorKind.UNAVAILABLE,
    )


async def test_a_service_that_is_not_there_is_unavailable() -> None:
    async with grpc.aio.insecure_channel("127.0.0.1:1") as channel:
        nowhere = channel.unary_unary(RPC, request_serializer=bytes, response_deserializer=bytes)
        error = await failure(nowhere, b"hello", deadline=1.0)
    assert type(error) is RemoteUnavailableError


@pytest.mark.parametrize("deadline", [0.0, -1.0, math.inf, math.nan, MAX_DEADLINE + 1])
async def test_every_call_has_a_bounded_deadline(
    call: grpc.aio.UnaryUnaryMultiCallable[bytes, bytes], deadline: float
) -> None:
    with pytest.raises(InvalidGrpcClientOptionError, match="gRPC client option deadline must be"):
        await call_unary(call, b"hello", rpc=RPC, deadline=deadline)


def test_the_kinds_of_remote_errors() -> None:
    kinds: dict[type[RemoteError], ErrorKind] = {
        RemoteNotFoundError: ErrorKind.NOT_FOUND,
        RemoteConflictError: ErrorKind.CONFLICT,
        RemoteForbiddenError: ErrorKind.FORBIDDEN,
        RemoteUnavailableError: ErrorKind.UNAVAILABLE,
        RemoteDefectError: ErrorKind.INTERNAL,
    }
    assert {error: error.kind for error in kinds} == kinds
```

`libs/platform/tests/test_identity_client.py`:

```python
from typing import Final, final, override
lazy from collections.abc import AsyncGenerator, Awaitable, Callable

import grpc
import pytest
from nats.aio.client import Client
from nats.aio.msg import Msg
from structlog.testing import capture_logs

from ledger.events.identity.v1.events_pb2 import (
    ROLE_ADMIN,
    ROLE_OWNER,
    CompanyCreated,
    MembershipGranted,
    MembershipRevoked,
)
from ledger.identity.v1.identity_query_pb2 import (
    GetCompanyRequest,
    GetCompanyResponse,
    GetPermissionsRequest,
    GetPermissionsResponse,
)
from ledger.identity.v1.identity_query_pb2_grpc import (
    IdentityQueryServiceServicer,
    add_IdentityQueryServiceServicer_to_server,
)
from ledger_contracts.envelope import Envelope
from ledger_kernel.access import Permission, Principal, SubjectId
from ledger_kernel.errors import NotFoundError
from ledger_kernel.ids import CompanyId, OrganizationId, Tenant, new_id
from ledger_platform.authorization import CachedPermissions
from ledger_platform.grpc_client import RemoteUnavailableError
from ledger_platform.grpc_server import serve_grpc
from ledger_platform.identity_client import (
    GET_PERMISSIONS,
    MEMBERSHIP_SUBJECTS,
    MembershipChanges,
    RemotePermissions,
    known_permissions,
)
from ledger_testing.caches import Forgotten

ORGANIZATION: Final = OrganizationId(new_id())
COMPANY: Final = CompanyId(new_id())
ALICE: Final = Principal(subject=SubjectId("2001"), organization=ORGANIZATION, provider_organization="1001")
TENANT: Final = Tenant(ORGANIZATION, COMPANY)

type Context = grpc.aio.ServicerContext[GetPermissionsRequest, GetPermissionsResponse]
type CompanyContext = grpc.aio.ServicerContext[GetCompanyRequest, GetCompanyResponse]


@final
class FakeIdentity(IdentityQueryServiceServicer):
    """The identity service as far as the platform's client can tell."""

    def __init__(self) -> None:
        self.asked: list[GetPermissionsRequest] = []
        self.codes = ["company.read", "invoice.issue", "spaceship.fly"]

    @override
    async def GetCompany(self, request: GetCompanyRequest, context: CompanyContext) -> GetCompanyResponse:
        del request, context
        raise NotFoundError

    @override
    async def GetPermissions(
        self, request: GetPermissionsRequest, context: Context
    ) -> GetPermissionsResponse:
        del context
        self.asked.append(request)
        return GetPermissionsResponse(permissions=self.codes)


@pytest.fixture
async def identity() -> AsyncGenerator[tuple[FakeIdentity, grpc.aio.Channel]]:
    fake = FakeIdentity()

    def register(server: grpc.aio.Server) -> None:
        add_IdentityQueryServiceServicer_to_server(fake, server)

    async with (
        serve_grpc(port=0, register=register) as port,
        grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel,
    ):
        yield fake, channel


def message(envelope: Envelope, subject: str = "identity.membership.granted") -> Msg:
    return Msg(Client(), subject=subject, data=envelope.data, headers=envelope.headers())


def granted(organization: str = str(ORGANIZATION), subject: str = "2001") -> Envelope:
    event = MembershipGranted(
        company_id=str(COMPANY), organization_id=organization, subject=subject, role=ROLE_ADMIN, version=2
    )
    return Envelope.wrap(event, source="identity", tenant=TENANT)


# ── Remote permissions ──────────────────────────────────────────────────────────


async def test_permissions_are_asked_of_identity_for_the_callers_organization(
    identity: tuple[FakeIdentity, grpc.aio.Channel],
) -> None:
    fake, channel = identity
    answer = await RemotePermissions(channel).permissions(ALICE, COMPANY)
    assert answer == frozenset({Permission.COMPANY_READ, Permission.INVOICE_ISSUE})
    (request,) = fake.asked
    assert (request.organization_id, request.company_id, request.subject) == (
        str(ORGANIZATION),
        str(COMPANY),
        "2001",
    )


def test_codes_this_version_does_not_know_are_ignored() -> None:
    assert known_permissions(["company.read", "spaceship.fly", ""]) == frozenset({Permission.COMPANY_READ})
    assert GET_PERMISSIONS == "/ledger.identity.v1.IdentityQueryService/GetPermissions"


async def test_an_identity_that_is_not_there_makes_permissions_unavailable() -> None:
    async with grpc.aio.insecure_channel("127.0.0.1:1") as channel:
        with pytest.raises(RemoteUnavailableError):
            await RemotePermissions(channel, deadline=1.0).permissions(ALICE, COMPANY)


async def test_a_cache_in_front_of_identity_asks_once_per_ttl(
    identity: tuple[FakeIdentity, grpc.aio.Channel],
) -> None:
    fake, channel = identity
    cache = CachedPermissions(RemotePermissions(channel))
    for _ in range(3):
        await cache.permissions(ALICE, COMPANY)
    assert len(fake.asked) == 1


# ── Membership changes ──────────────────────────────────────────────────────────


async def test_a_grant_makes_the_cache_forget_the_principal_in_the_company() -> None:
    cache = Forgotten()
    await MembershipChanges(cache).handle(message(granted()))
    assert cache.forgotten == [(ORGANIZATION, COMPANY, "2001")]


async def test_a_revocation_does_too() -> None:
    cache = Forgotten()
    event = MembershipRevoked(
        company_id=str(COMPANY), organization_id=str(ORGANIZATION), subject="2001", role=ROLE_OWNER, version=3
    )
    await MembershipChanges(cache).handle(
        message(Envelope.wrap(event, source="identity", tenant=TENANT), "identity.membership.revoked")
    )
    assert cache.forgotten == [(ORGANIZATION, COMPANY, "2001")]


async def test_another_event_on_the_subjects_changes_nothing() -> None:
    cache = Forgotten()
    other = Envelope.wrap(CompanyCreated(company_id=str(COMPANY)), source="identity", tenant=TENANT)
    await MembershipChanges(cache).handle(message(other))
    assert cache.forgotten == []


def unreadable(kind: str) -> Msg:
    match kind:
        case "no-envelope":
            return Msg(Client(), subject="identity.membership.granted", data=b"", headers=None)
        case "bad-organization":
            return message(granted(organization="not-an-id"))
        case "bad-subject":
            return message(granted(subject="not a subject"))
        case _:
            return Msg(
                Client(), subject="identity.membership.granted", data=b"\xff\xff", headers=granted().headers()
            )


@pytest.mark.parametrize("kind", ["no-envelope", "bad-organization", "bad-subject", "bad-payload"])
async def test_an_event_that_cannot_be_read_is_logged_and_skipped(kind: str) -> None:
    cache = Forgotten()
    with capture_logs() as logs:
        await MembershipChanges(cache).handle(unreadable(kind))
    assert cache.forgotten == []
    assert [log["event"] for log in logs] == ["authorization.membership_event_unreadable"]
    assert "not a subject" not in str(logs)


async def test_every_replica_following_the_subjects_hears_every_change(
    connect: Callable[[], Awaitable[Client]],
    eventually: Callable[[Callable[[], Awaitable[bool]]], Awaitable[None]],
) -> None:
    first, second = Forgotten(), Forgotten()
    one, other, publisher = await connect(), await connect(), await connect()
    await MembershipChanges(first).follow(one)
    await MembershipChanges(second).follow(other)
    await one.flush()
    await other.flush()
    envelope = granted()
    await publisher.publish("identity.membership.granted", envelope.data, headers=envelope.headers())

    async def both_heard() -> bool:
        return len(first.forgotten) == len(second.forgotten) == 1

    await eventually(both_heard)
    assert MEMBERSHIP_SUBJECTS == "identity.membership.>"
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest libs/platform/tests/test_grpc_client.py libs/platform/tests/test_identity_client.py -q --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'ledger_platform.grpc_client'`.

- [ ] **Step 4: Implement**

`libs/platform/src/ledger_platform/grpc_client.py`:

```python
"""Calls to other services' gRPC methods: always with a deadline, their errors back as ledger errors.

A chassis server ends a failed call with the status of the error's kind and the error's code in the trailing
metadata ``ledger-error-code`` (``grpc_server``). ``call_unary`` turns that back into a ``RemoteError`` of the
matching kind, keeping the remote code, so that a route that called another service answers as if the error
were its own: a company the other service does not have is a 404 here too. What the other side wrote in its
details is not repeated (it may quote what was asked), its code is cut at ``REMOTE_CODE_LIMIT``, and the
message names no call: a client that gets it as a problem's ``detail`` learns nothing of the services behind
the API, while ``RemoteError.rpc`` names the call for the log.

A call's deadline is fixed by its caller, at most ``MAX_DEADLINE``. Deriving it from the remaining deadline of
the request being served, and retrying idempotent methods, come with the first service-to-service caller
(milestone 3), as the spec records (§4.2).

Statuses map so: ``NOT_FOUND`` is not found; ``FAILED_PRECONDITION``, ``ALREADY_EXISTS`` and ``ABORTED`` are a
conflict; ``PERMISSION_DENIED`` and ``UNAUTHENTICATED`` are forbidden (this service, not its client, was
refused); ``INVALID_ARGUMENT``, ``OUT_OF_RANGE`` and ``UNIMPLEMENTED`` are a defect of ours (we sent what the
other side cannot take); anything else, a passed deadline and the other side's own defects included, is
unavailable: a dependency failed, and a retry may work.
"""

from typing import ClassVar, Final
lazy from collections.abc import Mapping

import grpc

from ledger_kernel.errors import ErrorKind, LedgerError, truncate
from ledger_platform.options import InvalidOptionError, require_seconds

ERROR_CODE_KEY: Final = "ledger-error-code"
"""The trailing metadata that carries a failed call's stable code."""
DEFAULT_DEADLINE: Final = 2.0
MAX_DEADLINE: Final = 30.0
"""No call between services waits longer: a request's own deadline is shorter."""
REMOTE_CODE_LIMIT: Final = 64
"""What is kept of the other side's code: a stable code is a short word, anything longer is cut."""
_UNKNOWN_CODE: Final = "unknown"


class InvalidGrpcClientOptionError(InvalidOptionError):
    """A gRPC client option that cannot work."""

    code: ClassVar[str] = "invalid_grpc_client_option"
    label: ClassVar[str] = "gRPC client option"


class RemoteError(LedgerError):
    """Another service answered a call with an error: which call, its status and the other side's code.

    The message says the status and the code only; ``rpc`` is for the log.
    """

    code: ClassVar[str] = "remote_error"

    rpc: str
    status: grpc.StatusCode
    remote_code: str

    def __init__(self, rpc: str, status: grpc.StatusCode, remote_code: str) -> None:
        kept = truncate(remote_code, REMOTE_CODE_LIMIT)
        super().__init__(f"a service this one depends on answered {status.name} ({kept})")
        self.rpc = rpc
        self.status = status
        self.remote_code = kept


class RemoteNotFoundError(RemoteError):
    """The other service does not have what was asked for."""

    kind: ClassVar[ErrorKind] = ErrorKind.NOT_FOUND
    code: ClassVar[str] = "remote_not_found"


class RemoteConflictError(RemoteError):
    """The other service's state forbids the call."""

    kind: ClassVar[ErrorKind] = ErrorKind.CONFLICT
    code: ClassVar[str] = "remote_conflict"


class RemoteForbiddenError(RemoteError):
    """The other service refused this service."""

    kind: ClassVar[ErrorKind] = ErrorKind.FORBIDDEN
    code: ClassVar[str] = "remote_forbidden"


class RemoteUnavailableError(RemoteError):
    """The other service could not answer: down, too slow, or failing; a retry may work."""

    kind: ClassVar[ErrorKind] = ErrorKind.UNAVAILABLE
    code: ClassVar[str] = "remote_unavailable"


class RemoteDefectError(RemoteError):
    """The other service could not take what this one sent: a defect of ours."""

    code: ClassVar[str] = "remote_defect"


_BY_STATUS: Final[Mapping[grpc.StatusCode, type[RemoteError]]] = {
    grpc.StatusCode.NOT_FOUND: RemoteNotFoundError,
    grpc.StatusCode.FAILED_PRECONDITION: RemoteConflictError,
    grpc.StatusCode.ALREADY_EXISTS: RemoteConflictError,
    grpc.StatusCode.ABORTED: RemoteConflictError,
    grpc.StatusCode.PERMISSION_DENIED: RemoteForbiddenError,
    grpc.StatusCode.UNAUTHENTICATED: RemoteForbiddenError,
    grpc.StatusCode.INVALID_ARGUMENT: RemoteDefectError,
    grpc.StatusCode.OUT_OF_RANGE: RemoteDefectError,
    grpc.StatusCode.UNIMPLEMENTED: RemoteDefectError,
}


def remote_error(error: grpc.aio.AioRpcError, rpc: str) -> RemoteError:
    """The ledger error a failed call to ``rpc`` stands for (see the module)."""
    code = dict(error.trailing_metadata() or ()).get(ERROR_CODE_KEY)
    status = error.code()
    return _BY_STATUS.get(status, RemoteUnavailableError)(
        rpc, status, code if isinstance(code, str) else _UNKNOWN_CODE
    )


async def call_unary[Request, Response](
    method: grpc.aio.UnaryUnaryMultiCallable[Request, Response],
    request: Request,
    *,
    rpc: str,
    deadline: float = DEFAULT_DEADLINE,
) -> Response:
    """Call ``method`` (named ``rpc``, ``/package.Service/Method``) within ``deadline`` seconds.

    A failure is the ``RemoteError`` the module describes; the deadline must be positive and at most
    ``MAX_DEADLINE``.
    """
    require_seconds(InvalidGrpcClientOptionError, "deadline", deadline, ceiling=MAX_DEADLINE)
    try:
        return await method(request, timeout=deadline)
    except grpc.aio.AioRpcError as error:
        raise remote_error(error, rpc) from error
```

In `libs/platform/src/ledger_platform/grpc_server.py`, the constant moves to the client module and the server imports it:

```python
from ledger_platform.grpc_client import ERROR_CODE_KEY
```

and the line `ERROR_CODE_KEY: Final = "ledger-error-code"` goes. Nothing imports the name from `grpc_server` any more (a plain import is not a re-export under mypy `--strict` and basedpyright, and an `as` alias trips ruff `PLC0414`): in `libs/platform/tests/test_grpc_server.py`, `ERROR_CODE_KEY` leaves the `from ledger_platform.grpc_server import (...)` list and is imported with

```python
from ledger_platform.grpc_client import ERROR_CODE_KEY
```

`libs/testing/src/ledger_testing/caches.py`:

```python
"""A permission cache that remembers what it was told to forget: for the tests of membership following."""

import asyncio
from typing import final

lazy from ledger_kernel.access import SubjectId
lazy from ledger_kernel.ids import CompanyId, OrganizationId


@final
class Forgotten:
    """Records every ``forget``; each sets ``arrived``, for a test waiting on events delivered by NATS."""

    __slots__ = ("arrived", "forgotten")

    def __init__(self) -> None:
        self.forgotten: list[tuple[OrganizationId, CompanyId, SubjectId]] = []
        self.arrived = asyncio.Event()

    def forget(self, organization: OrganizationId, company: CompanyId, subject: SubjectId) -> None:
        """Note that what is kept about ``subject`` in ``company`` was dropped."""
        self.forgotten.append((organization, company, subject))
        self.arrived.set()
```

`libs/platform/src/ledger_platform/identity_client.py`:

```python
"""The platform's client of the identity service: permissions for the authorization context, kept fresh.

``RemotePermissions`` asks ``IdentityQueryService.GetPermissions``, with a deadline on every call. A service
puts it behind a ``CachedPermissions``, and ``MembershipChanges`` drops what a membership event changed.
Every replica follows the events itself, on a core NATS subscription: a durable consumer would hand each event
to one replica only. Following is best effort; the cache's TTL bounds what a lost event costs.

A service opens its channel to identity as one of its resources (``Service``'s resources hook), so that the
channel is closed in order when the service stops; the first such service comes in milestone 3.
"""

from abc import ABCMeta, abstractmethod
from typing import Final, Protocol, final, override
lazy from collections.abc import Awaitable, Callable, Iterable, Mapping

import structlog
lazy import grpc
lazy from nats.aio.msg import Msg
lazy from nats.aio.subscription import Subscription

from ledger.events.identity.v1.events_pb2 import MembershipGranted, MembershipRevoked
from ledger.identity.v1.identity_query_pb2 import GetPermissionsRequest
from ledger.identity.v1.identity_query_pb2_grpc import IdentityQueryServiceStub
from ledger_contracts.envelope import Envelope
from ledger_kernel.access import Permission, parse_subject
from ledger_kernel.errors import InvalidInputError
from ledger_kernel.ids import CompanyId, OrganizationId, parse_id
from ledger_platform.authorization import PermissionSource
from ledger_platform.grpc_client import DEFAULT_DEADLINE, call_unary
lazy from ledger_kernel.access import Principal, SubjectId

GET_PERMISSIONS: Final = "/ledger.identity.v1.IdentityQueryService/GetPermissions"
MEMBERSHIP_SUBJECTS: Final = "identity.membership.>"
_GRANTED: Final = MembershipGranted.DESCRIPTOR.full_name
_REVOKED: Final = MembershipRevoked.DESCRIPTOR.full_name
_PERMISSIONS: Final[Mapping[str, Permission]] = {permission.value: permission for permission in Permission}
_logger = structlog.stdlib.get_logger(__name__)


def known_permissions(codes: Iterable[str]) -> frozenset[Permission]:
    """The permissions among ``codes`` that this version knows; a newer identity may grant others."""
    return frozenset(_PERMISSIONS[code] for code in codes if code in _PERMISSIONS)


@final
class RemotePermissions(PermissionSource):
    """Permissions as the identity service answers them, over gRPC."""

    __slots__ = ("_deadline", "_get_permissions")

    def __init__(self, channel: grpc.aio.Channel, *, deadline: float = DEFAULT_DEADLINE) -> None:
        self._get_permissions = IdentityQueryServiceStub(channel).GetPermissions
        self._deadline = deadline

    @override
    async def permissions(self, principal: Principal, company: CompanyId) -> frozenset[Permission]:
        request = GetPermissionsRequest(
            organization_id=str(principal.organization), company_id=str(company), subject=principal.subject
        )
        response = await call_unary(
            self._get_permissions, request, rpc=GET_PERMISSIONS, deadline=self._deadline
        )
        return known_permissions(response.permissions)


class PermissionCache(Protocol, metaclass=ABCMeta):
    """What ``MembershipChanges`` tells: a ``CachedPermissions`` is one."""

    @abstractmethod
    def forget(self, organization: OrganizationId, company: CompanyId, subject: SubjectId) -> None:
        """Drop what is kept about ``subject`` in ``company``."""


class Subscriber(Protocol, metaclass=ABCMeta):
    """What ``MembershipChanges`` asks of a NATS connection: a ``nats.aio.client.Client`` is one.

    nats-py leaves the type of ``subscribe``'s ``future`` open; seen through this protocol, only the ones
    used here remain.
    """

    @abstractmethod
    async def subscribe(self, subject: str, *, cb: Callable[[Msg], Awaitable[None]]) -> Subscription:
        """Deliver every message on ``subject`` to ``cb``."""


def _member(organization: str, company: str, subject: str) -> tuple[OrganizationId, CompanyId, SubjectId]:
    return OrganizationId(parse_id(organization)), CompanyId(parse_id(company)), parse_subject(subject)


def _changed(envelope: Envelope) -> tuple[OrganizationId, CompanyId, SubjectId] | None:
    """Whose permissions a membership event changed; ``None`` for any other event."""
    if envelope.type == _GRANTED:
        grant = envelope.unwrap(MembershipGranted)
        return _member(grant.organization_id, grant.company_id, grant.subject)
    if envelope.type == _REVOKED:
        revocation = envelope.unwrap(MembershipRevoked)
        return _member(revocation.organization_id, revocation.company_id, revocation.subject)
    return None


@final
class MembershipChanges:
    """Keeps a permission cache honest: drops what identity announces has changed."""

    __slots__ = ("_cache",)

    def __init__(self, cache: PermissionCache) -> None:
        self._cache = cache

    async def follow(self, nats: Subscriber) -> Subscription:
        """Follow every membership event on this replica; unsubscribe, or drain the connection, to stop."""
        return await nats.subscribe(MEMBERSHIP_SUBJECTS, cb=self.handle)

    async def handle(self, message: Msg) -> None:
        """Forget the permissions an event changed; one that cannot be read is logged and skipped."""
        try:
            changed = _changed(Envelope.from_headers(message.headers or {}, message.data))
        except InvalidInputError as error:
            _logger.warning(
                "authorization.membership_event_unreadable", subject=message.subject, code=error.code
            )
            return
        if changed is not None:
            self._cache.forget(*changed)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest libs/platform/tests/test_grpc_client.py libs/platform/tests/test_identity_client.py libs/platform/tests/test_grpc_server.py -q --no-cov`
Expected: PASS.

- [ ] **Step 6: Gate**

Run: `uv run poe fmt && uv run poe check`
Expected: every step passes, coverage 100 %.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock libs/platform libs/testing
git commit -m "feat(platform): gRPC client errors, remote permissions and membership change following

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: The identity service's package and its domain

**Files:**
- Create: `services/identity/pyproject.toml`, `services/identity/src/ledger_identity/__init__.py`, `py.typed`
- Create: `services/identity/src/ledger_identity/domain/__init__.py`, `ids.py`, `errors.py`, `text.py`, `roles.py`, `events.py`, `organization.py`, `company.py`, `branches.py`, `membership.py`
- Modify: `pyproject.toml` (root)
- Test: `services/identity/tests/test_domain.py`

**Interfaces:**
- Consumes: `TaxId` (`ledger_kernel.countries.do`), `Tenant`, `OrganizationId`, `CompanyId`, `new_id` (kernel ids), `Principal`, `SubjectId`, `Permission` (Task 1), the kernel errors.
- Produces (all under `ledger_identity.domain`):
  - `ids`: `BranchId`, `EmissionPointId`, `MembershipId` (`NewType` of `UUID`).
  - `text`: `LegalName`, `Name`, `Code` (`NewType` of `str`); `MAX_LEGAL_NAME = 150`, `MAX_NAME = 100`, `MAX_CODE = 10`; `parse_legal_name(text: str) -> LegalName`, `parse_name(text: str, *, field: str = "name") -> Name`, `parse_code(text: str) -> Code`.
  - `errors`: `InvalidNameError(field: str, problem: str)` (`invalid_name`), `InvalidCodeError()` (`invalid_code`), `OrganizationNotFoundError(organization)` (`organization_not_found`), `CompanyNotFoundError(company)` (`company_not_found`), `BranchNotFoundError(branch)` (`branch_not_found`), `EmissionPointNotFoundError(point)` (`emission_point_not_found`), `MembershipNotFoundError(subject)` (`membership_not_found`; the subject quoted as an excerpt), `TaxIdTakenError()` (409 `tax_id_taken`), `BranchCodeTakenError()` (409 `branch_code_taken`), `EmissionPointCodeTakenError()` (409 `emission_point_code_taken`), `CompanyInactiveError(company)` (409 `company_inactive`), `StaleVersionError(company)` (409 `stale_version`), `LastOwnerError()` (409 `last_owner`), `OwnersOnlyError()` (403 `owners_only`), `ActorNotAllowedError(permission: Permission)` (403 `permission_denied`: the actor's membership, as it is now, lacks the permission).
  - `roles`: `Role(StrEnum)` `OWNER`, `ADMIN`, `ACCOUNTANT`, `ISSUER`, `VIEWER`; `PERMISSIONS: Mapping[Role, frozenset[Permission]]`; `permissions_of(role: Role) -> frozenset[Permission]`; `READ_ONLY: frozenset[Permission]` (`company.read`, `membership.read`, `invoice.read`).
  - `events`: frozen keyword-only dataclasses `CompanyCreated(tenant, tax_id: TaxId, legal_name: LegalName, version: int)`, `CompanyUpdated(tenant, tax_id, legal_name, version)`, `CompanyDeactivated(tenant, version)`, `MembershipGranted(tenant, subject: SubjectId, role: Role, previous: Role | None, version: int)`, `MembershipRevoked(tenant, subject, role, version)`; `type IdentityEvent` = their union.
  - `organization`: `Organization(*, id: OrganizationId, provider_id: str, created_at: datetime)` with `classmethod of(principal: Principal, *, now: datetime) -> Organization`.
  - `company`: `CompanyStatus(StrEnum)` `ACTIVE`, `INACTIVE`; `Company(*, id, organization, tax_id, legal_name, status, version, created_at, updated_at)` with `tenant: Tenant`, `require_active() -> None`, `classmethod register(*, organization, tax_id, legal_name, now) -> tuple[Company, CompanyCreated]`, `rename(legal_name, *, now) -> tuple[Company, CompanyUpdated | None]`, `deactivate(*, now) -> tuple[Company, CompanyDeactivated]`.
  - `branches`: `Branch(*, id: BranchId, tenant, code: Code, name: Name, created_at)` with `classmethod open(company, *, code, name, now) -> Branch`; `EmissionPoint(*, id: EmissionPointId, tenant, branch: BranchId, code, name, created_at)` with `classmethod open(company, branch, *, code, name, now) -> EmissionPoint`.
  - `membership`: `MembershipStatus(StrEnum)` `ACTIVE`, `REVOKED`; `Membership(*, id: MembershipId, tenant, subject, role, status, version, changed_at, changed_by: SubjectId)` with `active: bool` and `permissions: frozenset[Permission]` (none once revoked); `Actor(subject: SubjectId, permissions: frozenset[Permission])` with `manages_memberships`, `manages_ownership`; `Roster(company: Company, current: Membership | None, owners: int)` (`current`: the subject's membership, active or revoked; `owners`: active owners); `found(company, founder: Principal, *, now) -> tuple[Membership, MembershipGranted]`; `grant(roster, *, subject, role, actor, now) -> tuple[Membership, MembershipGranted | None]`; `revoke(roster, *, subject: SubjectId, actor, now) -> tuple[Membership, MembershipRevoked]`; `permissions_in(company: Company, membership: Membership) -> frozenset[Permission]` (what other services may let the member do: the reading permissions only in a deactivated company).

- [ ] **Step 1: The package and its place in the workspace**

`services/identity/pyproject.toml`:

```toml
[project]
name = "ledger-identity"
version = "0.1.0"
description = "The identity service: organizations, companies, branches, emission points, memberships and roles."
requires-python = ">=3.15"
dependencies = ["ledger-kernel"]

[tool.uv.sources]
ledger-kernel = { workspace = true }

[build-system]
requires = ["uv_build>=0.13.0,<0.14.0"]
build-backend = "uv_build"
```

`services/identity/src/ledger_identity/__init__.py`:

```python
"""The identity service: organizations, companies, branches, emission points, memberships, roles."""
```

`services/identity/src/ledger_identity/py.typed`: empty file.

`services/identity/src/ledger_identity/domain/__init__.py`:

```python
"""What identity is about, in the standard library and the kernel only: no SQL, no HTTP, no wire format."""
```

Root `pyproject.toml`:

```toml
[project]
# ...
dependencies = ["ledger-kernel", "ledger-contracts", "ledger-platform", "ledger-identity"]
```

```toml
[tool.uv.workspace]
members = ["libs/*", "services/*"]

[tool.uv.sources]
ledger-kernel = { workspace = true }
ledger-contracts = { workspace = true }
ledger-platform = { workspace = true }
ledger-testing = { workspace = true }
ledger-identity = { workspace = true }
```

In `[tool.poe.tasks]`: add `mypy-identity = { cmd = "mypy services/identity" }` and append `"mypy-identity"` to the `mypy` sequence; add `deps-identity = { cmd = "deptry services/identity/src --config services/identity/pyproject.toml" }` and append `"deps-identity"` to the `deps` sequence.

In `[tool.ruff.lint.per-file-ignores]` add the same entry for services' tests:

```toml
"services/*/tests/**" = [
    "S101",    # assert is the point of a test
    "D",       # test names are the documentation
    "PLR2004", # magic values are fine in assertions
    "S105",    # fake credentials are not secrets
    "S106",
    "S603",    # a test may run this interpreter on code of its own
    "INP001",  # tests are not a package: each package's tests/ is its own root
]
```

(The same list as `"libs/*/tests/**"`; if Milestone 1's final review has changed that list, copy it as it stands.)

In `[tool.ruff.lint.isort]` add `"ledger_identity"` to `known-first-party`. In `[tool.basedpyright]` set `include = ["libs", "services", "conftest.py"]`. In `[tool.pytest]` set `testpaths = ["libs", "services"]`. In `[tool.coverage.run]` add `"ledger_identity"` to `source_pkgs`. In `[tool.importlinter]` add `"ledger_identity"` to `root_packages` and to the `source_modules` of "Production code never imports test tooling", and add:

```toml
[[tool.importlinter.contracts]]
name = "Libraries never import services"
type = "forbidden"
source_modules = ["ledger_kernel", "ledger_contracts", "ledger_platform", "ledger_testing"]
forbidden_modules = ["ledger_identity"]

[[tool.importlinter.contracts]]
name = "Identity's domain is the standard library and the kernel"
type = "forbidden"
source_modules = ["ledger_identity.domain"]
forbidden_modules = [
    "ledger_contracts", "ledger_platform", "pydantic", "sqlalchemy", "fastapi", "starlette", "grpc", "nats",
    "structlog", "google", "psycopg",
]
```

Run: `uv lock && uv sync`
Expected: `ledger-identity` installed in the workspace environment. If Milestone 1's final review added its test that every workspace package is wired into coverage, mypy, deptry and import-linter, it passes with the entries above (run it now: `uv run pytest -q --no-cov -k "package"` or the test's own name).

- [ ] **Step 2: Write the failing tests**

`services/identity/tests/test_domain.py`:

```python
import unicodedata
from datetime import UTC, datetime, timedelta
from typing import Final

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ledger_identity.domain.branches import Branch, EmissionPoint
from ledger_identity.domain.company import Company, CompanyStatus
from ledger_identity.domain.errors import (
    ActorNotAllowedError,
    BranchCodeTakenError,
    BranchNotFoundError,
    CompanyInactiveError,
    CompanyNotFoundError,
    EmissionPointCodeTakenError,
    EmissionPointNotFoundError,
    InvalidCodeError,
    InvalidNameError,
    LastOwnerError,
    MembershipNotFoundError,
    OrganizationNotFoundError,
    OwnersOnlyError,
    StaleVersionError,
    TaxIdTakenError,
)
from ledger_identity.domain.events import (
    CompanyCreated,
    CompanyDeactivated,
    CompanyUpdated,
    MembershipGranted,
    MembershipRevoked,
)
from ledger_identity.domain.membership import (
    Actor,
    MembershipStatus,
    Roster,
    found,
    grant,
    permissions_in,
    revoke,
)
from ledger_identity.domain.organization import Organization
from ledger_identity.domain.roles import PERMISSIONS, READ_ONLY, Role, permissions_of
from ledger_identity.domain.text import (
    MAX_CODE,
    MAX_LEGAL_NAME,
    MAX_NAME,
    LegalName,
    parse_code,
    parse_legal_name,
    parse_name,
)
from ledger_kernel.access import Permission, Principal, SubjectId
from ledger_kernel.countries.do import TaxId
from ledger_kernel.errors import ErrorKind
from ledger_kernel.ids import OrganizationId, Tenant, new_id
lazy from ledger_identity.domain.membership import Membership
lazy from ledger_kernel.errors import LedgerError

NOW: Final = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)
LATER: Final = NOW + timedelta(hours=1)
ORGANIZATION: Final = OrganizationId(new_id())
FOUNDER: Final = Principal(subject=SubjectId("2001"), organization=ORGANIZATION, provider_organization="1001")
RNC: Final = TaxId.parse("101850043")
OWNER: Final = Actor(SubjectId("2001"), permissions_of(Role.OWNER))
ADMIN: Final = Actor(SubjectId("2002"), permissions_of(Role.ADMIN))
VIEWER: Final = Actor(SubjectId("2003"), permissions_of(Role.VIEWER))


def registered() -> Company:
    company, _ = Company.register(
        organization=ORGANIZATION, tax_id=RNC, legal_name=parse_legal_name("Ferretería Duarte"), now=NOW
    )
    return company


def deactivated() -> Company:
    company, _ = registered().deactivate(now=LATER)
    return company


def member(company: Company, subject: str, role: Role) -> Membership:
    membership, _ = grant(
        Roster(company, None, owners=1), subject=SubjectId(subject), role=role, actor=OWNER, now=NOW
    )
    return membership


# ── Names and codes ─────────────────────────────────────────────────────────────


def test_a_name_is_trimmed_its_spaces_collapsed_and_composed() -> None:
    assert parse_legal_name("  Ferretería \t Duarte\n S.R.L. ") == "Ferretería Duarte S.R.L."
    assert parse_legal_name("Jos" + "e\u0301") == "José"  # decomposed accents are composed (NFC)


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("", "is empty"),
        (" \t\n ", "is empty"),
        ("x" * (MAX_LEGAL_NAME + 1), f"is longer than {MAX_LEGAL_NAME} characters"),
        ("Duarte\x00", "holds a control or format character"),
        ("Duar\u200dte", "holds a control or format character"),
    ],
    ids=["empty", "blank", "too-long", "nul", "zero-width-joiner"],
)
def test_a_name_that_cannot_be_shown_is_refused_without_being_echoed(text: str, problem: str) -> None:
    with pytest.raises(InvalidNameError) as caught:
        parse_legal_name(text)
    assert str(caught.value) == f"legal_name {problem}"
    assert (caught.value.kind, caught.value.code) == (ErrorKind.INVALID, "invalid_name")


def test_other_names_say_which_field_and_have_their_own_limit() -> None:
    assert parse_name("Sucursal Santiago") == "Sucursal Santiago"
    with pytest.raises(InvalidNameError, match=r"^branch_name is longer than 100 characters$"):
        parse_name("x" * (MAX_NAME + 1), field="branch_name")


@given(st.text(max_size=200))
def test_a_name_is_either_refused_or_read_the_same_way_twice(text: str) -> None:
    try:
        name = parse_legal_name(text)
    except InvalidNameError:
        return
    assert parse_legal_name(name) == name
    assert name == unicodedata.normalize("NFC", name)
    assert 0 < len(name) <= MAX_LEGAL_NAME


@pytest.mark.parametrize("text", ["001", "SUC1", "A", "9" * MAX_CODE])
def test_a_code_is_upper_case_letters_and_digits(text: str) -> None:
    assert parse_code(text) == text


@pytest.mark.parametrize("text", ["", "suc1", "SUC-1", "SUC 1", "1" * (MAX_CODE + 1), "Ñ"])
def test_anything_else_is_not_a_code(text: str) -> None:
    with pytest.raises(InvalidCodeError, match=f"a code is 1 to {MAX_CODE} upper-case letters and digits"):
        parse_code(text)


# ── Roles ───────────────────────────────────────────────────────────────────────


def test_what_each_role_may_do() -> None:
    everything = frozenset(Permission)
    expected = {
        Role.OWNER: everything,
        Role.ADMIN: everything - {Permission.OWNERSHIP_MANAGE},
        Role.ACCOUNTANT: frozenset(
            {
                Permission.COMPANY_READ,
                Permission.MEMBERSHIP_READ,
                Permission.INVOICE_READ,
                Permission.INVOICE_ISSUE,
            }
        ),
        Role.ISSUER: frozenset({Permission.COMPANY_READ, Permission.INVOICE_READ, Permission.INVOICE_ISSUE}),
        Role.VIEWER: frozenset({Permission.COMPANY_READ, Permission.INVOICE_READ}),
    }
    assert expected == PERMISSIONS


def test_every_role_reads_its_company_and_only_owners_manage_ownership() -> None:
    assert all(Permission.COMPANY_READ in permissions_of(role) for role in Role)
    assert [role for role in Role if Permission.OWNERSHIP_MANAGE in permissions_of(role)] == [Role.OWNER]


# ── Organizations and companies ─────────────────────────────────────────────────


def test_an_organization_is_the_one_its_principal_acts_in() -> None:
    assert Organization.of(FOUNDER, now=NOW) == Organization(
        id=ORGANIZATION, provider_id="1001", created_at=NOW
    )


def test_a_registered_company_is_active_at_its_first_version_and_says_so() -> None:
    company, created = Company.register(
        organization=ORGANIZATION, tax_id=RNC, legal_name=parse_legal_name("Duarte"), now=NOW
    )
    assert (company.status, company.version, company.created_at, company.updated_at) == (
        CompanyStatus.ACTIVE,
        1,
        NOW,
        NOW,
    )
    assert company.tenant == Tenant(ORGANIZATION, company.id)
    assert company.id.version == 7
    assert created == CompanyCreated(
        tenant=company.tenant, tax_id=RNC, legal_name=LegalName("Duarte"), version=1
    )


def test_renaming_a_company_makes_a_new_version() -> None:
    company = registered()
    renamed, updated = company.rename(parse_legal_name("Duarte Hermanos"), now=LATER)
    assert (renamed.legal_name, renamed.version, renamed.updated_at, renamed.created_at) == (
        "Duarte Hermanos",
        2,
        LATER,
        NOW,
    )
    assert updated == CompanyUpdated(
        tenant=company.tenant, tax_id=RNC, legal_name=LegalName("Duarte Hermanos"), version=2
    )


def test_renaming_a_company_to_its_own_name_changes_nothing() -> None:
    company = registered()
    assert company.rename(company.legal_name, now=LATER) == (company, None)


def test_a_deactivated_company_says_so_and_changes_no_more() -> None:
    company = registered()
    inactive, event = company.deactivate(now=LATER)
    assert (inactive.status, inactive.version) == (CompanyStatus.INACTIVE, 2)
    assert event == CompanyDeactivated(tenant=company.tenant, version=2)
    with pytest.raises(CompanyInactiveError) as caught:
        inactive.rename(parse_legal_name("Other"), now=LATER)
    assert (caught.value.kind, caught.value.code) == (ErrorKind.CONFLICT, "company_inactive")
    with pytest.raises(CompanyInactiveError):
        inactive.deactivate(now=LATER)


# ── Branches and emission points ───────────────────────────────────────────────


def test_a_branch_and_its_emission_points_belong_to_the_company() -> None:
    company = registered()
    branch = Branch.open(company, code=parse_code("SUC1"), name=parse_name("Santiago"), now=NOW)
    point = EmissionPoint.open(company, branch, code=parse_code("001"), name=parse_name("Caja 1"), now=NOW)
    assert (branch.tenant, branch.code, branch.name, branch.created_at) == (
        company.tenant,
        "SUC1",
        "Santiago",
        NOW,
    )
    assert (point.tenant, point.branch, point.code) == (company.tenant, branch.id, "001")


def test_nothing_opens_in_a_deactivated_company() -> None:
    company = registered()
    branch = Branch.open(company, code=parse_code("SUC1"), name=parse_name("Santiago"), now=NOW)
    inactive = company.deactivate(now=LATER)[0]
    with pytest.raises(CompanyInactiveError):
        Branch.open(inactive, code=parse_code("SUC2"), name=parse_name("Bonao"), now=LATER)
    with pytest.raises(CompanyInactiveError):
        EmissionPoint.open(inactive, branch, code=parse_code("002"), name=parse_name("Caja 2"), now=LATER)


def test_an_emission_point_cannot_open_in_a_branch_of_another_company() -> None:
    company, other = registered(), registered()
    branch = Branch.open(other, code=parse_code("SUC1"), name=parse_name("Santiago"), now=NOW)
    with pytest.raises(BranchNotFoundError):
        EmissionPoint.open(company, branch, code=parse_code("001"), name=parse_name("Caja 1"), now=NOW)


# ── Memberships ─────────────────────────────────────────────────────────────────


def test_the_founder_of_a_company_is_its_first_owner() -> None:
    company = registered()
    owner, granted = found(company, FOUNDER, now=NOW)
    assert (owner.subject, owner.role, owner.status, owner.version, owner.changed_by, owner.tenant) == (
        "2001",
        Role.OWNER,
        MembershipStatus.ACTIVE,
        1,
        "2001",
        company.tenant,
    )
    assert owner.active
    assert owner.permissions == frozenset(Permission)
    assert granted == MembershipGranted(
        tenant=company.tenant, subject=SubjectId("2001"), role=Role.OWNER, previous=None, version=1
    )


def test_an_admin_grants_and_changes_roles_below_owner() -> None:
    company = registered()
    viewer, granted = grant(
        Roster(company, None, owners=1), subject=SubjectId("3001"), role=Role.VIEWER, actor=ADMIN, now=NOW
    )
    assert (viewer.role, viewer.version, viewer.changed_by) == (Role.VIEWER, 1, "2002")
    assert granted is not None
    assert granted.previous is None
    issuer, changed = grant(
        Roster(company, viewer, owners=1), subject=viewer.subject, role=Role.ISSUER, actor=ADMIN, now=LATER
    )
    assert (issuer.id, issuer.role, issuer.version, issuer.changed_at) == (viewer.id, Role.ISSUER, 2, LATER)
    assert changed == MembershipGranted(
        tenant=company.tenant, subject=SubjectId("3001"), role=Role.ISSUER, previous=Role.VIEWER, version=2
    )


@pytest.mark.parametrize("role", [Role.VIEWER, Role.OWNER], ids=["viewer", "owner"])
def test_whoever_may_not_manage_memberships_changes_none(role: Role) -> None:
    company = registered()
    with pytest.raises(ActorNotAllowedError) as caught:
        grant(Roster(company, None, owners=1), subject=SubjectId("3001"), role=role, actor=VIEWER, now=NOW)
    assert (caught.value.kind, caught.value.code) == (ErrorKind.FORBIDDEN, "permission_denied")
    viewer = member(company, "3001", Role.VIEWER)
    with pytest.raises(ActorNotAllowedError):
        revoke(Roster(company, viewer, owners=1), subject=viewer.subject, actor=VIEWER, now=NOW)


def test_granting_the_role_a_principal_holds_changes_nothing() -> None:
    company = registered()
    viewer = member(company, "3001", Role.VIEWER)
    assert grant(
        Roster(company, viewer, owners=1), subject=viewer.subject, role=Role.VIEWER, actor=ADMIN, now=LATER
    ) == (
        viewer,
        None,
    )


@pytest.mark.parametrize(
    ("current", "role"),
    [(None, Role.OWNER), (Role.VIEWER, Role.OWNER), (Role.OWNER, Role.ADMIN)],
    ids=["new-owner", "promote-to-owner", "demote-an-owner"],
)
def test_only_an_owner_grants_or_takes_the_owner_role(current: Role | None, role: Role) -> None:
    company = registered()
    held = None if current is None else member(company, "3001", current)
    with pytest.raises(OwnersOnlyError) as caught:
        grant(Roster(company, held, owners=2), subject=SubjectId("3001"), role=role, actor=ADMIN, now=LATER)
    assert (caught.value.kind, caught.value.code) == (ErrorKind.FORBIDDEN, "owners_only")


def test_the_last_owner_cannot_be_demoted() -> None:
    company = registered()
    owner, _ = found(company, FOUNDER, now=NOW)
    with pytest.raises(LastOwnerError) as caught:
        grant(
            Roster(company, owner, owners=1), subject=owner.subject, role=Role.ADMIN, actor=OWNER, now=LATER
        )
    assert (caught.value.kind, caught.value.code) == (ErrorKind.CONFLICT, "last_owner")


def test_one_of_two_owners_can_be_demoted_by_an_owner() -> None:
    company = registered()
    second = member(company, "3001", Role.OWNER)
    demoted, _ = grant(
        Roster(company, second, owners=2), subject=second.subject, role=Role.ADMIN, actor=OWNER, now=LATER
    )
    assert demoted.role is Role.ADMIN


def test_no_role_is_granted_in_a_deactivated_company() -> None:
    with pytest.raises(CompanyInactiveError):
        grant(
            Roster(deactivated(), None, owners=1),
            subject=SubjectId("3001"),
            role=Role.VIEWER,
            actor=OWNER,
            now=LATER,
        )


def test_a_revoked_membership_is_kept_at_a_new_version_and_says_which_role_it_held() -> None:
    company = registered()
    viewer = member(company, "3001", Role.VIEWER)
    revoked, event = revoke(Roster(company, viewer, owners=1), subject=viewer.subject, actor=ADMIN, now=LATER)
    assert event == MembershipRevoked(
        tenant=company.tenant, subject=SubjectId("3001"), role=Role.VIEWER, version=2
    )
    assert (revoked.id, revoked.status, revoked.version, revoked.changed_at, revoked.changed_by) == (
        viewer.id,
        MembershipStatus.REVOKED,
        2,
        LATER,
        "2002",
    )
    assert not revoked.active
    assert revoked.permissions == frozenset()


def test_a_role_granted_again_continues_the_versions_of_the_revoked_membership() -> None:
    company = registered()
    viewer = member(company, "3001", Role.VIEWER)
    revoked, _ = revoke(Roster(company, viewer, owners=1), subject=viewer.subject, actor=ADMIN, now=NOW)
    again, granted = grant(
        Roster(company, revoked, owners=1), subject=viewer.subject, role=Role.VIEWER, actor=ADMIN, now=LATER
    )
    assert (again.id, again.status, again.version) == (viewer.id, MembershipStatus.ACTIVE, 3)
    assert granted == MembershipGranted(
        tenant=company.tenant, subject=SubjectId("3001"), role=Role.VIEWER, previous=None, version=3
    )


def test_a_role_can_be_revoked_even_in_a_deactivated_company() -> None:
    company = registered()
    viewer = member(company, "3001", Role.VIEWER)
    inactive = company.deactivate(now=LATER)[0]
    _, revoked = revoke(Roster(inactive, viewer, owners=1), subject=viewer.subject, actor=ADMIN, now=LATER)
    assert revoked.role is Role.VIEWER


def test_only_an_owner_revokes_an_owner_and_never_the_last() -> None:
    company = registered()
    owner, _ = found(company, FOUNDER, now=NOW)
    with pytest.raises(OwnersOnlyError):
        revoke(Roster(company, owner, owners=2), subject=owner.subject, actor=ADMIN, now=LATER)
    with pytest.raises(LastOwnerError):
        revoke(Roster(company, owner, owners=1), subject=owner.subject, actor=OWNER, now=LATER)
    _, revoked = revoke(Roster(company, owner, owners=2), subject=owner.subject, actor=OWNER, now=LATER)
    assert revoked.role is Role.OWNER


def test_revoking_a_role_nobody_holds_is_not_found() -> None:
    with pytest.raises(MembershipNotFoundError, match="'3001' holds no role in the company") as caught:
        revoke(Roster(registered(), None, owners=1), subject=SubjectId("3001"), actor=OWNER, now=NOW)
    assert caught.value.code == "membership_not_found"
    company = registered()
    viewer = member(company, "3001", Role.VIEWER)
    revoked, _ = revoke(Roster(company, viewer, owners=1), subject=viewer.subject, actor=OWNER, now=NOW)
    with pytest.raises(MembershipNotFoundError):
        revoke(Roster(company, revoked, owners=1), subject=viewer.subject, actor=OWNER, now=LATER)


def test_other_services_may_let_a_member_of_a_deactivated_company_read_only() -> None:
    company = registered()
    issuer = member(company, "3001", Role.ISSUER)
    assert permissions_in(company, issuer) == permissions_of(Role.ISSUER)
    assert permissions_in(company.deactivate(now=LATER)[0], issuer) == frozenset(
        {Permission.COMPANY_READ, Permission.INVOICE_READ}
    )
    owner, _ = found(company, FOUNDER, now=NOW)
    assert permissions_in(company.deactivate(now=LATER)[0], owner) == READ_ONLY
    revoked, _ = revoke(Roster(company, issuer, owners=1), subject=issuer.subject, actor=OWNER, now=NOW)
    assert permissions_in(company, revoked) == frozenset()


# ── Errors ──────────────────────────────────────────────────────────────────────

SOME_ID: Final = new_id()


@pytest.mark.parametrize(
    ("error", "kind", "code", "message"),
    [
        (
            OrganizationNotFoundError(SOME_ID),
            ErrorKind.NOT_FOUND,
            "organization_not_found",
            f"organization {SOME_ID} has no company yet",
        ),
        (
            CompanyNotFoundError(SOME_ID),
            ErrorKind.NOT_FOUND,
            "company_not_found",
            f"company {SOME_ID} was not found",
        ),
        (
            BranchNotFoundError(SOME_ID),
            ErrorKind.NOT_FOUND,
            "branch_not_found",
            f"branch {SOME_ID} was not found",
        ),
        (
            EmissionPointNotFoundError(SOME_ID),
            ErrorKind.NOT_FOUND,
            "emission_point_not_found",
            f"emission point {SOME_ID} was not found",
        ),
        (
            TaxIdTakenError(),
            ErrorKind.CONFLICT,
            "tax_id_taken",
            "the organization already has a company with this tax id",
        ),
        (
            BranchCodeTakenError(),
            ErrorKind.CONFLICT,
            "branch_code_taken",
            "the company already has a branch with this code",
        ),
        (
            EmissionPointCodeTakenError(),
            ErrorKind.CONFLICT,
            "emission_point_code_taken",
            "the branch already has an emission point with this code",
        ),
        (
            StaleVersionError(SOME_ID),
            ErrorKind.CONFLICT,
            "stale_version",
            f"company {SOME_ID} is not at the version the request names",
        ),
        (
            ActorNotAllowedError(Permission.MEMBERSHIP_MANAGE),
            ErrorKind.FORBIDDEN,
            "permission_denied",
            "this needs the permission membership.manage",
        ),
    ],
)
def test_every_error_has_its_kind_its_code_and_a_message_without_client_text(
    error: LedgerError, kind: ErrorKind, code: str, message: str
) -> None:
    assert (error.kind, error.code, str(error)) == (kind, code, message)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest services/identity -q --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'ledger_identity.domain.branches'`.

- [ ] **Step 4: Implement**

`services/identity/src/ledger_identity/domain/ids.py`:

```python
"""Identifiers of the identity service's own entities; organizations and companies use the kernel's."""

from typing import NewType
from uuid import UUID

BranchId = NewType("BranchId", UUID)
EmissionPointId = NewType("EmissionPointId", UUID)
MembershipId = NewType("MembershipId", UUID)
```

`services/identity/src/ledger_identity/domain/errors.py`:

```python
"""The identity service's errors: each with its kind and a stable code clients match on (§4.2, §10).

Messages name ids, fields and limits, never text a client typed: a legal name or tax id is the client's data.
"""

from typing import ClassVar
lazy from uuid import UUID

from ledger_kernel.errors import ConflictError, ForbiddenError, InvalidInputError, NotFoundError, excerpt
lazy from ledger_kernel.access import Permission, SubjectId


class InvalidNameError(InvalidInputError):
    """A name that cannot be shown as it is: empty, too long, or with an invisible character."""

    code: ClassVar[str] = "invalid_name"

    def __init__(self, field: str, problem: str) -> None:
        super().__init__(f"{field} {problem}")


class InvalidCodeError(InvalidInputError):
    """A code that is not 1 to 10 upper-case ASCII letters and digits."""

    code: ClassVar[str] = "invalid_code"

    def __init__(self) -> None:
        super().__init__("a code is 1 to 10 upper-case letters and digits")


class OrganizationNotFoundError(NotFoundError):
    """The organization has no company yet, so identity holds nothing of it."""

    code: ClassVar[str] = "organization_not_found"

    def __init__(self, organization: UUID) -> None:
        super().__init__(f"organization {organization} has no company yet")


class CompanyNotFoundError(NotFoundError):
    """No company by this id in the organization."""

    code: ClassVar[str] = "company_not_found"

    def __init__(self, company: UUID) -> None:
        super().__init__(f"company {company} was not found")


class BranchNotFoundError(NotFoundError):
    """No branch by this id in the company."""

    code: ClassVar[str] = "branch_not_found"

    def __init__(self, branch: UUID) -> None:
        super().__init__(f"branch {branch} was not found")


class EmissionPointNotFoundError(NotFoundError):
    """No emission point by this id in the branch."""

    code: ClassVar[str] = "emission_point_not_found"

    def __init__(self, point: UUID) -> None:
        super().__init__(f"emission point {point} was not found")


class MembershipNotFoundError(NotFoundError):
    """The subject holds no role in the company."""

    code: ClassVar[str] = "membership_not_found"

    def __init__(self, subject: SubjectId) -> None:
        super().__init__(f"{excerpt(subject)} holds no role in the company")


class TaxIdTakenError(ConflictError):
    """The organization already has a company with this tax id."""

    code: ClassVar[str] = "tax_id_taken"

    def __init__(self) -> None:
        super().__init__("the organization already has a company with this tax id")


class BranchCodeTakenError(ConflictError):
    """The company already has a branch with this code."""

    code: ClassVar[str] = "branch_code_taken"

    def __init__(self) -> None:
        super().__init__("the company already has a branch with this code")


class EmissionPointCodeTakenError(ConflictError):
    """The branch already has an emission point with this code."""

    code: ClassVar[str] = "emission_point_code_taken"

    def __init__(self) -> None:
        super().__init__("the branch already has an emission point with this code")


class CompanyInactiveError(ConflictError):
    """The company was deactivated: it changes no more."""

    code: ClassVar[str] = "company_inactive"

    def __init__(self, company: UUID) -> None:
        super().__init__(f"company {company} is inactive")


class StaleVersionError(ConflictError):
    """The company is not at a version ``If-Match`` names, or it changed meanwhile."""

    code: ClassVar[str] = "stale_version"

    def __init__(self, company: UUID) -> None:
        super().__init__(f"company {company} is not at the version the request names")


class LastOwnerError(ConflictError):
    """The change would leave the company without an owner."""

    code: ClassVar[str] = "last_owner"

    def __init__(self) -> None:
        super().__init__("a company keeps at least one owner")


class OwnersOnlyError(ForbiddenError):
    """Only an owner grants the owner role, or takes it away."""

    code: ClassVar[str] = "owners_only"

    def __init__(self) -> None:
        super().__init__("only an owner grants or takes the owner role")


class ActorNotAllowedError(ForbiddenError):
    """The actor's membership, read as it is now, lacks the permission the change needs.

    The same code as the platform's ``PermissionDeniedError``: for a client both mean "your role does not
    allow this"; this one is raised under the company's lock, when the role changed after the request was
    checked.
    """

    code: ClassVar[str] = "permission_denied"

    def __init__(self, permission: Permission) -> None:
        super().__init__(f"this needs the permission {permission.value}")
```

`services/identity/src/ledger_identity/domain/text.py`:

```python
"""Names and codes as people type them, checked once so that the rest of the domain can trust them.

A name is trimmed, its runs of white space are one space, it is in Unicode NFC, it holds no control or format
character, and it is neither empty nor longer than its limit. A code is 1 to ``MAX_CODE`` upper-case ASCII
letters and digits (``001``, ``SUC1``). A refusal says which field and what is wrong, never the text.
"""

import re
import unicodedata
from typing import Final, NewType

from ledger_identity.domain.errors import InvalidCodeError, InvalidNameError

LegalName = NewType("LegalName", str)
"""A company's registered name, as DGII knows it."""
Name = NewType("Name", str)
"""The name of a branch or an emission point."""
Code = NewType("Code", str)
"""A short code of a branch or an emission point, unique where it lives."""

MAX_LEGAL_NAME: Final = 150
"""As long as an e-CF's ``RazonSocialEmisor`` may be."""
MAX_NAME: Final = 100
MAX_CODE: Final = 10
_CODE: Final = re.compile(rf"[A-Z0-9]{{1,{MAX_CODE}}}", re.ASCII)
_INVISIBLE: Final = "C"
"""Unicode's "other" categories: controls, formats (zero-width joiners), surrogates, unassigned."""


def _cleaned(field: str, text: str, limit: int) -> str:
    cleaned = unicodedata.normalize("NFC", " ".join(text.split()))
    if not cleaned:
        raise InvalidNameError(field, "is empty")
    if len(cleaned) > limit:
        raise InvalidNameError(field, f"is longer than {limit} characters")
    if any(unicodedata.category(character).startswith(_INVISIBLE) for character in cleaned):
        raise InvalidNameError(field, "holds a control or format character")
    return cleaned


def parse_legal_name(text: str) -> LegalName:
    """A company's legal name, cleaned as the module says."""
    return LegalName(_cleaned("legal_name", text, MAX_LEGAL_NAME))


def parse_name(text: str, *, field: str = "name") -> Name:
    """The name of a branch or an emission point; ``field`` is how a refusal names it."""
    return Name(_cleaned(field, text, MAX_NAME))


def parse_code(text: str) -> Code:
    """A code of upper-case ASCII letters and digits."""
    if _CODE.fullmatch(text) is None:
        raise InvalidCodeError
    return Code(text)
```

`services/identity/src/ledger_identity/domain/roles.py`:

```python
"""Roles granted per company, and what each allows (§8)."""

from enum import StrEnum
from types import MappingProxyType
from typing import Final
lazy from collections.abc import Mapping

from ledger_kernel.access import Permission


class Role(StrEnum):
    """A principal's role in one company."""

    OWNER = "owner"
    """Everything, including who else owns the company."""
    ADMIN = "admin"
    """Everything but the owner role."""
    ACCOUNTANT = "accountant"
    """Reads everything, issues documents."""
    ISSUER = "issuer"
    """Issues documents."""
    VIEWER = "viewer"
    """Reads."""


_EVERYTHING: Final = frozenset(Permission)
PERMISSIONS: Final[Mapping[Role, frozenset[Permission]]] = MappingProxyType(
    {
        Role.OWNER: _EVERYTHING,
        Role.ADMIN: _EVERYTHING - {Permission.OWNERSHIP_MANAGE},
        Role.ACCOUNTANT: frozenset(
            {
                Permission.COMPANY_READ,
                Permission.MEMBERSHIP_READ,
                Permission.INVOICE_READ,
                Permission.INVOICE_ISSUE,
            }
        ),
        Role.ISSUER: frozenset({Permission.COMPANY_READ, Permission.INVOICE_READ, Permission.INVOICE_ISSUE}),
        Role.VIEWER: frozenset({Permission.COMPANY_READ, Permission.INVOICE_READ}),
    }
)


READ_ONLY: Final = frozenset({Permission.COMPANY_READ, Permission.MEMBERSHIP_READ, Permission.INVOICE_READ})
"""What a role still allows other services to do for a deactivated company: read."""


def permissions_of(role: Role) -> frozenset[Permission]:
    """What ``role`` allows in its company."""
    return PERMISSIONS[role]
```

`services/identity/src/ledger_identity/domain/events.py`:

```python
"""What identity announces (§4.1): the facts other services build on, as the domain states them."""

from dataclasses import dataclass
from typing import final

lazy from ledger_identity.domain.roles import Role
lazy from ledger_identity.domain.text import LegalName
lazy from ledger_kernel.access import SubjectId
lazy from ledger_kernel.countries.do import TaxId
lazy from ledger_kernel.ids import Tenant


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class CompanyCreated:
    """A company was registered."""

    tenant: Tenant
    tax_id: TaxId
    legal_name: LegalName
    version: int


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class CompanyUpdated:
    """A company's details changed."""

    tenant: Tenant
    tax_id: TaxId
    legal_name: LegalName
    version: int


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class CompanyDeactivated:
    """A company was deactivated."""

    tenant: Tenant
    version: int


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class MembershipGranted:
    """A principal was given a role in a company, or its role changed (``previous`` is the role it held)."""

    tenant: Tenant
    subject: SubjectId
    role: Role
    previous: Role | None
    version: int


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class MembershipRevoked:
    """A principal's role in a company was taken away."""

    tenant: Tenant
    subject: SubjectId
    role: Role
    version: int


type IdentityEvent = (
    CompanyCreated | CompanyUpdated | CompanyDeactivated | MembershipGranted | MembershipRevoked
)
```

`services/identity/src/ledger_identity/domain/organization.py`:

```python
"""An organization: one Zitadel organization, the customer every company belongs to."""

from dataclasses import dataclass
from typing import final
lazy from datetime import datetime

lazy from ledger_kernel.access import Principal
lazy from ledger_kernel.ids import OrganizationId


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Organization:
    """What identity keeps of an organization: its id here and at the identity provider, and since when."""

    id: OrganizationId
    provider_id: str
    created_at: datetime

    @classmethod
    def of(cls, principal: Principal, *, now: datetime) -> Organization:
        """The organization ``principal`` acts in, as first seen ``now``."""
        return cls(id=principal.organization, provider_id=principal.provider_organization, created_at=now)
```

`services/identity/src/ledger_identity/domain/company.py`:

```python
"""A company: one taxpayer of an organization, and the unit of tenancy (§7).

Its tax id never changes (another tax id is another company). Every change makes a new version, which is the
company's ``ETag``. A deactivated company changes no more; its data stays readable.
"""

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import final
lazy from datetime import datetime

from ledger_identity.domain.errors import CompanyInactiveError
from ledger_identity.domain.events import CompanyCreated, CompanyDeactivated, CompanyUpdated
from ledger_kernel.ids import CompanyId, Tenant, new_id
lazy from ledger_identity.domain.text import LegalName
lazy from ledger_kernel.countries.do import TaxId
lazy from ledger_kernel.ids import OrganizationId


class CompanyStatus(StrEnum):
    """Whether a company can still change."""

    ACTIVE = "active"
    INACTIVE = "inactive"


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Company:
    """A company and its version: each method returns the company changed and the event that says so."""

    id: CompanyId
    organization: OrganizationId
    tax_id: TaxId
    legal_name: LegalName
    status: CompanyStatus
    version: int
    created_at: datetime
    updated_at: datetime

    @property
    def tenant(self) -> Tenant:
        """The company with its organization: what its rows and its events are scoped to."""
        return Tenant(self.organization, self.id)

    @classmethod
    def register(
        cls, *, organization: OrganizationId, tax_id: TaxId, legal_name: LegalName, now: datetime
    ) -> tuple[Company, CompanyCreated]:
        """A new company of ``organization``, active, at version 1."""
        company = cls(
            id=CompanyId(new_id()),
            organization=organization,
            tax_id=tax_id,
            legal_name=legal_name,
            status=CompanyStatus.ACTIVE,
            version=1,
            created_at=now,
            updated_at=now,
        )
        created = CompanyCreated(tenant=company.tenant, tax_id=tax_id, legal_name=legal_name, version=1)
        return company, created

    def require_active(self) -> None:
        """Refuse any change of a deactivated company."""
        if self.status is CompanyStatus.INACTIVE:
            raise CompanyInactiveError(self.id)

    def rename(self, legal_name: LegalName, *, now: datetime) -> tuple[Company, CompanyUpdated | None]:
        """The company under ``legal_name``; unchanged, and no event, when that is already its name."""
        self.require_active()
        if legal_name == self.legal_name:
            return self, None
        renamed = replace(self, legal_name=legal_name, version=self.version + 1, updated_at=now)
        updated = CompanyUpdated(
            tenant=self.tenant, tax_id=self.tax_id, legal_name=legal_name, version=renamed.version
        )
        return renamed, updated

    def deactivate(self, *, now: datetime) -> tuple[Company, CompanyDeactivated]:
        """The company deactivated: it changes no more."""
        self.require_active()
        inactive = replace(self, status=CompanyStatus.INACTIVE, version=self.version + 1, updated_at=now)
        return inactive, CompanyDeactivated(tenant=self.tenant, version=inactive.version)
```

`services/identity/src/ledger_identity/domain/branches.py`:

```python
"""Where a company issues documents: its branches, and the emission points of each."""

from dataclasses import dataclass
from typing import final
lazy from datetime import datetime

from ledger_identity.domain.errors import BranchNotFoundError
from ledger_identity.domain.ids import BranchId, EmissionPointId
from ledger_kernel.ids import new_id
lazy from ledger_identity.domain.company import Company
lazy from ledger_identity.domain.text import Code, Name
lazy from ledger_kernel.ids import Tenant


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Branch:
    """A branch of a company, known by a code unique in the company."""

    id: BranchId
    tenant: Tenant
    code: Code
    name: Name
    created_at: datetime

    @classmethod
    def open(cls, company: Company, *, code: Code, name: Name, now: datetime) -> Branch:
        """A new branch of ``company``, which must be active."""
        company.require_active()
        return cls(id=BranchId(new_id()), tenant=company.tenant, code=code, name=name, created_at=now)


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class EmissionPoint:
    """A point of a branch that issues documents, known by a code unique in the branch."""

    id: EmissionPointId
    tenant: Tenant
    branch: BranchId
    code: Code
    name: Name
    created_at: datetime

    @classmethod
    def open(
        cls, company: Company, branch: Branch, *, code: Code, name: Name, now: datetime
    ) -> EmissionPoint:
        """A new emission point of ``branch``, which must be one of ``company``'s, and that must be active."""
        company.require_active()
        if branch.tenant != company.tenant:
            raise BranchNotFoundError(branch.id)
        return cls(
            id=EmissionPointId(new_id()),
            tenant=company.tenant,
            branch=branch.id,
            code=code,
            name=name,
            created_at=now,
        )
```

`services/identity/src/ledger_identity/domain/membership.py`:

```python
"""Memberships: who holds which role in a company, and the rules for changing that (§8).

One role per principal and company. The founder of a company is its first owner. Roles are granted and
revoked by whoever may manage memberships (``MEMBERSHIP_MANAGE``), the owner role by owners only
(``OWNERSHIP_MANAGE``); the actor is judged on its membership as it is when the change is made, under the
company's lock. A company always keeps an owner: the last can be neither demoted nor revoked. Roles are
granted in active companies only, but can always be revoked: taking access away must never be blocked.

A revoked membership is kept, with its last role and status ``revoked``: its version only grows, so a role
granted again continues from it, and the version every event carries never goes back (§4.1).
"""

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import final
lazy from datetime import datetime

from ledger_identity.domain.company import CompanyStatus
from ledger_identity.domain.errors import (
    ActorNotAllowedError,
    LastOwnerError,
    MembershipNotFoundError,
    OwnersOnlyError,
)
from ledger_identity.domain.events import MembershipGranted, MembershipRevoked
from ledger_identity.domain.ids import MembershipId
from ledger_identity.domain.roles import READ_ONLY, Role, permissions_of
from ledger_kernel.access import Permission
from ledger_kernel.ids import new_id
lazy from ledger_identity.domain.company import Company
lazy from ledger_kernel.access import Principal, SubjectId
lazy from ledger_kernel.ids import Tenant


class MembershipStatus(StrEnum):
    """Whether a membership still grants its role."""

    ACTIVE = "active"
    REVOKED = "revoked"


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Membership:
    """A principal's role in a company (or the one it held until revoked), its version and its last change."""

    id: MembershipId
    tenant: Tenant
    subject: SubjectId
    role: Role
    status: MembershipStatus
    version: int
    changed_at: datetime
    """When the membership last changed: the grant of its role, or its revocation."""
    changed_by: SubjectId
    """Who made that change."""

    @property
    def active(self) -> bool:
        """Whether the membership still grants its role."""
        return self.status is MembershipStatus.ACTIVE

    @property
    def permissions(self) -> frozenset[Permission]:
        """What the role allows in the company; nothing once the membership is revoked."""
        return permissions_of(self.role) if self.active else frozenset()


@final
@dataclass(frozen=True, slots=True)
class Actor:
    """Who changes a membership, and what its own membership allows it in the company now."""

    subject: SubjectId
    permissions: frozenset[Permission]

    @property
    def manages_memberships(self) -> bool:
        """Whether it may grant and revoke roles."""
        return Permission.MEMBERSHIP_MANAGE in self.permissions

    @property
    def manages_ownership(self) -> bool:
        """Whether it may grant and take the owner role."""
        return Permission.OWNERSHIP_MANAGE in self.permissions


@final
@dataclass(frozen=True, slots=True)
class Roster:
    """What a change of a principal's role is decided on: the company, its membership, its owners."""

    company: Company
    current: Membership | None
    """The principal's membership, active or revoked; ``None`` if it never held a role there."""
    owners: int
    """How many owners the company has now."""


def found(company: Company, founder: Principal, *, now: datetime) -> tuple[Membership, MembershipGranted]:
    """The membership of a company's founder: its first owner."""
    owner = Membership(
        id=MembershipId(new_id()),
        tenant=company.tenant,
        subject=founder.subject,
        role=Role.OWNER,
        status=MembershipStatus.ACTIVE,
        version=1,
        changed_at=now,
        changed_by=founder.subject,
    )
    granted = MembershipGranted(
        tenant=company.tenant, subject=founder.subject, role=Role.OWNER, previous=None, version=1
    )
    return owner, granted


def _require_managing(actor: Actor) -> None:
    if not actor.manages_memberships:
        raise ActorNotAllowedError(Permission.MEMBERSHIP_MANAGE)


def _require_ownership(actor: Actor) -> None:
    if not actor.manages_ownership:
        raise OwnersOnlyError


def grant(
    roster: Roster, *, subject: SubjectId, role: Role, actor: Actor, now: datetime
) -> tuple[Membership, MembershipGranted | None]:
    """``subject``'s membership with ``role``; unchanged, and no event, when it holds that role already."""
    roster.company.require_active()
    _require_managing(actor)
    held = roster.current
    current = held if held is not None and held.active else None
    if role is Role.OWNER or (current is not None and current.role is Role.OWNER):
        _require_ownership(actor)
    if current is not None and current.role is role:
        return current, None
    if current is not None and current.role is Role.OWNER and roster.owners <= 1:
        raise LastOwnerError
    membership = Membership(
        id=MembershipId(new_id()) if held is None else held.id,
        tenant=roster.company.tenant,
        subject=subject,
        role=role,
        status=MembershipStatus.ACTIVE,
        version=1 if held is None else held.version + 1,
        changed_at=now,
        changed_by=actor.subject,
    )
    granted = MembershipGranted(
        tenant=membership.tenant,
        subject=subject,
        role=role,
        previous=None if current is None else current.role,
        version=membership.version,
    )
    return membership, granted


def revoke(
    roster: Roster, *, subject: SubjectId, actor: Actor, now: datetime
) -> tuple[Membership, MembershipRevoked]:
    """Take ``subject``'s role in the company away: its membership, revoked, and the event that says so."""
    _require_managing(actor)
    current = roster.current
    if current is None or not current.active:
        raise MembershipNotFoundError(subject)
    if current.role is Role.OWNER:
        _require_ownership(actor)
        if roster.owners <= 1:
            raise LastOwnerError
    revoked = replace(
        current,
        status=MembershipStatus.REVOKED,
        version=current.version + 1,
        changed_at=now,
        changed_by=actor.subject,
    )
    event = MembershipRevoked(
        tenant=current.tenant, subject=current.subject, role=current.role, version=revoked.version
    )
    return revoked, event


def permissions_in(company: Company, membership: Membership) -> frozenset[Permission]:
    """What other services may let ``membership``'s holder do for ``company``.

    Its role's permissions while it is active; of those, only the ones that read once the company is
    deactivated (other services cannot tell, and must not issue for it); nothing once it is revoked.
    """
    if company.status is CompanyStatus.INACTIVE:
        return membership.permissions & READ_ONLY
    return membership.permissions
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest services/identity -q --no-cov`
Expected: PASS.

- [ ] **Step 6: Gate**

Run: `uv run poe fmt && uv run poe check`
Expected: every step passes, coverage 100 % (`ledger_identity` included).

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock services/identity
git commit -m "feat(identity): the service's package and its domain

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Identity use cases and their ports

**Files:**
- Create: `services/identity/src/ledger_identity/application/__init__.py`, `ports.py`, `commands.py`, `queries.py`
- Modify: `pyproject.toml` (root: import-linter)
- Test: `services/identity/tests/test_application.py`

**Interfaces:**
- Consumes: everything of Task 9; `Page`, `PageRequest`, `page_of` (Task 1); `Principal`, `SubjectId`, `Permission`; `TaxId`.
- Produces: in `ledger_identity.application.ports`: protocols `Organizations` (`add(organization) -> None`, `get(organization_id) -> Organization | None`), `Companies` (`add(company) -> bool`, `get(company_id) -> Company | None`, `locked(company_id) -> Company | None`, `update(company, *, version: int) -> bool`, `of_member(subject, request: PageRequest) -> Page[Company]`, in the unit's organization), `Branches` (`add(branch) -> bool`, `get(company_id, branch_id) -> Branch | None`, `page(company_id, request) -> Page[Branch]`), `EmissionPoints` (`add(point) -> bool`, `get(company_id, point_id) -> EmissionPoint | None`, `page(company_id, branch_id, request) -> Page[EmissionPoint]`), `Memberships` (`get(company_id, subject) -> Membership | None` (active or revoked), `put(membership) -> None`, `owners(company_id) -> int` (active owners), `page(company_id, request) -> Page[Membership]` (active ones)), `Events` (`publish(event: IdentityEvent) -> None`); `IdentityUnit(*, organizations, companies, branches, emission_points, memberships, events, now: datetime)` (all methods async; `now` is the unit's time, which everything it records and announces carries; every repository works inside the unit's organization). In `ledger_identity.application.commands`: `create_company(unit, founder: Principal, *, tax_id, legal_name) -> Company`; `rename_company(unit, company_id, *, legal_name, admits: Callable[[int], bool]) -> Company`; `deactivate_company(unit, company_id, *, admits) -> Company`; `open_branch(unit, company_id, *, code, name) -> Branch`; `open_emission_point(unit, company_id, branch_id, *, code, name) -> EmissionPoint`; `Granted(membership: Membership, created: bool)` (`created`: the subject held no active role before); `grant_role(unit, company_id, *, subject, role, actor: SubjectId) -> Granted`; `revoke_role(unit, company_id, *, subject, actor: SubjectId) -> None` (both lock the company, then read the actor's own membership: Decision 24). In `ledger_identity.application.queries`: `organization(unit, organization_id) -> Organization`, `company(unit, company_id) -> Company`, `companies_of(unit, subject, request) -> Page[Company]`, `branch(unit, company_id, branch_id) -> Branch`, `branches(unit, company_id, request) -> Page[Branch]`, `emission_point(unit, company_id, branch_id, point_id) -> EmissionPoint`, `emission_points(unit, company_id, branch_id, request) -> Page[EmissionPoint]`, `memberships(unit, company_id, request) -> Page[Membership]`, `permissions_of(unit, company_id, subject) -> frozenset[Permission]` (what other services may let the subject do: `permissions_in`, Decision 25).

- [ ] **Step 1: Layering**

In the root `pyproject.toml`, add:

```toml
[[tool.importlinter.contracts]]
name = "Identity is hexagonal"
type = "layers"
containers = ["ledger_identity"]
exhaustive = true
layers = ["application", "domain"]

[[tool.importlinter.contracts]]
name = "Identity's use cases know ports, not adapters or frameworks"
type = "forbidden"
source_modules = ["ledger_identity.application"]
forbidden_modules = [
    "ledger_contracts", "ledger_platform", "pydantic", "sqlalchemy", "fastapi", "starlette", "grpc", "nats",
    "google", "psycopg",
]
```

(Later tasks extend the `layers` of "Identity is hexagonal" as the other layers appear; `exhaustive` makes a module without a layer an error.)

- [ ] **Step 2: Write the failing tests**

`services/identity/tests/test_application.py`:

```python
from datetime import UTC, datetime, timedelta
from typing import Final, final, override
lazy from collections.abc import Callable, Iterable
lazy from uuid import UUID

import pytest

from ledger_identity.application import commands, queries
from ledger_identity.application.ports import (
    Branches,
    Companies,
    EmissionPoints,
    Events,
    IdentityUnit,
    Memberships,
    Organizations,
)
from ledger_identity.domain.company import CompanyStatus
from ledger_identity.domain.errors import (
    ActorNotAllowedError,
    BranchCodeTakenError,
    BranchNotFoundError,
    CompanyInactiveError,
    CompanyNotFoundError,
    EmissionPointCodeTakenError,
    EmissionPointNotFoundError,
    LastOwnerError,
    OrganizationNotFoundError,
    OwnersOnlyError,
    StaleVersionError,
    TaxIdTakenError,
)
from ledger_identity.domain.events import (
    CompanyCreated,
    CompanyDeactivated,
    CompanyUpdated,
    MembershipGranted,
    MembershipRevoked,
)
from ledger_identity.domain.ids import BranchId, EmissionPointId
from ledger_identity.domain.organization import Organization
from ledger_identity.domain.roles import READ_ONLY, Role
from ledger_identity.domain.text import parse_code, parse_legal_name, parse_name
from ledger_kernel.access import Permission, Principal, SubjectId
from ledger_kernel.countries.do import TaxId
from ledger_kernel.ids import CompanyId, OrganizationId, new_id
from ledger_kernel.pagination import PageRequest, page_of
lazy from ledger_identity.domain.branches import Branch, EmissionPoint
lazy from ledger_identity.domain.company import Company
lazy from ledger_identity.domain.events import IdentityEvent
lazy from ledger_identity.domain.membership import Membership
lazy from ledger_kernel.pagination import Page

NOW: Final = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)
LATER: Final = NOW + timedelta(minutes=5)
ORGANIZATION: Final = OrganizationId(new_id())
FOUNDER: Final = Principal(subject=SubjectId("2001"), organization=ORGANIZATION, provider_organization="1001")
STRANGER: Final = Principal(
    subject=SubjectId("9001"), organization=OrganizationId(new_id()), provider_organization="9"
)
OWNER: Final = FOUNDER.subject
ADMIN: Final = SubjectId("2002")
VIEWER: Final = SubjectId("3001")
RNC: Final = TaxId.parse("101850043")
CEDULA: Final = TaxId.parse("00113918205")


def anything(version: int) -> bool:
    del version
    return True


def nothing(version: int) -> bool:
    del version
    return False


def paged[T](items: Iterable[T], request: PageRequest, key: Callable[[T], UUID]) -> Page[T]:
    """A page as a repository reads it: in id order, after the cursor, one more than asked for."""
    after = [item for item in sorted(items, key=key) if request.after is None or key(item) > request.after]
    return page_of(after[: request.size + 1], request, key=key)


@final
class State:
    """identity's state in memory: what a unit reads and changes, and what it announced."""

    def __init__(self) -> None:
        self.organizations: dict[OrganizationId, Organization] = {}
        self.companies: dict[CompanyId, Company] = {}
        self.branches: dict[BranchId, Branch] = {}
        self.points: dict[EmissionPointId, EmissionPoint] = {}
        self.memberships: dict[tuple[CompanyId, SubjectId], Membership] = {}
        self.events: list[IdentityEvent] = []
        self.races_lost = False
        """While set, every compare-and-set of a company finds it changed by someone else."""

    def unit(self, now: datetime = NOW, organization: OrganizationId = ORGANIZATION) -> IdentityUnit:
        return IdentityUnit(
            organizations=MemoryOrganizations(self),
            companies=MemoryCompanies(self, organization),
            branches=MemoryBranches(self),
            emission_points=MemoryEmissionPoints(self),
            memberships=MemoryMemberships(self),
            events=MemoryEvents(self),
            now=now,
        )


@final
class MemoryOrganizations(Organizations):
    def __init__(self, state: State) -> None:
        self._state = state

    @override
    async def add(self, organization: Organization) -> None:
        self._state.organizations.setdefault(organization.id, organization)

    @override
    async def get(self, organization: OrganizationId) -> Organization | None:
        return self._state.organizations.get(organization)


@final
class MemoryCompanies(Companies):
    def __init__(self, state: State, organization: OrganizationId) -> None:
        self._state = state
        self._organization = organization

    @override
    async def add(self, company: Company) -> bool:
        kept = self._state.companies.values()
        if any(
            other.organization == company.organization and other.tax_id == company.tax_id for other in kept
        ):
            return False
        self._state.companies[company.id] = company
        return True

    @override
    async def get(self, company: CompanyId) -> Company | None:
        return self._state.companies.get(company)

    @override
    async def locked(self, company: CompanyId) -> Company | None:
        return self._state.companies.get(company)

    @override
    async def update(self, company: Company, *, version: int) -> bool:
        current = self._state.companies.get(company.id)
        if self._state.races_lost or current is None or current.version != version:
            return False
        self._state.companies[company.id] = company
        return True

    @override
    async def of_member(self, subject: SubjectId, request: PageRequest) -> Page[Company]:
        held = {
            company
            for (company, member), membership in self._state.memberships.items()
            if member == subject and membership.active
        }
        mine = [
            company
            for company in self._state.companies.values()
            if company.organization == self._organization and company.id in held
        ]
        return paged(mine, request, key=lambda company: company.id)


@final
class MemoryBranches(Branches):
    def __init__(self, state: State) -> None:
        self._state = state

    @override
    async def add(self, branch: Branch) -> bool:
        if any(
            other.tenant == branch.tenant and other.code == branch.code
            for other in self._state.branches.values()
        ):
            return False
        self._state.branches[branch.id] = branch
        return True

    @override
    async def get(self, company: CompanyId, branch: BranchId) -> Branch | None:
        found = self._state.branches.get(branch)
        return found if found is not None and found.tenant.company == company else None

    @override
    async def page(self, company: CompanyId, request: PageRequest) -> Page[Branch]:
        mine = [branch for branch in self._state.branches.values() if branch.tenant.company == company]
        return paged(mine, request, key=lambda branch: branch.id)


@final
class MemoryEmissionPoints(EmissionPoints):
    def __init__(self, state: State) -> None:
        self._state = state

    @override
    async def add(self, point: EmissionPoint) -> bool:
        if any(
            other.branch == point.branch and other.code == point.code for other in self._state.points.values()
        ):
            return False
        self._state.points[point.id] = point
        return True

    @override
    async def get(self, company: CompanyId, point: EmissionPointId) -> EmissionPoint | None:
        found = self._state.points.get(point)
        return found if found is not None and found.tenant.company == company else None

    @override
    async def page(self, company: CompanyId, branch: BranchId, request: PageRequest) -> Page[EmissionPoint]:
        mine = [p for p in self._state.points.values() if p.tenant.company == company and p.branch == branch]
        return paged(mine, request, key=lambda point: point.id)


@final
class MemoryMemberships(Memberships):
    def __init__(self, state: State) -> None:
        self._state = state

    @override
    async def get(self, company: CompanyId, subject: SubjectId) -> Membership | None:
        return self._state.memberships.get((company, subject))

    @override
    async def put(self, membership: Membership) -> None:
        self._state.memberships[membership.tenant.company, membership.subject] = membership

    @override
    async def owners(self, company: CompanyId) -> int:
        return sum(1 for member in self._active(company) if member.role is Role.OWNER)

    @override
    async def page(self, company: CompanyId, request: PageRequest) -> Page[Membership]:
        return paged(self._active(company), request, key=lambda membership: membership.id)

    def _active(self, company: CompanyId) -> list[Membership]:
        return [m for (held, _), m in self._state.memberships.items() if held == company and m.active]


@final
class MemoryEvents(Events):
    def __init__(self, state: State) -> None:
        self._state = state

    @override
    async def publish(self, event: IdentityEvent) -> None:
        self._state.events.append(event)


@pytest.fixture
def state() -> State:
    return State()


async def created(state: State, tax_id: TaxId = RNC, founder: Principal = FOUNDER) -> Company:
    return await commands.create_company(
        state.unit(organization=founder.organization),
        founder,
        tax_id=tax_id,
        legal_name=parse_legal_name("Duarte"),
    )


async def with_an_admin(state: State, company: Company) -> None:
    """``ADMIN`` made an admin of ``company`` by its owner."""
    await commands.grant_role(state.unit(), company.id, subject=ADMIN, role=Role.ADMIN, actor=OWNER)


# ── Companies ───────────────────────────────────────────────────────────────────


async def test_a_new_company_has_its_founder_as_owner_and_both_are_announced(state: State) -> None:
    company = await created(state)
    assert state.organizations == {
        ORGANIZATION: Organization(id=ORGANIZATION, provider_id="1001", created_at=NOW)
    }
    assert state.companies == {company.id: company}
    owner = state.memberships[company.id, OWNER]
    assert owner.role is Role.OWNER
    assert [type(event) for event in state.events] == [CompanyCreated, MembershipGranted]


async def test_a_second_company_of_the_organization_keeps_the_organization_as_it_was(state: State) -> None:
    await created(state)
    await commands.create_company(
        state.unit(LATER), FOUNDER, tax_id=CEDULA, legal_name=parse_legal_name("Duarte II")
    )
    assert state.organizations[ORGANIZATION].created_at == NOW
    assert len(state.companies) == 2


async def test_a_tax_id_the_organization_has_is_taken(state: State) -> None:
    await created(state)
    with pytest.raises(TaxIdTakenError):
        await created(state)
    assert len(state.events) == 2


async def test_another_organization_may_register_the_same_tax_id(state: State) -> None:
    await created(state)
    other = await created(state, founder=STRANGER)
    assert other.organization == STRANGER.organization


async def test_a_rename_is_a_new_version_announced(state: State) -> None:
    company = await created(state)
    renamed = await commands.rename_company(
        state.unit(LATER), company.id, legal_name=parse_legal_name("Duarte Hermanos"), admits=anything
    )
    assert (renamed.version, state.companies[company.id]) == (2, renamed)
    assert isinstance(state.events[-1], CompanyUpdated)


async def test_a_rename_to_the_same_name_changes_and_announces_nothing(state: State) -> None:
    company = await created(state)
    same = await commands.rename_company(
        state.unit(LATER), company.id, legal_name=company.legal_name, admits=anything
    )
    assert (same, len(state.events)) == (company, 2)


async def test_a_change_the_precondition_does_not_admit_is_stale(state: State) -> None:
    company = await created(state)
    with pytest.raises(StaleVersionError):
        await commands.rename_company(
            state.unit(LATER), company.id, legal_name=parse_legal_name("X"), admits=nothing
        )
    with pytest.raises(StaleVersionError):
        await commands.deactivate_company(state.unit(LATER), company.id, admits=nothing)
    assert state.companies[company.id] == company


async def test_a_company_changed_by_someone_else_meanwhile_is_stale(state: State) -> None:
    company = await created(state)
    state.races_lost = True
    with pytest.raises(StaleVersionError):
        await commands.rename_company(
            state.unit(LATER), company.id, legal_name=parse_legal_name("X"), admits=anything
        )
    assert len(state.events) == 2


async def test_a_deactivated_company_is_announced_and_opens_nothing_more(state: State) -> None:
    company = await created(state)
    inactive = await commands.deactivate_company(state.unit(LATER), company.id, admits=anything)
    assert (inactive.status, isinstance(state.events[-1], CompanyDeactivated)) == (
        CompanyStatus.INACTIVE,
        True,
    )
    with pytest.raises(CompanyInactiveError):
        await commands.open_branch(
            state.unit(LATER), company.id, code=parse_code("SUC1"), name=parse_name("S")
        )


async def test_a_company_that_does_not_exist_is_not_found(state: State) -> None:
    missing = CompanyId(new_id())
    with pytest.raises(CompanyNotFoundError):
        await commands.rename_company(
            state.unit(), missing, legal_name=parse_legal_name("X"), admits=anything
        )
    with pytest.raises(CompanyNotFoundError):
        await queries.company(state.unit(), missing)


# ── Branches and emission points ───────────────────────────────────────────────


async def test_branches_and_emission_points_are_opened_once_per_code(state: State) -> None:
    company = await created(state)
    branch = await commands.open_branch(
        state.unit(), company.id, code=parse_code("SUC1"), name=parse_name("Santiago")
    )
    with pytest.raises(BranchCodeTakenError):
        await commands.open_branch(
            state.unit(), company.id, code=parse_code("SUC1"), name=parse_name("Other")
        )
    point = await commands.open_emission_point(
        state.unit(), company.id, branch.id, code=parse_code("001"), name=parse_name("Caja")
    )
    with pytest.raises(EmissionPointCodeTakenError):
        await commands.open_emission_point(
            state.unit(), company.id, branch.id, code=parse_code("001"), name=parse_name("Caja")
        )
    assert await queries.branch(state.unit(), company.id, branch.id) == branch
    assert await queries.emission_point(state.unit(), company.id, branch.id, point.id) == point
    assert (await queries.branches(state.unit(), company.id, PageRequest())).items == (branch,)
    assert (await queries.emission_points(state.unit(), company.id, branch.id, PageRequest())).items == (
        point,
    )


async def test_an_emission_point_opens_in_a_branch_that_exists_only(state: State) -> None:
    company = await created(state)
    with pytest.raises(BranchNotFoundError):
        await commands.open_emission_point(
            state.unit(), company.id, BranchId(new_id()), code=parse_code("001"), name=parse_name("Caja")
        )
    with pytest.raises(BranchNotFoundError):
        await queries.emission_points(state.unit(), company.id, BranchId(new_id()), PageRequest())
    with pytest.raises(BranchNotFoundError):
        await queries.branch(state.unit(), company.id, BranchId(new_id()))


async def test_an_emission_point_is_found_under_its_own_branch_only(state: State) -> None:
    company = await created(state)
    first = await commands.open_branch(
        state.unit(), company.id, code=parse_code("SUC1"), name=parse_name("A")
    )
    second = await commands.open_branch(
        state.unit(), company.id, code=parse_code("SUC2"), name=parse_name("B")
    )
    point = await commands.open_emission_point(
        state.unit(), company.id, first.id, code=parse_code("001"), name=parse_name("Caja")
    )
    with pytest.raises(EmissionPointNotFoundError):
        await queries.emission_point(state.unit(), company.id, second.id, point.id)
    with pytest.raises(EmissionPointNotFoundError):
        await queries.emission_point(state.unit(), company.id, first.id, EmissionPointId(new_id()))


# ── Memberships ─────────────────────────────────────────────────────────────────


async def test_granting_a_role_creates_a_membership_or_changes_it(state: State) -> None:
    company = await created(state)
    await with_an_admin(state, company)
    first = await commands.grant_role(state.unit(), company.id, subject=VIEWER, role=Role.VIEWER, actor=ADMIN)
    again = await commands.grant_role(
        state.unit(LATER), company.id, subject=VIEWER, role=Role.VIEWER, actor=ADMIN
    )
    changed = await commands.grant_role(
        state.unit(LATER), company.id, subject=VIEWER, role=Role.ISSUER, actor=ADMIN
    )
    assert (first.created, again.created, changed.created) == (True, False, False)
    assert (again.membership, changed.membership.role) == (first.membership, Role.ISSUER)
    grants = [event for event in state.events if isinstance(event, MembershipGranted)]
    assert [(event.role, event.previous) for event in grants] == [
        (Role.OWNER, None),
        (Role.ADMIN, None),
        (Role.VIEWER, None),
        (Role.ISSUER, Role.VIEWER),
    ]


async def test_revoking_a_role_keeps_the_membership_revoked_and_announces_it(state: State) -> None:
    company = await created(state)
    await commands.grant_role(state.unit(), company.id, subject=VIEWER, role=Role.VIEWER, actor=OWNER)
    await commands.revoke_role(state.unit(LATER), company.id, subject=VIEWER, actor=OWNER)
    revoked = state.memberships[company.id, VIEWER]
    assert (revoked.active, revoked.version, revoked.changed_at, revoked.changed_by) == (
        False,
        2,
        LATER,
        OWNER,
    )
    assert isinstance(state.events[-1], MembershipRevoked)
    assert await queries.permissions_of(state.unit(), company.id, VIEWER) == frozenset()
    listed = await queries.memberships(state.unit(), company.id, PageRequest())
    assert [membership.subject for membership in listed.items] == [OWNER]
    assert (await queries.companies_of(state.unit(), VIEWER, PageRequest())).items == ()


async def test_a_role_granted_again_continues_the_membership_s_versions(state: State) -> None:
    company = await created(state)
    await commands.grant_role(state.unit(), company.id, subject=VIEWER, role=Role.VIEWER, actor=OWNER)
    await commands.revoke_role(state.unit(), company.id, subject=VIEWER, actor=OWNER)
    again = await commands.grant_role(
        state.unit(LATER), company.id, subject=VIEWER, role=Role.ISSUER, actor=OWNER
    )
    assert (again.created, again.membership.version) == (True, 3)
    versions = [
        event.version
        for event in state.events
        if isinstance(event, MembershipGranted | MembershipRevoked) and event.subject == VIEWER
    ]
    assert versions == [1, 2, 3]


async def test_the_founder_cannot_leave_the_company_ownerless(state: State) -> None:
    company = await created(state)
    with pytest.raises(LastOwnerError):
        await commands.revoke_role(state.unit(), company.id, subject=OWNER, actor=OWNER)
    with pytest.raises(LastOwnerError):
        await commands.grant_role(state.unit(LATER), company.id, subject=OWNER, role=Role.ADMIN, actor=OWNER)


async def test_the_actor_acts_with_the_role_it_holds_when_the_change_is_made(state: State) -> None:
    company = await created(state)
    await with_an_admin(state, company)
    await commands.grant_role(state.unit(), company.id, subject=ADMIN, role=Role.VIEWER, actor=OWNER)
    with pytest.raises(ActorNotAllowedError):
        await commands.grant_role(state.unit(), company.id, subject=VIEWER, role=Role.VIEWER, actor=ADMIN)
    await commands.grant_role(state.unit(), company.id, subject=ADMIN, role=Role.ADMIN, actor=OWNER)
    with pytest.raises(OwnersOnlyError):
        await commands.grant_role(state.unit(), company.id, subject=ADMIN, role=Role.OWNER, actor=ADMIN)
    await commands.revoke_role(state.unit(), company.id, subject=ADMIN, actor=OWNER)
    with pytest.raises(CompanyNotFoundError):
        await commands.revoke_role(state.unit(), company.id, subject=OWNER, actor=ADMIN)
    with pytest.raises(CompanyNotFoundError):
        await commands.grant_role(
            state.unit(), company.id, subject=VIEWER, role=Role.VIEWER, actor=SubjectId("9999")
        )


async def test_memberships_of_a_company_that_does_not_exist_are_not_found(state: State) -> None:
    with pytest.raises(CompanyNotFoundError):
        await commands.grant_role(
            state.unit(), CompanyId(new_id()), subject=VIEWER, role=Role.VIEWER, actor=OWNER
        )


# ── Queries ─────────────────────────────────────────────────────────────────────


async def test_the_organization_is_known_once_it_has_a_company(state: State) -> None:
    with pytest.raises(OrganizationNotFoundError):
        await queries.organization(state.unit(), ORGANIZATION)
    await created(state)
    assert (await queries.organization(state.unit(), ORGANIZATION)).provider_id == "1001"


async def test_a_principal_lists_the_companies_it_holds_a_role_in_page_by_page(state: State) -> None:
    mine = [await created(state, TaxId.parse(rnc)) for rnc in ("101850043", "130000012", "401007551")]
    theirs = await created(state, founder=STRANGER)
    first = await queries.companies_of(state.unit(), OWNER, PageRequest(size=2))
    assert first.items == tuple(sorted(mine, key=lambda company: company.id)[:2])
    assert first.next_cursor is not None
    rest = await queries.companies_of(state.unit(), OWNER, PageRequest(size=2, after=first.items[-1].id))
    assert (len(rest.items), rest.next_cursor) == (1, None)
    strangers = state.unit(organization=STRANGER.organization)
    assert (await queries.companies_of(strangers, STRANGER.subject, PageRequest())).items == (theirs,)


async def test_permissions_are_the_role_s_or_none(state: State) -> None:
    company = await created(state)
    assert await queries.permissions_of(state.unit(), company.id, OWNER) == frozenset(Permission)
    assert await queries.permissions_of(state.unit(), company.id, VIEWER) == frozenset()


async def test_other_services_get_the_reading_permissions_only_of_a_deactivated_company(state: State) -> None:
    company = await created(state)
    await commands.deactivate_company(state.unit(LATER), company.id, admits=anything)
    assert await queries.permissions_of(state.unit(), company.id, OWNER) == READ_ONLY
    assert await queries.permissions_of(state.unit(), CompanyId(new_id()), OWNER) == frozenset()


async def test_the_memberships_of_a_company_are_listed(state: State) -> None:
    company = await created(state)
    await commands.grant_role(state.unit(), company.id, subject=VIEWER, role=Role.VIEWER, actor=OWNER)
    page = await queries.memberships(state.unit(), company.id, PageRequest())
    assert {membership.subject for membership in page.items} == {"2001", "3001"}
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest services/identity/tests/test_application.py -q --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'ledger_identity.application'`.

- [ ] **Step 4: Implement**

`services/identity/src/ledger_identity/application/__init__.py`:

```python
"""identity's use cases, over ports: one unit (one transaction) per use case, no SQL, no HTTP."""
```

`services/identity/src/ledger_identity/application/ports.py`:

```python
"""What the use cases need of the world: identity's state, read and changed inside one unit of work.

A unit is one transaction scoped to an organization or one of its companies: row-level security limits what
its repositories see to that scope, and every repository is bound to the unit's organization and names it,
and the company where a row is a company's, in every statement (tenancy is checked twice, §2). The methods
are abstract so that they need no body: a ``...`` is a statement no test could reach.
"""

from abc import ABCMeta, abstractmethod
from dataclasses import dataclass
from typing import Protocol, final
lazy from datetime import datetime

lazy from ledger_identity.domain.branches import Branch, EmissionPoint
lazy from ledger_identity.domain.company import Company
lazy from ledger_identity.domain.events import IdentityEvent
lazy from ledger_identity.domain.ids import BranchId, EmissionPointId
lazy from ledger_identity.domain.membership import Membership
lazy from ledger_identity.domain.organization import Organization
lazy from ledger_kernel.access import SubjectId
lazy from ledger_kernel.ids import CompanyId, OrganizationId
lazy from ledger_kernel.pagination import Page, PageRequest


class Organizations(Protocol, metaclass=ABCMeta):
    """The organizations identity has seen."""

    @abstractmethod
    async def add(self, organization: Organization) -> None:
        """Keep ``organization``; one already kept stays as it was."""

    @abstractmethod
    async def get(self, organization: OrganizationId) -> Organization | None:
        """The organization, or ``None`` before its first company."""


class Companies(Protocol, metaclass=ABCMeta):
    """The companies of the unit's organization."""

    @abstractmethod
    async def add(self, company: Company) -> bool:
        """Keep a new company; false when the organization has a company with its tax id."""

    @abstractmethod
    async def get(self, company: CompanyId) -> Company | None:
        """The company, or ``None``."""

    @abstractmethod
    async def locked(self, company: CompanyId) -> Company | None:
        """The company, locked until the unit ends: changes of its memberships wait for one another."""

    @abstractmethod
    async def update(self, company: Company, *, version: int) -> bool:
        """Replace the company kept at ``version`` by ``company``; false when it is not at that version."""

    @abstractmethod
    async def of_member(self, subject: SubjectId, request: PageRequest) -> Page[Company]:
        """The companies of the unit's organization in which ``subject`` holds an active role, in id order."""


class Branches(Protocol, metaclass=ABCMeta):
    """The branches of the unit's company."""

    @abstractmethod
    async def add(self, branch: Branch) -> bool:
        """Keep a new branch; false when its company has a branch with its code."""

    @abstractmethod
    async def get(self, company: CompanyId, branch: BranchId) -> Branch | None:
        """The branch of ``company``, or ``None``."""

    @abstractmethod
    async def page(self, company: CompanyId, request: PageRequest) -> Page[Branch]:
        """The branches of ``company``, in id order."""


class EmissionPoints(Protocol, metaclass=ABCMeta):
    """The emission points of the unit's company."""

    @abstractmethod
    async def add(self, point: EmissionPoint) -> bool:
        """Keep a new emission point; false when its branch has one with its code."""

    @abstractmethod
    async def get(self, company: CompanyId, point: EmissionPointId) -> EmissionPoint | None:
        """The emission point of ``company``, or ``None``."""

    @abstractmethod
    async def page(self, company: CompanyId, branch: BranchId, request: PageRequest) -> Page[EmissionPoint]:
        """The emission points of ``branch``, in id order."""


class Memberships(Protocol, metaclass=ABCMeta):
    """Who holds, or held, which role in the companies of the unit's organization."""

    @abstractmethod
    async def get(self, company: CompanyId, subject: SubjectId) -> Membership | None:
        """``subject``'s membership in ``company``, active or revoked; ``None`` if it never held a role."""

    @abstractmethod
    async def put(self, membership: Membership) -> None:
        """Keep ``membership``, in place of the subject's previous one in the company (a revocation too)."""

    @abstractmethod
    async def owners(self, company: CompanyId) -> int:
        """How many active owners ``company`` has."""

    @abstractmethod
    async def page(self, company: CompanyId, request: PageRequest) -> Page[Membership]:
        """The active memberships of ``company``, in id order."""


class Events(Protocol, metaclass=ABCMeta):
    """What the unit announces once it commits."""

    @abstractmethod
    async def publish(self, event: IdentityEvent) -> None:
        """Announce ``event`` when, and only if, the unit commits (the outbox)."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class IdentityUnit:
    """One unit of work: identity's state as one transaction sees it, and what it announces."""

    organizations: Organizations
    companies: Companies
    branches: Branches
    emission_points: EmissionPoints
    memberships: Memberships
    events: Events
    now: datetime
    """The unit's time: everything it records and announces happened then, as one transaction does."""
```

`services/identity/src/ledger_identity/application/queries.py`:

```python
"""What reads identity's state: each query runs in a unit scoped to the caller's organization or company."""

from ledger_identity.domain.errors import (
    BranchNotFoundError,
    CompanyNotFoundError,
    EmissionPointNotFoundError,
    OrganizationNotFoundError,
)
from ledger_identity.domain.membership import permissions_in
lazy from ledger_identity.application.ports import IdentityUnit
lazy from ledger_identity.domain.branches import Branch, EmissionPoint
lazy from ledger_identity.domain.company import Company
lazy from ledger_identity.domain.ids import BranchId, EmissionPointId
lazy from ledger_identity.domain.membership import Membership
lazy from ledger_identity.domain.organization import Organization
lazy from ledger_kernel.access import Permission, SubjectId
lazy from ledger_kernel.ids import CompanyId, OrganizationId
lazy from ledger_kernel.pagination import Page, PageRequest


async def organization(unit: IdentityUnit, organization_id: OrganizationId) -> Organization:
    """The organization; ``OrganizationNotFoundError`` before its first company."""
    found = await unit.organizations.get(organization_id)
    if found is None:
        raise OrganizationNotFoundError(organization_id)
    return found


async def company(unit: IdentityUnit, company_id: CompanyId) -> Company:
    """The company; ``CompanyNotFoundError`` when the organization has none by that id."""
    found = await unit.companies.get(company_id)
    if found is None:
        raise CompanyNotFoundError(company_id)
    return found


async def companies_of(unit: IdentityUnit, subject: SubjectId, request: PageRequest) -> Page[Company]:
    """The companies of the unit's organization in which ``subject`` holds a role."""
    return await unit.companies.of_member(subject, request)


async def branch(unit: IdentityUnit, company_id: CompanyId, branch_id: BranchId) -> Branch:
    """The branch of the company; ``BranchNotFoundError`` otherwise."""
    found = await unit.branches.get(company_id, branch_id)
    if found is None:
        raise BranchNotFoundError(branch_id)
    return found


async def branches(unit: IdentityUnit, company_id: CompanyId, request: PageRequest) -> Page[Branch]:
    """The branches of the company."""
    return await unit.branches.page(company_id, request)


async def emission_point(
    unit: IdentityUnit, company_id: CompanyId, branch_id: BranchId, point_id: EmissionPointId
) -> EmissionPoint:
    """The emission point of the branch; ``EmissionPointNotFoundError`` otherwise."""
    found = await unit.emission_points.get(company_id, point_id)
    if found is None or found.branch != branch_id:
        raise EmissionPointNotFoundError(point_id)
    return found


async def emission_points(
    unit: IdentityUnit, company_id: CompanyId, branch_id: BranchId, request: PageRequest
) -> Page[EmissionPoint]:
    """The emission points of the branch, which must exist."""
    await branch(unit, company_id, branch_id)
    return await unit.emission_points.page(company_id, branch_id, request)


async def memberships(unit: IdentityUnit, company_id: CompanyId, request: PageRequest) -> Page[Membership]:
    """Who holds which role in the company."""
    return await unit.memberships.page(company_id, request)


async def permissions_of(
    unit: IdentityUnit, company_id: CompanyId, subject: SubjectId
) -> frozenset[Permission]:
    """What other services may let ``subject`` do in the company, which they cannot see deactivated.

    Its role's permissions; the reading ones only while the company is deactivated; none without an active
    membership, or in a company the organization does not have.
    """
    found = await unit.companies.get(company_id)
    membership = await unit.memberships.get(company_id, subject)
    if found is None or membership is None:
        return frozenset()
    return permissions_in(found, membership)
```

`services/identity/src/ledger_identity/application/commands.py`:

```python
"""What changes identity's state: each use case runs in one unit, which commits its changes and events."""

from dataclasses import dataclass
from typing import final
lazy from collections.abc import Callable

from ledger_identity.application.queries import branch, company
from ledger_identity.domain.branches import Branch, EmissionPoint
from ledger_identity.domain.company import Company
from ledger_identity.domain.errors import (
    BranchCodeTakenError,
    CompanyNotFoundError,
    EmissionPointCodeTakenError,
    StaleVersionError,
    TaxIdTakenError,
)
from ledger_identity.domain.membership import Actor, Roster, found, grant, revoke
from ledger_identity.domain.organization import Organization
lazy from ledger_identity.application.ports import IdentityUnit
lazy from ledger_identity.domain.ids import BranchId
lazy from ledger_identity.domain.membership import Membership
lazy from ledger_identity.domain.roles import Role
lazy from ledger_identity.domain.text import Code, LegalName, Name
lazy from ledger_kernel.access import Principal, SubjectId
lazy from ledger_kernel.countries.do import TaxId
lazy from ledger_kernel.ids import CompanyId

type Admits = Callable[[int], bool]
"""Whether a change may apply to a company at a version: the client's ``If-Match``."""


@final
@dataclass(frozen=True, slots=True)
class Granted:
    """The membership a grant left, and whether the subject held no active role before it."""

    membership: Membership
    created: bool


async def create_company(
    unit: IdentityUnit, founder: Principal, *, tax_id: TaxId, legal_name: LegalName
) -> Company:
    """Register a company of the founder's organization, with the founder as its first owner."""
    await unit.organizations.add(Organization.of(founder, now=unit.now))
    registered, created = Company.register(
        organization=founder.organization, tax_id=tax_id, legal_name=legal_name, now=unit.now
    )
    if not await unit.companies.add(registered):
        raise TaxIdTakenError
    owner, granted = found(registered, founder, now=unit.now)
    await unit.memberships.put(owner)
    await unit.events.publish(created)
    await unit.events.publish(granted)
    return registered


async def _admitted(unit: IdentityUnit, company_id: CompanyId, admits: Admits) -> Company:
    current = await company(unit, company_id)
    if not admits(current.version):
        raise StaleVersionError(current.id)
    return current


async def _replace(unit: IdentityUnit, current: Company, changed: Company) -> None:
    """Store ``changed`` in place of ``current``, unless another unit changed the company meanwhile."""
    if not await unit.companies.update(changed, version=current.version):
        raise StaleVersionError(current.id)


async def rename_company(
    unit: IdentityUnit, company_id: CompanyId, *, legal_name: LegalName, admits: Admits
) -> Company:
    """Give the company ``legal_name``, if its current version is one ``admits``."""
    current = await _admitted(unit, company_id, admits)
    renamed, updated = current.rename(legal_name, now=unit.now)
    if updated is not None:
        await _replace(unit, current, renamed)
        await unit.events.publish(updated)
    return renamed


async def deactivate_company(unit: IdentityUnit, company_id: CompanyId, *, admits: Admits) -> Company:
    """Deactivate the company, if its current version is one ``admits``."""
    current = await _admitted(unit, company_id, admits)
    inactive, deactivated = current.deactivate(now=unit.now)
    await _replace(unit, current, inactive)
    await unit.events.publish(deactivated)
    return inactive


async def open_branch(unit: IdentityUnit, company_id: CompanyId, *, code: Code, name: Name) -> Branch:
    """Open a branch of the company under a code it does not use yet."""
    opened = Branch.open(await company(unit, company_id), code=code, name=name, now=unit.now)
    if not await unit.branches.add(opened):
        raise BranchCodeTakenError
    return opened


async def open_emission_point(
    unit: IdentityUnit, company_id: CompanyId, branch_id: BranchId, *, code: Code, name: Name
) -> EmissionPoint:
    """Open an emission point of one of the company's branches, under a code the branch does not use yet."""
    owner = await company(unit, company_id)
    opened = EmissionPoint.open(
        owner, await branch(unit, company_id, branch_id), code=code, name=name, now=unit.now
    )
    if not await unit.emission_points.add(opened):
        raise EmissionPointCodeTakenError
    return opened


async def _roster(unit: IdentityUnit, company_id: CompanyId, subject: SubjectId) -> Roster:
    """The company locked, ``subject``'s membership and the owners: one membership change at a time."""
    locked = await unit.companies.locked(company_id)
    if locked is None:
        raise CompanyNotFoundError(company_id)
    current = await unit.memberships.get(company_id, subject)
    return Roster(locked, current, await unit.memberships.owners(company_id))


async def _actor(unit: IdentityUnit, company_id: CompanyId, subject: SubjectId) -> Actor:
    """The actor as its own membership stands now, read once the company is locked.

    The route checked the actor's permission before; a change that committed since (a demotion, a revocation)
    counts. Without an active membership the company does not exist for the actor.
    """
    held = await unit.memberships.get(company_id, subject)
    if held is None or not held.active:
        raise CompanyNotFoundError(company_id)
    return Actor(subject, held.permissions)


async def grant_role(
    unit: IdentityUnit, company_id: CompanyId, *, subject: SubjectId, role: Role, actor: SubjectId
) -> Granted:
    """Give ``subject`` the role ``role`` in the company, as the membership of ``actor`` allows."""
    roster = await _roster(unit, company_id, subject)
    acting = await _actor(unit, company_id, actor)
    membership, granted = grant(roster, subject=subject, role=role, actor=acting, now=unit.now)
    if granted is not None:
        await unit.memberships.put(membership)
        await unit.events.publish(granted)
    current = roster.current
    return Granted(membership, created=current is None or not current.active)


async def revoke_role(
    unit: IdentityUnit, company_id: CompanyId, *, subject: SubjectId, actor: SubjectId
) -> None:
    """Take ``subject``'s role in the company away, as the membership of ``actor`` allows."""
    roster = await _roster(unit, company_id, subject)
    acting = await _actor(unit, company_id, actor)
    revoked, event = revoke(roster, subject=subject, actor=acting, now=unit.now)
    await unit.memberships.put(revoked)
    await unit.events.publish(event)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest services/identity -q --no-cov`
Expected: PASS.

- [ ] **Step 6: Gate**

Run: `uv run poe fmt && uv run poe check`
Expected: every step passes, coverage 100 %.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml services/identity
git commit -m "feat(identity): use cases over ports

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Identity's schema: tables, migrations, and their tests

**Files:**
- Create: `services/identity/src/ledger_identity/settings.py`
- Create: `services/identity/src/ledger_identity/adapters/__init__.py`, `adapters/postgres/__init__.py`, `adapters/postgres/tables.py`, `adapters/postgres/migrate.py`
- Create: `services/identity/src/ledger_identity/adapters/postgres/migrations/__init__.py`, `migrations/env.py`, `migrations/versions/__init__.py`, `migrations/versions/r0001_identity.py`
- Modify: `services/identity/pyproject.toml`, `pyproject.toml` (root)
- Test: `services/identity/tests/conftest.py`, `services/identity/tests/test_migrations.py`

**Interfaces:**
- Consumes: `ApiSettings` (Task 3); `CHASSIS_METADATA` and `CHASSIS_POLICIES` (`ledger_platform.schema`: `outbox`, `inbox`, `idempotency_keys`, M1 and Task 5); `organization_isolation`, `tenant_isolation`, `Database` (Task 4); `MAX_CODE`, `MAX_LEGAL_NAME`, `MAX_NAME`, `MembershipStatus` (Task 9), `MAX_SUBJECT_LENGTH` (Task 1); `Postgres` (Task 2), `restored_logging`.
- Produces: `ledger_identity.settings.IdentitySettings(ApiSettings)` (`service_name` defaults to `identity`). `ledger_identity.adapters.postgres.tables`: `IdentityTable(DeclarativeBase)` and the records `OrganizationRecord` (`organizations`: `id`, `provider_id`, `created_at`), `CompanyRecord` (`companies`: `id`, `organization_id` → organizations, `tax_id`, `legal_name`, `status`, `version`, `created_at`, `updated_at`; unique `uq_companies_tax_id (organization_id, tax_id)` and `uq_companies_organization (organization_id, id)`), `MembershipRecord` (`memberships`: `id`, `organization_id`, `company_id`, `subject`, `role`, `status` (`active` or `revoked`), `version`, `changed_at`, `changed_by`; foreign key `fk_memberships_company (organization_id, company_id)` → companies `(organization_id, id)`; unique `uq_memberships_subject (company_id, subject)`, index `ix_memberships_subject (organization_id, subject)`), `BranchRecord` (`branches`: `id`, `organization_id`, `company_id`, `code`, `name`, `created_at`; `fk_branches_company (organization_id, company_id)` → companies; unique `uq_branches_code (company_id, code)` and `uq_branches_company (company_id, id)`), `EmissionPointRecord` (`emission_points`: `id`, `organization_id`, `company_id`, `branch_id`, `code`, `name`, `created_at`; `fk_emission_points_company (organization_id, company_id)` → companies, `fk_emission_points_branch (company_id, branch_id)` → branches `(company_id, id)`; unique `uq_emission_points_code (branch_id, code)`). `ledger_identity.adapters.postgres.migrate`: `SCRIPTS`, `upgrade(url: str, revision: str = "head") -> None`, `downgrade(url: str, revision: str = "base") -> None`, `head() -> str` (the revision this version's migrations end at), `async require_head(database: Database, expected: str) -> None` (raises `SchemaNotCurrentError`, kind `UNAVAILABLE`, unless the database is at `expected`: identity's readiness check, wired in Task 13), `main() -> int` (console script `ledger-identity-migrate`), `OfflineMigrationsError`, `MigrationUrlMissingError`. Revision `0001` creates every table above and the chassis's three, runs `CHASSIS_POLICIES` (`organization_isolation` on the idempotency keys), puts `organization_isolation` on organizations (column `id`), companies and memberships, and `tenant_isolation` on branches and emission points. Test fixtures (identity's `conftest.py`): `postgres` (session), `database_url` (session, migrated), `fresh_database_url` (an empty database per test), `database` (a pool on the migrated database, emptied after each test).

- [ ] **Step 1: Dependencies, the console script and layering**

`services/identity/pyproject.toml`:

```toml
[project]
name = "ledger-identity"
version = "0.1.0"
description = "The identity service: organizations, companies, branches, emission points, memberships and roles."
requires-python = ">=3.15"
dependencies = [
    "ledger-kernel",
    "ledger-platform",
    "alembic>=1.20.0",
    "pydantic>=2.14.1",
    "sqlalchemy>=2.1.4",
]

[project.scripts]
ledger-identity-migrate = "ledger_identity.adapters.postgres.migrate:main"

[tool.uv.sources]
ledger-kernel = { workspace = true }
ledger-platform = { workspace = true }

[build-system]
requires = ["uv_build>=0.13.0,<0.14.0"]
build-backend = "uv_build"
```

Root `pyproject.toml`: in `[tool.ruff.lint.flake8-type-checking]` add `"ledger_identity.adapters.postgres.tables.IdentityTable"` to `runtime-evaluated-base-classes`; in the contract "Identity is hexagonal" set `layers = ["adapters", "settings", "application", "domain"]`; add `"alembic"` to the `forbidden_modules` of the two identity `forbidden` contracts (domain and use cases).

Run: `uv lock && uv sync`
Expected: `alembic 1.20.0` and `mako` installed.

- [ ] **Step 2: Write the failing tests**

`services/identity/tests/conftest.py`:

```python
"""Fixtures of the identity service's tests: a real PostgreSQL migrated to this version's schema."""

lazy from collections.abc import AsyncGenerator, Iterator

import pytest
from sqlalchemy import text

from ledger_identity.adapters.postgres.migrate import upgrade
from ledger_kernel.ids import new_id
from ledger_platform.database import Database
from ledger_testing.containers import Postgres
from ledger_testing.logs import restored_logging

EMPTY_EVERY_TABLE = text(
    "TRUNCATE organizations, companies, memberships, branches, emission_points,"
    " outbox, inbox, idempotency_keys"
)
"""What a test leaves behind is removed: ``TRUNCATE`` is a privilege of the owner, which row-level security
does not filter."""


@pytest.fixture(autouse=True)
def restore_logging() -> Iterator[None]:
    """Each test starts from, and leaves, the plain baseline: building a service configures logging."""
    with restored_logging():
        yield


@pytest.fixture(scope="session")
def postgres() -> Iterator[Postgres]:
    """A real PostgreSQL for the whole session."""
    with Postgres.started() as server:
        yield server


@pytest.fixture(scope="session")
def database_url(postgres: Postgres) -> str:
    """identity's database, migrated to this version; the service connects as its owner, no superuser."""
    url = postgres.database("identity", role="identity", password="identity-password")
    upgrade(url)
    return url


@pytest.fixture
def fresh_database_url(postgres: Postgres) -> str:
    """An empty database of its own, owned by a role of its own: for the tests of the migrations."""
    name = f"fresh_{new_id().hex[16:]}"  # the random half of a UUIDv7
    return postgres.database(name, role=name, password="fresh-password")


@pytest.fixture
async def database(database_url: str) -> AsyncGenerator[Database]:
    """A pool on identity's database, every table emptied after the test."""
    async with Database.open(database_url, pool_size=4) as opened:
        yield opened
        async with opened.engine.begin() as connection:
            await connection.execute(EMPTY_EVERY_TABLE)
```

`services/identity/tests/test_migrations.py`:

```python
from typing import Final

import pytest
from alembic import command
from alembic.autogenerate import produce_migrations, render_python_code
from alembic.config import Config
from alembic.migration import MigrationContext
from pydantic import TypeAdapter
from sqlalchemy import MetaData, create_engine, inspect, text
from sqlalchemy.pool import NullPool

from ledger_identity.adapters.postgres import migrate
from ledger_identity.adapters.postgres.migrate import (
    SCRIPTS,
    MigrationUrlMissingError,
    OfflineMigrationsError,
    SchemaNotCurrentError,
    downgrade,
    head,
    require_head,
    upgrade,
)
from ledger_identity.adapters.postgres.tables import IdentityTable
from ledger_kernel.errors import ErrorKind
from ledger_platform.database import Database
from ledger_platform.schema import CHASSIS_METADATA

IDENTITY_TABLES: Final = frozenset(
    {"organizations", "companies", "memberships", "branches", "emission_points"}
)
CHASSIS_TABLES: Final = frozenset({"outbox", "inbox", "idempotency_keys"})
POLICIES: Final = TypeAdapter(list[tuple[str, str]])
SECURITY: Final = TypeAdapter(list[tuple[str, bool, bool]])


def tables_of(url: str) -> set[str]:
    engine = create_engine(url, poolclass=NullPool)
    try:
        with engine.connect() as connection:
            return set(inspect(connection).get_table_names())
    finally:
        engine.dispose()


def models() -> MetaData:
    """The chassis's tables and identity's, in one metadata, as the migrations must make them."""
    combined = MetaData()
    for table in (*CHASSIS_METADATA.tables.values(), *IdentityTable.metadata.tables.values()):
        table.to_metadata(combined)
    return combined


def pending_changes(url: str) -> str | None:
    """The migration the models would still need, as Alembic would write it; ``None`` when there is none."""
    engine = create_engine(url, poolclass=NullPool)
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(
                connection, opts={"compare_type": True, "compare_server_default": True}
            )
            operations = produce_migrations(context, models()).upgrade_ops
            return None if operations is None or operations.is_empty() else render_python_code(operations)
    finally:
        engine.dispose()


def scripts() -> Config:
    config = Config()
    config.set_main_option("script_location", SCRIPTS)
    return config


def test_the_schema_is_built_from_nothing_and_taken_back_to_nothing(fresh_database_url: str) -> None:
    upgrade(fresh_database_url)
    assert tables_of(fresh_database_url) == {*IDENTITY_TABLES, *CHASSIS_TABLES, "alembic_version"}
    downgrade(fresh_database_url)
    assert tables_of(fresh_database_url) == {"alembic_version"}
    upgrade(fresh_database_url)
    assert tables_of(fresh_database_url) >= IDENTITY_TABLES


def test_upgrading_a_database_already_at_this_version_changes_nothing(database_url: str) -> None:
    upgrade(database_url)
    assert pending_changes(database_url) is None


def test_the_migrated_schema_is_exactly_the_models(database_url: str) -> None:
    assert pending_changes(database_url) is None


def test_every_table_of_a_tenants_data_is_under_its_policy_forced_on_its_owner(database_url: str) -> None:
    engine = create_engine(database_url, poolclass=NullPool)
    try:
        with engine.connect() as connection:
            policies = POLICIES.validate_python(
                [
                    tuple(row)
                    for row in connection.execute(
                        text("SELECT tablename::text, policyname::text FROM pg_policies")
                    )
                ]
            )
            security = SECURITY.validate_python(
                [
                    tuple(row)
                    for row in connection.execute(
                        text(
                            "SELECT relname::text, relrowsecurity, relforcerowsecurity FROM pg_class "
                            "WHERE relkind = 'r' AND relnamespace = 'public'::regnamespace"
                        )
                    )
                ]
            )
    finally:
        engine.dispose()
    assert sorted(policies) == [
        ("branches", "tenant_isolation"),
        ("companies", "organization_isolation"),
        ("emission_points", "tenant_isolation"),
        ("idempotency_keys", "organization_isolation"),
        ("memberships", "organization_isolation"),
        ("organizations", "organization_isolation"),
    ]
    forced = {name for name, enabled, force in security if enabled and force}
    assert forced == IDENTITY_TABLES | {
        "idempotency_keys"
    }  # the outbox and the inbox stay outside on purpose


async def test_the_readiness_check_passes_on_a_database_at_this_version_only(
    database: Database, fresh_database_url: str
) -> None:
    assert head() == "0001"
    await require_head(database, head())
    upgrade(fresh_database_url)
    downgrade(fresh_database_url)  # alembic_version is left, empty
    async with Database.open(fresh_database_url, pool_size=1) as behind:
        with pytest.raises(SchemaNotCurrentError) as caught:
            await require_head(behind, head())
    assert (caught.value.kind, caught.value.code) == (ErrorKind.UNAVAILABLE, "schema_not_current")


def test_migrations_are_never_written_out_as_sql() -> None:
    config = scripts()
    config.attributes["url"] = "postgresql+psycopg://nobody@localhost/nothing"
    with pytest.raises(OfflineMigrationsError, match="run against a database"):
        command.upgrade(config, "head", sql=True)


def test_migrations_need_a_database_to_run_against() -> None:
    with pytest.raises(MigrationUrlMissingError, match="without a database URL"):
        command.upgrade(scripts(), "head")


def test_the_command_line_migrates_the_database_its_settings_name(
    monkeypatch: pytest.MonkeyPatch, fresh_database_url: str
) -> None:
    for name, value in {
        "LEDGER_DATABASE_URL": fresh_database_url,
        "LEDGER_TOKEN_ISSUER": "https://issuer.example",
        "LEDGER_TOKEN_AUDIENCE": "ledger",
        "LEDGER_TOKEN_JWKS_URL": "https://issuer.example/keys",
    }.items():
        monkeypatch.setenv(name, value)
    assert migrate.main() == 0
    assert tables_of(fresh_database_url) >= IDENTITY_TABLES
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest services/identity/tests/test_migrations.py -q --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'ledger_identity.adapters'`.

- [ ] **Step 4: Implement**

`services/identity/src/ledger_identity/settings.py`:

```python
"""The identity service's settings: the chassis's and the bearer-token policy, from ``LEDGER_*`` variables."""

from pydantic import Field

from ledger_platform.settings import ApiSettings


class IdentitySettings(ApiSettings):
    """identity configures nothing of its own yet; its name defaults to ``identity``."""

    service_name: str = Field(default="identity", pattern=r"^[a-z][a-z0-9-]{1,30}$")
```

`services/identity/src/ledger_identity/adapters/__init__.py`:

```python
"""How identity reaches the world it depends on: PostgreSQL, the outbox, the permission source."""
```

`services/identity/src/ledger_identity/adapters/postgres/__init__.py`:

```python
"""identity's state in PostgreSQL: tables, repositories and migrations (SQLAlchemy Core, Alembic)."""
```

`services/identity/src/ledger_identity/adapters/postgres/tables.py`:

```python
"""identity's tables, typed for SQLAlchemy Core: what the migrations create and the repositories use.

Organizations own their companies and the memberships in them (``organization_id``); companies own their
branches and emission points (``organization_id`` and ``company_id``). The migrations put each table under the
isolation of its owner. A row refers to another through a foreign key that carries the tenant
(``(organization_id, company_id)`` to a company, ``(company_id, branch_id)`` to a branch): PostgreSQL's
referential checks bypass row-level security, so a single-column key would let a row of one organization
point at another's company.
"""

from datetime import datetime
from typing import ClassVar, Final
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    MetaData,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql.schema import SchemaItem

from ledger_identity.domain.text import MAX_CODE, MAX_LEGAL_NAME, MAX_NAME
from ledger_kernel.access import MAX_SUBJECT_LENGTH

TAX_ID_LENGTH: Final = 11
PROVIDER_ID_LENGTH: Final = 64
WORD_LENGTH: Final = 16
"""For a status or a role: a short word of a closed list, which a check constraint names."""


class IdentityTable(DeclarativeBase):
    """identity's own tables; the chassis's are ``PlatformTable``'s."""

    metadata: ClassVar[MetaData] = MetaData()


class OrganizationRecord(IdentityTable):
    """An organization, written with its first company."""

    __tablename__: str = "organizations"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    provider_id: Mapped[str] = mapped_column(String(PROVIDER_ID_LENGTH))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CompanyRecord(IdentityTable):
    """A company of an organization, at its current version."""

    __tablename__: str = "companies"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint("organization_id", "tax_id", name="uq_companies_tax_id"),
        UniqueConstraint("organization_id", "id", name="uq_companies_organization"),
        CheckConstraint("status IN ('active', 'inactive')", name="ck_companies_status"),
        CheckConstraint("version > 0", name="ck_companies_version"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"))
    tax_id: Mapped[str] = mapped_column(String(TAX_ID_LENGTH))
    legal_name: Mapped[str] = mapped_column(String(MAX_LEGAL_NAME))
    status: Mapped[str] = mapped_column(String(WORD_LENGTH))
    version: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


def _of_company(table: str) -> ForeignKeyConstraint:
    """The key from ``table``'s ``(organization_id, company_id)`` to its company: the tenant is part of it."""
    return ForeignKeyConstraint(
        ["organization_id", "company_id"],
        ["companies.organization_id", "companies.id"],
        name=f"fk_{table}_company",
    )


class MembershipRecord(IdentityTable):
    """A principal's role in a company, or the one it held until it was revoked."""

    __tablename__: str = "memberships"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint("company_id", "subject", name="uq_memberships_subject"),
        _of_company("memberships"),
        CheckConstraint(
            "role IN ('owner', 'admin', 'accountant', 'issuer', 'viewer')", name="ck_memberships_role"
        ),
        CheckConstraint("status IN ('active', 'revoked')", name="ck_memberships_status"),
        CheckConstraint("version > 0", name="ck_memberships_version"),
        Index("ix_memberships_subject", "organization_id", "subject"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    organization_id: Mapped[UUID] = mapped_column()
    company_id: Mapped[UUID] = mapped_column()
    subject: Mapped[str] = mapped_column(String(MAX_SUBJECT_LENGTH))
    role: Mapped[str] = mapped_column(String(WORD_LENGTH))
    status: Mapped[str] = mapped_column(String(WORD_LENGTH))
    version: Mapped[int] = mapped_column(BigInteger)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    changed_by: Mapped[str] = mapped_column(String(MAX_SUBJECT_LENGTH))


class BranchRecord(IdentityTable):
    """A branch of a company."""

    __tablename__: str = "branches"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint("company_id", "code", name="uq_branches_code"),
        UniqueConstraint("company_id", "id", name="uq_branches_company"),
        _of_company("branches"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    organization_id: Mapped[UUID] = mapped_column()
    company_id: Mapped[UUID] = mapped_column()
    code: Mapped[str] = mapped_column(String(MAX_CODE))
    name: Mapped[str] = mapped_column(String(MAX_NAME))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EmissionPointRecord(IdentityTable):
    """An emission point of a branch."""

    __tablename__: str = "emission_points"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint("branch_id", "code", name="uq_emission_points_code"),
        _of_company("emission_points"),
        ForeignKeyConstraint(
            ["company_id", "branch_id"],
            ["branches.company_id", "branches.id"],
            name="fk_emission_points_branch",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    organization_id: Mapped[UUID] = mapped_column()
    company_id: Mapped[UUID] = mapped_column()
    branch_id: Mapped[UUID] = mapped_column()
    code: Mapped[str] = mapped_column(String(MAX_CODE))
    name: Mapped[str] = mapped_column(String(MAX_NAME))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
```

`services/identity/src/ledger_identity/adapters/postgres/migrate.py`:

```python
"""identity's schema brought to this version's (Alembic), from the tests and from the command line.

Migrations run on a synchronous psycopg engine: no event loop is needed, so the same code runs in a test,
in the container that migrates before the service starts, and on a laptop. Each upgrade or downgrade is one
transaction. ``require_head`` is the running service's side: its readiness check that the database is at the
revision this version's migrations end at.
"""

from typing import ClassVar, Final

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import String, column, select, table

from ledger_identity.settings import IdentitySettings
from ledger_kernel.errors import LedgerError, UnavailableError
lazy from ledger_platform.database import Database

SCRIPTS: Final = "ledger_identity.adapters.postgres:migrations"
"""Where the migration scripts are, as a package resource: they ship inside the wheel and the image."""
HEAD: Final = "head"
BASE: Final = "base"
_VERSION: Final = select(column("version_num", String)).select_from(table("alembic_version"))
"""The revision Alembic recorded: one row at a revision, none at the base, no table before any migration."""


class OfflineMigrationsError(LedgerError):
    """Someone asked for the migrations as SQL text. They run against a database only."""

    code: ClassVar[str] = "offline_migrations"

    def __init__(self) -> None:
        super().__init__("identity's migrations run against a database, not as SQL text")


class MigrationUrlMissingError(LedgerError):
    """The migrations were started without a database to run against."""

    code: ClassVar[str] = "migration_url_missing"

    def __init__(self) -> None:
        super().__init__("identity's migrations were started without a database URL")


class SchemaNotCurrentError(UnavailableError):
    """The database is not at the revision this version needs: migrations are pending (or ran ahead)."""

    code: ClassVar[str] = "schema_not_current"

    def __init__(self, expected: str) -> None:
        super().__init__(f"the database schema is not at revision {expected}, the one this version needs")


def _scripts() -> Config:
    config = Config()
    config.set_main_option("script_location", SCRIPTS)
    return config


def _config(url: str) -> Config:
    config = _scripts()
    # An attribute, not an option: the option parser would read a % in the password as interpolation.
    config.attributes["url"] = url
    return config


def upgrade(url: str, revision: str = HEAD) -> None:
    """Bring the database at ``url`` (``postgresql+psycopg://``) up to ``revision``."""
    command.upgrade(_config(url), revision)


def downgrade(url: str, revision: str = BASE) -> None:
    """Take the database at ``url`` down to ``revision``."""
    command.downgrade(_config(url), revision)


def head() -> str:
    """The revision this version's migrations end at: the schema its code expects."""
    (current,) = ScriptDirectory.from_config(_scripts()).get_heads()  # one line of history, one head
    return current


async def require_head(database: Database, expected: str) -> None:
    """Return when the database is at revision ``expected``, else raise ``SchemaNotCurrentError``.

    A database no migration ever ran on has no ``alembic_version`` table, and the query fails instead: the
    check fails either way.
    """
    async with database.transaction(None) as unit:
        found = (await unit.connection.execute(_VERSION)).scalars().all()
    if list(found) != [expected]:
        raise SchemaNotCurrentError(expected)


def main() -> int:
    """``ledger-identity-migrate``: bring the database of ``LEDGER_DATABASE_URL`` to this version's schema."""
    upgrade(IdentitySettings().database_url.get_secret_value())
    return 0
```

`services/identity/src/ledger_identity/adapters/postgres/migrations/__init__.py`:

```python
"""identity's Alembic environment and migration scripts."""
```

`services/identity/src/ledger_identity/adapters/postgres/migrations/env.py`:

```python
"""Alembic's entry point: identity's migrations on the URL ``migrate`` hands over, in one transaction.

Every session of a migration waits at most ten seconds for a lock: a migration that cannot get one fails, it
never queues the service's own statements behind it.
"""

from typing import Final
lazy from collections.abc import Mapping

from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

from ledger_identity.adapters.postgres.migrate import MigrationUrlMissingError, OfflineMigrationsError
from ledger_identity.adapters.postgres.tables import IdentityTable
from ledger_platform.schema import CHASSIS_METADATA

_LOCK_TIMEOUT: Final = "-c lock_timeout=10s"


def _run() -> None:
    if context.is_offline_mode():
        raise OfflineMigrationsError
    attributes: Mapping[str, object] = context.config.attributes
    url = attributes.get("url")
    if not isinstance(url, str):
        raise MigrationUrlMissingError
    engine = create_engine(url, poolclass=NullPool, connect_args={"options": _LOCK_TIMEOUT})
    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection, target_metadata=[CHASSIS_METADATA, IdentityTable.metadata]
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


_run()
```

`services/identity/src/ledger_identity/adapters/postgres/migrations/versions/__init__.py`:

```python
"""identity's migration scripts, one per revision."""
```

`services/identity/src/ledger_identity/adapters/postgres/migrations/versions/r0001_identity.py`:

```python
"""Revision 0001, from nothing: identity's first schema.

The chassis's tables, then organizations, companies, memberships, branches and emission points, every table
that holds a tenant's data under row-level security.
"""

from typing import Final
lazy from collections.abc import Sequence
lazy from datetime import datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

from ledger_platform.database import organization_isolation, tenant_isolation
from ledger_platform.schema import CHASSIS_POLICIES

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEWEST_FIRST: Final = (
    "idempotency_keys",
    "inbox",
    "outbox",
    "emission_points",
    "branches",
    "memberships",
    "companies",
    "organizations",
)


def _apply(statements: tuple[str, ...]) -> None:
    for statement in statements:
        op.execute(statement)


def _moment(name: str) -> sa.Column[datetime]:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False)


def _of_company(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["organization_id", "company_id"],
        ["companies.organization_id", "companies.id"],
        name=f"fk_{table}_company",
    )


def _chassis() -> None:
    """The platform's tables, as ``ledger_platform`` declares them, and their row-level security."""
    op.create_table(
        "outbox",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("subject", sa.String(), nullable=False),
        sa.Column("headers", JSONB(), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        _moment("created_at"),
        sa.Column("stored_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "next_attempt_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_outbox_due", "outbox", [sa.text("(attempts > 0)"), "created_at", "id", "next_attempt_at"]
    )
    op.create_table(
        "inbox",
        sa.Column("consumer", sa.String(), primary_key=True),
        sa.Column("event_id", sa.Uuid(), primary_key=True),
        _moment("processed_at"),
    )
    op.create_table(
        "idempotency_keys",
        sa.Column("organization_id", sa.Uuid(), primary_key=True),
        sa.Column("subject", sa.String(), primary_key=True),
        sa.Column("key", sa.String(), primary_key=True),
        sa.Column("fingerprint", sa.LargeBinary(), nullable=False),
        sa.Column("status", sa.Integer(), nullable=True),
        sa.Column("headers", JSONB(), nullable=True),
        sa.Column("body", sa.LargeBinary(), nullable=True),
        _moment("expires_at"),
    )
    op.create_index("ix_idempotency_keys_expiry", "idempotency_keys", ["organization_id", "expires_at"])
    _apply(CHASSIS_POLICIES)


def upgrade() -> None:
    """Create the chassis's tables and identity's, and put every tenant's data under row-level security."""
    _chassis()
    organizations = op.create_table(
        "organizations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("provider_id", sa.String(64), nullable=False),
        _moment("created_at"),
    )
    companies = op.create_table(
        "companies",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organizations.id"), nullable=False),
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
    )
    memberships = op.create_table(
        "memberships",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("subject", sa.String(128), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        _moment("changed_at"),
        sa.Column("changed_by", sa.String(128), nullable=False),
        sa.UniqueConstraint("company_id", "subject", name="uq_memberships_subject"),
        _of_company("memberships"),
        sa.CheckConstraint(
            "role IN ('owner', 'admin', 'accountant', 'issuer', 'viewer')", name="ck_memberships_role"
        ),
        sa.CheckConstraint("status IN ('active', 'revoked')", name="ck_memberships_status"),
        sa.CheckConstraint("version > 0", name="ck_memberships_version"),
    )
    op.create_index("ix_memberships_subject", "memberships", ["organization_id", "subject"])
    branches = op.create_table(
        "branches",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(10), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        _moment("created_at"),
        sa.UniqueConstraint("company_id", "code", name="uq_branches_code"),
        sa.UniqueConstraint("company_id", "id", name="uq_branches_company"),
        _of_company("branches"),
    )
    emission_points = op.create_table(
        "emission_points",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("branch_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(10), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        _moment("created_at"),
        sa.UniqueConstraint("branch_id", "code", name="uq_emission_points_code"),
        _of_company("emission_points"),
        sa.ForeignKeyConstraint(
            ["company_id", "branch_id"],
            ["branches.company_id", "branches.id"],
            name="fk_emission_points_branch",
        ),
    )
    _apply(organization_isolation(organizations, column="id"))
    _apply(organization_isolation(companies))
    _apply(organization_isolation(memberships))
    _apply(tenant_isolation(branches))
    _apply(tenant_isolation(emission_points))


def downgrade() -> None:
    """Drop every table this revision created; their policies go with them."""
    for table in _NEWEST_FIRST:
        op.drop_table(table)
```

(The migration spells every type and length out instead of importing the models': a revision is a record of what was done, and must not change when a model does. `test_the_migrated_schema_is_exactly_the_models` keeps the two equal.)

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest services/identity -q --no-cov`
Expected: PASS.

- [ ] **Step 6: Gate**

Run: `uv run poe fmt && uv run poe check`
Expected: every step passes, coverage 100 % (`env.py` and the revision are run by the tests through Alembic, which loads them by path; coverage measures them as files of `ledger_identity`).

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock services/identity
git commit -m "feat(identity): schema, migrations and row-level security, tested up, down and against the models

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: Identity's state in PostgreSQL: repositories, the outbox, the permission source

**Files:**
- Create: `services/identity/src/ledger_identity/adapters/postgres/repositories.py`, `adapters/postgres/unit.py`, `adapters/events.py`, `adapters/permissions.py`
- Modify: `services/identity/pyproject.toml`, `services/identity/tests/conftest.py`
- Test: `services/identity/tests/test_repositories.py`

**Interfaces:**
- Consumes: the ports and use cases (Task 10); the records (Task 11); `Outbox.add(unit, envelope, *, subject)`, `UnitOfWork.connection`, `UnitOfWork.scope`, `Database.transaction(scope)` (Task 4), `StreamSpec`, `Envelope.wrap(message, *, source, tenant, clock)`, `PermissionSource` (Task 6); the wire messages `ledger.events.identity.v1` (Task 7); `page_of` (Task 1); `eventually`, `waits_for_a_lock` (Task 2).
- Produces: `ledger_identity.adapters.postgres.repositories`: `PgOrganizations`, `PgCompanies`, `PgBranches`, `PgEmissionPoints`, `PgMemberships` (each `(connection: AsyncConnection, organization: OrganizationId)`, implementing its port and naming `organization` in every read, update and upsert). `ledger_identity.adapters.postgres.unit.bind(unit: UnitOfWork, outbox: Outbox, *, clock: Clock = utc_now) -> IdentityUnit` (binds the repositories to the organization of `unit.scope`, `UnscopedUnitError` without one; reads `clock` once: the unit's `now`, and the time of the events it announces). `ledger_identity.adapters.events`: `IDENTITY = StreamSpec(name="IDENTITY", subjects=("identity.>",))`, `SOURCE = "identity"`, `outgoing(event: IdentityEvent) -> tuple[Message, str]` (payload and subject: `identity.company.created|updated|deactivated`, `identity.membership.granted|revoked`), `OutboxEvents(unit: UnitOfWork, outbox: Outbox, *, clock: Clock = utc_now)` (the `Events` port). `ledger_identity.adapters.permissions.MembershipPermissions(database: Database)` (a `PermissionSource` for identity's own routes: the role's permissions of an active membership, in an inactive company too, Decision 25). Test fixture `outbox` (an `Outbox` with its own tracer and registry).

Re-check before starting: Milestone 1's final review renames the colliding `Clock`/`Sleep` aliases of the kernel and the platform; use the wall-clock alias `ledger_kernel.clock` defines then (written `Clock` below), and `Envelope.wrap`'s `clock` parameter as it stands. It also stops `repr(TaxId)` and `InvalidTaxIdError` from showing the digits; `str(tax_id)`, which the repositories store and the wire carries, must still be the digits (check, and use the accessor the fix wave names if `str` changed).

- [ ] **Step 1: Dependencies**

`services/identity/pyproject.toml` — `dependencies` gain the contracts and protobuf, `[tool.uv.sources]` the contracts, and deptry learns that the generated `ledger` package is the contracts':

```toml
dependencies = [
    "ledger-kernel",
    "ledger-contracts",
    "ledger-platform",
    "alembic>=1.20.0",
    "protobuf>=7.36.2",
    "pydantic>=2.14.1",
    "sqlalchemy>=2.1.4",
]
```

```toml
[tool.uv.sources]
ledger-kernel = { workspace = true }
ledger-contracts = { workspace = true }
ledger-platform = { workspace = true }

[tool.deptry.package_module_name_map]
"ledger-contracts" = ["ledger_contracts", "ledger"]
```

Run: `uv lock && uv sync`
Expected: no change of versions; the lock records the new edges.

- [ ] **Step 2: Write the failing tests**

Append to `services/identity/tests/conftest.py` (imports merged at the top):

```python
from opentelemetry.sdk.trace import TracerProvider
from prometheus_client import CollectorRegistry

from ledger_platform.outbox import Outbox
from ledger_platform.tracing import Tracing


@pytest.fixture
def outbox() -> Outbox:
    """An outbox of the test's own: its own tracer provider and metrics registry."""
    return Outbox(tracing=Tracing(TracerProvider()), registry=CollectorRegistry())
```

`services/identity/tests/test_repositories.py`:

```python
import asyncio
from datetime import UTC, datetime
from typing import Final
lazy from collections.abc import Awaitable, Callable

import psycopg
import pytest
from sqlalchemy import func, insert, select, text
from sqlalchemy.exc import IntegrityError
lazy from sqlalchemy import Insert

from ledger.events.identity.v1 import events_pb2 as wire
from ledger_contracts.envelope import Envelope
from ledger_identity.adapters.events import IDENTITY, SOURCE, outgoing
from ledger_identity.adapters.permissions import MembershipPermissions
from ledger_identity.adapters.postgres.tables import (
    BranchRecord,
    CompanyRecord,
    EmissionPointRecord,
    MembershipRecord,
)
from ledger_identity.adapters.postgres.unit import UnscopedUnitError, bind
from ledger_identity.application import commands, queries
from ledger_identity.domain.errors import (
    BranchCodeTakenError,
    CompanyNotFoundError,
    EmissionPointCodeTakenError,
    OwnersOnlyError,
    TaxIdTakenError,
)
from ledger_identity.domain.events import (
    CompanyCreated,
    CompanyDeactivated,
    CompanyUpdated,
    MembershipGranted,
    MembershipRevoked,
)
from ledger_identity.domain.roles import READ_ONLY, Role
from ledger_identity.domain.text import LegalName, parse_code, parse_legal_name, parse_name
from ledger_kernel.access import Permission, Principal, SubjectId
from ledger_kernel.countries.do import TaxId
from ledger_kernel.ids import CompanyId, OrganizationId, Tenant, new_id
from ledger_kernel.pagination import PageRequest
from ledger_platform.authentication import organization_of
from ledger_platform.outbox import OutboxRecord
from ledger_testing.waiting import eventually, waits_for_a_lock
lazy from ledger_identity.application.ports import IdentityUnit
lazy from ledger_identity.domain.company import Company
lazy from ledger_identity.domain.events import IdentityEvent
lazy from ledger_platform.database import Database, Scope
lazy from ledger_platform.outbox import Outbox

NOW: Final = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)
FOUNDER: Final = Principal(
    subject=SubjectId("2001"), organization=organization_of("1001"), provider_organization="1001"
)
PARTNER: Final = Principal(
    subject=SubjectId("2002"), organization=FOUNDER.organization, provider_organization="1001"
)
STRANGER: Final = Principal(
    subject=SubjectId("2001"), organization=organization_of("1002"), provider_organization="1002"
)
OWNER: Final = FOUNDER.subject
MEMBER: Final = Principal(
    subject=SubjectId("3001"), organization=FOUNDER.organization, provider_organization="1001"
)
RNC: Final = TaxId.parse("101850043")
ROW_LEVEL_SECURITY_OFF: Final = tuple(
    text(statement)
    for statement in (
        "ALTER TABLE organizations DISABLE ROW LEVEL SECURITY",
        "ALTER TABLE companies DISABLE ROW LEVEL SECURITY",
        "ALTER TABLE memberships DISABLE ROW LEVEL SECURITY",
        "ALTER TABLE branches DISABLE ROW LEVEL SECURITY",
        "ALTER TABLE emission_points DISABLE ROW LEVEL SECURITY",
    )
)
"""What a dropped policy would leave: run inside a transaction that rolls back (DDL is transactional)."""


class UndoneError(Exception):
    """Raised on purpose, so that the transaction it leaves rolls back everything it did."""


def anything(version: int) -> bool:
    del version
    return True


async def in_unit[T](
    database: Database, outbox: Outbox, scope: Scope, work: Callable[[IdentityUnit], Awaitable[T]]
) -> T:
    async with database.transaction(scope) as transaction:
        return await work(bind(transaction, outbox, clock=lambda: NOW))


async def create(
    database: Database, outbox: Outbox, founder: Principal = FOUNDER, tax_id: TaxId = RNC
) -> Company:
    return await in_unit(
        database,
        outbox,
        founder.organization,
        lambda unit: commands.create_company(
            unit, founder, tax_id=tax_id, legal_name=parse_legal_name("Duarte")
        ),
    )


async def write(database: Database, scope: Scope, statement: Insert) -> None:
    async with database.transaction(scope) as unit:
        await unit.connection.execute(statement)


async def published(database: Database) -> list[Envelope]:
    async with database.transaction(None) as unit:
        rows = await unit.connection.execute(
            select(OutboxRecord.headers, OutboxRecord.data).order_by(OutboxRecord.created_at, OutboxRecord.id)
        )
        return [Envelope.from_headers(headers, data) for headers, data in rows]


# ── Companies ───────────────────────────────────────────────────────────────────


async def test_a_company_is_kept_with_its_owner_and_organization(database: Database, outbox: Outbox) -> None:
    company = await create(database, outbox)
    tenant = company.tenant
    read = await in_unit(database, outbox, tenant, lambda unit: queries.company(unit, company.id))
    assert read == company
    organization = await in_unit(
        database, outbox, tenant, lambda unit: queries.organization(unit, tenant.organization)
    )
    assert (organization.id, organization.provider_id) == (FOUNDER.organization, "1001")
    permissions = await in_unit(
        database, outbox, tenant, lambda unit: queries.permissions_of(unit, company.id, OWNER)
    )
    assert permissions == frozenset(Permission)


async def test_what_a_unit_changes_is_announced_through_the_outbox_when_it_commits(
    database: Database, outbox: Outbox
) -> None:
    company = await create(database, outbox)
    created, granted = await published(database)
    assert (created.type, created.source, created.tenant) == (
        "ledger.events.identity.v1.CompanyCreated",
        SOURCE,
        company.tenant,
    )
    payload = created.unwrap(wire.CompanyCreated)
    assert (
        payload.company_id,
        payload.organization_id,
        payload.tax_id,
        payload.legal_name,
        payload.version,
    ) == (
        str(company.id),
        str(company.organization),
        "101850043",
        "Duarte",
        1,
    )
    assert granted.unwrap(wire.MembershipGranted).role == wire.ROLE_OWNER


async def test_a_unit_that_fails_keeps_and_announces_nothing(database: Database, outbox: Outbox) -> None:
    async def create_then_fail(unit: IdentityUnit) -> None:
        await commands.create_company(unit, FOUNDER, tax_id=RNC, legal_name=parse_legal_name("Duarte"))
        raise ZeroDivisionError

    with pytest.raises(ZeroDivisionError):
        await in_unit(database, outbox, FOUNDER.organization, create_then_fail)
    async with database.transaction(FOUNDER.organization) as unit:  # the scope that would see the company
        companies = (
            await unit.connection.execute(select(func.count()).select_from(CompanyRecord))
        ).scalar_one()
    assert (companies, await published(database)) == (0, [])


async def test_a_tax_id_is_unique_within_an_organization_only(database: Database, outbox: Outbox) -> None:
    await create(database, outbox)
    await create(database, outbox, founder=STRANGER)
    with pytest.raises(TaxIdTakenError):
        await create(database, outbox)


async def test_another_organization_sees_none_of_a_company(database: Database, outbox: Outbox) -> None:
    company = await create(database, outbox)
    with pytest.raises(CompanyNotFoundError):
        await in_unit(database, outbox, STRANGER.organization, lambda unit: queries.company(unit, company.id))
    stranger_tenant = Tenant(STRANGER.organization, company.id)
    with pytest.raises(CompanyNotFoundError):
        await in_unit(database, outbox, stranger_tenant, lambda unit: queries.company(unit, company.id))
    assert await MembershipPermissions(database).permissions(STRANGER, company.id) == frozenset()


async def test_a_change_applies_to_the_version_it_was_made_from_only(
    database: Database, outbox: Outbox
) -> None:
    company = await create(database, outbox)
    renamed, _ = company.rename(parse_legal_name("Duarte Hermanos"), now=NOW)
    assert await in_unit(
        database, outbox, company.tenant, lambda unit: unit.companies.update(renamed, version=1)
    )
    again, _ = company.rename(parse_legal_name("Duarte e Hijos"), now=NOW)
    assert not await in_unit(
        database, outbox, company.tenant, lambda unit: unit.companies.update(again, version=1)
    )
    assert (
        await in_unit(database, outbox, company.tenant, lambda unit: queries.company(unit, company.id))
    ).legal_name == ("Duarte Hermanos")


async def test_renames_and_deactivations_are_announced(database: Database, outbox: Outbox) -> None:
    company = await create(database, outbox)
    await in_unit(
        database,
        outbox,
        company.tenant,
        lambda unit: commands.rename_company(
            unit, company.id, legal_name=parse_legal_name("Duarte S.A."), admits=anything
        ),
    )
    await in_unit(
        database,
        outbox,
        company.tenant,
        lambda unit: commands.deactivate_company(unit, company.id, admits=anything),
    )
    types = [envelope.type.rsplit(".", 1)[1] for envelope in await published(database)]
    assert types == ["CompanyCreated", "MembershipGranted", "CompanyUpdated", "CompanyDeactivated"]


async def test_a_principal_pages_through_the_companies_it_holds_a_role_in(
    database: Database, outbox: Outbox
) -> None:
    for rnc in ("101850043", "130000012", "401007551"):
        await create(database, outbox, tax_id=TaxId.parse(rnc))
    await create(database, outbox, founder=STRANGER)
    first = await in_unit(
        database,
        outbox,
        FOUNDER.organization,
        lambda unit: queries.companies_of(unit, OWNER, PageRequest(size=2)),
    )
    assert len(first.items) == 2
    assert first.next_cursor is not None
    after = first.items[-1].id
    rest = await in_unit(
        database,
        outbox,
        FOUNDER.organization,
        lambda unit: queries.companies_of(unit, OWNER, PageRequest(size=2, after=after)),
    )
    assert (len(rest.items), rest.next_cursor) == (1, None)
    assert all(company.organization == FOUNDER.organization for company in (*first.items, *rest.items))
    partner = await in_unit(
        database,
        outbox,
        FOUNDER.organization,
        lambda unit: queries.companies_of(unit, PARTNER.subject, PageRequest()),
    )
    assert partner.items == ()


# ── Branches and emission points ───────────────────────────────────────────────


async def test_branches_and_emission_points_are_kept_per_company(database: Database, outbox: Outbox) -> None:
    company = await create(database, outbox)
    other = await create(database, outbox, tax_id=TaxId.parse("130000012"))
    branch = await in_unit(
        database,
        outbox,
        company.tenant,
        lambda unit: commands.open_branch(
            unit, company.id, code=parse_code("SUC1"), name=parse_name("Santiago")
        ),
    )
    await in_unit(
        database,
        outbox,
        other.tenant,
        lambda unit: commands.open_branch(unit, other.id, code=parse_code("SUC1"), name=parse_name("Bonao")),
    )
    with pytest.raises(BranchCodeTakenError):
        await in_unit(
            database,
            outbox,
            company.tenant,
            lambda unit: commands.open_branch(
                unit, company.id, code=parse_code("SUC1"), name=parse_name("X")
            ),
        )
    point = await in_unit(
        database,
        outbox,
        company.tenant,
        lambda unit: commands.open_emission_point(
            unit, company.id, branch.id, code=parse_code("001"), name=parse_name("Caja")
        ),
    )
    with pytest.raises(EmissionPointCodeTakenError):
        await in_unit(
            database,
            outbox,
            company.tenant,
            lambda unit: commands.open_emission_point(
                unit, company.id, branch.id, code=parse_code("001"), name=parse_name("Y")
            ),
        )
    assert (
        await in_unit(
            database, outbox, company.tenant, lambda unit: queries.branch(unit, company.id, branch.id)
        )
        == branch
    )
    assert (
        await in_unit(
            database,
            outbox,
            company.tenant,
            lambda unit: queries.emission_point(unit, company.id, branch.id, point.id),
        )
        == point
    )
    branches = await in_unit(
        database, outbox, company.tenant, lambda unit: queries.branches(unit, company.id, PageRequest())
    )
    points = await in_unit(
        database,
        outbox,
        company.tenant,
        lambda unit: queries.emission_points(unit, company.id, branch.id, PageRequest()),
    )
    assert (branches.items, points.items) == ((branch,), (point,))


async def test_a_company_scope_sees_no_branch_of_another_company(database: Database, outbox: Outbox) -> None:
    company = await create(database, outbox)
    other = await create(database, outbox, tax_id=TaxId.parse("130000012"))
    branch = await in_unit(
        database,
        outbox,
        company.tenant,
        lambda unit: commands.open_branch(
            unit, company.id, code=parse_code("SUC1"), name=parse_name("Santiago")
        ),
    )
    seen_by_other = await in_unit(
        database, outbox, other.tenant, lambda unit: unit.branches.page(company.id, PageRequest())
    )
    asked_wrongly = await in_unit(
        database, outbox, company.tenant, lambda unit: unit.branches.get(other.id, branch.id)
    )
    assert (seen_by_other.items, asked_wrongly) == ((), None)


# ── Tenancy checked twice ───────────────────────────────────────────────────────


async def test_a_row_cannot_point_at_a_company_or_a_branch_of_another_tenant(
    database: Database, outbox: Outbox
) -> None:
    mine = await create(database, outbox)
    sister = await create(database, outbox, tax_id=TaxId.parse("130000012"))
    theirs = await create(database, outbox, founder=STRANGER)
    branch = await in_unit(
        database,
        outbox,
        sister.tenant,
        lambda unit: commands.open_branch(unit, sister.id, code=parse_code("SUC1"), name=parse_name("Bonao")),
    )
    member_of_theirs = insert(MembershipRecord).values(
        id=new_id(),
        organization_id=mine.organization,
        company_id=theirs.id,
        subject="3001",
        role="viewer",
        status="active",
        version=1,
        changed_at=NOW,
        changed_by="2001",
    )
    branch_of_theirs = insert(BranchRecord).values(
        id=new_id(),
        organization_id=mine.organization,
        company_id=theirs.id,
        code="SUC9",
        name="X",
        created_at=NOW,
    )
    point_in_the_sister_s_branch = insert(EmissionPointRecord).values(
        id=new_id(),
        organization_id=mine.organization,
        company_id=mine.id,
        branch_id=branch.id,
        code="001",
        name="Caja",
        created_at=NOW,
    )
    attempts: tuple[tuple[Scope, Insert], ...] = (
        (mine.organization, member_of_theirs),  # row-level security lets each of these through
        (Tenant(mine.organization, theirs.id), branch_of_theirs),
        (mine.tenant, point_in_the_sister_s_branch),
    )
    for scope, statement in attempts:
        with pytest.raises(IntegrityError) as caught:
            await write(database, scope, statement)
        assert isinstance(caught.value.orig, psycopg.errors.ForeignKeyViolation)


async def test_the_repositories_keep_to_the_unit_s_organization_without_row_level_security(
    database: Database, outbox: Outbox
) -> None:
    company = await create(database, outbox)
    branch = await in_unit(
        database,
        outbox,
        company.tenant,
        lambda unit: commands.open_branch(unit, company.id, code=parse_code("SUC1"), name=parse_name("S")),
    )
    point = await in_unit(
        database,
        outbox,
        company.tenant,
        lambda unit: commands.open_emission_point(
            unit, company.id, branch.id, code=parse_code("001"), name=parse_name("Caja")
        ),
    )
    seen: list[object] = []

    async def look_as_a_stranger_with_the_policies_off() -> None:
        async with database.transaction(STRANGER.organization) as transaction:
            for statement in ROW_LEVEL_SECURITY_OFF:
                await transaction.connection.execute(statement)
            unit = bind(transaction, outbox, clock=lambda: NOW)
            every_company = select(func.count()).select_from(CompanyRecord)
            seen.extend(
                (
                    (await transaction.connection.execute(every_company)).scalar_one(),
                    await unit.organizations.get(FOUNDER.organization),
                    await unit.companies.get(company.id),
                    await unit.companies.locked(company.id),
                    await unit.companies.update(company, version=company.version),
                    (await unit.companies.of_member(OWNER, PageRequest())).items,
                    await unit.memberships.get(company.id, OWNER),
                    await unit.memberships.owners(company.id),
                    (await unit.memberships.page(company.id, PageRequest())).items,
                    await unit.branches.get(company.id, branch.id),
                    (await unit.branches.page(company.id, PageRequest())).items,
                    await unit.emission_points.get(company.id, point.id),
                    (await unit.emission_points.page(company.id, branch.id, PageRequest())).items,
                )
            )
            raise UndoneError  # the policies come back with the rollback

    with pytest.raises(UndoneError):
        await look_as_a_stranger_with_the_policies_off()
    assert seen == [1, None, None, None, False, (), None, 0, (), None, (), None, ()]


async def test_identity_s_state_is_never_read_without_a_scope(database: Database, outbox: Outbox) -> None:
    async with database.transaction(None) as transaction:
        with pytest.raises(UnscopedUnitError) as caught:
            bind(transaction, outbox)
    assert caught.value.code == "unscoped_unit"


# ── Memberships ─────────────────────────────────────────────────────────────────


async def test_roles_are_granted_counted_listed_revoked_and_granted_again(
    database: Database, outbox: Outbox
) -> None:
    company = await create(database, outbox)
    tenant = company.tenant

    async def grant(role: Role) -> commands.Granted:
        return await in_unit(
            database,
            outbox,
            tenant,
            lambda unit: commands.grant_role(
                unit, company.id, subject=MEMBER.subject, role=role, actor=OWNER
            ),
        )

    granted = await grant(Role.VIEWER)
    assert granted.created
    changed = await grant(Role.ISSUER)
    assert (changed.created, changed.membership.version) == (False, 2)
    listed = await in_unit(
        database, outbox, tenant, lambda unit: queries.memberships(unit, company.id, PageRequest(size=1))
    )
    assert listed.next_cursor is not None
    assert await in_unit(database, outbox, tenant, lambda unit: unit.memberships.owners(company.id)) == 1
    await in_unit(
        database,
        outbox,
        tenant,
        lambda unit: commands.revoke_role(unit, company.id, subject=MEMBER.subject, actor=OWNER),
    )
    revoked = await in_unit(
        database, outbox, tenant, lambda unit: unit.memberships.get(company.id, MEMBER.subject)
    )
    assert revoked is not None
    assert (revoked.active, revoked.version, revoked.changed_by) == (False, 3, OWNER)
    assert await MembershipPermissions(database).permissions(MEMBER, company.id) == frozenset()
    remaining = await in_unit(
        database, outbox, tenant, lambda unit: queries.memberships(unit, company.id, PageRequest())
    )
    assert [membership.subject for membership in remaining.items] == [OWNER]
    again = await grant(Role.VIEWER)
    assert (again.created, again.membership.version, again.membership.id) == (True, 4, granted.membership.id)
    types = [envelope.type.rsplit(".", 1)[1] for envelope in await published(database)]
    assert types[-4:] == ["MembershipGranted", "MembershipGranted", "MembershipRevoked", "MembershipGranted"]


async def test_two_owners_revoking_each_other_at_once_leave_one(
    database: Database, database_url: str, outbox: Outbox
) -> None:
    company = await create(database, outbox)
    tenant = company.tenant
    await in_unit(
        database,
        outbox,
        tenant,
        lambda unit: commands.grant_role(
            unit, company.id, subject=PARTNER.subject, role=Role.OWNER, actor=OWNER
        ),
    )
    holding, release = asyncio.Event(), asyncio.Event()

    async def first(unit: IdentityUnit) -> None:
        await unit.companies.locked(company.id)  # as the revocation does, before the other one can
        holding.set()
        await release.wait()
        await commands.revoke_role(unit, company.id, subject=PARTNER.subject, actor=OWNER)

    async def second(unit: IdentityUnit) -> None:
        await commands.revoke_role(unit, company.id, subject=OWNER, actor=PARTNER.subject)

    winner = asyncio.create_task(in_unit(database, outbox, tenant, first))
    await holding.wait()
    loser = asyncio.create_task(in_unit(database, outbox, tenant, second))
    await eventually(lambda: waits_for_a_lock(database_url))  # the second waits for the company
    assert not loser.done()
    release.set()
    await winner
    with pytest.raises(CompanyNotFoundError):  # its own role went first: it acts in the company no more
        await loser
    assert await in_unit(database, outbox, tenant, lambda unit: unit.memberships.owners(company.id)) == 1


async def test_an_owner_demoted_meanwhile_grants_with_the_role_it_holds_now(
    database: Database, database_url: str, outbox: Outbox
) -> None:
    company = await create(database, outbox)
    tenant = company.tenant
    await in_unit(
        database,
        outbox,
        tenant,
        lambda unit: commands.grant_role(
            unit, company.id, subject=PARTNER.subject, role=Role.OWNER, actor=OWNER
        ),
    )
    holding, release = asyncio.Event(), asyncio.Event()

    async def demote(unit: IdentityUnit) -> None:
        await unit.companies.locked(company.id)
        holding.set()
        await release.wait()
        await commands.grant_role(unit, company.id, subject=PARTNER.subject, role=Role.ADMIN, actor=OWNER)

    async def promote(unit: IdentityUnit) -> None:
        await commands.grant_role(
            unit, company.id, subject=MEMBER.subject, role=Role.OWNER, actor=PARTNER.subject
        )

    demotion = asyncio.create_task(in_unit(database, outbox, tenant, demote))
    await holding.wait()
    promotion = asyncio.create_task(in_unit(database, outbox, tenant, promote))
    await eventually(lambda: waits_for_a_lock(database_url))  # the promotion waits for the company
    release.set()
    await demotion
    with pytest.raises(OwnersOnlyError):  # checked by the route as an owner, decided as the admin it now is
        await promotion
    assert await in_unit(database, outbox, tenant, lambda unit: unit.memberships.owners(company.id)) == 1


async def test_permissions_come_from_the_membership_in_the_callers_organization(
    database: Database, outbox: Outbox
) -> None:
    company = await create(database, outbox)
    permissions = MembershipPermissions(database)
    assert await permissions.permissions(FOUNDER, company.id) == frozenset(Permission)
    assert await permissions.permissions(PARTNER, company.id) == frozenset()
    assert await permissions.permissions(FOUNDER, CompanyId(new_id())) == frozenset()


async def test_a_deactivated_company_lets_other_services_read_only_and_identity_still_revoke(
    database: Database, outbox: Outbox
) -> None:
    company = await create(database, outbox)
    await in_unit(
        database,
        outbox,
        company.tenant,
        lambda unit: commands.deactivate_company(unit, company.id, admits=anything),
    )
    for_other_services = await in_unit(
        database, outbox, FOUNDER.organization, lambda unit: queries.permissions_of(unit, company.id, OWNER)
    )
    assert for_other_services == READ_ONLY
    assert await MembershipPermissions(database).permissions(FOUNDER, company.id) == frozenset(Permission)


# ── The wire ────────────────────────────────────────────────────────────────────

TENANT: Final = Tenant(OrganizationId(new_id()), CompanyId(new_id()))


@pytest.mark.parametrize(
    ("event", "subject"),
    [
        (
            CompanyCreated(tenant=TENANT, tax_id=RNC, legal_name=LegalName("Duarte"), version=1),
            "identity.company.created",
        ),
        (
            CompanyUpdated(tenant=TENANT, tax_id=RNC, legal_name=LegalName("Duarte II"), version=2),
            "identity.company.updated",
        ),
        (CompanyDeactivated(tenant=TENANT, version=3), "identity.company.deactivated"),
        (
            MembershipGranted(
                tenant=TENANT, subject=SubjectId("3001"), role=Role.ISSUER, previous=Role.VIEWER, version=2
            ),
            "identity.membership.granted",
        ),
        (
            MembershipRevoked(tenant=TENANT, subject=SubjectId("3001"), role=Role.ISSUER, version=3),
            "identity.membership.revoked",
        ),
    ],
    ids=["created", "updated", "deactivated", "granted", "revoked"],
)
def test_every_event_has_its_subject_in_the_identity_stream(event: IdentityEvent, subject: str) -> None:
    message, published_on = outgoing(event)
    assert published_on == subject
    assert published_on.startswith("identity.")
    assert IDENTITY.subjects == ("identity.>",)
    assert message.DESCRIPTOR.full_name == f"ledger.events.identity.v1.{type(event).__name__}"


def test_the_wire_says_what_the_domain_says() -> None:
    granted, _ = outgoing(
        MembershipGranted(tenant=TENANT, subject=SubjectId("3001"), role=Role.OWNER, previous=None, version=1)
    )
    assert isinstance(granted, wire.MembershipGranted)
    assert (
        granted.company_id,
        granted.organization_id,
        granted.subject,
        granted.role,
        granted.previous_role,
        granted.version,
    ) == (
        str(TENANT.company),
        str(TENANT.organization),
        "3001",
        wire.ROLE_OWNER,
        wire.ROLE_UNSPECIFIED,
        1,
    )
    revoked, _ = outgoing(
        MembershipRevoked(tenant=TENANT, subject=SubjectId("3001"), role=Role.ACCOUNTANT, version=4)
    )
    assert isinstance(revoked, wire.MembershipRevoked)
    assert revoked.role == wire.ROLE_ACCOUNTANT
    deactivated, _ = outgoing(CompanyDeactivated(tenant=TENANT, version=5))
    assert isinstance(deactivated, wire.CompanyDeactivated)
    assert deactivated.version == 5
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest services/identity/tests/test_repositories.py -q --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'ledger_identity.adapters.events'`.

- [ ] **Step 4: Implement**

`services/identity/src/ledger_identity/adapters/postgres/repositories.py`:

```python
"""identity's repositories: the ports of ``application.ports`` over one transaction (SQLAlchemy Core).

Every repository is bound to the organization of its unit and names it in every read, update and upsert, with
the company where a row is a company's; row-level security enforces the unit's scope again. Tenancy is checked
twice (§2): a statement that forgot its filter still sees nothing of another tenant, and a policy dropped by
mistake still leaves every read and change inside the organization. An insert that could collide uses ``ON
CONFLICT DO NOTHING`` against the constraint that would refuse it, and reads the outcome from ``RETURNING``:
a taken tax id or code is an answer, not an exception to parse.
"""

from typing import final, override
lazy from datetime import datetime
lazy from uuid import UUID

from sqlalchemy import and_, func, select, update
from sqlalchemy.dialects.postgresql import insert
lazy from sqlalchemy import Row, Select
lazy from sqlalchemy.ext.asyncio import AsyncConnection
lazy from sqlalchemy.orm import InstrumentedAttribute

from ledger_identity.adapters.postgres.tables import (
    BranchRecord,
    CompanyRecord,
    EmissionPointRecord,
    MembershipRecord,
    OrganizationRecord,
)
from ledger_identity.application.ports import Branches, Companies, EmissionPoints, Memberships, Organizations
from ledger_identity.domain.branches import Branch, EmissionPoint
from ledger_identity.domain.company import Company, CompanyStatus
from ledger_identity.domain.ids import BranchId, EmissionPointId, MembershipId
from ledger_identity.domain.membership import Membership, MembershipStatus
from ledger_identity.domain.organization import Organization
from ledger_identity.domain.roles import Role
from ledger_identity.domain.text import Code, LegalName, Name
from ledger_kernel.access import SubjectId
from ledger_kernel.countries.do import TaxId
from ledger_kernel.ids import CompanyId, OrganizationId, Tenant
from ledger_kernel.pagination import page_of
lazy from ledger_kernel.pagination import Page, PageRequest

_ORGANIZATION = select(OrganizationRecord.id, OrganizationRecord.provider_id, OrganizationRecord.created_at)
_COMPANY = select(
    CompanyRecord.id,
    CompanyRecord.organization_id,
    CompanyRecord.tax_id,
    CompanyRecord.legal_name,
    CompanyRecord.status,
    CompanyRecord.version,
    CompanyRecord.created_at,
    CompanyRecord.updated_at,
)
_BRANCH = select(
    BranchRecord.id,
    BranchRecord.organization_id,
    BranchRecord.company_id,
    BranchRecord.code,
    BranchRecord.name,
    BranchRecord.created_at,
)
_POINT = select(
    EmissionPointRecord.id,
    EmissionPointRecord.organization_id,
    EmissionPointRecord.company_id,
    EmissionPointRecord.branch_id,
    EmissionPointRecord.code,
    EmissionPointRecord.name,
    EmissionPointRecord.created_at,
)
_MEMBERSHIP = select(
    MembershipRecord.id,
    MembershipRecord.organization_id,
    MembershipRecord.company_id,
    MembershipRecord.subject,
    MembershipRecord.role,
    MembershipRecord.status,
    MembershipRecord.version,
    MembershipRecord.changed_at,
    MembershipRecord.changed_by,
)
_ACTIVE = MembershipRecord.status == MembershipStatus.ACTIVE.value


def _paged[*Columns](
    statement: Select[*Columns], key: InstrumentedAttribute[UUID], request: PageRequest
) -> Select[*Columns]:
    """``statement`` in ``key`` order, after the request's cursor, one row more than the page holds."""
    after = statement if request.after is None else statement.where(key > request.after)
    return after.order_by(key).limit(request.size + 1)


def _organization(row: Row[UUID, str, datetime]) -> Organization:
    identifier, provider_id, created_at = row
    return Organization(id=OrganizationId(identifier), provider_id=provider_id, created_at=created_at)


def _company(row: Row[UUID, UUID, str, str, str, int, datetime, datetime]) -> Company:
    identifier, organization, tax_id, legal_name, status, version, created_at, updated_at = row
    return Company(
        id=CompanyId(identifier),
        organization=OrganizationId(organization),
        tax_id=TaxId.parse(tax_id),
        legal_name=LegalName(legal_name),
        status=CompanyStatus(status),
        version=version,
        created_at=created_at,
        updated_at=updated_at,
    )


def _branch(row: Row[UUID, UUID, UUID, str, str, datetime]) -> Branch:
    identifier, organization, company, code, name, created_at = row
    tenant = Tenant(OrganizationId(organization), CompanyId(company))
    return Branch(
        id=BranchId(identifier), tenant=tenant, code=Code(code), name=Name(name), created_at=created_at
    )


def _point(row: Row[UUID, UUID, UUID, UUID, str, str, datetime]) -> EmissionPoint:
    identifier, organization, company, branch, code, name, created_at = row
    return EmissionPoint(
        id=EmissionPointId(identifier),
        tenant=Tenant(OrganizationId(organization), CompanyId(company)),
        branch=BranchId(branch),
        code=Code(code),
        name=Name(name),
        created_at=created_at,
    )


def _membership(row: Row[UUID, UUID, UUID, str, str, str, int, datetime, str]) -> Membership:
    identifier, organization, company, subject, role, status, version, changed_at, changed_by = row
    return Membership(
        id=MembershipId(identifier),
        tenant=Tenant(OrganizationId(organization), CompanyId(company)),
        subject=SubjectId(subject),
        role=Role(role),
        status=MembershipStatus(status),
        version=version,
        changed_at=changed_at,
        changed_by=SubjectId(changed_by),
    )


@final
class PgOrganizations(Organizations):
    """The organizations identity has seen, in PostgreSQL: the unit's own."""

    __slots__ = ("_connection", "_organization")

    def __init__(self, connection: AsyncConnection, organization: OrganizationId) -> None:
        self._connection = connection
        self._organization = organization

    @override
    async def add(self, organization: Organization) -> None:
        await self._connection.execute(
            insert(OrganizationRecord)
            .values(
                id=organization.id, provider_id=organization.provider_id, created_at=organization.created_at
            )
            .on_conflict_do_nothing(index_elements=[OrganizationRecord.id])
        )

    @override
    async def get(self, organization: OrganizationId) -> Organization | None:
        mine = _ORGANIZATION.where(
            OrganizationRecord.id == organization, OrganizationRecord.id == self._organization
        )
        row = (await self._connection.execute(mine)).first()
        return None if row is None else _organization(row)


@final
class PgCompanies(Companies):
    """The companies of the unit's organization, in PostgreSQL."""

    __slots__ = ("_connection", "_organization")

    def __init__(self, connection: AsyncConnection, organization: OrganizationId) -> None:
        self._connection = connection
        self._organization = organization

    @override
    async def add(self, company: Company) -> bool:
        added = await self._connection.execute(
            insert(CompanyRecord)
            .values(
                id=company.id,
                organization_id=company.organization,
                tax_id=str(company.tax_id),
                legal_name=company.legal_name,
                status=company.status.value,
                version=company.version,
                created_at=company.created_at,
                updated_at=company.updated_at,
            )
            .on_conflict_do_nothing(constraint="uq_companies_tax_id")
            .returning(CompanyRecord.id)
        )
        return added.first() is not None

    @override
    async def get(self, company: CompanyId) -> Company | None:
        row = (await self._connection.execute(self._one(company))).first()
        return None if row is None else _company(row)

    @override
    async def locked(self, company: CompanyId) -> Company | None:
        row = (await self._connection.execute(self._one(company).with_for_update())).first()
        return None if row is None else _company(row)

    @override
    async def update(self, company: Company, *, version: int) -> bool:
        changed = await self._connection.execute(
            update(CompanyRecord)
            .where(
                CompanyRecord.organization_id == self._organization,
                CompanyRecord.id == company.id,
                CompanyRecord.version == version,
            )
            .values(
                legal_name=company.legal_name,
                status=company.status.value,
                version=company.version,
                updated_at=company.updated_at,
            )
        )
        return changed.rowcount == 1

    @override
    async def of_member(self, subject: SubjectId, request: PageRequest) -> Page[Company]:
        membership = and_(
            MembershipRecord.organization_id == CompanyRecord.organization_id,
            MembershipRecord.company_id == CompanyRecord.id,
        )
        statement = _COMPANY.join(MembershipRecord, membership).where(
            CompanyRecord.organization_id == self._organization, MembershipRecord.subject == subject, _ACTIVE
        )
        rows = (await self._connection.execute(_paged(statement, CompanyRecord.id, request))).all()
        return page_of([_company(row) for row in rows], request, key=lambda company: company.id)

    def _one(self, company: CompanyId) -> Select[UUID, UUID, str, str, str, int, datetime, datetime]:
        return _COMPANY.where(
            CompanyRecord.organization_id == self._organization, CompanyRecord.id == company
        )


@final
class PgBranches(Branches):
    """The branches of the unit's company, in PostgreSQL."""

    __slots__ = ("_connection", "_organization")

    def __init__(self, connection: AsyncConnection, organization: OrganizationId) -> None:
        self._connection = connection
        self._organization = organization

    @override
    async def add(self, branch: Branch) -> bool:
        added = await self._connection.execute(
            insert(BranchRecord)
            .values(
                id=branch.id,
                organization_id=branch.tenant.organization,
                company_id=branch.tenant.company,
                code=branch.code,
                name=branch.name,
                created_at=branch.created_at,
            )
            .on_conflict_do_nothing(constraint="uq_branches_code")
            .returning(BranchRecord.id)
        )
        return added.first() is not None

    @override
    async def get(self, company: CompanyId, branch: BranchId) -> Branch | None:
        row = (await self._connection.execute(self._of(company).where(BranchRecord.id == branch))).first()
        return None if row is None else _branch(row)

    @override
    async def page(self, company: CompanyId, request: PageRequest) -> Page[Branch]:
        rows = (await self._connection.execute(_paged(self._of(company), BranchRecord.id, request))).all()
        return page_of([_branch(row) for row in rows], request, key=lambda branch: branch.id)

    def _of(self, company: CompanyId) -> Select[UUID, UUID, UUID, str, str, datetime]:
        return _BRANCH.where(
            BranchRecord.organization_id == self._organization, BranchRecord.company_id == company
        )


@final
class PgEmissionPoints(EmissionPoints):
    """The emission points of the unit's company, in PostgreSQL."""

    __slots__ = ("_connection", "_organization")

    def __init__(self, connection: AsyncConnection, organization: OrganizationId) -> None:
        self._connection = connection
        self._organization = organization

    @override
    async def add(self, point: EmissionPoint) -> bool:
        added = await self._connection.execute(
            insert(EmissionPointRecord)
            .values(
                id=point.id,
                organization_id=point.tenant.organization,
                company_id=point.tenant.company,
                branch_id=point.branch,
                code=point.code,
                name=point.name,
                created_at=point.created_at,
            )
            .on_conflict_do_nothing(constraint="uq_emission_points_code")
            .returning(EmissionPointRecord.id)
        )
        return added.first() is not None

    @override
    async def get(self, company: CompanyId, point: EmissionPointId) -> EmissionPoint | None:
        statement = self._of(company).where(EmissionPointRecord.id == point)
        row = (await self._connection.execute(statement)).first()
        return None if row is None else _point(row)

    @override
    async def page(self, company: CompanyId, branch: BranchId, request: PageRequest) -> Page[EmissionPoint]:
        mine = self._of(company).where(EmissionPointRecord.branch_id == branch)
        rows = (await self._connection.execute(_paged(mine, EmissionPointRecord.id, request))).all()
        return page_of([_point(row) for row in rows], request, key=lambda point: point.id)

    def _of(self, company: CompanyId) -> Select[UUID, UUID, UUID, UUID, str, str, datetime]:
        return _POINT.where(
            EmissionPointRecord.organization_id == self._organization,
            EmissionPointRecord.company_id == company,
        )


@final
class PgMemberships(Memberships):
    """Who holds, or held, which role in the companies of the unit's organization, in PostgreSQL."""

    __slots__ = ("_connection", "_organization")

    def __init__(self, connection: AsyncConnection, organization: OrganizationId) -> None:
        self._connection = connection
        self._organization = organization

    @override
    async def get(self, company: CompanyId, subject: SubjectId) -> Membership | None:
        statement = self._of(company).where(MembershipRecord.subject == subject)
        row = (await self._connection.execute(statement)).first()
        return None if row is None else _membership(row)

    @override
    async def put(self, membership: Membership) -> None:
        values = {
            "role": membership.role.value,
            "status": membership.status.value,
            "version": membership.version,
            "changed_at": membership.changed_at,
            "changed_by": membership.changed_by,
        }
        await self._connection.execute(
            insert(MembershipRecord)
            .values(
                id=membership.id,
                organization_id=membership.tenant.organization,
                company_id=membership.tenant.company,
                subject=membership.subject,
                **values,
            )
            .on_conflict_do_update(
                constraint="uq_memberships_subject",
                set_=values,
                where=MembershipRecord.organization_id == self._organization,
            )
        )

    @override
    async def owners(self, company: CompanyId) -> int:
        statement = (
            select(func.count())
            .select_from(MembershipRecord)
            .where(
                MembershipRecord.organization_id == self._organization,
                MembershipRecord.company_id == company,
                MembershipRecord.role == Role.OWNER.value,
                _ACTIVE,
            )
        )
        return (await self._connection.execute(statement)).scalar_one()

    @override
    async def page(self, company: CompanyId, request: PageRequest) -> Page[Membership]:
        statement = _paged(self._of(company).where(_ACTIVE), MembershipRecord.id, request)
        rows = (await self._connection.execute(statement)).all()
        return page_of([_membership(row) for row in rows], request, key=lambda membership: membership.id)

    def _of(self, company: CompanyId) -> Select[UUID, UUID, UUID, str, str, str, int, datetime, str]:
        return _MEMBERSHIP.where(
            MembershipRecord.organization_id == self._organization, MembershipRecord.company_id == company
        )
```

`services/identity/src/ledger_identity/adapters/events.py`:

```python
"""What identity announces on the wire (§4.1): protobuf messages in envelopes, through the outbox.

Stream ``IDENTITY`` holds every subject ``identity.<aggregate>.<event>``; each event is scoped to its
company and organization (``ce-subject``, ``ce-organizationid``) and carries its aggregate's version.
"""

from typing import Final, final, override
lazy from collections.abc import Mapping

lazy from google.protobuf.message import Message

from ledger.events.identity.v1 import events_pb2 as wire
from ledger_contracts.envelope import Envelope
from ledger_identity.application.ports import Events
from ledger_identity.domain.events import (
    CompanyCreated,
    CompanyDeactivated,
    CompanyUpdated,
    MembershipGranted,
)
from ledger_identity.domain.roles import Role
from ledger_kernel.clock import utc_now
from ledger_platform.streams import StreamSpec
lazy from ledger_identity.domain.events import IdentityEvent, MembershipRevoked
lazy from ledger_kernel.clock import Clock
lazy from ledger_platform.database import UnitOfWork
lazy from ledger_platform.outbox import Outbox

IDENTITY: Final = StreamSpec(name="IDENTITY", subjects=("identity.>",))
"""The stream of identity's events, declared by identity at start."""
SOURCE: Final = "identity"
_ROLES: Final[Mapping[Role, wire.Role.ValueType]] = {
    Role.OWNER: wire.ROLE_OWNER,
    Role.ADMIN: wire.ROLE_ADMIN,
    Role.ACCOUNTANT: wire.ROLE_ACCOUNTANT,
    Role.ISSUER: wire.ROLE_ISSUER,
    Role.VIEWER: wire.ROLE_VIEWER,
}


def _granted(event: MembershipGranted) -> wire.MembershipGranted:
    return wire.MembershipGranted(
        company_id=str(event.tenant.company),
        organization_id=str(event.tenant.organization),
        subject=event.subject,
        role=_ROLES[event.role],
        previous_role=wire.ROLE_UNSPECIFIED if event.previous is None else _ROLES[event.previous],
        version=event.version,
    )


def _revoked(event: MembershipRevoked) -> wire.MembershipRevoked:
    return wire.MembershipRevoked(
        company_id=str(event.tenant.company),
        organization_id=str(event.tenant.organization),
        subject=event.subject,
        role=_ROLES[event.role],
        version=event.version,
    )


def outgoing(event: IdentityEvent) -> tuple[Message, str]:
    """The payload of ``event`` and the subject it is published on."""
    company, organization = str(event.tenant.company), str(event.tenant.organization)
    if isinstance(event, CompanyCreated):
        created = wire.CompanyCreated(
            company_id=company,
            organization_id=organization,
            tax_id=str(event.tax_id),
            legal_name=event.legal_name,
            version=event.version,
        )
        return created, "identity.company.created"
    if isinstance(event, CompanyUpdated):
        updated = wire.CompanyUpdated(
            company_id=company,
            organization_id=organization,
            tax_id=str(event.tax_id),
            legal_name=event.legal_name,
            version=event.version,
        )
        return updated, "identity.company.updated"
    if isinstance(event, CompanyDeactivated):
        deactivated = wire.CompanyDeactivated(
            company_id=company, organization_id=organization, version=event.version
        )
        return deactivated, "identity.company.deactivated"
    if isinstance(event, MembershipGranted):
        return _granted(event), "identity.membership.granted"
    return _revoked(event), "identity.membership.revoked"


@final
class OutboxEvents(Events):
    """Announces identity's events through the outbox of the unit's transaction."""

    __slots__ = ("_clock", "_outbox", "_unit")

    def __init__(self, unit: UnitOfWork, outbox: Outbox, *, clock: Clock = utc_now) -> None:
        self._unit = unit
        self._outbox = outbox
        self._clock = clock

    @override
    async def publish(self, event: IdentityEvent) -> None:
        message, subject = outgoing(event)
        envelope = Envelope.wrap(message, source=SOURCE, tenant=event.tenant, clock=self._clock)
        await self._outbox.add(self._unit, envelope, subject=subject)
```

`services/identity/src/ledger_identity/adapters/postgres/unit.py`:

```python
"""identity's unit of work over one chassis transaction: its repositories and its outbox."""

from typing import ClassVar

from ledger_identity.adapters.events import OutboxEvents
from ledger_identity.adapters.postgres.repositories import (
    PgBranches,
    PgCompanies,
    PgEmissionPoints,
    PgMemberships,
    PgOrganizations,
)
from ledger_identity.application.ports import IdentityUnit
from ledger_kernel.clock import utc_now
from ledger_kernel.errors import LedgerError
from ledger_kernel.ids import Tenant
lazy from ledger_kernel.clock import Clock
lazy from ledger_kernel.ids import OrganizationId
lazy from ledger_platform.database import Scope, UnitOfWork
lazy from ledger_platform.outbox import Outbox


class UnscopedUnitError(LedgerError):
    """identity's state was to be read in a transaction scoped to nobody: a defect of the caller."""

    code: ClassVar[str] = "unscoped_unit"

    def __init__(self) -> None:
        super().__init__("identity's state is read and changed in a transaction scoped to an organization")


def _organization_of(scope: Scope | None) -> OrganizationId:
    if scope is None:
        raise UnscopedUnitError
    return scope.organization if isinstance(scope, Tenant) else scope


def bind(unit: UnitOfWork, outbox: Outbox, *, clock: Clock = utc_now) -> IdentityUnit:
    """The identity view of the transaction ``unit``: what its use cases read, change and announce.

    The repositories are bound to the organization of the unit's scope, which they name in their statements
    as row-level security enforces it. ``clock`` is read once: its answer is the unit's ``now`` and the time
    of every event the unit announces.
    """
    organization = _organization_of(unit.scope)
    now = clock()
    connection = unit.connection
    return IdentityUnit(
        organizations=PgOrganizations(connection, organization),
        companies=PgCompanies(connection, organization),
        branches=PgBranches(connection, organization),
        emission_points=PgEmissionPoints(connection, organization),
        memberships=PgMemberships(connection, organization),
        events=OutboxEvents(unit, outbox, clock=lambda: now),
        now=now,
    )
```

`services/identity/src/ledger_identity/adapters/permissions.py`:

```python
"""Permissions as identity's own memberships say: the authorization context of identity's own API (§8).

Other services ask identity over gRPC; identity reads its table directly, in a short transaction of the
caller's organization: a company of another organization holds no membership the caller could see.

Identity's own routes get the role's permissions in a deactivated company too, unlike other services
(``queries.permissions_of``): identity refuses every change of such a company itself, with the precise
``company_inactive``, and still lets a role be revoked there, since taking access away is never blocked.
"""

from typing import final, override

from ledger_identity.adapters.postgres.repositories import PgMemberships
from ledger_platform.authorization import PermissionSource
lazy from ledger_kernel.access import Permission, Principal
lazy from ledger_kernel.ids import CompanyId
lazy from ledger_platform.database import Database


@final
class MembershipPermissions(PermissionSource):
    """What a principal may do in a company of its organization: the permissions of its active role."""

    __slots__ = ("_database",)

    def __init__(self, database: Database) -> None:
        self._database = database

    @override
    async def permissions(self, principal: Principal, company: CompanyId) -> frozenset[Permission]:
        async with self._database.transaction(principal.organization) as unit:
            memberships = PgMemberships(unit.connection, principal.organization)
            membership = await memberships.get(company, principal.subject)
        return frozenset() if membership is None else membership.permissions
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest services/identity -q --no-cov`
Expected: PASS.

- [ ] **Step 6: Gate**

Run: `uv run poe fmt && uv run poe check`
Expected: every step passes, coverage 100 %.

- [ ] **Step 7: Commit**

```bash
git add services/identity uv.lock
git commit -m "feat(identity): repositories, outbox events and the membership permission source

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 13: Identity's public API, part 1: the service, its organization and companies, the published OpenAPI

**Files:**
- Create: `services/identity/src/ledger_identity/api/__init__.py`, `api/context.py`, `api/models.py`, `api/organization.py`, `api/companies.py`, `api/rest.py`
- Create: `services/identity/src/ledger_identity/main.py`, `services/identity/src/ledger_identity/openapi.py`, `services/identity/openapi.json` (generated), `libs/testing/src/ledger_testing/api.py`
- Modify: `services/identity/pyproject.toml`, `services/identity/tests/conftest.py`, `libs/testing/pyproject.toml`, `pyproject.toml` (root)
- Test: `services/identity/tests/test_companies_api.py`, `services/identity/tests/test_readiness.py`, `services/identity/tests/test_openapi.py`, `libs/testing/tests/test_api.py`

**Interfaces:**
- Consumes: `Service`, `Interfaces`, `Events`, `Runtime`, `RuntimeDependency`, and Milestone 1's final-review `checks=` and resources hook (`ledger_platform.service`, re-checked in Task 1); `TokenPolicy`, `TokenVerifier`, `PublishedKeys`, `HttpKeys` (Task 3); `Idempotency`, `StoredResponse` (Task 5); `authenticated`, `company_in_path`, `CompanyAccess`, `page_request`, `if_match`, `IfMatch`, `etag`, `precondition_failed`, `idempotency_key`, `attempt_of`, `json_response`, `answer` (Task 6); `PROBLEM_JSON` (`ledger_platform.web`, M1); the use cases (Task 10); `head`, `require_head` (Task 11); `bind`, `IDENTITY`, `MembershipPermissions` (Task 12); `IdentitySettings` (Task 11); `LocalIssuer`, `nats_server`, `nats_address` (Tasks 2-3).
- Re-check before starting: the final signatures of `checks=` and of the resources hook in `libs/platform/src/ledger_platform/service.py`. `main.build` below is written against `Service(..., checks={name: check}, resources=(factory,))`, where a check is `async (Runtime[S]) -> None` (it raises when unhealthy) and a resource factory returns an async context manager the lifecycle enters at start and leaves at stop. Where the landed hook differs (another name, a check handed nothing, resources declared on `Interfaces` or `Events`), use it as it landed: a check handed nothing reads the database through `service.runtime()`, a closure over the service being built. Say so in the task report.
- Produces: `ledger_identity.api.context`: `type BindUnit = Callable[[UnitOfWork, Outbox], IdentityUnit]`, `type PermissionsOf = Callable[[Database], PermissionSource]`, `ApiContext(*, runtime, caller, in_company, bind, idempotency)` with `classmethod build(*, runtime, verifier, bind, permissions, idempotency) -> ApiContext`, `problems(*statuses: int) -> dict[int | str, dict[str, object]]` (each documented as `application/problem+json`), `precondition_checked() -> AbstractContextManager[None]` (a `StaleVersionError` becomes 412). `ledger_identity.api.models`: `Problem`, `NewCompany`, `CompanyChanges`, `CompanyView`, `CompanyPage`, `OrganizationView` (views have `classmethod of(entity) -> Self`; Task 14 adds the shapes of branches, emission points and memberships). `ledger_identity.api.rest.routes(context: ApiContext) -> Sequence[APIRouter]`. Routes: `GET /v1/organization`; `POST /v1/companies` (201, `Location`, `ETag`); `GET /v1/companies` (`limit`, `cursor`); `GET /v1/companies/{company_id}` (`ETag`); `PATCH /v1/companies/{company_id}` (`If-Match`); `POST /v1/companies/{company_id}/deactivate` (`Idempotency-Key`, `If-Match`). `ledger_identity.main`: `VERSION`, `build(settings: IdentitySettings) -> Service[IdentitySettings]` (its resource: the key fetcher's HTTP client; its own readiness check `migrations`: the database at `head()`), `create_app() -> FastAPI` (Granian's factory). `ledger_identity.openapi`: `document() -> str`, `main() -> int` (console script `ledger-identity-openapi PATH`). `ledger_testing.api` (what every service's API tests share): `Page` (`items`, `next_cursor`), `document(response) -> dict[str, object]`, `page(response) -> Page`, `Caller(issuer, subject=SUBJECT, organization=PROVIDER_ORGANIZATION)` with `bearer() -> dict[str, str]` and `posting(key) -> dict[str, str]`, `async created(client, path, payload, headers) -> str` (the id of what a `201` made, else `NotCreatedError`). Test fixtures: `nats_url` (session), `issuer`, `settings_for` (`(database_url) -> IdentitySettings`), `service` (identity started), `client` (an `httpx.AsyncClient` on its app), `owner` (the `Caller` `2001` of organization `1001`), `company` (the id of a company `owner` registered: RNC `101850043`, `Duarte`, key `c-1`).

- [ ] **Step 1: Dependencies, scripts, layering**

`services/identity/pyproject.toml` — add `"fastapi>=0.143.0"` to `dependencies` and the second script:

```toml
[project.scripts]
ledger-identity-migrate = "ledger_identity.adapters.postgres.migrate:main"
ledger-identity-openapi = "ledger_identity.openapi:main"
```

`libs/testing/pyproject.toml` — `ledger_testing.api` calls an app with httpx: add `"httpx>=0.28.1"` to `dependencies`.

Root `pyproject.toml`: the contract "Identity is hexagonal" gets every layer:

```toml
layers = ["openapi", "main", "api | adapters", "settings", "application", "domain"]
```

and a task to regenerate the document:

```toml
openapi = { cmd = "ledger-identity-openapi services/identity/openapi.json", help = "Regenerate the published OpenAPI documents" }
```

Run: `uv lock && uv sync`

- [ ] **Step 2: Write the failing tests**

Append to `services/identity/tests/conftest.py` (imports merged at the top):

```python
from typing import Final
lazy from collections.abc import Callable

import httpx
from pydantic import SecretStr

from ledger_identity.main import build
from ledger_identity.settings import IdentitySettings
from ledger_testing.api import Caller, created
from ledger_testing.containers import nats_address, nats_server
from ledger_testing.issuer import LocalIssuer
lazy from ledger_platform.service import Service

COMPANY: Final = {"tax_id": "101850043", "legal_name": "Duarte"}
"""What the ``company`` fixture registers."""


@pytest.fixture(scope="session")
def nats_url() -> Iterator[str]:
    """A real NATS server with JetStream."""
    with nats_server() as container:
        yield nats_address(container)


@pytest.fixture
async def issuer() -> AsyncGenerator[LocalIssuer]:
    """The identity provider the service trusts in the tests."""
    async with LocalIssuer.started() as started:
        yield started


@pytest.fixture
def settings_for(nats_url: str, issuer: LocalIssuer) -> Callable[[str], IdentitySettings]:
    """identity's settings on a database: the test's NATS and identity provider, any free gRPC port."""

    def settings(database_url: str) -> IdentitySettings:
        return IdentitySettings(
            environment="test",
            database_url=SecretStr(database_url),
            nats_url=SecretStr(nats_url),
            grpc_port=0,
            token_issuer=issuer.issuer,
            token_audience=issuer.audience,
            token_jwks_url=issuer.keys_url,
        )

    return settings


@pytest.fixture
async def service(
    database: Database, database_url: str, settings_for: Callable[[str], IdentitySettings]
) -> AsyncGenerator[Service[IdentitySettings]]:
    """identity, started on the test's PostgreSQL and NATS; ``database`` empties the tables after it stops."""
    del database
    built = build(settings_for(database_url))
    async with built.app.router.lifespan_context(built.app):
        yield built


@pytest.fixture
async def client(service: Service[IdentitySettings]) -> AsyncGenerator[httpx.AsyncClient]:
    """A client of identity's HTTP app."""
    transport = httpx.ASGITransport(app=service.app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://identity.test") as opened:
        yield opened


@pytest.fixture
def owner(issuer: LocalIssuer) -> Caller:
    """Subject ``2001`` of the provider's organization ``1001``: it owns every company it registers."""
    return Caller(issuer)


@pytest.fixture
async def company(client: httpx.AsyncClient, owner: Caller) -> str:
    """The id of a company ``owner`` registered (``COMPANY``, with the key ``c-1``)."""
    return await created(client, "/v1/companies", COMPANY, owner.posting("c-1"))
```

(`nats_url` is the settings' `SecretStr`, as the chassis's own tests pass it; every API test calls through `Caller`, `created`, `document` and `page` of `ledger_testing.api` and these fixtures instead of helpers of its own.)

`libs/testing/tests/test_api.py`:

```python
import httpx
import pytest

from ledger_testing.api import Caller, NotCreatedError, created, document, page
from ledger_testing.issuer import LocalIssuer


def answering(status: int, body: dict[str, object]) -> httpx.AsyncClient:
    """A client of an app that answers every request with ``status`` and ``body``."""

    def answer(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(status, json=body)

    return httpx.AsyncClient(transport=httpx.MockTransport(answer), base_url="http://service.test")


async def test_what_a_post_made_is_known_by_its_id() -> None:
    async with answering(201, {"id": "thing-1"}) as client:
        assert await created(client, "/v1/things", {"name": "thing"}, {}) == "thing-1"


async def test_a_post_that_made_nothing_says_what_came_back() -> None:
    async with answering(409, {"code": "taken"}) as client:
        with pytest.raises(NotCreatedError, match=r"got 409: .*taken"):
            await created(client, "/v1/things", {"name": "thing"}, {})


def test_a_caller_s_headers_carry_a_token_and_the_key_of_its_post() -> None:
    headers = Caller(LocalIssuer(), subject="3001", organization="1002").posting("k-1")
    assert headers["Idempotency-Key"] == "k-1"
    assert headers["Authorization"].startswith("Bearer ey")


def test_pages_and_documents_are_read_as_json() -> None:
    response = httpx.Response(200, json={"items": [{"id": "a"}], "next_cursor": None})
    assert page(response) == {"items": [{"id": "a"}], "next_cursor": None}
    assert document(response)["next_cursor"] is None
```

`services/identity/tests/test_companies_api.py`:

```python
import asyncio
from typing import Final
from uuid import UUID

import pytest
lazy import httpx

from ledger_identity.adapters.postgres.unit import bind
from ledger_identity.application import commands
from ledger_identity.domain.roles import Role
from ledger_kernel.access import SubjectId
from ledger_kernel.ids import CompanyId, Tenant
from ledger_platform.authentication import organization_of
from ledger_testing.api import Caller, created, document, page
lazy from ledger_identity.settings import IdentitySettings
lazy from ledger_platform.service import Service
lazy from ledger_testing.issuer import LocalIssuer

DUARTE: Final = {"tax_id": "101-85004-3", "legal_name": "Ferretería Duarte, S.R.L."}
"""The same taxpayer as the ``company`` fixture's, as a client may type it."""


async def grant(service: Service[IdentitySettings], company: str, subject: str, role: Role) -> None:
    """Give ``subject`` a role directly, as the owner (``2001``) would through the memberships API."""
    runtime = service.runtime()
    tenant = Tenant(organization_of("1001"), CompanyId(UUID(company)))
    async with runtime.database.transaction(tenant) as transaction:
        await commands.grant_role(
            bind(transaction, runtime.outbox),
            tenant.company,
            subject=SubjectId(subject),
            role=role,
            actor=SubjectId("2001"),
        )


# ── Creating ────────────────────────────────────────────────────────────────────


async def test_a_company_is_created_owned_by_its_creator_and_readable(
    client: httpx.AsyncClient, owner: Caller
) -> None:
    response = await client.post("/v1/companies", json=DUARTE, headers=owner.posting("k-1"))
    assert response.status_code == 201
    made = document(response)
    assert (made["tax_id"], made["tax_id_kind"], made["legal_name"], made["status"], made["version"]) == (
        "101850043",
        "rnc",
        "Ferretería Duarte, S.R.L.",
        "active",
        1,
    )
    assert made["organization_id"] == str(organization_of("1001"))
    assert response.headers["location"] == f"/v1/companies/{made['id']}"
    assert response.headers["etag"] == '"1"'
    read = await client.get(f"/v1/companies/{made['id']}", headers=owner.bearer())
    assert (read.status_code, document(read), read.headers["etag"]) == (200, made, '"1"')
    organization = await client.get("/v1/organization", headers=owner.bearer())
    assert document(organization)["provider_id"] == "1001"


async def test_the_organization_is_unknown_until_its_first_company(
    client: httpx.AsyncClient, owner: Caller
) -> None:
    response = await client.get("/v1/organization", headers=owner.bearer())
    assert (response.status_code, document(response)["code"]) == (404, "organization_not_found")


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/v1/organization"),
        ("GET", "/v1/companies"),
        ("POST", "/v1/companies"),
        ("GET", "/v1/companies/x"),
    ],
)
async def test_every_route_needs_a_bearer_token(client: httpx.AsyncClient, method: str, path: str) -> None:
    response = await client.request(method, path, headers={"Idempotency-Key": "k"})
    assert (response.status_code, document(response)["code"]) == (401, "missing_token")


async def test_a_post_without_an_idempotency_key_is_refused(client: httpx.AsyncClient, owner: Caller) -> None:
    response = await client.post("/v1/companies", json=DUARTE, headers=owner.bearer())
    assert (response.status_code, document(response)["code"]) == (422, "invalid_request")


async def test_a_retried_post_creates_once_and_answers_the_same(
    client: httpx.AsyncClient, owner: Caller
) -> None:
    first = await client.post("/v1/companies", json=DUARTE, headers=owner.posting("k-1"))
    again = await client.post("/v1/companies", json=DUARTE, headers=owner.posting("k-1"))
    assert (again.status_code, again.content) == (first.status_code, first.content)
    assert again.headers["idempotent-replayed"] == "true"
    assert "idempotent-replayed" not in first.headers
    listed = await client.get("/v1/companies", headers=owner.bearer())
    assert len(page(listed)["items"]) == 1


async def test_a_key_reused_for_another_body_is_refused(client: httpx.AsyncClient, owner: Caller) -> None:
    await created(client, "/v1/companies", DUARTE, owner.posting("k-1"))
    other = {"tax_id": "130000012", "legal_name": "Otra"}
    response = await client.post("/v1/companies", json=other, headers=owner.posting("k-1"))
    assert (response.status_code, document(response)["code"]) == (422, "idempotency_key_reused")


async def test_two_posts_with_one_key_at_once_create_one_company(
    client: httpx.AsyncClient, owner: Caller
) -> None:
    headers = owner.posting("k-together")
    first, second = await asyncio.gather(
        client.post("/v1/companies", json=DUARTE, headers=headers),
        client.post("/v1/companies", json=DUARTE, headers=headers),
    )
    assert (first.status_code, second.status_code, first.content) == (201, 201, second.content)
    assert sorted(response.headers.get("idempotent-replayed", "no") for response in (first, second)) == [
        "no",
        "true",
    ]


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        ({"tax_id": "12345", "legal_name": "Duarte"}, "invalid_tax_id"),
        ({"tax_id": "101850043", "legal_name": "Duarte\u0000"}, "invalid_name"),
        ({"tax_id": "101850043", "legal_name": "Duarte", "colour": "red"}, "invalid_request"),
        ({"tax_id": "101850043"}, "invalid_request"),
    ],
    ids=["tax-id", "legal-name", "unknown-field", "missing-field"],
)
async def test_a_company_that_cannot_be_registered_is_refused(
    client: httpx.AsyncClient, owner: Caller, payload: dict[str, str], code: str
) -> None:
    response = await client.post("/v1/companies", json=payload, headers=owner.posting("k-bad"))
    assert (response.status_code, document(response)["code"]) == (422, code)


async def test_a_tax_id_the_organization_has_is_a_conflict(
    client: httpx.AsyncClient, owner: Caller, company: str
) -> None:
    del company
    response = await client.post("/v1/companies", json=DUARTE, headers=owner.posting("k-2"))
    assert (response.status_code, document(response)["code"]) == (409, "tax_id_taken")


# ── Reading ─────────────────────────────────────────────────────────────────────


async def test_a_caller_pages_through_the_companies_it_holds_a_role_in(
    client: httpx.AsyncClient, issuer: LocalIssuer, owner: Caller
) -> None:
    for number, rnc in enumerate(("101850043", "130000012", "401007551")):
        payload = {"tax_id": rnc, "legal_name": f"Company {number}"}
        await created(client, "/v1/companies", payload, owner.posting(f"k-{number}"))
    first = page(await client.get("/v1/companies", params={"limit": 2}, headers=owner.bearer()))
    assert len(first["items"]) == 2
    rest = page(
        await client.get(
            "/v1/companies", params={"limit": 2, "cursor": first["next_cursor"]}, headers=owner.bearer()
        )
    )
    assert (len(rest["items"]), rest["next_cursor"]) == (1, None)
    partner = page(await client.get("/v1/companies", headers=Caller(issuer, subject="2002").bearer()))
    stranger = page(await client.get("/v1/companies", headers=Caller(issuer, organization="1002").bearer()))
    assert (partner["items"], stranger["items"]) == ([], [])


async def test_a_company_is_not_found_by_whoever_holds_no_role_in_it(
    client: httpx.AsyncClient, issuer: LocalIssuer, company: str
) -> None:
    for caller in (Caller(issuer, subject="2002"), Caller(issuer, organization="1002")):
        response = await client.get(f"/v1/companies/{company}", headers=caller.bearer())
        assert (response.status_code, document(response)["code"]) == (404, "company_not_found")


async def test_a_viewer_reads_but_does_not_change(
    client: httpx.AsyncClient, issuer: LocalIssuer, service: Service[IdentitySettings], company: str
) -> None:
    await grant(service, company, "3001", Role.VIEWER)
    viewer = Caller(issuer, subject="3001").bearer()
    assert (await client.get(f"/v1/companies/{company}", headers=viewer)).status_code == 200
    response = await client.patch(
        f"/v1/companies/{company}", json={"legal_name": "X"}, headers=viewer | {"If-Match": '"1"'}
    )
    assert (response.status_code, document(response)["code"]) == (403, "permission_denied")


async def test_a_company_id_that_is_not_an_id_is_refused(client: httpx.AsyncClient, owner: Caller) -> None:
    response = await client.get("/v1/companies/not-an-id", headers=owner.bearer())
    assert (response.status_code, document(response)["code"]) == (422, "invalid_id")


# ── Changing ────────────────────────────────────────────────────────────────────


async def test_a_change_needs_the_version_it_was_made_from(
    client: httpx.AsyncClient, owner: Caller, company: str
) -> None:
    path = f"/v1/companies/{company}"
    missing = await client.patch(path, json={"legal_name": "Duarte Hermanos"}, headers=owner.bearer())
    assert (missing.status_code, document(missing)["code"]) == (428, "precondition_required")
    stale = await client.patch(
        path, json={"legal_name": "Duarte Hermanos"}, headers=owner.bearer() | {"If-Match": '"7"'}
    )
    assert (stale.status_code, document(stale)["code"]) == (412, "precondition_failed")
    applied = await client.patch(
        path, json={"legal_name": "Duarte Hermanos"}, headers=owner.bearer() | {"If-Match": '"1"'}
    )
    assert (applied.status_code, applied.headers["etag"], document(applied)["legal_name"]) == (
        200,
        '"2"',
        "Duarte Hermanos",
    )
    anything = await client.patch(
        path, json={"legal_name": "Duarte e Hijos"}, headers=owner.bearer() | {"If-Match": "*"}
    )
    assert anything.headers["etag"] == '"3"'


async def test_of_two_changes_of_one_version_only_one_applies(
    client: httpx.AsyncClient, owner: Caller, company: str
) -> None:
    headers = owner.bearer() | {"If-Match": '"1"'}
    responses = await asyncio.gather(
        client.patch(f"/v1/companies/{company}", json={"legal_name": "One"}, headers=headers),
        client.patch(f"/v1/companies/{company}", json={"legal_name": "Other"}, headers=headers),
    )
    assert sorted(response.status_code for response in responses) == [200, 412]
    read = await client.get(f"/v1/companies/{company}", headers=owner.bearer())
    assert read.headers["etag"] == '"2"'


async def test_a_deactivated_company_changes_no_more_and_the_deactivation_replays(
    client: httpx.AsyncClient, owner: Caller, company: str
) -> None:
    path = f"/v1/companies/{company}/deactivate"
    headers = owner.posting("k-off") | {"If-Match": '"1"'}
    deactivated = await client.post(path, headers=headers)
    assert (deactivated.status_code, document(deactivated)["status"], deactivated.headers["etag"]) == (
        200,
        "inactive",
        '"2"',
    )
    again = await client.post(path, headers=headers)
    assert (again.content, again.headers["idempotent-replayed"]) == (deactivated.content, "true")
    change = await client.patch(
        f"/v1/companies/{company}", json={"legal_name": "X"}, headers=owner.bearer() | {"If-Match": "*"}
    )
    assert (change.status_code, document(change)["code"]) == (409, "company_inactive")
    stale = await client.post(path, headers=owner.posting("k-off-2") | {"If-Match": '"1"'})
    assert stale.status_code == 412
```

`services/identity/tests/test_readiness.py`:

```python
from typing import Final, TypedDict
lazy from collections.abc import Callable

import httpx
from pydantic import TypeAdapter

from ledger_identity.main import build
lazy from ledger_identity.settings import IdentitySettings


class Readiness(TypedDict):
    status: str
    checks: dict[str, bool]


READINESS: Final = TypeAdapter(Readiness)


async def test_identity_is_ready_on_a_database_at_its_schema(client: httpx.AsyncClient) -> None:
    response = await client.get("/readyz")
    readiness = READINESS.validate_json(response.content)
    assert (response.status_code, readiness["checks"]["migrations"]) == (200, True)
    assert all(readiness["checks"].values())


async def test_identity_on_a_database_without_its_schema_is_not_ready(
    fresh_database_url: str, settings_for: Callable[[str], IdentitySettings]
) -> None:
    built = build(settings_for(fresh_database_url))
    async with built.app.router.lifespan_context(built.app):
        transport = httpx.ASGITransport(app=built.app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://identity.test") as client:
            response = await client.get("/readyz")
    readiness = READINESS.validate_json(response.content)
    assert (response.status_code, readiness["checks"]["migrations"]) == (503, False)
```

(On the empty database the chassis's own check that its tables exist fails as well; the outbox relay logs a failed pass until the service stops. Neither is asserted here: they are Milestone 1's.)

`services/identity/tests/test_openapi.py`:

```python
from pathlib import Path
from typing import Final, TypedDict

from pydantic import TypeAdapter
lazy import pytest

from ledger_identity import openapi
from ledger_identity.main import VERSION, create_app
from ledger_identity.openapi import document

PUBLISHED: Final = Path(__file__).parents[1] / "openapi.json"
Parameter = TypedDict("Parameter", {"name": str, "in": str, "required": bool})


class Answer(TypedDict, total=False):
    content: dict[str, dict[str, object]]


class Operation(TypedDict, total=False):
    parameters: list[Parameter]
    security: list[dict[str, list[str]]]
    responses: dict[str, Answer]


class Document(TypedDict):
    openapi: str
    paths: dict[str, dict[str, Operation]]


DOCUMENT: Final = TypeAdapter(Document)


def test_the_published_document_is_the_one_the_code_makes() -> None:
    assert PUBLISHED.read_text(encoding="utf-8") == document(), "regenerate it: uv run poe openapi"


def test_the_document_is_openapi_3_1_and_every_post_takes_an_idempotency_key() -> None:
    parsed = DOCUMENT.validate_json(document())
    assert parsed["openapi"].startswith("3.1.")
    posts = [operations["post"] for operations in parsed["paths"].values() if "post" in operations]
    assert posts
    for post in posts:
        assert {"name": "Idempotency-Key", "in": "header", "required": True} in post.get("parameters", [])


def test_every_operation_is_for_bearers_of_a_token() -> None:
    parsed = DOCUMENT.validate_json(document())
    for operations in parsed["paths"].values():
        for operation in operations.values():
            assert operation.get("security") == [{"HTTPBearer": []}]


def test_every_error_an_operation_documents_is_a_problem_document() -> None:
    parsed = DOCUMENT.validate_json(document())
    errors = [
        answer
        for operations in parsed["paths"].values()
        for operation in operations.values()
        for status, answer in operation.get("responses", {}).items()
        if status.startswith(("4", "5"))
    ]
    assert errors
    for answer in errors:
        assert list(answer.get("content", {})) == ["application/problem+json"]


def test_the_command_line_writes_the_document(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "openapi.json"
    monkeypatch.setattr("sys.argv", ["ledger-identity-openapi", str(target)])
    assert openapi.main() == 0
    assert target.read_text(encoding="utf-8") == document()


def test_the_command_line_needs_a_path(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.argv", ["ledger-identity-openapi"])
    assert openapi.main() == 2
    assert capsys.readouterr().err == "usage: ledger-identity-openapi PATH\n"


def test_granians_factory_builds_the_app_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in {
        "LEDGER_DATABASE_URL": "postgresql+psycopg://identity:secret@localhost/identity",
        "LEDGER_TOKEN_ISSUER": "https://issuer.example",
        "LEDGER_TOKEN_AUDIENCE": "ledger",
        "LEDGER_TOKEN_JWKS_URL": "https://issuer.example/oauth/v2/keys",
    }.items():
        monkeypatch.setenv(name, value)
    app = create_app()
    assert (app.title, app.version) == ("identity", VERSION)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest services/identity/tests/test_companies_api.py services/identity/tests/test_readiness.py services/identity/tests/test_openapi.py libs/testing/tests/test_api.py -q --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'ledger_identity.main'` (and `'ledger_testing.api'`).

- [ ] **Step 4: Implement**

`services/identity/src/ledger_identity/api/__init__.py`:

```python
"""identity's interfaces: the REST API under ``/v1`` and the gRPC query service; thin, they delegate."""
```

`services/identity/src/ledger_identity/api/context.py`:

```python
"""What identity's routes are built with: the runtime, the caller, its access, and what ``main`` wires."""

from contextlib import contextmanager
from dataclasses import dataclass
from http import HTTPStatus
from typing import Annotated, Final, final
lazy from collections.abc import Callable, Iterator

from fastapi import Depends

from ledger_identity.api.models import Problem
from ledger_identity.domain.errors import StaleVersionError
from ledger_platform.routes import authenticated, company_in_path, precondition_failed
from ledger_platform.web import PROBLEM_JSON
lazy from ledger_identity.application.ports import IdentityUnit
lazy from ledger_identity.settings import IdentitySettings
lazy from ledger_platform.authentication import TokenVerifier
lazy from ledger_platform.authorization import PermissionSource
lazy from ledger_platform.database import Database, UnitOfWork
lazy from ledger_platform.idempotency import Idempotency
lazy from ledger_platform.outbox import Outbox
lazy from ledger_platform.routes import Caller, InCompany
lazy from ledger_platform.service import Runtime, RuntimeDependency

type BindUnit = Callable[[UnitOfWork, Outbox], IdentityUnit]
"""identity's unit of work over a chassis transaction (``adapters.postgres.unit.bind``)."""
type PermissionsOf = Callable[[Database], PermissionSource]
"""identity's permission source over its database (``adapters.permissions.MembershipPermissions``)."""
_PROBLEM_SCHEMA: Final = Problem.model_json_schema()
"""What every error answer holds, written into each answer: the document then needs no shared component."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class ApiContext:
    """The dependencies every router shares, built once with the app."""

    runtime: RuntimeDependency[IdentitySettings]
    caller: Caller
    in_company: InCompany
    bind: BindUnit
    idempotency: Idempotency

    @classmethod
    def build(
        cls,
        *,
        runtime: RuntimeDependency[IdentitySettings],
        verifier: TokenVerifier,
        bind: BindUnit,
        permissions: PermissionsOf,
        idempotency: Idempotency,
    ) -> ApiContext:
        """The context of identity's routes: the caller from ``verifier``, its access from ``permissions``."""
        caller = authenticated(verifier)

        async def permission_source(
            current: Annotated[Runtime[IdentitySettings], Depends(runtime)],
        ) -> PermissionSource:
            return permissions(current.database)

        return cls(
            runtime=runtime,
            caller=caller,
            in_company=company_in_path(caller, permission_source),
            bind=bind,
            idempotency=idempotency,
        )


def problems(*statuses: int) -> dict[int | str, dict[str, object]]:
    """The error answers an operation documents: RFC 9457 problem documents, ``application/problem+json``.

    Written out: FastAPI's ``model`` would document them as ``application/json``, which no error is sent as.
    """
    answers: dict[int | str, dict[str, object]] = {
        status: {
            "description": HTTPStatus(status).phrase,
            "content": {PROBLEM_JSON: {"schema": _PROBLEM_SCHEMA}},
        }
        for status in statuses
    }
    return answers


@contextmanager
def precondition_checked() -> Iterator[None]:
    """An update whose ``If-Match`` names no current version answers 412, as RFC 9110 says, not 409."""
    try:
        yield
    except StaleVersionError as error:
        raise precondition_failed() from error
```

`services/identity/src/ledger_identity/api/models.py`:

```python
"""The shapes of identity's public API: what clients send (unknown fields refused) and what they receive.

Ids are UUID strings, times ISO 8601 with their offset (UTC), enumerations their lowercase words.
"""

from datetime import datetime
from typing import ClassVar, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ledger_identity.domain.company import CompanyStatus
from ledger_kernel.countries.do import TaxIdKind
lazy from ledger_identity.domain.company import Company
lazy from ledger_identity.domain.organization import Organization


class _Shape(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)


class Problem(BaseModel):
    """An RFC 9457 problem document: what every failure answers."""

    type: str = Field(description="urn:ledger:error:<code>")
    title: str
    status: int
    code: str = Field(description="The stable code to match on.")
    detail: str | None = None


class NewCompany(_Shape):
    """A company to register."""

    tax_id: str = Field(
        min_length=1,
        max_length=20,
        description="RNC (9 digits) or cedula (11 digits); hyphens and spaces are ignored.",
        examples=["101-85004-3"],
    )
    legal_name: str = Field(
        min_length=1, max_length=300, description="As registered with DGII; up to 150 characters."
    )


class CompanyChanges(_Shape):
    """What can change in a company."""

    legal_name: str = Field(min_length=1, max_length=300)


class CompanyView(_Shape):
    """A company, at its current version (its ``ETag``)."""

    id: UUID
    organization_id: UUID
    tax_id: str
    tax_id_kind: TaxIdKind
    legal_name: str
    status: CompanyStatus
    version: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, company: Company) -> Self:
        """The view of ``company``."""
        return cls(
            id=company.id,
            organization_id=company.organization,
            tax_id=str(company.tax_id),
            tax_id_kind=company.tax_id.kind,
            legal_name=company.legal_name,
            status=company.status,
            version=company.version,
            created_at=company.created_at,
            updated_at=company.updated_at,
        )


class CompanyPage(_Shape):
    """Companies in id order, and the cursor of the next page (``null`` on the last)."""

    items: list[CompanyView]
    next_cursor: str | None


class OrganizationView(_Shape):
    """The caller's organization."""

    id: UUID
    provider_id: str = Field(description="The identity provider's (Zitadel's) id of the organization.")
    created_at: datetime

    @classmethod
    def of(cls, organization: Organization) -> Self:
        """The view of ``organization``."""
        return cls(
            id=organization.id, provider_id=organization.provider_id, created_at=organization.created_at
        )
```

`services/identity/src/ledger_identity/api/organization.py`:

```python
"""``/v1/organization``: the caller's organization."""

from typing import Annotated

from fastapi import APIRouter, Depends

from ledger_identity.api.context import problems
from ledger_identity.api.models import OrganizationView
from ledger_identity.application import queries
lazy from ledger_identity.api.context import ApiContext
lazy from ledger_identity.settings import IdentitySettings
lazy from ledger_kernel.access import Principal
lazy from ledger_platform.service import Runtime


def router(context: ApiContext) -> APIRouter:
    """The organization's routes."""
    organization = APIRouter(prefix="/v1/organization", tags=["organization"])
    caller, runtime = context.caller, context.runtime

    @organization.get("", response_model=OrganizationView, responses=problems(401, 404))
    async def read_organization(
        principal: Annotated[Principal, Depends(caller)],
        current: Annotated[Runtime[IdentitySettings], Depends(runtime)],
    ) -> OrganizationView:
        """The organization the caller acts in; 404 until it has a company."""
        async with current.database.transaction(principal.organization) as transaction:
            found = await queries.organization(
                context.bind(transaction, current.outbox), principal.organization
            )
        return OrganizationView.of(found)

    return organization
```

`services/identity/src/ledger_identity/api/companies.py`:

```python
"""``/v1/companies``: register, list, read, change and deactivate companies."""

from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, Depends
lazy from fastapi import Request, Response

from ledger_identity.api.context import precondition_checked, problems
from ledger_identity.api.models import CompanyPage, CompanyView
from ledger_identity.application import commands, queries
from ledger_identity.domain.text import parse_legal_name
from ledger_kernel.access import Permission
from ledger_kernel.countries.do import TaxId
from ledger_platform.routes import (
    answer,
    attempt_of,
    etag,
    idempotency_key,
    if_match,
    json_response,
    page_request,
)
lazy from ledger_identity.api.context import ApiContext
lazy from ledger_identity.api.models import CompanyChanges, NewCompany
lazy from ledger_identity.domain.company import Company
lazy from ledger_identity.settings import IdentitySettings
lazy from ledger_kernel.access import Principal
lazy from ledger_kernel.pagination import PageRequest
lazy from ledger_platform.authorization import CompanyAccess
lazy from ledger_platform.idempotency import StoredResponse
lazy from ledger_platform.routes import IfMatch
lazy from ledger_platform.service import Runtime


def stored(status: HTTPStatus, company: Company) -> StoredResponse:
    """A company's answer, as kept for replays: its view, where it lives and its version."""
    headers = {"Location": f"/v1/companies/{company.id}", "ETag": etag(company.version)}
    return json_response(status, CompanyView.of(company), headers)


def router(context: ApiContext) -> APIRouter:
    """The companies' routes."""
    companies = APIRouter(prefix="/v1/companies", tags=["companies"])
    caller, in_company, runtime = context.caller, context.in_company, context.runtime

    @companies.post(
        "", status_code=HTTPStatus.CREATED, response_model=CompanyView, responses=problems(401, 409, 422)
    )
    async def create_company(
        body: NewCompany,
        request: Request,
        principal: Annotated[Principal, Depends(caller)],
        key: Annotated[str, Depends(idempotency_key)],
        current: Annotated[Runtime[IdentitySettings], Depends(runtime)],
    ) -> Response:
        """Register a company of the caller's organization; the caller becomes its owner."""
        tax_id, legal_name = TaxId.parse(body.tax_id), parse_legal_name(body.legal_name)
        attempt = attempt_of(request, principal, key, body)
        async with current.database.transaction(principal.organization) as transaction:

            async def register() -> StoredResponse:
                unit = context.bind(transaction, current.outbox)
                created = await commands.create_company(unit, principal, tax_id=tax_id, legal_name=legal_name)
                return stored(HTTPStatus.CREATED, created)

            outcome = await context.idempotency.once(transaction, attempt, register)
        return answer(outcome)

    @companies.get("", response_model=CompanyPage, responses=problems(401, 422))
    async def list_companies(
        principal: Annotated[Principal, Depends(caller)],
        page: Annotated[PageRequest, Depends(page_request)],
        current: Annotated[Runtime[IdentitySettings], Depends(runtime)],
    ) -> CompanyPage:
        """The companies of the caller's organization in which it holds a role."""
        async with current.database.transaction(principal.organization) as transaction:
            unit = context.bind(transaction, current.outbox)
            found = await queries.companies_of(unit, principal.subject, page)
        return CompanyPage(
            items=[CompanyView.of(company) for company in found.items], next_cursor=found.next_cursor
        )

    @companies.get("/{company_id}", response_model=CompanyView, responses=problems(401, 403, 404, 422))
    async def read_company(
        access: Annotated[CompanyAccess, Depends(in_company)],
        response: Response,
        current: Annotated[Runtime[IdentitySettings], Depends(runtime)],
    ) -> CompanyView:
        """A company, with its version as ``ETag``."""
        access.require(Permission.COMPANY_READ)
        async with current.database.transaction(access.tenant) as transaction:
            found = await queries.company(context.bind(transaction, current.outbox), access.tenant.company)
        response.headers["ETag"] = etag(found.version)
        return CompanyView.of(found)

    @companies.patch(
        "/{company_id}", response_model=CompanyView, responses=problems(401, 403, 404, 409, 412, 422, 428)
    )
    async def change_company(
        body: CompanyChanges,
        access: Annotated[CompanyAccess, Depends(in_company)],
        condition: Annotated[IfMatch, Depends(if_match)],
        response: Response,
        current: Annotated[Runtime[IdentitySettings], Depends(runtime)],
    ) -> CompanyView:
        """Change a company, if it is at a version ``If-Match`` names."""
        access.require(Permission.COMPANY_MANAGE)
        legal_name = parse_legal_name(body.legal_name)
        with precondition_checked():
            async with current.database.transaction(access.tenant) as transaction:
                changed = await commands.rename_company(
                    context.bind(transaction, current.outbox),
                    access.tenant.company,
                    legal_name=legal_name,
                    admits=condition.admits,
                )
        response.headers["ETag"] = etag(changed.version)
        return CompanyView.of(changed)

    @companies.post(
        "/{company_id}/deactivate",
        response_model=CompanyView,
        responses=problems(401, 403, 404, 409, 412, 422, 428),
    )
    async def deactivate_company(
        request: Request,
        access: Annotated[CompanyAccess, Depends(in_company)],
        key: Annotated[str, Depends(idempotency_key)],
        condition: Annotated[IfMatch, Depends(if_match)],
        current: Annotated[Runtime[IdentitySettings], Depends(runtime)],
    ) -> Response:
        """Deactivate a company, if it is at a version ``If-Match`` names: it changes no more."""
        access.require(Permission.COMPANY_MANAGE)
        attempt = attempt_of(request, access.principal, key)
        with precondition_checked():
            async with current.database.transaction(access.tenant) as transaction:

                async def deactivate() -> StoredResponse:
                    unit = context.bind(transaction, current.outbox)
                    inactive = await commands.deactivate_company(
                        unit, access.tenant.company, admits=condition.admits
                    )
                    return stored(HTTPStatus.OK, inactive)

                outcome = await context.idempotency.once(transaction, attempt, deactivate)
        return answer(outcome)

    return companies
```

`services/identity/src/ledger_identity/api/rest.py`:

```python
"""identity's REST API: every router, under ``/v1``."""

lazy from collections.abc import Sequence

lazy from fastapi import APIRouter

from ledger_identity.api import companies, organization
lazy from ledger_identity.api.context import ApiContext


def routes(context: ApiContext) -> Sequence[APIRouter]:
    """The routers of identity's public API."""
    return (organization.router(context), companies.router(context))
```

`services/identity/src/ledger_identity/main.py`:

```python
"""identity's composition root: settings, adapters and interfaces wired into one chassis ``Service``."""

from importlib.metadata import version
from typing import Final
lazy from collections.abc import Sequence

lazy from fastapi import APIRouter, FastAPI

from ledger_identity.adapters.events import IDENTITY
from ledger_identity.adapters.permissions import MembershipPermissions
from ledger_identity.adapters.postgres.migrate import head, require_head
from ledger_identity.adapters.postgres.unit import bind
from ledger_identity.api.context import ApiContext
from ledger_identity.api.rest import routes
from ledger_identity.settings import IdentitySettings
from ledger_platform.authentication import HttpKeys, PublishedKeys, TokenPolicy, TokenVerifier
from ledger_platform.idempotency import Idempotency
from ledger_platform.service import Events, Interfaces, Service
lazy from ledger_platform.service import Runtime, RuntimeDependency

VERSION: Final = version("ledger-identity")


def build(settings: IdentitySettings) -> Service[IdentitySettings]:
    """The identity service ``settings`` describe: built, not started (its app's lifespan starts it).

    Its resource is the HTTP client that fetches the identity provider's keys, open while the service runs.
    Its own readiness check, ``migrations``: the database is at the revision this version's migrations end
    at, so a new version on a database not yet migrated stays out of rotation.
    """
    policy = TokenPolicy.of(settings)
    keys = HttpKeys()
    verifier = TokenVerifier(policy, keys=PublishedKeys(policy, fetch=keys))
    idempotency = Idempotency()
    expected = head()

    def http(runtime: RuntimeDependency[IdentitySettings]) -> Sequence[APIRouter]:
        context = ApiContext.build(
            runtime=runtime,
            verifier=verifier,
            bind=bind,
            permissions=MembershipPermissions,
            idempotency=idempotency,
        )
        return routes(context)

    async def migrations_at_head(runtime: Runtime[IdentitySettings]) -> None:
        await require_head(runtime.database, expected)

    return Service(
        settings=settings,
        version=VERSION,
        interfaces=Interfaces(http=http),
        events=Events(streams=(IDENTITY,)),
        checks={"migrations": migrations_at_head},
        resources=(keys.opened,),
    )


def create_app() -> FastAPI:
    """Granian's factory (``--factory``): the app of the service the ``LEDGER_*`` environment configures."""
    return build(IdentitySettings()).app
```

`libs/testing/src/ledger_testing/api.py`:

```python
"""Calling a service's public API in a test: a caller's headers, its answers as JSON, what a POST made.

Every service's API takes a bearer token, an ``Idempotency-Key`` on each ``POST``, and answers pages as
``{"items": [...], "next_cursor": ...}``: these helpers speak that, with tokens of ``LocalIssuer``.
"""

from dataclasses import dataclass
from typing import Final, TypedDict, final
lazy from collections.abc import Mapping

from pydantic import TypeAdapter
lazy import httpx

from ledger_testing.issuer import PROVIDER_ORGANIZATION, SUBJECT
lazy from ledger_testing.issuer import LocalIssuer

_CREATED: Final = 201
_SHOWN: Final = 500
"""How much of an unexpected answer's body a failure shows."""


class Page(TypedDict):
    """A page of a list: its items and the cursor of the next page (``None`` on the last)."""

    items: list[dict[str, object]]
    next_cursor: str | None


_DOCUMENT: Final = TypeAdapter(dict[str, object])
_PAGE: Final = TypeAdapter(Page)


class NotCreatedError(AssertionError):
    """A request that should have created something did not: what came back, for the test's report."""

    def __init__(self, response: httpx.Response) -> None:
        super().__init__(f"expected 201 Created, got {response.status_code}: {response.text[:_SHOWN]}")


def document(response: httpx.Response) -> dict[str, object]:
    """The JSON object a response holds: a resource, or a problem."""
    return _DOCUMENT.validate_json(response.content)


def page(response: httpx.Response) -> Page:
    """The page a list answered."""
    return _PAGE.validate_json(response.content)


@final
@dataclass(frozen=True, slots=True)
class Caller:
    """A principal of ``issuer`` (``subject`` of ``organization``), as the headers of its requests."""

    issuer: LocalIssuer
    subject: str = SUBJECT
    organization: str = PROVIDER_ORGANIZATION

    def bearer(self) -> dict[str, str]:
        """``Authorization: Bearer`` with a fresh token of the caller."""
        token = self.issuer.token(subject=self.subject, organization=self.organization)
        return {"Authorization": f"Bearer {token}"}

    def posting(self, key: str) -> dict[str, str]:
        """The headers of a ``POST``: the caller's token, and ``key`` as its ``Idempotency-Key``."""
        return self.bearer() | {"Idempotency-Key": key}


async def created(
    client: httpx.AsyncClient, path: str, payload: Mapping[str, object], headers: Mapping[str, str]
) -> str:
    """``POST`` ``payload`` to ``path``: the ``id`` of what it made, else ``NotCreatedError``."""
    response = await client.post(path, json=payload, headers=headers)
    if response.status_code != _CREATED:
        raise NotCreatedError(response)
    return str(document(response)["id"])
```

`services/identity/src/ledger_identity/openapi.py`:

```python
"""identity's OpenAPI document (3.1), as published in ``services/identity/openapi.json`` (§10).

The document is made from the code: the service is built with placeholder settings (nothing connects) and its
app asked for its schema. A test keeps the published file equal to it.
"""

import json
import sys
from pathlib import Path
from typing import Final

from pydantic import SecretStr

from ledger_identity.main import build
from ledger_identity.settings import IdentitySettings

_USAGE: Final = "usage: ledger-identity-openapi PATH\n"
_WRONG_USAGE: Final = 2
_DATABASE: Final = "postgresql+psycopg://openapi@localhost/openapi"
_ISSUER: Final = "https://issuer.invalid"
"""Placeholders: the document does not depend on whom the service trusts, and nothing is fetched."""
_AUDIENCE: Final = "ledger"
_KEYS: Final = "https://issuer.invalid/oauth/v2/keys"


def document() -> str:
    """The OpenAPI document of identity's public API, as JSON with sorted keys."""
    settings = IdentitySettings(
        database_url=SecretStr(_DATABASE),
        token_issuer=_ISSUER,
        token_audience=_AUDIENCE,
        token_jwks_url=_KEYS,
    )
    return json.dumps(build(settings).app.openapi(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> int:
    """``ledger-identity-openapi PATH``: write the document to ``PATH``."""
    match sys.argv[1:]:
        case [path]:
            Path(path).write_text(document(), encoding="utf-8")
            return 0
        case _:
            sys.stderr.write(_USAGE)
            return _WRONG_USAGE
```

- [ ] **Step 5: Publish the document and run the tests**

Run: `uv sync && uv run poe openapi && uv run pytest services/identity -q --no-cov`
Expected: `services/identity/openapi.json` written; PASS.

- [ ] **Step 6: Gate**

Run: `uv run poe fmt && uv run poe check`
Expected: every step passes, coverage 100 %.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock services/identity libs/testing
git commit -m "feat(identity): public API for organizations and companies, idempotent and conditional, OpenAPI published

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 14: Identity's public API, part 2: branches, emission points and memberships

**Files:**
- Create: `services/identity/src/ledger_identity/api/branches.py`, `services/identity/src/ledger_identity/api/emission_points.py`, `services/identity/src/ledger_identity/api/memberships.py`
- Modify: `services/identity/src/ledger_identity/api/models.py`, `services/identity/src/ledger_identity/api/rest.py`, `services/identity/openapi.json` (regenerated)
- Test: `services/identity/tests/test_establishments_api.py`, `services/identity/tests/test_memberships_api.py`

**Interfaces:**
- Consumes: `ApiContext`, `problems`, `stored`-style answers (Task 13); the use cases `open_branch`, `open_emission_point`, `grant_role`, `revoke_role` (the actor is the caller's subject: the use case reads its membership under the company's lock) and the queries (Task 10); `parse_code`, `parse_name`, `parse_subject`, `parse_id`; the platform's request dependencies (Task 6); the fixtures `client`, `issuer`, `owner`, `company` and `ledger_testing.api` (Task 13).
- Produces: models `NewBranch`, `BranchView`, `BranchPage`, `NewEmissionPoint`, `EmissionPointView`, `EmissionPointPage`, `RoleAssignment`, `MembershipView`, `MembershipPage` (views with `classmethod of(entity) -> Self`). Routers: `branches.router`, `emission_points.router`, `memberships.router` (each `(context: ApiContext) -> APIRouter`); `branches.READ` and `branches.OPEN` (the documented error answers); `emission_points.InBranch(access: CompanyAccess, branch: BranchId)`, the dependency value of a route under a branch. A created resource's `Location` is built from the ids the service holds (`/v1/companies/{company}/branches/{branch}`), never from the client's spelling of the path. Routes: `POST /v1/companies/{company_id}/branches` (201, `Idempotency-Key`), `GET /v1/companies/{company_id}/branches`, `GET /v1/companies/{company_id}/branches/{branch_id}`, `POST /v1/companies/{company_id}/branches/{branch_id}/emission-points` (201, `Idempotency-Key`), `GET /v1/companies/{company_id}/branches/{branch_id}/emission-points`, `GET /v1/companies/{company_id}/branches/{branch_id}/emission-points/{emission_point_id}`, `GET /v1/companies/{company_id}/memberships` (active memberships), `PUT /v1/companies/{company_id}/memberships/{subject}` (201 when the subject held no active role, else 200; a role granted again after a revocation continues the membership's version), `DELETE /v1/companies/{company_id}/memberships/{subject}` (204). Reading needs `company.read` (memberships: `membership.read`); opening needs `company.manage`; granting and revoking need `membership.manage` (and the owner role, `ownership.manage`).

- [ ] **Step 1: Write the failing tests**

`services/identity/tests/test_establishments_api.py`:

```python
from typing import Final

import pytest
lazy import httpx

from ledger_testing.api import Caller, created, document, page
lazy from ledger_testing.issuer import LocalIssuer

OTHER: Final = {"tax_id": "130000012", "legal_name": "Otra"}


async def another_company(client: httpx.AsyncClient, owner: Caller) -> str:
    return await created(client, "/v1/companies", OTHER, owner.posting("c-2"))


async def branch(client: httpx.AsyncClient, owner: Caller, company: str, code: str = "SUC1") -> str:
    """The id of a branch ``owner`` opened in ``company`` under ``code`` (a key per company and code)."""
    payload = {"code": code, "name": "Santiago"}
    path = f"/v1/companies/{company}/branches"
    return await created(client, path, payload, owner.posting(f"b-{company}-{code}"))


async def test_a_branch_is_opened_under_its_code_and_found_again(
    client: httpx.AsyncClient, owner: Caller, company: str
) -> None:
    path, payload = f"/v1/companies/{company}/branches", {"code": "SUC1", "name": " Santiago  Centro "}
    response = await client.post(path, json=payload, headers=owner.posting("b-1"))
    assert response.status_code == 201
    opened = document(response)
    assert (opened["code"], opened["name"], opened["company_id"]) == ("SUC1", "Santiago Centro", company)
    assert response.headers["location"] == f"{path}/{opened['id']}"
    read = await client.get(f"{path}/{opened['id']}", headers=owner.bearer())
    assert document(read) == opened
    again = await client.post(path, json=payload, headers=owner.posting("b-1"))
    assert (again.content, again.headers["idempotent-replayed"]) == (response.content, "true")


async def test_where_a_new_branch_lives_is_spelled_by_the_service(
    client: httpx.AsyncClient, owner: Caller, company: str
) -> None:
    response = await client.post(
        f"/v1/companies/{company.upper()}/branches",
        json={"code": "SUC2", "name": "Bonao"},
        headers=owner.posting("b-upper"),
    )
    assert response.status_code == 201
    assert response.headers["location"] == f"/v1/companies/{company}/branches/{document(response)['id']}"


async def test_a_branch_code_is_taken_once_per_company(
    client: httpx.AsyncClient, owner: Caller, company: str
) -> None:
    second = await another_company(client, owner)
    await branch(client, owner, company)
    taken = await client.post(
        f"/v1/companies/{company}/branches", json={"code": "SUC1", "name": "X"}, headers=owner.posting("b-2")
    )
    assert (taken.status_code, document(taken)["code"]) == (409, "branch_code_taken")
    assert await branch(client, owner, second)


@pytest.mark.parametrize(
    ("payload", "code"),
    [({"code": "suc 1", "name": "X"}, "invalid_code"), ({"code": "SUC1", "name": " "}, "invalid_name")],
    ids=["code", "name"],
)
async def test_a_branch_that_cannot_be_opened_is_refused(
    client: httpx.AsyncClient, owner: Caller, company: str, payload: dict[str, str], code: str
) -> None:
    response = await client.post(
        f"/v1/companies/{company}/branches", json=payload, headers=owner.posting("b-bad")
    )
    assert (response.status_code, document(response)["code"]) == (422, code)


async def test_branches_are_listed_page_by_page(
    client: httpx.AsyncClient, owner: Caller, company: str
) -> None:
    for code in ("SUC1", "SUC2"):
        await branch(client, owner, company, code)
    path = f"/v1/companies/{company}/branches"
    first = page(await client.get(path, params={"limit": 1}, headers=owner.bearer()))
    rest = page(
        await client.get(path, params={"limit": 1, "cursor": first["next_cursor"]}, headers=owner.bearer())
    )
    assert (len(first["items"]), len(rest["items"]), rest["next_cursor"]) == (1, 1, None)


async def test_a_branch_is_found_under_its_own_company_only(
    client: httpx.AsyncClient, owner: Caller, company: str
) -> None:
    second = await another_company(client, owner)
    branch_id = await branch(client, owner, company)
    response = await client.get(f"/v1/companies/{second}/branches/{branch_id}", headers=owner.bearer())
    assert (response.status_code, document(response)["code"]) == (404, "branch_not_found")


async def test_emission_points_open_in_a_branch_under_a_code_unique_there(
    client: httpx.AsyncClient, owner: Caller, company: str
) -> None:
    first, second = await branch(client, owner, company, "SUC1"), await branch(client, owner, company, "SUC2")
    base = f"/v1/companies/{company}/branches/{first}/emission-points"
    opened = await client.post(base, json={"code": "001", "name": "Caja 1"}, headers=owner.posting("p-1"))
    assert opened.status_code == 201
    point = document(opened)
    assert (point["branch_id"], point["code"], opened.headers["location"]) == (
        first,
        "001",
        f"{base}/{point['id']}",
    )
    taken = await client.post(base, json={"code": "001", "name": "Caja"}, headers=owner.posting("p-2"))
    assert (taken.status_code, document(taken)["code"]) == (409, "emission_point_code_taken")
    elsewhere = f"/v1/companies/{company}/branches/{second}/emission-points"
    assert (
        await client.post(elsewhere, json={"code": "001", "name": "Caja"}, headers=owner.posting("p-3"))
    ).status_code == 201
    assert document(await client.get(f"{base}/{point['id']}", headers=owner.bearer())) == point
    assert [item["id"] for item in page(await client.get(base, headers=owner.bearer()))["items"]] == [
        point["id"]
    ]
    wrong_branch = await client.get(f"{elsewhere}/{point['id']}", headers=owner.bearer())
    assert (wrong_branch.status_code, document(wrong_branch)["code"]) == (404, "emission_point_not_found")


async def test_an_emission_point_needs_a_branch_that_exists(
    client: httpx.AsyncClient, owner: Caller, company: str
) -> None:
    missing = "0192f7a4-6b1e-7c3d-8e4f-5a6b7c8d9e0f"
    base = f"/v1/companies/{company}/branches/{missing}/emission-points"
    opened = await client.post(base, json={"code": "001", "name": "Caja"}, headers=owner.posting("p-x"))
    listed = await client.get(base, headers=owner.bearer())
    assert (opened.status_code, document(opened)["code"], listed.status_code) == (
        404,
        "branch_not_found",
        404,
    )
    malformed = await client.get(f"/v1/companies/{company}/branches/not-an-id", headers=owner.bearer())
    assert (malformed.status_code, document(malformed)["code"]) == (422, "invalid_id")


async def test_a_viewer_reads_branches_and_opens_none(
    client: httpx.AsyncClient, issuer: LocalIssuer, owner: Caller, company: str
) -> None:
    await branch(client, owner, company)
    granted = await client.put(
        f"/v1/companies/{company}/memberships/3001", json={"role": "viewer"}, headers=owner.bearer()
    )
    assert granted.status_code == 201
    viewer = Caller(issuer, subject="3001")
    assert (await client.get(f"/v1/companies/{company}/branches", headers=viewer.bearer())).status_code == 200
    refused = await client.post(
        f"/v1/companies/{company}/branches", json={"code": "SUC9", "name": "X"}, headers=viewer.posting("b-v")
    )
    assert (refused.status_code, document(refused)["code"]) == (403, "permission_denied")


async def test_nothing_opens_in_a_deactivated_company(
    client: httpx.AsyncClient, owner: Caller, company: str
) -> None:
    branch_id = await branch(client, owner, company)
    off = await client.post(
        f"/v1/companies/{company}/deactivate", headers=owner.posting("off") | {"If-Match": '"1"'}
    )
    assert off.status_code == 200
    refused = await client.post(
        f"/v1/companies/{company}/branches",
        json={"code": "SUC2", "name": "X"},
        headers=owner.posting("b-off"),
    )
    point = await client.post(
        f"/v1/companies/{company}/branches/{branch_id}/emission-points",
        json={"code": "001", "name": "Caja"},
        headers=owner.posting("p-off"),
    )
    assert [(response.status_code, document(response)["code"]) for response in (refused, point)] == [
        (409, "company_inactive"),
        (409, "company_inactive"),
    ]
```

`services/identity/tests/test_memberships_api.py`:

```python
from dataclasses import dataclass
from typing import final

import pytest
lazy import httpx

from ledger_testing.api import Caller, document, page
lazy from ledger_testing.issuer import LocalIssuer


@final
@dataclass(frozen=True, slots=True)
class Members:
    """The memberships of one company, as its owner (``2001``) or another caller changes them."""

    client: httpx.AsyncClient
    issuer: LocalIssuer
    company: str

    async def assign(self, subject: str, role: str, by: str = "2001") -> httpx.Response:
        return await self.client.put(
            f"/v1/companies/{self.company}/memberships/{subject}",
            json={"role": role},
            headers=Caller(self.issuer, subject=by).bearer(),
        )

    async def revoke(self, subject: str, by: str = "2001") -> httpx.Response:
        return await self.client.delete(
            f"/v1/companies/{self.company}/memberships/{subject}",
            headers=Caller(self.issuer, subject=by).bearer(),
        )

    async def read_company(self, by: str) -> httpx.Response:
        return await self.client.get(
            f"/v1/companies/{self.company}", headers=Caller(self.issuer, subject=by).bearer()
        )


@pytest.fixture
def members(client: httpx.AsyncClient, issuer: LocalIssuer, company: str) -> Members:
    return Members(client, issuer, company)


async def test_an_owner_gives_a_role_changes_it_takes_it_away_and_gives_it_again(members: Members) -> None:
    granted = await members.assign("3001", "viewer")
    made = document(granted)
    assert (granted.status_code, made["role"], made["version"], made["granted_by"]) == (
        201,
        "viewer",
        1,
        "2001",
    )
    same = await members.assign("3001", "viewer")
    assert (same.status_code, document(same)["version"]) == (200, 1)
    changed = await members.assign("3001", "issuer")
    assert (changed.status_code, document(changed)["role"], document(changed)["version"]) == (
        200,
        "issuer",
        2,
    )
    assert (await members.read_company("3001")).status_code == 200
    revoked = await members.revoke("3001")
    assert (revoked.status_code, revoked.content) == (204, b"")
    gone = await members.read_company("3001")
    assert (gone.status_code, document(gone)["code"]) == (404, "company_not_found")
    again = await members.assign("3001", "viewer")
    assert (again.status_code, document(again)["version"], document(again)["id"]) == (201, 4, made["id"])


async def test_the_memberships_of_a_company_are_listed_to_whoever_may_read_them(
    client: httpx.AsyncClient, issuer: LocalIssuer, members: Members
) -> None:
    await members.assign("3001", "accountant")
    await members.assign("3002", "viewer")
    await members.assign("3003", "viewer")
    await members.revoke("3003")
    path = f"/v1/companies/{members.company}/memberships"
    listed = page(await client.get(path, headers=Caller(issuer, subject="3001").bearer()))
    assert sorted(str(item["subject"]) for item in listed["items"]) == ["2001", "3001", "3002"]
    refused = await client.get(path, headers=Caller(issuer, subject="3002").bearer())
    assert (refused.status_code, document(refused)["code"]) == (403, "permission_denied")


async def test_an_admin_cannot_touch_the_owner_role(members: Members) -> None:
    await members.assign("2002", "admin")
    promote = await members.assign("3001", "owner", by="2002")
    demote = await members.assign("2001", "viewer", by="2002")
    revoke = await members.revoke("2001", by="2002")
    for response in (promote, demote, revoke):
        assert (response.status_code, document(response)["code"]) == (403, "owners_only")
    viewer = await members.assign("3001", "viewer", by="2002")
    assert viewer.status_code == 201


async def test_a_company_always_keeps_an_owner(members: Members) -> None:
    leave = await members.revoke("2001")
    step_down = await members.assign("2001", "admin")
    for response in (leave, step_down):
        assert (response.status_code, document(response)["code"]) == (409, "last_owner")
    await members.assign("2002", "owner")
    assert (await members.assign("2001", "admin")).status_code == 200


async def test_a_role_is_given_in_an_active_company_only_and_taken_in_any(
    client: httpx.AsyncClient, owner: Caller, members: Members
) -> None:
    await members.assign("3001", "viewer")
    headers = owner.posting("off") | {"If-Match": '"1"'}
    assert (
        await client.post(f"/v1/companies/{members.company}/deactivate", headers=headers)
    ).status_code == 200
    refused = await members.assign("3002", "viewer")
    assert (refused.status_code, document(refused)["code"]) == (409, "company_inactive")
    assert (await members.revoke("3001")).status_code == 204


@pytest.mark.parametrize(
    ("subject", "payload", "expected"),
    [
        ("bad%20subject", {"role": "viewer"}, (422, "invalid_subject")),
        ("3001", {"role": "emperor"}, (422, "invalid_request")),
        ("3001", {"role": "viewer", "until": "2027"}, (422, "invalid_request")),
    ],
    ids=["subject", "role", "unknown-field"],
)
async def test_a_role_that_cannot_be_given_is_refused(
    members: Members, owner: Caller, subject: str, payload: dict[str, str], expected: tuple[int, str]
) -> None:
    response = await members.client.put(
        f"/v1/companies/{members.company}/memberships/{subject}", json=payload, headers=owner.bearer()
    )
    assert (response.status_code, document(response)["code"]) == expected


async def test_revoking_a_role_nobody_holds_is_not_found(members: Members) -> None:
    response = await members.revoke("4004")
    assert (response.status_code, document(response)["code"]) == (404, "membership_not_found")


async def test_memberships_of_another_organization_are_out_of_reach(
    client: httpx.AsyncClient, issuer: LocalIssuer, company: str
) -> None:
    stranger = Caller(issuer, organization="1002").bearer()
    response = await client.put(
        f"/v1/companies/{company}/memberships/3001", json={"role": "owner"}, headers=stranger
    )
    assert (response.status_code, document(response)["code"]) == (404, "company_not_found")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest services/identity/tests/test_establishments_api.py services/identity/tests/test_memberships_api.py -q --no-cov`
Expected: FAIL: the routes answer 404 `not_found` (they do not exist yet).

- [ ] **Step 3: Implement**

Append to `services/identity/src/ledger_identity/api/models.py` (and add the imports `from ledger_identity.domain.roles import Role`, which pydantic reads at runtime, and, for the `of` methods' annotations only, `lazy from ledger_identity.domain.branches import Branch, EmissionPoint` and `lazy from ledger_identity.domain.membership import Membership`):

```python
class NewBranch(_Shape):
    """A branch to open."""

    code: str = Field(
        min_length=1, max_length=10, description="1 to 10 upper-case letters and digits.", examples=["SUC1"]
    )
    name: str = Field(min_length=1, max_length=200)


class BranchView(_Shape):
    """A branch of a company."""

    id: UUID
    company_id: UUID
    code: str
    name: str
    created_at: datetime

    @classmethod
    def of(cls, branch: Branch) -> Self:
        """The view of ``branch``."""
        return cls(
            id=branch.id,
            company_id=branch.tenant.company,
            code=branch.code,
            name=branch.name,
            created_at=branch.created_at,
        )


class BranchPage(_Shape):
    """Branches in id order, and the cursor of the next page."""

    items: list[BranchView]
    next_cursor: str | None


class NewEmissionPoint(_Shape):
    """An emission point to open."""

    code: str = Field(
        min_length=1, max_length=10, description="1 to 10 upper-case letters and digits.", examples=["001"]
    )
    name: str = Field(min_length=1, max_length=200)


class EmissionPointView(_Shape):
    """An emission point of a branch."""

    id: UUID
    company_id: UUID
    branch_id: UUID
    code: str
    name: str
    created_at: datetime

    @classmethod
    def of(cls, point: EmissionPoint) -> Self:
        """The view of ``point``."""
        return cls(
            id=point.id,
            company_id=point.tenant.company,
            branch_id=point.branch,
            code=point.code,
            name=point.name,
            created_at=point.created_at,
        )


class EmissionPointPage(_Shape):
    """Emission points in id order, and the cursor of the next page."""

    items: list[EmissionPointView]
    next_cursor: str | None


class RoleAssignment(_Shape):
    """The role to hold in a company."""

    role: Role


class MembershipView(_Shape):
    """A principal's role in a company: an active membership, whose last change is the grant of its role."""

    id: UUID
    company_id: UUID
    subject: str = Field(description="The identity provider's id of the user or machine.")
    role: Role
    version: int = Field(description="One more with every change; a role granted again continues it.")
    granted_at: datetime
    granted_by: str

    @classmethod
    def of(cls, membership: Membership) -> Self:
        """The view of ``membership``, which is active."""
        return cls(
            id=membership.id,
            company_id=membership.tenant.company,
            subject=membership.subject,
            role=membership.role,
            version=membership.version,
            granted_at=membership.changed_at,
            granted_by=membership.changed_by,
        )


class MembershipPage(_Shape):
    """Memberships in id order, and the cursor of the next page."""

    items: list[MembershipView]
    next_cursor: str | None
```

`services/identity/src/ledger_identity/api/branches.py`:

```python
"""``/v1/companies/{company_id}/branches``: a company's branches."""

from http import HTTPStatus
from typing import Annotated, Final

from fastapi import APIRouter, Depends
lazy from fastapi import Request, Response

from ledger_identity.api.context import problems
from ledger_identity.api.models import BranchPage, BranchView
from ledger_identity.application import commands, queries
from ledger_identity.domain.ids import BranchId
from ledger_identity.domain.text import parse_code, parse_name
from ledger_kernel.access import Permission
from ledger_kernel.ids import parse_id
from ledger_platform.routes import answer, attempt_of, idempotency_key, json_response, page_request
lazy from ledger_identity.api.context import ApiContext
lazy from ledger_identity.api.models import NewBranch
lazy from ledger_identity.settings import IdentitySettings
lazy from ledger_kernel.pagination import PageRequest
lazy from ledger_platform.authorization import CompanyAccess
lazy from ledger_platform.idempotency import StoredResponse
lazy from ledger_platform.service import Runtime

READ: Final = problems(401, 403, 404, 422)
"""The error answers of a read under a company."""
OPEN: Final = problems(401, 403, 404, 409, 422)
"""The error answers of opening something under a company, under a code."""


def router(context: ApiContext) -> APIRouter:
    """The routes of branches."""
    branches = APIRouter(prefix="/v1/companies/{company_id}/branches", tags=["branches"])
    in_company, runtime = context.in_company, context.runtime

    @branches.post("", status_code=HTTPStatus.CREATED, response_model=BranchView, responses=OPEN)
    async def open_branch(
        body: NewBranch,
        request: Request,
        access: Annotated[CompanyAccess, Depends(in_company)],
        key: Annotated[str, Depends(idempotency_key)],
        current: Annotated[Runtime[IdentitySettings], Depends(runtime)],
    ) -> Response:
        """Open a branch of the company under a code it does not use yet."""
        access.require(Permission.COMPANY_MANAGE)
        code, name = parse_code(body.code), parse_name(body.name)
        attempt = attempt_of(request, access.principal, key, body)
        async with current.database.transaction(access.tenant) as transaction:

            async def open_it() -> StoredResponse:
                unit = context.bind(transaction, current.outbox)
                opened = await commands.open_branch(unit, access.tenant.company, code=code, name=name)
                location = f"/v1/companies/{opened.tenant.company}/branches/{opened.id}"
                return json_response(HTTPStatus.CREATED, BranchView.of(opened), {"Location": location})

            outcome = await context.idempotency.once(transaction, attempt, open_it)
        return answer(outcome)

    @branches.get("", response_model=BranchPage, responses=READ)
    async def list_branches(
        access: Annotated[CompanyAccess, Depends(in_company)],
        page: Annotated[PageRequest, Depends(page_request)],
        current: Annotated[Runtime[IdentitySettings], Depends(runtime)],
    ) -> BranchPage:
        """The company's branches."""
        access.require(Permission.COMPANY_READ)
        async with current.database.transaction(access.tenant) as transaction:
            found = await queries.branches(
                context.bind(transaction, current.outbox), access.tenant.company, page
            )
        return BranchPage(
            items=[BranchView.of(branch) for branch in found.items], next_cursor=found.next_cursor
        )

    @branches.get("/{branch_id}", response_model=BranchView, responses=READ)
    async def read_branch(
        branch_id: str,
        access: Annotated[CompanyAccess, Depends(in_company)],
        current: Annotated[Runtime[IdentitySettings], Depends(runtime)],
    ) -> BranchView:
        """A branch of the company."""
        access.require(Permission.COMPANY_READ)
        branch = BranchId(parse_id(branch_id))
        async with current.database.transaction(access.tenant) as transaction:
            found = await queries.branch(
                context.bind(transaction, current.outbox), access.tenant.company, branch
            )
        return BranchView.of(found)

    return branches
```

`services/identity/src/ledger_identity/api/emission_points.py`:

```python
"""``/v1/companies/{company_id}/branches/{branch_id}/emission-points``: the emission points of a branch."""

from dataclasses import dataclass
from http import HTTPStatus
from typing import Annotated, final

from fastapi import APIRouter, Depends
lazy from fastapi import Request, Response

from ledger_identity.api.branches import OPEN, READ
from ledger_identity.api.models import EmissionPointPage, EmissionPointView
from ledger_identity.application import commands, queries
from ledger_identity.domain.ids import BranchId, EmissionPointId
from ledger_identity.domain.text import parse_code, parse_name
from ledger_kernel.access import Permission
from ledger_kernel.ids import parse_id
from ledger_platform.routes import answer, attempt_of, idempotency_key, json_response, page_request
lazy from ledger_identity.api.context import ApiContext
lazy from ledger_identity.api.models import NewEmissionPoint
lazy from ledger_identity.settings import IdentitySettings
lazy from ledger_kernel.pagination import PageRequest
lazy from ledger_platform.authorization import CompanyAccess
lazy from ledger_platform.idempotency import StoredResponse
lazy from ledger_platform.service import Runtime


@final
@dataclass(frozen=True, slots=True)
class InBranch:
    """The branch a request's path names, and what the caller may do in the branch's company."""

    access: CompanyAccess
    branch: BranchId


def router(context: ApiContext) -> APIRouter:
    """The routes of emission points."""
    points = APIRouter(
        prefix="/v1/companies/{company_id}/branches/{branch_id}/emission-points", tags=["emission points"]
    )
    runtime = context.runtime

    async def in_branch(
        branch_id: str, access: Annotated[CompanyAccess, Depends(context.in_company)]
    ) -> InBranch:
        return InBranch(access, BranchId(parse_id(branch_id)))

    @points.post("", status_code=HTTPStatus.CREATED, response_model=EmissionPointView, responses=OPEN)
    async def open_emission_point(
        body: NewEmissionPoint,
        request: Request,
        place: Annotated[InBranch, Depends(in_branch)],
        key: Annotated[str, Depends(idempotency_key)],
        current: Annotated[Runtime[IdentitySettings], Depends(runtime)],
    ) -> Response:
        """Open an emission point of the branch under a code the branch does not use yet."""
        access = place.access
        access.require(Permission.COMPANY_MANAGE)
        code, name = parse_code(body.code), parse_name(body.name)
        attempt = attempt_of(request, access.principal, key, body)
        async with current.database.transaction(access.tenant) as transaction:

            async def open_it() -> StoredResponse:
                unit = context.bind(transaction, current.outbox)
                opened = await commands.open_emission_point(
                    unit, access.tenant.company, place.branch, code=code, name=name
                )
                branch_path = f"/v1/companies/{opened.tenant.company}/branches/{opened.branch}"
                location = f"{branch_path}/emission-points/{opened.id}"
                return json_response(HTTPStatus.CREATED, EmissionPointView.of(opened), {"Location": location})

            outcome = await context.idempotency.once(transaction, attempt, open_it)
        return answer(outcome)

    @points.get("", response_model=EmissionPointPage, responses=READ)
    async def list_emission_points(
        place: Annotated[InBranch, Depends(in_branch)],
        page: Annotated[PageRequest, Depends(page_request)],
        current: Annotated[Runtime[IdentitySettings], Depends(runtime)],
    ) -> EmissionPointPage:
        """The emission points of the branch."""
        access = place.access
        access.require(Permission.COMPANY_READ)
        async with current.database.transaction(access.tenant) as transaction:
            unit = context.bind(transaction, current.outbox)
            found = await queries.emission_points(unit, access.tenant.company, place.branch, page)
        items = [EmissionPointView.of(point) for point in found.items]
        return EmissionPointPage(items=items, next_cursor=found.next_cursor)

    @points.get("/{emission_point_id}", response_model=EmissionPointView, responses=READ)
    async def read_emission_point(
        emission_point_id: str,
        place: Annotated[InBranch, Depends(in_branch)],
        current: Annotated[Runtime[IdentitySettings], Depends(runtime)],
    ) -> EmissionPointView:
        """An emission point of the branch."""
        access = place.access
        access.require(Permission.COMPANY_READ)
        point = EmissionPointId(parse_id(emission_point_id))
        async with current.database.transaction(access.tenant) as transaction:
            unit = context.bind(transaction, current.outbox)
            found = await queries.emission_point(unit, access.tenant.company, place.branch, point)
        return EmissionPointView.of(found)

    return points
```

`services/identity/src/ledger_identity/api/memberships.py`:

```python
"""``/v1/companies/{company_id}/memberships``: who holds which role in a company."""

from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, Depends
lazy from fastapi import Response

from ledger_identity.api.context import problems
from ledger_identity.api.models import MembershipPage, MembershipView
from ledger_identity.application import commands, queries
from ledger_kernel.access import Permission, parse_subject
from ledger_platform.routes import page_request
lazy from ledger_identity.api.context import ApiContext
lazy from ledger_identity.api.models import RoleAssignment
lazy from ledger_identity.settings import IdentitySettings
lazy from ledger_kernel.pagination import PageRequest
lazy from ledger_platform.authorization import CompanyAccess
lazy from ledger_platform.service import Runtime


def router(context: ApiContext) -> APIRouter:
    """The routes of memberships."""
    memberships = APIRouter(prefix="/v1/companies/{company_id}/memberships", tags=["memberships"])
    in_company, runtime = context.in_company, context.runtime

    @memberships.get("", response_model=MembershipPage, responses=problems(401, 403, 404, 422))
    async def list_memberships(
        access: Annotated[CompanyAccess, Depends(in_company)],
        page: Annotated[PageRequest, Depends(page_request)],
        current: Annotated[Runtime[IdentitySettings], Depends(runtime)],
    ) -> MembershipPage:
        """Who holds which role in the company."""
        access.require(Permission.MEMBERSHIP_READ)
        async with current.database.transaction(access.tenant) as transaction:
            found = await queries.memberships(
                context.bind(transaction, current.outbox), access.tenant.company, page
            )
        items = [MembershipView.of(membership) for membership in found.items]
        return MembershipPage(items=items, next_cursor=found.next_cursor)

    @memberships.put(
        "/{subject}",
        response_model=MembershipView,
        responses=problems(401, 403, 404, 409, 422)
        | {
            HTTPStatus.CREATED.value: {
                "model": MembershipView,
                "description": "The subject held no role before",
            }
        },
    )
    async def assign_role(
        subject: str,
        body: RoleAssignment,
        response: Response,
        access: Annotated[CompanyAccess, Depends(in_company)],
        current: Annotated[Runtime[IdentitySettings], Depends(runtime)],
    ) -> MembershipView:
        """Give ``subject`` a role in the company, or change the one it holds: 201 when it held none.

        The caller's permission is checked here, and again on its membership as it is once the company is
        locked: a role it lost meanwhile does not count.
        """
        access.require(Permission.MEMBERSHIP_MANAGE)
        member = parse_subject(subject)
        async with current.database.transaction(access.tenant) as transaction:
            granted = await commands.grant_role(
                context.bind(transaction, current.outbox),
                access.tenant.company,
                subject=member,
                role=body.role,
                actor=access.principal.subject,
            )
        response.status_code = HTTPStatus.CREATED if granted.created else HTTPStatus.OK
        return MembershipView.of(granted.membership)

    @memberships.delete(
        "/{subject}", status_code=HTTPStatus.NO_CONTENT, responses=problems(401, 403, 404, 409, 422)
    )
    async def revoke_role(
        subject: str,
        access: Annotated[CompanyAccess, Depends(in_company)],
        current: Annotated[Runtime[IdentitySettings], Depends(runtime)],
    ) -> None:
        """Take ``subject``'s role in the company away (its membership is kept, revoked)."""
        access.require(Permission.MEMBERSHIP_MANAGE)
        member = parse_subject(subject)
        async with current.database.transaction(access.tenant) as transaction:
            await commands.revoke_role(
                context.bind(transaction, current.outbox),
                access.tenant.company,
                subject=member,
                actor=access.principal.subject,
            )

    return memberships
```

`services/identity/src/ledger_identity/api/rest.py` — every router:

```python
"""identity's REST API: every router, under ``/v1``."""

lazy from collections.abc import Sequence

lazy from fastapi import APIRouter

from ledger_identity.api import branches, companies, emission_points, memberships, organization
lazy from ledger_identity.api.context import ApiContext


def routes(context: ApiContext) -> Sequence[APIRouter]:
    """The routers of identity's public API."""
    return (
        organization.router(context),
        companies.router(context),
        branches.router(context),
        emission_points.router(context),
        memberships.router(context),
    )
```

- [ ] **Step 4: Publish the document and run the tests**

Run: `uv run poe openapi && uv run pytest services/identity -q --no-cov`
Expected: `openapi.json` gains the new paths; PASS (including `test_openapi.py`: every POST still takes an `Idempotency-Key`).

- [ ] **Step 5: Gate**

Run: `uv run poe fmt && uv run poe check`
Expected: every step passes, coverage 100 %.

- [ ] **Step 6: Commit**

```bash
git add services/identity
git commit -m "feat(identity): public API for branches, emission points and memberships

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 15: Identity's gRPC query service

**Files:**
- Create: `services/identity/src/ledger_identity/api/rpc.py`
- Modify: `services/identity/src/ledger_identity/main.py`, `services/identity/pyproject.toml`
- Test: `services/identity/tests/test_rpc.py`

**Interfaces:**
- Consumes: `IdentityQueryServiceServicer`, `add_IdentityQueryServiceServicer_to_server`, the request/response messages (Task 7); `queries.company`, `queries.permissions_of` (Task 10: a deactivated company's reading permissions only); `BindUnit` (Task 13); `GrpcServices`, `Runtime` (`ledger_platform.service`); `serve_grpc`'s `ErrorInterceptor` (M1) turns `InvalidIdError`, `InvalidSubjectError` and `CompanyNotFoundError` into `INVALID_ARGUMENT`/`NOT_FOUND` with their codes; `call_unary`, `RemotePermissions`, `CachedPermissions` (Tasks 6, 8) as the clients in the tests; the fixtures `client`, `owner`, `company`, `service` (Task 13).
- Produces: `ledger_identity.api.rpc.IdentityQuery(database: Database, outbox: Outbox, bind: BindUnit)` (the servicer) and `services(bind: BindUnit) -> GrpcServices[IdentitySettings]`; identity's `Service` serves it on `grpc_port`. It authenticates no caller until the mesh does (Decision 14's accepted risk): the port is never published outside the services' network.

- [ ] **Step 1: Dependency**

`services/identity/pyproject.toml` — add `"grpcio>=1.84.0"` to `dependencies`.

Run: `uv lock && uv sync`

- [ ] **Step 2: Write the failing tests**

`services/identity/tests/test_rpc.py`:

```python
from typing import Final
from uuid import UUID
lazy from collections.abc import AsyncGenerator

import grpc
import pytest
lazy import httpx

from ledger.identity.v1.identity_query_pb2 import (
    COMPANY_STATUS_ACTIVE,
    COMPANY_STATUS_INACTIVE,
    GetCompanyRequest,
    GetPermissionsRequest,
)
from ledger.identity.v1.identity_query_pb2_grpc import IdentityQueryServiceStub
from ledger_kernel.access import Permission, Principal, SubjectId
from ledger_kernel.ids import CompanyId
from ledger_platform.authentication import organization_of
from ledger_platform.authorization import CachedPermissions
from ledger_platform.grpc_client import RemoteDefectError, RemoteNotFoundError, call_unary
from ledger_platform.identity_client import RemotePermissions
lazy from ledger_identity.settings import IdentitySettings
lazy from ledger_platform.service import Service
lazy from ledger_testing.api import Caller

GET_COMPANY: Final = "/ledger.identity.v1.IdentityQueryService/GetCompany"
GET_PERMISSIONS: Final = "/ledger.identity.v1.IdentityQueryService/GetPermissions"
ORGANIZATION: Final = str(organization_of("1001"))
STRANGERS: Final = str(organization_of("1002"))


@pytest.fixture
async def channel(service: Service[IdentitySettings]) -> AsyncGenerator[grpc.aio.Channel]:
    assert service.grpc_port is not None
    async with grpc.aio.insecure_channel(f"127.0.0.1:{service.grpc_port}") as opened:
        yield opened


async def permissions_asked(
    channel: grpc.aio.Channel, organization: str, company: str, subject: str
) -> list[str]:
    request = GetPermissionsRequest(organization_id=organization, company_id=company, subject=subject)
    answer = await call_unary(IdentityQueryServiceStub(channel).GetPermissions, request, rpc=GET_PERMISSIONS)
    return list(answer.permissions)


async def test_a_company_is_answered_as_identity_holds_it(company: str, channel: grpc.aio.Channel) -> None:
    request = GetCompanyRequest(organization_id=ORGANIZATION, company_id=company)
    answer = await call_unary(IdentityQueryServiceStub(channel).GetCompany, request, rpc=GET_COMPANY)
    found = answer.company
    assert (found.id, found.organization_id, found.tax_id, found.legal_name, found.status, found.version) == (
        company,
        ORGANIZATION,
        "101850043",
        "Duarte",
        COMPANY_STATUS_ACTIVE,
        1,
    )


async def test_a_deactivated_company_is_answered_as_inactive_and_lets_others_read_only(
    client: httpx.AsyncClient, owner: Caller, company: str, channel: grpc.aio.Channel
) -> None:
    headers = owner.posting("off") | {"If-Match": '"1"'}
    assert (await client.post(f"/v1/companies/{company}/deactivate", headers=headers)).status_code == 200
    request = GetCompanyRequest(organization_id=ORGANIZATION, company_id=company)
    answer = await call_unary(IdentityQueryServiceStub(channel).GetCompany, request, rpc=GET_COMPANY)
    assert (answer.company.status, answer.company.version) == (COMPANY_STATUS_INACTIVE, 2)
    assert await permissions_asked(channel, ORGANIZATION, company, "2001") == [
        "company.read",
        "invoice.read",
        "membership.read",
    ]


async def test_a_company_of_another_organization_is_not_found(
    company: str, channel: grpc.aio.Channel
) -> None:
    request = GetCompanyRequest(organization_id=STRANGERS, company_id=company)
    with pytest.raises(RemoteNotFoundError) as caught:
        await call_unary(IdentityQueryServiceStub(channel).GetCompany, request, rpc=GET_COMPANY)
    assert caught.value.remote_code == "company_not_found"


@pytest.mark.parametrize(
    ("organization", "company_id", "subject", "code"),
    [
        ("not-an-id", "0192f7a4-6b1e-7c3d-8e4f-5a6b7c8d9e0f", "2001", "invalid_id"),
        (ORGANIZATION, "", "2001", "invalid_id"),
        (ORGANIZATION, "0192f7a4-6b1e-7c3d-8e4f-5a6b7c8d9e0f", "not a subject", "invalid_subject"),
    ],
    ids=["organization", "company", "subject"],
)
async def test_a_question_that_cannot_be_read_is_an_invalid_argument(
    channel: grpc.aio.Channel, organization: str, company_id: str, subject: str, code: str
) -> None:
    with pytest.raises(RemoteDefectError) as caught:
        await permissions_asked(channel, organization, company_id, subject)
    assert (caught.value.status, caught.value.remote_code) == (grpc.StatusCode.INVALID_ARGUMENT, code)


async def test_permissions_are_the_role_s_in_the_company_and_none_elsewhere(
    company: str, channel: grpc.aio.Channel
) -> None:
    assert await permissions_asked(channel, ORGANIZATION, company, "2001") == sorted(
        permission.value for permission in Permission
    )
    assert await permissions_asked(channel, ORGANIZATION, company, "2002") == []
    assert await permissions_asked(channel, STRANGERS, company, "2001") == []


async def test_the_platform_s_own_client_reads_identity_s_answers(
    client: httpx.AsyncClient, owner: Caller, company: str, channel: grpc.aio.Channel
) -> None:
    company_id = CompanyId(UUID(company))
    founder = Principal(
        subject=SubjectId("2001"), organization=organization_of("1001"), provider_organization="1001"
    )
    cached = CachedPermissions(RemotePermissions(channel))
    assert await cached.permissions(founder, company_id) == frozenset(Permission)
    granted = await client.put(
        f"/v1/companies/{company}/memberships/3001", json={"role": "issuer"}, headers=owner.bearer()
    )
    assert granted.status_code == 201
    issuer = Principal(
        subject=SubjectId("3001"), organization=founder.organization, provider_organization="1001"
    )
    assert await cached.permissions(issuer, company_id) == frozenset(
        {Permission.COMPANY_READ, Permission.INVOICE_READ, Permission.INVOICE_ISSUE}
    )
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest services/identity/tests/test_rpc.py -q --no-cov`
Expected: FAIL at `assert service.grpc_port is not None`: the service runs no gRPC server yet.

- [ ] **Step 4: Implement**

`services/identity/src/ledger_identity/api/rpc.py`:

```python
"""``ledger.identity.v1.IdentityQueryService``: what other services ask identity, synchronously (§3, §8).

Every request names the organization it is about, and each answer is read in a transaction scoped to that
organization: a company of another organization does not exist for it. Callers are other services, which the
mesh authenticates (Linkerd, deployment milestone); until then nothing here authenticates a caller, so the
port must never be reachable from outside the services' own network (an accepted risk the spec records). The
ids of a request are checked like any client's input, and a malformed one is ``INVALID_ARGUMENT`` with its
code. The permissions answered are what other services may let the principal do: in a deactivated company,
reading only.
"""

from typing import Final, final, override
lazy from collections.abc import Mapping

lazy import grpc

from ledger.identity.v1 import identity_query_pb2 as query
from ledger.identity.v1.identity_query_pb2_grpc import (
    IdentityQueryServiceServicer,
    add_IdentityQueryServiceServicer_to_server,
)
from ledger_identity.application import queries
from ledger_identity.domain.company import CompanyStatus
from ledger_kernel.access import parse_subject
from ledger_kernel.ids import CompanyId, OrganizationId, Tenant, parse_id
lazy from ledger_identity.api.context import BindUnit
lazy from ledger_identity.domain.company import Company
lazy from ledger_identity.settings import IdentitySettings
lazy from ledger_platform.database import Database
lazy from ledger_platform.outbox import Outbox
lazy from ledger_platform.service import GrpcServices, Runtime

_STATUS: Final[Mapping[CompanyStatus, query.CompanyStatus.ValueType]] = {
    CompanyStatus.ACTIVE: query.COMPANY_STATUS_ACTIVE,
    CompanyStatus.INACTIVE: query.COMPANY_STATUS_INACTIVE,
}

type _CompanyContext = grpc.aio.ServicerContext[query.GetCompanyRequest, query.GetCompanyResponse]
type _PermissionsContext = grpc.aio.ServicerContext[query.GetPermissionsRequest, query.GetPermissionsResponse]


def _wire(company: Company) -> query.Company:
    return query.Company(
        id=str(company.id),
        organization_id=str(company.organization),
        tax_id=str(company.tax_id),
        legal_name=company.legal_name,
        status=_STATUS[company.status],
        version=company.version,
    )


@final
class IdentityQuery(IdentityQueryServiceServicer):
    """identity's answers to other services."""

    def __init__(self, database: Database, outbox: Outbox, bind: BindUnit) -> None:
        self._database = database
        self._outbox = outbox
        self._bind = bind

    @override
    async def GetCompany(
        self, request: query.GetCompanyRequest, context: _CompanyContext
    ) -> query.GetCompanyResponse:
        del context
        tenant = Tenant(
            OrganizationId(parse_id(request.organization_id)), CompanyId(parse_id(request.company_id))
        )
        async with self._database.transaction(tenant) as transaction:
            company = await queries.company(self._bind(transaction, self._outbox), tenant.company)
        return query.GetCompanyResponse(company=_wire(company))

    @override
    async def GetPermissions(
        self, request: query.GetPermissionsRequest, context: _PermissionsContext
    ) -> query.GetPermissionsResponse:
        del context
        organization = OrganizationId(parse_id(request.organization_id))
        company = CompanyId(parse_id(request.company_id))
        subject = parse_subject(request.subject)
        async with self._database.transaction(organization) as transaction:
            permissions = await queries.permissions_of(
                self._bind(transaction, self._outbox), company, subject
            )
        return query.GetPermissionsResponse(
            permissions=sorted(permission.value for permission in permissions)
        )


def services(bind: BindUnit) -> GrpcServices[IdentitySettings]:
    """What identity's gRPC server serves, once the service runs."""

    def register(runtime: Runtime[IdentitySettings], server: grpc.aio.Server) -> None:
        add_IdentityQueryServiceServicer_to_server(
            IdentityQuery(runtime.database, runtime.outbox, bind), server
        )

    return register
```

`services/identity/src/ledger_identity/main.py` — import `from ledger_identity.api.rpc import services` and serve it (the `checks=` and resources arguments stay as Task 13 left them):

```python
    return Service(
        settings=settings,
        version=VERSION,
        interfaces=Interfaces(http=http, grpc=services(bind)),
        events=Events(streams=(IDENTITY,)),
        checks={"migrations": migrations_at_head},
        resources=(keys.opened,),
    )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest services/identity -q --no-cov`
Expected: PASS.

- [ ] **Step 6: Gate**

Run: `uv run poe fmt && uv run poe check`
Expected: every step passes, coverage 100 %.

- [ ] **Step 7: Commit**

```bash
git add services/identity uv.lock
git commit -m "feat(identity): IdentityQueryService over gRPC, read inside the asking organization

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 16: Identity's events, end to end

This task adds no production code: it proves, against the running service, that what Tasks 12-14 store in the outbox reaches NATS as §4.1 says, on the trace of the request that caused it, and that the platform's membership follower (Task 8) hears identity's real events.

**Files:**
- Test: `services/identity/tests/test_events.py`

**Interfaces:**
- Consumes: the `service`, `client`, `owner`, `company`, `database` and `nats_url` fixtures and `ledger_testing.api` (Tasks 11, 13); `IDENTITY`, `SOURCE` (Task 12); `Envelope.from_headers`; `OutboxRecord`; `MembershipChanges`, `Subscriber` (Task 8); `Forgotten` (`ledger_testing.caches`, Task 8); `eventually` (Task 2); the wire messages (Task 7); `organization_of`.
- Produces: nothing new.

- [ ] **Step 1: Write the tests**

`services/identity/tests/test_events.py`:

```python
import asyncio
from typing import Final, final
from uuid import UUID
lazy from collections.abc import AsyncGenerator, Callable

import pytest
from nats.aio.client import Client
from nats.js import JetStreamContext
from sqlalchemy import func, select
lazy import httpx
lazy from nats.aio.msg import Msg

from ledger.events.identity.v1 import events_pb2 as wire
from ledger_contracts.envelope import Envelope
from ledger_identity.adapters.events import IDENTITY, SOURCE
from ledger_kernel.access import SubjectId
from ledger_kernel.ids import CompanyId
from ledger_platform.authentication import organization_of
from ledger_platform.identity_client import MembershipChanges
from ledger_platform.outbox import OutboxRecord
from ledger_testing.api import created
from ledger_testing.caches import Forgotten
from ledger_testing.waiting import eventually
lazy from ledger_identity.settings import IdentitySettings
lazy from ledger_platform.database import Database
lazy from ledger_platform.identity_client import Subscriber
lazy from ledger_platform.service import Service
lazy from ledger_testing.api import Caller

ORGANIZATION: Final = organization_of("1001")
TRACE_ID: Final = "4bf92f3577b34da6a3ce929d0e0e4736"
TRACEPARENT: Final = f"00-{TRACE_ID}-b7ad6b7169203331-01"
DUARTE: Final = {"tax_id": "101850043", "legal_name": "Duarte"}


@final
class Heard:
    """Every identity event a plain NATS subscriber receives, as envelopes."""

    def __init__(self) -> None:
        self.envelopes: list[Envelope] = []
        self.arrived = asyncio.Event()

    async def keep(self, message: Msg) -> None:
        self.envelopes.append(Envelope.from_headers(message.headers or {}, message.data))
        self.arrived.set()

    def about(self, company_id: str) -> list[Envelope]:
        return [
            envelope
            for envelope in self.envelopes
            if envelope.tenant is not None and str(envelope.tenant.company) == company_id
        ]


@pytest.fixture
async def nats(nats_url: str) -> AsyncGenerator[Client]:
    client = Client()
    await client.connect(nats_url)
    try:
        yield client
    finally:
        await client.close()


@pytest.fixture
async def heard(nats: Client) -> Heard:
    listener = Heard()
    subscriber: Subscriber = nats
    await subscriber.subscribe("identity.>", cb=listener.keep)
    await nats.flush()
    return listener


async def traced_company(client: httpx.AsyncClient, owner: Caller) -> str:
    """A company registered by a request on the trace ``TRACEPARENT`` names (the same key every time)."""
    headers = owner.posting("c-traced") | {"traceparent": TRACEPARENT}
    return await created(client, "/v1/companies", DUARTE, headers)


async def nothing_waits_in_the_outbox(database: Database) -> bool:
    async with database.transaction(None) as unit:
        pending = (await unit.connection.execute(select(func.count()).select_from(OutboxRecord))).scalar_one()
    return pending == 0


async def within(seconds: float, arrived: asyncio.Event, check: Callable[[], bool]) -> None:
    """Wait at most ``seconds`` for ``check`` to hold, looking again each time something ``arrived``."""
    async with asyncio.timeout(seconds):
        while True:
            arrived.clear()  # before looking: whatever arrives after the look wakes the wait
            if check():
                return
            await arrived.wait()


async def test_identity_declares_its_stream(service: Service[IdentitySettings], nats: Client) -> None:
    del service
    info = await JetStreamContext(nats).stream_info(IDENTITY.name)
    assert info.config.subjects == ["identity.>"]


async def test_a_new_company_and_its_owner_go_out_in_their_envelopes(
    client: httpx.AsyncClient, owner: Caller, heard: Heard
) -> None:
    company_id = await traced_company(client, owner)
    await within(10, heard.arrived, lambda: len(heard.about(company_id)) == 2)
    created_event, granted = heard.about(company_id)
    assert (created_event.source, created_event.type, granted.type) == (
        SOURCE,
        "ledger.events.identity.v1.CompanyCreated",
        "ledger.events.identity.v1.MembershipGranted",
    )
    assert created_event.tenant is not None
    assert created_event.tenant.organization == ORGANIZATION
    payload = created_event.unwrap(wire.CompanyCreated)
    assert (payload.company_id, payload.tax_id, payload.legal_name, payload.version) == (
        company_id,
        "101850043",
        "Duarte",
        1,
    )
    founder = granted.unwrap(wire.MembershipGranted)
    assert (founder.subject, founder.role, founder.previous_role, founder.version) == (
        "2001",
        wire.ROLE_OWNER,
        wire.ROLE_UNSPECIFIED,
        1,
    )


async def test_an_event_continues_the_trace_of_the_request_that_caused_it(
    client: httpx.AsyncClient, owner: Caller, heard: Heard
) -> None:
    company_id = await traced_company(client, owner)
    await within(10, heard.arrived, lambda: len(heard.about(company_id)) == 2)
    for envelope in heard.about(company_id):
        assert envelope.traceparent is not None
        assert envelope.traceparent.split("-")[1] == TRACE_ID


async def test_a_replayed_post_announces_nothing_more(
    client: httpx.AsyncClient, owner: Caller, heard: Heard, nats: Client, database: Database
) -> None:
    company_id = await traced_company(client, owner)
    await within(10, heard.arrived, lambda: len(heard.about(company_id)) == 2)
    stream = JetStreamContext(nats)
    before = (await stream.stream_info(IDENTITY.name)).state.last_seq
    assert (await traced_company(client, owner)) == company_id  # the same key and body: replayed
    # The replay's transaction committed before it answered: whatever it stored is in the outbox, and the
    # relay deletes a row only once JetStream has the event. An empty outbox leaves nothing more to come.
    await eventually(lambda: nothing_waits_in_the_outbox(database))
    assert (await stream.stream_info(IDENTITY.name)).state.last_seq == before
    assert len(heard.about(company_id)) == 2


async def test_every_replica_following_memberships_hears_grants_and_revocations(
    client: httpx.AsyncClient, owner: Caller, company: str, nats: Client
) -> None:
    cache = Forgotten()
    await MembershipChanges(cache).follow(nats)
    await nats.flush()
    path = f"/v1/companies/{company}/memberships/3001"
    assert (await client.put(path, json={"role": "viewer"}, headers=owner.bearer())).status_code == 201
    assert (await client.delete(path, headers=owner.bearer())).status_code == 204
    expected = (ORGANIZATION, CompanyId(UUID(company)), SubjectId("3001"))
    await within(10, cache.arrived, lambda: cache.forgotten.count(expected) == 2)
```

- [ ] **Step 2: Run the tests**

Run: `uv run pytest services/identity/tests/test_events.py -q --no-cov`
Expected: PASS. Nothing is to be implemented: a failure is a defect of Tasks 12-14 or of the chassis, fixed where it lies (with a test that pins it) and committed with this task; the commit below adds whatever such a fix touched under `services/identity` and `libs/platform`.

- [ ] **Step 3: Gate**

Run: `uv run poe fmt && uv run poe check`
Expected: every step passes, coverage 100 %.

- [ ] **Step 4: Commit**

```bash
git add services/identity libs/platform
git commit -m "test(identity): events go out in their envelopes, once, on the request's trace

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 17: The production image, the local stack and CI

**Files:**
- Create: `libs/platform/src/ledger_platform/probe.py`, `services/identity/Dockerfile`, `.dockerignore`, `deploy/local/identity.env`
- Modify: `libs/platform/pyproject.toml`, `services/identity/pyproject.toml`, `pyproject.toml` (root), `deploy/local/compose.yaml`, `deploy/local/postgres/init.sql`, `Tiltfile`, `.github/workflows/ci.yml`
- Test: `libs/platform/tests/test_probe.py`

**Interfaces:**
- Consumes: `create_app` (Granian's factory) and `ledger-identity-migrate` (Tasks 11, 13); the compose services `postgres` and `nats` (M1).
- Produces: `ledger_platform.probe`: `TIMEOUT = 2.0`, `healthy(url: str, *, timeout: float = TIMEOUT) -> bool`, `main() -> int` (console script `ledger-probe URL`: 0 healthy, 1 not, 2 misused). Image `ledger-identity:local` (distroless, `nonroot`, Granian on uvloop, health check on `/readyz`). Compose services `identity-migrate` (one-shot) and `identity` (`127.0.0.1:8000` HTTP; gRPC on 50051 inside the compose network only, never published: Decision 14), database `identity` owned by role `identity` (no superuser, every tenant table under forced row-level security).

- [ ] **Step 1: Write the failing test of the probe**

`libs/platform/tests/test_probe.py`:

```python
import asyncio
import contextlib
from contextlib import asynccontextmanager
from typing import Final
lazy from collections.abc import AsyncGenerator

import pytest
from pydantic import TypeAdapter

from ledger_platform.probe import healthy, main

ADDRESS: Final = TypeAdapter(tuple[str, int] | tuple[str, int, int, int])


@asynccontextmanager
async def answering(head: bytes, *, delay: float = 0.0) -> AsyncGenerator[str]:
    """A server on 127.0.0.1 that answers every request with ``head`` (after ``delay`` seconds); its URL."""

    handlers: set[asyncio.Task[None]] = set()

    async def answer(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        with contextlib.suppress(ConnectionError, asyncio.IncompleteReadError):
            await reader.readuntil(b"\r\n\r\n")
            await asyncio.sleep(delay)
            writer.write(head)
            await writer.drain()
        writer.close()

    def accept(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        handler = asyncio.create_task(answer(reader, writer))
        handlers.add(handler)
        handler.add_done_callback(handlers.discard)

    server = await asyncio.start_server(accept, "127.0.0.1", 0)
    port = ADDRESS.validate_python(server.sockets[0].getsockname())[1]
    try:
        yield f"http://127.0.0.1:{port}/readyz"
    finally:
        server.close()
        server.close_clients()
        pending = tuple(handlers)
        for handler in pending:
            handler.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        await server.wait_closed()


OK: Final = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok"
UNAVAILABLE: Final = b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"


async def test_a_service_that_answers_200_is_healthy() -> None:
    async with answering(OK) as url:
        assert await asyncio.to_thread(healthy, url)


@pytest.mark.parametrize("head", [UNAVAILABLE, b"NOT HTTP AT ALL\r\n\r\n"], ids=["503", "garbage"])
async def test_a_service_that_answers_anything_else_is_not(head: bytes) -> None:
    async with answering(head) as url:
        assert not await asyncio.to_thread(healthy, url)


async def test_a_service_that_does_not_answer_in_time_is_not_healthy() -> None:
    async with answering(OK, delay=5.0) as url:
        assert not await asyncio.to_thread(healthy, url, timeout=0.2)


def test_a_service_that_is_not_there_is_not_healthy() -> None:
    assert not healthy("http://127.0.0.1:1/readyz", timeout=0.5)
    assert not healthy("http://127.0.0.1:notaport/readyz")


async def test_the_command_line_says_healthy_with_its_exit_code(monkeypatch: pytest.MonkeyPatch) -> None:
    async with answering(OK) as url:
        monkeypatch.setattr("sys.argv", ["ledger-probe", url])
        assert await asyncio.to_thread(main) == 0
    async with answering(UNAVAILABLE) as url:
        monkeypatch.setattr("sys.argv", ["ledger-probe", url])
        assert await asyncio.to_thread(main) == 1


@pytest.mark.parametrize("arguments", [[], ["https://example.com/"], ["http://a/", "http://b/"]])
def test_the_command_line_takes_one_plain_http_url(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], arguments: list[str]
) -> None:
    monkeypatch.setattr("sys.argv", ["ledger-probe", *arguments])
    assert main() == 2
    assert capsys.readouterr().err == "usage: ledger-probe http://HOST:PORT/PATH\n"
```

Run: `uv run pytest libs/platform/tests/test_probe.py -q --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'ledger_platform.probe'`.

- [ ] **Step 2: Implement the probe**

`libs/platform/src/ledger_platform/probe.py`:

```python
"""``ledger-probe URL``: exit 0 when ``URL`` answers 200 within two seconds, 1 when not, 2 when misused.

A service's runtime image has no shell, curl or wget, only the service's Python: its container health check is
this console script. Plain HTTP to the container itself, standard library only.
"""

import http.client
import sys
from typing import Final
from urllib.parse import urlsplit

TIMEOUT: Final = 2.0
_OK: Final = 200
_UNHEALTHY: Final = 1
_MISUSED: Final = 2
_USAGE: Final = "usage: ledger-probe http://HOST:PORT/PATH\n"


def healthy(url: str, *, timeout: float = TIMEOUT) -> bool:
    """Whether ``url`` (``http://``) answers 200 within ``timeout`` seconds; anything else is not healthy."""
    parts = urlsplit(url)
    try:
        connection = http.client.HTTPConnection(parts.hostname or "", parts.port, timeout=timeout)
    except ValueError:  # a port that is not a number
        return False
    try:
        connection.request("GET", parts.path or "/")
        return connection.getresponse().status == _OK
    except OSError, http.client.HTTPException:  # refused, reset, timed out, not HTTP
        return False
    finally:
        connection.close()


def main() -> int:
    """The console script: one ``http://`` URL, the exit code says whether it is healthy."""
    match sys.argv[1:]:
        case [url] if url.startswith("http://"):
            return 0 if healthy(url) else _UNHEALTHY
        case _:
            sys.stderr.write(_USAGE)
            return _MISUSED
```

`libs/platform/pyproject.toml`:

```toml
[project.scripts]
ledger-probe = "ledger_platform.probe:main"
```

Root `pyproject.toml`, contract "Platform layers", foundations layer (re-check the line first: it keeps `supervision` and any module Milestone 1's final review added there):

```toml
    "settings | options | lifecycle | supervision | probe",
```

Run: `uv sync && uv run pytest libs/platform/tests/test_probe.py -q --no-cov`
Expected: PASS.

- [ ] **Step 3: The image**

`services/identity/pyproject.toml` — the server, which no module imports (deptry is told so):

```toml
dependencies = [
    "ledger-kernel",
    "ledger-contracts",
    "ledger-platform",
    "alembic>=1.20.0",
    "fastapi>=0.143.0",
    "granian[uvloop]>=2.8.4",
    "grpcio>=1.84.0",
    "protobuf>=7.36.2",
    "pydantic>=2.14.1",
    "sqlalchemy>=2.1.4",
]
```

```toml
[tool.deptry.per_rule_ignores]
DEP002 = ["granian"]  # the ASGI server the image runs the app with; no module imports it
```

`.dockerignore` (repository root):

```
.git
.github
.superpowers
.venv
**/.venv
**/__pycache__
**/*.py[oc]
.mypy_cache
.ruff_cache
.pytest_cache
.import_linter_cache
.hypothesis
.coverage
htmlcov
.tilt-dev
docs
deploy
**/tests
```

`services/identity/Dockerfile`:

```dockerfile
# syntax=docker/dockerfile:1
# The identity service. Built from the workspace's lockfile with uv; run by Granian on uvloop, as an unprivileged
# user, from a file system it never writes to. Build from the repository root:
#   docker build -f services/identity/Dockerfile -t ledger-identity:local .

FROM ghcr.io/astral-sh/uv:0.13.0-trixie-slim AS build
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_INSTALL_DIR=/python \
    UV_PYTHON_PREFERENCE=only-managed \
    UV_PROJECT_ENVIRONMENT=/app/.venv
# The interpreter is uv's own build of CPython: self-contained, so it runs on a distroless base.
RUN uv python install 3.15.0
WORKDIR /src
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable --package ledger-identity

# Distroless: glibc, CA certificates and time zones, no shell, no package manager; user nonroot (65532).
FROM gcr.io/distroless/cc-debian13:nonroot@sha256:e792ab3d241a468a4fd7519ddbbebe66b49b5f365771716ea688ad40b6c6f1c2
LABEL org.opencontainers.image.title="ledger-identity" \
      org.opencontainers.image.description="Organizations, companies, branches, emission points and memberships" \
      org.opencontainers.image.source="https://github.com/balegre0/ledger"
COPY --from=build /python /python
COPY --from=build /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
USER nonroot
EXPOSE 8000 50051
HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=3 \
    CMD ["/app/.venv/bin/ledger-probe", "http://127.0.0.1:8000/readyz"]
ENTRYPOINT ["/app/.venv/bin/granian", "--interface", "asgi", "--factory", "--host", "0.0.0.0", "--port", "8000", \
            "--loop", "uvloop", "--workers", "1", "--no-access-log", "--respawn-failed-workers", \
            "ledger_identity.main:create_app"]
```

Run: `docker build -f services/identity/Dockerfile -t ledger-identity:local .`
Expected: the build ends with the image tagged; `docker image inspect ledger-identity:local --format '{{.Config.User}}'` prints `nonroot`.

- [ ] **Step 4: The local stack**

`deploy/local/postgres/init.sql` — append (the comment at its top already explains the role model):

```sql
-- The identity service's own database and role, on the same terms: it owns its database, it is no superuser,
-- and it cannot bypass row-level security (the migrations force it on every table of a tenant's data).
CREATE ROLE identity LOGIN PASSWORD 'identity' NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
CREATE DATABASE identity OWNER identity;
```

`deploy/local/identity.env`:

```
# The identity service in the local stack. A token is checked against the local Zitadel (compose profile
# `zitadel`); deploy/local/zitadel/bootstrap.sh writes zitadel/.local/identity.env with the real audience, which
# compose reads after this file. Without Zitadel the service still starts: keys are fetched on the first token.
LEDGER_ENVIRONMENT=local
LEDGER_LOG_FORMAT=json
LEDGER_DATABASE_URL=postgresql+psycopg://identity:identity@postgres:5432/identity
LEDGER_NATS_URL=nats://nats:4222
LEDGER_TOKEN_ISSUER=http://zitadel.localhost:8080
LEDGER_TOKEN_AUDIENCE=ledger-local
LEDGER_TOKEN_JWKS_URL=http://zitadel.localhost:8080/oauth/v2/keys
```

`deploy/local/compose.yaml` — the header comment gains a paragraph, and two services are added after `nats`:

```yaml
# Services: identity runs from its production image (services/identity/Dockerfile), read-only, as nonroot, with
# its migrations applied first by the one-shot identity-migrate. Its database and role come from
# postgres/init.sql; a data volume made before that file listed them needs `docker compose down -v`.
```

```yaml
  identity-migrate:
    # Brings identity's database to the image's schema, then exits; identity starts only once it has succeeded.
    image: ledger-identity:local
    build:
      context: ../..
      dockerfile: services/identity/Dockerfile
    entrypoint: ["/app/.venv/bin/ledger-identity-migrate"]
    env_file: [identity.env]
    read_only: true
    tmpfs: [/tmp]
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]
    restart: "no"
    depends_on:
      postgres: {condition: service_healthy}

  identity:
    image: ledger-identity:local
    env_file:
      - identity.env
      - path: zitadel/.local/identity.env
        required: false
    # HTTP only. The gRPC port (50051) stays inside this network: IdentityQueryService authenticates no
    # caller until the mesh does, so nothing outside the services may reach it.
    ports: ["127.0.0.1:8000:8000"]
    read_only: true
    tmpfs: [/tmp]
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]
    depends_on:
      identity-migrate: {condition: service_completed_successfully}
      nats: {condition: service_healthy}
```

`Tiltfile`:

```python
# Local development. `tilt up` runs the infrastructure, then builds and runs each service from its production
# image. A change to a service's code, to the libraries or to the lockfile rebuilds the image (uv's cache keeps
# that to seconds) and compose restarts the service, its migrations first.
docker_compose("deploy/local/compose.yaml")

docker_build(
    "ledger-identity:local",
    ".",
    dockerfile="services/identity/Dockerfile",
    only=["pyproject.toml", "uv.lock", ".python-version", "libs", "services"],
    ignore=["**/tests", "**/__pycache__", "**/.venv"],
)

dc_resource("postgres", labels=["infrastructure"])
dc_resource("nats", labels=["infrastructure"])
dc_resource("identity-migrate", labels=["identity"])
dc_resource("identity", labels=["identity"], links=[link("http://localhost:8000/docs", "identity API")])
```

Run:

```bash
docker compose -f deploy/local/compose.yaml down -v
docker compose -f deploy/local/compose.yaml up -d --wait --build
curl --fail --silent --show-error http://127.0.0.1:8000/readyz
curl --silent --output /dev/null --write-out '%{http_code}\n' http://127.0.0.1:8000/v1/companies
docker compose -f deploy/local/compose.yaml run --rm identity-migrate
docker compose -f deploy/local/compose.yaml exec postgres psql -U postgres -d identity -tAc \
  "SELECT relname FROM pg_class WHERE relforcerowsecurity ORDER BY relname"
docker compose -f deploy/local/compose.yaml down -v
```

Expected: `up --wait` exits 0 with `identity` healthy and `identity-migrate` exited 0; `/readyz` prints `"status":"ready"` with every check `true`: `database`, `nats`, `workers`, the chassis's check that its tables exist (under the name Milestone 1's final review gave it) and identity's `migrations`; the companies route answers `401`; the second migration run exits 0 and changes nothing; the last query lists `branches`, `companies`, `emission_points`, `idempotency_keys`, `memberships`, `organizations`.

- [ ] **Step 5: CI**

In `.github/workflows/ci.yml`, the last step becomes:

```yaml
      - name: Local stack starts healthy, identity from its production image
        # Builds the image, runs the migrations (one-shot service), starts identity and waits for its health check
        # (/readyz: PostgreSQL, NATS, workers, the schema); then checks that the API refuses an anonymous caller, that the image
        # runs as nonroot and that migrating again changes nothing. Torn down on any exit.
        run: |
          compose='docker compose -p ledger-ci -f deploy/local/compose.yaml'
          trap '$compose down -v' EXIT
          $compose build identity-migrate
          $compose up -d --wait || { $compose logs; exit 1; }
          curl --fail --silent --show-error http://127.0.0.1:8000/readyz
          test "$(curl --silent --output /dev/null --write-out '%{http_code}' http://127.0.0.1:8000/v1/companies)" = 401
          test "$(docker image inspect ledger-identity:local --format '{{.Config.User}}')" = nonroot
          $compose run --rm identity-migrate
```

and the job's `timeout-minutes` goes from 20 to 30 (the image build adds a few minutes on a cold cache).

- [ ] **Step 6: Gate**

Run: `uv run poe fmt && uv run poe check && docker compose -f deploy/local/compose.yaml config --quiet`
Expected: every step passes, coverage 100 %; the compose file is valid.

- [ ] **Step 7: Commit**

```bash
git add .dockerignore Tiltfile pyproject.toml uv.lock .github deploy libs/platform services/identity
git commit -m "build(identity): distroless image on Granian and uvloop, local stack and CI smoke test

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 18: A real Zitadel locally, the README, and the spec brought in line

**Files:**
- Create: `deploy/local/zitadel/bootstrap.sh`, `deploy/local/zitadel/token.sh`, `deploy/local/zitadel/README.md`
- Modify: `deploy/local/compose.yaml`, `deploy/local/postgres/init.sql`, `.gitignore`, `.github/workflows/ci.yml`, `README.md` (edited section by section, Step 3), `docs/superpowers/specs/2026-10-11-ledger-platform-design.md`

**Interfaces:**
- Consumes: the `identity` compose service and `deploy/local/identity.env` (Task 17).
- Produces: compose profile `zitadel` (service `zitadel`, `ghcr.io/zitadel/zitadel:v4.19.2`, issuer `http://zitadel.localhost:8080`); `deploy/local/zitadel/bootstrap.sh` (project `ledger`, API application `ledger-api`, machine user `ledger-dev` with JWT access tokens and a client secret; writes `deploy/local/zitadel/.local/client.env` and `.local/identity.env` with `LEDGER_TOKEN_AUDIENCE`); `deploy/local/zitadel/token.sh` (prints an access token). No automated test depends on any of it.

Every command in this task was run against Zitadel v4.19.2 and PostgreSQL 18.6 on 2026-10-12, as the plan was written: the bootstrap, the token request and the claims it returns (Decision 2).

- [ ] **Step 1: Zitadel in the local stack, behind a profile**

`deploy/local/postgres/init.sql` — append:

```sql
-- The local Zitadel's database and role (compose profile `zitadel`): Zitadel makes its schema at its first start,
-- as the owner of its database; it needs no superuser either.
CREATE ROLE zitadel LOGIN PASSWORD 'zitadel' NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
CREATE DATABASE zitadel OWNER zitadel;
```

`deploy/local/compose.yaml` — the service after `identity`, and its volume:

```yaml
  zitadel:
    # The identity provider of the local stack, optional: `docker compose --profile zitadel up -d --wait`. It
    # answers as zitadel.localhost:8080 on the host (curl and browsers resolve *.localhost to loopback) and in this
    # network (an alias), so a token's issuer is one string for a developer and for identity: Zitadel tells its
    # instance by the Host header. Machine users only, so no login UI runs. zitadel/README.md has the rest.
    profiles: [zitadel]
    image: ghcr.io/zitadel/zitadel:v4.19.2@sha256:956a298405189a8f6cd4ec92e8828b1bd10caa7da8b139c39d251d7053e33784
    # Root, in this local profile only: a new named volume belongs to root, and Zitadel, which runs as an
    # unprivileged user otherwise, writes the bootstrap token into it. Nothing of the profile ships.
    user: "0"
    command: ["start-from-init", "--masterkey", "MasterkeyNeedsToHave32Characters"]
    environment:
      ZITADEL_PORT: 8080
      ZITADEL_TLS_ENABLED: "false"
      ZITADEL_EXTERNALDOMAIN: zitadel.localhost
      ZITADEL_EXTERNALPORT: 8080
      ZITADEL_EXTERNALSECURE: "false"
      ZITADEL_DATABASE_POSTGRES_DSN: postgresql://zitadel:zitadel@postgres:5432/zitadel?sslmode=disable
      ZITADEL_FIRSTINSTANCE_ORG_NAME: ledger-local
      ZITADEL_FIRSTINSTANCE_ORG_HUMAN_PASSWORDCHANGEREQUIRED: "false"
      ZITADEL_FIRSTINSTANCE_ORG_MACHINE_MACHINE_USERNAME: ledger-bootstrap
      ZITADEL_FIRSTINSTANCE_ORG_MACHINE_MACHINE_NAME: ledger bootstrap
      ZITADEL_FIRSTINSTANCE_ORG_MACHINE_PAT_EXPIRATIONDATE: "2099-01-01T00:00:00Z"
      ZITADEL_FIRSTINSTANCE_PATPATH: /bootstrap/admin.pat
      ZITADEL_DEFAULTINSTANCE_FEATURES_LOGINV2_REQUIRED: "false"
    ports: ["127.0.0.1:8080:8080"]
    volumes: ["zitadel-bootstrap:/bootstrap"]
    networks:
      default:
        aliases: [zitadel.localhost]
    healthcheck:
      # The setting ZITADEL_TLS_ENABLED (not the --tlsMode flag) is what `zitadel ready` reads too.
      test: ["CMD", "/app/zitadel", "ready"]
      interval: 5s
      timeout: 10s
      retries: 30
      start_period: 20s
    depends_on:
      postgres: {condition: service_healthy}
```

```yaml
volumes:
  postgres: {}
  nats: {}
  zitadel-bootstrap: {}
```

`.gitignore` — append:

```
deploy/local/zitadel/.local/
```

`deploy/local/zitadel/bootstrap.sh`:

```sh
#!/bin/sh
# Creates, in the local Zitadel, what a developer needs to call ledger with a real token: a project (its id is the
# audience of the tokens), an API application for the platform, and a machine user with a client secret.
# Run once, after `docker compose -f deploy/local/compose.yaml --profile zitadel up -d --wait`. A second run fails
# (the names are taken); `docker compose ... --profile zitadel down -v` starts over. Needs curl and sed only.
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
ZITADEL=${ZITADEL:-http://zitadel.localhost:8080}
export COMPOSE_FILE="$HERE/../compose.yaml"
OUT="$HERE/.local"
umask 077
mkdir -p "$OUT"

docker compose cp zitadel:/bootstrap/admin.pat "$OUT/admin.pat"
PAT=$(tr -d '\r\n' < "$OUT/admin.pat")

# A JSON request with the bootstrap service account; prints Zitadel's compact JSON answer.
call() {
  curl --fail-with-body --silent --show-error -X "$1" "$ZITADEL$2" \
    -H "Authorization: Bearer $PAT" -H 'Content-Type: application/json' --data "$3"
}

# The value of a string field of a compact JSON answer: Zitadel's ids, client ids and secrets hold no quotes.
field() {
  sed -n "s/.*\"$1\":\"\([^\"]*\)\".*/\1/p"
}

project=$(call POST /management/v1/projects '{"name":"ledger","projectRoleAssertion":true}' | field id)
call POST "/management/v1/projects/$project/apps/api" \
  '{"name":"ledger-api","authMethodType":"API_AUTH_METHOD_TYPE_PRIVATE_KEY_JWT"}' > /dev/null
user=$(call POST /management/v1/users/machine \
  '{"userName":"ledger-dev","name":"ledger developer","accessTokenType":"ACCESS_TOKEN_TYPE_JWT"}' | field userId)
secret=$(call PUT "/management/v1/users/$user/secret" '{}')
organization=$(curl --fail-with-body --silent --show-error "$ZITADEL/management/v1/orgs/me" \
  -H "Authorization: Bearer $PAT" | field id)

cat > "$OUT/client.env" <<ENV
ZITADEL=$ZITADEL
PROJECT_ID=$project
ORGANIZATION_ID=$organization
CLIENT_ID=$(printf '%s' "$secret" | field clientId)
CLIENT_SECRET=$(printf '%s' "$secret" | field clientSecret)
ENV
cat > "$OUT/identity.env" <<ENV
LEDGER_TOKEN_AUDIENCE=$project
ENV

echo "project $project in organization $organization; the developer client is in $OUT/client.env"
echo "now restart identity on the new audience: docker compose -f deploy/local/compose.yaml up -d identity"
```

`deploy/local/zitadel/token.sh`:

```sh
#!/bin/sh
# Prints an access token (a JWT) of the local developer client, for `Authorization: Bearer ...`.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/.local/client.env"
scope="openid urn:zitadel:iam:user:resourceowner urn:zitadel:iam:org:projects:roles urn:zitadel:iam:org:project:id:$PROJECT_ID:aud"
curl --fail-with-body --silent --show-error "$ZITADEL/oauth/v2/token" \
  --user "$CLIENT_ID:$CLIENT_SECRET" \
  --data-urlencode grant_type=client_credentials --data-urlencode "scope=$scope" \
  | sed -n 's/.*"access_token":"\([^"]*\)".*/\1/p'
```

`deploy/local/zitadel/README.md`:

````markdown
# Zitadel, locally

The local stack can run a real Zitadel (v4.19.2) so that identity is called with real tokens. Nothing automated
needs it: the tests use a local OpenID provider of their own (`ledger_testing.issuer`).

## Start, bootstrap, call

```sh
docker compose -f deploy/local/compose.yaml --profile zitadel up -d --wait   # the first start takes a minute
sh deploy/local/zitadel/bootstrap.sh                                          # once per fresh volume
docker compose -f deploy/local/compose.yaml up -d identity                    # identity takes the new audience
TOKEN=$(sh deploy/local/zitadel/token.sh)
curl -X POST http://127.0.0.1:8000/v1/companies \
  -H "Authorization: Bearer $TOKEN" -H "Idempotency-Key: $(date +%s%N)" -H 'Content-Type: application/json' \
  -d '{"tax_id": "101850043", "legal_name": "Mi Empresa, S.R.L."}'
```

`bootstrap.sh` creates the project `ledger` (its id is the tokens' audience), the API application `ledger-api` and
the machine user `ledger-dev`, whose access tokens are JWTs. It writes, under `deploy/local/zitadel/.local/`
(ignored by git): `admin.pat` (Zitadel's bootstrap service account), `client.env` (the developer client's id and
secret) and `identity.env` (`LEDGER_TOKEN_AUDIENCE`, which compose gives identity after `deploy/local/identity.env`).

## What a token holds

`iss` `http://zitadel.localhost:8080`, `aud` the project id, `sub` the machine user's id, `client_id`,
`exp`/`iat`/`nbf`, and `urn:zitadel:iam:user:resourceowner:id` (the organization; identity derives the ledger
organization id from it). Project roles, once granted, come as `urn:zitadel:iam:org:project:<project id>:roles`.

## Names

Zitadel tells its instance by the `Host` header, so it must be reached by the name it was set up with,
`zitadel.localhost:8080`: curl and browsers resolve `*.localhost` to 127.0.0.1, and containers of the stack reach
it through a network alias. A service run on the host (not in compose) resolves names with the system's resolver,
which on Windows and on most Linux distributions does not: add `127.0.0.1 zitadel.localhost` to the hosts file.

## Start over

```sh
docker compose -f deploy/local/compose.yaml --profile zitadel down -v
rm -rf deploy/local/zitadel/.local
```
````

`.github/workflows/ci.yml` — the step "Local infrastructure configuration" validates the profile too:

```yaml
      - name: Local infrastructure configuration
        run: |
          docker compose -f deploy/local/compose.yaml config --quiet
          docker compose -f deploy/local/compose.yaml --profile zitadel config --quiet
```

- [ ] **Step 2: Verify the profile by hand**

Run the commands of the README's "Start, bootstrap, call" section, then `docker compose -f deploy/local/compose.yaml --profile zitadel down -v` and `rm -rf deploy/local/zitadel/.local`.
Expected: `up --wait` ends with `zitadel` healthy; `bootstrap.sh` prints the project and organization ids; the `curl` answers `201` with the company (its `organization_id` is the uuid5 of the Zitadel organization id `bootstrap.sh` printed); a second identical `curl` with the same key answers the same body with `idempotent-replayed: true`. If Docker on the machine cannot spare the memory Zitadel needs, the profile stays off and nothing else changes: this is why it is a profile.

- [ ] **Step 3: README**

`README.md` is edited, not replaced: its local-stack table, its chassis page and its "Operations, before production" list stay, brought up to date. Each edit quotes the text as it stands at `1f876b1`; Milestone 1's final review rewrites some of these sentences too (the readiness check of the chassis's tables, metric names, the RFC 9457 `instance`): where a sentence has been reworded since, make the same change of substance to it, and keep every change the fix wave made.

1. The introduction. Replace

```text
What exists today is milestone 1, the foundations: the shared libraries, the service chassis, the local stack
and the quality gate. No service exists yet; the first comes in milestone 2.
```

with

```text
What exists today is milestones 1 and 2: the shared libraries, the service chassis, the local stack, the quality
gate, and the first service, `identity` (organizations, companies, branches, emission points, memberships).
```

2. "Layout": in the table, the rows of `libs/kernel`, `libs/contracts`, `libs/platform`, `services/` and `deploy/local` become these, and the `libs/testing` row is added after `libs/platform`:

```text
| `libs/kernel` | `ledger_kernel`: UUIDv7 ids and the `Tenant`, the error vocabulary (a kind and a stable code per error), exact `Money`, clock, the Dominican `TaxId` (RNC or cédula), principals and permissions (`access`), cursor pagination. Standard library only |
| `libs/contracts` | `ledger_contracts` and the generated `ledger.*`: protobuf messages, gRPC stubs (`ledger.identity.v1`) and the CloudEvents `Envelope` of every event (`ledger.events.identity.v1`). The `.proto` sources are in `libs/contracts/proto` (a buf module); the generated code is committed |
| `libs/platform` | `ledger_platform`: the service chassis. Settings, logs, health, the HTTP app, PostgreSQL with row-level security, the outbox, consumers, JetStream streams, tracing, the gRPC server, and the `Service` that assembles them; bearer tokens checked against the identity provider's keys, the authorization context, idempotency keys, the dependencies routes declare, gRPC client helpers, and `ledger-probe` (an image's health check) |
| `libs/testing` | `ledger_testing`: what the tests of every package share (PostgreSQL and NATS containers, a local OpenID provider, waiting on what happens elsewhere, calling an API). A dev dependency: production code never imports it |
| `services/identity` | `ledger_identity`: organizations, companies, branches, emission points, memberships; REST under `/v1`, gRPC `IdentityQueryService`, events on the stream `IDENTITY` |
| `deploy/local` | PostgreSQL, NATS and identity for local development, and a Zitadel behind the compose profile `zitadel`; the `Tiltfile` at the root runs them |
```

3. "Develop": after the `uv run poe proto` line of the code block, add

```text
uv run poe openapi    # regenerate services/identity/openapi.json after changing identity's API; commit the result
```

4. "The local stack": the first command becomes `docker compose -f deploy/local/compose.yaml up -d --wait --build   # or: tilt up (Tilt's UI is at http://localhost:10350)`; add this row to the table after NATS's:

```text
| identity | `http://127.0.0.1:8000` | Its production image, migrated first by the one-shot `identity-migrate`; API documentation at `/docs`, `/readyz` checks the schema too. Database `identity`, role `identity` (password `identity`), on the same terms as `ledger_app`. Its gRPC port, 50051, is reachable inside the compose network only: `IdentityQueryService` authenticates no caller until the service mesh does |
```

and after the paragraph that follows the table, add: `To call identity with a real token, run the local Zitadel: [deploy/local/zitadel/README.md](deploy/local/zitadel/README.md).`

5. "The chassis in one page", "A minimal service". Replace

```text
The service's first migration creates the chassis's tables, `outbox` and `inbox` (`CHASSIS_METADATA` in
`ledger_platform.schema`), and puts every tenant table under `tenant_isolation`. The chassis creates no table
itself, and the service must find them there when it starts: without them it reports ready while the relay fails
at every pass and no event leaves. Until Alembic arrives (milestone 2) a migration is a script:
```

with

```text
The service's first migration creates the chassis's tables, `outbox`, `inbox` and `idempotency_keys`
(`CHASSIS_METADATA` in `ledger_platform.schema`), runs their policies (`CHASSIS_POLICIES`), and puts every tenant
table under `tenant_isolation`, or `organization_isolation` for what an organization owns as a whole. The chassis
creates no table itself, and a service that does not find them is not ready. A service's migrations are Alembic
revisions shipped in its package (identity's, in `ledger_identity.adapters.postgres.migrations`, are the ones to
copy, with the test that keeps them equal to the models); their gist, as a script:
```

and the script under it becomes

````markdown
```python
from sqlalchemy import text

from ledger_platform.database import Database, tenant_isolation
from ledger_platform.schema import CHASSIS_METADATA, CHASSIS_POLICIES  # it imports every module with a table

async with Database.open(url) as database, database.engine.begin() as connection:
    await connection.run_sync(CHASSIS_METADATA.create_all)  # outbox, inbox, idempotency_keys
    await connection.run_sync(PAYMENTS.metadata.create_all)
    for statement in (*CHASSIS_POLICIES, *tenant_isolation(PAYMENTS)):  # enable, force, create each policy
        await connection.execute(text(statement))
```
````

6. Same section, after the example service. Replace

```text
The tenant comes from the URL only so that the example runs: where it really comes from, the gateway's verified
claims, is the authorization context of milestone 2.
```

with

```text
The tenant comes from the URL only so that the example stays short. A real route declares
`company_in_path(authenticated(verifier), permissions)` (`ledger_platform.routes`): the caller is the principal its
bearer token proves, the company the path's, and a caller without a role there gets a 404; its `POST`s run through
`Idempotency`, its updates check `If-Match`. identity's routes are the example.
```

and replace `The example runs no gRPC server, because `ledger_contracts` defines no service yet.` with `The example runs no gRPC server; identity runs one (`ledger.identity.v1.IdentityQueryService`).`

7. "The parts": the `database.transaction(tenant)` row becomes

```text
| `database.transaction(scope)` | One PostgreSQL transaction scoped to a `Tenant` (one company), an `OrganizationId` (what the organization owns as a whole: its companies, its memberships, its idempotency keys) or `None` (no protected row); `unit.scope` says which, for repositories that filter by it too. `unit.connection` is for repositories, `outbox.add(unit, envelope, subject=)` stores an event in the same transaction, `unit.after_commit(callback)` runs once it has committed. It commits only if every statement succeeded: a database error caught and ignored inside it raises `AbortedTransactionError` at the exit instead of losing the work silently |
```

the `tenant_isolation(table)` row gains at its end `organization_isolation(table, column=)` does the same for a table an organization owns as a whole (its `organization_id`, or `id` for the organizations themselves).`, the Errors row lists `UnauthenticatedError` after `InvalidInputError`, and these rows are added after the Errors row:

```text
| `ApiSettings`, `TokenVerifier`, `HttpKeys` | A service with a public API takes `LEDGER_TOKEN_ISSUER`, `LEDGER_TOKEN_AUDIENCE` and `LEDGER_TOKEN_JWKS_URL` (https outside `local` and `test`). The verifier accepts asymmetric algorithms only, keeps the provider's keys for 10 minutes, refreshes them at once for a key id it lacks (at most once per 10 s), and answers 401 with the problem in a word, never the token; `HttpKeys` is the service's resource that fetches them |
| `authenticated`, `company_in_path`, `company_in_header`, `CompanyAccess` | What a route declares (`ledger_platform.routes`): the caller, and the company of the request with what the caller may do there (`access.require(Permission.COMPANY_MANAGE)`): 404 without a role there, 403 without the permission. Also `page_request` (`limit`, `cursor`), `if_match` (428 without it), `idempotency_key`, `attempt_of`, `json_response`, `answer` |
| `Idempotency` | `once(unit, attempt, work)` runs a `POST`'s work once per `(organization, subject, key)`, in the work's own transaction, and replays its stored answer byte for byte (`Idempotent-Replayed: true`); another body with the key is a 422, a twin in flight waits, keys live 24 h |
| `call_unary`, `RemotePermissions`, `CachedPermissions`, `MembershipChanges` | A gRPC call with a deadline whose failure comes back as a ledger error of the other side's kind; permissions asked of identity, cached 30 s and dropped by every replica when a membership event says so |
```

8. "What the chassis guarantees", "Fail-closed tenancy". Replace its first sentence, `` `database.transaction(tenant)` sets the transaction-local settings `ledger.organization_id` and `ledger.company_id`. ``, with `` `database.transaction(scope)` sets the transaction-local settings `ledger.organization_id` and `ledger.company_id` (the latter empty for an organization's own scope). ``, after the sentence that ends `refuses every write.` add `A table under `organization_isolation`, such as `idempotency_keys`, shows and accepts the rows of the transaction's organization, whatever company the transaction names, and nothing without a scope.`, and `` `outbox` and `inbox` are outside it on purpose`` becomes `` `outbox` and `inbox` are outside both on purpose``. In the table of kinds under "One error shape", add after the `CONFLICT` row: `| `UNAUTHENTICATED` | 401, with `WWW-Authenticate: Bearer` | `UNAUTHENTICATED` |`.

9. Add this section after "The chassis in one page" (before "Quality gate"):

```text
## The identity service

- Every `/v1` route needs a bearer token of the organization's identity provider; the organization comes from
  the token, the company from the path. A company the caller holds no role in is a 404.
- Roles per company: `owner`, `admin`, `accountant`, `issuer`, `viewer`. The owner role is granted and taken by
  owners only, a company always keeps one, and a change of roles is decided on the role its actor holds once it
  has the company's lock. A revoked membership is kept, so its version never goes back.
- Every `POST` takes an `Idempotency-Key`; a company's updates take `If-Match` with its `ETag`; lists take `limit`
  and `cursor`. Errors are RFC 9457 problem documents with a stable `code`. The OpenAPI document is published in
  `services/identity/openapi.json`, and a test keeps it equal to the code.
- Events: `identity.company.created|updated|deactivated`, `identity.membership.granted|revoked` (stream
  `IDENTITY`, protobuf `ledger.events.identity.v1`), each with its aggregate's version.
- Other services ask `ledger.identity.v1.IdentityQueryService` (`GetCompany`, `GetPermissions`, the latter
  reading-only for a deactivated company); the platform's `RemotePermissions` and `CachedPermissions` wrap it.
- Tenancy is checked twice: the repositories name the organization (and the company) in every statement, and
  PostgreSQL's row-level security enforces the transaction's scope; references between tables carry the tenant
  in their foreign keys.
- `/readyz` checks that the database is at the schema of the running version (`migrations`).
```

10. "Quality gate": in the `types` row, `once over `libs`` becomes `once over `libs` and `services``; in the `arch` row, after `layers inside `platform`` add `; libraries never import services; identity is hexagonal (domain, use cases, adapters, interfaces)`.

11. "Not done yet": replace the list under `Milestone 2 (identity) and later:` (and that line) with

```text
Next:

- Mirroring Zitadel users and machine clients into identity (Zitadel Actions v2 webhooks), organization-level
  roles, and a company's fiscal regime (with `fiscal-do`).
- Tracing of SQL statements and gRPC calls, a call's deadline derived from its caller's remaining deadline, and
  retries of idempotent methods: with the first service-to-service caller (milestone 3).
- The append-only audit log of security-relevant actions, with vault's signing audit (milestone 3); until then a
  membership keeps who changed it last.
- Envoy Gateway (rate limits, request size limits), the service mesh (Linkerd: mTLS, and a policy that admits only
  the callers of `IdentityQueryService`), Helm charts, schemathesis against the OpenAPI documents, image signing,
  SBOM and vulnerability scan: the deployment milestone.
```

keeping, as the fix wave left it, the item about a request correlation id and the RFC 9457 `instance` if it is still there. Under "Operations, before production:" keep the five items and add:

```text
- A sweeper for the expired idempotency keys of an organization that stopped posting: row-level security hides
  them from any job without a scope, so it needs the maintenance role of the first item (each `POST` purges its
  own organization's).
- `IdentityQueryService` (port 50051) authenticates no caller until the mesh does: never publish the port beyond
  the services' network, and admit only its callers by mesh policy.
```

- [ ] **Step 4: Bring the spec in line**

In `docs/superpowers/specs/2026-10-11-ledger-platform-design.md` make these thirteen edits. Each quotes the text as it stands at `1f876b1`; if a sentence has been reworded since (Milestone 1's final review edits the spec too), make the same change of substance to it. Together they leave no sentence of the spec contradicting what this milestone built, and record every deferral of Decision 16 and the accepted risk of Decision 14.

1. §3, the `platform` row of the libraries table. Replace

```text
gRPC client helpers and the authorization context come with the first service that needs them (milestone 2)
```

with

```text
Built with the first service (milestone 2): bearer-token verification against the identity provider's keys, the authorization context (permissions per company, cached 30 s and dropped on membership events), idempotency keys, cursor pagination, conditional updates, gRPC client helpers
```

and add this row after it:

```text
| `testing` | what the tests of every package share: PostgreSQL and NATS containers, a local OpenID provider, the logging baseline (a dev dependency) | anything; production code never imports it |
```

2. §4.2. Replace

```text
- Identity and tenant travel in metadata, signed by the gateway (see §8); services trust only that.
```

with

```text
- Between services the mesh authenticates the caller (Linkerd identity); a request names the organization,
  company and principal it is about, and the callee answers inside that organization only. Accepted risk until
  the mesh is deployed (deployment milestone): a gRPC server authenticates no caller, so its port is never
  published outside the services' own network (locally, the compose network), and a mesh policy that admits
  only the services that call it is required before production.
```

and in the table of kinds add, after the `CONFLICT` row:

```text
| `UNAUTHENTICATED` (no credentials, or ones that do not hold) | 401, with `WWW-Authenticate: Bearer` | `UNAUTHENTICATED` |
```

3. §7, the bullet that begins "Every tenant-owned table". Replace its first two sentences, from `- Every tenant-owned table has` through `which also forces it on the table's owner.`, with

```text
- A company's data has `organization_id` and `company_id`; **row-level security** shows and accepts only the
  rows where **both** equal the transaction-local settings `ledger.organization_id` and `ledger.company_id`
  (binding the organization as well keeps a company id from reaching across organizations). What an
  organization owns as a whole (its companies, the memberships in them, its idempotency keys) has
  `organization_id`, and is shown to a transaction of that organization or of any of its companies. `platform`
  sets both settings at the start of every transaction from its scope: a `Tenant`, an organization alone, or
  nothing. A table's migration enables its policy with the chassis's `tenant_isolation(table)` or
  `organization_isolation(table)`, which also force it on the table's owner. PostgreSQL's referential checks
  bypass row-level security, so a row refers to another through a foreign key that carries the tenant
  (`(organization_id, company_id)` to a company, `(company_id, branch_id)` to a branch): a row of one tenant
  cannot point at another's.
```

4. §8. Replace

```text
- **Envoy Gateway** validates the access token (JWKS from Zitadel), applies rate limits per client,
  and forwards the verified claims. Services do not re-validate tokens; they trust only headers set
  by the gateway, which strips any client-supplied copy.
```

with

```text
- **Envoy Gateway** validates the access token (JWKS from Zitadel) and applies rate limits per client.
  Every service validates the token again (defense in depth): issuer, audience (the Zitadel project),
  signature with a published key (asymmetric algorithms only), times within a small leeway. The principal is
  the token's `sub`; its organization is Zitadel's `urn:zitadel:iam:user:resourceowner:id`, whose ledger id is
  a UUIDv5 of it, the same in every service.
```

and after the bullet that begins "Authorization is decided in the service" add

```text
- A caller without any role in a company gets 404 for it, as if it did not exist; a role without the
  permission an operation needs gets 403. A change of roles is decided on the role its actor holds once it
  has the company's lock. A deactivated company grants other services the reading permissions only
  (`company.read`, `membership.read`, `invoice.read`); identity itself refuses any change of it but a
  revocation.
```

and replace

```text
- Every request carries one `company_id` and, with it, its organization; together they set the RLS
  context. Cross-company queries exist only in admin endpoints with a separate permission.
```

with

```text
- A request about a company carries its `company_id` (in the path, or the `Ledger-Company-Id` header) and,
  with it, its organization; together they set the RLS context. A request about the caller's organization as
  a whole (registering a company, listing the companies it holds a role in, reading the organization) runs in
  the organization's scope alone and sees only what the organization owns (§7). No request reaches across
  organizations.
```

5. §10. Append to the `Idempotency-Key` bullet

```text
  The key belongs to its caller (organization and subject), is claimed in the transaction that does the work
  and keeps the response with it; a request that fails keeps nothing; a concurrent twin waits for the first
  and replays its answer (`Idempotent-Replayed: true`).
```

and replace

```text
- Cursor pagination, `ETag` + `If-Match` on updates, ISO 8601 dates, amounts as strings.
```

with

```text
- Cursor pagination (`limit` 1-100, opaque `cursor`), `ETag` + `If-Match` on updates (428 without it, 412 when
  it names no current version; a `PUT` that sets a value outright, such as a membership's role, is exempt: a
  retry is harmless and racing ones serialize), ISO 8601 dates, amounts as strings.
```

6. §11. In Traces, replace

```text
the instrumentation of SQLAlchemy and gRPC
  arrives with the first service (milestone 2).
```

with

```text
the instrumentation of SQLAlchemy and gRPC
  arrives with the first call between services (milestone 3).
```

In Logs, replace

```text
request-scoped fields (trace id, company id) will merge in from context variables once the request
  middleware binds them (milestone 2); nothing binds them yet.
```

with

```text
a record written inside a span carries its `trace_id` and `span_id`, and a request's
  records its `organization_id`, `subject` and `company_id`, bound by the platform's request dependencies.
```

7. §13. Replace

```text
├── libs/{kernel,contracts,platform}/    (contracts/proto is the buf module: the .proto sources)
```

with

```text
├── libs/{kernel,contracts,platform,testing}/    (contracts/proto is the buf module: the .proto sources)
```

and

```text
│   └── <service>/{pyproject.toml, src/, tests/, migrations/, Dockerfile}
```

with

```text
│   └── <service>/{pyproject.toml, src/, tests/, openapi.json, Dockerfile}   (migrations ship in the package)
```

8. §14, the Contracts row. Replace

```text
buf breaking; schemathesis against the OpenAPI document
```

with

```text
buf breaking; the published OpenAPI document kept equal to the code by a test; schemathesis against it from the deployment milestone
```

9. §15, Local development. Replace

```text
from milestone 2, builds and runs each service
  with live reload; compose runs the infrastructure alone.
```

with

```text
from milestone 2, builds each service's production
  image and runs it, rebuilding on every change; compose alone runs the same stack, and the profile `zitadel`
  adds a local Zitadel.
```

10. §3, the `identity` row of the services table. Replace

```text
| `identity` | organizations, companies (RNC, legal name, fiscal regime), branches, emission points, memberships, roles per company, API clients (mirrors Zitadel machine users) | public REST (`/v1/organizations`, `/v1/companies`...), gRPC `IdentityQuery` (company by id, permissions of a principal) | Zitadel webhooks (Actions v2) for user lifecycle |
```

with

```text
| `identity` | organizations, companies (RNC, legal name; the fiscal regime comes with `fiscal-do`), branches, emission points, memberships, roles per company; API clients mirroring Zitadel machine users come with the Zitadel webhooks | public REST (`/v1/organization`, `/v1/companies`...), gRPC `IdentityQueryService` (company by id, permissions of a principal) | Zitadel webhooks (Actions v2) for user lifecycle, from a later milestone |
```

11. §3, the `vault` row. Replace `` `identity.company.deleted` `` with `` `identity.company.deactivated` `` (identity deactivates companies; it deletes none).

12. §4.2. Replace

```text
- Deadlines always set by the caller and propagated; retries only on idempotent methods
  (declared in the service config), with hedging off.
```

with

```text
- Deadlines always set by the caller: a fixed deadline per call, at most 30 s (milestone 2). Deriving it from
  the caller's own remaining deadline, and retries only on idempotent methods (declared in the service config,
  hedging off), come with the first service-to-service caller (milestone 3).
```

13. §9, the table. In the `Public API` row, `request size limits` becomes `request size limits (at the gateway, from the deployment milestone)`; the `Audit` row's measure becomes `Append-only audit log per service for security-relevant actions, exported to the log store; it comes with vault's signing audit (milestone 3), and until then a membership keeps who changed it last and every change of one is an event`.

Run: `uv run poe spell`
Expected: no typos.

- [ ] **Step 5: Gate**

Run: `uv run poe fmt && uv run poe check && docker compose -f deploy/local/compose.yaml --profile zitadel config --quiet`
Expected: every step passes.

- [ ] **Step 6: Commit**

```bash
git add .gitignore .github deploy README.md docs
git commit -m "docs: local Zitadel with a scripted bootstrap, README and spec for milestone 2

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Self-review (for the plan author; already applied)

**Spec coverage.** §16's milestone 2 ("Zitadel integration, organizations, companies, roles, RLS end to end") and the owner's eight decisions, task by task:

| Requirement | Where |
|---|---|
| Decision 1: `services/identity` hexagonal (§6): Organization, Company (TaxId, legal name, status), Branch, EmissionPoint, Membership, roles mapped to typed permissions; organization- and company-scoped RLS, an organization-only isolation helper | Tasks 1 (permissions), 4 (helper), 9 (domain), 10 (use cases), 11 (schema and policies), 12 (repositories) |
| Decision 2a: JWT validation in every service: issuer, audience, JWKS with caching and rotation, leeway, strict algorithms, Zitadel claims into a typed `Principal`; tests with a local issuer serving JWKS over HTTP | Tasks 1, 3 (and `ledger_testing.issuer`) |
| Decision 2b: request-scoped `Principal` + `Tenant` (company from path or header, checked against memberships), RLS scope, log context | Tasks 4, 6 |
| Decision 2c: idempotency keys on every POST (24 h, 422 on another body, concurrency-safe), chassis table and FastAPI dependency | Tasks 5, 6, 13, 14 |
| Decision 2d: cursor pagination, `ETag`/`If-Match` | Tasks 1, 6, 13 |
| Decision 2e: Alembic per service with the chassis tables and RLS; `upgrade head` from empty, `downgrade base`, models equal to the schema | Task 11 |
| Decision 3: `/v1` REST (organizations read own, companies CRUD with ETag, branches, emission points, memberships), OpenAPI 3.1 published, RFC 9457 errors | Tasks 13, 14 (the error documents are M1's `web.py`) |
| Decision 4: gRPC `IdentityQuery` in `ledger.identity.v1` (company, permissions of a principal); the platform's 30 s cache invalidated by `identity.membership.*` | Tasks 7, 8, 15 |
| Decision 5: events `ledger.events.identity.v1` through the outbox, stream `IDENTITY`, subjects `identity.<aggregate>.<event>` | Tasks 7, 12, 16 |
| Decision 6: production Dockerfile (multi-stage uv, non-root, read-only, distroless, Granian + uvloop, health check), compose with its own database and role, Tilt, CI build and smoke | Task 17 |
| Decision 7: Zitadel v4 pinned (v4.19.2, published 2026-09-28, image digest checked on ghcr.io), documented bootstrap, optional profile | Task 18 |
| Decision 8: out of scope (Actions webhooks, Envoy, Helm, vault, fiscal) | README "Not done yet"; spec §3 records the webhooks, the API clients and the fiscal regime as later (Task 18) |
| §2 rule 8 (tenancy checked twice) | every repository is bound to its unit's organization and names it, and the company, in every statement, on top of RLS (Task 12; a test with the policies switched off sees nothing of another organization); references carry the tenant in composite foreign keys, which RLS does not cover (Tasks 11, 12); tests that skip the filter still see nothing (Tasks 4, 12) |
| §4.1 events carry the aggregate's version | memberships are revoked, not deleted, so a membership's version never goes back (Tasks 9, 10, 12, 14) |
| §4.2 error mapping both ways | M1's server, Task 8's client (no RPC name in a client-visible message) |
| §8 a deactivated company | other services get its reading permissions only (Tasks 9, 10, 12, 15) |
| §9 "what a client sent never leaves in an error" | tokens (Task 3), names and subjects (Task 9), cursors and ids (Task 1), remote codes (Task 8) refuse or cut without echoing; `InvalidTaxIdError` stops echoing the digits in Milestone 1's final review |
| §11 trace ids and request fields in logs, events on the request's trace | Tasks 6, 16 |
| §11 readiness | Milestone 1's built-in check of the chassis's tables; identity's `migrations` check (Tasks 11, 13) |
| §13 quality gate for the new packages (two type checkers, deptry, import-linter, coverage) | Tasks 2, 9 (workspace wiring), every task's gate |

Deferred on purpose, each recorded in Decision 16, in the spec and in the README (Task 18): SQL and gRPC tracing, deadlines derived from the caller's and retries of idempotent methods, schemathesis, the company's fiscal regime, request size limits, the audit log, organization-level roles, Zitadel user mirroring and API clients. Accepted, and recorded the same way: identity's gRPC port authenticates no caller until the mesh (Decision 14).

**Placeholder scan.** No "TBD", "TODO", "similar to Task N", "add error handling" or unnamed types: every code step is complete. The steps that depend on what Milestone 1's final-review fix wave leaves in the chassis say exactly what to re-check: Task 1 Step 1 (the whole surface), Tasks 2, 4, 5, 6, 8, 9, 12 and 17 (the files or names of theirs the fix wave touches), Task 13 (the signatures of `checks=` and the resources hook, which `main.build` is written against), Task 18 Steps 3 and 4 (README and spec sentences quoted as of `1f876b1`).

**Type consistency.** Names used across tasks were checked against their definitions: `Principal(subject, organization, provider_organization, roles)`, `Permission.*`, `PageRequest(size, after)`, `Page(items, next_cursor)`, `page_of(rows, request, key=)`; `TokenPolicy`, `TokenVerifier.verify`, `PublishedKeys(policy, fetch=, clock=)`, `HttpKeys.opened`, `organization_of`; `Scope`, `Database.transaction(scope)`, `UnitOfWork.scope`, `organization_isolation(table, column=)`, `CHASSIS_METADATA`, `CHASSIS_POLICIES`; `Attempt.of(principal, key, method=, path=, body=)`, `Idempotency.once(unit, attempt, work)`, `StoredResponse(status, body, headers)`, `Outcome(response, replayed)`; `authenticated`, `company_in_path`, `company_in_header`, `CompanyAccess.require`, `IfMatch.admits`, `etag`, `precondition_failed`, `attempt_of`, `json_response`, `answer`; `PermissionSource.permissions`, `CachedPermissions.forget`, `RemotePermissions`, `MembershipChanges.follow/handle`, `call_unary(method, request, rpc=, deadline=)`, `RemoteError.remote_code`, `RemoteError.rpc`, `ERROR_CODE_KEY` (from `grpc_client` only); the use cases' keyword arguments (`tax_id`, `legal_name`, `admits`, `code`, `name`, `subject`, `role`, `actor`: a `SubjectId`) and the unit's `now`; `companies_of(unit, subject, request)`, `Companies.of_member(subject, request)`; `Membership(..., status, version, changed_at, changed_by)`, `Roster(company, current, owners)`, `Actor(subject, permissions)`, `grant(roster, *, subject, role, actor, now)`, `revoke(roster, *, subject, actor, now) -> (Membership, MembershipRevoked)`, `permissions_in(company, membership)`, `READ_ONLY`; `bind(unit, outbox, *, clock)`, `Pg*(connection, organization)`, `MembershipPermissions(database)`, `IDENTITY`, `SOURCE`, `outgoing`; `head()`, `require_head(database, expected)`; `ApiContext.build(...)`, `BindUnit`, `build(settings)`, `create_app()`, `services(bind)`; `ledger_testing`'s `Moment`, `eventually`, `waits_for_a_lock`, `libpq_url`, `Forgotten`, `Caller`, `created`, `document`, `page`; the fixtures `postgres`, `database_url`, `fresh_database_url`, `database`, `outbox`, `nats_url`, `issuer`, `settings_for`, `service`, `client`, `owner`, `company`.

**Review focus.** Each of the five lines at the top has its tests in the owning tasks: hostile tokens (Task 3: `alg: none`, HMAC with the public key, invented key ids in a flood, garbage, huge tokens, the leeway's last second on each side, JWKS down, slow, garbage, too large, redirected, proxies in the environment; Task 6: a megabyte `Authorization` header through the app); tenancy (Tasks 4, 6, 12, 13, 14, 15: other organization, no role, branch under another company, gRPC across organizations, RLS hiding rows the filter would let through, the filter holding with RLS off, cross-tenant foreign keys refused); idempotency (Tasks 5 and 13: replay byte for byte, other body, concurrent twins seen waiting on the lock, lock timeout, failure frees the key, expiry); concurrent and stale updates (Tasks 6 and 13: 428, 412, `*`, weak tags, racing PATCHes); memberships (Tasks 9, 10, 12, 14 and 15: admin and the owner role, the last owner, a deactivated company, two owners revoking each other at once, an owner demoted while its grant waits, a deactivated company's permissions in other services).

**What the plan could not verify by running it** (the executor checks these first if a step fails): the type checkers, import-linter, deptry and the tests on the code of this plan (it was written, not run); the signatures of Milestone 1's `checks=`, resources hook and `Events(workers=...)`, not final when the pre-flight amendments were written (Task 13 says how to adapt); Granian's behaviour when the app's lifespan fails at start (the image exits non-zero in practice; Task 17's smoke test would show otherwise); the Compose version on GitHub's runners for `up --wait` with a completed one-shot dependency (verified with Compose v5.5.1 locally). Verified while writing: every complete Python block of the plan, put in place, passes `ruff format --check` and `ruff check` 0.17.0 under the repository's configuration (with the identity entries Tasks 9 and 11 add); the blocks that change an existing file were read against that file; FastAPI 0.143 on CPython 3.15 resolves route parameters annotated with `lazy` imports (bodies, `Request`, dependencies), documents a path parameter declared by a dependency, and names the bearer scheme `HTTPBearer`; pytest-asyncio 1.4.0 with Hypothesis 6.169.0 runs every example of an `async` `@given` test (`asyncio_mode = "auto"`, warnings as errors); joserfc 1.7.5 decoding and its typing in basedpyright `all` and mypy `--strict`; Alembic 1.20's `produce_migrations` typing and its comparison of the outbox's expression index and server defaults on PostgreSQL 18.6 (no difference); the image recipe (uv-managed CPython 3.15.0 on distroless `cc-debian13`, Granian with uvloop, read-only, `nonroot`, healthy through a console-script probe); Zitadel v4.19.2's claims, bootstrap API, `zitadel.localhost` naming and non-superuser database role; `docker compose up --wait` with a completed one-shot dependency.

---

## Changelog (pre-flight amendments)

Amended on 2026-10-12 after the pre-flight conflict scan (`.superpowers/sdd/2026-10-12-milestone-2-identity/preflight.md`), under the controller's rulings and the coordinator's note on Milestone 1's final-review fix wave (`.superpowers/sdd/2026-10-11-milestone-1-foundations/final-review-fixes.md`). Every complete Python block of the plan was put in place again and passes `ruff format --check` and `ruff check` 0.17.0 under the repository's configuration (with the identity entries Tasks 9 and 11 add); the changed fragments were checked inside a probe module; `uv run typos` passes on the plan.

**Blocking**

- **B1** (Task 5): `ledger_platform.schema` imports `IdempotencyRecord`, holds the three chassis tables and exports `CHASSIS_POLICIES` (`organization_isolation` on `idempotency_keys`); its docstring no longer says every chassis table is outside row-level security. `test_schema.py` is rewritten for the three tables, the expiry index, the policy and the subprocess output; both files are in the task's Files and commit; `test_idempotency.py` reads `CHASSIS_METADATA` and applies `CHASSIS_POLICIES`; Step 5's expectation is corrected.
- **B2** (Task 11): `env.py` and the test's `models()` use `CHASSIS_METADATA`; the revision runs `CHASSIS_POLICIES` for `idempotency_keys`.
- **B3** (Task 8): `grpc_server` imports `ERROR_CODE_KEY` from `grpc_client` without re-exporting it; `test_grpc_server.py` imports it from `grpc_client`; `test_the_client_reads_the_code_where_the_server_writes_it` and its aliased import are dropped (the round trip through a real server already proves the two sides agree).
- **B4** (Task 8 Step 1): the platform's deptry map gains `"ledger-contracts" = ["ledger_contracts", "ledger"]`.
- **B5** (Task 13): the conftest passes `nats_url=SecretStr(nats_url)`.
- **B6** (Task 17): the foundations layer is `"settings | options | lifecycle | supervision | probe"`, with a re-check of the landed line; the top layer `"service | schema"` is named in the Global Constraints and Task 1.
- **B7** (Task 1 Step 2): `test_the_code_of_a_common_status_is_pinned`'s row becomes `(401, "unauthenticated")`.

**Should-fix**

- **S1** (Tasks 11, 12; Decision 22; spec §7): `UNIQUE (organization_id, id)` on companies, `UNIQUE (company_id, id)` on branches, composite foreign keys `(organization_id, company_id) → companies` for memberships, branches and emission points and `(company_id, branch_id) → branches` for emission points, in the models and the revision; a repository test proves a membership, a branch and an emission point pointing across tenants are refused with `ForeignKeyViolation` although row-level security lets each through.
- **S2** (Tasks 4, 10, 12; Decision 22): `UnitOfWork.scope` (Task 4, tested); `bind` binds every `Pg*` repository to the scope's organization (`UnscopedUnitError` without one, tested) and every read, update and upsert names it, besides the company; `Companies.of_member` loses its organization parameter (the unit's), and `queries.companies_of` takes a subject; a test switches the policies off inside a rolled-back transaction and shows every repository method still sees nothing of another organization. The ports' and repositories' docstrings and the self-review now state what is true.
- **S3** (Tasks 9, 10, 12, 15; Decision 25; spec §8): `READ_ONLY` and `permissions_in(company, membership)` in the domain; `queries.permissions_of` (what `GetPermissions` answers) gives a deactivated company's reading permissions only; identity's own `MembershipPermissions` keeps the role's permissions, because identity refuses changes of an inactive company with the precise 409 and must still allow revocations there (the existing Task 14 test). Tests in Tasks 9, 10, 12 (both sources side by side) and 15 (over gRPC).
- **S4** (Task 8): `RemoteError`'s message is `a service this one depends on answered <STATUS> (<code>)`, without the RPC path (kept as `rpc` for the log); `remote_code` is cut at `REMOTE_CODE_LIMIT = 64`; tests for both.
- **S5** (Tasks 7, 9-12, 14; Decision 23): memberships are revoked, not deleted: `MembershipStatus`, `Membership.status/active/changed_at/changed_by`, `revoke(..., now)` returns the revoked membership and its event, the port loses `remove`, lists, owner counts and permissions read active memberships only, the table gains `status` (and a version check) and `changed_at`/`changed_by`; a role granted again continues the version (tests in Tasks 9, 10, 12, 14); the proto comment says the version only grows.
- **S6** (Task 18 Step 4): four more spec edits and amended ones: §8's "every request carries one `company_id`" replaced by the organization-scoped requests, and §8 also states the 404/403 rule, the lock-time role and the deactivated company's reading permissions; §3's identity row (`/v1/organization`, `IdentityQueryService`, fiscal regime, API clients and webhooks later) and vault row (`identity.company.deactivated`); §4.2 deadlines (S12) and the gRPC accepted risk (S9); §7 composite keys (S1); §9 request size limits at the gateway and the audit log's deferral; §10 the membership `PUT`'s `If-Match` exemption (M15). The self-review's "recorded in the spec" is now true.
- **S7** (Task 18 Step 3): the README is edited section by section instead of replaced: the local-stack table (credentials, ports, the Windows note) and "Operations, before production" stay; the layout, the chassis page (`transaction(scope)`, `organization_isolation`, `CHASSIS_POLICIES`, Alembic, the request dependencies), the error table and the guarantees are updated; a section on identity is added; "Not done yet" keeps the unfinished Milestone 1 items (tracing, deadline derivation and retries, the correlation id and the RFC 9457 `instance` as the fix wave leaves them) and drops the finished ones; two operations items are added (the idempotency-key sweeper, the gRPC port).
- **S8** (Tasks 11, 13; Decision 26), as the coordinator ruled: the chassis's tables are checked by Milestone 1's built-in readiness check; identity adds its `migrations` check through `checks=`: `migrate.head()` and `migrate.require_head(database, expected)` (`SchemaNotCurrentError`, tested at a migrated and a downgraded database, and through `/readyz` at a migrated and an empty one in `test_readiness.py`). No parallel platform mechanism.
- **S9** (Decision 14; Tasks 15, 17, 18): recorded as an accepted risk in the Decisions, the spec (§4.2), the README and `rpc.py`'s docstring; compose publishes only identity's HTTP port; a mesh policy is required before production.
- **S10** (Tasks 2, 3, 6, 8, 13-16): `Moment` moves to `ledger_testing.clock` (Tasks 3 and 6 import it), `Forgotten` to `ledger_testing.caches` (Tasks 8 and 16), `eventually` to `ledger_testing.waiting` (the platform's fixture delegates to it); the identity API tests share `Caller`, `created`, `document`, `page` and `Page` (`ledger_testing.api`, tested in `libs/testing/tests/test_api.py`) and the conftest fixtures `owner`, `company` and `settings_for`, instead of five copies of `bearer`, `posting`, `body`, `page`, `Page` and `company()`. Doing so also fixed a latent bug: Task 14's branch helper reused one `Idempotency-Key` for branches of two companies (now a key per company and code).
- **S11** (Tasks 2, 5, 12, 16): `ledger_testing.waiting.waits_for_a_lock(url)` reads `pg_stat_activity` for `wait_event_type = 'Lock'` (tested against a real lock); the idempotency twin test and both membership race tests wait for it instead of sleeping; the replay test waits for an empty outbox and compares the stream's last sequence instead of sleeping 1.5 s.
- **S12** (Task 8; Decision 16; Task 18): the deferral of deadline derivation and retries to milestone 3 is recorded in `grpc_client`'s docstring, the spec (§4.2) and the README.
- **S13** (Tasks 9, 10, 12, 14; Decision 24): the use cases take the actor as a `SubjectId` and read its membership after locking the company (`_actor`: 404 without an active one); the domain checks `MEMBERSHIP_MANAGE` itself (`ActorNotAllowedError`, code `permission_denied`); a repository test races an owner's demotion against its grant of the owner role (403 `owners_only`); the two-owners race now ends with the loser's 404 (its own role went first), the company still keeping one owner.

**Coordinator's note (Milestone 1 final-review fix wave)**

- `checks=` carries identity's readiness check; the resources hook carries `HttpKeys` (Task 3: one HTTP client for the service's run, `trust_env=False`, no redirects; `fetch_keys` stays for tools and tests), and `identity_client` documents that a service's channel to identity is opened the same way (no service opens one in this milestone). Tasks 1, 2, 4, 5, 6, 8, 9, 12, 13, 17 and 18 say what to re-check (signatures, layers, the `Clock` alias, `TaxId`'s `str`, the README and spec sentences the fix wave rewrites); Task 13 says how to adapt `main.build` if the hooks landed under other names.
- Not taken: the idempotency purge as a supervised worker (`Events(workers=...)`). A worker has no scope, and `organization_isolation` hides every row from it; a policy exception would expose stored responses to any unscoped transaction. The purge stays in each organization's own `POST`s (Decision 8 gives the reason), and the expired keys of a dormant organization go to the README's operations list, with the maintenance role.
- gRPC RED metrics come with the fix wave (its item 11): see M17.

**Minor**

- **M1**: the Global Constraints and Task 1 Step 1 describe the landed Milestone 1 (`schema.CHASSIS_METADATA`, the `"service | schema"` and `supervision` layers) and the fix wave in progress; Step 1's check prints the schema's tables.
- **M2**: `MembershipNotFoundError` quotes the subject with `excerpt` (the domain test matches the quoted form).
- **M3**: the stale note about ordinary imports under `events.py` is deleted.
- **M4**: Task 16 commits `services/identity libs/platform`, and its Step 2 says a fix comes with a test that pins it.
- **M5**: `ASYMMETRIC_ALGORITHMS = frozenset[str](get_args(TokenAlgorithm))`; `TokenAlgorithm` becomes an ordinary alias (the value of a `type` alias is typed `Any`, which basedpyright's `reportAny` refuses: verified), and the test that kept the two lists equal is dropped.
- **M6**: `problems()` documents every error answer as `application/problem+json` with the `Problem` schema written in; `test_openapi.py` checks every documented 4xx and 5xx.
- **M7**: a 401 whose code is `invalid_token` challenges with `error="invalid_token"` (RFC 6750 §3.1), a missing token with the bare challenge; tests in Tasks 1 and 6.
- **M8**: the `Location` of branches and emission points is built from the ids the service holds; a test posts under an upper-case company id.
- **M9**: the services' test ignores mirror the libraries' (with `S603`), to be copied as the fix wave leaves them.
- **M10**: `test_every_database_gets_a_role_of_its_own` queries `pg_database` and `pg_roles`; the 412 test goes through a route.
- **M11**: `api/rest.py` (with `test_readiness.py`, the new toolkit modules and `schema.py`) is in the file map.
- **M12**: the `zitadel` role is `NOBYPASSRLS`; the compose comment says why the local profile runs Zitadel as root.
- **M13**: every key fetch uses `trust_env=False`; a test sets an unreachable proxy in the environment and still verifies.
- **M14**: a test pins the leeway at its last second on each side for `exp`, `nbf` and `iat` (joserfc 1.7.5 accepts `exp == now - leeway`: verified in its source), and an ASGI test sends a megabyte `Authorization` header (401 `invalid_token`, nothing fetched).
- **M15**: the membership `PUT`'s exemption from `If-Match` is recorded in Decision 17 and the spec (§10).
- **M16**: declined as code: the events do not gain an actor field (a wire change for a log that does not exist yet). Recorded instead in Decision 16, the spec (§9) and the README: the audit log comes with vault's signing audit (milestone 3); meanwhile a membership keeps who changed it last (`changed_by`, from S5) and every change is an event.
- **M17**: no change in this plan: Milestone 1's final review adds gRPC RED metrics to the server's interceptor (its item 11); Decision 16 says so.
