"""signer's certificates in PostgreSQL: ``Certificates`` over ``signer.certificates`` and ``signer.custody``.

The repository is bound to its unit's organization and names it, and the company, in every statement;
row-level security enforces the unit's scope again (§2). A certificate of another organization handed to it
is refused before any statement (``ForeignCertificateError``). A certificate and its sealed material are
written together; a retired certificate's material is deleted in the same unit (it never signs again, so its
key is not kept). A save names the version it moves from: a certificate another unit moved meanwhile is
``StaleCertificateError`` (the use cases lock what they change, so it is a defect).

The partial unique indexes keep one active certificate per company and mode: their violation is
``ActiveCertificateTakenError``, whatever statement meets it (another unit installed one meanwhile); any other
refusal stays the database's. Rows are read in a fixed order: the company's active certificates by id (the
order their locks are taken in), the lists the latest first.
"""

from contextlib import contextmanager
from typing import ClassVar, Final, final, override
lazy from collections.abc import Generator, Sequence
lazy from datetime import datetime
lazy from uuid import UUID

from psycopg.errors import UniqueViolation
from sqlalchemy import delete, insert, select, update
from sqlalchemy.exc import IntegrityError
lazy from sqlalchemy import ColumnElement, Row, Select
lazy from sqlalchemy.ext.asyncio import AsyncConnection

from ledger_kernel.errors import LedgerError
from ledger_kernel.ids import CompanyId, OrganizationId
from ledger_kernel.tenancy import Mode, Tenant
from ledger_signer.adapters.postgres.tables import CertificateRecord, CustodyRecord
from ledger_signer.application.ports import ActiveCertificateTakenError, Certificates, SealedMaterial
from ledger_signer.domain.certificates import (
    BackendKind,
    Certificate,
    CertificateId,
    CertificateStatus,
    RetiredReason,
)
from ledger_signer.domain.errors import CertificateRetiredError

ONE_ACTIVE_PER_MODE: Final = frozenset(f"certificates_one_active_{mode.value}" for mode in Mode)
"""The partial unique indexes that keep one active certificate per company and mode."""
_SERVES: Final = {
    Mode.TEST: CertificateRecord.serves_test.expression,
    Mode.LIVE: CertificateRecord.serves_live.expression,
}
_ACTIVE: Final = CertificateRecord.status == CertificateStatus.ACTIVE.value


class ForeignCertificateError(LedgerError):
    """A repository of one organization's unit was handed another organization's certificate."""

    code: ClassVar[str] = "signer_foreign_tenant"

    def __init__(self) -> None:
        super().__init__("a unit keeps the certificates of its own organization only")


class StaleCertificateError(LedgerError):
    """A certificate saved from a version another unit already moved: its caller did not lock it."""

    code: ClassVar[str] = "signer_certificate_stale"

    def __init__(self) -> None:
        super().__init__("a certificate was saved over a version it was not read at")


@contextmanager
def taken_as_raced() -> Generator[None]:
    """A partial unique index refusing a second active certificate for a mode, as the race it is.

    ``ActiveCertificateTakenError`` (from the database's refusal); every other refusal passes as it is.
    """
    try:
        yield
    except IntegrityError as error:
        refusal = error.orig
        if isinstance(refusal, UniqueViolation) and refusal.diag.constraint_name in ONE_ACTIVE_PER_MODE:
            raise ActiveCertificateTakenError from error
        raise


type CertificateRow = Row[
    UUID,
    UUID,
    UUID,
    bool,
    bool,
    str,
    str,
    bytes,
    bytes,
    str,
    str,
    str,
    datetime,
    datetime,
    datetime,
    datetime | None,
    str | None,
    int,
]
type CertificateSelect = Select[
    UUID,
    UUID,
    UUID,
    bool,
    bool,
    str,
    str,
    bytes,
    bytes,
    str,
    str,
    str,
    datetime,
    datetime,
    datetime,
    datetime | None,
    str | None,
    int,
]
_CERTIFICATE: Final = select(
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


def _certificate(row: CertificateRow) -> Certificate:
    (
        organization,
        company,
        identifier,
        serves_test,
        serves_live,
        backend,
        status,
        der,
        fingerprint,
        serial_number,
        subject,
        issuer,
        not_before,
        not_after,
        installed_at,
        retired_at,
        retired_reason,
        version,
    ) = row
    served = {Mode.TEST: serves_test, Mode.LIVE: serves_live}
    return Certificate(
        id=CertificateId(identifier),
        tenant=Tenant(OrganizationId(organization), CompanyId(company), None),
        modes=frozenset(mode for mode, serves in served.items() if serves),
        backend=BackendKind(backend),
        status=CertificateStatus(status),
        der=der,
        fingerprint_sha256=fingerprint,
        serial_number=serial_number,
        subject=subject,
        issuer=issuer,
        not_before=not_before,
        not_after=not_after,
        installed_at=installed_at,
        retired_at=retired_at,
        retired_reason=None if retired_reason is None else RetiredReason(retired_reason),
        version=version,
    )


def _state(certificate: Certificate) -> dict[str, object]:
    """The columns a certificate's changes touch."""
    reason = certificate.retired_reason
    return {
        "serves_test": Mode.TEST in certificate.modes,
        "serves_live": Mode.LIVE in certificate.modes,
        "status": certificate.status.value,
        "retired_at": certificate.retired_at,
        "retired_reason": None if reason is None else reason.value,
        "version": certificate.version,
    }


@final
class PgCertificates(Certificates):
    """The certificates of the unit's organization, in PostgreSQL (see the module)."""

    __slots__ = ("_connection", "_organization")

    def __init__(self, connection: AsyncConnection, organization: OrganizationId) -> None:
        self._connection = connection
        self._organization = organization

    def _require_own(self, certificate: Certificate) -> None:
        if certificate.tenant.organization != self._organization:
            raise ForeignCertificateError

    def _of(self, company: CompanyId, *where: ColumnElement[bool]) -> CertificateSelect:
        return _CERTIFICATE.where(
            CertificateRecord.organization_id == self._organization,
            CertificateRecord.company_id == company,
            *where,
        )

    async def _all(self, statement: CertificateSelect) -> list[Certificate]:
        found = await self._connection.execute(statement)
        return [_certificate(row) for row in found.all()]

    @override
    async def active(self, company: CompanyId, mode: Mode) -> Certificate | None:
        found = await self._all(self._of(company, _ACTIVE, _SERVES[mode]))
        return found[0] if found else None

    @override
    async def active_of_company(self, company: CompanyId, *, for_update: bool) -> Sequence[Certificate]:
        statement = self._of(company, _ACTIVE).order_by(CertificateRecord.id)
        return await self._all(statement.with_for_update() if for_update else statement)

    @override
    async def get(
        self, company: CompanyId, certificate: CertificateId, *, for_update: bool = False
    ) -> Certificate | None:
        statement = self._of(company, CertificateRecord.id == certificate)
        found = await self._all(statement.with_for_update() if for_update else statement)
        return found[0] if found else None

    @override
    async def listed(self, company: CompanyId, mode: Mode, *, include_retired: bool) -> Sequence[Certificate]:
        statement = self._of(company, _SERVES[mode], *(() if include_retired else (_ACTIVE,)))
        return await self._all(
            statement.order_by(CertificateRecord.installed_at.desc(), CertificateRecord.id.desc())
        )

    @override
    async def add(self, certificate: Certificate, material: SealedMaterial) -> None:
        self._require_own(certificate)
        owner = certificate.tenant
        with taken_as_raced():
            await self._connection.execute(
                insert(CertificateRecord).values(
                    organization_id=owner.organization,
                    company_id=owner.company,
                    id=certificate.id,
                    backend=certificate.backend.value,
                    certificate_der=certificate.der,
                    fingerprint_sha256=certificate.fingerprint_sha256,
                    serial_number=certificate.serial_number,
                    subject=certificate.subject,
                    issuer=certificate.issuer,
                    not_before=certificate.not_before,
                    not_after=certificate.not_after,
                    installed_at=certificate.installed_at,
                    **_state(certificate),
                )
            )
        await self._connection.execute(
            insert(CustodyRecord).values(
                organization_id=owner.organization,
                company_id=owner.company,
                certificate_id=certificate.id,
                sealed=material.sealed,
                wrapped_data_key=material.wrapped_data_key,
                kms_key_id=material.kms_key_id,
            )
        )

    @override
    async def save(self, certificate: Certificate) -> None:
        self._require_own(certificate)
        company = certificate.tenant.company
        statement = (
            update(CertificateRecord)
            .where(
                CertificateRecord.organization_id == self._organization,
                CertificateRecord.company_id == company,
                CertificateRecord.id == certificate.id,
                CertificateRecord.version == certificate.version - 1,
            )
            .values(**_state(certificate))
        )
        with taken_as_raced():
            changed = await self._connection.execute(statement)
        if changed.rowcount != 1:
            raise StaleCertificateError
        if not certificate.is_active:
            await self._connection.execute(
                delete(CustodyRecord).where(
                    CustodyRecord.organization_id == self._organization,
                    CustodyRecord.company_id == company,
                    CustodyRecord.certificate_id == certificate.id,
                )
            )

    @override
    async def material(self, certificate: Certificate) -> SealedMaterial:
        """``CertificateRetiredError`` when none is kept: a retirement deleted it, perhaps since read."""
        self._require_own(certificate)
        found = await self._connection.execute(
            select(CustodyRecord.sealed, CustodyRecord.wrapped_data_key, CustodyRecord.kms_key_id).where(
                CustodyRecord.organization_id == self._organization,
                CustodyRecord.company_id == certificate.tenant.company,
                CustodyRecord.certificate_id == certificate.id,
            )
        )
        row = found.first()
        if row is None:
            raise CertificateRetiredError
        sealed, wrapped, key_id = row
        return SealedMaterial(sealed=sealed, wrapped_data_key=wrapped, kms_key_id=key_id)
