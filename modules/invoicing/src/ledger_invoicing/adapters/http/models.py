"""The shapes of invoicing's public API: what clients send (unknown fields refused) and what they receive.

Ids are TypeIDs (``inv_…``, §9.4), amounts and rates are strings (§9.7), dates ISO 8601, times with their
offset (UTC), enumerations their lowercase words. A request's limits are in its schema, so the OpenAPI
document states them (a document's 1 to 1000 lines, an amount's places); the domain checks them again, with
what only it can check (a quantity above 0, a category of the company's regime). A document is answered as
§9.3 envelopes it: ``id``, ``object``, ``livemode``, ``created`` and ``updated``.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, ClassVar, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from ledger_invoicing.adapters.http.public import InvoiceRef, invoice_ref, parse_invoice_ref
from ledger_invoicing.application.commands import Creation
from ledger_invoicing.domain.fiscal import FiscalStatusName
from ledger_invoicing.domain.invoice import InvoiceStatus
from ledger_invoicing.domain.lines import MAX_PRICE_PLACES, MAX_QUANTITY_PLACES, LineTerms
from ledger_invoicing.domain.parties import party
from ledger_invoicing.domain.pricing import (
    MAX_EXCHANGE_RATE_PLACES,
    MAX_FIELD_OBJECT,
    MAX_FIELD_TEXT,
    MAX_LINES,
)
from ledger_invoicing.domain.text import (
    MAX_ADDRESS,
    MAX_DESCRIPTION,
    MAX_EMAIL,
    MAX_EXTERNAL_REFERENCE,
    MAX_LEGAL_NAME,
    MAX_REASON,
    parse_external_reference,
    parse_reason,
)
from ledger_kernel.money import MAX_INTEGER_DIGITS, Currency
from ledger_kernel.regimes import Direction, DocumentKind, TaxCategoryKind
from ledger_kernel.tax_ids import MAX_TAX_ID_LENGTH
from ledger_kernel.tenancy import Mode
lazy from ledger_invoicing.application.queries import Kept
lazy from ledger_invoicing.domain.fiscal import FiscalStatus
lazy from ledger_invoicing.domain.parties import Party, Seller
lazy from ledger_invoicing.domain.pricing import PricedLine
lazy from ledger_invoicing.domain.text import Reason
lazy from ledger_kernel.money import Money
lazy from ledger_kernel.regimes import CategoryTotal, TaxIdSchemes, Totals
lazy from ledger_kernel.tax_ids import TaxId

MAX_TAXES: Final = 10
"""Categories a line names at most: one of each tax, and no regime has as many taxes."""
MAX_NAMESPACES: Final = 5
"""Regimes ``fiscal`` names at most: the company's only, in fact (``field_unknown`` for any other)."""
MAX_FIELDS: Final = 50
"""Country fields of a regime a request sends at most."""
MAX_FIELD_NAME: Final = 40
CODE_LENGTH: Final = 10
"""An establishment's code: 1 to 10 upper-case letters and digits, as identity keeps them."""


def _decimal(places: int) -> str:
    """A plain decimal from 0, below 10^18, with at most ``places`` decimal places."""
    return rf"^[0-9]{{1,{MAX_INTEGER_DIGITS}}}(\.[0-9]{{1,{places}}})?$"


type Quantity = Annotated[str, StringConstraints(pattern=_decimal(MAX_QUANTITY_PLACES))]
type Amount = Annotated[str, StringConstraints(pattern=_decimal(MAX_PRICE_PLACES))]
type Rate = Annotated[str, StringConstraints(pattern=_decimal(MAX_EXCHANGE_RATE_PLACES))]
type CategoryKey = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_.-]{0,63}$")]
type FieldText = Annotated[str, StringConstraints(min_length=1, max_length=MAX_FIELD_TEXT)]
type FieldName = Annotated[str, StringConstraints(min_length=1, max_length=MAX_FIELD_NAME)]
type FieldObject = Annotated[dict[FieldName, FieldText], Field(max_length=MAX_FIELD_OBJECT)]
type CountryField = (
    FieldText | Annotated[dict[FieldName, FieldText | FieldObject], Field(max_length=MAX_LINES)]
)
"""A country field: text, or an object of text fields or of such objects. The regime says which of its own
fields is what (``replaces_ncf``, an object of at most 20 text fields); a channel's (Decision 24) may hold a
field per line and nest one level deeper (DO's ``withholdings``: per line, ``{"itbis", "isr"}``)."""


def _text(amount: Decimal) -> str:
    """An amount as the API writes it: plain, never in exponent form."""
    return format(amount, "f")


class _Shape(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)


class PartyTaxId(_Shape):
    """A tax id qualified by its scheme (§7.3), which says how the value is read and checked."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True, strict=True)

    scheme: str = Field(
        pattern=r"^[a-z]{2}\.[a-z][a-z0-9_]{0,30}$",
        description="A scheme an installed regime declares, <country>.<name>.",
        examples=["do.rnc"],
    )
    value: str = Field(min_length=1, max_length=MAX_TAX_ID_LENGTH, examples=["131-88023-9"])

    @classmethod
    def of(cls, tax_id: TaxId) -> Self:
        """The body of ``tax_id``, as its scheme normalized it."""
        return cls(scheme=tax_id.scheme, value=tax_id.value)


class PartyBody(_Shape):
    """The other party of a document, as the document keeps it for ever (§7.2)."""

    legal_name: str = Field(min_length=1, max_length=MAX_LEGAL_NAME)
    tax_id: PartyTaxId | None = None
    address: str | None = Field(default=None, min_length=1, max_length=MAX_ADDRESS)
    email: str | None = Field(default=None, min_length=1, max_length=MAX_EMAIL)


class LineBody(_Shape):
    """A line as written: what is sold, how much of it, at what price, less what discount, taxed how."""

    description: str = Field(min_length=1, max_length=MAX_DESCRIPTION)
    quantity: Quantity = Field(description="Above 0.", examples=["2", "1.5"])
    unit_price: Amount = Field(examples=["1250.00"])
    discount: Amount = Field(default="0", description="An amount, at most the line's gross.")
    taxes: list[CategoryKey] = Field(
        min_length=1,
        max_length=MAX_TAXES,
        description="The category of each tax charged on the line, of the company's regime.",
        examples=[["do.itbis.standard"]],
    )

    def terms(self) -> LineTerms:
        """The line as the domain reads it."""
        return LineTerms(
            description=self.description,
            quantity=Decimal(self.quantity),
            unit_price=Decimal(self.unit_price),
            discount=Decimal(self.discount),
            taxes=tuple(self.taxes),
        )


class NewInvoice(_Shape):
    """A document to create: issued at once unless ``draft`` (a draft is issued by ``/issue``)."""

    type: DocumentKind
    direction: Direction = Direction.SALES
    draft: bool = Field(default=False, strict=True)
    currency: str | None = Field(
        default=None, pattern=r"^[A-Z]{3}$", description="ISO 4217. Absent: the company's functional one."
    )
    exchange_rate: Rate | None = Field(
        default=None,
        description="Units of the functional currency per one of the document's: only in another currency.",
    )
    issue_date: date | None = Field(default=None, description="Absent: today, in the establishment's zone.")
    tax_point: date | None = Field(
        default=None,
        description=(
            "Absent: the issue date; for a note, the tax point of the invoice it corrects. "
            "Present: on the issue date or at most 400 days before it."
        ),
    )
    establishment: str | None = Field(
        default=None, min_length=1, max_length=CODE_LENGTH, description="Its code. Absent: MAIN."
    )
    buyer: PartyBody | None = None
    lines: list[LineBody] = Field(min_length=1, max_length=MAX_LINES)
    external_reference: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_EXTERNAL_REFERENCE,
        description="Your key for the document: unique per company, mode and type, for ever.",
    )
    corrects: InvoiceRef | None = Field(
        default=None, description="The invoice a credit or debit note corrects."
    )
    fiscal: Annotated[
        dict[FieldName, Annotated[dict[FieldName, CountryField], Field(max_length=MAX_FIELDS)]],
        Field(max_length=MAX_NAMESPACES),
    ] = Field(
        default_factory=dict[FieldName, dict[FieldName, CountryField]],
        description="Country fields, fiscal.<regime>.<field>: only those the company's regime declares.",
    )

    def creation(self, schemes: TaxIdSchemes) -> Creation:
        """What the request asks to create; a buyer's tax id is read with ``schemes``."""
        buyer = self.buyer
        return Creation(
            kind=self.type,
            direction=self.direction,
            lines=tuple(line.terms() for line in self.lines),
            currency=None if self.currency is None else Currency.of(self.currency),
            exchange_rate=None if self.exchange_rate is None else Decimal(self.exchange_rate),
            issue_date=self.issue_date,
            tax_point=self.tax_point,
            establishment=self.establishment,
            buyer=None
            if buyer is None
            else party(
                legal_name=buyer.legal_name,
                tax_id=None
                if buyer.tax_id is None
                else schemes.parse(buyer.tax_id.scheme, buyer.tax_id.value).tax_id,
                address=buyer.address,
                email=buyer.email,
            ),
            fiscal=self.fiscal,
            external_reference=(
                None if self.external_reference is None else parse_external_reference(self.external_reference)
            ),
            corrects=None if self.corrects is None else parse_invoice_ref(self.corrects),
            draft=self.draft,
        )


class VoidBody(_Shape):
    """Why a document is voided, if the caller says."""

    reason: str | None = Field(default=None, min_length=1, max_length=MAX_REASON)

    def parsed(self) -> Reason | None:
        """The reason, cleaned as free text is."""
        return None if self.reason is None else parse_reason(self.reason)


class LineTaxView(_Shape):
    """A tax a line is charged: the rate resolved at the tax point, kept for ever, and what it comes to."""

    category: str = Field(examples=["do.itbis.standard"])
    kind: TaxCategoryKind
    percent: str = Field(examples=["18"])
    since: date = Field(description="When the rate took effect: with the category, it names the rate.")
    amount: str = Field(
        description=(
            "This line's tax alone, as the regime's calculator computes a document of this line only: "
            "rounded where the regime rounds (per category, per line or per document), else exact. The "
            "document's tax per category is in its totals."
        )
    )


class LineView(_Shape):
    """A line as written and as priced."""

    description: str
    quantity: str
    unit_price: str
    discount: str
    gross: str = Field(description="quantity x unit_price, exact.")
    base: str = Field(description="gross - discount, rounded half up to the currency's minor unit.")
    taxes: list[LineTaxView]

    @classmethod
    def of(cls, line: PricedLine) -> Self:
        """The view of ``line``."""
        return cls(
            description=line.terms.description,
            quantity=_text(line.terms.quantity),
            unit_price=_text(line.terms.unit_price),
            discount=_text(line.terms.discount),
            gross=_amount(line.gross),
            base=_amount(line.base),
            taxes=[
                LineTaxView(
                    category=tax.category,
                    kind=tax.kind,
                    percent=_text(tax.percent),
                    since=tax.since,
                    amount=_amount(tax.tax),
                )
                for tax in line.taxes
            ],
        )


def _amount(money: Money) -> str:
    return _text(money.amount)


class CategoryView(_Shape):
    """A category of a document's totals: its base and its tax."""

    category: str
    percent: str
    base: str
    amount: str

    @classmethod
    def of(cls, total: CategoryTotal) -> Self:
        """The view of ``total``."""
        return cls(
            category=total.category,
            percent=_text(total.percent),
            base=_amount(total.base),
            amount=_amount(total.tax),
        )


class TotalsView(_Shape):
    """A document's totals, in ``currency``: ``gross == net + tax``."""

    currency: str
    net: str
    tax: str
    gross: str
    taxes: list[CategoryView]

    @classmethod
    def of(cls, totals: Totals) -> Self:
        """The view of ``totals``."""
        return cls(
            currency=totals.net.currency.code,
            net=_amount(totals.net),
            tax=_amount(totals.tax),
            gross=_amount(totals.gross),
            taxes=[CategoryView.of(total) for total in totals.categories],
        )


class PartyView(_Shape):
    """The other party of a document, as it keeps it."""

    legal_name: str
    tax_id: PartyTaxId | None
    address: str | None
    email: str | None

    @classmethod
    def of(cls, party: Party) -> Self:
        """The view of ``party``."""
        return cls(
            legal_name=party.legal_name,
            tax_id=None if party.tax_id is None else PartyTaxId.of(party.tax_id),
            address=party.address,
            email=party.email,
        )


class SellerView(_Shape):
    """The company that issued the document, as it was then."""

    legal_name: str
    tax_id: PartyTaxId

    @classmethod
    def of(cls, seller: Seller) -> Self:
        """The view of ``seller``."""
        return cls(legal_name=seller.legal_name, tax_id=PartyTaxId.of(seller.tax_id))


class AuthorityMessageView(_Shape):
    """One of the authority's messages, verbatim (§7.10): clients translate by ``code``."""

    code: str
    message: str
    lang: str = Field(examples=["es"])


class FiscalView(BaseModel):
    """What a fiscal channel last reported of the document (§9.8): one entry per channel.

    The channel's own details are under the regime's key (``"do": {"encf": ..., "security_code": ...,
    "qr_url": ...}``): every field a printed representation shows is there, so a client may print its own.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="allow",
        frozen=True,
        json_schema_extra={
            "description": (
                "Besides these, one member named after the regime (do) holds the channel's details: "
                "encf, security_code, signed_at, qr_url, track_id, emission_mode, delivery and the like."
            )
        },
    )
    channel: str = Field(examples=["do-ecf-v1"])
    environment: str = Field(examples=["testecf"])
    status: FiscalStatusName
    version: int = Field(description="The channel's version of this status: a newer one replaces it.")
    messages: list[AuthorityMessageView]

    @classmethod
    def of(cls, status: FiscalStatus) -> Self:
        """The view of ``status``."""
        return cls.model_validate(
            {
                "channel": status.channel,
                "environment": status.environment,
                "status": status.status,
                "version": status.version,
                "messages": [
                    {"code": message.code, "message": message.message, "lang": message.lang}
                    for message in status.messages
                ],
                status.regime: dict(status.details),
            }
        )


class InvoiceView(_Shape):
    """A commercial document at its current version (its ``ETag``): an invoice, a credit or a debit note."""

    id: InvoiceRef
    object: Literal["invoice"] = "invoice"
    livemode: bool
    created: datetime
    updated: datetime
    type: DocumentKind
    direction: Direction
    status: InvoiceStatus
    version: int
    external_reference: str | None
    corrects: InvoiceRef | None
    currency: str
    exchange_rate: str | None
    regime: str
    establishment: str
    issue_date: date
    tax_point: date
    buyer: PartyView | None
    seller: SellerView | None = Field(description="Set once the document is issued.")
    lines: list[LineView]
    totals: TotalsView
    functional_totals: TotalsView | None = Field(
        description="In the company's functional currency, for a document in another one."
    )
    fiscal: list[FiscalView] = Field(
        description="Each fiscal channel's status, by channel: none until a channel reports one."
    )
    issued_at: datetime | None
    voided_at: datetime | None
    void_reason: str | None

    @classmethod
    def of(cls, kept: Kept) -> Self:
        """The view of a document as it is kept, with its fiscal mirror."""
        invoice = kept.invoice
        terms, pricing = invoice.terms, invoice.pricing
        functional = pricing.functional
        return cls(
            id=invoice_ref(invoice.id),
            livemode=invoice.tenant.mode is Mode.LIVE,
            created=invoice.created_at,
            updated=invoice.updated_at,
            type=invoice.kind,
            direction=invoice.direction,
            status=invoice.status,
            version=invoice.version,
            external_reference=invoice.external_reference,
            corrects=None if invoice.corrects is None else invoice_ref(invoice.corrects),
            currency=terms.currency.code,
            exchange_rate=None if terms.exchange_rate is None else _text(terms.exchange_rate),
            regime=pricing.regime,
            establishment=pricing.establishment.code,
            issue_date=pricing.issue_date,
            tax_point=pricing.tax_point,
            buyer=None if terms.buyer is None else PartyView.of(terms.buyer),
            seller=None if invoice.seller is None else SellerView.of(invoice.seller),
            lines=[LineView.of(line) for line in pricing.lines],
            totals=TotalsView.of(pricing.totals),
            functional_totals=None if functional is None else TotalsView.of(functional),
            fiscal=[FiscalView.of(status) for status in kept.fiscal],
            issued_at=invoice.issued_at,
            voided_at=invoice.voided_at,
            void_reason=invoice.void_reason,
        )
