from uuid import UUID, uuid7

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ledger_kernel.ids import (
    COMPANY_PREFIX,
    ORGANIZATION_PREFIX,
    InvalidIdError,
    InvalidIdPrefixError,
    InvalidPublicIdError,
    format_id,
    new_id,
    parse_id,
    parse_id_prefix,
    parse_uuid,
)

CANONICAL = "0192f7a4-6b1e-7c3d-8e4f-5a6b7c8d9e0f"


def test_new_ids_are_version_7_unique_and_sorted_by_creation() -> None:
    ids = [new_id() for _ in range(1000)]
    assert all(value.version == 7 for value in ids)
    assert ids == sorted(ids)
    assert len(set(ids)) == len(ids)


@given(st.uuids())
def test_the_canonical_form_reads_back_in_any_case(value: UUID) -> None:
    assert parse_uuid(str(value)) == value
    assert parse_uuid(str(value).upper()) == value


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
        parse_uuid(text)


def test_a_huge_input_is_not_echoed_back() -> None:
    with pytest.raises(InvalidIdError) as caught:
        parse_uuid("x" * 10_000)
    assert len(str(caught.value)) < 60
    assert caught.value.code == "invalid_id"


def test_a_public_id_is_the_typeid_of_the_uuid() -> None:
    value = UUID("01890a5d-ac96-774b-bcce-b302099a8057")
    assert (
        format_id("inv", value) == "inv_01h455vb4pex5vsknk084sn02q"
    )  # the TypeID specification's own example
    assert parse_id("inv", "inv_01h455vb4pex5vsknk084sn02q") == value


@given(st.uuids())
def test_parse_reads_back_what_format_wrote(value: UUID) -> None:
    assert parse_id("cmp", format_id("cmp", value)) == value


@pytest.mark.parametrize(
    "text",
    [
        "cmp_01h455vb4pex5vsknk084sn02q",  # another prefix
        "inv01h455vb4pex5vsknk084sn02q",  # no separator
        "inv_01h455vb4pex5vsknk084sn02",  # 25 characters
        "inv_81h455vb4pex5vsknk084sn02q",  # first character above 7: more than 128 bits
        "inv_01H455VB4PEX5VSKNK084SN02Q",  # upper case
        "inv_01h455vb4pex5vsknk084sn0iq",  # 'i' is not in the alphabet
        "",
    ],
)
def test_anything_else_is_not_an_id_of_the_prefix(text: str) -> None:
    with pytest.raises(InvalidPublicIdError) as caught:
        parse_id("inv", text)
    assert caught.value.code == "invalid_public_id"


@pytest.mark.parametrize("prefix", ["i", "invoice", "Inv", "in_v", "1nv"])
def test_a_prefix_outside_the_naming_rule_is_a_defect(prefix: str) -> None:
    with pytest.raises(InvalidIdPrefixError):
        format_id(prefix, uuid7())


def test_a_uuid_in_canonical_form_still_reads() -> None:
    value = uuid7()
    assert parse_uuid(str(value)) == value


def test_an_id_prefix_is_two_to_five_lower_case_letters() -> None:
    assert parse_id_prefix("cmp") == "cmp"
    for text in ("c", "toolong", "Cmp", "c1"):
        with pytest.raises(InvalidIdPrefixError):
            parse_id_prefix(text)


def test_organizations_and_companies_have_the_public_prefixes_their_manifests_declare() -> None:
    assert (ORGANIZATION_PREFIX, COMPANY_PREFIX) == ("org", "cmp")
    assert parse_id_prefix(ORGANIZATION_PREFIX) == "org"
    assert parse_id_prefix(COMPANY_PREFIX) == "cmp"
