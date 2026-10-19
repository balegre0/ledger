from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, final
lazy from collections.abc import Callable, Mapping

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.x509.oid import ExtendedKeyUsageOID

from ledger_country_do.channels.ecf_v1.adapters.signing.trust import (
    DEFAULT_SKEW,
    MAX_INTERMEDIATES,
    CounterpartyCertificateError,
    CounterpartyTrust,
    NoTrustedRootsError,
    identities,
)
from ledger_country_do.channels.ecf_v1.adapters.signing.verifier import VerifiedSignature
from ledger_country_do_testing.pki import organization, person
from ledger_testing.pki import CERTIFYING, SIGNING, Authority, rsa_key
lazy from ledger_testing.pki import Issued

AT: Final = datetime(2026, 10, 13, 13, 30, tzinfo=UTC)
BUYER_RNC: Final = "131098193"
CEDULA: Final = "40212345678"
A_YEAR_ON: Final = AT + timedelta(days=365)
DOCUMENT_SIGNING: Final = x509.ObjectIdentifier("1.3.6.1.5.5.7.3.36")


@pytest.fixture(scope="module")
def root() -> Authority:
    return Authority.root("TRUSTED ROOT", now=AT)


@pytest.fixture(scope="module")
def middle(root: Authority) -> Authority:
    return root.intermediate("TRUSTED QUALIFIED CERTIFICATES")


@pytest.fixture(scope="module")
def seal(middle: Authority) -> Issued:
    return middle.issue(organization(BUYER_RNC), not_before=AT - timedelta(days=30))


@pytest.fixture(scope="module")
def policy(root: Authority) -> CounterpartyTrust:
    return CounterpartyTrust((root.certificate,))


def signature(issued: Issued, *intermediates: x509.Certificate) -> VerifiedSignature:
    return VerifiedSignature(issued.certificate, intermediates, "AbCdEf")


def test_a_seal_that_chains_to_a_trusted_root_and_names_the_buyer_is_trusted(
    policy: CounterpartyTrust, seal: Issued, middle: Authority
) -> None:
    policy.check(signature(seal, middle.certificate), counterparty=BUYER_RNC, signed_at=AT, received_at=AT)


def test_a_person_s_certificate_naming_the_buyer_s_cedula_is_trusted(
    policy: CounterpartyTrust, middle: Authority
) -> None:
    holder = middle.issue(person(CEDULA), not_before=AT - timedelta(days=1))
    received = AT + DEFAULT_SKEW
    policy.check(
        signature(holder, middle.certificate), counterparty=CEDULA, signed_at=AT, received_at=received
    )


def test_a_certificate_a_root_issued_directly_is_trusted(policy: CounterpartyTrust, root: Authority) -> None:
    direct = root.issue(organization(BUYER_RNC), not_before=AT - timedelta(days=1))
    policy.check(signature(direct), counterparty=BUYER_RNC, signed_at=AT, received_at=AT)


def test_no_chain_without_the_intermediate(policy: CounterpartyTrust, seal: Issued) -> None:
    with pytest.raises(CounterpartyCertificateError, match="does not chain to a trusted authority"):
        policy.check(signature(seal), counterparty=BUYER_RNC, signed_at=AT, received_at=AT)


def test_a_root_the_document_carries_is_never_trusted(policy: CounterpartyTrust) -> None:
    rogue = Authority.root("ROGUE ROOT", now=AT)
    forged = rogue.issue(organization(BUYER_RNC), not_before=AT - timedelta(days=1))
    with pytest.raises(CounterpartyCertificateError, match="does not chain to a trusted authority"):
        policy.check(
            signature(forged, rogue.certificate), counterparty=BUYER_RNC, signed_at=AT, received_at=AT
        )


def test_a_self_signed_certificate_is_never_trusted(policy: CounterpartyTrust) -> None:
    rogue = Authority.root("SELF", now=AT)
    with pytest.raises(CounterpartyCertificateError):
        policy.check(signature(rogue.issued), counterparty=BUYER_RNC, signed_at=AT, received_at=AT)


@final
@dataclass(frozen=True, slots=True)
class Validity:
    """A certificate valid from ``not_before`` to ``not_after``, its document received at ``received``."""

    not_before: datetime
    not_after: datetime
    received: datetime
    reason: str


@pytest.mark.parametrize(
    "case",
    [
        Validity(
            AT + timedelta(seconds=1), AT + timedelta(days=1), AT + timedelta(minutes=1), "when it signed"
        ),
        Validity(AT - timedelta(days=1), AT - timedelta(seconds=1), AT, "when it signed"),
        Validity(AT - timedelta(days=1), AT + timedelta(minutes=1), AT + timedelta(minutes=2), "at receipt"),
    ],
    ids=["not-yet-valid", "expired", "expired-before-receipt"],
)
def test_a_certificate_must_be_valid_when_it_signed_and_at_receipt(
    policy: CounterpartyTrust, middle: Authority, case: Validity
) -> None:
    issued = middle.issue(organization(BUYER_RNC), not_before=case.not_before, not_after=case.not_after)
    with pytest.raises(CounterpartyCertificateError, match=case.reason):
        policy.check(
            signature(issued, middle.certificate),
            counterparty=BUYER_RNC,
            signed_at=AT,
            received_at=case.received,
        )


def test_a_certificate_valid_at_both_bounds_exactly_is_trusted(
    policy: CounterpartyTrust, middle: Authority
) -> None:
    received = AT + DEFAULT_SKEW
    issued = middle.issue(organization(BUYER_RNC), not_before=AT, not_after=received)
    policy.check(
        signature(issued, middle.certificate), counterparty=BUYER_RNC, signed_at=AT, received_at=received
    )


@pytest.mark.parametrize(
    "received",
    [AT + DEFAULT_SKEW + timedelta(seconds=1), AT - DEFAULT_SKEW - timedelta(seconds=1)],
    ids=["received-long-after", "dated-in-the-future"],
)
def test_a_receipt_dated_more_than_the_skew_from_its_receipt_is_refused(
    policy: CounterpartyTrust, seal: Issued, middle: Authority, received: datetime
) -> None:
    with pytest.raises(CounterpartyCertificateError, match="far from"):
        policy.check(
            signature(seal, middle.certificate), counterparty=BUYER_RNC, signed_at=AT, received_at=received
        )


def test_a_signing_certificate_that_is_an_authority_is_refused(
    policy: CounterpartyTrust, root: Authority
) -> None:
    with pytest.raises(CounterpartyCertificateError, match="certification authority"):
        policy.check(signature(root.issued), counterparty=BUYER_RNC, signed_at=AT, received_at=AT)


@pytest.mark.parametrize(
    "purposes",
    [
        (ExtendedKeyUsageOID.CLIENT_AUTH, ExtendedKeyUsageOID.EMAIL_PROTECTION),
        (DOCUMENT_SIGNING,),
        (ExtendedKeyUsageOID.ANY_EXTENDED_KEY_USAGE,),
        (ExtendedKeyUsageOID.SERVER_AUTH, ExtendedKeyUsageOID.CLIENT_AUTH),
    ],
    ids=["client-and-email", "document-signing", "any", "server-and-client"],
)
def test_an_extended_key_usage_that_allows_signing_documents_is_respected(
    policy: CounterpartyTrust, middle: Authority, purposes: tuple[x509.ObjectIdentifier, ...]
) -> None:
    issued = middle.issue(organization(BUYER_RNC), not_before=AT - timedelta(days=1), extended_usage=purposes)
    policy.check(signature(issued, middle.certificate), counterparty=BUYER_RNC, signed_at=AT, received_at=AT)


@pytest.mark.parametrize(
    "purposes",
    [
        (ExtendedKeyUsageOID.SERVER_AUTH,),
        (ExtendedKeyUsageOID.CODE_SIGNING, ExtendedKeyUsageOID.TIME_STAMPING),
    ],
    ids=["server-only", "code-and-time-stamping"],
)
def test_an_extended_key_usage_for_other_purposes_is_refused(
    policy: CounterpartyTrust, middle: Authority, purposes: tuple[x509.ObjectIdentifier, ...]
) -> None:
    issued = middle.issue(organization(BUYER_RNC), not_before=AT - timedelta(days=1), extended_usage=purposes)
    with pytest.raises(CounterpartyCertificateError, match="extended key usage"):
        policy.check(
            signature(issued, middle.certificate), counterparty=BUYER_RNC, signed_at=AT, received_at=AT
        )


def test_a_certificate_of_someone_other_than_the_counterparty_is_refused(
    policy: CounterpartyTrust, seal: Issued, middle: Authority
) -> None:
    with pytest.raises(CounterpartyCertificateError, match="not the buyer") as raised:
        policy.check(
            signature(seal, middle.certificate), counterparty="101850043", signed_at=AT, received_at=AT
        )
    assert BUYER_RNC not in str(raised.value)
    assert "101850043" not in str(raised.value)


def test_too_many_carried_certificates_are_refused_before_any_chain_is_built(
    policy: CounterpartyTrust, seal: Issued, middle: Authority
) -> None:
    carried = (middle.certificate,) * (MAX_INTERMEDIATES + 1)
    with pytest.raises(CounterpartyCertificateError, match="too many"):
        policy.check(signature(seal, *carried), counterparty=BUYER_RNC, signed_at=AT, received_at=AT)


def test_a_policy_without_roots_is_a_defect() -> None:
    with pytest.raises(NoTrustedRootsError):
        CounterpartyTrust(())


def _name(*attributes: tuple[x509.ObjectIdentifier, str]) -> x509.Name:
    return x509.Name([x509.NameAttribute(oid, value) for oid, value in attributes])


IDENTITY_CASES: Final[list[tuple[x509.Name, set[str]]]] = [
    (organization("131098193"), {"131098193"}),
    (person("40212345678"), {"40212345678"}),
    (_name((x509.NameOID.SERIAL_NUMBER, "131098193")), {"131098193"}),
    (_name((x509.NameOID.SERIAL_NUMBER, "TINDO-131098193")), {"131098193"}),
    (_name((x509.NameOID.SERIAL_NUMBER, "PNODO-40212345678")), {"40212345678"}),
    (_name((x509.NameOID.ORGANIZATION_IDENTIFIER, "NTRDO-131098193")), {"131098193"}),
    (_name((x509.NameOID.SERIAL_NUMBER, "RNCDO-131098193")), set()),
    (_name((x509.NameOID.SERIAL_NUMBER, "VATDO-131098193")), set()),
    (_name((x509.NameOID.SERIAL_NUMBER, "PASDO-40212345678")), set()),
    (_name((x509.NameOID.ORGANIZATION_IDENTIFIER, "IDCDO-40212345678")), set()),
    (_name((x509.NameOID.ORGANIZATION_IDENTIFIER, "PASDO-131098193")), set()),
    (_name((x509.NameOID.ORGANIZATION_IDENTIFIER, "VATUS-131098193")), set()),
    (_name((x509.NameOID.SERIAL_NUMBER, "402-1234567-8")), {"40212345678"}),
    (_name((x509.NameOID.SERIAL_NUMBER, "IDCDO-402-1234567-8")), {"40212345678"}),
    (
        _name(
            (x509.NameOID.SERIAL_NUMBER, "IDCDO-40212345678"),
            (x509.NameOID.ORGANIZATION_IDENTIFIER, "VATDO-131098193"),
        ),
        {"40212345678", "131098193"},
    ),
    (_name((x509.NameOID.SERIAL_NUMBER, "IDCUS-40212345678")), set()),
    (_name((x509.NameOID.SERIAL_NUMBER, "PASDO-AB123456")), set()),
    (_name((x509.NameOID.SERIAL_NUMBER, "1310981930")), set()),
    (_name((x509.NameOID.SERIAL_NUMBER, "13109819")), set()),
    (_name((x509.NameOID.SERIAL_NUMBER, "1-3-1-0-9-8-1-9-3")), set()),
    (_name((x509.NameOID.COMMON_NAME, "131098193")), set()),
    (_name((x509.NameOID.ORGANIZATION_IDENTIFIER, "131098193")), set()),
]


@pytest.mark.parametrize(("name", "expected"), IDENTITY_CASES)
def test_a_subject_names_its_rnc_or_cedula(name: x509.Name, expected: set[str]) -> None:
    assert identities(name) == frozenset(expected)


# -- The chain's own rules, as the Web PKI's authority profile checks them -----------------------------------


@final
@dataclass(frozen=True, slots=True)
class Made:
    """A certificate a test builds by hand, and its key."""

    certificate: x509.Certificate
    key: rsa.RSAPrivateKey


def _made(
    subject: x509.Name,
    issuer: Made | None,
    *,
    authority: int | None,
    until: datetime = A_YEAR_ON,
    extensions: tuple[x509.ExtensionType, ...] = (),
) -> Made:
    """A CA (``authority``: its path length) or (``None``) a signing end entity; self-signed if no issuer."""
    key = rsa_key()
    signer = key if issuer is None else issuer.key
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject if issuer is None else issuer.certificate.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(AT - timedelta(days=30))
        .not_valid_after(until)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(signer.public_key()), critical=False
        )
    )
    if authority is None:
        builder = builder.add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        builder = builder.add_extension(SIGNING, critical=True)
    else:
        builder = builder.add_extension(x509.BasicConstraints(ca=True, path_length=authority), critical=True)
        builder = builder.add_extension(CERTIFYING, critical=True)
    for extension in extensions:
        builder = builder.add_extension(extension, critical=True)
    return Made(builder.sign(signer, hashes.SHA256()), key)


def _authority(name: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, name)])


def _verified(leaf: Made, *intermediates: Made) -> VerifiedSignature:
    return VerifiedSignature(leaf.certificate, tuple(each.certificate for each in intermediates), "AbCdEf")


def test_a_chain_built_by_hand_is_trusted_as_the_test_authority_s() -> None:
    root = _made(_authority("HAND ROOT"), None, authority=1)
    middle = _made(_authority("HAND MIDDLE"), root, authority=0)
    leaf = _made(organization(BUYER_RNC), middle, authority=None)
    trust = CounterpartyTrust((root.certificate,))
    trust.check(_verified(leaf, middle), counterparty=BUYER_RNC, signed_at=AT, received_at=AT)


def test_an_intermediate_expired_when_the_document_was_signed_breaks_the_chain() -> None:
    root = _made(_authority("HAND ROOT"), None, authority=1)
    middle = _made(_authority("HAND MIDDLE"), root, authority=0, until=AT - timedelta(seconds=1))
    leaf = _made(organization(BUYER_RNC), middle, authority=None)
    with pytest.raises(CounterpartyCertificateError, match="trusted authority"):
        CounterpartyTrust((root.certificate,)).check(
            _verified(leaf, middle), counterparty=BUYER_RNC, signed_at=AT, received_at=AT
        )


def test_an_authority_beyond_its_issuer_s_path_length_breaks_the_chain() -> None:
    root = _made(_authority("HAND ROOT"), None, authority=0)
    middle = _made(_authority("HAND MIDDLE"), root, authority=0)
    leaf = _made(organization(BUYER_RNC), middle, authority=None)
    with pytest.raises(CounterpartyCertificateError, match="trusted authority"):
        CounterpartyTrust((root.certificate,)).check(
            _verified(leaf, middle), counterparty=BUYER_RNC, signed_at=AT, received_at=AT
        )


def test_a_name_an_authority_excludes_breaks_the_chain() -> None:
    root = _made(_authority("HAND ROOT"), None, authority=1)
    excluded = x509.NameConstraints(permitted_subtrees=None, excluded_subtrees=[x509.DNSName("example.do")])
    middle = _made(_authority("HAND MIDDLE"), root, authority=0, extensions=(excluded,))
    named = x509.SubjectAlternativeName([x509.DNSName("buyer.example.do")])
    leaf = _made(organization(BUYER_RNC), middle, authority=None, extensions=(named,))
    with pytest.raises(CounterpartyCertificateError, match="trusted authority"):
        CounterpartyTrust((root.certificate,)).check(
            _verified(leaf, middle), counterparty=BUYER_RNC, signed_at=AT, received_at=AT
        )


SHA256_WITH_RSA: Final = bytes.fromhex("06092a864886f70d01010b")
SHA1_WITH_RSA: Final = bytes.fromhex("06092a864886f70d010105")


def _header(der: bytes, at: int) -> tuple[int, int]:
    """A DER element's header length and content length, at ``at``."""
    first = der[at + 1]
    if first < 0x80:
        return 2, first
    count = first & 0x7F
    return 2 + count, int.from_bytes(der[at + 2 : at + 2 + count])


LEGACY_DIGESTS: Final[Mapping[str, Callable[[], hashes.HashAlgorithm]]] = {"sha1": hashes.SHA1}
"""Digests a test uses on purpose to prove they are refused, never to protect anything; named here once."""


def _sha1_signed(made: Made, issuer: Made) -> Made:
    """``made`` as an SHA-1 signature by ``issuer`` would have it: cryptography no longer signs so."""
    der = made.certificate.public_bytes(Encoding.DER).replace(SHA256_WITH_RSA, SHA1_WITH_RSA)
    outer, _ = _header(der, 0)
    head, length = _header(der, outer)
    signed = issuer.key.sign(der[outer : outer + head + length], padding.PKCS1v15(), LEGACY_DIGESTS["sha1"]())
    return Made(x509.load_der_x509_certificate(der[: -len(signed)] + signed), made.key)


def test_a_certificate_signed_with_sha_1_breaks_the_chain() -> None:
    root = _made(_authority("HAND ROOT"), None, authority=1)
    middle = _made(_authority("HAND MIDDLE"), root, authority=0)
    leaf = _sha1_signed(_made(organization(BUYER_RNC), middle, authority=None), middle)
    certificate = leaf.certificate
    digest = certificate.signature_hash_algorithm
    assert digest is not None
    assert digest.name == "sha1"
    issuer_key = middle.certificate.public_key()
    assert isinstance(issuer_key, rsa.RSAPublicKey)
    issuer_key.verify(certificate.signature, certificate.tbs_certificate_bytes, padding.PKCS1v15(), digest)
    with pytest.raises(CounterpartyCertificateError, match="trusted authority"):
        CounterpartyTrust((root.certificate,)).check(
            _verified(leaf, middle), counterparty=BUYER_RNC, signed_at=AT, received_at=AT
        )
