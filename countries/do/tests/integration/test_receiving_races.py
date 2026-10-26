"""Two receptions of one e-NCF at once (Decision 16): the second to record it answers as a reception again.

Both find nothing recorded, both sign a receipt; the table records one (``received_documents_number_key``)
and the other's unit meets it (``ReceivedExistsError``): it answers the first's receipt when it carried the
same bytes, a duplicate's (reason 3) when it carried others, and records nothing. The race is made certain
here: the second reception's first look is blind.

Reading, verifying and trusting a document (``InboundFormat``) is CPU work of up to 2 MiB: it runs in a
thread, so the receiver's other requests (a probe, another reception) go on meanwhile.
"""

import asyncio
import hashlib
import time
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from typing import Final, Protocol, final, override
lazy from collections.abc import AsyncGenerator
lazy from datetime import datetime
lazy from uuid import UUID

import pytest

from ledger_country_do.channels.ecf_v1.adapters.archive import PortArchive
from ledger_country_do.channels.ecf_v1.adapters.inbound import Inbound
from ledger_country_do.channels.ecf_v1.adapters.postgres.unit import PgUnits
from ledger_country_do.channels.ecf_v1.adapters.signing.port import DocumentSigner
from ledger_country_do.channels.ecf_v1.adapters.xml.inbound import read_inbound_arecf
from ledger_country_do.channels.ecf_v1.application.ports import SigningAs
from ledger_country_do.channels.ecf_v1.application.ranges import NumberingUnits
from ledger_country_do.channels.ecf_v1.application.received import ReceivedDocuments
from ledger_country_do.channels.ecf_v1.application.receiving import InboundFormat, Receiver, Receiving, Sender
from ledger_country_do.channels.ecf_v1.domain.environments import Environment
from ledger_country_do.channels.ecf_v1.domain.exchanges import NotReceivedReason
from ledger_kernel.ids import CompanyId, OrganizationId, new_id
from ledger_kernel.tenancy import Mode, Tenant
from ledger_testing.pki import pem
from ledger_testing.signer import TestSigner
lazy from ledger_country_do.channels.ecf_v1.application.ranges import NumberingUnit
lazy from ledger_country_do.channels.ecf_v1.application.received import ReceivedDocument
lazy from ledger_country_do.channels.ecf_v1.application.receiving import Inspection
lazy from ledger_country_do.channels.ecf_v1.domain.approval import Decided
lazy from ledger_country_do.channels.ecf_v1.domain.encf import Encf
lazy from ledger_country_do.channels.ecf_v1.domain.exchanges import CommercialApproval, Receipt
lazy from ledger_country_do_testing.local_signer import KeyPairSigner
lazy from ledger_kernel.pagination import Page, PageRequest
lazy from ledger_platform.data.bound import BoundDatabase
lazy from ledger_platform.runtime.archiving import StoreArchive
lazy from ledger_testing.clock import ManualClock
lazy from ledger_testing.pki import Authority

GOLDEN: Final = Path(__file__).parents[1] / "contract" / "golden"
SENDER: Final = Sender(identities=frozenset({"101850043"}), stand_in=False)
SLOW: Final = 0.5
NAME: Final = b"<RazonSocialEmisor>FERRETERIA DUARTE SRL</RazonSocialEmisor>"


@final
class Blind(ReceivedDocuments):
    """The received documents, whose first look finds nothing: a reception that raced another one."""

    __slots__ = ("_inner",)

    def __init__(self, inner: ReceivedDocuments) -> None:
        self._inner = inner

    @override
    async def find(self, environment: Environment, issuer_rnc: str, encf: Encf) -> ReceivedDocument | None:
        return None

    @override
    async def get(self, document: UUID, *, lock: bool) -> ReceivedDocument | None:
        return await self._inner.get(document, lock=lock)

    @override
    async def page(
        self,
        request: PageRequest,
        *,
        environment: Environment | None,
        issuer: str | None,
        decided: Decided | None,
    ) -> Page[ReceivedDocument]:
        return await self._inner.page(request, environment=environment, issuer=issuer, decided=decided)

    @override
    async def add(self, document: ReceivedDocument) -> None:
        await self._inner.add(document)

    @override
    async def changed(self, document: ReceivedDocument) -> None:
        await self._inner.changed(document)


@final
class Slow(InboundFormat):
    """The receiver's inspection, a while long (``time.sleep``: it holds whatever thread runs it)."""

    __slots__ = ("_inner",)

    def __init__(self, inner: InboundFormat) -> None:
        self._inner = inner

    @override
    def ecf(self, document: bytes, *, environment: Environment, received_at: datetime) -> Inspection:
        time.sleep(SLOW)
        return self._inner.ecf(document, environment=environment, received_at=received_at)

    @override
    def approval(
        self, document: bytes, *, environment: Environment, received_at: datetime
    ) -> CommercialApproval | None:
        return self._inner.approval(document, environment=environment, received_at=received_at)

    @override
    def receipt(self, receipt: Receipt) -> bytes:
        return self._inner.receipt(receipt)


@final
class RacedUnits(NumberingUnits):
    """The channel's units, whose first is blind (``Blind``): every later one sees what is recorded."""

    __slots__ = ("_inner", "_opened")

    def __init__(self, inner: NumberingUnits) -> None:
        self._inner = inner
        self._opened = 0

    @override
    @asynccontextmanager
    async def unit(self, tenant: Tenant) -> AsyncGenerator[NumberingUnit]:
        self._opened += 1
        async with self._inner.unit(tenant) as unit:
            yield replace(unit, received=Blind(unit.received)) if self._opened == 1 else unit


@pytest.fixture
async def inbound(tmp_path: Path, authority: Authority) -> AsyncGenerator[Inbound]:
    roots = tmp_path / "roots.pem"
    roots.write_bytes(pem(authority.certificate))
    async with Inbound(roots, standins=frozenset()).opened() as opened:
        yield opened


class Receivings(Protocol):
    def __call__(self, units: NumberingUnits, inspecting: InboundFormat | None = None) -> Receiving: ...


@pytest.fixture
def receivings(
    inbound: Inbound, archive: StoreArchive, signer: KeyPairSigner, clock: ManualClock
) -> Receivings:
    """Reception over the units given, the inbound trust, the test's archive and the company's signer."""
    port = DocumentSigner(TestSigner(sign=signer.sign))

    def made(units: NumberingUnits, inspecting: InboundFormat | None = None) -> Receiving:
        archived = PortArchive(archive, clock=clock)
        return Receiving(units, inspecting or inbound, archived, port, clock=clock)

    return made


async def test_a_reception_that_raced_another_answers_as_a_reception_again(
    module_database: BoundDatabase, receivings: Receivings, sender: KeyPairSigner
) -> None:
    tenant = Tenant(OrganizationId(new_id()), CompanyId(new_id()), Mode.TEST)
    receiver = Receiver(
        who=SigningAs(
            organization=tenant.organization, company=tenant.company, mode=Mode.TEST, reason="do_ecf.receive"
        ),
        rnc="131098193",
        environment=Environment.TESTECF,
    )
    golden = (GOLDEN / "ecf-31.xml").read_bytes()
    signed = sender.sign(golden)
    other = sender.sign(golden.replace(NAME, NAME.replace(b"SRL", b"S.R.L.")))
    answered = await receivings(PgUnits(module_database)).receive(receiver, SENDER, signed)
    same = receivings(RacedUnits(PgUnits(module_database)))
    assert await same.receive(receiver, SENDER, signed) == answered
    duplicate = receivings(RacedUnits(PgUnits(module_database)))
    receipt = read_inbound_arecf(await duplicate.receive(receiver, SENDER, other))
    assert (receipt.received, receipt.reason) == (False, NotReceivedReason.DUPLICATE)
    async with PgUnits(module_database).unit(tenant) as unit:
        found = await unit.received.find(Environment.TESTECF, "101850043", receipt.encf)
    assert found is not None
    assert found.receipt.sha256 == hashlib.sha256(answered).digest()


async def test_a_slow_inspection_holds_no_other_request(
    module_database: BoundDatabase, receivings: Receivings, sender: KeyPairSigner, inbound: Inbound
) -> None:
    tenant = Tenant(OrganizationId(new_id()), CompanyId(new_id()), Mode.TEST)
    receiver = Receiver(
        who=SigningAs(
            organization=tenant.organization, company=tenant.company, mode=Mode.TEST, reason="do_ecf.receive"
        ),
        rnc="131098193",
        environment=Environment.TESTECF,
    )
    ticks: list[float] = []

    async def probe() -> None:
        while True:
            ticks.append(time.monotonic())
            await asyncio.sleep(0.02)

    probing = asyncio.create_task(probe())
    slow = receivings(PgUnits(module_database), Slow(inbound))
    started = time.monotonic()
    answered = await slow.receive(receiver, SENDER, sender.sign((GOLDEN / "ecf-31.xml").read_bytes()))
    probing.cancel()
    assert read_inbound_arecf(answered).received
    assert len([tick for tick in ticks if started <= tick <= started + SLOW]) >= 5
