"""Opening a company's PKCS#12 bundle: its RSA key and the certificate it signs with.

Every bundle that does not open, whatever the reason (a wrong password, bytes that are no bundle, a bundle cut
short, one over ``MAX_BUNDLE_BYTES`` or one whose key derivation would cost more than ``MAX_ITERATIONS``,
both refused unread), is ``BundleUnreadableError``, raised from nothing: the library's message may say which
reason it was, and so help guess a password. A bundle that opens without an RSA key or without its
certificate is ``BundleIncompleteError``. Opening is CPU work (the password's key derivation): custody calls
it in a thread.

The iteration counts a bundle names are read from its structure before the library opens it
(``iteration_counts``): its MAC's, and those of every password-based encryption it shows (PBES2's PBKDF2,
PKCS#12's and PKCS#5 v1's schemes) in its unencrypted parts and its key bags. A count above ``MAX_ITERATIONS``
(a company's bundle uses a few thousand; two million rounds cost a core over a second and a half) is refused,
so an import cannot hold a core for as long as it likes. Residual: a key bag inside an *encrypted* safe
(unusual: keys travel in plain safes, shrouded) shows its count only once decrypted; the imports' own pool
bounds what such a bundle can hold. The reader takes DER and BER (indefinite lengths, constructed strings).
"""

from dataclasses import dataclass
from typing import Final, final
lazy from collections.abc import Iterator

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
lazy from cryptography import x509

from ledger_signer.application.importing import MAX_BUNDLE_BYTES
from ledger_signer.application.ports import BundleIncompleteError, BundleUnreadableError
lazy from ledger_signer.application.ports import Secret

MAX_ITERATIONS: Final = 1_000_000
"""The most iterations a bundle's key derivation or MAC may name."""
_MAX_DEPTH: Final = 32
"""How deep the reader goes: a bundle nests a dozen levels; more is no bundle."""
_SEQUENCE: Final = 0x30
_INTEGER: Final = 0x02
_OCTET_STRING: Final = 0x04
_OBJECT_ID: Final = 0x06
_CONSTRUCTED: Final = 0x20
_EXPLICIT_0: Final = 0xA0
_INDEFINITE: Final = 0x80
_LONG_FORM: Final = 0x7F
_LENGTH_BYTES: Final = 4
"""The most bytes a length takes (4 GiB: far past any bundle)."""
_RSADSI: Final = bytes.fromhex("2a864886f70d01")
"""1.2.840.113549.1: RSA's PKCS arc."""
_DATA: Final = _RSADSI + bytes.fromhex("0701")
"""pkcs7-data: a content in the clear (an octet string holding DER)."""
_PBKDF2: Final = _RSADSI + bytes.fromhex("050c")
_PBES1: Final = frozenset(_RSADSI + bytes((0x05, last)) for last in (0x01, 0x03, 0x04, 0x06, 0x0A, 0x0B))
"""PKCS#5 v1's schemes: parameters ``(salt, iterations)``."""
_PKCS12_PBE: Final = frozenset(_RSADSI + bytes((0x0C, 0x01, last)) for last in range(1, 7))
"""PKCS#12's schemes: parameters ``(salt, iterations)``."""


class _MalformedError(Exception):
    """Bytes that are not the DER or BER a bundle is made of."""


@final
@dataclass(frozen=True, slots=True)
class _Element:
    """One TLV: its tag and its content (the children's encoding, for a constructed one)."""

    tag: int
    content: bytes

    @property
    def constructed(self) -> bool:
        return bool(self.tag & _CONSTRUCTED)

    def children(self, depth: int) -> list[_Element]:
        return list(_elements(self.content, depth + 1)) if self.constructed else []

    def octets(self, depth: int) -> bytes:
        """An octet string's bytes: its content, or its chunks' joined (a BER constructed one)."""
        if not self.constructed:
            return self.content
        return b"".join(child.octets(depth + 1) for child in self.children(depth))


def _length(data: bytes, at: int) -> tuple[int | None, int]:
    """The length at ``at`` (``None``: indefinite) and where the content starts."""
    if at >= len(data):
        raise _MalformedError
    first = data[at]
    if first < _INDEFINITE:
        return first, at + 1
    if first == _INDEFINITE:
        return None, at + 1
    size = first & _LONG_FORM
    end = at + 1 + size
    if size > _LENGTH_BYTES or end > len(data):
        raise _MalformedError
    return int.from_bytes(data[at + 1 : end], "big"), end


def _element(data: bytes, at: int, depth: int) -> tuple[_Element, int]:
    """The element at ``at`` and where the next one starts."""
    if depth > _MAX_DEPTH or at >= len(data):
        raise _MalformedError
    tag = data[at]
    length, start = _length(data, at + 1)
    if length is not None:
        end = start + length
        if end > len(data):
            raise _MalformedError
        return _Element(tag, data[start:end]), end
    if not tag & _CONSTRUCTED:
        raise _MalformedError
    cursor = start
    while data[cursor : cursor + 2] != b"\x00\x00":  # end-of-contents
        _, cursor = _element(data, cursor, depth + 1)
    return _Element(tag, data[start:cursor]), cursor + 2


def _elements(data: bytes, depth: int) -> Iterator[_Element]:
    cursor = 0
    while cursor < len(data):
        element, cursor = _element(data, cursor, depth)
        yield element


def _count(element: _Element) -> int:
    """An iteration count: a positive INTEGER."""
    if element.tag != _INTEGER or not element.content:
        raise _MalformedError
    count = int.from_bytes(element.content, "big", signed=True)
    if count < 1:
        raise _MalformedError
    return count


def _scheme_counts(children: list[_Element], depth: int) -> Iterator[int]:
    """The count an algorithm identifier names, if it is a password-based scheme's; and a clear content's."""
    match children:
        case [identifier, parameters, *_]:
            oid = identifier.content
        case _:
            return
    if oid in _PBES1 or oid in _PKCS12_PBE or oid == _PBKDF2:
        match parameters.children(depth):
            case [_, count, *_] if parameters.tag == _SEQUENCE:  # salt, iterations, ...
                yield _count(count)
            case _:
                raise _MalformedError
    elif oid == _DATA and parameters.tag == _EXPLICIT_0:
        match parameters.children(depth):
            case [content] if content.tag & ~_CONSTRUCTED == _OCTET_STRING:
                yield from _counts(list(_elements(content.octets(depth + 1), depth + 2)), depth + 2)
            case _:
                raise _MalformedError


def _counts(elements: list[_Element], depth: int) -> Iterator[int]:
    for element in elements:
        children = element.children(depth)
        if element.tag == _SEQUENCE and children and children[0].tag == _OBJECT_ID:
            yield from _scheme_counts(children, depth + 1)
        yield from _counts(children, depth + 1)


def _pfx_counts(bundle: bytes) -> Iterator[int]:
    """The counts of a PFX: its content's, then its MAC's (``_MalformedError`` for anything else)."""
    match list(_elements(bundle, 0)):
        case [pfx] if pfx.tag == _SEQUENCE:
            parts = pfx.children(0)
        case _:
            raise _MalformedError
    yield from _counts(parts[1:2], 1)  # version, authenticated safe, MAC data
    match parts:
        case [_, _, mac, *_]:
            match mac.children(1):
                case [_, _, count, *_]:  # digest, salt, iterations (one when left out)
                    yield _count(count)
                case _:
                    pass
        case _:
            pass


def iteration_counts(bundle: bytes) -> tuple[int, ...]:
    """Every iteration count ``bundle`` names where it can be read unopened (see the module).

    ``BundleUnreadableError`` for bytes that are not a bundle's structure.
    """
    try:
        return tuple(_pfx_counts(bundle))
    except _MalformedError:
        raise BundleUnreadableError from None


def open_bundle(bundle: bytes, password: Secret) -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
    """The RSA key and the certificate of ``bundle``, opened with ``password`` (see the module)."""
    if len(bundle) > MAX_BUNDLE_BYTES or any(count > MAX_ITERATIONS for count in iteration_counts(bundle)):
        raise BundleUnreadableError
    try:
        opened = pkcs12.load_pkcs12(bundle, password.reveal().encode())
    except ValueError, UnsupportedAlgorithm:  # an algorithm the library lacks is a bundle it cannot open
        raise BundleUnreadableError from None
    key, certificate = opened.key, opened.cert
    if not isinstance(key, rsa.RSAPrivateKey) or certificate is None:
        raise BundleIncompleteError
    return key, certificate.certificate
