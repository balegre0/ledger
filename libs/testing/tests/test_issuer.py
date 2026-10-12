import gzip
from http import HTTPStatus

import httpx
import pytest
from joserfc import jwt
from joserfc.jwk import KeySet, KeySetSerialization
from pydantic import TypeAdapter

from ledger_testing.issuer import (
    AUDIENCE,
    ISSUER,
    ORGANIZATION_CLAIM,
    PROVIDER_ORGANIZATION,
    SUBJECT,
    LocalIssuer,
)

JWKS = TypeAdapter(KeySetSerialization)
CLAIMS = TypeAdapter(dict[str, object])
HEADER = TypeAdapter(dict[str, str])


def keys_of(issuer: LocalIssuer) -> KeySet:
    return KeySet.import_key_set(JWKS.validate_json(issuer.published()))


def kids(issuer: LocalIssuer) -> list[str | None]:
    return [key.kid for key in keys_of(issuer)]


def claims_of(issuer: LocalIssuer, token: str) -> dict[str, object]:
    return CLAIMS.validate_python(jwt.decode(token, keys_of(issuer), algorithms=["RS256"]).claims)


async def test_the_keys_are_served_over_http_and_every_request_is_counted() -> None:
    async with LocalIssuer.started() as issuer, httpx.AsyncClient() as client:
        response = await client.get(issuer.keys_url)
        again = await client.get(issuer.keys_url)
    assert (response.status_code, response.content) == (200, issuer.published())
    assert response.headers["content-type"] == "application/json"
    assert again.status_code == 200
    assert issuer.fetches == 2
    assert issuer.keys_url.startswith("http://127.0.0.1:")


async def test_the_endpoint_answers_what_it_is_told_to() -> None:
    async with LocalIssuer.started() as issuer, httpx.AsyncClient() as client:
        issuer.failure = (HTTPStatus.SERVICE_UNAVAILABLE, b"down")
        response = await client.get(issuer.keys_url)
    assert (response.status_code, response.content) == (503, b"down")


async def test_the_endpoint_can_say_its_answer_is_encoded_and_keeps_every_request_head() -> None:
    async with LocalIssuer.started() as issuer, httpx.AsyncClient() as client:
        issuer.encoding = "gzip"
        issuer.failure = (HTTPStatus.OK, gzip.compress(b"{}"))
        response = await client.get(issuer.keys_url, headers={"X-Asked-By": "test"})
    assert (response.headers["content-encoding"], response.content) == ("gzip", b"{}")
    assert b"\r\nx-asked-by: test\r\n" in issuer.requests[0].lower()


async def test_a_request_still_waiting_when_the_issuer_stops_is_dropped() -> None:
    async with LocalIssuer.started() as issuer, httpx.AsyncClient(timeout=0.1) as client:
        issuer.delay = 30.0
        with pytest.raises(httpx.ReadTimeout):
            await client.get(issuer.keys_url)
    assert issuer.fetches == 1


def test_a_token_verifies_with_the_published_keys_and_carries_zitadels_claims() -> None:
    issuer = LocalIssuer()
    claims = claims_of(issuer, issuer.token(roles=("operator",)))
    assert claims["iss"] == ISSUER
    assert claims["aud"] == [AUDIENCE]
    assert claims["sub"] == SUBJECT
    assert claims[ORGANIZATION_CLAIM] == PROVIDER_ORGANIZATION
    assert claims[f"urn:zitadel:iam:org:project:{AUDIENCE}:roles"] == {
        "operator": {PROVIDER_ORGANIZATION: "ledger.test"}
    }
    assert {"exp", "iat", "nbf", "client_id"} <= claims.keys()


def test_claims_can_be_changed_and_left_out() -> None:
    issuer = LocalIssuer()
    claims = claims_of(issuer, issuer.token(claims={"aud": "elsewhere", "sub": None}))
    assert claims["aud"] == "elsewhere"
    assert "sub" not in claims
    assert f"urn:zitadel:iam:org:project:{AUDIENCE}:roles" not in claims


def test_a_new_key_is_published_beside_the_old_one_until_that_one_is_retired() -> None:
    issuer = LocalIssuer()
    assert issuer.rotate() == "key-2"
    assert kids(issuer) == ["key-1", "key-2"]
    issuer.retire("key-1")
    assert kids(issuer) == ["key-2"]
    newest = HEADER.validate_python(jwt.decode(issuer.token(), keys_of(issuer), algorithms=["RS256"]).header)
    assert newest["kid"] == "key-2"


def test_the_signing_key_is_the_newest_unless_another_is_named() -> None:
    issuer = LocalIssuer()
    issuer.rotate()
    assert issuer.signing_key().kid == "key-2"
    assert issuer.signing_key("key-1").kid == "key-1"
    assert issuer.token(kid="key-1").count(".") == 2
