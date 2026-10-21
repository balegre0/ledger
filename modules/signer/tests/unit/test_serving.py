"""The signer's role put together: its rules, its KMS's refusals, and what its handlers refuse to read.

The handlers' work (a deactivation retiring, a notice announcing) is the integration suite's; here, what is a
defect of whoever enqueued a notice: a scope that is no company's, a payload that is not a notice's.
"""

from datetime import timedelta
from typing import Final
from uuid import uuid7
lazy from collections.abc import Mapping
lazy from pathlib import Path

import pytest

from ledger_kernel.errors import ErrorKind
from ledger_kernel.ids import CompanyId, OrganizationId, new_id
from ledger_kernel.tenancy import Mode, OrganizationScope, Tenant
from ledger_platform.jobs.outcomes import Done, Job
from ledger_signer.adapters.custody.kms_local import KmsLocalRefusedError
from ledger_signer.adapters.serving import (
    SignerPortRefusedError,
    SignerWorkUnreadableError,
    expiry_notices,
    grpc_port,
    local_kms,
    rules_of,
)
from ledger_signer.adapters.settings import SignerSettings
from ledger_signer.application.notices import EXPIRY_NOTICE, Notices
from ledger_signer.application.ports import SignerRules
from ledger_signer.domain.certificates import BackendKind
lazy from ledger_kernel.tenancy import Scope
lazy from ledger_platform.config.settings import Environment
lazy from ledger_platform.data.codec import JsonValue
lazy from ledger_signer.application.ports import SignerUnits

ORGANIZATION: Final = OrganizationId(new_id())
COMPANY: Final = Tenant(ORGANIZATION, CompanyId(new_id()), Mode.TEST)
REAL_KEYS: Final[tuple[Environment, ...]] = ("staging", "production")


def job(scope: Scope | None, payload: Mapping[str, JsonValue]) -> Job:
    return Job(
        kind=EXPIRY_NOTICE,
        key="k:7",
        scope=scope,
        payload=payload,
        attempts=1,
        max_attempts=20,
        deadline_at=None,
        lease_token=uuid7(),
        traceparent=None,
    )


def test_the_rules_are_the_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEDGER_SIGNER_BACKENDS", "local,remote")
    monkeypatch.setenv("LEDGER_SIGNER_SIGNING_SKEW_SECONDS", "600")
    monkeypatch.setenv("LEDGER_SIGNER_SIGNING_CONCURRENCY", "8")
    monkeypatch.setenv("LEDGER_SIGNER_IMPORT_CONCURRENCY", "3")
    monkeypatch.setenv("LEDGER_SIGNER_EXPIRY_NOTICE_DAYS", "14,1")
    assert rules_of(SignerSettings()) == SignerRules(
        backends=frozenset(BackendKind),
        skew=timedelta(seconds=600),
        concurrency=8,
        import_concurrency=3,
        notice_days=(14, 1),
    )


@pytest.mark.parametrize("environment", REAL_KEYS)
def test_a_key_in_a_file_is_refused_where_keys_are_real(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, environment: Environment
) -> None:
    monkeypatch.setenv("LEDGER_SIGNER_KMS_KEY_FILE", str(tmp_path / "kms.key"))
    with pytest.raises(KmsLocalRefusedError):
        local_kms(SignerSettings(), environment)
    assert not (tmp_path / "kms.key").exists()


def test_a_key_file_is_made_on_a_laptop(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LEDGER_SIGNER_KMS_KEY_FILE", str(tmp_path / "kms.key"))
    kms = local_kms(SignerSettings(), "local")
    assert kms.key_id.startswith("local:")
    assert (tmp_path / "kms.key").exists()


@pytest.mark.parametrize(
    "scope",
    [None, OrganizationScope(ORGANIZATION, Mode.TEST)],
    ids=["nobody's", "an organization's"],
)
async def test_a_notice_of_no_company_is_never_read(scope: Scope | None, units: SignerUnits) -> None:
    notify = expiry_notices(Notices(units))
    with pytest.raises(SignerWorkUnreadableError, match=r"^a signer.expiry_notice the signer cannot read$"):
        await notify(job(scope, {"certificate": str(new_id()), "threshold_days": 7}))


@pytest.mark.parametrize(
    "payload",
    [{}, {"certificate": "not-a-uuid", "threshold_days": 7}, {"certificate": str(new_id())}],
    ids=["empty", "no uuid", "no days"],
)
async def test_a_notice_without_its_certificate_and_days_is_never_read(
    payload: Mapping[str, JsonValue], units: SignerUnits
) -> None:
    notify = expiry_notices(Notices(units))
    with pytest.raises(SignerWorkUnreadableError) as caught:
        await notify(job(COMPANY, payload))
    assert caught.value.kind is ErrorKind.INVALID  # the runner makes the job dead at once: no retry reads it


async def test_a_notice_of_an_unknown_certificate_is_done_and_announces_nothing(units: SignerUnits) -> None:
    notify = expiry_notices(Notices(units))
    assert await notify(job(COMPANY, {"certificate": str(new_id()), "threshold_days": 7})) == Done()


@pytest.mark.parametrize("environment", REAL_KEYS)
def test_where_keys_are_real_the_kms_is_refused_before_its_key_file_is_looked_for(
    environment: Environment,
) -> None:
    with pytest.raises(KmsLocalRefusedError):
        local_kms(SignerSettings(), environment)


@pytest.mark.parametrize("environment", REAL_KEYS)
def test_any_free_port_is_refused_where_keys_are_real(
    monkeypatch: pytest.MonkeyPatch, environment: Environment
) -> None:
    monkeypatch.setenv("LEDGER_SIGNER_GRPC_PORT", "0")
    with pytest.raises(SignerPortRefusedError):
        grpc_port(SignerSettings(), environment)
    monkeypatch.setenv("LEDGER_SIGNER_GRPC_PORT", "50051")
    assert grpc_port(SignerSettings(), environment) == 50051


@pytest.mark.parametrize("environment", ["local", "test"])
def test_any_free_port_is_taken_on_a_laptop_and_in_the_tests(
    monkeypatch: pytest.MonkeyPatch, environment: Environment
) -> None:
    monkeypatch.setenv("LEDGER_SIGNER_GRPC_PORT", "0")
    assert grpc_port(SignerSettings(), environment) == 0
