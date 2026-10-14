"""PostgreSQL: the pool of an app's connections, its limits, and what a unit of work checks of them.

``Database`` pools the connections (``BoundDatabase`` opens the units of work on it, ``data.bound``) and
probes the server on connections of their own. ``scope_settings`` is what a unit sets its scope from, and
``is_committable`` whether its transaction can still commit.
"""

from abc import ABCMeta, abstractmethod
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from math import ceil
from typing import ClassVar, Final, Protocol, final
lazy from collections.abc import AsyncGenerator

import psycopg
import structlog
from psycopg.pq import TransactionStatus
from sqlalchemy import event, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
lazy from sqlalchemy import Column, Table
lazy from sqlalchemy.engine import URL
lazy from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
lazy from sqlalchemy.pool import ConnectionPoolEntry, PoolResetState
lazy from sqlalchemy.sql.base import ReadOnlyColumnCollection

from ledger_kernel.errors import LedgerError
from ledger_platform.config.options import InvalidOptionError, require_at_least
from ledger_platform.config.settings import DATABASE_URL_SCHEME
lazy from ledger_kernel.tenancy import Scope

_COMPONENT: Final = "database"
"""What this module's option errors name (``InvalidOptionError.component``)."""

_DRIVER: Final = DATABASE_URL_SCHEME.removesuffix("://")
_DRIVERS: Final = (_DRIVER, f"{_DRIVER}_async")
"""The only driver, in the two spellings SQLAlchemy has for its asynchronous use (one dialect, one
connection class): transactions are checked through its connection (see ``is_committable``)."""
_OWNED_PARAMETERS: Final = ("application_name", "connect_timeout", "options")
"""Connection parameters the chassis sets from ``Database.create``'s arguments: a URL must not carry them."""
_PING: Final = text("SELECT 1")
_SECOND: Final = timedelta(seconds=1)
MILLISECOND: Final = timedelta(milliseconds=1)
"""The unit of PostgreSQL's timeouts (``statement_timeout``, ``lock_timeout``, ...)."""
LONGEST_TIMEOUT: Final = timedelta(milliseconds=2**31 - 1)
"""PostgreSQL keeps its timeouts in 32-bit milliseconds and refuses every connection that asks for more
(about 24.8 days); the limits that are not PostgreSQL's follow the same ceiling."""
_logger = structlog.stdlib.get_logger(__name__)

type AnyTable = Table[ReadOnlyColumnCollection[str, Column[object]]]
"""A ``Table`` with any columns.

SQLAlchemy 2.1 gives a bare ``Table`` the default ``Table[ReadOnlyColumnCollection[str, Column[Any]]]``, and
mypy refuses the ``Any`` of that default wherever it is spelled out; ``object`` accepts the same tables.
"""


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
    """Work was added to a unit of work whose transaction has ended. A bug in the caller."""

    code: ClassVar[str] = "closed_unit_of_work"

    def __init__(self) -> None:
        super().__init__("the transaction has ended: add its work and callbacks inside it, before it exits")


def _within(option: str, value: timedelta, *, floor: timedelta, named: str) -> None:
    if not floor <= value <= LONGEST_TIMEOUT:
        raise InvalidOptionError(
            component=_COMPONENT,
            option=option,
            requirement=f"between {named} and {LONGEST_TIMEOUT // MILLISECOND} milliseconds",
            value=value,
        )


def _whole_seconds(option: str, value: timedelta) -> None:
    """For what libpq and the pool take in whole seconds: less than one would round down to none."""
    _within(option, value, floor=_SECOND, named="one second")


def _milliseconds(option: str, value: timedelta) -> None:
    """Zero would switch the limit off in PostgreSQL, and so would anything that rounds down to it."""
    _within(option, value, floor=MILLISECOND, named="one millisecond")


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


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Pooling:
    """How a pool grows past its size, and what sits between it and PostgreSQL."""

    overflow: int | None = None
    """Connections opened beyond the pool's size for a burst, closed once given back; as many as its size by
    default."""
    through_pooler: bool = False
    """The URL goes through a transaction pooler (§6.7): psycopg then prepares no statement, since a statement
    it prepares on a session is gone when the pooler hands the next transaction another session."""


DEFAULT_POOLING: Final = Pooling()
DEFAULT_POOL_SIZE: Final = 10
"""Connections a pool keeps, unless told otherwise; as many again are opened for bursts."""


def _require_psycopg(url: URL) -> None:
    driver = url.drivername
    if driver not in _DRIVERS:
        raise InvalidOptionError(
            component=_COMPONENT,
            option="url driver",
            requirement=" or ".join(repr(name) for name in _DRIVERS),
            value=driver,
        )


def _leave_the_owned_parameters_out(url: URL) -> None:
    """A parameter of the URL would be overwritten by the chassis's own value without a word."""
    for key in _OWNED_PARAMETERS:
        if key in url.query:
            raise InvalidOptionError(
                component=_COMPONENT,
                option=f"url query {key}",
                requirement="absent (the chassis sets it)",
                value=url.query[key],
            )


def scope_settings(scope: Scope | None) -> dict[str, str]:
    """What ``ledger.organization_id``, ``ledger.company_id`` and ``ledger.mode`` are set to; empty if absent.

    A unit of work sets them as it begins (``BoundDatabase``), transaction-local, from its scope.
    """
    if scope is None:
        return {"organization": "", "company": "", "mode": ""}
    company, mode = scope.company, scope.mode
    return {
        "organization": str(scope.organization),
        "company": "" if company is None else str(company),
        "mode": "" if mode is None else mode.value,
    }


async def is_committable(connection: AsyncConnection) -> bool:
    """False once a statement of the transaction has failed.

    The driver knows without a round trip. Anything but a psycopg connection cannot be vouched for.
    """
    driver = (await connection.get_raw_connection()).driver_connection
    return (
        isinstance(driver, psycopg.AsyncConnection)
        and driver.info.transaction_status is not TransactionStatus.INERROR
    )


_SESSION_RESET: Final = "RESET ROLE; RESET ALL"
"""What a returned connection forgets: a role and any setting made for the session, in one round trip.

``RESET ALL`` goes back to the connection's startup values (``application_name`` and the limits of
``options``), and neither statement drops psycopg's prepared statements (``DISCARD ALL`` would). Without
parameters psycopg sends both in one simple query.
"""


class Cursor(Protocol, metaclass=ABCMeta):
    """What ``reset_session`` uses of a DB-API cursor."""

    @abstractmethod
    def execute(self, operation: str, /) -> object:
        """Run ``operation``."""

    @abstractmethod
    def close(self) -> None:
        """Release the cursor."""


class Session(Protocol, metaclass=ABCMeta):
    """What ``reset_session`` uses of a DB-API connection (SQLAlchemy hands it the adapted psycopg one)."""

    @abstractmethod
    def cursor(self) -> Cursor:
        """A cursor on the connection."""

    @abstractmethod
    def rollback(self) -> None:
        """End the open transaction, if any."""

    @property
    @abstractmethod
    def autocommit(self) -> bool:
        """Whether each statement runs in a transaction of its own (else the first opens one)."""

    @autocommit.setter
    @abstractmethod
    def autocommit(self, value: bool) -> None:
        """Switch autocommit on or off (outside a transaction)."""


def reset_session(
    dbapi_connection: Session, connection_record: ConnectionPoolEntry | None, reset_state: PoolResetState
) -> None:
    """The pool's ``reset`` hook: a connection goes back with no role and no session setting.

    Units only ever set transaction-local state (``SET LOCAL``), which the rollback ends; this covers code
    that set session state on a pooled connection (``SET ROLE``, ``SET ledger.x``), deliberately or by
    mistake, so it never reaches the next borrower. After the rollback the connection runs the two resets in
    autocommit, as one statement (one round trip, no ``BEGIN`` or ``COMMIT``), and goes back to opening
    transactions. A reset that fails raises, and the pool invalidates the connection instead of handing it on.
    A connection being closed (``terminate_only``) or collected outside the event loop is left alone: no one
    borrows it again.
    """
    del connection_record
    if reset_state.terminate_only or not reset_state.asyncio_safe:
        return
    dbapi_connection.rollback()
    dbapi_connection.autocommit = True
    try:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute(_SESSION_RESET)
        finally:
            cursor.close()
    finally:
        dbapi_connection.autocommit = False


def _server_options(limits: DatabaseLimits) -> str:
    """Startup options that give every session of the pool the server-side limits (in milliseconds)."""
    timeouts = {
        "statement_timeout": limits.statement_timeout,
        "lock_timeout": limits.lock_timeout,
        "idle_in_transaction_session_timeout": limits.idle_in_transaction_session_timeout,
    }
    return " ".join(f"-c {name}={timeout // MILLISECOND}" for name, timeout in timeouts.items())


@final
class Database:
    """A service's PostgreSQL database, reached through SQLAlchemy's asynchronous psycopg driver.

    ``engine`` pools the connections of the service's work; ``probe`` is an engine of the same database
    without a pool (``NullPool``), on which ``ping`` opens a connection of its own each time. ``create``
    builds both.
    """

    __slots__ = ("_engine", "_probe")

    def __init__(self, engine: AsyncEngine, *, probe: AsyncEngine) -> None:
        _require_psycopg(engine.url)
        _require_psycopg(probe.url)
        self._engine = engine
        self._probe = probe

    @classmethod
    def create(
        cls,
        url: str,
        *,
        pool_size: int = DEFAULT_POOL_SIZE,
        application_name: str = "ledger",
        limits: DatabaseLimits = DEFAULT_LIMITS,
        pooling: Pooling = DEFAULT_POOLING,
    ) -> Database:
        """A connection pool to ``url`` (``postgresql+psycopg://``), not yet connected; ``aclose`` closes it.

        No I/O: SQLAlchemy connects on first use. ``application_name`` is how PostgreSQL names these
        connections (``pg_stat_activity``, its logs): the service passes its own name. Beyond ``pool_size``
        the pool opens ``pooling.overflow`` more for bursts (as many again by default). The URL must not
        carry ``application_name``, ``connect_timeout`` or ``options``: the chassis sets them from its
        arguments.

        SQLAlchemy does not echo the values bound to a statement (tenant ids, payloads) in the text of a SQL
        error (``hide_parameters``): that text reaches logs. PostgreSQL's own message can still carry
        values, such as the ``Key (...)=(...)`` of a unique violation: the logs withhold it
        (``ledger_platform.observability.logs``).
        """
        require_at_least(_COMPONENT, "pool_size", pool_size)
        overflow = pool_size if pooling.overflow is None else pooling.overflow
        require_at_least(_COMPONENT, "overflow", overflow, 0)
        parsed = make_url(url)
        _require_psycopg(parsed)
        _leave_the_owned_parameters_out(parsed)
        connect_args: dict[str, object] = {
            "application_name": application_name,
            "connect_timeout": ceil(limits.connect_timeout / _SECOND),
            "options": _server_options(limits),
        }
        if pooling.through_pooler:
            connect_args["prepare_threshold"] = None
        engine = create_async_engine(
            url,
            pool_size=pool_size,
            max_overflow=overflow,
            pool_timeout=limits.pool_timeout.total_seconds(),
            pool_recycle=limits.pool_recycle // _SECOND,
            pool_pre_ping=True,
            hide_parameters=True,
            connect_args=connect_args,
        )
        event.listen(engine.sync_engine, "reset", reset_session)
        probe = create_async_engine(url, poolclass=NullPool, hide_parameters=True, connect_args=connect_args)
        return cls(engine, probe=probe)

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
        """``create``'s pool, closed on exit (``aclose``)."""
        database = cls.create(url, pool_size=pool_size, application_name=application_name, limits=limits)
        try:
            yield database
        finally:
            await database.aclose()

    async def aclose(self) -> None:
        """Close the pool's connections, then the probe's, the second even when the first close fails."""
        try:
            await self._engine.dispose()
        finally:
            await self._probe.dispose()

    @property
    def engine(self) -> AsyncEngine:
        """The pool: units of work run on it (``BoundDatabase``), and so do migrations and tests."""
        return self._engine

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
