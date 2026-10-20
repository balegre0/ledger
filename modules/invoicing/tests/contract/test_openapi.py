"""invoicing's OpenAPI document (3.1), as published in ``modules/invoicing/openapi.json`` (§9.1): a snapshot.

``uv run ledger-devtools sync`` writes the file from the code (the ``api`` app on placeholder settings, its
paths under the manifest's prefixes); this test compares it, as ``ledger-devtools check`` does, and the others
pin what the document says: the limits of a request, and what every operation takes and answers.
"""

from pathlib import Path
from typing import Final, TypedDict

from fastapi.routing import iter_route_contexts
from pydantic import SecretStr, TypeAdapter

from ledger_devtools.openapi import api_document, openapi_text
from ledger_devtools.workspace import Workspace
from ledger_invoicing._manifest import MANIFEST
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
    components: dict[str, dict[str, dict[str, object]]]


DOCUMENT: Final = TypeAdapter(Document)
SCHEMA: Final = TypeAdapter(dict[str, object])


def published() -> Document:
    return DOCUMENT.validate_json(PUBLISHED.read_text(encoding="utf-8"))


def operations() -> list[tuple[str, str, Operation]]:
    """Every operation of the published document: its path, its method and itself."""
    return [
        (path, method, operation)
        for path, item in published()["paths"].items()
        for method, operation in item.items()
    ]


def required_headers(operation: Operation) -> set[str]:
    return {
        parameter["name"]
        for parameter in operation.get("parameters", [])
        if parameter["in"] == "header" and parameter["required"]
    }


def test_the_published_document_is_the_one_sync_writes() -> None:
    invoicing = Workspace.load(ROOT).by_name("invoicing")
    made = openapi_text(invoicing, api_document())
    assert PUBLISHED.read_text(encoding="utf-8") == made, "regenerate it: uv run ledger-devtools sync"


def test_the_document_is_openapi_3_1_and_holds_invoicing_s_paths_only() -> None:
    document = published()
    assert document["openapi"].startswith("3.1.")
    assert sorted(document["paths"]) == [
        "/v1/invoices",
        "/v1/invoices/{invoice_id}",
        "/v1/invoices/{invoice_id}/issue",
        "/v1/invoices/{invoice_id}/void",
    ]


def test_every_operation_names_its_company_and_mode_and_every_post_its_key() -> None:
    for path, method, operation in operations():
        headers = required_headers(operation)
        assert {"Ledger-Company", "Ledger-Mode"} <= headers, (method, path)
        assert ("Idempotency-Key" in headers) == (method == "post"), (method, path)


def test_issuing_and_voiding_require_if_match_and_nothing_else_does() -> None:
    conditional = sorted(
        (method, path)
        for path, method, operation in operations()
        if "If-Match" in required_headers(operation)
    )
    assert conditional == [
        ("post", "/v1/invoices/{invoice_id}/issue"),
        ("post", "/v1/invoices/{invoice_id}/void"),
    ]
    for path, method, operation in operations():
        assert ({"412", "428"} <= set(operation.get("responses", {}))) == ((method, path) in conditional)


def test_every_answer_of_a_document_tells_its_version_and_only_a_creation_where_it_lives() -> None:
    for path, method, operation in operations():
        success = next(status for status in operation.get("responses", {}) if status.startswith("2"))
        headers = operation.get("responses", {})[success].get("headers", {})
        assert ("Location" in headers) == (success == "201"), (method, path)
        assert ("ETag" in headers) == (path != "/v1/invoices" or method == "post"), (method, path)


def test_every_error_is_a_problem_document_and_every_operation_may_find_the_service_unavailable() -> None:
    for _, _, operation in operations():
        assert operation.get("security") == [{"HTTPBearer": []}]
        assert "503" in operation.get("responses", {})
        for status, answer in operation.get("responses", {}).items():
            if status.startswith(("4", "5")):
                assert list(answer.get("content", {})) == ["application/problem+json"]


def test_a_request_s_limits_are_in_the_schema() -> None:
    schemas = published()["components"]["schemas"]
    new_invoice = SCHEMA.validate_python(schemas["NewInvoice"]["properties"])
    assert new_invoice["lines"] == {
        "items": {"$ref": "#/components/schemas/LineBody"},
        "maxItems": 1000,
        "minItems": 1,
        "title": "Lines",
        "type": "array",
    }
    assert schemas["Quantity"] == {"pattern": r"^[0-9]{1,18}(\.[0-9]{1,6})?$", "type": "string"}
    assert schemas["Amount"] == {"pattern": r"^[0-9]{1,18}(\.[0-9]{1,12})?$", "type": "string"}


def test_every_route_needs_a_bearer_token_and_declares_what_it_requires() -> None:
    routes = App("api", settings=PLACEHOLDERS).asgi().routes
    mine = [
        context.path for context in iter_route_contexts(routes) if (context.path or "").startswith(PREFIXES)
    ]
    assert len(mine) == len(operations())
    assert unguarded_routes(routes) == ()


ALL_OF: Final = TypeAdapter(dict[str, list[dict[str, object]]])


def test_a_creation_s_conflict_declares_the_holder_of_a_taken_business_key() -> None:
    """§9.6: ``external_reference_taken`` names the document that holds the key in ``existing_id``."""
    answers = published()["paths"]["/v1/invoices"]["post"].get("responses", {})
    schema = ALL_OF.validate_python(answers["409"].get("content", {})["application/problem+json"]["schema"])
    problem, extension = schema["allOf"]
    assert problem["title"] == "Problem"
    assert extension == {
        "type": "object",
        "properties": {
            "existing_id": {
                "type": "string",
                "pattern": r"^inv_[0-7][0-9a-hjkmnp-tv-z]{25}$",
                "description": "The document holding the external_reference (external_reference_taken only).",
            }
        },
    }
    for path, method, operation in operations():
        if (path, method) != ("/v1/invoices", "post") and "409" in operation.get("responses", {}):
            assert "existing_id" not in str(operation.get("responses", {})["409"])
