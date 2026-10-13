from typing import Protocol, TypeIs, runtime_checkable

import pytest

from ledger_platform.runtime.keys import (
    BindingMissingError,
    BindingTypeError,
    Directory,
    DuplicateBindingError,
    Key,
    UndeclaredDependencyError,
)


@runtime_checkable
class Greeter(Protocol):
    def greet(self) -> str: ...


def is_greeter(value: object) -> TypeIs[Greeter]:
    return isinstance(value, Greeter)


def anything(value: object) -> TypeIs[object]:
    del value
    return True


GREETER = Key[Greeter]("greeter", is_greeter)


class English:
    def greet(self) -> str:
        return "hello"


def test_a_bound_value_is_read_typed_through_a_declared_dependency() -> None:
    directory = Directory("apis")
    directory.add(GREETER.bind(English()))
    assert directory.view(frozenset({"greeter"})).get(GREETER).greet() == "hello"


def test_an_undeclared_dependency_is_refused_even_when_bound() -> None:
    directory = Directory("apis")
    directory.add(GREETER.bind(English()))
    with pytest.raises(UndeclaredDependencyError, match="greeter"):
        directory.view(frozenset()).get(GREETER)


def test_a_missing_or_doubled_binding_is_a_defect() -> None:
    directory = Directory("apis")
    with pytest.raises(BindingMissingError, match="apis 'greeter'"):
        directory.view(frozenset({"greeter"})).get(GREETER)
    directory.add(GREETER.bind(English()))
    with pytest.raises(DuplicateBindingError, match="apis 'greeter'"):
        directory.add(GREETER.bind(English()))


def test_a_value_of_the_wrong_type_under_a_name_is_a_defect() -> None:
    directory = Directory("apis")
    directory.add(Key[object]("greeter", anything).bind(42))
    with pytest.raises(BindingTypeError, match="greeter"):
        directory.view(frozenset({"greeter"})).get(GREETER)


def test_a_key_names_what_it_binds() -> None:
    binding = GREETER.bind(English())
    assert binding.name == GREETER.name == "greeter"
    assert isinstance(binding.value, English)
