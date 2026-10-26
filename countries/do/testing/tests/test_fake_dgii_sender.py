from typing import Final

import pytest
lazy import httpx

from ledger_country_do.channels.ecf_v1.adapters.xml import ecf
from ledger_country_do.channels.ecf_v1.domain.totals import compute_totals
from ledger_country_do_testing.fake_dgii.app import DEFAULT_PUBLIC_URL
from ledger_country_do_testing.fake_dgii.sender import Sender, SenderTokenRefusedError
from ledger_country_do_testing.local_signer import KeyPairSigner
from ledger_country_do_testing.pki import person
from ledger_country_do_testing.samples import SIGNED_AT, SIGNER_VALID_FROM, credit_invoice
from ledger_fiscal_kit.xml.hardened import texts
from ledger_testing.pki import Authority
lazy from ledger_country_do_testing.fake_dgii.app import FakeDgii

BASE: Final = f"{DEFAULT_PUBLIC_URL}/testecf/emisorreceptor"
"""The fake's own emisor-receptor: a receiver as any other, for the sender to drive."""
ACECF: Final = (
    b'<?xml version="1.0" encoding="utf-8"?><ACECF><DetalleAprobacionComercial><Version>1.0</Version>'
    b"<RNCEmisor>101850043</RNCEmisor><eNCF>E310000000001</eNCF><FechaEmision>13-10-2026</FechaEmision>"
    b"<MontoTotal>4887.58</MontoTotal><RNCComprador>131098193</RNCComprador><Estado>1</Estado>"
    b"<FechaHoraAprobacionComercial>13-10-2026 10:00:00</FechaHoraAprobacionComercial>"
    b"</DetalleAprobacionComercial></ACECF>"
)


def credit_invoice_xml() -> bytes:
    draft = credit_invoice()
    return ecf.render(draft, compute_totals(draft), signed_at=SIGNED_AT)


async def test_a_sender_authenticates_then_sends_an_e_cf_signed_and_named_as_dgii_names_it(
    http: httpx.AsyncClient, signer: KeyPairSigner, fake: FakeDgii
) -> None:
    sender = Sender(http, signer)
    token = await sender.token(BASE)
    answer = await sender.send_ecf(BASE, credit_invoice_xml(), token=token)
    assert answer.status_code == 200
    assert texts(answer.content, "DetalleAcusedeRecibo/Estado") == ("0",)
    (received,) = fake.received
    assert (received.kind, received.file_name) == ("buyer", "101850043E310000000001.xml")
    assert b"<X509Certificate>" in received.document  # signed by the sender before it was sent


async def test_a_sender_sends_a_commercial_approval_signed(
    http: httpx.AsyncClient, signer: KeyPairSigner, fake: FakeDgii
) -> None:
    sender = Sender(http, signer)
    answer = await sender.send_acecf(BASE, ACECF, token=await sender.token(BASE))
    assert answer.status_code == 200
    (approval,) = fake.state.approvals
    assert texts(approval, "DetalleAprobacionComercial/eNCF") == ("E310000000001",)
    assert b"<X509Certificate>" in approval


async def test_a_sender_without_a_token_is_answered_as_the_receiver_answers_it(
    http: httpx.AsyncClient, signer: KeyPairSigner
) -> None:
    sender = Sender(http, signer)
    assert (await sender.send_ecf(BASE, credit_invoice_xml(), token=None)).status_code == 401
    assert (await sender.send_acecf(BASE, ACECF, token=None)).status_code == 401


async def test_a_sender_the_receiver_does_not_trust_gets_no_token(http: httpx.AsyncClient) -> None:
    stranger = KeyPairSigner.from_issued(
        Authority.root(now=SIGNER_VALID_FROM).issue(person(), not_before=SIGNER_VALID_FROM)
    )
    with pytest.raises(SenderTokenRefusedError, match="refused the sender's signed seed: 400"):
        await Sender(http, stranger).token(BASE)


async def test_a_receiver_out_of_reach_gives_the_sender_no_seed(
    http: httpx.AsyncClient, signer: KeyPairSigner, fake: FakeDgii
) -> None:
    fake.script.outage = 1
    with pytest.raises(SenderTokenRefusedError, match="refused the sender's seed request: 503"):
        await Sender(http, signer).token(BASE)
