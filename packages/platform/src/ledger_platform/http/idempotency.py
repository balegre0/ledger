"""``Idempotency-Key`` (§9.5): the work of a ``POST`` runs once per key, and a retry replays its answer.

A key is scoped to the organization, the mode, the principal and the route (method and full path
template), and kept seven days in the module's own ``idempotency_keys`` table, in the transaction of the
work: the claim, the module's changes, its events and the stored answer commit together, and a failed request
keeps nothing. A twin waits on the claim's row lock and replays the first's answer; one that waits past the
lock timeout is told the key is in use (409). Another body under a used key is 422. Rows are purged by the
platform's job (Decision 6).

* The organization and the mode are the unit's (its row-level security sees no other): a unit without a mode
  cannot hold keys (``UnmodedIdempotencyScopeError``), which is why an idempotent route requires
  ``Ledger-Mode`` (Decision 4).
* The route is the template the app mounted the route under (``metrics.full_template``), never the path a
  client sent: the same key on two routes is two keys, and one route's answer is never replayed on another.
* The request is fingerprinted by its route and its ``CanonicalBody``: the same body sent with its keys in
  another order or other whitespace is the same request.
* What is stored is replayed exactly: the status, the body's bytes and the headers in their order, less those
  a replay must not repeat (cookies, credentials, the date, the length and the hop-by-hop headers). A server
  failure (5xx) is never stored (``ServerFailureResponseError`` carries it out of the transaction so that it
  rolls back), nor a body over the module's limit (``max_stored_body``, by default ``DEFAULT_STORED_BODY``:
  a defect of the route).
* Times are the database's (the unit's ``now``, its transaction's start).
"""

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import timedelta
from types import MappingProxyType
from typing import Annotated, ClassVar, Final, NewType, final
from urllib.parse import urlencode
lazy from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence

import psycopg
from fastapi import Header, Response
from pydantic import TypeAdapter
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import OperationalError
lazy from fastapi import Request
lazy from pydantic import BaseModel
lazy from sqlalchemy import Select

from ledger_kernel.errors import ConflictError, InvalidInputError, LedgerError
from ledger_platform.config.options import InvalidOptionError
from ledger_platform.http.metrics import full_template
lazy from ledger_kernel.access import Principal
lazy from ledger_kernel.ids import CompanyId
lazy from ledger_platform.data.database import AnyTable
lazy from ledger_platform.data.unit_of_work import UnitOfWork

_COMPONENT: Final = "idempotency"
"""What this module's option errors name (``InvalidOptionError.component``)."""
DEFAULT_RETENTION: Final = timedelta(days=7)
MAX_KEY_LENGTH: Final = 255
DEFAULT_STORED_BODY: Final = 1024 * 1024
"""Bytes of a response body a key keeps at most, unless its module says more (``max_stored_body``): 1 MiB. A
write's answer is a document, never a download; a module whose answers outgrow their requests (a document
priced line by line) keeps more."""
REPLAYED_HEADER: Final = "Idempotent-Replayed"
JSON: Final = "application/json"
_SERVER_FAILURE: Final = 500
"""The first status of a server failure (5xx), which is never stored."""
_UNSTORED_HEADERS: Final = frozenset(
    {
        "set-cookie",
        "authorization",
        "date",
        "content-length",
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)

"""Headers (lowercase) a replay must not repeat: a session or a credential handed to whoever retries, what the
server sets anew on every response (the date, the length), and the hop-by-hop headers of one connection."""
_KEY: Final = re.compile(rf"[!-~]{{1,{MAX_KEY_LENGTH}}}", re.ASCII)
"""Visible ASCII without spaces: what a client can put in a header and see back unchanged."""
_LENGTH_BYTES: Final = 8
_NO_HEADERS: Final[Mapping[str, str]] = MappingProxyType({})
_STORED: Final = TypeAdapter(tuple[bytes, int | None, bytes | None, list[tuple[str, str]] | None])
"""A key's row as ``_stored`` reads it: the request's hash, then the answer (``None`` until it is stored)."""


class InvalidIdempotencyKeyError(InvalidInputError):
    """A key that is not 1 to ``MAX_KEY_LENGTH`` visible ASCII characters (never repeated)."""

    code: ClassVar[str] = "invalid_idempotency_key"

    def __init__(self) -> None:
        super().__init__(f"an Idempotency-Key is 1 to {MAX_KEY_LENGTH} visible ASCII characters")


class IdempotencyKeyReusedError(InvalidInputError):
    """The key was used for another request: a client bug, never to be retried as it is."""

    code: ClassVar[str] = "idempotency_key_reused"

    def __init__(self) -> None:
        super().__init__("this Idempotency-Key was used for a different request")


class IdempotencyKeyInUseError(ConflictError):
    """A request with the same key is still being handled; retrying later replays its response."""

    code: ClassVar[str] = "idempotency_key_in_use"

    def __init__(self) -> None:
        super().__init__("a request with this Idempotency-Key is still being handled; retry later")


class UnmodedIdempotencyScopeError(LedgerError):
    """``once`` was given a unit without a mode: keys are kept per mode, so a moded unit is required."""

    code: ClassVar[str] = "idempotency_scope_unmoded"

    def __init__(self) -> None:
        super().__init__("an idempotency key is claimed in a unit scoped to an organization and a mode only")


class UnlabelledRouteError(LedgerError):
    """``attempt_of`` was called for a request whose route has no full template: no scope for a key."""

    code: ClassVar[str] = "idempotency_route_unlabelled"

    def __init__(self) -> None:
        super().__init__("an idempotency key is scoped to a route the app mounted before it was built")


class UnansweredIdempotencyKeyError(LedgerError):
    """A committed key without a stored response: the claim and the response always commit together."""

    code: ClassVar[str] = "idempotency_key_unanswered"

    def __init__(self) -> None:
        super().__init__("a committed idempotency key has no stored response")


class OversizedStoredResponseError(LedgerError):
    """The work answered a body larger than its module keeps (``max_stored_body``): a bug in the route."""

    code: ClassVar[str] = "idempotency_response_oversized"

    def __init__(self, size: int, limit: int) -> None:
        super().__init__(f"a response of {size} bytes is over the {limit} bytes an idempotency key keeps")


class ServerFailureResponseError(LedgerError):
    """The work answered a server failure (5xx), which is never stored: the transaction must roll back.

    ``response`` is the work's answer, for the route to send all the same; the key stays free for a retry.
    """

    code: ClassVar[str] = "idempotency_response_failed"

    def __init__(self, response: StoredResponse) -> None:
        super().__init__(f"the work answered {response.status}; a server failure is never stored")
        self.response: Final = response


@final
@dataclass(frozen=True, slots=True)
class StoredResponse:
    """A response as it is kept and replayed: the status, the body's bytes and the headers, in their order."""

    status: int
    body: bytes
    headers: Mapping[str, str]


@final
@dataclass(frozen=True, slots=True)
class Outcome:
    """The response of a request, and whether it was replayed from an earlier one with the same key."""

    response: StoredResponse
    replayed: bool


CanonicalBody = NewType("CanonicalBody", bytes)
"""The bytes a request's body is fingerprinted by: made by ``canonical_body``."""


def canonical_body(request: BaseModel | None) -> CanonicalBody:
    """The canonical JSON of a validated request body, or empty bytes for a request without one.

    Compact, keys sorted at every level, UTF-8: the same request sent with its keys in another order, other
    whitespace or other escapes has the same bytes, and a field left to its default is part of the request.
    """
    if request is None:
        return CanonicalBody(b"")
    document = json.dumps(
        request.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return CanonicalBody(document.encode())


@final
@dataclass(frozen=True, slots=True)
class Target:
    """The resource a request is for: its path, its query and the company it acts for."""

    path: str
    query: str
    company: CompanyId | None = None

    @classmethod
    def of(
        cls, path: str, parameters: Iterable[tuple[str, str]] = (), *, company: CompanyId | None = None
    ) -> Target:
        """``path`` with the query ``parameters`` sorted, then percent-encoded, for ``company``.

        The same parameters sent in another order are the same request; a repeated name keeps all its values,
        in any order too: a route that reads a repeated parameter as an ordered list must not take a key
        (spec §9.5). ``company`` is the one the request acts for when it is not in the path (``None`` when it
        acts for none): the same key for another company is another request.
        """
        return cls(path, urlencode(sorted(parameters)), company)


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Attempt:
    """One use of a key: by whom, on which route, which key, and the hash of the request it came with."""

    principal: str
    route: str
    key: str
    request_hash: bytes

    @classmethod
    def of(
        cls, principal: Principal, key: str, *, route: str, target: Target, body: CanonicalBody
    ) -> Attempt:
        """``principal``'s use of ``key`` on ``route`` (``METHOD /template``), for ``target`` with ``body``.

        The key is scoped to the route; the request is the route, the target's path, query and company, and
        the body, each length-prefixed: the same key on another resource of the route, or for another company
        named by a header, is another request (422).
        """
        if _KEY.fullmatch(key) is None:
            raise InvalidIdempotencyKeyError
        digest = hashlib.sha256()
        company = b"" if target.company is None else str(target.company).encode()
        for part in (route.encode(), target.path.encode(), target.query.encode(), company, body):
            digest.update(len(part).to_bytes(_LENGTH_BYTES, "big"))  # no part can run into the next
            digest.update(part)
        return cls(principal=f"user:{principal.subject}", route=route, key=key, request_hash=digest.digest())


def _storable(response: StoredResponse, limit: int) -> list[list[str]]:
    """The headers of ``response`` to store, in their order; raises if the response is not to be stored."""
    if response.status >= _SERVER_FAILURE:
        raise ServerFailureResponseError(response)
    if len(response.body) > limit:
        raise OversizedStoredResponseError(len(response.body), limit)
    return [
        [name, value] for name, value in response.headers.items() if name.lower() not in _UNSTORED_HEADERS
    ]


@final
class Idempotency:
    """Runs the work of a request once per key (see the module)."""

    __slots__ = ("_max_stored_body", "_retention")

    def __init__(
        self, *, retention: timedelta = DEFAULT_RETENTION, max_stored_body: int = DEFAULT_STORED_BODY
    ) -> None:
        """Keys kept ``retention``, each with an answer of at most ``max_stored_body`` bytes."""
        if retention <= timedelta(0):
            raise InvalidOptionError(
                component=_COMPONENT, option="retention", requirement="longer than zero", value=retention
            )
        if max_stored_body < 1:
            raise InvalidOptionError(
                component=_COMPONENT,
                option="max_stored_body",
                requirement="1 byte or more",
                value=max_stored_body,
            )
        self._retention = retention
        self._max_stored_body = max_stored_body

    async def once(
        self, unit: UnitOfWork, attempt: Attempt, work: Callable[[], Awaitable[StoredResponse]]
    ) -> Outcome:
        """Claim ``attempt``'s key in ``unit`` and run ``work``, or replay the response stored for it.

        ``unit`` is the transaction the work runs in, scoped to an organization and a mode (else
        ``UnmodedIdempotencyScopeError``, before any statement). Call it before the work's own statements: a
        twin waits in the claim, before it has locked anything. Raises ``IdempotencyKeyReusedError`` and
        ``IdempotencyKeyInUseError`` for the client; ``ServerFailureResponseError`` and
        ``OversizedStoredResponseError`` when the work's response is not to be stored.
        """
        keys = unit.tables.idempotency_keys
        identity = _identity(unit, attempt)
        row = [keys.c[column] == value for column, value in identity.items()]
        fresh = insert(keys).values(
            **identity,
            request_hash=attempt.request_hash,
            created_at=unit.now,
            expires_at=unit.now + self._retention,
        )
        claim = fresh.on_conflict_do_update(
            index_elements=tuple(identity),
            set_={
                "request_hash": fresh.excluded.request_hash,
                "response_status": None,
                "response_body": None,
                "response_headers": None,
                "created_at": fresh.excluded.created_at,
                "expires_at": fresh.excluded.expires_at,
            },
            where=keys.c.expires_at <= unit.now,
        ).returning(keys.c.key)
        try:
            claimed = (await unit.connection.execute(claim)).first() is not None
        except OperationalError as error:
            if isinstance(error.orig, psycopg.errors.LockNotAvailable):
                raise IdempotencyKeyInUseError from error
            raise
        if not claimed:
            read = _read(keys).where(*row)
            return Outcome(_answer((await unit.connection.execute(read)).one(), attempt), replayed=True)
        response = await work()
        await unit.connection.execute(
            update(keys)
            .where(*row)
            .values(
                response_status=response.status,
                response_body=response.body,
                response_headers=_storable(response, self._max_stored_body),
            )
        )
        return Outcome(response, replayed=False)

    async def replayed(self, unit: UnitOfWork, attempt: Attempt) -> Outcome | None:
        """The answer a committed first use of ``attempt``'s key stored, replayed; ``None`` if there is none.

        For a route that asks other modules before the unit of its work (rule 13): a retry is answered before
        it asks them, as it would be by ``once``, which the route still calls in the unit of its work. Nothing
        is claimed or locked: a first use still in flight is not seen (``once`` then waits for it), and an
        expired key is as good as unused. ``IdempotencyKeyReusedError`` for another request under the key;
        ``UnmodedIdempotencyScopeError`` for a unit without a mode, before any statement.
        """
        keys = unit.tables.idempotency_keys
        row = [keys.c[column] == value for column, value in _identity(unit, attempt).items()]
        read = _read(keys).where(*row, keys.c.expires_at > unit.now)
        found = (await unit.connection.execute(read)).first()
        return None if found is None else Outcome(_answer(found, attempt), replayed=True)


def _identity(unit: UnitOfWork, attempt: Attempt) -> dict[str, object]:
    """The key's row: the unit's organization and mode, the caller, the route and the key."""
    scope = unit.scope
    if scope is None or scope.mode is None:
        raise UnmodedIdempotencyScopeError
    return {
        "organization_id": scope.organization,
        "mode": scope.mode.value,
        "principal": attempt.principal,
        "route": attempt.route,
        "key": attempt.key,
    }


def _read(keys: AnyTable) -> Select[object, object, object, object]:
    """What a key's row holds of its request and its answer."""
    return select(keys.c.request_hash, keys.c.response_status, keys.c.response_body, keys.c.response_headers)


def _answer(found: Sequence[object], attempt: Attempt) -> StoredResponse:
    """The response stored in a key's row ``found``, if the request is the one that first used it."""
    request_hash, status, body, headers = _STORED.validate_python(tuple(found))
    if request_hash != attempt.request_hash:
        raise IdempotencyKeyReusedError
    if status is None or body is None or headers is None:
        raise UnansweredIdempotencyKeyError
    return StoredResponse(status, body, dict(headers))


async def idempotency_key(
    key: Annotated[
        str,
        Header(
            alias="Idempotency-Key",
            min_length=1,
            max_length=MAX_KEY_LENGTH,
            pattern=r"^[!-~]+$",
            description="Makes a retry safe: the same key and body change state once and answer the same.",
        ),
    ],
) -> str:
    """The ``Idempotency-Key`` every ``POST`` requires (§9.5)."""
    return key


def attempt_of(
    request: Request,
    principal: Principal,
    key: str,
    body: BaseModel | None = None,
    *,
    company: CompanyId | None,
) -> Attempt:
    """``principal``'s use of ``key`` for ``request``: its route's full template, its body and its company.

    ``company`` is the one the request acts for (``None`` when it acts for none): it is not always in the
    path, so it is named here and fingerprinted with the rest.

    ``UnlabelledRouteError`` when the route has no full template: an app ``create_app`` did not make, or a
    route added after the app was built.
    """
    template = full_template(request.scope)
    if template is None:
        raise UnlabelledRouteError
    return Attempt.of(
        principal,
        key,
        route=f"{request.method} {template}",
        target=Target.of(request.url.path, request.query_params.multi_items(), company=company),
        body=canonical_body(body),
    )


def json_response(status: int, body: BaseModel, headers: Mapping[str, str] = _NO_HEADERS) -> StoredResponse:
    """A JSON response as it is stored for replays."""
    return StoredResponse(status, body.model_dump_json().encode(), {"Content-Type": JSON, **headers})


def answer(outcome: Outcome) -> Response:
    """The response of an idempotent request: byte for byte the stored one, marked when it is a replay."""
    headers = dict(outcome.response.headers)
    if outcome.replayed:
        headers[REPLAYED_HEADER] = "true"
    return Response(content=outcome.response.body, status_code=outcome.response.status, headers=headers)
