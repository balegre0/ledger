# 0011. Flows above modules, and chained extension points

- Status: Proposed
- Date: 2026-10-16
- Spec: `docs/superpowers/specs/2026-10-13-ledger-platform-v2-design.md` §2 rule 2, §3.2, §3.10, §4.6

## Context

Rule 2 makes imports and event subscriptions together an acyclic graph of modules, and §3.10 binds one
implementation per extension point. Both are right for a handful of modules and wrong for an Odoo-like catalogue
(sales, inventory, accounting, payments, many countries). Two modules that must react to each other's facts
cannot both consume the other's events, so each such case forces a mediator module with no domain of its own.
And several modules legitimately hook the same moment (taxes, stock reservation, accounting entries on issuing
an invoice); a single implementation per point makes them fight over it.

## Decision

- A **flow** is a package of kind `flow` under `flows/<name>` (`ledger-flow-<name>`, import package
  `ledger_flow_<name>`). It coordinates a business process across modules: it consumes events and calls
  facades of the modules it declares, keeps its own state as persisted state machines on the job queue (ADR
  0003), and owns a schema and role like a module (ADR 0004).
- Layering: apps > flows > modules > countries' channels > `fiscal-kit` > `platform` > `kernel`. A flow may depend
  on any number of modules (`dependencies.modules`, declared); **no module may depend on a flow**, and a flow
  may not depend on another flow. Flows are leaves, so the module graph stays acyclic and a flow can never be
  the cause of a cycle.
- A flow holds no domain rules: those stay in the modules it drives. A process that needs compensation across
  more than five steps still triggers the revisit of ADR 0003.
- **Extension points are chained.** A module may declare `implements` for a point several modules implement;
  implementations run in `(order, module name)` order, `order` an integer declared per implementation in the
  manifest (default 100). The module that declares the point defines how results combine in its Protocol
  documentation (objections are concatenated; a pipeline passes its result on). The directory returns the
  ordered tuple; a point with no implementation still fails the start when declared as required.
- The generated import-linter contracts add: modules never import flows; flows import modules only through
  `api`, `events` and declared edges; apps may import `ledger_flow_<name>.assembly` only.

## Considered options

- Mediator modules for every cross-module process: rejected (modules with no domain, and the graph still grows
  an edge per process).
- Lift the acyclic rule: rejected (it is what keeps extraction mechanical, ADR 0001).
- A workflow engine now: rejected (ADR 0003's triggers are not met).
- Keep one implementation per extension point: rejected (it blocks the second module that needs the hook).

## Consequences

- Positive: cross-module processes have a home that cannot create cycles; modules stay extractable; several
  modules can extend one moment without a mediator.
- Negative: one more package kind for the manifest model, devtools and the runtime; ordering between chained
  implementations is a convention authors must respect; a flow is a coupling hotspot, so review it as one.
