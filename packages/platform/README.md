# ledger-platform

The chassis every app is built on (spec §5.2): each subpackage's `__all__` is its public surface, layered
`runtime` > `http | grpc` > `events | jobs` > `auth` > `data` > `observability` > `_manifest` > `config`.

- Owns schema `platform` (routing, schedule leases) and every schema's chassis tables (`chassis_schema_v1`);
  the purge jobs and schedules of its manifest.
- `config`: `Settings` (`LEDGER_*`, fail-closed), `PackageSettings` (a package's `LEDGER_<SECTION>_*`), the
  manifest model. `runtime`: `App`, the contexts and parts of the composition seam, `serve(role)`.
  `data`: `Database`, `BoundDatabase`, `UnitOfWork`, `ModuleTable`, RLS, migrations. `events`, `jobs`,
  `auth`, `http`, `observability`: see each `__init__`.
- Console script: `ledger-probe` (the image's health check).
- Decisions: [ADR 0001](../../docs/adr/0001-modular-monolith.md), [0002](../../docs/adr/0002-postgres-eventing.md),
  [0004](../../docs/adr/0004-schema-per-module.md).
