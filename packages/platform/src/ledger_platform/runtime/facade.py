"""The facade guard (§3.4, rule 13): no cross-module call while a unit of work is open on the task.

``@facade`` guards a method; ``require_facades`` checks, when an implementation is bound for other packages to
call (an API, an extension point implementation, a port), that every public coroutine function it has, on its
class or held by the instance, is guarded, and that it has no public async generator (which cannot be): an
entry point that was forgotten fails the assembly, not a call that happens to hold locks. A plain method that
returns an awaitable cannot be told from any other plain method without calling it: an API's entry points are
``async def``.
"""

import functools
import inspect
import weakref
from functools import partial
from types import CoroutineType
from typing import ClassVar, Final, Never
lazy from collections.abc import Callable, Coroutine, Iterator

from pydantic import TypeAdapter

from ledger_kernel.errors import LedgerError
from ledger_platform.data.unit_of_work import open_unit

_GUARDED: Final[weakref.WeakSet[object]] = weakref.WeakSet()
"""Every function ``@facade`` made."""
_ANYTHING: Final = TypeAdapter(object)
_INSTANCE_DICT: Final = TypeAdapter(dict[str, object])
_SLOTS: Final = TypeAdapter(str | tuple[str, ...] | list[str] | dict[str, object])


class UnitOpenError(LedgerError):
    """A facade was called while the caller held a transaction: a defect (it could hold locks across modules).

    A unit or a purge open on the task counts; a closed one a child task inherited does not.
    """

    code: ClassVar[str] = "unit_open"

    def __init__(self, name: str) -> None:
        super().__init__(f"{name} was called while a unit of work is open on the task (rule 13)")


class NotCoroutineFunctionError(LedgerError):
    """``@facade`` on something other than an ``async def``: a defect of its module."""

    code: ClassVar[str] = "facade_not_async"

    def __init__(self, name: str) -> None:
        super().__init__(f"{name} is not an async def: only one can be a @facade")


class UnguardedFacadeError(LedgerError):
    """A public coroutine method of a bound implementation is not a ``@facade``: a defect of its module."""

    code: ClassVar[str] = "facade_unguarded"

    def __init__(self, owner: str, name: str) -> None:
        super().__init__(f"{owner}.{name} is called by other modules and is not a @facade")


def _refuse(name: str) -> Never:
    raise NotCoroutineFunctionError(name)


async def _checked[R](name: str, coroutine: Coroutine[object, object, R]) -> R:
    """``coroutine``, unless a unit is open on the task that runs it (it was made outside one, run inside)."""
    if open_unit() is not None:
        coroutine.close()
        raise UnitOpenError(name)
    return await coroutine


def _started[R](
    start: Callable[[], Coroutine[object, object, R]], name: str
) -> CoroutineType[object, object, R]:
    """What ``start`` makes, typed as what an ``async def`` returns (``_checked`` is one)."""
    coroutine = start()
    return coroutine if isinstance(coroutine, CoroutineType) else _refuse(name)


def facade[**P, R](
    method: Callable[P, Coroutine[object, object, R]],
) -> Callable[P, CoroutineType[object, object, R]]:
    """Refuse calls to ``method`` made while a unit of work (or a purge) is open on the calling task.

    The check runs when the method is called, in the caller's context, and again when the coroutine it returns
    first runs, in the context of the task that awaits it: a coroutine made outside a unit and awaited inside
    one is refused too. The guard is a plain function marked as a coroutine function, typed to return
    ``CoroutineType`` (what an ``async def`` returns), so that a guarded method still satisfies the module's
    API Protocol for both type checkers. Only an ``async def`` can be a facade
    (``NotCoroutineFunctionError``).
    """
    if not inspect.iscoroutinefunction(method):
        _refuse(method.__qualname__)
    name = method.__qualname__

    @functools.wraps(method)
    def guarded(*args: P.args, **kwargs: P.kwargs) -> CoroutineType[object, object, R]:
        if open_unit() is not None:
            raise UnitOpenError(name)
        return _started(partial(_checked, name, method(*args, **kwargs)), name)

    _GUARDED.add(guarded)
    return inspect.markcoroutinefunction(guarded)


def is_facade(function: object) -> bool:
    """Whether ``function`` was made by ``@facade``."""
    return function in _GUARDED


def _held(implementation: object) -> Iterator[tuple[str, object]]:
    """What the instance itself holds, in its ``__dict__`` and its slots: data, so reading it runs nothing."""
    yield from _INSTANCE_DICT.validate_python(getattr(implementation, "__dict__", {})).items()
    for owner in type(implementation).__mro__:
        declared = _SLOTS.validate_python(owner.__dict__.get("__slots__", ()))
        for slot in (declared,) if isinstance(declared, str) else declared:
            yield slot, _ANYTHING.validate_python(getattr(implementation, slot, None))


def require_facades(implementation: object) -> None:
    """Refuse ``implementation`` if a public coroutine function it has is not a ``@facade`` (see the module).

    Methods are read on the class, so no property runs: methods and static methods are functions there, class
    methods are bound to the class (their function is checked). What the instance holds is read from its
    ``__dict__`` and its slots.
    """
    cls = type(implementation)
    members: list[tuple[str, object]] = inspect.getmembers(cls)
    for name, member in (*members, *_held(implementation)):
        function = member.__func__ if inspect.ismethod(member) else member
        unguarded = inspect.iscoroutinefunction(function) and not is_facade(function)
        if not name.startswith("_") and (unguarded or inspect.isasyncgenfunction(function)):
            raise UnguardedFacadeError(cls.__qualname__, name)
