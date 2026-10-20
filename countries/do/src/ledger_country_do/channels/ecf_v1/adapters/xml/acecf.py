"""A buyer's commercial approval or rejection of an e-CF it received (ACECF, DGII's ``acecf.xsd``).

The buyer names the e-CF (issuer, e-NCF, date, total), itself, its decision (``Estado`` 1 approved, 2
rejected), on a rejection why, and when it decided; it signs the document and sends it to the issuer and to
DGII. DGII's schema leaves the reason optional; an approval this channel sends always gives one when it
rejects (Task 23), in a text DGII's can hold. The document returned is valid for its schema.
"""

from typing import Final

from ledger_country_do.channels.ecf_v1.adapters.xml.schema_set import DO_SCHEMAS
from ledger_country_do.channels.ecf_v1.adapters.xml.writer import Node
from ledger_country_do.channels.ecf_v1.adapters.xsd.published import Format
from ledger_country_do.channels.ecf_v1.domain.drafts import text_violation
from ledger_country_do.channels.ecf_v1.domain.exchanges import (
    REASON,
    REASON_MISSING,
    REJECTION_REASON,
    ExchangeInvalidError,
)
from ledger_country_do.channels.ecf_v1.domain.notation import dgii_amount, dgii_date, dgii_moment
lazy from ledger_country_do.channels.ecf_v1.domain.exchanges import CommercialApproval

VERSION: Final = "1.0"
APPROVED: Final = "1"
REJECTED: Final = "2"


def _require_reason(approval: CommercialApproval) -> None:
    if approval.approved:
        return
    if approval.reason is None:
        raise ExchangeInvalidError(REASON, REASON_MISSING)
    violation = text_violation(approval.reason, REJECTION_REASON)
    if violation is not None:
        raise ExchangeInvalidError(REASON, violation.requirement)


def render_acecf(approval: CommercialApproval) -> bytes:
    """The unsigned ACECF of ``approval``.

    ``ExchangeInvalidError`` (``reason``) for a rejection that gives no reason, or one too long, blank, or
    holding a character DGII's texts cannot.
    """
    _require_reason(approval)
    root = Node.root(Format.ACECF, "acecf")
    detail = root.child("detalleaprobacioncomercial")
    detail.text("version", VERSION)
    detail.text("rncemisor", approval.issuer_rnc)
    detail.text("encf", str(approval.encf))
    detail.text("fechaemision", dgii_date(approval.issued_on))
    detail.text("montototal", dgii_amount(approval.total))
    detail.text("rnccomprador", approval.buyer_rnc)
    detail.text("estado", APPROVED if approval.approved else REJECTED)
    detail.text("detallemotivorechazo", approval.reason)
    detail.text("fechahoraaprobacioncomercial", dgii_moment(approval.at))
    return DO_SCHEMAS.checked(Format.ACECF, root.document(), unsigned=True)
