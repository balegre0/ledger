"""Each package's settings section (§3.10): declared in its manifest, read by a model of its assembly's.

The app reads every declared section when it is made, before any package assembles. A section declared
without a model, a model handed over for a section the manifest does not declare, or a model of another prefix
is a defect of the package, each its own error; values that cannot work are the deployment's, listed together
for every section at once, by location and never by value (``InvalidSettingsError``). A package's context
holds its own section alone: never the platform's settings, never another package's.
"""

from dataclasses import dataclass
from typing import ClassVar, Final, final
lazy from collections.abc import Iterable, Mapping

from pydantic import ValidationError

from ledger_kernel.errors import LedgerError
from ledger_platform.config.settings import section_prefix
lazy from ledger_platform.config.settings import PackageSettings


class SettingsNotProvidedError(LedgerError):
    """A package that declares a settings section and hands over no model to read it with."""

    code: ClassVar[str] = "settings_not_provided"

    def __init__(self, package: str, section: str) -> None:
        super().__init__(
            f"{package!r} declares a settings section ({section!r}) and hands over no model for it"
        )


class SettingsUndeclaredError(LedgerError):
    """A package that hands over a settings model for a section its manifest does not declare."""

    code: ClassVar[str] = "settings_undeclared"

    def __init__(self, package: str) -> None:
        super().__init__(
            f"{package!r} hands over a settings model, and its manifest declares no settings section"
        )


class SettingsPrefixError(LedgerError):
    """A settings model that reads variables of another prefix than its section's."""

    code: ClassVar[str] = "settings_prefix_mismatch"

    def __init__(self, package: str, prefix: str | None, expected: str) -> None:
        super().__init__(f"{package!r}'s settings model reads {prefix}, not {expected}")


class InvalidSettingsError(LedgerError):
    """Settings sections whose values cannot work; each problem reads ``<package>: <location>: <rule>``."""

    code: ClassVar[str] = "settings_invalid"

    def __init__(self, problems: tuple[str, ...]) -> None:
        super().__init__("settings: " + "; ".join(problems))
        self.problems: Final = problems


@final
@dataclass(frozen=True, slots=True)
class Declared:
    """What a package says of its settings: the section its manifest declares, the model it hands over."""

    package: str
    """A module's name or a channel's key."""
    section: str | None
    model: type[PackageSettings] | None


def _model(declared: Declared) -> type[PackageSettings] | None:
    """The model that reads ``declared``'s section, checked against the declaration; ``None`` without one."""
    if declared.section is None:
        if declared.model is not None:
            raise SettingsUndeclaredError(declared.package)
        return None
    if declared.model is None:
        raise SettingsNotProvidedError(declared.package, declared.section)
    expected = section_prefix(declared.section)
    prefix = declared.model.model_config.get("env_prefix")
    if prefix != expected:
        raise SettingsPrefixError(declared.package, prefix, expected)
    return declared.model


def read_sections(packages: Iterable[Declared]) -> Mapping[str, PackageSettings]:
    """Each declared section, read from the environment, by package (see the module)."""
    read: dict[str, PackageSettings] = {}
    problems: list[str] = []
    for declared in packages:
        model = _model(declared)
        if model is None:
            continue
        try:
            read[declared.package] = model()
        except ValidationError as error:
            problems.extend(
                f"{declared.package}: {'.'.join(str(part) for part in issue['loc'])}: {issue['msg']}"
                for issue in error.errors(include_input=False, include_url=False)
            )
    if problems:
        raise InvalidSettingsError(tuple(problems))
    return read
