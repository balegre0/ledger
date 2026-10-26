"""``Routings`` over one transaction (SQLAlchemy Core).

Bound to its unit's tenant, named in every statement (row-level security enforces it again, §2). Adding is
one ``INSERT ... ON CONFLICT DO NOTHING`` on (tenant, document, channel), then a read of the row kept: the
first routing of a document to a channel wins, and a second one is answered it. A routing is never updated
or deleted (the role holds no such grant).
"""

from typing import Final, final, override
lazy from datetime import datetime
lazy from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
lazy from sqlalchemy import ColumnElement, Row, Select
lazy from sqlalchemy.ext.asyncio import AsyncConnection

from ledger_fiscal.adapters.postgres.tables import RoutingRecord
from ledger_fiscal.application.ports import Routings
from ledger_fiscal.domain.enrollments import EnrollmentId
from ledger_fiscal.domain.routing import Routing
lazy from ledger_kernel.tenancy import Mode, Tenant

_ROUTING: Final = select(
    RoutingRecord.invoice_id, RoutingRecord.channel, RoutingRecord.enrollment_id, RoutingRecord.routed_at
)
type _Query = Select[UUID, str, UUID, datetime]
type _Row = Row[UUID, str, UUID, datetime]


def _routing(row: _Row) -> Routing:
    invoice, channel, enrollment, routed_at = row
    return Routing(invoice=invoice, channel=channel, enrollment=EnrollmentId(enrollment), routed_at=routed_at)


@final
class PgRoutings(Routings):
    """The routings of one company in one mode, in PostgreSQL."""

    __slots__ = ("_connection", "_mode", "_tenant")

    def __init__(self, connection: AsyncConnection, tenant: Tenant, mode: Mode) -> None:
        self._connection = connection
        self._tenant = tenant
        self._mode = mode

    def _mine(self) -> tuple[ColumnElement[bool], ...]:
        return (
            RoutingRecord.organization_id == self._tenant.organization,
            RoutingRecord.company_id == self._tenant.company,
            RoutingRecord.mode == self._mode.value,
        )

    def _of(self, invoice: UUID) -> _Query:
        return _ROUTING.where(*self._mine(), RoutingRecord.invoice_id == invoice)

    @override
    async def add(self, routing: Routing) -> Routing:
        _ = await self._connection.execute(
            insert(RoutingRecord)
            .values(
                organization_id=self._tenant.organization,
                company_id=self._tenant.company,
                mode=self._mode.value,
                invoice_id=routing.invoice,
                channel=routing.channel,
                enrollment_id=routing.enrollment,
                routed_at=routing.routed_at,
            )
            .on_conflict_do_nothing()
        )
        kept = await self.get(routing.invoice, routing.channel)
        return routing if kept is None else kept

    @override
    async def of(self, invoice: UUID) -> tuple[Routing, ...]:
        rows = (await self._connection.execute(self._of(invoice).order_by(RoutingRecord.channel))).all()
        return tuple(_routing(row) for row in rows)

    @override
    async def get(self, invoice: UUID, channel: str) -> Routing | None:
        row = (
            await self._connection.execute(self._of(invoice).where(RoutingRecord.channel == channel))
        ).first()
        return None if row is None else _routing(row)
