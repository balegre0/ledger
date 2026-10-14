# ledger-testing (dev-only)

What every package's tests share (spec §14), never installed in an image.

- The pytest plugin `ledger` (entry point `pytest11`): one PostgreSQL server per run, a database per xdist
  worker cloned from a template the packages' `pytest_ledger_template` hooks prepare, a local OpenID provider,
  a manual clock, the logging baseline.
- A module's fixtures: its conftest returns a `ModuleUnderTest` (fixture `module_under_test`) and gets
  `migrated`, `database`, `module_database`, `admin`, `settings`, `api`, `client` and the `fresh` database ones.
- Helpers: `database` (roles, schemas, migrations as production makes them), `migrations` (what a schema's
  revisions built), `api` (tokens and requests), `clock`, `waiting`.
