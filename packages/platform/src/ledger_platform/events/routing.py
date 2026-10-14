"""The routing table the fan-out uses: event type → the consumers (and their schemas) subscribed to it."""

from dataclasses import dataclass
from typing import Final, final
lazy from collections.abc import Mapping, Sequence

from pydantic import TypeAdapter
from sqlalchemy import select
lazy from sqlalchemy.ext.asyncio import AsyncConnection

from ledger_platform.data.tables import chassis_tables, platform_tables

_VERSION: Final = TypeAdapter(int)
_PAIRS: Final = TypeAdapter(list[tuple[str, str]])


@final
@dataclass(frozen=True, slots=True)
class Route:
    """A consumer, and the schema whose inbox it reads."""

    schema: str
    consumer: str


@final
@dataclass(frozen=True, slots=True)
class Routing:
    """The routing table at one version."""

    version: int
    routes: Mapping[str, tuple[Route, ...]]


async def routing_version(connection: AsyncConnection) -> int:
    """The current version (one indexed read; the fan-out asks once per pass)."""
    found = (await connection.execute(select(platform_tables().routing.c.version))).scalar_one()
    return _VERSION.validate_python(found)


async def load_routing(connection: AsyncConnection, schemas: Sequence[str]) -> Routing:
    """Every active subscription of ``schemas``, with the version read before them.

    The version is read first: a subscription committed in between is in the table, and its version change
    makes the next pass load again; a version read after could stamp an older table with a newer version.
    """
    version = await routing_version(connection)
    routes: dict[str, list[Route]] = {}
    for schema in schemas:
        table = chassis_tables(schema).subscriptions
        active = (
            select(table.c.consumer, table.c.event_type)
            .where(table.c.active.is_(True))
            .order_by(table.c.consumer)
        )
        rows = _PAIRS.validate_python([tuple(row) for row in (await connection.execute(active)).all()])
        for consumer, event_type in rows:
            routes.setdefault(event_type, []).append(Route(schema, consumer))
    return Routing(version, {event_type: tuple(found) for event_type, found in routes.items()})
