import httpx
import pytest
from joserfc import jwt

from ledger_testing.api import Caller, NotCreatedError, created, document, page
from ledger_testing.issuer import LocalIssuer


def answering(status: int, body: dict[str, object]) -> httpx.AsyncClient:
    """A client of an app that answers every request with ``status`` and ``body``."""

    def answer(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(status, json=body)

    return httpx.AsyncClient(transport=httpx.MockTransport(answer), base_url="http://service.test")


async def test_what_a_post_made_is_known_by_its_id() -> None:
    async with answering(201, {"id": "thing-1"}) as client:
        assert await created(client, "/v1/things", {"name": "thing"}, {}) == "thing-1"


async def test_a_post_that_made_nothing_says_what_came_back() -> None:
    async with answering(409, {"code": "taken"}) as client:
        with pytest.raises(NotCreatedError, match=r"got 409: .*taken"):
            await created(client, "/v1/things", {"name": "thing"}, {})


def test_a_caller_s_headers_carry_a_token_the_key_of_its_post_and_its_mode() -> None:
    caller = Caller(LocalIssuer(), subject="3001", organization="1002")
    headers = caller.posting("k-1")
    assert (headers["Idempotency-Key"], headers["Ledger-Mode"]) == ("k-1", "test")
    assert headers["Authorization"].startswith("Bearer ey")
    assert caller.posting("k-2", mode="live")["Ledger-Mode"] == "live"


def test_a_caller_s_token_carries_the_project_roles_it_was_given() -> None:
    issuer = LocalIssuer()
    bearer = Caller(issuer, roles=("ledger.organization.admin",)).bearer()["Authorization"]
    claims = jwt.decode(bearer.removeprefix("Bearer "), issuer.signing_key()).claims
    assert claims[f"urn:zitadel:iam:org:project:{issuer.audience}:roles"] == {
        "ledger.organization.admin": {"1001": "ledger.test"}
    }
    plain = Caller(issuer).bearer()["Authorization"]
    assert (
        f"urn:zitadel:iam:org:project:{issuer.audience}:roles"
        not in jwt.decode(plain.removeprefix("Bearer "), issuer.signing_key()).claims
    )


def test_pages_and_documents_are_read_as_json() -> None:
    answered = {"items": [{"id": "a"}], "has_more": False, "next_cursor": None, "previous_cursor": None}
    response = httpx.Response(200, json=answered)
    assert page(response) == answered
    assert document(response)["next_cursor"] is None
