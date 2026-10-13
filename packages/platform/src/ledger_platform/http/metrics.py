"""How long every HTTP request takes, by method, route template and status.

Each request is measured under the full template of the route that took it, as the app mounted it; a request
no API route took is ``unmatched``, and a method a client made up is ``other``: every label is bounded by the
code, never by a client.
"""

from dataclasses import dataclass, field
from http import HTTPMethod, HTTPStatus
from time import perf_counter
from typing import ClassVar, Final, Self, final
lazy from collections.abc import Awaitable, Callable, Mapping, MutableMapping

import structlog
from fastapi.routing import APIRoute, iter_route_contexts
lazy from fastapi import FastAPI
lazy from prometheus_client import Histogram

from ledger_kernel.errors import LedgerError

UNMATCHED: Final = "unmatched"
ROUTE_KEY: Final = "ledger.route"
"""Where in the scope of a request ``Measure`` leaves the template of its route."""
LABELS_KEY: Final = "ledger.route_labels"
"""Where in the scope of a request ``Measure`` leaves the app's ``RouteLabels``, for ``full_template``."""
_UNREGISTERED: Final = "unregistered"
_OTHER_METHOD: Final = "other"
_METHODS: Final = frozenset(method.value for method in HTTPMethod)
_FAILED: Final = HTTPStatus.INTERNAL_SERVER_ERROR.value
_logger = structlog.stdlib.get_logger(__name__)

# The ASGI protocol, as far as the chassis's middlewares need it. Starlette's own aliases hide the values
# behind ``Any``.
type AsgiScope = MutableMapping[str, object]
type AsgiMessage = MutableMapping[str, object]
type AsgiReceive = Callable[[], Awaitable[AsgiMessage]]
type AsgiSend = Callable[[AsgiMessage], Awaitable[None]]
type AsgiApp = Callable[[AsgiScope, AsgiReceive, AsgiSend], Awaitable[None]]


class AmbiguousRouteError(LedgerError):
    """One route reachable under two templates: no single metric label can name it."""

    code: ClassVar[str] = "ambiguous_route"

    def __init__(self, first: str, second: str) -> None:
        super().__init__(f"a route is declared as {first!r} and as {second!r}")


def declared_route(scope: Mapping[str, object]) -> str:
    """The template of the route that took the request as its router declares it; else ``unmatched``.

    Without the prefix of a router that mounted it: an app labels its routes with ``RouteLabels``.
    """
    route = scope.get("route")
    return route.path_format if isinstance(route, APIRoute) else UNMATCHED


@final
@dataclass(slots=True)
class RouteLabels:
    """The template each route is measured and logged under: the full path a client reaches it by.

    FastAPI reports the route a request took as its own router declared it, without the prefix of the
    ``include_router`` that mounted it, so two children ``/{id}`` under ``/accounts`` and ``/entries`` would
    share one label. The full template of every route comes from the public ``iter_route_contexts``, once,
    when the routes are final. A route object mounted under two templates has no single label: refused.
    """

    templates: Mapping[int, str]
    """By ``id`` of the route object: routes are not hashable, and the app keeps them alive. Never edited."""
    reported: set[int] = field(default_factory=set[int])
    """The routes found missing from ``templates``, so that each is said once. The only thing that changes."""

    @classmethod
    def of(cls, app: FastAPI) -> Self:
        """The labels of ``app``'s final routes; ``AmbiguousRouteError`` for a route under two templates."""
        templates: dict[int, str] = {}
        for context in iter_route_contexts(app.routes):
            route = context.original_route
            if not isinstance(route, APIRoute):
                continue  # FastAPI's own documentation routes: no request reaches them as a matched route
            template = context.path_format or route.path_format
            first = templates.setdefault(id(route), template)
            if first != template:
                raise AmbiguousRouteError(first, template)
        return cls(templates)

    def of_scope(self, scope: Mapping[str, object]) -> str:
        """The template of the route that took the request, bounded by the code, never by a client.

        A route that was not there when the app was built (FastAPI mounts a router by reference, so routes
        added to it later reach the app) is ``unregistered``, and said so once: its own path would be a
        wrong label whenever a prefix is missing. Anything no API route took (an unknown path, the
        documentation FastAPI serves itself) is ``unmatched``.
        """
        route = scope.get("route")
        if not isinstance(route, APIRoute):
            return UNMATCHED
        template = self.templates.get(id(route))
        if template is None:
            self._report_once(route)
            return _UNREGISTERED
        return template

    def _report_once(self, route: APIRoute) -> None:
        if id(route) not in self.reported:
            self.reported.add(id(route))
            _logger.warning("http.route_unregistered", path=route.path)


def _method_label(method: object) -> str:
    """The request method, or one shared label for any a client made up: each distinct value is a series."""
    return method if isinstance(method, str) and method in _METHODS else _OTHER_METHOD


@final
class Measure:
    """Times every HTTP request into a histogram labelled by method, route template and status.

    A plain ASGI middleware rather than ``@app.middleware("http")``: that decorator runs the application in a
    second task, so context variables an endpoint sets do not reach the middleware, and relays every response
    body through a stream.
    """

    __slots__ = ("_app", "_duration", "_labels")

    def __init__(self, app: AsgiApp, duration: Histogram, labels: RouteLabels) -> None:
        self._app = app
        self._duration = duration
        self._labels = labels

    async def __call__(self, scope: AsgiScope, receive: AsgiReceive, send: AsgiSend) -> None:
        """Run the request, and measure it however it ends."""
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        scope[LABELS_KEY] = self._labels
        started = perf_counter()
        status = _FAILED  # what an application that returns without answering amounts to

        async def answer(message: AsgiMessage) -> None:
            nonlocal status
            if message["type"] == "http.response.start" and isinstance(code := message["status"], int):
                status = code
            await send(message)

        try:
            await self._app(scope, receive, answer)
        except Exception:
            self._observe(scope, started, _FAILED)  # failed, even if a response had begun
            raise
        self._observe(scope, started, status)

    def _observe(self, scope: AsgiScope, started: float, status: int) -> None:
        """Measure the request, and leave its route in the scope (``ROUTE_KEY``) for ``_Contained``.

        The router names the route while it dispatches, so it is known only after the application ran.
        """
        route = scope[ROUTE_KEY] = self._labels.of_scope(scope)
        self._duration.labels(_method_label(scope["method"]), route, str(status)).observe(
            perf_counter() - started
        )


def full_template(scope: Mapping[str, object]) -> str | None:
    """The full template of the route that took the request, as the app mounted it; ``None`` if unknown.

    From the app's ``RouteLabels``, which ``Measure`` leaves in the scope (an app ``create_app`` made).
    Unknown, so ``None``, without them (the template a route declares lacks the prefix it was mounted
    under), for a request no route took, and for a route added after the app was built: never guessed.
    """
    labels = scope.get(LABELS_KEY)
    if not isinstance(labels, RouteLabels):
        return None
    template = labels.of_scope(scope)
    return None if template in {UNMATCHED, _UNREGISTERED} else template
