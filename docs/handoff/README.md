# Handoff index (for an AI or a person with no prior context)

Last updated 2026-10-21. This folder is the single entry point for continuing the work.

Read in this order:

1. [`HANDOFF.md`](HANDOFF.md): the owner's rules (Spanish with the owner, English in code; quality bar; never push without being asked), the repo map,
   the working process (implementer subagent in its own worktree, independent reviewer, fix round, merge, gate), the current state and the first things to do.
2. [`BACKLOG.md`](BACKLOG.md): everything that remains, in priority order (DGII certification path, signer production blockers, jury-driven tasks, milestones).
3. [`../reviews/2026-10-20-stack-jury-synthesis.md`](../reviews/2026-10-20-stack-jury-synthesis.md): the six-juror review of the stack and the ERP strategy, with the seven owner decisions still pending.
4. [`PROGRAM.md`](PROGRAM.md) and [`DECISION-LEDGER-2026-10-19-finish.md`](DECISION-LEDGER-2026-10-19-finish.md): the dated program ledger and the per-run decision ledger (every review verdict, ruling and merge).
5. The spec ([`../superpowers/specs/2026-10-13-ledger-platform-v2-design.md`](../superpowers/specs/2026-10-13-ledger-platform-v2-design.md), "the spec wins"), the ADRs in [`../adr`](../adr) and the plans in
   [`../superpowers/plans`](../superpowers/plans). The plans carry dated "As built" and review notes per task: they are binding decisions, not commentary.

## Which branch has what

- `main`: v2-M1 restructure, the DGII spike, the earlier GitHub work with its fix wave, and the invoicing module (all reviewed), plus this handoff.
- `feat/signer`: the integration branch. It contains `main` plus the signer module (Tasks 1-15) and the fiscal work (kit, builders, readers, DGII/buyer clients, S3 archive, channel entry, issue).
  The newest plans and notes live here. Its last full gate was green at 5cd35a2; run the gate again first.
- `feat/m3b-receiver`: Task 13 (receiver role), committed and not yet reviewed.
- Task 19 (the `do_ecf.advance` job) was uncommitted in a local worktree when this was written and is not in the repository.

## Do not

- Push, amend or rewrite history unless the owner asks. Do not kill processes by image name. Do not use port 8000. Do not touch the ferreteria containers. Do not print secrets.
