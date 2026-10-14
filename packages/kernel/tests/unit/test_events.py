from dataclasses import FrozenInstanceError, dataclass
from typing import ClassVar, final
from uuid import uuid7

import pytest

from ledger_kernel.events import Event, InvalidEventClassError, InvalidEventTypeError, parse_event_type


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
        dataclass(frozen=True, slots=True, kw_only=True)(type("Untyped", (Event,), {"VERSION": 1}))


@pytest.mark.parametrize(
    ("type_name", "version"), [("renamed", 1), ("probe.Thing.renamed", 1), ("probe.thing.renamed", 0)]
)
def test_an_event_class_with_a_bad_type_or_version_is_refused(type_name: str, version: int) -> None:
    with pytest.raises(InvalidEventClassError):
        type("Bad", (Event,), {"TYPE": type_name, "VERSION": version, "__annotations__": {}})


@pytest.mark.parametrize(
    ("type_name", "version"),
    [
        (7, 1),
        (None, 1),
        ("probe.thing.renamed", True),
        ("probe.thing.renamed", "1"),
        ("probe.thing.renamed", 1.0),
    ],
    ids=["number-type", "no-type", "bool-version", "text-version", "float-version"],
)
def test_an_event_class_with_a_type_or_version_of_the_wrong_kind_is_refused(
    type_name: object, version: object
) -> None:
    with pytest.raises(InvalidEventClassError):
        type("Bad", (Event,), {"TYPE": type_name, "VERSION": version, "__annotations__": {}})


def test_an_event_class_without_a_version_is_refused() -> None:
    with pytest.raises(InvalidEventClassError):
        type("Unversioned", (Event,), {"TYPE": "probe.thing.made", "__annotations__": {}})


def test_a_subclass_of_an_event_must_declare_its_own_type() -> None:
    made = type("Made", (Event,), {"TYPE": "probe.thing.made", "VERSION": 1, "__annotations__": {}})
    with pytest.raises(InvalidEventClassError, match="Remade"):
        type("Remade", (made,), {"VERSION": 2, "__annotations__": {}})
    remade = type("Remade", (made,), {"TYPE": "probe.thing.remade", "VERSION": 1, "__annotations__": {}})
    assert remade.__name__ == "Remade"


@dataclass(frozen=True, slots=True, kw_only=True)
class ThingEvent(Event):
    """An abstract base: the fields every event of a thing carries, and no type of its own."""

    ABSTRACT: ClassVar[bool] = True
    thing: str


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class ThingMade(ThingEvent):
    TYPE: ClassVar[str] = "probe.thing.made"
    VERSION: ClassVar[int] = 1


def test_an_abstract_base_declares_shared_fields_and_its_subclasses_their_types() -> None:
    event = ThingMade(scope=None, subject=None, aggregate_version=None, thing="t")
    assert (event.TYPE, event.thing) == ("probe.thing.made", "t")


def test_being_abstract_is_not_inherited() -> None:
    with pytest.raises(InvalidEventClassError, match="Forgotten"):
        type("Forgotten", (ThingEvent,), {"VERSION": 1, "__annotations__": {}})
    with pytest.raises(InvalidEventClassError, match="Concrete"):
        type("Concrete", (ThingEvent,), {"ABSTRACT": False, "VERSION": 1, "__annotations__": {}})


def test_an_event_type_has_three_dotted_lower_case_parts() -> None:
    assert parse_event_type("identity.company.registered") == "identity.company.registered"
    for text in ("identity.company", "Identity.company.registered", "a.b.c.d", "a.b.c "):
        with pytest.raises(InvalidEventTypeError, match=r"<module>\.<aggregate>\.<past_participle>"):
            parse_event_type(text)
