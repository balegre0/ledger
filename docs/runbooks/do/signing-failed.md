# Runbook: e-CF signed late, or not at all

`POST /v1/invoices` numbers an e-CF and signs it within the request's budget (spec §8.5 step 4); what it cannot
sign in time is left `numbered` for the `do_ecf.advance` job, which signs it, then sends it. Both sign through
the signer (`SignerPort`), as the issuing company.

## The alerts

| Signal | Level | Means |
|---|---|---|
| `do_ecf.signing_deferred` (`reason`: `timed_out` or the error's code, such as `do_ecf_signer_unavailable`, `do_ecf_archive_unavailable`, `do_ecf_certificate_missing`) | warning | A request left the signature to the job: the client got the e-NCF without its security code and QR, which the invoice gains once the job signs. A rate that climbs means the signer (or the archive) is slower than the budget: clients notice e-CF printed without their security code. Alert on the rate, not on one line. |
| `do_ecf.signing_failed` (`reason`: the error's code) | error | Signing failed in a way no retry mends: the signer refused the document (`do_ecf_document_refused`, with the signer's reason), the channel's checks of what came back failed, or a job named no document (`do_ecf_document_missing`, a defect). The document is `blocked`. Page. |
| `ledger_jobs_finished_total{kind="do_ecf.advance",outcome="dead"}` rising | (metric) | See [outcome-unknown.md](outcome-unknown.md): a dead advance job leaves a `numbered` document unsigned as well. |

A company that cannot sign (no certificate, one not valid at the moment of signing, the signer's clock) is not
an alert of this channel: its documents wait (`parked`, reason `certificate`) and a certificate installed wakes
them ([`../signer/certificate-expiring.md`](../signer/certificate-expiring.md); the signer down:
[`../signer/signer-unavailable.md`](../signer/signer-unavailable.md)).

## Find them

Every statement here runs as [sql-by-hand.md](../platform/sql-by-hand.md) says: `ledger_migrator` with
`SET ROLE do_ecf_owner`, never a superuser. A blocked document's advance job is parked `blocked`, so the job
queue (not under row-level security) names each one's tenant and document:

```sql
-- Blocked documents (they need a person), and companies waiting for a certificate.
SELECT organization_id, company_id, mode, key AS document_id, parked_reason, updated_at
  FROM do_ecf.jobs
 WHERE kind = 'do_ecf.advance' AND status = 'parked' AND parked_reason IN ('blocked', 'certificate');
```

Then the document, in a transaction scoped to its tenant (`do_ecf.documents` is under row-level security,
the owner included):

```sql
BEGIN READ ONLY;
SELECT set_config('ledger.organization_id', '<organization uuid>', true),
       set_config('ledger.company_id', '<company uuid>', true),
       set_config('ledger.mode', '<mode>', true);
SELECT id, invoice_id, environment, ecf_type, sequence, range_id, state, updated_at
  FROM do_ecf.documents
 WHERE company_id = '<company uuid>' AND id = '<document uuid>';
COMMIT;
```

## What to do

- **`timed_out` climbing**: the signer's latency (`ledger_grpc_request_duration_seconds` on the signer, and
  its backend), then the archive's. Nothing is lost; the job signs within seconds once the signer answers.
- **`blocked` after a range that ended before the moment of signing** (the range's `expires_on`): the number
  cannot be used. The document stays blocked; unblocking it (a new number, the invoice routed again) is an
  operator command of the LIVE milestone (plan Task 31). Meanwhile register the company's next range
  ([range-low.md](range-low.md)) so new invoices number.
- **`do_ecf_document_refused`**: the signer's reason names the profile's rule. A document the channel built
  that its own signer refuses is a defect: open an issue with the reason (never the document's bytes in a
  ticket; the archive keeps what was signed).
- **`do_ecf_document_missing`**: a job named a document that does not exist: a defect (the job is dead at once).
