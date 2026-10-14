"""The base of every event (§3.5): a fact a module or a channel announces, as a frozen dataclass.

A subclass sets ``TYPE`` (``<module>.<aggregate>.<past_participle>``, declared by the class itself, never
inherited) and ``VERSION`` (an ``int`` from 1) and adds its payload as fields. A base that only shares fields
between events sets ``ABSTRACT = True`` in its own body; its subclasses are not abstract unless they say so.

The envelope fields are here: ``scope`` (whose data; ``None`` for no tenant's), ``subject`` (the aggregate's
id) and ``aggregate_version`` (its version after the change, so projections apply only newer ones). The
platform's codec turns the payload into JSON and ``uow.publish`` writes it to the producer's outbox.
"""

import re
from dataclasses import dataclass
from typing import ClassVar, Final, override
lazy from uuid import UUID

from ledger_kernel.errors import LedgerError
lazy from ledger_kernel.tenancy import Scope

_TYPE: Final = re.compile(r"[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*", re.ASCII)


class InvalidEventTypeError(LedgerError):
    """Text that is not an event type: a defect of the code or the manifest naming it."""

    code: ClassVar[str] = "invalid_event_type"

    def __init__(self) -> None:
        super().__init__("an event type is <module>.<aggregate>.<past_participle>, lower case")


def parse_event_type(text: str) -> str:
    """Read an event type: three dotted lower-case parts (letters, digits, ``_``), each led by a letter."""
    if _TYPE.fullmatch(text) is None:
        raise InvalidEventTypeError
    return text


class InvalidEventClassError(LedgerError):
    """An event class without its own well-formed ``TYPE`` or without an ``int`` ``VERSION`` from 1.

    A defect of the class, found when it is declared.
    """

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
    ABSTRACT: ClassVar[bool] = False
    """``True`` in the body of a base that only shares fields: its ``TYPE`` and ``VERSION`` go unchecked."""
    scope: Scope | None
    subject: UUID | None
    aggregate_version: int | None

    @override
    def __init_subclass__(cls) -> None:
        super().__init_subclass__()
        own = cls.__dict__
        if "ABSTRACT" in own and cls.ABSTRACT:
            return
        if "TYPE" not in own or not hasattr(cls, "VERSION"):
            raise InvalidEventClassError(cls.__qualname__)
        type_name: object = cls.TYPE
        version: object = cls.VERSION
        well_formed = type(type_name) is str and _TYPE.fullmatch(type_name) is not None
        if not well_formed or type(version) is not int or version < 1:
            raise InvalidEventClassError(cls.__qualname__)
