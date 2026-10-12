"""Ordered ownership of everything that must be released at shutdown."""

from contextlib import AsyncExitStack
from typing import Self, final
lazy from collections.abc import Awaitable, Callable
lazy from contextlib import AbstractAsyncContextManager
lazy from types import TracebackType


@final
class Lifecycle:
    """Resources acquired while starting, released in reverse order exactly once.

    Release happens even when startup fails halfway, and a failing release does not stop the others
    (``AsyncExitStack`` semantics).
    """

    __slots__ = ("_stack",)

    def __init__(self) -> None:
        self._stack = AsyncExitStack()

    async def enter[T](self, resource: AbstractAsyncContextManager[T]) -> T:
        """Enter ``resource`` now; exit it at shutdown, before anything entered earlier."""
        return await self._stack.enter_async_context(resource)

    def on_close(self, callback: Callable[[], Awaitable[object]]) -> None:
        """Await ``callback`` at shutdown, before anything registered earlier."""
        self._stack.push_async_callback(callback)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        return await self._stack.__aexit__(exc_type, exc, traceback)
