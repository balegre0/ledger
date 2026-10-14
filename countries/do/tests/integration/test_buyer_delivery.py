import asyncio
import logging
import socket
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from ipaddress import IPv4Address, ip_address
from typing import Final, final, override
lazy from collections.abc import AsyncIterator, Sequence
lazy from ipaddress import IPv6Address

import httpx
import pytest

from ledger_country_do.channels.ecf_v1.adapters.buyers.client import (
    AUDIT_LOGGER,
    RECEPTION_PATH,
    SEED_PATH,
    VALIDATION_PATH,
    ArecfContradictoryError,
    ArecfMismatchError,
    ArecfMomentError,
    BuyerClient,
    BuyerUnavailableError,
    Delivered,
    NotApplicable,
    SeedSigned,
    log_seed_signed,
)
from ledger_country_do.channels.ecf_v1.adapters.buyers.endpoints import BuyerEndpointRefusedError
from ledger_country_do.channels.ecf_v1.adapters.dgii.client import DirectoryEntry
from ledger_country_do.channels.ecf_v1.adapters.http import MAX_ANSWER, DgiiTimeouts
from ledger_country_do.channels.ecf_v1.adapters.signing.trust import (
    CounterpartyCertificateError,
    CounterpartyTrust,
)
from ledger_country_do.channels.ecf_v1.adapters.signing.verifier import SignatureInvalidError
from ledger_country_do.channels.ecf_v1.adapters.xml import ecf
from ledger_country_do.channels.ecf_v1.adapters.xml.parsing import DoctypeRefusedError
from ledger_country_do.channels.ecf_v1.adapters.xml.schemas import SchemaViolationError
from ledger_country_do.channels.ecf_v1.domain.drafts import Buyer
from ledger_country_do.channels.ecf_v1.domain.environments import Environment
from ledger_country_do.channels.ecf_v1.domain.totals import compute_totals
from ledger_country_do_testing.fake_dgii.app import FakeDgii
from ledger_country_do_testing.fake_dgii.receiver import arecf
from ledger_country_do_testing.fake_dgii.state import dgii_check
from ledger_country_do_testing.pki import Authority, organization
from ledger_country_do_testing.samples import (
    BUYER,
    ISSUER,
    SIGNED_AT,
    SIGNER_VALID_FROM,
    consumer_invoice,
    credit_invoice,
)
from ledger_country_do_testing.signatures import sign_with_signxml
from ledger_country_do_testing.transports import FAKE_HOSTS
from ledger_testing.clock import ManualClock
lazy from ledger_country_do.channels.ecf_v1.adapters.buyers.client import DirectoryLookup
lazy from ledger_country_do.channels.ecf_v1.adapters.buyers.endpoints import Resolve
lazy from ledger_country_do.channels.ecf_v1.adapters.dgii.client import DgiiClient
lazy from ledger_country_do_testing.local_signer import KeyPairSigner
lazy from ledger_country_do_testing.pki import Issued

T: Final = Environment.TESTECF
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


async def nobody(environment: Environment, rnc: str) -> DirectoryEntry | None:
    del environment, rnc
    return None


async def simulator(environment: Environment, rnc: str) -> DirectoryEntry | None:
    del environment
    return DirectoryEntry("EMISOR RECEPTOR DGII", rnc, SIMULATOR, SIMULATOR, SIMULATOR)


def listed(reception: str, authentication: str | None = None) -> DirectoryLookup:
    async def lookup(environment: Environment, rnc: str) -> DirectoryEntry | None:
        del environment
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
        party.signer,
        directory,
        trust=party.trust,
        transport=transport,
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


async def signed(signer: KeyPairSigner, sequence: int = 1) -> bytes:
    draft = credit_invoice(sequence)
    return await signer.sign_document(ecf.render(draft, compute_totals(draft), signed_at=SIGNED_AT))


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
    delivery = await buyers.deliver(T, credit_invoice(), await signed(signer))
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
    first = await buyers.deliver(T, credit_invoice(), document)
    again = await buyers.deliver(T, credit_invoice(), document)
    assert isinstance(first, Delivered)
    assert isinstance(again, Delivered)
    assert (first.received, again.received, again.reason) == (True, False, 3)


async def test_a_receipt_whose_signature_fails_is_refused(
    buyers: BuyerClient, signer: KeyPairSigner, fake: FakeDgii
) -> None:
    fake.state.tamper_arecf = True
    with pytest.raises(SignatureInvalidError):
        await buyers.deliver(T, credit_invoice(), await signed(signer))


async def test_a_receipt_about_another_document_is_refused(
    buyers: BuyerClient, signer: KeyPairSigner
) -> None:
    with pytest.raises(ArecfMismatchError):
        await buyers.deliver(T, credit_invoice(2), await signed(signer, 1))


async def test_delivery_does_not_apply_without_an_identified_electronic_buyer(
    buyers: BuyerClient, party: Party, fake: FakeDgii
) -> None:
    assert await buyers.deliver(T, consumer_invoice(), b"") == NotApplicable("the buyer has no RNC or cédula")
    async with over(party, nobody, httpx.ASGITransport(app=fake.app)).opened() as client:
        found = await client.deliver(T, credit_invoice(), b"")
    assert found == NotApplicable("the buyer is not an electronic receiver")


async def test_a_buyer_that_fails_is_unavailable(party: Party, fake: FakeDgii) -> None:
    async with over(party, simulator, httpx.ASGITransport(app=fake.app)).opened() as client:
        fake.script.outage = 1
        with pytest.raises(BuyerUnavailableError, match="its seed"):
            await client.deliver(T, credit_invoice(), await signed(party.signer))


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
            await client.deliver(T, credit_invoice(), await signed(party.signer))


async def test_a_client_not_opened_delivers_nothing(party: Party) -> None:
    with pytest.raises(BuyerUnavailableError, match="not opened"):
        await over(party, simulator, None).deliver(T, credit_invoice(), await signed(party.signer))


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
            await client.deliver(T, credit_invoice(), await signed(party.signer))
    assert watched.requests == []


async def test_an_authentication_endpoint_is_checked_too_before_anything_is_sent(
    party: Party, fake: FakeDgii
) -> None:
    watched = Recording(httpx.ASGITransport(app=fake.app))
    lookup = listed(SIMULATOR, authentication="https://169.254.169.254/latest")
    async with over(party, lookup, watched).opened() as client:
        with pytest.raises(BuyerEndpointRefusedError):
            await client.deliver(T, credit_invoice(), await signed(party.signer))
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
            await client.deliver(T, credit_invoice(), await signed(party.signer))
    assert watched.requests == []


async def test_every_request_goes_to_the_address_checked_its_name_kept_for_tls_and_host(
    party: Party, fake: FakeDgii
) -> None:
    watched = Recording(httpx.ASGITransport(app=fake.app))
    async with over(party, simulator, watched).opened() as client:
        assert isinstance(await client.deliver(T, credit_invoice(), await signed(party.signer)), Delivered)
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
            await client.deliver(T, credit_invoice(), await signed(party.signer))
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
            await c.deliver(T, credit_invoice(), await signed(party.signer))


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
            await client.deliver(T, credit_invoice(), await signed(party.signer))
    assert sent == [f"/testecf/emisorreceptor{RECEPTION_PATH}"]


async def test_an_answer_over_the_limit_is_refused_as_it_streams(party: Party) -> None:
    transport = answering(b"<" + b"a" * MAX_ANSWER + b"/>")
    async with over(party, listed(SIMULATOR), transport).opened() as client:
        with pytest.raises(BuyerUnavailableError, match="its reception"):
            await client.deliver(T, credit_invoice(), await signed(party.signer))


async def test_a_buyer_that_stalls_is_given_up_on(party: Party) -> None:
    async def stalls(request: httpx.Request) -> httpx.Response:
        del request
        await asyncio.sleep(5)
        return httpx.Response(200, stream=httpx.ByteStream(b""))

    timeouts = DgiiTimeouts(connect=0.5, total=0.5, call=5.0)
    transport = httpx.MockTransport(stalls)
    async with over(party, listed(SIMULATOR), transport, timeouts=timeouts).opened() as client:
        with pytest.raises(BuyerUnavailableError, match="its reception"):
            await client.deliver(T, credit_invoice(), await signed(party.signer))


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
            await client.deliver(T, credit_invoice(), await signed(party.signer))


async def test_a_hostile_seed_is_never_signed(party: Party) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200, stream=httpx.ByteStream(b'<!DOCTYPE s [<!ENTITY e "x">]><SemillaModel>&e;</SemillaModel>')
        )

    async with over(party, simulator, httpx.MockTransport(handler)).opened() as client:
        with pytest.raises(BuyerUnavailableError, match="its seed"):
            await client.deliver(T, credit_invoice(), await signed(party.signer))


# -- The receipt: schema, signature, contents, and its certificate under the trust policy --------------------


async def test_a_receipt_with_a_doctype_is_refused_unread(party: Party) -> None:
    transport = answering(b'<?xml version="1.0"?><!DOCTYPE ARECF SYSTEM "http://x/"><ARECF/>')
    async with over(party, listed(SIMULATOR), transport).opened() as client:
        with pytest.raises(DoctypeRefusedError):
            await client.deliver(T, credit_invoice(), await signed(party.signer))


async def test_a_receipt_dgii_s_schema_refuses_is_refused(party: Party, fake: FakeDgii) -> None:
    broken = receipt(fake.state.receiver, party.clock).replace(
        b"<Version>1.0</Version>", b"<Version>2.0</Version>"
    )
    async with over(party, listed(SIMULATOR), answering(broken)).opened() as client:
        with pytest.raises(SchemaViolationError):
            await client.deliver(T, credit_invoice(), await signed(party.signer))


async def test_an_unsigned_receipt_is_refused(party: Party) -> None:
    unsigned = arecf(ISSUER.rnc, BUYER.rnc or "", "E310000000001", reason=None, at=party.clock())
    async with over(party, listed(SIMULATOR), answering(unsigned)).opened() as client:
        with pytest.raises(SignatureInvalidError):
            await client.deliver(T, credit_invoice(), await signed(party.signer))


@pytest.mark.parametrize(
    "changes",
    [{"issuer": "131880738"}, {"buyer": "101850043"}, {"encf": "E310000000002"}, {"encf": "e310000000001"}],
    ids=["another-issuer", "another-buyer", "another-encf", "encf-in-another-case"],
)
async def test_a_receipt_that_names_another_issuer_buyer_or_e_ncf_is_refused(
    party: Party, fake: FakeDgii, changes: dict[str, str]
) -> None:
    transport = answering(receipt(fake.state.receiver, party.clock, **changes))
    async with over(party, listed(SIMULATOR), transport).opened() as client:
        with pytest.raises(ArecfMismatchError):
            await client.deliver(T, credit_invoice(), await signed(party.signer))


async def test_a_receipt_not_received_without_a_reason_reads_so(party: Party, fake: FakeDgii) -> None:
    unsigned = arecf(ISSUER.rnc, BUYER.rnc or "", "E310000000001", reason=None, at=party.clock()).replace(
        b"<Estado>0</Estado>", b"<Estado> 1 </Estado>"
    )
    transport = answering(sign_with_signxml(unsigned, fake.state.receiver))
    async with over(party, listed(SIMULATOR), transport).opened() as client:
        delivery = await client.deliver(T, credit_invoice(), await signed(party.signer))
    assert isinstance(delivery, Delivered)
    assert (delivery.received, delivery.reason) == (False, None)


async def test_a_receipt_whose_moment_is_no_date_is_refused(party: Party, fake: FakeDgii) -> None:
    unsigned = arecf(ISSUER.rnc, BUYER.rnc or "", "E310000000001", reason=None, at=party.clock())
    start = unsigned.index(b"<FechaHoraAcuseRecibo>") + len(b"<FechaHoraAcuseRecibo>")
    impossible = unsigned[:start] + b"31-02-2026 09:30:00" + unsigned[start + 19 :]
    transport = answering(sign_with_signxml(impossible, fake.state.receiver))
    async with over(party, listed(SIMULATOR), transport).opened() as client:
        with pytest.raises(ArecfMomentError):
            await client.deliver(T, credit_invoice(), await signed(party.signer))


async def test_a_receipt_signed_under_an_authority_not_configured_is_refused(
    party: Party, fake: FakeDgii
) -> None:
    elsewhere = replace(party, trust=CounterpartyTrust((Authority.root("ANOTHER ROOT").certificate,)))
    async with over(elsewhere, simulator, httpx.ASGITransport(app=fake.app)).opened() as client:
        with pytest.raises(CounterpartyCertificateError, match="trusted authority"):
            await client.deliver(T, credit_invoice(), await signed(party.signer))


async def test_a_receipt_signed_by_someone_other_than_the_buyer_is_refused(
    party: Party, authority: Authority
) -> None:
    impostor = authority.issue(
        organization("131880738", name="OTRO"), not_before=party.clock() - timedelta(days=1)
    )
    transport = answering(receipt(impostor, party.clock))
    async with over(party, listed(SIMULATOR), transport).opened() as client:
        with pytest.raises(CounterpartyCertificateError, match="not the buyer"):
            await client.deliver(T, credit_invoice(), await signed(party.signer))


async def test_the_buyer_on_the_e_cf_is_the_identity_its_receipt_must_carry(
    party: Party, fake: FakeDgii
) -> None:
    other = Buyer(rnc="131880738", name="OTRO COMPRADOR SRL")
    draft = credit_invoice(buyer=other)
    document = await party.signer.sign_document(ecf.render(draft, compute_totals(draft), signed_at=SIGNED_AT))
    fake.state.receiver_requires_auth = False
    async with over(party, simulator, httpx.ASGITransport(app=fake.app)).opened() as client:
        with pytest.raises(CounterpartyCertificateError, match="not the buyer"):
            await client.deliver(T, draft, document)


async def test_a_receipt_dated_far_from_its_receipt_is_refused(party: Party, fake: FakeDgii) -> None:
    later = replace(party, clock=ManualClock(party.clock() + timedelta(minutes=6)))
    fake.state.receiver_requires_auth = False
    async with over(later, simulator, httpx.ASGITransport(app=fake.app)).opened() as client:
        with pytest.raises(CounterpartyCertificateError, match="far from"):
            await client.deliver(T, credit_invoice(), await signed(party.signer))


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
        party.signer,
        simulator,
        trust=party.trust,
        transport=httpx.ASGITransport(app=live.app),
        resolve=public_dns,
    )
    async with client.opened():
        delivery = await client.deliver(T, credit_invoice(), await signed(party.signer))
    assert isinstance(delivery, Delivered)
    assert delivery.received


# -- Review round: the seed audit, every checked address, one classification -------------------------------


async def test_every_buyer_seed_signed_is_audited_without_secrets(party: Party, fake: FakeDgii) -> None:
    audited: list[SeedSigned] = []
    client = BuyerClient(
        party.signer,
        simulator,
        trust=party.trust,
        transport=httpx.ASGITransport(app=fake.app),
        resolve=public_dns,
        clock=party.clock,
        audit=audited.append,
    )
    async with client.opened():
        await client.deliver(T, credit_invoice(), await signed(party.signer))
    assert audited == [SeedSigned(ISSUER.rnc, BUYER.rnc or "", SIMULATOR_HOST, party.clock())]
    fake.state.receiver_requires_auth = False
    unauthenticated = BuyerClient(
        party.signer,
        listed(SIMULATOR),
        trust=party.trust,
        transport=httpx.ASGITransport(app=fake.app),
        resolve=public_dns,
        clock=party.clock,
        audit=audited.append,
    )
    async with unauthenticated.opened():
        await unauthenticated.deliver(T, credit_invoice(2), await signed(party.signer, 2))
    assert len(audited) == 1


def test_the_default_audit_is_a_log_line_naming_no_secret(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger=AUDIT_LOGGER):
        log_seed_signed(
            SeedSigned(ISSUER.rnc, "131098193", SIMULATOR_HOST, datetime(2026, 10, 13, tzinfo=UTC))
        )
    (record,) = caplog.records
    assert record.name == AUDIT_LOGGER
    assert record.getMessage() == (
        "signed a buyer's seed: issuer 101850043, buyer 131098193, endpoint ecf.fake.test, "
        "at 2026-10-13T00:00:00+00:00"
    )


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
        assert isinstance(await client.deliver(T, credit_invoice(), await signed(party.signer)), Delivered)
    assert transport.hosts == [str(PUBLIC), str(SECOND)] * 3
    assert resolved == [SIMULATOR_HOST] * 3


async def test_a_request_that_may_have_reached_an_address_is_never_sent_to_another(
    party: Party, fake: FakeDgii
) -> None:
    transport = FirstAddressFails(httpx.ASGITransport(app=fake.app), after_sending=True)
    async with over(party, simulator, transport, resolve=both).opened() as client:
        with pytest.raises(BuyerUnavailableError, match="its seed"):
            await client.deliver(T, credit_invoice(), await signed(party.signer))
    assert transport.hosts == [str(PUBLIC)]


async def test_a_buyer_none_of_whose_addresses_answers_is_unavailable(party: Party, fake: FakeDgii) -> None:
    async def first_only(host: str, port: int) -> Sequence[IPv4Address | IPv6Address]:
        del host, port
        return (PUBLIC,)

    transport = FirstAddressFails(httpx.ASGITransport(app=fake.app), after_sending=False)
    async with over(party, simulator, transport, resolve=first_only).opened() as client:
        with pytest.raises(BuyerUnavailableError, match="its seed"):
            await client.deliver(T, credit_invoice(), await signed(party.signer))


async def test_a_receipt_that_says_received_and_gives_a_reason_is_refused(
    party: Party, fake: FakeDgii
) -> None:
    unsigned = arecf(ISSUER.rnc, BUYER.rnc or "", "E310000000001", reason=3, at=party.clock()).replace(
        b"<Estado>1</Estado>", b"<Estado>0</Estado>"
    )
    transport = answering(sign_with_signxml(unsigned, fake.state.receiver))
    async with over(party, listed(SIMULATOR), transport).opened() as client:
        with pytest.raises(ArecfContradictoryError):
            await client.deliver(T, credit_invoice(), await signed(party.signer))


def test_a_receipt_answered_but_wrong_is_invalid_and_a_buyer_that_failed_is_unavailable() -> None:
    invalid = (
        SchemaViolationError,
        SignatureInvalidError,
        DoctypeRefusedError,
        ArecfMismatchError,
        ArecfContradictoryError,
        ArecfMomentError,
        CounterpartyCertificateError,
        BuyerEndpointRefusedError,
    )
    assert {error.kind for error in invalid} == {"invalid"}
    assert BuyerUnavailableError.kind == "unavailable"
