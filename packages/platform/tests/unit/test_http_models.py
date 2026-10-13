"""``Page[T]``: the one list model every module's API answers with (§9.7)."""

from typing import ClassVar
from uuid import UUID

from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict, TypeAdapter

from ledger_kernel.pagination import Page as KernelPage
from ledger_platform.http.models import Page, page_of_items

THING: UUID = UUID(int=7)


class ThingView(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: UUID
    name: str


class Schemas(BaseModel):
    schemas: dict[str, dict[str, object]]


class Components(BaseModel):
    components: Schemas


def test_a_page_serializes_its_items_and_the_cursor_of_the_next() -> None:
    last = Page[ThingView](items=[ThingView(id=THING, name="a")], next_cursor=None)
    assert last.model_dump(mode="json") == {
        "items": [{"id": str(THING), "name": "a"}],
        "next_cursor": None,
    }
    assert Page[ThingView](items=[], next_cursor="abc").model_dump() == {"items": [], "next_cursor": "abc"}


def test_a_page_is_named_after_its_item_in_the_openapi_document() -> None:
    app = FastAPI()

    @app.get("/things")
    async def things() -> Page[ThingView]:
        return Page[ThingView](items=[], next_cursor=None)

    schemas = Components.model_validate(app.openapi()).components.schemas
    assert "Page_ThingView_" in schemas
    properties = TypeAdapter(dict[str, object]).validate_python(schemas["Page_ThingView_"]["properties"])
    assert properties.keys() == {"items", "next_cursor"}


def test_a_kernel_page_is_rendered_item_by_item() -> None:
    kept = KernelPage(items=(("a", 1), ("b", 2)), next_cursor="next")
    page = page_of_items(kept, lambda row: ThingView(id=UUID(int=row[1]), name=row[0]))
    assert page == Page[ThingView](
        items=[ThingView(id=UUID(int=1), name="a"), ThingView(id=UUID(int=2), name="b")],
        next_cursor="next",
    )
