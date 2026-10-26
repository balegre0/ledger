"""An invoice becomes an accepted e-CF, end to end (spec §8.5, §8.6, §14.2): every app, the fake DGII.

Through the ``api`` app's HTTP alone, as a client does it: companies registered at their fiscal address (Task
35), their certificates installed in the signer (the signer module, over gRPC), enrolled on do-ecf-v1 in
TesteCF with ranges for types 31 and 32, listed in DGII's directory at our receiver. Then the ``worker``
drains what the request left (``eventually``): the e-CF is sent once, accepted, delivered to the electronic
buyer through our ``receiver`` (Task 21), approved by it commercially (Tasks 22-23) and the approval mirrored
in the invoice; a consumer invoice goes as a summary; the printed representation is a PDF (Task 24). A signer
too slow for the request leaves the signature to the worker (Task 35); a document parked for its certificate
is woken by the signer's ``signer.certificate.installed``, moved by the worker's fan-out to fiscal's inbox
(Task 25); a document whose answer was lost is never sent again (Review Focus 1); an e-CF another taxpayer
sends is received and approved out, to its emitter and to DGII.
"""

import asyncio
import base64
import io
from abc import ABCMeta, abstractmethod
from http import HTTPStatus
from typing import Final, LiteralString, Protocol
lazy from collections.abc import Awaitable, Callable, Mapping
lazy from contextlib import AbstractAsyncContextManager
lazy from pathlib import Path

from pydantic import TypeAdapter
from pypdf import PdfReader
lazy import httpx
lazy import psycopg

from ledger_country_do.channels.ecf_v1.adapters.signing.verifier import STRICT, verify_enveloped
from ledger_country_do.channels.ecf_v1.adapters.xml import ecf
from ledger_country_do.channels.ecf_v1.adapters.xml.inbound import read_inbound_arecf
from ledger_country_do.channels.ecf_v1.domain.drafts import Buyer, Issuer
from ledger_country_do.channels.ecf_v1.domain.totals import compute_totals
from ledger_country_do_testing.fake_dgii.receiver import receiver_of_ours
from ledger_country_do_testing.fake_dgii.sender import Sender
from ledger_country_do_testing.fake_dgii.state import Scripted
from ledger_country_do_testing.local_signer import KeyPairSigner
from ledger_country_do_testing.samples import SIGNED_AT, credit_invoice
from ledger_fiscal_kit.xml.hardened import texts
from ledger_kernel.ids import COMPANY_PREFIX, parse_id
from ledger_platform.data.codec import JsonValue
from ledger_signer.adapters.http.router import PKCS12, UNLOCK_HEADER
from ledger_testing.api import created, document, page
from ledger_testing.pki import bundle
from ledger_testing.waiting import eventually
lazy from ledger_country_do_testing.fake_dgii.app import FakeDgii
lazy from ledger_testing.api import TestPrincipal
lazy from ledger_testing.pki import Issued

ISSUER_RNC: Final = "101850043"
BUYER_RNC: Final = "131098193"
PARKED_RNC: Final = "130000012"
SUPPLIER_RNC: Final = "132000003"
NAMES: Final = {
    ISSUER_RNC: "FERRETERIA DUARTE SRL",
    BUYER_RNC: "CONSTRUCTORA DEL CARIBE SRL",
    PARKED_RNC: "COMERCIAL LA ESPERA SRL",
    SUPPLIER_RNC: "SUMINISTROS DEL ESTE SRL",
}
"""The conftest's taxpayers (its ``certificates`` are theirs, by RNC)."""
RECEIVER_URL: Final = "https://receiver.ledger.test"
"""Our receiver's public URL, as the conftest's ``environment`` sets it."""
DRAINING: Final = 60.0
"""Seconds the worker is given to drain what a test waits for: polls of DGII are 1, 2 and 5 s apart."""
ADDRESS: Final = {"line": "AV. DUARTE 100, VILLA CONSUELO", "municipality": "010100", "province": "010000"}
ENVIRONMENT: Final = "testecf"
PASSWORD: Final = "e2e-bundle-password"
UNLOCK: Final = base64.b64encode(PASSWORD.encode()).decode()
LINE: Final = {
    "description": "CEMENTO GRIS 42.5 KG",
    "quantity": "2",
    "unit_price": "500.00",
    "taxes": ["do.itbis.standard"],
}
TO_THE_BUYER: Final = {
    "type": "invoice",
    "buyer": {"legal_name": NAMES[BUYER_RNC], "tax_id": {"scheme": "do.rnc", "value": BUYER_RNC}},
    "lines": [LINE],
}
"""A credit invoice (type 31) to an electronic buyer, which DGII's directory lists."""
TO_A_CONSUMER: Final = {"type": "invoice", "lines": [LINE]}
"""A consumer invoice (type 32) of 1,180.00: sent as a summary (RFCE)."""
FIRST_31: Final = "E310000000001"
FIRST_32: Final = "E320000000001"
FISCAL: Final = TypeAdapter(list[dict[str, JsonValue]])
OBJECT: Final = TypeAdapter(dict[str, JsonValue])
ROWS: Final = TypeAdapter(list[tuple[str, ...]])
COUNT: Final = TypeAdapter(int)
MARKED: Final = """
    SELECT coalesce(track_id, '') FROM do_ecf.documents
    WHERE submission_started_at IS NOT NULL AND ecf_type = '31'
"""
"""The e-CF sent (its submission marked) whose answer, and so its track id, is not known."""
SETTLING: Final = """
    SELECT count(*) FROM do_ecf.jobs
    WHERE kind = 'do_ecf.advance' AND status = 'ready' AND run_at > now() + interval '10 seconds'
"""
"""The advance job waiting out the settle delay (30 s) before it asks DGII for the e-NCF's track ids."""

type Settled = Callable[[Mapping[str, JsonValue], Mapping[str, JsonValue]], bool]
"""Whether an invoice's do-ecf-v1 entry and details are where a test waits for them."""


class Gate(Protocol):
    """The conftest's ``Gate``: while ``held`` is set and not released, the api's signatures wait."""

    held: asyncio.Event | None


class Stack(Protocol, metaclass=ABCMeta):
    """The conftest's ``stack``: the apps started, the fake DGII, the api's gate, the file archive."""

    @property
    @abstractmethod
    def client(self) -> httpx.AsyncClient:
        """A client of the ``api`` app."""

    @property
    @abstractmethod
    def fake(self) -> FakeDgii:
        """The fake DGII the worker reaches."""

    @property
    @abstractmethod
    def gate(self) -> Gate:
        """What holds the api's signatures."""

    @property
    @abstractmethod
    def archive(self) -> Path:
        """The apps' file archive."""

    @abstractmethod
    def receiver_client(self) -> AbstractAsyncContextManager[httpx.AsyncClient]:
        """A client of our receiver at its public URL."""

    @abstractmethod
    async def handled(self, consumer: str) -> float:
        """How many events the worker's ``consumer`` has handled."""


def posting(owner: TestPrincipal, company: str, key: str) -> dict[str, str]:
    return owner.posting(key) | {"Ledger-Company": company}


def reading(owner: TestPrincipal, company: str) -> dict[str, str]:
    return owner.bearer() | {"Ledger-Company": company, "Ledger-Mode": "test"}


async def installed(client: httpx.AsyncClient, owner: TestPrincipal, company: str, holder: Issued) -> str:
    """``holder``'s bundle installed in the signer for the company (test mode): its certificate's id."""
    headers = posting(owner, company, f"certificate-{holder.certificate.serial_number}")
    headers |= {UNLOCK_HEADER: UNLOCK, "Content-Type": PKCS12}
    response = await client.post(
        "/v1/certificates", headers=headers, content=bundle(holder, password=PASSWORD)
    )
    assert response.status_code == HTTPStatus.CREATED, response.text
    return str(document(response)["id"])


async def taxpayer(stack: Stack, owner: TestPrincipal, rnc: str, holder: Issued) -> str:
    """A company of ``rnc`` ready to issue and receive on do-ecf-v1 in TesteCF: its public id.

    Registered at its fiscal address, its certificate installed, enrolled with ranges of 31 and 32 (then
    active), buyer authentication on (our receiver requires a token), listed at our receiver.
    """
    client = stack.client
    registration = {
        "jurisdiction": {"country": "DO"},
        "functional_currency": "DOP",
        "tax_ids": [{"scheme": "do.rnc", "value": rnc}],
        "legal_name": NAMES[rnc],
        "address": ADDRESS,
    }
    company = await created(client, "/v1/companies", registration, owner.posting(f"company-{rnc}"))
    await installed(client, owner, company, holder)
    enrollment = await created(
        client,
        "/v1/fiscal/enrollments",
        {"channel": "do-ecf-v1", "environment": ENVIRONMENT},
        posting(owner, company, f"enroll-{rnc}"),
    )
    for ecf_type in ("31", "32"):
        ranged = {"environment": ENVIRONMENT, "ecf_type": ecf_type, "first": 1, "last": 100}
        await created(
            client,
            "/v1/do/numbering_ranges",
            ranged | {"expires_on": "2028-12-31"},
            posting(owner, company, f"range-{rnc}-{ecf_type}"),
        )
    reviewed = await client.get(f"/v1/fiscal/enrollments/{enrollment}", headers=reading(owner, company))
    assert document(reviewed)["status"] == "active", reviewed.text
    options = await client.put(
        "/v1/do/delivery_options", json={"buyer_authentication": True}, headers=reading(owner, company)
    )
    assert options.status_code == HTTPStatus.OK, options.text
    listed = receiver_of_ours(rnc, name=NAMES[rnc], public_url=RECEIVER_URL, environment=ENVIRONMENT)
    stack.fake.state.register(ENVIRONMENT, listed)
    return company


async def issued(
    stack: Stack, owner: TestPrincipal, company: str, body: Mapping[str, object], key: str = "invoice"
) -> dict[str, object]:
    """``body`` as ``POST /v1/invoices`` answered it (201)."""
    response = await stack.client.post("/v1/invoices", json=dict(body), headers=posting(owner, company, key))
    assert response.status_code == HTTPStatus.CREATED, response.text
    return document(response)


def fiscal_of(invoice: Mapping[str, object]) -> tuple[dict[str, JsonValue], dict[str, JsonValue]]:
    """The invoice's one ``fiscal[]`` entry, and do-ecf-v1's details in it (under ``do``)."""
    (entry,) = FISCAL.validate_python(invoice["fiscal"])
    return entry, OBJECT.validate_python(entry["do"])


async def drained(
    stack: Stack, owner: TestPrincipal, company: str, invoice: object, until: Settled
) -> dict[str, JsonValue]:
    """The invoice's do-ecf-v1 details once ``until(entry, details)`` holds, the worker draining meanwhile."""
    found: list[dict[str, JsonValue]] = []

    async def settled() -> bool:
        read = await stack.client.get(f"/v1/invoices/{invoice}", headers=reading(owner, company))
        entry, details = fiscal_of(document(read))
        found[:] = [details]
        return bool(until(entry, details))

    await eventually(settled, within=DRAINING)
    return found[0]


def accepted(entry: Mapping[str, JsonValue], details: Mapping[str, JsonValue]) -> bool:
    del details
    return entry["status"] == "accepted"


def delivered(entry: Mapping[str, JsonValue], details: Mapping[str, JsonValue]) -> bool:
    return accepted(entry, details) and details.get("delivery") == "delivered"


def summarized(entry: Mapping[str, JsonValue], details: Mapping[str, JsonValue]) -> bool:
    return accepted(entry, details) and details.get("delivery") == "not_applicable"


def approved(entry: Mapping[str, JsonValue], details: Mapping[str, JsonValue]) -> bool:
    del entry
    approval = details.get("commercial_approval")
    return isinstance(approval, dict) and approval.get("approved") is True


async def scalar(admin: psycopg.AsyncConnection, query: LiteralString, *parameters: object) -> int:
    cursor = await admin.execute(query, parameters)
    row = await cursor.fetchone()
    assert row is not None
    return COUNT.validate_python(row[0])


async def rows(
    admin: psycopg.AsyncConnection, query: LiteralString, *parameters: object
) -> list[tuple[str, ...]]:
    cursor = await admin.execute(query, parameters)
    return ROWS.validate_python(await cursor.fetchall())


async def until(check: Callable[[], Awaitable[bool]]) -> None:
    await eventually(check, within=DRAINING)


# ── An invoice to an electronic buyer of ours, from issue to commercial approval ─


async def test_an_invoice_is_accepted_delivered_to_our_buyer_approved_by_it_and_printed(
    stack: Stack, owner: TestPrincipal, certificates: dict[str, Issued]
) -> None:
    seller = await taxpayer(stack, owner, ISSUER_RNC, certificates[ISSUER_RNC])
    buyer = await taxpayer(stack, owner, BUYER_RNC, certificates[BUYER_RNC])
    invoice = await issued(stack, owner, seller, TO_THE_BUYER)
    entry, details = fiscal_of(invoice)
    assert (entry["channel"], entry["environment"], entry["status"]) == ("do-ecf-v1", ENVIRONMENT, "pending")
    security_code, qr_url = details["security_code"], details["qr_url"]
    assert details["encf"] == FIRST_31
    assert isinstance(security_code, str)
    assert len(security_code) == 6
    assert isinstance(qr_url, str)
    assert qr_url.startswith("https://ecf.fake.test/testecf/")

    after = await drained(stack, owner, seller, invoice["id"], delivered)
    assert stack.fake.state.submissions(FIRST_31) == 1  # sent once, accepted, never again
    assert after["security_code"] == security_code

    listing = await stack.client.get("/v1/do/received_documents", headers=reading(owner, buyer))
    (received,) = page(listing)["items"]
    assert (received["encf"], received["issuer_rnc"], received["security_code"]) == (
        FIRST_31,
        ISSUER_RNC,
        security_code,
    )
    seller_id = parse_id(COMPANY_PREFIX, seller)
    (receipt_file,) = stack.archive.glob(f"do-ecf-v1/test/*/{seller_id}/arecf/*.xml")
    receipt_xml = receipt_file.read_bytes()
    assert verify_enveloped(receipt_xml, STRICT).certificate == certificates[BUYER_RNC].certificate
    receipt = read_inbound_arecf(receipt_xml)
    assert (receipt.issuer_rnc, receipt.buyer_rnc, str(receipt.encf), receipt.received) == (
        ISSUER_RNC,
        BUYER_RNC,
        FIRST_31,
        True,
    )

    decided = await stack.client.post(
        f"/v1/do/received_documents/{received['id']}/approval",
        json={"decision": "approved"},
        headers=posting(owner, buyer, "approve"),
    )
    assert decided.status_code == HTTPStatus.ACCEPTED, decided.text
    mirrored = await drained(stack, owner, seller, invoice["id"], approved)
    assert OBJECT.validate_python(mirrored["commercial_approval"])["approved"] is True
    (to_dgii,) = stack.fake.state.dgii_approvals
    assert texts(to_dgii, "DetalleAprobacionComercial/eNCF", "DetalleAprobacionComercial/RNCComprador") == (
        FIRST_31,
        BUYER_RNC,
    )

    printed = await stack.client.get(
        f"/v1/invoices/{invoice['id']}/representation",
        params={"channel": "do-ecf-v1"},
        headers=reading(owner, seller),
    )
    assert printed.status_code == HTTPStatus.OK, printed.text
    assert printed.headers["content-type"] == "application/pdf"
    text = "".join(each.extract_text() for each in PdfReader(io.BytesIO(printed.content)).pages)
    assert FIRST_31 in text
    assert security_code in text


async def test_a_consumer_invoice_goes_as_a_summary_accepted_at_once_with_nobody_to_deliver_to(
    stack: Stack, owner: TestPrincipal, certificates: dict[str, Issued]
) -> None:
    seller = await taxpayer(stack, owner, ISSUER_RNC, certificates[ISSUER_RNC])
    invoice = await issued(stack, owner, seller, TO_A_CONSUMER)
    assert fiscal_of(invoice)[1]["encf"] == FIRST_32
    after = await drained(stack, owner, seller, invoice["id"], summarized)
    assert after["delivery"] == "not_applicable"
    (summary,) = stack.fake.received
    assert (summary.kind, summary.encf, summary.host) == ("rfce", FIRST_32, "fc.fake.test")


# ── The worker finishes what the request could not ─────────────────────────────


async def test_a_signer_too_slow_for_the_request_leaves_the_signature_to_the_worker_which_sends_it(
    stack: Stack, owner: TestPrincipal, certificates: dict[str, Issued], admin: psycopg.AsyncConnection
) -> None:
    seller = await taxpayer(stack, owner, ISSUER_RNC, certificates[ISSUER_RNC])
    stack.gate.held = asyncio.Event()
    invoice = await issued(stack, owner, seller, TO_A_CONSUMER)
    details = fiscal_of(invoice)[1]
    assert (details["encf"], details.get("security_code")) == (FIRST_32, None)
    after = await drained(stack, owner, seller, invoice["id"], accepted)
    security_code = after["security_code"]
    assert isinstance(security_code, str)
    assert len(security_code) == 6
    assert stack.fake.state.submissions(FIRST_32) == 1
    signed = await rows(admin, "SELECT state FROM do_ecf.documents WHERE ecf_type = '32'")
    assert signed == [("accepted",)]


async def test_a_document_parked_for_its_certificate_is_woken_by_the_signer_s_next_and_sent(
    stack: Stack,
    owner: TestPrincipal,
    certificates: dict[str, Issued],
    replacement: Issued,
    admin: psycopg.AsyncConnection,
) -> None:
    """The signer's event goes all the way: its outbox, the worker's fan-out, fiscal's inbox, the consumer."""
    company = await taxpayer(stack, owner, PARKED_RNC, certificates[PARKED_RNC])
    (first,) = page(await stack.client.get("/v1/certificates", headers=reading(owner, company)))["items"]
    retired = await stack.client.delete(f"/v1/certificates/{first['id']}", headers=reading(owner, company))
    assert retired.status_code == HTTPStatus.OK, retired.text
    invoice = await issued(stack, owner, company, TO_A_CONSUMER)
    assert fiscal_of(invoice)[1].get("security_code") is None

    async def parked() -> bool:
        found = await rows(
            admin, "SELECT status, coalesce(parked_reason, '') FROM do_ecf.jobs WHERE kind = 'do_ecf.advance'"
        )
        return found == [("parked", "certificate")]

    await until(parked)
    assert stack.fake.received == []
    await installed(stack.client, owner, company, replacement)
    await drained(stack, owner, company, invoice["id"], accepted)
    assert stack.fake.state.submissions(FIRST_32) == 1

    async def both_handled() -> bool:  # the first certificate's announcement, and its replacement's
        return await stack.handled("fiscal.certificates") == 2

    await until(both_handled)
    left = await scalar(admin, "SELECT count(*) FROM fiscal.inbox WHERE consumer = 'fiscal.certificates'")
    jobs = await rows(admin, "SELECT status FROM do_ecf.jobs WHERE kind = 'do_ecf.advance'")
    assert (left, jobs) == (0, [("done",)])  # nothing failed; only fiscal's resume wakes a job parked so


async def test_a_document_whose_answer_was_lost_is_never_sent_again_and_settles_by_its_track_id(
    stack: Stack, owner: TestPrincipal, certificates: dict[str, Issued], admin: psycopg.AsyncConnection
) -> None:
    """The fake takes the e-CF and loses its answer: the worker asks DGII for its track ids after the settle
    delay (brought forward here), adopts the one it finds and polls it; the e-CF is sent once."""
    seller = await taxpayer(stack, owner, ISSUER_RNC, certificates[ISSUER_RNC])
    stack.fake.script.by_encf[FIRST_31] = Scripted(break_after_send=True)
    invoice = await issued(stack, owner, seller, TO_THE_BUYER)

    async def unknown() -> bool:
        found = await rows(admin, MARKED)
        jobs = await scalar(admin, SETTLING)
        return found == [("",)] and jobs == 1

    await until(unknown)
    assert stack.fake.state.submissions(FIRST_31) == 1
    await admin.execute(
        "UPDATE do_ecf.jobs SET run_at = now() WHERE kind = 'do_ecf.advance' AND status = 'ready'"
    )
    await drained(stack, owner, seller, invoice["id"], accepted)
    assert stack.fake.state.submissions(FIRST_31) == 1
    (track_id,) = await rows(admin, "SELECT track_id FROM do_ecf.documents WHERE ecf_type = '31'")
    assert track_id == (stack.fake.received[0].track_id,)


# ── An e-CF another taxpayer sends one of our companies ────────────────────────


async def test_an_e_cf_from_another_taxpayer_is_received_and_its_commercial_approval_sent_out(
    stack: Stack, owner: TestPrincipal, certificates: dict[str, Issued]
) -> None:
    """DGII's Paso 9-like reception through our receiver; the approval goes to the emitter's receiver (DGII's
    simulator in TesteCF, for a taxpayer it lists no receiver of) and to DGII."""
    buyer = await taxpayer(stack, owner, BUYER_RNC, certificates[BUYER_RNC])
    draft = credit_invoice(
        7,
        issuer=Issuer(rnc=SUPPLIER_RNC, legal_name=NAMES[SUPPLIER_RNC], address="CALLE 1, LA ROMANA"),
        buyer=Buyer(rnc=BUYER_RNC, name=NAMES[BUYER_RNC]),
    )
    sent = ecf.render(draft, compute_totals(draft), signed_at=SIGNED_AT)
    base = f"{RECEIVER_URL}/do-ecf-v1/{ENVIRONMENT}/{BUYER_RNC}"
    async with stack.receiver_client() as http:
        sender = Sender(http, KeyPairSigner.from_issued(certificates[SUPPLIER_RNC]))
        answer = await sender.send_ecf(base, sent, token=await sender.token(base))
    assert answer.status_code == HTTPStatus.OK, answer.text
    assert verify_enveloped(answer.content, STRICT).certificate == certificates[BUYER_RNC].certificate
    assert read_inbound_arecf(answer.content).received

    listing = await stack.client.get("/v1/do/received_documents", headers=reading(owner, buyer))
    (received,) = page(listing)["items"]
    assert (received["issuer_rnc"], received["encf"]) == (SUPPLIER_RNC, "E310000000007")
    decided = await stack.client.post(
        f"/v1/do/received_documents/{received['id']}/approval",
        json={"decision": "approved"},
        headers=posting(owner, buyer, "approve"),
    )
    assert decided.status_code == HTTPStatus.ACCEPTED, decided.text

    async def sent_out() -> bool:
        read = await stack.client.get(
            f"/v1/do/received_documents/{received['id']}", headers=reading(owner, buyer)
        )
        approval = OBJECT.validate_python(document(read)["approval"])
        emitter, dgii = OBJECT.validate_python(approval["emitter"]), OBJECT.validate_python(approval["dgii"])
        return (emitter["status"], dgii["status"]) == ("sent", "sent")

    await until(sent_out)
    (to_the_emitter,) = stack.fake.state.approvals
    (to_dgii,) = stack.fake.state.dgii_approvals
    assert to_the_emitter == to_dgii
    assert texts(to_dgii, "DetalleAprobacionComercial/RNCEmisor") == (SUPPLIER_RNC,)
