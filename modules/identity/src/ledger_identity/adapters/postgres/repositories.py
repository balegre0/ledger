"""identity's repositories: the ports of ``application.ports`` over one transaction (SQLAlchemy Core).

Every repository is bound to the organization of its unit and names it in every read, update and upsert.
Row-level security enforces the unit's scope again. Tenancy is checked
twice (§2): a statement that forgot its filter still sees nothing of another tenant, and a policy dropped by
mistake still leaves every read and change inside the organization. An insert that could collide uses ``ON
CONFLICT DO NOTHING`` against the constraint or unique index that would refuse it, and reads the outcome from
``RETURNING``: a taken tax id or code is an answer, not an exception to parse. The index decides even where
row-level security hides the row that holds the value (the registry holds every organization's). A row of
another organization handed to a repository to keep is refused before any statement (``ForeignTenantError``):
a defect of the caller, which row-level security would refuse as well. A list is read in the direction of
its page (``ledger_platform.data.paging``): backwards for a page that ends before a cursor.
"""

from typing import ClassVar, final, override
lazy from collections.abc import Sequence
lazy from datetime import date, datetime
lazy from uuid import UUID

from sqlalchemy import and_, case, delete, false, func, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert
lazy from sqlalchemy import ColumnElement, Row, Select
lazy from sqlalchemy.ext.asyncio import AsyncConnection

from ledger_identity.adapters.postgres.tables import (
    ApiClientRecord,
    CompanyRecord,
    CompanyTaxIdRecord,
    EstablishmentRecord,
    MembershipRecord,
    OrganizationRecord,
    TaxRegistrationRecord,
)
from ledger_identity.application.ports import (
    ApiClients,
    Companies,
    Establishments,
    Holder,
    Memberships,
    Organizations,
    TaxRegistry,
)
from ledger_identity.domain.api_clients import ApiClient, ApiClientStatus
from ledger_identity.domain.company import Company, CompanyStatus, FiscalProfile
from ledger_identity.domain.establishments import Establishment
from ledger_identity.domain.ids import ApiClientId, EstablishmentId, MembershipId
from ledger_identity.domain.membership import Membership, MembershipStatus
from ledger_identity.domain.organization import Organization
from ledger_identity.domain.text import Code, LegalName, Name, TimeZone
from ledger_kernel.access import Role, SubjectId
from ledger_kernel.addresses import FiscalAddress
from ledger_kernel.errors import LedgerError
from ledger_kernel.ids import CompanyId, OrganizationId
from ledger_kernel.jurisdiction import CountryCode, Jurisdiction
from ledger_kernel.money import Currency
from ledger_kernel.pagination import page_of
from ledger_kernel.tax_ids import TaxId, TaxIdScheme
from ledger_kernel.tenancy import Mode, Tenant
from ledger_platform.data import paged
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


def _in_the_unit_s_mode(mode: Mode | None) -> ColumnElement[bool]:
    """A moded row is the unit's mode's; a unit with no mode sees none (row-level security's own rule)."""
    return false() if mode is None else ApiClientRecord.mode == mode.value


_API_CLIENT = select(
    ApiClientRecord.id,
    ApiClientRecord.organization_id,
    ApiClientRecord.mode,
    ApiClientRecord.subject,
    ApiClientRecord.name,
    ApiClientRecord.api_version,
    ApiClientRecord.status,
    ApiClientRecord.created_at,
    ApiClientRecord.created_by,
    ApiClientRecord.revoked_at,
)
_ORGANIZATION = select(
    OrganizationRecord.id,
    OrganizationRecord.provider_id,
    OrganizationRecord.created_at,
    OrganizationRecord.api_version,
)
_COMPANY = select(
    CompanyRecord.id,
    CompanyRecord.organization_id,
    CompanyRecord.country,
    CompanyRecord.subdivision,
    CompanyRecord.currency,
    CompanyRecord.legal_name,
    CompanyRecord.status,
    CompanyRecord.fiscal_profile_locked_at,
    CompanyRecord.version,
    CompanyRecord.created_at,
    CompanyRecord.updated_at,
    CompanyRecord.address_line,
    CompanyRecord.address_municipality,
    CompanyRecord.address_province,
)
type CompanyColumns = tuple[
    UUID,
    UUID,
    str,
    str | None,
    str,
    str,
    str,
    datetime | None,
    int,
    datetime,
    datetime,
    str | None,
    str | None,
    str | None,
]
"""What ``_COMPANY`` selects, in its order: the company's columns, its address's last."""
type CompanyRow = Row[*CompanyColumns]
_ESTABLISHMENT = select(
    EstablishmentRecord.id,
    EstablishmentRecord.organization_id,
    EstablishmentRecord.company_id,
    EstablishmentRecord.code,
    EstablishmentRecord.name,
    EstablishmentRecord.time_zone,
    EstablishmentRecord.created_at,
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


def _organization(row: Row[UUID, str, datetime, date]) -> Organization:
    identifier, provider_id, created_at, api_version = row
    return Organization(
        id=OrganizationId(identifier), provider_id=provider_id, created_at=created_at, api_version=api_version
    )


def _company(row: CompanyRow, tax_ids: tuple[TaxId, ...]) -> Company:
    (
        identifier,
        organization,
        country,
        subdivision,
        currency,
        legal_name,
        status,
        locked_at,
        version,
        created_at,
        updated_at,
        line,
        municipality,
        province,
    ) = row
    return Company(
        id=CompanyId(identifier),
        organization=OrganizationId(organization),
        profile=FiscalProfile(
            jurisdiction=Jurisdiction(CountryCode(country), subdivision),
            functional_currency=Currency.of(currency),
            tax_ids=tax_ids,
        ),
        legal_name=LegalName(legal_name),
        address=None
        if line is None
        else FiscalAddress(line=line, municipality=municipality, province=province),
        status=CompanyStatus(status),
        fiscal_profile_locked_at=locked_at,
        version=version,
        created_at=created_at,
        updated_at=updated_at,
    )


def _address_columns(address: FiscalAddress | None) -> dict[str, str | None]:
    """The ``address_*`` columns of ``address``: all empty for none."""
    if address is None:
        return {"address_line": None, "address_municipality": None, "address_province": None}
    return {
        "address_line": address.line,
        "address_municipality": address.municipality,
        "address_province": address.province,
    }


def _api_client(row: Row[UUID, UUID, str, str, str, date, str, datetime, str, datetime | None]) -> ApiClient:
    identifier, organization, mode, subject, name, api_version, status, created_at, created_by, revoked_at = (
        row
    )
    return ApiClient(
        id=ApiClientId(identifier),
        organization=OrganizationId(organization),
        mode=Mode(mode),
        subject=SubjectId(subject),
        name=Name(name),
        api_version=api_version,
        status=ApiClientStatus(status),
        created_at=created_at,
        created_by=SubjectId(created_by),
        revoked_at=revoked_at,
    )


def _establishment(row: Row[UUID, UUID, UUID, str, str, str, datetime]) -> Establishment:
    identifier, organization, company, code, name, time_zone, created_at = row
    return Establishment(
        id=EstablishmentId(identifier),
        tenant=Tenant(OrganizationId(organization), CompanyId(company), None),
        code=Code(code),
        name=Name(name),
        time_zone=TimeZone(time_zone),
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
                id=organization.id,
                provider_id=organization.provider_id,
                created_at=organization.created_at,
                api_version=organization.api_version,
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
        profile = company.profile
        attempt = (
            await self._connection.begin_nested()
        )  # a taken tax id leaves no company and no registration
        await self._connection.execute(
            insert(CompanyRecord).values(
                id=company.id,
                organization_id=company.organization,
                country=profile.jurisdiction.country,
                subdivision=profile.jurisdiction.subdivision,
                currency=profile.functional_currency.code,
                legal_name=company.legal_name,
                **_address_columns(company.address),
                status=company.status.value,
                fiscal_profile_locked_at=company.fiscal_profile_locked_at,
                version=company.version,
                created_at=company.created_at,
                updated_at=company.updated_at,
            )
        )
        await self._connection.execute(
            insert(CompanyTaxIdRecord).values(
                [
                    {
                        "organization_id": company.organization,
                        "company_id": company.id,
                        "position": position,
                        "scheme": tax_id.scheme,
                        "value": tax_id.value,
                    }
                    for position, tax_id in enumerate(profile.tax_ids)
                ]
            )
        )
        # The registry's key, not a lookup: another organization's company is a row this unit cannot see.
        registered = await self._connection.execute(
            insert(TaxRegistrationRecord)
            .values(
                [
                    {
                        "scheme": tax_id.scheme,
                        "value": tax_id.value,
                        "organization_id": company.organization,
                        "company_id": company.id,
                    }
                    for tax_id in profile.tax_ids
                ]
            )
            .on_conflict_do_nothing(
                index_elements=[TaxRegistrationRecord.scheme, TaxRegistrationRecord.value]
            )
            .returning(TaxRegistrationRecord.scheme)
        )
        if len(registered.all()) != len(profile.tax_ids):  # any one tax id held refuses the whole company
            await attempt.rollback()
            return False
        await attempt.commit()
        return True

    @override
    async def get(self, company: CompanyId) -> Company | None:
        rows = (await self._connection.execute(self._one(company))).all()
        return (await self._companies(rows))[0] if rows else None

    @override
    async def locked(self, company: CompanyId) -> Company | None:
        rows = (await self._connection.execute(self._one(company).with_for_update())).all()
        return (await self._companies(rows))[0] if rows else None

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
                country=company.profile.jurisdiction.country,
                subdivision=company.profile.jurisdiction.subdivision,
                currency=company.profile.functional_currency.code,
                legal_name=company.legal_name,
                **_address_columns(company.address),
                status=company.status.value,
                fiscal_profile_locked_at=company.fiscal_profile_locked_at,
                version=company.version,
                updated_at=company.updated_at,
            )
        )
        if changed.rowcount != 1:
            return False
        if company.status is CompanyStatus.INACTIVE:  # its tax ids are free for any organization to register
            await self._connection.execute(
                delete(TaxRegistrationRecord).where(
                    TaxRegistrationRecord.organization_id == self._organization,
                    TaxRegistrationRecord.company_id == company.id,
                )
            )
        return True

    @override
    async def retax(self, company: Company) -> bool:
        _require_own(company.organization, self._organization)
        listed = company.profile.tax_ids
        mine = and_(
            TaxRegistrationRecord.organization_id == self._organization,
            TaxRegistrationRecord.company_id == company.id,
        )
        await self._connection.execute(
            delete(CompanyTaxIdRecord).where(
                CompanyTaxIdRecord.organization_id == self._organization,
                CompanyTaxIdRecord.company_id == company.id,
            )
        )
        await self._connection.execute(
            insert(CompanyTaxIdRecord).values(
                [
                    {
                        "organization_id": company.organization,
                        "company_id": company.id,
                        "position": position,
                        "scheme": tax_id.scheme,
                        "value": tax_id.value,
                    }
                    for position, tax_id in enumerate(listed)
                ]
            )
        )
        keys = [(tax_id.scheme, tax_id.value) for tax_id in listed]
        await self._connection.execute(
            delete(TaxRegistrationRecord).where(
                mine, tuple_(TaxRegistrationRecord.scheme, TaxRegistrationRecord.value).not_in(keys)
            )
        )
        await self._connection.execute(
            insert(TaxRegistrationRecord)
            .values(
                [
                    {
                        "scheme": tax_id.scheme,
                        "value": tax_id.value,
                        "organization_id": company.organization,
                        "company_id": company.id,
                    }
                    for tax_id in listed
                ]
            )
            .on_conflict_do_nothing(
                index_elements=[TaxRegistrationRecord.scheme, TaxRegistrationRecord.value]
            )
        )
        held = await self._connection.execute(
            select(func.count()).select_from(TaxRegistrationRecord).where(mine)
        )
        return held.scalar_one() == len(listed)  # fewer: another company holds one of them

    @override
    async def of_member(self, subject: SubjectId, request: PageRequest) -> Page[Company]:
        membership = and_(
            MembershipRecord.organization_id == CompanyRecord.organization_id,
            MembershipRecord.company_id == CompanyRecord.id,
        )
        statement = _COMPANY.join(MembershipRecord, membership).where(
            CompanyRecord.organization_id == self._organization, MembershipRecord.subject == subject, _ACTIVE
        )
        rows = (await self._connection.execute(paged(statement, CompanyRecord.id, request))).all()
        return page_of(await self._companies(rows), request, key=lambda company: company.id)

    async def _companies(self, rows: Sequence[CompanyRow]) -> list[Company]:
        """The companies of ``rows``, each with its tax ids in their order (one read for all of them)."""
        found = await self._connection.execute(
            select(CompanyTaxIdRecord.company_id, CompanyTaxIdRecord.scheme, CompanyTaxIdRecord.value)
            .where(
                CompanyTaxIdRecord.organization_id == self._organization,
                CompanyTaxIdRecord.company_id.in_([row[0] for row in rows]),
            )
            .order_by(CompanyTaxIdRecord.company_id, CompanyTaxIdRecord.position)
        )
        tax_ids: dict[UUID, list[TaxId]] = {}
        for company, scheme, value in found.all():
            tax_ids.setdefault(company, []).append(TaxId(TaxIdScheme(scheme), value))
        return [_company(row, tuple(tax_ids[row[0]])) for row in rows]

    def _one(self, company: CompanyId) -> Select[*CompanyColumns]:
        return _COMPANY.where(
            CompanyRecord.organization_id == self._organization, CompanyRecord.id == company
        )


@final
class PgTaxRegistry(TaxRegistry):
    """The tax ids active companies hold, in PostgreSQL: no tenant's, so no organization is named."""

    __slots__ = ("_connection",)

    def __init__(self, connection: AsyncConnection) -> None:
        self._connection = connection

    @override
    async def holder_of(self, tax_id: TaxId) -> Holder | None:
        row = (
            await self._connection.execute(
                select(TaxRegistrationRecord.organization_id, TaxRegistrationRecord.company_id).where(
                    TaxRegistrationRecord.scheme == tax_id.scheme, TaxRegistrationRecord.value == tax_id.value
                )
            )
        ).first()
        return None if row is None else Holder(OrganizationId(row[0]), CompanyId(row[1]))


@final
class PgEstablishments(Establishments):
    """The establishments of the companies of the unit's organization, in PostgreSQL."""

    __slots__ = ("_connection", "_organization")

    def __init__(self, connection: AsyncConnection, organization: OrganizationId) -> None:
        self._connection = connection
        self._organization = organization

    @override
    async def add(self, establishment: Establishment) -> bool:
        _require_own(establishment.tenant.organization, self._organization)
        added = await self._connection.execute(
            insert(EstablishmentRecord)
            .values(
                id=establishment.id,
                organization_id=establishment.tenant.organization,
                company_id=establishment.tenant.company,
                code=establishment.code,
                name=establishment.name,
                time_zone=establishment.time_zone,
                created_at=establishment.created_at,
            )
            .on_conflict_do_nothing(constraint="uq_establishments_code")
            .returning(EstablishmentRecord.id)
        )
        return added.first() is not None

    @override
    async def get(self, company: CompanyId, establishment: EstablishmentId) -> Establishment | None:
        found = self._of(company).where(EstablishmentRecord.id == establishment)
        row = (await self._connection.execute(found)).first()
        return None if row is None else _establishment(row)

    @override
    async def by_code(self, company: CompanyId, code: Code) -> Establishment | None:
        row = (
            await self._connection.execute(self._of(company).where(EstablishmentRecord.code == code))
        ).first()
        return None if row is None else _establishment(row)

    @override
    async def page(self, company: CompanyId, request: PageRequest) -> Page[Establishment]:
        statement = paged(self._of(company), EstablishmentRecord.id, request)
        rows = (await self._connection.execute(statement)).all()
        return page_of([_establishment(row) for row in rows], request, key=lambda found: found.id)

    @override
    async def update(self, establishment: Establishment) -> None:
        _require_own(establishment.tenant.organization, self._organization)
        await self._connection.execute(
            update(EstablishmentRecord)
            .where(
                EstablishmentRecord.organization_id == self._organization,
                EstablishmentRecord.id == establishment.id,
            )
            .values(time_zone=establishment.time_zone)
        )

    def _of(self, company: CompanyId) -> Select[UUID, UUID, UUID, str, str, str, datetime]:
        return _ESTABLISHMENT.where(
            EstablishmentRecord.organization_id == self._organization,
            EstablishmentRecord.company_id == company,
        )


@final
class PgApiClients(ApiClients):
    """The API clients of the unit's organization and mode, in PostgreSQL: none in a unit with no mode."""

    __slots__ = ("_connection", "_mode", "_organization")

    def __init__(self, connection: AsyncConnection, organization: OrganizationId, mode: Mode | None) -> None:
        self._connection = connection
        self._organization = organization
        self._mode = mode

    @override
    async def add(self, client: ApiClient) -> bool:
        _require_own(client.organization, self._organization)
        if client.mode is not self._mode:
            raise ForeignTenantError
        # The index decides, even where row-level security hides the other mode's client of the machine user.
        added = await self._connection.execute(
            insert(ApiClientRecord)
            .values(
                id=client.id,
                organization_id=client.organization,
                mode=client.mode.value,
                subject=client.subject,
                name=client.name,
                api_version=client.api_version,
                status=client.status.value,
                created_at=client.created_at,
                created_by=client.created_by,
                revoked_at=client.revoked_at,
            )
            .on_conflict_do_nothing(
                index_elements=[ApiClientRecord.organization_id, ApiClientRecord.subject],
                index_where=ApiClientRecord.status == ApiClientStatus.ACTIVE.value,
            )
            .returning(ApiClientRecord.id)
        )
        return added.first() is not None

    @override
    async def get(self, client: ApiClientId) -> ApiClient | None:
        row = (await self._connection.execute(self._mine().where(ApiClientRecord.id == client))).first()
        return None if row is None else _api_client(row)

    @override
    async def revoked(self, client: ApiClient) -> bool:
        _require_own(client.organization, self._organization)
        changed = await self._connection.execute(
            update(ApiClientRecord)
            .where(
                ApiClientRecord.organization_id == self._organization,
                ApiClientRecord.id == client.id,
                ApiClientRecord.status == ApiClientStatus.ACTIVE.value,
                _in_the_unit_s_mode(self._mode),
            )
            .values(status=client.status.value, revoked_at=client.revoked_at)
        )
        return changed.rowcount == 1

    @override
    async def page(self, request: PageRequest) -> Page[ApiClient]:
        rows = (await self._connection.execute(paged(self._mine(), ApiClientRecord.id, request))).all()
        return page_of([_api_client(row) for row in rows], request, key=lambda found: found.id)

    def _mine(self) -> Select[UUID, UUID, str, str, str, date, str, datetime, str, datetime | None]:
        return _API_CLIENT.where(
            ApiClientRecord.organization_id == self._organization, _in_the_unit_s_mode(self._mode)
        )


async def client_of_subject(
    connection: AsyncConnection, organization: OrganizationId, subject: SubjectId
) -> ApiClient | None:
    """The API client of ``subject`` in ``organization``, in whichever mode it is bound to.

    The active one; else the newest revoked one, so that a machine user whose client was revoked stays
    refused. In any unit of the organization: API clients are its rows, whatever their mode (revision 0003).
    """
    newest_active_first = (
        case((ApiClientRecord.status == ApiClientStatus.ACTIVE.value, 0), else_=1),
        ApiClientRecord.id.desc(),
    )
    row = (
        await connection.execute(
            _API_CLIENT.where(
                ApiClientRecord.organization_id == organization, ApiClientRecord.subject == subject
            )
            .order_by(*newest_active_first)
            .limit(1)
        )
    ).first()
    return None if row is None else _api_client(row)


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
        statement = paged(self._of(company).where(_ACTIVE), MembershipRecord.id, request)
        rows = (await self._connection.execute(statement)).all()
        return page_of([_membership(row) for row in rows], request, key=lambda membership: membership.id)

    def _of(self, company: CompanyId) -> Select[UUID, UUID, UUID, str, str, str, int, datetime, str]:
        return _MEMBERSHIP.where(
            MembershipRecord.organization_id == self._organization, MembershipRecord.company_id == company
        )
