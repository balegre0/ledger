"""``/v1/organization``: the caller's organization."""

from typing import Annotated

from fastapi import APIRouter, Depends

from ledger_identity.api.context import problems
from ledger_identity.api.models import OrganizationView
from ledger_identity.application import queries
lazy from ledger_identity.api.context import ApiContext
lazy from ledger_identity.settings import IdentitySettings
lazy from ledger_kernel.access import Principal
lazy from ledger_platform.service import Runtime


def router(context: ApiContext) -> APIRouter:
    """The organization's routes."""
    organization = APIRouter(prefix="/v1/organization", tags=["organization"])
    caller, runtime = context.caller, context.runtime

    @organization.get("", response_model=OrganizationView, responses=problems(401, 404))
    async def read_organization(
        principal: Annotated[Principal, Depends(caller)],
        current: Annotated[Runtime[IdentitySettings], Depends(runtime)],
    ) -> OrganizationView:
        """The organization the caller acts in; 404 until it has a company."""
        async with current.database.transaction(principal.organization) as transaction:
            found = await queries.organization(
                context.bind(transaction, current.outbox), principal.organization
            )
        return OrganizationView.of(found)

    return organization
