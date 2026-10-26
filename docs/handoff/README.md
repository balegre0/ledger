# Handoff index (for an AI or a person with no prior context)

Last updated 2026-10-25. This folder is the single entry point for continuing the work.

Read in this order:

1. [`HANDOFF.md`](HANDOFF.md): the owner's rules (Spanish with the owner, English in code; quality bar; never push without being asked), the repo map,
   the working process (implementer subagent in its own worktree, independent reviewer, fix round, merge, gate), the current state and the first things to do.
2. [`BACKLOG.md`](BACKLOG.md): everything that remains, in priority order (DGII certification path, signer production blockers, jury-driven tasks, milestones).
3. [`../reviews/2026-10-25-architecture-review/VERDICT.md`](../reviews/2026-10-25-architecture-review/VERDICT.md): the
   architecture verdict for a CRM with ERP features (8 research reports, 100 judges, two tribunals): keep the stack,
   engine first, breadth by a numeric gate. It sets the order of work in `BACKLOG.md` §0.
   [`../reviews/2026-10-20-stack-jury-synthesis.md`](../reviews/2026-10-20-stack-jury-synthesis.md): the earlier
   six-juror review, with the seven owner decisions still pending.
4. [`PROGRAM.md`](PROGRAM.md) and [`DECISION-LEDGER-2026-10-19-finish.md`](DECISION-LEDGER-2026-10-19-finish.md): the dated program ledger and the per-run decision ledger (every review verdict, ruling and merge).
5. The spec ([`../superpowers/specs/2026-10-13-ledger-platform-v2-design.md`](../superpowers/specs/2026-10-13-ledger-platform-v2-design.md), "the spec wins"), the ADRs in [`../adr`](../adr) and the plans in
   [`../superpowers/plans`](../superpowers/plans). The plans carry dated "As built" and review notes per task: they are binding decisions, not commentary.

## Which branch has what

- `main`: everything reviewed and gated so far: v2-M1 restructure, the DGII spike, the earlier GitHub work with its fix wave, the invoicing module, the signer module (Tasks 1-15),
  the fiscal work (kit, builders, readers, DGII/buyer clients, S3 archive, channel entry, issue) and the receiver role (Task 13, merged on 2026-10-25), plus this handoff.
  The full gate was green twice on this tree on 2026-10-25. The plans and their "As built" notes live here.
- **As of 2026-10-25, local `main` also contains Task 19 (`do_ecf.advance`, the DGII submission job; reviewed twice, mutation-checked, gate green twice) and the review fixes of Task 13 (the receiver: no OpenAPI or probe through a proxy, trusted roots read at start, IPv6 limited by /64; gate green twice).**
  Local `main` is ahead of GitHub's `main` and has NOT been pushed. The next step is Task 20 once the owner gives the green light (the owner asked to continue only what was started, then wait).
- `feat/m3b-advance-final`, `feat/m3b-receiver-fix`, `wip/m3b-advance-snapshot`, `feat/signer`, `feat/m3b-receiver`, `feat/everything`: feature or integration branches whose content is already in `main` (the `wip/` snapshot is a superseded unfinished copy of Task 19).

## Do not

- Push, amend or rewrite history unless the owner asks. Do not kill processes by image name. Do not use port 8000. Do not touch the ferreteria containers. Do not print secrets.
