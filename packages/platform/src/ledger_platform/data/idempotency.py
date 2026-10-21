"""What an ``Idempotency-Key`` may be, whatever carries it (§9.5): an HTTP header, a gRPC request's field.

A key is 1 to ``MAX_KEY_LENGTH`` visible ASCII characters without spaces: what a client can put in a
header and see back unchanged. The HTTP edge (``http.idempotency``) and a module that takes keys over
another transport (the signer's imports) check them by the same rule and refuse them with the same error.
"""

import re
from typing import ClassVar, Final

from ledger_kernel.errors import InvalidInputError

MAX_KEY_LENGTH: Final = 255
_KEY: Final = re.compile(rf"[!-~]{{1,{MAX_KEY_LENGTH}}}", re.ASCII)


def is_idempotency_key(key: str) -> bool:
    """Whether ``key`` may be an idempotency key (see the module)."""
    return _KEY.fullmatch(key) is not None


class InvalidIdempotencyKeyError(InvalidInputError):
    """A key that is not 1 to ``MAX_KEY_LENGTH`` visible ASCII characters (never repeated)."""

    code: ClassVar[str] = "invalid_idempotency_key"

    def __init__(self) -> None:
        super().__init__(f"an Idempotency-Key is 1 to {MAX_KEY_LENGTH} visible ASCII characters")
