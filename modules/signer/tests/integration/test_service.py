"""The signer's app on PostgreSQL: its service on port 0 guarded per caller, its consumer, notices and purges.

Each test starts the ``signer`` app as ``ledger_signer`` (``LEDGER_SIGNER_GRPC_PORT=0``, every caller's token,
a local-file KMS's key made for it) and reaches it with the clients of Task 11 holding the api's or the
worker's token. What the service did is read back as the superuser reads it: the certificates, the sealed
custody, the outbox, the jobs, the audit rows (spec §3.7; Review Focus 1-6).
"""

import asyncio
import hashlib
import os
from abc import ABCMeta, abstractmethod
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from typing import Final, LiteralString, Protocol, TypeIs, final
lazy from collections.abc import AsyncGenerator, Sequence
lazy from pathlib import Path

import grpc
import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.x509.oid import NameOID
from pydantic import SecretStr, TypeAdapter
from signxml.algorithms import DigestAlgorithm, SignatureMethod
from signxml.verifier import SignatureConfiguration, XMLVerifier
lazy from cryptography import x509
lazy from fastapi import FastAPI
lazy from psycopg import AsyncConnection
lazy from signxml.verifier import VerifyResult

from ledger_fiscal_kit.ports import (
    SeedToSign,
    SignatureProfile,
    SigningCertificateNotValidError,
    SigningClockSkewError,
    XmlToSign,
)
from ledger_kernel.ids import CompanyId, OrganizationId, new_id
from ledger_kernel.tenancy import Mode, Tenant
from ledger_platform.grpc import RemoteError, call_stream_unary
from ledger_platform.grpc.client import RemoteDefectError
from ledger_platform.grpc.guard import MethodNotAllowedError
from ledger_platform.grpc.metadata import call_metadata
from ledger_platform.http.idempotency import IdempotencyKeyReusedError
from ledger_platform.runtime.app import App
from ledger_platform.runtime.calls import SystemCall
from ledger_signer.adapters.custody.kms_local import MASTER_KEY_BYTES
from ledger_signer.adapters.grpc import codec
from ledger_signer.adapters.grpc._generated.v1 import signer_pb2, signer_pb2_grpc
from ledger_signer.adapters.grpc.client import SignerClient, SignerDefectError
from ledger_signer.adapters.grpc.codec import ImportHeaderFirstError
from ledger_signer.adapters.grpc.server import SignerServer
from ledger_signer.application.importing import MAX_BUNDLE_BYTES, ImportRequest, ImportTooLargeError
from ledger_signer.application.ports import BundleUnreadableError, Secret
from ledger_signer.domain.errors import ClockSkewError, NotCoveredError
from ledger_testing.database import signer_url
from ledger_testing.modules import app_settings
from ledger_testing.pki import Authority, bundle, distinguished_name
from ledger_testing.waiting import eventually
lazy from ledger_platform.config.settings import Settings
lazy from ledger_platform.runtime.registries import Discover
lazy from ledger_signer.api import CertificateView
lazy from ledger_testing.issuer import LocalIssuer
lazy from ledger_testing.modules import ModuleUnderTest
lazy from ledger_testing.pki import Issued
lazy from ledger_testing.postgres import WorkerDatabase

TOKENS: Final = {
    "api": "api-token-0123456789abcdefghijklmnopqrstuv",
    "worker": "worker-token-0123456789abcdefghijklmnopq",
    "receiver": "receiver-token-0123456789abcdefghijklmn",
}
PASSWORD: Final = "PASSWORD-SENTINEL"
DOCUMENT: Final = b"<ECF><Encabezado><Nombre>DOCUMENT-SENTINEL</Nombre></Encabezado></ECF>"
SENTINELS: Final = (PASSWORD, "DOCUMENT-SENTINEL")
PROFILE: Final = SignatureProfile.XMLDSIG_ENVELOPED_RSA_SHA256
REFERENCE: Final = "E310000000001"
DEADLINE: Final = 10.0
CHUNK: Final = 64 * 1024
CHECKS: Final = TypeAdapter(dict[str, bool])
YEAR: Final = timedelta(days=365)
_CONFIGURATION: Final = SignatureConfiguration(
    require_x509=True,
    location="./",
    expect_references=1,
    signature_methods=frozenset({SignatureMethod.RSA_SHA256}),
    digest_algorithms=frozenset({DigestAlgorithm.SHA256}),
)


class _Verifies(Protocol, metaclass=ABCMeta):
    """signxml's ``XMLVerifier`` as called here (signxml 5.1 leaves parts of ``verify`` unannotated)."""

    excise_empty_xmlns_declarations: bool

    @abstractmethod
    def verify(
        self, data: bytes, *, x509_cert: x509.Certificate, expect_config: SignatureConfiguration
    ) -> VerifyResult | list[VerifyResult]:
        """Verify ``data`` with ``x509_cert``."""


def _is_verifier(value: object) -> TypeIs[_Verifies]:
    return isinstance(value, XMLVerifier)


def _as_verifier(made: object) -> _Verifies:
    """``made``, signxml's ``XMLVerifier``, as ``_Verifies`` describes it."""
    assert _is_verifier(made)
    return made


def verified(signed: bytes, certificate: x509.Certificate) -> None:
    """``signed`` verified by signxml with ``certificate``; signxml's error when it does not verify."""
    verifier = _as_verifier(XMLVerifier())
    verifier.excise_empty_xmlns_declarations = True  # lxml's spurious xmlns="" (signxml's issue 193)
    verifier.verify(signed, x509_cert=certificate, expect_config=_CONFIGURATION)


@final
@dataclass(frozen=True, slots=True)
class Running:
    """The signer's app, started: where its service listens, and a client of its probes."""

    app: App
    address: str
    probes: httpx.AsyncClient


@pytest.fixture
def key_file(tmp_path: Path) -> Path:
    path = tmp_path / "kms.key"
    path.write_bytes(os.urandom(MASTER_KEY_BYTES))
    return path


@final
@dataclass(frozen=True, slots=True)
class Deployment:
    """What the signer's app is made of: its settings (``LEDGER_SIGNER_*`` set) and the packages it finds."""

    settings: Settings
    discover: Discover


@pytest.fixture
def deployment(
    migrated: WorkerDatabase,
    local_issuer: LocalIssuer,
    module_under_test: ModuleUnderTest,
    monkeypatch: pytest.MonkeyPatch,
    key_file: Path,
) -> Deployment:
    """Every caller's token, the KMS's key, any free port, and the database as ``ledger_signer``."""
    for caller, token in TOKENS.items():
        monkeypatch.setenv(f"LEDGER_SIGNER_TOKEN_{caller.upper()}", token)
    monkeypatch.setenv("LEDGER_SIGNER_KMS_KEY_FILE", str(key_file))
    monkeypatch.setenv("LEDGER_SIGNER_GRPC_PORT", "0")
    url = signer_url(migrated)
    settings = app_settings(database_url=url, direct_url=url, issuer=local_issuer)
    return Deployment(settings, module_under_test.discovering())


@asynccontextmanager
async def started(deployment: Deployment) -> AsyncGenerator[Running]:
    """The signer's app, made now (its log handler writes to this moment's ``sys.stdout``) and started."""
    app = App("signer", settings=deployment.settings, discover=deployment.discover)
    served: FastAPI = app.asgi()
    async with served.router.lifespan_context(served):
        [server] = [
            resource
            for member in app.members
            for resource in member.resources
            if isinstance(resource, SignerServer)
        ]
        transport = httpx.ASGITransport(app=served)
        async with httpx.AsyncClient(transport=transport, base_url="http://ledger.test") as probes:
            yield Running(app, f"127.0.0.1:{server.port}", probes)


@pytest.fixture
async def signer(deployment: Deployment) -> AsyncGenerator[Running]:
    async with started(deployment) as running:
        yield running


@asynccontextmanager
async def client(signer: Running, caller: str) -> AsyncGenerator[SignerClient]:
    """A client of the signer with ``caller``'s token."""
    async with SignerClient(
        signer.address, SecretStr(TOKENS[caller]), deadline=DEADLINE, import_deadline=DEADLINE
    ) as opened:
        yield opened


@pytest.fixture
async def api(signer: Running) -> AsyncGenerator[SignerClient]:
    async with client(signer, "api") as opened:
        yield opened


@pytest.fixture
async def worker(signer: Running) -> AsyncGenerator[SignerClient]:
    async with client(signer, "worker") as opened:
        yield opened


@pytest.fixture(scope="module")
def authority() -> Authority:
    return Authority.root()


def issued(authority: Authority, *, lasting: timedelta = YEAR) -> Issued:
    """A signing certificate valid from a day ago for ``lasting`` from now."""
    now = datetime.now(UTC)
    return authority.issue(
        distinguished_name((NameOID.COMMON_NAME, "Signer Test Holder")),
        not_before=now - timedelta(days=1),
        not_after=now + lasting,
    )


@pytest.fixture
def holder(authority: Authority) -> Issued:
    return issued(authority)


def a_company() -> Tenant:
    return Tenant(OrganizationId(new_id()), CompanyId(new_id()), Mode.TEST)


def call_for(tenant: Tenant, call_id: str = "call-1") -> SystemCall:
    return SystemCall(reason="signer_tests", scope=tenant, call_id=call_id)


def request_for(tenant: Tenant, key: str = "import-0001") -> ImportRequest:
    return ImportRequest(tenant=tenant, modes=frozenset({Mode.TEST}), idempotency_key=key)


async def parts_of(data: bytes, size: int = CHUNK) -> AsyncGenerator[bytes]:
    for start in range(0, len(data), size):
        yield data[start : start + size]


async def imported(
    api: SignerClient, tenant: Tenant, certificate: Issued, key: str = "import-0001"
) -> CertificateView:
    """``certificate``'s bundle imported for ``tenant``'s mode under ``key``."""
    data = bundle(certificate, password=PASSWORD)
    outcome = await api.import_local(
        call_for(tenant), request_for(tenant, key), Secret(PASSWORD), parts_of(data, 256)
    )
    return outcome.view


def document(tenant: Tenant, at: datetime) -> XmlToSign:
    return XmlToSign(tenant=tenant, profile=PROFILE, document=DOCUMENT, signed_at=at, reference=REFERENCE)


async def rows(admin: AsyncConnection, query: LiteralString, *parameters: object) -> list[tuple[object, ...]]:
    cursor = await admin.execute(query, parameters)
    return await cursor.fetchall()


async def test_import_streams_a_bundle_and_installs_it(
    signer: Running, api: SignerClient, admin: AsyncConnection, holder: Issued
) -> None:
    tenant = a_company()
    view = await imported(api, tenant, holder)
    certificates = await rows(
        admin,
        "SELECT id, status, serves_test, serves_live FROM signer.certificates WHERE company_id = %s",
        tenant.company,
    )
    [(sealed,)] = await rows(admin, "SELECT sealed FROM signer.custody WHERE certificate_id = %s", view.id)
    key = holder.key.private_bytes(
        serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    outbox = await rows(
        admin, "SELECT type, subject FROM signer.outbox WHERE company_id = %s", tenant.company
    )
    notices = await rows(
        admin,
        "SELECT key FROM signer.jobs WHERE kind = 'signer.expiry_notice' AND company_id = %s ORDER BY run_at",
        tenant.company,
    )
    ready = await signer.probes.get("/readyz")
    assert certificates == [(view.id, "active", True, False)]
    assert isinstance(sealed, bytes)
    assert key not in sealed
    assert key[-64:] not in sealed
    assert outbox == [("signer.certificate.installed", view.id)]
    assert notices == [(f"{view.id}:{days}",) for days in (30, 14, 7, 1, 0)]
    assert ready.status_code == HTTPStatus.OK
    checks = CHECKS.validate_python(ready.json()["checks"])
    assert (checks["signer:grpc"], checks["signer:kms"]) == (True, True)


async def test_import_is_idempotent_and_twins_wait(
    api: SignerClient, admin: AsyncConnection, authority: Authority, holder: Issued
) -> None:
    tenant = a_company()
    data = bundle(holder, password=PASSWORD)
    request = request_for(tenant)
    first, second = await asyncio.gather(
        api.import_local(call_for(tenant), request, Secret(PASSWORD), parts_of(data)),
        api.import_local(call_for(tenant), request, Secret(PASSWORD), parts_of(data)),
    )
    another = bundle(issued(authority), password=PASSWORD)
    with pytest.raises(IdempotencyKeyReusedError):
        await api.import_local(call_for(tenant), request, Secret(PASSWORD), parts_of(another))
    failing = request_for(tenant, "import-0002")
    with pytest.raises(BundleUnreadableError):
        await api.import_local(call_for(tenant), failing, Secret(PASSWORD + "-wrong"), parts_of(another))
    after = await api.import_local(call_for(tenant), failing, Secret(PASSWORD), parts_of(another))
    assert sorted((first.replayed, second.replayed)) == [False, True]
    assert first.view == second.view
    assert not after.replayed  # the failed import kept no key
    installed = await rows(
        admin,
        "SELECT status FROM signer.certificates WHERE company_id = %s ORDER BY installed_at",
        tenant.company,
    )
    assert installed == [("retired",), ("active",)]


def sent(caller: str) -> tuple[tuple[str, str], ...]:
    return call_metadata(SecretStr(TOKENS[caller]), call_id="call-1")  # call_for's


async def stream(
    *requests: signer_pb2.ImportCertificateRequest,
) -> AsyncGenerator[signer_pb2.ImportCertificateRequest]:
    for request in requests:
        yield request


async def test_import_header_first_and_size_limits(signer: Running, admin: AsyncConnection) -> None:
    tenant = a_company()
    header = signer_pb2.ImportCertificateRequest(
        header=codec.local_header(call_for(tenant), request_for(tenant), Secret(PASSWORD))
    )
    chunk = signer_pb2.ImportCertificateRequest(chunk=bytes(CHUNK))
    over = (chunk for _ in range(MAX_BUNDLE_BYTES // CHUNK + 1))
    async with grpc.aio.insecure_channel(signer.address) as channel:
        stub = signer_pb2_grpc.SignerServiceStub(channel)
        codes: list[tuple[grpc.StatusCode, str]] = []
        for requests in ((chunk, header), (header, *over)):
            with pytest.raises(RemoteError) as caught:
                await call_stream_unary(
                    stub.ImportCertificate,
                    stream(*requests),
                    rpc=codec.IMPORT,
                    deadline=DEADLINE,
                    metadata=sent("api"),
                )
            codes.append((caught.value.status, caught.value.remote_code))
    assert codes == [
        (grpc.StatusCode.INVALID_ARGUMENT, ImportHeaderFirstError.code),
        (grpc.StatusCode.INVALID_ARGUMENT, ImportTooLargeError.code),
    ]
    assert await rows(
        admin, "SELECT count(*) FROM signer.certificates WHERE company_id = %s", tenant.company
    ) == [(0,)]


async def test_sign_xml_signs_audits_and_answers(
    api: SignerClient, worker: SignerClient, admin: AsyncConnection, holder: Issued
) -> None:
    tenant = a_company()
    view = await imported(api, tenant, holder)
    signed = await worker.sign_xml(call_for(tenant, "emission-7"), document(tenant, datetime.now(UTC)))
    verified(signed.document, holder.certificate)
    [(actor, call_id, subject, detail)] = await rows(
        admin,
        """SELECT actor, call_id, subject_id, detail FROM signer.audit_events
           WHERE company_id = %s AND action = 'certificate.sign_xml'""",
        tenant.company,
    )
    assert signed.certificate == view.id
    assert (actor, call_id, subject) == ("system:signer_tests", "emission-7", str(view.id))
    assert detail == {
        "caller": "worker",
        "mode": "test",
        "profile": PROFILE.value,
        "document_sha256": hashlib.sha256(DOCUMENT).hexdigest(),
        "reference": REFERENCE,
        "backend": "local",
    }
    audit = await rows(
        admin, "SELECT audit_events::text FROM signer.audit_events WHERE company_id = %s", tenant.company
    )
    assert all("DOCUMENT-SENTINEL" not in str(row) for row in audit)


async def test_sign_seed_audits_without_a_reference(
    api: SignerClient, worker: SignerClient, admin: AsyncConnection, holder: Issued
) -> None:
    tenant = a_company()
    _ = await imported(api, tenant, holder)
    seed = SeedToSign(
        tenant=tenant,
        profile=PROFILE,
        seed=b"<SemillaModel><valor>1</valor></SemillaModel>",
        audience="ecf.dgii.gov.do",
    )
    signed = await worker.sign_seed(call_for(tenant), seed)
    verified(signed.document, holder.certificate)
    [(detail,)] = await rows(
        admin,
        "SELECT detail FROM signer.audit_events WHERE company_id = %s AND action = 'certificate.sign_seed'",
        tenant.company,
    )
    assert isinstance(detail, dict)
    assert (detail["caller"], detail["audience"], "reference" in detail) == (
        "worker",
        "ecf.dgii.gov.do",
        False,
    )


async def test_the_api_token_cannot_sign_and_the_worker_token_cannot_import(
    api: SignerClient, worker: SignerClient, holder: Issued
) -> None:
    tenant = a_company()
    with pytest.raises(RemoteDefectError) as signing:
        await api.sign_xml(call_for(tenant), document(tenant, datetime.now(UTC)))
    with pytest.raises(RemoteDefectError) as importing:
        await imported(worker, tenant, holder)
    assert signing.value.remote_code == importing.value.remote_code == MethodNotAllowedError.code


async def test_window_and_skew_answer_failed_precondition_with_their_codes(
    api: SignerClient, worker: SignerClient, authority: Authority
) -> None:
    tenant = a_company()
    _ = await imported(api, tenant, issued(authority, lasting=timedelta(minutes=7)))
    now = datetime.now(UTC)
    with pytest.raises(SigningClockSkewError) as skewed:
        await worker.sign_xml(call_for(tenant), document(tenant, now - timedelta(hours=1)))
    with pytest.raises(SigningCertificateNotValidError) as uncovered:
        await worker.sign_xml(call_for(tenant), document(tenant, now + timedelta(minutes=4)))
    causes = [error.value.__cause__ for error in (skewed, uncovered)]
    assert [(cause.status, cause.remote_code) for cause in causes if isinstance(cause, RemoteError)] == [
        (grpc.StatusCode.FAILED_PRECONDITION, ClockSkewError.code),
        (grpc.StatusCode.FAILED_PRECONDITION, NotCoveredError.code),
    ]


async def test_an_audit_that_cannot_be_written_returns_no_signature(
    api: SignerClient, worker: SignerClient, admin: AsyncConnection, holder: Issued
) -> None:
    tenant = a_company()
    _ = await imported(api, tenant, holder)
    await admin.execute("REVOKE INSERT ON signer.audit_events FROM signer_rw")
    try:
        with pytest.raises(SignerDefectError):
            await worker.sign_xml(call_for(tenant), document(tenant, datetime.now(UTC)))
    finally:
        await admin.execute("GRANT INSERT ON signer.audit_events TO signer_rw")
    signatures = await rows(
        admin,
        "SELECT count(*) FROM signer.audit_events WHERE company_id = %s AND action = 'certificate.sign_xml'",
        tenant.company,
    )
    assert signatures == [(0,)]


INBOX: Final = """
    INSERT INTO signer.inbox (consumer, event_id, type, version, source, organization_id, company_id, mode,
                              subject, aggregate_version, occurred_at, payload)
    VALUES ('signer.company_deactivations', %s, 'identity.company.deactivated', 1, 'identity', %s, %s, NULL,
            %s, 2, now(), '{}')
"""


async def test_company_deactivation_retires_its_certificates(
    signer: Running, api: SignerClient, admin: AsyncConnection, holder: Issued
) -> None:
    del signer
    tenant = a_company()
    view = await imported(api, tenant, holder)
    await admin.execute(INBOX, (new_id(), tenant.organization, tenant.company, tenant.company))

    async def retired() -> bool:
        found = await rows(
            admin, "SELECT status, retired_reason FROM signer.certificates WHERE id = %s", view.id
        )
        return found == [("retired", "company_deactivated")]

    await eventually(retired)
    outbox = await rows(
        admin, "SELECT type FROM signer.outbox WHERE subject = %s ORDER BY occurred_at, type", view.id
    )
    custody = await rows(admin, "SELECT count(*) FROM signer.custody WHERE certificate_id = %s", view.id)
    [(actor,)] = await rows(
        admin,
        "SELECT actor FROM signer.audit_events WHERE subject_id = %s AND action = 'certificate.retire'",
        str(view.id),
    )
    assert outbox == [("signer.certificate.installed",), ("signer.certificate.retired",)]
    assert custody == [(0,)]
    assert actor == "system:consumer:signer.company_deactivations"
    assert await rows(admin, "SELECT count(*) FROM signer.inbox") == [(0,)]


async def test_expiry_notice_publishes_for_an_active_certificate_only(
    signer: Running, api: SignerClient, admin: AsyncConnection, authority: Authority
) -> None:
    tenant = a_company()
    gone = await imported(api, tenant, issued(authority), "import-0001")
    _ = await api.retire(call_for(tenant), Tenant(tenant.organization, tenant.company, None), gone.id)
    kept = await imported(api, tenant, issued(authority), "import-0002")
    now = datetime.now(UTC)
    async with signer.app.bound("signer").unit(tenant) as unit:
        for certificate in (gone, kept):
            payload = {"certificate": str(certificate.id), "threshold_days": 3}
            unit.jobs.enqueue("signer.expiry_notice", f"{certificate.id}:3", run_at=now, payload=payload)

    async def done() -> bool:
        found = await rows(
            admin, "SELECT count(*) FROM signer.jobs WHERE key LIKE '%%:3' AND status = 'done'"
        )
        return found == [(2,)]

    await eventually(done)
    expiring = await rows(
        admin,
        """SELECT subject, payload->>'threshold_days' FROM signer.outbox
           WHERE type = 'signer.certificate.expiring'""",
    )
    assert expiring == [(kept.id, "3")]


EXPIRED_KEY: Final = """
    INSERT INTO signer.idempotency_keys (organization_id, mode, principal, route, key, request_hash,
                                         created_at, expires_at)
    VALUES (%s, 'test', 'system:x', 'grpc ImportCertificate', 'old', '\\x00',
            now() - interval '9 days', now() - interval '2 days')
"""
OLD_DONE_JOB: Final = """
    INSERT INTO signer.jobs (kind, key, status, run_at, max_attempts, created_at, updated_at)
    VALUES ('signer.expiry_notice', 'old', 'done', now() - interval '9 days', 20,
            now() - interval '9 days', now() - interval '8 days')
"""


async def test_the_signer_purges_its_own_keys_and_done_jobs(signer: Running, admin: AsyncConnection) -> None:
    await admin.execute(EXPIRED_KEY, (new_id(),))
    await admin.execute(OLD_DONE_JOB)
    async with signer.app.bound("signer").unit(None) as unit:
        for kind in ("signer.purge_idempotency_keys", "signer.purge_done_jobs"):
            unit.jobs.enqueue(kind, "test", run_at=datetime.now(UTC), payload={})

    async def purged() -> bool:
        keys = await rows(admin, "SELECT count(*) FROM signer.idempotency_keys WHERE key = 'old'")
        jobs = await rows(admin, "SELECT count(*) FROM signer.jobs WHERE key = 'old'")
        return keys == jobs == [(0,)]

    await eventually(purged)


async def test_secrets_never_leave(
    deployment: Deployment, admin: AsyncConnection, holder: Issued, capsys: pytest.CaptureFixture[str]
) -> None:
    """The app is made inside the test, once ``capsys`` holds ``sys.stdout``: its log lines are captured."""
    async with (
        started(deployment) as running,
        client(running, "api") as api,
        client(running, "worker") as worker,
    ):
        logged = await secrets_kept(api, worker, admin, holder)
    seen = [capsys.readouterr().out, *logged]
    assert "grpc.caller_refused" in seen[0]
    assert "grpc.unhandled_error" in seen[0]
    assert "InsufficientPrivilege" in seen[0]  # the defect's traceback, rendered
    assert [text for text in seen if any(secret in text for secret in SENTINELS)] == []


async def secrets_kept(
    api: SignerClient, worker: SignerClient, admin: AsyncConnection, holder: Issued
) -> list[str]:
    """A refused import, an import, a signature, a refused caller and a defect: what each left behind."""
    tenant = a_company()
    with pytest.raises(BundleUnreadableError) as unreadable:
        await api.import_local(
            call_for(tenant), request_for(tenant), Secret(PASSWORD + "-x"), parts_of(b"garbage")
        )
    _ = await imported(api, tenant, holder, "import-0003")
    _ = await worker.sign_xml(call_for(tenant), document(tenant, datetime.now(UTC)))
    with pytest.raises(RemoteDefectError):  # the guard logs it: proof the capture sees the app's lines
        await imported(worker, tenant, holder, "import-0004")
    await admin.execute("REVOKE INSERT ON signer.audit_events FROM signer_rw")
    try:
        with pytest.raises(SignerDefectError):  # a defect: logged with its traceback rendered
            await worker.sign_xml(call_for(tenant), document(tenant, datetime.now(UTC)))
    finally:
        await admin.execute("GRANT INSERT ON signer.audit_events TO signer_rw")
    stored: Sequence[tuple[object, ...]] = [
        *await rows(
            admin, "SELECT detail::text FROM signer.audit_events WHERE company_id = %s", tenant.company
        ),
        *await rows(admin, "SELECT payload::text FROM signer.outbox WHERE company_id = %s", tenant.company),
        *await rows(admin, "SELECT payload::text FROM signer.jobs WHERE company_id = %s", tenant.company),
        *await rows(
            admin, "SELECT certificates::text FROM signer.certificates WHERE company_id = %s", tenant.company
        ),
    ]
    [(sealed,)] = await rows(admin, "SELECT sealed FROM signer.custody WHERE company_id = %s", tenant.company)
    assert isinstance(sealed, bytes)
    assert all(secret.encode() not in sealed for secret in SENTINELS)
    return [str(unreadable.value), *(str(row) for row in stored)]
