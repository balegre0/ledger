"""Fixtures of the DO pack's tests: one test hierarchy and one signer per session (RSA keys are slow)."""

import pytest

from ledger_country_do_testing.local_signer import KeyPairSigner
from ledger_country_do_testing.pki import Authority, person
lazy from ledger_country_do_testing.pki import Issued


@pytest.fixture(scope="session")
def authority() -> Authority:
    return Authority.root()


@pytest.fixture(scope="session")
def intermediate(authority: Authority) -> Authority:
    return authority.intermediate()


@pytest.fixture(scope="session")
def issued(intermediate: Authority) -> Issued:
    return intermediate.issue(person())


@pytest.fixture(scope="session")
def signer(issued: Issued) -> KeyPairSigner:
    return KeyPairSigner.from_issued(issued)
