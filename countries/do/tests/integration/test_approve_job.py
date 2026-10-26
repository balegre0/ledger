"""The ``do_ecf.approve`` job against the fake DGII and its emisor-receptor simulator (spec §8.6.5 step 6).

A company's decision of an e-CF it received becomes one ACECF, signed once and archived, sent to DGII
(``aprobacioncomercial``, once: its mark first, never resent) and to the emitter's approval service (DGII's
directory), each party's outcome recorded and audited apart. A party out of reach is tried again on Decision
21's schedule for seven days, then ``failed``; refused, failed and unknown outcomes are reported. The
simulator's host is pinned to a public address, as the delivery job's tests pin it.
"""

from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from ipaddress import IPv4Address
from typing import Final, Protocol, final, override
lazy from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping, Sequence
lazy from ipaddress import IPv6Address
lazy from pathlib import Path
lazy from uuid import UUID

import httpx
import pytest
from lxml import etree
from pydantic import TypeAdapter
from sqlalchemy import select, update
from structlog.testing import capture_logs

from ledger_country_do.channels.ecf_v1.adapters.buyers import client as buyers_client
from ledger_country_do.channels.ecf_v1.adapters.dgii.client import DgiiClient
from ledger_country_do.channels.ecf_v1.adapters.jobs import APPROVE
from ledger_country_do.channels.ecf_v1.adapters.outbound import OutboundClosedError
from ledger_country_do.channels.ecf_v1.adapters.postgres.tables import ReceivedDocumentRecord
from ledger_country_do.channels.ecf_v1.adapters.postgres.unit import bind
from ledger_country_do.channels.ecf_v1.adapters.signing.verifier import INBOUND, verify_enveloped
from ledger_country_do.channels.ecf_v1.adapters.xml.acecf import render_acecf
from ledger_country_do.channels.ecf_v1.application.approving import (
    ApprovalMissingError,
    ApprovalRequest,
    CommercialApprovals,
    NotTheBuyerError,
    ReceivedNotFoundError,
)
from ledger_country_do.channels.ecf_v1.application.documents import DeliveryOptions
from ledger_country_do.channels.ecf_v1.application.ports import (
    Archived,
    AuthorityUnavailableError,
    Failure,
)
from ledger_country_do.channels.ecf_v1.application.received import ReceivedDocument
from ledger_country_do.channels.ecf_v1.assembly import assemble
from ledger_country_do.channels.ecf_v1.domain.approval import Decision
from ledger_country_do.channels.ecf_v1.domain.encf import EcfType, Encf
from ledger_country_do.channels.ecf_v1.domain.environments import Environment
from ledger_country_do.channels.ecf_v1.domain.errors import EcfInvalidError
from ledger_country_do.channels.ecf_v1.domain.exchanges import CommercialApproval, InboundEcf
from ledger_country_do.channels.ecf_v1.settings import DoEcfSettings
from ledger_country_do_testing.transports import FAKE_HOSTS, outbound_over
from ledger_fiscal_kit.ports import ARCHIVE_PORT, SIGNER_PORT, ArchivePort, archive_key
from ledger_fiscal_kit.ports import ArchiveUnavailableError as PortUnavailableError
from ledger_kernel.ids import CompanyId, OrganizationId, new_id
from ledger_kernel.tenancy import Mode, Tenant
from ledger_platform.data.tables import chassis_tables
from ledger_platform.jobs.outcomes import Done, Job, RunAgain
from ledger_testing.pki import pem
from ledger_testing.signer import TestSigner
lazy from ledger_country_do_testing.fake_dgii.app import FakeDgii
lazy from ledger_country_do_testing.local_signer import KeyPairSigner
lazy from ledger_fiscal_kit.ports import ArchivedObject
lazy from ledger_platform.config.manifest import AppRole
lazy from ledger_platform.config.settings import Environment as AppEnvironment
lazy from ledger_platform.data.bound import BoundDatabase
lazy from ledger_platform.http.outbound import OutboundHttp
lazy from ledger_platform.jobs.outcomes import JobHandler, JobOutcome
lazy from ledger_platform.runtime.archiving import StoreArchive
lazy from ledger_platform.runtime.contexts import ChannelContext, ChannelParts
lazy from ledger_testing.pki import Authority, Issued

SETTINGS: Final = DoEcfSettings(
    receiver_public_url="https://receiver.ledger.test", ecf_host=FAKE_HOSTS.ecf, fc_host=FAKE_HOSTS.fc
)
CHASSIS: Final = chassis_tables("do_ecf")
BUYER: Final = "101850043"
"""The receiving company: the e-CF's buyer, who approves it."""
ISSUER: Final = "131098193"
"""The emitter: in TesteCF, DGII's directory answers its own simulator for it."""
DGII_APPROVAL: Final = "aprobacioncomercial/api/aprobacioncomercial"
EMITTER_APPROVAL: Final = "emisorreceptor/fe/aprobacioncomercial/api/ecf"
EMITTER_SEED: Final = "emisorreceptor/fe/autenticacion/api/semilla"
DIRECTORY: Final = "consultadirectorio/api/consultas/obtenerdirectorioporrnc"
PUBLIC: Final = IPv4Address("93.184.215.14")
SLACK: Final = timedelta(seconds=5)
ROWS: Final = TypeAdapter(list[dict[str, str]])
SIGNED_AT: Final = datetime(2026, 10, 13, 9, 30, tzinfo=UTC)
ACECF: Final = "{http://www.w3.org/2000/09/xmldsig#}Signature"


class Contexts(Protocol):
    """The conftest's ``contexts``: a channel context as the app makes it."""

    def __call__(
        self,
        *,
        settings: DoEcfSettings,
        bound: Mapping[str, object] | None = None,
        role: AppRole = "api",
        environment: AppEnvironment = "test",
        http: OutboundHttp | None = None,
    ) -> ChannelContext: ...


@final
class Network(httpx.AsyncBaseTransport):
    """The way to the fake DGII and its simulator: ``meanwhile`` runs first, for a request whose path ends
    with a key (what another run does meanwhile); ``answering`` answers a status of its own (and no body) to
    the requests whose path ends with a key; ``unlisted`` answers DGII's directory empty; ``dying``
    lets such a request reach the fake, then fails the run that sent it (``RunDiedError``) before its answer
    comes back: a process that died after sending.
    """

    __slots__ = ("_inner", "answering", "dying", "fake", "meanwhile", "requests", "unlisted")

    def __init__(self, fake: FakeDgii) -> None:
        self.fake: Final = fake
        self._inner = httpx.ASGITransport(app=fake.app)
        self.requests: list[str] = []
        self.answering: dict[str, int] = {}
        self.unlisted = False
        self.meanwhile: dict[str, Callable[[], Awaitable[None]]] = {}
        self.dying: set[str] = set()

    def asked(self, part: str) -> int:
        return sum(1 for path in self.requests if path.endswith(part))

    @override
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.requests.append(path)
        for part, hook in self.meanwhile.items():
            if path.endswith(part):
                await hook()
        for part, status in self.answering.items():
            if path.endswith(part):
                return httpx.Response(status, stream=httpx.ByteStream(b""), request=request)
        if self.unlisted and path.endswith(DIRECTORY):
            return httpx.Response(200, stream=httpx.ByteStream(b"[]"), request=request)
        answered = await self._inner.handle_async_request(request)
        if any(path.endswith(part) for part in self.dying):
            raise RunDiedError
        return answered


class RunDiedError(Exception):
    """The run died once its request had reached the fake: no answer, no error of the channel's."""


@final
class Watching(ArchivePort):
    """The test's archive, which keeps the key of every ACECF kept, and runs ``before_acecf`` before the first
    (what a run that raced does meanwhile)."""

    __slots__ = ("_inner", "before_acecf", "kept")

    def __init__(self, inner: StoreArchive) -> None:
        self._inner = inner
        self.kept: list[str] = []
        self.before_acecf: Callable[[], Awaitable[None]] | None = None

    @override
    async def put_once(
        self, key: str, content: bytes, *, retain_until: date, media_type: str
    ) -> ArchivedObject:
        if "/acecf/" in key:
            hook, self.before_acecf = self.before_acecf, None
            if hook is not None:
                await hook()
            self.kept.append(key)
        return await self._inner.put_once(key, content, retain_until=retain_until, media_type=media_type)

    @override
    async def get(self, key: str, *, sha256: bytes) -> bytes:
        return await self._inner.get(key, sha256=sha256)


@final
class LosingApprovals(ArchivePort):
    """The test's archive, unreachable for an ACECF (``acecf``) and nothing else."""

    __slots__ = ("_inner",)

    def __init__(self, inner: StoreArchive) -> None:
        self._inner = inner

    @override
    async def put_once(
        self, key: str, content: bytes, *, retain_until: date, media_type: str
    ) -> ArchivedObject:
        if "/acecf/" in key:
            raise PortUnavailableError
        return await self._inner.put_once(key, content, retain_until=retain_until, media_type=media_type)

    @override
    async def get(self, key: str, *, sha256: bytes) -> bytes:
        return await self._inner.get(key, sha256=sha256)


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Approval:
    """What a test reads of a received document's approval."""

    decision: str | None
    emitter: str | None
    emitter_reason: str | None
    sent_to_emitter_at: datetime | None
    dgii: str | None
    dgii_reason: str | None
    sent_to_dgii_at: datetime | None
    dgii_messages: list[dict[str, str]]
    dgii_started_at: datetime | None
    attempts: int
    key: str | None
    sha256: bytes | None
    at: datetime | None
    requested_at: datetime | None


APPROVAL: Final = TypeAdapter(Approval)


@final
@dataclass(slots=True)
class Worker:
    """The worker's ``do_ecf.approve`` handler and how a test drives it."""

    approve: JobHandler
    database: BoundDatabase
    network: Network
    fake: FakeDgii

    async def run(self, tenant: Tenant, document: UUID) -> JobOutcome:
        return await self.approve(
            Job(
                kind=APPROVE,
                key=str(document),
                scope=tenant,
                payload={},
                attempts=1,
                max_attempts=50,
                deadline_at=None,
                lease_token=new_id(),
                traceparent=None,
            )
        )


async def public_dns(host: str, port: int) -> Sequence[IPv4Address | IPv6Address]:
    del host, port
    return (PUBLIC,)


@pytest.fixture(autouse=True)
def public_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(buyers_client, "system_resolve", public_dns)


@pytest.fixture
def test_signer(signer: KeyPairSigner) -> TestSigner:
    """Signs documents and seeds (DGII's and emitters') with the session's key: the fake trusts it."""
    return TestSigner(sign=signer.sign, sign_seed=signer.sign)


@pytest.fixture
def network(fake: FakeDgii) -> Network:
    return Network(fake)


@pytest.fixture
def roots(tmp_path: Path, authority: Authority) -> Path:
    found = tmp_path / "roots.pem"
    found.write_bytes(pem(authority.certificate))
    return found


@final
@dataclass(slots=True)
class Started:
    """Workers started by a test, over the ports given (the conftest's unless changed)."""

    contexts: Contexts
    ports: dict[str, object]
    database: BoundDatabase
    network: Network
    roots: Path

    def parts(self, **changed: object) -> ChannelParts:
        settings = SETTINGS.model_copy(update={"trusted_roots": self.roots})
        context = self.contexts(
            settings=settings, bound=self.ports | changed, role="worker", http=outbound_over(self.network)
        )
        return assemble(context)

    def closed(self) -> Worker:
        """The worker's handler while the app is not running: its clients not open."""
        (approve,) = (each for each in self.parts().jobs if each.kind == APPROVE)
        return Worker(approve.handler, self.database, self.network, self.network.fake)

    @asynccontextmanager
    async def worker(self, **changed: object) -> AsyncGenerator[Worker]:
        parts = self.parts(**changed)
        (approve,) = (each for each in parts.jobs if each.kind == APPROVE)
        async with AsyncExitStack() as stack:
            for resource in parts.resources:
                await stack.enter_async_context(resource)
            yield Worker(approve.handler, self.database, self.network, self.network.fake)


@pytest.fixture
def started(
    contexts: Contexts,
    ports: dict[str, object],
    module_database: BoundDatabase,
    network: Network,
    roots: Path,
) -> Started:
    return Started(contexts, ports, module_database, network, roots)


@pytest.fixture
async def worker(started: Started) -> AsyncGenerator[Worker]:
    async with started.worker() as running:
        yield running


async def decided(
    database: BoundDatabase,
    decision: ApprovalRequest,
    *,
    environment: Environment = Environment.TESTECF,
    authenticates: bool = True,
) -> tuple[Tenant, UUID]:
    """A company that received an e-CF of ``ISSUER``'s and decided ``decision`` (its job enqueued)."""
    tenant = Tenant(OrganizationId(new_id()), CompanyId(new_id()), Mode.TEST)
    kind = EcfType.CREDIT_INVOICE
    received = ReceivedDocument(
        id=new_id(),
        environment=environment,
        ecf=InboundEcf(
            ecf_type=kind,
            encf=Encf(kind, 7),
            issuer_rnc=ISSUER,
            issuer_name="Constructora del Caribe SRL",
            buyer_rnc=BUYER,
            issued_on=date(2026, 10, 13),
            total=Decimal("1180.00"),
            itbis=Decimal("180.00"),
            signed_at=SIGNED_AT,
            security_code="AbC123",
        ),
        received_at=SIGNED_AT,
        document=Archived(key="received/7", sha256=bytes(32)),
        receipt=Archived(key="arecf/7", sha256=bytes(32)),
        version=1,
    )
    async with database.unit(tenant) as unit:
        bound = bind(unit)
        await bound.received.add(received)
        if authenticates:
            await bound.options.put(DeliveryOptions(buyer_authentication=True))
        _ = await CommercialApprovals.request(bound, received.id, decision)
    return tenant, received.id


APPROVED: Final = ApprovalRequest(decision=Decision.APPROVED)


async def approval_of(database: BoundDatabase, tenant: Tenant, document: UUID) -> Approval:
    record = ReceivedDocumentRecord
    async with database.unit(tenant) as unit:
        found = (
            await unit.connection.execute(
                select(
                    record.approval_decision.label("decision"),
                    record.approval_emitter.label("emitter"),
                    record.approval_emitter_reason.label("emitter_reason"),
                    record.approval_sent_to_emitter_at.label("sent_to_emitter_at"),
                    record.approval_dgii.label("dgii"),
                    record.approval_dgii_reason.label("dgii_reason"),
                    record.approval_sent_to_dgii_at.label("sent_to_dgii_at"),
                    record.approval_dgii_messages.label("dgii_messages"),
                    record.approval_dgii_started_at.label("dgii_started_at"),
                    record.approval_attempts.label("attempts"),
                    record.approval_key.label("key"),
                    record.approval_sha256.label("sha256"),
                    record.approval_at.label("at"),
                    record.approval_requested_at.label("requested_at"),
                ).where(record.id == document)
            )
        ).mappings()
    return APPROVAL.validate_python(dict(found.one()))


async def set_row(database: BoundDatabase, tenant: Tenant, document: UUID, **values: object) -> None:
    async with database.unit(tenant) as unit:
        await unit.connection.execute(
            update(ReceivedDocumentRecord).where(ReceivedDocumentRecord.id == document).values(**values)
        )


async def audited(database: BoundDatabase, tenant: Tenant, action: str) -> list[dict[str, str]]:
    audit = CHASSIS.audit_events
    async with database.unit(tenant) as unit:
        rows = await unit.connection.execute(
            select(audit.c.detail)
            .where(audit.c.company_id == tenant.company, audit.c.action == action)
            .order_by(audit.c.occurred_at, audit.c.id)
        )
        return ROWS.validate_python([detail for (detail,) in rows.all()])


def sent(party: str, outcome: str, reason: str = "") -> dict[str, str]:
    return {"party": party, "outcome": outcome, "reason": reason}


def fields_of(acecf: bytes) -> dict[str, str]:
    """The ACECF's detail, element by element (its signature apart)."""
    root = etree.fromstring(acecf)
    (detail,) = (child for child in root if child.tag != ACECF)
    return {str(child.tag): child.text or "" for child in detail}


async def database_now(database: BoundDatabase) -> datetime:
    async with database.unit(None) as unit:
        return unit.now


# -- The plan's cases ---------------------------------------------------------------------------------------


async def test_an_approval_is_signed_once_sent_to_dgii_and_to_the_emitter_each_recorded_and_audited(
    worker: Worker, module_database: BoundDatabase, archive: StoreArchive, issued: Issued
) -> None:
    tenant, document = await decided(module_database, APPROVED)
    assert await worker.run(tenant, document) == Done()
    state = worker.fake.state
    (to_dgii,), (to_emitter,) = state.dgii_approvals, state.approvals
    assert to_dgii == to_emitter  # one ACECF, signed once, for both
    assert verify_enveloped(to_dgii, INBOUND).certificate == issued.certificate
    assert fields_of(to_dgii) == {
        "Version": "1.0",
        "RNCEmisor": ISSUER,
        "eNCF": "E310000000007",
        "FechaEmision": "13-10-2026",
        "MontoTotal": "1180.00",
        "RNCComprador": BUYER,
        "Estado": "1",
        "FechaHoraAprobacionComercial": "13-10-2026 09:30:00",
    }
    found = await approval_of(module_database, tenant, document)
    assert (found.decision, found.dgii, found.emitter, found.attempts) == ("approved", "sent", "sent", 0)
    assert found.dgii_messages == [{"code": "", "text": "Aprobación comercial recibida"}]
    assert found.sent_to_dgii_at is not None
    assert found.sent_to_emitter_at is not None
    assert found.key is not None
    assert found.sha256 is not None
    assert "/acecf/" in found.key
    assert await archive.get(found.key, sha256=found.sha256) == to_dgii
    assert await audited(module_database, tenant, "received_document.approval_sent") == [
        sent("dgii", "sent"),
        sent("emitter", "sent"),
    ]
    assert await audited(module_database, tenant, "do_ecf.buyer_seed.signed") == [
        {"endpoint_host": "ecf.fake.test", "emitter": "******193"}
    ]
    assert await worker.run(tenant, document) == Done()  # settled: nothing signed or sent again
    assert (len(state.dgii_approvals), len(state.approvals)) == (1, 1)


async def test_a_rejection_s_acecf_says_why(worker: Worker, module_database: BoundDatabase) -> None:
    rejected = ApprovalRequest(decision=Decision.REJECTED, reason="Mercancía no recibida")
    tenant, document = await decided(module_database, rejected)
    assert await worker.run(tenant, document) == Done()
    (acecf,) = worker.fake.state.dgii_approvals
    shown = fields_of(acecf)
    assert (shown["Estado"], shown["DetalleMotivoRechazo"]) == ("2", "Mercancía no recibida")


async def test_an_emitter_dgii_lists_no_receiver_for_gets_nothing_and_dgii_gets_it(
    worker: Worker, module_database: BoundDatabase
) -> None:
    worker.network.unlisted = True
    tenant, document = await decided(module_database, APPROVED)
    assert await worker.run(tenant, document) == Done()
    found = await approval_of(module_database, tenant, document)
    assert (found.dgii, found.emitter, found.emitter_reason) == (
        "sent",
        "not_applicable",
        "no_directory_entry",
    )
    assert (len(worker.fake.state.dgii_approvals), worker.network.asked(EMITTER_APPROVAL)) == (1, 0)
    assert await audited(module_database, tenant, "received_document.approval_sent") == [
        sent("dgii", "sent"),
        sent("emitter", "not_applicable", "no_directory_entry"),
    ]


async def test_in_certecf_without_a_directory_only_dgii_gets_it(
    worker: Worker, module_database: BoundDatabase
) -> None:
    tenant, document = await decided(module_database, APPROVED, environment=Environment.CERTECF)
    assert await worker.run(tenant, document) == Done()
    found = await approval_of(module_database, tenant, document)
    assert (found.dgii, found.emitter, found.emitter_reason) == (
        "sent",
        "not_applicable",
        "no_directory_in_environment",
    )
    assert worker.network.asked(DIRECTORY) == 0


# -- Retried, refused, unknown -------------------------------------------------------------------------------


async def test_an_emitter_out_of_reach_is_tried_again_on_the_schedule_then_failed_and_reported(
    worker: Worker, module_database: BoundDatabase
) -> None:
    worker.network.answering[EMITTER_APPROVAL] = 503
    tenant, document = await decided(module_database, APPROVED)
    waits: list[timedelta] = []
    for _ in range(2):
        before = await database_now(module_database)
        outcome = await worker.run(tenant, document)
        assert isinstance(outcome, RunAgain)
        waits.append(outcome.at - before)
    assert timedelta(minutes=1) <= waits[0] < timedelta(minutes=1) + SLACK
    assert timedelta(minutes=5) <= waits[1] < timedelta(minutes=5) + SLACK
    found = await approval_of(module_database, tenant, document)
    assert (found.dgii, found.emitter, found.emitter_reason, found.attempts) == (
        "sent",
        "pending",
        "do_ecf_buyer_unavailable",
        2,
    )
    assert len(worker.fake.state.dgii_approvals) == 1  # DGII once, whatever the emitter does
    assert found.requested_at is not None
    await set_row(
        module_database, tenant, document, approval_requested_at=found.requested_at - timedelta(days=7)
    )
    with capture_logs() as logs:
        assert await worker.run(tenant, document) == Done()
    failed = await approval_of(module_database, tenant, document)
    assert (failed.dgii, failed.emitter, failed.emitter_reason) == (
        "sent",
        "failed",
        "do_ecf_buyer_unavailable",
    )
    assert [(entry["event"], entry["party"], entry["outcome"]) for entry in logs] == [
        ("do_ecf.approval_unsent", "emitter", "failed")
    ]
    assert (await audited(module_database, tenant, "received_document.approval_sent"))[-1] == sent(
        "emitter", "failed", "do_ecf_buyer_unavailable"
    )
    assert len(worker.fake.state.dgii_approvals) == 1


async def test_an_emitter_requiring_authentication_while_it_is_off_is_alerted_and_tried_again(
    worker: Worker, module_database: BoundDatabase
) -> None:
    tenant, document = await decided(module_database, APPROVED, authenticates=False)
    with capture_logs() as logs:
        assert isinstance(await worker.run(tenant, document), RunAgain)
    assert [entry["event"] for entry in logs] == ["do_ecf.approval_needs_authentication"]
    found = await approval_of(module_database, tenant, document)
    assert (found.emitter, found.emitter_reason) == ("pending", "do_ecf_buyer_requires_authentication")
    assert worker.network.asked(EMITTER_SEED) == 0
    assert await audited(module_database, tenant, "do_ecf.buyer_seed.signed") == []


async def test_dgii_s_directory_out_of_reach_leaves_the_emitter_to_try_again(
    worker: Worker, module_database: BoundDatabase
) -> None:
    worker.network.answering[DIRECTORY] = 503
    tenant, document = await decided(module_database, APPROVED)
    assert isinstance(await worker.run(tenant, document), RunAgain)
    found = await approval_of(module_database, tenant, document)
    assert (found.dgii, found.emitter, found.emitter_reason) == ("sent", "pending", "do_ecf_dgii_unavailable")


async def test_a_refusal_by_both_parties_is_kept_never_sent_again_and_reported(
    worker: Worker, module_database: BoundDatabase
) -> None:
    worker.network.answering |= {DGII_APPROVAL: 400, EMITTER_APPROVAL: 400}
    tenant, document = await decided(module_database, APPROVED)
    with capture_logs() as logs:
        assert await worker.run(tenant, document) == Done()
    found = await approval_of(module_database, tenant, document)
    assert (found.dgii, found.dgii_reason, found.emitter, found.emitter_reason) == (
        "refused",
        "refused",
        "refused",
        "refused",
    )
    assert found.dgii_messages == [{"code": "400", "text": ""}]
    assert [(entry["party"], entry["outcome"]) for entry in logs] == [
        ("dgii", "refused"),
        ("emitter", "refused"),
    ]
    assert await worker.run(tenant, document) == Done()
    assert (worker.network.asked(DGII_APPROVAL), worker.network.asked(EMITTER_APPROVAL)) == (1, 1)


async def test_an_approval_whose_outcome_at_dgii_is_unknown_is_never_sent_to_dgii_again(
    worker: Worker, module_database: BoundDatabase
) -> None:
    """Review Focus 1: a 5xx after sending may mean DGII took it."""
    worker.network.answering[DGII_APPROVAL] = 503
    tenant, document = await decided(module_database, APPROVED)
    with capture_logs() as logs:
        assert await worker.run(tenant, document) == Done()
    found = await approval_of(module_database, tenant, document)
    assert (found.dgii, found.dgii_reason, found.emitter) == ("outcome_unknown", "server_error", "sent")
    assert [(entry["party"], entry["outcome"]) for entry in logs] == [("dgii", "outcome_unknown")]
    assert await worker.run(tenant, document) == Done()
    assert worker.network.asked(DGII_APPROVAL) == 1


async def test_a_mark_no_run_answered_is_an_unknown_outcome_and_dgii_is_never_asked_again(
    worker: Worker, module_database: BoundDatabase
) -> None:
    """DGII throttling clears the mark (it certainly did not take it); a mark left by a run that died does
    not: the next run never sends it, nor the emitter's again."""
    worker.network.answering[DGII_APPROVAL] = 429
    tenant, document = await decided(module_database, APPROVED)
    assert isinstance(await worker.run(tenant, document), RunAgain)
    found = await approval_of(module_database, tenant, document)
    assert (found.dgii, found.dgii_reason, found.dgii_started_at, found.emitter) == (
        "pending",
        "do_ecf_dgii_unavailable",
        None,
        "sent",
    )
    assert found.key is not None
    signed = found.key
    await set_row(
        module_database, tenant, document, approval_dgii_started_at=await database_now(module_database)
    )
    worker.network.answering.clear()
    with capture_logs() as logs:
        assert await worker.run(tenant, document) == Done()
    after = await approval_of(module_database, tenant, document)
    assert (after.dgii, after.emitter, after.key) == ("outcome_unknown", "sent", signed)  # signed once
    assert (worker.network.asked(DGII_APPROVAL), worker.network.asked(EMITTER_APPROVAL)) == (1, 1)
    assert [(entry["party"], entry["outcome"]) for entry in logs] == [("dgii", "outcome_unknown")]


async def test_an_acecf_the_archive_cannot_keep_is_tried_again_then_both_parties_failed(
    started: Started, module_database: BoundDatabase, archive: StoreArchive
) -> None:
    tenant, document = await decided(module_database, APPROVED)
    async with started.worker(**{ARCHIVE_PORT.name: LosingApprovals(archive)}) as worker:
        assert isinstance(await worker.run(tenant, document), RunAgain)
        found = await approval_of(module_database, tenant, document)
        assert (found.dgii_reason, found.emitter_reason, found.key) == (
            "do_ecf_archive_unavailable",
            "do_ecf_archive_unavailable",
            None,
        )
        assert found.requested_at is not None
        moved = found.requested_at - timedelta(days=7)
        await set_row(module_database, tenant, document, approval_requested_at=moved)
        with capture_logs() as logs:
            assert await worker.run(tenant, document) == Done()
    failed = await approval_of(module_database, tenant, document)
    assert (failed.dgii, failed.emitter) == ("failed", "failed")
    assert [(entry["party"], entry["outcome"]) for entry in logs] == [
        ("dgii", "failed"),
        ("emitter", "failed"),
    ]
    assert (worker.network.asked(DGII_APPROVAL), worker.network.asked(EMITTER_APPROVAL)) == (0, 0)


async def test_a_party_another_run_settled_meanwhile_is_left_as_that_run_recorded_it(
    worker: Worker, module_database: BoundDatabase
) -> None:
    tenant, document = await decided(module_database, APPROVED)

    async def dgii_settled() -> None:
        now = await database_now(module_database)
        await set_row(module_database, tenant, document, approval_dgii="sent", approval_sent_to_dgii_at=now)

    async def emitter_settled() -> None:
        await set_row(module_database, tenant, document, approval_emitter="refused")

    worker.network.meanwhile |= {DGII_APPROVAL: dgii_settled, EMITTER_APPROVAL: emitter_settled}
    assert await worker.run(tenant, document) == Done()
    found = await approval_of(module_database, tenant, document)
    assert (found.dgii, found.emitter, found.dgii_messages) == ("sent", "refused", [])
    assert await audited(module_database, tenant, "received_document.approval_sent") == []


async def test_a_job_for_no_document_or_an_undecided_one_is_a_defect(
    worker: Worker, module_database: BoundDatabase
) -> None:
    tenant, document = await decided(module_database, APPROVED)
    with pytest.raises(ReceivedNotFoundError):
        await worker.run(tenant, new_id())
    await set_row(
        module_database,
        tenant,
        document,
        approval_decision=None,
        approval_requested_at=None,
        approval_emitter=None,
        approval_dgii=None,
    )
    with pytest.raises(ApprovalMissingError):
        await worker.run(tenant, document)


async def test_nothing_is_marked_for_dgii_while_its_client_is_not_open(
    started: Started, module_database: BoundDatabase
) -> None:
    """A mark is set only to send: a run that cannot reach DGII's client leaves none for the next to find."""
    tenant, document = await decided(module_database, APPROVED)
    with pytest.raises(OutboundClosedError):
        await started.closed().run(tenant, document)
    found = await approval_of(module_database, tenant, document)
    assert (found.dgii, found.dgii_started_at) == ("pending", None)
    assert found.key is not None  # signed and kept: the next run sends this one
    async with started.worker() as worker:
        assert await worker.run(tenant, document) == Done()
    assert (await approval_of(module_database, tenant, document)).dgii == "sent"


# -- The review's cases (2026-10-26) ------------------------------------------------------------------------


async def test_the_mark_is_committed_before_dgii_is_called_and_a_run_that_died_after_sending_never_resends(
    worker: Worker, module_database: BoundDatabase
) -> None:
    """Review I1: the call reaches DGII only once its mark is committed; the run that sent it dies before the
    answer; the next run finds the mark and never sends it again."""
    tenant, document = await decided(module_database, APPROVED)
    seen: list[datetime | None] = []

    async def mark_committed() -> None:
        seen.append((await approval_of(module_database, tenant, document)).dgii_started_at)

    worker.network.meanwhile[DGII_APPROVAL] = mark_committed
    worker.network.dying.add(DGII_APPROVAL)
    with pytest.raises(RunDiedError):
        await worker.run(tenant, document)
    assert len(seen) == 1
    assert seen[0] is not None
    assert len(worker.fake.state.dgii_approvals) == 1  # it reached DGII
    worker.network.meanwhile.clear()
    worker.network.dying.clear()
    with capture_logs() as logs:
        assert await worker.run(tenant, document) == Done()
    found = await approval_of(module_database, tenant, document)
    assert (found.dgii, found.emitter) == ("outcome_unknown", "sent")
    assert (len(worker.fake.state.dgii_approvals), worker.network.asked(DGII_APPROVAL)) == (1, 1)
    assert [(entry["party"], entry["outcome"]) for entry in logs] == [("dgii", "outcome_unknown")]


def not_ready(self: DgiiClient, environment: Environment) -> None:
    """``DgiiClient.require_ready`` of a client not open."""
    del self, environment
    raise AuthorityUnavailableError(Failure.NOT_OPEN, "reception")


def tampered(sign: Callable[[bytes], bytes]) -> Callable[[bytes], bytes]:
    """``sign``, its ACECF changed after it signed it (an approval turned into a rejection)."""

    def signing(document: bytes) -> bytes:
        return sign(document).replace(b"<Estado>1</Estado>", b"<Estado>2</Estado>")

    return signing


async def test_an_acecf_whose_signature_does_not_hold_is_neither_kept_nor_sent(
    started: Started, module_database: BoundDatabase, archive: StoreArchive, signer: KeyPairSigner
) -> None:
    """Review I2: the signed ACECF is checked (schema, signature) before the archive or anyone sees it."""
    tenant, document = await decided(module_database, APPROVED)
    watching = Watching(archive)
    broken = TestSigner(sign=tampered(signer.sign), sign_seed=signer.sign)
    async with started.worker(**{ARCHIVE_PORT.name: watching, SIGNER_PORT.name: broken}) as worker:
        with pytest.raises(EcfInvalidError):
            await worker.run(tenant, document)
    assert watching.kept == []
    assert (await approval_of(module_database, tenant, document)).key is None
    assert (worker.network.asked(DGII_APPROVAL), worker.network.asked(EMITTER_APPROVAL)) == (0, 0)


async def test_dgii_s_client_not_ready_leaves_dgii_to_try_again_and_the_emitter_still_gets_it(
    worker: Worker, module_database: BoundDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review I3: an error of DGII's client before anything is marked is DGII's alone."""
    monkeypatch.setattr(DgiiClient, "require_ready", not_ready)
    tenant, document = await decided(module_database, APPROVED)
    assert isinstance(await worker.run(tenant, document), RunAgain)
    found = await approval_of(module_database, tenant, document)
    assert (found.dgii, found.dgii_reason, found.dgii_started_at, found.emitter) == (
        "pending",
        "do_ecf_dgii_unavailable",
        None,
        "sent",
    )
    assert worker.network.asked(DGII_APPROVAL) == 0


async def test_the_run_holding_the_mark_records_dgii_s_answer_over_an_unknown_outcome_another_run_inferred(
    worker: Worker, module_database: BoundDatabase
) -> None:
    """Review M-a: a run that found the mark while the call was under way inferred an unknown outcome; the run
    that sent it has DGII's answer, which is not lost."""
    tenant, document = await decided(module_database, APPROVED)

    async def inferred() -> None:
        await set_row(module_database, tenant, document, approval_dgii="outcome_unknown")

    worker.network.meanwhile[DGII_APPROVAL] = inferred
    assert await worker.run(tenant, document) == Done()
    found = await approval_of(module_database, tenant, document)
    assert (found.dgii, found.dgii_messages) == (
        "sent",
        [{"code": "", "text": "Aprobación comercial recibida"}],
    )
    assert found.sent_to_dgii_at is not None


async def test_of_two_runs_that_signed_the_first_recorded_acecf_is_the_one_sent(
    started: Started, module_database: BoundDatabase, archive: StoreArchive, signer: KeyPairSigner
) -> None:
    """Review M-b: another run signed and recorded its ACECF while this one was keeping its own."""
    tenant, document = await decided(module_database, APPROVED)
    first = signer.sign(
        render_acecf(
            CommercialApproval(
                issuer_rnc=ISSUER,
                buyer_rnc=BUYER,
                encf=Encf(EcfType.CREDIT_INVOICE, 7),
                issued_on=date(2026, 10, 13),
                total=Decimal("1180.00"),
                approved=True,
                reason=None,
                at=SIGNED_AT,
            )
        )
    )
    watching = Watching(archive)

    async def recorded_first() -> None:
        key = archive_key(channel="do-ecf-v1", tenant=tenant, kind="acecf", content=first, extension="xml")
        kept = await archive.put_once(
            key, first, retain_until=date(2036, 12, 31), media_type="application/xml"
        )
        values = {"approval_key": kept.key, "approval_sha256": kept.sha256, "approval_at": SIGNED_AT}
        await set_row(module_database, tenant, document, **values)

    watching.before_acecf = recorded_first
    async with started.worker(**{ARCHIVE_PORT.name: watching}) as worker:
        assert await worker.run(tenant, document) == Done()
    state = worker.fake.state
    assert (state.dgii_approvals, state.approvals) == ([first], [first])
    found = await approval_of(module_database, tenant, document)
    assert found.at == SIGNED_AT


async def test_a_document_that_names_no_buyer_any_more_is_never_retried_silently(
    worker: Worker, module_database: BoundDatabase
) -> None:
    """Review M-c: only what may pass (out of reach, the company's certificate) is tried again."""
    tenant, document = await decided(module_database, APPROVED)
    await set_row(module_database, tenant, document, buyer_rnc=None)
    with pytest.raises(NotTheBuyerError):
        await worker.run(tenant, document)
    assert (await approval_of(module_database, tenant, document)).attempts == 0


# -- The re-review's cases (2026-10-26) ---------------------------------------------------------------------


async def test_a_run_that_set_no_mark_never_clears_another_run_s(
    worker: Worker, module_database: BoundDatabase
) -> None:
    """Re-review I4: while one run's call is at DGII, a second run whose DGII client is not ready records DGII
    pending and leaves the mark; the first dies; the next finds the mark and never sends again."""
    tenant, document = await decided(module_database, APPROVED)
    marks: list[datetime | None] = []

    async def second_run() -> None:
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(DgiiClient, "require_ready", not_ready)
            assert isinstance(await worker.run(tenant, document), RunAgain)
        marks.append((await approval_of(module_database, tenant, document)).dgii_started_at)

    worker.network.meanwhile[DGII_APPROVAL] = second_run
    worker.network.dying.add(DGII_APPROVAL)
    with pytest.raises(RunDiedError):
        await worker.run(tenant, document)
    assert len(marks) == 1
    assert marks[0] is not None
    worker.network.meanwhile.clear()
    worker.network.dying.clear()
    assert await worker.run(tenant, document) == Done()
    assert (await approval_of(module_database, tenant, document)).dgii == "outcome_unknown"
    assert len(worker.fake.state.dgii_approvals) == 1


async def test_a_run_that_set_no_mark_leaves_an_unknown_outcome_as_it_is(
    worker: Worker, module_database: BoundDatabase
) -> None:
    """Re-review I4, its variant: the row is already outcome_unknown when the not-ready run records."""
    tenant, document = await decided(module_database, APPROVED)

    async def inferred_then_second_run() -> None:
        await set_row(module_database, tenant, document, approval_dgii="outcome_unknown")
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(DgiiClient, "require_ready", not_ready)
            _ = await worker.run(tenant, document)
        assert (await approval_of(module_database, tenant, document)).dgii == "outcome_unknown"
        await set_row(module_database, tenant, document, approval_dgii="pending")

    worker.network.meanwhile[DGII_APPROVAL] = inferred_then_second_run
    assert await worker.run(tenant, document) == Done()
    assert (await approval_of(module_database, tenant, document)).dgii == "sent"


async def test_an_answer_under_a_mark_the_row_no_longer_holds_replaces_no_unknown_outcome(
    worker: Worker, module_database: BoundDatabase
) -> None:
    """Re-review I4 (Ma3): only the run holding the row's own mark replaces an unknown outcome."""
    tenant, document = await decided(module_database, APPROVED)

    async def another_mark() -> None:
        found = await approval_of(module_database, tenant, document)
        assert found.dgii_started_at is not None
        moved = found.dgii_started_at + timedelta(seconds=1)
        values = {"approval_dgii": "outcome_unknown", "approval_dgii_started_at": moved}
        await set_row(module_database, tenant, document, **values)

    worker.network.meanwhile[DGII_APPROVAL] = another_mark
    assert await worker.run(tenant, document) == Done()
    found = await approval_of(module_database, tenant, document)
    assert (found.dgii, found.dgii_messages) == ("outcome_unknown", [])


async def test_the_run_holding_the_mark_keeps_an_inferred_unknown_outcome_when_dgii_answered_nothing(
    worker: Worker, module_database: BoundDatabase
) -> None:
    """Re-review I5: only DGII's own answer replaces an unknown outcome another run inferred; a throttled call
    leaves it, and the mark, exactly as they are."""
    tenant, document = await decided(module_database, APPROVED)
    marks: list[datetime | None] = []

    async def inferred() -> None:
        marks.append((await approval_of(module_database, tenant, document)).dgii_started_at)
        await set_row(module_database, tenant, document, approval_dgii="outcome_unknown")

    worker.network.meanwhile[DGII_APPROVAL] = inferred
    worker.network.answering[DGII_APPROVAL] = 429
    assert await worker.run(tenant, document) == Done()
    found = await approval_of(module_database, tenant, document)
    assert (found.dgii, found.dgii_started_at) == ("outcome_unknown", marks[0])
    assert marks[0] is not None
    assert await worker.run(tenant, document) == Done()
    assert (worker.network.asked(DGII_APPROVAL), len(worker.fake.state.dgii_approvals)) == (1, 0)


async def test_a_run_whose_dgii_was_given_up_meanwhile_marks_and_sends_nothing(
    started: Started, module_database: BoundDatabase, archive: StoreArchive
) -> None:
    """Re-review Minor 1: the run read DGII pending; another run gave it up before this one marked."""
    tenant, document = await decided(module_database, APPROVED)
    watching = Watching(archive)

    async def given_up() -> None:
        await set_row(module_database, tenant, document, approval_dgii="failed")

    watching.before_acecf = given_up
    async with started.worker(**{ARCHIVE_PORT.name: watching}) as worker:
        assert await worker.run(tenant, document) == Done()
    found = await approval_of(module_database, tenant, document)
    assert (found.dgii, found.dgii_started_at, found.emitter) == ("failed", None, "sent")
    assert (worker.network.asked(DGII_APPROVAL), len(worker.fake.state.dgii_approvals)) == (0, 0)


async def test_seven_days_over_with_a_mark_set_is_an_unknown_outcome_not_a_failure(
    worker: Worker, module_database: BoundDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-review Minor 2: a marked approval may be at DGII, however long ago it was decided."""
    tenant, document = await decided(module_database, APPROVED)
    found = await approval_of(module_database, tenant, document)
    assert found.requested_at is not None
    values = {
        "approval_requested_at": found.requested_at - timedelta(days=8),
        "approval_dgii_started_at": found.requested_at - timedelta(days=8),
    }
    worker.network.answering[EMITTER_APPROVAL] = 503
    monkeypatch.setattr(DgiiClient, "require_ready", not_ready)
    assert isinstance(await worker.run(tenant, document), RunAgain)  # signed, DGII and the emitter to retry
    await set_row(module_database, tenant, document, **values)  # as if a run marked it, then died
    with capture_logs() as logs:
        assert await worker.run(tenant, document) == Done()
    after = await approval_of(module_database, tenant, document)
    assert (after.dgii, after.emitter) == ("outcome_unknown", "failed")
    assert [(entry["party"], entry["outcome"]) for entry in logs] == [
        ("dgii", "outcome_unknown"),
        ("emitter", "failed"),
    ]
    assert worker.network.asked(DGII_APPROVAL) == 0
