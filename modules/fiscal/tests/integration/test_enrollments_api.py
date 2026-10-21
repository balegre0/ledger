"""``/v1/fiscal/enrollments`` through the ``api`` app: a company enrolls on a channel, its enrollment is
reviewed against what the channel requires, listed and disabled, with identity answering who may do what."""

from dataclasses import replace
from datetime import date
from typing import Final, Protocol
from uuid import UUID
lazy from collections.abc import Mapping

import pytest
from pydantic import TypeAdapter
lazy import httpx
lazy import psycopg

from ledger_fiscal.adapters.http.models import EnrollmentView
from ledger_fiscal.domain.errors import ENROLLMENT_PREFIX
from ledger_fiscal_kit.channels import Requirements
from ledger_fiscal_kit.ports import SignerUnavailableError, SigningCertificateMissingError
from ledger_kernel.ids import COMPANY_PREFIX, CompanyId, format_id, parse_id
from ledger_kernel.tenancy import Mode, Tenant
from ledger_testing.api import TestPrincipal, document, page
lazy from ledger_fiscal_kit.channels import ChannelDef, EnrollmentState
lazy from ledger_kernel.errors import LedgerError
lazy from ledger_platform.runtime.calls import SystemCall
lazy from ledger_testing.issuer import LocalIssuer

PATH: Final = "/v1/fiscal/enrollments"
TESTECF: Final = {"channel": "do-fake-v1", "environment": "testecf"}
NOTHING_DUE: Final = Requirements(currently_due=())
UNKNOWN: Final = format_id(ENROLLMENT_PREFIX, UUID("0199e3a0-0000-7000-8000-000000000001"))
OTHER_TYPE: Final = format_id(COMPANY_PREFIX, UUID("0199e3a0-0000-7000-8000-000000000002"))
"""Fixed ids: xdist's workers must collect the same test names."""
OUTBOX: Final = TypeAdapter(list[tuple[str, int, object]])
ACTIONS: Final = TypeAdapter(list[str])
COUNT: Final = TypeAdapter(int)


class FakeChannel(Protocol):
    """The conftest's channel, as these tests use it (see the conftest)."""

    due: Requirements
    asked: list[tuple[SystemCall, EnrollmentState]]
    defined: ChannelDef
    failure: LedgerError | None


@pytest.fixture
def owned(owner: TestPrincipal, company: str) -> tuple[TestPrincipal, str]:
    """The owner and its company, together."""
    return owner, company


def posting(principal: TestPrincipal, company: str, key: str, mode: str = "test") -> dict[str, str]:
    """A POST's headers: the caller's token and key, the mode and the company."""
    return principal.posting(key, mode) | {"Ledger-Company": company}


def reading(principal: TestPrincipal, company: str, mode: str = "test") -> dict[str, str]:
    """A GET's headers: the caller's token, the mode and the company."""
    return principal.bearer() | {"Ledger-Company": company, "Ledger-Mode": mode}


def code_of(response: httpx.Response) -> tuple[int, object]:
    return response.status_code, document(response)["code"]


async def enrolled(
    client: httpx.AsyncClient, headers: Mapping[str, str], body: Mapping[str, str] = TESTECF
) -> dict[str, object]:
    response = await client.post(PATH, json=dict(body), headers=dict(headers))
    assert response.status_code == 201, response.text
    return document(response)


async def audited(admin: psycopg.AsyncConnection) -> list[str]:
    """The actions of fiscal's audit rows, oldest first."""
    cursor = await admin.execute("SELECT action FROM fiscal.audit_events ORDER BY occurred_at, id")
    return ACTIONS.validate_python([row[0] for row in await cursor.fetchall()])


async def keys(admin: psycopg.AsyncConnection) -> int:
    """How many idempotency keys fiscal keeps."""
    cursor = await admin.execute("SELECT count(*) FROM fiscal.idempotency_keys")
    row = await cursor.fetchone()
    return COUNT.validate_python(None if row is None else row[0])


async def events(admin: psycopg.AsyncConnection) -> list[tuple[str, int, object]]:
    """fiscal's outbox, oldest first: each event's type, aggregate version and payload."""
    cursor = await admin.execute(
        "SELECT type, aggregate_version, payload FROM fiscal.outbox ORDER BY occurred_at, id"
    )
    return OUTBOX.validate_python(await cursor.fetchall())


async def test_an_enrollment_waits_for_what_its_channel_requires_then_activates_when_read(
    client: httpx.AsyncClient,
    owner: TestPrincipal,
    company: str,
    fake_channel: FakeChannel,
    admin: psycopg.AsyncConnection,
) -> None:
    response = await client.post(PATH, json=TESTECF, headers=posting(owner, company, "e-1"))
    assert response.status_code == 201
    made = document(response)
    enrollment = parse_id(ENROLLMENT_PREFIX, str(made["id"]))
    assert response.headers["location"] == f"{PATH}/{made['id']}"
    assert response.headers["etag"] == '"1"'
    assert {name: made[name] for name in ("object", "livemode", "channel", "environment", "status")} == {
        "object": "enrollment",
        "livemode": False,
        "channel": "do-fake-v1",
        "environment": "testecf",
        "status": "pending",
    }
    assert (made["requirements"], made["disabled"], made["version"]) == (
        {"currently_due": ["do_ecf.range"], "disabled_reason": None},
        None,
        1,
    )
    assert EnrollmentView.model_validate_json(response.content).livemode is False  # the model it declares
    ((call, state),) = fake_channel.asked
    tenant = Tenant(state.organization, CompanyId(parse_id(COMPANY_PREFIX, company)), Mode.TEST)
    assert (call.reason, call.scope, state.environment, state.active) == (
        "fiscal:requirements",
        tenant,
        "testecf",
        False,
    )
    assert state.enrollment == enrollment
    fake_channel.due = NOTHING_DUE
    read = await client.get(f"{PATH}/{made['id']}", headers=reading(owner, company))
    assert (read.status_code, read.headers["etag"]) == (200, '"2"')
    assert (document(read)["status"], document(read)["requirements"]) == (
        "active",
        {"currently_due": [], "disabled_reason": None},
    )
    again = await client.get(f"{PATH}/{made['id']}", headers=reading(owner, company))
    assert (document(again)["version"], len(fake_channel.asked)) == (2, 3)  # asked again, nothing changed
    announced = await events(admin)
    assert [(kind, version) for kind, version, _ in announced] == [
        ("fiscal.enrollment.changed", 1),
        ("fiscal.enrollment.changed", 2),
    ]
    assert announced[-1][2] == {
        "enrollment": str(enrollment),
        "channel": "do-fake-v1",
        "environment": "testecf",
        "status": "active",
    }
    assert await audited(admin) == ["enrollment.enroll", "enrollment.review"]


async def test_one_enrollment_per_channel_and_mode_until_it_is_disabled(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str, admin: psycopg.AsyncConnection
) -> None:
    first = await enrolled(client, posting(owner, company, "e-1"))
    second = await client.post(
        PATH, json={**TESTECF, "environment": "certecf"}, headers=posting(owner, company, "e-2")
    )
    assert code_of(second) == (409, "fiscal_enrollment_exists")
    assert document(second)["existing_id"] == first["id"]
    live = await enrolled(client, posting(owner, company, "e-3", "live"), {**TESTECF, "environment": "ecf"})
    assert (live["livemode"], live["status"]) == (True, "pending")  # the other mode is another enrollment
    path = f"{PATH}/{first['id']}/disable"
    disabled = await client.post(
        path, json={"reason": "  To   certecf "}, headers=posting(owner, company, "d-1")
    )
    assert disabled.status_code == 200
    told = document(disabled)
    assert (told["status"], told["requirements"], disabled.headers["etag"]) == (
        "disabled",
        {"currently_due": ["do_ecf.range"], "disabled_reason": None},  # as the last review left them
        '"2"',
    )
    assert told["disabled"] == {"reason": "To certecf", "at": told["updated_at"]}
    announced = await events(admin)
    assert [(version, payload) for _, version, payload in announced[2:]] == [
        (
            2,
            {
                "enrollment": str(parse_id(ENROLLMENT_PREFIX, str(first["id"]))),
                "channel": "do-fake-v1",
                "environment": "testecf",
                "status": "disabled",
            },
        )
    ]  # after the two enrollments' own
    assert await audited(admin) == ["enrollment.enroll", "enrollment.enroll", "enrollment.disable"]
    retried = await client.post(
        path, json={"reason": "  To   certecf "}, headers=posting(owner, company, "d-1")
    )
    assert (retried.content, retried.headers["idempotent-replayed"]) == (disabled.content, "true")
    twice = await client.post(path, json={"reason": "Again"}, headers=posting(owner, company, "d-2"))
    assert (twice.status_code, document(twice)["version"]) == (200, 2)  # disabled already: as it was
    assert (len(await events(admin)), len(await audited(admin))) == (3, 3)  # and nothing more said
    moved = await enrolled(client, posting(owner, company, "e-4"), {**TESTECF, "environment": "certecf"})
    assert moved["id"] != first["id"]
    listed = page(await client.get(PATH, headers=reading(owner, company)))
    assert [item["id"] for item in listed["items"]] == [first["id"], moved["id"]]
    assert listed["has_more"] is False


@pytest.mark.parametrize(
    "case",
    [
        ({"channel": "do-other-v1", "environment": "testecf"}, "test", "fiscal_channel_unknown"),
        ({"channel": "do-fake-v1", "environment": "prod"}, "test", "fiscal_environment_unknown"),
        ({"channel": "do-fake-v1", "environment": "ecf"}, "test", "fiscal_environment_not_allowed"),
        ({"channel": "do-fake-v1", "environment": "testecf"}, "live", "fiscal_environment_not_allowed"),
        ({"channel": "do-fake-v1", "environment": "certecf"}, "live", "fiscal_environment_not_allowed"),
    ],
)
async def test_a_channel_and_environment_must_be_installed_and_of_the_request_s_mode(
    client: httpx.AsyncClient,
    owner: TestPrincipal,
    company: str,
    fake_channel: FakeChannel,
    case: tuple[dict[str, str], str, str],
) -> None:
    body, mode, code = case
    response = await client.post(PATH, json=body, headers=posting(owner, company, "e-1", mode))
    assert code_of(response) == (422, code)
    assert body["environment"] not in response.text
    assert fake_channel.asked == []


async def test_a_retry_replays_its_answer_with_the_same_key_and_body_only(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str
) -> None:
    first = await client.post(PATH, json=TESTECF, headers=posting(owner, company, "e-1"))
    again = await client.post(PATH, json=TESTECF, headers=posting(owner, company, "e-1"))
    assert (again.status_code, again.content, again.headers["idempotent-replayed"]) == (
        201,
        first.content,
        "true",
    )
    other = await client.post(
        PATH, json={**TESTECF, "environment": "certecf"}, headers=posting(owner, company, "e-1")
    )
    assert code_of(other) == (422, "idempotency_key_reused")


async def test_a_caller_downgraded_since_gets_its_answers_replayed_and_is_refused_anything_new(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str, local_issuer: LocalIssuer
) -> None:
    """Replays come before the permission a change needs: swapping them refuses the retries below."""
    member = TestPrincipal(local_issuer, subject="3001")
    path = f"/v1/companies/{company}/memberships/3001"
    assert (await client.put(path, json={"role": "admin"}, headers=owner.bearer())).status_code in {200, 201}
    made = await client.post(PATH, json=TESTECF, headers=posting(member, company, "k"))
    assert made.status_code == 201
    disable = f"{PATH}/{document(made)['id']}/disable"
    disabled = await client.post(disable, json={"reason": "Done"}, headers=posting(member, company, "d"))
    assert disabled.status_code == 200
    assert (await client.put(path, json={"role": "viewer"}, headers=owner.bearer())).status_code == 200
    retried = await client.post(PATH, json=TESTECF, headers=posting(member, company, "k"))
    assert (retried.status_code, retried.content, retried.headers["idempotent-replayed"]) == (
        201,
        made.content,
        "true",
    )
    redone = await client.post(disable, json={"reason": "Done"}, headers=posting(member, company, "d"))
    assert (redone.status_code, redone.content, redone.headers["idempotent-replayed"]) == (
        200,
        disabled.content,
        "true",
    )
    fresh = await client.post(PATH, json=TESTECF, headers=posting(member, company, "k-2"))
    assert code_of(fresh) == (403, "permission_denied")
    again = await client.post(disable, json={"reason": "Done"}, headers=posting(member, company, "d-2"))
    assert code_of(again) == (403, "permission_denied")
    seen = await client.get(f"{PATH}/{document(made)['id']}", headers=reading(member, company))
    assert seen.status_code == 200  # a viewer reads


@pytest.mark.parametrize(
    "case",
    [
        (SignerUnavailableError(), 503, "signer_unavailable"),
        (SigningCertificateMissingError(), 409, "signing_certificate_missing"),
    ],
    ids=["unavailable", "conflict"],
)
async def test_a_channel_that_fails_while_enrolling_leaves_no_key_no_enrollment_and_no_event(
    client: httpx.AsyncClient,
    owned: tuple[TestPrincipal, str],
    fake_channel: FakeChannel,
    admin: psycopg.AsyncConnection,
    case: tuple[LedgerError, int, str],
) -> None:
    (owner, company), (failure, status, code) = owned, case
    fake_channel.failure = failure
    refused = await client.post(PATH, json=TESTECF, headers=posting(owner, company, "e-1"))
    assert code_of(refused) == (status, code)
    assert page(await client.get(PATH, headers=reading(owner, company)))["items"] == []
    assert (await events(admin), await keys(admin)) == ([], 0)
    fake_channel.failure = None
    retried = await client.post(PATH, json=TESTECF, headers=posting(owner, company, "e-1"))
    assert retried.status_code == 201
    assert "idempotent-replayed" not in retried.headers


async def test_a_read_whose_channel_cannot_answer_now_answers_the_enrollment_as_kept(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str, fake_channel: FakeChannel
) -> None:
    made = await enrolled(client, posting(owner, company, "e-1"))
    fake_channel.failure = SignerUnavailableError()
    read = await client.get(f"{PATH}/{made['id']}", headers=reading(owner, company))
    assert (read.status_code, document(read)) == (200, made)


async def test_a_channel_not_in_force_today_is_refused(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str, fake_channel: FakeChannel
) -> None:
    ended = replace(fake_channel.defined, valid_from=date(2026, 1, 1), valid_until=date(2026, 1, 31))
    fake_channel.defined = ended
    refused = await client.post(PATH, json=TESTECF, headers=posting(owner, company, "e-1"))
    assert code_of(refused) == (422, "fiscal_channel_not_in_force")
    assert fake_channel.asked == []


async def test_a_company_the_caller_has_no_role_in_is_not_found(
    client: httpx.AsyncClient, company: str, local_issuer: LocalIssuer
) -> None:
    stranger = TestPrincipal(local_issuer, organization="1002", roles=("ledger.organization.admin",))
    for response in (
        await client.post(PATH, json=TESTECF, headers=posting(stranger, company, "s-1")),
        await client.get(PATH, headers=reading(stranger, company)),
    ):
        assert code_of(response) == (404, "company_not_found")


@pytest.mark.parametrize(
    "reference",
    ["enr_x", OTHER_TYPE, UNKNOWN],
    ids=["not-an-id", "another-type", "unknown"],
)
async def test_an_enrollment_by_anything_but_its_id_is_not_found(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str, reference: str
) -> None:
    read = await client.get(f"{PATH}/{reference}", headers=reading(owner, company))
    assert code_of(read) == (404, "fiscal_enrollment_not_found")
    disabled = await client.post(
        f"{PATH}/{reference}/disable", json={"reason": "x"}, headers=posting(owner, company, "d-1")
    )
    assert code_of(disabled) == (404, "fiscal_enrollment_not_found")


async def test_an_enrollment_of_the_other_mode_is_not_found(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str
) -> None:
    made = await enrolled(client, posting(owner, company, "e-1"))
    read = await client.get(f"{PATH}/{made['id']}", headers=reading(owner, company, "live"))
    assert code_of(read) == (404, "fiscal_enrollment_not_found")
    assert page(await client.get(PATH, headers=reading(owner, company, "live")))["items"] == []


async def test_a_disabled_enrollment_is_read_as_kept_without_asking_its_channel(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str, fake_channel: FakeChannel
) -> None:
    made = await enrolled(client, posting(owner, company, "e-1"))
    reason = "x" * 201
    refused = await client.post(
        f"{PATH}/{made['id']}/disable", json={"reason": reason}, headers=posting(owner, company, "d-1")
    )
    assert code_of(refused) == (422, "fiscal_disable_reason_invalid")
    assert reason not in refused.text
    await client.post(
        f"{PATH}/{made['id']}/disable", json={"reason": "Done"}, headers=posting(owner, company, "d-2")
    )
    asked = len(fake_channel.asked)
    read = await client.get(f"{PATH}/{made['id']}", headers=reading(owner, company))
    assert (document(read)["status"], len(fake_channel.asked)) == ("disabled", asked)
