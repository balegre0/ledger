"""The tables the chassis owns, which every service's migrations create, and their row-level security.

The tables are ``outbox``, ``inbox`` and ``idempotency_keys``. A service's first migration creates
``CHASSIS_METADATA`` beside the service's own tables and runs ``CHASSIS_POLICIES``. The chassis writes no
table by itself, and a service started without them is not ready (its ``schema`` check fails): the relay
would fail at every pass and no event would leave.

A table is registered when the module of its class is imported, and the classes sit with the code that uses
them: ``OutboxRecord`` in ``outbox``, ``InboxRecord`` in ``consumer``, ``IdempotencyRecord`` in
``idempotency``. ``PlatformTable.metadata`` therefore lacks some of them in a process that has not imported
all three (a migration, say). This module imports them and is the one thing a migration needs to import.
"""

from typing import Final

from ledger_platform.consumer import InboxRecord
from ledger_platform.database import PlatformTable, organization_isolation
from ledger_platform.idempotency import IdempotencyRecord
from ledger_platform.outbox import OutboxRecord

__all__ = ["CHASSIS_METADATA", "CHASSIS_POLICIES", "IdempotencyRecord", "InboxRecord", "OutboxRecord"]

CHASSIS_METADATA: Final = PlatformTable.metadata
"""Every table of the chassis, for ``create_all`` and ``drop_all``."""
CHASSIS_POLICIES: Final = organization_isolation(CHASSIS_METADATA.tables["idempotency_keys"])
"""The row-level security of the chassis's tables, for a migration to run once it has created them.

``idempotency_keys`` holds what an organization's callers sent and were answered, so it is under
``organization_isolation``. ``outbox`` and ``inbox`` stay outside on purpose: the relay reads the outbox
across tenants, and the inbox is claimed before a handler runs in its event's tenant."""
