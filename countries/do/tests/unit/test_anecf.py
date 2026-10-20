from datetime import UTC, datetime

import pytest

from ledger_country_do.channels.ecf_v1.adapters.xml.anecf import render_anecf
from ledger_country_do.channels.ecf_v1.adapters.xml.schema_set import DO_SCHEMAS
from ledger_country_do.channels.ecf_v1.adapters.xsd.published import Format
from ledger_country_do.channels.ecf_v1.domain.encf import EcfType
from ledger_country_do.channels.ecf_v1.domain.exchanges import ExchangeInvalidError, VoidedRange
from ledger_country_do.channels.ecf_v1.domain.notation import NaiveMomentError
from ledger_fiscal_kit.xml.hardened import texts

AT = datetime(2026, 10, 13, 14, 0, tzinfo=UTC)
RANGES = (
    VoidedRange(EcfType.CONSUMER_INVOICE, 1001, 1500),
    VoidedRange(EcfType.CREDIT_INVOICE, 200, 200),
    VoidedRange(EcfType.CREDIT_INVOICE, 51, 100),
)
FIRST = "DetalleAnulacion/Anulacion[1]"
SECOND = "DetalleAnulacion/Anulacion[2]"
TABLE = "TablaRangoSecuenciasAnuladaseNCF/Secuencias"


def test_an_anecf_counts_the_numbers_it_voids_by_type_and_in_all() -> None:
    document = render_anecf(issuer_rnc="101850043", ranges=RANGES, at=AT)
    assert DO_SCHEMAS.violations(Format.ANECF, document, unsigned=True) == ()
    assert texts(
        document,
        "Encabezado/Version",
        "Encabezado/RncEmisor",
        "Encabezado/CantidadeNCFAnulados",
        "Encabezado/FechaHoraAnulacioneNCF",
    ) == ("1.0", "101850043", "551", "13-10-2026 10:00:00")
    assert texts(
        document,
        f"{FIRST}/NoLinea",
        f"{FIRST}/TipoeCF",
        f"{FIRST}/{TABLE}[1]/SecuenciaeNCFDesde",
        f"{FIRST}/{TABLE}[1]/SecuenciaeNCFHasta",
        f"{FIRST}/{TABLE}[2]/SecuenciaeNCFDesde",
        f"{FIRST}/{TABLE}[2]/SecuenciaeNCFHasta",
        f"{FIRST}/CantidadeNCFAnulados",
    ) == ("1", "31", "E310000000051", "E310000000100", "E310000000200", "E310000000200", "51")
    assert texts(
        document,
        f"{SECOND}/NoLinea",
        f"{SECOND}/TipoeCF",
        f"{SECOND}/{TABLE}/SecuenciaeNCFDesde",
        f"{SECOND}/{TABLE}/SecuenciaeNCFHasta",
        f"{SECOND}/CantidadeNCFAnulados",
    ) == ("2", "32", "E320000001001", "E320000001500", "500")


@pytest.mark.parametrize("rnc", ["10185004", "101-85004-3", "1018500431", "x01850043"])
def test_an_anecf_names_its_issuer_by_an_rnc_or_cedula_never_quoted(rnc: str) -> None:
    with pytest.raises(ExchangeInvalidError) as raised:
        render_anecf(issuer_rnc=rnc, ranges=RANGES, at=AT)
    assert raised.value.field == "issuer_rnc"
    assert rnc not in str(raised.value)


def test_an_anecf_voids_something() -> None:
    with pytest.raises(ExchangeInvalidError, match="at least one range"):
        render_anecf(issuer_rnc="101850043", ranges=(), at=AT)


def test_an_anecf_is_dated_with_a_moment_that_carries_its_offset() -> None:
    with pytest.raises(NaiveMomentError):
        render_anecf(issuer_rnc="101850043", ranges=RANGES, at=AT.replace(tzinfo=None))
