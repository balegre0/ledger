# ledger-devtools (dev-only)

`ledger-devtools sync | check` (spec §4.7, [ADR 0009](../../docs/adr/0009-manifest-driven-workspace.md)).

- `sync` writes what the manifests imply: each package's `pyproject.toml` and `_manifest.py`, `.importlinter`,
  `deploy/local/postgres/init.sql`, each module's `openapi.json`, and the root `pyproject.toml`'s generated
  block (the `workspace` dependency group, `[tool.uv.sources]`, ruff's `known-first-party`). From the contracts
  (`contracts/proto/ledger/<module>/v<N>/<name>.proto`) it writes each module's gRPC code into
  `modules/<module>/src/ledger_<module>/adapters/grpc/_generated/v<N>/`, imported as
  `ledger_<module>.adapters.grpc._generated.v<N>` (protoc in a spawned process, with mypy-protobuf's stubs;
  gRPC stubs only for a contract with a service). That directory is the one the gate leaves unchecked, by path.
- `check` validates every manifest and how they agree; refuses a `.proto` that is not a module's sound contract
  (its layout, its module, its package, what protoc says of all of them compiled together, at the file it names),
  an `adapters/grpc/_generated` directory anywhere but a module's import package, and a file in one that `sync`
  did not write; fails on a stale or orphaned generated file; and checks the repository around them (members
  without a manifest, image pins and digests, the build context).
- The generated code is what the locked grpcio-tools (its protoc and protobuf runtime version) and mypy-protobuf
  write: after `uv lock` moves either, run `uv run poe sync` and commit what changed, or `check` fails.
- buf runs from the root's tasks in the pinned image, without network: `poe proto-lint`, and
  `poe proto-breaking [--base REF]` (contracts against the ref's, extracted with `git archive`; an explicit ref
  must name a commit, the default `main` may be absent; passes while the ref holds no `.proto`).
- Deferred: `gate`, `affected`, `scaffold`, CODEOWNERS (spec §16.4).
