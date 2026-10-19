from typing import Protocol, TypeIs, runtime_checkable

import pytest

from ledger_platform.runtime.keys import (
    BindingMissingError,
    BindingTypeError,
    ChainDirectory,
    Directory,
    DuplicateBindingError,
    Key,
    UndeclaredBindingError,
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
GREETERS = frozenset({"greeter"})


class English:
    def greet(self) -> str:
        return "hello"


def test_a_bound_value_is_read_typed_through_a_declared_dependency() -> None:
    directory = Directory("apis")
    directory.add(GREETER.bind(English()), by="greeter", declared=GREETERS)
    assert directory.view(frozenset({"greeter"})).get(GREETER).greet() == "hello"


def test_an_undeclared_dependency_is_refused_even_when_bound() -> None:
    directory = Directory("apis")
    directory.add(GREETER.bind(English()), by="greeter", declared=GREETERS)
    with pytest.raises(UndeclaredDependencyError, match="greeter"):
        directory.view(frozenset()).get(GREETER)


def test_a_missing_or_doubled_binding_is_a_defect() -> None:
    directory = Directory("apis")
    with pytest.raises(BindingMissingError, match="apis 'greeter'"):
        directory.view(frozenset({"greeter"})).get(GREETER)
    directory.add(GREETER.bind(English()), by="greeter", declared=GREETERS)
    with pytest.raises(DuplicateBindingError, match="apis 'greeter'"):
        directory.add(GREETER.bind(English()), by="greeter", declared=GREETERS)


def test_a_value_of_the_wrong_type_under_a_name_is_a_defect() -> None:
    directory = Directory("apis")
    directory.add(Key[object]("greeter", anything).bind(42), by="greeter", declared=GREETERS)
    with pytest.raises(BindingTypeError, match="greeter"):
        directory.view(frozenset({"greeter"})).get(GREETER)


def test_a_key_names_what_it_binds() -> None:
    binding = GREETER.bind(English())
    assert binding.name == GREETER.name == "greeter"
    assert isinstance(binding.value, English)


def test_a_binder_binds_only_what_it_declares() -> None:
    directory = Directory("ports")
    with pytest.raises(UndeclaredBindingError, match="ports 'greeter' is bound by 'invoicing'"):
        directory.add(GREETER.bind(English()), by="invoicing", declared=frozenset({"other"}))
    with pytest.raises(BindingMissingError):
        directory.view(GREETERS).get(GREETER)


class French:
    def greet(self) -> str:
        return "bonjour"


class German:
    def greet(self) -> str:
        return "hallo"


def greetings(chain: ChainDirectory) -> list[str]:
    return [greeter.greet() for greeter in chain.view(GREETERS).all(GREETER)]


def test_implementations_of_one_point_are_chained_by_order_then_by_implementer() -> None:
    chain = ChainDirectory("extensions")
    chain.add(GREETER.bind(French()), by="zeta", declared=GREETERS, order=100)
    chain.add(GREETER.bind(English()), by="beta", declared=GREETERS, order=100)
    chain.add(GREETER.bind(German()), by="omega", declared=GREETERS, order=5)
    chain.add(GREETER.bind(English()), by="alpha", declared=GREETERS, order=200)
    assert greetings(chain) == ["hallo", "hello", "bonjour", "hello"]  # 5, then 100 (beta, zeta), then 200


def test_an_implementer_gives_one_implementation_per_point() -> None:
    chain = ChainDirectory("extensions")
    chain.add(GREETER.bind(English()), by="beta", declared=GREETERS, order=100)
    with pytest.raises(DuplicateBindingError, match="extensions 'greeter'"):
        chain.add(GREETER.bind(French()), by="beta", declared=GREETERS, order=50)


def test_a_chain_is_bound_only_by_those_declaring_it_and_read_only_by_those_declaring_it() -> None:
    chain = ChainDirectory("extensions")
    with pytest.raises(UndeclaredBindingError, match="extensions 'greeter' is bound by 'fiscal'"):
        chain.add(GREETER.bind(English()), by="fiscal", declared=frozenset(), order=100)
    chain.add(GREETER.bind(English()), by="fiscal", declared=GREETERS, order=100)
    with pytest.raises(UndeclaredDependencyError, match="greeter"):
        chain.view(frozenset()).all(GREETER)


def test_a_chain_with_no_implementation_is_empty_unless_required_and_a_wrong_type_in_it_is_a_defect() -> None:
    chain = ChainDirectory("extensions")
    assert chain.view(GREETERS).all(GREETER) == ()
    with pytest.raises(BindingMissingError, match="extensions 'greeter'"):
        chain.require("greeter")
    chain.add(Key[object]("greeter", anything).bind(42), by="fiscal", declared=GREETERS, order=100)
    with pytest.raises(BindingTypeError, match="greeter"):
        chain.view(GREETERS).all(GREETER)
    chain.require("greeter")  # implemented, though wrongly: the start reads its type later
