"""Certificates described and retired: by the company, and when the company is deactivated.

What the other modules and the HTTP edge see of a certificate is its view (``view_of``): its public part,
never its key. A retirement is audited and announced once; retiring a retired certificate answers it as it is,
with nothing recorded (``RetireCertificate`` is naturally idempotent). A retirement is asked in a mode: one
asked in test mode never stops a certificate that serves live (``certificate_serves_live``; ADR 0007, a
test-mode credential is safe to hand out), one asked in live mode may retire any.
"""

from typing import ClassVar, Final, final
lazy from collections.abc import Sequence

from ledger_kernel.errors import ConflictError, InvalidValueError
from ledger_kernel.tenancy import Mode, Tenant
from ledger_signer.api import CertificateNotFoundError, CertificateView
from ledger_signer.domain.certificates import RetiredReason
from ledger_signer.events import CertificateRetired
lazy from ledger_kernel.ids import CompanyId
lazy from ledger_signer.application.ports import SignerUnit, SignerUnits
lazy from ledger_signer.domain.certificates import Certificate, CertificateId

_MODES: Final = tuple(Mode)
"""The modes in one order, for every list of them."""


class CertificateServesLiveError(ConflictError):
    """A retirement asked in test mode of a certificate that serves live: only a live request may stop it."""

    code: ClassVar[str] = "certificate_serves_live"

    def __init__(self) -> None:
        super().__init__("the certificate serves live mode: retire it with a live-mode request")


def view_of(certificate: Certificate) -> CertificateView:
    """What is shown of ``certificate``."""
    return CertificateView(
        id=certificate.id,
        organization=certificate.tenant.organization,
        company=certificate.tenant.company,
        modes=certificate.modes,
        backend=certificate.backend,
        status=certificate.status,
        fingerprint_sha256=certificate.fingerprint_sha256,
        serial_number=certificate.serial_number,
        subject=certificate.subject,
        issuer=certificate.issuer,
        not_before=certificate.not_before,
        not_after=certificate.not_after,
        installed_at=certificate.installed_at,
        retired_at=certificate.retired_at,
        retired_reason=None if certificate.retired_reason is None else certificate.retired_reason.value,
        version=certificate.version,
    )


def modes_of(certificate: Certificate) -> tuple[Mode, ...]:
    """The modes ``certificate`` serves (served last, once retired), in ``Mode``'s order."""
    return tuple(mode for mode in _MODES if mode in certificate.modes)


def state_of(certificate: Certificate) -> dict[str, str | int | list[str] | None]:
    """What an audit row hashes of ``certificate``: its state, never its subject or its key."""
    return {
        "id": str(certificate.id),
        "modes": [mode.value for mode in modes_of(certificate)],
        "backend": certificate.backend.value,
        "status": certificate.status.value,
        "fingerprint_sha256": certificate.fingerprint_sha256.hex(),
        "retired_reason": None if certificate.retired_reason is None else certificate.retired_reason.value,
        "version": certificate.version,
    }


def retired_event(certificate: Certificate) -> CertificateRetired:
    """The announcement that ``certificate`` (retired) never signs again."""
    reason = certificate.retired_reason
    if reason is None:
        raise InvalidValueError(field="retired_event.certificate", requirement="a retired certificate")
    return CertificateRetired(
        scope=certificate.tenant,
        subject=certificate.id,
        aggregate_version=certificate.version,
        certificate=certificate.id,
        reason=reason.value,
    )


async def _retire(unit: SignerUnit, certificate: Certificate, reason: RetiredReason) -> Certificate:
    """``certificate`` (active) retired for ``reason`` in ``unit``: kept, audited and announced."""
    retired = certificate.retired(reason, at=unit.now)
    await unit.certificates.save(retired)
    unit.audit.record(
        "certificate.retire",
        retired.id,
        before=state_of(certificate),
        after=state_of(retired),
        detail={"reason": reason.value},
    )
    unit.events.publish(retired_event(retired))
    return retired


def require_mode(owner: str, tenant: Tenant) -> Mode:
    """``tenant``'s mode; a tenant without one is a defect of ``owner``'s caller."""
    mode = tenant.mode
    if mode is None:
        raise InvalidValueError(field=f"{owner}.tenant", requirement="a tenant in a mode")
    return mode


def _active_first(certificate: Certificate) -> tuple[bool, float]:
    return (not certificate.is_active, -certificate.installed_at.timestamp())


@final
class Describing:
    """A company's certificates, as they are shown."""

    __slots__ = ("_units",)

    def __init__(self, units: SignerUnits) -> None:
        self._units = units

    async def certificates(self, tenant: Tenant, *, include_retired: bool) -> tuple[CertificateView, ...]:
        """The certificates serving ``tenant``'s company in its mode, active first, the latest first.

        Retired ones (those that served the mode last) only when ``include_retired``.
        """
        mode = require_mode("describing", tenant)
        async with self._units.unit(tenant) as unit:
            listed: Sequence[Certificate] = await unit.certificates.listed(
                tenant.company, mode, include_retired=include_retired
            )
        return tuple(view_of(certificate) for certificate in sorted(listed, key=_active_first))

    async def active(self, tenant: Tenant) -> CertificateView | None:
        """The certificate that signs for ``tenant``'s company in its mode, if any."""
        mode = require_mode("describing", tenant)
        async with self._units.unit(tenant) as unit:
            certificate = await unit.certificates.active(tenant.company, mode)
        return None if certificate is None else view_of(certificate)


@final
class Retiring:
    """A company retires one of its certificates, for every mode."""

    __slots__ = ("_units",)

    def __init__(self, units: SignerUnits) -> None:
        self._units = units

    async def retire(self, company: Tenant, certificate: CertificateId) -> CertificateView:
        """``certificate`` of ``company`` retired for every mode, or as it is when it was already.

        ``company`` is the company in the request's mode. ``CertificateNotFoundError`` when the company has no
        such certificate; ``CertificateServesLiveError`` when a test-mode request names an active certificate
        that serves live.
        """
        mode = require_mode("retiring", company)
        owner = Tenant(company.organization, company.company, None)
        async with self._units.unit(owner) as unit:
            found = await unit.certificates.get(company.company, certificate, for_update=True)
            if found is None:
                raise CertificateNotFoundError
            if not found.is_active:
                return view_of(found)
            if mode is Mode.TEST and Mode.LIVE in found.modes:
                raise CertificateServesLiveError
            return view_of(await _retire(unit, found, RetiredReason.RETIRED))


async def retire_company(unit: SignerUnit, company: CompanyId) -> None:
    """Every active certificate of ``company`` retired ``company_deactivated``, in the consumer's ``unit``."""
    for certificate in await unit.certificates.active_of_company(company, for_update=True):
        _ = await _retire(unit, certificate, RetiredReason.COMPANY_DEACTIVATED)
