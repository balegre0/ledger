import asyncio
import socket
from dataclasses import dataclass, replace
from datetime import timedelta
from decimal import Decimal
from ipaddress import IPv4Address, ip_address
from typing import TYPE_CHECKING, Final, final, override
lazy from collections.abc import AsyncIterator, Sequence
lazy from ipaddress import IPv6Address

import httpx
import pytest

from ledger_country_do.channels.ecf_v1.adapters.buyers import client as buyers_client
from ledger_country_do.channels.ecf_v1.adapters.buyers.approvals import (
    APPROVAL_PATH,
    ApprovalRefused,
    ApprovalSent,
)
from ledger_country_do.channels.ecf_v1.adapters.buyers.client import (
    RECEPTION_PATH,
    SEED_PATH,
    VALIDATION_PATH,
    ArecfMismatchError,
    BuyerClient,
    BuyerRequiresAuthenticationError,
    BuyerUnavailableError,
    Delivered,
    DeliveryDocument,
    NotApplicable,
    ReceiptRefused,
)
from ledger_country_do.channels.ecf_v1.adapters.buyers.endpoints import BuyerEndpointRefusedError
from ledger_country_do.channels.ecf_v1.adapters.buyers.seeds import SeedSigned
from ledger_country_do.channels.ecf_v1.adapters.http import MAX_ANSWER, DgiiTimeouts
from ledger_country_do.channels.ecf_v1.adapters.signing.port import DocumentSigner
from ledger_country_do.channels.ecf_v1.adapters.signing.trust import (
    CounterpartyCertificateError,
    CounterpartyTrust,
)
from ledger_country_do.channels.ecf_v1.adapters.signing.verifier import SignatureInvalidError
from ledger_country_do.channels.ecf_v1.adapters.xml import ecf
from ledger_country_do.channels.ecf_v1.adapters.xml.acecf import render_acecf
from ledger_country_do.channels.ecf_v1.adapters.xml.inbound import InboundDocumentInvalidError
from ledger_country_do.channels.ecf_v1.application.ports import DirectoryEntry
from ledger_country_do.channels.ecf_v1.domain.drafts import Buyer
from ledger_country_do.channels.ecf_v1.domain.environments import Environment
from ledger_country_do.channels.ecf_v1.domain.exchanges import (
    CommercialApproval,
    ExchangeInvalidError,
    NotReceivedReason,
)
from ledger_country_do.channels.ecf_v1.domain.totals import compute_totals
from ledger_country_do_testing.fake_dgii.app import FakeDgii
from ledger_country_do_testing.fake_dgii.receiver import arecf
from ledger_country_do_testing.fake_dgii.state import dgii_check
from ledger_country_do_testing.local_signer import SIGNING_AS
from ledger_country_do_testing.pki import organization
from ledger_country_do_testing.samples import (
    BUYER,
    ISSUER,
    SIGNED_AT,
    SIGNER_VALID_FROM,
    credit_invoice,
)
from ledger_country_do_testing.signatures import sign_with_signxml
from ledger_country_do_testing.transports import FAKE_HOSTS, outbound_over
from ledger_fiscal_kit.ports import SignerPort
from ledger_fiscal_kit.xml.hardened import DoctypeRefusedError
from ledger_fiscal_kit.xml.schemas import SchemaViolationError
from ledger_kernel.errors import InvalidValueError
from ledger_platform.http.outbound import OutboundHttp
from ledger_testing.clock import ManualClock
from ledger_testing.pki import Authority
from ledger_testing.signer import TestSigner
lazy from ledger_country_do.channels.ecf_v1.adapters.buyers.client import Delivery
lazy from ledger_country_do.channels.ecf_v1.adapters.buyers.endpoints import Resolve
lazy from ledger_country_do.channels.ecf_v1.adapters.dgii.client import DgiiClient
lazy from ledger_country_do.channels.ecf_v1.adapters.dgii.directory import DirectoryLookup
lazy from ledger_country_do.channels.ecf_v1.application.ports import SigningAs
lazy from ledger_country_do.channels.ecf_v1.domain.drafts import EcfDraft
lazy from ledger_country_do.channels.ecf_v1.domain.exchanges import Receipt
lazy from ledger_country_do_testing.local_signer import KeyPairSigner
lazy from ledger_fiscal_kit.ports import CertificateValidity, SeedToSign, Signed, XmlToSign
lazy from ledger_kernel.tenancy import Tenant
lazy from ledger_platform.runtime.calls import SystemCall
lazy from ledger_testing.pki import Issued

if TYPE_CHECKING:
    from ledger_platform.http.outbound import OutboundTimeouts

T: Final = Environment.TESTECF
WHO: Final = SIGNING_AS
SIMULATOR: Final = f"{FAKE_HOSTS.ecf}/testecf/emisorreceptor"
SIMULATOR_HOST: Final = "ecf.fake.test"
PUBLIC: Final = IPv4Address("93.184.215.14")
"""A globally routable address the tests' names resolve to (nothing connects to it: in-process transports)."""
SEED: Final = b"<SemillaModel><valor>v</valor><fecha>f</fecha></SemillaModel>"
REFUSED: Final = "the connection was refused"
NO_SUCH_NAME: Final = "no such name"
TOKEN: Final = b'{"token": "buyer-token", "expira": "2026-10-13T14:30:00Z"}'


async def public_dns(host: str, port: int) -> Sequence[IPv4Address | IPv6Address]:
    del host, port
    return (PUBLIC,)


async def nobody(who: SigningAs, environment: Environment, rnc: str) -> DirectoryEntry | None:
    del who, environment, rnc
    return None


async def simulator(who: SigningAs, environment: Environment, rnc: str) -> DirectoryEntry | None:
    del who, environment
    return DirectoryEntry("EMISOR RECEPTOR DGII", rnc, SIMULATOR, SIMULATOR, SIMULATOR)


def listed(reception: str, authentication: str | None = None) -> DirectoryLookup:
    async def lookup(who: SigningAs, environment: Environment, rnc: str) -> DirectoryEntry | None:
        del who, environment
        return DirectoryEntry("COMPRADOR", rnc, reception, reception, authentication)

    return lookup


@final
class Recording(httpx.AsyncBaseTransport):
    """Passes every request on and keeps it."""

    __slots__ = ("_inner", "requests")

    def __init__(self, inner: httpx.AsyncBaseTransport) -> None:
        self._inner = inner
        self.requests: list[httpx.Request] = []

    @override
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return await self._inner.handle_async_request(request)


@pytest.fixture
def trust(authority: Authority) -> CounterpartyTrust:
    return CounterpartyTrust((authority.certificate,))


@final
@dataclass(frozen=True, slots=True)
class Party:
    """Who delivers: the issuer's signer, the policy its buyers' certificates answer to, and its clock."""

    signer: KeyPairSigner
    trust: CounterpartyTrust
    clock: ManualClock


@pytest.fixture
def party(signer: KeyPairSigner, trust: CounterpartyTrust, clock: ManualClock) -> Party:
    return Party(signer, trust, clock)


def over(
    party: Party,
    directory: DirectoryLookup,
    transport: httpx.AsyncBaseTransport | None,
    *,
    resolve: Resolve = public_dns,
    timeouts: DgiiTimeouts | None = None,
) -> BuyerClient:
    return BuyerClient(
        DocumentSigner(party.signer),
        directory,
        trust=party.trust,
        http=outbound_over(transport),
        resolve=resolve,
        clock=party.clock,
        timeouts=timeouts or DgiiTimeouts(),
    )


def answering(arecf_bytes: bytes) -> httpx.MockTransport:
    """A buyer that publishes no authentication and answers ``arecf_bytes`` at reception."""

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, stream=httpx.ByteStream(arecf_bytes))

    return httpx.MockTransport(handler)


@pytest.fixture
async def buyers(party: Party, dgii: DgiiClient, fake: FakeDgii) -> AsyncIterator[BuyerClient]:
    client = over(party, dgii.directory, httpx.ASGITransport(app=fake.app))
    async with client.opened():
        yield client


def of(draft: EcfDraft) -> DeliveryDocument:
    """What a receipt of ``draft`` must name."""
    assert draft.buyer.rnc is not None
    return DeliveryDocument(issuer_rnc=draft.issuer.rnc, buyer_rnc=draft.buyer.rnc, encf=draft.encf)


async def deliver(
    client: BuyerClient, draft: EcfDraft, signed: bytes, *, authenticate: bool = True
) -> Delivery:
    """``signed`` delivered as ``WHO`` for ``draft``, in TesteCF."""
    return await client.deliver(WHO, T, of(draft), signed, authenticate=authenticate)


def refused_for(delivery: Delivery) -> str:
    """Why the receipt was refused; fails unless it was."""
    assert isinstance(delivery, ReceiptRefused)
    return delivery.reason


async def signed(signer: KeyPairSigner, sequence: int = 1) -> bytes:
    draft = credit_invoice(sequence)
    return signer.sign(ecf.render(draft, compute_totals(draft), signed_at=SIGNED_AT))


def receipt(receiver: Issued, clock: ManualClock, **changes: str) -> bytes:
    """An ARECF for ``credit_invoice()``, signed by signxml as ``receiver``; ``changes``: other values."""
    named = {"issuer": ISSUER.rnc, "buyer": BUYER.rnc or "", "encf": "E310000000001"} | changes
    return sign_with_signxml(
        arecf(named["issuer"], named["buyer"], named["encf"], reason=None, at=clock()), receiver
    )


# -- The plan's cases ---------------------------------------------------------------------------------------


async def test_an_accepted_e_cf_reaches_the_buyer_who_signs_its_receipt(
    buyers: BuyerClient, signer: KeyPairSigner, fake: FakeDgii
) -> None:
    delivery = await deliver(buyers, credit_invoice(), await signed(signer))
    assert isinstance(delivery, Delivered)
    assert (delivery.received, delivery.reason) == (True, None)
    assert delivery.certificate == fake.state.receiver.certificate
    assert (fake.received[-1].kind, fake.received[-1].file_name) == ("buyer", "101850043E310000000001.xml")
    assert fake.received[-1].host == SIMULATOR_HOST


async def test_without_the_buyer_s_authentication_and_a_second_time(
    buyers: BuyerClient, signer: KeyPairSigner, fake: FakeDgii
) -> None:
    fake.state.receiver_requires_auth = False
    document = await signed(signer)
    first = await deliver(buyers, credit_invoice(), document)
    again = await deliver(buyers, credit_invoice(), document)
    assert isinstance(first, Delivered)
    assert isinstance(again, Delivered)
    assert (first.received, again.received, again.reason) == (True, False, 3)


async def test_a_receipt_whose_signature_fails_is_refused(
    buyers: BuyerClient, signer: KeyPairSigner, fake: FakeDgii
) -> None:
    fake.state.tamper_arecf = True
    delivery = await deliver(buyers, credit_invoice(), await signed(signer))
    assert refused_for(delivery) == SignatureInvalidError.code
    assert isinstance(delivery, ReceiptRefused)
    assert b"<Estado>1</Estado>" in delivery.arecf  # the bytes, for the archive
    assert len(delivery.seeds) == 1


async def test_a_receipt_about_another_document_is_refused(
    buyers: BuyerClient, signer: KeyPairSigner
) -> None:
    assert refused_for(await deliver(buyers, credit_invoice(2), await signed(signer, 1))) == (
        ArecfMismatchError.code
    )


async def test_delivery_does_not_apply_to_no_electronic_receiver_nor_through_another_s_entry(
    party: Party, fake: FakeDgii
) -> None:
    signer = TestSigner(sign=party.signer.sign, sign_seed=party.signer.sign)
    watched = Recording(httpx.ASGITransport(app=fake.app))
    for lookup, expected in (
        (nobody, NotApplicable("no_directory_entry")),
        (stranger, NotApplicable("entry_names_another_rnc")),
    ):
        client = BuyerClient(
            DocumentSigner(signer), lookup, trust=party.trust, http=outbound_over(watched), resolve=public_dns
        )
        async with client.opened():
            assert await deliver(client, credit_invoice(), b"") == expected
    assert (watched.requests, signer.calls) == ([], [])


async def test_a_buyer_that_fails_is_unavailable(party: Party, fake: FakeDgii) -> None:
    async with over(party, simulator, httpx.ASGITransport(app=fake.app)).opened() as client:
        fake.script.outage = 1
        with pytest.raises(BuyerUnavailableError, match="its seed"):
            await deliver(client, credit_invoice(), await signed(party.signer))


@pytest.mark.parametrize(
    ("answer", "step"),
    [
        (b"not json", "its authentication"),
        (b'{"token": "a b", "expira": "x"}', "its authentication"),
        (None, "its seed"),
    ],
    ids=["token-not-json", "token-not-a-bearer", "unreachable"],
)
async def test_a_buyer_whose_answers_cannot_be_read_or_reached_is_unavailable(
    party: Party, answer: bytes | None, step: str
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if answer is None:
            raise httpx.ConnectError(REFUSED, request=request)
        if request.url.path.endswith("/semilla"):
            return httpx.Response(200, stream=httpx.ByteStream(SEED))
        return httpx.Response(200, stream=httpx.ByteStream(answer))

    async with over(party, simulator, httpx.MockTransport(handler)).opened() as client:
        with pytest.raises(BuyerUnavailableError, match=step):
            await deliver(client, credit_invoice(), await signed(party.signer))


async def test_a_client_not_opened_delivers_nothing(party: Party) -> None:
    with pytest.raises(BuyerUnavailableError, match="not opened"):
        await deliver(over(party, simulator, None), credit_invoice(), await signed(party.signer))


# -- Endpoints from DGII's directory are the buyer's to write: attacker-controlled --------------------------


@pytest.mark.parametrize(
    "reception",
    [
        "http://buyer.example.com/fe",
        "https://user@buyer.example.com",
        "https://10.0.0.7",
        "https://[::1]:8443",
        "https://buyer.example.com/fe?next=1",
        "https://localhost",
    ],
    ids=["plain-http", "userinfo", "ip-literal", "ipv6-literal", "query", "single-label"],
)
async def test_a_buyer_endpoint_that_is_not_a_public_https_name_is_refused_before_anything_is_sent(
    party: Party, fake: FakeDgii, reception: str
) -> None:
    watched = Recording(httpx.ASGITransport(app=fake.app))
    async with over(party, listed(reception), watched).opened() as client:
        with pytest.raises(BuyerEndpointRefusedError):
            await deliver(client, credit_invoice(), await signed(party.signer))
    assert watched.requests == []


async def test_an_authentication_endpoint_is_checked_too_before_anything_is_sent(
    party: Party, fake: FakeDgii
) -> None:
    watched = Recording(httpx.ASGITransport(app=fake.app))
    lookup = listed(SIMULATOR, authentication="https://169.254.169.254/latest")
    async with over(party, lookup, watched).opened() as client:
        with pytest.raises(BuyerEndpointRefusedError):
            await deliver(client, credit_invoice(), await signed(party.signer))
    assert watched.requests == []


@pytest.mark.parametrize(
    "addresses",
    [
        ("127.0.0.1",),
        ("10.1.2.3",),
        ("169.254.169.254",),
        ("::1",),
        ("fd00::1",),
        ("93.184.215.14", "192.168.1.1"),
    ],
    ids=[
        "loopback",
        "private",
        "link-local-metadata",
        "v6-loopback",
        "v6-unique-local",
        "one-of-two-private",
    ],
)
async def test_a_name_that_resolves_to_a_private_address_is_refused_before_anything_is_sent(
    party: Party, fake: FakeDgii, addresses: tuple[str, ...]
) -> None:
    async def resolve(host: str, port: int) -> Sequence[IPv4Address | IPv6Address]:
        del host, port
        return tuple(ip_address(each) for each in addresses)

    watched = Recording(httpx.ASGITransport(app=fake.app))
    async with over(party, simulator, watched, resolve=resolve).opened() as client:
        with pytest.raises(BuyerEndpointRefusedError, match="resolves to an address"):
            await deliver(client, credit_invoice(), await signed(party.signer))
    assert watched.requests == []


async def test_every_request_goes_to_the_address_checked_its_name_kept_for_tls_and_host(
    party: Party, fake: FakeDgii
) -> None:
    watched = Recording(httpx.ASGITransport(app=fake.app))
    async with over(party, simulator, watched).opened() as client:
        assert isinstance(await deliver(client, credit_invoice(), await signed(party.signer)), Delivered)
    assert [request.url.path for request in watched.requests] == [
        f"/testecf/emisorreceptor{path}" for path in (SEED_PATH, VALIDATION_PATH, RECEPTION_PATH)
    ]
    for request in watched.requests:
        assert (request.url.scheme, request.url.host, request.url.port) == ("https", str(PUBLIC), None)
        assert request.headers["host"] == SIMULATOR_HOST
        assert request.extensions["sni_hostname"] == SIMULATOR_HOST


async def test_a_name_rebound_to_a_private_address_between_requests_is_refused(
    party: Party, fake: FakeDgii
) -> None:
    answers = iter([(PUBLIC,), (PUBLIC,), (IPv4Address("127.0.0.1"),)])

    async def rebinding(host: str, port: int) -> Sequence[IPv4Address | IPv6Address]:
        del host, port
        return next(answers)

    watched = Recording(httpx.ASGITransport(app=fake.app))
    async with over(party, simulator, watched, resolve=rebinding).opened() as client:
        with pytest.raises(BuyerEndpointRefusedError):
            await deliver(client, credit_invoice(), await signed(party.signer))
    assert [request.url.path.rsplit("/", 1)[-1] for request in watched.requests] == [
        "semilla",
        "validacioncertificado",
    ]
    assert fake.received == []


async def test_a_name_that_does_not_resolve_is_unavailable(party: Party, fake: FakeDgii) -> None:
    async def unknown(host: str, port: int) -> Sequence[IPv4Address | IPv6Address]:
        del host, port
        raise socket.gaierror(socket.EAI_NONAME, NO_SUCH_NAME)

    async with over(party, simulator, httpx.ASGITransport(app=fake.app), resolve=unknown).opened() as c:
        with pytest.raises(BuyerUnavailableError, match="its seed"):
            await deliver(c, credit_invoice(), await signed(party.signer))


async def test_a_redirect_is_not_followed(party: Party) -> None:
    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request.url.path)
        return httpx.Response(
            302, headers={"Location": "https://127.0.0.1/admin"}, stream=httpx.ByteStream(b"")
        )

    lookup = listed(SIMULATOR)
    async with over(party, lookup, httpx.MockTransport(handler)).opened() as client:
        with pytest.raises(BuyerUnavailableError, match="its reception"):
            await deliver(client, credit_invoice(), await signed(party.signer))
    assert sent == [f"/testecf/emisorreceptor{RECEPTION_PATH}"]


async def test_an_answer_over_the_limit_is_refused_as_it_streams(party: Party) -> None:
    transport = answering(b"<" + b"a" * MAX_ANSWER + b"/>")
    async with over(party, listed(SIMULATOR), transport).opened() as client:
        with pytest.raises(BuyerUnavailableError, match="its reception"):
            await deliver(client, credit_invoice(), await signed(party.signer))


async def test_a_buyer_that_stalls_is_given_up_on(party: Party) -> None:
    async def stalls(request: httpx.Request) -> httpx.Response:
        del request
        await asyncio.sleep(5)
        return httpx.Response(200, stream=httpx.ByteStream(b""))

    timeouts = DgiiTimeouts(connect=0.5, total=0.5, call=5.0)
    transport = httpx.MockTransport(stalls)
    async with over(party, listed(SIMULATOR), transport, timeouts=timeouts).opened() as client:
        with pytest.raises(BuyerUnavailableError, match="its reception"):
            await deliver(client, credit_invoice(), await signed(party.signer))


async def test_a_delivery_has_an_overall_deadline(party: Party) -> None:
    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.5)
        if request.url.path.endswith("/semilla"):
            return httpx.Response(200, stream=httpx.ByteStream(SEED))
        return httpx.Response(200, stream=httpx.ByteStream(TOKEN))

    # The seed answers at 0.5 s, well within an attempt (1 s) and the deadline (0.8 s); the authentication
    # would answer at 1 s, past the deadline: each margin is 0.3 s or more, whatever the machine's load.
    timeouts = DgiiTimeouts(connect=1.0, total=1.0, call=0.8)
    transport = httpx.MockTransport(slow)
    async with over(party, simulator, transport, timeouts=timeouts).opened() as client:
        with pytest.raises(BuyerUnavailableError, match="its authentication"):
            await deliver(client, credit_invoice(), await signed(party.signer))


async def test_a_hostile_seed_is_never_signed(party: Party) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200, stream=httpx.ByteStream(b'<!DOCTYPE s [<!ENTITY e "x">]><SemillaModel>&e;</SemillaModel>')
        )

    async with over(party, simulator, httpx.MockTransport(handler)).opened() as client:
        with pytest.raises(BuyerUnavailableError, match="its seed"):
            await deliver(client, credit_invoice(), await signed(party.signer))


# -- The receipt: schema, signature, contents, and its certificate under the trust policy --------------------


async def test_a_receipt_with_a_doctype_is_refused_unread(party: Party) -> None:
    transport = answering(b'<?xml version="1.0"?><!DOCTYPE ARECF SYSTEM "http://x/"><ARECF/>')
    async with over(party, listed(SIMULATOR), transport).opened() as client:
        delivery = await deliver(client, credit_invoice(), await signed(party.signer))
    assert refused_for(delivery) == DoctypeRefusedError.code


async def test_a_receipt_dgii_s_schema_refuses_is_refused(party: Party, fake: FakeDgii) -> None:
    unsigned = arecf(ISSUER.rnc, BUYER.rnc or "", "E310000000001", reason=None, at=party.clock())
    broken = sign_with_signxml(
        unsigned.replace(b"<Version>1.0</Version>", b"<Version>2.0</Version>"), fake.state.receiver
    )
    async with over(party, listed(SIMULATOR), answering(broken)).opened() as client:
        delivery = await deliver(client, credit_invoice(), await signed(party.signer))
    assert refused_for(delivery) == InboundDocumentInvalidError.code


async def test_an_unsigned_receipt_is_refused(party: Party) -> None:
    unsigned = arecf(ISSUER.rnc, BUYER.rnc or "", "E310000000001", reason=None, at=party.clock())
    async with over(party, listed(SIMULATOR), answering(unsigned)).opened() as client:
        delivery = await deliver(client, credit_invoice(), await signed(party.signer))
    assert refused_for(delivery) == SignatureInvalidError.code


@pytest.mark.parametrize(
    "changes",
    [{"issuer": "131880738"}, {"buyer": "101850043"}, {"encf": "E310000000002"}],
    ids=["another-issuer", "another-buyer", "another-encf"],
)
async def test_a_receipt_that_names_another_issuer_buyer_or_e_ncf_is_refused(
    party: Party, fake: FakeDgii, changes: dict[str, str]
) -> None:
    transport = answering(receipt(fake.state.receiver, party.clock, **changes))
    async with over(party, listed(SIMULATOR), transport).opened() as client:
        delivery = await deliver(client, credit_invoice(), await signed(party.signer))
    assert refused_for(delivery) == ArecfMismatchError.code


async def test_a_receipt_naming_no_e_ncf_is_refused_as_unreadable(party: Party, fake: FakeDgii) -> None:
    transport = answering(receipt(fake.state.receiver, party.clock, encf="e310000000001"))
    async with over(party, listed(SIMULATOR), transport).opened() as client:
        delivery = await deliver(client, credit_invoice(), await signed(party.signer))
    assert refused_for(delivery) == InboundDocumentInvalidError.code


@pytest.mark.parametrize(
    ("state", "reason", "received"),
    [(b"00", None, True), (b"+0", None, True), (b"+01", NotReceivedReason.DUPLICATE, False)],
    ids=["zero-padded", "signed-zero", "signed-padded-one"],
)
async def test_a_receipt_s_state_is_read_as_the_integer_it_writes(
    party: Party, fake: FakeDgii, state: bytes, reason: NotReceivedReason | None, *, received: bool
) -> None:
    written = arecf(ISSUER.rnc, BUYER.rnc or "", "E310000000001", reason=reason, at=party.clock())
    spelled = written.replace(b"<Estado>0</Estado>", b"<Estado>" + state + b"</Estado>").replace(
        b"<Estado>1</Estado>", b"<Estado>" + state + b"</Estado>"
    )
    transport = answering(sign_with_signxml(spelled, fake.state.receiver))
    async with over(party, listed(SIMULATOR), transport).opened() as client:
        delivery = await deliver(client, credit_invoice(), await signed(party.signer))
    assert isinstance(delivery, Delivered)
    assert (delivery.received, delivery.reason) == (received, reason)


async def test_a_receipt_not_received_without_a_reason_reads_so(party: Party, fake: FakeDgii) -> None:
    unsigned = arecf(ISSUER.rnc, BUYER.rnc or "", "E310000000001", reason=None, at=party.clock()).replace(
        b"<Estado>0</Estado>", b"<Estado> 1 </Estado>"
    )
    transport = answering(sign_with_signxml(unsigned, fake.state.receiver))
    async with over(party, listed(SIMULATOR), transport).opened() as client:
        delivery = await deliver(client, credit_invoice(), await signed(party.signer))
    assert isinstance(delivery, Delivered)
    assert (delivery.received, delivery.reason) == (False, None)


async def test_a_receipt_whose_moment_is_no_date_is_refused(party: Party, fake: FakeDgii) -> None:
    unsigned = arecf(ISSUER.rnc, BUYER.rnc or "", "E310000000001", reason=None, at=party.clock())
    start = unsigned.index(b"<FechaHoraAcuseRecibo>") + len(b"<FechaHoraAcuseRecibo>")
    impossible = unsigned[:start] + b"31-02-2026 09:30:00" + unsigned[start + 19 :]
    transport = answering(sign_with_signxml(impossible, fake.state.receiver))
    async with over(party, listed(SIMULATOR), transport).opened() as client:
        delivery = await deliver(client, credit_invoice(), await signed(party.signer))
    assert refused_for(delivery) == InboundDocumentInvalidError.code


async def test_a_receipt_signed_under_an_authority_not_configured_is_refused(
    party: Party, fake: FakeDgii
) -> None:
    elsewhere = replace(party, trust=CounterpartyTrust((Authority.root("ANOTHER ROOT").certificate,)))
    async with over(elsewhere, simulator, httpx.ASGITransport(app=fake.app)).opened() as client:
        delivery = await deliver(client, credit_invoice(), await signed(party.signer))
    assert refused_for(delivery) == CounterpartyCertificateError.code


async def test_a_receipt_signed_by_someone_other_than_the_buyer_is_refused(
    party: Party, authority: Authority
) -> None:
    impostor = authority.issue(
        organization("131880738", name="OTRO"), not_before=party.clock() - timedelta(days=1)
    )
    transport = answering(receipt(impostor, party.clock))
    async with over(party, listed(SIMULATOR), transport).opened() as client:
        delivery = await deliver(client, credit_invoice(), await signed(party.signer))
    assert refused_for(delivery) == CounterpartyCertificateError.code


async def test_the_buyer_on_the_e_cf_is_the_identity_its_receipt_must_carry(
    party: Party, fake: FakeDgii
) -> None:
    other = Buyer(rnc="131880738", name="OTRO COMPRADOR SRL")
    draft = credit_invoice(buyer=other)
    document = party.signer.sign(ecf.render(draft, compute_totals(draft), signed_at=SIGNED_AT))
    fake.state.receiver_requires_auth = False
    async with over(party, simulator, httpx.ASGITransport(app=fake.app)).opened() as client:
        delivery = await deliver(client, draft, document)
    assert refused_for(delivery) == CounterpartyCertificateError.code


async def test_a_receipt_dated_far_from_its_receipt_is_refused(party: Party, fake: FakeDgii) -> None:
    later = replace(party, clock=ManualClock(party.clock() + timedelta(minutes=6)))
    fake.state.receiver_requires_auth = False
    async with over(later, simulator, httpx.ASGITransport(app=fake.app)).opened() as client:
        delivery = await deliver(client, credit_invoice(), await signed(party.signer))
    assert refused_for(delivery) == CounterpartyCertificateError.code


async def test_a_client_s_own_clock_is_the_system_s(
    authority: Authority, issued: Issued, party: Party
) -> None:
    receiver = authority.issue(
        organization(BUYER.rnc or "", name=BUYER.name or ""), not_before=SIGNER_VALID_FROM
    )
    live = FakeDgii(
        check=dgii_check(trusted=[issued.certificate]), receiver=receiver, public_url=FAKE_HOSTS.ecf
    )
    live.state.receiver_requires_auth = False
    client = BuyerClient(
        DocumentSigner(party.signer),
        simulator,
        trust=party.trust,
        http=outbound_over(httpx.ASGITransport(app=live.app)),
        resolve=public_dns,
    )
    async with client.opened():
        delivery = await deliver(client, credit_invoice(), await signed(party.signer))
    assert isinstance(delivery, Delivered)
    assert delivery.received


# -- Review round: the seed audit, every checked address, one classification -------------------------------


@pytest.mark.parametrize(
    ("base", "audience"),
    [
        (SIMULATOR, SIMULATOR_HOST),
        (SIMULATOR.replace(SIMULATOR_HOST, f"{SIMULATOR_HOST}:8443"), "ecf.fake.test:8443"),
    ],
    ids=["https-port", "own-port"],
)
async def test_a_buyer_seed_is_signed_for_the_endpoint_it_came_from(
    party: Party, fake: FakeDgii, base: str, audience: str
) -> None:
    signer = TestSigner(sign=party.signer.sign, sign_seed=party.signer.sign)
    client = BuyerClient(
        DocumentSigner(signer),
        listed(base, base),
        trust=party.trust,
        http=outbound_over(httpx.ASGITransport(app=fake.app)),
        resolve=public_dns,
        clock=party.clock,
    )
    async with client.opened():
        await deliver(client, credit_invoice(), await signed(party.signer))
    assert [(call.audience, call.tenant) for call in signer.calls] == [(audience, WHO.tenant)]


async def test_every_buyer_seed_signed_is_returned_for_the_caller_to_audit_without_secrets(
    party: Party, fake: FakeDgii
) -> None:
    async with over(party, simulator, httpx.ASGITransport(app=fake.app)).opened() as client:
        delivery = await deliver(client, credit_invoice(), await signed(party.signer))
    assert isinstance(delivery, Delivered)
    assert delivery.seeds == (
        SeedSigned(buyer=BUYER.rnc or "", endpoint_host=SIMULATOR_HOST, at=party.clock()),
    )
    assert delivery.received_at == party.clock()
    fake.state.receiver_requires_auth = False
    async with over(party, listed(SIMULATOR), httpx.ASGITransport(app=fake.app)).opened() as client:
        unauthenticated = await deliver(client, credit_invoice(2), await signed(party.signer, 2))
    assert isinstance(unauthenticated, Delivered)
    assert unauthenticated.seeds == ()
    shown = repr(delivery)
    assert "Signature" not in shown
    assert "-----BEGIN" not in shown


SECOND: Final = IPv4Address("93.184.215.15")


async def both(host: str, port: int) -> Sequence[IPv4Address | IPv6Address]:
    del host, port
    return (PUBLIC, SECOND)


@final
class FirstAddressFails(httpx.AsyncBaseTransport):
    """Fails every request to ``PUBLIC`` with ``failure`` (before or after sending), passes the rest on."""

    __slots__ = ("_after", "_inner", "hosts")

    def __init__(self, inner: httpx.AsyncBaseTransport, *, after_sending: bool) -> None:
        self._inner = inner
        self._after = after_sending
        self.hosts: list[str] = []

    @override
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.hosts.append(request.url.host)
        if request.url.host != str(PUBLIC):
            return await self._inner.handle_async_request(request)
        if self._after:
            raise httpx.ReadError(REFUSED, request=request)
        raise httpx.ConnectError(REFUSED, request=request)


async def test_an_address_that_cannot_be_connected_to_gives_way_to_the_next_checked_one(
    party: Party, fake: FakeDgii
) -> None:
    resolved: list[str] = []

    async def counted(host: str, port: int) -> Sequence[IPv4Address | IPv6Address]:
        resolved.append(host)
        return await both(host, port)

    transport = FirstAddressFails(httpx.ASGITransport(app=fake.app), after_sending=False)
    async with over(party, simulator, transport, resolve=counted).opened() as client:
        assert isinstance(await deliver(client, credit_invoice(), await signed(party.signer)), Delivered)
    assert transport.hosts == [str(PUBLIC), str(SECOND)] * 3
    assert resolved == [SIMULATOR_HOST] * 3


async def test_a_request_that_may_have_reached_an_address_is_never_sent_to_another(
    party: Party, fake: FakeDgii
) -> None:
    transport = FirstAddressFails(httpx.ASGITransport(app=fake.app), after_sending=True)
    async with over(party, simulator, transport, resolve=both).opened() as client:
        with pytest.raises(BuyerUnavailableError, match="its seed"):
            await deliver(client, credit_invoice(), await signed(party.signer))
    assert transport.hosts == [str(PUBLIC)]


async def test_a_buyer_none_of_whose_addresses_answers_is_unavailable(party: Party, fake: FakeDgii) -> None:
    async def first_only(host: str, port: int) -> Sequence[IPv4Address | IPv6Address]:
        del host, port
        return (PUBLIC,)

    transport = FirstAddressFails(httpx.ASGITransport(app=fake.app), after_sending=False)
    async with over(party, simulator, transport, resolve=first_only).opened() as client:
        with pytest.raises(BuyerUnavailableError, match="its seed"):
            await deliver(client, credit_invoice(), await signed(party.signer))


async def test_a_receipt_that_says_received_and_gives_a_reason_is_refused(
    party: Party, fake: FakeDgii
) -> None:
    unsigned = arecf(ISSUER.rnc, BUYER.rnc or "", "E310000000001", reason=3, at=party.clock()).replace(
        b"<Estado>1</Estado>", b"<Estado>0</Estado>"
    )
    transport = answering(sign_with_signxml(unsigned, fake.state.receiver))
    async with over(party, listed(SIMULATOR), transport).opened() as client:
        delivery = await deliver(client, credit_invoice(), await signed(party.signer))
    assert refused_for(delivery) == InboundDocumentInvalidError.code


def test_a_receipt_answered_but_wrong_is_invalid_and_a_buyer_that_failed_is_unavailable() -> None:
    invalid = (
        SchemaViolationError,
        SignatureInvalidError,
        DoctypeRefusedError,
        ArecfMismatchError,
        InboundDocumentInvalidError,
        CounterpartyCertificateError,
        BuyerEndpointRefusedError,
    )
    assert {error.kind for error in invalid} == {"invalid"}
    assert BuyerUnavailableError.kind == "unavailable"


async def test_buyers_are_called_through_an_outbound_client_named_buyers_that_keeps_no_connection(
    party: Party, fake: FakeDgii, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[tuple[str, bool]] = []
    made = OutboundHttp.client

    def watched(
        self: OutboundHttp, name: str, *, timeouts: OutboundTimeouts, keep_alive: bool = True
    ) -> httpx.AsyncClient:
        asked.append((name, keep_alive))
        return made(self, name, timeouts=timeouts, keep_alive=keep_alive)

    monkeypatch.setattr(OutboundHttp, "client", watched)
    async with over(party, simulator, httpx.ASGITransport(app=fake.app)).opened() as client:
        await deliver(client, credit_invoice(), await signed(party.signer))
    assert asked == [("buyers", False)]


# -- The authentication switch, failures that keep their seeds, ACECF to emitters -------------------------


async def stranger(who: SigningAs, environment: Environment, rnc: str) -> DirectoryEntry | None:
    """DGII's answer naming another taxpayer than the one asked about."""
    del who, environment, rnc
    return DirectoryEntry("OTRO", "131880738", SIMULATOR, SIMULATOR, SIMULATOR)


def recording(
    party: Party, directory: DirectoryLookup, transport: httpx.AsyncBaseTransport
) -> tuple[BuyerClient, TestSigner]:
    """A client whose signer records every request (``TestSigner.calls``), signing with the party's key."""
    signer = TestSigner(sign=party.signer.sign, sign_seed=party.signer.sign)
    client = BuyerClient(
        DocumentSigner(signer),
        directory,
        trust=party.trust,
        http=outbound_over(transport),
        resolve=public_dns,
        clock=party.clock,
    )
    return client, signer


async def test_with_authentication_off_no_seed_is_signed_and_a_buyer_requiring_it_fails_so(
    party: Party, fake: FakeDgii
) -> None:
    watched = Recording(httpx.ASGITransport(app=fake.app))
    client, signer = recording(party, simulator, watched)
    async with client.opened():
        with pytest.raises(BuyerRequiresAuthenticationError, match="requires authentication") as raised:
            await deliver(client, credit_invoice(), await signed(party.signer), authenticate=False)
    assert signer.calls == []
    assert raised.value.seeds == ()
    assert (raised.value.code, raised.value.kind) == ("do_ecf_buyer_requires_authentication", "unavailable")
    assert [request.url.path.rsplit("/", 1)[-1] for request in watched.requests] == ["ecf"]


async def test_with_authentication_on_the_seed_is_signed_as_the_issuing_company(
    party: Party, fake: FakeDgii
) -> None:
    client, signer = recording(party, simulator, httpx.ASGITransport(app=fake.app))
    async with client.opened():
        delivery = await deliver(client, credit_invoice(), await signed(party.signer))
    assert isinstance(delivery, Delivered)
    assert len(delivery.seeds) == 1
    assert [(call.tenant, call.audience) for call in signer.calls] == [(WHO.tenant, SIMULATOR_HOST)]


async def test_with_authentication_off_a_buyer_that_requires_none_is_delivered_to(
    party: Party, fake: FakeDgii
) -> None:
    fake.state.receiver_requires_auth = False
    client, signer = recording(party, simulator, httpx.ASGITransport(app=fake.app))
    async with client.opened():
        delivery = await deliver(client, credit_invoice(), await signed(party.signer), authenticate=False)
    assert isinstance(delivery, Delivered)
    assert (delivery.received, delivery.seeds, signer.calls) == (True, (), [])


async def test_a_receipt_signed_by_a_certificate_of_another_rnc_is_refused_with_its_bytes(
    party: Party, authority: Authority
) -> None:
    impostor = authority.issue(
        organization("131880738", name="OTRO"), not_before=party.clock() - timedelta(days=1)
    )
    answer = receipt(impostor, party.clock)
    async with over(party, listed(SIMULATOR), answering(answer)).opened() as client:
        delivery = await deliver(client, credit_invoice(), await signed(party.signer))
    assert delivery == ReceiptRefused(arecf=answer, reason=CounterpartyCertificateError.code, seeds=())


async def test_a_failure_after_a_seed_was_signed_still_hands_the_seed_over(
    party: Party, fake: FakeDgii
) -> None:
    answers = iter([(PUBLIC,), (PUBLIC,), (IPv4Address("127.0.0.1"),)])

    async def rebinding(host: str, port: int) -> Sequence[IPv4Address | IPv6Address]:
        del host, port
        return next(answers)

    async with over(
        party, simulator, httpx.ASGITransport(app=fake.app), resolve=rebinding
    ).opened() as client:
        with pytest.raises(BuyerEndpointRefusedError) as refused:
            await deliver(client, credit_invoice(), await signed(party.signer))
    assert [seed.endpoint_host for seed in refused.value.seeds] == [SIMULATOR_HOST]
    fake.script.outage = 0

    def reception_fails(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/semilla"):
            return httpx.Response(200, stream=httpx.ByteStream(SEED))
        if request.url.path.endswith("validacioncertificado"):
            return httpx.Response(200, stream=httpx.ByteStream(TOKEN))
        return httpx.Response(503, stream=httpx.ByteStream(b""))

    async with over(party, simulator, httpx.MockTransport(reception_fails)).opened() as client:
        with pytest.raises(BuyerUnavailableError, match="its reception") as failed:
            await deliver(client, credit_invoice(), await signed(party.signer))
    assert [seed.endpoint_host for seed in failed.value.seeds] == [SIMULATOR_HOST]


def acecf(signer: KeyPairSigner, *, signed_too: bool = True) -> bytes:
    """Our commercial approval of the sample credit invoice, as its buyer: signed unless told otherwise."""
    approval = CommercialApproval(
        issuer_rnc=ISSUER.rnc,
        buyer_rnc=BUYER.rnc or "",
        encf=credit_invoice().encf,
        issued_on=credit_invoice().issued_on,
        total=Decimal("4887.58"),
        approved=True,
        reason=None,
        at=SIGNED_AT,
    )
    document = render_acecf(approval)
    return signer.sign(document) if signed_too else document


async def test_an_acecf_reaches_the_emitter_s_approval_service_authenticated_when_switched_on(
    party: Party, fake: FakeDgii
) -> None:
    watched = Recording(httpx.ASGITransport(app=fake.app))
    client, signer = recording(party, simulator, watched)
    document = acecf(party.signer)
    async with client.opened():
        sent = await client.send_acecf(WHO, T, ISSUER.rnc, document, authenticate=True)
    assert sent == ApprovalSent(
        sent_at=party.clock(),
        seeds=(SeedSigned(buyer=ISSUER.rnc, endpoint_host=SIMULATOR_HOST, at=party.clock()),),
    )
    assert fake.state.approvals == [document]
    assert isinstance(sent, ApprovalSent)
    assert sent.sent_at == party.clock()  # when the emitter took it
    assert [request.url.path for request in watched.requests][-1] == f"/testecf/emisorreceptor{APPROVAL_PATH}"
    assert [call.audience for call in signer.calls] == [SIMULATOR_HOST]


async def test_an_acecf_the_emitter_refuses_is_refused_and_its_failures_are_unavailable(
    party: Party, fake: FakeDgii
) -> None:
    fake.state.receiver_requires_auth = False
    client, signer = recording(party, simulator, httpx.ASGITransport(app=fake.app))
    async with client.opened():
        refused = await client.send_acecf(
            WHO, T, ISSUER.rnc, acecf(party.signer, signed_too=False), authenticate=False
        )
        assert refused == ApprovalRefused(seeds=())
        fake.script.outage = 1
        with pytest.raises(BuyerUnavailableError, match="its approval service"):
            await client.send_acecf(WHO, T, ISSUER.rnc, acecf(party.signer), authenticate=False)
        fake.state.receiver_requires_auth = True
        with pytest.raises(BuyerRequiresAuthenticationError, match="its approval service"):
            await client.send_acecf(WHO, T, ISSUER.rnc, acecf(party.signer), authenticate=False)
    assert (fake.state.approvals, signer.calls) == ([], [])


async def test_an_acecf_never_goes_to_another_s_entry_nor_to_none(party: Party, fake: FakeDgii) -> None:
    watched = Recording(httpx.ASGITransport(app=fake.app))
    for lookup, expected in (
        (nobody, NotApplicable("no_directory_entry")),
        (stranger, NotApplicable("entry_names_another_rnc")),
    ):
        client, _ = recording(party, lookup, watched)
        async with client.opened():
            found = await client.send_acecf(WHO, T, ISSUER.rnc, acecf(party.signer), authenticate=True)
        assert found == expected
    assert watched.requests == []


@pytest.mark.parametrize(
    "approval",
    [
        "http://emitter.example.com/fe",
        "https://10.0.0.7",
        "https://localhost",
        "https://emitter.example.com/?x=1",
    ],
    ids=["plain-http", "ip-literal", "single-label", "query"],
)
async def test_an_approval_url_that_is_not_a_public_https_name_is_refused_before_anything_is_sent(
    party: Party, fake: FakeDgii, approval: str
) -> None:
    async def listed_approval(who: SigningAs, environment: Environment, rnc: str) -> DirectoryEntry | None:
        del who, environment
        return DirectoryEntry("EMISOR", rnc, SIMULATOR, approval, SIMULATOR)

    watched = Recording(httpx.ASGITransport(app=fake.app))
    client, signer = recording(party, listed_approval, watched)
    async with client.opened():
        with pytest.raises(BuyerEndpointRefusedError):
            await client.send_acecf(WHO, T, ISSUER.rnc, acecf(party.signer), authenticate=True)
    assert (watched.requests, signer.calls) == ([], [])


async def test_an_approval_to_an_address_that_is_not_public_is_refused_before_anything_is_sent(
    party: Party, fake: FakeDgii
) -> None:
    async def private(host: str, port: int) -> Sequence[IPv4Address | IPv6Address]:
        del host, port
        return (IPv4Address("10.1.2.3"),)

    watched = Recording(httpx.ASGITransport(app=fake.app))
    async with over(party, simulator, watched, resolve=private).opened() as client:
        with pytest.raises(BuyerEndpointRefusedError, match="resolves to an address"):
            await client.send_acecf(WHO, T, ISSUER.rnc, acecf(party.signer), authenticate=False)
    assert watched.requests == []


@pytest.mark.parametrize(
    ("issuer", "buyer", "field"),
    [("", "131098193", "issuer_rnc"), ("101850043", "1310981", "buyer_rnc")],
    ids=["no-issuer", "no-buyer"],
)
def test_a_delivery_names_an_rnc_or_a_cedula_for_both_parties(issuer: str, buyer: str, field: str) -> None:
    with pytest.raises(ExchangeInvalidError, match=rf"delivery_document\.{field}") as raised:
        DeliveryDocument(issuer_rnc=issuer, buyer_rnc=buyer, encf=credit_invoice().encf)
    assert [value for value in (issuer, buyer) if value and value in str(raised.value)] == []


async def test_a_buyer_answering_401_with_authentication_on_but_none_published_is_unavailable(
    party: Party, fake: FakeDgii
) -> None:
    # The switch is on; the buyer publishes no authentication URL yet refuses: no switch can help.
    client, signer = recording(party, listed(SIMULATOR), httpx.ASGITransport(app=fake.app))
    async with client.opened():
        with pytest.raises(BuyerUnavailableError) as raised:
            await deliver(client, credit_invoice(), await signed(party.signer), authenticate=True)
        with pytest.raises(BuyerUnavailableError) as approval:
            await client.send_acecf(WHO, T, ISSUER.rnc, acecf(party.signer), authenticate=True)
    for error in (raised.value, approval.value):
        assert type(error) is BuyerUnavailableError
        assert "switched off" not in str(error)
    assert signer.calls == []


async def test_no_outcome_of_an_exchange_shows_a_tax_id_or_the_documents(
    party: Party, fake: FakeDgii
) -> None:
    async with over(party, simulator, httpx.ASGITransport(app=fake.app)).opened() as client:
        delivered = await deliver(client, credit_invoice(), await signed(party.signer))
        sent = await client.send_acecf(WHO, T, ISSUER.rnc, acecf(party.signer), authenticate=True)
    fake.state.tamper_arecf = True
    async with over(party, simulator, httpx.ASGITransport(app=fake.app)).opened() as client:
        refused = await deliver(client, credit_invoice(2), await signed(party.signer, 2))
    assert isinstance(delivered, Delivered)
    assert isinstance(refused, ReceiptRefused)
    assert isinstance(sent, ApprovalSent)
    for outcome in (delivered, refused, sent, *delivered.seeds):
        shown = repr(outcome)
        assert [
            secret for secret in (BUYER.rnc or "", ISSUER.rnc, "<ARECF", "BEGIN") if secret in shown
        ] == []


async def test_a_defect_reading_a_receipt_is_not_a_refused_receipt(
    party: Party, fake: FakeDgii, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Only the receipt's own faults (pack errors, the kit's XML and schema refusals) refuse it: a value our
    # code breaks is a defect of ours, and must not be archived as the buyer's fault.
    def broken(document: bytes) -> Receipt:
        del document
        raise InvalidValueError(field="receipt", requirement="a value our code breaks")

    monkeypatch.setattr(buyers_client, "read_inbound_arecf", broken)
    async with over(party, simulator, httpx.ASGITransport(app=fake.app)).opened() as client:
        with pytest.raises(InvalidValueError):
            await deliver(client, credit_invoice(), await signed(party.signer))


@final
class SlowSeeds(SignerPort):
    """Signs a seed only after a minute: a signer that takes its time."""

    @override
    async def sign_xml(self, call: SystemCall, request: XmlToSign) -> Signed:
        raise AssertionError  # a delivery signs seeds only

    @override
    async def sign_seed(self, call: SystemCall, request: SeedToSign) -> Signed:
        await asyncio.sleep(60)
        raise AssertionError  # never reached: the exchange's deadline ends it

    @override
    async def certificate_validity(self, call: SystemCall, tenant: Tenant) -> CertificateValidity | None:
        raise AssertionError  # never asked


async def test_signing_a_buyer_s_seed_ends_with_the_exchange_s_deadline(party: Party, fake: FakeDgii) -> None:
    client = BuyerClient(
        DocumentSigner(SlowSeeds()),
        simulator,
        trust=party.trust,
        http=outbound_over(httpx.ASGITransport(app=fake.app)),
        resolve=public_dns,
        timeouts=DgiiTimeouts(connect=5, total=5, call=0.3),
    )
    loop = asyncio.get_running_loop()
    async with client.opened():
        began = loop.time()
        with pytest.raises(BuyerUnavailableError, match="its seed") as raised:
            await deliver(client, credit_invoice(), await signed(party.signer))
    assert loop.time() - began < 5
    assert raised.value.seeds == ()
    assert fake.received == []
