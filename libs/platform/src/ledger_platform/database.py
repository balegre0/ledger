"""PostgreSQL: one transaction per unit of work, with the tenant enforced by row-level security.

Every transaction sets ``ledger.organization_id`` and ``ledger.company_id`` (transaction-local). Tables
protected by ``tenant_isolation`` show and accept only the rows of that company; without a tenant they
show nothing and accept nothing (fail closed).
"""

from contextlib import asynccontextmanager
from typing import ClassVar, Final, final
lazy from collections.abc import AsyncGenerator, Callable

from sqlalchemy import MetaData, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import DeclarativeBase
lazy from sqlalchemy import Column, Table
lazy from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
lazy from sqlalchemy.sql.base import ReadOnlyColumnCollection

from ledger_kernel.errors import LedgerError
lazy from ledger_kernel.ids import Tenant

_SET_TENANT: Final = text(
    """
    SELECT set_config('ledger.organization_id', :organization, true),
           set_config('ledger.company_id', :company, true)
    """
)
_PING: Final = text("SELECT 1")
_IDENTIFIERS: Final = make_url("postgresql+psycopg://").get_dialect()().identifier_preparer
"""Quotes a name exactly when PostgreSQL needs it quoted: DDL is text, so the names in it must be safe.

The preparer of the dialect the engine uses, found through its URL scheme (the dialect class is untyped).
"""

type AnyTable = Table[ReadOnlyColumnCollection[str, Column[object]]]
"""A ``Table`` with any columns.

SQLAlchemy 2.1 gives a bare ``Table`` the default ``Table[ReadOnlyColumnCollection[str, Column[Any]]]``, and
mypy refuses the ``Any`` of that default wherever it is spelled out; ``object`` accepts the same tables.
"""


class PlatformTable(DeclarativeBase):
    """Tables the chassis adds to every service's database (outbox, inbox)."""

    metadata: ClassVar[MetaData] = MetaData()


class MissingTenantColumnError(LedgerError):
    """``tenant_isolation`` asked for a table without ``company_id``."""

    code: ClassVar[str] = "missing_tenant_column"

    def __init__(self, table: str) -> None:
        super().__init__(f"table {table!r} has no company_id column to isolate by")


def tenant_isolation(table: AnyTable) -> tuple[str, ...]:
    """DDL (for a migration) that shows and accepts only the current company's rows of ``table``."""
    if "company_id" not in table.c:
        raise MissingTenantColumnError(table.name)
    name = _IDENTIFIERS.format_table(table)
    current = "NULLIF(current_setting('ledger.company_id', true), '')::uuid"
    policy = (
        f"CREATE POLICY tenant_isolation ON {name} "
        f"USING (company_id = {current}) WITH CHECK (company_id = {current})"
    )
    return (
        f"ALTER TABLE {name} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {name} FORCE ROW LEVEL SECURITY",
        policy,
    )


@final
class UnitOfWork:
    """One open transaction: state changes, outbox and inbox commit together or not at all."""

    __slots__ = ("_after_commit", "_connection")

    def __init__(self, connection: AsyncConnection) -> None:
        self._connection = connection
        self._after_commit: list[Callable[[], object]] = []

    @property
    def connection(self) -> AsyncConnection:
        """For repositories: every statement runs inside the transaction."""
        return self._connection

    def after_commit(self, callback: Callable[[], object]) -> None:
        """Call ``callback`` once the transaction has committed; never if it rolls back.

        Callbacks run in the order added and must not raise: the data is already committed when they do.
        """
        self._after_commit.append(callback)

    def committed(self) -> None:
        """Called by ``Database`` after the commit."""
        for callback in self._after_commit:
            callback()


@final
class Database:
    """A service's PostgreSQL database."""

    __slots__ = ("_engine",)

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    @classmethod
    @asynccontextmanager
    async def open(cls, url: str, *, pool_size: int = 10) -> AsyncGenerator[Database]:
        """A connection pool to ``url``, closed on exit."""
        engine = create_async_engine(url, pool_size=pool_size, max_overflow=pool_size, pool_pre_ping=True)
        try:
            yield cls(engine)
        finally:
            await engine.dispose()

    @property
    def engine(self) -> AsyncEngine:
        """For migrations and tests; services use ``transaction``."""
        return self._engine

    @asynccontextmanager
    async def transaction(self, tenant: Tenant | None) -> AsyncGenerator[UnitOfWork]:
        """A transaction scoped to ``tenant``: commits on normal exit, rolls back on an exception."""
        async with self._engine.begin() as connection:
            await connection.execute(
                _SET_TENANT,
                {
                    "organization": "" if tenant is None else str(tenant.organization),
                    "company": "" if tenant is None else str(tenant.company),
                },
            )
            unit = UnitOfWork(connection)
            yield unit
        unit.committed()

    async def ping(self) -> None:
        """Raises unless PostgreSQL answers."""
        async with self._engine.connect() as connection:
            await connection.execute(_PING)
