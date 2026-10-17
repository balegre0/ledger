"""``/v1/countries`` built from installed packs, without a server: what each country holds."""

from datetime import date
from decimal import Decimal
from typing import Final

import pytest

from ledger_identity.adapters.http.countries import countries, country
from ledger_identity.domain.errors import CountryNotFoundError
from ledger_kernel.dated import Since, Timeline
from ledger_kernel.jurisdiction import CountryCode, Jurisdiction
from ledger_kernel.money import Currency, RoundingMode
from ledger_kernel.regimes import (
    Direction,
    DocumentKind,
    DocumentTypeDef,
    FieldDef,
    FieldKind,
    Locale,
    RateValue,
    RegimeDef,
    RegimeKey,
    RoundingPoint,
    RoundingRule,
    Source,
    TaxCategoryDef,
    TaxCategoryKey,
    TaxCategoryKind,
)
from ledger_platform.config.manifest import ChannelEntry

LAW: Final = Source(
    title="The Law", url="https://law.invalid/1", version="2026", retrieved_on=date(2026, 10, 13)
)
LABELS: Final = {Locale("en"): "Land", Locale("es"): "Tierra"}


def regime(key: str, country_code: str, subdivision: str | None = None) -> RegimeDef:
    """A regime of ``key`` for a jurisdiction, with one tax category, one document type and one field."""
    return RegimeDef(
        key=RegimeKey(key),
        jurisdiction=Jurisdiction(CountryCode(country_code), subdivision),
        currency=Currency.of("USD"),
        time_zone="America/New_York",
        labels=LABELS,
        tax_id_schemes=(),
        tax_categories=(
            TaxCategoryDef(
                key=TaxCategoryKey(f"{key}.vat.standard"),
                labels=LABELS,
                kind=TaxCategoryKind.STANDARD,
                rates=Timeline(
                    Since(date(2026, 1, 1), RateValue(percent=Decimal("19.00"), source=LAW)),
                    Since(
                        date(2020, 1, 1), RateValue(percent=Decimal(18), source=LAW), until=date(2026, 1, 1)
                    ),
                ),
            ),
        ),
        document_types=(
            DocumentTypeDef(
                code="1",
                labels=LABELS,
                kind=DocumentKind.INVOICE,
                direction=Direction.SALES,
                requires_buyer_tax_id=False,
            ),
        ),
        rounding=RoundingRule(
            mode=RoundingMode.HALF_UP,
            points=frozenset({RoundingPoint.LINE, RoundingPoint.DOCUMENT}),
            places=2,
            line_tolerance=Decimal("0.01"),
        ),
        fields=(FieldDef(name="code", labels=LABELS, kind=FieldKind.CODE, required=True),),
        parameters={
            "limit": Timeline(Since(date(2020, 1, 1), Decimal("250000.00"))),
            "days": Timeline(Since(date(2020, 1, 1), 15)),
        },
        sources=(LAW,),
    )


def channel(key: str, *, until: date | None = None) -> ChannelEntry:
    return ChannelEntry.model_validate(
        {
            "key": key,
            "entry_point": "ledger_country_zz.channels.probe:PACK",
            "schema": key.replace("-", "_"),
            "valid_from": date(2026, 1, 1),
            "valid_until": until,
            "environments": ["test", "prod"],
            "live_environments": ["prod"],
            "document_kinds": ["invoice", "credit_note"],
            "directions": ["sales"],
            "tier": "silver",
        }
    )


US: Final = regime("us", "US")
US_CA: Final = regime("us-ca", "US", "US-CA")
FR: Final = regime("fr", "FR")


def test_each_country_is_listed_once_with_its_regimes_national_first_and_the_channels_that_serve_it() -> None:
    listed = countries([US_CA, FR, US], [channel("us-probe-v2"), channel("fr-x-v1"), channel("us-probe-v1")])
    assert [(item.country, item.regimes, item.channels) for item in listed.items] == [
        ("FR", ["fr"], ["fr-x-v1"]),
        ("US", ["us", "us-ca"], ["us-probe-v1", "us-probe-v2"]),
    ]
    assert listed.items[0].labels == {"en": "Land", "es": "Tierra"}


def test_no_regime_means_no_country() -> None:
    assert countries([], [channel("us-probe-v1")]).items == []


def test_a_country_is_given_in_full() -> None:
    body = country("US", [FR, US_CA, US], [channel("us-probe-v1", until=date(2030, 1, 1))])
    assert (body.country, body.regimes, body.channels) == ("US", ["us", "us-ca"], ["us-probe-v1"])
    national, california = body.regime_details
    assert (national.key, national.currency, national.time_zone, california.key) == (
        "us",
        "USD",
        "America/New_York",
        "us-ca",
    )
    assert (national.jurisdiction.country, california.jurisdiction.subdivision) == ("US", "US-CA")
    (category,) = national.tax_categories
    assert (category.key, category.kind.value) == ("us.vat.standard", "standard")
    assert [(rate.since, rate.until, rate.value) for rate in category.rates] == [
        (date(2026, 1, 1), None, "19"),
        (date(2020, 1, 1), date(2026, 1, 1), "18"),
    ]
    assert category.rates[0].source is not None
    assert category.rates[0].source.title == "The Law"
    (document,) = national.document_types
    assert (document.code, document.kind.value, document.direction.value, document.corrects) == (
        "1",
        "invoice",
        "sales",
        [],
    )
    assert (national.rounding.mode.value, national.rounding.points, national.rounding.line_tolerance) == (
        "half_up",
        [RoundingPoint.DOCUMENT, RoundingPoint.LINE],
        "0.01",
    )
    assert [(field.name, field.kind.value, field.required) for field in national.fields] == [
        ("code", "code", True)
    ]
    assert {name: [entry.value for entry in entries] for name, entries in national.parameters.items()} == {
        "days": ["15"],
        "limit": ["250000.00"],
    }
    assert list(national.parameters) == [
        "days",
        "limit",
    ]  # by name, so the document does not change by itself
    (published,) = body.channel_details
    assert (
        published.key,
        published.valid_until,
        published.environments,
        published.live_environments,
        [kind.value for kind in published.document_kinds],
        [direction.value for direction in published.directions],
        published.tier,
    ) == (
        "us-probe-v1",
        date(2030, 1, 1),
        ["test", "prod"],
        ["prod"],
        ["invoice", "credit_note"],
        ["sales"],
        "silver",
    )


@pytest.mark.parametrize("text", ["ZZ", "us", "USA", "", "U", "💥💥", "x" * 400, " US"])
def test_text_that_is_no_installed_country_finds_none_and_is_not_repeated(text: str) -> None:
    with pytest.raises(CountryNotFoundError) as caught:
        country(text, [US, FR], [])
    assert text not in str(caught.value) or text == ""
    assert caught.value.code == "country_not_found"


def test_a_country_without_a_regime_is_not_found_though_it_is_a_country() -> None:
    with pytest.raises(CountryNotFoundError):
        country("DE", [US, FR], [channel("de-x-v1")])
