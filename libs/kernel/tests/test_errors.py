from typing import ClassVar

import pytest

from ledger_kernel.errors import (
    ConflictError,
    ErrorKind,
    ForbiddenError,
    InvalidInputError,
    LedgerError,
    NotFoundError,
    UnavailableError,
    excerpt,
)


@pytest.mark.parametrize(
    ("error", "kind", "code"),
    [
        (LedgerError, ErrorKind.INTERNAL, "internal"),
        (InvalidInputError, ErrorKind.INVALID, "invalid_input"),
        (NotFoundError, ErrorKind.NOT_FOUND, "not_found"),
        (ConflictError, ErrorKind.CONFLICT, "conflict"),
        (ForbiddenError, ErrorKind.FORBIDDEN, "forbidden"),
        (UnavailableError, ErrorKind.UNAVAILABLE, "unavailable"),
    ],
)
def test_every_base_error_has_a_kind_and_a_stable_code(
    error: type[LedgerError], kind: ErrorKind, code: str
) -> None:
    assert (error.kind, error.code) == (kind, code)


class DuplicateInvoiceError(ConflictError):
    code: ClassVar[str] = "duplicate_invoice"

    def __init__(self, number: int) -> None:
        super().__init__(f"invoice {number} exists")


def test_a_subclass_refines_the_code_and_keeps_the_kind() -> None:
    error = DuplicateInvoiceError(7)
    assert (error.kind, error.code, str(error)) == (
        ErrorKind.CONFLICT,
        "duplicate_invoice",
        "invoice 7 exists",
    )


def test_an_excerpt_is_short_and_quoted() -> None:
    assert excerpt("abc") == "'abc'"
    assert excerpt("x" * 10_000) == "'" + "x" * 40 + "...'"
    assert excerpt("a\nb") == "'a\\nb'"
