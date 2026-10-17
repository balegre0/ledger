"""Reading an OpenAPI document as data, for the chassis's corrections of what FastAPI wrote (§9)."""

from typing import TypeIs


def is_object(value: object) -> TypeIs[dict[str, object]]:
    """Whether ``value`` is a JSON object."""
    return isinstance(value, dict)


def is_array(value: object) -> TypeIs[list[object]]:
    """Whether ``value`` is a JSON array."""
    return isinstance(value, list)


def operations_of(document: dict[str, object]) -> list[dict[str, object]]:
    """Every operation of ``document`` (each ``get``, ``post``, ... of every path), in place."""
    paths = document.get("paths")
    return [
        operation
        for item in (paths.values() if is_object(paths) else ())
        if is_object(item)
        for operation in item.values()
        if is_object(operation)
    ]


def parameters_of(operation: dict[str, object]) -> list[dict[str, object]]:
    """The parameters ``operation`` documents (those that are objects), in place for the caller to correct."""
    parameters = operation.get("parameters")
    return [parameter for parameter in (parameters if is_array(parameters) else []) if is_object(parameter)]
