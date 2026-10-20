"""A buyer's receipt of an e-CF (ARECF, DGII's ``arecf.xsd``): what reception answers each e-CF it is sent.

The receiver names the issuer, itself, the e-NCF, whether it received it (``Estado`` 0 received, 1 not, with
DGII's reason code) and when; it signs the receipt (spec §3.8). The document returned is valid for its
schema.
"""

from typing import Final

from ledger_country_do.channels.ecf_v1.adapters.xml.schema_set import DO_SCHEMAS
from ledger_country_do.channels.ecf_v1.adapters.xml.writer import Node
from ledger_country_do.channels.ecf_v1.adapters.xsd.published import Format
from ledger_country_do.channels.ecf_v1.domain.notation import dgii_moment
lazy from ledger_country_do.channels.ecf_v1.domain.exchanges import Receipt

VERSION: Final = "1.0"
RECEIVED: Final = "0"
NOT_RECEIVED: Final = "1"


def render_arecf(receipt: Receipt) -> bytes:
    """The unsigned ARECF of ``receipt``."""
    root = Node.root(Format.ARECF, "arecf")
    detail = root.child("detalleacusederecibo")
    detail.text("version", VERSION)
    detail.text("rncemisor", receipt.issuer_rnc)
    detail.text("rnccomprador", receipt.buyer_rnc)
    detail.text("encf", str(receipt.encf))
    detail.text("estado", RECEIVED if receipt.received else NOT_RECEIVED)
    detail.number("codigomotivonorecibido", None if receipt.reason is None else receipt.reason.value)
    detail.text("fechahoraacuserecibo", dgii_moment(receipt.at))
    return DO_SCHEMAS.checked(Format.ARECF, root.document(), unsigned=True)
