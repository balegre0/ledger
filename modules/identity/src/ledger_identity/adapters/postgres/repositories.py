"""identity's repositories: the ports of ``application.ports`` over one transaction (SQLAlchemy Core).

Every repository is bound to the organization of its unit and names it in every read, update and upsert; the
repositories of a company's rows (branches, emission points) are bound to the unit's company as well, and name
it too. Row-level security enforces the unit's scope again. Tenancy is checked
twice (§2): a statement that forgot its filter still sees nothing of another tenant, and a policy dropped by
mistake still leaves every read and change inside the organization. An insert that could collide uses ``ON
CONFLICT DO NOTHING`` against the constraint or unique index that would refuse it, and reads the outcome from
``RETURNING``: a taken tax id or code is an answer, not an exception to parse. The index decides even where
row-level security hides the row that holds the value (an active company of another organization). A row of
another organization handed to a repository to keep is refused before any statement (``ForeignTenantError``):
a defect of the caller, which row-level security would refuse as well.
"""

from typing import ClassVar, final, override
lazy from datetime import datetime
lazy from uuid import UUID

from sqlalchemy import and_, false, func, select, update
from sqlalchemy.dialects.postgresql import insert
lazy from sqlalchemy import ColumnElement, Row, Select
lazy from sqlalchemy.ext.asyncio import AsyncConnection
lazy from sqlalchemy.orm import InstrumentedAttribute

from ledger_identity.adapters.postgres.tables import (
    BranchRecord,
    CompanyRecord,
    EmissionPointRecord,
    MembershipRecord,
    OrganizationRecord,
)
from ledger_identity.application.ports import Branches, Companies, EmissionPoints, Memberships, Organizations
from ledger_identity.domain.branches import Branch, EmissionPoint
from ledger_identity.domain.company import Company, CompanyStatus
from ledger_identity.domain.ids import BranchId, EmissionPointId, MembershipId
from ledger_identity.domain.membership import Membership, MembershipStatus
from ledger_identity.domain.organization import Organization
from ledger_identity.domain.roles import Role
from ledger_identity.domain.text import Code, LegalName, Name
from ledger_kernel.access import SubjectId
from ledger_kernel.countries.do import TaxId
from ledger_kernel.errors import LedgerError
from ledger_kernel.ids import CompanyId, OrganizationId
from ledger_kernel.pagination import page_of
from ledger_kernel.tenancy import Tenant
lazy from ledger_kernel.pagination import Page, PageRequest


class ForeignTenantError(LedgerError):
    """A repository of one organization's unit was handed a row of another organization to keep."""

    code: ClassVar[str] = "foreign_tenant"

    def __init__(self) -> None:
        super().__init__("a unit keeps the rows of its own organization only")


def _require_own(organization: OrganizationId, unit: OrganizationId) -> None:
    """Refuse a row of ``organization`` in a unit of the organization ``unit``."""
    if organization != unit:
        raise ForeignTenantError


def _require_own_company(tenant: Tenant, organization: OrganizationId, company: CompanyId | None) -> None:
    """Refuse a company's row in a unit of another organization or company, or of no company at all."""
    if company is None or tenant != Tenant(organization, company, None):
        raise ForeignTenantError


def _in_the_unit_s_company(
    column: InstrumentedAttribute[UUID], company: CompanyId | None
) -> ColumnElement[bool]:
    """``column`` is the unit's company; a unit scoped to a whole organization sees no company's row.

    Row-level security's own rule for the rows a company owns: the application states it again.
    """
    return false() if company is None else column == company


_ORGANIZATION = select(OrganizationRecord.id, OrganizationRecord.provider_id, OrganizationRecord.created_at)
_COMPANY = select(
    CompanyRecord.id,
    CompanyRecord.organization_id,
    CompanyRecord.tax_id,
    CompanyRecord.legal_name,
    CompanyRecord.status,
    CompanyRecord.version,
    CompanyRecord.created_at,
    CompanyRecord.updated_at,
)
_BRANCH = select(
    BranchRecord.id,
    BranchRecord.organization_id,
    BranchRecord.company_id,
    BranchRecord.code,
    BranchRecord.name,
    BranchRecord.created_at,
)
_POINT = select(
    EmissionPointRecord.id,
    EmissionPointRecord.organization_id,
    EmissionPointRecord.company_id,
    EmissionPointRecord.branch_id,
    EmissionPointRecord.code,
    EmissionPointRecord.name,
    EmissionPointRecord.created_at,
)
_MEMBERSHIP = select(
    MembershipRecord.id,
    MembershipRecord.organization_id,
    MembershipRecord.company_id,
    MembershipRecord.subject,
    MembershipRecord.role,
    MembershipRecord.status,
    MembershipRecord.version,
    MembershipRecord.changed_at,
    MembershipRecord.changed_by,
)
_ACTIVE = MembershipRecord.status == MembershipStatus.ACTIVE.value


def _paged[*Columns](
    statement: Select[*Columns], key: InstrumentedAttribute[UUID], request: PageRequest
) -> Select[*Columns]:
    """``statement`` in ``key`` order, after the request's cursor, one row more than the page holds."""
    after = statement if request.after is None else statement.where(key > request.after)
    return after.order_by(key).limit(request.size + 1)


def _organization(row: Row[UUID, str, datetime]) -> Organization:
    identifier, provider_id, created_at = row
    return Organization(id=OrganizationId(identifier), provider_id=provider_id, created_at=created_at)


def _company(row: Row[UUID, UUID, str, str, str, int, datetime, datetime]) -> Company:
    identifier, organization, tax_id, legal_name, status, version, created_at, updated_at = row
    return Company(
        id=CompanyId(identifier),
        organization=OrganizationId(organization),
        tax_id=TaxId.parse(tax_id),
        legal_name=LegalName(legal_name),
        status=CompanyStatus(status),
        version=version,
        created_at=created_at,
        updated_at=updated_at,
    )


def _branch(row: Row[UUID, UUID, UUID, str, str, datetime]) -> Branch:
    identifier, organization, company, code, name, created_at = row
    tenant = Tenant(OrganizationId(organization), CompanyId(company), None)
    return Branch(
        id=BranchId(identifier), tenant=tenant, code=Code(code), name=Name(name), created_at=created_at
    )


def _point(row: Row[UUID, UUID, UUID, UUID, str, str, datetime]) -> EmissionPoint:
    identifier, organization, company, branch, code, name, created_at = row
    return EmissionPoint(
        id=EmissionPointId(identifier),
        tenant=Tenant(OrganizationId(organization), CompanyId(company), None),
        branch=BranchId(branch),
        code=Code(code),
        name=Name(name),
        created_at=created_at,
    )


def _membership(row: Row[UUID, UUID, UUID, str, str, str, int, datetime, str]) -> Membership:
    identifier, organization, company, subject, role, status, version, changed_at, changed_by = row
    return Membership(
        id=MembershipId(identifier),
        tenant=Tenant(OrganizationId(organization), CompanyId(company), None),
        subject=SubjectId(subject),
        role=Role(role),
        status=MembershipStatus(status),
        version=version,
        changed_at=changed_at,
        changed_by=SubjectId(changed_by),
    )


@final
class PgOrganizations(Organizations):
    """The organizations identity has seen, in PostgreSQL: the unit's own."""

    __slots__ = ("_connection", "_organization")

    def __init__(self, connection: AsyncConnection, organization: OrganizationId) -> None:
        self._connection = connection
        self._organization = organization

    @override
    async def add(self, organization: Organization) -> None:
        _require_own(organization.id, self._organization)
        await self._connection.execute(
            insert(OrganizationRecord)
            .values(
                id=organization.id, provider_id=organization.provider_id, created_at=organization.created_at
            )
            .on_conflict_do_nothing(index_elements=[OrganizationRecord.id])
        )

    @override
    async def get(self, organization: OrganizationId) -> Organization | None:
        mine = _ORGANIZATION.where(
            OrganizationRecord.id == organization, OrganizationRecord.id == self._organization
        )
        row = (await self._connection.execute(mine)).first()
        return None if row is None else _organization(row)


@final
class PgCompanies(Companies):
    """The companies of the unit's organization, in PostgreSQL."""

    __slots__ = ("_connection", "_organization")

    def __init__(self, connection: AsyncConnection, organization: OrganizationId) -> None:
        self._connection = connection
        self._organization = organization

    @override
    async def add(self, company: Company) -> bool:
        _require_own(company.organization, self._organization)
        added = await self._connection.execute(
            insert(CompanyRecord)
            .values(
                id=company.id,
                organization_id=company.organization,
                tax_id=str(company.tax_id),
                legal_name=company.legal_name,
                status=company.status.value,
                version=company.version,
                created_at=company.created_at,
                updated_at=company.updated_at,
            )
            # The index, not a lookup: another organization's company is a row this unit cannot see.
            .on_conflict_do_nothing(
                index_elements=[CompanyRecord.tax_id],
                index_where=CompanyRecord.status == CompanyStatus.ACTIVE.value,
            )
            .returning(CompanyRecord.id)
        )
        return added.first() is not None

    @override
    async def get(self, company: CompanyId) -> Company | None:
        row = (await self._connection.execute(self._one(company))).first()
        return None if row is None else _company(row)

    @override
    async def locked(self, company: CompanyId) -> Company | None:
        row = (await self._connection.execute(self._one(company).with_for_update())).first()
        return None if row is None else _company(row)

    @override
    async def update(self, company: Company, *, version: int) -> bool:
        _require_own(company.organization, self._organization)
        changed = await self._connection.execute(
            update(CompanyRecord)
            .where(
                CompanyRecord.organization_id == self._organization,
                CompanyRecord.id == company.id,
                CompanyRecord.version == version,
            )
            .values(
                legal_name=company.legal_name,
                status=company.status.value,
                version=company.version,
                updated_at=company.updated_at,
            )
        )
        return changed.rowcount == 1

    @override
    async def of_member(self, subject: SubjectId, request: PageRequest) -> Page[Company]:
        membership = and_(
            MembershipRecord.organization_id == CompanyRecord.organization_id,
            MembershipRecord.company_id == CompanyRecord.id,
        )
        statement = _COMPANY.join(MembershipRecord, membership).where(
            CompanyRecord.organization_id == self._organization, MembershipRecord.subject == subject, _ACTIVE
        )
        rows = (await self._connection.execute(_paged(statement, CompanyRecord.id, request))).all()
        return page_of([_company(row) for row in rows], request, key=lambda company: company.id)

    def _one(self, company: CompanyId) -> Select[UUID, UUID, str, str, str, int, datetime, datetime]:
        return _COMPANY.where(
            CompanyRecord.organization_id == self._organization, CompanyRecord.id == company
        )


@final
class PgBranches(Branches):
    """The branches of the unit's company, in PostgreSQL: none in a unit of a whole organization."""

    __slots__ = ("_company", "_connection", "_organization")

    def __init__(
        self, connection: AsyncConnection, organization: OrganizationId, company: CompanyId | None
    ) -> None:
        self._connection = connection
        self._organization = organization
        self._company = company

    @override
    async def add(self, branch: Branch) -> bool:
        _require_own_company(branch.tenant, self._organization, self._company)
        added = await self._connection.execute(
            insert(BranchRecord)
            .values(
                id=branch.id,
                organization_id=branch.tenant.organization,
                company_id=branch.tenant.company,
                code=branch.code,
                name=branch.name,
                created_at=branch.created_at,
            )
            .on_conflict_do_nothing(constraint="uq_branches_code")
            .returning(BranchRecord.id)
        )
        return added.first() is not None

    @override
    async def get(self, company: CompanyId, branch: BranchId) -> Branch | None:
        row = (await self._connection.execute(self._of(company).where(BranchRecord.id == branch))).first()
        return None if row is None else _branch(row)

    @override
    async def page(self, company: CompanyId, request: PageRequest) -> Page[Branch]:
        rows = (await self._connection.execute(_paged(self._of(company), BranchRecord.id, request))).all()
        return page_of([_branch(row) for row in rows], request, key=lambda branch: branch.id)

    def _of(self, company: CompanyId) -> Select[UUID, UUID, UUID, str, str, datetime]:
        return _BRANCH.where(
            BranchRecord.organization_id == self._organization,
            _in_the_unit_s_company(BranchRecord.company_id, self._company),
            BranchRecord.company_id == company,
        )


@final
class PgEmissionPoints(EmissionPoints):
    """The emission points of the unit's company, in PostgreSQL: none in a unit of a whole organization."""

    __slots__ = ("_company", "_connection", "_organization")

    def __init__(
        self, connection: AsyncConnection, organization: OrganizationId, company: CompanyId | None
    ) -> None:
        self._connection = connection
        self._organization = organization
        self._company = company

    @override
    async def add(self, point: EmissionPoint) -> bool:
        _require_own_company(point.tenant, self._organization, self._company)
        added = await self._connection.execute(
            insert(EmissionPointRecord)
            .values(
                id=point.id,
                organization_id=point.tenant.organization,
                company_id=point.tenant.company,
                branch_id=point.branch,
                code=point.code,
                name=point.name,
                created_at=point.created_at,
            )
            .on_conflict_do_nothing(constraint="uq_emission_points_code")
            .returning(EmissionPointRecord.id)
        )
        return added.first() is not None

    @override
    async def get(self, company: CompanyId, point: EmissionPointId) -> EmissionPoint | None:
        statement = self._of(company).where(EmissionPointRecord.id == point)
        row = (await self._connection.execute(statement)).first()
        return None if row is None else _point(row)

    @override
    async def page(self, company: CompanyId, branch: BranchId, request: PageRequest) -> Page[EmissionPoint]:
        mine = self._of(company).where(EmissionPointRecord.branch_id == branch)
        rows = (await self._connection.execute(_paged(mine, EmissionPointRecord.id, request))).all()
        return page_of([_point(row) for row in rows], request, key=lambda point: point.id)

    def _of(self, company: CompanyId) -> Select[UUID, UUID, UUID, UUID, str, str, datetime]:
        return _POINT.where(
            EmissionPointRecord.organization_id == self._organization,
            _in_the_unit_s_company(EmissionPointRecord.company_id, self._company),
            EmissionPointRecord.company_id == company,
        )


@final
class PgMemberships(Memberships):
    """Who holds, or held, which role in the companies of the unit's organization, in PostgreSQL."""

    __slots__ = ("_connection", "_organization")

    def __init__(self, connection: AsyncConnection, organization: OrganizationId) -> None:
        self._connection = connection
        self._organization = organization

    @override
    async def get(self, company: CompanyId, subject: SubjectId) -> Membership | None:
        statement = self._of(company).where(MembershipRecord.subject == subject)
        row = (await self._connection.execute(statement)).first()
        return None if row is None else _membership(row)

    @override
    async def put(self, membership: Membership) -> None:
        _require_own(membership.tenant.organization, self._organization)
        values = {
            "role": membership.role.value,
            "status": membership.status.value,
            "version": membership.version,
            "changed_at": membership.changed_at,
            "changed_by": membership.changed_by,
        }
        await self._connection.execute(
            insert(MembershipRecord)
            .values(
                id=membership.id,
                organization_id=membership.tenant.organization,
                company_id=membership.tenant.company,
                subject=membership.subject,
                **values,
            )
            .on_conflict_do_update(
                constraint="uq_memberships_subject",
                set_=values,
                where=MembershipRecord.organization_id == self._organization,
            )
        )

    @override
    async def owners(self, company: CompanyId) -> int:
        statement = (
            select(func.count())
            .select_from(MembershipRecord)
            .where(
                MembershipRecord.organization_id == self._organization,
                MembershipRecord.company_id == company,
                MembershipRecord.role == Role.OWNER.value,
                _ACTIVE,
            )
        )
        return (await self._connection.execute(statement)).scalar_one()

    @override
    async def page(self, company: CompanyId, request: PageRequest) -> Page[Membership]:
        statement = _paged(self._of(company).where(_ACTIVE), MembershipRecord.id, request)
        rows = (await self._connection.execute(statement)).all()
        return page_of([_membership(row) for row in rows], request, key=lambda membership: membership.id)

    def _of(self, company: CompanyId) -> Select[UUID, UUID, UUID, str, str, str, int, datetime, str]:
        return _MEMBERSHIP.where(
            MembershipRecord.organization_id == self._organization, MembershipRecord.company_id == company
        )
