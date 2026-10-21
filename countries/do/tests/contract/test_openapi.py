"""do-ecf-v1's public routes in OpenAPI (3.1), published in ``countries/do/openapi.json`` (§10): a snapshot.

The document is made from the code: the ``api`` app is built with placeholder settings (nothing connects,
nothing is archived) and asked for its schema, restricted to the channel's paths. ``uv run ledger-devtools
sync`` writes the file; this test compares it (as ``ledger-devtools check`` does), and the others pin what the
document says.
"""

import json
import tempfile
from pathlib import Path
from typing import Final, TypedDict

from fastapi.routing import iter_route_contexts
from pydantic import SecretStr, TypeAdapter
lazy from fastapi import FastAPI

from ledger_country_do.channels.ecf_v1.assembly import CHANNEL
from ledger_devtools.openapi import api_document, openapi_text
from ledger_devtools.workspace import Workspace
from ledger_platform.config.settings import Settings
from ledger_platform.http.deps import unguarded_routes
from ledger_platform.runtime.app import App

ROOT: Final = Path(__file__).parents[4]
PUBLISHED: Final = Path(__file__).parents[2] / "openapi.json"
PLACEHOLDERS: Final = Settings(
    environment="test",
    database_url=SecretStr("postgresql+psycopg://openapi@localhost/openapi"),
    token_issuer="https://issuer.invalid",
    token_audience="ledger",
    token_jwks_url="https://issuer.invalid/oauth/v2/keys",
    archive_backend="file",
    archive_path=Path(tempfile.gettempdir()) / "ledger-openapi-archive",
)
"""The document depends on none of these: nothing connects, nothing is archived (the app never starts)."""
PREFIXES: Final = CHANNEL.entry.http_prefixes
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


def document() -> Document:
    """The channel's paths of the ``api`` app's document."""
    whole = ANY_DOCUMENT.validate_python(app().openapi())
    paths = PATHS.validate_python(whole["paths"])
    whole["paths"] = {path: item for path, item in paths.items() if path.startswith(PREFIXES)}
    return DOCUMENT.validate_json(json.dumps(whole))


def operations() -> list[Operation]:
    return [operation for operations in document()["paths"].values() for operation in operations.values()]


def test_the_published_document_is_the_one_sync_writes() -> None:
    country = Workspace.load(ROOT).by_name("do")
    made = openapi_text(country, api_document())
    assert PUBLISHED.read_text(encoding="utf-8") == made, "regenerate it: uv run ledger-devtools sync"


def test_the_document_holds_the_ranges_three_operations_and_a_post_takes_a_key_in_a_mode() -> None:
    parsed = document()
    assert parsed["openapi"].startswith("3.1.")
    assert {path: sorted(operations) for path, operations in parsed["paths"].items()} == {
        "/v1/do/numbering_ranges": ["get", "post"],
        "/v1/do/numbering_ranges/{range_id}": ["get"],
    }
    post = parsed["paths"]["/v1/do/numbering_ranges"]["post"]
    required = post.get("parameters", [])
    for header in ("Idempotency-Key", "Ledger-Mode", "Ledger-Company"):
        assert {"name": header, "in": "header", "required": True} in required
    answers = post.get("responses", {})
    assert {"201", "409", "422"} <= set(answers)
    assert {"Location", "ETag"} <= set(answers["201"].get("headers", {}))


def test_every_operation_is_for_bearers_of_a_token_and_declares_what_it_requires() -> None:
    for operation in operations():
        assert operation.get("security") == [{"HTTPBearer": []}]
        assert "503" in operation.get("responses", {})
    routes = app().routes
    channel = [
        context.path for context in iter_route_contexts(routes) if (context.path or "").startswith(PREFIXES)
    ]
    assert len(channel) == len(operations())
    assert unguarded_routes(routes) == ()
