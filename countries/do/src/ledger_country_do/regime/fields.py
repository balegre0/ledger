"""Regime ``do``'s law-level country fields: what a request says under ``fiscal.do`` because the law does.

The document type is law (spec §9.8): ``ecf_type`` names one of the ten e-CF types, ``"31"`` to ``"47"``,
when the default (§8.6.2) does not apply (a purchase, or a type 44, 45 or 46). ``replaces_ncf`` names the
paper NCF a document issued after a contingency replaces (Decreto 587-24 Art. 42): declared now, accepted
once the channel writes replacements. Format-level fields are the channel's (``do-ecf-v1``'s
``domain.fields``).
"""

from typing import Final

from ledger_kernel.regimes import FieldDef, FieldKind, Locale

_SPANISH: Final = Locale("es")
_ENGLISH: Final = Locale("en")

ECF_TYPE: Final = FieldDef(
    name="ecf_type",
    labels={_SPANISH: "Tipo de e-CF", _ENGLISH: "e-CF type"},
    kind=FieldKind.CODE,
)
REPLACES_NCF: Final = FieldDef(
    name="replaces_ncf",
    labels={_SPANISH: "NCF reemplazado", _ENGLISH: "Replaced NCF"},
    kind=FieldKind.OBJECT,
)
FIELDS: Final = (ECF_TYPE, REPLACES_NCF)
