"""Fixtures of the testing distribution's tests: a test hierarchy, a signer, a fake DGII over ASGI."""

from datetime import UTC, datetime
lazy from collections.abc import AsyncIterator

import httpx
import pytest

from ledger_country_do_testing.clock import ManualClock
from ledger_country_do_testing.fake_dgii.app import DEFAULT_PUBLIC_URL, FakeDgii
from ledger_country_do_testing.fake_dgii.state import dgii_check
from ledger_country_do_testing.local_signer import KeyPairSigner
from ledger_country_do_testing.pki import Authority, organization, person
from ledger_country_do_testing.samples import SIGNER_VALID_FROM
lazy from ledger_country_do_testing.pki import Issued


@pytest.fixture(scope="session")
def authority() -> Authority:
    return Authority.root(now=SIGNER_VALID_FROM)


@pytest.fixture(scope="session")
def issued(authority: Authority) -> Issued:
    return authority.issue(person(), not_before=SIGNER_VALID_FROM)


@pytest.fixture(scope="session")
def receiver_identity(authority: Authority) -> Issued:
    return authority.issue(
        organization("131098193", name="CONSTRUCTORA DEL CARIBE SRL"), not_before=SIGNER_VALID_FROM
    )


@pytest.fixture(scope="session")
def signer(issued: Issued) -> KeyPairSigner:
    return KeyPairSigner.from_issued(issued)


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock(datetime(2026, 10, 13, 13, 30, tzinfo=UTC))


@pytest.fixture
def fake(issued: Issued, receiver_identity: Issued, clock: ManualClock) -> FakeDgii:
    return FakeDgii(check=dgii_check(trusted=[issued.certificate]), receiver=receiver_identity, clock=clock)


@pytest.fixture
async def http(fake: FakeDgii) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fake.app), base_url=DEFAULT_PUBLIC_URL
    ) as client:
        yield client
