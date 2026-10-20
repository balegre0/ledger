"""The kit's events: what every channel publishes, scoped to a company in a mode, read back as published."""

from dataclasses import replace
from datetime import UTC, datetime
from typing import Final
from uuid import UUID

import pytest
from pydantic import TypeAdapter

from ledger_fiscal_kit.channels import StatusUpdate
from ledger_fiscal_kit.drafts import DocumentRef
from ledger_fiscal_kit.events import DocumentUpdated, ReceivedDocumentRecorded, RequirementsChanged
from ledger_fiscal_kit.status import MAX_MESSAGES, AuthorityMessage, FiscalStatus, Severity
from ledger_kernel.errors import InvalidValueError
from ledger_kernel.ids import CompanyId, OrganizationId
from ledger_kernel.tax_ids import TaxId, TaxIdScheme
from ledger_kernel.tenancy import Mode, OrganizationScope, Tenant
from ledger_platform.data.codec import JsonValue, canonical_json, payload_of
from ledger_platform.events.consumers import RawEvent
lazy from ledger_kernel.events import Event

TENANT: Final = Tenant(
    OrganizationId(UUID("0199e3a0-0000-7000-8000-000000000001")),
    CompanyId(UUID("0199e3a0-0000-7000-8000-000000000002")),
    Mode.TEST,
)
DOCUMENT: Final = DocumentRef(UUID("0199e3a0-0000-7000-8000-000000000003"))
RECEIVED: Final = UUID("0199e3a0-0000-7000-8000-000000000004")
OCCURRED_AT: Final = datetime(2026, 10, 19, 14, 30, tzinfo=UTC)
PAYLOAD: Final = TypeAdapter(dict[str, JsonValue])
UPDATE: Final = StatusUpdate(
    document=DOCUMENT,
    channel="do-ecf-v1",
    environment="testecf",
    state="accepted_conditionally",
    status=FiscalStatus.ACCEPTED_WITH_OBSERVATIONS,
    severity=Severity.WARNING,
    version=5,
    authority_number="E310000000001",
    messages=(
        AuthorityMessage(code="613", message="La sumatoria de ITBIS excede la tolerancia", lang="es"),
        AuthorityMessage(code="0", message="Aceptado condicional", lang="es"),
    ),
    details={
        "do": {"encf": "E310000000001", "security_code": "A1b2C3", "amounts": ["1180.00", 2, True, None]}
    },
    occurred_at=OCCURRED_AT,
)


ISSUER: Final = TaxId(TaxIdScheme("do.rnc"), "131880681")
RECORDED: Final = ReceivedDocumentRecorded(
    scope=TENANT,
    subject=RECEIVED,
    aggregate_version=None,
    received=RECEIVED,
    channel="do-ecf-v1",
    environment="testecf",
    issuer=ISSUER,
    number="E310000000077",
)


def raw(event: Event) -> RawEvent:
    """``event`` as a consumer receives it: its payload through the codec's bytes and back."""
    return RawEvent(
        id=UUID("0199e3a0-0000-7000-8000-0000000000ee"),
        type=event.TYPE,
        version=event.VERSION,
        source="do-ecf-v1",
        scope=event.scope,
        subject=event.subject,
        aggregate_version=event.aggregate_version or 0,
        occurred_at=OCCURRED_AT,
        traceparent=None,
        payload=PAYLOAD.validate_json(canonical_json(payload_of(event))),
    )


def test_the_types_and_versions_are_the_kit_s() -> None:
    assert (DocumentUpdated.TYPE, DocumentUpdated.VERSION) == ("fiscal_channel.document.updated", 1)
    assert (ReceivedDocumentRecorded.TYPE, ReceivedDocumentRecorded.VERSION) == (
        "fiscal_channel.received_document.recorded",
        1,
    )
    assert (RequirementsChanged.TYPE, RequirementsChanged.VERSION) == (
        "fiscal_channel.requirements.changed",
        1,
    )


def test_a_document_update_is_about_its_document_at_its_version() -> None:
    event = DocumentUpdated.of(TENANT, UPDATE)
    assert (event.scope, event.subject, event.aggregate_version, event.update) == (
        TENANT,
        DOCUMENT,
        5,
        UPDATE,
    )


def test_a_document_update_reads_back_as_it_was_published() -> None:
    event = DocumentUpdated.of(TENANT, UPDATE)
    assert DocumentUpdated.read(raw(event)) == event
    plain = replace(UPDATE, authority_number=None, messages=(), details={})
    assert DocumentUpdated.read(raw(DocumentUpdated.of(TENANT, plain))).update == plain


def test_a_document_update_with_more_than_the_kept_messages_is_refused() -> None:
    message = AuthorityMessage(code="613", message="x", lang="es")
    with pytest.raises(InvalidValueError, match=r"status_update.messages"):
        DocumentUpdated.of(TENANT, replace(UPDATE, messages=(message,) * (MAX_MESSAGES + 1)))


def test_a_document_update_agrees_with_its_envelope() -> None:
    event = DocumentUpdated.of(TENANT, UPDATE)
    with pytest.raises(InvalidValueError, match=r"document_updated.subject must be the update's document"):
        replace(event, subject=RECEIVED)
    with pytest.raises(
        InvalidValueError, match=r"document_updated.aggregate_version must be the update's version"
    ):
        replace(event, aggregate_version=4)


def test_another_event_is_not_read_as_a_document_update() -> None:
    changed = RequirementsChanged.of(TENANT, channel="do-ecf-v1")
    with pytest.raises(
        InvalidValueError, match=r"raw_event.type must be fiscal_channel.document.updated version 1"
    ):
        DocumentUpdated.read(raw(changed))
    newer = replace(raw(DocumentUpdated.of(TENANT, UPDATE)), version=2)
    with pytest.raises(InvalidValueError, match=r"raw_event.type"):
        DocumentUpdated.read(newer)


def test_a_payload_that_is_not_an_update_is_refused() -> None:
    broken = replace(raw(DocumentUpdated.of(TENANT, UPDATE)), payload={"update": "accepted"})
    with pytest.raises(
        InvalidValueError, match=r"raw_event.payload must be a fiscal_channel.document.updated"
    ):
        DocumentUpdated.read(broken)


@pytest.mark.parametrize(
    "scope",
    [
        None,
        Tenant(TENANT.organization, TENANT.company, None),
        OrganizationScope(TENANT.organization, Mode.TEST),
    ],
    ids=["none", "no-mode", "organization"],
)
def test_the_kit_s_events_are_scoped_to_a_company_in_a_mode(scope: Tenant | OrganizationScope | None) -> None:
    rule = r"\.scope must be a company's tenant in a mode"
    event = DocumentUpdated.of(TENANT, UPDATE)
    with pytest.raises(InvalidValueError, match=rule):
        replace(event, scope=scope)
    with pytest.raises(InvalidValueError, match=rule):
        replace(RequirementsChanged.of(TENANT, channel="do-ecf-v1"), scope=scope)
    with pytest.raises(InvalidValueError, match=rule):
        replace(RECORDED, scope=scope)


def test_a_received_document_is_about_itself() -> None:
    recorded = RECORDED
    assert (recorded.channel, recorded.environment, recorded.issuer, recorded.number) == (
        "do-ecf-v1",
        "testecf",
        ISSUER,
        "E310000000077",
    )
    assert raw(recorded).payload == {
        "received": str(RECEIVED),
        "channel": "do-ecf-v1",
        "environment": "testecf",
        "issuer": {"scheme": "do.rnc", "value": "131880681"},
        "number": "E310000000077",
    }
    with pytest.raises(
        InvalidValueError, match=r"received_document_recorded.subject must be the received document"
    ):
        replace(recorded, subject=DOCUMENT)


def test_a_requirements_change_names_its_channel_and_reads_back() -> None:
    changed = RequirementsChanged.of(TENANT, channel="do-ecf-v1")
    assert (changed.subject, changed.aggregate_version, changed.channel) == (
        TENANT.company,
        None,
        "do-ecf-v1",
    )
    assert RequirementsChanged.read(raw(changed)) == changed
    with pytest.raises(InvalidValueError, match=r"requirements_changed.subject must be the tenant's company"):
        replace(changed, subject=DOCUMENT)
    with pytest.raises(
        InvalidValueError, match=r"raw_event.type must be fiscal_channel.requirements.changed version 1"
    ):
        RequirementsChanged.read(raw(DocumentUpdated.of(TENANT, UPDATE)))
    broken = replace(raw(changed), payload={"channel": 1})
    with pytest.raises(
        InvalidValueError, match=r"raw_event.payload must be a fiscal_channel.requirements.changed"
    ):
        RequirementsChanged.read(broken)
