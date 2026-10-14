"""What identity audits (§6.4, §10): each change it makes through the API leaves one row in
``identity.audit_events``, in its transaction, naming the caller, the request and hashes of the subject before
and after. Read as the superuser: rows are under the ``organization`` scope."""

import hashlib
from typing import Final
from uuid import UUID

from pydantic import TypeAdapter
lazy import httpx
lazy import psycopg

from ledger_identity.adapters.postgres.unit import bind
from ledger_identity.application import queries
from ledger_kernel.ids import CompanyId
from ledger_kernel.tenancy import Tenant
from ledger_platform.auth.providers.zitadel import organization_of
from ledger_platform.data.codec import canonical_json, encode
from ledger_testing.api import document
lazy from ledger_platform.data.bound import BoundDatabase
lazy from ledger_testing.api import TestPrincipal

ORGANIZATION: Final = organization_of("1001")
DUARTE: Final = {"tax_id": {"scheme": "do.rnc", "value": "101850043"}, "legal_name": "Duarte"}
AUDIT: Final = """
    SELECT organization_id, actor, call_id, action, subject_type, subject_id, before_hash, after_hash, detail
    FROM identity.audit_events ORDER BY occurred_at, id
"""
type Row = tuple[UUID, str, str, str, str, str, bytes | None, bytes | None, dict[str, str]]
ROWS: Final = TypeAdapter(
    list[tuple[UUID, str, str, str, str, str, bytes | None, bytes | None, dict[str, str]]]
)


async def audited(admin: psycopg.AsyncConnection) -> list[Row]:
    cursor = await admin.execute(AUDIT)
    return ROWS.validate_python([tuple(row) for row in await cursor.fetchall()])


def digest(value: object) -> bytes:
    """What an audit row keeps of ``value``: the SHA-256 of its canonical JSON."""
    return hashlib.sha256(canonical_json(encode(value))).digest()


async def test_every_change_through_the_api_is_audited_as_its_caller_in_its_request(
    client: httpx.AsyncClient,
    owner: TestPrincipal,
    admin: psycopg.AsyncConnection,
    module_database: BoundDatabase,
) -> None:
    registered = await client.post("/v1/companies", json=DUARTE, headers=owner.posting("c-1"))
    company = str(document(registered)["id"])
    base = f"/v1/companies/{company}"
    renamed = await client.patch(
        base, json={"legal_name": "Duarte Hermanos"}, headers=owner.bearer() | {"If-Match": '"1"'}
    )
    granted = await client.put(f"{base}/memberships/3001", json={"role": "viewer"}, headers=owner.bearer())
    revoked = await client.delete(f"{base}/memberships/3001", headers=owner.bearer())
    branch = await client.post(
        f"{base}/branches", json={"code": "SUC1", "name": "Santiago"}, headers=owner.posting("b-1")
    )
    branch_id = str(document(branch)["id"])
    point = await client.post(
        f"{base}/branches/{branch_id}/emission-points",
        json={"code": "001", "name": "Caja"},
        headers=owner.posting("p-1"),
    )
    deactivated = await client.post(f"{base}/deactivate", headers=owner.posting("off") | {"If-Match": '"2"'})
    responses = (registered, renamed, granted, revoked, branch, point, deactivated)
    assert [response.status_code for response in responses] == [201, 200, 201, 204, 201, 201, 200]
    rows = await audited(admin)
    assert {row[0] for row in rows} == {ORGANIZATION}
    assert {row[1] for row in rows} == {"2001"}
    call = {
        name: response.headers["request-id"]
        for name, response in zip(
            ("register", "rename", "grant", "revoke", "branch", "point", "deactivate"), responses, strict=True
        )
    }
    assert [(row[2], row[3], row[4], row[8]) for row in rows] == [
        (call["register"], "company.register", "company", {}),
        (call["register"], "membership.grant", "membership", {"member": "2001", "role": "owner"}),
        (call["rename"], "company.rename", "company", {}),
        (call["grant"], "membership.grant", "membership", {"member": "3001", "role": "viewer"}),
        (call["revoke"], "membership.revoke", "membership", {"member": "3001", "role": "viewer"}),
        (call["branch"], "branch.open", "branch", {"code": "SUC1"}),
        (call["point"], "emission_point.open", "emission_point", {"branch_id": branch_id, "code": "001"}),
        (call["deactivate"], "company.deactivate", "company", {}),
    ]
    by_action = {row[3]: row for row in rows}
    assert [
        by_action[action][5] for action in ("company.register", "company.rename", "company.deactivate")
    ] == [company] * 3
    assert by_action["branch.open"][5] == branch_id
    assert by_action["emission_point.open"][5] == str(document(point)["id"])
    tenant = Tenant(ORGANIZATION, CompanyId(UUID(company)), None)
    async with module_database.unit(tenant) as unit:
        inactive = await queries.company(bind(unit), tenant.company)
    register, rename, deactivate = (
        by_action[a] for a in ("company.register", "company.rename", "company.deactivate")
    )
    assert (register[6], rename[6], rename[7], deactivate[6], deactivate[7]) == (
        None,
        register[7],
        deactivate[6],
        rename[7],
        digest(inactive),
    ), "each change starts from what the one before it left: the hashes chain"
    created, opened = (by_action[a] for a in ("membership.grant", "branch.open"))
    assert (created[6], opened[6]) == (None, None)
    assert all(row[7] is not None for row in rows)


async def test_a_replayed_request_audits_nothing_more(
    client: httpx.AsyncClient, owner: TestPrincipal, admin: psycopg.AsyncConnection
) -> None:
    first = await client.post("/v1/companies", json=DUARTE, headers=owner.posting("c-1"))
    again = await client.post("/v1/companies", json=DUARTE, headers=owner.posting("c-1"))
    assert (first.status_code, again.headers["idempotent-replayed"]) == (201, "true")
    assert [row[3] for row in await audited(admin)] == ["company.register", "membership.grant"]
