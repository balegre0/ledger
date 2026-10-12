import asyncio
import contextlib
import sys
import types
from contextlib import asynccontextmanager
from http import HTTPStatus
from typing import Annotated, ClassVar, Final, Literal, TypedDict, final, override
from uuid import UUID
lazy from collections.abc import AsyncGenerator, MutableMapping

import httpx
import pytest
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SpanExporter
from opentelemetry.trace import SpanKind, StatusCode, format_span_id, format_trace_id
from pydantic import BaseModel, ConfigDict, Field, JsonValue, SecretStr, TypeAdapter, field_validator
from structlog.testing import capture_logs
lazy from fastapi import FastAPI
lazy from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor
lazy from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
lazy from prometheus_client import CollectorRegistry

from ledger_kernel.errors import (
    ConflictError,
    ErrorKind,
    ForbiddenError,
    InvalidInputError,
    LedgerError,
    NotFoundError,
    UnauthenticatedError,
    UnavailableError,
    described,
)
from ledger_platform.health import Health
from ledger_platform.logs import configure_logging
from ledger_platform.settings import LogFormat, ServiceSettings
from ledger_platform.web import PROBLEM_JSON, AmbiguousRouteError, ApiInfo, Observability, create_app
lazy from ledger_platform.tracing import Tracing

TRACE_ID: Final = "0af7651916cd43dd8448eb211c80319c"
CALLER_SPAN_ID: Final = "b7ad6b7169203331"
TRACEPARENT: Final = f"00-{TRACE_ID}-{CALLER_SPAN_ID}-01"


class MissingItemError(NotFoundError):
    code: ClassVar[str] = "missing_item"

    def __init__(self, item: int) -> None:
        super().__init__(f"item {item} does not exist")


class BrokenInvariantError(LedgerError):
    """A defect of ours that carries a message meant for the log, not for a client."""

    code: ClassVar[str] = "broken_invariant"

    def __init__(self) -> None:
        super().__init__("the ledger is out of balance by 3")


class RefusedTokenError(UnauthenticatedError):
    """What the platform's token verifier raises (Task 3), as far as the HTTP app can tell: the code."""

    code: ClassVar[str] = "invalid_token"


class Item(BaseModel):
    name: str
    quantity: int


class Strict(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    name: str


class Cat(BaseModel):
    kind: Literal["cat"]


class Dog(BaseModel):
    kind: Literal["dog"]


class Household(BaseModel):
    pet: Annotated[Cat | Dog, Field(discriminator="kind")]


class Blob(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(val_json_bytes="base64")

    data: bytes


class Guarded(BaseModel):
    note: str

    @field_validator("note")
    @classmethod
    def refuse(cls, note: str) -> str:
        del note
        message = "x" * 1_000
        raise ValueError(message)


class FieldProblem(TypedDict):
    location: str
    type: str
    message: str


class InvalidRequest(TypedDict):
    code: str
    errors: list[FieldProblem]
    omitted_errors: int


class OpenApiDocument(TypedDict):
    paths: dict[str, object]


class Problem(TypedDict):
    type: str
    title: str
    status: int
    code: str


class Liveness(TypedDict):
    status: str
    wedged: list[str]
    dead: list[str]


PROBLEM: Final = TypeAdapter(Problem)
RECORD: Final = TypeAdapter(dict[str, JsonValue])
LIVENESS: Final = TypeAdapter(Liveness)
OTLP_HTTP: Final = "opentelemetry.exporter.otlp.proto.http"
"""The package of the OTLP/HTTP exporters FastAPI attaches by itself when told to: not installed here."""
KNOWN: Final = UUID(int=1)
INVALID_REQUEST: Final = TypeAdapter(InvalidRequest)
OPENAPI_DOCUMENT: Final = TypeAdapter(OpenApiDocument)

router = APIRouter()
ERRORS: dict[str, type[LedgerError]] = {
    "invalid": InvalidInputError,
    "conflict": ConflictError,
    "unauthenticated": UnauthenticatedError,
    "refused-token": RefusedTokenError,
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
async def http_error(status: int, detail: str = "because 4f9a") -> None:
    raise HTTPException(status_code=status, detail=detail, headers={"X-Reason": "test"})


DETAILS: Final[dict[str, object]] = {
    "mapping": {"reason": "x" * 1_000},
    "list": list(range(10_000)),
    "number": 42,
}


@router.get("/structured-errors/{kind}", include_in_schema=False)
async def structured_error(kind: str) -> None:
    raise HTTPException(status_code=503, detail=DETAILS[kind], headers={"Retry-After": "30"})


@router.post("/blobs", include_in_schema=False)
async def blob(item: Blob) -> None:
    del item


@router.post("/households", include_in_schema=False)
async def household(home: Household) -> Household:
    return home


@router.post("/guarded", include_in_schema=False)
async def guarded(note: Guarded) -> Guarded:
    return note


@router.get("/uuids/{value}", include_in_schema=False)
async def uuid_of(value: UUID) -> UUID:
    return value


@router.get("/errors/{kind}")
async def fail(kind: str) -> None:
    raise ERRORS[kind]


BY_KIND: Final[dict[ErrorKind, type[LedgerError]]] = {
    ErrorKind.INVALID: InvalidInputError,
    ErrorKind.NOT_FOUND: NotFoundError,
    ErrorKind.CONFLICT: ConflictError,
    ErrorKind.UNAUTHENTICATED: UnauthenticatedError,
    ErrorKind.FORBIDDEN: ForbiddenError,
    ErrorKind.UNAVAILABLE: UnavailableError,
    ErrorKind.INTERNAL: LedgerError,
}
STATUS_OF: Final[dict[ErrorKind, HTTPStatus]] = {
    ErrorKind.INVALID: HTTPStatus.UNPROCESSABLE_CONTENT,
    ErrorKind.NOT_FOUND: HTTPStatus.NOT_FOUND,
    ErrorKind.CONFLICT: HTTPStatus.CONFLICT,
    ErrorKind.UNAUTHENTICATED: HTTPStatus.UNAUTHORIZED,
    ErrorKind.FORBIDDEN: HTTPStatus.FORBIDDEN,
    ErrorKind.UNAVAILABLE: HTTPStatus.SERVICE_UNAVAILABLE,
    ErrorKind.INTERNAL: HTTPStatus.INTERNAL_SERVER_ERROR,
}


@router.get("/kinds/{kind}", include_in_schema=False)
async def fail_as(kind: ErrorKind) -> None:
    raise BY_KIND[kind]


@router.get("/crash")
async def crash() -> None:
    raise ZeroDivisionError


class Profile(BaseModel):
    tax_id: int


TAX_ID: Final = "001-1234567-8"
"""A cédula a route answers where its response model wants a number: pydantic quotes it."""


@router.get("/profile", response_model=Profile, include_in_schema=False)
async def profile() -> dict[str, str]:
    return {"tax_id": TAX_ID}


async def two_chunks_the_second_one_broken() -> AsyncGenerator[bytes]:
    yield b"first"
    raise ZeroDivisionError


@router.get("/stream", include_in_schema=False)
async def stream() -> StreamingResponse:
    return StreamingResponse(two_chunks_the_second_one_broken())


async def two_chunks_the_second_one_refused() -> AsyncGenerator[bytes]:
    yield b"first"
    raise ConflictError


@router.get("/stream-ledger-error", include_in_schema=False)
async def stream_ledger_error() -> StreamingResponse:
    """A ``LedgerError`` once the response has begun: too late for its problem document."""
    return StreamingResponse(two_chunks_the_second_one_refused())


async def exchange(app: FastAPI, path: str) -> list[MutableMapping[str, object]]:
    """One ``GET`` as an ASGI server makes it: every message the app sends back. Whatever the app raises
    reaches the caller, as it would reach the server."""
    sent: list[MutableMapping[str, object]] = []
    requested = False
    never = asyncio.Event()

    async def receive() -> MutableMapping[str, object]:
        nonlocal requested
        if requested:
            await never.wait()  # the client stays: Starlette listens for a disconnect while it streams
        requested = True
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: MutableMapping[str, object]) -> None:
        sent.append(message)

    scope: dict[str, object] = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"test")],
        "server": ("test", 80),
        "client": ("127.0.0.1", 50000),
    }
    await app(scope, receive, send)
    return sent


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
def observability(health: Health, registry: CollectorRegistry, tracing: Tracing) -> Observability:
    """The test's own health checks, metrics and spans (``exporter`` holds the finished ones)."""
    return Observability(health=health, registry=registry, tracer_provider=tracing.provider)


def build(observability: Observability, *routers: APIRouter) -> FastAPI:
    return create_app(
        info=ApiInfo(title="test", version="1.0"),
        observability=observability,
        lifespan=lifespan,
        routers=routers,
    )


@asynccontextmanager
async def serving(app: FastAPI) -> AsyncGenerator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
def app(observability: Observability) -> FastAPI:
    return build(observability, router)


@pytest.fixture
async def client(app: FastAPI) -> AsyncGenerator[httpx.AsyncClient]:
    async with serving(app) as client:
        yield client


async def metrics_of(client: httpx.AsyncClient) -> str:
    return (await client.get("/metrics")).text


@final
class RecordingProvider(TracerProvider):
    """A tracer provider that remembers every span processor anyone adds to it."""

    def __init__(self) -> None:
        super().__init__(shutdown_on_exit=False)
        self.added: list[SpanProcessor] = []

    @override
    def add_span_processor(self, span_processor: SpanProcessor) -> None:
        self.added.append(span_processor)
        super().add_span_processor(span_processor)


@final
class StandInExporter(SpanExporter):
    """Takes the place of an OTLP/HTTP exporter: made with an endpoint, it exports nothing."""

    def __init__(self, endpoint: str) -> None:
        self.endpoint = endpoint


def server_span(exporter: InMemorySpanExporter) -> ReadableSpan:
    """The span of the one request a test made, as FastAPI recorded it."""
    (span,) = [span for span in exporter.get_finished_spans() if span.kind is SpanKind.SERVER]
    return span


async def start_and_stop(app: FastAPI) -> None:
    """Run the lifespan of ``app`` as a server does, in ASGI messages."""
    from_server: asyncio.Queue[MutableMapping[str, object]] = asyncio.Queue()
    to_server: asyncio.Queue[MutableMapping[str, object]] = asyncio.Queue()
    running = asyncio.create_task(app({"type": "lifespan"}, from_server.get, to_server.put))
    await from_server.put({"type": "lifespan.startup"})
    assert await to_server.get() == {"type": "lifespan.startup.complete"}
    await from_server.put({"type": "lifespan.shutdown"})
    assert await to_server.get() == {"type": "lifespan.shutdown.complete"}
    await running


async def test_liveness_needs_nothing(client: httpx.AsyncClient) -> None:
    response = await client.get("/livez")
    assert (response.status_code, response.json()) == (200, {"status": "ok"})


async def test_liveness_fails_while_a_check_is_wedged_and_recovers_when_it_ends(
    registry: CollectorRegistry, tracing: Tracing
) -> None:
    release, ended = asyncio.Event(), asyncio.Event()
    now = [0.0]

    async def stubborn() -> None:
        while not release.is_set():
            with contextlib.suppress(asyncio.CancelledError):
                await release.wait()
        ended.set()

    health = Health(timeout=0.02, wedge_after=30, clock=lambda: now[0])
    health.add("nats", stubborn)
    try:
        observability = Observability(health=health, registry=registry, tracer_provider=tracing.provider)
        async with serving(build(observability, router)) as client:
            assert (await client.get("/readyz")).status_code == 503
            assert (await client.get("/livez")).status_code == 200
            now[0] = 31
            wedged = await client.get("/livez")
            assert (wedged.status_code, LIVENESS.validate_json(wedged.content)) == (
                503,
                {"status": "unrecoverable", "wedged": ["nats"], "dead": []},
            )
            release.set()
            await ended.wait()
            await asyncio.sleep(0)
            assert (await client.get("/livez")).status_code == 200
    finally:
        release.set()


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
        '\nledger_http_request_duration_seconds_count{method="GET",route="/items/{item_id}",status="200"} 1.0'
        in metrics
    )
    assert 'route="unmatched",status="404"' in metrics


async def test_a_request_whose_handler_raises_is_still_measured(client: httpx.AsyncClient) -> None:
    await client.get("/crash")
    metrics = (await client.get("/metrics")).text
    assert (
        '\nledger_http_request_duration_seconds_count{method="GET",route="/crash",status="500"} 1.0'
        in metrics
    )


async def test_a_response_that_breaks_after_it_began_is_measured_as_failed(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    await exchange(app, "/stream")
    metrics = (await client.get("/metrics")).text
    assert (
        '\nledger_http_request_duration_seconds_count{method="GET",route="/stream",status="500"} 1.0'
        in metrics
    )


async def test_a_bug_never_reaches_the_server_and_is_logged_once(app: FastAPI) -> None:
    """Starlette raises a bug again after answering it, for the server to log a second time, message and all:
    the app stops it. A client that lets exceptions through (as a server sees them) gets the answer alone."""
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=True)
    with capture_logs() as logs:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as strict:
            response = await strict.get("/crash")
    assert (response.status_code, PROBLEM.validate_json(response.content)["code"]) == (500, "internal")
    assert [entry["event"] for entry in logs] == ["http.unhandled_error"]


@pytest.mark.parametrize("path", ["/stream", "/stream-ledger-error"])
async def test_a_response_that_breaks_after_it_began_is_cut_off_logged_once_and_never_raised(
    app: FastAPI, path: str
) -> None:
    """The status and the first chunk are on the wire: the app leaves the response unfinished (the server
    closes the connection, so the client never takes a cut body for a whole one) and raises nothing."""
    with capture_logs() as logs:
        sent = await exchange(app, path)
    assert [(message["type"], message.get("status"), message.get("more_body")) for message in sent] == [
        ("http.response.start", 200, None),
        ("http.response.body", None, True),
    ]
    (entry,) = logs
    del entry["exc_info"]
    assert entry == {
        "event": "http.unhandled_error",
        "log_level": "error",
        "method": "GET",
        "route": path,
        "code": "internal",
    }


async def test_methods_nobody_defined_share_one_label(client: httpx.AsyncClient) -> None:
    """A client chooses the method freely; each distinct one must not become a series of its own."""
    await client.request("BREW", "/items/7")
    await client.request("POUR", "/items/7")
    metrics = (await client.get("/metrics")).text
    measured = (
        'ledger_http_request_duration_seconds_count{method="other",route="/items/{item_id}",status="405"}'
    )
    assert f"\n{measured} 2.0" in metrics


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


def test_every_kind_of_error_is_expected_to_have_a_status() -> None:
    """A kind added to the kernel fails here until it has a status: never a ``KeyError`` in the handler."""
    assert STATUS_OF.keys() == set(ErrorKind)
    assert BY_KIND.keys() == set(ErrorKind)


@pytest.mark.parametrize("kind", list(ErrorKind))
async def test_each_error_kind_has_its_status(client: httpx.AsyncClient, kind: ErrorKind) -> None:
    response = await client.get(f"/kinds/{kind}")
    status = STATUS_OF[kind]
    assert (response.status_code, PROBLEM.validate_json(response.content)["status"]) == (status, status)


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
            {"location": "body.name", "type": "missing", "message": "Field required"},
            {
                "location": "body.quantity",
                "type": "int_parsing",
                "message": "Input should be a valid integer, unable to parse string as an integer",
            },
        ],
        "omitted_errors": 0,
    }
    assert "SECRET-4f9a" not in response.text


async def test_a_name_the_client_chose_is_cut_in_a_location(client: httpx.AsyncClient) -> None:
    response = await client.post("/strict", json={"name": "x", "k" * 500: 1})
    (issue,) = INVALID_REQUEST.validate_json(response.content)["errors"]
    assert issue == {
        "location": "body." + "k" * 40 + "...",
        "type": "extra_forbidden",
        "message": "Extra inputs are not permitted",
    }


async def test_the_tag_of_a_discriminated_union_does_not_come_back(client: httpx.AsyncClient) -> None:
    response = await client.post("/households", json={"pet": {"kind": "SECRET-4f9a"}})
    assert response.status_code == 422
    assert "SECRET-4f9a" not in response.text
    (issue,) = INVALID_REQUEST.validate_json(response.content)["errors"]
    assert issue == {
        "location": "body.pet",
        "type": "union_tag_invalid",
        "message": "Input tag does not match any of the expected tags.",
    }


async def test_a_well_formed_uuid_goes_through(client: httpx.AsyncClient) -> None:
    response = await client.get(f"/uuids/{KNOWN}")
    assert (response.status_code, response.json()) == (200, str(KNOWN))


async def test_a_character_of_a_malformed_uuid_does_not_come_back(client: httpx.AsyncClient) -> None:
    response = await client.get("/uuids/SECRET-4f9a")
    (issue,) = INVALID_REQUEST.validate_json(response.content)["errors"]
    assert issue == {
        "location": "path.value",
        "type": "uuid_parsing",
        "message": "Input should be a valid UUID.",
    }


async def test_the_symbol_of_an_undecodable_blob_does_not_come_back(client: httpx.AsyncClient) -> None:
    response = await client.post("/blobs", json={"data": "!!SECRET!!"})
    (issue,) = INVALID_REQUEST.validate_json(response.content)["errors"]
    assert issue == {
        "location": "body.data",
        "type": "bytes_invalid_encoding",
        "message": "Data is not valid in the encoding the field expects.",
    }
    assert "SECRET" not in response.text
    assert "symbol" not in response.text


async def test_the_message_of_a_validator_is_cut(client: httpx.AsyncClient) -> None:
    response = await client.post("/guarded", json={"note": "anything"})
    (issue,) = INVALID_REQUEST.validate_json(response.content)["errors"]
    assert issue["type"] == "value_error"
    assert issue["message"] == ("Value error, " + "x" * 1_000)[:200] + "..."


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


async def test_a_response_that_breaks_its_model_is_logged_without_what_it_held(
    client: httpx.AsyncClient, capsys: pytest.CaptureFixture[str]
) -> None:
    """FastAPI's error quotes the response it refused; the log says where and of which type, once."""
    configure_logging(
        ServiceSettings(
            service_name="web-test",
            database_url=SecretStr("postgresql+psycopg://x"),
            log_format=LogFormat.JSON,
        )
    )
    response = await client.get("/profile")
    assert (response.status_code, PROBLEM.validate_json(response.content)["code"]) == (500, "internal")
    output = capsys.readouterr().out
    assert TAX_ID not in output
    records = [RECORD.validate_json(line) for line in output.splitlines()]
    (record,) = [record for record in records if record["logger"] != "httpx"]  # the test's client logs too
    assert record["event"] == "http.unhandled_error"
    assert "1 validation errors: response.tax_id (int_parsing)" in str(record["exception"])


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
    assert entry.pop("exc_info") is True  # the exception being handled: the one the route raised
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


async def test_an_unauthenticated_caller_is_told_how_to_authenticate(client: httpx.AsyncClient) -> None:
    response = await client.get("/errors/unauthenticated")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == 'Bearer realm="ledger"'
    assert response.json()["code"] == "unauthenticated"


async def test_a_token_that_was_refused_is_named_in_the_challenge(client: httpx.AsyncClient) -> None:
    response = await client.get("/errors/refused-token")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == 'Bearer realm="ledger", error="invalid_token"'


async def test_only_an_unauthenticated_caller_gets_the_challenge(client: httpx.AsyncClient) -> None:
    response = await client.get("/errors/forbidden")
    assert response.status_code == 403
    assert "www-authenticate" not in response.headers


async def test_an_http_exception_keeps_its_headers_and_does_not_repeat_its_detail(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/secure")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == 'Bearer realm="ledger"'
    assert response.headers["content-type"] == PROBLEM_JSON
    assert response.json() == {
        "type": "urn:ledger:error:unauthenticated",
        "title": "Unauthorized",
        "status": 401,
        "code": "unauthenticated",
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


async def test_included_routes_are_measured_by_their_full_template(observability: Observability) -> None:
    accounts, entries, api = APIRouter(), APIRouter(), APIRouter(prefix="/api")

    @accounts.get("/{item_id}")
    async def account(item_id: int) -> dict[str, int]:
        return {"id": item_id}

    @entries.get("/{item_id}")
    async def entry(item_id: int) -> dict[str, int]:
        return {"id": item_id}

    api.include_router(accounts, prefix="/accounts")
    api.include_router(entries, prefix="/entries")
    async with serving(build(observability, api)) as client:
        await client.get("/api/accounts/1")
        await client.get("/api/entries/2")
        await client.get("/api/entries/3")
        metrics = await metrics_of(client)
    assert 'route="/api/accounts/{item_id}",status="200"} 1.0' in metrics
    assert 'route="/api/entries/{item_id}",status="200"} 2.0' in metrics


async def test_a_route_reachable_under_two_templates_is_refused(observability: Observability) -> None:
    child, api = APIRouter(), APIRouter()

    @child.get("/{item_id}")
    async def item(item_id: int) -> dict[str, int]:
        return {"id": item_id}

    api.include_router(child, prefix="/old")
    api.include_router(child, prefix="/new")
    with pytest.raises(AmbiguousRouteError, match=r"/old/{item_id}.*/new/{item_id}"):
        build(observability, api)


@pytest.mark.parametrize("kind", ["mapping", "list", "number"])
async def test_a_detail_that_is_not_text_is_logged_bounded_and_the_answer_is_unchanged(
    client: httpx.AsyncClient, kind: str
) -> None:
    """FastAPI takes any detail: a mapping, a list of ten thousand, a number. The 503 stands, headers too."""
    with capture_logs() as logs:
        response = await client.get(f"/structured-errors/{kind}")
    assert response.status_code == 503
    assert response.headers["retry-after"] == "30"
    assert PROBLEM.validate_json(response.content)["code"] == "unavailable"
    shown = described(DETAILS[kind])
    assert len(shown) < 100
    assert logs == [
        {
            "event": "http.server_error",
            "log_level": "error",
            "method": "GET",
            "route": "/structured-errors/{kind}",
            "status": 503,
            "code": "unavailable",
            "detail": shown,
        }
    ]


async def test_a_route_added_after_the_app_was_built_is_unregistered_and_said_so_once(
    observability: Observability,
) -> None:
    included = APIRouter()
    app = build(observability, included)

    @included.get("/on-the-router/{number}", include_in_schema=False)
    async def on_the_router(number: int) -> dict[str, int]:
        return {"number": number}

    @app.get("/on-the-app/{number}", include_in_schema=False)
    async def on_the_app(number: int) -> dict[str, int]:
        return {"number": number}

    async with serving(app) as client:
        with capture_logs() as logs:
            for path in ("/on-the-router/1", "/on-the-router/2", "/on-the-app/1", "/on-the-app/2"):
                assert (await client.get(path)).status_code == 200
        metrics = await metrics_of(client)
    assert 'route="unregistered",status="200"} 4.0' in metrics
    assert "/on-the-router" not in metrics
    assert "/on-the-app" not in metrics
    assert logs == [
        {
            "event": "http.route_unregistered",
            "log_level": "warning",
            "path": "/on-the-router/{number}",
        },
        {"event": "http.route_unregistered", "log_level": "warning", "path": "/on-the-app/{number}"},
    ]


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (400, "bad_request"),
        (401, "unauthenticated"),
        (403, "forbidden"),
        (404, "not_found"),
        (405, "method_not_allowed"),
        (406, "not_acceptable"),
        (409, "conflict"),
        (410, "gone"),
        (412, "precondition_failed"),
        (413, "payload_too_large"),
        (415, "unsupported_media_type"),
        (422, "invalid"),
        (428, "precondition_required"),
        (429, "too_many_requests"),
        (500, "internal"),
        (501, "not_implemented"),
        (502, "bad_gateway"),
        (503, "unavailable"),
        (504, "gateway_timeout"),
    ],
)
async def test_the_code_of_a_common_status_is_pinned(
    client: httpx.AsyncClient, status: int, code: str
) -> None:
    """The codes are API: they must not follow the names CPython happens to give its statuses."""
    response = await client.get(f"/http-errors/{status}")
    assert response.status_code == status
    body = PROBLEM.validate_json(response.content)
    assert (body["code"], body["type"], body["status"]) == (code, f"urn:ledger:error:{code}", status)


@pytest.mark.parametrize(
    ("status", "title", "code"),
    [
        (418, "I'm a Teapot", "i_m_a_teapot"),
        (451, "Unavailable For Legal Reasons", "unavailable_for_legal_reasons"),
        (307, "Temporary Redirect", "temporary_redirect"),
        (299, "Successful", "successful"),
        (399, "Redirection", "redirection"),
        (499, "Client Error", "client_error"),
        (599, "Server Error", "server_error"),
        (699, "Error", "error"),
    ],
)
async def test_other_statuses_fall_back_to_their_phrase_or_their_class(
    client: httpx.AsyncClient, status: int, title: str, code: str
) -> None:
    response = await client.get(f"/http-errors/{status}")
    body = PROBLEM.validate_json(response.content)
    assert (body["title"], body["code"]) == (title, code)


async def test_a_server_error_raised_by_a_route_is_logged_and_a_client_error_is_not(
    client: httpx.AsyncClient,
) -> None:
    with capture_logs() as logs:
        refused = await client.get("/http-errors/404")
        failed = await client.get("/http-errors/503", params={"detail": "d" * 1_000})
    assert (refused.status_code, failed.status_code) == (404, 503)
    assert logs == [
        {
            "event": "http.server_error",
            "log_level": "error",
            "method": "GET",
            "route": "/http-errors/{status}",
            "status": 503,
            "code": "unavailable",
            "detail": "d" * 200 + "...",
        }
    ]
    assert "ddd" not in failed.text


async def test_the_lifespan_reaches_the_app_through_its_middleware(app: FastAPI) -> None:
    """What a server does at start and stop, spoken in ASGI: the middleware passes it on untouched."""
    events.clear()
    await start_and_stop(app)
    assert events == ["start", "stop"]


async def test_the_lifespan_is_the_one_given(app: FastAPI) -> None:
    events.clear()
    async with app.router.lifespan_context(app):
        assert events == ["start"]
    assert events == ["start", "stop"]


async def test_operational_routes_are_not_in_the_public_schema(client: httpx.AsyncClient) -> None:
    document = OPENAPI_DOCUMENT.validate_json((await client.get("/openapi.json")).content)
    assert set(document["paths"]) == {"/items/{item_id}", "/items", "/errors/{kind}", "/crash"}


async def test_a_request_is_traced_under_its_route_continuing_the_caller_s_trace(
    client: httpx.AsyncClient, exporter: InMemorySpanExporter
) -> None:
    """The span of a request is the parent of what its route does: an event it stores carries the trace."""
    await client.get("/items/7", headers={"traceparent": TRACEPARENT})
    span = server_span(exporter)
    assert span.name == "GET /items/{item_id}"
    assert span.context is not None
    assert span.parent is not None
    assert format_trace_id(span.context.trace_id) == TRACE_ID
    assert format_span_id(span.parent.span_id) == CALLER_SPAN_ID


async def test_a_bug_is_traced_by_its_type_alone_and_logged_once(
    client: httpx.AsyncClient, exporter: InMemorySpanExporter
) -> None:
    with capture_logs() as logs:
        await client.get("/crash")
    span = server_span(exporter)
    assert span.status.status_code is StatusCode.ERROR
    assert span.status.description is None
    assert span.attributes is not None
    assert span.attributes["error.type"] == "ZeroDivisionError"
    assert [recorded.events for recorded in exporter.get_finished_spans()] == [
        () for _ in exporter.get_finished_spans()
    ]
    assert [entry["event"] for entry in logs] == ["http.unhandled_error"]


async def test_the_environment_cannot_attach_exporters_of_its_own(
    health: Health, registry: CollectorRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FastAPI adds OTLP exporters to the provider when ``FASTAPI_OTEL_AUTO_CONFIGURE`` asks for them, past
    the redaction the chassis gives every exporter of its own; the app never does.

    FastAPI's OTLP/HTTP exporters are not installed here: stand-ins take their place, so that an attempt would
    reach the provider instead of failing at the import.
    """
    for name in ("_log_exporter", "metric_exporter", "trace_exporter"):
        module = types.ModuleType(f"{OTLP_HTTP}.{name}")
        for exporter in ("OTLPLogExporter", "OTLPMetricExporter", "OTLPSpanExporter"):
            monkeypatch.setattr(module, exporter, StandInExporter, raising=False)
        monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setenv("FASTAPI_OTEL_AUTO_CONFIGURE", "true")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4318")
    provider = RecordingProvider()
    await start_and_stop(
        build(Observability(health=health, registry=registry, tracer_provider=provider), router)
    )
    assert provider.added == []


@pytest.mark.parametrize("path", ["/livez", "/readyz", "/metrics"])
async def test_the_operational_routes_are_not_traced(
    client: httpx.AsyncClient, exporter: InMemorySpanExporter, path: str
) -> None:
    """Probes and scrapes come every few seconds from every prober: their spans would bury the requests."""
    await client.get(path)
    await client.get("/items/7")
    assert [span.name for span in exporter.get_finished_spans() if span.kind is SpanKind.SERVER] == [
        "GET /items/{item_id}"
    ]


async def test_liveness_fails_while_a_part_of_the_process_is_dead(
    client: httpx.AsyncClient, health: Health
) -> None:
    """A part nothing brings back (a worker stopped by a configuration mistake): only a restart helps."""
    gone: list[str] = []
    health.add_liveness("workers", lambda: not gone)
    assert (await client.get("/livez")).status_code == 200
    gone.append("consumer")
    dead = await client.get("/livez")
    assert (dead.status_code, LIVENESS.validate_json(dead.content)) == (
        503,
        {"status": "unrecoverable", "wedged": [], "dead": ["workers"]},
    )
