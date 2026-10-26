import socket
from typing import Final, cast
lazy from pathlib import Path

import pytest
from lxml import etree

from ledger_fiscal_kit.xml import hardened
from ledger_fiscal_kit.xml.hardened import (
    MAX_DOCUMENT,
    MalformedDocumentError,
    NodeRefusedError,
    XmlDocumentError,
    parser,
    require_readable,
    require_tree,
    root_name,
    texts,
)

_ENTITY: Final = '<!DOCTYPE a [<!ENTITY b "c">]><a>&b;</a>'
_UTF16_DECLARED: Final = f'<?xml version="1.0" encoding="UTF-16"?>{_ENTITY}'


@pytest.mark.parametrize(
    ("document", "code"),
    [
        (b"x" * (MAX_DOCUMENT + 1), "xml_document_too_large"),
        (b'<?xml version="1.0"?><!DOCTYPE a [<!ENTITY b "c">]><a>&b;</a>', "xml_document_doctype_refused"),
        (b"<!doctype a><a/>", "xml_document_doctype_refused"),
        (b'<a/><!DOCTYPE a SYSTEM "http://169.254.169.254/">', "xml_document_doctype_refused"),
        (b"<a>", "xml_document_malformed"),
        (_UTF16_DECLARED.encode("utf-16"), "xml_document_encoding_refused"),
        (_ENTITY.encode("utf-16"), "xml_document_encoding_refused"),
        (_ENTITY.encode("utf-16-le"), "xml_document_encoding_refused"),
        (_ENTITY.encode("utf-16-be"), "xml_document_encoding_refused"),
        (_ENTITY.encode("utf-32"), "xml_document_encoding_refused"),
        (
            '<?xml version="1.0" encoding="ISO-8859-1"?><a>é</a>'.encode("latin-1"),
            "xml_document_encoding_refused",
        ),
        (b"<?xml version='1.0' encoding='UTF-16'?><a/>", "xml_document_encoding_refused"),
    ],
    ids=[
        "too-large",
        "entity",
        "doctype-lowercase",
        "doctype-anywhere",
        "malformed",
        "utf-16-bom-declared",
        "utf-16-bom",
        "utf-16-le",
        "utf-16-be",
        "utf-32-bom",
        "latin-1-declared",
        "utf-16-declared-ascii",
    ],
)
def test_what_cannot_be_read_safely_is_refused_unread(document: bytes, code: str) -> None:
    with pytest.raises(XmlDocumentError) as caught:
        texts(document, "b")
    assert (caught.value.code, caught.value.kind) == (code, "invalid")
    assert "ENTITY" not in str(caught.value)


def test_the_limit_is_the_caller_s() -> None:
    require_readable(b"<a/>", limit=4)
    with pytest.raises(XmlDocumentError):
        require_readable(b"<ab/>", limit=4)


def test_texts_reads_what_it_is_asked_and_nothing_else() -> None:
    assert texts(b"<a><b>x</b><c/></a>", "b", "c", "d") == ("x", "", None)


def test_a_comment_cannot_split_what_texts_reads() -> None:
    # A comment inside a value (``1<!---->0``) leaves C14N without comments, and so a signature, unchanged:
    # the parser drops comments, so what is read is what was signed.
    document = b"<ECF><Estado>1<!---->0</Estado><RNC>1018<!-- x -->50043</RNC><!-- tail --></ECF>"
    assert texts(document, "Estado", "RNC") == ("10", "101850043")
    assert etree.fromstring(document, parser()).xpath("count(//comment())") == 0


def test_utf_8_is_read_with_or_without_its_bom_or_declaration() -> None:
    assert texts(b"\xef\xbb\xbf<a><b>\xc3\xa9</b></a>", "b") == ("\u00e9",)
    assert texts(b'<?xml version="1.0" encoding="utf-8"?><a><b>x</b></a>', "b") == ("x",)


@pytest.mark.parametrize(
    ("document", "code"),
    [
        (_UTF16_DECLARED.encode("utf-16"), "xml_document_doctype_refused"),
        (b'<?xml version="1.0" encoding="ISO-8859-1"?><a/>', "xml_document_encoding_refused"),
    ],
    ids=["doctype", "encoding"],
)
def test_what_got_past_the_byte_checks_is_refused_once_parsed(
    monkeypatch: pytest.MonkeyPatch, document: bytes, code: str
) -> None:
    def skipped(document: bytes, *, limit: int = MAX_DOCUMENT) -> None:
        del document, limit

    monkeypatch.setattr(hardened, "require_readable", skipped)
    with pytest.raises(XmlDocumentError) as caught:
        texts(document, "b")
    assert caught.value.code == code


def test_the_parser_refuses_exponential_entities() -> None:
    laughs = "".join(f'<!ENTITY l{n} "{f"&l{n - 1};" * 10}">' for n in range(1, 10))
    payload = f'<!DOCTYPE a [<!ENTITY l0 "lol">{laughs}]><a>&l9;</a>'.encode()
    with pytest.raises(etree.XMLSyntaxError, match="amplification"):
        etree.fromstring(payload, parser())


def test_the_parser_resolves_no_external_entity(tmp_path: Path) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("LOCAL-FILE-MARKER", encoding="utf-8")
    root = etree.fromstring(
        f'<!DOCTYPE a [<!ENTITY x SYSTEM "{secret.as_uri()}">]><a>&x;</a>'.encode(), parser()
    )
    assert b"LOCAL-FILE-MARKER" not in etree.tostring(root)


def test_the_parser_loads_no_external_dtd(tmp_path: Path) -> None:
    dtd = tmp_path / "defaults.dtd"
    dtd.write_text('<!ATTLIST a marker CDATA "LOCAL-FILE-MARKER">', encoding="utf-8")
    root = etree.fromstring(f'<!DOCTYPE a SYSTEM "{dtd.as_uri()}"><a/>'.encode(), parser())
    assert root.get("marker") is None


def test_the_parser_reaches_no_network() -> None:
    with socket.create_server(("127.0.0.1", 0)) as listening:
        listening.settimeout(0)
        _, port = cast("tuple[str, int]", listening.getsockname())
        etree.fromstring(f'<!DOCTYPE a SYSTEM "http://127.0.0.1:{port}/a.dtd"><a/>'.encode(), parser())
        with pytest.raises(BlockingIOError):
            listening.accept()


def test_the_parser_keeps_libxml2_s_depth_limit() -> None:
    with pytest.raises(etree.XMLSyntaxError, match="depth"):
        etree.fromstring(b"<a>" * 300 + b"</a>" * 300, parser())


@pytest.mark.parametrize(
    "document",
    [
        b"<ECF><MontoTotal>4<?x?>887.58</MontoTotal></ECF>",
        b"<ECF><Estado><?x?>1</Estado></ECF>",
        b"<ECF><RNC>131<?x?>098193</RNC></ECF>",
        b"<ECF><RNC>131098193</RNC><?x?></ECF>",
    ],
    ids=["split-amount", "before-a-value", "split-tax-id", "last-child"],
)
def test_a_processing_instruction_inside_the_root_is_refused(document: bytes) -> None:
    # findtext reads only the text before an instruction: "4<?x?>887.58" would read as "4".
    with pytest.raises(NodeRefusedError) as caught:
        texts(document, "MontoTotal")
    assert caught.value.code == "xml_document_node_refused"


def test_processing_instructions_outside_the_root_are_the_document_s_own() -> None:
    # Outside the root they are covered by C14N, and so by a signature: allowed, never read.
    assert texts(b"<?pi before?><a><b>x</b></a><?pi after?>", "b") == ("x",)


def test_only_an_element_is_a_tree() -> None:
    with pytest.raises(NodeRefusedError):
        require_tree("<a/>")
    require_tree(etree.fromstring(b"<a>t<b/>u</a>", parser()))


def test_a_malformed_document_s_error_carries_none_of_libxml2_s_message() -> None:
    # libxml2's message quotes the document: it is neither the error's cause nor shown as its context.
    with pytest.raises(MalformedDocumentError) as caught:
        texts(b"<a>SECRET</b>", "b")
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__


def test_root_name_is_the_root_element_s_tag_read_as_texts_reads() -> None:
    assert root_name(b"<ECF><Encabezado/></ECF>") == "ECF"
    assert root_name(b'<RFCE xmlns="urn:x"><a/></RFCE>') == "{urn:x}RFCE"
    with pytest.raises(MalformedDocumentError):
        root_name(b"<ECF>")
    with pytest.raises(XmlDocumentError, match="DOCTYPE"):
        root_name(b"<!DOCTYPE ECF><ECF/>")
