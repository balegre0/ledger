"""Subscriptions written by migrate (§3.5, ADR 0002): upserted, deactivated, and the routing version moved."""

from datetime import datetime
from typing import Final

from pydantic import TypeAdapter
from sqlalchemy import text
lazy import psycopg
lazy from sqlalchemy import Engine

from ledger_platform.events.subscriptions import bump_routing, sync_subscriptions

RENAMES: Final = {"other.renames": frozenset({"probe.thing.renamed"})}
_MOMENT: Final = TypeAdapter(datetime)
_ROWS: Final = TypeAdapter(list[tuple[str, str, bool, datetime]])


async def test_subscriptions_are_upserted_and_undeclared_ones_deactivated(
    migrator: Engine, superuser: psycopg.AsyncConnection
) -> None:
    del superuser  # the cleanup of the routing version
    with migrator.begin() as connection:
        connection.execute(text("SET LOCAL ROLE other_owner"))
        assert sync_subscriptions(connection, "other", RENAMES)
        assert not sync_subscriptions(connection, "other", RENAMES)
        assert sync_subscriptions(
            connection, "other", {"other.renames": frozenset({"probe.thing.renamed", "probe.thing.created"})}
        )
        assert sync_subscriptions(connection, "other", RENAMES)  # created deactivated
        assert sync_subscriptions(connection, "other", {})
        assert not sync_subscriptions(connection, "other", {})
        assert sync_subscriptions(connection, "other", RENAMES)  # reactivated
        stamped = _MOMENT.validate_python(connection.execute(text("SELECT now()")).scalar_one())
        rows = _ROWS.validate_python(
            [
                tuple(row)
                for row in connection.execute(
                    text(
                        "SELECT consumer, event_type, active, updated_at FROM other.subscriptions ORDER BY 2"
                    )
                ).all()
            ]
        )
    assert rows == [
        ("other.renames", "probe.thing.created", False, stamped),
        ("other.renames", "probe.thing.renamed", True, stamped),
    ]


async def test_bumping_the_routing_version_counts_up(
    migrator: Engine, superuser: psycopg.AsyncConnection
) -> None:
    del superuser  # puts the version back to 0 after the test
    with migrator.begin() as connection:
        connection.execute(text("SET LOCAL ROLE platform_owner"))
        assert (bump_routing(connection), bump_routing(connection)) == (1, 2)
