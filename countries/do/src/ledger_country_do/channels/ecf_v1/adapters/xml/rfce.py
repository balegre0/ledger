"""The summary of a consumer invoice under DOP 250,000 (RFCE), which DGII receives instead of the e-CF.

*Formato Resumen Factura Consumo Electrónica v1.0*: the invoice's identification, issuer, buyer and totals (no
rates), and the security code of the full, signed e-CF (``CodigoSeguridadeCF``), which the company keeps (IT
§9.1). The summary is signed in its turn.
"""

from typing import ClassVar, Final

from ledger_country_do.channels.ecf_v1.adapters.xml.ecf import require_written, write_buyer, write_totals
from ledger_country_do.channels.ecf_v1.adapters.xml.writer import Node
from ledger_country_do.channels.ecf_v1.adapters.xsd.published import Format
from ledger_country_do.channels.ecf_v1.domain.encf import EcfType
from ledger_country_do.channels.ecf_v1.domain.errors import EcfError
from ledger_country_do.channels.ecf_v1.domain.notation import dgii_date
lazy from ledger_country_do.channels.ecf_v1.domain.drafts import EcfDraft
lazy from ledger_country_do.channels.ecf_v1.domain.totals import Totals

VERSION: Final = "1.0"


class RfceTypeError(EcfError):
    """Only a type 32 has a summary: asking for another's is a defect of the caller."""

    code: ClassVar[str] = "do_ecf_rfce_type"

    def __init__(self, ecf_type: EcfType) -> None:
        super().__init__(f"an RFCE summarizes a type 32, not a type {ecf_type.value}")


def render(draft: EcfDraft, totals: Totals, *, security_code: str) -> bytes:
    """The unsigned RFCE of ``draft``, a type 32 whose full e-CF was signed with ``security_code``."""
    if draft.ecf_type is not EcfType.CONSUMER_INVOICE:
        raise RfceTypeError(draft.ecf_type)
    require_written(draft)
    root = Node.root(Format.RFCE, "rfce")
    header = root.child("encabezado")
    header.text("version", VERSION)
    ident = header.child("iddoc")
    ident.text("tipoecf", draft.ecf_type.value)
    ident.text("encf", str(draft.encf))
    ident.text("tipoingresos", draft.income.value)
    ident.number("tipopago", draft.payment.value)
    issuing = header.child("emisor")
    issuing.text("rncemisor", draft.issuer.rnc)
    issuing.text("razonsocialemisor", draft.issuer.legal_name)
    issuing.text("fechaemision", dgii_date(draft.issued_on))
    write_buyer(header, draft.buyer)
    write_totals(header, totals, rates=False)
    header.text("codigoseguridadecf", security_code)
    return root.document()
