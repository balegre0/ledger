# ledger

A business platform built to be sold, starting where the law forces demand: Dominican Republic electronic
invoicing (e-CF), sold as an API. Software vendors, ERPs, POS systems and online stores send a commercial
invoice as JSON; the platform turns it into a valid, signed e-CF, sends it to DGII, follows it to a final state,
receives other emitters' e-CF on the company's behalf and notifies the client. It is a set of small services
that share one chassis. The design is in
[docs/superpowers/specs/2026-10-11-ledger-platform-design.md](docs/superpowers/specs/2026-10-11-ledger-platform-design.md).

What exists today is milestone 1, the foundations: the shared libraries, the service chassis, the local stack
and the quality gate. No service exists yet; the first comes in milestone 2.

Python 3.15 · uv workspace · strictly typed in two checkers · zero suppressions · 100 % branch coverage.

## Layout

| Path | What |
|---|---|
| `libs/kernel` | `ledger_kernel`: UUIDv7 ids and the `Tenant`, the error vocabulary (a kind and a stable code per error), exact `Money`, clock, the Dominican `TaxId` (RNC or cédula). Standard library only |
| `libs/contracts` | `ledger_contracts` and the generated `ledger.*`: protobuf messages, gRPC stubs and the CloudEvents `Envelope` of every event. The `.proto` sources are in `libs/contracts/proto` (a buf module); the generated code is committed |
| `libs/platform` | `ledger_platform`: the service chassis. Settings, logs, health, the HTTP app, PostgreSQL with row-level security, the outbox, consumers, JetStream streams, tracing, the gRPC server, and the `Service` that assembles them |
| `services/` | One directory per service. Does not exist yet (milestone 2 onwards) |
| `deploy/local` | PostgreSQL and NATS for local development; the `Tiltfile` at the root runs them |
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
uv run poe            # every task
```

On Windows psycopg's asynchronous driver needs a selector event loop; the root `conftest.py` gives the tests
one. Services run in Linux containers, and CI runs on Linux.

### The local stack

```bash
docker compose -f deploy/local/compose.yaml up -d --wait   # or: tilt up (Tilt's UI is at http://localhost:10350)
docker compose -f deploy/local/compose.yaml down           # stop it; add -v to delete the data as well
```

| | Address | Notes |
|---|---|---|
| PostgreSQL 18.6 | `127.0.0.1:5432` | Database `ledger`, role `ledger_app` with password `ledger_app`. The role owns the database and is **not** a superuser and has no `BYPASSRLS`, so row-level security applies as it does in production |
| NATS 2.12.15 | `nats://127.0.0.1:4222` | JetStream on. Monitoring at `http://127.0.0.1:8222` (`/healthz?js-enabled-only=true`, `/jsz`) |

Both containers have health checks that prove they are usable (a login as `ledger_app`; JetStream enabled), so
`--wait` returns when a service could connect. A service running on the host is configured with:

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

A service is its settings, its routes, its consumers and, if it has one, its gRPC services, handed to a
`Service`. The chassis does the rest: the PostgreSQL pool, the NATS connection, the streams, the outbox relay,
the consumer workers, the gRPC server, health, metrics, traces and logs, started and stopped in a fixed order.

### A minimal service

The service's first migration creates the chassis's tables, `outbox` and `inbox` (`CHASSIS_METADATA` in
`ledger_platform.schema`), and puts every tenant table under `tenant_isolation`. The chassis creates no table
itself, and the service must find them there when it starts: without them it reports ready while the relay fails
at every pass and no event leaves. Until Alembic arrives (milestone 2) a migration is a script:

```python
from sqlalchemy import text

from ledger_platform.database import Database, tenant_isolation
from ledger_platform.schema import CHASSIS_METADATA  # it imports the outbox and the consumers, whose tables these are

async with Database.open(url) as database, database.engine.begin() as connection:
    await connection.run_sync(CHASSIS_METADATA.create_all)  # outbox and inbox
    await connection.run_sync(PAYMENTS.metadata.create_all)
    for statement in tenant_isolation(PAYMENTS):  # enable, force and create the policy
        await connection.execute(text(statement))
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

The tenant comes from the URL only so that the example runs: where it really comes from, the gateway's verified
claims, is the authorization context of milestone 2. `Money` is the only message in `ledger_contracts` so far;
a service declares its own event messages (`ledger.events.<service>.v1.PaymentRecorded`) in `libs/contracts/proto`.
`Runtime` and `RuntimeDependency` are `lazy` imports although a route signature uses them: a lazy import
resolves on first use, which is when FastAPI reads the signature (the types of a pydantic model's fields stay
ordinary imports).

The example runs no gRPC server, because `ledger_contracts` defines no service yet. With a `.proto` service and the
code `poe proto` generates from it, the registration is one function, passed as `Interfaces(http=routes, grpc=register)`:

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
| `Interfaces(http=, grpc=)` | `http` is a factory `(RuntimeDependency[S]) -> Sequence[APIRouter]`, called once when the service is built; the routes must be complete then. `grpc` is `(Runtime[S], grpc.aio.Server) -> None`, which adds the generated servicers, such as `add_<Service>Servicer_to_server(servicer, server)`; `None` runs no gRPC server. Methods must be `async def` (coroutines or async generators) to have their errors mapped |
| `Events(streams=, consumers=)` | `streams` are the `StreamSpec`s the service publishes to; every start creates a missing one (file storage) or widens an existing one to hold the spec (its subjects added, the longer of each limit kept: a stream only grows), and always `DEAD_LETTERS`; one deleted while the service runs is declared again by the relay when an event finds no stream (at most every 30 s). `consumers` is `(Runtime[S]) -> Sequence[ConsumerSpec]`, called once the service runs |
| `ServiceLimits(database=, publish_timeout=, nats_connect_timeout=, worker_stop_grace=)` | Every wait has a limit. `DatabaseLimits`: connect 5 s, statement 30 s, lock 5 s, idle in transaction 60 s, wait for a pooled connection 10 s, connection recycled after 30 min. `publish_timeout` 5 s, at most half the idle-in-transaction timeout (checked). `nats_connect_timeout` 60 s: the first connection is retried that long, then the start fails; once connected NATS is reconnected for as long as it takes. `worker_stop_grace` 10 s |
| `Runtime[S]` | What a running service works with: `settings`, `database`, `nats`, `jetstream`, `outbox`, `tracing`, `registry` (the metrics registry `/metrics` exports). A route asks for it with `Annotated[Runtime[S], Depends(runtime)]`; a consumer factory and the gRPC registration are handed it. While the service is not running, routes answer 503 `service_not_running` |
| `database.transaction(tenant)` | One PostgreSQL transaction scoped to a `Tenant` (or `None`: no tenant). `unit.connection` is for repositories, `outbox.add(unit, envelope, subject=)` stores an event in the same transaction, `unit.after_commit(callback)` runs once it has committed. It commits only if every statement succeeded: a database error caught and ignored inside it raises `AbortedTransactionError` at the exit instead of losing the work silently |
| `Envelope`, `Outbox` | An event is a protobuf message in a CloudEvents 1.0 envelope carried as NATS headers (`ce-id`, `ce-type` = the message's full name, `ce-source`, `ce-time`, `ce-subject` = company id, `ce-organizationid`, `traceparent`). `Envelope.wrap(message, source=, tenant=)` makes one, `envelope.unwrap(Message)` reads the payload back. `Outbox.add` refuses an event no relay could ever publish (a subject NATS cannot route, a message over its size limit) inside the caller's transaction |
| `ConsumerSpec(name=, stream=, subject=, handler=, max_deliver=, backoff=, ack_wait=)` | A durable JetStream pull consumer. `handler` is `async (Envelope, UnitOfWork) -> None`; the unit is the event's own transaction, scoped to its tenant, so the handler's writes commit together with the event's claim in `inbox`. The spec is the configuration: starting the consumer creates it or brings it to the spec. Its subject and its ack policy never change in place; a service that needs another subject gives the consumer a new name (`payments-settle-v2`). A stream that does not exist yet is no mistake: the consumer logs `consumer.stream_missing` and binds once its stream is there |
| `tenant_isolation(table)` | The DDL (a tuple of statements) for a migration that enables and forces row-level security on a table with `organization_id` and `company_id` columns, and creates the policy. `tenant_isolation_removal(table)` undoes it for a downgrade |
| Errors | Raise a `LedgerError` subclass: `InvalidInputError`, `NotFoundError`, `ConflictError`, `ForbiddenError`, `UnavailableError`, or your own with a stable `code`. Its message is built inside the class (`raise PaymentTooLargeError(limit)`), and `excerpt()` quotes at most 40 characters of an input |

| Variable | Default | |
|---|---|---|
| `LEDGER_SERVICE_NAME` | required | `^[a-z][a-z0-9-]{1,30}$`; names the service in logs, spans, metrics, and its PostgreSQL and NATS connections |
| `LEDGER_DATABASE_URL` | required | `postgresql+psycopg://user:password@host:5432/db`; no other driver is accepted. A secret |
| `LEDGER_NATS_URL` | `nats://localhost:4222` | `nats://` or `tls://`. A secret |
| `LEDGER_ENVIRONMENT` | `local` | `local`, `test`, `staging` or `production`; stamped on every log record |
| `LEDGER_LOG_LEVEL`, `LEDGER_LOG_FORMAT` | `INFO`, `console` | `DEBUG`, `INFO`, `WARNING` or `ERROR`; `console` or `json` |
| `LEDGER_GRPC_PORT` | `50051` | The gRPC server binds it on every interface, without TLS (the mesh does mTLS); `0` is any free port, and `service.grpc_port` says which |
| `LEDGER_HTTP_PORT` | `8000` | For the ASGI server that runs `service.app`; the chassis itself binds only the gRPC port |
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

**Fail-closed tenancy.** `database.transaction(tenant)` sets the transaction-local settings
`ledger.organization_id` and `ledger.company_id`. A table under `tenant_isolation` shows and accepts only the rows
where **both** columns equal them, and the policy is forced on the table's owner. With no tenant (`None`, as for an
event of platform-wide scope) such a table shows nothing and refuses every write. A superuser or a `BYPASSRLS` role
would ignore the policies, so a service never connects as one. `outbox` and `inbox` are outside it on purpose:
the relay reads across tenants and the inbox is claimed before the handler runs.

**One error shape.** Every error has a kind, and each kind one status per protocol:

| Kind | HTTP | gRPC |
|---|---|---|
| `INVALID` | 422 | `INVALID_ARGUMENT` |
| `NOT_FOUND` | 404 | `NOT_FOUND` |
| `CONFLICT` | 409 | `FAILED_PRECONDITION` |
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

- `/readyz` runs the checks `database` (a query), `nats` (the connection is up) and `workers` (none is dead)
  concurrently, each held to 2 seconds, and answers 200 `ready` or 503 `not_ready` with the result of each.
  Overlapping probes share one run of each check.
- `/livez` does not consult the dependencies, so a PostgreSQL outage does not restart every pod. It answers 503
  `unrecoverable` only when the process cannot recover by itself: a check has outlived ten time limits and will
  not stop, or a background worker is dead for good.
- Workers (the outbox relay, one per consumer) are supervised. One that fails is logged with its traceback and
  started again after 1 s, then 2 s, up to a minute, each delay shortened at random by up to a fifth so that
  replicas do not come back together; a run of a minute starts the delays over; `ledger_worker_restarts_total`
  counts the restarts. A worker waiting to start again is not dead: JetStream unable to serve a consumer for a
  moment fails neither probe, and NATS being unreachable fails the `nats` check alone; neither does a consumer whose
  stream does not exist yet (its publisher may start later). A worker that fails on a configuration mistake
  (JetStream refuses the consumer's configuration for good, the consumer's subject or ack policy would have to
  change, an option cannot work) is not started again: both probes fail, the orchestrator restarts the pod and
  the mistake shows as a crash loop.
- Stopping goes the other way: the gRPC server stops taking calls and lets those in flight finish (10 s), the
  workers are cancelled and awaited together within one `worker_stop_grace`, NATS drains, the pool closes and the
  pending spans are exported.

**Metrics** (`/metrics`, Prometheus): `http_request_duration_seconds` by method, route template and status (the
template the code declared, never the client's path); `ledger_events_total` by consumer and outcome (`handled`,
`duplicate`, `failed`, `rejected`, `malformed`); `ledger_outbox_pending`,
`ledger_outbox_oldest_pending_timestamp_seconds` and the counters of publishes, failed publishes and failed
passes; `ledger_worker_restarts_total`. The outbox gauges read the table (every 15 s, through an index on
`stored_at`), so every replica exports the same values: aggregate them with `max`, never `sum`. To alert on outbox lag: `ledger_outbox_pending > 0 and time() -
ledger_outbox_oldest_pending_timestamp_seconds > 60` (the timestamp is 0 when nothing waits).

**Data that never leaves the process.**

- Logs are structlog, JSON in production, with the standard-library loggers going through the same pipeline and
  every record stamped with the service and the environment. A traceback never carries the local variables of its
  frames: a frame that failed to connect holds the database password. An exception whose text can quote data is
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

## Quality gate

`uv run poe check` runs these in order, for every package; CI runs the same on Linux.

| Task | Tool | What it enforces |
|---|---|---|
| `format-check` | ruff format | Formatting (Markdown is left as written) |
| `lint` | ruff, `select = ["ALL"]` | Every rule except six: `D203` and `D213` (they conflict with `D211` and `D212`), `COM812` (the formatter), `CPY001` (no copyright header), `D105` and `D107` (docstrings of magic methods and of `__init__`). Tests also set aside `S101`, `S105`, `S106`, `S603`, `PLR2004`, `INP001` and the docstring rules (`D`) |
| `suppressions` | `git grep` | No `# noqa`, `# type: ignore`, `# pyright:`, `# mypy:`, `# ruff:` or coverage pragma in any Python file outside the generated protobuf code |
| `types` | basedpyright (`all`, warnings fail), once over `libs`; mypy `--strict` plus the optional error codes, once per package | Strict typing in two checkers |
| `arch` | import-linter | `platform` to `contracts` to `kernel`; the kernel is standard library only; contracts know the wire, not the infrastructure; production code never imports test tooling; layers inside `platform` |
| `deps` | deptry | No unused, missing or transitive dependency, per package |
| `spell` | typos | Spelling, in code and documents |
| `proto-lint` | buf `STANDARD`, in Docker | Protobuf style and structure |
| `test` | pytest, hypothesis, testcontainers | A real PostgreSQL 18.6 and NATS 2.12.15; 100 % branch coverage; warnings are errors; random order |

CI (`.github/workflows/ci.yml`) also installs with `uv sync --locked`, regenerates the protobuf code and fails on
a diff, runs `buf breaking` against `main` on pull requests, and checks that the local stack's configuration is
valid and that both containers become healthy.

Not in the gate yet (spec section 13): `pip-audit`, image scanning and secret scanning.

## Not done yet

Milestone 2 (identity) and later:

- Identity and the authorization context: tokens validated at the gateway, the principal's permissions per
  company, the request-scoped log fields (trace id, company id; nothing binds them yet), a request correlation id
  and the RFC 9457 `instance`.
- `Idempotency-Key` on the public API's `POST`s.
- Tracing of SQL and gRPC calls (FastAPI's request spans exist).
- gRPC client helpers: deadlines, propagation of identity, and the mapping of `ledger-error-code` back to domain
  errors.
- Alembic migrations, tested up and down in CI. Today a migration is a script, and `outbox` and `inbox`
  (`ledger_platform.schema`) belong in every service's first one.
- A readiness check that the chassis's tables exist. Today `/readyz` does not look: milestone 2 adds the migrations
  and the check.
- An entry point that serves `service.app` (the stack's choice is Granian), and a Dockerfile per service.
- Pagination primitives, with the first list endpoint.

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
