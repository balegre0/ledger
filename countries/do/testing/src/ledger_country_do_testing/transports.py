"""httpx transports that watch or break the fake's traffic, and the hosts tests give the fake."""

from typing import Final, final, override

import httpx

from ledger_country_do.channels.ecf_v1.adapters.dgii.endpoints import DgiiHosts

FAKE_HOSTS: Final = DgiiHosts(ecf="https://ecf.fake.test", fc="https://fc.fake.test")
"""The hosts the DO pack's tests give DGII's client in front of the fake (``.test`` never resolves)."""
_RESET: Final = "the connection was reset"


@final
class Counting(httpx.AsyncBaseTransport):
    """Passes every request on and records its path."""

    __slots__ = ("_inner", "paths")

    def __init__(self, inner: httpx.AsyncBaseTransport) -> None:
        self._inner = inner
        self.paths: list[str] = []

    @override
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.paths.append(request.url.path)
        return await self._inner.handle_async_request(request)


@final
class BreaksAfterSending(httpx.AsyncBaseTransport):
    """Delivers a request whose path holds ``path_part``, then breaks the connection before the answer."""

    __slots__ = ("_inner", "_path_part")

    def __init__(self, inner: httpx.AsyncBaseTransport, path_part: str) -> None:
        self._inner = inner
        self._path_part = path_part

    @override
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self._inner.handle_async_request(request)
        if self._path_part in request.url.path:
            await response.aread()
            raise httpx.ReadError(_RESET, request=request)
        return response
