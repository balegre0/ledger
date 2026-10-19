"""signer's tables as migrated: a company's unit sees its own certificates and sealed material, nothing else.

Without a scope a unit sees nothing at all; one active certificate serves a company in each mode; a custody
row can never point at another company's certificate.
"""

from datetime import UTC, datetime, timedelta
from typing import Final
lazy from uuid import UUID

import pytest
from psycopg.errors import CheckViolation, ForeignKeyViolation, InsufficientPrivilege, UniqueViolation
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.exc import IntegrityError, ProgrammingError

from ledger_kernel.ids import CompanyId, OrganizationId, new_id
from ledger_kernel.tenancy import OrganizationScope, Tenant
from ledger_signer.adapters.postgres.tables import CertificateRecord, CustodyRecord, SignerTable
lazy from ledger_kernel.tenancy import Scope
lazy from ledger_platform.data.bound import BoundDatabase

NOW: Final = datetime(2026, 10, 19, 12, tzinfo=UTC)


def a_company(organization: OrganizationId | None = None) -> Tenant:
    """A company (of a new organization unless ``organization`` is given), in no mode."""
    return Tenant(
        organization=organization or OrganizationId(new_id()), company=CompanyId(new_id()), mode=None
    )


def certificate_of(
    tenant: Tenant, *, test: bool = True, live: bool = False, status: str = "active"
) -> dict[str, object]:
    """A certificate of ``tenant``'s company serving ``test`` and ``live``, valid for a year from ``NOW``."""
    retired = status == "retired"
    return {
        "organization_id": tenant.organization,
        "company_id": tenant.company,
        "id": new_id(),
        "serves_test": test,
        "serves_live": live,
        "backend": "local",
        "status": status,
        "certificate_der": b"0\x82",
        "fingerprint_sha256": bytes(32),
        "serial_number": "1f",
        "subject": "CN=Holder",
        "issuer": "CN=Authority",
        "not_before": NOW,
        "not_after": NOW + timedelta(days=365),
        "installed_at": NOW,
        "retired_at": NOW if retired else None,
        "retired_reason": "retired" if retired else None,
        "version": 1,
    }


def custody_of(tenant: Tenant, certificate: UUID) -> dict[str, object]:
    """The sealed material of ``certificate`` (opaque bytes: nothing is sealed here)."""
    return {
        "organization_id": tenant.organization,
        "company_id": tenant.company,
        "certificate_id": certificate,
        "sealed": b"sealed",
        "wrapped_data_key": b"wrapped",
        "kms_key_id": "local:00000000",
    }


async def installed(
    module_database: BoundDatabase,
    tenant: Tenant,
    *,
    test: bool = True,
    live: bool = False,
    status: str = "active",
) -> UUID:
    """A certificate of ``tenant``'s company and its custody row; its id."""
    row = certificate_of(tenant, test=test, live=live, status=status)
    identifier = row["id"]
    assert isinstance(identifier, UUID)
    async with module_database.unit(tenant) as unit:
        await unit.connection.execute(insert(CertificateRecord).values(row))
        await unit.connection.execute(insert(CustodyRecord).values(custody_of(tenant, identifier)))
    return identifier


async def counts(module_database: BoundDatabase, scope: Scope | None) -> dict[str, int]:
    """How many rows of each of signer's tables ``scope`` sees."""
    async with module_database.unit(scope) as unit:
        return {
            model.name: (await unit.connection.execute(select(func.count()).select_from(model))).scalar_one()
            for model in SignerTable.metadata.tables.values()
        }


def violated(caught: pytest.ExceptionInfo[IntegrityError]) -> tuple[str, str | None]:
    """The kind of refusal and the constraint PostgreSQL named."""
    refusal = caught.value.orig
    assert isinstance(refusal, UniqueViolation | CheckViolation | ForeignKeyViolation)
    return type(refusal).__name__, refusal.diag.constraint_name


async def test_a_company_s_unit_sees_its_own_rows_and_nothing_without_a_scope(
    module_database: BoundDatabase,
) -> None:
    first = a_company()
    sibling = a_company(first.organization)  # another company of the same organization
    await installed(module_database, first)
    await installed(module_database, sibling, test=False, live=True)
    mine = {"certificates": 1, "custody": 1}
    assert await counts(module_database, first) == mine
    assert await counts(module_database, sibling) == mine
    assert await counts(module_database, a_company()) == {"certificates": 0, "custody": 0}
    assert await counts(module_database, OrganizationScope(first.organization, None)) == {
        "certificates": 0,
        "custody": 0,
    }  # an organization's unit names no company: a company's rows are its company's alone
    assert await counts(module_database, None) == {"certificates": 0, "custody": 0}


@pytest.mark.parametrize("scope", [None, "another company"])
async def test_a_unit_cannot_write_a_row_of_a_company_it_is_not(
    module_database: BoundDatabase, scope: str | None
) -> None:
    victim = a_company()
    with pytest.raises(ProgrammingError) as caught:
        async with module_database.unit(None if scope is None else a_company()) as unit:
            await unit.connection.execute(insert(CertificateRecord).values(certificate_of(victim)))
    assert isinstance(caught.value.orig, InsufficientPrivilege)  # the policy refuses the row


@pytest.mark.parametrize(
    ("first", "second", "index"),
    [
        ((True, False), (True, False), "certificates_one_active_test"),
        ((False, True), (False, True), "certificates_one_active_live"),
        ((True, False), (True, True), "certificates_one_active_test"),
        ((False, True), (True, True), "certificates_one_active_live"),
    ],
)
async def test_a_second_active_certificate_for_a_mode_is_refused(
    module_database: BoundDatabase, first: tuple[bool, bool], second: tuple[bool, bool], index: str
) -> None:
    """``first`` and ``second`` say which modes each serves: (test, live)."""
    tenant = a_company()
    await installed(module_database, tenant, test=first[0], live=first[1])
    row = certificate_of(tenant, test=second[0], live=second[1])
    with pytest.raises(IntegrityError) as caught:
        async with module_database.unit(tenant) as unit:
            await unit.connection.execute(insert(CertificateRecord).values(row))
    assert violated(caught) == ("UniqueViolation", index)


async def test_certificates_of_other_modes_or_retired_ones_stand_side_by_side(
    module_database: BoundDatabase,
) -> None:
    tenant = a_company()
    await installed(module_database, tenant, test=True)
    await installed(module_database, tenant, test=False, live=True)
    await installed(module_database, tenant, test=True, live=True, status="retired")
    await installed(
        module_database, tenant, test=False, live=False, status="retired"
    )  # it served none at last
    await installed(module_database, a_company(), test=True, live=True)  # another company's
    assert await counts(module_database, tenant) == {"certificates": 4, "custody": 4}


async def test_an_active_certificate_serves_a_mode(module_database: BoundDatabase) -> None:
    tenant = a_company()
    with pytest.raises(IntegrityError) as caught:
        async with module_database.unit(tenant) as unit:
            await unit.connection.execute(
                insert(CertificateRecord).values(certificate_of(tenant, test=False, live=False))
            )
    assert violated(caught) == ("CheckViolation", "ck_certificates_modes")


async def test_a_custody_row_cannot_point_at_another_company_s_certificate(
    module_database: BoundDatabase,
) -> None:
    victim = a_company()
    certificate = await installed(module_database, victim)
    attacker = a_company()
    with pytest.raises(IntegrityError) as caught:
        async with module_database.unit(attacker) as unit:  # the policy lets the row in
            await unit.connection.execute(insert(CustodyRecord).values(custody_of(attacker, certificate)))
    assert violated(caught) == ("ForeignKeyViolation", "fk_custody_certificate")


async def test_a_certificate_is_never_deleted_and_its_sealed_material_never_rewritten(
    module_database: BoundDatabase,
) -> None:
    tenant = a_company()
    certificate = await installed(module_database, tenant)
    for statement in (
        delete(CertificateRecord).where(CertificateRecord.id == certificate),
        update(CustodyRecord).where(CustodyRecord.certificate_id == certificate).values(sealed=b"other"),
    ):
        with pytest.raises(ProgrammingError) as caught:
            async with module_database.unit(tenant) as unit:
                await unit.connection.execute(statement)
        assert isinstance(caught.value.orig, InsufficientPrivilege)
    async with module_database.unit(tenant) as unit:  # retiring a certificate deletes its sealed material
        await unit.connection.execute(
            delete(CustodyRecord).where(CustodyRecord.certificate_id == certificate)
        )
    assert await counts(module_database, tenant) == {"certificates": 1, "custody": 0}


async def test_a_certificate_and_its_sealed_material_read_back_as_written(
    module_database: BoundDatabase,
) -> None:
    tenant = a_company()
    row = certificate_of(tenant, test=True, live=True)
    sealed = custody_of(tenant, new_id())
    sealed["certificate_id"] = row["id"]
    async with module_database.unit(tenant) as unit:
        await unit.connection.execute(insert(CertificateRecord).values(row))
        await unit.connection.execute(insert(CustodyRecord).values(sealed))
    async with module_database.unit(tenant) as unit:
        certificate = (
            await unit.connection.execute(
                select(
                    CertificateRecord.organization_id,
                    CertificateRecord.company_id,
                    CertificateRecord.id,
                    CertificateRecord.serves_test,
                    CertificateRecord.serves_live,
                    CertificateRecord.backend,
                    CertificateRecord.status,
                    CertificateRecord.certificate_der,
                    CertificateRecord.fingerprint_sha256,
                    CertificateRecord.serial_number,
                    CertificateRecord.subject,
                    CertificateRecord.issuer,
                    CertificateRecord.not_before,
                    CertificateRecord.not_after,
                    CertificateRecord.installed_at,
                    CertificateRecord.retired_at,
                    CertificateRecord.retired_reason,
                    CertificateRecord.version,
                )
            )
        ).one()
        material = (
            await unit.connection.execute(
                select(
                    CustodyRecord.organization_id,
                    CustodyRecord.company_id,
                    CustodyRecord.certificate_id,
                    CustodyRecord.sealed,
                    CustodyRecord.wrapped_data_key,
                    CustodyRecord.kms_key_id,
                )
            )
        ).one()
    assert tuple(certificate) == tuple(row.values())
    assert tuple(material) == tuple(sealed.values())
