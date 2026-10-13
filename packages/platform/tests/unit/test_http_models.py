"""``Page[T]`` and ``page_query``: the one list model and list request of every module's API (§9.7)."""

from typing import Annotated, ClassVar, Final
from uuid import UUID

import httpx
import pytest
from fastapi import Depends, FastAPI
from pydantic import BaseModel, ConfigDict, TypeAdapter

from ledger_kernel.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, encode_cursor
from ledger_kernel.pagination import Page as KernelPage
from ledger_platform.http.models import Page, page_of_items, page_query
from ledger_platform.http.problems import install_problem_handlers
lazy from ledger_kernel.pagination import PageRequest

THING: Final = UUID(int=7)
OTHER: Final = UUID(int=8)


class ThingView(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: UUID
    name: str


class Schemas(BaseModel):
    schemas: dict[str, dict[str, object]]


class Components(BaseModel):
    components: Schemas


class Asked(BaseModel):
    size: int
    after: UUID | None
    before: UUID | None


def test_a_page_serializes_its_items_whether_more_follow_and_where_they_start() -> None:
    last = Page[ThingView](items=[ThingView(id=THING, name="a")], has_more=False, next_cursor=None)
    assert last.model_dump(mode="json") == {
        "items": [{"id": str(THING), "name": "a"}],
        "has_more": False,
        "next_cursor": None,
    }


def test_a_page_is_named_after_its_item_in_the_openapi_document() -> None:
    app = FastAPI()

    @app.get("/things")
    async def things() -> Page[ThingView]:
        return Page[ThingView](items=[], has_more=False, next_cursor=None)

    schemas = Components.model_validate(app.openapi()).components.schemas
    assert "Page_ThingView_" in schemas
    properties = TypeAdapter(dict[str, object]).validate_python(schemas["Page_ThingView_"]["properties"])
    assert properties.keys() == {"items", "has_more", "next_cursor"}


@pytest.mark.parametrize(("cursor", "more"), [("next", True), (None, False)])
def test_a_kernel_page_is_rendered_item_by_item(cursor: str | None, *, more: bool) -> None:
    kept = KernelPage(items=(("a", 1), ("b", 2)), next_cursor=cursor)
    page = page_of_items(kept, lambda row: ThingView(id=UUID(int=row[1]), name=row[0]))
    assert page == Page[ThingView](
        items=[ThingView(id=UUID(int=1), name="a"), ThingView(id=UUID(int=2), name="b")],
        has_more=more,
        next_cursor=cursor,
    )


def lister() -> FastAPI:
    app = FastAPI()
    install_problem_handlers(app)

    @app.get("/things")
    async def things(asked: Annotated[PageRequest, Depends(page_query)]) -> Asked:
        return Asked(size=asked.size, after=asked.after, before=asked.before)

    return app


async def listed(query: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=lister(), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        return await client.get(f"/things{query}")


@pytest.mark.parametrize(
    ("query", "asked"),
    [
        ("", Asked(size=DEFAULT_PAGE_SIZE, after=None, before=None)),
        ("?limit=1", Asked(size=1, after=None, before=None)),
        (f"?limit={MAX_PAGE_SIZE}", Asked(size=MAX_PAGE_SIZE, after=None, before=None)),
        (f"?starting_after={encode_cursor(THING)}", Asked(size=DEFAULT_PAGE_SIZE, after=THING, before=None)),
        (f"?ending_before={encode_cursor(OTHER)}&limit=3", Asked(size=3, after=None, before=OTHER)),
    ],
    ids=["first-page", "smallest", "largest", "forwards", "backwards"],
)
async def test_a_list_asks_for_a_page_by_limit_and_one_opaque_cursor(query: str, asked: Asked) -> None:
    response = await listed(query)
    assert (response.status_code, Asked.model_validate_json(response.content)) == (200, asked)


@pytest.mark.parametrize(
    ("query", "code"),
    [
        ("?limit=0", "invalid_request"),
        (f"?limit={MAX_PAGE_SIZE + 1}", "invalid_request"),
        ("?starting_after=not-a-cursor-of-ours-at-all", "invalid_cursor"),
        (f"?ending_before={'A' * 65}", "invalid_request"),
        (
            f"?starting_after={encode_cursor(THING)}&ending_before={encode_cursor(OTHER)}",
            "page_cursors_conflict",
        ),
    ],
    ids=["zero", "over-the-maximum", "foreign-cursor", "huge-cursor", "both-directions"],
)
async def test_a_page_that_cannot_be_read_is_refused(query: str, code: str) -> None:
    response = await listed(query)
    assert (response.status_code, response.json()["code"]) == (422, code)
    assert "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA" not in response.text
