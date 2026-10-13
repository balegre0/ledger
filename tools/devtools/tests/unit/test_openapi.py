import json
from collections.abc import Callable
from typing import Final
lazy from pathlib import Path

from pydantic import TypeAdapter

from ledger_devtools.openapi import api_document, openapi_text
from ledger_devtools.workspace import Workspace

type Editor = Callable[[str, str, str], None]

DOCUMENT: Final = TypeAdapter(dict[str, object])
TABLE: Final = TypeAdapter(dict[str, dict[str, object]])


def ref(name: str) -> dict[str, str]:
    return {"$ref": f"#/components/schemas/{name}"}


def answer(name: str) -> dict[str, object]:
    return {"200": {"content": {"application/json": {"schema": ref(name)}}}}


WHOLE: Final[dict[str, object]] = {
    "openapi": "3.1.0",
    "info": {"title": "ledger", "version": "1"},
    "paths": {
        "/v1/companies": {"post": {"requestBody": {"content": {"application/json": {"schema": ref("New")}}}}},
        "/v1/companies/{company_id}": {"get": {"responses": answer("Company")}},
        "/v1/companiesx": {"get": {"responses": answer("Stray")}},
        "/v1/organization": {"get": {"responses": answer("Organization")}},
        "/v1/invoices": {"get": {"responses": answer("Invoice")}},
    },
    "components": {
        "schemas": {
            "New": {"properties": {"tax_id": ref("TaxId")}},
            "Company": {"properties": {"tax_id": ref("TaxId"), "items": {"items": ref("Branch")}}},
            "TaxId": {"type": "object"},
            "Branch": {"properties": {"company": ref("Company")}},
            "Organization": {"type": "object"},
            "Invoice": {"properties": {"company": ref("Company"), "lines": ref("Line")}},
            "Line": {"type": "object"},
            "Stray": {"type": "object"},
        },
        "securitySchemes": {"HTTPBearer": {"type": "http", "scheme": "bearer"}},
    },
}


def test_a_module_s_document_holds_its_paths_and_what_they_reference(tree: Path, edit: Editor) -> None:
    edit(
        "modules/identity", 'prefixes = ["/v1/companies"]', 'prefixes = ["/v1/organization", "/v1/companies"]'
    )
    text = openapi_text(Workspace.load(tree).by_name("identity"), WHOLE)
    document = DOCUMENT.validate_json(text)
    assert sorted(TABLE.validate_python(document["paths"])) == [
        "/v1/companies",
        "/v1/companies/{company_id}",
        "/v1/organization",
    ]
    components = TABLE.validate_python(document["components"])
    assert sorted(components["schemas"]) == ["Branch", "Company", "New", "Organization", "TaxId"]
    assert components["securitySchemes"] == {"HTTPBearer": {"type": "http", "scheme": "bearer"}}
    assert document["info"] == {"title": "ledger", "version": "1"}
    assert text == json.dumps(json.loads(text), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def test_a_package_without_http_has_an_empty_document(tree: Path) -> None:
    document = DOCUMENT.validate_json(openapi_text(Workspace.load(tree).by_name("kernel"), WHOLE))
    assert document["paths"] == {}
    assert TABLE.validate_python(document["components"])["schemas"] == {}


def test_the_api_document_is_the_api_app_s_and_connects_nowhere() -> None:
    document = api_document()
    assert TABLE.validate_python(document["paths"])
    assert document is api_document()  # built once per process
