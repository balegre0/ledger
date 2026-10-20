"""An import's answer as its idempotency key keeps it: the ``ImportCertificateResponse`` bytes (Decision 9).

What is stored reads back as the same view; bytes that hold an unspecified mode, backend or status are not an
answer the signer stored (a defect, never a guess).
"""

from datetime import UTC, datetime, timedelta
from typing import Final

import pytest

from ledger_kernel.errors import InvalidValueError
from ledger_kernel.ids import CompanyId, OrganizationId, new_id
from ledger_kernel.tenancy import Mode
from ledger_signer.adapters.grpc._generated.v1 import signer_pb2
from ledger_signer.adapters.postgres.import_keys import stored_answer, stored_view
from ledger_signer.api import CertificateView
from ledger_signer.domain.certificates import BackendKind, CertificateId, CertificateStatus

NOW: Final = datetime(2026, 10, 19, 12, 30, 15, 123456, tzinfo=UTC)
TEST: Final = frozenset({Mode.TEST})


def a_view(
    *,
    modes: frozenset[Mode] = TEST,
    backend: BackendKind = BackendKind.LOCAL,
    retired: bool = False,
) -> CertificateView:
    return CertificateView(
        id=CertificateId(new_id()),
        organization=OrganizationId(new_id()),
        company=CompanyId(new_id()),
        modes=modes,
        backend=backend,
        status=CertificateStatus.RETIRED if retired else CertificateStatus.ACTIVE,
        fingerprint_sha256=bytes(range(32)),
        serial_number="1f2e",
        subject="CN=SUBJECT-SENTINEL",
        issuer="CN=Authority",
        not_before=NOW - timedelta(days=1),
        not_after=NOW + timedelta(days=365),
        installed_at=NOW,
        retired_at=NOW + timedelta(hours=1) if retired else None,
        retired_reason="replaced" if retired else None,
        version=2 if retired else 1,
    )


@pytest.mark.parametrize(
    "view",
    [
        a_view(),
        a_view(modes=frozenset({Mode.LIVE}), backend=BackendKind.REMOTE),
        a_view(modes=frozenset(Mode), retired=True),
        a_view(modes=frozenset(), retired=True),
    ],
    ids=["active in test", "remote in live", "retired from both", "retired from none"],
)
def test_a_stored_answer_reads_back_as_its_view(view: CertificateView) -> None:
    body = stored_answer(view)
    answer = signer_pb2.ImportCertificateResponse.FromString(body)
    assert not answer.replayed
    assert answer.certificate.certificate_id == str(view.id)
    assert stored_view(body) == view


@pytest.mark.parametrize("field", ["mode", "backend", "status"])
def test_an_unspecified_value_is_no_stored_answer(field: str) -> None:
    answer = signer_pb2.ImportCertificateResponse.FromString(stored_answer(a_view()))
    certificate = answer.certificate
    match field:
        case "mode":
            certificate.modes.append(signer_pb2.MODE_UNSPECIFIED)
        case "backend":
            certificate.backend = signer_pb2.BACKEND_UNSPECIFIED
        case _:
            certificate.status = signer_pb2.CERTIFICATE_STATUS_UNSPECIFIED
    with pytest.raises(InvalidValueError):
        stored_view(answer.SerializeToString())
