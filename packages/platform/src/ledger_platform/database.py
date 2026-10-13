"""PostgreSQL: one transaction per unit of work, with the tenant enforced by row-level security.

Every transaction sets ``ledger.organization_id`` and ``ledger.company_id`` (transaction-local) from its
scope: a company of an organization (``Tenant``), or an organization alone (``OrganizationId``). Tables
protected by ``tenant_isolation`` show and accept only the rows of that organization and company; tables an
organization owns as a whole, protected by ``organization_isolation``, the rows of that organization. Without
a scope they show nothing and accept nothing (fail closed). A transaction commits only if every statement in
it succeeded, and what runs after the commit can no longer change the outcome.
"""

from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from functools import partial
from math import ceil
from types import BuiltinFunctionType, FunctionType, MethodType
from typing import ClassVar, Final, assert_type, final
lazy from collections.abc import AsyncGenerator, Callable, Sequence

import psycopg
import structlog
from psycopg.pq import TransactionStatus
from sqlalchemy import MetaData, Text, func, select, text, type_coerce
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool
lazy from sqlalchemy import Column, Table
lazy from sqlalchemy.engine import URL
lazy from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
lazy from sqlalchemy.sql.base import ReadOnlyColumnCollection

from ledger_kernel.errors import LedgerError
from ledger_kernel.ids import Tenant
from ledger_platform.options import InvalidOptionError, require_at_least
lazy from ledger_kernel.ids import OrganizationId

_DRIVERS: Final = ("postgresql+psycopg", "postgresql+psycopg_async")
"""The only driver, in the two spellings SQLAlchemy has for its asynchronous use (one dialect, one
connection class): transactions are checked through its connection (see ``_is_committable``)."""
_OWNED_PARAMETERS: Final = ("application_name", "connect_timeout", "options")
"""Connection parameters the chassis sets from ``Database.open``'s arguments: a URL must not carry them."""
SET_SCOPE: Final = text(
    """
    SELECT set_config('ledger.organization_id', :organization, true),
           set_config('ledger.company_id', :company, true)
    """
)
"""Sets the transaction's scope: run it with ``scope_settings(scope)``, before any statement of the scope."""
_PING: Final = text("SELECT 1")
_TENANT_COLUMNS: Final = ("organization_id", "company_id")
"""Each is also the name of a setting: ``ledger.organization_id``, ``ledger.company_id``."""
TENANT_POLICY: Final = "tenant_isolation"
ORGANIZATION_POLICY: Final = "organization_isolation"
_ORGANIZATION_SETTING: Final = "organization_id"
_IDENTIFIERS: Final = make_url(f"{_DRIVERS[0]}://").get_dialect()().identifier_preparer
"""Quotes a name exactly when PostgreSQL needs it quoted: DDL is text, so the names in it must be safe.

The preparer of the dialect the engine uses, found through its URL scheme (the dialect class is untyped).
"""
_SECOND: Final = timedelta(seconds=1)
_MILLISECOND: Final = timedelta(milliseconds=1)
_LONGEST: Final = timedelta(milliseconds=2**31 - 1)
"""PostgreSQL keeps its timeouts in 32-bit milliseconds and refuses every connection that asks for more
(about 24.8 days); the limits that are not PostgreSQL's follow the same ceiling."""
_logger = structlog.stdlib.get_logger(__name__)

type AnyTable = Table[ReadOnlyColumnCollection[str, Column[object]]]
"""A ``Table`` with any columns.

SQLAlchemy 2.1 gives a bare ``Table`` the default ``Table[ReadOnlyColumnCollection[str, Column[Any]]]``, and
mypy refuses the ``Any`` of that default wherever it is spelled out; ``object`` accepts the same tables.
"""

type Scope = Tenant | OrganizationId
"""Whose rows a transaction may touch: one company of an organization (``Tenant``), or an organization's own
rows (``OrganizationId``), such as its list of companies. ``None`` touches no protected row at all."""


class PlatformTable(DeclarativeBase):
    """Tables the chassis adds to every service's database (outbox, inbox, idempotency keys).

    Each table registers in ``metadata`` when the module of its class is imported (``outbox``, ``consumer``,
    ``idempotency``), so migrations create ``ledger_platform.schema.CHASSIS_METADATA`` instead, which has
    imported them all.
    """

    metadata: ClassVar[MetaData] = MetaData()


class MissingTenantColumnError(LedgerError):
    """``tenant_isolation`` asked for a table that lacks ``organization_id`` or ``company_id``."""

    code: ClassVar[str] = "missing_tenant_column"

    def __init__(self, table: str, columns: tuple[str, ...]) -> None:
        super().__init__(f"table {table!r} is missing the tenant columns {', '.join(columns)}")


class AbortedTransactionError(LedgerError):
    """A statement failed inside a transaction and the code went on: it can only be rolled back.

    A bug in the caller, never the client's input: a database error caught without a savepoint
    (``connection.begin_nested()``). PostgreSQL answers the ``COMMIT`` of such a transaction with a
    ``ROLLBACK`` and the driver reports success, so the work would be lost without a trace.
    """

    code: ClassVar[str] = "aborted_transaction"

    def __init__(self) -> None:
        super().__init__(
            "an error in the transaction was caught outside a savepoint; it was rolled back, not committed"
        )


class ClosedUnitOfWorkError(LedgerError):
    """A callback was added to a unit of work whose transaction has ended. A bug in the caller."""

    code: ClassVar[str] = "closed_unit_of_work"

    def __init__(self) -> None:
        super().__init__("the transaction has ended: add after-commit callbacks inside it, before it exits")


class InvalidDatabaseOptionError(InvalidOptionError):
    """A database option that cannot work."""

    code: ClassVar[str] = "invalid_database_option"
    label: ClassVar[str] = "database option"


def _within(option: str, value: timedelta, *, floor: timedelta, named: str) -> None:
    if not floor <= value <= _LONGEST:
        raise InvalidDatabaseOptionError(
            option=option,
            requirement=f"between {named} and {_LONGEST // _MILLISECOND} milliseconds",
            value=value,
        )


def _whole_seconds(option: str, value: timedelta) -> None:
    """For what libpq and the pool take in whole seconds: less than one would round down to none."""
    _within(option, value, floor=_SECOND, named="one second")


def _milliseconds(option: str, value: timedelta) -> None:
    """Zero would switch the limit off in PostgreSQL, and so would anything that rounds down to it."""
    _within(option, value, floor=_MILLISECOND, named="one millisecond")


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class DatabaseLimits:
    """How long the service waits on PostgreSQL, and PostgreSQL on the service, before giving up.

    The defaults are for a service that answers requests: every wait is short enough to fail inside a
    request's own deadline, and none is unlimited (a missing limit turns one stuck dependency into every
    worker stuck). A migration or a batch job passes its own, longer ``statement_timeout``.
    """

    connect_timeout: timedelta = timedelta(seconds=5)
    """To open a connection (whole seconds, at least one; libpq waits at least two). A healthy server takes
    milliseconds, so five seconds absorbs a failover blip without a request waiting for minutes."""
    statement_timeout: timedelta = timedelta(seconds=30)
    """PostgreSQL cancels a statement that runs longer. No statement of a request needs more."""
    lock_timeout: timedelta = timedelta(seconds=5)
    """PostgreSQL cancels a statement that waits longer for a lock: contention is an error to surface, not a
    queue of connections piling up."""
    idle_in_transaction_session_timeout: timedelta = timedelta(seconds=60)
    """PostgreSQL ends a session that holds a transaction open and sends nothing: its locks and its old
    snapshot hurt every other session. Longer than ``statement_timeout`` so that work between two statements
    of one transaction is never mistaken for a stall."""
    pool_timeout: timedelta = timedelta(seconds=10)
    """To get a connection from a full pool; then the caller gets an error and the request can answer 503."""
    pool_recycle: timedelta = timedelta(minutes=30)
    """A connection older than this (whole seconds, at least one) is replaced when next used, so that none
    outlives what sits between the service and PostgreSQL (proxies and load balancers drop idle ones)."""

    def __post_init__(self) -> None:
        _whole_seconds("connect_timeout", self.connect_timeout)
        _milliseconds("statement_timeout", self.statement_timeout)
        _milliseconds("lock_timeout", self.lock_timeout)
        _milliseconds("idle_in_transaction_session_timeout", self.idle_in_transaction_session_timeout)
        _milliseconds("pool_timeout", self.pool_timeout)
        _whole_seconds("pool_recycle", self.pool_recycle)


DEFAULT_LIMITS: Final = DatabaseLimits()
DEFAULT_POOL_SIZE: Final = 10
"""Connections a pool keeps, unless told otherwise; as many again are opened for bursts."""


def _require_psycopg(url: URL) -> None:
    driver = url.drivername
    if driver not in _DRIVERS:
        raise InvalidDatabaseOptionError(
            option="url driver", requirement=" or ".join(repr(name) for name in _DRIVERS), value=driver
        )


def _leave_the_owned_parameters_out(url: URL) -> None:
    """A parameter of the URL would be overwritten by the chassis's own value without a word."""
    for key in _OWNED_PARAMETERS:
        if key in url.query:
            raise InvalidDatabaseOptionError(
                option=f"url query {key}", requirement="absent (the chassis sets it)", value=url.query[key]
            )


def _matches(column: str, setting: str) -> str:
    """``column`` equals the ``ledger.<setting>`` the transaction set; no or empty setting matches nothing."""
    return f"{_IDENTIFIERS.quote(column)} = NULLIF(current_setting('ledger.{setting}', true), '')::uuid"


def ddl_name(table: AnyTable) -> str:
    """``table``'s name as DDL spells it: with its schema if it has one, quoted where PostgreSQL needs it."""
    return _IDENTIFIERS.format_table(table)


def tenant_isolation(table: AnyTable) -> tuple[str, ...]:
    """DDL (for a migration) that shows and accepts only the current tenant's rows of ``table``.

    A row belongs to the tenant when both its ``organization_id`` and its ``company_id`` are the ones the
    transaction set; both columns are required. Running the DDL again replaces the policy.

    The policy is permissive, PostgreSQL's default, and has to be: with row-level security on, a table shows
    nothing unless a permissive policy lets the row through, and a restrictive policy only narrows what the
    permissive ones allow, so a restrictive one alone would deny every row. Policies are OR-ed, so another
    permissive policy on the table would open it to more rows: narrow with restrictive ones.
    """
    missing = tuple(column for column in _TENANT_COLUMNS if column not in table.c)
    if missing:
        raise MissingTenantColumnError(table.name, missing)
    name = ddl_name(table)
    rule = " AND ".join(_matches(column, column) for column in _TENANT_COLUMNS)
    return (
        f"ALTER TABLE {name} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {name} FORCE ROW LEVEL SECURITY",
        f"DROP POLICY IF EXISTS {TENANT_POLICY} ON {name}",
        f"CREATE POLICY {TENANT_POLICY} ON {name} USING ({rule}) WITH CHECK ({rule})",
    )


def tenant_isolation_removal(table: AnyTable) -> tuple[str, ...]:
    """DDL (for a migration's downgrade) that undoes ``tenant_isolation``; harmless on a table without it."""
    name = ddl_name(table)
    return (
        f"DROP POLICY IF EXISTS {TENANT_POLICY} ON {name}",
        f"ALTER TABLE {name} NO FORCE ROW LEVEL SECURITY",
        f"ALTER TABLE {name} DISABLE ROW LEVEL SECURITY",
    )


def organization_isolation(table: AnyTable, *, column: str = _ORGANIZATION_SETTING) -> tuple[str, ...]:
    """DDL (for a migration) that shows and accepts only the current organization's rows of ``table``.

    For a table an organization owns as a whole (its companies, its memberships): a transaction scoped to the
    organization, or to any of its companies, sees all of its rows and no other organization's; without a
    scope, none. ``column`` holds the organization's id (``id`` for the table of organizations itself).
    Running the DDL again replaces the policy. Like ``tenant_isolation``'s, the policy is permissive: another
    permissive policy on the same table would widen it.
    """
    if column not in table.c:
        raise MissingTenantColumnError(table.name, (column,))
    name = ddl_name(table)
    rule = _matches(column, _ORGANIZATION_SETTING)
    return (
        f"ALTER TABLE {name} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {name} FORCE ROW LEVEL SECURITY",
        f"DROP POLICY IF EXISTS {ORGANIZATION_POLICY} ON {name}",
        f"CREATE POLICY {ORGANIZATION_POLICY} ON {name} USING ({rule}) WITH CHECK ({rule})",
    )


def organization_isolation_removal(table: AnyTable) -> tuple[str, ...]:
    """DDL (for a migration's downgrade) that undoes ``organization_isolation``; harmless without it."""
    name = ddl_name(table)
    return (
        f"DROP POLICY IF EXISTS {ORGANIZATION_POLICY} ON {name}",
        f"ALTER TABLE {name} NO FORCE ROW LEVEL SECURITY",
        f"ALTER TABLE {name} DISABLE ROW LEVEL SECURITY",
    )


type Callback = Callable[[], None]
"""Runs after a commit. A plain function: a coroutine function is rejected by the type checkers."""


@final
class _Callbacks:
    """The callbacks of one transaction: taken while it is open, handed over once when it has ended."""

    __slots__ = ("_closed", "_taken")

    def __init__(self) -> None:
        self._taken: list[Callback] = []
        self._closed = False

    def add(self, callback: Callback) -> None:
        if self._closed:
            raise ClosedUnitOfWorkError
        self._taken.append(callback)

    def close(self) -> tuple[Callback, ...]:
        """Take no more callbacks; the ones taken so far, which no one else will ever run."""
        self._closed = True
        taken = tuple(self._taken)
        self._taken.clear()
        return taken


def scope_settings(scope: Scope | None) -> dict[str, str]:
    """What ``ledger.organization_id`` and ``ledger.company_id`` are set to for ``scope``; empty if absent.

    The parameters of ``SET_SCOPE``: ``Database.transaction`` sets them, and so does a data migration that
    runs a statement in each scope (``ledger_platform.migrations.run_per_scope``).

    Exhaustive over ``Scope``: what reaches the last case must be an ``OrganizationId`` (``assert_type``), so
    a new kind of scope fails type checking here instead of being taken for an organization.
    """
    match scope:
        case None:
            return {"organization": "", "company": ""}
        case Tenant(organization=organization, company=company):
            return {"organization": str(organization), "company": str(company)}
        case organization:
            return {"organization": str(assert_type(organization, OrganizationId)), "company": ""}


@final
class UnitOfWork:
    """One open transaction: state changes, outbox and inbox commit together or not at all.

    Built by ``Database.transaction``, which closes it when the transaction ends and runs its callbacks.
    """

    __slots__ = ("_after_commit", "_connection", "_scope")

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

    @property
    def connection(self) -> AsyncConnection:
        """For repositories: every statement runs inside the transaction."""
        return self._connection

    def after_commit(self, callback: Callback) -> None:
        """Call ``callback`` once the transaction has committed; never if it rolls back.

        Callbacks run in the order added, once. They only schedule or signal. One that raises is logged and
        does not stop the others: the commit has happened and nothing a callback does can undo it.

        Add them inside the ``async with``. Once the transaction has ended, whether it committed or rolled
        back, and also from inside a callback, this raises ``ClosedUnitOfWorkError``: a callback that could
        never run must not be accepted in silence.
        """
        self._after_commit.add(callback)


async def _is_committable(connection: AsyncConnection) -> bool:
    """False once a statement of the transaction has failed.

    The driver knows without a round trip. Anything but a psycopg connection cannot be vouched for.
    """
    driver = (await connection.get_raw_connection()).driver_connection
    return (
        isinstance(driver, psycopg.AsyncConnection)
        and driver.info.transaction_status is not TransactionStatus.INERROR
    )


def _name_of(callback: Callback) -> str:
    """Where to find ``callback`` in the code: module and qualified name of the function or method behind it.

    A partial says so and names the function it wraps; any other callable is named by its type.
    """
    if isinstance(callback, partial):
        return f"partial({_name_of(callback.func)})"
    if isinstance(callback, FunctionType | MethodType | BuiltinFunctionType):
        return f"{callback.__module__ or 'builtins'}.{callback.__qualname__}"
    kind = type(callback)
    return f"{kind.__module__}.{kind.__qualname__}"


def _run(callbacks: tuple[Callback, ...]) -> None:
    """Run each callback, in order, whatever the others do."""
    for callback in callbacks:
        try:
            callback()
        except Exception:
            _logger.exception("database.after_commit_failed", callback=_name_of(callback))


def _server_options(limits: DatabaseLimits) -> str:
    """Startup options that give every session of the pool the server-side limits (in milliseconds)."""
    timeouts = {
        "statement_timeout": limits.statement_timeout,
        "lock_timeout": limits.lock_timeout,
        "idle_in_transaction_session_timeout": limits.idle_in_transaction_session_timeout,
    }
    return " ".join(f"-c {name}={timeout // _MILLISECOND}" for name, timeout in timeouts.items())


@final
class Database:
    """A service's PostgreSQL database, reached through SQLAlchemy's asynchronous psycopg driver.

    ``engine`` pools the connections of the service's work; ``probe`` is an engine of the same database
    without a pool (``NullPool``), on which ``ping`` opens a connection of its own each time. ``open`` builds
    both.
    """

    __slots__ = ("_engine", "_probe")

    def __init__(self, engine: AsyncEngine, *, probe: AsyncEngine) -> None:
        _require_psycopg(engine.url)
        _require_psycopg(probe.url)
        self._engine = engine
        self._probe = probe

    @classmethod
    @asynccontextmanager
    async def open(
        cls,
        url: str,
        *,
        pool_size: int = DEFAULT_POOL_SIZE,
        application_name: str = "ledger",
        limits: DatabaseLimits = DEFAULT_LIMITS,
    ) -> AsyncGenerator[Database]:
        """A connection pool to ``url`` (``postgresql+psycopg://``), closed on exit.

        ``application_name`` is how PostgreSQL names these connections (``pg_stat_activity``, its logs): the
        service passes its own name. Beyond ``pool_size`` the pool opens as many again, for bursts. The URL
        must not carry ``application_name``, ``connect_timeout`` or ``options``: the chassis sets them from
        its arguments.

        SQLAlchemy does not echo the values bound to a statement (tenant ids, payloads) in the text of a SQL
        error (``hide_parameters``): that text reaches logs. PostgreSQL's own message can still carry
        values, such as the ``Key (...)=(...)`` of a unique violation: the logs withhold it
        (``ledger_platform.logs``).
        """
        require_at_least(InvalidDatabaseOptionError, "pool_size", pool_size)
        parsed = make_url(url)
        _require_psycopg(parsed)
        _leave_the_owned_parameters_out(parsed)
        connect_args = {
            "application_name": application_name,
            "connect_timeout": ceil(limits.connect_timeout / _SECOND),
            "options": _server_options(limits),
        }
        engine = create_async_engine(
            url,
            pool_size=pool_size,
            max_overflow=pool_size,
            pool_timeout=limits.pool_timeout.total_seconds(),
            pool_recycle=limits.pool_recycle // _SECOND,
            pool_pre_ping=True,
            hide_parameters=True,
            connect_args=connect_args,
        )
        probe = create_async_engine(url, poolclass=NullPool, hide_parameters=True, connect_args=connect_args)
        try:
            yield cls(engine, probe=probe)
        finally:
            try:
                await engine.dispose()
            finally:  # the probe's connections are released even when the pool's close fails
                await probe.dispose()

    @property
    def engine(self) -> AsyncEngine:
        """For migrations and tests; services use ``transaction``."""
        return self._engine

    @asynccontextmanager
    async def transaction(self, scope: Scope | None) -> AsyncGenerator[UnitOfWork]:
        """A transaction scoped to ``scope``: commits on normal exit, rolls back on an exception.

        A ``Tenant`` scopes it to one company of an organization, an ``OrganizationId`` to the rows the
        organization owns as a whole, ``None`` to no protected row. It never reports a commit that did not
        happen: if a statement failed and the caller went on, the exit raises ``AbortedTransactionError`` and
        the callbacks do not run.
        """
        callbacks = _Callbacks()
        try:
            async with self._engine.begin() as connection:
                await connection.execute(SET_SCOPE, scope_settings(scope))
                yield UnitOfWork(connection, callbacks, scope)
                if not await _is_committable(connection):
                    raise AbortedTransactionError
        finally:
            after_commit = callbacks.close()  # rolled back or committed, the unit takes no more callbacks
        _run(after_commit)  # reached only when the transaction committed

    async def missing_tables(self, names: Sequence[str]) -> tuple[str, ...]:
        """Which of the tables ``names`` the database lacks (``to_regclass``, by the search path).

        On a connection of its own, like ``ping``: for a readiness check, never waiting for the pool.
        """
        found = select(*(type_coerce(func.to_regclass(name), Text) for name in names))
        async with self.probing() as connection:
            row = (await connection.execute(found)).one()
        return tuple(name for name, table in zip(names, row, strict=True) if table is None)

    async def ping(self) -> None:
        """Raises unless PostgreSQL answers, on a connection of its own (``probing``)."""
        async with self.probing() as connection:
            await connection.execute(_PING)

    @asynccontextmanager
    async def probing(self) -> AsyncGenerator[AsyncConnection]:
        """A connection of its own for a readiness check, outside the pool, closed on exit; no tenant scope.

        Never one of the pool's: a pool that every request holds is a busy service, not a broken one, and a
        readiness check that waited for it would take every replica out of rotation at the worst moment. Each
        use opens a connection, so a check that keeps passing should remember it did and stop asking.
        """
        async with self._probe.connect() as connection:
            yield connection
