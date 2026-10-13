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

- uv workspace members by glob (`packages/*`, `modules/*`, `countries/*`, `apps/*`, `tools/*`); one package
  per unit (`ledger-<name>`, one top-level import package each).
- `manifest.toml` per package is the source of truth: name, kind, owners, status, dependencies, schema, id
  prefixes, permissions with their built-in roles, events published and consumed, jobs, schedules, extension
  points, HTTP prefixes, quality tier.
- `ledger-devtools`, a tested workspace member: `sync` generates each package's `pyproject.toml` and
  `_manifest.py`, `.importlinter`, coverage targets, CODEOWNERS, database roles, identity's role grants and the
  reference docs; `check` validates manifests and fails on stale generated files; `affected` selects CI work;
  `scaffold` renders copier templates; `gate` runs the quality gate per package.
- Coverage is 100 % branch per package; vulture, deptry, typos and buf complete the gate.

## Consequences

- Positive: adding a module or country touches only its directory; CI time stays flat with `affected`.
- Negative: a generator to maintain; contributors edit manifests, never generated files.
