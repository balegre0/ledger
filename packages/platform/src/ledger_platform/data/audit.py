"""Who acts: the actor and call id every audit row records (§6.4; Decision 10).

The runtime binds them where it makes a ``Call`` (from a request) or a ``SystemCall`` (a consumer, a job, the
receiver); ``uow.audit`` reads them. An audit without an actor is a defect: the row would not say who.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import ClassVar, Final, final
lazy from collections.abc import Generator

from ledger_kernel.errors import LedgerError


@final
@dataclass(frozen=True, slots=True)
class Actor:
    """Who acts and in which call.

    ``name``: a principal's subject, an API client's id, or ``system:<reason>``; ``call_id``: the call's id.
    """

    name: str
    call_id: str


class MissingActorError(LedgerError):
    """``uow.audit`` was called where no call is bound: a defect of the code path."""

    code: ClassVar[str] = "missing_actor"

    def __init__(self) -> None:
        super().__init__("an audit event needs the acting call: bind it with acting_as")


_ACTOR: Final[ContextVar[Actor | None]] = ContextVar("ledger_actor", default=None)


@contextmanager
def acting_as(actor: Actor) -> Generator[None]:
    """``actor`` is who acts for the length of the block (the outer one is restored after it)."""
    token = _ACTOR.set(actor)
    try:
        yield
    finally:
        _ACTOR.reset(token)


def current_actor() -> Actor:
    """The acting call; ``MissingActorError`` when none is bound."""
    actor = _ACTOR.get()
    if actor is None:
        raise MissingActorError
    return actor
