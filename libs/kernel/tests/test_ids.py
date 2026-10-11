from dataclasses import FrozenInstanceError
lazy from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ledger_kernel.ids import CompanyId, InvalidIdError, OrganizationId, Tenant, new_id, parse_id

CANONICAL = "0192f7a4-6b1e-7c3d-8e4f-5a6b7c8d9e0f"


def test_new_ids_are_version_7_unique_and_sorted_by_creation() -> None:
    ids = [new_id() for _ in range(1000)]
    assert all(value.version == 7 for value in ids)
    assert ids == sorted(ids)
    assert len(set(ids)) == len(ids)


@given(st.uuids())
def test_the_canonical_form_reads_back_in_any_case(value: UUID) -> None:
    assert parse_id(str(value)) == value
    assert parse_id(str(value).upper()) == value


@pytest.mark.parametrize(
    "text",
    [
        "",
        "not-an-id",
        "{" + CANONICAL + "}",
        "urn:uuid:" + CANONICAL,
        CANONICAL.replace("-", ""),
        "0192f7a46-b1e-7c3d-8e4f-5a6b7c8d9e0f",  # hyphen moved
        CANONICAL + "\n",
        "\uff10\uff11\uff19\uff12f7a4-6b1e-7c3d-8e4f-5a6b7c8d9e0f",  # full-width digits
    ],
    ids=["empty", "word", "braces", "urn", "no-hyphens", "moved-hyphen", "newline", "full-width"],
)
def test_every_other_form_is_refused(text: str) -> None:
    with pytest.raises(InvalidIdError, match="not an id"):
        parse_id(text)


def test_a_huge_input_is_not_echoed_back() -> None:
    with pytest.raises(InvalidIdError) as caught:
        parse_id("x" * 10_000)
    assert len(str(caught.value)) < 60
    assert caught.value.code == "invalid_id"


def test_a_tenant_is_an_immutable_value() -> None:
    organization, company = OrganizationId(new_id()), CompanyId(new_id())
    tenant = Tenant(organization, company)
    assert tenant == Tenant(organization, company)
    assert len({tenant, Tenant(organization, company)}) == 1
    field = "company"
    with pytest.raises(FrozenInstanceError):
        setattr(tenant, field, company)
