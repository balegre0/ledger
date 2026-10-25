"""The in-process rate limit (§10): GCRA per credential, with an injected clock (no test waits)."""

from contextlib import asynccontextmanager
from typing import Annotated, Final, final, override
lazy from collections.abc import AsyncGenerator

import httpx
import pytest
from fastapi import APIRouter, Depends
from hypothesis import given
from hypothesis import strategies as st
from opentelemetry.sdk.trace import TracerProvider
from prometheus_client import CollectorRegistry
lazy from fastapi import FastAPI

from ledger_kernel.access import Principal, PrincipalKind, SubjectId
from ledger_platform.auth.providers.zitadel import organization_of
from ledger_platform.config.options import InvalidOptionError
from ledger_platform.http.app import ApiInfo, Edge, Observability, create_app
from ledger_platform.http.deps import Verifier
from ledger_platform.http.probes import OPERATIONAL_PATHS
from ledger_platform.http.problems import Problem
from ledger_platform.http.proving import authenticated
from ledger_platform.http.rate import ClientRate, RateLimitedError, RateLimiter, client_address
from ledger_platform.observability.health import Health

KEY: Final = "org:subject"


@final
class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def limiter_of(clock: Clock, *, rate: float = 2.0, burst: int = 3, keys: int = 100) -> RateLimiter:
    return RateLimiter(rate=rate, burst=burst, clock=clock, max_keys=keys)


def test_a_credential_may_burst_and_is_then_held_to_the_rate() -> None:
    clock = Clock()
    limiter = limiter_of(clock)  # 2 per second, a burst of 3
    assert [limiter.wait(KEY) for _ in range(3)] == [0.0, 0.0, 0.0]
    assert limiter.wait(KEY) == pytest.approx(0.5)  # the fourth waits one interval (1 / 2 s)
    clock.now += 0.5
    assert limiter.wait(KEY) == 0.0  # an interval later, one more is allowed
    assert limiter.wait(KEY) == pytest.approx(0.5)


def test_a_refused_request_does_not_use_up_the_allowance() -> None:
    clock = Clock()
    limiter = limiter_of(clock)
    for _ in range(3):
        limiter.wait(KEY)
    assert limiter.wait(KEY) > 0
    assert limiter.wait(KEY) == pytest.approx(0.5)  # asking again earns no further delay
    clock.now += 0.5
    assert limiter.wait(KEY) == 0.0


def test_an_idle_credential_has_its_whole_burst_back() -> None:
    clock = Clock()
    limiter = limiter_of(clock)
    for _ in range(3):
        limiter.wait(KEY)
    clock.now += 60
    assert [limiter.wait(KEY) for _ in range(3)] == [0.0, 0.0, 0.0]
    assert limiter.wait(KEY) > 0


def test_credentials_do_not_share_an_allowance() -> None:
    clock = Clock()
    limiter = limiter_of(clock)
    for _ in range(3):
        limiter.wait("a")
    assert limiter.wait("a") > 0
    assert limiter.wait("b") == 0.0


def test_the_credentials_it_remembers_are_bounded_and_idle_ones_make_room_one_at_a_time() -> None:
    clock = Clock()
    limiter = limiter_of(clock, keys=3)
    for key in ("a", "b", "c"):
        limiter.wait(key)
    clock.now += 60  # a, b and c are idle: each is forgotten when room is needed, not before
    for key in ("d", "e", "f"):
        assert limiter.wait(key) == 0.0
        assert limiter.remembered() == 3


def test_when_every_credential_is_active_a_new_one_is_allowed_and_no_allowance_is_reset() -> None:
    """The oldest active credential is set aside with its arrival time, not forgotten: no new caller waits for
    room, and the one set aside comes back held to the rate it had used."""
    clock = Clock()
    limiter = limiter_of(clock, rate=1.0, burst=1, keys=2)
    assert (limiter.wait("a"), limiter.wait("b")) == (0.0, 0.0)
    clock.now += 0.5
    assert limiter.wait("c") == 0.0  # a is set aside: no lockout
    assert (limiter.remembered(), limiter.set_aside()) == (2, 1)
    assert limiter.wait("a") == pytest.approx(0.5)  # back, still held: its allowance was not reset


def test_what_is_set_aside_is_bounded_too() -> None:
    """Past ``max_keys`` plus ``max_set_aside`` active credentials, the oldest set aside is forgotten."""
    clock = Clock()
    limiter = RateLimiter(rate=1.0, burst=1, clock=clock, max_keys=1, max_set_aside=1)
    for key in ("a", "b", "c"):
        assert limiter.wait(key) == 0.0
    assert (limiter.remembered(), limiter.set_aside()) == (1, 1)
    assert limiter.wait("a") == 0.0  # forgotten: the bound is what a flood this large costs


def test_the_credential_allowed_least_recently_goes_first() -> None:
    clock = Clock()
    limiter = limiter_of(clock, rate=1.0, burst=2, keys=2)
    limiter.wait("a")
    limiter.wait("b")
    clock.now += 2
    limiter.wait("a")  # a is now the most recent: b goes first when room is needed
    assert limiter.wait("c") == 0.0
    assert limiter.wait("a") == 0.0  # a kept its place and the rest of its burst
    assert limiter.wait("a") > 0


@pytest.mark.parametrize(
    ("rate", "burst", "keys", "option"),
    [(0.0, 1, 1, "rate"), (float("inf"), 1, 1, "rate"), (1.0, 0, 1, "burst"), (1.0, 1, 0, "max_keys")],
)
def test_options_that_cannot_work_are_refused(rate: float, burst: int, keys: int, option: str) -> None:
    with pytest.raises(InvalidOptionError) as caught:
        RateLimiter(rate=rate, burst=burst, max_keys=keys)
    assert caught.value.component == "rate limit"
    assert option in str(caught.value)
    with pytest.raises(InvalidOptionError, match="max_set_aside"):
        RateLimiter(rate=1.0, burst=1, max_set_aside=0)


def test_the_refusal_says_when_to_come_back_in_whole_seconds() -> None:
    assert RateLimitedError(0.2).retry_after == 1
    assert RateLimitedError(1.0).retry_after == 1
    assert RateLimitedError(1.01).retry_after == 2
    assert RateLimitedError(0.2).code == "rate_limited"


@final
class ByToken(Verifier):
    """Proves a token to the subject it spells."""

    @override
    async def verify(self, token: str) -> Principal:
        return Principal(
            subject=SubjectId(token),
            organization=organization_of("1001"),
            provider_organization="1001",
            kind=PrincipalKind.HUMAN,
        )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    del app
    yield


def guarded_app(limiter: RateLimiter | None) -> FastAPI:
    caller = authenticated(ByToken(), limiter)
    router = APIRouter()

    @router.get("/me")
    async def me(principal: Annotated[Principal, Depends(caller)]) -> dict[str, str]:
        return {"subject": principal.subject}

    @router.get("/slow-down")
    async def slow_down() -> None:
        raise RateLimitedError(2.5)

    observability = Observability(
        health=Health(), registry=CollectorRegistry(), tracer_provider=TracerProvider()
    )
    return create_app(
        info=ApiInfo(title="t", version="1"), observability=observability, lifespan=lifespan, routers=[router]
    )


async def get_as(app: FastAPI, subject: str, path: str = "/me") -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path, headers={"Authorization": f"Bearer {subject}"})


async def test_a_caller_over_its_rate_is_refused_after_it_is_proven_and_others_are_not() -> None:
    app = guarded_app(limiter_of(Clock(), rate=1.0, burst=2))
    first = [(await get_as(app, "s1")).status_code for _ in range(2)]
    over = await get_as(app, "s1")
    other = await get_as(app, "s2")
    assert (first, over.status_code, other.status_code) == ([200, 200], 429, 200)
    assert over.headers["retry-after"] == "1"
    problem = Problem.model_validate_json(over.content)
    assert (problem.code, problem.status) == ("rate_limited", 429)


async def test_an_unproven_caller_is_refused_401_and_never_counted() -> None:
    limiter = limiter_of(Clock(), rate=1.0, burst=1)
    app = guarded_app(limiter)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/me")
    assert response.status_code == 401
    assert limiter.remembered() == 0


async def test_an_app_without_a_limiter_does_not_limit() -> None:
    app = guarded_app(None)
    assert {(await get_as(app, "s1")).status_code for _ in range(20)} == {200}


async def test_a_refusal_to_wait_names_whole_seconds_in_retry_after() -> None:
    response = await get_as(guarded_app(None), "s1", "/slow-down")
    assert (response.status_code, response.headers["retry-after"]) == (429, "3")


def by_client_app(limiter: RateLimiter, *, forwarded_hops: int = 0) -> FastAPI:
    """An app with the receiver's edge (every client address held to ``limiter``, no document, the operational
    routes for direct callers alone), with one route of its own."""
    router = APIRouter()

    @router.get("/ping")
    async def ping() -> str:
        return "pong"

    observability = Observability(
        health=Health(), registry=CollectorRegistry(), tracer_provider=TracerProvider()
    )
    return create_app(
        info=ApiInfo(title="t", version="1"),
        observability=observability,
        lifespan=lifespan,
        routers=[router],
        edge=Edge(
            client_rate=ClientRate(limiter=limiter, forwarded_hops=forwarded_hops),
            documents=False,
            direct_operational=True,
        ),
    )


async def get_from(
    app: FastAPI, address: str, path: str = "/ping", *, forwarded: tuple[str, ...] = ()
) -> httpx.Response:
    """``path`` asked by a peer at ``address``, through proxies that wrote ``forwarded`` (one header each)."""
    transport = httpx.ASGITransport(app=app, client=(address, 4711))
    headers = [("X-Forwarded-For", value) for value in forwarded]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path, headers=headers)


async def test_a_client_address_over_its_rate_is_refused_before_the_app_and_others_are_not() -> None:
    app = by_client_app(limiter_of(Clock(), rate=1.0, burst=2))
    first = [(await get_from(app, "203.0.113.7")).status_code for _ in range(2)]
    over = await get_from(app, "203.0.113.7")
    other = await get_from(app, "198.51.100.1")
    assert (first, over.status_code, other.status_code) == ([200, 200], 429, 200)
    assert over.headers["retry-after"] == "1"
    problem = Problem.model_validate_json(over.content)
    assert (problem.code, problem.status) == ("rate_limited", 429)


async def test_probes_and_metrics_are_never_held_to_a_client_s_rate() -> None:
    app = by_client_app(limiter_of(Clock(), rate=1.0, burst=1))
    assert (await get_from(app, "10.0.0.1")).status_code == 200
    assert (await get_from(app, "10.0.0.1")).status_code == 429
    for path in ("/livez", "/readyz", "/metrics"):
        assert (await get_from(app, "10.0.0.1", path)).status_code == 200


@pytest.mark.parametrize("hops", [0, 1])
async def test_through_a_proxy_the_probes_and_metrics_are_not_found(hops: int) -> None:
    """The receiver's host is the internet's: a prober or a scraper reaches a replica directly, and sends no
    ``X-Forwarded-For``; a request that came through a proxy (it does, whatever the proxies declared) is
    answered as for a path that does not exist."""
    app = by_client_app(limiter_of(Clock(), rate=1.0, burst=100), forwarded_hops=hops)
    for path in sorted(OPERATIONAL_PATHS):
        proxied = await get_from(app, "10.0.0.1", path, forwarded=("192.0.2.1",))
        direct = await get_from(app, "10.0.0.1", path)
        assert (path, proxied.status_code, direct.status_code) == (path, 404, 200)
        problem = Problem.model_validate_json(proxied.content)
        assert (problem.code, problem.status) == ("not_found", 404)


async def test_a_probe_through_a_proxy_is_held_to_its_client_s_rate() -> None:
    app = by_client_app(limiter_of(Clock(), rate=1.0, burst=1), forwarded_hops=1)
    first = await get_from(app, "10.0.0.1", "/readyz", forwarded=("192.0.2.1",))
    second = await get_from(app, "10.0.0.1", "/metrics", forwarded=("192.0.2.1",))
    assert (first.status_code, second.status_code) == (404, 429)


@pytest.mark.parametrize("path", ["/openapi.json", "/docs", "/redoc", "/docs/oauth2-redirect"])
async def test_the_receiver_s_edge_serves_no_document(path: str) -> None:
    """Its callers are tax authorities and emitters, not our developers: nothing describes its routes."""
    response = await get_from(by_client_app(limiter_of(Clock(), rate=1.0, burst=100)), "10.0.0.1", path)
    assert response.status_code == 404


async def test_ipv6_clients_are_held_to_a_rate_by_their_64_bit_prefix() -> None:
    """A subscriber gets a /64 and may use any address in it: per address, it would have 2**64 allowances."""
    app = by_client_app(limiter_of(Clock(), rate=1.0, burst=1))
    assert (await get_from(app, "2001:db8::1")).status_code == 200
    assert (await get_from(app, "2001:db8::ffff:2")).status_code == 429  # the same /64
    assert (await get_from(app, "2001:db8:0:1::1")).status_code == 200  # the next /64


async def test_without_proxies_a_forwarded_address_is_ignored() -> None:
    """Anyone can write ``X-Forwarded-For``: with no proxy declared, the peer is the client."""
    app = by_client_app(limiter_of(Clock(), rate=1.0, burst=1))
    assert (await get_from(app, "10.0.0.1", forwarded=("192.0.2.1",))).status_code == 200
    assert (await get_from(app, "10.0.0.1", forwarded=("192.0.2.2",))).status_code == 429


async def test_behind_proxies_the_client_is_the_address_the_outermost_proxy_wrote() -> None:
    """With one proxy, the last address it appended is the client; what the client wrote before is not."""
    app = by_client_app(limiter_of(Clock(), rate=1.0, burst=1), forwarded_hops=1)
    assert (await get_from(app, "10.0.0.1", forwarded=("192.0.2.9, 192.0.2.1",))).status_code == 200
    assert (await get_from(app, "10.0.0.1", forwarded=("192.0.2.8, 192.0.2.1",))).status_code == 429
    assert (await get_from(app, "10.0.0.1", forwarded=("192.0.2.2",))).status_code == 200  # another client


@pytest.mark.parametrize(
    ("hops", "forwarded", "client", "address"),
    [
        (0, ("192.0.2.1",), ("10.0.0.1", 1), "10.0.0.1"),
        (1, ("192.0.2.9 , 192.0.2.1",), ("10.0.0.1", 1), "192.0.2.1"),
        (2, ("192.0.2.9, 192.0.2.1", "198.51.100.4"), ("10.0.0.1", 1), "192.0.2.1"),
        (2, ("192.0.2.1",), ("10.0.0.1", 1), "10.0.0.1"),  # fewer than declared: it did not pass them all
        (1, (" , ",), ("10.0.0.1", 1), "10.0.0.1"),
        (0, (), None, "unknown"),
        (0, (), ("2001:db8::1", 1), "2001:db8::/64"),
        (1, ("2001:DB8:0:0:abcd::9",), ("10.0.0.1", 1), "2001:db8::/64"),
        (0, (), ("::ffff:192.0.2.1", 1), "192.0.2.1"),
        (1, ("::ffff:192.0.2.1",), ("10.0.0.1", 1), "192.0.2.1"),
        (1, ("not-an-address",), ("10.0.0.1", 1), "10.0.0.1"),
        (1, ("192.0.2.1:4711",), ("10.0.0.1", 1), "10.0.0.1"),
        (1, ("192.0.2.1",), ("testclient", 1), "192.0.2.1"),
        (0, (), ("testclient", 1), "testclient"),
    ],
    ids=[
        "peer",
        "one proxy",
        "two proxies, two headers",
        "short chain",
        "empty chain",
        "no peer",
        "ipv6 peer",
        "ipv6 forwarded",
        "ipv4-mapped peer",
        "ipv4-mapped forwarded",
        "forwarded not an address",
        "forwarded with a port",
        "peer not an address, forwarded",
        "peer not an address",
    ],
)
def test_the_client_address_of_a_request(
    hops: int, forwarded: tuple[str, ...], client: tuple[str, int] | None, address: str
) -> None:
    headers = [(b"x-forwarded-for", value.encode()) for value in forwarded]
    scope: dict[str, object] = {"type": "http", "path": "/", "headers": headers, "client": client}
    assert client_address(scope, forwarded_hops=hops) == address


@pytest.mark.parametrize("hops", [-1, 6])
def test_the_number_of_proxies_is_bounded(hops: int) -> None:
    with pytest.raises(InvalidOptionError, match="forwarded_hops"):
        ClientRate(limiter=limiter_of(Clock()), forwarded_hops=hops)


@given(
    now=st.floats(min_value=1.0, max_value=1e9, allow_nan=False),
    rate=st.floats(min_value=1e-6, max_value=1e4, allow_nan=False),
    burst=st.integers(min_value=1, max_value=1000),
)
def test_a_credential_that_has_never_asked_is_always_allowed_its_first_request(
    now: float, rate: float, burst: int
) -> None:
    clock = Clock()
    clock.now = now
    assert limiter_of(clock, rate=rate, burst=burst).wait(KEY) == 0.0
