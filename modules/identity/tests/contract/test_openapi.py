"""identity's OpenAPI document (3.1), as published in ``modules/identity/openapi.json`` (§10): a snapshot.

The document is made from the code: the ``api`` app is built with placeholder settings (nothing connects) and
asked for its schema, restricted to identity's paths. ``uv run ledger-devtools sync`` writes the file; this
test compares it (as ``ledger-devtools check`` does), and the others pin what the document says.
"""

import json
from pathlib import Path
from typing import Final, TypedDict

from fastapi.routing import APIRoute, iter_route_contexts
from pydantic import SecretStr, TypeAdapter
lazy from fastapi import FastAPI
lazy from fastapi.dependencies.models import Dependant

from ledger_devtools.openapi import api_document, openapi_text
from ledger_devtools.workspace import Workspace
from ledger_identity._manifest import MANIFEST
from ledger_platform.config.settings import Settings
from ledger_platform.http.deps import if_match, unguarded_routes
from ledger_platform.runtime.app import App

ROOT: Final = Path(__file__).parents[4]
PUBLISHED: Final = Path(__file__).parents[2] / "openapi.json"
PLACEHOLDERS: Final = Settings(
    environment="test",
    database_url=SecretStr("postgresql+psycopg://openapi@localhost/openapi"),
    token_issuer="https://issuer.invalid",
    token_audience="ledger",
    token_jwks_url="https://issuer.invalid/oauth/v2/keys",
)
"""The document does not depend on whom the app trusts or where its database is: nothing connects."""
PREFIXES: Final = MANIFEST.http.prefixes if MANIFEST.http is not None else ()
Parameter = TypedDict("Parameter", {"name": str, "in": str, "required": bool})


class Answer(TypedDict, total=False):
    content: dict[str, dict[str, object]]
    headers: dict[str, dict[str, object]]


class Operation(TypedDict, total=False):
    parameters: list[Parameter]
    security: list[dict[str, list[str]]]
    responses: dict[str, Answer]


class Document(TypedDict):
    openapi: str
    paths: dict[str, dict[str, Operation]]


DOCUMENT: Final = TypeAdapter(Document)
ANY_DOCUMENT: Final = TypeAdapter(dict[str, object])
PATHS: Final = TypeAdapter(dict[str, object])


def app() -> FastAPI:
    """The ``api`` app on placeholder settings: what the document is made from."""
    return App("api", settings=PLACEHOLDERS).asgi()


def document() -> str:
    """identity's paths of the ``api`` app's document, as JSON with sorted keys."""
    whole = ANY_DOCUMENT.validate_python(app().openapi())
    paths = PATHS.validate_python(whole["paths"])
    whole["paths"] = {path: item for path, item in paths.items() if path.startswith(PREFIXES)}
    return json.dumps(whole, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def test_the_published_document_is_the_one_sync_writes() -> None:
    identity = Workspace.load(ROOT).by_name("identity")
    made = openapi_text(identity, api_document())
    assert PUBLISHED.read_text(encoding="utf-8") == made, "regenerate it: uv run ledger-devtools sync"


def test_the_document_is_openapi_3_1_and_every_post_takes_an_idempotency_key_in_a_mode() -> None:
    parsed = DOCUMENT.validate_json(document())
    assert parsed["openapi"].startswith("3.1.")
    posts = [operations["post"] for operations in parsed["paths"].values() if "post" in operations]
    assert len(posts) == 4
    for post in posts:
        required = post.get("parameters", [])
        assert {"name": "Idempotency-Key", "in": "header", "required": True} in required
        assert {"name": "Ledger-Mode", "in": "header", "required": True} in required  # missing: 400
        assert "400" in post.get("responses", {})


def test_the_document_holds_identity_s_paths_only() -> None:
    paths = DOCUMENT.validate_json(document())["paths"]
    assert paths
    assert all(
        path.startswith(("/v1/organization", "/v1/companies", "/v1/countries", "/v1/api_clients"))
        for path in paths
    )


def operations() -> list[Operation]:
    """Every operation of the published document."""
    parsed = DOCUMENT.validate_json(document())
    return [operation for operations in parsed["paths"].values() for operation in operations.values()]


def test_every_change_of_a_company_requires_if_match() -> None:
    parsed = DOCUMENT.validate_json(document())
    changes = [
        parsed["paths"]["/v1/companies/{company_id}"]["patch"],
        parsed["paths"]["/v1/companies/{company_id}/deactivate"]["post"],
    ]
    for change in changes:
        assert {"name": "If-Match", "in": "header", "required": True} in change.get("parameters", [])
        assert {"412", "428"} <= set(change.get("responses", {}))


def test_if_match_is_documented_once_and_required_wherever_it_is_read() -> None:
    for operation in operations():
        tags = [parameter for parameter in operation.get("parameters", []) if parameter["name"] == "If-Match"]
        answers = set(operation.get("responses", {}))
        assert (len(tags), [tag["required"] for tag in tags]) == (
            (1, [True]) if "428" in answers else (0, [])
        )


def reads_if_match(dependant: Dependant) -> bool:
    """Whether ``if_match`` is anywhere in the dependency tree of ``dependant``."""
    return any(
        dependency.call is if_match or reads_if_match(dependency) for dependency in dependant.dependencies
    )


def test_every_operation_that_reads_if_match_documents_it_as_required() -> None:
    paths = DOCUMENT.validate_json(document())["paths"]
    routes = [(context, context.original_route) for context in iter_route_contexts(app().routes)]
    conditional = [
        (context.path_format or route.path_format, method.lower())
        for context, route in routes
        if isinstance(route, APIRoute) and reads_if_match(route.dependant)
        for method in route.methods or ()
    ]
    assert sorted(conditional) == [
        ("/v1/companies/{company_id}", "patch"),
        ("/v1/companies/{company_id}/deactivate", "post"),
    ]
    for path, method in conditional:
        parameters = paths[path][method].get("parameters", [])
        assert {"name": "If-Match", "in": "header", "required": True} in parameters


def test_a_creation_says_where_its_resource_lives_and_no_other_answer_does() -> None:
    parsed = DOCUMENT.validate_json(document())
    located = [
        (method, status)
        for operations in parsed["paths"].values()
        for method, operation in operations.items()
        for status, answer in operation.get("responses", {}).items()
        if "Location" in answer.get("headers", {})
    ]
    creations = [
        "post"
        for operations in parsed["paths"].values()
        for method, operation in operations.items()
        if method == "post" and "201" in operation.get("responses", {})
    ]
    assert creations
    assert located == [("post", "201")] * len(creations)  # a PUT's resource is its URI (RFC 9110 15.3.2)


def test_every_answer_of_a_company_tells_its_version() -> None:
    paths = DOCUMENT.validate_json(document())["paths"]
    answers = [
        ("/v1/companies", "post", "201"),
        ("/v1/companies/{company_id}", "get", "200"),
        ("/v1/companies/{company_id}", "patch", "200"),
        ("/v1/companies/{company_id}/deactivate", "post", "200"),
    ]
    for path, method, status in answers:
        assert "ETag" in paths[path][method].get("responses", {})[status].get("headers", {})


def test_every_operation_may_find_the_service_unavailable() -> None:
    for operation in operations():
        assert "503" in operation.get("responses", {})


def test_every_operation_is_for_bearers_of_a_token() -> None:
    parsed = DOCUMENT.validate_json(document())
    for operations in parsed["paths"].values():
        for operation in operations.values():
            assert operation.get("security") == [{"HTTPBearer": []}]


def test_every_route_needs_a_bearer_token_and_declares_what_it_requires() -> None:
    """The route inventory: a permission in the company, a role in the organization, or the organization."""
    routes = app().routes
    v1 = [context.path for context in iter_route_contexts(routes) if (context.path or "").startswith("/v1")]
    assert len(v1) == len(operations())  # every operation the document lists is a route looked at
    assert unguarded_routes(routes) == ()


def test_every_error_an_operation_documents_is_a_problem_document() -> None:
    parsed = DOCUMENT.validate_json(document())
    errors = [
        answer
        for operations in parsed["paths"].values()
        for operation in operations.values()
        for status, answer in operation.get("responses", {}).items()
        if status.startswith(("4", "5"))
    ]
    assert errors
    for answer in errors:
        assert list(answer.get("content", {})) == ["application/problem+json"]
