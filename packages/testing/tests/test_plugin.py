import asyncio
import runpy
from contextlib import contextmanager, nullcontext
from importlib.util import find_spec
from typing import Final, final
lazy from collections.abc import Generator

import pytest

from ledger_testing import plugin
from ledger_testing.postgres import PostgresServer

pytest_plugins = ["pytester"]

SERVER = PostgresServer(host="db.invalid", port=5432, user="u", password="p")
LOADED_BEFORE_COVERAGE: Final = (
    "ledger_testing.plugin",
    "ledger_testing.hooks",
    "ledger_testing.postgres",
    "ledger_testing.images",
)
"""pytest imports its plugins (entry point ``pytest11``) before pytest-cov starts, and with them the modules
their hook specifications name: pluggy reads the hooks' signatures, which evaluates their annotations."""


@pytest.fixture
def pytester(pytester: pytest.Pytester) -> pytest.Pytester:
    """A nested run, configured as the repository is (the outer run's warnings are errors there too)."""
    pytester.makeini("[pytest]\nasyncio_default_fixture_loop_scope = function\n")
    return pytester


@final
class Node:
    """What xdist's controller hands ``pytest_configure_node``: its config and the worker's input."""

    def __init__(self, config: pytest.Config) -> None:
        self.config = config
        self.workerinput: dict[str, object] = {}


def test_the_controller_starts_one_server_hands_it_to_every_worker_and_stops_it(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []

    @contextmanager
    def fake_start() -> Generator[PostgresServer]:
        events.append("started")
        yield SERVER
        events.append("stopped")

    monkeypatch.setattr(plugin, "started_postgres", fake_start)
    controller = pytester.parseconfig()
    first, second = Node(controller), Node(controller)
    plugin.pytest_configure_node(first)
    plugin.pytest_configure_node(second)
    assert events == ["started"]
    assert first.workerinput == {"ledger_postgres": SERVER.to_json()} == second.workerinput
    plugin.pytest_unconfigure(controller)
    assert events == ["started", "stopped"]


def test_a_config_that_started_nothing_stops_nothing(pytester: pytest.Pytester) -> None:
    plugin.pytest_unconfigure(pytester.parseconfig())


def test_a_worker_uses_the_server_it_was_handed(pytester: pytest.Pytester) -> None:
    pytester.makeconftest(
        f"""
def pytest_configure(config):
    config.workerinput = {{"ledger_postgres": {SERVER.to_json()!r}, "workercount": 2}}
"""
    )
    pytester.makepyfile(
        """
def test_server(postgres):
    assert postgres.host == "db.invalid"
"""
    )
    pytester.runpytest("-p", "no:randomly").assert_outcomes(passed=1)


def test_without_xdist_the_session_starts_its_own_server(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(plugin, "started_postgres", lambda: nullcontext(SERVER))
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    pytester.makepyfile(
        """
def test_server(postgres, worker_name):
    assert (postgres.host, worker_name) == ("db.invalid", "main")
"""
    )
    pytester.runpytest("-p", "no:randomly").assert_outcomes(passed=1)


def test_every_hook_prepares_the_template_once_per_server(
    pytester: pytest.Pytester, postgres: PostgresServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(plugin, "started_postgres", lambda: nullcontext(postgres))
    monkeypatch.setattr(plugin, "TEMPLATE", "hooked_template")
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "hooked")
    pytester.syspathinsert()
    pytester.makeconftest(
        """
def pytest_ledger_template(template):
    template.execute(("INSERT INTO hooked VALUES (2)",))
"""
    )
    pytester.makepyfile(
        hooked_plugin="""
import pytest


@pytest.hookimpl(tryfirst=True)
def pytest_ledger_template(template):
    template.execute(("CREATE TABLE hooked (n int)", "INSERT INTO hooked VALUES (1)"))
""",
        test_hooked="""
import psycopg


def test_database(worker_database):
    assert worker_database.name == "ledger_hooked"
    with psycopg.connect(worker_database.admin_url()) as connection:
        assert connection.execute("SELECT sum(n) FROM hooked").fetchone() == (3,)
""",
    )
    pytester.runpytest("-p", "no:randomly", "-p", "hooked_plugin").assert_outcomes(passed=1)
    pytester.runpytest("-p", "no:randomly", "-p", "hooked_plugin").assert_outcomes(passed=1)


def test_every_test_runs_on_a_selector_event_loop(pytester: pytest.Pytester) -> None:
    item = pytester.getitem("def test_func(): pass")
    factories = plugin.pytest_asyncio_loop_factories(item.config, item)
    assert list(factories) == ["selector"]
    loop = factories["selector"]()
    try:
        assert isinstance(loop, asyncio.SelectorEventLoop)
    finally:
        loop.close()


@pytest.mark.parametrize("name", LOADED_BEFORE_COVERAGE)
def test_a_module_pytest_loads_before_coverage_starts_runs_on_its_own(name: str) -> None:
    """Running the module again, in a namespace of its own, is how coverage sees its top level run."""
    spec = find_spec(name)
    assert spec is not None
    assert spec.origin is not None
    again = f"{name}_again"
    assert runpy.run_path(spec.origin, run_name=again)["__name__"] == again
