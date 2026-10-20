import asyncio
import gzip
import logging
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Final, final, override
from uuid import UUID
lazy from collections.abc import Awaitable, Callable

import httpx
import pytest
from pydantic import TypeAdapter

from ledger_country_do.channels.ecf_v1.adapters.dgii.client import DgiiClient, read_answer
from ledger_country_do.channels.ecf_v1.adapters.dgii.endpoints import (
    DgiiHosts,
    LiveHostRefusedError,
    Service,
    ServiceNotInEnvironmentError,
)
from ledger_country_do.channels.ecf_v1.adapters.dgii.wire import TokenAnswer
from ledger_country_do.channels.ecf_v1.adapters.http import DEFAULT_TIMEOUTS, MAX_ANSWER, DgiiTimeouts
from ledger_country_do.channels.ecf_v1.adapters.signing.port import DocumentSigner
from ledger_country_do.channels.ecf_v1.adapters.xml import ecf, rfce
from ledger_country_do.channels.ecf_v1.adapters.xml.acecf import render_acecf
from ledger_country_do.channels.ecf_v1.adapters.xml.anecf import render_anecf
from ledger_country_do.channels.ecf_v1.application.ports import (
    Acknowledged,
    Answer,
    AuthorityUnavailableError,
    CertificateMissingError,
    CertificateNotValidError,
    DocumentUnsignableError,
    OutcomeUnknownError,
    Received,
    Refused,
    SignerClockSkewError,
    SignerUnavailableError,
)
from ledger_country_do.channels.ecf_v1.domain.encf import EcfType, Encf
from ledger_country_do.channels.ecf_v1.domain.environments import Environment
from ledger_country_do.channels.ecf_v1.domain.exchanges import CommercialApproval, VoidedRange
from ledger_country_do.channels.ecf_v1.domain.polling import TrackId
from ledger_country_do.channels.ecf_v1.domain.status_map import (
    DgiiMessage,
    DgiiService,
    EcfState,
    RfceQueryCode,
    decide,
)
from ledger_country_do.channels.ecf_v1.domain.totals import compute_totals
from ledger_country_do_testing.fake_dgii.state import Scripted, Status, VerifierCheck
from ledger_country_do_testing.local_signer import SIGNING_AS
from ledger_country_do_testing.samples import SIGNED_AT, consumer_invoice, credit_invoice
from ledger_country_do_testing.transports import FAKE_HOSTS, BreaksAfterSending, Counting, outbound_over
from ledger_fiscal_kit.ports import (
    SignatureRefusedError,
    SignerPort,
    SigningCertificateMissingError,
    SigningCertificateNotValidError,
    SigningClockSkewError,
)
from ledger_fiscal_kit.ports import SignerUnavailableError as PortUnavailableError
from ledger_kernel.ids import CompanyId
from ledger_platform.config.settings import LogFormat
from ledger_platform.http.outbound import OUTBOUND_SECONDS
from ledger_platform.observability.logs import configure_logging
from ledger_platform.observability.telemetry import Telemetry
from ledger_testing.logs import restored_logging
from ledger_testing.signer import TestSigner
lazy from ledger_country_do.channels.ecf_v1.domain.errors import EcfError
lazy from ledger_country_do_testing.fake_dgii.app import FakeDgii
lazy from ledger_country_do_testing.local_signer import KeyPairSigner
lazy from ledger_fiscal_kit.ports import CertificateValidity, SeedToSign, Signed, XmlToSign
lazy from ledger_kernel.errors import LedgerError
lazy from ledger_kernel.tenancy import Tenant
lazy from ledger_platform.runtime.calls import SystemCall
lazy from ledger_testing.clock import ManualClock

T: Final = Environment.TESTECF
WHO: Final = SIGNING_AS
ISSUER: Final = "101850043"
FIRST: Final = Encf(EcfType.CREDIT_INVOICE, 1)
SEED_PATH: Final = "/testecf/autenticacion/api/autenticacion/semilla"


async def signed(signer: KeyPairSigner, document: bytes) -> bytes:
    return signer.sign(document)


async def signed_ecf(signer: KeyPairSigner, sequence: int = 1, *, small_consumer: bool = False) -> bytes:
    draft = consumer_invoice(sequence) if small_consumer else credit_invoice(sequence)
    return await signed(signer, ecf.render(draft, compute_totals(draft), signed_at=SIGNED_AT))


def client_over(
    transport: httpx.AsyncBaseTransport,
    signer: SignerPort,
    clock: ManualClock,
    sleeps: list[float],
    timeouts: DgiiTimeouts | None = None,
) -> DgiiClient:
    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    documents = DocumentSigner(signer)
    if timeouts is None:
        return DgiiClient(
            documents, hosts=FAKE_HOSTS, clock=clock, sleep=sleep, http=outbound_over(transport)
        )
    return DgiiClient(
        documents,
        hosts=FAKE_HOSTS,
        timeouts=timeouts,
        clock=clock,
        sleep=sleep,
        http=outbound_over(transport),
    )


async def test_an_e_cf_is_received_then_decided(
    dgii: DgiiClient, signer: KeyPairSigner, fake: FakeDgii
) -> None:
    reception = await dgii.submit_ecf(WHO, T, ISSUER, FIRST, await signed_ecf(signer))
    assert isinstance(reception, Received)
    assert fake.received[-1].file_name == "101850043E310000000001.xml"
    assert fake.received[-1].host == "ecf.fake.test"
    assert await dgii.result(WHO, T, reception.track_id) == Answer(
        code=1, messages=(), sequence_used=True, rnc=ISSUER, encf="E310000000001"
    )


async def test_dgii_s_messages_and_secuencia_utilizada_arrive_intact(
    dgii: DgiiClient, signer: KeyPairSigner, fake: FakeDgii
) -> None:
    fake.script.default = Scripted(
        status=Status.REJECTED, messages=(("145", "El RNC no es emisor"),), sequence_used=False
    )
    reception = await dgii.submit_ecf(WHO, T, ISSUER, FIRST, await signed_ecf(signer))
    assert isinstance(reception, Received)
    answer = await dgii.result(WHO, T, reception.track_id)
    assert answer == Answer(
        code=2,
        messages=(DgiiMessage("145", "El RNC no es emisor"),),
        sequence_used=False,
        rnc=ISSUER,
        encf="E310000000001",
    )


async def test_a_refusal_at_the_door_is_an_answer_not_a_failure(
    dgii: DgiiClient, signer: KeyPairSigner
) -> None:
    refused = await dgii.submit_ecf(
        WHO, T, ISSUER, Encf(EcfType.CONSUMER_INVOICE, 1), await signed_ecf(signer, small_consumer=True)
    )
    assert isinstance(refused, Refused)
    assert refused.messages[0].code == "400"


async def test_the_rfce_is_answered_at_once_on_the_fc_host(
    dgii: DgiiClient, signer: KeyPairSigner, fake: FakeDgii
) -> None:
    draft = consumer_invoice()
    summary = await signed(signer, rfce.render(draft, compute_totals(draft), security_code="dcp79q"))
    answer = await dgii.submit_rfce(WHO, T, ISSUER, draft.encf, summary)
    assert answer == Answer(code=1, messages=(), sequence_used=True, encf="E320000000001")
    assert fake.received[-1].host == "fc.fake.test"
    assert fake.received[-1].file_name == f"101850043{draft.encf}.xml"
    refused = await dgii.submit_rfce(WHO, T, ISSUER, draft.encf, b"not xml")
    assert isinstance(refused, Refused)


async def test_the_rfce_s_messages_keep_their_string_codes_and_dgii_s_valor(
    dgii: DgiiClient, signer: KeyPairSigner, fake: FakeDgii
) -> None:
    fake.script.default = Scripted(
        status=Status.REJECTED, messages=(("RFCE-7", "El monto no cuadra"),), sequence_used=False
    )
    draft = consumer_invoice()
    summary = await signed(signer, rfce.render(draft, compute_totals(draft), security_code="dcp79q"))
    answer = await dgii.submit_rfce(WHO, T, ISSUER, draft.encf, summary)
    assert answer == Answer(
        code=2,
        messages=(DgiiMessage("RFCE-7", "El monto no cuadra"),),
        sequence_used=False,
        encf="E320000000001",
    )


async def test_track_ids_of_an_e_ncf_and_a_refusal_where_dgii_has_no_such_service(
    dgii: DgiiClient, signer: KeyPairSigner, clock: ManualClock
) -> None:
    first = await dgii.submit_ecf(WHO, T, ISSUER, FIRST, await signed_ecf(signer))
    second = await dgii.submit_ecf(WHO, T, ISSUER, FIRST, await signed_ecf(signer))
    assert isinstance(first, Received)
    assert isinstance(second, Received)
    assert await dgii.track_ids(WHO, T, ISSUER, FIRST) == (
        TrackId(first.track_id, clock.now),
        TrackId(second.track_id, clock.now),
    )
    with pytest.raises(ServiceNotInEnvironmentError):
        await dgii.track_ids(WHO, Environment.CERTECF, ISSUER, FIRST)


async def test_the_token_is_cached_and_renewed_five_minutes_before_it_expires(
    signer: KeyPairSigner, fake: FakeDgii, clock: ManualClock, sleeps: list[float]
) -> None:
    counting = Counting(httpx.ASGITransport(app=fake.app))
    async with client_over(counting, signer, clock, sleeps).opened() as dgii:
        await dgii.result(WHO, T, "a")
        await dgii.result(WHO, T, "b")
        clock.advance(timedelta(minutes=54))
        await dgii.result(WHO, T, "c")
        clock.advance(timedelta(minutes=2))
        await dgii.result(WHO, T, "d")
    assert counting.paths.count(SEED_PATH) == 2


async def test_one_token_is_fetched_however_many_calls_wait_for_it(
    signer: KeyPairSigner, fake: FakeDgii, clock: ManualClock, sleeps: list[float]
) -> None:
    counting = Counting(httpx.ASGITransport(app=fake.app))
    async with client_over(counting, signer, clock, sleeps).opened() as dgii:
        answers = await asyncio.gather(*(dgii.result(WHO, T, track_id) for track_id in "abcd"))
    assert [answer.code for answer in answers] == [0, 0, 0, 0]
    assert counting.paths.count(SEED_PATH) == 1


async def test_tokens_are_kept_per_environment_and_forgotten_when_closed(
    signer: KeyPairSigner, fake: FakeDgii, clock: ManualClock, sleeps: list[float]
) -> None:
    counting = Counting(httpx.ASGITransport(app=fake.app))
    client = client_over(counting, signer, clock, sleeps)
    async with client.opened() as dgii:
        await dgii.result(WHO, T, "a")
        await dgii.result(WHO, Environment.CERTECF, "a")
    async with client.opened() as dgii:
        await dgii.result(WHO, T, "a")
    seeds = [path for path in counting.paths if path.endswith("/semilla")]
    assert seeds == [SEED_PATH, "/certecf/autenticacion/api/autenticacion/semilla", SEED_PATH]


async def test_a_token_dgii_forgot_is_replaced_once(dgii: DgiiClient, fake: FakeDgii) -> None:
    await dgii.result(WHO, T, "a")
    fake.state.dgii.revoke()
    assert (await dgii.result(WHO, T, "a")).code == 0


async def test_what_cannot_change_dgii_s_state_is_retried_twice(
    dgii: DgiiClient, fake: FakeDgii, sleeps: list[float]
) -> None:
    await dgii.result(WHO, T, "warm the token")
    fake.script.outage = 2
    assert (await dgii.result(WHO, T, "a")).code == 0
    assert sleeps == [0.2, 0.5]
    fake.script.outage = 3
    with pytest.raises(AuthorityUnavailableError, match="server_error"):
        await dgii.result(WHO, T, "a")


async def test_a_submission_is_never_retried(
    dgii: DgiiClient, signer: KeyPairSigner, fake: FakeDgii, sleeps: list[float]
) -> None:
    await dgii.result(WHO, T, "warm the token")
    fake.script.outage = 1
    with pytest.raises(OutcomeUnknownError, match="server_error"):
        await dgii.submit_ecf(WHO, T, ISSUER, FIRST, await signed_ecf(signer))
    fake.script.outage = 1
    with pytest.raises(OutcomeUnknownError, match="server_error"):
        await dgii.submit_rfce(WHO, T, ISSUER, FIRST, await signed_ecf(signer))
    assert sleeps == []
    assert fake.received == []


async def test_a_connection_broken_after_sending_leaves_the_outcome_unknown(
    signer: KeyPairSigner, fake: FakeDgii, clock: ManualClock, sleeps: list[float]
) -> None:
    broken = BreaksAfterSending(httpx.ASGITransport(app=fake.app), "facturaselectronicas")
    async with client_over(broken, signer, clock, sleeps).opened() as dgii:
        with pytest.raises(OutcomeUnknownError):
            await dgii.submit_ecf(WHO, T, ISSUER, FIRST, await signed_ecf(signer))
    assert [each.encf for each in fake.received] == ["E310000000001"]
    assert sleeps == []


async def test_an_rfce_whose_connection_broke_after_sending_is_not_sent_again(
    signer: KeyPairSigner, fake: FakeDgii, clock: ManualClock, sleeps: list[float]
) -> None:
    broken = BreaksAfterSending(httpx.ASGITransport(app=fake.app), "recepcionfc")
    draft = consumer_invoice()
    summary = await signed(signer, rfce.render(draft, compute_totals(draft), security_code="dcp79q"))
    async with client_over(broken, signer, clock, sleeps).opened() as dgii:
        with pytest.raises(OutcomeUnknownError, match="outcome is unknown"):
            await dgii.submit_rfce(WHO, T, ISSUER, draft.encf, summary)
    assert [each.kind for each in fake.received] == ["rfce"]
    assert sleeps == []


async def test_the_live_environment_is_never_reached_through_a_fake(
    signer: KeyPairSigner, fake: FakeDgii, clock: ManualClock, sleeps: list[float]
) -> None:
    counting = Counting(httpx.ASGITransport(app=fake.app))
    async with client_over(counting, signer, clock, sleeps).opened() as dgii:
        with pytest.raises(LiveHostRefusedError):
            await dgii.result(WHO, Environment.ECF, "a")
        with pytest.raises(LiveHostRefusedError):
            await dgii.submit_ecf(WHO, Environment.ECF, ISSUER, FIRST, await signed_ecf(signer))
    assert counting.paths == []


async def test_the_live_environment_is_never_reached_through_an_injected_transport(
    signer: KeyPairSigner,
) -> None:
    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request.url.path)
        return reply(200, b"{}")

    documents = DocumentSigner(signer)
    async with DgiiClient(documents, http=outbound_over(httpx.MockTransport(handler))).opened() as dgii:
        with pytest.raises(LiveHostRefusedError):
            await dgii.result(WHO, Environment.ECF, "a")
        with pytest.raises(LiveHostRefusedError):
            await dgii.submit_rfce(WHO, Environment.ECF, ISSUER, FIRST, b"<RFCE/>")
    assert sent == []


SEED: Final = b"<SemillaModel><valor>v</valor><fecha>f</fecha></SemillaModel>"
GOOD_TOKEN: Final = b'{"token": "t", "expira": "2099-12-31T00:00:00Z"}'
"""Expires long after any test's clock: the client keeps it an hour at most (``TOKEN_CEILING``)."""


def reply(status: int, body: bytes = b"", headers: dict[str, str] | None = None) -> httpx.Response:
    """An answer whose body streams as a server's does (``content=`` would hand it over already read)."""
    return httpx.Response(status, headers=headers, stream=httpx.ByteStream(body))


def seeded(path: str) -> httpx.Response | None:
    """DGII's seed and a good token, for the paths of authentication; ``None`` for every other."""
    if path.endswith("/semilla"):
        return reply(200, SEED)
    if path.endswith("validarsemilla"):
        return reply(200, GOOD_TOKEN)
    return None


def scripted_dgii(token: bytes, status: int, answer: bytes, *, seed: bytes = SEED) -> httpx.MockTransport:
    """A DGII handing out ``seed``, answering ``token`` to its validation, ``answer`` to the rest."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/semilla"):
            return reply(200, seed)
        if request.url.path.endswith("validarsemilla"):
            return reply(200, token)
        return reply(status, answer)

    return httpx.MockTransport(handler)


@dataclass(frozen=True, slots=True)
class Undocumented:
    """DGII's answer to the seed's validation, then ``status``/``answer`` to ``call``: ``failure``."""

    token: bytes
    status: int
    answer: bytes
    call: str
    failure: str


@pytest.mark.parametrize(
    "case",
    [
        Undocumented(b"not json", 200, b"{}", "submit", "unreadable"),
        Undocumented(b'{"token": "t", "expira": "tomorrow"}', 200, b"{}", "submit", "unreadable"),
        Undocumented(
            rb'{"token": "t\r\nX-Injected: 1", "expira": "2026-10-13T14:30:00Z"}',
            200,
            b"{}",
            "submit",
            "unreadable",
        ),
        Undocumented(GOOD_TOKEN, 401, b"", "submit", "undocumented"),
        Undocumented(GOOD_TOKEN, 401, b"", "rfce", "undocumented"),
        Undocumented(GOOD_TOKEN, 429, b"", "submit", "throttled"),
        Undocumented(GOOD_TOKEN, 429, b"", "rfce", "throttled"),
        Undocumented(GOOD_TOKEN, 404, b"", "result", "undocumented"),
        Undocumented(GOOD_TOKEN, 302, b"", "result", "undocumented"),
        Undocumented(GOOD_TOKEN, 200, b"not json", "result", "unreadable"),
        Undocumented(GOOD_TOKEN, 200, b"[" + b" " * MAX_ANSWER + b"]", "result", "unreadable"),
        Undocumented(GOOD_TOKEN, 404, b"", "track_ids", "undocumented"),
        Undocumented(GOOD_TOKEN, 404, b"", "directory", "undocumented"),
        Undocumented(
            GOOD_TOKEN, 200, b'[{"trackId": "", "fechaRecepcion": "2026-10-13"}]', "track_ids", "unreadable"
        ),
        Undocumented(GOOD_TOKEN, 200, b'[{"trackId": "a"}]', "track_ids", "unreadable"),
        Undocumented(
            GOOD_TOKEN, 200, b'[{"trackId": "a", "fechaRecepcion": "ayer"}]', "track_ids", "unreadable"
        ),
    ],
    ids=[
        "token-not-json",
        "token-expiry",
        "token-not-a-header-value",
        "submit-401",
        "rfce-401",
        "submit-429",
        "rfce-429",
        "result-404",
        "result-redirect-not-followed",
        "result-not-json",
        "result-too-large",
        "track-ids-404",
        "directory-404",
        "empty-track-id",
        "no-reception-moment",
        "reception-moment-unreadable",
    ],
)
async def test_answers_dgii_does_not_document_are_failures(
    signer: KeyPairSigner, clock: ManualClock, sleeps: list[float], case: Undocumented
) -> None:
    transport = scripted_dgii(case.token, case.status, case.answer)
    async with client_over(transport, signer, clock, sleeps).opened() as dgii:
        with pytest.raises(AuthorityUnavailableError, match=case.failure):
            await call(dgii, case.call)
    assert sleeps == []


async def call(dgii: DgiiClient, service: str, environment: Environment = T) -> object:
    """The client's call of ``service``, with arguments that do not matter to the answer."""
    calls: dict[str, Callable[[], Awaitable[object]]] = {
        "submit": lambda: dgii.submit_ecf(WHO, environment, ISSUER, FIRST, b"<ECF/>"),
        "rfce": lambda: dgii.submit_rfce(WHO, environment, ISSUER, FIRST, b"<RFCE/>"),
        "track_ids": lambda: dgii.track_ids(WHO, environment, ISSUER, FIRST),
        "directory": lambda: dgii.directory(WHO, environment, "131098193"),
        "void": lambda: dgii.void_ranges(WHO, environment, b"<ANECF/>"),
        "approval": lambda: dgii.commercial_approval(WHO, environment, b"<ACECF/>"),
        "result": lambda: dgii.result(WHO, environment, "a"),
    }
    return await calls[service]()


@dataclass(frozen=True, slots=True)
class MayHaveTaken:
    """``status``/``answer`` to a submission (``service``) that DGII may have taken: ``reason``."""

    status: int
    answer: bytes
    service: str
    reason: str


@pytest.mark.parametrize(
    "case",
    [
        MayHaveTaken(200, b"not json", "submit", "unreadable"),
        MayHaveTaken(200, b"[" + b" " * MAX_ANSWER + b"]", "submit", "unreadable"),
        MayHaveTaken(200, b'{"trackId": null}', "submit", "undocumented"),
        MayHaveTaken(201, b'{"trackId": ""}', "submit", "undocumented"),
        MayHaveTaken(500, b"", "submit", "server_error"),
        MayHaveTaken(200, b"not json", "rfce", "unreadable"),
        MayHaveTaken(502, b"", "rfce", "server_error"),
        MayHaveTaken(404, b"", "submit", "undocumented"),
        MayHaveTaken(302, b"", "submit", "undocumented"),
        MayHaveTaken(404, b"", "rfce", "undocumented"),
    ],
    ids=[
        "not-json",
        "too-large",
        "no-track-id",
        "empty-track-id",
        "500",
        "rfce-not-json",
        "rfce-502",
        "submit-404",
        "submit-redirect-not-followed",
        "rfce-404",
    ],
)
async def test_a_submission_dgii_may_have_taken_has_an_unknown_outcome(
    signer: KeyPairSigner, clock: ManualClock, sleeps: list[float], case: MayHaveTaken
) -> None:
    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request.url.path)
        return seeded(request.url.path) or reply(case.status, case.answer)

    async with client_over(httpx.MockTransport(handler), signer, clock, sleeps).opened() as dgii:
        with pytest.raises(OutcomeUnknownError, match=f"{case.reason}.*outcome is unknown") as raised:
            await call(dgii, case.service)
    assert raised.value.reason == case.reason
    assert len(sent) == len(("seed", "validation", "the submission, once"))
    assert sleeps == []


async def test_a_submission_dgii_refuses_twice_as_unauthorized_is_sent_exactly_twice(
    signer: KeyPairSigner, clock: ManualClock, sleeps: list[float]
) -> None:
    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request.url.path.rsplit("/", 1)[-1])
        return seeded(request.url.path) or reply(401)

    async with client_over(httpx.MockTransport(handler), signer, clock, sleeps).opened() as dgii:
        with pytest.raises(AuthorityUnavailableError, match="reception: undocumented"):
            await dgii.submit_ecf(WHO, T, ISSUER, FIRST, b"<ECF/>")
    assert sent == ["semilla", "validarsemilla", "facturaselectronicas"] * 2


async def test_only_the_token_dgii_refused_is_dropped(
    signer: KeyPairSigner, clock: ManualClock, sleeps: list[float]
) -> None:
    issued: list[str] = []
    refused: list[str] = []
    both_sent = asyncio.Event()
    renewed_used = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/semilla"):
            return reply(200, SEED)
        if request.url.path.endswith("validarsemilla"):
            issued.append(f"t{len(issued) + 1}")
            return reply(200, b'{"token": "' + issued[-1].encode() + b'", "expira": "2099-12-31T00:00:00Z"}')
        if request.headers["authorization"] != "Bearer t1":
            renewed_used.set()
            return reply(200, b'{"codigo": 0}')
        refused.append(request.url.path)
        place = len(refused)
        if place == len(("first", "second")):
            both_sent.set()
        await both_sent.wait()
        if place > 1:
            await renewed_used.wait()
        return reply(401)

    async with client_over(httpx.MockTransport(handler), signer, clock, sleeps).opened() as dgii:
        answers = await asyncio.gather(dgii.result(WHO, T, "a"), dgii.result(WHO, T, "b"))
        assert [answer.code for answer in answers] == [0, 0]
        assert (await dgii.result(WHO, T, "c")).code == 0
    assert issued == ["t1", "t2"]


@pytest.mark.parametrize("failure", ["broken", "server_error"])
async def test_a_failed_authentication_starts_again_from_a_fresh_seed(
    signer: KeyPairSigner, clock: ManualClock, sleeps: list[float], failure: str
) -> None:
    sent: list[str] = []
    validated: list[bytes] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path.rsplit("/", 1)[-1]
        sent.append(path)
        if path == "semilla":
            return reply(200, SEED.replace(b">v<", f">v{sent.count('semilla')}<".encode()))
        if path == "validarsemilla":
            validated.append(await request.aread())
            if len(validated) == 1 and failure == "broken":
                reason = "reset"
                raise httpx.ReadError(reason, request=request)
            if len(validated) == 1:
                return reply(503)
            return reply(200, GOOD_TOKEN)
        return reply(200, b'{"codigo": 0}')

    async with client_over(httpx.MockTransport(handler), signer, clock, sleeps).opened() as dgii:
        assert (await dgii.result(WHO, T, "a")).code == 0
    assert sent == ["semilla", "validarsemilla", "semilla", "validarsemilla", "estado"]
    assert b"<valor>v1</valor>" in validated[0]
    assert b"<valor>v2</valor>" in validated[1]
    assert sleeps == [0.2]


async def test_an_authentication_that_keeps_failing_is_tried_three_times(
    signer: KeyPairSigner, clock: ManualClock, sleeps: list[float]
) -> None:
    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path.rsplit("/", 1)[-1]
        sent.append(path)
        return reply(200, SEED) if path == "semilla" else reply(503)

    async with client_over(httpx.MockTransport(handler), signer, clock, sleeps).opened() as dgii:
        with pytest.raises(AuthorityUnavailableError, match="validate_seed: server_error"):
            await dgii.result(WHO, T, "a")
    assert sent == ["semilla", "validarsemilla"] * 3
    assert sleeps == [0.2, 0.5]


@pytest.mark.parametrize(
    "seed",
    [
        b"not xml",
        b'<!DOCTYPE SemillaModel [<!ENTITY x "y">]><SemillaModel><valor>&x;</valor></SemillaModel>',
        b"<Other><valor>v</valor><fecha>f</fecha></Other>",
        b"<SemillaModel>" + b"x" * MAX_ANSWER + b"</SemillaModel>",
    ],
    ids=["not-xml", "doctype", "not-a-seed", "too-large"],
)
async def test_a_seed_dgii_would_not_send_is_never_signed(
    signer: KeyPairSigner, clock: ManualClock, sleeps: list[float], seed: bytes
) -> None:
    async with client_over(
        scripted_dgii(GOOD_TOKEN, 200, b"{}", seed=seed), signer, clock, sleeps
    ).opened() as dgii:
        with pytest.raises(AuthorityUnavailableError, match="seed: unreadable"):
            await dgii.result(WHO, T, "a")


def seed_signer(signer: KeyPairSigner, *failures: LedgerError) -> TestSigner:
    """Signs with ``signer``'s key, refusing the first calls with ``failures``; records each request."""
    return TestSigner(sign=signer.sign, sign_seed=signer.sign, failures=failures)


async def test_a_seed_is_signed_for_dgii_s_host_as_the_company_the_call_is_for(
    signer: KeyPairSigner, fake: FakeDgii, clock: ManualClock, sleeps: list[float]
) -> None:
    recording = seed_signer(signer)
    async with client_over(httpx.ASGITransport(app=fake.app), recording, clock, sleeps).opened() as dgii:
        await dgii.result(WHO, T, "a")
    official = DgiiClient(
        DocumentSigner(recording), http=outbound_over(scripted_dgii(GOOD_TOKEN, 200, b'{"codigo": 0}'))
    )
    async with official.opened():
        await official.result(WHO, T, "a")
    assert [call.audience for call in recording.calls] == ["ecf.fake.test", "ecf.dgii.gov.do"]
    assert {call.tenant for call in recording.calls} == {WHO.tenant}


async def test_a_seed_the_signer_refuses_to_sign_is_unreadable(
    signer: KeyPairSigner, clock: ManualClock, sleeps: list[float]
) -> None:
    refusing = seed_signer(signer, SignatureRefusedError("xml_document_unsignable"))
    async with client_over(scripted_dgii(GOOD_TOKEN, 200, b"{}"), refusing, clock, sleeps).opened() as dgii:
        with pytest.raises(AuthorityUnavailableError, match="seed: unreadable") as raised:
            await dgii.result(WHO, T, "a")
    assert isinstance(raised.value.__cause__, DocumentUnsignableError)


@pytest.mark.parametrize(
    ("refusal", "raised"),
    [
        (SigningCertificateMissingError(), CertificateMissingError),
        (SigningCertificateNotValidError(), CertificateNotValidError),
        (SigningClockSkewError(), SignerClockSkewError),
        (PortUnavailableError(), SignerUnavailableError),
    ],
    ids=["missing", "not-valid", "clock-skew", "unavailable"],
)
async def test_the_signer_s_other_refusals_pass_as_the_channel_s_and_nothing_is_sent(
    signer: KeyPairSigner,
    clock: ManualClock,
    sleeps: list[float],
    refusal: LedgerError,
    raised: type[EcfError],
) -> None:
    # The emitter parks on them by their own codes (a certificate to install, a clock to fix, a signer to
    # wait for): the e-CF never left.
    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request.url.path.rsplit("/", 1)[-1])
        return seeded(request.url.path) or reply(200, b"{}")

    refusing = seed_signer(signer, refusal)
    async with client_over(httpx.MockTransport(handler), refusing, clock, sleeps).opened() as dgii:
        with pytest.raises(raised) as caught:
            await dgii.submit_ecf(WHO, T, ISSUER, FIRST, b"<ECF/>")
    assert caught.value.__cause__ is refusal
    assert sent == ["semilla"]
    assert sleeps == []


async def test_a_seed_or_its_validation_refused_is_a_failure(
    signer: KeyPairSigner, clock: ManualClock, sleeps: list[float]
) -> None:
    def no_seed(request: httpx.Request) -> httpx.Response:
        del request
        return reply(404)

    def refuses(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/semilla"):
            return reply(200, SEED)
        return reply(400, b'{"trackId": null, "error": "400", "mensaje": "La firma no es valida"}')

    def undocumented(request: httpx.Request) -> httpx.Response:
        return reply(200, SEED) if request.url.path.endswith("/semilla") else reply(404)

    async with client_over(httpx.MockTransport(no_seed), signer, clock, sleeps).opened() as dgii:
        with pytest.raises(AuthorityUnavailableError, match="seed: undocumented"):
            await dgii.result(WHO, T, "a")
    async with client_over(httpx.MockTransport(refuses), signer, clock, sleeps).opened() as dgii:
        with pytest.raises(AuthorityUnavailableError, match="validate_seed: refused"):
            await dgii.result(WHO, T, "a")
    async with client_over(httpx.MockTransport(undocumented), signer, clock, sleeps).opened() as dgii:
        with pytest.raises(AuthorityUnavailableError, match="validate_seed: undocumented"):
            await dgii.result(WHO, T, "a")
    assert sleeps == []


async def test_a_client_with_every_default_reaches_dgii_s_own_hosts(signer: KeyPairSigner) -> None:
    hosts: list[str] = []
    encodings: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        encodings.append(request.headers["accept-encoding"])
        return seeded(request.url.path) or reply(200, b'{"codigo": 0}')

    documents = DocumentSigner(signer)
    async with DgiiClient(documents, http=outbound_over(httpx.MockTransport(handler))).opened() as dgii:
        assert (await dgii.result(WHO, T, "a")).code == 0
    assert set(hosts) == {"ecf.dgii.gov.do"}
    assert set(encodings) == {"gzip"}


async def test_no_error_quotes_a_token(
    signer: KeyPairSigner, clock: ManualClock, sleeps: list[float]
) -> None:
    secret = b"eyJhbGciOiJIUzI1NiJ9.c2VjcmV0.dGhpcy1pcy1zZWNyZXQ"
    token = b'{"token": "' + secret + b'", "expira": "' + b"9" * 65 + b'"}'
    async with client_over(scripted_dgii(token, 200, b"{}"), signer, clock, sleeps).opened() as dgii:
        with pytest.raises(AuthorityUnavailableError) as raised:
            await dgii.result(WHO, T, "a")
    told = f"{raised.value} {raised.value!r} {raised.value.__cause__} {raised.value.__cause__!r}"
    assert secret.decode() not in told
    assert "unreadable" in told


EMPTY_LIST: Final = gzip.compress(b"[]")


@pytest.mark.parametrize(
    ("encoding", "body"),
    [
        ("gzip, gzip", gzip.compress(EMPTY_LIST)),
        ("br", b"[]"),
        ("deflate", b"[]"),
        ("gzip", gzip.compress(b"[" + b" " * (4 * MAX_ANSWER) + b"]")),
        ("gzip", b"not gzip at all"),
        ("gzip", EMPTY_LIST[:-4]),
        ("gzip", EMPTY_LIST + EMPTY_LIST),
    ],
    ids=["gzip-twice", "brotli", "deflate", "bomb", "not-gzip", "truncated", "two-members"],
)
async def test_an_answer_is_decompressed_once_and_bounded_as_it_expands(
    signer: KeyPairSigner, clock: ManualClock, sleeps: list[float], encoding: str, body: bytes
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return seeded(request.url.path) or reply(200, body, {"Content-Encoding": encoding})

    async with client_over(httpx.MockTransport(handler), signer, clock, sleeps).opened() as dgii:
        with pytest.raises(AuthorityUnavailableError, match="track_ids: unreadable"):
            await dgii.track_ids(WHO, T, ISSUER, FIRST)


@pytest.mark.parametrize(
    ("encoding", "body"), [("gzip", EMPTY_LIST), ("identity", b"[]"), (" GZIP ", EMPTY_LIST)]
)
async def test_an_answer_plain_or_gzipped_is_read(
    signer: KeyPairSigner, clock: ManualClock, sleeps: list[float], encoding: str, body: bytes
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return seeded(request.url.path) or reply(200, body, {"Content-Encoding": encoding})

    async with client_over(httpx.MockTransport(handler), signer, clock, sleeps).opened() as dgii:
        assert await dgii.track_ids(WHO, T, ISSUER, FIRST) == ()


def test_an_answer_read_whole_is_bounded_too() -> None:
    oversized = httpx.Response(200, content=b'{"token": "t", "expira": "' + b" " * MAX_ANSWER + b'"}')
    with pytest.raises(AuthorityUnavailableError, match="validate_seed: unreadable"):
        read_answer(TypeAdapter(TokenAnswer), oversized, Service.VALIDATE_SEED)


async def test_track_ids_are_ordered_by_when_dgii_received_them(
    signer: KeyPairSigner, clock: ManualClock, sleeps: list[float]
) -> None:
    answer = (
        b'[{"trackId": "c", "fechaRecepcion": "2026-10-13T12:00:00-04:00"},'
        b' {"trackId": "a", "fechaRecepcion": "13-10-2026 09:15:00"},'
        b' {"trackId": "b", "fechaRecepcion": "2026-10-13T14:00:00Z"},'
        b' {"trackId": "d", "fechaRecepcion": "2026-10-13T12:00:00"}]'
    )
    async with client_over(scripted_dgii(GOOD_TOKEN, 200, answer), signer, clock, sleeps).opened() as dgii:
        found = await dgii.track_ids(WHO, T, ISSUER, FIRST)
    assert found is not None
    assert tuple(each.track_id for each in found) == ("a", "b", "c", "d")
    assert found[0].received_at == datetime(2026, 10, 13, 13, 15, tzinfo=UTC)


async def test_a_broken_answer_to_a_query_is_retried_then_a_failure(
    signer: KeyPairSigner, fake: FakeDgii, clock: ManualClock, sleeps: list[float]
) -> None:
    broken = BreaksAfterSending(httpx.ASGITransport(app=fake.app), "consultaresultado")
    async with client_over(broken, signer, clock, sleeps).opened() as dgii:
        with pytest.raises(AuthorityUnavailableError, match="broken"):
            await dgii.result(WHO, T, "a")
    assert sleeps == [0.2, 0.5]


async def stalls(request: httpx.Request) -> httpx.Response:
    """Authenticates at once, then never answers in time."""
    if (answer := seeded(request.url.path)) is not None:
        return answer
    await asyncio.sleep(60)
    return reply(200, b"{}")


async def test_every_attempt_has_a_time_limit(
    signer: KeyPairSigner, clock: ManualClock, sleeps: list[float]
) -> None:
    quick = DgiiTimeouts(connect=0.5, total=0.5)
    async with client_over(httpx.MockTransport(stalls), signer, clock, sleeps, quick).opened() as dgii:
        with pytest.raises(AuthorityUnavailableError, match="result: broken"):
            await dgii.result(WHO, T, "a")
        assert sleeps == [0.2, 0.5]
        with pytest.raises(OutcomeUnknownError, match="broken"):
            await dgii.submit_ecf(WHO, T, ISSUER, FIRST, b"<ECF/>")
    assert sleeps == [0.2, 0.5]


def test_a_call_s_overall_deadline_is_a_minute_by_default() -> None:
    assert DgiiTimeouts().call == 60.0


async def test_every_call_has_an_overall_deadline(
    signer: KeyPairSigner, clock: ManualClock, sleeps: list[float]
) -> None:
    tight = DgiiTimeouts(connect=5, total=5, call=0.1)
    async with client_over(httpx.MockTransport(stalls), signer, clock, sleeps, tight).opened() as dgii:
        with pytest.raises(AuthorityUnavailableError, match="result: timed_out"):
            await dgii.result(WHO, T, "a")
        with pytest.raises(AuthorityUnavailableError, match="reception: timed_out"):
            await dgii.submit_ecf(WHO, T, ISSUER, FIRST, b"<ECF/>")
    assert sleeps == []


async def test_no_retry_is_waited_for_past_the_deadline(
    signer: KeyPairSigner, clock: ManualClock, sleeps: list[float]
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return seeded(request.url.path) or reply(503)

    tight = DgiiTimeouts(connect=5, total=5, call=0.15)
    async with client_over(httpx.MockTransport(handler), signer, clock, sleeps, tight).opened() as dgii:
        with pytest.raises(AuthorityUnavailableError, match="result: server_error"):
            await dgii.result(WHO, T, "a")
    assert sleeps == []


@final
class SlowSigner(SignerPort):
    """Signs seeds slowly, as a signer process that takes its time."""

    __slots__ = ("_inner",)

    def __init__(self, inner: KeyPairSigner) -> None:
        self._inner = inner

    @override
    async def sign_xml(self, call: SystemCall, request: XmlToSign) -> Signed:
        raise AssertionError  # authentication signs seeds only

    @override
    async def sign_seed(self, call: SystemCall, request: SeedToSign) -> Signed:
        await asyncio.sleep(60)
        return await self._inner.sign_seed(call, request)

    @override
    async def certificate_validity(self, call: SystemCall, tenant: Tenant) -> CertificateValidity | None:
        raise AssertionError  # the client never asks


async def test_waiting_for_a_token_ends_with_the_deadline(
    signer: KeyPairSigner, clock: ManualClock, sleeps: list[float]
) -> None:
    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request.url.path.rsplit("/", 1)[-1])
        return seeded(request.url.path) or reply(200, b"{}")

    tight = DgiiTimeouts(connect=5, total=5, call=0.1)
    slow = SlowSigner(signer)
    async with client_over(httpx.MockTransport(handler), slow, clock, sleeps, tight).opened() as dgii:
        first, second = await asyncio.gather(
            dgii.result(WHO, T, "a"),
            dgii.submit_ecf(WHO, T, ISSUER, FIRST, b"<ECF/>"),
            return_exceptions=True,
        )
        assert sleeps == []
    assert isinstance(first, AuthorityUnavailableError)
    assert first.failure == "timed_out"
    assert isinstance(second, AuthorityUnavailableError)
    assert str(second) == "DGII's reception: timed_out"
    # Each call has its own deadline: under load the second may fetch a seed of its own, never send the e-CF.
    assert set(sent) == {"semilla"}


async def test_a_token_expiry_without_a_zone_is_read_as_utc(
    signer: KeyPairSigner, clock: ManualClock, sleeps: list[float]
) -> None:
    naive = b'{"token": "t", "expira": "2026-10-13T14:30:00"}'
    async with client_over(
        scripted_dgii(naive, 200, b'{"codigo": 0}'), signer, clock, sleeps
    ).opened() as dgii:
        assert (await dgii.result(WHO, T, "a")).code == 0
        assert (await dgii.result(WHO, T, "b")).code == 0


async def test_a_buyer_missing_from_the_directory_is_none(
    signer: KeyPairSigner, clock: ManualClock, sleeps: list[float]
) -> None:
    async with client_over(scripted_dgii(GOOD_TOKEN, 200, b"[]"), signer, clock, sleeps).opened() as dgii:
        assert await dgii.directory(WHO, T, "131098193") is None


async def test_dgii_unreachable_and_a_client_not_opened_are_failures(
    signer: KeyPairSigner, clock: ManualClock, sleeps: list[float]
) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        reason = "refused"
        raise httpx.ConnectError(reason, request=request)

    async with client_over(httpx.MockTransport(refuse), signer, clock, sleeps).opened() as dgii:
        with pytest.raises(AuthorityUnavailableError, match="unreachable"):
            await dgii.result(WHO, T, "a")
    with pytest.raises(AuthorityUnavailableError, match="not_open"):
        await dgii.result(WHO, T, "a")


async def test_the_directory_names_testecf_s_simulator(dgii: DgiiClient) -> None:
    entry = await dgii.directory(WHO, T, "131098193")
    assert entry is not None
    assert (entry.reception, entry.authentication) == (
        "https://ecf.fake.test/testecf/emisorreceptor",
        "https://ecf.fake.test/testecf/emisorreceptor",
    )
    assert entry.rnc == "131098193"


OTHER_ENTRY: Final = b'[{"rnc": "101850043", "urlRecepcion": "https://a.example.com"}]'


@pytest.mark.parametrize(
    ("listed", "found"),
    [
        (OTHER_ENTRY, ("101850043", "https://a.example.com")),
        (
            OTHER_ENTRY[:-1] + b', {"rnc": " 131098193 ", "urlRecepcion": "https://b.example.com"}]',
            ("131098193", "https://b.example.com"),
        ),
        (b"[]", None),
    ],
    ids=["another-rnc", "ours-second", "none"],
)
async def test_the_directory_answers_the_entry_of_the_rnc_asked_about_or_says_whose_it_is(
    signer: KeyPairSigner,
    clock: ManualClock,
    sleeps: list[float],
    listed: bytes,
    found: tuple[str, str] | None,
) -> None:
    # An entry naming another taxpayer is told as such (its rnc): whoever sends compares it first.
    async with client_over(scripted_dgii(GOOD_TOKEN, 200, listed), signer, clock, sleeps).opened() as dgii:
        entry = await dgii.directory(WHO, T, "131098193")
    assert (None if entry is None else (entry.rnc, entry.reception)) == found


async def test_a_document_is_not_sent_with_less_time_left_than_an_attempt_may_take(
    signer: KeyPairSigner, clock: ManualClock, sleeps: list[float]
) -> None:
    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request.url.path.rsplit("/", 1)[-1])
        return seeded(request.url.path) or reply(200, b'{"codigo": 0}')

    short = DgiiTimeouts(connect=5, total=5, call=1)
    async with client_over(httpx.MockTransport(handler), signer, clock, sleeps, short).opened() as dgii:
        with pytest.raises(AuthorityUnavailableError, match="reception: timed_out"):
            await dgii.submit_ecf(WHO, T, ISSUER, FIRST, b"<ECF/>")
        with pytest.raises(AuthorityUnavailableError, match="rfce: timed_out"):
            await dgii.submit_rfce(WHO, T, ISSUER, FIRST, b"<RFCE/>")
        assert (await dgii.result(WHO, T, "a")).code == 0
    assert sent == ["semilla", "validarsemilla", "estado"]


async def test_a_document_refused_401_is_not_sent_again_once_too_little_time_is_left(
    signer: KeyPairSigner, clock: ManualClock, sleeps: list[float]
) -> None:
    sent: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request.url.path.rsplit("/", 1)[-1])
        if (found := seeded(request.url.path)) is not None:
            return found
        await asyncio.sleep(1.0)  # a slow server: its refusal comes a second after the request
        return reply(401)

    tight = DgiiTimeouts(connect=5, total=2, call=2.5)
    async with client_over(httpx.MockTransport(handler), signer, clock, sleeps, tight).opened() as dgii:
        with pytest.raises(AuthorityUnavailableError, match="reception: timed_out"):
            await dgii.submit_ecf(WHO, T, ISSUER, FIRST, b"<ECF/>")
    assert sent.count("facturaselectronicas") == 1


async def test_a_result_names_its_e_ncf_as_either_spelling_and_an_overlong_one_is_unreadable(
    signer: KeyPairSigner, clock: ManualClock, sleeps: list[float]
) -> None:
    lower = b'{"codigo": 1, "rnc": "101850043", "encf": "E310000000001"}'
    async with client_over(scripted_dgii(GOOD_TOKEN, 200, lower), signer, clock, sleeps).opened() as dgii:
        answer = await dgii.result(WHO, T, "a")
    assert (answer.rnc, answer.encf) == (ISSUER, "E310000000001")
    overlong = b'{"codigo": 1, "eNCF": "E3100000000011"}'
    async with client_over(scripted_dgii(GOOD_TOKEN, 200, overlong), signer, clock, sleeps).opened() as dgii:
        with pytest.raises(AuthorityUnavailableError, match="result: unreadable"):
            await dgii.result(WHO, T, "a")


async def test_a_client_is_ready_for_an_environment_only_open_and_on_hosts_that_may_serve_it(
    signer: KeyPairSigner, fake: FakeDgii, clock: ManualClock, sleeps: list[float]
) -> None:
    client = client_over(httpx.ASGITransport(app=fake.app), signer, clock, sleeps)
    with pytest.raises(AuthorityUnavailableError, match="not_open"):
        client.require_ready(T)
    async with client.opened():
        client.require_ready(T)
        client.require_ready(Environment.CERTECF)
        with pytest.raises(LiveHostRefusedError):
            client.require_ready(Environment.ECF)
    assert fake.received == []


async def test_a_client_is_ready_only_when_every_host_the_environment_uses_may_serve_it(
    signer: KeyPairSigner,
    fake: FakeDgii,
    clock: ManualClock,
    sleeps: list[float],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = DgiiHosts.url

    def fc_refused(self: DgiiHosts, service: Service, environment: Environment) -> str:
        if service is Service.RFCE:
            raise LiveHostRefusedError
        return original(self, service, environment)

    async with client_over(httpx.ASGITransport(app=fake.app), signer, clock, sleeps).opened() as client:
        monkeypatch.setattr(DgiiHosts, "url", fc_refused)
        with pytest.raises(LiveHostRefusedError):
            client.require_ready(T)


def test_a_client_s_longest_attempt_at_sending_is_its_per_attempt_limit(signer: KeyPairSigner) -> None:
    network, documents = outbound_over(None), DocumentSigner(signer)
    assert DgiiClient(documents, http=network).attempt_limit() == DEFAULT_TIMEOUTS.total
    assert DgiiClient(documents, http=network, timeouts=DgiiTimeouts(total=42.0)).attempt_limit() == 42.0


@pytest.mark.parametrize("service", ["submit", "rfce"])
async def test_a_refusal_at_the_door_that_does_not_read_is_still_a_refusal(
    signer: KeyPairSigner, clock: ManualClock, sleeps: list[float], service: str
) -> None:
    transport = scripted_dgii(GOOD_TOKEN, 400, b"not json")
    async with client_over(transport, signer, clock, sleeps).opened() as dgii:
        refused = await call(dgii, service)
    assert refused == Refused((DgiiMessage("400", ""),))


async def test_dgii_is_called_through_the_channel_s_outbound_client_named_dgii(
    signer: KeyPairSigner, fake: FakeDgii, clock: ManualClock
) -> None:
    telemetry = Telemetry.unexported("do-ecf-v1")
    http = outbound_over(httpx.ASGITransport(app=fake.app), telemetry=telemetry)
    async with DgiiClient(DocumentSigner(signer), http=http, hosts=FAKE_HOSTS, clock=clock).opened() as dgii:
        await dgii.submit_ecf(WHO, T, ISSUER, FIRST, await signed_ecf(signer))
    seen = {
        (sample.labels["client"], sample.labels["outcome"]): sample.value
        for metric in telemetry.registry.collect()
        if metric.name == OUTBOUND_SECONDS
        for sample in metric.samples
        if sample.name.endswith("_count")
    }
    assert seen == {("dgii", "2xx"): 3}  # the seed, its validation, the submission


# -- One client for every company; RFCE query, range voids, approvals; services by environment ------------


OTHER: Final = replace(
    SIGNING_AS, company=CompanyId(UUID("0199e3a0-0000-7000-8000-0000000000b2")), reason="do-ecf-v1.other"
)
"""A second issuing company of the same organization."""


async def test_two_companies_get_a_token_each_from_a_seed_each_signs(
    signer: KeyPairSigner, fake: FakeDgii, clock: ManualClock, sleeps: list[float]
) -> None:
    recording = seed_signer(signer)
    counting = Counting(httpx.ASGITransport(app=fake.app))
    async with client_over(counting, recording, clock, sleeps).opened() as dgii:
        for _ in range(2):
            await dgii.result(WHO, T, "a")
            await dgii.result(OTHER, T, "a")
    assert counting.paths.count(SEED_PATH) == 2
    assert [call.tenant for call in recording.calls] == [WHO.tenant, OTHER.tenant]
    assert len(set(fake.state.dgii.issued())) == 2


async def test_a_token_dgii_refuses_is_renewed_for_its_company_only(
    signer: KeyPairSigner, clock: ManualClock, sleeps: list[float]
) -> None:
    issued: list[str] = []
    used: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/semilla"):
            return reply(200, SEED)
        if request.url.path.endswith("validarsemilla"):
            issued.append(f"t{len(issued) + 1}")
            return reply(200, b'{"token": "' + issued[-1].encode() + b'", "expira": "2099-12-31T00:00:00Z"}')
        token = request.headers["authorization"].removeprefix("Bearer ")
        used.append(token)
        return reply(401) if token == "t1" and used.count("t1") == 2 else reply(200, b'{"codigo": 0}')

    recording = seed_signer(signer)
    async with client_over(httpx.MockTransport(handler), recording, clock, sleeps).opened() as dgii:
        await dgii.result(WHO, T, "a")
        await dgii.result(OTHER, T, "a")
        await dgii.result(WHO, T, "b")
        await dgii.result(OTHER, T, "b")
    assert issued == ["t1", "t2", "t3"]
    assert used == ["t1", "t2", "t1", "t3", "t2"]
    assert [call.tenant for call in recording.calls] == [WHO.tenant, OTHER.tenant, WHO.tenant]


async def signed_summary(signer: KeyPairSigner) -> tuple[str, bytes]:
    draft = consumer_invoice()
    return "dcp79q", signer.sign(rfce.render(draft, compute_totals(draft), security_code="dcp79q"))


@dataclass(frozen=True, slots=True)
class Queried:
    """An RFCE the fake decided ``status``: ``consultarfce`` answers ``code``, which maps to ``state``."""

    status: Status
    code: RfceQueryCode
    state: EcfState


@pytest.mark.parametrize(
    "case",
    [
        Queried(Status.ACCEPTED, RfceQueryCode.ACCEPTED, EcfState.ACCEPTED),
        Queried(
            Status.ACCEPTED_CONDITIONALLY,
            RfceQueryCode.ACCEPTED_CONDITIONALLY,
            EcfState.ACCEPTED_CONDITIONALLY,
        ),
        Queried(Status.REJECTED, RfceQueryCode.REJECTED, EcfState.REJECTED),
    ],
    ids=["accepted", "accepted-conditionally", "rejected"],
)
async def test_an_rfce_sent_is_asked_about_on_the_fc_host_and_mapped_by_consultarfce_s_codes(
    dgii: DgiiClient, signer: KeyPairSigner, fake: FakeDgii, case: Queried
) -> None:
    fake.script.default = Scripted(status=case.status)
    security, summary = await signed_summary(signer)
    draft = consumer_invoice()
    await dgii.submit_rfce(WHO, T, ISSUER, draft.encf, summary)
    answer = await dgii.rfce_status(WHO, T, ISSUER, draft.encf, security)
    outcome = decide(
        answer.code,
        sequence_used=answer.sequence_used,
        messages=answer.messages,
        service=DgiiService.RFCE_QUERY,
    )
    assert (outcome.code, outcome.state, outcome.final) == (case.code, case.state, True)
    assert (answer.rnc, answer.encf) == (ISSUER, str(draft.encf))
    assert fake.received[-1].host == "fc.fake.test"


async def test_consultarfce_is_asked_on_dgii_s_fc_host(
    signer: KeyPairSigner, clock: ManualClock, sleeps: list[float]
) -> None:
    asked: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append((request.url.host, request.url.path))
        return seeded(request.url.path) or reply(200, b'{"codigo": "0"}')

    async with client_over(httpx.MockTransport(handler), signer, clock, sleeps).opened() as dgii:
        await dgii.rfce_status(WHO, T, ISSUER, Encf(EcfType.CONSUMER_INVOICE, 9), "abc123")
    assert asked[-1] == ("fc.fake.test", "/testecf/consultarfce/api/consultas/consulta")
    assert {host for host, _ in asked[:-1]} == {"ecf.fake.test"}  # the token is the ecf host's


async def test_an_rfce_dgii_has_not_recorded_is_no_encontrado_and_not_final(
    dgii: DgiiClient, fake: FakeDgii
) -> None:
    sent = len(fake.received)
    answer = await dgii.rfce_status(WHO, T, ISSUER, Encf(EcfType.CONSUMER_INVOICE, 9), "abc123")
    outcome = decide(
        answer.code,
        sequence_used=answer.sequence_used,
        messages=answer.messages,
        service=DgiiService.RFCE_QUERY,
    )
    assert (outcome.code, outcome.state, outcome.final) == (
        RfceQueryCode.NOT_FOUND,
        EcfState.OUTCOME_UNKNOWN,
        False,
    )
    assert len(fake.received) == sent  # asking sends nothing


def signed_anecf(signer: KeyPairSigner) -> bytes:
    voided = (VoidedRange(EcfType.CREDIT_INVOICE, 51, 100),)
    return signer.sign(render_anecf(issuer_rnc=ISSUER, ranges=voided, at=SIGNED_AT))


def signed_acecf(signer: KeyPairSigner) -> bytes:
    approval = CommercialApproval(
        issuer_rnc=ISSUER,
        buyer_rnc="131098193",
        encf=FIRST,
        issued_on=date(2026, 10, 13),
        total=Decimal("4887.58"),
        approved=True,
        reason=None,
        at=SIGNED_AT,
    )
    return signer.sign(render_acecf(approval))


async def test_a_signed_anecf_voids_its_ranges_once(
    dgii: DgiiClient, signer: KeyPairSigner, fake: FakeDgii
) -> None:
    anecf = signed_anecf(signer)
    answer = await dgii.void_ranges(WHO, T, anecf)
    assert isinstance(answer, Answer)
    assert (answer.code, answer.rnc, answer.sequence_used) == (1, ISSUER, None)
    assert fake.state.voids == [anecf]
    unsigned = render_anecf(
        issuer_rnc=ISSUER, ranges=(VoidedRange(EcfType.CREDIT_INVOICE, 1, 1),), at=SIGNED_AT
    )
    refused = await dgii.void_ranges(WHO, T, unsigned)
    assert isinstance(refused, Refused)


async def test_a_buyer_s_signed_acecf_reaches_dgii(
    dgii: DgiiClient, signer: KeyPairSigner, fake: FakeDgii
) -> None:
    acecf = signed_acecf(signer)
    acknowledged = await dgii.commercial_approval(WHO, T, acecf)
    assert acknowledged == Acknowledged("Aprobado", (DgiiMessage("", "Aprobación comercial recibida"),))
    assert fake.state.dgii_approvals == [acecf]
    assert isinstance(await dgii.commercial_approval(WHO, T, b"<ACECF/>"), Refused)


@pytest.mark.parametrize(
    "case",
    [
        MayHaveTaken(500, b"", "void", "server_error"),
        MayHaveTaken(200, b"not json", "void", "unreadable"),
        MayHaveTaken(200, b'{"codigo": "x"}', "void", "unreadable"),
        MayHaveTaken(404, b"", "approval", "undocumented"),
        MayHaveTaken(200, b'{"estado": ' + b'"x"' * 2 + b"}", "approval", "unreadable"),
        MayHaveTaken(200, b'{"mensaje": ["recibido"]}', "approval", "unreadable"),
    ],
    ids=[
        "void-500",
        "void-not-json",
        "void-code-not-a-number",
        "approval-404",
        "approval-not-json",
        "approval-without-state",
    ],
)
async def test_a_void_or_an_approval_dgii_may_have_taken_has_an_unknown_outcome(
    signer: KeyPairSigner, clock: ManualClock, sleeps: list[float], case: MayHaveTaken
) -> None:
    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request.url.path)
        return seeded(request.url.path) or reply(case.status, case.answer)

    async with client_over(httpx.MockTransport(handler), signer, clock, sleeps).opened() as dgii:
        with pytest.raises(OutcomeUnknownError, match=case.reason):
            await call(dgii, case.service)
    assert len(sent) == len(("seed", "validation", "the document, once"))


async def test_void_messages_are_read_as_text_or_as_dgii_s_message_objects(
    signer: KeyPairSigner, clock: ManualClock, sleeps: list[float]
) -> None:
    answer = b'{"rnc": "101850043", "codigo": "2", "mensajes": ["rango usado", {"codigo": 7, "valor": "x"}]}'
    async with client_over(scripted_dgii(GOOD_TOKEN, 200, answer), signer, clock, sleeps).opened() as dgii:
        voided = await dgii.void_ranges(WHO, T, b"<ANECF/>")
    assert voided == Answer(
        code=2,
        messages=(DgiiMessage("", "rango usado"), DgiiMessage("7", "x")),
        sequence_used=None,
        rnc=ISSUER,
    )


@pytest.mark.parametrize("service", ["void", "track_ids", "directory"])
async def test_what_certecf_does_not_publish_is_refused_before_anything_is_sent(
    signer: KeyPairSigner, fake: FakeDgii, clock: ManualClock, sleeps: list[float], service: str
) -> None:
    counting = Counting(httpx.ASGITransport(app=fake.app))
    recording = seed_signer(signer)
    async with client_over(counting, recording, clock, sleeps).opened() as dgii:
        with pytest.raises(ServiceNotInEnvironmentError):
            await call(dgii, service, Environment.CERTECF)
    assert (counting.paths, recording.calls) == ([], [])


async def test_a_call_whose_deadline_s_timer_fires_a_clock_tick_early_is_still_timed_out(
    signer: KeyPairSigner, clock: ManualClock, sleeps: list[float], monkeypatch: pytest.MonkeyPatch
) -> None:
    # asyncio runs a timer due within a clock resolution of now (Windows: about 16 ms): which limit ended an
    # attempt is the one asyncio armed, never what the clock reads once it has. The resolution is widened and
    # the server wakes once inside the window, so the deadline's timer fires before the deadline.
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "_clock_resolution", 0.25)
    cancelled_at: list[float] = []

    async def wakes_then_stalls(request: httpx.Request) -> httpx.Response:
        if (answer := seeded(request.url.path)) is not None:
            return answer
        try:
            await asyncio.sleep(0.2)
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled_at.append(loop.time())
            raise
        return reply(200, b"{}")

    tight = DgiiTimeouts(connect=5, total=5, call=0.3)
    async with client_over(
        httpx.MockTransport(wakes_then_stalls), signer, clock, sleeps, tight
    ).opened() as dgii:
        earliest_deadline = loop.time() + tight.call
        with pytest.raises(AuthorityUnavailableError, match="result: timed_out"):
            await dgii.result(WHO, T, "a")
    assert sleeps == []
    # The timer fired before the deadline: reading the clock (the old rule) would have said "broken".
    [fired] = cancelled_at
    assert fired < earliest_deadline


@final
@dataclass(frozen=True, slots=True)
class Logging:
    """What the platform's logging reads of an app's settings: everything, as text."""

    environment: str = "test"
    log_level: str = "DEBUG"
    log_format: LogFormat = LogFormat.CONSOLE


async def test_no_log_line_and_no_error_names_a_tax_id(
    capsys: pytest.CaptureFixture[str],
    signer: KeyPairSigner,
    fake: FakeDgii,
    clock: ManualClock,
    sleeps: list[float],
) -> None:
    """The platform's logging is configured once ``capsys`` holds stdout: every line written is captured."""
    told: list[str] = []
    # signxml (the fake's second opinion, a test dependency the channel never loads) logs whole documents at
    # DEBUG: the fake checks with the channel's own verifier alone here.
    fake.state.check = VerifierCheck(trusted=None)
    with restored_logging():
        configure_logging(Logging(), service="ledger-worker")
        logging.getLogger("ledger_country_do").debug("logging reaches the capture")
        async with client_over(httpx.ASGITransport(app=fake.app), signer, clock, sleeps).opened() as dgii:
            await dgii.submit_ecf(WHO, T, ISSUER, FIRST, await signed_ecf(signer))
            await dgii.track_ids(WHO, T, ISSUER, FIRST)
            await dgii.rfce_status(WHO, T, ISSUER, Encf(EcfType.CONSUMER_INVOICE, 9), "abc123")
            await dgii.directory(WHO, T, "131098193")
            for service, environment in (
                ("track_ids", Environment.CERTECF),
                ("directory", Environment.CERTECF),
                ("submit", Environment.ECF),
            ):
                with pytest.raises(EcfError) as raised:
                    await call(dgii, service, environment)
                told += [str(raised.value), repr(raised.value)]
        fake.script.outage = -1
        async with client_over(httpx.ASGITransport(app=fake.app), signer, clock, sleeps).opened() as dgii:
            for service in ("track_ids", "submit"):
                with pytest.raises(EcfError) as raised:
                    await call(dgii, service)
                told += [str(raised.value), repr(raised.value)]
    out = capsys.readouterr().out
    assert "logging reaches the capture" in out
    assert [
        tax_id for tax_id in (ISSUER, "131098193") if tax_id in out or any(tax_id in each for each in told)
    ] == []
