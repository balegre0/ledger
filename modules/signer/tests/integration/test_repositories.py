"""signer's certificates in PostgreSQL, through the units the use cases work in (``PgUnits``).

A certificate and its sealed material are kept together and read back as they were; a retirement deletes the
material; a company's unit sees its own certificates only; a second active certificate for a mode is
``ActiveCertificateTakenError`` whether the index refuses it at the statement or at the commit, and every
other refusal stays what it is.
"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Final

import pytest
from psycopg.errors import UniqueViolation
from sqlalchemy import insert
from sqlalchemy.exc import IntegrityError
lazy from psycopg import AsyncConnection

from ledger_kernel.ids import CompanyId, OrganizationId, new_id
from ledger_kernel.tenancy import Mode, Tenant
from ledger_platform.data.unit_of_work import UnitOfWork, open_unit
from ledger_signer.adapters.postgres.repositories import ForeignCertificateError, StaleCertificateError
from ledger_signer.adapters.postgres.tables import CertificateRecord
from ledger_signer.adapters.postgres.unit import PgUnits, SignerUnitUnscopedError, bind
from ledger_signer.application.ports import ActiveCertificateTakenError, SealedMaterial
from ledger_signer.domain.certificates import (
    BackendKind,
    Certificate,
    CertificateId,
    CertificateStatus,
    RetiredReason,
)
from ledger_signer.domain.errors import CertificateRetiredError
lazy from ledger_platform.data.bound import BoundDatabase

NOW: Final = datetime(2026, 10, 19, 12, tzinfo=UTC)
TEST: Final = frozenset({Mode.TEST})
LIVE: Final = frozenset({Mode.LIVE})
BOTH: Final = frozenset(Mode)


def a_company(organization: OrganizationId | None = None) -> Tenant:
    return Tenant(organization or OrganizationId(new_id()), CompanyId(new_id()), None)


def a_certificate(
    owner: Tenant, modes: frozenset[Mode] = TEST, *, installed_at: datetime = NOW
) -> Certificate:
    return Certificate(
        id=CertificateId(new_id()),
        tenant=owner,
        modes=modes,
        backend=BackendKind.LOCAL,
        status=CertificateStatus.ACTIVE,
        der=b"0\x82 certificate",
        fingerprint_sha256=new_id().bytes * 2,
        serial_number="1f",
        subject="CN=SUBJECT-SENTINEL",
        issuer="CN=Authority",
        not_before=NOW - timedelta(days=1),
        not_after=NOW + timedelta(days=365),
        installed_at=installed_at,
        retired_at=None,
        retired_reason=None,
        version=1,
    )


def material_of(certificate: Certificate) -> SealedMaterial:
    return SealedMaterial(
        sealed=b"sealed " + certificate.id.bytes,
        wrapped_data_key=b"wrapped",
        kms_key_id="local:0011223344556677",
    )


def row_of(certificate: Certificate) -> dict[str, object]:
    return {
        "organization_id": certificate.tenant.organization,
        "company_id": certificate.tenant.company,
        "id": certificate.id,
        "serves_test": Mode.TEST in certificate.modes,
        "serves_live": Mode.LIVE in certificate.modes,
        "backend": certificate.backend.value,
        "status": certificate.status.value,
        "certificate_der": certificate.der,
        "fingerprint_sha256": certificate.fingerprint_sha256,
        "serial_number": certificate.serial_number,
        "subject": certificate.subject,
        "issuer": certificate.issuer,
        "not_before": certificate.not_before,
        "not_after": certificate.not_after,
        "installed_at": certificate.installed_at,
        "retired_at": None,
        "retired_reason": None,
        "version": certificate.version,
    }


def in_mode(owner: Tenant, mode: Mode = Mode.TEST) -> Tenant:
    return Tenant(owner.organization, owner.company, mode)


@pytest.fixture
def units(module_database: BoundDatabase) -> PgUnits:
    return PgUnits(module_database)


async def added(units: PgUnits, *certificates: Certificate) -> None:
    for certificate in certificates:
        async with units.unit(in_mode(certificate.tenant)) as unit:
            await unit.certificates.add(certificate, material_of(certificate))


async def recorded(units: PgUnits, owner: Tenant, row: dict[str, object]) -> None:
    """``row`` inserted into ``signer.certificates`` as ``owner``'s unit ends: written with its records."""
    async with units.unit(in_mode(owner)):
        uow = open_unit()
        assert isinstance(uow, UnitOfWork)
        uow.record(insert(CertificateRecord).values(row))


async def custody_rows(admin: AsyncConnection, certificate: Certificate) -> int:
    """How many custody rows ``certificate`` has, past row-level security."""
    cursor = await admin.execute(b"SELECT 1 FROM signer.custody WHERE certificate_id = %s", (certificate.id,))
    return len(await cursor.fetchall())


async def test_a_certificate_and_its_material_read_back_as_kept(units: PgUnits) -> None:
    owner = a_company()
    certificate = a_certificate(owner, BOTH)
    await added(units, certificate)
    async with units.unit(in_mode(owner, Mode.LIVE)) as unit:
        repository = unit.certificates
        assert await repository.active(owner.company, Mode.LIVE) == certificate
        assert await repository.active(owner.company, Mode.TEST) == certificate
        assert await repository.get(owner.company, certificate.id) == certificate
        assert await repository.get(owner.company, certificate.id, for_update=True) == certificate
        assert await repository.active_of_company(owner.company, for_update=True) == [certificate]
        assert await repository.listed(owner.company, Mode.TEST, include_retired=False) == [certificate]
        assert await repository.material(certificate) == material_of(certificate)
        assert await repository.get(owner.company, CertificateId(new_id())) is None


async def test_modes_given_up_and_a_retirement_are_kept_the_material_deleted(
    units: PgUnits, admin: AsyncConnection
) -> None:
    owner = a_company()
    certificate = a_certificate(owner, BOTH)
    await added(units, certificate)
    live_only = certificate.without(TEST, at=NOW + timedelta(hours=1))
    async with units.unit(in_mode(owner)) as unit:
        await unit.certificates.save(live_only)
        assert await unit.certificates.active(owner.company, Mode.TEST) is None
    assert await custody_rows(admin, certificate) == 1
    retired = live_only.retired(RetiredReason.RETIRED, at=NOW + timedelta(hours=2))
    async with units.unit(in_mode(owner)) as unit:
        await unit.certificates.save(retired)
    assert await custody_rows(admin, certificate) == 0  # a retired certificate keeps no key
    async with units.unit(in_mode(owner, Mode.LIVE)) as unit:
        repository = unit.certificates
        assert await repository.get(owner.company, certificate.id) == retired
        assert await repository.active(owner.company, Mode.LIVE) is None
        assert await repository.active_of_company(owner.company, for_update=False) == []
        assert await repository.listed(owner.company, Mode.LIVE, include_retired=False) == []
        assert await repository.listed(owner.company, Mode.LIVE, include_retired=True) == [retired]
        assert (
            await repository.listed(owner.company, Mode.TEST, include_retired=True) == []
        )  # it left test first
        with pytest.raises(CertificateRetiredError):
            await repository.material(retired)


async def test_certificates_are_listed_by_mode_the_latest_first(units: PgUnits) -> None:
    owner = a_company()
    first = a_certificate(owner, TEST, installed_at=NOW)
    second = a_certificate(owner, LIVE, installed_at=NOW + timedelta(minutes=1))
    await added(units, first, second)
    replaced = first.retired(RetiredReason.REPLACED, at=NOW + timedelta(minutes=2))
    third = a_certificate(owner, TEST, installed_at=NOW + timedelta(minutes=2))
    async with units.unit(in_mode(owner)) as unit:
        await unit.certificates.save(replaced)
        await unit.certificates.add(third, material_of(third))
        assert await unit.certificates.listed(owner.company, Mode.TEST, include_retired=True) == [
            third,
            replaced,
        ]
        assert await unit.certificates.listed(owner.company, Mode.LIVE, include_retired=True) == [second]
        assert await unit.certificates.active_of_company(owner.company, for_update=True) == sorted(
            [second, third], key=lambda certificate: certificate.id
        )


async def test_a_company_s_unit_sees_its_own_certificates_only(units: PgUnits) -> None:
    owner = a_company()
    sibling = a_company(owner.organization)
    certificate = a_certificate(owner)
    await added(units, certificate)
    async with units.unit(in_mode(sibling)) as unit:  # row-level security: another company's unit
        assert await unit.certificates.get(owner.company, certificate.id) is None
        assert await unit.certificates.active(owner.company, Mode.TEST) is None
    async with units.unit(in_mode(owner)) as unit:  # the statements name the company too
        assert await unit.certificates.get(sibling.company, certificate.id) is None
        assert await unit.certificates.listed(sibling.company, Mode.TEST, include_retired=True) == []


async def test_another_organization_s_certificate_is_refused_before_any_statement(units: PgUnits) -> None:
    owner = a_company()
    foreign = a_certificate(a_company())
    async with units.unit(in_mode(owner)) as unit:
        repository = unit.certificates
        with pytest.raises(ForeignCertificateError):
            await repository.add(foreign, material_of(foreign))
        with pytest.raises(ForeignCertificateError):
            await repository.save(foreign)
        with pytest.raises(ForeignCertificateError):
            await repository.material(foreign)


async def test_a_save_of_a_version_another_unit_moved_is_refused(units: PgUnits) -> None:
    owner = a_company()
    certificate = a_certificate(owner, BOTH)
    await added(units, certificate)
    skipped = certificate.without(TEST, at=NOW).retired(RetiredReason.RETIRED, at=NOW)  # two versions on
    with pytest.raises(StaleCertificateError):
        async with units.unit(in_mode(owner)) as unit:
            await unit.certificates.save(skipped)


async def test_a_second_active_certificate_for_a_mode_is_taken_at_the_statement(units: PgUnits) -> None:
    owner = a_company()
    await added(units, a_certificate(owner, BOTH))
    rival = a_certificate(owner, LIVE)
    with pytest.raises(ActiveCertificateTakenError) as caught:
        await added(units, rival)
    assert isinstance(caught.value.__cause__, IntegrityError)
    async with units.unit(in_mode(owner)) as unit:
        assert await unit.certificates.get(owner.company, rival.id) is None


async def test_a_save_that_takes_a_mode_served_by_another_is_taken(units: PgUnits) -> None:
    owner = a_company()
    tested, lived = a_certificate(owner, TEST), a_certificate(owner, LIVE)
    await added(units, tested, lived)
    widened = replace(tested, modes=BOTH, version=2)  # never the domain's doing: the index still refuses it
    with pytest.raises(ActiveCertificateTakenError):
        async with units.unit(in_mode(owner)) as unit:
            await unit.certificates.save(widened)


async def test_a_second_active_certificate_written_at_the_commit_is_taken(units: PgUnits) -> None:
    owner = a_company()
    await added(units, a_certificate(owner, TEST))
    with pytest.raises(ActiveCertificateTakenError):
        await recorded(units, owner, row_of(a_certificate(owner, TEST)))


async def test_a_certificate_added_twice_is_a_defect_not_a_race(units: PgUnits) -> None:
    owner = a_company()
    certificate = a_certificate(owner, TEST)
    await added(units, certificate)
    retired = certificate.retired(RetiredReason.RETIRED, at=NOW)
    with pytest.raises(IntegrityError) as caught:
        await added(units, retired)  # the same id: its primary key refuses it
    assert isinstance(caught.value.orig, UniqueViolation)
    assert caught.value.orig.diag.constraint_name == "certificates_pkey"
    unserving = {**row_of(a_certificate(owner)), "serves_test": False, "serves_live": False}
    with pytest.raises(IntegrityError):  # a check refuses it, not the index
        await recorded(units, owner, unserving)


async def test_a_unit_scoped_to_nobody_has_no_signer_view(module_database: BoundDatabase) -> None:
    async with module_database.unit(None) as uow:
        with pytest.raises(SignerUnitUnscopedError) as caught:
            bind(uow)
    assert caught.value.code == "signer_unit_unscoped"
