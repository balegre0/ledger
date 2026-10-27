# Runbook: the stack against DGII's TesteCF

Runs the platform itself (api, worker, receiver and signer, in the local stack) against DGII's
pre-certification environment, TesteCF, and records what DGII's documentation left open
([`countries/do/docs/spike-findings.md`](../../../countries/do/docs/spike-findings.md), rows F1-F16, numbered as
the spike numbered them). It replaces the spike's command line, retired in v2-M3b (Decision 29). Nothing here
reaches production: TesteCF is an environment of test mode, and do-ecf-v1 refuses any host but DGII's for the
live environment.

**Two rules.**

- **Never send a document again whose outcome is unknown** (`do_ecf.submission_unknown`, a document in
  `outcome_unknown`): DGII may already hold it. Follow [outcome-unknown.md](outcome-unknown.md).
- **Never reuse an e-NCF the spike used.** TesteCF keeps submissions 60 days; the spike's journal
  (`.dgii/out/used-encf.txt`) holds every e-NCF it signed. Ranges registered in the stack for TesteCF start
  **above the highest sequence per type in that journal** (step 4). The journal is read, never edited, never
  deleted.

## 1. What the owner provides (once)

- **A taxpayer enrolled for TesteCF** (its RNC): DGII's portal (Oficina Virtual) → Facturación Electrónica →
  the postulación as electronic issuer; DGII then opens TesteCF to that RNC (spec §19, Q2).
- **A user with the signer's role** for that RNC (DGII's *Instructivo Delegaciones de Roles de Facturación
  Electrónica*): DGII rejects documents signed by someone not delegated (`secuenciaUtilizada = false`).
- **The certificate** of that user: a `.p12`/`.pfx` from an INDOTEL-authorized certification entity, and its
  password. A certificate made by a test PKI is refused by DGII (F9).
- **The trusted root CAs**, as one PEM file: the roots of the INDOTEL-authorized certification entities whose
  certificates buyers sign their receipts (ARECF) with, and senders their e-CF. The worker and the receiver
  refuse a counterparty whose certificate does not chain to one of them. Obtain them from the entities (or
  INDOTEL's list), not from a document a buyer sent (plan Owner item 7).
- **A buyer to bill in TesteCF**: an RNC and its legal name (for example the owner's other company).
- **The company's fiscal address** as DGII's registry holds it (line, municipality and province codes):
  DGII requires `DireccionEmisor`.

## 2. Secrets, kept out of the repository

Secrets live in `.dgii/` at the repository's root, which `.gitignore` ignores (with `*.p12` and `*.pfx`
anywhere). The certificate is imported once into the signer through the api (step 4); the signer keeps it
under its KMS key, and nothing else of it is needed afterwards. Never print a token, the password, the key or
the certificate; never paste them in a ticket.

```powershell
New-Item -ItemType Directory -Force .dgii | Out-Null
Copy-Item <path-to-the-owner's-file>.p12 .dgii/certificate.p12
Copy-Item <path-to-the-roots>.pem .dgii/trusted-roots.pem
git status --short --ignored .dgii   # shows ".dgii/" as ignored (!!); never add it, never commit a .p12
```

## 3. The stack, pointed at TesteCF

The local stack (`deploy/local/compose.yaml`) talks to the fake DGII by default. For TesteCF, on the machine
that runs it (never committed: `git diff --stat deploy/` must be empty before any commit):

1. The roots: `sh deploy/local/signer/tokens.sh` and `sh deploy/local/do/roots.sh` once, then put the owner's
   roots where the worker and the receiver read them:
   `Copy-Item .dgii/trusted-roots.pem deploy/local/do/.local/roots.pem` (git-ignored; `roots.sh` writes the
   development roots back).
2. DGII's hosts: in `deploy/local/do-ecf.env`, comment out `LEDGER_DO_ECF_ECF_HOST` and
   `LEDGER_DO_ECF_FC_HOST`: DGII's own hosts are the defaults. Keep `LEDGER_DO_ECF_RECEIVER_PUBLIC_URL` (no
   enrollment activates without one; TesteCF never calls our receiver unless the company registers it,
   [receiver-registration.md](receiver-registration.md)).
3. Start it with Zitadel for real tokens ([`deploy/local/zitadel/README.md`](../../../deploy/local/zitadel/README.md)):

   ```sh
   docker compose -f deploy/local/compose.yaml --profile zitadel up -d --wait --build
   sh deploy/local/zitadel/bootstrap.sh
   docker compose -f deploy/local/compose.yaml up -d api
   PERSON=$(sh deploy/local/zitadel/token.sh person)
   ```

   Ports: api 8100, worker 8101, signer probes 8102, receiver 8103, all on 127.0.0.1 (never 8000). Stop it with
   `docker compose -f deploy/local/compose.yaml down` (add `-v` only to discard the database and the archive:
   the archive holds every signed document and DGII's answers).

Every call below carries `Authorization: Bearer $PERSON`, `Ledger-Mode: test` and, for a `POST`, an
`Idempotency-Key` of its own; after the company exists, `Ledger-Company: <its id>`.

## 4. The company, its certificate, its enrollment and its ranges

```http
POST /v1/companies
{"jurisdiction": {"country": "DO"}, "functional_currency": "DOP",
 "tax_ids": [{"scheme": "do.rnc", "value": "<the enrolled RNC>"}],
 "legal_name": "<its legal name, as in DGII's registry>",
 "address": {"line": "<street and number>", "municipality": "<code>", "province": "<code>"}}

POST /v1/certificates?modes=test
Ledger-Certificate-Password: <base64 of the bundle's password, as UTF-8>
Content-Type: application/x-pkcs12
<.dgii/certificate.p12, the bytes>

POST /v1/fiscal/enrollments
{"channel": "do-ecf-v1", "environment": "testecf"}
```

The certificate import is the signer's (`../signer/certificate-expiring.md` has its answers); send the bundle
with `curl --data-binary @.dgii/certificate.p12` and the password from a prompt, never from the command line's
history. **Ranges (Decision 29):** find the highest sequence per type the spike signed, read-only:

```powershell
$journal = Get-Content .dgii/out/used-encf.txt
$unread = @($journal | Where-Object { $_ -cnotmatch '^E\d{2}\d{10}$' }).Count
"lines that are no e-NCF: $unread"   # must be 0
$journal |
  Where-Object { $_ -cmatch '^E(\d{2})(\d{10})$' } |
  ForEach-Object { [pscustomobject]@{ Type = $Matches[1]; Sequence = [long]$Matches[2] } } |
  Group-Object Type |
  ForEach-Object { "{0}: highest {1}" -f $_.Name, ($_.Group | Measure-Object Sequence -Maximum).Maximum }
```

**The first line printed must say 0.** Any other count means a line the scan skips (a blank line, a lower-case
`e`, a stray character), whose sequence would be missed: stop, read those lines (`$journal | Where-Object {
$_ -cnotmatch '^E\d{2}\d{10}$' }`), and decide by hand what each held before registering any range. Never edit
the journal to make the count 0.

Then register, for each type to emit (31, 32, and 33/34 for notes), a range whose `first` is above that
highest (a type the spike never used starts anywhere), inside what TesteCF accepts (sequences 1 to 10,000,000,
type 32 to 50,000,000, expiring 31-12-2028):

```http
POST /v1/do/numbering_ranges
{"environment": "testecf", "ecf_type": "31", "first": <highest + 1001>, "last": <first + 9999>,
 "expires_on": "2028-12-31"}
```

`GET /v1/fiscal/enrollments/{id}`: `active`, nothing required. Still `pending`: it names what it requires
(`certificate`, `numbering_range`, `receiver_url`).

## 5. Authentication (F9)

The worker authenticates with TesteCF the first time it submits (DGII's seed signed by the signer as the
company). A token refused shows as the first invoice staying `pending`/`signed` and the worker logging
`job_failed` for `do_ecf.advance` with `do_ecf_dgii_unavailable` (DGII refused the signed seed: the
certificate, its chain or its delegation), or the document parked `certificate`. Record which in F9.

## 6. A credit invoice, and a consumer invoice as an RFCE (F1, F2, F11, F12, F13)

```http
POST /v1/invoices
{"type": "invoice",
 "buyer": {"legal_name": "<the buyer's name>", "tax_id": {"scheme": "do.rnc", "value": "<the buyer's RNC>"}},
 "lines": [{"description": "CEMENTO GRIS 42.5 KG", "quantity": "2", "unit_price": "500.00",
            "taxes": ["do.itbis.standard"]}]}

POST /v1/invoices
{"type": "invoice", "lines": [{"description": "CEMENTO GRIS 42.5 KG", "quantity": "2",
                               "unit_price": "500.00", "taxes": ["do.itbis.standard"]}]}
```

The answer (201) carries `fiscal[0].do.encf`, and, when the request signed within its budget, the
`security_code` and `qr_url`. Then follow `GET /v1/invoices/{id}` until `fiscal[0].status` is final: the 31
gets a `track_id` and `accepted` (or `accepted_conditionally`: copy its `fiscal[0].messages` into F11); the 32
(RD$1,180.00, ITBIS included) goes `route: rfce`, no track id, and is decided at once.

- **F1**: the RFCE decided with a DGII code means the `ecf` host's token works on `fc`. A 401 there even after
  a renewed token leaves the summary `signed`, the worker retrying it (`job_failed`,
  `do_ecf_dgii_unavailable`): record it, it means it does not.
- **F2**: the code the RFCE reception returned (`fiscal[0].messages`, and `outcome_code` in
  `do_ecf.documents`, read as [sql-by-hand.md](../platform/sql-by-hand.md) says). A code outside 1, 2, 4 is `do_ecf.submission_unknown` (`undocumented`): record it.
- **F12**: the RD$1,180.00 invoice went as an RFCE and the RD$295,000.00 one (step 9) in full.
- **F13**: if any submission is refused at the door (DGII's `error`/`mensaje`, `rejected` with its message),
  record DGII's words; never provoke one, and ask DGII in writing whether such a number is burned.

## 7. An anonymous consumer invoice and the QR codes (F4, F8)

The 32 of step 6 names no buyer (`<Comprador />`): its acceptance settles F4 (an empty required element is
accepted); a rejection naming `Comprador` settles it the other way. Open each `qr_url` in a browser: DGII's
`consultatimbre`/`consultatimbrefc` page must show the document. The printed representation
(`GET /v1/invoices/{id}/representation?channel=do-ecf-v1`) prints the same QR. F8: the 31's URL carries
`RncComprador`; the omission rule is settled by asking DGII, or recorded as open.

## 8. Delivery to DGII's simulator (F5, F6, F14, F15, F16)

TesteCF's directory names DGII's emisor-receptor simulator for buyers. The accepted 31 of step 6 is delivered
by the `do_ecf.deliver` job; follow `fiscal[0].do.delivery` and `delivery_reason`:

- `delivered`: the directory's URLs are bases (**F5**) and the simulator's receipt held our checks. The ARECF
  is in the archive (`do_ecf.documents.receipt_key`, read the same way).
- `failed`, `do_ecf_buyer_unavailable` after a 404: the URLs are full endpoints (**F5**).
- `not_applicable`, `entry_names_another_rnc`: the directory answered with an entry naming **another** RNC (**F16**): record
  which RNC it named by asking `consultadirectorio` by hand; widening the match is a decision, never a quiet
  fix.
- `failed`, `do_ecf_counterparty_certificate_refused` (kept as `arecf-refused`, never retried) (**F15**): the
  simulator signs with a certificate that is not the buyer's (DGII's own, likely: plan Owner item 33) or does
  not chain to the trusted roots. Record which.
- `failed`, `do_ecf_buyer_requires_authentication`, `do_ecf.delivery_needs_authentication` logged: buyer
  authentication is off (the default). **F14** is still DGII's to answer (a buyer's seed has DGII's own
  format: a hostile buyer could relay DGII's seed for our RNC and obtain a DGII token as us); switch it on only
  for this test (`PUT /v1/do/delivery_options` `{"buyer_authentication": true}`), each seed signed then
  audited (`do_ecf.buyer_seed.signed`), and retry ([delivery-failed.md](delivery-failed.md)).
- **F6**: the client always sends the bearer to `consultadirectorio`; a directory answer settles that it is
  reachable with it, a 401 there would contradict DT-DGII.

## 9. A large consumer invoice, in full

A 32 of RD$295,000.00 (one line of 250,000.00 at the standard ITBIS), naming its buyer: `route: reception`, a
track id, accepted (F12).

## Parked documents, and the one physical resend

A document left `outcome_unknown`, or parked for a person: [outcome-unknown.md](outcome-unknown.md). Never
issue the same invoice again to "retry" it. That runbook also keeps the one place a document is posted twice:
DGII's client re-posts it after a 401 with a renewed token, assuming a 401 means DGII did not take it; if
TesteCF ever lists a track id from before the second post, or answers it twice, record it at once.

## Open questions and how each is settled

| # | How TesteCF settles it (or why it does not) | Step |
|---|---|---|
| F1 | The RFCE decided with a DGII code, not a 401, using the `ecf` host's token | 6 |
| F2 | The code the RFCE reception returns; an undocumented one leaves it unknown and is recorded | 6 |
| F3 | Not exercised on purpose: routing never sends a type 32 under RD$250,000 to `recepcion` | — |
| F4 | The anonymous type 32 (`<Comprador />`) accepted, or rejected naming `Comprador` | 6, 7 |
| F5 | Delivery reaches the simulator's reception (bases), or a 404 (full endpoints) | 8 |
| F6 | The directory answers with the bearer sent; whether it needs one is asked of DGII | 8 |
| F16 | The directory's entry names the RNC asked about (delivery runs), or another (not applicable) | 8 |
| F7 | Out of scope (a 1,000-line type 32 is not exercised) | — |
| F8 | The 31's QR opens with `RncComprador`; the omission case is asked of DGII | 7 |
| F9 | The owner's certificate authenticates | 5 |
| F10 | Not DGII's to answer (regime review) | — |
| F11 | Messages of any `accepted_conditionally` copied verbatim | 6 |
| F12 | RD$1,180.00 goes as an RFCE, RD$295,000.00 in full, both accepted | 6, 9 |
| F13 | Any refusal at the door recorded (never provoked); asked of DGII | 6 |
| F14 | Asked of DGII; buyer seeds signed are audited meanwhile | 8 |
| F15 | The simulator's receipt held our checks, or the refusal's reason | 8 |

## 10. Record, then clean up

Fill in [`countries/do/docs/spike-findings.md`](../../../countries/do/docs/spike-findings.md) (one line per F row:
settled, how, DGII's words) and commit it. Copy no secret into it: no token, no password, no certificate, no
cédula, no full XML (DGII's messages and codes are fine). Keep `.dgii/out` (the spike's journal and documents)
for as long as TesteCF keeps submissions, and the stack's archive with it. Put `deploy/local/do-ecf.env` and the
roots back (`git checkout deploy/local/do-ecf.env`, `sh deploy/local/do/roots.sh`) before working against the
fake again. Never commit `.dgii/`.

CerteCF, DGII's certification proper, follows: [certecf.md](certecf.md).
