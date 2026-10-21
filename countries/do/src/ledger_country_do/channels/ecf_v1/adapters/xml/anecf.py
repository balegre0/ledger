"""The annulment of unused e-NCF before DGII (ANECF, DGII's ``anecf.xsd``), signed and sent by the issuer.

The header counts every number voided and dates the annulment; one ``Anulacion`` per e-CF type lists that
type's ranges, each from its first e-NCF to its last, and counts them (``exchanges.annulments`` groups and
checks the ranges). The document returned is valid for its schema with the signature it will carry; the
signer adds that signature last.
"""

from typing import Final
lazy from collections.abc import Sequence
lazy from datetime import datetime

from ledger_country_do.channels.ecf_v1.adapters.xml.schema_set import DO_SCHEMAS
from ledger_country_do.channels.ecf_v1.adapters.xml.writer import Node
from ledger_country_do.channels.ecf_v1.adapters.xsd.published import Format
from ledger_country_do.channels.ecf_v1.domain.encf import Encf
from ledger_country_do.channels.ecf_v1.domain.exchanges import annulments, require_tax_id
from ledger_country_do.channels.ecf_v1.domain.notation import dgii_moment
lazy from ledger_country_do.channels.ecf_v1.domain.exchanges import VoidedRange

VERSION: Final = "1.0"


def render_anecf(*, issuer_rnc: str, ranges: Sequence[VoidedRange], at: datetime) -> bytes:
    """The unsigned ANECF of ``issuer_rnc`` voiding ``ranges`` at ``at``.

    ``ExchangeInvalidError`` for an issuer that is no RNC or cédula, or ranges ``annulments`` refuses;
    ``NaiveMomentError`` for a moment without its UTC offset.
    """
    require_tax_id("issuer_rnc", issuer_rnc)
    grouped = annulments(ranges)
    root = Node.root(Format.ANECF, "anecf")
    header = root.child("encabezado")
    header.text("version", VERSION)
    header.text("rncemisor", issuer_rnc)
    header.number("cantidadencfanulados", sum(each.count for each in grouped))
    header.text("fechahoraanulacionencf", dgii_moment(at))
    detail = root.child("detalleanulacion")
    for line, annulment in enumerate(grouped, start=1):
        voiding = detail.child("anulacion")
        voiding.number("nolinea", line)
        voiding.text("tipoecf", annulment.ecf_type.value)
        table = voiding.child("tablarangosecuenciasanuladasencf")
        for voided in annulment.ranges:
            sequences = table.child("secuencias")
            sequences.text("secuenciaencfdesde", str(Encf(voided.ecf_type, voided.first)))
            sequences.text("secuenciaencfhasta", str(Encf(voided.ecf_type, voided.last)))
        voiding.number("cantidadencfanulados", annulment.count)
    return DO_SCHEMAS.checked(Format.ANECF, root.document(), unsigned=True)
