"""A database bound to one module's schema and role: the only way a module opens units (§5.3, ADR 0004).

The runtime makes one per module from its manifest (schema, the event types it publishes, its job kinds); the
module never names a role or a schema. ``unit(scope)`` begins with ``SET LOCAL ROLE <schema>_rw`` and the
scope's settings, ``purging()`` with ``SET LOCAL ROLE <schema>_purge`` and no setting at all: both are
transaction-local, so a pooled (or pgbouncer) connection carries neither into the next transaction, whether
this one committed, rolled back, failed or was cancelled (the pool also resets every connection it takes
back: ``Database``). One task holds one unit at a time (rule 13): a unit or a purge opened inside an open unit
is refused.

A unit's time is the database's: ``now()``, the transaction's start, read with the scope. Every time a unit
writes (``occurred_at``, ``run_at``, ``deadline_at``) and every due or lease comparison (``now()`` in SQL)
are on that one clock, whatever the host's says (§3.6).
"""

from contextlib import asynccontextmanager
from types import MappingProxyType
from typing import ClassVar, Final, NotRequired, TypedDict, Unpack, final
lazy from collections.abc import AsyncGenerator, Mapping

import structlog
from pydantic import AwareDatetime, TypeAdapter
from sqlalchemy import text
lazy from sqlalchemy.ext.asyncio import AsyncConnection

from ledger_kernel.errors import LedgerError
from ledger_platform.data.database import AbortedTransactionError, is_committable, scope_settings
from ledger_platform.data.notifier import OUTBOX
from ledger_platform.data.roles import SchemaRoles, identifier
from ledger_platform.data.tables import chassis_tables
from ledger_platform.data.unit_of_work import Binding, Purge, UnitOfWork, open_unit, unmark_open
lazy from ledger_kernel.tenancy import Scope
lazy from ledger_platform.data.database import Database
lazy from ledger_platform.data.notifier import Signals
lazy from ledger_platform.data.tables import ChassisTables
lazy from ledger_platform.data.unit_of_work import Callback, JobPolicy

_BEGIN: Final = text(
    """
    SELECT set_config('ledger.organization_id', :organization, true),
           set_config('ledger.company_id', :company, true),
           set_config('ledger.mode', :mode, true),
           now()
    """
)
"""The unit's scope (``scope_settings``) and its time, in one round trip."""
_MOMENT: Final = TypeAdapter(AwareDatetime)
_logger = structlog.stdlib.get_logger(__name__)


class NestedUnitError(LedgerError):
    """A unit opened while the task holds one: two transactions and their locks (rule 13), a defect."""

    code: ClassVar[str] = "unit_nested"

    def __init__(self, schema: str) -> None:
        super().__init__(f"a {schema} transaction was opened inside an open unit: finish that unit first")


class Declared(TypedDict):
    """What a module's manifest declares besides its schema; each is optional."""

    publishes: NotRequired[frozenset[str]]
    """The event types it publishes (``UndeclaredEventTypeError`` for any other); none by default."""
    jobs: NotRequired[Mapping[str, JobPolicy]]
    """Its job kinds and their limits (``UndeclaredJobKindError`` for any other); none by default."""


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

    __slots__ = ("_binding", "_database", "_enter_purge", "_enter_rw", "_signals", "schema")

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
        self._database = database
        self.schema = roles.schema
        self._enter_rw = text(f"SET LOCAL ROLE {identifier(roles.rw)}")
        self._enter_purge = text(f"SET LOCAL ROLE {identifier(roles.purge)}")
        self._signals = signals
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
        self._refuse_nesting()
        async with self._database.engine.begin() as connection:
            await connection.execute(self._enter_rw)
            began = (await connection.execute(_BEGIN, scope_settings(scope))).one()
            unit = UnitOfWork(
                connection, scope=scope, now=_MOMENT.validate_python(began[3]), binding=self._binding
            )
            token = unit.mark_open()
            try:
                yield unit
                if not await is_committable(connection):
                    raise AbortedTransactionError
                await unit.flush()
            finally:
                callbacks = unit.close()
                unmark_open(token)
        if unit.published:
            self._signals.signal(OUTBOX)
        _run(callbacks)

    @asynccontextmanager
    async def purging(self) -> AsyncGenerator[AsyncConnection]:
        """A transaction in this schema's ``purge`` role: it reads and deletes expired idempotency keys only.

        No tenant setting is made (the purge spans organizations, and its policies need none); the purge
        deletes without ``RETURNING`` the stored responses. It is the task's open transaction while it lasts
        (``open_unit``), so a unit opened inside it is refused like one inside a unit.
        """
        self._refuse_nesting()
        purge = Purge()
        token = purge.mark_open()
        try:
            async with self._database.engine.begin() as connection:
                await connection.execute(self._enter_purge)
                yield connection
        finally:
            purge.close()
            unmark_open(token)

    def policy_of(self, kind: str) -> JobPolicy:
        """The limits of job kind ``kind``; ``UndeclaredJobKindError`` if the manifest does not declare it."""
        return self._binding.policy_of(kind)

    def _refuse_nesting(self) -> None:
        if open_unit() is not None:
            raise NestedUnitError(self.schema)
