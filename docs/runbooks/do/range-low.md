# Runbook: a company's e-NCF range running low or expiring

DGII authorizes each company's e-NCF numbers in ranges, per e-CF type and environment, most with an expiry
date (TesteCF's expire 31-12-2028). The company registers each range DGII authorized
(`POST /v1/do/numbering_ranges`); every invoice routed to do-ecf-v1 takes the next number of its type
(spec §8.6.3). A company with no number left cannot issue that type.

## The alerts

Spec §11 alerts when a range has **less than 20 % left or expires in less than 7 days**. That watch (a
schedule reading every active range) is the LIVE milestone's (plan Task 29:
range watch and numbering accounting); until it exists nothing pages before the numbers run out, and what
shows is:

| Signal | Means |
|---|---|
| `422 invalid_document` with `do_ecf_range_missing` on `POST /v1/invoices` | No number of that type can be given now in the company's environment: the invoice is refused, nothing stored. The customer sees it at once. |
| An enrollment `pending`, requiring `numbering_range` (`GET /v1/fiscal/enrollments/{id}`) | The environment has no usable range at all. |
| A `fiscal.route` job `parked` with reason `range_missing` | The last number went between the request's check and its numbering: the invoice is issued, its `fiscal[]` `pending`, and the job routes it again once a range is registered ([`../fiscal/invoice-pending-unnumbered.md`](../fiscal/invoice-pending-unnumbered.md)). |

Until Task 29, check by hand (weekly, and before a company's month end), as
[sql-by-hand.md](../platform/sql-by-hand.md) says (`ledger_migrator` with `SET ROLE do_ecf_owner`, never a
superuser). `do_ecf.numbering_ranges` is under row-level security, the owner included, so one transaction sees
one company's ranges: no single query sweeps every company (a read role for that, or Task 29's watch, is plan
Owner item 34). The companies that issue are those with advance jobs:

```sql
-- The tenants that issued e-CF in the last 35 days.
SELECT DISTINCT organization_id, company_id, mode
  FROM do_ecf.jobs
 WHERE kind = 'do_ecf.advance' AND created_at > now() - interval '35 days';
```

Then, for each tenant:

```sql
BEGIN READ ONLY;
SELECT set_config('ledger.organization_id', '<organization uuid>', true),
       set_config('ledger.company_id', '<company uuid>', true),
       set_config('ledger.mode', '<mode>', true);
-- Its active ranges with less than 20 % left, or expiring within 7 days.
SELECT id, environment, ecf_type, first, last, next, expires_on, (last - next + 1) AS remaining,
       round(100.0 * (last - next + 1) / (last - first + 1), 1) AS percent_left
  FROM do_ecf.numbering_ranges
 WHERE company_id = '<company uuid>' AND status = 'active'
   AND ((last - next + 1) < 0.2 * (last - first + 1)
        OR (expires_on IS NOT NULL AND expires_on < current_date + 7));
COMMIT;
```

A type with no active range left at all shows no row here: the company's `422 do_ecf_range_missing` and its
enrollment's `numbering_range` requirement say so.

A company sees its own: `GET /v1/do/numbering_ranges` (`remaining`, `expires_on`).

## What to do

1. Tell the company: it requests a new range from DGII (through its Oficina Virtual, OFV)
   for the type and environment. We never request ranges on its behalf.
2. The company (a member with `do_ecf.ranges.manage`) registers it exactly as DGII authorized it:

   ```http
   POST /v1/do/numbering_ranges
   Authorization: Bearer <token>
   Ledger-Company: <company id>
   Ledger-Mode: test | live
   Idempotency-Key: <a new key>
   Content-Type: application/json

   {"environment": "ecf", "ecf_type": "31", "first": 1001, "last": 5000, "expires_on": "2027-12-31"}
   ```

   Registering it publishes `fiscal_channel.requirements.changed`: the company's enrollment is reviewed, and a
   `fiscal.route` job parked `range_missing` is woken.
3. A range that ended before a document numbered from it was signed blocks that document (`blocked`:
   [signing-failed.md](signing-failed.md)); the new range does not unblock it (plan Task 31).

Ranges registered for TesteCF start above the highest sequence the spike's journal used per type
([testecf.md](testecf.md), Decision 29).
