"""``/v1/api_clients``: machine users registered as API clients, each bound to a mode and an API version.

An organization's administrator (the identity provider's project role ``ledger.organization.admin``) registers
a Zitadel machine user for the mode the request acts in (``Ledger-Mode``); from then on that user's tokens
carry the mode (``Ledger-Mode`` is optional for it) and its version pin. Registering is a ``POST``: it
keeps its ``Idempotency-Key``. Revoking is naturally idempotent and takes none. A client is found, listed and
revoked in its own mode.
"""

from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, Depends
lazy from fastapi import Request, Response

from ledger_identity.adapters.http.models import ApiClientView
from ledger_identity.adapters.http.public import api_client_ref, parse_api_client_ref
from ledger_identity.adapters.postgres.unit import bind
from ledger_identity.application import commands, queries
from ledger_identity.domain.api_clients import ApiClientSpec
from ledger_identity.domain.organization import ORGANIZATION_ADMIN
from ledger_identity.domain.text import parse_name
from ledger_kernel.access import parse_subject
from ledger_kernel.tenancy import OrganizationScope
from ledger_platform.http import (
    CURRENT_VERSION,
    REPLAYED_HEADER,
    Page,
    answer,
    attempt_of,
    holding,
    idempotency_key,
    json_response,
    ledger_mode,
    page_of_items,
    page_query,
    problems,
    success,
)
lazy from ledger_identity.adapters.http.context import ApiContext
lazy from ledger_identity.adapters.http.models import NewApiClient
lazy from ledger_kernel.access import Principal
lazy from ledger_kernel.pagination import PageRequest
lazy from ledger_kernel.tenancy import Mode
lazy from ledger_platform.http import StoredResponse


def router(context: ApiContext) -> APIRouter:
    """The API clients' routes."""
    clients = APIRouter(prefix="/v1/api_clients", tags=["api clients"])
    database = context.database
    administrator = holding(context.caller, ORGANIZATION_ADMIN)

    @clients.post(
        "",
        status_code=HTTPStatus.CREATED,
        response_model=ApiClientView,
        responses=problems(400, 401, 403, 409, 422)
        | success(HTTPStatus.CREATED, "Location", REPLAYED_HEADER),
    )
    async def register_client(
        principal: Annotated[Principal, Depends(administrator)],
        mode: Annotated[Mode, Depends(ledger_mode)],
        key: Annotated[str, Depends(idempotency_key)],
        body: NewApiClient,
        request: Request,
    ) -> Response:
        """Register a machine user as an API client of the caller's organization, for the request's mode.

        The caller must hold the identity provider's project role ``ledger.organization.admin``. The client
        is pinned to the current API version. It holds no role anywhere until it is given one in a company
        (memberships). A machine user that is an active client already, in either mode, is a conflict.
        """
        spec = ApiClientSpec(
            subject=parse_subject(body.subject), name=parse_name(body.name), api_version=CURRENT_VERSION
        )
        attempt = attempt_of(request, principal, key, body, company=None)
        async with database.unit(OrganizationScope(principal.organization, mode)) as unit:

            async def register() -> StoredResponse:
                client = await commands.register_api_client(
                    bind(unit), principal, commands.ClientRegistration(spec=spec, mode=mode)
                )
                location = {"Location": f"/v1/api_clients/{api_client_ref(client.id)}"}
                return json_response(HTTPStatus.CREATED, ApiClientView.of(client), location)

            outcome = await context.idempotency.once(unit, attempt, register)
        return answer(outcome)

    @clients.get("", response_model=Page[ApiClientView], responses=problems(400, 401, 403, 422))
    async def list_clients(
        principal: Annotated[Principal, Depends(administrator)],
        mode: Annotated[Mode, Depends(ledger_mode)],
        page: Annotated[PageRequest, Depends(page_query)],
    ) -> Page[ApiClientView]:
        """The API clients of the caller's organization in the request's mode, revoked ones too."""
        async with database.unit(OrganizationScope(principal.organization, mode)) as unit:
            found = await queries.api_clients(bind(unit), page)
        return page_of_items(found, ApiClientView.of)

    @clients.get("/{client_id}", response_model=ApiClientView, responses=problems(400, 401, 403, 404, 422))
    async def read_client(
        client_id: str,
        principal: Annotated[Principal, Depends(administrator)],
        mode: Annotated[Mode, Depends(ledger_mode)],
    ) -> ApiClientView:
        """An API client of the caller's organization, in the request's mode."""
        wanted = parse_api_client_ref(client_id)
        async with database.unit(OrganizationScope(principal.organization, mode)) as unit:
            found = await queries.api_client(bind(unit), wanted)
        return ApiClientView.of(found)

    @clients.delete("/{client_id}", response_model=ApiClientView, responses=problems(400, 401, 403, 404, 422))
    async def revoke_client(
        client_id: str,
        principal: Annotated[Principal, Depends(administrator)],
        mode: Annotated[Mode, Depends(ledger_mode)],
    ) -> ApiClientView:
        """Revoke an API client: its tokens stop counting at once (within a minute where cached).

        Idempotent: a client revoked already is answered as revoked, and nothing changes or is recorded.
        """
        wanted = parse_api_client_ref(client_id)
        async with database.unit(OrganizationScope(principal.organization, mode)) as unit:
            revoked = await commands.revoke_api_client(bind(unit), principal, wanted)
        return ApiClientView.of(revoked)

    return clients
