"""signer's OpenAPI document (3.1), as published in ``modules/signer/openapi.json`` (§10): a snapshot.

The document is made from the code: the ``api`` app is built with placeholder settings (nothing connects, no
signer is configured: its routes answer 503, and are documented the same) and asked for its schema, restricted
to signer's paths. ``uv run ledger-devtools sync`` writes the file; this test compares it (as
``ledger-devtools check`` does), and the others pin what the document says, and that it holds no secret.
"""

import json
from pathlib import Path
from typing import Final, TypedDict

from fastapi.routing import iter_route_contexts
from pydantic import SecretStr, TypeAdapter
lazy from fastapi import FastAPI

from ledger_devtools.openapi import api_document, openapi_text
from ledger_devtools.workspace import Workspace
from ledger_platform.config.settings import Settings
from ledger_platform.http.deps import unguarded_routes
from ledger_platform.runtime.app import App
from ledger_signer._manifest import MANIFEST
from ledger_signer.adapters.http.router import PKCS12, UNLOCK_HEADER

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


class Body(TypedDict):
    content: dict[str, object]


class Operation(TypedDict, total=False):
    parameters: list[Parameter]
    security: list[dict[str, list[str]]]
    responses: dict[str, Answer]
    requestBody: Body


class Document(TypedDict):
    openapi: str
    paths: dict[str, dict[str, Operation]]


DOCUMENT: Final = TypeAdapter(Document)
ANY_DOCUMENT: Final = TypeAdapter(dict[str, object])
PATHS: Final = TypeAdapter(dict[str, object])


def app() -> FastAPI:
    """The ``api`` app on placeholder settings: what the document is made from."""
    return App("api", settings=PLACEHOLDERS).asgi()


def document() -> Document:
    """signer's paths of the ``api`` app's document."""
    whole = ANY_DOCUMENT.validate_python(app().openapi())
    paths = PATHS.validate_python(whole["paths"])
    whole["paths"] = {path: item for path, item in paths.items() if path.startswith(PREFIXES)}
    return DOCUMENT.validate_json(json.dumps(whole))


def operations() -> list[Operation]:
    return [operation for operations in document()["paths"].values() for operation in operations.values()]


def test_the_published_document_is_the_one_sync_writes() -> None:
    signer = Workspace.load(ROOT).by_name("signer")
    made = openapi_text(signer, api_document())
    assert PUBLISHED.read_text(encoding="utf-8") == made, "regenerate it: uv run ledger-devtools sync"


def test_the_document_holds_signer_s_five_operations_each_in_a_mode_and_a_company() -> None:
    parsed = document()
    assert parsed["openapi"].startswith("3.1.")
    assert {path: sorted(operations) for path, operations in parsed["paths"].items()} == {
        "/v1/certificates": ["get", "post"],
        "/v1/certificates/remote": ["post"],
        "/v1/certificates/{certificate_id}": ["delete", "get"],
    }
    for operation in operations():
        required = operation.get("parameters", [])
        assert {"name": "Ledger-Mode", "in": "header", "required": True} in required
        assert {"name": "Ledger-Company", "in": "header", "required": True} in required
    for path in ("/v1/certificates", "/v1/certificates/remote"):
        posted = parsed["paths"][path]["post"]
        assert {"name": "Idempotency-Key", "in": "header", "required": True} in posted.get("parameters", [])
        assert {"201", "409", "422"} <= set(posted.get("responses", {}))
        assert {"Location", "Idempotent-Replayed"} <= set(
            posted.get("responses", {})["201"].get("headers", {})
        )


def test_an_import_documents_its_raw_bundle_its_password_header_and_its_limits() -> None:
    upload = document()["paths"]["/v1/certificates"]["post"]
    body = upload.get("requestBody")
    assert body is not None
    assert list(body["content"]) == [PKCS12]
    assert {"name": UNLOCK_HEADER, "in": "header", "required": True} in upload.get("parameters", [])
    assert {"408", "413", "415"} <= set(upload.get("responses", {}))
    remote = document()["paths"]["/v1/certificates/remote"]["post"]
    assert "413" not in remote.get("responses", {})


def test_no_secret_has_an_example_and_every_secret_is_write_only() -> None:
    text = openapi_text(Workspace.load(ROOT).by_name("signer"), api_document())  # its components too
    assert "example" not in text
    assert text.count('"format": "password"') == 2  # the bundle's password, the entity's credential
    assert text.count('"writeOnly": true') == 2


def test_every_operation_is_for_bearers_of_a_token_and_says_when_to_retry_a_signer_out_of_reach() -> None:
    for operation in operations():
        assert operation.get("security") == [{"HTTPBearer": []}]
        assert "Retry-After" in operation.get("responses", {})["503"].get("headers", {})
    routes = app().routes
    signer = [
        context.path for context in iter_route_contexts(routes) if (context.path or "").startswith(PREFIXES)
    ]
    assert len(signer) == len(operations())
    assert unguarded_routes(routes) == ()
