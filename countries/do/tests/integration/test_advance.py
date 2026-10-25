"""The ``do_ecf.advance`` job against the fake DGII (spec §3.6, §8.6.3, §8.6.4; Decisions 7-10, 37).

One outbound call per run; each step reads in one unit, calls out, records in another that locks the row and
checks what it expects. **Never resend** (Review Focus 1): a document is submitted at most once, ever. The
submission is marked (``submission_started_at``, committed) before the one call that sends it; a run that
finds the mark and no answer never submits: the outcome is unknown, settled by the e-NCF's track ids (or
``consultarfce`` for an RFCE), and left to a person after 24 hours. Each test names the rule it pins; the
fake keeps every document it received (``fake.received``), the network every request (``Network``).
"""

import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from typing import Final, Protocol, Unpack, final, override
lazy from collections.abc import AsyncGenerator, Mapping, Sequence
lazy from pathlib import Path
lazy from uuid import UUID

import httpx
import pytest
from pydantic import TypeAdapter
from sqlalchemy import func, select, text, update
from structlog.testing import capture_logs

from ledger_country_do._manifest import MANIFEST
from ledger_country_do.channels.ecf_v1.adapters.channel import DoEcfChannel
from ledger_country_do.channels.ecf_v1.adapters.jobs import ADVANCE
from ledger_country_do.channels.ecf_v1.adapters.postgres.tables import (
    DocumentRecord,
    ReleasedNumberRecord,
    SubmissionRecord,
)
from ledger_country_do.channels.ecf_v1.adapters.postgres.unit import PgUnits, bind
from ledger_country_do.channels.ecf_v1.application.ranges import (
    Enrollments,
    NumberingRange,
    RangeRequest,
    Ranging,
)
from ledger_country_do.channels.ecf_v1.assembly import assemble
from ledger_country_do.channels.ecf_v1.domain.encf import EcfType
from ledger_country_do.channels.ecf_v1.domain.environments import Environment
from ledger_country_do.channels.ecf_v1.domain.numbering import Authorization, RangeState, RangeStatus
from ledger_country_do.channels.ecf_v1.domain.polling import (
    RECEIPT_TOLERANCE,
    SETTLE_DELAY,
    TRACK_ID_WINDOW,
    UNAVAILABLE_WAIT,
)
from ledger_country_do.channels.ecf_v1.settings import DoEcfSettings
from ledger_country_do_testing.fake_dgii.state import Scripted, Status
from ledger_country_do_testing.fiscal import fiscal_draft
from ledger_country_do_testing.transports import FAKE_HOSTS, outbound_over
from ledger_fiscal_kit.channels import ResumeReason
from ledger_fiscal_kit.drafts import DocumentRef
from ledger_fiscal_kit.ports import SIGNER_PORT
from ledger_kernel.errors import InvalidValueError
from ledger_kernel.ids import CompanyId, OrganizationId, new_id
from ledger_kernel.tenancy import Mode, Tenant
from ledger_platform.blobs.archive import ArchiveDigestMismatchError
from ledger_platform.data.codec import JsonValue
from ledger_platform.data.tables import chassis_tables
from ledger_platform.jobs.outcomes import Done, Job, Park, RunAgain
from ledger_platform.jobs.runner import JobRunner
from ledger_platform.runtime.calls import SystemCall
from ledger_testing.pki import pem
from ledger_testing.signer import TestSigner
lazy from ledger_country_do_testing.fake_dgii.app import FakeDgii
lazy from ledger_country_do_testing.fake_dgii.state import Received
lazy from ledger_country_do_testing.fiscal import DraftOptions
lazy from ledger_country_do_testing.local_signer import KeyPairSigner
lazy from ledger_fiscal_kit.drafts import FiscalDraft
lazy from ledger_fiscal_kit.ports import SignerPort
lazy from ledger_platform.config.manifest import AppRole, JobDecl
lazy from ledger_platform.config.settings import Environment as AppEnvironment
lazy from ledger_platform.data.bound import BoundDatabase
lazy from ledger_platform.http.outbound import OutboundHttp
lazy from ledger_platform.jobs.outcomes import JobHandler, JobOutcome
lazy from ledger_platform.runtime.contexts import ChannelContext
lazy from ledger_testing.clock import ManualClock
lazy from ledger_testing.pki import Authority

SETTINGS: Final = DoEcfSettings(
    receiver_public_url="https://receiver.ledger.test", ecf_host=FAKE_HOSTS.ecf, fc_host=FAKE_HOSTS.fc
)
BUDGET: Final = timedelta(seconds=1.5)
EXPIRES: Final = date(2028, 12, 31)
CHASSIS: Final = chassis_tables("do_ecf")
PAYLOAD: Final = TypeAdapter(dict[str, JsonValue])
JOB_ROW: Final = TypeAdapter(tuple[str, str | None, str | None, datetime, datetime])
RECEPTION: Final = "recepcion/api/facturaselectronicas"
SUMMARY: Final = "recepcionfc/api/recepcion/ecf"
TRACK_IDS: Final = "consultatrackids/api/trackids/consulta"
RESULT: Final = "consultaresultado/api/consultas/estado"
RFCE_QUERY: Final = "consultarfce/api/consultas/consulta"
CREDIT: Final = EcfType.CREDIT_INVOICE
CONSUMER: Final = EcfType.CONSUMER_INVOICE
SLACK: Final = timedelta(seconds=1)
"""How much later than the step's own wait a ``RunAgain`` may name: the time its units took."""
(ADVANCE_DECL,) = (job for entry in MANIFEST.channels for job in entry.jobs if job.kind == ADVANCE)
REFUSED_CONNECTION: Final = "the connection was refused"
RESET_CONNECTION: Final = "the connection was reset"
REFUSAL: Final = b'{"trackId": null, "error": "400", "mensaje": "El XML no es v\\u00e1lido"}'


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


class CrashedError(Exception):
    """The process dying while a request is on its way (a test's stand-in): a defect, not a pack error."""


@final
class Network(httpx.AsyncBaseTransport):
    """The way to the fake DGII as a test breaks it; each rule applies to the requests whose path ends so.

    Before delivery: ``stalled`` (the next such request waits for the event first), then, never delivered:
    ``throttled`` (the next ``n`` answered 429), ``unreachable`` (the next ``n`` never connect), ``refused``
    (DGII's 400 at the door), ``hidden`` (the track-id lists answered empty). Delivered, then: ``held`` (the
    next such answer waits for the event), then ``broken`` (the connection breaks before the answer),
    ``failing`` (a 503), ``crashing`` (a defect), ``renamed`` (the answer's text ``old`` written ``new``).
    ``delivered`` is set once a held or stalled request waits.
    """

    __slots__ = (
        "_inner",
        "broken",
        "crashing",
        "delivered",
        "failing",
        "fake",
        "held",
        "hidden",
        "refused",
        "renamed",
        "requests",
        "stalled",
        "throttled",
        "unreachable",
    )

    def __init__(self, fake: FakeDgii) -> None:
        self.fake: Final = fake
        self._inner = httpx.ASGITransport(app=fake.app)
        self.requests: list[tuple[str, str]] = []
        self.stalled: dict[str, asyncio.Event] = {}
        self.throttled: dict[str, int] = {}
        self.unreachable: dict[str, int] = {}
        self.refused: set[str] = set()
        self.hidden = False
        self.held: dict[str, asyncio.Event] = {}
        self.broken: set[str] = set()
        self.failing: set[str] = set()
        self.crashing: set[str] = set()
        self.renamed: dict[str, tuple[str, str]] = {}
        self.delivered = asyncio.Event()

    @staticmethod
    def _take(rules: dict[str, int], path: str) -> bool:
        for part, left in rules.items():
            if path.endswith(part) and left > 0:
                rules[part] = left - 1
                return True
        return False

    async def _wait(self, rules: dict[str, asyncio.Event], path: str) -> None:
        """The first request matching one of ``rules`` waits for its event (the rule is spent)."""
        for part, release in tuple(rules.items()):
            if path.endswith(part):
                del rules[part]
                self.delivered.set()
                await release.wait()

    def posts(self, part: str) -> int:
        """How many ``POST`` requests whose path ends with ``part`` left for DGII (delivered or not)."""
        return sum(1 for method, path in self.requests if method == "POST" and path.endswith(part))

    def asked(self) -> list[str]:
        """Every request but authentication's, its path after the environment, in order."""
        return [path.split("/", 2)[2] for _, path in self.requests if "autenticacion" not in path]

    def _undelivered(self, request: httpx.Request) -> httpx.Response | None:
        path = request.url.path
        if self._take(self.throttled, path):
            return httpx.Response(429, stream=httpx.ByteStream(b""), request=request)
        if self._take(self.unreachable, path):
            raise httpx.ConnectError(REFUSED_CONNECTION, request=request)
        if any(path.endswith(part) for part in self.refused):
            return httpx.Response(400, stream=httpx.ByteStream(REFUSAL), request=request)
        if self.hidden and path.endswith(TRACK_IDS):
            return httpx.Response(200, stream=httpx.ByteStream(b"[]"), request=request)
        return None

    async def _answered(self, request: httpx.Request, response: httpx.Response) -> httpx.Response:
        path = request.url.path
        if any(path.endswith(part) for part in self.broken):
            raise httpx.ReadError(RESET_CONNECTION, request=request)
        if any(path.endswith(part) for part in self.failing):
            return httpx.Response(503, stream=httpx.ByteStream(b""), request=request)
        if any(path.endswith(part) for part in self.crashing):
            raise CrashedError
        body = await response.aread()
        for part, (old, new) in self.renamed.items():
            if path.endswith(part):
                body = body.replace(old.encode(), new.encode())
        headers = [
            (name, value) for name, value in response.headers.items() if name.lower() != "content-length"
        ]
        return httpx.Response(
            response.status_code, headers=headers, stream=httpx.ByteStream(body), request=request
        )

    @override
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.requests.append((request.method, path))
        await self._wait(self.stalled, path)
        undelivered = self._undelivered(request)
        if undelivered is not None:
            return undelivered
        response = await self._inner.handle_async_request(request)
        await response.aread()
        await self._wait(self.held, path)
        return await self._answered(request, response)


@final
@dataclass(slots=True)
class Worker:
    """The worker's channel, its ``do_ecf.advance`` handler, and how a test drives them."""

    channel: DoEcfChannel
    handler: JobHandler
    database: BoundDatabase
    network: Network
    fake: FakeDgii

    async def issued(self, draft: FiscalDraft, *, sign: bool = True) -> UUID:
        """``draft``'s document (signed within the budget unless told not to), its id."""
        await self.channel.issue(call_of(draft), draft, sign_within=BUDGET if sign else None)
        async with self.database.unit(tenant_of(draft)) as unit:
            found = await bind(unit).documents.of_invoice(draft.document)
        assert found is not None
        return found.id

    async def run(self, tenant: Tenant, document: UUID) -> JobOutcome:
        """One run of the job, as the runner calls it."""
        return await self.handler(job_of(tenant, document))

    def runner(self, **decl: object) -> JobRunner:
        """A runner of the kind, its declaration changed as ``decl`` says (a timeout, a lease)."""
        changed: JobDecl = ADVANCE_DECL.model_copy(update=decl)
        return JobRunner(database=self.database, kinds={ADVANCE: (changed, self.handler)})

    async def drain(self, document: UUID, runner: JobRunner | None = None, *, limit: int = 30) -> list[str]:
        """Run the document's job until it stops being ready, each wait cut short; what each run left.

        ``again:<seconds>`` (``RunAgain``, its wait), ``retry:<code>`` (a failure, retried),
        ``parked:<reason>``, ``done``, ``dead:<code>``.
        """
        running = runner or self.runner()
        trace: list[str] = []
        for _ in range(limit):
            await self.due_now(document)
            if not await running.run_due():
                return trace
            status, reason, code, waited = await self.job(document)
            if status == "ready":
                trace.append(f"retry:{code}" if code else f"again:{round(waited.total_seconds())}")
                continue
            trace.append({"parked": f"parked:{reason}", "done": "done"}.get(status, f"{status}:{code}"))
            return trace
        return trace

    async def due_now(self, document: UUID) -> None:
        """The document's job, if ready, due now (its wait cut short), its last failure forgotten."""
        jobs = CHASSIS.jobs
        async with self.database.unit(None) as unit:
            await unit.connection.execute(
                update(jobs)
                .where(jobs.c.kind == ADVANCE, jobs.c.key == str(document), jobs.c.status == "ready")
                .values(run_at=text("now()"), last_error_code=None)
            )

    async def job(self, document: UUID) -> tuple[str, str | None, str | None, timedelta]:
        """The job's status, why it is parked, its last error, and how long after its update it is due."""
        jobs = CHASSIS.jobs
        async with self.database.unit(None) as unit:
            row = (
                await unit.connection.execute(
                    select(
                        jobs.c.status,
                        jobs.c.parked_reason,
                        jobs.c.last_error_code,
                        jobs.c.run_at,
                        jobs.c.updated_at,
                    ).where(jobs.c.kind == ADVANCE, jobs.c.key == str(document))
                )
            ).one()
        status, reason, code, run_at, updated_at = JOB_ROW.validate_python(tuple(row))
        return status, reason, code, run_at - updated_at

    async def set_job(self, document: UUID, **values: object) -> None:
        jobs = CHASSIS.jobs
        async with self.database.unit(None) as unit:
            await unit.connection.execute(
                update(jobs).where(jobs.c.kind == ADVANCE, jobs.c.key == str(document)).values(**values)
            )

    def receptions(self, encf: str) -> list[Received]:
        """What the fake received of ``encf`` (an e-CF or an RFCE)."""
        return [each for each in self.fake.received if each.encf == encf]


@pytest.fixture
def test_signer(signer: KeyPairSigner) -> TestSigner:
    """Signs documents and DGII's seeds with the session's key: the fake trusts it."""
    return TestSigner(sign=signer.sign, sign_seed=signer.sign)


@pytest.fixture
def network(fake: FakeDgii) -> Network:
    return Network(fake)


@pytest.fixture
def roots(tmp_path: Path, authority: Authority) -> Path:
    """The worker's trusted roots (``LEDGER_DO_ECF_TRUSTED_ROOTS``): the session's authority."""
    found = tmp_path / "roots.pem"
    found.write_bytes(pem(authority.certificate))
    return found


@final
@dataclass(slots=True)
class Started:
    """Workers started by a test, each over its own signer (the ports' unless given)."""

    contexts: Contexts
    ports: dict[str, object]
    database: BoundDatabase
    network: Network
    roots: Path

    @asynccontextmanager
    async def worker(self, signer: SignerPort | None = None) -> AsyncGenerator[Worker]:
        bound = self.ports if signer is None else self.ports | {SIGNER_PORT.name: signer}
        settings = SETTINGS.model_copy(update={"trusted_roots": self.roots})
        parts = assemble(
            self.contexts(settings=settings, bound=bound, role="worker", http=outbound_over(self.network))
        )
        channel = parts.channel
        assert isinstance(channel, DoEcfChannel)
        (spec,) = parts.jobs
        async with AsyncExitStack() as stack:
            for resource in parts.resources:
                await stack.enter_async_context(resource)
            yield Worker(channel, spec.handler, self.database, self.network, self.network.fake)


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


def a_tenant() -> Tenant:
    return Tenant(OrganizationId(new_id()), CompanyId(new_id()), Mode.TEST)


def tenant_of(draft: FiscalDraft) -> Tenant:
    return Tenant(draft.organization, draft.company, draft.mode)


def draft_of(tenant: Tenant, **options: Unpack[DraftOptions]) -> FiscalDraft:
    return replace(
        fiscal_draft(**options),
        organization=tenant.organization,
        company=tenant.company,
        document=DocumentRef(new_id()),
    )


def consumer(tenant: Tenant) -> FiscalDraft:
    """A consumer invoice under RD$250,000: summarized (RFCE)."""
    return draft_of(tenant, counterparty=None)


def call_of(draft: FiscalDraft) -> SystemCall:
    return SystemCall(reason="fiscal:issue", scope=tenant_of(draft))


def job_of(tenant: Tenant, document: UUID) -> Job:
    return Job(
        kind=ADVANCE,
        key=str(document),
        scope=tenant,
        payload={},
        attempts=1,
        max_attempts=50,
        deadline_at=None,
        lease_token=new_id(),
        traceparent=None,
    )


async def with_range(
    module_database: BoundDatabase,
    tenant: Tenant,
    ecf_type: EcfType = CREDIT,
    *,
    environment: Environment = Environment.TESTECF,
) -> None:
    async with module_database.unit(tenant) as unit:
        ranges = bind(unit).ranges
        await ranges.series(environment, ecf_type)
        authorized = RangeState(
            id=new_id(),
            ecf_type=ecf_type,
            first=1,
            last=100,
            next=1,
            expires_on=EXPIRES,
            status=RangeStatus.ACTIVE,
        )
        await ranges.add(
            NumberingRange(environment=environment, range=authorized, created_at=unit.now, version=1)
        )


@final
@dataclass(frozen=True, slots=True)
class Row:
    """What a test reads of a document's row."""

    state: str
    version: int
    ecf_type: str
    sequence: int
    mark: datetime | None
    track_id: str | None
    since: datetime | None
    polls: int
    code: int | None
    sequence_used: bool | None
    fate: str | None
    messages: list[dict[str, str]]
    delivery: str
    signed_at: datetime | None

    @property
    def encf(self) -> str:
        return f"E{self.ecf_type}{self.sequence:010d}"


ROW: Final = TypeAdapter(Row)


async def row_of(module_database: BoundDatabase, tenant: Tenant, document: UUID) -> Row:
    async with module_database.unit(tenant) as unit:
        found = (
            await unit.connection.execute(
                select(
                    DocumentRecord.state,
                    DocumentRecord.version,
                    DocumentRecord.ecf_type,
                    DocumentRecord.sequence,
                    DocumentRecord.submission_started_at.label("mark"),
                    DocumentRecord.track_id,
                    DocumentRecord.track_id_since.label("since"),
                    DocumentRecord.polls,
                    DocumentRecord.outcome_code.label("code"),
                    DocumentRecord.sequence_used,
                    DocumentRecord.number_fate.label("fate"),
                    DocumentRecord.messages,
                    DocumentRecord.delivery_state.label("delivery"),
                    DocumentRecord.signed_at,
                ).where(DocumentRecord.id == document)
            )
        ).mappings()
    return ROW.validate_python(dict(found.one()))


async def set_row(module_database: BoundDatabase, tenant: Tenant, document: UUID, **values: object) -> None:
    async with module_database.unit(tenant) as unit:
        await unit.connection.execute(
            update(DocumentRecord).where(DocumentRecord.id == document).values(**values)
        )


async def updates_of(module_database: BoundDatabase, tenant: Tenant) -> list[dict[str, JsonValue]]:
    """The ``update`` of every ``fiscal_channel.document.updated`` ``tenant``'s units published, in order."""
    outbox = CHASSIS.outbox
    async with module_database.unit(None) as unit:
        rows = await unit.connection.execute(
            select(outbox.c.payload)
            .where(
                outbox.c.type == "fiscal_channel.document.updated",
                outbox.c.company_id == tenant.company,
            )
            .order_by(outbox.c.aggregate_version)
        )
        found: list[dict[str, JsonValue]] = []
        for (payload,) in rows.all():
            update_ = PAYLOAD.validate_python(payload)["update"]
            assert isinstance(update_, dict)
            found.append(update_)
        return found


def states_of(updates: list[dict[str, JsonValue]]) -> list[JsonValue]:
    return [each["state"] for each in updates]


async def submissions_of(
    module_database: BoundDatabase, tenant: Tenant
) -> list[tuple[UUID, str, int, datetime]]:
    """``(document, track id, sequence, recorded at)`` of every track id ``tenant``'s documents hold."""
    async with module_database.unit(tenant) as unit:
        rows = await unit.connection.execute(
            select(
                SubmissionRecord.document_id,
                SubmissionRecord.track_id,
                SubmissionRecord.sequence,
                SubmissionRecord.submitted_at,
            )
        )
        return [(document, track_id, sequence, at) for document, track_id, sequence, at in rows.all()]


async def database_now(module_database: BoundDatabase) -> datetime:
    async with module_database.unit(None) as unit:
        return unit.now


def again_after(outcome: JobOutcome, before: datetime, wait: timedelta) -> None:
    """``outcome`` runs the job again ``wait`` after ``before`` (the database's time just before the run)."""
    assert isinstance(outcome, RunAgain), outcome
    assert wait <= outcome.at - before <= wait + SLACK, outcome.at - before


def details_of(published: Mapping[str, JsonValue]) -> Mapping[str, JsonValue]:
    details = published["details"]
    assert isinstance(details, dict)
    found = details["do"]
    assert isinstance(found, dict)
    return found


def events_of(logged: Sequence[Mapping[str, object]]) -> list[tuple[object, object, object]]:
    return [(line["event"], line["log_level"], line.get("reason")) for line in logged]


# ── the way through: sign, submit once, poll, decide ─────────────────────────────────


async def test_a_credit_invoice_is_signed_submitted_once_polled_and_accepted(
    worker: Worker, module_database: BoundDatabase
) -> None:
    """Each run makes one call to DGII at most (Decision 10); one ``POST`` to reception, ever."""
    tenant = a_tenant()
    await with_range(module_database, tenant)
    document = await worker.issued(draft_of(tenant), sign=False)
    assert await worker.drain(document) == ["again:0", "again:1", "done"]
    row = await row_of(module_database, tenant, document)
    assert (row.state, row.fate, row.code, row.sequence_used, row.polls) == ("accepted", "used", 1, True, 1)
    assert row.track_id == worker.fake.received[0].track_id
    assert (worker.network.posts(RECEPTION), len(worker.receptions(row.encf))) == (1, 1)
    assert worker.network.asked() == [RECEPTION, RESULT]
    ((_, track_id, sequence, recorded_at),) = await submissions_of(module_database, tenant)
    assert (track_id, sequence) == (row.track_id, 1)
    assert row.mark is not None
    assert recorded_at >= row.mark  # recorded once DGII answered, after the mark
    updates = await updates_of(module_database, tenant)
    assert [(each["state"], each["status"], each["version"]) for each in updates] == [
        ("numbered", "pending", 1),
        ("signed", "pending", 2),
        ("submitted", "pending", 3),
        ("accepted", "accepted", 4),
    ]
    assert [details_of(each)["track_id"] for each in updates] == [None, None, row.track_id, row.track_id]
    assert details_of(updates[-1])["delivery"] == "not_started"  # the delivery job's (Task 21)


async def test_en_proceso_is_polled_on_the_schedule_publishing_once_while_it_lasts(
    worker: Worker, module_database: BoundDatabase
) -> None:
    tenant = a_tenant()
    await with_range(module_database, tenant)
    worker.fake.script.default = Scripted(processing=3)
    document = await worker.issued(draft_of(tenant))
    assert await worker.drain(document) == ["again:1", "again:2", "again:5", "again:10", "done"]
    row = await row_of(module_database, tenant, document)
    assert (row.state, row.polls, worker.network.posts(RECEPTION)) == ("accepted", 4, 1)
    updates = await updates_of(module_database, tenant)
    assert states_of(updates) == ["numbered", "signed", "submitted", "in_process", "accepted"]
    assert [each["version"] for each in updates] == [1, 2, 3, 4, 5]


async def test_a_conditional_acceptance_is_valid_and_keeps_dgii_s_messages(
    worker: Worker, module_database: BoundDatabase
) -> None:
    tenant = a_tenant()
    await with_range(module_database, tenant)
    worker.fake.script.default = Scripted(
        status=Status.ACCEPTED_CONDITIONALLY, messages=(("613", "Fuera de tolerancia"),)
    )
    document = await worker.issued(draft_of(tenant))
    assert await worker.drain(document) == ["again:1", "done"]
    row = await row_of(module_database, tenant, document)
    assert (row.state, row.fate, row.code) == ("accepted_conditionally", "used", 4)
    assert row.messages == [{"code": "613", "text": "Fuera de tolerancia"}]
    *_, last = await updates_of(module_database, tenant)
    assert (last["status"], last["severity"]) == ("accepted_with_observations", "warning")
    assert last["messages"] == [{"code": "613", "message": "Fuera de tolerancia", "lang": "es"}]


@pytest.mark.parametrize(("used", "fate"), [(True, "burned"), (False, "released")])
async def test_a_rejection_burns_or_releases_the_number_as_secuencia_utilizada_says(
    worker: Worker, module_database: BoundDatabase, *, used: bool, fate: str
) -> None:
    tenant = a_tenant()
    await with_range(module_database, tenant)
    worker.fake.script.default = Scripted(status=Status.REJECTED, sequence_used=used, messages=(("2", "No"),))
    document = await worker.issued(draft_of(tenant))
    assert await worker.drain(document) == ["again:1", "done"]
    row = await row_of(module_database, tenant, document)
    assert (row.state, row.fate, row.sequence_used, row.delivery) == (
        "rejected",
        fate,
        used,
        "not_applicable",
    )
    async with module_database.unit(tenant) as unit:
        released = (
            await unit.connection.execute(
                select(ReleasedNumberRecord.sequence, ReleasedNumberRecord.document_id)
            )
        ).all()
    assert released == ([] if used else [(1, document)])
    *_, last = await updates_of(module_database, tenant)
    assert (last["state"], last["status"], last["severity"]) == ("rejected", "rejected", "error")


async def test_a_released_number_is_given_again_only_receipt_tolerance_after_its_release(
    worker: Worker, module_database: BoundDatabase, clock: ManualClock
) -> None:
    """Decision 9's first safeguard: one minute after the release the next document gets another number."""
    tenant = a_tenant()
    await with_range(module_database, tenant)
    worker.fake.script.default = Scripted(status=Status.REJECTED, sequence_used=False)
    await worker.drain(await worker.issued(draft_of(tenant)))
    worker.fake.script.default = Scripted()
    clock.advance(timedelta(minutes=1))
    soon = await worker.issued(draft_of(tenant), sign=False)
    assert (await row_of(module_database, tenant, soon)).encf == "E310000000002"
    clock.advance(RECEIPT_TOLERANCE)
    later = await worker.issued(draft_of(tenant), sign=False)
    assert (await row_of(module_database, tenant, later)).encf == "E310000000001"
    async with module_database.unit(tenant) as unit:
        held = await unit.connection.execute(
            select(DocumentRecord.state, DocumentRecord.number_fate).where(DocumentRecord.sequence == 1)
        )
        assert sorted(held.all(), key=str) == [("numbered", None), ("rejected", "released")]


async def test_a_refusal_at_the_door_rejects_and_burns_the_number(
    worker: Worker, module_database: BoundDatabase
) -> None:
    """A 400 at reception: DGII may have recorded the number, which is never used again (F13)."""
    tenant = a_tenant()
    await with_range(module_database, tenant)
    document = await worker.issued(draft_of(tenant))
    worker.network.refused.add(RECEPTION)
    assert await worker.drain(document) == ["done"]
    row = await row_of(module_database, tenant, document)
    assert (row.state, row.fate, row.code, row.sequence_used) == ("rejected", "burned", 2, None)
    assert row.mark is not None
    assert row.messages == [{"code": "400", "text": "El XML no es válido"}]
    assert worker.network.posts(RECEPTION) == 1


async def test_a_decided_job_has_nothing_left_and_a_blocked_one_waits_for_a_person(
    worker: Worker, module_database: BoundDatabase
) -> None:
    tenant = a_tenant()
    await with_range(module_database, tenant)
    document = await worker.issued(draft_of(tenant))
    await worker.drain(document)
    assert await worker.run(tenant, document) == Done()
    blocked = await worker.issued(draft_of(tenant), sign=False)
    await set_row(module_database, tenant, blocked, state="blocked")
    assert await worker.run(tenant, blocked) == Park("blocked")
    voided = await worker.issued(draft_of(tenant), sign=False)
    await set_row(module_database, tenant, voided, state="voided")
    assert await worker.run(tenant, voided) == Done()
    assert worker.network.posts(RECEPTION) == 1


# ── never resend: the mark (Decision 8) ──────────────────────────────────────────────


async def test_the_mark_is_committed_before_the_one_call_that_sends(
    worker: Worker, module_database: BoundDatabase
) -> None:
    """While the request is on its way, another connection already sees the mark; the state is signed."""
    tenant = a_tenant()
    await with_range(module_database, tenant)
    document = await worker.issued(draft_of(tenant))
    release = asyncio.Event()
    worker.network.held[RECEPTION] = release
    running = asyncio.create_task(worker.run(tenant, document))
    await worker.network.delivered.wait()
    row = await row_of(module_database, tenant, document)
    assert (row.state, row.mark is not None) == ("signed", True)
    release.set()
    assert isinstance(await running, RunAgain)
    assert (await row_of(module_database, tenant, document)).state == "submitted"


async def test_a_run_that_finds_the_mark_never_submits_and_asks_for_track_ids_once_settled(
    worker: Worker, module_database: BoundDatabase
) -> None:
    """The process died between the call and the record: the outcome is unknown, never sent again."""
    tenant = a_tenant()
    await with_range(module_database, tenant)
    document = await worker.issued(draft_of(tenant))
    worker.network.crashing.add(RECEPTION)
    with pytest.raises(CrashedError):
        await worker.run(tenant, document)
    worker.network.crashing.clear()
    marked = await row_of(module_database, tenant, document)
    assert (marked.state, marked.mark is not None) == ("signed", True)
    before = await database_now(module_database)
    with capture_logs() as logged:
        again_after(await worker.run(tenant, document), before, SETTLE_DELAY)
    row = await row_of(module_database, tenant, document)
    assert (row.state, row.mark, row.since is not None, row.version) == (
        "outcome_unknown",
        marked.mark,
        True,
        3,
    )
    assert events_of(logged) == [("do_ecf.submission_unknown", "warning", "marked")]
    assert worker.network.posts(RECEPTION) == 1
    assert await worker.drain(document) == ["again:2", "done"]  # adopted, then polled
    assert worker.network.asked() == [RECEPTION, TRACK_IDS, RESULT]
    assert (await row_of(module_database, tenant, document)).state == "accepted"
    assert len(worker.receptions(row.encf)) == 1


async def test_a_defect_escaping_the_submission_is_retried_by_the_runner_and_never_resends(
    worker: Worker, module_database: BoundDatabase
) -> None:
    """Carry-over 5: an exception that is not a pack error means the outcome is unknown."""
    tenant = a_tenant()
    await with_range(module_database, tenant)
    document = await worker.issued(draft_of(tenant))
    worker.network.crashing.add(RECEPTION)
    trace = await worker.drain(document, limit=1)
    worker.network.crashing.clear()
    assert trace == ["retry:internal"]
    assert await worker.drain(document) == ["again:30", "again:2", "done"]
    assert worker.network.posts(RECEPTION) == 1


async def test_a_connection_broken_after_sending_is_settled_by_its_track_id(
    worker: Worker, module_database: BoundDatabase
) -> None:
    tenant = a_tenant()
    await with_range(module_database, tenant)
    document = await worker.issued(draft_of(tenant))
    worker.network.broken.add(RECEPTION)
    before = await database_now(module_database)
    with capture_logs() as logged:
        again_after(await worker.run(tenant, document), before, SETTLE_DELAY)
    assert events_of(logged) == [("do_ecf.submission_unknown", "warning", "broken")]
    row = await row_of(module_database, tenant, document)
    assert (row.state, row.track_id, row.polls) == ("outcome_unknown", None, 0)
    worker.network.broken.clear()
    assert await worker.drain(document) == ["again:2", "done"]
    row = await row_of(module_database, tenant, document)
    assert (row.state, row.track_id) == ("accepted", worker.fake.received[0].track_id)
    assert worker.network.asked() == [RECEPTION, TRACK_IDS, RESULT]
    assert states_of(await updates_of(module_database, tenant)) == [
        "numbered",
        "signed",
        "outcome_unknown",
        "submitted",
        "accepted",
    ]


async def test_a_server_error_after_sending_leaves_the_outcome_unknown(
    worker: Worker, module_database: BoundDatabase
) -> None:
    tenant = a_tenant()
    await with_range(module_database, tenant)
    document = await worker.issued(draft_of(tenant))
    worker.network.failing.add(RECEPTION)
    assert await worker.drain(document, limit=1) == ["again:30"]
    worker.network.failing.clear()
    assert (await row_of(module_database, tenant, document)).state == "outcome_unknown"
    assert await worker.drain(document) == ["again:2", "done"]
    assert worker.network.posts(RECEPTION) == 1


async def test_a_run_cut_short_by_its_timeout_mid_submission_never_sends_again(
    worker: Worker, module_database: BoundDatabase
) -> None:
    """The kind's ``timeout`` cancels the handler while DGII holds the request: the next run sees the mark."""
    tenant = a_tenant()
    await with_range(module_database, tenant)
    document = await worker.issued(draft_of(tenant))
    worker.network.held[RECEPTION] = asyncio.Event()  # never answered
    impatient = worker.runner(timeout=timedelta(seconds=0.5))
    assert await worker.drain(document, impatient, limit=1) == ["retry:job_timed_out"]
    assert await worker.drain(document) == ["again:30", "again:2", "done"]
    assert worker.network.posts(RECEPTION) == 1
    assert worker.network.asked() == [RECEPTION, TRACK_IDS, RESULT]


async def test_a_run_whose_lease_was_taken_mid_submission_records_nothing_and_nothing_is_sent_twice(
    worker: Worker, module_database: BoundDatabase
) -> None:
    """The lease's renewal finds another token: the handler is stopped; the next run finds the mark."""
    tenant = a_tenant()
    await with_range(module_database, tenant)
    document = await worker.issued(draft_of(tenant))
    worker.network.held[RECEPTION] = asyncio.Event()
    hurried = worker.runner(lease=timedelta(seconds=0.6))
    await worker.due_now(document)
    running = asyncio.create_task(hurried.run_due())
    await worker.network.delivered.wait()
    await worker.set_job(document, lease_token=new_id())  # another worker took it over
    with capture_logs() as logged:
        assert await running == 1
    assert "job_lease_lost" in [line["event"] for line in logged]
    row = await row_of(module_database, tenant, document)
    assert (row.state, row.mark is not None) == ("signed", True)
    await worker.set_job(document, status="ready", lease_token=None, lease_until=None)  # its recovery
    assert await worker.drain(document) == ["again:30", "again:2", "done"]
    assert worker.network.posts(RECEPTION) == 1


async def test_two_runs_of_one_document_at_once_send_it_once(
    worker: Worker, module_database: BoundDatabase
) -> None:
    """Both read it signed and unmarked; the mark is a compare-and-set: one sends, the other waits."""
    tenant = a_tenant()
    await with_range(module_database, tenant)
    document = await worker.issued(draft_of(tenant))
    first, second = await asyncio.gather(worker.run(tenant, document), worker.run(tenant, document))
    assert isinstance(first, RunAgain)
    assert isinstance(second, RunAgain)
    assert worker.network.posts(RECEPTION) == 1
    assert (await row_of(module_database, tenant, document)).state == "submitted"
    assert await worker.drain(document) == ["done"]


async def test_a_late_answer_of_a_run_overtaken_is_adopted_and_never_resent(
    worker: Worker, module_database: BoundDatabase
) -> None:
    """A run that lost its lease is answered after the next run found its mark: DGII answered the document."""
    tenant = a_tenant()
    await with_range(module_database, tenant)
    document = await worker.issued(draft_of(tenant))
    release = asyncio.Event()
    worker.network.held[RECEPTION] = release
    zombie = asyncio.create_task(worker.run(tenant, document))
    await worker.network.delivered.wait()
    assert isinstance(await worker.run(tenant, document), RunAgain)  # the mark found: unknown
    assert (await row_of(module_database, tenant, document)).state == "outcome_unknown"
    release.set()
    await zombie
    row = await row_of(module_database, tenant, document)
    assert (row.state, row.track_id) == ("submitted", worker.fake.received[0].track_id)
    assert await worker.drain(document) == ["done"]
    assert worker.network.posts(RECEPTION) == 1


async def test_a_late_answer_after_the_track_id_was_adopted_changes_nothing(
    worker: Worker, module_database: BoundDatabase
) -> None:
    tenant = a_tenant()
    await with_range(module_database, tenant)
    document = await worker.issued(draft_of(tenant))
    release = asyncio.Event()
    worker.network.held[RECEPTION] = release
    zombie = asyncio.create_task(worker.run(tenant, document))
    await worker.network.delivered.wait()
    await worker.run(tenant, document)  # unknown
    await worker.run(tenant, document)  # adopted
    adopted = await row_of(module_database, tenant, document)
    assert adopted.state == "submitted"
    release.set()
    await zombie
    assert await row_of(module_database, tenant, document) == adopted
    assert worker.network.posts(RECEPTION) == 1


# ── DGII certainly did not take it: the mark cleared, tried again later ──────────────


@pytest.mark.parametrize("failure", ["throttled", "unreachable"])
async def test_a_submission_that_never_left_is_unmarked_and_tried_again_after_a_wait(
    worker: Worker, module_database: BoundDatabase, failure: str
) -> None:
    """A 429, no connection: DGII certainly did not take it; the mark is cleared, nothing is unknown."""
    tenant = a_tenant()
    await with_range(module_database, tenant)
    document = await worker.issued(draft_of(tenant))
    rules = worker.network.throttled if failure == "throttled" else worker.network.unreachable
    rules[RECEPTION] = 2
    before = await database_now(module_database)
    again_after(await worker.run(tenant, document), before, UNAVAILABLE_WAIT)
    row = await row_of(module_database, tenant, document)
    assert (row.state, row.mark, row.polls, row.version) == ("signed", None, 1, 2)
    assert worker.receptions(row.encf) == []
    assert await worker.drain(document) == ["again:30", "again:1", "done"]
    assert len(worker.receptions(row.encf)) == 1
    assert worker.network.posts(RECEPTION) == 3  # two never delivered, one taken


async def test_a_company_that_cannot_sign_dgii_s_seed_waits_for_its_certificate_unmarked(
    started: Started, module_database: BoundDatabase, signer: KeyPairSigner
) -> None:
    """No token without a signed seed: nothing left; resumed once a certificate is installed."""
    tenant = a_tenant()
    await with_range(module_database, tenant)
    moments = [datetime(2200, 1, 1, tzinfo=UTC)]
    expired = TestSigner(sign=signer.sign, sign_seed=signer.sign, clock=lambda: moments[-1])
    async with started.worker(expired) as worker:
        document = await worker.issued(draft_of(tenant))
        assert await worker.drain(document) == ["parked:certificate"]
        row = await row_of(module_database, tenant, document)
        assert (row.state, row.mark) == ("signed", None)
        moments.append(datetime.now(UTC))
        call = SystemCall(reason="fiscal.certificates", scope=tenant)
        await worker.channel.resume(call, tenant.company, ResumeReason.CERTIFICATE_INSTALLED)
        assert (await worker.job(document))[0] == "ready"
        assert await worker.drain(document) == ["again:1", "done"]
        assert worker.network.posts(RECEPTION) == 1


# ── an unknown outcome settled by track ids (Decisions 7, 9) ─────────────────────────


async def broken_once(
    worker: Worker, module_database: BoundDatabase, tenant: Tenant, draft: FiscalDraft
) -> UUID:
    """``draft``'s document sent once, its answer lost: outcome unknown."""
    document = await worker.issued(draft)
    worker.network.broken.add(RECEPTION)
    await worker.run(tenant, document)
    worker.network.broken.clear()
    assert (await row_of(module_database, tenant, document)).state == "outcome_unknown"
    return document


@pytest.mark.parametrize(
    ("before_signing", "adopted"), [(timedelta(minutes=3), False), (RECEIPT_TOLERANCE, True)]
)
async def test_a_track_id_received_before_signing_less_the_tolerance_is_never_adopted(
    worker: Worker, module_database: BoundDatabase, *, before_signing: timedelta, adopted: bool
) -> None:
    tenant = a_tenant()
    await with_range(module_database, tenant)
    document = await broken_once(worker, module_database, tenant, draft_of(tenant))
    signed_at = (await row_of(module_database, tenant, document)).signed_at
    assert signed_at is not None
    (received,) = worker.fake.received
    received.received_at = signed_at - before_signing
    before = await database_now(module_database)
    outcome = await worker.run(tenant, document)
    row = await row_of(module_database, tenant, document)
    assert row.track_id == (received.track_id if adopted else None)
    assert row.state == ("submitted" if adopted else "outcome_unknown")
    again_after(outcome, before, timedelta(seconds=2))
    assert worker.network.posts(RECEPTION) == 1


async def test_a_track_id_of_an_earlier_document_with_the_same_number_is_never_adopted(
    worker: Worker, module_database: BoundDatabase, clock: ManualClock
) -> None:
    """Decision 9's second safeguard: the released number's earlier track id, even listed fresh."""
    tenant = a_tenant()
    await with_range(module_database, tenant)
    worker.fake.script.default = Scripted(status=Status.REJECTED, sequence_used=False)
    earlier = await worker.issued(draft_of(tenant))
    await worker.drain(earlier)
    (earlier_reception,) = worker.fake.received
    worker.fake.script.default = Scripted()
    clock.advance(RECEIPT_TOLERANCE + timedelta(minutes=1))
    later = await worker.issued(draft_of(tenant))
    later_row = await row_of(module_database, tenant, later)
    assert later_row.encf == "E310000000001"
    worker.network.crashing.add(RECEPTION)  # sent, its answer lost...
    with pytest.raises(CrashedError):
        await worker.run(tenant, later)
    worker.network.crashing.clear()
    worker.fake.received.pop()  # ...and never recorded by DGII
    assert later_row.signed_at is not None
    earlier_reception.received_at = later_row.signed_at  # listed as if fresh (a clock skewed, a late list)
    await worker.run(tenant, later)  # unknown
    await worker.run(tenant, later)  # asked: only the earlier document's
    row = await row_of(module_database, tenant, later)
    assert (row.state, row.track_id, row.polls) == ("outcome_unknown", None, 1)
    assert [(each[0], each[1]) for each in await submissions_of(module_database, tenant)] == [
        (earlier, earlier_reception.track_id)
    ]


async def test_the_newest_fresh_track_id_is_adopted_and_a_repeated_one_is_one(
    worker: Worker, module_database: BoundDatabase
) -> None:
    tenant = a_tenant()
    await with_range(module_database, tenant)
    document = await broken_once(worker, module_database, tenant, draft_of(tenant))
    (first,) = worker.fake.received
    newer = replace(first, track_id="f" * 32, received_at=first.received_at + timedelta(seconds=1))
    worker.fake.received.extend([newer, replace(newer)])
    await worker.run(tenant, document)
    assert (await row_of(module_database, tenant, document)).track_id == "f" * 32


async def test_no_fresh_track_id_is_asked_for_again_on_the_schedule_changing_nothing(
    worker: Worker, module_database: BoundDatabase
) -> None:
    tenant = a_tenant()
    await with_range(module_database, tenant)
    document = await broken_once(worker, module_database, tenant, draft_of(tenant))
    worker.network.hidden = True
    assert await worker.drain(document, limit=4) == ["again:2", "again:5", "again:10", "again:30"]
    row = await row_of(module_database, tenant, document)
    assert (row.state, row.polls, row.version) == ("outcome_unknown", 4, 3)
    assert states_of(await updates_of(module_database, tenant))[-1] == "outcome_unknown"
    assert worker.network.posts(RECEPTION) == 1


async def test_an_outcome_unknown_for_a_day_waits_for_a_person_and_says_so(
    worker: Worker, module_database: BoundDatabase
) -> None:
    tenant = a_tenant()
    await with_range(module_database, tenant)
    document = await broken_once(worker, module_database, tenant, draft_of(tenant))
    worker.network.hidden = True
    since = (await row_of(module_database, tenant, document)).since
    assert since is not None
    await set_row(module_database, tenant, document, track_id_since=since - TRACK_ID_WINDOW)
    with capture_logs() as logged:
        assert await worker.drain(document) == ["parked:outcome_unknown"]
    assert events_of(logged) == [("do_ecf.waiting_for_a_person", "error", "outcome_unknown")]
    assert worker.network.posts(RECEPTION) == 1
    worker.network.hidden = False  # an operator wakes it once DGII lists it
    call = SystemCall(reason="operator", scope=tenant)
    await worker.channel.resume(call, tenant.company, ResumeReason.OPERATOR)
    assert await worker.drain(document) == ["again:5", "done"]  # the schedule goes on: two asks made
    assert worker.network.posts(RECEPTION) == 1


async def test_in_certecf_an_unknown_outcome_waits_for_a_person_at_once(
    worker: Worker, module_database: BoundDatabase
) -> None:
    """CerteCF lists no track ids: nothing is asked, the document waits (Decision 7)."""
    tenant = a_tenant()
    await with_range(module_database, tenant, environment=Environment.CERTECF)
    draft = replace(draft_of(tenant), environment="certecf")
    document = await broken_once(worker, module_database, tenant, draft)
    assert await worker.drain(document) == ["parked:outcome_unknown"]
    assert TRACK_IDS not in worker.network.asked()
    assert worker.network.posts(RECEPTION) == 1


async def test_an_adopted_track_id_dgii_does_not_find_leaves_it_unknown_since_the_first_time(
    worker: Worker, module_database: BoundDatabase
) -> None:
    """``track_id_since`` is never reset: the window the next test closes runs from the first time."""
    tenant = a_tenant()
    await with_range(module_database, tenant)
    document = await broken_once(worker, module_database, tenant, draft_of(tenant))
    since = (await row_of(module_database, tenant, document)).since
    await worker.run(tenant, document)  # adopted
    (received,) = worker.fake.received
    received.track_id = "e" * 32  # DGII knows the adopted one no more
    await worker.run(tenant, document)  # polled: 0, No encontrado
    row = await row_of(module_database, tenant, document)
    assert (row.state, row.since, row.code) == ("outcome_unknown", since, 0)
    await worker.run(tenant, document)  # the new one adopted
    assert (await row_of(module_database, tenant, document)).track_id == "e" * 32
    assert worker.network.posts(RECEPTION) == 1


async def test_a_track_id_dgii_lists_but_never_finds_waits_for_a_person_once_the_window_closed(
    worker: Worker, module_database: BoundDatabase
) -> None:
    """The adopt → No encontrado → unknown loop ends with the window: a wake buys one lookup and one poll."""
    tenant = a_tenant()
    await with_range(module_database, tenant)
    document = await broken_once(worker, module_database, tenant, draft_of(tenant))
    row = await row_of(module_database, tenant, document)
    assert row.since is not None
    (received,) = worker.fake.received
    received.status = Status.NOT_FOUND  # listed, never found
    await set_row(module_database, tenant, document, track_id_since=row.since - timedelta(days=3))
    with capture_logs() as logged:
        assert await worker.drain(document) == ["again:2", "parked:outcome_unknown"]
    assert events_of(logged) == [("do_ecf.waiting_for_a_person", "error", "outcome_unknown")]
    call = SystemCall(reason="operator", scope=tenant)
    await worker.channel.resume(call, tenant.company, ResumeReason.OPERATOR)
    with capture_logs() as logged:
        assert await worker.drain(document) == ["again:10", "parked:outcome_unknown"]
    assert events_of(logged) == [("do_ecf.waiting_for_a_person", "error", "outcome_unknown")]
    parked = await row_of(module_database, tenant, document)
    assert (parked.state, parked.code, parked.polls) == ("outcome_unknown", 0, 4)
    assert parked.since == row.since - timedelta(days=3)
    assert worker.network.posts(RECEPTION) == 1


# ── RFCE (Decision 37) ───────────────────────────────────────────────────────────────


async def test_a_consumer_invoice_summary_is_decided_at_once_and_never_delivered(
    worker: Worker, module_database: BoundDatabase
) -> None:
    tenant = a_tenant()
    await with_range(module_database, tenant, CONSUMER)
    document = await worker.issued(consumer(tenant))
    assert await worker.drain(document) == ["done"]
    row = await row_of(module_database, tenant, document)
    assert (row.state, row.fate, row.delivery, row.track_id) == ("accepted", "used", "not_applicable", None)
    assert (worker.network.posts(SUMMARY), worker.network.posts(RECEPTION)) == (1, 0)
    assert states_of(await updates_of(module_database, tenant)) == ["numbered", "signed", "accepted"]


async def test_a_summary_whose_answer_was_lost_is_settled_by_consultarfce_never_resent(
    worker: Worker, module_database: BoundDatabase
) -> None:
    """0 (No encontrado) twice changes nothing; then 1: accepted."""
    tenant = a_tenant()
    await with_range(module_database, tenant, CONSUMER)
    document = await worker.issued(consumer(tenant))
    worker.network.broken.add(SUMMARY)
    assert await worker.drain(document, limit=1) == ["again:30"]
    worker.network.broken.clear()
    (received,) = worker.fake.received
    worker.fake.received.clear()  # not yet recorded by DGII
    assert await worker.drain(document, limit=2) == ["again:2", "again:5"]
    row = await row_of(module_database, tenant, document)
    assert (row.state, row.code, row.polls, row.version) == ("outcome_unknown", 0, 2, 3)
    worker.fake.received.append(received)
    assert await worker.drain(document) == ["done"]
    assert (await row_of(module_database, tenant, document)).state == "accepted"
    assert worker.network.posts(SUMMARY) == 1
    assert worker.network.asked() == [SUMMARY, RFCE_QUERY, RFCE_QUERY, RFCE_QUERY]


async def test_a_summary_rejected_by_consultarfce_releases_its_number_as_dgii_says(
    worker: Worker, module_database: BoundDatabase
) -> None:
    tenant = a_tenant()
    await with_range(module_database, tenant, CONSUMER)
    worker.fake.script.default = Scripted(status=Status.REJECTED, sequence_used=False)
    document = await worker.issued(consumer(tenant))
    worker.network.broken.add(SUMMARY)
    await worker.run(tenant, document)
    worker.network.broken.clear()
    assert await worker.drain(document) == ["done"]
    row = await row_of(module_database, tenant, document)
    assert (row.state, row.fate) == ("rejected", "released")


async def test_a_summary_dgii_never_finds_in_a_day_waits_for_a_person(
    worker: Worker, module_database: BoundDatabase
) -> None:
    tenant = a_tenant()
    await with_range(module_database, tenant, CONSUMER)
    document = await worker.issued(consumer(tenant))
    worker.network.broken.add(SUMMARY)
    await worker.run(tenant, document)
    worker.network.broken.clear()
    worker.fake.received.clear()
    since = (await row_of(module_database, tenant, document)).since
    assert since is not None
    await set_row(module_database, tenant, document, track_id_since=since - TRACK_ID_WINDOW)
    assert await worker.drain(document) == ["parked:outcome_unknown"]
    assert worker.network.posts(SUMMARY) == 1


async def test_a_marked_summary_is_asked_about_never_resent(
    worker: Worker, module_database: BoundDatabase
) -> None:
    tenant = a_tenant()
    await with_range(module_database, tenant, CONSUMER)
    document = await worker.issued(consumer(tenant))
    worker.network.crashing.add(SUMMARY)
    assert await worker.drain(document, limit=1) == ["retry:internal"]
    worker.network.crashing.clear()
    assert await worker.drain(document) == ["again:30", "done"]
    assert worker.network.asked() == [SUMMARY, RFCE_QUERY]
    assert (await row_of(module_database, tenant, document)).state == "accepted"


async def test_a_summary_answered_with_a_code_dgii_does_not_document_is_settled_later(
    worker: Worker, module_database: BoundDatabase
) -> None:
    """DGII took it (it answered) but said nothing documented: unknown, never a guess, never resent."""
    tenant = a_tenant()
    await with_range(module_database, tenant, CONSUMER)
    worker.fake.script.default = Scripted(status=Status.IN_PROCESS)
    document = await worker.issued(consumer(tenant))
    assert await worker.drain(document, limit=1) == ["again:30"]
    assert (await row_of(module_database, tenant, document)).state == "outcome_unknown"
    assert await worker.drain(document, limit=1) == ["again:30"]  # consultarfce answers 3: not documented
    assert worker.network.posts(SUMMARY) == 1


# ── DGII out of reach, answers that do not fit ───────────────────────────────────────


async def test_a_result_dgii_cannot_give_now_is_asked_again_after_a_wait_changing_nothing(
    worker: Worker, module_database: BoundDatabase
) -> None:
    tenant = a_tenant()
    await with_range(module_database, tenant)
    document = await worker.issued(draft_of(tenant))
    await worker.run(tenant, document)  # submitted
    worker.fake.script.outage = 3  # the poll's three tries answer 503
    before = await database_now(module_database)
    again_after(await worker.run(tenant, document), before, UNAVAILABLE_WAIT)
    row = await row_of(module_database, tenant, document)
    assert (row.state, row.polls, row.version) == ("submitted", 1, 3)
    assert await worker.drain(document) == ["done"]


async def test_an_answer_about_another_e_ncf_waits_for_a_person_changing_nothing(
    worker: Worker, module_database: BoundDatabase
) -> None:
    tenant = a_tenant()
    await with_range(module_database, tenant)
    document = await worker.issued(draft_of(tenant))
    await worker.run(tenant, document)
    (received,) = worker.fake.received
    received.encf = "E310000000099"
    with capture_logs() as logged:
        assert await worker.run(tenant, document) == Park("answer_mismatch")
    assert events_of(logged) == [("do_ecf.waiting_for_a_person", "error", "answer_mismatch")]
    assert (await row_of(module_database, tenant, document)).state == "submitted"


async def test_a_summary_answer_about_another_e_ncf_waits_for_a_person_then_is_asked_about(
    worker: Worker, module_database: BoundDatabase
) -> None:
    """Never taken as ours (``AnswerMismatchError``): the state unchanged, its mark kept; once an operator
    wakes it, the mark makes its outcome unknown and ``consultarfce`` settles it, never a resend."""
    tenant = a_tenant()
    await with_range(module_database, tenant, CONSUMER)
    document = await worker.issued(consumer(tenant))
    worker.network.renamed[SUMMARY] = ("E320000000001", "E320000000099")
    with capture_logs() as logged:
        assert await worker.drain(document) == ["parked:answer_mismatch"]
    assert events_of(logged) == [("do_ecf.waiting_for_a_person", "error", "answer_mismatch")]
    row = await row_of(module_database, tenant, document)
    assert (row.state, row.mark is not None, row.version) == ("signed", True, 2)
    worker.network.renamed.clear()
    await worker.channel.resume(
        SystemCall(reason="operator", scope=tenant), tenant.company, ResumeReason.OPERATOR
    )
    assert await worker.drain(document) == ["again:30", "done"]
    assert worker.network.posts(SUMMARY) == 1
    assert (await row_of(module_database, tenant, document)).state == "accepted"


async def test_a_signed_document_whose_archived_bytes_do_not_match_is_never_marked_nor_sent(
    worker: Worker, module_database: BoundDatabase
) -> None:
    """The bytes are read back and their SHA-256 checked before anything is marked (Decision 12)."""
    tenant = a_tenant()
    await with_range(module_database, tenant)
    document = await worker.issued(draft_of(tenant))
    await set_row(module_database, tenant, document, ecf_sha256=bytes(32))
    with pytest.raises(ArchiveDigestMismatchError):
        await worker.run(tenant, document)
    row = await row_of(module_database, tenant, document)
    assert (row.state, row.mark) == ("signed", None)
    assert worker.network.posts(RECEPTION) == 0


# ── waking (Channel.resume, a range registered) ──────────────────────────────────────


@final
class Enrolled(Enrollments):
    """Every company enrolled everywhere."""

    @override
    async def enrolled(self, tenant: Tenant, environment: Environment) -> bool:
        del tenant, environment
        return True


async def parked(worker: Worker, document: UUID, reason: str) -> None:
    await worker.set_job(document, status="parked", parked_reason=reason)


@pytest.mark.parametrize(
    ("reason", "woken"),
    [
        (ResumeReason.CERTIFICATE_INSTALLED, {"certificate"}),
        (ResumeReason.RANGE_REGISTERED, {"range_missing"}),
        (ResumeReason.OPERATOR, {"certificate", "outcome_unknown", "answer_mismatch"}),
    ],
)
async def test_resume_wakes_the_company_s_jobs_waiting_for_what_it_settles(
    worker: Worker, module_database: BoundDatabase, reason: ResumeReason, woken: set[str]
) -> None:
    tenant = a_tenant()
    await with_range(module_database, tenant)
    waiting = ("certificate", "range_missing", "outcome_unknown", "answer_mismatch", "blocked")
    documents = {each: await worker.issued(draft_of(tenant), sign=False) for each in waiting}
    for why, document in documents.items():
        await parked(worker, document, why)
    other = a_tenant()
    await with_range(module_database, other)
    elsewhere = await worker.issued(draft_of(other), sign=False)
    await parked(worker, elsewhere, "certificate")
    await worker.channel.resume(SystemCall(reason="fiscal", scope=tenant), tenant.company, reason)
    ready = {why for why, document in documents.items() if (await worker.job(document))[0] == "ready"}
    assert ready == woken
    assert (await worker.job(elsewhere))[0] == "parked"


async def test_resume_is_refused_for_a_company_the_call_is_not_about(worker: Worker) -> None:
    tenant = a_tenant()
    with pytest.raises(InvalidValueError, match=r"call\.scope"):
        await worker.channel.resume(
            SystemCall(reason="fiscal", scope=tenant), a_tenant().company, ResumeReason.OPERATOR
        )


async def test_a_range_registered_wakes_the_company_s_jobs_waiting_for_one(
    worker: Worker, module_database: BoundDatabase
) -> None:
    tenant = a_tenant()
    await with_range(module_database, tenant)
    document = await worker.issued(draft_of(tenant), sign=False)
    await parked(worker, document, "range_missing")
    ranging = Ranging(PgUnits(module_database), Enrolled())
    request = RangeRequest(
        environment=Environment.TESTECF,
        authorization=Authorization(ecf_type=EcfType.CONSUMER_INVOICE, first=1, last=10, expires_on=EXPIRES),
    )
    async with PgUnits(module_database).unit(tenant) as unit:
        await ranging.register(unit, request)
    assert (await worker.job(document))[0] == "ready"


# ── never two non-rejected documents with one e-NCF; one submission each (property) ──


FATES: Final = (
    Scripted(),
    Scripted(status=Status.REJECTED, sequence_used=False),
    Scripted(status=Status.REJECTED, sequence_used=True),
    Scripted(status=Status.ACCEPTED_CONDITIONALLY),
)
"""What the fake decides, by turns: accepted, released, burned, accepted with observations."""


@pytest.mark.parametrize("shift", [0, 1, 2, 3])
async def test_under_concurrency_each_document_is_sent_once_and_no_number_is_held_twice(
    worker: Worker, module_database: BoundDatabase, clock: ManualClock, shift: int
) -> None:
    """Outcomes by turns per e-NCF, released numbers taken again, every job run at once with a twin."""
    tenant = a_tenant()
    await with_range(module_database, tenant)
    documents: list[UUID] = []
    for _ in range(3):
        drafts = [draft_of(tenant) for _ in range(4)]
        issued = await asyncio.gather(*(worker.issued(each, sign=False) for each in drafts))
        for turn, document in enumerate(issued, start=shift + len(documents)):
            row = await row_of(module_database, tenant, document)
            worker.fake.script.by_encf[row.encf] = FATES[turn % len(FATES)]
        documents += issued
        for _ in range(6):
            await asyncio.gather(*(worker.run(tenant, each) for each in issued for _ in range(2)))
        clock.advance(RECEIPT_TOLERANCE + timedelta(seconds=1))
    async with module_database.unit(tenant) as unit:
        held = await unit.connection.execute(
            select(DocumentRecord.sequence, func.count())
            .where(DocumentRecord.number_fate.is_distinct_from("released"))
            .group_by(DocumentRecord.sequence)
        )
        assert all(count == 1 for _, count in held.all())
        standing = await unit.connection.execute(
            select(DocumentRecord.sequence, func.count())
            .where(DocumentRecord.state != "rejected")
            .group_by(DocumentRecord.sequence)
        )
        assert all(count == 1 for _, count in standing.all())  # no e-NCF of two non-rejected documents
        numbered = await unit.connection.execute(
            select(DocumentRecord.sequence, func.count()).group_by(DocumentRecord.sequence)
        )
        assert any(count > 1 for _, count in numbered.all())  # a released number numbered another
    for document in documents:
        assert (await row_of(module_database, tenant, document)).state in {
            "accepted",
            "accepted_conditionally",
            "rejected",
        }
    assert worker.network.posts(RECEPTION) == len(worker.fake.received) == len(documents)


# ── races: a step's answer recorded only while the document is as the step read it ──


async def summary_unknown(worker: Worker, module_database: BoundDatabase, tenant: Tenant) -> UUID:
    """A consumer invoice's summary sent once, its answer lost: outcome unknown."""
    document = await worker.issued(consumer(tenant))
    worker.network.broken.add(SUMMARY)
    await worker.run(tenant, document)
    worker.network.broken.clear()
    assert (await row_of(module_database, tenant, document)).state == "outcome_unknown"
    return document


@pytest.mark.parametrize("undocumented", [False, True], ids=["answered", "undocumented"])
async def test_a_result_that_comes_after_another_run_decided_changes_nothing(
    worker: Worker, module_database: BoundDatabase, *, undocumented: bool
) -> None:
    """Another run decided while this one waited for DGII: its answer (or its unknown code) is dropped."""
    tenant = a_tenant()
    await with_range(module_database, tenant)
    document = await worker.issued(draft_of(tenant))
    await worker.run(tenant, document)  # submitted
    release = asyncio.Event()
    worker.network.held[RESULT] = release
    late = asyncio.create_task(worker.run(tenant, document))
    await worker.network.delivered.wait()
    assert await worker.run(tenant, document) == Done()
    decided = await row_of(module_database, tenant, document)
    if undocumented:
        worker.network.renamed[RESULT] = ('"codigo":1', '"codigo":7')
    release.set()
    assert isinstance(await late, RunAgain)
    assert await row_of(module_database, tenant, document) == decided


async def test_track_ids_that_come_after_another_run_adopted_one_change_nothing(
    worker: Worker, module_database: BoundDatabase
) -> None:
    tenant = a_tenant()
    await with_range(module_database, tenant)
    document = await broken_once(worker, module_database, tenant, draft_of(tenant))
    release = asyncio.Event()
    worker.network.held[TRACK_IDS] = release
    late = asyncio.create_task(worker.run(tenant, document))
    await worker.network.delivered.wait()
    await worker.run(tenant, document)  # adopted
    adopted = await row_of(module_database, tenant, document)
    assert adopted.state == "submitted"
    release.set()
    assert isinstance(await late, RunAgain)
    assert await row_of(module_database, tenant, document) == adopted


async def test_a_summary_s_status_that_comes_after_another_run_decided_changes_nothing(
    worker: Worker, module_database: BoundDatabase
) -> None:
    tenant = a_tenant()
    await with_range(module_database, tenant, CONSUMER)
    document = await summary_unknown(worker, module_database, tenant)
    release = asyncio.Event()
    worker.network.held[RFCE_QUERY] = release
    late = asyncio.create_task(worker.run(tenant, document))
    await worker.network.delivered.wait()
    assert await worker.run(tenant, document) == Done()
    decided = await row_of(module_database, tenant, document)
    release.set()
    assert isinstance(await late, RunAgain)
    assert await row_of(module_database, tenant, document) == decided


async def test_a_submission_refused_by_a_429_after_another_run_found_its_mark_stays_unknown(
    worker: Worker, module_database: BoundDatabase
) -> None:
    """The mark is cleared only by the run that set it, while the document is still signed: never resent."""
    tenant = a_tenant()
    await with_range(module_database, tenant)
    document = await worker.issued(draft_of(tenant))
    release = asyncio.Event()
    worker.network.stalled[RECEPTION] = release
    worker.network.throttled[RECEPTION] = 1
    late = asyncio.create_task(worker.run(tenant, document))
    await worker.network.delivered.wait()
    await worker.run(tenant, document)  # the mark found: unknown
    unknown = await row_of(module_database, tenant, document)
    release.set()
    assert isinstance(await late, RunAgain)
    assert await row_of(module_database, tenant, document) == unknown
    assert (unknown.state, unknown.mark is not None) == ("outcome_unknown", True)
    assert worker.receptions(unknown.encf) == []


async def test_a_submission_refused_by_a_429_keeps_a_mark_another_run_set_meanwhile(
    worker: Worker, module_database: BoundDatabase
) -> None:
    """Still ``signed``, but no longer this run's mark: the run that set it is the one to clear it."""
    tenant = a_tenant()
    await with_range(module_database, tenant)
    document = await worker.issued(draft_of(tenant))
    release = asyncio.Event()
    worker.network.stalled[RECEPTION] = release
    worker.network.throttled[RECEPTION] = 1
    late = asyncio.create_task(worker.run(tenant, document))
    await worker.network.delivered.wait()
    ours = await row_of(module_database, tenant, document)
    assert ours.mark is not None
    theirs = ours.mark + timedelta(seconds=1)
    await set_row(module_database, tenant, document, submission_started_at=theirs)
    release.set()
    assert isinstance(await late, RunAgain)
    kept = await row_of(module_database, tenant, document)
    assert (kept.state, kept.mark, kept.polls, kept.version) == ("signed", theirs, ours.polls, ours.version)
    assert worker.receptions(kept.encf) == []


async def test_a_submission_broken_after_another_run_found_its_mark_changes_nothing_more(
    worker: Worker, module_database: BoundDatabase
) -> None:
    tenant = a_tenant()
    await with_range(module_database, tenant)
    document = await worker.issued(draft_of(tenant))
    release = asyncio.Event()
    worker.network.held[RECEPTION] = release
    worker.network.broken.add(RECEPTION)
    late = asyncio.create_task(worker.run(tenant, document))
    await worker.network.delivered.wait()
    await worker.run(tenant, document)  # the mark found: unknown
    unknown = await row_of(module_database, tenant, document)
    release.set()
    assert isinstance(await late, RunAgain)
    assert await row_of(module_database, tenant, document) == unknown
    assert worker.network.posts(RECEPTION) == 1


# ── certificates, DGII out of reach, answers that do not fit: while asking ──────────


@pytest.mark.parametrize("found", ["submitted", "outcome_unknown", "summary_unknown"])
async def test_a_company_that_cannot_sign_dgii_s_seed_waits_for_its_certificate_while_asking(
    worker: Worker, started: Started, module_database: BoundDatabase, signer: KeyPairSigner, found: str
) -> None:
    """A fresh client needs a token: without a signed seed, nothing is asked and nothing changes."""
    tenant = a_tenant()
    await with_range(module_database, tenant)
    await with_range(module_database, tenant, CONSUMER)
    if found == "submitted":
        document = await worker.issued(draft_of(tenant))
        await worker.run(tenant, document)
    elif found == "outcome_unknown":
        document = await broken_once(worker, module_database, tenant, draft_of(tenant))
    else:
        document = await summary_unknown(worker, module_database, tenant)
    before = await row_of(module_database, tenant, document)
    expired = TestSigner(
        sign=signer.sign, sign_seed=signer.sign, clock=lambda: datetime(2200, 1, 1, tzinfo=UTC)
    )
    async with started.worker(expired) as unable:
        assert await unable.run(tenant, document) == Park("certificate")
    assert await row_of(module_database, tenant, document) == before


@pytest.mark.parametrize("asked", [TRACK_IDS, RFCE_QUERY])
async def test_an_unknown_outcome_dgii_cannot_tell_now_is_asked_again_after_a_wait_then_a_person(
    worker: Worker, module_database: BoundDatabase, asked: str
) -> None:
    tenant = a_tenant()
    await with_range(module_database, tenant)
    await with_range(module_database, tenant, CONSUMER)
    if asked == TRACK_IDS:
        document = await broken_once(worker, module_database, tenant, draft_of(tenant))
    else:
        document = await summary_unknown(worker, module_database, tenant)
    worker.network.failing.add(asked)
    before = await database_now(module_database)
    again_after(await worker.run(tenant, document), before, UNAVAILABLE_WAIT)
    row = await row_of(module_database, tenant, document)
    assert (row.state, row.polls, row.version) == ("outcome_unknown", 1, 3)
    assert row.since is not None
    await set_row(module_database, tenant, document, track_id_since=row.since - TRACK_ID_WINDOW)
    with capture_logs() as logged:
        assert await worker.run(tenant, document) == Park("outcome_unknown")
    assert events_of(logged) == [("do_ecf.waiting_for_a_person", "error", "outcome_unknown")]
    assert (await row_of(module_database, tenant, document)).polls == 2


async def test_a_summary_s_status_about_another_e_ncf_waits_for_a_person(
    worker: Worker, module_database: BoundDatabase
) -> None:
    tenant = a_tenant()
    await with_range(module_database, tenant, CONSUMER)
    document = await summary_unknown(worker, module_database, tenant)
    worker.network.renamed[RFCE_QUERY] = ('"encf":"E320000000001"', '"encf":"E320000000099"')
    assert await worker.run(tenant, document) == Park("answer_mismatch")
    assert (await row_of(module_database, tenant, document)).state == "outcome_unknown"
