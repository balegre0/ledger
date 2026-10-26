"""``/v1/do/deliveries/{invoice}/retry`` and ``/v1/do/delivery_options`` through the ``api`` app (§8.6.5).

A delivery that failed is started again by the client (``failed`` to ``delivering``, the job enqueued, the
change audited and published); anything else is a 409. The company's delivery options are read and set (a
set-value ``PUT``, audited when it changes something): buyer authentication off by default (Decision 19).
"""

from dataclasses import dataclass, replace
from datetime import date
from typing import Final, Protocol, final
from uuid import UUID
lazy from collections.abc import Mapping

import pytest
from pydantic import TypeAdapter
from sqlalchemy import update
lazy import httpx
lazy import psycopg

from ledger_country_do.channels.ecf_v1.adapters.channel import DoEcfChannel
from ledger_country_do.channels.ecf_v1.adapters.postgres.tables import DocumentRecord
from ledger_country_do.channels.ecf_v1.adapters.postgres.unit import bind
from ledger_country_do.channels.ecf_v1.adapters.routes.models import invoice_ref
from ledger_country_do.channels.ecf_v1.application.ranges import NumberingRange
from ledger_country_do.channels.ecf_v1.assembly import assemble
from ledger_country_do.channels.ecf_v1.domain.encf import EcfType
from ledger_country_do.channels.ecf_v1.domain.environments import Environment
from ledger_country_do.channels.ecf_v1.domain.numbering import RangeState, RangeStatus
from ledger_country_do.channels.ecf_v1.settings import DoEcfSettings
from ledger_country_do_testing.fiscal import fiscal_draft
from ledger_fiscal_kit.drafts import DocumentRef
from ledger_kernel.ids import CompanyId, OrganizationId, new_id, parse_id
from ledger_kernel.tenancy import Mode, Tenant
from ledger_platform.runtime.calls import SystemCall
from ledger_testing.api import document
lazy from ledger_platform.config.manifest import AppRole
lazy from ledger_platform.config.settings import Environment as AppEnvironment
lazy from ledger_platform.data.bound import BoundDatabase
lazy from ledger_platform.http.outbound import OutboundHttp
lazy from ledger_platform.runtime.contexts import ChannelContext
lazy from ledger_testing.api import TestPrincipal
lazy from ledger_testing.issuer import LocalIssuer

RETRY: Final = "/v1/do/deliveries/{}/retry"
OPTIONS: Final = "/v1/do/delivery_options"
SETTINGS: Final = DoEcfSettings(receiver_public_url="https://receiver.ledger.test")
ROWS: Final = TypeAdapter(list[tuple[str, str]])
ONE: Final = TypeAdapter(tuple[str, str])
IDENTIFIER: Final = TypeAdapter(UUID)
NO_JOB: Final = (
    "DELETE FROM do_ecf.jobs WHERE kind = 'do_ecf.deliver' AND key = "
    "(SELECT id::text FROM do_ecf.documents WHERE invoice_id = %s)"
)
A_JOB: Final = (
    "INSERT INTO do_ecf.jobs (kind, key, organization_id, company_id, mode, status, run_at, max_attempts, "
    "payload, created_at, updated_at) SELECT 'do_ecf.deliver', id::text, organization_id, company_id, "
    "mode, %s, now(), 50, '{}'::jsonb, now(), now() FROM do_ecf.documents WHERE invoice_id = %s"
)
ANNOUNCED: Final = (
    "SELECT payload #>> '{update,details,do,delivery}', payload #>> '{update,version}' FROM do_ecf.outbox "
    "WHERE type = 'fiscal_channel.document.updated' ORDER BY aggregate_version DESC"
)


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


def posting(principal: TestPrincipal, company: str, key: str, mode: str = "test") -> dict[str, str]:
    return principal.posting(key, mode) | {"Ledger-Company": company}


def managing(principal: TestPrincipal, company: str, mode: str = "test") -> dict[str, str]:
    return principal.bearer() | {"Ledger-Company": company, "Ledger-Mode": mode}


def code_of(response: httpx.Response) -> tuple[int, object]:
    return response.status_code, document(response)["code"]


async def tenant_of(admin: psycopg.AsyncConnection, company: str) -> Tenant:
    """The company (``cmp_…``) as the channel's units see it, in test mode."""
    identifier = parse_id("cmp", company)
    found = await admin.execute("SELECT organization_id FROM identity.companies WHERE id = %s", (identifier,))
    row = await found.fetchone()
    assert row is not None
    return Tenant(OrganizationId(IDENTIFIER.validate_python(row[0])), CompanyId(identifier), Mode.TEST)


@final
@dataclass(frozen=True, slots=True)
class Failing:
    """Makes invoices whose e-CF's delivery failed, over the channel as the api assembles it."""

    contexts: Contexts
    database: BoundDatabase

    async def failed(self, tenant: Tenant) -> DocumentRef:
        return await failed_delivery(self.contexts, self.database, tenant)


@pytest.fixture
def failing(contexts: Contexts, module_database: BoundDatabase) -> Failing:
    return Failing(contexts, module_database)


async def failed_delivery(contexts: Contexts, module_database: BoundDatabase, tenant: Tenant) -> DocumentRef:
    """An invoice of ``tenant`` whose e-CF's delivery failed: its reference."""
    async with module_database.unit(tenant) as unit:
        ranges = bind(unit).ranges
        await ranges.series(Environment.TESTECF, EcfType.CREDIT_INVOICE)
        authorized = RangeState(
            id=new_id(),
            ecf_type=EcfType.CREDIT_INVOICE,
            first=1,
            last=100,
            next=1,
            expires_on=date(2028, 12, 31),
            status=RangeStatus.ACTIVE,
        )
        await ranges.add(
            NumberingRange(environment=Environment.TESTECF, range=authorized, created_at=unit.now, version=1)
        )
    channel = assemble(contexts(settings=SETTINGS)).channel
    assert isinstance(channel, DoEcfChannel)
    draft = replace(
        fiscal_draft(),
        organization=tenant.organization,
        company=tenant.company,
        document=DocumentRef(new_id()),
    )
    await channel.issue(SystemCall(reason="fiscal:issue", scope=tenant), draft, sign_within=None)
    async with module_database.unit(tenant) as unit:
        await unit.connection.execute(
            update(DocumentRecord)
            .where(DocumentRecord.invoice_id == draft.document)
            .values(delivery_state="failed", delivery_reason="do_ecf_buyer_requires_authentication")
        )
    return draft.document


async def test_a_failed_delivery_is_started_again_audited_announced_and_its_job_enqueued(
    client: httpx.AsyncClient,
    owner: TestPrincipal,
    company: str,
    failing: Failing,
    admin: psycopg.AsyncConnection,
) -> None:
    tenant = await tenant_of(admin, company)
    invoice = await failing.failed(tenant)
    path = RETRY.format(invoice_ref(invoice))
    response = await client.post(path, headers=posting(owner, company, "retry-1"))
    assert response.status_code == 200, response.text
    assert document(response) == {
        "object": "delivery",
        "invoice": invoice_ref(invoice),
        "livemode": False,
        "encf": "E310000000001",
        "delivery": "delivering",
        "reason": None,
        "version": 2,
    }
    jobs = await admin.execute("SELECT kind, status FROM do_ecf.jobs WHERE kind = 'do_ecf.deliver'")
    assert ROWS.validate_python(await jobs.fetchall()) == [("do_ecf.deliver", "ready")]
    audited = await admin.execute(
        "SELECT action, subject_type FROM do_ecf.audit_events WHERE action = 'delivery.retry'"
    )
    assert ROWS.validate_python(await audited.fetchall()) == [("delivery.retry", "delivery")]
    announced = await admin.execute(ANNOUNCED)
    assert ONE.validate_python(await announced.fetchone()) == ("delivering", "2")
    replayed = await client.post(path, headers=posting(owner, company, "retry-1"))
    assert (replayed.status_code, replayed.content, replayed.headers["idempotent-replayed"]) == (
        200,
        response.content,
        "true",
    )
    again = await client.post(path, headers=posting(owner, company, "retry-2"))
    assert code_of(again) == (409, "do_ecf_delivery_not_retryable")


@pytest.mark.parametrize("invoice", ["inv_01h455vb4pex5vsknk084sn02q", "rng_01h455vb4pex5vsknk084sn02q"])
async def test_an_invoice_without_an_e_cf_has_no_delivery_to_retry(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str, invoice: str
) -> None:
    response = await client.post(RETRY.format(invoice), headers=posting(owner, company, f"retry-{invoice}"))
    assert code_of(response) == (404, "do_ecf_delivery_not_found")


async def test_buyer_authentication_is_off_until_the_company_sets_it_once_audited(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str, admin: psycopg.AsyncConnection
) -> None:
    read = await client.get(OPTIONS, headers=managing(owner, company))
    assert (read.status_code, document(read)) == (
        200,
        {"object": "delivery_options", "livemode": False, "buyer_authentication": False},
    )
    for _ in range(2):  # setting it again changes nothing, and audits nothing
        put = await client.put(OPTIONS, json={"buyer_authentication": True}, headers=managing(owner, company))
        assert (put.status_code, document(put)["buyer_authentication"]) == (200, True)
    reread = await client.get(OPTIONS, headers=managing(owner, company))
    assert document(reread)["buyer_authentication"] is True
    live = await client.get(OPTIONS, headers=managing(owner, company, "live"))
    assert document(live) == {"object": "delivery_options", "livemode": True, "buyer_authentication": False}
    audited = await admin.execute(
        "SELECT action, subject_id FROM do_ecf.audit_events WHERE action = 'delivery_options.set'"
    )
    assert ROWS.validate_python(await audited.fetchall()) == [
        ("delivery_options.set", str(parse_id("cmp", company)))
    ]
    unknown = await client.put(
        OPTIONS, json={"buyer_authentication": True, "x": 1}, headers=managing(owner, company)
    )
    assert unknown.status_code == 422


# -- The review's cases (2026-10-25) ------------------------------------------------------------------------


async def deliver_job_set(admin: psycopg.AsyncConnection, invoice: DocumentRef, status: str | None) -> None:
    """The invoice's ``do_ecf.deliver`` job left ``status`` (``None``: there is none)."""
    await admin.execute(NO_JOB, (invoice,))
    if status is None:
        return
    await admin.execute(A_JOB, (status, invoice))


async def delivery_set(admin: psycopg.AsyncConnection, invoice: DocumentRef, state: str) -> None:
    await admin.execute(
        "UPDATE do_ecf.documents SET delivery_state = %s, delivery_since = now() WHERE invoice_id = %s",
        (state, invoice),
    )


@final
@dataclass(frozen=True, slots=True)
class Retrying:
    """The owner retrying deliveries of its company."""

    client: httpx.AsyncClient
    owner: TestPrincipal
    company: str

    async def retry(self, invoice: DocumentRef, key: str) -> httpx.Response:
        headers = posting(self.owner, self.company, key)
        return await self.client.post(RETRY.format(invoice_ref(invoice)), headers=headers)


@pytest.fixture
def retrying(client: httpx.AsyncClient, owner: TestPrincipal, company: str) -> Retrying:
    return Retrying(client, owner, company)


@pytest.mark.parametrize(
    "case",
    [
        ("delivering", "dead", 200),  # its job died: stuck for good otherwise
        ("delivering", None, 200),  # its job is gone (purged): the same
        ("not_started", "done", 200),
        ("delivering", "running", 409),  # a run under way may still finish it
        ("delivering", "ready", 409),
        ("not_started", "parked", 409),
        ("delivered", "done", 409),  # the retry raced the run that finished it
        ("failed", "running", 409),  # a run under way would leave it delivering with no job
        ("not_applicable", None, 409),
    ],
)
async def test_a_stuck_delivery_is_started_again_only_once_its_job_no_longer_runs(
    retrying: Retrying, failing: Failing, admin: psycopg.AsyncConnection, case: tuple[str, str | None, int]
) -> None:
    """``case``: the delivery's state, its job's status (``None``: none), the answer."""
    delivery, job, status = case
    tenant = await tenant_of(admin, retrying.company)
    invoice = await failing.failed(tenant)
    await delivery_set(admin, invoice, delivery)
    await deliver_job_set(admin, invoice, job)
    response = await retrying.retry(invoice, "stuck")
    assert response.status_code == status, response.text
    if status == 200:
        assert document(response)["delivery"] == "delivering"
        jobs = await admin.execute("SELECT status FROM do_ecf.jobs WHERE kind = 'do_ecf.deliver'")
        assert await jobs.fetchall() == [("ready",)]
    else:
        assert document(response)["code"] == "do_ecf_delivery_not_retryable"


async def test_a_retry_forgets_the_receipt_of_the_failed_attempt(
    client: httpx.AsyncClient,
    owner: TestPrincipal,
    company: str,
    failing: Failing,
    admin: psycopg.AsyncConnection,
) -> None:
    tenant = await tenant_of(admin, company)
    invoice = await failing.failed(tenant)
    await admin.execute(
        "UPDATE do_ecf.documents SET receipt_key = 'k', receipt_sha256 = %s WHERE invoice_id = %s",
        (bytes(32), invoice),
    )
    response = await client.post(
        RETRY.format(invoice_ref(invoice)), headers=posting(owner, company, "forget")
    )
    assert response.status_code == 200, response.text
    kept = await admin.execute(
        "SELECT receipt_key, receipt_sha256 FROM do_ecf.documents WHERE invoice_id = %s", (invoice,)
    )
    assert await kept.fetchone() == (None, None)


async def test_a_member_without_the_deliveries_permission_is_refused(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str, local_issuer: LocalIssuer
) -> None:
    viewer = TestPrincipal(local_issuer, subject="3001")
    membership = f"/v1/companies/{company}/memberships/3001"
    assert (await client.put(membership, json={"role": "viewer"}, headers=owner.bearer())).status_code in {
        200,
        201,
    }
    refused = (
        await client.post(
            RETRY.format("inv_01h455vb4pex5vsknk084sn02q"), headers=posting(viewer, company, "v")
        ),
        await client.get(OPTIONS, headers=managing(viewer, company)),
        await client.put(OPTIONS, json={"buyer_authentication": True}, headers=managing(viewer, company)),
    )
    assert [code_of(response) for response in refused] == [(403, "permission_denied")] * 3
