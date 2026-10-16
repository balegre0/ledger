"""identity's tests: the worker's database migrated as production migrates it, and identity served by ``api``.

identity is the module under test of ``ledger_testing``'s module fixtures: the template holds the platform's
schema and identity's, each brought to head by its revisions as ``ledger_migrator`` with the relay's grants
(what ``ledger-migrate`` runs; the migrate app itself is tested on fresh databases in ``test_migrations.py``).
Tests reach identity's schema the way the apps do: as ``ledger_app``, through ``module_database``, a
``BoundDatabase`` in ``identity_rw``. What is identity's own is here: who its use cases act as, its principals
and a registered company.
"""

from typing import Final
lazy from collections.abc import Iterator

import pytest
lazy import httpx

from ledger_identity._manifest import MANIFEST
from ledger_identity.adapters.postgres.schema import MIGRATIONS
from ledger_identity.domain.organization import ORGANIZATION_ADMIN
from ledger_platform.data.audit import Actor, acting_as
from ledger_testing.api import TestPrincipal, created
from ledger_testing.modules import ModuleUnderTest
lazy from ledger_testing.issuer import LocalIssuer
lazy from ledger_testing.postgres import Template

UNDER_TEST: Final = ModuleUnderTest(MANIFEST, MIGRATIONS)
TEST_ACTOR: Final = Actor("system:test", "identity-tests")
"""Who the use cases a test calls itself act as; a request through the app acts as its caller instead."""
COMPANY: Final = {
    "jurisdiction": {"country": "DO"},
    "functional_currency": "DOP",
    "tax_ids": [{"scheme": "do.rnc", "value": "101850043"}],
    "legal_name": "Duarte",
}
"""What the ``company`` fixture registers."""


def pytest_ledger_template(template: Template) -> None:
    """The roles and the schemas ``platform`` and ``identity``, each at head, the relay granted."""
    UNDER_TEST.prepare(template)


@pytest.fixture
def module_under_test() -> ModuleUnderTest:
    """identity, for the module fixtures of ``ledger_testing``."""
    return UNDER_TEST


@pytest.fixture(autouse=True)
def acting() -> Iterator[None]:
    """Every test acts as ``TEST_ACTOR``: what its own calls of the use cases audit needs an actor."""
    with acting_as(TEST_ACTOR):
        yield


@pytest.fixture
def owner(local_issuer: LocalIssuer) -> TestPrincipal:
    """Subject ``2001`` of the provider's organization ``1001``, its administrator (``ORGANIZATION_ADMIN``):
    it owns every company it registers."""
    return TestPrincipal(local_issuer, roles=(ORGANIZATION_ADMIN,))


@pytest.fixture
async def company(client: httpx.AsyncClient, owner: TestPrincipal) -> str:
    """The id of a company ``owner`` registered (``COMPANY``, with the key ``c-1``, in test mode)."""
    return await created(client, "/v1/companies", COMPANY, owner.posting("c-1"))
