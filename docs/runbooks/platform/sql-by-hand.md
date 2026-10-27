# Runbook: reading and changing a module's rows by hand

Some runbooks settle what no command settles yet (the LIVE milestone's operator commands, plan Task 31) with
SQL. This is how every such statement runs; the runbooks only give the statements.

## Who runs it, as which role

- Log in as `ledger_migrator` and `SET ROLE <schema>_owner` (`do_ecf_owner`, `fiscal_owner`, ...): the role
  that owns the schema. **Never a superuser** (nor any role with `BYPASSRLS`): it ignores row-level security
  and would read or change any company's rows.
- Open a ticket first, and have a second person review each changing statement before it runs (two-person
  rule). Write in the ticket what changed, the values before and after, and who reviewed it: these statements
  write no audit row.

## Row-level security applies to the owner too

Every tenant table (`do_ecf.documents`, `do_ecf.received_documents`, `do_ecf.numbering_ranges`,
`fiscal.routings`, `fiscal.enrollments`, ...) **forces** row-level security on its owner as well
(`ledger_platform.data.rls`): a row is visible only to a transaction whose settings name its organization,
company and mode. Without them a query returns **nothing**, which looks like "no such row". The queue tables
(`<schema>.jobs`, `<schema>.inbox`) are not under row-level security, and each of their rows names its
`organization_id`, `company_id` and `mode`.

So:

1. **Find the candidates** in `jobs` or `inbox` (no settings needed): each gives a tenant (organization,
   company, mode) and a key.
2. **For each tenant, one transaction** whose first statement sets the scope, as the module itself does:

   ```sql
   BEGIN;
   SELECT set_config('ledger.organization_id', '<organization uuid>', true),
          set_config('ledger.company_id', '<company uuid>', true),
          set_config('ledger.mode', '<mode>', true);
   -- the runbook's statements, naming the company and the row by id
   COMMIT;
   ```

   `true` makes each setting local to the transaction: it ends with it. Never set them for the session.
3. Check the row count every statement reports before `COMMIT`; anything unexpected is a `ROLLBACK`.
4. A statement on `jobs` or `inbox` names the tenant in its `WHERE` (`organization_id`, `company_id`, `mode`)
   beside the key, as the platform's own `enqueue` guards a key against another tenant's.

A question across companies (every range running low, every blocked document) cannot be asked this way: one
transaction sees one tenant. Ask it per company found in the queue tables, or wait for the read role or the
watch the runbook names (plan Owner item 34).
