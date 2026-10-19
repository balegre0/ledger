"""The ``SignatureValue`` a signed document carries: where its security code comes from.

It is read from the root's own ``Signature`` (a child of the root, as DGII's profile places it), never from
one nested deeper. Nothing is verified here: a document read from outside goes through
``verifier.verify_enveloped``, whose ``VerifiedSignature.signature_value`` is the same value.
"""

from typing import ClassVar, Final

from ledger_country_do.channels.ecf_v1.domain.errors import EcfError
from ledger_fiscal_kit.xml.hardened import DSIG, SIGNATURE, texts

_VALUE: Final = f"{SIGNATURE}/{{{DSIG}}}SignatureValue"


class SignatureValueMissingError(EcfError):
    """A document said to be signed holds no signature value: a signer's answer it never gives."""

    code: ClassVar[str] = "do_ecf_signature_value_missing"

    def __init__(self) -> None:
        super().__init__("the signed document holds no SignatureValue")


def signature_value(signed: bytes) -> str:
    """The signature value of ``signed``, whitespace removed."""
    (value,) = texts(signed, _VALUE)
    if value is None or not value.strip():
        raise SignatureValueMissingError
    return "".join(value.split())
