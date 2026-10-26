"""``/v1/do/received_documents`` through the ``api`` app: what emitters sent the company, and its decision.

The documents are the company's alone (row-level security, and the tenant named in every statement): another
company, or the other mode, sees none of them. A decision is kept once, audited and enqueued
(``do_ecf.approve``), replayed from its ``Idempotency-Key``; a second is a 409. Only the document's buyer
decides: a document naming none is refused.
"""

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Final, Protocol
from uuid import UUID

import pytest
from pydantic import TypeAdapter
lazy import httpx
lazy import psycopg

from ledger_country_do.channels.ecf_v1.adapters.postgres.unit import bind
from ledger_country_do.channels.ecf_v1.adapters.routes.models import received_ref
from ledger_country_do.channels.ecf_v1.application.ports import Archived
from ledger_country_do.channels.ecf_v1.application.received import ReceivedDocument
from ledger_country_do.channels.ecf_v1.domain.encf import EcfType, Encf
from ledger_country_do.channels.ecf_v1.domain.environments import Environment
from ledger_country_do.channels.ecf_v1.domain.exchanges import InboundEcf
from ledger_kernel.ids import CompanyId, OrganizationId, new_id, parse_id
from ledger_kernel.tenancy import Mode, Tenant
from ledger_testing.api import created, document, page
lazy from ledger_platform.data.bound import BoundDatabase
lazy from ledger_testing.api import TestPrincipal
lazy from ledger_testing.issuer import LocalIssuer

PATH: Final = "/v1/do/received_documents"
BUYER: Final = "101850043"
"""The ``company`` fixture's RNC: the buyer every document here names."""
ISSUER: Final = "131098193"
OTHER_ISSUER: Final = "40212345678"
SIBLING: Final = {
    "jurisdiction": {"country": "DO"},
    "functional_currency": "DOP",
    "tax_ids": [{"scheme": "do.rnc", "value": "130000003"}],
    "legal_name": "Hermana SRL",
}
IDENTIFIER: Final = TypeAdapter(UUID)
ROWS: Final = TypeAdapter(list[tuple[str, str]])
DGII_REFUSED: Final = (
    "UPDATE do_ecf.received_documents SET approval_dgii = 'refused', approval_dgii_reason = 'refused', "
    "approval_dgii_messages = %s::jsonb WHERE id = %s"
)
SIGNED_AT: Final = datetime(2026, 10, 13, 9, 30, tzinfo=UTC)


def posting(principal: TestPrincipal, company: str, key: str, mode: str = "test") -> dict[str, str]:
    return principal.posting(key, mode) | {"Ledger-Company": company}


def reading(principal: TestPrincipal, company: str, mode: str = "test") -> dict[str, str]:
    return principal.bearer() | {"Ledger-Company": company, "Ledger-Mode": mode}


def code_of(response: httpx.Response) -> tuple[int, object]:
    return response.status_code, document(response)["code"]


async def tenant_of(admin: psycopg.AsyncConnection, company: str) -> Tenant:
    """The company (``cmp_…``) as the channel's units see it, in test mode."""
    identifier = parse_id("cmp", company)
    found = await admin.execute("SELECT organization_id FROM identity.companies WHERE id = %s", (identifier,))
    row = await found.fetchone()
    assert row is not None
    return Tenant(OrganizationId(IDENTIFIER.validate_python(row[0])), CompanyId(identifier), Mode.TEST)


def a_received(
    *,
    issuer: str = ISSUER,
    sequence: int = 1,
    buyer: str | None = BUYER,
    environment: Environment = Environment.TESTECF,
) -> ReceivedDocument:
    """A document ``issuer`` sent the company, as the receiver records one (its archive keys made up)."""
    kind = EcfType.CREDIT_INVOICE
    return ReceivedDocument(
        id=new_id(),
        environment=environment,
        ecf=InboundEcf(
            ecf_type=kind,
            encf=Encf(kind, sequence),
            issuer_rnc=issuer,
            issuer_name="Emisora SRL",
            buyer_rnc=buyer,
            issued_on=date(2026, 10, 13),
            total=Decimal("1180.00"),
            itbis=Decimal("180.00"),
            signed_at=SIGNED_AT,
            security_code="AbC123",
        ),
        received_at=SIGNED_AT,
        document=Archived(key=f"received/{sequence}", sha256=bytes(32)),
        receipt=Archived(key=f"arecf/{sequence}", sha256=bytes(32)),
        version=1,
    )


async def received_by(database: BoundDatabase, tenant: Tenant, *documents: ReceivedDocument) -> None:
    async with database.unit(tenant) as unit:
        for each in documents:
            await bind(unit).received.add(each)


class Keep(Protocol):
    """``keep``: documents the ``company`` fixture received, recorded as its receiver records them."""

    async def __call__(self, *documents: ReceivedDocument) -> None: ...


@pytest.fixture
def keep(admin: psycopg.AsyncConnection, company: str, module_database: BoundDatabase) -> Keep:
    async def kept(*documents: ReceivedDocument) -> None:
        await received_by(module_database, await tenant_of(admin, company), *documents)

    return kept


def view_of(received: ReceivedDocument) -> dict[str, object]:
    """What the API answers of ``received`` before any decision."""
    return {
        "object": "received_document",
        "id": received_ref(received.id),
        "livemode": False,
        "environment": received.environment.value,
        "ecf_type": "31",
        "encf": str(received.ecf.encf),
        "issuer_rnc": received.ecf.issuer_rnc,
        "issuer_name": "Emisora SRL",
        "buyer_rnc": received.ecf.buyer_rnc,
        "issued_on": "2026-10-13",
        "total": "1180.00",
        "itbis": "180.00",
        "signed_at": "2026-10-13T09:30:00Z",
        "security_code": "AbC123",
        "received_at": "2026-10-13T09:30:00Z",
        "approval": None,
        "version": 1,
    }


def ids(response: httpx.Response) -> list[object]:
    return [item["id"] for item in page(response)["items"]]


async def test_received_documents_are_listed_by_filter_and_read_one_by_one_never_with_their_xml(
    client: httpx.AsyncClient,
    owner: TestPrincipal,
    company: str,
    admin: psycopg.AsyncConnection,
    module_database: BoundDatabase,
) -> None:
    tenant = await tenant_of(admin, company)
    first, other = a_received(), a_received(issuer=OTHER_ISSUER, environment=Environment.CERTECF)
    await received_by(module_database, tenant, first, other)
    listed = await client.get(PATH, headers=reading(owner, company))
    assert listed.status_code == 200, listed.text
    assert sorted(ids(listed), key=str) == sorted([received_ref(first.id), received_ref(other.id)])
    read = await client.get(f"{PATH}/{received_ref(first.id)}", headers=reading(owner, company))
    assert (read.status_code, read.headers["etag"]) == (200, '"1"')
    assert document(read) == view_of(first)
    for query, expected in (
        ({"environment": "certecf"}, [received_ref(other.id)]),
        ({"issuer": ISSUER}, [received_ref(first.id)]),
        ({"issuer": OTHER_ISSUER, "environment": "testecf"}, []),
        ({"approval": "approved"}, []),
    ):
        filtered = await client.get(PATH, params=query, headers=reading(owner, company))
        assert ids(filtered) == expected, query
    unparsable = await client.get(PATH, params={"issuer": "13109819X"}, headers=reading(owner, company))
    assert unparsable.status_code == 422


async def test_another_company_and_the_other_mode_see_nothing_of_a_company_s_received_documents(
    client: httpx.AsyncClient,
    owner: TestPrincipal,
    company: str,
    admin: psycopg.AsyncConnection,
    module_database: BoundDatabase,
) -> None:
    tenant = await tenant_of(admin, company)
    mine = a_received()
    await received_by(module_database, tenant, mine)
    sibling = await created(client, "/v1/companies", SIBLING, owner.posting("company-2"))
    path = f"{PATH}/{received_ref(mine.id)}"
    for headers in (reading(owner, sibling), reading(owner, company, "live")):
        assert ids(await client.get(PATH, headers=headers)) == []
        assert code_of(await client.get(path, headers=headers)) == (404, "do_ecf_received_document_not_found")
    for key, company_id, mode in (("theirs", sibling, "test"), ("live", company, "live")):
        approved = await client.post(
            f"{path}/approval", json={"decision": "approved"}, headers=posting(owner, company_id, key, mode)
        )
        assert code_of(approved) == (404, "do_ecf_received_document_not_found")
    found = await admin.execute("SELECT approval_decision FROM do_ecf.received_documents")
    assert await found.fetchall() == [(None,)]


async def test_a_decision_is_kept_once_audited_enqueued_and_replayed_from_its_key(
    client: httpx.AsyncClient,
    owner: TestPrincipal,
    company: str,
    admin: psycopg.AsyncConnection,
    module_database: BoundDatabase,
) -> None:
    tenant = await tenant_of(admin, company)
    decided, undecided = a_received(), a_received(sequence=2)
    await received_by(module_database, tenant, decided, undecided)
    path = f"{PATH}/{received_ref(decided.id)}/approval"
    body = {"decision": "rejected", "reason": "Mercancía no recibida"}
    response = await client.post(path, json=body, headers=posting(owner, company, "decide-1"))
    assert (response.status_code, response.headers["etag"]) == (202, '"2"'), response.text
    view = document(response)
    approval = view["approval"]
    assert isinstance(approval, dict)
    assert {key: approval[key] for key in ("decision", "reason", "signed_at", "emitter", "dgii")} == {
        "decision": "rejected",
        "reason": "Mercancía no recibida",
        "signed_at": None,
        "emitter": {"status": "pending", "reason": None, "sent_at": None},
        "dgii": {"status": "pending", "reason": None, "sent_at": None, "messages": []},
    }
    assert view["version"] == 2
    jobs = await admin.execute("SELECT kind, key FROM do_ecf.jobs WHERE kind = 'do_ecf.approve'")
    assert ROWS.validate_python(await jobs.fetchall()) == [("do_ecf.approve", str(decided.id))]
    audited = await admin.execute(
        "SELECT action, subject_type FROM do_ecf.audit_events WHERE action = 'received_document.approve'"
    )
    assert ROWS.validate_python(await audited.fetchall()) == [
        ("received_document.approve", "received_document")
    ]
    replayed = await client.post(path, json=body, headers=posting(owner, company, "decide-1"))
    assert (replayed.status_code, replayed.content, replayed.headers["idempotent-replayed"]) == (
        202,
        response.content,
        "true",
    )
    again = await client.post(
        path, json={"decision": "approved"}, headers=posting(owner, company, "decide-2")
    )
    assert code_of(again) == (409, "do_ecf_approval_exists")
    rejected = await client.get(PATH, params={"approval": "rejected"}, headers=reading(owner, company))
    assert ids(rejected) == [received_ref(decided.id)]
    pending = await client.get(PATH, params={"approval": "none"}, headers=reading(owner, company))
    assert ids(pending) == [received_ref(undecided.id)]
    jobs = await admin.execute("SELECT count(*) FROM do_ecf.jobs WHERE kind = 'do_ecf.approve'")
    assert await jobs.fetchone() == (1,)


@pytest.mark.parametrize(
    "body",
    [
        {"decision": "rejected"},
        {"decision": "rejected", "reason": "   "},
        {"decision": "approved", "reason": "Conforme"},
    ],
)
async def test_a_rejection_says_why_and_an_approval_does_not(
    client: httpx.AsyncClient,
    owner: TestPrincipal,
    company: str,
    keep: Keep,
    body: dict[str, str],
) -> None:
    received = a_received()
    await keep(received)
    path = f"{PATH}/{received_ref(received.id)}/approval"
    response = await client.post(path, json=body, headers=posting(owner, company, "why"))
    assert code_of(response) == (422, "do_ecf_approval_reason_invalid")
    too_long = {"decision": "rejected", "reason": "x" * 251}
    assert (
        await client.post(path, json=too_long, headers=posting(owner, company, "long"))
    ).status_code == 422


async def test_a_document_naming_no_buyer_or_no_document_is_approved_by_nobody(
    client: httpx.AsyncClient,
    owner: TestPrincipal,
    company: str,
    admin: psycopg.AsyncConnection,
    module_database: BoundDatabase,
) -> None:
    tenant = await tenant_of(admin, company)
    nobody_s = replace(a_received(), ecf=replace(a_received().ecf, buyer_rnc=None))
    await received_by(module_database, tenant, nobody_s)
    approved = await client.post(
        f"{PATH}/{received_ref(nobody_s.id)}/approval",
        json={"decision": "approved"},
        headers=posting(owner, company, "no-buyer"),
    )
    assert code_of(approved) == (409, "do_ecf_approval_not_buyer")
    for name in ("rdoc_01h455vb4pex5vsknk084sn02q", "rng_01h455vb4pex5vsknk084sn02q"):
        missing = await client.post(
            f"{PATH}/{name}/approval", json={"decision": "approved"}, headers=posting(owner, company, name)
        )
        assert code_of(missing) == (404, "do_ecf_received_document_not_found")


async def test_a_viewer_reads_what_the_company_received_and_decides_nothing(
    client: httpx.AsyncClient,
    owner: TestPrincipal,
    company: str,
    local_issuer: LocalIssuer,
    keep: Keep,
) -> None:
    received = a_received()
    await keep(received)
    viewer = TestPrincipal(local_issuer, subject="3001")
    membership = f"/v1/companies/{company}/memberships/3001"
    assert (await client.put(membership, json={"role": "viewer"}, headers=owner.bearer())).status_code in {
        200,
        201,
    }
    assert (await client.get(PATH, headers=reading(viewer, company))).status_code == 200
    refused = await client.post(
        f"{PATH}/{received_ref(received.id)}/approval",
        json={"decision": "approved"},
        headers=posting(viewer, company, "viewer"),
    )
    assert code_of(refused) == (403, "permission_denied")
    stranger = TestPrincipal(local_issuer, subject="3002")
    assert (await client.get(PATH, headers=reading(stranger, company))).status_code == 404


async def test_a_document_shows_where_its_decision_stands_and_what_dgii_said(
    client: httpx.AsyncClient,
    owner: TestPrincipal,
    company: str,
    admin: psycopg.AsyncConnection,
    module_database: BoundDatabase,
) -> None:
    tenant = await tenant_of(admin, company)
    received = a_received()
    await received_by(module_database, tenant, received)
    path = f"{PATH}/{received_ref(received.id)}"
    posted = await client.post(
        f"{path}/approval", json={"decision": "approved"}, headers=posting(owner, company, "shown")
    )
    assert posted.status_code == 202, posted.text
    await admin.execute(
        DGII_REFUSED,
        ('[{"code": "2", "text": "eNCF no aceptado"}]', received.id),
    )
    approval = document(await client.get(path, headers=reading(owner, company)))["approval"]
    assert isinstance(approval, dict)
    assert approval["dgii"] == {
        "status": "refused",
        "reason": "refused",
        "sent_at": None,
        "messages": [{"code": "2", "message": "eNCF no aceptado"}],
    }
