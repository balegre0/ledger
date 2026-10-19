import pytest

from ledger_fiscal_kit.xml.c14n import canonical
from ledger_fiscal_kit.xml.hardened import XmlDocumentError

_DOCUMENT = (
    b'<?xml version="1.0" encoding="UTF-8"?>\n'
    b'<a xmlns="urn:a" xmlns:b="urn:b"   z="2" y=\'1\'><b:c/><!-- note --><d>x</d></a>'
)


def test_the_canonical_form_is_c14n_1_0_without_comments() -> None:
    assert canonical(_DOCUMENT) == (b'<a xmlns="urn:a" xmlns:b="urn:b" y="1" z="2"><b:c></b:c><d>x</d></a>')


def test_the_exclusive_form_keeps_only_the_namespaces_each_element_uses() -> None:
    assert canonical(b'<a xmlns:b="urn:b"><c/></a>', exclusive=True) == b"<a><c></c></a>"
    assert canonical(b'<a xmlns:b="urn:b"><c/></a>') == b'<a xmlns:b="urn:b"><c></c></a>'


@pytest.mark.parametrize(
    ("document", "code"),
    [
        (b'<!DOCTYPE a [<!ENTITY b "c">]><a>&b;</a>', "xml_document_doctype_refused"),
        (b"<a>", "xml_document_malformed"),
    ],
)
def test_a_document_is_read_hardened(document: bytes, code: str) -> None:
    with pytest.raises(XmlDocumentError) as caught:
        canonical(document)
    assert caught.value.code == code
