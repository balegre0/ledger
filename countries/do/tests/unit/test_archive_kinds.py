"""``ArchiveKind``: what the channel archives, each a word fiscal-kit's archive keys accept."""

from typing import Final
from uuid import UUID

import pytest

from ledger_country_do.channels.ecf_v1.adapters.archive import ArchiveKind
from ledger_fiscal_kit.ports import archive_key
from ledger_kernel.ids import CompanyId, OrganizationId
from ledger_kernel.tenancy import Mode, Tenant

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
