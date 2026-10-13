"""Real NATS servers for tests, in containers, at the version the services run on.

PostgreSQL is ``ledger_testing.postgres``'s: one server per run, shared by every xdist worker.
"""

from typing import Final

from testcontainers.core.container import DockerContainer
from testcontainers.core.wait_strategies import LogMessageWaitStrategy

from ledger_testing.images import image

NATS_PORT: Final = 4222


def libpq_url(url: str) -> str:
    """The URL psycopg itself takes for ``url``, an SQLAlchemy ``postgresql+psycopg://`` URL."""
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def nats_server(*arguments: str) -> DockerContainer:
    """A NATS server with JetStream (the pinned image) and ``arguments``, ready once it says so."""
    return (
        DockerContainer(image("nats"))
        .with_command(["-js", *arguments])
        .with_exposed_ports(NATS_PORT)
        .waiting_for(LogMessageWaitStrategy("Server is ready"))
    )


def nats_address(container: DockerContainer) -> str:
    """The URL a client reaches ``container``'s NATS server by."""
    return f"nats://{container.get_container_host_ip()}:{container.get_exposed_port(NATS_PORT)}"
