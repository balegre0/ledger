import asyncio
from contextlib import asynccontextmanager
from http import HTTPStatus
from typing import ClassVar, Final, TypedDict
lazy from collections.abc import AsyncGenerator, MutableMapping

import httpx
import pytest
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from prometheus_client import CollectorRegistry
from pydantic import BaseModel, ConfigDict, TypeAdapter
from structlog.testing import capture_logs
lazy from fastapi import FastAPI

from ledger_kernel.errors import (
    ConflictError,
    ForbiddenError,
    InvalidInputError,
    LedgerError,
    NotFoundError,
    UnavailableError,
)
from ledger_platform.health import Health
from ledger_platform.web import PROBLEM_JSON, AmbiguousRouteError, ApiInfo, create_app


class MissingItemError(NotFoundError):
    code: ClassVar[str] = "missing_item"

    def __init__(self, item: int) -> None:
        super().__init__(f"item {item} does not exist")


class BrokenInvariantError(LedgerError):
    """A defect of ours that carries a message meant for the log, not for a client."""

    code: ClassVar[str] = "broken_invariant"

    def __init__(self) -> None:
        super().__init__("the ledger is out of balance by 3")


class Item(BaseModel):
    name: str
    quantity: int


class Strict(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    name: str


class FieldProblem(TypedDict):
    location: str
    message: str


class InvalidRequest(TypedDict):
    code: str
    errors: list[FieldProblem]
    omitted_errors: int


class OpenApiDocument(TypedDict):
    paths: dict[str, object]


INVALID_REQUEST: Final = TypeAdapter(InvalidRequest)
OPENAPI_DOCUMENT: Final = TypeAdapter(OpenApiDocument)

router = APIRouter()
ERRORS: dict[str, type[LedgerError]] = {
    "invalid": InvalidInputError,
    "conflict": ConflictError,
    "forbidden": ForbiddenError,
    "unavailable": UnavailableError,
    "internal": LedgerError,
    "defect": BrokenInvariantError,
}


@router.get("/items/{item_id}")
async def read_item(item_id: int) -> dict[str, int]:
    if item_id == 404:
        raise MissingItemError(item_id)
    return {"id": item_id}


@router.post("/items")
async def create_item(item: Item) -> Item:
    return item


@router.post("/strict", include_in_schema=False)
async def strict(item: Strict) -> Strict:
    return item


@router.post("/batches", include_in_schema=False)
async def batch(items: list[Item]) -> list[Item]:
    return items


@router.get("/secure", include_in_schema=False)
async def secure() -> None:
    raise HTTPException(
        status_code=401, detail="token 4f9a expired", headers={"WWW-Authenticate": 'Bearer realm="ledger"'}
    )


@router.get("/http-errors/{status}", include_in_schema=False)
async def http_error(status: int) -> None:
    raise HTTPException(status_code=status, detail="because 4f9a", headers={"X-Reason": "test"})


@router.get("/errors/{kind}")
async def fail(kind: str) -> None:
    raise ERRORS[kind]


@router.get("/crash")
async def crash() -> None:
    raise ZeroDivisionError


async def two_chunks_the_second_one_broken() -> AsyncGenerator[bytes]:
    yield b"first"
    raise ZeroDivisionError


@router.get("/stream", include_in_schema=False)
async def stream() -> StreamingResponse:
    return StreamingResponse(two_chunks_the_second_one_broken())


events: list[str] = []


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    del app
    events.append("start")
    yield
    events.append("stop")


@pytest.fixture
def health() -> Health:
    return Health(timeout=0.05)


@pytest.fixture
def registry() -> CollectorRegistry:
    return CollectorRegistry()


def build(health: Health, registry: CollectorRegistry, *routers: APIRouter) -> FastAPI:
    return create_app(
        info=ApiInfo(title="test", version="1.0"),
        health=health,
        registry=registry,
        lifespan=lifespan,
        routers=routers,
    )


@asynccontextmanager
async def serving(app: FastAPI) -> AsyncGenerator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
def app(health: Health, registry: CollectorRegistry) -> FastAPI:
    return build(health, registry, router)


@pytest.fixture
async def client(app: FastAPI) -> AsyncGenerator[httpx.AsyncClient]:
    async with serving(app) as client:
        yield client


async def metrics_of(client: httpx.AsyncClient) -> str:
    return (await client.get("/metrics")).text


async def test_liveness_needs_nothing(client: httpx.AsyncClient) -> None:
    response = await client.get("/livez")
    assert (response.status_code, response.json()) == (200, {"status": "ok"})


async def test_readiness_reports_each_check(client: httpx.AsyncClient, health: Health) -> None:
    async def ok() -> None:
        return None

    health.add("database", ok)
    response = await client.get("/readyz")
    assert (response.status_code, response.json()) == (200, {"status": "ready", "checks": {"database": True}})

    async def down() -> None:
        raise ConnectionError

    health.add("nats", down)
    response = await client.get("/readyz")
    assert response.status_code == 503
    assert response.json() == {"status": "not_ready", "checks": {"database": True, "nats": False}}


async def test_requests_are_measured_by_route_template(client: httpx.AsyncClient) -> None:
    await client.get("/items/7")
    await client.get("/nowhere")
    metrics = (await client.get("/metrics")).text
    assert (
        'http_request_duration_seconds_count{method="GET",route="/items/{item_id}",status="200"} 1.0'
        in metrics
    )
    assert 'route="unmatched",status="404"' in metrics


async def test_a_request_whose_handler_raises_is_still_measured(client: httpx.AsyncClient) -> None:
    await client.get("/crash")
    metrics = (await client.get("/metrics")).text
    assert 'http_request_duration_seconds_count{method="GET",route="/crash",status="500"} 1.0' in metrics


async def test_a_response_that_breaks_after_it_began_is_measured_as_failed(client: httpx.AsyncClient) -> None:
    await client.get("/stream")
    metrics = (await client.get("/metrics")).text
    assert 'http_request_duration_seconds_count{method="GET",route="/stream",status="500"} 1.0' in metrics


async def test_methods_nobody_defined_share_one_label(client: httpx.AsyncClient) -> None:
    """A client chooses the method freely; each distinct one must not become a series of its own."""
    await client.request("BREW", "/items/7")
    await client.request("POUR", "/items/7")
    metrics = (await client.get("/metrics")).text
    assert (
        'http_request_duration_seconds_count{method="other",route="/items/{item_id}",status="405"} 2.0'
        in metrics
    )


async def test_a_ledger_error_is_a_problem_document(client: httpx.AsyncClient) -> None:
    response = await client.get("/items/404")
    assert response.status_code == 404
    assert response.headers["content-type"] == PROBLEM_JSON
    assert response.json() == {
        "type": "urn:ledger:error:missing_item",
        "title": "Not Found",
        "status": 404,
        "code": "missing_item",
        "detail": "item 404 does not exist",
    }


@pytest.mark.parametrize(
    ("kind", "status"),
    [
        ("invalid", HTTPStatus.UNPROCESSABLE_CONTENT),
        ("conflict", HTTPStatus.CONFLICT),
        ("forbidden", HTTPStatus.FORBIDDEN),
        ("unavailable", HTTPStatus.SERVICE_UNAVAILABLE),
        ("internal", HTTPStatus.INTERNAL_SERVER_ERROR),
    ],
)
async def test_each_error_kind_has_its_status(
    client: httpx.AsyncClient, kind: str, status: HTTPStatus
) -> None:
    response = await client.get(f"/errors/{kind}")
    assert (response.status_code, response.json()["status"]) == (status, status)


async def test_a_body_that_breaks_the_schema_is_a_problem_document_listing_every_problem(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post("/items", json={"quantity": "SECRET-4f9a"})
    assert response.status_code == 422
    assert response.headers["content-type"] == PROBLEM_JSON
    assert response.json() == {
        "type": "urn:ledger:error:invalid_request",
        "title": "Unprocessable Content",
        "status": 422,
        "code": "invalid_request",
        "detail": "The request does not match the schema.",
        "errors": [
            {"location": "body.name", "message": "Field required"},
            {
                "location": "body.quantity",
                "message": "Input should be a valid integer, unable to parse string as an integer",
            },
        ],
        "omitted_errors": 0,
    }
    assert "SECRET-4f9a" not in response.text


async def test_a_name_the_client_chose_is_cut_in_a_location(client: httpx.AsyncClient) -> None:
    response = await client.post("/strict", json={"name": "x", "k" * 500: 1})
    (issue,) = INVALID_REQUEST.validate_json(response.content)["errors"]
    assert issue == {"location": "body." + "k" * 40 + "...", "message": "Extra inputs are not permitted"}


async def test_a_flood_of_problems_is_cut_and_counted(client: httpx.AsyncClient) -> None:
    response = await client.post("/batches", json=[{"name": 1}] * 50)
    body = INVALID_REQUEST.validate_json(response.content)
    assert len(body["errors"]) == 20
    assert body["omitted_errors"] == 80


async def test_a_bug_is_a_500_without_details(client: httpx.AsyncClient) -> None:
    response = await client.get("/crash")
    assert response.status_code == 500
    assert response.json() == {
        "type": "urn:ledger:error:internal",
        "title": "Internal Server Error",
        "status": 500,
        "code": "internal",
    }


async def test_an_internal_error_answers_like_a_bug_and_keeps_its_code_for_the_log(
    client: httpx.AsyncClient,
) -> None:
    with capture_logs() as logs:
        response = await client.get("/errors/defect")
    assert response.status_code == 500
    assert response.json() == {
        "type": "urn:ledger:error:internal",
        "title": "Internal Server Error",
        "status": 500,
        "code": "internal",
    }
    (entry,) = logs
    assert isinstance(entry.pop("exc_info"), BrokenInvariantError)
    assert entry == {
        "event": "http.unhandled_error",
        "log_level": "error",
        "method": "GET",
        "route": "/errors/{kind}",
        "code": "broken_invariant",
    }


async def test_a_bug_is_logged_without_the_request(client: httpx.AsyncClient) -> None:
    """Headers, query and body may carry credentials: the log says where it failed and what failed."""
    with capture_logs() as logs:
        await client.request(
            "GET",
            "/crash",
            params={"api_key": "hunter2"},
            headers={"authorization": "Bearer hunter2"},
            content="card=4111111111111111",
        )
    (entry,) = logs
    assert isinstance(entry.pop("exc_info"), ZeroDivisionError)
    assert entry == {
        "event": "http.unhandled_error",
        "log_level": "error",
        "method": "GET",
        "route": "/crash",
        "code": "internal",
    }


async def test_an_unknown_path_is_a_problem_document(client: httpx.AsyncClient) -> None:
    response = await client.get("/nowhere")
    assert response.status_code == 404
    assert response.headers["content-type"] == PROBLEM_JSON
    assert response.json() == {
        "type": "urn:ledger:error:not_found",
        "title": "Not Found",
        "status": 404,
        "code": "not_found",
    }


async def test_a_method_the_route_does_not_take_keeps_its_allow_header(client: httpx.AsyncClient) -> None:
    response = await client.post("/livez")
    assert response.status_code == 405
    assert response.headers["allow"] == "GET"
    assert response.headers["content-type"] == PROBLEM_JSON
    assert response.json() == {
        "type": "urn:ledger:error:method_not_allowed",
        "title": "Method Not Allowed",
        "status": 405,
        "code": "method_not_allowed",
    }


async def test_an_http_exception_keeps_its_headers_and_does_not_repeat_its_detail(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/secure")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == 'Bearer realm="ledger"'
    assert response.headers["content-type"] == PROBLEM_JSON
    assert response.json() == {
        "type": "urn:ledger:error:unauthorized",
        "title": "Unauthorized",
        "status": 401,
        "code": "unauthorized",
    }


@pytest.mark.parametrize(
    ("status", "title", "code"),
    [(499, "Client Error", "client_error"), (599, "Server Error", "server_error")],
)
async def test_a_status_the_standard_does_not_name_is_a_generic_problem(
    client: httpx.AsyncClient, status: int, title: str, code: str
) -> None:
    response = await client.get(f"/http-errors/{status}")
    assert response.status_code == status
    assert response.headers["x-reason"] == "test"
    assert response.json() == {
        "type": f"urn:ledger:error:{code}",
        "title": title,
        "status": status,
        "code": code,
    }


async def test_a_status_that_forbids_a_body_has_none(client: httpx.AsyncClient) -> None:
    response = await client.get("/http-errors/304")
    assert (response.status_code, response.content) == (304, b"")
    assert response.headers["x-reason"] == "test"


async def test_included_routes_are_measured_by_their_full_template(
    health: Health, registry: CollectorRegistry
) -> None:
    accounts, entries, api = APIRouter(), APIRouter(), APIRouter(prefix="/api")

    @accounts.get("/{item_id}")
    async def account(item_id: int) -> dict[str, int]:
        return {"id": item_id}

    @entries.get("/{item_id}")
    async def entry(item_id: int) -> dict[str, int]:
        return {"id": item_id}

    api.include_router(accounts, prefix="/accounts")
    api.include_router(entries, prefix="/entries")
    async with serving(build(health, registry, api)) as client:
        await client.get("/api/accounts/1")
        await client.get("/api/entries/2")
        await client.get("/api/entries/3")
        metrics = await metrics_of(client)
    assert 'route="/api/accounts/{item_id}",status="200"} 1.0' in metrics
    assert 'route="/api/entries/{item_id}",status="200"} 2.0' in metrics


async def test_a_route_reachable_under_two_templates_is_refused(
    health: Health, registry: CollectorRegistry
) -> None:
    child, api = APIRouter(), APIRouter()

    @child.get("/{item_id}")
    async def item(item_id: int) -> dict[str, int]:
        return {"id": item_id}

    api.include_router(child, prefix="/old")
    api.include_router(child, prefix="/new")
    with pytest.raises(AmbiguousRouteError, match=r"/old/{item_id}.*/new/{item_id}"):
        build(health, registry, api)


async def test_a_route_added_after_the_app_was_built_is_measured_by_its_own_path(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    @app.get("/late/{number}", include_in_schema=False)
    async def late(number: int) -> dict[str, int]:
        return {"number": number}

    await client.get("/late/1")
    assert 'route="/late/{number}",status="200"} 1.0' in await metrics_of(client)


async def test_the_lifespan_reaches_the_app_through_its_middleware(app: FastAPI) -> None:
    """What a server does at start and stop, spoken in ASGI: the middleware passes it on untouched."""
    events.clear()
    from_server: asyncio.Queue[MutableMapping[str, object]] = asyncio.Queue()
    to_server: asyncio.Queue[MutableMapping[str, object]] = asyncio.Queue()
    running = asyncio.create_task(app({"type": "lifespan"}, from_server.get, to_server.put))
    await from_server.put({"type": "lifespan.startup"})
    assert await to_server.get() == {"type": "lifespan.startup.complete"}
    assert events == ["start"]
    await from_server.put({"type": "lifespan.shutdown"})
    assert await to_server.get() == {"type": "lifespan.shutdown.complete"}
    await running
    assert events == ["start", "stop"]


async def test_the_lifespan_is_the_one_given(app: FastAPI) -> None:
    events.clear()
    async with app.router.lifespan_context(app):
        assert events == ["start"]
    assert events == ["start", "stop"]


async def test_operational_routes_are_not_in_the_public_schema(client: httpx.AsyncClient) -> None:
    document = OPENAPI_DOCUMENT.validate_json((await client.get("/openapi.json")).content)
    assert set(document["paths"]) == {"/items/{item_id}", "/items", "/errors/{kind}", "/crash"}
