"""What a route documents of its answers, and the 412 of a stale ``If-Match``."""

from http import HTTPStatus

import pytest
from pydantic import TypeAdapter
from starlette.exceptions import HTTPException

from ledger_platform.http.openapi import precondition_checked, problems, success
from ledger_platform.http.problems import PROBLEM_JSON, Problem

SCHEMA = TypeAdapter(dict[str, object])
ANSWERS = TypeAdapter(dict[int, dict[str, dict[str, object]]])


class StaleError(Exception):
    pass


def test_problems_are_documented_as_problem_documents_with_503_added() -> None:
    answers = problems(404, 409)
    assert set(answers) == {404, 409, 503}
    for status in (HTTPStatus.NOT_FOUND, HTTPStatus.CONFLICT, HTTPStatus.SERVICE_UNAVAILABLE):
        assert answers[status.value] == {
            "description": status.phrase,
            "content": {PROBLEM_JSON: {"schema": Problem.model_json_schema()}},
        }


def test_a_problem_document_names_its_members_and_allows_more() -> None:
    schema = SCHEMA.validate_python(Problem.model_json_schema())
    assert set(SCHEMA.validate_python(schema["properties"])) == {
        "type",
        "title",
        "status",
        "code",
        "detail",
        "request_id",
    }
    assert schema["required"] == ["type", "title", "status", "code"]
    assert schema["additionalProperties"] is True


def test_a_success_documents_the_headers_it_carries() -> None:
    answers = ANSWERS.validate_python(success(HTTPStatus.CREATED, "Location", "ETag"))
    assert set(answers) == {201}
    assert set(answers[201]["headers"]) == {"Location", "ETag"}
    replayed = ANSWERS.validate_python(success(HTTPStatus.OK, "Idempotent-Replayed"))[200]["headers"]
    assert replayed == {
        "Idempotent-Replayed": {
            "description": (
                "true when the answer is the one stored for an earlier use of the Idempotency-Key."
            ),
            "schema": {"type": "string", "enum": ["true"]},
        }
    }


def test_a_stale_version_is_a_412() -> None:
    with pytest.raises(HTTPException) as caught, precondition_checked(StaleError):
        raise StaleError
    assert caught.value.status_code == HTTPStatus.PRECONDITION_FAILED
    assert isinstance(caught.value.__cause__, StaleError)


def test_any_other_error_goes_through() -> None:
    with pytest.raises(LookupError), precondition_checked(StaleError):
        raise LookupError


def test_nothing_raised_is_nothing_answered() -> None:
    with precondition_checked(StaleError):
        pass
