"""invoicing's tests: the worker's database migrated as production migrates it, invoicing served by ``api``.

invoicing is the module under test of ``ledger_testing``'s module fixtures; the template holds the platform's
schema, identity's (the module it depends on: the app that serves invoicing assembles it, and the company a
document belongs to is identity's) and invoicing's, each brought to head by its revisions. Tests reach
invoicing's schema the way the apps do: as ``ledger_app``, through ``module_database``, a ``BoundDatabase`` in
``invoicing_rw``. What is invoicing's own is here: who its use cases act as, its principals and a company.
"""

from typing import Final
lazy from collections.abc import Iterator

import pytest

from ledger_identity.adapters.postgres.schema import MIGRATIONS as IDENTITY
from ledger_invoicing._manifest import MANIFEST
from ledger_invoicing.adapters.postgres.schema import MIGRATIONS
from ledger_platform.data.audit import Actor, acting_as
from ledger_testing.modules import ModuleUnderTest
lazy from ledger_testing.postgres import Template

UNDER_TEST: Final = ModuleUnderTest(MANIFEST, MIGRATIONS, needs=(IDENTITY,))
TEST_ACTOR: Final = Actor("system:test", "invoicing-tests")
"""Who the use cases a test calls itself act as; a request through the app acts as its caller instead."""


def pytest_ledger_template(template: Template) -> None:
    """The roles and the schemas ``platform``, ``identity`` and ``invoicing``, each at head, relay granted."""
    UNDER_TEST.prepare(template)


@pytest.fixture
def module_under_test() -> ModuleUnderTest:
    """invoicing, for the module fixtures of ``ledger_testing``."""
    return UNDER_TEST


@pytest.fixture(autouse=True)
def acting() -> Iterator[None]:
    """Every test acts as ``TEST_ACTOR``: what its own calls of the use cases audit needs an actor."""
    with acting_as(TEST_ACTOR):
        yield
