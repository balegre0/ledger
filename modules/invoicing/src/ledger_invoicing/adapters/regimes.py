"""Which installed regime governs a company: what prices its documents (§8.1, rule 12).

A subdivision whose law differs has a regime of its own (``us-ca``); any other place is governed by its
country's (``do``). A jurisdiction no installed regime governs has none: identity registers no company there,
so a use case that meets one reports the installation's defect (``RegimeMissingError``).
"""

lazy from ledger_invoicing.application.ports import RegimeOf
lazy from ledger_kernel.jurisdiction import Jurisdiction
lazy from ledger_kernel.regimes import RegimeDef
lazy from ledger_platform.runtime.registries import Regimes


def regime_of(regimes: Regimes) -> RegimeOf:
    """The lookup of the regime that governs a jurisdiction, among ``regimes``."""

    def governing(jurisdiction: Jurisdiction) -> RegimeDef | None:
        for key in (jurisdiction.subdivision, jurisdiction.country):
            if key is not None and key.lower() in regimes:
                return regimes.get(key.lower())
        return None

    return governing
