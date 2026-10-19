from enum import StrEnum
from typing import Final

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ledger_fiscal_kit.status import (
    MAX_MESSAGE_CODE,
    MAX_MESSAGE_TEXT,
    MAX_MESSAGES,
    AuthorityMessage,
    FiscalStatus,
    Severity,
    StatusEntry,
    StatusMap,
    UnknownAuthorityCodeError,
    UnmappedAuthorityCodeError,
    kept,
)
from ledger_kernel.errors import ErrorKind


class _State(StrEnum):
    PENDING = "pending"
    DONE = "done"


PENDING: Final = StatusEntry(_State.PENDING, FiscalStatus.PENDING, Severity.INFO, final=False)
DONE: Final = StatusEntry(_State.DONE, FiscalStatus.ACCEPTED, Severity.INFO, final=True)


def test_the_fiscal_statuses_and_severities_are_the_spec_s_names() -> None:
    assert {status: status.value for status in FiscalStatus} == {
        FiscalStatus.PENDING: "pending",
        FiscalStatus.ACCEPTED: "accepted",
        FiscalStatus.ACCEPTED_WITH_OBSERVATIONS: "accepted_with_observations",
        FiscalStatus.REJECTED: "rejected",
        FiscalStatus.VOIDED: "voided",
    }
    assert {severity: severity.value for severity in Severity} == {
        Severity.INFO: "info",
        Severity.WARNING: "warning",
        Severity.ERROR: "error",
    }


def test_a_map_answers_the_entry_of_each_documented_code() -> None:
    codes = StatusMap[int, _State]({0: PENDING, 1: DONE}, documented=(0, 1))
    assert codes.entry(0) is PENDING
    assert codes.entry(1) is DONE
    words = StatusMap[str, _State]({"OK": DONE}, documented=frozenset({"OK"}))
    assert words.entry("OK") is DONE


def test_a_documented_code_without_an_entry_is_refused_naming_it() -> None:
    with pytest.raises(UnmappedAuthorityCodeError, match="code 3 has no entry") as caught:
        StatusMap[int, _State]({0: PENDING, 1: DONE}, documented=(0, 1, 3))
    assert (caught.value.kind, caught.value.code) == (ErrorKind.INTERNAL, "authority_code_unmapped")


def test_an_entry_for_a_code_never_documented_is_refused_naming_it() -> None:
    with pytest.raises(UnmappedAuthorityCodeError, match="code 7 has an entry but is not documented"):
        StatusMap[int, _State]({0: PENDING, 7: DONE}, documented=(0,))


def test_a_code_the_map_does_not_hold_is_unknown_and_names_only_the_code() -> None:
    codes = StatusMap[int, _State]({1: DONE}, documented=(1,))
    with pytest.raises(UnknownAuthorityCodeError) as caught:
        codes.entry(9)
    assert (caught.value.kind, caught.value.code) == (ErrorKind.UNAVAILABLE, "authority_code_unknown")
    assert str(caught.value) == "the authority answered code 9, which it never documented for the service"
    with pytest.raises(UnknownAuthorityCodeError, match="code 'Aceptado'"):
        codes.entry("Aceptado")


def test_kept_keeps_the_first_twenty_messages_each_within_bounds() -> None:
    many = tuple(AuthorityMessage(code=f"{number}", message="x" * 600, lang="es") for number in range(25))
    found = kept(many)
    assert [message.code for message in found] == [f"{number}" for number in range(MAX_MESSAGES)]
    assert {len(message.message) for message in found} == {MAX_MESSAGE_TEXT}
    assert found[0].message.endswith("...")
    assert found[0].lang == "es"


def test_kept_leaves_a_message_within_bounds_as_it_is() -> None:
    message = AuthorityMessage(code="613", message="La sumatoria excede la tolerancia", lang="es")
    full = AuthorityMessage(code="c" * MAX_MESSAGE_CODE, message="x" * MAX_MESSAGE_TEXT, lang="es")
    assert kept([message, full]) == (message, full)


@given(
    st.lists(
        st.builds(
            AuthorityMessage,
            code=st.text(max_size=3 * MAX_MESSAGE_CODE),
            message=st.text(max_size=2 * MAX_MESSAGE_TEXT),
            lang=st.sampled_from(["es", "en"]),
        ),
        max_size=2 * MAX_MESSAGES,
    )
)
def test_kept_never_holds_more_than_its_bounds(messages: list[AuthorityMessage]) -> None:
    found = kept(messages)
    assert len(found) == min(len(messages), MAX_MESSAGES)
    assert all(len(message.message) <= MAX_MESSAGE_TEXT for message in found)
    assert all(len(message.code) <= MAX_MESSAGE_CODE for message in found)
