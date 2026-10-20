"""What the signer answers, as the routes answer it (§9.6): its own code, and the status a client acts on.

The client raises the signer's refusals as its facade does (``client.API_REFUSALS``): the ``INVALID`` family
is 422, a conflict 409, ``certificate_not_found`` 404, each by its kind. Three need a status their kind does
not say, so the routes raise them again as the subclasses here, which keep their code and message and name
their answer (the platform's ``ProblemAnswer``): a bundle past 2 MiB is 413, one that did not arrive
before the import's deadline 408, and a signer out of reach 503 with ``Retry-After``. What is ours to fix
answers 500 ``internal`` and is logged with its cause: an import stream our own client shaped wrongly
(``signer_import_header_first``, ``signer_import_chunks_unexpected``), and an answer of the signer no mapping
reads (the rest of the ``RemoteError`` family; ``signer_defect`` and the guard's refusals are defects
already).
"""

from contextlib import contextmanager
from http import HTTPStatus
from typing import ClassVar, Final
lazy from collections.abc import Generator, Mapping

from ledger_fiscal_kit.ports import SignerUnavailableError
from ledger_kernel.errors import ErrorKind, LedgerError
from ledger_platform.grpc.client import RemoteError
from ledger_signer.adapters.grpc.codec import (
    ImportChunksUnexpectedError,
    ImportHeaderFirstError,
    ImportTooSlowError,
)
from ledger_signer.application.importing import ImportTooLargeError

RETRY_AFTER_SECONDS: Final = 5
"""How long a client waits before asking a signer out of reach again."""
_NO_HEADERS: Final[Mapping[str, str]] = {}


class SignerOutOfReachError(SignerUnavailableError):
    """The signer out of reach: 503, try again in ``RETRY_AFTER_SECONDS``."""

    def problem_status(self) -> HTTPStatus:
        """503: its kind's, with ``Retry-After``."""
        return HTTPStatus.SERVICE_UNAVAILABLE

    def problem_headers(self) -> Mapping[str, str]:
        """``Retry-After``."""
        return {"Retry-After": str(RETRY_AFTER_SECONDS)}


class BundleTooLargeError(ImportTooLargeError):
    """A bundle past 2 MiB: 413, as the body limit answers it."""

    def problem_status(self) -> HTTPStatus:
        """413."""
        return HTTPStatus.CONTENT_TOO_LARGE

    def problem_headers(self) -> Mapping[str, str]:
        """None of its own."""
        return _NO_HEADERS


class BundleTooSlowError(ImportTooSlowError):
    """A bundle that trickled past the import's deadline: 408, the request took too long to arrive."""

    def problem_status(self) -> HTTPStatus:
        """408."""
        return HTTPStatus.REQUEST_TIMEOUT

    def problem_headers(self) -> Mapping[str, str]:
        """None of its own."""
        return _NO_HEADERS


class SignerCallDefectError(LedgerError):
    """A call of the signer that failed on a defect of ours, or that it answered as no mapping reads."""

    code: ClassVar[str] = "signer_call_defect"

    def __init__(self) -> None:
        super().__init__("a call of the signer failed on a defect")


@contextmanager
def answered() -> Generator[None]:
    """Raise what the signer answered inside as the routes answer it (see the module)."""
    try:
        yield
    except SignerUnavailableError as error:
        raise SignerOutOfReachError from error
    except ImportTooLargeError as error:
        raise BundleTooLargeError from error
    except ImportTooSlowError as error:
        raise BundleTooSlowError from error
    except (ImportHeaderFirstError, ImportChunksUnexpectedError) as error:
        raise SignerCallDefectError from error
    except RemoteError as error:
        if error.kind is ErrorKind.INTERNAL:
            raise
        raise SignerCallDefectError from error
