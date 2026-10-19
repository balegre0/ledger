"""The body limit (§10): a request over it is refused 413 before anything reads it, whatever it declares."""

from contextlib import asynccontextmanager
from typing import ClassVar, Final, final
lazy from collections.abc import AsyncGenerator, AsyncIterator

import httpx
import pytest
from fastapi import APIRouter, Depends, HTTPException
from opentelemetry.sdk.trace import TracerProvider
from prometheus_client import CollectorRegistry
from pydantic import BaseModel, ConfigDict
lazy from fastapi import FastAPI

from ledger_platform.config.options import InvalidOptionError
from ledger_platform.http.app import ApiInfo, Observability, create_app
from ledger_platform.http.limits import MAX_BODY_BYTES, MAX_UPLOAD_BYTES, larger_body
from ledger_platform.http.problems import Problem
from ledger_platform.observability.health import Health

LIMIT: Final = 64


class Note(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    text: str


@final
class Handled:
    """What the route saw: how many times it ran."""

    def __init__(self) -> None:
        self.calls = 0


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    del app
    yield


def app_of(handled: Handled, *, limit: int = LIMIT) -> FastAPI:
    router = APIRouter()

    @router.post("/echo")
    async def echo(note: Note) -> Note:
        handled.calls += 1
        return note

    @router.post("/refused")
    async def refused(note: Note) -> Note:
        del note
        raise HTTPException(status_code=401)

    @router.post("/ignores-its-body")
    async def ignores() -> dict[str, bool]:
        handled.calls += 1
        return {"ok": True}

    @router.post("/x/special")
    async def special(note: Note) -> Note:
        """Declared first: the router dispatches ``/x/special`` here, so the app's limit holds."""
        handled.calls += 1
        return note

    @router.post("/x/{item}", dependencies=[Depends(larger_body(LIMIT * 2))])
    async def item(item: str, note: Note) -> Note:
        del item
        handled.calls += 1
        return note

    @router.post("/uploads/{name}", dependencies=[Depends(larger_body(LIMIT * 2))])
    async def upload(name: str, note: Note) -> Note:
        """Takes up to twice the app's limit (a certificate's upload takes 2 MiB where the rest takes 1)."""
        del name
        handled.calls += 1
        return note

    observability = Observability(
        health=Health(), registry=CollectorRegistry(), tracer_provider=TracerProvider()
    )
    return create_app(
        info=ApiInfo(title="t", version="1"),
        observability=observability,
        lifespan=lifespan,
        routers=[router],
        max_body_bytes=limit,
    )


def client_of(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def small() -> bytes:
    return b'{"text":"x"}'


def large() -> bytes:
    return b'{"text":"' + b"x" * LIMIT + b'"}'


async def chunks(*parts: bytes) -> AsyncIterator[bytes]:
    for part in parts:
        yield part


def test_the_default_limit_is_one_mebibyte() -> None:
    assert MAX_BODY_BYTES == 1024 * 1024


async def test_a_body_within_the_limit_is_handled() -> None:
    handled = Handled()
    async with client_of(app_of(handled)) as client:
        response = await client.post("/echo", content=small(), headers={"Content-Type": "application/json"})
    assert (response.status_code, handled.calls) == (200, 1)


async def test_a_body_whose_declared_length_is_over_the_limit_is_refused_unread() -> None:
    handled = Handled()
    async with client_of(app_of(handled)) as client:
        response = await client.post("/echo", content=large(), headers={"Content-Type": "application/json"})
    assert (response.status_code, handled.calls) == (413, 0)
    assert response.headers["content-type"] == "application/problem+json"
    problem = Problem.model_validate_json(response.content)
    assert (problem.code, problem.status, problem.type) == (
        "payload_too_large",
        413,
        "urn:ledger:error:payload_too_large",
    )
    assert problem.detail is not None
    assert str(LIMIT) in problem.detail
    assert "xxxx" not in response.text


async def test_a_streamed_body_over_the_limit_is_refused_as_it_grows() -> None:
    handled = Handled()
    stream = chunks(b'{"text":"', b"x" * LIMIT, b'"}')
    async with client_of(app_of(handled)) as client:
        response = await client.post("/echo", content=stream, headers={"Content-Type": "application/json"})
    assert (response.status_code, handled.calls) == (413, 0)
    assert Problem.model_validate_json(response.content).code == "payload_too_large"


async def test_a_streamed_body_at_the_limit_is_handled() -> None:
    handled = Handled()
    exact = b'{"text":"' + b"x" * (LIMIT - len(b'{"text":""}')) + b'"}'
    assert len(exact) == LIMIT
    async with client_of(app_of(handled)) as client:
        response = await client.post(
            "/echo", content=chunks(exact[:10], exact[10:]), headers={"Content-Type": "application/json"}
        )
    assert (response.status_code, handled.calls) == (200, 1)


async def test_the_limit_is_met_before_the_route_can_refuse_the_caller() -> None:
    """Nothing of an oversized body is read to learn it: the answer is 413 even where a 401 would follow."""
    async with client_of(app_of(Handled())) as client:
        response = await client.post(
            "/refused", content=large(), headers={"Content-Type": "application/json"}
        )
    assert response.status_code == 413


async def test_a_body_declared_over_the_limit_is_refused_even_where_the_route_would_not_read_it() -> None:
    handled = Handled()
    async with client_of(app_of(handled)) as client:
        response = await client.post("/ignores-its-body", content=large())
    assert (response.status_code, handled.calls) == (413, 0)


async def test_a_body_that_declares_nothing_is_not_refused_before_it_is_read() -> None:
    handled = Handled()
    async with client_of(app_of(handled)) as client:
        response = await client.post("/ignores-its-body", content=chunks(b"x" * LIMIT))
    assert (response.status_code, handled.calls) == (200, 1)


async def test_a_route_may_take_a_larger_body_and_only_that_route() -> None:
    handled = Handled()
    over = b'{"text":"' + b"x" * (LIMIT + 10) + b'"}'  # over the app's limit, within the route's
    async with client_of(app_of(handled)) as client:
        taken = await client.post(
            "/uploads/a.p12", content=over, headers={"Content-Type": "application/json"}
        )
        elsewhere = await client.post("/echo", content=over, headers={"Content-Type": "application/json"})
        streamed = await client.post(
            "/uploads/b.p12",
            content=chunks(over[:20], over[20:]),
            headers={"Content-Type": "application/json"},
        )
        beyond = await client.post(
            "/uploads/c.p12", content=b"x" * (LIMIT * 2 + 1), headers={"Content-Type": "application/json"}
        )
    assert (taken.status_code, elsewhere.status_code, streamed.status_code) == (200, 413, 200)
    assert (beyond.status_code, handled.calls) == (413, 2)
    assert str(LIMIT * 2) in (Problem.model_validate_json(beyond.content).detail or "")


async def test_the_limit_is_the_one_of_the_route_the_request_is_dispatched_to() -> None:
    """``/x/special`` matches ``/x/{item}`` too, but the router sends it to ``/x/special``, declared first."""
    handled = Handled()
    over = b'{"text":"' + b"x" * (LIMIT + 10) + b'"}'
    async with client_of(app_of(handled)) as client:
        special = await client.post("/x/special", content=over, headers={"Content-Type": "application/json"})
        other = await client.post("/x/other", content=over, headers={"Content-Type": "application/json"})
    assert (special.status_code, other.status_code, handled.calls) == (413, 200, 1)


def test_the_largest_body_a_route_may_take_is_two_mebibytes() -> None:
    assert MAX_UPLOAD_BYTES == 2 * 1024 * 1024
    for limit in (0, MAX_UPLOAD_BYTES + 1):
        with pytest.raises(InvalidOptionError) as caught:
            larger_body(limit)
        assert caught.value.component == "http"


@pytest.mark.parametrize("limit", [0, -1])
def test_a_limit_below_one_byte_is_refused(limit: int) -> None:
    with pytest.raises(InvalidOptionError) as caught:
        app_of(Handled(), limit=limit)
    assert caught.value.component == "http"
