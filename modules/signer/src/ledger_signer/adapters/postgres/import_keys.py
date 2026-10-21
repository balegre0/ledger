"""An import's idempotency key (plan Decision 9): the platform's ``Idempotency``, in signer's own schema.

The key is scoped as an HTTP route's is: the unit's organization and mode, the claim's principal (the actor
the api forwarded), the route ``grpc ImportCertificate`` and the key; the request is the claim's hash. What
it keeps is the answer as the gRPC service sends it, the ``ImportCertificateResponse`` bytes with status 201
(``codec.import_response``), read back on a replay by ``codec.import_outcome``: one mapping of the wire, its
bytes pinned by the codec's golden test. Keys are purged by signer's own job.
"""

from typing import Final, final, override
lazy from collections.abc import Awaitable, Callable

from ledger_platform.http.idempotency import Attempt, StoredResponse
from ledger_signer.adapters.grpc import codec
from ledger_signer.adapters.grpc._generated.v1 import signer_pb2
from ledger_signer.application.ports import ImportKeys, ImportOutcome
lazy from ledger_platform.data.unit_of_work import UnitOfWork
lazy from ledger_platform.http.idempotency import Idempotency
lazy from ledger_signer.api import CertificateView
lazy from ledger_signer.application.ports import ImportClaim

ROUTE: Final = "grpc ImportCertificate"
"""The route an import's key is scoped to: no HTTP route has a name of this shape."""
CREATED: Final = 201


def _stored(view: CertificateView) -> bytes:
    """``view`` as the ``ImportCertificateResponse`` an import's key keeps (``codec.import_response``)."""
    return codec.import_response(ImportOutcome(view=view, replayed=False)).SerializeToString()


def _replayed(body: bytes) -> ImportOutcome:
    """The answer ``_stored`` kept as ``body``, replayed (``codec.import_outcome``)."""
    stored = codec.import_outcome(signer_pb2.ImportCertificateResponse.FromString(body))
    return ImportOutcome(view=stored.view, replayed=True)


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
        return None if stored is None else _replayed(stored.body)

    @override
    async def once(self, claim: ImportClaim, work: Callable[[], Awaitable[CertificateView]]) -> ImportOutcome:
        installed: list[CertificateView] = []

        async def installing() -> StoredResponse:
            view = await work()
            installed.append(view)
            return StoredResponse(CREATED, _stored(view), {})

        outcome = await self._idempotency.once(self._unit, _attempt(claim), installing)
        if outcome.replayed:
            return _replayed(outcome.response.body)
        return ImportOutcome(view=installed[0], replayed=False)
