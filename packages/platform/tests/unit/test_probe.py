import asyncio
import contextlib
from contextlib import asynccontextmanager
from typing import Final
lazy from collections.abc import AsyncGenerator

import pytest
from pydantic import TypeAdapter

from ledger_platform.observability.probe import healthy, main

ADDRESS: Final = TypeAdapter(tuple[str, int] | tuple[str, int, int, int])


@asynccontextmanager
async def answering(head: bytes, *, delay: float = 0.0) -> AsyncGenerator[str]:
    """A server on 127.0.0.1 that answers every request with ``head`` (after ``delay`` seconds); its URL."""

    handlers: set[asyncio.Task[None]] = set()

    async def answer(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        with contextlib.suppress(ConnectionError, asyncio.IncompleteReadError):
            await reader.readuntil(b"\r\n\r\n")
            await asyncio.sleep(delay)
            writer.write(head)
            await writer.drain()
        writer.close()

    def accept(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        handler = asyncio.create_task(answer(reader, writer))
        handlers.add(handler)
        handler.add_done_callback(handlers.discard)

    server = await asyncio.start_server(accept, "127.0.0.1", 0)
    port = ADDRESS.validate_python(server.sockets[0].getsockname())[1]
    try:
        yield f"http://127.0.0.1:{port}/readyz"
    finally:
        server.close()
        server.close_clients()
        pending = tuple(handlers)
        for handler in pending:
            handler.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        await server.wait_closed()


OK: Final = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok"
UNAVAILABLE: Final = b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"


async def test_a_service_that_answers_200_is_healthy() -> None:
    async with answering(OK) as url:
        assert await asyncio.to_thread(healthy, url)


@pytest.mark.parametrize("head", [UNAVAILABLE, b"NOT HTTP AT ALL\r\n\r\n"], ids=["503", "garbage"])
async def test_a_service_that_answers_anything_else_is_not(head: bytes) -> None:
    async with answering(head) as url:
        assert not await asyncio.to_thread(healthy, url)


async def test_a_service_that_does_not_answer_in_time_is_not_healthy() -> None:
    async with answering(OK, delay=5.0) as url:
        assert not await asyncio.to_thread(healthy, url, timeout=0.2)


def test_a_service_that_is_not_there_is_not_healthy() -> None:
    assert not healthy("http://127.0.0.1:1/readyz", timeout=0.5)
    assert not healthy("http://127.0.0.1:notaport/readyz")


async def test_the_command_line_says_healthy_with_its_exit_code(monkeypatch: pytest.MonkeyPatch) -> None:
    async with answering(OK) as url:
        monkeypatch.setattr("sys.argv", ["ledger-probe", url])
        assert await asyncio.to_thread(main) == 0
    async with answering(UNAVAILABLE) as url:
        monkeypatch.setattr("sys.argv", ["ledger-probe", url])
        assert await asyncio.to_thread(main) == 1


@pytest.mark.parametrize("arguments", [[], ["https://example.com/"], ["http://a/", "http://b/"]])
def test_the_command_line_takes_one_plain_http_url(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], arguments: list[str]
) -> None:
    monkeypatch.setattr("sys.argv", ["ledger-probe", *arguments])
    assert main() == 2
    assert capsys.readouterr().err == "usage: ledger-probe http://HOST:PORT/PATH\n"
