"""The inbound profile: what counterparties' signers add to DGII's shape, tolerated or checked."""

import base64
from dataclasses import dataclass
from typing import Final, final
lazy from collections.abc import Callable

import pytest
from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding
lazy from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey

from ledger_country_do.channels.ecf_v1.adapters.signing.verifier import (
    INBOUND,
    STRICT,
    SignatureInvalidError,
    verify_enveloped,
)
from ledger_country_do_testing.pki import Authority, organization, person, rsa_key
from ledger_country_do_testing.signatures import SHAPES, forge

ECF: Final = SHAPES["ecf"]


@final
@dataclass(frozen=True, slots=True)
class Signer:
    """A counterparty: its key, its certificate, and the intermediate that issued it."""

    key: RSAPrivateKey
    certificate: x509.Certificate
    intermediate: x509.Certificate


@pytest.fixture(scope="module")
def counterparty(intermediate: Authority) -> Signer:
    key = rsa_key()
    return Signer(key, intermediate.issue(organization(), key=key).certificate, intermediate.certificate)


def _der(certificate: x509.Certificate) -> str:
    return base64.b64encode(certificate.public_bytes(Encoding.DER)).decode()


def _b64_int(value: int) -> str:
    return base64.b64encode(value.to_bytes((value.bit_length() + 7) // 8)).decode()


def _key_value(key: RSAPrivateKey) -> str:
    numbers = key.public_key().public_numbers()
    return (
        f"<KeyValue><RSAKeyValue><Modulus>{_b64_int(numbers.n)}</Modulus>"
        f"<Exponent>{_b64_int(numbers.e)}</Exponent></RSAKeyValue></KeyValue>"
    )


def _ski(certificate: x509.Certificate) -> str:
    digest = certificate.extensions.get_extension_for_class(x509.SubjectKeyIdentifier).value.digest
    return base64.b64encode(digest).decode()


def _issuer_serial(issuer: str, serial: int | str) -> str:
    return (
        f"<X509IssuerSerial><X509IssuerName>{issuer}</X509IssuerName>"
        f"<X509SerialNumber>{serial}</X509SerialNumber></X509IssuerSerial>"
    )


type Edit = Callable[[Signer], Callable[[str], str]]


def _replace(old: str, new: str) -> Edit:
    return lambda _: lambda text: text.replace(old, new, 1)


def _in_x509_data(make: Callable[[Signer], str]) -> Edit:
    return lambda signer: lambda text: text.replace("</X509Data>", make(signer) + "</X509Data>", 1)


def _in_key_info(make: Callable[[Signer], str]) -> Edit:
    return lambda signer: lambda text: text.replace("</KeyInfo>", make(signer) + "</KeyInfo>", 1)


def _chain_first(signer: Signer) -> Callable[[str], str]:
    return lambda text: text.replace(
        "<X509Data>", f"<X509Data><X509Certificate>{_der(signer.intermediate)}</X509Certificate>", 1
    )


TOLERATED: Final[dict[str, Edit]] = {
    "id-signature": _replace("<Signature ", '<Signature Id="s" '),
    "id-signed-info": _replace("<SignedInfo>", '<SignedInfo Id="i">'),
    "id-reference": _replace('<Reference URI=""', '<Reference Id="r" URI=""'),
    "id-signature-value": _replace("<SignatureValue>", '<SignatureValue Id="v">'),
    "id-key-info": _replace("<KeyInfo>", '<KeyInfo Id="k">'),
    "id-x509-data": _replace("<X509Data>", '<X509Data Id="d">'),
    "key-name": _in_key_info(lambda _: "<KeyName>FERRETERIA DUARTE</KeyName>"),
    "key-value": _in_key_info(lambda signer: _key_value(signer.key)),
    "issuer-serial": _in_x509_data(
        lambda signer: _issuer_serial(
            signer.certificate.issuer.rfc4514_string(), signer.certificate.serial_number
        )
    ),
    "subject-name": _in_x509_data(
        lambda signer: f"<X509SubjectName>{signer.certificate.subject.rfc4514_string()}</X509SubjectName>"
    ),
    "ski": _in_x509_data(lambda signer: f"<X509SKI>{_ski(signer.certificate)}</X509SKI>"),
}


@pytest.mark.parametrize("variation", list(TOLERATED))
def test_what_common_signers_add_is_tolerated_inbound_and_refused_strictly(
    counterparty: Signer, variation: str
) -> None:
    signed = forge(ECF, counterparty.key, counterparty.certificate, edit=TOLERATED[variation](counterparty))
    verified = verify_enveloped(signed, INBOUND)
    assert verified.certificate == counterparty.certificate
    assert verified.intermediates == ()
    with pytest.raises(SignatureInvalidError, match="not DGII's profile"):
        verify_enveloped(signed, STRICT)


@pytest.mark.parametrize("order", ["intermediate-first", "leaf-first"])
def test_a_chain_in_x509_data_gives_its_leaf_and_untrusted_intermediates(
    counterparty: Signer, order: str
) -> None:
    edit = (
        _chain_first(counterparty)
        if order == "intermediate-first"
        else _in_x509_data(lambda signer: f"<X509Certificate>{_der(signer.intermediate)}</X509Certificate>")(
            counterparty
        )
    )
    signed = forge(ECF, counterparty.key, counterparty.certificate, edit=edit)
    verified = verify_enveloped(signed, INBOUND)
    assert verified.certificate == counterparty.certificate
    assert verified.intermediates == (counterparty.intermediate,)
    with pytest.raises(SignatureInvalidError, match="not DGII's profile"):
        verify_enveloped(signed, STRICT)


def test_whitespace_between_elements_is_tolerated_by_both(counterparty: Signer) -> None:
    edit = _replace("<SignedInfo>", "\n  <SignedInfo>\n    ")(counterparty)
    signed = forge(ECF, counterparty.key, counterparty.certificate, edit=edit)
    assert verify_enveloped(signed, STRICT).certificate == verify_enveloped(signed, INBOUND).certificate


def _text(value: str | bytes) -> str:
    assert isinstance(value, str)
    return value


def _dotnet(name: x509.Name) -> str:
    """``name`` as .NET writes it: most specific first, ", " between parts, OID. where it has no label."""
    labels = {"2.5.4.3": "CN", "2.5.4.6": "C", "2.5.4.10": "O", "2.5.4.5": "SERIALNUMBER"}
    return ", ".join(
        f"{labels.get(oid, 'OID.' + oid)}={_text(attribute.value)}"
        for attribute in reversed(list(name))
        if (oid := attribute.oid.dotted_string)
    )


def _java(name: x509.Name) -> str:
    """``name`` as Java writes it: OID.x.y.z for every type, no space after commas."""
    return ",".join(
        f"OID.{attribute.oid.dotted_string}={_text(attribute.value)}" for attribute in reversed(list(name))
    )


@pytest.mark.parametrize("style", ["dotnet", "java"])
def test_names_written_by_dotnet_or_java_name_the_certificate(counterparty: Signer, style: str) -> None:
    write = _dotnet if style == "dotnet" else _java
    certificate = counterparty.certificate

    def edit(text: str) -> str:
        claims = _issuer_serial(write(certificate.issuer), certificate.serial_number) + (
            f"<X509SubjectName>{write(certificate.subject)}</X509SubjectName>"
        )
        return text.replace("</X509Data>", claims + "</X509Data>", 1)

    signed = forge(ECF, counterparty.key, certificate, edit=edit)
    assert verify_enveloped(signed, INBOUND).certificate == certificate


def test_a_name_that_cannot_be_read_is_informational_only(counterparty: Signer) -> None:
    certificate = counterparty.certificate

    def edit(text: str) -> str:
        claims = (
            _issuer_serial("not a name", certificate.serial_number) + "<X509SubjectName>?</X509SubjectName>"
        )
        return text.replace("</X509Data>", claims + "</X509Data>", 1)

    assert (
        verify_enveloped(forge(ECF, counterparty.key, certificate, edit=edit), INBOUND).certificate
        == certificate
    )


def test_a_readable_name_with_another_serial_is_refused(counterparty: Signer) -> None:
    certificate = counterparty.certificate
    edit = _in_x509_data(lambda _: _issuer_serial(_dotnet(certificate.issuer), certificate.serial_number + 1))
    with pytest.raises(SignatureInvalidError, match="names another certificate or key"):
        verify_enveloped(forge(ECF, counterparty.key, certificate, edit=edit(counterparty)), INBOUND)


def test_everything_tolerated_at_once(counterparty: Signer) -> None:
    signed = forge(ECF, counterparty.key, counterparty.certificate, edit=_all(counterparty))
    assert verify_enveloped(signed, INBOUND).intermediates == (counterparty.intermediate,)


def _all(signer: Signer) -> Callable[[str], str]:
    def edit(text: str) -> str:
        text = _chain_first(signer)(text)
        for variation in TOLERATED.values():
            text = variation(signer)(text)
        return text

    return edit


def _stranger_leaf(_: Signer) -> str:
    stranger = Authority.root().issue(person())
    return f"<X509Certificate>{_der(stranger.certificate)}</X509Certificate>"


def _authority_only(signer: Signer) -> Callable[[str], str]:
    return lambda text: text.replace(_der(signer.certificate), _der(signer.intermediate), 1)


REFUSED: Final[dict[str, tuple[Edit, str]]] = {
    "two-leaves": (_in_x509_data(_stranger_leaf), "no single signing certificate"),
    "same-leaf-twice": (
        _in_x509_data(lambda signer: f"<X509Certificate>{_der(signer.certificate)}</X509Certificate>"),
        "no single signing certificate",
    ),
    "authority-signs": (_authority_only, "certification authority"),
    "other-key-value": (_in_key_info(lambda _: _key_value(rsa_key())), "names another certificate or key"),
    "key-value-not-base64": (
        _in_key_info(
            lambda _: (
                "<KeyValue><RSAKeyValue><Modulus>!</Modulus><Exponent>AQAB</Exponent>"
                "</RSAKeyValue></KeyValue>"
            )
        ),
        "names another certificate or key",
    ),
    "other-serial": (
        _in_x509_data(
            lambda signer: _issuer_serial(
                signer.certificate.issuer.rfc4514_string(), signer.certificate.serial_number + 1
            )
        ),
        "names another certificate or key",
    ),
    "other-issuer": (
        _in_x509_data(lambda signer: _issuer_serial("CN=SOMEONE ELSE", signer.certificate.serial_number)),
        "names another certificate or key",
    ),
    "serial-not-a-number": (
        _in_x509_data(lambda signer: _issuer_serial(signer.certificate.issuer.rfc4514_string(), "12a")),
        "names another certificate or key",
    ),
    "other-subject": (
        _in_x509_data(lambda _: "<X509SubjectName>CN=SOMEONE ELSE</X509SubjectName>"),
        "names another certificate or key",
    ),
    "other-ski": (
        _in_x509_data(lambda _: f"<X509SKI>{base64.b64encode(bytes(20)).decode()}</X509SKI>"),
        "names another certificate or key",
    ),
    "two-key-values": (
        _in_key_info(lambda signer: _key_value(signer.key) * 2),
        "not DGII's profile",
    ),
    "two-key-names": (
        _in_key_info(lambda _: "<KeyName>a</KeyName><KeyName>b</KeyName>"),
        "not DGII's profile",
    ),
    "two-x509-data": (_in_key_info(lambda _: "<X509Data/>"), "not DGII's profile"),
    "two-subject-names": (
        _in_x509_data(lambda _: "<X509SubjectName>CN=A</X509SubjectName>" * 2),
        "not DGII's profile",
    ),
    "id-on-transform": (_replace("<Transform ", '<Transform Id="t" '), "not DGII's profile"),
    "id-on-digest-method": (_replace("<DigestMethod ", '<DigestMethod Id="t" '), "not DGII's profile"),
    "retrieval-method": (_in_key_info(lambda _: '<RetrievalMethod URI="#k"/>'), "not DGII's profile"),
    "x509-digest": (_in_x509_data(lambda _: "<X509Digest>AAAA</X509Digest>"), "not DGII's profile"),
    "key-value-empty": (_in_key_info(lambda _: "<KeyValue/>"), "not DGII's profile"),
}


@pytest.mark.parametrize("variation", list(REFUSED))
def test_what_names_another_key_or_strays_from_the_profile_is_refused_inbound(
    counterparty: Signer, variation: str
) -> None:
    edit, reason = REFUSED[variation]
    signed = forge(ECF, counterparty.key, counterparty.certificate, edit=edit(counterparty))
    with pytest.raises(SignatureInvalidError, match=reason):
        verify_enveloped(signed, INBOUND)
