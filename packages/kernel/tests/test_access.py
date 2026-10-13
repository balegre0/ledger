from dataclasses import FrozenInstanceError
from typing import Final

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ledger_kernel.access import (
    MAX_PROVIDER_ORGANIZATION_LENGTH,
    MAX_SUBJECT_LENGTH,
    InvalidPrincipalError,
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


ORGANIZATION: Final = OrganizationId(new_id())


def build(**overrides: object) -> Principal:
    """A principal holding values of any type, the way a caller that skipped the type checker makes one."""
    principal = Principal(subject=SubjectId("2001"), organization=ORGANIZATION, provider_organization="1001")
    for name, value in overrides.items():
        object.__setattr__(principal, name, value)
    principal.__post_init__()
    return principal


def test_roles_are_kept_as_given_when_they_are_a_frozenset_of_text() -> None:
    assert build(roles=frozenset({"admin"})).roles == {"admin"}


@pytest.mark.parametrize(
    "roles",
    [{"admin"}, ["admin"], ("admin",), "admin", None, frozenset({1}), frozenset({b"admin"})],
    ids=["set", "list", "tuple", "text", "none", "numbers", "bytes"],
)
def test_roles_that_are_not_a_frozenset_of_text_are_refused(roles: object) -> None:
    with pytest.raises(InvalidPrincipalError, match="roles") as caught:
        build(roles=roles)
    assert caught.value.code == "invalid_principal"


@pytest.mark.parametrize(
    "provider_organization",
    ["", "x" * (MAX_PROVIDER_ORGANIZATION_LENGTH + 1), 1001, None],
    ids=["empty", "too-long", "number", "none"],
)
def test_a_provider_organization_that_is_empty_too_long_or_not_text_is_refused(
    provider_organization: object,
) -> None:
    with pytest.raises(InvalidPrincipalError, match="provider_organization") as caught:
        build(provider_organization=provider_organization)
    assert len(str(caught.value)) < 80


def test_a_provider_organization_of_the_longest_length_is_accepted() -> None:
    longest = "9" * MAX_PROVIDER_ORGANIZATION_LENGTH
    assert build(provider_organization=longest).provider_organization == longest
