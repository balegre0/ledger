from datetime import date
from decimal import Decimal
from typing import Final
lazy from collections.abc import Mapping, Sequence

import pytest

from ledger_kernel.dated import Since, Timeline
from ledger_kernel.jurisdiction import CountryCode, Jurisdiction
from ledger_kernel.money import Currency, RoundingMode
from ledger_kernel.regimes import (
    Locale,
    RegimeDef,
    RegimeKey,
    RoundingPoint,
    RoundingRule,
    Source,
    TaxIdSchemeDef,
)
from ledger_kernel.tax_ids import TaxIdCheck, TaxIdScheme
from ledger_platform.runtime.contexts import ChannelPack, ChannelParts, Module, ModuleParts
from ledger_platform.runtime.manifest import ChannelEntry, load_manifest
from ledger_platform.runtime.packs import Channels, load_modules
from ledger_platform.runtime.registries import (
    CHANNELS,
    MODULES,
    REGIMES,
    Discovered,
    InvalidPackError,
    Regimes,
    RegimeUnknownError,
    installed,
)
lazy from ledger_platform.runtime.contexts import ChannelContext, ModuleContext
lazy from ledger_platform.runtime.registries import Discover

SOURCE: Final = Source(
    title="Probe tax law", url="https://law.invalid/probe", version="1", retrieved_on=date(2026, 10, 13)
)
LABELS: Final[Mapping[Locale, str]] = {Locale("en"): "Probe"}


def four_digits(value: str) -> TaxIdCheck:
    return TaxIdCheck.VALID if len(value) == 4 and value.isdigit() else TaxIdCheck.INVALID_FORMAT


def unchanged(value: str) -> str:
    return value


def regime(key: str, *, time_zone: str = "America/New_York") -> RegimeDef:
    """The regime of ``key`` (``us``, or ``us-ca`` for a subdivision); its one scheme is ``<cc>.probe``."""
    country = key.partition("-")[0]
    code = CountryCode(country.upper())
    return RegimeDef(
        key=RegimeKey(key),
        jurisdiction=Jurisdiction(code, key.upper() if "-" in key else None),
        currency=Currency.of("USD"),
        time_zone=time_zone,
        labels=LABELS,
        tax_id_schemes=(
            TaxIdSchemeDef(
                key=TaxIdScheme(f"{country}.probe"),
                country=code,
                labels=LABELS,
                normalize=unchanged,
                check=four_digits,
                check_digit_enforced=False,
            ),
        ),
        tax_categories=(),
        document_types=(),
        rounding=RoundingRule(
            mode=RoundingMode.HALF_UP,
            points=frozenset({RoundingPoint.LINE}),
            places=2,
            line_tolerance=Decimal("0.01"),
        ),
        fields=(),
        parameters={"threshold": Timeline(Since(date(2020, 1, 1), Decimal(1000)))},
        sources=(SOURCE,),
    )


def entry(name: str, value: object) -> Discovered:
    return Discovered(name, lambda: value)


def finding(group: str, *entries: Discovered) -> Discover:
    def discover(asked: str) -> Sequence[Discovered]:
        return entries if asked == group else ()

    return discover


def channel_entry(key: str) -> ChannelEntry:
    return ChannelEntry.model_validate(
        {
            "key": key,
            "entry_point": "ledger_country_us.channels.probe:PACK",
            "assembly": "ledger_country_us.channels.probe.assembly:assemble",
            "schema": "us_probe",
            "valid_from": date(2026, 1, 1),
            "environments": ["test"],
            "document_kinds": ["invoice"],
            "directions": ["sales"],
            "tier": "bronze",
        }
    )


def unassembled(context: ChannelContext) -> ChannelParts:
    return ChannelParts(channel=context.definition)


def pack(key: str = "us-probe-v1", regime_key: str = "us") -> ChannelPack:
    return ChannelPack(entry=channel_entry(key), regime=regime_key, assemble=unassembled)


def nothing(context: ModuleContext) -> ModuleParts:
    del context
    return ModuleParts()


def broken() -> object:
    raise ImportError


def module(name: str, *, status: str = "active", kind: str = "module") -> Module:
    lines = (
        "[package]",
        f'name = "{name}"',
        f'kind = "{kind}"',
        'summary = "A probe."',
        'owners = ["@o"]',
        f'status = "{status}"',
    )
    return Module(manifest=load_manifest("\n".join(lines)), assemble=nothing)


def test_regimes_load_validate_and_freeze() -> None:
    mexico = regime("mx", time_zone="America/Mexico_City")
    regimes = Regimes.load(finding(REGIMES, entry("us", regime("us")), entry("mx", mexico)))
    assert regimes.get("us").key == "us"
    assert regimes.get("mx") is mexico
    assert regimes.keys() == ("mx", "us")
    assert regimes.tax_id_schemes.parse("us.probe", "1234").tax_id.value == "1234"
    assert regimes.tax_id_schemes.parse("mx.probe", "5678").tax_id.value == "5678"
    with pytest.raises(RegimeUnknownError, match="'fr'"):
        regimes.get("fr")


@pytest.mark.parametrize(
    ("entries", "named"),
    [
        ((entry("us", regime("us")), entry("us", regime("us"))), "us"),
        ((entry("us", regime("us", time_zone="Mars/Olympus")),), "us"),
        ((entry("us", 42),), "us"),
        ((entry("xx", regime("us")),), "xx"),
        ((Discovered("us", broken),), "us"),
        ((entry("us", regime("us")), entry("us-ca", regime("us-ca"))), "us-ca"),
    ],
    ids=[
        "duplicate-key",
        "unknown-time-zone",
        "not-a-regime",
        "named-apart-from-its-key",
        "fails-to-load",
        "tax-id-scheme-of-another-regime",
    ],
)
def test_a_duplicate_regime_key_an_unknown_time_zone_or_a_wrong_object_fails_the_start(
    entries: tuple[Discovered, ...], named: str
) -> None:
    with pytest.raises(InvalidPackError, match=rf"ledger\.countries\.regimes entry point '{named}'"):
        Regimes.load(finding(REGIMES, *entries))


def test_channels_load_with_a_known_regime_and_a_unique_key() -> None:
    regimes = Regimes.load(finding(REGIMES, entry("us", regime("us"))))
    probe = pack()
    channels = Channels.load(regimes, finding(CHANNELS, entry("us-probe-v1", probe)))
    assert channels.packs == (probe,)


@pytest.mark.parametrize(
    "entries",
    [
        (entry("us-probe-v1", pack(regime_key="mx")),),
        (entry("us-probe-v1", pack()), entry("us-probe-v1", pack())),
        (entry("us-probe-v1", 42),),
        (entry("us-other-v1", pack()),),
        (Discovered("us-probe-v1", broken),),
        (entry("mx-probe-v1", pack("mx-probe-v1", regime_key="us")),),
    ],
    ids=[
        "unknown-regime",
        "duplicate-key",
        "not-a-pack",
        "named-apart-from-its-key",
        "fails-to-load",
        "regime-of-another-country",
    ],
)
def test_channels_need_a_known_regime_and_a_unique_key(entries: tuple[Discovered, ...]) -> None:
    regimes = Regimes.load(finding(REGIMES, entry("us", regime("us"))))
    with pytest.raises(InvalidPackError, match=r"ledger\.countries\.channels entry point"):
        Channels.load(regimes, finding(CHANNELS, *entries))


def test_modules_load_and_suspended_ones_are_skipped() -> None:
    probe, paused = module("probe"), module("paused", status="suspended")
    found = load_modules(finding(MODULES, entry("probe", probe), entry("paused", paused)))
    assert found == (probe,)


@pytest.mark.parametrize(
    "entries",
    [
        (entry("other", module("probe")),),
        (entry("probe", 42),),
        (entry("probe", module("probe")), entry("probe", module("probe"))),
        (Discovered("probe", broken),),
        (entry("probe", module("probe", kind="package")),),
    ],
    ids=[
        "named-apart-from-its-manifest",
        "not-a-module",
        "duplicate-name",
        "fails-to-load",
        "not-a-module-kind",
    ],
)
def test_a_module_entry_that_does_not_hold_its_module_fails_the_start(
    entries: tuple[Discovered, ...],
) -> None:
    with pytest.raises(InvalidPackError, match=r"ledger\.modules entry point"):
        load_modules(finding(MODULES, *entries))


def test_installed_reads_entry_points() -> None:
    assert installed("ledger.no-such-group") == ()
    probe = next(each for each in installed("console_scripts") if each.name == "ledger-probe")
    assert callable(probe.load())
