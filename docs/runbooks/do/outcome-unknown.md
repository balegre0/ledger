# Runbook: an e-CF whose outcome at DGII is unknown

**The one rule: never send it again.** A document whose submission was marked (`submission_started_at`) may
already be at DGII. Nothing in the stack sends it a second time (spec Review Focus 1, Decision 8), and nobody
does by hand: not through the API, not by re-issuing the invoice, not with a script. Only DGII's answer about
it (its track ids, `consultarfce` for a summary) or a person settles it.

## The alerts

| Signal | Level | Means |
|---|---|---|
| `do_ecf.submission_unknown` (`reason`: the failure, `marked` or `undocumented`) | warning | A submission's answer was lost (a timeout, a broken connection, a 5xx after sending), a run found another's mark, or DGII answered a summary with a code it does not document. The `do_ecf.advance` job asks DGII after the settle delay (30 s): usually it settles by itself. Page nobody; count it. |
| `do_ecf.waiting_for_a_person` (`reason`: `outcome_unknown` or `answer_mismatch`) | error | The job gave up asking: DGII listed no fresh track id within 24 hours of the outcome becoming unknown (at once in CerteCF, which lists none), or DGII answered about **another** issuer or e-NCF. The job is parked; a person acts (below). Page. |
| `ledger_jobs_finished_total{kind="do_ecf.advance",outcome="dead"}` rising | (metric) | An advance job died: 50 failed runs, an `INVALID`/`CONFLICT` error, or its 15-day deadline. Its document stays where it was (`numbered`, `signed`, `submitted`, `in_process` or `outcome_unknown`) **and logs no `do_ecf.waiting_for_a_person`**: this metric is the only signal. Page. |

The runner logs `job_failed` (with `kind` and the error's `code`) for each failed run, `job_deadline_passed` and
`job_worker_lost` (with `kind`) when it buries jobs. Until the periodic gauges of spec §11 exist
(`ledger_jobs_dead{kind}`, `ledger_fiscal_documents{channel,state}`), alert on the counter's increase and
confirm in SQL.

**Noise to expect:** `job_failed` with code `do_ecf_dgii_unavailable` while a worker shuts down: the DGII
client is closed before the last runs end (`require_ready`), and each such run spends one of the job's 50
attempts. Harmless unless it repeats on every deploy for the same document.

## Find the documents

Run as the `do_ecf` schema's owner (row-level security applies to the apps' roles), read-only:

```sql
-- Parked for a person, by company.
SELECT j.company_id, j.key, j.parked_reason, j.updated_at
  FROM do_ecf.jobs j
 WHERE j.kind = 'do_ecf.advance' AND j.status = 'parked'
   AND j.parked_reason IN ('outcome_unknown', 'answer_mismatch');

-- Dead advance jobs and the documents they leave undecided.
SELECT j.company_id, j.key, j.last_error_code, j.attempts, j.deadline_at,
       d.id, d.environment, d.ecf_type, d.sequence, d.state, d.route,
       d.submission_started_at, d.track_id, d.track_id_since
  FROM do_ecf.jobs j
  JOIN do_ecf.documents d ON d.id::text = j.key
 WHERE j.kind = 'do_ecf.advance' AND j.status = 'dead'
   AND d.state NOT IN ('accepted', 'accepted_conditionally', 'rejected', 'voided');
```

The job's `key` is the document's id (`do_ecf.documents.id` as text). The company's invoice is `d.invoice_id`;
its `fiscal[]` entry shows the same state.

## Re-arm a dead advance job (until Task 31)

A fresh job is what the platform's `enqueue` makes of a dead one (attempts 0, a new deadline): it continues
where the document is, and a marked `signed` document is never sent by it (it finds the mark and asks DGII's
track ids). Fix the cause of the failures first (`last_error_code`), or it dies again. Open a ticket, have a
second person review the statement, run it as the schema's owner in one transaction, check the row count:

```sql
BEGIN;
UPDATE do_ecf.jobs
   SET status = 'ready', run_at = now(), attempts = 0, deadline_at = now() + interval '15 days',
       last_error_code = NULL, parked_reason = NULL, updated_at = now()
 WHERE kind = 'do_ecf.advance' AND key = '<document uuid>' AND status = 'dead';
-- It must say UPDATE 1; anything else is a ROLLBACK.
COMMIT;
```

## What to do, by what you found

1. **`signed` with a mark, the job dead** (a poison document: every run failed after the mark): the outcome is
   unknown. Never clear the mark. Ask DGII (step 3), then re-arm the job (above): it asks the track ids.
2. **`numbered`, `submitted`, `in_process`, the job dead**: nothing is lost; re-arm it. A `numbered` document has
   never left.
3. **`outcome_unknown`, parked**: ask DGII by hand, from a machine allowed to reach it, with the company's token
   (TesteCF and production: `consultatrackids` with `rncemisor` and `encf`; a summary: `consultarfce` with the
   security code; or DGII's portal, OFV). Record the answer in the ticket. Then:
   - DGII lists a track id received after the document was signed: the job adopts one only while its 24-hour
     window is open. Adopting a named track id, or reopening the window, is an operator command of the LIVE
     milestone (plan Task 31, `ledger-worker do-ecf adopt-track-id`); until it exists, making the parked job
     `ready` (the statement above with `status = 'parked'` in its `WHERE`) buys one lookup and one poll, no
     more (the window stays closed).
   - DGII has nothing: the number may still be burned at DGII. Abandoning it (burn it, route the invoice again
     with a new e-NCF) is the owner's decision (plan Owner item 3); never resend the same document.
4. **`answer_mismatch`**: DGII answered about another document than ours. Stop and compare the track id, the
   e-NCF and the issuer we sent (the archived e-CF, `ecf_key`) with what DGII answered. Raise it with DGII; a
   wake asks again, never a resend.
5. **A document parked `certificate` or `outcome_unknown` woken more than 15 days after its job was enqueued**
   dies at once (`deadline_passed`): a wake does not move the deadline (plan Owner item 13). Treat it as 1 or 2.

Task 31's operator commands (`ledger-worker do-ecf stuck|wake|adopt-track-id`) replace the SQL and the replay
above; they enqueue a fresh `do_ecf.advance` (or re-arm the deadline) for what they touch.

## The one physical resend there is (a 401)

DGII's client (`adapters/dgii/client.py`, `_authenticated`) answers a 401 to a document's `POST` by renewing the
token and posting the same bytes once more within the same call. It relies on a 401 meaning DGII did not take
the document. If DGII (TesteCF or production) ever answers a 401 to a `recepcion` or `recepcionfc` `POST` and
then lists a track id for that e-NCF from before the second `POST`, or answers it twice, record it at once (in
[`countries/do/docs/spike-findings.md`](../../../countries/do/docs/spike-findings.md) and the ticket): the
assumption is wrong and the client must stop resending on a 401.
