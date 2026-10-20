"""do-ecf-v1's domain loads nothing beyond the kernel, regime do and fiscal-kit's value types.

Import-linter reads import statements; this imports each module of the domain in a fresh interpreter
with every lazy import made eager, and names whatever it loads beyond the standard library: what the
channel's domain would bring into a process once used.
"""

import pkgutil
import subprocess
import sys
from typing import Final

import pytest

from ledger_country_do.channels.ecf_v1 import domain

ALLOWED: Final = frozenset({"ledger_country_do", "ledger_fiscal_kit", "ledger_kernel"})
FORBIDDEN_SEEN: Final = frozenset(
    {"fastapi", "sqlalchemy", "psycopg", "pydantic", "structlog", "httpx", "lxml"}
)
_PROBE: Final = """
import sys
sys.set_lazy_imports_filter(lambda *_: False)
before = set(sys.modules)
import {module}
loaded = {{name.split(".")[0] for name in set(sys.modules) - before}}
print(" ".join(sorted(loaded - set(sys.stdlib_module_names))))
"""
MODULES: Final = sorted(f"{domain.__name__}.{each.name}" for each in pkgutil.iter_modules(domain.__path__))


def loaded_beyond_the_standard_library(module: str) -> set[str]:
    """The top-level packages beyond the standard library that importing ``module`` loads."""
    found = subprocess.run(
        [sys.executable, "-c", _PROBE.format(module=module)], capture_output=True, text=True, check=True
    )
    return set(found.stdout.split())


@pytest.mark.parametrize("module", MODULES)
def test_a_module_of_the_domain_loads_no_third_party_and_no_platform(module: str) -> None:
    assert loaded_beyond_the_standard_library(module) <= ALLOWED


def test_the_probe_sees_the_third_parties_an_adapter_loads() -> None:
    assert "mapping" in {module.rsplit(".", 1)[1] for module in MODULES}
    loaded = loaded_beyond_the_standard_library("ledger_country_do.channels.ecf_v1.adapters.dgii.client")
    assert "httpx" in loaded & FORBIDDEN_SEEN
