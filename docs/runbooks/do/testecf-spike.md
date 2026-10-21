# Runbook: the DGII spike against TesteCF

Runs the spike's emission (`ledger-dgii-spike`) against DGII's pre-certification environment, TesteCF, and
records what DGII's documentation left open ([`countries/do/docs/spike-findings.md`](../../../countries/do/docs/spike-findings.md), rows F1-F16). Nothing here reaches
production: the CLI refuses the `ecf` environment whatever hosts it is given, and DGII's client refuses the
live environment on any host but DGII's.

**The one rule: never send a parked document again.** If a run prints `parked:`, DGII may already hold that
e-NCF. Do not run that sequence again, do not edit `used-encf.txt` to allow it; follow "Parked documents"
below.

## 1. What the owner provides (once)

- **A taxpayer enrolled for TesteCF** (its RNC): DGII's portal (Oficina Virtual) → Facturación Electrónica →
  the postulación as electronic issuer; DGII then opens TesteCF to that RNC (spec §19, Q2).
- **A user with the signer's role** for that RNC (DGII's *Instructivo Delegaciones de Roles de Facturación
  Electrónica*): DGII rejects documents signed by someone not delegated (`secuenciaUtilizada = false`).
- **The certificate** of that user: a `.p12`/`.pfx` from an INDOTEL-authorized certification entity, and its
  password. A certificate made by the spike's test PKI is refused by DGII (F9).
- **The trusted root CAs**, as one PEM file: the roots of the INDOTEL-authorized certification entities whose
  certificates buyers sign their receipts (ARECF) with. Delivery (`--deliver`) refuses to run without them,
  and a receipt whose certificate does not chain to one of them is refused. Obtain them from the entities
  (or INDOTEL's list), not from a document a buyer sent.
- **A buyer to bill in TesteCF**: an RNC and its legal name (for example the owner's other company).

## 2. Secrets, kept out of the repository

Secrets live in `.dgii/` at the repository's root, which `.gitignore` ignores (with `*.p12` and `*.pfx`
anywhere). Run every command from the repository's root: the CLI refuses a certificate, a password file or an
output directory that is not inside `.dgii/` of the directory it runs in (another `.dgii` elsewhere is
refused too). It never prints or logs a token, the password, the key or the certificate.

```powershell
New-Item -ItemType Directory -Force .dgii | Out-Null
Copy-Item <path-to-the-owner's-file>.p12 .dgii/certificate.p12
Set-Content -NoNewline -Encoding utf8 .dgii/certificate.password -Value (Read-Host -AsSecureString "password" | ConvertFrom-SecureString -AsPlainText)
Copy-Item <path-to-the-roots>.pem .dgii/trusted-roots.pem
git status --short --ignored .dgii   # shows ".dgii/" as ignored (!!); never add it, never commit a .p12
```

Settings for the shell session (names only; never print them back with `Get-ChildItem env:` or `echo`):

```powershell
$env:LEDGER_DO_ECF_SPIKE_ENVIRONMENT = "testecf"
$env:LEDGER_DO_ECF_SPIKE_P12 = ".dgii/certificate.p12"
$env:LEDGER_DO_ECF_SPIKE_P12_PASSWORD_FILE = ".dgii/certificate.password"
$env:LEDGER_DO_ECF_SPIKE_TRUSTED_ROOTS = ".dgii/trusted-roots.pem"
$env:LEDGER_DO_ECF_SPIKE_RNC = "<the enrolled RNC>"
$env:LEDGER_DO_ECF_SPIKE_LEGAL_NAME = "<its legal name, as in DGII's registry>"
$env:LEDGER_DO_ECF_SPIKE_ADDRESS = "<its address>"
$env:LEDGER_DO_ECF_SPIKE_BUYER_RNC = "<the RNC to bill in TesteCF>"
$env:LEDGER_DO_ECF_SPIKE_BUYER_NAME = "<its legal name>"
$env:LEDGER_DO_ECF_SPIKE_OUT = ".dgii/out"
```

Optional: `LEDGER_DO_ECF_SPIKE_SEQUENCE_EXPIRES` (`dd-mm-yyyy`, default `31-12-2028`: TesteCF's ranges),
`LEDGER_DO_ECF_SPIKE_ECF_HOST` and `LEDGER_DO_ECF_SPIKE_FC_HOST` (DGII's hosts by default; `https`, or `http`
on loopback only, for the fake).

TesteCF accepts sequences 1 to 10,000,000 of every type (type 32 to 50,000,000) expiring 31-12-2028, and keeps
submissions 60 days ([06]). The CLI records every e-NCF in `.dgii/out/used-encf.txt` **before** it signs it,
and refuses one already there; pick sequences far from any a pilot uses.

Every file the CLI writes (the journal, the signed documents, the summaries) is written crash-safe: a
temporary file, flushed to disk, then renamed over the name, so a crash leaves the old content or the new,
never a part. One `emit` runs at a time: it holds `.dgii/out/used-encf.lock` while it works, and a second run
refuses (`used-encf.lock is held by another run`). A run killed mid-way leaves the lock behind: check that no
`ledger-dgii-spike` is running, then delete the lock by hand. A journal that does not read
(`used-encf.txt does not read`) stops the CLI before anything is signed: repair it by hand, never by removing
an e-NCF from it.

What `emit` does, in order: builds the sample; records its e-NCF; signs it (and its RFCE on that route);
**writes the signed documents to `.dgii/out` before anything is sent**; sends once; polls; prints DGII's answer,
the security code and the QR URL; writes a JSON summary; with `--deliver`, once accepted, delivers an e-CF
sent in full to its buyer (a consumer invoice sent as an RFCE is never delivered: spec §8.6.5). Exit codes: 0 accepted, 1 rejected, still pending,
parked, unavailable or not received by the buyer, 2 a setting or argument refused.

## 3. Dry run against the fake DGII

Terminal 1 (granian prints its PID; stop it with Ctrl+C or `Stop-Process -Id <pid>`, never by name). It
listens on `127.0.0.1:8471` only (never port 8000), trusts any signer, and lists its own emisor-receptor
simulator for every buyer:

```powershell
uv run ledger-fake-dgii
```

Terminal 2, with the settings above plus the fake's host (plain `http` is allowed on loopback only) and an
output directory of the dry run's own, so the fake's e-NCF journal never mixes with TesteCF's:

```powershell
$env:LEDGER_DO_ECF_SPIKE_ECF_HOST = "http://127.0.0.1:8471"
$env:LEDGER_DO_ECF_SPIKE_FC_HOST = "http://127.0.0.1:8471"
$env:LEDGER_DO_ECF_SPIKE_OUT = ".dgii/fake-out"
uv run ledger-dgii-spike auth
uv run ledger-dgii-spike emit --type 31 --sequence 9000001
uv run ledger-dgii-spike emit --type 32 --sequence 9000001
uv run ledger-dgii-spike emit --type 32 --sequence 9000002 --large
Remove-Item env:LEDGER_DO_ECF_SPIKE_ECF_HOST, env:LEDGER_DO_ECF_SPIKE_FC_HOST
$env:LEDGER_DO_ECF_SPIKE_OUT = ".dgii/out"   # back to TesteCF's journal before any TesteCF run
```

`.dgii/fake-out` may be deleted whenever no dry run is going. **Never delete `.dgii/out`**: it is TesteCF's
journal of every e-NCF signed and the signed documents of any parked one (step 9).

Expected: `authenticated with testecf: a token was issued (not shown); consultaresultado answered codigo 0`;
`codigo 1 Aceptado -> accepted (number used)` for the 31 (with a track id) and for the large 32
(`route: reception`); `route: rfce  track id: none` and `codigo 1` for the small 32. Each prints `written:`
for the signed XML before DGII's answer.

`--deliver` against the fake on loopback prints `buyer: do_ecf_buyer_endpoint_refused: ... it is not https`
(exit 1): a buyer's endpoint from DGII's directory must be an `https` public host name, and the fake's
directory names `http://127.0.0.1:8471`. That refusal is the security check working, not a fault; delivery
to the fake's simulator is exercised by the tests (`test_spike_cli.py`, in process, with a public name).

## 4. TesteCF: authentication (F9)

```powershell
uv run ledger-dgii-spike auth
```

Expected: `authenticated with testecf: a token was issued (not shown); consultaresultado answered codigo 0`.
`error: do_ecf_dgii_unavailable: DGII's validate_seed: refused` means DGII refused the signed seed: the
certificate, its chain or its delegation. Record which in F9. Optionally, settle F9's other half with a
certificate from the spike's test PKI (`auth` only, nothing is sent): it must be refused.

## 5. TesteCF: a credit invoice, and a consumer invoice as an RFCE (F1, F2, F11, F12, F13)

```powershell
uv run ledger-dgii-spike emit --type 31 --sequence 1
uv run ledger-dgii-spike emit --type 32 --sequence 1
```

Expected: the 31 gets a track id and `codigo 1 Aceptado` (or `4 Aceptado Condicional`: copy its messages,
printed below the answer, into F11); the 32 (RD$1,180.00, ITBIS included) goes `route: rfce` and is answered
at once.

- **F1**: the RFCE answered with a `codigo` means the `ecf` host's token works on `fc`; a 401 there, even
  after a renewed token, shows as `parked: do_ecf_dgii_unavailable (failure: undocumented; ...)` and means it
  does not (follow "Parked documents").
- **F2**: the `codigo` the RFCE reception returned. A code outside 1, 2, 4 parks it with
  `do_ecf_dgii_code_unknown`: record it.
- **F12**: a RD$1,180.00 invoice went as an RFCE and the RD$295,000.00 one (step 8) in full.
- **F13**: if any submission is refused at the door (an answer with DGII's `error`/`mensaje`, printed as a
  message under `codigo 2 Rechazado -> rejected (number burned)`), record DGII's words; never provoke one, and
  ask DGII in writing whether such a number is burned.

## 6. TesteCF: an anonymous consumer invoice and the QR codes (F4, F8)

The 32 of step 5 is anonymous (`<Comprador />`): its acceptance settles F4 (an empty required element is
accepted); a rejection naming `Comprador` settles it the other way. Open each printed `QR:` URL in a browser:
DGII's `consultatimbre`/`consultatimbrefc` page must show the document. F8: the 31's URL carries
`RncComprador`; the samples never send an e-CF without a buyer RNC on the reception route (a type 32 of
RD$250,000 or more names its buyer), so the omission rule is settled by asking DGII, or recorded as open.

## 7. TesteCF: the directory and delivery to DGII's simulator (F5, F6, F14, F15)

```powershell
uv run ledger-dgii-spike emit --type 31 --sequence 2 --deliver
```

Expected: `buyer: received; receipt certificate identity: <attribute> <prefix>` (TesteCF's directory names
DGII's emisor-receptor simulator), and `.dgii/out/<RNC>E310000000002.arecf.xml`.

- **F5**: a receipt means the directory's URLs are bases; `buyer: do_ecf_buyer_unavailable: ... its
  reception` after a 404 means they are full endpoints.
- **F16** (with F5): `buyer: not applicable: the buyer is not an electronic receiver` for a buyer TesteCF
  does list means its directory answered with an entry naming **another** RNC (the client uses only an entry
  naming the RNC asked about, so nothing was sent). Record which RNC the entry named (DGII's simulator's own,
  for example) by asking `consultadirectorio` by hand; widening the match is a decision, never a quiet fix.
- **F6**: the client always sends the bearer to `consultadirectorio`; a directory answer settles that it is
  reachable, a 401 there would contradict DT-DGII. Whether it also works without a token is DGII's to say.
- **F15**: the receipt may be **refused** with `do_ecf_counterparty_certificate_refused` ("it is not the
  buyer's" if DGII's simulator signs with DGII's own certificate rather than one naming the buyer; "it does
  not chain to a trusted authority" if its issuer is not among the trusted roots). Record which, and the
  identity attributes the CLI prints (names and prefixes, never the number or the certificate). If it is
  DGII's own certificate, the fix is an explicit stand-in identity limited to TesteCF/CerteCF (refused in
  production, the chain still required), not a looser check.
- **F14**, to ask DGII: a buyer's seed has DGII's own `SemillaModel` format, so a hostile buyer endpoint could
  relay DGII's seed for our RNC, have us sign it, and obtain a DGII token as us. Does DGII bind its seeds to
  their audience, or recommend issuers refuse to sign a buyer seed that also validates at DGII? Every buyer
  seed signed is returned to the caller as a `SeedSigned` (buyer, endpoint host, time) for its audit; the
  spike authenticates whenever a buyer publishes an authentication URL, the stack only when the company
  switched it on (Decision 19).

## 8. TesteCF: a large consumer invoice, in full

```powershell
uv run ledger-dgii-spike emit --type 32 --sequence 2 --large
```

Expected: `route: reception`, a track id, `codigo 1` (F12).

## Parked documents

`parked: <code> (failure: <why>; sent: <whether it left>)` means the outcome is unknown or sending stopped
after signing. `sent: it never left` is certain; `it may be at DGII` and `DGII answered about it` are not
safe to resend. In every case:

1. **Never run that sequence again.** The signed documents are already in `.dgii/out` (written before
   sending), with the JSON summary naming the code and the failure.
2. Ask DGII by hand: `consultatrackids` for an e-CF (`rncemisor`, `encf`), `consultarfce` for an RFCE (with
   its security code), or DGII's portal; `codigo 0` from `consultarfce` means pending, not final.
3. Record what DGII says in the findings. v2-M3 automates this recovery; the spike never resends.

A document still pending after the polling schedule (`DGII: still pending ...`) has a track id: ask
`consultaresultado` later; never resend it either.

## Open questions and how each is settled

| # | How TesteCF settles it (or why it does not) | Step |
|---|---|---|
| F1 | The RFCE answered with a `codigo`, not a 401, using the `ecf` host's token | 5 |
| F2 | The `codigo` the RFCE reception returns; an undocumented one parks it and is recorded | 5 |
| F3 | Not exercised on purpose: routing never sends a type 32 under RD$250,000 to `recepcion` | — |
| F4 | The anonymous type 32 (`<Comprador />`) accepted, or rejected naming `Comprador` | 5, 6 |
| F5 | Delivery reaches the simulator's reception (bases), or a 404 (full endpoints) | 7 |
| F6 | The directory answers with the bearer sent; whether it needs one is asked of DGII | 7 |
| F16 | The directory's entry names the RNC asked about (delivery runs), or another (not applicable) | 7 |
| F7 | Out of scope (a 1,000-line type 32 is not sampled) | — |
| F8 | The 31's QR opens with `RncComprador`; the omission case is asked of DGII | 6 |
| F9 | The owner's certificate authenticates; optionally a test-PKI certificate is refused | 4 |
| F10 | Not DGII's to answer (regime review) | — |
| F11 | Messages of any `codigo 4` copied verbatim (no tolerance sample in the CLI) | 5 |
| F12 | RD$1,180.00 goes as an RFCE, RD$295,000.00 in full, both accepted | 5, 8 |
| F13 | Any refusal at the door recorded (never provoked); asked of DGII | 5 |
| F14 | Asked of DGII; buyer seeds signed are audit-logged meanwhile | 7 |
| F15 | The receipt's certificate identity printed, or the refusal's reason | 7 |

## 9. Record, then clean up

Fill in [`countries/do/docs/spike-findings.md`](../../../countries/do/docs/spike-findings.md) (one line per F row: settled, how, DGII's words) and commit it. Copy no secret
into it: no token, no password, no certificate, no cédula, no full XML (DGII's messages and codes are fine).
`.dgii/out` keeps the signed documents, DGII's answers and the journal of every e-NCF signed: keep it, never
delete it (a later run would otherwise sign a sequence TesteCF already holds). Never commit `.dgii/`.
