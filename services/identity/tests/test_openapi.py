from pathlib import Path
from typing import Final, TypedDict

from pydantic import TypeAdapter
lazy import pytest

from ledger_identity import openapi
from ledger_identity.main import VERSION, create_app
from ledger_identity.openapi import document

PUBLISHED: Final = Path(__file__).parents[1] / "openapi.json"
Parameter = TypedDict("Parameter", {"name": str, "in": str, "required": bool})


class Answer(TypedDict, total=False):
    content: dict[str, dict[str, object]]


class Operation(TypedDict, total=False):
    parameters: list[Parameter]
    security: list[dict[str, list[str]]]
    responses: dict[str, Answer]


class Document(TypedDict):
    openapi: str
    paths: dict[str, dict[str, Operation]]


DOCUMENT: Final = TypeAdapter(Document)


def test_the_published_document_is_the_one_the_code_makes() -> None:
    assert PUBLISHED.read_text(encoding="utf-8") == document(), "regenerate it: uv run poe openapi"


def test_the_document_is_openapi_3_1_and_every_post_takes_an_idempotency_key() -> None:
    parsed = DOCUMENT.validate_json(document())
    assert parsed["openapi"].startswith("3.1.")
    posts = [operations["post"] for operations in parsed["paths"].values() if "post" in operations]
    assert posts
    for post in posts:
        assert {"name": "Idempotency-Key", "in": "header", "required": True} in post.get("parameters", [])


def test_every_change_of_a_company_requires_if_match() -> None:
    parsed = DOCUMENT.validate_json(document())
    changes = [
        parsed["paths"]["/v1/companies/{company_id}"]["patch"],
        parsed["paths"]["/v1/companies/{company_id}/deactivate"]["post"],
    ]
    for change in changes:
        assert {"name": "If-Match", "in": "header", "required": False} in change.get("parameters", [])
        assert {"412", "428"} <= set(change.get("responses", {}))


def test_every_operation_is_for_bearers_of_a_token() -> None:
    parsed = DOCUMENT.validate_json(document())
    for operations in parsed["paths"].values():
        for operation in operations.values():
            assert operation.get("security") == [{"HTTPBearer": []}]


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


def test_the_command_line_writes_the_document(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "openapi.json"
    monkeypatch.setattr("sys.argv", ["ledger-identity-openapi", str(target)])
    assert openapi.main() == 0
    assert target.read_text(encoding="utf-8") == document()


def test_the_command_line_needs_a_path(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.argv", ["ledger-identity-openapi"])
    assert openapi.main() == 2
    assert capsys.readouterr().err == "usage: ledger-identity-openapi PATH\n"


def test_granians_factory_builds_the_app_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in {
        "LEDGER_DATABASE_URL": "postgresql+psycopg://identity:secret@localhost/identity",
        "LEDGER_TOKEN_ISSUER": "https://issuer.example",
        "LEDGER_TOKEN_AUDIENCE": "ledger",
        "LEDGER_TOKEN_JWKS_URL": "https://issuer.example/oauth/v2/keys",
    }.items():
        monkeypatch.setenv(name, value)
    app = create_app()
    assert (app.title, app.version) == ("identity", VERSION)
