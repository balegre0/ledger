# ledger-kernel

The vocabulary every package shares (spec §5.1), standard library only (import-linter contract 2).

- Owns: ids and TypeIDs (`ids`), tenancy and scopes (`tenancy`), principals, permissions and the built-in roles
  (`access`), money and currencies (`money`), jurisdictions, qualified tax ids, dated values (`dated`), the
  regime schema types and the tax calculator (`regimes`), event base (`events`), pagination, clocks (`clock`),
  the error kinds every boundary maps (`errors`).
- Public API: each module's names; nothing here loads, connects or reads the environment.
- No schema, no settings, no entry point. Loading regimes is the platform's (`ledger_platform.runtime.registries`).
