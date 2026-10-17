# ledger-identity

Organizations, companies, their establishments, memberships and roles (spec §7.1, §7.7).

- Owns schema `identity` (roles `identity_owner`, `identity_rw`, `identity_purge`), id prefixes `org`, `cmp`,
  `mbr`, `est`, `cli`, the `identity.*` permissions (granted by its role table, `domain/roles.py`) and events.
- Public API: HTTP under `/v1/organization`, `/v1/companies`, `/v1/api_clients` (machine users bound to a mode
  and an API version) and `/v1/countries` (the installed regimes and channels as data), mounted by `api` (snapshot:
  [`openapi.json`](openapi.json)); events `identity.company.*`, `identity.membership.*`; and, for the other
  modules, `IdentityApi` ([`api.py`](src/ledger_identity/api.py)): company by id, company by tax id,
  permissions of a principal, lock the fiscal profile.
- Assembly: `assembly.py` (`MODULE`, entry point `ledger.modules: identity`).
- Runbooks: [a company whose only owner does not exist](../../docs/runbooks/identity/orphaned-company.md).
