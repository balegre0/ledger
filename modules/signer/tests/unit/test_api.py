"""signer's public surface: its DTOs, its API's key and the errors that cross it."""

import dataclasses
from datetime import UTC, datetime, timedelta
from typing import Final

import pytest

from ledger_kernel.errors import ErrorKind
from ledger_kernel.ids import CompanyId, OrganizationId, new_id
from ledger_kernel.tenancy import Mode
from ledger_signer.adapters.unavailable import UnavailableSigner
from ledger_signer.api import (
    SIGNER_API,
    BackendKind,
    BackendNotOfferedError,
    CertificateId,
    CertificateNotFoundError,
    CertificateStatus,
    CertificateView,
    is_signer_api,
)

NOW: Final = datetime(2026, 10, 19, 12, tzinfo=UTC)


def a_view() -> CertificateView:
    return CertificateView(
        id=CertificateId(new_id()),
        organization=OrganizationId(new_id()),
        company=CompanyId(new_id()),
        modes=frozenset({Mode.TEST, Mode.LIVE}),
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


@pytest.mark.parametrize("field", [field.name for field in dataclasses.fields(CertificateView)])
def test_a_certificate_view_is_frozen(field: str) -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(a_view(), field, None)


def test_backends_and_statuses_are_their_words() -> None:
    assert [str(kind) for kind in BackendKind] == ["local", "remote"]
    assert [str(status) for status in CertificateStatus] == ["active", "retired"]
    assert CertificateStatus("retired") is CertificateStatus.RETIRED


def test_a_view_holds_the_public_part_of_a_certificate_and_no_secret() -> None:
    view = a_view()
    assert (view.modes, view.fingerprint_sha256, view.installed_at) == (
        frozenset({Mode.TEST, Mode.LIVE}),
        bytes(32),
        NOW,
    )
    assert (view.retired_at, view.retired_reason) == (None, None)
    assert {field.name for field in dataclasses.fields(CertificateView)}.isdisjoint(
        {"key", "private_key", "credential", "sealed", "password"}
    )


def test_is_signer_api_recognizes_an_implementation_and_refuses_another_object() -> None:
    assert is_signer_api(UnavailableSigner())
    assert not is_signer_api(object())
    assert SIGNER_API.name == "signer"
    stand_in = UnavailableSigner()
    assert SIGNER_API.take(stand_in) is stand_in


def test_the_errors_that_cross_the_api_have_stable_codes_and_name_no_value() -> None:
    missing = CertificateNotFoundError()
    refused = BackendNotOfferedError(BackendKind.REMOTE)
    assert (missing.kind, missing.code, str(missing)) == (
        ErrorKind.NOT_FOUND,
        "certificate_not_found",
        "certificate was not found",
    )
    assert (refused.kind, refused.code, str(refused)) == (
        ErrorKind.INVALID,
        "backend_not_offered",
        "this deployment does not offer the remote backend",
    )
