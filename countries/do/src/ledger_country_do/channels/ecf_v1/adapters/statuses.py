"""A document's ``StatusUpdate`` (fiscal-kit): what ``fiscal_channel.document.updated`` and ``Issued`` carry.

The state is the channel's own (``signed``...), the fiscal status and severity ``ecf.status_of``'s, the
authority number the e-NCF, the messages DGII's about it. ``details["do"]`` is what a client prints and
follows: the e-NCF, the route, delivery to the buyer, once signed its moment of signing (ISO 8601), security
code, QR URL and emission mode (``None`` before), and DGII's track id once it gave one (``None`` before, and
for a summary). The buyer's commercial approval joins it with the step that records it (Task 23).
"""

from typing import Final

from ledger_country_do.channels.ecf_v1.domain.channel import KEY
from ledger_country_do.channels.ecf_v1.domain.ecf import status_of
from ledger_fiscal_kit.channels import StatusUpdate
lazy from ledger_country_do.channels.ecf_v1.application.documents import EcfDocument
lazy from ledger_platform.data.codec import JsonValue

REGIME_KEY: Final = "do"
"""The key ``details`` are under: the regime's."""


def update_of(document: EcfDocument) -> StatusUpdate:
    """``document``'s state at its version (see the module)."""
    status, severity = status_of(document.state)
    signature = document.signature
    details: dict[str, JsonValue] = {
        "encf": str(document.encf),
        "route": document.route.value,
        "delivery": document.delivery.value,
        "signed_at": None if signature is None else signature.signed_at.isoformat(),
        "security_code": None if signature is None else signature.security_code,
        "qr_url": None if signature is None else signature.qr_url,
        "emission_mode": None if signature is None else signature.emission_mode.value,
        "track_id": document.track_id,
    }
    return StatusUpdate(
        document=document.invoice,
        channel=KEY,
        environment=document.environment.value,
        state=document.state.value,
        status=status,
        severity=severity,
        version=document.version,
        authority_number=str(document.encf),
        messages=document.messages,
        details={REGIME_KEY: details},
        occurred_at=document.updated_at,
    )
