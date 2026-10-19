"""Fixtures of fiscal-kit's tests: one signing certificate per session (RSA keys are slow), and signxml.

signxml shares no code with ``ledger_fiscal_kit.xml.dsig``: it is the second implementation every signature
made here is verified with. ``excise_empty_xmlns_declarations`` makes it undo lxml's spurious ``xmlns=""``
(its issue 193), without which it cannot verify a signature in the default namespace.
"""

from abc import ABCMeta, abstractmethod
from typing import Final, Protocol, TypeIs
lazy from collections.abc import Callable

import pytest
from cryptography import x509
from cryptography.x509.oid import NameOID
from signxml.algorithms import DigestAlgorithm, SignatureMethod
from signxml.verifier import SignatureConfiguration, XMLVerifier
lazy from signxml.verifier import VerifyResult

from ledger_testing.pki import Authority, distinguished_name
lazy from ledger_testing.pki import Issued

type Verify = Callable[[bytes, x509.Certificate], None]
"""Verify a signed document with signxml by ``certificate``: signxml's error when it is not good."""

_CONFIGURATION: Final = SignatureConfiguration(
    require_x509=True,
    location="./",
    expect_references=1,
    signature_methods=frozenset({SignatureMethod.RSA_SHA256}),
    digest_algorithms=frozenset({DigestAlgorithm.SHA256}),
)


class _Verifies(Protocol, metaclass=ABCMeta):
    """signxml's ``XMLVerifier`` as called here (signxml 5.1 leaves parts of ``verify`` unannotated)."""

    excise_empty_xmlns_declarations: bool

    @abstractmethod
    def verify(
        self, data: bytes, *, x509_cert: x509.Certificate, expect_config: SignatureConfiguration
    ) -> VerifyResult | list[VerifyResult]:
        """Verify ``data`` with ``x509_cert``."""


def _is_verifier(value: object) -> TypeIs[_Verifies]:
    return isinstance(value, XMLVerifier)


def _as_verifier(made: object) -> _Verifies:
    """``made``, signxml's ``XMLVerifier``, as ``_Verifies`` describes it."""
    assert _is_verifier(made)
    return made


@pytest.fixture(scope="session")
def issued() -> Issued:
    """A signing certificate of an authority of the test's own, with its key."""
    return Authority.root().issue(distinguished_name((NameOID.COMMON_NAME, "LEDGER TEST SIGNER")))


@pytest.fixture(scope="session")
def verify() -> Verify:
    """signxml's verification (see the module)."""

    def verified(signed: bytes, certificate: x509.Certificate) -> None:
        verifier = _as_verifier(XMLVerifier())
        verifier.excise_empty_xmlns_declarations = True
        verifier.verify(signed, x509_cert=certificate, expect_config=_CONFIGURATION)

    return verified
