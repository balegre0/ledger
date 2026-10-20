"""A ``FiscalDraft`` as an e-CF: its type, what DGII would refuse in it, its ``EcfDraft``, its totals checked.

``ecf_type_of`` applies regime ``do``'s default (§8.6.2): an invoice is a 31 when its buyer has an RNC or a
cédula and a 32 otherwise, a credit note a 34, a debit note a 33; a purchase names its type
(``fiscal.do.ecf_type``: 41, 43 or 47), and so does a 44, 45 or 46.

``objections`` collects everything DGII's format or rules refuse in a draft invoicing accepted, each an
``Objection`` (code ``do_ecf_<rule>``, the field as the client sent it, the rule; never the value), ordered
by line (the document's own first), then field, then code. Invoicing's limits are wider than DGII's
(quantities of 6 places against 2, prices of 12 against 4, item names of 500 characters against 80): these
are what a client learns only once enrolled. ``ecf_draft`` builds the e-CF of a draft without objections.

``verify_totals`` holds the kernel's totals, which invoicing computed, against DGII's, which the channel
computes from the e-CF draft: within DGII's tolerance (one unit per line, IT §12) in the document's
currency and in pesos, else the draft is a defect (``TotalsDisagreeError``), never sent.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import ClassVar, Final, final
lazy from collections.abc import Mapping
lazy from datetime import date

from ledger_country_do.channels.ecf_v1.domain.drafts import (
    ADDRESS,
    AMOUNT_LIMIT,
    BILLINGS,
    FOREIGN_CURRENCIES,
    FOREIGN_ID,
    INDICATOR_OF,
    LEGAL_NAME,
    NAMED_TYPES,
    NO_DISCOUNT_TYPES,
    NOTE_TYPES,
    PESO,
    BillingIndicator,
    Buyer,
    EcfDraft,
    GoodsOrService,
    Issuer,
    Line,
    LinePart,
    exchange_rate_valid,
    line_violations,
    max_lines,
    text_violation,
)
from ledger_country_do.channels.ecf_v1.domain.encf import EcfType
from ledger_country_do.channels.ecf_v1.domain.errors import EcfError, EcfInvalidError, from_regime
from ledger_country_do.channels.ecf_v1.domain.fields import read_fields
from ledger_country_do.channels.ecf_v1.domain.references import FIELD as CORRECTS
from ledger_country_do.channels.ecf_v1.domain.references import resolve
from ledger_country_do.regime import REGIME
from ledger_country_do.regime.entries import amount, document_type
from ledger_country_do.regime.fields import ECF_TYPE
from ledger_country_do.regime.parameters import RFCE_THRESHOLD
from ledger_country_do.regime.rounding import GLOBAL_TOLERANCE_PER_LINE
from ledger_country_do.regime.tax_ids import DIGITS
from ledger_fiscal_kit.channels import Objection
from ledger_kernel.regimes import Direction, DocumentKind
lazy from ledger_country_do.channels.ecf_v1.domain.drafts import EcfReference
lazy from ledger_country_do.channels.ecf_v1.domain.encf import Encf
lazy from ledger_country_do.channels.ecf_v1.domain.fields import EcfFields
lazy from ledger_country_do.channels.ecf_v1.domain.references import AcceptedEcf
lazy from ledger_country_do.channels.ecf_v1.domain.totals import Totals
lazy from ledger_fiscal_kit.drafts import DocumentRef, FiscalDraft, FiscalLineTax, FiscalParty
lazy from ledger_kernel.regimes import DocumentTypeDef, RegimeDef
lazy from ledger_kernel.regimes import Totals as KernelTotals
lazy from ledger_kernel.tax_ids import TaxId

_ECF_TYPE_FIELD: Final = f"fiscal.do.{ECF_TYPE.name}"
_MODIFICATION_FIELD: Final = "fiscal.do.modification_code"
_LINE_FIELDS: Final = {
    LinePart.NAME: "description",
    LinePart.QUANTITY: "quantity",
    LinePart.UNIT_PRICE: "unit_price",
    LinePart.DISCOUNT: "discount",
}
"""Each part of a line as the client names it; the amount (quantity x unit price) is the line itself."""
_DEFAULT_OF_NOTE: Final = {
    DocumentKind.CREDIT_NOTE: EcfType.CREDIT_NOTE,
    DocumentKind.DEBIT_NOTE: EcfType.DEBIT_NOTE,
}
_OWN: Final = "the document's currency"
_PESOS: Final = "pesos"


class DraftObjectedError(EcfInvalidError):
    """An e-CF was asked of a draft with objections: each is named by its code and field."""

    code: ClassVar[str] = "do_ecf_draft_objected"

    def __init__(self, objections: tuple[Objection, ...]) -> None:
        self.objections: tuple[Objection, ...] = objections
        named = "; ".join(f"{each.code} at {each.field}" for each in objections)
        super().__init__(f"the draft has objections: {named}")


class NumberTypeError(EcfError):
    """A draft was numbered in another type's series: a defect of the caller."""

    code: ClassVar[str] = "do_ecf_number_type"

    def __init__(self, number: EcfType, draft: EcfType) -> None:
        super().__init__(f"an e-NCF of type {number.value} cannot number an e-CF of type {draft.value}")


class TotalsDisagreeError(EcfError):
    """The kernel's totals and DGII's differ beyond DGII's tolerance: a defect, never sent."""

    code: ClassVar[str] = "do_ecf_totals_disagree"

    def __init__(self, side: str, what: str) -> None:
        super().__init__(f"the kernel's totals and DGII's disagree beyond the tolerance, in {side}: {what}")


def _definition(regime: RegimeDef, ecf_type: EcfType) -> DocumentTypeDef:
    return from_regime(lambda: document_type(regime, ecf_type.value))


def _rnc(tax_id: TaxId | None) -> str | None:
    """``tax_id``'s value when it is an RNC or a cédula."""
    return tax_id.value if tax_id is not None and tax_id.scheme in DIGITS else None


def _present(text: str | None) -> str:
    """``text``, or a blank text, which the e-CF draft refuses, when there is none."""
    return "" if text is None else text


def _default_type(draft: FiscalDraft) -> EcfType | None:
    note = _DEFAULT_OF_NOTE.get(draft.kind)
    if note is not None:
        return note
    if draft.direction is Direction.PURCHASE:
        return None
    counterparty = draft.counterparty
    identified = counterparty is not None and _rnc(counterparty.tax_id) is not None
    return EcfType.CREDIT_INVOICE if identified else EcfType.CONSUMER_INVOICE


def ecf_type_of(draft: FiscalDraft) -> EcfType | None:
    """The e-CF type of ``draft`` (see the module); ``None`` when it must name one and names none it takes."""
    fields, _ = read_fields(draft.fields)
    if fields.ecf_type is not None:
        return fields.ecf_type
    return None if ECF_TYPE.name in draft.fields else _default_type(draft)


def _pesos(draft: FiscalDraft) -> KernelTotals | None:
    """``draft``'s totals in pesos, if it has them."""
    if draft.currency == PESO:
        return draft.totals
    functional = draft.functional_totals
    return functional if functional is not None and functional.gross.currency == PESO else None


def _billing(taxes: tuple[FiscalLineTax, ...], regime: RegimeDef) -> BillingIndicator | str:
    """The billing indicator of a line taxed with ``taxes``, or the rule they break."""
    if any(tax.category.startswith(f"{regime.key}.isc.") for tax in taxes):
        return "isc_unsupported"
    indicator = INDICATOR_OF.get(taxes[0].category) if len(taxes) == 1 else None
    return "category_unmapped" if indicator is None else indicator


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class _Reading:
    """A draft as the channel reads it: its fields, type, references, lines' billing, objections."""

    fields: EcfFields
    ecf_type: EcfType | None
    references: tuple[EcfReference, ...]
    billings: tuple[BillingIndicator | None, ...]
    objections: tuple[Objection, ...]


@final
@dataclass(slots=True, kw_only=True)
class _Checker:
    """Reads one draft against DGII's rules, keeping each objection with its line (0: the document's own)."""

    draft: FiscalDraft
    accepted: Mapping[DocumentRef, AcceptedEcf]
    regime: RegimeDef
    found: list[tuple[int, Objection]] = field(default_factory=list)

    def add(self, code: str, path: str, requirement: str, *, line: int = 0) -> None:
        objection = Objection(code=f"do_ecf_{code}", field=path, message=f"{path} {requirement}")
        self.found.append((line, objection))

    def text(self, path: str, text: str | None, limit: int, *, required: bool) -> None:
        if text is None:
            if required:
                self.add("value_required", path, "is given")
            return
        violation = text_violation(text, limit)
        if violation is not None:
            self.add(violation.rule.value, path, violation.requirement)

    def read(self) -> _Reading:
        fields, field_objections = read_fields(self.draft.fields, self.regime)
        self.found.extend((0, each) for each in field_objections)
        self.parties()
        billings = self.lines()
        self.amounts()
        ecf_type = self.chosen_type(fields)
        references: tuple[EcfReference, ...] = ()
        if ecf_type is not None:
            self.buyer_rules(ecf_type)
            self.typed_lines(ecf_type, billings)
            references = self.references(ecf_type, fields)
        ordered = sorted(self.found, key=lambda each: (each[0], each[1].field or "", each[1].code))
        return _Reading(
            fields=fields,
            ecf_type=ecf_type,
            references=references,
            billings=billings,
            objections=tuple(objection for _, objection in ordered),
        )

    def parties(self) -> None:
        issuer = self.draft.issuer
        if _rnc(issuer.tax_id) is None:
            self.add("tax_id_invalid", "seller.tax_id", "is the company's RNC or cédula")
        self.text("seller.legal_name", issuer.legal_name, LEGAL_NAME, required=True)
        self.text("seller.address", issuer.address, ADDRESS, required=True)
        counterparty = self.draft.counterparty
        if counterparty is None:
            return
        self.text("buyer.legal_name", counterparty.legal_name, LEGAL_NAME, required=False)
        self.text("buyer.address", counterparty.address, ADDRESS, required=False)
        tax_id = counterparty.tax_id
        if tax_id is not None and _rnc(tax_id) is None:
            self.text("buyer.tax_id", tax_id.value, FOREIGN_ID, required=True)

    def lines(self) -> tuple[BillingIndicator | None, ...]:
        billings: list[BillingIndicator | None] = []
        for index, line in enumerate(self.draft.lines):
            violations = line_violations(
                name=line.description,
                quantity=line.quantity,
                unit_price=line.unit_price,
                discount=line.discount,
            )
            for part, violation in violations:
                named = _LINE_FIELDS.get(part)
                path = f"lines.{index}" if named is None else f"lines.{index}.{named}"
                self.add(violation.rule.value, path, violation.requirement, line=index + 1)
            billing = _billing(line.taxes, self.regime)
            if isinstance(billing, str):
                path = f"lines.{index}.taxes"
                self.add(billing, path, "is one ITBIS category DGII bills", line=index + 1)
                billings.append(None)
            else:
                billings.append(billing)
        return tuple(billings)

    def amounts(self) -> None:
        draft = self.draft
        pesos = _pesos(draft)
        grosses = (draft.totals.gross.amount, *(() if pesos is None else (pesos.gross.amount,)))
        if any(gross >= AMOUNT_LIMIT for gross in grosses):
            self.add(
                "amount_too_large", "totals", "have at most 16 integer digits, in pesos as in their currency"
            )
        if draft.currency == PESO:
            return
        if draft.currency.code not in FOREIGN_CURRENCIES:
            self.add("currency_unsupported", "currency", "is the peso or a currency DGII lists (TipoMoneda)")
            return
        rate = draft.exchange_rate
        if pesos is None or rate is None:
            self.add("exchange_rate_required", "exchange_rate", "is given, to pesos, for another currency")
        elif not exchange_rate_valid(rate):
            self.add("exchange_rate_invalid", "exchange_rate", "has at most 3 integer digits and 4 decimals")

    def chosen_type(self, fields: EcfFields) -> EcfType | None:
        draft = self.draft
        chosen = fields.ecf_type
        if chosen is None:
            if ECF_TYPE.name in draft.fields:
                return None  # its value is objected to already
            chosen = _default_type(draft)
        if chosen is None:
            self.add("ecf_type_required", _ECF_TYPE_FIELD, "names the purchase's type: 41, 43 or 47")
            return None
        definition = _definition(self.regime, chosen)
        if definition.kind is not draft.kind or definition.direction is not draft.direction:
            self.add(
                "ecf_type_not_allowed", _ECF_TYPE_FIELD, "is a type of the document's kind and direction"
            )
            return None
        return chosen

    def large(self) -> bool:
        """Whether the draft reaches the amount from which DGII receives a consumer invoice in full.

        A draft without totals in pesos is objected to already (``exchange_rate_required``).
        """
        draft = self.draft
        pesos = _pesos(draft)
        threshold = from_regime(lambda: amount(self.regime, RFCE_THRESHOLD, draft.issue_date))
        return pesos is not None and pesos.gross.amount >= threshold

    def buyer_rules(self, ecf_type: EcfType) -> None:
        counterparty = self.draft.counterparty
        identified = counterparty is not None and _rnc(counterparty.tax_id) is not None
        required = _definition(self.regime, ecf_type).requires_buyer_tax_id or (
            ecf_type is EcfType.CONSUMER_INVOICE and self.large()
        )
        if required and not identified:
            self.add(
                "buyer_tax_id_required", "buyer.tax_id", "is the buyer's RNC or cédula on this type or amount"
            )
        if ecf_type in NAMED_TYPES and (counterparty is None or counterparty.legal_name is None):
            self.add("buyer_name_required", "buyer.legal_name", "is given on a type 31, 41, 44, 45 or 46")

    def typed_lines(self, ecf_type: EcfType, billings: tuple[BillingIndicator | None, ...]) -> None:
        draft = self.draft
        most = max_lines(ecf_type)
        if len(draft.lines) > most:
            self.add("line_count", "lines", f"hold at most {most} lines on this type")
        allowed = BILLINGS[ecf_type]
        for index, (line, billing) in enumerate(zip(draft.lines, billings, strict=True)):
            if billing is not None and billing not in allowed:
                self.add(
                    "category_not_allowed", f"lines.{index}.taxes", "is one this type bills", line=index + 1
                )
            if ecf_type in NO_DISCOUNT_TYPES and line.discount != 0:
                self.add(
                    "discount_not_allowed", f"lines.{index}.discount", "is zero on this type", line=index + 1
                )

    def references(self, ecf_type: EcfType, fields: EcfFields) -> tuple[EcfReference, ...]:
        draft = self.draft
        if ecf_type not in NOTE_TYPES:
            if fields.modification is not None:
                self.add("field_invalid", _MODIFICATION_FIELD, "is given on a debit or credit note only")
            return ()
        single = len(draft.references) == 1
        if not single:
            self.add("reference_required", CORRECTS, "names the one invoice a debit or credit note corrects")
        modification = fields.modification
        if modification is None:
            self.add(
                "modification_code_required", _MODIFICATION_FIELD, "says why a debit or credit note corrects"
            )
            return ()
        if not single:
            return ()
        references, objections = resolve(
            draft, self.accepted, ecf_type=ecf_type, code=modification, regime=self.regime
        )
        self.found.extend((0, each) for each in objections)
        return references


def objections(
    draft: FiscalDraft, *, accepted: Mapping[DocumentRef, AcceptedEcf], regime: RegimeDef = REGIME
) -> tuple[Objection, ...]:
    """Everything DGII's format or rules refuse in ``draft`` (see the module).

    ``accepted`` holds the channel's accepted e-CF, in ``draft``'s company and mode, of the documents a note
    names (empty for an invoice).
    """
    return _Checker(draft=draft, accepted=accepted, regime=regime).read().objections


def _buyer(party: FiscalParty | None) -> Buyer:
    if party is None:
        return Buyer()
    rnc = _rnc(party.tax_id)
    foreign = party.tax_id if rnc is None else None
    return Buyer(
        rnc=rnc,
        foreign_id=None if foreign is None else foreign.value,
        name=party.legal_name,
        address=party.address,
    )


def ecf_draft(
    draft: FiscalDraft,
    *,
    number: Encf,
    accepted: Mapping[DocumentRef, AcceptedEcf],
    expires_on: date | None,
    regime: RegimeDef = REGIME,
) -> EcfDraft:
    """The e-CF of ``draft``, numbered ``number`` from a range that expires on ``expires_on``.

    ``DraftObjectedError`` when ``draft`` has objections; ``NumberTypeError`` for another type's number.
    """
    reading = _Checker(draft=draft, accepted=accepted, regime=regime).read()
    if reading.objections or reading.ecf_type is None:
        raise DraftObjectedError(reading.objections)
    if number.ecf_type is not reading.ecf_type:
        raise NumberTypeError(number.ecf_type, reading.ecf_type)
    issuer, fields = draft.issuer, reading.fields
    lines = tuple(
        Line(
            name=line.description,
            quantity=line.quantity,
            unit_price=line.unit_price,
            discount=line.discount,
            billing=BillingIndicator.NOT_BILLABLE if billing is None else billing,
            kind=GoodsOrService.SERVICE if line.goods is False else GoodsOrService.GOODS,
        )
        for line, billing in zip(draft.lines, reading.billings, strict=True)
    )
    return EcfDraft(
        encf=number,
        issued_on=draft.issue_date,
        issuer=Issuer(
            rnc=_present(_rnc(issuer.tax_id)),
            legal_name=_present(issuer.legal_name),
            address=_present(issuer.address),
        ),
        lines=lines,
        buyer=_buyer(draft.counterparty),
        sequence_expires_on=expires_on,
        income=fields.income,
        payment=fields.payment,
        payment_due=fields.payment_due,
        references=reading.references,
        currency=draft.currency,
        exchange_rate=draft.exchange_rate,
    )


def _close(left: Decimal, right: Decimal, tolerance: Decimal) -> bool:
    return abs(left - right) <= tolerance


def _agree(side: str, kernel: KernelTotals, channel: Totals, tolerance: Decimal) -> None:
    """Whether the kernel's ``kernel`` and DGII's ``channel`` agree within ``tolerance``, by category."""
    billed: set[BillingIndicator] = set()
    for category in kernel.categories:
        indicator = INDICATOR_OF.get(category.category)
        base, tax = category.base.amount, category.tax.amount
        if indicator is BillingIndicator.EXEMPT:
            agrees = channel.exempt is not None and _close(base, channel.exempt, tolerance)
        else:
            group = None if indicator is None else channel.group(indicator)
            agrees = (
                group is not None
                and _close(base, group.base, tolerance)
                and _close(tax, group.itbis, tolerance)
            )
        if indicator is None or not agrees:
            raise TotalsDisagreeError(side, category.category)
        billed.add(indicator)
    exempt = () if channel.exempt is None else (BillingIndicator.EXEMPT,)
    if {*(group.indicator for group in channel.groups), *exempt} != billed:
        raise TotalsDisagreeError(side, "categories")
    if not _close(kernel.gross.amount, channel.total, tolerance):
        raise TotalsDisagreeError(side, "gross")
    if not _close(kernel.tax.amount, channel.itbis_total or Decimal(0), tolerance):
        raise TotalsDisagreeError(side, "tax")


def verify_totals(draft: FiscalDraft, totals: Totals) -> None:
    """Whether ``draft``'s kernel totals are DGII's ``totals`` of its e-CF (see the module).

    ``TotalsDisagreeError`` names where they differ (the side, a category, the gross or the tax), never by
    how much.
    """
    tolerance = GLOBAL_TOLERANCE_PER_LINE * len(draft.lines)
    if draft.currency == PESO:
        _agree(_PESOS, draft.totals, totals, tolerance)
        if totals.foreign is not None:
            raise TotalsDisagreeError(_OWN, "a peso document has no other currency's totals")
        return
    own = totals.foreign
    if own is None:
        raise TotalsDisagreeError(_OWN, "the e-CF has no totals in it")
    _agree(_OWN, draft.totals, own, tolerance)
    functional = draft.functional_totals
    if functional is None:
        raise TotalsDisagreeError(_PESOS, "the draft has no totals in pesos")
    _agree(_PESOS, functional, totals, tolerance)
