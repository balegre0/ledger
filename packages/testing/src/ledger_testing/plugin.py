"""The ledger's pytest plugin (entry point ``pytest11``): what every package's tests share (§14.2).

- ``postgres``: one PostgreSQL server per run. Under xdist the controller starts it when the first worker is
  configured and hands its address to every worker (``workerinput``); without xdist the session starts one.
- ``worker_database``: this worker's own database, a clone of a template that the packages'
  ``pytest_ledger_template`` hooks prepare once per server.
- ``issuer_keys`` (session) and ``local_issuer`` (per test): an OpenID provider whose RSA key is made once.
- ``clock``: a wall clock the test moves. ``restore_logging`` (autouse): the plain logging baseline.
- A selector event loop: psycopg's async driver cannot run on Windows' proactor loop.

pytest imports this module, and what its hook specification names, before coverage starts: the names it uses
only when a fixture runs are imported lazily, so that coverage sees them load.
"""

import asyncio
import os
from abc import ABCMeta, abstractmethod
from contextlib import ExitStack, nullcontext
from typing import Final, Protocol
lazy from collections.abc import AsyncGenerator, Callable, Iterator, Mapping, MutableMapping
lazy from contextlib import AbstractContextManager

import pytest
from pydantic import TypeAdapter

from ledger_testing.hooks import LedgerHooks
lazy from ledger_testing.clock import ManualClock
lazy from ledger_testing.issuer import IssuerKeys, LocalIssuer
lazy from ledger_testing.logs import restored_logging
lazy from ledger_testing.postgres import (
    TEMPLATE,
    PostgresServer,
    Template,
    WorkerDatabase,
    clone,
    ensure_template,
    started_postgres,
)

_SERVER_INPUT: Final = "ledger_postgres"
_WORKER_INPUT: Final = TypeAdapter(dict[str, object])
_ADDRESS: Final = pytest.StashKey[str]()
"""The controller's server, as its workers receive it (``PostgresServer.to_json``)."""
_STACK: Final = pytest.StashKey[ExitStack]()


class Node(Protocol, metaclass=ABCMeta):
    """What ``pytest_configure_node`` uses of xdist's ``WorkerController``."""

    @property
    @abstractmethod
    def config(self) -> pytest.Config:
        """The controller's configuration."""

    @property
    @abstractmethod
    def workerinput(self) -> MutableMapping[str, object]:
        """What the worker receives at its start."""


def pytest_addhooks(pluginmanager: pytest.PytestPluginManager) -> None:
    """Declare ``pytest_ledger_template``."""
    pluginmanager.add_hookspecs(LedgerHooks)


def pytest_asyncio_loop_factories(
    config: pytest.Config, item: pytest.Item
) -> Mapping[str, Callable[[], asyncio.AbstractEventLoop]]:
    """A selector event loop everywhere: Windows' default proactor loop cannot run psycopg's async driver."""
    del config, item
    return {"selector": asyncio.SelectorEventLoop}


def _controller_address(config: pytest.Config) -> str:
    if _ADDRESS not in config.stash:
        stack = ExitStack()
        config.stash[_STACK] = stack
        config.stash[_ADDRESS] = stack.enter_context(started_postgres()).to_json()
    return config.stash[_ADDRESS]


@pytest.hookimpl(optionalhook=True)
def pytest_configure_node(node: Node) -> None:
    """For each worker, on xdist's controller: the one server, started for the first worker."""
    node.workerinput[_SERVER_INPUT] = _controller_address(node.config)


def pytest_unconfigure(config: pytest.Config) -> None:
    """The controller stops the server it started."""
    stack = config.stash.get(_STACK, None)
    if stack is not None:
        stack.close()


@pytest.fixture(scope="session")
def postgres(request: pytest.FixtureRequest) -> Iterator[PostgresServer]:
    """The run's PostgreSQL server (see the module)."""
    given = _WORKER_INPUT.validate_python(getattr(request.config, "workerinput", {})).get(_SERVER_INPUT)
    opened: AbstractContextManager[PostgresServer] = (
        nullcontext(PostgresServer.from_json(given)) if isinstance(given, str) else started_postgres()
    )
    with opened as server:
        yield server


@pytest.fixture(scope="session")
def worker_name() -> str:
    """``main`` without xdist, else the worker's id (``gw0``, ``gw1``, ...)."""
    return os.environ.get("PYTEST_XDIST_WORKER", "main")


@pytest.fixture(scope="session")
def worker_database(
    postgres: PostgresServer, worker_name: str, request: pytest.FixtureRequest
) -> WorkerDatabase:
    """This worker's own database, cloned from the template the packages' hooks prepared."""

    def prepare(template: Template) -> None:
        request.config.hook.pytest_ledger_template(template=template)

    ensure_template(postgres, prepare, name=TEMPLATE)
    name = f"ledger_{worker_name}"
    clone(postgres, name, template=TEMPLATE)
    return WorkerDatabase(postgres, name)


@pytest.fixture(autouse=True)
def restore_logging() -> Iterator[None]:
    """Each test starts from, and leaves, the plain logging baseline (logging is process-wide state)."""
    with restored_logging():
        yield


@pytest.fixture(scope="session")
def issuer_keys() -> IssuerKeys:
    """The local OpenID provider's first RSA key, made once per session (§14.2)."""
    return IssuerKeys.generate()


@pytest.fixture
async def local_issuer(issuer_keys: IssuerKeys) -> AsyncGenerator[LocalIssuer]:
    """An OpenID provider on 127.0.0.1 signing with the session's key."""
    async with LocalIssuer.started(keys=issuer_keys) as started:
        yield started


@pytest.fixture
def clock() -> ManualClock:
    """A wall clock the test moves (``Clock``)."""
    return ManualClock()
