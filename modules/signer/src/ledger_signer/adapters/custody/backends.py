"""``Custody``: the local and the remote backends behind the one port the use cases call.

A certificate says which backend holds its key; this dispatches on it. The remote backend exists only when
the deployment has a certification entity: without one, admitting or signing for it is
``EntityNotConfiguredError`` (the use case refuses an import for a backend the deployment does not offer
before it gets here; the signer's assembly refuses to start offering ``remote`` without an entity).
"""

from typing import ClassVar, Literal, assert_type, final, override

from ledger_kernel.errors import LedgerError
from ledger_signer.adapters.custody.local import LocalBackend
from ledger_signer.adapters.custody.remote import EntityAnswerInvalidError, RemoteBackend
from ledger_signer.application.ports import Custody
from ledger_signer.domain.certificates import BackendKind
lazy from ledger_fiscal_kit.xml.dsig import Prepared
lazy from ledger_signer.application.ports import (
    Admitted,
    CertificationEntity,
    KeyManagement,
    SealContext,
    SealedMaterial,
    Secret,
)

__all__ = ["Backends", "EntityAnswerInvalidError", "EntityNotConfiguredError"]


class EntityNotConfiguredError(LedgerError):
    """The remote backend asked for where the deployment has no certification entity: a defect."""

    code: ClassVar[str] = "entity_not_configured"

    def __init__(self) -> None:
        super().__init__("this deployment has no certification entity for the remote backend")


@final
class Backends(Custody):
    """Custody over the KMS and, when the deployment has one, a certification entity."""

    __slots__ = ("_local", "_remote")

    def __init__(self, kms: KeyManagement, *, entity: CertificationEntity | None) -> None:
        self._local = LocalBackend(kms)
        self._remote = None if entity is None else RemoteBackend(kms, entity)

    def _remote_backend(self) -> RemoteBackend:
        if self._remote is None:
            raise EntityNotConfiguredError
        return self._remote

    @override
    async def admit_local(self, bundle: bytes, password: Secret, *, seal_for: SealContext) -> Admitted:
        return await self._local.admit(bundle, password, seal_for=seal_for)

    @override
    async def admit_remote(self, credential: Secret, *, seal_for: SealContext) -> Admitted:
        return await self._remote_backend().admit(credential, seal_for=seal_for)

    @override
    async def sign(
        self,
        material: SealedMaterial,
        *,
        backend: BackendKind,
        prepared: Prepared,
        certificate_der: bytes,
        seal_for: SealContext,
    ) -> bytes:
        signing: LocalBackend | RemoteBackend
        match backend:
            case BackendKind.LOCAL:
                signing = self._local
            case _:
                assert_type(backend, Literal[BackendKind.REMOTE])
                signing = self._remote_backend()
        return await signing.sign(
            material, prepared=prepared, certificate_der=certificate_der, seal_for=seal_for
        )
