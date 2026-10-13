"""Channel packs and modules, found by entry point group (§3.10 step 1; Decision 13).

A channel pack must be of an installed regime and a key of its own, named like its entry point and its
manifest entry; a module must be named like its manifest. Anything else fails the start, naming the group
and the entry (``InvalidPackError``). A suspended module is neither assembled nor migrated.
"""

from typing import Final, final
lazy from collections.abc import Iterable

from ledger_platform.runtime.contexts import ChannelPack, Module
from ledger_platform.runtime.manifest import PackageStatus
from ledger_platform.runtime.registries import CHANNELS, MODULES, InvalidPackError, check_unique, installed
lazy from ledger_platform.runtime.registries import Discover, Discovered, Regimes


def _pack(found: Discovered, regimes: Regimes, seen: set[str]) -> ChannelPack:
    value = found.load()
    if not isinstance(value, ChannelPack):
        raise InvalidPackError(CHANNELS, found.name, "does not name a channel pack")
    if value.key != found.name:
        raise InvalidPackError(CHANNELS, found.name, f"names the channel {value.key!r}")
    if value.regime not in regimes:
        raise InvalidPackError(
            CHANNELS, found.name, f"serves the regime {value.regime!r}, which is not installed"
        )
    check_unique(CHANNELS, found.name, seen)
    return value


@final
class Channels:
    """Every installed channel pack, of a known regime and a unique key."""

    __slots__ = ("packs",)

    def __init__(self, packs: Iterable[ChannelPack]) -> None:
        self.packs: Final = tuple(packs)

    @classmethod
    def load(cls, regimes: Regimes, discover: Discover = installed) -> Channels:
        """The packs of group ``ledger.countries.channels`` (``InvalidPackError`` for a bad entry)."""
        seen: set[str] = set()
        return cls([_pack(found, regimes, seen) for found in discover(CHANNELS)])


def _module(found: Discovered, seen: set[str]) -> Module:
    value = found.load()
    if not isinstance(value, Module):
        raise InvalidPackError(MODULES, found.name, "does not name a Module")
    if value.manifest.package.name != found.name:
        raise InvalidPackError(MODULES, found.name, f"names the module {value.manifest.package.name!r}")
    check_unique(MODULES, found.name, seen)
    return value


def load_modules(discover: Discover = installed) -> tuple[Module, ...]:
    """The modules of group ``ledger.modules``, by name; a suspended one is neither assembled nor migrated."""
    seen: set[str] = set()
    modules = [_module(found, seen) for found in discover(MODULES)]
    active = (module for module in modules if module.manifest.package.status is not PackageStatus.SUSPENDED)
    return tuple(sorted(active, key=lambda module: module.manifest.package.name))
