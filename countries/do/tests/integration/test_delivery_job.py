"""The ``do_ecf.deliver`` job against the fake DGII's emisor-receptor simulator (§8.6.5; Decisions 18-21).

An accepted e-CF is delivered to an electronic buyer, its receipt (ARECF) archived; a summary, a buyer DGII
lists no receiver for, or an environment without DGII's directory, is ``not_applicable``. A buyer out of
reach is tried again on Decision 21's schedule for seven days, then the delivery failed. A buyer's seed is
signed only when the company switched buyer authentication on (Decision 19), and every seed signed is
audited with the buyer masked (Decision 20). A receipt that fails its checks is archived apart
(``arecf-refused``) and never retried (Review Focus 4). The simulator's host is pinned to a public address,
as the spike's tests pin it.
"""

from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, replace
from datetime import date, timedelta
from ipaddress import IPv4Address
from typing import Final, Protocol, Unpack, final, override
lazy from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping, Sequence
lazy from datetime import datetime
lazy from ipaddress import IPv6Address
lazy from pathlib import Path
lazy from uuid import UUID

import httpx
import pytest
from pydantic import BaseModel, TypeAdapter
from sqlalchemy import Text, cast, select, text, update
from structlog.testing import capture_logs
lazy import psycopg

from ledger_country_do.channels.ecf_v1.adapters.buyers import client as buyers_client
from ledger_country_do.channels.ecf_v1.adapters.channel import DoEcfChannel
from ledger_country_do.channels.ecf_v1.adapters.jobs import ADVANCE, DELIVER
from ledger_country_do.channels.ecf_v1.adapters.postgres.migrations.versions import r0004_do_ecf
from ledger_country_do.channels.ecf_v1.adapters.postgres.tables import DocumentRecord
from ledger_country_do.channels.ecf_v1.adapters.postgres.unit import bind
from ledger_country_do.channels.ecf_v1.adapters.signing.verifier import INBOUND, verify_enveloped
from ledger_country_do.channels.ecf_v1.application.documents import DeliveryOptions
from ledger_country_do.channels.ecf_v1.application.issuing import DocumentMissingError
from ledger_country_do.channels.ecf_v1.application.ports import ArchiveUnavailableError
from ledger_country_do.channels.ecf_v1.application.ranges import NumberingRange
from ledger_country_do.channels.ecf_v1.assembly import assemble
from ledger_country_do.channels.ecf_v1.domain.encf import EcfType
from ledger_country_do.channels.ecf_v1.domain.environments import Environment
from ledger_country_do.channels.ecf_v1.domain.numbering import RangeState, RangeStatus
from ledger_country_do.channels.ecf_v1.settings import DoEcfSettings
from ledger_country_do_testing.fake_dgii.state import DirectoryEntry
from ledger_country_do_testing.fiscal import fiscal_draft
from ledger_country_do_testing.pki import organization
from ledger_country_do_testing.samples import SIGNER_VALID_FROM
from ledger_country_do_testing.transports import FAKE_HOSTS, outbound_over
from ledger_fiscal_kit.drafts import DocumentRef
from ledger_fiscal_kit.ports import ARCHIVE_PORT, ArchivePort
from ledger_fiscal_kit.ports import ArchiveUnavailableError as PortUnavailableError
from ledger_kernel.errors import InvalidValueError
from ledger_kernel.ids import CompanyId, OrganizationId, new_id
from ledger_kernel.tenancy import Mode, Tenant
from ledger_platform.data.tables import chassis_tables
from ledger_platform.jobs.outcomes import Done, Job, RunAgain
from ledger_platform.runtime.calls import SystemCall
from ledger_testing.pki import pem
from ledger_testing.signer import TestSigner
lazy from ledger_country_do_testing.fake_dgii.app import FakeDgii
lazy from ledger_country_do_testing.fiscal import DraftOptions
lazy from ledger_country_do_testing.local_signer import KeyPairSigner
lazy from ledger_fiscal_kit.drafts import FiscalDraft
lazy from ledger_fiscal_kit.ports import ArchivedObject
lazy from ledger_platform.config.manifest import AppRole
lazy from ledger_platform.config.settings import Environment as AppEnvironment
lazy from ledger_platform.data.bound import BoundDatabase
lazy from ledger_platform.http.outbound import OutboundHttp
lazy from ledger_platform.jobs.outcomes import JobHandler, JobOutcome
lazy from ledger_platform.runtime.archiving import StoreArchive
lazy from ledger_platform.runtime.contexts import ChannelContext
lazy from ledger_testing.pki import Authority

SETTINGS: Final = DoEcfSettings(
    receiver_public_url="https://receiver.ledger.test", ecf_host=FAKE_HOSTS.ecf, fc_host=FAKE_HOSTS.fc
)
BUDGET: Final = timedelta(seconds=1.5)
CHASSIS: Final = chassis_tables("do_ecf")
BUYER_RNC: Final = "131098193"
"""The test drafts' buyer (``fiscal_draft``'s), whom the simulator's certificate names."""
DIRECTORY: Final = "consultadirectorio/api/consultas/obtenerdirectorioporrnc"
BUYER_RECEPTION: Final = "emisorreceptor/fe/recepcion/api/ecf"
BUYER_SEED: Final = "emisorreceptor/fe/autenticacion/api/semilla"
PUBLIC: Final = IPv4Address("93.184.215.14")
"""What the simulator's host name resolves to here (nothing connects to it: an in-process transport)."""
SLACK: Final = timedelta(seconds=5)
ROWS: Final = TypeAdapter(list[tuple[str | None, ...]])
DETAIL: Final = TypeAdapter(dict[str, str])
STATUS: Final = TypeAdapter(str | None)


class _Do(BaseModel):
    delivery: str


class _Details(BaseModel):
    do: _Do


class _Update(BaseModel):
    details: _Details
    version: int


class _Published(BaseModel):
    """What a test reads of a ``fiscal_channel.document.updated`` payload."""

    update: _Update


TWO_YEARS: Final = date(2028, 12, 31)


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
    """The way to the fake DGII and its simulator, kept: ``unlisted`` answers DGII's directory empty,
    ``failing`` answers a 503 to the requests whose path ends with it, ``meanwhile`` runs before a buyer's
    reception is delivered (what a person does while the job is under way).
    """

    __slots__ = ("_inner", "failing", "fake", "meanwhile", "requests", "unlisted")

    def __init__(self, fake: FakeDgii) -> None:
        self.fake: Final = fake
        self._inner = httpx.ASGITransport(app=fake.app)
        self.requests: list[tuple[str, str]] = []
        self.unlisted = False
        self.failing: set[str] = set()
        self.meanwhile: Callable[[], Awaitable[None]] | None = None

    def asked(self, part: str) -> int:
        """How many requests whose path ends with ``part`` were made."""
        return sum(1 for _, path in self.requests if path.endswith(part))

    @override
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.requests.append((request.method, path))
        if any(path.endswith(part) for part in self.failing):
            return httpx.Response(503, stream=httpx.ByteStream(b""), request=request)
        if self.unlisted and path.endswith(DIRECTORY):
            return httpx.Response(200, stream=httpx.ByteStream(b"[]"), request=request)
        if self.meanwhile is not None and path.endswith(BUYER_RECEPTION):
            await self.meanwhile()
        return await self._inner.handle_async_request(request)


@final
class LosingReceipts(ArchivePort):
    """The test's archive, unreachable for a buyer's receipt (``arecf``) and nothing else."""

    __slots__ = ("_inner",)

    def __init__(self, inner: StoreArchive) -> None:
        self._inner = inner

    @override
    async def put_once(
        self, key: str, content: bytes, *, retain_until: date, media_type: str
    ) -> ArchivedObject:
        if "/arecf/" in key:
            raise PortUnavailableError
        return await self._inner.put_once(key, content, retain_until=retain_until, media_type=media_type)

    @override
    async def get(self, key: str, *, sha256: bytes) -> bytes:
        return await self._inner.get(key, sha256=sha256)


@final
@dataclass(frozen=True, slots=True)
class Delivery:
    """What a test reads of a document's delivery."""

    state: str
    reason: str | None
    attempts: int
    since: datetime | None
    receipt_key: str | None
    receipt_sha256: bytes | None
    version: int


DELIVERY: Final = TypeAdapter(Delivery)


@final
@dataclass(slots=True)
class Worker:
    """The worker's channel and its two jobs' handlers, and how a test drives them."""

    channel: DoEcfChannel
    advance: JobHandler
    deliver: JobHandler
    database: BoundDatabase
    network: Network
    fake: FakeDgii

    async def accepted(self, draft: FiscalDraft) -> UUID:
        """``draft``'s document, signed within the budget and taken by its advance job until DGII decided."""
        tenant = tenant_of(draft)
        await self.channel.issue(SystemCall(reason="fiscal:issue", scope=tenant), draft, sign_within=BUDGET)
        async with self.database.unit(tenant) as unit:
            found = await bind(unit).documents.of_invoice(draft.document)
        assert found is not None
        outcomes = [await self.advance(job_of(ADVANCE, tenant, found.id)) for _ in range(3)]
        assert Done() in outcomes
        return found.id

    async def delivered(self, tenant: Tenant, document: UUID) -> JobOutcome:
        """One run of the document's delivery job."""
        return await self.deliver(job_of(DELIVER, tenant, document))

    async def job(self, document: UUID) -> str | None:
        """The status of the document's ``do_ecf.deliver`` job, if any."""
        jobs = CHASSIS.jobs
        async with self.database.unit(None) as unit:
            found = await unit.connection.execute(
                select(jobs.c.status).where(jobs.c.kind == DELIVER, jobs.c.key == str(document))
            )
            return STATUS.validate_python(found.scalar_one_or_none())


def job_of(kind: str, tenant: Tenant, document: UUID) -> Job:
    return Job(
        kind=kind,
        key=str(document),
        scope=tenant,
        payload={},
        attempts=1,
        max_attempts=50,
        deadline_at=None,
        lease_token=new_id(),
        traceparent=None,
    )


async def public_dns(host: str, port: int) -> Sequence[IPv4Address | IPv6Address]:
    del host, port
    return (PUBLIC,)


@pytest.fixture
def test_signer(signer: KeyPairSigner) -> TestSigner:
    """Signs documents and seeds (DGII's and buyers') with the session's key: the fake trusts it."""
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

    @asynccontextmanager
    async def worker(self, **changed: object) -> AsyncGenerator[Worker]:
        settings = SETTINGS.model_copy(update={"trusted_roots": self.roots})
        context = self.contexts(
            settings=settings,
            bound=self.ports | changed,
            role="worker",
            http=outbound_over(self.network),
        )
        parts = assemble(context)
        channel = parts.channel
        assert isinstance(channel, DoEcfChannel)
        advance, deliver = parts.jobs
        async with AsyncExitStack() as stack:
            for resource in parts.resources:
                await stack.enter_async_context(resource)
            yield Worker(
                channel, advance.handler, deliver.handler, self.database, self.network, self.network.fake
            )


@pytest.fixture
def started(
    contexts: Contexts,
    ports: dict[str, object],
    module_database: BoundDatabase,
    network: Network,
    roots: Path,
) -> Started:
    return Started(contexts, ports, module_database, network, roots)


@pytest.fixture(autouse=True)
def public_names(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every buyer's host name resolves to a public address (``PUBLIC``), as the spike's tests pin it."""
    monkeypatch.setattr(buyers_client, "system_resolve", public_dns)


@pytest.fixture
async def worker(started: Started) -> AsyncGenerator[Worker]:
    async with started.worker() as running:
        yield running


def tenant_of(draft: FiscalDraft) -> Tenant:
    return Tenant(draft.organization, draft.company, draft.mode)


async def a_company(
    database: BoundDatabase,
    *ecf_types: EcfType,
    environment: Environment = Environment.TESTECF,
    authenticates: bool = False,
) -> Tenant:
    """A company with a range of each of ``ecf_types`` (31 by default), its options as told."""
    tenant = Tenant(OrganizationId(new_id()), CompanyId(new_id()), Mode.TEST)
    async with database.unit(tenant) as unit:
        bound = bind(unit)
        for ecf_type in ecf_types or (EcfType.CREDIT_INVOICE,):
            await bound.ranges.series(environment, ecf_type)
            authorized = RangeState(
                id=new_id(),
                ecf_type=ecf_type,
                first=1,
                last=100,
                next=1,
                expires_on=TWO_YEARS,
                status=RangeStatus.ACTIVE,
            )
            await bound.ranges.add(
                NumberingRange(environment=environment, range=authorized, created_at=unit.now, version=1)
            )
        if authenticates:
            await bound.options.put(DeliveryOptions(buyer_authentication=True))
    return tenant


def draft_of(tenant: Tenant, **options: Unpack[DraftOptions]) -> FiscalDraft:
    return replace(
        fiscal_draft(**options),
        organization=tenant.organization,
        company=tenant.company,
        document=DocumentRef(new_id()),
    )


async def delivery_of(database: BoundDatabase, tenant: Tenant, document: UUID) -> Delivery:
    async with database.unit(tenant) as unit:
        found = (
            await unit.connection.execute(
                select(
                    DocumentRecord.delivery_state.label("state"),
                    DocumentRecord.delivery_reason.label("reason"),
                    DocumentRecord.delivery_attempts.label("attempts"),
                    DocumentRecord.delivery_since.label("since"),
                    DocumentRecord.receipt_key,
                    DocumentRecord.receipt_sha256,
                    DocumentRecord.version,
                ).where(DocumentRecord.id == document)
            )
        ).mappings()
    return DELIVERY.validate_python(dict(found.one()))


async def set_row(database: BoundDatabase, tenant: Tenant, document: UUID, **values: object) -> None:
    async with database.unit(tenant) as unit:
        await unit.connection.execute(
            update(DocumentRecord).where(DocumentRecord.id == document).values(**values)
        )


async def database_now(database: BoundDatabase) -> datetime:
    async with database.unit(None) as unit:
        return unit.now


async def published_deliveries(database: BoundDatabase, tenant: Tenant) -> list[tuple[str, int]]:
    """``(delivery, version)`` of every ``fiscal_channel.document.updated`` of ``tenant``, in order."""
    outbox = CHASSIS.outbox
    async with database.unit(None) as unit:
        rows = await unit.connection.execute(
            select(outbox.c.payload)
            .where(outbox.c.type == "fiscal_channel.document.updated", outbox.c.company_id == tenant.company)
            .order_by(outbox.c.aggregate_version)
        )
        published = [_Published.model_validate(payload).update for (payload,) in rows.all()]
    return [(each.details.do.delivery, each.version) for each in published]


async def audit_rows(database: BoundDatabase, tenant: Tenant) -> list[tuple[str | None, ...]]:
    """Every column of ``tenant``'s audit rows, as text."""
    audit = CHASSIS.audit_events
    async with database.unit(tenant) as unit:
        rows = await unit.connection.execute(
            select(*(cast(column, Text) for column in audit.columns)).where(
                audit.c.company_id == tenant.company
            )
        )
        return ROWS.validate_python([tuple(row) for row in rows.all()])


async def seeds_audited(database: BoundDatabase, tenant: Tenant) -> list[dict[str, str]]:
    audit = CHASSIS.audit_events
    async with database.unit(tenant) as unit:
        rows = await unit.connection.execute(
            select(audit.c.detail, audit.c.subject_type).where(
                audit.c.company_id == tenant.company, audit.c.action == "do_ecf.buyer_seed.signed"
            )
        )
        found: list[dict[str, str]] = []
        for detail, subject_type in rows.all():
            assert subject_type == "do_ecf"
            found.append(DETAIL.validate_python(detail))
        return found


def seeds(signer: TestSigner) -> int:
    return sum(1 for call in signer.calls if call.audience is not None)


# -- The plan's cases ---------------------------------------------------------------------------------------


async def test_an_accepted_e_cf_reaches_its_buyer_whose_receipt_is_archived_and_verified(
    worker: Worker, module_database: BoundDatabase, archive: StoreArchive
) -> None:
    """Decided accepted, the delivery job was enqueued with the decision (Task 19's binding)."""
    worker.fake.state.receiver_requires_auth = False
    tenant = await a_company(module_database)
    document = await worker.accepted(draft_of(tenant))
    assert await worker.job(document) == "ready"
    before = await database_now(module_database)
    assert await worker.delivered(tenant, document) == Done()
    found = await delivery_of(module_database, tenant, document)
    assert (found.state, found.reason, found.attempts) == ("delivered", None, 0)
    assert found.since is not None
    assert found.since >= before
    assert found.receipt_key is not None
    assert found.receipt_sha256 is not None
    assert "/arecf/" in found.receipt_key
    receipt = await archive.get(found.receipt_key, sha256=found.receipt_sha256)
    assert verify_enveloped(receipt, INBOUND).certificate == worker.fake.state.receiver.certificate
    assert (worker.fake.received[-1].kind, worker.network.asked(BUYER_RECEPTION)) == ("buyer", 1)
    assert (await published_deliveries(module_database, tenant))[-2:] == [
        ("not_started", 4),
        ("delivered", 5),
    ]
    assert await seeds_audited(module_database, tenant) == []  # no seed was signed: none asked
    assert await worker.delivered(tenant, document) == Done()  # settled: nothing sent again
    assert worker.network.asked(BUYER_RECEPTION) == 1


async def test_a_consumer_invoice_summary_is_never_delivered_nor_its_buyer_looked_up(
    worker: Worker, module_database: BoundDatabase
) -> None:
    tenant = await a_company(module_database, EcfType.CONSUMER_INVOICE)
    document = await worker.accepted(draft_of(tenant, counterparty=None))
    assert (await worker.job(document), worker.network.asked(DIRECTORY)) == (None, 0)
    assert (await delivery_of(module_database, tenant, document)).state == "not_applicable"
    await set_row(module_database, tenant, document, delivery_state="not_started")  # as if enqueued
    assert await worker.delivered(tenant, document) == Done()
    found = await delivery_of(module_database, tenant, document)
    assert (found.state, found.reason, worker.network.asked(DIRECTORY)) == (
        "not_applicable",
        "not_deliverable",
        0,
    )


async def test_a_buyer_dgii_lists_no_receiver_for_is_not_applicable(
    worker: Worker, module_database: BoundDatabase
) -> None:
    worker.network.unlisted = True
    tenant = await a_company(module_database)
    document = await worker.accepted(draft_of(tenant))
    assert await worker.delivered(tenant, document) == Done()
    found = await delivery_of(module_database, tenant, document)
    assert (found.state, found.reason) == ("not_applicable", "no_directory_entry")
    assert (worker.network.asked(DIRECTORY), worker.network.asked(BUYER_RECEPTION)) == (1, 0)


async def test_a_buyer_requiring_authentication_while_it_is_off_is_retried_never_signing_a_seed(
    worker: Worker, module_database: BoundDatabase, test_signer: TestSigner
) -> None:
    """Decision 19: off by default, no buyer's seed signed; alerted at every attempt; failed after 7 days."""
    tenant = await a_company(module_database)
    document = await worker.accepted(draft_of(tenant))
    signed_before = seeds(test_signer)
    waits: list[timedelta] = []
    with capture_logs() as logs:
        for _ in range(5):
            before = await database_now(module_database)
            outcome = await worker.delivered(tenant, document)
            assert isinstance(outcome, RunAgain)
            waits.append(outcome.at - before)
    expected = [timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=30), timedelta(hours=2)]
    assert all(
        want <= wait < want + SLACK for wait, want in zip(waits, [*expected, timedelta(hours=6)], strict=True)
    )
    assert [entry["event"] for entry in logs] == ["do_ecf.delivery_needs_authentication"] * 5
    assert seeds(test_signer) == signed_before  # DGII's token was signed while advancing; no buyer's seed
    assert worker.network.asked(BUYER_SEED) == 0
    found = await delivery_of(module_database, tenant, document)
    assert (found.state, found.reason, found.attempts) == (
        "delivering",
        "do_ecf_buyer_requires_authentication",
        5,
    )
    assert [state for state, _ in await published_deliveries(module_database, tenant)][-2:] == [
        "not_started",
        "delivering",
    ]  # an attempt that leaves it delivering publishes nothing
    assert found.since is not None
    await set_row(module_database, tenant, document, delivery_since=found.since - timedelta(days=7))
    with capture_logs() as logs:
        assert await worker.delivered(tenant, document) == Done()
    failed = await delivery_of(module_database, tenant, document)
    assert (failed.state, failed.reason, failed.attempts) == (
        "failed",
        "do_ecf_buyer_requires_authentication",
        6,
    )
    assert [entry["event"] for entry in logs] == [
        "do_ecf.delivery_needs_authentication",
        "do_ecf.delivery_failed",
    ]
    assert await seeds_audited(module_database, tenant) == []


async def test_no_wait_reaches_past_the_seven_days(worker: Worker, module_database: BoundDatabase) -> None:
    """Decision 21: the run that records the failure always comes, before the job's deadline."""
    tenant = await a_company(module_database)
    document = await worker.accepted(draft_of(tenant))
    now = await database_now(module_database)
    since = now - timedelta(days=7) + timedelta(hours=1)
    await set_row(
        module_database,
        tenant,
        document,
        delivery_state="delivering",
        delivery_since=since,
        delivery_attempts=9,
    )
    outcome = await worker.delivered(tenant, document)
    assert isinstance(outcome, RunAgain)
    assert outcome.at == since + timedelta(days=7)


async def test_with_buyer_authentication_on_the_seed_is_signed_audited_and_the_buyer_masked(
    worker: Worker, module_database: BoundDatabase, test_signer: TestSigner
) -> None:
    """Decision 20: one audit row per seed, its detail the endpoint's host and the buyer's tax id masked."""
    tenant = await a_company(module_database, authenticates=True)
    document = await worker.accepted(draft_of(tenant))
    signed_before = seeds(test_signer)
    assert await worker.delivered(tenant, document) == Done()
    assert (await delivery_of(module_database, tenant, document)).state == "delivered"
    assert seeds(test_signer) == signed_before + 1
    assert await seeds_audited(module_database, tenant) == [
        {"endpoint_host": "ecf.fake.test", "buyer": "******193"}
    ]
    rows = await audit_rows(module_database, tenant)
    assert rows
    assert not any(BUYER_RNC in (value or "") for row in rows for value in row)


async def test_a_receipt_signed_by_another_taxpayer_fails_the_delivery_archived_apart_never_retried(
    worker: Worker, module_database: BoundDatabase, archive: StoreArchive, authority: Authority
) -> None:
    """Review Focus 4: the refused receipt kept as ``arecf-refused``; the job is done with it."""
    worker.fake.state.receiver_requires_auth = False
    worker.fake.state.receiver = authority.issue(
        organization("131880738", name="OTRO"), not_before=SIGNER_VALID_FROM
    )
    tenant = await a_company(module_database)
    document = await worker.accepted(draft_of(tenant))
    with capture_logs() as logs:
        assert await worker.delivered(tenant, document) == Done()
    found = await delivery_of(module_database, tenant, document)
    assert (found.state, found.reason) == ("failed", "do_ecf_counterparty_certificate_refused")
    assert found.receipt_key is not None
    assert found.receipt_sha256 is not None
    assert "/arecf-refused/" in found.receipt_key
    assert await archive.get(found.receipt_key, sha256=found.receipt_sha256)
    assert [entry["event"] for entry in logs] == ["do_ecf.delivery_failed"]
    assert await worker.delivered(tenant, document) == Done()  # never retried
    assert worker.network.asked(BUYER_RECEPTION) == 1


# -- What the plan's cases imply ----------------------------------------------------------------------------


async def test_a_buyer_that_already_has_the_document_counts_as_delivered_and_one_that_refuses_it_fails(
    worker: Worker, module_database: BoundDatabase
) -> None:
    """A duplicate (3): an earlier attempt reached the buyer, its answer lost; another reason is final."""
    worker.fake.state.receiver_requires_auth = False
    tenant = await a_company(module_database)
    document = await worker.accepted(draft_of(tenant))
    assert await worker.delivered(tenant, document) == Done()
    await set_row(
        module_database, tenant, document, delivery_state="delivering", delivery_since=text("now()")
    )
    assert await worker.delivered(tenant, document) == Done()
    found = await delivery_of(module_database, tenant, document)
    assert (found.state, worker.network.asked(BUYER_RECEPTION)) == ("delivered", 2)
    other = await worker.accepted(draft_of(tenant))
    worker.fake.state.receiver_rncs = frozenset({"101850043"})
    assert await worker.delivered(tenant, other) == Done()
    refused = await delivery_of(module_database, tenant, other)
    assert (refused.state, refused.reason) == ("failed", "not_received_not_this_receiver")
    assert refused.receipt_key is not None
    assert "/arecf/" in refused.receipt_key  # the buyer's signed word: trusted, kept


async def test_a_buyer_endpoint_that_may_not_be_reached_and_dgii_s_directory_out_of_reach_are_retried(
    worker: Worker, module_database: BoundDatabase
) -> None:
    """DGII's directory first (a failure is not cached), then an endpoint it lists that may not be reached."""
    tenant = await a_company(module_database)
    document = await worker.accepted(draft_of(tenant))
    worker.network.failing.add(DIRECTORY)
    assert isinstance(await worker.delivered(tenant, document), RunAgain)
    assert (await delivery_of(module_database, tenant, document)).reason == "do_ecf_dgii_unavailable"
    worker.network.failing.clear()
    worker.fake.state.directory[BUYER_RNC] = DirectoryEntry(
        name="COMPRADOR",
        rnc=BUYER_RNC,
        reception="http://buyer.example.com/fe",
        approval="http://buyer.example.com/fe",
        authentication=None,
    )
    assert isinstance(await worker.delivered(tenant, document), RunAgain)
    found = await delivery_of(module_database, tenant, document)
    assert (found.state, found.reason, found.attempts) == ("delivering", "do_ecf_buyer_endpoint_refused", 2)


async def test_in_certecf_there_is_no_directory_to_find_a_buyer_in(
    worker: Worker, module_database: BoundDatabase
) -> None:
    tenant = await a_company(module_database, environment=Environment.CERTECF)
    document = await worker.accepted(replace(draft_of(tenant), environment="certecf"))
    assert await worker.delivered(tenant, document) == Done()
    found = await delivery_of(module_database, tenant, document)
    assert (found.state, found.reason, worker.network.asked(DIRECTORY)) == (
        "not_applicable",
        "no_directory_in_environment",
        0,
    )


async def test_a_receipt_the_archive_cannot_keep_fails_the_run_once_its_seeds_are_audited(
    started: Started, module_database: BoundDatabase, archive: StoreArchive
) -> None:
    tenant = await a_company(module_database, authenticates=True)
    async with started.worker(**{ARCHIVE_PORT.name: LosingReceipts(archive)}) as worker:
        document = await worker.accepted(draft_of(tenant))
        with pytest.raises(ArchiveUnavailableError):
            await worker.delivered(tenant, document)
    assert len(await seeds_audited(module_database, tenant)) == 1
    assert (await delivery_of(module_database, tenant, document)).state == "not_started"


async def test_a_delivery_settled_meanwhile_is_left_as_it_is(
    worker: Worker, module_database: BoundDatabase
) -> None:
    worker.fake.state.receiver_requires_auth = False
    tenant = await a_company(module_database)
    document = await worker.accepted(draft_of(tenant))

    async def settled() -> None:
        await set_row(module_database, tenant, document, delivery_state="not_applicable")

    worker.network.meanwhile = settled
    assert await worker.delivered(tenant, document) == Done()
    assert (await delivery_of(module_database, tenant, document)).state == "not_applicable"


async def test_a_job_naming_no_document_of_the_company_is_a_defect(
    worker: Worker, module_database: BoundDatabase
) -> None:
    tenant = await a_company(module_database)
    with pytest.raises(DocumentMissingError):
        await worker.delivered(tenant, new_id())


async def test_documents_accepted_before_this_revision_get_their_delivery_job(
    worker: Worker, module_database: BoundDatabase, admin: psycopg.AsyncConnection
) -> None:
    """Task 19's binding: every reception document accepted with delivery ``not_started`` is enqueued."""
    tenant = await a_company(module_database, EcfType.CREDIT_INVOICE, EcfType.CONSUMER_INVOICE)
    waiting = await worker.accepted(draft_of(tenant))
    done = await worker.accepted(draft_of(tenant))
    summary = await worker.accepted(draft_of(tenant, counterparty=None))
    jobs = CHASSIS.jobs
    async with module_database.unit(None) as unit:  # as before Task 21: no delivery job
        await unit.connection.execute(jobs.delete().where(jobs.c.kind == DELIVER))
    await set_row(module_database, tenant, done, delivery_state="delivered")
    await swept(admin)
    assert (await worker.job(waiting), await worker.job(done), await worker.job(summary)) == (
        "ready",
        None,
        None,
    )
    await swept(admin)  # again: the job is left as it is
    assert await worker.job(waiting) == "ready"
    forced = await admin.execute(
        "SELECT relforcerowsecurity FROM pg_class WHERE oid = 'do_ecf.documents'::regclass"
    )
    assert await forced.fetchone() == (True,)


async def swept(admin: psycopg.AsyncConnection) -> None:
    """Revision 0004's sweep, run as the schema's owner (whom row-level security binds too) runs it."""
    async with admin.transaction():
        await admin.execute("SET LOCAL ROLE do_ecf_owner")
        for statement in r0004_do_ecf.SWEEP:
            await admin.execute(statement.encode())


async def test_the_job_runs_for_a_company_in_a_mode_only(worker: Worker) -> None:
    with pytest.raises(InvalidValueError, match=r"do_ecf\.deliver\.scope"):
        await worker.deliver(job_of(DELIVER, a_tenant_without_mode(), new_id()))


def a_tenant_without_mode() -> Tenant:
    return Tenant(OrganizationId(new_id()), CompanyId(new_id()), None)
