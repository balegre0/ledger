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
