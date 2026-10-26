"""The fake DGII as one ASGI application, for tests (in process, over ``httpx.ASGITransport``) and by hand.

Every request is routed by its lower-cased path, as DGII's services ignore case (DT-EMI): the first segment is
the environment (``testecf``, ``certecf``, ``ecf``), the rest names the service. One application answers for
both of DGII's hosts (``ecf.dgii.gov.do`` and ``fc.dgii.gov.do``): their paths never collide, and the ``Host``
each request was sent to is recorded.
"""

import os
from datetime import UTC, datetime
from http import HTTPStatus
from pathlib import Path
from typing import ClassVar, Final, final
lazy from collections.abc import Callable

from cryptography import x509
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from starlette.applications import Starlette
from starlette.responses import Response
from starlette.routing import Route
lazy from starlette.requests import Request

from ledger_country_do_testing.fake_dgii.emitter import EmitterServices, UploadTooLargeError
from ledger_country_do_testing.fake_dgii.receiver import ReceiverServices
from ledger_country_do_testing.fake_dgii.state import FakeState, dgii_check
from ledger_country_do_testing.pki import organization
from ledger_kernel.errors import InvalidInputError
from ledger_testing.pki import Authority, Issued
lazy from ledger_country_do_testing.fake_dgii.emitter import Handler
lazy from ledger_country_do_testing.fake_dgii.state import Received, Script, SignatureCheck

ENVIRONMENTS: Final = frozenset({"testecf", "certecf", "ecf"})
DEFAULT_PUBLIC_URL: Final = "http://127.0.0.1:8471"
SIMULATOR_ROOT: Final = "LEDGER FAKE DGII CA"
"""The root the simulator's certificate is issued under when none is given (made at start, then lost)."""
_PEM_KEY: Final = b"-----BEGIN PRIVATE KEY-----"


class FakeRootUnreadableError(InvalidInputError):
    """``LEDGER_FAKE_DGII_ROOT`` names no readable PEM of a root and its RSA key (its path is never said)."""

    code: ClassVar[str] = "do_fake_dgii_root_unreadable"

    def __init__(self) -> None:
        super().__init__(
            "LEDGER_FAKE_DGII_ROOT must name a readable PEM file of a root certificate and its RSA key"
        )


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
    simulator signs its ARECFs with a certificate made at start, under the root ``LEDGER_FAKE_DGII_ROOT``
    names (a PEM file of its certificate and key: the local stack's, which its apps trust,
    ``deploy/local/do/roots.sh``), or else under a root made at start. ``LEDGER_FAKE_DGII_PUBLIC_URL`` is
    where clients reach it (default ``DEFAULT_PUBLIC_URL``).
    """
    public_url = os.environ.get("LEDGER_FAKE_DGII_PUBLIC_URL", DEFAULT_PUBLIC_URL)
    given = os.environ.get("LEDGER_FAKE_DGII_ROOT")
    root = Authority.root(SIMULATOR_ROOT) if given is None else _root(Path(given))
    receiver = root.issue(organization("131880738", name="EMISOR RECEPTOR DGII"))
    return FakeDgii(check=dgii_check(trusted=None), receiver=receiver, public_url=public_url).app


def _root(path: Path) -> Authority:
    """The authority whose certificate and RSA key ``path`` holds in PEM (``FakeRootUnreadableError``)."""
    try:
        content = path.read_bytes()
        certificate = x509.load_pem_x509_certificate(content)
        key = load_pem_private_key(content[content.index(_PEM_KEY) :], password=None)
    except (OSError, ValueError) as error:
        raise FakeRootUnreadableError from error
    if not isinstance(key, RSAPrivateKey):
        raise FakeRootUnreadableError
    return Authority(Issued(certificate, key))
