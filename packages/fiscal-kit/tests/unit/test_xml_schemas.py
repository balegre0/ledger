import hashlib
import threading
from dataclasses import replace
from enum import StrEnum
from typing import Final

import pytest
from lxml import etree

from ledger_fiscal_kit.xml.hardened import XmlDocumentError
from ledger_fiscal_kit.xml.schemas import (
    MAX_VIOLATIONS,
    Correction,
    CorrectionDriftError,
    PinnedSchema,
    SchemaChangedError,
    SchemaDriftError,
    SchemaSet,
    SchemaViolationError,
)
from ledger_kernel.errors import ErrorKind, InvalidValueError


class _Format(StrEnum):
    ORDER = "order.xsd"
    RECEIPT = "receipt.xsd"


_PUBLISHED: Final = b"""\xef\xbb\xbf<?xml version="1.0" encoding="utf-8"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
  <xs:element name="Order">
    <xs:complexType>
      <xs:sequence>
        <xs:element name="Number" type="NumberType" maxOccurs="unbounded"/>
        <xs:any namespace="http://www.w3.org/2000/09/xmldsig#" processContents="skip"/>
      </xs:sequence>
    </xs:complexType>
  </xs:element>
  <xs:simpleType name=" NumberType">
    <xs:restriction base="xs:string"><xs:pattern value="[0-9]{1,10}"/></xs:restriction>
  </xs:simpleType>
</xs:schema>
"""
"""A two-element schema as an authority might publish it: a BOM, and a type name that does not compile."""
_FIX: Final = Correction(
    wrong='name=" NumberType"',
    right='name="NumberType"',
    count=1,
    reason="a space before the type's name leaves the element that uses it without a type",
)
_PIN: Final = PinnedSchema(
    format=_Format.ORDER,
    published=_PUBLISHED,
    sha256=hashlib.sha256(_PUBLISHED).hexdigest(),
    corrections=(_FIX,),
)
_UNSIGNED: Final = b"<Order><Number>42</Number></Order>"
_SIGNED: Final = b'<Order><Number>42</Number><Signature xmlns="http://www.w3.org/2000/09/xmldsig#"/></Order>'


def _set(pin: PinnedSchema[_Format] = _PIN) -> SchemaSet[_Format]:
    return SchemaSet[_Format]({pin.format: pin})


def test_a_schema_is_corrected_as_pinned_and_compiles_once_corrected() -> None:
    with pytest.raises(etree.XMLSchemaParseError):
        etree.XMLSchema(etree.fromstring(_PUBLISHED))
    corrected = _set().corrected(_Format.ORDER)
    assert corrected.startswith(b"<?xml")
    assert b'name="NumberType"' in corrected
    etree.XMLSchema(etree.fromstring(corrected))


def test_a_schema_whose_bytes_changed_is_drift_at_first_use() -> None:
    schemas = _set(replace(_PIN, published=_PUBLISHED.replace(b"10", b"11")))
    with pytest.raises(SchemaChangedError, match=r"^the schema order\.xsd is not the file pinned$") as caught:
        schemas.violations(_Format.ORDER, _SIGNED)
    assert isinstance(caught.value, SchemaDriftError)
    assert (caught.value.kind, caught.value.code) == (ErrorKind.INTERNAL, "xml_schema_drift")


def test_a_correction_applied_another_number_of_times_than_pinned_is_drift() -> None:
    schemas = _set(replace(_PIN, corrections=(replace(_FIX, count=2),)))
    with pytest.raises(CorrectionDriftError, match=r"order\.xsd holds 1 of a correction pinned at 2"):
        schemas.corrected(_Format.ORDER)


def test_a_document_is_checked_as_it_will_be_once_signed() -> None:
    schemas = _set()
    assert schemas.violations(_Format.ORDER, _UNSIGNED, unsigned=True) == ()
    assert schemas.violations(_Format.ORDER, _SIGNED) == ()
    (missing,) = schemas.violations(_Format.ORDER, _UNSIGNED)
    assert (missing.line, missing.path) == (1, "/Order")
    assert "Missing child element" in missing.message


def test_checked_returns_the_document_unchanged_or_refuses_it_naming_paths_never_text() -> None:
    schemas = _set()
    assert schemas.checked(_Format.ORDER, _UNSIGNED, unsigned=True) is _UNSIGNED
    hostile = b"<Order><Number>Bearer s3cr3t 131098193</Number></Order>"
    with pytest.raises(SchemaViolationError) as caught:
        schemas.checked(_Format.ORDER, hostile, unsigned=True)
    refusal = caught.value
    assert (refusal.kind, refusal.code) == (ErrorKind.INVALID, "xml_schema_invalid")
    (violation,) = refusal.violations
    assert violation.path == "/Order/Number"
    assert violation.kind.startswith("SCHEMAV_")
    assert str(refusal) == (
        f"the schema order.xsd refuses the document: {violation.path} ({violation.kind}, line 1)"
    )
    assert "s3cr3t" in violation.message
    assert "s3cr3t" not in str(refusal)
    assert "s3cr3t" not in repr(violation)


def test_a_violation_cuts_what_libxml2_quotes_and_a_refusal_lists_at_most_its_bound() -> None:
    numbers = b"".join(b"<Number>" + b"x" * 400 + b"</Number>" for _ in range(MAX_VIOLATIONS + 5))
    found = _set().violations(_Format.ORDER, b"<Order>" + numbers + b"</Order>", unsigned=True)
    assert len(found) == MAX_VIOLATIONS
    assert all(len(violation.message) <= 160 + len("...") for violation in found)


def test_each_thread_compiles_its_own_schema() -> None:
    schemas = _set()
    here = schemas.schema(_Format.ORDER)
    assert schemas.schema(_Format.ORDER) is here
    elsewhere: list[object] = []
    thread = threading.Thread(target=lambda: elsewhere.append(schemas.schema(_Format.ORDER)))
    thread.start()
    thread.join()
    assert elsewhere[0] is not here


@pytest.mark.parametrize(
    ("document", "code"),
    [
        (b"<Order>", "xml_document_malformed"),
        (b'<!DOCTYPE Order SYSTEM "x"><Order/>', "xml_document_doctype_refused"),
    ],
)
def test_a_document_is_read_hardened_before_it_is_validated(document: bytes, code: str) -> None:
    with pytest.raises(XmlDocumentError) as caught:
        _set().violations(_Format.ORDER, document)
    assert caught.value.code == code


def test_a_format_the_set_does_not_pin_is_a_defect() -> None:
    with pytest.raises(InvalidValueError, match="format must be one the schema set pins"):
        _set().corrected(_Format.RECEIPT)


def test_a_pin_under_another_format_is_a_defect() -> None:
    with pytest.raises(InvalidValueError, match=r"^pins\[receipt\.xsd\]\.format must be receipt\.xsd$"):
        SchemaSet[_Format]({_Format.RECEIPT: _PIN})


def test_a_pin_never_shows_the_schema() -> None:
    assert "xs:schema" not in repr(_PIN)
