import httpx
import pytest

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


def test_a_caller_s_headers_carry_a_token_and_the_key_of_its_post() -> None:
    headers = Caller(LocalIssuer(), subject="3001", organization="1002").posting("k-1")
    assert headers["Idempotency-Key"] == "k-1"
    assert headers["Authorization"].startswith("Bearer ey")


def test_pages_and_documents_are_read_as_json() -> None:
    response = httpx.Response(200, json={"items": [{"id": "a"}], "next_cursor": None})
    assert page(response) == {"items": [{"id": "a"}], "next_cursor": None}
    assert document(response)["next_cursor"] is None
