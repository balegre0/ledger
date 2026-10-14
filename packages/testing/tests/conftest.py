"""The testing package's own tests: the platform is the module under test of the module fixtures' tests.

Its schema is in the template; the other tests make the databases they need.
"""

from typing import Final

import pytest

from ledger_platform._manifest import MANIFEST
from ledger_platform.data.migrations import platform_migrations
from ledger_testing.modules import ModuleUnderTest
lazy from ledger_testing.postgres import Template

UNDER_TEST: Final = ModuleUnderTest(MANIFEST, platform_migrations())


def pytest_ledger_template(template: Template) -> None:
    """The platform's schema, at head."""
    UNDER_TEST.prepare(template)


@pytest.fixture
def module_under_test() -> ModuleUnderTest:
    """The platform, as a module's suite names its own."""
    return UNDER_TEST
