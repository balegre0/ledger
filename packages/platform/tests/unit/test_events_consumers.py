"""Consumers (§3.5): a declaration merged with an assembly's handler; an inbox row read as an event."""

import uuid
from datetime import UTC, datetime
from typing import Final

import pytest

from ledger_kernel.ids import CompanyId, OrganizationId
from ledger_kernel.tenancy import Mode, Tenant
from ledger_platform.config.manifest import ConsumerDecl
from ledger_platform.events.consumers import (
    Consumer,
    ConsumerMismatchError,
    ConsumerSpec,
    RawEvent,
    raw_event_of,
)
lazy from ledger_platform.data.unit_of_work import UnitOfWork

ORGANIZATION, COMPANY, SUBJECT, EVENT = uuid.uuid7(), uuid.uuid7(), uuid.uuid7(), uuid.uuid7()
OCCURRED: Final = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)


async def ignore(event: RawEvent, unit: UnitOfWork) -> None:
    del event, unit


def test_a_consumer_takes_its_types_attempts_and_ordering_from_its_declaration() -> None:
    declared = ConsumerDecl(
        consumer="other.renames",
        types=("probe.thing.renamed", "probe.thing.created"),
        max_attempts=3,
        ordered=True,
    )
    consumer = Consumer.of(ConsumerSpec(name="other.renames", handler=ignore), declared)
    assert consumer == Consumer(
        name="other.renames",
        types=frozenset({"probe.thing.renamed", "probe.thing.created"}),
        max_attempts=3,
        ordered=True,
        handler=ignore,
    )


def test_a_consumer_the_manifest_does_not_declare_is_refused() -> None:
    declared = ConsumerDecl(consumer="other.renames", types=("probe.thing.renamed",))
    with pytest.raises(
        ConsumerMismatchError, match=r"other\.echo is not the declared other\.renames"
    ) as raised:
        Consumer.of(ConsumerSpec(name="other.echo", handler=ignore), declared)
    assert raised.value.code == "consumer_mismatch"


def test_an_inbox_row_is_read_as_the_event_it_holds() -> None:
    row = {
        "consumer": "other.renames",
        "event_id": EVENT,
        "type": "probe.thing.renamed",
        "version": 1,
        "source": "probe",
        "organization_id": ORGANIZATION,
        "company_id": COMPANY,
        "mode": "test",
        "subject": SUBJECT,
        "aggregate_version": 7,
        "occurred_at": OCCURRED,
        "traceparent": None,
        "payload": {"name": "x", "tags": ["a"], "count": 2, "gone": None},
        "status": "running",
        "attempts": 1,
    }
    assert raw_event_of(row) == RawEvent(
        id=EVENT,
        type="probe.thing.renamed",
        version=1,
        source="probe",
        scope=Tenant(OrganizationId(ORGANIZATION), CompanyId(COMPANY), Mode.TEST),
        subject=SUBJECT,
        aggregate_version=7,
        occurred_at=OCCURRED,
        traceparent=None,
        payload={"name": "x", "tags": ["a"], "count": 2, "gone": None},
    )
