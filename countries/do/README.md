# ledger-country-do

The Dominican Republic as a country pack (spec §8.6): regime `do` (DGII's tax law as typed, dated data) and
channel `do-ecf-v1` (DGII's e-CF: XML, schemas, signature, DGII's web services, buyers' receivers).

- `src/ledger_country_do/regime/`: tax-id schemes (`do.rnc`, `do.cedula`), ITBIS rates, the ten e-CF types,
  dated parameters, rounding and tolerance. Standard library only.
- `src/ledger_country_do/channels/ecf_v1/`: `domain` <- `application` <- `adapters`.
- `docs/runbooks/testecf-spike.md`: running the spike against DGII's TesteCF; `docs/spike-findings.md`: what it
  settled.

Built by the DGII spike (`docs/superpowers/plans/2026-10-13-v2-dgii-spike.md`); no app assembles it yet.
