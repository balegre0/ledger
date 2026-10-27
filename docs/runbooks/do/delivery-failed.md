# Runbook: an accepted e-CF that did not reach its buyer

DGII requires an issuer to send each accepted e-CF to an electronic buyer (spec §8.6.5). The `do_ecf.deliver`
job does it: one buyer exchange per run, retried 1 min, 5 min, 30 min, 2 h, then every 6 h, for seven days from
the start of the delivery (Decision 21); then the delivery is `failed`. The invoice's `fiscal[0].do.delivery`
says where it is: `not_started`, `delivering`, `delivered`, `failed` or `not_applicable` (a summary, a rejected
document, a buyer DGII's directory does not list, or CerteCF, which publishes no directory).

## The alerts

| Signal | Level | Means |
|---|---|---|
| `do_ecf.delivery_failed` (`reason`: a code) | error | The delivery failed for good: the buyer's signed receipt said *not received* (`not_received_<reason>`), its receipt failed our checks (`do_ecf_counterparty_certificate_refused`, `do_ecf_arecf_mismatch`, `do_ecf_signature_invalid`: kept as `arecf-refused`, never retried), or the buyer stayed out of reach for seven days (the last failure's code: `do_ecf_buyer_unavailable`, `do_ecf_buyer_endpoint_refused`, `do_ecf_buyer_requires_authentication`, ...). Tell the customer. |
| `do_ecf.delivery_needs_authentication` | error | The buyer requires a token and the company keeps buyer authentication off (Decision 19, the default while plan Owner item 8's F14 is open). Logged at **every** attempt until the company switches it on or the seven days end. Tell the customer. |
| `ledger_jobs_finished_total{kind="do_ecf.deliver",outcome="dead"}` rising | (metric) | A delivery job died (50 failed runs, a defect, or its 8-day deadline). Its delivery stays `not_started` or `delivering` and **logs no `do_ecf.delivery_failed`**: this metric is the only signal. Page. |

## Find them

Every statement here runs as [sql-by-hand.md](../platform/sql-by-hand.md) says: `ledger_migrator` with
`SET ROLE do_ecf_owner`, never a superuser. `do_ecf.documents` is under row-level security (the owner
included); the job queue is not, and names each job's tenant and document (its `key`):

```sql
-- Delivery jobs that died (stuck deliveries), and the companies whose deliveries ended lately (failed ones
-- among them): the tenant and the document of each.
SELECT organization_id, company_id, mode, key AS document_id, status, last_error_code, attempts, deadline_at
  FROM do_ecf.jobs
 WHERE kind = 'do_ecf.deliver'
   AND (status = 'dead' OR (status = 'done' AND updated_at > now() - interval '8 days'));
```

Then, per tenant, its deliveries that failed or are stuck:

```sql
BEGIN READ ONLY;
SELECT set_config('ledger.organization_id', '<organization uuid>', true),
       set_config('ledger.company_id', '<company uuid>', true),
       set_config('ledger.mode', '<mode>', true);
SELECT id, invoice_id, environment, ecf_type, sequence, delivery_state, delivery_reason, delivery_since,
       delivery_attempts
  FROM do_ecf.documents
 WHERE company_id = '<company uuid>' AND delivery_state IN ('failed', 'not_started', 'delivering');
COMMIT;
```

A `not_started` or `delivering` row whose job is `dead` (the first query) is stuck; one whose job is `ready`,
`running` or `parked` is still being tried. `GET /v1/do/delivery_options` (as the company) says whether buyer
authentication is on.

## The cure: the retry

The client, or an operator acting for it with a token of a member holding `do_ecf.deliveries.manage`, starts
the delivery again:

```http
POST /v1/do/deliveries/<inv_…>/retry
Authorization: Bearer <token>
Ledger-Company: <company id>
Ledger-Mode: test | live
Idempotency-Key: <a new key>
```

It moves the delivery to `delivering` with seven new days, forgets the earlier receipt, audits the retry,
publishes the e-CF's new version and enqueues the job again, in one unit. It accepts a delivery that `failed`
and one stuck `not_started` or `delivering` **once its job is done, dead or gone**; any other is 409
`do_ecf_delivery_not_retryable` (its job may still run: wait for it). An invoice without an e-CF in that
company and mode is 404 `do_ecf_delivery_not_found`.

Before retrying, mend the cause, or the retry fails the same way:

- `do_ecf_buyer_requires_authentication`: the company switches buyer authentication on
  (`PUT /v1/do/delivery_options` with `{"buyer_authentication": true}`); each buyer seed it then signs is
  audited (`do_ecf.buyer_seed.signed`, the buyer masked).
- `not_received_<reason>`: the buyer's own signed word (its specification, our signature, a duplicate, not its
  RNC): a retry sends the same bytes and gets the same answer. The customer settles it with the buyer.
  `not_received_duplicate` is recorded `delivered` (plan Owner item 19 asks whether it should be a failure).
- `do_ecf_counterparty_certificate_refused` / `do_ecf_arecf_mismatch`: the buyer's receipt was signed by a
  certificate that does not chain to `LEDGER_DO_ECF_TRUSTED_ROOTS` or does not name the buyer, or it names
  another document. The refused receipt is in the archive (`arecf-refused`). If the buyer's certification
  authority is missing from the trusted roots (plan Owner item 7), the owner adds it and the worker restarts
  (it reads the roots at start); TesteCF's simulator may sign with DGII's own certificate (plan Owner item 33).
- `do_ecf_buyer_unavailable`, `do_ecf_buyer_endpoint_refused`: the buyer's receiver is down, or the URL DGII's
  directory lists is not an `https` public host (refused on purpose, never widened). The customer tells the
  buyer; retry once it is reachable. DGII's directory is cached per worker for a while: a buyer that just
  changed its URL waits for the cache (24 h for an entry, 1 h for "no receiver").

A delivery that is `not_applicable` because DGII's directory lists no receiver for the buyer is not a failure:
the customer hands the buyer the printed representation (`GET /v1/invoices/{id}/representation`).
