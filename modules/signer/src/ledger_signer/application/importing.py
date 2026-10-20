"""Importing a certificate: admitted by custody outside any unit, installed in one unit under its key.

The order (spec §3.6, plan Decisions 8 to 10): the backend must be offered and the idempotency key well formed
(nothing else happens otherwise); a short unit looks the key up, and an answer stored under it is replayed at
once, with no custody, KMS or entity call (a replay needs neither the password nor a certificate still valid);
custody opens the bundle or asks the entity, and seals the secret under a
data key the KMS wraps, outside any unit; the domain admits the certificate's facts; then one unit, under the
request's tenant (its mode scopes the key), claims the key and, unless it replays an earlier answer, locks the
company's active certificates, installs the new one (each of them loses its modes, retired once it serves
none), keeps it, audits it, announces it and every certificate it retired, and plans its expiry notices.

Two imports for one mode at once, with different keys, both find no certificate to lock: the second to
commit is refused by the partial unique index (``ActiveCertificateTakenError``) and is tried again once, when
it finds the first and replaces it. A failure keeps nothing: no key, no row, no event.

The password is never stored, hashed or fingerprinted: the same bundle under the same key with another
password is the same request, and its answer replays once the first succeeded. A first import with a wrong
password is refused (``certificate_bundle_unreadable``) and keeps nothing, so the key stays free.
"""

import hashlib
from dataclasses import dataclass
from typing import Final, final
lazy from collections.abc import Awaitable, Callable

from ledger_kernel.errors import InvalidValueError
from ledger_kernel.ids import new_id
from ledger_kernel.tenancy import Tenant
from ledger_platform.data.audit import current_actor
from ledger_platform.data.idempotency import InvalidIdempotencyKeyError, is_idempotency_key
from ledger_signer.api import BackendNotOfferedError
from ledger_signer.application.certificates import modes_of, retired_event, state_of, view_of
from ledger_signer.application.notices import plan_notices
from ledger_signer.application.ports import ActiveCertificateTakenError, ImportClaim, SealContext
from ledger_signer.domain.certificates import (
    BackendKind,
    Certificate,
    CertificateId,
    CertificateStatus,
    admit,
    install,
)
from ledger_signer.events import CertificateInstalled
lazy from ledger_kernel.clock import Clock
lazy from ledger_kernel.tenancy import Mode
lazy from ledger_signer.api import CertificateView
lazy from ledger_signer.application.ports import (
    Admitted,
    Custody,
    ImportOutcome,
    Secret,
    SignerRules,
    SignerUnit,
    SignerUnits,
)

_LENGTH_BYTES: Final = 8


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class ImportRequest:
    """An import's header: for whom, in which mode, for which modes, under which idempotency key."""

    tenant: Tenant
    """The organization, the company and the request's mode (which scopes the key)."""
    modes: frozenset[Mode]
    """The modes the certificate serves: one or both (they need not include the request's)."""
    idempotency_key: str

    def __post_init__(self) -> None:
        if self.tenant.mode is None:
            raise InvalidValueError(field="import_request.tenant", requirement="a tenant in a mode")
        if not self.modes:
            raise InvalidValueError(field="import_request.modes", requirement="one mode or both")


def fingerprint_of(request: ImportRequest, backend: BackendKind, source_sha256: bytes) -> bytes:
    """Decision 9's request hash, the password in no part of it.

    SHA-256 over the length-prefixed company, request mode, modes (sorted), backend and the SHA-256 of the
    bundle or of the credential.
    """
    modes = ",".join(sorted(mode.value for mode in request.modes))
    digest = hashlib.sha256()
    for part in (
        str(request.tenant.company).encode(),
        str(request.tenant.mode).encode(),
        modes.encode(),
        backend.value.encode(),
        source_sha256,
    ):
        digest.update(len(part).to_bytes(_LENGTH_BYTES, "big"))  # no part can run into the next
        digest.update(part)
    return digest.digest()


@final
class Importing:
    """Certificates installed for a company: a bundle's key in custody, or an entity's credential."""

    __slots__ = ("_clock", "_custody", "_rules", "_units")

    def __init__(self, *, units: SignerUnits, custody: Custody, rules: SignerRules, clock: Clock) -> None:
        self._units = units
        self._custody = custody
        self._rules = rules
        self._clock = clock

    async def local(self, request: ImportRequest, bundle: bytes, password: Secret) -> ImportOutcome:
        """Install the certificate of a PKCS#12 ``bundle`` opened with ``password`` (see the module)."""

        async def admitted(seal_for: SealContext) -> Admitted:
            return await self._custody.admit_local(bundle, password, seal_for=seal_for)

        return await self._import(request, BackendKind.LOCAL, hashlib.sha256(bundle).digest(), admitted)

    async def remote(self, request: ImportRequest, credential: Secret) -> ImportOutcome:
        """Install the certificate an entity signs with for ``credential`` (see the module)."""

        async def admitted(seal_for: SealContext) -> Admitted:
            return await self._custody.admit_remote(credential, seal_for=seal_for)

        source = hashlib.sha256(credential.reveal().encode()).digest()
        return await self._import(request, BackendKind.REMOTE, source, admitted)

    async def _import(
        self,
        request: ImportRequest,
        backend: BackendKind,
        source_sha256: bytes,
        admitted: Callable[[SealContext], Awaitable[Admitted]],
    ) -> ImportOutcome:
        if backend not in self._rules.backends:
            raise BackendNotOfferedError(backend)
        if not is_idempotency_key(request.idempotency_key):
            raise InvalidIdempotencyKeyError
        claim = ImportClaim(
            principal=current_actor().name,
            key=request.idempotency_key,
            request_hash=fingerprint_of(request, backend, source_sha256),
        )
        tenant = request.tenant
        async with self._units.unit(tenant) as unit:
            replayed = await unit.import_keys.answered(claim)
        if replayed is not None:
            return replayed
        certificate = CertificateId(new_id())
        seal_for = SealContext(
            organization=tenant.organization, company=tenant.company, certificate=certificate
        )
        custody = await admitted(seal_for)
        admit(custody.facts, now=self._clock(), skew=self._rules.skew)
        try:
            return await self._install(request, claim, certificate, backend, custody)
        except ActiveCertificateTakenError:
            return await self._install(request, claim, certificate, backend, custody)

    async def _install(
        self,
        request: ImportRequest,
        claim: ImportClaim,
        certificate: CertificateId,
        backend: BackendKind,
        custody: Admitted,
    ) -> ImportOutcome:
        tenant = request.tenant
        async with self._units.unit(tenant) as unit:

            async def work() -> CertificateView:
                return await self._installed(unit, request, certificate, backend, custody)

            return await unit.import_keys.once(claim, work)

    async def _installed(
        self,
        unit: SignerUnit,
        request: ImportRequest,
        certificate: CertificateId,
        backend: BackendKind,
        custody: Admitted,
    ) -> CertificateView:
        tenant = request.tenant
        owner = Tenant(tenant.organization, tenant.company, None)
        facts = custody.facts
        new = Certificate(
            id=certificate,
            tenant=owner,
            modes=request.modes,
            backend=backend,
            status=CertificateStatus.ACTIVE,
            der=facts.der,
            fingerprint_sha256=facts.fingerprint_sha256,
            serial_number=facts.serial_number,
            subject=facts.subject,
            issuer=facts.issuer,
            not_before=facts.not_before,
            not_after=facts.not_after,
            installed_at=unit.now,
            retired_at=None,
            retired_reason=None,
            version=1,
        )
        active = await unit.certificates.active_of_company(tenant.company, for_update=True)
        installation = install(new, active, at=unit.now)
        for changed in installation.changed:  # first: the modes they lose are free for the new one
            await unit.certificates.save(changed)
        await unit.certificates.add(new, custody.material)
        replaces = installation.replaced
        unit.audit.record(
            "certificate.install",
            new.id,
            after=state_of(new),
            detail={
                "backend": backend.value,
                "modes": ",".join(mode.value for mode in modes_of(new)),
                "fingerprint_sha256": new.fingerprint_sha256.hex(),
                "replaces": ",".join(str(replaced) for replaced in replaces),
            },
        )
        unit.events.publish(
            CertificateInstalled(
                scope=owner,
                subject=new.id,
                aggregate_version=new.version,
                certificate=new.id,
                modes=modes_of(new),
                backend=backend.value,
                not_after=new.not_after,
                replaces=replaces,
            )
        )
        for retired in installation.retired:
            unit.events.publish(retired_event(retired))
        plan_notices(unit, new, days=self._rules.notice_days)
        return view_of(new)
