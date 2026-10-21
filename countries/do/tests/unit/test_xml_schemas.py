from typing import Final

import pytest

from ledger_country_do.channels.ecf_v1.adapters.xml.schema_set import DO_SCHEMAS
from ledger_country_do.channels.ecf_v1.adapters.xsd.published import Format
from ledger_country_do_testing.fake_dgii.receiver import arecf
from ledger_country_do_testing.samples import SIGNED_AT
from ledger_fiscal_kit.xml.schemas import SchemaViolationError

SEED: Final = b"<SemillaModel><valor>abc</valor><fecha>2026-10-12T09:00:00-04:00</fecha></SemillaModel>"
SIGNED_SEED: Final = SEED.replace(
    b"</SemillaModel>", b'<Signature xmlns="http://www.w3.org/2000/09/xmldsig#"/></SemillaModel>'
)


def test_a_document_is_checked_as_it_will_be_once_signed() -> None:
    assert DO_SCHEMAS.violations(Format.SEED, SEED, unsigned=True) == ()
    assert DO_SCHEMAS.violations(Format.SEED, SIGNED_SEED) == ()
    (missing,) = DO_SCHEMAS.violations(Format.SEED, SEED)
    assert missing.path == "/SemillaModel"
    assert "Missing child element" in missing.message


def test_a_violation_says_where_and_cuts_what_libxml2_quotes() -> None:
    wrong = SEED.replace(b"2026-10-12T09:00:00-04:00", b"x" * 400)
    (violation,) = DO_SCHEMAS.violations(Format.SEED, wrong, unsigned=True)
    assert (violation.line, violation.path) == (1, "/SemillaModel/fecha")
    assert len(violation.message) <= 160 + len("...")  # cut by the kernel's truncate


def test_checked_returns_the_document_or_refuses_it_with_its_violations() -> None:
    assert DO_SCHEMAS.checked(Format.SEED, SEED, unsigned=True) == SEED
    with pytest.raises(SchemaViolationError) as caught:
        DO_SCHEMAS.checked(Format.SEED, SEED, unsigned=False)
    assert caught.value.code == "xml_schema_invalid"
    assert len(caught.value.violations) == 1


def test_a_refusal_keeps_its_violations_as_data_and_never_quotes_the_document() -> None:
    hostile = "131098193000.00 Bearer s3cr3t"
    document = arecf("101850043", "131098193", "E310000000001", reason=None, at=SIGNED_AT).replace(
        b"<RNCComprador>131098193</RNCComprador>", f"<RNCComprador>{hostile}</RNCComprador>".encode()
    )
    with pytest.raises(SchemaViolationError) as caught:
        DO_SCHEMAS.checked(Format.ARECF, document, unsigned=True)
    refusal = caught.value
    assert hostile not in str(refusal)
    assert "s3cr3t" not in str(refusal)
    assert "131098193" not in str(refusal)
    (violation,) = refusal.violations
    assert violation.path.endswith("/RNCComprador")
    assert violation.kind.startswith("SCHEMAV_")
    assert f"{violation.path} ({violation.kind}, line {violation.line})" in str(refusal)
    assert "s3cr3t" not in repr(violation)
