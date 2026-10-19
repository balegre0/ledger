"""The shapes of ``/v1/countries``: each installed regime and channel as data, for clients to build forms.

Everything here is read from the packs installed in the deployment (§9.8): the regime a country's law is
(currencies, time zone, tax id schemes, tax categories with their dated rates, document types, rounding, the
fields it declares, dated parameters, sources) and the channels that serve it (§4.2: versions, environments,
document kinds, directions, tier). Amounts and rates are strings (§9.7), dates ISO 8601.
"""

from datetime import date
from typing import ClassVar, Self
lazy from collections.abc import Iterable, Mapping
lazy from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from ledger_identity.adapters.http.models import JurisdictionBody
from ledger_kernel.money import RoundingMode
from ledger_kernel.regimes import (
    Direction,
    DocumentKind,
    FieldKind,
    RoundingPoint,
    TaxCategoryKind,
)
from ledger_platform.config.manifest import Tier
lazy from ledger_kernel.dated import Since, Timeline
lazy from ledger_kernel.regimes import (
    DocumentTypeDef,
    FieldDef,
    Locale,
    RegimeDef,
    RoundingRule,
    Source,
    TaxCategoryDef,
    TaxIdSchemeDef,
)
lazy from ledger_platform.config.manifest import ChannelEntry

type Labels = dict[str, str]
"""A name in each language the pack provides: ``{"es": "…", "en": "…"}``."""


def labelled(labels: Mapping[Locale, str]) -> Labels:
    """``labels`` as the document shows them: by language code."""
    return {str(locale): text for locale, text in labels.items()}


class _Shape(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)


class SourceBody(_Shape):
    """Where a value comes from: a law, a regulation, an authority's technical document."""

    title: str
    url: str
    version: str
    retrieved_on: date

    @classmethod
    def of(cls, source: Source) -> Self:
        """The body of ``source``."""
        return cls(
            title=source.title, url=source.url, version=source.version, retrieved_on=source.retrieved_on
        )


class TaxIdSchemeBody(_Shape):
    """A tax id scheme: how a value is written and whether a check digit mismatch refuses it."""

    key: str = Field(examples=["do.rnc"])
    labels: Labels
    check_digit_enforced: bool
    peppol_icd: str | None

    @classmethod
    def of(cls, scheme: TaxIdSchemeDef) -> Self:
        """The body of ``scheme``."""
        return cls(
            key=scheme.key,
            labels=labelled(scheme.labels),
            check_digit_enforced=scheme.check_digit_enforced,
            peppol_icd=scheme.peppol_icd,
        )


class DatedValueBody(_Shape):
    """A value in effect from ``since`` (inclusive) until ``until`` (exclusive, or the next newer value)."""

    since: date
    until: date | None
    value: str = Field(description="A rate in percent or a parameter, as a string.", examples=["18"])
    source: SourceBody | None

    @classmethod
    def of(cls, entry: Since[Decimal | int | str], source: SourceBody | None = None) -> Self:
        """The body of ``entry``: a parameter's value, which may name its source."""
        return cls(since=entry.since, until=entry.until, value=str(entry.value), source=source)


class TaxCategoryBody(_Shape):
    """A tax category and its rates over time, newest first."""

    key: str = Field(examples=["do.itbis.standard"])
    labels: Labels
    kind: TaxCategoryKind
    rates: list[DatedValueBody]

    @classmethod
    def of(cls, category: TaxCategoryDef) -> Self:
        """The body of ``category``."""
        return cls(
            key=category.key,
            labels=labelled(category.labels),
            kind=category.kind,
            rates=[
                DatedValueBody(
                    since=entry.since,
                    until=entry.until,
                    value=str(entry.value.percent.normalize()),
                    source=SourceBody.of(entry.value.source),
                )
                for entry in category.rates.entries
            ],
        )


class DocumentTypeBody(_Shape):
    """A document type the law defines, and which types it may correct."""

    code: str = Field(examples=["31"])
    labels: Labels
    kind: DocumentKind
    direction: Direction
    requires_buyer_tax_id: bool
    corrects: list[str]

    @classmethod
    def of(cls, document: DocumentTypeDef) -> Self:
        """The body of ``document``."""
        return cls(
            code=document.code,
            labels=labelled(document.labels),
            kind=document.kind,
            direction=document.direction,
            requires_buyer_tax_id=document.requires_buyer_tax_id,
            corrects=list(document.corrects),
        )


class RoundingBody(_Shape):
    """How and where the regime rounds, and how far a document may stray per line."""

    mode: RoundingMode
    points: list[RoundingPoint]
    places: int
    line_tolerance: str

    @classmethod
    def of(cls, rounding: RoundingRule) -> Self:
        """The body of ``rounding``."""
        return cls(
            mode=rounding.mode,
            points=sorted(rounding.points),
            places=rounding.places,
            line_tolerance=str(rounding.line_tolerance),
        )


class FieldBody(_Shape):
    """A law-level field a request may carry under ``fiscal.<regime>``."""

    name: str
    labels: Labels
    kind: FieldKind
    required: bool

    @classmethod
    def of(cls, declared: FieldDef) -> Self:
        """The body of ``declared``."""
        return cls(
            name=declared.name,
            labels=labelled(declared.labels),
            kind=declared.kind,
            required=declared.required,
        )


class RegimeBody(_Shape):
    """A jurisdiction's tax law as data."""

    key: str = Field(examples=["do"])
    jurisdiction: JurisdictionBody
    currency: str = Field(description="ISO 4217: the currency its law is written in.", examples=["DOP"])
    time_zone: str = Field(description="The IANA zone its tax points are judged in.")
    labels: Labels
    tax_id_schemes: list[TaxIdSchemeBody]
    tax_categories: list[TaxCategoryBody]
    document_types: list[DocumentTypeBody]
    rounding: RoundingBody
    fields: list[FieldBody]
    parameters: dict[str, list[DatedValueBody]]
    sources: list[SourceBody]

    @classmethod
    def of(cls, regime: RegimeDef) -> Self:
        """The body of ``regime``."""
        return cls(
            key=regime.key,
            jurisdiction=JurisdictionBody.of(regime.jurisdiction),
            currency=regime.currency.code,
            time_zone=regime.time_zone,
            labels=labelled(regime.labels),
            tax_id_schemes=[TaxIdSchemeBody.of(scheme) for scheme in regime.tax_id_schemes],
            tax_categories=[TaxCategoryBody.of(category) for category in regime.tax_categories],
            document_types=[DocumentTypeBody.of(document) for document in regime.document_types],
            rounding=RoundingBody.of(regime.rounding),
            fields=[FieldBody.of(declared) for declared in regime.fields],
            parameters={name: _parameter(timeline) for name, timeline in sorted(regime.parameters.items())},
            sources=[SourceBody.of(source) for source in regime.sources],
        )


def _parameter(timeline: Timeline[Decimal | int | str]) -> list[DatedValueBody]:
    return [DatedValueBody.of(entry) for entry in timeline.entries]


class ChannelBody(_Shape):
    """A channel that serves the regime: the format and the authority's endpoints it speaks."""

    key: str = Field(examples=["do-ecf-v1"])
    valid_from: date
    valid_until: date | None
    environments: list[str]
    live_environments: list[str] = Field(description="The environments whose documents have legal effect.")
    document_kinds: list[DocumentKind]
    directions: list[Direction]
    tier: Tier

    @classmethod
    def of(cls, entry: ChannelEntry) -> Self:
        """The body of ``entry``."""
        return cls(
            key=entry.key,
            valid_from=entry.valid_from,
            valid_until=entry.valid_until,
            environments=list(entry.environments),
            live_environments=list(entry.live_environments),
            document_kinds=[DocumentKind(kind) for kind in entry.document_kinds],
            directions=[Direction(direction) for direction in entry.directions],
            tier=entry.tier,
        )


class CountrySummary(_Shape):
    """A country some regime is installed for."""

    country: str = Field(description="ISO 3166-1 alpha-2, upper case.", examples=["DO"])
    labels: Labels
    regimes: list[str] = Field(description="Its regimes' keys: the national one first.")
    channels: list[str] = Field(description="The keys of the channels that serve it.")

    @classmethod
    def of(cls, country: str, regimes: Iterable[RegimeDef], channels: Iterable[ChannelEntry]) -> Self:
        """The summary of ``country`` as ``regimes`` and ``channels`` (of it, by key) give it."""
        found = tuple(regimes)
        return cls(
            country=country,
            labels=labelled(found[0].labels),
            regimes=[regime.key for regime in found],
            channels=[entry.key for entry in channels],
        )


class CountryBody(_Shape):
    """A country in full: every regime installed for it and every channel that serves it, each once."""

    country: str = Field(description="ISO 3166-1 alpha-2, upper case.", examples=["DO"])
    labels: Labels
    regimes: list[RegimeBody] = Field(description="Its regimes: the national one first.")
    channels: list[ChannelBody] = Field(description="The channels that serve it.")


class CountryList(_Shape):
    """The countries installed, by code."""

    items: list[CountrySummary]
