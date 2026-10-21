"""``/v1/certificates`` on the chassis's app, over a fake of what the routes ask the signer.

The app is ``create_app``'s (its body limits, its problems), its caller proven by a token that spells its
subject, and what each subject may do in the one company is the test's own table. The fake records each call
(the call, the tenant, the request, the password or the credential, every chunk of a bundle as it arrived) and
answers as a test tells it, or fails as the signer's client would.
"""

import asyncio
import base64
import dataclasses
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from typing import Final, final, override
lazy from collections.abc import AsyncGenerator, AsyncIterable, Mapping, Sequence

import grpc
import httpx
import pytest
from opentelemetry.sdk.trace import TracerProvider
from prometheus_client import CollectorRegistry
from pydantic import SecretStr, TypeAdapter
from structlog.testing import capture_logs
lazy from fastapi import FastAPI

from ledger_fiscal_kit.ports import SignerUnavailableError
from ledger_kernel.access import Principal, PrincipalKind, SubjectId, parse_permission
from ledger_kernel.ids import COMPANY_PREFIX, CompanyId, format_id, new_id
from ledger_kernel.tenancy import Mode, Tenant
from ledger_platform.auth import PermissionSource
from ledger_platform.auth.providers.zitadel import organization_of
from ledger_platform.config.settings import Settings
from ledger_platform.grpc.client import RemoteDefectError, RemoteNotFoundError
from ledger_platform.http import Authentication
from ledger_platform.http.app import ApiInfo, Observability, create_app
from ledger_platform.http.deps import Verifier, unguarded_routes
from ledger_platform.http.idempotency import IdempotencyKeyInUseError, IdempotencyKeyReusedError
from ledger_platform.http.limits import MAX_UPLOAD_BYTES
from ledger_platform.http.proving import authenticated
from ledger_platform.observability.health import Health
from ledger_platform.observability.logs import configure_logging
from ledger_platform.runtime.calls import Call
from ledger_signer._manifest import MANIFEST
from ledger_signer.adapters.grpc.client import SignerDefectError
from ledger_signer.adapters.grpc.codec import (
    ImportChunksUnexpectedError,
    ImportHeaderFirstError,
    ImportTooSlowError,
)
from ledger_signer.adapters.http.models import CERTIFICATE_PREFIX, CertificateResource
from ledger_signer.adapters.http.problems import RETRY_AFTER_SECONDS
from ledger_signer.adapters.http.router import (
    CERTIFICATES_MANAGE,
    CERTIFICATES_READ,
    PREFIX,
    UNLOCK_HEADER,
    Routes,
    router,
)
from ledger_signer.api import (
    BackendKind,
    CertificateId,
    CertificateNotFoundError,
    CertificateStatus,
    CertificateView,
)
from ledger_signer.application.certificates import CertificateServesLiveError
from ledger_signer.application.importing import MAX_BUNDLE_BYTES, ImportTooLargeError
from ledger_signer.application.ports import (
    ActiveCertificateTakenError,
    BundleUnreadableError,
    ImportOutcome,
)
from ledger_signer.application.service import CertificateService
from ledger_testing.api import document, page
lazy from ledger_kernel.access import Permission
lazy from ledger_kernel.errors import LedgerError
lazy from ledger_platform.http.metrics import AsgiMessage, AsgiScope
lazy from ledger_platform.runtime.calls import SystemCall
lazy from ledger_signer.application.importing import ImportRequest
lazy from ledger_signer.application.ports import Secret

ORGANIZATION: Final = organization_of("1001")
COMPANY: Final = CompanyId(new_id())
COMPANY_REF: Final = format_id(COMPANY_PREFIX, COMPANY)
OTHER_COMPANY: Final = format_id(COMPANY_PREFIX, new_id())
AT: Final = datetime(2026, 10, 19, 12, tzinfo=UTC)
PASSWORD: Final = "PASSWORD-SENTINEL-4kQ"
UNLOCK: Final = base64.b64encode(PASSWORD.encode()).decode()
"""``PASSWORD`` as the header carries it."""
CREDENTIAL: Final = "CREDENTIAL-SENTINEL-8wZ"
SENTINELS: Final = (PASSWORD, UNLOCK, CREDENTIAL)
CHUNK: Final = 64 * 1024
PKCS12: Final = "application/x-pkcs12"
INVOICES_READ: Final = parse_permission("invoicing.invoices.read")
GRANTS: Final[Mapping[str, frozenset[Permission]]] = {
    "owner": frozenset({CERTIFICATES_READ, CERTIFICATES_MANAGE}),
    "viewer": frozenset({CERTIFICATES_READ}),
    "invoicer": frozenset({INVOICES_READ}),
}
"""What each subject may do in ``COMPANY``; a subject not here holds no role there (nor anyone elsewhere)."""
ACTIVE: Final = CertificateView(
    id=CertificateId(new_id()),
    organization=ORGANIZATION,
    company=COMPANY,
    modes=frozenset({Mode.LIVE, Mode.TEST}),
    backend=BackendKind.LOCAL,
    status=CertificateStatus.ACTIVE,
    fingerprint_sha256=bytes(range(32)),
    serial_number="0a1b",
    subject="CN=Juana Perez,serialNumber=IDCDO-00100000001",
    issuer="CN=Avansi CA",
    not_before=AT - timedelta(days=1),
    not_after=AT + timedelta(days=364),
    installed_at=AT,
    retired_at=None,
    retired_reason=None,
    version=1,
)
RETIRED: Final = dataclasses.replace(
    ACTIVE,
    id=CertificateId(new_id()),
    modes=frozenset({Mode.TEST}),
    status=CertificateStatus.RETIRED,
    installed_at=AT - timedelta(days=30),
    retired_at=AT,
    retired_reason="replaced",
    version=2,
)
ERRORS: Final = TypeAdapter(list[dict[str, str]])


@final
class Fake(CertificateService):
    """What the routes ask the signer, recorded; answered as a test says, or failing as it says."""

    def __init__(self) -> None:
        self.listed: Sequence[CertificateView] = (ACTIVE, RETIRED)
        self.replayed = False
        self.failure: Exception | None = None
        self.calls: list[tuple[str, Call, Tenant]] = []
        self.requests: list[ImportRequest] = []
        self.secrets: list[str] = []
        self.chunks: list[bytes] = []
        self.first_chunk = asyncio.Event()
        self.include_retired: list[bool] = []
        self.retired: list[CertificateId] = []

    def _record(self, method: str, call: Call | SystemCall, tenant: Tenant) -> None:
        assert isinstance(call, Call)  # a request's: its caller, in the request's company
        self.calls.append((method, call, tenant))
        if self.failure is not None:
            raise self.failure

    def _outcome(self) -> ImportOutcome:
        return ImportOutcome(view=ACTIVE, replayed=self.replayed)

    @override
    async def describe(
        self, call: Call | SystemCall, tenant: Tenant, *, include_retired: bool
    ) -> tuple[CertificateView, ...]:
        self._record("describe", call, tenant)
        self.include_retired.append(include_retired)
        listed = tuple(view for view in self.listed if tenant.mode in view.modes)
        return listed if include_retired else tuple(view for view in listed if view.status is ACTIVE.status)

    @override
    async def retire(
        self, call: Call | SystemCall, company: Tenant, certificate: CertificateId
    ) -> CertificateView:
        self._record("retire", call, company)
        self.retired.append(certificate)
        return dataclasses.replace(
            ACTIVE, status=CertificateStatus.RETIRED, retired_at=AT, retired_reason="retired"
        )

    @override
    async def import_local(
        self, call: Call | SystemCall, request: ImportRequest, password: Secret, bundle: AsyncIterable[bytes]
    ) -> ImportOutcome:
        self.requests.append(request)
        self.secrets.append(password.reveal())
        async for chunk in bundle:
            if chunk:
                self.chunks.append(chunk)
                self.first_chunk.set()
        self._record("import_local", call, request.tenant)
        return self._outcome()

    @override
    async def import_remote(
        self, call: Call | SystemCall, request: ImportRequest, credential: Secret
    ) -> ImportOutcome:
        self.requests.append(request)
        self.secrets.append(credential.reveal())
        self._record("import_remote", call, request.tenant)
        return self._outcome()


@final
class ByToken(Verifier):
    """Proves a token to the subject it spells, a person of the one organization."""

    @override
    async def verify(self, token: str) -> Principal:
        return Principal(
            subject=SubjectId(token),
            organization=ORGANIZATION,
            provider_organization="1001",
            kind=PrincipalKind.HUMAN,
        )


@final
class Grants(PermissionSource):
    """``GRANTS`` in ``COMPANY``; nothing anywhere else."""

    @override
    async def permissions(self, principal: Principal, company: CompanyId) -> frozenset[Permission]:
        return GRANTS.get(principal.subject, frozenset()) if company == COMPANY else frozenset()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    del app
    yield


def app_of(service: CertificateService) -> FastAPI:
    authentication = Authentication(authenticated(ByToken()), None)
    routes = Routes.build(authentication=authentication, permissions=Grants(), service=service)
    observability = Observability(
        health=Health(), registry=CollectorRegistry(), tracer_provider=TracerProvider()
    )
    return create_app(
        info=ApiInfo(title="t", version="1"),
        observability=observability,
        lifespan=lifespan,
        routers=[router(routes)],
    )


@pytest.fixture
def fake() -> Fake:
    return Fake()


@pytest.fixture
async def client(fake: Fake) -> AsyncGenerator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app_of(fake), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://ledger.test") as opened:
        yield opened


def reading(subject: str = "owner", company: str = COMPANY_REF, mode: str = "test") -> dict[str, str]:
    return {"Authorization": f"Bearer {subject}", "Ledger-Company": company, "Ledger-Mode": mode}


def posting(subject: str = "owner", key: str = "import-1", company: str = COMPANY_REF) -> dict[str, str]:
    return reading(subject, company) | {"Idempotency-Key": key}


def uploading(subject: str = "owner", key: str = "import-1", unlock: str = UNLOCK) -> dict[str, str]:
    return posting(subject, key) | {UNLOCK_HEADER: unlock, "Content-Type": PKCS12}


def unlocking(password: str) -> str:
    """``password`` as the header carries it: base64 of its UTF-8."""
    return base64.b64encode(password.encode()).decode()


def answered(response: httpx.Response) -> tuple[int, object]:
    return response.status_code, document(response)["code"]


def ref(view: CertificateView) -> str:
    """``view``'s public id."""
    return format_id(CERTIFICATE_PREFIX, view.id)


async def chunked(*parts: bytes) -> AsyncGenerator[bytes]:
    for part in parts:
        yield part


# ── What each route requires ───────────────────────────────────────────────────


def test_the_routes_require_the_permissions_the_manifest_declares() -> None:
    declared = {permission.code: frozenset(permission.roles) for permission in MANIFEST.permissions}
    assert declared == {
        CERTIFICATES_READ: frozenset({"owner", "admin", "accountant", "issuer", "viewer"}),
        CERTIFICATES_MANAGE: frozenset({"owner", "admin"}),
    }
    assert [declared_id.prefix for declared_id in MANIFEST.ids] == [CERTIFICATE_PREFIX]
    assert MANIFEST.http is not None
    assert MANIFEST.http.prefixes == (PREFIX,)


def test_every_route_is_for_bearers_of_a_token_and_declares_what_it_requires(fake: Fake) -> None:
    assert unguarded_routes(app_of(fake).routes) == ()


type Sending = tuple[str, str, Mapping[str, str], bytes | None]
"""A request: its method, its path, the headers other than the caller's, its body."""


REQUESTS: Final[Mapping[str, Sending]] = {
    "import": (
        "POST",
        PREFIX,
        {"Idempotency-Key": "k", UNLOCK_HEADER: UNLOCK, "Content-Type": PKCS12},
        b"pkcs12",
    ),
    "import_remote": (
        "POST",
        f"{PREFIX}/remote",
        {"Idempotency-Key": "k", "Content-Type": "application/json"},
        b'{"credential": "' + CREDENTIAL.encode() + b'"}',
    ),
    "list": ("GET", PREFIX, {}, None),
    "read": ("GET", f"{PREFIX}/{format_id(CERTIFICATE_PREFIX, ACTIVE.id)}", {}, None),
    "retire": ("DELETE", f"{PREFIX}/{format_id(CERTIFICATE_PREFIX, ACTIVE.id)}", {}, None),
}
ROUTES: Final = tuple(REQUESTS)
MANAGING: Final = ("import", "import_remote", "retire")


async def send(
    client: httpx.AsyncClient, request: Sending, subject: str, company: str = COMPANY_REF
) -> httpx.Response:
    method, path, headers, body = request
    return await client.request(method, path, headers=reading(subject, company) | dict(headers), content=body)


@pytest.mark.parametrize("route", ROUTES)
async def test_a_caller_without_a_role_in_the_company_finds_no_company(
    client: httpx.AsyncClient, fake: Fake, route: str
) -> None:
    request = REQUESTS[route]
    stranger = await send(client, request, "stranger")
    other = await send(client, request, "owner", OTHER_COMPANY)
    nobody = await client.request(request[0], request[1], headers=dict(request[2]), content=request[3])
    assert [answered(stranger), answered(other)] == [(404, "company_not_found")] * 2
    assert nobody.status_code == 401
    assert fake.calls == []


@pytest.mark.parametrize("route", ROUTES)
async def test_a_caller_without_the_permission_is_refused(
    client: httpx.AsyncClient, fake: Fake, route: str
) -> None:
    request = REQUESTS[route]
    refused = await send(client, request, "viewer" if route in MANAGING else "invoicer")
    assert answered(refused) == (403, "permission_denied")
    assert fake.calls == []


@pytest.mark.parametrize("route", ROUTES)
async def test_every_route_acts_in_the_request_s_mode(
    client: httpx.AsyncClient, fake: Fake, route: str
) -> None:
    method, path, headers, body = REQUESTS[route]
    without = {name: value for name, value in reading().items() if name != "Ledger-Mode"} | dict(headers)
    response = await client.request(method, path, headers=without, content=body)
    assert answered(response) == (400, "mode_required")
    assert fake.calls == []


# ── Importing a PKCS#12 bundle ─────────────────────────────────────────────────


async def test_an_import_streams_the_bundle_to_the_signer_and_answers_the_certificate(
    client: httpx.AsyncClient, fake: Fake
) -> None:
    response = await client.post(PREFIX, headers=uploading(), content=b"pkcs12-bytes")
    assert response.status_code == 201, response.text
    made = document(response)
    assert response.headers["location"] == f"{PREFIX}/{made['id']}"
    assert "idempotent-replayed" not in response.headers
    assert made == {
        "object": "certificate",
        "id": ref(ACTIVE),
        "livemode": False,
        "modes": ["test", "live"],
        "backend": "local",
        "status": "active",
        "fingerprint_sha256": bytes(range(32)).hex(),
        "serial_number": "0a1b",
        "subject": ACTIVE.subject,
        "issuer": "CN=Avansi CA",
        "not_before": "2026-10-18T12:00:00Z",
        "not_after": "2027-10-18T12:00:00Z",
        "retired_at": None,
        "retired_reason": None,
        "version": 1,
        "created_at": "2026-10-19T12:00:00Z",
        "updated_at": "2026-10-19T12:00:00Z",
    }
    assert CertificateResource.model_validate_json(response.content).id == ref(ACTIVE)
    [request] = fake.requests
    tenant = Tenant(ORGANIZATION, COMPANY, Mode.TEST)
    assert (request.tenant, request.modes, request.idempotency_key) == (
        tenant,
        frozenset({Mode.TEST}),
        "import-1",
    )
    assert (fake.secrets, b"".join(fake.chunks)) == ([PASSWORD], b"pkcs12-bytes")
    [(method, call, _)] = fake.calls
    assert (method, call.principal.subject, call.scope, bool(call.request_id)) == (
        "import_local",
        "owner",
        tenant,
        True,
    )


async def test_a_live_import_serves_the_modes_the_query_names(client: httpx.AsyncClient, fake: Fake) -> None:
    in_live = posting(key="a") | {"Ledger-Mode": "live", UNLOCK_HEADER: UNLOCK, "Content-Type": PKCS12}
    both = await client.post(f"{PREFIX}?modes=live&modes=test&modes=live", headers=in_live, content=b"p")
    live = await client.post(f"{PREFIX}?modes=live", headers=in_live | {"Idempotency-Key": "b"}, content=b"p")
    other = await client.post(
        f"{PREFIX}?modes=sandbox", headers=in_live | {"Idempotency-Key": "c"}, content=b"p"
    )
    assert [both.status_code, live.status_code, answered(other)] == [201, 201, (422, "invalid_request")]
    assert [request.modes for request in fake.requests] == [{Mode.LIVE, Mode.TEST}, {Mode.LIVE}]
    assert {request.tenant.mode for request in fake.requests} == {Mode.LIVE}


@pytest.mark.parametrize(
    ("mode", "named"),
    [("test", ("live",)), ("test", ("test", "live")), ("live", ("test",))],
    ids=["test names live", "test names both", "live names test alone"],
)
async def test_an_import_names_only_the_modes_its_request_s_mode_may_install(
    client: httpx.AsyncClient, fake: Fake, mode: str, named: tuple[str, ...]
) -> None:
    """A test-mode credential never changes what signs in live (ADR 0007): refused before the bundle."""
    query = "&".join(f"modes={word}" for word in named)
    refused = await client.post(
        f"{PREFIX}?{query}", headers=uploading() | {"Ledger-Mode": mode}, content=b"p"
    )
    remote_body = {"credential": CREDENTIAL, "modes": list(named)}
    remote = await client.post(
        f"{PREFIX}/remote", headers=posting() | {"Ledger-Mode": mode}, json=remote_body
    )
    assert [answered(refused), answered(remote)] == [(422, "certificate_modes_not_allowed")] * 2
    assert (fake.requests, fake.chunks) == ([], [])


async def test_a_replayed_import_says_so(client: httpx.AsyncClient, fake: Fake) -> None:
    fake.replayed = True
    response = await client.post(PREFIX, headers=uploading(), content=b"p")
    assert (response.status_code, response.headers["idempotent-replayed"]) == (201, "true")


async def test_a_bundle_reaches_the_signer_in_chunks_as_it_arrives_never_buffered_whole(
    client: httpx.AsyncClient, fake: Fake
) -> None:
    whole = bytes(range(256)) * (6 * 1024)  # 1.5 MiB
    parts = [whole[start : start + CHUNK] for start in range(0, len(whole), CHUNK)]

    async def body() -> AsyncGenerator[bytes]:
        yield parts[0]
        await asyncio.wait_for(
            fake.first_chunk.wait(), timeout=5
        )  # the signer has it before the rest is sent
        for part in parts[1:]:
            yield part

    response = await client.post(PREFIX, headers=uploading(), content=body())
    assert response.status_code == 201, response.text
    assert len(fake.chunks) >= 24 == len(parts)
    assert b"".join(fake.chunks) == whole


async def test_a_bundle_over_two_mib_is_refused_413(client: httpx.AsyncClient) -> None:
    declared = await client.post(PREFIX, headers=uploading(key="a"), content=bytes(MAX_UPLOAD_BYTES + 1))
    streamed = await client.post(
        PREFIX, headers=uploading(key="b"), content=chunked(bytes(MAX_UPLOAD_BYTES), b"x")
    )
    within = await client.post(PREFIX, headers=uploading(key="c"), content=bytes(MAX_UPLOAD_BYTES))
    assert [answered(declared), answered(streamed)] == [(413, "payload_too_large")] * 2
    assert within.status_code == 201
    assert MAX_UPLOAD_BYTES == MAX_BUNDLE_BYTES


async def test_the_password_travels_as_base64_of_its_utf8_whatever_its_letters(
    client: httpx.AsyncClient, fake: Fake
) -> None:
    """A header is Latin-1 to the server: base64 keeps ``contraseña`` the password its owner typed."""
    response = await client.post(PREFIX, headers=uploading(unlock=unlocking("contraseña")), content=b"p")
    assert response.status_code == 201, response.text
    assert fake.secrets == ["contraseña"]


@pytest.mark.parametrize(
    "unlock",
    ["contraseña", "not base64!", "cGFzc3dvcmQ", base64.b64encode(b"\xff\xfe").decode(), ""],
    ids=["raw utf-8", "not the alphabet", "unpadded", "not utf-8", "empty"],
)
async def test_a_password_header_that_is_not_base64_of_utf8_is_refused_naming_it(
    client: httpx.AsyncClient, fake: Fake, unlock: str
) -> None:
    headers = [
        (name.encode(), value.encode()) for name, value in uploading().items() if name != UNLOCK_HEADER
    ]
    headers.append((UNLOCK_HEADER.encode(), unlock.encode()))  # raw bytes: httpx would refuse non-ASCII text
    response = await client.post(PREFIX, headers=headers, content=b"p")
    assert answered(response) == (422, "invalid_request")
    locations = [error["location"] for error in ERRORS.validate_python(document(response)["errors"])]
    assert locations == [f"header.{UNLOCK_HEADER}"]
    assert fake.calls == []


async def test_an_import_without_its_password_is_refused_naming_the_header(
    client: httpx.AsyncClient, fake: Fake
) -> None:
    headers = {name: value for name, value in uploading().items() if name != UNLOCK_HEADER}
    missing = await client.post(PREFIX, headers=headers, content=b"p")
    long = await client.post(PREFIX, headers=uploading(unlock=unlocking(PASSWORD * 100)), content=b"p")
    assert [answered(missing), answered(long)] == [(422, "invalid_request")] * 2
    locations = [error["location"] for error in ERRORS.validate_python(document(missing)["errors"])]
    assert locations == [f"header.{UNLOCK_HEADER}"]
    assert all(secret not in long.text for secret in SENTINELS)
    assert fake.calls == []


@pytest.mark.parametrize("content_type", [None, "application/json", "application/x-pkcs12x", "text/plain"])
async def test_a_body_that_is_no_pkcs12_by_its_type_is_refused_415(
    client: httpx.AsyncClient, fake: Fake, content_type: str | None
) -> None:
    headers = {name: value for name, value in uploading().items() if name != "Content-Type"}
    if content_type is not None:
        headers["Content-Type"] = content_type
    response = await client.post(PREFIX, headers=headers, content=b"p")
    assert answered(response) == (415, "unsupported_media_type")
    assert fake.calls == []


async def test_the_type_s_parameters_and_case_do_not_matter(client: httpx.AsyncClient) -> None:
    headers = uploading() | {"Content-Type": "Application/X-PKCS12; name=company.p12"}
    assert (await client.post(PREFIX, headers=headers, content=b"p")).status_code == 201


async def test_a_client_gone_before_its_bundle_ended_is_a_bad_request(fake: Fake) -> None:
    app = app_of(fake)
    sent: list[AsgiMessage] = []
    part: AsgiMessage = {"type": "http.request", "body": b"part", "more_body": True}
    gone: AsgiMessage = {"type": "http.disconnect"}
    messages = iter([part, gone])

    async def receive() -> AsgiMessage:
        return next(messages)

    async def remember(message: AsgiMessage) -> None:
        sent.append(message)

    headers = [(name.lower().encode(), value.encode()) for name, value in uploading().items()]
    scope: AsgiScope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": PREFIX,
        "raw_path": PREFIX.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": headers,
        "client": ("127.0.0.1", 1),
        "server": ("ledger.test", 80),
    }
    with capture_logs() as logs:
        await app(scope, receive, remember)
    assert [message["status"] for message in sent if message["type"] == "http.response.start"] == [400]
    assert [entry for entry in logs if entry["log_level"] == "error"] == []
    assert fake.calls == []


# ── Importing a remote certificate ─────────────────────────────────────────────


async def test_a_remote_import_sends_the_credential_and_answers_the_certificate(
    client: httpx.AsyncClient, fake: Fake
) -> None:
    body = {"credential": CREDENTIAL, "modes": ["live"]}
    in_live = posting(key="r-1") | {"Ledger-Mode": "live"}
    response = await client.post(f"{PREFIX}/remote", headers=in_live, json=body)
    assert response.status_code == 201, response.text
    assert document(response)["id"] == ref(ACTIVE)
    [request] = fake.requests
    assert (request.modes, request.idempotency_key, fake.secrets) == (
        frozenset({Mode.LIVE}),
        "r-1",
        [CREDENTIAL],
    )
    defaulted = await client.post(
        f"{PREFIX}/remote", headers=posting(key="r-2"), json={"credential": CREDENTIAL}
    )
    assert defaulted.status_code == 201
    assert fake.requests[-1].modes == {Mode.TEST}


@pytest.mark.parametrize(
    "body",
    [
        {"credential": ""},
        {"credential": "x" * 2049},
        {"credential": CREDENTIAL, "modes": []},
        {"credential": CREDENTIAL, "extra": 1},
        {"modes": ["test"]},
    ],
    ids=["empty", "too_long", "no_modes", "unknown_field", "no_credential"],
)
async def test_a_remote_import_refuses_a_body_out_of_its_schema(
    client: httpx.AsyncClient, fake: Fake, body: Mapping[str, object]
) -> None:
    response = await client.post(f"{PREFIX}/remote", headers=posting(), json=dict(body))
    assert answered(response) == (422, "invalid_request")
    assert CREDENTIAL not in response.text
    assert fake.calls == []


# ── Reading and retiring ───────────────────────────────────────────────────────


async def test_the_list_answers_one_page_of_the_certificates_serving_the_request_s_mode(
    client: httpx.AsyncClient, fake: Fake
) -> None:
    active = await client.get(PREFIX, headers=reading(mode="live"))
    every = await client.get(f"{PREFIX}?include_retired=true", headers=reading())
    assert [active.status_code, every.status_code] == [200, 200]
    actives, everything = page(active), page(every)
    assert [item["id"] for item in actives["items"]] == [ref(ACTIVE)]
    assert actives["items"][0]["livemode"] is True
    assert [item["id"] for item in everything["items"]] == [ref(ACTIVE), ref(RETIRED)]
    assert (everything["has_more"], everything["next_cursor"], everything["previous_cursor"]) == (
        False,
        None,
        None,
    )
    assert fake.include_retired == [False, True]
    assert [tenant.mode for _, _, tenant in fake.calls] == [Mode.LIVE, Mode.TEST]
    retired = everything["items"][1]
    assert (retired["retired_at"], retired["retired_reason"], retired["updated_at"]) == (
        "2026-10-19T12:00:00Z",
        "replaced",
        "2026-10-19T12:00:00Z",
    )


async def test_one_certificate_is_read_by_its_id_among_those_of_the_request_s_mode(
    client: httpx.AsyncClient, fake: Fake
) -> None:
    found = await client.get(f"{PREFIX}/{ref(RETIRED)}", headers=reading())
    elsewhere = await client.get(f"{PREFIX}/{ref(RETIRED)}", headers=reading(mode="live"))
    unknown = await client.get(f"{PREFIX}/{format_id(CERTIFICATE_PREFIX, new_id())}", headers=reading())
    other_type = await client.get(f"{PREFIX}/{format_id(COMPANY_PREFIX, ACTIVE.id)}", headers=reading())
    assert (found.status_code, document(found)["id"]) == (200, ref(RETIRED))
    assert [answered(elsewhere), answered(unknown), answered(other_type)] == [
        (404, "certificate_not_found")
    ] * 3
    assert fake.include_retired == [True, True, True]


async def test_a_certificate_of_the_request_s_mode_is_retired_for_every_mode(
    client: httpx.AsyncClient, fake: Fake
) -> None:
    response = await client.delete(f"{PREFIX}/{ref(ACTIVE)}", headers=reading())
    assert response.status_code == 200, response.text
    assert (document(response)["status"], document(response)["retired_reason"]) == ("retired", "retired")
    assert fake.retired == [ACTIVE.id]
    [_, (method, _, company)] = fake.calls
    assert (method, company) == ("retire", Tenant(ORGANIZATION, COMPANY, Mode.TEST))


async def test_a_certificate_the_request_s_mode_never_saw_is_not_found_and_not_retired(
    client: httpx.AsyncClient, fake: Fake
) -> None:
    elsewhere = await client.delete(f"{PREFIX}/{ref(RETIRED)}", headers=reading(mode="live"))
    other_type = await client.delete(f"{PREFIX}/{format_id(COMPANY_PREFIX, ACTIVE.id)}", headers=reading())
    assert [answered(elsewhere), answered(other_type)] == [(404, "certificate_not_found")] * 2
    assert fake.retired == []


# ── What the signer answers ────────────────────────────────────────────────────


def named(value: object) -> str:
    """A parameter's id: an error by its class."""
    return type(value).__name__ if isinstance(value, Exception) else str(value)


@pytest.mark.parametrize(
    ("failure", "status", "code"),
    [
        (BundleUnreadableError(), 422, "certificate_bundle_unreadable"),
        (IdempotencyKeyReusedError(), 422, "idempotency_key_reused"),
        (IdempotencyKeyInUseError(), 409, "idempotency_key_in_use"),
        (ActiveCertificateTakenError(), 409, "certificate_install_raced"),
        (CertificateNotFoundError(), 404, "certificate_not_found"),
        (CertificateServesLiveError(), 409, "certificate_serves_live"),
        (ImportTooLargeError(), 413, "signer_import_too_large"),
        (ImportTooSlowError(), 408, "signer_import_too_slow"),
        (ImportHeaderFirstError(), 500, "internal"),
        (ImportChunksUnexpectedError(), 500, "internal"),
        (SignerDefectError("/s/ImportCertificate", grpc.StatusCode.INTERNAL, "internal"), 500, "internal"),
        (RemoteDefectError("/s/ImportCertificate", grpc.StatusCode.INVALID_ARGUMENT, "odd"), 500, "internal"),
        (RemoteNotFoundError("/s/ImportCertificate", grpc.StatusCode.NOT_FOUND, "odd"), 500, "internal"),
    ],
    ids=named,
)
async def test_every_refusal_of_the_signer_reaches_the_problem_with_its_own_code(
    client: httpx.AsyncClient, fake: Fake, failure: LedgerError, status: int, code: str
) -> None:
    fake.failure = failure
    with capture_logs() as logs:
        response = await client.post(PREFIX, headers=uploading(), content=b"p")
    assert answered(response) == (status, code)
    defects = [entry for entry in logs if entry.get("event") == "http.unhandled_error"]
    assert len(defects) == (1 if status >= HTTPStatus.INTERNAL_SERVER_ERROR else 0)
    if status == HTTPStatus.REQUEST_TIMEOUT:
        assert "retry-after" not in response.headers


async def test_a_signer_out_of_reach_is_503_with_retry_after_on_every_route(
    client: httpx.AsyncClient, fake: Fake
) -> None:
    fake.failure = SignerUnavailableError()
    for route in ROUTES:
        response = await send(client, REQUESTS[route], "owner")
        assert answered(response) == (503, "signer_unavailable"), route
        assert response.headers["retry-after"] == str(RETRY_AFTER_SECONDS)


async def test_the_rendered_log_of_a_defect_names_it_and_never_a_password_or_a_credential(
    fake: Fake, capsys: pytest.CaptureFixture[str]
) -> None:
    """The platform's own logging, configured once ``capsys`` holds stdout: what an operator reads."""
    configure_logging(
        Settings(environment="test", database_url=SecretStr("postgresql+psycopg://a:b@db.invalid/l")),
        service="ledger-api",
    )
    transport = httpx.ASGITransport(app=app_of(fake), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://ledger.test") as client:
        fake.failure = ImportHeaderFirstError()
        local = await client.post(PREFIX, headers=uploading(), content=b"p")
        remote = await client.post(f"{PREFIX}/remote", headers=posting(), json={"credential": CREDENTIAL})
    out = capsys.readouterr().out
    assert [answered(local), answered(remote)] == [(500, "internal")] * 2
    assert out.count("http.unhandled_error") == 2  # rendered: a blind capture would hold nothing
    assert "signer_call_defect" in out
    assert "ImportHeaderFirstError" in out  # the defect's cause, with its traceback
    assert [secret for secret in SENTINELS if secret in out] == []


async def test_a_password_or_a_credential_never_reaches_a_log_or_a_problem(
    client: httpx.AsyncClient,
    fake: Fake,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    seen: list[str] = []
    with capture_logs() as logs:
        for failure in (BundleUnreadableError(), SignerUnavailableError(), ImportHeaderFirstError(), None):
            fake.failure = failure
            local = await client.post(PREFIX, headers=uploading(), content=b"p")
            remote = await client.post(f"{PREFIX}/remote", headers=posting(), json={"credential": CREDENTIAL})
            seen += [local.text, str(local.headers), remote.text, str(remote.headers)]
        refused = await client.post(PREFIX, headers=uploading(unlock=unlocking(PASSWORD * 100)), content=b"p")
        seen.append(refused.text)
    captured = capsys.readouterr()
    seen += [captured.out, captured.err, caplog.text, repr(logs)]
    assert [text for text in seen if any(secret in text for secret in SENTINELS)] == []
