"""Typed directories (§3.10; Decision 12): modules' APIs, extension point implementations, channels' ports.

A ``Key[T]`` names one bound thing and knows how to recognize it (a ``TypeIs`` function, usually
``isinstance`` on a runtime-checkable Protocol): binding checks the type statically, reading checks it at
runtime, and no cast is ever needed.

Authority runs both ways. A package binds only the names its manifest declares (``add(..., declared=)``):
a module's API under its own name, extension point implementations under ``<declaring module>.<Protocol>``,
ports under the port's name. A package reads only the names it declares (``view(allowed)``): reading anything
else fails at that read, whenever it happens (at assembly, or at call time for what a module reads later).

A name is bound once: one implementation per extension point, one provider per port
(``DuplicateBindingError``). A point that needs several implementations (a chain of policies) will be a
directory of its own, keyed by implementer, when one needs it.
"""

from dataclasses import dataclass
from typing import ClassVar, TypeIs, final
lazy from collections.abc import Callable

from ledger_kernel.errors import LedgerError


class UndeclaredDependencyError(LedgerError):
    """A package read a binding its manifest does not declare: a defect of the package."""

    code: ClassVar[str] = "dependency_undeclared"

    def __init__(self, label: str, name: str) -> None:
        super().__init__(f"{label} {name!r} is read by a package that does not declare it")


class UndeclaredBindingError(LedgerError):
    """A package bound a name its manifest does not declare: a defect of the package."""

    code: ClassVar[str] = "binding_undeclared"

    def __init__(self, label: str, name: str, binder: str) -> None:
        super().__init__(f"{label} {name!r} is bound by {binder!r}, which does not declare it")


class BindingMissingError(LedgerError):
    """A declared binding that nothing provides: a defect of the app's assembly."""

    code: ClassVar[str] = "binding_missing"

    def __init__(self, label: str, name: str) -> None:
        super().__init__(f"{label} {name!r} is declared but nothing provides it")


class BindingTypeError(LedgerError):
    """What is bound under a name is not what its key names: a defect of the provider."""

    code: ClassVar[str] = "binding_type"

    def __init__(self, name: str) -> None:
        super().__init__(f"what is bound as {name!r} is not of the type its key names")


class DuplicateBindingError(LedgerError):
    """Two providers of one name: a defect of the app's assembly."""

    code: ClassVar[str] = "binding_duplicate"

    def __init__(self, label: str, name: str) -> None:
        super().__init__(f"{label} {name!r} is provided twice")


@final
@dataclass(frozen=True, slots=True)
class Binding:
    """A value bound under a key's name."""

    name: str
    value: object


@final
class Key[T]:
    """The typed name of a bound value."""

    __slots__ = ("_check", "name")

    def __init__(self, name: str, check: Callable[[object], TypeIs[T]]) -> None:
        self.name = name
        self._check = check

    def bind(self, value: T) -> Binding:
        """``value`` under this key (statically checked)."""
        return Binding(self.name, value)

    def take(self, value: object) -> T:
        """``value`` as ``T`` (checked at runtime)."""
        if self._check(value):
            return value
        raise BindingTypeError(self.name)


@final
class DirectoryView:
    """What one package may read of a directory."""

    __slots__ = ("_allowed", "_directory")

    def __init__(self, directory: Directory, allowed: frozenset[str]) -> None:
        self._directory = directory
        self._allowed = allowed

    def get[T](self, key: Key[T]) -> T:
        """The value bound under ``key``, if the reader declared it."""
        if key.name not in self._allowed:
            raise UndeclaredDependencyError(self._directory.label, key.name)
        return key.take(self._directory.value_of(key.name))


@final
class Directory:
    """Bindings by name, filled at assembly."""

    __slots__ = ("_values", "label")

    def __init__(self, label: str) -> None:
        self.label = label
        self._values: dict[str, object] = {}

    def add(self, binding: Binding, *, by: str, declared: frozenset[str]) -> None:
        """Bind for the package ``by``, which may bind only ``declared``; a name is bound once."""
        if binding.name not in declared:
            raise UndeclaredBindingError(self.label, binding.name, by)
        if binding.name in self._values:
            raise DuplicateBindingError(self.label, binding.name)
        self._values[binding.name] = binding.value

    def value_of(self, name: str) -> object:
        """The value bound under ``name`` (``BindingMissingError`` if none)."""
        if name not in self._values:
            raise BindingMissingError(self.label, name)
        return self._values[name]

    def view(self, allowed: frozenset[str]) -> DirectoryView:
        """What a package that declared ``allowed`` may read."""
        return DirectoryView(self, allowed)


type ApiDirectory = DirectoryView
"""Other modules' APIs, as a module that declared them may read them."""
type ExtensionDirectory = DirectoryView
"""Implementations of the extension points a module declares, bound by the modules that implement them."""
type Ports = DirectoryView
"""What modules provide to channels (a signer, a company directory), as a channel reads it."""
