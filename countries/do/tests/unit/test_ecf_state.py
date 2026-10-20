from itertools import product
from typing import Final

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ledger_country_do.channels.ecf_v1.domain.ecf import (
    FINAL_STATES,
    TRANSITIONS,
    DeliveryState,
    EcfDocumentState,
    IllegalTransitionError,
    Step,
    answered,
    is_final,
    transition,
)
from ledger_country_do.channels.ecf_v1.domain.status_map import EcfState

S: Final = EcfDocumentState
DECIDED: Final = frozenset({S.ACCEPTED, S.ACCEPTED_CONDITIONALLY, S.REJECTED})
EXPECTED: Final = {
    (S.NUMBERED, Step.SIGN): {S.SIGNED},
    (S.NUMBERED, Step.BLOCK): {S.BLOCKED},
    (S.NUMBERED, Step.VOID): {S.VOIDED},
    (S.BLOCKED, Step.UNBLOCK): {S.NUMBERED},
    (S.BLOCKED, Step.VOID): {S.VOIDED},
    (S.SIGNED, Step.SUBMIT): {S.SUBMITTED, S.OUTCOME_UNKNOWN},
    (S.SIGNED, Step.DECIDE): DECIDED,
    (S.SUBMITTED, Step.POLL): {S.IN_PROCESS, S.OUTCOME_UNKNOWN},
    (S.SUBMITTED, Step.DECIDE): DECIDED,
    (S.IN_PROCESS, Step.POLL): {S.IN_PROCESS, S.OUTCOME_UNKNOWN},
    (S.IN_PROCESS, Step.DECIDE): DECIDED,
    (S.OUTCOME_UNKNOWN, Step.ADOPT_TRACK_ID): {S.SUBMITTED},
    (S.OUTCOME_UNKNOWN, Step.POLL): {S.IN_PROCESS},
    (S.OUTCOME_UNKNOWN, Step.DECIDE): DECIDED,
}
SENT: Final = frozenset({S.SUBMITTED, S.IN_PROCESS, S.OUTCOME_UNKNOWN}) | DECIDED
"""States a document reaches only once it may have left for DGII."""


def reachable(start: EcfDocumentState) -> set[EcfDocumentState]:
    seen = {start}
    pending = [start]
    while pending:
        state = pending.pop()
        for (source, _), targets in TRANSITIONS.items():
            if source is state:
                for target in targets - seen:
                    seen.add(target)
                    pending.append(target)
    return seen


def test_the_table_is_the_one_drawn() -> None:
    assert {key: set(targets) for key, targets in TRANSITIONS.items()} == EXPECTED


@pytest.mark.parametrize(("state", "step", "to"), list(product(S, Step, S)))
def test_every_triple_is_either_listed_or_refused(
    state: EcfDocumentState, step: Step, to: EcfDocumentState
) -> None:
    if to in TRANSITIONS.get((state, step), frozenset()):
        assert transition(state, step, to) is to
        return
    with pytest.raises(IllegalTransitionError) as caught:
        transition(state, step, to)
    assert caught.value.code == "do_ecf_transition_illegal"
    assert caught.value.kind == "internal"
    assert str(caught.value) == f"an e-CF cannot go from {state} to {to} by {step}"


def test_an_unknown_outcome_never_leads_back_to_signed() -> None:
    for step in Step:
        with pytest.raises(IllegalTransitionError):
            transition(S.OUTCOME_UNKNOWN, step, S.SIGNED)


@pytest.mark.parametrize("state", sorted(SENT))
def test_once_a_document_may_have_left_it_can_never_be_signed_or_numbered_again(
    state: EcfDocumentState,
) -> None:
    assert reachable(state).isdisjoint({S.NUMBERED, S.SIGNED, S.BLOCKED, S.VOIDED})


def test_void_only_from_numbered_or_blocked() -> None:
    assert {state for (state, step) in TRANSITIONS if step is Step.VOID} == {S.NUMBERED, S.BLOCKED}


def test_the_final_states_lead_nowhere() -> None:
    assert frozenset(FINAL_STATES) == DECIDED | {S.VOIDED}
    assert all(state not in FINAL_STATES for state, _ in TRANSITIONS)
    assert [state for state in S if is_final(state)] == [state for state in S if state in FINAL_STATES]


@given(st.lists(st.tuples(st.sampled_from(list(Step)), st.sampled_from(list(S))), max_size=60))
def test_random_steps_never_leave_a_final_state(steps: list[tuple[Step, EcfDocumentState]]) -> None:
    state = S.NUMBERED
    left = False
    for step, to in steps:
        try:
            moved = transition(state, step, to)
        except IllegalTransitionError:
            continue
        assert not is_final(state)
        if state in SENT:
            left = True
        if left:
            assert moved not in {S.NUMBERED, S.SIGNED}
        state = moved


@pytest.mark.parametrize("state", list(EcfState))
def test_every_state_an_answer_leads_to_is_a_document_state(state: EcfState) -> None:
    assert answered(state).value == state.value


def test_delivery_is_its_own_column() -> None:
    assert {state: state.value for state in DeliveryState} == {
        DeliveryState.NOT_STARTED: "not_started",
        DeliveryState.NOT_APPLICABLE: "not_applicable",
        DeliveryState.DELIVERING: "delivering",
        DeliveryState.DELIVERED: "delivered",
        DeliveryState.FAILED: "failed",
    }
