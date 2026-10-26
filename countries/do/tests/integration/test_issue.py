"""``Channel.check`` and ``Channel.issue`` (spec §8.3, §8.5 step 4; Decisions 6, 11, 25, 35).

``check`` writes nothing: the draft's objections, a type with no number to give now, a note whose invoice
has no accepted e-CF here. ``issue`` numbers a document once per invoice under its series' lock and commits
it before anything is signed or archived (a unit that rolls back gives its number back); within the budget
it signs, archives, then records the signature in a unit that finds the document still numbered (a later
signature is discarded, its archive object an orphan). The ``do_ecf.advance`` job is enqueued with the
document: at once, or 5 s later when the request signs.
"""

import asyncio
from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import Final, Protocol, Unpack, final, override
lazy from collections.abc import Mapping
lazy from decimal import Decimal
lazy from uuid import UUID

import pytest
from pydantic import TypeAdapter
from sqlalchemy import select, update
from structlog.testing import capture_logs
lazy import psycopg

from ledger_country_do.channels.ecf_v1.adapters.channel import DoEcfChannel
from ledger_country_do.channels.ecf_v1.adapters.outbound import OutboundClosedError
from ledger_country_do.channels.ecf_v1.adapters.postgres.tables import DocumentRecord
from ledger_country_do.channels.ecf_v1.adapters.postgres.unit import bind
from ledger_country_do.channels.ecf_v1.application.issuing import SIGNING_DELAY, DocumentMissingError
from ledger_country_do.channels.ecf_v1.application.ranges import NumberingRange
from ledger_country_do.channels.ecf_v1.assembly import assemble
from ledger_country_do.channels.ecf_v1.domain.encf import EcfType
from ledger_country_do.channels.ecf_v1.domain.environments import Environment
from ledger_country_do.channels.ecf_v1.domain.mapping import DraftObjectedError
from ledger_country_do.channels.ecf_v1.domain.notation import local_date
from ledger_country_do.channels.ecf_v1.domain.numbering import (
    NoNumberAvailableError,
    RangeState,
    RangeStatus,
)
from ledger_country_do.channels.ecf_v1.settings import DoEcfSettings
from ledger_country_do_testing.fiscal import Priced, fiscal_draft
from ledger_fiscal_kit.drafts import DocumentRef, FiscalReference
from ledger_fiscal_kit.objections import NumberUnavailableError
from ledger_fiscal_kit.ports import ARCHIVE_PORT, SIGNER_PORT, ArchivePort, SignatureRefusedError, SignerPort
from ledger_fiscal_kit.status import FiscalStatus, Severity
from ledger_kernel.errors import InvalidValueError
from ledger_kernel.ids import CompanyId, OrganizationId, new_id
from ledger_kernel.regimes import Direction, DocumentKind
from ledger_kernel.tenancy import Mode, Tenant
from ledger_platform.data.codec import JsonValue
from ledger_platform.data.tables import chassis_tables
from ledger_platform.jobs.outcomes import Job, Park, RunAgain
from ledger_platform.runtime.calls import SystemCall
from ledger_platform.runtime.facade import facade
from ledger_testing.signer import TestSigner
lazy from ledger_country_do_testing.fiscal import DraftOptions
lazy from ledger_country_do_testing.local_signer import KeyPairSigner
lazy from ledger_fiscal_kit.channels import StatusUpdate
lazy from ledger_fiscal_kit.drafts import FiscalDraft
lazy from ledger_fiscal_kit.ports import ArchivedObject, CertificateValidity, SeedToSign, Signed, XmlToSign
lazy from ledger_platform.config.manifest import AppRole
lazy from ledger_platform.config.settings import Environment as AppEnvironment
lazy from ledger_platform.data.bound import BoundDatabase
lazy from ledger_platform.jobs.outcomes import JobOutcome, JobSpec
lazy from ledger_platform.runtime.archiving import StoreArchive
lazy from ledger_platform.runtime.contexts import ChannelContext
lazy from ledger_testing.clock import ManualClock

RECEIVED: Final = DoEcfSettings(receiver_public_url="https://receiver.ledger.test")
BUDGET: Final = timedelta(seconds=1.5)
EXPIRES: Final = date(2028, 12, 31)
CHASSIS: Final = chassis_tables("do_ecf")
PAYLOAD: Final = TypeAdapter(dict[str, JsonValue])
RUN: Final = TypeAdapter(tuple[datetime, str])
JOB: Final = TypeAdapter(tuple[str, int, datetime | None])
KILLED: Final = """
    UPDATE do_ecf.jobs SET status = 'dead', attempts = 50, deadline_at = now() - interval '1 day',
                           last_error_code = 'deadline_passed'
    WHERE kind = 'do_ecf.advance' AND key = %s
"""
"""A document's advance job dead past its deadline, as a wake more than 15 days late leaves it."""
PARKED: Final = """
    UPDATE do_ecf.jobs SET status = 'parked', parked_reason = 'certificate', attempts = 3
    WHERE kind = 'do_ecf.advance' AND key = %s
"""
COUNT: Final = TypeAdapter(int)
LOCK_WAITS: Final = (
    "SELECT count(*) FROM pg_stat_activity WHERE wait_event_type = 'Lock' AND datname = current_database()"
)
CREDIT: Final = EcfType.CREDIT_INVOICE
CONSUMER: Final = EcfType.CONSUMER_INVOICE
CREDIT_NOTE: Final = EcfType.CREDIT_NOTE


class Contexts(Protocol):
    """The conftest's ``contexts``: a channel context as the app makes it."""

    def __call__(
        self,
        *,
        settings: DoEcfSettings,
        bound: Mapping[str, object] | None = None,
        role: AppRole = "api",
        environment: AppEnvironment = "test",
    ) -> ChannelContext: ...


class CrashedError(Exception):
    """The process dying right after the archive kept a document (a test's stand-in)."""


@final
class RecordingArchive(ArchivePort):
    """The app's archive, every ``put_once`` recorded by key.

    ``crash_once``: the next ``put_once`` keeps the bytes, then raises ``CrashedError``. ``stall``: every
    ``put_once`` waits for ever (until cancelled), keeping nothing.
    """

    __slots__ = ("_inner", "crash_once", "kept", "stall")

    def __init__(self, inner: StoreArchive) -> None:
        self._inner = inner
        self.kept: list[str] = []
        self.crash_once = False
        self.stall = False

    @facade
    @override
    async def put_once(
        self, key: str, content: bytes, *, retain_until: date, media_type: str
    ) -> ArchivedObject:
        if self.stall:
            await asyncio.Event().wait()
        stored = await self._inner.put_once(key, content, retain_until=retain_until, media_type=media_type)
        self.kept.append(key)
        if self.crash_once:
            self.crash_once = False
            raise CrashedError
        return stored

    @facade
    @override
    async def get(self, key: str, *, sha256: bytes) -> bytes:
        return await self._inner.get(key, sha256=sha256)


@final
class HeldSigner(SignerPort):
    """``inner``, its first document held until ``release`` (``holding`` once it is)."""

    __slots__ = ("_inner", "_signed", "holding", "release")

    def __init__(self, inner: SignerPort) -> None:
        self._inner = inner
        self._signed = 0
        self.holding = asyncio.Event()
        self.release = asyncio.Event()

    @facade
    @override
    async def sign_xml(self, call: SystemCall, request: XmlToSign) -> Signed:
        self._signed += 1
        if self._signed == 1:
            self.holding.set()
            await self.release.wait()
        return await self._inner.sign_xml(call, request)

    @facade
    @override
    async def sign_seed(self, call: SystemCall, request: SeedToSign) -> Signed:
        raise AssertionError  # issuing signs documents only

    @facade
    @override
    async def certificate_validity(self, call: SystemCall, tenant: Tenant) -> CertificateValidity | None:
        raise AssertionError  # issuing asks the signer nothing first


@pytest.fixture
def test_signer(signer: KeyPairSigner) -> TestSigner:
    """The test's signer port, signing with the session's key: the channel verifies what it signs."""
    return TestSigner(sign=signer.sign)


@pytest.fixture
def recording(archive: StoreArchive) -> RecordingArchive:
    return RecordingArchive(archive)


@pytest.fixture
def ports(ports: dict[str, object], recording: RecordingArchive) -> dict[str, object]:
    return ports | {ARCHIVE_PORT.name: recording}


def signer_of(ports: Mapping[str, object]) -> SignerPort:
    """The signer the channel's ports hold."""
    found = ports[SIGNER_PORT.name]
    assert isinstance(found, SignerPort)
    return found


def channel_of(contexts: Contexts, ports: Mapping[str, object] | None = None) -> DoEcfChannel:
    channel = assemble(contexts(settings=RECEIVED, bound=ports)).channel
    assert isinstance(channel, DoEcfChannel)
    return channel


def a_tenant() -> Tenant:
    return Tenant(OrganizationId(new_id()), CompanyId(new_id()), Mode.TEST)


def draft_of(tenant: Tenant, *lines: Priced, **options: Unpack[DraftOptions]) -> FiscalDraft:
    """A draft of a fresh invoice of ``tenant``: a 31 to ``BUYER`` unless ``options`` say otherwise."""
    return replace(
        fiscal_draft(*lines, **options),
        organization=tenant.organization,
        company=tenant.company,
        document=DocumentRef(new_id()),
    )


def call_of(tenant: Tenant) -> SystemCall:
    return SystemCall(reason="fiscal:issue", scope=tenant)


async def with_range(
    module_database: BoundDatabase,
    tenant: Tenant,
    ecf_type: EcfType = CREDIT,
    *,
    numbers: tuple[int, int] = (1, 100),
    expires_on: date | None = EXPIRES,
) -> None:
    first, last = numbers
    async with module_database.unit(tenant) as unit:
        ranges = bind(unit).ranges
        await ranges.series(Environment.TESTECF, ecf_type)
        authorized = RangeState(
            id=new_id(),
            ecf_type=ecf_type,
            first=first,
            last=last,
            next=first,
            expires_on=expires_on,
            status=RangeStatus.ACTIVE,
        )
        await ranges.add(
            NumberingRange(environment=Environment.TESTECF, range=authorized, created_at=unit.now, version=1)
        )


async def documents_of(module_database: BoundDatabase, tenant: Tenant) -> list[tuple[str, int, str, int]]:
    """``(invoice, sequence, state, version)`` of every document of ``tenant``, by sequence."""
    async with module_database.unit(tenant) as unit:
        rows = await unit.connection.execute(
            select(
                DocumentRecord.invoice_id,
                DocumentRecord.sequence,
                DocumentRecord.state,
                DocumentRecord.version,
            ).order_by(DocumentRecord.sequence)
        )
        return [(str(invoice), sequence, state, version) for invoice, sequence, state, version in rows.all()]


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


async def advance_of(module_database: BoundDatabase, document: UUID) -> tuple[datetime, str]:
    """When the document's ``do_ecf.advance`` job runs, and its status."""
    jobs = CHASSIS.jobs
    async with module_database.unit(None) as unit:
        rows = await unit.connection.execute(
            select(jobs.c.run_at, jobs.c.status).where(
                jobs.c.kind == "do_ecf.advance", jobs.c.key == str(document)
            )
        )
        return RUN.validate_python(tuple(rows.one()))


async def document_row(module_database: BoundDatabase, tenant: Tenant) -> tuple[UUID, datetime]:
    """The id and creation of ``tenant``'s one document."""
    async with module_database.unit(tenant) as unit:
        rows = await unit.connection.execute(select(DocumentRecord.id, DocumentRecord.created_at))
        identifier, created_at = rows.one()
        return identifier, created_at


def encf_of(published: Mapping[str, JsonValue]) -> JsonValue:
    """The e-NCF a published update's details name."""
    details = published["details"]
    assert isinstance(details, dict)
    found = details["do"]
    assert isinstance(found, dict)
    return found["encf"]


def do(update_: StatusUpdate) -> Mapping[str, JsonValue]:
    found = update_.details["do"]
    assert isinstance(found, dict)
    return found


# ── issue: one document per invoice, numbered under its series' lock ───────────


async def test_issue_twice_for_one_invoice_numbers_one_document_once(
    contexts: Contexts, module_database: BoundDatabase
) -> None:
    tenant = a_tenant()
    await with_range(module_database, tenant)
    channel = channel_of(contexts)
    draft = draft_of(tenant)
    first = await channel.issue(call_of(tenant), draft, sign_within=None)
    again = await channel.issue(call_of(tenant), draft, sign_within=None)
    assert first == again
    assert first.signed is False
    status = first.status
    assert (status.document, status.channel, status.environment) == (draft.document, "do-ecf-v1", "testecf")
    assert (status.state, status.status, status.severity, status.version) == (
        "numbered",
        FiscalStatus.PENDING,
        Severity.INFO,
        1,
    )
    assert (status.authority_number, status.messages) == ("E310000000001", ())
    assert do(status) == {
        "encf": "E310000000001",
        "route": "reception",
        "delivery": "not_started",
        "security_code": None,
        "signed_at": None,
        "qr_url": None,
        "emission_mode": None,
        "track_id": None,
        "commercial_approval": None,
    }
    assert await documents_of(module_database, tenant) == [(str(draft.document), 1, "numbered", 1)]
    other = await channel.issue(call_of(tenant), draft_of(tenant), sign_within=None)
    assert other.status.authority_number == "E310000000002"
    updates = await updates_of(module_database, tenant)
    assert [(each["authority_number"], each["version"]) for each in updates] == [
        ("E310000000001", 1),
        ("E310000000002", 1),
    ]


async def test_a_document_issued_without_signing_is_the_advance_job_s_at_once(
    contexts: Contexts, module_database: BoundDatabase, recording: RecordingArchive, test_signer: TestSigner
) -> None:
    tenant = a_tenant()
    await with_range(module_database, tenant)
    await channel_of(contexts).issue(call_of(tenant), draft_of(tenant), sign_within=None)
    document, created_at = await document_row(module_database, tenant)
    assert await advance_of(module_database, document) == (created_at, "ready")
    assert (recording.kept, test_signer.calls) == ([], [])


async def advance_job(module_database: BoundDatabase, document: UUID) -> tuple[str, int, datetime | None]:
    """The status, attempts and deadline of the document's ``do_ecf.advance`` job."""
    jobs = CHASSIS.jobs
    async with module_database.unit(None) as unit:
        rows = await unit.connection.execute(
            select(jobs.c.status, jobs.c.attempts, jobs.c.deadline_at).where(
                jobs.c.kind == "do_ecf.advance", jobs.c.key == str(document)
            )
        )
        return JOB.validate_python(tuple(rows.one()))


async def test_issue_again_gives_an_undecided_document_whose_job_died_or_went_a_fresh_advance_job(
    contexts: Contexts, module_database: BoundDatabase, admin: psycopg.AsyncConnection, clock: ManualClock
) -> None:
    """Task 19's review: a job woken past its deadline dies at once, its document left undecided. fiscal's
    recovery path calls ``issue`` again: that enqueues ``do_ecf.advance`` anew, with a new deadline (never a
    resend: the job's own steps decide what to do with the document)."""
    tenant = a_tenant()
    await with_range(module_database, tenant)
    channel = channel_of(contexts)
    draft = draft_of(tenant)
    await channel.issue(call_of(tenant), draft, sign_within=None)
    document, _ = await document_row(module_database, tenant)
    key = str(document)
    await admin.execute(
        KILLED,
        (key,),
    )
    await channel.issue(call_of(tenant), draft, sign_within=None)
    status, attempts, deadline = await advance_job(module_database, document)
    assert (status, attempts) == ("ready", 0)
    assert deadline is not None
    assert deadline > clock() + timedelta(days=14)
    await admin.execute("DELETE FROM do_ecf.jobs WHERE kind = 'do_ecf.advance' AND key = %s", (key,))
    await channel.issue(call_of(tenant), draft, sign_within=None)
    assert (await advance_job(module_database, document))[:2] == ("ready", 0)
    assert await documents_of(module_database, tenant) == [(str(draft.document), 1, "numbered", 1)]


async def test_issue_again_leaves_a_live_job_and_a_decided_document_s_job_as_they_are(
    contexts: Contexts, module_database: BoundDatabase, admin: psycopg.AsyncConnection
) -> None:
    tenant = a_tenant()
    await with_range(module_database, tenant)
    channel = channel_of(contexts)
    draft = draft_of(tenant)
    await channel.issue(call_of(tenant), draft, sign_within=None)
    document, _ = await document_row(module_database, tenant)
    key = str(document)
    await admin.execute(
        PARKED,
        (key,),
    )
    await channel.issue(call_of(tenant), draft, sign_within=None)
    assert (await advance_job(module_database, document))[:2] == ("parked", 3)
    await admin.execute("UPDATE do_ecf.documents SET state = 'voided' WHERE id = %s", (document,))
    await admin.execute(
        "UPDATE do_ecf.jobs SET status = 'dead', parked_reason = NULL WHERE key = %s",
        (key,),
    )
    await channel.issue(call_of(tenant), draft, sign_within=None)
    assert (await advance_job(module_database, document))[:2] == ("dead", 3)


async def test_issue_within_the_budget_signs_archives_then_records(
    contexts: Contexts,
    module_database: BoundDatabase,
    recording: RecordingArchive,
    clock: ManualClock,
    archive: StoreArchive,
) -> None:
    tenant = a_tenant()
    await with_range(module_database, tenant)
    issued = await channel_of(contexts).issue(call_of(tenant), draft_of(tenant), sign_within=BUDGET)
    assert issued.signed is True
    status = issued.status
    assert (status.state, status.status, status.version) == ("signed", FiscalStatus.PENDING, 2)
    details = do(status)
    assert (details["signed_at"], details["emission_mode"], details["route"]) == (
        clock.now.isoformat(),
        "normal",
        "reception",
    )
    code = details["security_code"]
    assert isinstance(code, str)
    assert len(code) == 6
    qr = details["qr_url"]
    assert isinstance(qr, str)
    assert "consultatimbre?" in qr
    (key,) = recording.kept
    assert key.startswith(f"do-ecf-v1/test/{tenant.organization}/{tenant.company}/ecf/")
    async with module_database.unit(tenant) as unit:
        rows = await unit.connection.execute(
            select(
                DocumentRecord.id,
                DocumentRecord.ecf_key,
                DocumentRecord.ecf_sha256,
                DocumentRecord.created_at,
            )
        )
        document, ecf_key, sha256, created_at = rows.one()
    assert ecf_key == key
    assert sha256 is not None
    assert b"<ECF>" in await archive.get(key, sha256=sha256)
    assert await advance_of(module_database, document) == (created_at + SIGNING_DELAY, "ready")
    updates = await updates_of(module_database, tenant)
    assert [(each["state"], each["version"]) for each in updates] == [("numbered", 1), ("signed", 2)]
    assert [encf_of(each) for each in updates] == ["E310000000001", "E310000000001"]


async def test_a_consumer_invoice_summarized_archives_its_e_cf_and_its_rfce(
    contexts: Contexts, module_database: BoundDatabase, recording: RecordingArchive
) -> None:
    tenant = a_tenant()
    await with_range(module_database, tenant, CONSUMER)
    issued = await channel_of(contexts).issue(
        call_of(tenant), draft_of(tenant, counterparty=None), sign_within=BUDGET
    )
    assert (issued.signed, issued.status.authority_number) == (True, "E320000000001")
    assert do(issued.status)["route"] == "rfce"
    assert [key.split("/")[4] for key in recording.kept] == ["ecf", "rfce"]
    qr = do(issued.status)["qr_url"]
    assert isinstance(qr, str)
    assert "consultatimbrefc?" in qr


async def test_a_signer_slower_than_the_budget_leaves_the_document_numbered_for_the_job(
    contexts: Contexts,
    module_database: BoundDatabase,
    ports: dict[str, object],
    recording: RecordingArchive,
) -> None:
    tenant = a_tenant()
    await with_range(module_database, tenant)
    held = HeldSigner(signer_of(ports))
    channel = channel_of(contexts, ports | {SIGNER_PORT.name: held})
    issued = await channel.issue(call_of(tenant), draft_of(tenant), sign_within=timedelta(milliseconds=50))
    assert (issued.signed, issued.status.state, issued.status.version) == (False, "numbered", 1)
    assert recording.kept == []
    document, created_at = await document_row(module_database, tenant)
    assert await advance_of(module_database, document) == (created_at + SIGNING_DELAY, "ready")


async def test_two_requests_signing_one_document_record_one_signature(
    contexts: Contexts,
    module_database: BoundDatabase,
    ports: dict[str, object],
    recording: RecordingArchive,
    clock: ManualClock,
) -> None:
    """The first to record wins; the other's signature is archived, never referenced (Decision 11)."""
    tenant = a_tenant()
    await with_range(module_database, tenant)
    held = HeldSigner(signer_of(ports))
    channel = channel_of(contexts, ports | {SIGNER_PORT.name: held})
    draft = draft_of(tenant)
    slow = asyncio.create_task(channel.issue(call_of(tenant), draft, sign_within=timedelta(seconds=30)))
    await held.holding.wait()
    clock.advance(timedelta(seconds=1))  # another moment of signing: other bytes, another key
    fast = await channel.issue(call_of(tenant), draft, sign_within=timedelta(seconds=30))
    held.release.set()
    late = await slow
    assert fast.status == late.status
    assert (fast.signed, late.signed, fast.status.version) == (True, True, 2)
    assert len(recording.kept) == len(set(recording.kept)) == 2
    async with module_database.unit(tenant) as unit:
        kept = (await unit.connection.execute(select(DocumentRecord.ecf_key))).scalar_one()
    assert kept == recording.kept[0]  # the fast request's: archived first, recorded first
    assert await documents_of(module_database, tenant) == [(str(draft.document), 1, "signed", 2)]
    updates = await updates_of(module_database, tenant)
    assert [each["version"] for each in updates] == [1, 2]


async def test_a_signer_without_a_certificate_leaves_the_document_numbered(
    contexts: Contexts, module_database: BoundDatabase, ports: dict[str, object]
) -> None:
    tenant = a_tenant()
    await with_range(module_database, tenant)
    channel = channel_of(contexts, ports | {SIGNER_PORT.name: TestSigner(validity=None)})
    issued = await channel.issue(call_of(tenant), draft_of(tenant), sign_within=BUDGET)
    assert (issued.signed, issued.status.state) == (False, "numbered")


async def test_a_draft_with_objections_is_never_numbered(
    contexts: Contexts, module_database: BoundDatabase
) -> None:
    tenant = a_tenant()
    await with_range(module_database, tenant)
    channel = channel_of(contexts)
    precise = Priced(unit_price=Decimal("10.00"), quantity=Decimal("1.005"))
    with pytest.raises(DraftObjectedError) as caught:
        await channel.issue(call_of(tenant), draft_of(tenant, precise), sign_within=None)
    assert [each.code for each in caught.value.objections] == ["do_ecf_quantity_precision"]
    later = await channel.issue(call_of(tenant), draft_of(tenant), sign_within=None)
    assert later.status.authority_number == "E310000000001"  # the number was never taken


async def test_a_type_without_a_number_to_give_numbers_nothing(
    contexts: Contexts, module_database: BoundDatabase
) -> None:
    tenant = a_tenant()
    with pytest.raises(NoNumberAvailableError) as refused:
        await channel_of(contexts).issue(call_of(tenant), draft_of(tenant), sign_within=None)
    assert isinstance(refused.value, NumberUnavailableError)  # fiscal parks the routing on the kit's error
    assert await documents_of(module_database, tenant) == []


async def test_a_call_about_another_tenant_or_an_environment_of_the_other_mode_is_refused(
    contexts: Contexts,
) -> None:
    tenant = a_tenant()
    channel = channel_of(contexts)
    with pytest.raises(InvalidValueError, match=r"call\.scope"):
        await channel.issue(call_of(a_tenant()), draft_of(tenant), sign_within=None)
    with pytest.raises(InvalidValueError, match=r"call\.scope"):
        await channel.check(call_of(a_tenant()), draft_of(tenant))
    with pytest.raises(InvalidValueError, match=r"draft\.environment"):
        await channel.issue(call_of(tenant), replace(draft_of(tenant), environment="ecf"), sign_within=None)
    with pytest.raises(InvalidValueError, match=r"draft\.environment"):
        await channel.check(call_of(tenant), replace(draft_of(tenant), environment="production"))


# ── check: objections, nothing written ───────────────────────────────────────


async def test_check_objects_to_a_type_with_no_number_to_give_now(
    contexts: Contexts, module_database: BoundDatabase
) -> None:
    tenant = a_tenant()
    channel = channel_of(contexts)
    draft = draft_of(tenant)
    (missing,) = await channel.check(call_of(tenant), draft)
    assert (missing.code, missing.field) == ("do_ecf_range_missing", None)
    assert "register a DGII-authorized range" in missing.message
    await with_range(module_database, tenant, numbers=(1, 1))
    assert await channel.check(call_of(tenant), draft) == ()
    await channel.issue(call_of(tenant), draft, sign_within=None)
    (spent,) = await channel.check(call_of(tenant), draft_of(tenant))
    assert spent.code == "do_ecf_range_missing"
    assert await documents_of(module_database, tenant) == [(str(draft.document), 1, "numbered", 1)]


async def test_check_returns_the_draft_s_objections_and_the_missing_range_together(
    contexts: Contexts,
) -> None:
    tenant = a_tenant()
    precise = Priced(unit_price=Decimal("10.00"), quantity=Decimal("1.005"))
    found = await channel_of(contexts).check(call_of(tenant), draft_of(tenant, precise))
    assert [each.code for each in found] == ["do_ecf_quantity_precision", "do_ecf_range_missing"]


async def test_a_purchase_without_its_type_is_objected_and_no_range_is_asked_for(contexts: Contexts) -> None:
    tenant = a_tenant()
    found = await channel_of(contexts).check(call_of(tenant), draft_of(tenant, direction=Direction.PURCHASE))
    assert [each.code for each in found] == ["do_ecf_ecf_type_required"]


def credit_note(tenant: Tenant, corrects: DocumentRef) -> FiscalDraft:
    return draft_of(
        tenant,
        kind=DocumentKind.CREDIT_NOTE,
        references=(FiscalReference(document=corrects, issue_date=date(2026, 10, 13)),),
        fields={"modification_code": "3"},
    )


async def test_a_credit_note_needs_an_accepted_e_cf_of_its_invoice_here(
    contexts: Contexts, module_database: BoundDatabase
) -> None:
    tenant = a_tenant()
    await with_range(module_database, tenant)
    await with_range(module_database, tenant, CREDIT_NOTE, expires_on=None)
    channel = channel_of(contexts)
    invoice = draft_of(tenant)
    (unresolved,) = await channel.check(call_of(tenant), credit_note(tenant, invoice.document))
    assert (unresolved.code, unresolved.field) == ("do_ecf_reference_unresolved", "corrects")
    await channel.issue(call_of(tenant), invoice, sign_within=BUDGET)
    (signed_only,) = await channel.check(call_of(tenant), credit_note(tenant, invoice.document))
    assert signed_only.code == "do_ecf_reference_unresolved"  # signed is not accepted
    async with module_database.unit(tenant) as unit:
        accepted = {"state": "accepted", "submission_started_at": unit.now, "number_fate": "used"}
        await unit.connection.execute(update(DocumentRecord).values(**accepted))
    note = credit_note(tenant, invoice.document)
    assert await channel.check(call_of(tenant), note) == ()
    issued = await channel.issue(call_of(tenant), note, sign_within=BUDGET)
    assert (issued.signed, issued.status.authority_number) == (True, "E340000000001")


# ── the do_ecf.advance job: what Task 18 builds of it ───────────────────────────


def job_of(tenant: Tenant, document: UUID) -> Job:
    return Job(
        kind="do_ecf.advance",
        key=str(document),
        scope=tenant,
        payload={},
        attempts=1,
        max_attempts=50,
        deadline_at=None,
        lease_token=new_id(),
        traceparent=None,
    )


def advance_spec(contexts: Contexts, ports: Mapping[str, object]) -> JobSpec:
    """The channel's ``do_ecf.advance`` job, whatever other kinds it assembles."""
    (spec,) = [
        each
        for each in assemble(contexts(settings=RECEIVED, bound=ports)).jobs
        if each.kind == "do_ecf.advance"
    ]
    return spec


async def advanced(
    contexts: Contexts, ports: Mapping[str, object], tenant: Tenant, document: UUID
) -> JobOutcome:
    return await advance_spec(contexts, ports).handler(job_of(tenant, document))


async def test_the_job_signs_a_numbered_document_then_runs_again_at_once_to_submit_it(
    contexts: Contexts,
    module_database: BoundDatabase,
    ports: dict[str, object],
    recording: RecordingArchive,
) -> None:
    tenant = a_tenant()
    await with_range(module_database, tenant)
    await channel_of(contexts).issue(call_of(tenant), draft_of(tenant), sign_within=None)
    document, _ = await document_row(module_database, tenant)
    assert isinstance(await advanced(contexts, ports, tenant, document), RunAgain)
    assert len(recording.kept) == 1
    assert [row[2:] for row in await documents_of(module_database, tenant)] == [("signed", 2)]
    with pytest.raises(OutboundClosedError):  # the api has no DGII client: only the worker runs jobs
        await advanced(contexts, ports, tenant, document)
    assert len(recording.kept) == 1  # signed once
    updates = await updates_of(module_database, tenant)
    assert [each["version"] for each in updates] == [1, 2]


async def test_the_job_parks_a_document_whose_company_cannot_sign(
    contexts: Contexts, module_database: BoundDatabase, ports: dict[str, object]
) -> None:
    tenant = a_tenant()
    await with_range(module_database, tenant)
    await channel_of(contexts).issue(call_of(tenant), draft_of(tenant), sign_within=None)
    document, _ = await document_row(module_database, tenant)
    unable = ports | {SIGNER_PORT.name: TestSigner(validity=None)}
    assert await advanced(contexts, unable, tenant, document) == Park("certificate")
    assert [row[2:] for row in await documents_of(module_database, tenant)] == [("numbered", 1)]


async def test_the_job_blocks_a_document_whose_range_ended_before_it_was_signed(
    contexts: Contexts, module_database: BoundDatabase, ports: dict[str, object], clock: ManualClock
) -> None:
    tenant = a_tenant()
    await with_range(module_database, tenant, expires_on=local_date(clock.now))  # its last day: today
    await channel_of(contexts).issue(call_of(tenant), draft_of(tenant), sign_within=None)
    document, _ = await document_row(module_database, tenant)
    clock.advance(timedelta(days=1))
    assert await advanced(contexts, ports, tenant, document) == Park("blocked")
    assert [row[2:] for row in await documents_of(module_database, tenant)] == [("blocked", 2)]
    (_, blocked) = await updates_of(module_database, tenant)
    assert (blocked["state"], blocked["status"], blocked["severity"]) == ("blocked", "pending", "error")
    assert await advanced(contexts, ports, tenant, document) == Park("blocked")


async def test_the_job_of_a_document_that_is_not_there_is_a_defect(
    contexts: Contexts, ports: dict[str, object]
) -> None:
    with (
        capture_logs() as logged,
        pytest.raises(DocumentMissingError, match="no such e-CF document") as caught,
    ):
        await advanced(contexts, ports, a_tenant(), new_id())
    assert caught.value.kind == "conflict"  # the job is dead at once: no retry finds it
    assert [(line["event"], line["log_level"]) for line in logged] == [("do_ecf.signing_failed", "error")]
    spec = advance_spec(contexts, ports)
    unscoped = replace(
        job_of(a_tenant(), new_id()), scope=Tenant(OrganizationId(new_id()), CompanyId(new_id()), None)
    )
    with pytest.raises(InvalidValueError, match=r"do_ecf\.advance\.scope"):
        await spec.handler(unscoped)


# ── review: races, refusals, crashes ─────────────────────────────────────────


async def waiting_for_locks(admin: psycopg.AsyncConnection, count: int) -> None:
    """Until ``count`` sessions of the database wait for a lock (the series' row)."""
    async with asyncio.timeout(10):
        while True:
            answer = await admin.execute(LOCK_WAITS)
            row = await answer.fetchone()
            if row is not None and COUNT.validate_python(row[0]) >= count:
                return
            await asyncio.sleep(0.02)


async def test_two_first_requests_for_one_invoice_number_one_document(
    contexts: Contexts, module_database: BoundDatabase, admin: psycopg.AsyncConnection
) -> None:
    """Both find no document, both wait for the series: the second's insert meets the first's, rolls back
    (its number given back) and answers the first's document."""
    tenant = a_tenant()
    await with_range(module_database, tenant)
    channel = channel_of(contexts)
    draft = draft_of(tenant)
    holding, release = asyncio.Event(), asyncio.Event()

    async def hold() -> None:
        async with module_database.unit(tenant) as unit:
            await bind(unit).ranges.series(Environment.TESTECF, CREDIT)
            holding.set()
            await release.wait()

    holder = asyncio.create_task(hold())
    await holding.wait()
    racing = asyncio.gather(
        channel.issue(call_of(tenant), draft, sign_within=None),
        channel.issue(call_of(tenant), draft, sign_within=None),
    )
    await waiting_for_locks(admin, 2)
    release.set()
    await holder
    first, second = await racing
    assert first == second
    assert (first.status.authority_number, first.status.version) == ("E310000000001", 1)
    assert await documents_of(module_database, tenant) == [(str(draft.document), 1, "numbered", 1)]
    after = await channel.issue(call_of(tenant), draft_of(tenant), sign_within=None)
    assert after.status.authority_number == "E310000000002"  # the loser's number was given back


@pytest.mark.parametrize(
    "unable",
    [TestSigner(failures=[SignatureRefusedError("xml_document_signed")]), TestSigner()],
    ids=["refused-by-the-signer", "fails-its-own-check"],
)
async def test_the_job_blocks_a_document_that_cannot_be_signed_and_says_so(
    contexts: Contexts, module_database: BoundDatabase, ports: dict[str, object], unable: TestSigner
) -> None:
    """A refusal no retry mends (the signer's, or the channel's own check of what came back) blocks it."""
    tenant = a_tenant()
    await with_range(module_database, tenant)
    await channel_of(contexts).issue(call_of(tenant), draft_of(tenant), sign_within=None)
    document, _ = await document_row(module_database, tenant)
    with capture_logs() as logged:
        assert await advanced(contexts, ports | {SIGNER_PORT.name: unable}, tenant, document) == Park(
            "blocked"
        )
    assert [row[2:] for row in await documents_of(module_database, tenant)] == [("blocked", 2)]
    assert [(line["event"], line["log_level"]) for line in logged] == [("do_ecf.signing_failed", "error")]


async def test_a_request_whose_signature_fails_the_channel_s_check_leaves_it_numbered_and_says_so(
    contexts: Contexts, module_database: BoundDatabase, ports: dict[str, object]
) -> None:
    tenant = a_tenant()
    await with_range(module_database, tenant)
    channel = channel_of(contexts, ports | {SIGNER_PORT.name: TestSigner()})  # signs nothing
    with capture_logs() as logged:
        issued = await channel.issue(call_of(tenant), draft_of(tenant), sign_within=BUDGET)
    assert (issued.signed, issued.status.state) == (False, "numbered")
    assert [(line["event"], line["log_level"], line["reason"]) for line in logged] == [
        ("do_ecf.signing_failed", "error", "xml_schema_invalid")
    ]


async def test_a_request_the_signer_cannot_serve_now_leaves_it_to_the_job(
    contexts: Contexts, module_database: BoundDatabase, ports: dict[str, object]
) -> None:
    tenant = a_tenant()
    await with_range(module_database, tenant)
    channel = channel_of(contexts, ports | {SIGNER_PORT.name: TestSigner(validity=None)})
    with capture_logs() as logged:
        issued = await channel.issue(call_of(tenant), draft_of(tenant), sign_within=BUDGET)
    assert issued.status.state == "numbered"
    assert [(line["event"], line["log_level"], line["reason"]) for line in logged] == [
        ("do_ecf.signing_deferred", "warning", "do_ecf_certificate_missing")
    ]


async def test_a_crash_between_archiving_and_recording_leaves_an_orphan_and_the_job_signs_again(
    contexts: Contexts, module_database: BoundDatabase, ports: dict[str, object], clock: ManualClock
) -> None:
    tenant = a_tenant()
    await with_range(module_database, tenant)
    recording = ports[ARCHIVE_PORT.name]
    assert isinstance(recording, RecordingArchive)
    recording.crash_once = True
    with pytest.raises(CrashedError):
        await channel_of(contexts).issue(call_of(tenant), draft_of(tenant), sign_within=BUDGET)
    assert [row[2:] for row in await documents_of(module_database, tenant)] == [("numbered", 1)]
    clock.advance(timedelta(seconds=5))  # the job's run: another moment of signing, other bytes
    document, _ = await document_row(module_database, tenant)
    assert isinstance(await advanced(contexts, ports, tenant, document), RunAgain)
    orphan, kept = recording.kept
    async with module_database.unit(tenant) as unit:
        referenced = (await unit.connection.execute(select(DocumentRecord.ecf_key))).scalar_one()
    assert (referenced, orphan != kept) == (kept, True)


async def test_the_job_signs_and_archives_a_consumer_invoice_summarized(
    contexts: Contexts, module_database: BoundDatabase, ports: dict[str, object], recording: RecordingArchive
) -> None:
    tenant = a_tenant()
    await with_range(module_database, tenant, CONSUMER)
    await channel_of(contexts).issue(call_of(tenant), draft_of(tenant, counterparty=None), sign_within=None)
    document, _ = await document_row(module_database, tenant)
    assert isinstance(await advanced(contexts, ports, tenant, document), RunAgain)
    assert [key.split("/")[4] for key in recording.kept] == ["ecf", "rfce"]
    async with module_database.unit(tenant) as unit:
        rfce = (await unit.connection.execute(select(DocumentRecord.rfce_key))).scalar_one()
    assert rfce == recording.kept[1]


async def test_a_budget_that_runs_out_while_archiving_leaves_the_document_numbered(
    contexts: Contexts, module_database: BoundDatabase, recording: RecordingArchive, test_signer: TestSigner
) -> None:
    tenant = a_tenant()
    await with_range(module_database, tenant)
    recording.stall = True
    with capture_logs() as logged:
        issued = await channel_of(contexts).issue(
            call_of(tenant), draft_of(tenant), sign_within=timedelta(milliseconds=50)
        )
    assert (issued.signed, issued.status.state, recording.kept) == (False, "numbered", [])
    assert len(test_signer.calls) == 1  # signed, never kept: the put was cut short
    assert [(line["event"], line["reason"]) for line in logged] == [("do_ecf.signing_deferred", "timed_out")]
