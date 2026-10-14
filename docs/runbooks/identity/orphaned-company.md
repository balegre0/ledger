# Runbook: a company whose only owner does not exist

## What happened

identity does not ask the identity provider whether a subject exists when a role is granted (spec §17, known
limitations). An owner can therefore grant the owner role to a subject id that is no user or machine of Zitadel
(a typo, a user deleted since), then give up its own ownership. The company keeps an owner, as identity
guarantees, but nobody can act as it: no one can grant or take the owner role, and nobody may deactivate the
company unless an admin is left.

Symptoms: the customer reports that no one can manage owners; `GET /v1/companies/{id}/memberships` (as any member
with `membership.read`) lists every active owner with a `subject` that Zitadel does not know.

## Before changing anything

1. Open a ticket. Confirm who asks: a user who is an administrator of the organization in Zitadel
   (`ledger.organization.admin`), reached through the contact the customer registered, not through the request
   itself.
2. In Zitadel (console, or `GET /management/v1/users/{id}` with the organization's id in `x-zitadel-orgid`),
   confirm that every active owner's subject is unknown, and that the subject to become owner exists, is active
   and belongs to the company's organization.
3. Get a second person to review the statement below before it runs (two-person rule). Nothing else in the
   platform changes a membership outside identity's API.

## Restore an owner

Run as identity's database role (it owns the database; never a superuser, which would ignore row-level
security), in one transaction. The transaction-local settings are the scope identity itself would use, so the
policies let exactly that company's rows through.

```sql
BEGIN;
SELECT set_config('ledger.organization_id', '<organization uuid>', true),
       set_config('ledger.company_id', '<company uuid>', true);

-- The company is there, and active or not as the customer says.
SELECT id, status, version FROM companies WHERE id = '<company uuid>' FOR UPDATE;

-- The subject already has a membership (any role, active or revoked): make it the owner.
UPDATE memberships
   SET role = 'owner', status = 'active', version = version + 1,
       changed_at = now(), changed_by = 'support:<ticket>'
 WHERE company_id = '<company uuid>' AND subject = '<subject>';
-- It must say UPDATE 1. If it says UPDATE 0, the subject has no membership yet: insert one.
INSERT INTO memberships (id, organization_id, company_id, subject, role, status, version, changed_at, changed_by)
VALUES (uuidv7(), '<organization uuid>', '<company uuid>', '<subject>', 'owner', 'active', 1,
        now(), 'support:<ticket>');

-- Then, if the customer asks, revoke the owners that do not exist (never the one just restored).
UPDATE memberships
   SET status = 'revoked', version = version + 1, changed_at = now(), changed_by = 'support:<ticket>'
 WHERE company_id = '<company uuid>' AND subject IN ('<unknown subject>') AND status = 'active';

COMMIT;
```

Check the row counts each statement reports before the `COMMIT`; anything unexpected is a `ROLLBACK`.

## After

- The statements publish no event (`identity.membership.granted|revoked`): other services learn the new owner when
  their cached answer expires (30 s at most), and webhooks deliver nothing of it. Write in the ticket what changed,
  the versions before and after, and who reviewed it.
- Ask the restored owner to sign in and list the company's memberships: the change is then visible through the
  API like any other.

## Until it cannot happen

The fix is to verify a subject with Zitadel before granting it a role, and to clean memberships up when Zitadel
deletes a user: both come with the Zitadel user-sync work (Actions v2 webhooks).
