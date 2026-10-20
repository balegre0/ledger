"""What custody reads of a certificate for the domain to decide on: no secret, every fact ``admit`` needs.

A certificate its issuer signed with anything but a SHA-2 hash (SHA-1, MD5, MD4, MD2, or PSS over another
hash) may not sign: whoever can forge such a signature can make a certificate that names anyone.
"""

from datetime import UTC, datetime, timedelta
from typing import Final

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
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
    "md4": bytes.fromhex("06092a864886f70d010103"),
    "md2": bytes.fromhex("06092a864886f70d010102"),
}
"""The DER of the OIDs of RSA with SHA-1, MD5, MD4 and MD2: the same length as SHA-256's."""
SHA256: Final = bytes.fromhex("0609608648016503040201")
"""The DER of the OID of SHA-256, as RSASSA-PSS's parameters name it (its hash and MGF1's)."""
PSS_HASHES: Final = {"sha3-256": SHA256[:-1] + b"\b", "unknown": SHA256[:-1] + b"c"}
"""The same OID's arc ending in 8 (SHA3-256, not SHA-2) and in 99 (no hash at all)."""


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
    assert weak.signature_algorithm_oid not in {
        SignatureAlgorithmOID.RSA_WITH_SHA256,
        SignatureAlgorithmOID.RSASSA_PSS,
    }
    assert not facts_of(weak, matches_key=True).may_sign


def pss_signed(authority: Authority, holder: Issued) -> x509.Certificate:
    """``holder``'s certificate as ``authority`` would issue it signed with RSASSA-PSS over SHA-256."""
    certificate = holder.certificate
    return (
        x509.CertificateBuilder()
        .subject_name(certificate.subject)
        .issuer_name(authority.certificate.subject)
        .public_key(certificate.public_key())
        .serial_number(certificate.serial_number)
        .not_valid_before(certificate.not_valid_before_utc)
        .not_valid_after(certificate.not_valid_after_utc)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(certificate.extensions.get_extension_for_class(x509.KeyUsage).value, critical=True)
        .sign(
            authority.issued.key,
            hashes.SHA256(),
            rsa_padding=padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32),
        )
    )


def test_a_certificate_signed_with_pss_over_sha_256_may_sign(authority: Authority, holder: Issued) -> None:
    assert facts_of(pss_signed(authority, holder), matches_key=True).may_sign


@pytest.mark.parametrize("hash_oid", list(PSS_HASHES.values()), ids=list(PSS_HASHES))
def test_a_certificate_signed_with_pss_over_another_hash_may_not_sign(
    authority: Authority, holder: Issued, hash_oid: bytes
) -> None:
    der = pss_signed(authority, holder).public_bytes(serialization.Encoding.DER)
    assert der.count(SHA256) == 4  # PSS's hash and MGF1's, in the signed part and around the signature
    other = x509.load_der_x509_certificate(der.replace(SHA256, hash_oid))
    assert not facts_of(other, matches_key=True).may_sign


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
