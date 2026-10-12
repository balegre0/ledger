"""The tables the chassis owns, which the migrations of every service create: ``outbox`` and ``inbox``.

A service's first migration creates ``CHASSIS_METADATA`` beside the service's own tables. The chassis writes
no table by itself, and a service started without them reports ready while the relay fails at every pass and
no event leaves.

A table is registered when the module of its class is imported, and the classes sit with the code that uses
them: ``OutboxRecord`` in ``outbox``, ``InboxRecord`` in ``consumer``. ``PlatformTable.metadata`` is therefore
empty, or holds only one of the two, in a process that has imported neither (a migration, say). This module
imports both and is the one thing a migration needs to import.
"""

from typing import Final

from ledger_platform.consumer import InboxRecord
from ledger_platform.database import PlatformTable
from ledger_platform.outbox import OutboxRecord

__all__ = ["CHASSIS_METADATA", "InboxRecord", "OutboxRecord"]

CHASSIS_METADATA: Final = PlatformTable.metadata
"""Every table of the chassis, for ``create_all`` and ``drop_all``. Outside row-level security on purpose."""
