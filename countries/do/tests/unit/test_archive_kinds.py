"""What the channel archives: ``ArchiveKind`` (fiscal-kit's key words) and how (``PortArchive``)."""

import hashlib
from datetime import UTC, date, datetime
from typing import Final
from uuid import UUID

import pytest

from ledger_country_do.channels.ecf_v1.adapters.archive import (
    ArchiveKind,
    ArchiveNotBoundError,
    PortArchive,
    UnboundArchive,
)
from ledger_country_do.channels.ecf_v1.application.ports import Archived, ArchiveUnavailableError
from ledger_fiscal_kit.ports import archive_key
from ledger_kernel.ids import CompanyId, OrganizationId
from ledger_kernel.tenancy import Mode, Tenant
from ledger_platform.blobs.archive import ArchiveDigestMismatchError
from ledger_platform.runtime.archiving import StoreArchive
from ledger_testing.blobs import MemoryImmutableStore
from ledger_testing.clock import ManualClock

TENANT: Final = Tenant(OrganizationId(UUID(int=1)), CompanyId(UUID(int=2)), Mode.LIVE)


def test_the_channel_archives_these_kinds() -> None:
    assert list(ArchiveKind) == [
        ArchiveKind.ECF,
        ArchiveKind.RFCE,
        ArchiveKind.ANECF,
        ArchiveKind.ACECF,
        ArchiveKind.ARECF,
        ArchiveKind.ARECF_REFUSED,
        ArchiveKind.RECEIVED,
        ArchiveKind.REPRESENTATION,
    ]
    assert [kind.value for kind in ArchiveKind] == [
        "ecf",
        "rfce",
        "anecf",
        "acecf",
        "arecf",
        "arecf-refused",
        "received",
        "representation",
    ]


@pytest.mark.parametrize("kind", list(ArchiveKind))
def test_each_kind_is_a_path_segment_of_an_archive_key(kind: ArchiveKind) -> None:
    key = archive_key(channel="do-ecf-v1", tenant=TENANT, kind=kind, content=b"<ECF/>", extension="xml")
    assert key.split("/")[4] == kind.value


async def test_a_document_is_kept_once_under_its_key_for_ten_years_from_the_day_kept() -> None:
    store = MemoryImmutableStore()
    clock = ManualClock(datetime(2026, 10, 20, 15, tzinfo=UTC))
    archive = PortArchive(StoreArchive(store, retention_years=1, clock=clock), clock=clock)
    kept = await archive.ecf(TENANT, b"<ECF/>")
    assert kept == await archive.ecf(TENANT, b"<ECF/>")
    assert kept.key == archive_key(
        channel="do-ecf-v1", tenant=TENANT, kind=ArchiveKind.ECF, content=b"<ECF/>", extension="xml"
    )
    assert kept.sha256 == hashlib.sha256(b"<ECF/>").digest()
    summary = await archive.rfce(TENANT, b"<RFCE/>")
    assert summary.key.split("/")[4] == "rfce"
    assert await store.retained_until(kept.key) == date(2036, 10, 20)


async def test_an_archive_out_of_reach_is_the_channel_s_unavailable() -> None:
    clock = ManualClock()
    archive = PortArchive(
        StoreArchive(MemoryImmutableStore(unavailable_for=1), retention_years=10, clock=clock), clock=clock
    )
    with pytest.raises(ArchiveUnavailableError) as caught:
        await archive.ecf(TENANT, b"<ECF/>")
    assert caught.value.kind == "unavailable"


async def test_an_app_that_binds_no_archive_keeps_nothing() -> None:
    """``ledger-migrate``'s channel: asked to keep or read a document, a defect."""
    for keep in (UnboundArchive().ecf, UnboundArchive().rfce):
        with pytest.raises(ArchiveNotBoundError, match="binds no archive") as caught:
            await keep(TENANT, b"<ECF/>")
        assert caught.value.kind == "internal"
    with pytest.raises(ArchiveNotBoundError):
        await UnboundArchive().read(Archived(key="k", sha256=bytes(32)))
    with pytest.raises(ArchiveNotBoundError):
        await UnboundArchive().buyer_receipt(TENANT, b"<ARECF/>", refused=False)


async def test_a_document_is_read_back_only_as_its_digest_says() -> None:
    """What is sent to DGII is what was kept (Decision 12): other bytes are the archive's defect."""
    clock = ManualClock()
    archive = PortArchive(StoreArchive(MemoryImmutableStore(), retention_years=10, clock=clock), clock=clock)
    kept = await archive.ecf(TENANT, b"<ECF/>")
    assert await archive.read(kept) == b"<ECF/>"
    with pytest.raises(ArchiveDigestMismatchError):
        await archive.read(Archived(key=kept.key, sha256=hashlib.sha256(b"<ECF>other</ECF>").digest()))


async def test_an_archive_out_of_reach_reads_nothing_and_says_so() -> None:
    clock = ManualClock()
    store = StoreArchive(MemoryImmutableStore(unavailable_for=1), retention_years=10, clock=clock)
    with pytest.raises(ArchiveUnavailableError):
        await PortArchive(store, clock=clock).read(Archived(key="k", sha256=bytes(32)))
