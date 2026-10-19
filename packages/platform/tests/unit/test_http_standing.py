"""How a caller stands (§9.2, §9.7): the version it speaks, the mode its credential carries, revocation.

Identity answers ``Standings``; ``with_standing`` settles the request by it, and every answer names the
version the request speaks (``Ledger-Version``).
"""

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

from ledger_kernel.access import PrincipalKind, SubjectId
from ledger_kernel.tenancy import Mode
from ledger_platform.auth.providers.zitadel import organization_of
from ledger_platform.auth.tokens import TokenPolicy, TokenVerifier
from ledger_platform.config.options import InvalidOptionError
from ledger_platform.http import create_app
from ledger_platform.http.app import ApiInfo, Observability
from ledger_platform.http.authentication import Authentication
from ledger_platform.http.deps import authenticated
from ledger_platform.http.mode import ledger_mode_of
from ledger_platform.http.standing import (
    ApiClientRevokedError,
    ApiClientUnregisteredError,
    CachedStandings,
    Standing,
    Standings,
    resolve,
)
from ledger_platform.http.versions import (
    CURRENT_VERSION,
    KNOWN_VERSIONS,
    VERSION_HEADER,
    ApiVersion,
    VersionUnknownError,
    parse_version,
)
from ledger_platform.observability.health import Health
from ledger_testing.issuer import AUDIENCE, ISSUER, machine_claims
lazy from ledger_kernel.access import Principal
lazy from ledger_platform.observability.tracing import Tracing
lazy from ledger_testing.issuer import LocalIssuer

OLDER: Final = ApiVersion(date(2026, 1, 1))
NEWER: Final = ApiVersion(date(2026, 6, 1))
PROBLEM: Final = TypeAdapter(dict[str, object])
FULLWIDTH: Final = "".join(chr(0xFF10 + int(digit)) if digit.isdigit() else digit for digit in "2026-10-13")
"""A date written in digits that are not ASCII."""


@final
class Knowing(Standings):
    """What is known of the caller; counts the questions."""

    def __init__(self) -> None:
        self.standing = Standing()
        self.asked = 0

    @override
    async def of(self, principal: Principal) -> Standing:
        del principal
        self.asked += 1
        return self.standing


@pytest.fixture
def standings() -> Knowing:
    return Knowing()


@asynccontextmanager
async def nothing_to_start(app: FastAPI) -> AsyncGenerator[None]:
    del app
    yield


@pytest.fixture
def app(
    local_issuer: LocalIssuer, standings: Knowing, registry: CollectorRegistry, tracing: Tracing
) -> FastAPI:
    verifier = TokenVerifier(TokenPolicy(issuer=ISSUER, audience=AUDIENCE, keys_url=local_issuer.keys_url))
    caller = Authentication(authenticated(verifier), None).with_standing(standings)
    routes = APIRouter()

    @routes.get("/thing")
    async def thing(principal: Annotated[Principal, Depends(caller)]) -> str:
        return principal.subject

    @routes.get("/moded")
    async def moded(
        principal: Annotated[Principal, Depends(caller)],
        mode: Annotated[Mode, Depends(ledger_mode_of(caller))],
    ) -> str:
        del principal
        return mode.value

    @routes.get("/mode-first")
    async def mode_first(
        mode: Annotated[Mode, Depends(ledger_mode_of(caller))],
        principal: Annotated[Principal, Depends(caller)],
    ) -> str:
        del principal
        return mode.value

    @routes.get("/open")
    async def anyone() -> str:
        return "open"

    return create_app(
        info=ApiInfo(title="standing", version="1.0"),
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


# ── Reading a version ───────────────────────────────────────────────────────────


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
        FULLWIDTH,  # digits that are not ASCII
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
    both = Standing(client=OLDER, organization=NEWER)
    assert resolve(NEWER, both) == NEWER
    assert resolve(None, both) == OLDER
    assert resolve(None, Standing(organization=NEWER)) == NEWER
    assert resolve(None, Standing()) == CURRENT_VERSION


# ── The version on the wire ─────────────────────────────────────────────────────


async def test_without_a_header_or_a_pin_a_request_gets_the_current_version(
    client: httpx.AsyncClient, local_issuer: LocalIssuer, standings: Knowing
) -> None:
    response = await client.get("/thing", headers=bearer(local_issuer))
    assert (response.status_code, response.headers[VERSION_HEADER]) == (200, CURRENT_VERSION.isoformat())
    assert standings.asked == 1


async def test_a_request_that_names_a_version_gets_it_whatever_its_pins(
    client: httpx.AsyncClient, local_issuer: LocalIssuer, standings: Knowing
) -> None:
    standings.standing = Standing(client=OLDER, organization=NEWER)
    sent = CURRENT_VERSION.isoformat()
    for name in (VERSION_HEADER, VERSION_HEADER.lower()):  # header names are case-insensitive
        response = await client.get("/thing", headers=bearer(local_issuer) | {name: sent})
        assert (response.status_code, response.headers[VERSION_HEADER]) == (200, sent)


async def test_without_a_header_the_pins_decide(
    client: httpx.AsyncClient, local_issuer: LocalIssuer, standings: Knowing
) -> None:
    standings.standing = Standing(organization=NEWER)
    organization = await client.get("/thing", headers=bearer(local_issuer))
    standings.standing = Standing(client=OLDER, organization=NEWER)
    both = await client.get("/thing", headers=bearer(local_issuer))
    assert (organization.headers[VERSION_HEADER], both.headers[VERSION_HEADER]) == (
        NEWER.isoformat(),
        OLDER.isoformat(),
    )


@pytest.mark.parametrize("text", ["2020-01-01", "tomorrow", "2026-13-45", "x" * 500, FULLWIDTH])
async def test_a_version_that_does_not_exist_is_refused_once_the_caller_is_proven(
    client: httpx.AsyncClient, local_issuer: LocalIssuer, standings: Knowing, text: str
) -> None:
    refused = await client.get("/thing", headers=raw(local_issuer, text))
    assert (refused.status_code, PROBLEM.validate_json(refused.content)["code"]) == (400, "version_unknown")
    assert len(text) < 20 or text not in refused.text
    anonymous = await client.get("/thing", headers=raw(local_issuer, text)[1:])
    assert (anonymous.status_code, PROBLEM.validate_json(anonymous.content)["code"]) == (
        401,
        "missing_token",
    )
    assert standings.asked == 1  # the proven caller's; nobody was asked about the anonymous one


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


# ── The mode and the revocation of an API client ────────────────────────────────


async def test_a_human_names_its_mode_and_an_api_client_s_credential_carries_it(
    client: httpx.AsyncClient, local_issuer: LocalIssuer, standings: Knowing
) -> None:
    human = await client.get("/moded", headers=bearer(local_issuer))
    assert (human.status_code, PROBLEM.validate_json(human.content)["code"]) == (400, "mode_required")
    standings.standing = Standing(mode=Mode.LIVE)
    bare = await client.get("/moded", headers=bearer(local_issuer))
    repeated = await client.get("/moded", headers=bearer(local_issuer, **{"Ledger-Mode": "live"}))
    assert (bare.json(), repeated.json()) == ("live", "live")
    other = await client.get("/moded", headers=bearer(local_issuer, **{"Ledger-Mode": "test"}))
    assert (other.status_code, PROBLEM.validate_json(other.content)["code"]) == (403, "mode_mismatch")


async def test_a_route_that_reads_its_mode_before_its_caller_still_holds_a_credential_to_its_mode(
    client: httpx.AsyncClient, local_issuer: LocalIssuer, standings: Knowing
) -> None:
    """The mode depends on the caller: whatever the order a route declares them in, the credential rules."""
    standings.standing = Standing(mode=Mode.TEST)
    refused = await client.get("/mode-first", headers=bearer(local_issuer, **{"Ledger-Mode": "live"}))
    assert (refused.status_code, PROBLEM.validate_json(refused.content)["code"]) == (403, "mode_mismatch")
    bare = await client.get("/mode-first", headers=bearer(local_issuer))
    assert (bare.status_code, bare.json()) == (200, "test")


async def test_a_revoked_api_client_is_refused_whatever_its_token_says(
    client: httpx.AsyncClient, local_issuer: LocalIssuer, standings: Knowing
) -> None:
    standings.standing = Standing(mode=Mode.TEST, revoked=True)
    refused = await client.get("/thing", headers=bearer(local_issuer))
    assert (refused.status_code, PROBLEM.validate_json(refused.content)["code"]) == (
        401,
        "api_client_revoked",
    )
    assert refused.headers["www-authenticate"].startswith("Bearer")
    assert ApiClientRevokedError.code == "api_client_revoked"


async def test_a_machine_user_that_is_no_api_client_is_refused_and_a_person_is_not(
    client: httpx.AsyncClient, local_issuer: LocalIssuer, standings: Knowing
) -> None:
    machine = {"Authorization": f"Bearer {local_issuer.token(claims=machine_claims('4001'))}"}
    refused = await client.get("/thing", headers=machine)
    assert (refused.status_code, PROBLEM.validate_json(refused.content)["code"]) == (
        403,
        "api_client_unregistered",
    )
    assert (await client.get("/thing", headers=bearer(local_issuer))).status_code == 200  # a person
    standings.standing = Standing(mode=Mode.TEST)  # once registered, the machine acts in its mode
    assert (await client.get("/thing", headers=machine)).status_code == 200
    assert ApiClientUnregisteredError.code == "api_client_unregistered"


# ── Remembering a standing ──────────────────────────────────────────────────────


def principal_of(subject: str, organization: str = "1001") -> Principal:
    return Principal(
        subject=SubjectId(subject),
        organization=organization_of(organization),
        provider_organization=organization,
        kind=PrincipalKind.HUMAN,
    )


@final
class Time:
    """A clock that moves when told."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


async def test_a_standing_is_asked_once_for_a_while_and_for_each_caller() -> None:
    knowing, time = Knowing(), Time()
    cached = CachedStandings(knowing, ttl=30.0, size=10, clock=time)
    ana, bob, other = principal_of("ana"), principal_of("bob"), principal_of("ana", "1002")
    knowing.standing = Standing(client=OLDER, organization=NEWER)
    assert await cached.of(ana) == knowing.standing
    assert await cached.of(ana) == knowing.standing
    assert knowing.asked == 1
    await cached.of(bob)
    await cached.of(other)  # the same subject of another organization is another caller
    assert knowing.asked == 3
    time.now += 29.9
    await cached.of(ana)
    assert knowing.asked == 3
    time.now += 0.2  # past the ttl: asked again, and a change shows
    knowing.standing = Standing(organization=NEWER, revoked=True)
    assert await cached.of(ana) == knowing.standing
    assert knowing.asked == 4


async def test_the_standings_remembered_are_bounded_forgetting_the_oldest_first() -> None:
    knowing, time = Knowing(), Time()
    knowing.standing = Standing(organization=NEWER)
    cached = CachedStandings(knowing, ttl=30.0, size=2, clock=time)
    one, two, three = principal_of("1"), principal_of("2"), principal_of("3")
    for caller in (one, two, three):
        await cached.of(caller)
    assert knowing.asked == 3
    await cached.of(three)
    await cached.of(two)
    assert knowing.asked == 3  # the two newest are kept
    await cached.of(one)
    assert knowing.asked == 4  # the oldest was forgotten


async def test_a_caller_that_is_known_to_nobody_is_asked_again_until_it_is() -> None:
    """Its organization is not known yet; the next question must see the organization once it is."""
    knowing = Knowing()
    cached = CachedStandings(knowing, ttl=30.0, size=10, clock=Time())
    ana = principal_of("ana")
    assert await cached.of(ana) == Standing()
    knowing.standing = Standing(organization=NEWER)
    assert await cached.of(ana) == Standing(organization=NEWER)
    assert await cached.of(ana) == Standing(organization=NEWER)
    assert knowing.asked == 2


async def test_a_caller_that_failed_to_be_asked_is_asked_again() -> None:
    @final
    class Failing(Standings):
        def __init__(self) -> None:
            self.asked = 0

        @override
        async def of(self, principal: Principal) -> Standing:
            del principal
            self.asked += 1
            if self.asked == 1:
                raise ConnectionError
            return Standing()

    failing = Failing()
    cached = CachedStandings(failing, ttl=30.0, size=2, clock=Time())
    with pytest.raises(ConnectionError):
        await cached.of(principal_of("ana"))
    assert await cached.of(principal_of("ana")) == Standing()


def test_a_cache_that_remembers_nothing_is_a_mistake() -> None:
    for ttl, size in ((0.0, 1), (-1.0, 1), (30.0, 0)):
        with pytest.raises(InvalidOptionError):
            CachedStandings(Knowing(), ttl=ttl, size=size)
