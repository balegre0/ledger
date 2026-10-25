# Handoff index (for an AI or a person with no prior context)

Last updated 2026-10-25. This folder is the single entry point for continuing the work.

Read in this order:

1. [`HANDOFF.md`](HANDOFF.md): the owner's rules (Spanish with the owner, English in code; quality bar; never push without being asked), the repo map,
   the working process (implementer subagent in its own worktree, independent reviewer, fix round, merge, gate), the current state and the first things to do.
2. [`BACKLOG.md`](BACKLOG.md): everything that remains, in priority order (DGII certification path, signer production blockers, jury-driven tasks, milestones).
3. [`../reviews/2026-10-20-stack-jury-synthesis.md`](../reviews/2026-10-20-stack-jury-synthesis.md): the six-juror review of the stack and the ERP strategy, with the seven owner decisions still pending.
4. [`PROGRAM.md`](PROGRAM.md) and [`DECISION-LEDGER-2026-10-19-finish.md`](DECISION-LEDGER-2026-10-19-finish.md): the dated program ledger and the per-run decision ledger (every review verdict, ruling and merge).
5. The spec ([`../superpowers/specs/2026-10-13-ledger-platform-v2-design.md`](../superpowers/specs/2026-10-13-ledger-platform-v2-design.md), "the spec wins"), the ADRs in [`../adr`](../adr) and the plans in
   [`../superpowers/plans`](../superpowers/plans). The plans carry dated "As built" and review notes per task: they are binding decisions, not commentary.

## Which branch has what

- `main`: everything reviewed and gated so far: v2-M1 restructure, the DGII spike, the earlier GitHub work with its fix wave, the invoicing module, the signer module (Tasks 1-15),
  the fiscal work (kit, builders, readers, DGII/buyer clients, S3 archive, channel entry, issue) and the receiver role (Task 13, merged on 2026-10-25), plus this handoff.
  The full gate was green twice on this tree on 2026-10-25. The plans and their "As built" notes live here.
- **Task 19 is merged into local `main` (not pushed) as of 2026-10-25; the next step is Task 20 once the owner gives the green light.** History of the branch `feat/m3b-advance-final` (worktree `ledger-t19`): Task 19 (`do_ecf.advance`, the DGII submission job) in **review-fixed state**: the independent review's
  rulings (CRITICAL 1, IMPORTANT 1-3, the minors) are fixed or recorded in the plan, gate: on the first fix round three runs (one failed on the known gRPC flake, two green), on the re-review's fixes two runs, both green (see HANDOFF.md). Not in `main`: it awaits the
  re-review of the fixes, then the merge. `wip/m3b-advance-snapshot` is its superseded unfinished snapshot.
- `feat/m3b-receiver-fix` (worktree `ledger-r13`, from `main` 6f0ab2b): Task 13's review fix round (the review returned CHANGES REQUIRED, no Critical): the receiver
  serves no OpenAPI document and no probe or metrics through a proxy, reads its trusted roots at start, limits IPv6 clients by their /64. Not merged, not pushed; gate green twice (see HANDOFF.md).
- `feat/signer`, `feat/m3b-receiver`: older integration/feature branches whose content is already in `main`.

## Do not

- Push, amend or rewrite history unless the owner asks. Do not kill processes by image name. Do not use port 8000. Do not touch the ferreteria containers. Do not print secrets.
