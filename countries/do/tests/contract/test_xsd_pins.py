import hashlib
import re
from typing import Final

import pytest
import xmlschema
from lxml import etree

from ledger_country_do.channels.ecf_v1.adapters.xml import acecf, arecf
from ledger_country_do.channels.ecf_v1.adapters.xml.ecf import BUYER_REQUIRED_TYPES, PRICE_INDICATOR_TYPES
from ledger_country_do.channels.ecf_v1.adapters.xml.schema_set import DO_SCHEMAS, published_bytes
from ledger_country_do.channels.ecf_v1.adapters.xsd.published import (
    CORRECTIONS,
    PUBLISHED,
    SOURCE,
    Format,
)
from ledger_country_do.channels.ecf_v1.domain.drafts import (
    ADDRESS,
    BILLINGS,
    BUYER_PARTS,
    DISCOUNT_PLACES,
    EXCHANGE_RATE_LIMIT,
    EXCHANGE_RATE_PLACES,
    FOREIGN_CURRENCIES,
    FOREIGN_ID,
    INCOME_TYPES,
    INTEGER_DIGITS,
    ISR_REQUIRED_TYPES,
    ITBIS_WITHHOLDING_TYPES,
    ITEM_NAME,
    LEGAL_NAME,
    NAMED_TYPES,
    NO_DISCOUNT_TYPES,
    NOTE_TYPES,
    PAYMENT_DUE_TYPES,
    PERCEPTION_TYPES,
    QUANTITY_PLACES,
    RNC_TYPES,
    TAXED,
    UNDATED_TYPES,
    UNIT_PRICE_PLACES,
    WITHHELD_PLACES,
    WITHHOLDING_TYPES,
    BillingIndicator,
    BuyerPart,
    IncomeType,
    ModificationCode,
    PaymentType,
    WithholdingRole,
    max_lines,
)
from ledger_country_do.channels.ecf_v1.domain.encf import EcfType
from ledger_country_do.channels.ecf_v1.domain.exchanges import (
    ANNULMENT_COUNT,
    ANNULMENT_RANGES,
    ANNULMENT_TYPES,
    APPROVAL_TOTAL_DIGITS,
    APPROVAL_TOTAL_PLACES,
    REJECTION_REASON,
    NotReceivedReason,
)
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
    exempt: set[BillingIndicator] = {BillingIndicator.EXEMPT} if _declared(format_, "MontoExento") else set()
    assert BILLINGS[ecf_type] == taxed | exempt
    holds = {part for part, element in _BUYER_ELEMENTS.items() if _declared(format_, element)}
    assert BUYER_PARTS[ecf_type] == holds
    assert (ecf_type in INCOME_TYPES) is _declared(format_, "TipoIngresos")
    assert (ecf_type in PAYMENT_DUE_TYPES) is _declared(format_, "FechaLimitePago")
    assert (ecf_type in WITHHOLDING_TYPES) is _required(format_, "Retencion")
    assert (ecf_type in ITBIS_WITHHOLDING_TYPES) is (
        ecf_type in WITHHOLDING_TYPES and _declared(format_, "MontoITBISRetenido")
    )
    assert (ecf_type in ISR_REQUIRED_TYPES) is _required(format_, "MontoISRRetenido")
    assert (ecf_type in PERCEPTION_TYPES) is (
        ecf_type in WITHHOLDING_TYPES and _declared(format_, "TotalITBISPercepcion")
    )
    assert (ecf_type in PRICE_INDICATOR_TYPES) is _declared(format_, "IndicadorMontoGravado")
    assert (ecf_type in BUYER_REQUIRED_TYPES) is _required(format_, "Comprador")
    assert (ecf_type is EcfType.CREDIT_NOTE) is _required(format_, "IndicadorNotaCredito")


_BUYER_ELEMENTS: Final = {
    BuyerPart.RNC: "RNCComprador",
    BuyerPart.FOREIGN_ID: "IdentificadorExtranjero",
    BuyerPart.NAME: "RazonSocialComprador",
    BuyerPart.ADDRESS: "DireccionComprador",
}
_TEXTS: Final = {
    "RazonSocialEmisor": LEGAL_NAME,
    "DireccionEmisor": ADDRESS,
    "RazonSocialComprador": LEGAL_NAME,
    "DireccionComprador": ADDRESS,
    "IdentificadorExtranjero": FOREIGN_ID,
    "NombreItem": ITEM_NAME,
}
_NUMBERS: Final = {
    "CantidadItem": (INTEGER_DIGITS, QUANTITY_PLACES),
    "PrecioUnitarioItem": (INTEGER_DIGITS, UNIT_PRICE_PLACES),
    "DescuentoMonto": (INTEGER_DIGITS, DISCOUNT_PLACES),
    "MontoITBISRetenido": (INTEGER_DIGITS, WITHHELD_PLACES),
    "MontoISRRetenido": (INTEGER_DIGITS, WITHHELD_PLACES),
    "MontoItem": (INTEGER_DIGITS, 2),
    "TipoCambio": (len(str(EXCHANGE_RATE_LIMIT)) - 1, EXCHANGE_RATE_PLACES),
}
_INTEGER_PART: Final = re.compile(r"^\(?(?:\[0-9\]|\\d)\{1,([0-9]+)\}")
"""The integer part of a decimal type's pattern: ``[0-9]{1,16}`` or ``(\\d{1,16})``."""


def _facets(format_: Format, element: str) -> list[dict[str, str]]:
    """The facets of each simple type ``element`` is declared with in ``format_``."""
    schema = etree.fromstring(DO_SCHEMAS.corrected(format_))
    types = {found.get("name"): found for found in schema.iter(f"{XS}simpleType")}
    facets: list[dict[str, str]] = []
    for found in schema.iter(f"{XS}element"):
        if found.get("name") != element:
            continue
        declared = types[found.get("type")]
        facets.append(
            {
                etree.QName(facet).localname: facet.get("value", "")
                for facet in declared.iter()
                if facet.get("value") is not None and etree.QName(facet).localname != "enumeration"
            }
        )
    return facets


@pytest.mark.parametrize("ecf_type", list(EcfType))
def test_dgii_s_limits_are_the_draft_s(ecf_type: EcfType) -> None:
    format_ = _ecf(ecf_type)
    for element, limit in _TEXTS.items():
        assert all(int(facet["maxLength"]) == limit for facet in _facets(format_, element)), element
    for element, (integers, places) in _NUMBERS.items():
        for facet in _facets(format_, element):
            found = _INTEGER_PART.match(facet["pattern"])
            assert found is not None, element
            assert (int(found.group(1)), int(facet["fractionDigits"])) == (integers, places), element
    if _declared(format_, "TipoIngresos"):
        assert _enumeration(format_, "TipoIngresosValidationType") == {each.value for each in IncomeType}
    assert _enumeration(format_, "TipoPagoType") == {str(each.value) for each in PaymentType}
    if _declared(format_, "IndicadorAgenteRetencionoPercepcion"):
        roles = _enumeration(format_, "IndicadorAgenteRetencionoPercepcionType")
        assert roles == {str(each.value) for each in WithholdingRole}


def test_the_foreign_currencies_are_those_dgii_lists_that_money_knows() -> None:
    listed = _enumeration(Format.ECF_31, "TipoMonedaType")
    assert {code for code in listed if code in CURRENCIES} == FOREIGN_CURRENCIES
    assert all(_enumeration(_ecf(each), "TipoMonedaType") == listed for each in EcfType)


def test_the_modification_codes_are_dgii_s() -> None:
    expected = {str(code.value) for code in ModificationCode}
    referring = [each for each in EcfType if _declared(_ecf(each), "CodigoModificacion")]
    assert set(NOTE_TYPES) <= set(referring)
    assert all(_enumeration(_ecf(each), "CodigoModificacionType") == expected for each in referring)


def _max_occurs(format_: Format, name: str) -> int:
    (declared,) = (
        found
        for found in etree.fromstring(DO_SCHEMAS.corrected(format_)).iter(f"{XS}element")
        if found.get("name") == name
    )
    return int(declared.get("maxOccurs", "1"))


def test_the_annulment_s_limits_are_its_schema_s() -> None:
    assert _max_occurs(Format.ANECF, "Anulacion") == ANNULMENT_TYPES == len(EcfType)
    assert _max_occurs(Format.ANECF, "Secuencias") == ANNULMENT_RANGES
    assert _enumeration(Format.ANECF, "CFType") == {each.value for each in EcfType}
    counted = {facet["pattern"] for facet in _facets(Format.ANECF, "CantidadeNCFAnulados")}
    assert counted == {r"(\d{1,10})"}
    assert str(ANNULMENT_COUNT) == "9" * 10  # the largest number of ten digits


def test_the_approval_s_states_and_limits_are_its_schema_s() -> None:
    assert _enumeration(Format.ACECF, "EstadoType") == {acecf.APPROVED, acecf.REJECTED}
    (reason,) = _facets(Format.ACECF, "DetalleMotivoRechazo")
    assert int(reason["maxLength"]) == REJECTION_REASON
    (total,) = _facets(Format.ACECF, "MontoTotal")
    assert int(total["fractionDigits"]) == APPROVAL_TOTAL_PLACES
    # The ACECF's own type is wider than what it names: 18 digits in all, so 17 or 18 integer digits when the
    # cents are zero. An approval names an e-CF, whose MontoTotal has 16 at most: that is the cap.
    assert int(total["totalDigits"]) == 18
    for ecf_type in EcfType:
        for facet in _facets(_ecf(ecf_type), "MontoTotal"):
            found = _INTEGER_PART.match(facet["pattern"])
            assert found is not None
            assert int(found.group(1)) == APPROVAL_TOTAL_DIGITS


def test_the_receipt_s_states_and_reasons_are_its_schema_s() -> None:
    assert _enumeration(Format.ARECF, "EstadoType") == {arecf.RECEIVED, arecf.NOT_RECEIVED}
    reasons = _enumeration(Format.ARECF, "CodigoMotivoNoRecibidoType")
    assert reasons == {str(reason.value) for reason in NotReceivedReason}
