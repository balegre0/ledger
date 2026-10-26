import asyncio
import base64
import contextvars
import json
from functools import partial
from http import HTTPStatus
from typing import Final

import pytest
from pydantic import TypeAdapter
lazy import httpx
lazy import psycopg

from ledger_identity.adapters.postgres.unit import bind
from ledger_identity.application import commands
from ledger_identity.domain.organization import ORGANIZATION_ADMIN
from ledger_kernel.access import Role, SubjectId
from ledger_kernel.ids import COMPANY_PREFIX, ORGANIZATION_PREFIX, CompanyId, format_id, new_id, parse_id
from ledger_kernel.tenancy import Tenant
from ledger_platform.auth.providers.zitadel import organization_of
from ledger_testing.api import TestPrincipal, created, document, page
from ledger_testing.database import app_url
from ledger_testing.issuer import LocalIssuer
from ledger_testing.waiting import eventually, waits_for_a_lock
lazy from ledger_platform.data.bound import BoundDatabase
lazy from ledger_testing.postgres import WorkerDatabase


def body(*tax_ids: object, legal_name: str = "Duarte", **extra: object) -> dict[str, object]:
    """A registration of a Dominican company in pesos holding ``tax_ids``; ``extra`` adds or replaces."""
    return {
        "jurisdiction": {"country": "DO"},
        "functional_currency": "DOP",
        "tax_ids": list(tax_ids),
        "legal_name": legal_name,
    } | extra


DUARTE: Final = body({"scheme": "do.rnc", "value": "101-85004-3"}, legal_name="Ferretería Duarte, S.R.L.")
"""The same taxpayer as the ``company`` fixture's, as a client may type it."""
TAX_IDS: Final = TypeAdapter(list[dict[str, str]])
FAR_FUTURE: Final = 4_102_444_800
"""2100-01-01, as a JWT's ``exp``."""


async def grant(module_database: BoundDatabase, company: str, subject: str, role: Role) -> None:
    """Give ``subject`` a role directly, as the owner (``2001``) would through the memberships API."""
    tenant = Tenant(organization_of("1001"), CompanyId(parse_id(COMPANY_PREFIX, company)), None)
    async with module_database.unit(tenant) as unit:
        await commands.grant_role(
            bind(unit),
            tenant.company,
            subject=SubjectId(subject),
            role=role,
            actor=SubjectId("2001"),
        )


def segment(part: dict[str, object]) -> str:
    """One base64url segment of a JWT."""
    return base64.urlsafe_b64encode(json.dumps(part).encode()).rstrip(b"=").decode()


# ── Creating ────────────────────────────────────────────────────────────────────


async def test_a_company_is_created_owned_by_its_creator_and_readable(
    client: httpx.AsyncClient, owner: TestPrincipal
) -> None:
    response = await client.post("/v1/companies", json=DUARTE, headers=owner.posting("k-1"))
    assert response.status_code == 201
    made = document(response)
    assert (made["tax_ids"], made["legal_name"], made["status"], made["version"]) == (
        [{"scheme": "do.rnc", "value": "101850043"}],
        "Ferretería Duarte, S.R.L.",
        "active",
        1,
    )
    assert (made["jurisdiction"], made["functional_currency"], made["fiscal_profile_locked_at"]) == (
        {"country": "DO", "subdivision": None},
        "DOP",
        None,
    )
    assert made["warnings"] == []
    assert made["organization_id"] == format_id(ORGANIZATION_PREFIX, organization_of("1001"))
    assert response.headers["location"] == f"/v1/companies/{made['id']}"
    assert response.headers["etag"] == '"1"'
    read = await client.get(f"/v1/companies/{made['id']}", headers=owner.bearer())
    listed = {key: value for key, value in made.items() if key != "warnings"}  # said once, at registration
    assert (read.status_code, document(read), read.headers["etag"]) == (200, listed, '"1"')
    organization = await client.get("/v1/organization", headers=owner.bearer())
    assert document(organization)["provider_id"] == "1001"


async def test_the_organization_is_unknown_until_its_first_company(
    client: httpx.AsyncClient, owner: TestPrincipal
) -> None:
    response = await client.get("/v1/organization", headers=owner.bearer())
    assert (response.status_code, document(response)["code"]) == (404, "organization_not_found")


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/v1/organization"),
        ("GET", "/v1/companies"),
        ("POST", "/v1/companies"),
        ("GET", "/v1/companies/x"),
        ("PATCH", "/v1/companies/x"),
        ("POST", "/v1/companies/x/deactivate"),
    ],
)
async def test_every_route_needs_a_bearer_token(client: httpx.AsyncClient, method: str, path: str) -> None:
    response = await client.request(method, path, headers={"Idempotency-Key": "k"})
    assert (response.status_code, document(response)["code"]) == (401, "missing_token")


async def test_a_post_without_an_idempotency_key_is_refused(
    client: httpx.AsyncClient, owner: TestPrincipal
) -> None:
    headers = owner.bearer() | {"Ledger-Mode": "test"}
    response = await client.post("/v1/companies", json=DUARTE, headers=headers)
    assert (response.status_code, document(response)["code"]) == (422, "invalid_request")


@pytest.mark.parametrize(
    ("mode", "code"), [(None, "mode_required"), ("TEST", "mode_invalid")], ids=["missing", "not-a-mode"]
)
async def test_a_post_acts_in_the_mode_it_names(
    client: httpx.AsyncClient, owner: TestPrincipal, mode: str | None, code: str
) -> None:
    """An idempotent route is a moded route (Decision 4): its key is kept per mode."""
    headers = owner.bearer() | {"Idempotency-Key": "k-1"} | ({} if mode is None else {"Ledger-Mode": mode})
    response = await client.post("/v1/companies", json=DUARTE, headers=headers)
    assert (response.status_code, document(response)["code"]) == (400, code)
    assert page(await client.get("/v1/companies", headers=owner.bearer()))["items"] == []


async def test_a_key_is_kept_per_mode(client: httpx.AsyncClient, owner: TestPrincipal) -> None:
    """The same key and body in live mode are a new request there: it runs, and finds the taxpayer taken
    (one active company per tax id, whatever the mode); in test mode the key replays."""
    registered = await client.post("/v1/companies", json=DUARTE, headers=owner.posting("k-1"))
    live = await client.post("/v1/companies", json=DUARTE, headers=owner.posting("k-1", mode="live"))
    assert (live.status_code, document(live)["code"]) == (409, "tax_id_taken")
    assert "idempotent-replayed" not in live.headers
    again = await client.post("/v1/companies", json=DUARTE, headers=owner.posting("k-1"))
    assert (again.content, again.headers["idempotent-replayed"]) == (registered.content, "true")


async def test_a_retried_post_creates_once_and_answers_the_same(
    client: httpx.AsyncClient, owner: TestPrincipal
) -> None:
    first = await client.post("/v1/companies", json=DUARTE, headers=owner.posting("k-1"))
    again = await client.post("/v1/companies", json=DUARTE, headers=owner.posting("k-1"))
    assert (again.status_code, again.content) == (first.status_code, first.content)
    assert (again.headers["location"], again.headers["etag"]) == (first.headers["location"], '"1"')
    assert again.headers["idempotent-replayed"] == "true"
    assert "idempotent-replayed" not in first.headers
    listed = await client.get("/v1/companies", headers=owner.bearer())
    assert len(page(listed)["items"]) == 1


async def test_a_retry_with_its_fields_in_another_order_is_the_same_request(
    client: httpx.AsyncClient, owner: TestPrincipal
) -> None:
    first = await client.post("/v1/companies", json=DUARTE, headers=owner.posting("k-1"))
    reordered = json.dumps(
        {
            "legal_name": "Ferretería Duarte, S.R.L.",
            "tax_ids": [{"value": "101-85004-3", "scheme": "do.rnc"}],
            "functional_currency": "DOP",
            "jurisdiction": {"country": "DO"},
        },
        indent=3,
    )
    headers = owner.posting("k-1") | {"Content-Type": "application/json"}
    again = await client.post("/v1/companies", content=reordered.encode(), headers=headers)
    assert (again.content, again.headers["idempotent-replayed"]) == (first.content, "true")


@pytest.mark.parametrize(
    "roles", [(), ("ledger.viewer",)], ids=["a-machine-user-without-roles", "a-viewer-of-the-organization"]
)
async def test_registering_a_company_without_the_organization_s_admin_role_is_forbidden(
    client: httpx.AsyncClient, local_issuer: LocalIssuer, roles: tuple[str, ...]
) -> None:
    member = TestPrincipal(local_issuer, subject="2002", roles=roles)
    response = await client.post("/v1/companies", json=DUARTE, headers=member.posting("k-1"))
    assert (response.status_code, document(response)["code"]) == (403, "forbidden")
    assert page(await client.get("/v1/companies", headers=member.bearer()))["items"] == []


async def test_the_role_is_checked_before_what_the_request_must_carry(
    client: httpx.AsyncClient, local_issuer: LocalIssuer
) -> None:
    """As every route checks the caller's permission before its Idempotency-Key and its body."""
    response = await client.post("/v1/companies", json={}, headers=TestPrincipal(local_issuer).bearer())
    assert (response.status_code, document(response)["code"]) == (403, "forbidden")


async def test_a_key_reused_for_another_body_is_refused(
    client: httpx.AsyncClient, owner: TestPrincipal
) -> None:
    await created(client, "/v1/companies", DUARTE, owner.posting("k-1"))
    other = body({"scheme": "do.rnc", "value": "130000012"}, legal_name="Otra")
    response = await client.post("/v1/companies", json=other, headers=owner.posting("k-1"))
    assert (response.status_code, document(response)["code"]) == (422, "idempotency_key_reused")


async def test_a_key_belongs_to_its_caller(
    client: httpx.AsyncClient, local_issuer: LocalIssuer, owner: TestPrincipal
) -> None:
    mine = await created(client, "/v1/companies", DUARTE, owner.posting("k-1"))
    theirs = await client.post(
        "/v1/companies",
        json=DUARTE | {"tax_ids": [{"scheme": "do.rnc", "value": "401500022"}]},
        headers=TestPrincipal(local_issuer, organization="1002", roles=(ORGANIZATION_ADMIN,)).posting("k-1"),
    )
    assert theirs.status_code == 201
    assert "idempotent-replayed" not in theirs.headers
    assert document(theirs)["id"] != mine


async def test_two_posts_with_one_key_at_once_create_one_company(
    client: httpx.AsyncClient, owner: TestPrincipal
) -> None:
    headers = owner.posting("k-together")
    first, second = await asyncio.gather(
        client.post("/v1/companies", json=DUARTE, headers=headers),
        client.post("/v1/companies", json=DUARTE, headers=headers),
    )
    assert (first.status_code, second.status_code, first.content) == (201, 201, second.content)
    assert sorted(response.headers.get("idempotent-replayed", "no") for response in (first, second)) == [
        "no",
        "true",
    ]
    listed = await client.get("/v1/companies", headers=owner.bearer())
    assert len(page(listed)["items"]) == 1


RNC: Final = {"scheme": "do.rnc", "value": "101850043"}


def without(payload: dict[str, object], key: str) -> dict[str, object]:
    return {name: value for name, value in payload.items() if name != key}


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        (body({"scheme": "do.rnc", "value": "12345"}), "invalid_tax_id"),
        (body({"scheme": "do.cedula", "value": "101850043"}), "invalid_tax_id"),
        (body({"scheme": "do.rnc", "value": "x" * 41}), "invalid_request"),
        (body({"scheme": "mx.rfc", "value": "101850043"}), "tax_id_scheme_unknown"),
        (body({"scheme": "DO.RNC", "value": "101850043"}), "invalid_tax_id_scheme"),
        (body({"scheme": "zz.rnc", "value": "101850043"}), "invalid_tax_id_scheme"),
        (body("101850043"), "invalid_request"),
        (body({"scheme": "do.rnc"}), "invalid_request"),
        (body({"scheme": "do.rnc", "value": 101850043}), "invalid_request"),
        (body(RNC | {"kind": "rnc"}), "invalid_request"),
        (body(RNC, legal_name="Duarte\u0000"), "invalid_name"),
        (body(RNC, legal_name="x" * 301), "invalid_request"),
        (body(RNC, colour="red"), "invalid_request"),
        (without(body(RNC), "legal_name"), "invalid_request"),
        (body(), "invalid_request"),
        (body(RNC, RNC | {"value": "101-85004-3"}), "tax_id_duplicated"),
        (
            body(*({"scheme": "do.cedula", "value": f"0011391820{number}"} for number in range(6))),
            "invalid_request",
        ),
        (body(RNC, jurisdiction={"country": "do"}), "invalid_country"),
        (body(RNC, jurisdiction={"country": "ZZ"}), "invalid_country"),
        (body(RNC, jurisdiction={"country": "DO", "subdivision": "US-CA"}), "invalid_subdivision"),
        (body(RNC, jurisdiction={"country": "DO", "state": "x"}), "invalid_request"),
        (body(RNC, functional_currency="dop"), "invalid_currency"),
        (body(RNC, functional_currency="XXXX"), "invalid_request"),
        (without(body(RNC), "jurisdiction"), "invalid_request"),
        (without(body(RNC), "functional_currency"), "invalid_request"),
        (without(body(RNC), "tax_ids"), "invalid_request"),
    ],
    ids=[
        "malformed-value",
        "wrong-length-for-the-scheme",
        "too-long-value",
        "unknown-scheme",
        "upper-case-scheme",
        "no-such-country",
        "unqualified",
        "no-value",
        "value-not-text",
        "extra-field-in-tax-id",
        "legal-name",
        "legal-name-too-long",
        "unknown-field",
        "missing-legal-name",
        "no-tax-ids",
        "the-same-tax-id-twice",
        "too-many-tax-ids",
        "lower-case-country",
        "unassigned-country",
        "subdivision-of-another-country",
        "unknown-jurisdiction-field",
        "lower-case-currency",
        "four-letter-currency",
        "missing-jurisdiction",
        "missing-currency",
        "missing-tax-ids",
    ],
)
async def test_a_company_that_cannot_be_registered_is_refused(
    client: httpx.AsyncClient, owner: TestPrincipal, payload: dict[str, object], code: str
) -> None:
    response = await client.post("/v1/companies", json=payload, headers=owner.posting("k-bad"))
    assert (response.status_code, document(response)["code"]) == (422, code)


async def test_a_company_is_registered_in_its_regime_s_time_zone_unless_it_names_one(
    client: httpx.AsyncClient, owner: TestPrincipal
) -> None:
    by_default = await client.post("/v1/companies", json=DUARTE, headers=owner.posting("k-1"))
    named = await client.post(
        "/v1/companies",
        json=body({"scheme": "do.rnc", "value": "130000012"}, time_zone="Europe/Madrid"),
        headers=owner.posting("k-2"),
    )
    zones: list[tuple[str, str]] = []
    for made in (by_default, named):
        path = f"/v1/companies/{document(made)['id']}/establishments"
        (main,) = page(await client.get(path, headers=owner.bearer()))["items"]
        zones.append((str(main["code"]), str(main["time_zone"])))
    assert zones == [("MAIN", "America/Santo_Domingo"), ("MAIN", "Europe/Madrid")]


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"jurisdiction": {"country": "ES"}}, "jurisdiction_unsupported"),
        ({"jurisdiction": {"country": "ES"}, "time_zone": "Europe/Madrid"}, "jurisdiction_unsupported"),
        ({"functional_currency": "USD"}, "currency_unsupported"),
    ],
    ids=["no-regime", "no-regime-with-a-zone", "currency-the-regime-does-not-allow"],
)
async def test_a_profile_the_installed_regimes_do_not_allow_is_refused(
    client: httpx.AsyncClient, owner: TestPrincipal, change: dict[str, object], code: str
) -> None:
    """Only the regime of the company's jurisdiction can judge its documents: it must allow its profile."""
    refused = await client.post(
        "/v1/companies",
        json=body({"scheme": "do.rnc", "value": "101-85004-3"}) | change,
        headers=owner.posting("k-1"),
    )
    assert (refused.status_code, document(refused)["code"]) == (422, code)


@pytest.mark.parametrize("zone", ["Mars/Olympus", "../etc/passwd", "", "x" * 65])
async def test_a_company_s_time_zone_that_is_no_zone_is_refused(
    client: httpx.AsyncClient, owner: TestPrincipal, zone: str
) -> None:
    refused = await client.post(
        "/v1/companies", json=DUARTE | {"time_zone": zone}, headers=owner.posting("k-1")
    )
    assert refused.status_code == 422
    assert document(refused)["code"] in {"invalid_time_zone", "invalid_request"}


async def test_a_check_digit_mismatch_is_registered_as_dgii_issued_it(
    client: httpx.AsyncClient, owner: TestPrincipal
) -> None:
    """``do.rnc`` reports a mismatching check digit without refusing it: DGII has issued such numbers."""
    payload = body({"scheme": "do.rnc", "value": "101-85004-2"})
    made = await client.post("/v1/companies", json=payload, headers=owner.posting("k-1"))
    assert (made.status_code, document(made)["tax_ids"]) == (
        201,
        [{"scheme": "do.rnc", "value": "101850042"}],
    )
    assert document(made)["warnings"] == [{"code": "tax_id_checksum_mismatch", "field": "tax_ids.0"}]
    replayed = await client.post("/v1/companies", json=payload, headers=owner.posting("k-1"))
    assert (replayed.content, replayed.headers["idempotent-replayed"]) == (made.content, "true")  # warned too


async def test_a_company_holds_several_tax_ids_and_a_warning_names_the_one_it_is_about(
    client: httpx.AsyncClient, owner: TestPrincipal
) -> None:
    payload = body(
        {"scheme": "do.rnc", "value": "101-85004-3"}, {"scheme": "do.cedula", "value": "001-1391820-4"}
    )
    made = await client.post("/v1/companies", json=payload, headers=owner.posting("k-1"))
    assert made.status_code == 201
    listed = TAX_IDS.validate_python(document(made)["tax_ids"])
    assert [item["scheme"] for item in listed] == ["do.rnc", "do.cedula"]  # in order
    assert document(made)["warnings"] == [{"code": "tax_id_checksum_mismatch", "field": "tax_ids.1"}]


async def test_any_one_tax_id_of_a_company_that_is_held_is_a_conflict(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str
) -> None:
    del company
    payload = body({"scheme": "do.cedula", "value": "001-1391820-5"}, RNC)  # the cédula is free, the RNC held
    response = await client.post("/v1/companies", json=payload, headers=owner.posting("k-2"))
    assert (response.status_code, document(response)["code"]) == (409, "tax_id_taken")
    assert len(page(await client.get("/v1/companies", headers=owner.bearer()))["items"]) == 1


async def test_a_cedula_and_a_legal_name_of_three_hundred_characters_are_registered(
    client: httpx.AsyncClient, owner: TestPrincipal
) -> None:
    """The legal name's limit is identity's own (300): a channel enforces its own shorter one."""
    payload = body({"scheme": "do.cedula", "value": "001-1391820-5"}, legal_name="x" * 300)
    made = await client.post("/v1/companies", json=payload, headers=owner.posting("k-1"))
    assert made.status_code == 201
    assert (document(made)["tax_ids"], document(made)["legal_name"]) == (
        [{"scheme": "do.cedula", "value": "00113918205"}],
        "x" * 300,
    )


async def test_a_tax_id_the_organization_has_is_a_conflict(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str
) -> None:
    del company
    response = await client.post("/v1/companies", json=DUARTE, headers=owner.posting("k-2"))
    assert (response.status_code, document(response)["code"]) == (409, "tax_id_taken")


async def test_a_taxpayer_another_organization_holds_is_the_same_conflict_telling_nothing_more(
    client: httpx.AsyncClient, local_issuer: LocalIssuer, owner: TestPrincipal, company: str
) -> None:
    """One RNC, one active company, wherever it is: the answer is the one the caller's organization gets."""
    del company
    ours = await client.post("/v1/companies", json=DUARTE, headers=owner.posting("k-2"))
    other = TestPrincipal(local_issuer, organization="1002", roles=(ORGANIZATION_ADMIN,))
    theirs = await client.post("/v1/companies", json=DUARTE, headers=other.posting("k-1"))
    told, answered = document(theirs), document(ours)
    del told["request_id"], answered["request_id"]  # each request's own trace
    assert (theirs.status_code, told) == (409, answered)
    assert told["code"] == "tax_id_taken"


async def test_a_deactivated_company_s_taxpayer_may_be_registered_again(
    client: httpx.AsyncClient, local_issuer: LocalIssuer, owner: TestPrincipal, company: str
) -> None:
    headers = owner.posting("k-off") | {"If-Match": "*"}
    assert (await client.post(f"/v1/companies/{company}/deactivate", headers=headers)).status_code == 200
    other = TestPrincipal(local_issuer, organization="1002", roles=(ORGANIZATION_ADMIN,))
    theirs = await created(client, "/v1/companies", DUARTE, other.posting("k-1"))
    assert theirs != company


# ── Tokens ──────────────────────────────────────────────────────────────────────


async def test_a_token_that_cannot_be_trusted_is_refused_without_being_repeated(
    client: httpx.AsyncClient, local_issuer: LocalIssuer
) -> None:
    claims: dict[str, object] = {
        "iss": local_issuer.issuer,
        "aud": local_issuer.audience,
        "sub": "2001",
        "exp": FAR_FUTURE,
    }
    unsigned = f"{segment({'alg': 'none', 'kid': 'key-1'})}.{segment(claims)}."
    expired = local_issuer.token(claims={"exp": 1_000_000, "nbf": 0, "iat": 0})
    forged = LocalIssuer().token()  # signed with a key of its own, under the kid ``local_issuer`` publishes
    for token, problem in (
        (unsigned, "algorithm"),
        (expired, "expired"),
        (forged, "signature"),
        ("garbage", "malformed"),
        ("x" * 100_000, "malformed"),
    ):
        response = await client.get("/v1/companies", headers={"Authorization": f"Bearer {token}"})
        body = document(response)
        assert (response.status_code, body["code"], body["detail"]) == (
            401,
            "invalid_token",
            f"the access token was refused: {problem}",
        )
        assert token[:40] not in response.text


async def test_a_token_for_another_audience_is_refused(
    client: httpx.AsyncClient, local_issuer: LocalIssuer
) -> None:
    token = local_issuer.token(claims={"aud": ["another-api"]})
    response = await client.get("/v1/companies", headers={"Authorization": f"Bearer {token}"})
    assert (response.status_code, document(response)["detail"]) == (
        401,
        "the access token was refused: audience",
    )


async def test_an_id_token_of_the_same_project_is_refused_as_no_access_token(
    client: httpx.AsyncClient, local_issuer: LocalIssuer, owner: TestPrincipal, company: str
) -> None:
    """Shaped as Zitadel v4's ID token of a login: signed by the provider, the project among its audiences."""
    id_token = local_issuer.token(
        claims={
            "aud": ["web-app", local_issuer.audience],
            "azp": "web-app",
            "client_id": "web-app",
            "nonce": "a1b2c3d4",
            "amr": ["pwd"],
            "jti": None,
            "nbf": None,
        }
    )
    for path in ("/v1/companies", f"/v1/companies/{company}"):
        response = await client.get(path, headers={"Authorization": f"Bearer {id_token}"})
        body = document(response)
        assert (response.status_code, body["code"], body["detail"]) == (
            401,
            "invalid_token",
            "the access token was refused: not_access_token",
        )
        assert response.headers["WWW-Authenticate"] == 'Bearer realm="ledger", error="invalid_token"'
    assert (await client.get(f"/v1/companies/{company}", headers=owner.bearer())).status_code == 200


async def test_without_a_key_ever_fetched_a_token_cannot_be_checked(
    client: httpx.AsyncClient, local_issuer: LocalIssuer, owner: TestPrincipal
) -> None:
    local_issuer.failure = (HTTPStatus.BAD_GATEWAY, b"down")
    response = await client.get("/v1/companies", headers=owner.bearer())
    assert (response.status_code, document(response)["code"]) == (503, "token_keys_unavailable")


async def test_keys_held_keep_working_while_the_provider_is_down(
    client: httpx.AsyncClient, local_issuer: LocalIssuer, owner: TestPrincipal
) -> None:
    assert (await client.get("/v1/companies", headers=owner.bearer())).status_code == 200
    local_issuer.failure = (HTTPStatus.BAD_GATEWAY, b"down")
    assert (await client.get("/v1/companies", headers=owner.bearer())).status_code == 200
    assert local_issuer.fetches == 1


async def test_the_age_of_the_provider_s_keys_is_a_metric_of_the_service(
    client: httpx.AsyncClient, owner: TestPrincipal
) -> None:
    before = (await client.get("/metrics")).text
    assert "\nledger_auth_keys_age_seconds +Inf\n" in before
    await client.get("/v1/companies", headers=owner.bearer())
    after = (await client.get("/metrics")).text
    assert "\nledger_auth_keys_age_seconds " in after
    assert "\nledger_auth_keys_age_seconds +Inf\n" not in after


# ── Reading ─────────────────────────────────────────────────────────────────────


async def test_a_caller_pages_through_the_companies_it_holds_a_role_in(
    client: httpx.AsyncClient, local_issuer: LocalIssuer, owner: TestPrincipal
) -> None:
    for number, rnc in enumerate(("101850043", "130000012", "401007551")):
        payload = body({"scheme": "do.rnc", "value": rnc}, legal_name=f"Company {number}")
        await created(client, "/v1/companies", payload, owner.posting(f"k-{number}"))
    first = page(await client.get("/v1/companies", params={"limit": 2}, headers=owner.bearer()))
    assert len(first["items"]) == 2
    assert (first["has_more"], first["previous_cursor"]) == (True, None)
    rest = page(
        await client.get(
            "/v1/companies",
            params={"limit": 2, "starting_after": first["next_cursor"]},
            headers=owner.bearer(),
        )
    )
    assert (len(rest["items"]), rest["has_more"], rest["next_cursor"]) == (1, False, None)
    back = page(
        await client.get(
            "/v1/companies",
            params={"limit": 2, "ending_before": rest["previous_cursor"]},
            headers=owner.bearer(),
        )
    )
    assert (back["items"], back["has_more"], back["previous_cursor"]) == (first["items"], False, None)
    partner = page(
        await client.get("/v1/companies", headers=TestPrincipal(local_issuer, subject="2002").bearer())
    )
    stranger = page(
        await client.get("/v1/companies", headers=TestPrincipal(local_issuer, organization="1002").bearer())
    )
    assert (partner["items"], stranger["items"]) == ([], [])


@pytest.mark.parametrize(
    ("params", "code"),
    [
        ({"limit": 0}, "invalid_request"),
        ({"limit": 101}, "invalid_request"),
        ({"starting_after": "nope"}, "invalid_cursor"),
        ({"ending_before": "nope"}, "invalid_cursor"),
        (
            {"starting_after": "AQGJAAAAAHAAgAAAAAAAAAE", "ending_before": "AQGJAAAAAHAAgAAAAAAAAAE"},
            "page_cursors_conflict",
        ),
    ],
    ids=["no-items", "too-many", "foreign-cursor", "foreign-cursor-backwards", "both-cursors"],
)
async def test_a_page_that_cannot_be_asked_for_is_refused(
    client: httpx.AsyncClient, owner: TestPrincipal, params: dict[str, str | int], code: str
) -> None:
    response = await client.get("/v1/companies", params=params, headers=owner.bearer())
    assert (response.status_code, document(response)["code"]) == (422, code)


type Reach = tuple[str, str, dict[str, str]]
"""A request on a company: its method, what follows the company's path, and its headers beside the token."""


@pytest.mark.parametrize(
    "reach",
    [
        ("GET", "", {}),
        ("PATCH", "", {"If-Match": "*"}),
        ("POST", "/deactivate", {"If-Match": "*", "Idempotency-Key": "k-reach"}),
    ],
    ids=["read", "change", "deactivate"],
)
async def test_a_company_is_not_found_by_whoever_holds_no_role_in_it(
    client: httpx.AsyncClient, local_issuer: LocalIssuer, company: str, reach: Reach
) -> None:
    method, suffix, extra = reach
    for caller in (
        TestPrincipal(local_issuer, subject="2002"),
        TestPrincipal(local_issuer, organization="1002"),
    ):
        response = await client.request(
            method,
            f"/v1/companies/{company}{suffix}",
            json={"legal_name": "Taken over"} if method == "PATCH" else None,
            headers=caller.bearer() | extra,
        )
        assert (response.status_code, document(response)["code"]) == (404, "company_not_found")
    owner = TestPrincipal(local_issuer)
    unknown = await client.request(
        method, f"/v1/companies/{new_id()}{suffix}", headers=owner.bearer() | extra
    )
    assert (unknown.status_code, document(unknown)["code"]) == (404, "company_not_found")
    read = await client.get(f"/v1/companies/{company}", headers=owner.bearer())
    assert (document(read)["legal_name"], document(read)["status"], read.headers["etag"]) == (
        "Duarte",
        "active",
        '"1"',
    )


async def test_a_viewer_reads_but_does_not_change(
    client: httpx.AsyncClient, local_issuer: LocalIssuer, module_database: BoundDatabase, company: str
) -> None:
    await grant(module_database, company, "3001", Role.VIEWER)
    viewer = TestPrincipal(local_issuer, subject="3001")
    assert (await client.get(f"/v1/companies/{company}", headers=viewer.bearer())).status_code == 200
    listed = page(await client.get("/v1/companies", headers=viewer.bearer()))
    assert [item["id"] for item in listed["items"]] == [company]
    response = await client.patch(
        f"/v1/companies/{company}", json={"legal_name": "X"}, headers=viewer.bearer() | {"If-Match": '"1"'}
    )
    assert (response.status_code, document(response)["code"]) == (403, "permission_denied")
    deactivation = await client.post(
        f"/v1/companies/{company}/deactivate", headers=viewer.posting("k-v") | {"If-Match": '"1"'}
    )
    assert (deactivation.status_code, document(deactivation)["code"]) == (403, "permission_denied")


async def test_a_caller_without_the_permission_is_refused_before_its_preconditions_are_read(
    client: httpx.AsyncClient, local_issuer: LocalIssuer, module_database: BoundDatabase, company: str
) -> None:
    await grant(module_database, company, "3001", Role.VIEWER)
    viewer = TestPrincipal(local_issuer, subject="3001")
    unconditional = await client.patch(
        f"/v1/companies/{company}", json={"legal_name": "X"}, headers=viewer.bearer()
    )
    keyless = await client.post(f"/v1/companies/{company}/deactivate", headers=viewer.bearer())
    for response in (unconditional, keyless):
        assert (response.status_code, document(response)["code"]) == (403, "permission_denied")


async def test_an_admin_demoted_while_its_deactivation_waits_for_the_company_cannot_deactivate(
    client: httpx.AsyncClient,
    local_issuer: LocalIssuer,
    module_database: BoundDatabase,
    migrated: WorkerDatabase,
    company: str,
) -> None:
    await grant(module_database, company, "2002", Role.ADMIN)
    tenant = Tenant(organization_of("1001"), CompanyId(parse_id(COMPANY_PREFIX, company)), None)
    headers = TestPrincipal(local_issuer, subject="2002").posting("k-off") | {"If-Match": '"1"'}
    outside = contextvars.copy_context()  # the request is another client's: it holds no unit of the test's
    async with module_database.unit(tenant) as unit:  # holds the company's lock
        await commands.grant_role(
            bind(unit),
            tenant.company,
            subject=SubjectId("2002"),
            role=Role.VIEWER,
            actor=SubjectId("2001"),
        )
        waiting = asyncio.create_task(
            client.post(f"/v1/companies/{company}/deactivate", headers=headers), context=outside
        )
        await eventually(partial(waits_for_a_lock, app_url(migrated)))
    refused = await waiting
    assert (refused.status_code, document(refused)["code"]) == (403, "permission_denied")
    read = await client.get(f"/v1/companies/{company}", headers=TestPrincipal(local_issuer).bearer())
    assert (document(read)["status"], read.headers["etag"]) == ("active", '"1"')


async def test_a_company_id_that_is_not_an_id_finds_no_company(
    client: httpx.AsyncClient, owner: TestPrincipal
) -> None:
    response = await client.get("/v1/companies/not-an-id", headers=owner.bearer())
    assert (response.status_code, document(response)["code"]) == (404, "company_not_found")


# ── Changing ────────────────────────────────────────────────────────────────────


async def test_a_change_needs_the_version_it_was_made_from(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str
) -> None:
    path = f"/v1/companies/{company}"
    missing = await client.patch(path, json={"legal_name": "Duarte Hermanos"}, headers=owner.bearer())
    assert (missing.status_code, document(missing)["code"]) == (428, "precondition_required")
    for stale_tag in ('"7"', 'W/"1"', "1"):
        stale = await client.patch(
            path, json={"legal_name": "Duarte Hermanos"}, headers=owner.bearer() | {"If-Match": stale_tag}
        )
        assert (stale.status_code, document(stale)["code"]) == (412, "precondition_failed")
    applied = await client.patch(
        path, json={"legal_name": "Duarte Hermanos"}, headers=owner.bearer() | {"If-Match": '"7", "1"'}
    )
    assert (applied.status_code, applied.headers["etag"], document(applied)["legal_name"]) == (
        200,
        '"2"',
        "Duarte Hermanos",
    )
    anything = await client.patch(
        path, json={"legal_name": "Duarte e Hijos"}, headers=owner.bearer() | {"If-Match": "*"}
    )
    assert anything.headers["etag"] == '"3"'


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        ({"legal_name": " "}, "invalid_name"),
        ({}, "invalid_request"),
        ({"status": "inactive"}, "invalid_request"),
        ({"jurisdiction": {"country": "US"}}, "jurisdiction_unsupported"),
        ({"functional_currency": "USD"}, "currency_unsupported"),
        ({"tax_ids": []}, "invalid_request"),
        ({"tax_ids": [{"scheme": "do.rnc", "value": "130000012"}] * 2}, "tax_id_duplicated"),
    ],
    ids=["blank-name", "nothing", "unknown-field", "no-regime", "currency", "no-tax-id", "a-tax-id-twice"],
)
async def test_a_change_that_cannot_be_made_is_refused(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str, payload: dict[str, str], code: str
) -> None:
    response = await client.patch(
        f"/v1/companies/{company}", json=payload, headers=owner.bearer() | {"If-Match": '"1"'}
    )
    assert (response.status_code, document(response)["code"]) == (422, code)


async def test_until_it_is_locked_a_profile_changes_and_the_registry_follows_its_tax_ids(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str
) -> None:
    """``tax_ids`` is the whole list: the id it drops is released at once, the one it adds is held."""
    path = f"/v1/companies/{company}"
    refiled = await client.patch(
        path,
        json={"tax_ids": [{"scheme": "do.rnc", "value": "130000012"}], "legal_name": "Duarte Hermanos"},
        headers=owner.bearer() | {"If-Match": '"1"'},
    )
    assert (refiled.status_code, refiled.headers["etag"]) == (200, '"2"')  # one change, one version
    assert (document(refiled)["tax_ids"], document(refiled)["legal_name"]) == (
        [{"scheme": "do.rnc", "value": "130000012"}],
        "Duarte Hermanos",
    )
    released = await client.post("/v1/companies", json=DUARTE, headers=owner.posting("c-again"))
    held = await client.post(
        "/v1/companies",
        json=body({"scheme": "do.rnc", "value": "130-00001-2"}),
        headers=owner.posting("c-held"),
    )
    assert (released.status_code, held.status_code, document(held)["code"]) == (201, 409, "tax_id_taken")


async def test_a_new_jurisdiction_names_the_zone_its_main_establishment_keeps(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str
) -> None:
    """The old regime's zone would be stale: a change of jurisdiction says the new one, for ``MAIN``."""
    path = f"/v1/companies/{company}"
    moved = {"jurisdiction": {"country": "DO", "subdivision": "DO-01"}}
    zoneless = await client.patch(path, json=moved, headers=owner.bearer() | {"If-Match": '"1"'})
    assert (zoneless.status_code, document(zoneless)["code"]) == (422, "time_zone_required")
    zoned = moved | {"time_zone": "America/Puerto_Rico"}
    changed = await client.patch(path, json=zoned, headers=owner.bearer() | {"If-Match": '"1"'})
    assert changed.status_code == 200
    (main,) = page(await client.get(f"{path}/establishments", headers=owner.bearer()))["items"]
    assert (main["code"], main["time_zone"]) == ("MAIN", "America/Puerto_Rico")
    stray = await client.patch(
        path, json={"time_zone": "America/Santo_Domingo"}, headers=owner.bearer() | {"If-Match": '"2"'}
    )
    assert (stray.status_code, document(stray)["code"]) == (422, "time_zone_unused")


async def test_a_company_with_establishments_besides_main_keeps_its_jurisdiction(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str
) -> None:
    """Their zones were chosen under the old jurisdiction: identity will not guess them anew."""
    path = f"/v1/companies/{company}"
    opened = await client.post(
        f"{path}/establishments", json={"code": "SUC1", "name": "Santiago"}, headers=owner.posting("e-1")
    )
    assert opened.status_code == 201
    moved = {"jurisdiction": {"country": "DO", "subdivision": "DO-01"}, "time_zone": "America/Puerto_Rico"}
    refused = await client.patch(path, json=moved, headers=owner.bearer() | {"If-Match": '"1"'})
    assert (refused.status_code, document(refused)["code"]) == (409, "establishments_exist")


async def test_a_profile_cannot_take_a_tax_id_another_company_holds(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str
) -> None:
    other = await created(
        client, "/v1/companies", body({"scheme": "do.rnc", "value": "130000012"}), owner.posting("c-2")
    )
    taken = await client.patch(
        f"/v1/companies/{company}",
        json={
            "tax_ids": [
                {"scheme": "do.rnc", "value": "101850043"},
                {"scheme": "do.rnc", "value": "130000012"},
            ]
        },
        headers=owner.bearer() | {"If-Match": '"1"'},
    )
    assert (taken.status_code, document(taken)["code"]) == (409, "tax_id_taken")
    read = await client.get(f"/v1/companies/{company}", headers=owner.bearer())
    assert (read.headers["etag"], len(TAX_IDS.validate_python(document(read)["tax_ids"]))) == ('"1"', 1)
    assert other


async def test_a_locked_profile_changes_no_more_though_the_name_still_does(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str, admin: psycopg.AsyncConnection
) -> None:
    await admin.execute("UPDATE identity.companies SET fiscal_profile_locked_at = now()")
    path = f"/v1/companies/{company}"
    for change in (
        {"functional_currency": "DOP", "tax_ids": [{"scheme": "do.rnc", "value": "130000012"}]},
        {"jurisdiction": {"country": "DO", "subdivision": "DO-01"}},
    ):
        refused = await client.patch(path, json=change, headers=owner.bearer() | {"If-Match": "*"})
        assert (refused.status_code, document(refused)["code"]) == (409, "fiscal_profile_locked")
    same = await client.patch(
        path, json={"functional_currency": "DOP"}, headers=owner.bearer() | {"If-Match": "*"}
    )
    renamed = await client.patch(
        path, json={"legal_name": "Duarte e Hijos"}, headers=owner.bearer() | {"If-Match": "*"}
    )
    assert (same.status_code, same.headers["etag"]) == (200, '"1"')  # what it is already is no change
    assert (renamed.status_code, renamed.headers["etag"]) == (200, '"2"')


# ── The fiscal address (Task 35) ────────────────────────────────────────────────

ADDRESS: Final = {"line": "Av. Duarte 10, Villa Consuelo", "municipality": "010100", "province": "010000"}
"""An address in Santo Domingo de Guzmán, Distrito Nacional: codes of DGII's catalogue (regime ``do``)."""


async def test_a_company_is_registered_with_its_fiscal_address_or_without_one(
    client: httpx.AsyncClient, owner: TestPrincipal
) -> None:
    sent = DUARTE | {"address": {"line": "  Av. Duarte 	 10, Villa Consuelo ", "province": "010000"}}
    made = await client.post("/v1/companies", json=sent, headers=owner.posting("k-address"))
    assert made.status_code == 201, made.text
    assert document(made)["address"] == {
        "line": "Av. Duarte 10, Villa Consuelo",
        "municipality": None,
        "province": "010000",
    }
    read = await client.get(str(made.headers["location"]), headers=owner.bearer())
    assert document(read)["address"] == document(made)["address"]
    other = body({"scheme": "do.rnc", "value": "130000012"})
    bare = await client.post("/v1/companies", json=other, headers=owner.posting("k-bare"))
    assert document(bare)["address"] is None


@pytest.mark.parametrize(
    ("address", "code", "field"),
    [
        ({"line": "x" * 101}, "invalid_name", "address.line is longer than 100 characters"),
        ({"line": "   "}, "invalid_name", "address.line is empty"),
        ({"line": "Calle 1", "municipality": "999999"}, "address_code_unknown", "address.municipality "),
        ({"line": "Calle 1", "province": "01-00"}, "address_code_unknown", "address.province "),
        ({"municipality": "010100"}, "invalid_request", "line"),
        ({"line": "Calle 1", "zip": "10101"}, "invalid_request", "zip"),
    ],
)
async def test_an_address_the_dominican_regime_does_not_take_is_refused(
    client: httpx.AsyncClient, owner: TestPrincipal, address: dict[str, str], code: str, field: str
) -> None:
    """DGII's ``DireccionEmisor`` holds 100 characters; ``Municipio`` and ``Provincia``, its catalogue's."""
    refused = await client.post(
        "/v1/companies", json=DUARTE | {"address": address}, headers=owner.posting("k-x")
    )
    assert (refused.status_code, document(refused)["code"]) == (422, code), refused.text
    assert field in refused.text
    assert "999999" not in refused.text


async def test_a_company_moves_its_address_or_clears_it_even_once_its_profile_is_locked(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str, admin: psycopg.AsyncConnection
) -> None:
    await admin.execute("UPDATE identity.companies SET fiscal_profile_locked_at = now()")
    path = f"/v1/companies/{company}"
    moved = await client.patch(path, json={"address": ADDRESS}, headers=owner.bearer() | {"If-Match": '"1"'})
    assert (moved.status_code, moved.headers["etag"], document(moved)["address"]) == (200, '"2"', ADDRESS)
    same = await client.patch(path, json={"address": ADDRESS}, headers=owner.bearer() | {"If-Match": '"2"'})
    assert (same.status_code, same.headers["etag"]) == (200, '"2"')  # what it is already is no change
    renamed = await client.patch(
        path, json={"legal_name": "Duarte e Hijos"}, headers=owner.bearer() | {"If-Match": "*"}
    )
    assert document(renamed)["address"] == ADDRESS  # a change that names no address keeps it
    cleared = await client.patch(path, json={"address": None}, headers=owner.bearer() | {"If-Match": "*"})
    assert (cleared.status_code, document(cleared)["address"], cleared.headers["etag"]) == (200, None, '"4"')
    refused = await client.patch(
        path,
        json={"address": {"line": "Calle 1", "province": "999999"}},
        headers=owner.bearer() | {"If-Match": "*"},
    )
    assert (refused.status_code, document(refused)["code"]) == (422, "address_code_unknown")


COMPANY_ACTIONS: Final = (
    "SELECT action FROM identity.audit_events WHERE subject_type = 'company' ORDER BY occurred_at, action"
)


async def test_a_change_of_the_address_is_audited_as_such(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str, admin: psycopg.AsyncConnection
) -> None:
    path = f"/v1/companies/{company}"
    both = {"address": ADDRESS, "legal_name": "Duarte e Hijos"}
    changed = await client.patch(path, json=both, headers=owner.bearer() | {"If-Match": '"1"'})
    assert changed.status_code == 200, changed.text
    rows = await admin.execute(COMPANY_ACTIONS)
    assert [row[0] for row in await rows.fetchall()] == [
        "company.register",
        "company.change_address",
        "company.rename",
    ]


async def test_of_two_changes_of_one_version_only_one_applies(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str
) -> None:
    headers = owner.bearer() | {"If-Match": '"1"'}
    responses = await asyncio.gather(
        client.patch(f"/v1/companies/{company}", json={"legal_name": "One"}, headers=headers),
        client.patch(f"/v1/companies/{company}", json={"legal_name": "Other"}, headers=headers),
    )
    assert sorted(response.status_code for response in responses) == [200, 412]
    (won,) = (response for response in responses if response.status_code == 200)
    read = await client.get(f"/v1/companies/{company}", headers=owner.bearer())
    assert (read.headers["etag"], document(read)["legal_name"]) == ('"2"', document(won)["legal_name"])


async def test_a_deactivated_company_changes_no_more_and_the_deactivation_replays(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str
) -> None:
    path = f"/v1/companies/{company}/deactivate"
    headers = owner.posting("k-off") | {"If-Match": '"1"'}
    deactivated = await client.post(path, headers=headers)
    assert (deactivated.status_code, document(deactivated)["status"], deactivated.headers["etag"]) == (
        200,
        "inactive",
        '"2"',
    )
    assert "location" not in deactivated.headers  # a 201's header only
    again = await client.post(path, headers=headers)
    assert (again.content, again.headers["idempotent-replayed"]) == (deactivated.content, "true")
    change = await client.patch(
        f"/v1/companies/{company}", json={"legal_name": "X"}, headers=owner.bearer() | {"If-Match": "*"}
    )
    assert (change.status_code, document(change)["code"]) == (409, "company_inactive")
    stale = await client.post(path, headers=owner.posting("k-off-2") | {"If-Match": '"1"'})
    assert (stale.status_code, document(stale)["code"]) == (412, "precondition_failed")
    read = await client.get(f"/v1/companies/{company}", headers=owner.bearer())
    assert (read.status_code, document(read)["status"]) == (200, "inactive")


async def test_a_deactivation_needs_a_key_and_a_version(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str
) -> None:
    path = f"/v1/companies/{company}/deactivate"
    keyless = await client.post(path, headers=owner.bearer() | {"If-Match": '"1"', "Ledger-Mode": "test"})
    assert (keyless.status_code, document(keyless)["code"]) == (422, "invalid_request")
    modeless = await client.post(path, headers=owner.bearer() | {"If-Match": '"1"', "Idempotency-Key": "k"})
    assert (modeless.status_code, document(modeless)["code"]) == (400, "mode_required")
    unconditional = await client.post(path, headers=owner.posting("k-off"))
    assert (unconditional.status_code, document(unconditional)["code"]) == (428, "precondition_required")


async def test_a_deactivation_that_failed_frees_its_key_for_the_retry(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str
) -> None:
    path = f"/v1/companies/{company}/deactivate"
    failed = await client.post(path, headers=owner.posting("k-off") | {"If-Match": '"7"'})
    assert failed.status_code == 412
    retried = await client.post(path, headers=owner.posting("k-off") | {"If-Match": '"1"'})
    assert (retried.status_code, document(retried)["status"]) == (200, "inactive")
    assert "idempotent-replayed" not in retried.headers


async def test_two_deactivations_with_one_key_at_once_deactivate_once(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str
) -> None:
    headers = owner.posting("k-off") | {"If-Match": '"1"'}
    path = f"/v1/companies/{company}/deactivate"
    first, second = await asyncio.gather(
        client.post(path, headers=headers), client.post(path, headers=headers)
    )
    assert (first.status_code, second.status_code, first.content) == (200, 200, second.content)
    assert sorted(response.headers.get("idempotent-replayed", "no") for response in (first, second)) == [
        "no",
        "true",
    ]
