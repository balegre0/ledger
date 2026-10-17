from dataclasses import FrozenInstanceError
from typing import Final

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ledger_kernel.access import (
    MAX_PROVIDER_ORGANIZATION_LENGTH,
    MAX_SUBJECT_LENGTH,
    CompanyNotFoundError,
    InvalidPermissionError,
    InvalidSubjectError,
    Permission,
    PermissionDeniedError,
    Principal,
    Role,
    SubjectId,
    parse_permission,
    parse_subject,
)
from ledger_kernel.errors import ErrorKind, InvalidValueError
from ledger_kernel.ids import COMPANY_PREFIX, CompanyId, OrganizationId, format_id, new_id

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


@pytest.mark.parametrize(
    "text",
    [
        "identity.companies.read",
        "identity.memberships.manage",
        "invoicing.documents.issue",
        "invoicing.documents.void",
        "do_ecf.certificates.create",
        "identity.ownership.own",
    ],
)
def test_a_permission_is_module_resource_and_action(text: str) -> None:
    permission = parse_permission(text)
    assert permission == text
    assert type(permission) is str


@pytest.mark.parametrize(
    "text",
    [
        "",
        "identity.read",
        "Identity.companies.read",
        "identity.companies.delete",
        "identity.companies.read.more",
        "identity..read",
        "1identity.companies.read",
        "identity.companies.read\n",
        "identity.compañías.read",
        " identity.companies.read",
    ],
    ids=[
        "empty",
        "two-parts",
        "upper-case",
        "unknown-action",
        "four-parts",
        "empty-resource",
        "leading-digit",
        "newline",
        "non-ascii",
        "space",
    ],
)
def test_anything_else_is_not_a_permission(text: str) -> None:
    with pytest.raises(
        InvalidPermissionError, match=r"^not a permission \(<module>\.<resource>\.<action>\): "
    ) as caught:
        parse_permission(text)
    assert (caught.value.kind, caught.value.code) == (ErrorKind.INVALID, "invalid_permission")


def test_a_huge_permission_is_not_echoed_back() -> None:
    with pytest.raises(InvalidPermissionError) as caught:
        parse_permission("identity." * 10_000)
    assert len(str(caught.value)) < 120


def test_a_denied_permission_is_named_and_forbidden() -> None:
    error = PermissionDeniedError(parse_permission("identity.companies.manage"))
    assert str(error) == "this needs the permission identity.companies.manage"
    assert (error.kind, error.code, error.permission) == (
        ErrorKind.FORBIDDEN,
        "permission_denied",
        Permission("identity.companies.manage"),
    )


def test_a_company_the_caller_may_not_see_is_not_found() -> None:
    company = CompanyId(new_id())
    error = CompanyNotFoundError(company)
    assert str(error) == f"company {format_id(COMPANY_PREFIX, company)} was not found"
    assert (error.kind, error.code) == (ErrorKind.NOT_FOUND, "company_not_found")


def test_a_company_that_cannot_even_be_named_is_not_found_without_naming_it() -> None:
    error = CompanyNotFoundError()
    assert (str(error), error.code) == ("company was not found", "company_not_found")


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
    with pytest.raises(InvalidValueError, match=r"^principal\.roles must be a frozenset of text$") as caught:
        build(roles=roles)
    assert caught.value.code == "invalid_value"


@pytest.mark.parametrize(
    "provider_organization",
    ["", "x" * (MAX_PROVIDER_ORGANIZATION_LENGTH + 1), 1001, None],
    ids=["empty", "too-long", "number", "none"],
)
def test_a_provider_organization_that_is_empty_too_long_or_not_text_is_refused(
    provider_organization: object,
) -> None:
    with pytest.raises(
        InvalidValueError, match=r"^principal\.provider_organization must be text of 1 to "
    ) as caught:
        build(provider_organization=provider_organization)
    assert len(str(caught.value)) < 80


def test_a_provider_organization_of_the_longest_length_is_accepted() -> None:
    longest = "9" * MAX_PROVIDER_ORGANIZATION_LENGTH
    assert build(provider_organization=longest).provider_organization == longest


def test_the_built_in_roles_are_one_closed_list_of_words() -> None:
    assert [role.value for role in Role] == ["owner", "admin", "accountant", "issuer", "viewer"]
    assert Role("viewer") is Role.VIEWER
    with pytest.raises(ValueError, match="root"):
        Role("root")
