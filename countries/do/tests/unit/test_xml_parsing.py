import threading
from typing import Final

import pytest

from ledger_country_do.channels.ecf_v1.adapters.xml.parsing import (
    MAX_DOCUMENT,
    UnreadableDocumentError,
    require_readable,
    texts,
)
from ledger_country_do.channels.ecf_v1.adapters.xml.schemas import (
    SchemaViolationError,
    checked,
    schema,
    violations,
)
from ledger_country_do.channels.ecf_v1.adapters.xsd.published import Format

SEED: Final = b"<SemillaModel><valor>abc</valor><fecha>2026-10-12T09:00:00-04:00</fecha></SemillaModel>"
SIGNED_SEED: Final = SEED.replace(
    b"</SemillaModel>", b'<Signature xmlns="http://www.w3.org/2000/09/xmldsig#"/></SemillaModel>'
)


def test_a_document_is_checked_as_it_will_be_once_signed() -> None:
    assert violations(Format.SEED, SEED, unsigned=True) == ()
    assert violations(Format.SEED, SIGNED_SEED) == ()
    (missing,) = violations(Format.SEED, SEED)
    assert missing.path == "/SemillaModel"
    assert "Missing child element" in missing.message


def test_a_violation_says_where_and_cuts_what_libxml2_quotes() -> None:
    wrong = SEED.replace(b"2026-10-12T09:00:00-04:00", b"x" * 400)
    (violation,) = violations(Format.SEED, wrong, unsigned=True)
    assert (violation.line, violation.path) == (1, "/SemillaModel/fecha")
    assert len(violation.message) <= 160


def test_checked_returns_the_document_or_refuses_it_with_its_violations() -> None:
    assert checked(Format.SEED, SEED, unsigned=True) == SEED
    with pytest.raises(SchemaViolationError) as caught:
        checked(Format.SEED, SEED, unsigned=False)
    assert caught.value.code == "do_ecf_schema_invalid"
    assert len(caught.value.violations) == 1


@pytest.mark.parametrize(
    ("document", "code"),
    [
        (b"x" * (MAX_DOCUMENT + 1), "do_ecf_document_too_large"),
        (b'<?xml version="1.0"?><!DOCTYPE a [<!ENTITY b "c">]><a>&b;</a>', "do_ecf_document_doctype_refused"),
        (b"<!doctype a><a/>", "do_ecf_document_doctype_refused"),
        (b'<a/><!DOCTYPE a SYSTEM "http://169.254.169.254/">', "do_ecf_document_doctype_refused"),
        (b"<a>", "do_ecf_document_malformed"),
    ],
    ids=["too-large", "entity", "doctype-lowercase", "doctype-anywhere", "malformed"],
)
def test_what_cannot_be_read_safely_is_refused_unread(document: bytes, code: str) -> None:
    with pytest.raises(UnreadableDocumentError) as caught:
        texts(document, "b")
    assert (caught.value.code, caught.value.kind) == (code, "invalid")
    assert "ENTITY" not in str(caught.value)


def test_the_limit_is_the_caller_s() -> None:
    require_readable(b"<a/>", limit=4)
    with pytest.raises(UnreadableDocumentError):
        require_readable(b"<ab/>", limit=4)


def test_texts_reads_what_it_is_asked_and_nothing_else() -> None:
    assert texts(b"<a><b>x</b><c/></a>", "b", "c", "d") == ("x", "", None)


def test_each_thread_compiles_its_own_schema() -> None:
    here = schema(Format.SEED)
    assert schema(Format.SEED) is here
    elsewhere: list[object] = []
    thread = threading.Thread(target=lambda: elsewhere.append(schema(Format.SEED)))
    thread.start()
    thread.join()
    assert elsewhere[0] is not here


def test_a_malformed_document_is_refused_before_it_is_validated() -> None:
    with pytest.raises(UnreadableDocumentError) as caught:
        violations(Format.SEED, b"<SemillaModel>")
    assert caught.value.code == "do_ecf_document_malformed"
