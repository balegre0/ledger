"""What an ``Idempotency-Key`` may be, whatever the transport that carries it (§9.5)."""

import pytest

from ledger_kernel.errors import ErrorKind
from ledger_platform.data.idempotency import MAX_KEY_LENGTH, InvalidIdempotencyKeyError, is_idempotency_key


@pytest.mark.parametrize("key", ["k", "import-1", "!~", "k" * MAX_KEY_LENGTH])
def test_visible_ascii_without_spaces_is_a_key(key: str) -> None:
    assert is_idempotency_key(key)


@pytest.mark.parametrize("key", ["", "a b", "é", "tab\tkey", "k" * (MAX_KEY_LENGTH + 1)])
def test_anything_else_is_not(key: str) -> None:
    assert not is_idempotency_key(key)


def test_a_key_that_is_not_one_is_refused_without_repeating_it() -> None:
    error = InvalidIdempotencyKeyError()

    assert (error.kind, error.code) == (ErrorKind.INVALID, "invalid_idempotency_key")
    assert str(MAX_KEY_LENGTH) in str(error)
