"""``/v1/api_clients``: machine users registered for a mode, and what their credentials then carry."""

from datetime import date
from typing import Final, final

import pytest
from pydantic import TypeAdapter
lazy import httpx
lazy import psycopg

from ledger_identity.adapters.standings import STANDING_TTL, cached_standings
from ledger_identity.domain.ids import API_CLIENT_PREFIX
from ledger_identity.domain.organization import ORGANIZATION_ADMIN
from ledger_kernel.access import Principal, PrincipalKind, SubjectId
from ledger_kernel.ids import format_id, parse_id
from ledger_platform.auth.providers.zitadel import organization_of
from ledger_platform.http import CURRENT_VERSION
from ledger_platform.http.versions import VERSION_HEADER
from ledger_testing.api import created, document, page
lazy from ledger_platform.data import BoundDatabase
lazy from ledger_platform.runtime import App
lazy from ledger_testing.api import TestPrincipal
lazy from ledger_testing.issuer import LocalIssuer

NEW: Final = {"subject": "4001", "name": " Odoo   connector "}
EARLIER: Final = date(2026, 1, 1)
DOCUMENT: Final = TypeAdapter(dict[str, object])
PATHS: Final = TypeAdapter(dict[str, dict[str, dict[str, object]]])


def machine(local_issuer: LocalIssuer, subject: str = "4001") -> TestPrincipal:
    """The machine user ``subject`` of organization ``1001``, with the provider's admin role."""
    return TestPrincipal(
        local_issuer, subject=subject, roles=(ORGANIZATION_ADMIN,), kind=PrincipalKind.MACHINE
    )


async def register_in(client: httpx.AsyncClient, owner: TestPrincipal, mode: str, key: str = "k-1") -> str:
    """The id of the API client ``owner`` registers in ``mode`` for ``NEW``."""
    return await created(client, "/v1/api_clients", NEW, owner.posting(key, mode=mode))


async def test_an_administrator_registers_a_machine_user_for_its_mode_and_the_client_is_found_again(
    client: httpx.AsyncClient, owner: TestPrincipal
) -> None:
    response = await client.post("/v1/api_clients", json=NEW, headers=owner.posting("k-1", mode="live"))
    assert response.status_code == 201
    made = document(response)
    assert (made["mode"], made["subject"], made["name"], made["status"]) == (
        "live",
        "4001",
        "Odoo connector",
        "active",
    )
    assert (made["api_version"], made["created_by"], made["revoked_at"]) == (
        CURRENT_VERSION.isoformat(),
        "2001",
        None,
    )
    assert str(made["id"]).startswith(f"{API_CLIENT_PREFIX}_")
    assert response.headers["location"] == f"/v1/api_clients/{made['id']}"
    read = await client.get(f"/v1/api_clients/{made['id']}", headers=owner.bearer() | {"Ledger-Mode": "live"})
    assert (read.status_code, document(read)) == (200, made)
    again = await client.post("/v1/api_clients", json=NEW, headers=owner.posting("k-1", mode="live"))
    assert (again.content, again.headers["idempotent-replayed"]) == (response.content, "true")


async def test_registering_needs_the_organization_s_admin_role_a_mode_and_a_key(
    client: httpx.AsyncClient, local_issuer: LocalIssuer, owner: TestPrincipal
) -> None:
    plain = TestPrincipal(local_issuer, subject="2002")  # a person of the organization, no administrator
    forbidden = await client.post("/v1/api_clients", json=NEW, headers=plain.posting("k-1"))
    assert (forbidden.status_code, document(forbidden)["code"]) == (403, "forbidden")
    keyless = await client.post("/v1/api_clients", json=NEW, headers=owner.bearer() | {"Ledger-Mode": "test"})
    assert (keyless.status_code, document(keyless)["code"]) == (422, "invalid_request")
    modeless = await client.post(
        "/v1/api_clients", json=NEW, headers=owner.bearer() | {"Idempotency-Key": "k-1"}
    )
    assert (modeless.status_code, document(modeless)["code"]) == (400, "mode_required")


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        ({"subject": "has space", "name": "X"}, "invalid_subject"),
        ({"subject": "4001", "name": " "}, "invalid_name"),
        ({"subject": "", "name": "X"}, "invalid_request"),
        ({"subject": "4001", "name": "X", "mode": "live"}, "invalid_request"),
    ],
    ids=["subject", "name", "empty-subject", "mode-in-the-body"],
)
async def test_a_machine_user_that_cannot_be_registered_is_refused(
    client: httpx.AsyncClient, owner: TestPrincipal, payload: dict[str, str], code: str
) -> None:
    response = await client.post("/v1/api_clients", json=payload, headers=owner.posting("k-bad"))
    assert (response.status_code, document(response)["code"]) == (422, code)


async def test_a_machine_user_is_one_api_client_whatever_the_mode_until_it_is_revoked(
    client: httpx.AsyncClient, owner: TestPrincipal
) -> None:
    first = await register_in(client, owner, "test")
    for mode in ("test", "live"):
        taken = await client.post("/v1/api_clients", json=NEW, headers=owner.posting(f"k-{mode}", mode=mode))
        assert (taken.status_code, document(taken)["code"]) == (409, "api_client_exists")
    revoked = await client.delete(
        f"/v1/api_clients/{first}", headers=owner.bearer() | {"Ledger-Mode": "test"}
    )
    assert (revoked.status_code, document(revoked)["status"]) == (200, "revoked")
    assert await register_in(client, owner, "live", key="k-again")  # a new client, in the other mode


async def test_clients_are_listed_and_found_in_their_own_mode_only(
    client: httpx.AsyncClient, owner: TestPrincipal
) -> None:
    first = await register_in(client, owner, "test")
    other = await created(
        client, "/v1/api_clients", {"subject": "4002", "name": "Other"}, owner.posting("k-2", mode="live")
    )
    for mode, mine, theirs in (("test", first, other), ("live", other, first)):
        headers = owner.bearer() | {"Ledger-Mode": mode}
        listed = page(await client.get("/v1/api_clients", headers=headers))
        assert [item["id"] for item in listed["items"]] == [mine]
        seen = await client.get(f"/v1/api_clients/{theirs}", headers=headers)
        assert (seen.status_code, document(seen)["code"]) == (404, "api_client_not_found")
        gone = await client.delete(f"/v1/api_clients/{theirs}", headers=headers)
        assert (gone.status_code, document(gone)["code"]) == (404, "api_client_not_found")


async def test_revoking_is_idempotent_and_the_revoked_client_stays_listed(
    client: httpx.AsyncClient, owner: TestPrincipal
) -> None:
    made = await register_in(client, owner, "test")
    headers = owner.bearer() | {"Ledger-Mode": "test"}
    first = await client.delete(f"/v1/api_clients/{made}", headers=headers)
    again = await client.delete(f"/v1/api_clients/{made}", headers=headers)
    assert (first.status_code, again.status_code, document(again)) == (200, 200, document(first))
    assert document(first)["revoked_at"] is not None
    listed = page(await client.get("/v1/api_clients", headers=headers))
    assert [(item["id"], item["status"]) for item in listed["items"]] == [(made, "revoked")]


@pytest.mark.parametrize("which", ["uuid", "other-prefix", "upper-case", "empty-suffix", "garbage"])
async def test_a_client_that_is_not_named_by_its_public_id_is_not_found(
    client: httpx.AsyncClient, owner: TestPrincipal, which: str
) -> None:
    made = await register_in(client, owner, "test")
    inside = parse_id(API_CLIENT_PREFIX, made)
    named = {
        "uuid": str(inside),
        "other-prefix": format_id("est", inside),
        "upper-case": made.upper(),
        "empty-suffix": f"{API_CLIENT_PREFIX}_",
        "garbage": "x" * 300,
    }[which]
    headers = owner.bearer() | {"Ledger-Mode": "test"}
    for send in (client.get, client.delete):
        response = await send(f"/v1/api_clients/{named}", headers=headers)
        assert (response.status_code, document(response)["code"]) == (404, "api_client_not_found")
        assert len(named) < 6 or named not in response.text


async def test_every_route_needs_the_organization_s_admin_role(
    client: httpx.AsyncClient, local_issuer: LocalIssuer, owner: TestPrincipal
) -> None:
    made = await register_in(client, owner, "test")
    plain = TestPrincipal(local_issuer, subject="2002")
    headers = plain.bearer() | {"Ledger-Mode": "test"}
    for response in (
        await client.get("/v1/api_clients", headers=headers),
        await client.get(f"/v1/api_clients/{made}", headers=headers),
        await client.delete(f"/v1/api_clients/{made}", headers=headers),
    ):
        assert (response.status_code, document(response)["code"]) == (403, "forbidden")
    anonymous = await client.get("/v1/api_clients")
    assert (anonymous.status_code, document(anonymous)["code"]) == (401, "missing_token")


# ── What the client's credential carries ────────────────────────────────────────


async def test_a_registered_machine_user_acts_in_its_mode_without_naming_it_and_in_no_other(
    client: httpx.AsyncClient, local_issuer: LocalIssuer, owner: TestPrincipal
) -> None:
    await register_in(client, owner, "live")
    robot = machine(local_issuer)
    bare = await client.get("/v1/api_clients", headers=robot.bearer())
    assert (bare.status_code, page(bare)["items"][0]["subject"]) == (200, "4001")  # seen in live: its mode
    repeated = await client.get("/v1/api_clients", headers=robot.bearer() | {"Ledger-Mode": "live"})
    assert repeated.status_code == 200
    other = await client.get("/v1/api_clients", headers=robot.bearer() | {"Ledger-Mode": "test"})
    assert (other.status_code, document(other)["code"]) == (403, "mode_mismatch")
    human = await client.get("/v1/api_clients", headers=owner.bearer())  # a human names its mode
    assert (human.status_code, document(human)["code"]) == (400, "mode_required")


async def test_a_registered_machine_user_speaks_its_clients_version_unless_the_request_names_one(
    client: httpx.AsyncClient, local_issuer: LocalIssuer, owner: TestPrincipal, admin: psycopg.AsyncConnection
) -> None:
    await register_in(client, owner, "test")
    await admin.execute("UPDATE identity.api_clients SET api_version = %s", (EARLIER,))
    robot = machine(local_issuer)
    pinned = await client.get("/v1/api_clients", headers=robot.bearer())
    assert pinned.headers[VERSION_HEADER] == EARLIER.isoformat()
    named = await client.get(
        "/v1/api_clients", headers=robot.bearer() | {VERSION_HEADER: CURRENT_VERSION.isoformat()}
    )
    assert named.headers[VERSION_HEADER] == CURRENT_VERSION.isoformat()


async def test_a_revoked_client_s_credential_is_refused_at_once(
    client: httpx.AsyncClient, local_issuer: LocalIssuer, owner: TestPrincipal
) -> None:
    made = await register_in(client, owner, "test")
    await client.delete(f"/v1/api_clients/{made}", headers=owner.bearer() | {"Ledger-Mode": "test"})
    refused = await client.get("/v1/api_clients", headers=machine(local_issuer).bearer())
    assert (refused.status_code, document(refused)["code"]) == (401, "api_client_revoked")


@final
class Seconds:
    """A monotonic clock the test moves."""

    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


async def test_a_revocation_shows_within_the_30_seconds_a_replica_remembers_a_standing(
    client: httpx.AsyncClient, owner: TestPrincipal, module_database: BoundDatabase
) -> None:
    """What the revoking route promises (within 30 s, cached per replica), through identity's own cache."""
    made = await register_in(client, owner, "test")
    clock = Seconds()
    standings = cached_standings(module_database, clock=clock)
    robot = Principal(
        subject=SubjectId("4001"),
        organization=organization_of("1001"),
        provider_organization="1001",
        kind=PrincipalKind.MACHINE,
    )
    assert not (await standings.of(robot)).revoked
    await client.delete(f"/v1/api_clients/{made}", headers=owner.bearer() | {"Ledger-Mode": "test"})
    clock.now += STANDING_TTL - 0.1
    assert not (await standings.of(robot)).revoked  # this replica still remembers it active
    clock.now += 0.2
    assert (await standings.of(robot)).revoked
    assert STANDING_TTL == 30.0  # the documented bound


def test_the_revoking_route_says_how_soon_a_revocation_shows(api: App) -> None:
    paths = PATHS.validate_python(DOCUMENT.validate_python(api.asgi().openapi())["paths"])
    said = " ".join(str(paths["/v1/api_clients/{client_id}"]["delete"]["description"]).split())
    assert "within 30 s, cached per replica" in said


async def test_a_machine_user_that_is_no_client_is_refused_whatever_it_names(
    client: httpx.AsyncClient, local_issuer: LocalIssuer, owner: TestPrincipal
) -> None:
    """A machine acts only as an API client: one its organization never registered is refused."""
    await register_in(client, owner, "test")
    stranger = machine(local_issuer, "4999")
    for headers in (stranger.bearer(), stranger.bearer() | {"Ledger-Mode": "test"}):
        response = await client.get("/v1/api_clients", headers=headers)
        assert (response.status_code, document(response)["code"]) == (403, "api_client_unregistered")


async def test_whoever_an_api_client_names_is_bound_to_its_mode_even_when_it_looks_like_a_person(
    client: httpx.AsyncClient, local_issuer: LocalIssuer, owner: TestPrincipal
) -> None:
    """A machine whose token passes for a person's (its user name is one of the audiences) is still bound to
    its client's mode and revocation: a client's binding never depends on the heuristic (fail closed)."""
    await register_in(client, owner, "live")
    disguised = {"client_id": "other-app", "aud": [local_issuer.audience, "other-app"]}
    token = local_issuer.token(subject="4001", roles=(ORGANIZATION_ADMIN,), claims=disguised)
    headers = {"Authorization": f"Bearer {token}"}
    bare = await client.get("/v1/api_clients", headers=headers)
    other = await client.get("/v1/api_clients", headers=headers | {"Ledger-Mode": "test"})
    assert (bare.status_code, page(bare)["items"][0]["mode"]) == (200, "live")  # its client's mode
    assert (other.status_code, document(other)["code"]) == (403, "mode_mismatch")


async def test_a_person_does_not_register_itself_as_an_api_client(
    client: httpx.AsyncClient, owner: TestPrincipal
) -> None:
    itself = {"subject": owner.subject, "name": "Me"}
    response = await client.post("/v1/api_clients", json=itself, headers=owner.posting("k-me"))
    assert (response.status_code, document(response)["code"]) == (422, "api_client_human")
