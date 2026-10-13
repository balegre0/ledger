"""JSON answers read typed, for tests and the spike CLI (``response.json()`` is ``Any``)."""

from typing import Final

from pydantic import TypeAdapter
lazy import httpx

_OBJECT: Final = TypeAdapter(dict[str, object])
_LIST: Final = TypeAdapter(list[dict[str, object]])


def json_object(response: httpx.Response) -> dict[str, object]:
    """``response``'s body, a JSON object."""
    return _OBJECT.validate_json(response.content)


def json_list(response: httpx.Response) -> list[dict[str, object]]:
    """``response``'s body, a JSON list of objects."""
    return _LIST.validate_json(response.content)


def objects(value: object) -> list[dict[str, object]]:
    """A value of an answer already read, known to be a list of objects (DGII's ``mensajes``)."""
    return _LIST.validate_python(value)
