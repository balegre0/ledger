"""e-CF reception (spec §3.8, §10; Decisions 16, 17; DGII's Paso 9): every document answered with a receipt.

What arrives is archived raw first. Then it is read (hardened, its schema and values: ``read_inbound_ecf``),
its signature verified (``INBOUND``) and its certificate trusted for its ``RNCEmisor``, who must be the
token's sender. The answer is an ARECF signed as the receiving company (``SignerPort``) and archived:
received (``Estado`` 0) once the document is recorded with ``fiscal_channel.received_document.recorded`` in
one unit, else not received (``Estado`` 1) for DGII's reason: 1 specification (the receipt naming what the
document names, read leniently: ``name_inbound``), 2 signature, 3 the same e-NCF sent again with other bytes,
4 a buyer that is not this company. The same bytes again are answered with the receipt they were given.
Nothing but the raw copy is kept of a document that cannot be named (a 400, no receipt). Each reception logs
one line (``do_ecf.reception``): the company, the environment and how it was answered, nothing of the
document, its parties or its sender.
"""

import hashlib
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from http import HTTPStatus
from pathlib import Path
from typing import Final, Protocol

import pytest
from pydantic import TypeAdapter
from structlog.testing import capture_logs
lazy import httpx
lazy import psycopg

from ledger_country_do.channels.ecf_v1.adapters.signing.verifier import STRICT, verify_enveloped
from ledger_country_do.channels.ecf_v1.adapters.xml.inbound import read_inbound_arecf
from ledger_country_do.channels.ecf_v1.domain.encf import EcfType, Encf
from ledger_country_do.channels.ecf_v1.domain.exchanges import NotReceivedReason, Receipt
from ledger_country_do_testing.local_signer import KeyPairSigner
from ledger_country_do_testing.pki import organization
from ledger_country_do_testing.samples import SIGNER_VALID_FROM
from ledger_testing.pki import Authority
lazy from ledger_kernel.tenancy import Tenant
lazy from ledger_testing.clock import ManualClock

RECEIVING_RNC: Final = "131098193"
SENDER_RNC: Final = "101850043"
OTHER_RNC: Final = "131880738"
RECEPTION: Final = f"/do-ecf-v1/testecf/{RECEIVING_RNC}/fe/recepcion/api/ecf"
GOLDEN: Final = Path(__file__).parents[1] / "contract" / "golden"
ENCF: Final = Encf(EcfType.CREDIT_INVOICE, 1)
RECEIVED_AT: Final = datetime(2026, 10, 13, 13, 30, tzinfo=UTC)
ROWS: Final = TypeAdapter(list[tuple[object, ...]])
PAYLOAD: Final = TypeAdapter(dict[str, object])
NAME: Final = b"<RazonSocialEmisor>FERRETERIA DUARTE SRL</RazonSocialEmisor>"
RECEIVED_QUERY: Final = """
    SELECT organization_id, company_id, mode, environment, issuer_rnc, issuer_name, ecf_type, sequence,
        buyer_rnc, issued_on, total, itbis, signed_at, security_code, received_at, xml_sha256, arecf_sha256,
        version
    FROM do_ecf.received_documents
"""


class Send(Protocol):
    async def __call__(self, document: bytes, *, where: str = RECEPTION) -> httpx.Response: ...


def golden(name: str = "ecf-31") -> bytes:
    return (GOLDEN / f"{name}.xml").read_bytes()


def upload(document: bytes) -> dict[str, tuple[str, bytes, str]]:
    return {"xml": ("101850043E310000000001.xml", document, "text/xml")}


def receipt(response: httpx.Response, signer: KeyPairSigner) -> Receipt:
    """The ARECF ``response`` answers: signed in DGII's profile by the receiving company's certificate."""
    assert response.status_code == HTTPStatus.OK, response.text
    assert response.headers["content-type"].startswith("application/xml")
    assert verify_enveloped(response.content, STRICT).certificate == signer.certificate
    return read_inbound_arecf(response.content)


def received(*, encf: Encf = ENCF, at: datetime = RECEIVED_AT) -> Receipt:
    return Receipt(
        issuer_rnc=SENDER_RNC, buyer_rnc=RECEIVING_RNC, encf=encf, received=True, reason=None, at=at
    )


def not_received(reason: NotReceivedReason) -> Receipt:
    return Receipt(
        issuer_rnc=SENDER_RNC,
        buyer_rnc=RECEIVING_RNC,
        encf=ENCF,
        received=False,
        reason=reason,
        at=RECEIVED_AT,
    )


def archived(root: Path) -> dict[str, int]:
    """How many objects the archive keeps of each kind (the key's segment before the file)."""
    kinds: dict[str, int] = {}
    for found in root.rglob("*.xml"):
        kinds[found.parent.name] = kinds.get(found.parent.name, 0) + 1
    return kinds


def signer_of(authority: Authority, rnc: str) -> KeyPairSigner:
    return KeyPairSigner.from_issued(authority.issue(organization(rnc), not_before=SIGNER_VALID_FROM))


async def recorded(admin: psycopg.AsyncConnection) -> tuple[int, int]:
    """How many documents are recorded received, and how many events announce one."""
    documents = await admin.execute("SELECT count(*) FROM do_ecf.received_documents")
    events = await admin.execute(
        "SELECT count(*) FROM do_ecf.outbox WHERE type = 'fiscal_channel.received_document.recorded'"
    )
    ((kept,),) = ROWS.validate_python(await documents.fetchall())
    ((announced,),) = ROWS.validate_python(await events.fetchall())
    assert isinstance(kept, int)
    assert isinstance(announced, int)
    return kept, announced


@pytest.fixture
def archived_at(tmp_path: Path) -> Path:
    """Where the apps' file archive keeps objects (the conftest's ``archived`` settings)."""
    return tmp_path / "archive"


@pytest.fixture
def send(receiver_client: httpx.AsyncClient, token: str) -> Send:
    """Posts a document to the receiving company's reception with the sender's token."""

    async def sent(document: bytes, *, where: str = RECEPTION) -> httpx.Response:
        headers = {"Authorization": f"Bearer {token}"}
        return await receiver_client.post(where, files=upload(document), headers=headers)

    return sent


async def test_a_valid_e_cf_is_recorded_announced_and_answered_received(
    receiving: Tenant,
    send: Send,
    sender: KeyPairSigner,
    signer: KeyPairSigner,
    admin: psycopg.AsyncConnection,
) -> None:
    signed = sender.sign(golden())
    response = await send(signed)
    assert receipt(response, signer) == received()
    found = await admin.execute(RECEIVED_QUERY)
    (row,) = ROWS.validate_python(await found.fetchall())
    assert row[:12] == (
        receiving.organization,
        receiving.company,
        "test",
        "testecf",
        SENDER_RNC,
        "FERRETERIA DUARTE SRL",
        "31",
        1,
        RECEIVING_RNC,
        date(2026, 10, 13),
        Decimal("4887.58"),
        Decimal("676.08"),
    )
    signature_value = signed.split(b"<SignatureValue>")[1][:6].decode()
    assert row[12:] == (
        RECEIVED_AT,
        signature_value,
        RECEIVED_AT,
        hashlib.sha256(signed).digest(),
        hashlib.sha256(response.content).digest(),
        1,
    )
    announced = await admin.execute("SELECT type, subject, payload FROM do_ecf.outbox")
    ((kind, subject, payload),) = ROWS.validate_python(await announced.fetchall())
    assert kind == "fiscal_channel.received_document.recorded"
    details = PAYLOAD.validate_python(payload)
    assert (details["received"], details["channel"], details["environment"], details["number"]) == (
        str(subject),
        "do-ecf-v1",
        "testecf",
        str(ENCF),
    )


async def test_the_same_bytes_again_are_answered_with_the_receipt_they_were_given(
    send: Send, sender: KeyPairSigner, admin: psycopg.AsyncConnection, clock: ManualClock, archived_at: Path
) -> None:
    signed = sender.sign(golden())
    first = await send(signed)
    clock.advance(timedelta(seconds=30))
    second = await send(signed)
    assert (first.status_code, second.status_code) == (HTTPStatus.OK, HTTPStatus.OK)
    assert second.content == first.content
    assert await recorded(admin) == (1, 1)
    assert archived(archived_at) == {"received": 1, "arecf": 1}


async def test_the_same_e_cf_with_other_bytes_is_a_duplicate(
    send: Send, sender: KeyPairSigner, signer: KeyPairSigner, admin: psycopg.AsyncConnection
) -> None:
    assert receipt(await send(sender.sign(golden())), signer).received
    other = golden().replace(NAME, b"<RazonSocialEmisor>FERRETERIA DUARTE, SRL</RazonSocialEmisor>")
    assert receipt(await send(sender.sign(other)), signer) == not_received(NotReceivedReason.DUPLICATE)
    assert await recorded(admin) == (1, 1)


async def test_a_document_for_another_buyer_is_not_this_receiver_s(
    send: Send,
    sender: KeyPairSigner,
    signer: KeyPairSigner,
    admin: psycopg.AsyncConnection,
    archived_at: Path,
) -> None:
    other = golden().replace(f"<RNCComprador>{RECEIVING_RNC}".encode(), f"<RNCComprador>{OTHER_RNC}".encode())
    response = await send(sender.sign(other))
    assert receipt(response, signer) == not_received(NotReceivedReason.NOT_THIS_RECEIVER)
    assert await recorded(admin) == (0, 0)
    assert archived(archived_at) == {"received": 1, "arecf": 1}


async def test_a_document_changed_after_it_was_signed_is_a_signature_error(
    send: Send, sender: KeyPairSigner, signer: KeyPairSigner, admin: psycopg.AsyncConnection
) -> None:
    tampered = sender.sign(golden()).replace(b"CEMENTO GRIS", b"CEMENTO AZUL", 1)
    assert receipt(await send(tampered), signer) == not_received(NotReceivedReason.SIGNATURE)
    assert await recorded(admin) == (0, 0)


async def test_a_signature_no_trusted_authority_vouches_for_is_a_signature_error(
    send: Send, signer: KeyPairSigner, admin: psycopg.AsyncConnection
) -> None:
    stranger = signer_of(Authority.root(now=SIGNER_VALID_FROM), SENDER_RNC)
    assert receipt(await send(stranger.sign(golden())), signer) == not_received(NotReceivedReason.SIGNATURE)
    assert await recorded(admin) == (0, 0)


async def test_a_signature_by_a_certificate_of_another_taxpayer_is_a_signature_error(
    send: Send, authority: Authority, signer: KeyPairSigner
) -> None:
    someone = signer_of(authority, OTHER_RNC)
    assert receipt(await send(someone.sign(golden())), signer) == not_received(NotReceivedReason.SIGNATURE)


async def test_a_document_its_schema_refuses_is_a_specification_error_named_as_it_reads(
    send: Send, sender: KeyPairSigner, signer: KeyPairSigner, admin: psycopg.AsyncConnection
) -> None:
    unnamed = golden().replace(NAME, b"")
    assert receipt(await send(sender.sign(unnamed)), signer) == not_received(NotReceivedReason.SPECIFICATION)
    assert await recorded(admin) == (0, 0)


async def test_a_document_of_another_issuer_than_the_token_s_is_refused(
    send: Send, authority: Authority, admin: psycopg.AsyncConnection, archived_at: Path
) -> None:
    """The token is the sender's: it sends its own documents, signed by itself, never another's."""
    other = golden().replace(f"<RNCEmisor>{SENDER_RNC}".encode(), f"<RNCEmisor>{OTHER_RNC}".encode())
    response = await send(signer_of(authority, OTHER_RNC).sign(other))
    assert response.status_code == HTTPStatus.UNAUTHORIZED, response.text
    assert await recorded(admin) == (0, 0)
    assert archived(archived_at) == {"received": 1}


@pytest.mark.parametrize(
    "document",
    [
        b'<!DOCTYPE ECF [<!ENTITY e "x">]><ECF><Encabezado>&e;</Encabezado></ECF>',
        b"<ECF><Encabezado>",
        b"<ARECF/>",
    ],
    ids=["doctype", "malformed", "another-root"],
)
async def test_a_document_no_receipt_can_name_is_a_bad_request_and_only_its_raw_copy_is_kept(
    send: Send, admin: psycopg.AsyncConnection, archived_at: Path, document: bytes
) -> None:
    response = await send(document)
    assert response.status_code == HTTPStatus.BAD_REQUEST, response.text
    assert set(PAYLOAD.validate_json(response.content)) <= {"type", "title", "status", "code", "request_id"}
    assert await recorded(admin) == (0, 0)
    assert archived(archived_at) == {"received": 1}


async def test_what_is_not_a_form_with_its_file_is_a_bad_request(
    receiver_client: httpx.AsyncClient, token: str, archived_at: Path
) -> None:
    headers = {"Authorization": f"Bearer {token}"}
    for sent in (
        await receiver_client.post(
            RECEPTION, content=b"<ECF/>", headers=headers | {"Content-Type": "text/xml"}
        ),
        await receiver_client.post(
            RECEPTION, files={"file": ("x.xml", b"<ECF/>", "text/xml")}, headers=headers
        ),
        await receiver_client.post(
            RECEPTION,
            content=b'--x\r\nContent-Disposition: form-data; name="xml"\r\n\r\n<ECF/>',
            headers=headers | {"Content-Type": "multipart/form-data; boundary=x"},
        ),
    ):
        assert sent.status_code == HTTPStatus.BAD_REQUEST, sent.text
    assert archived(archived_at) == {}


async def test_dgii_s_paths_are_case_insensitive(
    send: Send, sender: KeyPairSigner, signer: KeyPairSigner
) -> None:
    mixed = f"/Do-Ecf-V1/TESTECF/{RECEIVING_RNC}/FE/Recepcion/Api/ECF"
    assert receipt(await send(sender.sign(golden()), where=mixed), signer) == received()


async def test_a_document_naming_no_buyer_is_not_this_receiver_s(
    send: Send, sender: KeyPairSigner, signer: KeyPairSigner, admin: psycopg.AsyncConnection
) -> None:
    """A 32 without RNCComprador (or a 43, a 47) is sent to no electronic receiver: none receives it."""
    response = await send(sender.sign(golden("ecf-32-anonymous")))
    assert receipt(response, signer) == Receipt(
        issuer_rnc=SENDER_RNC,
        buyer_rnc=RECEIVING_RNC,
        encf=Encf(EcfType.CONSUMER_INVOICE, 1),
        received=False,
        reason=NotReceivedReason.NOT_THIS_RECEIVER,
        at=RECEIVED_AT,
    )
    assert await recorded(admin) == (0, 0)


@pytest.mark.parametrize("refusal", ["unreadable", "untrusted"])
async def test_a_refused_document_of_another_issuer_than_the_token_s_gets_no_receipt(
    send: Send, archived_at: Path, refusal: str
) -> None:
    """Even a refusal names its issuer: the token's sender gets none about another's document."""
    other = golden().replace(f"<RNCEmisor>{SENDER_RNC}".encode(), f"<RNCEmisor>{OTHER_RNC}".encode())
    stranger = signer_of(Authority.root(now=SIGNER_VALID_FROM), OTHER_RNC)
    document = stranger.sign(other if refusal == "untrusted" else other.replace(NAME, b""))
    response = await send(document)
    assert response.status_code == HTTPStatus.UNAUTHORIZED, response.text
    assert archived(archived_at) == {"received": 1}


async def test_each_reception_logs_its_company_environment_and_outcome_and_nothing_of_the_document(
    receiving: Tenant, send: Send, sender: KeyPairSigner, authority: Authority
) -> None:
    """Operations see how a company's receptions are answered; no tax id, e-NCF or byte of a document."""
    signed = sender.sign(golden())
    other_buyer = golden().replace(
        f"<RNCComprador>{RECEIVING_RNC}".encode(), f"<RNCComprador>{OTHER_RNC}".encode()
    )
    other_issuer = golden().replace(f"<RNCEmisor>{SENDER_RNC}".encode(), f"<RNCEmisor>{OTHER_RNC}".encode())
    with capture_logs() as logs:
        for document in (
            signed,
            signed,
            sender.sign(golden().replace(NAME, b"<RazonSocialEmisor>OTRA SRL</RazonSocialEmisor>")),
            sender.sign(other_buyer),
            signed.replace(b"CEMENTO GRIS", b"CEMENTO AZUL", 1),
            sender.sign(golden().replace(NAME, b"")),
            b"<ECF><Encabezado>",
            signer_of(authority, OTHER_RNC).sign(other_issuer),
        ):
            _ = await send(document)
    receptions = [entry for entry in logs if entry["event"] == "do_ecf.reception"]
    assert receptions == [
        {
            "event": "do_ecf.reception",
            "log_level": "info",
            "package": "do-ecf-v1",
            "company_id": str(receiving.company),
            "environment": "testecf",
            "outcome": outcome,
        }
        for outcome in (
            "received",
            "again",
            "duplicate",
            "not_this_receiver",
            "signature",
            "specification",
            "no_receipt",
            "not_the_sender",
        )
    ]
    said = repr(logs)
    assert not any(secret in said for secret in (SENDER_RNC, RECEIVING_RNC, OTHER_RNC, str(ENCF), "CEMENTO"))
