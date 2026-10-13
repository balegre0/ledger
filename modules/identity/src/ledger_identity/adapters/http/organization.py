"""``/v1/organization``: the caller's organization."""

from typing import Annotated

from fastapi import APIRouter, Depends

from ledger_identity.adapters.http.models import OrganizationView
from ledger_identity.adapters.postgres.unit import bind
from ledger_identity.application import queries
from ledger_kernel.tenancy import OrganizationScope
from ledger_platform.http import in_organization, problems
lazy from ledger_identity.adapters.http.context import ApiContext
lazy from ledger_kernel.access import Principal


def router(context: ApiContext) -> APIRouter:
    """The organization's routes."""
    organization = APIRouter(prefix="/v1/organization", tags=["organization"])
    member, database = in_organization(context.caller), context.database

    @organization.get("", response_model=OrganizationView, responses=problems(401, 404))
    async def read_organization(principal: Annotated[Principal, Depends(member)]) -> OrganizationView:
        """The organization the caller acts in; 404 until it has a company."""
        async with database.unit(OrganizationScope(principal.organization, None)) as unit:
            found = await queries.organization(bind(unit), principal.organization)
        return OrganizationView.of(found)

    return organization
