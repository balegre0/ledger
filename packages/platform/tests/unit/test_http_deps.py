from contextlib import asynccontextmanager
from typing import Annotated, Final, TypedDict, final, override
lazy from collections.abc import AsyncGenerator

import httpx
import pytest
import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.exceptions import HTTPException
from hypothesis import given
from hypothesis import strategies as st
from pydantic import BaseModel, TypeAdapter
from structlog.testing import capture_logs
lazy from fastapi import FastAPI, Response
lazy from prometheus_client import CollectorRegistry

from ledger_kernel.access import Permission, Principal, SubjectId
from ledger_kernel.errors import LedgerError
from ledger_kernel.ids import CompanyId, new_id
from ledger_kernel.pagination import encode_cursor
from ledger_platform.auth.access import PermissionSource
from ledger_platform.auth.tokens import TokenPolicy, TokenVerifier, organization_of
from ledger_platform.http.app import ApiInfo, Observability, create_app
from ledger_platform.http.deps import (
    COMPANY_HEADER,
    REPLAYED_HEADER,
    IfMatch,
    allowed,
    answer,
    attempt_of,
    authenticated,
    company_in_header,
    company_in_path,
    etag,
    holding,
    idempotency_key,
    if_match,
    if_match_required,
    in_organization,
    json_response,
    page_request,
    parse_if_match,
    precondition_failed,
    unguarded_routes,
)
from ledger_platform.idempotency import Outcome
from ledger_platform.observability.health import Health
from ledger_testing.issuer import AUDIENCE, ISSUER, PROVIDER_ORGANIZATION, SUBJECT, LocalIssuer
lazy from ledger_kernel.ids import OrganizationId
lazy from ledger_kernel.pagination import PageRequest
lazy from ledger_platform.auth.access import CompanyAccess
lazy from ledger_platform.http.deps import Caller, InCompany
lazy from ledger_platform.observability.tracing import Tracing

COMPANY: Final = CompanyId(new_id())
OTHER_COMPANY: Final = CompanyId(new_id())
OTHER_PROVIDER_ORGANIZATION: Final = "1002"
PROBLEM: Final = TypeAdapter(dict[str, object])
BODY: Final = TypeAdapter(dict[str, object])
MAX_VERSION: Final = 10**18 - 1
"""The largest version an entity tag of ours names: eighteen digits."""


class Components(TypedDict):
    securitySchemes: dict[str, dict[str, str]]


class Operation(TypedDict, total=False):
    parameters: list[dict[str, object]]


class Document(TypedDict):
    components: Components
    paths: dict[str, dict[str, Operation]]


DOCUMENT: Final = TypeAdapter(Document)


class Thing(BaseModel):
    name: str


class BrokenError(Exception):
    """A defect of a route."""

    def __init__(self) -> None:
        super().__init__("the route broke")


def fail(how: str) -> None:
    """Fail as a route can: a bug, a defect the platform names, or a server error it raises."""
    match how:
        case "bug":
            raise BrokenError
        case "internal":
            raise LedgerError
        case _:
            raise HTTPException(503)


@final
class Grants(PermissionSource):
    """Permissions by organization, subject and company, as identity keeps them; counts the questions."""

    def __init__(self) -> None:
        self.granted: dict[tuple[OrganizationId, SubjectId, CompanyId], frozenset[Permission]] = {}
        self.asked = 0

    def grant(
        self,
        company: CompanyId,
        *permissions: Permission,
        subject: str = SUBJECT,
        organization: str = PROVIDER_ORGANIZATION,
    ) -> None:
        self.granted[organization_of(organization), SubjectId(subject), company] = frozenset(permissions)

    @override
    async def permissions(self, principal: Principal, company: CompanyId) -> frozenset[Permission]:
        self.asked += 1
        return self.granted.get((principal.organization, principal.subject, company), frozenset())


def applied(version: int, condition: IfMatch) -> dict[str, int]:
    """An update of ``version`` as a route answers it: 412 unless ``condition`` admits the version."""
    if not condition.admits(version):
        raise precondition_failed()
    return {"version": version}


def add_defective_routes(routes: APIRouter, in_path: InCompany, in_header: InCompany) -> None:
    """Routes that fail on the server's side, after the caller and its company are known."""

    @routes.get("/companies/{company_id}/broken/{how}")
    async def broken(how: str, access: Annotated[CompanyAccess, Depends(in_path)]) -> None:
        del access
        fail(how)

    @routes.get("/companies/{company_id}/of-the-header")
    async def ambiguous(access: Annotated[CompanyAccess, Depends(in_header)]) -> None:
        del access


ADMIN: Final = "ledger.organization.admin"
UNDECLARED: Final = "declares no requirement (allowed, holding or in_organization)"


def add_guarded_routes(routes: APIRouter, caller: Caller, in_path: InCompany) -> None:
    """Routes that declare what they require: a permission in the company, a role, or the organization."""

    @routes.post("/companies/{company_id}/managed")
    async def managed(
        access: Annotated[CompanyAccess, Depends(allowed(in_path, Permission.COMPANY_MANAGE))],
    ) -> str:
        return str(access.tenant.company)

    @routes.post("/registered")
    async def registered(principal: Annotated[Principal, Depends(holding(caller, ADMIN))]) -> str:
        return principal.subject

    @routes.get("/mine")
    async def mine(principal: Annotated[Principal, Depends(in_organization(caller))]) -> str:
        return str(principal.organization)


def router(verifier: TokenVerifier, grants: Grants) -> APIRouter:
    routes = APIRouter()
    caller = authenticated(verifier)

    async def source() -> PermissionSource:
        return grants

    in_path = company_in_path(caller, source)
    in_header = company_in_header(caller, source)

    @routes.get("/whoami")
    async def whoami(principal: Annotated[Principal, Depends(caller)]) -> dict[str, object]:
        return {"subject": principal.subject, "context": structlog.contextvars.get_contextvars()}

    @routes.get("/companies/{company_id}/things")
    async def things(access: Annotated[CompanyAccess, Depends(in_path)]) -> dict[str, object]:
        access.require(Permission.COMPANY_READ)
        return {"company": str(access.tenant.company), "context": structlog.contextvars.get_contextvars()}

    @routes.get("/things")
    async def things_of_the_header(access: Annotated[CompanyAccess, Depends(in_header)]) -> dict[str, str]:
        return {"company": str(access.tenant.company), "organization": str(access.tenant.organization)}

    add_defective_routes(routes, in_path, in_header)
    add_guarded_routes(routes, caller, in_path)

    @routes.get("/pages")
    async def pages(page: Annotated[PageRequest, Depends(page_request)]) -> dict[str, object]:
        return {"size": page.size, "after": None if page.after is None else str(page.after)}

    @routes.patch("/versions/{version}")
    async def versions(version: int, condition: Annotated[IfMatch, Depends(if_match)]) -> dict[str, bool]:
        return {"admitted": condition.admits(version)}

    @routes.put("/guarded/{version}", openapi_extra=if_match_required())
    async def guarded(version: int, condition: Annotated[IfMatch, Depends(if_match)]) -> dict[str, int]:
        return applied(version, condition)

    @routes.post("/keys")
    async def keys(key: Annotated[str, Depends(idempotency_key)]) -> dict[str, str]:
        return {"key": key}

    @routes.post("/answers/{replayed}")
    async def answers(*, replayed: bool) -> Response:
        stored = json_response(201, Thing(name="thing"), {"Location": "/things/1"})
        return answer(Outcome(stored, replayed=replayed))

    return routes


@pytest.fixture
async def issuer() -> AsyncGenerator[LocalIssuer]:
    async with LocalIssuer.started() as started:
        yield started


@pytest.fixture
def grants() -> Grants:
    return Grants()


@asynccontextmanager
async def nothing_to_start(app: FastAPI) -> AsyncGenerator[None]:
    del app
    yield


@pytest.fixture
def app(issuer: LocalIssuer, grants: Grants, registry: CollectorRegistry, tracing: Tracing) -> FastAPI:
    verifier = TokenVerifier(TokenPolicy(issuer=ISSUER, audience=AUDIENCE, keys_url=issuer.keys_url))
    return create_app(
        info=ApiInfo(title="routes", version="1.0"),
        observability=Observability(health=Health(), registry=registry, tracer_provider=tracing.provider),
        lifespan=nothing_to_start,
        routers=(router(verifier, grants),),
    )


@pytest.fixture
async def client(app: FastAPI) -> AsyncGenerator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as opened:
        yield opened


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def problem_code(response: httpx.Response) -> object:
    return PROBLEM.validate_json(response.content)["code"]


# ── Who calls ───────────────────────────────────────────────────────────────────


async def test_the_caller_is_the_principal_its_token_proves(
    client: httpx.AsyncClient, issuer: LocalIssuer
) -> None:
    response = await client.get("/whoami", headers=bearer(issuer.token(subject="2001")))
    assert response.status_code == 200
    body = BODY.validate_json(response.content)
    assert body["subject"] == "2001"
    assert body["context"] == {
        "organization_id": str(organization_of(PROVIDER_ORGANIZATION)),
        "subject": "2001",
    }


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Basic YWxpY2U6c2VjcmV0"}, {"Authorization": "Bearer"}],
    ids=["none", "basic", "empty-bearer"],
)
async def test_a_request_without_a_bearer_token_is_unauthenticated(
    client: httpx.AsyncClient, headers: dict[str, str]
) -> None:
    response = await client.get("/whoami", headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == 'Bearer realm="ledger"'
    assert problem_code(response) == "missing_token"


async def test_a_bad_token_is_unauthenticated_and_never_echoed(
    client: httpx.AsyncClient, issuer: LocalIssuer
) -> None:
    token = issuer.token(claims={"aud": "someone-else"})
    response = await client.get("/whoami", headers=bearer(token))
    assert response.status_code == 401
    assert problem_code(response) == "invalid_token"
    assert response.headers["www-authenticate"] == 'Bearer realm="ledger", error="invalid_token"'
    assert token not in response.text
    assert "audience" in response.text


async def test_a_megabyte_of_authorization_is_refused_unread(
    client: httpx.AsyncClient, issuer: LocalIssuer
) -> None:
    response = await client.get("/whoami", headers=bearer("x" * 1_000_000))
    assert (response.status_code, problem_code(response)) == (401, "invalid_token")
    assert "malformed" in response.text
    assert len(response.content) < 1_000
    assert issuer.fetches == 0


async def test_the_bound_fields_do_not_outlive_the_request(
    client: httpx.AsyncClient, issuer: LocalIssuer, grants: Grants
) -> None:
    grants.grant(COMPANY, Permission.INVOICE_READ)
    await client.get("/whoami", headers=bearer(issuer.token()))
    refused = await client.get(f"/companies/{COMPANY}/things", headers=bearer(issuer.token()))
    assert refused.status_code == 403  # raised by the route, after every field was bound
    assert structlog.contextvars.get_contextvars() == {}


async def test_the_api_document_declares_bearer_tokens(client: httpx.AsyncClient) -> None:
    document = DOCUMENT.validate_json((await client.get("/openapi.json")).content)
    assert document["components"]["securitySchemes"]["HTTPBearer"] == {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "JWT",
        "description": "An OAuth 2.0 access token (JWT) of the organization's identity provider.",
    }


# ── For which company ──────────────────────────────────────────────────────────


async def test_a_company_the_caller_holds_a_role_in_is_its_access(
    client: httpx.AsyncClient, issuer: LocalIssuer, grants: Grants
) -> None:
    grants.grant(COMPANY, Permission.COMPANY_READ)
    response = await client.get(f"/companies/{COMPANY}/things", headers=bearer(issuer.token()))
    assert response.status_code == 200
    body = BODY.validate_json(response.content)
    assert body["company"] == str(COMPANY)
    assert body["context"] == {
        "organization_id": str(organization_of(PROVIDER_ORGANIZATION)),
        "subject": "2001",
        "company_id": str(COMPANY),
    }


async def test_a_company_without_a_role_is_not_found(
    client: httpx.AsyncClient, issuer: LocalIssuer, grants: Grants
) -> None:
    grants.grant(COMPANY, Permission.COMPANY_READ)
    response = await client.get(f"/companies/{OTHER_COMPANY}/things", headers=bearer(issuer.token()))
    assert response.status_code == 404
    assert problem_code(response) == "company_not_found"


async def test_a_company_of_another_organization_is_not_found_in_the_path_or_a_header(
    client: httpx.AsyncClient, issuer: LocalIssuer, grants: Grants
) -> None:
    grants.grant(COMPANY, Permission.COMPANY_READ, organization=OTHER_PROVIDER_ORGANIZATION)
    token = issuer.token()  # the same subject id, in the caller's own organization
    in_path = await client.get(f"/companies/{COMPANY}/things", headers=bearer(token))
    in_header = await client.get("/things", headers=bearer(token) | {COMPANY_HEADER: str(COMPANY)})
    for response in (in_path, in_header):
        assert (response.status_code, problem_code(response)) == (404, "company_not_found")
    elsewhere = issuer.token(organization=OTHER_PROVIDER_ORGANIZATION)
    granted = await client.get("/things", headers=bearer(elsewhere) | {COMPANY_HEADER: str(COMPANY)})
    assert BODY.validate_json(granted.content) == {
        "company": str(COMPANY),
        "organization": str(organization_of(OTHER_PROVIDER_ORGANIZATION)),
    }


async def test_a_role_without_the_permission_is_forbidden(
    client: httpx.AsyncClient, issuer: LocalIssuer, grants: Grants
) -> None:
    grants.grant(COMPANY, Permission.INVOICE_READ)
    response = await client.get(f"/companies/{COMPANY}/things", headers=bearer(issuer.token()))
    assert response.status_code == 403
    assert problem_code(response) == "permission_denied"


async def test_a_route_allowed_a_permission_refuses_a_role_without_it_and_a_company_without_a_role(
    client: httpx.AsyncClient, issuer: LocalIssuer, grants: Grants
) -> None:
    grants.grant(COMPANY, Permission.COMPANY_READ, Permission.COMPANY_MANAGE)
    grants.grant(OTHER_COMPANY, Permission.COMPANY_READ)
    token = bearer(issuer.token())
    managed = await client.post(f"/companies/{COMPANY}/managed", headers=token)
    assert (managed.status_code, managed.json()) == (200, str(COMPANY))
    read_only = await client.post(f"/companies/{OTHER_COMPANY}/managed", headers=token)
    assert (read_only.status_code, problem_code(read_only)) == (403, "permission_denied")
    unknown = await client.post(f"/companies/{CompanyId(new_id())}/managed", headers=token)
    assert (unknown.status_code, problem_code(unknown)) == (404, "company_not_found")


async def test_a_route_holding_a_role_refuses_a_caller_the_provider_did_not_grant_it(
    client: httpx.AsyncClient, issuer: LocalIssuer
) -> None:
    granted = await client.post("/registered", headers=bearer(issuer.token(roles=[ADMIN])))
    assert (granted.status_code, granted.json()) == (200, SUBJECT)
    refused = await client.post("/registered", headers=bearer(issuer.token(roles=["ledger.viewer"])))
    assert (refused.status_code, problem_code(refused)) == (403, "forbidden")
    assert (
        PROBLEM.validate_json(refused.content)["detail"] == f"this needs the role {ADMIN} in the organization"
    )


async def test_a_route_of_the_whole_organization_takes_any_of_its_principals(
    client: httpx.AsyncClient, issuer: LocalIssuer
) -> None:
    response = await client.get("/mine", headers=bearer(issuer.token()))
    assert (response.status_code, response.json()) == (200, str(organization_of(PROVIDER_ORGANIZATION)))
    anonymous = await client.get("/mine")
    assert (anonymous.status_code, problem_code(anonymous)) == (401, "missing_token")


def test_the_guarded_routes_are_the_ones_that_need_a_token_and_declare_what_they_require(
    app: FastAPI,
) -> None:
    for guarded in ("/companies/{company_id}/managed", "/registered", "/mine"):
        assert unguarded_routes(app.routes, prefix=guarded) == ()
    assert unguarded_routes(app.routes, prefix="/whoami") == (f"GET /whoami: {UNDECLARED}",)
    assert unguarded_routes(app.routes, prefix="/pages") == (
        f"GET /pages: needs no bearer token; {UNDECLARED}",
    )
    assert (
        unguarded_routes(app.routes, prefix="/docs") == ()
    )  # the framework's own pages are no routes of ours


async def test_a_company_id_that_is_not_an_id_is_refused_unechoed(
    client: httpx.AsyncClient, issuer: LocalIssuer, grants: Grants
) -> None:
    in_path = await client.get(f"/companies/{'x' * 500}/things", headers=bearer(issuer.token()))
    in_header = await client.get("/things", headers=bearer(issuer.token()) | {COMPANY_HEADER: "x" * 500})
    for response in (in_path, in_header):
        assert (response.status_code, problem_code(response)) == (422, "invalid_id")
        assert "x" * 100 not in response.text
    assert grants.asked == 0


async def test_the_company_can_come_in_a_header(
    client: httpx.AsyncClient, issuer: LocalIssuer, grants: Grants
) -> None:
    grants.grant(COMPANY, Permission.COMPANY_READ)
    headers = bearer(issuer.token()) | {COMPANY_HEADER: str(COMPANY)}
    response = await client.get("/things", headers=headers)
    assert (response.status_code, BODY.validate_json(response.content)["company"]) == (200, str(COMPANY))
    missing = await client.get("/things", headers=bearer(issuer.token()))
    assert (missing.status_code, problem_code(missing)) == (422, "invalid_request")
    other = await client.get("/things", headers=bearer(issuer.token()) | {COMPANY_HEADER: str(OTHER_COMPANY)})
    assert (other.status_code, problem_code(other)) == (404, "company_not_found")


async def test_the_company_header_is_sent_once(
    client: httpx.AsyncClient, issuer: LocalIssuer, grants: Grants
) -> None:
    grants.grant(COMPANY, Permission.COMPANY_READ)
    grants.grant(OTHER_COMPANY, Permission.COMPANY_READ)
    headers = [
        ("Authorization", f"Bearer {issuer.token()}"),
        (COMPANY_HEADER, str(COMPANY)),
        (COMPANY_HEADER, str(OTHER_COMPANY)),
    ]
    response = await client.get("/things", headers=headers)
    assert (response.status_code, problem_code(response)) == (422, "invalid_header")
    assert COMPANY_HEADER in response.text
    assert grants.asked == 0


async def test_a_route_that_has_its_company_in_the_path_cannot_take_it_from_a_header(
    client: httpx.AsyncClient, issuer: LocalIssuer, grants: Grants
) -> None:
    grants.grant(COMPANY, Permission.COMPANY_READ)
    headers = bearer(issuer.token()) | {COMPANY_HEADER: str(COMPANY)}
    with capture_logs() as logs:
        response = await client.get(f"/companies/{COMPANY}/of-the-header", headers=headers)
    assert (response.status_code, problem_code(response)) == (500, "internal")
    (logged,) = [log for log in logs if log["event"] == "http.unhandled_error"]
    assert logged["code"] == "ambiguous_company"
    assert grants.asked == 0


@pytest.mark.parametrize(
    ("how", "event"),
    [
        ("bug", "http.unhandled_error"),
        ("internal", "http.unhandled_error"),
        ("unavailable", "http.server_error"),
    ],
)
async def test_the_log_of_a_server_failure_names_the_caller_and_its_company(
    client: httpx.AsyncClient, issuer: LocalIssuer, grants: Grants, how: str, event: str
) -> None:
    grants.grant(COMPANY, Permission.COMPANY_READ)
    with capture_logs() as logs:
        response = await client.get(f"/companies/{COMPANY}/broken/{how}", headers=bearer(issuer.token()))
    assert response.status_code >= 500
    (logged,) = [log for log in logs if log["event"] == event]
    assert (logged["organization_id"], logged["subject"], logged["company_id"]) == (
        str(organization_of(PROVIDER_ORGANIZATION)),
        SUBJECT,
        str(COMPANY),
    )


async def test_the_company_is_never_looked_up_for_an_unauthenticated_caller(
    client: httpx.AsyncClient, grants: Grants
) -> None:
    response = await client.get(f"/companies/{COMPANY}/things")
    assert (response.status_code, problem_code(response), grants.asked) == (401, "missing_token", 0)


# ── Pages ───────────────────────────────────────────────────────────────────────


async def test_a_page_has_a_default_size_and_starts_at_the_beginning(client: httpx.AsyncClient) -> None:
    assert BODY.validate_json((await client.get("/pages")).content) == {"size": 50, "after": None}


async def test_a_cursor_says_where_the_page_starts(client: httpx.AsyncClient) -> None:
    after = new_id()
    response = await client.get("/pages", params={"limit": 10, "cursor": encode_cursor(after)})
    assert BODY.validate_json(response.content) == {"size": 10, "after": str(after)}


@pytest.mark.parametrize(
    ("params", "code"),
    [
        ({"limit": 0}, "invalid_request"),
        ({"limit": 101}, "invalid_request"),
        ({"cursor": "nope"}, "invalid_cursor"),
        ({"cursor": "x" * 65}, "invalid_request"),
    ],
)
async def test_a_page_that_cannot_be_served_is_refused(
    client: httpx.AsyncClient, params: dict[str, str | int], code: str
) -> None:
    response = await client.get("/pages", params=params)
    assert (response.status_code, problem_code(response)) == (422, code)


# ── Preconditions ───────────────────────────────────────────────────────────────


async def test_an_update_without_if_match_is_refused(client: httpx.AsyncClient) -> None:
    response = await client.patch("/versions/3")
    assert (response.status_code, problem_code(response)) == (428, "precondition_required")


async def test_if_match_may_come_in_several_lines(client: httpx.AsyncClient) -> None:
    lines = [("If-Match", '"1"'), ("If-Match", '"2", "3"')]
    response = await client.patch("/versions/3", headers=lines)
    assert BODY.validate_json(response.content) == {"admitted": True}


async def test_an_if_match_too_long_in_all_is_refused(client: httpx.AsyncClient) -> None:
    line = ", ".join(f'"{version}"' for version in range(150))
    assert len(line) < 1024
    response = await client.patch("/versions/3", headers=[("If-Match", line), ("If-Match", line)])
    assert (response.status_code, problem_code(response)) == (422, "invalid_header")


async def test_if_match_is_documented_as_required_where_its_route_says_so(client: httpx.AsyncClient) -> None:
    paths = DOCUMENT.validate_json((await client.get("/openapi.json")).content)["paths"]

    def if_match_of(path: str, method: str) -> list[dict[str, object]]:
        parameters = paths[path][method].get("parameters", [])
        return [parameter for parameter in parameters if parameter["name"] == "If-Match"]

    (documented,) = if_match_of("/guarded/{version}", "put")
    assert (documented["in"], documented["required"], documented["schema"]) == (
        "header",
        True,
        {"type": "string", "maxLength": 1024},
    )
    assert if_match_of("/versions/{version}", "patch") == []  # never documented as optional
    assert if_match_required() is not if_match_required()


@pytest.mark.parametrize(
    ("header", "admitted"),
    [
        ('"3"', True),
        ('"2", "3"', True),
        ("*", True),
        ('"4"', False),
        ('W/"3"', False),
        ("3", False),
        ("garbage", False),
    ],
)
async def test_if_match_admits_the_versions_it_names(
    client: httpx.AsyncClient, header: str, *, admitted: bool
) -> None:
    response = await client.patch("/versions/3", headers={"If-Match": header})
    assert BODY.validate_json(response.content) == {"admitted": admitted}


def test_if_match_reads_strong_tags_only() -> None:
    assert parse_if_match(' "1" , W/"2", "3", "",  "x", "1234567890123456789" ') == IfMatch(frozenset({1, 3}))
    assert parse_if_match("*") == IfMatch(None)
    assert parse_if_match('"\N{ARABIC-INDIC DIGIT THREE}"') == IfMatch(frozenset())
    assert parse_if_match('"3') == IfMatch(frozenset())
    assert parse_if_match('"03", "0", "00"') == IfMatch(frozenset({0}))  # one spelling per version
    assert IfMatch(None).admits(12)


async def test_an_update_whose_precondition_fails_is_a_412(client: httpx.AsyncClient) -> None:
    refused = await client.put("/guarded/3", headers={"If-Match": '"2"'})
    assert (refused.status_code, problem_code(refused)) == (412, "precondition_failed")
    weak = await client.put("/guarded/3", headers={"If-Match": 'W/"3"'})
    assert weak.status_code == 412
    applied = await client.put("/guarded/3", headers={"If-Match": '"3"'})
    assert BODY.validate_json(applied.content) == {"version": 3}
    anything = await client.put("/guarded/3", headers={"If-Match": "*"})
    assert BODY.validate_json(anything.content) == {"version": 3}


def test_an_etag_is_the_version_in_quotes() -> None:
    assert etag(7) == '"7"'
    assert parse_if_match(etag(7)).admits(7)


@given(st.integers(0, MAX_VERSION), st.integers(0, MAX_VERSION))
def test_the_etag_of_a_version_admits_that_version_alone(version: int, other: int) -> None:
    condition = parse_if_match(etag(version))
    assert condition.admits(version)
    assert condition.admits(other) is (other == version)


# ── Idempotent POSTs ────────────────────────────────────────────────────────────


async def test_a_post_needs_an_idempotency_key_of_visible_ascii(client: httpx.AsyncClient) -> None:
    missing = await client.post("/keys")
    assert (missing.status_code, problem_code(missing)) == (422, "invalid_request")
    spaced = await client.post("/keys", headers={"Idempotency-Key": "two words"})
    assert spaced.status_code == 422
    too_long = await client.post("/keys", headers={"Idempotency-Key": "k" * 256})
    assert too_long.status_code == 422
    given_key = await client.post("/keys", headers={"Idempotency-Key": "2b6a3c1e"})
    assert BODY.validate_json(given_key.content) == {"key": "2b6a3c1e"}


async def test_an_outcome_is_answered_as_it_was_stored(client: httpx.AsyncClient) -> None:
    fresh = await client.post("/answers/false")
    replayed = await client.post("/answers/true")
    for response in (fresh, replayed):
        assert response.status_code == 201
        assert response.content == b'{"name":"thing"}'
        assert response.headers["content-type"] == "application/json"
        assert response.headers["location"] == "/things/1"
    assert REPLAYED_HEADER.lower() not in fresh.headers
    assert replayed.headers[REPLAYED_HEADER] == "true"


def posted_to(path: str) -> Request:
    scope: dict[str, object] = {
        "type": "http",
        "method": "POST",
        "path": path,
        "headers": [],
        "query_string": b"",
    }
    return Request(scope)


def test_an_attempt_is_of_the_method_the_path_and_the_canonical_body() -> None:
    principal = Principal(
        subject=SubjectId("2001"), organization=organization_of("1001"), provider_organization="1001"
    )
    request = posted_to("/v1/things")
    with_body = attempt_of(request, principal, "k", Thing(name="a"))
    assert (
        with_body.fingerprint
        == attempt_of(request, principal, "k", Thing.model_validate_json('{ "name" : "a" }')).fingerprint
    )
    assert with_body.fingerprint != attempt_of(request, principal, "k").fingerprint
    elsewhere = posted_to("/v1/other-things")
    assert with_body.fingerprint != attempt_of(elsewhere, principal, "k", Thing(name="a")).fingerprint
    assert (with_body.organization, with_body.subject, with_body.key) == (
        organization_of("1001"),
        "2001",
        "k",
    )
