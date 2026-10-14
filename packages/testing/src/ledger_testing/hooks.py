"""The hook packages implement in their ``conftest.py`` to prepare the template database (§14.2)."""

from abc import ABC, abstractmethod

import pytest

lazy from ledger_testing.postgres import Template


class LedgerHooks(ABC):
    """The ledger plugin's hook specifications (``pytest_addhooks`` declares them)."""

    @pytest.hookspec
    @abstractmethod
    def pytest_ledger_template(self, template: Template) -> None:
        """Prepare ``template`` once per server: roles, schemas, migrations. Every implementation runs."""
