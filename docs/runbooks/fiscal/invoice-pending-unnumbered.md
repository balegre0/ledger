# Runbook: an invoice left `pending` with no channel document, and unvoidable

## What happened

When an invoice is issued, fiscal routes it to each channel the company is enrolled in (spec §8.5). For each
one it first mirrors `pending` into invoicing (the invoice's `fiscal[]` entry, version 1) and keeps the
routing (`fiscal.routings`), **then** asks the channel to number it. That order is what keeps a number from
being given to an invoice voided meanwhile, and it has a cost: once `pending` is mirrored, invoicing refuses to
void the invoice (a channel holds it: any fiscal status but `rejected` or `voided`), so the customer can only
credit it.

If the channel then keeps refusing to number it, the invoice stays issued, its `fiscal[]` `pending`, with no
channel document behind it:

- **No number to give** (fiscal-kit's `NumberUnavailableError`; do-ecf-v1's `NoNumberAvailableError`: the
  last number of the type went between the request's check and its numbering). The request answered 201 with
  `fiscal[]` pending; the recovery consumer (`fiscal.commercial_documents`) parks a `fiscal.route` job (key
  `<invoice uuid>:<channel>`, reason `range_missing`), which a range registered wakes. This mends itself once
  the company registers a range ([`../do/range-low.md`](../do/range-low.md)).
- **A refusal no retry mends**: the channel's `issue` raised `DraftObjectedError` (a defect: its own `check`
  passed the same draft) or `InvalidValueError` (a scope that is not the draft's tenant, an environment not of
  its mode). The recovery consumer's event is given up on at once: **its inbox row is dead**. Nothing parks,
  nothing retries, and nothing else says so.

## The alerts

| Signal | Means |
|---|---|
| `ledger_inbox_handled_total{consumer="fiscal.commercial_documents",outcome="dead"}` rising, and `inbox.handler_failed` with `consumer=fiscal.commercial_documents`, `dead=true` | The recovery path gave up on an issued invoice: for this runbook, a channel refusing it. Page. |
| `ledger_jobs_finished_total{kind="fiscal.route",outcome="dead"}` rising | A `fiscal.route` job died (50 failed runs): the routing it carried is stuck. Page. |
| `fiscal.route` jobs parked `range_missing` for more than a day | A company keeps issuing without a range: tell it ([`../do/range-low.md`](../do/range-low.md)). |

## What to look at

As the `fiscal` schema's owner (row-level security applies to the apps' roles), read-only:

```sql
-- The dead recovery events: each names its invoice (subject) and the error's code.
SELECT company_id, mode, event_id, subject AS invoice_id, attempts, last_error_code
  FROM fiscal.inbox
 WHERE consumer = 'fiscal.commercial_documents' AND status = 'dead';

-- The routings fiscal kept for an invoice (one per channel), and the enrollment each went through.
SELECT invoice_id, channel, enrollment_id, routed_at
  FROM fiscal.routings WHERE invoice_id = '<invoice uuid>';

-- The routing jobs parked or dead.
SELECT key, status, parked_reason, attempts, last_error_code, updated_at
  FROM fiscal.jobs WHERE kind = 'fiscal.route' AND status IN ('parked', 'dead');
```

Then the channel's side: for do-ecf-v1, `SELECT state FROM do_ecf.documents WHERE invoice_id = '<invoice uuid>'`
returns **no row** (that is this case: a row means the channel numbered it, and the invoice follows the
channel's runbooks instead). `GET /v1/invoices/{id}` (as the company) shows `fiscal[]` `pending`, version 1.

## What to do

1. **No range** (`fiscal.route` parked `range_missing`): the company registers a range; the job routes the
   invoice again and the channel numbers it. Nothing else to do.
2. **The dead inbox row** (`DraftObjectedError`, `InvalidValueError`): fix the defect first: the error's code
   and the invoice's draft (`GET /v1/invoices/{id}`) say which rule refused it; a release with the fix ships
   before anything is retried, or it dies again. Then make the row ready again (the consumer is idempotent per
   invoice and channel: a redelivery adds nothing that exists), with a ticket and a second person reviewing,
   as the schema's owner, in one transaction:

   ```sql
   BEGIN;
   UPDATE fiscal.inbox
      SET status = 'ready', attempts = 0, next_attempt_at = now(), last_error_code = NULL,
          lease_until = NULL, lease_token = NULL
    WHERE consumer = 'fiscal.commercial_documents' AND event_id = '<event uuid>' AND status = 'dead';
   -- UPDATE 1, else ROLLBACK.
   COMMIT;
   ```

3. **A dead `fiscal.route` job**: its `last_error_code` says why; once mended, re-arm it the same way
   (`fiscal.jobs`, `kind = 'fiscal.route'`, its `key`, `status = 'ready'`, `run_at = now()`, `attempts = 0`).
4. **If the channel will never number it** (the invoice should never have gone to that channel): there is no
   supported way yet to release it. LIVE Task 31 adds an operator command that releases such an invoice
   (mirrors `voided` for that channel, audited, so invoicing may void or credit it) or routes it again; plan
   Owner item 18 asks whether that is acceptable. Until then the invoice stays `pending`; the ticket records
   the invoice, the channel and the error, and the customer is told it cannot void it yet.

Never edit `fiscal.routings` (append-only) or invoicing's `fiscal[]` mirror by hand: the channel's own versions
follow fiscal's `pending`, and a hand-made status would be overwritten or refused as stale.
