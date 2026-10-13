"""Certificates for tests: authorities of the test's own, and the PKCS#12 bundles a company holds.

A test makes its own hierarchy (``Authority.root``, then ``intermediate``), so no test depends on a real
authority. Subjects follow the Dominican certification authorities' conventions: a natural person's
tax-procedure certificate names its cédula in ``serialNumber`` (``IDCDO-<cédula>``), an organization's seal
its RNC in ``organizationIdentifier`` (``VATDO-<rnc>``). DGII's TesteCF takes only certificates of authorized
entities: these serve tests and the fake DGII, never DGII.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, final
lazy from collections.abc import Sequence

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.hazmat.primitives.serialization import BestAvailableEncryption, Encoding, pkcs12
from cryptography.x509.oid import NameOID

OPENING_PHRASE: Final = "it's the bundle's password"
"""What opens every bundle a test makes, unless it names another password."""
DEFAULT_BITS: Final = 2048
TAX_PROCEDURES: Final = "QUALIFIED CERTIFICATE FOR NATURAL PERSON - TAX PROCEDURES"
SIGNING: Final = x509.KeyUsage(
    digital_signature=True,
    content_commitment=True,
    key_encipherment=True,
    data_encipherment=False,
    key_agreement=False,
    key_cert_sign=False,
    crl_sign=False,
    encipher_only=False,
    decipher_only=False,
)
_CERTIFYING: Final = x509.KeyUsage(
    digital_signature=False,
    content_commitment=False,
    key_encipherment=False,
    data_encipherment=False,
    key_agreement=False,
    key_cert_sign=True,
    crl_sign=True,
    encipher_only=False,
    decipher_only=False,
)
_AUTHORITY_YEARS: Final = timedelta(days=3650)
_LEAF_DAYS: Final = timedelta(days=365)
_BACKDATED: Final = timedelta(days=1)

type PrivateKey = rsa.RSAPrivateKey | ec.EllipticCurvePrivateKey
"""RSA, which DGII's profile signs with; P-256, which it refuses."""


def rsa_key(bits: int = DEFAULT_BITS) -> rsa.RSAPrivateKey:
    """A new RSA key (tens of milliseconds at 2048 bits: tests that need many make them once)."""
    return rsa.generate_private_key(public_exponent=65537, key_size=bits)


def ec_key() -> ec.EllipticCurvePrivateKey:
    """A new P-256 key: one DGII's profile refuses."""
    return ec.generate_private_key(ec.SECP256R1())


def _name(*attributes: tuple[x509.ObjectIdentifier, str]) -> x509.Name:
    return x509.Name([x509.NameAttribute(oid, value) for oid, value in attributes])


def person(cedula: str = "40212345678", *, name: str = "JUAN PEREZ") -> x509.Name:
    """A natural person's certificate for tax procedures: ``serialNumber`` ``IDCDO-<cedula>``."""
    given, _, family = name.partition(" ")
    return _name(
        (NameOID.COUNTRY_NAME, "DO"),
        (NameOID.DN_QUALIFIER, TAX_PROCEDURES),
        (NameOID.COMMON_NAME, name),
        (NameOID.SERIAL_NUMBER, f"IDCDO-{cedula}"),
        (NameOID.GIVEN_NAME, given),
        (NameOID.SURNAME, family or given),
    )


def organization(rnc: str = "101850043", *, name: str = "FERRETERIA DUARTE SRL") -> x509.Name:
    """An organization's electronic seal: ``organizationIdentifier`` ``VATDO-<rnc>``."""
    return _name(
        (NameOID.COUNTRY_NAME, "DO"),
        (NameOID.ORGANIZATION_NAME, name),
        (NameOID.COMMON_NAME, name),
        (NameOID.ORGANIZATION_IDENTIFIER, f"VATDO-{rnc}"),
    )


@final
@dataclass(frozen=True, slots=True)
class Issued:
    """A certificate and its private key."""

    certificate: x509.Certificate
    key: PrivateKey

    def pem(self) -> bytes:
        """The certificate in PEM."""
        return self.certificate.public_bytes(Encoding.PEM)


def _authority_builder(
    subject: x509.Name, issuer: x509.Name, key: PrivateKey, issuer_key: PrivateKey, *, path_length: int
) -> x509.CertificateBuilder:
    return (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .add_extension(x509.BasicConstraints(ca=True, path_length=path_length), critical=True)
        .add_extension(_CERTIFYING, critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer_key.public_key()), critical=False
        )
    )


@final
class Authority:
    """A certification authority of a test's own: a root, or an intermediate a root issued."""

    __slots__ = ("issued",)

    def __init__(self, issued: Issued) -> None:
        self.issued: Issued = issued

    @property
    def certificate(self) -> x509.Certificate:
        """The authority's own certificate."""
        return self.issued.certificate

    @classmethod
    def root(cls, name: str = "LEDGER TEST ROOT CA", *, now: datetime | None = None) -> Authority:
        """A self-signed root, valid from a day before ``now`` for ten years."""
        key = rsa_key()
        subject = _name((NameOID.COUNTRY_NAME, "DO"), (NameOID.COMMON_NAME, name))
        moment = datetime.now(UTC) if now is None else now
        certificate = (
            _authority_builder(subject, subject, key, key, path_length=1)
            .not_valid_before(moment - _BACKDATED)
            .not_valid_after(moment + _AUTHORITY_YEARS)
            .sign(key, hashes.SHA256())
        )
        return cls(Issued(certificate, key))

    def intermediate(self, name: str = "LEDGER TEST QUALIFIED CERTIFICATES") -> Authority:
        """An authority this one issued, which issues end-entity certificates only."""
        key = rsa_key()
        subject = _name((NameOID.COUNTRY_NAME, "DO"), (NameOID.COMMON_NAME, name))
        certificate = (
            _authority_builder(subject, self.certificate.subject, key, self.issued.key, path_length=0)
            .not_valid_before(self.certificate.not_valid_before_utc)
            .not_valid_after(self.certificate.not_valid_after_utc)
            .sign(self.issued.key, hashes.SHA256())
        )
        return Authority(Issued(certificate, key))

    def issue(
        self,
        subject: x509.Name,
        *,
        key: PrivateKey | None = None,
        not_before: datetime | None = None,
        not_after: datetime | None = None,
        extended_usage: Sequence[x509.ObjectIdentifier] = (),
    ) -> Issued:
        """An end-entity signing certificate for ``subject``.

        Valid from a day ago for a year unless ``not_before`` or ``not_after`` say otherwise; with an extended
        key usage extension naming ``extended_usage`` when it names any.
        """
        own = rsa_key() if key is None else key
        now = datetime.now(UTC)
        builder = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(self.certificate.subject)
            .public_key(own.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - _BACKDATED if not_before is None else not_before)
            .not_valid_after(now + _LEAF_DAYS if not_after is None else not_after)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(SIGNING, critical=True)
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(self.issued.key.public_key()),
                critical=False,
            )
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(own.public_key()), critical=False)
        )
        if extended_usage:
            builder = builder.add_extension(x509.ExtendedKeyUsage(extended_usage), critical=False)
        return Issued(builder.sign(self.issued.key, hashes.SHA256()), own)


def bundle(
    issued: Issued, *, chain: Sequence[x509.Certificate] = (), password: str = OPENING_PHRASE
) -> bytes:
    """``issued`` as the PKCS#12 file a company holds, with ``chain``."""
    return pkcs12.serialize_key_and_certificates(
        b"ledger test",
        issued.key,
        issued.certificate,
        list(chain) or None,
        BestAvailableEncryption(password.encode()),
    )


def pem(*certificates: x509.Certificate) -> bytes:
    """``certificates`` one after the other in PEM: a trust store's file."""
    return b"".join(certificate.public_bytes(Encoding.PEM) for certificate in certificates)
