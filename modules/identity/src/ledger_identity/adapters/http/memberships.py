"""``/v1/companies/{company_id}/memberships``: who holds which role in a company (in both modes).

A ``PUT`` names its resource (the subject's membership) and is idempotent by itself: it takes no
``Idempotency-Key`` (so no ``Ledger-Mode`` either), and its 201 carries no ``Location``, the resource being
the request's own URI (RFC 9110 section 15.3.2).
"""

from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, Depends
lazy from fastapi import Response

from ledger_identity.adapters.http.models import MembershipView
from ledger_identity.adapters.postgres.unit import bind
from ledger_identity.application import commands, queries
from ledger_kernel.access import Permission, parse_subject
from ledger_platform.http import Page, allowed, page_of_items, page_query, problems
lazy from ledger_identity.adapters.http.context import ApiContext
lazy from ledger_identity.adapters.http.models import RoleAssignment
lazy from ledger_kernel.pagination import PageRequest
lazy from ledger_platform.auth import CompanyAccess


def router(context: ApiContext) -> APIRouter:
    """The routes of memberships."""
    memberships = APIRouter(prefix="/v1/companies/{company_id}/memberships", tags=["memberships"])
    database = context.database
    reader = allowed(context.in_company, Permission.MEMBERSHIP_READ)
    manager = allowed(context.in_company, Permission.MEMBERSHIP_MANAGE)

    @memberships.get("", response_model=Page[MembershipView], responses=problems(401, 403, 404, 422))
    async def list_memberships(
        access: Annotated[CompanyAccess, Depends(reader)],
        page: Annotated[PageRequest, Depends(page_query)],
    ) -> Page[MembershipView]:
        """Who holds which role in the company."""
        async with database.unit(access.tenant) as unit:
            found = await queries.memberships(bind(unit), access.tenant.company, page)
        return page_of_items(found, MembershipView.of)

    @memberships.put(
        "/{subject}",
        response_model=MembershipView,
        responses=problems(401, 403, 404, 409, 422)
        | {
            HTTPStatus.CREATED.value: {
                "model": MembershipView,
                "description": "The subject held no role before",
            }
        },
    )
    async def assign_role(
        access: Annotated[CompanyAccess, Depends(manager)],
        subject: str,
        body: RoleAssignment,
        response: Response,
    ) -> MembershipView:
        """Give ``subject`` a role in the company, or change the one it holds: 201 when it held none.

        The caller's permission is checked first, and again on its membership as it is once the company is
        locked: a role it lost meanwhile does not count. The owner role is given and taken by owners only.
        """
        member = parse_subject(subject)
        async with database.unit(access.tenant) as unit:
            granted = await commands.grant_role(
                bind(unit),
                access.tenant.company,
                subject=member,
                role=body.role,
                actor=access.principal.subject,
            )
        response.status_code = HTTPStatus.CREATED if granted.created else HTTPStatus.OK
        return MembershipView.of(granted.membership)

    @memberships.delete(
        "/{subject}", status_code=HTTPStatus.NO_CONTENT, responses=problems(401, 403, 404, 409, 422)
    )
    async def revoke_role(access: Annotated[CompanyAccess, Depends(manager)], subject: str) -> None:
        """Take ``subject``'s role in the company away (its membership is kept, revoked).

        Idempotent: revoking a role revoked already answers 204 again and changes nothing; a subject that
        never held a role in the company is a 404.
        """
        member = parse_subject(subject)
        async with database.unit(access.tenant) as unit:
            await commands.revoke_role(
                bind(unit), access.tenant.company, subject=member, actor=access.principal.subject
            )

    return memberships
