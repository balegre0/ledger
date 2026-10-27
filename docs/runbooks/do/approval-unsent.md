# Runbook: a commercial approval that did not reach a party

A company approves or rejects an e-CF it received (`POST /v1/do/received_documents/{id}/approval`, once). The
`do_ecf.approve` job signs the ACECF once, archives it, then sends it to two parties (spec §8.6.5 step 6):
**DGII** (`aprobacioncomercial`, sent once: an unknown outcome is never sent again) and **the emitter** (the
URL DGII's directory lists for it), each tried on Decision 21's schedule for seven days from the decision. Each
party's outcome is `pending`, `sent`, `refused`, `not_applicable`, `outcome_unknown` or `failed`
(`GET /v1/do/received_documents/{id}`, `approval.emitter` and `approval.dgii`).

## The alerts

| Signal | Level | Means |
|---|---|---|
| `do_ecf.approval_unsent` (`party`: `emitter` or `dgii`; `outcome`: `refused`, `failed` or `outcome_unknown`) | error | A party will not get the approval: it refused it (never sent again), seven days passed without reaching it, or DGII may have it and its answer was lost (never sent again; DGII publishes no query for approvals). Tell the customer; for `outcome_unknown`, settle it (below). |
| `do_ecf.approval_needs_authentication` | error | The emitter requires a token and the company keeps buyer authentication off (Decision 19). Logged at every attempt; the customer may switch it on (`PUT /v1/do/delivery_options`), and the job, still trying, then signs the emitter's seed. |
| `ledger_jobs_finished_total{kind="do_ecf.approve",outcome="dead"}` rising | (metric) | An approval job died (50 failed runs, a defect, or its 8-day deadline). A party it had not settled stays `pending` **and logs no `do_ecf.approval_unsent`**: this metric is the only signal (plan Owner item 28 asks whether such a party should be marked `failed`). Page. |

## Find them

As the `do_ecf` schema's owner, read-only:

```sql
SELECT r.company_id, r.id, r.environment, r.ecf_type, r.sequence, r.approval_decision,
       r.approval_requested_at, r.approval_emitter, r.approval_emitter_reason,
       r.approval_dgii, r.approval_dgii_reason, r.approval_dgii_started_at, j.status, j.last_error_code
  FROM do_ecf.received_documents r
  LEFT JOIN do_ecf.jobs j ON j.kind = 'do_ecf.approve' AND j.key = r.id::text
 WHERE r.approval_decision IS NOT NULL
   AND (r.approval_emitter IN ('pending', 'failed', 'outcome_unknown', 'refused')
        OR r.approval_dgii IN ('pending', 'failed', 'outcome_unknown', 'refused'));
```

A party `pending` whose job is `dead` (or gone) is stuck; one whose job is `ready`, `running` or `parked` is
still being tried.

## Settling it by hand (until Task 31's commands)

No route retries an approval and no operator command exists yet (plan Owner items 24, 25 and 27): a person with
database access settles each case, with a ticket and a second person reviewing the statement, as the schema's
owner, in one transaction, checking the row count before `COMMIT`.

- **DGII `outcome_unknown`** (`approval_dgii_started_at` set, no answer): the ACECF may be at DGII. **Never send
  it again.** Ask DGII (OFV, or DGII's support, naming the e-NCF and the buyer's RNC); record its answer in the
  ticket. If DGII has it, record it `sent`; if DGII confirms it does not, the owner decides whether it may be
  sent again (Owner item 24): never by clearing the mark on a hunch.

  ```sql
  BEGIN;
  UPDATE do_ecf.received_documents
     SET approval_dgii = 'sent', approval_dgii_reason = 'settled_by_hand',
         approval_sent_to_dgii_at = '<when DGII says it took it>', version = version + 1
   WHERE id = '<received document uuid>' AND approval_dgii = 'outcome_unknown';
  -- UPDATE 1, else ROLLBACK.
  COMMIT;
  ```

- **A party `pending` with its job dead**: the job's `last_error_code` says why it died; mend that first. Then
  re-arm it (attempts 0, a new deadline): the job resumes from what is recorded (the signed ACECF read back from
  the archive, a party already settled left alone, a DGII mark found never resent).

  ```sql
  BEGIN;
  UPDATE do_ecf.jobs
     SET status = 'ready', run_at = now(), attempts = 0, deadline_at = now() + interval '8 days',
         last_error_code = NULL, parked_reason = NULL, updated_at = now()
   WHERE kind = 'do_ecf.approve' AND key = '<received document uuid>' AND status = 'dead';
  -- UPDATE 1, else ROLLBACK.
  COMMIT;
  ```

  The seven days count from the decision (`approval_requested_at`): a party whose seven days have passed is
  recorded `failed` by the job's next run, and `do_ecf.approval_unsent` says so.

- **`failed` or `refused`**: nothing resends it (Owner item 25). The customer reaches the emitter another way;
  record what was done in the ticket.

Every statement above changes no audit row by itself: write in the ticket what changed, the version before and
after, and who reviewed it. Task 31's commands replace them, audited.
