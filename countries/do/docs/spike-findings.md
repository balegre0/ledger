# DGII spike: findings

What DGII's documentation left open (plan `docs/superpowers/plans/2026-10-13-v2-dgii-spike.md`, "DGII facts
this plan could not pin down", and the reviews of Tasks 11-13), what the code assumes, and what TesteCF
answered. The runbook ([`docs/runbooks/do/testecf-spike.md`](../../../docs/runbooks/do/testecf-spike.md),
"Open questions") says how each row is settled. Fill the last two columns from a TesteCF run; never paste a
token, a password, a certificate, a cédula or a whole document (DGII's codes and messages are fine).

Documents cited by the short names of `.superpowers/research/06-dgii-facts.md`: **DT-DGII** (*Descripción
Técnica Servicios DGII*), **DT-EMI** (*Descripción Técnica Emisores Electrónicos*), **IT** (*Informe Técnico
e-CF v1.0*), **FMT** (*Formato e-CF V1.0*).

| # | Question | The code assumes | Settled by | TesteCF answered (date, step) | Settled |
|---|---|---|---|---|---|
| F1 | Is the bearer token from `ecf.dgii.gov.do/{env}/autenticacion` accepted by `fc.dgii.gov.do` (RFCE)? | Yes: DT-DGII's curl examples for both hosts carry the same JWT | Runbook step 5 | | |
| F2 | The RFCE reception's `codigo` values | 1 Aceptado, 2 Rechazado, 4 Aceptado Condicional, as `consultaresultado`; anything else is unreadable (parked, `do_ecf_dgii_code_unknown`) | Runbook step 5 | | |
| F3 | DGII's answer to a type 32 below RD$250,000 sent to `recepcion` | A refusal (the fake answers 400 with `error`/`mensaje`); our routing never sends one | Not exercised on purpose | not exercised | — |
| F4 | An anonymous type 32: its XSD requires `Comprador` with every child optional, while DT-EMI forbids empty tags | The builder writes an empty `<Comprador />` only for an anonymous buyer | Runbook step 6 | | |
| F5 | Directory `urlRecepcion`/`urlAceptacion`/`urlOpcional`: a base URL or the full endpoint? | A base: the client appends `/fe/recepcion/api/ecf` and `/fe/autenticacion/api/{semilla,validacioncertificado}` (consistent with TesteCF's directory returning `…/testecf/emisorreceptor`) | Runbook step 7 | | |
| F16 (with F5) | Does TesteCF's directory answer an RNC with an entry naming another RNC (its emisor-receptor simulator under the simulator's own RNC, say)? | It names the RNC asked about; an entry naming another RNC is never used (the buyer is taken as no electronic receiver, nothing is sent) | Runbook step 7 | | |
| F6 | Is `consultadirectorio` authenticated? | DT-DGII's curl has no `Authorization`; the client sends the bearer anyway (harmless if ignored) | Runbook step 7 | | |
| F7 | The line limit of a type 32 | 1,000 (the e-CF 32 XSD: `Item [1..1000]`); [06]'s 10,000 for RFCE-summarized invoices is kept as a regime parameter, unused | Out of scope | not exercised | — |
| F8 | `RncComprador` in the QR when there is no buyer RNC | Left out | Runbook step 6 | | |
| F9 | Does TesteCF check the signer's chain to a DR-authorized entity (so a self-made certificate is refused)? | Yes: only the owner's real certificate works on TesteCF; generated certificates are for tests and the fake | Runbook step 4 | | |
| F10 | When the ITBIS rates took effect (18 % by Ley 253-12; 16 % phased 2013-2016) | Not asserted: the regime's timelines start at 2019-01-01 (do-ecf-v1's `valid_from`), where FMT's 18/16/0 hold | Regime review (not DGII) | not DGII's to answer | — |
| F11 | DGII's conditional-acceptance codes and messages | Only the cuadratura tolerance is documented (IT §12); messages are kept verbatim | Runbook step 5 | | |
| F12 | Whether `MontoTotal` (ITBIS included) is the amount compared with RD$250,000 | Yes | Runbook steps 5 and 8 | | |
| F13 | Does a refusal at the door (HTTP 400 from `recepcion`) burn the e-NCF? | Burned: never reuse a number DGII may have recorded | Runbook step 5 (observed, never provoked); ask DGII | | |
| F14 | Can a buyer relay DGII's seed for the issuer to sign (a signing oracle)? Does DGII bind seeds to their audience? | Not bound: every buyer seed signed is audit-logged; buyer authentication off unless a buyer requires it (v2-M4) | Runbook step 7 (ask DGII) | | |
| F15 | Whose certificate signs the TesteCF simulator's ARECFs, and which identity attributes it carries | The buyer's (`serialNumber`/`organizationIdentifier` naming its RNC); DGII's own would be refused | Runbook step 7 | | |

## Known gaps before production (not settled by TesteCF)

- **Egress firewall.** The buyer client refuses non-public addresses after DNS and pins the checked address,
  but a NAT64 gateway with an operator-chosen prefix makes an internal IPv4 address look like a public IPv6
  one, undetectably. The process that delivers needs an egress firewall blocking internal ranges.
- **Revocation.** A buyer's certificate is never checked against CRL or OCSP; production delivery is blocked
  until it is (v2-M4).
- **Buyer authentication** (signing a buyer's seed) is a per-company switch, off unless a buyer requires it
  (v2-M4), because of F14. Since the channel signs through `SignerPort` (v2-M2b), each seed's audience (the
  host it came from) is audited by the signer, but it is not bound into the signature: DGII's `SemillaModel`
  has no audience field, so a hostile receiver can still relay a real DGII seed for us to sign.
- **An exception escaping `Emitter.send`** that is not a pack error is a defect with an **unknown outcome**:
  the document may be at DGII; it is never sent again, only resumed as a parked document (binding on v2-M3's
  callers).
- **Duplicate receipts.** An ARECF "not received, duplicate" (reason 3) means "already received" only when the
  same e-CF had an earlier delivery whose outcome is unknown; otherwise it is a conflict (v2-M4).

Carried from the reviews of Tasks 8-13 (recorded in the spike's progress ledger, not new decisions):

- **Recovering a parked document** is by hand in the spike: `consultatrackids` for an e-CF, `consultarfce`
  for an RFCE (whose `codigo` 0 means pending, not final). v2-M3 automates it, and must not reuse a released
  e-NCF within `RECEIPT_TOLERANCE` of its last reception (or must exclude earlier attempts' track ids).
- **The buyer-seed audit** (v2-M3b Task 15): the log line is gone; the buyer client returns each seed signed
  (`SeedSigned`: buyer, endpoint host, time) and the delivery job audits it in `do_ecf.audit_events` with the
  buyer masked (Decision 20). Buyer authentication is a per-company switch, off by default (Decision 19).
- **A refused receipt is kept apart**: an ARECF that fails its checks (signature, schema, trust) comes back as
  `ReceiptRefused` with its bytes; the CLI writes it as `<RNC><e-NCF>.arecf-refused.xml`, the delivery job
  archives it (`arecf-refused`) and never retries.
