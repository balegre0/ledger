"""The local stack in compose (spec §13; plan Decision 28): every service healthy, the fake DGII among them.

Only when ``LEDGER_E2E_COMPOSE`` is ``1`` (the stack built here, ``up --build``) or ``prebuilt`` (from the
images already loaded, ``up --no-build``: CI's step after it builds them; ``.github/workflows/ci.yml``); the
gate skips it (it builds two images and starts seven containers). The stack's own files are written first,
as a developer writes them once (``deploy/local/signer/tokens.sh``, ``deploy/local/do/roots.sh``; each keeps
what exists). The stack runs as a compose project of its own (``ledger-e2e``), on the stack's published ports
(8000 is never one), and is always taken down with its volumes, whatever happened. Run it alone:
``LEDGER_E2E_COMPOSE=1 uv run pytest tests/e2e -k compose -c pyproject.toml`` (the variable is no setting of
the apps', which the other tests of this suite start).
"""

import os
import shutil
import subprocess
from http import HTTPStatus
from pathlib import Path
from typing import Final

import httpx
import pytest

ROOT: Final = Path(__file__).parents[2]
COMPOSE: Final = ROOT / "deploy" / "local" / "compose.yaml"
PROJECT: Final = "ledger-e2e"
PROBES: Final = {"api": 8100, "worker": 8101, "signer": 8102, "receiver": 8103}
"""Each app's published port on the host (compose's defaults)."""
FAKE_DGII: Final = "http://127.0.0.1:8471"
UNKNOWN_RECEIVER: Final = "http://127.0.0.1:8103/do-ecf-v1/testecf/999999999/fe/autenticacion/api/semilla"
"""A seed of a receiver of an RNC no company has: a bare 404."""
UP_TIMEOUT: Final = 1200
BUILDS: Final = {"1": "--build", "prebuilt": "--no-build"}
"""``LEDGER_E2E_COMPOSE``'s values, and how each brings the stack up."""

pytestmark = pytest.mark.skipif(
    os.environ.get("LEDGER_E2E_COMPOSE") not in BUILDS,
    reason="the compose stack runs with LEDGER_E2E_COMPOSE=1 or prebuilt only",
)


def _program(name: str) -> str:
    found = shutil.which(name)
    assert found is not None, f"{name} is needed"
    return found


def test_compose_stack() -> None:
    sh, docker = _program("sh"), _program("docker")
    for script in ("signer/tokens.sh", "do/roots.sh"):
        subprocess.run([sh, str(ROOT / "deploy" / "local" / script)], check=True)
    compose = [docker, "compose", "-p", PROJECT, "-f", str(COMPOSE)]
    try:
        building = BUILDS[os.environ["LEDGER_E2E_COMPOSE"]]
        subprocess.run([*compose, "up", "-d", building, "--wait"], check=True, timeout=UP_TIMEOUT)
        for app, port in PROBES.items():
            ready = httpx.get(f"http://127.0.0.1:{port}/readyz", timeout=10)
            assert ready.status_code == HTTPStatus.OK, (app, ready.text)
        assert httpx.get(UNKNOWN_RECEIVER, timeout=10).status_code == HTTPStatus.NOT_FOUND
        seed = httpx.get(f"{FAKE_DGII}/testecf/autenticacion/api/autenticacion/semilla", timeout=10)
        assert seed.status_code == HTTPStatus.OK
        assert b"<SemillaModel" in seed.content
    finally:
        subprocess.run([*compose, "down", "-v"], check=True, timeout=UP_TIMEOUT)
