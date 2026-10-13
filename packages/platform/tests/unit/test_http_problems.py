"""``install_problem_handlers`` on an app of its own, without the chassis's lifespan, metrics or probes.

The problem documents of a composed app are pinned in ``test_http_app``; these pin what a bare app gets.
"""

from typing import ClassVar

import httpx
from fastapi import APIRouter, FastAPI
from structlog.testing import capture_logs

from ledger_kernel.errors import LedgerError
from ledger_platform.http.metrics import declared_route
from ledger_platform.http.problems import PROBLEM_JSON, install_problem_handlers


class BrokenInvariantError(LedgerError):
    code: ClassVar[str] = "broken_invariant"

    def __init__(self) -> None:
        super().__init__("an invariant broke")


def bare(*routers: APIRouter) -> FastAPI:
    built = FastAPI()
    install_problem_handlers(built)
    for router in routers:
        built.include_router(router)
    return built


def client_of(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def test_a_defect_of_a_bare_app_is_logged_under_the_template_its_route_declares() -> None:
    router = APIRouter(prefix="/v1")

    @router.get("/things/{thing_id}")
    async def thing(thing_id: int) -> None:
        del thing_id
        raise BrokenInvariantError

    async with client_of(bare(router)) as client:
        with capture_logs() as logs:
            response = await client.get("/v1/things/7")
    assert (response.status_code, response.headers["content-type"]) == (500, PROBLEM_JSON)
    assert response.json()["code"] == "internal"
    assert [(entry["event"], entry["route"], entry["code"]) for entry in logs] == [
        ("http.unhandled_error", "/v1/things/{thing_id}", "broken_invariant")
    ]


def test_a_request_no_route_took_is_unmatched() -> None:
    assert declared_route({}) == "unmatched"
