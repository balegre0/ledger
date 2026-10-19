"""Whether a counterparty's signing certificate is one to trust: the policy an inbound signature answers to.

``verify_enveloped`` (``INBOUND``) says a signature holds and by which certificate; this says whether that
certificate may speak for the counterparty (a buyer signing its ARECF):

- **Chain.** It chains to one of the configured roots (DGII- and INDOTEL-authorized authorities), through the
  intermediates the document carries, which are never trusted themselves: a root the document carries is just
  another certificate. The chain is built and checked by ``cryptography.x509.verification`` with the Web PKI's
  rules for authorities (basic constraints, key usage, path length, name constraints, RSA of 2048 bits and
  more) and none of its rules for the end entity (a signing certificate names no host).
- **Time.** The certificate, and its whole chain, is valid at the moment the document says it was signed and
  at the moment it was received here, which may differ by ``skew`` at most either way (clocks differ; a
  receipt dated an hour away is not this exchange's).
- **Usage.** It may sign (fiscal-kit's ``dsig.signing_problem``: an end entity, ``digitalSignature`` or
  ``nonRepudiation``); an extended key usage, where present, names one of ``purposes`` (document signing,
  e-mail protection, client authentication as qualified certificates write them, or any).
- **Identity.** Its subject names the counterparty: an RNC or cédula in ``serialNumber`` (bare, or
  ``IDCDO-``/``TINDO-``/``PNODO-``) or ``organizationIdentifier`` (``VATDO-``/``NTRDO-``), as ``identities``
  reads them; no other prefix counts.

**Revocation is not checked** (no CRL, no OCSP): a known gap of the spike, recorded in its findings, for
v2-M4's delivery. A refusal says why without quoting the certificate, the document or a tax id.
"""

import re
from dataclasses import dataclass, field
from datetime import UTC, timedelta
from typing import ClassVar, Final, final
lazy from collections.abc import Sequence
lazy from datetime import datetime

from cryptography import x509
from cryptography.x509 import verification
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from ledger_country_do.channels.ecf_v1.domain.errors import EcfError, EcfInvalidError
from ledger_country_do.regime.tax_ids import normalize, scheme_of
from ledger_fiscal_kit.xml.dsig import signing_problem
lazy from ledger_country_do.channels.ecf_v1.adapters.signing.verifier import VerifiedSignature

DEFAULT_SKEW: Final = timedelta(minutes=5)
MAX_INTERMEDIATES: Final = 8
"""The most certificates a document may carry beside the signing one: no path is built from more."""
DOCUMENT_SIGNING_PURPOSES: Final = frozenset(
    {
        ExtendedKeyUsageOID.ANY_EXTENDED_KEY_USAGE,
        ExtendedKeyUsageOID.CLIENT_AUTH,
        ExtendedKeyUsageOID.EMAIL_PROTECTION,
        x509.ObjectIdentifier("1.3.6.1.5.5.7.3.36"),
        x509.ObjectIdentifier("1.3.6.1.4.1.311.10.3.12"),
        x509.ObjectIdentifier("1.2.840.113583.1.1.5"),
    }
)
"""Any purpose; client authentication and e-mail protection, which qualified certificates for persons and
seals carry; document signing (RFC 9336, Microsoft's, Adobe's)."""
_NUMBER: Final = r"([0-9]{9}|[0-9]{11}|[0-9]{3}-[0-9]{7}-[0-9]|[0-9]-[0-9]{2}-[0-9]{5}-[0-9])"
"""How an RNC (``131098193``, ``1-31-09819-3``) or a cédula (``40212345678``, ``402-1234567-8``) is spelled
in a certificate; the number itself is read by the regime's ``tax_ids`` (``normalize``, ``scheme_of``)."""
_SERIAL_NUMBER: Final = re.compile(rf"(?:(?:IDC|TIN|PNO)DO-)?{_NUMBER}")
"""``serialNumber``: bare, or after ETSI EN 319 412-1's natural-person prefixes for the Dominican Republic
(national identity card, tax identification number, national personal number)."""
_ORGANIZATION_IDENTIFIER: Final = re.compile(rf"(?:VAT|NTR)DO-{_NUMBER}")
"""``organizationIdentifier``: always prefixed, by legal persons' prefixes (VAT, national trade register)."""
_READERS: Final = {
    NameOID.SERIAL_NUMBER: _SERIAL_NUMBER,
    NameOID.ORGANIZATION_IDENTIFIER: _ORGANIZATION_IDENTIFIER,
}
_UNTRUSTED: Final = "it does not chain to a trusted authority"
_TOO_MANY: Final = "the document carries too many certificates"
_FAR: Final = "the document is dated too far from its receipt"
_SIGNED: Final = "it was not valid when it signed"
_RECEIVED: Final = "it is not valid at receipt"
_PURPOSE: Final = "its extended key usage does not allow signing documents"
_NOT_THEM: Final = "it is not the buyer's: its subject names another RNC or cédula, or none"


class CounterpartyCertificateError(EcfInvalidError):
    """A signature that holds, by a certificate this policy does not trust; the reason quotes nothing."""

    code: ClassVar[str] = "do_ecf_counterparty_certificate_refused"

    def __init__(self, reason: str) -> None:
        super().__init__(f"the counterparty's certificate is refused: {reason}")


class NoTrustedRootsError(EcfError):
    """A policy configured without roots would trust nothing: a defect of configuration."""

    code: ClassVar[str] = "do_ecf_no_trusted_roots"

    def __init__(self) -> None:
        super().__init__("a counterparty's certificates are trusted only under configured roots, and none is")


def identities(name: x509.Name) -> frozenset[str]:
    """The RNCs and cédulas ``name`` names (digits only), in ``serialNumber`` and ``organizationIdentifier``.

    ``serialNumber`` bare or prefixed ``IDCDO-``, ``TINDO-`` or ``PNODO-``; ``organizationIdentifier``
    prefixed ``VATDO-`` or ``NTRDO-``. Any other prefix (a passport's ``PASDO-``, another country's) or shape
    names nothing.
    """
    spelled = [
        matched[1]
        for attribute in name
        if (reader := _READERS.get(attribute.oid)) is not None
        and (matched := reader.fullmatch(attribute.value if isinstance(attribute.value, str) else ""))
        is not None
    ]
    return frozenset(
        digits
        for text in spelled
        if (digits := normalize(text)) is not None and scheme_of(digits) is not None
    )


def _valid_at(certificate: x509.Certificate, moment: datetime) -> bool:
    return certificate.not_valid_before_utc <= moment <= certificate.not_valid_after_utc


def _allows_documents(certificate: x509.Certificate, purposes: frozenset[x509.ObjectIdentifier]) -> bool:
    try:
        usage = certificate.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    except x509.ExtensionNotFound:
        return True
    return not purposes.isdisjoint(usage)


@final
@dataclass(frozen=True, slots=True)
class CounterpartyTrust:
    """The policy (see the module): ``roots`` configured, never taken from a document."""

    roots: Sequence[x509.Certificate]
    skew: timedelta = DEFAULT_SKEW
    purposes: frozenset[x509.ObjectIdentifier] = DOCUMENT_SIGNING_PURPOSES
    _store: verification.Store = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.roots:
            raise NoTrustedRootsError
        object.__setattr__(self, "_store", verification.Store(list(self.roots)))

    def _chains(self, verified: VerifiedSignature, moment: datetime) -> bool:
        verifier = (
            verification.PolicyBuilder()
            .store(self._store)
            .time(moment.astimezone(UTC))
            .extension_policies(
                ca_policy=verification.ExtensionPolicy.webpki_defaults_ca(),
                ee_policy=verification.ExtensionPolicy.permit_all(),
            )
            .build_client_verifier()
        )
        try:
            verifier.verify(verified.certificate, list(verified.intermediates))
        except verification.VerificationError:
            return False
        return True

    def check(
        self, verified: VerifiedSignature, *, counterparty: str, signed_at: datetime, received_at: datetime
    ) -> None:
        """Refuse ``verified``'s certificate unless it may speak for ``counterparty`` (see the module).

        ``signed_at``: when the document says it was signed; ``received_at``: when it arrived here.
        ``CounterpartyCertificateError`` says why it may not.
        """
        certificate = verified.certificate
        if len(verified.intermediates) > MAX_INTERMEDIATES:
            raise CounterpartyCertificateError(_TOO_MANY)
        if abs(received_at - signed_at) > self.skew:
            raise CounterpartyCertificateError(_FAR)
        if (problem := signing_problem(certificate)) is not None:
            raise CounterpartyCertificateError(problem)
        for moment, reason in ((signed_at, _SIGNED), (received_at, _RECEIVED)):
            if not _valid_at(certificate, moment):
                raise CounterpartyCertificateError(reason)
            if not self._chains(verified, moment):
                raise CounterpartyCertificateError(_UNTRUSTED)
        if not _allows_documents(certificate, self.purposes):
            raise CounterpartyCertificateError(_PURPOSE)
        if counterparty not in identities(certificate.subject):
            raise CounterpartyCertificateError(_NOT_THEM)
