from typing import Final

import pytest
lazy import httpx

from ledger_testing.api import Caller, created, document, page
lazy from ledger_testing.issuer import LocalIssuer

OTHER: Final = {"tax_id": "130000012", "legal_name": "Otra"}


async def another_company(client: httpx.AsyncClient, owner: Caller) -> str:
    return await created(client, "/v1/companies", OTHER, owner.posting("c-2"))


async def branch(client: httpx.AsyncClient, owner: Caller, company: str, code: str = "SUC1") -> str:
    """The id of a branch ``owner`` opened in ``company`` under ``code`` (a key per company and code)."""
    payload = {"code": code, "name": "Santiago"}
    path = f"/v1/companies/{company}/branches"
    return await created(client, path, payload, owner.posting(f"b-{company}-{code}"))


async def test_a_branch_is_opened_under_its_code_and_found_again(
    client: httpx.AsyncClient, owner: Caller, company: str
) -> None:
    path, payload = f"/v1/companies/{company}/branches", {"code": "SUC1", "name": " Santiago  Centro "}
    response = await client.post(path, json=payload, headers=owner.posting("b-1"))
    assert response.status_code == 201
    opened = document(response)
    assert (opened["code"], opened["name"], opened["company_id"]) == ("SUC1", "Santiago Centro", company)
    assert response.headers["location"] == f"{path}/{opened['id']}"
    read = await client.get(f"{path}/{opened['id']}", headers=owner.bearer())
    assert document(read) == opened
    again = await client.post(path, json=payload, headers=owner.posting("b-1"))
    assert (again.content, again.headers["idempotent-replayed"]) == (response.content, "true")


async def test_where_a_new_branch_lives_is_spelled_by_the_service(
    client: httpx.AsyncClient, owner: Caller, company: str
) -> None:
    response = await client.post(
        f"/v1/companies/{company.upper()}/branches",
        json={"code": "SUC2", "name": "Bonao"},
        headers=owner.posting("b-upper"),
    )
    assert response.status_code == 201
    assert response.headers["location"] == f"/v1/companies/{company}/branches/{document(response)['id']}"


async def test_a_branch_code_is_taken_once_per_company(
    client: httpx.AsyncClient, owner: Caller, company: str
) -> None:
    second = await another_company(client, owner)
    await branch(client, owner, company)
    taken = await client.post(
        f"/v1/companies/{company}/branches", json={"code": "SUC1", "name": "X"}, headers=owner.posting("b-2")
    )
    assert (taken.status_code, document(taken)["code"]) == (409, "branch_code_taken")
    assert await branch(client, owner, second)


@pytest.mark.parametrize(
    ("payload", "code"),
    [({"code": "suc 1", "name": "X"}, "invalid_code"), ({"code": "SUC1", "name": " "}, "invalid_name")],
    ids=["code", "name"],
)
async def test_a_branch_that_cannot_be_opened_is_refused(
    client: httpx.AsyncClient, owner: Caller, company: str, payload: dict[str, str], code: str
) -> None:
    response = await client.post(
        f"/v1/companies/{company}/branches", json=payload, headers=owner.posting("b-bad")
    )
    assert (response.status_code, document(response)["code"]) == (422, code)


async def test_branches_are_listed_page_by_page(
    client: httpx.AsyncClient, owner: Caller, company: str
) -> None:
    for code in ("SUC1", "SUC2"):
        await branch(client, owner, company, code)
    path = f"/v1/companies/{company}/branches"
    first = page(await client.get(path, params={"limit": 1}, headers=owner.bearer()))
    rest = page(
        await client.get(path, params={"limit": 1, "cursor": first["next_cursor"]}, headers=owner.bearer())
    )
    assert (len(first["items"]), len(rest["items"]), rest["next_cursor"]) == (1, 1, None)


async def test_a_branch_is_found_under_its_own_company_only(
    client: httpx.AsyncClient, owner: Caller, company: str
) -> None:
    second = await another_company(client, owner)
    branch_id = await branch(client, owner, company)
    response = await client.get(f"/v1/companies/{second}/branches/{branch_id}", headers=owner.bearer())
    assert (response.status_code, document(response)["code"]) == (404, "branch_not_found")


async def test_emission_points_open_in_a_branch_under_a_code_unique_there(
    client: httpx.AsyncClient, owner: Caller, company: str
) -> None:
    first, second = await branch(client, owner, company, "SUC1"), await branch(client, owner, company, "SUC2")
    base = f"/v1/companies/{company}/branches/{first}/emission-points"
    opened = await client.post(base, json={"code": "001", "name": "Caja 1"}, headers=owner.posting("p-1"))
    assert opened.status_code == 201
    point = document(opened)
    assert (point["branch_id"], point["code"], opened.headers["location"]) == (
        first,
        "001",
        f"{base}/{point['id']}",
    )
    taken = await client.post(base, json={"code": "001", "name": "Caja"}, headers=owner.posting("p-2"))
    assert (taken.status_code, document(taken)["code"]) == (409, "emission_point_code_taken")
    elsewhere = f"/v1/companies/{company}/branches/{second}/emission-points"
    assert (
        await client.post(elsewhere, json={"code": "001", "name": "Caja"}, headers=owner.posting("p-3"))
    ).status_code == 201
    assert document(await client.get(f"{base}/{point['id']}", headers=owner.bearer())) == point
    assert [item["id"] for item in page(await client.get(base, headers=owner.bearer()))["items"]] == [
        point["id"]
    ]
    wrong_branch = await client.get(f"{elsewhere}/{point['id']}", headers=owner.bearer())
    assert (wrong_branch.status_code, document(wrong_branch)["code"]) == (404, "emission_point_not_found")


async def test_an_emission_point_needs_a_branch_that_exists(
    client: httpx.AsyncClient, owner: Caller, company: str
) -> None:
    missing = "0192f7a4-6b1e-7c3d-8e4f-5a6b7c8d9e0f"
    base = f"/v1/companies/{company}/branches/{missing}/emission-points"
    opened = await client.post(base, json={"code": "001", "name": "Caja"}, headers=owner.posting("p-x"))
    listed = await client.get(base, headers=owner.bearer())
    assert (opened.status_code, document(opened)["code"], listed.status_code) == (
        404,
        "branch_not_found",
        404,
    )
    malformed = await client.get(f"/v1/companies/{company}/branches/not-an-id", headers=owner.bearer())
    assert (malformed.status_code, document(malformed)["code"]) == (422, "invalid_id")


async def test_a_viewer_reads_branches_and_opens_none(
    client: httpx.AsyncClient, issuer: LocalIssuer, owner: Caller, company: str
) -> None:
    await branch(client, owner, company)
    granted = await client.put(
        f"/v1/companies/{company}/memberships/3001", json={"role": "viewer"}, headers=owner.bearer()
    )
    assert granted.status_code == 201
    viewer = Caller(issuer, subject="3001")
    assert (await client.get(f"/v1/companies/{company}/branches", headers=viewer.bearer())).status_code == 200
    refused = await client.post(
        f"/v1/companies/{company}/branches", json={"code": "SUC9", "name": "X"}, headers=viewer.posting("b-v")
    )
    assert (refused.status_code, document(refused)["code"]) == (403, "permission_denied")


async def test_nothing_opens_in_a_deactivated_company(
    client: httpx.AsyncClient, owner: Caller, company: str
) -> None:
    branch_id = await branch(client, owner, company)
    off = await client.post(
        f"/v1/companies/{company}/deactivate", headers=owner.posting("off") | {"If-Match": '"1"'}
    )
    assert off.status_code == 200
    refused = await client.post(
        f"/v1/companies/{company}/branches",
        json={"code": "SUC2", "name": "X"},
        headers=owner.posting("b-off"),
    )
    point = await client.post(
        f"/v1/companies/{company}/branches/{branch_id}/emission-points",
        json={"code": "001", "name": "Caja"},
        headers=owner.posting("p-off"),
    )
    assert [(response.status_code, document(response)["code"]) for response in (refused, point)] == [
        (409, "company_inactive"),
        (409, "company_inactive"),
    ]


async def test_another_organization_finds_no_establishment_of_the_company(
    client: httpx.AsyncClient, issuer: LocalIssuer, owner: Caller, company: str
) -> None:
    branch_id = await branch(client, owner, company)
    stranger = Caller(issuer, organization="1002")
    paths = [
        f"/v1/companies/{company}/branches",
        f"/v1/companies/{company}/branches/{branch_id}",
        f"/v1/companies/{company}/branches/{branch_id}/emission-points",
    ]
    for path in paths:
        response = await client.get(path, headers=stranger.bearer())
        assert (response.status_code, document(response)["code"]) == (404, "company_not_found")
    opened = await client.post(
        paths[0], json={"code": "SUC9", "name": "X"}, headers=stranger.posting("b-stranger")
    )
    assert (opened.status_code, document(opened)["code"]) == (404, "company_not_found")


@pytest.mark.parametrize(
    ("method", "suffix"),
    [
        ("GET", "/branches"),
        ("POST", "/branches"),
        ("GET", "/branches/x"),
        ("GET", "/branches/x/emission-points"),
        ("POST", "/branches/x/emission-points"),
        ("GET", "/branches/x/emission-points/y"),
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


async def test_a_branch_is_opened_once_per_key_and_a_key_reused_for_another_branch_is_refused(
    client: httpx.AsyncClient, owner: Caller, company: str
) -> None:
    path = f"/v1/companies/{company}/branches"
    await created(client, path, {"code": "SUC1", "name": "A"}, owner.posting("b-key"))
    reused = await client.post(path, json={"code": "SUC2", "name": "B"}, headers=owner.posting("b-key"))
    assert (reused.status_code, document(reused)["code"]) == (422, "idempotency_key_reused")
    assert len(page(await client.get(path, headers=owner.bearer()))["items"]) == 1


async def test_a_branch_of_another_company_has_no_emission_points_here(
    client: httpx.AsyncClient, owner: Caller, company: str
) -> None:
    second = await another_company(client, owner)
    theirs = await branch(client, owner, second)
    point = await created(
        client,
        f"/v1/companies/{second}/branches/{theirs}/emission-points",
        {"code": "001", "name": "Caja"},
        owner.posting("p-theirs"),
    )
    base = f"/v1/companies/{company}/branches/{theirs}/emission-points"
    opened = await client.post(base, json={"code": "002", "name": "Caja"}, headers=owner.posting("p-here"))
    listed = await client.get(base, headers=owner.bearer())
    read = await client.get(f"{base}/{point}", headers=owner.bearer())
    assert [(response.status_code, document(response)["code"]) for response in (opened, listed, read)] == [
        (404, "branch_not_found"),
        (404, "branch_not_found"),
        (404, "emission_point_not_found"),
    ]
