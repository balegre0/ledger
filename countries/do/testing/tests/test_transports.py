import httpx

from ledger_country_do_testing.transports import Counting


async def test_counting_passes_each_request_on_and_records_its_path() -> None:
    inner = httpx.MockTransport(lambda request: httpx.Response(204, request=request))
    counting = Counting(inner)
    async with httpx.AsyncClient(transport=counting, base_url="https://ecf.fake.test") as client:
        first = await client.get("/testecf/autenticacion")
        second = await client.post("/testecf/recepcion")
    assert (first.status_code, second.status_code) == (204, 204)
    assert counting.paths == ["/testecf/autenticacion", "/testecf/recepcion"]
