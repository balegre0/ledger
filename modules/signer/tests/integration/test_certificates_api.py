"""``/v1/certificates`` through the ``api`` app, forwarded to an in-process signer over gRPC.

Each test starts the ``signer`` app as ``ledger_signer`` (port 0, every caller's token, a local-file KMS's key
made for it), then the ``api`` app of ``ledger_testing`` with ``LEDGER_SIGNER_ADDRESS`` naming it and
the api's own token: identity answers who may do what, the signer installs, lists and retires. What the
signer kept is read back as the superuser reads it.
"""

import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from typing import Final, LiteralString
lazy from collections.abc import AsyncGenerator, Mapping
lazy from pathlib import Path

import httpx
import pytest
from cryptography.x509.oid import NameOID
lazy from fastapi import FastAPI
lazy from psycopg import AsyncConnection

from ledger_platform.runtime.app import App
from ledger_signer.adapters.custody.kms_local import MASTER_KEY_BYTES
from ledger_signer.adapters.grpc.server import SignerServer
from ledger_signer.adapters.http.router import PKCS12, PREFIX, UNLOCK_HEADER
from ledger_testing.api import TestPrincipal, created, document, page
from ledger_testing.database import signer_url
from ledger_testing.modules import app_settings
from ledger_testing.pki import Authority, bundle, distinguished_name
from ledger_testing.waiting import eventually
lazy from ledger_platform.config.settings import Settings
lazy from ledger_testing.issuer import LocalIssuer
lazy from ledger_testing.modules import ModuleUnderTest
lazy from ledger_testing.pki import Issued
lazy from ledger_testing.postgres import WorkerDatabase

TOKENS: Final = {
    "api": "api-token-0123456789abcdefghijklmnopqrstuv",
    "worker": "worker-token-0123456789abcdefghijklmnopq",
    "receiver": "receiver-token-0123456789abcdefghijklmn",
}
PASSWORD: Final = "PASSWORD-SENTINEL-api"
ORGANIZATION_ADMIN: Final = "ledger.organization.admin"
"""The identity provider's project role that registers companies (identity's)."""
COMPANY: Final = {
    "jurisdiction": {"country": "DO"},
    "functional_currency": "DOP",
    "tax_ids": [{"scheme": "do.rnc", "value": "101850043"}],
    "legal_name": "Acme SRL",
}


@pytest.fixture(scope="module")
def authority() -> Authority:
    return Authority.root()


def issued(authority: Authority, name: str) -> Issued:
    """A signing certificate of ``name``, valid from a day ago for a year from now (the signer's clock)."""
    now = datetime.now(UTC)
    return authority.issue(
        distinguished_name((NameOID.COMMON_NAME, name)),
        not_before=now - timedelta(days=1),
        not_after=now + timedelta(days=365),
    )


@pytest.fixture
def key_file(tmp_path: Path) -> Path:
    path = tmp_path / "kms.key"
    path.write_bytes(os.urandom(MASTER_KEY_BYTES))
    return path


@pytest.fixture
def signer_settings(
    migrated: WorkerDatabase, local_issuer: LocalIssuer, monkeypatch: pytest.MonkeyPatch, key_file: Path
) -> Settings:
    """Every caller's token, the KMS's key, any free port, and the database as ``ledger_signer``."""
    for caller, token in TOKENS.items():
        monkeypatch.setenv(f"LEDGER_SIGNER_TOKEN_{caller.upper()}", token)
    monkeypatch.setenv("LEDGER_SIGNER_KMS_KEY_FILE", str(key_file))
    monkeypatch.setenv("LEDGER_SIGNER_GRPC_PORT", "0")
    url = signer_url(migrated)
    return app_settings(database_url=url, direct_url=url, issuer=local_issuer)


@asynccontextmanager
async def started(settings: Settings, module_under_test: ModuleUnderTest) -> AsyncGenerator[str]:
    """The signer's app, started: the address of its gRPC service."""
    app = App("signer", settings=settings, discover=module_under_test.discovering())
    served: FastAPI = app.asgi()
    async with served.router.lifespan_context(served):
        [server] = [
            resource
            for member in app.members
            for resource in member.resources
            if isinstance(resource, SignerServer)
        ]
        yield f"127.0.0.1:{server.port}"


@pytest.fixture
async def signer(signer_settings: Settings, module_under_test: ModuleUnderTest) -> AsyncGenerator[str]:
    async with started(signer_settings, module_under_test) as address:
        yield address


@pytest.fixture
def api(
    settings: Settings, module_under_test: ModuleUnderTest, monkeypatch: pytest.MonkeyPatch, signer: str
) -> App:
    """The ``api`` app, the signer's client given its address (the api's token is set with the others)."""
    monkeypatch.setenv("LEDGER_SIGNER_ADDRESS", signer)
    return App("api", settings=settings, discover=module_under_test.discovering())


@pytest.fixture
def owner(local_issuer: LocalIssuer) -> TestPrincipal:
    """Subject ``2001`` of the provider's organization ``1001``, its administrator: it owns its companies."""
    return TestPrincipal(local_issuer, roles=(ORGANIZATION_ADMIN,))


@pytest.fixture
async def company(client: httpx.AsyncClient, owner: TestPrincipal) -> str:
    """The public id of a company ``owner`` registered through identity's API."""
    return await created(client, "/v1/companies", COMPANY, owner.posting("company-1"))


def uploading(owner: TestPrincipal, company: str, key: str, mode: str = "test") -> dict[str, str]:
    return owner.posting(key, mode) | {
        "Ledger-Company": company,
        UNLOCK_HEADER: PASSWORD,
        "Content-Type": PKCS12,
    }


def reading(owner: TestPrincipal, company: str, mode: str = "test") -> dict[str, str]:
    return owner.bearer() | {"Ledger-Company": company, "Ledger-Mode": mode}


async def installed(client: httpx.AsyncClient, headers: Mapping[str, str], data: bytes) -> httpx.Response:
    """The bundle ``data`` sent to be installed."""
    return await client.post(PREFIX, headers=dict(headers), content=data)


def pkcs12_of(holder: Issued) -> bytes:
    """``holder``'s bundle, under ``PASSWORD``: a new salt each time, so new bytes each time."""
    return bundle(holder, password=PASSWORD)


async def rows(admin: AsyncConnection, query: LiteralString, *parameters: object) -> list[tuple[object, ...]]:
    cursor = await admin.execute(query, parameters)
    return await cursor.fetchall()


async def test_a_certificate_is_installed_listed_read_and_retired(
    client: httpx.AsyncClient,
    owner: TestPrincipal,
    company: str,
    authority: Authority,
    admin: AsyncConnection,
) -> None:
    response = await installed(
        client, uploading(owner, company, "import-1"), pkcs12_of(issued(authority, "Juana Perez"))
    )
    assert response.status_code == 201, response.text
    made = document(response)
    assert response.headers["location"] == f"{PREFIX}/{made['id']}"
    assert (made["object"], made["status"], made["modes"], made["backend"], made["livemode"]) == (
        "certificate",
        "active",
        ["test"],
        "local",
        False,
    )
    assert made["subject"] == "CN=Juana Perez"
    listed = await client.get(PREFIX, headers=reading(owner, company))
    in_live = await client.get(PREFIX, headers=reading(owner, company, "live"))
    read = await client.get(f"{PREFIX}/{made['id']}", headers=reading(owner, company))
    assert [item["id"] for item in page(listed)["items"]] == [made["id"]]
    assert page(in_live)["items"] == []
    assert document(read) == made
    retired = await client.delete(f"{PREFIX}/{made['id']}", headers=reading(owner, company))
    again = await client.delete(f"{PREFIX}/{made['id']}", headers=reading(owner, company))
    assert (retired.status_code, document(retired)["status"], document(retired)["retired_reason"]) == (
        200,
        "retired",
        "retired",
    )
    assert document(again) == document(retired)  # naturally idempotent
    assert page(await client.get(PREFIX, headers=reading(owner, company)))["items"] == []
    every = await client.get(f"{PREFIX}?include_retired=true", headers=reading(owner, company))
    assert [item["status"] for item in page(every)["items"]] == ["retired"]
    audited = await rows(admin, "SELECT action, actor FROM signer.audit_events ORDER BY occurred_at")
    assert [action for action, _ in audited] == ["certificate.install", "certificate.retire"]
    assert {actor for _, actor in audited} == {f"human:{owner.subject}"}


async def test_a_new_import_replaces_the_active_certificate_and_both_stay_visible(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str, authority: Authority
) -> None:
    first = await installed(client, uploading(owner, company, "import-1"), pkcs12_of(issued(authority, "A")))
    second = await installed(client, uploading(owner, company, "import-2"), pkcs12_of(issued(authority, "B")))
    assert [first.status_code, second.status_code] == [201, 201]
    every = page(await client.get(f"{PREFIX}?include_retired=true", headers=reading(owner, company)))
    assert [(item["id"], item["status"], item["retired_reason"]) for item in every["items"]] == [
        (document(second)["id"], "active", None),
        (document(first)["id"], "retired", "replaced"),
    ]


async def test_an_import_retried_with_its_key_is_answered_again_and_the_key_is_kept_per_mode(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str, authority: Authority
) -> None:
    data = pkcs12_of(issued(authority, "Holder"))
    first = await installed(client, uploading(owner, company, "import-1"), data)
    retried = await installed(client, uploading(owner, company, "import-1"), data)
    reused = await installed(client, uploading(owner, company, "import-1"), pkcs12_of(issued(authority, "B")))
    in_live = await installed(client, uploading(owner, company, "import-1", "live"), data)
    assert [first.status_code, retried.status_code, in_live.status_code] == [201, 201, 201]
    assert "idempotent-replayed" not in first.headers
    assert (retried.headers["idempotent-replayed"], document(retried)) == ("true", document(first))
    assert (reused.status_code, document(reused)["code"]) == (422, "idempotency_key_reused")
    assert "idempotent-replayed" not in in_live.headers
    assert document(in_live)["id"] != document(first)["id"]
    assert (document(in_live)["modes"], document(in_live)["livemode"]) == (["live"], True)


async def test_the_signer_s_refusals_reach_the_problem_and_the_password_never_leaves(
    client: httpx.AsyncClient,
    owner: TestPrincipal,
    company: str,
    authority: Authority,
    capsys: pytest.CaptureFixture[str],
) -> None:
    wrong = uploading(owner, company, "import-1") | {UNLOCK_HEADER: "not-the-password"}
    unreadable = await installed(client, wrong, pkcs12_of(issued(authority, "Holder")))
    remote = await client.post(
        f"{PREFIX}/remote",
        headers=owner.posting("import-2") | {"Ledger-Company": company},
        json={"credential": "CREDENTIAL-SENTINEL-api"},
    )
    unknown = await client.get(f"{PREFIX}/crt_01h455vb4pex5vsknk084sn02q", headers=reading(owner, company))
    assert (unreadable.status_code, document(unreadable)["code"]) == (422, "certificate_bundle_unreadable")
    assert (remote.status_code, document(remote)["code"]) == (422, "backend_not_offered")
    assert (unknown.status_code, document(unknown)["code"]) == (404, "certificate_not_found")
    captured = capsys.readouterr()
    seen = [captured.out, captured.err, unreadable.text, remote.text]
    assert [text for text in seen if PASSWORD in text or "CREDENTIAL-SENTINEL-api" in text] == []


async def test_api_is_ready_with_the_signer_stopped_and_its_routes_answer_503(
    signer_settings: Settings,
    settings: Settings,
    module_under_test: ModuleUnderTest,
    monkeypatch: pytest.MonkeyPatch,
    local_issuer: LocalIssuer,
) -> None:
    async with started(signer_settings, module_under_test) as address:
        pass  # started, then stopped: nothing listens at its address any more
    monkeypatch.setenv("LEDGER_SIGNER_ADDRESS", address)
    served = App("api", settings=settings, discover=module_under_test.discovering()).asgi()
    owner = TestPrincipal(local_issuer, roles=(ORGANIZATION_ADMIN,))
    async with served.router.lifespan_context(served):
        transport = httpx.ASGITransport(app=served)
        async with httpx.AsyncClient(transport=transport, base_url="http://ledger.test") as client:

            async def ready() -> bool:
                return (await client.get("/readyz")).status_code == HTTPStatus.OK

            await eventually(ready)
            company = await created(client, "/v1/companies", COMPANY, owner.posting("company-1"))
            listed = await client.get(PREFIX, headers=reading(owner, company))
    assert (listed.status_code, document(listed)["code"]) == (503, "signer_unavailable")
    assert listed.headers["retry-after"] == "5"
