"""Calls (§3.4): who asks, for whom, under which request; and the actor every audit row then records."""

from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import assert_type, final
lazy from collections.abc import Generator

from ledger_kernel.ids import new_id
from ledger_platform.data.audit import Actor, acting_as
lazy from ledger_kernel.access import Principal
lazy from ledger_kernel.tenancy import Scope


def _new_call_id() -> str:
    return str(new_id())


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Call:
    """A request made by a principal: built at the HTTP edge only."""

    principal: Principal
    scope: Scope
    request_id: str
    traceparent: str | None = None


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class SystemCall:
    """A call the platform makes itself (a consumer, a job, the receiver); it names its reason.

    It is never built from a request. Without a ``call_id`` it gets a fresh UUIDv7.
    """

    reason: str
    scope: Scope | None
    call_id: str = field(default_factory=_new_call_id)
    traceparent: str | None = None


def actor_of(call: Call | SystemCall) -> Actor:
    """Who acts in ``call``, as audit rows record it."""
    match call:
        case Call(principal=principal, request_id=request_id):
            return Actor(principal.subject, request_id)
        case _:
            assert_type(call, SystemCall)
            return Actor(f"system:{call.reason}", call.call_id)


@contextmanager
def acting(call: Call | SystemCall) -> Generator[None]:
    """``call``'s actor for the length of the block."""
    with acting_as(actor_of(call)):
        yield
