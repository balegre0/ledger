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
from ledger_country_do.channels.ecf_v1.domain.drafts import (
    BILLINGS,
    FOREIGN_CURRENCIES,
    NAMED_TYPES,
    NO_DISCOUNT_TYPES,
    NOTE_TYPES,
    RNC_TYPES,
    TAXED,
    UNDATED_TYPES,
    BillingIndicator,
    ModificationCode,
    max_lines,
)
from ledger_country_do.channels.ecf_v1.domain.encf import EcfType
from ledger_country_do.regime import REGIME
from ledger_fiscal_kit.xml.schemas import PinnedSchema, SchemaDriftError, SchemaSet
from ledger_kernel.money import CURRENCIES
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
    assert kind.requires_buyer_tax_id is (EcfType(kind.code) in RNC_TYPES)


def _ecf(ecf_type: EcfType) -> Format:
    return Format(f"ecf-{ecf_type.value}.xsd")


def _declared(format_: Format, name: str) -> bool:
    return any(
        found.get("name") == name
        for found in etree.fromstring(DO_SCHEMAS.corrected(format_)).iter(f"{XS}element")
    )


def _enumeration(format_: Format, simple_type: str) -> set[str]:
    schema = etree.fromstring(DO_SCHEMAS.corrected(format_))
    (declared,) = (found for found in schema.iter(f"{XS}simpleType") if found.get("name") == simple_type)
    return {value for value in (each.get("value") for each in declared.iter(f"{XS}enumeration")) if value}


@pytest.mark.parametrize("ecf_type", list(EcfType))
def test_each_type_s_rules_are_its_schema_s(ecf_type: EcfType) -> None:
    format_ = _ecf(ecf_type)
    (item,) = (
        found
        for found in etree.fromstring(DO_SCHEMAS.corrected(format_)).iter(f"{XS}element")
        if found.get("name") == "Item"
    )
    assert max_lines(ecf_type) == int(item.get("maxOccurs", "1"))
    assert (ecf_type in NAMED_TYPES) is _required(format_, "RazonSocialComprador")
    assert (ecf_type in RNC_TYPES) is (_required(format_, "Comprador") and _required(format_, "RNCComprador"))
    assert (ecf_type not in UNDATED_TYPES) is _required(format_, "FechaVencimientoSecuencia")
    assert (ecf_type in NOTE_TYPES) is _required(format_, "InformacionReferencia")
    assert (ecf_type not in NO_DISCOUNT_TYPES) is _declared(format_, "DescuentoMonto")
    assert _declared(format_, "OtraMoneda")  # every type may be in another currency
    taxed = {indicator for indicator in TAXED if _declared(format_, f"MontoGravadoI{indicator.value}")}
    assert BILLINGS[ecf_type] == {*taxed, BillingIndicator.EXEMPT}


def test_the_foreign_currencies_are_those_dgii_lists_that_money_knows() -> None:
    listed = _enumeration(Format.ECF_31, "TipoMonedaType")
    assert {code for code in listed if code in CURRENCIES} == FOREIGN_CURRENCIES
    assert all(_enumeration(_ecf(each), "TipoMonedaType") == listed for each in EcfType)


def test_the_modification_codes_are_dgii_s() -> None:
    expected = {str(code.value) for code in ModificationCode}
    referring = [each for each in EcfType if _declared(_ecf(each), "CodigoModificacion")]
    assert set(NOTE_TYPES) <= set(referring)
    assert all(_enumeration(_ecf(each), "CodigoModificacionType") == expected for each in referring)
