"""The e-CF state machine (spec §8.3, §8.6.4): a document's states, the steps between them, nothing else.

```
numbered ─SIGN─► signed ─SUBMIT─► submitted ─POLL─► in_process ─POLL─► in_process
  │  ▲               │  └─SUBMIT─► outcome_unknown ─ADOPT_TRACK_ID─► submitted
  │  └─UNBLOCK─┐     │             outcome_unknown ─POLL─► in_process   (consultarfce: pending)
  ├─BLOCK─► blocked  └─DECIDE─► accepted | accepted_conditionally | rejected
  └─VOID──► voided ◄─VOID─ blocked      (DECIDE also from submitted, in_process, outcome_unknown)
```

* ``SUBMIT`` leads to ``submitted`` (DGII gave a track id) or ``outcome_unknown`` (the request may have left
  and its answer was lost, or a previous run marked the submission and never recorded its answer).
* ``DECIDE`` records DGII's final answer: the RFCE's, answered at once, and a refusal at the door come
  straight from ``signed``; ``consultarfce``'s from ``outcome_unknown``.
* ``POLL`` records an answer not yet final: in process, or DGII knows no such track id (``outcome_unknown``,
  settled by the e-NCF's track ids). ``in_process`` stays ``in_process`` while DGII deliberates.
* **Nothing leads back to ``signed`` or ``numbered`` once a document may have left** (Decision 7: an unknown
  outcome is settled by a track id DGII lists, never by sending again), so no step can resend it.
* ``BLOCK`` (an error a person must look at, such as a range expired by the moment of signing) and ``VOID``
  (an accepted ANECF) happen only before signing; an operator ``UNBLOCK``s.
* The final states (``FINAL_STATES``) lead nowhere: delivery to the buyer is a column of its own
  (``DeliveryState``).
"""

from enum import StrEnum
from types import MappingProxyType
from typing import ClassVar, Final, Literal, assert_type
lazy from collections.abc import Mapping

from ledger_country_do.channels.ecf_v1.domain.errors import EcfError
from ledger_country_do.channels.ecf_v1.domain.status_map import EcfState


class EcfDocumentState(StrEnum):
    """Where an e-CF stands (spec §8.6.4)."""

    NUMBERED = "numbered"
    SIGNED = "signed"
    SUBMITTED = "submitted"
    IN_PROCESS = "in_process"
    OUTCOME_UNKNOWN = "outcome_unknown"
    ACCEPTED = "accepted"
    ACCEPTED_CONDITIONALLY = "accepted_conditionally"
    REJECTED = "rejected"
    BLOCKED = "blocked"
    VOIDED = "voided"


class Step(StrEnum):
    """What moves a document (see the module)."""

    SIGN = "sign"
    SUBMIT = "submit"
    ADOPT_TRACK_ID = "adopt_track_id"
    POLL = "poll"
    DECIDE = "decide"
    BLOCK = "block"
    UNBLOCK = "unblock"
    VOID = "void"


class DeliveryState(StrEnum):
    """Where delivery to the buyer stands, beside the document's state (spec §8.6.5)."""

    NOT_STARTED = "not_started"
    NOT_APPLICABLE = "not_applicable"
    DELIVERING = "delivering"
    DELIVERED = "delivered"
    FAILED = "failed"


_S: Final = EcfDocumentState
_DECIDED: Final = frozenset({_S.ACCEPTED, _S.ACCEPTED_CONDITIONALLY, _S.REJECTED})
FINAL_STATES: Final = _DECIDED | {_S.VOIDED}
"""The states nothing leaves."""

TRANSITIONS: Final[Mapping[tuple[EcfDocumentState, Step], frozenset[EcfDocumentState]]] = MappingProxyType(
    {
        (_S.NUMBERED, Step.SIGN): frozenset({_S.SIGNED}),
        (_S.NUMBERED, Step.BLOCK): frozenset({_S.BLOCKED}),
        (_S.NUMBERED, Step.VOID): frozenset({_S.VOIDED}),
        (_S.BLOCKED, Step.UNBLOCK): frozenset({_S.NUMBERED}),
        (_S.BLOCKED, Step.VOID): frozenset({_S.VOIDED}),
        (_S.SIGNED, Step.SUBMIT): frozenset({_S.SUBMITTED, _S.OUTCOME_UNKNOWN}),
        (_S.SIGNED, Step.DECIDE): _DECIDED,
        (_S.SUBMITTED, Step.POLL): frozenset({_S.IN_PROCESS, _S.OUTCOME_UNKNOWN}),
        (_S.SUBMITTED, Step.DECIDE): _DECIDED,
        (_S.IN_PROCESS, Step.POLL): frozenset({_S.IN_PROCESS, _S.OUTCOME_UNKNOWN}),
        (_S.IN_PROCESS, Step.DECIDE): _DECIDED,
        (_S.OUTCOME_UNKNOWN, Step.ADOPT_TRACK_ID): frozenset({_S.SUBMITTED}),
        (_S.OUTCOME_UNKNOWN, Step.POLL): frozenset({_S.IN_PROCESS}),
        (_S.OUTCOME_UNKNOWN, Step.DECIDE): _DECIDED,
    }
)
"""Every legal move: ``(state, step)`` to the states the step may lead to; anything else is refused."""


class IllegalTransitionError(EcfError):
    """A move the state machine does not have: a defect of the caller, never retried."""

    code: ClassVar[str] = "do_ecf_transition_illegal"

    def __init__(self, state: EcfDocumentState, step: Step, to: EcfDocumentState) -> None:
        super().__init__(f"an e-CF cannot go from {state} to {to} by {step}")


def transition(state: EcfDocumentState, step: Step, to: EcfDocumentState) -> EcfDocumentState:
    """``to``, when ``step`` may lead ``state`` there; ``IllegalTransitionError`` otherwise."""
    if to not in TRANSITIONS.get((state, step), frozenset()):
        raise IllegalTransitionError(state, step, to)
    return to


def is_final(state: EcfDocumentState) -> bool:
    """Whether ``state`` is one nothing leaves."""
    return state in FINAL_STATES


def answered(state: EcfState) -> EcfDocumentState:
    """The document's state an answer of DGII's leads to (``status_map``'s state)."""
    match state:
        case EcfState.IN_PROCESS:
            return _S.IN_PROCESS
        case EcfState.ACCEPTED:
            return _S.ACCEPTED
        case EcfState.ACCEPTED_CONDITIONALLY:
            return _S.ACCEPTED_CONDITIONALLY
        case EcfState.REJECTED:
            return _S.REJECTED
        case _:
            assert_type(state, Literal[EcfState.OUTCOME_UNKNOWN])
            return _S.OUTCOME_UNKNOWN
