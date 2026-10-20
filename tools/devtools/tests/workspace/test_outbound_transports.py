"""No shipped package replaces the network of a channel's outbound clients (§10).

``OutboundHttp(transports=...)`` swaps a client's network for a transport: tests and test tools do it, and a
channel's live-mode refusal relies on it (``OutboundHttp.replaced``). A package that ships in the image never
passes ``transports``, so in production every outbound client calls the network it was made for.
"""

import ast
from pathlib import Path
from typing import Final

from ledger_devtools.workspace import Workspace

ROOT: Final = Path(__file__).resolve().parents[4]
FACTORY: Final = "OutboundHttp"
KEYWORD: Final = "transports"


def _called(node: ast.expr) -> str | None:
    match node:
        case ast.Name(id=name) | ast.Attribute(attr=name):
            return name
        case _:
            return None


def replacing(source: str) -> tuple[int, ...]:
    """The lines of ``source`` that make an ``OutboundHttp`` with ``transports``."""
    return tuple(
        node.lineno
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and _called(node.func) == FACTORY
        and any(keyword.arg == KEYWORD for keyword in node.keywords)
    )


def test_no_shipped_package_replaces_an_outbound_client_s_network() -> None:
    scanned = [
        path
        for package in Workspace.load(ROOT).packages
        if package.is_production
        for path in sorted((ROOT / package.source).rglob("*.py"))
    ]
    assert ROOT / "packages/platform/src/ledger_platform/http/outbound.py" in scanned  # the scan saw sources
    found = [
        f"{path.relative_to(ROOT).as_posix()}:{line}"
        for path in scanned
        for line in replacing(path.read_text(encoding="utf-8"))
    ]
    assert found == []


def test_a_replacement_is_found_however_the_factory_is_named() -> None:
    planted = (
        "from ledger_platform.http import outbound\n"
        "from ledger_platform.http.outbound import OutboundHttp\n"
        "a = OutboundHttp(telemetry=t)\n"
        "b = OutboundHttp(telemetry=t, transports={'dgii': x})\n"
        "c = outbound.OutboundHttp(telemetry=t, transports=found)\n"
        "d = make()(transports=found)\n"
    )
    assert replacing(planted) == (4, 5)
