r"""Distinguished names as signers write them in ``X509IssuerSerial`` and ``X509SubjectName``, compared.

Tools write the same name differently: RFC 4514 (``CN=A,C=DO``), .NET (most specific first, ``, ``
between parts, ``E=``, ``S=``, ``SERIALNUMBER=``, ``OID.2.5.4.97=``), Java (``EMAILADDRESS=``,
``OID.x.y.z=``, lower-case labels), with quotes or backslash escapes (``\,``, ``\2C``, ``\C3\91``). A name is
read as a set of (OID, value) pairs: labels and their aliases mapped to OIDs, values unescaped, whitespace
collapsed and case folded; the ASN.1 string type is ignored. Order does not matter: a certificate's name never
repeats a pair.

These names are informational (the key comes from the certificate itself): one that cannot be read, a value
in hexadecimal BER (``#04...``) included, is skipped; one that is read must name the certificate.
"""

from typing import Final
lazy from collections.abc import Iterator

lazy from cryptography import x509

type NamePairs = frozenset[tuple[str, str]]

_LABELS: Final = {
    **dict.fromkeys(("CN", "COMMONNAME"), "2.5.4.3"),
    **dict.fromkeys(("SN", "SURNAME"), "2.5.4.4"),
    **dict.fromkeys(("SERIALNUMBER",), "2.5.4.5"),
    **dict.fromkeys(("C", "COUNTRYNAME"), "2.5.4.6"),
    **dict.fromkeys(("L", "LOCALITYNAME"), "2.5.4.7"),
    **dict.fromkeys(("S", "ST", "STATEORPROVINCENAME"), "2.5.4.8"),
    **dict.fromkeys(("STREET", "STREETADDRESS"), "2.5.4.9"),
    **dict.fromkeys(("O", "ORGANIZATIONNAME"), "2.5.4.10"),
    **dict.fromkeys(("OU", "ORGANIZATIONALUNITNAME"), "2.5.4.11"),
    **dict.fromkeys(("T", "TITLE"), "2.5.4.12"),
    **dict.fromkeys(("G", "GN", "GIVENNAME"), "2.5.4.42"),
    **dict.fromkeys(("DNQUALIFIER",), "2.5.4.46"),
    **dict.fromkeys(("ORGANIZATIONIDENTIFIER",), "2.5.4.97"),
    **dict.fromkeys(("E", "EMAIL", "EMAILADDRESS"), "1.2.840.113549.1.9.1"),
    **dict.fromkeys(("DC", "DOMAINCOMPONENT"), "0.9.2342.19200300.100.1.25"),
    **dict.fromkeys(("UID", "USERID"), "0.9.2342.19200300.100.1.1"),
}
"""Attribute labels of RFC 4514, .NET, Java and OpenSSL, upper-cased, to their OIDs."""
_SEPARATORS: Final = frozenset(",;+")
_HEX: Final = frozenset("0123456789abcdefABCDEF")
_HEX_PAIR: Final = 2
"""Hexadecimal digits in an escaped byte (``\2C``)."""


def _normal(value: str) -> str:
    return " ".join(value.split()).casefold()


def name_pairs(name: x509.Name) -> NamePairs:
    """``name``'s (OID, value) pairs, values normalized as ``parsed_name`` normalizes them."""
    return frozenset((attribute.oid.dotted_string, _normal(str(attribute.value))) for attribute in name)


def _parts(text: str) -> Iterator[str]:
    """``text`` cut at unescaped, unquoted separators; escapes and quotes kept for ``_value``."""
    start, quoted, escaped = 0, False, False
    for index, character in enumerate(text):
        if escaped:
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == '"':
            quoted = not quoted
        elif character in _SEPARATORS and not quoted:
            yield text[start:index]
            start = index + 1
    yield text[start:]


def _oid(label: str) -> str | None:
    dotted = label.upper().removeprefix("OID.")
    if dotted and all(part.isdigit() for part in dotted.split(".")):
        return dotted
    return _LABELS.get(label.upper())


def _value(written: str) -> str | None:
    """The value ``written`` stands for: quotes removed, escapes resolved; ``None`` if malformed or BER."""
    if written.startswith("#"):
        return None
    if written.startswith('"'):
        return written[1:-1] if len(written) > 1 and written.endswith('"') else None
    data = bytearray()
    index = 0
    while index < len(written):
        character, escaped = written[index], written[index + 1 : index + 3]
        if character != "\\":
            data += character.encode()
            index += 1
        elif len(escaped) == _HEX_PAIR and set(escaped) <= _HEX:
            data.append(int(escaped, 16))
            index += 3
        elif escaped and escaped[0] not in _HEX:
            data += escaped[0].encode()
            index += 2
        else:
            return None
    try:
        return data.decode()
    except UnicodeDecodeError:
        return None


def parsed_name(text: str) -> NamePairs | None:
    """``text``'s (OID, value) pairs, or ``None`` when it cannot be read as a distinguished name."""
    pairs: set[tuple[str, str]] = set()
    for part in _parts(text):
        label, equals, written = part.partition("=")
        oid = _oid(label.strip())
        value = _value(written.strip()) if equals else None
        if oid is None or value is None:
            return None
        pairs.add((oid, _normal(value)))
    return frozenset(pairs)


def names_agree(text: str, name: x509.Name) -> bool:
    """Whether ``text`` names ``name``; a name that cannot be read is skipped, so it agrees."""
    pairs = parsed_name(text)
    return pairs is None or pairs == name_pairs(name)
