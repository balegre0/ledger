# Runbook: an event fiscal gave up on

fiscal's consumers (`modules/fiscal/manifest.toml`, `[[events.consumes]]`) keep invoicing's mirror of each
channel's status, route issued invoices, review enrollments and wake parked documents. A consumer's inbox row
is **dead** when its handler raised an error no retry mends (`INVALID`, `CONFLICT`: a defect of the producer or
of fiscal) or spent its attempts; nothing handles that event again until a person does (spec §11: any dead
inbox row alerts).

## The alerts

Each is `ledger_inbox_handled_total{consumer=...,outcome="dead"}` rising on the worker, with the log line
`inbox.handler_failed` (`consumer`, `event_id`, `event_type`, `dead=true`):

| Signal | What is left undone |
|---|---|
| `ledger_inbox_handled_total{consumer="fiscal.commercial_documents",outcome="dead"}` | An issued invoice not routed by the recovery path: [invoice-pending-unnumbered.md](invoice-pending-unnumbered.md). |
| `ledger_inbox_handled_total{consumer="fiscal.channel_documents",outcome="dead"}` | A channel's status (accepted, rejected, delivered...) not mirrored into invoicing: the invoice's `fiscal[]` shows an older status than the channel's document. The next update of the same document mends it (the mirror is version-monotonic), but the last one of a document has no next. |
| `ledger_inbox_handled_total{consumer="fiscal.requirements",outcome="dead"}` | A channel's requirements of a company changed (a range registered) and fiscal did not mark the company's enrollments for review nor wake its `fiscal.route` jobs parked `range_missing`: an enrollment shows `pending` it no longer is, until its next review; parked routings wait. |
| `ledger_inbox_handled_total{consumer="fiscal.certificates",outcome="dead"}` | A certificate installed and the company's documents parked for one not woken (`do_ecf.advance` parked `certificate`): they wait until another wake. |
| `ledger_jobs_finished_total{kind="fiscal.route",outcome="dead"}` | See [invoice-pending-unnumbered.md](invoice-pending-unnumbered.md). |

## What to do

1. Read the row (as the `fiscal` schema's owner): `SELECT consumer, event_id, type, subject, company_id, mode,
   attempts, last_error_code FROM fiscal.inbox WHERE status = 'dead'`. Never copy its `payload` into a
   ticket.
2. The `last_error_code` names the cause. A defect is fixed and released first; an outage (the database, the
   channel's schema) is waited out.
3. Then make the row ready again: every fiscal consumer is idempotent (by event id, or by version for the
   mirror), so a redelivery adds nothing already done. With a ticket and a second person reviewing, in one
   transaction:

   ```sql
   BEGIN;
   UPDATE fiscal.inbox
      SET status = 'ready', attempts = 0, next_attempt_at = now(), last_error_code = NULL,
          lease_until = NULL, lease_token = NULL
    WHERE consumer = '<consumer>' AND event_id = '<event uuid>' AND status = 'dead';
   -- UPDATE 1, else ROLLBACK.
   COMMIT;
   ```

   An ordered consumer's later events of the same subject wait behind a dead head; readying it lets them pass.

Replaying a dead inbox row is an operator command of the LIVE milestone (spec §11's operator controls, plan
Task 31); it replaces the statement above, audited.
