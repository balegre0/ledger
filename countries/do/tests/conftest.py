"""Fixtures of the DO pack's tests: one test hierarchy and one signer per session (RSA keys are slow)."""

from datetime import UTC, datetime
lazy from collections.abc import AsyncIterator

import httpx
import pytest

from ledger_country_do.channels.ecf_v1.adapters.dgii.client import DgiiClient
from ledger_country_do_testing.fake_dgii.app import FakeDgii
from ledger_country_do_testing.fake_dgii.state import dgii_check
from ledger_country_do_testing.local_signer import KeyPairSigner
from ledger_country_do_testing.pki import organization, person
from ledger_country_do_testing.samples import SIGNER_VALID_FROM
from ledger_country_do_testing.transports import FAKE_HOSTS
from ledger_testing.clock import ManualClock
from ledger_testing.pki import Authority
lazy from ledger_testing.pki import Issued


@pytest.fixture(scope="session")
def authority() -> Authority:
    return Authority.root(now=SIGNER_VALID_FROM)


@pytest.fixture(scope="session")
def intermediate(authority: Authority) -> Authority:
    return authority.intermediate()


@pytest.fixture(scope="session")
def issued(intermediate: Authority) -> Issued:
    return intermediate.issue(person(), not_before=SIGNER_VALID_FROM)


@pytest.fixture(scope="session")
def signer(issued: Issued) -> KeyPairSigner:
    return KeyPairSigner.from_issued(issued)


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock(datetime(2026, 10, 13, 13, 30, tzinfo=UTC))


@pytest.fixture
def fake(authority: Authority, issued: Issued, clock: ManualClock) -> FakeDgii:
    receiver = authority.issue(
        organization("131098193", name="CONSTRUCTORA DEL CARIBE SRL"), not_before=SIGNER_VALID_FROM
    )
    return FakeDgii(
        check=dgii_check(trusted=[issued.certificate]),
        receiver=receiver,
        clock=clock,
        public_url=FAKE_HOSTS.ecf,
    )


@pytest.fixture
def sleeps() -> list[float]:
    return []


@pytest.fixture
async def dgii(
    signer: KeyPairSigner, fake: FakeDgii, clock: ManualClock, sleeps: list[float]
) -> AsyncIterator[DgiiClient]:
    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    client = DgiiClient(
        signer, hosts=FAKE_HOSTS, clock=clock, sleep=sleep, transport=httpx.ASGITransport(app=fake.app)
    )
    async with client.opened():
        yield client
