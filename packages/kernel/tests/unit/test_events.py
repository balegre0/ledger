from dataclasses import FrozenInstanceError, dataclass
from typing import ClassVar, final
from uuid import uuid7

import pytest

from ledger_kernel.events import Event, InvalidEventClassError


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Renamed(Event):
    TYPE: ClassVar[str] = "probe.thing.renamed"
    VERSION: ClassVar[int] = 1
    name: str


def test_an_event_carries_its_envelope_fields_and_its_payload() -> None:
    subject = uuid7()
    event = Renamed(scope=None, subject=subject, aggregate_version=3, name="x")
    assert (event.TYPE, event.VERSION, event.subject, event.aggregate_version, event.name) == (
        "probe.thing.renamed",
        1,
        subject,
        3,
        "x",
    )
    field = "name"
    with pytest.raises(FrozenInstanceError):  # frozen: a fact does not change
        setattr(event, field, "y")


def test_an_event_class_without_a_type_is_refused_when_it_is_declared() -> None:
    with pytest.raises(InvalidEventClassError, match="Untyped"):

        @dataclass(frozen=True, slots=True, kw_only=True)
        class Untyped(Event):
            VERSION: ClassVar[int] = 1


@pytest.mark.parametrize(
    ("type_name", "version"), [("renamed", 1), ("probe.Thing.renamed", 1), ("probe.thing.renamed", 0)]
)
def test_an_event_class_with_a_bad_type_or_version_is_refused(type_name: str, version: int) -> None:
    with pytest.raises(InvalidEventClassError):
        type("Bad", (Event,), {"TYPE": type_name, "VERSION": version, "__annotations__": {}})
