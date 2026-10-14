import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from enum import IntEnum, StrEnum
from typing import ClassVar, final
from uuid import UUID

import pytest

from ledger_kernel.events import Event
from ledger_kernel.money import Currency, Money
from ledger_kernel.tax_ids import TaxId, TaxIdScheme
from ledger_platform.data.codec import (
    MAX_PAYLOAD,
    UnencodableValueError,
    canonical_json,
    encode,
    jsonb_size,
    payload_of,
)


class Colour(StrEnum):
    RED = "red"


class Rank(IntEnum):
    FIRST = 1


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Priced(Event):
    TYPE: ClassVar[str] = "probe.item.priced"
    VERSION: ClassVar[int] = 1
    price: Money
    tax_id: TaxId
    tags: tuple[str, ...]


ID = UUID("01890a5d-ac96-774b-bcce-b302099a8057")
SANTO_DOMINGO = timezone(timedelta(hours=-4))


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        (True, True),
        (False, False),
        (3, 3),
        ("x", "x"),
        (Decimal("1E+2"), "100"),
        (Decimal("0.10"), "0.10"),
        (Decimal("1E-7"), "0.0000001"),
        (ID, str(ID)),
        (date(2026, 11, 15), "2026-11-15"),
        (datetime(2026, 11, 15, 4, 0, tzinfo=UTC), "2026-11-15T04:00:00+00:00"),
        (datetime(2026, 11, 15, 0, 0, tzinfo=SANTO_DOMINGO), "2026-11-15T04:00:00+00:00"),
        (Colour.RED, "red"),
        (Rank.FIRST, 1),
        (Currency.of("DOP"), "DOP"),
        ((1, "a"), [1, "a"]),
        ([Decimal(1)], ["1"]),
        ({"k": Decimal(1)}, {"k": "1"}),
        (Money.of("12.50", Currency.of("DOP")), {"amount": "12.50", "currency": "DOP"}),
    ],
)
def test_values_have_one_json_form(value: object, expected: object) -> None:
    assert encode(value) == expected


def test_an_events_payload_is_its_own_fields() -> None:
    event = Priced(
        scope=None,
        subject=ID,
        aggregate_version=2,
        price=Money.of("12.50", Currency.of("DOP")),
        tax_id=TaxId(TaxIdScheme("do.rnc"), "101850043"),
        tags=("a",),
    )
    assert payload_of(event) == {
        "price": {"amount": "12.50", "currency": "DOP"},
        "tax_id": {"scheme": "do.rnc", "value": "101850043"},
        "tags": ["a"],
    }


@pytest.mark.parametrize(
    ("value", "where", "kind"),
    [
        (datetime(2026, 1, 1, tzinfo=UTC).replace(tzinfo=None), "$", "naive datetime"),
        ({"k": object()}, "$.k", "object"),
        ({1: "x"}, "$", "mapping with a key that is not text"),
        ([1, 1.5], "$[1]", "float"),
        ([float("inf")], "$[0]", "float"),
        ({"a": "x\x00y"}, "$.a", "text with a NUL character"),
        ({"a\x00": 1}, "$", "mapping key with a NUL character"),
        ({"a": [Decimal("NaN")]}, "$.a[0]", "Decimal"),
        (b"x", "$", "bytes"),
        (Money, "$", "type"),
    ],
)
def test_anything_without_a_json_form_is_a_defect_named_by_its_path(
    value: object, where: str, kind: str
) -> None:
    with pytest.raises(
        UnencodableValueError, match=f"^{re.escape(where)} holds a {kind}, which has no JSON form$"
    ):
        encode(value)


@pytest.mark.parametrize(
    ("value", "where"),
    [
        ({"101850043": object()}, "$[#0]"),
        ({"ok": 1, "tax id": [object()]}, "$[#1][0]"),
        ({"a" * 33: object()}, "$[#0]"),
        ({"name_2": {"inner": object()}}, "$.name_2.inner"),
    ],
)
def test_a_mapping_key_is_named_only_when_it_looks_like_a_name(value: object, where: str) -> None:
    """Keys can be data (a tax id as a key): only short identifier-like keys are shown, others by position."""
    with pytest.raises(UnencodableValueError, match=f"^{re.escape(where)} holds") as raised:
        encode(value)
    assert "101850043" not in str(raised.value)


def test_equal_instants_have_equal_bytes() -> None:
    utc = datetime(2026, 11, 15, 4, 0, tzinfo=UTC)
    local = datetime(2026, 11, 15, 0, 0, tzinfo=SANTO_DOMINGO)
    assert canonical_json(encode({"at": utc})) == canonical_json(encode({"at": local}))


def test_an_unencodable_value_is_never_shown() -> None:
    with pytest.raises(UnencodableValueError) as raised:
        encode({"secret": b"101850043"})
    assert "101850043" not in str(raised.value)
    assert raised.value.code == "unencodable_value"


def test_canonical_json_is_compact_and_sorted() -> None:
    assert canonical_json({"b": 1, "a": [True, None], "ñ": "ü"}) == '{"a":[true,null],"b":1,"ñ":"ü"}'.encode()


def test_jsonb_size_counts_bytes_as_postgresql_prints_jsonb() -> None:
    """Real PostgreSQL agrees in ``test_data_bound``; floats never reach it (the codec refuses them)."""
    assert jsonb_size({"a": "ñ", "b": 1}) == len('{"a": "ñ", "b": 1}'.encode())


def test_the_payload_limit_is_64_kib() -> None:
    assert MAX_PAYLOAD == 64 * 1024
