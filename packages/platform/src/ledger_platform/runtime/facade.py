"""The facade guard (§3.4, rule 13): no cross-module call while a unit of work is open on the task."""

import functools
from typing import ClassVar
lazy from collections.abc import Callable, Coroutine

from ledger_kernel.errors import LedgerError
from ledger_platform.data.unit_of_work import open_unit


class UnitOpenError(LedgerError):
    """A facade was called while the caller held a transaction: a defect (it could hold locks across modules).

    A unit or a purge open on the task counts; a closed one a child task inherited does not.
    """

    code: ClassVar[str] = "unit_open"

    def __init__(self, name: str) -> None:
        super().__init__(f"{name} was called while a unit of work is open on the task (rule 13)")


def facade[**P, R](
    method: Callable[P, Coroutine[object, object, R]],
) -> Callable[P, Coroutine[object, object, R]]:
    """Refuse calls to ``method`` made while a unit of work (or a purge) is open on the calling task."""

    @functools.wraps(method)
    async def guarded(*args: P.args, **kwargs: P.kwargs) -> R:
        if open_unit() is not None:
            raise UnitOpenError(method.__qualname__)
        return await method(*args, **kwargs)

    return guarded
