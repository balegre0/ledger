"""The fake DGII as one ASGI application, for tests (in process, over ``httpx.ASGITransport``) and by hand.

Every request is routed by its lower-cased path, as DGII's services ignore case (DT-EMI): the first segment is
the environment (``testecf``, ``certecf``, ``ecf``), the rest names the service. One application answers for
both of DGII's hosts (``ecf.dgii.gov.do`` and ``fc.dgii.gov.do``): their paths never collide, and the ``Host``
each request was sent to is recorded.
"""

import os
from datetime import UTC, datetime
from http import HTTPStatus
from typing import Final, final
lazy from collections.abc import Callable

from starlette.applications import Starlette
from starlette.responses import Response
from starlette.routing import Route
lazy from starlette.requests import Request

from ledger_country_do_testing.fake_dgii.emitter import EmitterServices, UploadTooLargeError
from ledger_country_do_testing.fake_dgii.receiver import ReceiverServices
from ledger_country_do_testing.fake_dgii.state import FakeState, dgii_check
from ledger_country_do_testing.pki import Authority, organization
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
        self._handlers: dict[tuple[str, str], Handler] = {
            **EmitterServices(self.state).handlers(),
            **ReceiverServices(self.state).handlers(),
        }
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
        try:
            return await handler(request, environment)
        except UploadTooLargeError:
            return Response(status_code=HTTPStatus.REQUEST_ENTITY_TOO_LARGE)


def create_app() -> Starlette:
    """Granian's factory for a fake run by hand (``ledger-fake-dgii``).

    Every document's schema and signature are checked as the tests' fake checks them (``dgii_check``: our
    verifier, strict, and signxml), by any signer (the owner's real certificate is unknown to the fake); the
    simulator signs its ARECFs with a certificate made at start. ``LEDGER_FAKE_DGII_PUBLIC_URL`` is where
    clients reach it (default ``DEFAULT_PUBLIC_URL``).
    """
    public_url = os.environ.get("LEDGER_FAKE_DGII_PUBLIC_URL", DEFAULT_PUBLIC_URL)
    receiver = Authority.root("LEDGER FAKE DGII CA").issue(
        organization("131880738", name="EMISOR RECEPTOR DGII")
    )
    return FakeDgii(check=dgii_check(trusted=None), receiver=receiver, public_url=public_url).app
