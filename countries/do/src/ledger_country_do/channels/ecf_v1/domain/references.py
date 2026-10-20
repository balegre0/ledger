"""The e-CF a note corrects, found among the channel's own accepted documents (FMT, InformacionReferencia).

A ``FiscalDraft`` names the commercial document a note corrects; DGII wants that document's e-NCF and date.
The channel reads them from its own records: its accepted e-CF of that document, in the same company and
mode (``AcceptedEcf``, which the caller loads). A document with none there (never sent, rejected, issued on
paper) is unresolved. A note corrects a sales invoice only (the regime's ``corrects``), and modification
code 5, which DGII's schemas gloss as "Referencia Factura Consumo Electrónica", a consumer invoice (32).
"""

from dataclasses import dataclass
from typing import Final, final
lazy from collections.abc import Mapping
lazy from datetime import date

from ledger_country_do.channels.ecf_v1.domain.drafts import EcfReference, ModificationCode
from ledger_country_do.channels.ecf_v1.domain.encf import EcfType
from ledger_country_do.channels.ecf_v1.domain.errors import from_regime
from ledger_country_do.regime import REGIME
from ledger_country_do.regime.entries import document_type
from ledger_fiscal_kit.objections import Objection
lazy from ledger_country_do.channels.ecf_v1.domain.encf import Encf
lazy from ledger_fiscal_kit.drafts import DocumentRef, FiscalDraft
lazy from ledger_kernel.regimes import RegimeDef

FIELD: Final = "corrects"
"""Where a client names the invoice a note corrects (invoicing's ``corrects``)."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class AcceptedEcf:
    """An e-CF of the channel's that DGII accepted: its e-NCF and its issue date."""

    encf: Encf
    issued_on: date


def _objection(code: str, requirement: str) -> Objection:
    return Objection(code=f"do_ecf_{code}", field=FIELD, message=f"{FIELD} {requirement}")


def _reference(
    ecf: AcceptedEcf | None, corrects: tuple[str, ...], code: ModificationCode
) -> EcfReference | Objection:
    """The reference to ``ecf`` for ``code``, or why ``ecf`` cannot be the e-CF a note corrects."""
    if ecf is None:
        return _objection("reference_unresolved", "names an invoice whose e-CF DGII accepted, here")
    ecf_type = ecf.encf.ecf_type
    if ecf_type.value not in corrects:
        return _objection("reference_type_not_allowed", "names a sales invoice (31, 32, 44, 45 or 46)")
    if code is ModificationCode.REFERENCE_CONSUMER and ecf_type is not EcfType.CONSUMER_INVOICE:
        return _objection("reference_type_not_allowed", "names a consumer invoice (32) for code 5")
    return EcfReference(encf=ecf.encf, issued_on=ecf.issued_on, code=code)


def resolve(
    draft: FiscalDraft,
    accepted: Mapping[DocumentRef, AcceptedEcf],
    *,
    ecf_type: EcfType,
    code: ModificationCode,
    regime: RegimeDef = REGIME,
) -> tuple[tuple[EcfReference, ...], tuple[Objection, ...]]:
    """The references of ``draft``, a note of ``ecf_type`` correcting for ``code``; or the objections.

    ``accepted`` holds the accepted e-CF of ``draft``'s company and mode for the documents it names.
    """
    corrects = from_regime(lambda: document_type(regime, ecf_type.value)).corrects
    references: list[EcfReference] = []
    found: list[Objection] = []
    for reference in draft.references:
        found_or_reference = _reference(accepted.get(reference.document), corrects, code)
        if isinstance(found_or_reference, Objection):
            found.append(found_or_reference)
        else:
            references.append(found_or_reference)
    return ((), tuple(found)) if found else (tuple(references), ())
