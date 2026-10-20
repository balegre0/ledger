"""The e-CF a note corrects, found among the channel's own accepted documents (FMT, InformacionReferencia).

A ``FiscalDraft`` names the commercial document a note corrects; DGII wants that document's e-NCF and date.
The channel reads them from its own records: its accepted e-CF of that document, in the same company and
mode (``AcceptedEcf``, which the caller loads). A document with none there (never sent, rejected, issued on
paper) is unresolved, and a note corrects a sales invoice only (the regime's ``corrects``).
"""

from dataclasses import dataclass
from typing import Final, final
lazy from collections.abc import Mapping
lazy from datetime import date

from ledger_country_do.channels.ecf_v1.domain.drafts import EcfReference
from ledger_country_do.channels.ecf_v1.domain.errors import from_regime
from ledger_country_do.regime import REGIME
from ledger_country_do.regime.entries import document_type
from ledger_fiscal_kit.objections import Objection
lazy from ledger_country_do.channels.ecf_v1.domain.drafts import ModificationCode
lazy from ledger_country_do.channels.ecf_v1.domain.encf import EcfType, Encf
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
        ecf = accepted.get(reference.document)
        if ecf is None:
            found.append(
                _objection("reference_unresolved", "names an invoice whose e-CF DGII accepted, here")
            )
        elif ecf.encf.ecf_type.value not in corrects:
            found.append(
                _objection("reference_type_not_allowed", "names a sales invoice (31, 32, 44, 45 or 46)")
            )
        else:
            references.append(EcfReference(encf=ecf.encf, issued_on=ecf.issued_on, code=code))
    return ((), tuple(found)) if found else (tuple(references), ())
