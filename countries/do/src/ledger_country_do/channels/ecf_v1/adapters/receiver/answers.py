"""What the receiver answers (DT-EMI): a seed in DGII's ``SemillaModel``, a token, a signed receipt.

A seed is answered as DGII's own services write one (``valor``, then ``fecha`` in Santo Domingo's offset);
a token as ``{token, expira, expedido}`` (UTC, ``yyyy-MM-ddTHH:mm:ssZ``, as DGII's), in JSON unless the
caller asks for XML only (``Accept`` naming XML and not JSON: ``<RespuestaAutenticacion>`` with the same three
elements, a shape DGII does not document; **unconfirmed**, to check on TesteCF); a receipt as the signed
ARECF's bytes, ``application/xml``.
"""

from datetime import UTC
from typing import Final
from xml.sax.saxutils import escape
lazy from datetime import datetime

from fastapi.responses import JSONResponse, Response

from ledger_country_do.channels.ecf_v1.domain.notation import SANTO_DOMINGO
lazy from ledger_country_do.channels.ecf_v1.adapters.receiver.tokens import Token

XML: Final = "application/xml"
_UTC: Final = "%Y-%m-%dT%H:%M:%SZ"
_SEED_HEAD: Final = (
    '<?xml version="1.0" encoding="utf-8"?><SemillaModel xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
    ' xmlns:xsd="http://www.w3.org/2001/XMLSchema">'
)


def _utc(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime(_UTC)


def seed_answer(value: str, at: datetime) -> Response:
    """The ``SemillaModel`` of a seed ``value`` made ``at``."""
    fecha = at.astimezone(SANTO_DOMINGO).isoformat()
    body = f"{_SEED_HEAD}<valor>{escape(value)}</valor><fecha>{fecha}</fecha></SemillaModel>"
    return Response(body.encode(), media_type=XML)


def token_answer(token: Token, *, accept: str) -> Response:
    """``token`` as the caller asked for it (see the module)."""
    expires, issued = _utc(token.expires_at), _utc(token.issued_at)
    wanted = accept.lower()
    if "xml" in wanted and "json" not in wanted:
        body = (
            '<?xml version="1.0" encoding="utf-8"?><RespuestaAutenticacion>'
            f"<token>{escape(token.value)}</token><expira>{expires}</expira><expedido>{issued}</expedido>"
            "</RespuestaAutenticacion>"
        )
        return Response(body.encode(), media_type=XML)
    return JSONResponse({"token": token.value, "expira": expires, "expedido": issued})


def receipt_answer(signed: bytes) -> Response:
    """A signed ARECF, as it was signed."""
    return Response(signed, media_type=XML)
