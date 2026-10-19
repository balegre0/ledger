"""The chassis's own caller: proven by the request's bearer token, and nothing more.

``authenticated`` checks the token (401), holds the caller to its rate (429) and makes it the actor of what
the request audits. It does not apply how the caller stands (revocation, an API client's mode, the version
pins): ``Authentication.caller`` wraps it with that, and is the only caller a module or a country gets. So
nothing but the chassis builds a caller from this module: a generated import-linter contract forbids every
module and country to import it.
"""

from typing import Annotated
lazy from collections.abc import AsyncGenerator

from fastapi import Depends
lazy from fastapi import Request
lazy from fastapi.security import HTTPAuthorizationCredentials

from ledger_kernel.ids import new_id
from ledger_platform.auth.tokens import MissingTokenError
from ledger_platform.data.audit import Actor, acting_as
from ledger_platform.http.deps import BEARER
from ledger_platform.http.problems import current_request_id
from ledger_platform.http.rate import RateLimitedError
from ledger_platform.http.request_fields import bound
lazy from ledger_kernel.access import Principal
lazy from ledger_platform.http.deps import Caller, Verifier
lazy from ledger_platform.http.rate import RateLimiter


def authenticated(verifier: Verifier, limiter: RateLimiter | None = None) -> Caller:
    """The dependency that gives a route its caller, proven by the request's bearer token.

    With a ``limiter``, a caller over its rate is refused (429) once it is proven: nobody is counted, and so
    nobody can be limited, by presenting a token that does not verify.
    """

    async def caller(
        request: Request,
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(BEARER)],
    ) -> AsyncGenerator[Principal]:
        if credentials is None:
            raise MissingTokenError
        principal = await verifier.verify(credentials.credentials)
        if (
            limiter is not None
            and (wait := limiter.wait(f"{principal.organization}:{principal.subject}")) > 0
        ):
            raise RateLimitedError(wait)
        call = current_request_id() or str(new_id())
        with (
            bound(request, organization_id=str(principal.organization), subject=principal.subject),
            acting_as(Actor.of(principal, call)),
        ):
            yield principal

    return caller
