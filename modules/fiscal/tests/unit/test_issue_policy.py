"""Routing a document (§7.6, Decision 5) and what invoicing's mirror is told (§8.5), without a database.

``routes`` takes every active enrollment of the document's company and mode whose installed channel covers its
kind and direction, ignoring a pending one (``pending``'s), a disabled one, one of the other mode or another
company, and one whose channel is not installed. A channel's status reaches the mirror at its version + 1.
"""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import MappingProxyType
from typing import Final
from uuid import UUID

import pytest

from ledger_fiscal._manifest import MANIFEST
from ledger_fiscal.application.issuing import FiscalPolicy, mirrored, mirrored_version, tenant_of
from ledger_fiscal.application.rendering import FiscalRenderer
from ledger_fiscal.application.routing import pending, routes
from ledger_fiscal.domain.enrollments import Asked, Enrollment, EnrollmentId, EnrollmentStatus
from ledger_fiscal_kit.channels import ChannelRegistry, Requirements, StatusUpdate
from ledger_fiscal_kit.drafts import DocumentRef
from ledger_fiscal_kit.status import AuthorityMessage, FiscalStatus, Severity
from ledger_invoicing.api import (
    AuthorityMessageInfo,
    EstablishmentOfIssue,
    FiscalStatusName,
    InvoiceId,
    InvoiceInfo,
    InvoiceStatus,
)
from ledger_kernel.errors import InvalidValueError
from ledger_kernel.ids import CompanyId, OrganizationId, new_id
from ledger_kernel.money import Currency, Money
from ledger_kernel.regimes import Direction, DocumentKind, RegimeKey, Totals
from ledger_kernel.tenancy import Mode, Tenant
from ledger_platform.runtime.contexts import AssembledChannels
from ledger_platform.runtime.facade import is_facade
lazy from ledger_fiscal_kit.channels import Channel

NOW: Final = datetime(2026, 10, 19, 12, tzinfo=UTC)
PESO: Final = Currency.of("DOP")
TENANT: Final = Tenant(OrganizationId(new_id()), CompanyId(new_id()), Mode.TEST)
FAKE: Final = "do-fake-v1"
NOTHING_DUE: Final = Requirements(currently_due=())


def document(
    kind: DocumentKind = DocumentKind.INVOICE, direction: Direction = Direction.SALES
) -> InvoiceInfo:
    zero = Money(Decimal(0), PESO)
    return InvoiceInfo(
        id=InvoiceId(new_id()),
        tenant=TENANT,
        kind=kind,
        direction=direction,
        status=InvoiceStatus.ISSUED,
        version=1,
        external_reference=None,
        corrects=None,
        regime=RegimeKey("do"),
        establishment=EstablishmentOfIssue(code="001", time_zone="America/Santo_Domingo"),
        currency=PESO,
        exchange_rate=None,
        issue_date=date(2026, 10, 19),
        tax_point=date(2026, 10, 19),
        seller=None,
        buyer=None,
        lines=(),
        totals=Totals(net=zero, categories=(), tax=zero, gross=zero),
        functional_totals=None,
        fields=MappingProxyType({}),
        created_at=NOW,
        updated_at=NOW,
        issued_at=NOW,
        voided_at=None,
        void_reason=None,
    )


def enrollment(
    *,
    tenant: Tenant = TENANT,
    channel: str = FAKE,
    due: Requirements = NOTHING_DUE,
    environment: str = "testecf",
) -> Enrollment:
    asked = Asked(enrollment=EnrollmentId(new_id()), tenant=tenant, channel=channel, environment=environment)
    return Enrollment.enroll(asked, requirements=due, now=NOW)


@pytest.fixture
def channels(fake_channel: Channel) -> ChannelRegistry:
    assembled = AssembledChannels()
    assembled.add(FAKE, fake_channel)
    assembled.seal()
    return ChannelRegistry(assembled)


def test_a_document_routes_to_the_active_enrollments_covering_it_and_a_pending_one_waits(
    channels: ChannelRegistry,
) -> None:
    active = enrollment()
    waiting = enrollment(due=Requirements(currently_due=("certificate",)))
    disabled = enrollment().disabled("gone", now=NOW)
    live = enrollment(tenant=Tenant(TENANT.organization, TENANT.company, Mode.LIVE), environment="ecf")
    sibling = enrollment(tenant=Tenant(TENANT.organization, CompanyId(new_id()), Mode.TEST))
    uninstalled = enrollment(channel="do-gone-v1")
    every = (waiting, disabled, live, sibling, uninstalled, active)
    assert (live.status, waiting.status) == (EnrollmentStatus.PENDING, EnrollmentStatus.PENDING)
    assert routes(document(), every, channels) == (active,)
    assert pending(document(), every, channels) == (waiting,)
    assert routes(document(DocumentKind.CREDIT_NOTE), every, channels) == (active,)
    assert routes(document(direction=Direction.PURCHASE), every, channels) == ()  # the fake covers sales


def test_a_document_of_no_mode_is_about_no_tenant_a_call_could_name() -> None:
    assert tenant_of(document()) == TENANT
    with pytest.raises(InvalidValueError, match=r"document\.tenant"):
        tenant_of(replace(document(), tenant=Tenant(TENANT.organization, TENANT.company, None)))


def test_a_channel_s_status_reaches_the_mirror_one_version_on_its_details_under_the_regime() -> None:
    update = StatusUpdate(
        document=DocumentRef(UUID("0199e3a0-0000-7000-8000-0000000000b1")),
        channel="do-ecf-v1",
        environment="testecf",
        state="accepted_conditionally",
        status=FiscalStatus.ACCEPTED_WITH_OBSERVATIONS,
        severity=Severity.WARNING,
        version=5,
        authority_number="E310000000001",
        messages=(AuthorityMessage(code="1", message="Observación", lang="es"),),
        details={"do": {"encf": "E310000000001"}, "xx": "not ours"},
        occurred_at=NOW,
    )
    status = mirrored(update, "do")
    assert mirrored_version(update) == 6
    assert (status.channel, status.environment, status.status, status.authority_number) == (
        "do-ecf-v1",
        "testecf",
        FiscalStatusName.ACCEPTED_WITH_OBSERVATIONS,
        "E310000000001",
    )
    assert status.messages == (AuthorityMessageInfo(code="1", message="Observación", lang="es"),)
    assert (status.regime, dict(status.details), status.updated_at) == ("do", {"encf": "E310000000001"}, NOW)
    assert dict(mirrored(update, "xx").details) == {}  # details that are no object are not kept


def test_the_consumers_are_declared_as_the_platform_runs_them_and_the_points_are_implemented() -> None:
    """Task 12's notes: outside consumers (style ``outside``, the default 60 s timeout) are never ordered,
    so each is idempotent by the event or a version; ``fiscal.requirements`` runs inside its unit; fiscal
    implements invoicing's two points and depends on invoicing (so it sits above it)."""
    declared = {decl.consumer: decl for decl in MANIFEST.events.consumes}
    assert {
        name: (decl.types, decl.style, decl.ordered, decl.timeout) for name, decl in declared.items()
    } == {
        "fiscal.commercial_documents": (
            ("invoicing.invoice.issued",),
            "outside",
            False,
            timedelta(seconds=60),
        ),
        "fiscal.channel_documents": (
            ("fiscal_channel.document.updated",),
            "outside",
            False,
            timedelta(seconds=60),
        ),
        "fiscal.requirements": (
            ("fiscal_channel.requirements.changed",),
            "inside",
            False,
            timedelta(seconds=60),
        ),
    }
    assert MANIFEST.extension_points.implements == ("invoicing.IssuePolicy", "invoicing.Renderer")
    assert "invoicing" in MANIFEST.dependencies.modules


def test_every_coroutine_another_module_calls_is_a_facade() -> None:
    """invoicing binds an implementation only if each of its coroutines refuses a call made inside a unit."""
    assert all(
        is_facade(method) for method in (FiscalPolicy.check, FiscalPolicy.after_issue, FiscalRenderer.render)
    )
