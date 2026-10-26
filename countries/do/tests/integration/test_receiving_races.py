"""Two receptions of one e-NCF at once (Decision 16): the second to record it answers as a reception again.

Both find nothing recorded, both sign a receipt; the table records one (``received_documents_number_key``)
and the other's unit meets it (``ReceivedExistsError``): it answers the first's receipt when it carried the
same bytes, a duplicate's (reason 3) when it carried others, and records nothing. The race is made certain
here: the second reception's first look is blind.
"""

import hashlib
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from typing import Final, final, override
lazy from collections.abc import AsyncGenerator, Callable

import pytest

from ledger_country_do.channels.ecf_v1.adapters.archive import PortArchive
from ledger_country_do.channels.ecf_v1.adapters.inbound import Inbound
from ledger_country_do.channels.ecf_v1.adapters.postgres.unit import PgUnits
from ledger_country_do.channels.ecf_v1.adapters.signing.port import DocumentSigner
from ledger_country_do.channels.ecf_v1.adapters.xml.inbound import read_inbound_arecf
from ledger_country_do.channels.ecf_v1.application.ports import SigningAs
from ledger_country_do.channels.ecf_v1.application.ranges import NumberingUnits
from ledger_country_do.channels.ecf_v1.application.received import ReceivedDocuments
from ledger_country_do.channels.ecf_v1.application.receiving import Receiver, Receiving, Sender
from ledger_country_do.channels.ecf_v1.domain.environments import Environment
from ledger_country_do.channels.ecf_v1.domain.exchanges import NotReceivedReason
from ledger_kernel.ids import CompanyId, OrganizationId, new_id
from ledger_kernel.tenancy import Mode, Tenant
from ledger_testing.pki import pem
from ledger_testing.signer import TestSigner
lazy from ledger_country_do.channels.ecf_v1.application.ranges import NumberingUnit
lazy from ledger_country_do.channels.ecf_v1.application.received import ReceivedDocument
lazy from ledger_country_do.channels.ecf_v1.domain.encf import Encf
lazy from ledger_country_do_testing.local_signer import KeyPairSigner
lazy from ledger_platform.data.bound import BoundDatabase
lazy from ledger_platform.runtime.archiving import StoreArchive
lazy from ledger_testing.clock import ManualClock
lazy from ledger_testing.pki import Authority

GOLDEN: Final = Path(__file__).parents[1] / "contract" / "golden"
SENDER: Final = Sender(identities=frozenset({"101850043"}), stand_in=False)
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
    async def add(self, document: ReceivedDocument) -> None:
        await self._inner.add(document)


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


type Receivings = Callable[[NumberingUnits], Receiving]


@pytest.fixture
def receivings(
    inbound: Inbound, archive: StoreArchive, signer: KeyPairSigner, clock: ManualClock
) -> Receivings:
    """Reception over the units given, the inbound trust, the test's archive and the company's signer."""
    port = DocumentSigner(TestSigner(sign=signer.sign))

    def made(units: NumberingUnits) -> Receiving:
        return Receiving(units, inbound, PortArchive(archive, clock=clock), port, clock=clock)

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
