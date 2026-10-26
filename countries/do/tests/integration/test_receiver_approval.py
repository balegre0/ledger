"""Commercial approvals received (spec §3.8, §8.6.5 step 5; DGII's Paso 11): a buyer approves our e-CF.

The buyer sends its ACECF to the issuing company's receiver with a token. It is archived first, read
(``read_inbound_acecf``: its schema inside), its signature verified (``INBOUND``) and its certificate
trusted for its ``RNCComprador``, who must be the token's sender; it must name our company as the issuer and
one of our documents DGII accepted (or accepted with observations) in the path's environment, with that
document's date and total. Then the document records it (``commercial_approval``) at a new version and
``fiscal_channel.document.updated`` announces it: 200. Anything refused is a bare 400.
"""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from http import HTTPStatus
from typing import Final, Protocol
lazy from pathlib import Path
lazy from uuid import UUID

import pytest
from pydantic import TypeAdapter
lazy import httpx
lazy import psycopg

from ledger_country_do.channels.ecf_v1.adapters.postgres.unit import bind
from ledger_country_do.channels.ecf_v1.adapters.xml.acecf import render_acecf
from ledger_country_do.channels.ecf_v1.application.documents import EcfDocument, Signature
from ledger_country_do.channels.ecf_v1.application.ports import Archived
from ledger_country_do.channels.ecf_v1.application.ranges import NumberingRange
from ledger_country_do.channels.ecf_v1.domain.ecf import DeliveryState, EcfDocumentState, EmissionMode
from ledger_country_do.channels.ecf_v1.domain.encf import EcfType, Encf
from ledger_country_do.channels.ecf_v1.domain.environments import Environment
from ledger_country_do.channels.ecf_v1.domain.exchanges import CommercialApproval
from ledger_country_do.channels.ecf_v1.domain.numbering import RangeState, RangeStatus
from ledger_country_do.channels.ecf_v1.domain.routing import Route
from ledger_country_do.channels.ecf_v1.domain.status_map import NumberFate
from ledger_country_do_testing.fiscal import fiscal_draft
from ledger_country_do_testing.local_signer import KeyPairSigner
from ledger_country_do_testing.pki import organization
from ledger_country_do_testing.samples import SIGNER_VALID_FROM
from ledger_fiscal_kit.drafts import DocumentRef, FiscalParty
from ledger_kernel.ids import new_id
from ledger_kernel.tax_ids import TaxId, TaxIdScheme
from ledger_testing.pki import Authority
lazy from ledger_kernel.tenancy import Tenant
lazy from ledger_platform.data.bound import BoundDatabase

RECEIVING_RNC: Final = "131098193"
SENDER_RNC: Final = "101850043"
OTHER_RNC: Final = "131880738"
APPROVAL: Final = f"/do-ecf-v1/testecf/{RECEIVING_RNC}/fe/aprobacioncomercial/api/ecf"
ENCF: Final = Encf(EcfType.CREDIT_INVOICE, 1)
ISSUED_ON: Final = date(2026, 10, 13)
TOTAL: Final = Decimal("1180.00")
"""The total of ``fiscal_draft()``: one line of 1,000.00 at 18 %."""
APPROVED_AT: Final = datetime(2026, 10, 13, 13, 0, tzinfo=UTC)
ROWS: Final = TypeAdapter(list[tuple[object, ...]])
PAYLOAD: Final = TypeAdapter(dict[str, object])
APPROVED: Final = CommercialApproval(
    issuer_rnc=RECEIVING_RNC,
    buyer_rnc=SENDER_RNC,
    encf=ENCF,
    issued_on=ISSUED_ON,
    total=TOTAL,
    approved=True,
    reason=None,
    at=APPROVED_AT,
)


class Approve(Protocol):
    async def __call__(self, document: bytes) -> httpx.Response: ...


class Ours(Protocol):
    async def __call__(
        self, state: EcfDocumentState = EcfDocumentState.ACCEPTED, *, buyer: str | None = SENDER_RNC
    ) -> EcfDocument: ...


def party(rnc: str) -> FiscalParty:
    """A buyer of our e-CF, by its RNC."""
    return FiscalParty(tax_id=TaxId(TaxIdScheme("do.rnc"), rnc), legal_name=f"Compradora {rnc} SRL")


def upload(document: bytes) -> dict[str, tuple[str, bytes, str]]:
    return {"xml": ("ACECF.xml", document, "text/xml")}


def bare(response: httpx.Response, status: HTTPStatus) -> None:
    assert response.status_code == status, response.text
    assert set(PAYLOAD.validate_json(response.content)) <= {"type", "title", "status", "code", "request_id"}


@pytest.fixture
def approve(receiver_client: httpx.AsyncClient, token: str) -> Approve:
    async def sent(document: bytes) -> httpx.Response:
        headers = {"Authorization": f"Bearer {token}"}
        return await receiver_client.post(APPROVAL, files=upload(document), headers=headers)

    return sent


@pytest.fixture
def ours(receiving: Tenant, module_database: BoundDatabase) -> Ours:
    """Our company's e-CF ``E310000000001`` of 1,180.00, decided by DGII (accepted unless told otherwise)."""

    async def kept(
        state: EcfDocumentState = EcfDocumentState.ACCEPTED, *, buyer: str | None = SENDER_RNC
    ) -> EcfDocument:
        now = datetime.now(UTC)
        draft = replace(
            fiscal_draft(counterparty=None if buyer is None else party(buyer)),
            organization=receiving.organization,
            company=receiving.company,
            document=DocumentRef(new_id()),
        )
        authorized = RangeState(
            id=new_id(),
            ecf_type=EcfType.CREDIT_INVOICE,
            first=1,
            last=10,
            next=2,
            expires_on=date(2028, 12, 31),
            status=RangeStatus.ACTIVE,
        )
        signature = Signature(
            signed_at=now,
            security_code="AbC123",
            qr_url="https://ecf.dgii.gov.do/testecf/consultatimbre?x",
            emission_mode=EmissionMode.NORMAL,
            ecf=Archived(key="do-ecf-v1/test/o/c/ecf/a.xml", sha256=bytes(32)),
            rfce=None,
        )
        fate = NumberFate.USED if state is not EcfDocumentState.REJECTED else NumberFate.BURNED
        document = EcfDocument(
            id=new_id(),
            invoice=draft.document,
            environment=Environment.TESTECF,
            encf=ENCF,
            range_id=authorized.id,
            range_expires_on=authorized.expires_on,
            state=state,
            route=Route.RECEPTION,
            delivery=DeliveryState.NOT_STARTED,
            issued_on=ISSUED_ON,
            signature=signature,
            draft=draft,
            version=3,
            created_at=now,
            updated_at=now,
            submission_started_at=now,
            outcome_code=1,
            sequence_used=True,
            number_fate=fate,
        )
        async with module_database.unit(receiving) as unit:
            numbering = bind(unit)
            await numbering.ranges.series(Environment.TESTECF, EcfType.CREDIT_INVOICE)
            await numbering.ranges.add(
                NumberingRange(environment=Environment.TESTECF, range=authorized, created_at=now, version=1)
            )
            await numbering.documents.add(document)
        return document

    return kept


def approval_of(payload: object) -> object:
    """What an event of ``fiscal_channel.document.updated`` says of the buyer's commercial approval."""
    update = PAYLOAD.validate_python(PAYLOAD.validate_python(payload)["update"])
    return PAYLOAD.validate_python(PAYLOAD.validate_python(update["details"])["do"])["commercial_approval"]


async def updates(admin: psycopg.AsyncConnection, document: UUID) -> tuple[object, ...]:
    """The document's ``commercial_approval`` and version, then what each event announced of it."""
    found = await admin.execute(
        "SELECT commercial_approval, version FROM do_ecf.documents WHERE id = %s", (document,)
    )
    events = await admin.execute(
        "SELECT payload FROM do_ecf.outbox WHERE type = 'fiscal_channel.document.updated' ORDER BY id"
    )
    (row,) = ROWS.validate_python(await found.fetchall())
    announced = tuple(approval_of(payload) for (payload,) in ROWS.validate_python(await events.fetchall()))
    return (*row, *announced)


async def test_an_approval_of_our_accepted_document_is_recorded_and_announced(
    ours: Ours, approve: Approve, sender: KeyPairSigner, admin: psycopg.AsyncConnection, tmp_path: Path
) -> None:
    document = await ours()
    response = await approve(sender.sign(render_acecf(APPROVED)))
    assert (response.status_code, response.content) == (HTTPStatus.OK, b"")
    recorded = {"approved": True, "reason": None, "at": "2026-10-13T13:00:00+00:00"}
    assert await updates(admin, document.id) == (recorded, 4, recorded)
    kept = [found.parent.name for found in (tmp_path / "archive").rglob("*.xml")]
    assert kept == ["acecf"]
    again = await approve(sender.sign(render_acecf(APPROVED)))
    assert again.status_code == HTTPStatus.OK, again.text
    assert await updates(admin, document.id) == (recorded, 4, recorded)


async def test_a_rejection_is_recorded_with_its_reason(
    ours: Ours, approve: Approve, sender: KeyPairSigner, admin: psycopg.AsyncConnection
) -> None:
    document = await ours(EcfDocumentState.ACCEPTED_CONDITIONALLY)
    rejected = replace(APPROVED, approved=False, reason="MERCANCIA INCOMPLETA")
    response = await approve(sender.sign(render_acecf(rejected)))
    assert response.status_code == HTTPStatus.OK, response.text
    recorded = {"approved": False, "reason": "MERCANCIA INCOMPLETA", "at": "2026-10-13T13:00:00+00:00"}
    assert await updates(admin, document.id) == (recorded, 4, recorded)


async def test_an_approval_of_a_document_dgii_rejected_is_refused(
    ours: Ours, approve: Approve, sender: KeyPairSigner, admin: psycopg.AsyncConnection
) -> None:
    document = await ours(EcfDocumentState.REJECTED)
    bare(await approve(sender.sign(render_acecf(APPROVED))), HTTPStatus.BAD_REQUEST)
    assert await updates(admin, document.id) == (None, 3)


@pytest.mark.parametrize(
    "approval",
    [
        replace(APPROVED, total=Decimal("1180.01")),
        replace(APPROVED, issued_on=date(2026, 10, 12)),
        replace(APPROVED, encf=Encf(EcfType.CREDIT_INVOICE, 2)),
        replace(APPROVED, issuer_rnc=OTHER_RNC),
    ],
    ids=["another-total", "another-date", "another-number", "another-issuer"],
)
async def test_an_approval_that_does_not_match_our_document_is_refused(
    ours: Ours,
    approve: Approve,
    sender: KeyPairSigner,
    admin: psycopg.AsyncConnection,
    approval: CommercialApproval,
) -> None:
    document = await ours()
    bare(await approve(sender.sign(render_acecf(approval))), HTTPStatus.BAD_REQUEST)
    assert await updates(admin, document.id) == (None, 3)


async def test_an_approval_not_trusted_not_readable_or_not_the_sender_s_is_refused(
    ours: Ours,
    approve: Approve,
    sender: KeyPairSigner,
    authority: Authority,
    admin: psycopg.AsyncConnection,
) -> None:
    document = await ours()
    stranger = Authority.root(now=SIGNER_VALID_FROM).issue(
        organization(SENDER_RNC), not_before=SIGNER_VALID_FROM
    )
    for refused in (
        KeyPairSigner.from_issued(stranger).sign(render_acecf(APPROVED)),
        sender.sign(render_acecf(APPROVED)).replace(b"1180.00", b"1180.0", 1),
        render_acecf(APPROVED),
        b"<ACECF>",
    ):
        bare(await approve(refused), HTTPStatus.BAD_REQUEST)
    someone = KeyPairSigner.from_issued(
        authority.issue(organization(OTHER_RNC), not_before=SIGNER_VALID_FROM)
    )
    bare(
        await approve(someone.sign(render_acecf(replace(APPROVED, buyer_rnc=OTHER_RNC)))),
        HTTPStatus.UNAUTHORIZED,
    )
    assert await updates(admin, document.id) == (None, 3)


@pytest.mark.parametrize("buyer", [RECEIVING_RNC, None], ids=["another-buyer", "no-buyer"])
async def test_an_approval_by_a_taxpayer_that_is_not_the_document_s_buyer_is_refused(
    ours: Ours, approve: Approve, sender: KeyPairSigner, admin: psycopg.AsyncConnection, buyer: str | None
) -> None:
    """Spec §8.6.5 step 5: the buyer approves; a trusted third party (or anyone, for a document naming no
    buyer) does not, whatever its signature."""
    document = await ours(buyer=buyer)
    bare(await approve(sender.sign(render_acecf(APPROVED))), HTTPStatus.BAD_REQUEST)
    assert await updates(admin, document.id) == (None, 3)


async def test_an_older_word_of_the_buyer_never_overwrites_a_newer_one(
    ours: Ours, approve: Approve, sender: KeyPairSigner, admin: psycopg.AsyncConnection
) -> None:
    """The latest by its moment (``FechaHoraAprobacionComercial``) is kept, whatever arrives last."""
    document = await ours()
    assert (await approve(sender.sign(render_acecf(APPROVED)))).status_code == HTTPStatus.OK
    earlier = replace(APPROVED, approved=False, reason="TARDE", at=APPROVED_AT - timedelta(minutes=10))
    assert (await approve(sender.sign(render_acecf(earlier)))).status_code == HTTPStatus.OK
    recorded = {"approved": True, "reason": None, "at": "2026-10-13T13:00:00+00:00"}
    assert await updates(admin, document.id) == (recorded, 4, recorded)
    later = replace(earlier, at=APPROVED_AT + timedelta(minutes=10))
    assert (await approve(sender.sign(render_acecf(later)))).status_code == HTTPStatus.OK
    rejected = {"approved": False, "reason": "TARDE", "at": "2026-10-13T13:10:00+00:00"}
    assert await updates(admin, document.id) == (rejected, 5, recorded, rejected)
