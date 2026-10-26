"""The receiver's authentication (spec §3.8, §6.3; Decisions 15, 17): seeds, their validation, its tokens.

An emitter (or DGII, certifying us) asks the company's receiver for a seed, signs it and sends it back; the
receiver answers a token for the documents it then sends. A seed is 32 random bytes, kept hashed for five
minutes and taken once; the signature must hold (``INBOUND``) under a certificate the trusted roots vouch
for, and the token is bound to the company, its environment and the RNCs that certificate names (a DGII
stand-in, by its fingerprint, in the certification environments only). A token is kept hashed for an hour.
The company is the path's: an RNC no company holds, or one not enrolled in the path's environment (in the
mode the environment serves), is 404. A refusal echoes nothing of the request.
"""

import base64
import hashlib
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from pathlib import Path
from typing import Final, Protocol

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.x509.oid import NameOID
from lxml import etree
from pydantic import TypeAdapter
lazy import httpx
lazy import psycopg

from ledger_country_do.channels.ecf_v1.adapters.signing.verifier import STRICT, verify_enveloped
from ledger_country_do.channels.ecf_v1.adapters.xml.inbound import read_inbound_arecf
from ledger_country_do_testing.local_signer import KeyPairSigner
from ledger_country_do_testing.pki import organization
from ledger_country_do_testing.samples import SIGNER_VALID_FROM
from ledger_fiscal_kit.xml.hardened import parser
from ledger_testing.pki import Authority
lazy from ledger_kernel.tenancy import Tenant
lazy from ledger_testing.clock import ManualClock

RECEIVING_RNC: Final = "131098193"
OTHER_RNC: Final = "131880738"
DGII_RNC: Final = "401506254"
SEED: Final = "autenticacion/api/semilla"
VALIDATION: Final = "autenticacion/api/validacioncertificado"
RECEPTION: Final = "recepcion/api/ecf"
GOLDEN: Final = Path(__file__).parents[1] / "contract" / "golden"
ANSWER: Final = TypeAdapter(dict[str, object])
ROWS: Final = TypeAdapter(list[tuple[object, ...]])
TOKENS_QUERY: Final = """
    SELECT token_hash, company_id, mode, environment, senders, stand_in, expires_at
    FROM do_ecf.receiver_tokens
"""
SEED_BYTES: Final = 32
NOW: Final = datetime(2026, 10, 13, 13, 30, tzinfo=UTC)
"""The receiver's clock (the conftest's ``clock``)."""


class Enroll(Protocol):
    async def __call__(self, rnc: str, environment: str = "testecf") -> Tenant: ...


class Authenticate(Protocol):
    async def __call__(
        self, signer: KeyPairSigner, *, rnc: str = RECEIVING_RNC, environment: str = "testecf"
    ) -> httpx.Response: ...


def path(route: str, *, rnc: str = RECEIVING_RNC, environment: str = "testecf") -> str:
    return f"/do-ecf-v1/{environment}/{rnc}/fe/{route}"


def upload(document: bytes) -> dict[str, tuple[str, bytes, str]]:
    return {"xml": ("documento.xml", document, "text/xml")}


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def token_of(response: httpx.Response) -> str:
    assert response.status_code == HTTPStatus.OK, response.text
    token = ANSWER.validate_json(response.content)["token"]
    assert isinstance(token, str)
    return token


def seed_value(seed: bytes) -> str:
    root = etree.fromstring(seed, parser())
    return root.findtext("valor") or ""


def refused(response: httpx.Response, status: HTTPStatus) -> None:
    """``response`` is a bare problem of ``status``: its code and title, nothing of the request."""
    assert response.status_code == status, response.text
    assert set(ANSWER.validate_json(response.content)) <= {"type", "title", "status", "code", "request_id"}


@pytest.fixture(scope="module")
def dgii(authority: Authority) -> KeyPairSigner:
    """DGII's certificate as it signs during certification (Pasos 9, 11): it names DGII, not the issuer."""
    issued = authority.issue(organization(DGII_RNC, name="DGII"), not_before=SIGNER_VALID_FROM)
    return KeyPairSigner.from_issued(issued)


@pytest.fixture
def standins(dgii: KeyPairSigner) -> str:
    """DGII's certificate is a stand-in sender of this receiver (Decision 17)."""
    return dgii.certificate.fingerprint(hashes.SHA256()).hex()


async def test_a_seed_is_32_random_bytes_kept_hashed_for_five_minutes(
    receiving: Tenant,
    receiver_client: httpx.AsyncClient,
    admin: psycopg.AsyncConnection,
    clock: ManualClock,
) -> None:
    first = await receiver_client.get(path(SEED))
    second = await receiver_client.get(path(SEED))
    assert first.status_code == HTTPStatus.OK, first.text
    assert first.headers["content-type"].startswith("application/xml")
    root = etree.fromstring(first.content, parser())
    assert [child.tag for child in root] == ["valor", "fecha"]
    assert len(base64.b64decode(seed_value(first.content), validate=True)) == SEED_BYTES
    assert seed_value(first.content) != seed_value(second.content)
    assert datetime.fromisoformat(root.findtext("fecha") or "") == clock()
    found = await admin.execute(
        "SELECT seed_hash, company_id, environment, expires_at, used_at FROM do_ecf.receiver_seeds"
    )
    rows = ROWS.validate_python(await found.fetchall())
    assert len(rows) == 2
    assert {row[0] for row in rows} == {
        hashlib.sha256(seed_value(each.content).encode()).digest() for each in (first, second)
    }
    assert {(row[1], row[2], row[3], row[4]) for row in rows} == {
        (receiving.company, "testecf", clock() + timedelta(minutes=5), None)
    }


async def test_a_seed_signed_by_the_sender_is_exchanged_for_a_token_kept_hashed_for_an_hour(
    receiving: Tenant,
    authenticate: Authenticate,
    sender: KeyPairSigner,
    receiver_client: httpx.AsyncClient,
    admin: psycopg.AsyncConnection,
) -> None:
    validated = await authenticate(sender)
    token = token_of(validated)
    answer = ANSWER.validate_json(validated.content)
    assert set(answer) == {"token", "expira", "expedido"}
    assert (answer["expedido"], answer["expira"]) == ("2026-10-13T13:30:00Z", "2026-10-13T14:30:00Z")
    found = await admin.execute(TOKENS_QUERY)
    assert await found.fetchall() == [
        (
            hashlib.sha256(token.encode()).digest(),
            receiving.company,
            "test",
            "testecf",
            ["101850043"],
            False,
            NOW + timedelta(hours=1),
        )
    ]
    used = await admin.execute("SELECT used_at FROM do_ecf.receiver_seeds")
    assert await used.fetchall() == [(NOW,)]
    # The token opens the company's reception: what it sends is read next (an empty form is no document).
    refused(await receiver_client.post(path(RECEPTION), headers=bearer(token)), HTTPStatus.BAD_REQUEST)


@pytest.mark.usefixtures("receiving")
async def test_the_token_is_answered_in_xml_when_the_sender_asks_for_it(
    receiver_client: httpx.AsyncClient, sender: KeyPairSigner
) -> None:
    seed = await receiver_client.get(path(SEED))
    validated = await receiver_client.post(
        path(VALIDATION), files=upload(sender.sign(seed.content)), headers={"Accept": "application/xml"}
    )
    assert validated.status_code == HTTPStatus.OK, validated.text
    root = etree.fromstring(validated.content, parser())
    assert [child.tag for child in root] == ["token", "expira", "expedido"]
    assert (root.findtext("expira"), root.findtext("expedido")) == (
        "2026-10-13T14:30:00Z",
        "2026-10-13T13:30:00Z",
    )


@pytest.mark.usefixtures("receiving")
async def test_a_seed_serves_once(receiver_client: httpx.AsyncClient, sender: KeyPairSigner) -> None:
    signed = sender.sign((await receiver_client.get(path(SEED))).content)
    token_of(await receiver_client.post(path(VALIDATION), files=upload(signed)))
    refused(await receiver_client.post(path(VALIDATION), files=upload(signed)), HTTPStatus.UNAUTHORIZED)


@pytest.mark.usefixtures("receiving")
async def test_a_seed_expires_after_five_minutes(
    receiver_client: httpx.AsyncClient, sender: KeyPairSigner, clock: ManualClock
) -> None:
    signed = sender.sign((await receiver_client.get(path(SEED))).content)
    clock.advance(timedelta(minutes=6))
    refused(await receiver_client.post(path(VALIDATION), files=upload(signed)), HTTPStatus.UNAUTHORIZED)


@pytest.mark.usefixtures("receiving")
async def test_a_seed_is_the_company_s_and_the_environment_s_that_gave_it(
    receiver_client: httpx.AsyncClient, sender: KeyPairSigner, enroll: Enroll
) -> None:
    await enroll(OTHER_RNC)
    await enroll(RECEIVING_RNC, "ecf")
    for elsewhere in (path(VALIDATION, rnc=OTHER_RNC), path(VALIDATION, environment="ecf")):
        signed = sender.sign((await receiver_client.get(path(SEED))).content)
        refused(await receiver_client.post(elsewhere, files=upload(signed)), HTTPStatus.UNAUTHORIZED)


@pytest.mark.usefixtures("receiving")
async def test_a_seed_signed_under_a_root_not_trusted_is_refused(receiver_client: httpx.AsyncClient) -> None:
    stranger = Authority.root(now=SIGNER_VALID_FROM).issue(
        organization("101850043"), not_before=SIGNER_VALID_FROM
    )
    signed = KeyPairSigner.from_issued(stranger).sign((await receiver_client.get(path(SEED))).content)
    refused(await receiver_client.post(path(VALIDATION), files=upload(signed)), HTTPStatus.UNAUTHORIZED)


@pytest.mark.usefixtures("receiving")
async def test_a_seed_signed_by_a_certificate_naming_no_rnc_or_cedula_is_refused(
    receiver_client: httpx.AsyncClient, authority: Authority
) -> None:
    """The token is the sender's: a certificate that names nobody (no stand-in) sends for nobody."""
    nobody = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "ALGUIEN")])
    signer = KeyPairSigner.from_issued(authority.issue(nobody, not_before=SIGNER_VALID_FROM))
    signed = signer.sign((await receiver_client.get(path(SEED))).content)
    refused(await receiver_client.post(path(VALIDATION), files=upload(signed)), HTTPStatus.UNAUTHORIZED)


@pytest.mark.usefixtures("receiving")
async def test_a_seed_changed_after_it_was_signed_is_refused(
    receiver_client: httpx.AsyncClient, sender: KeyPairSigner
) -> None:
    seed = (await receiver_client.get(path(SEED))).content
    signed = sender.sign(seed).replace(b"<valor>", b"<valor>A", 1)
    refused(await receiver_client.post(path(VALIDATION), files=upload(signed)), HTTPStatus.UNAUTHORIZED)


@pytest.mark.usefixtures("receiving")
@pytest.mark.parametrize(
    "document",
    [
        b"not xml",
        b'<!DOCTYPE SemillaModel [<!ENTITY e "x">]><SemillaModel><valor>&e;</valor></SemillaModel>',
        b"<SemillaModel><valor>v</valor><fecha>2026-10-13T09:30:00-04:00</fecha></SemillaModel>",
        b"<ECF/>",
    ],
    ids=["not-xml", "doctype", "unsigned", "another-root"],
)
async def test_what_is_no_signed_seed_is_a_bad_request(
    receiver_client: httpx.AsyncClient, document: bytes
) -> None:
    refused(await receiver_client.post(path(VALIDATION), files=upload(document)), HTTPStatus.BAD_REQUEST)


@pytest.mark.usefixtures("receiving")
async def test_a_validation_without_its_file_is_a_bad_request(receiver_client: httpx.AsyncClient) -> None:
    refused(await receiver_client.post(path(VALIDATION), content=b"<a/>"), HTTPStatus.BAD_REQUEST)
    refused(
        await receiver_client.post(path(VALIDATION), files={"other": ("x.xml", b"<a/>", "text/xml")}),
        HTTPStatus.BAD_REQUEST,
    )


async def test_a_token_opens_its_company_s_reception_only_until_it_expires(
    token: str, receiver_client: httpx.AsyncClient, enroll: Enroll, clock: ManualClock
) -> None:
    await enroll(OTHER_RNC)
    await enroll(RECEIVING_RNC, "ecf")
    for elsewhere in (path(RECEPTION, rnc=OTHER_RNC), path(RECEPTION, environment="ecf")):
        refused(await receiver_client.post(elsewhere, headers=bearer(token)), HTTPStatus.UNAUTHORIZED)
    refused(await receiver_client.post(path(RECEPTION), headers=bearer(token)), HTTPStatus.BAD_REQUEST)
    clock.advance(timedelta(hours=1))
    refused(await receiver_client.post(path(RECEPTION), headers=bearer(token)), HTTPStatus.UNAUTHORIZED)


@pytest.mark.usefixtures("receiving")
@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Basic abc"}, {"Authorization": "Bearer"}, {"Authorization": "Bearer nope"}],
    ids=["none", "basic", "empty", "unknown"],
)
async def test_reception_and_approval_need_a_token(
    receiver_client: httpx.AsyncClient, headers: dict[str, str]
) -> None:
    for route in (RECEPTION, "aprobacioncomercial/api/ecf"):
        response = await receiver_client.post(path(route), headers=headers)
        refused(response, HTTPStatus.UNAUTHORIZED)
        assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.usefixtures("receiving")
@pytest.mark.parametrize(
    "where",
    [
        path(SEED, rnc=OTHER_RNC),
        path(SEED, environment="certecf"),
        path(SEED, environment="ecf"),
        path(SEED, environment="prod"),
        path(SEED, rnc="13109819X"),
        path(SEED, rnc="1310981930"),
        path(SEED, rnc="1-31-09819-3"),
    ],
    ids=[
        "no-company",
        "not-enrolled-there",
        "other-mode",
        "no-environment",
        "letters",
        "ten-digits",
        "dashes",
    ],
)
async def test_a_company_the_receiver_does_not_receive_for_is_not_found(
    receiver_client: httpx.AsyncClient, where: str
) -> None:
    refused(await receiver_client.get(where), HTTPStatus.NOT_FOUND)


@pytest.mark.usefixtures("receiving")
async def test_dgii_s_paths_are_case_insensitive(receiver_client: httpx.AsyncClient) -> None:
    response = await receiver_client.get(f"/DO-ECF-V1/TestECF/{RECEIVING_RNC}/FE/Autenticacion/API/Semilla")
    assert response.status_code == HTTPStatus.OK, response.text


async def test_a_dgii_stand_in_is_trusted_in_the_certification_environments_only(
    enroll: Enroll,
    authenticate: Authenticate,
    dgii: KeyPairSigner,
    receiver_client: httpx.AsyncClient,
) -> None:
    """DGII signs what it sends as Pasos 9 and 11 with its own certificate, which names no issuer of the
    documents: its fingerprint stands in for any sender in ``testecf`` and ``certecf``, never in ``ecf``."""
    await enroll(RECEIVING_RNC, "certecf")
    await enroll(RECEIVING_RNC, "ecf")
    golden = (GOLDEN / "ecf-31.xml").read_bytes()
    signed = dgii.sign(golden)
    certifying = token_of(await authenticate(dgii, environment="certecf"))
    received = await receiver_client.post(
        path(RECEPTION, environment="certecf"), files=upload(signed), headers=bearer(certifying)
    )
    assert received.status_code == HTTPStatus.OK, received.text
    verify_enveloped(received.content, STRICT)
    assert read_inbound_arecf(received.content).received
    live = token_of(await authenticate(dgii, environment="ecf"))
    refused(
        await receiver_client.post(
            path(RECEPTION, environment="ecf"), files=upload(signed), headers=bearer(live)
        ),
        HTTPStatus.UNAUTHORIZED,
    )
