# ledger-identity

Organizations, companies, their establishments, memberships and roles (spec §7.1, §7.7).

- Owns schema `identity` (roles `identity_owner`, `identity_rw`, `identity_purge`), id prefixes `org`, `cmp`,
  `mbr`, the `identity.*` permissions (granted by its role table, `domain/roles.py`) and events.
- Public API: HTTP under `/v1/organization` and `/v1/companies`, mounted by `api` (snapshot:
  [`openapi.json`](openapi.json)); events `identity.company.*`, `identity.membership.*`; and, for the other
  modules, `IdentityApi` ([`api.py`](src/ledger_identity/api.py)): company by id, company by tax id,
  permissions of a principal, lock the fiscal profile.
- Assembly: `assembly.py` (`MODULE`, entry point `ledger.modules: identity`).
- Runbooks: [a company whose only owner does not exist](../../docs/runbooks/identity/orphaned-company.md).
