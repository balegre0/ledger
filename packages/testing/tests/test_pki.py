from datetime import UTC, datetime, timedelta
from typing import Final

import pytest
from cryptography import x509
from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePrivateKey
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from cryptography.hazmat.primitives.serialization import load_pem_private_key, pkcs12
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from ledger_testing.pki import (
    DEFAULT_BITS,
    OPENING_PHRASE,
    SIGNING,
    Authority,
    bundle,
    distinguished_name,
    ec_key,
    pem,
)

SUBJECT: Final = distinguished_name((NameOID.COUNTRY_NAME, "DO"), (NameOID.COMMON_NAME, "LEDGER TEST SIGNER"))


def test_a_name_keeps_its_attributes_in_order() -> None:
    assert [attribute.oid for attribute in SUBJECT] == [NameOID.COUNTRY_NAME, NameOID.COMMON_NAME]
    assert SUBJECT.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == "LEDGER TEST SIGNER"


def test_a_hierarchy_issues_certificates_that_bundle_and_open_again() -> None:
    root = Authority.root()
    intermediate = root.intermediate()
    issued = intermediate.issue(SUBJECT)
    loaded = pkcs12.load_pkcs12(bundle(issued, chain=[intermediate.certificate]), OPENING_PHRASE.encode())
    assert loaded.cert is not None
    assert loaded.cert.certificate == issued.certificate
    assert [extra.certificate for extra in loaded.additional_certs] == [intermediate.certificate]
    assert issued.certificate.issuer == intermediate.certificate.subject
    assert pem(root.certificate).startswith(b"-----BEGIN CERTIFICATE-----")
    assert issued.pem() == pem(issued.certificate)
    assert pkcs12.load_pkcs12(bundle(issued), OPENING_PHRASE.encode()).additional_certs == []
    assert isinstance(issued.key, RSAPrivateKey)
    assert issued.key.key_size == DEFAULT_BITS
    assert issued.certificate.extensions.get_extension_for_class(x509.KeyUsage).value == SIGNING


def test_a_test_breaks_what_it_needs() -> None:
    root = Authority.root(now=datetime.now(UTC))
    past = datetime.now(UTC) - timedelta(days=10)
    issued = root.issue(SUBJECT, key=ec_key(), not_before=past - timedelta(days=1), not_after=past)
    assert isinstance(issued.key, EllipticCurvePrivateKey)
    assert issued.certificate.not_valid_after_utc == past.replace(microsecond=0)


def test_an_extended_key_usage_is_written_only_when_asked_for() -> None:
    root = Authority.root(now=datetime.now(UTC))
    usages = (ExtendedKeyUsageOID.EMAIL_PROTECTION,)
    usage = root.issue(SUBJECT, extended_usage=usages).certificate.extensions
    assert list(usage.get_extension_for_class(x509.ExtendedKeyUsage).value) == list(usages)
    plain = root.issue(SUBJECT).certificate.extensions
    with pytest.raises(x509.ExtensionNotFound):
        plain.get_extension_for_class(x509.ExtendedKeyUsage)


def test_a_server_certificate_names_its_hosts_and_its_key_reads_back() -> None:
    issued = Authority.root().server("party.test", "127.0.0.1")
    assert issued.certificate.subject.rfc4514_string() == "CN=party.test"
    usage = issued.certificate.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    assert list(usage) == [ExtendedKeyUsageOID.SERVER_AUTH]
    names = issued.certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert names.get_values_for_type(x509.DNSName) == ["party.test"]
    assert [str(address) for address in names.get_values_for_type(x509.IPAddress)] == ["127.0.0.1"]
    key = load_pem_private_key(issued.key_pem(), password=None)
    assert key.public_key() == issued.key.public_key()
    unnamed = Authority.root().issue(SUBJECT)
    with pytest.raises(x509.ExtensionNotFound):
        unnamed.certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName)
