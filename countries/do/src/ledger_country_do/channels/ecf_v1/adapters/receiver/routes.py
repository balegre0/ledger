"""The receiver's routes of do-ecf-v1 (spec §3.8; Decisions 15, 40; DT-EMI), per company and environment.

DGII registers a receiver's URLs per taxpayer and environment: ``/do-ecf-v1/{environment}/{rnc}`` names both
(Decision 15). The environment must be one of the channel's, and ``rnc`` an RNC or cédula (nine or eleven
ASCII digits) that fiscal's directory names a company by, enrolled on the channel in that environment, in the
mode the environment serves (``ChannelDef.mode_of``); anything else is a bare 404. Each ``(environment,
rnc)`` of a known environment and a well-formed RNC or cédula is held to ``RATE_PER_SECOND`` requests a
second, ``RATE_BURST`` at once (429 with ``Retry-After``), before the directory is asked (an arbitrary path
fills no bucket, so it never pushes a company's out of the limiter); the app holds each client address to
its own rate too (Task 13). The receiver's app lower-cases the paths (DGII's are case-insensitive), so every
parameter arrives in lower case.

- ``GET /fe/autenticacion/api/semilla``: a seed (``answers.seed_answer``).
- ``POST /fe/autenticacion/api/validacioncertificado``: a signed seed (multipart ``xml``, ``MAX_SEED`` bytes
  at most) exchanged for a token: 400 when it is no signed seed, 401 when its signature or certificate is
  not trusted, or the seed is not one of this company and environment, unexpired and unused.
- ``POST /fe/recepcion/api/ecf``: a token (``Authorization: Bearer``, else 401), then an e-CF (multipart
  ``xml``, at most ``MAX_INBOUND``: the app refuses a larger body with 413 before it is read), answered with
  the signed receipt (``application.receiving``); 400 when no receipt can name it, 401 when it is not the
  token's sender's.
- ``POST /fe/aprobacioncomercial/api/ecf``: a token, then an ACECF (``application.approvals_in``, at most
  ``MAX_SMALL_BODY``): 200, or a bare 400 (401 when it is not the token's sender's).

Each route that takes a body declares its limit (``larger_body``: the route's in place of the app's 1 MiB):
2 MiB for a reception, ``MAX_SMALL_BODY`` (64 KiB) for a validation or an approval, refused 413 before the
form is parsed.

Every refusal is a problem with its code and nothing of the request.
"""

import asyncio
from dataclasses import dataclass
from http import HTTPStatus
from typing import Final, final

from fastapi import APIRouter, Depends, HTTPException, Response
lazy from fastapi import Request

from ledger_country_do.channels.ecf_v1.adapters.receiver.answers import (
    receipt_answer,
    seed_answer,
    token_answer,
)
from ledger_country_do.channels.ecf_v1.adapters.receiver.seeds import SeedUnreadableError, SeedUntrustedError
from ledger_country_do.channels.ecf_v1.adapters.receiver.uploads import uploaded
from ledger_country_do.channels.ecf_v1.adapters.xml.inbound import MAX_INBOUND, MAX_SEED
from ledger_country_do.channels.ecf_v1.application.approvals_in import ApprovalRefusedError
from ledger_country_do.channels.ecf_v1.application.ports import SigningAs
from ledger_country_do.channels.ecf_v1.application.receiving import (
    NoReceiptError,
    NotTheSenderError,
    Receiver,
)
from ledger_country_do.channels.ecf_v1.domain.channel import KEY
from ledger_country_do.channels.ecf_v1.domain.environments import Environment
from ledger_country_do.regime.tax_ids import scheme_of
from ledger_kernel.tax_ids import TaxId
from ledger_platform.http.limits import larger_body
from ledger_platform.http.openapi import problems
from ledger_platform.http.rate import RateLimitedError, RateLimiter
from ledger_platform.runtime.calls import SystemCall
lazy from ledger_country_do.channels.ecf_v1.adapters.inbound import Inbound
lazy from ledger_country_do.channels.ecf_v1.adapters.receiver.tokens import ReceiverCredentials
lazy from ledger_country_do.channels.ecf_v1.application.approvals_in import Approving
lazy from ledger_country_do.channels.ecf_v1.application.receiving import Receiving, Sender
lazy from ledger_fiscal_kit.channels import ChannelDef
lazy from ledger_fiscal_kit.ports import CompanyDirectory
lazy from ledger_kernel.clock import Clock

PREFIX: Final = f"/{KEY}/{{environment}}/{{rnc}}/fe"
RATE_PER_SECOND: Final = 10
RATE_BURST: Final = 50
MAX_SEED_BYTES: Final = MAX_SEED
MAX_SMALL_BODY: Final = MAX_SEED
"""The body of a validation or an approval: a signed seed, an ACECF (with a certificate chain), far below."""
LOOKUP_REASON: Final = "do_ecf:receiver"
"""What fiscal's directory is asked as: the receiver looking a company up by the path's tax id."""
SIGNING_REASON: Final = "do_ecf.receive"
"""What the signer's audit names a receipt's signature for (``system:do_ecf.receive``)."""
_BEARER: Final = {"WWW-Authenticate": "Bearer"}
SEED_ANSWERS: Final = problems(404, 429)
VALIDATION_ANSWERS: Final = problems(400, 401, 404, 413, 429)
RECEPTION_ANSWERS: Final = problems(400, 401, 404, 413, 429)
APPROVAL_ANSWERS: Final = problems(400, 401, 404, 413, 429)


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class ReceiverApi:
    """What the routes share, built once by the assembly (in the receiver role only)."""

    definition: ChannelDef
    """The channel's environments, and the mode each serves."""
    directory: CompanyDirectory
    """Fiscal's: the company a tax id names, enrolled in an environment."""
    credentials: ReceiverCredentials
    inbound: Inbound
    receiving: Receiving
    approving: Approving
    clock: Clock


def _unauthenticated() -> HTTPException:
    return HTTPException(HTTPStatus.UNAUTHORIZED, headers=_BEARER)


@final
class _Endpoints:
    """The routes' bodies over ``ReceiverApi``, and the limiter per ``(environment, rnc)`` (the module)."""

    __slots__ = ("_api", "_limiter")

    def __init__(self, api: ReceiverApi) -> None:
        self._api = api
        self._limiter = RateLimiter(
            rate=RATE_PER_SECOND, burst=RATE_BURST, clock=lambda: api.clock().timestamp()
        )

    async def _receiver(self, environment: str, rnc: str) -> Receiver:
        """The company the path names, receiving in its environment (404 otherwise; 429 over its rate)."""
        api = self._api
        scheme = scheme_of(rnc) if rnc.isascii() and rnc.isdigit() else None
        if environment not in api.definition.environments or scheme is None:
            raise HTTPException(HTTPStatus.NOT_FOUND)  # counted by no company's bucket: none to fill
        wait = self._limiter.wait(f"{environment}/{rnc}")
        if wait > 0:
            raise RateLimitedError(wait)
        found = await api.directory.receiving_company(
            SystemCall(reason=LOOKUP_REASON, scope=None),
            tax_id=TaxId(scheme.key, rnc),
            channel=KEY,
            environment=environment,
        )
        if found is None or found.mode is not api.definition.mode_of(environment):
            raise HTTPException(HTTPStatus.NOT_FOUND)
        who = SigningAs(
            organization=found.organization, company=found.company, mode=found.mode, reason=SIGNING_REASON
        )
        return Receiver(who=who, rnc=rnc, environment=Environment(environment))

    async def _sender(self, request: Request, at: Receiver) -> Sender:
        """Who the request's bearer token was issued to, for this company and environment (401 otherwise)."""
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        found = (
            await self._api.credentials.sender(
                token.strip(), company=at.who.company, environment=at.environment
            )
            if scheme.lower() == "bearer" and token.strip()
            else None
        )
        if found is None:
            raise _unauthenticated()
        return found

    async def seed(self, environment: str, rnc: str) -> Response:
        """A seed to sign, valid five minutes, once."""
        at = await self._receiver(environment, rnc)
        value, made = await self._api.credentials.seed(at.who.company, at.environment)
        return seed_answer(value, made)

    async def validate(self, request: Request, environment: str, rnc: str) -> Response:
        """A seed, signed, exchanged for a token of an hour."""
        at = await self._receiver(environment, rnc)
        api = self._api
        document = await uploaded(request)
        try:
            signed = await asyncio.to_thread(
                api.inbound.seed, document, environment=at.environment, received_at=api.clock()
            )
        except SeedUnreadableError:
            raise HTTPException(HTTPStatus.BAD_REQUEST) from None
        except SeedUntrustedError:
            raise _unauthenticated() from None
        token = await api.credentials.exchange(
            signed, company=at.who.company, mode=at.who.mode, environment=at.environment
        )
        if token is None:
            raise _unauthenticated()
        return token_answer(token, accept=request.headers.get("accept", ""))

    async def receive(self, request: Request, environment: str, rnc: str) -> Response:
        """An e-CF, answered with a signed receipt."""
        at = await self._receiver(environment, rnc)
        sender = await self._sender(request, at)
        document = await uploaded(request)
        try:
            signed = await self._api.receiving.receive(at, sender, document)
        except NoReceiptError:
            raise HTTPException(HTTPStatus.BAD_REQUEST) from None
        except NotTheSenderError:
            raise _unauthenticated() from None
        return receipt_answer(signed)

    async def approve(self, request: Request, environment: str, rnc: str) -> Response:
        """A buyer's commercial approval (or rejection) of one of the company's accepted e-CF."""
        at = await self._receiver(environment, rnc)
        sender = await self._sender(request, at)
        document = await uploaded(request)
        try:
            await self._api.approving.receive(at, sender, document)
        except ApprovalRefusedError:
            raise HTTPException(HTTPStatus.BAD_REQUEST) from None
        except NotTheSenderError:
            raise _unauthenticated() from None
        return Response(status_code=HTTPStatus.OK)


def router(api: ReceiverApi) -> APIRouter:
    """The receiver's routes (see the module)."""
    endpoints = _Endpoints(api)
    routes = APIRouter(prefix=PREFIX, tags=["receiver"])
    routes.add_api_route(
        "/autenticacion/api/semilla",
        endpoints.seed,
        methods=["GET"],
        response_class=Response,
        responses=SEED_ANSWERS,
    )
    routes.add_api_route(
        "/autenticacion/api/validacioncertificado",
        endpoints.validate,
        methods=["POST"],
        response_class=Response,
        responses=VALIDATION_ANSWERS,
        dependencies=[Depends(larger_body(MAX_SMALL_BODY))],
    )
    routes.add_api_route(
        "/recepcion/api/ecf",
        endpoints.receive,
        methods=["POST"],
        response_class=Response,
        responses=RECEPTION_ANSWERS,
        dependencies=[Depends(larger_body(MAX_INBOUND))],
    )
    routes.add_api_route(
        "/aprobacioncomercial/api/ecf",
        endpoints.approve,
        methods=["POST"],
        response_class=Response,
        responses=APPROVAL_ANSWERS,
        dependencies=[Depends(larger_body(MAX_SMALL_BODY))],
    )
    return routes
