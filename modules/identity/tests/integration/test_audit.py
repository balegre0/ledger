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
from ledger_identity.domain.ids import API_CLIENT_PREFIX, ESTABLISHMENT_PREFIX
from ledger_kernel.ids import COMPANY_PREFIX, CompanyId, parse_id
from ledger_kernel.tenancy import Tenant
from ledger_platform.auth.providers.zitadel import organization_of
from ledger_platform.data.codec import canonical_json, encode
from ledger_testing.api import document
lazy from ledger_platform.data.bound import BoundDatabase
lazy from ledger_testing.api import TestPrincipal

ORGANIZATION: Final = organization_of("1001")
DUARTE: Final = {
    "jurisdiction": {"country": "DO"},
    "functional_currency": "DOP",
    "tax_ids": [{"scheme": "do.rnc", "value": "101850043"}],
    "legal_name": "Duarte",
}
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
    internal = str(parse_id(COMPANY_PREFIX, company))
    base = f"/v1/companies/{company}"
    renamed = await client.patch(
        base, json={"legal_name": "Duarte Hermanos"}, headers=owner.bearer() | {"If-Match": '"1"'}
    )
    granted = await client.put(f"{base}/memberships/3001", json={"role": "viewer"}, headers=owner.bearer())
    revoked = await client.delete(f"{base}/memberships/3001", headers=owner.bearer())
    establishment = await client.post(
        f"{base}/establishments",
        json={"code": "SUC1", "name": "Santiago", "time_zone": "Europe/Madrid"},
        headers=owner.posting("e-1"),
    )
    deactivated = await client.post(f"{base}/deactivate", headers=owner.posting("off") | {"If-Match": '"2"'})
    responses = (registered, renamed, granted, revoked, establishment, deactivated)
    assert [response.status_code for response in responses] == [201, 200, 201, 204, 201, 200]
    rows = await audited(admin)
    assert {row[0] for row in rows} == {ORGANIZATION}
    assert {row[1] for row in rows} == {"human:2001"}  # the caller, a person
    call = {
        name: response.headers["request-id"]
        for name, response in zip(
            ("register", "rename", "grant", "revoke", "establishment", "deactivate"), responses, strict=True
        )
    }
    assert [(row[2], row[3], row[4], row[8]) for row in rows] == [
        (call["register"], "company.register", "company", {}),
        (call["register"], "membership.grant", "membership", {"member": "2001", "role": "owner"}),
        (
            call["register"],
            "establishment.open",
            "establishment",
            {"code": "MAIN", "time_zone": "America/Santo_Domingo"},
        ),
        (call["rename"], "company.rename", "company", {}),
        (call["grant"], "membership.grant", "membership", {"member": "3001", "role": "viewer"}),
        (call["revoke"], "membership.revoke", "membership", {"member": "3001", "role": "viewer"}),
        (
            call["establishment"],
            "establishment.open",
            "establishment",
            {"code": "SUC1", "time_zone": "Europe/Madrid"},
        ),
        (call["deactivate"], "company.deactivate", "company", {}),
    ]
    by_action = {row[3]: row for row in rows}
    assert [
        by_action[action][5] for action in ("company.register", "company.rename", "company.deactivate")
    ] == [internal] * 3
    assert by_action["establishment.open"][5] == str(
        parse_id(ESTABLISHMENT_PREFIX, str(document(establishment)["id"]))
    )  # the last one opened
    tenant = Tenant(ORGANIZATION, CompanyId(parse_id(COMPANY_PREFIX, company)), None)
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
    created, opened = (by_action[a] for a in ("membership.grant", "establishment.open"))
    assert (created[6], opened[6]) == (None, None)
    assert all(row[7] is not None for row in rows)


async def test_a_replayed_request_audits_nothing_more(
    client: httpx.AsyncClient, owner: TestPrincipal, admin: psycopg.AsyncConnection
) -> None:
    first = await client.post("/v1/companies", json=DUARTE, headers=owner.posting("c-1"))
    again = await client.post("/v1/companies", json=DUARTE, headers=owner.posting("c-1"))
    assert (first.status_code, again.headers["idempotent-replayed"]) == (201, "true")
    assert [row[3] for row in await audited(admin)] == [
        "company.register",
        "membership.grant",
        "establishment.open",
    ]


async def test_registering_and_revoking_an_api_client_are_audited_as_the_administrator_in_its_request(
    client: httpx.AsyncClient, owner: TestPrincipal, admin: psycopg.AsyncConnection
) -> None:
    made = await client.post(
        "/v1/api_clients",
        json={"subject": "4001", "name": "Odoo"},
        headers=owner.posting("k-1", mode="live"),
    )
    revoked = await client.delete(
        f"/v1/api_clients/{document(made)['id']}", headers=owner.bearer() | {"Ledger-Mode": "live"}
    )
    rows = await audited(admin)
    detail = {"subject": "4001", "mode": "live"}
    assert [(row[1], row[2], row[3], row[4], row[8]) for row in rows] == [
        ("human:2001", made.headers["request-id"], "api_client.register", "api_client", detail),
        ("human:2001", revoked.headers["request-id"], "api_client.revoke", "api_client", detail),
    ]
    assert rows[0][5] == str(parse_id(API_CLIENT_PREFIX, str(document(made)["id"])))
    register, revoke = rows
    assert (register[6], register[7] is not None, revoke[6]) == (None, True, register[7])  # the hashes chain
    again = await client.delete(
        f"/v1/api_clients/{document(made)['id']}", headers=owner.bearer() | {"Ledger-Mode": "live"}
    )
    assert again.status_code == 200
    assert len(await audited(admin)) == 2  # revoking again records nothing
