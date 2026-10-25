"""``Ranges`` over one transaction (SQLAlchemy Core): a company's series, ranges and released numbers.

The repository is bound to its unit's tenant (a company in a mode) and names it in every statement;
row-level security enforces it again (§2). A series' row is its lock: ``series`` upserts it, so the first use
makes it and every use locks it (``ON CONFLICT DO UPDATE`` counts the use in its ``version``) until the unit
ends; registrations and allocations of a series therefore run one after the other, and two units never take
one number. An allocation applies ``numbering.choose`` under that lock: a released number taken is deleted, a
range's ``next`` moves on (its ``version`` with it). A number DGII released is kept until its
``available_at`` (``release``; Decision 9). A list is read in its page's direction.
"""

from typing import Final, final, override
lazy from datetime import date, datetime
lazy from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
lazy from sqlalchemy import ColumnElement, Row, Select
lazy from sqlalchemy.ext.asyncio import AsyncConnection

from ledger_country_do.channels.ecf_v1.adapters.postgres.tables import (
    NumberingRangeRecord,
    NumberingSeriesRecord,
    ReleasedNumberRecord,
)
from ledger_country_do.channels.ecf_v1.application.ranges import NumberingRange, Ranges
from ledger_country_do.channels.ecf_v1.domain.encf import EcfType
from ledger_country_do.channels.ecf_v1.domain.environments import Environment
from ledger_country_do.channels.ecf_v1.domain.numbering import (
    NoNumberAvailableError,
    RangeState,
    RangeStatus,
    Released,
    choose,
)
from ledger_kernel.pagination import page_of
from ledger_platform.data import paged
lazy from ledger_country_do.channels.ecf_v1.domain.encf import Encf
lazy from ledger_country_do.channels.ecf_v1.domain.numbering import Allocation
lazy from ledger_kernel.pagination import Page, PageRequest
lazy from ledger_kernel.tenancy import Mode, Tenant

_RANGE: Final = select(
    NumberingRangeRecord.id,
    NumberingRangeRecord.environment,
    NumberingRangeRecord.ecf_type,
    NumberingRangeRecord.first,
    NumberingRangeRecord.last,
    NumberingRangeRecord.next,
    NumberingRangeRecord.expires_on,
    NumberingRangeRecord.status,
    NumberingRangeRecord.created_at,
    NumberingRangeRecord.version,
)
type _Query = Select[UUID, str, str, int, int, int, date | None, str, datetime, int]
type _Row = Row[UUID, str, str, int, int, int, date | None, str, datetime, int]


def _registered(row: _Row) -> NumberingRange:
    identifier, environment, ecf_type, first, last, next_, expires_on, status, created_at, version = row
    state = RangeState(
        id=identifier,
        ecf_type=EcfType(ecf_type),
        first=first,
        last=last,
        next=next_,
        expires_on=expires_on,
        status=RangeStatus(status),
    )
    return NumberingRange(
        environment=Environment(environment), range=state, created_at=created_at, version=version
    )


@final
class PgRanges(Ranges):
    """The numbering of one company in one mode, in PostgreSQL."""

    __slots__ = ("_connection", "_mode", "_tenant")

    def __init__(self, connection: AsyncConnection, tenant: Tenant, mode: Mode) -> None:
        """``tenant`` is the unit's company, in ``mode`` (its own: a unit without one keeps no numbering)."""
        self._connection = connection
        self._tenant = tenant
        self._mode = mode

    def _owner(self) -> dict[str, object]:
        return {
            "organization_id": self._tenant.organization,
            "company_id": self._tenant.company,
            "mode": self._mode.value,
        }

    def _mine(self) -> tuple[ColumnElement[bool], ...]:
        owner = self._owner()
        return (
            NumberingRangeRecord.organization_id == owner["organization_id"],
            NumberingRangeRecord.company_id == owner["company_id"],
            NumberingRangeRecord.mode == owner["mode"],
        )

    def _selected(self) -> _Query:
        return _RANGE.where(*self._mine())

    async def _ranges(self, statement: _Query) -> tuple[NumberingRange, ...]:
        rows = (await self._connection.execute(statement)).all()
        return tuple(_registered(row) for row in rows)

    @override
    async def series(self, environment: Environment, ecf_type: EcfType) -> tuple[RangeState, ...]:
        key = {**self._owner(), "environment": environment.value, "ecf_type": ecf_type.value}
        locking = (
            insert(NumberingSeriesRecord)
            .values(**key, version=1)
            .on_conflict_do_update(
                index_elements=tuple(key), set_={"version": NumberingSeriesRecord.version + 1}
            )
        )
        await self._connection.execute(locking)
        found = await self._ranges(
            self._selected()
            .where(
                NumberingRangeRecord.environment == environment.value,
                NumberingRangeRecord.ecf_type == ecf_type.value,
            )
            .order_by(NumberingRangeRecord.first)
        )
        return tuple(registered.range for registered in found)

    @override
    async def add(self, registered: NumberingRange) -> None:
        state = registered.range
        await self._connection.execute(
            insert(NumberingRangeRecord).values(
                **self._owner(),
                id=state.id,
                environment=registered.environment.value,
                ecf_type=state.ecf_type.value,
                first=state.first,
                last=state.last,
                next=state.next,
                expires_on=state.expires_on,
                status=state.status.value,
                created_at=registered.created_at,
                version=registered.version,
            )
        )

    @override
    async def get(self, range_id: UUID) -> NumberingRange | None:
        found = await self._ranges(self._selected().where(NumberingRangeRecord.id == range_id))
        return found[0] if found else None

    @override
    async def page(
        self, request: PageRequest, *, environment: Environment | None, ecf_type: EcfType | None
    ) -> Page[NumberingRange]:
        statement = self._selected()
        if environment is not None:
            statement = statement.where(NumberingRangeRecord.environment == environment.value)
        if ecf_type is not None:
            statement = statement.where(NumberingRangeRecord.ecf_type == ecf_type.value)
        found = await self._ranges(paged(statement, NumberingRangeRecord.id, request))
        return page_of(found, request, key=lambda registered: registered.id)

    @override
    async def of(self, environment: Environment) -> tuple[RangeState, ...]:
        found = await self._ranges(
            self._selected().where(NumberingRangeRecord.environment == environment.value)
        )
        return tuple(registered.range for registered in found)

    def _of_series(self, environment: Environment, ecf_type: EcfType) -> tuple[ColumnElement[bool], ...]:
        owner = self._owner()
        return (
            ReleasedNumberRecord.organization_id == owner["organization_id"],
            ReleasedNumberRecord.company_id == owner["company_id"],
            ReleasedNumberRecord.mode == owner["mode"],
            ReleasedNumberRecord.environment == environment.value,
            ReleasedNumberRecord.ecf_type == ecf_type.value,
        )

    async def _released(self, environment: Environment, ecf_type: EcfType) -> list[Released]:
        rows = await self._connection.execute(
            select(ReleasedNumberRecord.sequence, ReleasedNumberRecord.available_at).where(
                *self._of_series(environment, ecf_type)
            )
        )
        return [
            Released(ecf_type=ecf_type, sequence=sequence, available_at=available_at)
            for sequence, available_at in rows.all()
        ]

    @override
    async def available(self, environment: Environment, ecf_type: EcfType, *, now: datetime) -> bool:
        ranges = await self._ranges(
            self._selected().where(
                NumberingRangeRecord.environment == environment.value,
                NumberingRangeRecord.ecf_type == ecf_type.value,
            )
        )
        released = await self._released(environment, ecf_type)
        try:
            choose(
                ecf_type=ecf_type,
                ranges=[registered.range for registered in ranges],
                released=released,
                now=now,
            )
        except NoNumberAvailableError:
            return False
        return True

    @override
    async def allocate(self, environment: Environment, ecf_type: EcfType, *, now: datetime) -> Allocation:
        ranges = await self.series(environment, ecf_type)
        of_series = self._of_series(environment, ecf_type)
        released = await self._released(environment, ecf_type)
        allocation = choose(ecf_type=ecf_type, ranges=ranges, released=released, now=now)
        if allocation.released:
            await self._connection.execute(
                delete(ReleasedNumberRecord).where(
                    *of_series, ReleasedNumberRecord.sequence == allocation.encf.sequence
                )
            )
        else:
            await self._connection.execute(
                update(NumberingRangeRecord)
                .where(*self._mine(), NumberingRangeRecord.id == allocation.range_id)
                .values(next=NumberingRangeRecord.next + 1, version=NumberingRangeRecord.version + 1)
            )
        return allocation

    @override
    async def release(
        self,
        environment: Environment,
        encf: Encf,
        *,
        document: UUID,
        released_at: datetime,
        available_at: datetime,
    ) -> None:
        await self._connection.execute(
            insert(ReleasedNumberRecord).values(
                **self._owner(),
                environment=environment.value,
                ecf_type=encf.ecf_type.value,
                sequence=encf.sequence,
                released_at=released_at,
                available_at=available_at,
                document_id=document,
            )
        )
