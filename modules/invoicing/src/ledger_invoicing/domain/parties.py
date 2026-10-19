"""The parties of a document, as snapshots it keeps for ever (§7.2, Decision 7).

The seller is the company as identity knew it when the document was issued: its legal name and primary tax
id. The buyer is what the client sent: ``{tax_id?, legal_name, address?, email?}``, with the tax id read by
the installed regimes' schemes before it gets here. A party is personal data: its ``repr`` shows its tax id
masked and nothing else.
"""

from dataclasses import dataclass
from typing import final, override

from ledger_invoicing.domain.text import parse_address, parse_email, parse_legal_name
lazy from ledger_invoicing.domain.text import Address, Email, LegalName
lazy from ledger_kernel.tax_ids import TaxId


@final
@dataclass(frozen=True, slots=True, kw_only=True, repr=False)
class Seller:
    """The company that issued the document, as it was then; its ``repr`` shows its tax id masked only."""

    legal_name: str
    tax_id: TaxId
    """Its primary tax id."""

    @override
    def __repr__(self) -> str:
        return f"Seller(tax_id={self.tax_id!r})"


@final
@dataclass(frozen=True, slots=True, kw_only=True, repr=False)
class Party:
    """The other party of a document: the buyer of a sale, the supplier a purchase is self-billed for."""

    legal_name: LegalName
    tax_id: TaxId | None
    address: Address | None
    email: Email | None

    @override
    def __repr__(self) -> str:
        return f"Party(tax_id={self.tax_id!r})"


def party(
    *,
    field: str = "buyer",
    legal_name: str,
    tax_id: TaxId | None = None,
    address: str | None = None,
    email: str | None = None,
) -> Party:
    """A party from what a client sent; ``field`` is how a refusal names it (``buyer.email``)."""
    return Party(
        legal_name=parse_legal_name(legal_name, field=f"{field}.legal_name"),
        tax_id=tax_id,
        address=None if address is None else parse_address(address, field=f"{field}.address"),
        email=None if email is None else parse_email(email, field=f"{field}.email"),
    )
