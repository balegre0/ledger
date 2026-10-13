"""The facade guard (§3.4, rule 13): no cross-module call while a unit of work is open on the task.

``@facade`` guards a method; ``require_facades`` checks, when an implementation is bound for other modules to
call, that every public coroutine method it has is guarded: an entry point that was forgotten fails the
assembly, not a call that happens to hold locks.
"""

import functools
import inspect
import weakref
from types import CoroutineType
from typing import ClassVar, Final, Never
lazy from collections.abc import Callable, Coroutine

from ledger_kernel.errors import LedgerError
from ledger_platform.data.unit_of_work import open_unit

_GUARDED: Final[weakref.WeakSet[object]] = weakref.WeakSet()
"""Every function ``@facade`` made."""


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


def facade[**P, R](
    method: Callable[P, Coroutine[object, object, R]],
) -> Callable[P, CoroutineType[object, object, R]]:
    """Refuse calls to ``method`` made while a unit of work (or a purge) is open on the calling task.

    The check runs when the method is called, in the caller's context, and the call returns the method's own
    coroutine. The guard is a plain function marked as a coroutine function, typed to return ``CoroutineType``
    (what an ``async def`` returns), so that a guarded method still satisfies the module's API Protocol for
    both type checkers. Only an ``async def`` can be a facade (``NotCoroutineFunctionError``).
    """
    if not inspect.iscoroutinefunction(method):
        _refuse(method.__qualname__)

    @functools.wraps(method)
    def guarded(*args: P.args, **kwargs: P.kwargs) -> CoroutineType[object, object, R]:
        if open_unit() is not None:
            raise UnitOpenError(method.__qualname__)
        coroutine = method(*args, **kwargs)
        return coroutine if isinstance(coroutine, CoroutineType) else _refuse(method.__qualname__)

    _GUARDED.add(guarded)
    return inspect.markcoroutinefunction(guarded)


def is_facade(function: object) -> bool:
    """Whether ``function`` was made by ``@facade``."""
    return function in _GUARDED


def require_facades(implementation: object) -> None:
    """Refuse ``implementation`` if a public coroutine method of its class is not a ``@facade``.

    Read on the class, so no property runs: methods and static methods are functions there, class methods are
    bound to the class (their function is checked).
    """
    cls = type(implementation)
    members: list[tuple[str, object]] = inspect.getmembers(cls)
    for name, member in members:
        function = member.__func__ if inspect.ismethod(member) else member
        if not name.startswith("_") and inspect.iscoroutinefunction(function) and not is_facade(function):
            raise UnguardedFacadeError(cls.__qualname__, name)
