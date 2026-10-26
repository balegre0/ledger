"""``CompanyOptions`` over one transaction (SQLAlchemy Core): a company's delivery options in one mode.

Bound to its unit's tenant (a company in a mode) and naming it in every statement, as row-level security does
again (§2). No row is the defaults; setting them inserts the row, or changes it with its version one more.
"""

from typing import final, override
lazy from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
lazy from sqlalchemy.ext.asyncio import AsyncConnection

from ledger_country_do.channels.ecf_v1.adapters.postgres.tables import CompanyOptionsRecord
from ledger_country_do.channels.ecf_v1.application.documents import CompanyOptions, DeliveryOptions
lazy from ledger_kernel.tenancy import Mode, Tenant


@final
class PgCompanyOptions(CompanyOptions):
    """The options of one company in one mode, in PostgreSQL."""

    __slots__ = ("_connection", "_mode", "_now", "_tenant")

    def __init__(self, connection: AsyncConnection, tenant: Tenant, mode: Mode, *, now: datetime) -> None:
        """``tenant``: the unit's company, in ``mode`` (its own); ``now``: the unit's time."""
        self._connection = connection
        self._tenant = tenant
        self._mode = mode
        self._now = now

    def _owner(self) -> dict[str, object]:
        return {
            "organization_id": self._tenant.organization,
            "company_id": self._tenant.company,
            "mode": self._mode.value,
        }

    @override
    async def get(self) -> DeliveryOptions:
        owner = self._owner()
        found = (
            await self._connection.execute(
                select(CompanyOptionsRecord.buyer_authentication).where(
                    CompanyOptionsRecord.organization_id == owner["organization_id"],
                    CompanyOptionsRecord.company_id == owner["company_id"],
                    CompanyOptionsRecord.mode == owner["mode"],
                )
            )
        ).scalar_one_or_none()
        return DeliveryOptions() if found is None else DeliveryOptions(buyer_authentication=found)

    @override
    async def put(self, options: DeliveryOptions) -> None:
        values = {"buyer_authentication": options.buyer_authentication, "updated_at": self._now}
        statement = insert(CompanyOptionsRecord).values(**self._owner(), **values, version=1)
        await self._connection.execute(
            statement.on_conflict_do_update(
                index_elements=["organization_id", "company_id", "mode"],
                set_={**values, "version": CompanyOptionsRecord.version + 1},
            )
        )
