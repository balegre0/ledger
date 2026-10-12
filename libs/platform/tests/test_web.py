import asyncio
from contextlib import asynccontextmanager
from http import HTTPStatus
from typing import ClassVar, Final, TypedDict
lazy from collections.abc import AsyncGenerator, MutableMapping

import httpx
import pytest
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from prometheus_client import CollectorRegistry
from pydantic import BaseModel, TypeAdapter
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
from ledger_platform.web import PROBLEM_JSON, create_app


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


class FieldProblem(TypedDict):
    location: str
    message: str


class InvalidRequest(TypedDict):
    code: str
    errors: list[FieldProblem]


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


@pytest.fixture
def app(health: Health, registry: CollectorRegistry) -> FastAPI:
    return create_app(
        title="test", version="1.0", health=health, registry=registry, lifespan=lifespan, routers=(router,)
    )


@pytest.fixture
async def client(app: FastAPI) -> AsyncGenerator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


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


async def test_a_body_that_breaks_the_schema_lists_every_problem(client: httpx.AsyncClient) -> None:
    response = await client.post("/items", json={"quantity": "many"})
    assert response.status_code == 422
    body = INVALID_REQUEST.validate_json(response.content)
    assert body["code"] == "invalid_request"
    assert {issue["location"] for issue in body["errors"]} == {"body.name", "body.quantity"}


async def test_a_bug_is_a_500_without_details(client: httpx.AsyncClient) -> None:
    response = await client.get("/crash")
    assert response.status_code == 500
    assert response.json() == {
        "type": "urn:ledger:error:internal",
        "title": "Internal Server Error",
        "status": 500,
        "code": "internal",
    }


async def test_an_internal_error_is_a_500_without_its_message_and_is_logged(
    client: httpx.AsyncClient,
) -> None:
    with capture_logs() as logs:
        response = await client.get("/errors/defect")
    assert response.status_code == 500
    assert response.json() == {
        "type": "urn:ledger:error:broken_invariant",
        "title": "Internal Server Error",
        "status": 500,
        "code": "broken_invariant",
    }
    (entry,) = logs
    assert isinstance(entry.pop("exc_info"), BrokenInvariantError)
    assert entry == {
        "event": "http.unhandled_error",
        "log_level": "error",
        "method": "GET",
        "route": "/errors/{kind}",
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
    }


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
