"""What a route documents of its answers (§9.6), and the 412 of an update whose ``If-Match`` is stale."""

from contextlib import contextmanager
from http import HTTPStatus
from typing import Final, Literal
lazy from collections.abc import Generator

from starlette.exceptions import HTTPException

from ledger_platform.http.problems import PROBLEM_JSON, Problem

type Answers = dict[int | str, dict[str, object]]
"""What a route documents as ``responses``: its answers by status."""
type SuccessHeader = Literal["Location", "ETag", "Idempotent-Replayed"]
"""A header a successful answer may carry."""
_PROBLEM_SCHEMA: Final = Problem.model_json_schema()
"""What every error answer holds, written into each answer: the document then needs no shared component."""
_UNAVAILABLE: Final = 503
"""What any operation may answer: the database, or the identity provider's keys, cannot be reached now."""
_TEXT: Final = {"type": "string"}
_SUCCESS_HEADERS: Final[dict[SuccessHeader, dict[str, object]]] = {
    "Location": {"description": "The path of the resource created.", "schema": _TEXT},
    "ETag": {"description": "The resource's version: what If-Match names to change it.", "schema": _TEXT},
    "Idempotent-Replayed": {
        "description": "true when the answer is the one stored for an earlier use of the Idempotency-Key.",
        "schema": {"type": "string", "enum": ["true"]},
    },
}


def problems(*statuses: int) -> Answers:
    """The error answers an operation documents, and 503: RFC 9457 problem documents, ``problem+json``.

    Written out: FastAPI's ``model`` would document them as ``application/json``, which no error is sent as.
    """
    answers: Answers = {
        status: {
            "description": HTTPStatus(status).phrase,
            "content": {PROBLEM_JSON: {"schema": _PROBLEM_SCHEMA}},
        }
        for status in (*statuses, _UNAVAILABLE)
    }
    return answers


def success(status: HTTPStatus, *headers: SuccessHeader) -> Answers:
    """The headers the success answer ``status`` carries, documented (FastAPI merges them into its answer)."""
    return {status.value: {"headers": {name: _SUCCESS_HEADERS[name] for name in headers}}}


@contextmanager
def precondition_checked(*stale: type[Exception]) -> Generator[None]:
    """An update whose ``If-Match`` names no current version (``stale`` raised) answers 412 (RFC 9110)."""
    try:
        yield
    except stale as error:
        raise HTTPException(HTTPStatus.PRECONDITION_FAILED) from error
