# ledger

A business platform built to be sold, starting where the law forces demand: Dominican Republic electronic
invoicing (e-CF), sold as an API. Software vendors, ERPs, POS systems and online stores send a commercial
invoice as JSON; the platform turns it into a valid, signed e-CF, sends it to DGII, follows it to a final state,
receives other emitters' e-CF on the company's behalf and notifies the client. It is a set of small services
that share one chassis. The design is in
[docs/superpowers/specs/2026-10-11-ledger-platform-design.md](docs/superpowers/specs/2026-10-11-ledger-platform-design.md).

What exists today is milestones 1 and 2: the shared libraries, the service chassis, the local stack, the quality
gate, and the first service, `identity` (organizations, companies, branches, emission points, memberships).

Python 3.15 · uv workspace · strictly typed in two checkers · zero suppressions · 100 % branch coverage.

## Layout

| Path | What |
|---|---|
| `libs/kernel` | `ledger_kernel`: UUIDv7 ids and the `Tenant`, the error vocabulary (a kind and a stable code per error), exact `Money`, clock, the Dominican `TaxId` (RNC or cédula), principals and permissions (`access`), cursor pagination. Standard library only |
| `libs/contracts` | `ledger_contracts` and the generated `ledger.*`: protobuf messages, gRPC stubs (`ledger.identity.v1`) and the CloudEvents `Envelope` of every event (`ledger.events.identity.v1`). The `.proto` sources are in `libs/contracts/proto` (a buf module); the generated code is committed |
| `libs/platform` | `ledger_platform`: the service chassis. Settings, logs, health, the HTTP app, PostgreSQL with row-level security and its migrations (Alembic, run the same way for every service), the outbox, consumers, JetStream streams, tracing, the gRPC server, and the `Service` that assembles them; bearer tokens checked against the identity provider's keys, the authorization context, idempotency keys, the dependencies routes declare, gRPC client helpers, and `ledger-probe` (an image's health check) |
| `libs/testing` | `ledger_testing`: what the tests of every package share (PostgreSQL and NATS containers, a local OpenID provider, waiting on what happens elsewhere, calling an API, reading back the schema a migration built). A dev dependency: production code never imports it |
| `services/identity` | `ledger_identity`: organizations, companies, branches, emission points, memberships; REST under `/v1`, gRPC `IdentityQueryService`, events on the stream `IDENTITY` |
| `deploy/local` | PostgreSQL, NATS and identity for local development, and a Zitadel behind the compose profile `zitadel`; the `Tiltfile` at the root runs them |
| `docs/superpowers` | Specs and plans |

Dependencies point one way, `platform` to `contracts` to `kernel`, and import-linter enforces it. A service may
depend on `libs/*`, never on another service.

## Develop

You need [uv](https://docs.astral.sh/uv/) 0.13 or later (it installs CPython 3.15) and Docker: the tests start a
real PostgreSQL and a real NATS, and the protobuf lint runs in a container.

```bash
uv sync               # the workspace into .venv (CI runs uv sync --locked)
uv run poe check      # the whole gate, as CI runs it; Docker must be running
uv run poe fmt        # format and autofix
uv run poe test       # only the tests, with coverage
uv run poe proto      # regenerate libs/contracts/src/ledger after editing a .proto; commit the result
uv run poe openapi    # regenerate services/identity/openapi.json after changing identity's API; commit the result
uv run poe            # every task
```

On Windows psycopg's asynchronous driver needs a selector event loop; the root `conftest.py` gives the tests
one. Services run in Linux containers, and CI runs on Linux.

### The local stack

```bash
docker compose -f deploy/local/compose.yaml up -d --wait --build   # or: tilt up (Tilt's UI is at http://localhost:10350)
docker compose -f deploy/local/compose.yaml down           # stop it; add -v to delete the data as well
```

`down -v` deletes every volume of the stack: the data of PostgreSQL (every service's database, and Zitadel's),
NATS's streams and Zitadel's bootstrap token. To start Zitadel alone over, `deploy/local/zitadel/reset.sh`.

| | Address | Notes |
|---|---|---|
| PostgreSQL 18.6 | `127.0.0.1:5432` | Database `ledger`, role `ledger_app` with password `ledger_app`. The role owns the database and is **not** a superuser and has no `BYPASSRLS`, so row-level security applies as it does in production |
| NATS 2.12.15 | `nats://127.0.0.1:4222` | JetStream on. Monitoring at `http://127.0.0.1:8222` (`/healthz?js-enabled-only=true`, `/jsz`) |
| identity | `http://127.0.0.1:8100` | `LEDGER_IDENTITY_PORT` moves it (8100 by default, clear of the many tools that take 8000). Its production image, migrated first by the one-shot `identity-migrate`; API documentation at `/docs`, `/readyz` checks the schema too. Database `identity`, role `identity` (password `identity`), on the same terms as `ledger_app`. Its gRPC port, 50051, is reachable inside the compose network only: `IdentityQueryService` authenticates no caller until the service mesh does |

The infrastructure's containers have health checks that prove they are usable (a login as `ledger_app`; JetStream
enabled), and identity's is its `/readyz`, so `--wait` returns once a service could connect and identity is ready.
To call identity with a real token, run the local Zitadel, which neither `up` without `--profile zitadel` nor
`tilt up` starts: [deploy/local/zitadel/README.md](deploy/local/zitadel/README.md).
A service running on the host is configured with:

```bash
LEDGER_SERVICE_NAME=payments
LEDGER_DATABASE_URL=postgresql+psycopg://ledger_app:ledger_app@127.0.0.1:5432/ledger
LEDGER_NATS_URL=nats://127.0.0.1:4222
```

The administrator `postgres` (password `postgres`) is there to run `docker exec` and nothing else: a service never
connects as a superuser, which ignores row-level security and would hide exactly the bugs the policies exist to
catch. `deploy/local/postgres/init.sql` creates the role and the database; it runs only when the data volume is
first created, so after editing it run `down -v`.

## The chassis in one page

A service is its settings, its routes, its consumers and, if it has any, its gRPC services, background workers,
resources (the clients it reaches others with) and readiness checks, handed to a `Service`. The chassis does the
rest: the PostgreSQL pool, the NATS connection, the streams, the resources, the outbox relay, the consumer and
service workers, the gRPC server, health, metrics, traces and logs, started and stopped in a fixed order.

### A minimal service

The service's first migration creates the chassis's tables, `outbox`, `inbox` and `idempotency_keys`
(`CHASSIS_METADATA` in `ledger_platform.schema`), runs their policies (`CHASSIS_POLICIES`), and puts every tenant
table under `tenant_isolation`, or `organization_isolation` for what an organization owns as a whole. The chassis
creates no table itself: without them the service starts but is not ready (its `schema` check fails), since the
relay would fail at every pass and no event would leave. A service's migrations are Alembic revisions shipped in
its package, run by the platform: `Migrations("<package>.adapters.postgres:migrations", owner="payments")` runs them
(`upgrade(url)`, `downgrade(url)`, `head()`; a console script calls `upgrade` before the service starts), and the
package's `env.py` is one line, `run_migrations(PAYMENTS.metadata)` (`ledger_platform.migrations`). identity's
revisions (`ledger_identity.adapters.postgres.migrations`) are the ones to copy, with the test that keeps them equal
to the models (`ledger_testing.migrations` reads the schema back: Alembic's comparison, check constraints, primary
keys, policies). The first revision's gist, as a script:

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

Migrations run one at a time per database (a transaction-level advisory lock, waited for 10 minutes at most; every
other lock 10 s at most), in one transaction each, never as SQL text. The service's readiness check of its schema
is `Interfaces(checks={"migrations": ...})` around `MIGRATIONS.readiness()` (`SchemaReadiness`): ready when the
database is at this version's head, or at a revision this version does not know (a newer version migrated it during
a rollout, and a schema only grows), not ready when it is behind or was never migrated; it reads on a connection of
its own, never the pool's, and stops asking once it has passed.

A data migration runs as the tables' owner, which `FORCE ROW LEVEL SECURITY` subjects to the policies: a statement
run with no scope changes nothing and says so only by its row count. `rows_by_scope(connection, table, where)`
counts the rows to change by tenant (or by organization, for a table without `company_id`), reading past the policy
for that one statement; `run_per_scope(connection, statement, expected)` runs the `UPDATE` or `DELETE` in each scope
and raises `UnexpectedRowCountError` (rolling the migration back) unless each run changed exactly the rows counted:

```python
def upgrade() -> None:
    op.add_column("payments", sa.Column("currency", sa.String(3), nullable=True))
    connection = op.get_bind()
    missing = PAYMENTS.c.currency.is_(None)
    expected = rows_by_scope(connection, PAYMENTS, missing)  # {Tenant(...): 12, ...}
    run_per_scope(connection, sa.update(PAYMENTS).where(missing).values(currency="DOP"), expected)
```

The service itself (this code was run against the local stack, and passes this repository's lint and both type
checkers once it sits in a package):

```python
"""payments: records payments and announces each one as an event."""

from typing import Annotated
lazy from collections.abc import Sequence

from fastapi import APIRouter, Depends
from sqlalchemy import Boolean, Column, MetaData, Table, Text, Uuid, false, insert, update

from ledger.common.v1.money_pb2 import Money
from ledger_contracts.envelope import Envelope
from ledger_kernel.ids import CompanyId, OrganizationId, Tenant, parse_id
from ledger_platform.consumer import ConsumerSpec
from ledger_platform.service import Events, Interfaces, Service, ServiceLimits
from ledger_platform.settings import ServiceSettings
from ledger_platform.streams import StreamSpec
lazy from ledger_platform.database import UnitOfWork
lazy from ledger_platform.service import Runtime, RuntimeDependency


class PaymentsSettings(ServiceSettings):
    """The chassis's settings and one of the service's own: ``LEDGER_CURRENCY``."""

    currency: str = "DOP"


PAYMENTS = Table(
    "payments",
    MetaData(),
    Column("id", Uuid(), primary_key=True),
    Column("organization_id", Uuid(), nullable=False),  # the two tenant columns that
    Column("company_id", Uuid(), nullable=False),  # tenant_isolation(PAYMENTS) requires
    Column("amount", Text, nullable=False),
    Column("settled", Boolean, nullable=False, server_default=false()),
)
STREAM = StreamSpec(name="PAYMENTS", subjects=("payments.>",))
RECORDED = "payments.payment.recorded"


def routes(runtime: RuntimeDependency[PaymentsSettings]) -> Sequence[APIRouter]:
    """The HTTP routes, made once when the service is built."""
    router = APIRouter()

    @router.post("/organizations/{organization}/companies/{company}/payments", status_code=202)
    async def record(
        organization: str,
        company: str,
        amount: str,
        current: Annotated[Runtime[PaymentsSettings], Depends(runtime)],
    ) -> dict[str, str]:
        tenant = Tenant(OrganizationId(parse_id(organization)), CompanyId(parse_id(company)))  # else a 422
        payment = Money(amount=amount, currency=current.settings.currency)
        event = Envelope.wrap(payment, source="payments", tenant=tenant)
        async with current.database.transaction(tenant) as unit:
            await unit.connection.execute(
                insert(PAYMENTS).values(
                    id=event.id,
                    organization_id=tenant.organization,
                    company_id=tenant.company,
                    amount=amount,
                )
            )
            await current.outbox.add(unit, event, subject=RECORDED)  # commits with the row, or not at all
        return {"id": str(event.id)}

    return (router,)


async def settle(event: Envelope, unit: UnitOfWork) -> None:
    """Runs once per event, in the event's tenant, in the transaction that claims the event."""
    event.unwrap(Money)  # an event of another type is refused for good (kind INVALID): it is dead-lettered
    await unit.connection.execute(update(PAYMENTS).where(PAYMENTS.c.id == event.id).values(settled=True))


def consumers(runtime: Runtime[PaymentsSettings]) -> Sequence[ConsumerSpec]:
    """The consumers, made once the service runs; a handler that needs more than its unit gets it here."""
    del runtime
    return (ConsumerSpec(name="payments-settle", stream=STREAM.name, subject=RECORDED, handler=settle),)


service = Service(
    settings=PaymentsSettings(),
    version="0.1.0",
    interfaces=Interfaces(http=routes),
    events=Events(streams=(STREAM,), consumers=consumers),
    limits=ServiceLimits(nats_connect_timeout=30.0),  # every wait has a limit; this one is not the default
)
app = service.app  # any ASGI server runs it; its lifespan starts and stops the whole service
```

The tenant comes from the URL only so that the example stays short. A real route declares
`company_in_path(authenticated(verifier), permissions)` (`ledger_platform.routes`): the caller is the principal its
bearer token proves, the company the path's, and a caller without a role there gets a 404; its `POST`s run through
`Idempotency`, its updates check `If-Match`. identity's routes are the example. Beside `Money`, `ledger_contracts`
holds identity's messages; a service declares its own event messages (`ledger.events.<service>.v1.PaymentRecorded`)
in `libs/contracts/proto`. `Runtime` and `RuntimeDependency` are `lazy` imports although a route signature uses
them: a lazy import resolves on first use, which is when FastAPI reads the signature (the types of a pydantic
model's fields stay ordinary imports).

The example runs no gRPC server; identity runs one (`ledger.identity.v1.IdentityQueryService`). With a `.proto`
service and the code `poe proto` generates from it, the registration is one function, passed as
`Interfaces(http=routes, grpc=register)`:

```python
def register(runtime: Runtime[PaymentsSettings], server: grpc.aio.Server) -> None:
    """Adds the service's gRPC services once it runs; ``runtime`` is what its methods work with."""
    add_PaymentsQueryServicer_to_server(PaymentsQuery(runtime), server)  # a generated function, and a servicer
```

The server listens on `LEDGER_GRPC_PORT`; an `async def` method that raises a `LedgerError` ends its call with the
status of the error's kind (see below).

### The parts

| Part | What it is |
|---|---|
| `ServiceSettings` | Subclass it for a service's own settings. Every field is a `LEDGER_*` environment variable (table below). A missing or invalid value, and any `LEDGER_*` variable that is not a setting (a misspelled name), stop the start with every problem listed; secrets are `SecretStr` and never in a `repr`, a log or an error |
| `Service(settings=, version=, interfaces=, events=, limits=)` | Assembles everything. `service.app` is the FastAPI application to serve; its lifespan runs the service. `service.runtime()`, `service.workers` and `service.grpc_port` are for code outside the routes. A service starts once (`ServiceAlreadyStartedError`) |
| `Interfaces(http=, grpc=, resources=, checks=, registry=)` | `http` is a factory `(RuntimeDependency[S]) -> Sequence[APIRouter]`, called once when the service is built; the routes must be complete then. `grpc` is `(Runtime[S], grpc.aio.Server) -> None`, which adds the generated servicers, such as `add_<Service>Servicer_to_server(servicer, server)`; `None` runs no gRPC server. Methods must be `async def` (coroutines or async generators) to have their errors mapped and their calls timed. `resources` is `(S) -> AbstractAsyncContextManager[object]`, typically an `@asynccontextmanager` that opens the service's clients (an HTTP client, gRPC channels) and yields one dataclass of its own holding them: entered at start after NATS and the streams, exited at stop after the gRPC server and the workers and before NATS drains; code reads it typed with `runtime.resources(Clients)` (`UnexpectedResourcesError` for another type). One typed object composed by the service keeps the surface to one hook and one accessor, and its own `async with` orders the closing. The exit runs in another task than the entry (the stop bounds it with `resources_close_timeout`), so the resource must not hold a task-bound scope (`asyncio.timeout`, `TaskGroup`, an anyio cancel scope) across its `yield`; clients do not. `checks` maps names to `async (Runtime[S]) -> object`, readiness checks beside the chassis's (whose names they cannot take); a check never uses the pool (it reads on `runtime.database.probing()`, a connection of its own) and, for what does not change back once it holds, remembers that it passed. `registry` is the `CollectorRegistry` the service keeps its metrics in (a new one by default): given one, metrics made before the service is built, such as `PublishedKeys(registry=)`'s key age, are exported on `/metrics` beside the chassis's; it must be that service's alone |
| `Events(streams=, consumers=)` | `streams` are the `StreamSpec`s the service publishes to; every start creates a missing one (file storage) or widens an existing one to hold the spec (its subjects added, the longer of each limit kept: a stream only grows), and always `DEAD_LETTERS`; one deleted while the service runs is declared again by the relay when an event finds no stream (at most every 30 s). `consumers` is `(Runtime[S]) -> Sequence[ConsumerSpec]`, called once the service runs. `workers` is `(Runtime[S]) -> Mapping[str, Work]`: background coroutines of the service's own by name, supervised like the relay and the consumers and stopped with them (a name the chassis uses is refused, `DuplicateWorkerError`) |
| `ServiceLimits(database=, pool_size=, publish_timeout=, nats_connect_timeout=, grpc_stop_grace=, worker_stop_grace=, resources_close_timeout=, nats_drain_timeout=, span_flush_timeout=)` | Every wait has a limit. `DatabaseLimits`: connect 5 s, statement 30 s, lock 5 s, idle in transaction 60 s, wait for a pooled connection 10 s, connection recycled after 30 min. `pool_size` 10 (as many again for bursts). `publish_timeout` 5 s, at most half the idle-in-transaction timeout (checked). `nats_connect_timeout` 60 s: the first connection is retried that long, then the start fails; once connected NATS is reconnected for as long as it takes, and its errors meanwhile are logged once a minute (the first at once). The stop: `grpc_stop_grace` 8 s, `worker_stop_grace` 8 s, `resources_close_timeout` 1 s (a hung close of the service's own resources is cancelled, logged and abandoned), `nats_drain_timeout` 4 s, `span_flush_timeout` 4 s; `stop_budget`, their sum (25 s), bounds a stop whatever hangs |
| `Runtime[S]` | What a running service works with: `settings`, `database`, `nats`, `jetstream`, `outbox`, `tracing`, `registry` (the metrics registry `/metrics` exports) and `resources(Kind)`. A route asks for it with `Annotated[Runtime[S], Depends(runtime)]`; the consumer and worker factories, the checks and the gRPC registration are handed it. While the service is not running, routes answer 503 `service_not_running` |
| `database.transaction(scope)` | One PostgreSQL transaction scoped to a `Tenant` (one company), an `OrganizationId` (what the organization owns as a whole: its companies, its memberships, its idempotency keys) or `None` (no protected row); `unit.scope` says which, for repositories that filter by it too. `unit.connection` is for repositories, `outbox.add(unit, envelope, subject=)` stores an event in the same transaction, `unit.after_commit(callback)` runs once it has committed. It commits only if every statement succeeded: a database error caught and ignored inside it raises `AbortedTransactionError` at the exit instead of losing the work silently |
| `Envelope`, `Outbox` | An event is a protobuf message in a CloudEvents 1.0 envelope carried as NATS headers (`ce-id`, `ce-type` = the message's full name, `ce-source`, `ce-time`, `ce-subject` = company id, `ce-organizationid`, `traceparent`). `Envelope.wrap(message, source=, tenant=)` makes one, `envelope.unwrap(Message)` reads the payload back. `Outbox.add` refuses an event no relay could ever publish (a subject NATS cannot route, a message over its size limit) inside the caller's transaction |
| `ConsumerSpec(name=, stream=, subject=, handler=, max_deliver=, backoff=, ack_wait=)` | A durable JetStream pull consumer. `handler` is `async (Envelope, UnitOfWork) -> None`; the unit is the event's own transaction, scoped to its tenant, so the handler's writes commit together with the event's claim in `inbox`. The spec is the configuration: starting the consumer creates it or brings it to the spec. Its subject and its ack policy never change in place; a service that needs another subject gives the consumer a new name (`payments-settle-v2`). A stream that does not exist yet is no mistake: the consumer logs `consumer.stream_missing` and binds once its stream is there |
| `Migrations`, `run_migrations`, `rows_by_scope`, `run_per_scope` | `ledger_platform.migrations`: a service's Alembic revisions run one at a time per database, its schema's readiness check (`readiness()`), and data migrations that change each scope's rows under row-level security, checked against a count (see above) |
| `tenant_isolation(table)` | The DDL (a tuple of statements) for a migration that enables and forces row-level security on a table with `organization_id` and `company_id` columns, and creates the policy. `tenant_isolation_removal(table)` undoes it for a downgrade. `organization_isolation(table, column=)` does the same for a table an organization owns as a whole (its `organization_id`, or `id` for the organizations themselves) |
| Errors | Raise a `LedgerError` subclass: `InvalidInputError`, `UnauthenticatedError`, `NotFoundError`, `ConflictError`, `ForbiddenError`, `UnavailableError`, or your own with a stable `code`. Its message is built inside the class (`raise PaymentTooLargeError(limit)`), and `excerpt()` quotes at most 40 characters of an input |
| `ApiSettings`, `TokenVerifier`, `HttpKeys` | A service with a public API takes `LEDGER_TOKEN_ISSUER`, `LEDGER_TOKEN_AUDIENCE` and `LEDGER_TOKEN_JWKS_URL` (https outside `local` and `test`). The verifier accepts access tokens only, never an ID token of the same project (one that lacks `jti` or `client_id`, or carries `azp`, `nonce`, `at_hash` or `c_hash`, is refused), and asymmetric algorithms only; it keeps the provider's keys for 10 minutes, refreshes them at once for a key id it lacks (at most once per 10 s), keeps serving the keys it holds while the provider is down, and answers 401 with the problem in a word, never the token; `HttpKeys` is the service's resource that fetches them |
| `authenticated`, `company_in_path`, `company_in_header`, `CompanyAccess` | What a route declares (`ledger_platform.routes`): the caller, and the company of the request with what the caller may do there (`access.require(Permission.COMPANY_MANAGE)`): 404 without a role there, 403 without the permission. Also `page_request` (`limit`, `cursor`), `if_match` (428 without it), `idempotency_key`, `attempt_of`, `json_response`, `answer` |
| `Idempotency` | `once(unit, attempt, work)` runs a `POST`'s work once per `(organization, subject, key)`, in the work's own transaction, and replays its stored answer byte for byte (`Idempotent-Replayed: true`); another body with the key is a 422, a twin in flight waits, keys live 24 h |
| `call_unary`, `RemotePermissions`, `CachedPermissions`, `follow_permission_changes` | A gRPC call with a deadline whose failure comes back as a ledger error of the other side's kind; permissions asked of identity, cached 30 s and dropped by every replica when identity announces a change: a membership granted or revoked drops the member's answer, a company deactivated every answer about the company (`forget_company`). Every identity event that changes what other services may do must be followed there |

| Variable | Default | |
|---|---|---|
| `LEDGER_SERVICE_NAME` | required | `^[a-z][a-z0-9-]{1,30}$`; names the service in logs, spans, metrics, and its PostgreSQL and NATS connections |
| `LEDGER_DATABASE_URL` | required | `postgresql+psycopg://user:password@host:5432/db`; no other driver is accepted. A secret |
| `LEDGER_NATS_URL` | `nats://localhost:4222` | `nats://` or `tls://`, in lower case (nats-py reads `NATS://` as a host), with a host. A secret |
| `LEDGER_ENVIRONMENT` | `local` | `local`, `test`, `staging` or `production`; stamped on every log record |
| `LEDGER_LOG_LEVEL`, `LEDGER_LOG_FORMAT` | `INFO`, `console` | `DEBUG`, `INFO`, `WARNING` or `ERROR`; `console` or `json` |
| `LEDGER_GRPC_PORT` | `50051` | The gRPC server binds it on every interface, without TLS (the mesh does mTLS); `0` is any free port, and `service.grpc_port` says which |
| `LEDGER_HTTP_PORT` | `8000` | For the ASGI server that runs `service.app`; the chassis itself binds only the gRPC port |
| `LEDGER_TOKEN_ISSUER`, `LEDGER_TOKEN_AUDIENCE`, `LEDGER_TOKEN_JWKS_URL` | required by `ApiSettings` | The identity provider's issuer, the audience every token must hold (Zitadel: the project's id), and where its keys are published |
| `LEDGER_TOKEN_ALGORITHMS`, `LEDGER_TOKEN_LEEWAY_SECONDS` | `["RS256"]`, `60` | A JSON list of the asymmetric algorithms a token may be signed with; the clock skew allowed, at most 300 seconds |
| `LEDGER_OTLP_ENDPOINT` | none | An OTLP/gRPC collector, such as `http://otel-collector:4317`; without it spans are created but not exported |

### What the chassis guarantees

**Exactly-once effects.** Delivery is at least once; the effects in PostgreSQL happen once. An event commits in
the outbox with the change it announces, so it exists only if the change committed. The relay locks the due rows
(`FOR UPDATE SKIP LOCKED`), publishes the batch, waits for JetStream's acknowledgements inside that transaction
and deletes the acknowledged rows; one that was not acknowledged stays and is retried after 1 s, then 2 s, up to
30 s, fresh events before retried ones. JetStream drops a republished event by its `Nats-Msg-Id` (the event id)
within the stream's window (2 minutes). A consumer then claims `(consumer, event id)` in `inbox` in the same
transaction as the handler's writes, so a second delivery finds the claim and is acknowledged without calling the
handler, and the message is acknowledged only after the commit. A failed handler is negatively acknowledged with
the backoff (1 s, 10 s, 1 min, 5 min, then 10 min) until `max_deliver` (10) deliveries. An event that can never
succeed, an unreadable envelope or a handler error of kind `INVALID`, is terminated at once. JetStream's
`MAX_DELIVERIES` and `MSG_TERMINATED` advisories are kept in the `DEAD_LETTERS` stream for 30 days, and name the
stream and the sequence of the message to inspect. This covers the database: an effect outside PostgreSQL (a call
to another system) is not undone by a rollback and must be idempotent itself.

**Not promised: order.** The chassis keeps no order between unrelated events (a retried event follows the ones
published after it, and replicas take batches in turn). A service that needs the order within an aggregate should
carry the aggregate's version in its event payloads, and a handler that sees a gap should fail the delivery with
an error that is not of kind `INVALID` (that kind terminates the event), so that it is retried after its backoff,
by when the missing event should have arrived. That is a convention of the service's events, not something the
chassis provides.

**Fail-closed tenancy.** `database.transaction(scope)` sets the transaction-local settings `ledger.organization_id`
and `ledger.company_id` (the latter empty for an organization's own scope). A table under `tenant_isolation` shows
and accepts only the rows where **both** columns equal them, and the policy is forced on the table's owner. With no
tenant (`None`, as for an event of platform-wide scope) such a table shows nothing and refuses every write. A table
under `organization_isolation`, such as `idempotency_keys`, shows and accepts the rows of the transaction's
organization, whatever company the transaction names, and nothing without a scope. A superuser or a `BYPASSRLS`
role would ignore the policies, so a service never connects as one. `outbox` and `inbox` are outside both on
purpose: the relay reads across tenants and the inbox is claimed before the handler runs.

**One error shape.** Every error has a kind, and each kind one status per protocol:

| Kind | HTTP | gRPC |
|---|---|---|
| `INVALID` | 422 | `INVALID_ARGUMENT` |
| `NOT_FOUND` | 404 | `NOT_FOUND` |
| `CONFLICT` | 409 | `FAILED_PRECONDITION` |
| `UNAUTHENTICATED` | 401, with `WWW-Authenticate: Bearer` | `UNAUTHENTICATED` |
| `FORBIDDEN` | 403 | `PERMISSION_DENIED` |
| `UNAVAILABLE` | 503 | `UNAVAILABLE` |
| `INTERNAL`, and any bug | 500, code `internal` | `INTERNAL`, code `internal` |

HTTP failures are RFC 9457 `application/problem+json`, the framework's own 404 and 405 included:
`{"type": "urn:ledger:error:<code>", "title", "status", "code", "detail"?}`. Clients match on `code`, which is
stable; the message may change. A request that breaks the schema is a 422 `invalid_request` whose body also has
`"errors"`, at most 20 entries of `{"location", "type", "message"}` (where it went wrong and of which type, never
the value), and `"omitted_errors"`, how many were left out. A bug answers a bare `internal` and the log gets the
details, once: the exception never reaches the ASGI server, which would log it a second time with its message (a
response that had begun is left unfinished, and the server closes the connection). gRPC carries the code in the
trailing metadata `ledger-error-code`, with generic details for a defect.

**Probes.** `/livez`, `/readyz` and `/metrics` are outside the OpenAPI document.

- `/readyz` runs the checks `database` (a query, on a connection of its own: a pool that requests keep busy never
  takes a replica out of rotation), `nats` (the connection is up), `workers` (none is dead), `schema` (the chassis's
  tables exist, `to_regclass`, on the same kind of connection) and the service's own `checks`
  concurrently, each held to 2 seconds, and answers 200 `ready` or 503 `not_ready` with the result of each.
  Overlapping probes share one run of each check. A check that starts failing is logged once with its traceback,
  then at debug level while it stays failing, and `health.check_recovered` when it passes again.
- `/livez` does not consult the dependencies, so a PostgreSQL outage does not restart every pod. It answers 503
  `unrecoverable` only when the process cannot recover by itself: a check has outlived ten time limits and will
  not stop, or a background worker is dead for good.
- Workers (the outbox relay, one per consumer, and the service's own) are supervised. One that fails is logged
  with its traceback and started again after 1 s, then 2 s, up to a minute, each delay shortened at random by up to a fifth so that
  replicas do not come back together; a run of a minute starts the delays over; `ledger_worker_restarts_total`
  counts the restarts. A worker waiting to start again is not dead: JetStream unable to serve a consumer for a
  moment fails neither probe, and NATS being unreachable fails the `nats` check alone; neither does a consumer whose
  stream does not exist yet (its publisher may start later). A worker that fails on a configuration mistake
  (JetStream refuses the consumer's configuration for good, the consumer's subject or ack policy would have to
  change, an option cannot work) is not started again: both probes fail, the orchestrator restarts the pod and
  the mistake shows as a crash loop.
- Stopping goes the other way, each part within its limit: the gRPC server stops taking calls and lets those in
  flight finish (`grpc_stop_grace`, 8 s), the workers are cancelled and awaited together (`worker_stop_grace`, 8 s;
  one that will not stop is logged and left behind), NATS drains (`nats_drain_timeout`, 4 s; then it is closed),
  the pool closes and the pending spans are exported (`span_flush_timeout`, 4 s; then they are left behind). A stop
  takes at most `ServiceLimits.stop_budget`, 25 s by default, whatever hangs. Kubernetes kills the pod
  `terminationGracePeriodSeconds` after asking it to stop (30 s by default), and the ASGI server drains its HTTP
  requests before the chassis's stop begins: set that grace above the server's own drain plus `stop_budget`.

**Metrics** (`/metrics`, Prometheus). Every name the chassis defines starts with `ledger_`:

| Metric | Labels | What |
|---|---|---|
| `ledger_http_request_duration_seconds` | `method`, `route`, `status` | Each HTTP request; `route` is the template the code declared, never the client's path |
| `ledger_grpc_request_duration_seconds` | `rpc`, `code` | Each call of an asynchronous gRPC method (`/package.Service/Method`, `OK`, `NOT_FOUND`...) |
| `ledger_consumer_deliveries_total` | `consumer`, `outcome` | Each delivery: `handled`, `duplicate`, `failed`, `rejected`, `malformed` |
| `ledger_outbox_pending`, `ledger_outbox_oldest_pending_timestamp_seconds` | | The outbox backlog, read from the table every 15 s (through an index on `stored_at`) |
| `ledger_outbox_published_total`, `ledger_outbox_publish_failed_total`, `ledger_outbox_failed_passes_total` | | The relay |
| `ledger_worker_restarts_total` | `worker` | Supervised restarts |
| `process_*`, `python_info`, `python_gc_*` | | prometheus-client's standard process, platform and garbage-collector collectors, under their usual names (dashboards expect them) |

The outbox gauges read the table, so every replica exports the same values: aggregate them with `max`, never
`sum`. To alert on outbox lag: `ledger_outbox_pending > 0 and time() - ledger_outbox_oldest_pending_timestamp_seconds
> 60` (the timestamp is 0 when nothing waits).

**Data that never leaves the process.**

- Logs are structlog, JSON in production, with the standard-library loggers going through the same pipeline and
  every record stamped with the service and the environment. While a handler runs, every line it logs carries the
  event's `event_id` and `event_type`, its `organization_id` and `company_id`, and the `trace_id`; a request's
  lines carry its caller's `organization_id` and `subject`, and its `company_id`, once the route's dependencies
  have read them. A traceback
  never carries the local variables of its frames: a frame that failed to connect holds the database password. An exception whose text can quote data is
  logged without that text, wherever it sits in the chain of causes, contexts and groups: an error of PostgreSQL
  or psycopg (including SQLAlchemy's wrapper) by its SQLSTATE, the names of its schema, table, column and
  constraint, and the statement, never the message (`Key (tax_id)=(...)`) or the parameters; a pydantic or FastAPI
  validation error by how many problems it has and where and of which type the first twenty are. A failed
  connection keeps libpq's words (the server and the role); any other exception keeps its message.
- Spans record an exception's type and nothing else. Every exporter drops `exception.message`,
  `exception.stacktrace`, the description of a status, and the path and query string of a request (`url.path`,
  `url.query`, `url.full`, `http.target`, `http.url`: a path carries the values of its parameters); the span's
  name and `http.route` keep the route's template.
- Errors never echo a whole input: they quote an excerpt, and a validation problem says where and of what type.
  SQLAlchemy does not echo bound values in a SQL error. A tax id is never repeated: an `InvalidTaxIdError` says what
  is wrong and how long the text was, and `repr(TaxId)` shows the kind and the last three digits.
- Traces cross the outbox: an event carries the `traceparent` of the span that added it, and a consumer's span
  continues that trace from the event alone. FastAPI's own request spans go to the service's provider and
  continue the caller's `traceparent`; the probes and `/metrics` are not traced.

## The identity service

- Every `/v1` route needs a bearer token of the organization's identity provider; the organization comes from
  the token, the company from the path. A company the caller holds no role in is a 404.
- Registering a company needs the identity provider's project role `ledger.organization.admin` (an administrator
  of the organization; 403 `forbidden` without it), and makes the caller its owner. The local Zitadel's bootstrap
  grants it to `ledger-dev`.
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
- `/readyz` checks that the database is at the schema of the running version, or at a newer one during a rollout
  (`migrations`).

## Quality gate

`uv run poe check` runs these in order, for every package; CI runs the same on Linux.

| Task | Tool | What it enforces |
|---|---|---|
| `format-check` | ruff format | Formatting (Markdown is left as written) |
| `lint` | ruff, `select = ["ALL"]` | Every rule except six: `D203` and `D213` (they conflict with `D211` and `D212`), `COM812` (the formatter), `CPY001` (no copyright header), `D105` and `D107` (docstrings of magic methods and of `__init__`). Tests also set aside `S101`, `S105`, `S106`, `S603`, `PLR2004`, `INP001` and the docstring rules (`D`) |
| `suppressions` | `git grep` | No `# noqa`, `# type: ignore`, `# pyright:`, `# mypy:`, `# ruff:` or coverage pragma in any Python file outside the generated protobuf code |
| `types` | basedpyright (`all`, warnings fail), once over `libs` and `services`; mypy `--strict` plus the optional error codes, once per package | Strict typing in two checkers |
| `arch` | import-linter | `platform` to `contracts` to `kernel`; the kernel is standard library only; contracts know the wire, not the infrastructure; production code never imports test tooling; layers inside `platform`; libraries never import services; identity is hexagonal (domain, use cases, adapters, interfaces) |
| `deps` | deptry | No unused, missing or transitive dependency, per package |
| `spell` | typos | Spelling, in code and documents |
| `proto-lint` | buf `STANDARD`, in Docker | Protobuf style and structure |
| `test` | pytest, hypothesis, testcontainers | A real PostgreSQL 18.6 and NATS 2.12.15; 100 % branch coverage; warnings are errors; random order. `tests/test_workspace.py` fails when a package under `libs/` or `services/` is missing from coverage's `source_pkgs`, import-linter's `root_packages`, the per-package mypy and deptry tasks or pytest's `testpaths` |

CI (`.github/workflows/ci.yml`) also installs with `uv sync --locked`, regenerates the protobuf code and fails on
a diff, runs `buf breaking` against `main` on pull requests, checks that the local stack's configuration is valid
(with and without the `zitadel` profile), builds identity's production image and starts the stack from it: healthy,
an anonymous call refused, gRPC not published, the image running as `nonroot`, a second migration a no-op.

Not in the gate yet (spec section 13): `pip-audit`, image scanning and secret scanning.

## Not done yet

Next:

- Mirroring Zitadel users and machine clients into identity (Zitadel Actions v2 webhooks), organization-level
  roles beyond registering companies, and a company's fiscal regime (with `fiscal-do`).
- Tracing of SQL statements and gRPC calls, a call's deadline derived from its caller's remaining deadline, and
  retries of idempotent methods: with the first service-to-service caller (milestone 3).
- The append-only audit log of security-relevant actions, with vault's signing audit (milestone 3); until then a
  membership keeps who changed it last.
- A request id for every request, in its logs and as the RFC 9457 `instance` of its problem documents.
- Envoy Gateway (rate limits, request size limits), the service mesh (Linkerd: mTLS, and a policy that admits only
  the callers of `IdentityQueryService`), Helm charts, schemathesis against the OpenAPI documents, image signing,
  SBOM and vulnerability scan: the deployment milestone.

Operations, before production:

- A migration owner separate from a runtime role that can only read and write: today one role owns the database,
  and an owner can disable its own policies. Behind PgBouncer in transaction mode the chassis's startup `options`
  (the server-side timeouts) are rejected; set them with `ALTER ROLE ... SET` there.
- A retention job for `inbox` rows, which are never deleted.
- Alerting on dead letters: nothing exports the size of `DEAD_LETTERS`; the `rejected` and `malformed` outcomes
  count the events terminated, and an event that exhausts `max_deliver` is not counted separately.
- Streams only grow: a start adds the subjects its `StreamSpec` declares and keeps the longer of each limit
  (`max_age`, 7 days by default and 30 for `DEAD_LETTERS`; `duplicate_window`, 2 minutes), so an older replica that
  restarts during a rollout can never drop a newer subject or have JetStream delete events early. Shrinking a stream
  (a subject nobody publishes any more, a shorter `max_age`) is an operations step, taken with the `nats` command
  line once no running version declares the old spec. A consumer's `max_deliver` and `ack_wait` still follow the
  latest start, so a new value must suit the older versions still running.
- A sweeper for the expired idempotency keys of an organization that stopped posting: row-level security hides
  them from any job without a scope, so it needs a maintenance role with `BYPASSRLS`, kept apart from the runtime
  role like the migration owner of the first item (each `POST` purges its own organization's).
- `IdentityQueryService` (port 50051) authenticates no caller until the mesh does: never publish the port beyond
  the services' network, and admit only its callers by mesh policy.
