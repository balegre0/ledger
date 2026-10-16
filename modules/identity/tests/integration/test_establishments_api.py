from typing import Final

import pytest
lazy import httpx

from ledger_testing.api import TestPrincipal, created, document, page
lazy from ledger_testing.issuer import LocalIssuer

OTHER: Final = {
    "jurisdiction": {"country": "DO"},
    "functional_currency": "DOP",
    "tax_ids": [{"scheme": "do.rnc", "value": "130000012"}],
    "legal_name": "Otra",
}
SANTO_DOMINGO: Final = "America/Santo_Domingo"


async def another_company(client: httpx.AsyncClient, owner: TestPrincipal) -> str:
    return await created(client, "/v1/companies", OTHER, owner.posting("c-2"))


async def establishment(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str, code: str = "SUC1"
) -> str:
    """The id of the establishment ``owner`` opens in ``company`` under ``code`` (one key per pair)."""
    payload = {"code": code, "name": "Santiago"}
    path = f"/v1/companies/{company}/establishments"
    return await created(client, path, payload, owner.posting(f"e-{company}-{code}"))


async def test_a_company_is_registered_with_its_main_establishment(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str
) -> None:
    listed = page(await client.get(f"/v1/companies/{company}/establishments", headers=owner.bearer()))
    (main,) = listed["items"]
    assert (main["code"], main["name"], main["time_zone"], main["company_id"]) == (
        "MAIN",
        "Main",
        SANTO_DOMINGO,
        company,
    )


async def test_an_establishment_is_opened_under_its_code_and_found_again(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str
) -> None:
    path, payload = f"/v1/companies/{company}/establishments", {"code": "SUC1", "name": " Santiago  Centro "}
    response = await client.post(path, json=payload, headers=owner.posting("e-1"))
    assert response.status_code == 201
    opened = document(response)
    assert (opened["code"], opened["name"], opened["company_id"]) == ("SUC1", "Santiago Centro", company)
    assert response.headers["location"] == f"{path}/{opened['id']}"
    read = await client.get(f"{path}/{opened['id']}", headers=owner.bearer())
    assert document(read) == opened
    again = await client.post(path, json=payload, headers=owner.posting("e-1"))
    assert (again.content, again.headers["idempotent-replayed"]) == (response.content, "true")


async def test_an_establishment_without_a_time_zone_works_in_the_main_one_s(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str
) -> None:
    path = f"/v1/companies/{company}/establishments"
    by_default = await client.post(path, json={"code": "SUC1", "name": "A"}, headers=owner.posting("e-1"))
    named = await client.post(
        path,
        json={"code": "SUC2", "name": "B", "time_zone": "Europe/Madrid"},
        headers=owner.posting("e-2"),
    )
    assert (document(by_default)["time_zone"], document(named)["time_zone"]) == (
        SANTO_DOMINGO,
        "Europe/Madrid",
    )


async def test_where_a_new_establishment_lives_is_spelled_by_the_service(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str
) -> None:
    response = await client.post(
        f"/v1/companies/{company.upper()}/establishments",
        json={"code": "SUC2", "name": "Bonao"},
        headers=owner.posting("e-upper"),
    )
    assert response.status_code == 201
    expected = f"/v1/companies/{company}/establishments/{document(response)['id']}"
    assert response.headers["location"] == expected


async def test_an_establishment_code_is_taken_once_per_company(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str
) -> None:
    second = await another_company(client, owner)
    await establishment(client, owner, company)
    for code in ("SUC1", "MAIN"):  # the company's own main establishment holds its code too
        taken = await client.post(
            f"/v1/companies/{company}/establishments",
            json={"code": code, "name": "X"},
            headers=owner.posting(f"e-taken-{code}"),
        )
        assert (taken.status_code, document(taken)["code"]) == (409, "establishment_code_taken")
    assert await establishment(client, owner, second)  # another company's SUC1 is free


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        ({"code": "suc 1", "name": "X"}, "invalid_code"),
        ({"code": "SUC1", "name": " "}, "invalid_name"),
        ({"code": "SUC1", "name": "X", "time_zone": "Mars/Olympus"}, "invalid_time_zone"),
        ({"code": "SUC1", "name": "X", "time_zone": "../etc/passwd"}, "invalid_time_zone"),
        ({"code": "SUC1", "name": "X", "colour": "red"}, "invalid_request"),
    ],
    ids=["code", "name", "unknown-zone", "path-as-zone", "unknown-field"],
)
async def test_an_establishment_that_cannot_be_opened_is_refused(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str, payload: dict[str, str], code: str
) -> None:
    response = await client.post(
        f"/v1/companies/{company}/establishments", json=payload, headers=owner.posting("e-bad")
    )
    assert (response.status_code, document(response)["code"]) == (422, code)


async def test_establishments_are_listed_page_by_page(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str
) -> None:
    for code in ("SUC1", "SUC2"):
        await establishment(client, owner, company, code)
    path = f"/v1/companies/{company}/establishments"  # with the main one: three
    first = page(await client.get(path, params={"limit": 2}, headers=owner.bearer()))
    rest = page(
        await client.get(
            path, params={"limit": 2, "starting_after": first["next_cursor"]}, headers=owner.bearer()
        )
    )
    assert (len(first["items"]), first["has_more"], len(rest["items"]), rest["next_cursor"]) == (
        2,
        True,
        1,
        None,
    )
    back = page(
        await client.get(path, params={"ending_before": rest["previous_cursor"]}, headers=owner.bearer())
    )
    assert (back["items"], back["has_more"]) == (first["items"], False)


async def test_an_establishment_is_found_under_its_own_company_only(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str
) -> None:
    second = await another_company(client, owner)
    wanted = await establishment(client, owner, company)
    response = await client.get(f"/v1/companies/{second}/establishments/{wanted}", headers=owner.bearer())
    assert (response.status_code, document(response)["code"]) == (404, "establishment_not_found")


async def test_a_viewer_reads_establishments_and_opens_none(
    client: httpx.AsyncClient, local_issuer: LocalIssuer, owner: TestPrincipal, company: str
) -> None:
    await establishment(client, owner, company)
    granted = await client.put(
        f"/v1/companies/{company}/memberships/3001", json={"role": "viewer"}, headers=owner.bearer()
    )
    assert granted.status_code == 201
    viewer = TestPrincipal(local_issuer, subject="3001")
    path = f"/v1/companies/{company}/establishments"
    assert (await client.get(path, headers=viewer.bearer())).status_code == 200
    refused = await client.post(path, json={"code": "SUC9", "name": "X"}, headers=viewer.posting("e-v"))
    assert (refused.status_code, document(refused)["code"]) == (403, "permission_denied")


async def test_nothing_opens_in_a_deactivated_company(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str
) -> None:
    off = await client.post(
        f"/v1/companies/{company}/deactivate", headers=owner.posting("off") | {"If-Match": '"1"'}
    )
    assert off.status_code == 200
    refused = await client.post(
        f"/v1/companies/{company}/establishments",
        json={"code": "SUC2", "name": "X"},
        headers=owner.posting("e-off"),
    )
    assert (refused.status_code, document(refused)["code"]) == (409, "company_inactive")


async def test_another_organization_finds_no_establishment_of_the_company(
    client: httpx.AsyncClient, local_issuer: LocalIssuer, owner: TestPrincipal, company: str
) -> None:
    wanted = await establishment(client, owner, company)
    stranger = TestPrincipal(local_issuer, organization="1002")
    paths = [
        f"/v1/companies/{company}/establishments",
        f"/v1/companies/{company}/establishments/{wanted}",
    ]
    for path in paths:
        response = await client.get(path, headers=stranger.bearer())
        assert (response.status_code, document(response)["code"]) == (404, "company_not_found")
    opened = await client.post(
        paths[0], json={"code": "SUC9", "name": "X"}, headers=stranger.posting("e-stranger")
    )
    assert (opened.status_code, document(opened)["code"]) == (404, "company_not_found")


@pytest.mark.parametrize(
    ("method", "suffix"),
    [
        ("GET", "/establishments"),
        ("POST", "/establishments"),
        ("GET", "/establishments/x"),
        ("GET", "/memberships"),
        ("PUT", "/memberships/3001"),
        ("DELETE", "/memberships/3001"),
    ],
)
async def test_every_route_under_a_company_needs_a_bearer_token(
    client: httpx.AsyncClient, company: str, method: str, suffix: str
) -> None:
    response = await client.request(
        method, f"/v1/companies/{company}{suffix}", headers={"Idempotency-Key": "k"}
    )
    assert (response.status_code, document(response)["code"]) == (401, "missing_token")


async def test_an_establishment_is_opened_once_per_key_and_a_key_reused_for_another_is_refused(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str
) -> None:
    path = f"/v1/companies/{company}/establishments"
    await created(client, path, {"code": "SUC1", "name": "A"}, owner.posting("e-key"))
    reused = await client.post(path, json={"code": "SUC2", "name": "B"}, headers=owner.posting("e-key"))
    assert (reused.status_code, document(reused)["code"]) == (422, "idempotency_key_reused")
    assert len(page(await client.get(path, headers=owner.bearer()))["items"]) == 2  # the main one and SUC1


async def test_a_key_is_not_replayed_for_another_company(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str
) -> None:
    """The company is part of the request: its key, used for another company, is a key reused (§9.5)."""
    second = await another_company(client, owner)
    payload = {"code": "SUC1", "name": "A"}
    mine = await client.post(
        f"/v1/companies/{company}/establishments", json=payload, headers=owner.posting("e-shared")
    )
    theirs = await client.post(
        f"/v1/companies/{second}/establishments", json=payload, headers=owner.posting("e-shared")
    )
    assert (mine.status_code, theirs.status_code) == (201, 422)
    assert document(theirs)["code"] == "idempotency_key_reused"
    assert "idempotent-replayed" not in theirs.headers
    listed = page(await client.get(f"/v1/companies/{second}/establishments", headers=owner.bearer()))
    assert [item["code"] for item in listed["items"]] == ["MAIN"]  # nothing of the first company's leaked


async def test_a_page_ending_before_an_establishment_holds_the_ones_just_before_it(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str
) -> None:
    """Read backwards from the newest (§9.7): the rows nearest the cursor, in id order, and more before."""
    for code in ("SUC1", "SUC2"):
        await establishment(client, owner, company, code)
    path, headers = f"/v1/companies/{company}/establishments", owner.bearer()
    ids = [item["id"] for item in page(await client.get(path, headers=headers))["items"]]  # MAIN, SUC1, SUC2
    two = page(await client.get(path, params={"limit": 2}, headers=headers))
    third = page(
        await client.get(path, params={"limit": 1, "starting_after": two["next_cursor"]}, headers=headers)
    )
    assert [item["id"] for item in third["items"]] == ids[2:]
    before = page(
        await client.get(
            path, params={"limit": 1, "ending_before": third["previous_cursor"]}, headers=headers
        )
    )
    assert ([item["id"] for item in before["items"]], before["has_more"]) == ([ids[1]], True)
    assert before["previous_cursor"] is not None
    first = page(
        await client.get(
            path, params={"limit": 1, "ending_before": before["previous_cursor"]}, headers=headers
        )
    )
    assert ([item["id"] for item in first["items"]], first["has_more"], first["previous_cursor"]) == (
        [ids[0]],
        False,
        None,
    )
