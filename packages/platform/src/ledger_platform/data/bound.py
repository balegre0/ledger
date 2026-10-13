"""A database bound to one module's schema and role: the only way a module opens units (§5.3, ADR 0004).

The runtime makes one per module from its manifest (schema, the event types it publishes, its job kinds); the
module never names a role or a schema. ``unit(scope)`` begins with ``SET LOCAL ROLE <schema>_rw`` and the
scope's settings, ``purging()`` with ``SET LOCAL ROLE <schema>_purge`` and no setting at all: both are
transaction-local, so a pooled (or pgbouncer) connection carries neither into the next transaction, whether
this one committed, rolled back, failed or was cancelled.
"""

from contextlib import asynccontextmanager
from types import MappingProxyType
from typing import NotRequired, TypedDict, Unpack, final
lazy from collections.abc import AsyncGenerator, Mapping

import structlog
from sqlalchemy import text
lazy from sqlalchemy.ext.asyncio import AsyncConnection

from ledger_kernel.clock import utc_now
from ledger_platform.data.database import SET_SCOPE, AbortedTransactionError, is_committable, scope_settings
from ledger_platform.data.notifier import OUTBOX
from ledger_platform.data.roles import SchemaRoles, identifier
from ledger_platform.data.tables import chassis_tables
from ledger_platform.data.unit_of_work import Binding, UnitOfWork
lazy from ledger_kernel.clock import Clock
lazy from ledger_kernel.tenancy import Scope
lazy from ledger_platform.data.database import Database
lazy from ledger_platform.data.notifier import Signals
lazy from ledger_platform.data.tables import ChassisTables
lazy from ledger_platform.data.unit_of_work import Callback, JobPolicy

_logger = structlog.stdlib.get_logger(__name__)


class Declared(TypedDict):
    """What a module's manifest declares besides its schema; each is optional."""

    publishes: NotRequired[frozenset[str]]
    """The event types it publishes (``UndeclaredEventTypeError`` for any other); none by default."""
    jobs: NotRequired[Mapping[str, JobPolicy]]
    """Its job kinds and their limits (``UndeclaredJobKindError`` for any other); none by default."""
    clock: NotRequired[Clock]
    """Where a unit reads its time; ``utc_now`` by default."""


def _run(callbacks: tuple[Callback, ...]) -> None:
    """Each callback, in order, whatever the others do: the commit has happened and nothing can undo it."""
    for callback in callbacks:
        try:
            callback()
        except Exception:
            _logger.exception("unit.after_commit_failed")


@final
class BoundDatabase:
    """One module's units (see the module)."""

    __slots__ = ("_binding", "_clock", "_enter_purge", "_enter_rw", "_signals", "database", "schema")

    def __init__(
        self,
        database: Database,
        *,
        schema: str,
        source: str,
        signals: Signals,
        **declared: Unpack[Declared],
    ) -> None:
        roles = SchemaRoles(schema=schema)
        self.database = database
        self.schema = roles.schema
        self._enter_rw = text(f"SET LOCAL ROLE {identifier(roles.rw)}")
        self._enter_purge = text(f"SET LOCAL ROLE {identifier(roles.purge)}")
        self._signals = signals
        self._clock = declared.get("clock", utc_now)
        self._binding = Binding(
            tables=chassis_tables(roles.schema),
            source=source,
            publishes=declared.get("publishes", frozenset()),
            policies=MappingProxyType(dict(declared.get("jobs", {}))),
        )

    @property
    def tables(self) -> ChassisTables:
        """The schema's chassis tables."""
        return self._binding.tables

    @asynccontextmanager
    async def unit(self, scope: Scope | None) -> AsyncGenerator[UnitOfWork]:
        """A unit in this module's role and ``scope``: commits on normal exit, rolls back on an exception.

        It never reports a commit that did not happen: if a statement failed and the block went on, the exit
        raises ``AbortedTransactionError``. After the commit, and only then, ``ledger_outbox`` is signalled
        once if the unit published, and the after-commit callbacks run.
        """
        async with self.database.engine.begin() as connection:
            await connection.execute(self._enter_rw)
            await connection.execute(SET_SCOPE, scope_settings(scope))
            unit = UnitOfWork(connection, scope=scope, now=self._clock(), binding=self._binding)
            token = unit.mark_open()
            try:
                yield unit
                if not await is_committable(connection):
                    raise AbortedTransactionError
                await unit.flush()
            finally:
                callbacks = unit.close()
                UnitOfWork.unmark(token)
        if unit.published:
            self._signals.signal(OUTBOX)
        _run(callbacks)

    @asynccontextmanager
    async def purging(self) -> AsyncGenerator[AsyncConnection]:
        """A transaction in this schema's ``purge`` role: it reads and deletes expired idempotency keys only.

        No tenant setting is made (the purge spans organizations, and its policies need none); the purge
        deletes without ``RETURNING`` the stored responses.
        """
        async with self.database.engine.begin() as connection:
            await connection.execute(self._enter_purge)
            yield connection
