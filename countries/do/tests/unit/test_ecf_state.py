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
    (S.OUTCOME_UNKNOWN, Step.DECIDE): DECIDED,
}


def closure(starts: set[EcfDocumentState]) -> set[EcfDocumentState]:
    """``starts`` and every state some sequence of moves leads them to."""
    seen = set(starts)
    pending = list(starts)
    while pending:
        state = pending.pop()
        for (source, _), targets in TRANSITIONS.items():
            if source is state:
                for target in targets - seen:
                    seen.add(target)
                    pending.append(target)
    return seen


def moves(state: EcfDocumentState) -> list[tuple[Step, EcfDocumentState]]:
    """Every legal move out of ``state``."""
    return sorted(
        (step, target)
        for (source, step), targets in TRANSITIONS.items()
        if source is state
        for target in targets
    )


AFTER_SUBMIT: Final = closure(
    {target for (_, step), targets in TRANSITIONS.items() if step is Step.SUBMIT for target in targets}
    | set(TRANSITIONS[S.SIGNED, Step.DECIDE])
)
"""Every state a document can reach once it may have left for DGII."""


def test_the_table_matches_its_snapshot() -> None:
    assert {key: set(targets) for key, targets in TRANSITIONS.items()} == EXPECTED


def test_only_a_signed_document_is_submitted() -> None:
    assert {state for state, step in TRANSITIONS if step is Step.SUBMIT} == {S.SIGNED}


def test_nothing_a_document_reaches_once_it_may_have_left_can_submit_it_again() -> None:
    assert AFTER_SUBMIT
    assert not any((state, Step.SUBMIT) in TRANSITIONS for state in AFTER_SUBMIT)
    assert AFTER_SUBMIT.isdisjoint({S.NUMBERED, S.SIGNED})


@given(st.data())
def test_every_path_submits_at_most_once(data: st.DataObject) -> None:
    state = S.NUMBERED
    submits = 0
    for _ in range(60):
        legal = moves(state)
        if not legal:
            assert is_final(state)
            break
        step, to = data.draw(st.sampled_from(legal))
        state = transition(state, step, to)
        submits += step is Step.SUBMIT
        assert submits <= 1


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


def test_an_unknown_outcome_leaves_only_by_a_track_id_or_a_decision() -> None:
    assert {step for state, step in TRANSITIONS if state is S.OUTCOME_UNKNOWN} == {
        Step.ADOPT_TRACK_ID,
        Step.DECIDE,
    }


def test_void_only_from_numbered_or_blocked() -> None:
    assert {state for (state, step) in TRANSITIONS if step is Step.VOID} == {S.NUMBERED, S.BLOCKED}


def test_the_final_states_lead_nowhere() -> None:
    assert frozenset(FINAL_STATES) == DECIDED | {S.VOIDED}
    assert all(state not in FINAL_STATES for state, _ in TRANSITIONS)
    assert [state for state in S if is_final(state)] == [state for state in S if state in FINAL_STATES]


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
