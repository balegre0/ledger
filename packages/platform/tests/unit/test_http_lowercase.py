"""``LowercasePath`` (§3.8, [06] §4): a receiver's paths are case-insensitive under its channels' prefixes."""

from contextlib import asynccontextmanager
from typing import Final
lazy from collections.abc import AsyncGenerator

import httpx
import pytest
from fastapi import APIRouter
from opentelemetry.sdk.trace import TracerProvider
from prometheus_client import CollectorRegistry
lazy from fastapi import FastAPI

from ledger_platform.config.options import InvalidOptionError
from ledger_platform.http.app import ApiInfo, Edge, Observability, create_app
from ledger_platform.http.lowercase import LowercasePath, ascii_lower
from ledger_platform.observability.health import Health
lazy from ledger_platform.http.metrics import AsgiMessage, AsgiScope

PREFIXES: Final = ("/do-ecf-v1/",)
SENT: Final = "/DO-ECF-V1/TESTECF/131880738/FE/Recepcion/API/ECF"


class Seen:
    """An ASGI app that records the scope it was handed and answers nothing."""

    def __init__(self) -> None:
        self.scopes: list[AsgiScope] = []

    async def __call__(self, scope: AsgiScope, receive: object, send: object) -> None:
        del receive, send
        self.scopes.append(dict(scope))


async def routed(path: str, *, kind: str = "http") -> AsgiScope:
    """The scope ``LowercasePath`` hands on for a request of ``path`` (raw as sent)."""
    seen = Seen()
    middleware = LowercasePath(seen, prefixes=PREFIXES)

    async def receive() -> AsgiMessage:
        return {"type": "http.disconnect"}

    async def send(message: AsgiMessage) -> None:
        del message

    scope: AsgiScope = {"type": kind, "path": path, "raw_path": path.encode()}
    await middleware(scope, receive, send)
    [handed] = seen.scopes
    assert handed["path"] == scope["path"]  # changed in place: the layers around read what the router left
    return handed


async def test_a_path_under_a_prefix_is_routed_lower_cased_and_its_raw_path_kept() -> None:
    scope = await routed(SENT)
    assert scope["path"] == "/do-ecf-v1/testecf/131880738/fe/recepcion/api/ecf"
    assert scope["raw_path"] == SENT.encode()


@pytest.mark.parametrize("path", ["/V1/Invoices", "/DO-ECF-V1", "/do-ecf-v10/X", "/Livez"])
async def test_a_path_outside_every_prefix_is_routed_as_sent(path: str) -> None:
    assert (await routed(path))["path"] == path


async def test_only_ascii_letters_change() -> None:
    """The Kelvin sign lower-cases to ``k`` in Unicode: here it stays itself and reaches no ASCII route."""
    kelvin, dotted = "\N{KELVIN SIGN}", "\N{LATIN CAPITAL LETTER I WITH DOT ABOVE}"
    assert (await routed(f"/DO-ECF-V1/{kelvin}{dotted}X"))["path"] == f"/do-ecf-v1/{kelvin}{dotted}x"
    assert ascii_lower(f"B-{kelvin}") == f"b-{kelvin}"


async def test_a_scope_that_is_no_request_passes_untouched() -> None:
    assert (await routed(SENT, kind="websocket"))["path"] == SENT


@pytest.mark.parametrize("prefix", ["do-ecf-v1/", "/do-ecf-v1", "/DO-ECF-V1/", "/", "/dó/"])
def test_a_prefix_must_be_a_lower_case_ascii_segment(prefix: str) -> None:
    with pytest.raises(InvalidOptionError, match="lowercase_prefixes"):
        LowercasePath(Seen(), prefixes=(prefix,))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    del app
    yield


async def test_an_app_answers_its_route_in_any_case_and_measures_it_under_its_template() -> None:
    router = APIRouter(prefix="/do-ecf-v1/{environment}/{rnc}")

    @router.post("/fe/recepcion/api/ecf")
    async def receive(environment: str, rnc: str) -> dict[str, str]:
        return {"environment": environment, "rnc": rnc}

    registry = CollectorRegistry()
    app = create_app(
        info=ApiInfo(title="receiver", version="1"),
        observability=Observability(health=Health(), registry=registry, tracer_provider=TracerProvider()),
        lifespan=lifespan,
        routers=[router],
        edge=Edge(lowercase_prefixes=PREFIXES),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://receiver.test") as client:
        answered = await client.post(SENT)
        elsewhere = await client.post("/V1/Invoices")
    assert (answered.status_code, answered.json()) == (200, {"environment": "testecf", "rnc": "131880738"})
    assert elsewhere.status_code == 404
    measured = registry.get_sample_value(
        "ledger_http_request_duration_seconds_count",
        {"method": "POST", "route": "/do-ecf-v1/{environment}/{rnc}/fe/recepcion/api/ecf", "status": "200"},
    )
    assert measured == 1.0
