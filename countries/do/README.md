# ledger-country-do

The Dominican Republic as a country pack (spec §8.6): regime `do` (DGII's tax law as typed, dated data) and
channel `do-ecf-v1` (DGII's e-CF: XML, schemas, signature, DGII's web services, buyers' receivers).

- `src/ledger_country_do/regime/`: tax-id schemes (`do.rnc`, `do.cedula`), ITBIS rates, the ten e-CF types,
  dated parameters, rounding and tolerance, in the kernel's regime types (`ledger_kernel.regimes`): the kernel
  and the standard library only. Registered by the entry point `ledger.countries.regimes: do`.
- `src/ledger_country_do/channels/ecf_v1/`: `domain` <- `application` <- `adapters`. The hardened XML reader and
  the signature profile (and what may sign) are fiscal-kit's (`ledger_fiscal_kit.xml`); the verifier, trust,
  names, stamp and the seed's shape stay here.
- Signing goes through fiscal-kit's `SignerPort` (`adapters/signing/port.py`, `DocumentSigner`), as the issuing
  company each call names (`SigningAs`): the signer decides whether its certificate covers the moment a document
  states; the channel never holds a key. DGII's client keeps a token per company, mode and environment; DGII's
  directory is cached per replica (`adapters/dgii/directory.py`).
- **Binding rule for every caller of `Emitter.send`** (v2-M3b's advance job included): `send` parks on every
  pack error; any other exception escaping it is a defect whose outcome is **unknown**: the document may be at
  DGII, so it is never sent again, only resumed as a parked one (track ids or `consultarfce` first).
- The channel is assembled (`channels/ecf_v1/assembly.py`, entry point `ledger.countries.channels: do-ecf-v1`):
  schema `do_ecf` (numbering series, ranges and released numbers; documents come with issuing), settings
  `LEDGER_DO_ECF_*` (`settings.py`: DGII's hosts and waits, trusted roots, test stand-ins, the receiver's URL),
  the public routes under `/v1/do` (`numbering_ranges`; snapshot in `openapi.json`) and what an enrollment
  requires (`certificate`, `numbering_range`, `receiver_url`). Every `Channel` method is answered: `status` is
  an invoice's document as it stands, `representation` its printed representation.
- The printed representation (`adapters/pdf/`) is a PDF drawn with reportlab, its QR encoded with segno, from
  the signed e-CF the archive keeps: archived on its first render, answered from the archive after
  (`application/representation.py`).
- [`docs/runbooks/do/testecf-spike.md`](../../docs/runbooks/do/testecf-spike.md): running the spike against
  DGII's TesteCF; [`docs/spike-findings.md`](docs/spike-findings.md): what it settled.

Built by the DGII spike (`docs/superpowers/plans/2026-10-13-v2-dgii-spike.md`), assembled by v2-M3b
(`docs/superpowers/plans/2026-10-19-v2-m3b-fiscal.md`).
