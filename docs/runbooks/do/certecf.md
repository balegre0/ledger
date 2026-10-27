# Runbook: DGII's certification (CerteCF), step by step

DGII certifies a taxpayer (or a provider's client) as an electronic issuer in its certification portal, in 14
steps (*Pasos*), against the CerteCF environment. This maps each step to what the stack does, and to what a
person does. The step names follow DGII's certification guide for electronic issuers (CERT-EMI, `06-dgii-facts`,
July 2025) as the research notes record them; confirm them against the portal when the owner opens it (plan
Owner item 1), and correct this table, not the stack.

Run [testecf.md](testecf.md) first: CerteCF assumes every finding TesteCF can settle is settled. The stack runs
as in testecf.md step 3, with an enrollment in `certecf` (test mode) instead of `testecf`, and ranges for
CerteCF as DGII's test set authorizes them.

**What CerteCF lacks, and what the stack does about it:**

- No `consultatrackids`: a submission whose outcome is unknown cannot be settled by DGII's track ids, so the
  document parks for a person at once (`do_ecf.waiting_for_a_person`, `outcome_unknown`):
  [outcome-unknown.md](outcome-unknown.md). Never resend it; ask DGII through the portal.
- No `consultadirectorio`: delivery to a buyer is `not_applicable` (`no_directory_in_environment`), and so is
  the emitter's copy of an approval (plan Owner item 20 asks how DGII exercises delivery in certification).
- DGII itself sends to our receiver in Pasos 9 and 11: the receiver must be reachable from the internet at
  `https://<receiver host>/do-ecf-v1/certecf/<RNC>` ([receiver-registration.md](receiver-registration.md)).

| Paso | DGII | What the stack does / what a person does |
|---|---|---|
| 1 | Application (postulación) accepted; the portal opens | Person: the application in OFV, with the receiver's URLs (Recepción, Aprobación Comercial, Autenticación) for CerteCF. The company exists in the stack, its certificate imported (`POST /v1/certificates`), enrolled `certecf`. |
| 2 | Data tests: the e-CF of DGII's test set | Person: download DGII's test set (its rows give each document's data and e-NCF). Register the ranges it names (`POST /v1/do/numbering_ranges`, `environment: certecf`). Issue each row through `POST /v1/invoices` (types 31-47 as enabled; 41 and 47 are not issued yet: plan BACKLOG §1 item 11). The worker signs, sends and polls each; `fiscal[0].status` shows DGII's answer. A consumer invoice under RD$250,000 is sent as an RFCE to `recepcionfc`; its full signed e-CF (the archive's `ecf_key`) is uploaded by the person in the portal's "Facturas de consumo < 250Mil" screen. Documents with ISC lines cannot be issued yet (plan Task 32, Owner item 6). |
| 3 | Data tests: commercial approvals | Person: the approvals the set lists, decided through `POST /v1/do/received_documents/{id}/approval` for documents received, or as the set prescribes; the `do_ecf.approve` job sends each ACECF to DGII ([approval-unsent.md](approval-unsent.md)). |
| 4 | Simulation tests: e-CF | The same issuing path as Paso 2, the documents the person chooses; the stack's outcomes as in Paso 2. |
| 5 | Simulation of the printed representation | `GET /v1/invoices/{id}/representation?channel=do-ecf-v1`: the PDF (e-NCF, security code, QR). Person: upload as the portal asks. |
| 6 | Validation of the printed representation | Person: DGII reviews; corrections to the layout are code changes (plan Owner item 5). |
| 7 | URLs of the test services | Person: confirm or update the receiver's CerteCF URLs in the portal. The receiver must answer from now on. |
| 8 | Start of the reception test; DGII's root certificate downloadable | Person: download DGII's root and add it to `LEDGER_DO_ECF_TRUSTED_ROOTS` (the receiver and the worker read it at start: restart them). If DGII signs with a certificate that does not name its sender, its SHA-256 fingerprint goes in `LEDGER_DO_ECF_TEST_SENDER_STANDINS` (Decision 17; honoured in `testecf` and `certecf` only). |
| 9 | Reception: DGII sends e-CF, expects ARECF | The receiver authenticates DGII (seed, signed seed, token), answers each e-CF with an ARECF signed as the company, records it (`GET /v1/do/received_documents`). Watch `do_ecf.reception` lines (`outcome`) and the archive. A `signature` outcome means DGII's certificate does not chain to the roots of Paso 8. |
| 10 | Start of the commercial-approval reception test | Person: nothing but readiness. |
| 11 | Reception of commercial approvals: DGII sends ACECF | The receiver records each buyer's word on the company's e-CF (`commercial_approval` in the invoice's `fiscal[0].do`). A 400 means the ACECF did not read or named a document we did not issue. |
| 12 | URLs of the production services | Person: register the receiver's production URLs (`.../do-ecf-v1/ecf/<RNC>`). Production requires the LIVE milestone (the live gate, plan Task 31) before any live enrollment activates. |
| 13 | Sworn statement (declaración jurada) | Person: the owner's, outside the stack. |
| 14 | Authorization | Person: DGII authorizes the issuer. The company's live enrollment, its live ranges and its live certificate follow, after the LIVE tasks (28-31). |

Record every DGII message and code met on the way in
[`countries/do/docs/spike-findings.md`](../../../countries/do/docs/spike-findings.md) (no secret, no full XML),
and the portal's step names if they differ from this table.
