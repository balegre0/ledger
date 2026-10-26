"""``Channel.status`` and ``Channel.representation`` (spec §8.3, §8.6.7, §9.8; Decisions 12, 13, 26).

A document's printed representation is rendered from the signed e-CF the archive keeps (read back against its
SHA-256), archived on its first render with its key and digest recorded on the document, and answered from
the archive after, rendered no more. A document not signed yet has none (409 ``do_ecf_not_signed``).
"""

from dataclasses import replace
from datetime import date, timedelta
from io import BytesIO
from typing import Final, Protocol, Unpack, final, override
lazy from collections.abc import Awaitable, Callable, Mapping

import pytest
from pypdf import PdfReader
from sqlalchemy import select, update

from ledger_country_do.channels.ecf_v1.adapters.channel import DoEcfChannel
from ledger_country_do.channels.ecf_v1.adapters.postgres.tables import DocumentRecord
from ledger_country_do.channels.ecf_v1.adapters.postgres.unit import bind
from ledger_country_do.channels.ecf_v1.application.ranges import NumberingRange
from ledger_country_do.channels.ecf_v1.application.representation import (
    DocumentNotIssuedError,
    NotSignedError,
)
from ledger_country_do.channels.ecf_v1.assembly import assemble
from ledger_country_do.channels.ecf_v1.domain.encf import EcfType
from ledger_country_do.channels.ecf_v1.domain.environments import Environment
from ledger_country_do.channels.ecf_v1.domain.numbering import RangeState, RangeStatus
from ledger_country_do.channels.ecf_v1.settings import DoEcfSettings
from ledger_country_do_testing.fiscal import fiscal_draft
from ledger_fiscal_kit.drafts import DocumentRef
from ledger_fiscal_kit.ports import ARCHIVE_PORT, ArchivePort, archive_key
from ledger_kernel.errors import ConflictError, InvalidValueError, NotFoundError
from ledger_kernel.ids import CompanyId, OrganizationId, new_id
from ledger_kernel.tenancy import Mode, Tenant
from ledger_platform.runtime.calls import SystemCall
from ledger_platform.runtime.facade import facade
from ledger_testing.signer import TestSigner
lazy from ledger_country_do_testing.fiscal import DraftOptions, Priced
lazy from ledger_country_do_testing.local_signer import KeyPairSigner
lazy from ledger_fiscal_kit.channels import StatusUpdate
lazy from ledger_fiscal_kit.drafts import FiscalDraft
lazy from ledger_fiscal_kit.ports import ArchivedObject
lazy from ledger_platform.config.manifest import AppRole
lazy from ledger_platform.data.bound import BoundDatabase
lazy from ledger_platform.runtime.archiving import StoreArchive
lazy from ledger_platform.runtime.contexts import ChannelContext

RECEIVED: Final = DoEcfSettings(receiver_public_url="https://receiver.ledger.test")
BUDGET: Final = timedelta(seconds=1.5)
EXPIRES: Final = date(2028, 12, 31)


class Contexts(Protocol):
    """The conftest's ``contexts``: a channel context as the app makes it."""

    def __call__(
        self, *, settings: DoEcfSettings, bound: Mapping[str, object] | None = None, role: AppRole = "api"
    ) -> ChannelContext: ...


@final
class SpyArchive(ArchivePort):
    """The app's archive, every key kept (and its media type) and read recorded.

    ``after_put``, if set, runs after each put.
    """

    __slots__ = ("_inner", "after_put", "kept", "media_types", "read")

    def __init__(self, inner: StoreArchive) -> None:
        self._inner = inner
        self.kept: list[str] = []
        self.media_types: list[str] = []
        self.read: list[str] = []
        self.after_put: Callable[[], Awaitable[None]] | None = None

    @facade
    @override
    async def put_once(
        self, key: str, content: bytes, *, retain_until: date, media_type: str
    ) -> ArchivedObject:
        stored = await self._inner.put_once(key, content, retain_until=retain_until, media_type=media_type)
        self.kept.append(key)
        self.media_types.append(media_type)
        if self.after_put is not None:
            await self.after_put()
        return stored

    @facade
    @override
    async def get(self, key: str, *, sha256: bytes) -> bytes:
        self.read.append(key)
        return await self._inner.get(key, sha256=sha256)


@pytest.fixture
def test_signer(signer: KeyPairSigner) -> TestSigner:
    """The test's signer port, signing with the session's key: the channel verifies what it signs."""
    return TestSigner(sign=signer.sign)


@pytest.fixture
def spy(archive: StoreArchive) -> SpyArchive:
    return SpyArchive(archive)


@pytest.fixture
def ports(ports: dict[str, object], spy: SpyArchive) -> dict[str, object]:
    return ports | {ARCHIVE_PORT.name: spy}


def channel_of(contexts: Contexts) -> DoEcfChannel:
    channel = assemble(contexts(settings=RECEIVED)).channel
    assert isinstance(channel, DoEcfChannel)
    return channel


def a_tenant() -> Tenant:
    return Tenant(OrganizationId(new_id()), CompanyId(new_id()), Mode.TEST)


def draft_of(tenant: Tenant, *lines: Priced, **options: Unpack[DraftOptions]) -> FiscalDraft:
    return replace(
        fiscal_draft(*lines, **options),
        organization=tenant.organization,
        company=tenant.company,
        document=DocumentRef(new_id()),
    )


def call_of(tenant: Tenant) -> SystemCall:
    return SystemCall(reason="fiscal:render", scope=tenant)


async def with_range(module_database: BoundDatabase, tenant: Tenant, ecf_type: EcfType) -> None:
    async with module_database.unit(tenant) as unit:
        ranges = bind(unit).ranges
        await ranges.series(Environment.TESTECF, ecf_type)
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
            NumberingRange(environment=Environment.TESTECF, range=authorized, created_at=unit.now, version=1)
        )


async def representation_row(module_database: BoundDatabase, tenant: Tenant) -> tuple[object, ...]:
    """``(representation_key, representation_sha256, version, state)`` of ``tenant``'s one document."""
    async with module_database.unit(tenant) as unit:
        rows = await unit.connection.execute(
            select(
                DocumentRecord.representation_key,
                DocumentRecord.representation_sha256,
                DocumentRecord.version,
                DocumentRecord.state,
            )
        )
        return tuple(rows.one())


def do(update_: StatusUpdate) -> Mapping[str, object]:
    found = update_.details["do"]
    assert isinstance(found, dict)
    return found


def text_of(pdf: bytes) -> str:
    return "\n".join(page.extract_text() for page in PdfReader(BytesIO(pdf)).pages)


async def signed(
    contexts: Contexts,
    module_database: BoundDatabase,
    ecf_type: EcfType = EcfType.CREDIT_INVOICE,
    **options: Unpack[DraftOptions],
) -> tuple[Tenant, FiscalDraft, StatusUpdate]:
    """A fresh tenant's invoice, numbered and signed by the channel: the tenant, the draft and its status."""
    tenant = a_tenant()
    await with_range(module_database, tenant, ecf_type)
    draft = draft_of(tenant, **options)
    issued = await channel_of(contexts).issue(call_of(tenant), draft, sign_within=BUDGET)
    assert issued.signed is True
    return tenant, draft, issued.status


async def test_the_representation_is_the_signed_e_cf_printed_archived_once_and_recorded(
    contexts: Contexts, module_database: BoundDatabase, spy: SpyArchive
) -> None:
    tenant, draft, status = await signed(contexts, module_database)
    details = do(status)
    ecf_key = spy.kept[0]
    spy.read.clear()
    represented = await channel_of(contexts).representation(call_of(tenant), draft.document)
    assert (represented.media_type, represented.filename) == ("application/pdf", "E310000000001.pdf")
    text = text_of(represented.content)
    assert "e-NCF: E310000000001" in text
    assert f"Código de seguridad: {details['security_code']}" in text
    assert "Factura de Crédito Fiscal Electrónica" in text
    assert "Monto total" in text
    assert "1180.00" in text
    assert "e-CF emitido en modalidad de Contingencia" not in text
    representation = archive_key(
        channel="do-ecf-v1",
        tenant=tenant,
        kind="representation",
        content=represented.content,
        extension="pdf",
    )
    assert spy.read == [ecf_key]  # rendered from the signed e-CF the archive keeps
    assert spy.kept[1:] == [representation]
    assert spy.media_types[1:] == ["application/pdf"]
    key, sha256, version, state = await representation_row(module_database, tenant)
    assert (key, version, state) == (representation, 2, "signed")  # not a step of the e-CF
    assert isinstance(sha256, bytes)
    assert len(sha256) == 32


async def test_a_later_representation_is_the_archived_bytes_rendered_no_more(
    contexts: Contexts, module_database: BoundDatabase, spy: SpyArchive
) -> None:
    tenant, draft, _ = await signed(contexts, module_database)
    channel = channel_of(contexts)
    first = await channel.representation(call_of(tenant), draft.document)
    representation = spy.kept[-1]
    kept, spy.read[:] = list(spy.kept), []
    again = await channel.representation(call_of(tenant), draft.document)
    assert again == first
    assert spy.kept == kept  # nothing archived again
    assert spy.read == [representation]  # the e-CF is not read: nothing is rendered


async def test_a_consumer_invoice_s_qr_names_dgii_s_consultatimbrefc(
    contexts: Contexts, module_database: BoundDatabase
) -> None:
    tenant, draft, status = await signed(
        contexts, module_database, EcfType.CONSUMER_INVOICE, counterparty=None
    )
    assert do(status)["route"] == "rfce"
    represented = await channel_of(contexts).representation(call_of(tenant), draft.document)
    assert represented.filename == "E320000000001.pdf"
    text = text_of(represented.content)
    assert "Factura de Consumo Electrónica" in text
    assert "Comprador" not in text
    qr = do(status)["qr_url"]
    assert isinstance(qr, str)
    assert "/consultatimbrefc?" in qr


async def test_a_document_not_signed_yet_has_no_representation_and_nothing_is_archived(
    contexts: Contexts, module_database: BoundDatabase, spy: SpyArchive
) -> None:
    tenant = a_tenant()
    await with_range(module_database, tenant, EcfType.CREDIT_INVOICE)
    draft = draft_of(tenant)
    channel = channel_of(contexts)
    await channel.issue(call_of(tenant), draft, sign_within=None)
    with pytest.raises(NotSignedError, match="not signed yet") as caught:
        await channel.representation(call_of(tenant), draft.document)
    assert isinstance(caught.value, ConflictError)
    assert (caught.value.code, caught.value.kind) == ("do_ecf_not_signed", "conflict")
    assert (spy.kept, spy.read) == ([], [])


async def test_an_invoice_the_channel_never_issued_has_no_representation(contexts: Contexts) -> None:
    with pytest.raises(DocumentNotIssuedError) as caught:
        await channel_of(contexts).representation(call_of(a_tenant()), DocumentRef(new_id()))
    assert isinstance(caught.value, NotFoundError)
    assert (caught.value.code, caught.value.kind) == ("do_ecf_document_not_issued", "not_found")


async def test_a_representation_recorded_first_by_another_render_is_the_one_answered(
    contexts: Contexts, module_database: BoundDatabase, spy: SpyArchive, archive: StoreArchive
) -> None:
    """Two versions of the PDF library side by side: the first recorded wins, the other is an orphan."""
    tenant, draft, _ = await signed(contexts, module_database)
    other = b"%PDF-1.4 rendered by another version"
    key = archive_key(
        channel="do-ecf-v1", tenant=tenant, kind="representation", content=other, extension="pdf"
    )
    stored = await archive.put_once(key, other, retain_until=date(2036, 10, 13), media_type="application/pdf")

    async def recorded_meanwhile() -> None:
        async with module_database.unit(tenant) as unit:
            await unit.connection.execute(
                update(DocumentRecord).values(
                    representation_key=stored.key, representation_sha256=stored.sha256
                )
            )

    spy.after_put = recorded_meanwhile
    represented = await channel_of(contexts).representation(call_of(tenant), draft.document)
    assert represented.content == other
    assert (await representation_row(module_database, tenant))[0] == stored.key


async def test_status_is_the_document_as_it_stands_or_none(
    contexts: Contexts, module_database: BoundDatabase
) -> None:
    tenant, draft, status = await signed(contexts, module_database)
    channel = channel_of(contexts)
    assert await channel.status(call_of(tenant), draft.document) == status
    assert do(status)["ecf_type"] == "31"
    assert await channel.status(call_of(tenant), DocumentRef(new_id())) is None
    assert await channel.status(call_of(a_tenant()), draft.document) is None  # another company's


@pytest.mark.parametrize(
    "scope",
    [None, Tenant(OrganizationId(new_id()), CompanyId(new_id()), None)],
    ids=["no tenant", "no mode"],
)
async def test_status_and_representation_are_asked_in_a_company_in_a_mode(
    contexts: Contexts, scope: Tenant | None
) -> None:
    channel = channel_of(contexts)
    call = SystemCall(reason="fiscal:render", scope=scope)
    with pytest.raises(InvalidValueError, match="a company in a mode"):
        await channel.status(call, DocumentRef(new_id()))
    with pytest.raises(InvalidValueError, match="a company in a mode"):
        await channel.representation(call, DocumentRef(new_id()))
