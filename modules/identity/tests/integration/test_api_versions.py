"""``Ledger-Version`` through identity's API (§9.2): the organization's pin, the header, the echo."""

from datetime import date
from typing import Final

import pytest
lazy import httpx
lazy import psycopg

from ledger_platform.http import CURRENT_VERSION
from ledger_platform.http.versions import VERSION_HEADER
from ledger_testing.api import document
lazy from ledger_testing.api import TestPrincipal

EARLIER: Final = date(2026, 1, 1)


async def pin_organization(admin: psycopg.AsyncConnection, version: date) -> None:
    await admin.execute("UPDATE identity.organizations SET api_version = %s", (version,))


async def test_an_organization_is_pinned_to_the_current_version_when_its_first_company_is_registered(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str
) -> None:
    del company
    organization = await client.get("/v1/organization", headers=owner.bearer())
    assert document(organization)["api_version"] == CURRENT_VERSION.isoformat()


async def test_every_answer_names_the_version_it_speaks(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str
) -> None:
    current = CURRENT_VERSION.isoformat()
    for path in ("/v1/organization", "/v1/companies", f"/v1/companies/{company}", "/v1/countries"):
        assert (await client.get(path, headers=owner.bearer())).headers[VERSION_HEADER] == current
    missing = await client.get("/v1/companies/not-an-id", headers=owner.bearer())
    anonymous = await client.get("/v1/companies")
    assert (missing.status_code, missing.headers[VERSION_HEADER]) == (404, current)
    assert (anonymous.status_code, anonymous.headers[VERSION_HEADER]) == (401, current)


async def test_the_organization_s_pin_decides_unless_the_request_names_a_version(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str, admin: psycopg.AsyncConnection
) -> None:
    del company
    await pin_organization(admin, EARLIER)
    pinned = await client.get("/v1/organization", headers=owner.bearer())
    assert pinned.headers[VERSION_HEADER] == EARLIER.isoformat()
    named = await client.get(
        "/v1/organization", headers=owner.bearer() | {VERSION_HEADER: CURRENT_VERSION.isoformat()}
    )
    assert named.headers[VERSION_HEADER] == CURRENT_VERSION.isoformat()


async def test_a_pin_is_remembered_for_a_while(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str, admin: psycopg.AsyncConnection
) -> None:
    del company
    await pin_organization(admin, EARLIER)
    await client.get("/v1/organization", headers=owner.bearer())
    await pin_organization(admin, CURRENT_VERSION)
    remembered = await client.get("/v1/organization", headers=owner.bearer())
    assert remembered.headers[VERSION_HEADER] == EARLIER.isoformat()  # the pin of 30 s ago, until it expires


async def test_a_second_company_leaves_the_organization_s_pin_as_it_was(
    client: httpx.AsyncClient,
    owner: TestPrincipal,
    company: str,
    admin: psycopg.AsyncConnection,
) -> None:
    del company
    await pin_organization(admin, EARLIER)
    other = {
        "jurisdiction": {"country": "DO"},
        "functional_currency": "DOP",
        "tax_ids": [{"scheme": "do.rnc", "value": "130000012"}],
        "legal_name": "Otra",
    }
    assert (await client.post("/v1/companies", json=other, headers=owner.posting("c-2"))).status_code == 201
    row = await (await admin.execute("SELECT api_version FROM identity.organizations")).fetchone()
    assert row == (EARLIER,)


@pytest.mark.parametrize("text", ["2020-01-01", "tomorrow", "2026-13-45"])
async def test_a_version_that_does_not_exist_is_refused(
    client: httpx.AsyncClient, owner: TestPrincipal, text: str
) -> None:
    refused = await client.get("/v1/organization", headers=owner.bearer() | {VERSION_HEADER: text})
    assert (refused.status_code, document(refused)["code"]) == (400, "version_unknown")
