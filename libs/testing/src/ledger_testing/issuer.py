"""A local OpenID provider for tests: RSA keys, their JWKS served on 127.0.0.1, access tokens on demand.

The tokens look like the access tokens Zitadel v4 issues to a machine user (``iss``, ``aud``, ``sub``,
``exp``, ``iat``, ``nbf``, ``jti``, ``client_id``, the resource owner's organization id and, when asked,
project roles; header ``typ`` ``JWT``), signed RS256 with a key the JWKS document publishes. A test can
rotate and retire keys, make the endpoint fail or stall, and count how often it was asked.
"""

import asyncio
import contextlib
import json
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from typing import Final, final
lazy from collections.abc import AsyncGenerator, Iterable, Mapping

from joserfc import jwt
from joserfc.jwk import RSAKey
from pydantic import TypeAdapter

ISSUER: Final = "https://issuer.ledger.test"
AUDIENCE: Final = "ledger-test"
PROVIDER_ORGANIZATION: Final = "1001"
SUBJECT: Final = "2001"
ORGANIZATION_CLAIM: Final = "urn:zitadel:iam:user:resourceowner:id"
_KEY_SIZE: Final = 2048
_LIFETIME: Final = timedelta(minutes=5)
_END_OF_HEAD: Final = b"\r\n\r\n"
_ADDRESS: Final = TypeAdapter(tuple[str, int] | tuple[str, int, int, int])
"""An internet address as a socket reports it: host and port, plus flow and scope for IPv6."""


def _answer_of(status: HTTPStatus, body: bytes, encoding: str | None) -> bytes:
    encoded = "" if encoding is None else f"Content-Encoding: {encoding}\r\n"
    head = (
        f"HTTP/1.1 {status.value} {status.phrase}\r\nContent-Type: application/json\r\n{encoded}"
        f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n"
    )
    return head.encode("ascii") + body


@final
class LocalIssuer:
    """An identity provider as a resource server sees one: published keys, and tokens signed with them."""

    __slots__ = (
        "_current",
        "_handlers",
        "_keys",
        "_port",
        "_published",
        "audience",
        "delay",
        "encoding",
        "failure",
        "fetches",
        "issuer",
        "requests",
    )

    def __init__(self, *, issuer: str = ISSUER, audience: str = AUDIENCE) -> None:
        self.issuer = issuer
        self.audience = audience
        self.fetches = 0
        """How many times the JWKS document was asked for."""
        self.failure: tuple[HTTPStatus, bytes] | None = None
        """What the endpoint answers instead of the keys while it is set."""
        self.delay = 0.0
        """Seconds the endpoint waits before it answers."""
        self.encoding: str | None = None
        """The ``Content-Encoding`` the endpoint says its answer has (the body is sent as given)."""
        self.requests: list[bytes] = []
        """The head of every request, as it arrived."""
        self._keys: dict[str, RSAKey] = {}
        self._published: list[str] = []
        self._handlers: set[asyncio.Task[None]] = set()
        self._port = 0
        self._current = self.rotate()

    @classmethod
    @asynccontextmanager
    async def started(cls, *, issuer: str = ISSUER, audience: str = AUDIENCE) -> AsyncGenerator[LocalIssuer]:
        """An issuer whose JWKS endpoint listens on 127.0.0.1 for the length of the block."""
        local = cls(issuer=issuer, audience=audience)
        server = await asyncio.start_server(local._accept, "127.0.0.1", 0)
        local._port = _ADDRESS.validate_python(server.sockets[0].getsockname())[1]
        try:
            yield local
        finally:
            server.close()
            server.close_clients()
            pending = tuple(local._handlers)
            for handler in pending:
                handler.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            await server.wait_closed()

    @property
    def keys_url(self) -> str:
        """Where the JWKS document is served (once ``started``)."""
        return f"http://127.0.0.1:{self._port}/keys"

    def rotate(self) -> str:
        """Make a new key, publish it beside the others and sign with it from now on; its id."""
        kid = f"key-{len(self._keys) + 1}"
        self._keys[kid] = RSAKey.generate_key(
            _KEY_SIZE, {"kid": kid, "use": "sig", "alg": "RS256"}, private=True
        )
        self._published.append(kid)
        self._current = kid
        return kid

    def retire(self, kid: str) -> None:
        """Stop publishing ``kid``. Tokens can still be signed with it, as by anyone who kept it."""
        self._published.remove(kid)

    def published(self) -> bytes:
        """The JWKS document: the public half of every published key."""
        keys = [self._keys[kid].as_dict(private=False) for kid in self._published]
        return json.dumps({"keys": keys}).encode()

    def signing_key(self, kid: str | None = None) -> RSAKey:
        """The private key ``kid`` (the newest by default), for a test that signs something unusual."""
        return self._keys[kid or self._current]

    def token(
        self,
        *,
        subject: str = SUBJECT,
        organization: str = PROVIDER_ORGANIZATION,
        roles: Iterable[str] = (),
        claims: Mapping[str, object] | None = None,
        kid: str | None = None,
    ) -> str:
        """An access token for ``subject`` of ``organization``, valid for five minutes from now.

        ``roles`` are project roles granted in that organization. ``claims`` replace or add claims; one set
        to ``None`` is left out. ``kid`` signs with that key instead of the newest.
        """
        issued = int(datetime.now(UTC).timestamp())
        payload: dict[str, object] = {
            "iss": self.issuer,
            "aud": [self.audience],
            "sub": subject,
            "client_id": "ledger-test",
            "jti": f"V2_{uuid.uuid7().hex}-at_{uuid.uuid7().hex}",
            "iat": issued,
            "nbf": issued,
            "exp": issued + int(_LIFETIME.total_seconds()),
            ORGANIZATION_CLAIM: organization,
        }
        granted = tuple(roles)
        if granted:
            payload[f"urn:zitadel:iam:org:project:{self.audience}:roles"] = {
                role: {organization: "ledger.test"} for role in granted
            }
        payload.update(claims or {})
        kept = {name: value for name, value in payload.items() if value is not None}
        return jwt.encode(
            {"alg": "RS256", "kid": kid or self._current, "typ": "JWT"}, kept, self.signing_key(kid)
        )

    def _accept(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        handler = asyncio.create_task(self._answer(reader, writer))
        self._handlers.add(handler)
        handler.add_done_callback(self._handlers.discard)

    async def _answer(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Answer one request with the keys (or the failure), whatever was asked, then close."""
        with contextlib.suppress(ConnectionError, asyncio.IncompleteReadError):
            self.requests.append(await reader.readuntil(_END_OF_HEAD))
            self.fetches += 1
            await asyncio.sleep(self.delay)
            status, body = self.failure or (HTTPStatus.OK, self.published())
            writer.write(_answer_of(status, body, self.encoding))
            await writer.drain()
        writer.close()
        with contextlib.suppress(ConnectionError):
            await writer.wait_closed()
