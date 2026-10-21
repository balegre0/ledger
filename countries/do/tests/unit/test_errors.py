"""The DO pack's errors are the kernel's: one hierarchy, from the channel to the spike's command line."""

import importlib
import pkgutil
from datetime import date
from typing import Final

import pytest

from ledger_country_do.channels.ecf_v1.domain.errors import (
    EcfConflictError,
    EcfError,
    EcfInvalidError,
    EcfUnavailableError,
    RegimeDefectError,
    RegimeNotInForceError,
    from_regime,
)
from ledger_country_do.regime import REGIME
from ledger_country_do.regime.entries import EntryKind, EntryProblem, RegimeEntryError
from ledger_kernel.dated import NotInEffectError
from ledger_kernel.errors import (
    ConflictError,
    ErrorKind,
    InvalidInputError,
    LedgerError,
    UnavailableError,
)

PACKAGES: Final = ("ledger_country_do", "ledger_country_do_testing")
SETTINGS: Final = "ledger_country_do.channels.ecf_v1.settings"
ALEMBIC_ENVIRONMENT: Final = ".migrations.env"
"""A schema's migration environment: importing it runs the migrations."""


def declared() -> set[type[BaseException]]:
    """Every exception class a module of the pack (or of its dev-only package) declares."""
    for package in PACKAGES:
        root = importlib.import_module(package)
        for found in pkgutil.walk_packages(root.__path__, prefix=f"{package}."):
            if not found.name.endswith(ALEMBIC_ENVIRONMENT):  # Alembic's to run, never to import
                importlib.import_module(found.name)
    pending: list[type[BaseException]] = [BaseException]
    seen: set[type[BaseException]] = set()
    while pending:
        for child in pending.pop().__subclasses__():
            if child not in seen:
                seen.add(child)
                pending.append(child)
    return {error for error in seen if error.__module__.partition(".")[0] in PACKAGES}


def is_settings_refusal(error: type[BaseException]) -> bool:
    """A refusal of the channel's settings section: a ``ValueError`` pydantic turns into the section's
    ``ValidationError`` (naming the setting, never its value), as the platform's settings refuse theirs."""
    return error.__module__ == SETTINGS and issubclass(error, ValueError)


def test_every_error_of_the_pack_is_a_ledger_error() -> None:
    errors = declared()
    assert len(errors) > 50
    assert (
        sorted(
            f"{error.__module__}.{error.__qualname__}"
            for error in errors
            if not issubclass(error, LedgerError) and not is_settings_refusal(error)
        )
        == []
    )
    assert sum(is_settings_refusal(error) for error in errors) == 3


@pytest.mark.parametrize(
    ("error", "base", "kind", "code"),
    [
        (EcfError, LedgerError, ErrorKind.INTERNAL, "do_ecf_internal"),
        (EcfInvalidError, InvalidInputError, ErrorKind.INVALID, "do_ecf_invalid"),
        (EcfConflictError, ConflictError, ErrorKind.CONFLICT, "do_ecf_conflict"),
        (EcfUnavailableError, UnavailableError, ErrorKind.UNAVAILABLE, "do_ecf_unavailable"),
    ],
)
def test_the_channel_s_bases_are_the_kernel_s_kinds(
    error: type[EcfError], base: type[LedgerError], kind: ErrorKind, code: str
) -> None:
    assert issubclass(error, base)
    assert (error.kind, error.code) == (kind, code)


def test_a_value_not_in_effect_is_the_caller_s_and_a_missing_entry_the_pack_s() -> None:
    def not_in_effect() -> None:
        raise NotInEffectError(date(2018, 12, 31))

    def undeclared() -> None:
        raise RegimeEntryError(REGIME.key, EntryKind.PARAMETER, "x", EntryProblem.UNDECLARED)

    with pytest.raises(RegimeNotInForceError) as early:
        from_regime(not_in_effect)
    assert str(early.value) == "the regime cannot decide this document: no value is in effect on 2018-12-31"
    with pytest.raises(RegimeDefectError) as defect:
        from_regime(undeclared)
    assert str(defect.value) == "the regime is defective: regime 'do' parameter 'x' is not declared"
    assert from_regime(lambda: 1) == 1
