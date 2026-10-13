# 0009. A manifest-driven uv workspace with generated lists and contracts

- Status: Proposed
- Date: 2026-10-13
- Spec: `docs/superpowers/specs/2026-10-13-ledger-platform-v2-design.md` §4

## Context

v1's root `pyproject.toml` listed every package in at least eight places, each Dockerfile repeated every
workspace manifest, and a workspace test existed only to catch forgotten edits. With many modules and countries
this breaks first. Airflow, Home Assistant and the Azure SDK solve it with a manifest per unit and a repository
tool that generates and validates everything derived from it.

## Decision

- uv workspace members by glob (`packages/*`, `modules/*`, `countries/*`, `countries/*/testing`, `apps/*`,
  `tools/*`); one package per unit (`ledger-<name>`, one top-level import package each).
- `manifest.toml` per package is the source of truth: name, kind, owners, status, dependencies, schema, id
  prefixes, permissions with their built-in roles, events published (with optional public names) and consumed,
  jobs, schedules, extension points and provided ports, HTTP prefixes; for countries also regimes and channels
  (with environments, document kinds and tier). The platform has a manifest too (its schema, prefix, schedules).
- `ledger-devtools`, a tested workspace member, built now with three commands: `sync` generates each package's
  `pyproject.toml` and `_manifest.py`, `.importlinter`, coverage targets, database roles and grants (modules,
  channels, platform), identity's role grants and event schema snapshots; `check` validates manifests (including
  the module graph with subscription edges) and fails on stale generated files; `gate` runs the quality gate per
  package. `affected` with a CI matrix, `scaffold`, CODEOWNERS and reference docs come later.
- Devtools treats each channel like a module for schemas, roles, grants, id prefixes, permissions,
  subscriptions and job kinds.
- Coverage is 100 % branch per package; vulture, deptry, typos and buf complete the gate.

## Consequences

- Positive: adding a module or country touches only its directory and generated files.
- Negative: a generator to maintain; contributors edit manifests, never generated files.
