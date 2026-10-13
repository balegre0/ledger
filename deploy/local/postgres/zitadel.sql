-- Written by hand: Zitadel is no manifest package, so `ledger-devtools sync` (which writes init.sql, the ledger's
-- roles, database and schemas) knows nothing of it. Runs once, as the administrator, after init.sql, when
-- PostgreSQL first creates its data directory (compose.yaml).
--
-- The local Zitadel's database and role (compose profile `zitadel`): Zitadel makes its schema at its first start,
-- as the owner of its database; it needs no superuser either. The password is for a database that is reachable
-- from this machine only (compose.yaml binds 127.0.0.1).
CREATE ROLE zitadel LOGIN PASSWORD 'zitadel' NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
CREATE DATABASE zitadel OWNER zitadel;
