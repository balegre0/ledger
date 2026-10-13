"""The ten e-CF types (FMT; spec §8.6.2): kind, direction, and whether the buyer's tax id is required.

``requires_buyer_tax_id`` is what the e-CF schemas demand (``RNCComprador`` required in types 31, 41 and 45);
for a purchase (41, 43, 47) the "buyer" block names the seller the company self-bills. Corrections (which
types a note may modify) are v2-M3's.
"""

from typing import Final

from ledger_country_do.regime.definitions import Direction, DocumentKind, DocumentTypeDef
from ledger_country_do.regime.sources import FORMAT_ECF

_I, _C, _D = DocumentKind.INVOICE, DocumentKind.CREDIT_NOTE, DocumentKind.DEBIT_NOTE
_S, _P = Direction.SALES, Direction.PURCHASE


def _type(code: str, label: str, kind: DocumentKind, direction: Direction, *, buyer: bool) -> DocumentTypeDef:
    return DocumentTypeDef(
        code=code, label=label, kind=kind, direction=direction, requires_buyer_tax_id=buyer, source=FORMAT_ECF
    )


TYPES: Final = (
    _type("31", "Factura de Crédito Fiscal Electrónica", _I, _S, buyer=True),
    _type("32", "Factura de Consumo Electrónica", _I, _S, buyer=False),
    _type("33", "Nota de Débito Electrónica", _D, _S, buyer=False),
    _type("34", "Nota de Crédito Electrónica", _C, _S, buyer=False),
    _type("41", "Comprobante Electrónico de Compras", _I, _P, buyer=True),
    _type("43", "Comprobante Electrónico para Gastos Menores", _I, _P, buyer=False),
    _type("44", "Comprobante Electrónico para Regímenes Especiales", _I, _S, buyer=False),
    _type("45", "Comprobante Electrónico Gubernamental", _I, _S, buyer=True),
    _type("46", "Comprobante Electrónico para Exportaciones", _I, _S, buyer=False),
    _type("47", "Comprobante Electrónico para Pagos al Exterior", _I, _P, buyer=False),
)
