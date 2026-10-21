"""The kit's value types a channel's pure layers import: the kernel and the standard library, nothing else.

Each module is imported in a fresh interpreter with every lazy import made eager, so what it would load once
used is loaded now; whatever it brings beyond the standard library is named.
"""

import subprocess
import sys
from typing import Final

import pytest

ALLOWED: Final = frozenset({"ledger_fiscal_kit", "ledger_kernel"})
_PROBE: Final = """
import sys
sys.set_lazy_imports_filter(lambda *_: False)
before = set(sys.modules)
import {module}
loaded = {{name.split(".")[0] for name in set(sys.modules) - before}}
print(" ".join(sorted(loaded - set(sys.stdlib_module_names))))
"""


def loaded_beyond_the_standard_library(module: str) -> set[str]:
    """The top-level packages beyond the standard library that importing ``module`` loads."""
    found = subprocess.run(
        [sys.executable, "-c", _PROBE.format(module=module)], capture_output=True, text=True, check=True
    )
    return set(found.stdout.split())


@pytest.mark.parametrize("module", ["status", "objections", "drafts"])
def test_a_pure_module_of_the_kit_loads_the_kernel_and_the_standard_library_only(module: str) -> None:
    assert loaded_beyond_the_standard_library(f"ledger_fiscal_kit.{module}") <= ALLOWED


def test_the_probe_sees_what_an_impure_module_loads() -> None:
    assert "ledger_platform" in loaded_beyond_the_standard_library("ledger_fiscal_kit.channels")
