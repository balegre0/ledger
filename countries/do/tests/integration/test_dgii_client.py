import asyncio
import gzip
from dataclasses import dataclass
from datetime import timedelta
from typing import Final

import httpx
import pytest
from pydantic import TypeAdapter

from ledger_country_do.channels.ecf_v1.adapters.dgii.client import (
    MAX_ANSWER,
    DgiiClient,
    DgiiTimeouts,
    read_answer,
)
from ledger_country_do.channels.ecf_v1.adapters.dgii.endpoints import LiveHostRefusedError, Service
from ledger_country_do.channels.ecf_v1.adapters.dgii.wire import TokenAnswer
from ledger_country_do.channels.ecf_v1.adapters.xml import ecf, rfce
from ledger_country_do.channels.ecf_v1.application.ports import (
    Answer,
    AuthorityUnavailableError,
    OutcomeUnknownError,
    Received,
    Refused,
)
from ledger_country_do.channels.ecf_v1.domain.encf import EcfType, Encf
from ledger_country_do.channels.ecf_v1.domain.environments import Environment
from ledger_country_do.channels.ecf_v1.domain.status_map import DgiiMessage
from ledger_country_do.channels.ecf_v1.domain.totals import compute_totals
from ledger_country_do_testing.fake_dgii.state import Scripted, Status
from ledger_country_do_testing.samples import SIGNED_AT, consumer_invoice, credit_invoice
from ledger_country_do_testing.transports import FAKE_HOSTS, BreaksAfterSending, Counting
lazy from ledger_country_do_testing.clock import ManualClock
lazy from ledger_country_do_testing.fake_dgii.app import FakeDgii
lazy from ledger_country_do_testing.local_signer import KeyPairSigner

T: Final = Environment.TESTECF
ISSUER: Final = "101850043"
FIRST: Final = Encf(EcfType.CREDIT_INVOICE, 1)
SEED_PATH: Final = "/testecf/autenticacion/api/autenticacion/semilla"


async def signed_ecf(signer: KeyPairSigner, sequence: int = 1, *, small_consumer: bool = False) -> bytes:
    draft = consumer_invoice(sequence) if small_consumer else credit_invoice(sequence)
    return await signer.sign_document(ecf.render(draft, compute_totals(draft), signed_at=SIGNED_AT))


def client_over(
    transport: httpx.AsyncBaseTransport,
    signer: KeyPairSigner,
    clock: ManualClock,
    sleeps: list[float],
    timeouts: DgiiTimeouts | None = None,
) -> DgiiClient:
    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    if timeouts is None:
        return DgiiClient(signer, hosts=FAKE_HOSTS, clock=clock, sleep=sleep, transport=transport)
    return DgiiClient(
        signer, hosts=FAKE_HOSTS, timeouts=timeouts, clock=clock, sleep=sleep, transport=transport
    )


async def test_an_e_cf_is_received_then_decided(
    dgii: DgiiClient, signer: KeyPairSigner, fake: FakeDgii
) -> None:
    reception = await dgii.submit_ecf(T, ISSUER, FIRST, await signed_ecf(signer))
    assert isinstance(reception, Received)
    assert fake.received[-1].file_name == "101850043E310000000001.xml"
    assert fake.received[-1].host == "ecf.fake.test"
    assert await dgii.result(T, reception.track_id) == Answer(code=1, messages=(), sequence_used=True)


async def test_dgii_s_messages_and_secuencia_utilizada_arrive_intact(
    dgii: DgiiClient, signer: KeyPairSigner, fake: FakeDgii
) -> None:
    fake.script.default = Scripted(
        status=Status.REJECTED, messages=(("145", "El RNC no es emisor"),), sequence_used=False
    )
    reception = await dgii.submit_ecf(T, ISSUER, FIRST, await signed_ecf(signer))
    assert isinstance(reception, Received)
    answer = await dgii.result(T, reception.track_id)
    assert answer == Answer(
        code=2, messages=(DgiiMessage("145", "El RNC no es emisor"),), sequence_used=False
    )


async def test_a_refusal_at_the_door_is_an_answer_not_a_failure(
    dgii: DgiiClient, signer: KeyPairSigner
) -> None:
    refused = await dgii.submit_ecf(
        T, ISSUER, Encf(EcfType.CONSUMER_INVOICE, 1), await signed_ecf(signer, small_consumer=True)
    )
    assert isinstance(refused, Refused)
    assert refused.messages[0].code == "400"


async def test_the_rfce_is_answered_at_once_on_the_fc_host(
    dgii: DgiiClient, signer: KeyPairSigner, fake: FakeDgii
) -> None:
    draft = consumer_invoice()
    summary = await signer.sign_document(rfce.render(draft, compute_totals(draft), security_code="dcp79q"))
    answer = await dgii.submit_rfce(T, ISSUER, draft.encf, summary)
    assert answer == Answer(code=1, messages=(), sequence_used=True)
    assert fake.received[-1].host == "fc.fake.test"
    assert fake.received[-1].file_name == f"101850043{draft.encf}.xml"
    refused = await dgii.submit_rfce(T, ISSUER, draft.encf, b"not xml")
    assert isinstance(refused, Refused)


async def test_the_rfce_s_messages_keep_their_string_codes_and_dgii_s_valor(
    dgii: DgiiClient, signer: KeyPairSigner, fake: FakeDgii
) -> None:
    fake.script.default = Scripted(
        status=Status.REJECTED, messages=(("RFCE-7", "El monto no cuadra"),), sequence_used=False
    )
    draft = consumer_invoice()
    summary = await signer.sign_document(rfce.render(draft, compute_totals(draft), security_code="dcp79q"))
    answer = await dgii.submit_rfce(T, ISSUER, draft.encf, summary)
    assert answer == Answer(
        code=2, messages=(DgiiMessage("RFCE-7", "El monto no cuadra"),), sequence_used=False
    )


async def test_track_ids_of_an_e_ncf_and_none_where_dgii_has_no_such_service(
    dgii: DgiiClient, signer: KeyPairSigner
) -> None:
    first = await dgii.submit_ecf(T, ISSUER, FIRST, await signed_ecf(signer))
    second = await dgii.submit_ecf(T, ISSUER, FIRST, await signed_ecf(signer))
    assert isinstance(first, Received)
    assert isinstance(second, Received)
    assert await dgii.track_ids(T, ISSUER, FIRST) == (first.track_id, second.track_id)
    assert await dgii.track_ids(Environment.CERTECF, ISSUER, FIRST) is None


async def test_the_token_is_cached_and_renewed_five_minutes_before_it_expires(
    signer: KeyPairSigner, fake: FakeDgii, clock: ManualClock, sleeps: list[float]
) -> None:
    counting = Counting(httpx.ASGITransport(app=fake.app))
    async with client_over(counting, signer, clock, sleeps).opened() as dgii:
        await dgii.result(T, "a")
        await dgii.result(T, "b")
        clock.advance(timedelta(minutes=54))
        await dgii.result(T, "c")
        clock.advance(timedelta(minutes=2))
        await dgii.result(T, "d")
    assert counting.paths.count(SEED_PATH) == 2


async def test_one_token_is_fetched_however_many_calls_wait_for_it(
    signer: KeyPairSigner, fake: FakeDgii, clock: ManualClock, sleeps: list[float]
) -> None:
    counting = Counting(httpx.ASGITransport(app=fake.app))
    async with client_over(counting, signer, clock, sleeps).opened() as dgii:
        answers = await asyncio.gather(*(dgii.result(T, track_id) for track_id in "abcd"))
    assert [answer.code for answer in answers] == [0, 0, 0, 0]
    assert counting.paths.count(SEED_PATH) == 1


async def test_tokens_are_kept_per_environment_and_forgotten_when_closed(
    signer: KeyPairSigner, fake: FakeDgii, clock: ManualClock, sleeps: list[float]
) -> None:
    counting = Counting(httpx.ASGITransport(app=fake.app))
    client = client_over(counting, signer, clock, sleeps)
    async with client.opened() as dgii:
        await dgii.result(T, "a")
        await dgii.result(Environment.CERTECF, "a")
    async with client.opened() as dgii:
        await dgii.result(T, "a")
    seeds = [path for path in counting.paths if path.endswith("/semilla")]
    assert seeds == [SEED_PATH, "/certecf/autenticacion/api/autenticacion/semilla", SEED_PATH]


async def test_a_token_dgii_forgot_is_replaced_once(dgii: DgiiClient, fake: FakeDgii) -> None:
    await dgii.result(T, "a")
    fake.state.dgii.revoke()
    assert (await dgii.result(T, "a")).code == 0


async def test_what_cannot_change_dgii_s_state_is_retried_twice(
    dgii: DgiiClient, fake: FakeDgii, sleeps: list[float]
) -> None:
    await dgii.result(T, "warm the token")
    fake.script.outage = 2
    assert (await dgii.result(T, "a")).code == 0
    assert sleeps == [0.2, 0.5]
    fake.script.outage = 3
    with pytest.raises(AuthorityUnavailableError, match="server_error"):
        await dgii.result(T, "a")


async def test_a_submission_is_never_retried(
    dgii: DgiiClient, signer: KeyPairSigner, fake: FakeDgii, sleeps: list[float]
) -> None:
    await dgii.result(T, "warm the token")
    fake.script.outage = 1
    with pytest.raises(AuthorityUnavailableError):
        await dgii.submit_ecf(T, ISSUER, FIRST, await signed_ecf(signer))
    fake.script.outage = 1
    with pytest.raises(AuthorityUnavailableError):
        await dgii.submit_rfce(T, ISSUER, FIRST, await signed_ecf(signer))
    assert sleeps == []
    assert fake.received == []


async def test_a_connection_broken_after_sending_leaves_the_outcome_unknown(
    signer: KeyPairSigner, fake: FakeDgii, clock: ManualClock, sleeps: list[float]
) -> None:
    broken = BreaksAfterSending(httpx.ASGITransport(app=fake.app), "facturaselectronicas")
    async with client_over(broken, signer, clock, sleeps).opened() as dgii:
        with pytest.raises(OutcomeUnknownError):
            await dgii.submit_ecf(T, ISSUER, FIRST, await signed_ecf(signer))
    assert [each.encf for each in fake.received] == ["E310000000001"]
    assert sleeps == []


async def test_an_rfce_whose_connection_broke_after_sending_is_not_sent_again(
    signer: KeyPairSigner, fake: FakeDgii, clock: ManualClock, sleeps: list[float]
) -> None:
    broken = BreaksAfterSending(httpx.ASGITransport(app=fake.app), "recepcionfc")
    draft = consumer_invoice()
    summary = await signer.sign_document(rfce.render(draft, compute_totals(draft), security_code="dcp79q"))
    async with client_over(broken, signer, clock, sleeps).opened() as dgii:
        with pytest.raises(OutcomeUnknownError, match="outcome is unknown"):
            await dgii.submit_rfce(T, ISSUER, draft.encf, summary)
    assert [each.kind for each in fake.received] == ["rfce"]
    assert sleeps == []


async def test_the_live_environment_is_never_reached_through_a_fake(
    signer: KeyPairSigner, fake: FakeDgii, clock: ManualClock, sleeps: list[float]
) -> None:
    counting = Counting(httpx.ASGITransport(app=fake.app))
    async with client_over(counting, signer, clock, sleeps).opened() as dgii:
        with pytest.raises(LiveHostRefusedError):
            await dgii.result(Environment.ECF, "a")
        with pytest.raises(LiveHostRefusedError):
            await dgii.submit_ecf(Environment.ECF, ISSUER, FIRST, await signed_ecf(signer))
    assert counting.paths == []


SEED: Final = b"<SemillaModel><valor>v</valor><fecha>f</fecha></SemillaModel>"
GOOD_TOKEN: Final = b'{"token": "t", "expira": "2026-10-13T14:30:00Z"}'


def scripted_dgii(token: bytes, status: int, answer: bytes, *, seed: bytes = SEED) -> httpx.MockTransport:
    """A DGII handing out ``seed``, answering ``token`` to its validation, ``answer`` to the rest."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/semilla"):
            return httpx.Response(200, content=seed)
        if request.url.path.endswith("validarsemilla"):
            return httpx.Response(200, content=token)
        return httpx.Response(status, content=answer)

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
        Undocumented(GOOD_TOKEN, 200, b"not json", "submit", "unreadable"),
        Undocumented(GOOD_TOKEN, 200, b"[" + b" " * (1024 * 1024) + b"]", "submit", "unreadable"),
        Undocumented(GOOD_TOKEN, 404, b"", "submit", "undocumented"),
        Undocumented(GOOD_TOKEN, 200, b'{"trackId": null}', "submit", "undocumented"),
        Undocumented(GOOD_TOKEN, 404, b"", "rfce", "undocumented"),
        Undocumented(GOOD_TOKEN, 404, b"", "result", "undocumented"),
        Undocumented(GOOD_TOKEN, 302, b"", "result", "undocumented"),
        Undocumented(GOOD_TOKEN, 404, b"", "track_ids", "undocumented"),
        Undocumented(GOOD_TOKEN, 404, b"", "directory", "undocumented"),
        Undocumented(GOOD_TOKEN, 200, b'[{"trackId": ""}]', "track_ids", "unreadable"),
    ],
    ids=[
        "token-not-json",
        "token-expiry",
        "token-not-a-header-value",
        "not-json",
        "too-large",
        "not-found",
        "no-track-id",
        "rfce-404",
        "result-404",
        "redirect-not-followed",
        "track-ids-404",
        "directory-404",
        "empty-track-id",
    ],
)
async def test_answers_dgii_does_not_document_are_failures(
    signer: KeyPairSigner, clock: ManualClock, sleeps: list[float], case: Undocumented
) -> None:
    transport = scripted_dgii(case.token, case.status, case.answer)
    async with client_over(transport, signer, clock, sleeps).opened() as dgii:
        with pytest.raises(AuthorityUnavailableError, match=case.failure):
            await call(dgii, case.call)


async def call(dgii: DgiiClient, service: str) -> object:
    """The client's call of ``service``, with arguments that do not matter to the answer."""
    match service:
        case "submit":
            return await dgii.submit_ecf(T, ISSUER, FIRST, b"<ECF/>")
        case "rfce":
            return await dgii.submit_rfce(T, ISSUER, FIRST, b"<RFCE/>")
        case "track_ids":
            return await dgii.track_ids(T, ISSUER, FIRST)
        case "directory":
            return await dgii.directory(T, "131098193")
        case _:
            return await dgii.result(T, "a")


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
            await dgii.result(T, "a")


async def test_a_seed_or_its_validation_refused_is_a_failure(
    signer: KeyPairSigner, clock: ManualClock, sleeps: list[float]
) -> None:
    def no_seed(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(404)

    def refuses(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/semilla"):
            return httpx.Response(200, content=SEED)
        return httpx.Response(400, json={"trackId": None, "error": "400", "mensaje": "La firma no es válida"})

    async with client_over(httpx.MockTransport(no_seed), signer, clock, sleeps).opened() as dgii:
        with pytest.raises(AuthorityUnavailableError, match="seed: undocumented"):
            await dgii.result(T, "a")
    async with client_over(httpx.MockTransport(refuses), signer, clock, sleeps).opened() as dgii:
        with pytest.raises(AuthorityUnavailableError, match="validate_seed: refused"):
            await dgii.result(T, "a")


async def test_a_seed_validation_dgii_does_not_document_is_a_failure(
    signer: KeyPairSigner, clock: ManualClock, sleeps: list[float]
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/semilla"):
            return httpx.Response(200, content=SEED)
        return httpx.Response(404)

    async with client_over(httpx.MockTransport(handler), signer, clock, sleeps).opened() as dgii:
        with pytest.raises(AuthorityUnavailableError, match="validate_seed: undocumented"):
            await dgii.result(T, "a")


async def test_a_client_with_every_default_reaches_dgii_s_own_hosts(signer: KeyPairSigner) -> None:
    hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        if request.url.path.endswith("/semilla"):
            return httpx.Response(200, content=SEED)
        if request.url.path.endswith("validarsemilla"):
            return httpx.Response(200, content=GOOD_TOKEN)
        return httpx.Response(200, content=b'{"codigo": 0}')

    async with DgiiClient(signer, transport=httpx.MockTransport(handler)).opened() as dgii:
        assert (await dgii.result(T, "a")).code == 0
    assert set(hosts) == {"ecf.dgii.gov.do"}


async def test_no_error_quotes_a_token(
    signer: KeyPairSigner, clock: ManualClock, sleeps: list[float]
) -> None:
    secret = b"eyJhbGciOiJIUzI1NiJ9.c2VjcmV0.dGhpcy1pcy1zZWNyZXQ"
    token = b'{"token": "' + secret + b'", "expira": "' + b"9" * 65 + b'"}'
    async with client_over(scripted_dgii(token, 200, b"{}"), signer, clock, sleeps).opened() as dgii:
        with pytest.raises(AuthorityUnavailableError) as raised:
            await dgii.result(T, "a")
    told = f"{raised.value} {raised.value!r} {raised.value.__cause__} {raised.value.__cause__!r}"
    assert secret.decode() not in told
    assert "unreadable" in told


async def test_a_compressed_answer_is_bounded_once_expanded(
    signer: KeyPairSigner, clock: ManualClock, sleeps: list[float]
) -> None:
    bomb = gzip.compress(b"[" + b" " * (2 * MAX_ANSWER) + b"]")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/semilla"):
            return httpx.Response(200, content=SEED)
        if request.url.path.endswith("validarsemilla"):
            return httpx.Response(200, content=GOOD_TOKEN)
        return httpx.Response(200, headers={"Content-Encoding": "gzip"}, content=bomb)

    async with client_over(httpx.MockTransport(handler), signer, clock, sleeps).opened() as dgii:
        with pytest.raises(AuthorityUnavailableError, match="unreadable"):
            await dgii.track_ids(T, ISSUER, FIRST)


def test_an_answer_read_whole_is_bounded_too() -> None:
    oversized = httpx.Response(200, content=b'{"token": "t", "expira": "' + b" " * MAX_ANSWER + b'"}')
    with pytest.raises(AuthorityUnavailableError, match="validate_seed: unreadable"):
        read_answer(TypeAdapter(TokenAnswer), oversized, Service.VALIDATE_SEED)


async def test_a_broken_answer_to_a_query_is_retried_then_a_failure(
    signer: KeyPairSigner, fake: FakeDgii, clock: ManualClock, sleeps: list[float]
) -> None:
    broken = BreaksAfterSending(httpx.ASGITransport(app=fake.app), "consultaresultado")
    async with client_over(broken, signer, clock, sleeps).opened() as dgii:
        with pytest.raises(AuthorityUnavailableError, match="broken"):
            await dgii.result(T, "a")
    assert sleeps == [0.2, 0.5]


async def test_every_call_has_a_time_limit(
    signer: KeyPairSigner, clock: ManualClock, sleeps: list[float]
) -> None:
    async def stalls(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/semilla"):
            return httpx.Response(200, content=SEED)
        if request.url.path.endswith("validarsemilla"):
            return httpx.Response(200, content=GOOD_TOKEN)
        await asyncio.sleep(60)
        return httpx.Response(200, content=b"{}")

    quick = DgiiTimeouts(connect=0.05, total=0.05)
    async with client_over(httpx.MockTransport(stalls), signer, clock, sleeps, quick).opened() as dgii:
        with pytest.raises(AuthorityUnavailableError, match="result: broken"):
            await dgii.result(T, "a")
        assert sleeps == [0.2, 0.5]
        with pytest.raises(OutcomeUnknownError):
            await dgii.submit_ecf(T, ISSUER, FIRST, b"<ECF/>")
    assert sleeps == [0.2, 0.5]


async def test_a_token_expiry_without_a_zone_is_read_as_utc(
    signer: KeyPairSigner, clock: ManualClock, sleeps: list[float]
) -> None:
    naive = b'{"token": "t", "expira": "2026-10-13T14:30:00"}'
    async with client_over(
        scripted_dgii(naive, 200, b'{"codigo": 0}'), signer, clock, sleeps
    ).opened() as dgii:
        assert (await dgii.result(T, "a")).code == 0
        assert (await dgii.result(T, "b")).code == 0


async def test_a_buyer_missing_from_the_directory_is_none(
    signer: KeyPairSigner, clock: ManualClock, sleeps: list[float]
) -> None:
    async with client_over(scripted_dgii(GOOD_TOKEN, 200, b"[]"), signer, clock, sleeps).opened() as dgii:
        assert await dgii.directory(T, "131098193") is None


async def test_dgii_unreachable_and_a_client_not_opened_are_failures(
    signer: KeyPairSigner, clock: ManualClock, sleeps: list[float]
) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        reason = "refused"
        raise httpx.ConnectError(reason, request=request)

    async with client_over(httpx.MockTransport(refuse), signer, clock, sleeps).opened() as dgii:
        with pytest.raises(AuthorityUnavailableError, match="unreachable"):
            await dgii.result(T, "a")
    with pytest.raises(AuthorityUnavailableError, match="not_open"):
        await dgii.result(T, "a")


async def test_the_directory_names_testecf_s_simulator(dgii: DgiiClient) -> None:
    entry = await dgii.directory(T, "131098193")
    assert entry is not None
    assert (entry.reception, entry.authentication) == (
        "https://ecf.fake.test/testecf/emisorreceptor",
        "https://ecf.fake.test/testecf/emisorreceptor",
    )
    assert await dgii.directory(Environment.TESTECF, "1") is not None
