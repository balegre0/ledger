"""The ``EcfFormat`` port over DGII's formats: written, checked against DGII's schema, signature verified.

A document is checked unsigned, as it will be once signed, before the signer sees it, and checked again once
signed, its signature verified by the pack's own verifier (DGII's profile, strict), before it is sent: a
document DGII's schema refuses never leaves, and neither does a signature that does not hold.
"""

from typing import final, override
lazy from datetime import datetime

from ledger_country_do.channels.ecf_v1.adapters.signing.verifier import verify_enveloped
from ledger_country_do.channels.ecf_v1.adapters.xml import ecf, rfce
from ledger_country_do.channels.ecf_v1.adapters.xml.schema_set import DO_SCHEMAS
from ledger_country_do.channels.ecf_v1.adapters.xsd.published import Format
from ledger_country_do.channels.ecf_v1.application.ports import EcfFormat
lazy from ledger_country_do.channels.ecf_v1.domain.drafts import EcfDraft
lazy from ledger_country_do.channels.ecf_v1.domain.totals import Totals


@final
class DgiiXml(EcfFormat):
    """DGII's formats (see the module)."""

    __slots__ = ()

    @override
    def ecf(self, draft: EcfDraft, totals: Totals, *, signed_at: datetime) -> bytes:
        return DO_SCHEMAS.checked(
            ecf.FORMAT_OF[draft.ecf_type], ecf.render(draft, totals, signed_at=signed_at), unsigned=True
        )

    @override
    def rfce(self, draft: EcfDraft, totals: Totals, *, security_code: str) -> bytes:
        return DO_SCHEMAS.checked(
            Format.RFCE, rfce.render(draft, totals, security_code=security_code), unsigned=True
        )

    @override
    def signed(self, draft: EcfDraft, signed: bytes, *, rfce: bool) -> str:
        DO_SCHEMAS.checked(Format.RFCE if rfce else ecf.FORMAT_OF[draft.ecf_type], signed, unsigned=False)
        return verify_enveloped(signed).signature_value
