"""``/v1/certificates`` through the ``api`` app, forwarded to an in-process signer over gRPC.

Each test starts the ``signer`` app as ``ledger_signer`` (port 0, every caller's token, a local-file KMS's key
made for it), then the ``api`` app of ``ledger_testing`` with ``LEDGER_SIGNER_ADDRESS`` naming it and
the api's own token: identity answers who may do what, the signer installs, lists and retires. What the
signer kept is read back as the superuser reads it; whether the signer was called at all, from its metrics.
One test serves the signer's service itself, over a certification entity, so that the remote route is offered.
"""

import asyncio
import base64
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from typing import Final, LiteralString, final
lazy from collections.abc import AsyncGenerator, Mapping
lazy from contextlib import AbstractAsyncContextManager
lazy from pathlib import Path

import httpx
import pytest
from cryptography.x509.oid import NameOID
from prometheus_client import CollectorRegistry
lazy from fastapi import FastAPI
lazy from psycopg import AsyncConnection

from ledger_platform.data.bound import BoundDatabase
from ledger_platform.data.unit_of_work import JobPolicy
from ledger_platform.grpc.server import ServerOptions
from ledger_platform.runtime.app import App
from ledger_signer._manifest import MANIFEST
from ledger_signer.adapters.custody.kms_local import MASTER_KEY_BYTES, LocalFileKms
from ledger_signer.adapters.grpc.server import SignerServer
from ledger_signer.adapters.http.router import PKCS12, PREFIX, UNLOCK_HEADER
from ledger_signer.adapters.serving import build, caller_tokens
from ledger_signer.adapters.settings import SignerSettings
from ledger_signer.application.notices import EXPIRY_NOTICE
from ledger_testing.api import TestPrincipal, created, document, page
from ledger_testing.database import signer_url
from ledger_testing.modules import QuietSignals, app_settings
from ledger_testing.pki import bundle, distinguished_name
from ledger_testing.waiting import eventually
lazy from ledger_kernel.clock import Clock
lazy from ledger_platform.config.settings import Settings
lazy from ledger_platform.data.database import Database
lazy from ledger_signer.application.ports import CertificationEntity
lazy from ledger_testing.issuer import LocalIssuer
lazy from ledger_testing.modules import ModuleUnderTest
lazy from ledger_testing.pki import Authority, Issued
lazy from ledger_testing.postgres import WorkerDatabase

TOKENS: Final = {
    "api": "api-token-0123456789abcdefghijklmnopqrstuv",
    "worker": "worker-token-0123456789abcdefghijklmnopq",
    "receiver": "receiver-token-0123456789abcdefghijklmn",
}
PASSWORD: Final = "contraseña-SENTINEL"
"""Past ASCII on purpose: the header carries it as base64 of its UTF-8."""
UNLOCK: Final = base64.b64encode(PASSWORD.encode()).decode()
CREDENTIAL: Final = "CREDENTIAL-SENTINEL"
"""The credential the conftest's entity signs for."""
SENTINELS: Final = (PASSWORD, UNLOCK, CREDENTIAL)
ORGANIZATION_ADMIN: Final = "ledger.organization.admin"
"""The identity provider's project role that registers companies (identity's)."""
COMPANY: Final = {
    "jurisdiction": {"country": "DO"},
    "functional_currency": "DOP",
    "tax_ids": [{"scheme": "do.rnc", "value": "101850043"}],
    "legal_name": "Acme SRL",
}
OTHER_COMPANY: Final = COMPANY | {
    "tax_ids": [{"scheme": "do.rnc", "value": "130000012"}],
    "legal_name": "Otra SRL",
}
CALLS: Final = "ledger_grpc_request_duration_seconds_count"
"""The signer's count of the calls it answered, per method and status."""


@final
@dataclass(frozen=True, slots=True)
class Running:
    """The signer, started: where its service listens, and a client of its probes and metrics."""

    address: str
    probes: httpx.AsyncClient

    async def calls(self) -> int:
        """How many calls the signer has answered so far."""
        exported = (await self.probes.get("/metrics")).text
        return sum(
            int(float(line.rsplit(" ", 1)[1])) for line in exported.splitlines() if line.startswith(CALLS)
        )


def issued(authority: Authority, name: str) -> Issued:
    """A signing certificate of ``name``, valid from a day ago for a year from now (the signer's clock)."""
    now = datetime.now(UTC)
    return authority.issue(
        distinguished_name((NameOID.COMMON_NAME, name)),
        not_before=now - timedelta(days=1),
        not_after=now + timedelta(days=365),
    )


def pkcs12_of(holder: Issued) -> bytes:
    """``holder``'s bundle, under ``PASSWORD``: a new salt each time, so new bytes each time."""
    return bundle(holder, password=PASSWORD)


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
async def started(settings: Settings, module_under_test: ModuleUnderTest) -> AsyncGenerator[Running]:
    """The signer's app, made now (its log handler writes to this moment's ``sys.stdout``) and started."""
    app = App("signer", settings=settings, discover=module_under_test.discovering())
    served: FastAPI = app.asgi()
    async with served.router.lifespan_context(served):
        [server] = [
            resource
            for member in app.members
            for resource in member.resources
            if isinstance(resource, SignerServer)
        ]
        transport = httpx.ASGITransport(app=served)
        async with httpx.AsyncClient(transport=transport, base_url="http://signer.test") as probes:
            yield Running(f"127.0.0.1:{server.port}", probes)


@asynccontextmanager
async def api_of(
    address: str, settings: Settings, module_under_test: ModuleUnderTest, monkeypatch: pytest.MonkeyPatch
) -> AsyncGenerator[httpx.AsyncClient]:
    """The ``api`` app reaching the signer at ``address``, made now and started: a client of it."""
    monkeypatch.setenv("LEDGER_SIGNER_ADDRESS", address)
    served = App("api", settings=settings, discover=module_under_test.discovering()).asgi()
    async with served.router.lifespan_context(served):
        transport = httpx.ASGITransport(app=served, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://ledger.test") as client:
            yield client


@final
@dataclass(frozen=True, slots=True)
class Stack:
    """What a test starts the signer's app and the ``api`` app with, inside itself."""

    signer_settings: Settings
    api_settings: Settings
    module: ModuleUnderTest
    monkeypatch: pytest.MonkeyPatch

    def signer(self) -> AbstractAsyncContextManager[Running]:
        """The signer's app, started."""
        return started(self.signer_settings, self.module)

    def api(self, address: str) -> AbstractAsyncContextManager[httpx.AsyncClient]:
        """The ``api`` app reaching the signer at ``address``, started: a client of it."""
        return api_of(address, self.api_settings, self.module, self.monkeypatch)


@pytest.fixture
def stack(
    signer_settings: Settings,
    settings: Settings,
    module_under_test: ModuleUnderTest,
    monkeypatch: pytest.MonkeyPatch,
) -> Stack:
    return Stack(signer_settings, settings, module_under_test, monkeypatch)


@pytest.fixture
async def signer(signer_settings: Settings, module_under_test: ModuleUnderTest) -> AsyncGenerator[Running]:
    async with started(signer_settings, module_under_test) as running:
        yield running


@pytest.fixture
def api(
    settings: Settings, module_under_test: ModuleUnderTest, monkeypatch: pytest.MonkeyPatch, signer: Running
) -> App:
    """The ``api`` app, the signer's client given its address (the api's token is set with the others)."""
    monkeypatch.setenv("LEDGER_SIGNER_ADDRESS", signer.address)
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
        UNLOCK_HEADER: UNLOCK,
        "Content-Type": PKCS12,
    }


def reading(owner: TestPrincipal, company: str, mode: str = "test") -> dict[str, str]:
    return owner.bearer() | {"Ledger-Company": company, "Ledger-Mode": mode}


async def installed(
    client: httpx.AsyncClient, headers: Mapping[str, str], data: bytes, query: str = ""
) -> httpx.Response:
    """The bundle ``data`` sent to be installed."""
    return await client.post(f"{PREFIX}{query}", headers=dict(headers), content=data)


def code_of(response: httpx.Response) -> tuple[int, object]:
    return response.status_code, document(response)["code"]


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
        client, uploading(owner, company, "import-1"), pkcs12_of(issued(authority, "Juana"))
    )
    assert response.status_code == 201, response.text  # the password, past ASCII, opened the bundle
    made = document(response)
    assert response.headers["location"] == f"{PREFIX}/{made['id']}"
    assert (made["object"], made["status"], made["modes"], made["backend"], made["livemode"]) == (
        "certificate",
        "active",
        ["test"],
        "local",
        False,
    )
    assert made["subject"] == "CN=Juana"
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
    assert code_of(reused) == (422, "idempotency_key_reused")
    assert "idempotent-replayed" not in in_live.headers
    assert document(in_live)["id"] != document(first)["id"]
    assert (document(in_live)["modes"], document(in_live)["livemode"]) == (["live"], True)


async def test_a_test_mode_request_never_installs_nor_retires_what_serves_live(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str, authority: Authority, signer: Running
) -> None:
    """ADR 0007: a test-mode credential is safe to hand out; only a live one changes what signs in live."""
    data = pkcs12_of(issued(authority, "Both"))
    before = await signer.calls()
    for query in ("?modes=live", "?modes=test&modes=live"):
        refused = await installed(client, uploading(owner, company, "import-t"), data, query)
        assert code_of(refused) == (422, "certificate_modes_not_allowed")
    assert await signer.calls() == before  # refused at the edge, before the bundle is read
    both = await installed(
        client, uploading(owner, company, "import-l", "live"), data, "?modes=test&modes=live"
    )
    assert (both.status_code, document(both)["modes"]) == (201, ["test", "live"])
    path = f"{PREFIX}/{document(both)['id']}"
    from_test = await client.delete(path, headers=reading(owner, company))
    assert code_of(from_test) == (409, "certificate_serves_live")
    assert document(await client.get(path, headers=reading(owner, company)))["status"] == "active"
    from_live = await client.delete(path, headers=reading(owner, company, "live"))
    assert (from_live.status_code, document(from_live)["status"]) == (200, "retired")


async def test_the_signer_s_refusals_reach_the_problem(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str, authority: Authority
) -> None:
    wrong = uploading(owner, company, "import-1") | {UNLOCK_HEADER: base64.b64encode(b"not-it").decode()}
    unreadable = await installed(client, wrong, pkcs12_of(issued(authority, "Holder")))
    remote = await client.post(
        f"{PREFIX}/remote",
        headers=owner.posting("import-2") | {"Ledger-Company": company},
        json={"credential": "c"},
    )
    unknown = await client.get(f"{PREFIX}/crt_01h455vb4pex5vsknk084sn02q", headers=reading(owner, company))
    assert code_of(unreadable) == (422, "certificate_bundle_unreadable")
    assert code_of(remote) == (422, "backend_not_offered")
    assert code_of(unknown) == (404, "certificate_not_found")


async def test_who_may_not_act_in_the_company_never_reaches_the_signer(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str, authority: Authority, signer: Running
) -> None:
    """The signer trusts the company and actor the api forwards: identity answers first (Task 12's note)."""
    made = await installed(
        client, uploading(owner, company, "import-1"), pkcs12_of(issued(authority, "Kept"))
    )
    path = f"{PREFIX}/{document(made)['id']}"
    other_company = await created(client, "/v1/companies", OTHER_COMPANY, owner.posting("company-2"))
    viewer, clerk = (TestPrincipal(owner.issuer, subject=subject) for subject in ("3001", "3002"))
    for principal, role, of in ((viewer, "viewer", company), (clerk, "accountant", other_company)):
        granted = await client.put(
            f"/v1/companies/{of}/memberships/{principal.subject}", json={"role": role}, headers=owner.bearer()
        )
        assert granted.status_code in {200, 201}, granted.text
    stranger = TestPrincipal(owner.issuer, subject="4001", organization="9999", roles=(ORGANIZATION_ADMIN,))
    data = pkcs12_of(issued(authority, "Refused"))
    before = await signer.calls()
    answers = [
        code_of(await installed(client, uploading(stranger, company, "s-1"), data)),
        code_of(await client.get(PREFIX, headers=reading(stranger, company))),
        code_of(await installed(client, uploading(clerk, company, "c-1"), data)),
        code_of(await client.delete(path, headers=reading(clerk, company))),
        code_of(await installed(client, uploading(viewer, company, "v-1"), data)),
        code_of(await client.delete(path, headers=reading(viewer, company))),
    ]
    assert answers == [(404, "company_not_found")] * 4 + [(403, "permission_denied")] * 2
    assert await signer.calls() == before
    assert (await client.get(path, headers=reading(viewer, company))).status_code == 200  # a viewer reads


async def test_a_bundle_that_stops_coming_is_408_not_503(
    stack: Stack, owner: TestPrincipal, authority: Authority
) -> None:
    """The signer gives up on the stream first and says so; the api, waiting two seconds more, hears it."""
    stack.monkeypatch.setenv("LEDGER_SIGNER_IMPORT_DEADLINE_SECONDS", "1")
    data = pkcs12_of(issued(authority, "Slow"))

    async def stalled() -> AsyncGenerator[bytes]:
        yield data[:100]
        await asyncio.Event().wait()

    async with stack.signer() as running, stack.api(running.address) as client:
        company = await created(client, "/v1/companies", COMPANY, owner.posting("company-1"))
        async with asyncio.timeout(10):
            response = await client.post(
                PREFIX, headers=uploading(owner, company, "import-1"), content=stalled()
            )
    assert code_of(response) == (408, "signer_import_too_slow")


@pytest.fixture
async def offering_remote(
    stack: Stack, database: Database, key_file: Path, entity: CertificationEntity, clock: Clock
) -> AsyncGenerator[str]:
    """The signer's service over the conftest's entity and clock, both backends offered: its address."""
    stack.monkeypatch.setenv("LEDGER_SIGNER_BACKENDS", "local,remote")
    settings = SignerSettings()
    kms = LocalFileKms(key_file, environment="test")
    units = BoundDatabase(  # as the signer's role binds them: its events, and the job kind it enqueues
        database,
        schema="signer",
        source="signer",
        signals=QuietSignals(),
        publishes=frozenset(event.type for event in MANIFEST.events.publishes),
        jobs={EXPIRY_NOTICE: JobPolicy(max_attempts=20, deadline=None)},
    )
    built = build(units, settings=settings, kms=kms, entity=entity, clock=clock)
    server = SignerServer(
        built.servicer,
        port=0,
        registry=CollectorRegistry(),
        callers=caller_tokens(settings),
        options=ServerOptions(grace=0.1),
    )
    async with server:
        yield f"127.0.0.1:{server.port}"


async def test_one_key_names_one_import_whichever_route_it_came_by(
    stack: Stack, offering_remote: str, owner: TestPrincipal, authority: Authority, clock: Clock
) -> None:
    """The signer keeps the key for one operation, *import*: a bundle's key reused for a credential is 422."""
    at = clock()  # the conftest's clock, which this signer reads
    holder = authority.issue(
        distinguished_name((NameOID.COMMON_NAME, "L")),
        not_before=at - timedelta(days=1),
        not_after=at + timedelta(days=365),
    )
    async with stack.api(offering_remote) as client:
        company = await created(client, "/v1/companies", COMPANY, owner.posting("company-1"))
        local = await installed(client, uploading(owner, company, "one-key"), pkcs12_of(holder))
        remote_headers = owner.posting("one-key") | {"Ledger-Company": company}
        remote = await client.post(
            f"{PREFIX}/remote", headers=remote_headers, json={"credential": CREDENTIAL}
        )
        fresh_headers = owner.posting("another-key") | {"Ledger-Company": company}
        fresh = await client.post(f"{PREFIX}/remote", headers=fresh_headers, json={"credential": CREDENTIAL})
    assert local.status_code == 201, local.text
    assert code_of(remote) == (422, "idempotency_key_reused")
    assert (fresh.status_code, document(fresh)["backend"]) == (201, "remote")


async def test_the_password_and_the_credential_never_leave_in_a_log_or_a_problem(
    stack: Stack, owner: TestPrincipal, authority: Authority, capsys: pytest.CaptureFixture[str]
) -> None:
    """Both apps are made inside the test, once ``capsys`` holds ``sys.stdout``: their lines are captured."""
    async with stack.signer() as running, stack.api(running.address) as client:
        company = await created(client, "/v1/companies", COMPANY, owner.posting("company-1"))
        wrong = uploading(owner, company, "import-1") | {UNLOCK_HEADER: base64.b64encode(b"wrong").decode()}
        answers = [
            await installed(client, wrong, pkcs12_of(issued(authority, "Holder"))),
            await installed(
                client, uploading(owner, company, "import-2"), pkcs12_of(issued(authority, "Holder"))
            ),
            await client.post(
                f"{PREFIX}/remote",
                headers=owner.posting("import-3") | {"Ledger-Company": company},
                json={"credential": CREDENTIAL},
            ),
        ]
    assert [response.status_code for response in answers] == [422, 201, 422]
    captured = capsys.readouterr()
    # Both apps wrote here (each closes its pools; logging is the process's, configured last by the api's):
    # what follows reads their lines, not an empty capture.
    assert captured.out.count("app.pools_closing") == 2
    seen = [captured.out, captured.err, *(response.text for response in answers)]
    assert [secret for secret in SENTINELS for text in seen if secret in text] == []


async def test_api_is_ready_with_the_signer_stopped_and_its_routes_answer_503(
    stack: Stack, owner: TestPrincipal
) -> None:
    async with stack.signer() as running:
        pass  # started, then stopped: nothing listens at its address any more
    async with stack.api(running.address) as client:

        async def ready() -> bool:
            return (await client.get("/readyz")).status_code == HTTPStatus.OK

        await eventually(ready)
        company = await created(client, "/v1/companies", COMPANY, owner.posting("company-1"))
        listed = await client.get(PREFIX, headers=reading(owner, company))
    assert code_of(listed) == (503, "signer_unavailable")
    assert listed.headers["retry-after"] == "5"
