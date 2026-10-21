"""``/v1/do/numbering_ranges`` through the ``api`` app: a company registers DGII's authorized ranges.

The company enrolls through fiscal (whose review asks this channel what it requires: a range), registers a
range of an environment it is enrolled in, in the request's mode, and fiscal finds nothing due any more. What
a range may be is DGII's: an expiry for every type but 32 and 34, numbers within ten digits, first to last, no
number shared with a range of its series. identity answers who may do what (fiscal's directory).
"""

import asyncio
from typing import Final

import pytest
from pydantic import TypeAdapter
lazy import httpx
lazy import psycopg

from ledger_country_do.channels.ecf_v1.adapters.routes.models import RANGE_PREFIX, NumberingRangeView
from ledger_kernel.ids import parse_id
from ledger_testing.api import TestPrincipal, created, document, page
lazy from ledger_testing.issuer import LocalIssuer

PATH: Final = "/v1/do/numbering_ranges"
ENROLLMENTS: Final = "/v1/fiscal/enrollments"
A31: Final = {"environment": "testecf", "ecf_type": "31", "first": 1, "last": 100, "expires_on": "2028-12-31"}
OUTBOX: Final = TypeAdapter(list[tuple[str, object]])
ACTIONS: Final = TypeAdapter(list[str])
SIBLING: Final = {
    "jurisdiction": {"country": "DO"},
    "functional_currency": "DOP",
    "tax_ids": [{"scheme": "do.rnc", "value": "131880738"}],
    "legal_name": "Ferreteria Duarte Este SRL",
}
"""Another company of the owner's organization."""


def posting(principal: TestPrincipal, company: str, key: str, mode: str = "test") -> dict[str, str]:
    return principal.posting(key, mode) | {"Ledger-Company": company}


def reading(principal: TestPrincipal, company: str, mode: str = "test") -> dict[str, str]:
    return principal.bearer() | {"Ledger-Company": company, "Ledger-Mode": mode}


def code_of(response: httpx.Response) -> tuple[int, object]:
    return response.status_code, document(response)["code"]


@pytest.fixture
async def enrollment(client: httpx.AsyncClient, owner: TestPrincipal, company: str) -> str:
    """The company enrolled on do-ecf-v1 in ``testecf``: its id, the channel asked what it requires."""
    body = {"channel": "do-ecf-v1", "environment": "testecf"}
    response = await client.post(ENROLLMENTS, json=body, headers=posting(owner, company, "enroll"))
    assert response.status_code == 201, response.text
    made = document(response)
    assert (made["status"], made["requirements"]) == (
        "pending",
        {"currently_due": ["numbering_range"], "disabled_reason": None},  # the test signer has a certificate
    )
    return str(made["id"])


async def test_a_registered_range_is_answered_audited_announced_and_satisfies_the_enrollment(
    client: httpx.AsyncClient,
    owner: TestPrincipal,
    company: str,
    enrollment: str,
    admin: psycopg.AsyncConnection,
) -> None:
    response = await client.post(PATH, json=A31, headers=posting(owner, company, "r-1"))
    assert response.status_code == 201, response.text
    made = document(response)
    parse_id(RANGE_PREFIX, str(made["id"]))
    assert response.headers["location"] == f"{PATH}/{made['id']}"
    assert response.headers["etag"] == '"1"'
    assert {key: value for key, value in made.items() if key not in {"id", "created_at"}} == {
        "object": "numbering_range",
        "livemode": False,
        "environment": "testecf",
        "ecf_type": "31",
        "first": 1,
        "last": 100,
        "next": 1,
        "remaining": 100,
        "expires_on": "2028-12-31",
        "status": "active",
        "version": 1,
    }
    assert NumberingRangeView.model_validate_json(response.content).remaining == 100
    audited = await admin.execute("SELECT action FROM do_ecf.audit_events ORDER BY occurred_at, id")
    assert ACTIONS.validate_python([row[0] for row in await audited.fetchall()]) == [
        "numbering_range.register"
    ]
    announced = await admin.execute("SELECT type, payload FROM do_ecf.outbox ORDER BY occurred_at, id")
    assert OUTBOX.validate_python(await announced.fetchall()) == [
        ("fiscal_channel.requirements.changed", {"channel": "do-ecf-v1"})
    ]
    reviewed = await client.get(f"{ENROLLMENTS}/{enrollment}", headers=reading(owner, company))
    assert (document(reviewed)["status"], document(reviewed)["requirements"]) == (
        "active",
        {"currently_due": [], "disabled_reason": None},
    )
    replayed = await client.post(PATH, json=A31, headers=posting(owner, company, "r-1"))
    assert (replayed.status_code, replayed.content, replayed.headers["idempotent-replayed"]) == (
        201,
        response.content,
        "true",
    )


@pytest.mark.usefixtures("enrollment")
async def test_ranges_are_listed_by_environment_and_type_and_read_one_by_one(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str
) -> None:
    first = document(await client.post(PATH, json=A31, headers=posting(owner, company, "a")))
    consumer = A31 | {"ecf_type": "32", "expires_on": None}
    second = document(await client.post(PATH, json=consumer, headers=posting(owner, company, "b")))
    assert second["expires_on"] is None  # DGII may authorize 32 and 34 undated
    listed = page(await client.get(PATH, headers=reading(owner, company)))
    assert [item["id"] for item in listed["items"]] == [first["id"], second["id"]]
    only = page(await client.get(PATH, params={"ecf_type": "32"}, headers=reading(owner, company)))
    assert [item["id"] for item in only["items"]] == [second["id"]]
    none = page(await client.get(PATH, params={"environment": "certecf"}, headers=reading(owner, company)))
    assert none["items"] == []
    one = await client.get(f"{PATH}/{first['id']}", headers=reading(owner, company))
    assert (one.status_code, one.headers["etag"], document(one)) == (200, '"1"', first)
    unknown = await client.get(f"{PATH}/rng_01h455vb4pex5vsknk084sn02q", headers=reading(owner, company))
    assert code_of(unknown) == (404, "do_ecf_range_not_found")
    malformed = await client.get(f"{PATH}/{company}", headers=reading(owner, company))
    assert code_of(malformed) == (404, "do_ecf_range_not_found")
    live = page(await client.get(PATH, headers=reading(owner, company, "live")))
    assert live["items"] == []  # the other mode's


@pytest.mark.usefixtures("enrollment")
@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({"expires_on": None}, (422, "do_ecf_range_expiry_required")),
        ({"first": 101}, (422, "do_ecf_range_invalid")),
        ({"environment": "certecf"}, (422, "do_ecf_environment_not_enrolled")),
        ({"first": 0}, (422, "invalid_request")),
        ({"ecf_type": "30"}, (422, "invalid_request")),
    ],
)
async def test_a_range_that_is_no_dgii_authorization_is_refused(
    client: httpx.AsyncClient,
    owner: TestPrincipal,
    company: str,
    change: dict[str, object],
    expected: tuple[int, str],
) -> None:
    response = await client.post(PATH, json=A31 | change, headers=posting(owner, company, "bad"))
    assert code_of(response) == expected
    assert page(await client.get(PATH, headers=reading(owner, company)))["items"] == []


@pytest.mark.usefixtures("enrollment")
async def test_a_range_sharing_a_number_with_its_series_conflicts(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str
) -> None:
    assert (await client.post(PATH, json=A31, headers=posting(owner, company, "one"))).status_code == 201
    shared = A31 | {"first": 100, "last": 150}
    assert code_of(await client.post(PATH, json=shared, headers=posting(owner, company, "two"))) == (
        409,
        "do_ecf_range_overlaps",
    )
    after = A31 | {"first": 101, "last": 150}
    assert (await client.post(PATH, json=after, headers=posting(owner, company, "three"))).status_code == 201
    other_type = A31 | {"ecf_type": "34"}
    assert (
        await client.post(PATH, json=other_type, headers=posting(owner, company, "four"))
    ).status_code == 201


@pytest.mark.usefixtures("enrollment")
async def test_a_test_environment_takes_no_live_range(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str
) -> None:
    response = await client.post(PATH, json=A31, headers=posting(owner, company, "live", mode="live"))
    assert code_of(response) == (422, "do_ecf_environment_not_allowed")


@pytest.mark.usefixtures("enrollment")
async def test_a_viewer_reads_ranges_and_registers_none(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str, local_issuer: LocalIssuer
) -> None:
    viewer = TestPrincipal(local_issuer, subject="3001")
    membership = f"/v1/companies/{company}/memberships/3001"
    assert (await client.put(membership, json={"role": "viewer"}, headers=owner.bearer())).status_code in {
        200,
        201,
    }
    assert code_of(await client.post(PATH, json=A31, headers=posting(viewer, company, "v"))) == (
        403,
        "permission_denied",
    )
    assert (await client.get(PATH, headers=reading(viewer, company))).status_code == 200
    stranger = TestPrincipal(local_issuer, subject="3002")
    assert (await client.get(PATH, headers=reading(stranger, company))).status_code == 404


@pytest.mark.usefixtures("enrollment")
async def test_two_overlapping_registrations_at_once_register_one(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str
) -> None:
    """The series' row lock serializes them: the second sees the first's range, whichever it is."""
    shared = A31 | {"first": 50, "last": 150}
    answers = await asyncio.gather(
        client.post(PATH, json=A31, headers=posting(owner, company, "at-once-1")),
        client.post(PATH, json=shared, headers=posting(owner, company, "at-once-2")),
    )
    assert sorted(answer.status_code for answer in answers) == [201, 409]
    (refused,) = (answer for answer in answers if answer.status_code == 409)
    assert code_of(refused) == (409, "do_ecf_range_overlaps")
    assert len(page(await client.get(PATH, headers=reading(owner, company)))["items"]) == 1


@pytest.mark.usefixtures("enrollment")
async def test_a_test_mode_range_of_the_live_environment_is_refused(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str
) -> None:
    live_environment = A31 | {"environment": "ecf"}
    response = await client.post(PATH, json=live_environment, headers=posting(owner, company, "ecf-in-test"))
    assert code_of(response) == (422, "do_ecf_environment_not_allowed")


@pytest.mark.usefixtures("enrollment")
async def test_a_range_is_read_by_its_own_company_in_its_own_mode_only(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str
) -> None:
    made = await client.post(PATH, json=A31, headers=posting(owner, company, "mine"))
    assert made.status_code == 201
    path = f"{PATH}/{document(made)['id']}"
    sibling = await created(client, "/v1/companies", SIBLING, owner.posting("company-2"))
    assert code_of(await client.get(path, headers=reading(owner, sibling))) == (404, "do_ecf_range_not_found")
    live = await client.get(path, headers=reading(owner, company, "live"))
    assert code_of(live) == (404, "do_ecf_range_not_found")
    assert (await client.get(path, headers=reading(owner, company))).status_code == 200
