"""identity's REST API: every router, under ``/v1``."""

lazy from collections.abc import Sequence

lazy from fastapi import APIRouter

from ledger_identity.api import companies, organization
lazy from ledger_identity.api.context import ApiContext


def routes(context: ApiContext) -> Sequence[APIRouter]:
    """The routers of identity's public API."""
    return (organization.router(context), companies.router(context))
