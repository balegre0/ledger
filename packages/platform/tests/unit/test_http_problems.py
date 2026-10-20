"""``install_problem_handlers`` on an app of its own, without the chassis's lifespan, metrics or probes.

The problem documents of a composed app are pinned in ``test_http_app``; these pin what a bare app gets.
"""

from http import HTTPStatus
from typing import ClassVar
lazy from collections.abc import Mapping

import httpx
import pytest
from fastapi import APIRouter, FastAPI
from opentelemetry.trace import NoOpTracerProvider
from structlog.testing import capture_logs

from ledger_kernel.errors import ErrorKind, InvalidInputError, LedgerError
from ledger_platform.http.metrics import declared_route
from ledger_platform.http.problems import PROBLEM_JSON, BadRequestError, install_problem_handlers, status_of


class BrokenInvariantError(LedgerError):
    code: ClassVar[str] = "broken_invariant"

    def __init__(self) -> None:
        super().__init__("an invariant broke")


def bare(*routers: APIRouter) -> FastAPI:
    """An app of its own, traced nowhere (whatever provider another test made process-wide)."""
    built = FastAPI(
        telemetry={
            "tracer_provider": NoOpTracerProvider(),
            "logs": False,
            "metrics": False,
            "auto_configure": False,
        }
    )
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


class OddFieldError(InvalidInputError):
    code: ClassVar[str] = "odd_field"

    def __init__(self) -> None:
        super().__init__("a field is odd")


class RefusedHeaderError(BadRequestError):
    code: ClassVar[str] = "refused_header"

    def __init__(self) -> None:
        super().__init__("the header is refused")


async def test_a_bad_request_is_a_400_problem_of_its_own_code() -> None:
    router = APIRouter()

    @router.get("/refused")
    async def refused() -> None:
        raise RefusedHeaderError

    async with client_of(bare(router)) as client:
        response = await client.get("/refused")
    assert (response.status_code, response.headers["content-type"]) == (400, PROBLEM_JSON)
    assert response.json() == {
        "type": "urn:ledger:error:refused_header",
        "title": "Bad Request",
        "status": 400,
        "code": "refused_header",
        "detail": "the header is refused",
    }
    assert RefusedHeaderError().kind is ErrorKind.INVALID


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (RefusedHeaderError(), HTTPStatus.BAD_REQUEST),
        (OddFieldError(), HTTPStatus.UNPROCESSABLE_CONTENT),
        (BrokenInvariantError(), HTTPStatus.INTERNAL_SERVER_ERROR),
    ],
)
def test_every_other_error_keeps_the_status_of_its_kind(error: LedgerError, status: HTTPStatus) -> None:
    assert status_of(error) == status


async def test_a_request_outside_any_trace_has_no_request_id() -> None:
    router = APIRouter()

    @router.get("/refused")
    async def refused() -> None:
        raise RefusedHeaderError

    async with client_of(bare(router)) as client:
        response = await client.get("/refused")
    assert "request_id" not in response.json()
    assert "Request-Id" not in response.headers


class SlowBodyError(InvalidInputError):
    """A refusal answered with a status and a header of its own: its kind's 422 would mislead a client."""

    code: ClassVar[str] = "slow_body"

    def __init__(self) -> None:
        super().__init__("the body did not arrive in time")

    def problem_status(self) -> HTTPStatus:
        return HTTPStatus.REQUEST_TIMEOUT

    def problem_headers(self) -> Mapping[str, str]:
        return {"Retry-After": "5", "Content-Type": "text/plain"}


class AnsweredDefectError(LedgerError):
    """A defect that names a status of its own: a defect is a 500 ``internal`` whatever it names."""

    code: ClassVar[str] = "answered_defect"

    def __init__(self) -> None:
        super().__init__("a defect")

    def problem_status(self) -> HTTPStatus:
        return HTTPStatus.REQUEST_TIMEOUT

    def problem_headers(self) -> Mapping[str, str]:
        return {"Retry-After": "5"}


async def test_an_error_that_names_its_answer_is_answered_with_its_status_and_headers() -> None:
    router = APIRouter()

    @router.get("/slow")
    async def slow() -> None:
        raise SlowBodyError

    @router.get("/defect")
    async def defect() -> None:
        raise AnsweredDefectError

    async with client_of(bare(router)) as client:
        response = await client.get("/slow")
        with capture_logs() as logs:
            broken = await client.get("/defect")
    assert (response.status_code, response.headers["retry-after"]) == (408, "5")
    assert response.headers["content-type"] == PROBLEM_JSON  # a header of its own never replaces the type
    assert response.json() == {
        "type": "urn:ledger:error:slow_body",
        "title": "Request Timeout",
        "status": 408,
        "code": "slow_body",
        "detail": "the body did not arrive in time",
    }
    assert (status_of(SlowBodyError()), status_of(AnsweredDefectError())) == (408, 500)
    assert (broken.status_code, broken.json()["code"], "retry-after" in broken.headers) == (
        500,
        "internal",
        False,
    )
    assert [entry["code"] for entry in logs] == ["answered_defect"]
