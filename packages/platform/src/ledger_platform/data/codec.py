"""One JSON codec for event payloads, job payloads and audit hashes (§3.5).

``Decimal`` as a string, never a float; UUIDs as strings; aware dates and times as ISO 8601; enumerations as
their value; a ``Currency`` as its code; other dataclasses (``Money``, ``TaxId``) as objects of their fields;
tuples and lists as arrays; mappings with text keys as objects. Anything else is a defect of the code that
built the value, named by its path (``$.price.amount``) and its type, never shown.
"""

import json
import math
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import ClassVar, Final, TypeIs
from uuid import UUID

from pydantic import TypeAdapter

from ledger_kernel.errors import LedgerError
from ledger_kernel.money import Currency
lazy from ledger_kernel.events import Event

type JsonValue = str | int | float | bool | list[JsonValue] | dict[str, JsonValue] | None
MAX_PAYLOAD: Final = 65_536
"""An event's payload, in bytes as PostgreSQL prints ``jsonb`` (the outbox's check)."""
_ENVELOPE: Final = frozenset({"scope", "subject", "aggregate_version"})
_ANYTHING: Final = TypeAdapter(object)


class UnencodableValueError(LedgerError):
    """A value with no JSON form reached the codec: a defect of the code that built it."""

    code: ClassVar[str] = "unencodable_value"

    def __init__(self, path: str, kind: str) -> None:
        super().__init__(f"{path} holds a {kind}, which has no JSON form")


def encode(value: object, path: str = "$") -> JsonValue:
    """``value``'s JSON form (see the module); ``path`` names it in an error."""
    match value:
        case Enum(value=str() | int() as plain):
            return plain
        case None | bool() | int() | str():
            return value
        case _ if _is_array(value):
            return [encode(item, f"{path}[{index}]") for index, item in enumerate(value)]
        case _ if _is_object(value):
            return _object(value, path)
        case _:
            return _scalar(value, path)


def _is_array(value: object) -> TypeIs[tuple[object, ...] | list[object]]:
    return isinstance(value, tuple | list)


def _is_object(value: object) -> TypeIs[Mapping[object, object]]:
    return isinstance(value, Mapping)


def _attribute(value: object, name: str) -> object:
    """``value.name``, as the ``object`` it is (``getattr`` answers ``Any``)."""
    return _ANYTHING.validate_python(getattr(value, name))


def _scalar(value: object, path: str) -> JsonValue:
    """A number, an id, a date or a currency; anything else must be a dataclass instance (``_fields``)."""
    match value:
        case float() if math.isfinite(value):
            return value
        case Decimal() if value.is_finite():
            return format(value, "f")
        case UUID():
            return str(value)
        case datetime() if value.tzinfo is None:
            raise UnencodableValueError(path, "naive datetime")
        case date():
            return value.isoformat()
        case Currency():
            return value.code
        case _:
            return _fields(value, path)


def _fields(value: object, path: str) -> JsonValue:
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: encode(_attribute(value, field.name), f"{path}.{field.name}")
            for field in fields(value)
        }
    raise UnencodableValueError(path, type(value).__name__)


def _object(value: Mapping[object, object], path: str) -> dict[str, JsonValue]:
    encoded: dict[str, JsonValue] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise UnencodableValueError(path, "mapping with a key that is not text")
        encoded[key] = encode(item, f"{path}.{key}")
    return encoded


def payload_of(event: Event) -> dict[str, JsonValue]:
    """An event's payload: its own fields, not the envelope's."""
    return {
        field.name: encode(_attribute(event, field.name), f"$.{field.name}")
        for field in fields(event)
        if field.name not in _ENVELOPE
    }


def canonical_json(value: JsonValue) -> bytes:
    """Compact, keys sorted, UTF-8: equal values give equal bytes (what audit hashes)."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()


def jsonb_size(value: JsonValue) -> int:
    """The size of ``value`` as PostgreSQL prints ``jsonb`` (``", "`` and ``": "`` separators), in bytes."""
    return len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode())
