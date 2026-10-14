import asyncio
import contextvars
from dataclasses import dataclass
from functools import partial
from typing import final
from uuid import UUID

import pytest
lazy import httpx

from ledger_identity.adapters.http.models import MembershipView
from ledger_identity.adapters.postgres.unit import bind
from ledger_identity.application import commands
from ledger_kernel.access import Role, SubjectId
from ledger_kernel.ids import CompanyId
from ledger_kernel.tenancy import Tenant
from ledger_platform.auth.providers.zitadel import organization_of
from ledger_testing.api import TestPrincipal, document, page
from ledger_testing.database import app_url
from ledger_testing.waiting import eventually, waits_for_a_lock
lazy from ledger_platform.data.bound import BoundDatabase
lazy from ledger_testing.issuer import LocalIssuer
lazy from ledger_testing.postgres import WorkerDatabase


@final
@dataclass(frozen=True, slots=True)
class Members:
    """The memberships of one company, as its owner (``2001``) or another caller changes them."""

    client: httpx.AsyncClient
    issuer: LocalIssuer
    company: str

    async def assign(self, subject: str, role: str, by: str = "2001") -> httpx.Response:
        return await self.client.put(
            f"/v1/companies/{self.company}/memberships/{subject}",
            json={"role": role},
            headers=TestPrincipal(self.issuer, subject=by).bearer(),
        )

    async def revoke(self, subject: str, by: str = "2001") -> httpx.Response:
        return await self.client.delete(
            f"/v1/companies/{self.company}/memberships/{subject}",
            headers=TestPrincipal(self.issuer, subject=by).bearer(),
        )

    async def read_company(self, by: str) -> httpx.Response:
        return await self.client.get(
            f"/v1/companies/{self.company}", headers=TestPrincipal(self.issuer, subject=by).bearer()
        )


@pytest.fixture
def members(client: httpx.AsyncClient, local_issuer: LocalIssuer, company: str) -> Members:
    return Members(client, local_issuer, company)


async def test_an_owner_gives_a_role_changes_it_takes_it_away_and_gives_it_again(members: Members) -> None:
    granted = await members.assign("3001", "viewer")
    made = document(granted)
    view = MembershipView.model_validate_json(granted.content)
    assert (view.granted_by, view.granted_at.tzinfo is not None) == ("2001", True)
    assert (granted.status_code, made["role"], made["version"], made["granted_by"]) == (
        201,
        "viewer",
        1,
        "2001",
    )
    same = await members.assign("3001", "viewer")
    assert (same.status_code, document(same)["version"]) == (200, 1)
    changed = await members.assign("3001", "issuer")
    assert (changed.status_code, document(changed)["role"], document(changed)["version"]) == (
        200,
        "issuer",
        2,
    )
    assert (await members.read_company("3001")).status_code == 200
    revoked = await members.revoke("3001")
    assert (revoked.status_code, revoked.content) == (204, b"")
    gone = await members.read_company("3001")
    assert (gone.status_code, document(gone)["code"]) == (404, "company_not_found")
    again = await members.assign("3001", "viewer")
    assert (again.status_code, document(again)["version"], document(again)["id"]) == (201, 4, made["id"])


async def test_the_memberships_of_a_company_are_listed_to_whoever_may_read_them(
    client: httpx.AsyncClient, local_issuer: LocalIssuer, members: Members
) -> None:
    await members.assign("3001", "accountant")
    await members.assign("3002", "viewer")
    await members.assign("3003", "viewer")
    await members.revoke("3003")
    path = f"/v1/companies/{members.company}/memberships"
    listed = page(await client.get(path, headers=TestPrincipal(local_issuer, subject="3001").bearer()))
    assert sorted(str(item["subject"]) for item in listed["items"]) == ["2001", "3001", "3002"]
    refused = await client.get(path, headers=TestPrincipal(local_issuer, subject="3002").bearer())
    assert (refused.status_code, document(refused)["code"]) == (403, "permission_denied")


async def test_an_admin_cannot_touch_the_owner_role(members: Members) -> None:
    await members.assign("2002", "admin")
    promote = await members.assign("3001", "owner", by="2002")
    demote = await members.assign("2001", "viewer", by="2002")
    revoke = await members.revoke("2001", by="2002")
    for response in (promote, demote, revoke):
        assert (response.status_code, document(response)["code"]) == (403, "owners_only")
    viewer = await members.assign("3001", "viewer", by="2002")
    assert viewer.status_code == 201


async def test_a_company_always_keeps_an_owner(members: Members) -> None:
    leave = await members.revoke("2001")
    step_down = await members.assign("2001", "admin")
    for response in (leave, step_down):
        assert (response.status_code, document(response)["code"]) == (409, "last_owner")
    await members.assign("2002", "owner")
    assert (await members.assign("2001", "admin")).status_code == 200


async def test_a_role_is_given_in_an_active_company_only_and_taken_in_any(
    client: httpx.AsyncClient, owner: TestPrincipal, members: Members
) -> None:
    await members.assign("3001", "viewer")
    headers = owner.posting("off") | {"If-Match": '"1"'}
    assert (
        await client.post(f"/v1/companies/{members.company}/deactivate", headers=headers)
    ).status_code == 200
    refused = await members.assign("3002", "viewer")
    assert (refused.status_code, document(refused)["code"]) == (409, "company_inactive")
    assert (await members.revoke("3001")).status_code == 204


@pytest.mark.parametrize(
    ("subject", "payload", "expected"),
    [
        ("bad%20subject", {"role": "viewer"}, (422, "invalid_subject")),
        ("3001", {"role": "emperor"}, (422, "invalid_request")),
        ("3001", {"role": "viewer", "until": "2027"}, (422, "invalid_request")),
    ],
    ids=["subject", "role", "unknown-field"],
)
async def test_a_role_that_cannot_be_given_is_refused(
    members: Members, owner: TestPrincipal, subject: str, payload: dict[str, str], expected: tuple[int, str]
) -> None:
    response = await members.client.put(
        f"/v1/companies/{members.company}/memberships/{subject}", json=payload, headers=owner.bearer()
    )
    assert (response.status_code, document(response)["code"]) == expected


async def test_revoking_a_role_nobody_holds_is_not_found(members: Members) -> None:
    response = await members.revoke("4004")
    assert (response.status_code, document(response)["code"]) == (404, "membership_not_found")


async def test_memberships_of_another_organization_are_out_of_reach(
    client: httpx.AsyncClient, local_issuer: LocalIssuer, company: str
) -> None:
    stranger = TestPrincipal(local_issuer, organization="1002").bearer()
    response = await client.put(
        f"/v1/companies/{company}/memberships/3001", json={"role": "owner"}, headers=stranger
    )
    assert (response.status_code, document(response)["code"]) == (404, "company_not_found")


async def test_an_owner_demoted_while_its_grant_of_ownership_waits_for_the_company_is_refused(
    module_database: BoundDatabase, migrated: WorkerDatabase, members: Members
) -> None:
    await members.assign("2002", "owner")
    tenant = Tenant(organization_of("1001"), CompanyId(UUID(members.company)), None)
    outside = contextvars.copy_context()  # the request is another client's: it holds no unit of the test's
    async with module_database.unit(tenant) as unit:  # holds the company's lock
        await commands.grant_role(
            bind(unit),
            tenant.company,
            subject=SubjectId("2001"),
            role=Role.ADMIN,
            actor=SubjectId("2002"),
        )
        waiting = asyncio.create_task(members.assign("3001", "owner", by="2001"), context=outside)
        await eventually(partial(waits_for_a_lock, app_url(migrated)))
    refused = await waiting
    assert (refused.status_code, document(refused)["code"]) == (403, "owners_only")
    gone = await members.revoke("3001")
    assert (gone.status_code, document(gone)["code"]) == (404, "membership_not_found")


async def test_revoking_a_role_twice_answers_204_twice(members: Members) -> None:
    """A DELETE is idempotent: its retry answers as the first did and changes nothing more."""
    await members.assign("3001", "viewer")
    assert [(await members.revoke("3001")).status_code for _ in range(2)] == [204, 204]
    regranted = await members.assign("3001", "viewer")
    assert document(regranted)["version"] == 3  # granted, revoked once, granted: the retry changed nothing
    unknown = await members.revoke("4004")
    assert (unknown.status_code, document(unknown)["code"]) == (404, "membership_not_found")


async def test_a_member_without_the_permission_cannot_give_or_take_a_role(members: Members) -> None:
    await members.assign("3001", "accountant")
    given = await members.assign("3002", "viewer", by="3001")
    taken = await members.revoke("2001", by="3001")
    for response in (given, taken):
        assert (response.status_code, document(response)["code"]) == (403, "permission_denied")


async def test_memberships_are_listed_page_by_page(client: httpx.AsyncClient, members: Members) -> None:
    await members.assign("3001", "viewer")
    path = f"/v1/companies/{members.company}/memberships"
    owner = TestPrincipal(members.issuer).bearer()
    first = page(await client.get(path, params={"limit": 1}, headers=owner))
    rest = page(
        await client.get(path, params={"limit": 1, "starting_after": first["next_cursor"]}, headers=owner)
    )
    assert (len(first["items"]), len(rest["items"]), rest["next_cursor"]) == (1, 1, None)
    back = page(await client.get(path, params={"ending_before": rest["previous_cursor"]}, headers=owner))
    assert (back["items"], back["has_more"]) == (first["items"], False)
