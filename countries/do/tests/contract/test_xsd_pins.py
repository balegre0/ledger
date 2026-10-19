import hashlib

import pytest
import xmlschema
from lxml import etree

from ledger_country_do.channels.ecf_v1.adapters.xml.schema_set import DO_SCHEMAS, published_bytes
from ledger_country_do.channels.ecf_v1.adapters.xsd.published import (
    CORRECTIONS,
    PUBLISHED,
    SOURCE,
    Format,
)
from ledger_country_do.regime import REGIME
from ledger_fiscal_kit.xml.schemas import PinnedSchema, SchemaDriftError, SchemaSet
lazy from ledger_kernel.regimes import DocumentTypeDef

XS = "{http://www.w3.org/2001/XMLSchema}"
NOT_AS_PUBLISHED = frozenset(CORRECTIONS)


@pytest.mark.parametrize("format_", list(Format))
def test_every_vendored_schema_is_the_file_dgii_published(format_: Format) -> None:
    published = PUBLISHED[format_]
    assert hashlib.sha256(published_bytes(format_)).hexdigest() == published.sha256
    assert published.url.startswith(SOURCE)
    assert " " not in published.url


@pytest.mark.parametrize("format_", list(Format))
def test_every_schema_compiles_in_both_implementations_once_corrected(format_: Format) -> None:
    data = DO_SCHEMAS.corrected(format_)
    etree.XMLSchema(etree.fromstring(data))
    xmlschema.XMLSchema10(data.decode("utf-8"))
    root = etree.fromstring(data).find(f"{XS}element")
    assert root is not None
    assert root.get("name") == PUBLISHED[format_].root


@pytest.mark.parametrize("format_", sorted(NOT_AS_PUBLISHED))
def test_the_corrected_schemas_do_not_compile_as_published(format_: Format) -> None:
    with pytest.raises(etree.XMLSchemaParseError):
        etree.XMLSchema(etree.fromstring(published_bytes(format_)))


def test_a_schema_that_changed_is_drift() -> None:
    changed = PinnedSchema(
        format=Format.SEED, published=lambda: b"<changed/>", sha256=PUBLISHED[Format.SEED].sha256
    )
    with pytest.raises(SchemaDriftError, match="is not the file pinned"):
        SchemaSet[Format]({Format.SEED: changed}).corrected(Format.SEED)


def test_a_correction_that_no_longer_applies_is_drift() -> None:
    fixed = published_bytes(Format.ACECF).replace(b"(?:", b"(")
    pinned = PinnedSchema(
        format=Format.ACECF,
        published=lambda: fixed,
        sha256=hashlib.sha256(fixed).hexdigest(),
        corrections=CORRECTIONS[Format.ACECF],
    )
    with pytest.raises(SchemaDriftError, match="holds 0 of a correction pinned at 1"):
        SchemaSet[Format]({Format.ACECF: pinned}).corrected(Format.ACECF)


def _required(format_: Format, name: str) -> bool:
    declared = [
        found
        for found in etree.fromstring(DO_SCHEMAS.corrected(format_)).iter(f"{XS}element")
        if found.get("name") == name
    ]
    return any(found.get("minOccurs", "1") != "0" for found in declared)


@pytest.mark.parametrize("kind", REGIME.document_types, ids=[kind.code for kind in REGIME.document_types])
def test_the_regime_requires_a_buyer_tax_id_where_dgii_s_schema_does(kind: DocumentTypeDef) -> None:
    format_ = Format(f"ecf-{kind.code}.xsd")
    assert kind.requires_buyer_tax_id is (
        _required(format_, "Comprador") and _required(format_, "RNCComprador")
    )
