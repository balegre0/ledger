"""Expiry notices (plan Decision 10): jobs planned at installation, announced when they fall due.

Installing a certificate enqueues one ``signer.expiry_notice`` job per notice the domain plans
(``domain.expiry.notices``), keyed ``<certificate id>:<threshold days>``, in the installation's unit. When one
falls due the certificate is announced as expiring if it is still active; a certificate retired meanwhile is
announced nothing. No scan of every tenant's certificates, no table of its own.
"""

from typing import Final, final
lazy from collections.abc import Sequence

from ledger_signer.domain.expiry import notices
from ledger_signer.events import CertificateExpiring
lazy from ledger_kernel.tenancy import Tenant
lazy from ledger_signer.application.ports import JobKind, SignerUnit, SignerUnits
lazy from ledger_signer.domain.certificates import Certificate, CertificateId

EXPIRY_NOTICE: Final[JobKind] = "signer.expiry_notice"


def plan_notices(unit: SignerUnit, certificate: Certificate, *, days: Sequence[int]) -> None:
    """Enqueue, in ``unit``, the notices of ``certificate`` installed at the unit's time."""
    for notice in notices(certificate.not_after, days=days, now=unit.now):
        unit.jobs.enqueue(
            EXPIRY_NOTICE,
            f"{certificate.id}:{notice.threshold_days}",
            run_at=notice.at,
            payload={"certificate": str(certificate.id), "threshold_days": notice.threshold_days},
        )


@final
class Notices:
    """A notice fallen due: the certificate announced as expiring, if it still signs."""

    __slots__ = ("_units",)

    def __init__(self, units: SignerUnits) -> None:
        self._units = units

    async def notify(self, tenant: Tenant, certificate: CertificateId, *, threshold_days: int) -> bool:
        """Announce that ``certificate`` of ``tenant``'s company expires within ``threshold_days`` days.

        ``tenant`` is the job's scope. True when it was announced; false when the certificate is retired or
        unknown (nothing to tell).
        """
        async with self._units.unit(tenant) as unit:
            found = await unit.certificates.get(tenant.company, certificate)
            if found is None or not found.is_active:
                return False
            unit.events.publish(
                CertificateExpiring(
                    scope=found.tenant,
                    subject=found.id,
                    aggregate_version=found.version,
                    certificate=found.id,
                    not_after=found.not_after,
                    threshold_days=threshold_days,
                )
            )
            return True
