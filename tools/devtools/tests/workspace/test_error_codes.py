"""Every error code means one thing: no two error classes of the workspace declare the same code.

A client matches on ``code`` (§9.6); two classes declaring one code would make one code mean two failures. A
subclass that declares no code of its own shares its base's on purpose and is not counted. Every exception
class of a first-party module counts, ``LedgerError`` or not.
"""

import importlib
import pkgutil
from collections import defaultdict
from pathlib import Path
from typing import ClassVar, Final

from ledger_devtools.workspace import Workspace
from ledger_kernel.errors import LedgerError

ROOT: Final = Path(__file__).resolve().parents[4]
"""The repository: its manifests name the workspace's packages."""

ENTRY_POINTS: Final = frozenset({"env"})
"""Alembic's migration environments: importing one runs migrations."""


def imported(package: str) -> None:
    """Import ``package`` and every module below it, but Alembic's environments."""
    root = importlib.import_module(package)
    for found in pkgutil.walk_packages(root.__path__, prefix=f"{package}."):
        if found.name.rpartition(".")[2] not in ENTRY_POINTS:
            importlib.import_module(found.name)


def subclasses(base: type[BaseException]) -> set[type[BaseException]]:
    found: set[type[BaseException]] = set()
    for child in base.__subclasses__():
        found |= {child, *subclasses(child)}
    return found


def declared_codes(classes: set[type[BaseException]], packages: tuple[str, ...]) -> dict[str, list[str]]:
    """Each code a class of ``packages`` declares in its own body, with the classes that declare it.

    Classes of other modules (the tests' own, the standard library's) are left out.
    """
    codes: dict[str, list[str]] = defaultdict(list)
    for error in classes:
        if error.__module__.partition(".")[0] not in packages:
            continue
        match vars(error):
            case {"code": str() as code}:
                codes[code].append(f"{error.__module__}.{error.__qualname__}")
            case _:
                pass
    return codes


def first_party() -> tuple[str, ...]:
    """The workspace's import packages, as the manifests name them."""
    return tuple(package.import_name for package in Workspace.load(ROOT).packages)


def test_no_two_error_classes_of_the_workspace_declare_one_code() -> None:
    packages = first_party()
    for package in packages:
        imported(package)
    codes = declared_codes(subclasses(BaseException), packages)
    twice = {code: sorted(owners) for code, owners in codes.items() if len(owners) > 1}
    assert twice == {}


def test_a_code_two_classes_declare_is_named() -> None:
    class FirstError(LedgerError):
        code: ClassVar[str] = "twice"

    class SecondError(LedgerError):
        code: ClassVar[str] = "twice"

    class InheritedError(FirstError):
        pass

    FirstError.__module__ = SecondError.__module__ = InheritedError.__module__ = "ledger_example"
    codes = declared_codes({FirstError, SecondError, InheritedError}, ("ledger_example",))
    assert sorted(codes["twice"]) == [
        "ledger_example.test_a_code_two_classes_declare_is_named.<locals>.FirstError",
        "ledger_example.test_a_code_two_classes_declare_is_named.<locals>.SecondError",
    ]


def test_a_code_an_error_outside_the_ledger_hierarchy_shares_is_named() -> None:
    class PackError(Exception):
        code: ClassVar[str] = "twice"

    class KernelError(LedgerError):
        code: ClassVar[str] = "twice"

    PackError.__module__ = KernelError.__module__ = "ledger_pack"
    codes = declared_codes(subclasses(BaseException), ("ledger_pack",))
    assert sorted(name.rpartition(".")[2] for name in codes["twice"]) == ["KernelError", "PackError"]
