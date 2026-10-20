"""What custody reads of a certificate for the domain to decide on: no secret, every fact ``admit`` needs.

A certificate its issuer signed with SHA-1 or MD5 may not sign: whoever can forge such a signature can make a
certificate that names anyone.
"""

from datetime import UTC, datetime, timedelta
from typing import Final

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID, SignatureAlgorithmOID

from ledger_signer.adapters.custody.facts import facts_of, is_its_key
from ledger_signer.domain.certificates import CertificateFacts
from ledger_testing.pki import distinguished_name, ec_key, rsa_key
lazy from ledger_testing.pki import Authority, Issued

NOW: Final = datetime(2026, 10, 19, 12, tzinfo=UTC)
SHA256_WITH_RSA: Final = bytes.fromhex("06092a864886f70d01010b")
"""The DER of the OID sha256WithRSAEncryption, as a certificate names its signature's algorithm (twice)."""
WEAK: Final = {
    "sha1": bytes.fromhex("06092a864886f70d010105"),
    "md5": bytes.fromhex("06092a864886f70d010104"),
}
"""The DER of the OIDs sha1WithRSAEncryption and md5WithRSAEncryption: the same length as SHA-256's."""


def signed_with(certificate: x509.Certificate, algorithm: bytes) -> x509.Certificate:
    """``certificate`` as if its issuer had signed it with ``algorithm`` (its signature no longer holds).

    cryptography refuses to sign with SHA-1 or MD5; the facts read the algorithm a certificate names, never
    its signature, so naming one is enough.
    """
    der = certificate.public_bytes(serialization.Encoding.DER)
    assert der.count(SHA256_WITH_RSA) == 2  # in the signed part and around the signature
    return x509.load_der_x509_certificate(der.replace(SHA256_WITH_RSA, algorithm))


def bare(authority: Authority, *, ca: bool | None, usage: x509.KeyUsage | None) -> x509.Certificate:
    """A certificate ``authority`` issued with the basic constraints and key usage given (or without)."""
    key = rsa_key()
    builder = (
        x509.CertificateBuilder()
        .subject_name(distinguished_name((NameOID.COMMON_NAME, "Bare")))
        .issuer_name(authority.certificate.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(NOW - timedelta(days=1))
        .not_valid_after(NOW + timedelta(days=30))
    )
    if ca is not None:
        builder = builder.add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
    if usage is not None:
        builder = builder.add_extension(usage, critical=True)
    return builder.sign(authority.issued.key, hashes.SHA256())


def test_the_facts_of_a_signing_certificate(holder: Issued) -> None:
    certificate = holder.certificate
    facts = facts_of(certificate, matches_key=True)
    assert facts == CertificateFacts(
        der=certificate.public_bytes(serialization.Encoding.DER),
        fingerprint_sha256=certificate.fingerprint(hashes.SHA256()),
        serial_number=format(certificate.serial_number, "x"),
        subject=certificate.subject.rfc4514_string(),
        issuer=certificate.issuer.rfc4514_string(),
        not_before=certificate.not_valid_before_utc,
        not_after=certificate.not_valid_after_utc,
        rsa_bits=2048,
        may_sign=True,
        is_authority=False,
        matches_key=True,
    )
    assert not facts_of(certificate, matches_key=False).matches_key


def test_an_authority_s_certificate_is_one(authority: Authority) -> None:
    facts = facts_of(authority.certificate, matches_key=True)
    assert facts.is_authority
    assert not facts.may_sign


def test_a_certificate_without_basic_constraints_is_no_authority(authority: Authority) -> None:
    signing = x509.KeyUsage(
        digital_signature=True,
        content_commitment=False,
        key_encipherment=False,
        data_encipherment=False,
        key_agreement=False,
        key_cert_sign=False,
        crl_sign=False,
        encipher_only=False,
        decipher_only=False,
    )
    facts = facts_of(bare(authority, ca=None, usage=signing), matches_key=True)
    assert not facts.is_authority
    assert facts.may_sign


def test_a_certificate_without_key_usage_may_not_sign(authority: Authority) -> None:
    facts = facts_of(bare(authority, ca=False, usage=None), matches_key=True)
    assert not facts.may_sign


def test_a_certificate_of_another_kind_of_key_has_no_rsa_size(authority: Authority) -> None:
    issued = authority.issue(distinguished_name((NameOID.COMMON_NAME, "Elliptic")), key=ec_key())
    assert facts_of(issued.certificate, matches_key=False).rsa_bits is None


@pytest.mark.parametrize("algorithm", list(WEAK.values()), ids=list(WEAK))
def test_a_certificate_signed_with_a_broken_hash_may_not_sign(holder: Issued, algorithm: bytes) -> None:
    weak = signed_with(holder.certificate, algorithm)
    assert weak.signature_algorithm_oid in {
        SignatureAlgorithmOID.RSA_WITH_SHA1,
        SignatureAlgorithmOID.RSA_WITH_MD5,
    }
    assert not facts_of(weak, matches_key=True).may_sign


def test_the_key_of_a_certificate_is_its_own(
    holder: Issued, entity_holder: Issued, authority: Authority
) -> None:
    own = holder.key
    assert isinstance(own, rsa.RSAPrivateKey)
    key = own.public_key()
    assert is_its_key(holder.certificate, key)
    assert not is_its_key(entity_holder.certificate, key)
    elliptic = authority.issue(distinguished_name((NameOID.COMMON_NAME, "Elliptic")), key=ec_key())
    assert not is_its_key(elliptic.certificate, key)
