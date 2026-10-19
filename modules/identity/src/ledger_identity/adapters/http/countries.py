"""``/v1/countries``: every installed regime and channel as data (§9.8), so clients build forms from it.

Read from the packs the deployment installs, never from a database: a country is there when a regime is
installed for it. Any authenticated caller may read it (it is public law), in no company and no mode.
"""

from typing import Annotated
lazy from collections.abc import Iterable, Sequence

from fastapi import APIRouter, Depends

from ledger_identity.adapters.http.country_models import (
    ChannelBody,
    CountryBody,
    CountryList,
    CountrySummary,
    RegimeBody,
    labelled,
)
from ledger_identity.domain.errors import CountryNotFoundError
from ledger_kernel.jurisdiction import InvalidCountryError, parse_country
from ledger_platform.http import any_principal, problems
lazy from ledger_identity.adapters.http.context import ApiContext
lazy from ledger_kernel.access import Principal
lazy from ledger_kernel.regimes import RegimeDef
lazy from ledger_platform.config.manifest import ChannelEntry


def _regimes_of(country: str, regimes: Iterable[RegimeDef]) -> tuple[RegimeDef, ...]:
    """The regimes of ``country``, the national one first (keys sort that way)."""
    return tuple(
        sorted((regime for regime in regimes if regime.jurisdiction.country == country), key=lambda r: r.key)
    )


def _channels_of(country: str, channels: Iterable[ChannelEntry]) -> tuple[ChannelEntry, ...]:
    """The channels that serve ``country``: their key starts with its code, lower case."""
    prefix = f"{country.lower()}-"
    return tuple(sorted((entry for entry in channels if entry.key.startswith(prefix)), key=lambda e: e.key))


def countries(regimes: Sequence[RegimeDef], channels: Sequence[ChannelEntry]) -> CountryList:
    """Every country some regime is installed for, by code."""
    codes = sorted({regime.jurisdiction.country for regime in regimes})
    return CountryList(
        items=[
            CountrySummary.of(code, _regimes_of(code, regimes), _channels_of(code, channels))
            for code in codes
        ]
    )


def country(code: str, regimes: Sequence[RegimeDef], channels: Sequence[ChannelEntry]) -> CountryBody:
    """``code``'s regimes and channels in full; ``CountryNotFoundError`` for text naming no such country."""
    try:
        wanted = parse_country(code)
    except InvalidCountryError:
        raise CountryNotFoundError from None
    found = _regimes_of(wanted, regimes)
    if not found:
        raise CountryNotFoundError
    return CountryBody(
        country=wanted,
        labels=labelled(found[0].labels),
        regimes=[RegimeBody.of(regime) for regime in found],
        channels=[ChannelBody.of(entry) for entry in _channels_of(wanted, channels)],
    )


def router(context: ApiContext) -> APIRouter:
    """The countries' routes."""
    routes = APIRouter(prefix="/v1/countries", tags=["countries"])
    reader = any_principal(context.caller)
    regimes = context.regimes.all()

    @routes.get("", response_model=CountryList, responses=problems(401))
    async def list_countries(principal: Annotated[Principal, Depends(reader)]) -> CountryList:
        """The countries a regime is installed for, with the keys of their regimes and channels."""
        del principal
        return countries(regimes, context.channels)

    @routes.get("/{country_code}", response_model=CountryBody, responses=problems(401, 404, 422))
    async def read_country(
        country_code: str, principal: Annotated[Principal, Depends(reader)]
    ) -> CountryBody:
        """A country's regimes (its tax law as data) and the channels that serve it.

        The code is ISO 3166-1 alpha-2, upper case; anything else finds no country.
        """
        del principal
        return country(country_code, regimes, context.channels)

    return routes
