"""signer's tests: the worker's database migrated as production migrates it; signer's schema its login's.

signer is the module under test of ``ledger_testing``'s module fixtures; the template holds the platform's
schema, identity's (the module its manifest depends on, for its events) and signer's, each brought to head by
its revisions. signer is isolated in the signer's role: its schema is ``ledger_signer``'s alone, so
``database`` and ``module_database`` log in as ``ledger_signer``, and ``app_database`` is the login every
other app uses, which must reach none of it. What is signer's own is here: who its use cases act as.
"""

from typing import Final
lazy from collections.abc import Iterator

import pytest

from ledger_platform.data.audit import Actor, acting_as
from ledger_signer._manifest import MANIFEST
from ledger_signer.adapters.postgres.schema import MIGRATIONS
from ledger_testing.modules import ModuleUnderTest
lazy from ledger_testing.postgres import Template

UNDER_TEST: Final = ModuleUnderTest(MANIFEST, MIGRATIONS)
TEST_ACTOR: Final = Actor("system:test", "signer-tests")
"""Who the use cases a test calls itself act as; a request through the app acts as its caller instead."""


def pytest_ledger_template(template: Template) -> None:
    """The roles and the schemas ``platform``, ``identity`` and ``signer``, each at head, relay granted."""
    UNDER_TEST.prepare(template)


@pytest.fixture
def module_under_test() -> ModuleUnderTest:
    """signer, for the module fixtures of ``ledger_testing``."""
    return UNDER_TEST


@pytest.fixture(autouse=True)
def acting() -> Iterator[None]:
    """Every test acts as ``TEST_ACTOR``: what its own calls of the use cases audit needs an actor."""
    with acting_as(TEST_ACTOR):
        yield
