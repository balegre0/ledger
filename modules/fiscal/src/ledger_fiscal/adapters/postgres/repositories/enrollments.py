"""``Enrollments`` over one transaction (SQLAlchemy Core).

The repository is bound to its unit's tenant (a company in a mode) and names it in every statement;
row-level security enforces it again (§2). Adding uses ``ON CONFLICT DO NOTHING`` against the partial unique
index of one enrollment not disabled per company, mode and channel, and reads the outcome from ``RETURNING``:
a taken channel is an answer (its holder), not an exception to parse. An enrollment of another tenant handed
to it is refused before any statement (``ForeignTenantError``). A list is read in its page's direction.
"""

from typing import ClassVar, Final, final, override
lazy from datetime import datetime
lazy from uuid import UUID

from pydantic import TypeAdapter
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.sql import text
lazy from sqlalchemy import ColumnElement, Row, Select
lazy from sqlalchemy.ext.asyncio import AsyncConnection

from ledger_fiscal.adapters.postgres.tables import EnrollmentRecord
from ledger_fiscal.application.ports import Enrollments
from ledger_fiscal.domain.enrollments import Enrollment, EnrollmentId, EnrollmentStatus
from ledger_kernel.errors import LedgerError
from ledger_kernel.ids import CompanyId, OrganizationId
from ledger_kernel.pagination import page_of
from ledger_kernel.tenancy import Mode
from ledger_platform.data import paged
lazy from ledger_kernel.pagination import Page, PageRequest
lazy from ledger_kernel.tenancy import Tenant

_DUE: Final = TypeAdapter(tuple[str, ...])
"""``currently_due`` as the driver reads a ``text[]``: a list, read back as the domain's tuple."""
_ENROLLMENT: Final = select(
    EnrollmentRecord.id,
    EnrollmentRecord.organization_id,
    EnrollmentRecord.company_id,
    EnrollmentRecord.mode,
    EnrollmentRecord.channel,
    EnrollmentRecord.environment,
    EnrollmentRecord.status,
    EnrollmentRecord.currently_due,
    EnrollmentRecord.disabled_reason,
    EnrollmentRecord.version,
    EnrollmentRecord.created_at,
    EnrollmentRecord.updated_at,
)
type _Row = Row[UUID, UUID, UUID, str, str, str, str, list[str], str | None, int, datetime, datetime]


class ForeignTenantError(LedgerError):
    """A repository of one tenant's unit was handed another tenant's enrollment to keep."""

    code: ClassVar[str] = "fiscal_foreign_tenant"

    def __init__(self) -> None:
        super().__init__("a unit keeps the enrollments of its own company and mode only")


def _enrollment(row: _Row) -> Enrollment:
    (
        identifier,
        organization,
        company,
        mode,
        channel,
        environment,
        status,
        due,
        reason,
        version,
        created_at,
        updated_at,
    ) = row
    return Enrollment(
        id=EnrollmentId(identifier),
        organization=OrganizationId(organization),
        company=CompanyId(company),
        mode=Mode(mode),
        channel=channel,
        environment=environment,
        status=EnrollmentStatus(status),
        currently_due=_DUE.validate_python(due),
        disabled_reason=reason,
        version=version,
        created_at=created_at,
        updated_at=updated_at,
    )


@final
class PgEnrollments(Enrollments):
    """The enrollments of one company in one mode, in PostgreSQL."""

    __slots__ = ("_connection", "_mode", "_tenant")

    def __init__(self, connection: AsyncConnection, tenant: Tenant, mode: Mode) -> None:
        """``tenant`` is the unit's company, in ``mode`` (its own: a unit without one keeps no enrollment)."""
        self._connection = connection
        self._tenant = Tenant(tenant.organization, tenant.company, mode)
        self._mode = mode

    def _mine(self) -> tuple[ColumnElement[bool], ...]:
        return (
            EnrollmentRecord.organization_id == self._tenant.organization,
            EnrollmentRecord.company_id == self._tenant.company,
            EnrollmentRecord.mode == self._mode.value,
        )

    def _selected(
        self,
    ) -> Select[UUID, UUID, UUID, str, str, str, str, list[str], str | None, int, datetime, datetime]:
        return _ENROLLMENT.where(*self._mine())

    def _require_own(self, enrollment: Enrollment) -> None:
        if enrollment.tenant != self._tenant:
            raise ForeignTenantError

    async def _one(
        self,
        statement: Select[
            UUID, UUID, UUID, str, str, str, str, list[str], str | None, int, datetime, datetime
        ],
    ) -> Enrollment | None:
        row = (await self._connection.execute(statement)).first()
        return None if row is None else _enrollment(row)

    @override
    async def add(self, enrollment: Enrollment) -> EnrollmentId | None:
        self._require_own(enrollment)
        added = (
            insert(EnrollmentRecord)
            .values(
                organization_id=enrollment.organization,
                company_id=enrollment.company,
                mode=enrollment.mode.value,
                id=enrollment.id,
                channel=enrollment.channel,
                environment=enrollment.environment,
                status=enrollment.status.value,
                currently_due=list(enrollment.currently_due),
                disabled_reason=enrollment.disabled_reason,
                version=enrollment.version,
                created_at=enrollment.created_at,
                updated_at=enrollment.updated_at,
            )
            .on_conflict_do_nothing(
                index_elements=("company_id", "mode", "channel"), index_where=text("status <> 'disabled'")
            )
            .returning(EnrollmentRecord.id)
        )
        if (await self._connection.execute(added)).first() is not None:
            return None
        holder = select(EnrollmentRecord.id).where(
            *self._mine(),
            EnrollmentRecord.channel == enrollment.channel,
            EnrollmentRecord.status != EnrollmentStatus.DISABLED.value,
        )
        return EnrollmentId((await self._connection.execute(holder)).scalar_one())

    @override
    async def get(self, enrollment: EnrollmentId) -> Enrollment | None:
        return await self._one(self._selected().where(EnrollmentRecord.id == enrollment))

    @override
    async def locked(self, enrollment: EnrollmentId) -> Enrollment | None:
        return await self._one(self._selected().where(EnrollmentRecord.id == enrollment).with_for_update())

    @override
    async def update(self, enrollment: Enrollment, *, version: int) -> None:
        self._require_own(enrollment)
        await self._connection.execute(
            update(EnrollmentRecord)
            .where(*self._mine(), EnrollmentRecord.id == enrollment.id, EnrollmentRecord.version == version)
            .values(
                status=enrollment.status.value,
                currently_due=list(enrollment.currently_due),
                disabled_reason=enrollment.disabled_reason,
                version=enrollment.version,
                updated_at=enrollment.updated_at,
            )
        )

    @override
    async def on(self, channel: str, environment: str) -> Enrollment | None:
        return await self._one(
            self._selected().where(
                EnrollmentRecord.channel == channel,
                EnrollmentRecord.environment == environment,
                EnrollmentRecord.status != EnrollmentStatus.DISABLED.value,
            )
        )

    @override
    async def every(self) -> tuple[Enrollment, ...]:
        rows = (await self._connection.execute(self._selected().order_by(EnrollmentRecord.id))).all()
        return tuple(_enrollment(row) for row in rows)

    @override
    async def page(self, request: PageRequest) -> Page[Enrollment]:
        rows = (await self._connection.execute(paged(self._selected(), EnrollmentRecord.id, request))).all()
        return page_of([_enrollment(row) for row in rows], request, key=lambda found: found.id)
