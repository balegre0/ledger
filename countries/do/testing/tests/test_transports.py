import httpx
import pytest

from ledger_country_do.channels.ecf_v1.adapters.http import BUYERS_CLIENT, DGII_CLIENT
from ledger_country_do_testing.transports import BreaksAfterSending, Counting, outbound_over
from ledger_platform.observability.telemetry import Telemetry


async def test_counting_passes_each_request_on_and_records_its_path() -> None:
    inner = httpx.MockTransport(lambda request: httpx.Response(204, request=request))
    counting = Counting(inner)
    async with httpx.AsyncClient(transport=counting, base_url="https://ecf.fake.test") as client:
        first = await client.get("/testecf/autenticacion")
        second = await client.post("/testecf/recepcion")
    assert (first.status_code, second.status_code) == (204, 204)
    assert counting.paths == ["/testecf/autenticacion", "/testecf/recepcion"]


async def test_breaking_after_sending_delivers_the_request_then_loses_its_answer() -> None:
    delivered: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        delivered.append(request.url.path)
        return httpx.Response(200, content=b"{}", request=request)

    broken = BreaksAfterSending(httpx.MockTransport(answer), "recepcion")
    async with httpx.AsyncClient(transport=broken, base_url="https://ecf.fake.test") as client:
        assert (await client.get("/testecf/autenticacion")).status_code == 200
        with pytest.raises(httpx.ReadError):
            _ = await client.post("/testecf/recepcion")
    assert delivered == ["/testecf/autenticacion", "/testecf/recepcion"]


def test_outbound_clients_go_over_the_transport_given_or_the_network() -> None:
    over = outbound_over(httpx.MockTransport(lambda request: httpx.Response(204, request=request)))
    assert (over.replaced(DGII_CLIENT), over.replaced(BUYERS_CLIENT)) == (True, True)
    network = outbound_over(None, telemetry=Telemetry.unexported("transports-test"))
    assert (network.replaced(DGII_CLIENT), network.replaced(BUYERS_CLIENT)) == (False, False)
