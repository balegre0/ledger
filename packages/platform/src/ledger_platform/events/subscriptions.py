"""Subscriptions in the database (§3.5, ADR 0002): written by ``ledger-migrate`` before a rollout.

Each consuming schema's ``subscriptions`` table lists its consumers' event types; migrate upserts the
declared rows, deactivates the rest (a rollback keeps the newer rows: an older runner simply claims only types
it knows), and bumps ``platform.routing.version`` so every fan-out reloads its routing table at its next
pass. Rows are stamped with the database's time (§3.6).
"""

from typing import Final
lazy from collections.abc import Mapping

from pydantic import TypeAdapter
from sqlalchemy import func, tuple_, update
from sqlalchemy.dialects.postgresql import insert
lazy from sqlalchemy import Connection

from ledger_platform.data.tables import chassis_tables, platform_tables

_VERSION: Final = TypeAdapter(int)


def sync_subscriptions(connection: Connection, schema: str, declared: Mapping[str, frozenset[str]]) -> bool:
    """Make ``schema``'s subscriptions what ``declared`` says; whether anything changed. Run as its owner.

    New rows are written and inactive ones re-activated; an active row is left alone, so the rows returned
    are real changes. Rows no longer declared are deactivated, never deleted.
    """
    table = chassis_tables(schema).subscriptions
    now = func.now()
    pairs = sorted((consumer, event_type) for consumer, types in declared.items() for event_type in types)
    changed = 0
    if pairs:
        fresh = insert(table).values(
            [
                {"consumer": consumer, "event_type": event_type, "active": True, "updated_at": now}
                for consumer, event_type in pairs
            ]
        )
        upserted = fresh.on_conflict_do_update(
            index_elements=(table.c.consumer, table.c.event_type),
            set_={"active": True, "updated_at": now},
            where=table.c.active.is_(False),
        )
        changed += len(connection.execute(upserted.returning(table.c.consumer)).all())
    stale = update(table).where(table.c.active.is_(True)).values(active=False, updated_at=now)
    if pairs:
        stale = stale.where(tuple_(table.c.consumer, table.c.event_type).not_in(pairs))
    changed += len(connection.execute(stale.returning(table.c.consumer)).all())
    return changed > 0


def bump_routing(connection: Connection) -> int:
    """Move ``platform.routing.version`` on; the new version. Run as ``platform_owner``."""
    routing = platform_tables().routing
    bumped = update(routing).values(version=routing.c.version + 1).returning(routing.c.version)
    return _VERSION.validate_python(connection.execute(bumped).scalar_one())
