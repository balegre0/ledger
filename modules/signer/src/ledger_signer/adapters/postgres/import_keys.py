"""An import's idempotency key (plan Decision 9): the platform's ``Idempotency``, in signer's own schema.

The key is scoped as an HTTP route's is: the unit's organization and mode, the claim's principal (the actor
the api forwarded), the route ``grpc ImportCertificate`` and the key; the request is the claim's hash. What
it keeps is the answer as the gRPC service sends it, the ``ImportCertificateResponse`` bytes with status 201,
read back as the certificate's view on a replay (``stored_view``). Keys are purged by signer's own job.
"""

from datetime import UTC
from typing import Final, final, override
lazy from collections.abc import Awaitable, Callable, Mapping
lazy from datetime import datetime
lazy from uuid import UUID

from google.protobuf.timestamp_pb2 import Timestamp

from ledger_kernel.errors import InvalidValueError
from ledger_kernel.ids import CompanyId, OrganizationId
from ledger_kernel.tenancy import Mode
from ledger_platform.http.idempotency import Attempt, StoredResponse
from ledger_signer.adapters.grpc._generated.v1 import signer_pb2
from ledger_signer.api import CertificateView
from ledger_signer.application.ports import ImportKeys, ImportOutcome
from ledger_signer.domain.certificates import BackendKind, CertificateId, CertificateStatus
lazy from ledger_platform.data.unit_of_work import UnitOfWork
lazy from ledger_platform.http.idempotency import Idempotency
lazy from ledger_signer.application.ports import ImportClaim

ROUTE: Final = "grpc ImportCertificate"
"""The route an import's key is scoped to: no HTTP route has a name of this shape."""
CREATED: Final = 201
_MODES: Final[Mapping[Mode, signer_pb2.Mode.ValueType]] = {
    Mode.TEST: signer_pb2.MODE_TEST,
    Mode.LIVE: signer_pb2.MODE_LIVE,
}
_BACKENDS: Final[Mapping[BackendKind, signer_pb2.Backend.ValueType]] = {
    BackendKind.LOCAL: signer_pb2.BACKEND_LOCAL,
    BackendKind.REMOTE: signer_pb2.BACKEND_REMOTE,
}
_STATUSES: Final[Mapping[CertificateStatus, signer_pb2.CertificateStatus.ValueType]] = {
    CertificateStatus.ACTIVE: signer_pb2.CERTIFICATE_STATUS_ACTIVE,
    CertificateStatus.RETIRED: signer_pb2.CERTIFICATE_STATUS_RETIRED,
}


def _timestamp(moment: datetime) -> Timestamp:
    stamp = Timestamp()
    stamp.FromDatetime(moment)
    return stamp


def _back[T](table: Mapping[T, int], value: int, field: str) -> T:
    """The value of ``table`` whose wire value is ``value``; an unknown one is no answer the signer stored."""
    for known, wire in table.items():
        if wire == value:
            return known
    raise InvalidValueError(field=f"stored_answer.{field}", requirement="a value the signer sends")


def stored_answer(view: CertificateView) -> bytes:
    """``view`` as the ``ImportCertificateResponse`` an import's key keeps (not a replay)."""
    certificate = signer_pb2.Certificate(
        certificate_id=str(view.id),
        company=signer_pb2.Company(organization_id=str(view.organization), company_id=str(view.company)),
        modes=[wire for mode, wire in _MODES.items() if mode in view.modes],
        backend=_BACKENDS[view.backend],
        status=_STATUSES[view.status],
        fingerprint_sha256=view.fingerprint_sha256,
        serial_number=view.serial_number,
        subject=view.subject,
        issuer=view.issuer,
        not_before=_timestamp(view.not_before),
        not_after=_timestamp(view.not_after),
        installed_at=_timestamp(view.installed_at),
        retired_at=None if view.retired_at is None else _timestamp(view.retired_at),
        retired_reason=view.retired_reason or "",
        version=view.version,
    )
    return signer_pb2.ImportCertificateResponse(certificate=certificate, replayed=False).SerializeToString()


def stored_view(body: bytes) -> CertificateView:
    """The view ``stored_answer`` kept as ``body``."""
    certificate = signer_pb2.ImportCertificateResponse.FromString(body).certificate
    return CertificateView(
        id=CertificateId(UUID(certificate.certificate_id)),
        organization=OrganizationId(UUID(certificate.company.organization_id)),
        company=CompanyId(UUID(certificate.company.company_id)),
        modes=frozenset(_back(_MODES, wire, "modes") for wire in certificate.modes),
        backend=_back(_BACKENDS, certificate.backend, "backend"),
        status=_back(_STATUSES, certificate.status, "status"),
        fingerprint_sha256=certificate.fingerprint_sha256,
        serial_number=certificate.serial_number,
        subject=certificate.subject,
        issuer=certificate.issuer,
        not_before=certificate.not_before.ToDatetime(UTC),
        not_after=certificate.not_after.ToDatetime(UTC),
        installed_at=certificate.installed_at.ToDatetime(UTC),
        retired_at=certificate.retired_at.ToDatetime(UTC) if certificate.HasField("retired_at") else None,
        retired_reason=certificate.retired_reason or None,
        version=certificate.version,
    )


def _attempt(claim: ImportClaim) -> Attempt:
    return Attempt(principal=claim.principal, route=ROUTE, key=claim.key, request_hash=claim.request_hash)


@final
class PgImportKeys(ImportKeys):
    """The unit's import keys, in ``signer.idempotency_keys`` (see the module)."""

    __slots__ = ("_idempotency", "_unit")

    def __init__(self, unit: UnitOfWork, idempotency: Idempotency) -> None:
        self._unit = unit
        self._idempotency = idempotency

    @override
    async def answered(self, claim: ImportClaim) -> ImportOutcome | None:
        stored = await self._idempotency.stored(self._unit, _attempt(claim))
        return None if stored is None else ImportOutcome(view=stored_view(stored.body), replayed=True)

    @override
    async def once(self, claim: ImportClaim, work: Callable[[], Awaitable[CertificateView]]) -> ImportOutcome:
        installed: list[CertificateView] = []

        async def installing() -> StoredResponse:
            view = await work()
            installed.append(view)
            return StoredResponse(CREATED, stored_answer(view), {})

        outcome = await self._idempotency.once(self._unit, _attempt(claim), installing)
        if outcome.replayed:
            return ImportOutcome(view=stored_view(outcome.response.body), replayed=True)
        return ImportOutcome(view=installed[0], replayed=False)
