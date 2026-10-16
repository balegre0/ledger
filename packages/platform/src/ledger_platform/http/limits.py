"""The size limit of a request body (§10): over it, a request is refused 413 before the app buffers it.

Without a limit a client can stream a body of any size into an app that buffers it before it knows who sent
it. ``BodyLimit`` answers for the app. A request that declares more than the limit is refused without the app
running at all; one that declares less, or nothing (chunked), is refused as soon as the bytes read pass the
limit, and whatever the route then tries to send is dropped (a route answers only once it has read its body).
FastAPI turns an error raised while a body is read into a 400, so the 413 is written here and not raised.
"""

from typing import Final, final

from starlette.datastructures import Headers

from ledger_platform.http.problems import PayloadTooLargeError, refusal
lazy from ledger_platform.http.metrics import AsgiApp, AsgiMessage, AsgiReceive, AsgiScope, AsgiSend

MAX_BODY_BYTES: Final = 1024 * 1024
"""A JSON body of the public API: 1 MiB (§10). A route that takes more (a certificate upload) is set apart."""


def _declared_length(scope: AsgiScope) -> int | None:
    """The ``Content-Length`` a request declares, if it declares a whole number."""
    declared = Headers(scope=scope).get("content-length")
    return int(declared) if declared is not None and declared.isascii() and declared.isdigit() else None


@final
class BodyLimit:
    """Refuses a request body over ``limit`` bytes (see the module); an ASGI middleware, like ``Measure``."""

    __slots__ = ("_app", "_limit")

    def __init__(self, app: AsgiApp, *, limit: int = MAX_BODY_BYTES) -> None:
        self._app = app
        self._limit = limit

    async def __call__(self, scope: AsgiScope, receive: AsgiReceive, send: AsgiSend) -> None:
        """Run the request, or answer 413 when its body is over the limit."""
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        limit, declared = self._limit, _declared_length(scope)
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
