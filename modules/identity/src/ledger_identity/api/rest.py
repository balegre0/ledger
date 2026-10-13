"""identity's REST API: every router, under ``/v1``."""

lazy from collections.abc import Sequence

lazy from fastapi import APIRouter

from ledger_identity.api import branches, companies, emission_points, memberships, organization
lazy from ledger_identity.api.context import ApiContext


def routes(context: ApiContext) -> Sequence[APIRouter]:
    """The routers of identity's public API."""
    return (
        organization.router(context),
        companies.router(context),
        branches.router(context),
        emission_points.router(context),
        memberships.router(context),
    )
