"""What is installed (§3.10 step 1, §8.1-§8.2): entry points by group, and the regimes they register.

Each loader validates every entry and freezes the result: an entry that does not hold what its group names,
an entry named apart from what it holds, a duplicate, or a regime whose time zone the system does not know
fails the start, naming the group and the entry (``InvalidPackError``). Discovery is a function, so a test
passes its own entries and needs nothing installed. Channel packs and modules, which name the contexts they
assemble with, are loaded by ``runtime.packs``.
"""

import zoneinfo
from dataclasses import dataclass
from importlib.metadata import entry_points
from types import MappingProxyType
from typing import ClassVar, Final, final
lazy from collections.abc import Callable, Iterable, Mapping, Sequence

from ledger_kernel.errors import LedgerError
from ledger_kernel.regimes import RegimeDef, TaxIdSchemes

MODULES: Final = "ledger.modules"
REGIMES: Final = "ledger.countries.regimes"
CHANNELS: Final = "ledger.countries.channels"


class InvalidPackError(LedgerError):
    """An installed entry point the start refuses: a defect of the package that declares it."""

    code: ClassVar[str] = "invalid_pack"

    def __init__(self, group: str, name: str, reason: str) -> None:
        super().__init__(f"{group} entry point {name!r} {reason}")


class RegimeUnknownError(LedgerError):
    """A regime key no installed country registers."""

    code: ClassVar[str] = "regime_unknown"

    def __init__(self, key: str) -> None:
        super().__init__(f"no regime {key!r} is installed")


@final
@dataclass(frozen=True, slots=True)
class Discovered:
    """An entry point: its name, and how to load what it names."""

    name: str
    load: Callable[[], object]


type Discover = Callable[[str], Sequence[Discovered]]
"""The entry points of a group."""


def installed(group: str) -> tuple[Discovered, ...]:
    """The entry points of ``group`` installed in this environment."""
    return tuple(Discovered(entry.name, entry.load) for entry in entry_points(group=group))


def check_unique(group: str, name: str, seen: set[str]) -> None:
    """Refuse a second entry of one name in ``group``."""
    if name in seen:
        raise InvalidPackError(group, name, "is installed twice")
    seen.add(name)


def _regime(found: Discovered, seen: set[str]) -> RegimeDef:
    value = found.load()
    if not isinstance(value, RegimeDef):
        raise InvalidPackError(REGIMES, found.name, "does not name a RegimeDef")
    if value.key != found.name:
        raise InvalidPackError(REGIMES, found.name, f"names the regime {value.key!r}")
    check_unique(REGIMES, found.name, seen)
    try:
        zoneinfo.ZoneInfo(value.time_zone)
    except zoneinfo.ZoneInfoNotFoundError as error:
        raise InvalidPackError(REGIMES, found.name, "has a time zone this system does not know") from error
    return value


@final
class Regimes:
    """Every installed regime by key, validated and frozen."""

    __slots__ = ("_by_key", "tax_id_schemes")

    def __init__(self, regimes: Iterable[RegimeDef]) -> None:
        by_key = {regime.key: regime for regime in regimes}
        self._by_key: Mapping[str, RegimeDef] = MappingProxyType(dict(sorted(by_key.items())))
        self.tax_id_schemes: Final = TaxIdSchemes(
            scheme for regime in self._by_key.values() for scheme in regime.tax_id_schemes
        )
        """Every regime's tax id schemes: a client's ``{scheme, value}`` is read with it."""

    @classmethod
    def load(cls, discover: Discover = installed) -> Regimes:
        """The regimes of group ``ledger.countries.regimes`` (``InvalidPackError`` for a bad entry)."""
        seen: set[str] = set()
        return cls([_regime(found, seen) for found in discover(REGIMES)])

    def get(self, key: str) -> RegimeDef:
        """The regime of ``key`` (``RegimeUnknownError`` if none is installed)."""
        regime = self._by_key.get(key)
        if regime is None:
            raise RegimeUnknownError(key)
        return regime

    def keys(self) -> tuple[str, ...]:
        """The installed regimes' keys, sorted."""
        return tuple(self._by_key)

    def __contains__(self, key: object) -> bool:
        """Whether a regime of ``key`` is installed."""
        return key in self._by_key
