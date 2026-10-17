"""``Ledger-Version`` (§9.2): header parsing, the pins' precedence and the echo on every answer."""

from contextlib import asynccontextmanager
from datetime import date
from typing import Annotated, Final, final, override
lazy from collections.abc import AsyncGenerator

import httpx
import pytest
from fastapi import APIRouter, Depends
from pydantic import TypeAdapter
lazy from fastapi import FastAPI
lazy from prometheus_client import CollectorRegistry

from ledger_kernel.access import SubjectId
from ledger_platform.auth.providers.zitadel import organization_of
from ledger_platform.auth.tokens import TokenPolicy, TokenVerifier
from ledger_platform.config.options import InvalidOptionError
from ledger_platform.http import create_app
from ledger_platform.http.app import ApiInfo, Observability
from ledger_platform.http.authentication import Authentication
from ledger_platform.http.deps import authenticated
from ledger_platform.http.versions import (
    CURRENT_VERSION,
    KNOWN_VERSIONS,
    VERSION_HEADER,
    ApiVersion,
    CachedPins,
    Pinned,
    Pins,
    VersionUnknownError,
    parse_version,
    resolve,
)
from ledger_platform.observability.health import Health
from ledger_testing.issuer import AUDIENCE, ISSUER
lazy from ledger_kernel.access import Principal
lazy from ledger_platform.observability.tracing import Tracing
lazy from ledger_testing.issuer import LocalIssuer

OLDER: Final = ApiVersion(date(2026, 1, 1))
NEWER: Final = ApiVersion(date(2026, 6, 1))
PROBLEM: Final = TypeAdapter(dict[str, object])


@final
class Pinning(Pins):
    """What the caller's client and organization are pinned to; counts the questions."""

    def __init__(self) -> None:
        self.pinned = Pinned(client=None, organization=None)
        self.asked = 0

    @override
    async def of(self, principal: Principal) -> Pinned:
        del principal
        self.asked += 1
        return self.pinned


@pytest.fixture
def pins() -> Pinning:
    return Pinning()


@asynccontextmanager
async def nothing_to_start(app: FastAPI) -> AsyncGenerator[None]:
    del app
    yield


@pytest.fixture
def app(local_issuer: LocalIssuer, pins: Pinning, registry: CollectorRegistry, tracing: Tracing) -> FastAPI:
    verifier = TokenVerifier(TokenPolicy(issuer=ISSUER, audience=AUDIENCE, keys_url=local_issuer.keys_url))
    caller = Authentication(authenticated(verifier), None).versioned(pins)
    routes = APIRouter()

    @routes.get("/thing")
    async def thing(principal: Annotated[Principal, Depends(caller)]) -> str:
        return principal.subject

    @routes.get("/open")
    async def anyone() -> str:
        return "open"

    return create_app(
        info=ApiInfo(title="versions", version="1.0"),
        observability=Observability(health=Health(), registry=registry, tracer_provider=tracing.provider),
        lifespan=nothing_to_start,
        routers=(routes,),
    )


@pytest.fixture
async def client(app: FastAPI) -> AsyncGenerator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as opened:
        yield opened


def bearer(local_issuer: LocalIssuer, **headers: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {local_issuer.token()}"} | headers


def raw(local_issuer: LocalIssuer, version: str) -> list[tuple[bytes, bytes]]:
    """The caller's token and a ``Ledger-Version`` of any text, as the bytes it travels in."""
    return [
        (b"authorization", f"Bearer {local_issuer.token()}".encode("ascii")),
        (VERSION_HEADER.encode("ascii"), version.encode("utf-8")),
    ]


# ── Reading ─────────────────────────────────────────────────────────────────────


def test_a_version_is_a_known_date_in_its_one_written_form() -> None:
    assert parse_version(CURRENT_VERSION.isoformat()) == CURRENT_VERSION
    assert CURRENT_VERSION in KNOWN_VERSIONS
    assert max(KNOWN_VERSIONS) == CURRENT_VERSION
    assert list(KNOWN_VERSIONS) == sorted(set(KNOWN_VERSIONS))  # oldest first, each once


@pytest.mark.parametrize(
    "text",
    [
        "2020-01-01",  # a date of no version
        "2026-13-45",
        "2026-02-30",
        "tomorrow",
        "",
        " 2026-10-13",
        "2026-10-13 ",
        "2026-10-13T00:00:00",
        "20261013",
        "2026-1-3",
        "\uff12\uff10\uff12\uff16-\uff11\uff10-\uff11\uff13",  # digits that are not ASCII
        "+2026-10-13",
        "x" * 500,
    ],
)
def test_text_that_is_no_version_is_refused_and_not_repeated(text: str) -> None:
    with pytest.raises(VersionUnknownError) as caught:
        parse_version(text)
    assert caught.value.code == "version_unknown"
    assert len(text) < 20 or text not in str(caught.value)


def test_the_error_names_the_versions_there_are() -> None:
    with pytest.raises(VersionUnknownError, match=CURRENT_VERSION.isoformat()):
        parse_version("2020-01-01")


def test_the_request_s_version_beats_its_client_s_pin_which_beats_its_organization_s() -> None:
    both = Pinned(client=OLDER, organization=NEWER)
    assert resolve(NEWER, both) == NEWER
    assert resolve(None, both) == OLDER
    assert resolve(None, Pinned(client=None, organization=NEWER)) == NEWER
    assert resolve(None, Pinned(client=None, organization=None)) == CURRENT_VERSION


# ── On the wire ─────────────────────────────────────────────────────────────────


async def test_without_a_header_or_a_pin_a_request_gets_the_current_version(
    client: httpx.AsyncClient, local_issuer: LocalIssuer, pins: Pinning
) -> None:
    response = await client.get("/thing", headers=bearer(local_issuer))
    assert (response.status_code, response.headers[VERSION_HEADER]) == (200, CURRENT_VERSION.isoformat())
    assert pins.asked == 1


async def test_a_request_that_names_a_version_gets_it_and_asks_nobody_s_pin(
    client: httpx.AsyncClient, local_issuer: LocalIssuer, pins: Pinning
) -> None:
    pins.pinned = Pinned(client=OLDER, organization=NEWER)
    sent = CURRENT_VERSION.isoformat()
    for name in (VERSION_HEADER, VERSION_HEADER.lower()):  # header names are case-insensitive
        response = await client.get("/thing", headers=bearer(local_issuer) | {name: sent})
        assert (response.status_code, response.headers[VERSION_HEADER]) == (200, sent)
    assert pins.asked == 0


async def test_without_a_header_the_pins_decide(
    client: httpx.AsyncClient, local_issuer: LocalIssuer, pins: Pinning
) -> None:
    pins.pinned = Pinned(client=None, organization=NEWER)
    organization = await client.get("/thing", headers=bearer(local_issuer))
    pins.pinned = Pinned(client=OLDER, organization=NEWER)
    both = await client.get("/thing", headers=bearer(local_issuer))
    assert (organization.headers[VERSION_HEADER], both.headers[VERSION_HEADER]) == (
        NEWER.isoformat(),
        OLDER.isoformat(),
    )


@pytest.mark.parametrize(
    "text",
    ["2020-01-01", "tomorrow", "2026-13-45", "x" * 500, "\uff12\uff10\uff12\uff16-\uff11\uff10-\uff11\uff13"],
)
async def test_a_version_that_does_not_exist_is_refused_once_the_caller_is_proven(
    client: httpx.AsyncClient, local_issuer: LocalIssuer, pins: Pinning, text: str
) -> None:
    refused = await client.get("/thing", headers=raw(local_issuer, text))
    assert (refused.status_code, PROBLEM.validate_json(refused.content)["code"]) == (400, "version_unknown")
    assert len(text) < 20 or text not in refused.text
    assert pins.asked == 0
    anonymous = await client.get("/thing", headers=raw(local_issuer, text)[1:])
    assert (anonymous.status_code, PROBLEM.validate_json(anonymous.content)["code"]) == (
        401,
        "missing_token",
    )


async def test_two_versions_sent_are_refused_rather_than_one_of_them_chosen(
    client: httpx.AsyncClient, local_issuer: LocalIssuer
) -> None:
    headers = [
        ("Authorization", f"Bearer {local_issuer.token()}"),
        (VERSION_HEADER, CURRENT_VERSION.isoformat()),
        (VERSION_HEADER, CURRENT_VERSION.isoformat()),
    ]
    refused = await client.get("/thing", headers=headers)
    assert (refused.status_code, PROBLEM.validate_json(refused.content)["code"]) == (400, "version_unknown")


async def test_every_answer_names_its_version_those_of_routes_that_ask_nobody_and_failures_too(
    client: httpx.AsyncClient,
) -> None:
    sent = CURRENT_VERSION.isoformat()
    for path, headers in (("/open", {}), ("/open", {VERSION_HEADER: sent}), ("/thing", {}), ("/nowhere", {})):
        response = await client.get(path, headers=headers)
        assert response.headers[VERSION_HEADER] == sent, path


async def test_a_header_that_is_no_version_does_not_stop_a_route_that_reads_none(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/open", headers={VERSION_HEADER: "tomorrow"})
    assert (response.status_code, response.headers[VERSION_HEADER]) == (200, CURRENT_VERSION.isoformat())


async def test_the_operational_routes_say_nothing_of_versions(client: httpx.AsyncClient) -> None:
    for path in ("/livez", "/readyz", "/metrics"):
        assert VERSION_HEADER not in (await client.get(path)).headers


def test_the_document_says_every_route_takes_the_header(app: FastAPI) -> None:
    paths = TypeAdapter(dict[str, dict[str, dict[str, object]]]).validate_python(app.openapi()["paths"])
    for path in ("/thing", "/open"):
        found = TypeAdapter(list[dict[str, object]]).validate_python(paths[path]["get"]["parameters"])
        assert [(parameter["name"], parameter["in"], parameter["required"]) for parameter in found] == [
            (VERSION_HEADER, "header", False)
        ]
        assert found[0]["schema"] == {
            "type": "string",
            "format": "date",
            "enum": [version.isoformat() for version in KNOWN_VERSIONS],
        }
    assert not {"/livez", "/readyz", "/metrics"} & set(paths)  # the probes are not in the document


# ── Remembering pins ────────────────────────────────────────────────────────────


def principal_of(subject: str, organization: str = "1001") -> Principal:
    return Principal(
        subject=SubjectId(subject),
        organization=organization_of(organization),
        provider_organization=organization,
    )


@final
class Time:
    """A clock that moves when told."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


async def test_pins_are_asked_once_for_a_while_and_for_each_caller() -> None:
    pins, time = Pinning(), Time()
    cached = CachedPins(pins, ttl=30.0, size=10, clock=time)
    ana, bob, other = principal_of("ana"), principal_of("bob"), principal_of("ana", "1002")
    pins.pinned = Pinned(client=OLDER, organization=NEWER)
    assert await cached.of(ana) == pins.pinned
    assert await cached.of(ana) == pins.pinned
    assert pins.asked == 1
    await cached.of(bob)
    await cached.of(other)  # the same subject of another organization is another caller
    assert pins.asked == 3
    time.now += 29.9
    await cached.of(ana)
    assert pins.asked == 3
    time.now += 0.2  # past the ttl: asked again, and a changed pin shows
    pins.pinned = Pinned(client=None, organization=NEWER)
    assert await cached.of(ana) == pins.pinned
    assert pins.asked == 4


async def test_the_pins_remembered_are_bounded_forgetting_the_oldest_first() -> None:
    pins, time = Pinning(), Time()
    pins.pinned = Pinned(client=None, organization=NEWER)
    cached = CachedPins(pins, ttl=30.0, size=2, clock=time)
    one, two, three = principal_of("1"), principal_of("2"), principal_of("3")
    for caller in (one, two, three):
        await cached.of(caller)
    assert pins.asked == 3
    await cached.of(three)
    await cached.of(two)
    assert pins.asked == 3  # the two newest are kept
    await cached.of(one)
    assert pins.asked == 4  # the oldest was forgotten


async def test_a_caller_with_no_pins_is_asked_again_until_it_has_some() -> None:
    """Its organization is not known yet; the next question must see it once it is."""
    pins = Pinning()
    cached = CachedPins(pins, ttl=30.0, size=10, clock=Time())
    ana = principal_of("ana")
    assert await cached.of(ana) == Pinned(client=None, organization=None)
    pins.pinned = Pinned(client=None, organization=NEWER)
    assert await cached.of(ana) == Pinned(client=None, organization=NEWER)
    assert await cached.of(ana) == Pinned(client=None, organization=NEWER)
    assert pins.asked == 2


async def test_a_caller_that_failed_to_be_asked_is_asked_again() -> None:
    @final
    class Failing(Pins):
        def __init__(self) -> None:
            self.asked = 0

        @override
        async def of(self, principal: Principal) -> Pinned:
            del principal
            self.asked += 1
            if self.asked == 1:
                raise ConnectionError
            return Pinned(client=None, organization=None)

    failing = Failing()
    cached = CachedPins(failing, ttl=30.0, size=2, clock=Time())
    with pytest.raises(ConnectionError):
        await cached.of(principal_of("ana"))
    assert await cached.of(principal_of("ana")) == Pinned(client=None, organization=None)


def test_a_cache_that_remembers_nothing_is_a_mistake() -> None:
    for ttl, size in ((0.0, 1), (-1.0, 1), (30.0, 0)):
        with pytest.raises(InvalidOptionError):
            CachedPins(Pinning(), ttl=ttl, size=size)
