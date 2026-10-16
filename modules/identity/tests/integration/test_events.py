"""What identity announces (§3.5): each change writes its event to ``identity.outbox`` with the change, in the
same transaction; the fan-out (the worker's) delivers it from there. Read as the superuser: the outbox is the
module's and the relay's."""

from typing import Final
from uuid import UUID

from pydantic import TypeAdapter
lazy import httpx
lazy import psycopg

from ledger_platform.auth.providers.zitadel import organization_of
from ledger_testing.api import TestPrincipal, created, document
lazy from ledger_testing.issuer import LocalIssuer

ORGANIZATION: Final = organization_of("1001")
TRACE_ID: Final = "4bf92f3577b34da6a3ce929d0e0e4736"
TRACEPARENT: Final = f"00-{TRACE_ID}-b7ad6b7169203331-01"
DUARTE: Final = {
    "jurisdiction": {"country": "DO"},
    "functional_currency": "DOP",
    "tax_ids": [{"scheme": "do.rnc", "value": "101850043"}],
    "legal_name": "Duarte",
}
OUTBOX: Final = """
    SELECT type, version, source, organization_id, company_id, mode, subject, aggregate_version, traceparent,
           payload
    FROM identity.outbox ORDER BY id
"""
type Row = tuple[str, int, str, UUID, UUID, str | None, UUID, int, str | None, dict[str, object]]
ROWS: Final = TypeAdapter(
    list[tuple[str, int, str, UUID, UUID, str | None, UUID, int, str | None, dict[str, object]]]
)


async def outbox(admin: psycopg.AsyncConnection) -> list[Row]:
    """Every event waiting in identity's outbox, oldest first."""
    cursor = await admin.execute(OUTBOX)
    return ROWS.validate_python([tuple(row) for row in await cursor.fetchall()])


async def test_a_new_company_and_its_owner_are_announced_with_the_change(
    client: httpx.AsyncClient, owner: TestPrincipal, admin: psycopg.AsyncConnection
) -> None:
    company = UUID(await created(client, "/v1/companies", DUARTE, owner.posting("c-1")))
    registered, granted = await outbox(admin)
    assert registered[:8] == (
        "identity.company.registered",
        1,
        "identity",
        ORGANIZATION,
        company,
        None,
        company,
        1,
    )
    assert registered[9] == {
        "jurisdiction": {"country": "DO", "subdivision": None},
        "functional_currency": "DOP",
        "tax_ids": [{"scheme": "do.rnc", "value": "101850043"}],
        "legal_name": "Duarte",
    }
    membership = granted[6]
    assert granted[:6] == ("identity.membership.granted", 1, "identity", ORGANIZATION, company, None)
    assert (granted[7], granted[9]) == (1, {"member": "2001", "role": "owner", "previous": None})
    assert membership != company


async def test_an_event_carries_the_trace_of_the_request_that_caused_it(
    client: httpx.AsyncClient, owner: TestPrincipal, admin: psycopg.AsyncConnection
) -> None:
    """The server's span is linked to the caller's trace (not its child): the event carries the server's."""
    headers = owner.posting("c-traced") | {"traceparent": TRACEPARENT}
    response = await client.post("/v1/companies", json=DUARTE, headers=headers)
    assert response.status_code == 201
    traces = {row[8] for row in await outbox(admin)}
    (traceparent,) = traces
    assert traceparent is not None
    assert traceparent.split("-")[1] == response.headers["request-id"]


async def test_renames_and_deactivations_are_announced_at_the_company_s_versions(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str, admin: psycopg.AsyncConnection
) -> None:
    path = f"/v1/companies/{company}"
    renamed = await client.patch(
        path, json={"legal_name": "Duarte SRL"}, headers=owner.bearer() | {"If-Match": "*"}
    )
    unchanged = await client.patch(
        path, json={"legal_name": "Duarte SRL"}, headers=owner.bearer() | {"If-Match": "*"}
    )
    off = await client.post(
        f"{path}/deactivate", headers=owner.posting("off-1", mode="live") | {"If-Match": "*"}
    )
    assert (renamed.status_code, unchanged.status_code, off.status_code) == (200, 200, 200)
    events = [(row[0], row[5], row[6], row[7], row[9]) for row in (await outbox(admin))[2:]]
    assert events == [
        (
            "identity.company.updated",
            None,
            UUID(company),
            2,
            {
                "tax_ids": [{"scheme": "do.rnc", "value": "101850043"}],
                "legal_name": "Duarte SRL",
                "fiscal_profile_locked": False,
            },
        ),
        ("identity.company.deactivated", None, UUID(company), 3, {}),  # a live request: still no mode
    ]


async def test_a_request_that_fails_or_replays_announces_nothing_more(
    client: httpx.AsyncClient, local_issuer: LocalIssuer, owner: TestPrincipal, admin: psycopg.AsyncConnection
) -> None:
    await created(client, "/v1/companies", DUARTE, owner.posting("c-1"))
    replayed = await client.post("/v1/companies", json=DUARTE, headers=owner.posting("c-1"))
    taken = await client.post("/v1/companies", json=DUARTE, headers=owner.posting("c-2"))
    stranger = TestPrincipal(local_issuer, organization="1002", roles=owner.roles)
    elsewhere = await client.post("/v1/companies", json=DUARTE, headers=stranger.posting("c-1"))
    assert (replayed.headers["idempotent-replayed"], document(taken)["code"], elsewhere.status_code) == (
        "true",
        "tax_id_taken",
        409,
    )
    assert len(await outbox(admin)) == 2


async def test_a_membership_granted_changed_and_revoked_twice_is_announced_once_each(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str, admin: psycopg.AsyncConnection
) -> None:
    path = f"/v1/companies/{company}/memberships/3001"
    assert (await client.put(path, json={"role": "viewer"}, headers=owner.bearer())).status_code == 201
    assert (await client.put(path, json={"role": "viewer"}, headers=owner.bearer())).status_code == 200
    assert (await client.put(path, json={"role": "issuer"}, headers=owner.bearer())).status_code == 200
    assert [(await client.delete(path, headers=owner.bearer())).status_code for _ in range(2)] == [204, 204]
    events = [(row[0], row[7], row[9]) for row in (await outbox(admin))[2:]]
    assert events == [
        ("identity.membership.granted", 1, {"member": "3001", "role": "viewer", "previous": None}),
        ("identity.membership.granted", 2, {"member": "3001", "role": "issuer", "previous": "viewer"}),
        ("identity.membership.revoked", 3, {"member": "3001", "role": "issuer"}),
    ]
    subjects = {row[6] for row in (await outbox(admin))[2:]}
    assert len(subjects) == 1  # one membership, one subject: its versions order its events
