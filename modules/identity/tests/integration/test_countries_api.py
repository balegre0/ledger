"""``/v1/countries`` over the regimes this deployment installs: the Dominican Republic's, as real data."""

import pytest
lazy import httpx

from ledger_identity.adapters.http.country_models import CountryBody, CountryList
from ledger_testing.api import document
lazy from ledger_testing.api import TestPrincipal


@pytest.mark.parametrize("path", ["/v1/countries", "/v1/countries/DO"])
async def test_countries_need_a_bearer_token(client: httpx.AsyncClient, path: str) -> None:
    response = await client.get(path)
    assert (response.status_code, document(response)["code"]) == (401, "missing_token")


async def test_the_installed_countries_are_listed(client: httpx.AsyncClient, owner: TestPrincipal) -> None:
    listed = await client.get("/v1/countries", headers=owner.bearer())
    assert listed.status_code == 200
    (summary,) = CountryList.model_validate_json(listed.content).items
    assert (summary.country, summary.labels, summary.regimes) == (
        "DO",
        {"es": "República Dominicana", "en": "Dominican Republic"},
        ["do"],
    )
    assert summary.channels == []  # do-ecf-v1 is a library until it has an assembly (v2-M3)


async def test_a_country_is_published_with_its_law_as_data(
    client: httpx.AsyncClient, owner: TestPrincipal
) -> None:
    response = await client.get("/v1/countries/DO", headers=owner.bearer())
    assert response.status_code == 200
    body = CountryBody.model_validate_json(response.content)
    assert (body.country, [regime.key for regime in body.regimes], body.channels) == ("DO", ["do"], [])
    (regime,) = body.regimes
    assert (regime.key, regime.currency, regime.time_zone) == ("do", "DOP", "America/Santo_Domingo")
    assert (regime.jurisdiction.country, regime.jurisdiction.subdivision) == ("DO", None)
    assert {scheme.key for scheme in regime.tax_id_schemes} == {"do.rnc", "do.cedula"}
    standard = next(category for category in regime.tax_categories if category.key == "do.itbis.standard")
    assert standard.rates[0].value == "18"
    assert standard.rates[0].source is not None
    assert standard.rates[0].source.url.startswith("https://")
    assert {"31", "32", "33", "34"} <= {entry.code for entry in regime.document_types}
    assert regime.parameters["rfce_threshold"][0].value == "250000.00"
    assert regime.sources, "a regime names where its values come from"


@pytest.mark.parametrize("code", ["ZZ", "do", "DOM", "D", "DE", "%F0%9F%92%A5", "x" * 400])
async def test_text_that_names_no_installed_country_finds_none_and_is_not_repeated(
    client: httpx.AsyncClient, owner: TestPrincipal, code: str
) -> None:
    response = await client.get(f"/v1/countries/{code}", headers=owner.bearer())
    assert (response.status_code, document(response)["code"]) == (404, "country_not_found")
    assert len(code) < 6 or code not in response.text
