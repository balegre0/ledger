"""The request side of ``Idempotency-Key`` (§9.5): the header, the route a key is scoped to, the answer."""

import uuid
from contextlib import asynccontextmanager
from typing import Annotated, ClassVar, Final
lazy from collections.abc import AsyncGenerator

import httpx
import pytest
from fastapi import APIRouter, Depends, FastAPI
from opentelemetry.sdk.trace import TracerProvider
from prometheus_client import CollectorRegistry
from pydantic import BaseModel, ConfigDict
from structlog.testing import capture_logs
lazy from fastapi import Request

from ledger_kernel.access import Principal, SubjectId
from ledger_kernel.ids import CompanyId
from ledger_platform.auth.providers.zitadel import organization_of
from ledger_platform.http.app import ApiInfo, Observability, create_app
from ledger_platform.http.idempotency import (
    MAX_KEY_LENGTH,
    REPLAYED_HEADER,
    Attempt,
    CanonicalBody,
    InvalidIdempotencyKeyError,
    Outcome,
    Target,
    UnlabelledRouteError,
    answer,
    attempt_of,
    canonical_body,
    idempotency_key,
    json_response,
)
from ledger_platform.http.problems import install_problem_handlers
from ledger_platform.observability.health import Health

CALLER: Final = Principal(
    subject=SubjectId("2001"), organization=organization_of("1001"), provider_organization="1001"
)
KEYED: Final = {"Idempotency-Key": "k", "Content-Type": "application/json"}


class Note(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    text: str
    pinned: bool = False


class Seen(BaseModel):
    route: str
    key: str
    principal: str
    request_hash: str


def keyed(prefix: str = "") -> APIRouter:
    """A router whose one route answers what ``attempt_of`` made of its request."""
    router = APIRouter(prefix=prefix)

    @router.post("/{item_id}")
    async def create(
        request: Request, item_id: int, note: Note, key: Annotated[str, Depends(idempotency_key)]
    ) -> Seen:
        del item_id
        tried = attempt_of(request, CALLER, key, note, company=None)
        return Seen(
            route=tried.route, key=tried.key, principal=tried.principal, request_hash=tried.request_hash.hex()
        )

    return router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    del app
    yield


def chassis_app(*routers: APIRouter) -> FastAPI:
    observability = Observability(
        health=Health(), registry=CollectorRegistry(), tracer_provider=TracerProvider()
    )
    built = create_app(info=ApiInfo(title="t", version="1"), observability=observability, lifespan=lifespan)
    for router in routers:
        built.include_router(router)
    return built


def mounted(*mounts: tuple[str, APIRouter]) -> FastAPI:
    """A chassis app with each router included under its prefix before the app is built."""
    observability = Observability(
        health=Health(), registry=CollectorRegistry(), tracer_provider=TracerProvider()
    )
    outer = [APIRouter(prefix=prefix) for prefix, _ in mounts]
    for parent, (_, router) in zip(outer, mounts, strict=True):
        parent.include_router(router)
    return create_app(
        info=ApiInfo(title="t", version="1"), observability=observability, lifespan=lifespan, routers=outer
    )


def client_of(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def seen(app: FastAPI, path: str, body: object, key: str = "k-1") -> Seen:
    async with client_of(app) as client:
        response = await client.post(path, json=body, headers={"Idempotency-Key": key})
    assert response.status_code == 200, response.text
    return Seen.model_validate_json(response.content)


async def test_a_key_is_scoped_to_the_full_template_of_its_route() -> None:
    shared = keyed()
    app = mounted(("/v1/a", shared), ("/v1/b", keyed()))
    first = await seen(app, "/v1/a/1", {"text": "x"})
    second = await seen(app, "/v1/b/1", {"text": "x"})
    other_item = await seen(app, "/v1/a/2", {"text": "x"})
    assert (first.route, second.route) == ("POST /v1/a/{item_id}", "POST /v1/b/{item_id}")
    assert first.request_hash != second.request_hash
    assert other_item.route == first.route  # one route, one scope for its keys
    assert other_item.request_hash != first.request_hash  # but another resource is another request
    assert (first.key, first.principal) == ("k-1", "user:2001")


async def test_the_query_counts_in_its_canonical_order() -> None:
    app = mounted(("/v1", keyed()))
    sorted_query = await seen(app, "/v1/1?a=1&b=2&a=0", {"text": "x"})
    shuffled = await seen(app, "/v1/1?b=2&a=0&a=1", {"text": "x"})
    other_value = await seen(app, "/v1/1?a=1&b=3&a=0", {"text": "x"})
    without = await seen(app, "/v1/1", {"text": "x"})
    assert sorted_query.request_hash == shuffled.request_hash
    assert len({sorted_query.request_hash, other_value.request_hash, without.request_hash}) == 3


async def test_the_same_body_in_another_order_or_spacing_is_the_same_request() -> None:
    app = mounted(("/v1", keyed()))
    async with client_of(app) as client:
        compact = await client.post("/v1/1", content=b'{"text":"x","pinned":false}', headers=KEYED)
        spaced = await client.post("/v1/1", content=b'{ "pinned": false,\n "text": "x" }', headers=KEYED)
        defaulted = await client.post("/v1/1", content=b'{"text":"x"}', headers=KEYED)
    hashes = {
        Seen.model_validate_json(answered.content).request_hash for answered in (compact, spaced, defaulted)
    }
    assert len(hashes) == 1


async def test_an_app_without_the_chassis_s_route_labels_has_no_key_scope() -> None:
    """Without ``create_app``'s labels the full template is unknown: refused rather than guessed."""
    app = FastAPI()
    install_problem_handlers(app)
    app.include_router(keyed("/v1/notes"))
    async with client_of(app) as client:
        with capture_logs() as logs:
            response = await client.post("/v1/notes/1", json={"text": "x"}, headers=KEYED)
    assert (response.status_code, response.json()["code"]) == (500, "internal")
    assert [entry["code"] for entry in logs] == ["idempotency_route_unlabelled"]


async def test_a_route_added_after_the_app_was_built_has_no_key_scope() -> None:
    late = APIRouter()
    app = chassis_app(late)

    @late.post("/late")
    async def handle(request: Request, key: Annotated[str, Depends(idempotency_key)]) -> None:
        attempt_of(request, CALLER, key, company=None)

    async with client_of(app) as client:
        with capture_logs() as logs:
            response = await client.post("/late", headers=KEYED)
    assert (response.status_code, response.json()["code"]) == (500, "internal")
    assert {entry["event"] for entry in logs} >= {"http.unhandled_error"}
    assert UnlabelledRouteError().code == "idempotency_route_unlabelled"


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Idempotency-Key": ""},
        {"Idempotency-Key": "a b"},
        {"Idempotency-Key": "k" * (MAX_KEY_LENGTH + 1)},
    ],
    ids=["missing", "empty", "space", "too-long"],
)
async def test_a_post_without_a_usable_key_is_refused_without_repeating_it(headers: dict[str, str]) -> None:
    async with client_of(mounted(("/v1", keyed()))) as client:
        response = await client.post("/v1/1", json={"text": "x"}, headers=headers)
    assert response.status_code == 422
    assert "kkkkkkkk" not in response.text
    assert "a b" not in response.text


@pytest.mark.parametrize("key", ["", "a b", "é", "k" * (MAX_KEY_LENGTH + 1)])
def test_an_attempt_refuses_a_key_no_header_could_carry_and_never_repeats_it(key: str) -> None:
    with pytest.raises(InvalidIdempotencyKeyError) as caught:
        Attempt.of(
            CALLER,
            key,
            route="POST /v1/x",
            target=Target.of("/v1/x"),
            body=canonical_body(None),
        )
    assert caught.value.code == "invalid_idempotency_key"
    assert key not in str(caught.value) or not key


def test_route_and_body_cannot_run_into_each_other() -> None:
    def hashed(route: str, path: str, query: str, body: bytes) -> bytes:
        return Attempt.of(
            CALLER, "k", route=route, target=Target(path, query), body=CanonicalBody(body)
        ).request_hash

    assert hashed("POST /a", "/a", "", b"b") != hashed("POST /a", "/a", "b", b"")
    assert hashed("POST /a", "/a", "b", b"") != hashed("POST /a", "/ab", "", b"")
    assert hashed("POST /a", "/a", "", b"") != hashed("POST /a/", "", "", b"")


def test_a_replay_is_answered_byte_for_byte_and_marked() -> None:
    stored = json_response(201, Note(text="x"), {"Location": "/v1/notes/1"})
    first, again = answer(Outcome(stored, replayed=False)), answer(Outcome(stored, replayed=True))
    assert (first.status_code, first.body) == (201, b'{"text":"x","pinned":false}')
    assert REPLAYED_HEADER not in first.headers
    assert (again.body, again.headers[REPLAYED_HEADER]) == (first.body, "true")
    assert again.headers["location"] == "/v1/notes/1"


def test_the_company_a_request_acts_for_is_part_of_the_request() -> None:
    """A company named by a header is in neither path nor body: another company is another request."""

    def fingerprint(company: CompanyId | None) -> bytes:
        return Attempt.of(
            CALLER,
            "k",
            route="POST /v1/invoices",
            target=Target.of("/v1/invoices", company=company),
            body=canonical_body(None),
        ).request_hash

    first, second = CompanyId(uuid.uuid7()), CompanyId(uuid.uuid7())
    assert len({fingerprint(first), fingerprint(second), fingerprint(None)}) == 3
    assert fingerprint(first) == fingerprint(first)
