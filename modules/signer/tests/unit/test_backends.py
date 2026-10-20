"""Custody's two backends behind one port: a key we keep sealed, or a credential at a certification entity.

Each signature verifies with signxml (an implementation that shares no code with fiscal-kit's ``dsig``); each
unwraps its data key through the KMS (no cache); the entity sees a 32-byte digest and nothing of the document.
A sealed secret that does not sign for its certificate is custody damaged (fiscal-kit's pairing errors, which
the signing use case turns into an internal error).
"""

import hashlib
import os
from abc import ABCMeta, abstractmethod
from typing import Final, Protocol, TypeIs, final, override
lazy from collections.abc import Sequence
lazy from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from signxml.algorithms import DigestAlgorithm, SignatureMethod
from signxml.verifier import SignatureConfiguration, XMLVerifier
from structlog.testing import capture_logs
lazy from cryptography import x509
lazy from signxml.verifier import VerifyResult

from ledger_fiscal_kit.xml import dsig
from ledger_kernel.errors import ErrorKind, LedgerError
from ledger_kernel.ids import CompanyId, OrganizationId, new_id
from ledger_signer.adapters.custody.backends import (
    Backends,
    EntityAnswerInvalidError,
    EntityNotConfiguredError,
)
from ledger_signer.adapters.custody.envelope import sealed
from ledger_signer.adapters.custody.kms_local import MASTER_KEY_BYTES, LocalFileKms
from ledger_signer.application.ports import (
    BundleUnreadableError,
    CertificationEntity,
    EntityCertificate,
    EntityRefusedError,
    EntityUnavailableError,
    KeyManagement,
    SealContext,
    Secret,
)
from ledger_signer.domain.certificates import BackendKind, CertificateId
from ledger_testing.pki import bundle, ec_key
lazy from ledger_signer.application.ports import Admitted
lazy from ledger_testing.pki import Issued

PASSWORD: Final = "PASSWORD-SENTINEL"
CREDENTIAL: Final = "CREDENTIAL-SENTINEL"
DOCUMENT: Final = b"<Document><Reference>DOCUMENT-SENTINEL</Reference></Document>"
DIGEST_BYTES: Final = 32
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


def verified(signed: bytes, certificate: x509.Certificate) -> None:
    """``signed`` verified by signxml with ``certificate``; signxml's error when it does not verify."""
    verifier = _as_verifier(XMLVerifier())
    verifier.excise_empty_xmlns_declarations = True  # lxml's spurious xmlns="" (signxml's issue 193)
    verifier.verify(signed, x509_cert=certificate, expect_config=_CONFIGURATION)


class CountingKms(KeyManagement, Protocol):
    """The stand-in KMS, and how many data keys it unwrapped."""

    unwraps: int


class RecordingEntity(CertificationEntity, Protocol):
    """The stand-in entity: its switches, and the digests it signed."""

    available: bool
    refusing: bool
    digests: list[bytes]


@final
class Answering(CertificationEntity):
    """An entity that answers ``chain`` for every credential and never signs."""

    __slots__ = ("_chain",)

    def __init__(self, chain: tuple[bytes, ...]) -> None:
        self._chain = chain

    @override
    async def certificate(self, credential: bytes) -> EntityCertificate:
        return EntityCertificate(chain=self._chain)

    @override
    async def sign_digest(self, credential: bytes, digest: bytes) -> bytes:
        raise EntityUnavailableError


def a_context() -> SealContext:
    return SealContext(
        organization=OrganizationId(new_id()),
        company=CompanyId(new_id()),
        certificate=CertificateId(new_id()),
    )


def der(certificate: x509.Certificate) -> bytes:
    return certificate.public_bytes(serialization.Encoding.DER)


def pkcs8(issued: Issued) -> bytes:
    return issued.key.private_bytes(
        serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )


@pytest.fixture
def backends(kms: KeyManagement, entity: CertificationEntity) -> Backends:
    """Both backends over the stand-in KMS and entity."""
    return Backends(kms, entity=entity)


async def sign(
    backends: Backends, admitted: Admitted, backend: BackendKind, context: SealContext, certificate: bytes
) -> bytes:
    return await backends.sign(
        admitted.material,
        backend=backend,
        prepared=dsig.prepare(DOCUMENT),
        certificate_der=certificate,
        seal_for=context,
    )


async def test_a_bundle_s_key_is_sealed_and_signs_once_unwrapped(
    backends: Backends, kms: CountingKms, holder: Issued
) -> None:
    context = a_context()
    admitted = await backends.admit_local(
        bundle(holder, password=PASSWORD), Secret(PASSWORD), seal_for=context
    )
    facts = admitted.facts
    assert facts.der == der(holder.certificate)
    assert facts.matches_key
    assert facts.rsa_bits == 2048
    assert pkcs8(holder) not in admitted.material.sealed
    before = kms.unwraps
    for count in (1, 2):
        signed = await sign(backends, admitted, BackendKind.LOCAL, context, facts.der)
        verified(signed, holder.certificate)
        assert kms.unwraps == before + count  # every signature unwraps its data key: no cache


async def test_a_bundle_that_does_not_open_is_refused(backends: Backends, holder: Issued) -> None:
    with pytest.raises(BundleUnreadableError):
        await backends.admit_local(bundle(holder), Secret(PASSWORD), seal_for=a_context())


async def test_a_key_that_does_not_sign_for_the_certificate_is_custody_damaged(
    backends: Backends, holder: Issued, entity_holder: Issued
) -> None:
    context = a_context()
    admitted = await backends.admit_local(
        bundle(holder, password=PASSWORD), Secret(PASSWORD), seal_for=context
    )
    with pytest.raises(dsig.SigningPairError):
        await sign(backends, admitted, BackendKind.LOCAL, context, der(entity_holder.certificate))


async def test_a_sealed_credential_taken_for_a_key_is_custody_damaged(
    backends: Backends, holder: Issued
) -> None:
    context = a_context()
    admitted = await backends.admit_remote(Secret(CREDENTIAL), seal_for=context)
    with pytest.raises(dsig.NotAnRsaKeyError) as caught:
        await sign(backends, admitted, BackendKind.LOCAL, context, der(holder.certificate))
    assert caught.value.__cause__ is None  # the parser's message stays behind


async def test_a_sealed_key_of_another_kind_is_custody_damaged(
    backends: Backends, kms: KeyManagement, holder: Issued
) -> None:
    context = a_context()
    elliptic = ec_key().private_bytes(
        serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    material = await sealed(kms, elliptic, context)
    with pytest.raises(dsig.NotAnRsaKeyError):
        await backends.sign(
            material,
            backend=BackendKind.LOCAL,
            prepared=dsig.prepare(DOCUMENT),
            certificate_der=der(holder.certificate),
            seal_for=context,
        )


async def test_a_credential_is_sealed_and_the_entity_signs_a_digest(
    backends: Backends, kms: CountingKms, entity: RecordingEntity, entity_holder: Issued
) -> None:
    context = a_context()
    admitted = await backends.admit_remote(Secret(CREDENTIAL), seal_for=context)
    facts = admitted.facts
    assert facts.der == der(entity_holder.certificate)
    assert facts.matches_key
    assert CREDENTIAL.encode() not in admitted.material.sealed
    before = kms.unwraps
    signed = await sign(backends, admitted, BackendKind.REMOTE, context, facts.der)
    verified(signed, entity_holder.certificate)
    assert kms.unwraps == before + 1
    (digest,) = entity.digests
    assert len(digest) == DIGEST_BYTES
    signed_info = dsig.prepare(DOCUMENT).to_sign
    assert digest == hashlib.sha256(signed_info).digest()  # SignedInfo's, never the document's


@pytest.mark.parametrize("failure", ["unavailable", "refusing"])
async def test_an_entity_that_does_not_serve_is_said_so(
    backends: Backends, entity: RecordingEntity, entity_holder: Issued, failure: str
) -> None:
    context = a_context()
    admitted = await backends.admit_remote(Secret(CREDENTIAL), seal_for=context)
    entity.available, entity.refusing = failure != "unavailable", failure == "refusing"
    expected = EntityUnavailableError if failure == "unavailable" else EntityRefusedError
    with pytest.raises(expected):
        await sign(backends, admitted, BackendKind.REMOTE, context, der(entity_holder.certificate))
    with pytest.raises(expected):
        await backends.admit_remote(Secret(CREDENTIAL), seal_for=a_context())


async def test_an_entity_signature_of_another_key_is_custody_damaged(
    backends: Backends, holder: Issued
) -> None:
    context = a_context()
    admitted = await backends.admit_remote(Secret(CREDENTIAL), seal_for=context)
    with pytest.raises(dsig.SigningPairError):  # dsig checks every value against the certificate
        await sign(backends, admitted, BackendKind.REMOTE, context, der(holder.certificate))


@pytest.mark.parametrize("chain", [(), (b"not a certificate",)], ids=["no certificate", "garbage"])
async def test_an_entity_answer_that_is_no_certificate_is_refused(
    kms: KeyManagement, chain: Sequence[bytes]
) -> None:
    backends = Backends(kms, entity=Answering(tuple(chain)))
    with pytest.raises(EntityAnswerInvalidError) as caught:
        await backends.admit_remote(Secret(CREDENTIAL), seal_for=a_context())
    assert caught.value.code == "entity_answer_invalid"
    assert caught.value.kind is ErrorKind.INTERNAL
    assert caught.value.__cause__ is None


async def test_without_an_entity_the_remote_backend_is_not_configured(
    kms: KeyManagement, holder: Issued
) -> None:
    backends = Backends(kms, entity=None)
    context = a_context()
    with pytest.raises(EntityNotConfiguredError) as caught:
        await backends.admit_remote(Secret(CREDENTIAL), seal_for=context)
    assert caught.value.code == "entity_not_configured"
    admitted = await backends.admit_local(
        bundle(holder, password=PASSWORD), Secret(PASSWORD), seal_for=context
    )
    with pytest.raises(EntityNotConfiguredError):
        await sign(backends, admitted, BackendKind.REMOTE, context, der(holder.certificate))


def test_custody_shows_nothing_it_holds(backends: Backends) -> None:
    assert repr(backends).startswith("<ledger_signer.adapters.custody.backends.Backends object")


def _chain(error: BaseException) -> list[BaseException]:
    """``error`` and every error it was raised from or while handling."""
    chain: list[BaseException] = []
    current: BaseException | None = error
    while current is not None and current not in chain:
        chain.append(current)
        current = current.__cause__ or current.__context__
    return chain


async def test_no_secret_leaves_custody_by_an_error_a_log_or_a_repr(
    tmp_path: Path, entity: RecordingEntity, holder: Issued
) -> None:
    """Review Focus 3: the password, credential, key, document and holder's name stay inside."""
    key_file = tmp_path / "master.key"
    key_file.write_bytes(os.urandom(MASTER_KEY_BYTES))
    backends = Backends(LocalFileKms(key_file, environment="test"), entity=entity)
    context = a_context()
    failures: list[BaseException] = []
    with capture_logs() as logs:
        admitted = await backends.admit_local(
            bundle(holder, password=PASSWORD), Secret(PASSWORD), seal_for=context
        )
        remote = await backends.admit_remote(Secret(CREDENTIAL), seal_for=a_context())
        for attempt in (
            backends.admit_local(bundle(holder, password=PASSWORD), Secret(CREDENTIAL), seal_for=context),
            backends.admit_local(PASSWORD.encode(), Secret(PASSWORD), seal_for=context),
            sign(backends, admitted, BackendKind.LOCAL, a_context(), der(holder.certificate)),
            sign(backends, admitted, BackendKind.REMOTE, context, der(holder.certificate)),
        ):
            try:
                await attempt
            except LedgerError as error:
                failures.append(error)
        entity.refusing = True
        try:
            await sign(backends, remote, BackendKind.REMOTE, context, der(holder.certificate))
        except LedgerError as error:
            failures.append(error)
    assert len(failures) == 5
    key = pkcs8(holder).hex()
    shown = [
        *(str(error) for failure in failures for error in _chain(failure)),
        *(repr(error) for failure in failures for error in _chain(failure)),
        repr(admitted),
        repr(remote),
        repr(Secret(PASSWORD)),
        str(logs),
    ]
    for secret in (PASSWORD, CREDENTIAL, "DOCUMENT-SENTINEL", "HOLDER-SENTINEL", key):
        assert not [text for text in shown if secret in text], secret
