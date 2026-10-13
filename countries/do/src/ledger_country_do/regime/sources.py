"""Where regime ``do``'s values come from: one ``Source`` per document, cited by the values that use it.

DGII's documents were fetched on 2026-10-12 from https://dgii.gov.do (index: "Documentación sobre e-CF");
`.superpowers/research/06-dgii-facts.md` lists them with their versions.
"""

from datetime import date
from typing import Final

from ledger_country_do.regime.definitions import Source

RETRIEVED: Final = date(2026, 10, 13)
COVERED_FROM: Final = date(2019, 1, 1)
"""The first day the pack asserts values for: channel do-ecf-v1's ``valid_from`` (spec §4.3)."""
STDNUM_COMMIT: Final = "006192e59be8ed6e08fd680256868a6c31eb2ba9"
"""python-stdnum's ``master`` as read on 2026-10-13 (``git ls-remote``): the version the checks follow."""
_STDNUM: Final = f"https://github.com/arthurdejong/python-stdnum/blob/{STDNUM_COMMIT}/stdnum/do/"
_DGII_ECF: Final = "https://dgii.gov.do/cicloContribuyente/facturacion/comprobantesFiscalesElectronicosE-CF/Documentacin%20sobre%20eCF/"
_FETCHED: Final = date(2026, 10, 12)

STDNUM_RNC: Final = Source(
    title="python-stdnum, stdnum.do.rnc: the RNC's check digit (weights 7 9 8 6 5 4 3 2, modulo 11)",
    url=_STDNUM + "rnc.py",
    version=f"commit {STDNUM_COMMIT}; DGII publishes no algorithm (secondary source)",
    retrieved_on=RETRIEVED,
)
STDNUM_CEDULA: Final = Source(
    title="python-stdnum, stdnum.do.cedula: the cédula's check digit (Luhn) and DGII's numbers that fail it",
    url=_STDNUM + "cedula.py",
    version=f"commit {STDNUM_COMMIT}; DGII publishes no algorithm (secondary source)",
    retrieved_on=RETRIEVED,
)
FORMAT_ECF: Final = Source(
    title="DGII, Formato Comprobante Fiscal Electrónico (e-CF) V1.0",
    url=_DGII_ECF + "Formatos%20XML/Formato%20Comprobante%20Fiscal%20Electr%C3%B3nico%20(e-CF)%20V1.0.pdf",
    version="Versión 1.0, Octubre 2025",
    retrieved_on=_FETCHED,
)
TECHNICAL_REPORT: Final = Source(
    title="DGII, Informe Técnico e-CF v1.0 (§12 tolerancia, §13 redondeo, §18 representación impresa)",
    url=_DGII_ECF + "Informe%20y%20Descripci%C3%B3n%20T%C3%A9cnica/Informe%20T%C3%A9cnico%20e-CF%20v1.0.pdf",
    version="changelog 23-03-2026",
    retrieved_on=_FETCHED,
)
SERVICES_DGII: Final = Source(
    title="DGII, Descripción Técnica Servicios DGII (recepción, RFCE, consultas)",
    url=_DGII_ECF
    + "Informe%20y%20Descripci%C3%B3n%20T%C3%A9cnica/Descripcion%20Tecnica%20Servicios%20DGII.pdf",
    version="changelog 02-01-2026",
    retrieved_on=_FETCHED,
)
DECREE_587_24: Final = Source(
    title="Decreto 587-24, Reglamento de aplicación de la Ley 32-23 (Arts. 39-43, contingencia)",
    url="https://dgii.gov.do/legislacion/decretos/Documents/2024/Decreto587-24.pdf",
    version="10-10-2024",
    retrieved_on=_FETCHED,
)
ALL: Final = (STDNUM_RNC, STDNUM_CEDULA, FORMAT_ECF, TECHNICAL_REPORT, SERVICES_DGII, DECREE_587_24)
