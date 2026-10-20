"""signer's units in PostgreSQL: what a unit records commits with it, an import's key is the platform's, and
the use cases work over custody and the database as they do over the stand-ins.

Two imports for one mode at once (different keys): the one that commits second is refused by the partial
unique index, tries again once, and replaces the first: one active certificate per mode, never two.
"""

import asyncio
import os
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Final, final, override
lazy from collections.abc import AsyncGenerator, Awaitable, Sequence
lazy from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.x509.oid import NameOID
lazy from psycopg import AsyncConnection

from ledger_fiscal_kit.ports import SignatureProfile, SigningCertificateMissingError, XmlToSign
from ledger_kernel.ids import CompanyId, OrganizationId, new_id
from ledger_kernel.tenancy import Mode, Tenant
from ledger_platform.data.bound import BoundDatabase
from ledger_platform.data.unit_of_work import JobPolicy
from ledger_platform.http.idempotency import IdempotencyKeyReusedError
from ledger_signer.adapters.custody.backends import Backends
from ledger_signer.adapters.custody.kms_local import MASTER_KEY_BYTES, LocalFileKms
from ledger_signer.adapters.postgres.unit import PgUnits
from ledger_signer.api import CertificateView
from ledger_signer.application.certificates import Retiring
from ledger_signer.application.importing import Importing, ImportRequest
from ledger_signer.application.notices import EXPIRY_NOTICE
from ledger_signer.application.ports import (
    ActiveCertificateTakenError,
    Certificates,
    ImportClaim,
    ImportOutcome,
    Secret,
    SignerRules,
    SignerUnits,
)
from ledger_signer.application.signing import Signing
from ledger_signer.domain.certificates import BackendKind, CertificateId, CertificateStatus, RetiredReason
from ledger_signer.events import CertificateRetired
from ledger_testing.database import signer_url
from ledger_testing.modules import QuietSignals
from ledger_testing.pki import OPENING_PHRASE, bundle, distinguished_name
from ledger_testing.waiting import eventually, waits_for_a_lock
lazy from ledger_kernel.clock import Clock
lazy from ledger_platform.data.database import Database
lazy from ledger_signer.application.ports import Custody, SealedMaterial, SignerUnit
lazy from ledger_signer.domain.certificates import Certificate
lazy from ledger_testing.pki import Authority, Issued
lazy from ledger_testing.postgres import WorkerDatabase

NOW: Final = datetime(2026, 10, 19, 12, tzinfo=UTC)
"""The stand-in clock's time (conftest's ``NOW``), around which the test certificates are valid."""
PASSWORD: Final = "PASSWORD-SENTINEL"
DOCUMENT: Final = b"<Document><Reference>DOCUMENT-SENTINEL</Reference></Document>"
RULES: Final = SignerRules(
    backends=frozenset(BackendKind), skew=timedelta(seconds=300), concurrency=2, notice_days=(30, 7, 0)
)
TEST: Final = frozenset({Mode.TEST})
OUTBOX: Final = "SELECT type FROM signer.outbox WHERE company_id = %s ORDER BY type"
AUDIT: Final = "SELECT action FROM signer.audit_events WHERE company_id = %s ORDER BY action"


class AbandonedError(Exception):
    """A unit's block that fails: the unit keeps nothing."""


def a_company() -> Tenant:
    """A company of a new organization, in test mode."""
    return Tenant(OrganizationId(new_id()), CompanyId(new_id()), Mode.TEST)


def owner_of(tenant: Tenant) -> Tenant:
    return Tenant(tenant.organization, tenant.company, None)


@pytest.fixture
def signer_database(database: Database) -> BoundDatabase:
    """signer's units as its role binds them: its events, and the job kind its use cases enqueue."""
    return BoundDatabase(
        database,
        schema="signer",
        source="signer",
        signals=QuietSignals(),
        publishes=frozenset({"signer.certificate.installed", "signer.certificate.retired"}),
        jobs={EXPIRY_NOTICE: JobPolicy(max_attempts=20, deadline=None)},
    )


@pytest.fixture
def units(signer_database: BoundDatabase) -> PgUnits:
    return PgUnits(signer_database)


@pytest.fixture
def local_kms(tmp_path: Path) -> LocalFileKms:
    """The local-file KMS over a key file of the test's own."""
    path = tmp_path / "master.key"
    path.write_bytes(os.urandom(MASTER_KEY_BYTES))
    return LocalFileKms(path, environment="test")


async def rows(admin: AsyncConnection, query: str, company: CompanyId) -> list[tuple[object, ...]]:
    """What ``query`` (with the company as its one parameter) finds, past row-level security."""
    cursor = await admin.execute(query.encode(), (company,))
    return list(await cursor.fetchall())


def a_view(tenant: Tenant) -> CertificateView:
    return CertificateView(
        id=CertificateId(new_id()),
        organization=tenant.organization,
        company=tenant.company,
        modes=TEST,
        backend=BackendKind.LOCAL,
        status=CertificateStatus.ACTIVE,
        fingerprint_sha256=bytes(32),
        serial_number="1f",
        subject="CN=Holder",
        issuer="CN=Authority",
        not_before=NOW,
        not_after=NOW + timedelta(days=365),
        installed_at=NOW,
        retired_at=None,
        retired_reason=None,
        version=1,
    )


async def test_a_unit_s_events_audit_rows_and_jobs_commit_with_it_or_not_at_all(
    units: PgUnits, admin: AsyncConnection
) -> None:
    tenant = a_company()
    certificate = CertificateId(new_id())
    run_at = NOW + timedelta(days=30)

    def record(unit: SignerUnit) -> None:
        unit.events.publish(
            CertificateRetired(
                scope=owner_of(tenant),
                subject=certificate,
                aggregate_version=2,
                certificate=certificate,
                reason=RetiredReason.RETIRED.value,
            )
        )
        unit.audit.record("certificate.retire", certificate, detail={"reason": "retired"}, before={"v": 1})
        unit.jobs.enqueue(EXPIRY_NOTICE, f"{certificate}:7", run_at=run_at, payload={"threshold_days": 7})

    async def abandoned() -> None:
        async with units.unit(tenant) as unit:
            record(unit)
            raise AbandonedError

    with pytest.raises(AbandonedError):
        await abandoned()
    outbox = "SELECT type, subject FROM signer.outbox WHERE company_id = %s"
    audit = "SELECT action, subject_id, actor, detail FROM signer.audit_events WHERE company_id = %s"
    jobs = "SELECT kind, key, run_at, mode, payload FROM signer.jobs WHERE company_id = %s"
    for query in (outbox, audit, jobs):
        assert await rows(admin, query, tenant.company) == []
    async with units.unit(tenant) as unit:
        record(unit)
        assert unit.now.utcoffset() is not None
    assert await rows(admin, outbox, tenant.company) == [("signer.certificate.retired", certificate)]
    assert await rows(admin, audit, tenant.company) == [
        ("certificate.retire", str(certificate), "system:test", {"reason": "retired"})
    ]
    assert await rows(admin, jobs, tenant.company) == [
        (EXPIRY_NOTICE, f"{certificate}:7", run_at, "test", {"threshold_days": 7})
    ]


async def test_an_import_s_key_is_claimed_once_and_its_answer_replayed(units: PgUnits) -> None:
    tenant = a_company()
    view = a_view(tenant)
    claim = ImportClaim(principal="human:owner", key="import-1", request_hash=bytes(32))
    worked: list[CertificateView] = []

    async def work() -> CertificateView:
        worked.append(view)
        return view

    async with units.unit(tenant) as unit:
        assert await unit.import_keys.answered(claim) is None
    async with units.unit(tenant) as unit:
        assert await unit.import_keys.once(claim, work) == ImportOutcome(view=view, replayed=False)
    async with units.unit(tenant) as unit:
        assert await unit.import_keys.answered(claim) == ImportOutcome(view=view, replayed=True)
    async with units.unit(tenant) as unit:
        assert await unit.import_keys.once(claim, work) == ImportOutcome(view=view, replayed=True)
    assert worked == [view]  # the work ran once
    other = replace(claim, request_hash=bytes([1]) * 32)
    async with units.unit(tenant) as unit:
        with pytest.raises(IdempotencyKeyReusedError):
            await unit.import_keys.answered(other)
    with pytest.raises(IdempotencyKeyReusedError):
        async with units.unit(tenant) as unit:
            await unit.import_keys.once(other, work)
    async with units.unit(Tenant(tenant.organization, tenant.company, Mode.LIVE)) as unit:
        assert await unit.import_keys.answered(claim) is None  # a key is kept per mode


@final
class _Watched(Certificates):
    """A unit's certificates, telling ``HeldUnits`` when one is added or refused for its mode."""

    __slots__ = ("_certificates", "added", "taken")

    def __init__(self, certificates: Certificates) -> None:
        self._certificates = certificates
        self.added = False
        self.taken = False

    @override
    async def active(self, company: CompanyId, mode: Mode) -> Certificate | None:
        return await self._certificates.active(company, mode)

    @override
    async def active_of_company(self, company: CompanyId, *, for_update: bool) -> Sequence[Certificate]:
        return await self._certificates.active_of_company(company, for_update=for_update)

    @override
    async def get(
        self, company: CompanyId, certificate: CertificateId, *, for_update: bool = False
    ) -> Certificate | None:
        return await self._certificates.get(company, certificate, for_update=for_update)

    @override
    async def listed(self, company: CompanyId, mode: Mode, *, include_retired: bool) -> Sequence[Certificate]:
        return await self._certificates.listed(company, mode, include_retired=include_retired)

    @override
    async def add(self, certificate: Certificate, material: SealedMaterial) -> None:
        try:
            await self._certificates.add(certificate, material)
        except ActiveCertificateTakenError:
            self.taken = True
            raise
        self.added = True

    @override
    async def save(self, certificate: Certificate) -> None:
        await self._certificates.save(certificate)

    @override
    async def material(self, certificate: Certificate) -> SealedMaterial:
        return await self._certificates.material(certificate)


@final
class HeldUnits(SignerUnits):
    """``PgUnits`` whose first unit to add a certificate commits only once another unit waits for a lock."""

    __slots__ = ("_held", "_units", "_url", "taken")

    def __init__(self, units: PgUnits, url: str) -> None:
        self._units = units
        self._url = url
        self._held = False
        self.taken = 0
        """How many adds the partial unique index refused."""

    async def _waiting(self) -> bool:
        return await waits_for_a_lock(self._url)

    @override
    @asynccontextmanager
    async def unit(self, tenant: Tenant) -> AsyncGenerator[SignerUnit]:
        async with self._units.unit(tenant) as unit:
            watched = _Watched(unit.certificates)
            try:
                yield replace(unit, certificates=watched)
            finally:
                self.taken += watched.taken
            if watched.added and not self._held:
                self._held = True
                await eventually(self._waiting)  # the other import's insert waits behind this one's


@pytest.fixture
def held(units: PgUnits, migrated: WorkerDatabase) -> HeldUnits:
    """signer's units, the first to add a certificate held until another waits behind it."""
    return HeldUnits(units, signer_url(migrated))


@pytest.fixture(scope="session")
def second(authority: Authority) -> Issued:
    """Another certificate of the company, valid as ``holder`` is."""
    return authority.issue(
        distinguished_name((NameOID.COMMON_NAME, "Second Holder")),
        not_before=NOW - timedelta(days=1),
        not_after=NOW + timedelta(days=365),
    )


@pytest.fixture
def racing(held: HeldUnits, custody: Custody, clock: Clock) -> Importing:
    """Imports over ``held``."""
    return Importing(units=held, custody=custody, rules=RULES, clock=clock)


async def test_two_imports_for_one_mode_at_once_leave_one_active_certificate(
    held: HeldUnits, racing: Importing, holder: Issued, second: Issued, admin: AsyncConnection
) -> None:
    tenant = a_company()

    def imported(key: str, issued: Issued) -> Awaitable[ImportOutcome]:
        request = ImportRequest(tenant=tenant, modes=TEST, idempotency_key=key)
        return racing.local(request, bundle(issued), Secret(OPENING_PHRASE))

    outcomes = await asyncio.gather(imported("first", holder), imported("second", second))
    assert held.taken == 1  # the second to commit was refused once, then replaced the first
    async with held.unit(tenant) as unit:
        listed = await unit.certificates.listed(tenant.company, Mode.TEST, include_retired=True)
    by_id = {certificate.id: certificate for certificate in listed}
    assert set(by_id) == {outcome.view.id for outcome in outcomes}
    (active,) = (certificate for certificate in listed if certificate.is_active)
    (replaced,) = (certificate for certificate in listed if not certificate.is_active)
    assert replaced.retired_reason is RetiredReason.REPLACED
    assert active.installed_at >= replaced.installed_at
    assert await rows(admin, OUTBOX, tenant.company) == [
        ("signer.certificate.installed",),
        ("signer.certificate.installed",),
        ("signer.certificate.retired",),
    ]


async def test_the_use_cases_keep_sign_and_retire_over_custody_and_postgres(
    units: PgUnits, local_kms: LocalFileKms, clock: Clock, holder: Issued, admin: AsyncConnection
) -> None:
    custody = Backends(local_kms, entity=None)
    importing = Importing(units=units, custody=custody, rules=RULES, clock=clock)
    signing = Signing(units=units, custody=custody, rules=RULES, clock=clock)
    tenant = a_company()
    request = ImportRequest(tenant=tenant, modes=TEST, idempotency_key="import-1")
    content = bundle(holder, password=PASSWORD)
    installed = await importing.local(request, content, Secret(PASSWORD))
    assert not installed.replayed
    assert await importing.local(request, content, Secret("another")) == replace(installed, replayed=True)
    key = holder.key.private_bytes(
        serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    sealed = await rows(
        admin, "SELECT sealed, kms_key_id FROM signer.custody WHERE company_id = %s", tenant.company
    )
    ((material, key_id),) = sealed
    assert isinstance(material, bytes)
    assert key not in material
    assert key_id == local_kms.key_id
    to_sign = XmlToSign(
        tenant=tenant,
        profile=SignatureProfile.XMLDSIG_ENVELOPED_RSA_SHA256,
        document=DOCUMENT,
        signed_at=NOW,
        reference="E310000000001",
    )
    signed = await signing.sign_xml("worker", to_sign)
    assert signed.certificate == installed.view.id
    assert b"<SignatureValue>" in signed.document
    retired = await Retiring(units).retire(owner_of(tenant), installed.view.id)
    assert retired.status is CertificateStatus.RETIRED
    assert await rows(admin, "SELECT 1 FROM signer.custody WHERE company_id = %s", tenant.company) == []
    with pytest.raises(SigningCertificateMissingError):
        await signing.sign_xml("worker", to_sign)
    assert await rows(admin, AUDIT, tenant.company) == [
        ("certificate.install",),
        ("certificate.retire",),
        ("certificate.sign_xml",),
    ]
