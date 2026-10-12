from dataclasses import FrozenInstanceError

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ledger_kernel.access import (
    MAX_SUBJECT_LENGTH,
    InvalidSubjectError,
    Permission,
    Principal,
    SubjectId,
    parse_subject,
)
from ledger_kernel.ids import OrganizationId, new_id

SUBJECTS = st.from_regex(rf"[A-Za-z0-9._@:-]{{1,{MAX_SUBJECT_LENGTH}}}", fullmatch=True)


@given(SUBJECTS)
def test_a_subject_of_the_allowed_characters_reads_back_unchanged(text: str) -> None:
    assert parse_subject(text) == text


@pytest.mark.parametrize(
    "text",
    ["", " ", "a b", "394763379928989699\n", "josé", "x" * (MAX_SUBJECT_LENGTH + 1), "a/b", "<b>"],
    ids=["empty", "space", "inner-space", "newline", "non-ascii", "too-long", "slash", "markup"],
)
def test_anything_else_is_not_a_subject(text: str) -> None:
    with pytest.raises(InvalidSubjectError, match="not a subject"):
        parse_subject(text)


def test_a_huge_subject_is_not_echoed_back() -> None:
    with pytest.raises(InvalidSubjectError) as caught:
        parse_subject("x" * 10_000)
    assert len(str(caught.value)) < 80
    assert caught.value.code == "invalid_subject"


def test_permissions_have_stable_dotted_codes() -> None:
    assert {permission.value for permission in Permission} == {
        "company.read",
        "company.manage",
        "membership.read",
        "membership.manage",
        "ownership.manage",
        "certificate.manage",
        "invoice.read",
        "invoice.issue",
    }


def test_a_principal_is_an_immutable_value_without_roles_by_default() -> None:
    organization = OrganizationId(new_id())
    principal = Principal(subject=SubjectId("2001"), organization=organization, provider_organization="1001")
    assert principal == Principal(
        subject=SubjectId("2001"), organization=organization, provider_organization="1001"
    )
    assert principal.roles == frozenset()
    field = "subject"
    with pytest.raises(FrozenInstanceError):
        setattr(principal, field, "someone else")
