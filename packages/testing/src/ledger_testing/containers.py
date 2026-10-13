"""Real PostgreSQL and NATS servers for tests, in containers, at the versions the services run on."""

from contextlib import contextmanager
from typing import Final, final
lazy from collections.abc import Generator

import psycopg
from psycopg import sql
from testcontainers.community.postgres import PostgresContainer
from testcontainers.core.container import DockerContainer
from testcontainers.core.wait_strategies import LogMessageWaitStrategy

POSTGRES_IMAGE: Final = "postgres:18.6"
NATS_IMAGE: Final = "nats:2.12.15"
NATS_PORT: Final = 4222
_POSTGRES_PORT: Final = 5432


@final
class Postgres:
    """A PostgreSQL server; every database it makes belongs to a login role of its own that is no superuser.

    A superuser bypasses row-level security, and so does a table's owner unless the table forces it: tests
    connect as the kind of role a service connects as, never as the administrator.
    """

    __slots__ = ("_container",)

    def __init__(self, container: PostgresContainer) -> None:
        self._container = container

    @classmethod
    @contextmanager
    def started(cls) -> Generator[Postgres]:
        """A server for the length of the block."""
        with PostgresContainer(POSTGRES_IMAGE, driver=None) as container:
            yield cls(container)

    def database(self, name: str, *, role: str, password: str) -> str:
        """A new database ``name`` owned by the new role ``role``: its SQLAlchemy URL, psycopg driver."""
        with psycopg.connect(self._container.get_connection_url(), autocommit=True) as admin:
            admin.execute(
                sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS").format(
                    sql.Identifier(role), sql.Literal(password)
                )
            )
            admin.execute(
                sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(name), sql.Identifier(role))
            )
        host = self._container.get_container_host_ip()
        port = self._container.get_exposed_port(_POSTGRES_PORT)
        return f"postgresql+psycopg://{role}:{password}@{host}:{port}/{name}"


def libpq_url(url: str) -> str:
    """The URL psycopg itself takes for ``url``, an SQLAlchemy ``postgresql+psycopg://`` URL."""
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def nats_server(*arguments: str) -> DockerContainer:
    """A NATS server with JetStream, started with ``arguments`` too, ready once it says so."""
    return (
        DockerContainer(NATS_IMAGE)
        .with_command(["-js", *arguments])
        .with_exposed_ports(NATS_PORT)
        .waiting_for(LogMessageWaitStrategy("Server is ready"))
    )


def nats_address(container: DockerContainer) -> str:
    """The URL a client reaches ``container``'s NATS server by."""
    return f"nats://{container.get_container_host_ip()}:{container.get_exposed_port(NATS_PORT)}"
