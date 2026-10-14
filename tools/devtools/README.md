# ledger-devtools (dev-only)

`ledger-devtools sync | check` (spec §4.7, [ADR 0009](../../docs/adr/0009-manifest-driven-workspace.md)).

- `sync` writes what the manifests imply: each package's `pyproject.toml` and `_manifest.py`, `.importlinter`,
  `deploy/local/postgres/init.sql`, each module's `openapi.json`, and the root `pyproject.toml`'s generated
  block (the `workspace` dependency group, `[tool.uv.sources]`, ruff's `known-first-party`).
- `check` validates every manifest and how they agree, fails on a stale or orphaned generated file, and checks
  the repository around them (members without a manifest, image pins and digests, the build context).
- Deferred: `gate`, `affected`, `scaffold`, CODEOWNERS (spec §16.4).
