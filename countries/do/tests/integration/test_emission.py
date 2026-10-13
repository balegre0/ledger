from typing import Final, final, override

import httpx
import pytest

from ledger_country_do.channels.ecf_v1.adapters.dgii.client import DgiiClient
from ledger_country_do.channels.ecf_v1.adapters.signing.verifier import verify_enveloped
from ledger_country_do.channels.ecf_v1.adapters.xml.documents import DgiiXml
from ledger_country_do.channels.ecf_v1.adapters.xml.parsing import texts
from ledger_country_do.channels.ecf_v1.application.emission import Emitter, Unknown
from ledger_country_do.channels.ecf_v1.application.ports import Failure
from ledger_country_do.channels.ecf_v1.domain.environments import Environment
from ledger_country_do.channels.ecf_v1.domain.printed import QrHosts
from ledger_country_do.channels.ecf_v1.domain.routing import Route
from ledger_country_do.channels.ecf_v1.domain.status_map import (
    DgiiMessage,
    EcfState,
    FiscalStatus,
    NumberFate,
    Severity,
)
from ledger_country_do_testing.fake_dgii.state import Scripted, Status
from ledger_country_do_testing.samples import consumer_invoice, credit_invoice
from ledger_country_do_testing.transports import FAKE_HOSTS, BreaksAfterSending, Counting
lazy from ledger_country_do_testing.clock import ManualClock
lazy from ledger_country_do_testing.fake_dgii.app import FakeDgii
lazy from ledger_country_do_testing.local_signer import KeyPairSigner

QR_HOSTS: Final = QrHosts(ecf=FAKE_HOSTS.ecf, fc=FAKE_HOSTS.fc)
RECEPTION: Final = "facturaselectronicas"
_LOST: Final = "the connection was lost on the way to DGII"


@pytest.fixture
def emitter(dgii: DgiiClient, signer: KeyPairSigner, clock: ManualClock, sleeps: list[float]) -> Emitter:
    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    return Emitter(
        xml=DgiiXml(),
        signer=signer,
        authority=dgii,
        environment=Environment.TESTECF,
        clock=clock,
        sleep=sleep,
        qr_hosts=QR_HOSTS,
    )


async def no_sleep(seconds: float) -> None:
    del seconds


@final
class LostOnTheWay(httpx.AsyncBaseTransport):
    """Breaks the first request whose path holds ``path_part`` before it reaches DGII; passes the rest."""

    __slots__ = ("_inner", "_lost", "_path_part")

    def __init__(self, inner: httpx.AsyncBaseTransport, path_part: str) -> None:
        self._inner = inner
        self._path_part = path_part
        self._lost = False

    @override
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if self._path_part in request.url.path and not self._lost:
            self._lost = True
            raise httpx.WriteError(_LOST, request=request)
        return await self._inner.handle_async_request(request)


async def test_a_credit_invoice_is_signed_sent_polled_and_accepted(
    emitter: Emitter, fake: FakeDgii, sleeps: list[float]
) -> None:
    emitted = await emitter.emit(credit_invoice())
    assert emitted.route is Route.RECEPTION
    assert emitted.outcome is not None
    assert (emitted.outcome.status, emitted.outcome.number, emitted.pending) == (
        FiscalStatus.ACCEPTED,
        NumberFate.USED,
        False,
    )
    assert fake.received[-1].document == emitted.signed_ecf
    assert verify_enveloped(emitted.signed_ecf).signature_value.startswith(emitted.security_code)
    assert emitted.qr.startswith(
        "https://ecf.fake.test/testecf/consultatimbre?RncEmisor=101850043&RncComprador=131098193"
    )
    assert sleeps == [1.0]


async def test_aceptado_condicional_after_en_proceso_is_valid_with_its_messages(
    emitter: Emitter, fake: FakeDgii, sleeps: list[float]
) -> None:
    fake.script.default = Scripted(
        status=Status.ACCEPTED_CONDITIONALLY, messages=(("613", "Fuera de tolerancia"),), processing=2
    )
    emitted = await emitter.emit(credit_invoice())
    assert emitted.outcome is not None
    assert (emitted.outcome.state, emitted.outcome.status, emitted.outcome.severity) == (
        EcfState.ACCEPTED_CONDITIONALLY,
        FiscalStatus.ACCEPTED_WITH_OBSERVATIONS,
        Severity.WARNING,
    )
    assert emitted.outcome.messages == (DgiiMessage("613", "Fuera de tolerancia"),)
    assert emitted.outcome.number is NumberFate.USED
    assert sleeps == [1.0, 2.0, 5.0]


async def test_a_rejection_dgii_lets_reuse_releases_the_number(emitter: Emitter, fake: FakeDgii) -> None:
    fake.script.default = Scripted(status=Status.REJECTED, sequence_used=False)
    emitted = await emitter.emit(credit_invoice())
    assert emitted.outcome is not None
    assert (emitted.outcome.status, emitted.outcome.number) == (FiscalStatus.REJECTED, NumberFate.RELEASED)


async def test_a_rejection_that_used_the_sequence_burns_the_number(emitter: Emitter, fake: FakeDgii) -> None:
    fake.script.default = Scripted(status=Status.REJECTED, sequence_used=True)
    emitted = await emitter.emit(credit_invoice())
    assert emitted.outcome is not None
    assert (emitted.outcome.status, emitted.outcome.number) == (FiscalStatus.REJECTED, NumberFate.BURNED)


async def test_a_small_consumer_invoice_goes_as_an_rfce_to_fc_with_the_e_cf_s_security_code(
    emitter: Emitter, fake: FakeDgii, sleeps: list[float]
) -> None:
    emitted = await emitter.emit(consumer_invoice())
    assert (emitted.route, emitted.track_id, sleeps) == (Route.RFCE, None, [])
    assert emitted.signed_rfce is not None
    assert texts(emitted.signed_rfce, "Encabezado/CodigoSeguridadeCF") == (emitted.security_code,)
    assert verify_enveloped(emitted.signed_ecf).signature_value[:6] == emitted.security_code
    assert fake.received[-1].host == "fc.fake.test"
    assert emitted.outcome is not None
    assert emitted.outcome.status is FiscalStatus.ACCEPTED
    assert emitted.qr == (
        f"https://fc.fake.test/testecf/consultatimbrefc?RncEmisor=101850043&ENCF=E320000000001&MontoTotal=1180.00"
        f"&CodigoSeguridad={emitted.security_code.replace('+', '%2B').replace('/', '%2F')}"
    )


async def test_a_large_consumer_invoice_goes_in_full(emitter: Emitter, fake: FakeDgii) -> None:
    emitted = await emitter.emit(consumer_invoice(2, large=True))
    assert (emitted.route, emitted.signed_rfce) == (Route.RECEPTION, None)
    assert fake.received[-1].host == "ecf.fake.test"


async def test_an_rfce_code_dgii_does_not_document_is_not_guessed_and_the_rfce_is_parked(
    emitter: Emitter, fake: FakeDgii
) -> None:
    fake.script.default = Scripted(status=Status.IN_PROCESS)
    emitted = await emitter.emit(consumer_invoice())
    assert (emitted.unknown, emitted.outcome, emitted.pending) == (
        Unknown("do_ecf_dgii_code_unknown", None),
        None,
        True,
    )
    assert fake.received[-1].document == emitted.signed_rfce


async def test_a_broken_connection_after_sending_is_resolved_by_track_ids_not_a_second_send(
    signer: KeyPairSigner, fake: FakeDgii, clock: ManualClock
) -> None:
    broken = BreaksAfterSending(httpx.ASGITransport(app=fake.app), RECEPTION)
    async with DgiiClient(
        signer, hosts=FAKE_HOSTS, clock=clock, sleep=no_sleep, transport=broken
    ).opened() as dgii:
        emitter = Emitter(
            xml=DgiiXml(),
            signer=signer,
            authority=dgii,
            environment=Environment.TESTECF,
            clock=clock,
            sleep=no_sleep,
        )
        emitted = await emitter.emit(credit_invoice())
    assert len(fake.received) == 1
    assert emitted.track_id == fake.received[0].track_id
    assert emitted.outcome is not None
    assert emitted.outcome.status is FiscalStatus.ACCEPTED


async def test_a_document_lost_before_dgii_recorded_it_is_sent_again_once_dgii_names_no_track_id(
    signer: KeyPairSigner, fake: FakeDgii, clock: ManualClock
) -> None:
    counted = Counting(LostOnTheWay(httpx.ASGITransport(app=fake.app), RECEPTION))
    async with DgiiClient(
        signer, hosts=FAKE_HOSTS, clock=clock, sleep=no_sleep, transport=counted
    ).opened() as dgii:
        emitter = Emitter(
            xml=DgiiXml(),
            signer=signer,
            authority=dgii,
            environment=Environment.TESTECF,
            clock=clock,
            sleep=no_sleep,
        )
        emitted = await emitter.emit(credit_invoice())
    tail = [path.rsplit("/", 1)[-1] for path in counted.paths if "semilla" not in path]
    assert tail == [RECEPTION, "consulta", RECEPTION, "estado"]
    assert len(fake.received) == 1
    assert emitted.track_id == fake.received[0].track_id
    assert emitted.outcome is not None
    assert emitted.outcome.status is FiscalStatus.ACCEPTED


async def test_an_rfce_whose_connection_broke_after_sending_is_parked_not_resent(
    signer: KeyPairSigner, fake: FakeDgii, clock: ManualClock
) -> None:
    broken = Counting(BreaksAfterSending(httpx.ASGITransport(app=fake.app), "recepcionfc"))
    async with DgiiClient(
        signer, hosts=FAKE_HOSTS, clock=clock, sleep=no_sleep, transport=broken
    ).opened() as dgii:
        emitter = Emitter(
            xml=DgiiXml(),
            signer=signer,
            authority=dgii,
            environment=Environment.TESTECF,
            clock=clock,
            sleep=no_sleep,
        )
        emitted = await emitter.emit(consumer_invoice())
    assert [path for path in broken.paths if "recepcionfc" in path] == [
        "/testecf/recepcionfc/api/recepcion/ecf"
    ]
    assert len(fake.received) == 1
    assert (emitted.route, emitted.unknown, emitted.outcome, emitted.pending) == (
        Route.RFCE,
        Unknown("do_ecf_dgii_outcome_unknown", Failure.BROKEN),
        None,
        True,
    )
    assert fake.received[0].document == emitted.signed_rfce


async def test_an_e_cf_whose_outcome_certecf_cannot_tell_is_parked_with_the_signed_document(
    signer: KeyPairSigner, fake: FakeDgii, clock: ManualClock
) -> None:
    broken = Counting(BreaksAfterSending(httpx.ASGITransport(app=fake.app), RECEPTION))
    async with DgiiClient(
        signer, hosts=FAKE_HOSTS, clock=clock, sleep=no_sleep, transport=broken
    ).opened() as dgii:
        emitter = Emitter(
            xml=DgiiXml(),
            signer=signer,
            authority=dgii,
            environment=Environment.CERTECF,
            clock=clock,
            sleep=no_sleep,
        )
        emitted = await emitter.emit(credit_invoice())
    assert [path for path in broken.paths if RECEPTION in path] == [
        "/certecf/recepcion/api/facturaselectronicas"
    ]
    assert not any("trackids" in path for path in broken.paths)
    assert (emitted.unknown, emitted.track_id, emitted.pending) == (
        Unknown("do_ecf_dgii_outcome_unknown", Failure.BROKEN),
        None,
        True,
    )
    assert fake.received[0].document == emitted.signed_ecf
    assert emitted.qr.startswith("https://ecf.dgii.gov.do/certecf/consultatimbre?")
