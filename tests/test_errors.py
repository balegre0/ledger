"""Every error code means one thing: no two error classes of the workspace declare the same code.

A client matches on ``code`` (§9.6); two classes declaring one code would make one code mean two failures. A
subclass that declares no code of its own shares its base's on purpose and is not counted.
"""

import importlib
import pkgutil
import tomllib
from collections import defaultdict
from pathlib import Path
from typing import ClassVar, Final

from pydantic import TypeAdapter

from ledger_kernel.errors import LedgerError

ROOT: Final = Path(__file__).resolve().parent.parent

ENTRY_POINTS: Final = frozenset({"env"})
"""Alembic's migration environments: importing one runs migrations."""


def imported(package: str) -> None:
    """Import ``package`` and every module below it, but Alembic's environments."""
    root = importlib.import_module(package)
    for found in pkgutil.walk_packages(root.__path__, prefix=f"{package}."):
        if found.name.rpartition(".")[2] not in ENTRY_POINTS:
            importlib.import_module(found.name)


def subclasses(base: type[LedgerError]) -> set[type[LedgerError]]:
    found: set[type[LedgerError]] = set()
    for child in base.__subclasses__():
        found |= {child, *subclasses(child)}
    return found


def declared_codes(classes: set[type[LedgerError]], packages: tuple[str, ...]) -> dict[str, list[str]]:
    """Each code a class of ``packages`` declares in its own body, with the classes that declare it.

    Classes of other modules (the tests' own) are left out.
    """
    codes: dict[str, list[str]] = defaultdict(list)
    for error in classes:
        if "code" in vars(error) and error.__module__.partition(".")[0] in packages:
            codes[error.code].append(f"{error.__module__}.{error.__qualname__}")
    return codes


def first_party() -> tuple[str, ...]:
    """The workspace's import packages: those coverage measures."""
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return tuple(_SOURCE_PKGS.validate_python(config["tool"]["coverage"]["run"]["source_pkgs"]))


_SOURCE_PKGS: Final = TypeAdapter(list[str])


SHARED_UNTIL_THE_VOCABULARY_CUTOVER: Final = {
    "company_not_found": [
        "ledger_identity.domain.errors.CompanyNotFoundError",
        "ledger_platform.auth.access.NoCompanyAccessError",
    ],
    "invalid_tax_id": [
        "ledger_kernel.countries.do.InvalidTaxIdError",
        "ledger_kernel.tax_ids.InvalidTaxIdError",
    ],
    "permission_denied": [
        "ledger_identity.domain.errors.ActorNotAllowedError",
        "ledger_platform.auth.access.PermissionDeniedError",
    ],
}
"""The codes two classes still declare, each on purpose, until one class per code replaces them.

The kernel's ``PermissionDeniedError`` and ``CompanyNotFoundError`` take the place of identity's and the
platform's, and the kernel's Dominican tax ids move to ``countries/do`` (the plan's vocabulary cutover).
Exact: a pair that goes away must leave this list, and no new pair may join it.
"""


def test_no_two_error_classes_of_the_workspace_declare_one_code() -> None:
    packages = first_party()
    for package in packages:
        imported(package)
    codes = declared_codes(subclasses(LedgerError) | {LedgerError}, packages)
    twice = {code: sorted(owners) for code, owners in codes.items() if len(owners) > 1}
    assert twice == SHARED_UNTIL_THE_VOCABULARY_CUTOVER


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
