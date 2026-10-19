"""``Ledger-Mode`` (§9.7): the exact words ``test`` and ``live``, sent once; anything else is a 400."""

from typing import Annotated, Final
lazy from collections.abc import AsyncGenerator

import httpx
import pytest
from fastapi import Depends, FastAPI
from fastapi.security import HTTPBearer
lazy from fastapi import Request
lazy from fastapi.security import HTTPAuthorizationCredentials

from ledger_kernel.access import Principal, PrincipalKind, SubjectId
from ledger_kernel.errors import ErrorKind
from ledger_kernel.tenancy import Mode
from ledger_platform.auth.providers.zitadel import organization_of
from ledger_platform.http.mode import (
    MODE_HEADER,
    MODE_KEY,
    ModeInvalidError,
    ModeMismatchError,
    ModeRequiredError,
    ledger_mode_of,
    require_mode_header,
)
from ledger_platform.http.problems import PROBLEM_JSON, install_problem_handlers
lazy from ledger_platform.http.deps import Caller

SECRET_LOOKING: str = "live; tax_id=101850043"
"""A value no mode is, carrying something a problem must never repeat."""
_BEARER: Final = HTTPBearer(auto_error=False)
CALLER: Final = Principal(
    subject=SubjectId("1"),
    organization=organization_of("1001"),
    provider_organization="1001",
    kind=PrincipalKind.HUMAN,
)


def proven(carried: Mode | None) -> Caller:
    """A caller proven without a token; its credential carries ``carried`` (an API client's), if any."""

    async def caller(
        request: Request, credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_BEARER)]
    ) -> AsyncGenerator[Principal]:
        del credentials
        if carried is not None:
            request.scope[MODE_KEY] = carried
        yield CALLER

    return caller


def client(carried: Mode | None = None) -> httpx.AsyncClient:
    """A client of a moded route whose caller's credential carries ``carried``, if any."""
    built = FastAPI()
    install_problem_handlers(built)

    @built.get("/moded")
    async def moded(mode: Annotated[Mode, Depends(ledger_mode_of(proven(carried)))]) -> dict[str, str]:
        return {"mode": mode.value}

    return httpx.AsyncClient(transport=httpx.ASGITransport(built), base_url="http://t")


@pytest.mark.parametrize("sent", ["test", "live"])
async def test_the_mode_header_names_the_mode(sent: str) -> None:
    async with client() as moded:
        assert (await moded.get("/moded", headers={"Ledger-Mode": sent})).json() == {"mode": sent}


async def test_the_header_s_name_is_matched_in_any_case_as_http_says() -> None:
    async with client() as moded:
        assert (await moded.get("/moded", headers={"ledger-mode": "live"})).json() == {"mode": "live"}


@pytest.mark.parametrize(
    ("headers", "code"),
    [
        ({}, "mode_required"),
        ({"Ledger-Mode": "prod"}, "mode_invalid"),
        ({"Ledger-Mode": "Test"}, "mode_invalid"),
        ({"Ledger-Mode": "LIVE"}, "mode_invalid"),
        ({"Ledger-Mode": " test"}, "mode_invalid"),
        ({"Ledger-Mode": "test "}, "mode_invalid"),
        ({"Ledger-Mode": ""}, "mode_invalid"),
        ({"Ledger-Mode": "test,live"}, "mode_invalid"),
        ({"Ledger-Mode": SECRET_LOOKING}, "mode_invalid"),
        ({"Ledger-Mode": "x" * 10_000}, "mode_invalid"),
    ],
    ids=[
        "missing",
        "other",
        "title-case",
        "upper-case",
        "leading-space",
        "trailing-space",
        "empty",
        "list",
        "with-a-secret",
        "huge",
    ],
)
async def test_a_moded_route_without_a_valid_mode_is_a_bad_request(
    headers: dict[str, str], code: str
) -> None:
    async with client() as moded:
        response = await moded.get("/moded", headers=headers)
    assert (response.status_code, response.headers["content-type"], response.json()["code"]) == (
        400,
        PROBLEM_JSON,
        code,
    )
    assert "prod" not in response.text
    assert "101850043" not in response.text
    assert "xxxx" not in response.text


@pytest.mark.parametrize("second", ["test", "live"])
async def test_a_mode_sent_twice_is_refused_even_when_both_agree(second: str) -> None:
    async with client() as moded:
        response = await moded.get("/moded", headers=[(MODE_HEADER, "test"), (MODE_HEADER, second)])
    assert (response.status_code, response.json()["code"]) == (400, "mode_invalid")


def test_the_mode_errors_are_invalid_input_with_stable_codes() -> None:
    assert (ModeRequiredError().kind, ModeRequiredError.code) == (ErrorKind.INVALID, "mode_required")
    assert (ModeInvalidError().kind, ModeInvalidError.code) == (ErrorKind.INVALID, "mode_invalid")
    assert (ModeMismatchError().kind, ModeMismatchError.code) == (ErrorKind.FORBIDDEN, "mode_mismatch")


@pytest.mark.parametrize("mode", [Mode.TEST, Mode.LIVE])
async def test_a_credential_that_carries_its_mode_needs_no_header_and_may_repeat_it(mode: Mode) -> None:
    async with client(mode) as moded:
        bare = await moded.get("/moded")
        repeated = await moded.get("/moded", headers={MODE_HEADER: mode.value})
    assert (bare.json(), repeated.json()) == ({"mode": mode.value}, {"mode": mode.value})


async def test_a_credential_cannot_act_in_the_other_mode() -> None:
    async with client(Mode.TEST) as moded:
        response = await moded.get("/moded", headers={MODE_HEADER: "live"})
    assert (response.status_code, response.headers["content-type"], response.json()["code"]) == (
        403,
        PROBLEM_JSON,
        "mode_mismatch",
    )


@pytest.mark.parametrize("sent", ["prod", "Test", "", "x" * 1000])
async def test_a_header_that_is_no_mode_is_refused_whatever_the_credential_says(sent: str) -> None:
    async with client(Mode.TEST) as moded:
        response = await moded.get("/moded", headers={MODE_HEADER: sent})
    assert (response.status_code, response.json()["code"]) == (400, "mode_invalid")
    assert len(sent) < 100 or sent not in response.text


async def test_two_headers_are_refused_even_to_a_credential_that_carries_a_mode() -> None:
    async with client(Mode.TEST) as moded:
        response = await moded.get("/moded", headers=[(MODE_HEADER, "test"), (MODE_HEADER, "test")])
    assert (response.status_code, response.json()["code"]) == (400, "mode_invalid")


def test_a_document_says_the_mode_header_is_required_and_names_its_words() -> None:
    """Missing, it is a 400 (``mode_required``), not a 422: FastAPI sees an optional header, the document must
    not; only ``Ledger-Mode`` header parameters change."""
    other = {"name": "Idempotency-Key", "in": "header", "required": True}
    query = {"name": MODE_HEADER, "in": "query", "required": False}
    document: dict[str, object] = {
        "openapi": "3.1.0",
        "paths": {
            "/moded": {
                "post": {
                    "parameters": [{"name": MODE_HEADER, "in": "header", "required": False}, other, query]
                },
                "get": {"responses": {}},
                "summary": "not an operation",
            },
            "/ignored": "not an operation",
        },
    }
    require_mode_header(document)
    assert document["paths"] == {
        "/moded": {
            "post": {
                "parameters": [
                    {
                        "name": MODE_HEADER,
                        "in": "header",
                        "required": True,
                        "schema": {"type": "string", "enum": ["test", "live"]},
                        "description": "test or live: the mode the request acts in.",
                    },
                    other,
                    query,
                ]
            },
            "get": {"responses": {}},
            "summary": "not an operation",
        },
        "/ignored": "not an operation",
    }
    without_paths: dict[str, object] = {"openapi": "3.1.0"}
    require_mode_header(without_paths)
    assert without_paths == {"openapi": "3.1.0"}
