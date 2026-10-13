"""The base of every event (§3.5): a fact a module or a channel announces, as a frozen dataclass.

A subclass sets ``TYPE`` (``<module>.<aggregate>.<past_participle>``) and ``VERSION`` and adds its payload as
fields. The envelope fields are here: ``scope`` (whose data; ``None`` for no tenant's), ``subject`` (the
aggregate's id) and ``aggregate_version`` (its version after the change, so projections apply only newer
ones). The platform's codec turns the payload into JSON and ``uow.publish`` writes it to the producer's
outbox.
"""

import re
from dataclasses import dataclass
from typing import ClassVar, Final, override
lazy from uuid import UUID

from ledger_kernel.errors import LedgerError
lazy from ledger_kernel.tenancy import Scope

_TYPE: Final = re.compile(r"[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*", re.ASCII)


class InvalidEventClassError(LedgerError):
    """An event class without a well-formed ``TYPE`` or with a ``VERSION`` below 1: a defect, found early."""

    code: ClassVar[str] = "invalid_event_class"

    def __init__(self, name: str) -> None:
        super().__init__(
            f"event class {name} needs TYPE <module>.<aggregate>.<past_participle> and VERSION >= 1"
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class Event:
    """A fact, as the producer states it (see the module)."""

    TYPE: ClassVar[str]
    VERSION: ClassVar[int]
    scope: Scope | None
    subject: UUID | None
    aggregate_version: int | None

    @override
    def __init_subclass__(cls) -> None:
        super().__init_subclass__()
        try:
            type_name, version = cls.TYPE, cls.VERSION
        except AttributeError as error:
            raise InvalidEventClassError(cls.__qualname__) from error
        if _TYPE.fullmatch(type_name) is None or version < 1:
            raise InvalidEventClassError(cls.__qualname__)
