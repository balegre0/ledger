# 0009. A manifest-driven uv workspace with generated lists and contracts

- Status: Proposed
- Date: 2026-10-13 (as built in v2-M1: `gate` deferred, the root's lists generated, test dependencies, settings)
- Spec: `docs/superpowers/specs/2026-10-13-ledger-platform-v2-design.md` §4

## Context

v1's root `pyproject.toml` listed every package in at least eight places, each Dockerfile repeated every
workspace manifest, and a workspace test existed only to catch forgotten edits. With many modules and countries
this breaks first. Airflow, Home Assistant and the Azure SDK solve it with a manifest per unit and a repository
tool that generates and validates everything derived from it.

## Decision

- uv workspace members by glob (`packages/*`, `modules/*`, `countries/*`, `countries/*/testing`, `apps/*`,
  `tools/*`); one package per unit (`ledger-<name>`, one top-level import package each).
- `manifest.toml` per package is the source of truth: name, kind, owners, status, a settings section,
  dependencies (workspace packages, modules, third-party distributions and the test dependencies), schema, id
  prefixes, permissions with their built-in roles, events published (with optional public names) and consumed,
  jobs, schedules, extension points and provided ports, HTTP prefixes; for countries also regimes and channels
  (with environments, document kinds, tier and a settings section). The platform has a manifest too (its schema,
  prefix, jobs, schedules). The model is `ledger_platform.config.manifest`: devtools validates with it and every
  generated `_manifest.py` imports it.
- `ledger-devtools`, a tested workspace member, built now with two commands: `sync` generates each package's
  `pyproject.toml` (with its entry points, the `ledger.settings` ones included, and deptry's module map read from
  the installed distributions' metadata) and `_manifest.py`, `.importlinter`, coverage targets, database roles and
  grants (modules, channels, platform), each module's OpenAPI snapshot, and the root `pyproject.toml`'s generated
  block (the dev-only packages and every test dependency, `[tool.uv.sources]`, ruff's `known-first-party`); `check`
  validates manifests (including the module graph with subscription edges) and fails on stale or orphaned
  generated files. `gate` is deferred (`uv run poe check` is the gate), with `affected` and a CI matrix,
  `scaffold`, CODEOWNERS, reference docs, identity's generated role grants and event schema snapshots.
- Devtools treats each channel like a module for schemas, roles, grants, id prefixes, permissions,
  subscriptions and job kinds.
- Coverage is 100 % branch per package; vulture, deptry, typos and buf complete the gate.

## Consequences

- Positive: adding a module or country touches only its directory and generated files (a workspace test plants
  both in a copy of the repository and finds `sync` then `check` clean).
- Negative: a generator to maintain; contributors edit manifests, never generated files.
