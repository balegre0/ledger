"""The fake DGII as one ASGI application, for tests (in process, over ``httpx.ASGITransport``) and by hand.

Every request is routed by its lower-cased path, as DGII's services ignore case (DT-EMI): the first segment is
the environment (``testecf``, ``certecf``, ``ecf``), the rest names the service. One application answers for
both of DGII's hosts (``ecf.dgii.gov.do`` and ``fc.dgii.gov.do``): their paths never collide, and the ``Host``
each request was sent to is recorded.
"""

from datetime import UTC, datetime
from http import HTTPStatus
from typing import Final, final
lazy from collections.abc import Callable

from starlette.applications import Starlette
from starlette.responses import Response
from starlette.routing import Route
lazy from starlette.requests import Request

from ledger_country_do_testing.fake_dgii.emitter import EmitterServices
from ledger_country_do_testing.fake_dgii.state import FakeState
lazy from ledger_country_do_testing.fake_dgii.emitter import Handler
lazy from ledger_country_do_testing.fake_dgii.state import Received, Script, SignatureCheck
lazy from ledger_country_do_testing.pki import Issued

ENVIRONMENTS: Final = frozenset({"testecf", "certecf", "ecf"})
DEFAULT_PUBLIC_URL: Final = "http://127.0.0.1:8471"


def _utc_now() -> datetime:
    return datetime.now(UTC)


@final
class FakeDgii:
    """The fake (see the module); ``app`` is the ASGI application, ``state`` what tests read and set."""

    __slots__ = ("_handlers", "app", "state")

    def __init__(
        self,
        *,
        check: SignatureCheck,
        receiver: Issued,
        clock: Callable[[], datetime] | None = None,
        public_url: str = DEFAULT_PUBLIC_URL,
    ) -> None:
        self.state: FakeState = FakeState(
            check=check, receiver=receiver, clock=clock or _utc_now, public_url=public_url
        )
        self._handlers: dict[tuple[str, str], Handler] = EmitterServices(self.state).handlers()
        self.app: Starlette = Starlette(
            routes=[Route("/{path:path}", self._dispatch, methods=["GET", "POST"])]
        )

    @property
    def script(self) -> Script:
        """What the fake answers."""
        return self.state.script

    @property
    def received(self) -> list[Received]:
        """Every document received, in order."""
        return self.state.received

    async def _dispatch(self, request: Request) -> Response:
        environment, _, service = request.url.path.lower().strip("/").partition("/")
        handler = self._handlers.get((request.method, service))
        if environment not in ENVIRONMENTS or handler is None:
            return Response(status_code=HTTPStatus.NOT_FOUND)
        outage = self.state.script.outage
        if outage:
            self.state.script.outage = outage - 1 if outage > 0 else outage
            return Response(status_code=HTTPStatus.SERVICE_UNAVAILABLE)
        return await handler(request, environment)
