-- Runs once, as the administrator, when PostgreSQL first creates its data directory (see compose.yaml).
--
-- `ledger_app` is the role services connect as. It is a plain login role, with every privilege that would
-- let it step around row-level security switched off by name (they are PostgreSQL's defaults; spelled out so
-- that nobody reads this as an oversight). It owns the database, so it can create the tables of a service's
-- migrations; the policies of those tables are `FORCE`d (see `tenant_isolation`), which is what subjects an
-- owner to them. The platform's tests create this very role and database.
--
-- The password is for a database that is reachable from this machine only (compose.yaml binds 127.0.0.1).
CREATE ROLE ledger_app LOGIN PASSWORD 'ledger_app' NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
CREATE DATABASE ledger OWNER ledger_app;

-- The identity service's own database and role, on the same terms: it owns its database, it is no superuser,
-- and it cannot bypass row-level security (the migrations force it on every table of a tenant's data).
CREATE ROLE identity LOGIN PASSWORD 'identity' NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
CREATE DATABASE identity OWNER identity;

-- The local Zitadel's database and role (compose profile `zitadel`): Zitadel makes its schema at its first start,
-- as the owner of its database; it needs no superuser either.
CREATE ROLE zitadel LOGIN PASSWORD 'zitadel' NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
CREATE DATABASE zitadel OWNER zitadel;
