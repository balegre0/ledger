"""The size limit of a request body (§10): over it, a request is refused 413 before the app buffers it.

Without a limit a client can stream a body of any size into an app that buffers it before it knows who sent
it. ``BodyLimit`` answers for the app. A request that declares more than the limit is refused without the app
running at all; one that declares less, or nothing (chunked), is refused as soon as the bytes read pass the
limit, and whatever the route then tries to send is dropped (a route answers only once it has read its body).
FastAPI turns an error raised while a body is read into a 400, so the 413 is written here and not raised.

A route that takes more than the app's limit (a certificate upload, 2 MiB at most) says so as a dependency,
``dependencies=[Depends(larger_body(MAX_UPLOAD_BYTES))]``: the app reads it from its routes when it is made
(``route_limits``) and holds each request of that route to that limit instead.
"""

from typing import Final, final
from weakref import WeakKeyDictionary
lazy from collections.abc import Callable, Coroutine, Sequence

from fastapi.routing import APIRoute, iter_route_contexts
from starlette.datastructures import Headers
from starlette.routing import Match
lazy from starlette.routing import BaseRoute

from ledger_platform.config.options import InvalidOptionError
from ledger_platform.http.problems import PayloadTooLargeError, refusal
lazy from ledger_platform.http.metrics import AsgiApp, AsgiMessage, AsgiReceive, AsgiScope, AsgiSend

MAX_BODY_BYTES: Final = 1024 * 1024
"""A JSON body of the public API: 1 MiB (§10). A route that takes more (a certificate upload) is set apart."""
MAX_UPLOAD_BYTES: Final = 2 * 1024 * 1024
"""The most any route takes (§10): a certificate upload."""
type LargerBody = Callable[[], Coroutine[object, object, None]]
"""The dependency ``larger_body`` makes: it does nothing when called; the app reads the limit it holds."""
_LARGER: Final[WeakKeyDictionary[LargerBody, int]] = WeakKeyDictionary()
"""Every dependency ``larger_body`` made, and its limit."""


def larger_body(limit: int = MAX_UPLOAD_BYTES) -> LargerBody:
    """A dependency that lets its route take a body of up to ``limit`` bytes, ``MAX_UPLOAD_BYTES`` at most.

    The limit is the route's alone, in place of the app's, whichever is larger: a route that declares it is
    held to it, every other route to the app's.
    """
    if not 0 < limit <= MAX_UPLOAD_BYTES:
        raise InvalidOptionError(
            component="http", option="larger_body", requirement=f"1 to {MAX_UPLOAD_BYTES} bytes", value=limit
        )

    async def larger() -> None:
        """Nothing: the limit is the body limit's to apply, before the route runs."""

    _LARGER[larger] = limit
    return larger


def route_limits(routes: Sequence[BaseRoute]) -> tuple[tuple[BaseRoute, int | None], ...]:
    """Every route of ``routes`` in the order the router tries them, each with the limit it declares, if any.

    ``routes`` are an app's, its included routers' too. A request is held to the limit of the route the router
    dispatches it to: the first that matches it fully, declared before any other that would match it too
    (``/x/special`` before ``/x/{id}``).
    """
    found: list[tuple[BaseRoute, int | None]] = []
    for context in iter_route_contexts(routes):
        route = context.original_route
        limits = [_LARGER[call] for call in _own_calls(route)] if isinstance(route, APIRoute) else []
        found.append((route, max(limits) if limits else None))
    return tuple(found)


def _own_calls(route: APIRoute) -> tuple[LargerBody, ...]:
    """The dependencies a route declares itself that ``larger_body`` made."""
    return tuple(
        dependency.call
        for dependency in route.dependant.dependencies
        if callable(dependency.call) and dependency.call in _LARGER
    )


def _declared_length(scope: AsgiScope) -> int | None:
    """The ``Content-Length`` a request declares, if it declares a whole number."""
    declared = Headers(scope=scope).get("content-length")
    return int(declared) if declared is not None and declared.isascii() and declared.isdigit() else None


@final
class BodyLimit:
    """Refuses a request body over ``limit`` bytes (see the module); an ASGI middleware, like ``Measure``."""

    __slots__ = ("_app", "_limit", "_routes")

    def __init__(
        self,
        app: AsgiApp,
        *,
        limit: int = MAX_BODY_BYTES,
        routes: Sequence[tuple[BaseRoute, int | None]] = (),
    ) -> None:
        self._app = app
        self._limit = limit
        self._routes = tuple(routes)

    def _limit_of(self, scope: AsgiScope) -> int:
        """The limit of the route the request is dispatched to, if it declares one; else the app's."""
        for route, limit in self._routes:
            if route.matches(scope)[0] is Match.FULL:
                return self._limit if limit is None else limit
        return self._limit

    async def __call__(self, scope: AsgiScope, receive: AsgiReceive, send: AsgiSend) -> None:
        """Run the request, or answer 413 when its body is over the limit."""
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        limit, declared = self._limit_of(scope), _declared_length(scope)
        if declared is not None and declared > limit:
            await refusal(PayloadTooLargeError(limit))(scope, receive, send)
            return
        read, answered = 0, False

        async def limited() -> AsgiMessage:
            nonlocal read, answered
            message = await receive()
            body = message.get("body")
            read += len(body) if isinstance(body, bytes) else 0  # a disconnect carries none
            if read > limit:
                answered = True
                await refusal(PayloadTooLargeError(limit))(scope, receive, send)
                raise PayloadTooLargeError(limit)
            return message

        async def guarded(message: AsgiMessage) -> None:
            if not answered:
                await send(message)

        await self._app(scope, limited, guarded)
