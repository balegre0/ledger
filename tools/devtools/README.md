# ledger-devtools (dev-only)

`ledger-devtools sync | check` (spec §4.7, [ADR 0009](../../docs/adr/0009-manifest-driven-workspace.md)).

- `sync` writes what the manifests imply: each package's `pyproject.toml` and `_manifest.py`, `.importlinter`,
  `deploy/local/postgres/init.sql`, each module's `openapi.json`, and the root `pyproject.toml`'s generated
  block (the `workspace` dependency group, `[tool.uv.sources]`, ruff's `known-first-party`). From each contract
  (`contracts/proto/ledger/<module>/v<N>/<name>.proto`) it writes the module's gRPC code into
  `modules/<module>/src/ledger_<module>/adapters/grpc/_generated/` (protoc in process, with mypy-protobuf's
  stubs; gRPC stubs only for a contract with a service), the one directory the gate leaves out by path.
- `check` validates every manifest and how they agree, refuses a `.proto` that is not a module's contract (its
  layout, its module, its package), fails on a stale or orphaned generated file, and checks the repository
  around them (members without a manifest, image pins and digests, the build context).
- buf runs from the root's tasks in the pinned image: `poe proto-lint`, and `poe proto-breaking [--base REF]`
  (contracts against `main`'s by default, extracted with `git archive`; passes while the ref holds no `.proto`).
- Deferred: `gate`, `affected`, `scaffold`, CODEOWNERS (spec §16.4).
